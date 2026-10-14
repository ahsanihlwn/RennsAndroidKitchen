# SPDX-License-Identifier: MPL-2.0
"""Event-driven Qt backdrop source with bounded GUI capture work.

Only source changes refresh the Gaussian texture. Moving a card samples the
cached scene; its foreground and native Qt fonts never enter that source.
"""
import math
import time
from collections import OrderedDict

from PySide6.QtCore import QObject, QEvent, QPoint, QPointF, QRect, QRectF, QSize, QTimer, Qt, Slot
from PySide6.QtGui import QImage, QPainter, QPixmap, QRegion
from PySide6.QtWidgets import QApplication, QWidget, QLabel
from shiboken6 import isValid


class LiveBackdrop(QObject):
    def __init__(self, root, owner, overlay=None, radius=18.0):
        super().__init__(owner)
        self.root, self.owner, self.overlay, self.radius = root, owner, overlay, radius
        self.pad = math.ceil(radius * 1.5)+2
        self.cards = []
        self.image = QPixmap()
        self._source = QImage()
        self._stopped = True
        self._capturing = False
        self._capture_jobs = None
        self._capture_target = None
        self._last_timings = {}
        self._dirty_after_capture = False
        self._dirty = QRegion()
        self._labels = OrderedDict()
        self._label_bytes = 0
        self._label_hits = 0
        self._resize_active = False
        self._capture_abort = False
        self._image_size = QSize()
        self._previous_image = QPixmap()
        self._previous_size = QSize()
        self._blend_started = 0.0
        self._blend = 1.0
        self._resize_timer = QTimer(self)
        self._resize_timer.setSingleShot(True)
        self._resize_timer.setInterval(120)
        self._resize_timer.timeout.connect(self._settle_resize)
        self._blend_timer = QTimer(self)
        self._blend_timer.setTimerType(Qt.PreciseTimer)
        self._blend_timer.setInterval(16)
        self._blend_timer.timeout.connect(self._advance_blend)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(33)
        self._timer.timeout.connect(self.refresh)
        self._capture_timer = QTimer(self)
        self._capture_timer.setSingleShot(True)
        self._capture_timer.setTimerType(Qt.PreciseTimer)
        self._capture_timer.timeout.connect(self._advance_capture)

    def attach(self, card):
        if card not in self.cards:
            self.cards.append(card)
            card.installEventFilter(self)
        card._live_backdrop = self
        card._backdrop_origin = None
        if not self.image.isNull() and hasattr(card, "_blur_pix"):
            card._blur_pix = self.image

    def detach(self, card):
        from app_ext.class_BlurOverlay import _crop_logical
        if card in self.cards:
            self.cards.remove(card)
        if isValid(card):
            card.removeEventFilter(self)
            if not self.image.isNull() and hasattr(card, "_blur_pix"):
                origin = self.origin_for(card)
                card._blur_pix = _crop_logical(self.image, -origin.x(), -origin.y(), card.width(), card.height())
            card._live_backdrop = None
            card._backdrop_origin = None
        if not self.cards:
            self.stop()

    def origin_for(self, card):
        from app_ext.class_BlurOverlay import _widget_global_pos
        return _widget_global_pos(self.root)-_widget_global_pos(card)-QPoint(self.pad, self.pad)

    def _destination(self, card, image, size, view_size=None):
        from app_ext.class_BlurOverlay import _widget_global_pos
        view = view_size if view_size is not None else self.root.size()
        sx, sy = view.width()/max(1, size.width()), view.height()/max(1, size.height())
        offset = _widget_global_pos(self.root)-_widget_global_pos(card)
        image_size = image.deviceIndependentSize()
        return QRectF(offset.x()-self.pad*sx, offset.y()-self.pad*sy,
                      image_size.width()*sx, image_size.height()*sy)

    def layers_for(self, card):
        layers = []
        if not self._previous_image.isNull() and self._blend < 1.0:
            layers.append((self._previous_image, self._destination(card, self._previous_image, self._previous_size), 1.0))
        if not self.image.isNull():
            layers.append((self.image, self._destination(card, self.image, self._image_size), self._blend))
        return layers

    def _update_cards(self):
        for card in self.cards:
            if isValid(card):
                card.update()

    def _begin_resize(self, old_size):
        if not self._resize_active and self._blend_timer.isActive() and not old_size.isEmpty():
            # Keep the currently visible mix when another drag interrupts its
            # handoff; copying our blur texture never captures Qt foreground.
            ratio = self.image.devicePixelRatioF()
            frozen = QPixmap(math.ceil((old_size.width()+2*self.pad)*ratio),
                             math.ceil((old_size.height()+2*self.pad)*ratio))
            frozen.setDevicePixelRatio(ratio)
            frozen.fill(Qt.transparent)
            painter = QPainter(frozen)
            painter.setRenderHint(QPainter.SmoothPixmapTransform)
            for image, size, alpha in ((self._previous_image, self._previous_size, 1.0),
                                       (self.image, self._image_size, self._blend)):
                if not image.isNull():
                    rect = self._destination(self.root, image, size, old_size).translated(self.pad, self.pad)
                    painter.setOpacity(alpha)
                    painter.drawPixmap(rect, image, QRectF(image.rect()))
            painter.end()
            self.image, self._image_size = frozen, QSize(old_size)
            self._source = QImage()
            for card in self.cards:
                if isValid(card) and hasattr(card, "_blur_pix"):
                    card._blur_pix = frozen
        self._blend_timer.stop()
        self._previous_image = QPixmap()
        self._blend = 1.0
        self._resize_active = True
        self._timer.stop()
        self._capture_timer.stop()
        if self._capturing:
            self._capture_abort = True
        else:
            self._capture_jobs = self._capture_target = None
        self._dirty = QRegion(self.root.rect())
        self._resize_timer.start()
        self._update_cards()

    @Slot()
    def _settle_resize(self):
        self._resize_active = False
        if self._alive():
            self._dirty = QRegion(self.root.rect())
            self.refresh()

    @Slot()
    def _advance_blend(self):
        at = min(1.0, (time.perf_counter()-self._blend_started)/.12)
        self._blend = 1-(1-at)**3
        if at >= 1:
            self._blend_timer.stop()
            self._previous_image = QPixmap()
            if not self._dirty.isEmpty():
                self._timer.start()
        self._update_cards()

    def _excluded(self):
        result = [self.owner]
        if self.overlay is not None:
            result.append(self.overlay)
            result.extend(getattr(self.overlay, "_backdrop_exclusions", ()))
        for card in self.cards:
            result.append(card)
            result.append(getattr(card, "_backdrop_exclusion", None))
        return tuple(obj for obj in result if obj is not None and isValid(obj))

    def resume(self):
        if self._stopped:
            self._stopped = False
            QApplication.instance().installEventFilter(self)
        if self.image.isNull():
            self.refresh(force=True)

    def stop(self):
        self._stopped = True
        self._timer.stop()
        self._capture_timer.stop()
        self._resize_timer.stop()
        self._blend_timer.stop()
        self._resize_active = False
        self._previous_image = QPixmap()
        self._blend = 1.0
        self._capture_jobs = self._capture_target = None
        app = QApplication.instance()
        if app is not None:
            app.removeEventFilter(self)

    def _alive(self):
        self.cards = [card for card in self.cards if isValid(card)]
        return (not self._stopped and isValid(self.root) and self.root.isVisible()
                and any(card.isVisible() for card in self.cards))

    @Slot()
    def refresh(self, force=False):
        if not self._alive():
            return
        if self._resize_active or self._blend_timer.isActive():
            self._dirty = QRegion(self.root.rect())
            return
        if getattr(self.root.window(), "_cached_page_transition_active", False):
            self._timer.start()
            return
        if self._capture_jobs is not None:
            self._dirty_after_capture = True
            return
        ratio = min(self.root.devicePixelRatioF(), 960/max(1, self.root.width()+2*self.pad, self.root.height()+2*self.pad))
        self._capture_ratio = ratio
        self._capture_size = self.root.size()
        self._capture_abort = False
        if force:
            start = time.perf_counter()
            self._capturing = True
            from main_ext.class_ThemeUi import theme
            target = QPixmap(math.ceil((self.root.width()+2*self.pad)*ratio),
                             math.ceil((self.root.height()+2*self.pad)*ratio))
            target.setDevicePixelRatio(ratio)
            target.fill(theme.BG)
            painter = QPainter(target)
            try:
                for widget, offset, region, root in self._jobs(self.root,
                        QPoint(self.pad, self.pad), self.root.rect(), self._excluded(), True):
                    if not root and self._draw_cached_label(widget, painter, offset, region):
                        continue
                    flags = QWidget.RenderFlag.DrawWindowBackground if root else QWidget.RenderFlag(0)
                    widget.render(painter, offset+region.boundingRect().topLeft(), region, flags)
            finally:
                painter.end()
                self._capturing = False
            work = (time.perf_counter()-start)*1000
            if self._capture_abort:
                return
            self._publish(target, work, work)
            self._scene_size = self.root.size()
            self._dirty = QRegion()
            return
        from main_ext.class_ThemeUi import theme
        reuse = (not self._source.isNull() and self._source.devicePixelRatioF() == ratio
                 and getattr(self, "_scene_size", None) == self.root.size())
        if reuse:
            self._capture_target = QPixmap.fromImage(self._source)
        else:
            self._capture_target = QPixmap(math.ceil((self.root.width()+2*self.pad)*ratio),
                                           math.ceil((self.root.height()+2*self.pad)*ratio))
            self._capture_target.setDevicePixelRatio(ratio)
            self._capture_target.fill(theme.BG)
        dirty = self._dirty.boundingRect() if reuse and not self._dirty.isEmpty() else self.root.rect()
        self._dirty = QRegion()
        self._capture_jobs = iter(self._jobs(self.root, QPoint(self.pad, self.pad),
            dirty.intersected(self.root.rect()), self._excluded(), True))
        self._capture_started = time.perf_counter()
        self._capture_work_ms = 0.0
        self._dirty_after_capture = False
        self._capture_timer.start(0)

    def _jobs(self, widget, target, clip, excluded, root=False):
        if widget in excluded or not isValid(widget) or not widget.isVisible():
            return
        region = QRegion(widget.rect()).intersected(clip if isinstance(clip, QRegion) else QRegion(clip))
        if not widget.mask().isEmpty():
            region &= widget.mask()
        if region.isEmpty():
            return
        yield widget, target, region, root
        from PySide6.QtWidgets import QWidget
        for child in widget.children():
            if isinstance(child, QWidget) and isValid(child) and not child.isWindow():
                child_clip = region.translated(-child.pos()).intersected(QRegion(child.rect()))
                if not child_clip.isEmpty():
                    yield from self._jobs(child, target+child.pos(), child_clip, excluded)

    def _draw_cached_label(self, widget, painter, target, region):
        if (not isinstance(widget, QLabel) or type(widget).paintEvent is not QLabel.paintEvent
                or widget.graphicsEffect() is not None):
            return False
        ratio = self._capture_ratio
        x, y = target.x()*ratio, target.y()*ratio
        phase_x, phase_y = x-math.floor(x), y-math.floor(y)
        pixmap = widget.pixmap()
        movie = widget.movie()
        key = (id(widget), widget.width(), widget.height(), ratio, round(phase_x, 8), round(phase_y, 8),
               widget.text(), widget.font().toString(), widget.palette().cacheKey(), widget.styleSheet(),
               widget.objectName(), widget.alignment(), widget.textFormat(), widget.wordWrap(),
               widget.indent(), widget.margin(), widget.isEnabled(), widget.underMouse(),
               widget.selectionStart(), widget.selectedText(),
               pixmap.cacheKey() if pixmap is not None else 0,
               movie.currentFrameNumber() if movie is not None else -1)
        layer = self._labels.pop(key, None)
        if layer is None:
            layer = QPixmap(max(1, math.ceil(widget.width()*ratio+phase_x)),
                            max(1, math.ceil(widget.height()*ratio+phase_y)))
            layer.setDevicePixelRatio(ratio)
            layer.fill(Qt.transparent)
            label_painter = QPainter(layer)
            label_painter.translate(phase_x/ratio, phase_y/ratio)
            try:
                widget.render(label_painter, QPoint(), QRegion(widget.rect()), QWidget.RenderFlag(0))
            finally:
                label_painter.end()
            self._label_bytes += layer.width()*layer.height()*4
        else:
            self._label_hits += 1
        self._labels[key] = layer
        while self._label_bytes > 8*1024*1024 or len(self._labels) > 256:
            _, discarded = self._labels.popitem(last=False)
            self._label_bytes -= discarded.width()*discarded.height()*4
        painter.save()
        painter.setClipRegion(region.translated(target), Qt.IntersectClip)
        painter.drawPixmap(QPointF(math.floor(x)/ratio, math.floor(y)/ratio), layer)
        painter.restore()
        return True

    @Slot()
    def _advance_capture(self):
        if self._capture_jobs is None or self._capture_target is None:
            return
        if not self._alive() or self.root.size() != self._capture_size:
            self._capture_jobs = self._capture_target = None
            if self._alive():
                self._timer.start()
            return
        if getattr(self.root.window(), "_cached_page_transition_active", False):
            self._capture_jobs = self._capture_target = None
            self._dirty = QRegion(self.root.rect())
            self._timer.start()
            return
        start = time.perf_counter()
        self._capturing = True
        finished = False
        painter = QPainter(self._capture_target)
        try:
            while time.perf_counter()-start < .004:
                try:
                    widget, target, region, root = next(self._capture_jobs)
                except StopIteration:
                    finished = True
                    break
                if not isValid(widget):
                    continue
                if not root and self._draw_cached_label(widget, painter, target, region):
                    continue
                # QWidget.render's target offset is the region's origin.
                flags = QWidget.RenderFlag.DrawWindowBackground if root else QWidget.RenderFlag(0)
                widget.render(painter, target+region.boundingRect().topLeft(), region, flags)
        finally:
            painter.end()
            self._capturing = False
            self._capture_work_ms += (time.perf_counter()-start)*1000
        if self._capture_abort:
            self._capture_jobs = self._capture_target = None
            return
        if not finished:
            self._capture_timer.start(0)
            return
        target, self._capture_target = self._capture_target, None
        self._capture_jobs = None
        self._publish(target, self._capture_work_ms, (time.perf_counter()-self._capture_started)*1000)
        self._scene_size = self.root.size()
        if self._dirty_after_capture:
            self._timer.start()

    def _publish(self, target, work, elapsed):
        source = target.toImage()
        start = time.perf_counter()
        if source != self._source:
            from app_ext.class_BlurOverlay import make_blur_pixmap
            image = make_blur_pixmap(target, self.radius)
            if not self.image.isNull() and self._image_size != self._capture_size:
                self._previous_image, self._previous_size = self.image, QSize(self._image_size)
                self._blend = 0.0
                self._blend_started = time.perf_counter()
                self._blend_timer.start()
            self.image = image
            self._image_size = QSize(self._capture_size)
            self._source = source
            for card in self.cards:
                if isValid(card):
                    if hasattr(card, "_blur_pix"):
                        card._blur_pix = self.image
                    card.update()
        self._last_timings = {"capture_work_ms": work, "capture_elapsed_ms": elapsed,
                              "filter_ms": (time.perf_counter()-start)*1000}

    def eventFilter(self, obj, event):
        kind = event.type()
        if obj is self.root and kind == QEvent.Resize:
            self._begin_resize(event.oldSize())
            return False
        if kind in (QEvent.StyleChange, QEvent.FontChange, QEvent.PaletteChange):
            # Inherited styles can change without altering the local QSS.
            for key in [key for key in self._labels if key[0] == id(obj)]:
                discarded = self._labels.pop(key)
                self._label_bytes -= discarded.width()*discarded.height()*4
        if obj in self.cards:
            if kind in (QEvent.Move, QEvent.Resize, QEvent.Show):
                obj.update()
                if kind == QEvent.Show:
                    self.resume()
                    if not self._dirty.isEmpty() or getattr(self, "_scene_size", None) != self.root.size():
                        self._timer.start()
            elif kind in (QEvent.Hide, QEvent.Close) and not any(isValid(card) and card.isVisible() for card in self.cards):
                self._timer.stop()
                self._capture_timer.stop()
                self._capture_jobs = self._capture_target = None
                self._resize_timer.stop()
                self._blend_timer.stop()
                self._resize_active = False
                self._blend = 1.0
                self._previous_image = QPixmap()
            return False
        if (self._stopped or self._capturing or kind != QEvent.Paint
                or not hasattr(obj, "window") or not isValid(self.root)):
            return False
        try:
            if obj.window() is not self.root.window():
                return False
            if self.overlay is not None and (getattr(self.overlay, "_opacity", 1) < .995
                                             or getattr(self.overlay, "_closing", False)):
                return False
            if any(obj is item or item.isAncestorOf(obj) for item in self._excluded()):
                return False
            if obj is not self.root and not self.root.isAncestorOf(obj):
                return False
            from app_ext.class_BlurOverlay import _widget_global_pos
            region = event.region().translated(_widget_global_pos(obj)-_widget_global_pos(self.root))
            self._dirty |= region.intersected(QRegion(self.root.rect()))
        except (RuntimeError, AttributeError):
            return False
        if self._resize_active or self._blend_timer.isActive():
            return False
        if self._capture_jobs is not None:
            self._dirty_after_capture = True
        elif not self._timer.isActive():
            self._timer.start()
        return False
