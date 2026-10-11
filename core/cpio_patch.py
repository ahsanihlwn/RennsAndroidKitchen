#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0


from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

try:
    from core.proc_util import CREATE_NO_WINDOW
except Exception:  # standalone run without the package on sys.path
    import sys
    CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


MANIFEST_NAME = "ramdisk_manifest.json"


class CpioPatchError(RuntimeError):
    pass


def _sha_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def _run_cpio(magiskboot: Path, dec: Path, command: str | list[str]) -> None:

    if isinstance(command, str):
        cmd_args = [command]
    else:
        cmd_args = command
    if not cmd_args:
        return
    proc = subprocess.run(
        [str(magiskboot), "cpio", dec.name, *cmd_args],
        cwd=str(dec.parent),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        creationflags=CREATE_NO_WINDOW,
    )
    if proc.returncode != 0:
        cmd_repr = cmd_args[0] if len(cmd_args) == 1 else f"{len(cmd_args)} commands"
        raise CpioPatchError(
            f"magiskboot cpio {cmd_repr!r} failed (exit {proc.returncode}): "
            f"{(proc.stdout or '').strip()}"
        )


def _read_cpio_config(config_path: Path) -> "list[tuple[str, str]]":

    entries: list[tuple[str, str]] = []
    for line in config_path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        parts = line.rsplit(" ", 3)
        if len(parts) != 4:
            continue
        path, mode, _uid, _gid = parts
        entries.append((path, mode))
    return entries


def build_manifest(extract_dir: Path, config_path: Path) -> dict:

    cfg = _read_cpio_config(config_path)


    lower_counts: dict[str, int] = {}
    for path, _mode in cfg:
        lower_counts[path.lower()] = lower_counts.get(path.lower(), 0) + 1
    colliding = {p.lower() for p in lower_counts if lower_counts[p] > 1}

    entries = []
    for path, mode in cfg:
        rec: dict = {"path": path, "mode": mode}
        if path.lower() in colliding:
            rec["unsafe"] = True
            entries.append(rec)
            continue

        disk = extract_dir / path
        try:
            if disk.is_symlink():
                rec["type"] = "symlink"
                rec["target"] = os.readlink(disk)
            elif disk.is_dir():
                rec["type"] = "dir"
            elif disk.is_file():
                rec["type"] = "file"
                rec["sha"] = _sha_file(disk)
                rec["size"] = disk.stat().st_size
            else:
                # entry in config but missing on disk (rare) — flag unsafe so
                # repack won't touch it.
                rec["unsafe"] = True
        except OSError:
            rec["unsafe"] = True
        entries.append(rec)

    return {
        "version": 1,
        "entries": entries,
        "has_collisions": bool(colliding),
        "collision_count": len(colliding),
    }


def write_manifest(meta_dir: Path, manifest: dict) -> None:
    (meta_dir / MANIFEST_NAME).write_text(
        json.dumps(manifest, indent=1) + "\n", encoding="utf-8"
    )


def read_manifest(meta_dir: Path) -> dict | None:
    path = meta_dir / MANIFEST_NAME
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _readlink(path: Path) -> str | None:
    try:
        return os.readlink(path)
    except OSError:
        return None


def _walk_current(ramdisk_dir: Path) -> "dict[str, dict]":

    current: dict[str, dict] = {}
    for root, dirs, files in os.walk(ramdisk_dir):
        # os.walk descends into symlinked dirs by default; don't — treat a
        # symlink as a leaf entry regardless of what it points at.
        real_dirs = []
        for d in dirs:
            p = Path(root) / d
            rel = p.relative_to(ramdisk_dir).as_posix()
            if p.is_symlink():
                current[rel] = {"type": "symlink", "target": _readlink(p)}
            else:
                current[rel] = {"type": "dir"}
                real_dirs.append(d)
        dirs[:] = real_dirs
        for fn in files:
            p = Path(root) / fn
            rel = p.relative_to(ramdisk_dir).as_posix()
            if p.is_symlink():
                current[rel] = {"type": "symlink", "target": _readlink(p)}
            else:
                current[rel] = {"type": "file", "path": p}
    return current


def _norm_mode(mode_str: str | None) -> str | None:
    if not mode_str:
        return None
    try:
        return f"{int(str(mode_str), 8):o}"
    except (ValueError, TypeError):
        return str(mode_str).lstrip("0") or "0"


def diff_against_manifest(
    ramdisk_dir: Path, manifest: dict, desired_modes: dict[str, str] | None = None
) -> "tuple[list[dict], list[str]]":

    # Index manifest entries by path; remember the unsafe set and known modes.
    snap: dict[str, dict] = {}
    unsafe: set[str] = set()
    unsafe_lower: set[str] = set()
    for rec in manifest.get("entries", []):
        snap[rec["path"]] = rec
        if rec.get("unsafe"):
            unsafe.add(rec["path"])
            unsafe_lower.add(rec["path"].lower())

    current = _walk_current(ramdisk_dir)
    warnings: list[str] = []
    ops: list[dict] = []

    # Default mode for brand-new entries the snapshot never saw.
    NEW_FILE_MODE = "644"
    NEW_DIR_MODE = "755"

    def is_under_unsafe(path: str) -> bool:
        # A path is untouchable if it (or any ancestor) is in a collision group.
        lp = path.lower()
        if lp in unsafe_lower:
            return True
        for u in unsafe_lower:
            if lp.startswith(u + "/"):
                return True
        return False

    # additions / modifications (drive from the current folder)
    # Sort so parent dirs are created before children.
    for rel in sorted(current.keys()):
        if is_under_unsafe(rel):
            if rel not in snap:
                warnings.append(
                    f"ignored edit under case-colliding path: {rel}"
                )
            continue

        cur = current[rel]
        old = snap.get(rel)

        if cur["type"] == "dir":
            if old is None:
                dir_mode = desired_modes.get(rel, NEW_DIR_MODE) if desired_modes else NEW_DIR_MODE
                ops.append({"op": "mkdir", "path": rel, "mode": dir_mode})
            else:
                # existing dir: check if mode changed via desired_modes
                if desired_modes and rel in desired_modes:
                    new_mode = desired_modes[rel]
                    old_mode = old.get("mode")
                    if old_mode and _norm_mode(new_mode) != _norm_mode(old_mode):
                        ops.append({"op": "mkdir", "path": rel, "mode": new_mode})
            continue

        if cur["type"] == "symlink":
            tgt = cur.get("target")
            if tgt is None:
                warnings.append(f"unreadable symlink skipped: {rel}")
                continue
            if old is None or old.get("type") != "symlink" or old.get("target") != tgt:
                if old is not None and old.get("type") != "symlink":
                    ops.append({"op": "rm", "path": rel})
                ops.append({"op": "ln", "path": rel, "target": tgt})
            continue

        # regular file
        fp = cur["path"]
        mode = None
        if desired_modes and rel in desired_modes:
            mode = desired_modes[rel]

        if old is not None and old.get("type") == "file":
            old_mode = old.get("mode")
            mode_changed = (
                mode is not None
                and old_mode is not None
                and _norm_mode(mode) != _norm_mode(old_mode)
            )
            # unchanged content and unchanged mode → skip
            if not mode_changed:
                try:
                    if old.get("sha") and _sha_file(fp) == old["sha"]:
                        continue
                except OSError:
                    pass
            if mode is None:
                mode = old.get("mode", NEW_FILE_MODE)
        else:
            if old is not None and old.get("type") != "file":
                ops.append({"op": "rm", "path": rel})
            if mode is None:
                if rel.startswith(("system/bin/", "bin/", "vendor/bin/", "odm/bin/", "sbin/")) or rel.endswith(".sh"):
                    mode = "755"
                else:
                    mode = NEW_FILE_MODE
        ops.append({"op": "add", "path": rel, "mode": mode, "file": fp})


    for path, rec in snap.items():
        if rec.get("unsafe") or is_under_unsafe(path):
            continue
        if path in current:
            continue
        # Skip if an ancestor was already removed (rm -r covers children).
        parent_removed = any(
            path.startswith(op["path"] + "/")
            for op in ops
            if op["op"] == "rm"
        )
        if parent_removed:
            continue
        ops.append({"op": "rm", "path": path})

    return ops, warnings


def apply_ops(magiskboot: Path, dec: Path, ops: list[dict], scratch: Path) -> None:

    if " " in str(scratch):
        raise CpioPatchError(f"scratch dir must be space-free: {scratch}")
    if dec.parent.resolve() != scratch.resolve():
        raise CpioPatchError("dec must live inside the scratch dir")

    rms = [o for o in ops if o["op"] == "rm"]
    mkdirs = [o for o in ops if o["op"] == "mkdir"]
    adds = [o for o in ops if o["op"] == "add"]
    lns = [o for o in ops if o["op"] == "ln"]

    BATCH_SIZE = 50
    for i in range(0, len(rms), BATCH_SIZE):
        _run_cpio(magiskboot, dec, [f"rm -r {o['path']}" for o in rms[i : i + BATCH_SIZE]])
    for i in range(0, len(mkdirs), BATCH_SIZE):
        _run_cpio(magiskboot, dec, [f"mkdir {o['mode']} {o['path']}" for o in mkdirs[i : i + BATCH_SIZE]])

    for chunk_start in range(0, len(adds), BATCH_SIZE):
        chunk = adds[chunk_start : chunk_start + BATCH_SIZE]
        staged = []
        cmd_batch = []
        for j, o in enumerate(chunk):
            stage_name = f".stage_{chunk_start + j}"
            stage_path = scratch / stage_name
            shutil.copyfile(o["file"], stage_path)
            staged.append(stage_path)
            cmd_batch.append(f"add {o['mode']} {o['path']} {stage_name}")
        try:
            _run_cpio(magiskboot, dec, cmd_batch)
        finally:
            for sp in staged:
                try:
                    sp.unlink()
                except OSError:
                    pass

    for i in range(0, len(lns), BATCH_SIZE):
        _run_cpio(magiskboot, dec, [f"ln {o['target']} {o['path']}" for o in lns[i : i + BATCH_SIZE]])
