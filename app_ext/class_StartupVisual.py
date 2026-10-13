# SPDX-License-Identifier: MPL-2.0
import os
import re
import sys
import time
import math
import xml.etree.ElementTree as ET

from PySide6.QtCore import Qt, QTimer, QRect, QRectF, QSizeF, QPointF, QPoint, QEvent, Signal
from PySide6.QtGui import QColor, QIcon, QImage, QPainter, QPainterPath, QPen, QPixmap, QRegion, QTransform
from PySide6.QtWidgets import QWidget


_ANIMATION_EPOCH = time.monotonic()
_ICON_SIZE = 68
_BRAND_HEIGHT = 20
_SVG_TOKENS = re.compile(r"[MmLlCcQqZz]|[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?")


def _logo_path():
    try:
        from icons.handler import icon
        path = icon("splash")
        if os.path.isfile(path) and not path.endswith("fallback.png"):
            return path
    except Exception:
        pass
    roots = (
        os.path.dirname(os.path.dirname(__file__)),
        os.path.dirname(sys.executable),
    )
    for root in roots:
        if root:
            for filename in ("splash.svg", "ic_launcher_white.svg", "ic_launcher.svg"):
                path = os.path.join(root, "icons", filename)
                if os.path.isfile(path):
                    return path
    return ""


def _brand_path():
    roots = (
        os.path.dirname(os.path.dirname(__file__)),
        os.path.dirname(sys.executable),
    )
    for root in roots:
        if root:
            path = os.path.join(root, "icons", "rennsproject_logo.svg")
            if os.path.isfile(path):
                return path
    return ""


def _read_svg_path(data):

    tokens = _SVG_TOKENS.findall(data)
    path = QPainterPath()
    i = 0
    cur_x, cur_y = 0.0, 0.0
    start_x, start_y = 0.0, 0.0
    cmd = None
    while i < len(tokens):
        token = tokens[i]
        if token in "MmLlCcQqZz":
            cmd = token
            i += 1
        if cmd in ("Z", "z"):
            path.closeSubpath()
            cur_x, cur_y = start_x, start_y
            cmd = None
        elif cmd == "M":
            cur_x = float(tokens[i])
            cur_y = float(tokens[i + 1])
            start_x, start_y = cur_x, cur_y
            path.moveTo(cur_x, cur_y)
            i += 2
            cmd = "L"
        elif cmd == "m":
            cur_x += float(tokens[i])
            cur_y += float(tokens[i + 1])
            start_x, start_y = cur_x, cur_y
            path.moveTo(cur_x, cur_y)
            i += 2
            cmd = "l"
        elif cmd == "L":
            cur_x = float(tokens[i])
            cur_y = float(tokens[i + 1])
            path.lineTo(cur_x, cur_y)
            i += 2
        elif cmd == "l":
            cur_x += float(tokens[i])
            cur_y += float(tokens[i + 1])
            path.lineTo(cur_x, cur_y)
            i += 2
        elif cmd == "C":
            x1, y1 = float(tokens[i]), float(tokens[i + 1])
            x2, y2 = float(tokens[i + 2]), float(tokens[i + 3])
            cur_x, cur_y = float(tokens[i + 4]), float(tokens[i + 5])
            path.cubicTo(x1, y1, x2, y2, cur_x, cur_y)
            i += 6
        elif cmd == "c":
            x1 = cur_x + float(tokens[i])
            y1 = cur_y + float(tokens[i + 1])
            x2 = cur_x + float(tokens[i + 2])
            y2 = cur_y + float(tokens[i + 3])
            cur_x += float(tokens[i + 4])
            cur_y += float(tokens[i + 5])
            path.cubicTo(x1, y1, x2, y2, cur_x, cur_y)
            i += 6
        elif cmd == "Q":
            x1, y1 = float(tokens[i]), float(tokens[i + 1])
            cur_x, cur_y = float(tokens[i + 2]), float(tokens[i + 3])
            path.quadTo(x1, y1, cur_x, cur_y)
            i += 4
        elif cmd == "q":
            x1 = cur_x + float(tokens[i])
            y1 = cur_y + float(tokens[i + 1])
            cur_x += float(tokens[i + 2])
            cur_y += float(tokens[i + 3])
            path.quadTo(x1, y1, cur_x, cur_y)
            i += 4
        else:
            i += 1
    return path


def _logo_outline(svg_path):

    try:
        root = ET.parse(svg_path).getroot()
        view_x, view_y, view_width, view_height = (
            float(value) for value in root.attrib["viewBox"].split()
        )
        group = next(node for node in root.iter() if node.get("id") == "object-1")
        transform_attr = group.get("transform", "")
        translate = re.search(
            r"translate\(\s*([-+\d.]+)[ ,]+([-+\d.]+)\s*\)",
            transform_attr,
        )
        tx = float(translate.group(1)) if translate else 0.0
        ty = float(translate.group(2)) if translate else 0.0
        paths = [
            node for node in group
            if node.tag.rsplit("}", 1)[-1] == "path"
        ]
        if not paths:
            return QPainterPath()
        if len(paths) == 1:
            outline = _read_svg_path(paths[0].attrib["d"])
        else:
            outline = _read_svg_path(paths[0].attrib["d"]).united(
                _read_svg_path(paths[1].attrib["d"])
            )
        scale = _ICON_SIZE / view_width
        offset_x = (tx - view_x) * scale
        offset_y = (ty - view_y) * scale
        return QTransform(scale, 0, 0, scale, offset_x, offset_y).map(outline)
    except (ET.ParseError, OSError, KeyError, StopIteration, ValueError, IndexError):
        return QPainterPath()


class StartupVisual(QWidget):


    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_OpaquePaintEvent)
        self.setFocusPolicy(Qt.StrongFocus)

        # Load the saved palette before the first window paint. The main UI
        # reads the same palette later, so the reveal has no color jump.
        from main_ext.class_ThemeUi import theme
        theme.load()
        self._background = QColor(theme.BG)
        self._foreground = QColor(theme.FG)
        self._accent = QColor(theme.accent_hex)

        self.logo_path = _logo_path()
        self.brand_path = _brand_path()
        self._outline = _logo_outline(self.logo_path) if self.logo_path else QPainterPath()
        source = QIcon(self.logo_path).pixmap(_ICON_SIZE, _ICON_SIZE) if self.logo_path else QPixmap()
        self._themed_logo = QPixmap(source.size())
        if not source.isNull():
            self._themed_logo.setDevicePixelRatio(source.devicePixelRatio())
        self._themed_logo.fill(Qt.transparent)
        if not source.isNull():
            painter = QPainter(self._themed_logo)
            painter.drawPixmap(0, 0, source)
            painter.setCompositionMode(QPainter.CompositionMode_SourceIn)
            painter.fillRect(self._themed_logo.rect(), self._foreground)
            painter.end()

        brand_source = QIcon(self.brand_path).pixmap(400, 60) if self.brand_path else QPixmap()
        self._themed_brand = QPixmap()
        if not brand_source.isNull():
            brand_width = round(_BRAND_HEIGHT * brand_source.width() / brand_source.height())
            brand_source = brand_source.scaled(
                brand_width, _BRAND_HEIGHT, Qt.IgnoreAspectRatio, Qt.SmoothTransformation
            )
            self._themed_brand = QPixmap(brand_source.size())
            self._themed_brand.setDevicePixelRatio(brand_source.devicePixelRatio())
            self._themed_brand.fill(Qt.transparent)
            painter = QPainter(self._themed_brand)
            painter.drawPixmap(0, 0, brand_source)
            painter.setCompositionMode(QPainter.CompositionMode_SourceIn)
            painter.fillRect(self._themed_brand.rect(), self._foreground)
            painter.end()

        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.PreciseTimer)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._update_glint)
        if not self._outline.isEmpty():
            self._timer.start()

    def _update_glint(self):
        dirty_size = _ICON_SIZE + 24
        self.update(QRect(
            (self.width() - dirty_size) // 2,
            (self.height() - dirty_size) // 2,
            dirty_size,
            dirty_size,
        ))

    def stop(self):
        self._timer.stop()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(event.rect(), self._background)
        painter.setRenderHint(QPainter.Antialiasing)

        center_x = self.width() / 2
        center_y = self.height() / 2
        logo_x = round(center_x - _ICON_SIZE / 2)
        logo_y = round(center_y - _ICON_SIZE / 2 - 22)
        if not self._themed_logo.isNull():
            logo_size = self._themed_logo.deviceIndependentSize()
            logo_x = round(center_x - logo_size.width() / 2)
            logo_y = round(center_y - logo_size.height() / 2)
            logo_y -= 22
            painter.drawPixmap(
                logo_x,
                logo_y,
                self._themed_logo,
            )

        if not self._outline.isEmpty():
            painter.translate(logo_x, logo_y)
            painter.setBrush(Qt.NoBrush)
            track = QColor(self._accent)
            track.setAlpha(42)
            painter.setPen(QPen(track, 0.7))
            painter.drawPath(self._outline)

            phase = ((time.monotonic() - _ANIMATION_EPOCH) % 1.6) / 1.6
            tail = phase + 0.14
            glint = self._outline.trimmed(phase, min(tail, 1.0))
            if tail > 1.0:
                glint.addPath(self._outline.trimmed(0.0, tail - 1.0))
            for thickness, alpha in ((4, 28), (1.6, 235)):
                color = QColor(self._accent)
                color.setAlpha(alpha)
                pen = QPen(color)
                pen.setWidthF(thickness)
                pen.setCapStyle(Qt.RoundCap)
                pen.setJoinStyle(Qt.RoundJoin)
                painter.setPen(pen)
                painter.drawPath(glint)

        # The glint path is drawn in logo-local coordinates. Restore widget
        # coordinates before centering the brand logo.
        painter.resetTransform()
        if not self._themed_brand.isNull():
            brand_size = self._themed_brand.deviceIndependentSize()
            left = round(center_x - brand_size.width() / 2)
            brand_center_y = self.height() - max(26, round(self.height() * 0.045)) - 4
            top = round(brand_center_y - brand_size.height() / 2)
            painter.drawPixmap(
                left,
                top,
                self._themed_brand,
            )
        painter.end()

    def mousePressEvent(self, event):
        event.accept()

    def mouseReleaseEvent(self, event):
        event.accept()


class StartupContentReveal(QWidget):


    finished = Signal()
    _DURATION = 0.28
    _FIRST_DELAY = 0.10
    _ROW_DELAY = 0.06

    def __init__(self, page, project_list=None, cover_until_start=False,
                 excluded_widgets=(), bottom_up=False, freeze_content=False):
        super().__init__(page)
        from main_ext.class_ThemeUi import theme

        self._page = page
        self._project_list = project_list
        self._excluded_widgets = tuple(excluded_widgets)
        self._bottom_up = bottom_up
        self._background = QColor(theme.BG)
        self._cover_until_start = cover_until_start
        self._started_at = None
        self._end_at = 0.0
        # An opaque snapshot keeps translucent reveal paints from repainting
        # the entire live FileTree on every frame. Other page reveals retain
        # their live content, including interactive excluded widgets.
        self._freeze_content = bool(freeze_content and not self._excluded_widgets)
        self._snapshot = None
        self._snapshot_frame = None
        self._snapshot_frame_key = None
        self._gpu_frame = None
        self._snapshot_gpu_error = None
        self._snapshot_presented = False
        self._awaiting_presentation = False
        self._last_band_alphas = None
        self._stopped = False
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_NoSystemBackground)
        self.setStyleSheet("background: transparent;")
        self.setGeometry(page.rect())
        page.installEventFilter(self)

        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.PreciseTimer)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._tick)
        self._presentation_timer = QTimer(self)
        self._presentation_timer.setSingleShot(True)
        self._presentation_timer.setInterval(1000)
        self._presentation_timer.timeout.connect(self._presentation_timeout)

    def eventFilter(self, watched, event):
        if watched is self._page and event.type() in (QEvent.Resize, QEvent.DevicePixelRatioChange):
            self.setGeometry(self._page.rect())
            if self._snapshot is not None:
                # Resize ends the short freeze so the live layout is never
                # stretched, clipped or recaptured during an animation.
                self.stop()
                self.finished.emit()
        return super().eventFilter(watched, event)

    def prepare_snapshot(self):
        """Capture Qt text/widgets once, before the loader starts fading."""
        if not self._freeze_content or self._stopped or self._snapshot is not None:
            return
        visible = self.isVisible()
        self.hide()
        try:
            self._page.ensurePolished()
            if self._page.layout() is not None:
                self._page.layout().activate()
            self.setGeometry(self._page.rect())
            snapshot = self._page.grab().toImage()
            if not snapshot.isNull():
                self._snapshot = snapshot.convertToFormat(QImage.Format_ARGB32_Premultiplied)
                self.setAttribute(Qt.WA_TranslucentBackground, False)
                self.setAttribute(Qt.WA_NoSystemBackground, False)
                self.setStyleSheet("")
                self.setAttribute(Qt.WA_OpaquePaintEvent)
                from core.skia_gpu import gpu_available
                from PySide6.QtWidgets import QApplication
                allowed = getattr(QApplication.instance(), "_skia_direct_composition_allowed", None)
                if allowed is None:
                    from PySide6.QtQuick import QQuickWindow, QSGRendererInterface
                    allowed = QQuickWindow.graphicsApi() == QSGRendererInterface.GraphicsApi.OpenGL
                if gpu_available() and allowed:
                    from core.skia_snapshot import SnapshotGpuFrame
                    self._gpu_frame = SnapshotGpuFrame(self, self._snapshot, self._background)
                    self._gpu_frame.frameSwapped.connect(self._on_snapshot_presented)
                    # Initialize/upload while the loader is still opaque.
                    # This one preparation readback never runs in the fade.
                    self._gpu_frame.grabFramebuffer()
                    if not self._gpu_frame.isValid() or self._gpu_frame.error:
                        self._snapshot_gpu_failed()
                    else:
                        self._gpu_frame.show()
                if self._gpu_frame is None:
                    self._snapshot_frame = self._compose_snapshot(self._bands(), None)
        finally:
            if visible:
                self.show()

    def _snapshot_gpu_failed(self):
        frame = self._gpu_frame
        self._gpu_frame = None
        if frame is not None:
            self._snapshot_gpu_error = frame.error or "Qt snapshot framebuffer is unavailable"
            frame.shutdown()
            frame.hide()
            frame.deleteLater()
        self.update()
        if self._awaiting_presentation:
            self._begin_clock()

    def _on_snapshot_presented(self):
        window = self._page.windowHandle()
        if window is not None and window.isExposed():
            self._snapshot_presented = True
            if self._awaiting_presentation:
                self._begin_clock()

    def _presentation_timeout(self):
        self._snapshot_gpu_failed()

    def _cover_state(self):
        bands = self._bands()
        elapsed = None if self._started_at is None else time.monotonic() - self._started_at
        return bands, self._band_alphas(bands, elapsed)

    def _band_alphas(self, bands, elapsed):
        return tuple(255 if elapsed is None else round(255 * (1.0 - max(0.0, min(1.0,
            (elapsed - self._FIRST_DELAY - index * self._ROW_DELAY) / self._DURATION))) ** 3)
            for index in range(len(bands)))

    def _compose_snapshot(self, bands, elapsed, damage=None):
        from core.skia_gpu import render_gpu, skia
        alphas = self._band_alphas(bands, elapsed)
        ratio = self.devicePixelRatioF()
        physical_top = 0 if damage is None else math.floor(damage.top() * ratio)
        physical_bottom = self._snapshot.height() if damage is None else math.ceil((damage.bottom() + 1) * ratio)
        origin_y = physical_top / ratio
        size = self.size() if damage is None else QSizeF(self.width(), (physical_bottom - physical_top) / ratio)
        key = (self._snapshot.cacheKey(), self.width(), self.height(), tuple(bands), alphas, physical_top, physical_bottom)
        if key == self._snapshot_frame_key:
            return self._snapshot_frame

        def draw(canvas, bridge):
            canvas.clear(bridge.color(self._background))
            canvas.save()
            canvas.scale(1.0 / ratio, 1.0 / ratio)
            canvas.drawImage(bridge.image(self._snapshot), 0, -physical_top,
                             skia.SamplingOptions(skia.FilterMode.kNearest))
            canvas.restore()
            canvas.translate(0, -origin_y)
            for (top, bottom), alpha in zip(bands, alphas):
                if alpha:
                    color = QColor(self._background)
                    color.setAlpha(alpha)
                    canvas.drawRect(skia.Rect.MakeXYWH(0, top, self.width(), bottom - top),
                                    skia.Paint(Color4f=bridge.color(color)))

        frame = render_gpu(size, ratio, draw)
        if frame is None:
            # Native Qt fallback still reuses the snapshot and its Qt glyphs.
            frame = QImage(self._snapshot) if damage is None else self._snapshot.copy(
                0, physical_top, self._snapshot.width(), physical_bottom - physical_top)
            painter = QPainter(frame)
            painter.translate(0, -origin_y)
            for (top, bottom), alpha in zip(bands, alphas):
                if alpha:
                    color = QColor(self._background)
                    color.setAlpha(alpha)
                    painter.fillRect(QRect(0, top, self.width(), bottom - top), color)
            painter.end()
        else:
            frame = frame.convertToFormat(QImage.Format_ARGB32_Premultiplied)
        self._snapshot_frame_key = key
        self._snapshot_frame = frame
        return frame

    def _bands(self):
        height = self.height()
        if height <= 0:
            return []

        if self._project_list is None or not self._project_list.isVisible():
            cuts = [round(height * part / 5) for part in range(6)]
        else:
            project_list = self._project_list
            list_top = max(0, min(height, project_list.mapTo(self._page, QPoint()).y()))
            list_bottom = max(list_top, min(height, list_top + project_list.height()))
            cuts = [0, list_top]
            viewport = project_list.viewport()
            for index in range(project_list.count()):
                item = project_list.item(index)
                if item.isHidden():
                    continue
                row = project_list.visualItemRect(item)
                if row.top() >= viewport.height():
                    break
                if row.bottom() < 0:
                    continue
                bottom = viewport.mapTo(self._page, QPoint(0, row.bottom() + 1)).y()
                bottom = min(bottom, list_bottom)
                if list_top < bottom and bottom > cuts[-1]:
                    cuts.append(bottom)
            cuts.append(height)

        cuts = sorted(set(cuts))
        bands = list(zip(cuts, cuts[1:]))
        return bands[::-1] if self._bottom_up else bands

    def start(self):
        if self._stopped:
            return
        if self._freeze_content and self._snapshot is None:
            self.prepare_snapshot()
        self.show()
        self.raise_()
        if (self._gpu_frame is not None and not self._snapshot_presented
                and not self._page.testAttribute(Qt.WA_DontShowOnScreen)):
            # First native presentation can compile Qt's compositor shaders.
            # Keep the cover fully opaque until that one-off work is complete.
            self._awaiting_presentation = True
            self._presentation_timer.start()
            self._gpu_frame.update()
            return
        self._begin_clock()

    def _begin_clock(self):
        if self._stopped or self._started_at is not None:
            return
        self._presentation_timer.stop()
        self._awaiting_presentation = False
        self._started_at = time.monotonic()
        self._last_band_alphas = self._band_alphas(self._bands(), None)
        self._end_at = (self._FIRST_DELAY
                        + max(0, len(self._bands()) - 1) * self._ROW_DELAY
                        + self._DURATION)
        self._timer.start()
        self.update()

    def _tick(self):
        if self._started_at is None:
            return
        if self._gpu_frame is not None:
            self._gpu_frame.update()
        elif self._snapshot is not None:
            bands, alphas = self._cover_state()
            changed = [(top, bottom) for index, (top, bottom) in enumerate(bands)
                       if self._last_band_alphas is None or alphas[index] != self._last_band_alphas[index]]
            self._last_band_alphas = alphas
            if changed:
                top = min(band[0] for band in changed)
                bottom = max(band[1] for band in changed)
                self.update(QRect(0, top, self.width(), bottom - top))
        else:
            self.update()
        if time.monotonic() - self._started_at >= self._end_at:
            self.stop()
            self.finished.emit()

    def stop(self):
        if self._stopped:
            return
        self._stopped = True
        self._timer.stop()
        self._presentation_timer.stop()
        if self._gpu_frame is not None:
            self._gpu_frame.shutdown()
            self._gpu_frame = None
        self.hide()
        self._page.removeEventFilter(self)
        self._snapshot = self._snapshot_frame = None
        self.deleteLater()

    def paintEvent(self, event):
        if self._gpu_frame is not None:
            # The GL child presents Skia's framebuffer directly. Keep its
            # opaque parent inexpensive; never compose/read back a duplicate.
            painter = QPainter(self)
            painter.fillRect(event.rect(), self._background)
            painter.end()
            return
        if self._snapshot is not None:
            elapsed = None if self._started_at is None else time.monotonic() - self._started_at
            damage = event.rect()
            # Read back only the changing bands. Static background and fully
            # revealed rows remain in Qt's opaque backing store.
            frame = self._compose_snapshot(self._bands(), elapsed, damage)
            painter = QPainter(self)
            ratio = self.devicePixelRatioF()
            painter.drawImage(QPointF(0, math.floor(damage.top() * ratio) / ratio), frame)
            painter.end()
            return
        region = QRegion(self.rect())
        for widget in self._excluded_widgets:
            if widget.isVisible():
                region -= QRegion(QRect(widget.mapTo(self._page, QPoint()), widget.size()))
        if self._started_at is None:
            if self._cover_until_start:
                painter = QPainter(self)
                painter.setClipRegion(region)
                painter.fillRect(event.rect(), self._background)
                painter.end()
            return
        bands = self._bands()
        elapsed = time.monotonic() - self._started_at
        self._end_at = max(self._end_at, self._FIRST_DELAY
                           + max(0, len(bands) - 1) * self._ROW_DELAY
                           + self._DURATION)

        painter = QPainter(self)
        painter.setClipRegion(region)
        for index, (top, bottom) in enumerate(bands):
            progress = max(0.0, min(1.0,
                                   (elapsed - self._FIRST_DELAY - index * self._ROW_DELAY)
                                   / self._DURATION))
            alpha = round(255 * (1.0 - progress) ** 3)
            if alpha:
                color = QColor(self._background)
                color.setAlpha(alpha)
                painter.fillRect(QRect(0, top, self.width(), bottom - top), color)
        painter.end()
