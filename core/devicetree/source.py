# SPDX-License-Identifier: MPL-2.0
from __future__ import annotations

import struct as _struct

from .tree import BootInfo, Node, Property
from .data import LABEL, REF_PHANDLE, REF_PATH

# type markers (treesource.c enum, extending markertype)
TYPE_NONE = "TYPE_NONE"
TYPE_UINT8 = "TYPE_UINT8"
TYPE_UINT16 = "TYPE_UINT16"
TYPE_UINT32 = "TYPE_UINT32"
TYPE_UINT64 = "TYPE_UINT64"
TYPE_STRING = "TYPE_STRING"

_DELIM_START = {TYPE_UINT8: "[", TYPE_UINT16: "/bits/ 16 <",
                TYPE_UINT32: "<", TYPE_UINT64: "/bits/ 64 <",
                TYPE_STRING: ""}
_DELIM_END = {TYPE_UINT8: "]", TYPE_UINT16: ">", TYPE_UINT32: ">",
              TYPE_UINT64: ">", TYPE_STRING: ""}

_IS_TYPE = {TYPE_UINT8, TYPE_UINT16, TYPE_UINT32, TYPE_UINT64, TYPE_STRING}
_TYPE_WIDTH = {TYPE_UINT8: 1, TYPE_UINT16: 2, TYPE_UINT32: 4, TYPE_UINT64: 8,
               TYPE_STRING: 0}


def _isstring(c: int) -> bool:
    if c == 0:
        return True
    if 0x20 <= c <= 0x7E:
        return True
    return c in (0x07, 0x08, 0x09, 0x0A, 0x0B, 0x0C, 0x0D)


def _write_propval_string(raw: bytes, length: int) -> str:
    s = raw[:length]
    assert s[-1] == 0
    out = ['"']
    for c in s[:-1]:
        if c == 0x07:
            out.append("\\a")
        elif c == 0x08:
            out.append("\\b")
        elif c == 0x09:
            out.append("\\t")
        elif c == 0x0A:
            out.append("\\n")
        elif c == 0x0B:
            out.append("\\v")
        elif c == 0x0C:
            out.append("\\f")
        elif c == 0x0D:
            out.append("\\r")
        elif c == 0x5C:
            out.append("\\\\")
        elif c == 0x22:
            out.append('\\"')
        elif 0x20 <= c <= 0x7E:
            out.append(chr(c))
        else:
            out.append("\\x%02x" % c)
    out.append('"')
    return "".join(out)


def _write_propval_int(raw: bytes, length: int, width: int) -> str:
    out = []
    fmt = {1: ">B", 2: ">H", 4: ">I", 8: ">Q"}[width]
    n = length // width
    for i in range(n):
        v = _struct.unpack_from(fmt, raw, i * width)[0]
        if width == 1:
            out.append("%02x" % v)
        else:
            out.append("0x%02x" % v)
        if i != n - 1:
            out.append(" ")
    return "".join(out)


def _guess_value_type(raw: bytes, offset: int, length: int):

    p = raw[offset:offset + length]
    nnotstring = 0
    nnul = 0
    for c in p:
        if not _isstring(c):
            nnotstring += 1
        if c == 0:
            nnul += 1
    extra = []
    if p[length - 1] == 0 and nnotstring == 0 and nnul <= (length - nnul):
        if nnul > 1:
            # add_string_markers: one TYPE_STRING per extra element
            l = p.index(b"\0"[0]) + 1
            while l < length:
                extra.append(offset + l)
                nxt = p.index(b"\0"[0], l)
                l = nxt + 1
        return TYPE_STRING, extra
    if (length % 4) == 0:
        return TYPE_UINT32, extra
    return TYPE_UINT8, extra


class _M:
    __slots__ = ("type", "offset", "ref")

    def __init__(self, t, off, ref=None):
        self.type = t
        self.offset = off
        self.ref = ref


def _guess_type_markers(prop: Property):

    raw = bytes(prop.val.val)
    length = len(raw)
    # start from existing markers (labels, phandle/path refs), sorted by offset
    existing = sorted(prop.val.markers, key=lambda m: m.offset)
    markers: list[_M] = [_M(m.type, m.offset, m.ref) for m in existing]

    # If a type marker is already present, assume fully marked (rare here).
    if any(m.type in _IS_TYPE for m in markers):
        return markers

    out: list[_M] = []
    offset = 0
    mi = 0
    while mi < len(markers):
        m = markers[mi]
        if m.offset > offset:
            t, extra = _guess_value_type(raw, offset, m.offset - offset)
            out.append(_M(t, offset))
            for e in extra:
                out.append(_M(TYPE_STRING, e))
            offset = m.offset
        out.append(m)
        if m.type == REF_PHANDLE:
            out.append(_M(TYPE_UINT32, offset))
            offset += 4
        mi += 1

    if offset < length:
        t, extra = _guess_value_type(raw, offset, length - offset)
        out.append(_M(t, offset))
        for e in extra:
            out.append(_M(TYPE_STRING, e))

    out.sort(key=lambda mm: (mm.offset, 0 if mm.type in _IS_TYPE else 1))
    return out


def _write_propval(prop: Property) -> str:
    raw = bytes(prop.val.val)
    length = len(raw)
    if length == 0:
        return ";"

    markers = _guess_type_markers(prop)
    parts = [" ="]
    emit_type = TYPE_NONE

    def _type_marker_length(i):
        # distance to the NEXT type marker, or 0 (= to end) if none follows
        for j in range(i + 1, len(markers)):
            if markers[j].type in _IS_TYPE:
                return markers[j].offset - markers[i].offset
        return 0

    for idx, m in enumerate(markers):
        nxt_off = markers[idx + 1].offset if idx + 1 < len(markers) else length
        chunk_len = nxt_off - m.offset
        tml = _type_marker_length(idx)
        data_len = tml if tml else (length - m.offset)

        if m.type in _IS_TYPE:
            emit_type = m.type
            parts.append(" " + _DELIM_START[emit_type])
        elif m.type == LABEL:
            parts.append(" %s:" % m.ref)

        if emit_type == TYPE_NONE or chunk_len == 0:
            continue

        p = raw[m.offset:]
        if emit_type == TYPE_UINT16:
            parts.append(_write_propval_int(p, chunk_len, 2))
        elif emit_type == TYPE_UINT32:
            ph = None
            for mm in markers:
                if mm.type == REF_PHANDLE and mm.offset == m.offset:
                    ph = mm
                    break
            if ph is not None:
                parts.append(("&{%s}" % ph.ref) if ph.ref.startswith("/") else ("&%s" % ph.ref))
                if chunk_len > 4:
                    parts.append(" " + _write_propval_int(p[4:], chunk_len - 4, 4))
            else:
                parts.append(_write_propval_int(p, chunk_len, 4))
            if data_len > chunk_len:
                parts.append(" ")
        elif emit_type == TYPE_UINT64:
            parts.append(_write_propval_int(p, chunk_len, 8))
        elif emit_type == TYPE_STRING:
            parts.append(_write_propval_string(p, chunk_len))
        else:
            parts.append(_write_propval_int(p, chunk_len, 1))

        if chunk_len == data_len:
            pos = m.offset + chunk_len
            end = _DELIM_END.get(emit_type, "") or ""
            parts.append(end if pos == length else (end + ","))
            emit_type = TYPE_NONE

    parts.append(";")
    return "".join(parts)


def _write_tree_source_node(tree: Node, level: int, out: list) -> None:
    prefix = "\t" * level
    labels = "".join("%s: " % l for l in tree.labels)
    if tree.name:
        out.append("%s%s%s {" % (prefix, labels, tree.name))
    else:
        out.append("%s%s/ {" % (prefix, labels))

    for prop in tree.proplist:
        if prop.deleted:
            continue
        plabels = "".join("%s: " % l for l in prop.labels)
        out.append("%s%s%s%s" % ("\t" * (level + 1), plabels, prop.name,
                                 _write_propval(prop)))
    for child in tree.children:
        if child.deleted:
            continue
        out.append("")
        _write_tree_source_node(child, level + 1, out)
    out.append("%s};" % prefix)


def dt_to_source(bi: BootInfo) -> str:
    out = ["/dts-v1/;", ""]
    for re in bi.reservelist:
        labels = "".join("%s: " % l for l in re.labels)
        out.append("%s/memreserve/\t0x%016x 0x%016x;" % (labels, re.address, re.size))
    body = []
    _write_tree_source_node(bi.dt, 0, body)
    out.extend(body)
    return "\n".join(out) + "\n"
