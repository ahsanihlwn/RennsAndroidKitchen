# SPDX-License-Identifier: MPL-2.0
from core.project_lifecycle import log_project_event
import sys, os, re, shutil, shlex
from core.fsdetect import detect_fs_type
from core.erofs import unpack_erofs, repack_erofs
import json
from PySide6.QtCore import QEvent
import time
from core.flag import window_title
from core.threader import run_in_thread
from core.loading import LoadingAnimator
from main_ext.class_LoadingBar import LoadingBar
from core.code_editor import CodeHighlighter
from core.fsdetect import detect_fs_root
from PySide6.QtCore import Signal
from core.terminal import Terminal
from icons.handler import icon, icon_pixmap, tint_icon, create_themed_icon
from main_ext.class_WebToggle import WebToggle
from app_ext.class_SmoothScrollArea import SmoothScrollArea
from app_ext.class_BlurOverlay import BlurOverlay as _BlurOverlay
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
    QLineEdit,
    QScrollArea
)
from core.rennsproject_bridge import MainFeatures
from PySide6.QtCore import QTimer
from PySide6.QtGui import (
    QIcon,
    QPixmap,
    QPainter,
    QColor,
    QAction,
    QKeySequence,
    QTextCursor,
    QCursor,
    QTransform,
)

from PySide6.QtCore import (
    Qt,
    QRect,
    QSize,
    QFileSystemWatcher
)

from main_ext.class_ConfigEngine import ConfigEngine,prescan_task,load_file_context,load_fs_config,perm_to_rwx
from main_ext.class_ArchiveTab import ArchiveTab
from main_ext.class_PayloadViewer import PayloadViewer
from main_ext.class_CustomSplitter import CustomSplitter
from main_ext.class_FileTree import FileTree, _load_ctx_from_dir, _load_fs_from_dir
from main_ext.class_FileTreePanel import FileTreePanel
from main_ext.class_WebEditorPanel import WebEditorPanel
from main_ext.class_UnifiedTabContainer import UnifiedTabContainer
from main_ext.tab_scroll_buttons import ThemedScrollTabBar, configure_tab_scroll_buttons
from main_ext.file_run_commands import build_run_command, is_runnable_file


class _EditorGrabHandle(QSplitterHandle):


    def __init__(self, orientation, parent):
        super().__init__(orientation, parent)
        self._hover = False
        self.setAttribute(Qt.WA_Hover, True)

    def enterEvent(self, e):
        self._hover = True
        self.update()
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._hover = False
        self.update()
        super().leaveEvent(e)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), QColor(_th.BG))

        if self._hover:
            hex_ = _th.accent_hex.lstrip("#")
            r, g, b = int(hex_[0:2], 16), int(hex_[2:4], 16), int(hex_[4:6], 16)
            color = QColor(r, g, b, 220)
        else:
            color = QColor(140, 140, 150, 90)

        p.setPen(Qt.NoPen)
        p.setBrush(color)

        cx = self.width() // 2
        cy = self.height() // 2
        dot_r = 2
        gap = 5

        if self.orientation() == Qt.Vertical:
            for dx in (-gap, 0, gap):
                p.drawEllipse(cx + dx - dot_r, cy - dot_r, dot_r * 2, dot_r * 2)
        else:
            align_widget = getattr(self, "_align_widget", None)
            if isinstance(align_widget, QSplitter) and align_widget.count() > 1:
                tree_handle = align_widget.handle(1)
                if tree_handle is not None and tree_handle.isVisible():
                    align_widget = tree_handle
            if align_widget is not None and align_widget.isVisible():
                window = self.window()
                cy = (
                    align_widget.mapTo(window, align_widget.rect().center()).y()
                    - self.mapTo(window, self.rect().topLeft()).y()
                )
            for dy in (-gap, 0, gap):
                p.drawEllipse(cx - dot_r, cy + dy - dot_r, dot_r * 2, dot_r * 2)


class _EditorVSplitter(QSplitter):
    def __init__(self, orientation=Qt.Vertical, parent=None):
        super().__init__(orientation, parent)

    def createHandle(self):
        return _EditorGrabHandle(self.orientation(), self)


def _nb_overlay(base: str, ctx: dict, fs: dict):

    import json as _j
    nb_root = os.path.join(base, "neobranch")
    if not os.path.isdir(nb_root):
        return ctx, fs
    for fs_root_name in os.listdir(nb_root):
        fs_root_dir = os.path.join(nb_root, fs_root_name)
        if not os.path.isdir(fs_root_dir):
            continue
        state_path = os.path.join(fs_root_dir, "neobranch_state.json")
        if not os.path.isfile(state_path):
            continue
        try:
            with open(state_path, "r", encoding="utf-8") as _f:
                active_branch = _j.load(_f).get("active_branch")
        except Exception:
            continue
        if not active_branch:
            continue
        branch_cfg = os.path.join(fs_root_dir, f"branch_{active_branch}", "config")
        if not os.path.isdir(branch_cfg):
            continue
        ctx.update(_load_ctx_from_dir(branch_cfg))
        fs.update(_load_fs_from_dir(branch_cfg))
        print(f"[NeoBranch] overlay: {fs_root_name!r} → {active_branch!r}")
    return ctx, fs
from main_ext.class_AppDialog import AppDialog
from core.ui_lang import t, on_change as on_language_change, remove_listener as remove_language_listener
from main_ext.class_ToggleSwitch import build_card, build_level_slider
from main_ext.class_ImageToggle import ImageToggle
from main_ext.class_ThemeUi import theme as _th
import warnings
warnings.filterwarnings("ignore", category=RuntimeWarning, message='.*Failed to disconnect.*')
print("[PRIORITY_STAGE 2] MAIN LOAD")

def extract_terminal_command_name(raw_cmd: str) -> str:

    if not raw_cmd:
        return "terminal"
    clean = str(raw_cmd).strip()
    try:
        parts = shlex.split(clean, posix=False)
    except Exception:
        parts = clean.split()
    if not parts:
        return "terminal"

    first = parts[0].strip().strip('"').strip("'")
    if not first:
        return "terminal"

    base = os.path.basename(first.replace("\\", "/"))
    name, ext = os.path.splitext(base)
    if ext.lower() in [".exe", ".bat", ".cmd", ".sh", ".py", ".ps1"]:
        base = name
    return base or first or "terminal"

class Main(MainFeatures, QWidget):
    instance = None
    ready = Signal()
    return_to_project_signal = Signal()
    preparation_status = Signal(str)
    preparation_failed = Signal(str)

    # Class-level service bootstrap state
    _config_service_ready = False
    _config_service_mgr = None

    @classmethod
    def _ensure_config_service(cls, timeout: float = 10.0):

        # Health check: kalau service udah ditandai ready TAPI proses udah mati
        # atau WS disconnected, reset flag biar respawn.
        if cls._config_service_ready:
            try:
                from main_ext.config_engine_client import _ServiceConnection
                mgr = cls._config_service_mgr
                if mgr and mgr.is_alive() and _ServiceConnection.get().is_connected():
                    return  # masih sehat
                # service mati / disconnected → reset & respawn
                cls._config_service_ready = False
                cls._config_service_mgr = None
                print("[Main] service unhealthy, respawning...")
            except Exception:
                cls._config_service_ready = False
                cls._config_service_mgr = None
        try:
            import time as _t
            _t0 = _t.perf_counter()
            from services.process_manager import get_manager
            from main_ext.config_engine_client import ensure_connected
            mgr = get_manager()
            # start_process() idempotent — kalau AppUI udah pre-warm, ini no-op
            mgr.start_process()
            port = mgr.wait_ready(timeout=timeout)
            wait_ms = (_t.perf_counter() - _t0) * 1000
            ensure_connected(port)
            cls._config_service_mgr = mgr
            cls._config_service_ready = True
            print(f"[Main] ConfigEngine service ready on port {port} (pid={mgr.pid}, wait={wait_ms:.0f}ms)")
        except Exception as e:
            print(f"[Main] WARN: gagal start ConfigEngine service: {e}")
            print(f"[Main] WARN: fallback ke in-process ConfigEngine (UI bisa lag)")

    @classmethod
    def _shutdown_config_service(cls):
        if cls._config_service_mgr is not None:
            try:
                cls._config_service_mgr.stop()
            except Exception as e:
                print(f"[Main] WARN: stop service error: {e}")
        cls._config_service_ready = False
        cls._config_service_mgr = None

    def __init__(self, project=None, p1=None, p2=None, profile_provider=None):
        super().__init__()
        self.setAttribute(Qt.WA_DontShowOnScreen, True)
        self.loading = None
        self._ready_emitted = False
        self._project_load_failed = False
        self._entry_transition_active = False
        self._entry_content_reveal = None
        self._pending_prescan_result = None
        self._preparation_failed = False
        self._preparation_aborted = False
        self._editor_ready = False
        self._pending_prescan_bases = set()
        self._status_preparing_editor_emitted = False
        self._preparation_timer = None
        self._preparation_start_time = None
        self._preparation_timeout = 30.0


        Main.instance = self
        self.project = project
        self.p1 = p1
        self.p2 = p2


        from core.task_queue import TaskQueue
        self.task_queue = TaskQueue(run_in_thread)
        self._completed_task_types = set()


        self.task_queue.set_on_empty(self.maybe_finish_loading)

        self.setStyleSheet(f"background-color: {_th.BG};")

        # Register listener so Main rebuilds when theme changes (only once per instance)
        if not getattr(self, "_theme_listener_registered", False):
            _th.on_change(self._on_theme_changed)
            self._theme_listener_registered = True


        self.profile_provider = profile_provider


        self.setWindowTitle(window_title())
        on_language_change(self._refresh_edition_title)
        self.setWindowIcon(QIcon(icon("ic_launcher")))
        self.resize(1080, 700)


        layout = QVBoxLayout(self)
        #QApplication.processEvents()


        self.tree = FileTree()
        self.tree2 = FileTree()
        self.tree_mirror = FileTree()
        self.tree_panel = FileTreePanel(self.tree)
        self.tree2_panel = FileTreePanel(self.tree2)
        self.tree_mirror_panel = FileTreePanel(self.tree_mirror)
        self.tree.itemExpanded.connect(self.tree.on_expand)
        self.tree2.itemExpanded.connect(self.tree2.on_expand)
        self.tree_mirror.itemExpanded.connect(self.tree_mirror.on_expand)

        # Apply the saved view mode before the project trees are populated.
        for tree in (self.tree, self.tree2, self.tree_mirror):
            tree.set_display_mode(_th.tree_view_mode)
        _th.on_view_mode_change(self._on_tree_view_mode_changed)

        self.active_tree = self.tree
        self.tree_mode = 1
        self.drag_src_path = None
        self.drag_src_parent = None
        # Clipboard copy/cut FileTree — nempel di window supaya bisa lintas tree
        # (copy di tree kiri, paste di tree kanan), sama seperti drag-drop.
        self.fs_clipboard = None

        self.tree.folder_has_child = {}
        self.tree.fsdetect_cache = {}

        # run prescan in background


        self.splitter = _EditorVSplitter(Qt.Horizontal)
        self.splitter.setHandleWidth(8)
        self.splitter.setStyleSheet(f"""
            QSplitter {{ background: {_th.BG}; }}
            QSplitter::handle {{ background: {_th.BG}; }}
        """)

        # LEFT PANEL
        left_wrap = QWidget()
        left_layout = QVBoxLayout(left_wrap)
        left_layout.setContentsMargins(0,0,0,0)
        left_layout.setSpacing(0)


        top_bar_wrap = QWidget()
        top_bar_layout = QHBoxLayout(top_bar_wrap)
        top_bar_layout.setContentsMargins(8, 8, 8, 6)
        top_bar_layout.setSpacing(10)
        self._init_private_toolbar(top_bar_layout)


        divider = QWidget()
        divider.setFixedWidth(1)
        divider.setFixedHeight(28)  # lebih pendek dikit biar estetik
        divider.setStyleSheet(f"""
            background-color: {_th.border_color(0.12)};
        """)

        top_bar_layout.addWidget(divider)

        # Explorer bar
        from main_ext.class_SmartActionBar import SmartActionBar


        exp_bar = QHBoxLayout()
        exp_bar.setContentsMargins(0,0,0,0)

        self.smart_bar = SmartActionBar()
        exp_bar.addWidget(self.smart_bar)
        exp_bar.addStretch()


        exp_bar.setContentsMargins(0,0,0,0)
        exp_label = QLabel("")

        btn_add_folder = QPushButton()
        btn_add_folder.setIcon(QIcon(icon("add_folder")))
        btn_add_folder.clicked.connect(lambda: self.active_tree.add_new("folder"))

        btn_add_file = QPushButton()
        btn_add_file.setIcon(QIcon(icon("add_file")))
        btn_add_file.clicked.connect(lambda: self.active_tree.add_new("file"))

        self.btn_mode = QPushButton()
        self.btn_mode.setIcon(QIcon(icon("tree_mode")))
        self.btn_mode.setToolTip("Toggle Layout / Split View")
        self.btn_mode.clicked.connect(self.toggle_tree_mode)
        self.btn_mode.setEnabled(False)  # ⬅️ KUNCI DI AWAL

        exp_bar.addWidget(exp_label)
        exp_bar.addSpacing(0)

        btn_return = QPushButton()
        btn_return.setIcon(QIcon(icon("back_to_proj")))  # bebas icon apa
        btn_return.clicked.connect(self.return_to_project)

        self.smart_bar.add_button(btn_return)

        # SETTINGS BUTTON
        self.btn_settings = QPushButton()
        self.btn_settings.setIcon(QIcon(icon("set_project")))
        self.btn_settings.clicked.connect(self.open_project_settings)

        self.smart_bar.add_button(self.btn_settings)
        self.smart_bar.add_button(self.btn_mode)

        self._add_private_actions()

        # TERMINAL BUTTON
        btn_terminal = QPushButton()
        btn_terminal.setIcon(QIcon(icon("terminal")))
        btn_terminal.clicked.connect(self.toggle_terminal)
        self.smart_bar.add_button(btn_terminal)


        from core.binary_handler import is_available as _is_bin_avail, _get_watcher, _DEFAULT_BIN_ROOT
        btn_android_tools = QPushButton()
        btn_android_tools.setIcon(QIcon(icon("android_tools")))
        btn_android_tools.clicked.connect(self.open_android_tools)
        self.smart_bar.add_button(btn_android_tools)
        btn_android_tools.setVisible(_is_bin_avail("adb.exe"))

        # Live-update saat user install/uninstall package via DependencyManager.
        try:
            _w = _get_watcher(_DEFAULT_BIN_ROOT)
            if _w is not None:
                _w.updated.connect(
                    lambda: btn_android_tools.setVisible(_is_bin_avail("adb.exe"))
                )
        except Exception:
            pass


        btn_import = QPushButton()
        btn_import.setIcon(QIcon(icon("import")))
        btn_import.clicked.connect(self.import_files)

        self.smart_bar.add_button(btn_import)


        self.smart_bar.add_button(btn_add_folder)
        self.smart_bar.add_button(btn_add_file)

        # exp_bar.addWidget(self.loading_dot)
        # exp_bar.addWidget(self.loading_text)

        exp_bar.addStretch()

        exp_bar_widget = QWidget()
        exp_bar_widget.setLayout(exp_bar)
        exp_bar_widget.setFixedHeight(32)

        top_bar_layout.addWidget(exp_bar_widget, 1)

        self._start_private_features()

        left_layout.addWidget(top_bar_wrap)


        self.loading_bar = LoadingBar()
        left_layout.addWidget(self.loading_bar)


        # Tree Split
        self.tree_split = CustomSplitter(Qt.Vertical)
        self.tree_split.setHandleWidth(8)
        self.tree_split.addWidget(self.tree_panel)
        left_layout.addWidget(self.tree_split, 1)
        left_layout.setStretch(0, 0)  # top bar
        left_layout.setStretch(1, 0)  # loading bar
        left_layout.setStretch(2, 1)  # tree


        exp_bar_widget.setSizePolicy(
            QSizePolicy.Expanding,
            QSizePolicy.Fixed
        )

        self.tree_split.setSizePolicy(
            QSizePolicy.Expanding,
            QSizePolicy.Expanding
        )


        self.right_wrap = QWidget()
        self.right_wrap.setObjectName("rightWrap")
        self.right_wrap.setStyleSheet(f"""
        QWidget#rightWrap {{
            background-color: {_th.BG};
            border: none;
        }}
        """)

        right_layout = QVBoxLayout(self.right_wrap)
        right_layout.setContentsMargins(0, 6, 6, 0)
        right_layout.setSpacing(0)


        self.unified_tabs = UnifiedTabContainer()
        self.unified_tabs.allClosed.connect(self._on_all_tabs_closed)
        self.unified_tabs.editorAllClosed.connect(self._on_all_editor_tabs_closed)
        self.unified_tabs.runFileRequested.connect(self.run_editor_file)

        # Alias kompatibilitas — kode lama pakai self.editor_panel / self.aux_tab
        self.editor_panel = self.unified_tabs
        self.aux_tab      = self.unified_tabs

        self.editor_wrap_inner = QWidget()
        self.editor_wrap_inner.setObjectName("editorCard")
        self.editor_wrap_inner.setAttribute(Qt.WA_StyledBackground, True)


        self.editor_wrap_inner.setStyleSheet(f"""
        QWidget#editorCard {{
            background-color: {_th.BG3};
            border: 1px solid {_th.border_color(0.18)};
            border-radius: 8px;
        }}
        """)
        inner_layout = QVBoxLayout(self.editor_wrap_inner)
        inner_layout.setContentsMargins(3, 3, 3, 3)
        inner_layout.setSpacing(0)
        inner_layout.addWidget(self.unified_tabs)


        # TERMINAL PANEL (HARUS ADA WRAPPER BIAR GA NGEBUG)
        # TERMINAL PANEL MIRIP EDITOR TAB
        self.terminal_wrap = QWidget()
        self.terminal_wrap.setObjectName("terminalWrap")
        self.terminal_wrap.setAttribute(Qt.WA_StyledBackground, True)


        self.terminal_wrap.setStyleSheet(f"""
        QWidget#terminalWrap {{
            background-color: {_th.BG};
            border: 1px solid {_th.border_color(0.18)};
            border-radius: 8px;
        }}
        """)
        tw_layout = QVBoxLayout(self.terminal_wrap)
        tw_layout.setSpacing(0)
        tw_layout.setContentsMargins(3, 3, 3, 3)


        self.terminal_bar_wrap = QWidget()
        self.terminal_bar_wrap.setObjectName("terminalTabBarWrap")
        self.terminal_bar_wrap.setFixedHeight(34)
        self.terminal_bar_wrap.setAttribute(Qt.WA_StyledBackground, True)
        self.terminal_bar_wrap.setStyleSheet(f"""
        QWidget#terminalTabBarWrap {{
            background: {_th.BG_DARK};
            border-bottom: 1px solid {_th.border_color(0.06)};
            border-top-left-radius: 4px;
            border-top-right-radius: 4px;
        }}
        """)
        self.terminal_bar = QHBoxLayout(self.terminal_bar_wrap)
        self.terminal_bar.setContentsMargins(0, 0, 8, 0)
        self.terminal_bar.setSpacing(0)

        # TAB SESSION CONTAINER
        self.session_tabs = ThemedScrollTabBar()
        self.session_tabs.setStyleSheet(UnifiedTabContainer._tab_bar_style())
        self.session_tabs.setTabsClosable(False)
        self.session_tabs.setExpanding(False)
        self.session_tabs.setMovable(False)
        self.session_tabs.setUsesScrollButtons(True)
        configure_tab_scroll_buttons(self.session_tabs)
        self.session_tabs.setIconSize(QSize(14, 14))

        # koneksikan event tab → handler
        self.session_tabs.currentChanged.connect(self._on_terminal_tab_changed)

        # ADD SESSION BUTTON (+)
        self.btn_add_session = QPushButton()
        self.btn_add_session.setFixedSize(24, 24)
        self.btn_add_session.setCursor(Qt.PointingHandCursor)
        self.btn_add_session.setIcon(create_themed_icon("add_session", _th.FG_BLUE, _th.FG_BLUE_LIGHT, 12))
        self.btn_add_session.setIconSize(QSize(12, 12))
        self.btn_add_session.setToolTip("New Session")
        self.btn_add_session.setStyleSheet(f"""
            QPushButton {{
                background: {_th.accent(0.08)};
                border: 1px solid {_th.accent(0.24)};
                border-radius: 12px;
                padding: 0px;
            }}
            QPushButton:hover {{
                background: {_th.accent(0.18)};
                border-color: {_th.accent(0.48)};
            }}
            QPushButton:pressed {{
                background: {_th.accent(0.30)};
            }}
        """)
        # klik -> buat session baru sesuai active_root_id
        self.btn_add_session.clicked.connect(self.create_terminal_session)

        # ROOT SELECTOR (DROPDOWN)
        self.active_root_id = "tree1"
        self._linux_source_root_id = "tree1"
        self._wsl_ready = False
        self._wsl_probe_running = False
        self._wsl_probe_checked_at = None
        self.root_selector = QPushButton()
        self.root_selector.setFixedHeight(24)
        self.root_selector.setFixedWidth(44)
        self.root_selector.setCursor(Qt.PointingHandCursor)
        self.root_selector.setLayoutDirection(Qt.RightToLeft)
        self.root_selector.setIconSize(QSize(12, 12))
        self.root_selector.setStyleSheet(f"""
            QPushButton {{
                background: {_th.border_color(0.06)};
                border: 1px solid {_th.border_color(0.12)};
                border-radius: 6px;
                padding-left: 2px;
                padding-right: 5px;
                color: {_th.FG2};
                font-family: 'Poppins', 'Inter', 'Segoe UI', sans-serif;
                font-size: 11px;
                font-weight: 500;
            }}
            QPushButton:hover {{
                background: {_th.border_color(0.12)};
                border-color: {_th.border_color(0.24)};
                color: {_th.FG_BRIGHT};
            }}
            QPushButton:pressed {{
                background: {_th.border_color(0.16)};
            }}
        """)
        self._update_root_selector_display()
        self.root_selector.clicked.connect(self.show_root_menu)

        self.root_label = QLabel(os.path.basename(self.p1) if self.p1 else "")
        self.root_label.setStyleSheet(f"color:{_th.FG_HOVER}; font-size:11px;")

        # SPAM ENTER TOGGLE (QWEB / WebToggle)
        self.spam_enter_wrap = QWidget()
        self.spam_enter_wrap.setFixedHeight(24)
        self.spam_enter_wrap.setObjectName("terminalSpamEnterWrap")
        self.spam_enter_wrap.setCursor(Qt.PointingHandCursor)
        self.spam_enter_wrap.setToolTip("Spam Enter: Otomatis kirim Enter berulang ke terminal")

        _se_lay = QHBoxLayout(self.spam_enter_wrap)
        _se_lay.setContentsMargins(8, 0, 4, 0)
        _se_lay.setSpacing(6)

        self.lbl_terminal_spam_enter = QLabel("Spam Enter")
        self.lbl_terminal_spam_enter.setAttribute(Qt.WA_TransparentForMouseEvents)

        self.toggle_terminal_spam_enter = WebToggle(
            checked=self._is_spam_enter_enabled(),
            size=QSize(34, 18),
            neutral=False
        )
        _se_lay.addWidget(self.lbl_terminal_spam_enter)
        _se_lay.addWidget(self.toggle_terminal_spam_enter)

        self.toggle_terminal_spam_enter.clicked.connect(self._on_terminal_bar_spam_enter_toggled)

        def _on_wrap_click(e):
            if e.button() == Qt.LeftButton:
                self.toggle_terminal_spam_enter.setChecked(not self.toggle_terminal_spam_enter.isChecked())
                self._on_terminal_bar_spam_enter_toggled()
        self.spam_enter_wrap.mousePressEvent = _on_wrap_click

        self._update_spam_enter_bar_ui(self._is_spam_enter_enabled())

        # masukkan ke bar: session tabs, tombol (+), stretch, root dropdown, spam enter
        self.terminal_bar.addWidget(self.session_tabs)
        self.terminal_bar.addSpacing(6)
        self.terminal_bar.addWidget(self.btn_add_session)
        self.terminal_bar.addStretch()
        self.terminal_bar.addWidget(self.root_selector)
        self.terminal_bar.addSpacing(8)
        self.terminal_bar.addWidget(self.spam_enter_wrap)
        self.terminal_bar.addSpacing(4)


        # tambahkan terminal bar wrapper (session tab + dropdown)
        tw_layout.addWidget(self.terminal_bar_wrap)
        self.terminal_wrap.hide()


        self.editor_split = _EditorVSplitter()
        self.editor_split.setHandleWidth(12)
        self.editor_split.addWidget(self.editor_wrap_inner)
        self.editor_split.addWidget(self.terminal_wrap)
        self.editor_split.setStyleSheet(f"""
            QSplitter {{
                background: {_th.BG};
            }}
            QSplitter::handle {{
                background: {_th.BG};
            }}
        """)
        self.terminal_wrap.hide()

        # session per root (pisah total)
        self.terminal_sessions = {
            "tree1": [],
            "tree2": [],
            "linux": [],
        }
        self.file_run_terminal_sessions = {
            "tree1": {},
            "tree2": {}
        }

        # huruf index per root (A, B, C...)
        self.terminal_session_index = {
            "tree1": 1,
            "tree2": 1,
            "linux": 1,
        }

        self.active_terminal_widget = None

        self.terminal_was_open = False

        right_layout.addWidget(self.editor_split)

        # hide editor at start
        self.right_wrap.hide()


        self.splitter.addWidget(left_wrap)
        self.splitter.addWidget(self.right_wrap)
        self.splitter.handle(1)._align_widget = self.tree_split
        self.tree_split.splitterMoved.connect(lambda *_: self.splitter.handle(1).update())
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.splitterMoved.connect(lambda *_: self.smart_bar.check_overflow())
        self.smart_bar.set_right_ref(self.right_wrap)


        layout.addWidget(self.splitter)


        self.erofs_bar = QLabel("")
        self.erofs_bar.setVisible(False)
        self.erofs_bar.setStyleSheet(f"""
            QLabel {{
                background: {_th.BG3};
                color: {_th.FG_HOVER};
                padding: 2px 6px;      /* BAR TIPIS */
                border-top: 1px solid {_th.GRAY};
                font-size: 11px;
                min-height: 0px;       /* SUPAYA NGGAK MAKSA TINGGI */
            }}
        """)
        # ⬇️ Tambahkan ini biar bener-bener tipis
        self.erofs_bar.setFixedHeight(16)


        layout.addWidget(self.erofs_bar)


        save_shortcut = QAction(self)
        save_shortcut.setShortcut("Ctrl+S")
        save_shortcut.triggered.connect(self.save_hotkey)
        self.addAction(save_shortcut)

        find_shortcut = QAction(self)
        find_shortcut.setShortcut("Ctrl+F")
        find_shortcut.triggered.connect(self.open_find_dialog)
        self.addAction(find_shortcut)


        del_shortcut = QAction(self)
        del_shortcut.setShortcut(QKeySequence(Qt.Key_Delete))
        del_shortcut.triggered.connect(self._tree_delete_hotkey)
        self.addAction(del_shortcut)


        # ⛔ JANGAN LANJUT BUILD DI CONSTRUCTOR
        QTimer.singleShot(0, self._build_after_init)
        return

    def update_smartbar_visibility(self):
        # SmartActionBar auto-detect overflow via resizeEvent,
        # tapi kalau dipanggil manual (misal setelah splitter geser) trigger juga.
        self.smart_bar.check_overflow()

    def return_to_project(self):
        if not AppDialog.confirm(
            self, t("main_return_title"),
            t("main_return_msg"),
            yes_label=t("main_btn_return"), no_label=t("main_btn_cancel")
        ):
            return


        self._returning = True
        if self.close():
            self.return_to_project_signal.emit()
        else:
            self._returning = False

    def _build_after_init(self):
        # SEMUA ISI __init__ SETELAH STATE DASAR PINDAH KE SINI
        # (tree, splitter, editor, terminal, dll)

        # AUTO LOAD PROJECT
        if self.project and self.p1:
            QTimer.singleShot(0, self.load_project)
        self._refresh_wsl_terminal_status()

    def _refresh_edition_title(self):
        self.setWindowTitle(window_title())

    def _on_tree_view_mode_changed(self, mode):
        for tree in (self.tree, self.tree2, self.tree_mirror):
            tree.set_display_mode(mode)
        for panel in (self.tree_panel, self.tree2_panel, self.tree_mirror_panel):
            panel.refresh_metadata()

    def _on_theme_changed(self):

        try:
            self.setStyleSheet(f"background-color: {_th.BG};")
            if hasattr(self, "smart_bar"):
                self.smart_bar.refresh_theme()
            for panel_name in ("tree_panel", "tree2_panel", "tree_mirror_panel"):
                panel = getattr(self, panel_name, None)
                if panel is not None:
                    panel.refresh_theme()
            if hasattr(self, "unified_tabs"):
                self.unified_tabs.refresh_tab_bar_theme()
            if hasattr(self, "session_tabs"):
                self.session_tabs.setStyleSheet(UnifiedTabContainer._tab_bar_style())
                configure_tab_scroll_buttons(self.session_tabs)
            # Apply themed stylesheets to known persistent UI elements
            if hasattr(self, "right_wrap"):
                self.right_wrap.setStyleSheet(f"""
                QWidget#rightWrap {{
                    background-color: {_th.BG};
                    border: none;
                }}
                """)
            if hasattr(self, "editor_wrap_inner"):
                self.editor_wrap_inner.setStyleSheet(f"""
                QWidget#editorCard {{
                    background-color: {_th.BG3};
                    border: 1px solid {_th.border_color(0.18)};
                    border-radius: 8px;
                }}
                """)
            if hasattr(self, "terminal_wrap"):
                self.terminal_wrap.setStyleSheet(f"""
                QWidget#terminalWrap {{
                    background-color: {_th.BG};
                    border: 1px solid {_th.border_color(0.18)};
                    border-radius: 8px;
                }}
                """)
            if hasattr(self, "editor_split"):
                self.editor_split.setStyleSheet(f"""
                    QSplitter {{ background: {_th.BG}; }}
                    QSplitter::handle {{ background: {_th.BG}; }}
                """)
            if hasattr(self, "erofs_bar"):
                self.erofs_bar.setStyleSheet(f"""
                    QLabel {{
                        background: {_th.BG3};
                        color: {_th.FG_HOVER};
                        padding: 2px 6px;
                        border-top: 1px solid {_th.GRAY};
                        font-size: 11px;
                        min-height: 0px;
                    }}
                """)
            if hasattr(self, "root_label"):
                self.root_label.setStyleSheet(f"color:{_th.FG_HOVER}; font-size:11px;")
            if hasattr(self, "btn_add_session"):
                self.btn_add_session.setIcon(create_themed_icon("add_session", _th.FG_BLUE, _th.FG_BLUE_LIGHT, 12))
                self.btn_add_session.setStyleSheet(f"""
                    QPushButton {{
                        background: {_th.accent(0.08)};
                        border: 1px solid {_th.accent(0.24)};
                        border-radius: 12px;
                        padding: 0px;
                    }}
                    QPushButton:hover {{
                        background: {_th.accent(0.18)};
                        border-color: {_th.accent(0.48)};
                    }}
                    QPushButton:pressed {{
                        background: {_th.accent(0.30)};
                    }}
                """)
            if hasattr(self, "root_selector"):
                self.root_selector.setStyleSheet(f"""
                    QPushButton {{
                        background: {_th.border_color(0.06)};
                        border: 1px solid {_th.border_color(0.12)};
                        border-radius: 6px;
                        padding-left: 2px;
                        padding-right: 5px;
                        color: {_th.FG2};
                        font-family: 'Poppins', 'Inter', 'Segoe UI', sans-serif;
                        font-size: 11px;
                        font-weight: 500;
                    }}
                    QPushButton:hover {{
                        background: {_th.border_color(0.12)};
                        border-color: {_th.border_color(0.24)};
                        color: {_th.FG_BRIGHT};
                    }}
                    QPushButton:pressed {{
                        background: {_th.border_color(0.16)};
                    }}
                """)

                self._update_root_selector_display()
            if hasattr(self, "spam_enter_wrap"):
                self._update_spam_enter_bar_ui(self._is_spam_enter_enabled())


            try:
                ep = getattr(self, "editor_panel", None)
                if ep and ep.view_initialized():
                    from PySide6.QtCore import QUrl
                    from main_ext.class_WebEditorPanel import _build_html
                    ep.view.page().setBackgroundColor(QColor(_th.BG3))
                    ep.view.setStyleSheet(f"background: {_th.BG3};")
                    from core.monaco_assets import resolve_monaco_vs
                    ep.bridge._ready = False
                    base_dir = str(resolve_monaco_vs().parent) + os.sep
                    ep.view.setHtml(_build_html(), QUrl.fromLocalFile(base_dir))
            except Exception as _e:
                print(f"[Main._on_theme_changed] WebEditor reload skipped: {_e}")
            for t_ in (self.tree, self.tree2, self.tree_mirror):
                try:
                    t_.reload_icons()
                except Exception:
                    pass
        except Exception as e:
            print(f"[Main._on_theme_changed] {e}")


    def load_project(self):
        if not self.project or not self.p1 or getattr(self, "_closing", False):
            return


        try:
            if self._wait_for_config_service():
                return
            self._load_project_impl()
        except Exception as e:
            import traceback
            print(f"[Main] load_project GAGAL: {e}")
            traceback.print_exc()
            log_project_event("OPEN_FAILED", self.project, phase="load_project", error=str(e))
            self._fail_project_preparation(str(e))

    def _fail_project_preparation(self, reason: str = ""):
        if self._preparation_failed or self._ready_emitted:
            return
        self._preparation_failed = True
        self._project_load_failed = True
        self._preparation_aborted = True
        self._stop_preparation_timer()
        try:
            self.set_loading_failed(reason or "Load failed")
        except Exception:
            pass
        self.preparation_failed.emit(reason or "Load failed")

    def _stop_preparation_timer(self):
        if getattr(self, "_preparation_timer", None) is not None:
            try:
                self._preparation_timer.stop()
                self._preparation_timer.deleteLater()
            except Exception:
                pass
            self._preparation_timer = None

    def abort_project_preparation(self):
        self._closing = True
        self._preparation_aborted = True
        self._stop_preparation_timer()
        for tree in self._get_active_trees():
            try:
                tree.stop_background_work()
            except Exception:
                pass

    def _get_active_trees(self):
        trees = []
        if getattr(self, "tree", None) is not None:
            trees.append(self.tree)
        if getattr(self, "tree_mirror", None) is not None:
            trees.append(self.tree_mirror)
        if getattr(self, "p2", None) and getattr(self, "tree2", None) is not None:
            trees.append(self.tree2)
        return trees

    def _emit_ready_once(self):
        if self._ready_emitted or self._preparation_failed or self._preparation_aborted:
            return
        self._ready_emitted = True
        self.ready.emit()

    def _wait_for_config_service(self):

        if Main._config_service_ready:
            mgr = Main._config_service_mgr
            from main_ext.config_engine_client import _ServiceConnection
            if mgr and mgr.is_alive() and _ServiceConnection.get().is_connected():
                return False
        from services.process_manager import get_manager
        mgr = get_manager()
        mgr.start_process()
        started = getattr(self, "_config_service_wait_started", None)
        if started is None:
            started = self._config_service_wait_started = time.monotonic()
        if mgr.poll_ready() is None and time.monotonic() - started < 10:
            QTimer.singleShot(50, self, self.load_project)
            return True
        # Handshake is available (or the deadline expired): no polling sleeps.
        Main._ensure_config_service(timeout=0)
        return False

    def _load_project_impl(self):
        root = self.project
        port1 = os.path.join(root, self.p1)

        # load_project() has already completed the service bootstrap while
        # allowing the loading window's event loop to keep painting.

        # A killed repack process may have left a NeoBranch overlay on disk.
        from main_ext.class_NeoBranchManager import NeoBranchManager
        for tree_base in (os.path.join(root, name) for name in (self.p1, self.p2) if name):
            recovered = NeoBranchManager(tree_base).recover_interrupted_repacks()
            if recovered:
                print(f"[NeoBranch] recovered interrupted repacks in {tree_base}: {recovered}")
        self.task_queue.resume_after_recovery()

        # TREE 1
        self.tree.base = port1
        self.tree.engine = ConfigEngine(port1)
        self.tree.engine.tree_ref = self.tree


        _full1 = self.tree.engine.load_full_state()
        self.tree.ctx = _full1.get("ctx", {})
        self.tree.fs = _full1.get("fs", {})
        self.tree.ctx_neocat = _full1.get("neocat", {})

        self.tree.folder_has_child = {}
        self.tree.fsdetect_cache = {}

        # state ctx/fs/neocat/overlay baru di-load di atas — kasih sinyal ke
        # populate_root() untuk SKIP reload_config_cache pertama (cegah double-load)
        self.tree._skip_initial_reload = True

        # MIRROR TREE (mode 1 split support)
        self.tree_mirror.base = self.tree.base
        self.tree_mirror.engine = self.tree.engine
        self.tree_mirror.ctx = self.tree.ctx
        self.tree_mirror.fs = self.tree.fs
        self.tree_mirror.ctx_neocat = self.tree.ctx_neocat
        self.tree_mirror.folder_has_child = self.tree.folder_has_child
        self.tree_mirror.fsdetect_cache = self.tree.fsdetect_cache
        self.tree_mirror._skip_initial_reload = True
        self.tree_mirror.clear()
        self.tree_mirror.populate_root()

        # TREE 2
        if self.p2:
            port2 = os.path.join(root, self.p2)
            self.tree2.base = port2
            self.tree2.engine = ConfigEngine(port2)
            self.tree2.engine.tree_ref = self.tree2

            _full2 = self.tree2.engine.load_full_state()
            self.tree2.ctx = _full2.get("ctx", {})
            self.tree2.fs = _full2.get("fs", {})
            self.tree2.ctx_neocat = _full2.get("neocat", {})

            self.tree2.clear()
            self.tree2.folder_has_child = {}
            self.tree2.fsdetect_cache = {}

            self.tree2._skip_initial_reload = True
            self.tree2.populate_root()

            self.tree_mode = 2
            self.apply_tree_mode()
        else:
            self.tree2.hide()
            self.tree_mode = 1
            self.apply_tree_mode()
            if hasattr(self, "btn_mode"):
                self.btn_mode.show()


        self.tree._skip_initial_reload = True
        self.tree.populate_root()

        # loading UI
        self.set_loading_active(["Scanning environment..."])
        self.preparation_status.emit("Scanning environment...")

        # Editor panel background preparation
        self.editor_panel.bridge.editorReady.connect(self._on_editor_ready)
        self.editor_panel.prepare()
        if self.editor_panel.is_ready():
            self._editor_ready = True

        import time
        self._preparation_start_time = time.monotonic()
        self._pending_prescan_bases = set()

        bases_to_scan = [self.tree.base]
        if getattr(self, "p2", None) and getattr(self, "tree2", None) and getattr(self.tree2, "base", None):
            if os.path.normpath(self.tree2.base) != os.path.normpath(self.tree.base):
                bases_to_scan.append(self.tree2.base)

        for base_path in bases_to_scan:
            norm_b = os.path.normpath(base_path)
            self._pending_prescan_bases.add(norm_b)
            run_in_thread(
                prescan_task,
                base_path,
                callback=lambda ok, msg, bp=norm_b: self._after_prescan(ok, msg, bp)
            )

        self._stop_preparation_timer()
        self._preparation_timer = QTimer(self)
        self._preparation_timer.setInterval(50)
        self._preparation_timer.timeout.connect(self._check_preparation_gate)
        self._preparation_timer.start()

    def _on_editor_ready(self):
        if self._preparation_failed or self._preparation_aborted or getattr(self, "_closing", False):
            return
        self._editor_ready = True
        self._check_preparation_gate()

    def _check_preparation_gate(self):
        if getattr(self, "_ready_emitted", False) or self._preparation_failed or self._preparation_aborted or getattr(self, "_closing", False):
            return

        if self._preparation_start_time and (time.monotonic() - self._preparation_start_time) > self._preparation_timeout:
            self._fail_project_preparation("Preparation timed out")
            return

        for tree in self._get_active_trees():
            if getattr(tree, "_directory_errors", 0) > 0:
                self._fail_project_preparation("Directory error during startup")
                return
            if tree.directory_work_pending():
                return

        if len(self._pending_prescan_bases) > 0:
            return

        if not self._editor_ready:
            if hasattr(self, "editor_panel") and self.editor_panel.is_ready():
                self._editor_ready = True
            else:
                if not self._status_preparing_editor_emitted:
                    self._status_preparing_editor_emitted = True
                    self.preparation_status.emit("Preparing editor...")
                return

        self._stop_preparation_timer()
        if hasattr(self, "splitter"):
            self.splitter.show()
        if hasattr(self, "btn_mode"):
            self.btn_mode.show()
            self.btn_mode.setEnabled(True)
        self.setAttribute(Qt.WA_DontShowOnScreen, False)
        if hasattr(self, "update_smartbar_visibility"):
            self.update_smartbar_visibility()

        self.set_loading_finished("Finished")

        try:
            from core import rak_cli
            rak_cli.attach(self)
            rak_cli.start_ipc_server()
            rak_cli.ensure_rak_bat()
            rak_cli.maybe_autostart()
        except Exception as _e:
            print(f"[Main] RAK_CLI attach skipped: {_e}")

        self._emit_ready_once()

    def _begin_entry_transition(self):
        self._entry_transition_active = True

    def _complete_entry_transition(self):
        self._entry_transition_active = False
        result = self._pending_prescan_result
        self._pending_prescan_result = None
        if result is not None and not getattr(self, "_closing", False) and not self._preparation_aborted:
            QTimer.singleShot(0, self, lambda: self._after_prescan(*result))

    def _after_prescan(self, ok, msg, base_path=None):
        if getattr(self, "_closing", False) or self._preparation_aborted or self._preparation_failed:
            return

        if base_path is None:
            base_path = os.path.normpath(self.tree.base)
        else:
            base_path = os.path.normpath(base_path)

        if self._entry_transition_active:
            self._pending_prescan_result = (ok, msg, base_path)
            return

        if base_path not in self._pending_prescan_bases:
            return
        self._pending_prescan_bases.remove(base_path)

        if not ok:
            self._fail_project_preparation(f"Prescan failed: {msg}")
            return

        try:
            data = json.loads(msg)
            folders = data.get("folders", {})
            fscache = data.get("fscache", {})
            snap_from_prescan = data.get("child_snapshot", {})
        except Exception as e:
            self._fail_project_preparation(f"Prescan malformed JSON: {e}")
            return

        target_tree = self.tree
        if getattr(self, "p2", None) and getattr(self, "tree2", None) and getattr(self.tree2, "base", None):
            if base_path == os.path.normpath(self.tree2.base):
                target_tree = self.tree2

        target_tree.folder_has_child = {
            os.path.normpath(k): v for k, v in folders.items()
        }
        target_tree.fsdetect_cache = {
            os.path.normpath(k): v for k, v in fscache.items()
        }
        if snap_from_prescan:
            target_tree.child_snapshot = {
                os.path.normpath(k): set(v)
                for k, v in snap_from_prescan.items()
            }

        root_item = target_tree.topLevelItem(0)
        if root_item:
            target_tree.refresh_folder(root_item)
        try:
            target_tree.nb_refresh_all_dots()
        except Exception:
            pass
        target_tree.viewport().update()

        if target_tree is self.tree and getattr(self, "tree_mirror", None):
            self.tree_mirror.folder_has_child = self.tree.folder_has_child
            self.tree_mirror.fsdetect_cache = self.tree.fsdetect_cache
            if hasattr(self.tree, "child_snapshot"):
                self.tree_mirror.child_snapshot = self.tree.child_snapshot
            mirror_root = self.tree_mirror.topLevelItem(0)
            if mirror_root:
                self.tree_mirror.refresh_folder(mirror_root)
            try:
                self.tree_mirror.nb_refresh_all_dots()
            except Exception:
                pass
            self.tree_mirror.viewport().update()

        self._check_preparation_gate()


    def project_settings_path(self):
        from core.app_paths import PROJECT_DATA_DIR
        base = PROJECT_DATA_DIR
        os.makedirs(base, exist_ok=True)
        return os.path.join(base, "project_settings.json")

    def default_project_settings(self):
        return {
            "erofs": {
                "default": False,
                "method": "lz4hc",
                "level": 1
            },
            "brotli": {
                "default": True,
                "level": 1
            },
            "selinux": {
                "enabled": False,
                "ai": False,
                "plain": True
            },
            "super": {
                "norm": True
            },
            "terminal": {
                "spam_enter": False
            }
        }

    def _is_spam_enter_enabled(self) -> bool:
        try:
            data = self.ensure_project_settings()
            return bool(data.get("terminal", {}).get("spam_enter", False))
        except Exception:
            return False

    def _set_all_terminals_spam_enter(self, enabled: bool):
        for root_sessions in getattr(self, "terminal_sessions", {}).values():
            for term in root_sessions:
                if hasattr(term, "set_spam_enter"):
                    term.set_spam_enter(enabled)

    def _update_root_selector_display(self):
        if not hasattr(self, "root_selector"):
            return
        label = {"tree1": "1", "tree2": "2", "linux": "Linux"}[self.active_root_id]
        self.root_selector.setText(label)
        self.root_selector.setFixedWidth(70 if self.active_root_id == "linux" else 44)
        self.root_selector.setToolTip("Terminal mode: Linux (WSL)" if self.active_root_id == "linux" else "Terminal mode")
        self.root_selector.setIcon(create_themed_icon("arrow_open", _th.FG_MUTED, _th.FG_BRIGHT, 12))

    def _refresh_wsl_terminal_status(self):

        checked_at = self._wsl_probe_checked_at
        if self._wsl_probe_running or (checked_at is not None and time.monotonic() - checked_at < 30):
            return
        from core.threader import run_in_pool
        from core.wsl_manager import terminal_is_ready
        self._wsl_probe_running = True

        def _done(_token, ok, ready):
            self._wsl_probe_running = False
            self._wsl_probe_checked_at = time.monotonic()
            if getattr(self, "_closing", False):
                return
            self._wsl_ready = bool(ok and ready)
            self.update_root_selector()

        run_in_pool("terminal-wsl", lambda: (True, terminal_is_ready()), None, _done)

    def _terminal_root_items(self):
        items = [(os.path.basename(self.tree.base or self.p1 or "") or "Tree1", "tree1")]
        base1 = os.path.normcase(os.path.normpath(self.tree.base)) if self.tree.base else None
        base2 = os.path.normcase(os.path.normpath(self.tree2.base)) if self.tree2.base else None
        if self.p2 and base2 and base1 != base2:
            items.append((os.path.basename(self.tree2.base), "tree2"))
        if self._wsl_ready or self.terminal_sessions.get("linux"):
            items.append(("Linux", "linux"))
        return items

    def _terminal_root_base(self, root):
        if root == "linux":
            root = self._linux_source_root_id
        if root == "tree2" and self.tree2.base:
            return self.tree2.base
        return self.tree.base

    def _on_terminal_bar_spam_enter_toggled(self):
        enabled = self.toggle_terminal_spam_enter.isChecked()
        self._set_all_terminals_spam_enter(enabled)
        self._update_spam_enter_bar_ui(enabled)
        self._save_spam_enter_setting(enabled)

    def _update_spam_enter_bar_ui(self, enabled: bool):
        if not hasattr(self, "spam_enter_wrap"):
            return
        if hasattr(self, "toggle_terminal_spam_enter") and self.toggle_terminal_spam_enter.isChecked() != enabled:
            self.toggle_terminal_spam_enter.setChecked(enabled)

        if enabled:
            self.lbl_terminal_spam_enter.setStyleSheet(f"""
                color: {_th.FG_BLUE_LIGHT};
                font-family: 'Poppins', 'Inter', 'Segoe UI', sans-serif;
                font-size: 11px;
                font-weight: 600;
                background: transparent;
                border: none;
            """)
            self.spam_enter_wrap.setStyleSheet(f"""
                QWidget#terminalSpamEnterWrap {{
                    background: {_th.accent(0.14)};
                    border: 1px solid {_th.accent(0.38)};
                    border-radius: 6px;
                }}
                QWidget#terminalSpamEnterWrap:hover {{
                    background: {_th.accent(0.20)};
                    border-color: {_th.accent(0.55)};
                }}
            """)
        else:
            self.lbl_terminal_spam_enter.setStyleSheet(f"""
                color: {_th.FG_MUTED};
                font-family: 'Poppins', 'Inter', 'Segoe UI', sans-serif;
                font-size: 11px;
                font-weight: 500;
                background: transparent;
                border: none;
            """)
            self.spam_enter_wrap.setStyleSheet(f"""
                QWidget#terminalSpamEnterWrap {{
                    background: {_th.border_color(0.04)};
                    border: 1px solid {_th.border_color(0.09)};
                    border-radius: 6px;
                }}
                QWidget#terminalSpamEnterWrap:hover {{
                    background: {_th.border_color(0.08)};
                    border-color: {_th.border_color(0.18)};
                }}
            """)

    def _save_spam_enter_setting(self, enabled: bool):
        try:
            data = self.ensure_project_settings()
            data.setdefault("terminal", {})["spam_enter"] = enabled
            with open(self.project_settings_path(), "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            print(f"[Main] Warning: Failed to save spam_enter setting: {e}")

    def ensure_project_settings(self):
        path = self.project_settings_path()

        # belum ada → bikin
        if not os.path.isfile(path):
            with open(path, "w") as f:
                json.dump(self.default_project_settings(), f, indent=2)
            return self.default_project_settings()

        # ada tapi rusak → regen
        try:
            with open(path) as f:
                data = json.load(f)
        except Exception:
            with open(path, "w") as f:
                json.dump(self.default_project_settings(), f, indent=2)
            return self.default_project_settings()

        terminal = data.get("terminal") if isinstance(data, dict) else None
        if isinstance(terminal, dict) and "unix_bash" in terminal:
            terminal.pop("unix_bash")
            try:
                with open(path, "w") as f:
                    json.dump(data, f, indent=2)
            except OSError:
                pass
        return data


    def open_project_settings(self):
        from main_ext.class_WebToggle import WebToggle
        from PySide6.QtWidgets import QFrame

        data = self.ensure_project_settings()
        erofs_cfg   = data.get("erofs",   {})
        brotli_cfg  = data.get("brotli",  {})
        selinux_cfg = data.get("selinux", {})
        super_cfg   = data.get("super",   {})

        # ── dialog: frameless + translucent (AppDialog pattern) ──
        dlg = QDialog(self)
        dlg.setWindowTitle(t("main_project_settings"))
        dlg.setModal(True)
        dlg.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
        dlg.setAttribute(Qt.WA_TranslucentBackground)
        dlg.setStyleSheet("background:transparent;")

        outer = QVBoxLayout(dlg)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # Convert _th.BG2 → rgba dengan alpha (efek kaca transparan di atas BlurOverlay)
        _hex = _th.BG2.lstrip("#")
        _r, _g, _b = int(_hex[0:2], 16), int(_hex[2:4], 16), int(_hex[4:6], 16)

        card = QWidget()
        card.setObjectName("settings_card")
        card.setFixedSize(520, 540)
        card.setStyleSheet(f"""
            QWidget#settings_card {{
                background: {_th.panel_rgba(_r, _g, _b, 170)};
                border: 1px solid {_th.border_color(0.22)};
                border-radius: 14px;
            }}
            QWidget {{
                background: transparent;
                color: {_th.FG};
                font-family: 'Poppins', 'Inter', 'Segoe UI', sans-serif;
                font-size: 13px;
            }}
            QLabel {{
                background: transparent;
                border: none;
                color: {_th.FG};
            }}
            QScrollArea {{ background: transparent; border: none; }}
            QScrollBar:vertical {{
                background: transparent; width: 4px; margin: 0; border: none;
            }}
            QScrollBar::handle:vertical {{
                background: {_th.SCROLL_HANDLE}; border-radius: 2px; min-height: 20px;
            }}
            QScrollBar::handle:vertical:hover {{ background: {_th.accent(0.45)}; }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical,
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
                height: 0; background: transparent;
            }}
        """)
        outer.addWidget(card)

        root = QVBoxLayout(card)
        root.setContentsMargins(16, 16, 16, 14)
        root.setSpacing(10)

        # ── header ──
        hdr = QHBoxLayout()
        hdr.setSpacing(8)
        from icons.handler import icon as _icon
        ic = QLabel()
        ic.setPixmap(QIcon(_icon("set_project")).pixmap(15, 15))
        ic.setFixedSize(18, 18)
        ic.setAlignment(Qt.AlignVCenter)
        hdr_title = QLabel(t("main_project_settings"))
        hdr_title.setStyleSheet(f"font-size:14px; font-weight:600; color:{_th.FG}; background:transparent; border:none;")
        hdr.addWidget(ic)
        hdr.addWidget(hdr_title)
        hdr.addStretch()
        root.addLayout(hdr)

        # ── thin divider ──
        hdiv = QWidget()
        hdiv.setFixedHeight(1)
        hdiv.setStyleSheet(f"background: {_th.border_color(0.07)};")
        root.addWidget(hdiv)

        # ── scroll ──
        scroll = SmoothScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)

        content = QWidget()
        content.setStyleSheet("background: transparent;")
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 4, 0, 4)
        content_layout.setSpacing(10)
        scroll.setWidget(content)
        root.addWidget(scroll, 1)

        # ── helper: option row inside selinux body ──
        def build_option_row(title, subtitle, toggle):
            row = QWidget()
            row.setStyleSheet("background: transparent;")
            lay = QHBoxLayout(row)
            lay.setContentsMargins(0, 6, 0, 6)
            lay.setSpacing(8)

            enabled = toggle.isEnabled()
            title_color = _th.FG if enabled else _th.FG_MUTED
            desc_color  = _th.FG_DESC if enabled else _th.border_color(0.28)

            text = QVBoxLayout()
            t = QLabel(title)
            t.setStyleSheet(f"color:{title_color}; font-size:12px; font-weight:500; background:transparent; border:none;")
            s = QLabel(subtitle)
            s.setStyleSheet(f"color:{desc_color}; font-size:10px; background:transparent; border:none;")
            text.addWidget(t)
            text.addWidget(s)

            lay.addLayout(text)
            lay.addStretch()
            lay.addWidget(toggle)
            return row

        def thin_divider():
            d = QWidget()
            d.setFixedHeight(1)
            d.setStyleSheet(f"background: {_th.border_color(0.06)};")
            return d


        super_cfg = data.get("super", {})
        super_toggle = WebToggle(super_cfg.get("norm", True))
        super_card, _ = build_card(
            "Normalize SUPER",
            "Normalize partition slots from *_a and *_b to single image",
            super_toggle
        )
        content_layout.addWidget(super_card)


        erofs_cfg = data.get("erofs", {})
        erofs_toggle = WebToggle(erofs_cfg.get("default", True))
        erofs_card, erofs_body = build_card("EROFS", "Default", erofs_toggle)
        self.sync_card(erofs_toggle, erofs_body, erofs_cfg.get("default", True))

        # Custom dropdown (BlurCard popup) — replace QComboBox
        _method_items = ["lz4", "lz4hc", "lzma", "deflate", "zstd"]
        _method_state = {"value": erofs_cfg.get("method", "lz4")}

        _dropdown_ss = f"""
            QPushButton {{
                background: {_th.border_color(0.05)};
                border: 1px solid {_th.border_color(0.09)};
                border-radius: 6px;
                color: {_th.FG};
                font-size: 12px;
                padding: 0;
                text-align: left;
            }}
            QPushButton:hover {{
                background: {_th.border_color(0.09)};
                border-color: {_th.border_color(0.15)};
                color: {_th.FG_HOVER};
            }}
            QPushButton:pressed {{ background: {_th.border_color(0.13)}; }}
            QLabel {{ background: transparent; border: none; }}
        """

        # Custom button: label di kiri, panah file tree di kanan
        method = QPushButton()
        method.setFixedHeight(28)
        method.setFixedWidth(92)
        method.setCursor(Qt.PointingHandCursor)
        method.setStyleSheet(_dropdown_ss)

        _btn_lay = QHBoxLayout(method)
        _btn_lay.setContentsMargins(10, 0, 8, 0)
        _btn_lay.setSpacing(6)
        _method_label = QLabel(_method_state["value"])
        _method_label.setStyleSheet(f"color:{_th.FG}; background:transparent; border:none; font-size:12px;")
        _method_label.setAttribute(Qt.WA_TransparentForMouseEvents)
        _chev = QLabel()
        _chev.setPixmap(icon_pixmap("arrow_right").scaled(
            9, 9, Qt.KeepAspectRatio, Qt.SmoothTransformation,
        ).transformed(QTransform().rotate(90), Qt.SmoothTransformation))
        _chev.setFixedSize(14, 14)
        _chev.setAlignment(Qt.AlignCenter)
        _chev.setStyleSheet("background:transparent; border:none;")
        _chev.setAttribute(Qt.WA_TransparentForMouseEvents)
        _btn_lay.addWidget(_method_label)
        _btn_lay.addStretch()
        _btn_lay.addWidget(_chev)

        # API compat: shim currentText() supaya kode existing (line save) jalan
        method.currentText = lambda: _method_state["value"]

        def _on_method_pick(v):
            _method_state["value"] = v
            _method_label.setText(v)

        def _open_method_popup():
            self._show_blur_dropdown(method, _method_items, _method_state["value"], _on_method_pick)
        method.clicked.connect(_open_method_popup)

        lvl_slider = build_level_slider()
        lvl_slider.setValue(erofs_cfg.get("level", 6))
        lvl_value = QLabel(f"Level: {lvl_slider.value()}")
        lvl_slider.valueChanged.connect(lambda v: lvl_value.setText(f"Level: {v}"))

        method_row = QHBoxLayout()
        method_row.setContentsMargins(0, 0, 0, 0)
        method_row.setSpacing(8)
        method_row.addWidget(QLabel("Compression Method"))
        method_row.addStretch()
        method_row.addWidget(method)
        erofs_body.layout().addLayout(method_row)
        erofs_body.layout().addWidget(QLabel("Compression Level"))
        erofs_body.layout().addWidget(lvl_value)
        erofs_body.layout().addWidget(lvl_slider)
        erofs_toggle.clicked.connect(
            lambda: erofs_body.setVisible(not erofs_toggle.isChecked())
        )
        content_layout.addWidget(erofs_card)


        brotli_cfg = data.get("brotli", {})
        brotli_toggle = WebToggle(brotli_cfg.get("default", True))
        brotli_card, brotli_body = build_card("Brotli", "Default", brotli_toggle)

        brotli_slider = build_level_slider()
        brotli_slider.setValue(brotli_cfg.get("level", 4))
        brotli_value = QLabel(f"Level: {brotli_slider.value()}")
        brotli_slider.valueChanged.connect(lambda v: brotli_value.setText(f"Level: {v}"))

        brotli_body.layout().addWidget(QLabel("Compression Level"))
        brotli_body.layout().addWidget(brotli_value)
        brotli_body.layout().addWidget(brotli_slider)

        self.sync_card(brotli_toggle, brotli_body, brotli_cfg.get("default", True))
        brotli_toggle.clicked.connect(
            lambda: brotli_body.setVisible(not brotli_toggle.isChecked())
        )
        content_layout.addWidget(brotli_card)


        selinux_cfg = data.get("selinux", {})

        selinux_toggle = WebToggle(selinux_cfg.get("enabled", False))
        selinux_card, selinux_body = build_card(
            "SELinux Patch", "Apply SELinux Patch", selinux_toggle
        )

        # AI neocat Model 1.0 sementara di-disable → toggle mati & tidak bisa diklik, otomatis Plain Context
        ai_toggle    = WebToggle(False)
        ai_toggle.setEnabled(False)
        plain_toggle = WebToggle(True)
        plain_toggle.setEnabled(False)

        ai_row    = build_option_row(
            "AI neocat Model 1.0",
            "Enhanced contextual accuracy with intelligent policy handling",
            ai_toggle
        )
        plain_row = build_option_row(
            "Plain Context",
            "Faster context processing with minimal overhead",
            plain_toggle
        )

        selinux_body.layout().addWidget(ai_row)
        selinux_body.layout().addWidget(thin_divider())
        selinux_body.layout().addWidget(plain_row)

        def update_selinux_ui():
            selinux_body.setVisible(selinux_toggle.isChecked())

        selinux_toggle.clicked.connect(update_selinux_ui)
        update_selinux_ui()

        content_layout.addWidget(selinux_card)

        content_layout.addStretch()

        # ── footer divider ──
        fdiv = QWidget()
        fdiv.setFixedHeight(1)
        fdiv.setStyleSheet(f"background: {_th.border_color(0.07)};")
        root.addWidget(fdiv)

        # ── footer buttons ──
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        btn_row.addStretch()

        btn_cancel = QPushButton(t("main_btn_cancel"))
        btn_cancel.setFixedHeight(30)
        btn_cancel.setCursor(Qt.PointingHandCursor)
        btn_cancel.setStyleSheet(f"""
            QPushButton {{
                background: {_th.border_color(0.06)};
                border: 1px solid {_th.border_color(0.10)};
                border-radius: 6px;
                color: {_th.FG3};
                font-size: 12px;
                padding: 0 14px;
                height: 30px;
            }}
            QPushButton:hover {{ background: {_th.border_color(0.10)}; border-color: {_th.border_color(0.18)}; color: {_th.FG_HOVER}; }}
            QPushButton:pressed {{ background: {_th.border_color(0.14)}; }}
        """)
        btn_cancel.clicked.connect(dlg.reject)

        btn_apply = QPushButton(t("main_btn_apply"))
        btn_apply.setFixedHeight(30)
        btn_apply.setCursor(Qt.PointingHandCursor)
        btn_apply.setStyleSheet(f"""
            QPushButton {{
                background: {_th.accent(0.18)};
                border: 1px solid {_th.accent(0.38)};
                border-radius: 6px;
                color: {_th.FG_BLUE};
                font-size: 12px;
                font-weight: 600;
                padding: 0 18px;
                height: 30px;
            }}
            QPushButton:hover {{ background: {_th.accent(0.32)}; border-color: {_th.accent(0.60)}; color: {_th.FG_BLUE_LIGHT}; }}
            QPushButton:pressed {{ background: {_th.accent(0.48)}; }}
        """)

        def apply():
            out = {
                "erofs": {
                    "default": erofs_toggle.isChecked(),
                    "method": method.currentText(),
                    "level": lvl_slider.value()
                },
                "brotli": {
                    "default": brotli_toggle.isChecked(),
                    "level": brotli_slider.value()
                },
                "selinux": {
                    "enabled": selinux_toggle.isChecked(),
                    "ai": ai_toggle.isChecked(),
                    "plain": plain_toggle.isChecked()
                },
                "super": {
                    "norm": super_toggle.isChecked()
                },
                "terminal": {
                    "spam_enter": self._is_spam_enter_enabled()
                }
            }
            with open(self.project_settings_path(), "w") as f:
                json.dump(out, f, indent=2)
            dlg.accept()

        btn_apply.clicked.connect(apply)

        btn_row.addWidget(btn_cancel)
        btn_row.addWidget(btn_apply)
        root.addLayout(btn_row)

        from app_ext.class_BlurOverlay import install_window_fade
        _ov = _BlurOverlay(self)
        dlg._overlay = _ov
        install_window_fade(dlg)
        dlg.exec()
        _ov.close_overlay()

    def sync_card(self, toggle, body, default_value: bool):

        toggle.blockSignals(True)
        toggle.setChecked(default_value)
        toggle.blockSignals(False)

        body.setVisible(not default_value)

    def save_project_settings(self, dlg, erofs_on, level, selinux_on):
        path = self.project_settings_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)

        data = {
            "erofs": {
                "enabled": erofs_on,
                "level": level
            },
            "selinux_patch": selinux_on
        }

        with open(path, "w") as f:
            json.dump(data, f, indent=2)

        dlg.accept()

    def update_root_selector_visibility(self):
        self.update_root_selector()


    def toggle_terminal(self):
        self._refresh_wsl_terminal_status()


        if self.terminal_wrap.isVisible():
            self.terminal_wrap.hide()
            self.terminal_was_open = False

            # jika tidak ada editor → area kanan hilang total
            if self.editor_panel.tab_count() == 0:
                self.right_wrap.hide()
                self.editor_split.widget(0).hide()
                self.editor_split.handle(1).setEnabled(False)
                self._check_restore_tree_mode()
                self.update_smartbar_visibility()
                return

            # jika ada editor → editor full
            self.right_wrap.show()
            self.editor_split.widget(0).show()
            self.editor_split.handle(1).setEnabled(True)
            self.editor_split.setSizes([1, 0])
            self.update_smartbar_visibility()
            return


        self.terminal_was_open = True
        self.right_wrap.show()

        root = self.active_root_id
        if len(self.terminal_sessions[root]) == 0:
            self.create_terminal_session()
        else:
            self.refresh_terminal_tabs()

        # CASE: TIDAK ADA EDITOR
        if self.editor_panel.tab_count() == 0:
            self.editor_split.widget(0).hide()
            self.editor_split.handle(1).setEnabled(False)
            self.terminal_wrap.show()
            self.editor_split.setSizes([0, 1])

            if self.tree_mode == 2:
                self.tree_mode = 3
                self.apply_tree_mode()
            self.update_smartbar_visibility()
            return

        # CASE: ADA EDITOR
        self.editor_split.widget(0).show()
        self.editor_split.handle(1).setEnabled(True)
        self.terminal_wrap.show()

        # default split
        self.editor_split.setSizes([1, 1])


    def show_session_menu(self):
        menu = QMenu(self)
        for label, root in self._terminal_root_items():
            action = menu.addAction(label)
            action.triggered.connect(lambda _, rid=root: self.switch_root(rid))

        menu.exec(QCursor.pos())

    def show_root_menu(self):
        self._refresh_wsl_terminal_status()
        from app_ext.class_BlurOverlay import BlurCard
        from PySide6.QtCore import QEventLoop, QPoint
        from PySide6.QtGui import QColor as _QC

        popup = QWidget(None, Qt.Popup | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint)
        popup.setAttribute(Qt.WA_TranslucentBackground)
        popup.setAttribute(Qt.WA_DeleteOnClose)

        outer = QVBoxLayout(popup)
        outer.setContentsMargins(0, 0, 0, 0)

        _br, _bg, _bb = _th._palette["_border_rgb"]
        _tint = _QC(_th.BG2); _tint.setAlpha(128)
        card = BlurCard(radius=12.0, tint=_tint,
                        border=_QC(_br, _bg, _bb, int(0.18 * 255)))
        outer.addWidget(card)

        lay = QVBoxLayout(card)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(1)

        items = self._terminal_root_items()

        chosen = [None]
        for label, root_id in items:
            active = (root_id == self.active_root_id)
            b = QPushButton(label)
            b.setFixedHeight(32)
            b.setCursor(Qt.PointingHandCursor)
            fg = _th.FG_BLUE if active else _th.FG
            fw = "600" if active else "400"
            b.setStyleSheet(f"""
                QPushButton {{
                    background: {"" + _th.accent(0.12) if active else "transparent"};
                    border: none; border-radius: 6px;
                    color: {fg}; font-size: 12px; font-weight: {fw};
                    text-align: left; padding: 0 14px;
                }}
                QPushButton:hover {{ background: {_th.accent(0.14)}; color: {_th.FG_BRIGHT}; }}
                QPushButton:pressed {{ background: {_th.border_color(0.08)}; }}
            """)
            def _pick(_, rid=root_id):
                chosen[0] = rid
                popup.close()
            b.clicked.connect(_pick)
            lay.addWidget(b)

        card.adjustSize()
        popup.adjustSize()

        anchor = self.root_selector
        global_pos = anchor.mapToGlobal(anchor.rect().bottomLeft())
        global_pos.setY(global_pos.y() + 4)

        # clamp biar nggak bocor keluar window
        hint = popup.sizeHint()
        pw = hint.width() if hint.width() > 0 else popup.width()
        ph = hint.height() if hint.height() > 0 else popup.height()
        tl = self.mapToGlobal(self.rect().topLeft())
        br = self.mapToGlobal(self.rect().bottomRight())
        x = global_pos.x() if global_pos.x() + pw <= br.x() else max(tl.x(), br.x() - pw)
        y = global_pos.y() if global_pos.y() + ph <= br.y() else max(tl.y(), br.y() - ph)
        popup.move(QPoint(x, y))

        card.freeze(self, popup.pos())
        popup.show()

        loop = QEventLoop()
        popup.destroyed.connect(loop.quit)
        loop.exec()

        if chosen[0]:
            self.switch_root(chosen[0])

    def _show_blur_dropdown(self, anchor, items, current, on_pick):

        from app_ext.class_BlurOverlay import BlurCard
        from PySide6.QtCore import QEventLoop, QPoint
        from PySide6.QtGui import QColor as _QC

        popup = QWidget(None, Qt.Popup | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint)
        popup.setAttribute(Qt.WA_TranslucentBackground)
        popup.setAttribute(Qt.WA_DeleteOnClose)

        outer = QVBoxLayout(popup)
        outer.setContentsMargins(0, 0, 0, 0)

        _br, _bg, _bb = _th._palette["_border_rgb"]
        _tint = _QC(_th.BG2); _tint.setAlpha(128)
        card = BlurCard(radius=12.0, tint=_tint,
                        border=_QC(_br, _bg, _bb, int(0.18 * 255)))
        outer.addWidget(card)

        lay = QVBoxLayout(card)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(1)

        chosen = [None]
        for value in items:
            active = (value == current)
            b = QPushButton(value)
            b.setFixedHeight(30)
            b.setCursor(Qt.PointingHandCursor)
            fg = _th.FG_BLUE if active else _th.FG
            fw = "600" if active else "400"
            b.setStyleSheet(f"""
                QPushButton {{
                    background: {"" + _th.accent(0.12) if active else "transparent"};
                    border: none; border-radius: 6px;
                    color: {fg}; font-size: 12px; font-weight: {fw};
                    text-align: left; padding: 0 14px;
                }}
                QPushButton:hover {{ background: {_th.accent(0.14)}; color: {_th.FG_BRIGHT}; }}
                QPushButton:pressed {{ background: {_th.border_color(0.08)}; }}
            """)
            def _pick(_, v=value):
                chosen[0] = v
                popup.close()
            b.clicked.connect(_pick)
            lay.addWidget(b)

        card.adjustSize()
        popup.adjustSize()
        # Lebar minimum mengikuti anchor button
        if popup.width() < anchor.width():
            popup.setFixedWidth(anchor.width())

        global_pos = anchor.mapToGlobal(anchor.rect().bottomLeft())
        global_pos.setY(global_pos.y() + 4)


        anchor_win = anchor.window()

        # Clamp ke anchor's top-level window (settings dialog)
        hint = popup.sizeHint()
        pw = hint.width() if hint.width() > 0 else popup.width()
        ph = hint.height() if hint.height() > 0 else popup.height()
        tl = anchor_win.mapToGlobal(anchor_win.rect().topLeft())
        br = anchor_win.mapToGlobal(anchor_win.rect().bottomRight())
        x = global_pos.x() if global_pos.x() + pw <= br.x() else max(tl.x(), br.x() - pw)
        y = global_pos.y() if global_pos.y() + ph <= br.y() else max(tl.y(), br.y() - ph)
        popup.move(QPoint(x, y))

        card.freeze(self, popup.pos())
        popup.show()

        loop = QEventLoop()
        popup.destroyed.connect(loop.quit)
        loop.exec()

        if chosen[0] is not None:
            on_pick(chosen[0])

    def switch_root(self, root_id):
        if root_id not in {rid for _, rid in self._terminal_root_items()}:
            return
        previous_root = self.active_root_id
        if root_id == "linux" and previous_root in ("tree1", "tree2"):
            self._linux_source_root_id = previous_root
        self.active_root_id = root_id

        # update label path
        self.root_label.setText(os.path.basename(self._terminal_root_base(root_id) or ""))

        # UPDATE DROPDOWN
        self.update_root_selector()

        # refresh sessions
        self.refresh_terminal_tabs()

        # auto create session kalau kosong
        if len(self.terminal_sessions[self.active_root_id]) == 0:
            if self.create_terminal_session() is None:
                self.active_root_id = previous_root
                self.root_label.setText(os.path.basename(self._terminal_root_base(previous_root) or ""))
                self.update_root_selector()
                self.refresh_terminal_tabs()

    def _index_to_alpha(self, idx):

        result = ""
        while idx > 0:
            idx -= 1
            result = chr(65 + (idx % 26)) + result
            idx //= 26
        return result

    def _reveal_editor_pane(self):

        was_hidden = not self.right_wrap.isVisible()

        self.right_wrap.show()
        self.editor_split.widget(0).show()
        self.editor_split.handle(1).setEnabled(True)

        if self.tree_mode == 2:
            self.tree_mode = 3
            self.apply_tree_mode()

        if was_hidden:
            total_w = self.width()
            left_w = min(280, max(220, int(total_w * 0.28)))
            right_w = max(400, total_w - left_w)
            self.splitter.setSizes([left_w, right_w])

    def open_payload_tab(self, filepath):
        from core import payload_tool

        norm = os.path.normpath(filepath)

        # Cek kalau tab sudah ada
        existing = self.unified_tabs.find_aux_by_filepath(filepath, PayloadViewer)
        if existing >= 0:
            self.unified_tabs.switch_to_aux_tab(existing)
            self._reveal_editor_pane()
            return

        # Guard double-click
        if not hasattr(self, "_payload_opening"):
            self._payload_opening = set()
        if norm in self._payload_opening:
            return
        self._payload_opening.add(norm)

        # Tab dibuka langsung (list kosong); daftar partisi di-load async biar
        # UI gak freeze pas baca payload.bin besar.
        widget = PayloadViewer(filepath, partitions=None, loading_handler=self)

        self._reveal_editor_pane()

        self.unified_tabs.add_aux_tab(widget, os.path.basename(filepath), filepath=filepath)
        self.update_smartbar_visibility()

        def _worker():
            try:
                return True, payload_tool.list_partitions(filepath)
            except Exception as e:
                return False, str(e)

        def _after(ok, result):
            self._payload_opening.discard(norm)
            if not ok:
                AppDialog.warning(self, t("main_payload_error"), str(result))
                self.unified_tabs.close_aux_tab_widget(widget)
                return
            widget.set_partitions(result)

        run_in_thread(_worker, None, callback=_after)

    def open_super_tab(self, img_path):
        from main_ext.class_SuperViewer import SuperViewer

        viewer = SuperViewer(img_path, loading_handler=self)

        self._reveal_editor_pane()

        self.unified_tabs.add_aux_tab(viewer, os.path.basename(img_path), filepath=img_path)
        self.update_smartbar_visibility()

    def open_image_tab(self, filepath, read_only=False):
        from main_ext.class_ImageViewer import ImageViewer

        # Dedup: kalau tab gambar untuk file ini sudah ada, switch saja.
        existing = self.unified_tabs.find_aux_by_filepath(filepath, ImageViewer)
        if existing >= 0:
            self.unified_tabs.switch_to_aux_tab(existing)
            self._reveal_editor_pane()
            return

        viewer = ImageViewer(filepath, loading_handler=self)
        if read_only and hasattr(viewer, "set_read_only"):
            viewer.set_read_only(True)

        self._reveal_editor_pane()

        self.unified_tabs.add_aux_tab(viewer, os.path.basename(filepath), filepath=filepath)
        self.update_smartbar_visibility()

    def open_audio_tab(self, filepath, read_only=False):
        from main_ext.class_AudioViewer import AudioViewer

        # Dedup: kalau tab audio untuk file ini sudah ada, switch saja.
        existing = self.unified_tabs.find_aux_by_filepath(filepath, AudioViewer)
        if existing >= 0:
            self.unified_tabs.switch_to_aux_tab(existing)
            self._reveal_editor_pane()
            return

        viewer = AudioViewer(filepath, loading_handler=self)
        if read_only and hasattr(viewer, "set_read_only"):
            viewer.set_read_only(True)

        self._reveal_editor_pane()

        self.unified_tabs.add_aux_tab(viewer, os.path.basename(filepath), filepath=filepath)
        self.update_smartbar_visibility()

    def open_video_tab(self, filepath, read_only=False):
        from main_ext.class_VideoViewer import VideoViewer

        # Dedup: kalau tab video untuk file ini sudah ada, switch saja.
        existing = self.unified_tabs.find_aux_by_filepath(filepath, VideoViewer)
        if existing >= 0:
            self.unified_tabs.switch_to_aux_tab(existing)
            self._reveal_editor_pane()
            return

        viewer = VideoViewer(filepath, loading_handler=self)
        if read_only and hasattr(viewer, "set_read_only"):
            viewer.set_read_only(True)

        self._reveal_editor_pane()

        self.unified_tabs.add_aux_tab(viewer, os.path.basename(filepath), filepath=filepath)
        self.update_smartbar_visibility()

    # ── MOUNT: buka file teks di Monaco (read-only kalau EROFS/sparse; EXT editable) ──
    def open_mount_file_in_editor(self, img_path, internal, read_only=False):

        from core import imgfs
        from core.threader import run_in_thread

        internal = internal.replace("\\", "/").strip("/")

        # Validasi format teks di awal (tanpa menyentuh disk)
        if not imgfs.is_text_filename(internal):
            self._toast_or_print(f"'{os.path.basename(internal)}' bukan file teks — tidak dibuka")
            return

        # Ambil ino/nid dari index mount jika tersedia (fast direct lookup)
        t = getattr(self, "active_tree", None)
        ino = None
        if t and hasattr(t, "_ensure_mounts"):
            key = os.path.normpath(img_path)
            index = t._ensure_mounts().get(key, {})
            parent = internal.rsplit("/", 1)[0] if "/" in internal else ""
            name = internal.rsplit("/", 1)[-1]
            for child in index.get(parent, []):
                if child.get("name") == name:
                    ino = child.get("ino")
                    break

        label = f"open-mount-{os.path.normpath(img_path)}-{internal}"
        if hasattr(self, "push_task"):
            try:
                self.push_task(label)
            except Exception:
                pass

        def work():
            try:
                if ino is not None:
                    data = imgfs.read_ino(img_path, ino)
                else:
                    data, _ = imgfs.read_path(img_path, internal)
            except Exception as ex:
                return False, str(ex)

            if not imgfs.is_text_bytes(data):
                return False, "is_binary"

            text, _enc = imgfs.decode_text(data)
            return True, text

        def on_done(ok, payload):
            if hasattr(self, "pop_task"):
                try:
                    self.pop_task(label)
                except Exception:
                    pass

            if not ok:
                if payload == "is_binary":
                    self._toast_or_print(f"'{os.path.basename(internal)}' adalah binary — tidak dibuka")
                else:
                    self._toast_or_print(f"Gagal baca '{os.path.basename(internal)}': {payload}")
                return

            text = payload
            key = os.path.normpath(img_path)
            tab_id = f"mount://{key}//{internal}"
            title = os.path.basename(internal)

            on_save = None
            if not read_only:
                def on_save(_tab_id, content, _img=img_path, _int=internal):
                    self._save_mount_text(_img, _int, content, _tab_id)

            self._reveal_editor_pane()
            from main_ext.class_WebEditorPanel import _detect_language
            lang = _detect_language(internal)
            self.unified_tabs.open_virtual(tab_id, title, text, language=lang,
                                           read_only=read_only, on_save=on_save)
            self.update_smartbar_visibility()

        run_in_thread(work, None, callback=on_done)

    def _save_mount_text(self, img_path, internal, content, tab_id):

        from core import imgfs
        from core.threader import run_in_thread
        from main_ext.class_AppDialog import AppDialog

        raw = content.encode("utf-8")

        def work():
            try:
                imgfs.write_path_data(img_path, internal, raw)
                return True, len(raw)
            except Exception as e:
                return False, e

        def on_done(ok, payload):
            if ok:
                self._toast_or_print(f"Tersimpan ke image: {os.path.basename(internal)}")
                t = getattr(self, "active_tree", None)
                if t and hasattr(t, "patch_mount_entry"):
                    t.patch_mount_entry(img_path, internal, size=payload)
            else:
                self._toast_or_print(f"Gagal simpan (mount): {payload}")
                try:
                    AppDialog.warning(self, "Tidak bisa simpan", str(payload))
                except Exception:
                    pass

        run_in_thread(work, None, callback=on_done)

    def _toast_or_print(self, msg):
        try:
            if hasattr(self, "show_toast"):
                self.show_toast(msg); return
        except Exception:
            pass
        print("[MOUNT]", msg)

    def open_archive_tab(self, filepath):
        t = self.active_tree
        icons = {
            "folder":      t.icon_folder,
            "file":        t.icon_file,
            "image":       t.icon_image,
            "apk":         t.icon_apk,
            "archive":     t.icon_archive,
            "archive_jar": t.icon_archive_jar,
            "archive_7z":  t.icon_archive_7z,
            "archive_tar": t.icon_archive_tar,
            "archive_rar": t.icon_archive_rar,
            "archive_gz":  t.icon_archive_gz,
            "archive_iso": t.icon_archive_iso,
            "txt":         t.icon_txt,
            "bin":         t.icon_bin,
            "drive":       t.icon_drive,
            "audio":       t.icon_audio,
            "video":       t.icon_video,
            "json":        t.icon_json,
            "xml":         t.icon_xml,
            "cfg":         t.icon_cfg,
            "csv":         t.icon_csv,
            "py":          t.icon_py,
            "cpp":         t.icon_cpp,
            "c":           t.icon_c,
            "cs":          t.icon_cs,
            "java":        t.icon_java,
            "js":          t.icon_js,
            "html":        t.icon_html,
            "css":         t.icon_css,
            "qss":         t.icon_qss,
            "h":           t.icon_h,
            "hpp":         t.icon_hpp,
            "rust":        t.icon_rust,
            "php":         t.icon_php,
            "kt":          t.icon_kt,
            "smali":       t.icon_smali,
            "sh":          t.icon_sh,
            "zsh":         t.icon_zsh,
            "fish":        t.icon_fish,
            "prop":        t.icon_prop,
            "rc":          t.icon_rc,
            "asm":         t.icon_asm,
            "swift":       t.icon_swift,
            "linux_meta":  t.icon_linux_meta,
        }

        widget = ArchiveTab(filepath, icons)

        self._reveal_editor_pane()

        self.unified_tabs.add_aux_tab(widget, os.path.basename(filepath), filepath=filepath)
        self.update_smartbar_visibility()

    def open_so_tab(self, filepath):
        from main_ext.class_SoViewer import SoViewer

        norm = os.path.normpath(filepath)

        # Dedup: kalau tab untuk file .so ini sudah ada, switch saja
        existing = self.unified_tabs.find_aux_by_filepath(filepath, SoViewer)
        if existing >= 0:
            self.unified_tabs.switch_to_aux_tab(existing)
            self._reveal_editor_pane()
            return

        viewer = SoViewer(filepath, loading_handler=self)

        self._reveal_editor_pane()

        self.unified_tabs.add_aux_tab(viewer, os.path.basename(filepath), filepath=filepath)
        self.update_smartbar_visibility()

    def open_bin_tab(self, filepath):
        from main_ext.class_HexViewer import HexViewer

        norm = os.path.normpath(filepath)

        # Dedup: kalau tab untuk file binary ini sudah ada, switch saja
        existing = self.unified_tabs.find_aux_by_filepath(filepath, HexViewer)
        if existing >= 0:
            self.unified_tabs.switch_to_aux_tab(existing)
            self._reveal_editor_pane()
            return

        viewer = HexViewer(filepath, loading_handler=self)

        self._reveal_editor_pane()

        self.unified_tabs.add_aux_tab(viewer, os.path.basename(filepath), filepath=filepath)
        self.update_smartbar_visibility()


    def _generate_unique_terminal_tab_name(self, base_name: str, current_term=None, root=None) -> str:
        root = root or self.active_root_id
        if current_term is not None:
            root = next((rid for rid, sessions in self.terminal_sessions.items() if current_term in sessions), root)
        existing_names = set()
        for t in self.terminal_sessions.get(root, []):
            if t is not current_term:
                name = getattr(t, "_terminal_tab_name", None)
                if name:
                    existing_names.add(name)

        if base_name not in existing_names:
            return base_name

        i = 2
        while f"{base_name} {i}" in existing_names:
            i += 1
        return f"{base_name} {i}"

    def _on_terminal_first_command(self, term, cmd: str):
        if not term:
            return
        base = extract_terminal_command_name(cmd)
        new_name = self._generate_unique_terminal_tab_name(base, current_term=term)
        term._terminal_tab_name = new_name

        for i in range(self.session_tabs.count()):
            if self.session_tabs.tabData(i) is term:
                self.session_tabs.setTabText(i, new_name)
                self._apply_terminal_close_button(i)
                break

    def create_terminal_session(self):
        import random
        root = self.active_root_id   # tree1 / tree2 / linux

        tab_name = self._generate_unique_terminal_tab_name("New Session")

        # Buat terminal baru
        root_base = self._terminal_root_base(root)
        try:
            term = Terminal(
                cwd=root_base,
                spam_enter=self._is_spam_enter_enabled(),
                bash_mode=False,
                wsl_mode=(root == "linux"),
            )
        except Exception as exc:
            AppDialog.error(self, "Terminal", str(exc))
            return None
        term._terminal_tab_name = tab_name
        term.firstCommandSubmitted.connect(lambda cmd, t=term: self._on_terminal_first_command(t, cmd))

        # Assign random shell icon — stored on widget so refresh can reuse it
        term._tab_icon_key = random.choice(["icon_sh", "icon_zsh", "icon_fish"])

        # BLOCK updates untuk mencegah flash effect
        self.terminal_wrap.setUpdatesEnabled(False)

        # Hide widget baru IMMEDIATELY
        term.hide()

        # Simpan ke session group root ini
        self.terminal_sessions[root].append(term)
        self.terminal_session_index[root] += 1

        # Tampilkan di TabBar
        idx = self.session_tabs.addTab(tab_name)
        self.session_tabs.setTabData(idx, term)
        self.session_tabs.setTabIcon(idx, QIcon(icon(term._tab_icon_key)))
        self._apply_terminal_close_button(idx)

        # HIDE semua terminal lain
        for rr in self.terminal_sessions.values():
            for t in rr:
                if t != term:
                    t.hide()

        # Add ke layout (widget sudah hidden, updates blocked)
        self.terminal_wrap.layout().addWidget(term)

        # Set current index
        self.session_tabs.setCurrentIndex(idx)

        # Switch terminal (akan show widget)
        self.switch_terminal_session(term)

        # RE-ENABLE updates SETELAH semuanya settled
        self.terminal_wrap.setUpdatesEnabled(True)
        self.terminal_wrap.update()
        return term

    def refresh_terminal_tabs(self):
        was_blocked = self.session_tabs.blockSignals(True)
        # SEMUA terminal di-hide dulu
        for rr in self.terminal_sessions.values():
            for t in rr:
                t.hide()

        # HAPUS TAB SATU-PER-SATU
        while self.session_tabs.count() > 0:
            self.session_tabs.removeTab(0)

        root = self.active_root_id
        sessions = self.terminal_sessions[root]

        # REBUILD TAB SESUAI ROOT YANG AKTIF
        for i, term in enumerate(sessions):
            name = getattr(term, "_terminal_tab_name", None)
            if not name:
                name = self._generate_unique_terminal_tab_name("New Session", current_term=term)
                term._terminal_tab_name = name
            idx = self.session_tabs.addTab(name)
            self.session_tabs.setTabData(idx, term)
            icon_key = getattr(term, "_tab_icon_key", "icon_sh")
            self.session_tabs.setTabIcon(idx, QIcon(icon(icon_key)))
            self._apply_terminal_close_button(idx)

        # TAMPILKAN SESSION AKTIF / TERAKHIR
        if sessions:
            target_widget = self.active_terminal_widget if self.active_terminal_widget in sessions else sessions[-1]
            target_idx = 0
            for i in range(self.session_tabs.count()):
                if self.session_tabs.tabData(i) is target_widget:
                    target_idx = i
                    break
            self.session_tabs.setCurrentIndex(target_idx)
            self.switch_terminal_session(target_widget)
        else:
            self.active_terminal_widget = None
        self.session_tabs.blockSignals(was_blocked)

    def _select_terminal_session(self, term):
        if not term:
            return
        for i in range(self.session_tabs.count()):
            if self.session_tabs.tabData(i) is term:
                self.session_tabs.setCurrentIndex(i)
                self.switch_terminal_session(term)
                return

    def _on_terminal_tab_changed(self, index):
        widget = self.session_tabs.tabData(index)
        if widget:
            self.switch_terminal_session(widget)

    def switch_terminal_session(self, widget):
        if hasattr(self, "active_terminal_widget") and self.active_terminal_widget:
            self.active_terminal_widget.hide()

        self.active_terminal_widget = widget
        widget.show()
        if hasattr(widget, "setFocus"):
            widget.setFocus()

    def add_session_tab(self, session):
        idx = self.session_tabs.addTab(session.name)
        self.session_tabs.setTabData(idx, session)
        self.session_tabs.setCurrentIndex(idx)
        self._apply_terminal_close_button(idx)

    def _apply_terminal_close_button(self, index):
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
        term = self.session_tabs.tabData(index)
        btn.clicked.connect(lambda _, t=term: self._close_terminal_tab_by_term(t))
        self.session_tabs.setTabButton(index, QTabBar.RightSide, btn)

    def _close_terminal_tab_by_term(self, term):
        if not term:
            return
        for i in range(self.session_tabs.count()):
            if self.session_tabs.tabData(i) is term:
                self.close_terminal_tab_by_index(i)
                return

    def close_terminal_tab_by_index(self, index):
        root = self.active_root_id

        widget = self.session_tabs.tabData(index)
        if widget is None:
            return

        # Remove UI tab
        self.session_tabs.removeTab(index)

        # Remove from session storage
        if widget in self.terminal_sessions[root]:
            self.terminal_sessions[root].remove(widget)
        if self.active_terminal_widget is widget:
            self.active_terminal_widget = None
        for sessions in getattr(self, "file_run_terminal_sessions", {}).values():
            for path, term in list(sessions.items()):
                if term is widget:
                    del sessions[path]

        # Kill terminal widget
        # Kill process dulu (WAJIB)
        if hasattr(widget, "shutdown"):
            widget.shutdown()

        widget.setParent(None)
        widget.deleteLater()


        widget = None


        if not any(self.terminal_sessions.values()):
            self.terminal_wrap.hide()
            self.active_terminal_widget = None

            if self.editor_panel.tab_count() == 0:
                self.right_wrap.hide()
                self.editor_split.widget(0).hide()
                self.editor_split.handle(1).setEnabled(False)
                self._check_restore_tree_mode()
            else:
                self.editor_split.widget(0).show()
                self.editor_split.setSizes([1, 0])
            self.update_smartbar_visibility()
            return


        if len(self.terminal_sessions[root]) == 0:
            # Pindah ke root lain yang masih punya sesi
            for other_root, sessions in self.terminal_sessions.items():
                if sessions:
                    self.switch_root(other_root)
                    return


        self.refresh_terminal_tabs()

    def _path_in_base(self, filepath, base):
        if not filepath or not base:
            return False
        path = os.path.normcase(os.path.normpath(filepath))
        root = os.path.normcase(os.path.normpath(base))
        return path == root or path.startswith(root + os.sep)

    def _root_for_editor_file(self, filepath):
        if self._path_in_base(filepath, getattr(self.tree2, "base", None)):
            return "tree2"
        if self._path_in_base(filepath, getattr(self.tree, "base", None)):
            return "tree1"
        if (
            getattr(self, "active_tree", None) is getattr(self, "tree2", None)
            and getattr(self.tree2, "base", None)
        ):
            return "tree2"
        return "tree1"

    def _set_terminal_root_for_run(self, root):
        self.active_root_id = root
        self._linux_source_root_id = root
        try:
            self.root_label.setText(os.path.basename(
                self.tree.base if root == "tree1" else self.tree2.base
            ))
        except Exception:
            pass
        self.update_root_selector()
        self.refresh_terminal_tabs()

    def _show_terminal_for_run(self):
        self.terminal_was_open = True
        self.right_wrap.show()
        self.terminal_wrap.show()
        if self.editor_panel.tab_count() == 0:
            self.editor_split.widget(0).hide()
            self.editor_split.handle(1).setEnabled(False)
            self.editor_split.setSizes([0, 1])
        else:
            self.editor_split.widget(0).show()
            self.editor_split.handle(1).setEnabled(True)
            self.editor_split.setSizes([1, 1])
        if self.tree_mode == 2:
            self.tree_mode = 3
            self.apply_tree_mode()
        self.update_smartbar_visibility()

    def run_editor_file(self, filepath):
        if not is_runnable_file(filepath):
            return
        command = build_run_command(filepath)
        if not command:
            return

        root = self._root_for_editor_file(filepath)
        norm = os.path.normcase(os.path.normpath(filepath))
        sessions = self.file_run_terminal_sessions.setdefault(root, {})
        term = sessions.get(norm)

        if term is None or term not in self.terminal_sessions[root]:
            cwd = os.path.dirname(filepath) or (
                self.tree.base if root == "tree1" else self.tree2.base
            )
            term = Terminal(
                cwd=cwd,
                spam_enter=self._is_spam_enter_enabled(),
                bash_mode=False,
            )
            term._tab_icon_key = "start"
            term._terminal_tab_name = self._generate_unique_terminal_tab_name(os.path.basename(filepath), root=root)
            term.firstCommandSubmitted.connect(lambda cmd, t=term: self._on_terminal_first_command(t, cmd))
            term.hide()
            self.terminal_sessions[root].append(term)
            self.terminal_wrap.layout().addWidget(term)
            sessions[norm] = term

        self._set_terminal_root_for_run(root)
        self._select_terminal_session(term)
        self._show_terminal_for_run()
        term.run_command_silent(command)

    def open_file_in_editor(self, filepath, force=False, source_path=None, source_tree=None):


        # FIX TERMINAL MODE NYANGKUT
        if self.terminal_wrap.isVisible() and not self.editor_split.widget(0).isVisible():
            self.editor_split.widget(0).show()
            self.editor_split.handle(1).setEnabled(True)
            self.editor_split.setSizes([700, 300])

        name_only = os.path.basename(filepath)
        ext = os.path.splitext(filepath)[1].lower()

        # 🔥 ALLOW TEXT + FS METADATA
        is_meta = (
            name_only.endswith("_file_contexts") or
            name_only.endswith("_fs_config") or
            name_only.endswith("_fs_options") or
            name_only.endswith("_info")
        )

        allowed = [
            ".json", ".jsonc",
            ".kt", ".kts",
            ".smali",
            ".rs",
            ".php", ".php3", ".php4", ".php5", ".phtml",
            ".qss", ".rc", ".prop", ".properties",
            ".xml", ".xsl", ".xslt", ".xsd", ".dtd", ".rss", ".atom",
            ".cfg", ".conf", ".ini",
            ".csv",
            ".h", ".hpp", ".hxx", ".hh",
            ".sh", ".bash", ".zsh", ".fish", ".ksh", ".csh", ".tcsh", ".dash",
            *FileTree.TEXT_EXT,
        ]

        if not (ext in allowed or is_meta or force):
            return

        # TAMPILKAN EDITOR AREA
        self._reveal_editor_pane()

        if getattr(self, "terminal_wrap", None) and self.terminal_wrap.isVisible():
            self.editor_split.setSizes([700, 300])

        # BUKA DI UNIFIED TABS
        self.unified_tabs.open_file(filepath)
        tree = source_tree or self.active_tree
        if source_path and tree:
            fs_root = tree._nb_fs_root_for_path(source_path)
            if fs_root:
                nb = tree._get_nb_manager()
                active = nb.get_active_branch(fs_root) if nb else None
                self.unified_tabs.bind_neobranch_tab(
                    filepath, tree.base, fs_root, source_path,
                    active["name"] if active else None,
                )
        self.update_smartbar_visibility()


    def show_erofs_loading(self, text):
        self.erofs_bar.setText(text)
        self.erofs_bar.setVisible(True)

    def hide_erofs_loading(self):
        self.erofs_bar.setVisible(False)

    def show_loading_for_path(self, folder_path):
        # cari item dari path
        item = self.tree.find_item_by_path(self.tree.topLevelItem(0), folder_path)
        if not item:
            return None

        #pake json icon
        anim = LoadingAnimator(
            item,
            self.tree,
            "loading_progress",
            "loading_finish"
        )
        anim.start()

        # simpan animator biar bisa dihentikan
        return anim


    def toggle_tree_mode(self):


        if not self.isVisible():
            return

        # kalau loading masih tampil, jangan ubah layout
        if self.loading and self.loading.isVisible():
            return


        # kalau splitter belum siap
        if not hasattr(self, "tree_split"):
            return


        if not hasattr(self, "tree_mode"):
            self.tree_mode = 1


        self.tree_mode += 1
        if self.tree_mode > 3:
            self.tree_mode = 1


        self.apply_tree_mode()


    def _ensure_loading_manager(self):
        if not hasattr(self, "loading_manager") or self.loading_manager is None:
            from main_ext.class_LoadingManager import LoadingManager
            self.loading_manager = LoadingManager(self)
            # Wire signals ke loading_bar
            self.loading_manager.state_active.connect(self.loading_bar.set_active)
            self.loading_manager.state_finished.connect(self.loading_bar.set_finished)
            self.loading_manager.state_failed.connect(self.loading_bar.set_failed)
            self.loading_manager.state_clean.connect(self.loading_bar.set_clean)
            # klik ••• → clear riwayat history
            self.loading_bar.clear_history_requested.connect(self.clear_loading_history)
            # _task_stack: shadow list yg dipertahankan biar caller lama yg masih
            # baca getattr(main, "_task_stack", []) tetap dapet data konsisten
            self._task_stack = []

    def set_loading_clean(self):
        self._ensure_loading_manager()
        self.loading_manager.set_clean()

    def set_loading_active(self, tasks: list):


        self._ensure_loading_manager()
        if self.loading_manager.is_busy():
            return  # manager handle sendiri
        self.loading_bar.set_active(tasks)

    def maybe_finish_loading(self):

        mgr = getattr(self, "loading_manager", None)
        if mgr is not None and mgr.is_busy():
            return
        # Fallback _task_stack lama (kalau manager belum ready)
        stack = getattr(self, "_task_stack", [])
        if stack:
            return
        self._completed_task_types = set()
        if mgr is not None:
            mgr.finish_session()
        else:
            self.set_loading_finished("Done")

    def set_loading_finished(self, text="Done"):
        self._ensure_loading_manager()
        self.loading_manager.set_finished(text)

    def set_loading_failed(self, text="Failed"):
        self._ensure_loading_manager()
        self.loading_manager.set_failed(text)

    def cancel_task(self, name):

        self._ensure_loading_manager()
        self.loading_manager.cancel_task(name)
        try:
            self._task_stack.remove(name)
        except ValueError:
            pass

    def clear_loading_history(self):

        self._ensure_loading_manager()
        self.loading_manager.clear_history()

    def get_loading_history(self):
        self._ensure_loading_manager()
        return self.loading_manager.get_history()

    def push_task(self, name):

        self._ensure_loading_manager()
        self.loading_manager.push_task(name)
        # shadow buat backward compat
        self._task_stack.append(name)

    def pop_task(self, name, ok=True):

        self._ensure_loading_manager()
        self.loading_manager.pop_task(name, ok)
        try:
            self._task_stack.remove(name)
        except ValueError:
            pass
        # Track jenis task terakhir untuk maybe_finish_loading legacy message
        if not hasattr(self, "_completed_task_types"):
            self._completed_task_types = set()
        for keyword in ("repack", "unpack", "convert", "moving", "extracting", "preparing"):
            if keyword in name:
                self._completed_task_types.add(keyword)
                break

    def refresh_loading_status(self):


        return

    def import_files(self):

        tree = self.active_tree
        item = tree.drag_hover_item or tree.currentItem()

        # tentukan target folder
        if item:
            full = item.data(0, Qt.UserRole)
            if os.path.isfile(full):
                target_dir = os.path.dirname(full)
            else:
                target_dir = full
        else:
            # tidak ada hover atau selection → import ke root
            target_dir = tree.base

        # dialog pilih file/folder
        dlg = QFileDialog(self, "Import Files")
        dlg.setFileMode(QFileDialog.ExistingFiles)
        dlg.setOption(QFileDialog.DontUseNativeDialog, False)

        if dlg.exec() != QFileDialog.Accepted:
            return

        paths = dlg.selectedFiles()
        if not paths:
            return

        # Worker function — jalan di background thread, file I/O berat di sini
        # Worker return (ok, msg); payload lewat closure dict
        _import_payload = {"rels": [], "errors": []}
        _tree_base = str(tree.base)

        def _do_import():
            for src in paths:
                name = os.path.basename(src)
                dst = os.path.join(target_dir, name)
                try:
                    if os.path.isdir(src):
                        shutil.copytree(src, dst, dirs_exist_ok=True)
                    else:
                        shutil.copy2(src, dst)
                    rel = os.path.relpath(dst, _tree_base).replace("\\", "/")
                    _import_payload["rels"].append(rel)
                except Exception as e:
                    _import_payload["errors"].append((src, str(e)))
            return True, ""

        def _on_done(ok, msg):
            self.set_loading_finished("Import finished")
            if not ok:
                AppDialog.error(self, "Import Error", str(msg))
                return
            for rel in _import_payload["rels"]:
                try:
                    tree.engine.apply(rel)
                except Exception as e:
                    print("APPLY ERROR:", e)
            parent_item = tree.find_item_by_path(tree.topLevelItem(0), target_dir)
            if parent_item:
                tree.refresh_folder(parent_item)
            errs = _import_payload["errors"]
            if errs:
                err_msg = "\n".join(f"{os.path.basename(s)}: {e}" for s, e in errs[:5])
                AppDialog.warning(self, "Some files failed", err_msg)

        self.set_loading_active([f"Importing ({len(paths)})" if len(paths) > 1 else "Importing.."])
        run_in_thread(_do_import, None, callback=_on_done)


    def update_root_selector(self):

        items = self._terminal_root_items()
        if self.active_root_id not in {rid for _, rid in items}:
            self.active_root_id = "tree1"
        self.root_selector.setVisible(len(items) > 1)
        self._update_root_selector_display()

    def apply_tree_mode(self):
        while self.tree_split.count() > 0:
            w = self.tree_split.widget(0)
            w.setParent(None)

        base1 = self.tree.base
        base2 = self.tree2.base

        # MODE 1: SINGLE
        if self.tree_mode == 1:
            self.tree_split.setOrientation(Qt.Vertical)
            self.tree_split.addWidget(self.tree_panel)
            self.active_tree = self.tree

        # MODE 2: HORIZONTAL SPLIT
        elif self.tree_mode == 2:
            self.tree_split.setOrientation(Qt.Horizontal)
            self.tree_split.addWidget(self.tree_panel)
            if base2:
                self.tree_split.addWidget(self.tree2_panel)
            else:
                self.tree_split.addWidget(self.tree_mirror_panel)

        # MODE 3: VERTICAL SPLIT
        elif self.tree_mode == 3:
            self.tree_split.setOrientation(Qt.Vertical)
            self.tree_split.addWidget(self.tree_panel)
            if base2:
                self.tree_split.addWidget(self.tree2_panel)
            else:
                self.tree_split.addWidget(self.tree_mirror_panel)

        self.sync_tree_focus()
        self.update_root_selector()
        def reset_tree_split_sizes():
            self.tree_split.setSizes([1, 1])
            self.splitter.handle(1).update()

        QTimer.singleShot(0, reset_tree_split_sizes)

    def sync_tree_focus(self):
        for t_ in (self.tree, self.tree2, self.tree_mirror):
            try:
                t_.itemClicked.disconnect()
            except Exception:
                pass

        def select_tree1():
            self.active_tree = self.tree
            self.tree2.clearSelection()     # <<  BERSIHKAN HIGHLIGHT TREE 2
            self.tree2.setCurrentItem(None)
            self.tree_mirror.clearSelection()
            self.tree_mirror.setCurrentItem(None)

        def select_tree2():
            self.active_tree = self.tree2
            self.tree.clearSelection()      # <<  BERSIHKAN HIGHLIGHT TREE 1
            self.tree.setCurrentItem(None)
            self.tree_mirror.clearSelection()
            self.tree_mirror.setCurrentItem(None)

        def select_tree_mirror():
            self.active_tree = self.tree_mirror
            self.tree.clearSelection()
            self.tree.setCurrentItem(None)
            self.tree2.clearSelection()
            self.tree2.setCurrentItem(None)

        self.tree.itemClicked.connect(lambda *_: select_tree1())
        self.tree2.itemClicked.connect(lambda *_: select_tree2())
        self.tree_mirror.itemClicked.connect(lambda *_: select_tree_mirror())


    def set_active_tree(self, tree):
        self.active_tree = tree

    def _tree_delete_hotkey(self):
        focus_w = QApplication.focusWidget()
        if focus_w and isinstance(focus_w, (QPlainTextEdit, QTextEdit, QLineEdit)):
            return

        tree = getattr(self, "active_tree", None) or getattr(self, "tree", None)
        if tree and hasattr(tree, "delete_selected"):
            tree.delete_selected()

    def save_editor_to_file(self):
        self.unified_tabs.save_current()

    def save_hotkey(self):
        self.unified_tabs.save_current()

    def close_tab(self, filepath_or_index=None):

        if isinstance(filepath_or_index, str):
            filepath = filepath_or_index
            if self.unified_tabs.is_unsaved(filepath):
                if not AppDialog.confirm_close(
                    self, t("main_unsaved_title"),
                    t("main_unsaved_msg")
                ):
                    return
            self.unified_tabs.close_file(filepath)

        if self.unified_tabs.tab_count() == 0:
            self._on_all_tabs_closed()

    def _show_aux_tab(self):

        pass

    def _show_editor_panel(self):

        pass

    def _on_all_editor_tabs_closed(self):

        if self.unified_tabs.aux_tab_count() > 0:
            return
        self._on_all_tabs_closed()

    def _on_all_tabs_closed(self):

        if self.terminal_wrap.isVisible():
            self.right_wrap.show()
            self.editor_split.widget(0).hide()
            self.editor_split.handle(1).setEnabled(False)
            self.editor_split.setSizes([0, 1])
            if self.tree_mode == 2:
                self.tree_mode = 3
                self.apply_tree_mode()
        else:
            self.right_wrap.hide()
            self.editor_split.widget(0).hide()
            self.editor_split.handle(1).setEnabled(False)
            self._check_restore_tree_mode()
            self.update_smartbar_visibility()

    def _check_restore_tree_mode(self):

        has_editor   = self.unified_tabs.tab_count() > 0 or self.unified_tabs.aux_tab_count() > 0
        has_terminal = self.terminal_wrap.isVisible()
        if not has_editor and not has_terminal and self.tree_mode == 3:
            self.tree_mode = 2
            self.apply_tree_mode()

    def _close_aux_tab(self, index):

        pass

    def open_find_dialog(self):
        from PySide6.QtWidgets import QApplication
        focused = QApplication.focusWidget()
        trees = []
        for attr in ("active_tree", "tree", "tree2", "tree_mirror"):
            t_ = getattr(self, attr, None)
            if t_ and t_ not in trees:
                trees.append(t_)

        # Jika fokus di tree atau viewport-nya, panggil FileSearchUI
        if focused and any(focused == t_ or (hasattr(t_, "viewport") and focused == t_.viewport()) for t_ in trees):
            active_tree = getattr(self, "active_tree", None)
            target = active_tree if active_tree in trees else (trees[0] if trees else None)
            if target and hasattr(target, "show_search"):
                target.show_search()
                return

        # Monaco built-in find/replace
        if hasattr(self, "unified_tabs") and hasattr(self.unified_tabs, "view"):
            self.unified_tabs.view.page().runJavaScript(
                "editor && editor.getAction('actions.find').run();"
            )

    def open_android_tools(self):
        from main_ext.class_AndroidToolsDialog import AndroidToolsDialog
        AndroidToolsDialog.open(self, self)

    def force_close(self):

        self._stop_private_features()
        self.close()

    def _force_stop_background_work(self, kill_service: bool = True):

        # 1) SESI: batalin antrian task + worker threads (selalu, di kedua jalur)
        try:
            self.task_queue.cancel_all()
        except Exception as e:
            print(f"[Main] WARN: cancel task queue error: {e}")

        try:
            mgr = getattr(self, "loading_manager", None)
            if mgr is not None and hasattr(mgr, "clear_all"):
                mgr.clear_all()
            self._task_stack = []
        except Exception as e:
            print(f"[Main] WARN: clear loading tasks error: {e}")

        try:
            from core.lock_recovery import force_release_managed_tools
            force_release_managed_tools(only_children=True)
        except Exception as e:
            print(f"[Main] WARN: cancel tool processes error: {e}")

        try:
            from core.threader import stop_all_threads
            stop_all_threads()
        except Exception as e:
            print(f"[Main] WARN: stop threads error: {e}")

        # 2) WS SERVICE: cuma di-matikan kalau benar-benar keluar app.
        if not kill_service:
            print("[Main] return-to-project: sesi project dihentikan, WS service tetap hidup")
            return

        try:
            from main_ext.config_engine_client import _ServiceConnection
            conn = _ServiceConnection.get()
            if conn.is_connected():
                try:
                    conn.ws.close()
                except Exception:
                    pass
        except Exception:
            pass

        try:
            from services.process_manager import get_manager
            get_manager().stop(timeout=0.4)
        except Exception as e:
            print(f"[Main] WARN: stop service error: {e}")

    def _has_background_work(self):
        if self.task_queue.is_busy():
            return True
        try:
            mgr = getattr(self, "loading_manager", None)
            if mgr is not None and mgr.is_busy():
                return True
        except Exception:
            pass
        try:
            from core.threader import has_active_threads
            return has_active_threads()
        except Exception:
            return False

    def closeEvent(self, event):
        force_stop = False
        if not getattr(self, "_preparation_aborted", False) and self._has_background_work():
            if not AppDialog.confirm_close(
                self, t("main_tasks_running_title"),
                t("main_tasks_running_msg"),
                close_label=t("main_btn_force_close"), cancel_label=t("main_btn_wait")
            ):
                event.ignore()
                return
            force_stop = True
        self._closing = True
        self._preparation_aborted = True
        self._stop_preparation_timer()
        for tree in (self.tree, self.tree2, self.tree_mirror):
            if tree is not None:
                tree.stop_background_work()
        _th.remove_listener(self._on_theme_changed)
        remove_language_listener(self._refresh_edition_title)
        if force_stop:


            self._force_stop_background_work(kill_service=not getattr(self, "_returning", False))
        _th.remove_view_mode_listener(self._on_tree_view_mode_changed)
        # Stop background timers & threads sebelum destroy supaya
        # QThread tidak ke-GC saat masih running
        self._stop_private_features()


        self._closing = True
        if self._entry_content_reveal is not None:
            self._entry_content_reveal.stop()
            self._entry_content_reveal = None
        self._complete_entry_transition()
        if Main.instance is self:
            Main.instance = None
        for sessions in self.terminal_sessions.values():
            for terminal in sessions:
                terminal.shutdown()
        try:
            from core import rak_cli
            rak_cli.detach(self)
        except Exception as _e:
            print(f"[Main] RAK_CLI detach skipped: {_e}")
        event.accept()
        if self.project:
            session_started = getattr(self, "_project_session_started_at", None)
            reason = "back_to_projects" if getattr(self, "_returning", False) else "app_exit"
            if session_started is not None:
                log_project_event(
                    "EXIT", self.project,
                    reason=reason,
                    session_ms=round((time.perf_counter() - session_started) * 1000),
                )
            else:
                log_project_event("CLOSE_BEFORE_ENTER", self.project, reason=reason)
        if not getattr(self, "_returning", False):
            self._shutdown_and_quit()

    def _shutdown_and_quit(self):

        try:
            from main_ext.config_engine_client import _ServiceConnection
            conn = _ServiceConnection.get()
            if conn.is_connected():
                try:
                    conn.ws.close()
                except Exception:
                    pass
        except Exception:
            pass
        try:
            from services.process_manager import get_manager
            get_manager().stop()
        except Exception as e:
            print(f"[Main] WARN: stop service error: {e}")
        try:
            from core.threader import stop_all_threads
            stop_all_threads()
        except Exception as e:
            print(f"[Main] WARN: stop threads error: {e}")
        from core.threader import quit_when_idle
        quit_when_idle()

# if __name__ == "__main__":
#     app = QApplication(sys.argv)


#     sys.exit(app.exec())


