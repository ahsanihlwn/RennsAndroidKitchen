# SPDX-License-Identifier: MPL-2.0
import ctypes
import shutil
import sys


def list_windows_disk_usage() -> list[dict]:
    if sys.platform != "win32":
        return []
    mask = ctypes.windll.kernel32.GetLogicalDrives()
    if not mask:
        raise OSError("Could not enumerate Windows volumes")
    result = []
    for index in range(26):
        if not mask & (1 << index):
            continue
        key = f"{chr(65 + index)}:"
        try:
            usage = shutil.disk_usage(key + "\\")
        except OSError:
            continue
        if usage.total > 0:
            result.append({"key": key, "total": usage.total, "used": usage.used})
    return result
