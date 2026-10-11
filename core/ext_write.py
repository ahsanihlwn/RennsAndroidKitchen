#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0


import os
import struct
import time

S_IFMT   = 0o170000
S_IFDIR  = 0o040000
S_IFREG  = 0o100000
S_IFLNK  = 0o120000

EXT4_INDEX_FL       = 0x1000      # direktori pakai htree
EXT4_EXTENTS_FL     = 0x80000
EXT4_INLINE_DATA_FL = 0x10000000

# offset field superblock
SB_OFF              = 1024
SB_INODES_COUNT     = 0x00
SB_BLOCKS_COUNT_LO  = 0x04
SB_FREE_BLOCKS_LO   = 0x0C
SB_FREE_INODES      = 0x10
SB_FEATURE_COMPAT   = 0x5C
SB_FEATURE_INCOMPAT = 0x60
SB_FEATURE_RO       = 0x64
SB_UUID             = 0x68
SB_FREE_BLOCKS_HI   = 0x158

# offset field group descriptor
BG_BLOCK_BITMAP_LO  = 0x00
BG_INODE_BITMAP_LO  = 0x04
BG_INODE_TABLE_LO   = 0x08
BG_FREE_BLOCKS_LO   = 0x0C
BG_FREE_INODES_LO   = 0x0E
BG_USED_DIRS_LO     = 0x10
BG_FLAGS            = 0x12
BG_ITABLE_UNUSED_LO = 0x1C
BG_CHECKSUM         = 0x1E
# bagian 64-bit descriptor (hanya ada bila desc_size >= 64 & fitur 64bit)
BG_BLOCK_BITMAP_HI  = 0x20
BG_INODE_BITMAP_HI  = 0x24
BG_INODE_TABLE_HI   = 0x28
BG_FREE_BLOCKS_HI   = 0x2C
BG_FREE_INODES_HI   = 0x2E
BG_USED_DIRS_HI     = 0x30

EXT4_BG_INODE_UNINIT = 0x1
EXT4_BG_BLOCK_UNINIT = 0x2
EXT4_BG_INODE_ZEROED = 0x4

# offset field inode
I_MODE        = 0x00
I_SIZE_LO     = 0x04
I_DTIME       = 0x14
I_LINKS_COUNT = 0x1A
I_BLOCKS_LO   = 0x1C
I_FLAGS       = 0x20
I_BLOCK       = 0x28
I_SIZE_HI     = 0x6C
I_FILE_ACL_LO = 0x68


class ExtWriteError(Exception):
    pass


_CRC16_TAB = []
for _i in range(256):
    _c = _i
    for _ in range(8):
        _c = (_c >> 1) ^ (0xA001 if (_c & 1) else 0)
    _CRC16_TAB.append(_c)
del _i, _c


def crc16(crc: int, data: bytes) -> int:
    for b in data:
        crc = (crc >> 8) ^ _CRC16_TAB[(crc ^ b) & 0xFF]
    return crc & 0xFFFF


class ExtWriter:


    def __init__(self, reader):
        self.r = reader
        self.f = reader.f
        self.bs = reader.block_size

        if getattr(reader, "has_metadata_csum", False):
            raise ExtWriteError(
                "image pakai metadata_csum — hapus butuh recompute crc32c "
                "per-struktur (belum didukung)")

        self.f.seek(SB_OFF)
        sb = self.f.read(1024)
        self.feature_compat = struct.unpack_from("<I", sb, SB_FEATURE_COMPAT)[0]
        self.feature_ro     = struct.unpack_from("<I", sb, SB_FEATURE_RO)[0]
        self.uuid           = sb[SB_UUID:SB_UUID + 16]
        self.has_gdt_csum   = bool(self.feature_ro & 0x10)      # gdt_csum
        self.has_journal    = bool(self.feature_compat & 0x4)
        self.has_64bit      = getattr(reader, "has_64bit", False)

        if self.has_journal:
            raise ExtWriteError(
                "image punya journal aktif — tulis langsung bisa di-rollback "
                "saat mount (belum didukung)")

    # superblock
    def _sb_u32(self, off):
        self.f.seek(SB_OFF + off)
        return struct.unpack_from("<I", self.f.read(4), 0)[0]

    def _sb_set_u32(self, off, val):
        self.f.seek(SB_OFF + off)
        self.f.write(struct.pack("<I", val & 0xFFFFFFFF))

    def _free_blocks(self):
        lo = self._sb_u32(SB_FREE_BLOCKS_LO)
        hi = self._sb_u32(SB_FREE_BLOCKS_HI) if self.has_64bit else 0
        return (hi << 32) | lo

    def _set_free_blocks(self, n):
        self._sb_set_u32(SB_FREE_BLOCKS_LO, n & 0xFFFFFFFF)
        if self.has_64bit:
            self._sb_set_u32(SB_FREE_BLOCKS_HI, (n >> 32) & 0xFFFFFFFF)

    # group descriptor
    def _gd_offset(self, group):
        return self.r.gdt_block * self.bs + group * self.r.desc_size

    def _gd_read(self, group):
        self.f.seek(self._gd_offset(group))
        return bytearray(self.f.read(self.r.desc_size))

    def _gd_write(self, group, d):

        if self.has_gdt_csum:
            struct.pack_into("<H", d, BG_CHECKSUM, 0)
            c = crc16(0xFFFF, self.uuid)
            c = crc16(c, struct.pack("<I", group))
            c = crc16(c, bytes(d[:BG_CHECKSUM]))
            if self.r.desc_size > 0x20:
                c = crc16(c, bytes(d[0x20:self.r.desc_size]))
            struct.pack_into("<H", d, BG_CHECKSUM, c & 0xFFFF)
        self.f.seek(self._gd_offset(group))
        self.f.write(bytes(d))

    def _gd_field(self, d, off_lo, off_hi=None, width=2):
        if width == 2:
            v = struct.unpack_from("<H", d, off_lo)[0]
        else:
            v = struct.unpack_from("<I", d, off_lo)[0]
        if off_hi is not None and self.has_64bit and self.r.desc_size >= off_hi + 2:
            v |= struct.unpack_from("<H", d, off_hi)[0] << 16
        return v

    def _gd_set_field(self, d, off_lo, val, off_hi=None):
        struct.pack_into("<H", d, off_lo, val & 0xFFFF)
        if off_hi is not None and self.has_64bit and self.r.desc_size >= off_hi + 2:
            struct.pack_into("<H", d, off_hi, (val >> 16) & 0xFFFF)

    # bitmap
    def _bitmap_clear(self, bitmap_block, index):

        byte_off = bitmap_block * self.bs + (index >> 3)
        self.f.seek(byte_off)
        b = self.f.read(1)
        if not b:
            return False
        mask = 1 << (index & 7)
        if not (b[0] & mask):
            return False          # sudah bebas; jangan hitung dua kali
        self.f.seek(byte_off)
        self.f.write(bytes([b[0] & ~mask]))
        return True

    # inode
    def _read_inode_raw(self, ino):
        off = self.r._inode_offset(ino)
        self.f.seek(off)
        return off, bytearray(self.f.read(self.r.inode_size))

    def _inode_blocks(self, ino):

        mode, size, flags, iblock, raw = self.r._read_inode(ino)
        blocks = set()

        if flags & EXT4_INLINE_DATA_FL:
            return blocks, raw       # tak ada blok data terpisah

        if self.r.has_extents and (flags & EXT4_EXTENTS_FL):
            self._collect_extent(bytes(iblock), blocks, top=True)
        else:
            ptrs = list(struct.unpack_from("<15I", bytes(iblock), 0))
            for p in ptrs[:12]:
                if p:
                    blocks.add(p)
            per = self.bs // 4
            if ptrs[12]:
                blocks.add(ptrs[12])
                for p in self._read_ptr_block(ptrs[12], per):
                    if p:
                        blocks.add(p)
            if ptrs[13]:
                blocks.add(ptrs[13])
                for p1 in self._read_ptr_block(ptrs[13], per):
                    if not p1:
                        continue
                    blocks.add(p1)
                    for p2 in self._read_ptr_block(p1, per):
                        if p2:
                            blocks.add(p2)
            if ptrs[14]:
                blocks.add(ptrs[14])
                for p1 in self._read_ptr_block(ptrs[14], per):
                    if not p1:
                        continue
                    blocks.add(p1)
                    for p2 in self._read_ptr_block(p1, per):
                        if not p2:
                            continue
                        blocks.add(p2)
                        for p3 in self._read_ptr_block(p2, per):
                            if p3:
                                blocks.add(p3)

        # blok xattr eksternal (i_file_acl) ikut milik inode ini
        file_acl = struct.unpack_from("<I", bytes(raw), I_FILE_ACL_LO)[0]
        if file_acl:
            blocks.add(file_acl)
        return blocks, raw

    def _read_ptr_block(self, blk, per):
        self.f.seek(blk * self.bs)
        data = self.f.read(self.bs)
        if len(data) < self.bs:
            return []
        return list(struct.unpack_from(f"<{per}I", data, 0))

    def _collect_extent(self, node, blocks, top=False):
        if len(node) < 12 or struct.unpack_from("<H", node, 0)[0] != 0xF30A:
            return
        entries = struct.unpack_from("<H", node, 2)[0]
        depth   = struct.unpack_from("<H", node, 6)[0]
        if depth == 0:
            for i in range(entries):
                b = 12 + i * 12
                if b + 12 > len(node):
                    break
                ln  = struct.unpack_from("<H", node, b + 4)[0] & 0x7FFF
                hi  = struct.unpack_from("<H", node, b + 6)[0]
                lo  = struct.unpack_from("<I", node, b + 8)[0]
                phys = (hi << 32) | lo
                for k in range(ln):
                    blocks.add(phys + k)
        else:
            for i in range(entries):
                b = 12 + i * 12
                if b + 12 > len(node):
                    break
                lo = struct.unpack_from("<I", node, b + 4)[0]
                hi = struct.unpack_from("<H", node, b + 8)[0]
                leaf = (hi << 32) | lo
                if not leaf:
                    continue
                blocks.add(leaf)          # blok indeks juga dibebaskan
                self.f.seek(leaf * self.bs)
                self._collect_extent(self.f.read(self.bs), blocks)

    # free helpers
    def _free_block_list(self, blocks):

        freed = 0
        per_group = {}
        for b in blocks:
            if b == 0:
                continue
            g = (b - self.r.first_data_block) // self.r.blocks_per_group
            idx = (b - self.r.first_data_block) % self.r.blocks_per_group
            per_group.setdefault(g, []).append(idx)

        for g, idxs in per_group.items():
            if g >= self.r.groups:
                continue
            d = self._gd_read(g)
            bitmap_blk = self._gd_field(d, BG_BLOCK_BITMAP_LO,
                                        BG_BLOCK_BITMAP_HI, width=4)
            n = 0
            for idx in idxs:
                if self._bitmap_clear(bitmap_blk, idx):
                    n += 1
            if not n:
                continue
            free_b = self._gd_field(d, BG_FREE_BLOCKS_LO, BG_FREE_BLOCKS_HI)
            self._gd_set_field(d, BG_FREE_BLOCKS_LO, free_b + n,
                               BG_FREE_BLOCKS_HI)
            # blok sudah dipakai -> grup jelas bukan UNINIT lagi
            flags = struct.unpack_from("<H", d, BG_FLAGS)[0]
            struct.pack_into("<H", d, BG_FLAGS, flags & ~EXT4_BG_BLOCK_UNINIT)
            self._gd_write(g, d)
            freed += n

        if freed:
            self._set_free_blocks(self._free_blocks() + freed)
        return freed

    def _free_inode(self, ino, is_dir):
        g = (ino - 1) // self.r.inodes_per_group
        idx = (ino - 1) % self.r.inodes_per_group
        d = self._gd_read(g)
        bitmap_blk = self._gd_field(d, BG_INODE_BITMAP_LO,
                                    BG_INODE_BITMAP_HI, width=4)
        if not self._bitmap_clear(bitmap_blk, idx):
            return False
        free_i = self._gd_field(d, BG_FREE_INODES_LO, BG_FREE_INODES_HI)
        self._gd_set_field(d, BG_FREE_INODES_LO, free_i + 1, BG_FREE_INODES_HI)
        if is_dir:
            used = self._gd_field(d, BG_USED_DIRS_LO, BG_USED_DIRS_HI)
            if used:
                self._gd_set_field(d, BG_USED_DIRS_LO, used - 1, BG_USED_DIRS_HI)
        self._gd_write(g, d)
        self._sb_set_u32(SB_FREE_INODES, self._sb_u32(SB_FREE_INODES) + 1)
        return True

    def _mark_inode_deleted(self, ino):
        off, raw = self._read_inode_raw(ino)
        struct.pack_into("<H", raw, I_LINKS_COUNT, 0)
        struct.pack_into("<I", raw, I_DTIME, int(time.time()) & 0xFFFFFFFF)
        struct.pack_into("<I", raw, I_BLOCKS_LO, 0)
        struct.pack_into("<I", raw, I_SIZE_LO, 0)
        struct.pack_into("<I", raw, I_SIZE_HI, 0)
        self.f.seek(off)
        self.f.write(bytes(raw))

    def _adjust_parent_links(self, parent_ino, delta):

        off, raw = self._read_inode_raw(parent_ino)
        links = struct.unpack_from("<H", raw, I_LINKS_COUNT)[0]
        new = max(0, links + delta)
        struct.pack_into("<H", raw, I_LINKS_COUNT, new)
        self.f.seek(off)
        self.f.write(bytes(raw))
        return new

    # dirent
    def _dir_blocks(self, dir_ino):
        mode, size, flags, iblock, raw = self.r._read_inode(dir_ino)
        if (mode & S_IFMT) != S_IFDIR:
            raise ExtWriteError("bukan direktori")
        if flags & EXT4_INLINE_DATA_FL:
            raise ExtWriteError("direktori inline_data belum didukung")
        if flags & EXT4_INDEX_FL:
            raise ExtWriteError(
                "direktori pakai htree (dir_index) — hapus butuh rebuild "
                "index (belum didukung)")
        return self.r._dir_data_blocks(iblock, size, flags)

    def unlink_dirent(self, dir_ino, name):

        if isinstance(name, str):
            name = name.encode("utf-8")

        for blk in self._dir_blocks(dir_ino):
            self.f.seek(blk * self.bs)
            data = bytearray(self.f.read(self.bs))
            pos = 0
            prev = None
            while pos + 8 <= len(data):
                e_ino   = struct.unpack_from("<I", data, pos)[0]
                rec_len = struct.unpack_from("<H", data, pos + 4)[0]
                name_len = data[pos + 6]
                ftype    = data[pos + 7]
                if rec_len < 8 or pos + rec_len > len(data):
                    break
                nm = bytes(data[pos + 8: pos + 8 + name_len])
                if e_ino != 0 and nm == name:
                    if prev is None:
                        # entri pertama di blok: tak ada pendahulu yang bisa
                        # diperpanjang -> nolkan inode (dilewati pembaca)
                        struct.pack_into("<I", data, pos, 0)
                    else:
                        prev_len = struct.unpack_from("<H", data, prev + 4)[0]
                        struct.pack_into("<H", data, prev + 4, prev_len + rec_len)
                    self.f.seek(blk * self.bs)
                    self.f.write(bytes(data))
                    return e_ino, ftype
                prev = pos
                pos += rec_len
        raise FileNotFoundError(name.decode("utf-8", "replace"))

    def dir_is_empty(self, dir_ino):

        for (e_ino, nm, ft) in self.r._iter_dir(dir_ino):
            if nm not in (".", ".."):
                return False
        return True

    # delete
    def delete_inode(self, ino):

        mode, size, flags, iblock, raw = self.r._read_inode(ino)
        is_dir = (mode & S_IFMT) == S_IFDIR

        # symlink pendek (fast symlink) menyimpan target di i_block, bukan blok
        is_fast_link = ((mode & S_IFMT) == S_IFLNK and size < 60
                        and not (flags & EXT4_EXTENTS_FL))
        if is_fast_link:
            blocks = set()
            file_acl = struct.unpack_from("<I", bytes(raw), I_FILE_ACL_LO)[0]
            if file_acl:
                blocks.add(file_acl)
        else:
            blocks, raw = self._inode_blocks(ino)

        freed = self._free_block_list(blocks)
        self._mark_inode_deleted(ino)
        self._free_inode(ino, is_dir)
        return freed

    def remove_entry(self, parent_ino, name, child_ino=None):

        ino, ftype = self.unlink_dirent(parent_ino, name)
        if child_ino is not None and ino != child_ino:
            ino = child_ino

        mode, size, flags, iblock, raw = self.r._read_inode(ino)
        is_dir = (mode & S_IFMT) == S_IFDIR

        links = struct.unpack_from("<H", bytes(raw), I_LINKS_COUNT)[0]
        freed = 0
        if is_dir:
            # direktori: '.' menyumbang 1 link ke dirinya, '..' 1 link ke induk
            freed = self.delete_inode(ino)
            self._adjust_parent_links(parent_ino, -1)
        elif links > 1:
            # hard link masih ada di tempat lain: cukup kurangi link count
            off, r2 = self._read_inode_raw(ino)
            struct.pack_into("<H", r2, I_LINKS_COUNT, links - 1)
            self.f.seek(off)
            self.f.write(bytes(r2))
        else:
            freed = self.delete_inode(ino)

        self.f.flush()
        return {"ino": ino, "is_dir": is_dir, "blocks_freed": freed}

    # truncate
    def shrink_to(self, ino, new_size, old_size=None):

        mode, size, flags, iblock, raw = self.r._read_inode(ino)
        if old_size is not None:
            size = old_size
        if new_size >= size:
            return 0
        if flags & EXT4_INLINE_DATA_FL:
            return 0

        bs = self.bs
        keep = (new_size + bs - 1) // bs          # jumlah blok logis yg disimpan

        if self.r.has_extents and (flags & EXT4_EXTENTS_FL):
            freed = self._shrink_extents(ino, keep)
        else:
            freed = self._shrink_classic(ino, keep)

        if freed:
            # i_blocks dihitung dalam satuan 512 byte
            off, r2 = self._read_inode_raw(ino)
            iblocks = struct.unpack_from("<I", r2, I_BLOCKS_LO)[0]
            per_block = bs // 512
            struct.pack_into("<I", r2, I_BLOCKS_LO,
                             max(0, iblocks - freed * per_block))
            self.f.seek(off)
            self.f.write(bytes(r2))
        return freed

    def _shrink_extents(self, ino, keep):

        off, raw = self._read_inode_raw(ino)
        node = bytearray(raw[I_BLOCK:I_BLOCK + 60])
        if struct.unpack_from("<H", node, 0)[0] != 0xF30A:
            return 0
        if struct.unpack_from("<H", node, 6)[0] != 0:     # depth > 0
            return 0

        entries = struct.unpack_from("<H", node, 2)[0]
        free_blocks = []
        new_entries = 0
        for i in range(entries):
            b = 12 + i * 12
            e_blk = struct.unpack_from("<I", node, b)[0]
            e_len = struct.unpack_from("<H", node, b + 4)[0] & 0x7FFF
            hi    = struct.unpack_from("<H", node, b + 6)[0]
            lo    = struct.unpack_from("<I", node, b + 8)[0]
            phys  = (hi << 32) | lo

            if e_blk >= keep:
                # seluruh extent di luar rentang -> buang
                free_blocks.extend(phys + k for k in range(e_len))
                continue
            if e_blk + e_len > keep:
                # extent terpotong: sisakan bagian awalnya saja
                cut = keep - e_blk
                free_blocks.extend(phys + k for k in range(cut, e_len))
                struct.pack_into("<H", node, b + 4, cut)
            new_entries += 1

        if not free_blocks:
            return 0

        # rapatkan entry yang tersisa & nolkan sisa slot
        kept = []
        for i in range(entries):
            b = 12 + i * 12
            e_blk = struct.unpack_from("<I", node, b)[0]
            e_len = struct.unpack_from("<H", node, b + 4)[0] & 0x7FFF
            if e_blk < keep and e_len:
                kept.append(bytes(node[b:b + 12]))
        for i, ent in enumerate(kept):
            node[12 + i * 12: 24 + i * 12] = ent
        for i in range(len(kept), entries):
            node[12 + i * 12: 24 + i * 12] = b"\x00" * 12
        struct.pack_into("<H", node, 2, len(kept))

        raw[I_BLOCK:I_BLOCK + 60] = node
        self.f.seek(off)
        self.f.write(bytes(raw))
        return self._free_block_list(free_blocks)

    def _shrink_classic(self, ino, keep):

        off, raw = self._read_inode_raw(ino)
        ptrs = list(struct.unpack_from("<15I", raw, I_BLOCK))
        if ptrs[12] or ptrs[13] or ptrs[14]:
            return 0

        free_blocks = []
        for i in range(keep, 12):
            if ptrs[i]:
                free_blocks.append(ptrs[i])
                ptrs[i] = 0
        if not free_blocks:
            return 0
        struct.pack_into("<15I", raw, I_BLOCK, *ptrs)
        self.f.seek(off)
        self.f.write(bytes(raw))
        return self._free_block_list(free_blocks)

    def flush(self):
        self.f.flush()
        try:
            os.fsync(self.f.fileno())
        except (OSError, AttributeError, ValueError):
            pass
