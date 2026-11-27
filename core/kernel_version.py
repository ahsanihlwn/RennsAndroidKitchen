# SPDX-License-Identifier: MPL-2.0
"""Kernel version detection and binary inspection engine.

Inspects Android boot images (boot.img, vendor_boot.img), compressed kernel
payloads (gzip, lz4, zstd, xz), and raw/ELF kernel binaries (ARM64 Image, vmlinux)
to detect the exact Linux kernel version, AOSP branch, git commit, compiler,
and architecture.
"""
from __future__ import annotations

import bz2
from dataclasses import dataclass, field
import gzip
import hashlib
import io
import lzma
import os
from pathlib import Path
import re
import struct
from typing import Optional
import zlib

ARM64_MAGIC = b"ARM\x64"
ARM64_MAGIC_OFF = 56

_VERSION_RE = re.compile(rb"Linux version ([0-9]+\.[0-9]+(?:\.[0-9]+)?)([^\x00\n]{0,300})")
_VERMAGIC_RE = re.compile(rb"vermagic=([^\x00\n]{1,160})")
_IKCFG_START = b"IKCFG_ST"
_IKCFG_END = b"IKCFG_ED"
_MAX_DECODED_BYTES = 512 * 1024 * 1024
_CODECS = {
    b"\x02\x21\x4c\x18": ("LZ4", "lz4"),
    b"\x04\x22\x4d\x18": ("LZ4", "lz4"),
    b"\x28\xb5\x2f\xfd": ("Zstandard", "zstandard"),
    b"\x1f\x8b": ("gzip", ""),
    b"\xfd7zXZ\x00": ("XZ", ""),
    b"BZh": ("bzip2", ""),
}


@dataclass
class KernelVersionInfo:
    file_path: str
    is_kernel: bool = False
    full_banner: str = ""
    version_string: str = ""  # e.g. "5.10.168-android12-9-00001-g38fa741d44fa"
    base_version: str = ""     # e.g. "5.10.168"
    major: int = 0
    minor: int = 0
    patch: int = 0
    short_version: str = ""    # e.g. "5.10"
    android_release: str = ""  # e.g. "android12"
    aosp_branch: str = ""      # e.g. "android12-5.10"
    git_commit: str = ""       # e.g. "38fa741d44fa"
    compiler: str = ""         # e.g. "Android clang version 14.0.6"
    arch: str = ""             # e.g. "arm64"
    vermagic: str = ""
    has_config: bool = False
    config_sha256: str = ""
    error: str = ""
    config_text: str = field(default="", repr=False)
    android_build_id: str = ""
    estimated_source_files: int = 0

    def to_dict(self) -> dict:
        return {
            "file_path": self.file_path,
            "is_kernel": self.is_kernel,
            "full_banner": self.full_banner,
            "version_string": self.version_string,
            "base_version": self.base_version,
            "major": self.major,
            "minor": self.minor,
            "patch": self.patch,
            "short_version": self.short_version,
            "android_release": self.android_release,
            "aosp_branch": self.aosp_branch,
            "git_commit": self.git_commit,
            "android_build_id": self.android_build_id,
            "compiler": self.compiler,
            "arch": self.arch,
            "vermagic": self.vermagic,
            "has_config": self.has_config,
            "config_sha256": self.config_sha256,
            "error": self.error,
        }


def _decompress_incremental(data: bytes, magic: bytes, factory, max_output: int | None) -> bytes | None:
    """Decompress one or more concatenated members while ignoring trailing DTB/padding."""
    limit = (max_output + 1) if max_output is not None else None
    chunks = []
    total = 0
    view = memoryview(data)
    pos = 0
    n = len(data)
    while pos < n and data[pos : pos + len(magic)] == magic:
        dec = factory()
        while pos < n and not getattr(dec, "eof", False):
            end = min(n, pos + 256 * 1024)
            rem = (limit - total) if limit is not None else 0
            piece = dec.decompress(view[pos:end], rem) if limit is not None else dec.decompress(view[pos:end])
            if piece:
                chunks.append(piece)
                total += len(piece)
                if limit is not None and total >= limit:
                    return b"".join(chunks)[:limit]
            if getattr(dec, "eof", False):
                unused = getattr(dec, "unused_data", b"")
                pos = end - len(unused)
                break
            unconsumed = getattr(dec, "unconsumed_tail", b"")
            if unconsumed:
                pos = end - len(unconsumed)
            else:
                pos = end
        if not getattr(dec, "eof", False):
            # Truncated stream without EOF marker: reject if bounded check is active
            return None if max_output is not None else (b"".join(chunks) or None)
    return b"".join(chunks) if chunks else None


def decompress_kernel_stream(data: bytes, max_output: int | None = None) -> bytes:
    """Decompresses kernel stream if compressed (gzip, xz, bz2, zstd, lz4), tolerating appended DTBs."""
    if not data or len(data) < 4:
        return data
    limit = (max_output + 1) if max_output is not None else None
    try:
        if data.startswith(b"\x1f\x8b"):
            out = _decompress_incremental(
                data, b"\x1f\x8b", lambda: zlib.decompressobj(16 + zlib.MAX_WBITS), max_output
            )
            return out if out is not None else data
        if data.startswith(b"\xfd7zXZ\x00"):
            out = _decompress_incremental(
                data, b"\xfd7zXZ\x00", lambda: lzma.LZMADecompressor(lzma.FORMAT_XZ), max_output
            )
            return out if out is not None else data
        if data.startswith(b"BZh"):
            out = _decompress_incremental(data, b"BZh", bz2.BZ2Decompressor, max_output)
            return out if out is not None else data
        if data.startswith(b"\x28\xb5\x2f\xfd"):
            import zstandard
            with zstandard.ZstdDecompressor().stream_reader(io.BytesIO(data)) as reader:
                return reader.read(limit) if limit is not None else reader.read()
        if data.startswith(b"\x04\x22\x4d\x18"):
            import lz4.frame
            dctx = lz4.frame.LZ4FrameDecompressor()
            out = dctx.decompress(data, max_length=limit if limit is not None else -1)
            return out if (dctx.eof or (limit is not None and len(out) >= limit)) else data
        if data.startswith(b"\x02\x21\x4c\x18"):
            from core.lz4_legacy import decompress
            return decompress(data, max_output=max_output) or data
    except Exception:
        return data
    return data


def extract_kernel_payload_from_boot_img(file_path: str | Path, max_size: int | None = None) -> bytes | None:
    """Extracts raw kernel bytes from Android boot.img / vendor_boot.img."""
    try:
        with open(file_path, "rb") as f:
            header = f.read(4096)
            if header[:8] != b"ANDROID!" and header[:8] != b"VNDRBOOT":
                return None

            if header[:8] == b"ANDROID!":
                version = struct.unpack_from("<I", header, 40)[0]
                if version >= 3:
                    kernel_size = struct.unpack_from("<I", header, 8)[0]
                    page_size = 4096
                else:
                    kernel_size = struct.unpack_from("<I", header, 8)[0]
                    page_size = struct.unpack_from("<I", header, 36)[0] or 2048

                if version > 4 or not 2048 <= page_size <= 65536 or page_size & (page_size - 1):
                    return None
                if kernel_size <= 0 or (max_size is not None and kernel_size > max_size):
                    return None
                f.seek(page_size)
                payload = f.read(kernel_size)
                return payload if len(payload) == kernel_size else None
    except Exception:
        pass
    return None


def parse_kernel_banner(banner_str: str, info: KernelVersionInfo) -> None:
    """Parses a full 'Linux version ...' banner string into KernelVersionInfo fields."""
    info.full_banner = banner_str.strip()

    # Pattern: Linux version <version_string> (<builder>) (<compiler>) #<build_num> ...
    # e.g.: Linux version 5.10.168-android12-9-00001-g38fa741d44fa (user@host) (clang 14.0.6) #1 SMP ...
    parts = banner_str.split(None, 3)
    if len(parts) >= 3:
        info.version_string = parts[2]

    # Parse semver (e.g. 5.10.168)
    m = re.search(r"([0-9]+)\.([0-9]+)(?:\.([0-9]+))?", info.version_string or banner_str)
    if m:
        info.major = int(m.group(1))
        info.minor = int(m.group(2))
        info.patch = int(m.group(3)) if m.group(3) else 0
        info.base_version = f"{info.major}.{info.minor}.{info.patch}"
        info.short_version = f"{info.major}.{info.minor}"

    # Parse Android release (e.g. android12, android13, android14)
    m_and = re.search(r"(android\d+)", banner_str, re.IGNORECASE)
    if m_and:
        info.android_release = m_and.group(1).lower()

    # Derive AOSP branch (e.g. android12-5.10)
    if info.android_release and info.short_version:
        info.aosp_branch = f"{info.android_release}-{info.short_version}"
    elif info.short_version:
        info.aosp_branch = f"linux-{info.short_version}.y"

    # Parse Git commit hash (e.g. -g38fa741d44fa)
    m_git = re.search(r"-g([0-9a-fA-F]{7,40})", banner_str)
    if m_git:
        info.git_commit = m_git.group(1).lower()

    build = re.search(r"-ab(\d+)(?:-|$)", info.version_string)
    if build:
        info.android_build_id = build.group(1)

    # Parse Compiler (handles possible nested parentheses like (Android (...) clang version ...))
    m_comp = re.search(r"((?:Android\s+)?(?:clang|gcc)[^)\n#]+)", banner_str, re.IGNORECASE)
    if m_comp:
        info.compiler = m_comp.group(1).strip()


def extract_ikconfig(data: bytes) -> Optional[str]:
    """Extracts and decompresses embedded /proc/config.gz (IKCONFIG) if present."""
    start_idx = data.find(_IKCFG_START)
    if start_idx == -1:
        return None

    # The gzip stream starts immediately after IKCFG_ST (8 bytes)
    stream_start = start_idx + len(_IKCFG_START)
    end_idx = data.find(_IKCFG_END, stream_start)
    try:
        end = min(end_idx if end_idx != -1 else len(data), stream_start + 1024 * 1024)
        with gzip.GzipFile(fileobj=io.BytesIO(data[stream_start:end])) as stream:
            config = stream.read(1024 * 1024 + 1)
        if len(config) <= 1024 * 1024:
            return config.decode("utf-8", errors="replace")
    except (ValueError, OSError, EOFError, zlib.error):
        pass
    return None


def kernel_config_digest(text: str) -> str:
    entries = sorted(
        line for line in text.splitlines()
        if line.startswith("CONFIG_") or re.fullmatch(r"# CONFIG_\w+ is not set", line)
    )
    return hashlib.sha256("\n".join(entries).encode("utf-8")).hexdigest() if entries else ""


_INSPECT_CACHE_LOCK = None
_INSPECT_CACHE: dict[tuple[str, int, int], KernelVersionInfo] = {}


def inspect_kernel(file_path: str | Path) -> KernelVersionInfo:
    """Inspects a binary file, boot image, or raw kernel image to extract version info."""
    import os
    import threading
    from dataclasses import replace

    global _INSPECT_CACHE_LOCK
    if _INSPECT_CACHE_LOCK is None:
        _INSPECT_CACHE_LOCK = threading.Lock()

    path_obj = Path(file_path)
    info = KernelVersionInfo(file_path=str(path_obj))

    if not path_obj.exists() or not path_obj.is_file():
        info.error = f"File not found: {file_path}"
        return info

    st = path_obj.stat()
    file_size = st.st_size
    if file_size == 0:
        info.error = "File is empty"
        return info

    cache_key = (os.path.abspath(str(path_obj)), st.st_size, st.st_mtime_ns)
    with _INSPECT_CACHE_LOCK:
        cached = _INSPECT_CACHE.get(cache_key)
    if cached is not None:
        return replace(cached, file_path=str(path_obj))

    # 1. Try unpacking from boot.img / vendor_boot.img
    payload = extract_kernel_payload_from_boot_img(path_obj, _MAX_DECODED_BYTES)
    if payload is None:
        # 2. Read directly from file (limit read to 128 MB for scanning)
        with open(path_obj, "rb") as f:
            magic = f.read(8)
            if magic == b"VNDRBOOT":
                info.error = "Vendor boot images do not contain a kernel. Use boot.img or Image."
                return info
            if magic == b"ANDROID!":
                info.error = "The boot image has no readable kernel payload within the size limit."
                return info
            f.seek(0)
            payload = f.read(min(file_size, 128 * 1024 * 1024))

    # Decompress if compressed
    raw_data = decompress_kernel_stream(payload, max_output=_MAX_DECODED_BYTES)
    if len(raw_data) > _MAX_DECODED_BYTES:
        info.error = "Decoded kernel exceeds the 512 MiB inspection limit."
        return info
    if raw_data is payload:
        for magic, (codec, package) in _CODECS.items():
            if payload.startswith(magic):
                hint = f" Install the Python {package} package in this environment or use a decoded Image." if package else ""
                info.error = f"Could not decode the {codec} kernel payload.{hint}"
                return info

    # Detect Architecture
    raw_arm64_image = raw_data[ARM64_MAGIC_OFF:ARM64_MAGIC_OFF + 4] == ARM64_MAGIC
    if raw_arm64_image:
        info.arch = "arm64"
    elif raw_data[:4] == b"\x7fELF":
        # ELF check
        if len(raw_data) > 18:
            elf_machine = struct.unpack_from("<H", raw_data, 18)[0]
            if elf_machine == 183:  # EM_AARCH64
                info.arch = "arm64"
            elif elf_machine == 40:  # EM_ARM
                info.arch = "arm"
            elif elf_machine == 62:  # EM_X86_64
                info.arch = "x86_64"
            elif elf_machine == 3:   # EM_386
                info.arch = "x86"

    # Search for "Linux version ..." banner
    match = _VERSION_RE.search(raw_data)
    if match:
        info.is_kernel = True
        banner_raw = match.group(0).decode("utf-8", errors="replace")
        parse_kernel_banner(banner_raw, info)
    elif raw_arm64_image:
        info.is_kernel = True

    # Search for vermagic
    vm = _VERMAGIC_RE.search(raw_data)
    if vm:
        info.vermagic = vm.group(1).decode("utf-8", errors="replace")
    elif info.version_string:
        info.vermagic = info.version_string

    # Check for embedded IKCONFIG
    info.has_config = raw_data.find(_IKCFG_START) != -1
    if info.has_config:
        info.config_text = extract_ikconfig(raw_data) or ""
        info.config_sha256 = kernel_config_digest(info.config_text)

    if info.is_kernel:
        info.estimated_source_files = len(set(re.findall(
            rb"[a-zA-Z0-9_.-]+/[a-zA-Z0-9_./-]+\.[chSrs]{1,2}\x00", raw_data
        )))

    with _INSPECT_CACHE_LOCK:
        if len(_INSPECT_CACHE) >= 8:
            _INSPECT_CACHE.pop(next(iter(_INSPECT_CACHE)))
        _INSPECT_CACHE[cache_key] = replace(info)

    return info
