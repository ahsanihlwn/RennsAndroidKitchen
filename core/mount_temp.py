# SPDX-License-Identifier: MPL-2.0
import os
import hashlib
import shutil
import subprocess

from core.app_paths import MOUNT_RUNTIME_DIR
from core.proc_util import CREATE_NO_WINDOW

TEMP_DIR = MOUNT_RUNTIME_DIR


def _hide(path):
    try:
        subprocess.run(["attrib", "+H", path], check=False, capture_output=True,
                        creationflags=CREATE_NO_WINDOW)
    except Exception:
        pass


def cleanup_all():

    try:
        if os.path.isdir(TEMP_DIR):
            shutil.rmtree(TEMP_DIR, ignore_errors=True)
    except Exception:
        pass


def _ensure_dir():
    if not os.path.isdir(TEMP_DIR):
        os.makedirs(TEMP_DIR, exist_ok=True)
        _hide(TEMP_DIR)


def temp_path_for(img_path, internal):

    key = f"{os.path.normpath(img_path)}::{internal}"
    h = hashlib.md5(key.encode("utf-8", "ignore")).hexdigest()[:16]
    base = os.path.basename(internal) or "file"
    return os.path.join(TEMP_DIR, f"{h}__{base}")


def extract_bytes(img_path, internal, data: bytes):

    _ensure_dir()
    out = temp_path_for(img_path, internal)
    try:
        if os.path.isfile(out) and os.path.getsize(out) == len(data):
            return out
    except Exception:
        pass
    with open(out, "wb") as f:
        f.write(data)
    return out


def release(temp_file):

    try:
        if temp_file and os.path.isfile(temp_file) and \
           os.path.normpath(temp_file).startswith(os.path.normpath(TEMP_DIR)):
            os.remove(temp_file)
    except Exception:
        pass


