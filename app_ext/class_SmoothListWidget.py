# SPDX-License-Identifier: MPL-2.0
import os, sys, json, subprocess
from PySide6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QLineEdit, QPushButton, QFileDialog, QListWidget,
    QListWidgetItem, QMessageBox, QStackedWidget, QRadioButton,
    QGroupBox, QFormLayout, QScrollArea, QSizePolicy
)

from PySide6.QtWidgets import QFrame, QDialog, QStackedLayout
from PySide6.QtGui import QTransform
from PySide6.QtWidgets import QGridLayout
from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QIcon
from core.flag import window_title
from PySide6.QtCore import QPropertyAnimation, QEasingCurve, QTimer
from PySide6.QtWidgets import QMenu, QProgressBar
from PySide6.QtCore import Signal
from PySide6.QtGui import QColor, QPainter

class SmoothListWidget(QListWidget):
    scrollingChanged = Signal(bool)
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.setVerticalScrollMode(QListWidget.ScrollPerPixel)

        self._target_scroll = 0
        self._anim = QPropertyAnimation(self.verticalScrollBar(), b"value", self)
        self._anim.setDuration(220)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)

        self._scroll_timer = QTimer(self)
        self._scroll_timer.setSingleShot(True)
        self._scroll_timer.timeout.connect(self._apply_scroll)
        self._scrolling = False
        self._viewport_background = None
        self._anim.finished.connect(self._finish_scroll)
        self._idle_timer = QTimer(self)
        self._idle_timer.setSingleShot(True)
        self._idle_timer.timeout.connect(self._finish_scroll)

    def set_viewport_background(self, color):
        self._viewport_background = QColor(color)
        self.viewport().setAttribute(Qt.WA_OpaquePaintEvent)
        self.viewport().update()

    def paintEvent(self, event):
        if self._viewport_background is not None:
            painter = QPainter(self.viewport())
            painter.fillRect(event.rect(), self._viewport_background)
            painter.end()
        super().paintEvent(event)

    def _begin_scroll(self):
        if not self._scrolling:
            self._scrolling = True
            self.scrollingChanged.emit(True)

    def _finish_scroll(self):
        if self._scrolling:
            self._scrolling = False
            self.scrollingChanged.emit(False)

    def wheelEvent(self, event):
        self._begin_scroll()
        pixels = event.pixelDelta().y()
        if pixels:
            self._anim.stop()
            self._scroll_timer.stop()
            bar = self.verticalScrollBar()
            bar.setValue(bar.value() - pixels)
            self._target_scroll = bar.value()
            self._idle_timer.start(80)
            event.accept()
            return
        delta = event.angleDelta().y()
        if not delta:
            self._finish_scroll()
            event.ignore()
            return

        # KONVERSI KE PIXEL (INI KUNCI)
        pixel_delta = delta / 120 * 40   # 40px per notch (atur feel di sini)

        bar = self.verticalScrollBar()
        if not self._anim.state() == QPropertyAnimation.Running and not self._scroll_timer.isActive():
            self._target_scroll = bar.value()

        self._target_scroll = max(
            bar.minimum(),
            min(bar.maximum(), self._target_scroll - pixel_delta)
        )

        self._scroll_timer.start(0)
        event.accept()

    def _apply_scroll(self):
        bar = self.verticalScrollBar()

        self._anim.stop()
        self._anim.setStartValue(bar.value())
        self._anim.setEndValue(int(self._target_scroll))
        self._anim.start()
