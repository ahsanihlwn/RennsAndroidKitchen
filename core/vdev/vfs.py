# SPDX-License-Identifier: MPL-2.0
from __future__ import annotations

import os
import posixpath

print("[CORE STAGE] VDEV VFS")

_SYMLINK_MAGIC = b"!<symlink>"
_SYMLINK_MAGIC_UTF16 = b"!\x00<\x00s\x00y\x00m\x00l\x00i\x00n\x00k\x00>\x00"
_MAX_LINK_SIZE = 4096
_MAX_LINK_HOPS = 40

KIND_DIR = "dir"
KIND_FILE = "file"


class VNode:


    __slots__ = ("vpath", "source", "rel", "kind", "partition", "size")

    def __init__(self, vpath, source, rel, kind, partition="", size=0):
        self.vpath = vpath
        self.source = source
        self.rel = rel
        self.kind = kind
        self.partition = partition
        self.size = size

    @property
    def host(self):

        return self.source.host_path(self.rel) if self.source else None

    @property
    def in_image(self):
        return self.source is not None and self.source.kind == "image"

    @property
    def is_dir(self):
        return self.kind == KIND_DIR

    @property
    def is_file(self):
        return self.kind == KIND_FILE

    def __repr__(self):
        return f"<VNode {self.vpath} ({self.kind}) @ {self.partition}>"


class Mount:
    __slots__ = ("point", "source", "partition")

    def __init__(self, point, source, partition):
        self.point = normalize(point)
        self.source = source
        self.partition = partition

    @property
    def host(self):
        return self.source.host_path("")

    def __repr__(self):
        return f"<Mount {self.point} = {self.partition} ({self.source.label})>"


def normalize(vpath):

    if not vpath:
        return "/"
    if (vpath.startswith("/") and "\\" not in vpath and "//" not in vpath
            and "/." not in vpath and not vpath.endswith("/")):
        return vpath                      # sudah rapi
    p = vpath.replace("\\", "/")
    if not p.startswith("/"):
        p = "/" + p
    p = posixpath.normpath(p)
    return "/" if p == "." else p


def vpath_under(vpath, point):

    if point == "/":
        return True
    return vpath == point or vpath.startswith(point + "/")


def read_link_target(host_path):

    try:
        if os.path.getsize(host_path) > _MAX_LINK_SIZE:
            return None
        with open(host_path, "rb") as f:
            raw = f.read(_MAX_LINK_SIZE)
    except OSError:
        return None

    if raw.startswith(_SYMLINK_MAGIC):
        rest = raw[len(_SYMLINK_MAGIC):]
    elif raw.startswith(_SYMLINK_MAGIC_UTF16):
        rest = raw[len(_SYMLINK_MAGIC_UTF16):]
    elif raw.startswith(b"\xff\xfe") and _SYMLINK_MAGIC in raw:
        rest = raw[raw.find(_SYMLINK_MAGIC) + len(_SYMLINK_MAGIC):]
    else:
        return None

    if rest.startswith(b"\xff\xfe"):
        target = rest[2:].decode("utf-16le", errors="ignore")
    elif rest.startswith(b"\xfe\xff"):
        target = rest[2:].decode("utf-16be", errors="ignore")
    elif rest.startswith(b"\xef\xbb\xbf"):
        target = rest[3:].decode("utf-8", errors="ignore")
    elif len(rest) >= 2 and rest[1:2] == b"\x00":
        target = rest.decode("utf-16le", errors="ignore")
    else:
        target = rest.decode("utf-8", errors="ignore")

    target = target.replace("\x00", "").strip()
    return target.replace("\\", "/") or None


class VirtualFS:


    def __init__(self):
        self.mounts = []
        self._by_point = {}         # vpath mount point -> Mount
        self._lookup_cache = {}     # vpath -> VNode | None
        self.link_hops = 0          # statistik, dilaporkan di UI


        self.apex_manifests = {}

    # konstruksi
    def add_mount(self, point, source, partition):

        m = Mount(point, source, partition)
        self.mounts.append(m)


        if source.is_root_dir():
            self._by_point[m.point] = m
        # Mount point terpanjang menang — urutkan sekali di sini supaya
        # _mount_for() tinggal ambil yang pertama cocok.
        self.mounts.sort(key=lambda m: len(m.point), reverse=True)
        self._lookup_cache.clear()

    def has_partition(self, name):
        return any(m.partition == name for m in self.mounts)

    def partition_host(self, name):
        for m in self.mounts:
            if m.partition == name:
                return m.host
        return None

    def partition_source(self, name):
        for m in self.mounts:
            if m.partition == name:
                return m.source
        return None

    def mount_points(self):
        return [m.point for m in self.mounts]

    # internal
    def _mount_for(self, vpath):
        for m in self.mounts:
            if m.point != "/" and vpath_under(vpath, m.point):
                return m
        for m in self.mounts:
            if m.point == "/":
                return m
        return None

    def _to_rel(self, vpath):

        m = self._mount_for(vpath)
        if m is None:
            return None
        rel = vpath if m.point == "/" else vpath[len(m.point):]
        return m, rel.strip("/")


    # API utama
    def lookup(self, vpath, _hops=0):

        vpath = normalize(vpath)
        if _hops == 0 and vpath in self._lookup_cache:
            return self._lookup_cache[vpath]
        if _hops > _MAX_LINK_HOPS:
            return None

        result = self._resolve(vpath, _hops)
        if _hops == 0:
            self._lookup_cache[vpath] = result
        return result

    def _resolve(self, vpath, hops):
        if vpath == "/":
            hit = self._to_rel("/")
            if hit is None:
                return None
            m, _rel = hit
            if not m.source.is_root_dir():
                return None
            return VNode("/", m.source, "", KIND_DIR, m.partition)

        parent_v, name = posixpath.split(vpath)

        # Mount point selalu ada, walau folder induknya kosong di sumber lain —
        # kernel yang bikin. Jawab lebih dulu.
        m = self._by_point.get(vpath)
        if m is not None:
            return VNode(vpath, m.source, "", KIND_DIR, m.partition)

        parent = self.lookup(parent_v, hops + 1)
        if parent is None or not parent.is_dir:
            return None


        if parent.vpath != parent_v:
            return self.lookup(posixpath.join(parent.vpath, name), hops + 1)

        entry = parent.source.entries(parent.rel).get(name)   # case-exact
        if entry is None:
            return None
        kind, size = entry

        rel = f"{parent.rel}/{name}" if parent.rel else name

        if kind == KIND_FILE:
            target = parent.source.link_target(rel, size)
            if target is not None:
                self.link_hops += 1
                dest = (normalize(target) if target.startswith("/")
                        else normalize(posixpath.join(parent_v, target)))
                return self.lookup(dest, hops + 1)
            return VNode(vpath, parent.source, rel, KIND_FILE,
                         parent.partition, size)

        return VNode(vpath, parent.source, rel, KIND_DIR, parent.partition)

    # helper baca
    def exists(self, vpath):
        return self.lookup(vpath) is not None

    def is_dir(self, vpath):
        n = self.lookup(vpath)
        return n is not None and n.is_dir

    def is_file(self, vpath):
        n = self.lookup(vpath)
        return n is not None and n.is_file

    def listdir(self, vpath):

        node = self.lookup(vpath)
        names = set()
        if node is not None and node.is_dir:
            names.update(node.source.entries(node.rel).keys())
        base = normalize(vpath)
        prefix = "/" if base == "/" else base + "/"
        for m in self.mounts:
            if m.point != base and m.point.startswith(prefix):
                child = m.point[len(prefix):].split("/", 1)[0]
                if child:
                    names.add(child)
        return sorted(names)

    def glob(self, vdir, suffix=""):

        out = []
        for name in self.listdir(vdir):
            if suffix and not name.endswith(suffix):
                continue
            p = normalize(posixpath.join(vdir, name))
            if self.is_file(p):
                out.append(p)
        return out

    def walk_files(self, vdir, limit=100000):

        out = []
        stack = [normalize(vdir)]
        seen = set()
        while stack and len(out) < limit:
            cur = stack.pop()
            if cur in seen:
                continue
            seen.add(cur)
            node = self.lookup(cur)
            if node is None or not node.is_dir:
                continue
            for name in self.listdir(cur):
                p = normalize(posixpath.join(cur, name))
                child = self.lookup(p)
                if child is None:
                    continue
                if child.is_dir:
                    stack.append(p)
                else:
                    out.append(p)
                    if len(out) >= limit:
                        break
        return out

    def read_bytes(self, vpath, limit=-1):
        node = self.lookup(vpath)
        if node is None or not node.is_file:
            return None
        return node.source.read(node.rel, limit)

    def selinux(self, vpath):

        node = self.lookup(vpath)
        if node is None:
            return ""
        fn = getattr(node.source, "selinux", None)
        if fn is None:
            return ""
        try:
            return fn(node.rel) or ""
        except Exception:                    # noqa: BLE001
            return ""

    def source_ref(self, vpath):

        node = self.lookup(vpath)
        if node is None or not node.is_file:
            return None
        host = node.host
        if host is not None:
            return host
        opener = getattr(node.source, "open", None)
        if opener is not None:
            fh = opener(node.rel)
            if fh is not None:
                return fh
        return node.source.read(node.rel)

    def read_text(self, vpath, limit=-1):
        raw = self.read_bytes(vpath, limit)
        if raw is None:
            return None
        return raw.decode("utf-8", errors="replace")
