# SPDX-License-Identifier: MPL-2.0
import os
import re
import sys
import time
import xml.etree.ElementTree as ET

from PySide6.QtCore import Qt, QTimer, QRect, QPoint, QEvent, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap, QRegion, QTransform
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
                 excluded_widgets=(), bottom_up=False):
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
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_NoSystemBackground)
        self.setStyleSheet("background: transparent;")
        self.setGeometry(page.rect())
        page.installEventFilter(self)

        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.PreciseTimer)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._tick)

    def eventFilter(self, watched, event):
        if watched is self._page and event.type() == QEvent.Resize:
            self.setGeometry(self._page.rect())
        return super().eventFilter(watched, event)

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
        self.show()
        self.raise_()
        self._started_at = time.monotonic()
        self._end_at = (self._FIRST_DELAY
                        + max(0, len(self._bands()) - 1) * self._ROW_DELAY
                        + self._DURATION)
        self._timer.start()
        self.update()

    def _tick(self):
        if self._started_at is None:
            return
        self.update()
        if time.monotonic() - self._started_at >= self._end_at:
            self.stop()
            self.finished.emit()

    def stop(self):

        self._timer.stop()
        self.hide()
        self.deleteLater()

    def paintEvent(self, event):
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
