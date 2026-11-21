# SPDX-License-Identifier: MPL-2.0
import os
import re
import shlex

from core.app_paths import BIN_DIR
from core.binary_handler import resolve_binary

BIN_PATH = BIN_DIR

UUID_RE = re.compile(
    r"Filesystem UUID:\s*([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})"
)


def get_uuid_from_fs_options(fs_options_path):
    if not os.path.isfile(fs_options_path):
        return None, f"fs_options tidak ditemukan: {fs_options_path}"

    try:
        with open(fs_options_path, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                if "Filesystem UUID:" in line:
                    m = UUID_RE.search(line)
                    if m:
                        return m.group(1), None
                    return None, "UUID ditemukan tapi formatnya tidak valid"
    except Exception as e:
        return None, f"Gagal membaca fs_options: {e}"

    return None, "UUID tidak ditemukan di fs_options"


def unpack_erofs(img_path):
    UNPACK_EROFS = resolve_binary(BIN_PATH, "extract.erofs.exe")
    if not UNPACK_EROFS:
        return False, "EROFS Environtment not found"

    # FS CHECK — pakai header_magic (no binary spawn)
    from core.header_magic import is_fs_image as _check_fs
    fs_type = _check_fs(img_path)
    if fs_type != "EROFS":
        return False, f"Tipe filesystem tidak cocok: {fs_type or 'UNKNOWN'} (harus EROFS)"

    parent = os.path.dirname(img_path)
    name = os.path.splitext(os.path.basename(img_path))[0]
    result_dir = os.path.join(parent, name)

    if not os.path.exists(parent):
        os.makedirs(parent, exist_ok=True)

    from core.storage_detector import get_optimal_io_workers
    threads = get_optimal_io_workers(parent, max_cap=64, hdd_cap=4)

    try:
        from core.native_backend import erofs_unpack
        report = erofs_unpack(
            img_path,
            parent,
            image_base_name=name,
            threads=threads,
            write_configs=True,
            overwrite=True,
        )
        files = report.get("files_extracted", 0)
        return True, f"OK → {result_dir} ({files} files extracted)"
    except Exception as e:
        return False, f"erofs_unpack failed: {e}"


def _patch_fs_config(fs_config_file: str, folder_path: str, part_name: str):
    # Baca entry yang sudah ada (key = path string)
    existing = set()
    try:
        with open(fs_config_file, "r", encoding="utf-8", errors="ignore") as f:
            for ln in f:
                s = ln.strip()
                if s and not s.startswith("#"):
                    existing.add(s.split()[0])
    except Exception as e:
        print(f"[PATCH_FSCFG] read error: {e}")
        return

    new_lines = []

    # Walk folder dan cari path yang missing
    for root, dirs, files in os.walk(folder_path):
        rel_dir = os.path.relpath(root, os.path.dirname(folder_path)).replace("\\", "/")
        if rel_dir not in existing:
            new_lines.append(f"{rel_dir} 0 0 0755")
            existing.add(rel_dir)

        for fname in files:
            rel_file = os.path.join(
                os.path.relpath(root, os.path.dirname(folder_path)), fname
            ).replace("\\", "/")
            if rel_file not in existing:
                new_lines.append(f"{rel_file} 0 0 0644")
                existing.add(rel_file)

    if not new_lines:
        return

    print(f"[PATCH_FSCFG] adding {len(new_lines)} missing entries to fs_config")
    try:
        with open(fs_config_file, "a", encoding="utf-8", newline="\n") as f:
            for ln in new_lines:
                f.write(ln + "\n")
    except Exception as e:
        print(f"[PATCH_FSCFG] write error: {e}")


def repack_erofs(folder_path, part_name=None):
    MKFS_BIN = resolve_binary(BIN_PATH, "mkfs.erofs.exe")
    if not MKFS_BIN:
        return False, "EROFS ENVIRONTMENT NOT FOUND"

    # Ambil nama partisi otomatis
    if part_name is None:
        part_name = os.path.basename(folder_path.rstrip("/\\"))

    from core.fsdetect import detect_fs_root
    parent = os.path.dirname(folder_path)
    name   = os.path.basename(folder_path.rstrip("/\\"))

    # FS ROOT CHECK
    fs_type = detect_fs_root(parent, name)
    if fs_type != "EROFS":
        return False, f"Folder bukan filesystem EROFS: {fs_type or 'UNKNOWN'}"

    cfg_dir = os.path.join(parent, "config")

    fs_options_file = os.path.join(cfg_dir, f"{name}_fs_options")
    fs_config_file  = os.path.join(cfg_dir, f"{name}_fs_config")
    file_contexts   = os.path.join(cfg_dir, f"{name}_file_contexts")

    # UUID dari fs_options saja
    uuid_val, err = get_uuid_from_fs_options(fs_options_file)
    if err:
        return False, f"UUID lookup gagal: {err}"

    # Pastikan file-file config lain ada
    if not os.path.isfile(fs_config_file):
        return False, f"fs_config tidak ditemukan: {fs_config_file}"
    if not os.path.isfile(file_contexts):
        return False, f"file_context tidak ditemukan: {file_contexts}"

    # AUTO-PATCH fs_config: tambahkan entry yang missing
    _patch_fs_config(fs_config_file, folder_path, part_name)

    # Output img baru
    out_img = os.path.join(parent, f"{name}_new.img")

    from core.repack_policy import get_erofs_args

    z_arg, t_arg, erofs_mode = get_erofs_args()
    from core.storage_detector import get_optimal_io_workers
    workers = get_optimal_io_workers(folder_path, max_cap=64, hdd_cap=4)

    ts = 0
    t_parts = shlex.split(t_arg, posix=False)
    if len(t_parts) >= 2 and t_parts[0] == "-T":
        try:
            ts = int(t_parts[1])
        except (ValueError, TypeError):
            ts = 0

    print("========== EROFS REPACK DEBUG ==========")
    print(f"Mode      : {erofs_mode}")
    print(f"Algorithm : {z_arg}")
    print(f"Timestamp : {ts}")
    print(f"UUID      : {uuid_val}")
    print(f"Workers   : {workers}")
    print(f"Output    : {out_img}")
    print("========================================")

    try:
        from core.native_backend import erofs_pack
        report = erofs_pack(
            source_dir=folder_path,
            output_image=out_img,
            algo=z_arg,
            mount_point=f"/{part_name}",
            fs_config=fs_config_file,
            file_contexts=file_contexts,
            uuid=uuid_val,
            timestamp=ts,
            threads=workers,
        )
        return True, f"OK → {out_img}"
    except Exception as e:
        return False, f"erofs_pack error: {e}"
