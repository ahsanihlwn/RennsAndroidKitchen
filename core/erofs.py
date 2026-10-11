# SPDX-License-Identifier: MPL-2.0
import os
import subprocess
import shlex
import re
import subprocess
import os
import re
import subprocess
from main_ext.class_ConfigEngine import ConfigEngine
from core.app_paths import BIN_DIR
print("[CORE STAGE] EROFS STARTING")


BIN_PATH = BIN_DIR

from core.binary_handler import resolve_binary
from core.proc_util import CREATE_NO_WINDOW, kill_process_tree as _kill_process_tree


UUID_RE = re.compile(
    r"Filesystem UUID:\s*([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})"
)

def get_uuid_from_fs_options(fs_options_path):
    print(f"[UUID] open fs_options: {fs_options_path}")

    if not os.path.isfile(fs_options_path):
        print("[UUID][ERR] fs_options FILE NOT FOUND")
        return None, f"fs_options tidak ditemukan: {fs_options_path}"

    try:
        with open(fs_options_path, "r", encoding="utf-8", errors="ignore") as f:
            for idx, line in enumerate(f, start=1):
                print(f"[UUID][LINE {idx}] {line.strip()}")

                if "Filesystem UUID:" in line:
                    print("[UUID] keyword FOUND")
                    m = UUID_RE.search(line)
                    if m:
                        print(f"[UUID] PARSED UUID = {m.group(1)}")
                        return m.group(1), None

                    print("[UUID][ERR] UUID FORMAT INVALID")
                    return None, "UUID ditemukan tapi formatnya tidak valid"

    except Exception as e:
        print(f"[UUID][EXCEPTION] {e}")
        return None, f"Gagal membaca fs_options: {e}"

    print("[UUID][ERR] UUID NOT FOUND IN FILE")
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
    # Keep every untrusted path as one argv item so metacharacters in a file
    # name are always data, never executable syntax.
    cmd = [
        UNPACK_EROFS, "-i", img_path, "-x", "-f", "-s",
        f"-T{threads}", "-o", parent,
    ]

    try:
        p = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            shell=False,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=CREATE_NO_WINDOW
        )
        out, _ = p.communicate()

        if p.returncode != 0:
            return False, (out or "").strip() or "extract.erofs failed"

        # After extraction, determine actual result folder
        # Common behaviour: extract.erofs will create parent/<name>/...
        if os.path.isdir(result_dir):
            # good: extractor created parent/<name> — that's our final folder
            return True, out.strip()


        print(f"[UNPACK] parent dir = {parent}")
        print(f"[UNPACK] result_dir = {result_dir}")

        try:
            if not os.path.exists(result_dir):
                print("[UNPACK] create result_dir")
                os.makedirs(result_dir, exist_ok=True)

            for item in os.listdir(parent):
                print(f"[UNPACK][SCAN] found item: {item}")

                src = os.path.join(parent, item)
                print(f"[UNPACK][SRC] {src}")

                if os.path.abspath(src) == os.path.abspath(img_path):
                    print("[UNPACK][SKIP] image file")
                    continue

                if os.path.abspath(src) == os.path.abspath(result_dir):
                    print("[UNPACK][SKIP] result_dir itself")
                    continue

                dst = os.path.join(result_dir, item)
                print(f"[UNPACK][MOVE] {src} -> {dst}")

                if os.path.exists(dst):
                    print("[UNPACK][OVERWRITE] removing existing dst")
                    import shutil
                    if os.path.isdir(dst):
                        shutil.rmtree(dst)
                    else:
                        os.remove(dst)

                os.rename(src, dst)
                print("[UNPACK][OK] moved")

        except Exception as move_err:
            print(f"[UNPACK][ERR] relocate failed: {move_err}")
            warn_msg = f"WARN relocate: {move_err}"
            return True, warn_msg


        return True, out.strip()

    except Exception as e:
        return False, str(e)


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
        # entry untuk direktori ini sendiri
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
        # newline="\n": mkfs.erofs parse strict, CRLF/empty-line bikin
        # "Ill-formed line". Jangan tulis baris kosong sebagai separator.
        with open(fs_config_file, "a", encoding="utf-8", newline="\n") as f:
            for ln in new_lines:
                f.write(ln + "\n")
    except Exception as e:
        print(f"[PATCH_FSCFG] write error: {e}")


def repack_erofs(folder_path, part_name=None):
    MKFS_BIN = resolve_binary(BIN_PATH, "mkfs.erofs.exe")
    if not MKFS_BIN:
        return False, "EROFS ENVIRONTMENT NOT FOUND"

    # PATCH: ambil nama partisi otomatis
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

    # pastikan file2 config lain ada
    if not os.path.isfile(fs_config_file):
        return False, f"fs_config tidak ditemukan: {fs_config_file}"
    if not os.path.isfile(file_contexts):
        return False, f"file_context tidak ditemukan: {file_contexts}"

    # AUTO-PATCH fs_config: tambahkan entry yang missing
    _patch_fs_config(fs_config_file, folder_path, part_name)

    # output img baru
    out_img = os.path.join(parent, f"{name}_new.img")

    from core.repack_policy import get_erofs_args

    z_arg, t_arg, erofs_mode = get_erofs_args()
    from core.storage_detector import get_optimal_io_workers
    workers = get_optimal_io_workers(folder_path, max_cap=64, hdd_cap=4)

    # t_arg currently is "-T <timestamp>".  It is configuration produced by
    # RAK, not a shell fragment; split it only into the two argv parameters.
    t_parts = shlex.split(t_arg, posix=False)
    cmd = [
        MKFS_BIN, z_arg, *t_parts,
        f"--workers={workers}",
        "--async-queue-limit=2048",
        "-U", uuid_val,
        f"--mount-point=/{part_name}",
        f"--fs-config-file={fs_config_file}",
        f"--file-contexts={file_contexts}",
        out_img, folder_path,
    ]


    print("========== EROFS REPACK DEBUG ==========")
    print(f"Mode      : {erofs_mode}")
    print("Command   :")
    print(subprocess.list2cmdline(cmd))
    print("========================================")


    p = None
    try:
        print("[REPACK] running mkfs.erofs...")

        p = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            shell=False,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=CREATE_NO_WINDOW
        )


        out, _ = p.communicate()

        print("[REPACK] process finished")
        print(f"[REPACK] returncode = {p.returncode}")
        print("[REPACK] output:")
        print(out)

        if p.returncode != 0:
            return False, f"mkfs.erofs error: {out.strip()}"

        return True, f"OK → {out_img}"

    except Exception as e:
        if p is not None:
            try:
                _kill_process_tree(p.pid)
            except Exception:
                pass
        return False, str(e)
