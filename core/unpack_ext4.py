# SPDX-License-Identifier: MPL-2.0
import os
import subprocess
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

def main(img_path: str):


    img_path = os.path.abspath(os.path.normpath(img_path))

    if not os.path.isfile(img_path):
        return False, "Image not found"
    EXT_BIN = resolve_binary(BIN_PATH, "ext_tool.exe")
    if not EXT_BIN:
        return False, "EXT_TOOL NOT FOUND"

    try:
        # output folder = <imgname tanpa .img>
        base_dir = os.path.dirname(img_path)
        part_name = os.path.splitext(os.path.basename(img_path))[0]
        extracted_folder = os.path.join(base_dir, part_name)

        cmd = [
            EXT_BIN,
            "--unpack",
            img_path
        ]

        print("[EXT-UNPACK] RUN:", _log_safe(" ".join(cmd)))

        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=BIN_PATH,
            env=_ext_tool_env(),
            creationflags=CREATE_NO_WINDOW
        )

        print("[EXT-UNPACK] returncode:", proc.returncode)
        print("[EXT-UNPACK] output:\n", _log_safe(proc.stdout))

        if proc.returncode != 0:
            return False, proc.stdout.strip() or "Unpack failed"

        # validasi hasil
        if not os.path.isdir(extracted_folder):
            return False, "Unpack finished but output folder missing"

        return True, extracted_folder

    except Exception as e:
        return False, str(e)


def ext4_x(img_path: str, out_dir: str = None):

    return main(img_path)
