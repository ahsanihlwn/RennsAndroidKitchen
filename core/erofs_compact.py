#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0


import os
import struct

from core import erofs_write as ew

EROFS_INODE_FLAT_PLAIN         = 0
EROFS_INODE_COMPRESSED_FULL    = 1
EROFS_INODE_FLAT_INLINE        = 2
EROFS_INODE_COMPRESSED_COMPACT = 3
EROFS_INODE_CHUNK_BASED        = 4

_NULL_STARTBLK = 0xFFFFFFFF


class CompactError(Exception):
    pass


def _meta_extent(f, sb, ino, exact=False):

    bs = sb.block_size
    start = ino["off"]
    end = ino["data_off"]
    lay = ino["layout"]
    size = ino["size"]

    if lay == EROFS_INODE_FLAT_INLINE:
        end = ino["data_off"] + (size % bs)
    elif lay in (EROFS_INODE_COMPRESSED_FULL, EROFS_INODE_COMPRESSED_COMPACT):
        if exact:
            try:
                slots = _compressed_blkaddr_slots(f, sb, ino["nid"])
            except Exception:
                slots = None
            if slots:


                return start, max(off for (off, _v) in slots) + 8
            # tanpa slot terbaca, jangan menebak lebih panjang dari header
            hoff = (ino["data_off"] + 7) & ~7
            return start, hoff + 8
        hoff = (ino["data_off"] + 7) & ~7
        n_lcl = (size + bs - 1) // bs
        end = hoff + 8 + n_lcl * 4 + 16
    return start, end


def _inode_data_span(f, sb, ino):

    bs = sb.block_size
    lay = ino["layout"]
    size = ino["size"]

    if lay == EROFS_INODE_FLAT_PLAIN:
        start = ew._startblk(ino)
        if ew._is_null_start(ino, start) or not size:
            return None
        return start, (size + bs - 1) // bs

    if lay == EROFS_INODE_FLAT_INLINE:
        start = ew._startblk(ino)
        full = size // bs
        if ew._is_null_start(ino, start) or not full:
            return None
        return start, full

    if lay in (EROFS_INODE_COMPRESSED_FULL, EROFS_INODE_COMPRESSED_COMPACT):


        blocks = ew.inode_pblocks(f, sb, ino)
        if not blocks:
            return None
        lo, hi = min(blocks), max(blocks)
        return lo, hi - lo + 1

    if lay == EROFS_INODE_CHUNK_BASED:
        raise CompactError("inode chunk-based (layout 4) belum didukung")
    return None


def build_map(path, progress=None):

    from core import imgfs

    fs = imgfs.detect_fs(path)
    if fs != "EROFS":
        raise CompactError("hanya untuk image EROFS")
    if imgfs._is_sparse_file(path):
        raise CompactError("image sparse — unsparse dulu")

    f = open(path, "rb")
    try:
        r = imgfs.ErofsReader(f)
        sb = ew.open_sb(f)
        bs = sb.block_size

        if sb.feature_incompat & ew.EROFS_FEATURE_INCOMPAT_METABOX:
            raise CompactError("image pakai METABOX — belum didukung")
        # Dedupe/fragments berarti satu blok bisa dipakai banyak inode; rencana
        # per-inode di sini mengasumsikan kepemilikan tunggal.
        if sb.feature_incompat & ew.EROFS_FEATURE_INCOMPAT_FRAGMENTS:
            raise CompactError(
                "image pakai FRAGMENTS/DEDUPE — blok bisa dipakai bersama, "
                "pemadatan per-inode tak aman")

        nodes = r.walk()
        locked = set()
        spans = []          # (nid, layout, start_blk, nblocks)
        owner = {}

        allnodes = [None] + nodes
        total_n = len(allnodes)
        for i, n in enumerate(allnodes):
            nid = r.root_nid if n is None else n.ino
            try:
                ino = ew.read_inode(f, sb, nid)
            except Exception:
                continue

            a, b = _meta_extent(f, sb, ino)
            for blk in range(a // bs, (b + bs - 1) // bs):
                locked.add(blk)

            try:
                span = _inode_data_span(f, sb, ino)
            except CompactError:
                raise
            except Exception:
                span = None
            if span:
                start, cnt = span
                spans.append((nid, ino["layout"], start, cnt))
                for k in range(cnt):
                    owner[start + k] = nid

            if progress and (i % 200 == 0 or i == total_n - 1):
                progress("scan", i + 1, total_n)


        spans.sort(key=lambda s: s[2])
        groups = []          # (start, cnt, [(nid, layout), ...])
        for (nid, layout, start, cnt) in spans:
            end = start + cnt
            if groups and start < groups[-1][0] + groups[-1][1]:
                g_start, g_cnt, members = groups[-1]
                new_end = max(g_start + g_cnt, end)
                groups[-1] = (g_start, new_end - g_start,
                              members + [(nid, layout)])
            else:
                groups.append((start, cnt, [(nid, layout)]))

        total = sb.total_blocks
        used = set(owner) | locked
        holes = sorted(b for b in range(1, total) if b not in used)
        return {"locked": locked, "spans": spans, "groups": groups,
                "owner": owner,
                "used": used, "holes": holes, "total_blocks": total,
                "block_size": bs, "file_size": os.path.getsize(path)}
    finally:
        try:
            f.close()
        except Exception:
            pass


def plan_moves(m, reserved_blocks=0):

    locked = m["locked"]
    # Bekerja atas GRUP: span yang beririsan sudah digabung di build_map dan
    # harus berpindah bersama dengan delta yang sama.
    groups = sorted(m.get("groups") or
                    [(s[2], s[3], [(s[0], s[1])]) for s in m["spans"]],
                    key=lambda g: g[0])

    occupied = set(locked)      # blok terpakai pada tata letak BARU
    moves = []
    start_at = max(1, reserved_blocks)

    def free_at(at, cnt):
        for k in range(cnt):
            b = at + k
            if b in locked or b in occupied:
                return False
        return True

    cursor = start_at
    for (src, cnt, members) in groups:
        at = cursor
        while not free_at(at, cnt):
            at += 1
        for k in range(cnt):
            occupied.add(at + k)
        if at != src:
            # satu entri per anggota: penyalinan blok dilakukan sekali oleh
            # anggota pertama, sisanya hanya menambal alamat dengan delta sama
            for i, (nid, layout) in enumerate(members):
                moves.append((nid, layout, src, at, cnt if i == 0 else 0))
        cursor = at + cnt

    new_total = (max(occupied) + 1) if occupied else 1
    return moves, new_total


def _rewrite_flat_start(f, sb, nid, new_start):
    ino = ew.read_inode(f, sb, nid)
    ew._write_inode_fields(f, ino, startblk=new_start)


def _compressed_blkaddr_slots(f, sb, nid):

    from core import erofs_z

    ino = ew.read_inode(f, sb, nid)
    bs = sb.block_size
    sbi = {"block_size": bs, "blkszbits": sb.blkszbits,
           "lz4_0padding": bool(sb.feature_incompat & 0x1)}
    z = erofs_z.ZInode(f, sbi, ino)

    totalidx = z.totalidx
    ebase = z.ebase
    lclusterbits = z.lclusterbits

    compacted_4b_initial = ((32 - ebase % 32) // 4) & 7
    compacted_2b = 0
    if (z.h_advise & erofs_z.ADVISE_COMPACTED_2B) and compacted_4b_initial < totalidx:
        compacted_2b = (totalidx - compacted_4b_initial) & ~15

    out = []
    seen = set()
    for lcn in range(totalidx):
        pos = ebase
        amortizedshift = 2
        lcn_eff = lcn
        if lcn_eff >= compacted_4b_initial:
            pos += compacted_4b_initial * 4
            lcn_eff -= compacted_4b_initial
            if lcn_eff < compacted_2b:
                amortizedshift = 1
            else:
                pos += compacted_2b * 2
                lcn_eff -= compacted_2b
        pos += lcn_eff * (1 << amortizedshift)

        if (1 << amortizedshift) == 4 and lclusterbits <= 14:
            vcnt = 2
        elif (1 << amortizedshift) == 2 and lclusterbits <= 12:
            vcnt = 16
        else:
            raise CompactError("konfigurasi vcnt tak didukung")

        unit = vcnt << amortizedshift
        base = pos - (pos & (unit - 1))
        if base in seen:
            continue
        seen.add(base)

        f.seek(base + unit - 4)
        raw = f.read(4)
        if len(raw) < 4:
            continue
        out.append((base + unit - 4, struct.unpack("<I", raw)[0]))
    return out


def _rewrite_compressed_base(f, sb, nid, delta):

    from core import erofs_z

    ino = ew.read_inode(f, sb, nid)
    bs = sb.block_size
    sbi = {"block_size": bs, "blkszbits": sb.blkszbits,
           "lz4_0padding": bool(sb.feature_incompat & 0x1)}
    z = erofs_z.ZInode(f, sbi, ino)

    totalidx = z.totalidx
    ebase = z.ebase
    lclusterbits = z.lclusterbits

    compacted_4b_initial = ((32 - ebase % 32) // 4) & 7
    compacted_2b = 0
    if (z.h_advise & erofs_z.ADVISE_COMPACTED_2B) and compacted_4b_initial < totalidx:
        compacted_2b = (totalidx - compacted_4b_initial) & ~15

    # Kumpulkan posisi awal tiap unit indeks, lalu geser blkaddr-nya sekali.
    seen = set()
    for lcn in range(totalidx):
        pos = ebase
        amortizedshift = 2
        lcn_eff = lcn
        if lcn_eff >= compacted_4b_initial:
            pos += compacted_4b_initial * 4
            lcn_eff -= compacted_4b_initial
            if lcn_eff < compacted_2b:
                amortizedshift = 1
            else:
                pos += compacted_2b * 2
                lcn_eff -= compacted_2b
        pos += lcn_eff * (1 << amortizedshift)

        if (1 << amortizedshift) == 4 and lclusterbits <= 14:
            vcnt = 2
        elif (1 << amortizedshift) == 2 and lclusterbits <= 12:
            vcnt = 16
        else:
            raise CompactError("konfigurasi vcnt tak didukung")

        unit = vcnt << amortizedshift
        base = pos - (pos & (unit - 1))
        if base in seen:
            continue
        seen.add(base)

        f.seek(base + unit - 4)
        raw = f.read(4)
        if len(raw) < 4:
            continue
        blkaddr = struct.unpack("<I", raw)[0]
        f.seek(base + unit - 4)
        f.write(struct.pack("<I", (blkaddr + delta) & 0xFFFFFFFF))


def apply_moves(path, moves, new_total, m, progress=None, patch_inodes=True):

    bs = m["block_size"]


    patches = []      # (offset_byte, nilai_le32)
    f = open(path, "rb")
    try:
        sb = ew.open_sb(f)
        for (nid, layout, src, dst, cnt) in (moves if patch_inodes else ()):
            if layout in (EROFS_INODE_FLAT_PLAIN, EROFS_INODE_FLAT_INLINE):
                ino = ew.read_inode(f, sb, nid)
                patches.append((ino["off"] + ino["u_off"], dst))
            else:


                for (off, val) in _compressed_blkaddr_slots(f, sb, nid):
                    patches.append((off, (val + (dst - src)) & 0xFFFFFFFF))
    finally:
        try:
            f.close()
        except Exception:
            pass


    copies = [mv for mv in moves if mv[4] > 0]
    # Urutkan agar penyalinan tak menimpa sumber yang belum dipindah: yang
    # bergerak MAJU disalin dari belakang, yang MUNDUR dari depan.
    forward = sorted([mv for mv in copies if mv[3] > mv[2]],
                     key=lambda x: x[2], reverse=True)
    backward = sorted([mv for mv in copies if mv[3] < mv[2]],
                      key=lambda x: x[2])
    ordered = backward + forward


    stage_path = path + ".compact-stage"
    CH = 8 * 1024 * 1024

    f = open(path, "r+b")
    try:
        with open(stage_path, "w+b") as stage:
            offsets = []
            cur = 0
            for (nid, layout, src, dst, cnt) in ordered:
                total_bytes = cnt * bs
                s_off = src * bs
                pos = 0
                while pos < total_bytes:
                    n = min(CH, total_bytes - pos)
                    f.seek(s_off + pos)
                    stage.write(f.read(n))
                    pos += n
                offsets.append((cur, total_bytes, dst))
                cur += total_bytes

            for i, (st_off, length, dst) in enumerate(offsets):
                d_off = dst * bs
                pos = 0
                while pos < length:
                    n = min(CH, length - pos)
                    stage.seek(st_off + pos)
                    f.seek(d_off + pos)
                    f.write(stage.read(n))
                    pos += n
                if progress and (i % 50 == 0 or i == len(offsets) - 1):
                    progress("move", i + 1, len(offsets))

        for (off, val) in patches:
            f.seek(off)
            f.write(struct.pack("<I", val))

        f.flush()
        try:
            os.fsync(f.fileno())
        except (OSError, AttributeError, ValueError):
            pass
    finally:
        try:
            f.close()
        except Exception:
            pass
        try:
            os.remove(stage_path)
        except OSError:
            pass


    return new_total * bs


def _inode_meta_blob(f, sb, ino):

    a, b = _meta_extent(f, sb, ino)
    f.seek(a)
    return f.read(b - a), a, b


def plan_inode_relocation(path):

    from core import imgfs

    f = open(path, "rb")
    try:
        r = imgfs.ErofsReader(f)
        sb = ew.open_sb(f)
        bs = sb.block_size

        nodes = r.walk()
        items = []
        for n in [None] + nodes:
            nid = r.root_nid if n is None else n.ino
            try:
                ino = ew.read_inode(f, sb, nid)
            except Exception:
                continue
            # exact=True: blob akan DISALIN, jadi panjangnya harus persis —
            # lihat catatan di _meta_extent soal blob yang saling menimpa.
            a, b = _meta_extent(f, sb, ino, exact=True)
            items.append((nid, a, b - a, ino["layout"]))

        # urut posisi asal supaya yang sudah di depan cenderung tetap
        items.sort(key=lambda x: x[1])


        clipped = []
        for i, (nid, a, size, layout) in enumerate(items):
            if i + 1 < len(items):
                next_a = items[i + 1][1]
                if a + size > next_a:
                    size = max(0, next_a - a)
            if size:
                clipped.append((nid, a, size, layout))
        items = clipped


        base = sb.meta_blkaddr * bs
        first_nid = min(i[0] for i in items)
        cursor = base + first_nid * 32

        mapping = {}
        for (nid, a, size, layout) in items:
            new_off = cursor


            if size <= bs and (new_off % bs) + size > bs:
                new_off = ((new_off // bs) + 1) * bs
            new_nid = (new_off - base) // 32
            mapping[nid] = new_nid
            cursor = new_off + ((size + 31) & ~31)

        meta_end = cursor
        return mapping, meta_end, items
    finally:
        try:
            f.close()
        except Exception:
            pass


def _remap_dirents(f, sb, r, mapping, bs):

    S_IFMT, S_IFDIR = 0o170000, 0o040000
    changed = 0
    for nid in list(mapping):
        try:
            ino = ew.read_inode(f, sb, nid)
        except Exception:
            continue
        if (ino["mode"] & S_IFMT) != S_IFDIR:
            continue
        try:
            data = bytearray(ew._read_inode_data(f, sb, ino))
        except Exception:
            continue

        # dirent dikelompokkan per blok; ubah nid di tempat lalu tulis balik
        pos = 0
        total = len(data)
        dirty = False
        while pos < total:
            blk = data[pos: pos + bs]
            blen = len(blk)
            if blen < 12:
                break
            first_nameoff = struct.unpack_from("<H", blk, 8)[0]
            count = first_nameoff // 12
            if count == 0 or count * 12 > blen:
                break
            for i in range(count):
                e = pos + i * 12
                old = struct.unpack_from("<Q", data, e)[0]
                new = mapping.get(old)
                if new is not None and new != old:
                    struct.pack_into("<Q", data, e, new)
                    dirty = True
            pos += bs

        if dirty:
            _write_inode_data(f, sb, ino, bytes(data), bs)
            changed += 1
    return changed


def _write_inode_data(f, sb, ino, data, bs):

    lay = ino["layout"]
    if lay == EROFS_INODE_FLAT_PLAIN:
        f.seek(ew._startblk(ino) * bs)
        f.write(data)
        return
    if lay == EROFS_INODE_FLAT_INLINE:
        full = (ino["size"] // bs) * bs
        if full:
            f.seek(ew._startblk(ino) * bs)
            f.write(data[:full])
        if len(data) > full:
            f.seek(ino["data_off"])
            f.write(data[full:])
        return
    raise CompactError(f"tak bisa menulis balik direktori layout {lay}")


def _stage_metadata(path):

    from core import imgfs

    mapping, meta_end, items = plan_inode_relocation(path)

    f = open(path, "rb")
    try:
        r = imgfs.ErofsReader(f)
        sb = ew.open_sb(f)
        bs = sb.block_size
        S_IFMT, S_IFDIR = 0o170000, 0o040000

        blobs = {}
        for (nid, a, size, layout) in items:
            f.seek(a)
            blobs[nid] = bytearray(f.read(size))


        dirs_in_data = {}
        for (nid, a, size, layout) in items:
            try:
                ino = ew.read_inode(f, sb, nid)
            except Exception:
                continue
            if (ino["mode"] & S_IFMT) != S_IFDIR:
                continue

            dsize = ino["size"]
            if layout == EROFS_INODE_FLAT_INLINE:
                full = (dsize // bs) * bs
            else:
                full = ((dsize + bs - 1) // bs) * bs
            if full:
                # sebagian/seluruh dirent ada di blok data -> tangani nanti
                dirs_in_data[nid] = (ew._startblk(ino), full)

            if layout != EROFS_INODE_FLAT_INLINE:
                continue
            tail_len = dsize - full
            if not tail_len:
                continue

            blob = blobs[nid]
            head = len(blob) - tail_len
            tail = bytearray(blob[head:])
            _remap_dirent_buf(tail, 0, len(tail), mapping)
            blob[head:] = tail

        # Posisi slot blkaddr RELATIF terhadap awal inode, supaya alamatnya
        # bisa ditambal di dalam blob nanti tanpa membaca ulang dari disk.
        slot_rel = {}
        for (nid, a, size, layout) in items:
            if layout not in (EROFS_INODE_COMPRESSED_FULL,
                              EROFS_INODE_COMPRESSED_COMPACT):
                continue
            try:
                slots = _compressed_blkaddr_slots(f, sb, nid)
            except Exception:
                continue
            # simpan pula NILAI aslinya: delta dihitung dari basis span
            # (min blkaddr), yang belum tentu sama dengan nilai slot pertama.
            slot_rel[nid] = [(off - a, val) for (off, val) in slots]

        new_root = mapping.get(r.root_nid, r.root_nid)
        return {"mapping": mapping, "blobs": blobs, "items": items,
                "new_root": new_root, "meta_end": meta_end,
                "dirs_in_data": dirs_in_data, "has_48bit": sb.has_48bit,
                "slot_rel": slot_rel, "moves": (),
                "base": sb.meta_blkaddr * bs}
    finally:
        try:
            f.close()
        except Exception:
            pass


def _remap_dirent_buf(buf, start, end, mapping):

    if end - start < 12:
        return False
    count = struct.unpack_from("<H", buf, start + 8)[0] // 12
    if count == 0 or start + count * 12 > end:
        return False
    dirty = False
    for i in range(count):
        e = start + i * 12
        old = struct.unpack_from("<Q", buf, e)[0]
        new = mapping.get(old)
        if new is not None and new != old:
            struct.pack_into("<Q", buf, e, new)
            dirty = True
    return dirty


def _write_staged_metadata(path, staged, progress=None):

    mapping = staged["mapping"]
    blobs = staged["blobs"]
    items = staged["items"]
    base = staged["base"]

    f = open(path, "r+b")
    try:
        sb = ew.open_sb(f)
        bs = sb.block_size


        for (nid, layout, src, dst, cnt_blk) in staged.get("moves", ()):
            blob = blobs.get(nid)
            if blob is None:
                continue
            if layout in (EROFS_INODE_FLAT_PLAIN, EROFS_INODE_FLAT_INLINE):
                u_off = ew.I_U_OFF_EXTENDED if (struct.unpack_from("<H", blob, 0)[0] & 1) else ew.I_U_OFF_COMPACT
                if len(blob) >= u_off + 4:
                    struct.pack_into("<I", blob, u_off,
                                     dst & 0xFFFFFFFF)
            else:


                delta = dst - src
                for (rel, orig) in staged["slot_rel"].get(nid, ()):
                    if rel + 4 <= len(blob):
                        struct.pack_into("<I", blob, rel,
                                         (orig + delta) & 0xFFFFFFFF)


        moved_to = {mv[0]: mv[3] for mv in staged.get("moves", ())}
        dir_writes = []
        for nid, (old_start, full_bytes) in staged["dirs_in_data"].items():
            if old_start == _NULL_STARTBLK or not full_bytes:
                continue
            start = moved_to.get(nid, old_start)
            for k in range(full_bytes // bs):
                f.seek((start + k) * bs)
                blk = bytearray(f.read(bs))
                if len(blk) < bs:
                    break
                if _remap_dirent_buf(blk, 0, len(blk), mapping):
                    dir_writes.append(((start + k) * bs, bytes(blk)))


        for (off, data) in dir_writes:
            f.seek(off)
            f.write(data)

        # 2) pindahkan blob inode ke posisi barunya
        for i, (nid, a, size, layout) in enumerate(items):
            f.seek(base + mapping[nid] * 32)
            f.write(bytes(blobs[nid]))
            if progress and (i % 200 == 0 or i == len(items) - 1):
                progress("inode", i + 1, len(items))

        # 3) rootnid di superblock
        new_root = staged["new_root"]
        if staged["has_48bit"]:
            f.seek(ew.EROFS_SUPER_OFFSET + 128)
            f.write(struct.pack("<Q", new_root))
        else:
            f.seek(ew.EROFS_SUPER_OFFSET + ew.SB_ROOTNID_2B_OFF)
            f.write(struct.pack("<H", new_root & 0xFFFF))

        f.flush()
        try:
            os.fsync(f.fileno())
        except (OSError, AttributeError, ValueError):
            pass
    finally:
        try:
            f.close()
        except Exception:
            pass


def relocate_inodes(path, progress=None):

    from core import imgfs

    mapping, meta_end, items = plan_inode_relocation(path)
    moved = {k: v for k, v in mapping.items() if k != v}
    if not moved:
        return 0, meta_end

    f = open(path, "r+b")
    try:
        r = imgfs.ErofsReader(f)
        sb = ew.open_sb(f)
        bs = sb.block_size
        base = sb.meta_blkaddr * bs

        # 1) baca seluruh blob metadata SEBELUM apa pun bergeser
        blobs = {}
        for (nid, a, size, layout) in items:
            f.seek(a)
            blobs[nid] = f.read(size)

        # 2) remap nid di dirent (masih memakai tata letak lama)
        if progress:
            progress("remap", 0, 1)
        _remap_dirents(f, sb, r, mapping, bs)

        # 3) baca ULANG blob: isi dirent sudah berubah pada langkah 2, dan
        #    untuk FLAT_INLINE sebagian dirent tersimpan di dalam blob itu
        for (nid, a, size, layout) in items:
            f.seek(a)
            blobs[nid] = f.read(size)

        # 4) tulis blob ke posisi baru
        for i, (nid, a, size, layout) in enumerate(items):
            new_off = base + mapping[nid] * 32
            f.seek(new_off)
            f.write(blobs[nid])
            if progress and (i % 200 == 0 or i == len(items) - 1):
                progress("inode", i + 1, len(items))

        # 5) rootnid di superblock
        new_root = mapping.get(r.root_nid, r.root_nid)
        if sb.has_48bit:
            f.seek(ew.EROFS_SUPER_OFFSET + 128)
            f.write(struct.pack("<Q", new_root))
        else:
            f.seek(ew.EROFS_SUPER_OFFSET + ew.SB_ROOTNID_2B_OFF)
            f.write(struct.pack("<H", new_root & 0xFFFF))

        sb.refresh_checksum()
        f.flush()
        try:
            os.fsync(f.fileno())
        except (OSError, AttributeError, ValueError):
            pass
    finally:
        try:
            f.close()
        except Exception:
            pass
    return len(moved), meta_end


def compact(path, progress=None, dry_run=False, relocate_meta=True):

    m = build_map(path, progress=progress)
    before = m["file_size"]
    bs = m["block_size"]

    reserved = 0
    n_reloc = 0
    if relocate_meta:
        mapping, meta_end, items = plan_inode_relocation(path)
        reserved = (meta_end + bs - 1) // bs
        n_reloc = sum(1 for k, v in mapping.items() if k != v)


        m = dict(m)
        m["locked"] = {b for b in m["locked"] if b < reserved}

    moves, data_total = plan_moves(m, reserved_blocks=reserved)
    new_total = max(data_total, reserved)
    new_size = new_total * bs

    result = {
        "before": before,
        "after": new_size,
        "saved": max(0, before - new_size),
        "moves": len(moves),
        "blocks_moved": sum(mv[4] for mv in moves),
        "holes_before": len(m["holes"]),
        "locked_blocks": len(m["locked"]),
        "total_blocks_before": m["total_blocks"],
        "total_blocks_after": new_total,
        "meta_blocks_after": reserved,
        "inodes_relocated": n_reloc,
        "dry_run": dry_run,
    }
    if dry_run:
        return result

    if not moves and not n_reloc and new_size >= before:
        result["after"] = before
        result["saved"] = 0
        return result


    staged = None
    if relocate_meta and n_reloc:
        staged = _stage_metadata(path)
        # rencana pemindahan diteruskan supaya alamat di dalam blob bisa
        # ditambal tanpa membaca ulang inode dari disk
        staged["moves"] = moves

    apply_moves(path, moves, new_total, m, progress=progress,
                patch_inodes=(staged is None))

    # 3) metadata ke depan (memakai blob yang sudah diamankan)
    if staged is not None:
        _write_staged_metadata(path, staged, progress=progress)

    # 4) rapikan superblock & potong ekor
    with open(path, "r+b") as f:
        sb = ew.open_sb(f)
        sb.set_total_blocks(new_total)
        sb.refresh_checksum()
        f.flush()
    if new_size < os.path.getsize(path):
        with open(path, "r+b") as f:
            f.truncate(new_size)

    result["after"] = os.path.getsize(path)
    result["saved"] = max(0, before - result["after"])
    return result
