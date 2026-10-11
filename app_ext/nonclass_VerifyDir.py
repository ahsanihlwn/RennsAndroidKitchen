# SPDX-License-Identifier: MPL-2.0
import os, json, ntpath
from core.app_paths import PROJECT_DATA_DIR

PROJECT_DIR = PROJECT_DATA_DIR
LOCATION_CFG = os.path.join(PROJECT_DIR, "default_location.json")


def system_documents_dir():
    return os.path.join(os.path.expanduser("~"), "Documents")


def load_default_location():
    try:
        with open(LOCATION_CFG, "r", encoding="utf-8") as f:
            path = json.load(f).get("path", "")
    except Exception:
        path = ""

    if path and os.path.isabs(path):
        return os.path.normpath(path)
    return ""


def resolve_default_location():
    return load_default_location() or system_documents_dir()

from concurrent.futures import ThreadPoolExecutor

def _verify_single_project(item_idx: int, proj: dict):

    try:
        proj_copy = dict(proj)
        root = proj_copy.get("path")
        p1 = proj_copy.get("port1", "")
        p2 = proj_copy.get("port2", "")
        changed = False

        if root and not os.path.isabs(root):
            abs_root = os.path.abspath(os.path.normpath(root))
            if os.path.isdir(abs_root):
                proj_copy["path"] = abs_root
                root = abs_root
                changed = True

        if root and p1:
            drive = ntpath.splitdrive(ntpath.normpath(root))[0]
            # Missing removable volumes are temporarily offline, not deleted projects.
            if drive and not os.path.isdir(drive + "\\"):
                return (item_idx, True, proj_copy, changed)

        if not root or not os.path.isdir(root):
            return (item_idx, False, None, True)

        if not p1:
            return (item_idx, False, None, True)

        p1_path = os.path.join(root, p1)
        if not os.path.isdir(p1_path):
            return (item_idx, False, None, True)

        if p2 != "":
            p2_path = os.path.join(root, p2)
            if not os.path.isdir(p2_path):
                return (item_idx, False, None, True)

        return (item_idx, True, proj_copy, changed)
    except Exception:
        return (item_idx, False, None, True)


def verify_and_clean_projects(json_path):

    if not os.path.exists(json_path):
        return []

    try:
        with open(json_path, "r", encoding="utf-8") as f:
            projects = json.load(f)
    except Exception:
        return []

    if not isinstance(projects, list) or not projects:
        return []

    # Paralelisasi pengecekan disk I/O untuk setiap project
    max_workers = min(8, len(projects))
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        results = list(executor.map(
            lambda pair: _verify_single_project(pair[0], pair[1]),
            enumerate(projects)
        ))

    # Urutkan kembali sesuai urutan asli di JSON
    results.sort(key=lambda r: r[0])

    valid_projects = []
    changed = False

    for idx, is_valid, proj_data, item_changed in results:
        if not is_valid:
            changed = True
        else:
            if item_changed:
                changed = True
            valid_projects.append(proj_data)

    # Rewrite JSON kalo ada yang dibuang atau path diubah
    if changed:
        try:
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(valid_projects, f, indent=2)
        except Exception:
            pass

    return valid_projects
