# SPDX-License-Identifier: MPL-2.0
"""RAK project configuration, imported tree navigation, and container setup."""
import datetime
import json
import os

RAK_PROJECT_FILE = "rak_project.json"
RAK_PROJECT_INFO = "rak_project.txt"


def _safe_dir_name(name: str) -> str:
    """Sanitise a project/tree name so it is safe to use as a directory segment."""
    import re
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", str(name or "")).strip(". ")
    return cleaned or "_unnamed"


def try_create_junction(link_path: str, target_path: str) -> bool:
    """Create an NTFS directory junction on Windows or a directory symlink on POSIX."""
    try:
        target_abs = os.path.abspath(target_path)
        link_abs = os.path.abspath(link_path)
        if os.path.lexists(link_abs):
            return False
        if os.name == "nt":
            import _winapi
            _winapi.CreateJunction(target_abs, link_abs)
            return os.path.isdir(link_abs)
        os.symlink(target_abs, link_abs, target_is_directory=True)
        return os.path.isdir(link_abs)
    except Exception:
        return False


def _remove_junction_or_stub(path: str) -> None:
    """Remove an NTFS junction, POSIX symlink, or `.rak_target` stub directory at *path*.

    Only removes the link itself; never touches the target.
    """
    if not os.path.lexists(path):
        return
    if os.path.islink(path):
        try:
            os.unlink(path)
            return
        except OSError:
            pass
    if os.name == "nt":
        try:
            # NTFS junctions appear as directories — os.path.islink is False on
            # older Pythons, but os.rmdir removes a junction without following it.
            import _winapi  # noqa: F401
            os.rmdir(path)
            return
        except Exception:
            pass
    # Fallback: try rmdir for an ordinary empty dir (stub case)
    try:
        if os.path.isdir(path):
            os.rmdir(path)
        else:
            os.remove(path)
    except OSError:
        pass


def load_project_config(proj_root_or_dict) -> dict:
    """Read rak_project.json if present, or resolve imported projects from AppData."""
    if isinstance(proj_root_or_dict, dict):
        proj_root = proj_root_or_dict.get("path", "")
        if proj_root_or_dict.get("imported") and proj_root and os.path.isdir(proj_root):
            name = proj_root_or_dict.get("project_name") or os.path.basename(proj_root) or "Project"
            return {
                "version": 1,
                "project_name": name,
                "mode": 1,
                "imported": True,
                "has_imported_trees": True,
                "trees": {
                    name: {
                        "name": name,
                        "type": "imported",
                        "target": os.path.abspath(proj_root),
                    }
                },
            }
    else:
        proj_root = str(proj_root_or_dict or "")
    if not proj_root or not os.path.isdir(proj_root):
        return {}
    cfg_path = os.path.join(proj_root, RAK_PROJECT_FILE)
    if os.path.isfile(cfg_path):
        try:
            with open(cfg_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    # Check central projects.json in AppData or active stores for imported projects
    try:
        from ui.project_store import find_project_record
        row = find_project_record(proj_root)
        if row and row.get("imported"):
            name = row.get("project_name") or os.path.basename(proj_root) or "Project"
            return {
                "version": 1,
                "project_name": name,
                "mode": 1,
                "imported": True,
                "has_imported_trees": True,
                "trees": {
                    name: {
                        "name": name,
                        "type": "imported",
                        "target": os.path.abspath(proj_root),
                    }
                },
            }
    except Exception:
        pass

    return {}


def resolve_tree_target(proj_root_or_dict, tree_name: str) -> str:
    """Resolve a logical tree name to its physical target directory.

    Resolution order:
    1. Quick-check the in-memory project record dict (port1_target / port2_target).
    2. ``rak_project.json`` → ``trees.<name>.target``.
    3. ``.rak_target`` marker file inside ``proj_root/<tree_name>/``.
    4. ``proj_root/<tree_name>/`` itself (local tree).

    For imported trees the resolved path is the **external folder** the user
    selected — Kitchen operates on it directly.  The junction at
    ``proj_root/<tree_name>`` is just an alias; ``target`` in rak_project.json
    always records the real external path.
    """
    if not proj_root_or_dict or not tree_name:
        return ""

    if isinstance(proj_root_or_dict, dict):
        proj_root = proj_root_or_dict.get("path", "")
        if proj_root_or_dict.get("imported") and proj_root and os.path.isdir(proj_root):
            return os.path.abspath(proj_root)
        # Quick check in record dict
        if proj_root_or_dict.get("port1") == tree_name and proj_root_or_dict.get("port1_target"):
            t = proj_root_or_dict["port1_target"]
            if os.path.isdir(t):
                return os.path.abspath(t)
        if proj_root_or_dict.get("port2") == tree_name and proj_root_or_dict.get("port2_target"):
            t = proj_root_or_dict["port2_target"]
            if os.path.isdir(t):
                return os.path.abspath(t)
    else:
        proj_root = str(proj_root_or_dict or "")

    # 1. Check rak_project.json
    cfg = load_project_config(proj_root)
    if cfg.get("imported"):
        return os.path.abspath(proj_root)
    trees = cfg.get("trees", {})
    if isinstance(trees, dict) and tree_name in trees:
        node = trees[tree_name]
        target = node.get("target") if isinstance(node, dict) else node
        if target and os.path.isdir(target):
            return os.path.abspath(target)

    for k, v in cfg.items():
        if isinstance(v, dict) and (k == tree_name or v.get("name") == tree_name):
            target = v.get("target")
            if target and os.path.isdir(target):
                return os.path.abspath(target)

    # 2. Check local path or marker
    local_path = os.path.join(proj_root, tree_name)
    marker = os.path.join(local_path, ".rak_target")
    if os.path.isfile(marker):
        try:
            with open(marker, "r", encoding="utf-8") as f:
                target = f.read().strip()
            if target and os.path.isdir(target):
                return os.path.abspath(target)
        except Exception:
            pass

    return os.path.abspath(local_path)


def _setup_imported_tree(
    proj_root: str,
    tree_name: str,
    external_source: str,
) -> str:
    """Link one imported tree into the project container.

    The selected external folder IS the tree root — Kitchen operates on it
    directly.  This function:

    * Creates a ``config/`` sub-directory **inside the external folder**
      (same as the local-tree behaviour, just at the external location).
    * Places an NTFS junction at ``proj_root/<tree_name>/`` → external folder
      (preferred), or a ``.rak_target`` stub directory as fallback.
    * If an alias already exists (re-import), it is removed and recreated so
      there is no stale state.

    Returns the absolute path of the external folder (= the tree root).
    """
    external_abs = os.path.abspath(external_source)

    # Ensure config/ exists inside the external folder (same as local trees).
    os.makedirs(os.path.join(external_abs, "config"), exist_ok=True)

    # Alias inside the project folder → external folder directly.
    alias = os.path.join(proj_root, tree_name)
    if os.path.lexists(alias):
        # Remove stale junction/stub so re-import always gives a clean state.
        _remove_junction_or_stub(alias)

    if not try_create_junction(alias, external_abs):
        # Junction failed — create a lightweight config-only stub with a
        # .rak_target marker so resolve_tree_target can find the real path.
        os.makedirs(os.path.join(alias, "config"), exist_ok=True)
        with open(os.path.join(alias, ".rak_target"), "w", encoding="utf-8") as f:
            f.write(external_abs)

    return external_abs


def setup_project_container(proj_root: str, project_name: str, mode: int, p1: str, p2: str = "",
                            target1: str = None, target2: str = None) -> dict:
    """Set up project container directory, rak_project.json, and tree structures.

    Import behaviour
    ----------------
    When the user imports an external folder for a tree the function:

    * Creates a ``config/`` sub-directory inside the **external folder** so
      Kitchen can store its metadata there (same layout as a local tree).
    * Places an NTFS junction (or ``.rak_target`` stub fallback) at
      ``proj_root/<tree_name>/`` → the external folder so the project folder
      stays self-contained and browsable.
    * Records the external folder path as ``trees.<name>.target`` in
      rak_project.json.  ``resolve_tree_target`` therefore returns the real
      external path directly — no AppData handler indirection.

    Re-importing (calling this with a different target for a slot that already
    has a junction) cleanly removes the old alias and creates a new one.

    Non-imported trees are created as ordinary local folders inside proj_root.
    """
    proj_root = os.path.abspath(proj_root)
    os.makedirs(proj_root, exist_ok=True)

    trees_meta = {}

    # ── Tree 1 ────────────────────────────────────────────────────────────────
    t1_imported = bool(target1 and os.path.isdir(target1))
    if t1_imported:
        t1_target = _setup_imported_tree(proj_root, p1, os.path.abspath(target1))
    else:
        t1_target = os.path.abspath(os.path.join(proj_root, p1))
        os.makedirs(os.path.join(proj_root, p1, "config"), exist_ok=True)

    trees_meta[p1] = {
        "name": p1,
        "type": "imported" if t1_imported else "local",
        "target": t1_target,
    }

    # ── Tree 2 (Dual ROM only) ─────────────────────────────────────────────────
    t2_imported = False
    t2_target = ""
    if mode == 2 and p2:
        t2_imported = bool(target2 and os.path.isdir(target2))
        if t2_imported:
            t2_target = _setup_imported_tree(proj_root, p2, os.path.abspath(target2))
        else:
            t2_target = os.path.abspath(os.path.join(proj_root, p2))
            os.makedirs(os.path.join(proj_root, p2, "config"), exist_ok=True)

        trees_meta[p2] = {
            "name": p2,
            "type": "imported" if t2_imported else "local",
            "target": t2_target,
        }

    # ── rak_project.json ───────────────────────────────────────────────────────
    has_imported = t1_imported or t2_imported
    config_data = {
        "version": 1,
        "project_name": project_name,
        "mode": mode,
        "created_at": datetime.datetime.now().isoformat(),
        "has_imported_trees": has_imported,
        "trees": trees_meta,
    }

    cfg_file = os.path.join(proj_root, RAK_PROJECT_FILE)
    with open(cfg_file, "w", encoding="utf-8") as f:
        json.dump(config_data, f, indent=2, ensure_ascii=False)

    # ── Human-readable info file ───────────────────────────────────────────────
    info_file = os.path.join(proj_root, RAK_PROJECT_INFO)
    info_lines = [
        "==================================================",
        "  Renns Android Kitchen (RAK) - Project Container",
        "==================================================",
        f"Project Name : {project_name}",
        f"Created At   : {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"Mode         : {'Single ROM (1 Tree)' if mode == 1 else 'Dual ROM (2 Trees)'}",
        "",
        "Tree Configuration:",
    ]
    if t1_imported:
        info_lines.append(f"  [Tree 1] {p1}: Imported → {t1_target}")
    else:
        info_lines.append(f"  [Tree 1] {p1}: Local folder")
    if mode == 2 and p2:
        if t2_imported:
            info_lines.append(f"  [Tree 2] {p2}: Imported → {t2_target}")
        else:
            info_lines.append(f"  [Tree 2] {p2}: Local folder")
    info_lines.append("")
    info_lines.append(
        "Note: Imported trees are linked directly — Kitchen operates on the "
        "external folder in-place. No files are copied."
    )
    with open(info_file, "w", encoding="utf-8") as f:
        f.write("\n".join(info_lines) + "\n")

    return config_data


def setup_imported_project(folder_path: str) -> dict:
    """Set up an imported project in-memory without polluting the folder.

    Kitchen operates on the folder directly as tree1. No files (rak_project.json,
    rak_project.txt, or empty config directories) are written to the user's folder.
    Any leftover rak_project.* files from previous imports are cleaned up.
    """
    folder_path = os.path.abspath(folder_path)
    # Clean up leftover files from previous imports if present
    for artifact in (RAK_PROJECT_FILE, RAK_PROJECT_INFO):
        old_file = os.path.join(folder_path, artifact)
        if os.path.isfile(old_file):
            try:
                os.remove(old_file)
            except OSError:
                pass

    name = os.path.basename(folder_path) or "Project"
    return {
        "version": 1,
        "project_name": name,
        "mode": 1,
        "imported": True,
        "has_imported_trees": True,
        "created_at": datetime.datetime.now().isoformat(),
        "trees": {
            name: {
                "name": name,
                "type": "imported",
                "target": folder_path,
            }
        },
    }
