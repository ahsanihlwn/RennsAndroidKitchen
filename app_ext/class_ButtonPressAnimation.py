# SPDX-License-Identifier: MPL-2.0
import time
import math

from PySide6.QtCore import (
    QObject, QEvent, QPoint, QRectF, QTimer, Qt, Property, QPropertyAnimation,
    QEasingCurve,
)
from PySide6.QtGui import QImage, QPainter
from PySide6.QtWidgets import (
    QAbstractButton, QGraphicsEffect, QWidget, QPushButton, QStyleOptionButton, QStyle,
)
from core.skia_gpu import gpu_available, render_gpu
from core.skia_button import render_button

_CONTROL_EVENTS = frozenset((
    QEvent.Polish, QEvent.Show, QEvent.Hide, QEvent.EnabledChange,
    QEvent.MouseButtonPress, QEvent.MouseButtonRelease, QEvent.KeyPress, QEvent.Paint,
))


class PressScaleEffect(QGraphicsEffect):


    def __init__(self, parent):
        super().__init__(parent)
        self._scale = 1.0
        self._gpu_frame_key = None
        self._gpu_frame = None
        self._gpu_source = None

    def _get_scale(self):
        return self._scale

    def _set_scale(self, value):
        self._scale = float(value)
        self.update()

    scale = Property(float, _get_scale, _set_scale)

    def draw(self, painter):
        widget = self.parent()
        if isinstance(widget, QPushButton) and not widget.property("skipButtonGpuRendering"):
            image = render_button(widget, self._scale, include_children=True)
            if image is not None:
                painter.drawImage(0, 0, image)
                return
        if self._scale == 1.0:
            # Widget surfaces already use Skia; an idle card needs no source
            # capture, texture upload or readback for the identity transform.
            self.drawSource(painter)
            return
        bounds = self.sourceBoundingRect(Qt.LogicalCoordinates)
        offset = QPoint()
        source = self.sourcePixmap(Qt.LogicalCoordinates, offset, QGraphicsEffect.NoPad)
        if source.isNull():
            return
        # Qt can return a fresh pixmap cache key for an unchanged effect source
        # on every animation tick. Reuse its texture when the Qt pixels match;
        # checked/hover/font/child-content updates still invalidate naturally.
        if (self._gpu_source is not None
                and self._gpu_source.devicePixelRatioF() == source.devicePixelRatioF()
                and self._gpu_source.toImage() == source.toImage()):
            source = self._gpu_source
        else:
            self._gpu_source = source
        center = bounds.center()
        ratio = source.devicePixelRatioF()
        size = source.deviceIndependentSize()
        key = (source.cacheKey(), ratio, self._scale, center.x(), center.y(), offset.x(), offset.y())
        if key != self._gpu_frame_key:
            def draw(canvas, bridge):
                canvas.translate(center.x() - offset.x(), center.y() - offset.y())
                canvas.scale(self._scale, self._scale)
                canvas.translate(offset.x() - center.x(), offset.y() - center.y())
                bridge.draw_image(canvas, source, QRectF(0, 0, size.width(), size.height()))

            self._gpu_frame = render_gpu(size, ratio, draw)
            self._gpu_frame_key = key if self._gpu_frame is not None else None
        if self._gpu_frame is not None:
            painter.drawImage(offset, self._gpu_frame)
            return
        if abs(self._scale - 1.0) < 0.0001:
            self.drawSource(painter)
            return
        painter.save()
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        painter.translate(center)
        painter.scale(self._scale, self._scale)
        painter.translate(-center)
        painter.drawPixmap(offset, source)
        painter.restore()


class PressAnimatedButton(QPushButton):


    _paints_button_press_feedback = True

    def event(self, event):
        if event.type() in (QEvent.StyleChange, QEvent.FontChange, QEvent.PaletteChange,
                            QEvent.LayoutDirectionChange, QEvent.DynamicPropertyChange):
            self._qt_button_texture_key = None
        return super().event(event)

    def paintEvent(self, event):
        scale = button_press_scale(self)
        image = render_button(self, scale)
        if image is not None:
            painter = QPainter(self)
            painter.drawImage(0, 0, image)
            painter.end()
            return
        option = QStyleOptionButton()
        self.initStyleOption(option)
        ratio = self.devicePixelRatioF()
        # QStyle/QPainter own font shaping and glyph rasterization. Skia only
        # uploads and transforms these Qt pixels, including at fractional DPI.
        texture_key = (self.width(), self.height(), ratio, option.state.value,
                       option.features.value, option.text, option.icon.cacheKey(),
                       option.iconSize.width(), option.iconSize.height(),
                       option.palette.cacheKey(), self.font().toString())
        if texture_key != getattr(self, "_qt_button_texture_key", None):
            source = QImage(math.ceil(self.width() * ratio), math.ceil(self.height() * ratio),
                            QImage.Format_RGBA8888_Premultiplied)
            source.setDevicePixelRatio(ratio)
            source.fill(Qt.transparent)
            capture = QPainter(source)
            capture.setFont(self.font())
            self.style().drawControl(QStyle.CE_PushButton, option, capture, self)
            capture.end()
            self._qt_button_texture = source
            self._qt_button_texture_key = texture_key
        source = self._qt_button_texture
        center = self.rect().toRectF().center()

        def draw(canvas, bridge):
            canvas.translate(center.x(), center.y())
            canvas.scale(scale, scale)
            canvas.translate(-center.x(), -center.y())
            bridge.draw_image(canvas, source, QRectF(self.rect()))

        image = render_gpu(self.size(), ratio, draw)
        if image is not None:
            painter = QPainter(self)
            painter.drawImage(0, 0, image)
            painter.end()
            return
        if scale >= 0.9999:
            super().paintEvent(event)
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
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
        self._manager._active.add(self)
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
        if not self.owns_effect():
            self.reset()
        elif not self._pressed and self._effect.scale >= 0.9999:
            # Native paint filtering handles idle vectors without forcing
            # every button and its descendants into an offscreen Qt effect.
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
        # A foreign opacity effect can own a button during popup handoff. Its
        # native source paint still gets vector geometry, before the effect
        # captures the Qt-rendered widget. Active press effects draw directly.
        if (kind == QEvent.Paint and isinstance(widget, QPushButton)
                and not controller._painted
                and not widget.property("skipButtonGpuRendering")
                and not isinstance(widget.graphicsEffect(), PressScaleEffect)):
            image = render_button(widget)
            if image is not None:
                painter = QPainter(widget)
                painter.drawImage(0, 0, image)
                painter.end()
                return True
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
