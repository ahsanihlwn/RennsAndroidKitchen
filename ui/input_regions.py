# SPDX-License-Identifier: MPL-2.0
"""Keep live backdrop pixels outside a content viewport out of pointer input."""
from PySide6.QtCore import QObject, Property, Signal, Slot, QPointF, QEvent
from PySide6.QtQuick import QQuickItem


class ContentInputRegion(QObject):
    itemChanged = Signal()
    viewportChanged = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._item = None
        self._viewport = None
        self._filter_installed = False

    def _sync_filter(self):
        want = self._item is not None and self._viewport is not None
        if want == self._filter_installed:
            return
        if self._item is not None:
            if want:
                self._item.installEventFilter(self)
            else:
                self._item.removeEventFilter(self)
        self._filter_installed = want and self._item is not None

    @Property(QQuickItem, notify=itemChanged)
    def item(self):
        return self._item

    @item.setter
    def item(self, value):
        if self._item is value:
            return
        if self._item is not None and self._filter_installed:
            self._item.removeEventFilter(self)
            self._filter_installed = False
        self._item = value
        self._sync_filter()
        self.itemChanged.emit()

    @Property(QQuickItem, notify=viewportChanged)
    def viewport(self):
        return self._viewport

    @viewport.setter
    def viewport(self, value):
        if self._viewport is not value:
            self._viewport = value
            self._sync_filter()
            self.viewportChanged.emit()

    @Slot(QPointF, result=bool)
    def withinViewport(self, point):
        if self._item is None:
            return False
        if self._viewport is None:
            return True
        mapped = self._item.mapToItem(self._viewport, point)
        bounds = self._viewport.property('inputBounds')
        return bounds.left() <= mapped.x() < bounds.right() and bounds.top() <= mapped.y() < bounds.bottom()

    @Slot(QPointF, result=bool)
    def contains(self, point):
        return (self._item is not None and 0 <= point.x() < self._item.width()
                and 0 <= point.y() < self._item.height() and self.withinViewport(point))

    def eventFilter(self, watched, event):
        if self._viewport is None or watched is not self._item:
            return False
        kind = event.type()
        if kind in (QEvent.MouseMove, QEvent.MouseButtonRelease):
            outside = not self.withinViewport(event.position())
        elif kind in (QEvent.TouchUpdate, QEvent.TouchEnd):
            outside = any(not self.withinViewport(point.position()) for point in event.points())
        else:
            return False
        if not outside:
            return False
        # Switch deliberately keeps its press beyond contains(). Cancel before
        # its native release handler can toggle, without changing enabled bindings.
        watched.ungrabMouse()
        watched.ungrabTouchPoints()
        watched.setKeepMouseGrab(False)
        watched.setKeepTouchGrab(False)
        event.accept()
        return True
