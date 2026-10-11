# SPDX-License-Identifier: MPL-2.0
from __future__ import annotations

import struct as _struct

from .data import Data, empty_data
from .fdt import (
    FDT_MAGIC, FDT_BEGIN_NODE, FDT_END_NODE, FDT_PROP, FDT_NOP, FDT_END,
    FDT_V1_SIZE, FDT_V2_SIZE, FDT_V3_SIZE, FDT_V17_SIZE, align,
)
from .tree import (
    Node, Property, ReserveInfo, BootInfo, nodename_from_path, guess_boot_cpuid,
)

# flatten flags (flattree.c)
FTF_FULLPATH = 0x1
FTF_VARALIGN = 0x2
FTF_NAMEPROPS = 0x4
FTF_BOOTCPUID = 0x8
FTF_STRTABSIZE = 0x10
FTF_STRUCTSIZE = 0x20
FTF_NOPS = 0x40

# version_table[] — (version, last_comp_version, hdr_size, flags)
VERSION_TABLE = {
    1: (1, 1, FDT_V1_SIZE, FTF_FULLPATH | FTF_VARALIGN | FTF_NAMEPROPS),
    2: (2, 1, FDT_V2_SIZE, FTF_FULLPATH | FTF_VARALIGN | FTF_NAMEPROPS | FTF_BOOTCPUID),
    3: (3, 1, FDT_V3_SIZE, FTF_FULLPATH | FTF_VARALIGN | FTF_NAMEPROPS | FTF_BOOTCPUID | FTF_STRTABSIZE),
    16: (16, 16, FDT_V3_SIZE, FTF_BOOTCPUID | FTF_STRTABSIZE | FTF_NOPS),
    17: (17, 16, FDT_V17_SIZE, FTF_BOOTCPUID | FTF_STRTABSIZE | FTF_STRUCTSIZE | FTF_NOPS),
}


def _stringtable_insert(strbuf: Data, s: str) -> int:

    raw = strbuf.val
    needle = s.encode("ascii")
    i = 0
    n = len(raw)
    while i < n:
        end = raw.find(b"\0", i)
        if end < 0:
            break
        if raw[i:end] == needle:
            return i
        i = end + 1
    off = len(raw)
    strbuf.append_data(needle + b"\0")
    return off


def _flatten_tree(tree: Node, dtbuf: Data, strbuf: Data, flags: int) -> None:
    if tree.deleted:
        return

    dtbuf.append_cell(FDT_BEGIN_NODE)
    name = tree.fullpath if (flags & FTF_FULLPATH) else tree.name
    name = name or ""
    dtbuf.append_data(name.encode("ascii") + b"\0")
    dtbuf.append_align(4)

    seen_name_prop = False
    for prop in tree.proplist:
        if prop.deleted:
            continue
        if prop.name == "name":
            seen_name_prop = True
        nameoff = _stringtable_insert(strbuf, prop.name)
        dtbuf.append_cell(FDT_PROP)
        dtbuf.append_cell(prop.val.len)
        dtbuf.append_cell(nameoff)
        if (flags & FTF_VARALIGN) and prop.val.len >= 8:
            dtbuf.append_align(8)
        dtbuf.append_data(bytes(prop.val.val))
        dtbuf.append_align(4)

    if (flags & FTF_NAMEPROPS) and not seen_name_prop:
        # dtc injects a synthetic "name" property for old versions
        dtbuf.append_cell(FDT_PROP)
        dtbuf.append_cell(tree.basenamelen + 1)
        dtbuf.append_cell(_stringtable_insert(strbuf, "name"))
        if (flags & FTF_VARALIGN) and (tree.basenamelen + 1) >= 8:
            dtbuf.append_align(8)
        base = (tree.name or "")[:tree.basenamelen]
        dtbuf.append_data(base.encode("ascii") + b"\0")
        dtbuf.append_align(4)

    for child in tree.children:
        _flatten_tree(child, dtbuf, strbuf, flags)

    dtbuf.append_cell(FDT_END_NODE)


def _flatten_reserve_list(reservelist) -> Data:
    d = empty_data()
    for re in reservelist:
        d.append_re(re.address, re.size)
    return d


def _make_fdt_header(vi, reservesize, dtsize, strsize, boot_cpuid_phys):
    version, last_comp, hdr_size, flags = vi
    reservesize += 16  # + sizeof(fdt_reserve_entry) terminator

    # All fields default 0xffffffff (memset 0xff), then set the ones we use.
    f = {k: 0xFFFFFFFF for k in (
        "magic", "totalsize", "off_dt_struct", "off_dt_strings",
        "off_mem_rsvmap", "version", "last_comp_version", "boot_cpuid_phys",
        "size_dt_strings", "size_dt_struct")}

    f["magic"] = FDT_MAGIC
    f["version"] = version
    f["last_comp_version"] = last_comp

    reserve_off = align(hdr_size, 8)
    f["off_mem_rsvmap"] = reserve_off
    f["off_dt_struct"] = reserve_off + reservesize
    f["off_dt_strings"] = reserve_off + reservesize + dtsize
    f["totalsize"] = reserve_off + reservesize + dtsize + strsize

    if flags & FTF_BOOTCPUID:
        f["boot_cpuid_phys"] = boot_cpuid_phys
    if flags & FTF_STRTABSIZE:
        f["size_dt_strings"] = strsize
    if flags & FTF_STRUCTSIZE:
        f["size_dt_struct"] = dtsize

    full = _struct.pack(
        ">10I", f["magic"], f["totalsize"], f["off_dt_struct"],
        f["off_dt_strings"], f["off_mem_rsvmap"], f["version"],
        f["last_comp_version"], f["boot_cpuid_phys"], f["size_dt_strings"],
        f["size_dt_struct"])
    return full[:hdr_size]  # header truncated to the version's hdr_size


def dt_to_blob(bi: BootInfo, version: int = 17) -> bytes:
    vi = VERSION_TABLE.get(version)
    if vi is None:
        raise ValueError("Unknown device tree blob version %d" % version)

    dtbuf = empty_data()
    strbuf = empty_data()
    _flatten_tree(bi.dt, dtbuf, strbuf, vi[3])
    dtbuf.append_cell(FDT_END)

    reservebuf = _flatten_reserve_list(bi.reservelist)

    header = _make_fdt_header(vi, reservebuf.len, dtbuf.len, strbuf.len,
                              bi.boot_cpuid_phys)

    # Assemble: header, align(8), reservebuf, null reserve entry, dtbuf, strbuf
    blob = empty_data()
    blob.append_data(header)
    blob.append_align(8)
    blob.merge(reservebuf)
    blob.append_zeroes(16)  # terminating fdt_reserve_entry
    blob.merge(dtbuf)
    blob.merge(strbuf)
    return bytes(blob.val)


class _InBuf:


    def __init__(self, blob: bytes, base: int, limit: int):
        self.blob = blob
        self.base = base
        self.limit = limit
        self.ptr = base

    def read_chunk(self, n: int) -> bytes:
        if self.ptr + n > self.limit:
            raise ValueError("Premature end of data parsing flat device tree")
        out = self.blob[self.ptr:self.ptr + n]
        self.ptr += n
        return out

    def read_word(self) -> int:
        return _struct.unpack(">I", self.read_chunk(4))[0]

    def realign(self, a: int) -> None:
        off = self.ptr - self.base
        self.ptr = self.base + align(off, a)
        if self.ptr > self.limit:
            raise ValueError("Premature end of data parsing flat device tree")

    def read_string(self) -> str:
        end = self.blob.index(b"\0", self.ptr)
        if end >= self.limit:
            raise ValueError("Premature end of data parsing flat device tree")
        s = self.blob[self.ptr:end].decode("ascii", "replace")
        self.ptr = end + 1
        self.realign(4)
        return s

    def read_data(self, n: int) -> Data:
        if n == 0:
            return empty_data()
        d = Data(self.read_chunk(n))
        self.realign(4)
        return d

    def read_stringtable(self, offset: int) -> str:
        p = self.base + offset
        end = self.blob.index(b"\0", p)
        return self.blob[p:end].decode("ascii", "replace")


def _flat_read_property(dtbuf: _InBuf, strbuf: _InBuf, flags: int) -> Property:
    proplen = dtbuf.read_word()
    stroff = dtbuf.read_word()
    name = strbuf.read_stringtable(stroff)
    if (flags & FTF_VARALIGN) and proplen >= 8:
        dtbuf.realign(8)
    val = dtbuf.read_data(proplen)
    return Property(name, val)


def _flat_read_mem_reserve(inb: _InBuf):
    out = []
    while True:
        chunk = inb.read_chunk(16)
        address, size = _struct.unpack(">QQ", chunk)
        if size == 0:
            break
        out.append(ReserveInfo(address, size))
    return out


def _unflatten_tree(dtbuf: _InBuf, strbuf: _InBuf, parent_flatname: str, flags: int) -> Node:
    node = Node()
    flatname = dtbuf.read_string()
    if flags & FTF_FULLPATH:
        node.name = nodename_from_path(parent_flatname, flatname)
    else:
        node.name = flatname

    while True:
        val = dtbuf.read_word()
        if val == FDT_PROP:
            node.add_property(_flat_read_property(dtbuf, strbuf, flags))
        elif val == FDT_BEGIN_NODE:
            node.add_child(_unflatten_tree(dtbuf, strbuf, flatname, flags))
        elif val == FDT_END_NODE:
            break
        elif val == FDT_END:
            raise ValueError("Premature FDT_END in device tree blob")
        elif val == FDT_NOP:
            continue
        else:
            raise ValueError("Invalid opcode word 0x%08x in device tree blob" % val)
    return node


def dt_from_blob(blob: bytes) -> BootInfo:
    if len(blob) < 4:
        raise ValueError("Blob too small")
    magic = _struct.unpack_from(">I", blob, 0)[0]
    if magic != FDT_MAGIC:
        raise ValueError("Blob has incorrect magic number")

    totalsize = _struct.unpack_from(">I", blob, 4)[0]
    if totalsize < FDT_V1_SIZE:
        raise ValueError("DT blob size (%d) is too small" % totalsize)
    blob = blob[:totalsize]

    (_magic, _total, off_dt, off_str, off_mem_rsvmap, version, _last_comp,
     boot_cpuid_phys) = _struct.unpack_from(">8I", blob, 0)

    if off_mem_rsvmap >= totalsize:
        raise ValueError("Mem Reserve structure offset exceeds total size")
    if off_dt >= totalsize:
        raise ValueError("DT structure offset exceeds total size")
    if off_str > totalsize:
        raise ValueError("String table offset exceeds total size")

    if version >= 3:
        size_str = _struct.unpack_from(">I", blob, 32)[0]
        if off_str + size_str > totalsize:
            raise ValueError("String table extends past total size")
        strbuf = _InBuf(blob, off_str, off_str + size_str)
    else:
        strbuf = _InBuf(blob, off_str, totalsize)

    if version >= 17:
        size_dt = _struct.unpack_from(">I", blob, 36)[0]
        if off_dt + size_dt > totalsize:
            raise ValueError("Structure block extends past total size")

    flags = 0
    if version < 16:
        flags |= FTF_FULLPATH | FTF_NAMEPROPS | FTF_VARALIGN
    else:
        flags |= FTF_NOPS

    memresvbuf = _InBuf(blob, off_mem_rsvmap, totalsize)
    dtbuf = _InBuf(blob, off_dt, totalsize)

    reservelist = _flat_read_mem_reserve(memresvbuf)

    val = dtbuf.read_word()
    if val != FDT_BEGIN_NODE:
        raise ValueError(
            "Device tree blob doesn't begin with FDT_BEGIN_NODE (begins with 0x%08x)" % val)
    tree = _unflatten_tree(dtbuf, strbuf, "", flags)
    val = dtbuf.read_word()
    if val != FDT_END:
        raise ValueError("Device tree blob doesn't end with FDT_END")

    return BootInfo(reservelist, tree, boot_cpuid_phys)
