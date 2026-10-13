# SPDX-License-Identifier: MPL-2.0
"""One cached page/identity/footer scene; Qt supplies all font pixels."""
import time
from PySide6.QtCore import Qt, QEvent, QTimer, QPoint, QRectF, Signal
from PySide6.QtGui import QColor, QPainter, QImage
from PySide6.QtWidgets import QWidget, QGraphicsOpacityEffect, QLabel, QApplication
from shiboken6 import isValid

from app_ext.class_StartupVisual import StartupContentReveal
from core.skia_button import button_surface, button_label
from core.skia_patch import SurfacePatch


class AppPageTransition(QWidget):
    finished = Signal()

    def __init__(self, page, project_list=None, footer=None, profile=None, excluded_widgets=(), bottom_up=False):
        super().__init__(page.parentWidget())
        from main_ext.class_ThemeUi import theme
        self._page = page
        self._background = QColor(theme.BG)
        self._stopped = False
        self._root = self.window()
        self._started_at = None
        self._footer, self._profile = footer, profile
        self._profile_layers = []
        self._profile_shape = None
        self._profile_patch = None
        self._profile_target = None
        self._profile_target_effect = None
        self._footer_layers = []
        self._excluded = list(excluded_widgets)
        self.setObjectName("appPageTransition")
        self.setStyleSheet(f"QWidget#appPageTransition {{background:{theme.BG};border:none;}}")
        self.setAttribute(Qt.WA_OpaquePaintEvent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setGeometry(page.geometry())
        self.hide()

        # Reuse the original band geometry/timing without its live translucent
        # overlay. This model never paints or starts its own timer.
        self._reveal = StartupContentReveal(page, project_list, bottom_up=bottom_up)
        self._reveal.hide()
        self._bands = self._reveal._bands()
        self._end_at = self._reveal._FIRST_DELAY + max(0, len(self._bands)-1)*self._reveal._ROW_DELAY + self._reveal._DURATION

        if profile is not None:
            profile.hide()
            profile._group.finished.disconnect(profile._finish)
            profile._target_effect = QGraphicsOpacityEffect(profile._target)
            profile._target_effect.setOpacity(0)
            profile._target.setGraphicsEffect(profile._target_effect)
            self._profile_target = profile._target
            self._profile_target_effect = profile._target_effect
            self._profile_shape = button_surface(profile._surface)
            if self._profile_shape is not None:
                self._profile_patch = SurfacePatch(self._profile_shape, self.devicePixelRatioF())
            for view in profile.findChildren(QWidget, options=Qt.FindDirectChildrenOnly):
                if view is profile._surface:
                    continue
                effect = view.graphicsEffect()
                if effect is not None:
                    effect.setEnabled(False)
                try:
                    pixmap = view.pixmap() if isinstance(view, QLabel) else None
                    source = pixmap if pixmap is not None and not pixmap.isNull() else view.grab()
                    self._profile_layers.append((view, source))
                finally:
                    if effect is not None:
                        effect.setEnabled(True)
        if footer is not None:
            footer.hide()
            footer._group.finished.disconnect(footer._finish)
            for view in footer._views:
                from PySide6.QtWidgets import QStyleOptionButton, QStyle
                option = QStyleOptionButton()
                view.ensurePolished()
                view.initStyleOption(option)
                option.state &= ~(QStyle.State_MouseOver | QStyle.State_HasFocus | QStyle.State_Sunken)
                shape = button_surface(view, option)
                patch = SurfacePatch(shape, self.devicePixelRatioF()) if shape is not None else None
                caption = None
                if view._skia_layer_kind != "shape":
                    caption = button_label(view, option, self.devicePixelRatioF()) if shape is not None else footer._view_image(view)
                self._footer_layers.append((view, patch, caption))

        page.ensurePolished()
        if page.layout() is not None:
            page.layout().activate()
        self._snapshot = page.grab().toImage().convertToFormat(QImage.Format_ARGB32_Premultiplied)
        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.PreciseTimer)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._tick)
        self._completion_timer = QTimer(self)
        self._completion_timer.setSingleShot(True)
        self._completion_timer.timeout.connect(self.stop)
        page.installEventFilter(self)
        watched = [page, self._profile_target, self._profile_target_effect]
        if profile is not None:
            watched.extend([profile, profile._surface])
            watched.extend(view for view, _ in self._profile_layers)
        if footer is not None:
            watched.append(footer)
            watched.extend(view for view, _, _ in self._footer_layers)
        for obj in watched:
            if obj is not None:
                obj.destroyed.connect(self.stop)

    def _usable(self):
        if self._stopped or not isValid(self._page):
            return False
        for model in (self._profile, self._footer):
            if model is not None and (not isValid(model) or not isValid(model._group)):
                return False
        if self._profile is not None:
            if (self._profile._stopped or not isValid(self._profile._surface)
                    or not isValid(self._profile_target)
                    or self._profile._surface.graphicsEffect() is None):
                return False
            if any(not isValid(view) for view, _ in self._profile_layers):
                return False
        return all(isValid(view) and view.graphicsEffect() is not None for view, _, _ in self._footer_layers)

    def start(self):
        if not self._usable():
            self.stop()
            return
        root = self.window()
        for model in (self._profile, self._footer):
            if model is not None:
                model._group.start()
                model._group.pause()
        root._cached_page_transition_active = True
        self.show()
        self.setAttribute(Qt.WA_OpaquePaintEvent)
        self.raise_()
        self._started_at = time.monotonic()
        self._timer.start()
        self._completion_timer.start(round(self._end_at * 1000) + 32)
        self._tick()

    def _tick(self):
        if not self._usable():
            self.stop()
            return
        elapsed = time.monotonic() - self._started_at
        for model in (self._profile, self._footer):
            if model is not None:
                model._group.setCurrentTime(min(round(elapsed*1000), model._DURATION))
        self.update()
        if elapsed >= self._end_at:
            self.stop()

    def _alphas(self):
        elapsed = None if self._started_at is None else time.monotonic() - self._started_at
        return self._reveal._band_alphas(self._bands, elapsed)

    def _rect(self, widget):
        return QRectF(widget.geometry()).translated(widget.parentWidget().mapTo(self.parentWidget(), QPoint()) - self.pos())

    def paintEvent(self, event):
        if not self._usable() or self._snapshot is None:
            self.stop()
            return
        painter = QPainter(self)
        # Qt presents cached font pixels and Skia corner/edge assets. Geometry
        # and alpha changes do not cause Skia redraw/readback or widget paints.
        painter.drawImage(QPoint(), self._snapshot)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.save()
        from PySide6.QtGui import QRegion
        region = QRegion(self.rect())
        for widget in self._excluded:
            if isValid(widget):
                region -= QRegion(widget.rect().translated(widget.mapTo(self._page, QPoint())))
        painter.setClipRegion(region)
        for (top, bottom), alpha in zip(self._bands, self._alphas()):
            color = QColor(self._background)
            color.setAlpha(alpha)
            painter.fillRect(0, top, self.width(), bottom-top, color)
        painter.restore()
        if self._profile is not None:
            view = self._profile._surface
            if self._profile_patch is not None:
                self._profile_patch.draw(painter, self._rect(view), view.graphicsEffect().opacity())
            for view, image in self._profile_layers:
                effect = view.graphicsEffect()
                painter.setOpacity(effect.opacity() if effect else 1)
                painter.drawPixmap(self._rect(view), image, QRectF(image.rect()))
        if self._footer is not None:
            painter.setOpacity(1)
            painter.translate(self._footer.pos()-self.pos())
            painter.fillRect(self._footer.rect(), self._background)
            for view, patch, caption in self._footer_layers:
                alpha = view.graphicsEffect().opacity()
                rect = QRectF(view.geometry())
                if patch is not None:
                    patch.draw(painter, rect, alpha)
                if caption is not None:
                    painter.setOpacity(alpha)
                    painter.drawImage(rect, caption)

    def eventFilter(self, obj, event):
        if obj is self._page and event.type() in (QEvent.Resize, QEvent.Hide, QEvent.DevicePixelRatioChange):
            self.stop()
        return False

    def stop(self):
        if self._stopped:
            return
        self._stopped = True
        self._timer.stop()
        self._completion_timer.stop()
        if isValid(self._page):
            self._page.removeEventFilter(self)
        if isValid(self._reveal):
            self._reveal.stop()
        if isValid(self._root):
            self._root._cached_page_transition_active = False
        target, effect = self._profile_target, self._profile_target_effect
        if target is not None and isValid(target) and effect is not None and isValid(effect):
            if target.graphicsEffect() is effect:
                target.setGraphicsEffect(None)
        self._profile_target_effect = None
        for model in (self._profile, self._footer):
            if model is not None and isValid(model):
                if model is self._profile:
                    if isValid(model._group):
                        model._group.stop()
                    model._stopped = True
                    model._target_effect = None
                    model.hide()
                    model.deleteLater()
                else:
                    model.stop()
                model.finished.emit()
        self.hide()
        if isValid(self._page):
            self._page.update()
        self._snapshot = None
        self._profile_layers.clear()
        self._footer_layers.clear()
        clock = getattr(QApplication.instance(), "_visible_label_clock", None)
        if clock is not None:
            clock.wake()
        self.finished.emit()
        self.deleteLater()
