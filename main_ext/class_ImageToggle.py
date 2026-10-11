# SPDX-License-Identifier: MPL-2.0
from icons.handler import icon
from PySide6.QtWidgets import (
    QApplication,
    QWidget,
    QFrame,
    QComboBox,
    QVBoxLayout,
    QSlider,
    QSizePolicy,
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
from PySide6.QtGui import (
    QIcon,
    QPixmap,
    QPainter,
    QColor
)

from PySide6.QtCore import (
    Qt,
    QRect,
    QSize
)
from main_ext.class_ThemeUi import theme as _th

class ImageToggle(QPushButton):
    def __init__(self, checked=False, size=QSize(35, 19)):
        super().__init__()
        self.setCheckable(True)
        self.setChecked(checked)
        self.setFixedSize(size)

        # MATIIN SEMUA INTERAKSI VISUAL BUTTON
        self.setFocusPolicy(Qt.NoFocus)
        self.setAttribute(Qt.WA_Hover, False)
        self.setCursor(Qt.PointingHandCursor)

        self._pix = QPixmap(icon("toggle"))
        self.clicked.connect(self.update)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)

        # background = card (theme-aware dark/light)
        painter.fillRect(self.rect(), QColor(_th.BG_DARK))

        full_w = self._pix.width()
        full_h = self._pix.height()
        half_h = full_h // 2

        # ON = atas, OFF = bawah  ← SESUAI GAMBAR
        y = 0 if self.isChecked() else half_h

        cropped = self._pix.copy(QRect(0, y, full_w, half_h))

        painter.drawPixmap(
            self.rect(),
            cropped.scaled(
                self.size(),
                Qt.IgnoreAspectRatio,
                Qt.SmoothTransformation
            )
        )
