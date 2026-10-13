# SPDX-License-Identifier: MPL-2.0
from math import ceil, cos, exp, floor, sin, sqrt

from PySide6.QtCore import (
    Qt, QPoint, QRect, QRectF, Signal, QParallelAnimationGroup, QVariantAnimation,
    QEasingCurve,
)
from PySide6.QtGui import QImage, QPainter, QPainterPath
from PySide6.QtWidgets import (
    QWidget, QPushButton, QGraphicsOpacityEffect, QStyleOption,
    QStyleOptionButton, QStyle,
)
from core.skia_gpu import render_gpu, skia
from core.skia_button import button_surface


def _spring_progress(progress):

    if progress <= 0.0:
        return 0.0
    if progress >= 1.0:
        return 1.0
    damping, frequency = 0.78, 12.0
    oscillation = sqrt(1.0 - damping * damping)

    def response(at):
        phase = frequency * oscillation * at
        return 1.0 - exp(-damping * frequency * at) * (
            cos(phase) + damping / oscillation * sin(phase)
        )

    return response(progress) / response(1.0)


class _StyleButton(QPushButton):


    def draw(self, painter, rect, opacity):
        if opacity <= 0.0:
            return
        option = QStyleOptionButton()
        self.initStyleOption(option)
        option.rect = QRect(0, 0, max(1, round(rect.width())), max(1, round(rect.height())))
        option.state &= ~(QStyle.State_MouseOver | QStyle.State_HasFocus | QStyle.State_Sunken)
        painter.save()
        painter.setOpacity(opacity)
        painter.setFont(self.font())
        painter.translate(rect.topLeft())
        painter.scale(rect.width() / option.rect.width(), rect.height() / option.rect.height())
        self.style().drawControl(QStyle.CE_PushButton, option, painter, self)
        painter.restore()


class ButtonCardMorph(QWidget):
    finished = Signal()
    _DURATION = 440

    def __init__(self, host, card, button, button_rect, opening=True, source_effect=None,
                 launch_scale=1.0, defer_source_handoff=False, caption_draw=None,
                 caption_start=None, destination_frame=None):
        super().__init__(host)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, opening)
        self.setObjectName("buttonCardMorph")
        self.setStyleSheet("QWidget#buttonCardMorph { background: transparent; border: none; }")
        self.setGeometry(host.rect())
        self._host = host
        self._card = card
        self._opening = opening
        self._source_effect = source_effect
        self._defer_source_handoff = defer_source_handoff
        self._caption_draw = caption_draw
        self._caption_start = caption_start
        self._destination_frame = destination_frame
        self._destination_opacity = 0.0
        self._stopped = False
        self._card_rect = QRectF(card.geometry())
        self._button_rect = QRectF(button_rect)
        self._launch_scale = float(launch_scale)
        center = self._button_rect.center()
        self._launch_rect = QRectF(
            center.x() - self._button_rect.width() * self._launch_scale / 2,
            center.y() - self._button_rect.height() * self._launch_scale / 2,
            self._button_rect.width() * self._launch_scale,
            self._button_rect.height() * self._launch_scale,
        )
        self._caption_rect = QRectF(0, 0, button.width(), button.height())
        self._growth_width = max(1.0, self._card_rect.width() - self._button_rect.width())
        self._fade_easing = QEasingCurve(QEasingCurve.OutCubic)
        self._blur_surface = None
        self._blur_pixmap = None
        self._blur_origin = None
        self._caption_image = None
        self._caption_transform = None
        self._skia_gpu_active = False

        self._button_surface = self._button_view(button, caption=False)
        self._caption = self._button_view(button, caption=True)
        self._card_surface = QWidget(self)
        self._card_surface.setObjectName(card.objectName())
        self._card_surface.setAttribute(Qt.WA_StyledBackground)
        self._card_surface.setStyleSheet(card.styleSheet())
        self._card_surface.ensurePolished()
        self._card_surface.hide()

        # Keep the native form at its final size and render it only once.
        # Its bitmap is never scaled, including on displays with a higher DPI.
        self._form_pixmap = card.grab()
        self._card_effect = QGraphicsOpacityEffect(card)
        self._card_effect.setOpacity(0.0)
        card.setGraphicsEffect(self._card_effect)

        self._group = QParallelAnimationGroup(self)
        self._group.finished.connect(self._finish)
        self._clock = QVariantAnimation(self._group)
        self._clock.setDuration(self._DURATION)
        self._clock.setStartValue(0.0)
        self._clock.setEndValue(1.0)
        self._clock.setEasingCurve(QEasingCurve.Linear)
        self._clock.valueChanged.connect(self._advance)
        self._group.addAnimation(self._clock)
        self._advance(0.0)

    def _button_view(self, button, caption):
        view = _StyleButton(self)
        view.setProperty("skipButtonGpuRendering", True)
        view.setObjectName("popupMorphCaption" if caption else "popupMorphButton")
        view.setAttribute(Qt.WA_TransparentForMouseEvents)
        view.setFocusPolicy(Qt.NoFocus)
        view.setAutoDefault(False)
        view.setFont(button.font())
        view.setPalette(button.palette())
        view.setLayoutDirection(button.layoutDirection())
        view.setEnabled(button.isEnabled())
        view.setProperty("role", button.property("role"))
        if caption:
            view.setText(button.text())
            view.setIcon(button.icon())
            view.setIconSize(button.iconSize())
        stylesheet = button.styleSheet()
        if caption:
            stylesheet += """
                QPushButton#popupMorphCaption,
                QPushButton#popupMorphCaption:hover,
                QPushButton#popupMorphCaption:pressed {
                    background: transparent; border-color: transparent;
                }
            """
        view.setStyleSheet(stylesheet)
        view.ensurePolished()
        view.hide()
        return view

    @staticmethod
    def _opacity(progress, start, end, keyframes=()):
        previous_at, previous_value = 0.0, start
        for at, value in (*keyframes, (1.0, end)):
            if progress <= at:
                blend = (progress - previous_at) / max(0.00001, at - previous_at)
                return previous_value + (value - previous_value) * blend
            previous_at, previous_value = at, value
        return end

    def _advance(self, progress):
        self._destination_opacity = (
            max(0.0, min(1.0, (progress - 0.68) / 0.17))
            if not self._opening and self._destination_frame is not None else 0.0
        )
        motion = _spring_progress(progress if self._opening else min(1.0, progress / 0.68))
        self._caption_motion = motion
        start, end = (self._launch_rect, self._card_rect) if self._opening else (self._card_rect, self._button_rect)
        self._surface_rect = QRectF(
            start.x() + (end.x() - start.x()) * motion,
            start.y() + (end.y() - start.y()) * motion,
            start.width() + (end.width() - start.width()) * motion,
            start.height() + (end.height() - start.height()) * motion,
        )
        growth = max(0.0, min(1.0,
            (self._surface_rect.width() - self._button_rect.width()) / self._growth_width
        ))
        self._caption_scale = (
            self._launch_scale + (1.25 - self._launch_scale) * growth
            if self._opening else 1.0 + 0.25 * growth
        )
        bounded_motion = max(0.0, min(1.0, motion))
        self._radius = (
            7 * self._launch_scale + (14 - 7 * self._launch_scale) * bounded_motion
            if self._opening else 7 + 7 * (1 - bounded_motion)
        )
        eased = self._fade_easing.valueForProgress(progress)
        if self._opening:
            self._button_opacity = self._opacity(eased, 1.0, 0.0, ((0.30, 0.0),))
            self._surface_opacity = self._opacity(progress, 0.0, 0.0, ((0.12, 1.0), (0.65, 1.0)))
            self._caption_opacity = self._opacity(progress, 1.0, 0.0, ((0.04, 1.0), (0.36, 0.0)))
            self._form_opacity = self._opacity(progress, 0.0, 1.0, ((0.65, 0.0),))
        else:
            self._button_opacity = self._opacity(eased, 0.0, 1.0, ((0.30, 0.0), (0.65, 1.0)))
            self._surface_opacity = self._opacity(eased, 0.0, 0.0, ((0.25, 1.0), (0.30, 1.0), (0.65, 0.0)))
            self._caption_opacity = self._opacity(progress, 0.0, 1.0, ((0.12, 0.0), (0.55, 1.0)))
            self._form_opacity = self._opacity(eased, 1.0, 0.0, ((0.25, 0.0),))
            if self._defer_source_handoff:
                self._caption_opacity = 1.0
            if self._source_effect is not None and not self._defer_source_handoff:
                # Reveal the real button after the spring settles, then fade
                # the popup away while its final-size drawing still matches.
                self._source_effect.setOpacity(self._opacity(
                    progress, 0.0, 1.0, ((0.70, 0.0), (0.78, 1.0)),
                ))
                self._host.window().setWindowOpacity(self._opacity(
                    progress, 1.0, 0.0, ((0.85, 1.0),),
                ))
        self.update()

    def paintEvent(self, event):
        image = render_gpu(self.size(), self.devicePixelRatioF(), self._paint_gpu)
        self._skia_gpu_active = image is not None
        painter = QPainter(self)
        # Erase the previous spring outline on the translucent popup canvas.
        painter.setCompositionMode(QPainter.CompositionMode_Source)
        painter.fillRect(self.rect(), Qt.transparent)
        painter.setCompositionMode(QPainter.CompositionMode_SourceOver)
        if image is not None:
            painter.drawImage(QPoint(), image)
            return
        self._paint_qt(painter)

    def _paint_qt(self, painter):
        blend = self._destination_opacity
        weight = 1.0 - blend
        if blend == 1.0:
            painter.drawPixmap(self._button_rect.topLeft(), self._destination_frame)
            return
        painter.setRenderHints(
            QPainter.Antialiasing | QPainter.TextAntialiasing | QPainter.SmoothPixmapTransform
        )
        path = QPainterPath()
        path.addRoundedRect(self._surface_rect, self._radius, self._radius)
        painter.save()
        painter.setClipPath(path)
        if self._blur_pixmap is not None:
            painter.drawPixmap(self._blur_origin, self._blur_pixmap)

        self._button_surface.draw(painter, self._surface_rect, self._button_opacity * weight)
        if self._surface_opacity > 0.0:
            option = QStyleOption()
            option.initFrom(self._card_surface)
            option.rect = QRect(0, 0, max(1, round(self._surface_rect.width())), max(1, round(self._surface_rect.height())))
            painter.save()
            painter.setOpacity(self._surface_opacity * weight)
            painter.translate(self._surface_rect.topLeft())
            painter.scale(self._surface_rect.width() / option.rect.width(), self._surface_rect.height() / option.rect.height())
            self._card_surface.style().drawPrimitive(QStyle.PE_Widget, option, painter, self._card_surface)
            painter.restore()
        if self._form_opacity > 0.0:
            painter.save()
            painter.setOpacity(self._form_opacity * weight)
            painter.drawPixmap(self._card_rect.topLeft(), self._form_pixmap)
            painter.restore()
        if self._caption_opacity > 0.0:
            image, rect = self._qt_caption()
            painter.save()
            painter.setOpacity(self._caption_opacity * weight)
            painter.drawImage(rect, image)
            painter.restore()
        painter.restore()
        if blend > 0.0:
            painter.save()
            painter.setOpacity(blend)
            painter.drawPixmap(self._button_rect.topLeft(), self._destination_frame)
            painter.restore()

    def _qt_surface(self, size, draw):
        # Qt owns stylesheet resolution, glyphs and icons. Only compositing,
        # clipping and the animation transforms are performed by Skia.
        dpr = self.devicePixelRatioF()
        image = QImage(max(1, ceil(size.width() * dpr)),
                       max(1, ceil(size.height() * dpr)),
                       QImage.Format_ARGB32_Premultiplied)
        image.setDevicePixelRatio(dpr)
        image.fill(Qt.transparent)
        painter = QPainter(image)
        painter.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing |
                               QPainter.SmoothPixmapTransform)
        draw(painter)
        painter.end()
        return image

    def _paint_gpu(self, canvas, bridge):
        blend = self._destination_opacity
        weight = 1.0 - blend
        if blend == 1.0:
            bridge.draw_image(canvas, self._destination_frame, self._button_rect)
            return
        rect = self._surface_rect
        clip = skia.RRect.MakeRectXY(
            skia.Rect.MakeXYWH(rect.x(), rect.y(), rect.width(), rect.height()),
            self._radius, self._radius,
        )
        if self._blur_pixmap is not None:
            canvas.save()
            canvas.clipRRect(clip, True)
            bridge.draw_image(canvas, self._blur_pixmap, QRectF(
                self._blur_origin.toPointF(), self._blur_pixmap.deviceIndependentSize(),
            ))
            canvas.restore()

        local = QRectF(0, 0, rect.width(), rect.height())
        if self._button_opacity > 0.0:
            option = QStyleOptionButton()
            self._button_surface.initStyleOption(option)
            option.state &= ~(QStyle.State_MouseOver | QStyle.State_HasFocus | QStyle.State_Sunken)
            surface = button_surface(self._button_surface, option)
            if surface is not None:
                surface.draw(canvas, bridge, rect, self._button_opacity * weight, self._radius)
            else:
                canvas.save()
                canvas.clipRRect(clip, True)
                image = self._qt_surface(local.size(), lambda painter:
                    self._button_surface.draw(painter, local, 1.0))
                bridge.draw_image(canvas, image, rect, self._button_opacity * weight)
                canvas.restore()
        if self._surface_opacity > 0.0:
            def draw_card(painter):
                option = QStyleOption()
                option.initFrom(self._card_surface)
                option.rect = QRect(0, 0, max(1, round(rect.width())),
                                    max(1, round(rect.height())))
                painter.scale(rect.width() / option.rect.width(),
                              rect.height() / option.rect.height())
                self._card_surface.style().drawPrimitive(
                    QStyle.PE_Widget, option, painter, self._card_surface,
                )
            surface = button_surface(self._card_surface)
            if surface is not None:
                surface.draw(canvas, bridge, rect, self._surface_opacity * weight, self._radius)
            else:
                canvas.save()
                canvas.clipRRect(clip, True)
                image = self._qt_surface(local.size(), draw_card)
                bridge.draw_image(canvas, image, rect, self._surface_opacity * weight)
                canvas.restore()
        if self._form_opacity > 0.0:
            canvas.save()
            canvas.clipRRect(clip, True)
            bridge.draw_image(canvas, self._form_pixmap, self._card_rect,
                              self._form_opacity * weight)
            canvas.restore()
        if self._caption_opacity > 0.0:
            canvas.save()
            canvas.clipRRect(clip, True)
            image, caption_rect = self._qt_caption()
            bridge.draw_image(canvas, image, caption_rect, self._caption_opacity * weight)
            canvas.restore()
        if blend > 0.0:
            bridge.draw_image(canvas, self._destination_frame, self._button_rect, blend)

    def _qt_caption(self):
        # Rasterize the small caption at its actual transform in Qt. Enlarging
        # a native-size glyph bitmap would change its weight and sharpness.
        dpr = self.devicePixelRatioF()
        scale = self._caption_scale
        center = self._surface_rect.center()
        if self._caption_start is not None and not self._opening:
            motion = self._caption_motion
            center = self._caption_start + (self._button_rect.center()-self._caption_start)*motion
        size = self._caption_rect.size() * scale
        left, top = center.x() - size.width() / 2, center.y() - size.height() / 2
        x, y = floor(left * dpr) / dpr, floor(top * dpr) / dpr
        width = ceil((left + size.width() - x) * dpr) / dpr
        height = ceil((top + size.height() - y) * dpr) / dpr
        caption_rect = QRectF(x, y, width, height)
        transform = (dpr, scale, left - x, top - y, width, height)
        if self._caption_image is None or self._caption_transform != transform:
            def draw_caption(painter):
                painter.translate(center.x() - x, center.y() - y)
                painter.scale(scale, scale)
                painter.translate(-self._caption_rect.center())
                if self._caption_draw is not None:
                    self._caption_draw(painter)
                else:
                    self._caption.draw(painter, self._caption_rect, 1.0)
            self._caption_image = self._qt_surface(caption_rect.size(), draw_caption)
            self._caption_transform = transform
        return self._caption_image, caption_rect

    @staticmethod
    def settle_card(host, card):

        host.clearMask()
        surface = getattr(host, "_morph_blur_surface", None)
        if surface is not None:
            # Glass ends inside the one-pixel native border. The extra
            # animation margin must stay transparent, including its corners.
            surface.pin_backdrop_to(host.mapToGlobal(QPoint()))
            surface.setGeometry(card.geometry().adjusted(1, 1, -1, -1))
            surface._corner = 13.0
        host.window().update()

    @staticmethod
    def expand_backdrop(host):

        surface = getattr(host, "_morph_blur_surface", None)
        if surface is not None:
            surface.setGeometry(host.rect())
            surface._corner = 14.0
            surface.pin_backdrop_to(host.mapToGlobal(QPoint()))
        return surface

    def start(self):
        from main_ext.class_ThemeUi import theme
        surface = self.expand_backdrop(self._host)
        if surface is not None and theme.blur_enabled:
            self._blur_surface = surface
            self._blur_pixmap = surface.grab()
            self._blur_origin = surface.pos()
        if surface is not None:
            surface.hide()
        self._host.clearMask()
        self.show()
        self.raise_()
        self._group.start()

    def stop(self, restore=True):
        if self._stopped:
            return
        self._stopped = True
        self._group.stop()
        self.hide()
        if restore:
            self._restore_card()
        self.deleteLater()

    def _restore_card(self):
        from main_ext.class_ThemeUi import theme
        if self._card.graphicsEffect() is self._card_effect:
            self._card.setGraphicsEffect(None)
        self.settle_card(self._host, self._card)
        if self._blur_surface is not None and theme.blur_enabled:
            self._blur_surface.show()

    def _finish(self):
        # Keep the closing drawing until its owner hides the native window.
        if self._opening:
            self.stop()
        self.finished.emit()
