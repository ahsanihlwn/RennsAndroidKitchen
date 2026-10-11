# SPDX-License-Identifier: MPL-2.0
from __future__ import annotations

print("[CORE STAGE] VDEV CONTEXT")


class Context:
    def __init__(self, port_dir, layout, vfs, ramdisk_vfs,
                 fstab=None, fstab_origin="", options=None, progress=None):
        self.port_dir = port_dir
        self.layout = layout
        self.vfs = vfs
        self.ramdisk_vfs = ramdisk_vfs
        self.fstab = fstab or []
        self.fstab_origin = fstab_origin
        self.options = options or {}
        self.profile = {}          # diisi stage 0, dibaca stage lain
        self.shared = {}           # titipan antar-stage
        self._progress = progress
        self._apex = None


    @property
    def has_system(self):
        return bool(self.layout.system_root)

    @property
    def has_vendor(self):
        return ("vendor" in self.layout.partitions
                or "vendor" in self.layout.fs_images)

    # ── opsi ──
    @property
    def vbmeta_enabled(self):

        return bool(self.options.get("vbmeta_enabled", True))

    @property
    def deep_linker(self):
        return bool(self.options.get("deep_linker", True))

    # ── lazy APEX index ──
    @property
    def apex(self):
        if self._apex is None:
            from core.vdev.apexdb import build_index
            self.note("Indexing APEX…")
            self._apex = build_index(self.vfs)
        return self._apex

    # ── util ──
    def note(self, text):
        if self._progress:
            try:
                self._progress(text)
            except Exception:
                pass

    def prop(self, key, default=""):
        return self.profile.get("props", {}).get(key, default)

    @property
    def is64(self):
        return bool(self.profile.get("has_lib64", True))

    def libdirs(self, partition):

        base = "" if partition == "system" else "/" + partition
        if partition == "system":
            base = "/system"
        dirs = []
        if self.is64:
            dirs.append(base + "/lib64")
        dirs.append(base + "/lib")
        return dirs
