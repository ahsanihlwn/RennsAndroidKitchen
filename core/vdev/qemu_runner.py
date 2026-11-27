# SPDX-License-Identifier: MPL-2.0
"""QEMU ARM64 / ARM32 / ARM64+32 (64_32) Virtual Device runner for RAK Boot Analyzer.

Design & licensing boundaries:
- Executes QEMU strictly as an external subprocess (never linked into RAK / rak_native),
  keeping GPL-2.0 QEMU cleanly separated from MPL-2.0 RAK.
- Auto-provisions portable Upstream QEMU (GPL-2.0), official AOSP Linux 6.6 ARM64 VirtIO
  kernel (uncompressed Image) + 9 VirtIO .ko modules, real static first-stage /init,
  super.img / .zip partition extraction, sparse unsparsing, and valid EXT4 /metadata & /data.
- Non-destructive: all ROM partition images are attached read-only, with ephemeral writable
  /metadata and /data EXT4 images on a drive with ample free space.
"""
from __future__ import annotations

import base64
import datetime
import gzip
import hashlib
import json
import os
import re
import shutil
import socket
import struct
import threading
import time
import urllib.request
from dataclasses import dataclass, field

from core import kitchen_process
from core.app_paths import BIN_DIR, CACHE_DIR

QEMU_LICENSE = "GPL-2.0-only (external subprocess)"
VALID_ABIS = ("auto", "arm64", "arm64_32", "arm32")

# Official Google AOSP prebuilt QEMU VirtIO/Ranchu ARM64 Linux 6.6 kernel (GPL-2.0)
_AOSP_QEMU_BASE = (
    "https://android.googlesource.com/platform/prebuilts/qemu-kernel/"
    "+/refs/heads/main/arm64/6.6"
)
AOSP_QEMU_ARM64_KERNEL_URL = f"{_AOSP_QEMU_BASE}/kernel-6.6-gz?format=TEXT"

# Exact 9 AOSP 6.6 VirtIO .ko modules required by first-stage init for virtio-blk + virtio-gpu + input
# (intentionally excludes virtio_net.ko which depends on net_failover)
AOSP_QEMU_VIRTIO_MODULES: tuple[tuple[str, str], ...] = (
    ("goldfish_modules", "virtio_mmio.ko"),
    ("gki_modules", "virtio_pci_legacy_dev.ko"),
    ("gki_modules", "virtio_pci_modern_dev.ko"),
    ("gki_modules", "virtio_pci.ko"),
    ("gki_modules", "virtio_blk.ko"),
    ("gki_modules", "virtio_console.ko"),
    ("goldfish_modules", "virtio_dma_buf.ko"),
    ("goldfish_modules", "virtio-gpu.ko"),
    ("goldfish_modules", "virtio_input.ko"),
)

_VIRTIO_MODULES_DEP = (
    "virtio_mmio.ko:\n"
    "virtio_pci_legacy_dev.ko:\n"
    "virtio_pci_modern_dev.ko:\n"
    "virtio_pci.ko: virtio_pci_modern_dev.ko virtio_pci_legacy_dev.ko\n"
    "virtio_blk.ko:\n"
    "virtio_console.ko:\n"
    "virtio_dma_buf.ko:\n"
    "virtio-gpu.ko: virtio_dma_buf.ko\n"
    "virtio_input.ko:\n"
)

# Official Windows portable installer index (Stefan Weil's official qemu.org Windows builds, GPL-2.0)
QEMU_W64_INDEX_URL = "https://qemu.weilnetz.de/w64/"
QEMU_W64_FALLBACK_URL = "https://qemu.weilnetz.de/w64/qemu-w64-setup-20260811.exe"

_BOOT_MILESTONES = (
    ("kernel", re.compile(r"Linux version\s+\S+", re.I), "Kernel booted"),
    ("init_first", re.compile(r"init first stage started|first_stage_console", re.I), "init first stage"),
    ("fs_mounted", re.compile(r"__mount\(source=/dev/block/vd.*=0:\s*Success|Switching root to '/system'", re.I), "Partitions mounted"),
    ("selinux", re.compile(r"Opening SELinux policy|SELinux:\s+Initializing|Loading SELinux policy", re.I), "SELinux policy"),
    ("init_second", re.compile(r"init second stage started", re.I), "init second stage"),
    ("apexd", re.compile(r"\bapexd\b.*(?:Activated \d+ packages|Marking APEXd as activated|Successfully mounted package)", re.I), "APEX activated"),
    ("linkerconfig", re.compile(r"linkerconfig generated /linkerconfig|perform_apex_config.*succeeded", re.I), "Linkerconfig ready"),
    ("zygote", re.compile(r"\bzygote(?:64|32)?\b.*(?:Preloading|Starting|SystemServer)|starting service 'zygote", re.I), "Zygote started"),
    ("surfaceflinger", re.compile(r"SurfaceFlinger is starting|Connecting display .*HAL ID|RenderEngine.*OpenGL ES|starting service 'surfaceflinger'|sid=u:r:surfaceflinger:s0.*IComposer", re.I), "SurfaceFlinger & GPU"),
    ("bootanim", re.compile(r"starting service 'bootanim'|bootanimation\.zip is loaded|BootAnimationStartTiming", re.I), "BootAnimation"),
    ("system_server", re.compile(r"Entered the Android system server|SystemServer\s*:|sid=u:r:system_server:s0|comm=\"system_server\"", re.I), "SystemServer running"),
    ("systemui", re.compile(r"StartSystemUi|com\.android\.systemui", re.I), "SystemUI launching"),
    ("boot_completed", re.compile(r"sys\.boot_completed\s*=\s*1|Boot is finished|Displayed .*Launcher", re.I), "Boot completed"),
)

_PANIC_RE = re.compile(
    r"Kernel panic - not syncing|Unable to handle kernel|"
    r"CANNOT LINK EXECUTABLE[^\n]*(?:surfaceflinger|zygote|init|servicemanager|hwservicemanager)|"
    r"FATAL EXCEPTION IN SYSTEM PROCESS|init: critical process .* exited|"
    r"init: Failed to load kernel modules|init: Init encountered errors|SELinux:.*Could not load policy|"
    r"InitFatalReboot|Failed to initialize property area|Unable to serialize property contexts|"
    r"reboot:\s*Restarting system",
    re.I,
)

_LOGCAT_LINE_RE = re.compile(
    r"^(?:\d{2}-\d{2}\s+)?(\d{2}:\d{2}:\d{2}\.\d{3})\s+(\d+)\s+(\d+)\s+([VDIWEFA])\s+([^:]{1,40}):\s*(.*)$"
)
_DMESG_LINE_RE = re.compile(r"^\[\s*(\d+\.\d+)\]\s*(.*)$")

_KNOWN_PARTITIONS = (
    "system",
    "vendor",
    "product",
    "system_ext",
    "odm",
    "vendor_dlkm",
    "system_dlkm",
    "odm_dlkm",
    "mi_ext",
    "optics",
    "prism",
    "oem",
    "cust",
    "preload",
    "vgc",
    "custom",
    "special_preload",
    "oppo_product",
    "opproduct",
    "my_product",
    "my_stock",
    "my_bigball",
    "my_carrier",
    "my_company",
    "my_engineering",
    "my_heytap",
    "my_manifest",
    "my_preload",
    "my_region",
    "tr_mi",
    "tr_product",
    "tr_region",
    "tr_theme",
    "tr_preload",
    "tr_company",
    "tr_carrier",
    "tr_manifest",
    "tr_misc",
    "tr_overlayfs",
)

_OEM_PART_PREFIXES = ("tr_", "my_", "oppo_", "mi_", "oplus_")
_IGNORED_IMG_SUFFIXES = (
    "_new", "_old", "_bak", "_backup", "_orig", "_original", "_patched",
    "_mod", "_modded", "_test", "_tmp", "_temp", "_format", "_new_format",
    "_fixed", "_repack", "_signed", "_unsigned", "_raw", "_sparse", "_copy",
)
_NON_MOUNT_IMGS = {
    "boot", "init_boot", "vendor_boot", "recovery", "dtbo", "dtb", "vbmeta",
    "vbmeta_system", "vbmeta_vendor", "super", "super_empty", "userdata", "metadata",
    "cache", "modem", "dsp", "bluetooth", "abl", "xbl", "tz", "hyp", "keymaster",
    "preloader", "lk", "scp", "spmfw", "mcupm", "gz", "tee", "sspm", "dpm", "md1img",
    "efuse", "logo", "persist", "misc", "frp", "nvdata", "nvram", "protect1", "protect2",
}


def _is_mountable_partition_name(stem: str) -> bool:
    s = (stem or "").strip().lower()
    if not s or s in _NON_MOUNT_IMGS:
        return False
    if s in _KNOWN_PARTITIONS:
        return True
    if not re.fullmatch(r"[a-z0-9_]+", s):
        return False
    if any(s.endswith(suf) for suf in _IGNORED_IMG_SUFFIXES):
        return False
    return any(s.startswith(pref) for pref in _OEM_PART_PREFIXES)


def _exe(name: str) -> str:
    return f"{name}.exe" if os.name == "nt" else name


def _free_bytes(path: str) -> int:
    try:
        probe = path
        while probe and not os.path.exists(probe):
            parent = os.path.dirname(probe)
            if parent == probe:
                break
            probe = parent
        return int(shutil.disk_usage(probe or ".").free)
    except OSError:
        return 0


def _list_windows_fixed_drives() -> list[str]:
    if os.name != "nt":
        return []
    drives = []
    try:
        import ctypes
        bitmask = ctypes.windll.kernel32.GetLogicalDrives()
        for i in range(26):
            if bitmask & (1 << i):
                root = f"{chr(65 + i)}:\\"
                # 3 = DRIVE_FIXED
                if ctypes.windll.kernel32.GetDriveTypeW(root) == 3:
                    drives.append(root)
    except Exception:
        for ch in ("D", "C", "E", "F"):
            root = f"{ch}:\\"
            if os.path.isdir(root):
                drives.append(root)
    return drives


def _pick_qemu_work_root(hint_path: str = "", min_free_gb: float = 45.0) -> str:
    """Select a writable cache root with ample free space (never exhaust a nearly-full C: drive)."""
    env_override = (os.environ.get("RAK_QEMU_CACHE_DIR") or "").strip()
    if env_override:
        os.makedirs(env_override, exist_ok=True)
        return os.path.normpath(env_override)

    min_bytes = int(min_free_gb * 1024 * 1024 * 1024)
    default_root = os.path.normpath(os.path.join(CACHE_DIR, "vdev_qemu"))
    if _free_bytes(default_root) >= min_bytes:
        os.makedirs(default_root, exist_ok=True)
        return default_root

    # Prefer the ROM target's drive if it has sufficient free space
    candidates: list[tuple[int, str]] = []
    if hint_path and os.name == "nt":
        drv = os.path.splitdrive(os.path.abspath(hint_path))[0]
        if drv:
            cand = os.path.normpath(os.path.join(drv + "\\", ".rak_qemu_cache"))
            fb = _free_bytes(drv + "\\")
            if fb >= min_bytes:
                os.makedirs(cand, exist_ok=True)
                return cand
            candidates.append((fb, cand))

    # Otherwise pick the fixed drive with the most free space
    for drv in _list_windows_fixed_drives():
        cand = os.path.normpath(os.path.join(drv, ".rak_qemu_cache"))
        candidates.append((_free_bytes(drv), cand))

    candidates.append((_free_bytes(default_root), default_root))
    candidates.sort(key=lambda item: item[0], reverse=True)
    chosen = candidates[0][1]
    os.makedirs(chosen, exist_ok=True)
    return chosen


def _prune_old_sessions(work_root: str, keep: int = 0) -> None:
    """Remove older ephemeral sess_* directories immediately."""
    try:
        sess_dirs = sorted(
            [
                os.path.join(work_root, d)
                for d in os.listdir(work_root)
                if d.startswith("sess_") and os.path.isdir(os.path.join(work_root, d))
            ]
        )
        victims = sess_dirs[:-keep] if keep > 0 else sess_dirs
        for old in victims:
            shutil.rmtree(old, ignore_errors=True)
    except OSError:
        pass


def cleanup_vdev_ephemeral_caches(
    *,
    include_extracted: bool = True,
    exclude_dirs: set[str] | list[str] | None = None,
    async_run: bool = False,
) -> dict:
    """Clean all multi-GB ephemeral Boot Analyzer & QEMU caches while preserving kernels/ & QEMU binaries.

    Removes:
    - sess_* (528 MB ephemeral userdata/metadata/ramdisk per session)
    - zip_cache, vdev_zip, super_cache, raw_cache, templates (when include_extracted=True)

    Preserves:
    - kernels/ (~35 MB decompressed AOSP kernel + VirtIO .ko + cached static /init)
    - D:\\RAK_QEMU / BIN_DIR/qemu binaries
    - vdev_reports / vdev_vfs_view (tiny editor snapshots)
    """
    excluded = {os.path.normcase(os.path.normpath(p)) for p in (exclude_dirs or ()) if p}

    def _do_clean() -> dict:
        roots: list[str] = [
            os.path.normpath(os.path.join(CACHE_DIR, "vdev_qemu")),
            os.path.normpath(os.path.join(CACHE_DIR, "vdev_zip")),
        ]
        env_dir = (os.environ.get("RAK_QEMU_CACHE_DIR") or "").strip()
        if env_dir:
            roots.append(os.path.normpath(env_dir))
        for drv in _list_windows_fixed_drives():
            roots.append(os.path.normpath(os.path.join(drv, ".rak_qemu_cache")))

        removed: list[str] = []
        seen: set[str] = set()
        for root in roots:
            norm_root = os.path.normcase(root)
            if norm_root in seen or not os.path.isdir(root):
                continue
            seen.add(norm_root)

            # Direct vdev_zip root under CACHE_DIR
            if os.path.basename(root).lower() == "vdev_zip":
                if include_extracted and norm_root not in excluded:
                    shutil.rmtree(root, ignore_errors=True)
                    removed.append(root)
                continue

            try:
                names = list(os.listdir(root))
            except OSError:
                continue
            for name in names:
                full = os.path.normpath(os.path.join(root, name))
                if os.path.normcase(full) in excluded:
                    continue
                low = name.lower()
                if low == "kernels" or low == "qemu_bin":
                    continue
                should_rm = low.startswith("sess_") or (
                    include_extracted
                    and low in ("zip_cache", "vdev_zip", "super_cache", "raw_cache", "templates")
                )
                if should_rm:
                    if os.path.isdir(full):
                        shutil.rmtree(full, ignore_errors=True)
                        removed.append(full)
                    elif os.path.isfile(full):
                        try:
                            os.remove(full)
                            removed.append(full)
                        except OSError:
                            pass
        return {"removed": removed, "count": len(removed)}

    if async_run:
        threading.Thread(target=_do_clean, daemon=True, name="RAK-Vdev-CacheCleanup").start()
        return {"async": True}
    return _do_clean()


def _candidate_sdk_roots() -> list[str]:
    roots = []
    for env in ("ANDROID_HOME", "ANDROID_SDK_ROOT"):
        val = (os.environ.get(env) or "").strip()
        if val and os.path.isdir(val) and val not in roots:
            roots.append(val)
    if os.name == "nt":
        local_app = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~\\AppData\\Local")
        cand = os.path.join(local_app, "Android", "Sdk")
        if os.path.isdir(cand) and cand not in roots:
            roots.append(cand)
    else:
        for cand in (
            os.path.expanduser("~/Android/Sdk"),
            os.path.expanduser("~/Library/Android/sdk"),
        ):
            if os.path.isdir(cand) and cand not in roots:
                roots.append(cand)
    return roots


def _android_emu_subdir() -> str:
    if os.name == "nt":
        return "windows-x86_64"
    return "linux-x86_64"


def _find_upstream_binary(name: str) -> str:
    exe_name = _exe(name)

    # 0) Explicit env override
    env_dir = (os.environ.get("RAK_QEMU_DIR") or "").strip()
    if env_dir:
        for rel in (exe_name, os.path.join("bin", exe_name)):
            cand = os.path.join(env_dir, rel)
            if os.path.isfile(cand):
                return os.path.normpath(cand)

    # 1) RAK package directory (BIN_DIR/qemu)
    for rel in (
        os.path.join("qemu", exe_name),
        os.path.join("qemu", "bin", exe_name),
        exe_name,
    ):
        cand = os.path.join(BIN_DIR, rel)
        if os.path.isfile(cand):
            return os.path.normpath(cand)

    # 2) Portable <Drive>:\RAK_QEMU on fixed drives (preferred when C: is low on space)
    if os.name == "nt":
        for drv in _list_windows_fixed_drives():
            for sub in ("RAK_QEMU", os.path.join(".rak_qemu_cache", "qemu_bin")):
                cand = os.path.join(drv, sub, exe_name)
                if os.path.isfile(cand):
                    return os.path.normpath(cand)

    # 3) PATH
    found = shutil.which(name) or shutil.which(exe_name)
    if found and os.path.isfile(found):
        return os.path.normpath(found)

    # 4) Standard install directories
    extra_dirs = (
        [
            r"C:\Program Files\qemu",
            r"C:\Program Files (x86)\qemu",
            r"C:\msys64\ucrt64\bin",
            r"C:\msys64\mingw64\bin",
        ]
        if os.name == "nt"
        else ["/usr/bin", "/usr/local/bin", "/opt/homebrew/bin"]
    )
    for d in extra_dirs:
        cand = os.path.join(d, exe_name)
        if os.path.isfile(cand):
            return os.path.normpath(cand)
    return ""


def _find_android_sdk_qemu(arch: str) -> tuple[str, str]:
    """Return (binary_path, emulator_root) for Android SDK's bundled QEMU ranchu binary."""
    sub = _android_emu_subdir()
    names = (
        [_exe("qemu-system-aarch64-headless"), _exe("qemu-system-aarch64")]
        if arch in ("arm64", "arm64_32")
        else [_exe("qemu-system-armel-headless"), _exe("qemu-system-armel")]
    )
    for sdk in _candidate_sdk_roots():
        emu = os.path.join(sdk, "emulator")
        qdir = os.path.join(emu, "qemu", sub)
        for nm in names:
            cand = os.path.join(qdir, nm)
            if os.path.isfile(cand):
                return os.path.normpath(cand), os.path.normpath(emu)
    return "", ""


def _find_7z_binary() -> str:
    found = shutil.which("7z") or shutil.which("7za")
    if found and os.path.isfile(found):
        return found
    if os.name == "nt":
        for cand in (
            r"C:\Program Files\7-Zip\7z.exe",
            r"C:\Program Files (x86)\7-Zip\7z.exe",
            os.path.join(BIN_DIR, "7z.exe"),
        ):
            if os.path.isfile(cand):
                return cand
    return ""


def ensure_qemu_binary(*, on_progress=None, hint_path: str = "") -> tuple[bool, str]:
    """Ensure portable Upstream QEMU (`qemu-system-aarch64`) is available; auto-download if missing on Windows."""
    existing = _find_upstream_binary("qemu-system-aarch64")
    if existing:
        return True, existing

    if os.name != "nt":
        return False, "Upstream QEMU not found. Install `qemu-system-aarch64` via your system package manager."

    sevenz = _find_7z_binary()
    if not sevenz:
        return False, (
            "Upstream QEMU not found and 7-Zip is unavailable for portable extraction. "
            "Install QEMU from https://qemu.weilnetz.de/w64/ or install 7-Zip."
        )

    # Pick install target on a drive with >= 1.5 GB free
    work_root = _pick_qemu_work_root(hint_path, min_free_gb=1.5)
    drv = os.path.splitdrive(work_root)[0]
    install_dir = os.path.normpath(os.path.join(drv + "\\", "RAK_QEMU")) if drv else os.path.join(work_root, "qemu_bin")
    exe_target = os.path.join(install_dir, "qemu-system-aarch64.exe")
    if os.path.isfile(exe_target):
        return True, exe_target

    os.makedirs(install_dir, exist_ok=True)
    dl_url = QEMU_W64_FALLBACK_URL
    try:
        if on_progress:
            on_progress("Checking latest official Upstream QEMU (GPL-2.0) Windows build…")
        req = urllib.request.Request(QEMU_W64_INDEX_URL, headers={"User-Agent": "RennsAndroidKitchen/1.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            html = resp.read().decode("utf-8", "replace")
        matches = sorted(set(re.findall(r'qemu-w64-setup-\d{8}\.exe', html)))
        if matches:
            dl_url = QEMU_W64_INDEX_URL + matches[-1]
    except Exception:
        pass

    installer_path = os.path.join(work_root, "qemu-w64-setup.exe.tmp")
    try:
        if on_progress:
            on_progress(f"Auto-downloading portable Upstream QEMU ({os.path.basename(dl_url)})…")
        req = urllib.request.Request(dl_url, headers={"User-Agent": "RennsAndroidKitchen/1.0"})
        written = 0
        last_t = 0.0
        with urllib.request.urlopen(req, timeout=60) as resp, open(installer_path, "wb") as out_fh:
            while True:
                chunk = resp.read(262144)
                if not chunk:
                    break
                out_fh.write(chunk)
                written += len(chunk)
                now = time.monotonic()
                if on_progress and (now - last_t) >= 0.5:
                    last_t = now
                    on_progress(f"Downloading Upstream QEMU ({written / 1048576:.1f} MB)…")

        if on_progress:
            on_progress(f"Extracting portable Upstream QEMU into {install_dir}…")
        with kitchen_process.without_console():
            res = kitchen_process.run(
                [sevenz, "x", "-y", f"-o{install_dir}", installer_path],
                capture_output=True,
                text=True,
                creationflags=kitchen_process.CREATE_NO_WINDOW,
            )
        if res.returncode != 0 or not os.path.isfile(exe_target):
            return False, f"Failed to extract Upstream QEMU (7z exit={res.returncode})."
        if on_progress:
            on_progress(f"Portable Upstream QEMU ready at {install_dir}")
        return True, os.path.normpath(exe_target)
    except Exception as exc:
        return False, f"Auto-download Upstream QEMU failed: {exc}"
    finally:
        try:
            if os.path.isfile(installer_path):
                os.remove(installer_path)
        except OSError:
            pass


def _candidate_kernel_dirs() -> list[str]:
    dirs = [os.path.normpath(os.path.join(BIN_DIR, "qemu", "kernels"))]
    if os.name == "nt":
        for drv in _list_windows_fixed_drives():
            cand = os.path.normpath(os.path.join(drv, ".rak_qemu_cache", "kernels"))
            if cand not in dirs:
                dirs.append(cand)
    return dirs


def find_qemu_kernels() -> dict[str, str]:
    """Locate VirtIO/GKI ARM64 and ARM32 kernel images in RAK BIN_DIR, cache, or Android SDK."""
    found = {"arm64": "", "arm32": ""}

    # 1) RAK BIN_DIR/qemu/kernels/ and free-space cache kernels
    for kdir in _candidate_kernel_dirs():
        if not os.path.isdir(kdir):
            continue
        for nm in ("Image-arm64", "kernel-ranchu-arm64", "kernel-arm64", "Image.gz", "Image"):
            p = os.path.join(kdir, nm)
            if os.path.isfile(p) and not found["arm64"]:
                found["arm64"] = os.path.normpath(p)
        for nm in ("kernel-ranchu-arm32", "kernel-arm32", "zImage-arm32", "zImage"):
            p = os.path.join(kdir, nm)
            if os.path.isfile(p) and not found["arm32"]:
                found["arm32"] = os.path.normpath(p)

    # 2) Android SDK system-images (prefer newest API level)
    for sdk in _candidate_sdk_roots():
        si = os.path.join(sdk, "system-images")
        if not os.path.isdir(si):
            continue
        api_dirs = sorted(os.listdir(si), reverse=True)
        for api in api_dirs:
            api_path = os.path.join(si, api)
            if not os.path.isdir(api_path):
                continue
            for tag in sorted(os.listdir(api_path)):
                tag_path = os.path.join(api_path, tag)
                if not os.path.isdir(tag_path):
                    continue
                for abi_dir, key in (("arm64-v8a", "arm64"), ("armeabi-v7a", "arm32")):
                    if found[key]:
                        continue
                    for kn in ("kernel-ranchu", "kernel-ranchu-64", "kernel-qemu"):
                        kp = os.path.join(tag_path, abi_dir, kn)
                        if os.path.isfile(kp):
                            found[key] = os.path.normpath(kp)
                            break
    return found


def find_qemu_virtio_modules_dir() -> str:
    """Return directory containing all 9 AOSP 6.6 VirtIO .ko modules, or '' if incomplete."""
    required = [name for _, name in AOSP_QEMU_VIRTIO_MODULES]
    for kdir in _candidate_kernel_dirs():
        mdir = os.path.join(kdir, "modules_6.6")
        if os.path.isdir(mdir) and all(os.path.isfile(os.path.join(mdir, nm)) for nm in required):
            return os.path.normpath(mdir)
    return ""


def detect_qemu_environment() -> dict:
    """Probe available QEMU ARM64 / ARM32 binaries, VirtIO/GKI kernels, and VirtIO .ko modules."""
    up_64 = _find_upstream_binary("qemu-system-aarch64")
    up_32 = _find_upstream_binary("qemu-system-arm")
    sdk_64, emu_root = _find_android_sdk_qemu("arm64")
    sdk_32, emu_root_32 = _find_android_sdk_qemu("arm32")
    emu_root = emu_root or emu_root_32
    kernels = find_qemu_kernels()
    modules_dir = find_qemu_virtio_modules_dir()

    if up_64 or up_32:
        backend = "upstream"
        bin_64 = up_64 or sdk_64
        bin_32 = up_32 or sdk_32
        backend_label = "Upstream QEMU (GPL-2.0)"
    elif sdk_64 or sdk_32:
        backend = "android-qemu"
        bin_64 = sdk_64
        bin_32 = sdk_32
        backend_label = "Android SDK QEMU (GPL-2.0)"
    else:
        backend = "none"
        bin_64 = ""
        bin_32 = ""
        backend_label = "Not installed (auto-provision available)"

    return {
        "available": bool(bin_64 or bin_32),
        "upstreamAvailable": bool(up_64 or up_32),
        "backend": backend,
        "backendLabel": backend_label,
        "license": QEMU_LICENSE,
        "arm64Binary": bin_64,
        "arm32Binary": bin_32,
        "upstreamArm64": up_64,
        "upstreamArm32": up_32,
        "androidSdkArm64": sdk_64,
        "androidSdkArm32": sdk_32,
        "emulatorRoot": emu_root,
        "arm64Kernel": kernels["arm64"],
        "arm32Kernel": kernels["arm32"],
        "virtioModulesDir": modules_dir,
        "kernelDir": os.path.normpath(os.path.join(BIN_DIR, "qemu", "kernels")),
        "supportedAbis": [
            a
            for a, ok in (
                ("arm64", bool(bin_64)),
                ("arm64_32", bool(bin_64)),
                ("arm32", bool(bin_32 or bin_64)),
            )
            if ok
        ],
    }


def resolve_qemu_abi(profile: dict | None, requested_abi: str = "auto") -> str:
    """Resolve effective Android ABI mode: 'arm64', 'arm64_32', or 'arm32'."""
    req = str(requested_abi or "auto").strip().lower().replace("-", "_")
    if req in ("64", "aarch64", "arm64_v8a", "arm64"):
        return "arm64"
    if req in ("64_32", "63_32", "arm64_32", "zygote64_32"):
        return "arm64_32"
    if req in ("32", "arm", "armv7", "armeabi_v7a", "arm32"):
        return "arm32"

    prof = profile or {}
    props = prof.get("props") or {}
    zyg = str(props.get("ro.zygote") or "").strip().lower()
    if "64_32" in zyg or "32_64" in zyg:
        return "arm64_32"
    if zyg == "zygote64":
        return "arm64"
    if zyg == "zygote32":
        return "arm32"

    has64 = bool(prof.get("has_lib64", True))
    has32 = bool(prof.get("has_lib32", False))
    if has64 and has32:
        return "arm64_32"
    if has64:
        return "arm64"
    return "arm32"


# ── Pure-Python newc CPIO packer (for non-destructive QEMU boot ramdisk) ──

def _cpio_newc_entry(name: str, data: bytes, mode: int = 0o100644, ino: int = 1) -> bytes:
    clean = name.lstrip("/")
    name_b = clean.encode("utf-8") + b"\x00"
    header = (
        f"070701"
        f"{ino:08x}"
        f"{mode:08x}"
        f"{0:08x}{0:08x}"
        f"{1:08x}"
        f"{0:08x}"
        f"{len(data):08x}"
        f"{0:08x}{0:08x}{0:08x}{0:08x}"
        f"{len(name_b):08x}"
        f"{0:08x}"
    ).encode("ascii")
    out = bytearray(header)
    out.extend(name_b)
    while len(out) % 4:
        out.append(0)
    out.extend(data)
    while len(out) % 4:
        out.append(0)
    return bytes(out)


def build_qemu_overlay_cpio_gz(
    partitions: list[tuple[str, str]],
    *,
    abi_mode: str = "arm64_32",
    headless: bool = True,
    permissive: bool = True,
    first_stage_init_bytes: bytes = b"",
    modules_dir: str = "",
    hardware_gate: dict | None = None,
    virtio_gfx_overlay: bool = False,
) -> bytes:
    """Synthesize a bootable gzipped CPIO initramfs with static /init, VirtIO .kos, fstab.qemu & init.qemu.rc."""
    fstab_lines = [
        "# Generated by RAK Boot Analyzer (QEMU virtual device overlay — non-destructive)",
    ]
    part_mount_dirs: list[str] = []
    # Check whether real kernel IKCFG explicitly lacks support for a partition's filesystem
    unsupported_fs = set((hardware_gate or {}).get("unsupportedFsTypes") or ())

    # virtio-blk devices map in attachment order: vda, vdb, vdc, ...
    has_vendor = False
    has_oem = False
    for idx, (part_name, fs_type) in enumerate(partitions):
        dev = f"/dev/block/vd{chr(ord('a') + idx)}"
        ft = (fs_type or "ext4").lower()
        if ft == "ext":
            ft = "ext4"
        if part_name == "vendor":
            has_vendor = True
        if part_name == "oem":
            has_oem = True
        if part_name in ("userdata", "data"):
            fstab_lines.append(f"{dev}  /data  ext4  rw,noatime,nosuid,nodev  wait,first_stage_mount")
            part_mount_dirs.append("data")
        elif part_name == "metadata":
            fstab_lines.append(f"{dev}  /metadata  ext4  rw,noatime,nosuid,nodev  wait,first_stage_mount")
            part_mount_dirs.append("metadata")
        elif part_name == "misc":
            # BCB / bootctl block device (not mounted as a filesystem during first_stage_mount)
            fstab_lines.append(f"{dev}  /misc  emmc  defaults  defaults")
        elif part_name == "system":
            # Enforce real kernel IKCFG: if the ROM's real kernel lacks this FS, do NOT let generic QEMU silently pass!
            eff_ft = f"{ft}_unsupported_by_kernel" if ft in unsupported_fs else ft
            fstab_lines.append(f"{dev}  /system  {eff_ft}  ro  wait,first_stage_mount")
            part_mount_dirs.append("system")
        else:
            eff_ft = f"{ft}_unsupported_by_kernel" if ft in unsupported_fs else ft
            fstab_lines.append(f"{dev}  /{part_name}  {eff_ft}  ro  wait,first_stage_mount")
            part_mount_dirs.append(part_name)

    # Generic VirtIO Graphics Bridge (STRICTLY gated: ONLY enabled when Kernel<->Vendor contract PASSED!)
    # Mounts read-only overlayfs lowerdir=/oem:/vendor so /vendor is untouched on disk while exposing
    # pure-DRM composer3 + minigbm_gbm_mesa + Mesa llvmpipe/SwiftShader to SurfaceFlinger & BootAnimation.
    if virtio_gfx_overlay and has_vendor and has_oem and not (hardware_gate or {}).get("bootBlockedByVendor"):
        fstab_lines.append("overlay  /vendor  overlay  ro,lowerdir=/oem:/vendor  first_stage_mount")

    zyg_prop = {
        "arm64": "zygote64",
        "arm64_32": "zygote64_32",
        "arm32": "zygote32",
    }.get(abi_mode, "zygote64_32")

    gate_props = []
    for sub in ((hardware_gate or {}).get("subsystems") or ()):
        k = str(sub.get("key") or "").strip()
        st = str(sub.get("status") or "generic").strip().lower()
        if k:
            gate_props.append(f"    setprop ro.rak.hwgate.{k} {st}")
    if (hardware_gate or {}).get("bootBlockedByVendor"):
        gate_props.append("    setprop ro.rak.vendor_fault 1")

    # Do NOT override/mask broken /vendor graphics/HAL properties (no fake ranchu/swiftshader override):
    # let the ROM's real /vendor run unmodified so broken vendor HALs surface real runtime errors!
    init_rc_lines = [
        "# Generated by RAK Boot Analyzer — Kernel-Contract QEMU Overlay (vendor unmodified)",
        "on early-init",
        f"    setprop ro.zygote {zyg_prop}",
        "    setprop debug.sf.nobootanimation 1" if headless else "    setprop debug.sf.nobootanimation 0",
        "    setprop persist.sys.usb.config adb",
        *gate_props,
        "",
        "service rak_logcat_console /system/bin/logcat -v threadtime",
        "    class main",
        "    user root",
        "    group root log",
        "    console",
        "    oneshot",
        "",
    ]

    fstab_bytes = ("\n".join(fstab_lines) + "\n").encode("utf-8")
    init_rc_bytes = ("\n".join(init_rc_lines) + "\n").encode("utf-8")

    buf = bytearray()
    ino = 1

    def _add_dir(path: str):
        nonlocal ino
        buf.extend(_cpio_newc_entry(path, b"", mode=0o040755, ino=ino))
        ino += 1

    def _add_file(path: str, payload: bytes, mode: int = 0o100644):
        nonlocal ino
        buf.extend(_cpio_newc_entry(path, payload, mode=mode, ino=ino))
        ino += 1

    # Standard Android first-stage mountpoint directories (missing /debug_ramdisk or /second_stage_resources aborts init!)
    base_dirs = [
        "dev", "proc", "sys", "mnt", "mnt/vendor", "mnt/product",
        "system", "system/bin", "system/etc",
        "vendor", "product", "system_ext", "odm", "vendor_dlkm", "system_dlkm", "odm_dlkm", "mi_ext",
        "lib", "lib/modules",
        "debug_ramdisk", "second_stage_resources", "first_stage_ramdisk",
        "metadata", "data", "apex", "linkerconfig", "acct", "config", "cache",
        "postinstall", "storage", "oem",
    ]
    seen_dirs: set[str] = set()
    for d in base_dirs:
        if d not in seen_dirs:
            _add_dir(d)
            seen_dirs.add(d)
    for pdir in part_mount_dirs:
        if pdir and pdir not in seen_dirs:
            _add_dir(pdir)
            seen_dirs.add(pdir)
        fs_sub = f"first_stage_ramdisk/{pdir}"
        if pdir and fs_sub not in seen_dirs:
            _add_dir(fs_sub)
            seen_dirs.add(fs_sub)

    if first_stage_init_bytes:
        fs_blob = first_stage_init_bytes
        if virtio_gfx_overlay and has_vendor and has_oem and not (hardware_gate or {}).get("bootBlockedByVendor"):
            # Android 15/16 user builds (e.g. Transsion XOS 16) check /proc/mounts with
            # StartsWith(line, "overlay") inside SetupSelinux() and abort trying to copy
            # /system/xbin/overlay_remounter under enforcing SELinux. Renaming the ephemeral
            # overlay mount-source label from "overlay-" to "rak_gfx-" keeps fstype="overlay"
            # identical in the kernel while preventing SetupSelinux() from misclassifying our
            # read-only VirtIO Graphics Bridge as an `adb remount` / tranoverlay mount.
            fs_blob = fs_blob.replace(b"overlay-\x00", b"rak_gfx-\x00", 1)
        _add_file("init", fs_blob, mode=0o100755)

    # Include AOSP 6.6 VirtIO .ko modules + modules.load + modules.dep when available
    if modules_dir and os.path.isdir(modules_dir):
        loaded: list[str] = []
        for _, mod_name in AOSP_QEMU_VIRTIO_MODULES:
            mp = os.path.join(modules_dir, mod_name)
            if os.path.isfile(mp):
                try:
                    with open(mp, "rb") as mfh:
                        _add_file(f"lib/modules/{mod_name}", mfh.read(), mode=0o100644)
                    loaded.append(mod_name)
                except OSError:
                    pass
        if loaded:
            # Real-stage KMI enforcement: if vendor_dlkm/.ko KMI CRC or symbols mismatch the ROM's real kernel,
            # inject a failing module entry so first-stage init genuinely fails LoadKernelModules -> Kernel panic!
            if (hardware_gate or {}).get("kmiFaults"):
                loaded.append("vendor_kmi_mismatch_gate.ko")
            _add_file("lib/modules/modules.load", ("\n".join(loaded) + "\n").encode("utf-8"))
            _add_file("lib/modules/modules.dep", _VIRTIO_MODULES_DEP.encode("utf-8"))

    # Write fstab.qemu in all standard Android search paths + fstab.ranchu + init.qemu.rc
    for rel, blob in (
        ("fstab.qemu", fstab_bytes),
        ("first_stage_ramdisk/fstab.qemu", fstab_bytes),
        ("system/etc/fstab.qemu", fstab_bytes),
        ("fstab.ranchu", fstab_bytes),
        ("init.qemu.rc", init_rc_bytes),
        ("init.ranchu.rc", init_rc_bytes),
    ):
        _add_file(rel, blob, mode=0o100644)

    buf.extend(_cpio_newc_entry("TRAILER!!!", b"", mode=0, ino=0))
    return gzip.compress(bytes(buf))


def _is_android_sparse(path: str) -> bool:
    try:
        with open(path, "rb") as fh:
            return fh.read(4) == b"\x3a\xff\x26\xed"
    except OSError:
        return False


def _detect_fs_type(path: str) -> str:
    try:
        with open(path, "rb") as fh:
            head = fh.read(4096)
    except OSError:
        return "ext4"
    if len(head) >= 1028 and head[1024:1028] == b"\xe2\xe1\xf5\xe0":
        return "erofs"
    if len(head) >= 1082 and head[1080:1082] == b"\x53\xef":
        return "ext4"
    return "ext4"


_VIRGL_SUPPORT_CACHE: dict[str, bool] = {}


def _qemu_supports_virgl(binary: str) -> bool:
    """Probe once per QEMU binary whether virtio-gpu-gl-pci + egl-headless are compiled in."""
    key = os.path.normcase(os.path.abspath(binary or ""))
    if key in _VIRGL_SUPPORT_CACHE:
        return _VIRGL_SUPPORT_CACHE[key]
    ok = False
    try:
        if binary and os.path.isfile(binary):
            import subprocess as _sp
            kw: dict = {"capture_output": True, "text": True, "timeout": 8}
            if os.name == "nt":
                kw["creationflags"] = getattr(_sp, "CREATE_NO_WINDOW", 0)
            r_dev = _sp.run([binary, "-device", "virtio-gpu-gl-pci,help"], **kw)
            r_disp = _sp.run([binary, "-display", "help"], **kw)
            ok = (
                r_dev.returncode == 0
                and "virtio-gpu-gl-pci" in (r_dev.stdout or "")
                and "egl-headless" in (r_disp.stdout or "")
            )
    except Exception:
        ok = False
    _VIRGL_SUPPORT_CACHE[key] = ok
    return ok


def detect_rom_mapper_suffix(vendor_img_path: str) -> str:
    """Inspect a vendor image's SELinux vendor_service_contexts (read-only) for authorized `mapper/<suffix>` HALs."""
    if not vendor_img_path or not os.path.isfile(vendor_img_path):
        return ""
    try:
        from core.vdev.mounts import ImageSource
        src = ImageSource(vendor_img_path)
        raw = src.read("etc/selinux/vendor_service_contexts")
        if not raw:
            return ""
        text = raw.decode("utf-8", "ignore")
        fallback = ""
        for ln in text.splitlines():
            s = ln.strip()
            if not s or s.startswith("#"):
                continue
            parts = s.split()
            if len(parts) >= 2 and parts[0].startswith("mapper/"):
                suffix = parts[0].split("/", 1)[1].strip()
                if not suffix:
                    continue
                if "hal_graphics_mapper_service" in parts[1]:
                    return suffix
                if not fallback:
                    fallback = suffix
        return fallback
    except Exception:
        return ""


def _pick_free_vnc_port() -> tuple[int, int]:
    """Allocate a free localhost TCP port >= 5910 and return (tcp_port, vnc_display_num)."""
    for port in range(5910, 5999):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
                return port, port - 5900
            except OSError:
                continue
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        return port, max(0, port - 5900)


def _download_gitiles_b64(url: str, dest_path: str, *, on_progress=None, label: str = "") -> tuple[bool, int, str]:
    """Download a binary file from Google Gitiles (?format=TEXT base64 stream)."""
    tmp_path = dest_path + ".downloading"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "RennsAndroidKitchen/1.0"})
        b64_carry = b""
        written_bytes = 0
        last_report = 0.0
        with urllib.request.urlopen(req, timeout=30) as resp, open(tmp_path, "wb") as out_fh:
            while True:
                chunk = resp.read(131072)
                if not chunk:
                    break
                cleaned = b64_carry + b"".join(chunk.split())
                usable = (len(cleaned) // 4) * 4
                if usable:
                    dec = base64.b64decode(cleaned[:usable])
                    out_fh.write(dec)
                    written_bytes += len(dec)
                b64_carry = cleaned[usable:]
                now = time.monotonic()
                if on_progress and label and (now - last_report) >= 0.4:
                    last_report = now
                    on_progress(f"Downloading {label} ({written_bytes / 1048576:.1f} MB)…")
            if b64_carry:
                pad = b64_carry + b"=" * ((4 - len(b64_carry) % 4) % 4)
                dec = base64.b64decode(pad)
                out_fh.write(dec)
                written_bytes += len(dec)
        os.replace(tmp_path, dest_path)
        return True, written_bytes, ""
    except Exception as exc:
        try:
            if os.path.isfile(tmp_path):
                os.remove(tmp_path)
        except OSError:
            pass
        return False, 0, str(exc)


def ensure_qemu_virtio_modules(*, on_progress=None, work_root: str = "") -> tuple[bool, str]:
    """Ensure all 9 official AOSP 6.6 VirtIO .ko modules are present locally."""
    existing = find_qemu_virtio_modules_dir()
    if existing:
        return True, existing

    kdir = os.path.join(BIN_DIR, "qemu", "kernels")
    if _free_bytes(kdir) < 20 * 1024 * 1024 and work_root:
        kdir = os.path.join(work_root, "kernels")
    mdir = os.path.normpath(os.path.join(kdir, "modules_6.6"))
    os.makedirs(mdir, exist_ok=True)

    missing = [(sub, nm) for sub, nm in AOSP_QEMU_VIRTIO_MODULES if not os.path.isfile(os.path.join(mdir, nm))]
    if missing and on_progress:
        on_progress(f"Auto-downloading {len(missing)} AOSP 6.6 VirtIO kernel modules…")

    for sub, nm in missing:
        url = f"{_AOSP_QEMU_BASE}/{sub}/{nm}?format=TEXT"
        dest = os.path.join(mdir, nm)
        ok, nbytes, err = _download_gitiles_b64(url, dest)
        if not ok or nbytes < 1024:
            return False, f"Failed to download {nm}: {err or 'empty file'}"
        with open(dest, "rb") as fh:
            if fh.read(4) != b"\x7fELF":
                try:
                    os.remove(dest)
                except OSError:
                    pass
                return False, f"Downloaded {nm} is not a valid ELF kernel module."
    return True, mdir


def _ensure_uncompressed_arm64_kernel(kpath: str, work_root: str, *, on_progress=None) -> str:
    """Upstream ARM64 QEMU `-kernel` requires an uncompressed ARM64 Image (not gzip)."""
    try:
        with open(kpath, "rb") as fh:
            head = fh.read(64)
    except OSError:
        return kpath

    # Already an uncompressed ARM64 Image?
    if len(head) >= 60 and head[56:60] == b"ARM\x64":
        return kpath
    if len(head) < 2 or head[:2] != b"\x1f\x8b":
        return kpath

    # Decompress into a cache directory with free space
    kdir = os.path.join(work_root, "kernels")
    os.makedirs(kdir, exist_ok=True)
    st = os.stat(kpath)
    out_img = os.path.normpath(os.path.join(kdir, f"Image-arm64-{st.st_size}.raw"))
    if os.path.isfile(out_img) and os.path.getsize(out_img) > 10 * 1024 * 1024:
        return out_img

    if on_progress:
        on_progress("Decompressing AOSP 6.6 ARM64 kernel Image for QEMU…")
    tmp = out_img + ".tmp"
    try:
        with gzip.open(kpath, "rb") as fin, open(tmp, "wb") as fout:
            shutil.copyfileobj(fin, fout, length=1024 * 1024)
        os.replace(tmp, out_img)
        return out_img
    except Exception:
        try:
            if os.path.isfile(tmp):
                os.remove(tmp)
        except OSError:
            pass
        return kpath


def ensure_qemu_kernel(abi_mode: str = "arm64_32", *, on_progress=None, work_root: str = "") -> tuple[bool, str]:
    """Return (ok, kernel_path_or_error). Auto-downloads AOSP 6.6 VirtIO ARM64 kernel + 9 VirtIO .ko modules."""
    kernels = find_qemu_kernels()
    existing = kernels["arm32"] if abi_mode == "arm32" and kernels["arm32"] else kernels["arm64"]
    if existing and os.path.isfile(existing):
        ensure_qemu_virtio_modules(on_progress=on_progress, work_root=work_root)
        return True, existing

    kdir = os.path.join(BIN_DIR, "qemu", "kernels")
    if _free_bytes(kdir) < 60 * 1024 * 1024 and work_root:
        kdir = os.path.join(work_root, "kernels")
    os.makedirs(kdir, exist_ok=True)
    dest = os.path.normpath(os.path.join(kdir, "kernel-ranchu-arm64"))

    if on_progress:
        on_progress("Auto-downloading official AOSP VirtIO ARM64 6.6 kernel…")

    ok, written_bytes, err = _download_gitiles_b64(
        AOSP_QEMU_ARM64_KERNEL_URL,
        dest,
        on_progress=on_progress,
        label="AOSP VirtIO ARM64 6.6 kernel",
    )
    if not ok:
        return False, f"Auto-download AOSP VirtIO kernel failed: {err}"

    # Verify magic (gzip 0x1f 0x8b or raw ARM64 Image header)
    with open(dest, "rb") as chk:
        head = chk.read(64)
    valid_magic = (len(head) >= 2 and head[:2] == b"\x1f\x8b") or (len(head) >= 60 and head[56:60] == b"ARM\x64")
    if written_bytes < 1024 * 1024 or not valid_magic:
        try:
            os.remove(dest)
        except OSError:
            pass
        return False, "Downloaded kernel failed integrity check (invalid gzip/ARM64 header)."

    ensure_qemu_virtio_modules(on_progress=on_progress, work_root=work_root)
    if on_progress:
        on_progress(f"AOSP VirtIO ARM64 kernel ready ({written_bytes / 1048576:.1f} MB)")
    return True, dest


# ── Real static first-stage /init extractor & cache ──

def _is_static_arm64_elf(blob: bytes) -> bool:
    """Verify blob is a statically linked AArch64 ELF (no PT_INTERP program header)."""
    if not blob or len(blob) < 64 or blob[:4] != b"\x7fELF":
        return False
    if blob[4] != 2 or blob[5] != 1:  # 64-bit little-endian
        return False
    e_machine = struct.unpack_from("<H", blob, 18)[0]
    if e_machine != 183:  # EM_AARCH64
        return False
    e_phoff = struct.unpack_from("<Q", blob, 32)[0]
    e_phentsize = struct.unpack_from("<H", blob, 54)[0]
    e_phnum = struct.unpack_from("<H", blob, 56)[0]
    if e_phoff == 0 or e_phentsize < 56:
        return False
    for i in range(min(e_phnum, 64)):
        off = e_phoff + i * e_phentsize
        if off + 4 > len(blob):
            break
        p_type = struct.unpack_from("<I", blob, off)[0]
        if p_type == 3:  # PT_INTERP -> dynamically linked!
            return False
    return True


def _extract_static_init_from_boot_bytes(raw_boot: bytes) -> bytes:
    """Parse an Android boot.img / init_boot.img byte buffer and return static ARM64 /init if found."""
    if not raw_boot or len(raw_boot) < 4096:
        return b""
    try:
        from core.vdev import bootimg
        if raw_boot[:8] == bootimg.BOOT_MAGIC:
            span = bootimg._boot_ramdisk_span(raw_boot[:4096])
        elif raw_boot[:8] == bootimg.VENDOR_BOOT_MAGIC:
            span = bootimg._vendor_ramdisk_span(raw_boot[:4096])
        else:
            return b""
        if not span:
            return b""
        off, sz = span
        if sz <= 0 or off + sz > len(raw_boot):
            return b""
        cpio_raw = bootimg._decompress(raw_boot[off:off + sz])
        if not cpio_raw:
            return b""
        for name, _mode, payload in bootimg.cpio_entries(
            cpio_raw,
            want=lambda n: n.lstrip("/") in ("init", "system/bin/init"),
        ):
            if _is_static_arm64_elf(payload):
                return payload
    except Exception:
        pass
    return b""


def _extract_static_init_from_boot_file(path: str) -> bytes:
    if not path or not os.path.isfile(path):
        return b""
    try:
        from core.vdev import bootimg
        cpio_raw = bootimg.read_ramdisk(path)
        if not cpio_raw:
            return b""
        for name, _mode, payload in bootimg.cpio_entries(
            cpio_raw,
            want=lambda n: n.lstrip("/") in ("init", "system/bin/init"),
        ):
            if _is_static_arm64_elf(payload):
                return payload
    except Exception:
        pass
    return b""


def _cached_first_stage_init_paths(work_root: str = "") -> list[str]:
    paths = [os.path.normpath(os.path.join(kdir, "first_stage_init_arm64")) for kdir in _candidate_kernel_dirs()]
    if work_root:
        p = os.path.normpath(os.path.join(work_root, "kernels", "first_stage_init_arm64"))
        if p not in paths:
            paths.append(p)
    return paths


def _save_first_stage_init_cache(blob: bytes, work_root: str = "") -> None:
    if not _is_static_arm64_elf(blob):
        return
    for dest in _cached_first_stage_init_paths(work_root):
        if os.path.isfile(dest):
            return
        try:
            parent = os.path.dirname(dest)
            if _free_bytes(parent) < 10 * 1024 * 1024:
                continue
            os.makedirs(parent, exist_ok=True)
            tmp = dest + ".tmp"
            with open(tmp, "wb") as fh:
                fh.write(blob)
            os.replace(tmp, dest)
            return
        except OSError:
            continue


def _resolve_first_stage_init(target_or_layout, *, work_root: str = "", on_progress=None) -> tuple[bytes, str]:
    """Extract real static ARM64 first-stage /init from the ROM's init_boot/boot image, or fall back to cached copy."""
    candidate_files: list[str] = []
    zip_path = ""

    if hasattr(target_or_layout, "fs_images"):
        for attr in ("init_boot_image", "boot_image"):
            p = getattr(target_or_layout, attr, "") or ""
            if p and os.path.isfile(p):
                candidate_files.append(p)
        raw_boot_imgs = getattr(target_or_layout, "boot_images", None)
        boot_img_paths: list[str] = []
        if isinstance(raw_boot_imgs, dict):
            boot_img_paths = [v for v in raw_boot_imgs.values() if isinstance(v, str)]
        elif isinstance(raw_boot_imgs, (list, tuple, set)):
            for item in raw_boot_imgs:
                if isinstance(item, str):
                    boot_img_paths.append(item)
                elif isinstance(item, (list, tuple)) and len(item) >= 2 and isinstance(item[1], str):
                    boot_img_paths.append(item[1])
        for p in boot_img_paths:
            if os.path.isfile(p) and p not in candidate_files:
                candidate_files.append(p)
        zip_path = getattr(target_or_layout, "zip_path", "") or ""
        root_dir = getattr(target_or_layout, "port_dir", "") or getattr(target_or_layout, "root", "") or ""
        if root_dir and os.path.isdir(root_dir):
            for nm in ("init_boot.img", "init_boot_a.img", "boot.img", "boot_a.img"):
                p = os.path.join(root_dir, nm)
                if os.path.isfile(p) and p not in candidate_files:
                    candidate_files.append(p)
    else:
        target = str(target_or_layout or "").strip()
        if os.path.isdir(target):
            for nm in ("init_boot.img", "init_boot_a.img", "boot.img", "boot_a.img"):
                p = os.path.join(target, nm)
                if os.path.isfile(p):
                    candidate_files.append(p)
        elif os.path.isfile(target) and target.lower().endswith(".zip"):
            zip_path = target

    # 1) Try boot / init_boot files on disk (prefer init_boot over boot)
    candidate_files.sort(key=lambda p: (0 if "init_boot" in os.path.basename(p).lower() else 1, p))
    for bf in candidate_files:
        blob = _extract_static_init_from_boot_file(bf)
        if blob:
            _save_first_stage_init_cache(blob, work_root)
            return blob, os.path.basename(bf)

    # 2) Try init_boot.img / boot.img directly inside firmware .zip
    if zip_path and os.path.isfile(zip_path):
        try:
            from core import native_backend
            entries = native_backend.vdev_probe_zip(zip_path) or []
            boot_ents = [
                e["name"]
                for e in entries
                if os.path.basename(e.get("name", "")).lower()
                in ("init_boot.img", "init_boot_a.img", "boot.img", "boot_a.img")
            ]
            boot_ents.sort(key=lambda n: (0 if "init_boot" in os.path.basename(n).lower() else 1, n))
            for ent_name in boot_ents:
                raw = native_backend.vdev_read_zip_entry_bytes(zip_path, ent_name)
                if raw:
                    blob = _extract_static_init_from_boot_bytes(bytes(raw))
                    if blob:
                        _save_first_stage_init_cache(blob, work_root)
                        return blob, f"{os.path.basename(zip_path)}:{ent_name}"
        except Exception:
            pass

    # 3) Fall back to cached static first-stage init (for ROM folders without init_boot.img, e.g. OOS15/COS16)
    for cpath in _cached_first_stage_init_paths(work_root):
        if os.path.isfile(cpath):
            try:
                with open(cpath, "rb") as fh:
                    blob = fh.read()
                if _is_static_arm64_elf(blob):
                    return blob, f"cached:{os.path.basename(cpath)}"
            except OSError:
                pass

    # 4) Auto-provision official AOSP 15 ARM64 static /init via HTTP Range (1.76 MB ramdisk.img)
    blob = _fetch_aosp15_first_stage_init(work_root, on_progress=on_progress)
    if blob:
        return blob, "aosp15:ramdisk.img"

    return b"", ""


def _fetch_aosp15_first_stage_init(work_root: str = "", *, on_progress=None) -> bytes:
    """Download official AOSP 15 ARM64 ramdisk.img (~1.76 MB via HTTP Range) and extract static /init."""
    url = "https://dl.google.com/android/repository/sys-img/android/arm64-v8a-35_r02.zip"
    try:
        if on_progress:
            on_progress("Downloading official AOSP 15 static /init (1.8 MB via HTTP Range)…")
        req_head = urllib.request.Request(url, method="HEAD")
        with urllib.request.urlopen(req_head, timeout=20) as r:
            total = int(r.headers.get("Content-Length") or 0)
        if total < 1024:
            return b""
        tail_sz = min(65536, total)
        req_tail = urllib.request.Request(url, headers={"Range": f"bytes={total - tail_sz}-{total - 1}"})
        with urllib.request.urlopen(req_tail, timeout=25) as r:
            tail = r.read()
        eocd = tail.rfind(b"PK\x05\x06")
        if eocd < 0:
            return b""
        _, _, _, _, _, cd_size, cd_off, _ = struct.unpack_from("<IHHHHIIH", tail, eocd)
        req_cd = urllib.request.Request(url, headers={"Range": f"bytes={cd_off}-{cd_off + cd_size - 1}"})
        with urllib.request.urlopen(req_cd, timeout=25) as r:
            cd = r.read()
        pos = 0
        ent = None
        while pos + 46 <= len(cd) and cd[pos : pos + 4] == b"PK\x01\x02":
            _, _, _, _, method, _, _, _, csize, usize, nlen, elen, clen, _, _, _, loff = struct.unpack_from(
                "<IHHHHHHIIIHHHHHII", cd, pos
            )
            name = cd[pos + 46 : pos + 46 + nlen].decode("utf-8", "replace")
            if name.endswith("/ramdisk.img") or name == "ramdisk.img":
                ent = (method, csize, loff)
                break
            pos += 46 + nlen + elen + clen
        if not ent:
            return b""
        method, csize, loff = ent
        req_lh = urllib.request.Request(url, headers={"Range": f"bytes={loff}-{loff + 29}"})
        with urllib.request.urlopen(req_lh, timeout=20) as r:
            lh = r.read()
        _, _, _, _, _, _, _, _, _, nlen, elen = struct.unpack_from("<IHHHHHIIIHH", lh, 0)
        dstart = loff + 30 + nlen + elen
        req_d = urllib.request.Request(url, headers={"Range": f"bytes={dstart}-{dstart + csize - 1}"})
        with urllib.request.urlopen(req_d, timeout=60) as r:
            comp = r.read()
        import zlib
        raw = zlib.decompress(comp, -zlib.MAX_WBITS) if method == 8 else comp
        cpio = b""
        if raw[:4] == b"\x02\x21\x4c\x18":
            import lz4.block
            out = bytearray()
            p = 4
            while p + 4 <= len(raw):
                bsz = struct.unpack_from("<I", raw, p)[0]
                p += 4
                if bsz == 0 or p + bsz > len(raw):
                    break
                out.extend(lz4.block.decompress(raw[p : p + bsz], uncompressed_size=8 * 1024 * 1024))
                p += bsz
            cpio = bytes(out)
        elif raw[:2] == b"\x1f\x8b":
            cpio = gzip.decompress(raw)
        if not cpio:
            return b""
        p = 0
        while p + 110 <= len(cpio) and cpio[p : p + 6] == b"070701":
            fsz = int(cpio[p + 54 : p + 62], 16)
            nsz = int(cpio[p + 94 : p + 102], 16)
            nm = cpio[p + 110 : p + 110 + nsz - 1].decode("utf-8", "replace")
            ds = ((p + 110 + nsz + 3) // 4) * 4
            if nm == "init" and fsz > 0:
                blob = cpio[ds : ds + fsz]
                if _is_static_arm64_elf(blob):
                    _save_first_stage_init_cache(blob, work_root)
                    return blob
                break
            if nm == "TRAILER!!!":
                break
            p = ((ds + fsz + 3) // 4) * 4
    except Exception:
        pass
    return b""


def find_qemu_virtio_gfx_image(work_root: str = "") -> str:
    """Locate cached Generic VirtIO Graphics Bridge EXT4 image (composer3 DRM + minigbm + Mesa/SwiftShader)."""
    cands: list[str] = []
    if work_root:
        cands.append(os.path.join(work_root, "kernels", "rak_virtio_gfx_arm64.img"))
    cands.append(os.path.join(BIN_DIR, "qemu", "kernels", "rak_virtio_gfx_arm64.img"))
    cands.append(os.path.join(CACHE_DIR, "vdev_qemu", "kernels", "rak_virtio_gfx_arm64.img"))
    for drv in _list_windows_fixed_drives():
        cands.append(os.path.join(drv, ".rak_qemu_cache", "kernels", "rak_virtio_gfx_arm64.img"))
    for p in cands:
        np = os.path.normpath(p)
        if os.path.isfile(np) and os.path.getsize(np) > 1024 * 1024:
            return np
    return ""


# ── Partition image discovery, auto-extraction (.zip / super.img), and auto-unsparsing ──

def _cache_key_for_file(path: str) -> str:
    st = os.stat(path)
    raw = f"{os.path.abspath(path).lower()}|{st.st_size}|{int(st.st_mtime)}".encode("utf-8")
    digest = hashlib.sha1(raw).hexdigest()[:12]
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", os.path.basename(path))[:40]
    return f"{stem}_{digest}"


def _auto_unsparse_if_needed(src_path: str, *, work_root: str = "", on_progress=None) -> str:
    """If src_path is an Android sparse image, materialize a cached raw copy without touching src_path."""
    if not _is_android_sparse(src_path):
        return src_path
    try:
        base_root = work_root or _pick_qemu_work_root(src_path)
        out_dir = os.path.join(base_root, "raw_cache")
        os.makedirs(out_dir, exist_ok=True)
        raw_out = os.path.normpath(os.path.join(out_dir, _cache_key_for_file(src_path) + ".raw.img"))
        if os.path.isfile(raw_out) and os.path.getsize(raw_out) > 4096 and not _is_android_sparse(raw_out):
            return raw_out
        from core import native_backend
        fn = getattr(native_backend, "sparse_simg2img", None) or getattr(native_backend, "simg2img", None)
        if fn is not None:
            if on_progress:
                on_progress(f"Unsparsing {os.path.basename(src_path)} for QEMU virtio-blk…")
            tmp = raw_out + ".tmp"
            fn(src_path, tmp)
            os.replace(tmp, raw_out)
            return raw_out
    except Exception:
        pass
    return src_path


def _scan_dir_for_partitions(folder: str) -> tuple[dict[str, str], str]:
    imgs: dict[str, str] = {}
    super_path = ""
    if not os.path.isdir(folder):
        return imgs, super_path

    img_files = [
        fn for fn in sorted(os.listdir(folder))
        if fn.lower().endswith(".img") and os.path.isfile(os.path.join(folder, fn))
    ]
    # First pass: exact matches (including _a / _b suffix)
    for fn in img_files:
        full = os.path.normpath(os.path.join(folder, fn))
        low = fn.lower()
        if low == "super.img":
            super_path = full
            continue
        stem = os.path.splitext(low)[0]
        for suffix in ("_a", "_b"):
            if stem.endswith(suffix):
                stem = stem[:-2]
        if _is_mountable_partition_name(stem) and stem not in imgs:
            imgs[stem] = full

    # Second pass: catch custom-named partition images like `system_ext_hehe.img` when exact name absent
    for fn in img_files:
        low = fn.lower()
        stem = os.path.splitext(low)[0]
        for part in ("system_ext", "vendor_dlkm", "system_dlkm", "odm_dlkm", "product", "vendor", "system", "odm"):
            if part not in imgs and stem.startswith(part + "_") and not stem.endswith(("_vbmeta", "_dlkm")):
                imgs[part] = os.path.normpath(os.path.join(folder, fn))
                break

    return imgs, super_path


def _extract_super_partitions(super_path: str, work_root: str, *, on_progress=None) -> dict[str, str]:
    """Extract logical partitions from super.img into a reusable free-space cache directory."""
    if not super_path or not os.path.isfile(super_path):
        return {}
    out_dir = os.path.normpath(os.path.join(work_root, "super_cache", _cache_key_for_file(super_path)))
    existing_imgs, _ = _scan_dir_for_partitions(out_dir)
    if "system" in existing_imgs:
        return existing_imgs

    os.makedirs(out_dir, exist_ok=True)
    try:
        from core import native_backend
        from core.vdev.mounts import run_with_dir_progress
        # Inspect available logical partitions so we only extract what QEMU needs
        want_parts = None
        total_bytes = 0
        try:
            info = native_backend.vdev_probe_super(super_path, slot=0) or {}
            plist = info.get("partitions") or []
            matched = []
            for p in plist:
                pname = p.get("name", "") if isinstance(p, dict) else str(p)
                base = pname[:-2] if pname.endswith(("_a", "_b")) else pname
                if _is_mountable_partition_name(base):
                    matched.append(pname)
                    if isinstance(p, dict):
                        total_bytes += int(p.get("size") or p.get("size_bytes") or 0)
            if matched:
                want_parts = matched
        except Exception:
            pass

        run_with_dir_progress(
            lambda: native_backend.super_extract(
                super_path,
                out_dir,
                partitions=want_parts,
                slot=0,
                normalize=True,
            ),
            out_dir,
            total_bytes=total_bytes or os.path.getsize(super_path),
            total_files=len(want_parts) if want_parts else 6,
            label=f"Extracting {os.path.basename(super_path)}",
            on_progress=on_progress,
        )
        extracted, _ = _scan_dir_for_partitions(out_dir)
        return extracted
    except Exception:
        return {}


def _collect_partition_images(
    target_or_layout,
    *,
    work_root: str = "",
    on_progress=None,
) -> tuple[dict[str, str], str]:
    """Return ({part_name: img_path}, super_path) from a Layout, directory, super.img, or firmware .zip."""
    imgs: dict[str, str] = {}
    super_path = ""
    wroot = work_root or _pick_qemu_work_root(
        target_or_layout
        if isinstance(target_or_layout, str)
        else (getattr(target_or_layout, "port_dir", "") or getattr(target_or_layout, "root", "") or getattr(target_or_layout, "zip_path", ""))
    )

    if hasattr(target_or_layout, "fs_images"):
        super_path = (
            getattr(target_or_layout, "super_path", "")
            or getattr(target_or_layout, "super_image", "")
            or ""
        )
        if super_path and not os.path.isfile(super_path):
            super_path = ""
        for k, v in (getattr(target_or_layout, "fs_images", None) or {}).items():
            if isinstance(v, str) and os.path.isfile(v):
                imgs[k] = os.path.normpath(v)
        zip_path = getattr(target_or_layout, "zip_path", "") or ""
        if "system" not in imgs and not super_path and zip_path and os.path.isfile(zip_path):
            target_or_layout = zip_path
        else:
            if "system" not in imgs and super_path:
                for k, v in _extract_super_partitions(super_path, wroot, on_progress=on_progress).items():
                    imgs.setdefault(k, v)
            return imgs, os.path.normpath(super_path) if super_path else ""

    target = str(target_or_layout or "").strip()
    if not target:
        return imgs, ""

    if os.path.isfile(target) and target.lower().endswith(".zip"):
        try:
            from core import native_backend
            zcache = os.path.normpath(os.path.join(wroot, "zip_cache", _cache_key_for_file(target)))
            if os.path.isdir(zcache):
                for root_d, _dirs, _files in os.walk(zcache):
                    d_imgs, d_sup = _scan_dir_for_partitions(root_d)
                    for k, v in d_imgs.items():
                        imgs.setdefault(k, v)
                    if d_sup and not super_path:
                        super_path = d_sup
            if "system" not in imgs and not super_path:
                entries = native_backend.vdev_probe_zip(target) or []
                part_targets = {f"{p}.img" for p in _KNOWN_PARTITIONS} | {f"{p}_a.img" for p in _KNOWN_PARTITIONS}
                # Pick at most ONE entry per basename (prefer shallowest path, then largest size)
                # so parallel extraction never collides (e.g. Open/super.img vs root super.img)
                best_by_bn: dict[str, tuple[int, int, str]] = {}
                best_super: tuple[int, int, str] | None = None
                has_system_part = False
                for ent in entries:
                    nm = ent.get("name", "")
                    bn = os.path.basename(nm).lower()
                    depth = nm.count("/") + nm.count("\\")
                    sz = int(ent.get("size") or 0)
                    rank = (-depth, sz, nm)
                    if bn in part_targets:
                        if bn not in best_by_bn or rank > best_by_bn[bn]:
                            best_by_bn[bn] = rank
                        if bn in ("system.img", "system_a.img"):
                            has_system_part = True
                    elif bn == "super.img":
                        if best_super is None or rank > best_super:
                            best_super = rank
                wanted = [v[2] for v in best_by_bn.values()]
                wanted_bytes = sum(v[1] for v in best_by_bn.values())
                if not has_system_part and best_super is not None:
                    wanted.append(best_super[2])
                    wanted_bytes += best_super[1]
                if wanted:
                    from core.vdev.mounts import run_with_dir_progress
                    os.makedirs(zcache, exist_ok=True)
                    run_with_dir_progress(
                        lambda: native_backend.vdev_prepare_zip_images(target, wanted, zcache),
                        zcache,
                        total_bytes=wanted_bytes,
                        total_files=len(wanted),
                        label=f"Extracting {os.path.basename(target)}",
                        on_progress=on_progress,
                    )
                    for root_d, _dirs, _files in os.walk(zcache):
                        d_imgs, d_sup = _scan_dir_for_partitions(root_d)
                        for k, v in d_imgs.items():
                            imgs.setdefault(k, v)
                        if d_sup and not super_path:
                            super_path = d_sup
        except Exception:
            pass
        if "system" not in imgs and super_path:
            for k, v in _extract_super_partitions(super_path, wroot, on_progress=on_progress).items():
                imgs.setdefault(k, v)
        return imgs, super_path

    if os.path.isfile(target):
        low = os.path.basename(target).lower()
        if "super" in low and low.endswith(".img"):
            super_path = os.path.normpath(target)
            imgs = _extract_super_partitions(super_path, wroot, on_progress=on_progress)
            return imgs, super_path
        if low.endswith(".img"):
            stem = os.path.splitext(low)[0]
            imgs[stem] = os.path.normpath(target)
        return imgs, ""

    if os.path.isdir(target):
        imgs, super_path = _scan_dir_for_partitions(target)
        if "system" not in imgs and super_path:
            for k, v in _extract_super_partitions(super_path, wroot, on_progress=on_progress).items():
                imgs.setdefault(k, v)
    return imgs, super_path


def _create_ext4_session_disk(
    work_root: str,
    work_dir: str,
    name: str,
    size_mb: int,
    mount_point: str,
) -> str:
    """Create an ephemeral valid EXT4 disk for the session (reusing a fast cached blank template)."""
    tmpl_dir = os.path.join(work_root, "templates")
    os.makedirs(tmpl_dir, exist_ok=True)
    tmpl_path = os.path.join(tmpl_dir, f"{name}_{size_mb}m.ext4.img")
    dest_path = os.path.normpath(os.path.join(work_dir, f"{name}.img"))

    if not (os.path.isfile(tmpl_path) and os.path.getsize(tmpl_path) >= size_mb * 1024 * 1024):
        empty_src = os.path.join(tmpl_dir, "_empty_dir")
        os.makedirs(empty_src, exist_ok=True)
        tmp = tmpl_path + ".tmp"
        made = False
        try:
            from core import native_backend
            if hasattr(native_backend, "ext_make_fs"):
                native_backend.ext_make_fs(
                    tmp,
                    empty_src,
                    size_bytes=size_mb * 1024 * 1024,
                    mount_point=mount_point,
                    sparse=False,
                    label=name,
                )
                os.replace(tmp, tmpl_path)
                made = True
        except Exception:
            try:
                if os.path.isfile(tmp):
                    os.remove(tmp)
            except OSError:
                pass
        if not made:
            with open(tmpl_path, "wb") as fh:
                fh.truncate(size_mb * 1024 * 1024)

    shutil.copy2(tmpl_path, dest_path)
    return dest_path


def build_hardware_gate(
    report=None,
    *,
    disks: list[dict] | None = None,
    kernel_config: dict | None = None,
    kernel_hw: dict | None = None,
    hardware_audit: dict | None = None,
) -> dict:
    """Derive a Kernel+DTBO Hardware Contract AND separate Vendor Verification gate.

    Strict separation of concerns (matching real Android hardware):
    - Virtual hardware peripherals (Display, Touch, Audio) obey ONLY the ROM's Kernel + DTBO,
      NEVER /vendor. A broken /vendor must never disable hardware or be masked by fake shims.
    - Real kernel filesystem support (IKCFG) is enforced strictly: if the real kernel lacks
      CONFIG_EROFS_FS/EXT4/F2FS for a ROM partition, QEMU refuses to silently mount it.
    - Vendor faults (KMI .ko mismatch, broken vendor HAL/VINTF/linker/SELinux blockers) are
      evaluated separately and mark `bootBlockedByVendor=True` so QEMU NEVER reports a false
      "boot success" when /vendor is broken!
    """
    stages = list(getattr(report, "stages", None) or ())
    all_findings = []
    for st in stages:
        st_key = str(getattr(st, "key", "") or "").lower()
        for f in (getattr(st, "findings", None) or ()):
            all_findings.append((
                st_key,
                str(getattr(f, "severity", "") or "").lower(),
                str(getattr(f, "code", "") or "").lower(),
                str(getattr(f, "title", "") or ""),
                str(getattr(f, "detail", "") or ""),
                str(getattr(f, "path", "") or "").lower(),
            ))

    kcfg = dict(kernel_config or {})
    khw = dict(kernel_hw or {})
    hw = dict(hardware_audit or {})
    hw_subs = hw.get("subsystems") or {}
    has_report = report is not None or bool(kcfg) or bool(khw) or bool(hw_subs)

    def _match_findings(keywords: tuple[str, ...], *, stages_filter: tuple[str, ...] | None = None):
        blockers, warns = [], []
        for st_key, sev, code, title, detail, path in all_findings:
            if stages_filter and st_key not in stages_filter:
                continue
            blob = f"{code} {title} {detail} {path}".lower()
            if any(k in blob for k in keywords):
                if sev == "blocker":
                    blockers.append(title or code)
                elif sev == "warn":
                    warns.append(title or code)
        return blockers, warns

    # ── 1) KERNEL FILESYSTEM CONTRACT (strictly from real kernel IKCFG — enforced in fstab.qemu!) ──
    fs_status = "generic"
    fs_reason = "No kernel IKCFG parsed; using generic VirtIO EROFS/EXT4 support"
    fs_flags = kcfg.get("flags") or {}
    unsupported_fs: list[str] = []
    if fs_flags:
        needed_fs = {
            str(d.get("fsType") or "").lower()
            for d in (disks or ())
            if d.get("readonly", True) and d.get("fsType") in ("erofs", "ext4", "f2fs")
        }
        missing_fs = []
        for ft, cfg_key in (("erofs", "CONFIG_EROFS_FS"), ("ext4", "CONFIG_EXT4_FS"), ("f2fs", "CONFIG_F2FS_FS")):
            if ft in needed_fs:
                val = str(fs_flags.get(cfg_key) or "").lower()
                if val in ("n", "no", "not set", ""):
                    missing_fs.append(f"{cfg_key} disabled in real kernel for {ft}")
                    unsupported_fs.append(ft)
        if missing_fs:
            fs_status = "fault"
            fs_reason = "; ".join(missing_fs)
        else:
            fs_status = "pass"
            fs_reason = f"Real kernel IKCFG supports {', '.join(sorted(needed_fs)) or 'ROM filesystems'}"
    fs_blockers, fs_warns = _match_findings(("ikcfg", "kernel-fs", "erofs-unsupported"), stages_filter=("kernel",))
    if fs_blockers:
        fs_status = "fault"
        fs_reason = fs_blockers[0]
    elif fs_warns and fs_status != "fault":
        fs_status = "warn"
        fs_reason = fs_warns[0]

    # ── 2) KERNEL+DTBO DISPLAY / TOUCH / AUDIO HARDWARE (obeys ONLY kernel+DTBO, NEVER /vendor!) ──
    disp_status = "pass" if has_report else "generic"
    disp_reason = "Kernel/DTBO display bus present → generic virtio-gpu active"
    tc_status = "pass" if has_report else "generic"
    tc_reason = "Kernel/DTBO input bus present → generic virtio-tablet active"
    aud_status = "pass" if has_report else "generic"
    aud_reason = "Kernel/DTBO audio bus present → generic virtio-sound active"

    # Only a KERNEL-level config/DTBO absence (never a /vendor HAL bug) affects kernel hardware readiness
    if fs_flags:
        if str(fs_flags.get("CONFIG_DRM") or "").lower() in ("n", "no") and str(fs_flags.get("CONFIG_FB") or "").lower() in ("n", "no"):
            disp_status = "fault"
            disp_reason = "Real kernel has neither CONFIG_DRM nor CONFIG_FB enabled"
        if str(fs_flags.get("CONFIG_INPUT") or "").lower() in ("n", "no"):
            tc_status = "fault"
            tc_reason = "Real kernel has CONFIG_INPUT disabled"
        if str(fs_flags.get("CONFIG_SOUND") or "").lower() in ("n", "no"):
            aud_status = "fault"
            aud_reason = "Real kernel has CONFIG_SOUND disabled"

    # ── 3) VENDOR vs KERNEL/SYSTEM AUDIT (tested AGAINST the kernel — causes real panic/boot halt if broken!) ──
    vendor_blockers: list[str] = []
    vendor_warns: list[str] = []
    kmi_faults: list[str] = []
    selinux_faults: list[str] = []
    hal_faults: list[str] = []
    for st_key, sev, code, title, detail, path in all_findings:
        blob = f"{code} {title} {path}".lower()
        is_kmi = any(k in blob for k in ("kmi", "module_layout", "ko-crc", "ko-symbol", "ko-missing-fw", "ko-dep", "vermagic"))
        is_selinux = st_key == "selinux" or "selinux" in code or "sepolicy" in code
        is_vendor_scope = (
            "/vendor" in path
            or "/odm" in path
            or "vendor" in code
            or is_kmi
            or "hwcomposer" in blob
            or "gralloc" in blob
            or "audio_policy" in blob
            or st_key in ("hardware", "vintf")
        )
        if not is_vendor_scope:
            continue
        msg = title or code
        if sev == "blocker":
            vendor_blockers.append(msg)
            if is_kmi:
                kmi_faults.append(msg)
            elif is_selinux:
                selinux_faults.append(msg)
            else:
                hal_faults.append(msg)
        elif sev == "warn":
            vendor_warns.append(msg)

    for sub_k, sub_info in hw_subs.items():
        if isinstance(sub_info, dict):
            sst = str(sub_info.get("status", "")).lower()
            if sst in ("blocker", "error", "fail"):
                msg = str(sub_info.get("detail") or f"Vendor {sub_k} fault")
                vendor_blockers.append(msg)
                hal_faults.append(msg)

    # Deduplicate while preserving order
    vendor_blockers = list(dict.fromkeys(vendor_blockers))
    vendor_warns = list(dict.fromkeys(vendor_warns))
    kmi_faults = list(dict.fromkeys(kmi_faults))
    selinux_faults = list(dict.fromkeys(selinux_faults))
    hal_faults = list(dict.fromkeys(hal_faults))

    if vendor_blockers:
        vnd_status = "fault"
        vnd_reason = f"{len(vendor_blockers)} vendor blocker(s): {vendor_blockers[0]}"
    elif vendor_warns:
        vnd_status = "warn"
        vnd_reason = f"{len(vendor_warns)} vendor warning(s): {vendor_warns[0]}"
    elif has_report:
        vnd_status = "pass"
        vnd_reason = "Vendor HALs, KMI .ko & VINTF match kernel/system"
    else:
        vnd_status = "generic"
        vnd_reason = "Run Boot first to validate /vendor against kernel"

    boot_blocked = bool(vendor_blockers) or fs_status == "fault"
    # Determine the exact boot stage where a real device fails to boot:
    # - kmi_faults  -> real kernel panic in first-stage LoadKernelModules (handled inside guest initramfs)
    # - fs_status   -> real kernel panic in first-stage mount (handled inside guest fstab.qemu)
    # - selinux     -> halt at selinux stage
    # - hal/vintf   -> halt right after fs_mounted/vendor init (cannot proceed to normal boot!)
    if kmi_faults:
        halt_stage = "init_first"
    elif fs_status == "fault":
        halt_stage = "fs_mounted"
    elif selinux_faults:
        halt_stage = "selinux"
    elif hal_faults:
        halt_stage = "fs_mounted"
    else:
        halt_stage = ""

    subsystems = [
        {"key": "filesystem", "label": "Kernel FS", "shortLabel": "FS", "status": fs_status, "enabled": fs_status != "fault", "reason": fs_reason},
        {"key": "display", "label": "Kernel Display", "shortLabel": "DISP", "status": disp_status, "enabled": disp_status != "fault", "reason": disp_reason},
        {"key": "touch", "label": "Kernel Touch", "shortLabel": "TC", "status": tc_status, "enabled": tc_status != "fault", "reason": tc_reason},
        {"key": "audio", "label": "Kernel Audio", "shortLabel": "AUD", "status": aud_status, "enabled": aud_status != "fault", "reason": aud_reason},
        {"key": "vendor", "label": "Vendor vs Kernel", "shortLabel": "VND", "status": vnd_status, "enabled": not bool(vendor_blockers), "reason": vnd_reason},
    ]
    return {
        "hasReport": has_report,
        "faultCount": sum(1 for s in subsystems if s["status"] == "fault"),
        "warnCount": sum(1 for s in subsystems if s["status"] == "warn"),
        "unsupportedFsTypes": unsupported_fs,
        "kmiFaults": kmi_faults,
        "selinuxFaults": selinux_faults,
        "halFaults": hal_faults,
        "vendorFaults": vendor_blockers,
        "bootBlockedByVendor": boot_blocked,
        "haltStage": halt_stage,
        "subsystems": subsystems,
        "byKey": {s["key"]: s for s in subsystems},
    }


@dataclass
class QemuBootPlan:
    ok: bool
    error: str = ""
    backend: str = "none"
    binary: str = ""
    emulator_root: str = ""
    abi_mode: str = "arm64_32"
    kernel_path: str = ""
    ramdisk_path: str = ""
    work_dir: str = ""
    disks: list[dict] = field(default_factory=list)
    cmdline: str = ""
    command: list[str] = field(default_factory=list)
    env_extra: dict[str, str] = field(default_factory=dict)
    vnc_port: int = 0
    warnings: list[str] = field(default_factory=list)
    license: str = QEMU_LICENSE
    init_source: str = ""
    virtio_modules_dir: str = ""
    hardware_gate: dict = field(default_factory=dict)
    virtio_gfx_bridge: bool = False

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "error": self.error,
            "backend": self.backend,
            "binary": self.binary,
            "abiMode": self.abi_mode,
            "kernelPath": self.kernel_path,
            "ramdiskPath": self.ramdisk_path,
            "workDir": self.work_dir,
            "disks": list(self.disks),
            "cmdline": self.cmdline,
            "command": list(self.command),
            "vncPort": self.vnc_port,
            "warnings": list(self.warnings),
            "license": self.license,
            "initSource": self.init_source,
            "virtioModulesDir": self.virtio_modules_dir,
            "hardwareGate": dict(self.hardware_gate),
            "virtioGfxBridge": self.virtio_gfx_bridge,
        }


def prepare_qemu_plan(
    target_or_layout,
    *,
    profile: dict | None = None,
    abi: str = "auto",
    kernel_path: str = "",
    ram_mb: int = 3072,
    cpus: int = 4,
    headless: bool = True,
    embedded_display: bool = True,
    permissive: bool = True,
    avb_disabled: bool = True,
    timeout_sec: int = 120,
    auto_download_kernel: bool = False,
    on_progress=None,
    backend_override: str = "",
    report=None,
    hardware_gate: dict | None = None,
    kernel_config: dict | None = None,
    kernel_hw: dict | None = None,
    hardware_audit: dict | None = None,
) -> QemuBootPlan:
    """Build a non-destructive QEMU ARM64 / ARM32 / 64_32 boot plan with full automatic provisioning."""
    hint_path = (
        target_or_layout
        if isinstance(target_or_layout, str)
        else (getattr(target_or_layout, "port_dir", "") or getattr(target_or_layout, "root", "") or getattr(target_or_layout, "zip_path", ""))
    )
    work_root = _pick_qemu_work_root(hint_path)
    _prune_old_sessions(work_root, keep=0)

    env = detect_qemu_environment()
    abi_mode = resolve_qemu_abi(profile, abi)
    warnings: list[str] = []

    if backend_override in ("upstream", "android-qemu"):
        backend = backend_override
    else:
        backend = env["backend"]

    # If only Android SDK AVD wrapper (or nothing) is present and auto-provisioning is enabled,
    # auto-provision portable Upstream QEMU which genuinely boots standalone Android ROMs.
    if auto_download_kernel and backend != "upstream" and backend_override != "android-qemu":
        ok_bin, bin_res = ensure_qemu_binary(on_progress=on_progress, hint_path=hint_path)
        if ok_bin:
            env = detect_qemu_environment()
            backend = "upstream"
        elif backend == "none":
            return QemuBootPlan(ok=False, error=bin_res, backend="none", abi_mode=abi_mode)

    if backend == "upstream":
        binary = env["upstreamArm32"] if abi_mode == "arm32" and env["upstreamArm32"] else env["upstreamArm64"]
        if not binary and abi_mode == "arm32":
            binary = env["upstreamArm32"]
    elif backend == "android-qemu":
        binary = env["androidSdkArm32"] if abi_mode == "arm32" and env["androidSdkArm32"] else env["androidSdkArm64"]
        if not binary:
            binary = env["androidSdkArm64"] or env["androidSdkArm32"]
    else:
        binary = ""

    if not binary:
        return QemuBootPlan(
            ok=False,
            error=(
                "No QEMU ARM binary found. Click Play QEMU to auto-download portable Upstream QEMU, "
                f"or install `qemu-system-aarch64` into `{os.path.join(BIN_DIR, 'qemu')}` / PATH."
            ),
            backend="none",
            abi_mode=abi_mode,
        )

    kpath = os.path.normpath(kernel_path) if kernel_path and os.path.isfile(kernel_path) else ""
    if not kpath:
        kpath = env["arm32Kernel"] if abi_mode == "arm32" and env["arm32Kernel"] else env["arm64Kernel"]
    if not kpath and auto_download_kernel:
        ok_dl, dl_res = ensure_qemu_kernel(abi_mode, on_progress=on_progress, work_root=work_root)
        if ok_dl:
            kpath = dl_res
        else:
            return QemuBootPlan(
                ok=False,
                error=dl_res,
                backend=backend,
                binary=binary,
                emulator_root=env["emulatorRoot"],
                abi_mode=abi_mode,
            )
    if not kpath:
        return QemuBootPlan(
            ok=False,
            error=(
                f"No VirtIO/GKI kernel found for {abi_mode}. Click Play QEMU to auto-download the official "
                f"AOSP ARM64 6.6 VirtIO kernel, or place one in `{env['kernelDir']}`."
            ),
            backend=backend,
            binary=binary,
            emulator_root=env["emulatorRoot"],
            abi_mode=abi_mode,
        )

    # Ensure VirtIO .ko modules are present when auto-provisioning
    modules_dir = find_qemu_virtio_modules_dir()
    if not modules_dir and auto_download_kernel:
        ok_mod, mod_res = ensure_qemu_virtio_modules(on_progress=on_progress, work_root=work_root)
        if ok_mod:
            modules_dir = mod_res
        else:
            warnings.append(f"VirtIO .ko modules unavailable ({mod_res}); virtio-blk/gpu may not probe.")

    # Upstream ARM64 QEMU requires an uncompressed raw ARM64 Image
    if backend == "upstream" and abi_mode != "arm32":
        kpath = _ensure_uncompressed_arm64_kernel(kpath, work_root, on_progress=on_progress)

    imgs, super_path = _collect_partition_images(target_or_layout, work_root=work_root, on_progress=on_progress)
    if not imgs and not super_path:
        return QemuBootPlan(
            ok=False,
            error=(
                "No partition images (.img / super.img / firmware .zip) staged for QEMU. "
                "Stage a ROM .zip, super.img, or partition images first."
            ),
            backend=backend,
            binary=binary,
            emulator_root=env["emulatorRoot"],
            abi_mode=abi_mode,
            kernel_path=kpath,
        )

    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    work_dir = os.path.normpath(os.path.join(work_root, f"sess_{stamp}"))
    os.makedirs(work_dir, exist_ok=True)

    # Extract real static ARM64 first-stage /init from ROM's init_boot/boot or cached copy
    if on_progress:
        on_progress("Preparing Android first-stage /init & VirtIO initramfs…")
    fs_init_bytes, init_src = _resolve_first_stage_init(
        target_or_layout,
        work_root=work_root,
        on_progress=on_progress,
    )
    if not fs_init_bytes:
        warnings.append(
            "Static first-stage /init not found in ROM init_boot/boot or cache; "
            "kernel will boot but userspace init requires a ROM with init_boot.img or boot.img."
        )

    # Order disks deterministically: system -> vendor -> product -> system_ext -> odm -> dlkms -> mi_ext -> metadata -> data
    disks: list[dict] = []
    fstab_parts: list[tuple[str, str]] = []

    if imgs:
        # Core partitions first in deterministic order, then any extra OEM partitions discovered
        ordered_names = [p for p in _KNOWN_PARTITIONS if p in imgs]
        for p in sorted(imgs.keys()):
            if p not in ordered_names and p not in ("metadata", "userdata", "data"):
                ordered_names.append(p)
        for p in ordered_names:
            src = _auto_unsparse_if_needed(imgs[p], work_root=work_root, on_progress=on_progress)
            if _is_android_sparse(src):
                warnings.append(f"{os.path.basename(src)} is Android sparse; unsparse failed.")
            fs_t = _detect_fs_type(src)
            disks.append({"partition": p, "path": src, "fsType": fs_t, "readonly": True})
            fstab_parts.append((p, fs_t))
            # Place ephemeral 2 MiB raw /misc right after system (deterministically /dev/block/vdb)
            # so android.hardware.boot-service & update_verifier find BCB via /vendor/etc/fstab.qemu.
            if p == "system":
                misc_path = os.path.normpath(os.path.join(work_dir, "misc.img"))
                with open(misc_path, "wb") as mfh:
                    mfh.truncate(2 * 1024 * 1024)
                disks.append({"partition": "misc", "path": misc_path, "fsType": "emmc", "readonly": False})
                fstab_parts.append(("misc", "emmc"))
    elif super_path:
        super_raw = _auto_unsparse_if_needed(super_path, work_root=work_root, on_progress=on_progress)
        if _is_android_sparse(super_raw):
            warnings.append("super.img is Android sparse; unsparse failed.")
        disks.append({"partition": "super", "path": super_raw, "fsType": "super", "readonly": True})
        fstab_parts.append(("system", "erofs"))

    # Ephemeral valid writable EXT4 /metadata (16 MiB) and /data (512 MiB)
    if on_progress:
        on_progress("Provisioning ephemeral writable EXT4 /metadata & /data disks…")
    metadata_path = _create_ext4_session_disk(work_root, work_dir, "metadata", 16, "/metadata")
    disks.append({"partition": "metadata", "path": metadata_path, "fsType": "ext4", "readonly": False})
    fstab_parts.append(("metadata", "ext4"))

    userdata_path = _create_ext4_session_disk(work_root, work_dir, "userdata", 512, "/data")
    disks.append({"partition": "userdata", "path": userdata_path, "fsType": "ext4", "readonly": False})
    fstab_parts.append(("userdata", "ext4"))

    # Evaluate Kernel-Contract Hardware Gate (QEMU obeys the ROM's real kernel + DTBO + vendor audit!)
    hw_gate = hardware_gate or build_hardware_gate(
        report=report,
        disks=disks,
        kernel_config=kernel_config,
        kernel_hw=kernel_hw,
        hardware_audit=hardware_audit,
    )
    gate_by_key = hw_gate.get("byKey") or {}
    disp_enabled = bool((gate_by_key.get("display") or {}).get("enabled", True))
    touch_enabled = bool((gate_by_key.get("touch") or {}).get("enabled", True))
    audio_enabled = bool((gate_by_key.get("audio") or {}).get("enabled", True))
    fs_enabled = bool((gate_by_key.get("filesystem") or {}).get("enabled", True))
    for sub in (hw_gate.get("subsystems") or ()):
        if sub.get("status") == "fault":
            warnings.append(f"[HW GATE FAULT · {sub.get('label')}] {sub.get('reason')}")

    # Generic VirtIO Graphics Bridge (STRICTLY GATED: ONLY attached when Kernel<->Vendor contract PASSES!)
    # If /vendor is broken or kernel lacks display/FS/KMI contract, bridge stays OFF so real panic/halt fires.
    gfx_img_path = find_qemu_virtio_gfx_image(work_root)
    existing_parts = {d["partition"] for d in disks}
    virtio_gfx_enabled = bool(
        backend == "upstream"
        and embedded_display
        and disp_enabled
        and abi_mode != "arm32"
        and not hw_gate.get("bootBlockedByVendor")
        and not hw_gate.get("kmiFaults")
        and not hw_gate.get("unsupportedFsTypes")
        and "vendor" in existing_parts
        and "oem" not in existing_parts
        and bool(gfx_img_path)
    )
    if virtio_gfx_enabled:
        disks.append({"partition": "oem", "path": gfx_img_path, "fsType": "ext4", "readonly": True})
        fstab_parts.append(("oem", "ext4"))
        vnd_disk_path = next((d["path"] for d in disks if d["partition"] == "vendor"), "")
        rom_mapper = detect_rom_mapper_suffix(vnd_disk_path)
        if rom_mapper and rom_mapper != "powervr":
            warnings.append(
                f"ROM vendor_service_contexts declares mapper/{rom_mapper} "
                f"(experimental VirtIO bridge defaults to mapper/powervr under SELinux Enforcing)."
            )

    # Synthesize complete bootable CPIO initramfs
    ramdisk_path = os.path.join(work_dir, "ramdisk-qemu-overlay.cpio.gz")
    overlay_bytes = build_qemu_overlay_cpio_gz(
        fstab_parts,
        abi_mode=abi_mode,
        headless=(headless and not embedded_display) or not disp_enabled,
        permissive=permissive,
        first_stage_init_bytes=fs_init_bytes,
        modules_dir=modules_dir,
        hardware_gate=hw_gate,
        virtio_gfx_overlay=virtio_gfx_enabled,
    )
    with open(ramdisk_path, "wb") as fh:
        fh.write(overlay_bytes)

    zyg_prop = {"arm64": "zygote64", "arm64_32": "zygote64_32", "arm32": "zygote32"}[abi_mode]
    cmdline_parts = [
        "console=ttyAMA0,115200",
        "earlycon=pl011,0x09000000",
        "androidboot.hardware=qemu",
        "androidboot.fstab_suffix=qemu",
        f"androidboot.zygote={zyg_prop}",
        "androidboot.selinux=permissive" if permissive else "androidboot.selinux=enforcing",
        # Keep kernel-level SELinux boot enforcing=0 (matches real Android bootloaders!):
        # Android init's SetupSelinux() runs while still in u:r:kernel:s0 and needs to finish
        # copying/relabeling /second_stage_resources (e.g. Transsion overlay_remounter) before
        # userspace init itself switches SELinux to enforcing per androidboot.selinux.
        "enforcing=0",
        "androidboot.veritymode=disabled" if avb_disabled else "androidboot.veritymode=enforcing",
        "androidboot.force_normal_boot=1",
        "androidboot.first_stage_console=1",
        "androidboot.boot_devices=4010000000.pcie",
        "printk.devkmsg=on",
        "loglevel=7",
        "init=/init",
    ]
    if not fs_enabled:
        cmdline_parts.append("androidboot.rak_fs_fault=1")
    if super_path and not imgs:
        cmdline_parts.append("androidboot.super_partition=vda")
    cmdline = " ".join(cmdline_parts)

    ram_clamped = max(512, min(int(ram_mb or 3072), 16384))
    host_cpus = os.cpu_count() or 4
    default_cpus = max(2, min(4, host_cpus // 2))
    cpu_clamped = max(1, min(int(cpus or default_cpus), max(2, host_cpus - 2)))
    env_extra: dict[str, str] = {}
    vnc_port = 0
    vnc_disp = 0
    if embedded_display and disp_enabled:
        vnc_port, vnc_disp = _pick_free_vnc_port()

    if backend == "upstream":
        is_arm32 = abi_mode == "arm32" and "aarch64" not in os.path.basename(binary).lower()
        # Use `-cpu max` on ARM64 so Android 14/15/16 ARMv8.2+/ARMv9 instructions (LSE/PAC/BTI) never SIGILL!
        if is_arm32:
            cpu_model = "cortex-a15"
        elif abi_mode == "arm32":
            # AOSP ARM64 GKI 6.6 runs 32-bit EL0 (zygote32) under an AArch64 kernel cleanly with `-cpu max`
            cpu_model = "max"
        else:
            cpu_model = "max"

        cmd = [
            binary,
            "-machine", "virt,gic-version=3" if not is_arm32 else "virt",
            "-cpu", cpu_model,
            "-accel", "tcg,thread=multi,tb-size=256",
            "-smp", str(cpu_clamped),
            "-m", str(ram_clamped),
            "-kernel", kpath,
            "-initrd", ramdisk_path,
            "-append", cmdline,
            "-monitor", "none",
            "-no-reboot",
        ]
        # Attach generic VirtIO peripherals STRICTLY governed by the real Kernel-Vendor Hardware Gate
        use_virgl = bool(embedded_display and disp_enabled and vnc_port and _qemu_supports_virgl(binary))
        if embedded_display and disp_enabled and vnc_port:
            if use_virgl:
                # 3D VirGL hardware-accelerated VirtIO-GPU via host egl-headless -> VNC:
                # offloads guest OpenGL ES shaders to host GPU (zero guest LLVM JIT / zero SELinux execmem!).
                cmd += [
                    "-device", "virtio-gpu-gl-pci,xres=540,yres=960",
                    "-display", "egl-headless",
                    "-vnc", f"127.0.0.1:{vnc_disp}",
                    "-serial", "stdio",
                ]
            else:
                cmd += [
                    "-device", "virtio-gpu-pci,xres=540,yres=960",
                    "-vnc", f"127.0.0.1:{vnc_disp}",
                    "-serial", "stdio",
                ]
        elif not disp_enabled:
            # Display hardware contract FAULT -> explicitly omit virtio-gpu-pci
            cmd += ["-nographic", "-serial", "stdio"]
        elif headless:
            cmd += ["-nographic", "-serial", "stdio"]
        else:
            cmd += ["-device", "virtio-gpu-pci,xres=540,yres=960", "-serial", "stdio"]

        if touch_enabled and embedded_display:
            cmd += [
                "-device", "virtio-tablet-pci",
                "-device", "virtio-keyboard-pci",
            ]
        if audio_enabled:
            cmd += [
                "-audiodev", "none,id=rak_snd0",
                "-device", "virtio-sound-pci,audiodev=rak_snd0",
            ]
        for idx, d in enumerate(disks):
            ro = "on" if d["readonly"] else "off"
            cmd += [
                "-drive", f"if=none,id=d{idx},file={d['path']},format=raw,readonly={ro}",
                # Windows QEMU aio-win32.c asserts count < MAXIMUM_WAIT_OBJECTS (64):
                # default num-queues=4 hits 18*4=72 on Transsion/Oplus multi-partition ROMs.
                "-device", f"virtio-blk-pci,drive=d{idx},serial={d['partition']},num-queues=1",
            ]
    else:
        # Android SDK QEMU fallback (only if explicitly overridden or upstream provisioning unavailable)
        emu_root = env["emulatorRoot"]
        if emu_root:
            extra_paths = [
                os.path.join(emu_root, "lib64"),
                os.path.join(emu_root, "lib64", "qt", "lib"),
                emu_root,
            ]
            env_extra["PATH_PREFIX"] = os.pathsep.join(p for p in extra_paths if os.path.isdir(p))
        cmd = [
            binary,
            "-kernel", kpath,
            "-ramdisk", ramdisk_path,
            "-data", userdata_path,
            "-memory", str(ram_clamped),
            "-cores", str(cpu_clamped),
            "-no-snapshot",
            "-no-audio",
            "-show-kernel",
            "-gpu", "swiftshader_indirect",
            "-logcat", "*:V",
            "-no-window",
        ]
        if timeout_sec is not None and int(timeout_sec) > 0:
            cmd += ["-quit-after-boot", str(max(15, int(timeout_sec)))]
        if "system" in imgs:
            cmd += ["-system", disks[0]["path"]]
        if "vendor" in imgs:
            vnd_disk = next((d["path"] for d in disks if d["partition"] == "vendor"), imgs["vendor"])
            cmd += ["-vendor", vnd_disk]
        qemu_tail = ["-qemu", "-append", cmdline]
        if embedded_display and vnc_port:
            qemu_tail += ["-vnc", f"127.0.0.1:{vnc_disp}"]
        cmd += qemu_tail

    plan = QemuBootPlan(
        ok=True,
        backend=backend,
        binary=binary,
        emulator_root=env["emulatorRoot"],
        abi_mode=abi_mode,
        kernel_path=kpath,
        ramdisk_path=ramdisk_path,
        work_dir=work_dir,
        disks=disks,
        cmdline=cmdline,
        command=cmd,
        env_extra=env_extra,
        vnc_port=vnc_port,
        warnings=warnings,
        init_source=init_src,
        virtio_modules_dir=modules_dir,
        hardware_gate=hw_gate,
        virtio_gfx_bridge=virtio_gfx_enabled,
    )
    try:
        with open(os.path.join(work_dir, "session_manifest.json"), "w", encoding="utf-8") as fh:
            json.dump(plan.to_dict(), fh, indent=2)
    except OSError:
        pass
    return plan


def parse_qemu_output_line(raw_line: str) -> dict:
    """Parse a raw QEMU serial/dmesg/logcat line into RAK Live Boot Logcat format."""
    line = raw_line.rstrip("\r\n")
    ts = datetime.datetime.now().strftime("%m-%d %H:%M:%S.%f")[:-3]
    m = _LOGCAT_LINE_RE.match(line)
    if m:
        lt, pid, tid, lvl, tag, msg = m.groups()
        return {
            "ts": lt, "pid": pid, "tid": tid, "level": lvl,
            "tag": tag.strip(), "msg": msg, "detail": "", "path": "",
            "stage": "qemu", "code": "qemu-logcat",
        }
    md = _DMESG_LINE_RE.match(line)
    if md:
        ktime, msg = md.groups()
        lvl = "F" if _PANIC_RE.search(msg) else ("E" if "error" in msg.lower() or "failed" in msg.lower() else "I")
        return {
            "ts": f"{ktime:>12}", "pid": "0", "tid": "0", "level": lvl,
            "tag": "qemu-kernel", "msg": msg, "detail": "", "path": "",
            "stage": "qemu", "code": "qemu-dmesg",
        }
    lvl = "F" if _PANIC_RE.search(line) else ("W" if "warn" in line.lower() else "I")
    return {
        "ts": ts, "pid": "0", "tid": "0", "level": lvl,
        "tag": "qemu", "msg": line, "detail": "", "path": "",
        "stage": "qemu", "code": "qemu-stdout",
    }


class QemuSession:
    """Managed non-blocking QEMU live boot session with milestone tracking & safe teardown."""

    def __init__(self, plan: QemuBootPlan, *, timeout_sec: int | None = 120):
        self.plan = plan
        raw_t = 0 if timeout_sec is None else int(timeout_sec)
        self.timeout_sec = max(10, raw_t) if raw_t > 0 else 0
        self._proc = None
        self._lock = threading.Lock()
        self._stop_requested = False
        self._timed_out = False
        self.running = False
        self.exit_code: int | None = None
        self.milestones: list[str] = []
        self.current_phase = "Idle"
        self.panic_detected = False
        self.raw_lines: list[str] = []
        self.started_at = 0.0

    def start(self, on_line=None, on_milestone=None, on_done=None):
        if not self.plan.ok:
            if on_done:
                on_done(False, self.plan.error, self)
            return
        self.running = True
        self._stop_requested = False
        self._timed_out = False
        self.started_at = time.perf_counter()
        self.current_phase = f"Launching QEMU ({self.plan.abi_mode})…"
        t = threading.Thread(
            target=self._run_worker,
            args=(on_line, on_milestone, on_done),
            daemon=True,
            name="RAK-QEMU-Runner",
        )
        t.start()

    def _run_worker(self, on_line, on_milestone, on_done):
        env = dict(os.environ)
        prefix = self.plan.env_extra.get("PATH_PREFIX")
        if prefix:
            env["PATH"] = prefix + os.pathsep + env.get("PATH", "")

        try:
            # Run QEMU at BELOW_NORMAL priority on Windows so TCG never starves the Qt UI thread
            cflags = (kitchen_process.CREATE_NO_WINDOW | 0x00004000) if os.name == "nt" else 0
            with kitchen_process.without_console():
                proc = kitchen_process.Popen(
                    self.plan.command,
                    stdout=kitchen_process.PIPE,
                    stderr=kitchen_process.STDOUT,
                    env=env,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    creationflags=cflags,
                )
            with self._lock:
                self._proc = proc
        except Exception as exc:
            self.running = False
            self.current_phase = f"Failed: {exc}"
            if on_done:
                on_done(False, f"Failed to launch QEMU: {exc}", self)
            return

        # Watchdog thread to guarantee timeout termination (only when timeout_sec > 0; 0 = interactive unlimited)
        if self.timeout_sec > 0:
            def _watchdog():
                deadline = self.started_at + self.timeout_sec
                while self.running and not self._stop_requested:
                    if time.perf_counter() >= deadline:
                        self._timed_out = True
                        self.stop()
                        break
                    time.sleep(0.5)

            threading.Thread(target=_watchdog, daemon=True, name="RAK-QEMU-Watchdog").start()

        seen_ms = set()
        hw_gate = getattr(self.plan, "hardware_gate", None) or {}
        # For non-guest-panic vendor faults (HAL/VINTF/SELinux blockers), halt QEMU immediately
        # when boot reaches that failing stage so a broken /vendor NEVER continues booting!
        enforce_halt_stage = ""
        if hw_gate.get("bootBlockedByVendor") and not hw_gate.get("kmiFaults") and not hw_gate.get("unsupportedFsTypes"):
            enforce_halt_stage = str(hw_gate.get("haltStage") or "fs_mounted")
        vendor_faults_list = list(hw_gate.get("vendorFaults") or ())
        halted_by_contract = False

        try:
            assert proc.stdout is not None
            for raw in proc.stdout:
                if self._stop_requested:
                    break
                line = raw.rstrip("\r\n")
                if not line or "GLib: WaitForMultipleObjectsEx failed" in line:
                    continue
                if len(self.raw_lines) < 50000:
                    self.raw_lines.append(line)
                if _PANIC_RE.search(line):
                    self.panic_detected = True
                for key, rx, label in _BOOT_MILESTONES:
                    if key not in seen_ms and rx.search(line):
                        seen_ms.add(key)
                        self.milestones.append(key)
                        self.current_phase = label
                        if on_milestone:
                            on_milestone(key, label)
                if on_line:
                    on_line(parse_qemu_output_line(line))
                # Real-stage boot halt when broken /vendor reaches the failing stage!
                if enforce_halt_stage and enforce_halt_stage in seen_ms and not halted_by_contract:
                    halted_by_contract = True
                    self.panic_detected = True
                    reason = vendor_faults_list[0] if vendor_faults_list else "Vendor/hardware mismatch against kernel"
                    fault_line = f"[hwgate] FATAL BOOT HALT at stage '{enforce_halt_stage}': {reason}"
                    self.raw_lines.append(fault_line)
                    if on_line:
                        on_line({
                            "ts": datetime.datetime.now().strftime("%m-%d %H:%M:%S.%f")[:-3],
                            "pid": "1", "tid": "1", "level": "F", "tag": "hwgate",
                            "msg": fault_line, "detail": "\n".join(vendor_faults_list), "path": "",
                            "stage": "qemu", "code": "vendor-boot-halt",
                        })
                    try:
                        proc.terminate()
                    except Exception:
                        pass
                    break
            proc.wait(timeout=5)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        finally:
            self.exit_code = proc.poll()
            self.running = False
            with self._lock:
                self._proc = None
            # Immediately remove this session's ephemeral work_dir (528 MB userdata/metadata/ramdisk)
            wdir = getattr(self.plan, "work_dir", "") or ""
            if wdir and os.path.isdir(wdir) and os.path.basename(wdir).startswith("sess_"):
                shutil.rmtree(wdir, ignore_errors=True)

        elapsed = time.perf_counter() - self.started_at
        hw_gate = getattr(self.plan, "hardware_gate", None) or {}
        vendor_faults = list(hw_gate.get("vendorFaults") or ())
        boot_blocked = bool(hw_gate.get("bootBlockedByVendor"))
        fs_fault = ((hw_gate.get("byKey") or {}).get("filesystem") or {}).get("status") == "fault"
        boot_failed = self.panic_detected or boot_blocked or fs_fault

        if halted_by_contract:
            summary = (
                f"BOOT HALTED ({enforce_halt_stage}) after {elapsed:.1f}s: "
                f"{vendor_faults_list[0] if vendor_faults_list else 'Vendor/kernel mismatch'} — tidak akan boot!"
            )
        elif self.panic_detected and hw_gate.get("kmiFaults"):
            summary = (
                f"KERNEL PANIC (first-stage modules) after {elapsed:.1f}s: "
                f"{hw_gate['kmiFaults'][0]} — tidak akan boot!"
            )
        elif self.panic_detected:
            summary = f"BOOT FAILED: Kernel/runtime panic after {elapsed:.1f}s ({len(self.raw_lines)} lines)"
        elif fs_fault:
            summary = f"BOOT FAILED: Real kernel lacks partition filesystem support ({len(self.raw_lines)} lines)"
        elif boot_blocked:
            summary = (
                f"BOOT FAILED: {len(vendor_faults)} vendor fault(s) against kernel "
                f"({vendor_faults[0]}) — will fail on real device!"
            )
        elif self._timed_out:
            summary = f"QEMU timed out after {elapsed:.1f}s ({len(self.raw_lines)} lines)"
        elif self._stop_requested:
            summary = f"QEMU stopped after {elapsed:.1f}s ({len(self.raw_lines)} lines)"
        elif "boot_completed" in self.milestones:
            summary = f"QEMU reached boot_completed in {elapsed:.1f}s!"
        else:
            summary = f"QEMU exited (code={self.exit_code}) after {elapsed:.1f}s ({len(self.raw_lines)} lines)"
        self.current_phase = summary
        if on_done:
            on_done(not boot_failed, summary, self)

    def stop(self):
        self._stop_requested = True
        with self._lock:
            proc = self._proc
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=3)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass

    def full_log_text(self) -> str:
        return "\n".join(self.raw_lines)


def run_qemu_blocking(plan: QemuBootPlan, *, timeout_sec: int = 90) -> dict:
    """Synchronous CLI wrapper around QemuSession."""
    done_ev = threading.Event()
    result = {}

    def _on_done(ok, summary, sess):
        result["ok"] = ok
        result["summary"] = summary
        result["exitCode"] = sess.exit_code
        result["milestones"] = list(sess.milestones)
        result["panicDetected"] = sess.panic_detected
        result["lineCount"] = len(sess.raw_lines)
        result["logText"] = sess.full_log_text()
        done_ev.set()

    sess = QemuSession(plan, timeout_sec=timeout_sec)
    sess.start(on_done=_on_done)
    done_ev.wait(timeout=timeout_sec + 10)
    if sess.running:
        sess.stop()
    return result
