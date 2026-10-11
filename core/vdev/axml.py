# SPDX-License-Identifier: MPL-2.0
from __future__ import annotations

import struct

print("[CORE STAGE] VDEV AXML")

# Tipe chunk
_RES_STRING_POOL = 0x0001
_RES_XML_RESOURCE_MAP = 0x0180
_RES_XML_START_ELEMENT = 0x0102

_UTF8_FLAG = 1 << 8

# Tipe nilai atribut (Res_value.dataType)
_TYPE_STRING = 0x03
_TYPE_INT_BOOL = 0x12

# Resource ID atribut framework yang kita butuhkan, untuk memulihkan nama
# atribut pada manifest yang nama-nya sudah dibuang aapt2.
_ATTR_BY_RES_ID = {
    0x01010003: "name",
    0x01010009: "protectionLevel",
    0x0101000E: "sharedUserId",
    0x01010021: "targetPackage",
    0x0101028E: "required",
    0x0101021B: "versionCode",
    0x0101021C: "versionName",
    0x01010572: "targetName",
    0x010103A2: "isStatic",
    0x010102BE: "process",
}


class StringPool:


    __slots__ = ("_buf", "_base", "_offsets", "_utf8", "_cache", "count")

    def __init__(self, buf, chunk_off):
        _type, header_size, _size = struct.unpack_from("<HHI", buf, chunk_off)
        (count, _style_count, flags,
         strings_start, _styles_start) = struct.unpack_from("<IIIII", buf, chunk_off + 8)
        self._buf = buf
        self.count = count
        self._utf8 = bool(flags & _UTF8_FLAG)
        self._base = chunk_off + strings_start
        # Satu panggilan C untuk seluruh tabel offset.
        self._offsets = struct.unpack_from("<%dI" % count, buf, chunk_off + header_size) \
            if count else ()
        self._cache = {}

    def __len__(self):
        return self.count

    def get(self, index):
        if index < 0 or index >= self.count:
            return ""
        hit = self._cache.get(index)
        if hit is not None:
            return hit
        try:
            value = self._decode(self._base + self._offsets[index])
        except (struct.error, IndexError, UnicodeDecodeError):
            value = ""
        self._cache[index] = value
        return value

    def _decode(self, pos):
        buf = self._buf
        if self._utf8:
            # Dua panjang berurutan (jumlah karakter, lalu jumlah byte),
            # masing-masing 1 atau 2 byte tergantung bit tinggi.
            n = buf[pos]
            pos += 2 if n & 0x80 else 1
            n = buf[pos]
            if n & 0x80:
                blen = ((n & 0x7F) << 8) | buf[pos + 1]
                pos += 2
            else:
                blen = n
                pos += 1
            return buf[pos:pos + blen].decode("utf-8", "replace")

        n = struct.unpack_from("<H", buf, pos)[0]
        pos += 2
        if n & 0x8000:
            n = ((n & 0x7FFF) << 16) | struct.unpack_from("<H", buf, pos)[0]
            pos += 2
        return buf[pos:pos + n * 2].decode("utf-16le", "replace")


def parse(data):

    if not data or len(data) < 8:
        return None
    magic, header_size, _total = struct.unpack_from("<HHI", data, 0)
    if magic != 0x0003:
        return None

    pool = None
    res_map = ()
    out = []
    depth = 0
    pos = header_size
    end = len(data)

    while pos + 8 <= end:
        ctype, chunk_header, csize = struct.unpack_from("<HHI", data, pos)
        if csize < 8 or pos + csize > end:
            break

        if ctype == _RES_STRING_POOL:
            pool = StringPool(data, pos)

        elif ctype == _RES_XML_RESOURCE_MAP:
            n = (csize - chunk_header) // 4
            if n > 0:
                res_map = struct.unpack_from("<%dI" % n, data, pos + chunk_header)

        elif ctype == _RES_XML_START_ELEMENT and pool is not None:
            body = pos + chunk_header
            _ns, name_idx = struct.unpack_from("<iI", data, body)
            attr_start, attr_size, attr_count = struct.unpack_from("<HHH", data, body + 8)
            attrs = {}
            base = body + attr_start
            stride = attr_size or 20
            for i in range(attr_count):
                a = base + i * stride
                if a + 20 > end:
                    break
                _ans, an_idx, raw_idx = struct.unpack_from("<iiI", data, a)
                dtype, dval = struct.unpack_from("<BI", data, a + 15)

                key = pool.get(an_idx) if an_idx >= 0 else ""
                if not key and 0 <= an_idx < len(res_map):
                    key = _ATTR_BY_RES_ID.get(res_map[an_idx], "")
                if not key:
                    continue

                if raw_idx != 0xFFFFFFFF:
                    value = pool.get(raw_idx)
                elif dtype == _TYPE_STRING:
                    value = pool.get(dval)
                elif dtype == _TYPE_INT_BOOL:
                    value = bool(dval)
                else:
                    value = dval
                attrs[key] = value

            out.append((depth, pool.get(name_idx), attrs))
            depth += 1

        elif ctype == 0x0103:            # END_ELEMENT
            depth = max(0, depth - 1)

        pos += csize

    return out
