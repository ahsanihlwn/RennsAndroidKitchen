# SPDX-License-Identifier: MPL-2.0
import os
import sys
from core.command_context import OperationError
print("[CORE STAGE] EXT4 REPACK")

# HELPER: ALIGN & SIZE SCAN

def align_up(value: int, align: int) -> int:
    if align <= 0:
        return value
    rem = value % align
    if rem == 0:
        return value
    return value + (align - rem)


def get_tree_usage_bytes(root_dir: str, dir_block_size: int = 4096,
                         inode_overhead_bytes: int = 256) -> int:
    from core.aosp_fs_helper import estimate_ext4_usage_bytes
    return estimate_ext4_usage_bytes(root_dir, block_size=dir_block_size)


def choose_ext4_size(
    partition_dir: str,
    old_size: int | None,
    block_size: int = 4096,
    spare_ratio: float = 0.05,
    align_mb: int = 4,
    min_size_mb: int = 16,
    min_free_mb: int = 16,
    max_free_mb: int = 512,
    preserve_old_size: bool = False,
    precomputed_total: int | None = None,
) -> int:
    from core.aosp_fs_helper import estimate_ext4_image_size
    return estimate_ext4_image_size(
        partition_dir,
        old_size=old_size,
        block_size=block_size,
        spare_ratio=max(spare_ratio, 0.08),
        align_mb=align_mb,
        min_size_mb=min_size_mb,
        min_free_mb=min_free_mb,
        max_free_mb=max_free_mb,
        preserve_old_size=preserve_old_size,
        precomputed_total=precomputed_total,
    )

# HELPER: PARSE _info

def parse_info(info_path: str) -> dict:
    data: dict[str, str] = {}
    if not os.path.exists(info_path):
        raise FileNotFoundError(f"_info file not found: {info_path}")

    with open(info_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or ":" not in line:
                continue
            key, value = line.split(":", 1)
            data[key.strip()] = value.strip()

    return data


def auto_detect_partition_name(config_dir: str) -> str:

    candidates = [fn for fn in os.listdir(config_dir) if fn.endswith("_info")]
    if not candidates:
        raise OperationError("Gak nemu *_info di folder config, kasih partition_name di argumen dong.")
    if len(candidates) > 1:
        raise OperationError("Ada banyak *_info di config, tolong kasih partition_name di argumen.")

    name = os.path.splitext(candidates[0])[0]
    # "product_info" -> "product"
    if name.endswith("_info"):
        name = name[:-5]
    return name


# MAIN

def main(partition_dir):
    try:
        import traceback
        from core.native_backend import ext_make_fs

        partition_dir = os.path.abspath(os.path.normpath(partition_dir))

        base = os.path.dirname(partition_dir)
        partition_name = os.path.basename(partition_dir)
        config_dir = os.path.join(base, "config")

        # Paths penting
        info_path = os.path.join(config_dir, f"{partition_name}_info")
        fs_config_path = os.path.join(config_dir, f"{partition_name}_fs_config")
        file_contexts_path = os.path.join(config_dir, f"{partition_name}_file_contexts")

        out_img = os.path.join(base, f"{partition_name}_new.img")

        # Validasi
        if not os.path.exists(info_path):
            return False, f"_info hilang: {info_path}"
        if not os.path.exists(fs_config_path):
            return False, f"fs_config hilang: {fs_config_path}"
        if not os.path.isdir(partition_dir):
            return False, f"Folder partisi tidak valid: {partition_dir}"

        # Baca _info
        info = parse_info(info_path)

        try:
            old_size = int(info.get("Size", "0"))
        except Exception:
            old_size = None

        try:
            block_size = int(info.get("BlockSize", "4096") or "4096")
        except Exception:
            block_size = 4096

        from core.aosp_fs_helper import normalize_ext4_mount_point, prepare_tree_and_configs_for_target
        mount_point = info.get("MountPoint", f"/{partition_name}")
        mount_point_clean = normalize_ext4_mount_point(mount_point, partition_name)
        prepare_tree_and_configs_for_target(
            partition_dir,
            partition_name,
            config_dir,
            info.get("Format", "EXT4"),
            mount_point=mount_point,
        )
        if not os.path.exists(file_contexts_path):
            return False, f"file_contexts hilang: {file_contexts_path}"
        volume_label = info.get("VolumeName", "").strip() or None

        # Hitung size
        from core.threader_control import report_progress
        report_progress(2, "Scanning EXT4 partition size")
        used_bytes = get_tree_usage_bytes(partition_dir, dir_block_size=block_size)

        final_size = choose_ext4_size(
            partition_dir=partition_dir,
            old_size=old_size,
            block_size=block_size,
            spare_ratio=0.05,
            align_mb=4,
            min_size_mb=16,
            min_free_mb=16,
            max_free_mb=512,
            preserve_old_size=False,
            precomputed_total=used_bytes,
        )

        print(
            "[EXT4][SIZE]",
            f"old={old_size}",
            f"used={used_bytes}",
            f"final={final_size}",
        )

        ext_make_fs(
            out_img,
            partition_dir,
            size_bytes=final_size,
            mount_point=mount_point_clean,
            file_contexts=file_contexts_path,
            fs_config=fs_config_path,
            block_size=block_size,
            label=volume_label,
        )

        return True, out_img

    except Exception as e:
        return False, f"{e}\n{traceback.format_exc()}"


if __name__ == "__main__":
    if len(sys.argv) > 1:
        ok, msg = main(sys.argv[1])
        print(f"[{'OK' if ok else 'FAIL'}] {msg}")
        sys.exit(0 if ok else 1)
    else:
        print("Usage: python -m core.repack_ext4 <partition_dir>")
        sys.exit(1)
