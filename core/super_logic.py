# SPDX-License-Identifier: MPL-2.0
import os
import subprocess
import json
from core.app_paths import BIN_DIR, PROJECT_DATA_DIR
print("[CORE STAGE] SUPER SPARSE LOGIC CORE")

PROJECT_DIR = PROJECT_DATA_DIR

SETTINGS_FILE = os.path.join(PROJECT_DIR, "project_settings.json")

BIN_PATH = BIN_DIR

from core.binary_handler import resolve_binary
from core.proc_util import CREATE_NO_WINDOW

def is_super_norm_enabled():
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        return bool(cfg.get("super", {}).get("norm", True))
    except Exception as e:
        print("[SUPER] settings read failed → default norm=true:", e)
        return True

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


import re

SUPER_PART_RE = re.compile(
    r"super:\s+\d+\s+\.\.\s+\d+:\s+([a-zA-Z0-9_]+)"
)

def read_super_info(img_path):
    LPUNPACK = resolve_binary(BIN_PATH, "lpunpack.exe")
    if not LPUNPACK:
        return False, "LPUNPACK NOT FOUND"

    info = SuperInfo()

    cmd = [LPUNPACK, "--info", img_path]

    try:
        p = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            shell=False,
            text=True,
            creationflags=CREATE_NO_WINDOW
        )

        for line in p.stdout:
            line = line.rstrip()
            info.raw += line + "\n"

            # PARTITION
            m = SUPER_PART_RE.search(line)
            if m:
                info.partitions.append(m.group(1))
                continue

            # GROUP
            if line.strip().startswith("Group:"):
                grp = line.split(":", 1)[1].strip()
                info.groups.add(grp)
                if grp.endswith("_a"):
                    info.active_slot = "A"
                elif grp.endswith("_b"):
                    info.active_slot = "B"

    except Exception as e:
        print("SUPER INFO ERROR:", e)

    return info

def extract_partition(img_path, part_name, out_dir):

    LPUNPACK = resolve_binary(BIN_PATH, "lpunpack.exe")
    if not LPUNPACK:
        return False, "LPUNPACK NOT FOUND"

    cmd = [
        LPUNPACK,
        "-p",
        part_name,
        img_path,
        out_dir
    ]
    return subprocess.run(cmd, capture_output=True, text=True, creationflags=CREATE_NO_WINDOW)


def extract_all(img_path, out_dir):
    LPUNPACK = resolve_binary(BIN_PATH, "lpunpack.exe")
    if not LPUNPACK:
        return False, "LPUNPACK NOT FOUND"

    norm = is_super_norm_enabled()

    print("[SUPER] extract_all called")
    print("[SUPER] IMG  :", img_path)
    print("[SUPER] OUT  :", out_dir)
    print("[SUPER] norm :", norm)
    print("[SUPER] BIN  :", LPUNPACK)

    cmd = [
        LPUNPACK,
        img_path,
        out_dir
    ]

    # 🔥 POLICY HOOK (BENAR)
    if norm:
        cmd.append("--norm")
        print("[SUPER] policy → normalize enabled (--norm)")
    else:
        print("[SUPER] policy → normalize disabled")

    print("[SUPER] CMD :", " ".join(cmd))

    p = subprocess.run(cmd, capture_output=True, text=True, creationflags=CREATE_NO_WINDOW)

    print("[SUPER] RETURN :", p.returncode)
    print("[SUPER] STDOUT :\n", p.stdout)
    print("[SUPER] STDERR :\n", p.stderr)

    if p.returncode != 0:
        return False, p.stderr or "lpunpack failed"

    return True, out_dir

def convert_to_raw(img_path, out_dir):
    LPUNPACK = resolve_binary(BIN_PATH, "lpunpack.exe")
    if not LPUNPACK:
        return False, "LPUNPACK NOT FOUND"

    print("[SUPER] UNSPARSE START")

    # lpunpack replaces the sparse image with raw data.
    if not os.path.isfile(img_path):
        return False, "File tidak ditemukan"

    if not os.path.isdir(out_dir):
        os.makedirs(out_dir, exist_ok=True)

    cmd = [
        LPUNPACK,
        img_path,
        out_dir,
        "--unsparse"
    ]

    print("[SUPER] CMD :", " ".join(cmd))

    try:
        p = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            creationflags=CREATE_NO_WINDOW
        )
        print("[SUPER] UNSPARSE RC :", p.returncode)
        print("[SUPER] STDOUT :", p.stdout)
        print("[SUPER] STDERR :", p.stderr)

        if p.returncode != 0:
            return False, p.stderr or "unsparse failed"

        return True, p.stdout

    except Exception as e:
        return False, str(e)
