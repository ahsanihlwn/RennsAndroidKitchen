# SPDX-License-Identifier: MPL-2.0
import os
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QLineEdit, QListWidget, QListWidgetItem, QFrame
)
from PySide6.QtCore import Qt, QThread, Signal, QTimer
from PySide6.QtGui import QIcon, QColor
from main_ext.class_ThemeUi import theme as _th
from icons.handler import icon

def expand_and_select_path(tree, full_path):
    root_item = tree.topLevelItem(0)
    if not root_item:
        return

    root_path = root_item.data(0, Qt.UserRole)
    if not root_path:
        return

    full_norm = os.path.normcase(os.path.normpath(full_path))
    root_norm = os.path.normcase(os.path.normpath(root_path))

    if not full_norm.startswith(root_norm):
        return

    rel_path = os.path.relpath(full_path, root_path)
    if rel_path == ".":
        tree.setCurrentItem(root_item)
        return

    parts = rel_path.split(os.sep)
    current_item = root_item

    for part in parts:
        if current_item.childCount() == 1 and current_item.child(0).text(0) == "Empty":
            tree.on_expand(current_item)
        current_item.setExpanded(True)

        found = False
        for i in range(current_item.childCount()):
            child = current_item.child(i)
            if child.text(0).lower() == part.lower():
                current_item = child
                found = True
                break
        if not found:
            return

    tree.setCurrentItem(current_item)
    tree.scrollToItem(current_item)

class SearchWorker(QThread):
    result_found = Signal(str, str, bool) # name, full_path, is_dir
    search_finished = Signal(str)

    def __init__(self, start_dir, exclude_dirs, query):
        super().__init__()
        self.start_dir = start_dir

        self.exclude_dirs = set(os.path.abspath(d) for d in exclude_dirs)
        self.query = query.lower()
        self._is_running = True
        self._found_count = 0
        self.max_results = 100 # Increased to 100

    def run(self):
        self._search_dir(self.start_dir)
        self.search_finished.emit(self.start_dir)

    def _search_dir(self, current_dir):
        if not self._is_running or self._found_count >= self.max_results:
            return

        try:
            with os.scandir(current_dir) as it:
                for entry in it:
                    if not self._is_running or self._found_count >= self.max_results:
                        return

                    is_dir = entry.is_dir()
                    full_path = entry.path

                    if is_dir:
                        if os.path.abspath(full_path) in self.exclude_dirs:
                            continue

                    name_lower = entry.name.lower()

                    if self.query in name_lower:
                        self._found_count += 1
                        self.result_found.emit(entry.name, full_path, is_dir)

                    if is_dir:
                        self._search_dir(full_path)
        except PermissionError:
            pass
        except Exception:
            pass

    def stop(self):
        self._is_running = False

class FileSearchUI(QWidget):
    def __init__(self, tree_widget):
        super().__init__(tree_widget.window())
        self.tree = tree_widget
        self.worker = None
        self.worker2 = None

        self.setWindowFlags(Qt.Popup | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)

        from app_ext.class_BlurOverlay import BlurCard
        from PySide6.QtGui import QColor

        _br, _bg, _bb = _th._palette["_border_rgb"]
        _tint = QColor(_th.BG2)
        _tint.setAlpha(180)
        self.card = BlurCard(radius=16.0, tint=_tint, border=QColor(_br, _bg, _bb, int(0.3 * 255)))

        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.addWidget(self.card)

        self.setStyleSheet(f"""
            QLineEdit {{
                background-color: {_th.BG3};
                border: 1px solid {_th.border_color(0.3)};
                border-radius: 6px;
                color: {_th.FG_BRIGHT};
                padding: 6px 10px;
                font-size: 13px;
            }}
            QLineEdit:focus {{
                border: 1px solid {_th.accent(0.5)};
                background-color: {_th.BG_DARK};
            }}
            QListWidget {{
                background-color: transparent;
                border: none;
                color: {_th.FG_BRIGHT};
                outline: none;
            }}
            QListWidget::item {{
                padding: 2px 6px;
                border-radius: 4px;
                margin-bottom: 2px;
            }}
            QListWidget::item:selected {{
                background-color: {_th.accent(0.25)};
                color: {_th.FG_BRIGHT};
            }}
            QListWidget::item:hover {{
                background-color: {_th.border_color(0.15)};
            }}
            
            QScrollBar:vertical {{
                background: transparent; width: 6px; margin: 0; border: none;
            }}
            QScrollBar::handle:vertical {{
                background: {_th.SCROLL_HANDLE}; border-radius: 3px; min-height: 20px;
            }}
            QScrollBar::handle:vertical:hover {{ background: {_th.accent(0.45)}; }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical,
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
                height: 0; background: transparent;
            }}
            
            QScrollBar:horizontal {{
                background: transparent; height: 6px; margin: 0; border: none;
            }}
            QScrollBar::handle:horizontal {{
                background: {_th.SCROLL_HANDLE}; border-radius: 3px; min-width: 20px;
            }}
            QScrollBar::handle:horizontal:hover {{ background: {_th.accent(0.45)}; }}
            QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal,
            QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{
                width: 0; background: transparent;
            }}
        """)

        layout = QVBoxLayout(self.card)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(6)

        self.search_bar = QLineEdit()
        self.search_bar.setPlaceholderText("Search file/folder...")
        self.search_bar.textChanged.connect(self._on_text_changed)
        self.search_bar.returnPressed.connect(self._on_enter)

        self.list_widget = QListWidget()
        self.list_widget.itemClicked.connect(self._on_item_clicked)
        self.list_widget.itemActivated.connect(self._on_item_clicked)
        self.list_widget.itemDoubleClicked.connect(self._on_item_clicked)
        self.list_widget.setIconSize(self.tree.iconSize())

        layout.addWidget(self.search_bar)
        layout.addWidget(self.list_widget)

        self.timer = QTimer()
        self.timer.setSingleShot(True)
        self.timer.setInterval(300)
        self.timer.timeout.connect(self._do_search)

        self.hide()

    def show_search(self):
        self.resize(350, 450)

        tree_global = self.tree.mapToGlobal(self.tree.rect().topLeft())
        x = tree_global.x() + (self.tree.width() - self.width()) // 2
        y = tree_global.y() + (self.tree.height() - self.height()) // 2

        from PySide6.QtCore import QPoint
        pos = QPoint(max(0, x), max(0, y))
        self.move(pos)

        self.card.freeze(self.tree.window(), pos)

        self.show()
        self.raise_()
        self.search_bar.setFocus()
        self.search_bar.selectAll()

    def _on_text_changed(self, text):
        self.timer.stop()
        self.timer.start()

    def _add_separator(self):
        item = QListWidgetItem()
        item.setFlags(Qt.NoItemFlags)

        from PySide6.QtWidgets import QFrame
        from PySide6.QtCore import QSize
        from main_ext.class_ThemeUi import theme as _th

        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setFrameShadow(QFrame.Sunken)
        line.setStyleSheet(f"background-color: {_th.border_color(0.2)};")
        line.setFixedHeight(1)

        item.setSizeHint(QSize(0, 10)) # 10px height for spacing

        self.list_widget.addItem(item)
        self.list_widget.setItemWidget(item, line)

    def _do_search(self):
        query = self.search_bar.text().strip()
        self.list_widget.clear()

        self._global_separator_added = False

        if self.worker:
            self.worker.stop()
            self.worker.wait()
            self.worker = None

        if self.worker2:
            self.worker2.stop()
            self.worker2.wait()
            self.worker2 = None

        if not query:
            return

        priority_dir = None
        current_item = self.tree.currentItem()
        if current_item:
            path = current_item.data(0, Qt.UserRole)
            if path and os.path.exists(path):
                if os.path.isdir(path):
                    priority_dir = os.path.abspath(path)
                else:
                    priority_dir = os.path.abspath(os.path.dirname(path))

        # Dapatkan path asli tree (bisa fallback ke self.tree.base)
        root_item = self.tree.topLevelItem(0)
        root_dir = root_item.data(0, Qt.UserRole) if root_item else None
        if not root_dir and hasattr(self.tree, 'base'):
            root_dir = self.tree.base

        if root_dir:
            self.root_dir = os.path.abspath(root_dir)
        else:
            self.root_dir = None

        if not self.root_dir:
            return

        # Gunakan normcase supaya aman terhadap perbedaan case huruf drive di Windows
        is_priority_valid = False
        if priority_dir:
            p_dir_norm = os.path.normcase(priority_dir)
            r_dir_norm = os.path.normcase(self.root_dir)
            if p_dir_norm.startswith(r_dir_norm):
                is_priority_valid = True

        if is_priority_valid:
            self.worker = SearchWorker(priority_dir, [], query)
            self.worker.result_found.connect(self._on_priority_result)
            self.worker.search_finished.connect(self._on_priority_finished)
            self.worker.start()
        else:
            self._start_global_search([])

    def _on_priority_finished(self, priority_dir):
        # Mulai worker 2 dengan exclude_dirs = [priority_dir] yang sudah dicari
        self._start_global_search([priority_dir])

    def _start_global_search(self, exclude_dirs):
        if not self.root_dir:
            return
        self.worker2 = SearchWorker(self.root_dir, exclude_dirs, self.search_bar.text().strip())
        self.worker2.result_found.connect(self._on_global_result)
        self.worker2.start()

    def _on_priority_result(self, name, full_path, is_dir):
        self._on_result(name, full_path, is_dir)

    def _on_global_result(self, name, full_path, is_dir):
        # Insert separator if this is the first global result AND there are priority results
        if not self._global_separator_added:
            if self.list_widget.count() > 0:
                self._add_separator()
            self._global_separator_added = True
        self._on_result(name, full_path, is_dir)

    def _on_result(self, name, full_path, is_dir):
        query = self.search_bar.text().strip().lower()

        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            if item.data(Qt.UserRole) == full_path:
                return

        item = QListWidgetItem()
        item.setText(name + ("/" if is_dir else ""))
        item.setData(Qt.UserRole, full_path)
        item.setToolTip(full_path)

        try:
            if is_dir:
                item.setIcon(self.tree._resolve_folder_icon(full_path, ""))
            else:
                item.setIcon(self.tree._resolve_file_icon(full_path))
        except Exception:
            try:
                if is_dir:
                    item.setIcon(QIcon(icon("folder")))
                else:
                    item.setIcon(QIcon(icon("file")))
            except Exception:
                pass

        self.list_widget.addItem(item)

    def _on_item_clicked(self, item):
        path = item.data(Qt.UserRole)
        if not path:
            return # Ignore clicks on separator
        expand_and_select_path(self.tree, path)
        self.hide()

    def _on_enter(self):
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            if item.data(Qt.UserRole): # skip separator
                self._on_item_clicked(item)
                return

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.hide()
            self.tree.setFocus()
            event.accept()
        elif event.key() == Qt.Key_Down:
            self.list_widget.setFocus()
            # Select first actual item
            for i in range(self.list_widget.count()):
                if self.list_widget.item(i).data(Qt.UserRole):
                    self.list_widget.setCurrentRow(i)
                    break
            event.accept()
        elif event.key() == Qt.Key_Up:
            self.list_widget.setFocus()
            # Select last actual item
            for i in range(self.list_widget.count()-1, -1, -1):
                if self.list_widget.item(i).data(Qt.UserRole):
                    self.list_widget.setCurrentRow(i)
                    break
            event.accept()
        else:
            super().keyPressEvent(event)

    def hideEvent(self, event):
        if self.worker:
            self.worker.stop()
        if self.worker2:
            self.worker2.stop()
        super().hideEvent(event)
