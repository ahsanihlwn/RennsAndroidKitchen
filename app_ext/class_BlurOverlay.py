# SPDX-License-Identifier: MPL-2.0
from PySide6.QtWidgets import (QWidget, QGraphicsScene,
                                QGraphicsPixmapItem, QGraphicsBlurEffect)
from PySide6.QtCore    import (Qt, QThread, Signal, Slot, QObject, QRect, QRectF, QPoint,
                                QPropertyAnimation, QEasingCurve, Property,
                                QMetaObject, Q_ARG, QTimer)
from PySide6.QtGui     import QPainter, QPixmap, QColor, QPainterPath, QPen, QImage, QRegion
from contextlib        import contextmanager
from main_ext.class_ThemeUi import theme as _th
from core.skia_gpu import render_gpu, skia


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


def _gpu_card_frame(widget, backdrop, origin):
    ratio = widget.devicePixelRatioF()
    color = _flat_color() if widget._flat else widget._tint
    key = (widget.width(), widget.height(), ratio, widget._corner,
           widget._flat, color.rgba(), widget._border.rgba(),
           backdrop.cacheKey(), origin.x(), origin.y())
    cached = getattr(widget, "_skia_card_frame", None)
    if cached is not None and cached[0] == key:
        return cached[1]

    def draw(canvas, bridge):
        rect = skia.Rect.MakeWH(widget.width(), widget.height())
        rounded = skia.RRect.MakeRectXY(rect, widget._corner, widget._corner)
        canvas.save()
        canvas.clipRRect(rounded, True)
        if not widget._flat and not backdrop.isNull():
            bridge.draw_image(canvas, backdrop, QRectF(origin.toPointF(), backdrop.deviceIndependentSize()))
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


# Simpan referensi thread blur agar tidak di-destroy GC saat masih berjalan
_ACTIVE_BLUR_THREADS = set()

def _safely_register_blur_thread(thread: QThread, worker=None):
    if thread is None:
        return
    _ACTIVE_BLUR_THREADS.add(thread)
    def _cleanup():
        _ACTIVE_BLUR_THREADS.discard(thread)
    thread.finished.connect(_cleanup)


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


# blur worker
class _BlurWorker(QObject):
    done = Signal(QPixmap)

    def __init__(self, pix: QPixmap, radius: float = 28.0, darken: float = 0.45):
        super().__init__()
        self._pix  = pix
        self._r    = radius
        self._dark = darken

    def run(self):
        pix  = self._pix
        w, h = pix.width(), pix.height()

        scene = QGraphicsScene()
        item  = QGraphicsPixmapItem(pix)
        fx    = QGraphicsBlurEffect()
        fx.setBlurRadius(self._r)
        fx.setBlurHints(QGraphicsBlurEffect.QualityHint)
        item.setGraphicsEffect(fx)
        scene.addItem(item)

        blurred = QPixmap(w, h)
        blurred.fill(Qt.transparent)
        p = QPainter(blurred)
        p.setRenderHint(QPainter.Antialiasing)
        scene.render(p, QRectF(0, 0, w, h), QRectF(0, 0, w, h))
        p.end()

        p2 = QPainter(blurred)
        p2.fillRect(blurred.rect(), QColor(0, 0, 0, int(self._dark * 255)))
        p2.end()

        self.done.emit(blurred)


# overlay widget
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
        self._thread = None
        self._worker = None
        self._capture_without_backdrop = capture_without_backdrop
        self._opacity = 1.0  # default solid; fade-in di-set 0.0 kalau animate


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
            if not self._flat:


                base_col = source_widget.palette().color(source_widget.backgroundRole())
                if not base_col.isValid() or base_col.alpha() == 0:
                    base_col = QColor(13, 14, 18)  # fallback gelap senada tema
                src = QPixmap(source_widget.size())
                src.fill(base_col)
                source_widget.render(src)
                # bungkus dengan padding (warna base juga) supaya blur tepi tak bocor
                padded = QPixmap(source_widget.width() + pad * 2,
                                 source_widget.height() + pad * 2)
                padded.fill(base_col)
                _pp = QPainter(padded)
                _pp.drawPixmap(pad, pad, src)
                _pp.end()
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
            if not self._flat:
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
        elif sync_blur:


            base_blur = make_blur_pixmap(padded, radius)
            solid = QPixmap(base_blur.size())
            _bc = QColor(13, 14, 18)
            if source_widget is not None:
                _c = source_widget.palette().color(source_widget.backgroundRole())
                if _c.isValid() and _c.alpha() > 0:
                    _bc = _c
            solid.fill(_bc)
            _sp = QPainter(solid)
            _sp.drawPixmap(0, 0, base_blur)
            _sp.fillRect(solid.rect(), QColor(0, 0, 0, int(darken * 255)))
            _sp.end()
            # crop padding lalu set siap
            self._on_done(solid)
            self.raise_()
            self.show()
        else:
            self.raise_()
            self.show()
            self._thread = QThread()
            self._worker = _BlurWorker(padded, radius, darken)
            _safely_register_blur_thread(self._thread, self._worker)
            self._worker.moveToThread(self._thread)
            self._thread.started.connect(self._worker.run)
            self._worker.done.connect(self._on_done)
            self._worker.done.connect(self._thread.quit)
            self._thread.start()

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

    def _on_done(self, pix: QPixmap):
        pad = getattr(self, "_pad", 0)
        if pad > 0:
            w = self.width()
            h = self.height()
            self._pix = pix.copy(pad, pad, w, h)
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
        if watched is self.parentWidget() and event.type() == QEvent.Resize:
            self.setGeometry(watched.rect())
            self.update()
        return False

    def paintEvent(self, _ev):
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
            p.drawPixmap(0, 0, self._pix)

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
                    # A glass dialog needs the app pixels before this dark
                    # backdrop is painted; otherwise its blur becomes black.
                    hide_backdrop = self._capture_without_backdrop and self.isVisible()
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
            if old is not None:
                old.hide()
                old.deleteLater()
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
            src = QPixmap(source_widget.size())
            src.fill(base_col)
            source_widget.render(src)
            blurred = make_blur_pixmap(src, radius)
            # Komposit di atas base SOLID supaya tidak ada lubang alpha dari
            # tepi blur (make_blur_pixmap mengisi transparan → alpha menyebar).
            out = QPixmap(blurred.size())
            out.fill(base_col)
            p = QPainter(out)
            p.drawPixmap(0, 0, blurred)
            p.fillRect(out.rect(), QColor(0, 0, 0, int(darken * 255)))
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
        if hasattr(self, "_worker") and self._worker:
            try:
                self._worker.done.disconnect()
            except Exception:
                pass
        if hasattr(self, "_thread") and self._thread:
            t = self._thread
            self._thread = None
            try:
                if t.isRunning():
                    t.quit()
                    t.wait(100)
            except (RuntimeError, Exception):
                pass
            # t tetap disimpan di _ACTIVE_BLUR_THREADS sampai t.finished,
            # sehingga tidak akan pernah memicu 'QThread: Destroyed while thread is still running'.
        self.hide()
        self.deleteLater()


# blurred card background for context menus
class BlurCard(QWidget):

    def __init__(self, parent=None, radius: float = 18.0, tint: QColor = None,
                 border: QColor = None, corner: float = 10.0):
        super().__init__(parent)
        self._blur_pix = QPixmap()
        self._backdrop_origin = None
        self._radius   = radius
        self._tint     = tint   or QColor(0, 0, 0, 40)
        self._border   = border or QColor(255, 255, 255, 28)
        self._corner   = corner
        self._flat     = not _th.blur_enabled
        self.setAttribute(Qt.WA_TranslucentBackground)

    def pin_backdrop_to(self, global_pos):

        self._backdrop_origin = QPoint(global_pos) if global_pos is not None else None
        self.update()

    def freeze(self, root_widget: QWidget, global_pos):
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
        self._blur_pix = blurred.copy(pad, pad, w, h)
        self.update()

    def freeze_from_widget(self, root_widget: QWidget, global_pos):

        if self._flat:
            self._blur_pix = QPixmap()
            self.update()
            return
        if root_widget is None:
            self.freeze(self.window(), global_pos)
            return

        w, h = self.width(), self.height()
        pad = int(self._radius) + 4
        rw, rh = w + pad * 2, h + pad * 2

        try:
            root_global = root_widget.mapToGlobal(root_widget.rect().topLeft())
            local_x = global_pos.x() - root_global.x()
            local_y = global_pos.y() - root_global.y()

            source = QPixmap(rw, rh)
            source.fill(Qt.transparent)
            painter = QPainter(source)


            root_widget.render(painter, QPoint(pad - local_x, pad - local_y))
            painter.end()

            blurred = make_blur_pixmap(source, self._radius)
            self._blur_pix = blurred.copy(pad, pad, w, h)
            self.update()
        except Exception:
            # Keep the regular popup fallback for rare detached windows.
            self.freeze(root_widget, global_pos)

    def paintEvent(self, _ev):
        origin = (self.mapFromGlobal(self._backdrop_origin)
                  if self._backdrop_origin is not None else QPoint())
        image = _gpu_card_frame(self, self._blur_pix, origin)
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
                origin = (self.mapFromGlobal(self._backdrop_origin)
                          if self._backdrop_origin is not None else QPoint())
                p.drawPixmap(origin, self._blur_pix)
            p.fillPath(path, self._tint)
        p.setClipping(False)

        pen = QPen(self._border)
        pen.setWidth(1)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(r.adjusted(0.5, 0.5, -0.5, -0.5), self._corner, self._corner)
        p.end()


# Live blur worker — runs in QThread (untuk LiveBlurCard)
class _LiveBlurWorker(QObject):

    done = Signal(object)   # QImage hasil blur

    def __init__(self, radius: float):
        super().__init__()
        self._radius = radius

    @Slot("QImage")
    def process(self, img):
        px      = QPixmap.fromImage(img)
        blurred = make_blur_pixmap(px, self._radius)
        self.done.emit(blurred.toImage())


# Live "frosted-glass hole" BlurCard
class LiveBlurCard(QWidget):


    def __init__(self, parent=None, radius: float = 18.0,
                 tint: QColor = None, border: QColor = None, corner: float = 10.0):
        super().__init__(parent)
        self._radius      = radius
        self._tint        = tint   or QColor(0, 0, 0, 60)
        self._border      = border or QColor(255, 255, 255, 28)
        self._corner      = corner
        self._crop        = QPixmap()
        self._win_ref     = None
        self._root_ref    = None
        self._busy        = False    # worker sedang proses → skip frame
        self._move_filter = None
        self._timer       = None
        self._flat        = not _th.blur_enabled
        self.setAttribute(Qt.WA_TranslucentBackground)

        # Worker thread setup — skip sama sekali kalau blur dimatikan (flat gray
        # statis, ga perlu capture/blur berulang tiap N ms).
        self._thread = None
        self._worker = None
        if not self._flat:
            self._thread = QThread()
            self._worker = _LiveBlurWorker(radius)
            _safely_register_blur_thread(self._thread, self._worker)
            self._worker.moveToThread(self._thread)
            self._worker.done.connect(self._on_blur_done)
            self._thread.start()

    # internal

    def _grab_area(self):

        win  = self._win_ref
        root = self._root_ref
        if win is None or root is None:
            return None
        screen = root.screen() if hasattr(root, "screen") else None
        if screen is None:
            from PySide6.QtGui import QGuiApplication
            screen = QGuiApplication.primaryScreen()
        pad = int(self._radius) + 4
        wg  = win.mapToGlobal(win.rect().topLeft())
        cw, ch = win.width(), win.height()
        raw = screen.grabWindow(0, wg.x() - pad, wg.y() - pad,
                                cw + pad * 2, ch + pad * 2)
        self._pending_pad = pad
        self._pending_cw  = cw
        self._pending_ch  = ch
        return raw

    def _schedule_capture(self):

        if self._flat or self._busy or self._win_ref is None:
            return
        raw = self._grab_area()
        if raw is None or raw.isNull():
            return
        self._busy = True
        QMetaObject.invokeMethod(
            self._worker, "process",
            Qt.QueuedConnection,
            Q_ARG("QImage", raw.toImage()),
        )

    def _on_blur_done(self, img):

        self._busy = False
        px  = QPixmap.fromImage(img)
        pad = getattr(self, "_pending_pad", int(self._radius) + 4)
        cw  = getattr(self, "_pending_cw", px.width()  - pad * 2)
        ch  = getattr(self, "_pending_ch", px.height() - pad * 2)
        self._crop = px.copy(pad, pad, cw, ch)
        self.update()

    def _update_crop(self):
        self.update()

    # public API

    def freeze_full(self, root_widget: QWidget, win: QWidget = None):
        if win is not None:
            self._win_ref = win
        self._root_ref = root_widget
        self._schedule_capture()

    def refreeze(self, root_widget: QWidget):
        self._root_ref = root_widget
        self._schedule_capture()

    def attach_to_window(self, win: QWidget, root: QWidget):
        self._win_ref  = win
        self._root_ref = root
        _card = self

        if self._flat:
            # Flat gray statis — ga perlu timer capture berulang.
            return

        from PySide6.QtCore import QTimer as _QT, QEvent as _QEv

        # Live timer — capture tiap 100ms, worker skip frame kalau masih sibuk
        _t = _QT(win)
        _t.timeout.connect(_card._schedule_capture)
        _t.start(50)
        self._timer = _t

        # Move filter — saat drag, update crop dari snapshot existing (instant, no blur)
        class _MoveFilter(QObject):
            def eventFilter(self_, obj, ev):
                if ev.type() == _QEv.Move:
                    _card._update_crop()
                return False

        self._move_filter = _MoveFilter(win)
        win.installEventFilter(self._move_filter)

    def stop_live(self):
        if self._timer:
            self._timer.stop()

    def start_live(self):
        if self._timer:
            self._timer.start(50)
        self._schedule_capture()

    def cleanup(self):
        self.stop_live()
        if hasattr(self, "_worker") and self._worker:
            try:
                self._worker.done.disconnect()
            except Exception:
                pass
        if hasattr(self, "_thread") and self._thread:
            t = self._thread
            self._thread = None
            try:
                if t.isRunning():
                    t.quit()
                    t.wait(100)
            except (RuntimeError, Exception):
                pass

    # paint

    def paintEvent(self, _ev):
        image = _gpu_card_frame(self, self._crop, QPoint())
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
            if not self._crop.isNull():
                p.drawPixmap(0, 0, self._crop)
            p.fillPath(path, self._tint)
        p.setClipping(False)

        pen = QPen(self._border)
        pen.setWidth(1)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(r.adjusted(0.5, 0.5, -0.5, -0.5), self._corner, self._corner)
        p.end()


# sync blur for small areas (context menu bg)
def make_blur_pixmap(source: QPixmap, radius: float = 18.0) -> QPixmap:

    w, h = source.width(), source.height()
    scene = QGraphicsScene()
    item  = QGraphicsPixmapItem(source)
    fx    = QGraphicsBlurEffect()
    fx.setBlurRadius(radius)
    fx.setBlurHints(QGraphicsBlurEffect.QualityHint)
    item.setGraphicsEffect(fx)
    scene.addItem(item)
    out = QPixmap(w, h)
    out.fill(Qt.transparent)
    p = QPainter(out)
    p.setRenderHint(QPainter.Antialiasing)
    scene.render(p, QRectF(0, 0, w, h), QRectF(0, 0, w, h))
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
                        fade_in_ms: int = 140, fade_out_ms: int = 120):

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
                    ov.attach_window_surface(win)
                except Exception:
                    pass
            win.setWindowOpacity(1.0)

        win.showEvent = _new_show_instant

        def _trigger_overlay_close_instant():
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
                ov.attach_window_surface(win)
            except Exception:
                pass
        def _start_in():
            if win is None:
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
