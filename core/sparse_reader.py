#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0


import os
import struct
import bisect

SPARSE_MAGIC = 0xED26FF3A

CHUNK_RAW       = 0xCAC1
CHUNK_FILL      = 0xCAC2
CHUNK_DONT_CARE = 0xCAC3
CHUNK_CRC32     = 0xCAC4

# Buffered I/O size for simg2img & read()
_IO_BUF_SIZE = 4 * 1024 * 1024  # 4 MiB — sweet spot for streaming


def is_sparse(path):
    try:
        with open(path, "rb") as f:
            return struct.unpack("<I", f.read(4))[0] == SPARSE_MAGIC
    except Exception:
        return False


class SparseReader:


    def __init__(self, path):
        self.path = path
        self.f = open(path, "rb")
        self._build_map()
        self.pos = 0

    # map
    def _build_map(self):
        f = self.f
        f.seek(0)
        hdr = f.read(28)
        magic = struct.unpack_from("<I", hdr, 0)[0]
        if magic != SPARSE_MAGIC:
            raise ValueError("bukan sparse image")
        self.major      = struct.unpack_from("<H", hdr, 4)[0]
        file_hdr_sz     = struct.unpack_from("<H", hdr, 8)[0]
        self.chunk_hdr_sz = struct.unpack_from("<H", hdr, 10)[0]
        self.block_size = struct.unpack_from("<I", hdr, 12)[0]
        self.total_blocks = struct.unpack_from("<I", hdr, 16)[0]
        total_chunks    = struct.unpack_from("<I", hdr, 20)[0]


        self._starts = []   # out_start tiap chunk (buat bisect)
        self._chunks = []
        out = 0
        f.seek(file_hdr_sz)
        for _ in range(total_chunks):
            ch = f.read(self.chunk_hdr_sz)
            if len(ch) < 12:
                break
            ctype = struct.unpack_from("<H", ch, 0)[0]
            nblk  = struct.unpack_from("<I", ch, 4)[0]
            tsz   = struct.unpack_from("<I", ch, 8)[0]
            body  = tsz - self.chunk_hdr_sz
            length = nblk * self.block_size

            if ctype == CHUNK_RAW:
                src = f.tell()
                self._starts.append(out)
                self._chunks.append((out, length, "raw", src))
                f.seek(body, 1)
                out += length
            elif ctype == CHUNK_FILL:
                fill = f.read(4)
                self._starts.append(out)
                self._chunks.append((out, length, "fill", fill))
                out += length
            elif ctype == CHUNK_DONT_CARE:
                self._starts.append(out)
                self._chunks.append((out, length, "zero", None))
                out += length
            elif ctype == CHUNK_CRC32:
                f.seek(body, 1)   # skip, tidak menambah output
            else:
                # tipe tak dikenal: skip body
                f.seek(body, 1)

        self.size = out  # total ukuran image asli (unsparsed)

    # file API
    def seek(self, offset, whence=0):
        if whence == 0:
            self.pos = offset
        elif whence == 1:
            self.pos += offset
        elif whence == 2:
            self.pos = self.size + offset
        return self.pos

    def tell(self):
        return self.pos

    def read(self, size=-1):
        if size is None or size < 0:
            size = self.size - self.pos
        end = min(self.pos + size, self.size)
        if end <= self.pos:
            return b""

        # Pre-allocate result buffer to avoid repeated bytearray concat
        total = end - self.pos
        result = bytearray(total)
        result_offset = 0
        pos = self.pos

        # cari chunk awal via bisect (starts terurut)
        idx = bisect.bisect_right(self._starts, pos) - 1
        if idx < 0:
            idx = 0

        while pos < end and idx < len(self._chunks):
            c_start, c_len, kind, payload = self._chunks[idx]
            c_end = c_start + c_len
            if pos >= c_end:
                idx += 1
                continue
            if pos < c_start:
                # gap (harusnya tak terjadi krn DONT_CARE eksplisit) -> zero
                take = min(c_start, end) - pos
                # result[result_offset:result_offset+take] already zero
                result_offset += take
                pos += take
                continue

            take = min(c_end, end) - pos
            rel = pos - c_start
            if kind == "raw":
                self.f.seek(payload + rel)
                # Read directly into result buffer in chunks
                bytes_left = take
                while bytes_left > 0:
                    chunk = self.f.read(min(bytes_left, _IO_BUF_SIZE))
                    if not chunk:
                        break
                    n = len(chunk)
                    result[result_offset:result_offset + n] = chunk
                    result_offset += n
                    bytes_left -= n
            elif kind == "fill":
                # ulangi 4-byte fill, align ke posisi
                fill = payload if payload else b"\x00\x00\x00\x00"
                if fill == b"\x00\x00\x00\x00":
                    # Already zero-initialized, just advance
                    result_offset += take
                else:
                    # Build fill pattern for this segment
                    phase = rel % 4
                    # Create a repeating fill buffer (up to _IO_BUF_SIZE)
                    rep_count = min(take + phase + 3, _IO_BUF_SIZE) // 4 + 1
                    fill_buf = fill * rep_count
                    written = 0
                    while written < take:
                        seg = min(take - written, len(fill_buf) - phase)
                        result[result_offset:result_offset + seg] = \
                            fill_buf[phase:phase + seg]
                        result_offset += seg
                        written += seg
                        phase = 0  # only first segment has phase offset
            else:  # zero / DONT_CARE
                # Already zero-initialized, skip
                result_offset += take
            pos += take
            idx += 1

        self.pos = end
        return bytes(result)

    def close(self):
        try:
            self.f.close()
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


def simg2img(src_path: str, dst_path: str):

    if not os.path.isfile(src_path):
        return False, f"Source not found: {src_path}"

    src_path = os.path.abspath(src_path)
    dst_path = os.path.abspath(dst_path)
    if src_path == dst_path:
        return False, "src_path and dst_path must differ"

    try:
        src_f = open(src_path, "rb")
    except Exception as e:
        return False, f"open src failed: {e}"

    try:
        # Parse sparse header
        hdr = src_f.read(28)
        if len(hdr) < 28:
            src_f.close()
            return False, "File too small for sparse header"

        magic = struct.unpack_from("<I", hdr, 0)[0]
        if magic != SPARSE_MAGIC:
            src_f.close()
            return False, f"Not a sparse image (magic=0x{magic:08X})"

        major       = struct.unpack_from("<H", hdr, 4)[0]
        file_hdr_sz = struct.unpack_from("<H", hdr, 8)[0]
        chunk_hdr_sz = struct.unpack_from("<H", hdr, 10)[0]
        block_size  = struct.unpack_from("<I", hdr, 12)[0]
        total_blocks = struct.unpack_from("<I", hdr, 16)[0]
        total_chunks = struct.unpack_from("<I", hdr, 20)[0]

        total_size = total_blocks * block_size
        print(f"[SIMG2IMG] src={src_path} blocks={total_blocks} "
              f"blk_sz={block_size} raw_size={total_size}")

        # Open output
        try:
            dst_f = open(dst_path, "wb")
        except Exception as e:
            src_f.close()
            return False, f"open dst failed: {e}"

        src_f.seek(file_hdr_sz)
        blocks_written = 0

        # Pre-build zero buffer for DONT_CARE/zero-fill (reuse, no alloc)
        zero_buf = b"\x00" * min(block_size * 256, _IO_BUF_SIZE)

        for _ in range(total_chunks):
            ch = src_f.read(chunk_hdr_sz)
            if len(ch) < 12:
                break

            ctype    = struct.unpack_from("<H", ch, 0)[0]
            nblk     = struct.unpack_from("<I", ch, 4)[0]
            total_sz = struct.unpack_from("<I", ch, 8)[0]
            body_sz  = total_sz - chunk_hdr_sz

            if ctype == CHUNK_RAW:
                # Stream raw data in buffered chunks
                remaining = nblk * block_size
                while remaining > 0:
                    to_read = min(remaining, _IO_BUF_SIZE)
                    data = src_f.read(to_read)
                    if not data:
                        break
                    dst_f.write(data)
                    remaining -= len(data)
                blocks_written += nblk

            elif ctype == CHUNK_FILL:
                fill = src_f.read(4)
                total_bytes = nblk * block_size
                if fill == b"\x00\x00\x00\x00":
                    # Write zeros in large buffers
                    remaining = total_bytes
                    while remaining > 0:
                        w = min(remaining, len(zero_buf))
                        dst_f.write(zero_buf[:w])
                        remaining -= w
                else:
                    # Build fill buffer, write in large segments
                    # Fill buffer = pattern repeated to _IO_BUF_SIZE
                    reps = _IO_BUF_SIZE // 4
                    fill_buf = fill * reps
                    remaining = total_bytes
                    while remaining > 0:
                        w = min(remaining, len(fill_buf))
                        dst_f.write(fill_buf[:w])
                        remaining -= w
                blocks_written += nblk

            elif ctype == CHUNK_DONT_CARE:
                # Write zeros for DONT_CARE (needed for correct raw output)
                total_bytes = nblk * block_size
                remaining = total_bytes
                while remaining > 0:
                    w = min(remaining, len(zero_buf))
                    dst_f.write(zero_buf[:w])
                    remaining -= w
                blocks_written += nblk

            elif ctype == CHUNK_CRC32:
                # Skip CRC chunk body
                src_f.seek(body_sz, 1)

            else:
                # Unknown chunk type — skip
                src_f.seek(body_sz, 1)

        dst_f.close()
        src_f.close()

        if blocks_written != total_blocks:
            return False, (
                f"blocks_written({blocks_written}) != "
                f"total_blocks({total_blocks})"
            )

        print(f"[SIMG2IMG] OK blocks={blocks_written} -> {dst_path}")
        return True, dst_path

    except Exception as e:
        try:
            src_f.close()
        except Exception:
            pass
        try:
            dst_f.close()
            os.remove(dst_path)
        except Exception:
            pass
        return False, f"decode error: {e}"
