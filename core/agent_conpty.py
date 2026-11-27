# SPDX-License-Identifier: MPL-2.0
"""Dedicated background ConPTY host for agent CLIs and metadata probes.

On Windows, attaching agent processes to a single headless ConPTY session
via PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE gives the CLI and its ordinary child
processes a real hidden console without ever creating or flashing a desktop
console window (avoiding CREATE_NEW_CONSOLE + SW_HIDE window flicker).
Standard I/O remains on isolated OS pipes via STARTF_USESTDHANDLES and
PROC_THREAD_ATTRIBUTE_HANDLE_LIST so JSON line protocols never pass through VT.
"""
from __future__ import annotations

import atexit
import ctypes
import io
import logging
import os
import subprocess
import threading
import time

if os.name == 'nt':
    from ctypes import wintypes as wt
    import msvcrt
else:
    wt = None

_log = logging.getLogger(__name__)

_STARTF_USESHOWWINDOW = 0x00000001
_STARTF_USESTDHANDLES = 0x00000100
_SW_HIDE = 0
_EXTENDED_STARTUPINFO_PRESENT = 0x00080000
_CREATE_UNICODE_ENVIRONMENT = 0x00000400
_PROC_THREAD_ATTRIBUTE_HANDLE_LIST = 0x00020002
_PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE = 0x00020016
_HANDLE_FLAG_INHERIT = 0x00000001
_DUPLICATE_SAME_ACCESS = 0x00000002
_STILL_ACTIVE = 259
_WAIT_OBJECT_0 = 0x00000000
_WAIT_TIMEOUT = 0x00000102
_INFINITE = 0xFFFFFFFF
_GENERIC_READ = 0x80000000
_GENERIC_WRITE = 0x40000000
_FILE_SHARE_READ = 0x00000001
_FILE_SHARE_WRITE = 0x00000002
_OPEN_EXISTING = 3
_STD_INPUT_HANDLE = -10
_STD_OUTPUT_HANDLE = -11
_STD_ERROR_HANDLE = -12
_INVALID_HANDLE = ctypes.c_void_p(-1).value


if os.name == 'nt':
    class _COORD(ctypes.Structure):
        _fields_ = [('X', ctypes.c_short), ('Y', ctypes.c_short)]

    class _SECURITY_ATTRIBUTES(ctypes.Structure):
        _fields_ = [
            ('nLength', wt.DWORD),
            ('lpSecurityDescriptor', ctypes.c_void_p),
            ('bInheritHandle', wt.BOOL),
        ]

    class _STARTUPINFOW(ctypes.Structure):
        _fields_ = [
            ('cb', wt.DWORD),
            ('lpReserved', wt.LPWSTR),
            ('lpDesktop', wt.LPWSTR),
            ('lpTitle', wt.LPWSTR),
            ('dwX', wt.DWORD),
            ('dwY', wt.DWORD),
            ('dwXSize', wt.DWORD),
            ('dwYSize', wt.DWORD),
            ('dwXCountChars', wt.DWORD),
            ('dwYCountChars', wt.DWORD),
            ('dwFillAttribute', wt.DWORD),
            ('dwFlags', wt.DWORD),
            ('wShowWindow', wt.WORD),
            ('cbReserved2', wt.WORD),
            ('lpReserved2', ctypes.POINTER(ctypes.c_byte)),
            ('hStdInput', wt.HANDLE),
            ('hStdOutput', wt.HANDLE),
            ('hStdError', wt.HANDLE),
        ]

    class _STARTUPINFOEXW(ctypes.Structure):
        _fields_ = [
            ('StartupInfo', _STARTUPINFOW),
            ('lpAttributeList', ctypes.c_void_p),
        ]

    class _PROCESS_INFORMATION(ctypes.Structure):
        _fields_ = [
            ('hProcess', wt.HANDLE),
            ('hThread', wt.HANDLE),
            ('dwProcessId', wt.DWORD),
            ('dwThreadId', wt.DWORD),
        ]

    _k32 = ctypes.WinDLL('kernel32', use_last_error=True)
    _k32.GetCurrentProcess.restype = wt.HANDLE
    _k32.DuplicateHandle.argtypes = [
        wt.HANDLE, wt.HANDLE, wt.HANDLE, ctypes.POINTER(wt.HANDLE),
        wt.DWORD, wt.BOOL, wt.DWORD,
    ]
    _k32.DuplicateHandle.restype = wt.BOOL
    _k32.CreatePipe.argtypes = [
        ctypes.POINTER(wt.HANDLE), ctypes.POINTER(wt.HANDLE),
        ctypes.c_void_p, wt.DWORD,
    ]
    _k32.CreatePipe.restype = wt.BOOL
    _k32.SetHandleInformation.argtypes = [wt.HANDLE, wt.DWORD, wt.DWORD]
    _k32.SetHandleInformation.restype = wt.BOOL
    _k32.CreateFileW.argtypes = [
        wt.LPWSTR, wt.DWORD, wt.DWORD, ctypes.c_void_p,
        wt.DWORD, wt.DWORD, wt.HANDLE,
    ]
    _k32.CreateFileW.restype = wt.HANDLE
    _k32.CloseHandle.argtypes = [wt.HANDLE]
    _k32.CloseHandle.restype = wt.BOOL
    _k32.GetStdHandle.argtypes = [wt.DWORD]
    _k32.GetStdHandle.restype = wt.HANDLE
    _k32.WaitForSingleObject.argtypes = [wt.HANDLE, wt.DWORD]
    _k32.WaitForSingleObject.restype = wt.DWORD
    _k32.GetExitCodeProcess.argtypes = [wt.HANDLE, ctypes.POINTER(wt.DWORD)]
    _k32.GetExitCodeProcess.restype = wt.BOOL
    _k32.TerminateProcess.argtypes = [wt.HANDLE, wt.UINT]
    _k32.TerminateProcess.restype = wt.BOOL
    if hasattr(_k32, 'CreatePseudoConsole'):
        _k32.CreatePseudoConsole.argtypes = [
            _COORD, wt.HANDLE, wt.HANDLE, wt.DWORD, ctypes.POINTER(ctypes.c_void_p),
        ]
        _k32.CreatePseudoConsole.restype = ctypes.c_long
        _k32.ClosePseudoConsole.argtypes = [ctypes.c_void_p]
        _k32.ClosePseudoConsole.restype = None
    _k32.InitializeProcThreadAttributeList.argtypes = [
        ctypes.c_void_p, wt.DWORD, wt.DWORD, ctypes.POINTER(ctypes.c_size_t),
    ]
    _k32.InitializeProcThreadAttributeList.restype = wt.BOOL
    _k32.UpdateProcThreadAttribute.argtypes = [
        ctypes.c_void_p, wt.DWORD, ctypes.c_size_t, ctypes.c_void_p,
        ctypes.c_size_t, ctypes.c_void_p, ctypes.c_void_p,
    ]
    _k32.UpdateProcThreadAttribute.restype = wt.BOOL
    _k32.DeleteProcThreadAttributeList.argtypes = [ctypes.c_void_p]
    _k32.DeleteProcThreadAttributeList.restype = None
    _k32.CreateProcessW.argtypes = [
        wt.LPWSTR, wt.LPWSTR, ctypes.c_void_p, ctypes.c_void_p, wt.BOOL,
        wt.DWORD, ctypes.c_void_p, wt.LPWSTR, ctypes.c_void_p,
        ctypes.POINTER(_PROCESS_INFORMATION),
    ]
    _k32.CreateProcessW.restype = wt.BOOL
else:
    _k32 = None


def is_supported():
    return (
        os.name == 'nt'
        and _k32 is not None
        and hasattr(_k32, 'CreatePseudoConsole')
        and hasattr(_k32, 'InitializeProcThreadAttributeList')
    )


def _valid_handle(handle):
    return bool(handle) and handle != _INVALID_HANDLE


def _safe_close(handle):
    if _valid_handle(handle):
        try:
            _k32.CloseHandle(handle)
        except Exception:
            pass


def _duplicate_inheritable(handle):
    if not _valid_handle(handle):
        return None
    curr = _k32.GetCurrentProcess()
    target = wt.HANDLE()
    if not _k32.DuplicateHandle(curr, handle, curr, ctypes.byref(target), 0, True, _DUPLICATE_SAME_ACCESS):
        raise ctypes.WinError(ctypes.get_last_error())
    return target.value


def _open_devnull(write):
    sa = _SECURITY_ATTRIBUTES(ctypes.sizeof(_SECURITY_ATTRIBUTES), None, True)
    access = _GENERIC_WRITE if write else _GENERIC_READ
    handle = _k32.CreateFileW(
        os.devnull,
        access,
        _FILE_SHARE_READ | _FILE_SHARE_WRITE,
        ctypes.byref(sa),
        _OPEN_EXISTING,
        0,
        None,
    )
    if not _valid_handle(handle):
        raise ctypes.WinError(ctypes.get_last_error())
    return handle


def _make_pipe(for_write_child):
    sa = _SECURITY_ATTRIBUTES(ctypes.sizeof(_SECURITY_ATTRIBUTES), None, True)
    read_h, write_h = wt.HANDLE(), wt.HANDLE()
    if not _k32.CreatePipe(ctypes.byref(read_h), ctypes.byref(write_h), ctypes.byref(sa), 0):
        raise ctypes.WinError(ctypes.get_last_error())
    parent_h = read_h.value if for_write_child else write_h.value
    child_h = write_h.value if for_write_child else read_h.value
    if not _k32.SetHandleInformation(parent_h, _HANDLE_FLAG_INHERIT, 0):
        err = ctypes.get_last_error()
        _safe_close(read_h.value)
        _safe_close(write_h.value)
        raise ctypes.WinError(err)
    return parent_h, child_h


def _wrap_pipe(handle, *, write, text, encoding, errors, bufsize):
    flags = 0 if write else os.O_RDONLY
    fd = msvcrt.open_osfhandle(handle, flags)
    mode = 'wb' if write else 'rb'
    raw = os.fdopen(fd, mode, buffering=(0 if bufsize == 0 else -1))
    if not text:
        return raw
    return io.TextIOWrapper(
        raw,
        encoding=encoding or 'utf-8',
        errors=errors or 'replace',
        line_buffering=(bufsize == 1),
    )


def _encode_env(env):
    if env is None:
        return None
    entries = []
    for raw_k, raw_v in env.items():
        k = os.fsdecode(raw_k)
        v = os.fsdecode(raw_v)
        if '\0' in k or '\0' in v or '=' in k[1:]:
            raise ValueError('illegal environment variable name or value')
        entries.append(f'{k}={v}')
    return ctypes.create_unicode_buffer('\0'.join(entries) + '\0\0')


class ConptyProcess:
    """subprocess.Popen-compatible handle for a process attached to AgentTerminalService."""

    console_mode = 'conpty'

    def __init__(self, args, hprocess, pid, *, stdin=None, stdout=None, stderr=None):
        self.args = args
        self._hprocess = hprocess
        self.pid = pid
        self.stdin = stdin
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = None
        self._lock = threading.Lock()

    def poll(self):
        if self.returncode is not None:
            return self.returncode
        with self._lock:
            if self.returncode is not None or not _valid_handle(self._hprocess):
                return self.returncode
            code = wt.DWORD()
            if not _k32.GetExitCodeProcess(self._hprocess, ctypes.byref(code)):
                return None
            if code.value != _STILL_ACTIVE:
                self.returncode = code.value
            elif _k32.WaitForSingleObject(self._hprocess, 0) == _WAIT_OBJECT_0:
                if _k32.GetExitCodeProcess(self._hprocess, ctypes.byref(code)):
                    self.returncode = code.value
            return self.returncode

    def wait(self, timeout=None):
        if self.returncode is not None:
            return self.returncode
        deadline = None if timeout is None else time.monotonic() + max(0.0, float(timeout))
        while True:
            code = self.poll()
            if code is not None:
                return code
            if deadline is None:
                ms = _INFINITE
            else:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise subprocess.TimeoutExpired(self.args, timeout)
                ms = max(1, int(remaining * 1000))
            res = _k32.WaitForSingleObject(self._hprocess, ms)
            if res == _WAIT_TIMEOUT:
                raise subprocess.TimeoutExpired(self.args, timeout)
            if res == _WAIT_OBJECT_0:
                code = self.poll()
                if code is not None:
                    return code

    def terminate(self):
        if self.poll() is not None:
            return
        with self._lock:
            if self.returncode is None and _valid_handle(self._hprocess):
                if not _k32.TerminateProcess(self._hprocess, 1):
                    err = ctypes.get_last_error()
                    if self.poll() is None and err != 5:
                        raise ctypes.WinError(err)

    kill = terminate

    def __enter__(self):
        return self

    def __exit__(self, exc_type, value, traceback):
        try:
            if self.stdout:
                self.stdout.close()
            if self.stderr:
                self.stderr.close()
            if self.stdin:
                self.stdin.close()
        finally:
            self.wait()

    def __del__(self):
        h = getattr(self, '_hprocess', None)
        self._hprocess = None
        if _k32 is not None:
            _safe_close(h)


class AgentTerminalService:
    """Shared headless ConPTY session dedicated exclusively to agent processes."""

    def __init__(self, cols=120, rows=30):
        self._cols = cols
        self._rows = rows
        self._lock = threading.Lock()
        self._hpc = None
        self._pty_in_w = None
        self._drain_thread = None
        self._drainer_exited = False
        self._closed = False

    def is_active(self):
        with self._lock:
            return self._hpc is not None and not self._drainer_exited and not self._closed

    def _start_locked(self):
        pty_in_r, pty_in_w = wt.HANDLE(), wt.HANDLE()
        pty_out_r, pty_out_w = wt.HANDLE(), wt.HANDLE()
        if not _k32.CreatePipe(ctypes.byref(pty_in_r), ctypes.byref(pty_in_w), None, 0):
            raise ctypes.WinError(ctypes.get_last_error())
        if not _k32.CreatePipe(ctypes.byref(pty_out_r), ctypes.byref(pty_out_w), None, 0):
            err = ctypes.get_last_error()
            _safe_close(pty_in_r.value)
            _safe_close(pty_in_w.value)
            raise ctypes.WinError(err)

        hpc = ctypes.c_void_p()
        hr = _k32.CreatePseudoConsole(_COORD(self._cols, self._rows), pty_in_r.value, pty_out_w.value, 0, ctypes.byref(hpc))
        _safe_close(pty_in_r.value)
        _safe_close(pty_out_w.value)
        if hr != 0 or not hpc.value:
            _safe_close(pty_in_w.value)
            _safe_close(pty_out_r.value)
            raise OSError(f'CreatePseudoConsole failed with HRESULT 0x{hr & 0xFFFFFFFF:08X}')

        self._hpc = hpc.value
        self._pty_in_w = pty_in_w.value
        self._drainer_exited = False
        fd = msvcrt.open_osfhandle(pty_out_r.value, os.O_RDONLY)

        def _drain():
            try:
                while True:
                    chunk = os.read(fd, 4096)
                    if not chunk:
                        break
            except OSError:
                pass
            finally:
                self._drainer_exited = True
                try:
                    os.close(fd)
                except OSError:
                    pass

        self._drain_thread = threading.Thread(target=_drain, daemon=True, name='RAK-Agent-ConPTY')
        self._drain_thread.start()

    def _close_locked(self):
        hpc = self._hpc
        pty_in_w = self._pty_in_w
        self._hpc = None
        self._pty_in_w = None
        if hpc:
            try:
                _k32.ClosePseudoConsole(hpc)
            except Exception:
                pass
        _safe_close(pty_in_w)

    def ensure_started(self):
        with self._lock:
            if self._closed:
                self._closed = False
            if self._hpc is None or self._drainer_exited:
                if self._hpc is not None:
                    self._close_locked()
                self._start_locked()
            return self._hpc

    def shutdown(self):
        with self._lock:
            self._closed = True
            thread = self._drain_thread
            self._drain_thread = None
            self._close_locked()
        if thread is not None and thread.is_alive():
            thread.join(0.25)

    def spawn(
        self,
        command,
        *,
        cwd=None,
        env=None,
        stdin=None,
        stdout=None,
        stderr=None,
        text=False,
        encoding=None,
        errors=None,
        bufsize=-1,
    ):
        argv = [os.fspath(part) for part in command] if not isinstance(command, (str, bytes)) else [os.fspath(command)]
        cmdline = command if isinstance(command, str) else subprocess.list2cmdline(argv)
        cwd_str = os.fspath(cwd) if cwd is not None else None
        env_buf = _encode_env(env)

        hpc = self.ensure_started()
        try:
            return self._spawn_with_hpc(
                hpc, argv, cmdline, cwd_str, env_buf,
                stdin=stdin, stdout=stdout, stderr=stderr,
                text=text, encoding=encoding, errors=errors, bufsize=bufsize,
            )
        except OSError:
            if not self._drainer_exited:
                raise
            with self._lock:
                self._close_locked()
                self._start_locked()
                hpc = self._hpc
            return self._spawn_with_hpc(
                hpc, argv, cmdline, cwd_str, env_buf,
                stdin=stdin, stdout=stdout, stderr=stderr,
                text=text, encoding=encoding, errors=errors, bufsize=bufsize,
            )

    def _resolve_std_handle(self, target, *, is_input, stdout_child=None):
        if target == subprocess.PIPE:
            parent_h, child_h = _make_pipe(not is_input)
            return parent_h, child_h, True
        if target == subprocess.DEVNULL:
            return None, _open_devnull(not is_input), True
        if target == subprocess.STDOUT and not is_input:
            if stdout_child is None:
                raise ValueError('STDOUT specified for stderr without stdout')
            return None, stdout_child, False
        if target is None:
            std_id = _STD_INPUT_HANDLE if is_input else (_STD_OUTPUT_HANDLE if stdout_child is None else _STD_ERROR_HANDLE)
            std_h = _k32.GetStdHandle(std_id)
            if _valid_handle(std_h):
                try:
                    return None, _duplicate_inheritable(std_h), True
                except OSError:
                    pass
            return None, _open_devnull(not is_input), True
        if isinstance(target, int):
            osf = msvcrt.get_osfhandle(target)
            return None, _duplicate_inheritable(osf), True
        osf = msvcrt.get_osfhandle(target.fileno())
        return None, _duplicate_inheritable(osf), True

    def _spawn_with_hpc(
        self,
        hpc,
        argv,
        cmdline,
        cwd_str,
        env_buf,
        *,
        stdin,
        stdout,
        stderr,
        text,
        encoding,
        errors,
        bufsize,
    ):
        owned_child_handles = []
        parent_in = parent_out = parent_err = None
        attr_buf = None
        attr_initialized = False
        try:
            parent_in, h_in, own_in = self._resolve_std_handle(stdin, is_input=True)
            if own_in:
                owned_child_handles.append(h_in)
            parent_out, h_out, own_out = self._resolve_std_handle(stdout, is_input=False)
            if own_out:
                owned_child_handles.append(h_out)
            parent_err, h_err, own_err = self._resolve_std_handle(stderr, is_input=False, stdout_child=h_out)
            if own_err:
                owned_child_handles.append(h_err)

            unique_handles = list(dict.fromkeys(h for h in (h_in, h_out, h_err) if _valid_handle(h)))
            handle_arr = (wt.HANDLE * len(unique_handles))(*unique_handles) if unique_handles else None

            attr_count = 2 if handle_arr is not None else 1
            size = ctypes.c_size_t(0)
            _k32.InitializeProcThreadAttributeList(None, attr_count, 0, ctypes.byref(size))
            attr_buf = ctypes.create_string_buffer(size.value)
            if not _k32.InitializeProcThreadAttributeList(attr_buf, attr_count, 0, ctypes.byref(size)):
                raise ctypes.WinError(ctypes.get_last_error())
            attr_initialized = True

            hpc_ptr = ctypes.c_void_p(hpc)
            if not _k32.UpdateProcThreadAttribute(
                attr_buf, 0, _PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE,
                hpc_ptr, ctypes.sizeof(ctypes.c_void_p), None, None,
            ):
                raise ctypes.WinError(ctypes.get_last_error())

            if handle_arr is not None:
                if not _k32.UpdateProcThreadAttribute(
                    attr_buf, 0, _PROC_THREAD_ATTRIBUTE_HANDLE_LIST,
                    handle_arr, ctypes.sizeof(handle_arr), None, None,
                ):
                    raise ctypes.WinError(ctypes.get_last_error())

            si = _STARTUPINFOEXW()
            si.StartupInfo.cb = ctypes.sizeof(_STARTUPINFOEXW)
            si.StartupInfo.dwFlags = _STARTF_USESTDHANDLES | _STARTF_USESHOWWINDOW
            si.StartupInfo.wShowWindow = _SW_HIDE
            si.StartupInfo.hStdInput = h_in
            si.StartupInfo.hStdOutput = h_out
            si.StartupInfo.hStdError = h_err
            si.lpAttributeList = ctypes.addressof(attr_buf)

            flags = _EXTENDED_STARTUPINFO_PRESENT
            if env_buf is not None:
                flags |= _CREATE_UNICODE_ENVIRONMENT

            pi = _PROCESS_INFORMATION()
            ok = _k32.CreateProcessW(
                None,
                cmdline,
                None,
                None,
                True,
                flags,
                env_buf,
                cwd_str,
                ctypes.byref(si),
                ctypes.byref(pi),
            )
            if not ok:
                raise ctypes.WinError(ctypes.get_last_error())
            _safe_close(pi.hThread)
        except BaseException:
            _safe_close(parent_in)
            _safe_close(parent_out)
            _safe_close(parent_err)
            raise
        finally:
            if attr_initialized and attr_buf is not None:
                _k32.DeleteProcThreadAttributeList(attr_buf)
            for h in owned_child_handles:
                _safe_close(h)

        in_stream = _wrap_pipe(parent_in, write=True, text=text, encoding=encoding, errors=errors, bufsize=bufsize) if parent_in else None
        out_stream = _wrap_pipe(parent_out, write=False, text=text, encoding=encoding, errors=errors, bufsize=bufsize) if parent_out else None
        err_stream = _wrap_pipe(parent_err, write=False, text=text, encoding=encoding, errors=errors, bufsize=bufsize) if parent_err else None
        return ConptyProcess(argv, pi.hProcess, pi.dwProcessId, stdin=in_stream, stdout=out_stream, stderr=err_stream)


agent_terminal_service = AgentTerminalService()
if os.name == 'nt':
    atexit.register(agent_terminal_service.shutdown)
