# SPDX-License-Identifier: MPL-2.0
import math

from PySide6.QtCore import Qt, QEvent, QPointF, QRect, QRectF, Signal, QVariantAnimation, QEasingCurve
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QWidget, QGraphicsOpacityEffect


class ProjectCardsTransition(QWidget):
    finished = Signal()
    EXIT_DURATION = 240
    REMOVE_MOVE_START = 0.70
    REMOVE_MOVE_STAGGER = 0.05
    MOVE_DURATION = 310
    ENTER_DURATION = 180
    ENTER_MOVE_PROGRESS = 0.50

    def __init__(self, project_list, changes, suppress_role, snapshot_row):
        viewport = project_list.viewport()
        super().__init__(viewport)
        self.setObjectName("projectCardsTransition")
        self.setStyleSheet("QWidget#projectCardsTransition { background: transparent; border: none; }")
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_NoSystemBackground)
        self.setGeometry(viewport.rect())
        self._project_list = project_list
        self._suppress_role = suppress_role
        self._stopped = False
        self._elapsed = 0.0
        self._scene = []
        self._suppressed = []
        self._exit_ease = QEasingCurve(QEasingCurve.InOutSine)
        # Reveal quickly, then let opacity and scale settle with a long ease-out.
        self._enter_ease = QEasingCurve(QEasingCurve.OutQuint)
        self._move_ease = QEasingCurve(QEasingCurve.OutCubic)
        self._hover_effect = viewport.graphicsEffect()
        self._hover_enabled = self._hover_effect is not None and self._hover_effect.isEnabled()
        if self._hover_enabled:
            self._hover_effect.setEnabled(False)
        self._mouse_transparent = project_list.testAttribute(Qt.WA_TransparentForMouseEvents)

        # A scrollbar clamp can bring previously offscreen rows into view.
        # Keep every old position, but only grab images needed on screen.
        source = {}
        images = {}
        for index in range(project_list.count()):
            item = project_list.item(index)
            if item.isHidden():
                continue
            rect = QRect(project_list.visualItemRect(item))
            source[id(item)] = (item, rect)
            if rect.intersects(viewport.rect()):
                _, images[id(item)] = snapshot_row(project_list, item)

        for item, hidden in changes:
            item.setHidden(hidden)
        project_list.doItemsLayout()

        target = {}
        for index in range(project_list.count()):
            item = project_list.item(index)
            if not item.isHidden():
                target[id(item)] = (item, QRect(project_list.visualItemRect(item)))

        for key in dict.fromkeys((*source, *target)):
            old, new = source.get(key), target.get(key)
            item, rect = old or new
            destination = new[1] if new is not None else rect
            if not rect.intersects(viewport.rect()) and not destination.intersects(viewport.rect()):
                continue
            pixmap = images.get(key)
            if pixmap is None:
                _, pixmap = snapshot_row(project_list, item)
            kind = "remove" if new is None else "add" if old is None else "keep"
            self._scene.append((rect, destination, pixmap, kind))
            if new is not None:
                self._suppressed.append((item, project_list.itemWidget(item)))

        # Deletion and drive hiding share the same exit and downward cascade.
        # Insertion moves rows together and reveals cards halfway through.
        removed = any(hidden for _, hidden in changes)
        added = any(not hidden for _, hidden in changes)
        removal_only = removed and not added
        self._move_delay = (round(self.EXIT_DURATION * self.REMOVE_MOVE_START) if removal_only
                            else self.EXIT_DURATION if removed else 0)
        # Fading rows stay above the incoming rows throughout their exit.
        self._scene.sort(key=lambda row: {"keep": 0, "remove": 1, "add": 2}[row[3]])
        self._move_delays = []
        move_count = 0
        movement_end = 0
        for source, target, _, kind in self._scene:
            delay = self._move_delay
            if kind == "keep" and source != target:
                if removal_only:
                    delay = round(delay + move_count * self.MOVE_DURATION * self.REMOVE_MOVE_STAGGER)
                move_count += 1
                movement_end = max(movement_end, delay + self.MOVE_DURATION)
            self._move_delays.append(delay)
        self._enter_delay = self._move_delay + (
            math.ceil(self.MOVE_DURATION * self.ENTER_MOVE_PROGRESS) if move_count else 0
        )
        duration = max(
            self.EXIT_DURATION if removed else 0,
            movement_end,
            self._enter_delay + self.ENTER_DURATION if added else 0,
        )

        for item, widget in self._suppressed:
            item.setData(suppress_role, True)
            if widget is not None:
                opacity = QGraphicsOpacityEffect(widget)
                opacity.setOpacity(0.0)
                widget.setGraphicsEffect(opacity)
        project_list.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self._clock = QVariantAnimation(self)
        self._clock.setStartValue(0.0)
        self._clock.setEndValue(float(duration))
        self._clock.setDuration(duration)
        self._clock.valueChanged.connect(self._advance)
        self._clock.finished.connect(self.stop)
        viewport.installEventFilter(self)
        project_list.verticalScrollBar().valueChanged.connect(self.stop)
        project_list.model().modelAboutToBeReset.connect(self.stop)
        project_list.model().rowsAboutToBeRemoved.connect(self.stop)

    def eventFilter(self, watched, event):
        if event.type() in (QEvent.Resize, QEvent.Hide, QEvent.Wheel):
            self.stop()
        return super().eventFilter(watched, event)

    def _advance(self, elapsed):
        self._elapsed = float(elapsed)
        self.update()

    def start(self):
        self.show()
        self.raise_()
        self._clock.start()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        # Returning cards sit above the moving cards only once space is open.
        for (source, target, pixmap, kind), delay in zip(self._scene, self._move_delays):
            if kind == "keep":
                move = self._move_ease.valueForProgress(max(0.0, min(
                    1.0, (self._elapsed - delay) / self.MOVE_DURATION,
                )))
                painter.setOpacity(1.0)
                point = QPointF(source.topLeft()) + QPointF(target.topLeft() - source.topLeft()) * move
                painter.drawPixmap(point, pixmap)
                continue
            if kind == "add":
                phase = max(0.0, min(1.0, (self._elapsed - self._enter_delay) / self.ENTER_DURATION))
                phase = self._enter_ease.valueForProgress(phase)
                painter.setOpacity(phase)
                rect = target
                inset = 1.0 - phase
            else:
                phase = self._exit_ease.valueForProgress(min(1.0, self._elapsed / self.EXIT_DURATION))
                painter.setOpacity(1.0 - phase)
                rect = source
                inset = phase
            dx = max(2, round(rect.width() * 0.02)) * inset
            dy = max(1, round(rect.height() * 0.02)) * inset
            painter.drawPixmap(
                QRectF(rect).adjusted(dx, dy, -dx, -dy), pixmap,
                QRectF(pixmap.rect()),
            )
        painter.end()

    def stop(self, *_):
        if self._stopped:
            return
        self._stopped = True
        self._clock.stop()
        project_list = self._project_list
        viewport = project_list.viewport()
        viewport.removeEventFilter(self)
        project_list.verticalScrollBar().valueChanged.disconnect(self.stop)
        project_list.model().modelAboutToBeReset.disconnect(self.stop)
        project_list.model().rowsAboutToBeRemoved.disconnect(self.stop)
        for item, widget in self._suppressed:
            item.setData(self._suppress_role, None)
            if widget is not None:
                widget.setGraphicsEffect(None)
        if self._hover_enabled:
            self._hover_effect.setEnabled(True)
        project_list.setAttribute(Qt.WA_TransparentForMouseEvents, self._mouse_transparent)
        project_list._target_scroll = project_list.verticalScrollBar().value()
        self.hide()
        self.finished.emit()
        self.deleteLater()
