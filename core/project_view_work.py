# SPDX-License-Identifier: MPL-2.0
import os
import shutil
import hashlib


def _copy_view(view, logical, target):
    canonical=view.canonical(logical)
    source, _origin = view._resolve_direct(canonical)
    if not source or os.path.islink(source):
        raise RuntimeError(f"Path project view tidak dapat dimaterialisasi: {logical}")
    os.makedirs(target, exist_ok=True)
    for entry in view.list_dir(logical,_with_physical=True):
        source=entry['_physical']
        if not source or os.path.islink(source):
            raise RuntimeError(f"Symlink tidak didukung oleh project view: {entry['path']}")
        dest = os.path.join(target, entry["name"])
        if entry.get('symlink'):
            shutil.copy2(source,dest)
        elif entry["kind"] == "dir":
            _copy_view(view, entry["path"], dest)
        else:
            shutil.copy2(source, dest)

def _manifest(root):

    result = {}
    if os.path.islink(root):
        raise RuntimeError(f"Symlink tidak didukung oleh project view: {root}")
    if not os.path.isdir(root):
        return result
    for base, dirs, files in os.walk(root):
        for name in dirs + files:
            path = os.path.join(base, name)
            if os.path.islink(path):
                raise RuntimeError(f"Symlink tidak didukung oleh project view: {path}")
            rel = os.path.relpath(path, root).replace(os.sep, "/")
            if os.path.isdir(path):
                result[rel] = ("dir", "")
            elif os.path.isfile(path):
                digest = hashlib.sha256()
                with open(path, "rb") as stream:
                    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                        digest.update(chunk)
                result[rel] = ("file", digest.hexdigest())
            else:
                raise RuntimeError(f"Tipe file tidak didukung: {path}")
    return result

def _branch_signature(manager, fs_name, branch):
    if manager.is_physical_checkout(fs_name):
        from core.neobranch_checkout import _live_manifest
        return (manager._read_state(fs_name).get('active_branch'), _live_manifest(manager,fs_name))
    branch_dir = manager._branch_dir(fs_name, branch)
    with open(os.path.join(branch_dir, "branch.json"), "rb") as stream:
        metadata = hashlib.sha256(stream.read()).digest()
    return (metadata,
            _manifest(manager._branch_fs_dir(fs_name, branch)),
            _manifest(manager._branch_config_dir(fs_name, branch)))
