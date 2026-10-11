#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0


import os
import struct


EROFS_SUPER_OFFSET = 1024
EROFS_SUPER_MAGIC  = 0xE0F5E1E2
EROFS_ISLOTSIZE    = 32

EROFS_INODE_FLAT_PLAIN         = 0
EROFS_INODE_COMPRESSED_FULL    = 1
EROFS_INODE_FLAT_INLINE        = 2
EROFS_INODE_COMPRESSED_COMPACT = 3
EROFS_INODE_CHUNK_BASED        = 4

# offset field di erofs_super_block (relatif EROFS_SUPER_OFFSET)
SB_MAGIC_OFF        = 0
SB_CHECKSUM_OFF     = 4
SB_FEATURE_COMPAT   = 8
SB_BLKSZBITS_OFF    = 12
SB_ROOTNID_2B_OFF   = 14        # union rb: rootnid_2b / blocks_hi (48BIT)
SB_BLOCKS_LO_OFF    = 36
SB_META_BLKADDR_OFF = 40
SB_FEATURE_INCOMPAT = 80

EROFS_FEATURE_COMPAT_SB_CHKSUM   = 0x00000001
EROFS_FEATURE_INCOMPAT_FRAGMENTS = 0x00000020   # == DEDUPE
EROFS_FEATURE_INCOMPAT_48BIT     = 0x00000080
EROFS_FEATURE_INCOMPAT_METABOX   = 0x00000100

# offset field di erofs_inode_compact / _extended
I_FORMAT_OFF   = 0      # le16
I_XATTR_ICOUNT = 2      # le16
I_MODE_OFF     = 4      # le16
I_NB_OFF       = 6      # le16  (nlink / startblk_hi / blocks_hi)
I_SIZE_OFF     = 8      # le32 (compact) / le64 (extended)
# Kedua bentuk inode menaruh i_u di byte 16. Pada compact, byte 12..15
# adalah i_mtime; pada extended, byte 8..15 adalah i_size 64-bit.
I_U_OFF_COMPACT  = 16   # le32 startblk_lo / blocks_lo / rdev
I_U_OFF_EXTENDED = 16   # le32 startblk_lo / blocks_lo / rdev

EROFS_I_DATALAYOUT_BIT  = 1
EROFS_I_DATALAYOUT_MASK = 0x07
EROFS_I_NLINK_1_BIT     = 4

_NULL_STARTBLK = 0xFFFFFFFF


def _null_startblk(inode):

    return ((1 << 48) - 1 if inode.get("addr_mask") == (1 << 48) - 1
            else _NULL_STARTBLK)


def _is_null_start(inode, start=None):

    if start is None:
        start = _startblk(inode)
    return not ((start ^ ((1 << 48) - 1)) & inode.get("addr_mask", 0xFFFFFFFF))


_CRC32C_POLY_LE = 0x82F63B78
_CRC_TAB = []
for _i in range(256):
    _c = _i
    for _ in range(8):
        _c = (_c >> 1) ^ (_CRC32C_POLY_LE if (_c & 1) else 0)
    _CRC_TAB.append(_c)
del _i, _c


def crc32c(crc: int, data: bytes) -> int:
    for b in data:
        crc = (crc >> 8) ^ _CRC_TAB[(crc ^ b) & 0xFF]
    return crc & 0xFFFFFFFF


class ErofsWriteError(Exception):
    pass


def _has_avb_footer(f):

    here = f.tell()
    try:
        f.seek(0, os.SEEK_END)
        end = f.tell()
        if end < 64:
            return False
        f.seek(end - 64)
        footer = f.read(64)
        if len(footer) != 64 or footer[:4] != b"AVBf":
            return False
        _magic, _major, _minor, original, vbmeta_off, vbmeta_size = \
            struct.unpack_from(">4sIIQQQ", footer)
        # Offset harus menunjuk ke area sebelum footer; ini menghindari false
        # positive dari empat byte AVBf yang kebetulan ada di data file.
        return (0 < original <= vbmeta_off <= end - 64 and
                0 < vbmeta_size <= end - 64 - vbmeta_off)
    finally:
        f.seek(here)


def _read_avb_footer(f):

    here = f.tell()
    try:
        f.seek(0, os.SEEK_END)
        end = f.tell()
        if end < 64:
            return None
        f.seek(end - 64)
        footer = f.read(64)
        if len(footer) != 64 or footer[:4] != b"AVBf":
            return None
        _magic, major, minor, original, vbmeta_off, vbmeta_size = \
            struct.unpack_from(">4sIIQQQ", footer)
        if not (0 < original <= vbmeta_off <= end - 64 and
                0 < vbmeta_size <= end - 64 - vbmeta_off):
            return None
        return {
            "original_image_size": original,
            "vbmeta_offset": vbmeta_off,
            "vbmeta_size": vbmeta_size,
            "version_major": major,
            "version_minor": minor,
        }
    finally:
        f.seek(here)


def _strip_avb_footer(f, sb):

    info = _read_avb_footer(f)
    if info is None:
        raise ErofsWriteError("tidak ada footer AVB untuk di-strip")

    original = info["original_image_size"]
    bs = sb.block_size
    expected = sb.total_blocks * bs

    if original % bs != 0:
        raise ErofsWriteError(
            f"original_image_size ({original}) bukan kelipatan "
            f"block_size ({bs}) — footer AVB mencurigakan")

    if original != expected:
        raise ErofsWriteError(
            f"original_image_size di footer ({original}) != "
            f"sb.total_blocks * block_size ({expected}) — "
            f"image mungkin corrupt atau sudah dimodifikasi")

    f.truncate(original)
    f.flush()
    return info


class _Sb:


    def __init__(self, f):
        self.f = f
        f.seek(EROFS_SUPER_OFFSET)
        raw = f.read(144)
        if len(raw) < 128:
            raise ErofsWriteError("superblock terpotong")
        magic = struct.unpack_from("<I", raw, SB_MAGIC_OFF)[0]
        if magic != EROFS_SUPER_MAGIC:
            raise ErofsWriteError("bukan image EROFS")

        self.feature_compat   = struct.unpack_from("<I", raw, SB_FEATURE_COMPAT)[0]
        self.feature_incompat = struct.unpack_from("<I", raw, SB_FEATURE_INCOMPAT)[0]
        blkszbits = raw[SB_BLKSZBITS_OFF]
        self.blkszbits  = blkszbits if blkszbits else 12
        self.block_size = 1 << self.blkszbits
        self.meta_blkaddr = struct.unpack_from("<I", raw, SB_META_BLKADDR_OFF)[0]
        self.blocks_lo    = struct.unpack_from("<I", raw, SB_BLOCKS_LO_OFF)[0]
        self.has_48bit    = bool(self.feature_incompat & EROFS_FEATURE_INCOMPAT_48BIT)
        # union rb: kalau 48BIT aktif field ini blocks_hi, kalau tidak rootnid_2b
        rb = struct.unpack_from("<H", raw, SB_ROOTNID_2B_OFF)[0]
        self.blocks_hi = rb if self.has_48bit else 0
        self.has_chksum = bool(self.feature_compat & EROFS_FEATURE_COMPAT_SB_CHKSUM)

    @property
    def total_blocks(self):
        return (self.blocks_hi << 32) | self.blocks_lo

    def set_total_blocks(self, nblocks):

        if nblocks >> 32 and not self.has_48bit:
            raise ErofsWriteError("jumlah block > 2^32 tapi image bukan 48BIT")
        self.f.seek(EROFS_SUPER_OFFSET + SB_BLOCKS_LO_OFF)
        self.f.write(struct.pack("<I", nblocks & 0xFFFFFFFF))
        if self.has_48bit:
            self.f.seek(EROFS_SUPER_OFFSET + SB_ROOTNID_2B_OFF)
            self.f.write(struct.pack("<H", (nblocks >> 32) & 0xFFFF))
        self.blocks_lo = nblocks & 0xFFFFFFFF
        self.blocks_hi = (nblocks >> 32) if self.has_48bit else 0

    def refresh_checksum(self):

        if not self.has_chksum:
            return None
        bs = self.block_size
        length = bs - EROFS_SUPER_OFFSET if bs > EROFS_SUPER_OFFSET else bs
        self.f.seek(EROFS_SUPER_OFFSET)
        buf = bytearray(self.f.read(length))
        if len(buf) < length:
            buf.extend(b"\x00" * (length - len(buf)))
        struct.pack_into("<I", buf, SB_CHECKSUM_OFF, 0)
        crc = crc32c(0xFFFFFFFF, bytes(buf))
        self.f.seek(EROFS_SUPER_OFFSET + SB_CHECKSUM_OFF)
        self.f.write(struct.pack("<I", crc))
        return crc


def _inode_offset(sb, nid):
    return sb.meta_blkaddr * sb.block_size + nid * EROFS_ISLOTSIZE


def read_inode(f, sb, nid):

    if nid >> 63:
        raise ErofsWriteError(
            "inode berada di METABOX; edit in-place belum aman untuk metadata ini")
    off = _inode_offset(sb, nid)
    f.seek(off)
    hdr = f.read(64)
    if len(hdr) < 32:
        raise ErofsWriteError(f"inode nid={nid} terpotong")

    fmt = struct.unpack_from("<H", hdr, I_FORMAT_OFF)[0]
    is_extended = (fmt & 1) == 1
    layout = (fmt >> EROFS_I_DATALAYOUT_BIT) & EROFS_I_DATALAYOUT_MASK
    xattr_count = struct.unpack_from("<H", hdr, I_XATTR_ICOUNT)[0]
    mode = struct.unpack_from("<H", hdr, I_MODE_OFF)[0]
    nb   = struct.unpack_from("<H", hdr, I_NB_OFF)[0]

    if is_extended:
        size = struct.unpack_from("<Q", hdr, I_SIZE_OFF)[0]
        hdr_size = 64
    else:
        size = struct.unpack_from("<I", hdr, I_SIZE_OFF)[0]
        hdr_size = 32
    u_off = I_U_OFF_EXTENDED if is_extended else I_U_OFF_COMPACT
    u_raw = struct.unpack_from("<I", hdr, u_off)[0]


    if is_extended:
        start_hi = nb
        addr_mask = (1 << 48) - 1
    elif ((mode & 0o170000) != 0o040000 and
          (fmt & (1 << EROFS_I_NLINK_1_BIT))):
        start_hi = nb
        addr_mask = (1 << 48) - 1
    else:
        start_hi = 0
        addr_mask = (1 << 32) - 1

    xattr_off  = off + hdr_size
    xattr_size = (12 + (xattr_count - 1) * 4) if xattr_count else 0
    return {
        "nid": nid, "off": off, "fmt": fmt, "is_extended": is_extended,
        "layout": layout, "mode": mode, "size": size, "u_raw": u_raw,
        "startblk": (start_hi << 32) | u_raw, "start_hi": start_hi,
        "addr_mask": addr_mask, "u_off": u_off,
        "nb": nb, "hdr_size": hdr_size, "xattr_off": xattr_off,
        "xattr_count": xattr_count, "xattr_size": xattr_size,
        "data_off": xattr_off + xattr_size,
    }


def _startblk(inode):

    return inode["startblk"]


def _write_inode_fields(f, inode, *, layout=None, size=None, startblk=None):

    off = inode["off"]

    if layout is not None:
        fmt = inode["fmt"]
        fmt = (fmt & ~(EROFS_I_DATALAYOUT_MASK << EROFS_I_DATALAYOUT_BIT)) | \
              ((layout & EROFS_I_DATALAYOUT_MASK) << EROFS_I_DATALAYOUT_BIT)
        f.seek(off + I_FORMAT_OFF)
        f.write(struct.pack("<H", fmt & 0xFFFF))
        inode["fmt"] = fmt
        inode["layout"] = layout

    if size is not None:
        if inode["is_extended"]:
            f.seek(off + I_SIZE_OFF)
            f.write(struct.pack("<Q", size))
        else:
            if size > 0xFFFFFFFF:
                raise ErofsWriteError("ukuran > 4 GiB pada inode compact")
            f.seek(off + I_SIZE_OFF)
            f.write(struct.pack("<I", size))
        inode["size"] = size

    if startblk is not None:
        if startblk > ((1 << 48) - 1):
            raise ErofsWriteError("startblk > 48-bit tidak didukung EROFS")
        high = (startblk >> 32) & 0xFFFF
        if high:
            if inode["is_extended"]:
                # i_nb memang menjadi startblk_hi pada extended inode.
                f.seek(off + I_NB_OFF)
                f.write(struct.pack("<H", high))
            else:
                # Compact inode dapat menyimpan high-half hanya bila nlink=1;
                # bit ini mengubah arti i_nb dari nlink menjadi startblk_hi.
                old_nlink = inode["nb"]
                if not (inode["fmt"] & (1 << EROFS_I_NLINK_1_BIT)):
                    if old_nlink != 1:
                        raise ErofsWriteError(
                            "compact inode dengan hardlink tidak bisa diarahkan ke blok 48-bit")
                    fmt = inode["fmt"] | (1 << EROFS_I_NLINK_1_BIT)
                    f.seek(off + I_FORMAT_OFF)
                    f.write(struct.pack("<H", fmt))
                    inode["fmt"] = fmt
                f.seek(off + I_NB_OFF)
                f.write(struct.pack("<H", high))
        elif inode["is_extended"]:
            f.seek(off + I_NB_OFF)
            f.write(b"\x00\x00")
        elif inode["fmt"] & (1 << EROFS_I_NLINK_1_BIT):
            # Tetap bit NLINK_1, namun nilai high-nya harus nol.
            f.seek(off + I_NB_OFF)
            f.write(b"\x00\x00")

        f.seek(off + inode["u_off"])
        f.write(struct.pack("<I", startblk & 0xFFFFFFFF))
        inode["u_raw"] = startblk & 0xFFFFFFFF
        inode["start_hi"] = high
        inode["startblk"] = startblk


def _plain_capacity(sb, inode):

    bs = sb.block_size
    return ((inode["size"] + bs - 1) // bs) * bs


def _inline_parts(sb, inode):

    bs = sb.block_size
    full = inode["size"] // bs
    tail = inode["size"] - full * bs
    return full, tail


def plan_write(f, sb, inode, new_len, *, strip_avb=True):

    bs = sb.block_size
    layout = inode["layout"]

    if layout == EROFS_INODE_CHUNK_BASED:
        raise ErofsWriteError(
            "inode chunk-based (layout 4) belum didukung untuk tulis")

    # 1. in-place FLAT_PLAIN
    if layout == EROFS_INODE_FLAT_PLAIN:
        cap = _plain_capacity(sb, inode)
        startblk = _startblk(inode)
        if new_len <= cap and (not _is_null_start(inode, startblk) or new_len == 0):
            return {"mode": "inplace_plain", "grow": 0,
                    "detail": f"in-place {new_len}/{cap}B di block {startblk}"}

    # 2. in-place FLAT_INLINE
    if layout == EROFS_INODE_FLAT_INLINE:
        old_full, old_tail = _inline_parts(sb, inode)
        new_full = new_len // bs
        new_tail = new_len - new_full * bs


        if new_full == old_full and new_tail <= old_tail:
            if old_full == 0 or not _is_null_start(inode):
                return {"mode": "inplace_inline", "grow": 0,
                        "detail": (f"in-place inline: {new_full} block penuh + "
                                   f"tail {new_tail}/{old_tail}B")}


    if sb.feature_incompat & EROFS_FEATURE_INCOMPAT_FRAGMENTS:
        raise ErofsWriteError(
            "image pakai FRAGMENTS/DEDUPE — jalur append belum didukung")
    if sb.feature_incompat & EROFS_FEATURE_INCOMPAT_METABOX:
        raise ErofsWriteError("image pakai METABOX — jalur append belum didukung")
    if _has_avb_footer(f):
        if not strip_avb:
            raise ErofsWriteError(
                "image memiliki footer AVB; append akan menulis setelah vbmeta. "
                "Pakai repack lalu sign ulang, atau ganti konten yang masih muat")
        avb_info = _read_avb_footer(f)
        if avb_info is None:
            raise ErofsWriteError(
                "footer AVB terdeteksi tapi gagal di-parse — "
                "image mungkin corrupt")
    else:
        avb_info = None
    nblocks = (new_len + bs - 1) // bs
    result = {"mode": "append", "grow": nblocks * bs,
              "detail": (f"append {nblocks} block ({nblocks * bs}B) di ujung image, "
                         f"inode di-repoint jadi FLAT_PLAIN")}
    if avb_info is not None:
        result["avb_strip"] = avb_info
        result["detail"] += (f" (AVB footer di-strip: "
                             f"{avb_info['original_image_size']}B → raw)")
    return result


def _do_inplace_plain(f, sb, inode, data):
    bs = sb.block_size
    base = _startblk(inode) * bs
    f.seek(base)
    f.write(data)
    # pad sisa block terakhir dgn NUL supaya tak ada sisa konten lama terbaca
    rem = (-len(data)) % bs
    if rem:
        f.write(b"\x00" * rem)
    _write_inode_fields(f, inode, size=len(data))


def _do_inplace_inline(f, sb, inode, data):
    bs = sb.block_size
    old_full, old_tail = _inline_parts(sb, inode)
    new_full = len(data) // bs
    new_tail = len(data) - new_full * bs

    if new_full:
        f.seek(_startblk(inode) * bs)
        f.write(data[:new_full * bs])
    # tail: tulis di tempat, sisa slot lama di-NUL supaya tak ada ekor lama
    f.seek(inode["data_off"])
    f.write(data[new_full * bs:])
    if old_tail > new_tail:
        f.write(b"\x00" * (old_tail - new_tail))
    _write_inode_fields(f, inode, size=len(data))


def _do_append(f, sb, inode, data):

    bs = sb.block_size
    f.seek(0, os.SEEK_END)
    end = f.tell()

    # mulai di batas block; kalau image tak kelipatan block, pad dulu
    start_block = (end + bs - 1) // bs
    pad = start_block * bs - end
    if pad:
        f.write(b"\x00" * pad)

    # jangan menimpa area yang masih diklaim superblock
    if start_block < sb.total_blocks:
        start_block = sb.total_blocks
        f.seek(start_block * bs)

    f.seek(start_block * bs)
    f.write(data)
    rem = (-len(data)) % bs
    if rem:
        f.write(b"\x00" * rem)

    nblocks = (len(data) + bs - 1) // bs
    _write_inode_fields(f, inode,
                        layout=EROFS_INODE_FLAT_PLAIN,
                        size=len(data),
                        startblk=start_block if nblocks else _null_startblk(inode))

    # perluas klaim block di superblock supaya kernel/tool lain tahu
    new_total = start_block + nblocks
    if new_total > sb.total_blocks:
        sb.set_total_blocks(new_total)
    return start_block, nblocks


def write_file(f, nid, data: bytes, *, sb=None, allow_grow=True,
               strip_avb=True):

    if sb is None:
        sb = _Sb(f)
    inode = read_inode(f, sb, nid)

    S_IFMT, S_IFREG = 0o170000, 0o100000
    if (inode["mode"] & S_IFMT) != S_IFREG:
        raise ErofsWriteError("bukan file reguler")

    plan = plan_write(f, sb, inode, len(data), strip_avb=strip_avb)
    if plan["mode"] == "append" and not allow_grow:
        raise ErofsWriteError(
            "konten baru tak muat di tempat dan penambahan ukuran image "
            "dimatikan (allow_grow=False)")

    old_size = inode["size"]
    if plan["mode"] == "inplace_plain":
        _do_inplace_plain(f, sb, inode, data)
    elif plan["mode"] == "inplace_inline":
        _do_inplace_inline(f, sb, inode, data)
    else:
        # strip AVB footer dulu kalau plan menyebutkan avb_strip
        if plan.get("avb_strip"):
            _strip_avb_footer(f, sb)
        _do_append(f, sb, inode, data)


    crc = sb.refresh_checksum()
    f.flush()
    try:
        os.fsync(f.fileno())
    except (OSError, AttributeError, ValueError):
        pass

    result = {"mode": plan["mode"], "detail": plan["detail"],
              "old_size": old_size, "new_size": len(data),
              "grew": plan["grow"], "checksum": crc}
    if plan.get("avb_strip"):
        result["avb_stripped"] = plan["avb_strip"]
    return result


EROFS_DIRENT_SIZE = 12


def _read_inode_data(f, sb, inode):

    bs = sb.block_size
    size = inode["size"]
    layout = inode["layout"]

    if layout == EROFS_INODE_FLAT_PLAIN:
        if _is_null_start(inode):
            return b"\x00" * size
        f.seek(_startblk(inode) * bs)
        return f.read(size).ljust(size, b"\x00")

    if layout == EROFS_INODE_FLAT_INLINE:
        full = (size // bs) * bs
        tail = size - full
        out = b""
        if full:
            if _is_null_start(inode):
                out += b"\x00" * full
            else:
                f.seek(_startblk(inode) * bs)
                out += f.read(full).ljust(full, b"\x00")
        if tail:
            f.seek(inode["data_off"])
            out += f.read(tail)
        return out

    raise ErofsWriteError(f"direktori dengan layout {layout} belum didukung")


def parse_dirents(data, bs):

    out = []
    pos = 0
    total = len(data)
    while pos < total:
        block = data[pos: pos + bs]
        blen = len(block)
        if blen < EROFS_DIRENT_SIZE:
            break
        first_nameoff = struct.unpack_from("<H", block, 8)[0]
        count = first_nameoff // EROFS_DIRENT_SIZE
        if count == 0 or count * EROFS_DIRENT_SIZE > blen:
            break
        for i in range(count):
            e = i * EROFS_DIRENT_SIZE
            nid     = struct.unpack_from("<Q", block, e)[0]
            nameoff = struct.unpack_from("<H", block, e + 8)[0]
            ftype   = block[e + 10]
            if i + 1 < count:
                nxt = struct.unpack_from("<H", block, (i + 1) * EROFS_DIRENT_SIZE + 8)[0]
            else:
                nxt = blen
            nameoff = min(nameoff, blen)
            nxt     = min(nxt, blen)
            name = block[nameoff:nxt].split(b"\x00", 1)[0]
            if name:
                out.append((nid, name, ftype))
        pos += bs
    return out


def build_dirents(entries, bs):

    # urutan wajib: byte-wise naik; '.' dan '..' otomatis di depan karena
    # 0x2E lebih kecil dari alfanumerik, dan '.' < '..' (prefiks lebih pendek).
    ents = sorted(entries, key=lambda e: e[1])

    for i in range(1, len(ents)):
        if ents[i][1] == ents[i - 1][1]:
            raise ErofsWriteError(
                f"nama ganda di direktori: {ents[i][1]!r}")

    # kemas greedy per blok: entri ke-n butuh 12 byte slot + len(nama)
    blocks = []
    cur = []
    used = 0
    for ent in ents:
        need = EROFS_DIRENT_SIZE + len(ent[1])
        if cur and used + need > bs:
            blocks.append(cur)
            cur, used = [], 0
        if EROFS_DIRENT_SIZE + len(ent[1]) > bs:
            raise ErofsWriteError(f"nama terlalu panjang: {ent[1]!r}")
        cur.append(ent)
        used += need
    if cur:
        blocks.append(cur)
    if not blocks:
        raise ErofsWriteError("direktori tak boleh kosong ('.' dan '..' wajib)")

    out = bytearray()
    for bi, blk in enumerate(blocks):
        buf = bytearray(bs)
        nameoff = len(blk) * EROFS_DIRENT_SIZE
        for i, (nid, name, ftype) in enumerate(blk):
            e = i * EROFS_DIRENT_SIZE
            struct.pack_into("<Q", buf, e, nid)
            struct.pack_into("<H", buf, e + 8, nameoff)
            buf[e + 10] = ftype
            buf[e + 11] = 0
            buf[nameoff:nameoff + len(name)] = name
            nameoff += len(name)
        if bi == len(blocks) - 1:
            # blok terakhir boleh parsial: potong tepat di ujung nama terakhir
            out += bytes(buf[:nameoff])
        else:
            out += bytes(buf)
    return bytes(out)


def read_dir(f, nid, *, sb=None):

    if sb is None:
        sb = _Sb(f)
    inode = read_inode(f, sb, nid)
    S_IFMT, S_IFDIR = 0o170000, 0o040000
    if (inode["mode"] & S_IFMT) != S_IFDIR:
        raise ErofsWriteError("bukan direktori")
    data = _read_inode_data(f, sb, inode)
    return [(n, nm.decode("utf-8", "replace"), t)
            for (n, nm, t) in parse_dirents(data, sb.block_size)]


def _write_dir_data(f, sb, inode, new_data):

    plan = plan_write(f, sb, inode, len(new_data))
    if plan["mode"] == "inplace_plain":
        _do_inplace_plain(f, sb, inode, new_data)
    elif plan["mode"] == "inplace_inline":
        _do_inplace_inline(f, sb, inode, new_data)
    else:
        _do_append(f, sb, inode, new_data)
    return plan


def unlink(f, dir_nid, name, *, sb=None):

    if sb is None:
        sb = _Sb(f)
    if isinstance(name, str):
        name = name.encode("utf-8")
    if name in (b".", b".."):
        raise ErofsWriteError("'.' dan '..' tak boleh dihapus")

    inode = read_inode(f, sb, dir_nid)
    S_IFMT, S_IFDIR = 0o170000, 0o040000
    if (inode["mode"] & S_IFMT) != S_IFDIR:
        raise ErofsWriteError("bukan direktori")

    data = _read_inode_data(f, sb, inode)
    entries = parse_dirents(data, sb.block_size)
    kept = [e for e in entries if e[1] != name]
    if len(kept) == len(entries):
        raise FileNotFoundError(name.decode("utf-8", "replace"))

    removed = next(e for e in entries if e[1] == name)
    new_data = build_dirents(kept, sb.block_size)
    plan = _write_dir_data(f, sb, inode, new_data)

    sb.refresh_checksum()
    f.flush()
    try:
        os.fsync(f.fileno())
    except (OSError, AttributeError, ValueError):
        pass
    return {"removed_nid": removed[0], "removed_ftype": removed[2],
            "entries_before": len(entries), "entries_after": len(kept),
            "mode": plan["mode"], "grew": plan["grow"],
            "old_size": len(data), "new_size": len(new_data)}


def write_mode(f, nid, perm_octal: int, *, sb=None):

    if sb is None:
        sb = _Sb(f)
    inode = read_inode(f, sb, nid)
    new_mode = (inode["mode"] & ~0o7777) | (perm_octal & 0o7777)
    f.seek(inode["off"] + I_MODE_OFF)
    f.write(struct.pack("<H", new_mode & 0xFFFF))
    sb.refresh_checksum()
    f.flush()
    return new_mode


EROFS_XATTR_INDEX_SECURITY = 6


def _find_inline_selinux(buf, base):

    pos = base
    while pos + 4 <= len(buf):
        name_len   = buf[pos]
        name_index = buf[pos + 1]
        value_size = struct.unpack_from("<H", buf, pos + 2)[0]
        if name_len == 0 and name_index == 0 and value_size == 0:
            break
        name = buf[pos + 4: pos + 4 + name_len]
        vstart = pos + 4 + name_len
        if name_index == EROFS_XATTR_INDEX_SECURITY and name == b"selinux":
            return vstart, value_size, pos
        nxt = vstart + value_size
        pos = (nxt + 3) & ~3
    return None


def write_selinux(f, nid, new_ctx: str, *, sb=None):

    if sb is None:
        sb = _Sb(f)
    inode = read_inode(f, sb, nid)
    if not inode["xattr_count"]:
        raise ErofsWriteError("inode tak punya xattr")

    f.seek(inode["xattr_off"])
    area = bytearray(f.read(inode["xattr_size"]))
    if len(area) < 12:
        raise ErofsWriteError("area xattr terpotong")

    shared_count = area[4]
    hit = _find_inline_selinux(area, 12 + shared_count * 4)
    if hit is None:
        if shared_count:
            raise ErofsWriteError(
                "security.selinux tersimpan di area shared (dipakai bersama "
                "inode lain) — ubah konteks butuh repack")
        raise ErofsWriteError("entry security.selinux tak ditemukan")

    vstart, value_size, _entry_pos = hit
    new_bytes = new_ctx.encode("utf-8")
    if len(new_bytes) > value_size:
        raise ErofsWriteError(
            f"konteks baru ({len(new_bytes)}B) > slot lama ({value_size}B) — "
            f"butuh repack")

    # tulis value + pad NUL sampai panjang slot lama (layout tak bergeser),
    # lalu perbarui e_value_size agar pembaca tahu panjang sebenarnya.
    padded = new_bytes + b"\x00" * (value_size - len(new_bytes))
    f.seek(inode["xattr_off"] + vstart)
    f.write(padded)
    f.seek(inode["xattr_off"] + _entry_pos + 2)
    f.write(struct.pack("<H", len(new_bytes)))
    sb.refresh_checksum()
    f.flush()
    return new_ctx


def open_sb(f):
    return _Sb(f)


def read_avb_footer(f):

    return _read_avb_footer(f)


def strip_avb_footer(f, sb=None):

    if sb is None:
        sb = _Sb(f)
    return _strip_avb_footer(f, sb)


def inode_pblocks(f, sb, inode):

    bs = sb.block_size
    layout = inode["layout"]
    size = inode["size"]
    out = set()

    if layout == EROFS_INODE_FLAT_PLAIN:
        start = _startblk(inode)
        if not _is_null_start(inode, start) and size:
            for k in range((size + bs - 1) // bs):
                out.add(start + k)
        return out

    if layout == EROFS_INODE_FLAT_INLINE:
        start = _startblk(inode)
        full = size // bs                 # tail menumpang di blok meta
        if not _is_null_start(inode, start):
            for k in range(full):
                out.add(start + k)
        return out

    if layout in (EROFS_INODE_COMPRESSED_FULL, EROFS_INODE_COMPRESSED_COMPACT):
        from core import erofs_z
        sbi = {"block_size": bs, "blkszbits": sb.blkszbits,
               "lz4_0padding": bool(sb.feature_incompat & 0x1)}
        try:
            z = erofs_z.ZInode(f, sbi, inode)
        except (NotImplementedError, Exception):
            return out
        ofs = 0
        guard = 0
        while ofs < size and guard < 1 << 22:
            guard += 1
            try:
                m_la, pblk, headtype, m_llen = z._map(ofs)
            except Exception:
                break
            if m_llen <= 0:
                break
            # tanpa big_pcluster satu pcluster = satu blok masukan
            nblk = ((m_llen + bs - 1) // bs) if headtype == 0 else 1
            for k in range(nblk):
                out.add(pblk + k)
            ofs = m_la + m_llen
        return out

    return out          # chunk-based dsb: tak dihitung


def _inode_meta_blocks(f, sb, inode):

    bs = sb.block_size
    start = inode["off"]
    end = inode["data_off"]
    lay = inode["layout"]
    size = inode["size"]

    if lay == EROFS_INODE_FLAT_INLINE:
        end = inode["data_off"] + (size % bs)
    elif lay in (EROFS_INODE_COMPRESSED_FULL, EROFS_INODE_COMPRESSED_COMPACT):
        hoff = (inode["data_off"] + 7) & ~7
        n_lcl = (size + bs - 1) // bs
        end = hoff + 8 + n_lcl * 4 + 16
    return set(range(start // bs, (end + bs - 1) // bs))


def usage_report(path, progress=None):

    from core import imgfs

    fs = imgfs.detect_fs(path)
    if fs != "EROFS":
        raise ValueError("usage_report hanya untuk EROFS")

    # Gunakan reader yang sama seperti operasi read lain agar Android sparse
    # dipandang sebagai image hasil unsparse, bukan header sparse mentah.
    r = None
    try:
        _, r = imgfs.open_reader(path)
        f = r.f
        sb = _Sb(f)
        bs = sb.block_size

        nodes = r.walk()
        used = set()
        meta_used = set()
        n_files = n_dirs = n_links = 0
        logical = 0

        total = len(nodes) + 1
        # root ikut dihitung: dirent-nya juga menempati blok
        for i, n in enumerate([None] + nodes):
            nid = r.root_nid if n is None else n.ino
            if n is not None:
                if n.is_dir:
                    n_dirs += 1
                elif n.is_link:
                    n_links += 1
                else:
                    n_files += 1
                    logical += n.size
            try:
                ino = read_inode(f, sb, nid)
                used |= inode_pblocks(f, sb, ino)


                meta_used |= _inode_meta_blocks(f, sb, ino)
            except Exception:
                pass
            if progress and (i % 200 == 0 or i == total - 1):
                progress(i + 1, total)

        file_size = os.path.getsize(path)
        total_blocks = sb.total_blocks or (file_size // bs)

        occupied = used | meta_used
        used_blocks = len(used)
        meta_blocks = len(meta_used - used)
        wasted_blocks = max(0, total_blocks - len(occupied))

        return {
            "fs": "EROFS",
            "block_size": bs,
            "file_size": file_size,
            "total_blocks": total_blocks,
            "meta_bytes": meta_blocks * bs,
            "used_blocks": used_blocks,
            "used_bytes": used_blocks * bs,
            "wasted_blocks": wasted_blocks,
            "wasted_bytes": wasted_blocks * bs,
            "logical_bytes": logical,          # jumlah i_size seluruh file
            "reclaimable_bytes": wasted_blocks * bs,
            "files": n_files, "dirs": n_dirs, "links": n_links,
        }
    finally:
        if r is not None:
            try:
                r.f.close()
            except Exception:
                pass


def verify_checksum(f):

    sb = _Sb(f)
    if not sb.has_chksum:
        return (None, None)
    bs = sb.block_size
    length = bs - EROFS_SUPER_OFFSET if bs > EROFS_SUPER_OFFSET else bs
    f.seek(EROFS_SUPER_OFFSET)
    buf = bytearray(f.read(length))
    stored = struct.unpack_from("<I", buf, SB_CHECKSUM_OFF)[0]
    struct.pack_into("<I", buf, SB_CHECKSUM_OFF, 0)
    return stored, crc32c(0xFFFFFFFF, bytes(buf))
