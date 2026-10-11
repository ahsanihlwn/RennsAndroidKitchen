# SPDX-License-Identifier: MPL-2.0
import os
import sys
import re
import time
import json
import subprocess
import threading
from typing import Optional

from PySide6.QtCore import QObject, Signal, Slot
from PySide6.QtGui import QColor
from core.app_paths import BIN_DIR

try:
    from winpty import PTY
    import winpty.enums as winpty_enums
    HAS_WINPTY = True
except ImportError:
    HAS_WINPTY = False
    PTY = None
    winpty_enums = None

BIN_PATH = BIN_DIR

from core.terminal_binary import (
    get_terminal_binary_directories,
    get_external_cli_directories,
    get_windows_path_directories,
)
# Regex to strip ANSI escape codes for toPlainText() buffer
_ANSI_STRIP_RE = re.compile(r"\x1b(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")


class PtySignals(QObject):
    data_ready = Signal(str)
    process_exited = Signal(int)


class PtyProcessManager:


    def __init__(self, cwd: Optional[str] = None, bin_root: Optional[str] = None,
                 spam_enter: bool = False, bash_mode: bool = False,
                 wsl_mode: bool = False):
        self.cwd = str(cwd or os.getcwd()) if wsl_mode else os.path.normpath(cwd or os.getcwd())
        self.bin_root = bin_root or BIN_PATH
        self.wsl_mode = wsl_mode
        self.bash_mode = bash_mode and not wsl_mode
        self.pty: Optional[PTY] = None
        self.signals = PtySignals()
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._buffer: list[str] = []
        self._buffer_lock = threading.Lock()
        self._max_buffer_chars = 100_000
        self._last_cols = -1
        self._last_rows = -1
        self._spam_enter_enabled = threading.Event()
        self._spam_enter_stop = threading.Event()
        self._spam_enter_thread: Optional[threading.Thread] = None
        self.set_spam_enter(spam_enter)

    def set_bash_mode(self, enabled: bool):
        self.bash_mode = enabled and not self.wsl_mode

    def set_spam_enter(self, enabled: bool):

        if enabled:
            self._spam_enter_enabled.set()
            if self._spam_enter_thread is None or not self._spam_enter_thread.is_alive():
                self._spam_enter_stop.clear()
                self._spam_enter_thread = threading.Thread(
                    target=self._spam_enter_loop,
                    name="RAK-TerminalSpamEnter",
                    daemon=True,
                )
                self._spam_enter_thread.start()
        else:
            self._spam_enter_enabled.clear()

    def _spam_enter_loop(self):

        while not self._spam_enter_stop.is_set():
            if not self._spam_enter_enabled.wait(0.10):
                continue
            if self._spam_enter_stop.is_set():
                break
            self.write("\r")
            self._spam_enter_stop.wait(0.20)

    def start(self, cols: int = 80, rows: int = 24, shell_cmd: Optional[str] = None):

        if not HAS_WINPTY:
            raise RuntimeError("pywinpty is not available in the current environment.")

        # Prepare environment: ensure all binary directories from package.json and BIN_PATH are in PATH
        env = os.environ.copy()
        current_path = env.get("PATH", "")
        extra_paths = []
        if self.bin_root and not self.wsl_mode:
            for p in get_terminal_binary_directories(self.bin_root):
                if p not in extra_paths:
                    extra_paths.append(p)
        if BIN_PATH and BIN_PATH != self.bin_root and not self.wsl_mode:
            for p in get_terminal_binary_directories(BIN_PATH):
                if p not in extra_paths:
                    extra_paths.append(p)
        for p in (get_external_cli_directories() if not self.wsl_mode else []):
            if p not in extra_paths:
                extra_paths.append(p)
        for p in (get_windows_path_directories() if not self.wsl_mode else []):
            if p not in extra_paths:
                extra_paths.append(p)

        if extra_paths:
            env["PATH"] = os.pathsep.join(extra_paths) + os.pathsep + current_path

        # Force interactive terminal environment variables
        env["TERM"] = "xterm-256color"
        env["COLORTERM"] = "truecolor"

        # Launch WSL directly in ConPTY so input, resize and Ctrl+C stay interactive.
        if self.wsl_mode:
            from core.wsl_manager import _wsl_path, distro_for_path, windows_to_wsl_path
            appname = _wsl_path()
            if not appname:
                raise RuntimeError("WSL is not available on this Windows installation.")
            args = []
            distro = distro_for_path(self.cwd)
            if distro:
                args.extend(["--distribution", distro])
            args.extend(["--cd", windows_to_wsl_path(self.cwd)])
            cmdline = subprocess.list2cmdline(args)
            if shell_cmd:
                cmdline += " " + shell_cmd
        elif sys.platform == "win32":
            appname = os.environ.get("COMSPEC", r"C:\Windows\System32\cmd.exe")
            cmdline = shell_cmd or "/k"
        else:
            appname = os.environ.get("SHELL", "/bin/bash")
            cmdline = shell_cmd or ""

        cols = max(80, cols)
        rows = max(15, rows)
        self._last_cols = cols
        self._last_rows = rows

        # Use native Windows ConPTY for proper process resize events (Node.js, Claude Code, etc.)
        backend = getattr(winpty_enums.Backend, "ConPTY", None) if winpty_enums else None
        try:
            self.pty = PTY(cols, rows, backend=backend)
        except Exception:
            self.pty = PTY(cols, rows)

        # Format env string as required by winpty: key=val\0key=val\0\0
        env_strs = [f"{k}={v}" for k, v in env.items()]
        env_blob = "\0".join(env_strs) + "\0\0"

        # Ensure cwd exists
        launch_dir = self.cwd if os.path.isdir(self.cwd) else os.getcwd()
        if not self.pty.spawn(appname, cmdline=cmdline, cwd=launch_dir, env=env_blob):
            raise RuntimeError(f"Could not start the terminal shell: {appname}")

        self._running = True
        self._thread = threading.Thread(target=self._reader_loop, daemon=True)
        self._thread.start()

    def _reader_loop(self):

        while self._running and self.pty:
            try:
                data = self.pty.read(blocking=False)
                if data:
                    # Append to plain text buffer
                    clean = _ANSI_STRIP_RE.sub("", data)
                    with self._buffer_lock:
                        self._buffer.append(clean)
                        # Trim if buffer gets too large
                        total_len = sum(len(x) for x in self._buffer)
                        if total_len > self._max_buffer_chars:
                            # Keep recent chunks
                            while self._buffer and total_len > self._max_buffer_chars:
                                popped = self._buffer.pop(0)
                                total_len -= len(popped)

                    self.signals.data_ready.emit(data)
                else:
                    if self.pty.iseof() or not self.pty.isalive():
                        break
                    time.sleep(0.01)
            except Exception:
                break

        exit_code = 0
        try:
            if self.pty:
                exit_code = self.pty.get_exitstatus() or 0
        except Exception:
            pass
        self.signals.process_exited.emit(exit_code)

    def write(self, text: str):

        if self._running and self.pty:
            try:
                self.pty.write(text)
            except Exception:
                pass

    def resize(self, cols: int, rows: int):

        cols = max(80, cols)
        rows = max(15, rows)
        if cols == self._last_cols and rows == self._last_rows:
            return
        self._last_cols = cols
        self._last_rows = rows
        if self._running and self.pty:
            try:
                self.pty.set_size(cols, rows)
            except Exception:
                pass

    def get_plain_text(self) -> str:

        with self._buffer_lock:
            return "".join(self._buffer)

    def shutdown(self):

        self._running = False
        self._spam_enter_stop.set()
        self._spam_enter_enabled.set()
        if self._spam_enter_thread and self._spam_enter_thread.is_alive():
            self._spam_enter_thread.join(0.25)
        if self.pty:
            try:
                self.pty.cancel_io()
            except Exception:
                pass
            self.pty = None


class TerminalBridge(QObject):

    outputReceived = Signal(str)
    themeChanged = Signal(str)
    clearRequested = Signal()
    terminalReady = Signal()
    commandSubmitted = Signal(str)

    def __init__(self, pty_mgr: PtyProcessManager, parent: Optional[QObject] = None):
        super().__init__(parent)
        self.pty_mgr = pty_mgr
        self.is_ready = False
        self._pending_commands: list[str] = []
        self._pending_output: list[str] = []
        self._output_lock = threading.Lock()

        # Connect PTY data to JS output signal
        self.pty_mgr.signals.data_ready.connect(self._on_pty_data)

    def _on_pty_data(self, chunk: str):
        with self._output_lock:
            if not self.is_ready:
                self._pending_output.append(chunk)
                return
        self.outputReceived.emit(chunk)

    @Slot(str)
    def sendInput(self, data: str):

        if not hasattr(self, "_line_buffer"):
            self._line_buffer = []

        is_enter = False
        i = 0
        n = len(data)
        while i < n:
            ch = data[i]
            if ch in ('\r', '\n'):
                is_enter = True
                i += 1
            elif ch in ('\x08', '\x7f'):  # Backspace (DEL or BS)
                if self._line_buffer:
                    self._line_buffer.pop()
                i += 1
            elif ch in ('\x03', '\x15'):  # Ctrl+C or Ctrl+U (line kill)
                self._line_buffer.clear()
                i += 1
            elif ch == '\x1b':  # Escape sequence (e.g. arrow keys \x1b[A, etc.)
                if i + 1 < n and data[i + 1] == '[':
                    i += 2
                    while i < n and data[i] not in 'ABCDHJKmnsu~':
                        i += 1
                    i += 1
                else:
                    i += 1
            elif ord(ch) >= 32 or ch == '\t':  # Printable characters and tab
                self._line_buffer.append(ch)
                i += 1
            else:
                i += 1

        if is_enter:
            chars_typed = len(self._line_buffer)
            raw_line = "".join(self._line_buffer).strip()
            self._line_buffer.clear()

            # If line buffer was empty (e.g. Up arrow / history), try extracting from recent terminal output
            if not raw_line:
                try:
                    recent = self.pty_mgr.get_plain_text().splitlines()
                    if recent:
                        last = recent[-1].strip()
                        for prompt_char in ('>', '$', '#', '%'):
                            if prompt_char in last:
                                cand = last.rsplit(prompt_char, 1)[-1].strip()
                                if cand:
                                    raw_line = cand
                                    break
                except Exception:
                    pass

            if raw_line:
                self.commandSubmitted.emit(raw_line)

            # Auto-convert Unix / Bash command if bash_mode is enabled
            if getattr(self.pty_mgr, "bash_mode", False) and raw_line:
                from core.unix_bash_converter import UnixBashConverter
                conv = UnixBashConverter.convert(raw_line)
                if conv and conv != raw_line:
                    erase_count = chars_typed or len(raw_line)
                    erase_seq = "\x08" * erase_count
                    self.pty_mgr.write(erase_seq + conv + "\r\n")
                    return

            # Also check recent plain text buffer from PTY in case command was from shell history / paste
            recent_text = ""
            try:
                recent_lines = self.pty_mgr.get_plain_text().splitlines()
                if recent_lines:
                    recent_text = recent_lines[-1].strip()
            except Exception:
                recent_text = ""

            try:
                from core.rak_agent_docs import handle_terminal_input
                handle_terminal_input(raw_line, cwd=self.pty_mgr.cwd, extra_line=recent_text)
            except Exception as e:
                print(f"[TerminalBridge] Error in handle_terminal_input: {e}", flush=True)

        self.pty_mgr.write(data)

    @Slot(int, int)
    def resizePty(self, cols: int, rows: int):

        self.pty_mgr.resize(cols, rows)

    @Slot(int, int)
    def onTerminalReady(self, cols: int, rows: int):

        with self._output_lock:
            self.is_ready = True
            # Flush any initial output (prompt, banner) that arrived while HTML was loading
            if self._pending_output:
                for chunk in self._pending_output:
                    self.outputReceived.emit(chunk)
                self._pending_output.clear()

        self.pty_mgr.resize(cols, rows)
        self.terminalReady.emit()

        # Flush any pending commands queued before terminal was ready
        if self._pending_commands:
            for cmd in self._pending_commands:
                self.pty_mgr.write(cmd + "\r\n")
            self._pending_commands.clear()

    def run_command(self, cmd: str):

        if not self.is_ready:
            self._pending_commands.append(cmd)
        else:
            self.pty_mgr.write(cmd + "\r\n")
