# SPDX-License-Identifier: MPL-2.0
import os
import sys
import subprocess
from core.app_paths import BIN_DIR
print("[CORE STAGE] EXT4 REPACK")


from core.binary_handler import resolve_binary
from core.proc_util import CREATE_NO_WINDOW

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

    total_bytes = 0
    dir_count = 0
    inode_count = 0

    for dirpath, dirnames, filenames in os.walk(root_dir):
        dir_count += 1
        inode_count += 1  # folder = 1 inode

        # Anggap tiap folder minimal makan 1 block
        total_bytes += dir_block_size

        for name in filenames:
            inode_count += 1
            fp = os.path.join(dirpath, name)

            if os.path.islink(fp):
                # symlink: anggap 1 block juga
                total_bytes += dir_block_size
            else:
                try:
                    total_bytes += os.path.getsize(fp)
                except OSError:
                    # broken file, permission error, dll -> skip
                    pass

    # overhead inode table, kira-kira aja (256 bytes per inode)
    inode_overhead = inode_count * inode_overhead_bytes
    return total_bytes + inode_overhead

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


    if precomputed_total is not None:
        total = precomputed_total
    else:
        # Hitung REAL "size on disk"
        total = 0
        for root, dirs, files in os.walk(partition_dir):
            # folder minimal 1 block
            total += block_size

            for f in files:
                fp = os.path.join(root, f)
                try:
                    raw = os.path.getsize(fp)
                except:
                    raw = 0

                # hitung "size on disk" = dibagi jml block
                blocks = (raw + block_size - 1) // block_size
                total += blocks * block_size

    min_size = min_size_mb * 1024 * 1024
    min_free = min_free_mb * 1024 * 1024
    max_free = max_free_mb * 1024 * 1024


    free = int(total * spare_ratio)
    free = max(min_free, free)
    free = min(max_free, free)
    need = max(total + free, min_size)

    # Align to block
    need = align_up(need, block_size)

    # Align ke N MB
    if align_mb > 0:
        need = align_up(need, align_mb * 1024 * 1024)


    if preserve_old_size and old_size and old_size > 0 and need <= old_size:
        return old_size

    return need

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
        raise RuntimeError("Gak nemu *_info di folder config, kasih partition_name di argumen dong.")
    if len(candidates) > 1:
        raise RuntimeError("Ada banyak *_info di config, tolong kasih partition_name di argumen.")

    name = os.path.splitext(candidates[0])[0]
    # "product_info" -> "product"
    if name.endswith("_info"):
        name = name[:-5]
    return name


# MAIN

def main(partition_dir):
    try:
        import traceback

        partition_dir = os.path.abspath(os.path.normpath(partition_dir))

        # 🔥 RESOLVE SAAT DIPAKAI
        MAKE_EXT4FS_EXE = resolve_binary(BIN_DIR, "make_ext4fs.exe")

        if not MAKE_EXT4FS_EXE or not os.path.exists(MAKE_EXT4FS_EXE):
            return False, "EXT Environtment not found"

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
        if not os.path.exists(file_contexts_path):
            return False, f"file_contexts hilang: {file_contexts_path}"
        if not os.path.isdir(partition_dir):
            return False, f"Folder partisi tidak valid: {partition_dir}"
        if not os.path.exists(MAKE_EXT4FS_EXE):
            return False, f"make_ext4fs.exe tidak ditemukan:\n{MAKE_EXT4FS_EXE}"

        # Baca _info
        info = parse_info(info_path)

        try:
            old_size = int(info.get("Size", "0"))
        except:
            old_size = None

        try:
            block_size = int(info.get("BlockSize", "4096") or "4096")
        except:
            block_size = 4096

        mount_point = info.get("MountPoint", f"/{partition_name}")
        mount_point_clean = mount_point.lstrip("/") or partition_name   # <-- FIX: gak boleh kosong

        # Hitung size
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


        # Build command
        cmd = [
            MAKE_EXT4FS_EXE,
            "-l", str(final_size),
            "-a", mount_point_clean,
            "-S", file_contexts_path,
            "-C", fs_config_path,
            out_img,
            partition_dir,
        ]

        # Run tool (capture stdout/stderr seperti EROFS)
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"


        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            shell=False,
            env=env,
            creationflags=CREATE_NO_WINDOW,
        )

        if proc.returncode != 0:
            err_out = (proc.stdout or "") + "\n" + (proc.stderr or "")
            return False, f"make_ext4fs ERROR (code {proc.returncode}):\n{err_out.strip()}"

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
