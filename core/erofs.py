# SPDX-License-Identifier: MPL-2.0
import os
import re
import shlex

from core.binary_handler import is_available

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
    if not is_available("extract.erofs.exe"):
        return False, "Native EROFS engine (rak_native) not available"

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

    from core.threader_control import threader_control
    threads = threader_control.recommend_tool_threads(parent, max_cap=64, hdd_cap=4)

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


def _patch_fs_config(fs_config_file: str, folder_path: str, part_name: str, *, mount_point: str | None = None):
    from core.aosp_fs_helper import patch_missing_fs_config
    patch_missing_fs_config(
        fs_config_file,
        folder_path,
        part_name,
        mount_point=mount_point,
        skip_lost_found=True,
    )


def repack_erofs(folder_path, part_name=None):
    if not is_available("mkfs.erofs.exe"):
        return False, "Native EROFS engine (rak_native) not available"

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

    from core.aosp_fs_helper import parse_erofs_mount_point, prepare_tree_and_configs_for_target
    mount_point = parse_erofs_mount_point(fs_options_file, part_name)

    # Pastikan tree & config memenuhi spesifikasi AOSP EROFS (tanpa empty lost+found, fallback file_contexts, dll.)
    from core.threader_control import report_progress
    report_progress(2, "Updating fs_config")
    prepare_tree_and_configs_for_target(
        folder_path,
        part_name,
        cfg_dir,
        "EROFS",
        mount_point=mount_point,
    )
    if not os.path.isfile(file_contexts):
        return False, f"file_context tidak ditemukan: {file_contexts}"

    # Output img baru
    out_img = os.path.join(parent, f"{name}_new.img")

    from core.repack_policy import get_erofs_args

    z_arg, t_arg, erofs_mode = get_erofs_args()
    from core.threader_control import threader_control
    workers = threader_control.recommend_tool_threads(folder_path, max_cap=64, hdd_cap=4)

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
            mount_point=mount_point,
            fs_config=fs_config_file,
            file_contexts=file_contexts,
            uuid=uuid_val,
            timestamp=ts,
            threads=workers,
        )
        return True, f"OK → {out_img}"
    except Exception as e:
        return False, f"erofs_pack error: {e}"
