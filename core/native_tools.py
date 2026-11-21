# SPDX-License-Identifier: MPL-2.0
"""Built-in native Rust tool registry backed in-process by rak_native (PyO3)."""
from __future__ import annotations

import os

NATIVE_PLATFORM_TOOLS = (
    "adb",
    "fastboot",
    "mke2fs",
    "make_f2fs",
    "make_f2fs_casefold",
    "etc1tool",
    "hprof-conv",
    "sqlite3",
)

NATIVE_BUILTIN_PACKAGES = frozenset({
    "ext_tool", "make_ext4fs", "lpunpack", "magiskboot",
    "erofs_rs", "erofs", "mkfs.erofs", "dump.erofs", "fsck.erofs", "extract.erofs",
    "sdatimg", "sdat2img", "img2sdat",
    "brotli_env", "brotli", "brotli_rs",
    "platform-tools", "platform_tools", "platform_tools_rs",
    *NATIVE_PLATFORM_TOOLS, "hprof_conv",
})

_NATIVE_TOOL_FNS = {
    "lpunpack": "super_extract_json",
    "lpmake": "super_extract_json",
    "ext_cli": "ext_unpack",
    "ext_tool": "ext_unpack",
    "make_ext4fs": "ext_make_fs",
    "img2simg": "sparse_img2simg",
    "simg2img": "sparse_simg2img",
    "magiskboot": "boot_unpack_json",
    "mkfs.erofs": "erofs_pack",
    "mkfs_erofs": "erofs_pack",
    "dump.erofs": "erofs_info_json",
    "dump_erofs": "erofs_info_json",
    "fsck.erofs": "erofs_verify_json",
    "fsck_erofs": "erofs_verify_json",
    "extract.erofs": "erofs_unpack",
    "extract_erofs": "erofs_unpack",
    "erofs": "erofs_info_json",
    "erofs_rs": "erofs_info_json",
    "sdat2img": "sdat_sdat2img",
    "img2sdat": "sdat_img2sdat",
    "sdatimg": "sdat_sdat2img",
    "brotli": "brotli_compress_file_json",
    "unbrotli": "brotli_decompress_file_json",
    "brcat": "brotli_decompress_file_json",
    "brotli_rs": "brotli_compress_file_json",
    "platform-tools": "platform_tools_version_json",
    "platform_tools": "platform_tools_version_json",
    "platform_tools_rs": "platform_tools_version_json",
    "adb": "adb_run_json",
    "fastboot": "fastboot_run_json",
    "mke2fs": "platform_tool_run_stdio",
    "make_f2fs": "platform_tool_run_stdio",
    "make_f2fs_casefold": "platform_tool_run_stdio",
    "etc1tool": "platform_tool_run_stdio",
    "hprof-conv": "platform_tool_run_stdio",
    "hprof_conv": "platform_tool_run_stdio",
    "sqlite3": "platform_tool_run_stdio",
}


def _normalize_stem(name: str) -> str:
    base = os.path.basename(str(name or "").strip()).lower()
    if base.endswith(".exe"):
        base = base[:-4]
    return base


def is_native_tool(name: str) -> bool:
    return _normalize_stem(name) in _NATIVE_TOOL_FNS


def resolve_native_tool(name: str) -> str | None:
    fn_name = _NATIVE_TOOL_FNS.get(_normalize_stem(name))
    if not fn_name:
        return None
    from core.native_backend import native_module_path
    return native_module_path(fn_name)
