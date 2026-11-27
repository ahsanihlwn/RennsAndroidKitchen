# SPDX-License-Identifier: MPL-2.0
"""Modular AOSP filesystem metadata & tree preparation helpers for EROFS <-> EXT4."""

import math
import os
import re

EXT_FORMATS = {"EXT2", "EXT3", "EXT4"}

_VENDOR_PARTITIONS = {"vendor", "odm", "vendor_dlkm", "odm_dlkm"}


def default_partition_selinux_context(part_name: str) -> str:
    """Return a conservative AOSP default SELinux context for a partition root."""
    clean = (part_name or "").strip().strip("/").lower()
    if clean in _VENDOR_PARTITIONS:
        return "u:object_r:vendor_file:s0"
    return "u:object_r:system_file:s0"


def parse_erofs_mount_point(fs_options_path: str, part_name: str) -> str:
    """Read `--mount-point=...` from `<part>_fs_options`, defaulting to `/<part_name>`."""
    fallback = f"/{part_name.strip().strip('/')}" if part_name.strip().strip("/") else "/"
    if not fs_options_path or not os.path.isfile(fs_options_path):
        return fallback
    try:
        with open(fs_options_path, "r", encoding="utf-8", errors="ignore") as f:
            text = f.read()
    except OSError:
        return fallback
    match = re.search(r'--mount-point=(?:"([^"]*)"|\'([^\']*)\'|(\S+))', text)
    if not match:
        return fallback
    val = next((g for g in match.groups() if g is not None), "").strip()
    if not val:
        return fallback
    if val == "/":
        return "/"
    return val if val.startswith("/") else f"/{val}"


def normalize_ext4_mount_point(mount_point: str, part_name: str) -> str:
    """Normalize MountPoint for `ext_make_fs` while preserving `/` for SAR images."""
    raw = (mount_point or "").strip()
    if raw == "/":
        return "/"
    cleaned = raw.lstrip("/")
    return cleaned or part_name.strip().strip("/")


def _escape_fc_regex(path: str) -> str:
    out = []
    for ch in path:
        if ch in ".+*[]()*?^${}|\\":
            out.append("\\" + ch)
        else:
            out.append(ch)
    return "".join(out)


def _extract_root_selinux_context(file_contexts_path: str, part_name: str, mount_point: str) -> str:
    """Find the most appropriate partition root SELinux context from `file_contexts`."""
    candidates = []
    mp_norm = mount_point if mount_point.startswith("/") else f"/{mount_point}"
    if mp_norm == "/":
        candidates.extend(["/", f"/{part_name}", f"/{part_name}/"])
    else:
        candidates.extend([mp_norm, f"{mp_norm}/", f"/{part_name}", f"/{part_name}/", "/"])

    by_pattern = {}
    first_ctx = None
    if os.path.isfile(file_contexts_path):
        try:
            with open(file_contexts_path, "r", encoding="utf-8", errors="ignore") as f:
                for raw in f:
                    line = raw.strip()
                    if not line or line.startswith("#"):
                        continue
                    parts = line.split()
                    if len(parts) < 2:
                        continue
                    pat = parts[0]
                    ctx = parts[-1]
                    if ctx == "<<none>>":
                        continue
                    if first_ctx is None:
                        first_ctx = ctx
                    by_pattern[pat] = ctx
        except OSError:
            pass

    for cand in candidates:
        if cand in by_pattern:
            return by_pattern[cand]
    return first_ctx or default_partition_selinux_context(part_name)


def patch_missing_fs_config(
    fs_config_file: str,
    folder_path: str,
    part_name: str,
    *,
    mount_point: str | None = None,
    skip_lost_found: bool = False,
) -> int:
    """Append missing filesystem entries to `fs_config` with symlink-aware permissions."""
    if not os.path.isfile(fs_config_file) or not os.path.isdir(folder_path):
        return 0

    existing = set()
    try:
        with open(fs_config_file, "r", encoding="utf-8", errors="ignore") as f:
            for ln in f:
                raw = ln.rstrip("\r\n")
                s = raw.strip()
                if not s or s.startswith("#"):
                    continue
                if raw.startswith(" "):
                    existing.add("/")
                else:
                    existing.add(s.split()[0].lstrip("/"))
    except OSError as e:
        print(f"[PATCH_FSCFG] read error: {e}")
        return 0

    from core.symlink import read_symlink_target

    sar_root = (mount_point or "").strip() == "/"
    folder_abs = os.path.abspath(folder_path)
    parent_abs = os.path.dirname(folder_abs)
    new_lines = []

    def _rel_key(full_path: str, *, is_root_dir: bool = False) -> str:
        if sar_root:
            if is_root_dir:
                return "/"
            return os.path.relpath(full_path, folder_abs).replace("\\", "/")
        rel = os.path.relpath(full_path, parent_abs).replace("\\", "/")
        return rel

    if sar_root and "/" not in existing:
        new_lines.append("/ 0 0 0755")
        existing.add("/")

    for root, dirs, files in os.walk(folder_abs):
        is_root = os.path.abspath(root) == folder_abs
        if not (sar_root and is_root):
            rel_dir = _rel_key(root)
            if not (skip_lost_found and rel_dir in (f"{part_name}/lost+found", "lost+found")):
                if rel_dir not in existing:
                    new_lines.append(f"{rel_dir} 0 0 0755")
                    existing.add(rel_dir)

        for dname in dirs:
            if skip_lost_found and is_root and dname == "lost+found":
                continue
            full_dir = os.path.join(root, dname)
            if os.path.islink(full_dir):
                rel_link = _rel_key(full_dir)
                if rel_link not in existing:
                    new_lines.append(f"{rel_link} 0 0 0777")
                    existing.add(rel_link)

        for fname in files:
            full_file = os.path.join(root, fname)
            rel_file = _rel_key(full_file)
            if rel_file not in existing:
                is_sym = os.path.islink(full_file) or (read_symlink_target(full_file) is not None)
                mode_str = "0777" if is_sym else "0644"
                new_lines.append(f"{rel_file} 0 0 {mode_str}")
                existing.add(rel_file)

    if not new_lines:
        return 0

    print(f"[PATCH_FSCFG] adding {len(new_lines)} missing entries to fs_config")
    try:
        need_leading_nl = False
        if os.path.getsize(fs_config_file) > 0:
            with open(fs_config_file, "rb") as fb:
                fb.seek(-1, os.SEEK_END)
                need_leading_nl = fb.read(1) != b"\n"
        with open(fs_config_file, "a", encoding="utf-8", newline="\n") as f:
            if need_leading_nl:
                f.write("\n")
            for ln in new_lines:
                f.write(ln + "\n")
    except OSError as e:
        print(f"[PATCH_FSCFG] write error: {e}")
        return 0
    return len(new_lines)


def _remove_empty_lost_found_for_erofs(partition_dir: str, fs_config_path: str, part_name: str) -> None:
    """Remove EXT4's empty `lost+found` dir and synthetic `fs_config` lines before EROFS pack."""
    lf_dir = os.path.join(partition_dir, "lost+found")
    removed_dir = False
    if os.path.isdir(lf_dir) and not os.path.islink(lf_dir):
        try:
            if not os.listdir(lf_dir):
                os.rmdir(lf_dir)
                removed_dir = True
        except OSError:
            pass

    if removed_dir and os.path.isfile(fs_config_path):
        try:
            with open(fs_config_path, "r", encoding="utf-8", errors="ignore") as f:
                lines = f.readlines()
            filtered = [
                ln for ln in lines
                if (ln.strip().split()[0] if ln.strip() else "")
                not in (f"{part_name}/lost+found", "lost+found")
            ]
            if len(filtered) != len(lines):
                with open(fs_config_path, "w", encoding="utf-8", newline="\n") as f:
                    f.writelines(ln.rstrip("\r\n") + "\n" for ln in filtered if ln.strip())
        except OSError:
            pass


def _ensure_file_contexts_for_erofs(file_contexts_path: str, part_name: str, mount_point: str) -> None:
    """Synthesize minimal AOSP `file_contexts` if an EXT image had no SELinux xattrs."""
    if os.path.isfile(file_contexts_path) and os.path.getsize(file_contexts_path) > 0:
        return
    ctx = default_partition_selinux_context(part_name)
    mp_norm = mount_point if mount_point.startswith("/") else f"/{mount_point}"
    lines = [f"/ {ctx}"]
    if mp_norm != "/":
        lines.extend([
            f"{mp_norm} {ctx}",
            f"{mp_norm}/ {ctx}",
            f"{mp_norm}(/.*)? {ctx}",
        ])
    else:
        lines.append(f"/{part_name}(/.*)? {ctx}")
    os.makedirs(os.path.dirname(file_contexts_path), exist_ok=True)
    with open(file_contexts_path, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + "\n")


def _ensure_ext4_file_contexts(file_contexts_path: str, part_name: str, mount_point: str) -> None:
    """Ensure `file_contexts` has AOSP partition catch-all and `lost+found` rules without duplicates."""
    if not os.path.isfile(file_contexts_path):
        _ensure_file_contexts_for_erofs(file_contexts_path, part_name, mount_point)

    mp_norm = mount_point if mount_point.startswith("/") else f"/{mount_point}"
    mp_clean = mp_norm.rstrip("/")
    lf_raw = "/lost+found" if mp_norm == "/" else f"{mp_clean}/lost+found"
    lf_escaped = _escape_fc_regex(lf_raw)
    catchall_pat = "/(.*)?" if mp_norm == "/" else f"{_escape_fc_regex(mp_clean)}(/.*)?"
    catchall_aliases = {catchall_pat, "/.*", "/(.*)?"}

    try:
        with open(file_contexts_path, "r", encoding="utf-8", errors="ignore") as f:
            orig_lines = f.read().splitlines()
    except OSError:
        return

    all_patterns = set()
    has_catchall = False
    for raw in orig_lines:
        s = raw.strip()
        if not s or s.startswith("#"):
            continue
        toks = s.split()
        if len(toks) < 2:
            continue
        pat = toks[0]
        all_patterns.add(pat)
        type_tok = toks[1] if len(toks) >= 3 and toks[1].startswith("-") else None
        if (type_tok is None or type_tok == "-d") and pat in catchall_aliases:
            has_catchall = True

    ctx = _extract_root_selinux_context(file_contexts_path, part_name, mp_norm)
    need_catchall = not has_catchall and catchall_pat not in all_patterns
    need_lf = lf_escaped not in all_patterns and lf_raw not in all_patterns

    if not need_catchall and not need_lf:
        return

    updated = list(orig_lines)
    if need_catchall:
        # Prepend at lowest priority (AOSP selabel_file last-match-wins) so all specific rules override it.
        insert_at = 0
        while insert_at < len(updated) and (not updated[insert_at].strip() or updated[insert_at].lstrip().startswith("#")):
            insert_at += 1
        updated.insert(insert_at, f"{catchall_pat} {ctx}")
    if need_lf:
        updated.append(f"{lf_escaped} {ctx}")

    try:
        with open(file_contexts_path, "w", encoding="utf-8", newline="\n") as f:
            f.write("\n".join(updated) + "\n")
    except OSError:
        pass


def prepare_tree_and_configs_for_target(
    partition_dir: str,
    part_name: str,
    config_dir: str,
    target_fs: str,
    *,
    mount_point: str | None = None,
) -> None:
    """Apply AOSP filesystem-specific tree/config adjustments before repack or conversion."""
    target = (target_fs or "").upper()
    fs_config_path = os.path.join(config_dir, f"{part_name}_fs_config")
    file_contexts_path = os.path.join(config_dir, f"{part_name}_file_contexts")
    mp = mount_point or f"/{part_name}"

    if target == "EROFS":
        _remove_empty_lost_found_for_erofs(partition_dir, fs_config_path, part_name)
        _ensure_file_contexts_for_erofs(file_contexts_path, part_name, mp)
        patch_missing_fs_config(
            fs_config_path,
            partition_dir,
            part_name,
            mount_point=mp,
            skip_lost_found=True,
        )
    elif target in EXT_FORMATS:
        _ensure_ext4_file_contexts(file_contexts_path, part_name, mp)
        patch_missing_fs_config(
            fs_config_path,
            partition_dir,
            part_name,
            mount_point=mp,
            skip_lost_found=False,
        )


def estimate_ext4_usage_bytes(partition_dir: str, block_size: int = 4096) -> int:
    """Estimate EXT4 space usage including data blocks, directory blocks, inodes, and xattrs."""
    bs = max(1024, int(block_size or 4096))
    data_bytes = 0
    inode_count = 2  # root + lost+found

    for root, dirs, files in os.walk(partition_dir):
        inode_count += 1
        data_bytes += bs  # directory block(s)
        for dname in dirs:
            if os.path.islink(os.path.join(root, dname)):
                inode_count += 1
        for fname in files:
            inode_count += 1
            fp = os.path.join(root, fname)
            if os.path.islink(fp):
                continue
            try:
                sz = os.path.getsize(fp)
            except OSError:
                sz = 0
            if sz > 0:
                blocks = (sz + bs - 1) // bs
                data_bytes += blocks * bs

    # 256B inode table + conservative 256B xattr/metadata allowance per inode
    meta_bytes = inode_count * 512
    return data_bytes + meta_bytes


def estimate_ext4_image_size(
    partition_dir: str,
    *,
    old_size: int | None = None,
    block_size: int = 4096,
    spare_ratio: float = 0.08,
    align_mb: int = 4,
    min_size_mb: int = 16,
    min_free_mb: int = 16,
    max_free_mb: int = 512,
    preserve_old_size: bool = False,
    precomputed_total: int | None = None,
) -> int:
    """Compute an EXT4 image size that safely fits files, metadata, journal, and free slack."""
    bs = max(1024, int(block_size or 4096))
    total = precomputed_total if precomputed_total is not None else estimate_ext4_usage_bytes(partition_dir, bs)

    # `make_ext4fs` allocates a journal of clamp(total_blocks / 64, 1024, 32768) blocks.
    est_blocks = max(1, (total + bs - 1) // bs)
    journal_blocks = max(1024, min(32768, est_blocks // 64))
    journal_bytes = journal_blocks * bs

    min_size = min_size_mb * 1024 * 1024
    min_free = min_free_mb * 1024 * 1024
    max_free = max_free_mb * 1024 * 1024

    free = max(min_free, min(max_free, int(total * spare_ratio)))
    need = max(total + journal_bytes + free, min_size)

    rem = need % bs
    if rem:
        need += bs - rem
    if align_mb > 0:
        align_bytes = align_mb * 1024 * 1024
        rem_a = need % align_bytes
        if rem_a:
            need += align_bytes - rem_a

    if preserve_old_size and old_size and old_size > 0 and need <= old_size:
        return old_size
    return need
