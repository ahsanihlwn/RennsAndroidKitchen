# SPDX-License-Identifier: MPL-2.0
import os
import sys

_DRIVE_CACHE = {}


def is_rotational_drive(path: str) -> bool:

    if not path:
        return False

    try:
        abs_path = os.path.abspath(path)
        drive = os.path.splitdrive(abs_path)[0].upper()

        # Cache check (e.g. "C:" or "D:")
        if drive in _DRIVE_CACHE:
            return _DRIVE_CACHE[drive]

        if sys.platform == "win32":
            res = _detect_windows_rotational(drive)
            _DRIVE_CACHE[drive] = res
            return res
        elif sys.platform.startswith("linux"):
            res = _detect_linux_rotational(abs_path)
            _DRIVE_CACHE[drive] = res
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
        stat = os.stat(path)
        major = os.major(stat.st_dev)
        minor = os.minor(stat.st_dev)
        rot_file = f"/sys/dev/block/{major}:{minor}/queue/rotational"
        if os.path.isfile(rot_file):
            with open(rot_file, "r") as f:
                return f.read().strip() == "1"
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

