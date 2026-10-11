# SPDX-License-Identifier: MPL-2.0
# Generated app data lives under %LOCALAPPDATA%/RennsAndroidKitchen.
from __future__ import annotations

import hashlib
import os
import re
import shutil


APP_ROOT = os.path.join(
    os.environ.get("LOCALAPPDATA", os.path.expanduser("~")),
    "RennsAndroidKitchen",
)
BIN_DIR = os.path.join(APP_ROOT, "bin")
APP_DIR = os.path.join(APP_ROOT, "app")
DATA_DIR = os.path.join(APP_ROOT, "data")
PROJECT_DATA_DIR = os.path.join(DATA_DIR, "project")
NEOCAT_DATA_DIR = os.path.join(DATA_DIR, "neocat")
STATE_DIR = os.path.join(APP_ROOT, "state")
USER_STATE_DIR = os.path.join(STATE_DIR, "user")
RUNTIME_DIR = os.path.join(APP_ROOT, "runtime")
TEMP_DIR = os.path.join(APP_ROOT, "temp")
LOG_DIR = os.path.join(APP_ROOT, "logs")

CACHE_DIR = os.path.join(APP_ROOT, "cache")
ASSET_CACHE_DIR = os.path.join(CACHE_DIR, "assets")
PROJECT_CACHE_ROOT = os.path.join(CACHE_DIR, "projects")
TERMINAL_RUNTIME_DIR = os.path.join(RUNTIME_DIR, "terminal")
MOUNT_RUNTIME_DIR = os.path.join(RUNTIME_DIR, "mount")
IPC_RUNTIME_DIR = os.path.join(RUNTIME_DIR, "ipc")
NEOCAT_LEARNING_DIR = os.path.join(NEOCAT_DATA_DIR, "learning", "learning_contexts")


def ensure_app_layout() -> None:

    for path in (
        APP_ROOT, BIN_DIR, APP_DIR, DATA_DIR, PROJECT_DATA_DIR, NEOCAT_DATA_DIR,
        STATE_DIR, USER_STATE_DIR, RUNTIME_DIR, TEMP_DIR, LOG_DIR,
        CACHE_DIR, ASSET_CACHE_DIR, PROJECT_CACHE_ROOT,
        TERMINAL_RUNTIME_DIR, MOUNT_RUNTIME_DIR, IPC_RUNTIME_DIR,
    ):
        os.makedirs(path, exist_ok=True)
    # Project metadata used to live directly under APP_ROOT. Move it before a
    # caller reads project_settings.json so GUI and standalone CLI agree.
    migrate_legacy_dir(os.path.join(APP_ROOT, "project"), PROJECT_DATA_DIR)


def migrate_legacy_dir(source: str, destination: str) -> bool:

    if not os.path.isdir(source):
        return False
    if not os.path.exists(destination):
        os.makedirs(os.path.dirname(destination), exist_ok=True)
        shutil.move(source, destination)
    else:
        _merge_legacy_tree(source, destination)
    return True


def migrate_legacy_file(source: str, destination: str) -> bool:

    if os.path.isfile(destination) or not os.path.isfile(source):
        return False
    try:
        os.makedirs(os.path.dirname(destination), exist_ok=True)
        os.replace(source, destination)
        return True
    except OSError:
        return False


def project_cache_key(project_base: str) -> str:

    canonical = os.path.normcase(os.path.realpath(os.path.abspath(project_base)))
    digest = hashlib.sha256(canonical.encode("utf-8", "surrogatepass")).hexdigest()
    return digest[:24]


def project_cache_dir(project_base: str, *parts: str, create: bool = False) -> str:
    path = os.path.join(PROJECT_CACHE_ROOT, project_cache_key(project_base), *parts)
    if create:
        os.makedirs(path, exist_ok=True)
    return path


def _safe_component(value: str) -> str:

    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "")).strip("._")
    if not cleaned:
        raise ValueError("NeoCat cache scope must not be empty")
    return cleaned[:96]


def _legacy_neocat_dir(project_base: str, fs_root: str | None, branch_name: str | None) -> str:
    base = os.path.abspath(project_base)
    if branch_name is None:
        return os.path.join(base, "neocat_config")
    return os.path.join(
        base, "neobranch", _safe_component(fs_root),
        f"branch_{_safe_component(branch_name)}", "neocat_config",
    )


def _merge_legacy_tree(source: str, destination: str) -> None:

    if not os.path.isdir(source):
        return
    os.makedirs(destination, exist_ok=True)
    for root, dirs, files in os.walk(source):
        rel = os.path.relpath(root, source)
        target_dir = destination if rel == "." else os.path.join(destination, rel)
        os.makedirs(target_dir, exist_ok=True)
        for directory in dirs:
            os.makedirs(os.path.join(target_dir, directory), exist_ok=True)
        for filename in files:
            src_file = os.path.join(root, filename)
            dst_file = os.path.join(target_dir, filename)
            if not os.path.exists(dst_file):
                shutil.move(src_file, dst_file)
            else:
                try:
                    os.remove(src_file)
                except OSError:
                    pass
    shutil.rmtree(source, ignore_errors=True)


def neocat_cache_dir(
    project_base: str,
    fs_root: str | None = None,
    branch_name: str | None = None,
    *,
    create: bool = False,
    migrate_legacy: bool = True,
) -> str:

    if branch_name is None:
        target = project_cache_dir(project_base, "neocat")
    else:
        target = project_cache_dir(
            project_base,
            "neocat", "branches", _safe_component(fs_root),
            _safe_component(branch_name),
        )

    legacy = _legacy_neocat_dir(project_base, fs_root, branch_name)
    if migrate_legacy and os.path.isdir(legacy):
        if not os.path.exists(target):
            os.makedirs(os.path.dirname(target), exist_ok=True)
            shutil.move(legacy, target)
        else:
            _merge_legacy_tree(legacy, target)
        print(f"[NEOCAT] cache migrated to AppData for {os.path.basename(os.path.abspath(project_base))}")

    if create:
        os.makedirs(target, exist_ok=True)
    return target


def neocat_snapshot_dir(project_base: str, fs_root: str, *, create: bool = False) -> str:

    path = project_cache_dir(
        project_base, "neocat", "branch_snapshots", _safe_component(fs_root)
    )
    legacy = os.path.join(
        os.path.abspath(project_base), "neobranch", _safe_component(fs_root),
        "source", "neocat_config",
    )
    if os.path.isdir(legacy):
        if not os.path.exists(path):
            os.makedirs(os.path.dirname(path), exist_ok=True)
            shutil.move(legacy, path)
        else:
            _merge_legacy_tree(legacy, path)
        print(f"[NEOCAT] branch snapshot migrated to AppData for {os.path.basename(os.path.abspath(project_base))}")
    if create:
        os.makedirs(path, exist_ok=True)
    return path


def remove_neocat_branch_cache(project_base: str, fs_root: str, branch_name: str) -> None:

    target = neocat_cache_dir(
        project_base, fs_root, branch_name, migrate_legacy=False
    )
    branch_root = project_cache_dir(project_base, "neocat", "branches")
    try:
        if os.path.commonpath((os.path.realpath(target), os.path.realpath(branch_root))) != os.path.realpath(branch_root):
            raise ValueError("Refusing to remove a path outside NeoCat branch cache")
        if os.path.isdir(target):
            shutil.rmtree(target)
    except (OSError, ValueError):
        pass


def neocat_context_path(
    project_base: str,
    fs_root: str,
    branch_name: str | None = None,
    *,
    create: bool = False,
) -> str:
    directory = neocat_cache_dir(
        project_base, fs_root, branch_name, create=create
    )
    return os.path.join(directory, f"{_safe_component(fs_root)}_file_contexts")
