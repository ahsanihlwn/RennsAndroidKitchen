# SPDX-License-Identifier: MPL-2.0
"""Fade a prepared native window without repainting its QWidget contents."""
import time

from PySide6.QtCore import QObject, QEvent, QTimer, QPropertyAnimation, QEasingCurve, Signal
from PySide6.QtWidgets import QApplication, QWidget


class WindowContentReveal(QObject):
    prepared = Signal()
    finished = Signal()
    DURATION_MS = 620

    def __init__(self, page):
        super().__init__(page)
        self._page = page
        self._stopped = False
        self._prepared = False
        self._started_at = None
        self._prepare_started = None
        self._last_damage = None
        self._painted = False
        self._settle_timer = QTimer(self)
        self._settle_timer.setInterval(16)
        self._settle_timer.timeout.connect(self._settle)
        self._animation = QPropertyAnimation(page, b"windowOpacity", self)
        self._animation.setDuration(self.DURATION_MS)
        self._animation.setStartValue(0.0)
        self._animation.setEndValue(1.0)
        self._animation.setEasingCurve(QEasingCurve.OutCubic)
        self._animation.finished.connect(self._finish)

    def prepare(self):
        if self._stopped or self._prepare_started is not None:
            return
        self._prepare_started = self._last_damage = time.monotonic()
        QApplication.instance().installEventFilter(self)
        self._page.ensurePolished()
        if self._page.layout() is not None:
            self._page.layout().activate()
        # Map and paint while the loading window is still present. Skia widget
        # caches, Qt glyphs and deferred Show/style work settle before the clock.
        self._page.setWindowOpacity(0.0)
        self._page.show()
        self._page.update()
        self._settle_timer.start()

    def eventFilter(self, obj, event):
        kind = event.type()
        if kind in (QEvent.Paint, QEvent.LayoutRequest, QEvent.StyleChange):
            if isinstance(obj, QWidget) and (obj is self._page or self._page.isAncestorOf(obj)):
                self._last_damage = time.monotonic()
                if kind == QEvent.Paint:
                    self._painted = True
        return False

    def _settle(self):
        if self._stopped or self._prepared:
            return
        now = time.monotonic()
        if (self._painted and now - self._last_damage >= .032) or now - self._prepare_started >= 2.0:
            self._settle_timer.stop()
            QApplication.instance().removeEventFilter(self)
            self._prepared = True
            self.prepared.emit()

    def start(self):
        if self._stopped or not self._prepared or self._started_at is not None:
            return
        self._started_at = time.perf_counter()
        from main_ext.class_ThemeUi import theme
        if not theme.animations_enabled:
            self._finish()
            return
        # Windows composites the backing store; no grabs, Skia draw, readPixels,
        # opacity effect, or transparent child overlay is needed on each frame.
        self._animation.start()

    def _finish(self):
        if self._stopped:
            return
        self.stop()
        self.finished.emit()

    def stop(self):
        if self._stopped:
            return
        self._stopped = True
        self._settle_timer.stop()
        self._animation.stop()
        QApplication.instance().removeEventFilter(self)
        if self._started_at is None:
            self._page.hide()
        self._page.setWindowOpacity(1.0)
        self.deleteLater()
