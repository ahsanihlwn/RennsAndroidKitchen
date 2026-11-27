# SPDX-License-Identifier: MPL-2.0
import ctypes
import os
import shutil
import sys
import threading
from concurrent.futures import Future, wait

if sys.platform == "win32":
    from ctypes import wintypes
else:
    wintypes = None


_VOLUME_QUERIES = {}
_VOLUME_LOCK = threading.Lock()
_SEEK_PENALTY_CACHE = {}

_DRIVE_FIXED = 3
_FILE_SHARE_READ_WRITE = 0x00000001 | 0x00000002
_OPEN_EXISTING = 3
_IOCTL_STORAGE_QUERY_PROPERTY = 0x002D1400
_STORAGE_DEVICE_SEEK_PENALTY_PROPERTY = 7
_PROPERTY_STANDARD_QUERY = 0

_PSEUDO_FS_TYPES = frozenset({
    "autofs", "binfmt_misc", "bpf", "cgroup", "cgroup2", "configfs",
    "debugfs", "devfs", "devpts", "devtmpfs", "efivarfs", "fuse.gvfsd-fuse",
    "fuse.lxcfs", "fuse.portal", "fusectl", "hugetlbfs", "map", "mqueue",
    "nsfs", "overlay", "proc", "pstore", "ramfs", "rpc_pipefs", "securityfs",
    "squashfs", "sysfs", "tmpfs", "tracefs",
})

_LINUX_SKIP_PREFIXES = (
    "/proc", "/sys", "/dev", "/run", "/snap", "/var/snap", "/boot/efi",
)

_MACOS_SKIP_PREFIXES = (
    "/dev", "/System/Volumes", "/private/var/vm",
)


if sys.platform == "win32":
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
        norm = str(key or "").strip()
        if not norm:
            return None
        with _VOLUME_LOCK:
            if norm in _SEEK_PENALTY_CACHE:
                return _SEEK_PENALTY_CACHE[norm]
        try:
            from core.storage_detector import is_rotational_drive
            result = bool(is_rotational_drive(norm))
        except Exception:
            result = None
        with _VOLUME_LOCK:
            _SEEK_PENALTY_CACHE[norm] = result
        return result
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


def _should_skip_posix_mount(mountpoint: str, fstype: str, platform: str) -> bool:
    mp = str(mountpoint or "")
    if not mp or not mp.startswith("/"):
        return True
    if str(fstype or "").lower() in _PSEUDO_FS_TYPES:
        return True
    if mp == "/":
        return False
    if platform == "darwin":
        if any(mp == p or mp.startswith(p + "/") for p in _MACOS_SKIP_PREFIXES):
            return True
    else:
        if any(mp == p or mp.startswith(p + "/") for p in _LINUX_SKIP_PREFIXES):
            return True
    return False


def _posix_drive_keys(platform: str | None = None) -> list[str]:
    plat = sys.platform if platform is None else platform
    if plat == "win32":
        return []
    keys: list[str] = []
    seen_devs: set[str] = set()
    seen_mps: set[str] = set()

    def _add(mp: str, dev: str = "", fstype: str = "") -> None:
        if _should_skip_posix_mount(mp, fstype, plat):
            return
        norm_mp = "/" if mp == "/" else mp.rstrip("/")
        if norm_mp in seen_mps:
            return
        if dev and dev in seen_devs and norm_mp != "/":
            return
        seen_mps.add(norm_mp)
        if dev:
            seen_devs.add(dev)
        keys.append(norm_mp)

    if os.path.isdir("/"):
        _add("/")

    try:
        import psutil
        for part in psutil.disk_partitions(all=False):
            _add(part.mountpoint, part.device or "", part.fstype or "")
    except Exception:
        if plat == "darwin":
            volumes = "/Volumes"
            if os.path.isdir(volumes):
                try:
                    for entry in sorted(os.listdir(volumes)):
                        cand = os.path.join(volumes, entry)
                        if os.path.isdir(cand) and not os.path.islink(cand):
                            _add(cand)
                except OSError:
                    pass
        else:
            try:
                with open("/proc/mounts", "r", encoding="utf-8", errors="ignore") as f:
                    for line in f:
                        parts = line.split()
                        if len(parts) >= 3:
                            dev, mp, fstype = parts[0], parts[1].replace("\\040", " "), parts[2]
                            if dev.startswith("/") or fstype in ("drvfs", "9p", "fuseblk", "ntfs3"):
                                _add(mp, dev, fstype)
            except OSError:
                pass

    return keys


def _volume_usage_path(key: str) -> str:
    if len(key) == 2 and key[1] == ":" and key[0].isalpha():
        return key + "\\"
    return key


def _volume_query(key):
    with _VOLUME_LOCK:
        query = _VOLUME_QUERIES.get(key)
        if query is not None and not query.done():
            return query
        query = Future()
        _VOLUME_QUERIES[key] = query

        def read():
            try:
                query.set_result(shutil.disk_usage(_volume_usage_path(key)))
            except OSError:
                query.set_result(None)
            except Exception as error:
                query.set_exception(error)

        # A volume query can hang inside the OS. Keep at most one
        # pending query per volume, and never wait for it during process exit.
        threading.Thread(target=read, name="rak-volume-" + key, daemon=True).start()
        return query


def list_windows_disk_usage(
    timeout: float = 1.0,
    *,
    include_rotational: bool = True,
    cached_rows: list[dict] | None = None,
) -> list[dict]:
    keys = _windows_drive_keys() if sys.platform == "win32" else _posix_drive_keys()
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


list_disk_usage = list_windows_disk_usage
