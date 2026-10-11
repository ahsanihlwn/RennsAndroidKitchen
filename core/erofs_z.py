#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0


import io
import struct
import zlib

try:
    import lz4.block as _lz4block
except ImportError:
    _lz4block = None

try:
    import zstandard as _zstandard
except ImportError:
    _zstandard = None

PLAIN, HEAD1, NONHEAD, HEAD2 = 0, 1, 2, 3
LCLUSTER_TYPE_MAX = 4
LI_D0_CBLKCNT = 1 << 11

ADVISE_COMPACTED_2B    = 0x0001
ADVISE_BIG_PCLUSTER_1  = 0x0002
ADVISE_BIG_PCLUSTER_2  = 0x0004
ADVISE_INLINE_PCLUSTER = 0x0008
ADVISE_INTERLACED      = 0x0010
ADVISE_FRAGMENT        = 0x0020

LAYOUT_COMPRESSED_FULL = 1
LAYOUT_COMPRESSED_COMPACT = 3

ALG_LZ4, ALG_LZMA, ALG_DEFLATE, ALG_ZSTD = range(4)


def _ilog2(n):
    return n.bit_length() - 1


def _le32(buf, off):
    c = buf[off:off + 4]
    if len(c) < 4:
        c = c + b"\x00" * (4 - len(c))
    return struct.unpack("<I", c)[0]


def _decode_compactedbits(lobits, blob, pos):
    v = _le32(blob, pos // 8) >> (pos & 7)
    lo = v & ((1 << lobits) - 1)
    typ = (v >> lobits) & 3
    return lo, typ


class _Rec:

    __slots__ = ("lcn", "type", "clusterofs", "pblk", "delta0", "delta1")
    def __init__(self):
        self.lcn = 0; self.type = 0; self.clusterofs = 0
        self.pblk = 0; self.delta0 = 0; self.delta1 = 0


class ZInode:
    def __init__(self, f, sbi, inode):
        self.f = f
        self.bs = sbi["block_size"]
        self.blkszbits = sbi["blkszbits"]
        self.lz4_0padding = sbi["lz4_0padding"]
        self.isize = inode["size"]
        self.layout = inode["layout"]

        hoff = (inode["data_off"] + 7) & ~7
        f.seek(hoff); h = f.read(8)
        self.h_advise = struct.unpack_from("<H", h, 4)[0]
        self.h_algorithmtype = h[6]
        self.h_clusterbits = h[7]

        if self.layout not in (LAYOUT_COMPRESSED_FULL, LAYOUT_COMPRESSED_COMPACT):
            raise NotImplementedError(f"layout terkompresi {self.layout}")
        for bit, nm in ((ADVISE_BIG_PCLUSTER_1, "big_pcluster"),
                        (ADVISE_BIG_PCLUSTER_2, "big_pcluster"),
                        (ADVISE_FRAGMENT, "fragment"),
                        (ADVISE_INLINE_PCLUSTER, "inline_pcluster"),
                        (ADVISE_INTERLACED, "interlaced")):
            if self.h_advise & bit:
                raise NotImplementedError(nm)

        # Pada COMPRESSED_FULL, bit advise 0 artinya extent metadata (bukan
        # COMPACTED_2B); format petanya berbeda dari lcluster index biasa.
        if self.layout == LAYOUT_COMPRESSED_FULL and (self.h_advise & ADVISE_COMPACTED_2B):
            raise NotImplementedError("full-index extent metadata")

        self.lclusterbits = self.blkszbits + (self.h_clusterbits & 0x0F)
        if self.lclusterbits != self.blkszbits:
            raise NotImplementedError("lcluster != block")

        self.ebase = hoff + 8
        self.full_index_base = hoff + 16
        self.totalidx = (self.isize + self.bs - 1) // self.bs
        # Memo per lcn — lihat catatan panjang di _load_lcluster().
        self._lcache = {}
        # Memo extent terdekompresi — lihat _extent_at().
        self._ecache = {}

    # port: z_erofs_load_compact_lcluster (non big_pcluster)
    def _load_lcluster(self, lcn, lookahead):

        cached = self._lcache.get(lcn)
        if cached is not None:
            return cached
        rec = self._load_lcluster_uncached(lcn)
        self._lcache[lcn] = rec
        return rec

    def _load_lcluster_uncached(self, lcn):


        if lcn < 0 or lcn >= self.totalidx:
            raise ValueError(f"lcluster di luar file: {lcn}")
        if self.layout == LAYOUT_COMPRESSED_FULL:
            return self._load_full_lcluster(lcn)

        bs = self.bs
        lclusterbits = self.lclusterbits
        ebase = self.ebase
        totalidx = self.totalidx
        rec = _Rec(); rec.lcn = lcn

        compacted_4b_initial = ((32 - ebase % 32) // 4) & 7
        compacted_2b = 0
        if (self.h_advise & ADVISE_COMPACTED_2B) and compacted_4b_initial < totalidx:
            compacted_2b = (totalidx - compacted_4b_initial) & ~15

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
            raise NotImplementedError("vcnt config")

        unit = vcnt << amortizedshift
        lobits = max(lclusterbits, _ilog2(LI_D0_CBLKCNT) + 1)
        encodebits = (unit - 4) * 8 // vcnt
        bytes_off = pos & (unit - 1)

        base = pos - bytes_off
        self.f.seek(base)
        blob = self.f.read(unit + 4)
        i = bytes_off >> amortizedshift

        lo, typ = _decode_compactedbits(lobits, blob, encodebits * i)
        rec.type = typ
        if typ == NONHEAD:
            rec.clusterofs = 1 << lclusterbits
            rec.delta1 = self._la_distance(lobits, encodebits, vcnt, blob, i)
            if lo & LI_D0_CBLKCNT:
                raise NotImplementedError("big_pcluster cblkcnt")
            elif i + 1 != vcnt:
                rec.delta0 = lo
            else:
                lo2, t2 = _decode_compactedbits(lobits, blob, encodebits * (i - 1))
                if t2 != NONHEAD:
                    lo2 = 0
                elif lo2 & LI_D0_CBLKCNT:
                    lo2 = 1
                rec.delta0 = lo2 + 1
            return rec

        # HEAD / PLAIN
        rec.clusterofs = lo
        nblk = 1
        j = i
        while j > 0:
            j -= 1
            lo_j, t_j = _decode_compactedbits(lobits, blob, encodebits * j)
            if t_j == NONHEAD:
                j -= lo_j
            if j >= 0:
                nblk += 1
        blkaddr = _le32(blob, unit - 4)
        rec.pblk = blkaddr + nblk
        return rec

    def _load_full_lcluster(self, lcn):

        pos = self.full_index_base + lcn * 8
        self.f.seek(pos)
        raw = self.f.read(8)
        if len(raw) != 8:
            raise ValueError("full lcluster index terpotong")
        advise, clusterofs, lo, hi = struct.unpack("<HHHH", raw)
        rec = _Rec()
        rec.lcn = lcn
        rec.type = advise & 0x3
        if rec.type == NONHEAD:
            rec.clusterofs = 1 << self.lclusterbits
            rec.delta0 = lo
            rec.delta1 = hi
            if rec.delta0 & LI_D0_CBLKCNT:
                raise NotImplementedError("big_pcluster cblkcnt")
            return rec
        if rec.type >= LCLUSTER_TYPE_MAX:
            raise ValueError(f"tipe lcluster tidak valid: {rec.type}")
        if clusterofs >= 1 << self.lclusterbits:
            raise ValueError("cluster offset tidak valid")
        rec.clusterofs = clusterofs
        rec.pblk = lo | (hi << 16)
        return rec

    def _la_distance(self, lobits, encodebits, vcnt, blob, i):
        d1 = 0
        lo = 0
        while i < vcnt:
            lo, typ = _decode_compactedbits(lobits, blob, encodebits * i)
            if typ != NONHEAD:
                return d1
            d1 += 1
            i += 1
        if not (lo & LI_D0_CBLKCNT):
            d1 += lo - 1
        return d1

    # lookback: cari HEAD induk dari NONHEAD
    def _lookback(self, rec, lookback_distance):
        lclusterbits = self.lclusterbits
        lcn = rec.lcn
        while lcn >= lookback_distance:
            lcn = lcn - lookback_distance
            r = self._load_lcluster(lcn, False)
            if r.type == NONHEAD:
                lookback_distance = r.delta0
                if lookback_distance == 0:
                    break
                continue
            else:
                # found HEAD
                return r, (lcn << lclusterbits) | r.clusterofs
        raise ValueError("bogus lookback")

    # m_llen: scan ke depan sampai HEAD berikutnya
    def _decompressedlen(self, head_rec, m_la):
        lclusterbits = self.lclusterbits
        headlcn = m_la >> lclusterbits
        lcn = head_rec.lcn
        clusterofs = head_rec.clusterofs
        while True:
            if (lcn << lclusterbits) >= self.isize:
                return self.isize - m_la
            r = self._load_lcluster(lcn, True)
            clusterofs = r.clusterofs
            if r.type == NONHEAD:
                if r.delta1 == 0:
                    r.delta1 = 1
            elif r.type < LCLUSTER_TYPE_MAX:
                if lcn != headlcn:
                    break
                r.delta1 = 1
            lcn += r.delta1
        return (lcn << lclusterbits) + clusterofs - m_la

    # map satu offset logical -> (m_la, pblk, headtype, m_llen)
    def _map(self, ofs):
        lclusterbits = self.lclusterbits
        endoff = ofs & ((1 << lclusterbits) - 1)
        lcn = ofs >> lclusterbits

        rec = self._load_lcluster(lcn, True)
        if rec.type in (HEAD1, HEAD2, PLAIN):
            if endoff >= rec.clusterofs:
                headtype = rec.type
                m_la = (rec.lcn << lclusterbits) | rec.clusterofs
                head_rec = rec
            else:
                # endoff < clusterofs -> lcn harus >=1, lookback dari lcn-1
                if lcn < 1:
                    headtype = rec.type
                    m_la = (rec.lcn << lclusterbits) | rec.clusterofs
                    head_rec = rec
                else:
                    head_rec, m_la = self._lookback_from(lcn - 1)
                    headtype = head_rec.type
        elif rec.type == NONHEAD:
            head_rec, m_la = self._lookback(rec, rec.delta0)
            headtype = head_rec.type
        else:
            raise ValueError(f"type {rec.type}")

        pblk = head_rec.pblk
        m_llen = self._decompressedlen(head_rec, m_la)
        return m_la, pblk, headtype, m_llen

    def _lookback_from(self, lcn):
        # bikin rec semu utk lookback mulai dari lcn dgn distance 1
        r = self._load_lcluster(lcn, False)
        lclusterbits = self.lclusterbits
        if r.type != NONHEAD:
            return r, (lcn << lclusterbits) | r.clusterofs
        return self._lookback(r, r.delta0)

    # decompress 1 pcluster lz4
    def _lz4(self, src, out_len):
        if _lz4block is None:
            raise RuntimeError("module lz4 belum terpasang")
        data = src
        if self.lz4_0padding:
            k = 0
            while k < len(data) and data[k] == 0:
                k += 1
            data = data[k:]
        return _lz4block.decompress(data, uncompressed_size=out_len)

    @staticmethod
    def _trim_input_padding(src):

        pos = 0
        while pos < len(src) and src[pos] == 0:
            pos += 1
        if pos == len(src):
            raise ValueError("pcluster terkompresi hanya berisi padding nol")
        return src[pos:]

    def _decompress(self, algorithm, src, out_len):
        if algorithm == ALG_LZ4:
            return self._lz4(src, out_len)

        data = self._trim_input_padding(src)
        if algorithm == ALG_DEFLATE:
            try:
                out = zlib.decompress(data)
            except zlib.error as ex:
                raise ValueError(f"deflate EROFS rusak/tidak didukung: {ex}") from ex
        elif algorithm == ALG_ZSTD:
            if _zstandard is None:
                raise RuntimeError("module zstandard belum terpasang")
            try:
                out = _zstandard.ZstdDecompressor().decompress(
                    data, max_output_size=out_len)
            except Exception as ex:
                raise ValueError(f"zstd EROFS rusak/tidak didukung: {ex}") from ex
        elif algorithm == ALG_LZMA:
            # EROFS memakai MicroLZMA, bukan .xz/.lzma standar Python.
            raise NotImplementedError("LZMA EROFS (MicroLZMA) belum tersedia di runtime")
        else:
            raise NotImplementedError(f"algoritma kompresi EROFS {algorithm}")
        if len(out) != out_len:
            raise ValueError(f"hasil dekompresi {len(out)}B, seharusnya {out_len}B")
        return out

    def _extent_at(self, ofs):


        for m_la, data in self._ecache.values():
            if m_la <= ofs < m_la + len(data):
                return m_la, data

        bs = self.bs
        m_la, pblk, headtype, m_llen = self._map(ofs)
        if m_llen <= 0:
            return m_la, b""
        self.f.seek(pblk * bs)
        if headtype == PLAIN:
            data = self.f.read(m_llen)
        else:
            algorithm = ((self.h_algorithmtype >> 4) & 0x0F
                         if headtype == HEAD2 else self.h_algorithmtype & 0x0F)
            data = self._decompress(algorithm, self.f.read(bs), m_llen)

        # Cache dibatasi: file besar punya ribuan extent, dan pemakaian di
        # sini selalu terlokalisasi, jadi menyimpan semuanya cuma memakan RAM.
        if len(self._ecache) < 64:
            self._ecache[m_la] = (m_la, data)
        return m_la, data

    def read_range(self, start, length):

        if start < 0:
            start = 0
        end = min(start + max(0, length), self.isize)
        if start >= end:
            return b""

        out = bytearray()
        ofs = start
        while ofs < end:
            m_la, data = self._extent_at(ofs)
            if not data:
                break
            rel = ofs - m_la
            if rel < 0:                      # peta meleset; hindari loop tak henti
                break
            take = data[rel:rel + (end - ofs)]
            if not take:
                break
            out += take
            ofs += len(take)
        return bytes(out)

    def read(self):
        out = bytearray(self.isize)
        ofs = 0
        while ofs < self.isize:
            m_la, data = self._extent_at(ofs)
            if not data:
                break
            out[m_la:m_la + len(data)] = data
            ofs = m_la + len(data)
        return bytes(out)


class ZInodeFile(io.RawIOBase):


    def __init__(self, zinode):
        self._z = zinode
        self._pos = 0

    # ── sifat ──
    def readable(self):
        return True

    def seekable(self):
        return True

    def writable(self):
        return False

    @property
    def size(self):
        return self._z.isize

    # ── posisi ──
    def tell(self):
        return self._pos

    def seek(self, pos, whence=0):
        if whence == 0:
            new = pos
        elif whence == 1:
            new = self._pos + pos
        elif whence == 2:
            new = self._z.isize + pos
        else:
            raise ValueError(f"whence {whence}")
        self._pos = max(0, new)
        return self._pos

    # ── baca ──
    def read(self, n=-1):
        if n is None or n < 0:
            n = self._z.isize - self._pos
        data = self._z.read_range(self._pos, n)
        self._pos += len(data)
        return data

    def readall(self):
        return self.read(-1)

    def readinto(self, b):
        data = self.read(len(b))
        b[:len(data)] = data
        return len(data)
