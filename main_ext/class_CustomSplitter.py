# SPDX-License-Identifier: MPL-2.0
import sys, os, re, shutil
from core.fsdetect import detect_fs_type
from core.erofs import unpack_erofs, repack_erofs
import json
from PySide6.QtCore import QEvent
import time
from core.threader import run_in_thread
from core.loading import LoadingAnimator
from core.code_editor import CodeHighlighter
from core.fsdetect import detect_fs_root
from PySide6.QtCore import Signal
from core.terminal import Terminal
from PySide6.QtWidgets import (
    QApplication,
    QWidget,
    QVBoxLayout,
    QTreeWidget,
    QTreeWidgetItem,
    QHeaderView,
    QTabWidget,
    QPlainTextEdit,
    QMenu,
    QFileDialog,
    QMessageBox,
    QSplitter,
    QSplitterHandle,
    QTabBar,
    QDialog,
    QHBoxLayout,
    QCheckBox,
    QLabel,
    QPushButton,
    QListWidget,
    QListWidgetItem,
    QTextEdit,
    QScrollArea
)
from PySide6.QtCore import QTimer
from PySide6.QtGui import (
    QIcon,
    QPainter,
    QColor,
    QAction,
    QKeySequence,
    QTextCursor,
    QCursor
)

from PySide6.QtCore import (
    Qt,
    QRect,
    QSize,
    QFileSystemWatcher
)
from main_ext.class_ThemeUi import theme as _th

class CleanSplitterHandle(QSplitterHandle):
    def __init__(self, orientation, parent):
        super().__init__(orientation, parent)
        self._hovered = False
        self.setAttribute(Qt.WA_Hover, True)

    def enterEvent(self, event):
        self._hovered = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hovered = False
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), QColor(_th.BG))

        if self._hovered:
            hex_ = _th.accent_hex.lstrip("#")
            r, g, b = int(hex_[0:2], 16), int(hex_[2:4], 16), int(hex_[4:6], 16)
            color = QColor(r, g, b, 220)
        else:
            color = QColor(140, 140, 150, 90)

        p.setPen(Qt.NoPen)
        p.setBrush(color)

        dot_r = 2
        gap = 5
        cx = self.width() // 2
        cy = self.height() // 2

        if self.orientation() == Qt.Horizontal:
            # vertical split → dots stacked vertically
            for dy in (-gap, 0, gap):
                p.drawEllipse(cx - dot_r, cy + dy - dot_r, dot_r * 2, dot_r * 2)
        else:
            # horizontal split → dots in a row
            for dx in (-gap, 0, gap):
                p.drawEllipse(cx + dx - dot_r, cy - dot_r, dot_r * 2, dot_r * 2)

class CustomSplitter(QSplitter):
    def __init__(self, orientation=Qt.Horizontal, parent=None):
        super().__init__(orientation, parent)

    def createHandle(self):
        # QSplitter.createHandle signature expects no args; return a QSplitterHandle subclass
        return CleanSplitterHandle(self.orientation(), self)

