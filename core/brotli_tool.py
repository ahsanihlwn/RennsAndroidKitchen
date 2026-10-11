# SPDX-License-Identifier: MPL-2.0
import os
import json
from core.app_paths import BIN_DIR, PROJECT_DATA_DIR
import subprocess

print("[CORE STAGE] BROTLI TOOL")

BIN_PATH = BIN_DIR

PROJECT_DIR = PROJECT_DATA_DIR
SETTINGS_FILE = os.path.join(PROJECT_DIR, "project_settings.json")

from core.binary_handler import resolve_binary
from core.proc_util import CREATE_NO_WINDOW

# brotli CLI accepts -q 0..11
LEVEL_MIN = 0
LEVEL_MAX = 11
DEFAULT_LEVEL = 1


def _log(msg):
    print(f"[BROTLI] {msg}")


def _ext_tool_env():
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    return env


def _resolve_brotli_bin():
    return resolve_binary(BIN_PATH, "brotli.exe")


def get_brotli_level() -> int:

    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            cfg = json.load(f)

        br = cfg.get("brotli")
        if not isinstance(br, dict):
            _log("section 'brotli' missing → fallback default")
            return DEFAULT_LEVEL

        if br.get("default", True) is True:
            _log(f"Mode DEFAULT → level={DEFAULT_LEVEL}")
            return DEFAULT_LEVEL

        level = br.get("level")
        if not isinstance(level, int) or not (LEVEL_MIN <= level <= LEVEL_MAX):
            _log(f"level invalid: {level} → fallback {DEFAULT_LEVEL}")
            return DEFAULT_LEVEL

        _log(f"Mode CUSTOM → level={level}")
        return level

    except Exception as e:
        _log(f"settings read error: {e} → fallback {DEFAULT_LEVEL}")
        return DEFAULT_LEVEL


def compress_to_br(img_path: str, keep_source: bool = False):

    img_path = os.path.abspath(os.path.normpath(img_path))
    if not os.path.isfile(img_path):
        return False, f"Source not found: {img_path}"

    BROTLI = _resolve_brotli_bin()
    if not BROTLI:
        return False, "BROTLI BINARY NOT FOUND"

    out_path = img_path + ".br"

    # brotli will refuse to overwrite unless -f
    level = get_brotli_level()
    cmd = [BROTLI, "-q", str(level), "-f", "-o", out_path, img_path]

    _log(f"compress level={level} src={img_path}")
    _log(f"RUN: {' '.join(cmd)}")

    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=BIN_PATH,
            env=_ext_tool_env(),
            creationflags=CREATE_NO_WINDOW,
        )
    except Exception as e:
        return False, f"brotli spawn error: {e}"

    _log(f"returncode={proc.returncode}")
    if proc.stdout:
        _log(f"output:\n{proc.stdout}")

    if proc.returncode != 0:
        return False, (proc.stdout or "").strip() or "brotli compress failed"

    if not os.path.isfile(out_path):
        return False, "brotli finished but output file missing"

    if not keep_source:
        try:
            os.remove(img_path)
        except Exception as e:
            _log(f"warn: could not delete source {img_path}: {e}")

    return True, out_path


def decompress_from_br(br_path: str, keep_source: bool = False):

    br_path = os.path.abspath(os.path.normpath(br_path))
    if not os.path.isfile(br_path):
        return False, f"Source not found: {br_path}"

    if not br_path.lower().endswith(".br"):
        return False, f"Not a .br file: {br_path}"

    BROTLI = _resolve_brotli_bin()
    if not BROTLI:
        return False, "BROTLI BINARY NOT FOUND"

    out_path = br_path[:-3]  # strip ".br"

    cmd = [BROTLI, "-d", "-f", "-o", out_path, br_path]

    _log(f"decompress src={br_path}")
    _log(f"RUN: {' '.join(cmd)}")

    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=BIN_PATH,
            env=_ext_tool_env(),
            creationflags=CREATE_NO_WINDOW,
        )
    except Exception as e:
        return False, f"brotli spawn error: {e}"

    _log(f"returncode={proc.returncode}")
    if proc.stdout:
        _log(f"output:\n{proc.stdout}")

    if proc.returncode != 0:
        return False, (proc.stdout or "").strip() or "brotli decompress failed"

    if not os.path.isfile(out_path):
        return False, "brotli finished but output file missing"

    if not keep_source:
        try:
            os.remove(br_path)
        except Exception as e:
            _log(f"warn: could not delete source {br_path}: {e}")

    return True, out_path
