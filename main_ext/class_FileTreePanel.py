# SPDX-License-Identifier: MPL-2.0
import os

from PySide6.QtCore import Qt, QTimer, QSize
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QSizePolicy

from icons.handler import icon
from main_ext.class_ThemeUi import theme as _th


class FileTreePanel(QWidget):
    def __init__(self, tree, parent=None):
        super().__init__(parent)
        self.tree = tree
        self._project_name = ""
        self.setObjectName("fileTreePanel")
        self.setAttribute(Qt.WA_StyledBackground, True)

        layout = QVBoxLayout(self)
        # Keep the square tree viewport clear of the panel's rounded bottom edge.
        layout.setContentsMargins(1, 1, 1, 11)
        layout.setSpacing(0)

        self.header = QWidget()
        self.header.setObjectName("fileTreePanelHeader")
        self.header.setContextMenuPolicy(Qt.CustomContextMenu)
        self.header.customContextMenuRequested.connect(self._show_root_menu)
        header_layout = QHBoxLayout(self.header)
        header_layout.setContentsMargins(14, 6, 13, 6)
        header_layout.setSpacing(9)

        self.folder_icon = QLabel()
        self.folder_icon.setFixedSize(24, 24)
        header_layout.addWidget(self.folder_icon)

        self.title = QLabel()
        self.title.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        header_layout.addWidget(self.title, 1)

        self.count = QLabel("0 items")
        self.count.setObjectName("fileTreeItemCount")
        self.count.setAlignment(Qt.AlignCenter)
        header_layout.addWidget(self.count)

        self._scroll_corner = QWidget()
        self._scroll_corner.setObjectName("fileTreeScrollCorner")
        tree.setCornerWidget(self._scroll_corner)

        layout.addWidget(self.header)
        layout.addWidget(tree, 1)

        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.timeout.connect(self.refresh_metadata)
        model = tree.model()
        model.rowsInserted.connect(lambda *_: self._refresh_timer.start(0))
        model.rowsRemoved.connect(lambda *_: self._refresh_timer.start(0))
        model.modelReset.connect(lambda *_: self._refresh_timer.start(0))

        self.refresh_theme()
        self.refresh_metadata()

    def refresh_theme(self):
        # Qt paints both scrollbar containers separately from the viewport.
        for scrollbar in (self.tree.horizontalScrollBar(), self.tree.verticalScrollBar()):
            scroll_container = scrollbar.parentWidget()
            if scroll_container is not None:
                scroll_container.setStyleSheet(
                    f"QWidget#{scroll_container.objectName()} {{ background: {_th.BG3}; }}"
                )
        self._scroll_corner.setStyleSheet(
            f"QWidget#fileTreeScrollCorner {{ background: {_th.BG3}; }}"
        )
        self.setStyleSheet(f"""
            QWidget#fileTreePanel {{
                background: {_th.BG3};
                border: 1px solid {_th.border_color(0.13)};
                border-radius: 10px;
            }}
            QWidget#fileTreePanelHeader {{
                background: transparent;
                border: none;
                border-bottom: 1px solid {_th.border_color(0.08)};
            }}
            QWidget#fileTreePanelHeader QLabel {{
                background: transparent;
                border: none;
            }}
            QWidget#fileTreePanelHeader QLabel#fileTreeItemCount {{
                background: {_th.BG4};
                border-radius: 10px;
                color: {_th.FG2};
                font-size: 10px;
                font-weight: 600;
                padding: 3px 8px;
            }}
        """)
        self.title.setStyleSheet(f"color: {_th.FG_BRIGHT}; font-size: 13px; font-weight: 600;")
        self.folder_icon.setPixmap(QIcon(icon("project_ic")).pixmap(QSize(24, 24)))

    def refresh_metadata(self):
        base = self.tree.base
        name = os.path.basename(os.path.normpath(base)) if base else ""
        if name != self._project_name:
            self._project_name = name
            self.title.setToolTip(name)
        self._fit_title()

        if self.tree.display_mode == "single_dir":
            items = (self.tree.topLevelItem(i) for i in range(self.tree.topLevelItemCount()))
        else:
            root = self.tree.topLevelItem(0)
            items = (root.child(i) for i in range(root.childCount())) if root else ()
        count = sum(1 for item in items if item.data(0, Qt.UserRole))
        self.count.setText(f"{count} items")

    def _fit_title(self):
        available = max(0, self.title.width())
        self.title.setText(self.title.fontMetrics().elidedText(
            self._project_name, Qt.ElideRight, available,
        ))

    def _show_root_menu(self, pos):
        viewport_pos = self.tree.viewport().mapFromGlobal(self.header.mapToGlobal(pos))
        self.tree.open_context_menu(viewport_pos)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit_title()
