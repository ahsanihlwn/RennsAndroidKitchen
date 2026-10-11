# SPDX-License-Identifier: MPL-2.0
import time

from PySide6.QtCore import (
    QObject, QEvent, QPoint, QTimer, Qt, Property, QPropertyAnimation,
    QEasingCurve,
)
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import (
    QAbstractButton, QGraphicsEffect, QWidget, QPushButton, QStyleOptionButton, QStyle,
)

_CONTROL_EVENTS = frozenset((
    QEvent.Polish, QEvent.Show, QEvent.Hide, QEvent.EnabledChange,
    QEvent.MouseButtonPress, QEvent.MouseButtonRelease, QEvent.KeyPress,
))


class PressScaleEffect(QGraphicsEffect):


    def __init__(self, parent):
        super().__init__(parent)
        self._scale = 1.0

    def _get_scale(self):
        return self._scale

    def _set_scale(self, value):
        self._scale = float(value)
        self.update()

    scale = Property(float, _get_scale, _set_scale)

    def draw(self, painter):
        if self._scale >= 0.9999:
            self.drawSource(painter)
            return
        bounds = self.sourceBoundingRect(Qt.LogicalCoordinates)
        offset = QPoint()
        source = self.sourcePixmap(Qt.LogicalCoordinates, offset, QGraphicsEffect.NoPad)
        if source.isNull():
            return
        center = bounds.center()
        painter.save()
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        painter.translate(center)
        painter.scale(self._scale, self._scale)
        painter.translate(-center)
        painter.drawPixmap(offset, source)
        painter.restore()


class PressAnimatedButton(QPushButton):


    _paints_button_press_feedback = True

    def paintEvent(self, event):
        scale = button_press_scale(self)
        if scale >= 0.9999:
            super().paintEvent(event)
            return
        option = QStyleOptionButton()
        self.initStyleOption(option)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        center = self.rect().toRectF().center()
        painter.translate(center)
        painter.scale(scale, scale)
        painter.translate(-center)
        self.style().drawControl(QStyle.CE_PushButton, option, painter, self)
        painter.end()


class _ButtonPressController(QObject):
    def __init__(self, widget, manager):
        super().__init__(widget)
        self.widget = widget
        self._manager = manager
        self._effect = None
        self._animation = None
        self._painted = getattr(widget, "_paints_button_press_feedback", False)
        self._pressed = False
        self._press_started = 0.0
        self._release_timer = QTimer(self)
        self._release_timer.setSingleShot(True)
        self._release_timer.timeout.connect(lambda: self._animate(1.0))
        widget.destroyed.connect(lambda: manager._active.discard(self))
        if isinstance(widget, QAbstractButton):
            widget.pressed.connect(self.press)
            widget.released.connect(self.release)

    def owns_effect(self):
        return self._effect is not None and (
            self.widget.graphicsEffect() is None if self._painted
            else self.widget.graphicsEffect() is self._effect
        )

    def _discard_animation(self):
        if self._animation is not None:
            self._animation.stop()
            self._animation.deleteLater()
            self._animation = None

    def _ensure_effect(self):
        if self.owns_effect():
            return True
        self.reset()
        # A transition owns its opacity effect until its own handoff finishes.
        if self.widget.graphicsEffect() is not None:
            return False
        self._effect = PressScaleEffect(self.widget)
        if not self._painted:
            self.widget.setGraphicsEffect(self._effect)
        self._animation = QPropertyAnimation(self._effect, b"scale", self)
        self._animation.setEasingCurve(QEasingCurve.OutCubic)
        self._animation.finished.connect(self._animation_finished)
        if self._painted:
            self._animation.valueChanged.connect(self.widget.update)
        self._manager._active.add(self)
        return True

    def press(self):
        from main_ext.class_ThemeUi import theme
        self._release_timer.stop()
        if not theme.animations_enabled or not self.widget.isEnabled() or not self.widget.isVisible():
            self.reset()
            return
        if not self._ensure_effect():
            return
        self._pressed = True
        self._press_started = time.monotonic()
        self._animate(0.96)

    def release(self):
        if not self._pressed:
            return
        self._pressed = False
        from main_ext.class_ThemeUi import theme
        if not theme.animations_enabled:
            self.reset()
            return
        remaining_ms = max(0, 120 - int((time.monotonic() - self._press_started) * 1000))
        if remaining_ms:
            self._release_timer.start(remaining_ms)
        else:
            self._animate(1.0)

    def _animate(self, target):
        from main_ext.class_ThemeUi import theme
        if not theme.animations_enabled or not self.owns_effect():
            self.reset()
            return
        self._animation.stop()
        self._animation.setStartValue(self._effect.scale)
        self._animation.setEndValue(target)
        self._animation.setDuration(110 if target < 1.0 else 170)
        self._animation.start()

    def _animation_finished(self):
        if not self.owns_effect() or (not self._pressed and self._effect.scale >= 0.9999):
            self.reset()

    def reset(self):
        self._pressed = False
        self._release_timer.stop()
        self._discard_animation()
        if self._painted and self._effect is not None:
            self._effect.deleteLater()
            self.widget.update()
        elif self.owns_effect():
            self.widget.setGraphicsEffect(None)
        self._effect = None
        self._manager._active.discard(self)


class _ButtonPressManager(QObject):
    def __init__(self, app):
        super().__init__(app)
        self._active = set()

    @staticmethod
    def _is_control(widget):
        return isinstance(widget, QAbstractButton) or (
            isinstance(widget, QWidget) and widget.property("buttonPressFeedback") is True
        )

    def _register(self, widget):
        controller = getattr(widget, "_button_press_feedback", None)
        if controller is None:
            controller = _ButtonPressController(widget, self)
            widget._button_press_feedback = controller
        return controller

    def eventFilter(self, widget, event):
        kind = event.type()
        if kind == QEvent.WindowDeactivate and isinstance(widget, QWidget) and widget.isWindow():
            for controller in tuple(self._active):
                if controller.widget.window() is widget:
                    controller.reset()
            return False
        if kind not in _CONTROL_EVENTS or not self._is_control(widget):
            return False
        controller = self._register(widget)
        if kind == QEvent.Hide or (kind == QEvent.EnabledChange and not widget.isEnabled()):
            controller.reset()
        elif not isinstance(widget, QAbstractButton):
            if kind == QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
                controller.press()
            elif kind == QEvent.MouseButtonRelease and event.button() == Qt.LeftButton:
                controller.release()
        return False


def install_button_press_animations(app):

    if app is None or getattr(app, "_button_press_manager", None) is not None:
        return
    # Hide Qt's button focus outline while retaining Tab/Space activation.
    app.setStyleSheet(app.styleSheet() + "\nQPushButton { outline: none; }")
    manager = _ButtonPressManager(app)
    app._button_press_manager = manager
    app.installEventFilter(manager)
    for widget in app.allWidgets():
        if manager._is_control(widget):
            manager._register(widget)


def has_button_press_feedback(widget):
    controller = getattr(widget, "_button_press_feedback", None)
    return controller is not None and controller.owns_effect()


def button_press_scale(widget):
    controller = getattr(widget, "_button_press_feedback", None)
    return controller._effect.scale if controller is not None and controller.owns_effect() else 1.0


def reset_button_press_feedback(widget):
    controller = getattr(widget, "_button_press_feedback", None)
    if controller is not None:
        controller.reset()
