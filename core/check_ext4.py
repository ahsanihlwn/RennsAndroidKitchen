# SPDX-License-Identifier: MPL-2.0
import subprocess
import os
import re
import sys
from core.app_paths import BIN_DIR

BIN_PATH = BIN_DIR

from core.binary_handler import resolve_binary
from core.proc_util import CREATE_NO_WINDOW

def _log_safe(value):
    return str(value).encode("ascii", "backslashreplace").decode("ascii")

def _ext_tool_env():
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    return env

def check_ext4_magic(img_path: str) -> str:
    img_path = os.path.abspath(os.path.normpath(img_path))
    print("[DEBUG] check_ext4_magic:", _log_safe(img_path))

    # 🔥 RESOLVE SAAT DIPAKAI
    EXT_BIN = resolve_binary(BIN_PATH, "ext_tool.exe")

    print("[DEBUG] EXT_BIN =", EXT_BIN)
    print("[DEBUG] BIN_PATH =", BIN_PATH)
    print("[DEBUG] EXT_BIN exists:", os.path.isfile(EXT_BIN) if EXT_BIN else False)

    if not os.path.isfile(img_path):
        print("[DEBUG] img not found")
        return ""

    if not EXT_BIN or not os.path.isfile(EXT_BIN):
        print("[DEBUG] ext_tool.exe not found")
        return ""

    try:
        proc = subprocess.run(
            [EXT_BIN, "--read", img_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
            cwd=BIN_PATH,
            env=_ext_tool_env(),
            creationflags=CREATE_NO_WINDOW
        )

        for line in proc.stdout.splitlines():
            if "Detected type" in line:
                fs = line.split(":", 1)[1].strip()
                return fs if fs in ("EXT2", "EXT3", "EXT4") else ""

        return ""

    except Exception as e:
        print("[DEBUG] exception:", e)
        return ""

