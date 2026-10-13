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
import shutil

class SmoothScrollArea(QScrollArea):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        bar = self.verticalScrollBar()
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        self._target_scroll = bar.value()
        self._anim = QPropertyAnimation(bar, b"value", self)
        self._anim.setDuration(220)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)

        self._scroll_timer = QTimer(self)
        self._scroll_timer.setSingleShot(True)
        self._scroll_timer.timeout.connect(self._apply_scroll)
        bar.rangeChanged.connect(self._reset_scroll)

    def _reset_scroll(self, *_):
        self._scroll_timer.stop()
        self._anim.stop()
        self._target_scroll = self.verticalScrollBar().value()

    def hideEvent(self, event):
        self._reset_scroll()
        super().hideEvent(event)

    def wheelEvent(self, event):
        bar = self.verticalScrollBar()
        pixels = event.pixelDelta().y()
        if pixels:
            self._reset_scroll()
            before = bar.value()
            bar.setValue(before - pixels)
            self._target_scroll = bar.value()
            event.setAccepted(bar.value() != before)
            return
        delta = event.angleDelta().y()
        if delta == 0:
            super().wheelEvent(event)
            return

        pixel_delta = delta / 120 * 40  # SAMA PERSIS kayak SmoothListWidget

        if self._anim.state() != QPropertyAnimation.Running and not self._scroll_timer.isActive():
            self._target_scroll = bar.value()

        self._target_scroll = max(
            bar.minimum(),
            min(bar.maximum(), self._target_scroll - pixel_delta)
        )

        if self._target_scroll == bar.value():
            event.ignore()
            return
        from main_ext.class_ThemeUi import theme
        if theme.animations_enabled:
            self._scroll_timer.start(0)
        else:
            self._reset_scroll()
            bar.setValue(int(max(bar.minimum(), min(bar.maximum(), bar.value() - pixel_delta))))
            self._target_scroll = bar.value()
        event.accept()

    def _apply_scroll(self):
        bar = self.verticalScrollBar()

        self._anim.stop()
        self._anim.setStartValue(bar.value())
        self._anim.setEndValue(int(self._target_scroll))
        self._anim.start()
