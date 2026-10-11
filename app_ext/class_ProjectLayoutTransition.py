# SPDX-License-Identifier: MPL-2.0
from PySide6.QtCore import Qt, QPoint, QPointF, QRect, QEvent, Signal, QVariantAnimation, QEasingCurve
from PySide6.QtGui import QPainterPath
from PySide6.QtWidgets import QWidget


class ProjectLayoutTransition(QWidget):
    finished = Signal()
    _CARD_DURATION = 440
    _STAGGER_FRACTION = 0.05

    def __init__(self, page, project_list, body_layout, sidebar, drives_layout,
                 source_cards, source_list, in_sidebar):
        super().__init__(page)
        self.setObjectName("projectLayoutTransition")
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_NoSystemBackground)
        self.setStyleSheet("QWidget#projectLayoutTransition { background: transparent; border: none; }")
        self.setGeometry(page.rect())
        self._page = page
        self._project_list = project_list
        self._body_layout = body_layout
        self._body = body_layout.parentWidget()
        self._sidebar = sidebar
        self._drives_layout = drives_layout
        self._in_sidebar = in_sidebar
        self._source_list = QRect(source_list)
        self._target_list = QRect(project_list.geometry())
        self._stopped = False
        self._cards = []
        self._frozen_rows = {}
        self._ease = QEasingCurve(QEasingCurve.OutCubic)
        self._stagger = self._CARD_DURATION * self._STAGGER_FRACTION

        # Reserve every destination while the actual widgets travel above it.
        # Their text, progress bars, and press effects remain native and intact.
        for widget, source in source_cards:
            index = drives_layout.indexOf(widget)
            placeholder = QWidget(drives_layout.parentWidget())
            placeholder.setFixedSize(widget.size())
            placeholder.setStyleSheet("background: transparent; border: none;")
            drives_layout.insertWidget(index, placeholder, 0, Qt.AlignVCenter)
            placeholder.show()
            widget.setParent(self)
            widget.setGeometry(source)
            widget.show()
            self._cards.append((widget, placeholder, index, QRect(source)))
        drives_layout.activate()

        body_layout.setEnabled(False)
        self._body.installEventFilter(self)
        page.installEventFilter(self)
        project_list.viewport().installEventFilter(self)
        project_list.verticalScrollBar().valueChanged.connect(self._sync_visible_rows)
        project_list.model().modelAboutToBeReset.connect(self.stop)
        project_list.model().rowsAboutToBeRemoved.connect(self.stop)
        self._sync_visible_rows()
        self._retarget()
        self._clock = QVariantAnimation(self)
        self._clock.setStartValue(0.0)
        self._clock.setEndValue(1.0)
        self._clock.setDuration(round(self._CARD_DURATION + max(0, len(self._cards)-1) * self._stagger))
        self._clock.valueChanged.connect(self._advance)
        self._clock.finished.connect(self.stop)
        self._advance(0.0)

    def eventFilter(self, watched, event):
        if not self._stopped and event.type() == QEvent.Resize:
            if watched is self._page:
                self.setGeometry(self._page.rect())
            elif watched is self._body:
                self._retarget()
                if hasattr(self, "_clock"):
                    self._advance(self._clock.currentValue())
            elif (watched is self._project_list.viewport()
                  and event.size().height() != event.oldSize().height()):
                self._sync_visible_rows()
        return super().eventFilter(watched, event)

    def _sync_visible_rows(self, *_):
        if self._stopped:
            return
        view = self._project_list
        viewport = view.viewport().rect()
        for index in range(view.count()):
            item = view.item(index)
            row = view.itemWidget(item)
            layout = row.layout() if row is not None else None
            if layout is None:
                continue
            visible = not item.isHidden() and view.visualItemRect(item).intersects(viewport)
            if visible:
                if layout in self._frozen_rows:
                    del self._frozen_rows[layout]
                    layout.setEnabled(True)
                    layout.invalidate()
                    layout.activate()
            elif layout.isEnabled():
                # QListWidget resizes every item's widget, even off screen.
                # Only visible rows need their child layouts during the motion.
                layout.setEnabled(False)
                self._frozen_rows[layout] = row

    def _retarget(self):
        margins = self._body_layout.contentsMargins()
        rect = self._body.contentsRect().adjusted(
            margins.left(), margins.top(), -margins.right(), -margins.bottom(),
        )
        if self._in_sidebar:
            self._sidebar.setGeometry(rect.x(), rect.y(), self._sidebar.width(), rect.height())
            rect.setLeft(rect.left() + self._sidebar.width() + self._body_layout.spacing())
        self._target_list = rect

    def _advance(self, progress):
        if self._stopped:
            return
        elapsed = float(progress) * self._clock.duration()
        for index, (widget, placeholder, _, source) in enumerate(self._cards):
            # Disk N+1 starts when disk N has completed 5% of its timeline.
            phase = max(0.0, min(1.0, (elapsed-index*self._stagger)/self._CARD_DURATION))
            end = placeholder.mapTo(self._page, QPoint())
            start = source.topLeft()
            dx, dy = end.x()-start.x(), end.y()-start.y()
            path = QPainterPath(QPointF(start))
            path.cubicTo(
                QPointF(start.x()+dx*0.25, start.y()+dy*0.65),
                QPointF(start.x()+dx*0.80, start.y()+dy*0.95),
                QPointF(end),
            )
            point = path.pointAtPercent(self._ease.valueForProgress(phase))
            widget.move(round(point.x()), round(point.y()))

        # Resize the real list instead of stretching a screenshot of its text.
        eased = self._ease.valueForProgress(float(progress))
        left = round(self._source_list.x() + (self._target_list.x()-self._source_list.x())*eased)
        top = round(self._source_list.y() + (self._target_list.y()-self._source_list.y())*eased)
        self._project_list.setGeometry(
            left, top, max(0, self._target_list.right()-left+1), self._target_list.height(),
        )

    def start(self):
        self.show()
        self.raise_()
        self._clock.start()

    def stop(self, *_):
        if self._stopped:
            return
        self._stopped = True
        self._clock.stop()
        enabled = self._page.updatesEnabled()
        self._page.setUpdatesEnabled(False)
        try:
            self._body.removeEventFilter(self)
            self._page.removeEventFilter(self)
            self._project_list.viewport().removeEventFilter(self)
            self._project_list.verticalScrollBar().valueChanged.disconnect(self._sync_visible_rows)
            self._project_list.model().modelAboutToBeReset.disconnect(self.stop)
            self._project_list.model().rowsAboutToBeRemoved.disconnect(self.stop)
            for layout in self._frozen_rows:
                layout.setEnabled(True)
                layout.invalidate()
            for widget, placeholder, index, _ in self._cards:
                self._drives_layout.removeWidget(placeholder)
                self._drives_layout.insertWidget(index, widget, 0, Qt.AlignVCenter)
                widget.show()
                placeholder.hide()
                placeholder.deleteLater()
            self._drives_layout.activate()
            self._body_layout.setEnabled(True)
            self._body_layout.activate()
            for layout in self._frozen_rows:
                layout.activate()
            self._frozen_rows.clear()
            self.hide()
        finally:
            self._page.setUpdatesEnabled(enabled)
        self.finished.emit()
        self.deleteLater()
