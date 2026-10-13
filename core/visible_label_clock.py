# SPDX-License-Identifier: MPL-2.0
"""One marquee clock; clipped project labels do not tick or measure glyphs."""
import weakref
from PySide6.QtCore import QObject, QTimer, QRect, QPoint
from PySide6.QtWidgets import QApplication, QAbstractScrollArea
from shiboken6 import isValid


class VisibleLabelClock(QObject):
    def __init__(self, app):
        super().__init__(app)
        self.labels = weakref.WeakSet()
        self.timer = QTimer(self)
        self.timer.setInterval(50)
        self.timer.timeout.connect(self._tick)

    @classmethod
    def instance(cls):
        app = QApplication.instance()
        clock = getattr(app, "_visible_label_clock", None)
        if clock is None:
            clock = app._visible_label_clock = cls(app)
        return clock

    def track(self, label):
        self.labels.add(label)
        parent = label.parentWidget()
        while parent is not None:
            if isinstance(parent, QAbstractScrollArea):
                label._marquee_viewport = parent.viewport()
                if not getattr(parent, "_marquee_clock_connected", False):
                    parent._marquee_clock_connected = True
                    parent.verticalScrollBar().valueChanged.connect(self.wake)
                break
            parent = parent.parentWidget()
        self.wake()

    def wake(self, *_):
        if not self.timer.isActive():
            self.timer.start()

    @staticmethod
    def visible(label):
        if not isValid(label) or not label.isVisible():
            return False
        if getattr(label.window(), "_cached_page_transition_active", False):
            return False
        viewport = getattr(label, "_marquee_viewport", None)
        if viewport is None:
            return True
        return (isValid(viewport) and viewport.isVisible()
                and viewport.rect().intersects(QRect(label.mapTo(viewport, QPoint()), label.size())))

    def _tick(self):
        active = False
        for label in tuple(self.labels):
            if not isValid(label):
                self.labels.discard(label)
                continue
            if self.visible(label) and label._max_offset() > 0:
                active = True
                label._advance_scroll()
        if not active:
            self.timer.stop()
