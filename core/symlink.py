# SPDX-License-Identifier: MPL-2.0
import os
import sys
import stat
from concurrent.futures import ThreadPoolExecutor

HEADER = "!<symlink>"
MAX_SIZE = 512

_ROOT_CACHE = {}
_symlink_cache = {}
_TARGET_STAT_CACHE = {}  # normpath -> (exists: bool, is_dir: bool, is_file: bool)


def _fast_stat_candidate(path: str, cache=None):

    norm = os.path.normpath(path)
    cache = _TARGET_STAT_CACHE if cache is None else cache
    cached = cache.get(norm)
    if cached is not None:
        return cached
    try:
        st = os.stat(norm)
        mode = st.st_mode
        res = (True, stat.S_ISDIR(mode), stat.S_ISREG(mode))
    except OSError:
        res = (False, False, False)
    cache[norm] = res
    return res


def find_project_root(start_path):

    if not start_path:
        return start_path

    try:
        current = os.path.abspath(start_path)
        if os.path.isfile(current):
            current = os.path.dirname(current)

        if current in _ROOT_CACHE:
            return _ROOT_CACHE[current]

        orig = current
        indicators = {"system", "vendor", "product", "system_ext"}

        while True:
            try:
                entries = set(os.listdir(current))
                if any(name in entries for name in indicators):
                    _ROOT_CACHE[orig] = current
                    return current
            except Exception:
                pass

            parent = os.path.dirname(current)
            if parent == current:
                _ROOT_CACHE[orig] = start_path
                return start_path

            current = parent
    except Exception:
        return start_path


def is_candidate(file_path):
    try:
        return os.path.getsize(file_path) <= MAX_SIZE
    except Exception:
        return False


def read_symlink_target(file_path):
    """Decode the existing Android/Cygwin placeholder header without resolving it."""
    try:
        with open(file_path, "rb") as f:
            raw = f.read(MAX_SIZE + 1)

        if len(raw) > MAX_SIZE:
            return None

        # harus mulai dengan ASCII / UTF header !<symlink>
        if raw.startswith(b"!<symlink>"):
            remainder = raw[len(b"!<symlink>"):]
        elif raw.startswith(b"\xff\xfe") and b"!<symlink>" in raw:
            idx = raw.find(b"!<symlink>")
            remainder = raw[idx + len(b"!<symlink>"):]
        elif raw.startswith(b"!\x00<\x00s\x00y\x00m\x00l\x00i\x00n\x00k\x00>\x00"):
            remainder = raw[len(b"!\x00<\x00s\x00y\x00m\x00l\x00i\x00n\x00k\x00>\x00"):]
        else:
            return None

        # decode remainder
        if remainder.startswith(b"\xFF\xFE"):
            target = remainder[2:].decode("utf-16le", errors="ignore")
        elif remainder.startswith(b"\xFE\xFF"):
            target = remainder[2:].decode("utf-16be", errors="ignore")
        elif remainder.startswith(b"\xEF\xBB\xBF"):
            target = remainder[3:].decode("utf-8", errors="ignore")
        else:
            if len(remainder) >= 2 and remainder[1:2] == b"\x00":
                target = remainder.decode("utf-16le", errors="ignore")
            else:
                try:
                    target = remainder.decode("utf-8")
                except Exception:
                    target = remainder.decode("utf-16le", errors="ignore")

        target = target.replace("\x00", "").strip()
        if not target:
            return None

        return target
    except (OSError, UnicodeError):
        return None


def _build_target_candidates(file_path, norm_target, project_root):
    clean = norm_target.lstrip("/")
    base_dir = os.path.dirname(file_path)
    candidates = []

    if norm_target.startswith("/"):
        # SAR handling: misal target /system/bin dan project_root adalah D:\...\target\system
        if clean.startswith("system/"):
            candidates.append(os.path.normpath(os.path.join(project_root, clean)))
            sub = clean[7:]
            if sub:
                candidates.append(os.path.normpath(os.path.join(project_root, sub)))
            candidates.append(os.path.normpath(os.path.join(project_root, "system", clean)))
        else:
            candidates.append(os.path.normpath(os.path.join(project_root, clean)))
            candidates.append(os.path.normpath(os.path.join(project_root, "system", clean)))
            parent = os.path.dirname(project_root)
            if parent and parent != project_root:
                candidates.append(os.path.normpath(os.path.join(parent, clean)))
    else:
        candidates.append(os.path.normpath(os.path.join(base_dir, norm_target)))
        candidates.append(os.path.normpath(os.path.join(project_root, norm_target)))
        candidates.append(os.path.normpath(os.path.join(project_root, "system", norm_target)))

    return list(dict.fromkeys(candidates))


def _inspect_node(path: str, cache=None):
    """Return `(exists, is_dir, is_file, symlink_target_or_none)` without following `path`."""
    norm = os.path.normpath(path)
    store = _TARGET_STAT_CACHE if cache is None else cache
    key = ("__node__", norm)
    cached = store.get(key)
    if cached is not None:
        return cached
    try:
        st = os.lstat(norm)
        mode = st.st_mode
        if stat.S_ISLNK(mode):
            try:
                link_t = os.readlink(norm)
            except OSError:
                link_t = ""
            res = (True, False, False, link_t or None)
        elif stat.S_ISDIR(mode):
            res = (True, True, False, None)
        elif stat.S_ISREG(mode):
            link_t = read_symlink_target(norm) if st.st_size <= MAX_SIZE else None
            if link_t:
                res = (True, False, False, link_t)
            else:
                res = (True, False, True, None)
        else:
            res = (True, False, False, None)
    except OSError:
        res = (False, False, False, None)
    store[key] = res
    return res


def _walk_root_for(cand: str, project_root: str):
    proj_abs = os.path.normpath(project_root)
    parent_abs = os.path.dirname(proj_abs)
    for root_cand in (proj_abs, parent_abs):
        if not root_cand:
            continue
        try:
            if os.path.normcase(os.path.commonpath([cand, root_cand])) == os.path.normcase(root_cand):
                return root_cand
        except ValueError:
            continue
    return None


def _resolve_candidate_chain(
    candidate: str,
    project_root: str,
    cache,
    visited: frozenset,
    depth: int,
    hops_out: list | None = None,
):
    cand = os.path.normpath(candidate)
    if depth > 24:
        return (False, False, False, cand)

    # Fast path: single lstat check resolves ordinary files/directories directly.
    ex, is_d, is_f, link_t = _inspect_node(cand, cache)
    if ex and link_t is None:
        return (True, is_d, is_f, cand)

    if ex and link_t is not None:
        key = os.path.normcase(cand)
        if key in visited:
            return (False, False, False, cand)
        if hops_out is not None:
            hops_out.append(cand)
        next_visited = visited | {key}
        norm_lt = link_t.replace("\\", "/").strip()
        best_missing = None
        for nc in _build_target_candidates(cand, norm_lt, project_root):
            if os.path.normcase(nc) == key:
                continue
            sub_ex, sub_d, sub_f, sub_res = _resolve_candidate_chain(
                nc, project_root, cache, next_visited, depth + 1, hops_out
            )
            if sub_ex:
                return (True, sub_d, sub_f, sub_res)
            if best_missing is None:
                best_missing = sub_res
        if os.path.islink(cand):
            try:
                st = os.stat(cand)
                return (
                    True,
                    stat.S_ISDIR(st.st_mode),
                    stat.S_ISREG(st.st_mode),
                    os.path.normpath(os.path.realpath(cand)),
                )
            except OSError:
                pass
        return (False, False, False, best_missing or cand)

    # Intermediate-symlink component walk (only reached when `cand` does not exist directly).
    walk_root = _walk_root_for(cand, project_root)
    if not walk_root:
        return (False, False, False, cand)
    rel = os.path.relpath(cand, walk_root)
    if rel in (".", "") or rel.startswith(".."):
        return (False, False, False, cand)
    parts = rel.split(os.sep)
    cand_key = os.path.normcase(cand)
    for i in range(len(parts) - 1):
        cur = os.path.normpath(os.path.join(walk_root, *parts[: i + 1]))
        c_ex, c_dir, _c_file, c_link = _inspect_node(cur, cache)
        if not c_ex:
            break
        if c_dir and c_link is None:
            continue
        if c_link is None:
            break
        key = os.path.normcase(cur)
        if key in visited:
            return (False, False, False, cand)
        if hops_out is not None:
            hops_out.append(cur)
        tail = parts[i + 1 :]
        next_visited = visited | {key}
        norm_lt = c_link.replace("\\", "/").strip()
        best_missing = None
        for next_base in _build_target_candidates(cur, norm_lt, project_root):
            combined = os.path.normpath(os.path.join(next_base, *tail))
            if os.path.normcase(combined) == cand_key:
                continue
            sub_ex, sub_d, sub_f, sub_res = _resolve_candidate_chain(
                combined, project_root, cache, next_visited, depth + 1, hops_out
            )
            if sub_ex:
                return (True, sub_d, sub_f, sub_res)
            if best_missing is None:
                best_missing = sub_res
        return (False, False, False, best_missing or cand)

    return (False, False, False, cand)


def resolve_symlink_target(file_path, target, project_root, *, target_cache=None, collect_hops=False):
    """Resolve an already-known Android symlink target against `project_root`."""
    try:
        if not target:
            return None

        norm_target = target.replace("\\", "/").strip()
        clean = norm_target.lstrip("/")
        candidates = _build_target_candidates(file_path, norm_target, project_root)

        norm_file = os.path.normpath(file_path)
        norm_file_key = os.path.normcase(norm_file)
        visited = frozenset({norm_file_key})
        hops = [] if collect_hops else None

        resolved = None
        fallback_resolved = None
        exists = False
        is_dir = False
        is_file = False

        # 1. Cari kandidat yang eksis (termasuk lewat intermediate / chained symlink)
        for c in candidates:
            if os.path.normcase(c) == norm_file_key:
                continue
            ex, is_d, is_f, final_path = _resolve_candidate_chain(
                c, project_root, target_cache, visited, 0, hops
            )
            if ex:
                resolved = final_path
                exists = True
                is_dir = is_d
                is_file = is_f
                break
            if fallback_resolved is None:
                fallback_resolved = final_path
            elif fallback_resolved in candidates and final_path != c:
                fallback_resolved = final_path

        # 2. Fallback jika tidak ada kandidat yang eksis
        if not resolved:
            if fallback_resolved:
                resolved = fallback_resolved
            else:
                for c in candidates:
                    if os.path.normcase(c) != norm_file_key:
                        resolved = c
                        break
            if not resolved:
                resolved = candidates[0] if candidates else os.path.normpath(os.path.join(project_root, clean))

        if not exists:

            # Fallback heuristic untuk broken / unmounted partition
            clean_t = norm_target.rstrip("/")
            last_part = clean_t.split("/")[-1].lower() if clean_t else ""
            name_lower = os.path.basename(file_path).lower()

            if norm_target.endswith("/"):
                is_dir = True
            elif "." in last_part or "." in name_lower:
                is_file = True
            elif any(p in norm_target.lower() for p in ("/bin/", "/xbin/", "/sbin/")):
                is_file = True
            else:
                is_dir = True

        result = {
            "file": file_path,
            "target": target,
            "resolved": resolved,
            "exists": exists,
            "is_dir": is_dir,
            "is_file": is_file,
        }
        if collect_hops:
            result["hops"] = list(dict.fromkeys(hops))
        return result

    except Exception:
        pass

    return None


def check_symlink(file_path, project_root, *, target_cache=None):
    try:
        target = read_symlink_target(file_path)
        if not target and os.path.islink(file_path):
            target = os.readlink(file_path)
        if not target:
            return None
        return resolve_symlink_target(file_path, target, project_root, target_cache=target_cache)
    except Exception:
        pass
    return None


def clear_symlink_cache():
    global _symlink_cache, _ROOT_CACHE, _TARGET_STAT_CACHE
    _symlink_cache.clear()
    _ROOT_CACHE.clear()
    _TARGET_STAT_CACHE.clear()


def invalidate_symlink_cache(path=None):
    global _symlink_cache, _ROOT_CACHE, _TARGET_STAT_CACHE
    if not path:
        clear_symlink_cache()
        return
    norm = os.path.normpath(path)
    to_del = [k for k in _symlink_cache if k == norm or k.startswith(norm + os.sep)]
    for k in to_del:
        _symlink_cache.pop(k, None)

    to_del_root = [k for k in _ROOT_CACHE if k == norm or k.startswith(norm + os.sep)]
    for k in to_del_root:
        _ROOT_CACHE.pop(k, None)

    to_del_target = [
        k for k in _TARGET_STAT_CACHE
        if (isinstance(k, str) and (k == norm or k.startswith(norm + os.sep)))
        or (isinstance(k, tuple) and len(k) == 2 and (k[1] == norm or k[1].startswith(norm + os.sep)))
    ]
    for k in to_del_target:
        _TARGET_STAT_CACHE.pop(k, None)


def resolve_symlink(file_path, project_root=None, reload=False):

    global _symlink_cache

    file_path = os.path.normpath(file_path)

    if not reload and file_path in _symlink_cache:
        return _symlink_cache[file_path]

    if not project_root:
        project_root = find_project_root(file_path)

    result = check_symlink(file_path, project_root)

    _symlink_cache[file_path] = result
    return result


def is_symlink_placeholder(file_path):
    data = resolve_symlink(file_path)
    return data is not None


def batch_resolve_symlinks(file_paths, project_root=None, max_workers=None):

    global _symlink_cache

    if not file_paths:
        return {}

    # Pisahkan yang belum ada di cache
    uncached = []
    for p in file_paths:
        norm = os.path.normpath(p)
        if norm not in _symlink_cache:
            uncached.append(p)

    if not uncached:
        return {os.path.normpath(p): _symlink_cache.get(os.path.normpath(p)) for p in file_paths}

    if not project_root:
        project_root = find_project_root(uncached[0])

    from core.native_backend import _get_native, resolve_symlinks
    if _get_native() is not None:
        _symlink_cache.update(resolve_symlinks(uncached, project_root))
        return {os.path.normpath(p): _symlink_cache.get(os.path.normpath(p)) for p in file_paths}

    # Untuk jumlah sedikit (<= 8), langsung sequential tanpa overhead ThreadPool
    if len(uncached) <= 8:
        for p in uncached:
            norm = os.path.normpath(p)
            _symlink_cache[norm] = check_symlink(p, project_root)
    else:
        # Multi-threaded parallel resolution
        if max_workers is None or max_workers <= 0:
            try:
                from core.storage_detector import get_optimal_io_workers
                max_workers = get_optimal_io_workers(uncached[0], max_cap=16, hdd_cap=4)
            except Exception:
                max_workers = min(8, os.cpu_count() or 2)

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(check_symlink, p, project_root): p for p in uncached}
            for fut in futures:
                p = futures[fut]
                norm = os.path.normpath(p)
                try:
                    res = fut.result()
                except Exception:
                    res = None
                _symlink_cache[norm] = res

    return {os.path.normpath(p): _symlink_cache.get(os.path.normpath(p)) for p in file_paths}


def scan_folder(start_path, verbose=True):

    if verbose:
        print(f"\n[SYMLINK] Scanning from: {start_path}")

    project_root = find_project_root(start_path)
    if verbose:
        print(f"[SYMLINK] Detected project root: {project_root}")

    # Fast scandir collector (menghindari os.path.getsize stat berulang)
    candidates = []

    def _walk_scandir(folder):
        try:
            with os.scandir(folder) as it:
                for entry in it:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            _walk_scandir(entry.path)
                        elif entry.stat(follow_symlinks=False).st_size <= MAX_SIZE:
                            candidates.append(entry.path)
                    except Exception:
                        pass
        except Exception:
            pass

    _walk_scandir(start_path)

    if verbose:
        print(f"[SYMLINK] Candidates (<= {MAX_SIZE} bytes): {len(candidates)}")

    results = batch_resolve_symlinks(candidates, project_root)
    valid_symlinks = [v for v in results.values() if v is not None]

    if verbose:
        for s in valid_symlinks:
            print("=" * 60)
            print("SYMLINK FILE :", s["file"])
            print("TARGET       :", s["target"])
            print("RESOLVED TO  :", s["resolved"])
            print("STATUS       :", "VALID" if s["exists"] else "BROKEN")
            print("=" * 60)

        print(f"\nTotal symlinks found: {len(valid_symlinks)}")

    return valid_symlinks


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python symlink.py <path>")
        sys.exit(1)

    scan_folder(sys.argv[1])
