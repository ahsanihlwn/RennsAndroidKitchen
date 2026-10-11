# SPDX-License-Identifier: MPL-2.0
from __future__ import annotations

import os
import posixpath
import zipfile

print("[CORE STAGE] VDEV APEXDB")

PAYLOAD = "apex_payload.img"
MANIFEST = "apex_manifest.pb"


ORIGINAL_APEX = "original_apex"

# Batas kedalaman rekursi capex. Sebuah capex sah hanya membungkus satu lapis;
# lebih dari itu berarti berkas cacat atau sengaja dibuat memutar.
_MAX_NEST = 3

# Nama file daftar library publik di dalam APEX (llndk.libraries.30.txt, …).
_PUBLIC_PREFIXES = ("llndk.libraries", "vndksp.libraries")

# Folder tempat .apex biasa disimpan, relatif ke root virtual.
APEX_DIRS = (
    "/system/apex",
    "/system_ext/apex",
    "/product/apex",
    "/vendor/apex",
)


class ApexIndex:


    def __init__(self):
        self.libs = {}          # "libc.so" -> {"com.android.runtime"}
        self.public_libs = set()  # LL-NDK / VNDK-SP yang sah dipakai lintas partisi
        self.apex_count = 0
        self.failed = []        # (nama apex, alasan)
        self.available = False  # False = indeks gagal dibangun sama sekali


        self.manifests = {}     # nama file apex -> {"name":…, "version":…}
        self.files = {}         # nama paket apex -> set(path di dalam payload)

    def declared_name(self, apex_file):
        return (self.manifests.get(apex_file) or {}).get("name", "")

    def has_path(self, apex_pkg, rel):
        return rel.strip("/") in self.files.get(apex_pkg, ())

    def provides(self, soname):
        return self.libs.get(soname)

    def provider_text(self, soname):
        owners = self.libs.get(soname)
        return ", ".join(sorted(owners)) if owners else ""


def _read_apex_manifest(raw):

    try:
        if not raw or raw[0] != 0x0A:
            return None
        ln = raw[1]
        name = raw[2:2 + ln].decode("utf-8", "replace")
        version = None
        i = 2 + ln
        if i < len(raw) and raw[i] == 0x10:
            v, shift, j = 0, 0, i + 1
            while j < len(raw):
                b = raw[j]
                v |= (b & 0x7F) << shift
                j += 1
                if not b & 0x80:
                    break
                shift += 7
            version = v
        return {"name": name, "version": version}
    except (IndexError, UnicodeDecodeError):
        return None


def _index_one(zpath, index, name=None, depth=0):
    if name is None:
        name = os.path.splitext(os.path.basename(zpath))[0]
    if isinstance(zpath, (bytes, bytearray, memoryview)):
        import io as _io
        zpath = _io.BytesIO(zpath)
    try:
        with zipfile.ZipFile(zpath) as z:
            names = z.namelist()
            if MANIFEST in names:
                info = _read_apex_manifest(z.read(MANIFEST))
                if info:
                    index.manifests[name] = info
            if PAYLOAD not in names:
                if ORIGINAL_APEX in names and depth < _MAX_NEST:


                    inner = z.read(ORIGINAL_APEX)
                    _index_one(inner, index, name, depth + 1)
                    return
                index.failed.append((name, "no apex_payload.img"))
                return
            with z.open(PAYLOAD) as f:
                if not f.seekable():
                    index.failed.append((name, "payload is not seekable"))
                    return
                from core.imgfs import ExtReader, ErofsReader, _detect_fs_bytes
                head = f.read(8192)
                f.seek(0)
                fs = _detect_fs_bytes(head)
                if fs == "EXT":
                    reader = ExtReader(f)
                elif fs == "EROFS":
                    reader = ErofsReader(f)
                else:
                    index.failed.append((name, f"unknown payload fs ({fs or '?'})"))
                    return
                pkg = index.manifests.get(name, {}).get("name") or name
                paths = index.files.setdefault(pkg, set())
                for node in reader.walk():
                    p = node.path
                    paths.add(p.strip("/"))
                    if p.endswith(".so"):
                        base = posixpath.basename(p)
                        index.libs.setdefault(base, set()).add(name)
                        continue


                    base = posixpath.basename(p)
                    if base.startswith(_PUBLIC_PREFIXES) and base.endswith(".txt"):
                        try:
                            raw = reader.read_file(node.ino)
                        except Exception:
                            continue
                        for line in raw.decode("utf-8", "replace").splitlines():
                            line = line.split("#", 1)[0].strip()
                            if line.endswith(".so"):
                                index.public_libs.add(line)
    except Exception as exc:                       # noqa: BLE001 - best effort
        index.failed.append((name, str(exc)))
        return
    index.apex_count += 1


def _index_from_mounts(vfs, index):

    if not vfs.is_dir("/apex"):
        return False
    pkgs = [p for p in (vfs.listdir("/apex") or []) if vfs.is_dir("/apex/" + p)]
    if not pkgs:
        return False

    for pkg in pkgs:
        base = "/apex/" + pkg
        prefix_len = len(base) + 1
        paths = index.files.setdefault(pkg, set())
        for vp in vfs.walk_files(base, limit=20000):
            rel = vp[prefix_len:]
            paths.add(rel)
            name = rel.rsplit("/", 1)[-1]
            if name.endswith(".so"):
                index.libs.setdefault(name, set()).add(pkg)
            elif name.startswith(_PUBLIC_PREFIXES) and name.endswith(".txt"):
                raw = vfs.read_bytes(vp)
                if not raw:
                    continue
                for line in raw.decode("utf-8", "replace").splitlines():
                    line = line.split("#", 1)[0].strip()
                    if line.endswith(".so"):
                        index.public_libs.add(line)
        index.apex_count += 1

    # Manifest sudah dibaca saat mounting; dipakai ulang apa adanya.
    for stem, info in (getattr(vfs, "apex_manifests", None) or {}).items():
        index.manifests[stem] = {"name": info.get("name", ""), "version": None}
    return True


def build_index(vfs, limit=400):

    index = ApexIndex()

    # Jalur cepat: APEX sudah dipasang di /apex, tinggal dipungut dari sana.
    if _index_from_mounts(vfs, index):
        index.available = bool(index.libs)
        return index

    # Jalur lama: tidak ada mount (mis. APEX sudah ter-ekstrak jadi folder,
    # atau vfs dibangun tanpa mount_apexes). Buka berkas .apex sendiri.
    seen = 0
    for vdir in APEX_DIRS:
        if not vfs.is_dir(vdir):
            continue
        for vp in vfs.glob(vdir):
            base = posixpath.basename(vp)
            if not base.endswith((".apex", ".capex")):
                continue
            ref = vfs.source_ref(vp)
            if ref is None:
                continue
            _index_one(ref, index, base.rsplit(".", 1)[0])
            seen += 1
            if seen >= limit:
                break
        # APEX yang sudah ter-ekstrak (flattened) — langsung baca foldernya.
        for name in vfs.listdir(vdir):
            sub = posixpath.join(vdir, name)
            if not vfs.is_dir(sub):
                continue
            for libdir in ("lib", "lib64"):
                d = posixpath.join(sub, libdir)
                if vfs.is_dir(d):
                    for f in vfs.walk_files(d, limit=4000):
                        if f.endswith(".so"):
                            index.libs.setdefault(
                                posixpath.basename(f), set()).add(name)
                    index.apex_count += 1

    index.available = bool(index.libs)
    return index
