# SPDX-License-Identifier: MPL-2.0
import ctypes
from ctypes import wintypes
import shutil
import sys
import threading
from concurrent.futures import Future, wait


_VOLUME_QUERIES = {}
_VOLUME_LOCK = threading.Lock()
_SEEK_PENALTY_CACHE = {}

_DRIVE_FIXED = 3
_FILE_SHARE_READ_WRITE = 0x00000001 | 0x00000002
_OPEN_EXISTING = 3
_IOCTL_STORAGE_QUERY_PROPERTY = 0x002D1400
_STORAGE_DEVICE_SEEK_PENALTY_PROPERTY = 7
_PROPERTY_STANDARD_QUERY = 0


class _StoragePropertyQuery(ctypes.Structure):
    _fields_ = [
        ("PropertyId", wintypes.DWORD),
        ("QueryType", wintypes.DWORD),
        ("AdditionalParameters", ctypes.c_byte * 1),
    ]


class _DeviceSeekPenaltyDescriptor(ctypes.Structure):
    _fields_ = [
        ("Version", wintypes.DWORD),
        ("Size", wintypes.DWORD),
        ("IncursSeekPenalty", ctypes.c_ubyte),
    ]


def drive_incurs_seek_penalty(key: str) -> bool | None:
    """Return True for HDD/non-fixed drives, False for fixed SSDs, None if unknown."""
    if sys.platform != "win32":
        return None
    norm = str(key).rstrip("\\/").upper()
    if not norm:
        return None
    with _VOLUME_LOCK:
        if norm in _SEEK_PENALTY_CACHE:
            return _SEEK_PENALTY_CACHE[norm]
    result = None
    try:
        kernel32 = ctypes.windll.kernel32
        if kernel32.GetDriveTypeW(norm + "\\") != _DRIVE_FIXED:
            result = True
        else:
            kernel32.CreateFileW.restype = wintypes.HANDLE
            invalid = wintypes.HANDLE(-1).value
            handle = kernel32.CreateFileW(
                f"\\\\.\\{norm}",
                0,
                _FILE_SHARE_READ_WRITE,
                None,
                _OPEN_EXISTING,
                0,
                None,
            )
            if handle != invalid:
                try:
                    query = _StoragePropertyQuery(_STORAGE_DEVICE_SEEK_PENALTY_PROPERTY, _PROPERTY_STANDARD_QUERY)
                    descriptor = _DeviceSeekPenaltyDescriptor()
                    returned = wintypes.DWORD(0)
                    ok = kernel32.DeviceIoControl(
                        handle,
                        _IOCTL_STORAGE_QUERY_PROPERTY,
                        ctypes.byref(query),
                        ctypes.sizeof(query),
                        ctypes.byref(descriptor),
                        ctypes.sizeof(descriptor),
                        ctypes.byref(returned),
                        None,
                    )
                    if ok:
                        result = bool(descriptor.IncursSeekPenalty)
                finally:
                    kernel32.CloseHandle(handle)
    except OSError:
        result = None
    with _VOLUME_LOCK:
        _SEEK_PENALTY_CACHE[norm] = result
    return result


def _windows_drive_keys():
    if sys.platform != "win32":
        return []
    mask = ctypes.windll.kernel32.GetLogicalDrives()
    if not mask:
        raise OSError("Could not enumerate Windows volumes")
    return [f"{chr(65 + index)}:" for index in range(26) if mask & (1 << index)]


def _volume_query(key):
    with _VOLUME_LOCK:
        query = _VOLUME_QUERIES.get(key)
        if query is not None and not query.done():
            return query
        query = Future()
        _VOLUME_QUERIES[key] = query

        def read():
            try:
                query.set_result(shutil.disk_usage(key + "\\"))
            except OSError:
                query.set_result(None)
            except Exception as error:
                query.set_exception(error)

        # A Windows volume query can hang inside the OS. Keep at most one
        # pending query per drive, and never wait for it during process exit.
        threading.Thread(target=read, name="rak-volume-" + key, daemon=True).start()
        return query


def list_windows_disk_usage(
    timeout: float = 1.0,
    *,
    include_rotational: bool = True,
    cached_rows: list[dict] | None = None,
) -> list[dict]:
    keys = _windows_drive_keys()
    if not keys:
        return []
    cached_by_key = {
        row["key"]: row
        for row in (cached_rows or ())
        if isinstance(row, dict) and row.get("key")
    }
    queries = []
    skipped = {}
    for key in keys:
        if not include_rotational and drive_incurs_seek_penalty(key) is not False:
            if key in cached_by_key:
                skipped[key] = cached_by_key[key]
            continue
        queries.append((key, _volume_query(key)))
    ready = set()
    if queries:
        ready, _ = wait([query for _, query in queries], timeout=max(0.0, timeout))
    query_map = dict(queries)
    result = []
    for key in keys:
        if key in skipped:
            result.append(skipped[key])
            continue
        query = query_map.get(key)
        if query is None or query not in ready:
            continue
        usage = query.result()
        if usage is not None and usage.total > 0:
            result.append({"key": key, "total": usage.total, "used": usage.used})
    return result
