# SPDX-License-Identifier: MPL-2.0
import ctypes
import shutil
import sys
import threading
from concurrent.futures import Future, wait


_VOLUME_QUERIES = {}
_VOLUME_LOCK = threading.Lock()


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


def list_windows_disk_usage(timeout: float = 1.0) -> list[dict]:
    queries = [(key, _volume_query(key)) for key in _windows_drive_keys()]
    if not queries:
        return []
    ready, _ = wait([query for _, query in queries], timeout=max(0.0, timeout))
    result = []
    for key, query in queries:
        if query not in ready:
            continue
        usage = query.result()
        if usage is not None and usage.total > 0:
            result.append({"key": key, "total": usage.total, "used": usage.used})
    return result
