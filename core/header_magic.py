#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0


import struct
import os


_EXT_MAGIC     = 0xEF53
_EROFS_MAGIC   = 0xE0F5E1E2
_SPARSE_MAGIC  = 0xED26FF3A
_LP_MAGIC      = 0x414C5000   # 'ALP\x00'
_LP_MAGIC_GEO  = 0x616C4467   # 'aldg'
_PAYLOAD_MAGIC = b'CrAU'
_BOOT_MAGIC = b'ANDROID!'
_VENDOR_BOOT_MAGIC = b'VNDRBOOT'


_READ_SIZE = 65536


def _r32(buf, off):
    if len(buf) < off + 4:
        return None
    return struct.unpack_from('<I', buf, off)[0]

def _r16(buf, off):
    if len(buf) < off + 2:
        return None
    return struct.unpack_from('<H', buf, off)[0]


def _detect_ext(sb: bytes) -> str:
    if len(sb) < 0x68:
        return "EXT2"          # too short → assume ext2, caller sudah tahu magic valid

    s_compat    = _r32(sb, 0x5C)
    s_incompat  = _r32(sb, 0x60)
    s_ro_compat = _r32(sb, 0x64)

    # EXT4: EXTENTS(0x40) | 64BIT(0x80) | FLEX_BG(0x200)  incompat
    #       HUGE_FILE(0x8) | GDT_CSUM(0x10) | DIR_NLINK(0x20) | EXTRA_ISIZE(0x40)  ro_compat
    if (s_incompat & (0x0040 | 0x0080 | 0x0200)) or \
       (s_ro_compat & (0x0008 | 0x0010 | 0x0020 | 0x0040)):
        return "EXT4"

    # EXT3: HAS_JOURNAL(0x4) compat  atau  RECOVER/JOURNAL_DEV(0x4|0x8) incompat
    if (s_compat & 0x0004) or (s_incompat & (0x0004 | 0x0008)):
        return "EXT3"

    return "EXT2"


def check_magic(filepath: str) -> str:

    if not os.path.isfile(filepath):
        return ""

    try:
        with open(filepath, 'rb') as f:
            data = f.read(_READ_SIZE)
    except Exception:
        return ""

    if len(data) < 4:
        return ""

    # Payload (CrAU)
    if data[:4] == _PAYLOAD_MAGIC:
        return "PAYLOAD"

    # Android boot images
    if data.startswith(_VENDOR_BOOT_MAGIC):
        return "VNDRBOOT"
    if data.startswith(_BOOT_MAGIC):
        return "BOOT"

    # Sparse
    if _r32(data, 0) == _SPARSE_MAGIC:
        return "SPARSE"

    # Super / LpMetadata
    # Geometry header bisa di offset 0, 4096, atau 8192
    for off in (0, 4096, 8192):
        v = _r32(data, off)
        if v in (_LP_MAGIC, _LP_MAGIC_GEO):
            return "SUPER"

    # Fallback: beberapa device punya zero-padding besar sebelum geometry header.
    # Skip semua 0x00, cek magic di byte pertama yang non-zero.
    nz = 0
    dlen = len(data)
    while nz < dlen and data[nz] == 0:
        nz += 1
    if nz > 0 and nz + 3 < dlen:
        v = _r32(data, nz)
        if v in (_LP_MAGIC, _LP_MAGIC_GEO):
            return "SUPER"


    for off in (0, 512, 1024, 4096):
        if _r32(data, off) == _EROFS_MAGIC:
            return "EROFS"


    for sb_start in (1024, 0):
        if _r16(data, sb_start + 0x38) == _EXT_MAGIC:
            return _detect_ext(data[sb_start:])

    return ""


def is_super_or_sparse(filepath: str) -> str:

    result = check_magic(filepath)
    if result in ("SUPER", "SPARSE"):
        return result
    return ""


def is_fs_image(filepath: str) -> str:

    result = check_magic(filepath)
    if result in ("EROFS", "EXT4", "EXT3", "EXT2"):
        return result
    return ""


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: header_magic.py <file> [file2 ...]")
        sys.exit(1)
    for path in sys.argv[1:]:
        result = check_magic(path)
        print(f"{path}  →  {result if result else '(unknown)'}")
