# SPDX-License-Identifier: MPL-2.0
from __future__ import annotations

import struct

print("[CORE STAGE] VDEV AVBDESC")

MAGIC = b"AVB0"
HEADER_SIZE = 256

# Tag deskriptor (avb_descriptor.h)
TAG_PROPERTY = 0
TAG_HASHTREE = 1
TAG_HASH = 2
TAG_KERNEL_CMDLINE = 3
TAG_CHAIN_PARTITION = 4

_TAG_NAMES = {
    TAG_PROPERTY: "property",
    TAG_HASHTREE: "hashtree",
    TAG_HASH: "hash",
    TAG_KERNEL_CMDLINE: "kernel_cmdline",
    TAG_CHAIN_PARTITION: "chain",
}


class Descriptor:
    __slots__ = ("tag", "kind", "partition", "image_size", "hash_algorithm",
                 "salt", "digest", "data_block_size", "hash_block_size",
                 "tree_offset", "tree_size", "rollback_index_location",
                 "flags")

    def __init__(self, tag):
        self.tag = tag
        self.kind = _TAG_NAMES.get(tag, f"tag{tag}")
        self.partition = ""
        self.image_size = 0
        self.hash_algorithm = ""
        self.salt = b""
        self.digest = b""
        self.data_block_size = 0
        self.hash_block_size = 0
        self.tree_offset = 0
        self.tree_size = 0
        self.rollback_index_location = 0
        self.flags = 0

    @property
    def digest_hex(self):
        return self.digest.hex()

    @property
    def salt_hex(self):
        return self.salt.hex()

    def __repr__(self):
        return f"<{self.kind} {self.partition or '-'}>"


def _cstr(buf):
    return buf.split(b"\x00", 1)[0].decode("utf-8", "replace")


def parse_descriptors(data):

    if not data or len(data) < HEADER_SIZE or data[:4] != MAGIC:
        return []
    try:
        auth_size = struct.unpack_from(">Q", data, 12)[0]
        desc_off = struct.unpack_from(">Q", data, 96)[0]
        desc_size = struct.unpack_from(">Q", data, 104)[0]
    except struct.error:
        return []

    base = HEADER_SIZE + auth_size + desc_off
    end = min(base + desc_size, len(data))
    out = []
    pos = base
    while pos + 16 <= end:
        try:
            tag, nbytes = struct.unpack_from(">QQ", data, pos)
        except struct.error:
            break
        body = pos + 16
        body_end = body + nbytes
        if nbytes <= 0 or body_end > end:
            break
        d = _parse_one(tag, data, body, body_end)
        if d is not None:
            out.append(d)
        # Deskriptor selalu di-align 8 byte.
        pos = body_end + ((8 - (body_end % 8)) % 8)
    return out


def _parse_one(tag, data, body, body_end):
    d = Descriptor(tag)
    try:
        if tag == TAG_HASHTREE:
            # AvbHashtreeDescriptor
            (dm_ver, image_size, tree_offset, tree_size, data_bs, hash_bs,
             _fec_roots, _fec_off, _fec_size) = struct.unpack_from(
                ">IQQQIIIQQ", data, body)
            algo = _cstr(data[body + 56:body + 88])
            name_len, salt_len, digest_len, flags = struct.unpack_from(
                ">IIII", data, body + 88)
            p = body + 104 + 60          # reserved[60] sesudah flags
            # Tata letak: ... flags, reserved[60], lalu name/salt/digest.
            p = body + 88 + 16 + 60
            d.image_size = image_size
            d.tree_offset = tree_offset
            d.tree_size = tree_size
            d.data_block_size = data_bs
            d.hash_block_size = hash_bs
            d.hash_algorithm = algo
            d.flags = flags
            d.partition = _cstr(data[p:p + name_len])
            p += name_len
            d.salt = bytes(data[p:p + salt_len])
            p += salt_len
            d.digest = bytes(data[p:p + digest_len])
            return d

        if tag == TAG_HASH:
            # AvbHashDescriptor
            image_size = struct.unpack_from(">Q", data, body)[0]
            algo = _cstr(data[body + 8:body + 40])
            name_len, salt_len, digest_len, flags = struct.unpack_from(
                ">IIII", data, body + 40)
            p = body + 56 + 60           # reserved[60]
            d.image_size = image_size
            d.hash_algorithm = algo
            d.flags = flags
            d.partition = _cstr(data[p:p + name_len])
            p += name_len
            d.salt = bytes(data[p:p + salt_len])
            p += salt_len
            d.digest = bytes(data[p:p + digest_len])
            return d

        if tag == TAG_CHAIN_PARTITION:
            # AvbChainPartitionDescriptor
            ril, name_len, key_len = struct.unpack_from(">III", data, body)
            p = body + 12 + 64           # reserved[64]
            d.rollback_index_location = ril
            d.partition = _cstr(data[p:p + name_len])
            return d

        if tag == TAG_PROPERTY:
            key_len = struct.unpack_from(">Q", data, body)[0]
            p = body + 16
            d.partition = _cstr(data[p:p + key_len])
            return d

        if tag == TAG_KERNEL_CMDLINE:
            _flags, cl_len = struct.unpack_from(">II", data, body)
            d.partition = _cstr(data[body + 8:body + 8 + cl_len])[:120]
            return d
    except (struct.error, IndexError):
        return None
    return d


def summarize(data):

    out = {"hashtree": [], "hash": [], "chain": [], "other": []}
    for d in parse_descriptors(data):
        if d.tag == TAG_HASHTREE:
            out["hashtree"].append(d)
        elif d.tag == TAG_HASH:
            out["hash"].append(d)
        elif d.tag == TAG_CHAIN_PARTITION:
            out["chain"].append(d)
        else:
            out["other"].append(d)
    return out
