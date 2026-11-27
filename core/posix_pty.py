# SPDX-License-Identifier: MPL-2.0
"""Lightweight POSIX PTY wrapper matching the winpty.PTY interface on macOS & Linux."""
from __future__ import annotations

import codecs
import os
import shlex
import signal
import struct
import subprocess
import sys
import threading
from typing import Optional

if sys.platform != "win32":
    import errno as _errno
    import fcntl as _fcntl
    import pty as _posix_pty_mod
    import select as _select
    import termios as _termios
    HAS_POSIX_PTY = True
else:
    HAS_POSIX_PTY = False


class PosixPty:
    """Minimal POSIX PTY process manager compatible with PtyProcessManager."""

    def __init__(self, cols: int, rows: int):
        if not HAS_POSIX_PTY:
            raise RuntimeError("POSIX PTY is only available on macOS and Linux.")
        self._cols = max(2, int(cols))
        self._rows = max(1, int(rows))
        self._master_fd: int = -1
        self._proc: Optional[subprocess.Popen] = None
        self._eof = False
        self._decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        self._lock = threading.Lock()

    def _apply_winsize(self, fd: int, cols: int, rows: int) -> None:
        try:
            winsize = struct.pack("HHHH", rows, cols, 0, 0)
            _fcntl.ioctl(fd, _termios.TIOCSWINSZ, winsize)
        except OSError:
            pass

    def spawn(self, appname: str, cmdline: str = "", cwd: Optional[str] = None, env: Optional[dict] = None) -> bool:
        master_fd, slave_fd = _posix_pty_mod.openpty()
        self._apply_winsize(slave_fd, self._cols, self._rows)
        argv = [appname]
        if cmdline:
            argv.extend(shlex.split(cmdline))
        try:
            self._proc = subprocess.Popen(
                argv,
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                cwd=cwd or os.getcwd(),
                env=env,
                start_new_session=True,
                close_fds=True,
            )
        except Exception:
            os.close(master_fd)
            os.close(slave_fd)
            raise
        os.close(slave_fd)
        self._master_fd = master_fd
        return True

    def read(self, blocking: bool = False) -> str:
        fd = self._master_fd
        if fd < 0:
            self._eof = True
            return ""
        timeout = None if blocking else 0.0
        try:
            readable, _, _ = _select.select([fd], [], [], timeout)
        except (OSError, ValueError):
            self._eof = True
            return ""
        if not readable:
            return ""
        try:
            chunk = os.read(fd, 4096)
        except OSError as exc:
            if exc.errno in (_errno.EIO, _errno.EBADF):
                self._eof = True
                return ""
            raise
        if not chunk:
            self._eof = True
            return self._decoder.decode(b"", final=True)
        return self._decoder.decode(chunk, final=False)

    def write(self, text: str) -> None:
        with self._lock:
            fd = self._master_fd
            if fd < 0:
                return
            data = text.encode("utf-8", errors="replace")
            view = memoryview(data)
            while view:
                written = os.write(fd, view)
                view = view[written:]

    def set_size(self, cols: int, rows: int) -> None:
        self._cols = max(2, int(cols))
        self._rows = max(1, int(rows))
        with self._lock:
            if self._master_fd >= 0:
                self._apply_winsize(self._master_fd, self._cols, self._rows)
            if self._proc and self._proc.poll() is None:
                try:
                    os.killpg(os.getpgid(self._proc.pid), signal.SIGWINCH)
                except OSError:
                    pass

    def isalive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def iseof(self) -> bool:
        return self._eof

    def get_exitstatus(self) -> int:
        if self._proc is None:
            return 0
        rc = self._proc.poll()
        return 0 if rc is None else int(rc)

    def cancel_io(self) -> None:
        with self._lock:
            fd = self._master_fd
            self._master_fd = -1
        if fd >= 0:
            try:
                os.close(fd)
            except OSError:
                pass
        if self._proc is not None and self._proc.poll() is None:
            try:
                os.killpg(os.getpgid(self._proc.pid), signal.SIGHUP)
            except OSError:
                try:
                    self._proc.terminate()
                except OSError:
                    pass
            try:
                self._proc.wait(timeout=0.5)
            except Exception:
                try:
                    self._proc.kill()
                except OSError:
                    pass
