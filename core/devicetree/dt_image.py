# SPDX-License-Identifier: MPL-2.0
from __future__ import annotations

import struct as _struct

from .fdt import (
    FDT_MAGIC, DT_TABLE_MAGIC, DT_TABLE_HEADER_SIZE, DT_TABLE_ENTRY_SIZE, align,
)


class DTImageError(RuntimeError):
    pass


class DTEntry:

    __slots__ = ("fdt", "id", "rev", "custom", "orig_offset")

    def __init__(self, fdt: bytes, id: int = 0, rev: int = 0, custom=(0, 0, 0, 0),
                 orig_offset: int | None = None):
        self.fdt = fdt
        self.id = id
        self.rev = rev
        self.custom = tuple(custom)
        self.orig_offset = orig_offset


class DTImage:

    __slots__ = ("entries", "page_size", "version")

    def __init__(self, entries, page_size=2048, version=0):
        self.entries: list[DTEntry] = entries
        self.page_size = page_size
        self.version = version


def is_container(blob: bytes) -> bool:
    return len(blob) >= 4 and _struct.unpack_from(">I", blob, 0)[0] == DT_TABLE_MAGIC


def split(blob: bytes) -> DTImage:

    if not is_container(blob):
        raise DTImageError("not an Android DT/DTBO container (magic mismatch)")
    if len(blob) < DT_TABLE_HEADER_SIZE:
        raise DTImageError("container truncated (header)")

    (magic, total_size, header_size, dt_entry_size, dt_entry_count,
     dt_entries_offset, page_size, version) = _struct.unpack_from(">8I", blob, 0)

    if header_size < DT_TABLE_HEADER_SIZE:
        raise DTImageError("bad dt_table header_size %d" % header_size)
    if dt_entry_size < DT_TABLE_ENTRY_SIZE:
        raise DTImageError("bad dt_entry_size %d" % dt_entry_size)

    entries = []
    for i in range(dt_entry_count):
        eoff = dt_entries_offset + i * dt_entry_size
        if eoff + DT_TABLE_ENTRY_SIZE > len(blob):
            raise DTImageError("dt_entry %d out of range" % i)
        dt_size, dt_offset, eid, erev, c0, c1, c2, c3 = \
            _struct.unpack_from(">8I", blob, eoff)
        if dt_offset + dt_size > len(blob):
            raise DTImageError("FDT %d extends past image" % i)
        fdt = blob[dt_offset:dt_offset + dt_size]
        if len(fdt) >= 4 and _struct.unpack_from(">I", fdt, 0)[0] != FDT_MAGIC:
            raise DTImageError("entry %d is not an FDT (magic mismatch)" % i)
        entries.append(DTEntry(fdt, eid, erev, (c0, c1, c2, c3),
                               orig_offset=dt_offset))

    return DTImage(entries, page_size, version)


def build(image: DTImage) -> bytes:

    n = len(image.entries)
    header_size = DT_TABLE_HEADER_SIZE
    entry_size = DT_TABLE_ENTRY_SIZE
    entries_offset = header_size
    table_end = entries_offset + n * entry_size
    page = image.page_size or 2048

    # Can we faithfully reproduce the original layout?
    faithful = all(e.orig_offset is not None for e in image.entries)
    if faithful:
        placements = [(e.orig_offset, e.fdt) for e in image.entries]
        # offsets must not collide with the header/table region
        if any(off < table_end for off, _ in placements):
            faithful = False

    out = bytearray()
    if faithful:
        total_size = max(off + len(fdt) for off, fdt in placements)
        out += b"\0" * total_size
        for e, (off, fdt) in zip(image.entries, placements):
            out[off:off + len(fdt)] = fdt
        records = [(len(e.fdt), off, e.id, e.rev, e.custom)
                   for e, (off, _) in zip(image.entries, placements)]
    else:
        payload = bytearray()
        payload_base = table_end
        records = []
        for e in image.entries:
            start = payload_base + len(payload)
            pad = (align(start, page) - start) if page > 1 else 0
            payload += b"\0" * pad
            dt_offset = payload_base + len(payload)
            payload += e.fdt
            records.append((len(e.fdt), dt_offset, e.id, e.rev, e.custom))
        total_size = payload_base + len(payload)
        out += b"\0" * total_size
        for (dt_size, dt_offset, _, _, _), e in zip(records, image.entries):
            out[dt_offset:dt_offset + dt_size] = e.fdt

    # write header + entry table over the front
    _struct.pack_into(">8I", out, 0, DT_TABLE_MAGIC, total_size, header_size,
                      entry_size, n, entries_offset, page, image.version)
    for i, (dt_size, dt_offset, eid, erev, custom) in enumerate(records):
        _struct.pack_into(">8I", out, entries_offset + i * entry_size,
                          dt_size, dt_offset, eid, erev,
                          custom[0], custom[1], custom[2], custom[3])
    return bytes(out)
