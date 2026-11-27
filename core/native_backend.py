# SPDX-License-Identifier: MPL-2.0
# RAK_NATIVE_BACKEND accepts auto, python, or rust.
from collections import Counter
from functools import lru_cache
import os
import threading
import json
from pathlib import Path
import sys

_calls = Counter()
_calls_lock = threading.Lock()


@lru_cache(maxsize=1)
def _load_native():
    try:
        # Development builds have a fingerprinted location so a running Windows
        # app can keep its old DLL while the next process uses the new build.
        runtime = Path(__file__).resolve().parents[1] / 'build' / 'native' / 'runtime'
        receipt = runtime / 'current.json'
        target=os.environ.get('RAK_NATIVE_RUNTIME')
        if target or receipt.is_file():
            if not target:target = json.loads(receipt.read_text(encoding='utf-8')).get('path')
            if target and target!='None' and Path(target).resolve().is_relative_to(runtime.resolve()):
                sys.path.insert(0,str(Path(target).resolve()))
        import rak_native
    except (ImportError, OSError, ValueError) as error:
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


def binary_image_bytechunks(path, ranges):
    module = _get_native()
    encoder = getattr(module, "binary_image_bytechunks", None)
    if encoder is None:
        return None
    result = encoder(path, ranges)
    _record("binary_image_bytechunks")
    return result


def binary_flat_sha256(path, ranges):
    digest = getattr(_get_native(), "binary_flat_sha256", None)
    if digest is None:
        return None
    result = digest(path, ranges)
    _record("binary_flat_sha256")
    return result


def binary_dwarf_functions(path):
    index = getattr(_get_native(), "binary_dwarf_functions", None)
    if index is None:
        return None
    result = index(path)
    if result is not None:
        _record("binary_dwarf_functions")
    return result


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


def create_path_index(roots):
    module = _get_native()
    if module is not None and hasattr(module,'PathIndex'):
        _record('path_index')
        return module.PathIndex([os.path.abspath(os.fspath(root)) for root in roots])
    if os.environ.get('RAK_NATIVE_BACKEND','auto').lower()=='rust':
        raise RuntimeError('Rust path index unavailable. Run python tools/build_native.py --dev')
    return None


def directory_entries(path, root, *, path_index=None, refresh=False, include_magic=True):

    path, root = os.path.normpath(os.fspath(path)), os.path.normpath(os.fspath(root))
    if path_index is not None:
        result=json.loads(path_index.entries_json(path,root,refresh,bool(include_magic)))
        _record('indexed_directory_entries')
        return result
    module = _get_native()
    if module is not None:
        result = json.loads(module.directory_entries_json(path, root, bool(include_magic)))
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
                read_only = False if is_dir else (not os.access(entry.path, os.W_OK))
            except OSError:
                is_dir, size, stamp, read_only = False, None, None, False
            link = check_symlink(entry.path, root, target_cache=cache) if (
                not is_dir and size is not None and size <= 512) else None
            magic = check_magic(entry.path) if (include_magic and not is_dir and entry.name.lower().endswith((".img", ".bin"))) else ""
            result.append(dict(name=entry.name, path=entry.path, is_dir=is_dir,
                               size=size, mtime_ns=stamp, symlink=link, magic=magic, read_only=read_only))
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
def create_compare_job():
    module = _get_native()
    if module is not None and hasattr(module, "CompareJob"):
        _record("compare_job")
        return module.CompareJob()
    if os.environ.get("RAK_NATIVE_BACKEND", "auto").lower() == "rust":
        raise RuntimeError("Rust CompareJob unavailable. Run python tools/build_native.py --dev")
    return None


def diff_lines(path_a, path_b):
    path_a = os.path.normpath(os.fspath(path_a))
    path_b = os.path.normpath(os.fspath(path_b))
    module = _get_native()
    if module is not None and hasattr(module, "diff_lines"):
        _record("diff_lines")
        return module.diff_lines(path_a, path_b)
    if os.environ.get("RAK_NATIVE_BACKEND", "auto").lower() == "rust":
        raise RuntimeError("Rust diff_lines unavailable. Run python tools/build_native.py --dev")
    import difflib
    def _read(path):
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as source:
                return source.readlines()
        except OSError:
            return []
    lines_a = _read(path_a)
    lines_b = _read(path_b)
    return lines_a, lines_b, difflib.SequenceMatcher(None, lines_a, lines_b, autojunk=False).get_opcodes()


def _require_native_fn(name):
    module = _get_native()
    fn = getattr(module, name, None) if module is not None else None
    if fn is not None:
        return fn
    raise RuntimeError(f"Rust backend missing {name}. Run python tools/build_native.py --dev")


def oem_detect_magic(path):
    path = os.path.normpath(os.fspath(path))
    module = _get_native()
    if module is not None and hasattr(module, "oem_detect_magic"):
        _record("oem_detect_magic")
        return module.oem_detect_magic(path)
    return ""


def oem_inspect(path):
    path = os.path.normpath(os.fspath(path))
    fn = _require_native_fn("oem_inspect_json")
    _record("oem_inspect")
    return json.loads(fn(path))


def oem_extract(path, out_dir, selected=None):
    path = os.path.normpath(os.fspath(path))
    out_dir = os.path.normpath(os.fspath(out_dir))
    fn = _require_native_fn("oem_extract_json")
    _record("oem_extract")
    sel = list(selected) if selected is not None else None
    return json.loads(fn(path, out_dir, sel))


def scan_apks(root):
    root = os.path.normpath(os.fspath(root))
    fn = _require_native_fn("scan_apks_json")
    _record("scan_apks")
    return json.loads(fn(root))


def read_apk_icon(apk_path, entry_name):
    apk_path = os.path.normpath(os.fspath(apk_path))
    fn = _require_native_fn("read_apk_icon_bytes")
    _record("read_apk_icon")
    return bytes(fn(apk_path, entry_name))


def patch_fstab(text, *, disable_avb=True, disable_encryption=True):
    fn = _require_native_fn("patch_fstab_json")
    _record("patch_fstab")
    return json.loads(fn(str(text), bool(disable_avb), bool(disable_encryption)))


def patch_props(text, updates, removals=()):
    fn = _require_native_fn("patch_props_json")
    _record("patch_props")
    pairs = [(str(k), str(v)) for k, v in updates]
    rems = [str(k) for k in removals]
    return json.loads(fn(str(text), pairs, rems))


def selinux_audit_allow(log_text, *, include_untrusted=False):
    fn = _require_native_fn("selinux_audit_allow_json")
    _record("selinux_audit_allow")
    return json.loads(fn(str(log_text), bool(include_untrusted)))


def inspect_vbmeta(path):
    path = os.path.normpath(os.fspath(path))
    fn = _require_native_fn("inspect_vbmeta_json")
    _record("inspect_vbmeta")
    return json.loads(fn(path))


def patch_vbmeta(path, *, disable_hashtree=True, disable_verification=True):
    path = os.path.normpath(os.fspath(path))
    fn = _require_native_fn("patch_vbmeta_json")
    _record("patch_vbmeta")
    return json.loads(fn(path, bool(disable_hashtree), bool(disable_verification)))


def native_module_path(fn_name=None):
    module = _get_native()
    if module is None:
        return None
    if fn_name is not None and not hasattr(module, fn_name):
        return None
    return getattr(module, "__file__", "rak_native")


def super_info(path, *, slot=0):
    path = os.path.normpath(os.fspath(path))
    fn = _require_native_fn("super_info_json")
    _record("super_info")
    return json.loads(fn(path, int(slot)))


def super_extract(path, out_dir, *, partitions=None, slot=0, normalize=False, progress=None):
    path = os.path.normpath(os.fspath(path))
    out_dir = os.path.normpath(os.fspath(out_dir))
    fn = _require_native_fn("super_extract_json")
    _record("super_extract")
    sel = [str(p) for p in partitions] if partitions else None
    cb = _resolve_progress_cb(progress)
    return json.loads(fn(path, out_dir, sel, int(slot), bool(normalize), cb))


def super_unsparse(path, out_dir=None, *, progress=None):
    path = os.path.normpath(os.fspath(path))
    out_arg = os.path.normpath(os.fspath(out_dir)) if out_dir is not None else None
    fn = _require_native_fn("super_unsparse")
    _record("super_unsparse")
    cb = _resolve_progress_cb(progress)
    return fn(path, out_arg, cb)


def dtbo_inspect(path):
    path = os.path.normpath(os.fspath(path))
    fn = _require_native_fn("dtbo_inspect_json")
    _record("dtbo_inspect")
    return json.loads(fn(path))


def dtbo_unpack(path, out_dir, *, decompile_dts=True, progress=None):
    path = os.path.normpath(os.fspath(path))
    out_dir = os.path.normpath(os.fspath(out_dir))
    fn = _require_native_fn("dtbo_unpack_json")
    _record("dtbo_unpack")
    cb = _resolve_progress_cb(progress)
    return json.loads(fn(path, out_dir, bool(decompile_dts), cb))


def dtbo_pack(output_path, spec, *, progress=None):
    output_path = os.path.normpath(os.fspath(output_path))
    fn = _require_native_fn("dtbo_pack_json")
    _record("dtbo_pack")
    cb = _resolve_progress_cb(progress)
    return json.loads(fn(output_path, json.dumps(spec), cb))


def fdt_to_dts(dtb_path):
    dtb_path = os.path.normpath(os.fspath(dtb_path))
    fn = _require_native_fn("fdt_to_dts_text")
    _record("fdt_to_dts")
    return fn(dtb_path)


def image_logical_size(path):
    path = os.path.normpath(os.fspath(path))
    fn = _require_native_fn("image_logical_size")
    _record("image_logical_size")
    return int(fn(path))


def super_make(
    output_path,
    device_size,
    groups,
    partitions,
    *,
    metadata_size=65536,
    metadata_slots=2,
    block_size=4096,
    alignment=1048576,
    alignment_offset=0,
    super_name="super",
    sparse=False,
    virtual_ab=False,
    auto_slot_suffixing=False,
    force_full_image=True,
    progress=None,
):
    output_path = os.path.normpath(os.fspath(output_path))
    fn = _require_native_fn("super_make_json")
    _record("super_make")
    cb = _resolve_progress_cb(progress)
    groups_payload = [
        {"name": str(g["name"]), "max_size": int(g["max_size"])}
        for g in groups
    ]
    parts_payload = [
        {
            "name": str(p["name"]),
            "attr": str(p.get("attr", "readonly")),
            "size": int(p.get("size", 0)),
            "group": str(p.get("group", "default")),
            "image": os.path.normpath(os.fspath(p["image"])) if p.get("image") else None,
        }
        for p in partitions
    ]
    return json.loads(
        fn(
            output_path,
            int(device_size),
            json.dumps(groups_payload),
            json.dumps(parts_payload),
            int(metadata_size),
            int(metadata_slots),
            int(block_size),
            int(alignment),
            int(alignment_offset),
            str(super_name),
            bool(sparse),
            bool(virtual_ab),
            bool(auto_slot_suffixing),
            bool(force_full_image),
            cb,
        )
    )


def ext_inspect(path):
    path = os.path.normpath(os.fspath(path))
    fn = _require_native_fn("ext_inspect_json")
    _record("ext_inspect")
    return json.loads(fn(path))


def _resolve_progress_cb(progress):
    if progress is not None:
        return progress
    try:
        from core.threader_control import get_progress_reporter
        return get_progress_reporter()
    except Exception:
        return None


def ext_unpack(path, *, progress=None):
    path = os.path.normpath(os.fspath(path))
    fn = _require_native_fn("ext_unpack")
    _record("ext_unpack")
    cb = _resolve_progress_cb(progress)
    return fn(path, cb) if cb is not None else fn(path)


def ext_open_reader(path):
    """Open a persistent NativeExtReader handle for an EXT2/3/4 (or sparse) image."""
    path = os.path.normpath(os.fspath(path))
    cls = _require_native_fn("NativeExtReader")
    _record("ext_open_reader")
    return cls(path)


def ext_open_bytes_reader(data):
    """Open an in-memory NativeExtReader handle from raw/sparse bytes (e.g. APEX payloads)."""
    cls = _require_native_fn("NativeExtReader")
    _record("ext_open_bytes_reader")
    return cls.from_bytes(bytes(data))


def ext_usage(path):
    """Compute space usage breakdown of an EXT2/3/4 image natively in Rust."""
    path = os.path.normpath(os.fspath(path))
    fn = _require_native_fn("ext_usage_json")
    _record("ext_usage")
    return json.loads(fn(path))


def ext_edit_plan(path, internal_path, new_len):
    """Plan an in-place EXT file write without mutating the image."""
    path = os.path.normpath(os.fspath(path))
    fn = _require_native_fn("ext_edit_plan_json")
    _record("ext_edit_plan")
    return json.loads(fn(path, str(internal_path), int(new_len)))


def ext_edit_write(path, internal_path, data):
    """Write file data directly in-place into an EXT image using Rust ext_cli."""
    path = os.path.normpath(os.fspath(path))
    fn = _require_native_fn("ext_edit_write")
    _record("ext_edit_write")
    return json.loads(fn(path, str(internal_path), bytes(data)))


def ext_edit_chmod(path, internal_path, perm_octal):
    """Update inode permission bits directly in-place in an EXT image."""
    path = os.path.normpath(os.fspath(path))
    fn = _require_native_fn("ext_edit_chmod")
    _record("ext_edit_chmod")
    return int(fn(path, str(internal_path), int(perm_octal)))


def ext_edit_selinux(path, internal_path, new_ctx):
    """Update security.selinux xattr directly in-place in an EXT image."""
    path = os.path.normpath(os.fspath(path))
    fn = _require_native_fn("ext_edit_selinux")
    _record("ext_edit_selinux")
    return fn(path, str(internal_path), str(new_ctx))


def ext_edit_rm(path, internal_path, *, recursive=True):
    """Remove (unlink) a file or directory directly in-place from an EXT image."""
    path = os.path.normpath(os.fspath(path))
    fn = _require_native_fn("ext_edit_rm")
    _record("ext_edit_rm")
    return json.loads(fn(path, str(internal_path), bool(recursive)))


def ext_make_fs(
    out_img,
    src_dir=None,
    *,
    size_bytes=0,
    mount_point=None,
    file_contexts=None,
    fs_config=None,
    block_size=4096,
    sparse=False,
    label=None,
    progress=None,
):
    out_img = os.path.normpath(os.fspath(out_img))
    src_arg = os.path.normpath(os.fspath(src_dir)) if src_dir is not None else None
    fc_arg = os.path.normpath(os.fspath(file_contexts)) if file_contexts is not None else None
    fs_arg = os.path.normpath(os.fspath(fs_config)) if fs_config is not None else None
    fn = _require_native_fn("ext_make_fs")
    _record("ext_make_fs")
    cb = _resolve_progress_cb(progress)
    return fn(
        out_img,
        src_arg,
        int(size_bytes),
        mount_point,
        fc_arg,
        fs_arg,
        int(block_size),
        bool(sparse),
        label,
        cb,
    )


def sparse_img2simg(src_path, dst_path, *, block_size=4096, progress=None):
    src_path = os.path.normpath(os.fspath(src_path))
    dst_path = os.path.normpath(os.fspath(dst_path))
    fn = _require_native_fn("sparse_img2simg")
    _record("sparse_img2simg")
    cb = _resolve_progress_cb(progress)
    return fn(src_path, dst_path, int(block_size), cb) if cb is not None else fn(src_path, dst_path, int(block_size))


def sparse_simg2img(src_path, dst_path, *, progress=None):
    src_path = os.path.normpath(os.fspath(src_path))
    dst_path = os.path.normpath(os.fspath(dst_path))
    fn = _require_native_fn("sparse_simg2img")
    _record("sparse_simg2img")
    cb = _resolve_progress_cb(progress)
    return fn(src_path, dst_path, cb) if cb is not None else fn(src_path, dst_path)


def pac_list(path):
    path = os.path.normpath(os.fspath(path))
    fn = _require_native_fn("pac_list_json")
    _record("pac_list")
    return json.loads(fn(path))


def pac_verify_crc(path):
    path = os.path.normpath(os.fspath(path))
    fn = _require_native_fn("pac_verify_crc_json")
    _record("pac_verify_crc")
    return json.loads(fn(path))


def pac_extract(path, out_dir, *, selected=None, verify_crc=False):
    path = os.path.normpath(os.fspath(path))
    out_dir = os.path.normpath(os.fspath(out_dir))
    fn = _require_native_fn("pac_extract_json")
    _record("pac_extract")
    sel = [str(item) for item in selected] if selected else None
    return json.loads(fn(path, out_dir, sel, bool(verify_crc)))


# ── Boot image (magiskboot native) ───────────────────────────────────────────

def boot_inspect(image):
    """Inspect a boot / vendor_boot image header without extracting components."""
    image = os.path.normpath(os.fspath(image))
    fn = _require_native_fn("boot_inspect_json")
    _record("boot_inspect")
    return json.loads(fn(image))


def boot_unpack(image, out_dir, *, skip_decomp=False, dump_hdr=True):
    """Unpack a boot / vendor_boot image into out_dir.

    Returns dict: {out_dir, header_version, is_vendor, v4_entries, files}.
    """
    image = os.path.normpath(os.fspath(image))
    out_dir = os.path.normpath(os.fspath(out_dir))
    fn = _require_native_fn("boot_unpack_json")
    _record("boot_unpack")
    return json.loads(fn(image, out_dir, bool(skip_decomp), bool(dump_hdr)))


def boot_dtb_patch(dtb_path):
    """Patch fstab flags inside a DTB file using magiskboot's native DTB patcher."""
    dtb_path = os.path.normpath(os.fspath(dtb_path))
    fn = _require_native_fn("boot_dtb_patch_json")
    _record("boot_dtb_patch")
    return json.loads(fn(dtb_path))


def boot_repack(meta_dir, orig_img, out_img, *, skip_comp=False):
    """Repack components in meta_dir using orig_img as reference into out_img.

    Returns dict: {out_img, size}.
    """
    meta_dir = os.path.normpath(os.fspath(meta_dir))
    orig_img = os.path.normpath(os.fspath(orig_img))
    out_img = os.path.normpath(os.fspath(out_img))
    fn = _require_native_fn("boot_repack_json")
    _record("boot_repack")
    return json.loads(fn(meta_dir, orig_img, out_img, bool(skip_comp)))


def boot_compress(input_path, output_path, fmt):
    """Compress input_path with the given format, write to output_path.

    Returns dict: {output, format, input_size, output_size}.
    """
    input_path = os.path.normpath(os.fspath(input_path))
    output_path = os.path.normpath(os.fspath(output_path))
    fn = _require_native_fn("boot_compress_json")
    _record("boot_compress")
    return json.loads(fn(input_path, output_path, str(fmt)))


def boot_decompress(input_path, output_path):
    """Auto-detect and decompress input_path to output_path.

    Returns dict: {output, format, input_size, output_size}.
    """
    input_path = os.path.normpath(os.fspath(input_path))
    output_path = os.path.normpath(os.fspath(output_path))
    fn = _require_native_fn("boot_decompress_json")
    _record("boot_decompress")
    return json.loads(fn(input_path, output_path))


def boot_detect_format(path):
    """Return the compression format name of a file (e.g. 'gzip', 'raw')."""
    path = os.path.normpath(os.fspath(path))
    fn = _require_native_fn("boot_detect_format")
    _record("boot_detect_format")
    return fn(path)


def boot_cpio(cpio_path, commands):
    """Run one or more magiskboot cpio sub-commands on cpio_path.

    commands: list[str] e.g. ['extract'], ['add 0644 init /path/to/init'].
    Returns dict: {ok, exit_code}.
    """
    cpio_path = os.path.normpath(os.fspath(cpio_path))
    fn = _require_native_fn("boot_cpio_json")
    _record("boot_cpio")
    return json.loads(fn(cpio_path, list(commands)))


# ── EROFS (erofs-rs native) ──────────────────────────────────────────────────

def erofs_detect(path, *, offset=None):
    """Detect if path is a valid EROFS filesystem (and/or Android sparse)."""
    path = os.path.normpath(os.fspath(path))
    fn = _require_native_fn("erofs_detect_json")
    _record("erofs_detect")
    return json.loads(fn(path, offset))


def erofs_info(path, *, offset=None):
    """Inspect EROFS superblock, features, block counts, and UUID."""
    path = os.path.normpath(os.fspath(path))
    fn = _require_native_fn("erofs_info_json")
    _record("erofs_info")
    return json.loads(fn(path, offset))


def erofs_list(path, *, target=None, recursive=True, offset=None):
    """List files and directories inside an EROFS image."""
    path = os.path.normpath(os.fspath(path))
    fn = _require_native_fn("erofs_list_json")
    _record("erofs_list")
    return json.loads(fn(path, target, bool(recursive), offset))


def erofs_stat(path, internal_path, *, offset=None):
    """Stat an individual inode in an EROFS image without unpacking."""
    path = os.path.normpath(os.fspath(path))
    fn = _require_native_fn("erofs_stat_json")
    _record("erofs_stat")
    return json.loads(fn(path, str(internal_path), offset))


def erofs_read_bytes(path, internal_path, *, offset=None):
    """Read file bytes directly from an EROFS image without unpacking."""
    path = os.path.normpath(os.fspath(path))
    fn = _require_native_fn("erofs_read_bytes")
    _record("erofs_read_bytes")
    return bytes(fn(path, str(internal_path), offset))


def erofs_unpack(
    image,
    out_dir,
    *,
    image_base_name=None,
    targets=None,
    recursive=True,
    config_only=False,
    write_configs=True,
    overwrite=True,
    threads=0,
    offset=None,
    symlink_mode=None,
    progress=None,
):
    """Unpack an EROFS image into an output directory.

    Writes <part>_fs_config, <part>_file_contexts, and <part>_fs_options.
    Returns dict: ExtractReport.
    """
    image = os.path.normpath(os.fspath(image))
    out_dir = os.path.normpath(os.fspath(out_dir))
    fn = _require_native_fn("erofs_unpack")
    _record("erofs_unpack")
    targs = [str(t) for t in targets] if targets else None
    cb = _resolve_progress_cb(progress)
    return json.loads(
        fn(
            image,
            out_dir,
            image_base_name,
            targs,
            bool(recursive),
            bool(config_only),
            bool(write_configs),
            bool(overwrite),
            int(threads or 0),
            offset,
            str(symlink_mode) if symlink_mode is not None else None,
            cb,
        )
    )


def erofs_pack(
    source_dir,
    output_image,
    *,
    algo=None,
    level=None,
    pcluster_size=None,
    mount_point=None,
    fs_config=None,
    file_contexts=None,
    uuid=None,
    timestamp=None,
    block_size=None,
    inline_data=None,
    inode_format=None,
    force_uid=None,
    force_gid=None,
    all_root=None,
    enable_crc=None,
    threads=0,
    progress=None,
):
    """Pack a directory into a standard Linux/Android EROFS image.

    Returns dict: PackReport.
    """
    source_dir = os.path.normpath(os.fspath(source_dir))
    output_image = os.path.normpath(os.fspath(output_image))
    fc = os.path.normpath(os.fspath(fs_config)) if fs_config else None
    sx = os.path.normpath(os.fspath(file_contexts)) if file_contexts else None
    fn = _require_native_fn("erofs_pack")
    _record("erofs_pack")
    cb = _resolve_progress_cb(progress)
    return json.loads(
        fn(
            source_dir,
            output_image,
            str(algo) if algo is not None else None,
            int(level) if level is not None else None,
            int(pcluster_size) if pcluster_size is not None else None,
            str(mount_point) if mount_point is not None else None,
            fc,
            sx,
            str(uuid) if uuid is not None else None,
            int(timestamp) if timestamp is not None else None,
            int(block_size) if block_size is not None else None,
            bool(inline_data) if inline_data is not None else None,
            str(inode_format) if inode_format is not None else None,
            int(force_uid) if force_uid is not None else None,
            int(force_gid) if force_gid is not None else None,
            bool(all_root) if all_root is not None else None,
            bool(enable_crc) if enable_crc is not None else None,
            int(threads or 0),
            cb,
        )
    )


def erofs_verify(image, *, check_data=True, verify_digest=False, threads=0, offset=None):
    """Verify integrity of an EROFS filesystem image (fsck equivalent).

    Returns dict: VerifyReport.
    """
    image = os.path.normpath(os.fspath(image))
    fn = _require_native_fn("erofs_verify_json")
    _record("erofs_verify")
    return json.loads(
        fn(image, bool(check_data), bool(verify_digest), int(threads or 0), offset)
    )


def erofs_edit_write(image, internal_path, data, *, allow_grow=True, strip_avb=True):
    """Direct edit: write file data in-place without unpacking.

    Returns dict: WriteResult.
    """
    image = os.path.normpath(os.fspath(image))
    fn = _require_native_fn("erofs_edit_write")
    _record("erofs_edit_write")
    return json.loads(
        fn(image, str(internal_path), bytes(data), bool(allow_grow), bool(strip_avb))
    )


def erofs_edit_chmod(image, internal_path, perm_octal):
    """Direct edit: update inode mode/permissions in-place."""
    image = os.path.normpath(os.fspath(image))
    fn = _require_native_fn("erofs_edit_chmod")
    _record("erofs_edit_chmod")
    return int(fn(image, str(internal_path), int(perm_octal)))


def erofs_edit_selinux(image, internal_path, new_ctx):
    """Direct edit: update SELinux security context in-place."""
    image = os.path.normpath(os.fspath(image))
    fn = _require_native_fn("erofs_edit_selinux")
    _record("erofs_edit_selinux")
    return str(fn(image, str(internal_path), str(new_ctx)))


def erofs_edit_rm(image, internal_path, *, recursive=False, strip_avb=True):
    """Direct edit: remove (unlink) a file or directory in-place without unpacking."""
    image = os.path.normpath(os.fspath(image))
    fn = _require_native_fn("erofs_edit_rm")
    _record("erofs_edit_rm")
    return json.loads(
        fn(image, str(internal_path), bool(recursive), bool(strip_avb))
    )


def erofs_compact(image, *, dry_run=False, relocate_meta=True):
    """In-place compaction: reclaim holes and relocate metadata.

    Returns dict: CompactReport.
    """
    image = os.path.normpath(os.fspath(image))
    fn = _require_native_fn("erofs_compact")
    _record("erofs_compact")
    return json.loads(fn(image, bool(dry_run), bool(relocate_meta)))


def erofs_usage(image, *, offset=None):
    """Space usage breakdown of an EROFS image.

    Returns dict: UsageReport.
    """
    image = os.path.normpath(os.fspath(image))
    fn = _require_native_fn("erofs_usage_json")
    _record("erofs_usage")
    return json.loads(fn(image, offset))


def erofs_avb_inspect(image):
    """Inspect AVB footer on an EROFS image file.

    Returns dict or None: AvbFooter.
    """
    image = os.path.normpath(os.fspath(image))
    fn = _require_native_fn("erofs_avb_inspect_json")
    _record("erofs_avb_inspect")
    return json.loads(fn(image))


def erofs_avb_strip(image):
    """Strip AVB footer from an EROFS image file.

    Returns dict: AvbFooter.
    """
    image = os.path.normpath(os.fspath(image))
    fn = _require_native_fn("erofs_avb_strip_json")
    _record("erofs_avb_strip")
    return json.loads(fn(image))


def erofs_edit_plan(image, internal_path, new_len, *, strip_avb=True):
    """Plan an EROFS write (inplace_plain / inplace_inline / append) without mutating the image."""
    image = os.path.normpath(os.fspath(image))
    fn = _require_native_fn("erofs_edit_plan_json")
    _record("erofs_edit_plan")
    return json.loads(fn(image, str(internal_path), int(new_len), bool(strip_avb)))


def erofs_open_reader(image, *, offset=0):
    """Open a persistent NativeErofsReader handle on an EROFS image file (raw or sparse)."""
    image = os.path.normpath(os.fspath(image))
    cls = _require_native_fn("NativeErofsReader")
    _record("erofs_open_reader")
    return cls(image, int(offset))


def erofs_open_bytes_reader(data):
    """Open a persistent NativeErofsReader handle directly on in-memory EROFS bytes."""
    cls = _require_native_fn("NativeErofsReader")
    _record("erofs_open_bytes_reader")
    return cls.from_bytes(bytes(data))


def compute_avatar_target_dim(screen_width, screen_height, dpr=1.0, max_ui_logical=64):
    """Compute precision square avatar pixel dimension for the active display."""
    w = max(1, int(screen_width or 1920))
    h = max(1, int(screen_height or 1080))
    ratio = float(dpr) if dpr and float(dpr) > 0 else 1.0
    ui_logical = max(16, int(max_ui_logical or 64))
    module = _get_native()
    fn = getattr(module, "compute_avatar_target_dim", None) if module is not None else None
    if fn is not None:
        _record("compute_avatar_target_dim")
        return int(fn(w, h, ratio, ui_logical))
    import math
    phys_w = max(1.0, round(w * ratio))
    phys_h = max(1.0, round(h * ratio))
    short_edge = min(phys_w, phys_h)
    raw = math.ceil(max(ui_logical * 2.0 * ratio, short_edge * ((ui_logical * 2.0) / 720.0)))
    even = (raw + 1) & ~1
    return max(32, min(1024, even))


def sync_active_avatars(source_dir, active_dir, screen_width, screen_height, dpr=1.0, max_ui_logical=64, crop=True):
    """Resize source-cached avatars into active_dir sized for the active display."""
    src = os.path.normpath(os.fspath(source_dir))
    dst = os.path.normpath(os.fspath(active_dir))
    w = max(1, int(screen_width or 1920))
    h = max(1, int(screen_height or 1080))
    ratio = float(dpr) if dpr and float(dpr) > 0 else 1.0
    ui_logical = max(16, int(max_ui_logical or 64))
    crop_flag = bool(crop)
    module = _get_native()
    fn = getattr(module, "sync_active_avatars_json", None) if module is not None else None
    if fn is not None:
        result = json.loads(fn(src, dst, w, h, ratio, ui_logical, crop_flag))
        _record("sync_active_avatars")
        return result
    if os.environ.get("RAK_NATIVE_BACKEND", "auto").lower() == "rust":
        raise RuntimeError("Rust avatar engine unavailable; run python tools/build_native.py")
    # Pure-Python fallback when native module is unavailable or RAK_NATIVE_BACKEND=python.
    import time
    from pathlib import Path
    from PIL import Image
    started = time.perf_counter()
    target_dim = compute_avatar_target_dim(w, h, ratio, ui_logical)
    src_path = Path(src)
    dst_path = Path(dst)
    dst_path.mkdir(parents=True, exist_ok=True)
    meta_path = dst_path / ".active_meta.json"
    existing_meta = {}
    if meta_path.is_file():
        try:
            existing_meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            existing_meta = {}
    target_changed = (
        existing_meta.get("target_dim") != target_dim
        or existing_meta.get("engine_version") != 2
        or existing_meta.get("crop", True) != crop_flag
    )
    sources = {}
    for entry in sorted(src_path.iterdir()):
        if not entry.is_file() or entry.name.startswith(".") or entry.name.endswith(".tmp"):
            continue
        if entry.suffix.lower() not in (".png", ".jpg", ".jpeg", ".webp"):
            continue
        stem = entry.stem
        if stem in sources and sources[stem].suffix.lower() == ".png" and entry.suffix.lower() != ".png":
            continue
        sources[stem] = entry
    expected = {f"{stem}.png" for stem in sources}
    removed = 0
    for item in list(dst_path.iterdir()):
        if item.is_file() and item.name != ".active_meta.json" and item.name not in expected:
            try:
                item.unlink()
                removed += 1
            except OSError:
                pass
    stamps = {}
    processed = 0
    reused = 0
    old_sources = existing_meta.get("sources") or {}
    for stem, entry in sources.items():
        out_name = f"{stem}.png"
        out_file = dst_path / out_name
        st = entry.stat()
        stamp = {"len": st.st_size, "mtime_ns": st.st_mtime_ns}
        stamps[out_name] = stamp
        if not target_changed and out_file.is_file() and old_sources.get(out_name) == stamp:
            reused += 1
            continue
        try:
            with Image.open(entry) as im:
                im = im.convert("RGBA")
                if crop_flag:
                    side = min(im.width, im.height)
                    if im.width != im.height:
                        left = (im.width - side) // 2
                        top = (im.height - side) // 2
                        im = im.crop((left, top, left + side, top + side))
                    dim = max(1, min(target_dim, side))
                    dst_w, dst_h = dim, dim
                else:
                    max_side = max(1, max(im.width, im.height))
                    scale_dim = max(1, min(target_dim, max_side))
                    dst_w = max(1, round(im.width * scale_dim / max_side))
                    dst_h = max(1, round(im.height * scale_dim / max_side))
                if im.width != dst_w or im.height != dst_h:
                    im = im.resize((dst_w, dst_h), Image.Resampling.LANCZOS)
                tmp = dst_path / f".{out_name}.tmp.{os.getpid()}"
                im.save(tmp, format="PNG")
                os.replace(tmp, out_file)
            processed += 1
        except Exception:
            if not (out_name == "google.png" and crop_flag):
                raise
    if processed or removed or target_changed or not meta_path.is_file():
        meta = {
            "engine_version": 2,
            "target_dim": target_dim,
            "crop": crop_flag,
            "screen_width": max(1, round(w * ratio)),
            "screen_height": max(1, round(h * ratio)),
            "dpr_milli": max(1, round(ratio * 1000)),
            "sources": stamps,
        }
        tmp_meta = dst_path / f".active_meta.json.tmp.{os.getpid()}"
        tmp_meta.write_text(json.dumps(meta, indent=2), encoding="utf-8")
        os.replace(tmp_meta, meta_path)
    return {
        "target_dim": target_dim,
        "screen_width": w,
        "screen_height": h,
        "phys_width": max(1, round(w * ratio)),
        "phys_height": max(1, round(h * ratio)),
        "dpr": ratio,
        "processed": processed,
        "reused": reused,
        "removed": removed,
        "elapsed_ms": int((time.perf_counter() - started) * 1000),
    }


# ── sdatimg (sdat2img & img2sdat native) ──────────────────────────────────────

def sdat_transfer_list_info(path):
    """Inspect an Android OTA transfer.list file.

    Returns dict: {version, new_blocks, stash_slots, max_stashed_blocks, commands_count, total_bytes}.
    """
    path = os.path.normpath(os.fspath(path))
    fn = _require_native_fn("sdat_transfer_list_info_json")
    _record("sdat_transfer_list_info")
    return json.loads(fn(path))


def sdat2img(transfer_list, new_dat, output_image, *, sparse_output=False, no_clobber=False, quiet=True):
    """Convert Android block-based OTA files (transfer.list + new.dat) to partition image (.img).

    Returns str: output image path.
    """
    tl = os.path.normpath(os.fspath(transfer_list))
    dat = os.path.normpath(os.fspath(new_dat))
    out = os.path.normpath(os.fspath(output_image))
    fn = _require_native_fn("sdat_sdat2img")
    _record("sdat_sdat2img")
    return fn(tl, dat, out, bool(sparse_output), bool(no_clobber), bool(quiet))


def img2sdat(
    input_image,
    outdir,
    *,
    version=4,
    prefix=None,
    input_format=None,
    file_map=None,
    clobbered_blocks=None,
    src_image=None,
    threads=None,
    quiet=True,
):
    """Convert partition image (sparse or raw) to Android block-based OTA files (.new.dat, .transfer.list, .patch.dat).

    Returns str: output directory prefix.
    """
    img = os.path.normpath(os.fspath(input_image))
    out = os.path.normpath(os.fspath(outdir))
    f_map = os.path.normpath(os.fspath(file_map)) if file_map else None
    s_img = os.path.normpath(os.fspath(src_image)) if src_image else None
    fn = _require_native_fn("sdat_img2sdat")
    _record("sdat_img2sdat")
    return fn(
        img,
        out,
        int(version if version is not None else 4),
        str(prefix) if prefix is not None else None,
        str(input_format) if input_format is not None else None,
        f_map,
        str(clobbered_blocks) if clobbered_blocks is not None else None,
        s_img,
        int(threads) if threads is not None else None,
        bool(quiet),
    )


# ── brotli (brotli-rs native) ────────────────────────────────────────────────

def brotli_compress_file(input_path, output_path, *, quality=1, lgwin=None, mode="generic"):
    """Compress input_path to output_path using native pure-Rust brotli-rs.

    Returns dict: {input_path, output_path, bytes_in, bytes_out}.
    """
    src = os.path.normpath(os.fspath(input_path))
    dst = os.path.normpath(os.fspath(output_path))
    fn = _require_native_fn("brotli_compress_file_json")
    _record("brotli_compress_file")
    return json.loads(
        fn(
            src,
            dst,
            int(quality),
            int(lgwin) if lgwin is not None else None,
            str(mode),
        )
    )


def brotli_decompress_file(input_path, output_path, *, allow_concatenated=False):
    """Decompress a .br file to output_path using native pure-Rust brotli-rs.

    Returns dict: {input_path, output_path, bytes_in, bytes_out}.
    """
    src = os.path.normpath(os.fspath(input_path))
    dst = os.path.normpath(os.fspath(output_path))
    fn = _require_native_fn("brotli_decompress_file_json")
    _record("brotli_decompress_file")
    return json.loads(fn(src, dst, bool(allow_concatenated)))


def brotli_decompressed_size(input_path, *, allow_concatenated=False):
    """Measure exact uncompressed byte size of a .br file via native brotli-rs."""
    src = os.path.normpath(os.fspath(input_path))
    fn = _require_native_fn("brotli_decompressed_size")
    _record("brotli_decompressed_size")
    return int(fn(src, bool(allow_concatenated)))


def brotli_compress_bytes(data, *, quality=1, lgwin=None, mode="generic"):
    """Compress an in-memory buffer using native brotli-rs."""
    fn = _require_native_fn("brotli_compress_bytes")
    _record("brotli_compress_bytes")
    return bytes(
        fn(
            bytes(data),
            int(quality),
            int(lgwin) if lgwin is not None else None,
            str(mode),
        )
    )


def brotli_decompress_bytes(data, *, allow_concatenated=False):
    """Decompress an in-memory brotli buffer using native brotli-rs."""
    fn = _require_native_fn("brotli_decompress_bytes")
    _record("brotli_decompress_bytes")
    return bytes(fn(bytes(data), bool(allow_concatenated)))


def brotli_open_reader(input_path):
    """Open a streaming file-like native Brotli decompressor."""
    src = os.path.normpath(os.fspath(input_path))
    cls = _require_native_fn("BrotliFileReader")
    _record("brotli_open_reader")
    return cls(src)


# ── platform-tools (adb, fastboot, mke2fs, make_f2fs, etc1tool, hprof-conv, sqlite3) ──

def platform_cancel_token():
    """Create a thread-safe cancellation token for native ADB/fastboot operations."""
    cls = _require_native_fn("NativeCancelToken")
    _record("platform_cancel_token")
    return cls()


def adb_ensure_server(host="127.0.0.1", port=5037):
    """Ensure the in-process native ADB host server is listening on (host, port)."""
    fn = _require_native_fn("adb_ensure_server")
    _record("adb_ensure_server")
    return bool(fn(str(host), int(port)))


def adb_shutdown_servers():
    """Shut down all embedded in-process ADB host servers cleanly."""
    module = _get_native()
    if module is None:
        return
    fn = getattr(module, "adb_shutdown_servers", None)
    if fn is not None:
        _record("adb_shutdown_servers")
        fn()


def adb_run(args, *, cwd=None, timeout_ms=None, cancel=None, on_output=None, on_progress=None):
    """Execute an ADB CLI command natively in-process and capture structured output."""
    fn = _require_native_fn("adb_run_json")
    _record("adb_run")
    argv = [str(a) for a in args]
    cwd_str = os.path.normpath(os.fspath(cwd)) if cwd else None
    timeout_val = int(timeout_ms) if timeout_ms is not None else None
    return json.loads(fn(argv, cwd_str, timeout_val, cancel, on_output, on_progress))


def adb_run_stdio(args):
    """Execute an ADB CLI command directly attached to process stdio."""
    fn = _require_native_fn("adb_run_stdio")
    _record("adb_run_stdio")
    return int(fn([str(a) for a in args]))


def fastboot_list_devices():
    """Discover connected fastboot devices natively via USB in-process."""
    fn = _require_native_fn("fastboot_list_devices_json")
    _record("fastboot_list_devices")
    return json.loads(fn())


def fastboot_run(args, *, cwd=None, timeout_ms=None, cancel=None, on_output=None, on_progress=None):
    """Execute a fastboot CLI command natively in-process with byte-level progress."""
    fn = _require_native_fn("fastboot_run_json")
    _record("fastboot_run")
    argv = [str(a) for a in args]
    cwd_str = os.path.normpath(os.fspath(cwd)) if cwd else None
    timeout_val = int(timeout_ms) if timeout_ms is not None else None
    return json.loads(fn(argv, cwd_str, timeout_val, cancel, on_output, on_progress))


def fastboot_run_stdio(args):
    """Execute a fastboot CLI command directly attached to process stdio."""
    fn = _require_native_fn("fastboot_run_stdio")
    _record("fastboot_run_stdio")
    return int(fn([str(a) for a in args]))


def platform_tool_run_stdio(tool, args):
    """Execute any native platform-tools CLI (adb, fastboot, sqlite3, etc.) on stdio."""
    fn = _require_native_fn("platform_tool_run_stdio")
    _record("platform_tool_run_stdio")
    return int(fn(str(tool), [str(a) for a in args]))


def platform_tools_version():
    """Return metadata for the embedded native platform-tools-rs suite."""
    fn = _require_native_fn("platform_tools_version_json")
    _record("platform_tools_version")
    return json.loads(fn())


def render_markdown(text, *, path=None, theme=None):
    """Parse and render Markdown into structured blocks via Rust rak_native."""
    module = _get_native()
    fn = getattr(module, "markdown_render_json", None) if module is not None else None
    if fn is None:
        if os.environ.get("RAK_NATIVE_BACKEND", "auto").lower() == "rust":
            raise RuntimeError("Rust markdown_render_json unavailable. Run python tools/build_native.py")
        lines = str(text or "").splitlines()
        return {
            "title": os.path.basename(os.fspath(path)) if path else "Markdown",
            "blocks": [{"kind": "paragraph", "level": 0, "anchor": "", "text": str(text or ""),
                        "html": str(text or ""), "language": "", "code": "", "url": "", "alt": "",
                        "items": [], "headersHtml": [], "headersText": [], "alignments": [], "rows": []}] if text else [],
            "headings": [],
            "stats": {"headingsCount": 0, "words": len(str(text or "").split()), "lines": len(lines), "readMinutes": 1 if text else 0},
        }
    theme_json = json.dumps(theme) if theme else None
    path_str = os.fspath(path) if path else None
    _record("markdown_render")
    return json.loads(fn(str(text or ""), path_str, theme_json))


def render_markdown_file(path, *, theme=None):
    """Read and render a Markdown file on disk via Rust rak_native."""
    src = os.path.normpath(os.fspath(path))
    module = _get_native()
    fn = getattr(module, "markdown_render_file_json", None) if module is not None else None
    if fn is None:
        if os.environ.get("RAK_NATIVE_BACKEND", "auto").lower() == "rust":
            raise RuntimeError("Rust markdown_render_file_json unavailable. Run python tools/build_native.py")
        with open(src, "r", encoding="utf-8", errors="ignore") as f:
            source = f.read()
        return {"source": source, "document": render_markdown(source, path=src, theme=theme)}
    theme_json = json.dumps(theme) if theme else None
    _record("markdown_render_file")
    return json.loads(fn(src, theme_json))


# ── vdev full-firmware analyzer & boot simulator native primitives ──

def vdev_probe_super(super_path, slot=0):
    fn = _require_native_fn("vdev_probe_super_json")
    _record("vdev_probe_super")
    return json.loads(fn(os.path.normpath(os.fspath(super_path)), int(slot)))


def vdev_super_open_erofs(super_path, partition, slot=0):
    fn = _require_native_fn("vdev_super_open_erofs")
    _record("vdev_super_open_erofs")
    return fn(os.path.normpath(os.fspath(super_path)), str(partition), int(slot))


def vdev_super_open_ext(super_path, partition, slot=0):
    fn = _require_native_fn("vdev_super_open_ext")
    _record("vdev_super_open_ext")
    return fn(os.path.normpath(os.fspath(super_path)), str(partition), int(slot))


def vdev_probe_zip(zip_path):
    fn = _require_native_fn("vdev_probe_zip_json")
    _record("vdev_probe_zip")
    return json.loads(fn(os.path.normpath(os.fspath(zip_path))))


def vdev_read_zip_entry_bytes(zip_path, entry_name):
    fn = _require_native_fn("vdev_read_zip_entry_bytes")
    _record("vdev_read_zip_entry_bytes")
    return bytes(fn(os.path.normpath(os.fspath(zip_path)), str(entry_name)))


def vdev_prepare_zip_images(zip_path, entries, cache_dir):
    fn = _require_native_fn("vdev_prepare_zip_images_json")
    _record("vdev_prepare_zip_images")
    return json.loads(fn(
        os.path.normpath(os.fspath(zip_path)),
        [str(e) for e in entries],
        os.path.normpath(os.fspath(cache_dir)),
    ))


def vdev_inspect_boot(path):
    fn = _require_native_fn("vdev_inspect_boot_json")
    _record("vdev_inspect_boot")
    return json.loads(fn(os.path.normpath(os.fspath(path))))


def vdev_inspect_boot_bytes(data):
    fn = _require_native_fn("vdev_inspect_boot_bytes_json")
    _record("vdev_inspect_boot_bytes")
    return json.loads(fn(bytes(data)))


def vdev_inspect_zip_boot(zip_path, entry_name, *, cache_dir=None):
    fn = _require_native_fn("vdev_inspect_zip_boot_json")
    _record("vdev_inspect_zip_boot")
    cdir = os.path.normpath(os.fspath(cache_dir)) if cache_dir else None
    return json.loads(fn(os.path.normpath(os.fspath(zip_path)), str(entry_name), cdir))


def vdev_inspect_vbmeta(path):
    fn = _require_native_fn("vdev_inspect_vbmeta_json")
    _record("vdev_inspect_vbmeta")
    return json.loads(fn(os.path.normpath(os.fspath(path))))


def vdev_inspect_vbmeta_bytes(data):
    fn = _require_native_fn("vdev_inspect_vbmeta_bytes_json")
    _record("vdev_inspect_vbmeta_bytes")
    return json.loads(fn(bytes(data)))


def vdev_inspect_zip_vbmeta(zip_path, entry_name, *, cache_dir=None):
    fn = _require_native_fn("vdev_inspect_zip_vbmeta_json")
    _record("vdev_inspect_zip_vbmeta")
    cdir = os.path.normpath(os.fspath(cache_dir)) if cache_dir else None
    return json.loads(fn(os.path.normpath(os.fspath(zip_path)), str(entry_name), cdir))


def vdev_inspect_dtbo(path):
    fn = _require_native_fn("vdev_inspect_dtbo_json")
    _record("vdev_inspect_dtbo")
    return json.loads(fn(os.path.normpath(os.fspath(path))))


def vdev_inspect_zip_dtbo(zip_path, entry_name, *, cache_dir=None):
    fn = _require_native_fn("vdev_inspect_zip_dtbo_json")
    _record("vdev_inspect_zip_dtbo")
    cdir = os.path.normpath(os.fspath(cache_dir)) if cache_dir else None
    return json.loads(fn(os.path.normpath(os.fspath(zip_path)), str(entry_name), cdir))


def vdev_verify_avb_hash(path, image_size, hash_algo, salt_hex, expected_digest_hex, *, zip_entry=None):
    fn = _require_native_fn("vdev_verify_avb_hash_json")
    _record("vdev_verify_avb_hash")
    return json.loads(fn(
        os.path.normpath(os.fspath(path)),
        int(image_size),
        str(hash_algo),
        str(salt_hex),
        str(expected_digest_hex),
        str(zip_entry) if zip_entry else None,
    ))


def vdev_verify_hashtree(img_path, image_size, salt_hex, data_block_size, hash_block_size,
                         algorithm="sha256", expected_root_hex=""):
    fn = _require_native_fn("vdev_verify_hashtree_json")
    _record("vdev_verify_hashtree")
    return json.loads(fn(
        os.path.normpath(os.fspath(img_path)),
        int(image_size),
        str(salt_hex),
        int(data_block_size),
        int(hash_block_size),
        str(algorithm),
        str(expected_root_hex),
    ))


def vdev_scan_elfs(paths, *, with_symbols=False):
    fn = _require_native_fn("vdev_scan_elfs_json")
    _record("vdev_scan_elfs")
    return json.loads(fn([os.path.normpath(os.fspath(p)) for p in paths], bool(with_symbols)))


def vdev_scan_erofs_elfs(native_reader, paths, *, with_symbols=False):
    fn = _require_native_fn("vdev_scan_erofs_elfs_json")
    _record("vdev_scan_erofs_elfs")
    return json.loads(fn(native_reader, [str(p) for p in paths], bool(with_symbols)))


def vdev_scan_ext_elfs(native_reader, paths, *, with_symbols=False):
    fn = _require_native_fn("vdev_scan_ext_elfs_json")
    _record("vdev_scan_ext_elfs")
    return json.loads(fn(native_reader, [str(p) for p in paths], bool(with_symbols)))


def vdev_scan_elf_bytes(items, *, with_symbols=False):
    fn = _require_native_fn("vdev_scan_elf_bytes_json")
    _record("vdev_scan_elf_bytes")
    return json.loads(fn([(str(p), bytes(b)) for p, b in items], bool(with_symbols)))


def vdev_scan_ko_files(items):
    fn = _require_native_fn("vdev_scan_ko_files_json")
    _record("vdev_scan_ko_files")
    return json.loads(fn([(str(vp), os.path.normpath(os.fspath(p))) for vp, p in items]))


def vdev_scan_erofs_kos(native_reader, items):
    fn = _require_native_fn("vdev_scan_erofs_kos_json")
    _record("vdev_scan_erofs_kos")
    return json.loads(fn(native_reader, [(str(vp), str(rel)) for vp, rel in items]))


def vdev_scan_ext_kos(native_reader, items):
    fn = _require_native_fn("vdev_scan_ext_kos_json")
    _record("vdev_scan_ext_kos")
    return json.loads(fn(native_reader, [(str(vp), str(rel)) for vp, rel in items]))


def vdev_index_erofs_apexes(native_reader, items):
    fn = _require_native_fn("vdev_index_erofs_apexes_json")
    _record("vdev_index_erofs_apexes")
    return json.loads(fn(native_reader, [(str(vp), str(rel)) for vp, rel in items]))


def vdev_index_ext_apexes(native_reader, items):
    fn = _require_native_fn("vdev_index_ext_apexes_json")
    _record("vdev_index_ext_apexes")
    return json.loads(fn(native_reader, [(str(vp), str(rel)) for vp, rel in items]))


def vdev_index_file_apexes(items):
    fn = _require_native_fn("vdev_index_file_apexes_json")
    _record("vdev_index_file_apexes")
    return json.loads(fn([(str(vp), os.path.normpath(os.fspath(p))) for vp, p in items]))


def vdev_scan_apk_files(items):
    fn = _require_native_fn("vdev_scan_apk_files_json")
    _record("vdev_scan_apk_files")
    return json.loads(fn([(str(vp), os.path.normpath(os.fspath(p))) for vp, p in items]))


def vdev_scan_erofs_apks(native_reader, items):
    fn = _require_native_fn("vdev_scan_erofs_apks_json")
    _record("vdev_scan_erofs_apks")
    return json.loads(fn(native_reader, [(str(vp), str(rel)) for vp, rel in items]))


def vdev_scan_ext_apks(native_reader, items):
    fn = _require_native_fn("vdev_scan_ext_apks_json")
    _record("vdev_scan_ext_apks")
    return json.loads(fn(native_reader, [(str(vp), str(rel)) for vp, rel in items]))


def vdev_parse_classpath_pb(data):
    fn = _require_native_fn("vdev_parse_classpath_pb_json")
    _record("vdev_parse_classpath_pb")
    return json.loads(fn(bytes(data)))


def vdev_walk_erofs(native_reader):
    fn = _require_native_fn("vdev_walk_erofs_json")
    _record("vdev_walk_erofs")
    return json.loads(fn(native_reader))


def vdev_walk_ext(native_reader):
    fn = _require_native_fn("vdev_walk_ext_json")
    _record("vdev_walk_ext")
    return json.loads(fn(native_reader))


def vdev_read_erofs_paths(native_reader, paths):
    fn = _require_native_fn("vdev_read_erofs_paths_json")
    _record("vdev_read_erofs_paths")
    return fn(native_reader, [str(p) for p in paths])


def vdev_selinux_policy():
    cls = _require_native_fn("NativeSelinuxPolicy")
    _record("vdev_selinux_policy")
    return cls()


def vdev_dex_verifier():
    cls = _require_native_fn("NativeDexVerifier")
    _record("vdev_dex_verifier")
    return cls()


def vdev_parse_arsc_bytes(data):
    fn = _require_native_fn("vdev_parse_arsc_bytes_json")
    _record("vdev_parse_arsc_bytes")
    return json.loads(fn(bytes(data)))





