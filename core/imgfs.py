#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0


import io
import os
import sys
import struct
import json
import logging
import threading
from contextlib import contextmanager


_EXT_MAGIC   = 0xEF53
_EROFS_MAGIC = 0xE0F5E1E2
_READ_SIZE   = 65536


def _r32(buf, off):
    if len(buf) < off + 4:
        return None
    return struct.unpack_from('<I', buf, off)[0]


def _r16(buf, off):
    if len(buf) < off + 2:
        return None
    return struct.unpack_from('<H', buf, off)[0]


S_IFMT   = 0o170000
S_IFSOCK = 0o140000
S_IFLNK  = 0o120000
S_IFREG  = 0o100000
S_IFBLK  = 0o060000
S_IFDIR  = 0o040000
S_IFCHR  = 0o020000
S_IFIFO  = 0o010000

S_ISUID = 0o4000
S_ISGID = 0o2000
S_ISVTX = 0o1000

_TYPE_CHAR = {
    S_IFSOCK: 's', S_IFLNK: 'l', S_IFREG: '-', S_IFBLK: 'b',
    S_IFDIR:  'd', S_IFCHR: 'c', S_IFIFO: 'p',
}


def mode_to_str(mode: int) -> str:
    t = _TYPE_CHAR.get(mode & S_IFMT, '?')

    def rwx(bits, x_on, x_special, lo_char, up_char):
        r = 'r' if bits & 0o4 else '-'
        w = 'w' if bits & 0o2 else '-'
        if x_special:
            x = up_char if not (bits & 0o1) else lo_char
        else:
            x = 'x' if bits & 0o1 else '-'
        return r + w + x

    u = rwx((mode >> 6) & 7, mode & 0o100, mode & S_ISUID, 's', 'S')
    g = rwx((mode >> 3) & 7, mode & 0o010, mode & S_ISGID, 's', 'S')
    o = rwx( mode       & 7, mode & 0o001, mode & S_ISVTX, 't', 'T')
    return t + u + g + o


_SPARSE_MAGIC = 0xED26FF3A


_IMAGE_WRITE_LOCKS = {}
_IMAGE_WRITE_LOCKS_GUARD = threading.Lock()


@contextmanager
def image_write_lock(path):

    key = os.path.normcase(os.path.abspath(path))
    with _IMAGE_WRITE_LOCKS_GUARD:
        lock = _IMAGE_WRITE_LOCKS.setdefault(key, threading.RLock())
    with lock:
        yield


def _detect_fs_bytes(data):

    if len(data) < 4:
        return ""
    for off in (0, 512, 1024, 4096):
        if _r32(data, off) == _EROFS_MAGIC:
            return "EROFS"
    for sb_start in (1024, 0):
        if _r16(data, sb_start + 0x38) == _EXT_MAGIC:
            return "EXT"
    return ""


def detect_fs(path):

    with open(path, 'rb') as f:
        data = f.read(_READ_SIZE)
    if len(data) < 4:
        return ""
    from core import native_backend
    try:
        if native_backend.erofs_detect(path).get("is_erofs"):
            return "EROFS"
    except Exception:
        pass
    if _r32(data, 0) == _SPARSE_MAGIC:
        from core.sparse_reader import SparseReader
        sr = SparseReader(path)
        try:
            head = sr.read(_READ_SIZE)
        finally:
            sr.close()
        return _detect_fs_bytes(head)
    return _detect_fs_bytes(data)


class Node:
    __slots__ = ("path", "is_dir", "size", "ino", "mode", "is_link", "target", "selinux")

    def __init__(self, path, is_dir, size, ino, mode, is_link=False, target=None, selinux=None):
        self.path    = path
        self.is_dir  = is_dir
        self.size    = size
        self.ino     = ino
        self.mode    = mode
        self.is_link = is_link
        self.target  = target
        self.selinux = selinux        # str konteks, atau None bila tak ada/gagal

    @property
    def perm(self):
        return mode_to_str(self.mode)

    def to_dict(self):
        d = {
            "path": self.path, "is_dir": self.is_dir,
            "size": self.size, "ino": self.ino,
            "selinux": self.selinux,
            "perm": self.perm,
            "mode": oct(self.mode & 0o7777),
        }
        if self.is_link:
            d["link"] = self.target
        return d


EXT_FT_DIR  = 2
EXT_FT_LINK = 7


class ExtReader:
    def __init__(self, f):
        self.f = f
        self._read_super()

    def _read_super(self):
        f = self.f
        f.seek(1024)
        sb = f.read(1024)

        self.inodes_count      = struct.unpack_from('<I', sb, 0x00)[0]
        self.blocks_count_lo   = struct.unpack_from('<I', sb, 0x04)[0]
        self.blocks_count_hi   = struct.unpack_from('<I', sb, 0x150)[0]
        log_block_size         = struct.unpack_from('<I', sb, 0x18)[0]
        self.block_size        = 1024 << log_block_size
        self.blocks_per_group  = struct.unpack_from('<I', sb, 0x20)[0]
        self.inodes_per_group  = struct.unpack_from('<I', sb, 0x28)[0]
        self.inode_size        = struct.unpack_from('<H', sb, 0x58)[0] or 128
        self.first_data_block  = struct.unpack_from('<I', sb, 0x14)[0]

        self.feature_incompat  = struct.unpack_from('<I', sb, 0x60)[0]
        self.feature_ro_compat = struct.unpack_from('<I', sb, 0x64)[0]
        self.desc_size         = struct.unpack_from('<H', sb, 0xFE)[0]

        self.has_64bit   = bool(self.feature_incompat & 0x80)
        self.has_extents = bool(self.feature_incompat & 0x40)
        self.has_metadata_csum = bool(self.feature_ro_compat & 0x400)
        if not self.has_64bit or self.desc_size == 0:
            self.desc_size = 32

        self.blocks_count = ((self.blocks_count_hi << 32) | self.blocks_count_lo
                             if self.has_64bit else self.blocks_count_lo)
        # jumlah group (gunakan high-half pada ext4 64-bit; tanpa ini mount
        # image besar berhenti sebelum group inode terakhir).
        self.groups = (self.blocks_count + self.blocks_per_group - 1) // self.blocks_per_group

        # lokasi group descriptor table = block tepat setelah superblock
        # block 0 (or 1 if block_size==1024) hold sb; GDT mulai block first_data_block+1
        self.gdt_block = self.first_data_block + 1

    def _read_block(self, blk):
        self.f.seek(blk * self.block_size)
        return self.f.read(self.block_size)

    def _group_desc(self, group):
        # baca satu descriptor
        off = self.gdt_block * self.block_size + group * self.desc_size
        self.f.seek(off)
        d = self.f.read(self.desc_size)
        bg_inode_table_lo = struct.unpack_from('<I', d, 0x08)[0]
        bg_inode_table_hi = 0
        if self.desc_size >= 0x28 and self.has_64bit:
            bg_inode_table_hi = struct.unpack_from('<I', d, 0x28)[0]
        return (bg_inode_table_hi << 32) | bg_inode_table_lo

    def _inode_offset(self, ino):
        # ino 1-based -> byte offset inode di image
        group = (ino - 1) // self.inodes_per_group
        index = (ino - 1) % self.inodes_per_group
        itable = self._group_desc(group)
        return itable * self.block_size + index * self.inode_size

    def _read_inode(self, ino):
        off = self._inode_offset(ino)
        self.f.seek(off)
        raw = self.f.read(self.inode_size)

        mode  = struct.unpack_from('<H', raw, 0x00)[0]
        size_lo = struct.unpack_from('<I', raw, 0x04)[0]
        size_hi = struct.unpack_from('<I', raw, 0x6C)[0]
        size  = (size_hi << 32) | size_lo
        flags = struct.unpack_from('<I', raw, 0x20)[0]
        # i_block area @ 0x28, 60 bytes
        iblock = raw[0x28:0x28 + 60]
        return mode, size, flags, iblock, raw

    # xattr (security.selinux)
    def _parse_xattr_entries(self, buf, base, names_value_base):


        pos = base
        while pos + 4 <= len(buf):
            name_len   = buf[pos]
            name_index = buf[pos + 1]
            if name_len == 0 and name_index == 0:
                break  # end-of-entries marker (4 zero bytes)
            value_offs = struct.unpack_from('<H', buf, pos + 2)[0]
            value_inum = struct.unpack_from('<I', buf, pos + 4)[0]
            value_size = struct.unpack_from('<I', buf, pos + 8)[0]
            name = buf[pos + 16: pos + 16 + name_len]
            # name_index 6 = "security." ; selinux disimpan di sana
            if name_index == 6 and name == b"selinux" and value_inum == 0:
                vstart = names_value_base + value_offs
                val = buf[vstart: vstart + value_size]
                return val.split(b"\x00", 1)[0].decode("utf-8", "replace")
            # entry berikutnya: header 16 + name, dipad ke kelipatan 4
            pos += 16 + name_len
            pos = (pos + 3) & ~3
        return None

    def _inode_selinux(self, raw):

        # in-inode
        extra = struct.unpack_from('<H', raw, 0x80)[0] if len(raw) >= 0x82 else 0
        if self.inode_size > 128 and extra:
            xstart = 128 + extra
            if xstart + 4 <= len(raw) and struct.unpack_from('<I', raw, xstart)[0] == 0xEA020000:
                # value-area untuk in-inode = tepat setelah magic header (xstart+4)
                val = self._parse_xattr_entries(raw, xstart + 4, xstart + 4)
                if val is not None:
                    return val
        # external xattr block (i_file_acl)
        file_acl = struct.unpack_from('<I', raw, 0x68)[0]
        if file_acl:
            self.f.seek(file_acl * self.block_size)
            blk = self.f.read(self.block_size)
            # ext4_xattr_header (32 bytes), magic @0 = 0xEA020000
            if len(blk) >= 32 and struct.unpack_from('<I', blk, 0)[0] == 0xEA020000:
                # entries mulai @32 ; e_value_offs relatif ke awal block
                return self._parse_xattr_entries(blk, 32, 0)
        return None

    # data block resolution
    def _extent_leaf_blocks(self, iblock):

        # extent header @ start of iblock
        magic = struct.unpack_from('<H', iblock, 0)[0]
        if magic != 0xF30A:
            return None
        entries = struct.unpack_from('<H', iblock, 2)[0]
        depth   = struct.unpack_from('<H', iblock, 6)[0]
        result = []
        if depth == 0:
            for i in range(entries):
                base = 12 + i * 12
                ee_block = struct.unpack_from('<I', iblock, base)[0]
                ee_len   = struct.unpack_from('<H', iblock, base + 4)[0]
                ee_start_hi = struct.unpack_from('<H', iblock, base + 6)[0]
                ee_start_lo = struct.unpack_from('<I', iblock, base + 8)[0]


                unwritten = bool(ee_len & 0x8000)
                phys = 0 if unwritten else ((ee_start_hi << 32) | ee_start_lo)
                result.append((ee_block, phys, ee_len & 0x7FFF))
            return result
        # depth > 0: index nodes -> baca block index, rekursi 1 level
        for i in range(entries):
            base = 12 + i * 12
            ei_leaf_lo = struct.unpack_from('<I', iblock, base + 4)[0]
            ei_leaf_hi = struct.unpack_from('<H', iblock, base + 8)[0]
            leaf = (ei_leaf_hi << 32) | ei_leaf_lo
            blk = self._read_block(leaf)
            sub = self._extent_leaf_blocks(blk)
            if sub:
                result.extend(sub)
        return result

    def _dir_data_blocks(self, iblock, size, flags):

        nblocks = (size + self.block_size - 1) // self.block_size

        if self.has_extents and (flags & 0x80000):
            ext = self._extent_leaf_blocks(iblock)
            blocks = [0] * nblocks
            if ext:
                for (logical, phys, length) in ext:
                    if not phys:
                        continue
                    for k in range(length):
                        lb = logical + k
                        if lb < nblocks:
                            blocks[lb] = phys + k
            return blocks


        return self._classic_file_blocks(iblock, size)

    def read_file(self, ino):

        mode, size, flags, iblock, raw = self._read_inode(ino)
        bs = self.block_size

        # inline data (ext4 inline_data feature): isi tersimpan di i_block + xattr
        if flags & 0x10000000:  # EXT4_INLINE_DATA_FL
            # 60 byte pertama di i_block; sisanya bisa di system.data xattr (jarang utk text kecil)
            return iblock[:size] if size <= 60 else iblock[:60]

        out = bytearray(size)
        if self.has_extents and (flags & 0x80000):
            ext = self._extent_leaf_blocks(iblock) or []
            for (logical, phys, length) in ext:
                if not phys:
                    continue
                for k in range(length):
                    pos = (logical + k) * bs
                    if pos >= size:
                        break
                    self.f.seek((phys + k) * bs)
                    chunk = self.f.read(min(bs, size - pos))
                    out[pos:pos + len(chunk)] = chunk
        else:
            # classic block map: 12 direct + single + double indirect
            for i, blk in enumerate(self._classic_file_blocks(iblock, size)):
                pos = i * bs
                if blk:
                    self.f.seek(blk * bs)
                    chunk = self.f.read(min(bs, size - pos))
                    out[pos:pos + len(chunk)] = chunk
        return bytes(out)

    def _file_phys_blocks(self, iblock, size, flags):

        bs = self.block_size
        nblocks = (size + bs - 1) // bs
        phys = [0] * nblocks
        if self.has_extents and (flags & 0x80000):
            for (logical, p, length) in (self._extent_leaf_blocks(iblock) or []):
                if not p:
                    continue
                for k in range(length):
                    lb = logical + k
                    if lb < nblocks:
                        phys[lb] = p + k
        else:
            blist = self._classic_file_blocks(iblock, size)
            for i in range(min(nblocks, len(blist))):
                phys[i] = blist[i]
        return phys

    def write_file(self, ino, new_data: bytes):

        mode, size, flags, iblock, raw = self._read_inode(ino)
        bs = self.block_size

        if flags & 0x10000000:   # inline_data
            raise ValueError("file inline_data — belum didukung untuk write")
        if (mode & S_IFMT) != S_IFREG:
            raise ValueError("bukan file reguler")

        phys = self._file_phys_blocks(iblock, size, flags)
        capacity = len(phys) * bs
        new_size = len(new_data)
        if new_size > capacity:
            raise ValueError(
                f"konten baru {new_size}B > kapasitas blok teralokasi {capacity}B "
                f"({len(phys)} blok). Edit ini butuh realokasi (repack), bukan in-place."
            )
        if getattr(self, "has_metadata_csum", False) and new_size != size:
            raise ValueError(
                "image pakai metadata_csum: hanya penggantian dengan ukuran "
                "yang sama yang aman; perubahan ukuran butuh recompute checksum/repack")
        if 0 in phys[: (new_size + bs - 1) // bs]:
            raise ValueError("file punya hole di rentang tulis (sparse) — in-place tak aman")

        # tulis per block; block terakhir di-zero-pad sampai batas block
        for i, pblk in enumerate(phys):
            start = i * bs
            if start >= new_size:
                break
            chunk = new_data[start:start + bs]
            if len(chunk) < bs:
                chunk = chunk + b"\x00" * (bs - len(chunk))   # pad sisa block
            self.f.seek(pblk * bs)
            self.f.write(chunk)


        if new_size != size:
            ioff = self._inode_offset(ino)
            self.f.seek(ioff + 0x04)
            self.f.write(struct.pack('<I', new_size & 0xFFFFFFFF))
            self.f.seek(ioff + 0x6C)
            self.f.write(struct.pack('<I', (new_size >> 32) & 0xFFFFFFFF))
        self.f.flush()
        return new_size

    # write permission (mode bits) IN-PLACE
    def write_mode(self, ino, new_perm_octal: int):

        if getattr(self, "has_metadata_csum", False):
            raise ValueError("image pakai metadata_csum — edit mode butuh recompute crc (belum didukung)")
        mode = struct.unpack_from('<H', self._read_inode(ino)[4], 0x00)[0]
        new_mode = (mode & ~0o7777) | (new_perm_octal & 0o7777)
        ioff = self._inode_offset(ino)
        self.f.seek(ioff + 0x00)
        self.f.write(struct.pack('<H', new_mode & 0xFFFF))
        self.f.flush()
        return new_mode

    # write security.selinux xattr IN-PLACE
    def _find_selinux_slot(self, buf, base, names_value_base):

        pos = base
        while pos + 4 <= len(buf):
            name_len   = buf[pos]
            name_index = buf[pos + 1]
            if name_len == 0 and name_index == 0:
                break
            value_offs = struct.unpack_from('<H', buf, pos + 2)[0]
            value_inum = struct.unpack_from('<I', buf, pos + 4)[0]
            value_size = struct.unpack_from('<I', buf, pos + 8)[0]
            name = buf[pos + 16: pos + 16 + name_len]
            if name_index == 6 and name == b"selinux" and value_inum == 0:
                return {
                    "entry_pos": pos,
                    "value_size_off": pos + 8,
                    "value_abs": names_value_base + value_offs,
                    "value_size": value_size,
                }
            pos += 16 + name_len
            pos = (pos + 3) & ~3
        return None

    def write_selinux(self, ino, new_ctx: str):

        if getattr(self, "has_metadata_csum", False):
            raise ValueError("image pakai metadata_csum — edit selinux butuh recompute crc (belum didukung)")

        new_bytes = new_ctx.encode("utf-8")
        ioff = self._inode_offset(ino)
        self.f.seek(ioff)
        raw = bytearray(self.f.read(self.inode_size))

        # in-inode xattr
        extra = struct.unpack_from('<H', raw, 0x80)[0] if len(raw) >= 0x82 else 0
        if self.inode_size > 128 and extra:
            xstart = 128 + extra
            if xstart + 4 <= len(raw) and struct.unpack_from('<I', raw, xstart)[0] == 0xEA020000:
                slot = self._find_selinux_slot(raw, xstart + 4, xstart + 4)
                if slot:
                    self._overwrite_xattr_value(ioff, slot, new_bytes,
                                                kind="inode")
                    return new_ctx

        # external xattr block (i_file_acl)
        file_acl = struct.unpack_from('<I', raw, 0x68)[0]
        if file_acl:
            boff = file_acl * self.block_size
            self.f.seek(boff)
            blk = bytearray(self.f.read(self.block_size))
            if len(blk) >= 32 and struct.unpack_from('<I', blk, 0)[0] == 0xEA020000:
                slot = self._find_selinux_slot(blk, 32, 0)
                if slot:
                    self._overwrite_xattr_value(boff, slot, new_bytes,
                                                kind="block")
                    return new_ctx

        raise ValueError("entry security.selinux tidak ditemukan di inode ini")

    def _overwrite_xattr_value(self, area_off, slot, new_bytes, kind):

        old_size = slot["value_size"]
        if len(new_bytes) > old_size:
            raise ValueError(
                f"konteks SELinux baru ({len(new_bytes)}B) > slot lama ({old_size}B). "
                f"Edit ini butuh realokasi xattr (repack), bukan in-place."
            )
        # value-area: tulis new_bytes lalu pad NUL sampai old_size (jaga layout)
        padded = new_bytes + b"\x00" * (old_size - len(new_bytes))
        self.f.seek(area_off + slot["value_abs"])
        self.f.write(padded)
        # update e_value_size = panjang aktual (tanpa pad)
        self.f.seek(area_off + slot["value_size_off"])
        self.f.write(struct.pack('<I', len(new_bytes)))
        self.f.flush()

    def _classic_file_blocks(self, iblock, size):

        nblocks = (size + self.block_size - 1) // self.block_size
        ptrs = list(struct.unpack_from('<15I', iblock, 0))
        blocks = ptrs[:12]
        per = self.block_size // 4

        def read_ind(blk):
            if not blk:
                return [0] * per
            self.f.seek(blk * self.block_size)
            ind = self.f.read(self.block_size)
            return [struct.unpack_from('<I', ind, j * 4)[0] for j in range(per)]

        if len(blocks) < nblocks and ptrs[12]:        # single indirect
            blocks += read_ind(ptrs[12])
        if len(blocks) < nblocks and ptrs[13]:        # double indirect
            for p in read_ind(ptrs[13]):
                if len(blocks) >= nblocks:
                    break
                blocks += read_ind(p)
        if len(blocks) < nblocks and ptrs[14]:        # triple indirect
            for p1 in read_ind(ptrs[14]):
                if len(blocks) >= nblocks:
                    break
                for p2 in read_ind(p1):
                    if len(blocks) >= nblocks:
                        break
                    blocks += read_ind(p2)
        return blocks[:nblocks]

    def _iter_dir(self, ino):
        mode, size, flags, iblock, _ = self._read_inode(ino)
        for blk in self._dir_data_blocks(iblock, size, flags):
            if not blk:
                continue
            data = self._read_block(blk)
            pos = 0
            while pos + 8 <= len(data):
                e_ino  = struct.unpack_from('<I', data, pos)[0]
                rec_len = struct.unpack_from('<H', data, pos + 4)[0]
                name_len = data[pos + 6]
                file_type = data[pos + 7]
                if (rec_len < 8 or rec_len & 3 or pos + rec_len > len(data)):
                    break
                if (e_ino != 0 and e_ino <= self.inodes_count and name_len > 0
                        and name_len <= rec_len - 8):
                    name = data[pos + 8: pos + 8 + name_len].decode('utf-8', 'replace')
                    if name not in (".", ".."):
                        yield e_ino, name, file_type
                pos += rec_len

    def _read_symlink_target(self, ino):
        mode, size, flags, iblock, raw = self._read_inode(ino)
        # fast symlink: target inline di i_block kalau size < 60
        if size < 60:
            return iblock[:size].decode('utf-8', 'replace')
        # slow symlink: di data block
        for blk in self._dir_data_blocks(iblock, size, flags):
            if blk:
                return self._read_block(blk)[:size].decode('utf-8', 'replace')
        return ""

    def walk(self):
        nodes = []
        ROOT_INO = 2
        visited_dirs = set()

        def recurse(ino, prefix):
            if ino in visited_dirs:
                return
            visited_dirs.add(ino)
            for e_ino, name, ftype in self._iter_dir(ino):
                path = prefix + "/" + name if prefix else name
                mode, size, flags, _ib, raw = self._read_inode(e_ino)
                is_dir  = (mode & S_IFMT) == S_IFDIR or ftype == EXT_FT_DIR
                is_link = (mode & S_IFMT) == S_IFLNK or ftype == EXT_FT_LINK
                target = None
                if is_link:
                    target = self._read_symlink_target(e_ino)
                selinux = self._inode_selinux(raw)
                nodes.append(Node(path, is_dir, size, e_ino, mode, is_link, target, selinux))
                if is_dir:
                    recurse(e_ino, path)

        recurse(ROOT_INO, "")
        return nodes


class _CloseProxy:
    __slots__ = ("_owner",)

    def __init__(self, owner):
        self._owner = owner

    def close(self):
        self._owner.close()


def _translate_erofs_error(exc: Exception, internal_path: str = "") -> Exception:
    msg = str(exc)
    low = msg.lower()
    if "path not found:" in low:
        return FileNotFoundError(internal_path or msg)
    if "is a directory" in low:
        return IsADirectoryError(internal_path or msg)
    if any(tok in low for tok in (
        "invalid erofs", "corrupted", "edit error", "compaction error",
        "invalid path", "invalid argument", "unsupported", "magic",
        "allow_grow", "compress", "read-only", "non-empty",
    )):
        return ValueError(msg)
    return exc


def _node_from_erofs_dict(d: dict, *, strip_root_slash: bool = True) -> Node:
    path = d.get("path", "")
    if strip_root_slash:
        path = path.strip("/")
    return Node(
        path=path,
        is_dir=bool(d.get("is_dir")),
        size=int(d.get("size", 0)),
        ino=int(d.get("nid", d.get("ino", 0))),
        mode=int(d.get("mode", 0)),
        is_link=bool(d.get("is_link")),
        target=d.get("target") or None,
        selinux=d.get("selinux") or None,
    )


class _NativeErofsHandle:
    """Thin adapter around rak_native.NativeErofsReader for mount/browse/read."""

    def __init__(self, native_reader):
        self._native = native_reader
        self.f = _CloseProxy(self)

    @property
    def root_nid(self) -> int:
        return int(self._native.root_nid)

    def walk(self):
        raw_nodes = json.loads(self._native.walk_json(False))
        return [
            _node_from_erofs_dict(item)
            for item in raw_nodes
            if item.get("path", "") not in ("", "/")
        ]

    def children(self, nid: int):
        return json.loads(self._native.children_json(int(nid)))

    def stat_nid(self, nid: int) -> dict:
        return json.loads(self._native.stat_nid_json(int(nid)))

    def stat_path(self, internal_path: str) -> Node:
        return _node_from_erofs_dict(
            json.loads(self._native.stat_path_json(str(internal_path)))
        )

    def read_file(self, nid: int) -> bytes:
        return bytes(self._native.read_file(int(nid)))

    def read_path(self, internal_path: str) -> bytes:
        return bytes(self._native.read_path(str(internal_path)))

    def open_file(self, nid: int):
        return io.BytesIO(self.read_file(nid))

    def close(self):
        native = getattr(self, "_native", None)
        if native is not None:
            native.close()


def build_nodes(path):
    # Selalu lewat open_reader agar Android sparse dibaca dalam ruang offset
    # asli juga pada caller lama yang masih memakai build_nodes().
    fs, reader = open_reader(path)
    try:
        return fs, reader.walk()
    finally:
        _close(reader)


def _fmt_size(n):
    if n < 1024:
        return f"{n} B"
    x = float(n)
    for u in ("KB", "MB", "GB"):
        x /= 1024.0
        if x < 1024:
            return f"{x:.1f} {u}"
    return f"{x:.1f} TB"


# byte yang dianggap "teks" walau di luar ASCII-printable
_TEXT_CTRL = {0x09, 0x0A, 0x0D, 0x0C, 0x08}   # tab, LF, CR, FF, backspace


def is_text_bytes(data: bytes, sample: int = 8192) -> bool:

    if not data:
        return True
    if data[:3] == b"\xef\xbb\xbf" or data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return True
    chunk = data[:sample]
    if b"\x00" in chunk:
        return False
    nontext = sum(1 for b in chunk if b < 0x20 and b not in _TEXT_CTRL)
    return (nontext / len(chunk)) <= 0.30


def decode_text(data: bytes):

    for enc in ("utf-8-sig", "utf-8"):
        try:
            return data.decode(enc), enc.replace("-sig", "")
        except UnicodeDecodeError:
            pass
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        try:
            return data.decode("utf-16"), "utf-16"
        except UnicodeDecodeError:
            pass
    return data.decode("latin-1"), "latin-1"


def _is_sparse_file(path):
    try:
        with open(path, 'rb') as f:
            return _r32(f.read(4), 0) == _SPARSE_MAGIC
    except Exception:
        return False


def open_reader(path, writable=False):

    fs = detect_fs(path)
    if not fs:
        raise ValueError("magic tidak dikenali (bukan EXT/EROFS)")
    if _is_sparse_file(path):
        if writable:
            raise NotImplementedError("write in-place tidak didukung untuk sparse")
        if fs == "EROFS":
            from core import native_backend
            return (fs, _NativeErofsHandle(native_backend.erofs_open_reader(path)))
        from core.sparse_reader import SparseReader
        f = SparseReader(path)
        return (fs, ExtReader(f))
    if fs == "EROFS":
        from core import native_backend
        return (fs, _NativeErofsHandle(native_backend.erofs_open_reader(path)))
    f = open(path, 'r+b' if writable else 'rb')
    return (fs, ExtReader(f))


_TEXT_EXTENSIONS = {
    # Plain text & config
    ".txt", ".text", ".log", ".ini", ".cfg", ".conf", ".rc", ".prop", ".properties",
    ".env", ".editorconfig", ".gitignore", ".gitattributes", ".gitmodules",
    ".dockerignore", ".toml", ".yaml", ".yml", ".json", ".json5", ".ndjson", ".jsonl", ".lock",
    ".version", ".changelog", ".changes", ".history", ".readme", ".todo", ".fixme", ".notes",
    ".license", ".copying", ".notice",
    # Markup & docs
    ".xml", ".xsl", ".xslt", ".xsd", ".dtd", ".rss", ".atom", ".svg",
    ".html", ".htm", ".xhtml",
    ".md", ".markdown", ".rst", ".tex",
    # Code & scripts
    ".sh", ".bash", ".zsh", ".fish", ".ksh", ".csh", ".tcsh", ".dash",
    ".py", ".pyw", ".pyi",
    ".js", ".mjs", ".cjs", ".jsx", ".ts", ".tsx",
    ".java", ".kt", ".kts", ".groovy", ".gradle", ".smali",
    ".c", ".h", ".cpp", ".hpp", ".cc", ".cxx", ".hxx", ".hh",
    ".rs", ".go", ".rb", ".php", ".lua", ".luau", ".pl", ".pm",
    ".css", ".scss", ".sass", ".less",
    ".qss", ".mk", ".mak", ".te", ".cil", ".policy",
    ".csv", ".tsv", ".diff", ".patch",
}

_TEXT_FILENAMES = {
    "makefile", "dockerfile", "kbuild", "gemfile", "rakefile",
    "fstab", "ueventd.rc", "build.prop", "default.prop",
}


def is_text_filename(name: str) -> bool:

    base = os.path.basename(name).lower()
    if base in _TEXT_FILENAMES:
        return True
    if (
        base.endswith("_file_contexts") or
        base.endswith("_fs_config") or
        base.endswith("_fs_options") or
        base.endswith("_info") or
        base.startswith("file_contexts") or
        base.startswith("fs_config") or
        base.startswith("fstab.") or
        base.endswith(".prop") or
        base.endswith(".rc")
    ):
        return True
    ext = os.path.splitext(base)[1]
    return ext in _TEXT_EXTENSIONS


def _root_node(fs, reader):
    """The directory walkers deliberately omit the filesystem root itself."""
    if fs == "EXT":
        mode, size, _flags, _blocks, raw = reader._read_inode(2)
        return Node("", True, size, 2, mode, selinux=reader._inode_selinux(raw))
    info = reader.stat_nid(reader.root_nid)
    return _node_from_erofs_dict(info, strip_root_slash=True)


def resolve_node(path, internal_path):

    internal_path = internal_path.strip("/").replace("\\", "/")
    fs, r = open_reader(path)
    if not internal_path:
        try:
            return r, _root_node(fs, r)
        except Exception:
            logging.exception("Reading filesystem root metadata failed: %s", path)
            _close(r)
            raise
    if fs == "EROFS":
        try:
            return r, r.stat_path("/" + internal_path)
        except Exception as exc:
            _close(r)
            raise _translate_erofs_error(exc, internal_path) from exc
    for n in r.walk():
        if n.path == internal_path:
            return r, n
    try:
        r.f.close()
    except Exception:
        pass
    raise FileNotFoundError(internal_path)


def read_ino(path, ino):

    fs, r = open_reader(path)
    try:
        data = r.read_file(ino)
        return data if data is not None else b""
    finally:
        try:
            r.f.close()
        except Exception:
            pass


def read_path(path, internal_path):

    r, node = resolve_node(path, internal_path)
    try:
        if node.is_dir:
            raise IsADirectoryError(internal_path)
        if isinstance(r, _NativeErofsHandle):
            try:
                norm = "/" + internal_path.strip("/").replace("\\", "/")
                return r.read_path(norm), node
            except Exception as exc:
                raise _translate_erofs_error(exc, internal_path) from exc
        return r.read_file(node.ino), node
    finally:
        try:
            r.f.close()
        except Exception:
            pass


def _safe_join(dest_dir, rel):

    rel = rel.replace("\\", "/").strip("/")
    parts = []
    for p in rel.split("/"):
        if p in ("", "."):
            continue
        if p == "..":
            raise ValueError(f"path tidak aman (mengandung '..'): {rel}")
        parts.append(p)
    if not parts:
        raise ValueError("path kosong")
    out = os.path.normpath(os.path.join(dest_dir, *parts))
    base = os.path.normpath(dest_dir)
    if out != base and not out.startswith(base + os.sep):
        raise ValueError(f"path keluar dari folder tujuan: {rel}")
    return out


def _win_safe_name(name):

    if os.name != "nt":
        return name
    out = []
    for ch in name:
        out.append("_" if ch in '<>:"|?*' or ord(ch) < 32 else ch)
    s = "".join(out).rstrip(" .")
    return s or "_"


def extract_path(img_path, internal_path, dest_dir, progress=None):

    internal_path = internal_path.strip("/").replace("\\", "/")
    os.makedirs(dest_dir, exist_ok=True)

    fs, r = open_reader(img_path)
    try:
        nodes = r.walk()
        target = next((n for n in nodes if n.path == internal_path), None)
        extracting_root = not internal_path
        if not extracting_root and target is None:
            raise FileNotFoundError(internal_path)

        if extracting_root:


            subset = list(nodes)
            parent = ""
        elif target.is_dir:
            subset = [target] + [n for n in nodes
                                 if n.path.startswith(internal_path + "/")]
        else:
            subset = [target]

        # yang ditulis ke disk relatif terhadap induk target, sehingga
        # mengekstrak "app/Foo" menghasilkan "<dest>/Foo/..."
        if not extracting_root:
            parent = internal_path.rsplit("/", 1)[0] if "/" in internal_path else ""
        prefix = len(parent) + 1 if parent else 0

        subset.sort(key=lambda n: n.path)      # induk sebelum anak
        total = len(subset)
        n_files = n_dirs = n_links = 0
        bytes_out = 0
        meta_lines = []
        skipped_links = []
        output_sources = {}

        for i, n in enumerate(subset):
            rel = n.path[prefix:]
            safe_rel = "/".join(_win_safe_name(p) for p in rel.split("/"))
            out_path = _safe_join(dest_dir, safe_rel)
            previous = output_sources.setdefault(os.path.normcase(out_path), n.path)
            if previous != n.path:
                raise ValueError(
                    "dua nama Linux menjadi path Windows yang sama: "
                    f"'{previous}' dan '{n.path}'")

            if n.is_dir:
                os.makedirs(out_path, exist_ok=True)
                n_dirs += 1
            elif n.is_link:
                os.makedirs(os.path.dirname(out_path), exist_ok=True)
                try:
                    if os.path.lexists(out_path):
                        os.remove(out_path)
                    os.symlink(n.target or "", out_path)
                    n_links += 1
                except (OSError, NotImplementedError, AttributeError):
                    # Windows tanpa hak bikin symlink: catat, jangan gagalkan
                    skipped_links.append((rel, n.target or ""))
            else:
                os.makedirs(os.path.dirname(out_path), exist_ok=True)
                data = r.read_file(n.ino)
                with open(out_path, "wb") as fh:
                    fh.write(data)
                bytes_out += len(data)
                n_files += 1

            meta_lines.append(
                f"{rel or '.'}\t{n.perm}\t{n.selinux or ''}"
                + (f"\tlink -> {n.target}" if n.is_link else ""))

            if progress:
                progress(i + 1, total, n.path)

        # metadata di SAMPING hasil, bukan di dalamnya, supaya folder yang
        # diekstrak tetap bersih dan bisa langsung dipakai
        base_name = (_win_safe_name(os.path.splitext(os.path.basename(img_path))[0])
                     if extracting_root else
                     _win_safe_name(internal_path.rsplit("/", 1)[-1]))
        meta_path = os.path.join(dest_dir, f"{base_name}_metadata.txt")
        try:
            with open(meta_path, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(f"# extract dari {os.path.basename(img_path)} :: "
                         f"{internal_path}\n")
                fh.write("# path\tpermission\tselinux\n")
                fh.write("\n".join(meta_lines) + "\n")
                if skipped_links:
                    fh.write("\n# symlink TIDAK dibuat (OS menolak); "
                             "target tercatat di sini:\n")
                    for (rel, tgt) in skipped_links:
                        fh.write(f"# {rel} -> {tgt}\n")
        except OSError:
            meta_path = None

        return {"dest": dest_dir,
                "files": n_files, "dirs": n_dirs, "links": n_links,
                "skipped_links": len(skipped_links),
                "bytes": bytes_out,
                "metadata": meta_path,
                "root": (dest_dir if extracting_root else
                         _safe_join(dest_dir, _win_safe_name(
                             internal_path.rsplit("/", 1)[-1]))) }
    finally:
        try:
            r.f.close()
        except Exception:
            pass


def _resolve_writable(path, internal_path):

    internal_path = internal_path.strip("/").replace("\\", "/")
    fs, r = open_reader(path, writable=True)
    if not internal_path:
        try:
            return fs, r, _root_node(fs, r)
        except Exception:
            logging.exception("Resolving writable filesystem root failed: %s", path)
            _close(r)
            raise
    for n in r.walk():
        if n.path == internal_path:
            return fs, r, n
    try:
        r.f.close()
    except Exception:
        pass
    raise FileNotFoundError(internal_path)


def _close(r):
    try:
        r.f.close()
    except Exception:
        pass


def write_path_data(path, internal_path, new_data: bytes, allow_grow=True,
                    strip_avb=True):

    with image_write_lock(path):
        return _write_path_data_unlocked(path, internal_path, new_data,
                                         allow_grow=allow_grow,
                                         strip_avb=strip_avb)


def preflight_write_path_data(path, internal_path, new_data: bytes,
                              allow_grow=True):

    with image_write_lock(path):
        return _preflight_write_path_data_unlocked(path, internal_path,
                                                   new_data, allow_grow)


def _preflight_write_path_data_unlocked(path, internal_path, new_data,
                                        allow_grow=True):
    norm = internal_path.strip("/").replace("\\", "/")
    fs = detect_fs(path)
    if not fs:
        raise ValueError("magic tidak dikenali (bukan EXT/EROFS)")
    if fs == "EROFS":
        if not norm:
            raise IsADirectoryError(internal_path)
        if _is_sparse_file(path):
            raise NotImplementedError("write in-place tidak didukung untuk sparse")
        from core import native_backend
        erofs_path = "/" + norm
        try:
            st = native_backend.erofs_stat(path, erofs_path)
        except Exception as exc:
            raise _translate_erofs_error(exc, norm) from exc
        if st.get("is_dir") or st.get("is_link"):
            raise IsADirectoryError(norm)
        try:
            plan = native_backend.erofs_edit_plan(path, erofs_path, len(new_data))
        except Exception as exc:
            raise _translate_erofs_error(exc, norm) from exc
        if plan.get("mode") == "append" and not allow_grow:
            raise ValueError("konten baru tidak muat dan allow_grow=False")
        out = dict(plan)
        out["grow"] = int(plan.get("grow_bytes", 0))
        return out

    fs, r, node = _resolve_writable(path, internal_path)
    try:
        if node.is_dir or node.is_link:
            raise IsADirectoryError(internal_path)
        mode, old_size, flags, iblock, _raw = r._read_inode(node.ino)
        if flags & 0x10000000:
            raise ValueError("file inline_data — belum didukung untuk write")
        if (mode & S_IFMT) != S_IFREG:
            raise ValueError("bukan file reguler")
        phys = r._file_phys_blocks(iblock, old_size, flags)
        bs = r.block_size
        new_size = len(new_data)
        capacity = len(phys) * bs
        if new_size > capacity:
            raise ValueError(
                f"konten baru {new_size}B > kapasitas blok teralokasi "
                f"{capacity}B ({len(phys)} blok)")
        needed = (new_size + bs - 1) // bs
        if 0 in phys[:needed]:
            raise ValueError("file punya hole di rentang tulis (sparse)")
        if getattr(r, "has_metadata_csum", False) and new_size != old_size:
            raise ValueError(
                "image pakai metadata_csum: ukuran konten harus sama "
                "atau image perlu direpack")
        return {"mode": "inplace_ext", "old_size": old_size,
                "new_size": new_size, "grow": 0}
    finally:
        _close(r)


def _write_path_data_unlocked(path, internal_path, new_data: bytes,
                              allow_grow=True, strip_avb=True):
    norm = internal_path.strip("/").replace("\\", "/")
    fs = detect_fs(path)
    if not fs:
        raise ValueError("magic tidak dikenali (bukan EXT/EROFS)")
    if fs == "EROFS":
        if not norm:
            raise IsADirectoryError(internal_path)
        if _is_sparse_file(path):
            raise NotImplementedError("write in-place tidak didukung untuk sparse")
        from core import native_backend
        erofs_path = "/" + norm
        try:
            st = native_backend.erofs_stat(path, erofs_path)
        except Exception as exc:
            raise _translate_erofs_error(exc, norm) from exc
        if st.get("is_dir") or st.get("is_link"):
            raise IsADirectoryError(norm)
        try:
            res = native_backend.erofs_edit_write(
                path,
                erofs_path,
                new_data,
                allow_grow=allow_grow,
                strip_avb=strip_avb,
            )
        except Exception as exc:
            raise _translate_erofs_error(exc, norm) from exc
        return int(res["new_size"])

    fs, r, node = _resolve_writable(path, internal_path)
    try:
        if node.is_dir or node.is_link:
            raise IsADirectoryError(internal_path)
        n = r.write_file(node.ino, new_data)
        if len(new_data) < node.size:
            from core import ext_write
            try:
                w = ext_write.ExtWriter(r)
                w.shrink_to(node.ino, len(new_data), old_size=node.size)
                w.flush()
            except ext_write.ExtWriteError:
                # image tak memenuhi syarat tulis struktural (metadata_csum
                # / journal): isi tetap tertulis, blok sisanya tak dilepas.
                pass
        return n
    finally:
        _close(r)


def plan_path_write(path, internal_path, new_len):

    internal_path = internal_path.strip("/").replace("\\", "/")
    fs = detect_fs(path)
    if fs != "EROFS":
        return None
    if not internal_path:
        raise IsADirectoryError(internal_path)
    from core import native_backend
    try:
        plan = native_backend.erofs_edit_plan(path, "/" + internal_path, int(new_len))
    except Exception as exc:
        raise _translate_erofs_error(exc, internal_path) from exc
    out = dict(plan)
    out["grow"] = int(plan.get("grow_bytes", 0))
    return out


def set_perm(path, internal_path, perm_octal):

    with image_write_lock(path):
        return _set_perm_unlocked(path, internal_path, perm_octal)


def _set_perm_unlocked(path, internal_path, perm_octal):

    if isinstance(perm_octal, str):
        perm_octal = int(perm_octal, 8)
    norm = internal_path.strip("/").replace("\\", "/")
    fs = detect_fs(path)
    if not fs:
        raise ValueError("magic tidak dikenali (bukan EXT/EROFS)")
    if fs == "EROFS":
        if _is_sparse_file(path):
            raise NotImplementedError("write in-place tidak didukung untuk sparse")
        from core import native_backend
        erofs_path = "/" + norm if norm else "/"
        try:
            return native_backend.erofs_edit_chmod(path, erofs_path, perm_octal)
        except Exception as exc:
            raise _translate_erofs_error(exc, norm) from exc

    fs, r, node = _resolve_writable(path, internal_path)
    try:
        return r.write_mode(node.ino, perm_octal)
    finally:
        _close(r)


def set_selinux(path, internal_path, new_ctx):

    with image_write_lock(path):
        return _set_selinux_unlocked(path, internal_path, new_ctx)


def _set_selinux_unlocked(path, internal_path, new_ctx):

    norm = internal_path.strip("/").replace("\\", "/")
    fs = detect_fs(path)
    if not fs:
        raise ValueError("magic tidak dikenali (bukan EXT/EROFS)")
    if fs == "EROFS":
        if _is_sparse_file(path):
            raise NotImplementedError("write in-place tidak didukung untuk sparse")
        from core import native_backend
        erofs_path = "/" + norm if norm else "/"
        try:
            return native_backend.erofs_edit_selinux(path, erofs_path, new_ctx)
        except Exception as exc:
            raise _translate_erofs_error(exc, norm) from exc

    fs, r, node = _resolve_writable(path, internal_path)
    try:
        return r.write_selinux(node.ino, new_ctx)
    finally:
        _close(r)


def _delete_path_ext(r, internal_path, recursive):

    from core import ext_write

    try:
        w = ext_write.ExtWriter(r)
    except ext_write.ExtWriteError as e:
        raise ValueError(str(e)) from e

    nodes = r.walk()
    by_path = {n.path: n for n in nodes}
    target = by_path.get(internal_path)
    if target is None:
        raise FileNotFoundError(internal_path)

    # kumpulkan yang akan dihapus: target + seluruh keturunannya
    victims = [target]
    if target.is_dir:
        victims += [n for n in nodes if n.path.startswith(internal_path + "/")]
        if len(victims) > 1 and not recursive:
            raise ValueError(f"folder tidak kosong: {internal_path}")

    # terdalam dulu (bottom-up)
    victims.sort(key=lambda n: n.path.count("/"), reverse=True)

    ROOT_INO = 2
    freed = 0
    try:
        for n in victims:
            parent_path = n.path.rsplit("/", 1)[0] if "/" in n.path else ""
            parent = by_path.get(parent_path)
            parent_ino = parent.ino if parent is not None else ROOT_INO
            name = n.path.rsplit("/", 1)[-1]
            res = w.remove_entry(parent_ino, name, child_ino=n.ino)
            freed += res["blocks_freed"]
    except ext_write.ExtWriteError as e:
        raise ValueError(str(e)) from e
    finally:
        w.flush()

    return {"removed": internal_path,
            "was_dir": target.is_dir,
            "entries_removed": len(victims),
            "blocks_freed": freed,
            "mode": "ext_unlink"}


def delete_path(path, internal_path, recursive=True):

    with image_write_lock(path):
        return _delete_path_unlocked(path, internal_path, recursive=recursive)


def _delete_path_unlocked(path, internal_path, recursive=True):

    internal_path = internal_path.strip("/").replace("\\", "/")
    if not internal_path:
        raise ValueError("root image tak bisa dihapus")

    fs = detect_fs(path)
    if not fs:
        raise ValueError("magic tidak dikenali (bukan EXT/EROFS)")

    if fs == "EROFS":
        if _is_sparse_file(path):
            raise NotImplementedError("write in-place tidak didukung untuk sparse")
        from core import native_backend
        try:
            res = native_backend.erofs_edit_rm(
                path, "/" + internal_path, recursive=bool(recursive), strip_avb=True
            )
        except Exception as exc:
            raise _translate_erofs_error(exc, internal_path) from exc
        return {
            "removed": internal_path,
            "was_dir": bool(res.get("was_dir", False)),
            "entries_before": int(res.get("entries_before", 0)),
            "entries_after": int(res.get("entries_after", 0)),
            "mode": res.get("mode", "unlink"),
            "grew": bool(res.get("grew_bytes", 0)),
            "grew_bytes": int(res.get("grew_bytes", 0)),
        }

    fs, r = open_reader(path, writable=True)
    try:
        return _delete_path_ext(r, internal_path, recursive)
    finally:
        _close(r)


def usage_report(path, progress=None):

    fs = detect_fs(path)
    if not fs:
        raise ValueError("magic tidak dikenali (bukan EXT/EROFS)")

    if fs == "EROFS":
        from core import native_backend
        try:
            rep = native_backend.erofs_usage(path)
        except Exception as exc:
            raise _translate_erofs_error(exc) from exc
        data_blocks = int(rep.get("data_blocks", 0))
        meta_blocks = int(rep.get("meta_blocks", 0))
        wasted_blocks = int(rep.get("wasted_blocks", 0))
        data_bytes = int(rep.get("data_bytes", 0))
        meta_bytes = int(rep.get("meta_bytes", 0))
        rep.setdefault("used_blocks", data_blocks + meta_blocks)
        rep.setdefault("free_blocks", wasted_blocks)
        rep.setdefault("used_bytes", data_bytes + meta_bytes)
        rep.setdefault("links", int(rep.get("symlinks", 0)))
        return rep


    r = None
    try:
        _, r = open_reader(path)
        r.f.seek(1024)
        sb = r.f.read(1024)
        free_lo = struct.unpack_from("<I", sb, 0x0C)[0]
        free_hi = struct.unpack_from("<I", sb, 0x158)[0] if r.has_64bit else 0
        free_blocks = (free_hi << 32) | free_lo
        total_blocks = r.blocks_count
        bs = r.block_size

        nodes = r.walk()
        n_files = sum(1 for n in nodes if not n.is_dir and not n.is_link)
        n_dirs  = sum(1 for n in nodes if n.is_dir)
        n_links = sum(1 for n in nodes if n.is_link)
        logical = sum(n.size for n in nodes if not n.is_dir and not n.is_link)
        used_blocks = max(0, total_blocks - free_blocks)

        return {
            "fs": "EXT",
            "block_size": bs,
            "file_size": os.path.getsize(path),
            "total_blocks": total_blocks,
            "meta_bytes": 0,
            "used_blocks": used_blocks,
            "used_bytes": used_blocks * bs,
            "wasted_blocks": 0,
            "wasted_bytes": 0,
            "free_blocks": free_blocks,
            "free_bytes": free_blocks * bs,
            "logical_bytes": logical,
            "reclaimable_bytes": 0,
            "files": n_files, "dirs": n_dirs, "links": n_links,
        }
    finally:
        if r is not None:
            _close(r)


def compact_image(path, progress=None, dry_run=False):

    with image_write_lock(path):
        return _compact_image_unlocked(path, progress=progress, dry_run=dry_run)


def _compact_image_unlocked(path, progress=None, dry_run=False):

    fs = detect_fs(path)
    if fs != "EROFS":
        raise NotImplementedError(
            "compact hanya untuk EROFS; pada EXT ruang sudah bebas saat hapus")
    if _is_sparse_file(path):
        raise NotImplementedError("write in-place tidak didukung untuk sparse")
    from core import native_backend
    try:
        return native_backend.erofs_compact(path, dry_run=bool(dry_run))
    except Exception as exc:
        raise _translate_erofs_error(exc) from exc


def list_image(path):

    fs, reader = open_reader(path)   # sparse-aware
    try:
        nodes = reader.walk()
    finally:
        try:
            reader.f.close()
        except Exception:
            pass
    out = []
    for n in nodes:
        out.append({
            "path": n.path,
            "is_dir": n.is_dir,
            "is_link": n.is_link,
            "size": n.size,
            "selinux": n.selinux or "",
            "perm": n.perm,
            "target": n.target or "",
            "ino": n.ino,
        })
    return out


def fmt_size(n):
    return _fmt_size(n)
