# SPDX-License-Identifier: MPL-2.0
import os
print("[CORE STAGE] MAKE FILESYSTEM")


def make_filesystem(folder_path, fs_type, mount_point=None):

    folder_path = os.path.normpath(folder_path)
    if not os.path.isdir(folder_path):
        return False, f"Folder tidak ditemukan: {folder_path}"

    project_base = os.path.dirname(folder_path)
    part_name = os.path.basename(folder_path.rstrip("/\\"))
    config_dir = os.path.join(project_base, "config")

    fc_path  = os.path.join(config_dir, f"{part_name}_file_contexts")
    fs_path  = os.path.join(config_dir, f"{part_name}_fs_config")
    opt_path = os.path.join(config_dir, f"{part_name}_fs_options")
    info_path = os.path.join(config_dir, f"{part_name}_info")

    if os.path.isfile(fs_path) and (os.path.isfile(opt_path) or os.path.isfile(info_path)):
        return False, f"Filesystem untuk '{part_name}' sudah ada confignya."

    os.makedirs(config_dir, exist_ok=True)


    from main_ext.class_FilesystemBuilder import FilesystemBuilder
    builder = FilesystemBuilder(project_base, folder_path, forced_fs_type=fs_type)
    try:
        builder._create_fs_config(None)
        if fs_type == "EROFS":
            builder._create_fs_options()
        else:
            builder._create_fs_info()
    except Exception as e:
        return False, f"Gagal generate fs_config/identity: {e}"

    # mount-point custom (default builder pakai /<part_name> utk EROFS,
    # /mnt/<part_name> utk EXT4) — override kalau user isi sendiri.
    if mount_point:
        mount_point = mount_point if mount_point.startswith("/") else f"/{mount_point}"
        try:
            if fs_type == "EROFS":
                _patch_mount_point_erofs(opt_path, mount_point, part_name, folder_path)
            else:
                _patch_mount_point_ext(info_path, mount_point)
        except Exception as e:
            return False, f"Gagal set mount-point: {e}"


    try:
        from core.context_patcher import ContextPatcher
        patcher = ContextPatcher(project_base)
        scan_results = patcher.scan_tree(part_name, mode="ai", debug=False)
        patcher.apply_results(scan_results)
    except Exception as e:
        return False, f"Gagal generate file_contexts: {e}"

    if not os.path.isfile(fc_path):
        # folder kosong / tidak ada hole terdeteksi -> minimal root entry
        try:
            with open(fc_path, "w", encoding="utf-8", newline="\n") as f:
                f.write(f"/ u:object_r:system_file:s0\n")
        except Exception as e:
            return False, f"Gagal menulis file_contexts kosong: {e}"


    if fs_type == "EROFS":
        from core.erofs import repack_erofs
        ok, msg = repack_erofs(folder_path, part_name)
    else:
        from core import repack_ext4
        ok, msg = repack_ext4.main(folder_path)

    if not ok:
        return False, f"Build image gagal: {msg}"


    new_img = os.path.join(project_base, f"{part_name}_new.img")
    final_img = os.path.join(project_base, f"{part_name}.img")
    if os.path.isfile(new_img):
        try:
            if os.path.isfile(final_img):
                os.remove(final_img)
            os.replace(new_img, final_img)
            msg = final_img
        except Exception as e:
            return True, f"Build OK tapi rename gagal ({e}): {new_img}"

    return True, msg


def _patch_mount_point_erofs(opt_path, mount_point, part_name, folder_path):
    if not os.path.isfile(opt_path):
        return
    with open(opt_path, "r", encoding="utf-8", newline="\n") as f:
        content = f.read()
    content = content.replace(f"--mount-point=/{part_name}", f"--mount-point={mount_point}")
    with open(opt_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(content)


def _patch_mount_point_ext(info_path, mount_point):
    if not os.path.isfile(info_path):
        return
    lines = []
    with open(info_path, "r", encoding="utf-8", newline="\n") as f:
        for ln in f:
            if ln.startswith("MountPoint:"):
                lines.append(f"MountPoint: {mount_point}\n")
            else:
                lines.append(ln)
    with open(info_path, "w", encoding="utf-8", newline="\n") as f:
        f.writelines(lines)
