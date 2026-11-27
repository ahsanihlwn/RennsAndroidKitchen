# SPDX-License-Identifier: MPL-2.0
"""Recursive OS directory watcher for external / RAK Terminal / shell mutations.

On Windows, uses kernel `ReadDirectoryChangesW` with `FILE_FLAG_OVERLAPPED` and
`CancelIoEx` in a single daemon thread per workspace root—requiring zero external
dependencies and releasing the root handle synchronously on `stop()`.
On non-Windows platforms, degrades cleanly to a no-op stub (`start()` -> `False`).
"""
from __future__ import annotations

import logging
import os
import struct
import sys
import threading

_LOG = logging.getLogger(__name__)

_SKIP_ROOT_DIRS = frozenset({"config", "neobranch", "meta-inf", ".git", ".rak_quarantine"})
_SKIP_LEAF_NAMES = frozenset({"agents.md", "claude.md", "codex.md", "gemini.md"})
_SKIP_LEAF_PREFIXES = (".rak_", ".rak-agent-", ".rak-extract-", ".rak-transfer-")

_BUFFER_SIZE = 65536


def _should_ignore(rel_path: str) -> bool:
    rel = str(rel_path or "").replace("\\", "/").strip("/")
    if not rel:
        return True
    parts = [p for p in rel.split("/") if p]
    if not parts:
        return True
    first = parts[0].casefold()
    if first in _SKIP_ROOT_DIRS or first.startswith("."):
        return True
    leaf = parts[-1].casefold()
    if leaf in _SKIP_LEAF_NAMES or leaf.startswith(_SKIP_LEAF_PREFIXES):
        return True
    return False


if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _ULONG_PTR = ctypes.c_size_t

    class _OVERLAPPED(ctypes.Structure):
        _fields_ = [
            ("Internal", _ULONG_PTR),
            ("InternalHigh", _ULONG_PTR),
            ("Offset", wintypes.DWORD),
            ("OffsetHigh", wintypes.DWORD),
            ("hEvent", wintypes.HANDLE),
        ]

    _INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
    _FILE_LIST_DIRECTORY = 0x0001
    _FILE_SHARE_ALL = 0x0001 | 0x0002 | 0x0004
    _OPEN_EXISTING = 3
    _FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
    _FILE_FLAG_OVERLAPPED = 0x40000000
    _NOTIFY_FILTER = (
        0x00000001  # FILE_NOTIFY_CHANGE_FILE_NAME
        | 0x00000002  # FILE_NOTIFY_CHANGE_DIR_NAME
        | 0x00000008  # FILE_NOTIFY_CHANGE_SIZE
        | 0x00000010  # FILE_NOTIFY_CHANGE_LAST_WRITE
        | 0x00000040  # FILE_NOTIFY_CHANGE_CREATION
    )
    _WAIT_OBJECT_0 = 0x00000000
    _INFINITE = 0xFFFFFFFF
    _ERROR_IO_PENDING = 997
    _ERROR_OPERATION_ABORTED = 995
    _ERROR_NOTIFY_ENUM_DIR = 1022

    _kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    _kernel32.CreateFileW.restype = wintypes.HANDLE

    _kernel32.CreateEventW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
    _kernel32.CreateEventW.restype = wintypes.HANDLE

    _kernel32.SetEvent.argtypes = [wintypes.HANDLE]
    _kernel32.SetEvent.restype = wintypes.BOOL

    _kernel32.ResetEvent.argtypes = [wintypes.HANDLE]
    _kernel32.ResetEvent.restype = wintypes.BOOL

    _kernel32.ReadDirectoryChangesW.argtypes = [
        wintypes.HANDLE,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.BOOL,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        ctypes.POINTER(_OVERLAPPED),
        wintypes.LPVOID,
    ]
    _kernel32.ReadDirectoryChangesW.restype = wintypes.BOOL

    _kernel32.WaitForMultipleObjects.argtypes = [
        wintypes.DWORD,
        ctypes.POINTER(wintypes.HANDLE),
        wintypes.BOOL,
        wintypes.DWORD,
    ]
    _kernel32.WaitForMultipleObjects.restype = wintypes.DWORD

    _kernel32.GetOverlappedResult.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(_OVERLAPPED),
        ctypes.POINTER(wintypes.DWORD),
        wintypes.BOOL,
    ]
    _kernel32.GetOverlappedResult.restype = wintypes.BOOL

    _kernel32.CancelIoEx.argtypes = [wintypes.HANDLE, ctypes.POINTER(_OVERLAPPED)]
    _kernel32.CancelIoEx.restype = wintypes.BOOL

    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    _kernel32.CloseHandle.restype = wintypes.BOOL

    def _is_valid_handle(handle) -> bool:
        if not handle:
            return False
        return ctypes.c_void_p(handle).value != _INVALID_HANDLE_VALUE

    def _parse_notify_buffer(raw: bytes) -> list[tuple[int, str]]:
        out: list[tuple[int, str]] = []
        offset = 0
        total = len(raw)
        while offset + 12 <= total:
            next_offset, action, name_len = struct.unpack_from("<III", raw, offset)
            name_start = offset + 12
            name_end = min(total, name_start + name_len)
            rel_path = raw[name_start:name_end].decode("utf-16-le", errors="replace")
            if rel_path:
                out.append((action, rel_path))
            if next_offset <= 0:
                break
            offset += next_offset
        return out


class ProjectFsWatcher:
    """Thread-safe recursive directory watcher with debouncing and lock filtering."""

    def __init__(self, root_dir: str, on_batch, *, is_locked_fn=None, debounce_sec: float = 0.15):
        self.root_dir = os.path.abspath(os.path.normpath(root_dir))
        self._on_batch = on_batch
        self._is_locked_fn = is_locked_fn
        self._debounce_sec = max(0.02, float(debounce_sec))

        self._lock = threading.Lock()
        self._pending: dict[str, int] = {}
        self._overflowed = False
        self._timer: threading.Timer | None = None
        self._stopped = False
        self._started = False

        self._dir_handle = None
        self._stop_event = None
        self._io_event = None
        self._thread: threading.Thread | None = None
        self._ready_event = threading.Event()

    def start(self) -> bool:
        if sys.platform != "win32":
            return False
        if not self.root_dir or not os.path.isdir(self.root_dir):
            return False

        with self._lock:
            if self._started or self._stopped:
                return False
            dir_handle = _kernel32.CreateFileW(
                self.root_dir,
                _FILE_LIST_DIRECTORY,
                _FILE_SHARE_ALL,
                None,
                _OPEN_EXISTING,
                _FILE_FLAG_BACKUP_SEMANTICS | _FILE_FLAG_OVERLAPPED,
                None,
            )
            if not _is_valid_handle(dir_handle):
                return False

            stop_event = _kernel32.CreateEventW(None, True, False, None)
            io_event = _kernel32.CreateEventW(None, True, False, None)
            if not _is_valid_handle(stop_event) or not _is_valid_handle(io_event):
                for h in (dir_handle, stop_event, io_event):
                    if _is_valid_handle(h):
                        _kernel32.CloseHandle(h)
                return False

            self._dir_handle = dir_handle
            self._stop_event = stop_event
            self._io_event = io_event
            self._started = True
            self._ready_event.clear()
            self._thread = threading.Thread(
                target=self._run_loop,
                name=f"rak-fswatch:{os.path.basename(self.root_dir) or 'root'}",
                daemon=True,
            )
            self._thread.start()

        self._ready_event.wait(timeout=1.0)
        return True

    def _close_handles(self) -> None:
        with self._lock:
            handles = (self._dir_handle, self._stop_event, self._io_event)
            self._dir_handle = None
            self._stop_event = None
            self._io_event = None
        if sys.platform == "win32":
            for h in handles:
                if _is_valid_handle(h):
                    try:
                        _kernel32.CloseHandle(h)
                    except Exception:
                        pass

    def _run_loop(self) -> None:
        buf = ctypes.create_string_buffer(_BUFFER_SIZE)
        first_arm = True
        try:
            while True:
                with self._lock:
                    if self._stopped:
                        break
                    dir_handle = self._dir_handle
                    stop_event = self._stop_event
                    io_event = self._io_event
                if not (_is_valid_handle(dir_handle) and _is_valid_handle(stop_event) and _is_valid_handle(io_event)):
                    break

                _kernel32.ResetEvent(io_event)
                ov = _OVERLAPPED()
                ov.hEvent = io_event
                ok = _kernel32.ReadDirectoryChangesW(
                    dir_handle,
                    buf,
                    _BUFFER_SIZE,
                    True,
                    _NOTIFY_FILTER,
                    None,
                    ctypes.byref(ov),
                    None,
                )
                err = 0 if ok else ctypes.get_last_error()
                if first_arm:
                    first_arm = False
                    self._ready_event.set()

                if not ok and err not in (0, _ERROR_IO_PENDING):
                    if err == _ERROR_NOTIFY_ENUM_DIR:
                        self._enqueue([], overflowed=True)
                        continue
                    break

                wait_handles = (wintypes.HANDLE * 2)(stop_event, io_event)
                wait_res = _kernel32.WaitForMultipleObjects(2, wait_handles, False, _INFINITE)
                if wait_res == _WAIT_OBJECT_0 or self._stopped:
                    if _kernel32.CancelIoEx(dir_handle, ctypes.byref(ov)):
                        dummy = wintypes.DWORD(0)
                        _kernel32.GetOverlappedResult(dir_handle, ctypes.byref(ov), ctypes.byref(dummy), True)
                    break

                if wait_res != _WAIT_OBJECT_0 + 1:
                    break

                transferred = wintypes.DWORD(0)
                done_ok = _kernel32.GetOverlappedResult(dir_handle, ctypes.byref(ov), ctypes.byref(transferred), False)
                if not done_ok:
                    done_err = ctypes.get_last_error()
                    if done_err == _ERROR_OPERATION_ABORTED:
                        break
                    if done_err == _ERROR_NOTIFY_ENUM_DIR:
                        self._enqueue([], overflowed=True)
                        continue
                    break

                if transferred.value == 0:
                    self._enqueue([], overflowed=True)
                    continue

                raw = ctypes.string_at(buf, transferred.value)
                events = _parse_notify_buffer(raw)
                batch: list[tuple[str, int]] = []
                for action, rel_path in events:
                    if _should_ignore(rel_path):
                        continue
                    full_path = os.path.abspath(os.path.join(self.root_dir, rel_path))
                    if self._is_locked_fn is not None:
                        try:
                            if self._is_locked_fn(full_path):
                                continue
                        except Exception:
                            pass
                    batch.append((full_path, action))
                if batch:
                    self._enqueue(batch, overflowed=False)
        except Exception as exc:
            _LOG.debug("[ProjectFsWatcher] loop terminated (%s): %s", self.root_dir, exc)
        finally:
            self._ready_event.set()
            self._close_handles()

    def _enqueue(self, items: list[tuple[str, int]], *, overflowed: bool) -> None:
        with self._lock:
            if self._stopped:
                return
            for full_path, action in items:
                self._pending[full_path] = action
            if overflowed:
                self._overflowed = True
            if self._timer is not None:
                self._timer.cancel()
            timer = threading.Timer(self._debounce_sec, self._flush)
            timer.daemon = True
            self._timer = timer
            timer.start()

    def _flush(self) -> None:
        with self._lock:
            if self._stopped:
                return
            pending = list(self._pending.items())
            overflowed = self._overflowed
            self._pending.clear()
            self._overflowed = False
            self._timer = None
        if not pending and not overflowed:
            return
        try:
            self._on_batch(pending, overflowed)
        except Exception as exc:
            _LOG.debug("[ProjectFsWatcher] callback failed (%s): %s", self.root_dir, exc)

    def stop(self) -> None:
        with self._lock:
            self._stopped = True
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
            stop_event = self._stop_event
            dir_handle = self._dir_handle
            thread = self._thread
            self._thread = None

        if sys.platform == "win32":
            if _is_valid_handle(stop_event):
                try:
                    _kernel32.SetEvent(stop_event)
                except Exception:
                    pass
            if _is_valid_handle(dir_handle):
                try:
                    _kernel32.CancelIoEx(dir_handle, None)
                except Exception:
                    pass

        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=2.0)

        self._close_handles()
