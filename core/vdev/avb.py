# SPDX-License-Identifier: MPL-2.0
from __future__ import annotations

import os
import struct

print("[CORE STAGE] VDEV AVB")

MAGIC = b"AVB0"
HEADER_SIZE = 256

# Nilai flag sesuai libavb.
FLAG_HASHTREE_DISABLED = 1        # dm-verity dimatikan
FLAG_VERIFICATION_DISABLED = 2    # verifikasi rantai vbmeta dimatikan


class VbMeta:
    def __init__(self, path, flags, release, algorithm):
        self.path = path
        self.flags = flags
        self.release = release
        self.algorithm = algorithm

    @property
    def hashtree_disabled(self):
        return bool(self.flags & FLAG_HASHTREE_DISABLED)

    @property
    def verification_disabled(self):
        return bool(self.flags & FLAG_VERIFICATION_DISABLED)

    @property
    def patched(self):

        return self.hashtree_disabled or self.verification_disabled

    def describe(self):
        if not self.flags:
            return "flags=0 (verity AKTIF, stock)"
        bits = []
        if self.hashtree_disabled:
            bits.append("hashtree-disabled")
        if self.verification_disabled:
            bits.append("verification-disabled")
        return f"flags={self.flags} ({', '.join(bits)})"


def read_vbmeta(path):

    try:
        with open(path, "rb") as f:
            head = f.read(HEADER_SIZE)
    except OSError:
        return None
    if len(head) < HEADER_SIZE or head[:4] != MAGIC:
        return None
    try:
        flags = struct.unpack_from(">I", head, 120)[0]
        algorithm = struct.unpack_from(">I", head, 28)[0]
    except struct.error:
        return None
    release = head[128:176].split(b"\x00", 1)[0].decode("ascii", "replace")
    return VbMeta(path, flags, release, algorithm)


def find_vbmeta_images(port_dir, extra_dirs=()):

    out = {}
    roots = [port_dir]
    roots.extend(extra_dirs)
    for root in roots:
        if not root or not os.path.isdir(root):
            continue
        try:
            names = os.listdir(root)
        except OSError:
            continue
        for name in names:
            low = name.lower()
            if not low.startswith("vbmeta") or not low.endswith(".img"):
                continue
            meta = read_vbmeta(os.path.join(root, name))
            if meta is not None:
                out.setdefault(os.path.splitext(name)[0], meta)
    return out
