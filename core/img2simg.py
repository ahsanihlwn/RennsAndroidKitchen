# SPDX-License-Identifier: MPL-2.0
import os
import struct

print("[CORE STAGE] IMG2SIMG")

# Try importing numpy for vectorized classification
try:
    import numpy as np
    _HAS_NUMPY = True
except ImportError:
    _HAS_NUMPY = False

DEFAULT_BLOCK_SIZE = 4096


BLOCKS_PER_BUFFER = 4096

SPARSE_MAGIC       = 0xED26FF3A
MAJOR_VERSION      = 1
MINOR_VERSION      = 0
FILE_HEADER_SIZE   = 28
CHUNK_HEADER_SIZE  = 12

CHUNK_TYPE_RAW       = 0xCAC1
CHUNK_TYPE_FILL      = 0xCAC2
CHUNK_TYPE_DONT_CARE = 0xCAC3


def _log(msg):
    print(f"[IMG2SIMG] {msg}")


def _classify_block(block_bytes):

    n = len(block_bytes)
    if n < 4 or (n & 3):
        return ('raw', None)
    pattern = block_bytes[:4]
    # Fast path: compare to pattern repeated. memoryview-aware.
    if block_bytes == pattern * (n // 4):
        return ('fill', bytes(pattern))
    return ('raw', None)


def img2simg(src_path: str, dst_path: str, blk_sz: int = DEFAULT_BLOCK_SIZE):

    if blk_sz <= 0 or (blk_sz & 3):
        return False, f"blk_sz must be positive multiple of 4 (got {blk_sz})"

    if not os.path.isfile(src_path):
        return False, f"Source not found: {src_path}"

    src_path = os.path.abspath(src_path)
    dst_path = os.path.abspath(dst_path)
    if src_path == dst_path:
        return False, "src_path and dst_path must differ"

    src_size = os.path.getsize(src_path)
    if src_size == 0:
        return False, "Source file is empty"

    total_blks = (src_size + blk_sz - 1) // blk_sz  # ceil-div, pad last

    use_numpy = _HAS_NUMPY and (blk_sz & 3) == 0
    backend = "numpy" if use_numpy else "pure-python"
    _log(f"src={src_path} size={src_size} blk_sz={blk_sz} "
         f"total_blks={total_blks} backend={backend}")

    try:
        src_f = open(src_path, "rb")
    except Exception as e:
        return False, f"open src failed: {e}"

    try:
        dst_f = open(dst_path, "wb")
    except Exception as e:
        src_f.close()
        return False, f"open dst failed: {e}"

    chunks_written = 0
    blocks_emitted = 0

    def flush_chunk(kind, fill_pattern, count, raw_buf):
        nonlocal chunks_written, blocks_emitted
        if kind is None or count == 0:
            return
        if kind == 'fill':
            total_sz = CHUNK_HEADER_SIZE + 4
            dst_f.write(struct.pack(
                "<HHII", CHUNK_TYPE_FILL, 0, count, total_sz
            ))
            dst_f.write(fill_pattern)
        elif kind == 'raw':
            payload_sz = count * blk_sz
            total_sz = CHUNK_HEADER_SIZE + payload_sz
            dst_f.write(struct.pack(
                "<HHII", CHUNK_TYPE_RAW, 0, count, total_sz
            ))
            dst_f.write(raw_buf)
        else:
            raise AssertionError(f"unknown chunk kind: {kind}")
        chunks_written += 1
        blocks_emitted += count

    try:
        # Reserve file header space; rewrite at end with real chunk count.
        dst_f.write(b"\x00" * FILE_HEADER_SIZE)

        current_kind = None
        current_fill = None
        current_count = 0
        raw_buf = bytearray()

        blocks_remaining = total_blks

        if use_numpy:


            elements_per_blk = blk_sz // 4  # uint32 elements per block

            while blocks_remaining > 0:
                n_read = min(BLOCKS_PER_BUFFER, blocks_remaining)
                buf_bytes = src_f.read(n_read * blk_sz)

                # Handle partial last read (pad to full block boundary)
                actual_bytes = len(buf_bytes)
                expected_bytes = n_read * blk_sz
                if actual_bytes < expected_bytes:
                    buf_bytes += b"\x00" * (expected_bytes - actual_bytes)

                blocks_remaining -= n_read

                # Reshape into 2D uint32 array: (n_read, elements_per_blk)
                arr = np.frombuffer(buf_bytes, dtype=np.uint32).reshape(
                    n_read, elements_per_blk
                )


                first_col = arr[:, :1]
                is_fill = (arr == first_col).all(axis=1)  # bool array

                # Extract fill patterns for fill blocks
                fill_patterns = arr[:, 0]  # first uint32 of each block

                # ── Merge consecutive same-type blocks into chunks ──
                for bi in range(n_read):
                    if is_fill[bi]:
                        kind = 'fill'
                        fill_val = fill_patterns[bi].tobytes()
                    else:
                        kind = 'raw'
                        fill_val = None

                    blk_start = bi * blk_sz
                    blk_data = buf_bytes[blk_start: blk_start + blk_sz]

                    if current_kind is None:
                        current_kind = kind
                        current_fill = fill_val
                        current_count = 1
                        if kind == 'raw':
                            raw_buf = bytearray(blk_data)
                    elif (kind == current_kind and
                          (kind != 'fill' or fill_val == current_fill)):
                        current_count += 1
                        if kind == 'raw':
                            raw_buf.extend(blk_data)
                    else:
                        flush_chunk(current_kind, current_fill,
                                    current_count, raw_buf)
                        current_kind = kind
                        current_fill = fill_val
                        current_count = 1
                        raw_buf = (bytearray(blk_data) if kind == 'raw'
                                   else bytearray())

        else:
            # Pure Python fallback path
            while blocks_remaining > 0:
                data = src_f.read(blk_sz)
                if len(data) < blk_sz:
                    data = data + b"\x00" * (blk_sz - len(data))
                blocks_remaining -= 1

                kind, fill = _classify_block(data)

                if current_kind is None:
                    current_kind = kind
                    current_fill = fill
                    current_count = 1
                    if kind == 'raw':
                        raw_buf.extend(data)
                elif (kind == current_kind and
                      (kind != 'fill' or fill == current_fill)):
                    current_count += 1
                    if kind == 'raw':
                        raw_buf.extend(data)
                else:
                    flush_chunk(current_kind, current_fill,
                                current_count, raw_buf)
                    current_kind = kind
                    current_fill = fill
                    current_count = 1
                    raw_buf = (bytearray(data) if kind == 'raw'
                               else bytearray())

        # Tail flush
        flush_chunk(current_kind, current_fill, current_count, raw_buf)

        # Rewrite real file header
        dst_f.seek(0)
        dst_f.write(struct.pack(
            "<IHHHHIIII",
            SPARSE_MAGIC,
            MAJOR_VERSION,
            MINOR_VERSION,
            FILE_HEADER_SIZE,
            CHUNK_HEADER_SIZE,
            blk_sz,
            total_blks,
            chunks_written,
            0,  # image checksum (unused; libsparse decoders ignore)
        ))
    except Exception as e:
        try:
            dst_f.close()
            os.remove(dst_path)
        except Exception:
            pass
        src_f.close()
        return False, f"encode error: {e}"
    finally:
        try:
            src_f.close()
        except Exception:
            pass
        try:
            dst_f.close()
        except Exception:
            pass

    if blocks_emitted != total_blks:
        return False, (
            f"internal: blocks_emitted({blocks_emitted}) != total_blks({total_blks})"
        )

    _log(f"OK chunks={chunks_written} blocks={blocks_emitted} -> {dst_path}")
    return True, dst_path


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Encode raw image → Android sparse image (v1.0)")
    parser.add_argument("src", help="input raw image (.img)")
    parser.add_argument("dst", help="output sparse image")
    parser.add_argument("-b", "--blk-sz", type=int, default=DEFAULT_BLOCK_SIZE,
                        help="block size in bytes (default 4096)")
    args = parser.parse_args()
    ok, result = img2simg(args.src, args.dst, args.blk_sz)
    if ok:
        print("OK:", result)
        raise SystemExit(0)
    else:
        print("FAIL:", result)
        raise SystemExit(1)
