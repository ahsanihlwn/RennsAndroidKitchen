# SPDX-License-Identifier: MPL-2.0
import os
import sys
import json
import shutil
import re
from core.app_paths import neocat_cache_dir, neocat_context_path
from core.native_backend import (
    parse_metadata_files,
    prescan_json as _native_prescan_json,
    insert_config_lines,
)

# PARSING HELPERS

def _load_metadata_directory(cfgdir, suffix, kind, *, ignore_errors=False):
    if not os.path.isdir(cfgdir):
        return {}
    paths = [os.path.join(cfgdir, name) for name in os.listdir(cfgdir) if name.endswith(suffix)]
    return parse_metadata_files(paths, kind, ignore_errors=ignore_errors)


def load_file_context(basepath):

    cfgdir = os.path.join(basepath, "config")
    if not os.path.isdir(cfgdir):
        os.makedirs(cfgdir, exist_ok=True)
        return {}
    return _load_metadata_directory(cfgdir, "_file_contexts", "contexts")


def load_fs_config(basepath):

    cfgdir = os.path.join(basepath, "config")
    if not os.path.isdir(cfgdir):
        os.makedirs(cfgdir, exist_ok=True)
        return {}

    # Auto-ensure _fs_config for boot/vendor_boot if missing
    try:
        from core.boot_image import ensure_boot_fs_config
        for fname in os.listdir(cfgdir):
            if fname.endswith("_bootconf"):
                bname = fname[:-len("_bootconf")]
                if not os.path.isfile(os.path.join(cfgdir, f"{bname}_fs_config")):
                    ensure_boot_fs_config(basepath, bname)
    except Exception:
        pass

    return _load_metadata_directory(cfgdir, "_fs_config", "fs_config")


def perm_to_rwx(o):
    try:
        p = int(o, 8)
    except:
        p = 0o644

    f = [
        (p & 0o400, 'r'), (p & 0o200, 'w'), (p & 0o100, 'x'),
        (p & 0o040, 'r'), (p & 0o020, 'w'), (p & 0o010, 'x'),
        (p & 0o004, 'r'), (p & 0o002, 'w'), (p & 0o001, 'x'),
    ]
    return ''.join(s if flag else '-' for flag, s in f)


def prescan_task(base, recursive=False):

    native = _native_prescan_json(base, recursive=recursive, full=True)
    if native is not None:
        return True, native
    folders = {}
    fscache = {}
    child_snapshot = {}

    def _record_dir(root, dirnames, filenames):
        try:
            norm = os.path.normpath(root)
        except Exception:
            norm = root
        folders[norm] = (len(dirnames) > 0 or len(filenames) > 0)
        child_snapshot[norm] = [
            os.path.normpath(os.path.join(root, d)) for d in dirnames
        ]
        for fn in filenames:
            if fn.lower().endswith(".img"):
                full = os.path.join(root, fn)
                try:
                    st = os.stat(full)
                    mtime = int(st.st_mtime)
                    size = st.st_size
                except Exception:
                    mtime = 0
                    size = 0
                from core.header_magic import check_magic
                try:
                    fs_type = check_magic(full)
                except Exception:
                    fs_type = ""
                fscache[os.path.normpath(full)] = {
                    "mtime": mtime, "size": size, "fs_type": fs_type
                }

    if recursive:
        for root, dirnames, filenames in os.walk(base):
            _record_dir(root, dirnames, filenames)
    else:
        def _split(p):
            dn, fn = [], []
            try:
                with os.scandir(p) as it:
                    for e in it:
                        try:
                            (dn if e.is_dir(follow_symlinks=False) else fn).append(e.name)
                        except OSError:
                            fn.append(e.name)
            except OSError:
                pass
            return dn, fn
        top_dirs, top_files = _split(base)
        _record_dir(base, top_dirs, top_files)
        for d in top_dirs:
            sub = os.path.join(base, d)
            sd, sf = _split(sub)
            _record_dir(sub, sd, sf)

    payload = {
        "folders": folders,
        "fscache": fscache,
        "child_snapshot": child_snapshot,
    }
    try:
        s = json.dumps(payload)
    except Exception:
        s = "{}"
    return True, s


def load_neocat_ctx(base):

    def _load_dir(cache_dir):
        return _load_metadata_directory(cache_dir, "_file_contexts", "contexts")

    neocat_dir = neocat_cache_dir(base)
    neocat = _load_dir(neocat_dir)

    nb_root = os.path.join(base, "neobranch")
    if os.path.isdir(nb_root):
        for fs_root_name in os.listdir(nb_root):
            fs_root_dir = os.path.join(nb_root, fs_root_name)
            state_path = os.path.join(fs_root_dir, "neobranch_state.json")
            if not os.path.isfile(state_path):
                continue
            try:
                with open(state_path, "r", encoding="utf-8") as _f:
                    active_branch = json.load(_f).get("active_branch")
            except Exception:
                continue
            if not active_branch:
                continue
            branch_neocat = neocat_cache_dir(base, fs_root_name, active_branch)
            neocat.update(_load_dir(branch_neocat))
    print(f"[NEOCAT] loaded {len(neocat)} AppData cache entries for {os.path.basename(base)}")
    return neocat


def _load_ctx_from_dir(cfg_dir):

    return _load_metadata_directory(cfg_dir, "_file_contexts", "contexts", ignore_errors=True)


def _load_fs_from_dir(cfg_dir):

    return _load_metadata_directory(cfg_dir, "_fs_config", "fs_config", ignore_errors=True)


def nb_overlay(base, ctx, fs):

    nb_root = os.path.join(base, "neobranch")
    if not os.path.isdir(nb_root):
        return ctx, fs
    for fs_root_name in os.listdir(nb_root):
        fs_root_dir = os.path.join(nb_root, fs_root_name)
        if not os.path.isdir(fs_root_dir):
            continue
        state_path = os.path.join(fs_root_dir, "neobranch_state.json")
        if not os.path.isfile(state_path):
            continue
        try:
            with open(state_path, "r", encoding="utf-8") as _f:
                active_branch = json.load(_f).get("active_branch")
        except Exception:
            continue
        if not active_branch:
            continue
        branch_cfg = os.path.join(fs_root_dir, f"branch_{active_branch}", "config")
        if not os.path.isdir(branch_cfg):
            continue
        ctx.update(_load_ctx_from_dir(branch_cfg))
        fs.update(_load_fs_from_dir(branch_cfg))
        print(f"[NeoBranch] overlay: {fs_root_name!r} -> {active_branch!r}")
    return ctx, fs


def load_full_state(base):

    ctx = load_file_context(base)
    fs = load_fs_config(base)
    neocat = load_neocat_ctx(base)
    ctx, fs = nb_overlay(base, ctx, fs)
    return {"ctx": ctx, "fs": fs, "neocat": neocat}


# CONSTANTS

DEFAULT_CTX = "u:object_r:system_file:s0"
DEFAULT_PERM = "0777"


# ENGINE CLASS

class ConfigEngine:
    def __init__(self, base):
        self.base = base
        self.cfgdir = os.path.join(base, "config")

        self.root_map_ctx = {}
        self.root_map_fs = {}

        self._scan_root_files()

    def _scan_root_files(self):
        os.makedirs(self.cfgdir, exist_ok=True)
        for fname in os.listdir(self.cfgdir):
            full = os.path.join(self.cfgdir, fname)

            if fname.endswith("_file_contexts"):
                root = fname.replace("_file_contexts", "")
                self.root_map_ctx[root] = fname

            elif fname.endswith("_fs_config"):
                root = fname.replace("_fs_config", "")
                self.root_map_fs[root] = fname

    def _has_valid_line(self, filepath):
        with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
            for ln in f:
                s = ln.strip()
                if s and not s.startswith("#"):
                    return True
        return False

    def _get_root(self, rel):
        return rel.split("/")[0]

    def escaped_ctx_key(self, rel):
        parts = rel.replace("\\", "/").split("/")
        parts = [re.escape(p) for p in parts]
        return "/" + "/".join(parts)

    def infer_permission(self, rel):
        full = os.path.join(self.base, rel).replace("\\", "/")
        rel = rel.replace("\\", "/")

        if os.path.isdir(full):
            uid = "0"
            gid = "2000" if ("system/bin" in rel or "system/xbin" in rel or "vendor/bin" in rel) else "0"
            return uid, gid, "0755"

        link = None
        if os.path.isfile(full):
            try:
                import core.fspatch as fp
                link = fp.islink(full)
            except:
                link = None

        if link:
            uid = "0"
            gid = "2000" if ("/system/bin" in rel or "/system/xbin" in rel or "/vendor/bin" in rel) else "0"

            if "/bin" in rel or "/xbin" in rel:
                mode = "0755"
            elif rel.endswith(".sh"):
                mode = "0750"
            else:
                mode = "0644"

            return uid, gid, mode

        if "/bin/" in rel or "/xbin/" in rel:
            uid = "0"
            gid = "2000" if ("system/bin" in rel or "vendor/bin" in rel or "system/xbin" in rel) else "0"
            mode = "0755"
            if rel.endswith(".sh"):
                mode = "0750"
            return uid, gid, mode

        return "0", "0", "0644"

    def apply(self, rel):
        rel = rel.replace("\\", "/").strip("/")

        root = self._get_root(rel)

        if root not in self.root_map_ctx or root not in self.root_map_fs:
            self.rebuild_root_map()

        if root not in self.root_map_ctx or root not in self.root_map_fs:
            return

        ctx_file = os.path.join(self.cfgdir, self.root_map_ctx[root])
        fs_file = os.path.join(self.cfgdir, self.root_map_fs[root])

        ctx_key = self.escaped_ctx_key(rel)
        fs_key = rel

        # FILE CONTEXTS
        with open(ctx_file, "r", encoding="utf-8", errors="ignore") as f:
            lines = [ln.rstrip("\n") for ln in f]

        if not any(ln.startswith(ctx_key + " ") for ln in lines):
            final = []
            inserted = False
            for ln in lines:
                s = ln.strip()
                if s and not s.startswith("#"):
                    key = s.split()[0]
                    if not inserted and ctx_key < key:
                        final.append(f"{ctx_key} {DEFAULT_CTX}")
                        inserted = True
                final.append(ln)
            if not inserted:
                final.append(f"{ctx_key} {DEFAULT_CTX}")

            with open(ctx_file, "w", encoding="utf-8", errors="ignore") as f:
                f.write("\n".join(final) + "\n")

        # FS CONFIG
        with open(fs_file, "r", encoding="utf-8", errors="ignore") as f:
            lines = [ln.rstrip("\n") for ln in f]

        if not any(ln.split()[0] == fs_key for ln in lines if ln.strip() and not ln.startswith("#")):

            inferred = self.infer_permission(fs_key)

            if len(inferred) == 4:
                uid, gid, mode, *_ = inferred
                new_line = f"{fs_key} {uid} {gid} {mode}"
            else:
                uid, gid, mode = inferred
                new_line = f"{fs_key} {uid} {gid} {mode}"

            final = []
            inserted = False

            for ln in lines:
                s = ln.strip()
                if s and not s.startswith("#"):
                    key = s.split()[0]
                    if not inserted and fs_key < key:
                        final.append(new_line)
                        inserted = True
                final.append(ln)

            if not inserted:
                final.append(new_line)

            with open(fs_file, "w", encoding="utf-8") as f:
                f.write("\n".join(final) + "\n")

        # refresh cache internal
        self.ctx = load_file_context(self.base)
        self.fs = load_fs_config(self.base)

    def move_paths(self, rel_old, rel_new):

        rel_old = rel_old.strip("/").replace("\\", "/")
        rel_new = rel_new.strip("/").replace("\\", "/")

        old_root = self._get_root(rel_old)
        new_root = self._get_root(rel_new)

        if old_root not in self.root_map_ctx or old_root not in self.root_map_fs:
            return

        if new_root not in self.root_map_ctx or new_root not in self.root_map_fs:
            remove_only = False
            ctx_dst_path = os.path.join(self.cfgdir, self.root_map_ctx.get(old_root, ""))
            fs_dst_path = os.path.join(self.cfgdir, self.root_map_fs.get(old_root, ""))
            _dst_override = True
        else:
            remove_only = False
            ctx_dst_path = os.path.join(self.cfgdir, self.root_map_ctx.get(new_root, ""))
            fs_dst_path = os.path.join(self.cfgdir, self.root_map_fs.get(new_root, ""))
            _dst_override = False

        ctx_src = os.path.join(self.cfgdir, self.root_map_ctx.get(old_root, ""))
        fs_src = os.path.join(self.cfgdir, self.root_map_fs.get(old_root, ""))

        ctx_dst = ctx_dst_path
        fs_dst = fs_dst_path
        fs_dst = os.path.join(self.cfgdir, self.root_map_fs.get(new_root, "")) if not remove_only else None

        old_ctx_prefix = self.escaped_ctx_key(rel_old)
        old_fs_prefix = rel_old

        def read_ctx_lines(path):
            if not path or not os.path.isfile(path):
                return []
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                lines = [ln.rstrip("\n") for ln in f]
            parsed = []
            for ln in lines:
                s = ln.strip()
                if not s:
                    parsed.append((ln, None, None))
                    continue
                if s.startswith("#"):
                    parsed.append((ln, None, None))
                    continue
                parts = s.split(None, 1)
                key = parts[0]
                rest = parts[1] if len(parts) > 1 else ""
                parsed.append((ln, key, rest))
            return parsed

        def read_fs_lines(path):
            if not path or not os.path.isfile(path):
                return []
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                lines = [ln.rstrip("\n") for ln in f]
            parsed = []
            for ln in lines:
                s = ln.strip()
                if not s:
                    parsed.append((ln, None, None))
                    continue
                if s.startswith("#"):
                    parsed.append((ln, None, None))
                    continue
                parts = s.split(None, 3)
                key = parts[0]
                rest = " ".join(parts[1:]) if len(parts) > 1 else ""
                parsed.append((ln, key, rest))
            return parsed

        ctx_lines = read_ctx_lines(ctx_src)
        fs_lines = read_fs_lines(fs_src)

        ctx_to_move = []
        for idx, (raw, key, rest) in enumerate(ctx_lines):
            if key and (key == old_ctx_prefix or key.startswith(old_ctx_prefix + "/")):
                ctx_to_move.append((idx, key, rest))

        fs_to_move = []
        for idx, (raw, key, rest) in enumerate(fs_lines):
            if key and (key == old_fs_prefix or key.startswith(old_fs_prefix + "/")):
                fs_to_move.append((idx, key, rest))

        mapping_ctx = {}
        for _, key, rest in ctx_to_move:
            suffix = key[len(old_ctx_prefix):]
            prefix_new = self.escaped_ctx_key(rel_new)
            new_key = prefix_new + suffix
            mapping_ctx[key] = new_key

        mapping_fs = {}
        for _, key, rest in fs_to_move:
            suffix = key[len(old_fs_prefix):]
            new_key = rel_new + suffix
            mapping_fs[key] = new_key

        def remove_indexes(lines, idxs):
            idx_set = set(i for i, *_ in idxs)
            return [t for i, t in enumerate(lines) if i not in idx_set]

        ctx_lines_remaining = remove_indexes(ctx_lines, ctx_to_move)
        fs_lines_remaining = remove_indexes(fs_lines, fs_to_move)

        if ctx_src and os.path.isfile(ctx_src):
            with open(ctx_src, "w", encoding="utf-8", errors="ignore") as f:
                f.write("\n".join([ln for ln, _, _ in ctx_lines_remaining]) + ("\n" if ctx_lines_remaining else ""))

        if fs_src and os.path.isfile(fs_src):
            with open(fs_src, "w", encoding="utf-8", errors="ignore") as f:
                f.write("\n".join([ln for ln, _, _ in fs_lines_remaining]) + ("\n" if fs_lines_remaining else ""))

        def insert_sorted_into_file(target_path, new_entries, is_ctx=True):
            if not target_path:
                return
            with open(target_path, "r", encoding="utf-8", errors="ignore") as f:
                lines = [ln.rstrip("\n") for ln in f]

            final_text = insert_config_lines(lines, new_entries, skip_existing=False)

            with open(target_path, "w", encoding="utf-8", errors="ignore") as f:
                f.write(final_text)

        if True:
            ctx_new_entries = []
            old_ctx_map = {key: rest for _, key, rest in ctx_to_move}
            for oldk, newk in mapping_ctx.items():
                rest = old_ctx_map.get(oldk, DEFAULT_CTX)
                rest = rest if rest else DEFAULT_CTX
                ctx_new_entries.append((newk, rest))

            fs_new_entries = []
            old_fs_map = {key: rest for _, key, rest in fs_to_move}
            for oldk, newk in mapping_fs.items():
                rest = old_fs_map.get(oldk, "").strip()
                rest = rest if rest else "0 0 " + DEFAULT_PERM
                fs_new_entries.append((newk, rest))

            if ctx_dst:
                insert_sorted_into_file(ctx_dst, ctx_new_entries, is_ctx=True)
            if fs_dst:
                insert_sorted_into_file(fs_dst, fs_new_entries, is_ctx=False)

        self.ctx = load_file_context(self.base)
        self.fs = load_fs_config(self.base)

    def move_paths_cross(self, src_engine, rel_old, rel_new):

        rel_old = rel_old.strip("/").replace("\\", "/")
        rel_new = rel_new.strip("/").replace("\\", "/")

        old_root = rel_old.split("/")[0]
        new_root = rel_new.split("/")[0]

        src_cfgdir = src_engine.cfgdir
        dst_cfgdir = self.cfgdir

        ctx_src = os.path.join(src_cfgdir, old_root + "_file_contexts")
        fs_src = os.path.join(src_cfgdir, old_root + "_fs_config")
        ctx_dst = os.path.join(dst_cfgdir, new_root + "_file_contexts")
        fs_dst = os.path.join(dst_cfgdir, new_root + "_fs_config")

        if not os.path.isfile(ctx_src) and not os.path.isfile(fs_src):
            return
        if not os.path.isfile(ctx_dst):
            open(ctx_dst, "w").close()
        if not os.path.isfile(fs_dst):
            open(fs_dst, "w").close()

        old_ctx_prefix = self.escaped_ctx_key(rel_old)
        old_fs_prefix = rel_old

        def _read_lines(path, is_ctx):
            if not path or not os.path.isfile(path):
                return []
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                lines = [ln.rstrip("\n") for ln in f]
            parsed = []
            for ln in lines:
                s = ln.strip()
                if not s or s.startswith("#"):
                    parsed.append((ln, None, None))
                    continue
                parts = s.split(None, 1) if is_ctx else s.split(None, 3)
                key = parts[0]
                rest = (parts[1] if len(parts) > 1 else "") if is_ctx else (" ".join(parts[1:]) if len(parts) > 1 else "")
                parsed.append((ln, key, rest))
            return parsed

        ctx_lines = _read_lines(ctx_src, is_ctx=True)
        fs_lines = _read_lines(fs_src, is_ctx=False)

        ctx_to_move = [(i, k, r) for i, (_, k, r) in enumerate(ctx_lines)
                       if k and (k == old_ctx_prefix or k.startswith(old_ctx_prefix + "/"))]
        fs_to_move = [(i, k, r) for i, (_, k, r) in enumerate(fs_lines)
                      if k and (k == old_fs_prefix or k.startswith(old_fs_prefix + "/"))]

        new_ctx_prefix = self.escaped_ctx_key(rel_new)
        ctx_new_entries = [
            (new_ctx_prefix + k[len(old_ctx_prefix):], r or DEFAULT_CTX)
            for _, k, r in ctx_to_move
        ]
        fs_new_entries = [
            (rel_new + k[len(old_fs_prefix):], r.strip() or f"0 0 {DEFAULT_PERM}")
            for _, k, r in fs_to_move
        ]

        def _remove_and_write(path, lines, to_remove):
            idx_set = {i for i, *_ in to_remove}
            remaining = [ln for i, (ln, _, _) in enumerate(lines) if i not in idx_set]
            with open(path, "w", encoding="utf-8", errors="ignore") as f:
                f.write("\n".join(remaining) + ("\n" if remaining else ""))

        if ctx_to_move and os.path.isfile(ctx_src):
            _remove_and_write(ctx_src, ctx_lines, ctx_to_move)
        if fs_to_move and os.path.isfile(fs_src):
            _remove_and_write(fs_src, fs_lines, fs_to_move)

        def _insert_sorted(path, new_entries):
            if not path or not os.path.isfile(path) or not new_entries:
                return
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                lines = [ln.rstrip("\n") for ln in f]
            final_text = insert_config_lines(lines, new_entries, skip_existing=True)
            with open(path, "w", encoding="utf-8", errors="ignore") as f:
                f.write(final_text)

        _insert_sorted(ctx_dst, ctx_new_entries)
        _insert_sorted(fs_dst, fs_new_entries)

        src_engine.ctx = load_file_context(src_engine.base)
        src_engine.fs = load_fs_config(src_engine.base)
        self.ctx = load_file_context(self.base)
        self.fs = load_fs_config(self.base)

    def delete_paths(self, rel):
        rel = rel.strip("/").replace("\\", "/")
        root = self._get_root(rel)

        if root not in self.root_map_ctx and root not in self.root_map_fs:
            return

        ctx_file = os.path.join(self.cfgdir, self.root_map_ctx.get(root, "")) if root in self.root_map_ctx else None
        fs_file = os.path.join(self.cfgdir, self.root_map_fs.get(root, "")) if root in self.root_map_fs else None

        if ctx_file and os.path.isfile(ctx_file):
            try:
                with open(ctx_file, "r", encoding="utf-8", errors="ignore") as f:
                    lines = [ln.rstrip("\n") for ln in f]
                out_lines = []
                prefix = self.escaped_ctx_key(rel)
                for ln in lines:
                    s = ln.strip()
                    if not s or s.startswith("#"):
                        out_lines.append(ln)
                        continue
                    key = s.split()[0]
                    if not (key == prefix or key.startswith(prefix + "/")):
                        out_lines.append(ln)
                with open(ctx_file, "w", encoding="utf-8", errors="ignore") as f:
                    f.write("\n".join(out_lines) + ("\n" if out_lines else ""))
            except Exception as e:
                print("WARN delete_paths(ctx):", e)

        if fs_file and os.path.isfile(fs_file):
            try:
                with open(fs_file, "r", encoding="utf-8", errors="ignore") as f:
                    lines = [ln.rstrip("\n") for ln in f]
                out_lines = []
                prefix = rel
                for ln in lines:
                    s = ln.strip()
                    if not s or s.startswith("#"):
                        out_lines.append(ln)
                        continue
                    parts = s.split()
                    key = parts[0]
                    if not (key == prefix or key.startswith(prefix + "/")):
                        out_lines.append(ln)
                with open(fs_file, "w", encoding="utf-8", errors="ignore") as f:
                    f.write("\n".join(out_lines) + ("\n" if out_lines else ""))
            except Exception as e:
                print("WARN delete_paths(fs):", e)

        self.ctx = load_file_context(self.base)
        self.fs = load_fs_config(self.base)

    def apply_context_only(self, rel):
        root = self._get_root(rel)


        if root not in self.root_map_ctx:
            self.rebuild_root_map()
        if root not in self.root_map_ctx:
            return
        ctx_file = os.path.join(self.cfgdir, self.root_map_ctx[root])
        if not os.path.isfile(ctx_file):
            return
        ctx_key = self.escaped_ctx_key(rel)

        with open(ctx_file, "r", encoding="utf-8", errors="ignore") as f:
            lines = [ln.rstrip("\n") for ln in f]

        if any(ln.startswith(ctx_key + " ") for ln in lines):
            return

        lines.append(f"{ctx_key} {DEFAULT_CTX}")
        lines.sort()
        with open(ctx_file, "w", encoding="utf-8", errors="ignore") as f:
            f.write("\n".join(lines) + "\n")

        self.ctx = load_file_context(self.base)

    def apply_permission_only(self, rel):
        root = self._get_root(rel)
        # re-scan kalau basi (lihat catatan di apply_context_only)
        if root not in self.root_map_fs:
            self.rebuild_root_map()
        if root not in self.root_map_fs:
            return
        fs_file = os.path.join(self.cfgdir, self.root_map_fs[root])
        if not os.path.isfile(fs_file):
            return
        fs_key = rel

        with open(fs_file, "r", encoding="utf-8", errors="ignore") as f:
            lines = [ln.rstrip("\n") for ln in f]

        if any(ln.split()[0] == fs_key for ln in lines if ln.strip() and not ln.startswith("#")):
            return

        inferred = self.infer_permission(fs_key)

        if len(inferred) == 4:
            uid, gid, mode, *_ = inferred
            new_line = f"{fs_key} {uid} {gid} {mode}"
        else:
            uid, gid, mode = inferred
            new_line = f"{fs_key} {uid} {gid} {mode}"

        lines.append(new_line)
        lines.sort()

        with open(fs_file, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")

        self.fs = load_fs_config(self.base)

    def apply_perm_on_repack(self, fs_root_folder):
        fs_root_folder = os.path.abspath(fs_root_folder)
        part = os.path.basename(fs_root_folder.rstrip("/\\"))


        if part not in self.root_map_fs:
            self.rebuild_root_map()
        if part not in self.root_map_fs:
            raise RuntimeError(f"FS root tidak dikenal: {part}")

        fs_file = os.path.join(self.cfgdir, self.root_map_fs[part])

        with open(fs_file, "r", encoding="utf-8", errors="ignore") as f:
            lines = [ln.rstrip("\n") for ln in f]

        existing = set(
            ln.split()[0]
            for ln in lines
            if ln.strip() and not ln.startswith("#")
        )

        new_lines = []

        for root, dirs, files in os.walk(fs_root_folder):
            for d in dirs:
                full = os.path.join(root, d)
                rel = os.path.relpath(full, fs_root_folder).replace("\\", "/")
                key = f"{part}/{rel}" if rel != "." else part

                if key in existing:
                    continue

                uid, gid, mode = self.infer_permission(key)
                new_lines.append(f"{key} {uid} {gid} {mode}")
                existing.add(key)

            for f in files:
                full = os.path.join(root, f)
                rel = os.path.relpath(full, fs_root_folder).replace("\\", "/")
                key = f"{part}/{rel}"

                if key in existing:
                    continue

                inferred = self.infer_permission(key)
                if len(inferred) == 4:
                    uid, gid, mode, *_ = inferred
                    new_lines.append(f"{key} {uid} {gid} {mode}")
                else:
                    uid, gid, mode = inferred
                    new_lines.append(f"{key} {uid} {gid} {mode}")

                existing.add(key)

        if new_lines:
            final = lines + new_lines
            final.sort()
            with open(fs_file, "w", encoding="utf-8") as f:
                f.write("\n".join(final) + "\n")

        self.fs = load_fs_config(self.base)

    def validate_fs_config_for_mkfs(self, fs_config_path):
        bad = []

        with open(fs_config_path, "r", encoding="utf-8", errors="ignore") as f:
            for ln in f:
                s = ln.strip()
                if not s or s.startswith("#"):
                    continue

                parts = s.split()
                key = parts[0]

                if key == "/" or key == "":
                    bad.append(key)

        if bad:
            raise RuntimeError(f"fs_config invalid entry: {bad}")

    def apply_predicted_context(self, fs_root_folder, mode="ai", tree_id=0, dry_run=False):

        from core.context_patcher import ContextPatcher

        fs_root_folder = os.path.abspath(fs_root_folder)
        part = os.path.basename(fs_root_folder.rstrip("/\\"))

        # re-scan kalau root_map basi (lihat apply_perm_on_repack)
        if part not in self.root_map_ctx:
            self.rebuild_root_map()
        if part not in self.root_map_ctx:
            raise RuntimeError(f"[apply_predicted_context] Unknown fs root: {part}")

        cp = ContextPatcher(self.base)

        neocat_dir = cp.neocat_out_dir
        cache_files = []
        if os.path.isdir(neocat_dir):
            cache_files = [
                f for f in os.listdir(neocat_dir)
                if f == f"{part}_file_contexts"
            ]

        force_fresh = False
        try:
            state_path = os.path.join(self.base, "neobranch", part, "neobranch_state.json")
            if os.path.isfile(state_path):
                with open(state_path, "r", encoding="utf-8") as f:
                    active_name = json.load(f).get("active_branch")
                if active_name:
                    branch_json = os.path.join(self.base, "neobranch", part, f"branch_{active_name}", "branch.json")
                    edited = []
                    if os.path.isfile(branch_json):
                        with open(branch_json, "r", encoding="utf-8") as f:
                            edited = json.load(f).get("edited_files", [])
                    fc_path = os.path.join(self.base, "config", f"{part}_file_contexts")
                    existing_ctx = set()
                    if os.path.isfile(fc_path):
                        with open(fc_path, "r", encoding="utf-8", errors="ignore") as f:
                            for ln in f:
                                s = ln.strip()
                                if s and not s.startswith("#"):
                                    existing_ctx.add(s.split()[0])
                    neocat_fc = neocat_context_path(self.base, part)
                    if os.path.isfile(neocat_fc):
                        with open(neocat_fc, "r", encoding="utf-8", errors="ignore") as f:
                            for ln in f:
                                s = ln.strip()
                                if s and not s.startswith("#"):
                                    existing_ctx.add(s.split()[0])
                    for entry in edited:
                        rel = str(entry.get("rel_path", "")).replace("\\", "/").strip("/")
                        if rel == part or rel.startswith(part + "/"):
                            if self.escaped_ctx_key(rel) not in existing_ctx:
                                force_fresh = True
                                break
        except Exception:
            force_fresh = False


        if force_fresh:
            print(f"[apply_predicted_context] Active NeoBranch missing ctx → fresh inference (mode={mode})")
            cp._infer_cache.clear()
        elif cache_files:
            print(f"[apply_predicted_context] Cache present ({len(cache_files)}), incremental scan for new holes (mode={mode})")
        else:
            print(f"[apply_predicted_context] No cache, full inference (mode={mode})")

        # 1) cari + infer hole baru (cache lama otomatis di-skip di PASS-1)
        new_scan = cp.scan_tree(part, mode=mode)
        # 2) merge hole baru ke cache neocat (existing tidak ditiban)
        cp.save_to_neocat_cache(new_scan)

        # 3) apply dari cache PENUH (lama + hole baru) ke config.
        #    Kalau cache kosong/tak ada utk part ini, fallback ke hasil scan.
        scan = {}
        for fc_name in os.listdir(neocat_dir) if os.path.isdir(neocat_dir) else []:
            if fc_name != f"{part}_file_contexts":
                continue
            cached_data = cp._load_neocat_cache(fc_name)
            if cached_data:
                scan[fc_name] = [
                    (k, v, "cached", "neocat-cache")
                    for k, v in cached_data.items()
                ]
        if not scan:
            scan = new_scan

        cp.apply_results(scan, dry_run=dry_run)
        self.ctx = load_file_context(self.base)

    def rebuild_root_map(self):
        self.root_map_ctx.clear()
        self.root_map_fs.clear()
        self._scan_root_files()

    def load_full_state(self):

        full = load_full_state(self.base)
        self.ctx = full["ctx"]
        self.fs = full["fs"]
        return {
            **full,
            "root_map_ctx": dict(self.root_map_ctx),
            "root_map_fs": dict(self.root_map_fs),
        }

    def _write_ctx_fs_entry(self, rel, ctx_val, fs_val):
        rel = rel.strip("/").replace("\\", "/")
        root = self._get_root(rel)

        if root not in self.root_map_ctx or root not in self.root_map_fs:
            return

        ctx_file = os.path.join(self.cfgdir, self.root_map_ctx[root])
        fs_file = os.path.join(self.cfgdir, self.root_map_fs[root])
        ctx_key = self.escaped_ctx_key(rel)

        if ctx_val:
            with open(ctx_file, "r", encoding="utf-8", errors="ignore") as f:
                lines = [ln.rstrip("\n") for ln in f]
            if not any(ln.startswith(ctx_key + " ") for ln in lines):
                final = []
                inserted = False
                for ln in lines:
                    s = ln.strip()
                    if s and not s.startswith("#"):
                        key = s.split()[0]
                        if not inserted and ctx_key < key:
                            final.append(f"{ctx_key} {ctx_val}")
                            inserted = True
                    final.append(ln)
                if not inserted:
                    final.append(f"{ctx_key} {ctx_val}")
                with open(ctx_file, "w", encoding="utf-8", errors="ignore") as f:
                    f.write("\n".join(final) + "\n")

        if fs_val:
            with open(fs_file, "r", encoding="utf-8", errors="ignore") as f:
                lines = [ln.rstrip("\n") for ln in f]
            if not any(ln.split()[0] == rel
                       for ln in lines if ln.strip() and not ln.strip().startswith("#")):
                if isinstance(fs_val, (tuple, list)):
                    entry = f"{rel} {' '.join(str(x) for x in fs_val[:3])}"
                else:
                    entry = f"{rel} {' '.join(str(fs_val).split()[:3])}"
                final = []
                inserted = False
                for ln in lines:
                    s = ln.strip()
                    if s and not s.startswith("#"):
                        key = s.split()[0]
                        if not inserted and rel < key:
                            final.append(entry)
                            inserted = True
                    final.append(ln)
                if not inserted:
                    final.append(entry)
                with open(fs_file, "w", encoding="utf-8") as f:
                    f.write("\n".join(final) + "\n")

    def handle_fs_transfer(self, old_rel, new_rel, old_base=None, new_base=None,
                           src_ctx=None, src_fs=None):
        new_rel = new_rel.strip("/").replace("\\", "/")

        if old_rel:
            old_rel = old_rel.strip("/").replace("\\", "/")

            self.move_paths(old_rel, new_rel)

            self.ctx = load_file_context(self.base)
            self.fs = load_fs_config(self.base)

            self.rebuild_root_map()
            return

        abs_new = os.path.join(self.base, new_rel)

        if src_ctx or src_fs:
            for root, dirs, files in os.walk(abs_new):
                rel_root = os.path.relpath(root, self.base).replace("\\", "/")
                root_name = rel_root.split("/")[0]

                if root_name not in self.root_map_ctx or root_name not in self.root_map_fs:
                    continue

                ctx_key_root = self.escaped_ctx_key(rel_root)
                if rel_root not in (self.fs or {}) or ctx_key_root not in (self.ctx or {}):
                    src_ctx_val = None
                    src_fs_val = None

                    if src_ctx:
                        ctx_key_escaped = self.escaped_ctx_key(rel_root)
                        if ctx_key_escaped in src_ctx:
                            src_ctx_val = src_ctx[ctx_key_escaped]
                        else:
                            suffix = "/".join(rel_root.split("/")[1:])
                            for k, v in src_ctx.items():
                                if k.endswith("/" + suffix) or k == suffix:
                                    src_ctx_val = v
                                    break

                    if src_fs:
                        if rel_root in src_fs:
                            src_fs_val = src_fs[rel_root]
                        else:
                            suffix = "/".join(rel_root.split("/")[1:])
                            for k, v in src_fs.items():
                                if k.endswith("/" + suffix) or k == suffix:
                                    src_fs_val = v
                                    break

                    if src_ctx_val or src_fs_val:
                        self._write_ctx_fs_entry(rel_root, src_ctx_val, src_fs_val)
                    else:
                        self.apply(rel_root)

                for name in files:
                    full_f = os.path.join(root, name)
                    rel_f = os.path.relpath(full_f, self.base).replace("\\", "/")

                    ctx_key_f = self.escaped_ctx_key(rel_f)
                    if rel_f not in (self.fs or {}) or ctx_key_f not in (self.ctx or {}):
                        src_ctx_val = None
                        src_fs_val = None

                        if src_ctx:
                            ctx_key_escaped = self.escaped_ctx_key(rel_f)
                            if ctx_key_escaped in src_ctx:
                                src_ctx_val = src_ctx[ctx_key_escaped]
                            else:
                                suffix = "/".join(rel_f.split("/")[1:])
                                for k, v in src_ctx.items():
                                    if k.endswith("/" + suffix) or k == suffix:
                                        src_ctx_val = v
                                        break

                        if src_fs:
                            if rel_f in src_fs:
                                src_fs_val = src_fs[rel_f]
                            else:
                                suffix = "/".join(rel_f.split("/")[1:])
                                for k, v in src_fs.items():
                                    if k.endswith("/" + suffix) or k == suffix:
                                        src_fs_val = v
                                        break

                        if src_ctx_val or src_fs_val:
                            self._write_ctx_fs_entry(rel_f, src_ctx_val, src_fs_val)
                        else:
                            self.apply(rel_f)
        else:
            for root, dirs, files in os.walk(abs_new):
                rel_root = os.path.relpath(root, self.base).replace("\\", "/")
                root_name = rel_root.split("/")[0]
                if root_name not in self.root_map_ctx or root_name not in self.root_map_fs:
                    continue
                self.apply(rel_root)
                for name in files:
                    full = os.path.join(root, name)
                    rel = os.path.relpath(full, self.base).replace("\\", "/")
                    self.apply(rel)

        self.ctx = load_file_context(self.base)
        self.fs = load_fs_config(self.base)
        self.rebuild_root_map()
