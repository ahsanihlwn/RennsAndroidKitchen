# SPDX-License-Identifier: MPL-2.0
import json
import os

from core import native_backend
from core.app_paths import PROJECT_DATA_DIR

print("[CORE STAGE] BROTLI TOOL")

PROJECT_DIR = PROJECT_DATA_DIR
SETTINGS_FILE = os.path.join(PROJECT_DIR, "project_settings.json")

# brotli accepts quality 0..11
LEVEL_MIN = 0
LEVEL_MAX = 11
DEFAULT_LEVEL = 1


def _log(msg):
    print(f"[BROTLI] {msg}")


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
        if not isinstance(level, int) or isinstance(level, bool) or not (LEVEL_MIN <= level <= LEVEL_MAX):
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

    out_path = img_path + ".br"
    level = get_brotli_level()
    _log(f"compress (native brotli-rs) level={level} src={img_path}")

    try:
        stats = native_backend.brotli_compress_file(img_path, out_path, quality=level)
    except (OSError, ValueError) as e:
        return False, f"brotli compress failed: {e}"

    _log(f"compressed bytes_in={stats.get('bytes_in')} bytes_out={stats.get('bytes_out')}")

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

    out_path = br_path[:-3]  # strip ".br"
    _log(f"decompress (native brotli-rs) src={br_path}")

    try:
        stats = native_backend.brotli_decompress_file(br_path, out_path)
    except (OSError, ValueError) as e:
        return False, f"brotli decompress failed: {e}"

    _log(f"decompressed bytes_in={stats.get('bytes_in')} bytes_out={stats.get('bytes_out')}")

    if not os.path.isfile(out_path):
        return False, "brotli finished but output file missing"

    if not keep_source:
        try:
            os.remove(br_path)
        except Exception as e:
            _log(f"warn: could not delete source {br_path}: {e}")

    return True, out_path
