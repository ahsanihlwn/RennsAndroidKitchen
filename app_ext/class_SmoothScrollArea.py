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

    def wheelEvent(self, event):
        delta = event.angleDelta().y()
        if delta == 0:
            return

        pixel_delta = delta / 120 * 40  # SAMA PERSIS kayak SmoothListWidget

        bar = self.verticalScrollBar()

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
