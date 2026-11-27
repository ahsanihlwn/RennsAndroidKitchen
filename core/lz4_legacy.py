# SPDX-License-Identifier: MPL-2.0
"""Decode the legacy LZ4 blocks used by Android boot images."""
import struct


MAGIC = b"\x02\x21\x4c\x18"
BLOCK_SIZE = 8 << 20


def decompress(data, *, max_output=None):
    try:
        import lz4.block
    except (ImportError, OSError):
        return None

    out = bytearray()
    pos, end = 0, len(data)
    while pos + 4 <= end and data[pos:pos + 4] == MAGIC:
        pos += 4
        while pos + 4 <= end and data[pos:pos + 4] != MAGIC:
            size, = struct.unpack_from("<I", data, pos)
            pos += 4
            if not size or pos + size > end:
                return bytes(out) or None
            try:
                block = lz4.block.decompress(data[pos:pos + size], uncompressed_size=BLOCK_SIZE)
            except (ValueError, lz4.block.LZ4BlockError):
                return bytes(out) or None
            if max_output is not None and len(out) + len(block) > max_output:
                return None
            out.extend(block)
            pos += size
    return bytes(out) or None
