# SPDX-License-Identifier: MPL-2.0
from __future__ import annotations

import struct as _struct

from .fdt import FDT_MAGIC, DT_TABLE_MAGIC
from .flatten import dt_from_blob, dt_to_blob
from .source import dt_to_source
from .parser import dt_from_source
from .tree import (
    BootInfo, Node, Property, ReserveInfo, fill_fullpaths, fixup_references,
    assign_explicit_phandles,
)


class DeviceTreeError(RuntimeError):
    pass


def bootinfo_from_blob(dtb_bytes: bytes) -> BootInfo:

    bi = dt_from_blob(dtb_bytes)
    assign_explicit_phandles(bi.dt)
    return bi


def blob_from_bootinfo(bi: BootInfo, version: int = 17) -> bytes:

    return dt_to_blob(bi, version)


def source_from_bootinfo(bi: BootInfo) -> str:

    return dt_to_source(bi)


def bootinfo_from_source(dts_text: str, base_dir: str = ".") -> BootInfo:

    bi = dt_from_source(dts_text, base_dir)
    fill_fullpaths(bi.dt, "")
    fixup_references(bi.dt)
    return bi


def compile(dts_text: str, version: int = 17, base_dir: str = ".") -> bytes:

    return dt_to_blob(bootinfo_from_source(dts_text, base_dir), version)


def looks_like_container(blob: bytes) -> bool:
    return len(blob) >= 4 and _struct.unpack_from(">I", blob, 0)[0] == DT_TABLE_MAGIC


def decompile(dtb_bytes: bytes) -> str:

    if looks_like_container(dtb_bytes):
        raise DeviceTreeError(
            "blob is an Android DT/DTBO container (magic d7b7ab1e); "
            "split it with core.devicetree.dt_image first")
    return dt_to_source(dt_from_blob(dtb_bytes))


def apply_overlay_blobs(base_blob: bytes, overlay_blob: bytes, version: int = 17) -> bytes:

    from .overlay import apply_overlay
    base = bootinfo_from_blob(base_blob)
    ov = bootinfo_from_blob(overlay_blob)
    return blob_from_bootinfo(apply_overlay(base, ov), version)


__all__ = [
    "DeviceTreeError",
    "decompile",
    "compile",
    "bootinfo_from_blob",
    "blob_from_bootinfo",
    "source_from_bootinfo",
    "bootinfo_from_source",
    "apply_overlay_blobs",
    "looks_like_container",
    "BootInfo", "Node", "Property", "ReserveInfo",
]
