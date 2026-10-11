# SPDX-License-Identifier: MPL-2.0
from __future__ import annotations

import os
import re

from core.vdev.source import DirSource, ImageSource
from core.vdev.vfs import VirtualFS, normalize

print("[CORE STAGE] VDEV MOUNTS")


KNOWN_PARTITIONS = {
    "system", "system_ext", "product", "vendor", "odm", "oem", "cust",
    "odm_dlkm", "vendor_dlkm", "system_dlkm", "mi_ext", "preload", "prism",
    "optics", "my_product", "my_engineering", "my_stock", "my_heytap",
    "my_carrier", "my_region", "my_bigball", "my_manifest", "my_company",
    "oppo_product", "opproduct", "special_preload", "vendor_boot", "boot",
    "init_boot", "recovery", "dtbo", "vbmeta", "vbmeta_system",
}

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
        if base in KNOWN_PARTITIONS:
            return base
    return name


def _boot_kind(port_dir, name):

    p = os.path.join(_config_dir(port_dir), f"{name}_bootconf")
    try:
        with open(p, "r", encoding="utf-8", errors="ignore") as f:
            return f.read().strip().lower()
    except OSError:
        return ""


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


def scan_layout(port_dir):

    layout = Layout(port_dir)
    port_dir = layout.port_dir

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
            if name in KNOWN_PARTITIONS or _looks_like_partition(host):
                norm = _strip_slot(name)
                # Folder tanpa suffix slot selalu menang atas system_a/system_b;
                # antar slot, _a yang dipakai (slot aktif pada mayoritas dump).
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


    layout.boot_images = _boot_images(port_dir, entries)
    layout.partition_names = derive_partition_names(layout)

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
        if name in layout.partition_names:
            _consider(name, path)


    vnd_src = None
    if "vendor" in layout.partitions:
        vnd_src = DirSource(layout.partitions["vendor"])
    elif "vendor" in layout.fs_images:
        vnd_src = ImageSource(layout.fs_images["vendor"])
    elif "vendor" in layout.images:
        try:
            if _detect_fs(layout.images["vendor"]):
                vnd_src = ImageSource(layout.images["vendor"])
                layout.fs_images["vendor"] = layout.images["vendor"]
        except Exception:                 # noqa: BLE001
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
        except Exception:                 # noqa: BLE001
            pass
        for name, path in layout.images.items():
            if name in layout.partition_names:
                _consider(name, path)

    sys_src = None
    if "system" in layout.partitions:
        sys_src = DirSource(layout.partitions["system"])
    elif "system" in layout.fs_images:
        sys_src = ImageSource(layout.fs_images["system"])
    if sys_src is not None:


        layout._sources["system"] = sys_src
        root = _names_from_mountpoints(sys_src)
        if root:
            layout.partition_names |= root
            for name, path in layout.images.items():
                if name in root:
                    _consider(name, path)

    # ── SAR? ──
    sysdir = layout.partitions.get("system")
    if sysdir:
        if os.path.isfile(os.path.join(sysdir, "system", "build.prop")):
            layout.sar = True
            layout.system_root = sysdir
        elif os.path.isfile(os.path.join(sysdir, "build.prop")):
            layout.sar = False
            layout.system_root = sysdir
        else:
            # Tidak ada build.prop di dua-duanya — tebak dari adanya subfolder
            # system/, karena itu bentuk paling umum sekarang.
            layout.sar = os.path.isdir(os.path.join(sysdir, "system"))
            layout.system_root = sysdir
    elif "system" in layout.fs_images:
        # system belum di-unpack: baca langsung dari image-nya.
        layout.system_root = layout.fs_images["system"]
        layout.system_from_image = True
        src = ImageSource(layout.system_root)
        layout.sar = "system" in src.entries("")

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
        return DirSource(host)
    img = layout.fs_images.get(name)
    if img:
        layout.sources[name] = "image"
        return ImageSource(img)
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
    from core.vdev.apexdb import MANIFEST, _read_apex_manifest

    seen = 0


    mounted = set()
    for d in _APEX_DIRS:
        if not vfs.is_dir(d):
            continue
        for name in sorted(vfs.listdir(d) or []):
            if not name.endswith((".apex", ".capex")):
                continue
            vp = d + "/" + name
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
