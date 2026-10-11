# SPDX-License-Identifier: MPL-2.0
from __future__ import annotations

import os

print("[CORE STAGE] VDEV SOURCE")

KIND_DIR = "dir"
KIND_FILE = "file"

_MAX_LINK_SIZE = 4096


class DirSource:


    kind = "dir"

    def __init__(self, root):
        self.root = os.path.normpath(root)
        self._cache = {}

    def __repr__(self):
        return f"<DirSource {self.root}>"

    @property
    def label(self):
        return "folder"

    def _abs(self, rel):
        if not rel:
            return self.root
        return os.path.join(self.root, rel.replace("/", os.sep))

    def entries(self, rel_dir):
        hit = self._cache.get(rel_dir)
        if hit is not None:
            return hit
        out = {}
        try:
            with os.scandir(self._abs(rel_dir)) as it:
                for e in it:
                    try:
                        if e.is_dir():
                            out[e.name] = (KIND_DIR, 0)
                        else:
                            out[e.name] = (KIND_FILE, e.stat().st_size)
                    except OSError:
                        out[e.name] = (KIND_FILE, 0)
        except OSError:
            pass
        self._cache[rel_dir] = out
        return out

    def read(self, rel, limit=-1):
        try:
            with open(self._abs(rel), "rb") as f:
                return f.read() if limit < 0 else f.read(limit)
        except OSError:
            return None

    def open(self, rel):

        try:
            return open(self._abs(rel), "rb")
        except OSError:
            return None

    def selinux(self, rel):

        return ""

    def link_target(self, rel, size=None):


        if size is not None and size > _MAX_LINK_SIZE:
            return None
        from core.vdev.vfs import read_link_target
        return read_link_target(self._abs(rel))

    def host_path(self, rel):
        return self._abs(rel)

    def is_root_dir(self):
        return os.path.isdir(self.root)


class ImageSource:


    kind = "image"

    def __init__(self, img_path):
        self.img_path = os.path.normpath(img_path)
        self._reader = None
        self._fs = ""
        self._opened = False
        self._failed = False
        self.error = ""
        self._dir_ino = {}       # rel_dir -> inode/nid, sudah ter-resolve
        self._dir_full = {}      # rel_dir -> {nama: (kind, size, child_id, is_link)}
        self._dir_pub = {}       # rel_dir -> {nama: (kind, size)}  — cache publik

    def __repr__(self):
        return f"<ImageSource {os.path.basename(self.img_path)}>"

    @property
    def label(self):
        return "image"

    # ── buka reader sekali (murah: cuma superblock, bukan tree) ──
    def _ensure_reader(self):
        if self._opened or self._failed:
            return
        self._opened = True
        try:
            from core.imgfs import open_reader
            self._fs, self._reader = open_reader(self.img_path)
        except Exception as exc:            # noqa: BLE001 — image rusak itu wajar
            self._failed = True
            self.error = str(exc)

    def _root_id(self):
        if self._fs == "EROFS":
            return self._reader.root_nid
        return 2                            # ROOT_INO ext2/3/4, baku

    def _iter_children(self, ino):

        from core.imgfs import S_IFMT, S_IFDIR, S_IFLNK
        reader = self._reader
        if self._fs == "EROFS":
            inode = reader._read_inode(ino)
            for child_nid, name, _ftype in reader._iter_dir(inode):
                cinode = reader._read_inode(child_nid)
                mode = cinode["mode"]
                is_dir = (mode & S_IFMT) == S_IFDIR
                is_link = (mode & S_IFMT) == S_IFLNK
                yield (child_nid, name, KIND_DIR if is_dir else KIND_FILE,
                      cinode["size"], is_link)
        else:
            for child_ino, name, _ftype in reader._iter_dir(ino):
                mode, size, _flags, _iblock, _raw = reader._read_inode(child_ino)
                is_dir = (mode & S_IFMT) == S_IFDIR
                is_link = (mode & S_IFMT) == S_IFLNK
                yield (child_ino, name, KIND_DIR if is_dir else KIND_FILE,
                      size, is_link)

    def _resolve_dir_id(self, rel_dir):

        cached = self._dir_ino.get(rel_dir)
        if cached is not None:
            return cached
        if rel_dir == "":
            rid = self._root_id()
            self._dir_ino[""] = rid
            return rid
        parent, _, name = rel_dir.rpartition("/")
        ent = self._full_entries(parent).get(name)
        if ent is None or ent[0] != KIND_DIR:
            return None
        rid = ent[2]
        self._dir_ino[rel_dir] = rid
        return rid

    def _full_entries(self, rel_dir):

        cached = self._dir_full.get(rel_dir)
        if cached is not None:
            return cached
        out = {}
        self._ensure_reader()
        if self._reader is not None:
            rid = self._resolve_dir_id(rel_dir)
            if rid is not None:
                try:
                    for child_id, name, kind, size, is_link in self._iter_children(rid):
                        out[name] = (kind, size, child_id, is_link)
                except Exception:           # noqa: BLE001 — satu direktori korup
                    pass                     # tidak boleh menjatuhkan seluruh partisi
        self._dir_full[rel_dir] = out
        return out

    def entries(self, rel_dir):
        rel_dir = rel_dir.strip("/")
        cached = self._dir_pub.get(rel_dir)
        if cached is not None:
            return cached
        full = self._full_entries(rel_dir)
        out = {name: (kind, size) for name, (kind, size, _id, _link) in full.items()}
        self._dir_pub[rel_dir] = out
        return out

    def read(self, rel, limit=-1):
        rel = rel.strip("/")
        if not rel:
            return None
        parent, _, name = rel.rpartition("/")
        ent = self._full_entries(parent).get(name)
        if ent is None or ent[0] != KIND_FILE or self._reader is None:
            return None
        try:
            data = self._reader.read_file(ent[2])
        except Exception:                   # noqa: BLE001
            return None
        if data is None:
            return None
        return data if limit < 0 else data[:limit]

    def open(self, rel):

        rel = rel.strip("/")
        if not rel:
            return None
        parent, _, name = rel.rpartition("/")
        ent = self._full_entries(parent).get(name)
        if ent is None or ent[0] != KIND_FILE or self._reader is None:
            return None
        opener = getattr(self._reader, "open_file", None)
        if opener is None:
            return None
        try:
            return opener(ent[2])
        except Exception:                   # noqa: BLE001
            return None

    def link_target(self, rel, size=None):
        # Image menyimpan symlink asli — targetnya langsung tersedia, tidak
        # perlu menebak placeholder seperti di folder unpack.
        rel = rel.strip("/")
        if not rel:
            return None
        parent, _, name = rel.rpartition("/")
        ent = self._full_entries(parent).get(name)
        if ent is None or not ent[3] or self._reader is None:
            return None
        try:
            if self._fs == "EROFS":
                inode = self._reader._read_inode(ent[2])
                target = self._reader._read_symlink(inode)
            else:
                target = self._reader._read_symlink_target(ent[2])
        except Exception:                   # noqa: BLE001
            return None
        return target.replace("\\", "/") if target else None

    def selinux(self, rel):

        rel = rel.strip("/")
        if not rel:
            return ""
        self._ensure_reader()
        if self._reader is None:
            return ""
        parent, _, name = rel.rpartition("/")
        ent = self._full_entries(parent).get(name)
        if ent is None:
            return ""
        node_id = ent[2]
        try:
            if self._fs == "EROFS":
                inode = self._reader._read_inode(node_id)
                return self._reader._inode_selinux(inode) or ""
            _mode, _size, _flags, _ib, raw = self._reader._read_inode(node_id)
            return self._reader._inode_selinux(raw) or ""
        except Exception:                    # noqa: BLE001 - xattr rusak
            return ""

    def host_path(self, rel):
        return None                          # isinya di dalam image

    def is_root_dir(self):


        self._ensure_reader()
        return not self._failed


class ApexSource(ImageSource):


    kind = "apex"

    def __init__(self, ref_factory, pkg):


        super().__init__(pkg)
        self.pkg = pkg
        self._ref_factory = ref_factory
        self._zips = []          # ditahan supaya stream payload tetap hidup

    def __repr__(self):
        return f"<ApexSource {self.pkg}>"

    @property
    def label(self):
        return "apex"

    def _ensure_reader(self):
        if self._opened or self._failed:
            return
        self._opened = True
        try:
            import io as _io
            import zipfile as _zip
            from core.imgfs import _detect_fs_bytes, ExtReader, ErofsReader

            PAYLOAD = "apex_payload.img"
            ORIGINAL = "original_apex"

            ref = self._ref_factory()
            if ref is None:
                self._failed = True
                self.error = "apex unreadable"
                return

            z = _zip.ZipFile(ref)
            self._zips.append(z)
            names = z.namelist()

            if PAYLOAD not in names:
                if ORIGINAL not in names:
                    self._failed = True
                    self.error = "no apex_payload.img"
                    return
                # APEX terkompresi: .apex utuh ada DI DALAM, harus dibuka dulu.
                inner = _zip.ZipFile(_io.BytesIO(z.read(ORIGINAL)))
                self._zips.append(inner)
                if PAYLOAD not in inner.namelist():
                    self._failed = True
                    self.error = "compressed apex without payload"
                    return
                z = inner

            f = z.open(PAYLOAD)
            if not f.seekable():


                f = _io.BytesIO(z.read(PAYLOAD))

            head = f.read(8192)
            f.seek(0)
            fs = _detect_fs_bytes(head)
            if fs == "EXT":
                self._fs, self._reader = "EXT", ExtReader(f)
            elif fs == "EROFS":
                self._fs, self._reader = "EROFS", ErofsReader(f)
            else:
                self._failed = True
                self.error = f"unknown payload fs ({fs or '?'})"
        except Exception as exc:             # noqa: BLE001 — satu apex rusak
            self._failed = True              # tidak boleh menjatuhkan analisa
            self.error = str(exc)

    def host_path(self, rel):
        return None

    def is_root_dir(self):

        return True
