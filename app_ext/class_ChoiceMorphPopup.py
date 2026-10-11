# SPDX-License-Identifier: MPL-2.0
from PySide6.QtCore import QEvent, QEventLoop, QPoint, QRect, Qt, Signal, Property, QPropertyAnimation, QEasingCurve
from PySide6.QtGui import QColor, QRegion
from PySide6.QtWidgets import (
    QApplication, QWidget, QPushButton, QLabel, QVBoxLayout,
    QScrollArea, QFrame, QSizePolicy, QGraphicsOpacityEffect,
)

from app_ext.class_BlurOverlay import BlurCard
from app_ext.class_ButtonPressAnimation import reset_button_press_feedback
from main_ext.class_ThemeUi import theme


class ChoiceMorphPopup(QWidget):
    closed = Signal()

    def __init__(self, root, anchor, choices, current):
        super().__init__(root, Qt.Popup | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint)
        self.setObjectName("choiceMorphPopup")
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self._root, self._anchor = root, anchor
        self._progress = 0.0
        self._closing = False
        self._finalizing = False
        self._closed = False
        self._source_effect = None
        self.selected = None
        choices = tuple(choices)
        if not choices:
            raise ValueError("A choice popup needs at least one option")
        anchor.ensurePolished()
        reset_button_press_feedback(anchor)
        source = anchor.grab()

        border = theme._palette["_border_rgb"]
        tint = QColor(theme.BG2)
        tint.setAlpha(128)
        self.card = BlurCard(self, radius=18, tint=tint, border=QColor(*border, 46), corner=6)
        self.card.setObjectName("choiceMorphCard")
        self._body = QWidget(self.card)
        self._body.setStyleSheet("background: transparent; border: none;")
        layout = QVBoxLayout(self._body)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(3)

        scroll = QScrollArea(self._body)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Ignored)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setStyleSheet(theme.app_stylesheet() + "\nQScrollArea { background: transparent; border: none; }")
        scroll.viewport().setStyleSheet("background: transparent; border: none;")
        options = QWidget()
        options.setStyleSheet("background: transparent; border: none;")
        options_layout = QVBoxLayout(options)
        options_layout.setContentsMargins(0, 0, 0, 0)
        options_layout.setSpacing(1)
        self.buttons = []
        self._current_button = None
        for code, label in choices:
            active = code == current
            button = QPushButton(label, options)
            button.setObjectName("choiceMorphOption")
            button.setFixedHeight(32)
            button.setAutoDefault(False)
            button.setCursor(Qt.PointingHandCursor)
            button.setStyleSheet(f"""
                QPushButton {{
                    background: {theme.accent(0.12) if active else "transparent"};
                    border: none; border-radius: 6px; outline: none;
                    color: {theme.FG_BLUE if active else theme.FG};
                    font-size: 12px; font-weight: {600 if active else 400};
                    text-align: left; padding: 0 14px;
                }}
                QPushButton:hover {{ background: {theme.accent(0.14)}; color: {theme.FG_BRIGHT}; }}
                QPushButton:pressed {{ background: {theme.border_color(0.08)}; }}
            """)
            button.clicked.connect(lambda checked=False, value=code: self._pick(value))
            options_layout.addWidget(button)
            self.buttons.append(button)
            if active:
                self._current_button = button
        scroll.setWidget(options)

        # Align one edge with the source and grow into the side with more room.
        anchor_rect = QRect(anchor.mapToGlobal(QPoint()), anchor.size())
        available = QRect(root.mapToGlobal(QPoint()), root.size())
        screen = anchor.screen()
        if screen is not None:
            available = available.intersected(screen.availableGeometry())
        width = min(available.width(), max(anchor.width(), 140, options.sizeHint().width() + 8))
        desired_height = 8 + options.sizeHint().height()
        below = available.bottom() - anchor_rect.top() + 1
        above = anchor_rect.bottom() - available.top() + 1
        self._upward = desired_height > below and above > below
        height = min(desired_height, above if self._upward else below)
        x = max(available.left(), min(anchor_rect.right() - width + 1, available.right() - width + 1))
        y = anchor_rect.bottom() - height + 1 if self._upward else anchor_rect.top()
        self.setGeometry(x, y, max(1, width), max(1, height))
        self._source_rect = anchor_rect.translated(-self.pos())
        self._target_rect = self.rect()
        self._body.setFixedSize(self.size())
        layout.addWidget(scroll, 1)
        layout.activate()

        # Only the small source image fades. Live option text is never scaled.
        self._source_caption = QLabel(self)
        self._source_caption.setAttribute(Qt.WA_TransparentForMouseEvents)
        self._source_caption.setStyleSheet("background: transparent; border: none;")
        self._source_caption.setPixmap(source)
        self._source_caption.setGeometry(self._source_rect)
        self._caption_effect = QGraphicsOpacityEffect(self._source_caption)
        self._source_caption.setGraphicsEffect(self._caption_effect)
        self._animation = QPropertyAnimation(self, b"progress", self)
        self._animation.setEasingCurve(QEasingCurve.OutCubic)
        self._animation.finished.connect(self._animation_finished)
        self._set_progress(0)

    def _set_progress(self, value):
        self._progress = float(value)
        start, end = self._source_rect, self._target_rect
        rect = QRect(*(
            round(a + (b - a) * value)
            for a, b in zip((start.x(), start.y(), start.width(), start.height()),
                            (end.x(), end.y(), end.width(), end.height()))
        ))
        self.card.setGeometry(rect)
        self.card._corner = 6 + 4 * value
        self.card.update()
        self._body.move(0, rect.height() - self.height() if self._upward else 0)
        # Clip the fixed-size contents instead of resizing/scaling their text.
        clip = QRect(0, -self._body.y(), rect.width(), rect.height()).adjusted(4, 4, -4, -4)
        self._body.setMask(QRegion(clip))
        self._body.setVisible(value >= 0.30)
        self._caption_effect.setOpacity(max(0.0, 1 - value / 0.30))

    progress = Property(float, lambda self: self._progress, _set_progress)

    def exec(self):
        # The popup can be deleted inside a nested file/test/event loop. Keep
        # its wait loop independent so closing cannot delete a running loop.
        loop = QEventLoop()
        self.closed.connect(loop.quit)
        self.destroyed.connect(loop.quit)
        if self._anchor.graphicsEffect() is None:
            self._source_effect = QGraphicsOpacityEffect(self._anchor)
            self._source_effect.setOpacity(0)
            self._anchor.setGraphicsEffect(self._source_effect)
        self.card.setGeometry(self._target_rect)
        self.card.freeze_from_widget(self._root, self.pos())
        self.card.pin_backdrop_to(self.pos())
        self._set_progress(0 if theme.animations_enabled else 1)
        QApplication.instance().installEventFilter(self)
        self.show()
        if self._closed:
            return self.selected
        if theme.animations_enabled:
            self._animate(1, 240)
        else:
            self._focus_current()
        if not self._closed:
            loop.exec()
        return self.selected

    def _animate(self, target, duration):
        self._animation.stop()
        self._animation.setStartValue(self._progress)
        self._animation.setEndValue(target)
        self._animation.setDuration(duration)
        self._animation.start()

    def _animation_finished(self):
        if self._closing:
            self._finalizing = True
            self.close()
        else:
            self._focus_current()

    def _focus_current(self):
        button = self._current_button or (self.buttons[0] if self.buttons else None)
        if button is not None:
            button.setFocus(Qt.PopupFocusReason)
            scroll = self._body.findChild(QScrollArea)
            scroll.ensureWidgetVisible(button)

    def _pick(self, code):
        if not self._closing:
            self.selected = code
            self.close()

    def closeEvent(self, event):
        if (not self._finalizing and self.isVisible() and self._anchor.isVisible()
                and theme.animations_enabled and self._progress > 0):
            event.ignore()
            if not self._closing:
                self._closing = True
                for button in self.buttons:
                    reset_button_press_feedback(button)
                self._animate(0, max(60, round(180 * self._progress)))
            return
        super().closeEvent(event)

    def _restore_anchor(self):
        if self._source_effect is not None:
            try:
                if self._anchor.graphicsEffect() is self._source_effect:
                    self._anchor.setGraphicsEffect(None)
            except RuntimeError:
                pass  # The root window can destroy its anchor while closing.
            self._source_effect = None

    def hideEvent(self, event):
        self._animation.stop()
        self._restore_anchor()
        QApplication.instance().removeEventFilter(self)
        super().hideEvent(event)
        if not self._closed:
            self._closed = True
            self.closed.emit()
            self.deleteLater()

    def eventFilter(self, watched, event):
        kind = event.type()
        if watched is self._root and kind in (QEvent.Hide, QEvent.Close, QEvent.Move, QEvent.Resize):
            self._finalizing = True
            self.close()
        elif kind == QEvent.MouseButtonPress and self.isVisible():
            point = self.mapFromGlobal(event.globalPosition().toPoint())
            if not self.card.geometry().contains(point):
                self.close()
                return True
        elif kind == QEvent.KeyPress and isinstance(watched, QWidget) and watched.window() is self:
            if event.key() == Qt.Key_Escape:
                self.close()
                return True
            if self._closing:
                return True
            if event.key() in (Qt.Key_Up, Qt.Key_Down, Qt.Key_Home, Qt.Key_End):
                focused = QApplication.focusWidget()
                index = self.buttons.index(focused) if focused in self.buttons else 0
                if event.key() == Qt.Key_Home:
                    index = 0
                elif event.key() == Qt.Key_End:
                    index = len(self.buttons) - 1
                else:
                    index = (index + (1 if event.key() == Qt.Key_Down else -1)) % len(self.buttons)
                self.buttons[index].setFocus(Qt.TabFocusReason)
                self._body.findChild(QScrollArea).ensureWidgetVisible(self.buttons[index])
                return True
            if event.key() in (Qt.Key_Return, Qt.Key_Enter):
                focused = QApplication.focusWidget()
                if focused in self.buttons:
                    focused.click()
                else:
                    self.close()
                return True
        return False
