# SPDX-License-Identifier: MPL-2.0
"""Native Qt Quick terminal item for PySide6 backed by Rust termcore engine."""
from __future__ import annotations

import ctypes
import math
import os
import sys
import threading
import time
import uuid
from pathlib import Path

from PySide6.QtCore import (
    Property, QPointF, QRectF, Qt, QTimer, Signal, Slot, QEvent
)
from PySide6.QtGui import (
    QColor, QCursor, QFont, QFontMetricsF, QGuiApplication,
    QKeyEvent, QMouseEvent, QPainter, QPainterPath, QPen, QWheelEvent
)
from PySide6.QtQuick import QQuickItem, QQuickPaintedItem
from PySide6.QtQml import qmlRegisterType
try:
    from shiboken6 import isValid
except ImportError:
    def isValid(obj):
        return obj is not None

# ==============================================================================
# 1. C-ABI BINDINGS TO TERMCORE
# ==============================================================================

EV_WAKEUP = 0
EV_TITLE = 1
EV_BELL = 2
EV_CLIPBOARD = 3
EV_EXIT = 4

CF_BOLD = 1
CF_ITALIC = 2
CF_UL_SINGLE = 4
CF_UL_DOUBLE = 8
CF_UL_CURLY = 16
CF_UL_DOTTED = 32
CF_UL_DASHED = 64
CF_STRIKE = 128
CF_WIDE = 256
CF_SPACER = 512
CF_SELECTED = 1024
CF_HAS_EXTRA = 2048
CF_LINK = 4096

MODE_MOUSE = 1
MODE_ALT_SCREEN = 2
MODE_APP_CURSOR = 4
MODE_BRACKETED_PASTE = 8
MODE_FOCUS_REPORT = 16

TK_CHAR = 0
TK_ENTER = 1
TK_TAB = 2
TK_BACKSPACE = 3
TK_ESCAPE = 4
TK_UP = 5
TK_DOWN = 6
TK_LEFT = 7
TK_RIGHT = 8
TK_HOME = 9
TK_END = 10
TK_PGUP = 11
TK_PGDN = 12
TK_INSERT = 13
TK_DELETE = 14
TK_F1 = 20

TM_SHIFT = 1
TM_ALT = 2
TM_CTRL = 4
TM_SUPER = 8

TEV_PRESS = 1
TEV_REPEAT = 2
TEV_RELEASE = 3

TACT_PRESS = 0
TACT_RELEASE = 1
TACT_MOTION = 2

CALLBACK_FUNC = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_int)

class CCell(ctypes.Structure):
    _fields_ = [
        ("ch", ctypes.c_uint32),
        ("fg", ctypes.c_uint32),
        ("bg", ctypes.c_uint32),
        ("ul", ctypes.c_uint32),
        ("flags", ctypes.c_uint32),
    ]

class CInfo(ctypes.Structure):
    _fields_ = [
        ("cols", ctypes.c_int32),
        ("rows", ctypes.c_int32),
        ("cursor_col", ctypes.c_int32),
        ("cursor_row", ctypes.c_int32),
        ("cursor_shape", ctypes.c_int32),
        ("cursor_blink", ctypes.c_int32),
        ("display_offset", ctypes.c_int32),
        ("history_size", ctypes.c_int32),
        ("mode", ctypes.c_uint32),
        ("has_selection", ctypes.c_int32),
        ("fg", ctypes.c_uint32),
        ("bg", ctypes.c_uint32),
        ("cursor_color", ctypes.c_uint32),
        ("cursor_text", ctypes.c_uint32),
        ("sel_bg", ctypes.c_uint32),
    ]

def _find_termcore_dll():
    root = Path(__file__).resolve().parent.parent
    candidates = [
        root / "build" / "termcore_runtime" / "release" / "termcore.dll",
        root / "core" / "terminal" / "core" / "target" / "release" / "termcore.dll",
        root / "core" / "termcore.dll",
        root / "core" / "terminal" / "build" / "Release" / "termcore.dll",
        root / "core" / "terminal" / "build" / "cargo" / "release" / "termcore.dll",
        Path(sys.executable).parent / "termcore.dll",
    ]
    existing = [c for c in candidates if c.is_file()]
    if existing:
        newest = max(existing, key=lambda p: p.stat().st_mtime)
        return str(newest)
    return "termcore.dll"

_lib_path = _find_termcore_dll()
try:
    _termcore = ctypes.CDLL(_lib_path)
except Exception as e:
    _termcore = None
    print(f"[qtterm] WARNING: Failed to load termcore.dll from '{_lib_path}': {e}")

if _termcore:
    _termcore.term_new.restype = ctypes.c_void_p
    _termcore.term_new.argtypes = [
        ctypes.c_char_p, ctypes.POINTER(ctypes.c_char_p), ctypes.c_char_p,
        ctypes.c_int, ctypes.c_int, ctypes.c_int, CALLBACK_FUNC, ctypes.c_void_p
    ]
    if hasattr(_termcore, "term_new_log"):
        _termcore.term_new_log.restype = ctypes.c_void_p
        _termcore.term_new_log.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_int, CALLBACK_FUNC, ctypes.c_void_p]
    _termcore.term_free.argtypes = [ctypes.c_void_p]
    _termcore.term_str_free.argtypes = [ctypes.c_char_p]
    _termcore.term_resize.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int]
    _termcore.term_set_theme.argtypes = [
        ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_uint32,
        ctypes.c_uint32, ctypes.c_uint32, ctypes.POINTER(ctypes.c_uint32)
    ]
    _termcore.term_snapshot.restype = ctypes.c_int
    _termcore.term_snapshot.argtypes = [ctypes.c_void_p, ctypes.POINTER(CCell), ctypes.c_size_t, ctypes.POINTER(CInfo)]
    _termcore.term_cell_extra.restype = ctypes.c_size_t
    _termcore.term_cell_extra.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_uint32), ctypes.c_size_t]
    _termcore.term_write.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_size_t]
    _termcore.term_feed.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_size_t]
    if hasattr(_termcore, "term_set_pty_mute"):
        _termcore.term_set_pty_mute.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_char_p]
    _termcore.term_paste.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
    _termcore.term_focus.argtypes = [ctypes.c_void_p, ctypes.c_int]
    _termcore.term_key.restype = ctypes.c_int
    _termcore.term_key.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_uint32, ctypes.c_char_p, ctypes.c_uint32, ctypes.c_int]
    _termcore.term_mouse.restype = ctypes.c_int
    _termcore.term_mouse.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_uint32]
    _termcore.term_scroll.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_uint32]
    _termcore.term_scroll_local.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
    _termcore.term_sel_start.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int]
    _termcore.term_sel_update.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int]
    _termcore.term_sel_clear.argtypes = [ctypes.c_void_p]
    _termcore.term_sel_all.argtypes = [ctypes.c_void_p]
    _termcore.term_sel_text.restype = ctypes.c_void_p
    _termcore.term_sel_text.argtypes = [ctypes.c_void_p]
    _termcore.term_title.restype = ctypes.c_void_p
    _termcore.term_title.argtypes = [ctypes.c_void_p]
    _termcore.term_alive.restype = ctypes.c_int
    _termcore.term_alive.argtypes = [ctypes.c_void_p]
    _termcore.term_exit_code.restype = ctypes.c_int
    _termcore.term_exit_code.argtypes = [ctypes.c_void_p]

def _rgb(v: int) -> QColor:
    return QColor((v >> 16) & 0xFF, (v >> 8) & 0xFF, v & 0xFF)

def _pack(c: QColor) -> int:
    return (c.red() << 16) | (c.green() << 8) | c.blue()

TANGO_COLORS = [
    "#2e3436", "#cc0000", "#4e9a06", "#c4a000", "#3465a4", "#75507b", "#06989a", "#d3d7cf",
    "#555753", "#ef2929", "#8ae234", "#fce94f", "#729fcf", "#ad7fa8", "#34e2e2", "#eeeeec"
]

def _map_qt_key(k: int) -> int:
    if k in (Qt.Key.Key_Return, Qt.Key.Key_Enter): return TK_ENTER
    if k in (Qt.Key.Key_Tab, Qt.Key.Key_Backtab): return TK_TAB
    if k == Qt.Key.Key_Backspace: return TK_BACKSPACE
    if k == Qt.Key.Key_Escape: return TK_ESCAPE
    if k == Qt.Key.Key_Up: return TK_UP
    if k == Qt.Key.Key_Down: return TK_DOWN
    if k == Qt.Key.Key_Left: return TK_LEFT
    if k == Qt.Key.Key_Right: return TK_RIGHT
    if k == Qt.Key.Key_Home: return TK_HOME
    if k == Qt.Key.Key_End: return TK_END
    if k == Qt.Key.Key_PageUp: return TK_PGUP
    if k == Qt.Key.Key_PageDown: return TK_PGDN
    if k == Qt.Key.Key_Insert: return TK_INSERT
    if k == Qt.Key.Key_Delete: return TK_DELETE
    if Qt.Key.Key_F1 <= k <= Qt.Key.Key_F24:
        return TK_F1 + (k - Qt.Key.Key_F1)
    return TK_CHAR

def _is_modifier_only(k: int) -> bool:
    return k in (
        Qt.Key.Key_Shift, Qt.Key.Key_Control, Qt.Key.Key_Alt, Qt.Key.Key_AltGr, Qt.Key.Key_Meta,
        Qt.Key.Key_Super_L, Qt.Key.Key_Super_R, Qt.Key.Key_Hyper_L, Qt.Key.Key_Hyper_R,
        Qt.Key.Key_CapsLock, Qt.Key.Key_NumLock, Qt.Key.Key_ScrollLock, Qt.Key.Key_Mode_switch,
        Qt.Key.Key_unknown
    )


# ==============================================================================
# 2. NATIVE TERMINAL ITEM (QQuickPaintedItem)
# ==============================================================================

def _detect_terminal_font() -> str:
    if sys.platform == "win32":
        from PySide6.QtGui import QFontInfo
        for fam in ("Cascadia Mono", "Cascadia Code", "Consolas"):
            if QFontInfo(QFont(fam)).family().lower() == fam.lower():
                return fam
        return "Consolas"
    return "monospace"


class Terminal(QQuickPaintedItem):
    """QML-ready native terminal element with ConPTY + Alacritty VT engine."""

    # Signals
    configChanged = Signal()
    fontChanged = Signal()
    themeChanged = Signal()
    titleChanged = Signal()
    runningChanged = Signal()
    sizeChanged = Signal()
    scrollChanged = Signal()
    selectionChanged = Signal()
    bell = Signal()
    exited = Signal(int)
    interruptRequested = Signal()
    nativeCommandSubmitted = Signal(str)
    _core_event_signal = Signal(int)
    _native_output_signal = Signal(str)
    _native_done_signal = Signal(int, bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFlag(QQuickItem.Flag.ItemAcceptsInputMethod, False)
        self.setAcceptedMouseButtons(Qt.MouseButton.AllButtons)
        self.setAcceptHoverEvents(True)
        self.setActiveFocusOnTab(True)
        self.setCursor(Qt.CursorShape.IBeamCursor)
        self.setOpaquePainting(True)
        self._core_event_signal.connect(self._handle_core_event, Qt.ConnectionType.QueuedConnection)
        self._native_output_signal.connect(self._on_native_output, Qt.ConnectionType.QueuedConnection)
        self._native_done_signal.connect(self._on_native_done, Qt.ConnectionType.QueuedConnection)

        self._program = ""
        self._arguments = []
        self._cwd = ""
        self._auto_start = True
        self._wsl_mode = False
        self._scrollback = 10000
        self._native_cmd_running = False
        self._native_cancel = None
        self._native_thread = None
        self._native_conhost_row = 0
        self._native_conhost_col = 0
        self._native_shell_kind = "cmd"
        self._native_lines_fed = 0
        self._native_sync_seq = 0

        self._family = _detect_terminal_font()
        self._pixel_size = 15
        self._padding = 8

        self._fg = QColor("#cdd6f4")
        self._bg = QColor("#1e1e2e")
        self._cursor_color = QColor("#f5e0dc")
        self._selection_color = QColor("#585b70")
        self._palette = [QColor(c) for c in TANGO_COLORS]

        self._cursor_blink = True
        self._cursor_on = True
        self._cursor_shape = "beam"
        self._title = ""
        self._cols = 80
        self._rows = 24

        self._term = None
        self._output_only = False
        self._cells = None
        self._info = CInfo()

        self._cell_w = 8.0
        self._cell_h = 16.0
        self._ascent = 12.0
        self._fonts = [QFont() for _ in range(4)]
        self._paint_lock = threading.Lock()
        self._has_focus = False
        self._color_cache = {}
        self._pen_cache = {}

        # Animation & frame timer for smooth cursor crossfade & smooth glide motion
        self._cursor_opacity = 1.0
        self._cursor_cycle_start = time.perf_counter() - 0.08
        self._cursor_width = 5.5
        self._cur_render_x = 0.0
        self._cur_render_y = 0.0
        self._cur_start_x = 0.0
        self._cur_start_y = 0.0
        self._cur_target_x = 0.0
        self._cur_target_y = 0.0
        self._cur_move_start = 0.0
        self._cur_move_duration = 0.060  # Adaptive glide duration; scales down cleanly on rapid input
        self._cur_last_target_time = 0.0
        self._cur_cadence = 0.16
        self._cur_burst_count = 0
        self._last_repeat_key_time = 0.0
        self._cur_initialized = False

        self._anim_timer = QTimer(self)
        self._anim_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._anim_timer.setInterval(16)
        self._anim_timer.timeout.connect(self._on_anim_frame)

        # Legacy blink timer kept for compatibility
        self._blink_timer = self._anim_timer

        # Resize debounce timer to prevent ConPTY buffer desync during rapid window dragging
        self._resize_timer = QTimer(self)
        self._resize_timer.setSingleShot(True)
        self._resize_timer.setInterval(50)
        self._resize_timer.timeout.connect(self._apply_pending_resize)
        self._pending_cols = self._cols
        self._pending_rows = self._rows

        # Event coalescing / throttle for high-frequency VT bursts
        self._wake_pending = False
        self._last_refresh_time = 0.0

        # C-callback holder to avoid GC
        self._cb_holder = CALLBACK_FUNC(self._on_core_event)

        self._update_metrics()

    # ---- Properties ----

    @Property(str, notify=configChanged)
    def program(self): return self._program
    @program.setter
    def program(self, val):
        if self._program != val: self._program = val; self.configChanged.emit()

    @Property('QStringList', notify=configChanged)
    def arguments(self): return self._arguments
    @arguments.setter
    def arguments(self, val):
        if self._arguments != val: self._arguments = list(val); self.configChanged.emit()

    @Property(str, notify=configChanged)
    def workingDirectory(self): return self._cwd
    @workingDirectory.setter
    def workingDirectory(self, val):
        if self._cwd != val: self._cwd = val; self.configChanged.emit()

    @Property(bool, notify=configChanged)
    def autoStart(self): return self._auto_start
    @autoStart.setter
    def autoStart(self, val): self._auto_start = bool(val)

    @Property(bool, notify=configChanged)
    def wslMode(self): return self._wsl_mode
    @wslMode.setter
    def wslMode(self, val):
        b = bool(val)
        if self._wsl_mode != b:
            self._wsl_mode = b
            self.configChanged.emit()

    @Property(int, notify=configChanged)
    def scrollback(self): return self._scrollback
    @scrollback.setter
    def scrollback(self, val): self._scrollback = int(val)

    @Property(str, notify=fontChanged)
    def fontFamily(self): return self._family
    @fontFamily.setter
    def fontFamily(self, val):
        if self._family != val:
            self._family = val
            self._update_metrics()
            self._fit()
            self.fontChanged.emit()
            self.update()

    @Property(int, notify=fontChanged)
    def fontPixelSize(self): return self._pixel_size
    @fontPixelSize.setter
    def fontPixelSize(self, val):
        if self._pixel_size != val and val >= 4:
            self._pixel_size = val
            self._update_metrics()
            self._fit()
            self.fontChanged.emit()
            self.update()

    @Property(int, notify=fontChanged)
    def padding(self): return self._padding
    @padding.setter
    def padding(self, val):
        if self._padding != val and val >= 0:
            self._padding = val
            self._fit()
            self.fontChanged.emit()
            self.update()

    @Property(QColor, notify=themeChanged)
    def foreground(self): return self._fg
    @foreground.setter
    def foreground(self, val):
        c = QColor(val)
        if self._fg != c: self._fg = c; self._apply_theme(); self.themeChanged.emit()

    @Property(QColor, notify=themeChanged)
    def background(self): return self._bg
    @background.setter
    def background(self, val):
        c = QColor(val)
        if self._bg != c: self._bg = c; self._apply_theme(); self.themeChanged.emit(); self.update()

    @Property(QColor, notify=themeChanged)
    def cursorColor(self): return self._cursor_color
    @cursorColor.setter
    def cursorColor(self, val):
        c = QColor(val)
        if self._cursor_color != c: self._cursor_color = c; self._apply_theme(); self.themeChanged.emit()

    @Property(QColor, notify=themeChanged)
    def selectionColor(self): return self._selection_color
    @selectionColor.setter
    def selectionColor(self, val):
        c = QColor(val)
        if self._selection_color != c: self._selection_color = c; self._apply_theme(); self.themeChanged.emit()

    @Property('QVariantList', notify=themeChanged)
    def ansiPalette(self):
        return [c.name() for c in self._palette]
    @ansiPalette.setter
    def ansiPalette(self, val):
        if val:
            self._palette = [QColor(c) for c in val]
            while len(self._palette) < 16:
                self._palette.append(QColor("#000000"))
            self._apply_theme()
            self.themeChanged.emit()

    @Property(bool, notify=configChanged)
    def cursorBlink(self):
        return self._cursor_blink
    @cursorBlink.setter
    def cursorBlink(self, val):
        b = bool(val)
        if self._cursor_blink != b:
            self._cursor_blink = b
            if not self._cursor_blink:
                self._cursor_on = True
                self._cursor_opacity = 1.0
                if self._cur_move_start <= 0.0:
                    self._anim_timer.stop()
            elif self.hasActiveFocus():
                self._cursor_cycle_start = time.perf_counter() - 0.08
                self._cursor_opacity = 1.0
                if not self._anim_timer.isActive():
                    self._anim_timer.start(16)
            self.configChanged.emit()
            self.update()

    @Property(str, notify=configChanged)
    def cursorShape(self):
        return self._cursor_shape
    @cursorShape.setter
    def cursorShape(self, val):
        v = str(val or "beam").lower()
        if self._cursor_shape != v:
            self._cursor_shape = v
            self.configChanged.emit()
            self.update()

    @Property(float, notify=configChanged)
    def cursorWidth(self):
        return self._cursor_width
    @cursorWidth.setter
    def cursorWidth(self, val):
        v = max(1.0, float(val))
        if self._cursor_width != v:
            self._cursor_width = v
            self.configChanged.emit()
            self.update()

    def _cursor_shape_int(self) -> int:
        if self._cursor_shape in ("beam", "bar", "line"):
            return 2
        if self._cursor_shape == "underline":
            return 1
        if self._cursor_shape == "block":
            return 0
        return 2

    @Property(str, notify=titleChanged)
    def title(self): return self._title

    @Property(bool, notify=runningChanged)
    def running(self):
        return bool(self._term and _termcore and _termcore.term_alive(self._term))

    @Property(int, notify=runningChanged)
    def exitCode(self):
        return _termcore.term_exit_code(self._term) if (self._term and _termcore) else 0

    @Property(int, notify=sizeChanged)
    def columns(self): return self._cols

    @Property(int, notify=sizeChanged)
    def rows(self): return self._rows

    @Property(int, notify=scrollChanged)
    def historySize(self): return self._info.history_size

    @Property(int, notify=scrollChanged)
    def scrollOffset(self): return self._info.display_offset
    @scrollOffset.setter
    def scrollOffset(self, val):
        if self._term and _termcore:
            _termcore.term_scroll_local(self._term, 0, int(val))
            self._refresh()

    @Property(bool, notify=scrollChanged)
    def mouseTracking(self):
        return bool(self._info.mode & MODE_MOUSE)

    @Property(bool, notify=selectionChanged)
    def hasSelection(self): return bool(self._info.has_selection)

    # ---- Lifecycle ----

    def componentComplete(self):
        super().componentComplete()
        self._fit()
        if self._resize_timer.isActive():
            self._resize_timer.stop()
            self._apply_pending_resize()
        if not self._term and self._auto_start and self._cols >= 2 and self._rows >= 1:
            self.start()

    def geometryChange(self, new_geo, old_geo):
        super().geometryChange(new_geo, old_geo)
        if new_geo.size() != old_geo.size():
            self._fit()
            if not self._term and self._auto_start and self._cols >= 2 and self._rows >= 1:
                self.start()

    def _update_metrics(self):
        fonts = []
        for i in range(4):
            f = QFont(self._family)
            f.setPixelSize(self._pixel_size)
            f.setStyleHint(QFont.StyleHint.Monospace)
            f.setFixedPitch(True)
            f.setKerning(False)
            f.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
            f.setBold(bool(i & 1))
            f.setItalic(bool(i & 2))
            fonts.append(f)

        fm = QFontMetricsF(fonts[0])
        adv = fm.horizontalAdvance("M" * 64) / 64.0
        cell_w = max(1.0, adv)
        cell_h = max(1.0, math.ceil(fm.height()))
        ascent = fm.ascent() + (cell_h - fm.height()) / 2.0
        with self._paint_lock:
            self._fonts = fonts
            self._cell_w = cell_w
            self._cell_h = cell_h
            self._ascent = ascent

    def _fit(self):
        w, h = self.width(), self.height()
        if w <= 0 or h <= 0 or not _termcore:
            return
        c = max(2, int((w - 2 * self._padding) / self._cell_w))
        r = max(1, int((h - 2 * self._padding) / self._cell_h))
        if c == self._cols and r == self._rows and c == self._pending_cols and r == self._pending_rows:
            return

        self._pending_cols = c
        self._pending_rows = r
        if not self._term:
            self._cols = c
            self._rows = r
            self.sizeChanged.emit()
        else:
            self._resize_timer.start(50)

    def _apply_pending_resize(self):
        if not self._term or not _termcore:
            return
        c = self._pending_cols
        r = self._pending_rows
        if c != self._cols or r != self._rows:
            self._cols = c
            self._rows = r
            self.sizeChanged.emit()
            _termcore.term_resize(self._term, c, r, int(self._cell_w), int(self._cell_h))
            self._update_cursor_target(snap=True)
            self._refresh()

    def _apply_theme(self):
        if not self._term or not _termcore:
            return
        ansi = (ctypes.c_uint32 * 16)(*[_pack(self._palette[i]) for i in range(16)])
        sel = self._selection_color
        _termcore.term_set_theme(
            self._term, _pack(self._fg), _pack(self._bg), _pack(self._cursor_color),
            _pack(self._bg), _pack(sel), ansi
        )
        self._refresh()

    # ---- Actions ----

    @Slot()
    def start(self):
        self.stop()
        self._output_only = False
        if not _termcore or self._cols < 2 or self._rows < 1:
            return

        prog = self._program.strip()
        if not prog:
            prog = os.environ.get("COMSPEC", "cmd.exe") if sys.platform == "win32" else os.environ.get("SHELL", "/bin/bash")

        prog_b = prog.encode("utf-8")
        args_b = [a.encode("utf-8") for a in self._arguments]
        argv = (ctypes.c_char_p * (len(args_b) + 1))(*args_b, None) if args_b else None
        cwd_b = self._cwd.encode("utf-8") if self._cwd else None

        self._term = _termcore.term_new(
            prog_b, argv, cwd_b, self._cols, self._rows, self._scrollback,
            self._cb_holder, None
        )
        self._initialize_view()

    @Slot()
    def startOutput(self):
        if self._term and self._output_only:
            return
        self.stop()
        self._output_only = True
        if _termcore and hasattr(_termcore, "term_new_log"):
            self._term = _termcore.term_new_log(self._cols, self._rows, self._scrollback, self._cb_holder, None)
            self._initialize_view()

    def _initialize_view(self):
        if self._term:
            self._apply_theme()
            self._refresh()
            self._cursor_cycle_start = time.perf_counter() - 0.08
            self._cursor_opacity = 1.0
            if self._cursor_blink:
                self._anim_timer.start(16)
            self.runningChanged.emit()

    @Slot()
    def stop(self):
        if self._native_cancel is not None:
            try:
                self._native_cancel.cancel()
            except Exception:
                pass
            self._native_cancel = None
        self._native_cmd_running = False
        if isValid(self._resize_timer) and self._resize_timer.isActive():
            self._resize_timer.stop()
        if isValid(self._anim_timer):
            self._anim_timer.stop()
        self._cur_move_start = 0.0
        self._cur_initialized = False
        if self._term and _termcore:
            t = self._term
            self._term = None
            _termcore.term_free(t)
            if isValid(self):
                self.runningChanged.emit()

    @Slot(str)
    def sendText(self, text: str):
        if not self._term or not _termcore or not text:
            return
        b = text.encode("utf-8")
        _termcore.term_write(self._term, b, len(b))

    @Slot(str)
    def feedText(self, text: str):
        """Directly feed text (e.g. process output or escape sequences) to the VT emulator without writing to PTY stdin."""
        if not self._term or not _termcore or not text:
            return
        if not isinstance(text, str):
            text = "".join(text) if isinstance(text, (list, tuple)) else str(text)
        b = text.encode("utf-8")
        _termcore.term_feed(self._term, b, len(b))

    @Slot()
    def copy(self):
        if not self._term or not _termcore:
            return
        ptr = _termcore.term_sel_text(self._term)
        if ptr:
            try:
                raw = ctypes.cast(ptr, ctypes.c_char_p).value or b""
            finally:
                _termcore.term_str_free(ctypes.cast(ptr, ctypes.c_char_p))
            s = raw.decode("utf-8", errors="replace")
            cb = QGuiApplication.clipboard()
            if cb:
                cb.setText(s)

    @Slot()
    def paste(self):
        if not self._term or not _termcore:
            return
        cb = QGuiApplication.clipboard()
        if cb:
            t = cb.text()
            if t:
                if not self._output_only and not self._wsl_mode and not self._native_cmd_running and not (self._info.mode & MODE_ALT_SCREEN):
                    stripped = t.strip()
                    if "\n" not in stripped and "\r" not in stripped and (t.endswith("\n") or t.endswith("\r")):
                        from core.terminal_native_router import is_locked_terminal_command
                        if is_locked_terminal_command(stripped):
                            self.runNativeCommand(stripped)
                            return
                b = t.encode("utf-8")
                _termcore.term_paste(self._term, b)

    @Slot()
    def selectAll(self):
        if self._term and _termcore:
            _termcore.term_sel_all(self._term)
            self._refresh()

    @Slot()
    def clearSelection(self):
        if self._term and _termcore:
            _termcore.term_sel_clear(self._term)
            self._refresh()

    @Slot()
    def scrollToBottom(self):
        self.scrollOffset = 0

    @Slot(result=str)
    def debugInfo(self) -> str:
        return f"cols={self._cols} rows={self._rows} alive={self.running} hist={self.historySize}"

    def _screen_lines(self) -> list[str]:
        self._refresh()
        if not self._cells or not self._info.rows or not self._info.cols:
            return []
        lines = []
        cols = self._info.cols
        for r in range(self._info.rows):
            chars = []
            for c in range(cols):
                cc = self._cells[r * cols + c]
                if cc.flags & CF_SPACER:
                    continue
                ch = cc.ch
                chars.append(chr(ch) if ch else " ")
            lines.append("".join(chars).rstrip())
        return lines

    @Slot(result=str)
    def screenText(self) -> str:
        return "\n".join(self._screen_lines())

    @Slot(str)
    def _on_native_output(self, chunk: str):
        if not isValid(self) or not self._term or not chunk:
            return
        norm = chunk.replace("\r\n", "\n").replace("\n", "\r\n")
        self._native_lines_fed += norm.count("\n")
        self.feedText(norm)

    @Slot(int, bool)
    def _on_native_done(self, _exit_code: int, prompt_cr: bool):
        self._native_cmd_running = False
        self._native_cancel = None
        if not isValid(self) or not self._term or not _termcore:
            return
        if prompt_cr and not self._output_only:
            _termcore.term_write(self._term, b"\r", 1)

    def _start_locked_native_command(
        self,
        raw_cmd: str,
        tokens: list[str],
        cwd: str,
        *,
        echo_cmd: bool = True,
        prefix_nl: bool = False,
        prompt_cr: bool = True,
    ):
        self._native_cmd_running = True
        self._native_lines_fed = 0
        try:
            from core import native_backend
            self._native_cancel = native_backend.platform_cancel_token()
        except Exception:
            self._native_cancel = None
        if echo_cmd:
            self._native_lines_fed += 1
            self.feedText(raw_cmd + "\r\n")
        elif prefix_nl:
            self._native_lines_fed += 1
            self.feedText("\r\n")
        self.nativeCommandSubmitted.emit(raw_cmd)

        cancel_token = self._native_cancel
        def _worker():
            from core.terminal_native_router import run_locked_terminal_command
            rc = 0
            try:
                rc = run_locked_terminal_command(
                    tokens,
                    cwd=cwd,
                    emit=lambda text: self._native_output_signal.emit(text),
                    cancel_token=cancel_token,
                )
            except Exception as exc:
                self._native_output_signal.emit(f"error: {exc}\n")
                rc = 1
            finally:
                if isValid(self):
                    self._native_done_signal.emit(int(rc), bool(prompt_cr))

        self._native_thread = threading.Thread(target=_worker, name="RAK-NativeTerminalCmd", daemon=True)
        self._native_thread.start()

    @Slot(str)
    def runNativeCommand(self, command: str):
        """Run a locked native command in-process and pipe its output to this terminal."""
        from core.terminal_native_router import split_command_tokens, is_locked_terminal_command
        clean = str(command or "").strip()
        if not clean or not is_locked_terminal_command(clean):
            self.sendText(clean + "\r")
            return
        if self._native_cmd_running:
            return
        tokens = split_command_tokens(clean)
        lines = self._screen_lines()
        self._native_conhost_row = int(self._info.cursor_row)
        self._native_conhost_col = int(self._info.cursor_col)
        self._native_shell_kind = "cmd"
        cwd = self._cwd or os.getcwd()
        if lines and 0 <= self._info.cursor_row < len(lines):
            row_text = lines[self._info.cursor_row].strip()
            if ">" in row_text:
                cand = row_text.rsplit(">", 1)[0].strip()
                if cand.startswith("PS "):
                    cand = cand[3:].strip()
                    self._native_shell_kind = "powershell"
                if cand and os.path.isdir(cand):
                    cwd = cand
        self._start_locked_native_command(clean, tokens, cwd, echo_cmd=True, prefix_nl=False, prompt_cr=True)

    def _try_intercept_prompt_command(self) -> bool:
        if (
            self._output_only
            or self._wsl_mode
            or self._native_cmd_running
            or (self._info.mode & MODE_ALT_SCREEN)
        ):
            return False
        from core.terminal_native_router import extract_prompt_command
        lines = self._screen_lines()
        parsed = extract_prompt_command(lines, self._info.cursor_row, self._cols, fallback_cwd=self._cwd)
        if not parsed:
            return False
        effective_cwd, raw_cmd, tokens, shell_kind = parsed
        self._native_conhost_row = int(self._info.cursor_row)
        self._native_conhost_col = max(0, int(self._info.cursor_col) - len(raw_cmd))
        self._native_shell_kind = shell_kind
        self._native_cmd_running = True
        if hasattr(_termcore, "term_set_pty_mute"):
            _termcore.term_set_pty_mute(self._term, 1, None)
        clear_seq = b"\x1b" if shell_kind == "cmd" else b"\x15\x0b"
        _termcore.term_write(self._term, clear_seq, len(clear_seq))

        def _begin():
            if not isValid(self) or not self._term:
                return
            if hasattr(_termcore, "term_set_pty_mute"):
                _termcore.term_set_pty_mute(self._term, 0, None)
            self._start_locked_native_command(
                raw_cmd,
                tokens,
                effective_cwd,
                echo_cmd=False,
                prefix_nl=True,
                prompt_cr=True,
            )

        QTimer.singleShot(45, _begin)
        return True

    # ---- Event Loop & Refresh ----

    def _on_core_event(self, user, kind):
        # Called from Rust worker thread; dispatch safely to Qt main thread
        if isValid(self):
            self._core_event_signal.emit(kind)

    @Slot(int)
    def _handle_core_event(self, kind):
        # Executes on Qt main GUI thread
        if not isValid(self) or not self._term:
            return
        if kind == EV_WAKEUP:
            if not self._wake_pending:
                self._wake_pending = True
                QTimer.singleShot(0, self._schedule_refresh)
        elif kind == EV_TITLE:
            self._update_title()
        elif kind == EV_BELL:
            self.bell.emit()
        elif kind == EV_EXIT:
            self._on_exit()

    def _schedule_refresh(self):
        if not isValid(self):
            return
        self._wake_pending = False
        self._refresh()

    def inputMethodEvent(self, e):
        # Consume IME events without committing uncontrolled text into raw PTY
        e.accept()

    def _sample_cursor_motion(self, now: float) -> tuple[float, float]:
        """Pure cursor interpolation for paint() on QSGRenderThread (never touches QTimers)."""
        if self._cur_move_start <= 0.0:
            return self._cur_render_x, self._cur_render_y
        dt = (now - self._cur_move_start) / max(0.001, self._cur_move_duration)
        if dt >= 1.0:
            return self._cur_target_x, self._cur_target_y
        t = max(0.0, min(1.0, dt))
        ease = 1.0 - (1.0 - t) ** 2
        return (
            self._cur_start_x + (self._cur_target_x - self._cur_start_x) * ease,
            self._cur_start_y + (self._cur_target_y - self._cur_start_y) * ease,
        )

    def _advance_cursor_motion(self, now: float) -> bool:
        """Advance smooth cursor coordinates to `now`; returns True while glide remains active."""
        if self._cur_move_start <= 0.0:
            return False
        dt = (now - self._cur_move_start) / max(0.001, self._cur_move_duration)
        if dt >= 1.0:
            self._cur_render_x = self._cur_target_x
            self._cur_render_y = self._cur_target_y
            self._cur_move_start = 0.0
            if self._anim_timer.interval() != 16:
                self._anim_timer.setInterval(16)
            return False
        t = max(0.0, min(1.0, dt))
        ease = 1.0 - (1.0 - t) ** 2  # Balanced quadratic ease-out (smooth, no abrupt jerk)
        self._cur_render_x = self._cur_start_x + (self._cur_target_x - self._cur_start_x) * ease
        self._cur_render_y = self._cur_start_y + (self._cur_target_y - self._cur_start_y) * ease
        return True

    def _on_anim_frame(self):
        now = time.perf_counter()
        need_anim = self._advance_cursor_motion(now)

        # 2. Cursor Crossfade Animation: Fast fade in (80ms) -> Hold ON (420ms) -> Fast fade out (80ms) -> Hold OFF (320ms)
        if self._cursor_blink and self.hasActiveFocus():
            need_anim = True
            t_cycle = (now - self._cursor_cycle_start) % 0.90
            if t_cycle < 0.08:
                s = t_cycle / 0.08
                self._cursor_opacity = s * s * (3.0 - 2.0 * s)
            elif t_cycle < 0.50:
                self._cursor_opacity = 1.0
            elif t_cycle < 0.58:
                s = (t_cycle - 0.50) / 0.08
                self._cursor_opacity = 1.0 - (s * s * (3.0 - 2.0 * s))
            else:
                self._cursor_opacity = 0.0
            self._cursor_on = bool(self._cursor_opacity > 0.05)
        else:
            self._cursor_opacity = 1.0 if not self._cursor_blink else (1.0 if self.hasActiveFocus() else 0.0)
            self._cursor_on = bool(self._cursor_opacity > 0.05)

        if need_anim:
            self.update()
        else:
            self._anim_timer.stop()

    def _on_blink(self):
        self._on_anim_frame()

    def _on_exit(self):
        self.runningChanged.emit()
        if self._term and _termcore:
            code = _termcore.term_exit_code(self._term)
            self.exited.emit(code)

    def _update_title(self):
        if self._term and _termcore:
            ptr = _termcore.term_title(self._term)
            if ptr:
                s = ctypes.c_char_p(ptr).value.decode("utf-8", errors="replace")
                _termcore.term_str_free(ctypes.cast(ptr, ctypes.c_char_p))
                if self._title != s:
                    self._title = s
                    self.titleChanged.emit()

    def _update_cursor_target(self, snap: bool = False):
        if self._cols <= 0 or self._rows <= 0:
            return
        c_col = max(0, min(self._cols - 1, self._info.cursor_col))
        c_row = max(0, min(self._rows - 1, self._info.cursor_row))
        tx = self._padding + c_col * self._cell_w
        ty = self._padding + c_row * self._cell_h

        now = time.perf_counter()

        if snap or not self._cur_initialized:
            self._cur_render_x = tx
            self._cur_render_y = ty
            self._cur_start_x = tx
            self._cur_start_y = ty
            self._cur_target_x = tx
            self._cur_target_y = ty
            self._cur_move_start = 0.0
            self._cur_last_target_time = now
            self._cur_cadence = 0.16
            self._cur_burst_count = 0
            self._cur_initialized = True
            return

        if abs(tx - self._cur_target_x) < 0.1 and abs(ty - self._cur_target_y) < 0.1:
            return

        # Large jump (e.g. clear screen, rapid scroll, or jump across lines): snap directly
        if abs(ty - self._cur_target_y) > 2.0 * self._cell_h or abs(tx - self._cur_target_x) > 25.0 * self._cell_w:
            self._cur_render_x = tx
            self._cur_render_y = ty
            self._cur_start_x = tx
            self._cur_start_y = ty
            self._cur_target_x = tx
            self._cur_target_y = ty
            self._cur_move_start = 0.0
            self._cur_last_target_time = now
            self._cur_cadence = 0.16
            self._cur_burst_count = 0
            return

        prev_target_time = self._cur_last_target_time
        self._cur_last_target_time = now
        interval = (now - prev_target_time) if prev_target_time > 0.0 else 1.0

        step_cols = abs(tx - self._cur_target_x) / max(1.0, self._cell_w)
        same_row = abs(ty - self._cur_render_y) < 0.5

        if interval < 0.16:
            if interval < self._cur_cadence:
                self._cur_cadence = interval
            else:
                self._cur_cadence = self._cur_cadence * 0.65 + interval * 0.35
            self._cur_burst_count = min(self._cur_burst_count + 1, 20)
        else:
            self._cur_cadence = 0.16
            self._cur_burst_count = 0

        is_auto_repeat = (now - self._last_repeat_key_time) < 0.09
        if is_auto_repeat:
            target_dur = max(0.010, min(0.022, self._cur_cadence * 0.50))
        elif self._cur_burst_count >= 1 and interval < 0.12:
            target_dur = max(0.012, min(0.055, self._cur_cadence * 0.55))
        elif step_cols <= 1.5 and same_row:
            target_dur = 0.055
        else:
            target_dur = 0.075

        start_x = self._cur_render_x
        start_y = self._cur_render_y
        if (is_auto_repeat or (self._cur_burst_count >= 2 and step_cols <= 3.0)) and same_row:
            # Cap trailing lag so the cursor closes flush on rapid typing and leaves no space gap on backspace
            max_lag = self._cell_w * (0.90 if tx < start_x else 1.05)
            if tx - start_x > max_lag:
                start_x = tx - max_lag
                self._cur_render_x = start_x
            elif start_x - tx > max_lag:
                start_x = tx + max_lag
                self._cur_render_x = start_x

        self._cur_start_x = start_x
        self._cur_start_y = start_y
        self._cur_target_x = tx
        self._cur_target_y = ty
        self._cur_move_duration = target_dur
        self._cur_move_start = now
        if self._anim_timer.interval() != 8:
            self._anim_timer.setInterval(8)
        if not self._anim_timer.isActive():
            self._anim_timer.start(8)

    def _refresh(self):
        if not self._term or not _termcore or self._cols <= 0 or self._rows <= 0:
            return
        total = self._cols * self._rows
        with self._paint_lock:
            if not self._cells or len(self._cells) < total:
                self._cells = (CCell * total)()

            prev_scroll = self._info.display_offset
            prev_sel = self._info.has_selection
            prev_col = self._info.cursor_col
            prev_row = self._info.cursor_row
            _termcore.term_snapshot(self._term, self._cells, total, ctypes.byref(self._info))

        if self._info.display_offset != prev_scroll:
            self.scrollChanged.emit()
            self._update_cursor_target(snap=True)
        elif self._info.cursor_col != prev_col or self._info.cursor_row != prev_row or not self._cur_initialized:
            self._update_cursor_target(snap=False)

        if self._info.has_selection != prev_sel:
            self.selectionChanged.emit()
        self.update()

    def _cached_color(self, val: int) -> QColor:
        c = self._color_cache.get(val)
        if c is None:
            if len(self._color_cache) >= 512:
                self._color_cache.clear()
            c = _rgb(val)
            self._color_cache[val] = c
        return c

    def _cached_pen(self, val: int) -> QPen:
        p = self._pen_cache.get(val)
        if p is None:
            if len(self._pen_cache) >= 512:
                self._pen_cache.clear()
            p = QPen(self._cached_color(val))
            self._pen_cache[val] = p
        return p

    # ---- Painting ----

    def paint(self, painter: QPainter):
        with self._paint_lock:
            if not self._term or not self._cells or self._info.cols <= 0 or self._info.rows <= 0:
                painter.fillRect(self.boundingRect(), self._bg)
                return

            cols, rows = self._info.cols, self._info.rows
            if len(self._cells) < cols * rows:
                painter.fillRect(self.boundingRect(), self._bg)
                return

            bg = self._cached_color(self._info.bg)
            painter.fillRect(self.boundingRect(), bg)

            x0, y0 = self._padding, self._padding
            w, h = self._cell_w, self._cell_h
            ascent = self._ascent
            cells = self._cells
            fonts = self._fonts
            def_bg = self._info.bg
            has_focus = self._has_focus
            c_col, c_row = self._info.cursor_col, self._info.cursor_row
            cursor_color_val = self._info.cursor_color
            cursor_text_val = self._info.cursor_text

            painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)

            # 1. Backgrounds (only for non-default cells, run-length merged)
            for r in range(rows):
                c = 0
                row_idx = r * cols
                y = y0 + r * h
                while c < cols:
                    bg_val = cells[row_idx + c].bg
                    if bg_val == def_bg:
                        c += 1
                        continue
                    start_c = c
                    while c < cols and cells[row_idx + c].bg == bg_val:
                        c += 1
                    painter.fillRect(QRectF(x0 + start_c * w, y, (c - start_c) * w, h), self._cached_color(bg_val))

            # 2. Text Glyphs
            for r in range(rows):
                row_idx = r * cols
                last_col = cols - 1
                while last_col >= 0 and cells[row_idx + last_col].ch <= 32:
                    last_col -= 1
                if last_col < 0:
                    continue

                y = y0 + r * h
                base_y = y + ascent
                c = 0
                while c <= last_col:
                    cell = cells[row_idx + c]
                    ch = cell.ch
                    flags = cell.flags
                    if (flags & CF_SPACER) or ch <= 32:
                        c += 1
                        continue

                    if ch < 127 and not (flags & (CF_HAS_EXTRA | CF_WIDE)):
                        fgc = cell.fg
                        style = flags & (CF_BOLD | CF_ITALIC)
                        font_idx = (1 if (style & CF_BOLD) else 0) | (2 if (style & CF_ITALIC) else 0)
                        start_c = c
                        run_chars = []
                        while c <= last_col:
                            d = cells[row_idx + c]
                            if (d.flags & (CF_SPACER | CF_HAS_EXTRA | CF_WIDE)) or d.ch >= 127:
                                break
                            if d.ch <= 32:
                                break
                            if (d.fg != fgc or (d.flags & (CF_BOLD | CF_ITALIC)) != style):
                                break
                            run_chars.append(chr(d.ch))
                            c += 1
                        painter.setFont(fonts[font_idx])
                        painter.setPen(self._cached_pen(fgc))
                        painter.drawText(QPointF(x0 + start_c * w, base_y), "".join(run_chars))
                    else:
                        painter.setFont(fonts[(1 if (flags & CF_BOLD) else 0) | (2 if (flags & CF_ITALIC) else 0)])
                        painter.setPen(self._cached_pen(cell.fg))
                        painter.drawText(QPointF(x0 + c * w, base_y), chr(ch))
                        c += 1

            # 3. Smooth Gliding Cursor (never touch QTimers or QQuickItem focus from QSGRenderThread paint())
            if 0 <= c_row < rows and 0 <= c_col < cols:
                if self._cur_initialized:
                    cur_x, cur_y = self._sample_cursor_motion(time.perf_counter())
                else:
                    cur_x, cur_y = x0 + c_col * w, y0 + c_row * h
                cur_w = w
                cur_rect = QRectF(cur_x, cur_y, cur_w, h)
                ccol = QColor(self._cached_color(cursor_color_val))
                if not has_focus:
                    painter.setPen(QPen(ccol, 1))
                    painter.setBrush(Qt.BrushStyle.NoBrush)
                    painter.drawRect(cur_rect.adjusted(0.5, 0.5, -0.5, -0.5))
                elif self._cursor_opacity > 0.02:
                    ccol.setAlphaF(self._cursor_opacity)
                    shape = self._cursor_shape_int()
                    bw = max(4.0, self._cursor_width)
                    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
                    if shape == 1:
                        painter.fillRect(QRectF(cur_rect.x(), cur_rect.bottom() - bw, cur_rect.width(), bw), ccol)
                    elif shape == 2:
                        r_rect = QRectF(cur_rect.x(), cur_rect.y(), bw, cur_rect.height())
                        painter.setPen(Qt.PenStyle.NoPen)
                        painter.setBrush(ccol)
                        painter.drawRoundedRect(r_rect, 1.2, 1.2)
                    else:
                        painter.fillRect(cur_rect, ccol)
                        cell = cells[c_row * cols + c_col]
                        if cell.ch > 32 and not (cell.flags & CF_SPACER):
                            tcol = QColor(self._cached_color(cursor_text_val))
                            tcol.setAlphaF(self._cursor_opacity)
                            painter.setFont(fonts[0])
                            painter.setPen(tcol)
                            painter.drawText(QPointF(cur_rect.x(), cur_rect.y() + ascent), chr(cell.ch))
                    painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)

    # ---- Input Events ----

    def _mods_of(self, m: Qt.KeyboardModifiers) -> int:
        r = 0
        if m & Qt.KeyboardModifier.ShiftModifier: r |= TM_SHIFT
        if m & Qt.KeyboardModifier.AltModifier: r |= TM_ALT
        if m & Qt.KeyboardModifier.ControlModifier: r |= TM_CTRL
        if m & Qt.KeyboardModifier.MetaModifier: r |= TM_SUPER
        return r

    def keyPressEvent(self, e: QKeyEvent):
        if not self._term or not _termcore:
            e.ignore()
            return
        m = e.modifiers()
        k = e.key()
        if (m == (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier) and k == Qt.Key.Key_C):
            self.copy()
            e.accept()
            return
        if m == Qt.KeyboardModifier.ControlModifier and k == Qt.Key.Key_C and self._info.has_selection:
            self.copy()
            self.clearSelection()
            e.accept()
            return
        if self._output_only:
            if m == Qt.KeyboardModifier.ControlModifier and k == Qt.Key.Key_C:
                self.interruptRequested.emit()
                e.accept()
            else:
                e.ignore()
            return
        if self._native_cmd_running:
            if m == Qt.KeyboardModifier.ControlModifier and k == Qt.Key.Key_C:
                if self._native_cancel is not None:
                    try:
                        self._native_cancel.cancel()
                    except Exception:
                        pass
                self.feedText("^C\r\n")
            e.accept()
            return
        if (m == (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier) and k == Qt.Key.Key_V) or (m == Qt.KeyboardModifier.ShiftModifier and k == Qt.Key.Key_Insert):
            self.paste()
            e.accept()
            return

        if _is_modifier_only(k):
            e.accept()
            return

        key = _map_qt_key(k)
        if key == TK_ENTER and (m & ~Qt.KeyboardModifier.KeypadModifier) == Qt.KeyboardModifier.NoModifier:
            if self._try_intercept_prompt_command():
                e.accept()
                return
        mods = self._mods_of(m)
        text = e.text()
        base = 0
        if key == TK_CHAR:
            if text and (ord(text[0]) < 0x20 or ord(text[0]) == 0x7F):
                text = ""
            if 0x20 <= k <= 0x7E:
                base = k + 32 if ord('A') <= k <= ord('Z') else k
            elif text:
                base = ord(text[0])

        t_bytes = text.encode("utf-8") if text else b""
        c_col = self._info.cursor_col
        c_row = self._info.cursor_row

        handled = _termcore.term_key(
            self._term, key, base, t_bytes, mods, TEV_REPEAT if e.isAutoRepeat() else TEV_PRESS
        )
        if handled:
            now = time.perf_counter()
            self._last_repeat_key_time = now if e.isAutoRepeat() else 0.0

            # Keep cursor solid ON immediately during typing
            self._cursor_opacity = 1.0
            self._cursor_cycle_start = now - 0.08
            self._cursor_on = True
            if not self._anim_timer.isActive():
                self._anim_timer.start()

            self.update()
            e.accept()
        else:
            e.ignore()

    def mousePressEvent(self, e: QMouseEvent):
        self.forceActiveFocus()
        if not self._term or not _termcore:
            return
        c = int((e.position().x() - self._padding) / self._cell_w)
        r = int((e.position().y() - self._padding) / self._cell_h)
        if e.button() == Qt.MouseButton.LeftButton:
            _termcore.term_sel_start(self._term, 0, c, r, 0)
            self._refresh()
            e.accept()

    def mouseMoveEvent(self, e: QMouseEvent):
        if not self._term or not _termcore:
            return
        c = int((e.position().x() - self._padding) / self._cell_w)
        r = int((e.position().y() - self._padding) / self._cell_h)
        if e.buttons() & Qt.MouseButton.LeftButton:
            _termcore.term_sel_update(self._term, c, r, 0)
            self._refresh()
            e.accept()

    def mouseReleaseEvent(self, e: QMouseEvent):
        if not self._term or not _termcore:
            return
        if e.button() == Qt.MouseButton.LeftButton:
            self._refresh()
            e.accept()

    def wheelEvent(self, e: QWheelEvent):
        if not self._term or not _termcore:
            return
        delta = e.angleDelta().y()
        lines = int(delta / 40.0) if delta else 0
        if lines:
            _termcore.term_scroll(self._term, lines, 0, 0, self._mods_of(e.modifiers()))
            self._refresh()
            e.accept()

    def focusInEvent(self, e):
        super().focusInEvent(e)
        self._has_focus = True
        if self._term and _termcore:
            _termcore.term_focus(self._term, 1)
        self._cursor_on = True
        self._cursor_opacity = 1.0
        self._cursor_cycle_start = time.perf_counter() - 0.08
        if self._cursor_blink and not self._anim_timer.isActive():
            self._anim_timer.start(16)
        self.update()

    def focusOutEvent(self, e):
        super().focusOutEvent(e)
        self._has_focus = False
        if self._term and _termcore:
            _termcore.term_focus(self._term, 0)
        self._cursor_opacity = 0.0
        self._cursor_on = False
        if self._cur_move_start <= 0.0:
            self._anim_timer.stop()
        self.update()


def register_qml_types():
    """Register QtTerm QML module so `import QtTerm 1.0` works natively."""
    qmlRegisterType(Terminal, "QtTerm", 1, 0, "Terminal")
