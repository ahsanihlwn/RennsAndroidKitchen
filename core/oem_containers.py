# SPDX-License-Identifier: MPL-2.0
"""Clean-room OEM container, embedded filesystem, and F2FS operations."""
from __future__ import annotations

import os
import time
import uuid

from core import kitchen_process as subprocess
from core.app_paths import BIN_DIR
from core.binary_handler import resolve_binary
from core.command_context import OperationError
from core.native_backend import oem_detect_magic, oem_extract, oem_inspect
from core.proc_util import CREATE_NO_WINDOW

OEM_CONTAINER_KINDS = frozenset({
    "OZIP",
    "OFP",
    "OFP_QC",
    "OFP_MTK",
    "OFP_ZIP",
    "OPS",
    "KDZ",
    "DZ",
    "UPDATE_APP",
    "PAC",
    "CPB",
    "AMLOGIC",
    "ALLWINNER",
    "ROMFS",
    "SQUASHFS",
})


def detect_oem_kind(path: str) -> str:
    if not path or not os.path.isfile(path):
        return ""
    return oem_detect_magic(path)


def inspect_container(path: str) -> dict:
    if not os.path.isfile(path):
        raise FileNotFoundError(path)
    try:
        return oem_inspect(path)
    except OSError as error:
        raise OperationError(str(error)) from error


def verify_pac_crc(path: str) -> dict:
    if not os.path.isfile(path):
        raise FileNotFoundError(path)
    from core.native_backend import pac_verify_crc

    try:
        return pac_verify_crc(path)
    except OSError as error:
        raise OperationError(str(error)) from error


def extract_container(path: str, out_dir: str | None = None, selected: list[str] | None = None) -> dict:
    if not os.path.isfile(path):
        raise FileNotFoundError(path)
    if out_dir is None:
        stem = os.path.splitext(os.path.basename(path))[0] or "oem_extracted"
        out_dir = os.path.join(os.path.dirname(path), stem)
    os.makedirs(out_dir, exist_ok=True)
    try:
        result = oem_extract(path, out_dir, selected=selected)
    except OSError as error:
        raise OperationError(str(error)) from error

    # For KDZ containers, also offer clean info on extracted .dz segments; do not
    # delete the source file automatically so user assets remain recoverable.
    result["out_dir"] = out_dir
    return result


def unpack_f2fs(img_path: str, out_dir: str | None = None) -> str:
    if not os.path.isfile(img_path):
        raise FileNotFoundError(img_path)
    tool = resolve_binary(BIN_DIR, "extract.f2fs.exe") or resolve_binary(BIN_DIR, "imgkit.exe")
    if not tool:
        raise OperationError(
            "F2FS extraction tool (extract.f2fs.exe / imgkit.exe) is not installed in bin/."
        )
    if out_dir is None:
        out_dir = os.path.dirname(img_path)
    os.makedirs(out_dir, exist_ok=True)
    name = os.path.splitext(os.path.basename(img_path))[0]
    if os.path.basename(tool).lower().startswith("imgkit"):
        cmd = [tool, "unpack", "-i", img_path, "-o", out_dir]
    else:
        target = os.path.join(out_dir, name)
        os.makedirs(target, exist_ok=True)
        cmd = [tool, img_path, "-o", target]
    proc = subprocess.run(cmd, capture_output=True, text=True, creationflags=CREATE_NO_WINDOW)
    if proc.returncode != 0:
        raise OperationError((proc.stderr or proc.stdout or f"F2FS unpack failed ({proc.returncode})").strip())
    return os.path.join(out_dir, name)


def _dir_size_bytes(folder: str) -> int:
    total = 0
    for root, _dirs, files in os.walk(folder):
        for fname in files:
            fpath = os.path.join(root, fname)
            try:
                if not os.path.islink(fpath):
                    total += os.path.getsize(fpath)
            except OSError:
                pass
    return total


def repack_f2fs(
    folder: str,
    out_img: str | None = None,
    *,
    fs_config: str | None = None,
    file_contexts: str | None = None,
    readonly: bool = False,
    compress: bool = False,
    utc: int | None = None,
) -> str:
    if not os.path.isdir(folder):
        raise FileNotFoundError(folder)
    mkfs = resolve_binary(BIN_DIR, "mkfs.f2fs.exe")
    sload = resolve_binary(BIN_DIR, "sload.f2fs.exe")
    if not mkfs or not sload:
        raise OperationError("F2FS repack requires mkfs.f2fs.exe and sload.f2fs.exe in bin/.")

    base = os.path.dirname(os.path.abspath(folder))
    part = os.path.basename(os.path.abspath(folder))
    if out_img is None:
        out_img = os.path.join(base, f"{part}.img")
    if fs_config is None:
        cand = os.path.join(base, "config", f"{part}_fs_config")
        if os.path.isfile(cand):
            fs_config = cand
    if file_contexts is None:
        cand = os.path.join(base, "config", f"{part}_file_contexts")
        if os.path.isfile(cand):
            file_contexts = cand

    raw_size = _dir_size_bytes(folder)
    # Reserve 64 MiB F2FS metadata + 15% headroom, aligned to 4 KiB blocks.
    alloc = int(((64 * 1024 * 1024) + raw_size) * 1.15)
    alloc = ((alloc + 4095) // 4096) * 4096

    tmp_img = f"{out_img}.tmp-{uuid.uuid4().hex[:8]}"
    stamp = str(int(utc if utc is not None else time.time()))
    try:
        with open(tmp_img, "wb") as stream:
            stream.truncate(alloc)
        features = (
            "extra_attr,compression,ro"
            if readonly
            else "extra_attr,inode_checksum,sb_checksum,compression"
        )
        mk_cmd = [
            mkfs, "-d", "0", "-l", part, "-O", features,
            "-U", str(uuid.uuid4()), "-T", stamp, tmp_img, "-f",
        ]
        p1 = subprocess.run(mk_cmd, capture_output=True, text=True, creationflags=CREATE_NO_WINDOW)
        if p1.returncode != 0:
            raise OperationError((p1.stderr or p1.stdout or "mkfs.f2fs failed").strip())

        sl_cmd = [sload, "-d", "0"]
        if compress:
            sl_cmd.append("-c")
        if readonly:
            sl_cmd.append("-r")
        if fs_config:
            sl_cmd.extend(["-C", fs_config])
        sl_cmd.extend(["-f", folder, "-p", tmp_img])
        if file_contexts:
            sl_cmd.extend(["-s", file_contexts])
        sl_cmd.extend(["-t", f"/{part}", "-T", stamp, tmp_img])
        p2 = subprocess.run(sl_cmd, capture_output=True, text=True, creationflags=CREATE_NO_WINDOW)
        if p2.returncode != 0:
            raise OperationError((p2.stderr or p2.stdout or "sload.f2fs failed").strip())

        os.replace(tmp_img, out_img)
        return out_img
    finally:
        if os.path.exists(tmp_img):
            try:
                os.remove(tmp_img)
            except OSError:
                pass
