# SPDX-License-Identifier: MPL-2.0
import os, json, shutil, datetime, sys, tempfile, threading, stat, time
from functools import wraps
from core.app_paths import (
    neocat_cache_dir,
    neocat_snapshot_dir,
    remove_neocat_branch_cache,
)


NB_STORE_DIR = "neobranch"


def _hide_dir(path: str):

    if not os.path.isdir(path):
        return
    if sys.platform == "win32":
        try:
            import subprocess
            from core.proc_util import CREATE_NO_WINDOW
            subprocess.call(
                ["attrib", "+h", path],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=CREATE_NO_WINDOW,
            )
        except Exception as e:
            print(f"[NeoBranch] _hide_dir warning: {e}")


BRANCH_COLORS = {
    "red": "#e05555", "blue": "#4d8ef0", "yellow": "#f0c040",
    "green": "#4db860", "orange": "#f08030", "purple": "#9060d0", "pink": "#e070a0",
}

CONFIG_SUFFIXES = ("_file_contexts", "_fs_config", "_fs_options", "_info")
_metadata_lock = threading.RLock()


def _locked_metadata(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        with _metadata_lock:
            return fn(*args, **kwargs)
    return wrapped


def _atomic_json_write(path: str, data: dict):

    parent = os.path.dirname(path)
    os.makedirs(parent, exist_ok=True)
    fd, pending = tempfile.mkstemp(prefix=".neobranch-", suffix=".json", dir=parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        _retry_fs_op(lambda: os.replace(pending, path), path)
    finally:
        if os.path.exists(pending):
            _remove_path(pending)


def _retryable_fs_error(exc):
    return isinstance(exc, PermissionError) or getattr(exc, "winerror", None) in (5, 32, 33)


def _retry_fs_op(operation, path, attempts=6):

    for attempt in range(attempts):
        try:
            return operation()
        except OSError as exc:
            if not _retryable_fs_error(exc) or attempt == attempts - 1:
                raise
            if os.path.lexists(path) and not os.path.islink(path):
                try:
                    os.chmod(path, stat.S_IREAD | stat.S_IWRITE)
                except OSError:
                    pass
            time.sleep(0.08 * (attempt + 1))


def _remove_path(path):
    if not os.path.lexists(path):
        return
    if os.path.isdir(path) and not os.path.islink(path):
        def retry_readonly(func, failed_path, exc_info):
            if not _retryable_fs_error(exc_info[1]):
                raise exc_info[1]
            _retry_fs_op(lambda: func(failed_path), failed_path)

        _retry_fs_op(lambda: shutil.rmtree(path, onerror=retry_readonly), path)
    else:
        _retry_fs_op(lambda: os.remove(path), path)


def _copy2_with_retry(src, dst):

    if not os.path.lexists(dst):
        return _retry_fs_op(lambda: shutil.copy2(src, dst), src)
    parent = os.path.dirname(dst)
    fd, pending = tempfile.mkstemp(prefix=".neobranch-copy-", dir=parent)
    os.close(fd)
    try:
        _retry_fs_op(lambda: shutil.copy2(src, pending), pending)
        _retry_fs_op(lambda: os.replace(pending, dst), dst)
    finally:
        if os.path.lexists(pending):
            _remove_path(pending)
    return dst


class NeoBranchManager:

    def __init__(self, tree_base: str):
        self.tree_base = os.path.normpath(tree_base)
        self._neobranch_root = os.path.join(self.tree_base, NB_STORE_DIR)

    # path helpers

    def _nb_root(self, fs_root):          return os.path.join(self._neobranch_root, fs_root)
    def _source_dir(self, fs_root):       return os.path.join(self._nb_root(fs_root), "source")
    def _source_fs_dir(self, fs_root):    return os.path.join(self._source_dir(fs_root), fs_root)
    def _source_config_dir(self, fs_root):return os.path.join(self._source_dir(fs_root), "config")
    def _source_neocat_config_dir(self, fs_root): return neocat_snapshot_dir(self.tree_base, fs_root)
    def _branch_dir(self, fs_root, b):    return os.path.join(self._nb_root(fs_root), f"branch_{b}")
    def _branch_fs_dir(self, fs_root, b): return os.path.join(self._branch_dir(fs_root, b), fs_root)
    def _branch_config_dir(self, fs_root, b): return os.path.join(self._branch_dir(fs_root, b), "config")
    def _branch_neocat_config_dir(self, fs_root, b): return neocat_cache_dir(self.tree_base, fs_root, b)
    def _branch_json_path(self, fs_root, b):  return os.path.join(self._branch_dir(fs_root, b), "branch.json")

    # create branch

    def create_branch(self, fs_root: str, branch_name: str, color: str) -> dict:

        self._validate_branch_name(branch_name)
        branch_dir = self._branch_dir(fs_root, branch_name)
        if os.path.exists(branch_dir):
            raise ValueError(f"Branch '{branch_name}' already exists for '{fs_root}'.")

        self._ensure_source_snapshot(fs_root)
        _hide_dir(self._neobranch_root)

        os.makedirs(self._branch_fs_dir(fs_root, branch_name), exist_ok=True)
        branch_cfg = self._branch_config_dir(fs_root, branch_name)
        os.makedirs(branch_cfg, exist_ok=True)
        branch_neocat = self._branch_neocat_config_dir(fs_root, branch_name)
        os.makedirs(branch_neocat, exist_ok=True)

        # A branch reads unedited files from the live source tree. Seed its
        # config from that same source revision rather than an older snapshot.
        source_cfg = os.path.join(self.tree_base, "config")
        if os.path.isdir(source_cfg):
            for fname in os.listdir(source_cfg):
                if not any(fname == fs_root + suffix for suffix in CONFIG_SUFFIXES):
                    continue
                src = os.path.join(source_cfg, fname)
                dst = os.path.join(branch_cfg, fname)
                if os.path.isfile(src):
                    shutil.copy2(src, dst)
                    print(f"[NeoBranch] config seeded -> branch_{branch_name}: {fname}")

        source_neocat = neocat_cache_dir(self.tree_base)
        if os.path.isdir(source_neocat):
            for fname in os.listdir(source_neocat):
                if fname != fs_root + "_file_contexts":
                    continue
                src = os.path.join(source_neocat, fname)
                dst = os.path.join(branch_neocat, fname)
                if os.path.isfile(src):
                    shutil.copy2(src, dst)
                    print(f"[NeoBranch] neocat seeded -> branch_{branch_name}: {fname}")

        info = {
            "name": branch_name, "color": color, "fs_root": fs_root,
            "created_at": datetime.datetime.now().isoformat(),
            "edited_files": [], "deleted_paths": [],
        }
        self._write_branch_json(self._branch_json_path(fs_root, branch_name), info)
        return info

    # list / query

    def list_branches(self, fs_root: str) -> list:
        nb_root = self._nb_root(fs_root)
        if not os.path.isdir(nb_root):
            return []
        branches = []
        for entry in sorted(os.listdir(nb_root)):
            if not entry.startswith("branch_"):
                continue
            jp = os.path.join(nb_root, entry, "branch.json")
            if os.path.isfile(jp):
                try:
                    with open(jp, "r", encoding="utf-8") as f:
                        branches.append(json.load(f))
                except Exception:
                    pass
        return branches

    def has_any_branch(self, fs_root: str) -> bool:
        return len(self.list_branches(fs_root)) > 0

    def get_active_branch(self, fs_root: str):
        state = self._read_state(fs_root)
        active = state.get("active_branch")
        if not active:
            return None
        jp = self._branch_json_path(fs_root, active)
        if not os.path.isfile(jp):
            return None
        try:
            with open(jp, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None

    @_locked_metadata
    def set_active_branch(self, fs_root: str, branch_name):
        state = self._read_state(fs_root)
        state["active_branch"] = branch_name
        self._write_state(fs_root, state)

    # config dir routing

    def get_live_config_dir(self, fs_root: str) -> str:

        active = self.get_active_branch(fs_root)
        if active:
            return self._branch_config_dir(fs_root, active["name"])
        return os.path.join(self.tree_base, "config")

    def get_branch_config_path(self, fs_root: str, branch_name: str, config_fname: str):
        p = os.path.join(self._branch_config_dir(fs_root, branch_name), config_fname)
        return p if os.path.isfile(p) else None

    # record edit

    def _normalize_branch_rel(self, fs_root: str, branch_name: str, path) -> str:

        if path is None:
            return ""
        raw = os.fspath(path)
        rel = raw

        try:
            norm = os.path.normpath(raw)
            branch_fs = os.path.normpath(self._branch_fs_dir(fs_root, branch_name))
            branch_dir = os.path.normpath(self._branch_dir(fs_root, branch_name))
            base = os.path.normpath(self.tree_base)

            if os.path.isabs(norm):
                if norm == branch_fs:
                    rel = fs_root
                elif norm.startswith(branch_fs + os.sep):
                    rel = fs_root + "/" + os.path.relpath(norm, branch_fs).replace("\\", "/")
                elif norm == branch_dir:
                    rel = ""
                elif norm.startswith(branch_dir + os.sep):
                    rel = os.path.relpath(norm, branch_dir).replace("\\", "/")
                elif norm == base:
                    rel = ""
                elif norm.startswith(base + os.sep):
                    rel = os.path.relpath(norm, base).replace("\\", "/")
        except Exception:
            rel = raw

        rel = str(rel).replace("\\", "/").strip().strip("/")
        if not rel or rel == ".":
            return ""

        bad_prefix = f"{NB_STORE_DIR}/{fs_root}/branch_{branch_name}/"
        if rel.startswith(bad_prefix):
            rel = rel[len(bad_prefix):]
        elif rel.startswith(f"{NB_STORE_DIR}/"):
            parts = rel.split("/")
            if len(parts) >= 4 and parts[1] == fs_root and parts[2] == f"branch_{branch_name}":
                rel = "/".join(parts[3:])
            else:
                return ""

        # A path such as vendor/../config must not escape this partition when
        # deleted_paths is applied to the real tree during repack.
        rel = os.path.normpath(rel.replace("/", os.sep)).replace("\\", "/").rstrip("/")
        if not (rel == fs_root or rel.startswith(fs_root + "/")):
            return ""
        return rel

    def _sanitize_branch_json(self, fs_root: str, branch_name: str, info: dict, write: bool = False) -> dict:
        changed = False
        now = datetime.datetime.now().isoformat()

        edited = {}
        for entry in info.get("edited_files", []):
            rel = self._normalize_branch_rel(
                fs_root,
                branch_name,
                entry.get("rel_path") or entry.get("abs_branch_path") or "",
            )
            if not rel:
                changed = True
                continue

            preferred = os.path.join(self._branch_dir(fs_root, branch_name), rel.replace("/", os.sep))
            fixed = dict(entry)
            fixed["rel_path"] = rel
            fixed["abs_branch_path"] = os.path.normpath(preferred)
            fixed.setdefault("edited_at", now)
            edited[rel] = fixed
            if fixed != entry:
                changed = True

        deleted = []
        seen_deleted = set()
        for rel_del in info.get("deleted_paths", []):
            rel = self._normalize_branch_rel(fs_root, branch_name, rel_del)
            if not rel:
                changed = True
                continue
            if rel not in seen_deleted:
                deleted.append(rel)
                seen_deleted.add(rel)
            if rel != rel_del:
                changed = True

        if len(edited) != len(info.get("edited_files", [])):
            changed = True
        if deleted != info.get("deleted_paths", []):
            changed = True

        if changed:
            info = dict(info)
            info["edited_files"] = list(edited.values())
            info["deleted_paths"] = deleted
            if write:
                self._write_branch_json(self._branch_json_path(fs_root, branch_name), info)
                print(f"[NeoBranch] sanitized branch metadata: {fs_root}/{branch_name}")
        return info

    @_locked_metadata
    def record_edit(self, fs_root: str, branch_name: str, abs_source_path: str) -> str:

        rel = self._normalize_branch_rel(fs_root, branch_name, abs_source_path)
        if not rel:
            return abs_source_path
        branch_file = os.path.join(self._branch_dir(fs_root, branch_name), rel)
        os.makedirs(os.path.dirname(branch_file), exist_ok=True)
        if not os.path.exists(branch_file):
            try:
                shutil.copy2(abs_source_path, branch_file)
            except Exception as e:
                print(f"[NeoBranch] record_edit copy failed: {e}")

        jp = self._branch_json_path(fs_root, branch_name)
        info = self._sanitize_branch_json(fs_root, branch_name, self._read_branch_json(jp), write=True)
        if rel not in [e["rel_path"] for e in info.get("edited_files", [])]:
            info.setdefault("edited_files", []).append({
                "rel_path": rel, "abs_branch_path": branch_file,
                "edited_at": datetime.datetime.now().isoformat(),
            })
            self._write_branch_json(jp, info)
        return branch_file

    @_locked_metadata
    def prune_edit_prefix(self, fs_root: str, branch_name: str, rel_path: str, remove_overlay: bool = False):

        norm = self._normalize_branch_rel(fs_root, branch_name, rel_path)
        if not norm:
            return False
        jp = self._branch_json_path(fs_root, branch_name)
        info = self._sanitize_branch_json(fs_root, branch_name, self._read_branch_json(jp), write=True)
        edited = info.get("edited_files", [])
        kept = [
            e for e in edited
            if not (
                str(e.get("rel_path", "")).replace("\\", "/").rstrip("/") == norm
                or str(e.get("rel_path", "")).replace("\\", "/").startswith(norm + "/")
            )
        ]
        changed = len(kept) != len(edited)
        if changed:
            info["edited_files"] = kept

        if remove_overlay:
            branch_overlay = os.path.join(self._branch_dir(fs_root, branch_name), norm.replace("/", os.sep))
            if os.path.lexists(branch_overlay):
                _remove_path(branch_overlay)
                changed = True

        if changed:
            self._write_branch_json(jp, info)
        return changed

    @_locked_metadata
    def record_tree_edit(self, fs_root: str, branch_name: str, logical_path: str, physical_path: str):

        rel_root = self._normalize_branch_rel(fs_root, branch_name, logical_path)
        physical_path = os.path.normpath(physical_path)
        if not rel_root:
            return

        expected_physical = os.path.normpath(
            os.path.join(self._branch_dir(fs_root, branch_name), rel_root.replace("/", os.sep))
        )
        if os.path.normcase(physical_path) != os.path.normcase(expected_physical):
            try:
                if os.path.exists(expected_physical):
                    physical_path = expected_physical
                elif os.path.isdir(physical_path):
                    os.makedirs(os.path.dirname(expected_physical), exist_ok=True)
                    shutil.copytree(physical_path, expected_physical, symlinks=True, dirs_exist_ok=True)
                    physical_path = expected_physical
                elif os.path.isfile(physical_path):
                    os.makedirs(os.path.dirname(expected_physical), exist_ok=True)
                    shutil.copy2(physical_path, expected_physical)
                    physical_path = expected_physical
            except Exception as e:
                print(f"[NeoBranch] normalize overlay copy failed: {e}")

        now = datetime.datetime.now().isoformat()
        new_entries = {}

        def add_entry(rel: str, phys: str, kind: str):
            rel = rel.replace("\\", "/").rstrip("/")
            new_entries[rel] = {
                "rel_path": rel,
                "abs_branch_path": os.path.normpath(phys),
                "edited_at": now,
                "type": kind,
            }

        if os.path.isdir(physical_path):
            add_entry(rel_root, physical_path, "dir")
            for root, dirs, files in os.walk(physical_path):
                for d in dirs:
                    p = os.path.join(root, d)
                    rel = rel_root + "/" + os.path.relpath(p, physical_path).replace("\\", "/")
                    add_entry(rel, p, "dir")
                for fn in files:
                    p = os.path.join(root, fn)
                    rel = rel_root + "/" + os.path.relpath(p, physical_path).replace("\\", "/")
                    add_entry(rel, p, "file")
        elif os.path.isfile(physical_path):
            add_entry(rel_root, physical_path, "file")
        else:
            return

        jp = self._branch_json_path(fs_root, branch_name)
        info = self._sanitize_branch_json(fs_root, branch_name, self._read_branch_json(jp), write=True)
        existing = {
            str(e.get("rel_path", "")).replace("\\", "/").rstrip("/"): e
            for e in info.get("edited_files", [])
        }
        existing.update(new_entries)
        info["edited_files"] = list(existing.values())
        self._write_branch_json(jp, info)

    def get_branch_file_path(self, fs_root: str, branch_name: str, abs_source_path: str) -> str:

        rel = self._normalize_branch_rel(fs_root, branch_name, abs_source_path)
        if not rel:
            return abs_source_path
        branch_file = os.path.join(self._branch_dir(fs_root, branch_name), rel)
        if not os.path.exists(branch_file):
            os.makedirs(os.path.dirname(branch_file), exist_ok=True)
            try:
                shutil.copy2(abs_source_path, branch_file)
            except Exception as e:
                print(f"[NeoBranch] get_branch_file_path copy failed: {e}")
        return branch_file

    # delete tracking

    @_locked_metadata
    def record_delete(self, fs_root: str, branch_name: str, rel_path: str, prune_overlay: bool = True):

        norm = self._normalize_branch_rel(fs_root, branch_name, rel_path)
        if not norm:
            return
        if prune_overlay:
            self.prune_edit_prefix(fs_root, branch_name, norm, remove_overlay=True)
        jp = self._branch_json_path(fs_root, branch_name)
        info = self._sanitize_branch_json(fs_root, branch_name, self._read_branch_json(jp), write=True)
        deleted = info.setdefault("deleted_paths", [])
        changed = False
        if norm not in deleted:
            deleted.append(norm)
            changed = True
            print(f"[NeoBranch] recorded delete: {norm}")

        if changed:
            self._write_branch_json(jp, info)

    def get_deleted_paths(self, fs_root: str, branch_name: str) -> list:
        jp = self._branch_json_path(fs_root, branch_name)
        info = self._sanitize_branch_json(fs_root, branch_name, self._read_branch_json(jp), write=True)
        return info.get("deleted_paths", [])

    def is_deleted_in_branch(self, fs_root: str, branch_name: str, rel_path: str) -> bool:

        norm = self._normalize_branch_rel(fs_root, branch_name, rel_path)
        if not norm:
            return False
        for d in self.get_deleted_paths(fs_root, branch_name):
            d = self._normalize_branch_rel(fs_root, branch_name, d)
            if not d:
                continue
            if norm == d or norm.startswith(d + "/"):
                return True
        return False

    def deleted_set_for_branch(self, fs_root: str, branch_name: str) -> frozenset:

        jp = self._branch_json_path(fs_root, branch_name)
        raw = self._read_branch_json(jp).get("deleted_paths", [])
        out = set()
        for d in raw:
            nd = self._normalize_branch_rel(fs_root, branch_name, d)
            if nd:
                out.add(nd)
        return frozenset(out)

    @staticmethod
    def is_deleted_in_set(norm_rel: str, deleted_set) -> bool:

        if not norm_rel or not deleted_set:
            return False
        for d in deleted_set:
            if norm_rel == d or norm_rel.startswith(d + "/"):
                return True
        return False

    @_locked_metadata
    def undelete(self, fs_root: str, branch_name: str, rel_path: str):
        jp = self._branch_json_path(fs_root, branch_name)
        info = self._sanitize_branch_json(fs_root, branch_name, self._read_branch_json(jp), write=True)
        norm = self._normalize_branch_rel(fs_root, branch_name, rel_path)
        if not norm:
            return
        info["deleted_paths"] = [
            d for d in info.get("deleted_paths", [])
            if d != norm and not d.startswith(norm + "/")
        ]
        self._write_branch_json(jp, info)

    # repack integration

    def prepare_for_repack(self, fs_root: str, branch_name: str):

        source_fs  = self._source_fs_dir(fs_root)
        real_fs    = os.path.join(self.tree_base, fs_root)
        tree_cfg   = os.path.join(self.tree_base, "config")
        source_cfg = self._source_config_dir(fs_root)
        tree_neocat = neocat_cache_dir(self.tree_base)
        source_neocat = self._source_neocat_config_dir(fs_root)
        manifest_path = os.path.join(self._source_dir(fs_root), "repack_manifest.json")
        if os.path.isfile(manifest_path):
            raise RuntimeError(
                f"Interrupted NeoBranch repack for {fs_root}; restore the source before retrying"
            )

        # Complete the backup before touching the previous snapshot or real tree.
        pending_fs = source_fs + ".pending"
        if os.path.isdir(pending_fs):
            _remove_path(pending_fs)
        shutil.copytree(real_fs, pending_fs, symlinks=True)
        if os.path.isdir(source_fs):
            _remove_path(source_fs)
        _retry_fs_op(lambda: os.replace(pending_fs, source_fs), pending_fs)
        print(f"[NeoBranch][repack] source fs snapshotted: {fs_root}")

        # 2. Fresh config snapshot
        if os.path.isdir(source_cfg):
            _remove_path(source_cfg)
        os.makedirs(source_cfg, exist_ok=True)
        if os.path.isdir(tree_cfg):
            for fname in os.listdir(tree_cfg):
                for sfx in CONFIG_SUFFIXES:
                    if fname == fs_root + sfx:
                        src = os.path.join(tree_cfg, fname)
                        dst = os.path.join(source_cfg, fname)
                        if os.path.isfile(src):
                            shutil.copy2(src, dst)
                        break

        # 2b. Fresh neocat_config snapshot for this fs_root.
        if os.path.isdir(source_neocat):
            _remove_path(source_neocat)
        os.makedirs(source_neocat, exist_ok=True)
        src_neocat_file = os.path.join(tree_neocat, fs_root + "_file_contexts")
        if os.path.isfile(src_neocat_file):
            shutil.copy2(src_neocat_file, os.path.join(source_neocat, fs_root + "_file_contexts"))

        config_names = [fs_root + suffix for suffix in CONFIG_SUFFIXES]
        manifest = {
            "present_config": [name for name in config_names
                               if os.path.isfile(os.path.join(tree_cfg, name))],
            "present_neocat": os.path.isfile(src_neocat_file),
        }
        _atomic_json_write(manifest_path, manifest)

        try:
            # 3. Overlay branch fs files
            branch_json = self._sanitize_branch_json(
                fs_root,
                branch_name,
                self._read_branch_json(self._branch_json_path(fs_root, branch_name)),
                write=True,
            )

            # 3a. Hapus deleted_paths dari source tree dulu (logical delete -> physical delete sementara)
            for rel_del in branch_json.get("deleted_paths", []):
                rel_del = self._normalize_branch_rel(fs_root, branch_name, rel_del)
                if not rel_del:
                    continue
                dest_del = os.path.join(self.tree_base, rel_del.replace("/", os.sep))
                if os.path.isdir(dest_del):
                    _remove_path(dest_del)
                    print(f"[NeoBranch][repack] apply delete (dir): {rel_del}")
                elif os.path.isfile(dest_del):
                    _remove_path(dest_del)
                    print(f"[NeoBranch][repack] apply delete (file): {rel_del}")

            # 3b. Merge the physical branch overlay. This carries empty dirs too.
            branch_fs = self._branch_fs_dir(fs_root, branch_name)
            if os.path.isdir(branch_fs):
                shutil.copytree(branch_fs, real_fs, symlinks=True, dirs_exist_ok=True,
                                copy_function=_copy2_with_retry)
                print(f"[NeoBranch][repack] overlay branch fs dir: {fs_root}")

            # 3c. Copy edited branch files ke source tree (legacy exact entries)
            for entry in branch_json.get("edited_files", []):
                rel = self._normalize_branch_rel(fs_root, branch_name, entry.get("rel_path", ""))
                if not rel:
                    continue
                preferred = os.path.join(self._branch_dir(fs_root, branch_name), rel.replace("/", os.sep))
                branch_file = preferred
                dest        = os.path.join(self.tree_base, rel)
                if os.path.isdir(branch_file):
                    os.makedirs(dest, exist_ok=True)
                elif os.path.isfile(branch_file):
                    os.makedirs(os.path.dirname(dest), exist_ok=True)
                    _copy2_with_retry(branch_file, dest)
                    print(f"[NeoBranch][repack] overlay fs: {rel}")

            # 4. Overlay branch config -> tree_base/config
            branch_cfg = self._branch_config_dir(fs_root, branch_name)
            if os.path.isdir(branch_cfg):
                for fname in os.listdir(branch_cfg):
                    if not any(fname == fs_root + sfx for sfx in CONFIG_SUFFIXES):
                        continue
                    src = os.path.join(branch_cfg, fname)
                    dst = os.path.join(tree_cfg, fname)
                    if os.path.isfile(src):
                        _copy2_with_retry(src, dst)
                        print(f"[NeoBranch][repack] overlay config: {fname}")

            # 5. Overlay branch neocat_config -> tree_base/neocat_config
            branch_neocat = self._branch_neocat_config_dir(fs_root, branch_name)
            if os.path.isdir(branch_neocat):
                os.makedirs(tree_neocat, exist_ok=True)
                fname = fs_root + "_file_contexts"
                src = os.path.join(branch_neocat, fname)
                dst = os.path.join(tree_neocat, fname)
                if os.path.isfile(src):
                    self._copy_sanitized_file_contexts(src, dst)
                    print(f"[NeoBranch][repack] overlay neocat_config: {fname}")
        except Exception:
            self.restore_after_repack(fs_root)
            raise

    def sync_live_file_contexts_to_branch(self, fs_root: str, branch_name: str):

        src = os.path.join(self.tree_base, "config", f"{fs_root}_file_contexts")
        dst = os.path.join(self._branch_config_dir(fs_root, branch_name), f"{fs_root}_file_contexts")
        if not os.path.isfile(src):
            return False
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        self._copy_sanitized_file_contexts(src, dst, merge_from=dst)
        print(f"[NeoBranch][repack] synced file_contexts to branch: {fs_root}")
        return True

    def sync_live_neocat_to_branch(self, fs_root: str, branch_name: str):
        src = os.path.join(neocat_cache_dir(self.tree_base), f"{fs_root}_file_contexts")
        dst = os.path.join(self._branch_neocat_config_dir(fs_root, branch_name), f"{fs_root}_file_contexts")
        if not os.path.isfile(src):
            return False
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        self._copy_sanitized_file_contexts(src, dst, merge_from=dst)
        print(f"[NeoBranch][repack] synced neocat_config to branch: {fs_root}")
        return True

    def _normalize_file_context_key(self, key: str):
        key = str(key).strip()
        for _ in range(4):
            if not key.startswith(f"/{NB_STORE_DIR}/"):
                return key
            parts = key.strip("/").split("/")
            if len(parts) < 5 or parts[0] != NB_STORE_DIR:
                return None
            fs_root = parts[1]
            marker = parts[2]
            rest = parts[3:]
            if not (marker.startswith("branch_") or marker == "source"):
                return None
            if not rest or rest[0] != fs_root:
                return None
            key = "/" + "/".join(rest)
        return None

    def _copy_sanitized_file_contexts(self, src: str, dst: str, merge_from: str = None):
        entries = {}
        for path in [merge_from, src]:
            if not path or not os.path.isfile(path):
                continue
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                for ln in f:
                    s = ln.strip()
                    if not s or s.startswith("#"):
                        continue
                    parts = s.split(None, 1)
                    if len(parts) < 2:
                        continue
                    key = self._normalize_file_context_key(parts[0])
                    if not key:
                        continue
                    entries[key] = parts[1]
        with open(dst, "w", encoding="utf-8", errors="ignore") as f:
            f.write("\n".join(f"{k} {entries[k]}" for k in sorted(entries)) + ("\n" if entries else ""))

    def restore_after_repack(self, fs_root: str):

        source_fs  = self._source_fs_dir(fs_root)
        real_fs    = os.path.join(self.tree_base, fs_root)
        tree_cfg   = os.path.join(self.tree_base, "config")
        source_cfg = self._source_config_dir(fs_root)
        tree_neocat = neocat_cache_dir(self.tree_base)
        source_neocat = self._source_neocat_config_dir(fs_root)
        manifest_path = os.path.join(self._source_dir(fs_root), "repack_manifest.json")
        manifest = None
        if os.path.isfile(manifest_path):
            with open(manifest_path, "r", encoding="utf-8") as f:
                manifest = json.load(f)
        if manifest is None:
            return False

        # Do not consume the recovery marker when its backup is incomplete.
        # A later retry can still restore the original tree if the files return.
        if not os.path.isdir(source_fs):
            raise RuntimeError(f"NeoBranch source backup missing: {source_fs}")
        if not os.path.isdir(source_cfg):
            raise RuntimeError(f"NeoBranch config backup missing: {source_cfg}")
        for fname in manifest.get("present_config", []):
            if not os.path.isfile(os.path.join(source_cfg, fname)):
                raise RuntimeError(f"NeoBranch config backup missing: {fname}")
        if manifest.get("present_neocat") and not os.path.isfile(
            os.path.join(source_neocat, fs_root + "_file_contexts")
        ):
            raise RuntimeError(f"NeoBranch NeoCat backup missing: {fs_root}")

        # Copy before touching the live tree. A failed rename leaves the live
        # tree in place; a failed install rolls the moved tree back.
        restore_work = tempfile.mkdtemp(prefix="restore-", dir=self._source_dir(fs_root))
        staged_fs = os.path.join(restore_work, "staged")
        displaced_fs = os.path.join(restore_work, "previous")
        installed = False
        try:
            shutil.copytree(source_fs, staged_fs, symlinks=True)
            if os.path.lexists(real_fs):
                _retry_fs_op(lambda: os.replace(real_fs, displaced_fs), real_fs)
            try:
                _retry_fs_op(lambda: os.replace(staged_fs, real_fs), staged_fs)
                installed = True
            except Exception:
                if os.path.lexists(displaced_fs) and not os.path.lexists(real_fs):
                    _retry_fs_op(lambda: os.replace(displaced_fs, real_fs), displaced_fs)
                raise
        finally:
            if not installed and os.path.lexists(real_fs):
                try:
                    _remove_path(restore_work)
                except OSError:
                    pass
        print(f"[NeoBranch][repack] fs tree restored: {fs_root}")
        try:
            _remove_path(restore_work)
        except OSError as exc:
            print(f"[NeoBranch][repack] old tree cleanup deferred: {exc}")

        if os.path.isdir(source_cfg):
            for fname in os.listdir(source_cfg):
                src = os.path.join(source_cfg, fname)
                dst = os.path.join(tree_cfg, fname)
                if os.path.isfile(src):
                    _copy2_with_retry(src, dst)
                    print(f"[NeoBranch][repack] config restored: {fname}")
        if manifest is not None:
            present = set(manifest.get("present_config", []))
            for suffix in CONFIG_SUFFIXES:
                fname = fs_root + suffix
                dst = os.path.join(tree_cfg, fname)
                if fname not in present and os.path.isfile(dst):
                    _remove_path(dst)

        fname = fs_root + "_file_contexts"
        src_neocat = os.path.join(source_neocat, fname)
        dst_neocat = os.path.join(tree_neocat, fname)
        if os.path.isfile(src_neocat):
            os.makedirs(tree_neocat, exist_ok=True)
            _copy2_with_retry(src_neocat, dst_neocat)
            print(f"[NeoBranch][repack] neocat_config restored: {fname}")
        elif os.path.isfile(dst_neocat):
            _remove_path(dst_neocat)
            print(f"[NeoBranch][repack] neocat_config removed: {fname}")
            try:
                if os.path.isdir(tree_neocat) and not os.listdir(tree_neocat):
                    os.rmdir(tree_neocat)
            except Exception:
                pass
        os.remove(manifest_path)
        return True

    def recover_interrupted_repacks(self) -> list[str]:

        restored = []
        if not os.path.isdir(self._neobranch_root):
            return restored
        for fs_root in os.listdir(self._neobranch_root):
            manifest = os.path.join(self._source_dir(fs_root), "repack_manifest.json")
            if os.path.isfile(manifest):
                self.restore_after_repack(fs_root)
                restored.append(fs_root)
        return restored

    # delete branch

    @_locked_metadata
    def delete_branch(self, fs_root: str, branch_name: str):
        branch_dir = self._branch_dir(fs_root, branch_name)
        if os.path.isdir(branch_dir):
            _remove_path(branch_dir)
        remove_neocat_branch_cache(self.tree_base, fs_root, branch_name)
        state = self._read_state(fs_root)
        if state.get("active_branch") == branch_name:
            state["active_branch"] = None
            self._write_state(fs_root, state)

    # source snapshot

    def _ensure_source_snapshot(self, fs_root: str):

        source_dir = self._source_dir(fs_root)
        completed = os.path.join(source_dir, "snapshot_complete.json")
        manifest = os.path.join(source_dir, "repack_manifest.json")
        real_fs    = os.path.join(self.tree_base, fs_root)
        real_cfg   = os.path.join(self.tree_base, "config")

        if os.path.isfile(manifest):
            raise RuntimeError(f"Interrupted NeoBranch repack for {fs_root}; restore the source first")
        if os.path.isfile(completed):
            return
        if not os.path.isdir(real_fs):
            raise FileNotFoundError(real_fs)

        nb_root = self._nb_root(fs_root)
        os.makedirs(nb_root, exist_ok=True)
        _hide_dir(self._neobranch_root)
        pending = source_dir + ".pending"
        if os.path.exists(pending):
            _remove_path(pending)
        os.makedirs(pending)
        try:
            shutil.copytree(real_fs, os.path.join(pending, fs_root), symlinks=True)
            pending_cfg = os.path.join(pending, "config")
            os.makedirs(pending_cfg)
            if os.path.isdir(real_cfg):
                for suffix in CONFIG_SUFFIXES:
                    fname = fs_root + suffix
                    src = os.path.join(real_cfg, fname)
                    if os.path.isfile(src):
                        shutil.copy2(src, os.path.join(pending_cfg, fname))
            _atomic_json_write(os.path.join(pending, "snapshot_complete.json"), {"fs_root": fs_root})
            if os.path.isdir(source_dir):
                _remove_path(source_dir)
            _retry_fs_op(lambda: os.replace(pending, source_dir), pending)
        except Exception:
            if os.path.isdir(pending):
                _remove_path(pending)
            raise
        print(f"[NeoBranch] source snapshot created: {fs_root}")

    # SELinux / perm helpers

    def resolve_read_path(self, fs_root: str, branch_name: str, abs_source_path: str) -> str:
        rel = os.path.relpath(abs_source_path, self.tree_base).replace("\\", "/")
        branch_file = os.path.join(self._branch_dir(fs_root, branch_name), rel)
        return branch_file if os.path.isfile(branch_file) else abs_source_path

    # state helpers

    def _state_path(self, fs_root): return os.path.join(self._nb_root(fs_root), "neobranch_state.json")

    def _read_state(self, fs_root: str) -> dict:
        p = self._state_path(fs_root)
        if os.path.isfile(p):
            try:
                with open(p, "r", encoding="utf-8") as f: return json.load(f)
            except Exception: pass
        return {}

    def _write_state(self, fs_root: str, state: dict):
        p = self._state_path(fs_root)
        _atomic_json_write(p, state)

    # branch.json helpers

    def _read_branch_json(self, path: str) -> dict:
        if os.path.isfile(path):
            try:
                with open(path, "r", encoding="utf-8") as f: return json.load(f)
            except Exception: pass
        return {"edited_files": [], "deleted_paths": []}

    def _write_branch_json(self, path: str, data: dict):
        _atomic_json_write(path, data)

    # validator

    @staticmethod
    def _validate_branch_name(name: str):
        import re
        if not name:
            raise ValueError("Branch name cannot be empty.")
        if not re.match(r'^[a-zA-Z0-9_\-]+$', name):
            raise ValueError("Branch name may only contain letters, numbers, underscore, and dash.")
        if len(name) > 64:
            raise ValueError("Branch name must be 64 characters or fewer.")
