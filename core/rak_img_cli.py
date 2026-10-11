# SPDX-License-Identifier: MPL-2.0
import os
import sys
import json
import base64
from typing import Optional, Tuple, List, Dict, Any

_this_dir = os.path.dirname(os.path.abspath(__file__))
_proj_root = os.path.dirname(_this_dir)
if _proj_root not in sys.path:
    sys.path.insert(0, _proj_root)

from core import imgfs
from core.rak_neobranch_view import (
    ActiveProjectView, active_branch_for_path, project_root, source_image_branch,
)


def _format_size(size_bytes: int) -> str:
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"


def _notify_gui_refresh(img_path: str, internal_path: str = "", action: str = ""):

    try:
        from core.rak_cli import _call_ipc_server
        _call_ipc_server(f"refresh")
    except Exception:
        pass


def _resolve_img_path(raw_path: str, cwd: str) -> str:
    if not raw_path:
        return ""
    if os.path.isabs(raw_path):
        return os.path.normpath(raw_path)
    return os.path.normpath(os.path.join(cwd, raw_path))


def _project_path(raw_path: str, cwd: str):

    logical = _resolve_img_path(raw_path, cwd)
    try:
        root = project_root(logical)
    except ValueError:
        return logical, None
    return logical, ActiveProjectView(root)


def _read_host_path(raw_path: str, cwd: str) -> str:

    logical, view = _project_path(raw_path, cwd)
    if view is None:
        return logical
    physical, _ = view.resolve(logical)
    return physical or ""


def _active_branch_at(raw_path: str, cwd: str) -> str:
    return active_branch_for_path(_resolve_img_path(raw_path, cwd))


def cmd_ls(args: List[str], cwd: str) -> Tuple[bool, str]:
    flags = {a.lower() for a in args if a.startswith("-")}
    pos = [a for a in args if not a.startswith("-")]

    if not pos:
        return False, "usage: rak img ls <img_path> [subpath] [-l] [-r] [--json]"

    img_path = _read_host_path(pos[0], cwd)
    if not os.path.isfile(img_path):
        return False, f"Image file not found: {pos[0]}"

    fs = imgfs.detect_fs(img_path)
    if not fs:
        return False, f"Unsupported filesystem magic or not an EXT/EROFS image: {pos[0]}"

    target_sub = pos[1].strip("/").replace("\\", "/") if len(pos) > 1 else ""
    is_json = "--json" in flags
    is_long = "-l" in flags or "--long" in flags
    is_recursive = "-r" in flags or "-R" in flags or "--recursive" in flags

    try:
        entries = imgfs.list_image(img_path)
    except Exception as e:
        return False, f"Failed to read image '{os.path.basename(img_path)}': {e}"

    # Filter by subpath
    filtered = []
    for e in entries:
        p = e["path"].strip("/").replace("\\", "/")
        if not p:
            continue

        if target_sub:
            if p == target_sub:
                continue
            if not p.startswith(target_sub + "/"):
                continue
            rel = p[len(target_sub) + 1:]
        else:
            rel = p

        if not is_recursive and "/" in rel:
            continue

        filtered.append(e)

    filtered.sort(key=lambda x: (not x.get("is_dir", False), x.get("path", "").lower()))

    if is_json:
        return True, json.dumps(filtered, indent=2)

    lines = []
    if not filtered:
        lines.append(f"(Directory empty or subpath not found: '{target_sub}')")
    else:
        # Header
        lines.append(f"{'TYPE':<6} {'PERM':<11} {'SIZE':>10}  {'SELINUX':<32} {'PATH'}")
        lines.append("-" * 80)
        for e in filtered:
            is_dir = e.get("is_dir", False)
            is_link = e.get("is_link", False)
            t_str = "[DIR]" if is_dir else ("[LINK]" if is_link else "[FILE]")
            perm = e.get("perm", "----------")
            size_str = _format_size(e.get("size", 0)) if not is_dir else "-"
            selinux = e.get("selinux") or "-"
            p_display = e.get("path", "")
            if is_link and e.get("target"):
                p_display += f" -> {e['target']}"
            lines.append(f"{t_str:<6} {perm:<11} {size_str:>10}  {selinux:<32} {p_display}")

    return True, "\n".join(lines)


def cmd_cat(args: List[str], cwd: str) -> Tuple[bool, str]:
    if len(args) < 2:
        return False, "usage: rak img cat <img_path> <internal_path>"

    img_path = _read_host_path(args[0], cwd)
    if not os.path.isfile(img_path):
        return False, f"Image file not found: {args[0]}"

    internal_path = args[1].strip("/").replace("\\", "/")
    try:
        data, node = imgfs.read_path(img_path, internal_path)
    except FileNotFoundError:
        return False, f"File not found in image: '{internal_path}'"
    except Exception as e:
        return False, f"Failed to read '{internal_path}': {e}"

    if getattr(node, "is_dir", False):
        return False, f"'{internal_path}' is a directory, cannot cat."

    try:
        sys.stdout.buffer.write(data)
        sys.stdout.buffer.flush()
    except Exception as e:
        return False, f"Stdout write error: {e}"

    return True, ""


def cmd_stat(args: List[str], cwd: str) -> Tuple[bool, str]:
    flags = {a.lower() for a in args if a.startswith("-")}
    pos = [a for a in args if not a.startswith("-")]

    if len(pos) < 2:
        return False, "usage: rak img stat <img_path> <internal_path> [--json]"

    img_path = _read_host_path(pos[0], cwd)
    if not os.path.isfile(img_path):
        return False, f"Image file not found: {pos[0]}"

    internal_path = pos[1].strip("/").replace("\\", "/")
    try:
        _, node = imgfs.read_path(img_path, internal_path)
    except FileNotFoundError:
        return False, f"Path not found in image: '{internal_path}'"
    except Exception as e:
        return False, f"Failed to stat '{internal_path}': {e}"

    is_json = "--json" in flags
    if is_json:
        return True, json.dumps(node.to_dict(), indent=2)

    kind = "Directory" if node.is_dir else ("Symbolic Link" if node.is_link else "Regular File")
    lines = [
        f"Path:       {node.path}",
        f"Image:      {os.path.basename(img_path)}",
        f"Type:       {kind}",
        f"Size:       {node.size} bytes ({_format_size(node.size)})",
        f"Mode:       {oct(node.mode & 0o7777)} ({node.perm})",
        f"Inode:      {node.ino}",
        f"SELinux:    {node.selinux or '(none)'}",
    ]
    if node.is_link:
        lines.append(f"Target:     {node.target}")

    return True, "\n".join(lines)


def cmd_extract(args: List[str], cwd: str) -> Tuple[bool, str]:
    # Bentuk batch eksplisit. Legacy tetap: extract <img> <path> [dest].
    # Contoh: extract system.img --to pulled --path etc/a --path etc/b
    if "--path" in args or "-p" in args:
        return _cmd_extract_many(args, cwd)
    if len(args) < 2:
        return False, "usage: rak img extract <img_path> <internal_path> [dest_path]"

    img_path = _read_host_path(args[0], cwd)
    if not os.path.isfile(img_path):
        return False, f"Image file not found: {args[0]}"

    internal_path = args[1].strip("/").replace("\\", "/")
    dest = args[2] if len(args) > 2 else ""

    # Check if target is a single file or a directory
    try:
        entries = imgfs.list_image(img_path)
    except Exception as e:
        return False, f"Failed to read image: {e}"

    # Check for exact file match
    exact_file = next((e for e in entries if e["path"] == internal_path and not e["is_dir"]), None)

    if exact_file:
        # Extract single file
        dest_file = dest
        if not dest_file:
            dest_file = os.path.basename(internal_path)
        if not os.path.isabs(dest_file):
            dest_file = os.path.normpath(os.path.join(cwd, dest_file))

        if os.path.isdir(dest_file):
            dest_file = os.path.join(dest_file, os.path.basename(internal_path))

        os.makedirs(os.path.dirname(os.path.abspath(dest_file)), exist_ok=True)
        try:
            data, _ = imgfs.read_path(img_path, internal_path)
            with open(dest_file, "wb") as f:
                f.write(data)
            return True, f"Extracted '{internal_path}' -> '{dest_file}' ({_format_size(len(data))})"
        except Exception as e:
            return False, f"Extraction failed: {e}"

    # Extract subtree or entire image
    dest_dir = dest
    if not dest_dir:
        base_name = os.path.splitext(os.path.basename(img_path))[0]
        dest_dir = os.path.join(cwd, f"{base_name}_{internal_path.replace('/', '_')}" if internal_path else base_name)
    if not os.path.isabs(dest_dir):
        dest_dir = os.path.normpath(os.path.join(cwd, dest_dir))

    os.makedirs(dest_dir, exist_ok=True)
    count = 0
    total_bytes = 0
    failures = []

    prefix = (internal_path + "/") if internal_path else ""
    for e in entries:
        p = e["path"]
        if internal_path and p != internal_path and not p.startswith(prefix):
            continue

        rel = p[len(prefix):] if internal_path else p
        if not rel:
            continue

        target_out = os.path.join(dest_dir, rel.replace("/", os.sep))
        if e["is_dir"]:
            os.makedirs(target_out, exist_ok=True)
        elif not e["is_link"]:
            os.makedirs(os.path.dirname(target_out), exist_ok=True)
            try:
                data, _ = imgfs.read_path(img_path, p)
                with open(target_out, "wb") as f:
                    f.write(data)
                count += 1
                total_bytes += len(data)
            except Exception as ex:
                failures.append(f"{p}: {type(ex).__name__}: {ex}")

    message = f"Extracted {count} files ({_format_size(total_bytes)}) to '{dest_dir}'"
    if failures:
        return False, message + "\nFailed:\n" + "\n".join(failures)
    return True, message


def _cmd_extract_many(args: List[str], cwd: str) -> Tuple[bool, str]:

    if not args:
        return False, "usage: rak img extract <img> --to <folder> --path <path> [--path <path> ...]"
    img_path = _read_host_path(args[0], cwd)
    if not os.path.isfile(img_path):
        return False, f"Image file not found: {args[0]}"

    dest = ""
    paths = []
    i = 1
    while i < len(args):
        token = args[i]
        if token in ("--to", "-o"):
            i += 1
            if i >= len(args):
                return False, "--to requires a destination folder"
            dest = args[i]
        elif token in ("--path", "-p"):
            i += 1
            if i >= len(args):
                return False, f"{token} requires an internal image path"
            paths.append(args[i].strip("/").replace("\\", "/"))
        else:
            return False, f"unknown extract option: {token}"
        i += 1

    if not dest or not paths:
        return False, "usage: rak img extract <img> --to <folder> --path <path> [--path <path> ...]"
    dest = _resolve_img_path(dest, cwd)

    # Jika folder sudah dipilih, anaknya tidak diekstrak ulang; ini mencegah
    # overwrite/metadata ganda saat multi-select membawa parent dan child.
    unique = []
    for p in dict.fromkeys(paths):
        if "" in unique:
            continue
        if not p:
            unique = [""]
            continue
        if any(parent and p.startswith(parent + "/") for parent in unique):
            continue
        unique = [old for old in unique if not (p and old.startswith(p + "/"))]
        unique.append(p)
    names = [p.rsplit("/", 1)[-1] if p else os.path.splitext(os.path.basename(img_path))[0]
             for p in unique]
    if len({n.lower() for n in names}) != len(names):
        return False, "selected paths have colliding output names; extract them to separate folders"

    results, failures = [], []
    try:
        os.makedirs(dest, exist_ok=True)
        for p in unique:
            try:
                results.append(imgfs.extract_path(img_path, p, dest))
            except Exception as ex:
                failures.append(f"{p or '/'}: {type(ex).__name__}: {ex}")
    except Exception as ex:
        return False, f"Failed to prepare extraction: {ex}"

    files = sum(x.get("files", 0) for x in results)
    total = sum(x.get("bytes", 0) for x in results)
    message = f"Extracted {len(results)}/{len(unique)} selected paths ({files} files, {_format_size(total)}) to '{dest}'"
    if failures:
        return False, message + "\nFailed:\n" + "\n".join(failures)
    return True, message


def cmd_write(args: List[str], cwd: str, main_ref=None) -> Tuple[bool, str]:
    if len(args) < 3:
        return False, "usage: rak img write <img_path> <internal_path> <local_file|->"

    img_path = _read_host_path(args[0], cwd)
    if not os.path.isfile(img_path):
        return False, f"Image file not found: {args[0]}"

    internal_path = args[1].strip("/").replace("\\", "/")
    src_file = args[2]

    if src_file == "-":
        try:
            new_data = sys.stdin.buffer.read()
        except Exception as e:
            return False, f"Failed to read from stdin: {e}"
    else:
        norm_src = _read_host_path(src_file, cwd)
        if not os.path.isfile(norm_src):
            return False, f"Source local file not found: {src_file}"
        try:
            with open(norm_src, "rb") as f:
                new_data = f.read()
        except Exception as e:
            return False, f"Failed to read '{src_file}': {e}"

    try:
        new_size = imgfs.write_path_data(img_path, internal_path, new_data)
    except FileNotFoundError:
        return False, f"Target file '{internal_path}' does not exist inside image '{os.path.basename(img_path)}'."
    except IsADirectoryError:
        return False, f"'{internal_path}' is a directory inside the image."
    except ValueError as e:
        return False, f"Write rejected: {e}"
    except Exception as e:
        return False, f"Failed to write into image: {e}"

    # Sync GUI if live
    _notify_gui_refresh(img_path, internal_path, action="write")

    return True, f"Successfully wrote {len(new_data)} bytes to '{internal_path}' in '{os.path.basename(img_path)}' (new size: {new_size} B)"


def cmd_patch(args: List[str], cwd: str, main_ref=None) -> Tuple[bool, str]:

    if len(args) != 2:
        return False, "usage: rak img patch <img_path> <manifest.json>"
    img_path = _read_host_path(args[0], cwd)
    manifest_path = _read_host_path(args[1], cwd)
    if not os.path.isfile(img_path):
        return False, f"Image file not found: {args[0]}"
    if not os.path.isfile(manifest_path):
        return False, f"Patch manifest not found: {args[1]}"
    try:
        with open(manifest_path, "r", encoding="utf-8") as fh:
            manifest = json.load(fh)
        items = manifest.get("files") if isinstance(manifest, dict) else manifest
        if not isinstance(items, list) or not items:
            raise ValueError("manifest must be a non-empty JSON list or {\"files\": [...]} ")
    except Exception as ex:
        return False, f"Invalid patch manifest: {ex}"

    prepared = []
    written = []
    try:
        with imgfs.image_write_lock(img_path):


            for index, item in enumerate(items, 1):
                if not isinstance(item, dict):
                    raise ValueError(f"item #{index} must be an object")
                internal = str(item.get("path", "")).strip("/").replace("\\", "/")
                if not internal:
                    raise ValueError(f"item #{index}: missing path")
                sources = [key for key in ("source", "content", "base64", "replacements") if key in item]
                if len(sources) != 1:
                    raise ValueError(f"{internal}: specify exactly one of source/content/base64/replacements")
                key = sources[0]
                if key == "source":
                    source = _read_host_path(str(item["source"]), cwd)
                    if not os.path.isfile(source):
                        raise FileNotFoundError(f"{internal}: source not found: {item['source']}")
                    with open(source, "rb") as fh:
                        data = fh.read()
                elif key == "base64":
                    data = base64.b64decode(str(item["base64"]), validate=True)
                elif key == "content":
                    encoding = str(item.get("encoding", "utf-8"))
                    data = str(item["content"]).encode(encoding)
                else:
                    old, node = imgfs.read_path(img_path, internal)
                    if node.is_dir or node.is_link:
                        raise IsADirectoryError(internal)
                    encoding = str(item.get("encoding", "utf-8"))
                    text = old.decode(encoding)
                    replacements = item["replacements"]
                    if not isinstance(replacements, list) or not replacements:
                        raise ValueError(f"{internal}: replacements must be a non-empty list")
                    for replacement in replacements:
                        if not isinstance(replacement, dict) or "find" not in replacement:
                            raise ValueError(f"{internal}: every replacement needs 'find'")
                        find = str(replacement["find"])
                        repl = str(replacement.get("replace", ""))
                        count = int(replacement.get("count", -1))
                        if not find:
                            raise ValueError(f"{internal}: empty 'find' is not allowed")
                        hits = text.count(find)
                        if not hits and replacement.get("required", True):
                            raise ValueError(f"{internal}: text not found: {find!r}")
                        text = text.replace(find, repl, count if count >= 0 else -1)
                    data = text.encode(encoding)
                prepared.append((internal, data))

            duplicate_paths = {p for p, _data in prepared
                               if sum(1 for item, _ in prepared if item == p) > 1}
            if duplicate_paths:
                raise ValueError(
                    "path muncul lebih dari sekali; gabungkan semua replacements "
                    "untuk satu file: " + ", ".join(sorted(duplicate_paths)))


            for internal, data in prepared:
                imgfs.preflight_write_path_data(img_path, internal, data)

            for internal, data in prepared:
                new_size = imgfs.write_path_data(img_path, internal, data)
                written.append((internal, new_size))
    except Exception as ex:
        prefix = (f" after writing {len(written)}/{len(prepared)} item(s)"
                  if prepared else "")
        return False, f"Patch failed{prefix}: {type(ex).__name__}: {ex}"

    _notify_gui_refresh(img_path, action="patch")
    return True, f"Patched {len(written)} file(s) in '{os.path.basename(img_path)}': " + \
                 ", ".join(f"{p} ({n} B)" for p, n in written)


def cmd_perm(args: List[str], cwd: str, main_ref=None) -> Tuple[bool, str]:
    if len(args) < 3:
        return False, "usage: rak img perm <img_path> <internal_path> <octal>"

    img_path = _read_host_path(args[0], cwd)
    if not os.path.isfile(img_path):
        return False, f"Image file not found: {args[0]}"

    internal_path = args[1].strip("/").replace("\\", "/")
    octal_str = args[2]

    try:
        new_mode = imgfs.set_perm(img_path, internal_path, octal_str)
    except FileNotFoundError:
        return False, f"Path '{internal_path}' not found in image."
    except Exception as e:
        return False, f"Failed to set permission: {e}"

    _notify_gui_refresh(img_path, internal_path, action="perm")
    return True, f"Changed mode of '{internal_path}' to {oct(new_mode & 0o7777)} ({imgfs.mode_to_str(new_mode)})"


def cmd_selinux(args: List[str], cwd: str, main_ref=None) -> Tuple[bool, str]:
    if len(args) < 3:
        return False, "usage: rak img selinux <img_path> <internal_path> <context>"

    img_path = _read_host_path(args[0], cwd)
    if not os.path.isfile(img_path):
        return False, f"Image file not found: {args[0]}"

    internal_path = args[1].strip("/").replace("\\", "/")
    ctx = args[2]

    try:
        new_ctx = imgfs.set_selinux(img_path, internal_path, ctx)
    except FileNotFoundError:
        return False, f"Path '{internal_path}' not found in image."
    except ValueError as e:
        return False, f"SELinux update rejected: {e}"
    except Exception as e:
        return False, f"Failed to set SELinux context: {e}"

    _notify_gui_refresh(img_path, internal_path, action="selinux")
    return True, f"Changed SELinux context of '{internal_path}' to '{new_ctx}'"


def cmd_rm(args: List[str], cwd: str, main_ref=None) -> Tuple[bool, str]:
    flags = {a.lower() for a in args if a.startswith("-")}
    pos = [a for a in args if not a.startswith("-")]

    if len(pos) < 2:
        return False, "usage: rak img rm <img_path> <internal_path> [-r]"

    img_path = _read_host_path(pos[0], cwd)
    if not os.path.isfile(img_path):
        return False, f"Image file not found: {pos[0]}"

    internal_path = pos[1].strip("/").replace("\\", "/")
    recursive = "-r" in flags or "-R" in flags or "--recursive" in flags or "-f" in flags

    try:
        res = imgfs.delete_path(img_path, internal_path, recursive=recursive)
    except FileNotFoundError:
        return False, f"Path '{internal_path}' not found in image."
    except ValueError as e:
        return False, f"Delete failed: {e}"
    except Exception as e:
        return False, f"Delete error: {e}"

    _notify_gui_refresh(img_path, internal_path, action="rm")
    return True, f"Deleted '{internal_path}' from '{os.path.basename(img_path)}' (mode: {res.get('mode')})"


def cmd_info(args: List[str], cwd: str) -> Tuple[bool, str]:
    if not args:
        return False, "usage: rak img info <img_path>"

    img_path = _read_host_path(args[0], cwd)
    if not os.path.isfile(img_path):
        return False, f"Image file not found: {args[0]}"

    try:
        rep = imgfs.usage_report(img_path)
    except Exception as e:
        return False, f"Failed to inspect usage for '{os.path.basename(img_path)}': {e}"

    fs = rep.get("fs", "Unknown")
    file_sz = rep.get("file_size", os.path.getsize(img_path))
    bs = rep.get("block_size", 4096)
    tot_blk = rep.get("total_blocks", 0)
    used_blk = rep.get("used_blocks", 0)
    free_blk = rep.get("free_blocks", 0)
    wasted = rep.get("wasted_bytes", 0)
    files = rep.get("files", 0)
    dirs = rep.get("dirs", 0)
    links = rep.get("links", 0)

    lines = [
        f"Image:       {os.path.basename(img_path)}",
        f"Filesystem:  {fs}",
        f"Image Size:  {_format_size(file_sz)} ({file_sz:,} bytes)",
        f"Block Size:  {bs} bytes",
        f"Blocks:      {tot_blk:,} total | {used_blk:,} used | {free_blk:,} free",
        f"Nodes:       {files:,} files | {dirs:,} dirs | {links:,} symlinks",
    ]
    if wasted > 0:
        lines.append(f"Wasted Space:{_format_size(wasted)} (unreferenced blocks from deleted files; run repack or compact to reclaim)")

    return True, "\n".join(lines)


def cmd_mounted(main_ref=None) -> Tuple[bool, str]:

    if main_ref is None:
        try:
            from core import rak_cli
            main_ref = getattr(rak_cli, "_main_ref", None)
        except Exception:
            pass

    tree = getattr(main_ref, "active_tree", None) if main_ref else None
    if not tree or not hasattr(tree, "_mounts"):
        return False, "RAK Kitchen GUI is not currently running. No active virtual mounts in GUI."

    mounts = list(tree._mounts.keys())
    if not mounts:
        return True, "No images are currently mounted in the Kitchen GUI."

    lines = ["Mounted Images in Kitchen GUI:"]
    for m in mounts:
        lines.append(f"  - {m}")
    return True, "\n".join(lines)


def run_img_cli(args: List[str], cwd: Optional[str] = None, main_ref=None) -> Tuple[bool, str]:

    if not args:
        return False, (
            "usage: rak img <subcommand> [arguments...]\n"
            "Subcommands:\n"
            "  ls <img_path> [subpath] [-l] [-r] [--json] : List directory inside image\n"
            "  cat <img_path> <internal_path>              : Read file content from image\n"
            "  stat <img_path> <internal_path> [--json]    : Show file metadata in image\n"
            "  extract <img_path> <path> [dest]   (pull)   : Extract file or folder from image\n"
            "  extract <img> --to <folder> --path <path> [--path <path> ...] : Extract several paths\n"
            "  write <img_path> <path> <file|->   (push)   : Write file directly into image\n"
            "  patch <img_path> <manifest.json>           : Apply several file/text changes\n"
            "  perm <img_path> <path> <octal>     (chmod)  : Change file permission mode\n"
            "  selinux <img_path> <path> <context>(chcon)  : Change SELinux context\n"
            "  rm <img_path> <path> [-r]          (del)    : Delete file or directory in image\n"
            "  info <img_path>                    (usage)  : Show filesystem & block usage info\n"
            "  mounted                            (mounts) : List images mounted in GUI"
        )

    subcmd = args[0].lower()
    subargs = args[1:]
    work_dir = cwd or os.getcwd()

    image_commands = {"ls", "cat", "stat", "extract", "pull", "write", "push",
                      "patch", "batch-write", "write-batch", "perm", "chmod",
                      "selinux", "chcon", "rm", "del", "info", "usage"}
    mutating_commands = {"write", "push", "patch", "batch-write", "write-batch",
                         "perm", "chmod", "selinux", "chcon", "rm", "del"}
    if subcmd in image_commands and subargs:
        try:
            logical_image = _resolve_img_path(subargs[0], work_dir)
            source_branch = source_image_branch(logical_image)
            if source_branch:
                stem = os.path.splitext(os.path.basename(logical_image))[0]
                return False, (f"{os.path.basename(logical_image)} is a source image for active "
                               f"NeoBranch '{source_branch}'. Use 'rak fs' on the unpacked {stem}/ tree "
                               "until the branch is repacked.")
            branch = _active_branch_at(subargs[0], work_dir)
            if branch and subcmd in mutating_commands:
                return False, (f"Image is in active NeoBranch '{branch}'. Direct image edits "
                               "are unsupported; use RAK filesystem actions or switch to source.")
            if subcmd in ("extract", "pull"):
                if "--path" in subargs or "-p" in subargs:
                    dest = next((subargs[i + 1] for i, token in enumerate(subargs[:-1])
                                 if token in ("--to", "-o")), work_dir)
                else:
                    dest = subargs[2] if len(subargs) > 2 else work_dir
                dest_branch = _active_branch_at(dest, work_dir)
                if dest_branch:
                    return False, (f"Extraction destination is in active NeoBranch '{dest_branch}'. "
                                   "Extract outside the ROM project, then use RAK filesystem actions to import it.")
        except ValueError as exc:
            return False, str(exc)

    if subcmd == "ls":
        return cmd_ls(subargs, work_dir)
    elif subcmd == "cat":
        return cmd_cat(subargs, work_dir)
    elif subcmd == "stat":
        return cmd_stat(subargs, work_dir)
    elif subcmd in ("extract", "pull"):
        return cmd_extract(subargs, work_dir)
    elif subcmd in ("write", "push"):
        return cmd_write(subargs, work_dir, main_ref=main_ref)
    elif subcmd in ("patch", "batch-write", "write-batch"):
        return cmd_patch(subargs, work_dir, main_ref=main_ref)
    elif subcmd in ("perm", "chmod"):
        return cmd_perm(subargs, work_dir, main_ref=main_ref)
    elif subcmd in ("selinux", "chcon"):
        return cmd_selinux(subargs, work_dir, main_ref=main_ref)
    elif subcmd in ("rm", "unlink", "del"):
        return cmd_rm(subargs, work_dir, main_ref=main_ref)
    elif subcmd in ("info", "usage"):
        return cmd_info(subargs, work_dir)
    elif subcmd in ("mounted", "mounts"):
        return cmd_mounted(main_ref=main_ref)
    else:
        return False, f"Unknown img subcommand: '{subcmd}'. Run 'rak img' for help."
