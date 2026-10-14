# SPDX-License-Identifier: MPL-2.0
from PySide6.QtWidgets import (QApplication, QWidget, QGraphicsScene,
                                QGraphicsPixmapItem, QGraphicsBlurEffect)
from PySide6.QtCore    import (Qt, Slot, QObject, QRect, QRectF, QPoint, QSize, QEvent,
                                QPropertyAnimation, QEasingCurve, Property,
                                QTimer)
from PySide6.QtGui     import QPainter, QPixmap, QColor, QPainterPath, QPen, QImage, QRegion
from contextlib        import contextmanager
from collections import OrderedDict
import math
from shiboken6 import isValid
from main_ext.class_ThemeUi import theme as _th
from core.skia_gpu import render_gpu, blur_gpu, skia
from core.live_backdrop import LiveBackdrop as _LiveBackdrop
from core.skia_patch import SurfacePatch


_BLUR_CACHE = OrderedDict()
_BLUR_CACHE_BYTES = 0
_BLUR_CACHE_BUDGET = 8 * 1024 * 1024


def _crop_logical(pixmap, x, y, width, height):
    ratio = pixmap.devicePixelRatioF()
    return pixmap.copy(QRect(round(x * ratio), round(y * ratio),
                             round(width * ratio), round(height * ratio)))


def _widget_global_pos(widget):
    window = widget.window()
    # QWidget geometry updates immediately during a morph/move. QWindow's
    # platform mapping can still have the previous position until dispatch.
    return window.geometry().topLeft() + widget.mapTo(window, QPoint())


def _capture_widget_area(root, global_pos, width, height, pad=0, device_pixel_ratio=None,
                         exclude_widgets=()):
    """Capture only the backdrop region at its native DPR, including edge padding."""
    ratio = root.devicePixelRatioF() if device_pixel_ratio is None else device_pixel_ratio
    result = QPixmap(math.ceil((width + pad * 2) * ratio),
                     math.ceil((height + pad * 2) * ratio))
    result.setDevicePixelRatio(ratio)
    result.fill(QColor(_th.BG))
    local = global_pos - _widget_global_pos(root) - QPoint(pad, pad)
    region = QRect(local, result.deviceIndependentSize().toSize()).intersected(root.rect())
    if not region.isEmpty():
        painter = QPainter(result)
        _render_backdrop_region(root, painter, region.topLeft() - local, region, exclude_widgets, True)
        painter.end()
    return result


def _render_backdrop_region(widget, painter, target, region, excluded, draw_background=False):
    if widget in excluded:
        return
    nested = [item for item in excluded if isValid(item) and widget.isAncestorOf(item)]
    flags = QWidget.DrawWindowBackground if draw_background else QWidget.RenderFlag(0)
    if not nested:
        widget.render(painter, target, QRegion(region), flags | QWidget.DrawChildren)
        return
    # Merely swallowing an opaque foreground's paint leaves a clipped hole in
    # its parent's background. Draw ancestors without children, then the actual
    # backdrop children in stacking order. No hide/show or input focus changes.
    widget.render(painter, target, QRegion(region), flags)
    for child in widget.findChildren(QWidget, options=Qt.FindDirectChildrenOnly):
        if child.isHidden() or child.isWindow() or child in excluded:
            continue
        child_region = region.translated(-child.pos()).intersected(child.rect())
        if not child_region.isEmpty():
            child_target = target + child.pos() + child_region.topLeft() - region.topLeft()
            _render_backdrop_region(child, painter, child_target, child_region, nested)


def _prepare_dialog_shell(win):
    """Keep a framed card's transparent native shell clear of inherited QSS."""
    from PySide6.QtWidgets import QDialog
    layout = win.layout()
    if (not isinstance(win, QDialog) or not win.testAttribute(Qt.WA_TranslucentBackground)
            or layout is None or layout.count() != 1 or layout.itemAt(0).widget() is None):
        return
    rule = 'QDialog[_rakDialogShell="true"] {background:transparent; border:none;}'
    sheet = win.styleSheet()
    if rule not in sheet:
        base = sheet if "{" in sheet or not sheet.strip() else "* {" + sheet + "}"
        win.setProperty("_rakDialogShell", True)
        win.setStyleSheet(base + "\n" + rule)


class _DialogSurfaceGeometry(QObject):
    def __init__(self, surface, host, use_content):
        super().__init__(surface)
        self.surface, self.host, self.use_content = surface, host, use_content
        host.installEventFilter(self)
        self.sync()

    def sync(self):
        rect = self.host.rect()
        layout = self.host.layout()
        if self.use_content and layout is not None and layout.count() == 1:
            layout.activate()
            card = layout.itemAt(0).widget()
            if card is not None:
                rect = card.geometry()
        if self.surface.geometry() != rect:
            self.surface.setGeometry(rect)

    def eventFilter(self, obj, event):
        from PySide6.QtCore import QEvent
        if event.type() in (QEvent.Resize, QEvent.LayoutRequest, QEvent.Show):
            self.sync()
        return False


def _flat_color() -> QColor:
    return QColor(_th.BG_MODAL)


class _BlurFrameShape:
    def __init__(self, background, border, width, radius):
        self.background, self.border, self.width, self.radius = background, border, width, radius

    def draw(self, canvas, bridge, rect):
        bounds = skia.Rect.MakeXYWH(rect.x(), rect.y(), rect.width(), rect.height())
        rounded = skia.RRect.MakeRectXY(bounds, self.radius, self.radius)
        if self.background.alpha():
            canvas.save()
            canvas.clipRRect(rounded, True)
            canvas.drawColor(bridge.color(self.background))
            canvas.restore()
        if self.width and self.border.alpha():
            inset = bounds.makeInset(self.width/2, self.width/2)
            paint = skia.Paint(Color4f=bridge.color(self.border), AntiAlias=True,
                Style=skia.Paint.kStroke_Style, StrokeWidth=self.width)
            canvas.drawRRect(skia.RRect.MakeRectXY(inset, self.radius, self.radius), paint)


def _draw_blur_patch(painter, patch, rect, *, corners=False):
    ratio = patch.image.devicePixelRatioF()
    width, height = math.ceil(rect.width()*ratio), math.ceil(rect.height()*ratio)
    cap = min(patch.cap*ratio, width/2, height/2)
    source_cap = patch.cap*ratio
    sx = (0, source_cap, patch.image.width()-source_cap, patch.image.width())
    sy = (0, source_cap, patch.image.height()-source_cap, patch.image.height())
    dx = tuple(rect.left()+x/ratio for x in (0, cap, width-cap, width))
    dy = tuple(rect.top()+y/ratio for y in (0, cap, height-cap, height))
    # Mask interiors are opaque and border interiors transparent. Drawing
    # their stretched center still touches every pixel of a large card.
    for y in range(3):
        for x in range(3):
            if (x == 1 and y == 1) or (corners and (x == 1 or y == 1)):
                continue
            painter.drawImage(QRectF(dx[x], dy[y], dx[x+1]-dx[x], dy[y+1]-dy[y]), patch.image,
                              QRectF(sx[x], sy[y], sx[x+1]-sx[x], sy[y+1]-sy[y]))


def _blur_layer_destination(widget, destination, ratio):
    if destination == QRectF(widget.rect()):
        # A frozen texture stretched to the card must fill its last physical
        # pixel. The rounded mask owns edge coverage; an image rectangle ending
        # inside that pixel would clip it again during the final GPU handoff.
        return QRectF(0, 0, math.ceil(widget.width()*ratio)/ratio,
                      math.ceil(widget.height()*ratio)/ratio)
    return destination


def _blur_card_resize_frame(widget, layers):
    """Resample frozen blur with cached Skia AA corners, without GPU readback."""
    ratio = widget.devicePixelRatioF()
    color = _flat_color() if widget._flat else widget._tint
    physical_width, physical_height = widget.width()*ratio, widget.height()*ratio
    end_phase = (round(physical_width % 1, 6), round(physical_height % 1, 6))
    style = (ratio, widget._corner, widget._border.rgba(), end_phase)
    cached_patches = getattr(widget, "_blur_resize_patches", None)
    if cached_patches is None or cached_patches[0] != style:
        # Keep the corners separated enough for Skia's analytic AA path,
        # including a half-pixel right/bottom edge at 150% display scale.
        mask = SurfacePatch(_BlurFrameShape(QColor(255, 255, 255), QColor(0, 0, 0, 0), 0, widget._corner), ratio,
                            end_phase=end_phase, center_pixels=32)
        border = (SurfacePatch(_BlurFrameShape(QColor(0, 0, 0, 0), widget._border, 1, widget._corner), ratio,
                               end_phase=end_phase, center_pixels=32)
                  if widget._border.alpha() else None)
        widget._blur_resize_patches = style, mask, border
    else:
        _, mask, border = cached_patches
    key = (widget.width(), widget.height(), style, color.rgba(),
           tuple((image.cacheKey(), rect.x(), rect.y(), rect.width(), rect.height(), alpha)
                 for image, rect, alpha in layers))
    cached = getattr(widget, "_blur_resize_frame", None)
    if cached is not None and cached[0] == key:
        return cached[1]
    image = QImage(math.ceil(widget.width()*ratio), math.ceil(widget.height()*ratio), QImage.Format_ARGB32_Premultiplied)
    image.setDevicePixelRatio(ratio)
    image.fill(Qt.transparent)
    rect = QRectF(widget.rect())
    painter = QPainter(image)
    painter.setRenderHint(QPainter.SmoothPixmapTransform)
    if not widget._flat:
        for backdrop, destination, alpha in layers:
            painter.setOpacity(alpha)
            painter.drawPixmap(_blur_layer_destination(widget, destination, ratio),
                               backdrop, QRectF(backdrop.rect()))
    painter.setOpacity(1)
    # Coverage comes from the mask below. Fill the complete pixel buffer so
    # Qt's integer rectangle rasterizer does not omit a quarter-pixel edge.
    painter.fillRect(QRectF(0, 0, image.width()/ratio, image.height()/ratio), color)
    painter.setCompositionMode(QPainter.CompositionMode_DestinationIn)
    _draw_blur_patch(painter, mask, rect, corners=True)
    # Copy coverage of a fractional final row/column from the same Skia mask.
    cap = mask.cap
    for physical, horizontal in ((physical_width, False), (physical_height, True)):
        if physical % 1 > .001:
            strip = (QRectF(cap, math.floor(physical)/ratio, max(0, image.width()/ratio-2*cap), 1/ratio)
                     if horizontal else QRectF(math.floor(physical)/ratio, cap, 1/ratio, max(0, image.height()/ratio-2*cap)))
            source = (QRectF(mask.cap*ratio, mask.image.height()-1, 1, 1) if horizontal
                      else QRectF(mask.image.width()-1, mask.cap*ratio, 1, 1))
            painter.drawImage(strip, mask.image, source)
    painter.setCompositionMode(QPainter.CompositionMode_SourceOver)
    if border is not None:
        _draw_blur_patch(painter, border, rect)
    painter.end()
    widget._blur_resize_frame = key, image
    return image


def _gpu_card_frame(widget, backdrop, origin, layers=None):
    ratio = widget.devicePixelRatioF()
    color = _flat_color() if widget._flat else widget._tint
    key = (widget.width(), widget.height(), ratio, widget._corner,
           widget._flat, color.rgba(), widget._border.rgba(),
           backdrop.cacheKey(), origin.x(), origin.y(),
           tuple((image.cacheKey(), rect.x(), rect.y(), rect.width(), rect.height(), alpha)
                 for image, rect, alpha in layers) if layers is not None else None)
    cached = getattr(widget, "_skia_card_frame", None)
    if cached is not None and cached[0] == key:
        return cached[1]

    def draw(canvas, bridge):
        rect = skia.Rect.MakeWH(widget.width(), widget.height())
        rounded = skia.RRect.MakeRectXY(rect, widget._corner, widget._corner)
        canvas.save()
        canvas.clipRRect(rounded, True)
        if not widget._flat and not backdrop.isNull():
            if layers is None:
                destination = QRectF(origin.toPointF(), backdrop.deviceIndependentSize())
                bridge.draw_image(canvas, backdrop, _blur_layer_destination(widget, destination, ratio))
            else:
                for image, destination, alpha in layers:
                    bridge.draw_image(canvas, image, _blur_layer_destination(widget, destination, ratio), alpha)
        canvas.drawColor(bridge.color(color))
        canvas.restore()
        inset = skia.Rect.MakeXYWH(.5, .5, widget.width() - 1, widget.height() - 1)
        paint = skia.Paint(Color4f=bridge.color(widget._border), AntiAlias=True,
                           Style=skia.Paint.kStroke_Style, StrokeWidth=1)
        canvas.drawRRect(skia.RRect.MakeRectXY(inset, widget._corner, widget._corner), paint)

    image = render_gpu(widget.size(), ratio, draw)
    if image is not None:
        image = image.convertToFormat(QImage.Format_ARGB32_Premultiplied)
        widget._skia_card_frame = (key, image)
    return image


# Crossfade overlay (snapshot → fade out → reveal)
class CrossfadeOverlay(QWidget):

    def __init__(self, parent: QWidget, exclude_widgets=()):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WA_NoSystemBackground)
        self.setGeometry(parent.rect())
        self._exclude_widgets = tuple(exclude_widgets)
        self.refresh_exclusions()
        # Capture the current Qt backing content, not the monitor's previous
        # frame (which can still contain the dropdown that just closed).
        self._pix = parent.grab()

        self._opacity = 1.0
        self.raise_()
        self.show()

    def refresh_exclusions(self):
        if not self._exclude_widgets:
            return
        parent = self.parentWidget()
        region = QRegion(self.rect())
        for widget in self._exclude_widgets:
            if widget.isVisibleTo(parent):
                region -= QRegion(QRect(widget.mapTo(parent, QPoint()), widget.size()))
        self.setMask(region)

    def _get_opacity(self) -> float:
        return self._opacity

    def _set_opacity(self, v: float):
        self._opacity = max(0.0, min(1.0, v))
        self.update()

    opacity = Property(float, _get_opacity, _set_opacity)

    def paintEvent(self, _ev):
        p = QPainter(self)
        p.setOpacity(self._opacity)
        p.drawPixmap(0, 0, self._pix)
        p.end()

    def fade_out(self, duration: int = 220, easing=QEasingCurve.OutCubic):
        anim = QPropertyAnimation(self, b"opacity", self)
        anim.setDuration(duration)
        anim.setStartValue(1.0)
        anim.setEndValue(0.0)
        anim.setEasingCurve(easing)
        anim.finished.connect(self.deleteLater)
        self._anim = anim
        anim.start()


class BlurOverlay(QWidget):
    def __init__(self, parent: QWidget, radius: float = 28.0, darken: float = 0.45,
                 animate: bool = True, cover_screen: bool = False,
                 source_widget: QWidget = None, block_input: bool = False,
                 sync_blur: bool = False, backdrop: str = "texture",
                 capture_without_backdrop: bool = False,
                 cache_fade_background: bool = False):


        if cover_screen:
            super().__init__(None)
            self.setWindowFlags(Qt.FramelessWindowHint | Qt.Tool
                                | Qt.WindowStaysOnTopHint | Qt.NoDropShadowWindowHint)
            self.setAttribute(Qt.WA_TranslucentBackground)
        else:
            super().__init__(parent)
            parent.installEventFilter(self)
        self._pix    = QPixmap()
        self._texture = None
        self._texture_color = None
        self._fade_underlay = QPixmap()
        self._fade_underlay_size = None
        self._cache_fade_background = cache_fade_background
        self._ready  = False
        self._blur_timer = QTimer(self)
        self._blur_timer.setSingleShot(True)
        self._blur_timer.timeout.connect(self._finish_blur)
        self._pending_blur = None
        self._radius = radius
        self._darken = darken
        self._explicit_blur = str(backdrop).casefold() == "blur"
        self._explicit_root = source_widget or parent
        self._cover_screen = cover_screen
        self._capture_without_backdrop = capture_without_backdrop
        self._opacity = 1.0  # default solid; fade-in di-set 0.0 kalau animate
        self._live_backdrop = None
        self._embedded_parent = parent if not cover_screen else None


        self._flat = str(backdrop).casefold() != "blur" or not _th.blur_enabled

        if not block_input:
            self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WA_NoSystemBackground)

        screen = parent.screen() if hasattr(parent, "screen") else None
        if screen is None:
            from PySide6.QtGui import QGuiApplication
            screen = QGuiApplication.primaryScreen()

        pad = int(radius) + 4
        padded = None

        if source_widget is not None:
            # ── RENDER path: jepret widget langsung (login overlay) ──
            self.setGeometry(parent.rect())
            if not self._flat and not getattr(_th, "live_blur_enabled", False):


                padded = _capture_widget_area(source_widget,
                    source_widget.mapToGlobal(QPoint()),
                    source_widget.width(), source_widget.height(), pad)
        elif cover_screen:
            # Geometry = seluruh screen
            self.setGeometry(screen.geometry())
            if not self._flat:
                pw  = screen.geometry().width()  + pad * 2
                ph  = screen.geometry().height() + pad * 2
                pg_x = screen.geometry().x() - pad
                pg_y = screen.geometry().y() - pad
                padded = screen.grabWindow(0, pg_x, pg_y, pw, ph)
        else:
            self.setGeometry(parent.rect())
            if not self._flat and not getattr(_th, "live_blur_enabled", False):
                pw, ph = parent.width() + pad * 2, parent.height() + pad * 2
                pg = parent.mapToGlobal(parent.rect().topLeft())
                pg_x = pg.x() - pad
                pg_y = pg.y() - pad
                padded = screen.grabWindow(0, pg_x, pg_y, pw, ph)


        animate = animate and _th.animations_enabled
        if animate:
            self._opacity = 0.0
            if self._flat and self._cache_fade_background:
                self._cache_fade_underlay()
        self._pad    = pad

        if self._flat:
            # Ringan: langsung siap, tanpa capture/blur/thread sama sekali.
            self._ready = True
            self.raise_()
            self.show()
        else:
            self._pending_blur = padded
            if sync_blur:
                self._finish_blur()
            else:
                # The shared native OpenGL context belongs to the GUI thread.
                # Never create QPixmap/QGraphicsScene or use that context in a worker.
                self._blur_timer.start(0)
            self.raise_()
            self.show()

        if self._explicit_blur and hasattr(_th, "on_effect_level_change"):
            callback = self._effect_changed
            _th.on_effect_level_change(callback)
            bound_theme = _th
            self.destroyed.connect(lambda: bound_theme.remove_effect_level_listener(callback))

        if animate:
            # Fade-in mulai setelah show — animasi pada self._opacity (custom)
            self._fade_in = QPropertyAnimation(self, b"opacity", self)
            self._fade_in.setDuration(180)
            self._fade_in.setStartValue(0.0)
            self._fade_in.setEndValue(1.0)
            self._fade_in.setEasingCurve(QEasingCurve.OutCubic)
            if self._flat and self._cache_fade_background:
                self._fade_in.finished.connect(self._release_fade_underlay)
            self._fade_in.start()

    def _get_opacity(self) -> float:
        return self._opacity
    def _set_opacity(self, v: float):
        self._opacity = max(0.0, min(1.0, v))
        self.update()
    opacity = Property(float, _get_opacity, _set_opacity)

    @Slot()
    def _finish_blur(self):
        source, self._pending_blur = self._pending_blur, None
        if getattr(self, "_closing", False):
            return
        if getattr(_th, "live_blur_enabled", False) and not self._cover_screen:
            scene = getattr(self, "_live_backdrop_scene", None)
            if scene is None:
                scene = _LiveBackdrop(self._explicit_root, self, self, self._radius)
                self._live_backdrop_scene = scene
            scene.attach(self)
            scene.resume()
            self._ready = True
            self.update()
            return
        if source is None or source.isNull():
            return
        blurred = make_blur_pixmap(source, self._radius)
        painter = QPainter(blurred)
        painter.fillRect(QRectF(0, 0, *self._logical_size(blurred)),
                         QColor(0, 0, 0, round(self._darken * 255)))
        painter.end()
        self._on_done(blurred)

    def exclude_backdrop_widget(self, widget):
        exclusions = getattr(self, "_backdrop_exclusions", [])
        self._backdrop_exclusions = [*exclusions, widget]

    def fit_to_parent(self):
        parent = self._embedded_parent
        if parent is not None and isValid(parent):
            self.setGeometry(parent.rect())

    def _effect_changed(self, level):
        if not isValid(self) or getattr(self, "_closing", False):
            return
        scene = getattr(self, "_live_backdrop", None)
        if scene is not None and isValid(scene) and level != "high":
            scene.detach(self)
        self._flat = level == "low"
        if self._flat:
            self._pix = QPixmap()
        else:
            if level != "high":
                self._backdrop_capture_depth = True
                try:
                    self._pending_blur = _capture_widget_area(self._explicit_root,
                        _widget_global_pos(self), self.width(), self.height(), self._pad,
                        exclude_widgets=(self, *getattr(self, "_backdrop_exclusions", ())))
                finally:
                    self._backdrop_capture_depth = False
            self._finish_blur()
        self._ready = True
        self.update()

    @staticmethod
    def _logical_size(pixmap):
        size = pixmap.deviceIndependentSize()
        return size.width(), size.height()

    def _on_done(self, pix: QPixmap):
        pad = getattr(self, "_pad", 0)
        if pad > 0:
            w = self.width()
            h = self.height()
            self._pix = _crop_logical(pix, pad, pad, w, h)
        else:
            self._pix = pix
        self._ready = True
        self.update()

    def _cache_fade_underlay(self):
        parent = self.parentWidget()
        if parent is None:
            return
        visible = self.isVisible()
        if visible:
            self.hide()
        try:
            self._fade_underlay = parent.grab(self.geometry())
            self._fade_underlay_size = self.size()
        finally:
            if visible:
                self.show()
        self.setAttribute(Qt.WA_OpaquePaintEvent, not self._fade_underlay.isNull())

    def _release_fade_underlay(self):
        self._fade_underlay = QPixmap()
        self._fade_underlay_size = None
        self.setAttribute(Qt.WA_OpaquePaintEvent, False)
        self.update()

    def _load_texture(self):
        from textures.handler import texture_pixmap
        self._texture = texture_pixmap("black")
        if self._texture.isNull():
            self._texture_color = QColor(0, 0, 0, 150)
            return
        image = self._texture.toImage()
        color = image.pixelColor(0, 0)
        # The shipped 10x10 tile is a uniform translucent black. A solid
        # fill produces the same pixels without thousands of tiled draws.
        if all(image.pixelColor(x, y) == color
               for y in range(image.height()) for x in range(image.width())):
            self._texture_color = color

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if (not self._fade_underlay.isNull()
                and event.size() != self._fade_underlay_size):
            self._release_fade_underlay()

    def eventFilter(self, watched, event):
        from PySide6.QtCore import QEvent
        if watched is self._embedded_parent and event.type() in (QEvent.Resize, QEvent.Move):
            self.fit_to_parent()
            self.update()
        return False

    def paintEvent(self, _ev):
        if getattr(self, "_backdrop_capture_depth", False):
            return
        if not self._ready:
            return

        p = QPainter(self)
        if not self._fade_underlay.isNull():
            p.drawPixmap(0, 0, self._fade_underlay)
        p.setOpacity(self._opacity)

        if getattr(self, "_flat", False):
            if self._texture is None:
                self._load_texture()
            if self._texture_color is not None:
                p.fillRect(self.rect(), self._texture_color)
            else:
                p.drawTiledPixmap(self.rect(), self._texture)
        else:
            scene = getattr(self, "_live_backdrop", None)
            if scene is not None and isValid(scene) and self in scene.cards:
                p.setRenderHint(QPainter.SmoothPixmapTransform)
                for image, destination, alpha in scene.layers_for(self):
                    p.setOpacity(self._opacity*alpha)
                    p.drawPixmap(destination, image, QRectF(image.rect()))
                p.setOpacity(self._opacity)
                p.fillRect(self.rect(), QColor(0, 0, 0, round(self._darken * 255)))
            else:
                p.setRenderHint(QPainter.SmoothPixmapTransform)
                p.drawPixmap(QRectF(self.rect()), self._pix, QRectF(self._pix.rect()))

        p.end()

    def attach_window_surface(self, win: QWidget, *, corner: float = 14.0,
                              host: QWidget = None, freeze_immediately: bool = False):

        if win is not None:
            _prepare_dialog_shell(win)

        # Explicit backdrop="blur" is already scoped by the caller (login or
        # compare-card). Do not stack a second dialog surface over it.
        if not _th.blur_enabled or win is None or not self._flat:
            return None

        surface_host = host or win

        def _freeze_surface(card):

            def _capture():
                if getattr(self, "_destroyed", False):
                    return
                try:
                    if card.parentWidget() is None or not card.isVisible():
                        return
                    root = self.parentWidget() or win.parentWidget() or win
                    card._source_overlay = self
                    if host is not None:
                        card._backdrop_exclusion = host
                    # A glass dialog needs the app pixels before this dark
                    # backdrop is painted; otherwise its blur becomes black.
                    hide_backdrop = (not getattr(_th, "live_blur_enabled", False) and
                                     self._capture_without_backdrop and self.isVisible())
                    if hide_backdrop:
                        self.hide()
                    try:
                        card.freeze_from_widget(
                            root,
                            card.mapToGlobal(card.rect().topLeft()),
                        )
                    finally:
                        if hide_backdrop and not getattr(self, "_destroyed", False):
                            self.show()
                except (RuntimeError, Exception):
                    pass

            if not freeze_immediately and _th.animations_enabled and self._opacity < 0.995:
                QTimer.singleShot(190, _capture)
            else:
                _capture()

        try:
            old = getattr(self, "_surface_card", None)
            if old is not None and old.parentWidget() is surface_host:
                return old
            # A shared backdrop may hand off between Automation's pickers and
            # log window. The preceding window still owns its surface during
            # its closing fade; destroying that surface here makes it flash.
        except (RuntimeError, Exception):
            pass

        try:
            # Some windows already own their card. Freeze that card in place
            # instead of adding another layer underneath it.
            existing = surface_host.findChild(BlurCard)
            if existing is not None:
                self._surface_card = existing
                _freeze_surface(existing)
                return existing

            tint = QColor(_th.BG2)
            tint.setAlpha(72)
            surface = BlurCard(
                surface_host,
                radius=18.0,
                tint=tint,
                border=QColor(0, 0, 0, 0),
                corner=corner,
            )
            surface.setAttribute(Qt.WA_TransparentForMouseEvents)
            if host is None:
                surface._geometry_filter = _DialogSurfaceGeometry(surface, surface_host, True)
            else:
                # A morph host owns its moving/settled clipping geometry.
                surface.setGeometry(surface_host.rect())
            surface.lower()
            surface.show()
            self._surface_card = surface
            _freeze_surface(surface)
            return surface
        except (RuntimeError, Exception):
            return None

    def recapture(self, source_widget, radius: float = 28.0, darken: float = 0.5):

        if getattr(self, "_flat", False):
            # Flat mode: tak ada apa pun buat di-recapture, geometry aja yang diikuti.
            self.setGeometry(source_widget.rect())
            self.update()
            return
        try:
            base_col = source_widget.palette().color(source_widget.backgroundRole())
            if not base_col.isValid() or base_col.alpha() == 0:
                base_col = QColor(13, 14, 18)
            src = _capture_widget_area(source_widget,
                source_widget.mapToGlobal(QPoint()), source_widget.width(), source_widget.height())
            blurred = make_blur_pixmap(src, radius)
            # Komposit di atas base SOLID supaya tidak ada lubang alpha dari
            # tepi blur (make_blur_pixmap mengisi transparan → alpha menyebar).
            out = QPixmap(blurred.size())
            out.setDevicePixelRatio(blurred.devicePixelRatioF())
            out.fill(base_col)
            p = QPainter(out)
            p.drawPixmap(0, 0, blurred)
            p.fillRect(QRectF(0, 0, *self._logical_size(out)), QColor(0, 0, 0, int(darken * 255)))
            p.end()
            self._pix = out
            self._ready = True
            self.setGeometry(source_widget.rect())
            self.update()
        except Exception:
            pass

    def close_overlay(self):
        # Guard: skip kalau fade-out udah jalan (cegah double-trigger
        # dari helper install_window_fade + caller)
        if getattr(self, "_closing", False):
            return
        self._closing = True
        fade_in = getattr(self, "_fade_in", None)
        if fade_in is not None:
            fade_in.stop()
        if not _th.animations_enabled:
            self._final_close()
            return
        # Fade-out → setelah selesai baru hide + deleteLater
        try:
            if self._flat and self._cache_fade_background:
                self._cache_fade_underlay()
            self._fade_out = QPropertyAnimation(self, b"opacity", self)
            self._fade_out.setDuration(180)
            self._fade_out.setStartValue(self._opacity)
            self._fade_out.setEndValue(0.0)
            self._fade_out.setEasingCurve(QEasingCurve.InCubic)
            self._fade_out.finished.connect(self._final_close)
            self._fade_out.start()
        except Exception:
            self._final_close()

    def _final_close(self):
        # Guard: idempotent — bisa dipanggil manual (mis. AppDialog._run)
        # lalu lagi dari _fade_out.finished. Cegah double-destroy.
        if getattr(self, "_destroyed", False):
            return
        self._destroyed = True
        scene = getattr(self, "_live_backdrop_scene", None)
        if scene is not None and isValid(scene):
            scene.stop()
        self._blur_timer.stop()
        self._pending_blur = None
        self.hide()
        self.deleteLater()


# blurred card background for context menus
class BlurCard(QWidget):

    def __init__(self, parent=None, radius: float = 18.0, tint: QColor = None,
                 border: QColor = None, corner: float = 10.0):
        super().__init__(parent)
        self._blur_pix = QPixmap()
        self._backdrop_origin = None
        self._frozen_size = QSize()
        self._radius   = radius
        self._tint     = tint   or QColor(0, 0, 0, 40)
        self._border   = border or QColor(255, 255, 255, 28)
        self._corner   = corner
        self._flat     = not _th.blur_enabled
        self._backdrop_root = None
        self._source_overlay = None
        self._live_backdrop = None
        self._resize_pending = False
        self._resize_frame_timer = QTimer(self)
        self._resize_frame_timer.setSingleShot(True)
        self._resize_frame_timer.setInterval(140)
        self._resize_frame_timer.timeout.connect(self._finish_resize_frame)
        self.setAttribute(Qt.WA_TranslucentBackground)
        if hasattr(_th, "on_effect_level_change"):
            self._effect_listener = self._effect_changed
            _th.on_effect_level_change(self._effect_listener)
            callback, bound_theme = self._effect_listener, _th
            self.destroyed.connect(lambda: bound_theme.remove_effect_level_listener(callback))

    def _attach_live(self, root):
        overlay = self._source_overlay
        owner = overlay if overlay is not None and isValid(overlay) else self
        field = "_live_backdrop_scene" if owner is overlay else "_owned_live_backdrop"
        scene = getattr(owner, field, None)
        if scene is None or not isValid(scene) or scene.root is not root:
            if scene is not None and isValid(scene):
                scene.stop()
                scene.deleteLater()
            scene = _LiveBackdrop(root, owner, overlay, self._radius)
            setattr(owner, field, scene)
        needs_refresh = not scene.image.isNull()
        scene.attach(self)
        scene.resume()
        if needs_refresh:
            scene.refresh()
        return True

    def _effect_changed(self, level):
        if not isValid(self):
            return
        self._flat = level == "low"
        self._effect_pending = not self.isVisible()
        scene = self._live_backdrop
        if scene is not None and isValid(scene) and level != "high":
            scene.detach(self)
        if self._flat:
            self._blur_pix = QPixmap()
        elif self.isVisible() and self._backdrop_root is not None and isValid(self._backdrop_root):
            self.freeze_from_widget(self._backdrop_root, self.mapToGlobal(QPoint()))
        self.update()

    def showEvent(self, event):
        super().showEvent(event)
        if getattr(self, "_effect_pending", False):
            self._effect_pending = False
            self._effect_changed(getattr(_th, "effect_level", "medium"))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if not self._blur_pix.isNull():
            self._resize_pending = True
            self._resize_frame_timer.start()

    @Slot()
    def _finish_resize_frame(self):
        self._resize_pending = False
        self._blur_resize_frame = None
        self.update()

    def pin_backdrop_to(self, global_pos):

        self._backdrop_origin = QPoint(global_pos) if global_pos is not None else None
        self.update()

    def freeze(self, root_widget: QWidget, global_pos):
        self._backdrop_root = root_widget
        if getattr(_th, "live_blur_enabled", False) and self._attach_live(root_widget):
            return
        if self._flat:
            # Blur dimatikan — skip capture layar + QGraphicsBlurEffect sama sekali.
            self._blur_pix = QPixmap()
            self.update()
            return

        w, h = self.width(), self.height()
        pad  = int(self._radius) + 4
        rw, rh = w + pad * 2, h + pad * 2


        screen = root_widget.screen() if hasattr(root_widget, "screen") else None
        if screen is None:
            from PySide6.QtGui import QGuiApplication
            screen = QGuiApplication.primaryScreen()

        gx, gy = global_pos.x() - pad, global_pos.y() - pad
        padded = screen.grabWindow(0, gx, gy, rw, rh)

        blurred = make_blur_pixmap(padded, self._radius)
        self._blur_pix = _crop_logical(blurred, pad, pad, w, h)
        self._frozen_size = QSize(w, h)
        self.update()

    def freeze_from_widget(self, root_widget: QWidget, global_pos):
        self._backdrop_root = root_widget
        if self._flat:
            self._blur_pix = QPixmap()
            self.update()
            return
        if root_widget is None:
            self.freeze(self.window(), global_pos)
            return

        if getattr(_th, "live_blur_enabled", False) and self._attach_live(root_widget):
            return

        w, h = self.width(), self.height()
        pad = int(self._radius) + 4

        try:
            source = _capture_widget_area(root_widget, global_pos, w, h, pad)
            blurred = make_blur_pixmap(source, self._radius)
            self._blur_pix = _crop_logical(blurred, pad, pad, w, h)
            self._frozen_size = QSize(w, h)
            self.update()
        except Exception:
            # Keep the regular popup fallback for rare detached windows.
            self.freeze(root_widget, global_pos)

    def paintEvent(self, _ev):
        scene = getattr(self, "_live_backdrop", None)
        origin = (scene.origin_for(self) if scene is not None and isValid(scene) else
                  self.mapFromGlobal(self._backdrop_origin)
                  if self._backdrop_origin is not None else QPoint())
        layers = (scene.layers_for(self) if scene is not None and isValid(scene) else
                  [(self._blur_pix, QRectF(origin.toPointF(),
                     self.size().toSizeF() if self._backdrop_origin is None and not self._frozen_size.isEmpty()
                     else self._blur_pix.deviceIndependentSize()), 1.0)]
                  if not self._blur_pix.isNull() else [])
        resizing = self._resize_pending or (scene is not None and isValid(scene) and
                                            (scene._resize_active or scene._blend_timer.isActive()))
        image = (_blur_card_resize_frame(self, layers) if resizing else
                 _gpu_card_frame(self, self._blur_pix, origin, layers))
        if image is not None:
            painter = QPainter(self)
            painter.drawImage(QPoint(), image)
            painter.end()
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)

        r    = QRectF(self.rect())
        path = QPainterPath()
        path.addRoundedRect(r, self._corner, self._corner)

        p.setClipPath(path)
        if self._flat:


            p.fillPath(path, _flat_color())
        else:
            if not self._blur_pix.isNull():
                p.setRenderHint(QPainter.SmoothPixmapTransform)
                for backdrop, destination, alpha in layers:
                    p.setOpacity(alpha)
                    p.drawPixmap(destination, backdrop, QRectF(backdrop.rect()))
                p.setOpacity(1)
            p.fillPath(path, self._tint)
        p.setClipping(False)

        pen = QPen(self._border)
        pen.setWidth(1)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(r.adjusted(0.5, 0.5, -0.5, -0.5), self._corner, self._corner)
        p.end()


# Live "frosted-glass hole" BlurCard
class LiveBlurCard(BlurCard):
    """Qt foreground with a live app backdrop filtered by the shared GPU."""
    def __init__(self, parent=None, radius=18.0, tint=None, border=None, corner=10.0):
        super().__init__(parent, radius, tint, border, corner)
        self._win_ref = None
        self._root_ref = None
        self._cleaned = False

    def freeze_full(self, root_widget, win=None):
        if win is not None:
            self._win_ref = win
        self.refreeze(root_widget)

    def refreeze(self, root_widget):
        if self._flat or self._cleaned or root_widget is None:
            return
        self._root_ref = root_widget
        self.freeze_from_widget(root_widget, self.mapToGlobal(QPoint()))

    def attach_to_window(self, win, root):
        self.freeze_full(root, win)

    def stop_live(self):
        scene = getattr(self, "_live_backdrop", None)
        if scene is not None and isValid(scene):
            scene.stop()

    def start_live(self):
        if self._cleaned:
            return
        scene = getattr(self, "_live_backdrop", None)
        if scene is not None and isValid(scene):
            scene.resume()

    def cleanup(self):
        self.stop_live()
        self._cleaned = True
        self._win_ref = self._root_ref = None


# sync blur for small areas (context menu bg)
def make_blur_pixmap(source: QPixmap, radius: float = 18.0) -> QPixmap:
    """Freeze a native GPU Gaussian blur, retaining a bounded cache for reuse."""
    global _BLUR_CACHE_BYTES
    if source.isNull():
        return QPixmap()
    radius = max(0.0, float(radius))
    key = (source.cacheKey(), source.width(), source.height(),
           source.devicePixelRatioF(), radius)
    cached = _BLUR_CACHE.pop(key, None)
    if cached is not None:
        _BLUR_CACHE[key] = cached
        return QPixmap(cached)
    image = blur_gpu(source, radius)
    out = QPixmap.fromImage(image) if image is not None else _qt_blur_pixmap(source, radius)
    size = out.width() * out.height() * 4
    if size <= _BLUR_CACHE_BUDGET:
        _BLUR_CACHE[key] = QPixmap(out)
        _BLUR_CACHE_BYTES += size
        while _BLUR_CACHE_BYTES > _BLUR_CACHE_BUDGET or len(_BLUR_CACHE) > 32:
            _, old = _BLUR_CACHE.popitem(last=False)
            _BLUR_CACHE_BYTES -= old.width() * old.height() * 4
    return out


def _qt_blur_pixmap(source, radius):
    """Compatibility fallback for machines without the native GPU backend."""
    w, h = source.width(), source.height()
    ratio = source.devicePixelRatioF()
    scene = QGraphicsScene()
    item  = QGraphicsPixmapItem(source)
    fx    = QGraphicsBlurEffect()
    fx.setBlurRadius(radius * ratio)
    fx.setBlurHints(QGraphicsBlurEffect.QualityHint)
    item.setGraphicsEffect(fx)
    scene.addItem(item)
    out = QPixmap(w, h)
    out.setDevicePixelRatio(ratio)
    out.fill(Qt.transparent)
    p = QPainter(out)
    p.setRenderHint(QPainter.Antialiasing)
    rect = QRectF(0, 0, w / ratio, h / ratio)
    scene.render(p, rect, rect)
    p.end()
    return out


# context manager
@contextmanager
def blur_context(parent: QWidget, radius: float = 28.0, darken: float = 0.45):
    ov = BlurOverlay(parent, radius, darken)
    try:
        yield ov
    finally:
        ov.close_overlay()


# Helper: install fade in/out animation pada window/dialog
def install_window_fade(win, fade_in_delay: int = 120,
                        fade_in_ms: int = 140, fade_out_ms: int = 120,
                        *, close_overlay_on_finish: bool = True,
                        freeze_backdrop_immediately: bool = False):

    _prepare_dialog_shell(win)

    if getattr(win, "_window_fade_installed", False):
        return
    win._window_fade_installed = True

    if not _th.animations_enabled:


        win.setWindowOpacity(0.0)
        win._fade_anim = None
        win._closing_with_fade = False

        _orig_show_instant = win.showEvent

        def _new_show_instant(e):
            _orig_show_instant(e)
            ov = getattr(win, "_overlay", None)
            if ov is not None and hasattr(ov, "attach_window_surface"):
                try:
                    ov.attach_window_surface(win, freeze_immediately=freeze_backdrop_immediately)
                except Exception:
                    pass
            win.setWindowOpacity(1.0)

        win.showEvent = _new_show_instant

        def _trigger_overlay_close_instant():
            if not close_overlay_on_finish:
                return
            ov = getattr(win, "_overlay", None)
            if ov is not None and hasattr(ov, "close_overlay"):
                try: ov.close_overlay()
                except Exception: pass
                win._overlay = None

        from PySide6.QtWidgets import QDialog
        if isinstance(win, QDialog):
            _orig_done = win.done

            def _new_done_instant(result):
                if win._closing_with_fade:
                    _orig_done(result)
                    return
                win._closing_with_fade = True
                _trigger_overlay_close_instant()
                _orig_done(result)
            win.done = _new_done_instant
        else:
            _orig_close = win.close

            def _new_close_instant():
                if win._closing_with_fade:
                    return _orig_close()
                win._closing_with_fade = True
                _trigger_overlay_close_instant()
                return _orig_close()
            win.close = _new_close_instant
        return

    win.setWindowOpacity(0.0)
    win._fade_anim = None
    win._closing_with_fade = False

    _orig_show = win.showEvent

    def _new_show(e):
        _orig_show(e)
        ov = getattr(win, "_overlay", None)
        if ov is not None and hasattr(ov, "attach_window_surface"):
            try:
                ov.attach_window_surface(win, freeze_immediately=freeze_backdrop_immediately)
            except Exception:
                pass
        def _start_in():
            if not isValid(win) or win._closing_with_fade:
                return
            anim = QPropertyAnimation(win, b"windowOpacity", win)
            anim.setDuration(fade_in_ms)
            anim.setStartValue(0.0)
            anim.setEndValue(1.0)
            anim.setEasingCurve(QEasingCurve.OutCubic)
            win._fade_anim = anim
            anim.start()
        from PySide6.QtCore import QTimer as _QT
        _QT.singleShot(fade_in_delay, _start_in)

    win.showEvent = _new_show

    def _trigger_overlay_close():
        if not close_overlay_on_finish:
            return
        ov = getattr(win, "_overlay", None)
        if ov is not None and hasattr(ov, "close_overlay"):
            try: ov.close_overlay()
            except Exception: pass
            win._overlay = None  # prevent double-close oleh caller

    # Override done() (QDialog) atau close() (QWidget plain)
    from PySide6.QtWidgets import QDialog
    if isinstance(win, QDialog):
        _orig_done = win.done

        def _new_done(result):
            if win._closing_with_fade:
                _orig_done(result)
                return
            win._closing_with_fade = True
            if win._fade_anim is not None:
                win._fade_anim.stop()
            _trigger_overlay_close()
            anim = QPropertyAnimation(win, b"windowOpacity", win)
            anim.setDuration(fade_out_ms)
            anim.setStartValue(win.windowOpacity())
            anim.setEndValue(0.0)
            anim.setEasingCurve(QEasingCurve.InCubic)
            anim.finished.connect(lambda: _orig_done(result))
            win._fade_anim = anim
            anim.start()
        win.done = _new_done
    else:
        _orig_close = win.close

        def _new_close():
            if win._closing_with_fade:
                return _orig_close()
            win._closing_with_fade = True
            if win._fade_anim is not None:
                win._fade_anim.stop()
            _trigger_overlay_close()
            anim = QPropertyAnimation(win, b"windowOpacity", win)
            anim.setDuration(fade_out_ms)
            anim.setStartValue(win.windowOpacity())
            anim.setEndValue(0.0)
            anim.setEasingCurve(QEasingCurve.InCubic)
            anim.finished.connect(_orig_close)
            win._fade_anim = anim
            anim.start()
        win.close = _new_close
