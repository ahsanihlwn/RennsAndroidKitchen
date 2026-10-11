# SPDX-License-Identifier: MPL-2.0
import sys
import os
import shlex
import json
from typing import Optional

from PySide6.QtWidgets import QWidget, QVBoxLayout
from PySide6.QtCore import Qt, QUrl, QTimer, Signal
from PySide6.QtGui import QColor, QDragEnterEvent, QDragMoveEvent, QDropEvent
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWebEngineCore import QWebEngineSettings
from PySide6.QtWebChannel import QWebChannel

from core.terminal_binary import resolve_terminal_binary
from core.terminal_pty import PtyProcessManager, TerminalBridge, BIN_PATH
from core.app_paths import TERMINAL_RUNTIME_DIR

def _th():
    from main_ext.class_ThemeUi import theme as _t
    return _t

def normalize_terminal_command(cmd: str, bin_root: str | None):

    if not bin_root:
        return cmd

    try:
        parts = shlex.split(cmd, posix=False)
    except ValueError:
        return cmd

    if not parts:
        return cmd

    binary = parts[0]
    args   = parts[1:]

    # Try resolve dari custom bin directory
    real = resolve_terminal_binary(bin_root, binary)
    if real:
        return " ".join([f'"{real}"'] + args)
    else:
        return cmd


class _DocumentAdapter:

    def __init__(self, pty_mgr: PtyProcessManager):
        self._pty_mgr = pty_mgr

    def toPlainText(self) -> str:
        return self._pty_mgr.get_plain_text()


print("[CORE STAGE] WEB HYBRID TERMINAL")


class TerminalWebView(QWebEngineView):

    def __init__(self, terminal_widget: "Terminal", parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.terminal_widget = terminal_widget
        self.setAcceptDrops(True)

    def dragEnterEvent(self, event: QDragEnterEvent):
        if self._can_accept_drag(event):
            event.setDropAction(Qt.CopyAction)
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event: QDragMoveEvent):
        if self._can_accept_drag(event):
            event.setDropAction(Qt.CopyAction)
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event: QDropEvent):
        paths = self._extract_drag_paths(event)
        if paths:
            try:
                insert_text = " ".join(self.terminal_widget.quote_dropped_path(p) for p in paths)
            except ValueError:
                event.ignore()
                return
            if insert_text:
                self.terminal_widget.pty_mgr.write(insert_text + " ")
                self.terminal_widget.setFocus()
                event.setDropAction(Qt.CopyAction)
                event.acceptProposedAction()
                return

        event.ignore()

    def _can_accept_drag(self, event) -> bool:
        md = event.mimeData()
        if md.hasUrls() or md.hasText() or md.hasFormat("application/x-rdt-archive"):
            return True

        main = self.window()
        if main and (getattr(main, "drag_src_paths", None) or getattr(main, "drag_src_path", None)):
            return True

        try:
            from main import Main
            if getattr(Main, "instance", None) and getattr(Main.instance, "drag_src_paths", None):
                return True
        except Exception:
            pass

        return False

    def _extract_drag_paths(self, event) -> list[str]:
        md = event.mimeData()
        paths = []

        # 1. External drop from Windows Explorer / Desktop / URI list
        if md.hasUrls():
            for url in md.urls():
                local = url.toLocalFile()
                if local:
                    paths.append(local)

        # 2. Internal drop from RAK FileTree (drag_src_paths / drag_src_phys_paths)
        if not paths:
            main = self.window()
            src = getattr(main, "drag_src_paths", None) if main else None
            if not src and main:
                src = getattr(main, "drag_src_phys_paths", None)
            if not src and main and getattr(main, "drag_src_path", None):
                src = [getattr(main, "drag_src_path", None)]

            if not src:
                try:
                    from main import Main
                    inst = getattr(Main, "instance", None)
                    if inst:
                        src = (
                            getattr(inst, "drag_src_paths", None) or
                            getattr(inst, "drag_src_phys_paths", None) or
                            [getattr(inst, "drag_src_path", None)]
                        )
                except Exception:
                    pass

            if src:
                paths = [p for p in src if p]

        # 3. Drop from ArchiveTab
        if not paths and md.hasFormat("application/x-rdt-archive"):
            try:
                raw = bytes(md.data("application/x-rdt-archive")).decode("utf-8", errors="ignore")
                paths = [p for p in raw.split("\n") if p.strip()]
            except Exception:
                pass

        # 4. Plain text drop
        if not paths and md.hasText():
            t = md.text().strip()
            if t:
                paths = [t]

        return paths


class Terminal(QWidget):


    firstCommandSubmitted = Signal(str)

    def __init__(self, cwd: Optional[str] = None, bin_root: Optional[str] = None,
                 spam_enter: bool = False, bash_mode: bool = False,
                 parent: Optional[QWidget] = None, *, wsl_mode: bool = False):
        super().__init__(parent)
        self.bin_root = bin_root or BIN_PATH
        self.current_cwd = str(cwd or os.getcwd()) if wsl_mode else os.path.normpath(cwd or os.getcwd())
        self.is_live_command = False
        self._ready = False
        self._tab_icon_key = "icon_sh"
        self._terminal_tab_name = ""
        self._first_command_seen = False
        self.wsl_mode = wsl_mode
        self.bash_mode = bash_mode and not wsl_mode

        # Outer layout
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Initialize PTY Manager & Bridge
        self.pty_mgr = PtyProcessManager(
            cwd=self.current_cwd,
            bin_root=self.bin_root,
            spam_enter=spam_enter,
            bash_mode=self.bash_mode,
            wsl_mode=self.wsl_mode,
        )
        self.bridge = TerminalBridge(self.pty_mgr, self)
        self.bridge.commandSubmitted.connect(self._record_first_command)

        # Initialize WebEngineView with drag & drop support
        self.view = TerminalWebView(self, self)
        self.view.setAttribute(Qt.WA_OpaquePaintEvent, True)

        # Set background to prevent white flicker during load
        th = _th()
        bg_color = getattr(th, "BG", "#121417")
        self.view.setStyleSheet(f"background: {bg_color};")
        self.view.page().setBackgroundColor(QColor(bg_color))

        # Configure WebEngine Settings
        s = self.view.settings()
        s.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True)
        s.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True)
        s.setAttribute(QWebEngineSettings.WebAttribute.JavascriptEnabled, True)
        s.setAttribute(QWebEngineSettings.WebAttribute.ScrollAnimatorEnabled, False)

        # Set up QWebChannel
        self.channel = QWebChannel(self.view.page())
        self.channel.registerObject("bridge", self.bridge)
        self.view.page().setWebChannel(self.channel)

        # Start PTY Process (cmd.exe or the user's WSL shell).
        try:
            self.pty_mgr.start(cols=80, rows=24)
        except Exception:
            self.pty_mgr.shutdown()
            self.deleteLater()
            raise

        # Connect terminal ready signal
        self.bridge.terminalReady.connect(self._on_terminal_ready)

        # Load local HTML file with robust fallback resolution
        html_file = self._resolve_terminal_html()
        self.view.load(QUrl.fromLocalFile(html_file))

        layout.addWidget(self.view)

        # Enable Drag & Drop
        self.setAcceptDrops(True)

    @staticmethod
    def _resolve_terminal_html() -> str:
        core_dir = os.path.dirname(os.path.abspath(__file__))
        cand = os.path.join(core_dir, "terminal.html")
        if os.path.isfile(cand):
            return cand

        exe_dir = os.path.dirname(os.path.abspath(sys.executable))
        candidates = [
            os.path.join(exe_dir, "core", "terminal.html"),
            os.path.join(exe_dir, "terminal.html"),
            os.path.join(TERMINAL_RUNTIME_DIR, "terminal.html"),
        ]
        for c in candidates:
            if c and os.path.isfile(c):
                return c

        return cand

    def _on_terminal_ready(self):
        self._ready = True
        self._sync_theme()
        self.setFocus()

    def _sync_theme(self):

        try:
            th = _th()
            bg = getattr(th, "BG", "#121417")
            fg = getattr(th, "FG", "#e2e8f0")
            accent = getattr(th, "ACCENT", "#38bdf8")
            theme_data = {
                "theme": {
                    "background": bg,
                    "foreground": fg,
                    "cursor": accent,
                    "cursorAccent": bg,
                    "selectionBackground": "rgba(56, 189, 248, 0.35)",
                },
                "bg": bg,
                "fontSize": 15
            }
            self.bridge.themeChanged.emit(json.dumps(theme_data))
        except Exception:
            pass

    def document(self):

        return _DocumentAdapter(self.pty_mgr)

    def set_bash_mode(self, enabled: bool):

        self.bash_mode = enabled and not self.wsl_mode
        if hasattr(self, "pty_mgr") and self.pty_mgr:
            self.pty_mgr.set_bash_mode(self.bash_mode)

    def quote_dropped_path(self, path: str) -> str:

        if self.wsl_mode:
            from core.wsl_manager import windows_to_wsl_path
            return shlex.quote(windows_to_wsl_path(path))
        path = str(path)
        if os.path.isabs(path) or "\\" in path or "/" in path:
            path = os.path.normpath(path)
        return f'"{path}"' if any(ch in path for ch in " &^()") else path

    def run_command_silent(self, raw_cmd: str, retries: int = 50) -> bool:

        if not raw_cmd:
            return False

        if getattr(self, "bash_mode", False):
            from core.unix_bash_converter import UnixBashConverter
            raw_cmd = UnixBashConverter.convert(raw_cmd)

        self._record_first_command(raw_cmd)
        real_cmd = raw_cmd if self.wsl_mode else normalize_terminal_command(raw_cmd, self.bin_root)
        self.bridge.run_command(real_cmd)
        return True

    def run_binary(self, raw_cmd: str):
        return self.run_command_silent(raw_cmd)

    def convert_bash_to_windows(self, raw_bash_cmd: str) -> str:

        from core.unix_bash_converter import UnixBashConverter
        return UnixBashConverter.convert(raw_bash_cmd)

    def run_bash_command(self, raw_bash_cmd: str, retries: int = 50) -> bool:

        if not raw_bash_cmd:
            return False

        if self.wsl_mode:
            return self.run_command_silent(raw_bash_cmd, retries=retries)

        from core.unix_bash_converter import UnixBashConverter
        win_cmd = UnixBashConverter.convert(raw_bash_cmd)
        if not win_cmd:
            return False

        self._record_first_command(raw_bash_cmd)
        real_cmd = normalize_terminal_command(win_cmd, self.bin_root)
        self.bridge.run_command(real_cmd)
        return True

    def _record_first_command(self, command: str):

        clean = str(command or "").strip()
        if not clean or self._first_command_seen:
            return
        self._first_command_seen = True
        self.firstCommandSubmitted.emit(clean)

    def set_spam_enter(self, enabled: bool):
        self.pty_mgr.set_spam_enter(enabled)

    def shutdown(self):

        try:
            self.pty_mgr.shutdown()
        except Exception:
            pass

    def setFocus(self):
        super().setFocus()
        self.view.setFocus()
        self.view.page().runJavaScript("if (window.term) window.term.focus();")

    def showEvent(self, event):
        super().showEvent(event)
        self.setFocus()
        # Re-fit terminal dimensions when tab is switched/shown
        QTimer.singleShot(50, lambda: self.view.page().runJavaScript(
            "if (window.requestFit) window.requestFit(); if (window.term) window.term.focus();"
        ))
        QTimer.singleShot(150, lambda: self.view.page().runJavaScript(
            "if (window.requestFit) window.requestFit(); if (window.term) window.term.focus();"
        ))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.view.page().runJavaScript(
            "if (window.requestFit) window.requestFit();"
        )

    def dragEnterEvent(self, event):
        self.view.dragEnterEvent(event)

    def dragMoveEvent(self, event):
        self.view.dragMoveEvent(event)

    def dropEvent(self, event):
        self.view.dropEvent(event)
