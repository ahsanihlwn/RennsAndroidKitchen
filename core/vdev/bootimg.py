# SPDX-License-Identifier: MPL-2.0
from __future__ import annotations

import bz2
import gzip
import io
import lzma
import struct

print("[CORE STAGE] VDEV BOOTIMG")

BOOT_MAGIC = b"ANDROID!"
VENDOR_BOOT_MAGIC = b"VNDRBOOT"
CPIO_MAGIC = b"070701"

_MAX_RAMDISK = 256 << 20        # 256 MB; ramdisk nyata jauh di bawah ini


_LZ4_LEGACY_MAGIC = b"\x02\x21\x4c\x18"
_LZ4_LEGACY_BLOCK = 8 << 20      # blok legacy maksimum 8 MB


def _lz4_legacy(data):

    try:
        import lz4.block
    except Exception:                                  # noqa: BLE001
        return None
    out = bytearray()
    pos, end = 0, len(data)
    while pos + 4 <= end:
        if data[pos:pos + 4] != _LZ4_LEGACY_MAGIC:
            break
        pos += 4
        while pos + 4 <= end:
            if data[pos:pos + 4] == _LZ4_LEGACY_MAGIC:
                break                                  # frame berikutnya
            size = struct.unpack_from("<I", data, pos)[0]
            pos += 4
            if size == 0 or pos + size > end:
                pos = end
                break
            try:
                out += lz4.block.decompress(
                    data[pos:pos + size], uncompressed_size=_LZ4_LEGACY_BLOCK)
            except Exception:                          # noqa: BLE001
                return bytes(out) or None
            pos += size
    return bytes(out) or None


# dekompresi
def _decompress(data):

    if not data:
        return None
    if data[:2] == b"\x1f\x8b":                       # gzip
        try:
            return gzip.decompress(data)
        except OSError:
            # Ramdisk boot sering berisi beberapa anggota gzip beruntun.
            out, stream = b"", io.BytesIO(data)
            while True:
                try:
                    with gzip.GzipFile(fileobj=stream) as g:
                        out += g.read()
                except (OSError, EOFError):
                    break
                if stream.tell() >= len(data):
                    break
            return out or None
    if data[:4] == b"\x04\x22\x4d\x18":               # lz4 frame modern
        try:
            import lz4.frame
            return lz4.frame.decompress(data)
        except Exception:                              # noqa: BLE001
            return None
    if data[:4] == _LZ4_LEGACY_MAGIC:                 # lz4 legacy (dipakai magiskboot)
        return _lz4_legacy(data)
    if data[:6] == b"\xfd7zXZ\x00" or data[:5] == b"\x5d\x00\x00":   # xz / lzma
        try:
            return lzma.decompress(data)
        except lzma.LZMAError:
            try:
                d = lzma.LZMADecompressor()
                return d.decompress(data)
            except lzma.LZMAError:
                return None
    if data[:3] == b"BZh":                            # bzip2
        try:
            return bz2.decompress(data)
        except OSError:
            return None
    if data[:4] == b"\x28\xb5\x2f\xfd":               # zstd
        try:
            import zstandard
            return zstandard.ZstdDecompressor().decompressobj().decompress(data)
        except Exception:                              # noqa: BLE001
            return None
    if data[:6] == CPIO_MAGIC:                        # sudah mentah
        return data
    return None


# header boot image
def _align(value, page):
    return (value + page - 1) // page * page


def kernel_cmdline(path):

    try:
        with open(path, "rb") as f:
            head = f.read(4096)
    except OSError:
        return ""
    if len(head) < 128:
        return ""

    def _cstr(off, size):
        chunk = head[off:off + size]
        end = chunk.find(b"\x00")
        if end >= 0:
            chunk = chunk[:end]
        return chunk.decode("utf-8", "replace").strip()

    def _u32(off):
        return int.from_bytes(head[off:off + 4], "little")

    if head[:8] == BOOT_MAGIC:
        ver = _u32(40)
        if ver >= 3:
            return _cstr(44, 1536)
        main = _cstr(64, 512)
        extra = _cstr(608, 1024)
        return (main + " " + extra).strip() if extra else main
    if head[:8] == VENDOR_BOOT_MAGIC:
        return _cstr(28, 2048)
    return ""


def read_ramdisk(path):

    try:
        with open(path, "rb") as f:
            head = f.read(4096)
            if head[:8] == BOOT_MAGIC:
                span = _boot_ramdisk_span(head)
            elif head[:8] == VENDOR_BOOT_MAGIC:
                span = _vendor_ramdisk_span(head)
            else:
                return None
            if span is None:
                return None
            offset, size = span
            if size <= 0 or size > _MAX_RAMDISK:
                return None
            f.seek(offset)
            raw = f.read(size)
    except (OSError, struct.error):
        return None
    return _decompress(raw)


def _boot_ramdisk_span(head):

    try:
        version = struct.unpack_from("<I", head, 40)[0]
        if version >= 3:
            # v3/v4: page selalu 4096, urutan kernel lalu ramdisk.
            kernel_size = struct.unpack_from("<I", head, 8)[0]
            ramdisk_size = struct.unpack_from("<I", head, 12)[0]
            page = 4096
            offset = page + _align(kernel_size, page)
            return offset, ramdisk_size
        kernel_size = struct.unpack_from("<I", head, 8)[0]
        ramdisk_size = struct.unpack_from("<I", head, 16)[0]
        page = struct.unpack_from("<I", head, 36)[0] or 2048
        offset = page + _align(kernel_size, page)
        return offset, ramdisk_size
    except struct.error:
        return None


def _vendor_ramdisk_span(head):

    try:
        page = struct.unpack_from("<I", head, 12)[0] or 4096
        ramdisk_size = struct.unpack_from("<I", head, 24)[0]
        header_size = struct.unpack_from("<I", head, 2096)[0] or 2112
        offset = _align(header_size, page)
        return offset, ramdisk_size
    except struct.error:
        return None


# cpio
def cpio_entries(data, want=None, limit=20000):

    if not data or data[:6] != CPIO_MAGIC:
        return
    pos, count = 0, 0
    end = len(data)
    while pos + 110 <= end and count < limit:
        if data[pos:pos + 6] != CPIO_MAGIC:
            break
        try:
            mode = int(data[pos + 14:pos + 22], 16)
            filesize = int(data[pos + 54:pos + 62], 16)
            namesize = int(data[pos + 94:pos + 102], 16)
        except ValueError:
            break
        name_start = pos + 110
        name = data[name_start:name_start + namesize - 1].decode("utf-8", "replace")
        data_start = _align(name_start + namesize, 4)
        if name == "TRAILER!!!":
            break
        if want is None or want(name):
            yield name, mode, data[data_start:data_start + filesize]
        pos = _align(data_start + filesize, 4)
        count += 1


def find_fstab(path):

    data = read_ramdisk(path)
    if not data:
        return None

    def want(name):
        base = name.rsplit("/", 1)[-1]
        return base.startswith("fstab.")

    best = None
    for name, _mode, blob in cpio_entries(data, want=want):
        text = blob.decode("utf-8", "replace")
        # first_stage_ramdisk lebih otoritatif daripada salinan di root.
        if "first_stage_ramdisk" in name:
            return name, text
        if best is None:
            best = (name, text)
    return best
