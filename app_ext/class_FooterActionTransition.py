# SPDX-License-Identifier: MPL-2.0
from itertools import zip_longest
from math import ceil

from PySide6.QtCore import (
    Qt, QPoint, QRect, QRectF, Signal, QParallelAnimationGroup, QPropertyAnimation, QEasingCurve,
)
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import (
    QWidget, QPushButton, QGraphicsOpacityEffect, QStyle, QStyleOptionButton,
)
from core.skia_gpu import render_gpu
from core.skia_button import button_surface, button_label, _source_sheet


class FooterActionTransition(QWidget):
    finished = Signal()
    _DURATION = 280

    def __init__(self, stack, source_buttons, target_buttons):
        super().__init__(stack)
        from main_ext.class_ThemeUi import theme

        self.setObjectName("footerActionTransition")
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WA_StyledBackground)
        # The replicas live on the stack, outside either page's stylesheet.
        # Give them the same base rules as the buttons they represent.
        self.setStyleSheet(
            theme.app_stylesheet() +
            f"\nQWidget#footerActionTransition {{ background: {theme.BG}; border: none; }}"
        )

        def actions(buttons):
            result = [
                (button, QRect(button.mapTo(stack, QPoint()), button.size()))
                for button in buttons if not button.isHidden()
            ]
            # Keep actions in their visual slots. The rightmost action morphs
            # into the rightmost action, without crossing neighbouring buttons.
            return sorted(result, key=lambda action: action[1].right(), reverse=True)

        source = actions(source_buttons)
        target = actions(target_buttons)
        bounds = QRect()
        for _, rect in (*source, *target):
            bounds = bounds.united(rect)
        self.setGeometry(bounds)
        origin = bounds.topLeft()
        source = [(button, rect.translated(-origin)) for button, rect in source]
        target = [(button, rect.translated(-origin)) for button, rect in target]

        self._group = QParallelAnimationGroup(self)
        self._group.finished.connect(self._finish)
        self._label_views = []
        self._shape_views = []
        self._views = []
        self._view_images = {}
        self._background = QColor(theme.BG)
        self._skia_gpu_active = False

        for old, new in zip_longest(source, target):
            if old is None or new is None:
                button, rect = old if old is not None else new
                view = self._button_view(button, rect, "full")
                self._fade(view, 1.0 if old is not None else 0.0,
                           0.0 if old is not None else 1.0)
                continue

            old_button, old_rect = old
            new_button, new_rect = new
            old_shape = self._button_view(old_button, old_rect, "shape")
            new_shape = self._button_view(new_button, old_rect, "shape")
            self._shape_views.extend((old_shape, new_shape))
            for view in (old_shape, new_shape):
                self._animate(view, b"geometry", old_rect, new_rect)
            self._fade(old_shape, 1.0, 0.0)
            self._fade(new_shape, 0.0, 1.0)

            # Native QPushButtons render icon/text at their final, fixed sizes.
            # Only the blank surfaces above animate their geometry.
            old_label = self._button_view(old_button, old_rect, "label")
            new_label = self._button_view(new_button, new_rect, "label")
            self._label_views.extend((old_label, new_label))
            self._fade(old_label, 1.0, 0.0, label=True)
            self._fade(new_label, 0.0, 1.0, label=True)

    def _button_view(self, button, rect, kind):
        view = QPushButton(self)
        view._skia_layer_kind = kind
        view.setProperty("skipButtonGpuRendering", True)
        view.setObjectName("footerActionLabel" if kind == "label" else "footerActionView")
        view.setAttribute(Qt.WA_TransparentForMouseEvents)
        view.setAttribute(Qt.WA_TranslucentBackground)
        view.setFocusPolicy(Qt.NoFocus)
        view.setAutoDefault(False)
        view.setFont(button.font())
        view.setPalette(button.palette())
        view.setLayoutDirection(button.layoutDirection())
        view.setEnabled(button.isEnabled())
        view.setProperty("role", button.property("role"))
        view.setIconSize(button.iconSize())
        if kind != "shape":
            view.setText(button.text())
            view.setIcon(button.icon())

        stylesheet = _source_sheet(button)
        if kind == "label":
            if stylesheet.strip() and "{" not in stylesheet:
                stylesheet = "QPushButton {" + stylesheet + "}"
            stylesheet += """
                QPushButton#footerActionLabel,
                QPushButton#footerActionLabel:hover,
                QPushButton#footerActionLabel:pressed,
                QPushButton#footerActionLabel:disabled {
                    background: transparent;
                    border-color: transparent;
                }
            """
        view.setStyleSheet(stylesheet)
        view.setGeometry(rect)
        # Keep native buttons as style/animation models. Their Qt-rendered
        # textures are composed by one GPU canvas instead of child effects.
        view.hide()
        self._views.append(view)
        return view

    def _animate(self, target, prop, start, end, keyframes=()):
        animation = QPropertyAnimation(target, prop, self._group)
        animation.setDuration(self._DURATION)
        animation.setEasingCurve(QEasingCurve.InOutCubic)
        animation.setStartValue(start)
        animation.setEndValue(end)
        for at, value in keyframes:
            animation.setKeyValueAt(at, value)
        animation.valueChanged.connect(self.update)
        self._group.addAnimation(animation)

    def _fade(self, view, start, end, label=False):
        effect = QGraphicsOpacityEffect(view)
        effect.setOpacity(start)
        view.setGraphicsEffect(effect)
        # Use a brief overlap for captions, so different words do not stay
        # superimposed for the whole geometry transition. Neither caption moves.
        keyframes = ()
        if label:
            keyframes = ((0.45, 0.0),) if start else ((0.25, 0.0), (0.65, 1.0))
        self._animate(effect, b"opacity", start, end, keyframes)

    def _view_image(self, view):
        dpr = self.devicePixelRatioF()
        key = (view.width(), view.height(), dpr)
        cached = self._view_images.get(view)
        if cached is not None and cached[0] == key:
            return cached[1]
        image = QImage(max(1, ceil(view.width() * dpr)),
                       max(1, ceil(view.height() * dpr)),
                       QImage.Format_ARGB32_Premultiplied)
        image.setDevicePixelRatio(dpr)
        image.fill(Qt.transparent)
        option = QStyleOptionButton()
        view.ensurePolished()
        view.initStyleOption(option)
        option.state &= ~(QStyle.State_MouseOver | QStyle.State_HasFocus | QStyle.State_Sunken)
        painter = QPainter(image)
        painter.setFont(view.font())
        painter.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing |
                               QPainter.SmoothPixmapTransform)
        view.style().drawControl(QStyle.CE_PushButton, option, painter, view)
        painter.end()
        # Labels keep their final geometry, so glyphs and icons are captured
        # once per DPR. Only blank shapes need a new texture after resizing.
        self._view_images[view] = (key, image)
        return image

    def _layers(self):
        for view in self._views:
            opacity = view.graphicsEffect().opacity()
            if opacity > 0.0:
                yield self._view_image(view), QRectF(view.geometry()), opacity

    def _paint_gpu(self, canvas, bridge):
        canvas.clear(bridge.color(self._background))
        for view in self._views:
            opacity = view.graphicsEffect().opacity()
            if opacity <= 0.0:
                continue
            option = QStyleOptionButton()
            view.ensurePolished()
            view.initStyleOption(option)
            option.state &= ~(QStyle.State_MouseOver | QStyle.State_HasFocus | QStyle.State_Sunken)
            rect = QRectF(view.geometry())
            surface = button_surface(view, option)
            if surface is None:
                bridge.draw_image(canvas, self._view_image(view), rect, opacity)
                continue
            surface.draw(canvas, bridge, rect, opacity)
            if view._skia_layer_kind != "shape":
                bridge.draw_image(canvas, button_label(view, option, self.devicePixelRatioF()),
                                  rect, opacity)

    def paintEvent(self, event):
        image = render_gpu(self.size(), self.devicePixelRatioF(), self._paint_gpu)
        self._skia_gpu_active = image is not None
        painter = QPainter(self)
        if image is not None:
            painter.drawImage(QPoint(), image)
            return
        painter.fillRect(self.rect(), self._background)
        for layer, rect, opacity in self._layers():
            painter.setOpacity(opacity)
            painter.drawImage(rect, layer)

    def start(self):
        self.show()
        self.raise_()
        self._group.start()

    def stop(self):
        self._group.stop()
        self.hide()
        self.deleteLater()

    def _finish(self):
        self.hide()
        self.finished.emit()
        self.deleteLater()
