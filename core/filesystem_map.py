# SPDX-License-Identifier: MPL-2.0
"""Persistent per-partition directory & symlink snapshot cache (filesystem_map).

Stored under AppData (`PROJECT_CACHE_ROOT/<project_key>/filesystem_map/<partition>.json`),
never inside ROM partition trees, so repacks stay clean while UI folder browsing and
post-unpack transitions reuse full tree/symlink knowledge without repeated full disk crawls.
"""
from __future__ import annotations

import hashlib
import json
import os
import stat
import tempfile
import threading

from core.app_paths import _safe_component, project_cache_dir
from core.symlink import MAX_SIZE as SYMLINK_MAX_SIZE, check_symlink, resolve_symlink_target

CACHE_VERSION = 1
_SKIP_ROOTS = frozenset({"config", "neobranch", "meta-inf", ".rak_quarantine"})
_MAGIC_EXTS = (".img", ".bin", ".ozip", ".ofp", ".ops", ".kdz", ".dz", ".app", ".pac", ".cpb", ".romfs", ".squashfs", ".sqsh")

_LOCK = threading.RLock()
_MAPS: dict[tuple[str, str, str], "PartitionMap"] = {}


def clear_memory_cache() -> None:
    """Drop in-memory PartitionMap instances (intended for tests)."""
    with _LOCK:
        _MAPS.clear()


def _norm_base(project_base: str) -> str:
    return os.path.normcase(os.path.abspath(os.path.normpath(project_base)))


def split_partition_rel(project_base: str, path: str) -> tuple[str, str] | None:
    """Split `path` into `(partition, sub_rel)` relative to `project_base`, or `None`."""
    if not project_base or not path:
        return None
    base_abs = os.path.abspath(os.path.normpath(project_base))
    target_abs = os.path.abspath(os.path.normpath(path))
    try:
        rel = os.path.relpath(target_abs, base_abs)
    except ValueError:
        return None
    if rel == "." or rel.startswith("..") or os.path.isabs(rel):
        return None
    rel_slash = rel.replace(os.sep, "/")
    parts = rel_slash.split("/", 1)
    partition = parts[0]
    if not partition or partition.startswith(".") or partition.casefold() in _SKIP_ROOTS:
        return None
    sub_rel = parts[1].strip("/") if len(parts) > 1 else ""
    return partition, sub_rel


def branch_context(project_base: str, partition: str) -> tuple[str | None, bool]:
    """Return `(active_branch_name, is_physical_checkout)` for `partition`."""
    nb_part = os.path.join(os.path.abspath(os.path.normpath(project_base)), "neobranch", partition)
    if not os.path.isdir(nb_part):
        return None, False
    try:
        from main_ext.class_NeoBranchManager import NeoBranchManager
        nb = NeoBranchManager(project_base)
        active = nb.get_active_branch(partition)
        if active is None:
            return None, False
        return str(active["name"]), bool(nb.is_physical_checkout(partition))
    except Exception:
        return None, False


def _is_partition_busy(project_base: str, partition: str) -> bool:
    nb_part = os.path.join(os.path.abspath(os.path.normpath(project_base)), "neobranch", partition)
    return (
        os.path.isfile(os.path.join(nb_part, "checkout", "journal.json"))
        or os.path.isfile(os.path.join(nb_part, "source", "repack_manifest.json"))
    )


def _branch_cache_filename(partition: str, branch_name: str | None) -> str:
    safe_part = _safe_component(partition)
    if not branch_name:
        return f"{safe_part}.json"
    digest = hashlib.blake2b(branch_name.encode("utf-8"), digest_size=4).hexdigest()
    return f"{safe_part}__branch_{_safe_component(branch_name)}_{digest}.json"


def _cache_path_for(
    project_base: str,
    partition: str,
    *,
    branch_name: str | None = None,
    create: bool = False,
) -> str:
    cache_dir = project_cache_dir(project_base, "filesystem_map", create=create)
    return os.path.join(cache_dir, _branch_cache_filename(partition, branch_name))


def _is_stat_read_only(info) -> bool:
    if info is None:
        return False
    attrs = getattr(info, "st_file_attributes", 0)
    if attrs:
        return bool(attrs & 0x1)
    import stat
    return not bool(info.st_mode & stat.S_IWRITE)


def _entry_dict(
    name: str,
    is_dir: bool,
    size: int,
    mtime_ns: int,
    is_link: bool,
    link_target: str,
    magic: str = "",
    read_only: bool = False,
) -> dict:
    return {
        "name": str(name),
        "is_dir": bool(is_dir),
        "size": 0 if is_dir else int(size or 0),
        "mtime_ns": 0 if is_dir else int(mtime_ns or 0),
        "is_link": bool(is_link),
        "link_target": str(link_target or "") if is_link else "",
        "magic": str(magic or ""),
        "read_only": False if is_dir else bool(read_only),
    }


def _row_from_entry(project_base: str, physical_dir: str, entry: dict, target_cache: dict) -> dict:
    name = entry["name"]
    full_path = os.path.join(physical_dir, name)
    is_dir = bool(entry.get("is_dir"))
    is_link = bool(entry.get("is_link"))
    symlink = (
        resolve_symlink_target(full_path, entry.get("link_target") or "", project_base, target_cache=target_cache)
        if is_link else None
    )
    return {
        "name": name,
        "path": full_path,
        "is_dir": is_dir,
        "size": 0 if is_dir else int(entry.get("size") or 0),
        "mtime_ns": 0 if is_dir else int(entry.get("mtime_ns") or 0),
        "symlink": symlink,
        "magic": entry.get("magic") or "",
        "read_only": False if is_dir else bool(entry.get("read_only", False)),
    }


class PartitionMap:
    """Thread-safe persistent directory snapshot map for a single ROM partition."""

    def __init__(self, project_base: str, partition: str, cache_path: str, *, branch_name: str = ""):
        self.project_base = os.path.abspath(os.path.normpath(project_base))
        self.partition = partition
        self.branch_name = str(branch_name or "")
        self.cache_path = cache_path
        self.fs_type = ""
        self.dirs: dict[str, dict] = {}
        self.lock = threading.RLock()
        self._dirty = False

    def load(self) -> bool:
        with self.lock:
            if not os.path.isfile(self.cache_path):
                return False
            try:
                with open(self.cache_path, "r", encoding="utf-8") as stream:
                    data = json.load(stream)
                if not isinstance(data, dict) or data.get("version") != CACHE_VERSION:
                    self.dirs = {}
                    return False
                dirs_raw = data.get("dirs")
                if not isinstance(dirs_raw, dict):
                    self.dirs = {}
                    return False
                self.fs_type = str(data.get("fs_type") or "")
                parsed: dict[str, dict] = {}
                for sub_rel, dinfo in dirs_raw.items():
                    if not isinstance(dinfo, dict):
                        continue
                    entries_raw = dinfo.get("entries")
                    if not isinstance(entries_raw, dict):
                        continue
                    clean_entries = {}
                    for ename, edata in entries_raw.items():
                        if isinstance(edata, dict):
                            clean_entries[ename] = _entry_dict(
                                edata.get("name", ename),
                                bool(edata.get("is_dir", False)),
                                int(edata.get("size") or 0),
                                int(edata.get("mtime_ns") or 0),
                                bool(edata.get("is_link", False)),
                                str(edata.get("link_target") or ""),
                                str(edata.get("magic") or ""),
                            )
                    parsed[str(sub_rel)] = {
                        "dir_mtime_ns": int(dinfo.get("dir_mtime_ns") or 0),
                        "entries": clean_entries,
                    }
                self.dirs = parsed
                self._dirty = False
                return True
            except (OSError, ValueError, TypeError):
                self.dirs = {}
                return False

    def save(self) -> None:
        with self.lock:
            parent = os.path.dirname(self.cache_path)
            tmp_path = None
            try:
                os.makedirs(parent, exist_ok=True)
                payload = {
                    "version": CACHE_VERSION,
                    "partition": self.partition,
                    "branch": self.branch_name,
                    "fs_type": self.fs_type,
                    "dirs": self.dirs,
                }
                with tempfile.NamedTemporaryFile(
                    mode="w",
                    encoding="utf-8",
                    dir=parent,
                    prefix=".fsmap-",
                    suffix=".tmp",
                    delete=False,
                ) as tmp:
                    tmp_path = tmp.name
                    json.dump(payload, tmp, ensure_ascii=False, separators=(",", ":"))
                os.replace(tmp_path, self.cache_path)
                tmp_path = None
                self._dirty = False
            except OSError:
                pass
            finally:
                if tmp_path and os.path.exists(tmp_path):
                    try:
                        os.remove(tmp_path)
                    except OSError:
                        pass


def get_partition_map(
    project_base: str,
    partition: str,
    *,
    branch_name: str | None = None,
    create: bool = False,
) -> PartitionMap | None:
    """Return the cached `PartitionMap` if present in memory or on disk, or create if requested."""
    if not project_base or not partition:
        return None
    branch_clean = str(branch_name or "")
    key = (_norm_base(project_base), partition.casefold(), branch_clean.casefold())
    with _LOCK:
        existing = _MAPS.get(key)
        if existing is not None:
            return existing
        cache_path = _cache_path_for(project_base, partition, branch_name=branch_clean or None, create=False)
        if not os.path.isfile(cache_path) and not create:
            return None
        pmap = PartitionMap(project_base, partition, cache_path, branch_name=branch_clean)
        if os.path.isfile(cache_path):
            pmap.load()
        _MAPS[key] = pmap
        return pmap


def clone_branch_map(
    project_base: str,
    partition: str,
    src_branch: str | None,
    dst_branch: str | None,
) -> PartitionMap | None:
    """Clone an existing `PartitionMap` (e.g. Source -> new NeoBranch) without re-crawling disk."""
    src_map = get_partition_map(project_base, partition, branch_name=src_branch, create=False)
    if src_map is None:
        return None
    with src_map.lock:
        fs_type = src_map.fs_type
        cloned_dirs = {
            sub_rel: {
                "dir_mtime_ns": int(dinfo.get("dir_mtime_ns") or 0),
                "entries": {ename: dict(edata) for ename, edata in dinfo.get("entries", {}).items()},
            }
            for sub_rel, dinfo in src_map.dirs.items()
        }
    dst_map = get_partition_map(project_base, partition, branch_name=dst_branch, create=True)
    if dst_map is None:
        return None
    with dst_map.lock:
        dst_map.fs_type = fs_type
        dst_map.dirs = cloned_dirs
        dst_map.save()
    return dst_map


def remove_branch_map(project_base: str, partition: str, branch_name: str | None) -> None:
    """Drop in-memory and persistent `filesystem_map` cache for a specific partition/branch."""
    if not project_base or not partition:
        return
    branch_clean = str(branch_name or "")
    key = (_norm_base(project_base), partition.casefold(), branch_clean.casefold())
    with _LOCK:
        pmap = _MAPS.pop(key, None)
    cache_path = (
        pmap.cache_path
        if pmap is not None
        else _cache_path_for(project_base, partition, branch_name=branch_clean or None, create=False)
    )
    try:
        if os.path.isfile(cache_path):
            os.remove(cache_path)
    except OSError:
        pass


def seed_from_mount_index(
    project_base: str,
    target_dir: str,
    child_index: dict,
    fs_type: str = "",
) -> PartitionMap | None:
    """Promote an unpacked image's `MountIndex` child map directly into persistent `filesystem_map`."""
    split = split_partition_rel(project_base, target_dir)
    if split is None:
        return None
    partition, sub_prefix = split
    if sub_prefix:
        return None
    pmap = get_partition_map(project_base, partition, create=True)
    if pmap is None:
        return None

    dirs: dict[str, dict] = {}
    for parent_rel, children in (child_index or {}).items():
        clean_parent = str(parent_rel or "").strip("/")
        entry_map: dict[str, dict] = {}
        for child in children:
            name = str(child.get("name") or "")
            if not name:
                continue
            is_link = bool(child.get("is_link"))
            is_dir = bool(child.get("is_dir")) and not is_link
            size = 0 if is_dir else int(child.get("size") or 0)
            target = str(child.get("target") or "") if is_link else ""
            entry_map[name] = _entry_dict(name, is_dir, size, 0, is_link, target, "")
            if is_dir:
                child_rel = f"{clean_parent}/{name}" if clean_parent else name
                dirs.setdefault(child_rel, {"dir_mtime_ns": 0, "entries": {}})
        dir_mtime = 0
        host_dir = os.path.join(target_dir, clean_parent.replace("/", os.sep)) if clean_parent else target_dir
        try:
            dir_mtime = os.stat(host_dir).st_mtime_ns
        except OSError:
            pass
        dirs[clean_parent] = {"dir_mtime_ns": int(dir_mtime), "entries": entry_map}
    dirs.setdefault("", {"dir_mtime_ns": 0, "entries": {}})

    with pmap.lock:
        pmap.fs_type = str(fs_type or pmap.fs_type or "")
        pmap.dirs = dirs
        pmap.save()
    return pmap


def peek_cached_rows(
    project_base: str,
    physical_dir: str,
    logical_dir: str,
    *,
    branch_name: str | None = None,
    resolve_branch: bool = True,
) -> list[dict] | None:
    """Return cached directory entries formatted like `native_backend.directory_entries`, or `None`."""
    split = split_partition_rel(project_base, logical_dir)
    if split is None:
        return None
    partition, sub_rel = split
    if resolve_branch:
        active_branch, is_physical = branch_context(project_base, partition)
        if active_branch is not None and not is_physical:
            return None
        branch_name = active_branch
    pmap = get_partition_map(project_base, partition, branch_name=branch_name, create=False)
    if pmap is None:
        return None
    with pmap.lock:
        dinfo = pmap.dirs.get(sub_rel)
        if dinfo is None:
            return None
        snapshot_entries = list(dinfo.get("entries", {}).values())

    # Always pass a fresh stat cache so symlink `exists` checks see current target status.
    target_cache: dict = {}
    rows = [_row_from_entry(project_base, physical_dir, entry, target_cache) for entry in snapshot_entries]
    return sorted(rows, key=lambda r: r["name"])


def _probe_disk_entry(
    project_base: str,
    full_path: str,
    name: str,
    is_dir: bool,
    is_os_link: bool,
    size: int,
    mtime_ns: int,
    prev: dict | None,
    *,
    include_magic: bool,
    target_cache: dict,
) -> tuple[dict, dict | None]:
    magic = ""
    if is_dir and not is_os_link:
        entry = _entry_dict(name, True, 0, 0, False, "", "")
        return entry, None

    if is_os_link:
        try:
            target = os.readlink(full_path)
        except OSError:
            target = ""
        sym = resolve_symlink_target(full_path, target, project_base, target_cache=target_cache) if target else None
        is_link = sym is not None or bool(target)
        entry = _entry_dict(name, False, size, mtime_ns, is_link, target, "")
        return entry, sym

    low = name.lower()
    want_magic = include_magic and low.endswith((".img", ".bin"))

    if (
        prev is not None
        and not prev.get("is_dir")
        and int(prev.get("mtime_ns") or 0) == mtime_ns
        and int(prev.get("size") or 0) == size
        and int(prev.get("mtime_ns") or 0) != 0
    ):
        is_link = bool(prev.get("is_link"))
        link_target = prev.get("link_target") or ""
        sym = resolve_symlink_target(full_path, link_target, project_base, target_cache=target_cache) if is_link else None
        magic = prev.get("magic") or ""
        if want_magic and not is_link and not magic:
            from core.header_magic import check_magic
            magic = check_magic(full_path)
        return _entry_dict(name, False, size, mtime_ns, is_link, link_target, magic), sym

    if prev is not None and not prev.get("is_dir") and int(prev.get("mtime_ns") or 0) == 0:
        # Freshly seeded from unpacked image metadata; hydrate stat stamps cleanly without re-reading known links/large files.
        if prev.get("is_link") and size <= SYMLINK_MAX_SIZE:
            link_target = prev.get("link_target") or ""
            sym = resolve_symlink_target(full_path, link_target, project_base, target_cache=target_cache)
            return _entry_dict(name, False, size, mtime_ns, bool(link_target), link_target, ""), sym
        if not prev.get("is_link") and size > SYMLINK_MAX_SIZE:
            if want_magic:
                from core.header_magic import check_magic
                magic = check_magic(full_path)
            return _entry_dict(name, False, size, mtime_ns, False, "", magic), None

    sym = check_symlink(full_path, project_base, target_cache=target_cache) if size <= SYMLINK_MAX_SIZE else None
    is_link = sym is not None
    link_target = sym["target"] if is_link else ""
    if want_magic and not is_link:
        from core.header_magic import check_magic
        magic = check_magic(full_path)
    return _entry_dict(name, False, size, mtime_ns, is_link, link_target, magic), sym


def scan_and_revalidate_dir(
    project_base: str,
    physical_dir: str,
    logical_dir: str,
    *,
    include_magic: bool = False,
    branch_name: str | None = None,
) -> list[dict]:
    """Scan `physical_dir` once via `os.scandir`, reuse unchanged entries from `filesystem_map`, and persist changes."""
    split = split_partition_rel(project_base, logical_dir)
    if split is None:
        from core.native_backend import directory_entries
        return directory_entries(physical_dir, project_base, include_magic=include_magic)

    partition, sub_rel = split
    pmap = get_partition_map(project_base, partition, branch_name=branch_name, create=True)
    with pmap.lock:
        prev_dir = pmap.dirs.get(sub_rel)
        prev_entries = dict(prev_dir.get("entries", {})) if prev_dir else {}
        prev_dir_mtime = int(prev_dir.get("dir_mtime_ns") or 0) if prev_dir else -1

    try:
        dir_mtime_ns = int(os.stat(physical_dir).st_mtime_ns)
    except OSError:
        dir_mtime_ns = 0

    new_entries: dict[str, dict] = {}
    rows: list[dict] = []
    target_cache: dict = {}

    with os.scandir(physical_dir) as it:
        for entry in it:
            try:
                is_os_link = entry.is_symlink()
                is_dir = False if is_os_link else entry.is_dir(follow_symlinks=False)
                info = None if is_dir else entry.stat(follow_symlinks=False)
                size = 0 if is_dir else (int(info.st_size) if info else 0)
                mtime_ns = 0 if is_dir else (int(info.st_mtime_ns) if info else 0)
                read_only = False if is_dir else _is_stat_read_only(info)
            except OSError:
                is_dir, is_os_link, size, mtime_ns, read_only = False, False, 0, 0, False

            ent_dict, sym_dict = _probe_disk_entry(
                project_base,
                entry.path,
                entry.name,
                is_dir,
                is_os_link,
                size,
                mtime_ns,
                prev_entries.get(entry.name),
                include_magic=include_magic,
                target_cache=target_cache,
            )
            ent_dict["read_only"] = read_only
            new_entries[entry.name] = ent_dict
            rows.append({
                "name": entry.name,
                "path": entry.path,
                "is_dir": ent_dict["is_dir"],
                "size": ent_dict["size"],
                "mtime_ns": ent_dict["mtime_ns"],
                "symlink": sym_dict,
                "magic": ent_dict["magic"],
                "read_only": read_only,
            })

    with pmap.lock:
        if prev_dir is None or new_entries != prev_entries or dir_mtime_ns != prev_dir_mtime:
            pmap.dirs[sub_rel] = {"dir_mtime_ns": dir_mtime_ns, "entries": new_entries}
            pmap.save()

    return sorted(rows, key=lambda r: r["name"])


def record_entry(
    project_base: str,
    logical_path: str,
    physical_path: str | None = None,
    *,
    branch_name: str | None = None,
    resolve_branch: bool = True,
) -> None:
    """Update or insert a single file/folder/symlink in `filesystem_map` after an in-app mutation."""
    split = split_partition_rel(project_base, logical_path)
    if split is None:
        return
    partition, sub_rel = split
    if resolve_branch:
        if _is_partition_busy(project_base, partition):
            return
        active_branch, is_physical = branch_context(project_base, partition)
        if active_branch is not None and not is_physical:
            return
        branch_name = active_branch
    pmap = get_partition_map(project_base, partition, branch_name=branch_name, create=False)
    if pmap is None or not sub_rel:
        return

    phys = physical_path or logical_path
    if not os.path.lexists(phys):
        record_delete(project_base, logical_path, branch_name=branch_name, resolve_branch=False)
        return

    parent_rel, _, name = sub_rel.rpartition("/")
    try:
        st = os.lstat(phys)
        is_os_link = stat.S_ISLNK(st.st_mode)
        is_dir = stat.S_ISDIR(st.st_mode) and not is_os_link
        size = 0 if is_dir else int(st.st_size)
        mtime_ns = 0 if is_dir else int(st.st_mtime_ns)
    except OSError:
        return

    ent_dict, _ = _probe_disk_entry(
        project_base,
        phys,
        name,
        is_dir,
        is_os_link,
        size,
        mtime_ns,
        None,
        include_magic=False,
        target_cache={},
    )
    with pmap.lock:
        changed = False
        parent_info = pmap.dirs.get(parent_rel)
        if parent_info is not None:
            if parent_info["entries"].get(name) != ent_dict:
                parent_info["entries"][name] = ent_dict
                changed = True
            try:
                parent_info["dir_mtime_ns"] = int(os.stat(os.path.dirname(phys)).st_mtime_ns)
            except OSError:
                pass
        if is_dir:
            # Invalidate copied/replaced subtree so next expand validates fresh children on disk.
            prefix = sub_rel + "/"
            for key in [k for k in pmap.dirs if k == sub_rel or k.startswith(prefix)]:
                pmap.dirs.pop(key, None)
                changed = True
        if changed:
            pmap.save()


def _remove_all_partition_branches(project_base: str, partition: str) -> None:
    norm_b = _norm_base(project_base)
    part_cf = partition.casefold()
    removed_paths = set()
    with _LOCK:
        for key in [k for k in _MAPS if k[0] == norm_b and k[1] == part_cf]:
            pmap = _MAPS.pop(key, None)
            if pmap is not None:
                removed_paths.add(pmap.cache_path)
    cache_dir = project_cache_dir(project_base, "filesystem_map", create=False)
    prefix = f"{_safe_component(partition)}__branch_"
    base_json = f"{_safe_component(partition)}.json"
    if os.path.isdir(cache_dir):
        try:
            for fname in os.listdir(cache_dir):
                if fname == base_json or (fname.startswith(prefix) and fname.endswith(".json")):
                    removed_paths.add(os.path.join(cache_dir, fname))
        except OSError:
            pass
    for path in removed_paths:
        try:
            if os.path.isfile(path):
                os.remove(path)
        except OSError:
            pass


def record_delete(
    project_base: str,
    logical_path: str,
    *,
    branch_name: str | None = None,
    resolve_branch: bool = True,
) -> None:
    """Remove a deleted file, folder subtree, or entire partition from `filesystem_map`."""
    split = split_partition_rel(project_base, logical_path)
    if split is None:
        return
    partition, sub_rel = split
    if resolve_branch:
        if _is_partition_busy(project_base, partition):
            return
        active_branch, is_physical = branch_context(project_base, partition)
        if active_branch is not None and not is_physical:
            return
        branch_name = active_branch
    if not sub_rel:
        if branch_name:
            remove_branch_map(project_base, partition, branch_name)
        else:
            _remove_all_partition_branches(project_base, partition)
        return

    pmap = get_partition_map(project_base, partition, branch_name=branch_name, create=False)
    if pmap is None:
        return
    parent_rel, _, name = sub_rel.rpartition("/")
    prefix = sub_rel + "/"
    with pmap.lock:
        changed = False
        parent_info = pmap.dirs.get(parent_rel)
        if parent_info is not None and name in parent_info["entries"]:
            parent_info["entries"].pop(name, None)
            changed = True
        for key in [k for k in pmap.dirs if k == sub_rel or k.startswith(prefix)]:
            pmap.dirs.pop(key, None)
            changed = True
        if changed:
            pmap.save()


def record_rename(
    project_base: str,
    old_logical: str,
    new_logical: str,
    new_physical: str | None = None,
) -> None:
    """Relocate a file, folder subtree, or partition root in `filesystem_map`."""
    old_split = split_partition_rel(project_base, old_logical)
    new_split = split_partition_rel(project_base, new_logical)
    if old_split is None and new_split is None:
        return
    if old_split is None:
        record_entry(project_base, new_logical, new_physical)
        return
    if new_split is None:
        record_delete(project_base, old_logical)
        return

    old_part, old_sub = old_split
    new_part, new_sub = new_split
    old_branch, old_phys = branch_context(project_base, old_part)
    new_branch, new_phys = branch_context(project_base, new_part)

    if (
        (old_branch is not None and not old_phys)
        or (new_branch is not None and not new_phys)
        or old_part.casefold() != new_part.casefold()
        or (old_branch or "").casefold() != (new_branch or "").casefold()
        or _is_partition_busy(project_base, old_part)
        or _is_partition_busy(project_base, new_part)
    ):
        record_delete(project_base, old_logical)
        record_entry(project_base, new_logical, new_physical)
        return

    if not old_sub and not new_sub:
        branch_clean = str(old_branch or "")
        old_key = (_norm_base(project_base), old_part.casefold(), branch_clean.casefold())
        new_key = (_norm_base(project_base), new_part.casefold(), branch_clean.casefold())
        with _LOCK:
            pmap = _MAPS.pop(old_key, None)
            if pmap is None:
                pmap = get_partition_map(project_base, old_part, branch_name=old_branch, create=False)
            if pmap is None:
                return
            old_cache = pmap.cache_path
            new_cache = _cache_path_for(project_base, new_part, branch_name=old_branch, create=True)
            with pmap.lock:
                pmap.partition = new_part
                pmap.cache_path = new_cache
                try:
                    if os.path.isfile(old_cache):
                        os.replace(old_cache, new_cache)
                    else:
                        pmap.save()
                except OSError:
                    pmap.save()
            _MAPS[new_key] = pmap
        return

    pmap = get_partition_map(project_base, old_part, branch_name=old_branch, create=False)
    if pmap is None:
        return

    old_parent, _, old_name = old_sub.rpartition("/")
    new_parent, _, new_name = new_sub.rpartition("/")
    phys = new_physical or new_logical

    with pmap.lock:
        changed = False
        old_pinfo = pmap.dirs.get(old_parent)
        moved_entry = old_pinfo["entries"].pop(old_name, None) if old_pinfo else None
        if moved_entry is not None:
            changed = True
        if os.path.lexists(phys):
            try:
                st = os.lstat(phys)
                is_os_link = stat.S_ISLNK(st.st_mode)
                is_dir = stat.S_ISDIR(st.st_mode) and not is_os_link
                size = 0 if is_dir else int(st.st_size)
                mtime_ns = 0 if is_dir else int(st.st_mtime_ns)
                moved_entry, _ = _probe_disk_entry(
                    project_base,
                    phys,
                    new_name,
                    is_dir,
                    is_os_link,
                    size,
                    mtime_ns,
                    None,
                    include_magic=False,
                    target_cache={},
                )
            except OSError:
                if moved_entry is not None:
                    moved_entry = dict(moved_entry, name=new_name)
        elif moved_entry is not None:
            moved_entry = dict(moved_entry, name=new_name)

        new_pinfo = pmap.dirs.get(new_parent)
        if moved_entry is not None and new_pinfo is not None:
            new_pinfo["entries"][new_name] = moved_entry
            changed = True

        old_prefix = old_sub + "/"
        relocated_dirs = {}
        for k in list(pmap.dirs):
            if k == old_sub or k.startswith(old_prefix):
                relocated_dirs[new_sub + k[len(old_sub):]] = pmap.dirs.pop(k)
                changed = True
        pmap.dirs.update(relocated_dirs)

        if changed:
            pmap.save()


def _collect_symlink_fanout_dirs(
    pmap: PartitionMap,
    project_base: str,
    existence_changed_norm: set[str],
    target_cache: dict,
) -> set[str]:
    """Find cached directories whose symlinks point to any newly created/deleted path."""
    if not existence_changed_norm:
        return set()
    dirty_dirs: set[str] = set()
    part_root = os.path.join(project_base, pmap.partition)
    with pmap.lock:
        for d_rel, dinfo in pmap.dirs.items():
            link_dir = os.path.join(part_root, d_rel.replace("/", os.sep)) if d_rel else part_root
            for entry in dinfo.get("entries", {}).values():
                if not (entry.get("is_link") and entry.get("link_target")):
                    continue
                link_full = os.path.join(link_dir, entry["name"])
                sym = resolve_symlink_target(
                    link_full, entry["link_target"], project_base, target_cache=target_cache, collect_hops=True
                )
                if not sym:
                    continue
                check_paths = []
                if sym.get("resolved"):
                    check_paths.append(sym["resolved"])
                check_paths.extend(sym.get("hops") or ())
                hit = False
                for p in check_paths:
                    p_norm = os.path.normcase(os.path.normpath(p))
                    if p_norm in existence_changed_norm or any(
                        p_norm.startswith(ch + os.sep) for ch in existence_changed_norm
                    ):
                        hit = True
                        break
                if hit:
                    dirty_dirs.add(link_dir)
                    break
    return dirty_dirs


def sync_external_paths(
    project_base: str,
    changed_paths: list[str] | set[str] | tuple[str, ...],
    *,
    overflowed: bool = False,
) -> set[str] | None:
    """Apply batched external filesystem mutations to active `filesystem_map`s.

    Returns a set of affected parent directory paths for UI refresh, or `None`
    when the batch overflowed/exceeded threshold and all loaded dirs should refresh.
    """
    base_abs = os.path.abspath(os.path.normpath(project_base))
    if overflowed or len(changed_paths) > 256:
        norm_b = _norm_base(base_abs)
        with _LOCK:
            maps = [m for k, m in _MAPS.items() if k[0] == norm_b]
        for pmap in maps:
            if _is_partition_busy(base_abs, pmap.partition):
                continue
            with pmap.lock:
                for dinfo in pmap.dirs.values():
                    dinfo["dir_mtime_ns"] = 0
        return None

    dirty_parents: set[str] = set()
    by_partition: dict[str, dict[str, tuple[str, str]]] = {}
    base_norm = os.path.normcase(base_abs)

    for raw in changed_paths:
        if not raw:
            continue
        full_abs = os.path.abspath(os.path.normpath(raw))
        split = split_partition_rel(base_abs, full_abs)
        if split is None:
            if os.path.normcase(full_abs) == base_norm:
                dirty_parents.add(base_abs)
            else:
                parent_abs = os.path.dirname(full_abs)
                leaf = os.path.basename(full_abs).casefold()
                if (
                    os.path.normcase(parent_abs) == base_norm
                    and leaf
                    and not leaf.startswith(".")
                    and leaf not in _SKIP_ROOTS
                ):
                    dirty_parents.add(base_abs)
            continue
        partition, sub_rel = split
        if not sub_rel:
            dirty_parents.add(base_abs)
        by_partition.setdefault(partition, {})[os.path.normcase(full_abs)] = (sub_rel, full_abs)

    all_existence_changed: set[str] = set()
    target_cache: dict = {}
    touched_maps: list[PartitionMap] = []

    for partition, item_map in by_partition.items():
        if _is_partition_busy(base_abs, partition):
            continue
        for sub_rel, full_abs in item_map.values():
            parent_dir = os.path.dirname(full_abs)
            if parent_dir:
                dirty_parents.add(parent_dir)
            if not os.path.lexists(full_abs):
                dirty_parents.add(full_abs)

        active_branch, is_physical = branch_context(base_abs, partition)
        if active_branch is not None and not is_physical:
            continue

        pmap = get_partition_map(base_abs, partition, branch_name=active_branch, create=False)
        if pmap is None:
            continue
        touched_maps.append(pmap)

        existence_changed_norm: set[str] = set()
        remove_whole_partition = False

        with pmap.lock:
            changed = False
            for sub_rel, full_abs in item_map.values():
                if not sub_rel:
                    if not os.path.lexists(full_abs):
                        remove_whole_partition = True
                        dirty_parents.add(base_abs)
                        existence_changed_norm.add(os.path.normcase(full_abs))
                    continue

                parent_rel, _, name = sub_rel.rpartition("/")
                parent_info = pmap.dirs.get(parent_rel)

                if not os.path.lexists(full_abs):
                    existence_changed_norm.add(os.path.normcase(full_abs))
                    if parent_info is not None and name in parent_info["entries"]:
                        parent_info["entries"].pop(name, None)
                        changed = True
                    prefix = sub_rel + "/"
                    for k in [key for key in pmap.dirs if key == sub_rel or key.startswith(prefix)]:
                        pmap.dirs.pop(k, None)
                        changed = True
                    continue

                try:
                    st = os.lstat(full_abs)
                    is_os_link = stat.S_ISLNK(st.st_mode)
                    is_dir = stat.S_ISDIR(st.st_mode) and not is_os_link
                    size = 0 if is_dir else int(st.st_size)
                    mtime_ns = 0 if is_dir else int(st.st_mtime_ns)
                except OSError:
                    continue

                ent_dict, _ = _probe_disk_entry(
                    base_abs,
                    full_abs,
                    name,
                    is_dir,
                    is_os_link,
                    size,
                    mtime_ns,
                    None,
                    include_magic=False,
                    target_cache=target_cache,
                )
                if parent_info is not None:
                    if name not in parent_info["entries"]:
                        existence_changed_norm.add(os.path.normcase(full_abs))
                    if parent_info["entries"].get(name) != ent_dict:
                        parent_info["entries"][name] = ent_dict
                        changed = True
                    try:
                        parent_info["dir_mtime_ns"] = int(os.stat(os.path.dirname(full_abs)).st_mtime_ns)
                    except OSError:
                        pass
                else:
                    existence_changed_norm.add(os.path.normcase(full_abs))

            if changed and not remove_whole_partition:
                pmap.save()

        if remove_whole_partition:
            remove_branch_map(base_abs, partition, active_branch)
        else:
            dirty_parents.update(
                _collect_symlink_fanout_dirs(pmap, base_abs, existence_changed_norm, target_cache)
            )
        all_existence_changed.update(existence_changed_norm)

    # Also catch cross-partition broken/healed symlinks across other already-loaded active PartitionMaps.
    if all_existence_changed:
        norm_b = _norm_base(base_abs)
        touched_ids = {id(m) for m in touched_maps}
        with _LOCK:
            other_maps = [m for k, m in _MAPS.items() if k[0] == norm_b and id(m) not in touched_ids]
        for other in other_maps:
            if _is_partition_busy(base_abs, other.partition):
                continue
            active_b, is_phys = branch_context(base_abs, other.partition)
            if active_b is not None and not is_phys:
                continue
            if (other.branch_name or "").casefold() != (active_b or "").casefold():
                continue
            dirty_parents.update(
                _collect_symlink_fanout_dirs(other, base_abs, all_existence_changed, target_cache)
            )

    return dirty_parents
