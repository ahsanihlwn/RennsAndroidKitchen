# SPDX-License-Identifier: MPL-2.0
from __future__ import annotations

import mmap
import struct

print("[CORE STAGE] VDEV ELFDEPS")


_LIEF = None
_LIEF_CFG = None
try:
    import lief as _lief_mod

    _lief_mod.logging.disable()
    _LIEF_CFG = _lief_mod.ELF.ParserConfig()
    _LIEF_CFG.parse_relocations = False
    _LIEF_CFG.parse_dyn_symbols = False
    _LIEF_CFG.parse_symtab_symbols = False
    _LIEF_CFG.parse_symbol_versions = False
    _LIEF_CFG.parse_notes = False
    _LIEF_CFG.parse_overlay = False
    _LIEF = _lief_mod
except Exception:                  # noqa: BLE001 — tanpa lief pun tetap jalan
    _LIEF = None


def backend_name():

    return "lief (native)" if _LIEF is not None else "stdlib mmap+struct"


def _read_needed_lief(path):
    try:
        binary = _LIEF.ELF.parse(path, _LIEF_CFG)
    except Exception:              # noqa: BLE001
        return None
    if binary is None:
        return None
    try:
        return [e.name for e in binary.dynamic_entries
                if str(e.tag).endswith("NEEDED")]
    except Exception:              # noqa: BLE001
        return None

_MAGIC = b"\x7fELF"

PT_LOAD = 1
PT_DYNAMIC = 2

DT_NULL = 0
DT_NEEDED = 1
DT_STRTAB = 5

# Batas waras: dynamic section yang lebih besar dari ini hampir pasti file
# rusak / bukan ELF sungguhan. Cegah loop panjang tanpa guna.
_MAX_DYN_ENTRIES = 4096


def read_needed(src):

    if isinstance(src, (bytes, bytearray, memoryview)):
        try:
            return _parse(src)
        except (struct.error, IndexError, ValueError):
            return None
    if _LIEF is not None:
        out = _read_needed_lief(src)
        if out is not None:
            return out
        # lief menolak file ini — coba pembaca sendiri sebelum menyerah,
        # supaya ELF yang sedikit nyeleneh tetap terbaca.
    return _read_needed_stdlib(src)


def _read_needed_stdlib(path):
    try:
        fh = open(path, "rb")
    except OSError:
        return None
    try:
        try:
            mm = mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ)
        except (ValueError, OSError):
            return None            # file kosong / tidak bisa di-map
        try:
            return _parse(mm)
        except (struct.error, IndexError, ValueError):
            return None            # header terpotong / ngawur
        finally:
            mm.close()
    finally:
        fh.close()


def _parse(mm):
    if len(mm) < 64 or mm[:4] != _MAGIC:
        return None

    is64 = mm[4] == 2
    endian = "<" if mm[5] == 1 else ">"

    if is64:
        phoff = struct.unpack_from(endian + "Q", mm, 32)[0]
        phentsize, phnum = struct.unpack_from(endian + "HH", mm, 54)
        ph_fmt, ph_default = endian + "IIQQQQQQ", 56
    else:
        phoff = struct.unpack_from(endian + "I", mm, 28)[0]
        phentsize, phnum = struct.unpack_from(endian + "HH", mm, 42)
        ph_fmt, ph_default = endian + "IIIIIIII", 32

    if not phoff or not phnum:
        return []                  # tidak ada program header (mis. file .o)

    stride = phentsize or ph_default
    loads = []
    dyn = None

    for i in range(phnum):
        off = phoff + i * stride
        if off + ph_default > len(mm):
            break
        f = struct.unpack_from(ph_fmt, mm, off)
        # Urutan field beda antara ELF32 dan ELF64 (p_flags pindah tempat).
        if is64:
            p_type, p_offset, p_vaddr, p_filesz = f[0], f[2], f[3], f[5]
        else:
            p_type, p_offset, p_vaddr, p_filesz = f[0], f[1], f[2], f[4]
        if p_type == PT_LOAD:
            loads.append((p_vaddr, p_offset, p_filesz))
        elif p_type == PT_DYNAMIC:
            dyn = (p_offset, p_filesz)

    if dyn is None:
        return []

    dyn_fmt, dyn_size = (endian + "qQ", 16) if is64 else (endian + "iI", 8)
    dyn_off, dyn_len = dyn
    count = min(dyn_len // dyn_size, _MAX_DYN_ENTRIES)

    needed_offsets = []
    strtab_vaddr = None
    for i in range(count):
        pos = dyn_off + i * dyn_size
        if pos + dyn_size > len(mm):
            break
        tag, val = struct.unpack_from(dyn_fmt, mm, pos)
        if tag == DT_NULL:
            break
        if tag == DT_NEEDED:
            needed_offsets.append(val)
        elif tag == DT_STRTAB:
            strtab_vaddr = val

    if not needed_offsets or strtab_vaddr is None:
        return []

    # DT_STRTAB itu alamat virtual — petakan ke offset file lewat PT_LOAD
    # yang memuatnya, persis cara loader melakukannya.
    strtab_off = None
    for p_vaddr, p_offset, p_filesz in loads:
        if p_vaddr <= strtab_vaddr < p_vaddr + p_filesz:
            strtab_off = p_offset + (strtab_vaddr - p_vaddr)
            break
    if strtab_off is None:
        return []

    out = []
    limit = len(mm)
    for rel in needed_offsets:
        start = strtab_off + rel
        if start >= limit:
            continue
        end = mm.find(b"\x00", start)
        if end > start:
            out.append(mm[start:end].decode("utf-8", "replace"))
    return out
