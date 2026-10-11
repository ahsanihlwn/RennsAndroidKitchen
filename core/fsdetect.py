# SPDX-License-Identifier: MPL-2.0
import os
import subprocess
import time
print("[CORE STAGE] FILESYSTEM DETECT")
# Magic identifiers
MAGIC_EROF = "0xE0F5E1E2"

BIN_PATH = os.path.join(
    os.environ.get("LOCALAPPDATA", os.path.expanduser("~")),
    "RennsAndroidKitchen",
    "bin"
)

from core.binary_handler import resolve_binary
from core.proc_util import CREATE_NO_WINDOW


from core.header_magic import is_fs_image as _detect_fs_type_fast

def detect_fs_type(path):

    if not os.path.isfile(path):
        return ""

    # 🔥 RESOLVE SAAT DIPAKAI (LIVE)
    dump_erofs = resolve_binary(BIN_PATH, "dump.erofs.exe")
    if not dump_erofs:
        return ""

    # CHECK EROFS
    try:
        cmd = [dump_erofs, path]
        p = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
            text=True,
            creationflags=CREATE_NO_WINDOW
        )
        out, err = p.communicate(timeout=5)
        if p.returncode == 0:
            for line in out.splitlines():
                if "Filesystem magic number" in line and MAGIC_EROF in line:
                    return "EROFS"
    except:
        pass

    # CHECK EXT2/3/4
    try:
        from core.check_ext4 import check_ext4_magic
        fs = check_ext4_magic(path)
        if fs:
            return fs
    except:
        pass

    return ""

def detect_fs_root(base_dir, folder_name):


    cfg_dir = os.path.join(base_dir, "config")


    bootconf_path = os.path.join(cfg_dir, f"{folder_name}_bootconf")
    if os.path.isfile(bootconf_path):
        try:
            with open(bootconf_path, "r", encoding="utf-8", errors="ignore") as f:
                kind = f.read().strip().lower()
            if kind == "boot":
                return "BOOT"
            if kind in ("vndrboot", "vendor_boot"):
                return "VNDRBOOT"
        except:
            pass


    opt_path = os.path.join(cfg_dir, f"{folder_name}_fs_options")
    if os.path.isfile(opt_path):
        try:
            with open(opt_path, "r", encoding="utf-8") as f:
                if "mkfs.erofs" in f.read():
                    return "EROFS"
        except:
            pass


    info_path = os.path.join(cfg_dir, f"{folder_name}_info")
    if os.path.isfile(info_path):
        try:
            with open(info_path, "r", encoding="utf-8") as f:
                for ln in f:
                    if ln.startswith("Format:"):
                        fmt = ln.split(":",1)[1].strip()
                        # Contoh: EXT4, EXT3, EXT2
                        if fmt.startswith("EXT"):
                            return fmt
        except:
            pass


    return ""

def _scandir_split(path):

    subdirs, files = [], []
    try:
        with os.scandir(path) as it:
            for e in it:
                try:
                    if e.is_dir(follow_symlinks=False):
                        subdirs.append(e.name)
                    else:
                        files.append(e.name)
                except OSError:
                    files.append(e.name)
    except OSError:
        pass
    return subdirs, files


def _prescan_task_inproc(path, recursive=False):

    from core.native_backend import prescan_json
    native = prescan_json(path, recursive=recursive, full=False)
    if native is not None:
        return True, native
    import json
    folders = {}
    fscache = {}
    child_snapshot = {}

    def _scan_one(root):
        norm = os.path.normpath(root)
        dirs, files = _scandir_split(root)
        folders[norm] = bool(dirs or files)
        child_snapshot[norm] = [
            os.path.normpath(os.path.join(root, d)) for d in dirs
        ]
        for d in dirs:
            dtype = _detect_fs_type_fast(os.path.join(root, d))
            if dtype:
                fscache[os.path.normpath(os.path.join(root, d))] = dtype
        return dirs

    if recursive:
        for root, dirs, files in os.walk(path):
            norm = os.path.normpath(root)
            folders[norm] = bool(dirs or files)
            child_snapshot[norm] = [
                os.path.normpath(os.path.join(root, d)) for d in dirs
            ]
            for d in dirs:
                dtype = _detect_fs_type_fast(os.path.join(root, d))
                if dtype:
                    fscache[os.path.normpath(os.path.join(root, d))] = dtype
    else:
        # Depth-1: root + tiap anak-langsung 1 level lagi, supaya panah expand
        # pada anak root akurat tanpa harus user expand dulu. Sisanya lazy.
        for d in _scan_one(path):
            _scan_one(os.path.join(path, d))

    return True, json.dumps({
        "folders": folders,
        "fscache": fscache,
        "child_snapshot": child_snapshot,
    })


def prescan_task(path, recursive=False):

    try:
        from main_ext.config_engine_client import _ServiceConnection, prescan_task_fast
        if _ServiceConnection.get().is_connected():
            return prescan_task_fast(path, recursive=recursive)
    except TypeError:
        # client lama belum punya param recursive → fallback in-process depth-1
        return _prescan_task_inproc(path, recursive=recursive)
    except Exception:
        pass
    return _prescan_task_inproc(path, recursive=recursive)
