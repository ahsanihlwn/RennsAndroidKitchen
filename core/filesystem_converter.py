# SPDX-License-Identifier: MPL-2.0
import math
import os
import re
import uuid


EXT_FORMATS = {"EXT2", "EXT3", "EXT4"}


def _read_kv_file(path):
    data = {}
    if not os.path.isfile(path):
        return data
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            if ":" not in line:
                continue
            key, value = line.split(":", 1)
            data[key.strip()] = value.strip()
    return data


def _find_option_value(text, name):
    pattern = rf"{re.escape(name)}=(?:\"([^\"]*)\"|'([^']*)'|(\S+))"
    match = re.search(pattern, text)
    if not match:
        return ""
    for group in match.groups():
        if group is not None:
            return group
    return ""


def _read_fs_options(path):
    data = _read_kv_file(path)
    options = data.get("mkfs.erofs options", "")

    if not data.get("Filesystem UUID"):
        match = re.search(r"(?:^|\s)-U\s+([0-9a-fA-F-]{36})", options)
        if match:
            data["Filesystem UUID"] = match.group(1)

    data["MountPoint"] = _find_option_value(options, "--mount-point")
    return data


def _tree_size_bytes(path):
    total = 0
    for root, dirs, files in os.walk(path):
        total += 4096
        for name in files:
            fp = os.path.join(root, name)
            try:
                total += os.path.getsize(fp)
            except OSError:
                pass
    return total


def _align_up(value, align):
    if align <= 0:
        return value
    rem = value % align
    return value if rem == 0 else value + align - rem


def _remove_file(path):
    if os.path.exists(path):
        os.remove(path)


def _write_text(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def _build_ext_info(partition_dir, part_name, fs_options):
    block_size = 4096
    used = _tree_size_bytes(partition_dir)
    size = _align_up(max(used, 16 * 1024 * 1024), 4 * 1024 * 1024)
    block_count = max(1, math.ceil(size / block_size))
    used_blocks = math.ceil(used / block_size)
    free_blocks = max(0, block_count - used_blocks)

    fs_uuid = fs_options.get("Filesystem UUID") or str(uuid.uuid4())
    mount_point = fs_options.get("MountPoint") or f"/{part_name}"

    return (
        f"PartitionName: {part_name}\n"
        "Format: EXT4\n"
        f"Size: {size}\n"
        f"BlockSize: {block_size}\n"
        f"BlockCount: {block_count}\n"
        f"FreeBlocks: {free_blocks}\n"
        f"MountPoint: {mount_point}\n"
        "VolumeName: \n"
        f"UUID: {fs_uuid}\n"
    )


def _build_erofs_options(partition_dir, part_name, info, config_dir):
    fs_uuid = info.get("UUID") or str(uuid.uuid4())
    mount_point = info.get("MountPoint") or f"/{part_name}"
    if not mount_point.startswith("/"):
        mount_point = "/" + mount_point

    fs_config = os.path.join(config_dir, f"{part_name}_fs_config")
    file_contexts = os.path.join(config_dir, f"{part_name}_file_contexts")
    out_img = f"{part_name}_repack.img"

    try:
        from core.repack_policy import get_erofs_args
        z_arg, t_arg, _ = get_erofs_args()
    except Exception:
        z_arg, t_arg = "-zlz4hc", "-T 0"

    return (
        "Filesystem created:        Thu Jan  1 07:00:00 1970\n"
        f"Filesystem UUID:           {fs_uuid}\n"
        "mkfs.erofs options:        "
        f"{z_arg} {t_arg} -U {fs_uuid} "
        f"--mount-point={mount_point} "
        f"--fs-config-file={fs_config} "
        f"--file-contexts={file_contexts} "
        f"{out_img} {partition_dir}\n"
    )


def convert_filesystem_metadata(partition_dir, target_fs=None):
    partition_dir = os.path.abspath(os.path.normpath(partition_dir))
    if not os.path.isdir(partition_dir):
        return False, f"Partition folder not found: {partition_dir}"

    base_dir = os.path.dirname(partition_dir)
    part_name = os.path.basename(partition_dir.rstrip("/\\"))
    config_dir = os.path.join(base_dir, "config")

    info_path = os.path.join(config_dir, f"{part_name}_info")
    options_path = os.path.join(config_dir, f"{part_name}_fs_options")
    fs_config_path = os.path.join(config_dir, f"{part_name}_fs_config")
    file_contexts_path = os.path.join(config_dir, f"{part_name}_file_contexts")

    if not os.path.isdir(config_dir):
        return False, f"Config folder not found: {config_dir}"
    if not os.path.isfile(fs_config_path):
        return False, f"fs_config not found: {fs_config_path}"
    if not os.path.isfile(file_contexts_path):
        return False, f"file_contexts not found: {file_contexts_path}"

    if target_fs is None:
        if os.path.isfile(options_path):
            target_fs = "EXT4"
        elif os.path.isfile(info_path):
            target_fs = "EROFS"
        else:
            return False, f"No filesystem metadata found for {part_name}"

    target_fs = target_fs.upper()

    if target_fs in EXT_FORMATS:
        if not os.path.isfile(options_path):
            return False, f"EROFS options not found: {options_path}"
        fs_options = _read_fs_options(options_path)
        _write_text(info_path, _build_ext_info(partition_dir, part_name, fs_options))
        _remove_file(options_path)
        return True, "Converted metadata to EXT4"

    if target_fs == "EROFS":
        if not os.path.isfile(info_path):
            return False, f"EXT info not found: {info_path}"
        info = _read_kv_file(info_path)
        _write_text(options_path, _build_erofs_options(partition_dir, part_name, info, config_dir))
        _remove_file(info_path)
        return True, "Converted metadata to EROFS"

    return False, f"Unsupported target filesystem: {target_fs}"


def main(partition_dir, target_fs=None):
    return convert_filesystem_metadata(partition_dir, target_fs)
