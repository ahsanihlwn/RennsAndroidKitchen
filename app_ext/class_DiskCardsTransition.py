# SPDX-License-Identifier: MPL-2.0
from PySide6.QtCore import Qt, QEvent, QPoint, QPointF, QRect, QRectF, Signal, QVariantAnimation, QEasingCurve
from PySide6.QtGui import QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import QWidget, QSizePolicy


class DiskCardsTransition(QWidget):
    finished = Signal()
    EXIT_DURATION = 380
    MOVE_DURATION = 310
    MOVE_DELAY = round(EXIT_DURATION * 0.70)
    MOVE_STAGGER = 55

    @staticmethod
    def capture(host, cards):
        # An atomic layout change disables page updates. QWidget.grab() then
        # omits child controls unless updates are temporarily enabled here.
        owner = host
        while owner.parentWidget() is not None and not owner.parentWidget().updatesEnabled():
            owner = owner.parentWidget()
        enabled = owner.updatesEnabled()
        if not enabled:
            owner.setUpdatesEnabled(True)
        result = {}
        try:
            for key, entry in cards.items():
                widget = entry["widget"]
                result[key] = (QRect(widget.mapTo(host, QPoint()), widget.size()),
                               DiskCardsTransition._rounded_card(widget))
        finally:
            if not enabled:
                owner.setUpdatesEnabled(False)
        return result

    @staticmethod
    def _rounded_card(widget):
        widget.ensurePolished()
        if widget.layout() is not None:
            widget.layout().activate()
        effect = widget.graphicsEffect()
        enabled = effect is not None and effect.isEnabled()
        if enabled:
            effect.setEnabled(False)
        try:
            image = widget.grab()
        finally:
            if enabled:
                effect.setEnabled(True)
        rounded = QPixmap(image.size())
        rounded.setDevicePixelRatio(image.devicePixelRatioF())
        rounded.fill(Qt.transparent)
        painter = QPainter(rounded)
        painter.setRenderHint(QPainter.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(QRectF(widget.rect()), 10, 10)
        painter.setClipPath(path)
        painter.drawPixmap(0, 0, image)
        painter.end()
        return rounded

    def __init__(self, host, source, cards):
        super().__init__(host)
        self.setObjectName("diskCardsTransition")
        self.setStyleSheet("QWidget#diskCardsTransition { background: transparent; border: none; }")
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_NoSystemBackground)
        self.setGeometry(host.rect())
        self._stopped = False
        self._targets = []
        self._scene = []
        self._elapsed = 0.0
        self._exit_ease = QEasingCurve(QEasingCurve.InOutSine)
        self._move_ease = QEasingCurve(QEasingCurve.OutCubic)
        target = self.capture(host, cards)
        added = target.keys() - source.keys()
        removed = source.keys() - target.keys()
        move_index = 0
        duration = self.EXIT_DURATION if removed else self.MOVE_DURATION
        for key in dict.fromkeys((*source, *target)):
            old, new = source.get(key), target.get(key)
            rect, pixmap = old or new
            destination = new[0] if new is not None else rect
            if not rect.intersects(host.rect()) and not destination.intersects(host.rect()):
                continue
            kind = "remove" if key in removed else "add" if key in added else "keep"
            delay = 0
            if kind == "keep" and rect != destination:
                if removed and not added:
                    delay = self.MOVE_DELAY + move_index * self.MOVE_STAGGER
                move_index += 1
                duration = max(duration, delay + self.MOVE_DURATION)
            self._scene.append((rect, destination, pixmap, kind, delay))
        self._scene.sort(key=lambda record: record[3] == "remove")
        for entry in cards.values():
            widget = entry["widget"]
            policy = QSizePolicy(widget.sizePolicy())
            self._targets.append((widget, policy, widget.isHidden()))
            retained = QSizePolicy(policy)
            retained.setRetainSizeWhenHidden(True)
            widget.setSizePolicy(retained)
            widget.hide()
        self._clock = QVariantAnimation(self)
        self._clock.setDuration(duration)
        self._clock.setStartValue(0.0)
        self._clock.setEndValue(float(duration))
        self._clock.valueChanged.connect(self._advance)
        self._clock.finished.connect(self.stop)
        host.installEventFilter(self)

    def eventFilter(self, watched, event):
        if event.type() in (QEvent.Resize, QEvent.Hide):
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
        for source, target, pixmap, kind, delay in self._scene:
            if kind == "remove":
                phase = self._exit_ease.valueForProgress(min(1.0, self._elapsed / self.EXIT_DURATION))
                painter.setOpacity(1.0 - phase)
                dx = max(2, round(source.width() * .02)) * phase
                dy = max(1, round(source.height() * .02)) * phase
                painter.drawPixmap(QRectF(source).adjusted(dx, dy, -dx, -dy), pixmap, QRectF(pixmap.rect()))
            elif kind == "add":
                phase = self._exit_ease.valueForProgress(min(1.0, self._elapsed / self.MOVE_DURATION))
                painter.setOpacity(phase)
                painter.drawPixmap(QPointF(target.topLeft()), pixmap)
            else:
                phase = self._move_ease.valueForProgress(max(0.0, min(1.0, (self._elapsed - delay) / self.MOVE_DURATION)))
                point = QPointF(source.topLeft()) + QPointF(target.topLeft() - source.topLeft()) * phase
                painter.setOpacity(1.0)
                painter.drawPixmap(point, pixmap)
        painter.end()

    def stop(self, *_):
        if self._stopped:
            return
        self._stopped = True
        self._clock.stop()
        host = self.parentWidget()
        host.removeEventFilter(self)
        enabled = host.updatesEnabled()
        host.setUpdatesEnabled(False)
        try:
            for widget, policy, hidden in self._targets:
                widget.setSizePolicy(policy)
                widget.setVisible(not hidden)
            self.hide()
        finally:
            host.setUpdatesEnabled(enabled)
        self.finished.emit()
        self.deleteLater()
