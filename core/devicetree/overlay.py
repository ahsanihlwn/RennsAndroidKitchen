# SPDX-License-Identifier: MPL-2.0
from __future__ import annotations

import struct as _struct

from .tree import Node, Property, BootInfo
from .data import Data


class OverlayError(RuntimeError):
    pass


def _max_phandle(node: Node, cur: int = 0) -> int:
    ph = node.phandle
    if ph not in (0, 0xFFFFFFFF):
        cur = max(cur, ph)
    for c in node.children:
        cur = _max_phandle(c, cur)
    return cur


def _read_u32(data: bytes, off: int) -> int:
    return _struct.unpack_from(">I", data, off)[0]


def _write_u32(prop: Property, off: int, val: int) -> None:
    _struct.pack_into(">I", prop.val.val, off, val & 0xFFFFFFFF)


def _adjust_phandles(node: Node, delta: int) -> None:

    if node.phandle not in (0, 0xFFFFFFFF):
        node.phandle += delta
    for pname in ("phandle", "linux,phandle"):
        p = node.get_property(pname)
        if p is not None and p.val.len >= 4:
            _write_u32(p, 0, _read_u32(bytes(p.val.val), 0) + delta)
    for c in node.children:
        _adjust_phandles(c, delta)


def _apply_local_fixups(root: Node, delta: int) -> None:

    lf = root.get_node_by_path("/__local_fixups__")
    if lf is None:
        return

    def walk(fixup_node: Node, target_node: Node):
        for prop in fixup_node.proplist:
            tprop = target_node.get_property(prop.name)
            if tprop is None:
                continue
            raw = bytes(prop.val.val)
            for i in range(0, len(raw) - 3, 4):
                off = _read_u32(raw, i)
                if off + 4 <= tprop.val.len:
                    _write_u32(tprop, off, _read_u32(bytes(tprop.val.val), off) + delta)
        for child in fixup_node.children:
            tchild = target_node.get_subnode(child.name)
            if tchild is not None:
                walk(child, tchild)

    walk(lf, root)


def _apply_external_fixups(base: Node, overlay: Node) -> None:

    fixups = overlay.get_node_by_path("/__fixups__")
    if fixups is None:
        return
    symbols = base.get_node_by_path("/__symbols__")
    if symbols is None:
        raise OverlayError("base tree has no /__symbols__ (compile base with -@)")

    for prop in fixups.proplist:
        label = prop.name
        sym = symbols.get_property(label)
        if sym is None:
            raise OverlayError('unresolved overlay reference to "%s"' % label)
        target_path = bytes(sym.val.val).rstrip(b"\0").decode("ascii")
        target = base.get_node_by_path(target_path)
        if target is None or target.phandle in (0, 0xFFFFFFFF):
            raise OverlayError('symbol "%s" -> "%s" has no phandle' % (label, target_path))
        phandle = target.phandle

        # value is a list of NUL-terminated "path:property:offset" entries
        for entry in bytes(prop.val.val).split(b"\0"):
            if not entry:
                continue
            path, pname, off_s = entry.decode("ascii").rsplit(":", 2)
            node = overlay.get_node_by_path(path)
            if node is None:
                raise OverlayError("fixup path not found: %s" % path)
            tprop = node.get_property(pname)
            if tprop is None:
                raise OverlayError("fixup property not found: %s/%s" % (path, pname))
            _write_u32(tprop, int(off_s), phandle)


def _merge_node(target: Node, src: Node) -> None:

    for prop in src.proplist:
        if prop.deleted:
            continue
        existing = target.get_property(prop.name)
        if existing is not None:
            existing.val = prop.val.copy()
        else:
            np = Property(prop.name, prop.val.copy())
            target.add_property(np)
    for child in src.children:
        if child.deleted:
            continue
        tchild = target.get_subnode(child.name)
        if tchild is None:
            tchild = Node()
            tchild.name = child.name
            target.add_child(tchild)
        _merge_node(tchild, child)


def _fragment_target(base: Node, overlay: Node, fragment: Node) -> Node:

    tp = fragment.get_property("target")
    if tp is not None and tp.val.len >= 4:
        phandle = _read_u32(bytes(tp.val.val), 0)
        node = base.get_node_by_phandle(phandle)
        if node is None:
            raise OverlayError("fragment target phandle 0x%x not found" % phandle)
        return node
    tpp = fragment.get_property("target-path")
    if tpp is not None:
        path = bytes(tpp.val.val).rstrip(b"\0").decode("ascii")
        node = base.get_node_by_path(path)
        if node is None:
            raise OverlayError("fragment target-path not found: %s" % path)
        return node
    raise OverlayError("fragment has neither target nor target-path")


def apply_overlay(base: BootInfo, overlay: BootInfo) -> BootInfo:

    broot = base.dt
    oroot = overlay.dt

    # 1. rebase overlay phandles past the base's max, fix internal refs
    delta = _max_phandle(broot)
    if delta:
        _adjust_phandles(oroot, delta)
        _apply_local_fixups(oroot, delta)

    # 2. resolve overlay's external references against base __symbols__
    _apply_external_fixups(broot, oroot)

    # 3. merge each fragment's __overlay__ into its target
    for fragment in oroot.children:
        ov = fragment.get_subnode("__overlay__")
        if ov is None:
            continue
        target = _fragment_target(broot, oroot, fragment)
        _merge_node(target, ov)

    return base
