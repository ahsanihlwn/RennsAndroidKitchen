# SPDX-License-Identifier: MPL-2.0
"""RAK project configuration, imported tree navigation, and container setup."""
import datetime
import json
import os

RAK_PROJECT_FILE = "rak_project.json"
RAK_PROJECT_INFO = "rak_project.txt"


def try_create_junction(link_path: str, target_path: str) -> bool:
    """Create an NTFS directory junction on Windows without requiring admin privileges."""
    try:
        import _winapi
        target_abs = os.path.abspath(target_path)
        link_abs = os.path.abspath(link_path)
        if os.path.exists(link_abs):
            return False
        _winapi.CreateJunction(target_abs, link_abs)
        return os.path.isdir(link_abs)
    except Exception:
        return False


def load_project_config(proj_root_or_dict) -> dict:
    """Read rak_project.json if present in the project container."""
    if isinstance(proj_root_or_dict, dict):
        proj_root = proj_root_or_dict.get("path", "")
    else:
        proj_root = str(proj_root_or_dict or "")
    if not proj_root or not os.path.isdir(proj_root):
        return {}
    cfg_path = os.path.join(proj_root, RAK_PROJECT_FILE)
    if not os.path.isfile(cfg_path):
        return {}
    try:
        with open(cfg_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def resolve_tree_target(proj_root_or_dict, tree_name: str) -> str:
    """Resolve a logical tree name to its physical target directory.
    
    If mapped via rak_project.json or local .rak_target marker, navigates to target.
    Otherwise defaults to os.path.join(proj_root, tree_name).
    """
    if not proj_root_or_dict or not tree_name:
        return ""
    
    if isinstance(proj_root_or_dict, dict):
        proj_root = proj_root_or_dict.get("path", "")
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


def setup_project_container(proj_root: str, project_name: str, mode: int, p1: str, p2: str = "",
                            target1: str = None, target2: str = None) -> dict:
    """Set up project container directory, rak_project.json, and tree structures.
    
    Provides explicit attention/structure inside the container so it is clearly
    a designated project folder with metadata, and routes imported trees to external
    progress directories.
    """
    proj_root = os.path.abspath(proj_root)
    os.makedirs(proj_root, exist_ok=True)
    
    trees_meta = {}
    
    # Setup Tree 1
    t1_imported = bool(target1 and os.path.isdir(target1))
    t1_target = os.path.abspath(target1) if t1_imported else os.path.abspath(os.path.join(proj_root, p1))
    trees_meta[p1] = {
        "name": p1,
        "type": "imported" if t1_imported else "local",
        "target": t1_target
    }
    
    if t1_imported:
        os.makedirs(os.path.join(t1_target, "config"), exist_ok=True)
        link1 = os.path.join(proj_root, p1)
        if not os.path.exists(link1):
            if not try_create_junction(link1, t1_target):
                os.makedirs(link1, exist_ok=True)
                os.makedirs(os.path.join(link1, "config"), exist_ok=True)
                with open(os.path.join(link1, ".rak_target"), "w", encoding="utf-8") as f:
                    f.write(t1_target)
    else:
        os.makedirs(os.path.join(proj_root, p1, "config"), exist_ok=True)
        
    # Setup Tree 2 if Dual ROM
    if mode == 2 and p2:
        t2_imported = bool(target2 and os.path.isdir(target2))
        t2_target = os.path.abspath(target2) if t2_imported else os.path.abspath(os.path.join(proj_root, p2))
        trees_meta[p2] = {
            "name": p2,
            "type": "imported" if t2_imported else "local",
            "target": t2_target
        }
        
        if t2_imported:
            os.makedirs(os.path.join(t2_target, "config"), exist_ok=True)
            link2 = os.path.join(proj_root, p2)
            if not os.path.exists(link2):
                if not try_create_junction(link2, t2_target):
                    os.makedirs(link2, exist_ok=True)
                    os.makedirs(os.path.join(link2, "config"), exist_ok=True)
                    with open(os.path.join(link2, ".rak_target"), "w", encoding="utf-8") as f:
                        f.write(t2_target)
        else:
            os.makedirs(os.path.join(proj_root, p2, "config"), exist_ok=True)

    # rak_project.json
    has_imported = t1_imported or bool(mode == 2 and p2 and target2 and os.path.isdir(target2))
    config_data = {
        "version": 1,
        "project_name": project_name,
        "mode": mode,
        "created_at": datetime.datetime.now().isoformat(),
        "has_imported_trees": has_imported,
        "trees": trees_meta
    }
    
    cfg_file = os.path.join(proj_root, RAK_PROJECT_FILE)
    with open(cfg_file, "w", encoding="utf-8") as f:
        json.dump(config_data, f, indent=2, ensure_ascii=False)
        
    # Project attention / info file
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
        f"  [Tree 1] {p1}: {'Imported -> ' + t1_target if t1_imported else 'Local folder'}",
    ]
    if mode == 2 and p2:
        t2_imported = bool(target2 and os.path.isdir(target2))
        info_lines.append(f"  [Tree 2] {p2}: {'Imported -> ' + trees_meta[p2]['target'] if t2_imported else 'Local folder'}")
    info_lines.append("")
    info_lines.append("Note: This container holds project metadata and routes imported trees to their target folders.")
    with open(info_file, "w", encoding="utf-8") as f:
        f.write("\n".join(info_lines) + "\n")
        
    return config_data
