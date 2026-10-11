# SPDX-License-Identifier: MPL-2.0
import json
import os
import hashlib

from core import apktool_tool
from core.native_backend import directory_entries
from core.fsdetect import detect_fs_root
from main_ext.class_NeoBranchManager import NeoBranchManager, NB_STORE_DIR


def _collapse_target(path):
    if os.path.basename(path).lower() != "recovery":
        return None
    try:
        with open(os.path.join(path, ".rak_dtgen.json"), encoding="utf-8") as source:
            rel = json.load(source).get("collapse_to") or ""
        target = os.path.normpath(os.path.join(path, rel.replace("/", os.sep)))
        return target if rel and os.path.isdir(target) else None
    except (OSError, ValueError, TypeError):
        return None


def scan_tree_directory(spec):
    base, logical = spec["base"], spec["path"]
    physical = spec.get("target") or logical
    branch_only = bool(spec.get("branch") and not os.path.isdir(physical))
    if branch_only:
        physical = spec["branch"]
    try:
        rows = directory_entries(physical, base)
    except (FileNotFoundError, NotADirectoryError):
        if spec.get("folder_symlink"):
            rows = []
        else:
            raise
    for entry in rows:
        entry["source"] = os.path.join(logical, entry["name"]) if branch_only else entry["path"]
        entry["branch"] = entry["path"] if branch_only else None

    nb = NeoBranchManager(base)
    is_root = os.path.normcase(os.path.normpath(logical)) == os.path.normcase(os.path.normpath(base))
    colors = {}
    if not branch_only:
        if is_root:
            store = os.path.join(base, NB_STORE_DIR)
            try:
                roots = os.listdir(store)
            except OSError:
                roots = []
            for root in roots:
                active = nb.get_active_branch(root)
                colors[root] = active.get("color", "blue") if active else None
                source = os.path.join(base, root)
                if active and not os.path.lexists(source):
                    branch = nb._branch_fs_dir(root, active["name"])
                    if os.path.isdir(branch):
                        rows.append(dict(name=root, path=branch, source=source, branch=branch,
                                         is_dir=True, size=0, mtime_ns=0, symlink=None, magic=""))
        else:
            rel = os.path.relpath(logical, base).replace("\\", "/")
            root, _, inside = rel.partition("/")
            active = nb.get_active_branch(root)
            if active:
                branch_dir = os.path.join(nb._branch_fs_dir(root, active["name"]), inside.replace("/", os.sep))
                deleted = nb.deleted_set_for_branch(root, active["name"])
                def is_deleted(entry):
                    relative = os.path.relpath(entry["source"], base).replace("\\", "/")
                    return nb.is_deleted_in_set(nb._normalize_branch_rel(root, active["name"], relative), deleted)
                source_names = {entry["name"] for entry in rows}
                hidden_names = {entry["name"] for entry in rows if is_deleted(entry)}
                rows = [entry for entry in rows if entry["name"] not in hidden_names]
                try:
                    branch_rows = directory_entries(branch_dir, base)
                except OSError:
                    branch_rows = []
                for entry in branch_rows:
                    if entry["name"] not in source_names or entry["name"] in hidden_names:
                        entry["source"] = os.path.join(logical, entry["name"])
                        entry["branch"] = entry["path"]
                        rows.append(entry)

    visible = []
    dropped = set(spec.get("dropped_badge_paths", ()))
    for entry in rows:
        name, is_dir, source = entry["name"], entry["is_dir"], entry["source"]
        if name.startswith(".rak_") or name.casefold() in {"agents.md", "claude.md", "codex.md"}:
            continue
        if is_root and (name == NB_STORE_DIR or (
                is_dir and name.casefold() == apktool_tool.APKTOOL_DEC_DIR.casefold()
                and not spec["show_apktool_dec"])):
            continue
        entry["fs_type"] = detect_fs_root(base, name) if is_dir and is_root else ""
        entry["collapse"] = _collapse_target(entry["path"]) if is_dir else None
        entry["apk_mirror"] = apktool_tool.mirror_dir_for(base, source) if (
            source.lower().endswith(".apk") and apktool_tool.is_decompiled(base, source)) else None
        low = name.lower()
        badge = entry["magic"] if low.endswith(".img") or (
            low.endswith(".bin") and entry["magic"] == "PAYLOAD") else ""
        if os.path.normpath(entry["path"]) in dropped:
            if not low.endswith((".img", ".bin")):
                from core.header_magic import check_magic
                entry["magic"] = check_magic(entry["path"])
            badge = entry["magic"]
        if low.endswith(".img.br"):
            badge = "BR" if entry["size"] else ""
        elif low.endswith((".new.dat", ".new.dat.br")):
            suffix = ".new.dat.br" if low.endswith(".new.dat.br") else ".new.dat"
            transfer = entry["path"][:-len(suffix)] + ".transfer.list"
            badge = ("DAT.BR" if suffix.endswith(".br") else "DAT") if os.path.isfile(transfer) else ""
        entry["badge"] = badge
        is_folder = is_dir or bool(entry["symlink"] and entry["symlink"]["is_dir"])
        entry["priority"] = (-2 if is_dir and is_root and low == "config" else
                              -1 if is_dir and is_root and low == apktool_tool.APKTOOL_DEC_DIR.lower() else
                              0 if is_folder else
                              1)
        raw = json.dumps(entry, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        entry["fingerprint"] = hashlib.blake2b(raw, digest_size=16).hexdigest()
        visible.append(entry)
    visible.sort(key=lambda entry: (entry["priority"], entry["name"].lower()))
    signature = [(entry["source"], entry["fingerprint"]) for entry in visible], sorted(colors.items())
    return True, dict(entries=visible, colors=colors, is_root=is_root, branch_only=branch_only,
                      physical=physical, signature=signature, unchanged=signature == spec.get("previous"))
