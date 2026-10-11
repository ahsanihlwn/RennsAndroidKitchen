# SPDX-License-Identifier: MPL-2.0
from __future__ import annotations

import bz2
import gzip
import lzma
import re
import struct

print("[CORE STAGE] VDEV KERNEL")

ARM64_MAGIC = b"ARM\x64"
ARM64_MAGIC_OFF = 56

_VERSION_RE = re.compile(rb"Linux version [0-9][^\x00\n]{0,240}")
_VERMAGIC_RE = re.compile(rb"vermagic=([^\x00]{1,160})")

_PAGE_SIZES = {0: "unspecified", 1: "4K", 2: "16K", 3: "64K"}


def _decompress(data):

    if not data:
        return None
    if data[:2] == b"\x1f\x8b":
        try:
            return gzip.decompress(data)
        except OSError:
            # Kernel sering punya sampah di ekor setelah stream gzip-nya.
            try:
                d = gzip.GzipFile(fileobj=__import__("io").BytesIO(data))
                return d.read()
            except OSError:
                return None
    if data[:6] == b"\xfd7zXZ\x00":
        try:
            return lzma.decompress(data)
        except lzma.LZMAError:
            try:
                return lzma.LZMADecompressor().decompress(data)
            except lzma.LZMAError:
                return None
    if data[:3] == b"BZh":
        try:
            return bz2.decompress(data)
        except OSError:
            return None
    if data[:4] == b"\x28\xb5\x2f\xfd":
        try:
            import zstandard
            return zstandard.ZstdDecompressor().decompressobj().decompress(data)
        except Exception:                      # noqa: BLE001
            return None
    if data[:4] == b"\x04\x22\x4d\x18":
        try:
            import lz4.frame
            return lz4.frame.decompress(data)
        except Exception:                      # noqa: BLE001
            return None
    if data[:4] == b"\x02\x21\x4c\x18":
        from core.vdev.bootimg import _lz4_legacy
        return _lz4_legacy(data)
    return data                                # sudah mentah


class KernelInfo:
    __slots__ = ("path", "version", "banner", "vermagic", "arch", "page_size",
                 "image_size", "text_offset", "has_config", "size", "error")

    def __init__(self, path):
        self.path = path
        self.version = ""       # mis. "5.10.226-android12-9-00047-g4968e29b..."
        self.banner = ""        # baris "Linux version ..." lengkap
        self.vermagic = ""      # vermagic kernel sendiri, utk dibanding modul
        self.arch = ""
        self.page_size = ""
        self.image_size = 0
        self.text_offset = 0
        self.has_config = False
        self.size = 0
        self.error = ""

    def __repr__(self):
        return f"<KernelInfo {self.version or self.error or '?'}>"


def read_kernel(boot_img_path):

    info = KernelInfo(boot_img_path)
    try:
        with open(boot_img_path, "rb") as f:
            head = f.read(4096)
            if head[:8] != b"ANDROID!":
                info.error = "bukan boot image"
                return info
            version = struct.unpack_from("<I", head, 40)[0]
            if version >= 3:
                ksize = struct.unpack_from("<I", head, 8)[0]
                page = 4096
            else:
                ksize = struct.unpack_from("<I", head, 8)[0]
                page = struct.unpack_from("<I", head, 36)[0] or 2048
            if ksize <= 0:
                info.error = "kernel kosong"
                return info
            f.seek(page)
            raw = f.read(ksize)
    except OSError as exc:
        info.error = str(exc)
        return info

    img = _decompress(raw)
    if not img:
        info.error = "kernel tidak bisa didekompresi"
        return info
    info.size = len(img)

    if img[ARM64_MAGIC_OFF:ARM64_MAGIC_OFF + 4] == ARM64_MAGIC:
        info.arch = "arm64"
        try:
            text_off, image_size, flags = struct.unpack_from("<QQQ", img, 8)
            info.text_offset = text_off
            info.image_size = image_size
            info.page_size = _PAGE_SIZES.get((flags >> 1) & 3, "?")
        except struct.error:
            pass

    m = _VERSION_RE.search(img)
    if m:
        info.banner = m.group(0).decode("utf-8", "replace")
        # "Linux version 5.10.226-android12-9-00047-gxxxx (builder@host) ..."
        parts = info.banner.split(None, 3)
        if len(parts) >= 3:
            info.version = parts[2]


    vm = _VERMAGIC_RE.search(img)
    if vm:
        info.vermagic = vm.group(1).decode("utf-8", "replace")
    elif info.version:
        pat = re.escape(info.version.encode()) + rb"[^\x00\n]{0,160}"
        for m2 in re.finditer(pat, img):
            cand = m2.group(0).decode("utf-8", "replace")


            if " " in cand and any(t in cand for t in
                                   ("SMP", "preempt", "mod_unload")):
                info.vermagic = cand.strip()
                break
        if not info.vermagic:
            # Benar-benar tidak ketemu — biarkan versi telanjang, dan
            # vermagic_matches() akan menandainya tidak bisa dinilai.
            info.vermagic = info.version

    info.has_config = img.find(b"IKCFG_ST") >= 0
    return info


def module_vermagics(ramdisk_bytes, limit=600):

    from core.vdev.bootimg import cpio_entries
    out = {}
    if not ramdisk_bytes:
        return out
    count = 0
    for name, _mode, blob in cpio_entries(
            ramdisk_bytes, want=lambda n: n.endswith(".ko")):
        m = _VERMAGIC_RE.search(blob)
        if m:
            key = m.group(1).decode("utf-8", "replace")
            slot = out.setdefault(key, {"mods": [], "has_crcs": False})
            slot["mods"].append(name.rsplit("/", 1)[-1])
            if b"__versions" in blob:
                slot["has_crcs"] = True
        count += 1
        if count >= limit:
            break
    return out


def vermagic_matches(kernel_vermagic, vermagic, has_crcs=False):

    if not kernel_vermagic or not vermagic:
        return None                            # tidak diketahui, jangan menuduh
    a, b = kernel_vermagic, vermagic
    if has_crcs:
        # strcspn(x, " ") — buang segmen pertama (versi) beserta pemisahnya.
        if " " not in a:


            return None
        a = a.split(" ", 1)[1]
        b = b.split(" ", 1)[1] if " " in b else ""
    return a == b
