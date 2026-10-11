# SPDX-License-Identifier: MPL-2.0
import os
from main_ext.class_AppDialog import AppDialog
from main_ext.class_ThemeUi import theme as _th
from main_ext.file_run_commands import is_runnable_file
from core.ui_lang import t
from icons.handler import icon as _icon_path
from main_ext.tab_scroll_buttons import (
    ThemedScrollTabBar, configure_tab_scroll_buttons, tab_scroll_style,
)
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout,
    QStackedWidget, QTabBar, QPushButton,
    QSizePolicy, QApplication, QDialog, QLabel
)
from PySide6.QtCore import Qt, QSize, Signal, QTimer
from PySide6.QtGui import QColor, QPainter, QIcon

print("[MAIN STAGE] UNIFIED_TAB_CONTAINER")

_EDITOR_TAG = "__editor__"


def _dot_pixmap(size: int = 6) -> "QPixmap":
    from icons.handler import icon as _icon
    from PySide6.QtGui import QPixmap, QPainter
    src = QPixmap(_icon("dot")).scaled(size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    out = QPixmap(src.size())
    out.fill(Qt.transparent)
    p = QPainter(out)
    p.fillRect(out.rect(), QColor(_th.accent_hex))
    p.setCompositionMode(QPainter.CompositionMode_DestinationIn)
    p.drawPixmap(0, 0, src)
    p.end()
    return out


def _icon_with_dot(base_icon: "QIcon", icon_size: int = 16, dot_size: int = 8) -> "QIcon":
    from PySide6.QtGui import QPixmap, QPainter, QIcon
    base = base_icon.pixmap(icon_size, icon_size)
    if base.isNull():
        base = QPixmap(icon_size, icon_size)
        base.fill(Qt.transparent)
    out = QPixmap(base)
    dot = _dot_pixmap(dot_size)
    p = QPainter(out)
    # dot overlap di pojok kiri-bawah icon, tetap dalam canvas 16x16
    p.drawPixmap(0, icon_size - dot_size, dot)
    p.end()
    return QIcon(out)


class _UnsavedDialog(QDialog):
    def __init__(self, filename: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Close Tab")
        self.setModal(True)
        self.setFixedSize(320, 126)
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        box = QWidget()
        box.setObjectName("dlg_box")
        # Hanya #dlg_box yang dapat border/background — tidak cascade ke children
        box.setStyleSheet(f"""
            QWidget#dlg_box {{
                background: {_th.BG3};
                border: 1px solid {_th.border_color(0.09)};
                border-radius: 10px;
            }}
        """)
        outer.addWidget(box)

        lay = QVBoxLayout(box)
        lay.setContentsMargins(20, 18, 20, 16)
        lay.setSpacing(14)

        msg = QLabel(t("utc_unsaved_msg", filename=filename))
        msg.setStyleSheet(f"color: {_th.FG}; font-size: 13px; border: none; background: transparent;")
        msg.setWordWrap(True)
        lay.addWidget(msg)

        btns = QHBoxLayout()
        btns.setSpacing(8)
        btns.addStretch()

        cancel_btn = QPushButton(t("utc_btn_cancel"))
        cancel_btn.setFixedSize(80, 30)
        cancel_btn.setCursor(Qt.PointingHandCursor)
        cancel_btn.setStyleSheet(f"""
            QPushButton {{
                background: {_th.border_color(0.06)};
                border: 1px solid {_th.border_color(0.10)};
                border-radius: 6px;
                color: {_th.FG3};
                font-size: 12px;
            }}
            QPushButton:hover {{ background: {_th.border_color(0.10)}; color: {_th.FG_HOVER}; }}
        """)
        cancel_btn.clicked.connect(self.reject)

        close_btn = QPushButton(t("utc_btn_close"))
        close_btn.setFixedSize(80, 30)
        close_btn.setCursor(Qt.PointingHandCursor)
        close_btn.setStyleSheet(f"""
            QPushButton {{
                background: {_th.red(0.18)};
                border: 1px solid {_th.red(0.38)};
                border-radius: 6px;
                color: {_th.RED_LIGHT};
                font-size: 12px;
                font-weight: 600;
            }}
            QPushButton:hover {{
                background: {_th.red(0.32)};
                border-color: {_th.red(0.60)};
                color: {_th.RED_LIGHTER};
            }}
            QPushButton:pressed {{ background: {_th.red(0.48)}; }}
        """)
        close_btn.clicked.connect(self.accept)

        btns.addWidget(cancel_btn)
        btns.addWidget(close_btn)
        lay.addLayout(btns)


class UnifiedTabContainer(QWidget):
    allClosed       = Signal()
    editorAllClosed = Signal()
    fileSaved       = Signal(str)
    runFileRequested = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)

        from main_ext.class_WebEditorPanel import WebEditorPanel

        self._editor_tabs  = {}
        self._aux_widgets  = []

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        tab_bar_wrap = QWidget()
        tab_bar_wrap.setObjectName("editorTabBarWrap")
        tab_bar_wrap.setFixedHeight(34)


        tab_bar_wrap.setStyleSheet(f"""
            QWidget#editorTabBarWrap {{
                background: {_th.BG_DARK};
                border-bottom: 1px solid {_th.border_color(0.06)};
                border-top-left-radius: 4px;
                border-top-right-radius: 4px;
            }}
        """)
        tab_bar_layout = QHBoxLayout(tab_bar_wrap)
        tab_bar_layout.setContentsMargins(0, 0, 0, 0)
        tab_bar_layout.setSpacing(0)

        self._tab_bar = ThemedScrollTabBar()
        self._tab_bar.setDrawBase(False)
        self._tab_bar.setExpanding(False)
        self._tab_bar.setElideMode(Qt.ElideMiddle)
        self._tab_bar.setIconSize(QSize(16, 16))
        self._tab_bar.setUsesScrollButtons(True)
        self._tab_bar.setTabsClosable(False)
        self._tab_bar.setMovable(False)
        self._tab_bar.setStyleSheet(self._tab_bar_style())
        configure_tab_scroll_buttons(self._tab_bar)
        self._tab_bar.tabCloseRequested.connect(self._on_tab_close_requested)
        self._tab_bar.currentChanged.connect(self._on_tab_changed)

        tab_bar_layout.addWidget(self._tab_bar, 1)

        self._run_btn = QPushButton()
        self._run_btn.setFixedSize(30, 30)
        self._run_btn.setCursor(Qt.PointingHandCursor)
        self._run_btn.setToolTip("Run File")
        run_icon_path = _icon_path("start")
        if not os.path.isfile(run_icon_path):
            run_icon_path = os.path.join("icons", "start.svg")
        self._run_btn.setIcon(QIcon(run_icon_path))
        self._run_btn.setIconSize(QSize(16, 16))
        self._run_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent;
                border: none;
                border-radius: 4px;
                padding: 0;
                margin: 0 3px 0 0;
            }}
            QPushButton:hover {{
                background: {_th.border_color(0.08)};
            }}
            QPushButton:pressed {{
                background: {_th.accent(0.16)};
            }}
        """)
        self._run_btn.clicked.connect(self._run_active_file)
        self._run_btn.hide()
        tab_bar_layout.addWidget(self._run_btn)
        root.addWidget(tab_bar_wrap)

        self._stack = QStackedWidget()
        self._stack.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        root.addWidget(self._stack, 1)

        self._editor_panel = WebEditorPanel()
        self._editor_panel.fileSaved.connect(self.fileSaved)
        self._editor_panel.bridge.editorReady.connect(self._on_monaco_ready)
        self._editor_panel.bridge.tabClosed.connect(self._on_monaco_tab_closed_from_js)
        self._editor_panel.bridge.fileUnsaved.connect(self.mark_unsaved)
        self._editor_panel.bridge.fileSaved.connect(self.mark_saved)

        self._stack.addWidget(self._editor_panel)
        self._monaco_stack_idx = 0

        self._active_filepath = None
        self._ignore_tab_change = False

    @staticmethod
    def _tab_bar_style():
        return f"""
        QTabBar {{
            background: transparent;
            border: none;
            font-family: 'Poppins', 'Inter', 'Segoe UI', 'SF Pro Display', sans-serif;
            font-size: 12px;
        }}
        QTabBar::tab {{
            background: transparent;
            color: {_th.GRAY_MID2};
            padding: 0 4px 0 12px;
            height: 31px;
            min-width: 80px;
            max-width: 200px;
            border-right: 1px solid {_th.border_color(0.04)};
            border-top: 2px solid transparent;
            margin: 0;
        }}
        QTabBar::tab:hover {{
            background: {_th.border_color(0.04)};
            color: {_th.FG2};
        }}
        QTabBar::tab:selected {{
            background: {_th.BG3};
            color: {_th.FG_BRIGHT};
            border-top: 2px solid {_th.accent_hex};
        }}
        QTabBar::close-button {{
            subcontrol-position: right;
            width: 16px;
            height: 16px;
            margin-right: 4px;
            border-radius: 3px;
        }}
        QTabBar::close-button:hover {{
            background: {_th.red(0.18)};
        }}
        {tab_scroll_style()}
        """

    def refresh_tab_bar_theme(self):
        self._tab_bar.setStyleSheet(self._tab_bar_style())
        configure_tab_scroll_buttons(self._tab_bar)

    def _on_monaco_ready(self):
        js = """
        (function() {
            var bar = document.getElementById('tab-bar');
            if (bar) bar.style.display = 'none';
        })();
        """
        self._editor_panel.view.page().runJavaScript(js)

    def _add_tab(self, title: str, is_editor: bool, data=None) -> int:
        idx = self._tab_bar.addTab(title)
        self._tab_bar.setTabData(idx, {"is_editor": is_editor, "data": data})
        self._attach_close_button(idx)
        return idx

    def _attach_close_button(self, index: int):
        meta      = self._tab_meta(index)
        is_editor = meta.get("is_editor", False)
        data      = meta.get("data")

        btn = QPushButton("×")
        btn.setFixedSize(16, 16)
        btn.setCursor(Qt.PointingHandCursor)
        btn.setStyleSheet(f"""
            QPushButton {{
                color: {_th.GRAY_MID};
                font-size: 13px;
                font-weight: bold;
                background: transparent;
                border: none;
                border-radius: 3px;
                padding: 0;
            }}
            QPushButton:hover {{
                color: {_th.RED_STRONG};
                background: {_th.red(0.18)};
            }}
            QPushButton:pressed {{
                color: {_th.RED_FLASH};
                background: {_th.red(0.30)};
            }}
        """)

        def _do_close(checked=False, _is_editor=is_editor, _data=data):
            for i in range(self._tab_bar.count()):
                m = self._tab_meta(i)
                if m.get("is_editor") == _is_editor and m.get("data") == _data:
                    self._do_close_tab(i)
                    return

        btn.clicked.connect(_do_close)
        self._tab_bar.setTabButton(index, QTabBar.RightSide, btn)

    def _tab_meta(self, idx: int) -> dict:
        return self._tab_bar.tabData(idx) or {}

    def _find_editor_tab(self, filepath: str) -> int:
        norm = os.path.normpath(filepath)
        for i in range(self._tab_bar.count()):
            m = self._tab_meta(i)
            if m.get("is_editor") and os.path.normpath(m.get("data", "")) == norm:
                return i
        return -1

    def _find_aux_tab(self, widget) -> int:
        for i in range(self._tab_bar.count()):
            m = self._tab_meta(i)
            if not m.get("is_editor") and m.get("data") is widget:
                return i
        return -1

    @staticmethod
    def _file_icon(filepath: str) -> QIcon:
        try:
            from icons.handler import icon as _icon
            ext  = os.path.splitext(filepath)[1].lower()
            name = os.path.basename(filepath).lower()

            _aux_map = {
                ".zip": "archive",    ".jar": "archive_jar", ".apk": "apk",
                ".7z":  "archive_7z", ".rar": "archive_rar",
                ".tar": "archive_tar",".tgz": "archive_tar",
                ".gz":  "archive_gz", ".bz2": "archive_gz", ".xz": "archive_gz",
                ".lz4": "archive_gz", ".zst": "archive_gz", ".zstd": "archive_gz",
                ".iso": "archive_iso",".pac": "archive",
                ".img": "drive",      ".bin": "bin",
                ".so":  "icon_so",
                ".mp3": "icon_audio", ".wav": "icon_audio", ".ogg": "icon_audio", ".flac": "icon_audio",
                ".mp4": "icon_video", ".mkv": "icon_video", ".avi": "icon_video", ".mov": "icon_video",
                ".png": "image",      ".jpg": "image",  ".jpeg": "image",
                ".gif": "image",      ".webp": "image", ".bmp": "image",  ".svg": "image",
            }
            key = _aux_map.get(ext)
            if not key:
                # Daftar media lengkap (m4a/aac/opus/aiff/dll) — pakai sumber
                # kebenaran tunggal di FileTree, bukan hardcode ulang.
                from main_ext.class_FileTree import FileTree
                if ext in FileTree.AUDIO_EXT:
                    key = "icon_audio"
                elif ext in FileTree.VIDEO_EXT:
                    key = "icon_video"
                elif ext in FileTree.IMAGE_EXT:
                    key = "image"
            if not key:
                from main_ext.class_WebEditorPanel import EditorBridge
                key = EditorBridge._ext_to_icon_key(filepath)

            path = _icon(key)
            if path and os.path.isfile(path):
                return QIcon(path)
        except Exception:
            pass
        return QIcon()

    def open_file(self, filepath: str) -> bool:
        name     = os.path.basename(filepath)
        existing = self._find_editor_tab(filepath)
        if existing >= 0:
            self._ignore_tab_change = True
            self._tab_bar.setCurrentIndex(existing)
            self._ignore_tab_change = False
            self._stack.setCurrentIndex(self._monaco_stack_idx)
            self._active_filepath = filepath
            self._editor_panel.bridge.open_file(filepath)
            self._update_run_button()
            return True

        idx = self._add_tab(name, is_editor=True, data=filepath)
        self._tab_bar.setTabIcon(idx, self._file_icon(filepath))
        self._ignore_tab_change = True
        self._tab_bar.setCurrentIndex(idx)
        self._ignore_tab_change = False
        self._stack.setCurrentIndex(self._monaco_stack_idx)
        self._active_filepath = filepath
        self._editor_panel.open_file(filepath)
        self._update_run_button()
        return True

    def bind_neobranch_tab(self, filepath: str, tree_base: str, fs_root: str,
                           source_path: str, branch_name):
        self._editor_panel.bridge.bind_neobranch_tab(
            filepath, tree_base, fs_root, source_path, branch_name
        )

    def prepare_neobranch_switch(self, tree_base: str, fs_root: str) -> bool:

        paths = self._editor_panel.bridge.neobranch_tabs(tree_base, fs_root)
        if any(self._editor_panel.is_unsaved(path) for path in paths):
            AppDialog.warning(
                self, "Unsaved NeoBranch tabs",
                "Save or close the edited tabs for this partition before switching branches."
            )
            return False
        for path in paths:
            self.close_file(path)
        return True

    def open_virtual(self, tab_id: str, title: str, content: str,
                     language: str = None, read_only: bool = False, on_save=None) -> bool:

        existing = self._find_editor_tab(tab_id)
        if existing >= 0:
            self._ignore_tab_change = True
            self._tab_bar.setCurrentIndex(existing)
            self._ignore_tab_change = False
            self._stack.setCurrentIndex(self._monaco_stack_idx)
            self._active_filepath = tab_id
            self._editor_panel.bridge.open_virtual(tab_id, content, language, read_only, on_save)
            self._update_run_button()
            return True

        idx = self._add_tab(title, is_editor=True, data=tab_id)
        self._tab_bar.setTabIcon(idx, self._file_icon(title))   # ikon dari nama file
        self._ignore_tab_change = True
        self._tab_bar.setCurrentIndex(idx)
        self._ignore_tab_change = False
        self._stack.setCurrentIndex(self._monaco_stack_idx)
        self._active_filepath = tab_id
        self._editor_panel.bridge.open_virtual(tab_id, content, language, read_only, on_save)
        self._update_run_button()
        return True

    def close_file(self, filepath: str):
        idx = self._find_editor_tab(filepath)
        if idx < 0:
            return
        self._tab_bar.removeTab(idx)
        self._editor_panel.close_file(filepath)
        self._after_tab_removed()

    def _on_monaco_tab_closed_from_js(self, filepath: str):
        idx = self._find_editor_tab(filepath)
        if idx >= 0:
            self._tab_bar.removeTab(idx)
            self._after_tab_removed()

    def mark_unsaved(self, filepath: str):
        idx = self._find_editor_tab(filepath)
        if idx < 0:
            return
        base = self._file_icon(filepath)
        self._tab_bar.setTabIcon(idx, _icon_with_dot(base))

    def mark_saved(self, filepath: str):
        idx = self._find_editor_tab(filepath)
        if idx < 0:
            return
        self._tab_bar.setTabIcon(idx, self._file_icon(filepath))

    def add_aux_tab(self, widget: QWidget, title: str, filepath: str = None) -> int:
        stack_idx = self._stack.addWidget(widget)
        tab_idx   = self._add_tab(title, is_editor=False, data=widget)
        if filepath:
            self._tab_bar.setTabIcon(tab_idx, self._file_icon(filepath))
        self._ignore_tab_change = True
        self._tab_bar.setCurrentIndex(tab_idx)
        self._ignore_tab_change = False
        self._stack.setCurrentIndex(stack_idx)
        self._active_filepath = None
        self._update_run_button()
        return tab_idx

    def find_aux_by_filepath(self, filepath: str, widget_class) -> int:
        norm = os.path.normpath(filepath)
        for i in range(self._tab_bar.count()):
            m = self._tab_meta(i)
            if not m.get("is_editor"):
                w  = m.get("data")
                wp = getattr(w, "filepath", None) or getattr(w, "img", None)
                if isinstance(w, widget_class) and wp and os.path.normpath(wp) == norm:
                    return i
        return -1

    def close_aux_tab_widget(self, widget: QWidget):

        for i in range(self._tab_bar.count()):
            m = self._tab_meta(i)
            if not m.get("is_editor") and m.get("data") is widget:
                self._do_close_tab(i)
                return

    def switch_to_aux_tab(self, tab_idx: int):
        m = self._tab_meta(tab_idx)
        if m.get("is_editor"):
            return
        self._ignore_tab_change = True
        self._tab_bar.setCurrentIndex(tab_idx)
        self._ignore_tab_change = False
        widget = m.get("data")
        if widget:
            self._stack.setCurrentIndex(self._stack.indexOf(widget))
        self._active_filepath = None
        self._update_run_button()

    def tab_count(self) -> int:
        return self._tab_bar.count()

    def editor_tab_count(self) -> int:
        return sum(1 for i in range(self._tab_bar.count()) if self._tab_meta(i).get("is_editor"))

    def aux_tab_count(self) -> int:
        return self.tab_count() - self.editor_tab_count()

    def has_unsaved(self) -> bool:
        return self._editor_panel.has_unsaved()

    def is_unsaved(self, filepath: str) -> bool:
        return self._editor_panel.is_unsaved(filepath)

    def save_current(self):
        self._editor_panel.save_current()

    def get_active_file(self):
        return self._active_filepath

    def get_open_files(self) -> list:
        return self._editor_panel.get_open_files()

    def _activate_editor(self, filepath: str):
        self._stack.setCurrentIndex(self._monaco_stack_idx)
        self._active_filepath = filepath

    def _on_tab_changed(self, index: int):
        if self._ignore_tab_change or index < 0:
            return
        m = self._tab_meta(index)
        if not m:
            return
        if m.get("is_editor"):
            fp = m.get("data")
            self._activate_editor(fp)
            self._editor_panel.bridge.open_file(fp)
        else:
            widget = m.get("data")
            if widget:
                self._stack.setCurrentIndex(self._stack.indexOf(widget))
            self._active_filepath = None
        self._update_run_button()

    def _on_tab_close_requested(self, index: int):
        self._do_close_tab(index)

    def _do_close_tab(self, index: int):
        m = self._tab_meta(index)
        if not m:
            return
        if m.get("is_editor"):
            fp = m.get("data")
            if self._editor_panel.is_unsaved(fp):
                if not AppDialog.confirm_close(
                    self, t("utc_unsaved_title"),
                    t("utc_unsaved_msg", filename=os.path.basename(fp))
                ):
                    return
            self._tab_bar.removeTab(index)
            self._editor_panel.close_file(fp)
        else:
            widget = m.get("data")
            self._tab_bar.removeTab(index)
            if widget:


                has_pending = False
                try:
                    has_pending = (
                        hasattr(widget, "has_pending_task")
                        and widget.has_pending_task()
                    )
                except Exception:
                    has_pending = False

                if has_pending:
                    widget._dcv_owner_stack = self._stack
                    widget.hide()
                    widget.request_close_when_idle()
                else:
                    self._stack.removeWidget(widget)
                    try:
                        widget.deleteLater()
                    except Exception:
                        pass
        self._after_tab_removed()

    def _after_tab_removed(self):
        total        = self._tab_bar.count()
        editor_count = self.editor_tab_count()
        if editor_count == 0:
            self.editorAllClosed.emit()
        if total == 0:
            self._update_run_button()
            self.allClosed.emit()
            return
        cur = self._tab_bar.currentIndex()
        if cur >= 0:
            self._on_tab_changed(cur)
        else:
            self._update_run_button()

    def _update_run_button(self):
        fp = self._active_filepath
        self._run_btn.setVisible(bool(fp and is_runnable_file(fp)))

    def _run_active_file(self):
        fp = self._active_filepath
        if fp and is_runnable_file(fp):
            self.runFileRequested.emit(fp)

    @property
    def bridge(self):
        return self._editor_panel.bridge

    @property
    def view(self):
        return self._editor_panel.view

    def view_initialized(self):
        return self._editor_panel.view_initialized()

    def is_ready(self) -> bool:
        return self._editor_panel.is_ready()

    def prepare(self):
        self._editor_panel.prepare()

    def open_file_monaco(self, filepath: str) -> bool:
        return self.open_file(filepath)
