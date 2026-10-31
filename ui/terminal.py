# SPDX-License-Identifier: MPL-2.0
"""PTY lifetime and WebChannel state for a native Qt Quick terminal pane."""
import json
import os
import shlex
from pathlib import Path

from PySide6.QtCore import QObject, Property, Signal, Slot, QUrl

from core.terminal_pty import PtyProcessManager, TerminalBridge, BIN_PATH


class TerminalSession(QObject):
    activeChanged = Signal()
    warningRaised = Signal(str, str)
    firstCommandSubmitted = Signal(str)

    def __init__(self, cwd=None, *, bin_root=None, bash_mode=False, wsl_mode=False,
                 manager=None, parent=None):
        super().__init__(parent)
        self._manager = manager or PtyProcessManager(cwd=cwd, bin_root=bin_root or BIN_PATH,
                                                     bash_mode=bash_mode, wsl_mode=wsl_mode)
        self._bridge = TerminalBridge(self._manager, self)
        self._active = False
        self._bin_root=bin_root or BIN_PATH
        self._wsl_mode=wsl_mode
        self._theme = None
        self._first_command_seen=False
        self._bridge.terminalReady.connect(self._send_theme)
        self._bridge.commandSubmitted.connect(self._report_first_command)

    @Property(QObject, constant=True)
    def bridge(self):
        return self._bridge

    @Property(bool, notify=activeChanged)
    def active(self):
        return self._active

    @Property(QUrl, constant=True)
    def url(self):
        return QUrl.fromLocalFile(str(Path(__file__).resolve().parents[1] / "core" / "terminal.html"))

    @Slot(result=bool)
    def start(self):
        if self._active:
            return True
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
        if not self._active:
            return
        self._manager.shutdown()
        self._active = False
        self._bridge.is_ready = False
        self.activeChanged.emit()

    @Slot("QVariantMap")
    def applyTheme(self, colors):
        background = colors.get("BG", "#121417")
        self._theme = {"theme": {"background": background,
                                 "foreground": colors.get("FG", "#e2e8f0"),
                                 "cursor": colors.get("accent_hex", colors.get("ACCENT", "#38bdf8")),
                                 "cursorAccent": background,
                                 "selectionBackground": "rgba(56, 189, 248, 0.35)"},
                       "bg": background, "fontSize": 15}
        self._send_theme()

    def _send_theme(self):
        if self._theme is not None and self._bridge.is_ready:
            self._bridge.themeChanged.emit(json.dumps(self._theme))

    @Slot(str)
    def runCommand(self, command):
        if self._active:
            self._report_first_command(command)
            from core.terminal_titles import normalize_terminal_command
            self._bridge.run_command(command if self._wsl_mode else normalize_terminal_command(command,self._bin_root))

    @Slot('QVariantList')
    def insertDroppedPaths(self,paths):
        if not self._active:return
        def quote(path):
            path=path.toLocalFile() if isinstance(path,QUrl) else str(path)
            url=QUrl(path)
            if url.isLocalFile():path=url.toLocalFile()
            if self._wsl_mode:
                from core.wsl_manager import windows_to_wsl_path
                return shlex.quote(windows_to_wsl_path(path))
            if os.path.isabs(path) or '\\' in path or '/' in path:path=os.path.normpath(path)
            return '"'+path+'"' if any(ch in path for ch in ' &^()') else path
        value=' '.join(quote(path) for path in paths if path)
        if value:self._manager.write(value+' ')

    def _report_first_command(self,command):
        clean=str(command or '').strip()
        if clean and not self._first_command_seen:
            self._first_command_seen=True
            self.firstCommandSubmitted.emit(clean)
