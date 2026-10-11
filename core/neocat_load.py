# SPDX-License-Identifier: MPL-2.0
import os
import shutil
import subprocess

from core.proc_util import CREATE_NO_WINDOW
from core.app_paths import APP_ROOT, NEOCAT_DATA_DIR, NEOCAT_LEARNING_DIR, migrate_legacy_dir

# Contexts live at the application root.
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SRC_CONTEXTS = os.path.join(_ROOT, "learning_contexts")

# Destination: AppData
NEOCAT_DIR       = NEOCAT_DATA_DIR
NEOCAT_LEARNING  = os.path.dirname(NEOCAT_LEARNING_DIR)
NEOCAT_CONTEXTS  = NEOCAT_LEARNING_DIR
_LEGACY_NEOCAT_DIR = os.path.join(APP_ROOT, "neocat")


def _hide(path: str):

    try:
        subprocess.run(
            ["attrib", "+H", path],
            check=False, capture_output=True, creationflags=CREATE_NO_WINDOW
        )
    except Exception:
        pass


def _has_content(folder: str) -> bool:

    if not os.path.isdir(folder):
        return False
    try:
        return any(True for _ in os.scandir(folder))
    except PermissionError:
        return False


def ensure_neocat() -> bool:

    migrate_legacy_dir(_LEGACY_NEOCAT_DIR, NEOCAT_DIR)
    if _has_content(NEOCAT_CONTEXTS):
        print("[neocat] already populated — skip")
        return False

    if not os.path.isdir(_SRC_CONTEXTS):
        print(f"[neocat] source tidak ditemukan: {_SRC_CONTEXTS}")
        return False

    # Buat folder tujuan
    os.makedirs(NEOCAT_CONTEXTS, exist_ok=True)

    # Hide neocat via WinAPI
    _hide(NEOCAT_DIR)

    # Copy semua isi learning_contexts source → destination
    copied = 0
    for entry in os.scandir(_SRC_CONTEXTS):
        dst = os.path.join(NEOCAT_CONTEXTS, entry.name)
        try:
            if entry.is_file():
                shutil.copy2(entry.path, dst)
            elif entry.is_dir():
                shutil.copytree(entry.path, dst, dirs_exist_ok=True)
            copied += 1
            print(f"[neocat] copied: {entry.name}")
        except Exception as e:
            print(f"[neocat] failed ({entry.name}): {e}")

    print(f"[neocat] done — {copied} item(s) -> {NEOCAT_CONTEXTS}")
    return True


if __name__ == "__main__":
    ensure_neocat()
