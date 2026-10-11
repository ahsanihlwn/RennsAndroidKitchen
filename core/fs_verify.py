# SPDX-License-Identifier: MPL-2.0
# core/fs_verify.py
import os
from core import fspatch

print("[CORE STAGE] FILESYSTEM VERIFY")
class FSVerifier:
    VERBOSE = False

    def __init__(self, base):
        self.base = base  # project root

    def verify_root(self, fs_root):

        fs_dir = os.path.join(self.base, fs_root)
        cfg = os.path.join(self.base, "config", f"{fs_root}_fs_config")

        if not os.path.isdir(fs_dir):
            return False, f"{fs_root} dir not found"

        if not os.path.isfile(cfg):
            return False, f"{fs_root}_fs_config missing"

        had_extra_fields = fspatch.has_extra_fields(cfg)
        orig = fspatch.scanfs(cfg)
        files = fspatch.scanfsdir(fs_dir)
        newfs = fspatch.fspatch(orig, files, fs_dir)

        if had_extra_fields or len(newfs) != len(orig):
            fspatch.writetofile(cfg, newfs)
            return True, "patched"

        return True, "ok"
