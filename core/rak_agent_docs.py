# SPDX-License-Identifier: MPL-2.0
# Refresh the kitchen CLI instructions in the user's project.

import os
import sys
import re
from typing import Optional, List, Tuple

# Ensure project root is in sys.path
_this_dir = os.path.dirname(os.path.abspath(__file__))
_proj_root = os.path.dirname(_this_dir)
if _proj_root not in sys.path:
    sys.path.insert(0, _proj_root)

try:
    from core.header_magic import check_magic
except ImportError:
    def check_magic(path: str) -> str:
        return ""

# Canonical Delimiters
START_MARKER = "<!-- RAK_CLI_AUTO_GENERATED: START. DO NOT REMOVE THIS OR EDIT INSTRUCT -->"
END_MARKER = "<!-- RAK_CLI_AUTO_GENERATED: END -->"

# Known partition directory names in Android ROMs
KNOWN_PARTITIONS = {
    "system", "vendor", "product", "system_ext", "odm", "oem", "mi_ext",
    "cust", "boot", "vendor_boot", "recovery", "dtbo", "vbmeta", "radio"
}


def is_start_delimiter(line: str) -> bool:

    u = line.strip().upper()
    return "RAK_CLI_AUTO_GENERATED" in u and ("DONT REMOVE" in u or "START" in u)


def is_end_delimiter(line: str) -> bool:

    u = line.strip().upper()
    return "RAK_CLI_AUTO_GENERATED" in u and "END" in u


def _format_bytes(size_bytes: int) -> str:

    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"


def scan_project_partitions(cwd: str) -> List[dict]:

    results = []
    if not cwd or not os.path.isdir(cwd):
        return results

    try:
        from core.rak_neobranch_view import ActiveProjectView, project_root
        view = ActiveProjectView(project_root(cwd))
        entries = [item["name"] for item in view.list_dir(cwd)]
    except ValueError:
        view = None
        try:
            entries = sorted(os.listdir(cwd))
        except OSError:
            return results
    except OSError:
        return results

    def visible_path(path):
        return view.resolve(path)[0] if view else path

    def visible_exists(path):
        physical = visible_path(path)
        return bool(physical and os.path.exists(physical))

    # 1. Detect Images (.img, .bin)
    for name in entries:
        full_path = visible_path(os.path.join(cwd, name))
        if not full_path or not os.path.isfile(full_path):
            continue

        lower = name.lower()
        if lower.endswith((".img", ".bin")):
            try:
                magic = check_magic(full_path)
            except Exception:
                magic = ""

            try:
                size_str = _format_bytes(os.path.getsize(full_path))
            except Exception:
                size_str = "unknown size"

            if magic == "EROFS":
                results.append({
                    "name": name,
                    "kind": "image",
                    "format": "EROFS",
                    "desc": f"EROFS Partition Image ({size_str})",
                    "action": f"rak unpack {name}",
                })
            elif magic in ("EXT4", "EXT3", "EXT2"):
                results.append({
                    "name": name,
                    "kind": "image",
                    "format": magic,
                    "desc": f"{magic} Partition Image ({size_str})",
                    "action": f"rak unpack {name}",
                })
            elif magic == "SUPER":
                results.append({
                    "name": name,
                    "kind": "image",
                    "format": "SUPER",
                    "desc": f"Android Dynamic Super Partition ({size_str})",
                    "action": f"rak unpack {name}",
                })
            elif magic == "SPARSE":
                results.append({
                    "name": name,
                    "kind": "image",
                    "format": "SPARSE",
                    "desc": f"Android Sparse Image ({size_str})",
                    "action": f"rak unpack {name}",
                })
            elif magic == "PAYLOAD":
                results.append({
                    "name": name,
                    "kind": "image",
                    "format": "PAYLOAD",
                    "desc": f"Android OTA CrAU Payload ({size_str})",
                    "action": f"rak unpack {name}",
                })
            elif magic in ("BOOT", "VNDRBOOT"):
                results.append({
                    "name": name,
                    "kind": "image",
                    "format": magic,
                    "desc": f"Android {magic} Image ({size_str})",
                    "action": f"rak unpack {name}",
                })

    # 2. Detect Unpacked Partition Directories
    for name in entries:
        if name.startswith((".", "__")) or name.lower() in ("node_modules", "venv", "env", "dist", "build", "core", "bin", "tests"):
            continue

        logical_path = os.path.join(cwd, name)
        full_path = visible_path(logical_path)
        if not full_path or not os.path.isdir(full_path):
            continue

        lower = name.lower()
        is_known = lower in KNOWN_PARTITIONS
        has_android_fs = (
            visible_exists(os.path.join(logical_path, "build.prop")) or
            visible_exists(os.path.join(logical_path, "default.prop")) or
            visible_exists(os.path.join(logical_path, "config"))
        )

        if is_known or has_android_fs:
            results.append({
                "name": name + "/",
                "kind": "dir",
                "format": "FOLDER",
                "desc": f"Unpacked Partition Directory",
                "action": f"rak repack {name}",
            })

    return results


def detect_dual_trees(cwd: Optional[str] = None) -> Tuple[Optional[str], Optional[str]]:

    # 1. Check live Main instance via rak_cli._main_ref or Main.instance
    main = None
    try:
        from core import rak_cli
        main = getattr(rak_cli, "_main_ref", None)
    except Exception:
        pass

    if not main:
        try:
            from main import Main
            main = getattr(Main, "instance", None)
        except Exception:
            pass

    if main:
        t1 = getattr(main.tree, "base", None) if hasattr(main, "tree") and main.tree else None
        t2 = getattr(main.tree2, "base", None) if hasattr(main, "tree2") and main.tree2 else None
        if t1 and os.path.isdir(t1):
            if t2 and os.path.isdir(t2):
                return (os.path.normpath(t1), os.path.normpath(t2))
            return (os.path.normpath(t1), None)

    # 2. Standalone / heuristic detection from cwd
    if cwd and os.path.isdir(cwd):
        norm_cwd = os.path.normpath(cwd)
        # Check if cwd is project root containing PORT1/PORT2 or port/base
        try:
            subs = {e.lower(): os.path.join(norm_cwd, e) for e in os.listdir(norm_cwd) if os.path.isdir(os.path.join(norm_cwd, e))}
            if "port1" in subs and "port2" in subs:
                return (subs["port1"], subs["port2"])
            if "port" in subs and ("base" in subs or "stock" in subs):
                return (subs["port"], subs.get("base") or subs.get("stock"))
        except Exception:
            pass

        # Check if cwd is itself Tree 1 and parent directory contains Tree 2
        parent = os.path.dirname(norm_cwd)
        if parent and os.path.isdir(parent):
            try:
                parent_subs = {e.lower(): os.path.join(parent, e) for e in os.listdir(parent) if os.path.isdir(os.path.join(parent, e))}
                base_name = os.path.basename(norm_cwd).lower()
                if base_name in ("port1", "port"):
                    t2 = parent_subs.get("port2") or parent_subs.get("base") or parent_subs.get("stock")
                    if t2:
                        return (norm_cwd, t2)
            except Exception:
                pass

    return (None, None)


def get_all_workspace_dirs(cwd: Optional[str] = None) -> List[str]:

    dirs = []

    def _add(d):
        if d and isinstance(d, str):
            norm = os.path.normpath(d)
            if os.path.isdir(norm) and norm not in dirs:
                dirs.append(norm)

    # 1. Check live Main instance
    main = None
    try:
        from core import rak_cli
        main = getattr(rak_cli, "_main_ref", None)
    except Exception:
        pass

    if not main:
        try:
            from main import Main
            main = getattr(Main, "instance", None)
        except Exception:
            pass

    if main:
        if hasattr(main, "active_tree") and main.active_tree and getattr(main.active_tree, "base", None):
            _add(main.active_tree.base)
        if hasattr(main, "tree") and main.tree and getattr(main.tree, "base", None):
            _add(main.tree.base)
        if hasattr(main, "tree2") and main.tree2 and getattr(main.tree2, "base", None):
            _add(main.tree2.base)
        if getattr(main, "project", None):
            _add(main.project)

    # 2. Check provided cwd
    if cwd:
        _add(cwd)

    # 3. If cwd was inside a tree directory, inspect parent for sibling trees
    if cwd and os.path.isdir(cwd):
        parent = os.path.dirname(os.path.normpath(cwd))
        if parent and os.path.isdir(parent):
            try:
                for entry in os.listdir(parent):
                    sibling = os.path.join(parent, entry)
                    if os.path.isdir(sibling) and entry.lower() in ("port", "port1", "port2", "base", "stock", "rom1", "rom2"):
                        _add(sibling)
            except Exception:
                pass

    # 4. Fallback to os.getcwd()
    if not dirs:
        _add(os.getcwd())

    return dirs


def generate_dynamic_instructions(cwd: str) -> str:
    norm_cwd = os.path.normpath(cwd) if cwd else os.getcwd()
    t1_base, t2_base = detect_dual_trees(norm_cwd)

    lines = [
        START_MARKER,
        "# 🤖 Renns Android Kitchen (RAK) — Environment & Command Router",
        "",
        "You are operating in an Android ROM / partition project managed by **Renns Android Kitchen (RAK)**.",
        f"- **Current Workspace:** `{norm_cwd}`",
    ]

    if t1_base:
        lines.append(f"- **Tree 1 (Port ROM / Active Build):** `{t1_base}`")
    if t2_base:
        lines.append(f"- **Tree 2 (Base ROM / Reference Donor):** `{t2_base}`")

    lines.extend([
        "- **Kitchen IPC:** Live synchronized (all `rak` commands route to the Kitchen GUI).",
        "",
        "## ⚠️ CRITICAL RULES FOR AI AGENTS",
        "1. **DO NOT invoke raw binaries directly** (never execute `extract.erofs.exe`, `make_ext4fs.exe`, `lpunpack.exe`, `simg2img.exe` manually).",
        "2. **ALWAYS use the unified `rak` CLI** available in your PATH / terminal.",
        "   - Smart Auto-Detect: Use `rak unpack <file>` directly without guessing the filesystem. RAK detects EROFS, EXT4, SUPER, SPARSE, BOOT, PAYLOAD, and archives automatically via header magic.",
        "   - Smart Auto-Repack: Use `rak repack <folder>` to repack partition directories back to images.",
        "   - `rak` routes directly to the Kitchen engine & GUI task queue via local IPC.",
        "   - GUI state, loading bars, and the FileTree will refresh automatically.",
        "3. Run `rak list` to see all supported actions.",
        "4. Run `rak badge <path>` to check filesystem format of partition images.",
        "5. Run `rak refresh` to force the GUI FileTree to synchronize with disk.",
        "6. **VIRTUAL MOUNT vs PHYSICAL UNPACK**:",
        "   - EROFS and EXT4 images can be virtually mounted in GUI (`rak mount <file>`) or directly operated on via CLI (`rak img ...`).",
        "   - Virtual mount does NOT create a physical folder on disk. Do NOT look for an unpacked folder unless you ran `rak unpack <file>`.",
        "   - You can read and write files directly inside the image without unpacking using `rak img cat` and `rak img write`!",
        "",
        "## 💾 Virtual In-Image Operations (`rak img`) — Zero Disk Space!",
        "For EXT2/3/4 and EROFS images, you can inspect, read, and edit files in-place without unpacking:",
        "- `rak img ls <file.img> [subpath] [-l] [-r]` : List directory contents, permissions, and SELinux contexts.",
        "- `rak img cat <file.img> <internal_path>` : Dump file contents directly to stdout.",
        "- `rak img stat <file.img> <internal_path>` : Show detailed node metadata (mode, inode, SELinux).",
        "- `rak img extract <file.img> <internal_path> [dest]` : Extract single file or subtree (alias: `pull`).",
        "- `rak img write <file.img> <internal_path> <local_file>` : Write/edit file directly inside image in-place (alias: `push`).",
        "- `rak img perm <file.img> <internal_path> <octal>` : Change permission mode (e.g. 0644, 0755).",
        "- `rak img selinux <file.img> <internal_path> <context>` : Change SELinux context.",
        "- `rak img rm <file.img> <internal_path> [-r]` : Delete file or directory inside image.",
        "- `rak img info <file.img>` : Show filesystem & block usage stats (alias: `usage`).",
        "- Image reads resolve the active NeoBranch version of images inside a partition tree. The root-level source image for an active partition is blocked until repack; use `rak fs` on the unpacked tree.",
        "- Direct image writes are blocked when that image belongs to an active NeoBranch; use managed `rak fs` actions for unpacked files.",
        "- Extract image contents outside an active partition folder, then import with `rak fs`.",
        "- `rak mount <file.img>` : Mount image virtually into Kitchen GUI FileTree.",
        "- `rak unmount <file.img>` : Unmount virtual image from GUI.",
        "- `rak mounted` : List images currently mounted in GUI.",
        "",
        "## 🌿 NeoBranch Management (`rak branch`) — Non-Destructive Partition Branching",
        "NeoBranch provides Git-style non-destructive versioning per filesystem partition.",
        "Edits, additions, and deletions are saved into the active branch overlay while keeping the original firmware snapshot intact.",
        "- `rak branch create <partition_folder> <branch_name> [color]` : Create and activate a new branch for a partition (e.g. `rak branch create vendor my-feature blue`). Colors: blue, green, red, yellow, orange, purple, pink.",
        "- `rak branch switch <partition_folder> <branch_name>` : Switch between branches (alias: `checkout`). To switch back to untouched original source, use `rak branch switch <partition_folder> source`.",
        "- `rak branch list [partition_folder]` : List all branches and show which branch is currently active.",
        "- `rak branch current <partition_folder>` : Check the currently active branch (alias: `status`).",
        "- `rak branch delete <partition_folder> <branch_name> --yes` : Permanently delete a branch overlay.",
        "",
        "## Reading the Active Project View (`rak fs`)",
        "For files inside an unpacked ROM project, use the logical partition path shown by RAK. The active NeoBranch may contain files absent from the source folder.",
        "- `rak ls [folder]` (alias: `rak fs ls`) : List the merged active view (source files plus branch additions and replacements, minus branch deletions). The `[branch]` or `[source]` tag shows where each visible entry comes from.",
        "- `rak fs ls <folder> --branch-only` : Show only entries physically present in the active branch overlay.",
        "- `rak cat <file>` (alias: `rak fs cat`) : Read the active version of a text file, including branch-only files.",
        "- `rak grep <regex> [path]` (alias: `rak fs grep`) : Search visible files in the active view.",
        "- In the built-in AI tools, use `list_dir`, `inspect`, `read_file`, and `grep_search`; these use the same active NeoBranch view.",
        "- Do not use PowerShell `Get-ChildItem`/`Get-Content`, `dir`, `type`, raw Python file reads, or direct `neobranch/` storage paths to inspect ROM project files. They bypass the logical branch view and can report the wrong content or miss branch-only files.",
        "- The built-in agent shell tool is disabled while any NeoBranch is active; use structured RAK file commands.",
        "- Check `rak branch current <partition_folder>` before work when the intended branch is unclear.",
        "",
        "## Safe Project-Tree Mutations (`rak fs`)",
        "All project-tree file mutations must use `rak fs`; it validates managed paths, uses the GUI task pipeline, and preserves NeoBranch and filesystem metadata.",
        "- `rak fs copy <source> <destination-folder>` : Copy through the managed clipboard pipeline.",
        "- `rak fs move <source> <destination-folder>` : Move through the managed clipboard pipeline.",
        "- To duplicate a file or folder in the same parent, use `rak fs copy <source> <parent-folder>`; the clipboard pipeline chooses a numbered name.",
        "- `rak fs delete <path> --yes` : Delete through the managed task queue; explicit acknowledgement is required.",
        "- `rak fs mkdir <parent-folder> <name>` / `rak fs touch <parent-folder> <name>` : Create a folder or empty file.",
        "- `rak fs rename <path> <new-name> [--force]` : Rename with filesystem metadata remapping; --force retries a lock held by Kitchen child tools.",
        "- `rak fs write <logical-file> --from <utf8-input-file>` : Write text through the active NeoBranch. The input can be a temporary file outside the ROM tree.",
        "- `rak fs replace <logical-file> <old-text> <new-text> [--all]` : Replace text through the active NeoBranch.",
        "- `rak fs symlink <parent-folder> <name> <android-target>` : Create an Android symlink with metadata handling.",
        "- `rak fs make-filesystem <partition-folder> --type <erofs|ext4> [--mount-point /path] --yes` : Initialize a new partition filesystem.",
        "- Mounted images are not project-tree folders: use `rak img` for their contents.",
        "- Boot/vendor_boot ramdisks are CPIO: mode/UID/GID live in CPIO entries, but CPIO has no per-file SELinux label. RAK currently reapplies mode overrides from `config/<boot>_fs_config`; edited UID/GID fields are not reapplied. Never create `config/<boot>_file_contexts` to label a boot ramdisk; RAK rejects that ineffective metadata at repack. Find the policy actually loaded in this boot mode: recovery may carry `*_file_contexts` and `sepolicy` in the ramdisk root, while normal boot may use mounted system/vendor policy. On vendor_boot v4, RAK edits an existing policy file in its original fragment but rejects new or duplicate-fragment policy paths whose target cannot be inferred from the merged tree. For a file that survives until SELinux loads, inspect the applicable `restorecon` path before claiming it has a label.",
        "- `rak compare` reads each active view; save JSON or `.renxec` reports outside active partition folders.",
        "- `rak automation <file.renxec> <fs>` applies RDT to the selected partition's active NeoBranch view; without an active branch it writes to source.",
        "- `rak policy plan <fs> [--apply]` reads the active view of supported partitions. `--apply` saves verified NeoCat proposals in the target branch's cache when that is the only active branch.",
        "- Other legacy conversion, unpack, and APK actions exposed by `rak` are rejected for targets inside an active branch until their handlers use the logical view.",
        "",
    ])

    # Dual Tree section if Tree 2 exists
    if t1_base and t2_base:
        lines.extend([
            "## 🌲 Dual-Tree Workspace Awareness (Port vs Base/Stock)",
            "This Kitchen project contains two synchronized ROM trees:",
            f"- **Tree 1 (Port ROM / Target):** `{t1_base}` — This is your active target workspace where modifications and repacks take place.",
            f"- **Tree 2 (Base ROM / Reference Donor):** `{t2_base}` — This is the original reference / stock ROM.",
            "",
            "### 🔄 Cross-Tree Operations (Never Blind to Tree 2!)",
            f"1. **Inspect / Reference Tree 2 Freely:** When working in Tree 1, inspect Tree 2 at `{t2_base}` with `rak ls`, `rak cat`, or `rak grep` and explicit Tree 2 paths. This keeps reads on Tree 2's active NeoBranch view.",
            "2. **Compare Partitions Across Trees:**",
            "   Use `rak compare <tree1_dir> <tree2_dir>` to inspect each tree's active NeoBranch view, including branch-only files, deletions, permissions, and SELinux contexts.",
            f"   - Example: `rak compare \"{t1_base}\\system\" \"{t2_base}\\system\"`",
            f"   - Example: `rak compare \"{t1_base}\\vendor\" \"{t2_base}\\vendor\"`",
            "3. **Repack Modified Partitions:**",
            f"   Repack Tree 1 partitions using `rak repack <folder>` (e.g. `rak repack \"{t1_base}\\system\"` or `rak repack system` when inside Tree 1).",
            "",
        ])

        # Partitions for Tree 1
        t1_parts = scan_project_partitions(t1_base)
        lines.append("## 📦 Detected Partitions in Tree 1 (Port / Target)")
        if t1_parts:
            for p in t1_parts:
                if p["action"]:
                    lines.append(f"- `{p['name']}` ({p['desc']}) ➔ Command: `{p['action']}`")
                else:
                    lines.append(f"- `{p['name']}` ({p['desc']})")
        else:
            lines.append("- *(No raw partition images or unpacked folders detected in Tree 1 root)*")

        # Partitions for Tree 2
        t2_parts = scan_project_partitions(t2_base)
        lines.append("")
        lines.append("## 📦 Detected Partitions in Tree 2 (Base / Reference)")
        if t2_parts:
            for p in t2_parts:
                lines.append(f"- `{p['name']}` ({p['desc']}) [Reference only]")
        else:
            lines.append("- *(No raw partition images or unpacked folders detected in Tree 2 root)*")

    else:
        # Single Tree / Current Directory
        target_scan_dir = t1_base or norm_cwd
        partitions = scan_project_partitions(target_scan_dir)
        lines.append("## 📦 Detected Partitions in this Project")
        if partitions:
            for p in partitions:
                if p["action"]:
                    lines.append(f"- `{p['name']}` ({p['desc']}) ➔ Command: `{p['action']}`")
                else:
                    lines.append(f"- `{p['name']}` ({p['desc']})")
        else:
            lines.append("- *(No raw partition images or unpacked partition folders detected in root)*")
            lines.append("- Place `.img` files here or specify relative/absolute paths with `rak`.")

    lines.extend([
        "",
        "## 🛠️ Unified `rak` Command Quick Reference",
        "- `rak unpack <file>` : Smart auto-unpack partition image or archive (EROFS, EXT4, SUPER, SPARSE, BOOT, PAYLOAD, etc.).",
        "- `rak repack <folder>` : Smart auto-repack directory to partition image.",
        "- `rak img ls <file.img> [subpath]` : List files, permissions & SELinux inside image without unpack.",
        "- `rak img cat <file.img> <path>` : Read file content directly from image to stdout.",
        "- `rak img write <file.img> <path> <file>` : Write/edit file directly into image in-place.",
        "- `rak img extract <file.img> <path> [dest]` : Extract single file or subtree from image.",
        "- `rak fs ls [folder]` / `rak fs cat <file>` / `rak fs grep <regex> [path]` : Read active NeoBranch project files.",
        "- `rak fs copy <source> <destination-folder>` : Safe project-tree copy (NeoBranch-aware).",
        "- `rak fs move <source> <destination-folder>` : Safe project-tree move (NeoBranch-aware).",
        "- `rak fs delete <path> --yes` : Safe queued project-tree delete.",
        "- `rak fs mkdir <parent-folder> <name>` / `rak fs touch <parent-folder> <name>` : Create folder/file safely.",
        "- `rak fs rename <path> <new-name> [--force]` : Project-tree rename with filesystem metadata remapping.",
        "- `rak fs write <file> --from <input>` / `rak fs replace <file> <old> <new>` : Write or edit text through NeoBranch.",
        "- `rak fs symlink <parent-folder> <name> <android-target>` : Create Android symlink safely.",
        "- `rak fs make-filesystem <partition-folder> --type <erofs|ext4> --yes` : Create partition filesystem.",
        "- `rak mount <file.img>` : Mount image virtually into Kitchen GUI FileTree.",
        "- `rak unmount <file.img>` : Unmount virtual image from GUI.",
        "- `rak compare <dir_a> <dir_b>` : Compare the active NeoBranch view of each partition (gated by developer server).",
        "- `rak automation <file.renxec> [fs]` : Execute automation on the selected partition's active NeoBranch or source (gated by developer server). Specify [fs] when the project has multiple partitions.",
        "- `rak list` : View all available Kitchen actions.",
        "- `rak refresh` : Synchronize Kitchen GUI with filesystem.",
        "- `rak badge <path>` : Inspect live filesystem type badge.",
        "- `rak batch unpack <path1> | <path2>` : Run batch auto-unpack on multiple files.",
        "- `rak batch repack <path1> | <path2>` : Run batch auto-repack on multiple folders.",
        "- `rak doc` : Refresh dynamic agent documentation in current folder.",
        END_MARKER,
    ])

    return "\n".join(lines)


def update_agent_file(target_file_path: str, cwd: str) -> bool:

    new_block = generate_dynamic_instructions(cwd)

    if not os.path.exists(target_file_path):
        # File does not exist: create it cleanly with the block
        try:
            with open(target_file_path, "w", encoding="utf-8") as f:
                f.write(new_block + "\n")
            return True
        except Exception as e:
            print(f"[RAK Agent Docs] Error creating {target_file_path}: {e}")
            return False

    # File exists: search line by line for delimiters
    try:
        with open(target_file_path, "r", encoding="utf-8") as f:
            content = f.read()
    except Exception as e:
        print(f"[RAK Agent Docs] Error reading {target_file_path}: {e}")
        return False

    # Determine newline format (\r\n or \n)
    newline = "\r\n" if "\r\n" in content else "\n"
    lines = content.splitlines(keepends=True)

    start_idx = None
    end_idx = None

    for idx, line in enumerate(lines):
        if is_start_delimiter(line) and start_idx is None:
            start_idx = idx
        elif is_end_delimiter(line) and start_idx is not None:
            end_idx = idx
            break

    if start_idx is not None and end_idx is not None:
        # Delimiters found: replace ONLY the block from start_idx to end_idx
        block_lines = [line + newline for line in new_block.splitlines()]
        new_lines = lines[:start_idx] + block_lines + lines[end_idx + 1:]
        final_content = "".join(new_lines)
    elif start_idx is not None and end_idx is None:
        # Start marker found but end marker missing: replace from start_idx to EOF
        block_lines = [line + newline for line in new_block.splitlines()]
        new_lines = lines[:start_idx] + block_lines + [newline]
        final_content = "".join(new_lines)
    else:
        # Delimiters not present: prepend the managed block to preserve user content
        final_content = new_block + newline + newline + content

    try:
        with open(target_file_path, "w", encoding="utf-8") as f:
            f.write(final_content)
        return True
    except Exception as e:
        print(f"[RAK Agent Docs] Error writing {target_file_path}: {e}")
        return False


def update_all_agent_files(cwd: Optional[str] = None) -> List[str]:

    all_dirs = get_all_workspace_dirs(cwd)
    updated = []

    for d in all_dirs:
        for filename in ("CLAUDE.md", "AGENTS.md"):
            path = os.path.join(d, filename)
            if update_agent_file(path, d):
                updated.append(os.path.normpath(path))
    return updated


# Regex matching AI Agent command triggers (handles npx, paths, prefixes, suffixes)
_AGENT_REGEX = re.compile(
    r'(?:\b|[\/\\@])(claude(?:-code)?|gemini|codex|aider|copilot|amp|cursor|windsurf|qwen|deepseek|ollama|agent)\b',
    re.IGNORECASE
)


def handle_terminal_input(line: str, cwd: Optional[str] = None, extra_line: str = ""):

    candidates = [l for l in (line, extra_line) if l and l.strip()]
    if not candidates:
        return

    targets = set()
    for text in candidates:
        m = _AGENT_REGEX.search(text)
        if m:
            trigger = m.group(1).lower()
            if trigger in ("claude", "claude-code"):
                targets.update(["CLAUDE.md", "AGENTS.md"])
            elif trigger == "gemini":
                targets.update(["GEMINI.md", "AGENTS.md", "CLAUDE.md"])
            elif trigger == "codex":
                targets.update(["CODEX.md", "AGENTS.md", "CLAUDE.md"])
            else:
                targets.update(["AGENTS.md", "CLAUDE.md"])

    if not targets:
        return

    all_dirs = get_all_workspace_dirs(cwd)
    updated_paths = []
    for d in all_dirs:
        for target in targets:
            target_path = os.path.join(d, target)
            if update_agent_file(target_path, d):
                updated_paths.append(target_path)

    if updated_paths:
        dirs_str = ", ".join(all_dirs)
        print(f"[RAK Terminal] Synchronized {' & '.join(sorted(targets))} in {dirs_str}", flush=True)
