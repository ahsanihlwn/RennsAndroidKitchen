# SPDX-License-Identifier: MPL-2.0
"""Recursive OS directory watcher for external / RAK Terminal / shell mutations.

- Windows: kernel `ReadDirectoryChangesW` with `FILE_FLAG_OVERLAPPED` + `CancelIoEx`.
- Linux:   kernel `inotify` via libc `ctypes` with self-pipe wakeup + dynamic subdir watches
           (falls back to polling if inotify watch limit is exhausted).
- macOS:   lightweight background directory snapshot-diff poller with instant `stop()` wakeup.

All platforms require zero external dependencies, honor the same ignore/lock filters and
debouncing semantics, and release OS handles synchronously on `stop()`.
"""
from __future__ import annotations

import logging
import os
import select
import struct
import sys
import threading

_LOG = logging.getLogger(__name__)

_SKIP_ROOT_DIRS = frozenset({"config", "neobranch", "meta-inf", ".git", ".rak_quarantine"})
_SKIP_LEAF_NAMES = frozenset({"agents.md", "claude.md", "codex.md", "gemini.md"})
_SKIP_LEAF_PREFIXES = (".rak_", ".rak-agent-", ".rak-extract-", ".rak-transfer-")

_BUFFER_SIZE = 65536
_POLL_INTERVAL_SEC = 0.35
_POLL_MAX_ENTRIES = 50_000


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


# Linux inotify bindings (libc, zero external dependencies)
_INOTIFY_LIBC = None
if sys.platform.startswith("linux"):
    import ctypes
    import ctypes.util

    _IN_MODIFY = 0x00000002
    _IN_ATTRIB = 0x00000004
    _IN_CLOSE_WRITE = 0x00000008
    _IN_MOVED_FROM = 0x00000040
    _IN_MOVED_TO = 0x00000080
    _IN_CREATE = 0x00000100
    _IN_DELETE = 0x00000200
    _IN_DELETE_SELF = 0x00000400
    _IN_MOVE_SELF = 0x00000800
    _IN_Q_OVERFLOW = 0x00004000
    _IN_IGNORED = 0x00008000
    _IN_ISDIR = 0x40000000
    _IN_NONBLOCK = 0x00000800
    _IN_CLOEXEC = 0x00080000
    _IN_WATCH_MASK = (
        _IN_CREATE
        | _IN_DELETE
        | _IN_MODIFY
        | _IN_CLOSE_WRITE
        | _IN_ATTRIB
        | _IN_MOVED_FROM
        | _IN_MOVED_TO
        | _IN_DELETE_SELF
        | _IN_MOVE_SELF
    )
    _IN_EVENT_HEADER = struct.Struct("iIII")

    try:
        _libc_name = ctypes.util.find_library("c") or "libc.so.6"
        _libc = ctypes.CDLL(_libc_name, use_errno=True)
        _libc.inotify_init1.argtypes = [ctypes.c_int]
        _libc.inotify_init1.restype = ctypes.c_int
        _libc.inotify_add_watch.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_uint32]
        _libc.inotify_add_watch.restype = ctypes.c_int
        _libc.inotify_rm_watch.argtypes = [ctypes.c_int, ctypes.c_int]
        _libc.inotify_rm_watch.restype = ctypes.c_int
        _INOTIFY_LIBC = _libc
    except Exception:
        _INOTIFY_LIBC = None


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

        # Windows handles
        self._dir_handle = None
        self._stop_event = None
        self._io_event = None

        # POSIX handles (Linux inotify + self-pipe / macOS poller event)
        self._inotify_fd: int = -1
        self._wake_r: int = -1
        self._wake_w: int = -1
        self._poll_stop = threading.Event()

        self._thread: threading.Thread | None = None
        self._ready_event = threading.Event()

    def start(self) -> bool:
        if not self.root_dir or not os.path.isdir(self.root_dir):
            return False
        if sys.platform == "win32":
            return self._start_windows()
        if sys.platform.startswith("linux") and _INOTIFY_LIBC is not None:
            if self._start_linux_inotify():
                return True
        return self._start_posix_poll()

    def _start_windows(self) -> bool:
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

    def _start_linux_inotify(self) -> bool:
        with self._lock:
            if self._started or self._stopped:
                return False
            fd = _INOTIFY_LIBC.inotify_init1(_IN_NONBLOCK | _IN_CLOEXEC)
            if fd < 0:
                return False
            try:
                wake_r, wake_w = os.pipe()
            except OSError:
                try:
                    os.close(fd)
                except OSError:
                    pass
                return False
            self._inotify_fd = fd
            self._wake_r = wake_r
            self._wake_w = wake_w
            self._started = True
            self._ready_event.clear()
            self._thread = threading.Thread(
                target=self._run_linux_inotify,
                name=f"rak-fswatch:{os.path.basename(self.root_dir) or 'root'}",
                daemon=True,
            )
            self._thread.start()
        self._ready_event.wait(timeout=2.0)
        return True

    def _start_posix_poll(self) -> bool:
        with self._lock:
            if self._started or self._stopped:
                return False
            self._poll_stop.clear()
            self._started = True
            self._ready_event.clear()
            self._thread = threading.Thread(
                target=self._run_posix_poll,
                name=f"rak-fswatch-poll:{os.path.basename(self.root_dir) or 'root'}",
                daemon=True,
            )
            self._thread.start()
        self._ready_event.wait(timeout=2.0)
        return True

    def _close_handles(self) -> None:
        with self._lock:
            handles = (self._dir_handle, self._stop_event, self._io_event)
            self._dir_handle = None
            self._stop_event = None
            self._io_event = None
            fds = (self._inotify_fd, self._wake_r, self._wake_w)
            self._inotify_fd = -1
            self._wake_r = -1
            self._wake_w = -1
        if sys.platform == "win32":
            for h in handles:
                if _is_valid_handle(h):
                    try:
                        _kernel32.CloseHandle(h)
                    except Exception:
                        pass
        else:
            for fd in fds:
                if fd >= 0:
                    try:
                        os.close(fd)
                    except OSError:
                        pass

    def _filter_path(self, full_path: str) -> bool:
        try:
            rel_path = os.path.relpath(full_path, self.root_dir)
        except ValueError:
            return False
        if rel_path == "." or rel_path.startswith(".."):
            return False
        if _should_ignore(rel_path):
            return False
        if self._is_locked_fn is not None:
            try:
                if self._is_locked_fn(full_path):
                    return False
            except Exception:
                pass
        return True

    def _run_linux_inotify(self) -> None:
        wd_to_dir: dict[int, str] = {}
        dir_to_wd: dict[str, int] = {}

        def _add_watch_tree(base_dir: str) -> bool:
            hit_limit = False
            stack = [base_dir]
            while stack:
                cur = stack.pop()
                if cur in dir_to_wd:
                    continue
                if cur != self.root_dir:
                    try:
                        rel = os.path.relpath(cur, self.root_dir)
                    except ValueError:
                        continue
                    if _should_ignore(rel):
                        continue
                wd = _INOTIFY_LIBC.inotify_add_watch(
                    self._inotify_fd, os.fsencode(cur), _IN_WATCH_MASK
                )
                if wd < 0:
                    hit_limit = True
                    continue
                wd_to_dir[wd] = cur
                dir_to_wd[cur] = wd
                try:
                    with os.scandir(cur) as it:
                        for entry in it:
                            try:
                                if entry.is_dir(follow_symlinks=False):
                                    stack.append(entry.path)
                            except OSError:
                                pass
                except OSError:
                    pass
            return not hit_limit

        try:
            _add_watch_tree(self.root_dir)
            self._ready_event.set()
            hdr_size = _IN_EVENT_HEADER.size
            while not self._stopped:
                fd = self._inotify_fd
                wake_r = self._wake_r
                if fd < 0 or wake_r < 0:
                    break
                try:
                    readable, _, _ = select.select([fd, wake_r], [], [])
                except (OSError, ValueError):
                    break
                if wake_r in readable or self._stopped:
                    break
                if fd not in readable:
                    continue
                try:
                    raw = os.read(fd, _BUFFER_SIZE)
                except BlockingIOError:
                    continue
                except OSError:
                    break
                if not raw:
                    continue

                batch: list[tuple[str, int]] = []
                overflowed = False
                offset = 0
                total = len(raw)
                while offset + hdr_size <= total:
                    wd, mask, _cookie, name_len = _IN_EVENT_HEADER.unpack_from(raw, offset)
                    offset += hdr_size
                    name_bytes = raw[offset : offset + name_len].rstrip(b"\x00")
                    offset += name_len

                    if mask & _IN_Q_OVERFLOW:
                        overflowed = True
                        continue
                    if mask & _IN_IGNORED:
                        old_dir = wd_to_dir.pop(wd, None)
                        if old_dir is not None:
                            dir_to_wd.pop(old_dir, None)
                        continue
                    parent_dir = wd_to_dir.get(wd)
                    if not parent_dir:
                        continue
                    if name_bytes:
                        name = os.fsdecode(name_bytes)
                        full_path = os.path.abspath(os.path.join(parent_dir, name))
                    else:
                        full_path = parent_dir

                    if (mask & _IN_ISDIR) and (mask & (_IN_CREATE | _IN_MOVED_TO)) and os.path.isdir(full_path):
                        _add_watch_tree(full_path)

                    if not self._filter_path(full_path):
                        continue
                    if mask & (_IN_CREATE | _IN_MOVED_TO):
                        action = 1
                    elif mask & (_IN_DELETE | _IN_MOVED_FROM):
                        action = 2
                    else:
                        action = 3
                    batch.append((full_path, action))

                if batch or overflowed:
                    self._enqueue(batch, overflowed=overflowed)
        except Exception as exc:
            _LOG.debug("[ProjectFsWatcher] inotify loop terminated (%s): %s", self.root_dir, exc)
        finally:
            self._ready_event.set()
            self._close_handles()

    def _snapshot_tree(self) -> tuple[dict[str, tuple[int, int, bool]], bool]:
        snap: dict[str, tuple[int, int, bool]] = {}
        overflowed = False
        stack = [self.root_dir]
        while stack:
            cur = stack.pop()
            try:
                with os.scandir(cur) as it:
                    for entry in it:
                        path = entry.path
                        try:
                            rel = os.path.relpath(path, self.root_dir)
                        except ValueError:
                            continue
                        if _should_ignore(rel):
                            continue
                        try:
                            st = entry.stat(follow_symlinks=False)
                            is_dir = entry.is_dir(follow_symlinks=False)
                        except OSError:
                            continue
                        snap[path] = (st.st_mtime_ns, st.st_size, is_dir)
                        if len(snap) >= _POLL_MAX_ENTRIES:
                            overflowed = True
                            return snap, overflowed
                        if is_dir:
                            stack.append(path)
            except OSError:
                continue
        return snap, overflowed

    def _run_posix_poll(self) -> None:
        try:
            prev, _ = self._snapshot_tree()
            self._ready_event.set()
            while not self._stopped and not self._poll_stop.wait(_POLL_INTERVAL_SEC):
                if self._stopped:
                    break
                cur, overflowed = self._snapshot_tree()
                batch: list[tuple[str, int]] = []
                for path, meta in cur.items():
                    old = prev.get(path)
                    if old is None:
                        if self._filter_path(path):
                            batch.append((path, 1))
                    elif old != meta:
                        if self._filter_path(path):
                            batch.append((path, 3))
                for path in prev:
                    if path not in cur and self._filter_path(path):
                        batch.append((path, 2))
                prev = cur
                if batch or overflowed:
                    self._enqueue(batch, overflowed=overflowed)
        except Exception as exc:
            _LOG.debug("[ProjectFsWatcher] poll loop terminated (%s): %s", self.root_dir, exc)
        finally:
            self._ready_event.set()
            self._close_handles()

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
            wake_w = self._wake_w
            thread = self._thread
            self._thread = None

        self._poll_stop.set()

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
        elif wake_w >= 0:
            try:
                os.write(wake_w, b"x")
            except OSError:
                pass

        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=2.0)

        self._close_handles()
