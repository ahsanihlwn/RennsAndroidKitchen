# SPDX-License-Identifier: MPL-2.0
from __future__ import annotations

from .data import Data, empty_data


class Property:
    __slots__ = ("name", "val", "labels", "deleted")

    def __init__(self, name: str, val: Data, deleted: bool = False):
        self.name = name
        self.val = val
        self.labels: list[str] = []
        self.deleted = deleted


class Node:
    __slots__ = ("name", "proplist", "children", "parent", "labels",
                 "phandle", "deleted", "fullpath", "basenamelen")

    def __init__(self):
        self.name: str | None = None
        self.proplist: list[Property] = []
        self.children: list[Node] = []
        self.parent: Node | None = None
        self.labels: list[str] = []
        self.phandle: int = 0
        self.deleted = False
        self.fullpath: str | None = None
        self.basenamelen: int = 0

    # builders (livetree.c)
    def add_property(self, prop: Property) -> None:
        self.proplist.append(prop)

    def add_child(self, child: "Node") -> None:
        child.parent = self
        self.children.append(child)

    def get_property(self, name: str) -> Property | None:
        for p in self.proplist:
            if not p.deleted and p.name == name:
                return p
        return None

    def delete_property_by_name(self, name: str) -> None:
        for p in self.proplist:
            if p.name == name:
                p.deleted = True

    # path / label / phandle lookup
    def get_subnode(self, nodename: str) -> "Node | None":
        for c in self.children:
            if not c.deleted and c.name == nodename:
                return c
        return None

    def get_node_by_path(self, path: str) -> "Node | None":
        if not path:
            return None if self.deleted else self
        while path.startswith("/"):
            path = path[1:]
        if not path:
            return None if self.deleted else self
        slash = path.find("/")
        for child in self.children:
            if child.deleted:
                continue
            if slash >= 0 and child.name == path[:slash]:
                return child.get_node_by_path(path[slash + 1:])
            if slash < 0 and child.name == path:
                return child
        return None

    def get_node_by_label(self, label: str) -> "Node | None":
        for l in self.labels:
            if l == label:
                return None if self.deleted else self
        for child in self.children:
            n = child.get_node_by_label(label)
            if n:
                return n
        return None

    def get_node_by_phandle(self, phandle: int) -> "Node | None":
        if self.phandle == phandle:
            return None if self.deleted else self
        for child in self.children:
            n = child.get_node_by_phandle(phandle)
            if n:
                return n
        return None

    def get_node_by_ref(self, ref: str) -> "Node | None":
        if ref.startswith("/"):
            return self.get_node_by_path(ref)
        return self.get_node_by_label(ref)


class ReserveInfo:
    __slots__ = ("address", "size", "labels")

    def __init__(self, address: int, size: int):
        self.address = address
        self.size = size
        self.labels: list[str] = []


class BootInfo:
    __slots__ = ("reservelist", "dt", "boot_cpuid_phys")

    def __init__(self, reservelist, dt: Node, boot_cpuid_phys: int):
        self.reservelist: list[ReserveInfo] = reservelist or []
        self.dt = dt
        self.boot_cpuid_phys = boot_cpuid_phys


# name_node / nodename_from_path (flattree.c helper)
def nodename_from_path(ppath: str, cpath: str) -> str:
    plen = len(ppath)
    if cpath[:plen] != ppath:
        raise ValueError('Path "%s" is not valid as a child of "%s"' % (cpath, ppath))
    if ppath != "/":
        plen += 1
    return cpath[plen:]


def _join_path(prefix: str, name: str) -> str:
    if prefix == "":
        return "/" if name == "" else "/" + name
    if prefix == "/":
        return "/" + name
    return prefix + "/" + name


def fill_fullpaths(tree: Node, prefix: str) -> None:

    name = tree.name or ""
    tree.fullpath = _join_path(prefix, name)
    at = name.find("@")
    tree.basenamelen = at if at >= 0 else len(name)
    for child in tree.children:
        fill_fullpaths(child, tree.fullpath)


def get_node_phandle(root: Node, node: Node, counter: list) -> int:

    if node.phandle not in (0, 0xFFFFFFFF):
        return node.phandle
    ph = counter[0]
    while root.get_node_by_phandle(ph):
        ph += 1
    counter[0] = ph
    node.phandle = ph
    if not node.get_property("phandle"):
        from .data import empty_data
        node.add_property(Property("phandle", empty_data().append_cell(ph)))
    return node.phandle


def fixup_references(root: Node) -> None:

    import struct as _s
    from .data import REF_PHANDLE, REF_PATH, Marker
    counter = [1]

    def walk(node: Node):
        for prop in node.proplist:
            if prop.deleted:
                continue
            # phandle refs first (fixed width, no length change)
            for m in prop.val.markers:
                if m.type == REF_PHANDLE:
                    refnode = root.get_node_by_ref(m.ref)
                    if refnode is None:
                        raise ValueError('Reference to non-existent node or label "%s"' % m.ref)
                    ph = get_node_phandle(root, refnode, counter)
                    _s.pack_into(">I", prop.val.val, m.offset, ph)
            # path refs: insert path string at marker (length changes), process
            # in increasing offset with running shift
            path_markers = [m for m in prop.val.markers if m.type == REF_PATH]
            shift = 0
            for m in sorted(path_markers, key=lambda mm: mm.offset):
                refnode = root.get_node_by_ref(m.ref)
                if refnode is None:
                    raise ValueError('Reference to non-existent node or label "%s"' % m.ref)
                path = (refnode.fullpath or "").encode("ascii") + b"\0"
                at = m.offset + shift
                prop.val.val[at:at] = path
                # shift later markers
                for mm in prop.val.markers:
                    if mm is not m and mm.offset >= m.offset:
                        mm.offset += len(path)
                shift += len(path)
        for child in node.children:
            if not child.deleted:
                walk(child)

    walk(root)


def assign_explicit_phandles(tree: Node) -> None:

    import struct as _s
    for pname in ("phandle", "linux,phandle"):
        p = tree.get_property(pname)
        if p is not None and p.val.len >= 4:
            tree.phandle = _s.unpack_from(">I", bytes(p.val.val), 0)[0]
            break
    for child in tree.children:
        assign_explicit_phandles(child)


def guess_boot_cpuid(tree: Node) -> int:

    cpus = tree.get_node_by_path("/cpus")
    if not cpus:
        return 0
    bootcpu = None
    for c in cpus.children:
        if not c.deleted:
            bootcpu = c
            break
    if not bootcpu:
        return 0
    reg = bootcpu.get_property("reg")
    if not reg or reg.val.len < 4:
        return 0
    import struct as _s
    return _s.unpack_from(">I", bytes(reg.val.val), 0)[0]
