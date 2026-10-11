# SPDX-License-Identifier: MPL-2.0
from __future__ import annotations

import hashlib

print("[CORE STAGE] VDEV HASHTREE")

# Baca dalam potongan besar; hashlib itu C, yang mahal justru overhead
# per-panggilan read() kalau dilakukan per 4 KB.
_CHUNK_BLOCKS = 256


def compute_root(path, image_size, salt, data_block_size, hash_block_size,
                 algorithm="sha256", progress=None):

    if not image_size or not data_block_size or not hash_block_size:
        return None
    try:
        hashlib.new(algorithm)
    except ValueError:
        return None                      # algoritma tidak dikenal, jangan menebak

    digest_size = hashlib.new(algorithm).digest_size

    try:
        with open(path, "rb") as f:
            level = bytearray()
            remaining = image_size
            done = 0
            chunk_size = data_block_size * _CHUNK_BLOCKS
            while remaining > 0:
                want = min(chunk_size, remaining)
                buf = f.read(want)
                if not buf:
                    break
                for off in range(0, len(buf), data_block_size):
                    blk = buf[off:off + data_block_size]
                    if len(blk) < data_block_size:
                        blk = blk + b"\x00" * (data_block_size - len(blk))
                    h = hashlib.new(algorithm)
                    h.update(salt)
                    h.update(blk)
                    level += h.digest()
                remaining -= len(buf)
                done += len(buf)
                if progress:
                    try:
                        progress(done, image_size)
                    except Exception:    # noqa: BLE001 — progress tak boleh fatal
                        pass
    except OSError:
        return None

    if not level:
        return None

    # Naik level sampai tersisa satu digest.
    while len(level) > digest_size:
        pad = (-len(level)) % hash_block_size
        if pad:
            level += b"\x00" * pad
        nxt = bytearray()
        for i in range(0, len(level), hash_block_size):
            h = hashlib.new(algorithm)
            h.update(salt)
            h.update(level[i:i + hash_block_size])
            nxt += h.digest()
        level = nxt

    return bytes(level[:digest_size])


def verify(path, descriptor, progress=None):

    root = compute_root(
        path,
        descriptor.image_size,
        descriptor.salt,
        descriptor.data_block_size,
        descriptor.hash_block_size,
        descriptor.hash_algorithm or "sha256",
        progress=progress,
    )
    if root is None:
        return None, None
    return root == descriptor.digest, root
