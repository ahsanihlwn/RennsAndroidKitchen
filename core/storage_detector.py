# SPDX-License-Identifier: MPL-2.0
import os
import sys

_DRIVE_CACHE = {}


def _existing_ancestor(path: str) -> str:
    cur = os.path.abspath(path)
    while cur and not os.path.exists(cur):
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent
    return cur or "."


def is_rotational_drive(path: str) -> bool:

    if not path:
        return False

    try:
        abs_path = os.path.abspath(path)
        if sys.platform == "win32":
            cache_key = os.path.splitdrive(abs_path)[0].upper()
            if cache_key in _DRIVE_CACHE:
                return _DRIVE_CACHE[cache_key]
            res = _detect_windows_rotational(cache_key)
            _DRIVE_CACHE[cache_key] = res
            return res

        probe = _existing_ancestor(abs_path)
        st = os.stat(probe)
        cache_key = f"dev:{st.st_dev}"
        if cache_key in _DRIVE_CACHE:
            return _DRIVE_CACHE[cache_key]

        if sys.platform.startswith("linux"):
            res = _detect_linux_rotational(probe)
        elif sys.platform == "darwin":
            res = _detect_macos_rotational(probe)
        else:
            res = False
        _DRIVE_CACHE[cache_key] = res
        return res
    except Exception:
        pass

    return False


def _detect_windows_rotational(drive: str) -> bool:

    if not drive or len(drive) < 2 or drive[1] != ":":
        return False

    import ctypes
    from ctypes import wintypes

    drive_letter = drive[0].upper()
    device_path = f"\\\\.\\{drive_letter}:"

    IOCTL_STORAGE_QUERY_PROPERTY = 0x002D1400
    StorageDeviceSeekPenaltyProperty = 7
    PropertyStandardQuery = 0

    class STORAGE_PROPERTY_QUERY(ctypes.Structure):
        _fields_ = [
            ("PropertyId", ctypes.c_int),
            ("QueryType", ctypes.c_int),
            ("AdditionalParameters", ctypes.c_byte * 1),
        ]

    class DEVICE_SEEK_PENALTY_DESCRIPTOR(ctypes.Structure):
        _fields_ = [
            ("Version", wintypes.DWORD),
            ("Size", wintypes.DWORD),
            ("IncursSeekPenalty", ctypes.c_byte),
        ]

    # Open volume with 0 (no read/write access required, query only)
    h = ctypes.windll.kernel32.CreateFileW(
        device_path,
        0,
        3,  # FILE_SHARE_READ | FILE_SHARE_WRITE
        None,
        3,  # OPEN_EXISTING
        0,
        None,
    )
    if h == -1 or h == 0xFFFFFFFFFFFFFFFF:
        return False

    try:
        query = STORAGE_PROPERTY_QUERY(
            StorageDeviceSeekPenaltyProperty, PropertyStandardQuery
        )
        desc = DEVICE_SEEK_PENALTY_DESCRIPTOR()
        bytes_returned = wintypes.DWORD()
        success = ctypes.windll.kernel32.DeviceIoControl(
            h,
            IOCTL_STORAGE_QUERY_PROPERTY,
            ctypes.byref(query),
            ctypes.sizeof(query),
            ctypes.byref(desc),
            ctypes.sizeof(desc),
            ctypes.byref(bytes_returned),
            None,
        )
        if success:
            return bool(desc.IncursSeekPenalty)
    finally:
        ctypes.windll.kernel32.CloseHandle(h)

    return False


def _detect_linux_rotational(path: str) -> bool:

    try:
        st = os.stat(path)
        major = os.major(st.st_dev)
        minor = os.minor(st.st_dev)
        for rot_file in (
            f"/sys/dev/block/{major}:{minor}/queue/rotational",
            f"/sys/dev/block/{major}:{minor}/../queue/rotational",
        ):
            if os.path.isfile(rot_file):
                with open(rot_file, "r", encoding="utf-8") as f:
                    return f.read().strip() == "1"
    except Exception:
        pass
    return False


def _detect_macos_rotational(path: str) -> bool:
    import subprocess
    try:
        df = subprocess.run(
            ["df", "-P", path],
            capture_output=True, text=True, timeout=3, check=False,
        )
        lines = [ln.strip() for ln in (df.stdout or "").splitlines() if ln.strip()]
        if len(lines) >= 2:
            dev = lines[-1].split()[0]
            if dev.startswith("/dev/"):
                info = subprocess.run(
                    ["diskutil", "info", dev],
                    capture_output=True, text=True, timeout=3, check=False,
                )
                for line in (info.stdout or "").splitlines():
                    low = line.strip().lower()
                    if low.startswith("solid state:"):
                        return "no" in low.split(":", 1)[1]
    except Exception:
        pass
    return False


def get_optimal_io_workers(path: str, max_cap: int = 64, hdd_cap: int = 4) -> int:

    cpu = max(1, os.cpu_count() or 1)

    if is_rotational_drive(path):
        # HDD: 2-4 threads preserves sector locality and maximizes throughput
        return max(1, min(hdd_cap, cpu))
    else:
        # SSD / NVMe: scale to user's CPU core count
        return max(1, min(max_cap, cpu))

