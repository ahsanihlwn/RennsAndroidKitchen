# SPDX-License-Identifier: MPL-2.0
# RAK_NATIVE_BACKEND accepts auto, python, or rust.
from collections import Counter
from functools import lru_cache
import os
import threading
import json

_calls = Counter()
_calls_lock = threading.Lock()


@lru_cache(maxsize=1)
def _load_native():
    try:
        import rak_native
    except (ImportError, OSError) as error:
        return None, str(error)
    if getattr(rak_native, "API_VERSION", None) != 3:
        return None, "rak_native API version does not match this application"
    return rak_native, None


def _get_native():
    mode = os.environ.get("RAK_NATIVE_BACKEND", "auto").lower()
    if mode not in ("auto", "python", "rust"):
        raise ValueError("RAK_NATIVE_BACKEND must be auto, python, or rust")
    if mode == "python":
        return None
    module, reason = _load_native()
    if module is None and mode == "rust":
        raise RuntimeError(f"Rust backend unavailable: {reason}. Run python tools/build_native.py")
    return module


def _record(operation):
    with _calls_lock:
        _calls[operation] += 1


def diagnostics():
    module = _get_native()
    with _calls_lock:
        calls = dict(_calls)
    return {"backend": "rust" if module else "python",
            "version": getattr(module, "__version__", None), "native_calls": calls}


def parse_metadata_files(paths, kind, *, ignore_errors=False):

    if kind not in ("contexts", "fs_config"):
        raise ValueError("kind must be contexts or fs_config")
    paths = [os.fspath(path) for path in paths]
    module = _get_native()
    if module is not None:
        result = module.parse_metadata(paths, kind, ignore_errors)
        _record(f"parse_{kind}")
        return result
    values = {}
    for path in paths:
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as source:
                for line in source:
                    text = line.strip()
                    if not text or text.startswith("#"):
                        continue
                    fields = text.split()
                    column = 3 if kind == "fs_config" else 1
                    if len(fields) > column:
                        values[fields[0]] = fields[column]
        except OSError:
            if not ignore_errors:
                raise
    return values


def prescan_json(base, *, recursive=False, full=True):

    module = _get_native()
    if module is None:
        return None
    result = module.prescan_json(os.path.normpath(os.fspath(base)), recursive, full)
    _record("prescan_full" if full else "prescan_fast")
    return result


def insert_config_lines(lines, new_entries, *, skip_existing=False):

    mode = os.environ.get("RAK_NATIVE_BACKEND", "auto").lower()
    module = _get_native()
    if module is not None:
        fn = getattr(module, "config_insert_lines", None)
        if fn is not None:
            _record("config_insert_lines")
            return fn(list(lines), list(new_entries), skip_existing)

    if mode == "rust":
        raise RuntimeError(
            "Rust backend missing config_insert_lines. Run python tools/build_native.py"
        )

    lines_list = list(lines)
    entries_list = list(new_entries)
    existing_keys = set()
    if skip_existing:
        for ln in lines_list:
            s = ln.strip()
            if s and not s.startswith("#"):
                existing_keys.add(s.split()[0])

    final = []
    inserted = set()
    for ln in lines_list:
        s = ln.strip()
        if s and not s.startswith("#"):
            key = s.split()[0]
            for nk, nrest in entries_list:
                if nk < key and nk not in inserted and (not skip_existing or nk not in existing_keys):
                    final.append(f"{nk} {nrest}")
                    inserted.add(nk)
        final.append(ln)

    for nk, nrest in entries_list:
        if nk not in inserted and (not skip_existing or nk not in existing_keys):
            final.append(f"{nk} {nrest}")

    return "\n".join(final) + "\n"


def resolve_symlinks(paths, root):

    paths = [os.path.normpath(os.fspath(path)) for path in paths]
    root = os.path.normpath(os.fspath(root))
    module = _get_native()
    if module is not None:
        result = json.loads(module.symlink_batch_json(paths, root))
        _record("resolve_symlinks")
        return {path: result[path] for path in paths}
    from core.symlink import check_symlink
    cache = {}
    return {path: check_symlink(path, root, target_cache=cache) for path in paths}


def directory_entries(path, root):

    path, root = os.path.normpath(os.fspath(path)), os.path.normpath(os.fspath(root))
    module = _get_native()
    if module is not None:
        result = json.loads(module.directory_entries_json(path, root))
        _record("directory_entries")
        return result
    from core.symlink import check_symlink
    from core.header_magic import check_magic
    cache, result = {}, []
    with os.scandir(path) as entries:
        for entry in entries:
            try:
                is_dir = entry.is_dir(follow_symlinks=False)
                info = None if is_dir else entry.stat(follow_symlinks=False)
                size, stamp = (0, 0) if is_dir else (info.st_size, info.st_mtime_ns)
            except OSError:
                is_dir, size, stamp = False, None, None
            link = check_symlink(entry.path, root, target_cache=cache) if (
                not is_dir and size is not None and size <= 512) else None
            magic = check_magic(entry.path) if not is_dir and entry.name.lower().endswith((".img", ".bin")) else ""
            result.append(dict(name=entry.name, path=entry.path, is_dir=is_dir,
                               size=size, mtime_ns=stamp, symlink=link, magic=magic))
    return sorted(result, key=lambda entry: entry["name"])


def directory_snapshots(paths, previous):

    paths = [os.path.normpath(os.fspath(path)) for path in paths]
    module = _get_native()
    if module is not None:
        result = json.loads(module.directory_snapshots_json(paths, json.dumps(previous)))
        _record("directory_snapshots")
        return result
    snapshots, changed, missing = {}, [], []
    for path in paths:
        try:
            with os.scandir(path) as entries:
                signature = []
                for entry in entries:
                    is_dir = entry.is_dir(follow_symlinks=False)
                    info = None if is_dir else entry.stat(follow_symlinks=False)
                    signature.append([entry.name, is_dir, 0 if is_dir else info.st_size,
                                      0 if is_dir else info.st_mtime_ns])
            signature.sort()
        except OSError:
            missing.append(path)
            continue
        if previous.get(path) != signature:
            changed.append(path)
        snapshots[path] = signature
    return dict(snapshots=snapshots, changed=changed, missing=missing)


def _python_asset_index(directories):
    output = {}
    for directory in directories:
        names, files = [], []
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    try:
                        if entry.is_symlink() and not os.path.exists(entry.path):
                            continue
                        if entry.is_file():
                            files.append(entry.name)
                        names.append(entry.name)
                    except OSError:
                        continue
        except OSError:
            pass
        output[directory] = dict(entries=sorted(names), files=sorted(files))
    return output


def asset_index(directories):

    directories = [os.path.normpath(os.fspath(path)) for path in directories]
    module = _get_native()
    if module is not None:
        result = json.loads(module.asset_index_json(directories))
        _record("asset_index")
        return result
    return _python_asset_index(directories)
