# SPDX-License-Identifier: MPL-2.0
from __future__ import annotations

import os
import re

from core.vdev.source import DirSource, ImageSource, MemoryTreeSource
from core.vdev.vfs import VirtualFS, normalize

print("[CORE STAGE] VDEV MOUNTS")


KNOWN_PARTITIONS = {
    "system", "system_ext", "product", "vendor", "odm", "oem", "cust",
    "odm_dlkm", "vendor_dlkm", "system_dlkm", "mi_ext", "preload", "prism",
    "optics", "my_product", "my_engineering", "my_stock", "my_heytap",
    "my_carrier", "my_region", "my_bigball", "my_manifest", "my_company",
    "oppo_product", "opproduct", "special_preload", "vendor_boot", "boot",
    "init_boot", "recovery", "dtbo", "vbmeta", "vbmeta_system", "vbmeta_vendor",
    "tr_carrier", "tr_company", "tr_manifest", "tr_mi_b", "tr_misc",
    "tr_overlayfs", "tr_preload", "tr_product", "tr_region", "tr_theme",
}

_OEM_PREFIXES = ("tr_", "my_", "oppo_", "mi_", "oplus_")
_DLKM_NAMES = {"vendor_dlkm", "odm_dlkm", "system_dlkm"}

# Penanda "folder ini root sebuah partisi", bukan folder biasa / folder APK.
_PARTITION_MARKERS = (
    "build.prop", "etc", "bin", "lib", "lib64", "app", "priv-app",
    "framework", "overlay", "firmware", "system",
)

_FSTAB_RE = re.compile(r"^fstab\.")


class FstabEntry:
    __slots__ = ("source", "mount_point", "fs_type", "mnt_flags",
                 "fs_mgr_flags", "raw", "origin")

    def __init__(self, source, mount_point, fs_type, mnt_flags,
                 fs_mgr_flags, raw="", origin=""):
        self.source = source
        self.mount_point = mount_point
        self.fs_type = fs_type
        self.mnt_flags = mnt_flags
        self.fs_mgr_flags = fs_mgr_flags
        self.raw = raw
        self.origin = origin

    # ── fs_mgr flag helpers ──
    def flag(self, name):

        for f in self.fs_mgr_flags:
            if f == name or f.startswith(name + "="):
                return True
        return False

    def flag_value(self, name):

        for f in self.fs_mgr_flags:
            if f.startswith(name + "="):
                return f.split("=", 1)[1]
        return ""

    @property
    def avb(self):

        return self.flag("avb")

    @property
    def partition_name(self):

        s = self.source.rsplit("/", 1)[-1]
        return s

    def __repr__(self):
        return f"<Fstab {self.mount_point} <- {self.source} ({self.fs_type})>"


class Layout:


    def __init__(self, port_dir):
        self.port_dir = os.path.normpath(port_dir)
        self.partitions = {}      # nama -> host dir
        self.boot_dirs = {}       # nama -> host dir (hasil unpack boot)
        self.images = {}          # nama -> host .img yang belum di-unpack
        self.sar = False          # system-as-root
        self.system_root = ""     # host dir yang jadi "/"
        self.ramdisk_dir = ""     # host dir ramdisk first-stage
        self.ramdisk_source = ""  # dari vendor_boot / boot / tidak ada
        self.fstab_path = ""      # vpath atau host path fstab yang dipakai
        self.fstab_origin = ""    # keterangan asal fstab buat laporan
        self.notes = []           # catatan non-fatal buat laporan
        self.compat_aliases = []  # /system/<partisi> yang disintesis (lihat build_vfs)
        self.sources = {}         # nama partisi -> "folder" | "image"
        self.system_from_image = False
        self.fs_images = {}       # nama -> host .img yang BENAR filesystem


        self.ref_images = {}      # nama -> host .img pasangan folder
        self.boot_images = []     # [(BOOT|VNDRBOOT, host path)]
        self.fstab_sources = []   # dari mana daftar partisi didapat
        self.partition_names = set()   # menurut ROM, bukan menurut daftar bawaan
        self._sources = {}        # cache source per partisi (hindari walk ganda)
        self.source_kind = "folder"   # "folder" | "super" | "zip"
        self.super_path = ""
        self.zip_path = ""
        self.boot_inspections = {}    # name -> BootInspectionReport dict
        self.vbmeta_reports = {}      # name -> VbMetaSummary dict
        self.invalid_vbmeta = []      # [(name, path)] vbmeta files with corrupt/invalid AVB0 header
        self.dtbo_report = None       # VdevDtboReport dict (if dtbo.img / unpacked dtbo present)
        self.dtbo_source = ""         # host path or zip://...#entry
        self.boot_props = {}          # ro.boot.* / ro.hardware / cmdline tokens
        self.dtb_fstabs = []          # fstab entries dari DTB android,fstab
        self.ramdisk_virtual = None   # MemoryTreeSource saat ramdisk dibaca dari .img/.zip

    def has(self, name):
        return name in self.partitions

    def all_partition_names(self):

        names = list(self.partitions)
        for n in self.fs_images:
            if n not in self.partitions:
                names.append(n)
        return names

    @property
    def any_partition(self):

        return bool(self.partitions or self.fs_images)


def _config_dir(port_dir):
    return os.path.join(port_dir, "config")


def _looks_like_partition(host_dir):
    try:
        names = set(os.listdir(host_dir))
    except OSError:
        return False
    return any(m in names for m in _PARTITION_MARKERS)


def _strip_slot(name):

    if len(name) > 2 and name[-2:] in ("_a", "_b"):
        base = name[:-2]
        if base in KNOWN_PARTITIONS or base.startswith(_OEM_PREFIXES) or base in _DLKM_NAMES:
            return base
    return name


def _is_known_or_oem_partition(name):
    return (
        name in KNOWN_PARTITIONS
        or name in _DLKM_NAMES
        or name.startswith(_OEM_PREFIXES)
    )


def _boot_kind(port_dir, name):
    from core.boot_image import read_bootconf
    kind, _ = read_bootconf(os.path.join(_config_dir(port_dir), f"{name}_bootconf"))
    return kind


def _boot_images(port_dir, names):

    from core.header_magic import check_magic
    out = []
    for name in names:
        path = os.path.join(port_dir, name)
        try:
            kind = check_magic(path)
        except Exception:                 # noqa: BLE001
            continue
        if kind in ("BOOT", "VNDRBOOT"):
            out.append((kind, path))
    # vendor_boot lebih otoritatif untuk fstab first-stage daripada boot.
    out.sort(key=lambda kv: 0 if kv[0] == "VNDRBOOT" else 1)
    return out


def _names_from_fstab(text):

    out = set()
    for entry in parse_fstab(text):
        src = entry.source
        if src.startswith("/") or src.startswith("#"):
            continue
        out.add(_strip_slot(src))
        mp = entry.mount_point.strip("/")
        if mp and "/" not in mp:
            out.add(mp)
    return out


def derive_partition_names(layout):

    names = set()

    # ── 1) fstab dari boot image ──
    for _kind, path in layout.boot_images:
        try:
            from core.vdev.bootimg import find_fstab as _boot_fstab
            hit = _boot_fstab(path)
        except Exception:                 # noqa: BLE001
            hit = None
        if hit:
            layout.fstab_sources.append(f"{os.path.basename(path)}:{hit[0]}")
            names |= _names_from_fstab(hit[1])

    # ── 1b) fstab dari ramdisk yang sudah ke-unpack ──
    if layout.ramdisk_dir:
        for base in (os.path.join(layout.ramdisk_dir, "first_stage_ramdisk"),
                     layout.ramdisk_dir):
            if not os.path.isdir(base):
                continue
            for fname in os.listdir(base):
                if not _FSTAB_RE.match(fname):
                    continue
                try:
                    with open(os.path.join(base, fname), "r",
                              encoding="utf-8", errors="ignore") as f:
                        names |= _names_from_fstab(f.read())
                    layout.fstab_sources.append(f"ramdisk:{fname}")
                except OSError:
                    pass

    return names


def _names_from_mountpoints(source):

    try:
        return {n for n, (kind, _sz) in source.entries("").items()
                if kind == "dir"}
    except Exception:                     # noqa: BLE001
        return set()


def _parse_cmdline_tokens(text, boot_props):
    if not text:
        return
    for tok in text.replace("\n", " ").split():
        if "=" not in tok:
            continue
        k, v = tok.split("=", 1)
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        if not k or not v:
            continue
        if k.startswith("androidboot."):
            sub = k[len("androidboot."):]
            boot_props.setdefault(k, v)
            boot_props.setdefault("ro.boot." + sub, v)
            if sub == "hardware":
                boot_props.setdefault("ro.hardware", v)
        elif k.startswith("ro.boot.") or k == "ro.hardware":
            boot_props.setdefault(k, v)


def _absorb_boot_report(layout, name, report):
    if not isinstance(report, dict):
        return
    layout.boot_inspections[name] = report
    hdr = report.get("header") or {}
    _parse_cmdline_tokens(hdr.get("cmdline", ""), layout.boot_props)
    _parse_cmdline_tokens(report.get("bootconfig", ""), layout.boot_props)
    for arg in (report.get("dtb") or {}).get("bootargs", []) or []:
        _parse_cmdline_tokens(arg, layout.boot_props)
    for arg in (report.get("kernel_dtb") or {}).get("bootargs", []) or []:
        _parse_cmdline_tokens(arg, layout.boot_props)

    for dtb_key in ("dtb", "kernel_dtb"):
        for ent in (report.get(dtb_key) or {}).get("fstab", []) or []:
            mp = ent.get("mnt_point", "")
            dev = ent.get("dev", "")
            if not mp:
                continue
            mnt_flags = [x for x in ent.get("mnt_flags", "").split(",") if x]
            fs_mgr = [x for x in ent.get("fsmgr_flags", "").split(",") if x]
            fe = FstabEntry(dev, mp, ent.get("fs_type", ""), mnt_flags, fs_mgr,
                            raw=f"{dev} {mp} {ent.get('fs_type', '')}", origin=f"{name}:dtb")
            layout.dtb_fstabs.append(fe)
            part = _strip_slot(fe.partition_name)
            if part and not part.startswith("/"):
                layout.partition_names.add(part)
            mpp = mp.strip("/")
            if mpp and "/" not in mpp:
                layout.partition_names.add(mpp)

    emb = report.get("embedded_vbmeta")
    if isinstance(emb, dict) and emb.get("valid"):
        layout.vbmeta_reports.setdefault(f"{name}:embedded", emb)

    for rd in report.get("ramdisks") or []:
        for fpath, text in (rd.get("fstabs") or {}).items():
            layout.fstab_sources.append(f"{name}:{fpath}")
            layout.partition_names |= _names_from_fstab(text)


def _absorb_dtbo_report(layout, report, source=""):
    if not isinstance(report, dict):
        return
    layout.dtbo_report = report
    layout.dtbo_source = source
    for arg in report.get("all_bootargs") or []:
        _parse_cmdline_tokens(arg, layout.boot_props)
    for ent in report.get("all_fstab") or []:
        mp = ent.get("mnt_point", "")
        dev = ent.get("dev", "")
        if not mp:
            continue
        mnt_flags = [x for x in ent.get("mnt_flags", "").split(",") if x]
        fs_mgr = [x for x in ent.get("fsmgr_flags", "").split(",") if x]
        fe = FstabEntry(dev, mp, ent.get("fs_type", ""), mnt_flags, fs_mgr,
                        raw=f"{dev} {mp} {ent.get('fs_type', '')}", origin="dtbo:dtb")
        layout.dtb_fstabs.append(fe)
        part = _strip_slot(fe.partition_name)
        if part and not part.startswith("/"):
            layout.partition_names.add(part)
        mpp = mp.strip("/")
        if mpp and "/" not in mpp:
            layout.partition_names.add(mpp)
    emb = report.get("embedded_vbmeta")
    if isinstance(emb, dict) and emb.get("valid"):
        layout.vbmeta_reports.setdefault("dtbo:embedded", emb)


def _build_virtual_ramdisk(layout):
    files = {}
    symlinks = {}
    # vendor_boot lebih prioritas daripada boot/init_boot untuk first-stage ramdisk
    order = sorted(
        layout.boot_inspections.items(),
        key=lambda kv: (0 if "vendor_boot" in kv[0].lower()
                        else 1 if "init_boot" in kv[0].lower() else 2, kv[0]),
    )
    source_name = ""
    for name, report in order:
        rds = report.get("ramdisks") or []
        if not rds:
            continue
        if not source_name:
            source_name = name
        for rd in rds:
            for k, v in (rd.get("fstabs") or {}).items():
                files.setdefault(k, v)
            for k, mods in (rd.get("modules_load") or {}).items():
                files.setdefault(k, "\n".join(mods) + "\n")
            for k, v in (rd.get("modules_dep") or {}).items():
                files.setdefault(k, v)
            for k, v in (rd.get("init_rc") or {}).items():
                files.setdefault(k, v)
            for k in (rd.get("files") or []):
                files.setdefault(k, b"")
            for k, target in (rd.get("symlinks") or {}).items():
                symlinks.setdefault(k, target)
    if files or symlinks:
        layout.ramdisk_virtual = MemoryTreeSource(files, symlinks, label=source_name or "ramdisk")
        if not layout.ramdisk_source:
            layout.ramdisk_source = source_name or "boot_img"


def _attach_super_partitions(layout, super_path, slot=0):
    from core import native_backend
    try:
        info = native_backend.vdev_probe_super(super_path, slot=slot)
    except Exception as exc:                 # noqa: BLE001
        layout.notes.append(f"super.img probe failed: {exc}")
        return
    layout.super_path = os.path.normpath(super_path)
    for part in info.get("partitions") or []:
        pname = _strip_slot(part.get("name", ""))
        fs_type = part.get("fs_type", "")
        if not pname or fs_type not in ("EXT", "EROFS"):
            continue
        layout.partition_names.add(pname)
        if pname in layout.partitions:
            continue
        layout.fs_images[pname] = layout.super_path
        layout._sources[pname] = ImageSource(
            layout.super_path,
            super_path=layout.super_path,
            partition=part.get("name", pname),
            slot=slot,
            fs_hint=fs_type,
        )
        layout.sources[pname] = "super"


def _pick_large_cache_base(hint_path="", min_c_free_gb=45.0):
    """Avoid filling a tight C: drive with multi-GB ZIP image extractions."""
    import shutil
    from core.app_paths import CACHE_DIR

    def _free(p):
        try:
            probe = p
            while probe and not os.path.exists(probe):
                par = os.path.dirname(probe)
                if par == probe:
                    break
                probe = par
            return int(shutil.disk_usage(probe or ".").free)
        except OSError:
            return 0

    default_base = os.path.join(CACHE_DIR, "vdev_zip")
    c_free = _free(CACHE_DIR)
    if os.name != "nt" or c_free >= int(min_c_free_gb * 1024 * 1024 * 1024):
        return default_base

    # Prefer the drive where the ZIP itself lives if it has more free space than C:
    candidates = [(c_free, default_base)]
    if hint_path:
        drv = os.path.splitdrive(os.path.abspath(hint_path))[0]
        if drv:
            cand = os.path.join(drv + "\\", ".rak_qemu_cache", "vdev_zip")
            candidates.append((_free(drv + "\\"), cand))
    try:
        import ctypes
        mask = ctypes.windll.kernel32.GetLogicalDrives()
        for i in range(26):
            if mask & (1 << i):
                root = f"{chr(65 + i)}:\\"
                if ctypes.windll.kernel32.GetDriveTypeW(root) == 3:
                    cand = os.path.join(root, ".rak_qemu_cache", "vdev_zip")
                    candidates.append((_free(root), cand))
    except Exception:
        pass
    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates[0][1]


def _prune_zip_cache(base_dir, keep_key="", max_entries=2):
    """Keep at most `max_entries` extracted firmware ZIP caches so disk usage stays bounded."""
    import shutil
    if not os.path.isdir(base_dir):
        return
    try:
        dirs = []
        for name in os.listdir(base_dir):
            full = os.path.join(base_dir, name)
            if os.path.isdir(full) and name != keep_key:
                try:
                    mtime = os.path.getmtime(full)
                except OSError:
                    mtime = 0.0
                dirs.append((mtime, full))
        dirs.sort(key=lambda item: item[0], reverse=True)
        for _mt, old in dirs[max(0, max_entries - 1):]:
            shutil.rmtree(old, ignore_errors=True)
    except OSError:
        pass


def _zip_cache_dir(zip_path):
    import hashlib
    key = hashlib.sha1(os.path.normcase(os.path.abspath(zip_path)).encode("utf-8")).hexdigest()[:16]
    base = _pick_large_cache_base(zip_path)
    _prune_zip_cache(base, keep_key=key, max_entries=2)
    return os.path.join(base, key)


def _fmt_progress_bytes(nbytes: int) -> str:
    if nbytes >= 1024 * 1024 * 1024:
        return f"{nbytes / (1024 * 1024 * 1024):.2f} GB"
    return f"{nbytes / (1024 * 1024):.0f} MB"


def run_with_dir_progress(
    work_fn,
    watch_dir: str,
    *,
    total_bytes: int,
    total_files: int,
    label: str,
    on_progress=None,
    poll_sec: float = 0.25,
):
    """Run a blocking extraction callable while streaming live bytes / % / MB/s / active file progress."""
    if not on_progress:
        return work_fn()

    import threading
    import time

    stop_ev = threading.Event()
    t0 = time.monotonic()

    def _emit(msg: str):
        try:
            on_progress(msg)
        except Exception:
            pass

    _emit(f"{label} — 0/{max(1, total_files)} (0%)…")

    def _monitor():
        prev_bytes = 0
        prev_t = t0
        smooth_bps = 0.0
        while not stop_ev.wait(poll_sec):
            done_bytes = 0
            done_imgs = 0
            active_parts: list[str] = []
            if os.path.isdir(watch_dir):
                try:
                    with os.scandir(watch_dir) as it:
                        for e in it:
                            if not e.is_file(follow_symlinks=False):
                                continue
                            nm = e.name
                            low = nm.lower()
                            if low.startswith(".") or low.endswith(".json"):
                                continue
                            try:
                                # MUST use os.stat(e.path), NOT e.stat()!
                                # On Windows NTFS, scandir's cached WIN32_FIND_DATA returns 0 for actively written files!
                                sz = os.stat(e.path).st_size
                            except OSError:
                                sz = 0
                            if low.endswith(".part") or low.endswith(".tmp"):
                                done_bytes += sz
                                stem_nm = nm[:-5] if low.endswith(".part") else nm[:-4]
                                active_parts.append(stem_nm if stem_nm.lower().endswith(".img") else stem_nm + ".img")
                            elif low.endswith(".img"):
                                done_bytes += sz
                                done_imgs += 1
                except OSError:
                    pass
            now = time.monotonic()
            dt = max(0.05, now - prev_t)
            inst_bps = max(0.0, (done_bytes - prev_bytes) / dt)
            smooth_bps = inst_bps if smooth_bps <= 0 else (smooth_bps * 0.65 + inst_bps * 0.35)
            prev_bytes = done_bytes
            prev_t = now

            if total_bytes > 0:
                pct = min(99, int(done_bytes * 100 / total_bytes))
                size_str = f"{_fmt_progress_bytes(done_bytes)} / {_fmt_progress_bytes(total_bytes)}"
            else:
                pct = min(99, int(done_imgs * 100 / max(1, total_files)))
                size_str = _fmt_progress_bytes(done_bytes)
            spd_str = f" · {smooth_bps / 1048576:.0f} MB/s" if smooth_bps >= 512 * 1024 else ""
            cur_str = f" · {', '.join(active_parts[:2])}" if active_parts else ""
            _emit(f"{label} — {done_imgs}/{max(1, total_files)} · {size_str} ({pct}%{spd_str}){cur_str}")

    mon = threading.Thread(target=_monitor, daemon=True, name="RAK-Extract-Progress")
    mon.start()
    try:
        return work_fn()
    finally:
        stop_ev.set()
        mon.join(timeout=0.6)


def _scan_zip_layout(layout, zip_path, slot=0, progress=None):
    from core import native_backend
    layout.source_kind = "zip"
    layout.zip_path = os.path.normpath(zip_path)
    zname = os.path.basename(layout.zip_path)

    def _note(msg: str):
        if progress:
            try:
                progress(msg)
            except Exception:
                pass

    _note(f"Probing {zname}…")
    try:
        entries = native_backend.vdev_probe_zip(layout.zip_path)
    except Exception as exc:                 # noqa: BLE001
        layout.notes.append(f"Firmware ZIP unreadable: {exc}")
        return layout

    by_role = {}
    for ent in entries:
        by_role.setdefault(ent.get("role", "other"), []).append(ent)

    cache_dir = _zip_cache_dir(layout.zip_path)

    # 1) Boot / vendor_boot / init_boot langsung dibaca di RAM dari ZIP (cached)
    boot_entries = sorted(
        by_role.get("boot", []),
        key=lambda e: (e["name"].count("/"),
                       1 if "debug" in e["basename"].lower() else 0,
                       0 if "vendor_boot" in e["basename"].lower()
                       else 1 if "init_boot" in e["basename"].lower() else 2, e["basename"]),
    )
    seen_boots = set()
    for ent in boot_entries:
        bname = _strip_slot(os.path.splitext(ent["basename"])[0])
        if bname in seen_boots:
            continue
        seen_boots.add(bname)
        _note(f"Inspecting {ent['basename']} from {zname}…")
        try:
            rep = native_backend.vdev_inspect_zip_boot(layout.zip_path, ent["name"], cache_dir=cache_dir)
            _absorb_boot_report(layout, bname, rep)
            kind = "VNDRBOOT" if "vendor_boot" in bname.lower() else "BOOT"
            layout.boot_images.append((kind, f"zip://{layout.zip_path}#{ent['name']}"))
        except Exception as exc:             # noqa: BLE001
            layout.notes.append(f"Boot inspect skipped for {ent['basename']}: {exc}")

    # 2) VBMeta langsung dibaca di RAM dari ZIP (cached)
    vb_entries = sorted(by_role.get("vbmeta", []), key=lambda e: (e["name"].count("/"), e["name"]))
    for ent in vb_entries:
        vname = _strip_slot(os.path.splitext(ent["basename"])[0])
        if vname in layout.vbmeta_reports:
            continue
        try:
            vb = native_backend.vdev_inspect_zip_vbmeta(layout.zip_path, ent["name"], cache_dir=cache_dir)
            if isinstance(vb, dict) and vb.get("valid"):
                layout.vbmeta_reports[vname] = vb
        except Exception:
            pass

    # 2b) DTBO langsung dibaca & diparse di RAM dari ZIP (cached)
    dtbo_entries = sorted(by_role.get("dtbo", []), key=lambda e: (e["name"].count("/"), e["name"]))
    for ent in dtbo_entries:
        _note(f"Inspecting {ent['basename']} from {zname}…")
        try:
            drep = native_backend.vdev_inspect_zip_dtbo(layout.zip_path, ent["name"], cache_dir=cache_dir)
            _absorb_dtbo_report(layout, drep, source=f"zip://{layout.zip_path}#{ent['name']}")
            break
        except Exception as exc:             # noqa: BLE001
            layout.notes.append(f"DTBO inspect skipped for {ent['basename']}: {exc}")

    # 3) Siapkan super.img atau partisi .img lewat cache paralel Rust (dengan live progress bytes + % + MB/s!)
    super_ents = [e for e in by_role.get("super", []) if "empty" not in e["basename"].lower()]
    if super_ents:
        super_ents.sort(key=lambda e: (e["name"].count("/"), -int(e.get("size") or 0)))
        super_ents = [super_ents[0]]
    part_ents = by_role.get("partition", []) or by_role.get("firmware_img", [])
    selected_ents = super_ents if super_ents else part_ents
    to_prepare = [e["name"] for e in selected_ents]
    total_prep_bytes = sum(int(e.get("size") or 0) for e in selected_ents)

    if to_prepare:
        try:
            prepared = run_with_dir_progress(
                lambda: native_backend.vdev_prepare_zip_images(layout.zip_path, to_prepare, cache_dir),
                cache_dir,
                total_bytes=total_prep_bytes,
                total_files=len(to_prepare),
                label=f"Extracting {zname}",
                on_progress=progress,
            )
        except Exception as exc:             # noqa: BLE001
            layout.notes.append(f"ZIP partition prepare failed: {exc}")
            prepared = []
        for item in prepared:
            hp = item.get("host_path", "")
            base = os.path.basename(hp)
            stem = _strip_slot(os.path.splitext(base)[0])
            if stem == "super":
                _note(f"Indexing dynamic partitions in {base}…")
                _attach_super_partitions(layout, hp, slot=slot)
                continue
            fs_type = item.get("fs_type", "")
            layout.images[stem] = hp
            if fs_type in ("EXT", "EROFS"):
                layout.partition_names.add(stem)
                layout.fs_images[stem] = hp

    _note("Finalizing partition layout & ramdisk…")
    _finalize_system_layout(layout)
    _build_virtual_ramdisk(layout)
    return layout


def _finalize_system_layout(layout):
    vnd_src = layout._sources.get("vendor")
    if vnd_src is None:
        if "vendor" in layout.partitions:
            vnd_src = DirSource(layout.partitions["vendor"])
        elif "vendor" in layout.fs_images:
            vnd_src = ImageSource(layout.fs_images["vendor"])
        elif "vendor" in layout.images:
            try:
                from core.imgfs import detect_fs as _detect_fs
                if _detect_fs(layout.images["vendor"]):
                    vnd_src = ImageSource(layout.images["vendor"])
                    layout.fs_images["vendor"] = layout.images["vendor"]
            except Exception:
                vnd_src = None
    if vnd_src is not None:
        layout._sources["vendor"] = vnd_src
        try:
            for fname, (kind, _sz) in vnd_src.entries("etc").items():
                if kind != "file" or not _FSTAB_RE.match(fname):
                    continue
                blob = vnd_src.read("etc/" + fname)
                if not blob:
                    continue
                layout.partition_names |= _names_from_fstab(
                    blob.decode("utf-8", "replace"))
                layout.fstab_sources.append(f"vendor:etc/{fname}")
        except Exception:
            pass

    sys_src = layout._sources.get("system")
    if sys_src is None:
        if "system" in layout.partitions:
            sys_src = DirSource(layout.partitions["system"])
        elif "system" in layout.fs_images:
            sys_src = ImageSource(layout.fs_images["system"])
    if sys_src is not None:
        layout._sources["system"] = sys_src
        root = _names_from_mountpoints(sys_src)
        if root:
            layout.partition_names |= root

    sysdir = layout.partitions.get("system")
    if sysdir:
        if os.path.isfile(os.path.join(sysdir, "system", "build.prop")):
            layout.sar = True
            layout.system_root = sysdir
        elif os.path.isfile(os.path.join(sysdir, "build.prop")):
            layout.sar = False
            layout.system_root = sysdir
        else:
            layout.sar = os.path.isdir(os.path.join(sysdir, "system"))
            layout.system_root = sysdir
    elif "system" in layout.fs_images:
        layout.system_root = layout.fs_images["system"]
        layout.system_from_image = True
        src = layout._sources.get("system") or ImageSource(layout.system_root)
        layout._sources["system"] = src
        layout.sar = "system" in src.entries("")


def _scan_staged_layout(layout, staged, slot=0, progress=None):
    """Build a Layout purely from user-staged firmware slots, super.img, child partitions, or base .zip."""
    from core import native_backend
    from core.header_magic import check_magic
    from core.imgfs import detect_fs as _detect_fs

    def _note(msg: str):
        if progress:
            try:
                progress(msg)
            except Exception:
                pass

    zip_path = str(staged.get("zip_path") or "").strip()
    super_path = str(staged.get("super_path") or "").strip()
    firmware = staged.get("firmware") or {}
    partitions = staged.get("partitions") or {}

    if zip_path and os.path.isfile(zip_path):
        _scan_zip_layout(layout, zip_path, slot=slot, progress=progress)

    if super_path and os.path.isfile(super_path):
        _note(f"Probing dynamic partitions in {os.path.basename(super_path)}…")
        layout.source_kind = "super" if not zip_path else "staged"
        _attach_super_partitions(layout, super_path, slot=slot)

    # Explicit outer firmware slots (boot, vendor_boot, init_boot, vbmeta*, dtbo)
    fw_items = list(firmware.items())
    for fw_idx, (raw_key, raw_path) in enumerate(fw_items, start=1):
        fpath = str(raw_path or "").strip()
        if not fpath or not (os.path.isfile(fpath) or os.path.isdir(fpath)):
            continue
        _note(f"Inspecting firmware [{fw_idx}/{len(fw_items)}]: {os.path.basename(fpath)}…")
        norm = _strip_slot(str(raw_key).strip().lower())
        norm_path = os.path.normpath(fpath)
        if os.path.isdir(norm_path):
            if norm == "dtbo":
                try:
                    drep = native_backend.vdev_inspect_dtbo(norm_path)
                    _absorb_dtbo_report(layout, drep, source=norm_path)
                except Exception as exc:      # noqa: BLE001
                    layout.notes.append(f"DTBO folder inspect skipped: {exc}")
                continue
            layout.boot_dirs[norm] = norm_path
            rd = os.path.join(norm_path, "ramdisk")
            if os.path.isdir(rd):
                if "vendor_boot" in norm or not layout.ramdisk_dir:
                    layout.ramdisk_dir = rd
                    layout.ramdisk_source = norm
            continue

        layout.images[norm] = norm_path
        if norm == "dtbo":
            try:
                drep = native_backend.vdev_inspect_dtbo(norm_path)
                _absorb_dtbo_report(layout, drep, source=norm_path)
            except Exception as exc:          # noqa: BLE001
                layout.notes.append(f"DTBO inspect skipped: {exc}")
            continue
        if norm.startswith("vbmeta"):
            try:
                vb = native_backend.vdev_inspect_vbmeta(norm_path)
                if isinstance(vb, dict) and vb.get("valid"):
                    layout.vbmeta_reports[norm] = vb
                else:
                    layout.invalid_vbmeta.append((norm, norm_path))
            except Exception as exc:          # noqa: BLE001
                layout.invalid_vbmeta.append((norm, norm_path))
                layout.notes.append(f"VBMeta inspect skipped for {norm}: {exc}")
            continue

        if norm in ("boot", "vendor_boot", "init_boot"):
            kind = "VNDRBOOT" if "vendor_boot" in norm else "BOOT"
            try:
                magic = check_magic(norm_path)
                if magic in ("BOOT", "VNDRBOOT"):
                    kind = magic
            except Exception:
                pass
            # Replace any prior boot image of the same slot name
            layout.boot_images = [
                (k, p) for (k, p) in layout.boot_images
                if _strip_slot(os.path.splitext(os.path.basename(p.split("#")[-1]))[0]).lower() != norm
            ]
            layout.boot_images.append((kind, norm_path))
            layout.boot_images.sort(key=lambda kv: 0 if kv[0] == "VNDRBOOT" else 1)
            try:
                rep = native_backend.vdev_inspect_boot(norm_path)
                _absorb_boot_report(layout, norm, rep)
            except Exception as exc:          # noqa: BLE001
                layout.notes.append(f"Boot inspect skipped for {norm}: {exc}")

    # Explicit inner SUPER / logical child partitions (.img or unpacked folder)
    part_items = list(partitions.items())
    for p_idx, (raw_name, raw_path) in enumerate(part_items, start=1):
        ppath = str(raw_path or "").strip()
        if not ppath or not (os.path.isfile(ppath) or os.path.isdir(ppath)):
            continue
        pct = int(p_idx * 100 / max(1, len(part_items)))
        _note(f"Checking partition [{p_idx}/{len(part_items)}]: {os.path.basename(ppath)} ({pct}%)…")
        norm = _strip_slot(str(raw_name).strip().lower())
        norm_path = os.path.normpath(ppath)
        if os.path.isdir(norm_path):
            layout.partitions[norm] = norm_path
            layout.fs_images.pop(norm, None)
            layout._sources.pop(norm, None)
            layout.partition_names.add(norm)
            continue
        if norm == "super":
            _attach_super_partitions(layout, norm_path, slot=slot)
            continue
        layout.images[norm] = norm_path
        try:
            fs_kind = _detect_fs(norm_path)
        except Exception:
            fs_kind = None
        if fs_kind:
            layout.partitions.pop(norm, None)
            layout._sources.pop(norm, None)
            layout.fs_images[norm] = norm_path
            layout.partition_names.add(norm)
        else:
            layout.notes.append(f"Partition image '{os.path.basename(norm_path)}' is not a recognised EXT4/EROFS image")

    _note("Indexing system & vendor filesystem trees…")
    layout.partition_names |= derive_partition_names(layout)
    _finalize_system_layout(layout)
    if not layout.ramdisk_dir:
        layout.ramdisk_virtual = None
        _build_virtual_ramdisk(layout)
    if firmware or partitions:
        layout.source_kind = "staged"
    return layout


def scan_layout(port_dir, options=None, progress=None):

    options = options or {}
    slot = int(options.get("slot", 0))
    layout = Layout(port_dir or "")

    staged = options.get("staged")
    if isinstance(staged, dict) and any(
        staged.get(k) for k in ("zip_path", "super_path", "firmware", "partitions")
    ):
        return _scan_staged_layout(layout, staged, slot=slot, progress=progress)

    target = layout.port_dir

    if os.path.isfile(target):
        low = target.lower()
        if low.endswith(".zip"):
            return _scan_zip_layout(layout, target, slot=slot, progress=progress)
        if low.endswith(".img"):
            layout.source_kind = "super"
            _attach_super_partitions(layout, target, slot=slot)
            _finalize_system_layout(layout)
            return layout

    port_dir = target

    try:
        entries = sorted(os.listdir(port_dir))
    except OSError as exc:
        layout.notes.append(f"Port folder unreadable: {exc}")
        return layout

    found = {}          # nama_normal -> (host, nama_asli)
    for name in entries:
        host = os.path.join(port_dir, name)
        if os.path.isdir(host):
            kind = _boot_kind(port_dir, name)
            if kind:
                layout.boot_dirs[name] = host
                continue
            if name == "config":
                continue
            if _is_known_or_oem_partition(name) or _looks_like_partition(host):
                norm = _strip_slot(name)
                prev = found.get(norm)
                if prev is None or (prev[1] != norm and name == norm) \
                        or (prev[1].endswith("_b") and name.endswith("_a")):
                    found[norm] = (host, name)
        elif name.lower().endswith(".img"):
            layout.images[_strip_slot(os.path.splitext(name)[0])] = host

    for norm, (host, original) in found.items():
        layout.partitions[norm] = host
        if original != norm:
            layout.notes.append(f"{original} treated as {norm}")

    mounted_images = options.get("mounted_images")
    if mounted_images:
        items = (
            mounted_images.items()
            if isinstance(mounted_images, dict)
            else [
                (_strip_slot(os.path.splitext(os.path.basename(p))[0]), p)
                for p in mounted_images
                if isinstance(p, str) and p
            ]
        )
        for mname, mpath in items:
            if mpath and os.path.isfile(mpath):
                norm = _strip_slot(str(mname))
                layout.images.setdefault(norm, os.path.normpath(mpath))
                layout.partition_names.add(norm)

    layout.boot_images = _boot_images(port_dir, entries)
    # Inspect boot/vendor_boot/init_boot images directly with Rust
    try:
        from core import native_backend
        for _kind, bpath in layout.boot_images:
            bname = _strip_slot(os.path.splitext(os.path.basename(bpath))[0])
            try:
                rep = native_backend.vdev_inspect_boot(bpath)
                _absorb_boot_report(layout, bname, rep)
            except Exception:
                pass
        for name, ipath in layout.images.items():
            if name.startswith("vbmeta"):
                try:
                    vb = native_backend.vdev_inspect_vbmeta(ipath)
                    if isinstance(vb, dict) and vb.get("valid"):
                        layout.vbmeta_reports[name] = vb
                    else:
                        layout.invalid_vbmeta.append((name, ipath))
                except Exception:
                    layout.invalid_vbmeta.append((name, ipath))
            elif name == "dtbo" and layout.dtbo_report is None:
                try:
                    drep = native_backend.vdev_inspect_dtbo(ipath)
                    _absorb_dtbo_report(layout, drep, source=ipath)
                except Exception:
                    pass
    except Exception:
        pass

    if "super" in layout.images:
        _attach_super_partitions(layout, layout.images["super"], slot=slot)

    layout.partition_names |= derive_partition_names(layout)

    from core.imgfs import detect_fs as _detect_fs

    def _consider(name, path):
        if name in layout.boot_dirs:
            return
        try:
            if not _detect_fs(path):
                return
        except Exception:                 # noqa: BLE001
            return
        if name in layout.partitions:
            layout.ref_images.setdefault(name, path)
            return
        layout.fs_images.setdefault(name, path)

    for name, path in layout.images.items():
        if name in layout.partition_names or _is_known_or_oem_partition(name):
            _consider(name, path)

    _finalize_system_layout(layout)
    for name, path in layout.images.items():
        if name in layout.partition_names:
            _consider(name, path)
    _finalize_system_layout(layout)

    # ── ramdisk first-stage ──
    for name, host in layout.boot_dirs.items():
        kind = _boot_kind(port_dir, name)
        rd = os.path.join(host, "ramdisk")
        if not os.path.isdir(rd):
            continue
        if kind in ("vndrboot", "vendor_boot"):
            layout.ramdisk_dir = rd
            layout.ramdisk_source = name
            break
        if kind == "boot" and not layout.ramdisk_dir:
            layout.ramdisk_dir = rd
            layout.ramdisk_source = name

    if not layout.ramdisk_dir:
        _build_virtual_ramdisk(layout)

    return layout


def _source_for(layout, name):
    cached = layout._sources.get(name)
    if cached is not None:
        layout.sources[name] = cached.label
        return cached
    # Prefer the edited partition folder over its original image.
    host = layout.partitions.get(name)
    if host:
        layout.sources[name] = "folder"
        src = DirSource(host)
        layout._sources[name] = src
        return src
    img = layout.fs_images.get(name)
    if img:
        layout.sources[name] = "image"
        src = ImageSource(img)
        layout._sources[name] = src
        return src
    return None


def _rel_under(vpath, mount_point):

    if mount_point == "/":
        return vpath.lstrip("/")
    if vpath == mount_point:
        return ""
    if vpath.startswith(mount_point + "/"):
        return vpath[len(mount_point) + 1:]
    return None


def partition_mount_point(layout, name):

    return "/" if (name == "system" and layout.sar) else "/" + name


def lost_symlink(layout, vpath):

    refs = getattr(layout, "ref_images", None)
    if not vpath.startswith("/") or not refs:
        return ""
    probe = layout.__dict__.setdefault("_img_probe", {})

    # Partisi paling spesifik lebih dulu; "/" (system SAR) cocok dengan semua
    # path, jadi harus dicoba terakhir supaya tidak menutupi yang lain.
    names = sorted(refs, key=lambda n: -len(partition_mount_point(layout, n)))
    for name in names:
        rel = _rel_under(vpath, partition_mount_point(layout, name))
        if not rel:
            continue
        src = probe.get(name)
        if src is None:
            try:
                src = probe[name] = ImageSource(refs[name])
            except Exception:             # noqa: BLE001 - image rusak, lewati
                probe[name] = False
                continue
        if src is False:
            continue
        try:
            target = src.link_target(rel)
        except Exception:                 # noqa: BLE001
            target = None
        if target:
            return target
    return ""


_APEX_DIRS = ("/system/apex", "/system_ext/apex", "/product/apex", "/vendor/apex")


def mount_apexes(vfs, limit=400):

    import zipfile as _zip
    from core.vdev.source import ApexSource
    from core.vdev.apexdb import MANIFEST, ApexIndex, _read_apex_manifest

    candidates = []
    for d in _APEX_DIRS:
        if not vfs.is_dir(d):
            continue
        for name in sorted(vfs.listdir(d) or []):
            if name.endswith((".apex", ".capex")):
                candidates.append(d + "/" + name)
                if len(candidates) >= limit:
                    break
        if len(candidates) >= limit:
            break

    if not candidates:
        return 0

    # Fast parallel Rust APEX indexer (populates BOTH /apex/<pkg> mounts and ApexIndex in one pass)
    try:
        from core import native_backend
        if native_backend._get_native() is not None:
            by_host = []
            by_erofs = {}
            by_ext = {}
            for vp in candidates:
                node = vfs.lookup(vp)
                if node is None or not node.is_file:
                    continue
                if node.host:
                    by_host.append((vp, node.host))
                elif getattr(node.source, "native_handle", None) is not None:
                    fs_t = node.source.fs_type
                    if fs_t == "EROFS":
                        by_erofs.setdefault(id(node.source), (node.source.native_handle, []))[1].append((vp, node.rel))
                    elif fs_t == "EXT":
                        by_ext.setdefault(id(node.source), (node.source.native_handle, []))[1].append((vp, node.rel))

            summaries = []
            if by_host:
                summaries.extend(native_backend.vdev_index_file_apexes(by_host))
            for handle, items in by_erofs.values():
                summaries.extend(native_backend.vdev_index_erofs_apexes(handle, items))
            for handle, items in by_ext.values():
                summaries.extend(native_backend.vdev_index_ext_apexes(handle, items))

            if summaries:
                idx = ApexIndex()
                mounted = set()
                seen = 0
                for item in summaries:
                    vp = item.get("vpath", "")
                    stem = item.get("stem", "")
                    pkg = item.get("pkg_name") or stem
                    err = item.get("error", "")
                    if pkg:
                        idx.manifests[stem] = {"name": pkg, "version": item.get("version")}
                    if err:
                        idx.failed.append((stem or vp, err))
                        continue
                    idx.apex_count += 1
                    files = item.get("files") or []
                    idx.files[pkg] = set(files)
                    for lib in item.get("libs") or []:
                        idx.libs.setdefault(lib, set()).add(pkg)
                    for pl in item.get("public_libs") or []:
                        idx.public_libs.add(pl)
                    for jar in item.get("classpath_jars") or []:
                        idx.classpath_jars.append(jar)

                    point = "/apex/" + pkg
                    if point not in mounted:
                        mounted.add(point)
                        vfs.apex_manifests[stem] = {"name": pkg, "point": point}
                        vfs.add_mount(point, ApexSource(lambda _vp=vp: vfs.source_ref(_vp), pkg),
                                      "apex:" + pkg)
                        seen += 1
                idx.available = bool(idx.libs) or idx.apex_count > 0
                vfs._preindexed_apex = idx
                return seen
    except Exception:
        pass

    seen = 0
    mounted = set()
    for vp in candidates:
        name = vp.rsplit("/", 1)[-1]
        pkg = ""
        try:
            ref = vfs.source_ref(vp)
            if ref is None:
                continue
            with _zip.ZipFile(ref) as z:
                if MANIFEST in z.namelist():
                    info = _read_apex_manifest(z.read(MANIFEST))
                    pkg = (info or {}).get("name", "")
        except Exception:                # noqa: BLE001 — apex rusak dilewati
            continue
        if not pkg:
            continue
        point = "/apex/" + pkg
        if point in mounted:
            continue
        mounted.add(point)
        stem = name.rsplit(".", 1)[0]
        vfs.apex_manifests[stem] = {"name": pkg, "point": point}
        vfs.add_mount(point, ApexSource(lambda _vp=vp: vfs.source_ref(_vp), pkg),
                      "apex:" + pkg)
        seen += 1
        if seen >= limit:
            return seen
    return seen


def build_vfs(layout):

    vfs = VirtualFS()

    if layout.system_root:
        src = layout._sources.get("system")
        if src is None:
            src = (ImageSource(layout.system_root) if layout.system_from_image
                   else DirSource(layout.system_root))
        layout.sources["system"] = "image" if layout.system_from_image else "folder"
        vfs.add_mount("/" if layout.sar else "/system", src, "system")

    for name in layout.all_partition_names():
        if name == "system":
            continue
        src = _source_for(layout, name)
        if src is not None:
            vfs.add_mount("/" + name, src, name)

    # Tidak ada system sama sekali: pakai folder port sebagai root supaya
    # analisa partisi tunggal (mis. cuma vendor) tetap bisa jalan.
    if not layout.system_root and layout.partitions:
        vfs.add_mount("/", DirSource(layout.port_dir), "port")

    # ── APEX: dipasang seperti apexd memasangnya ──
    mount_apexes(vfs)

    ramdisk_vfs = None
    if layout.ramdisk_dir:
        ramdisk_vfs = VirtualFS()
        ramdisk_vfs.add_mount("/", DirSource(layout.ramdisk_dir),
                              layout.ramdisk_source)
    elif layout.ramdisk_virtual is not None:
        ramdisk_vfs = VirtualFS()
        ramdisk_vfs.add_mount("/", layout.ramdisk_virtual,
                              layout.ramdisk_source or "ramdisk")


    if layout.sar and layout.system_root:
        for name in ("system_ext", "product", "vendor", "odm"):
            src = vfs.partition_source(name)
            if src is None:
                continue
            alias = "/system/" + name
            if vfs.lookup(alias) is None:
                vfs.add_mount(alias, src, name)
                layout.compat_aliases.append(alias)

    return vfs, ramdisk_vfs


# fstab
def parse_fstab(text, origin=""):

    out = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) < 4:
            continue
        source, mount_point, fs_type = parts[0], parts[1], parts[2]
        mnt_flags = parts[3].split(",") if len(parts) > 3 else []
        fs_mgr = parts[4].split(",") if len(parts) > 4 else []
        out.append(FstabEntry(source, mount_point, fs_type,
                              mnt_flags, fs_mgr, raw=line, origin=origin))
    return out


def find_fstab(vfs, ramdisk_vfs, layout):

    # 1) ramdisk vendor_boot / boot
    if ramdisk_vfs is not None:
        for base in ("/first_stage_ramdisk", "/"):
            if not ramdisk_vfs.is_dir(base):
                continue
            for name in ramdisk_vfs.listdir(base):
                if not _FSTAB_RE.match(name):
                    continue
                vp = normalize(base + "/" + name)
                text = ramdisk_vfs.read_text(vp)
                if text:
                    origin = f"{layout.ramdisk_source} ramdisk: {vp}"
                    entries = parse_fstab(text, origin)
                    if entries:
                        return entries, origin

    # 1b) DTB android,fstab entries
    if getattr(layout, "dtb_fstabs", None):
        return list(layout.dtb_fstabs), layout.dtb_fstabs[0].origin

    # 2) /vendor/etc, 3) /odm/etc
    for base in ("/vendor/etc", "/odm/etc"):
        if not vfs.is_dir(base):
            continue
        for name in vfs.listdir(base):
            if not _FSTAB_RE.match(name):
                continue
            vp = normalize(base + "/" + name)
            text = vfs.read_text(vp)
            if text:
                entries = parse_fstab(text, vp)
                if entries:
                    return entries, vp

    return [], ""


def all_fstab_entries(layout, vfs, ramdisk_vfs):

    out = {}

    def _absorb(text):
        for entry in parse_fstab(text):
            name = _strip_slot(entry.partition_name)
            if name and name not in out:
                out[name] = entry
            mp = entry.mount_point.strip("/")
            if mp and "/" not in mp and mp not in out:
                out[mp] = entry

    for _kind, path in layout.boot_images:
        try:
            from core.vdev.bootimg import find_fstab as _boot_fstab
            hit = _boot_fstab(path)
        except Exception:                 # noqa: BLE001
            hit = None
        if hit:
            _absorb(hit[1])

    if layout.ramdisk_dir:
        for base in (os.path.join(layout.ramdisk_dir, "first_stage_ramdisk"),
                     layout.ramdisk_dir):
            if not os.path.isdir(base):
                continue
            for fname in os.listdir(base):
                if not _FSTAB_RE.match(fname):
                    continue
                try:
                    with open(os.path.join(base, fname), "r",
                              encoding="utf-8", errors="ignore") as f:
                        _absorb(f.read())
                except OSError:
                    pass

    for base in ("/vendor/etc", "/odm/etc"):
        if not vfs.is_dir(base):
            continue
        for name in vfs.listdir(base):
            if not _FSTAB_RE.match(name):
                continue
            text = vfs.read_text(normalize(base + "/" + name))
            if text:
                _absorb(text)

    return out
