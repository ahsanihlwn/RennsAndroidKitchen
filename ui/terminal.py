# SPDX-License-Identifier: MPL-2.0
"""PTY lifetime and state for a native Qt Quick terminal pane."""
from __future__ import annotations

import json
import os
import shlex
import sys
from pathlib import Path

from PySide6.QtCore import QObject, Property, Signal, Slot, QTimer, QUrl, Qt
from PySide6.QtGui import QColor
from shiboken6 import isValid

from core.terminal_pty import PtyProcessManager, TerminalBridge, BIN_PATH


def _detect_terminal_font() -> str:
    if sys.platform == "win32":
        from PySide6.QtGui import QFont, QFontInfo
        for fam in ("Cascadia Mono", "Cascadia Code", "Consolas"):
            if QFontInfo(QFont(fam)).family().lower() == fam.lower():
                return fam
        return "Consolas"
    return "monospace"


class TerminalSession(QObject):
    activeChanged = Signal()
    configChanged = Signal()
    themeChanged = Signal()
    fontChanged = Signal()
    warningRaised = Signal(str, str)
    firstCommandSubmitted = Signal(str)
    commandActivitySettled = Signal()

    def __init__(self, cwd=None, *, bin_root=None, bash_mode=False, wsl_mode=False,
                 manager=None, parent=None):
        super().__init__(parent)
        self._cwd = str(cwd or "")
        self._bin_root = bin_root or BIN_PATH
        self._wsl_mode = wsl_mode
        self._bash_mode = bash_mode
        self._custom_manager = manager is not None
        self._manager = manager or PtyProcessManager(cwd=cwd, bin_root=self._bin_root,
                                                     bash_mode=bash_mode, wsl_mode=wsl_mode)
        self._bridge = TerminalBridge(self._manager, self)
        self._active = False
        self._theme = None
        self._first_command_seen = False
        self._term_item = None
        self._font_family = _detect_terminal_font()
        self._font_size = 15
        self._cursor_shape = "beam"
        self._cursor_width = 5.5
        self._fg = QColor("#cdd6f4")
        self._bg = QColor("#1e1e2e")
        self._cursor_color = QColor("#f5e0dc")
        self._selection_color = QColor("#585b7099")
        self._palette = []

        if self._wsl_mode:
            self._program = "wsl.exe"
            self._arguments = []
        elif not self._is_process_terminal_manager(self._manager):
            self._program = os.environ.get("COMSPEC", "cmd.exe") if sys.platform == "win32" else os.environ.get("SHELL", "/bin/bash")
            self._arguments = []
        else:
            self._program = ""
            self._arguments = []

        self._bridge.terminalReady.connect(self._send_theme)
        self._bridge.commandSubmitted.connect(self._on_command_activity)
        self._command_activity_pending = False
        self._activity_timer = QTimer(self)
        self._activity_timer.setSingleShot(True)
        self._activity_timer.setInterval(450)
        self._activity_timer.timeout.connect(self._emit_activity_settled)
        if hasattr(self._manager, "signals") and hasattr(self._manager.signals, "data_ready"):
            try:
                self._manager.signals.data_ready.connect(self._on_pty_output_activity, Qt.QueuedConnection)
            except Exception:
                pass

    @staticmethod
    def _is_process_terminal_manager(mgr):
        return type(mgr).__name__ == "ProcessTerminalManager"

    @Property(QObject, constant=True)
    def bridge(self):
        return self._bridge

    @Property(bool, notify=activeChanged)
    def active(self):
        return self._active

    @Property(QUrl, constant=True)
    def url(self):
        return QUrl()

    @Property(str, notify=configChanged)
    def program(self):
        return self._program

    @Property('QStringList', notify=configChanged)
    def arguments(self):
        return self._arguments

    @Property(str, notify=configChanged)
    def workingDirectory(self):
        return self._cwd

    @Property(bool, notify=configChanged)
    def autoStart(self):
        return bool(self._program)

    @Property(bool, constant=True)
    def wslMode(self):
        return self._wsl_mode

    @Property(str, notify=fontChanged)
    def fontFamily(self):
        return self._font_family

    @Property(int, notify=fontChanged)
    def fontPixelSize(self):
        return self._font_size

    @Property(str, notify=configChanged)
    def cursorShape(self):
        return self._cursor_shape

    @Property(float, notify=configChanged)
    def cursorWidth(self):
        return self._cursor_width

    @Property(QColor, notify=themeChanged)
    def foreground(self):
        return self._fg

    @Property(QColor, notify=themeChanged)
    def cursorColor(self):
        return self._cursor_color

    @Property(QColor, notify=themeChanged)
    def selectionColor(self):
        return self._selection_color

    @Property('QVariantList', notify=themeChanged)
    def palette(self):
        return self._palette

    @Slot(QObject)
    def bindTerminal(self, term_item):
        if self._term_item is term_item:
            return
        self._term_item = term_item
        if term_item is not None:
            if self._is_process_terminal_manager(self._manager):
                term_item.startOutput()
                term_item.interruptRequested.connect(self._interrupt_process_terminal)
            if hasattr(term_item, "nativeCommandSubmitted"):
                term_item.nativeCommandSubmitted.connect(self._on_command_activity)
            if hasattr(self._manager, "signals") and hasattr(self._manager.signals, "data_ready"):
                self._manager.signals.data_ready.connect(term_item.feedText)
            buf = getattr(self._manager, "_buffer", None)
            if buf:
                if isinstance(buf, list):
                    term_item.feedText("".join(buf))
                elif isinstance(buf, str):
                    term_item.feedText(buf)
            self._bridge.is_ready = True
            self._bridge.terminalReady.emit()
            self._send_theme()

    @Slot()
    def _interrupt_process_terminal(self):
        self._manager.write("\x03")

    @Slot(result=bool)
    def start(self):
        if self._active:
            return True
        if self._custom_manager:
            try:
                self._manager.start(cols=80, rows=24)
            except Exception as error:
                self._manager.shutdown()
                self.warningRaised.emit("Terminal", str(error))
                return False
        self._active = True
        self.activeChanged.emit()
        return True

    @Slot()
    def stop(self):
        self._activity_timer.stop()
        self._command_activity_pending = False
        if not self._active:
            return
        if self._term_item is not None and isValid(self._term_item):
            self._term_item.stop()
        self._term_item = None
        if self._custom_manager:
            self._manager.shutdown()
        self._active = False
        self._bridge.is_ready = False
        self.activeChanged.emit()

    @Slot("QVariantMap")
    def applyTheme(self, colors):
        is_light = bool(colors.get("is_light", False))
        background = colors.get("BG", "#ffffff" if is_light else "#121417")
        foreground = colors.get("FG", "#1a1a1a" if is_light else "#e2e8f0")
        accent = colors.get("accent_hex", colors.get("ACCENT", "#0284c7" if is_light else "#38bdf8"))
        selection_bg = colors.get("BG_SELECTED", "rgba(2, 132, 199, 0.35)" if is_light else "rgba(56, 189, 248, 0.35)")

        if is_light:
            ansi = {
                "black": "#1e293b", "red": "#dc2626", "green": "#16a34a", "yellow": "#ca8a04",
                "blue": "#2563eb", "magenta": "#9333ea", "cyan": "#0891b2", "white": "#475569",
                "brightBlack": "#64748b", "brightRed": "#ef4444", "brightGreen": "#22c55e",
                "brightYellow": "#eab308", "brightBlue": "#3b82f6", "brightMagenta": "#a855f7",
                "brightCyan": "#06b6d4", "brightWhite": "#0f172a",
            }
        else:
            ansi = {
                "black": "#1e293b", "red": "#ef4444", "green": "#22c55e", "yellow": "#eab308",
                "blue": "#3b82f6", "magenta": "#a855f7", "cyan": "#06b6d4", "white": "#f8fafc",
                "brightBlack": "#475569", "brightRed": "#f87171", "brightGreen": "#4ade80",
                "brightYellow": "#fde047", "brightBlue": "#60a5fa", "brightMagenta": "#c084fc",
                "brightCyan": "#22d3ee", "brightWhite": "#ffffff",
            }

        self._theme = {
            "theme": {
                "background": background,
                "foreground": foreground,
                "cursor": accent,
                "cursorAccent": background,
                "selectionBackground": selection_bg,
                **ansi
            },
            "bg": background,
            "fg": foreground,
            "is_light": is_light,
            "fontSize": 14
        }
        self._fg = QColor(foreground)
        self._bg = QColor(background)
        self._cursor_color = QColor(accent)
        self._selection_color = QColor(selection_bg)
        self._palette = [
            ansi["black"], ansi["red"], ansi["green"], ansi["yellow"],
            ansi["blue"], ansi["magenta"], ansi["cyan"], ansi["white"],
            ansi["brightBlack"], ansi["brightRed"], ansi["brightGreen"], ansi["brightYellow"],
            ansi["brightBlue"], ansi["brightMagenta"], ansi["brightCyan"], ansi["brightWhite"]
        ]
        self.themeChanged.emit()
        self._send_theme()

    def _send_theme(self):
        if self._theme is not None and self._bridge.is_ready:
            self._bridge.themeChanged.emit(json.dumps(self._theme))

    @Slot(str)
    def runCommand(self, command):
        if self._active:
            self._on_command_activity(command)
            if not self._wsl_mode:
                from core.terminal_native_router import is_locked_terminal_command
                if is_locked_terminal_command(command):
                    if self._term_item is not None and isValid(self._term_item) and hasattr(self._term_item, "runNativeCommand"):
                        self._term_item.runNativeCommand(command)
                        return
                    if self._custom_manager and hasattr(self._bridge, "run_locked_native"):
                        self._bridge.run_locked_native(command)
                        return
            from core.terminal_titles import normalize_terminal_command
            cmd_norm = command if self._wsl_mode else normalize_terminal_command(command, self._bin_root)
            if self._term_item is not None and isValid(self._term_item):
                self._term_item.sendText(cmd_norm + "\r")
            elif self._custom_manager:
                self._bridge.run_command(cmd_norm)

    @Slot('QVariantList')
    def insertDroppedPaths(self, paths):
        if not self._active:
            return
        def quote(path):
            path = path.toLocalFile() if isinstance(path, QUrl) else str(path)
            url = QUrl(path)
            if url.isLocalFile():
                path = url.toLocalFile()
            if self._wsl_mode:
                from core.wsl_manager import windows_to_wsl_path
                return shlex.quote(windows_to_wsl_path(path))
            if os.path.isabs(path) or '\\' in path or '/' in path:
                path = os.path.normpath(path)
            return '"' + path + '"' if any(ch in path for ch in ' &^()') else path
        value = ' '.join(quote(path) for path in paths if path)
        if value:
            if self._term_item is not None and isValid(self._term_item):
                self._term_item.sendText(value + ' ')
            elif self._custom_manager:
                self._manager.write(value + ' ')

    def _report_first_command(self, command):
        clean = str(command or '').strip()
        if clean and not self._first_command_seen:
            self._first_command_seen = True
            self.firstCommandSubmitted.emit(clean)

    @Slot(str)
    def _on_command_activity(self, command):
        self._report_first_command(command)
        if str(command or '').strip():
            self._command_activity_pending = True
            self._activity_timer.start()

    @Slot()
    def _on_pty_output_activity(self):
        if self._active and self._command_activity_pending:
            self._activity_timer.start()

    @Slot()
    def _emit_activity_settled(self):
        if not self._active or not self._command_activity_pending:
            return
        self._command_activity_pending = False
        self.commandActivitySettled.emit()
