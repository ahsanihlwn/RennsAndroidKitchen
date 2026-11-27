# SPDX-License-Identifier: MPL-2.0
"""Compare reproducibility at both Android boot container and kernel levels."""
import hashlib
import mmap
import struct

from core import kernel_version
from core.command_context import OperationError


MAX_KERNEL_BYTES = 512 * 1024 * 1024
COMPARE_BLOCK_BYTES = 4096
COMPRESSED_HEADERS = (
    b"\x1f\x8b", b"\xfd7zXZ\x00", b"BZh", b"\x28\xb5\x2f\xfd",
    b"\x04\x22\x4d\x18", b"\x02\x21\x4c\x18",
)


def _digest(data):
    return {"size": len(data), "sha256": hashlib.sha256(data).hexdigest()}


def _describe(path):
    with open(path, "rb") as stream:
        if len(stream.read(48)) != 48:
            raise OperationError(f"Truncated boot image: {path}")
        with mmap.mmap(stream.fileno(), 0, access=mmap.ACCESS_READ) as data:
            version, = struct.unpack_from("<I", data, 40)
            if data[:8] != b"ANDROID!" or version > 4:
                raise OperationError(f"Expected an Android boot v0–v4 image: {path}")
            page = 4096 if version >= 3 else struct.unpack_from("<I", data, 36)[0]
            if not 2048 <= page <= 65536 or page & (page - 1) or len(data) < page:
                raise OperationError(f"Invalid Android boot page size: {path}")

            def integer(offset, type_code="I"):
                return struct.unpack_from("<" + type_code, data, offset)[0]

            def alignment(end):
                return (end + page - 1) & ~(page - 1)

            sections = {"header_page": (0, page)}
            offset = page

            def append(name, size, start=None):
                nonlocal offset
                start = offset if start is None else start
                if size:
                    if start < offset or start + size > len(data):
                        raise OperationError(f"Truncated or overlapping {name} in {path}")
                    sections[name] = (start, start + size)
                offset = alignment(start + size)

            append("kernel", integer(8))
            append("ramdisk", integer(12 if version >= 3 else 16))
            if version < 3:
                append("second", integer(24))
                if version >= 1 and integer(1632):
                    append("recovery_dtbo", integer(1632), integer(1636, "Q"))
                if version == 2:
                    append("dtb", integer(1648))
            if version == 4:
                append("boot_signature", integer(1580))
            if offset < len(data):
                sections["trailer"] = (offset, len(data))

            with memoryview(data) as view:
                components = {name: _digest(view[start:end]) for name, (start, end) in sections.items()}
                blocks = None
                if "kernel" in sections and components["kernel"]["size"] <= MAX_KERNEL_BYTES:
                    start, end = sections["kernel"]
                    raw = bytes(view[start:end])
                    decoded = kernel_version.decompress_kernel_stream(raw, max_output=MAX_KERNEL_BYTES)
                    if len(decoded) <= MAX_KERNEL_BYTES and (decoded is not raw or not raw.startswith(COMPRESSED_HEADERS)):
                        components["decoded_kernel"] = _digest(decoded)
                        decoded_view = memoryview(decoded)
                        blocks = [
                            hashlib.sha256(decoded_view[index:index + COMPARE_BLOCK_BYTES]).digest()
                            for index in range(0, len(decoded), COMPARE_BLOCK_BYTES)
                        ]
                    else:
                        components["decoded_kernel"] = None
                return {"file": _digest(view), "version": version, "components": components}, blocks


def compare_boot(reference, candidate):
    before, before_blocks = _describe(reference)
    after, after_blocks = _describe(candidate)
    left, right = before["components"], after["components"]
    components = {
        key: None if left.get(key) is None and right.get(key) is None else left.get(key) == right.get(key)
        for key in sorted(set(left) | set(right))
    }
    result = {
        "reference": before, "candidate": after,
        "byte_identical": before["file"] == after["file"], "matching_components": components,
    }
    if before_blocks is not None and after_blocks is not None:
        result["decoded_kernel_similarity"] = {
            "block_bytes": COMPARE_BLOCK_BYTES,
            "matching_blocks": sum(left == right for left, right in zip(before_blocks, after_blocks)),
            "total_blocks": max(len(before_blocks), len(after_blocks)),
        }
    return result
