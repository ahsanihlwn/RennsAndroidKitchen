# SPDX-License-Identifier: MPL-2.0
import json
import os
from types import SimpleNamespace

from core.app_paths import PROJECT_DATA_DIR
from core.command_context import OperationError

print("[CORE STAGE] SUPER SPARSE LOGIC CORE")

PROJECT_DIR = PROJECT_DATA_DIR
SETTINGS_FILE = os.path.join(PROJECT_DIR, "project_settings.json")


def is_super_norm_enabled(settings_file: str | None = None):
    path = settings_file or SETTINGS_FILE
    try:
        with open(path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        return bool(cfg.get("super", {}).get("norm", True))
    except Exception as e:
        print("[SUPER] settings read failed → default norm=true:", e)
        return True


def normalize_partition_names(partitions):
    """Return normalized unique output names (strip *_a / *_b) preserving order."""
    seen = set()
    normalized = []
    for name in partitions:
        if len(name) > 2 and (name.endswith("_a") or name.endswith("_b")):
            base = name[:-2]
        else:
            base = name
        if base and base not in seen:
            seen.add(base)
            normalized.append(base)
    return normalized


class SuperInfo:
    def __init__(self):
        self.raw = ""
        self.partitions = []
        self.groups = set()
        self.active_slot = None
        self.slots = set()

    def add_partition(self, name):
        self.partitions.append(name)
        if name.endswith("_a"):
            self.slots.add("a")
        elif name.endswith("_b"):
            self.slots.add("b")


def read_super_info(img_path):
    from core.native_backend import super_info

    info = SuperInfo()
    try:
        data = super_info(img_path)
    except Exception as e:
        print("SUPER INFO ERROR:", e)
        return info

    info.raw = data.get("raw", "")
    for part in data.get("partitions", []):
        info.add_partition(part)
    for grp in data.get("groups", []):
        info.groups.add(grp)
    info.active_slot = data.get("active_slot")
    return info


def extract_partition(img_path, part_name, out_dir):
    from core.native_backend import super_extract

    try:
        res = super_extract(img_path, out_dir, partitions=[part_name], normalize=False)
        return SimpleNamespace(returncode=0, stdout=json.dumps(res), stderr="")
    except Exception as e:
        return SimpleNamespace(returncode=1, stdout="", stderr=str(e))


def extract_all(img_path, out_dir):
    from core.native_backend import super_extract

    norm = is_super_norm_enabled()

    print("[SUPER] extract_all called")
    print("[SUPER] IMG  :", img_path)
    print("[SUPER] OUT  :", out_dir)
    print("[SUPER] norm :", norm)
    if norm:
        print("[SUPER] policy → normalize enabled")
    else:
        print("[SUPER] policy → normalize disabled")

    try:
        res = super_extract(img_path, out_dir, normalize=norm)
        print("[SUPER] OUTPUTS:", res.get("outputs", []))
        return True, out_dir
    except Exception as e:
        return False, str(e)


def convert_to_raw(img_path, out_dir):
    from core.native_backend import super_unsparse

    print("[SUPER] UNSPARSE START")

    if not os.path.isfile(img_path):
        return False, "File tidak ditemukan"

    if not os.path.isdir(out_dir):
        os.makedirs(out_dir, exist_ok=True)

    try:
        out_path = super_unsparse(img_path, out_dir)
        print("[SUPER] UNSPARSE OK :", out_path)
        return True, out_path
    except OperationError as e:
        return False, str(e)
    except Exception as e:
        return False, str(e)
