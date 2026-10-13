# SPDX-License-Identifier: MPL-2.0
"""Opaque startup scene with cached Skia surfaces and native Qt font pixels."""
import time

from PySide6.QtCore import QObject, QEvent, QPoint, QRectF, QTimer, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QRegion
from PySide6.QtWidgets import QWidget, QStyle, QApplication
from shiboken6 import isValid

from app_ext.class_StartupVisual import StartupContentReveal
from core.skia_button import button_surface
from core.skia_patch import SurfacePatch
from core.skia_widgets import _option


class DriveUsageTiming:
    duration = 840
    stagger = duration * .05

    @classmethod
    def total(cls, count):
        return cls.duration + max(0, count - 1) * cls.stagger

    @classmethod
    def progress(cls, elapsed_ms, index):
        local = max(0.0, min(cls.duration, elapsed_ms - index * cls.stagger))
        if local <= 420:
            return 1.0 - (1.0 - local / 700) ** 3
        return 1.0 - .4 ** 3 * (1.0 - (local - 420) / 420) ** 4.5


class _SuppressPaint(QObject):
    def __init__(self, widgets, parent):
        super().__init__(parent)
        self._widgets = set(widgets)

    def eventFilter(self, obj, event):
        return event.type() == QEvent.Paint and obj in self._widgets


class _DriveLayer:
    def __init__(self, root, entry):
        self.fraction = entry["usage_fraction"]
        self.percent = (self.fraction * 100 if entry.get("usage_percent") is None
                        else entry["usage_percent"])
        usage = entry["usage"]
        self.used, self.total = usage._used_gb, usage._total_gb
        self.labels = []
        for widget in (usage, entry["percent"]):
            rect = QRectF(widget.contentsRect()).translated(widget.mapTo(root, QPoint()))
            self.labels.append((rect, widget.font(), widget.palette().color(widget.foregroundRole()),
                                widget.alignment(), widget.fontMetrics()))
        metrics, width = self.labels[0][4], self.labels[0][0].width()
        self.short = metrics.horizontalAdvance(usage._final_variants[0]) > width
        bar = entry["bar"]
        option = _option(bar)
        self.bar_rect = QRectF(bar.rect()).translated(bar.mapTo(root, QPoint()))
        self.chunk_rect = QRectF(bar.style().subElementRect(QStyle.SE_ProgressBarContents, option, bar))
        self.chunk_rect.translate(bar.mapTo(root, QPoint()))
        self.reverse = option.invertedAppearance ^ (bar.layoutDirection() == Qt.RightToLeft)
        ratio = root.devicePixelRatioF()
        shape = button_surface(bar, option)
        chunk = button_surface(bar, option, "chunk")
        self.track = SurfacePatch(shape, ratio) if shape is not None else None
        self.chunk = SurfacePatch(chunk, ratio) if chunk is not None else None

    def draw(self, painter, progress):
        usage = f"{self.used * progress:.0f}/{self.total:.0f}" + ("" if self.short else " GB")
        metrics, rect = self.labels[0][4], self.labels[0][0]
        usage = metrics.elidedText(usage, Qt.ElideRight, round(rect.width()))
        for (rect, font, color, align, _), text in zip(self.labels, (usage, f"{int(self.percent * progress)}%")):
            painter.setFont(font)
            painter.setPen(color)
            painter.drawText(rect, int(align), text)
        if self.track is not None:
            self.track.draw(painter, self.bar_rect)
        if self.chunk is not None:
            rect = QRectF(self.chunk_rect)
            width = rect.width() * self.fraction * progress
            if self.reverse:
                rect.setLeft(rect.right() - width)
            else:
                rect.setWidth(width)
            if width > 0:
                self.chunk.draw(painter, rect)


class StartupTransition(QWidget):
    finished = Signal()
    _FADE = .260

    def __init__(self, root, splash, page=None, project_list=None, excluded_widgets=(), drive_entries=()):
        super().__init__(root)
        from main_ext.class_ThemeUi import theme
        self._root = root
        self._background = QColor(theme.BG)
        self._stopped = False
        self._started_at = None
        self._bands = []
        self._band_region = QRegion()
        self._model = None
        self._drives = []
        self.setGeometry(root.rect())
        self.setObjectName("startupTransition")
        self.setStyleSheet(f"QWidget#startupTransition {{ background:{theme.BG}; border:none; }}")
        self.setAttribute(Qt.WA_OpaquePaintEvent)
        self.hide()
        self._splash_frame = splash.grab().toImage()
        splash.stop()
        splash.hide()
        if page is not None:
            model = self._model = StartupContentReveal(page, project_list)
            offset = page.mapTo(root, QPoint())
            self._bands = [(top + offset.y(), bottom + offset.y()) for top, bottom in model._bands()]
            self._band_region = QRegion(page.rect().translated(offset))
            for widget in excluded_widgets:
                self._band_region -= QRegion(widget.rect().translated(widget.mapTo(root, QPoint())))
        entries = [entry for entry in drive_entries if not entry.get("trash") and entry["widget"].isVisible()]
        suppressed = [entry[key] for entry in entries for key in ("usage", "percent", "bar")]
        blocker = _SuppressPaint(suppressed, self)
        app = QApplication.instance()
        # Application filters run before widget-local filters. Suppress before
        # the global Skia manager paints a progress chunk into the snapshot.
        app.installEventFilter(blocker)
        try:
            # One capture while the splash is still opaque. Never grab/read
            # back the live page in an animation frame.
            self._content_frame = root.grab().toImage()
        finally:
            app.removeEventFilter(blocker)
        self._drives = [_DriveLayer(root, entry) for entry in entries]
        self._end_at = self._FADE
        if self._model is not None:
            model = self._model
            self._end_at = max(self._end_at, model._FIRST_DELAY + max(0, len(self._bands)-1)*model._ROW_DELAY + model._DURATION)
        if self._drives:
            self._end_at = max(self._end_at, .1 + DriveUsageTiming.total(len(self._drives)) / 1000)
        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.PreciseTimer)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._tick)
        root.installEventFilter(self)

    def start(self):
        if self._stopped or self._started_at is not None:
            return
        self._root._cached_page_transition_active = True
        self.show()
        self.setAttribute(Qt.WA_OpaquePaintEvent)
        self.raise_()
        self._started_at = time.monotonic()
        self._timer.start()
        self.update()

    def _tick(self):
        self.update()
        if time.monotonic() - self._started_at >= self._end_at:
            self.stop()

    def paintEvent(self, event):
        if self._stopped:
            return
        elapsed = 0 if self._started_at is None else time.monotonic() - self._started_at
        painter = QPainter(self)
        painter.fillRect(event.rect(), self._background)
        painter.drawImage(QPoint(), self._content_frame)
        for index, drive in enumerate(self._drives):
            drive.draw(painter, DriveUsageTiming.progress((elapsed - .1) * 1000, index))
        if self._model is not None:
            painter.save()
            painter.setClipRegion(self._band_region)
            for (top, bottom), alpha in zip(self._bands, self._model._band_alphas(self._bands, elapsed)):
                if alpha:
                    color = QColor(self._background)
                    color.setAlpha(alpha)
                    painter.fillRect(0, top, self.width(), bottom-top, color)
            painter.restore()
        if elapsed < self._FADE:
            painter.setOpacity((1 - max(0, elapsed) / self._FADE) ** 3)
            painter.drawImage(QPoint(), self._splash_frame)
        painter.end()

    def eventFilter(self, obj, event):
        if obj is self._root and event.type() in (QEvent.Resize, QEvent.Hide, QEvent.DevicePixelRatioChange):
            self.stop()
        return False

    def stop(self):
        if self._stopped:
            return
        self._stopped = True
        self._timer.stop()
        if isValid(self._root):
            self._root.removeEventFilter(self)
            self._root._cached_page_transition_active = False
        if self._model is not None and isValid(self._model):
            self._model.stop()
        self.hide()
        self.finished.emit()
        self.deleteLater()
