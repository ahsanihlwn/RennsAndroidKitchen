# SPDX-License-Identifier: MPL-2.0
"""Animate cached project rows without a graphics effect on the viewport."""
import time
from PySide6.QtCore import Qt, QTimer, QRect
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QWidget, QLabel
from shiboken6 import isValid


class ProjectHoverCanvas(QWidget):
    def __init__(self, project_list, background):
        super().__init__(project_list.viewport())
        self.project_list = project_list
        self.background = QColor(background)
        self.item = self.pressed_item = None
        self._states = {}
        self._scrolling = False
        self._allowed = True
        self._paint_key = None
        self.setObjectName("projectHoverCanvas")
        self.setStyleSheet("QWidget#projectHoverCanvas {background:transparent;border:none;}")
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WA_OpaquePaintEvent)
        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._advance)
        self.hide()

    def setEnabled(self, enabled):
        self._allowed = enabled
        if not enabled:
            self._timer.stop()
            self.hide()
        elif self._states and not self._scrolling:
            self._advance()

    def set_scrolling(self, scrolling):
        self._scrolling = scrolling
        if scrolling:
            self._timer.stop()
            self._states.clear()
            self.hide()

    def _retarget(self, item, target, duration):
        if item is None:
            return
        old = self._states.get(id(item), {})
        self._states[id(item)] = {"item": item, "start": old.get("scale", 1.0),
                                 "scale": old.get("scale", 1.0), "target": target,
                                 "started": time.monotonic(), "duration": duration / 1000,
                                 "image": old.get("image"), "signature": old.get("signature")}
        self._timer.setInterval(16)
        self._timer.start()
        self._advance()

    def set_hovered_item(self, item):
        # Hover keeps no canvas, snapshots or animation timer alive.
        self.item = item

    def set_pressed_item(self, item, pressed):
        if self._scrolling or not self._allowed:
            return
        if pressed:
            self.pressed_item = item
        elif item is not self.pressed_item:
            return
        else:
            self.pressed_item = None
        self._retarget(item, .97 if pressed else 1.0, 110 if pressed else 170)

    def _advance(self):
        if self._scrolling or not self._allowed:
            return
        now = time.monotonic()
        bounds = QRect()
        animating = False
        for key, state in tuple(self._states.items()):
            if not isValid(state["item"]):
                del self._states[key]
                continue
            progress = min(1.0, (now - state["started"]) / state["duration"])
            animating |= progress < 1
            state["scale"] = state["start"] + (state["target"] - state["start"]) * (1 - (1-progress)**3)
            if progress == 1 and state["target"] == 1:
                del self._states[key]
                continue
            item = state["item"]
            rect = self.project_list.visualItemRect(item).adjusted(0, 0, 0, -8)
            rect = rect.intersected(self.parentWidget().rect())
            if rect.isEmpty():
                continue
            widget = self.project_list.itemWidget(item)
            labels = widget.findChildren(QLabel) if widget is not None else ()
            signature = (rect.size(), self.background.rgba(), item.isSelected(),
                         tuple((label.text(), getattr(label, "_offset", None), label.font().key(), label.styleSheet()) for label in labels))
            if state["image"] is None or signature != state["signature"]:
                self.hide()
                state["image"] = self.parentWidget().grab(rect)
                state["signature"] = signature
            state["rect"] = rect
            bounds = bounds.united(rect)
        if bounds.isEmpty():
            self.hide()
            self._timer.stop()
            return
        self.setGeometry(bounds)
        self.show()
        self.raise_()
        self._timer.setInterval(16 if animating else 50)
        key = (bounds.getRect(), tuple((state["scale"], state["signature"]) for state in self._states.values()))
        if key != self._paint_key:
            self._paint_key = key
            self.update()

    def reset(self):
        self._timer.stop()
        self.item = self.pressed_item = None
        self._states.clear()
        self._paint_key = None
        self.hide()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(event.rect(), self.background)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        for state in self._states.values():
            if state.get("image") is None or "rect" not in state:
                continue
            rect = state["rect"].translated(-self.pos())
            center = rect.center()
            painter.save()
            painter.translate(center)
            painter.scale(state["scale"], state["scale"])
            painter.translate(-center)
            painter.drawPixmap(rect.topLeft(), state["image"])
            painter.restore()
