# SPDX-License-Identifier: MPL-2.0
from __future__ import annotations

import hashlib
import json
import mmap
import os
import re
import shutil
import struct
import subprocess
from pathlib import Path
from typing import Any

from core import cpio_patch
from core import magiskboot_tree as mbt

HDR_FORMAT = "<8sIIIII2048sI16sIIQIIII"
HDR_SIZE = 2128
TABLE_ENTRY_FORMAT = "<III32s64s"
TABLE_ENTRY_SIZE = 108
V4_META_NAME = "v4_boot_meta.json"
LZ4_LEGACY_MAGIC = 0x184C2102


def compress_lz4_legacy(data: bytes, block_size: int = 8 * 1024 * 1024) -> bytes:

    try:
        import lz4.block
        out = bytearray(struct.pack("<I", LZ4_LEGACY_MAGIC))
        pos = 0
        while pos < len(data):
            chunk = data[pos : pos + block_size]
            pos += block_size
            c_chunk = lz4.block.compress(
                chunk, store_size=False, mode="high_compression", compression=9
            )
            out.extend(struct.pack("<I", len(c_chunk)))
            out.extend(c_chunk)
        return bytes(out)
    except Exception as exc:
        print(f"info: native lz4 compress fallback: {exc}")
        return b""


def decompress_lz4_legacy(data: bytes, block_size: int = 8 * 1024 * 1024) -> bytes:

    try:
        import lz4.block
        if len(data) < 4 or struct.unpack_from("<I", data, 0)[0] != LZ4_LEGACY_MAGIC:
            return b""
        pos = 4
        decompressed = bytearray()
        while pos + 4 <= len(data):
            c_size = struct.unpack_from("<I", data, pos)[0]
            pos += 4
            if pos + c_size > len(data):
                break
            c_chunk = data[pos : pos + c_size]
            pos += c_size
            d_chunk = lz4.block.decompress(c_chunk, uncompressed_size=block_size)
            decompressed.extend(d_chunk)
        return bytes(decompressed)
    except Exception as exc:
        print(f"info: native lz4 decompress fallback: {exc}")
        return b""


def is_vendor_boot_v4(data_or_path: bytes | str | Path) -> tuple[bool, dict[str, Any]]:

    if isinstance(data_or_path, (str, Path)):
        p = Path(data_or_path)
        if not p.is_file():
            return False, {}
        with open(p, "rb") as f:
            head = f.read(4096)
    else:
        head = data_or_path[:4096]

    if len(head) < HDR_SIZE:
        return False, {}

    magic = head[:8]
    if magic != b"VNDRBOOT":
        return False, {}

    ver = struct.unpack_from("<I", head, 8)[0]
    if ver < 4:
        return False, {}

    hdr = struct.unpack_from(HDR_FORMAT, head, 0)
    info = {
        "magic": magic.decode("latin1"),
        "header_version": hdr[1],
        "page_size": hdr[2],
        "kernel_addr": hdr[3],
        "ramdisk_addr": hdr[4],
        "vendor_ramdisk_size": hdr[5],
        "cmdline": hdr[6].rstrip(b"\x00").decode("latin1", errors="replace"),
        "tags_addr": hdr[7],
        "name": hdr[8].rstrip(b"\x00").decode("latin1", errors="replace"),
        "header_size": hdr[9],
        "dtb_size": hdr[10],
        "dtb_addr": hdr[11],
        "table_size": hdr[12],
        "table_entry_num": hdr[13],
        "table_entry_size": hdr[14],
        "bootconfig_size": hdr[15],
    }

    is_v4 = info["table_size"] > 0 and info["table_entry_num"] > 0
    return is_v4, info


def pad(buf: bytes, page_size: int = 4096) -> bytes:
    rem = len(buf) % page_size
    if rem != 0:
        buf += b"\x00" * (page_size - rem)
    return buf


def parse_table_entries(table_bytes: bytes, entry_num: int) -> list[dict[str, Any]]:
    entries = []
    for i in range(entry_num):
        chunk = table_bytes[i * TABLE_ENTRY_SIZE : (i + 1) * TABLE_ENTRY_SIZE]
        if len(chunk) < TABLE_ENTRY_SIZE:
            break
        r_size, r_off, r_type, r_name, b_id = struct.unpack(TABLE_ENTRY_FORMAT, chunk)
        entries.append({
            "index": i,
            "ramdisk_size": r_size,
            "ramdisk_offset": r_off,
            "ramdisk_type": r_type,
            "ramdisk_name": r_name.rstrip(b"\x00").decode("latin1", errors="replace"),
            "board_id": b_id.hex(),
            "board_id_bytes": b_id.hex(),
        })
    return entries


def unpack_vendor_boot_v4(
    img_path: Path,
    meta_dir: Path,
    visible_ramdisk: Path,
    magiskboot: Path,
) -> bool:

    data = img_path.read_bytes()
    is_v4, info = is_vendor_boot_v4(data)
    if not is_v4:
        return False

    page_size = info["page_size"]
    hdr_pages = (info["header_size"] + page_size - 1) // page_size
    r_pages = (info["vendor_ramdisk_size"] + page_size - 1) // page_size
    dtb_pages = (info["dtb_size"] + page_size - 1) // page_size
    tbl_pages = (info["table_size"] + page_size - 1) // page_size

    r_offset = hdr_pages * page_size
    dtb_offset = r_offset + (r_pages * page_size)
    tbl_offset = dtb_offset + (dtb_pages * page_size)
    bc_offset = tbl_offset + (tbl_pages * page_size)

    # Save DTB
    dtb_data = data[dtb_offset : dtb_offset + info["dtb_size"]]
    (meta_dir / "dtb").write_bytes(dtb_data)

    # Save table
    tbl_data = data[tbl_offset : tbl_offset + info["table_size"]]
    entries = parse_table_entries(tbl_data, info["table_entry_num"])

    # Save bootconfig
    bc_data = data[bc_offset : bc_offset + info["bootconfig_size"]]
    (meta_dir / "bootconfig").write_bytes(bc_data)

    # Parse AVB footer if present
    avb_pos = data.rfind(b"AVBf")
    has_avb = False
    avb_info = {}
    if avb_pos != -1 and len(data) >= avb_pos + 64:
        f_magic, f_vmaj, f_vmin, f_orig_sz, f_vbmeta_off, f_vbmeta_sz = struct.unpack(
            ">4sIIQQQ", data[avb_pos : avb_pos + 36]
        )
        if f_magic == b"AVBf":
            has_avb = True
            avb_info = {
                "version_major": f_vmaj,
                "version_minor": f_vmin,
                "original_image_size": f_orig_sz,
                "vbmeta_offset": f_vbmeta_off,
                "vbmeta_size": f_vbmeta_sz,
                "total_file_size": len(data),
            }
            vbmeta_blob = data[f_vbmeta_off : f_vbmeta_off + f_vbmeta_sz]
            (meta_dir / "vbmeta.bin").write_bytes(vbmeta_blob)

    # Extract each ramdisk entry
    entry_files: dict[int, list[str]] = {}
    ramdisk_stream_start = r_offset

    import tempfile

    visible_ramdisk.mkdir(parents=True, exist_ok=True)

    for entry in entries:
        idx = entry["index"]
        e_off = ramdisk_stream_start + entry["ramdisk_offset"]
        e_sz = entry["ramdisk_size"]
        e_comp = data[e_off : e_off + e_sz]

        # Save compressed entry
        e_comp_path = meta_dir / f"entry_{idx}.comp"
        e_comp_path.write_bytes(e_comp)

        e_cpio_path = meta_dir / f"entry_{idx}.cpio"
        e_pristine_path = meta_dir / f"entry_{idx}.cpio.pristine"

        # Decompress with native C lz4 or fallback to magiskboot
        dec_bytes = decompress_lz4_legacy(e_comp)
        if dec_bytes:
            e_cpio_path.write_bytes(dec_bytes)
        else:
            mbt.run([magiskboot, "decompress", e_comp_path.name, e_cpio_path.name], meta_dir)
        shutil.copyfile(e_cpio_path, e_pristine_path)

        # Extract to index files
        with tempfile.TemporaryDirectory(prefix="v4_extract_") as tmp_ext:
            tpath = Path(tmp_ext)
            shutil.copyfile(e_cpio_path, tpath / "ramdisk.cpio")
            mbt.run([magiskboot, "cpio", "ramdisk.cpio", "extract"], tpath)
            ext_dir = tpath / "ramdisk"
            f_list = []
            if ext_dir.is_dir():
                for p in ext_dir.rglob("*"):
                    if p.is_file() or p.is_symlink():
                        rel = str(p.relative_to(ext_dir)).replace("\\", "/")
                        f_list.append(rel)
                # Copy into visible_ramdisk (later entries overlay earlier ones)
                shutil.copytree(ext_dir, visible_ramdisk, dirs_exist_ok=True)
            entry_files[idx] = f_list

    # Save v4 metadata
    v4_meta = {
        "header": info,
        "entries": entries,
        "entry_files": entry_files,
        "has_avb": has_avb,
        "avb_info": avb_info,
    }
    (meta_dir / V4_META_NAME).write_text(json.dumps(v4_meta, indent=2), encoding="utf-8")
    return True


def prepare_v4_meta_from_source(
    meta_dir: Path,
    magiskboot: Path,
) -> bool:

    src_img = meta_dir / mbt.SOURCE_IMAGE
    if not src_img.is_file():
        return False

    data = src_img.read_bytes()
    is_v4, info = is_vendor_boot_v4(data)
    if not is_v4:
        return False

    page_size = info["page_size"]
    hdr_pages = (info["header_size"] + page_size - 1) // page_size
    r_pages = (info["vendor_ramdisk_size"] + page_size - 1) // page_size
    dtb_pages = (info["dtb_size"] + page_size - 1) // page_size
    tbl_pages = (info["table_size"] + page_size - 1) // page_size

    r_offset = hdr_pages * page_size
    dtb_offset = r_offset + (r_pages * page_size)
    tbl_offset = dtb_offset + (dtb_pages * page_size)
    bc_offset = tbl_offset + (tbl_pages * page_size)

    # Save DTB if not present
    dtb_file = meta_dir / "dtb"
    if not dtb_file.is_file():
        dtb_file.write_bytes(data[dtb_offset : dtb_offset + info["dtb_size"]])

    # Save table
    tbl_data = data[tbl_offset : tbl_offset + info["table_size"]]
    entries = parse_table_entries(tbl_data, info["table_entry_num"])

    # Save bootconfig
    bc_file = meta_dir / "bootconfig"
    if not bc_file.is_file():
        bc_file.write_bytes(data[bc_offset : bc_offset + info["bootconfig_size"]])

    # Parse AVB footer if present
    avb_pos = data.rfind(b"AVBf")
    has_avb = False
    avb_info = {}
    if avb_pos != -1 and len(data) >= avb_pos + 64:
        f_magic, f_vmaj, f_vmin, f_orig_sz, f_vbmeta_off, f_vbmeta_sz = struct.unpack(
            ">4sIIQQQ", data[avb_pos : avb_pos + 36]
        )
        if f_magic == b"AVBf":
            has_avb = True
            avb_info = {
                "version_major": f_vmaj,
                "version_minor": f_vmin,
                "original_image_size": f_orig_sz,
                "vbmeta_offset": f_vbmeta_off,
                "vbmeta_size": f_vbmeta_sz,
                "total_file_size": len(data),
            }
            vb_file = meta_dir / "vbmeta.bin"
            if not vb_file.is_file():
                vb_file.write_bytes(data[f_vbmeta_off : f_vbmeta_off + f_vbmeta_sz])

    entry_files: dict[int, list[str]] = {}
    ramdisk_stream_start = r_offset

    import tempfile

    for entry in entries:
        idx = entry["index"]
        e_off = ramdisk_stream_start + entry["ramdisk_offset"]
        e_sz = entry["ramdisk_size"]
        e_comp = data[e_off : e_off + e_sz]

        e_comp_path = meta_dir / f"entry_{idx}.comp"
        e_comp_path.write_bytes(e_comp)

        e_cpio_path = meta_dir / f"entry_{idx}.cpio"
        e_pristine_path = meta_dir / f"entry_{idx}.cpio.pristine"

        if not e_pristine_path.is_file():
            dec_bytes = decompress_lz4_legacy(e_comp)
            if dec_bytes:
                e_cpio_path.write_bytes(dec_bytes)
            else:
                mbt.run([magiskboot, "decompress", e_comp_path.name, e_cpio_path.name], meta_dir)
            shutil.copyfile(e_cpio_path, e_pristine_path)

        with tempfile.TemporaryDirectory(prefix="v4_index_") as tmp_ext:
            tpath = Path(tmp_ext)
            shutil.copyfile(e_pristine_path, tpath / "ramdisk.cpio")
            mbt.run([magiskboot, "cpio", "ramdisk.cpio", "extract"], tpath)
            ext_dir = tpath / "ramdisk"
            f_list = []
            if ext_dir.is_dir():
                for p in ext_dir.rglob("*"):
                    if p.is_file() or p.is_symlink():
                        rel = str(p.relative_to(ext_dir)).replace("\\", "/")
                        f_list.append(rel)
            entry_files[idx] = f_list

    v4_meta = {
        "header": info,
        "entries": entries,
        "entry_files": entry_files,
        "has_avb": has_avb,
        "avb_info": avb_info,
    }
    (meta_dir / V4_META_NAME).write_text(json.dumps(v4_meta, indent=2), encoding="utf-8")
    return True


def repack_vendor_boot_v4(
    meta_dir: Path,
    visible_ramdisk: Path,
    out_img: str | Path,
    magiskboot: Path,
    manifest: dict,
    desired_modes: dict[str, str] | None = None,
) -> tuple[bool, str]:

    v4_meta_path = meta_dir / V4_META_NAME
    if not v4_meta_path.is_file():
        return False, "v4_boot_meta.json not found"

    v4_meta = json.loads(v4_meta_path.read_text(encoding="utf-8"))
    entries = v4_meta["entries"]
    if not entries:
        return False, "vendor_boot ramdisk table has no entries"
    header_info = v4_meta.get("header_info") or v4_meta.get("header", {})
    entry_files: dict[str, list[str]] = {
        str(k): [str(p) for p in v] for k, v in v4_meta["entry_files"].items()
    }

    # Diff current visible_ramdisk against manifest
    ops, warnings = cpio_patch.diff_against_manifest(
        visible_ramdisk, manifest, desired_modes=desired_modes
    )


    entry_paths = {
        entry["index"]: set(entry_files.get(str(entry["index"]), []))
        for entry in entries
    }
    ops_by_entry: dict[int, list[dict]] = {entry["index"]: [] for entry in entries}
    platform_entries = [e["index"] for e in entries if e["ramdisk_type"] == 1]
    recovery_entries = [e["index"] for e in entries if e["ramdisk_type"] == 2]

    for op in ops:
        p = op["path"]
        basename = p.rsplit("/", 1)[-1]
        is_policy = (basename in ("file_contexts", "sepolicy", "precompiled_sepolicy")
                     or basename.endswith(("_contexts", "_sepolicy.cil")))
        targets = [idx for idx, paths in entry_paths.items() if p in paths]
        if is_policy and len(targets) > 1:
            return False, (
                f"{p} exists in multiple vendor_boot fragments; the merged "
                "ramdisk tree cannot select which policy to edit. Preserve "
                "the originals and edit an explicitly selected fragment."
            )
        if not targets and op["op"] in ("rm", "mkdir"):
            # entry_files lists leaves, not directories. Locate an existing
            # directory by the files it contained in each fragment.
            targets = [
                idx for idx, paths in entry_paths.items()
                if any(path.startswith(p + "/") for path in paths)
            ]

        if not targets:
            if is_policy:
                return False, (
                    f"Cannot choose a vendor_boot ramdisk fragment for {p}: "
                    "SELinux policy location is device and boot-mode "
                    "specific. Edit an existing policy file in its "
                    "original fragment instead of adding this path to "
                    "the merged ramdisk tree."
                )
            else:
                # Keep the previous default for ordinary new content, but
                # select fragments by their declared type rather than index.
                targets = (platform_entries[:1] or [entries[0]["index"]])
                if (p.startswith(("system/bin/", "bin/", "system/lib64/",
                                  "res/", "twres/", "FFiles/", "sbin/",
                                  "system/etc/")) or "recovery" in p or "fox" in p):
                    targets += recovery_entries[:1]

        for idx in dict.fromkeys(targets):
            ops_by_entry[idx].append(op)

    # Patch and compress each entry independently
    comp_pieces: list[bytes] = []
    updated_entries = []
    current_ramdisk_offset = 0

    import tempfile

    for entry in entries:
        idx = entry["index"]
        e_ops = ops_by_entry.get(idx, [])
        pristine_cpio = meta_dir / f"entry_{idx}.cpio.pristine"
        if not pristine_cpio.is_file():
            return False, f"Pristine cpio missing for entry {idx}"

        original_comp = meta_dir / f"entry_{idx}.comp"
        if not original_comp.is_file():
            return False, f"Original compressed ramdisk missing for entry {idx}"

        if not e_ops:
            # Recompressing an untouched entry can add megabytes and push an AVB
            # image past its fixed partition size. Keep its original stream.
            comp_bytes = original_comp.read_bytes()
        else:
            scratch = Path(tempfile.mkdtemp(prefix=f"rak_v4_{idx}_"))
            try:
                dec = scratch / f"entry_{idx}.cpio"
                shutil.copyfile(pristine_cpio, dec)
                cpio_patch.apply_ops(magiskboot, dec, e_ops, scratch)

                original_bytes = original_comp.read_bytes()
                if original_bytes.startswith(struct.pack("<I", LZ4_LEGACY_MAGIC)):
                    # Preserve the original stream format for edited entries.
                    comp_bytes = compress_lz4_legacy(dec.read_bytes())
                    compression = "lz4_legacy"
                elif original_bytes.startswith((b"070701", b"070702")):
                    comp_bytes = dec.read_bytes()
                    compression = "raw"
                else:
                    detected = mbt.run(
                        [magiskboot, "decompress", original_comp, "original.dec"],
                        scratch,
                    )
                    match = re.search(r"Detected format:\s*\[(.*?)\]", detected)
                    if not match or match.group(1) in ("", "unknown"):
                        return False, f"Cannot identify ramdisk compression for entry {idx}"
                    compression = match.group(1)
                    comp_bytes = b""

                if not comp_bytes:
                    comp_file = scratch / f"entry_{idx}.comp"
                    mbt.run(
                        [magiskboot, f"compress={compression}", dec.name, comp_file.name],
                        scratch,
                    )
                    comp_bytes = comp_file.read_bytes()
            finally:
                shutil.rmtree(scratch, ignore_errors=True)

        comp_pieces.append(comp_bytes)
        updated_entries.append({
            "index": idx,
            "ramdisk_size": len(comp_bytes),
            "ramdisk_offset": current_ramdisk_offset,
            "ramdisk_type": entry["ramdisk_type"],
            "ramdisk_name": entry["ramdisk_name"],
            "board_id_bytes": entry.get("board_id_bytes", "00" * 64),
        })
        current_ramdisk_offset += len(comp_bytes)

    # Concatenate ramdisks
    concatenated_ramdisk = b"".join(comp_pieces)
    total_ramdisk_size = len(concatenated_ramdisk)

    # Pack updated vendor_ramdisk_table
    tbl_bytes = bytearray()
    for ue in updated_entries:
        r_name_b = ue["ramdisk_name"].encode("latin1")[:32].ljust(32, b"\x00")
        b_id_b = bytes.fromhex(ue["board_id_bytes"])[:64].ljust(64, b"\x00")
        tbl_bytes.extend(
            struct.pack(
                TABLE_ENTRY_FORMAT,
                ue["ramdisk_size"],
                ue["ramdisk_offset"],
                ue["ramdisk_type"],
                r_name_b,
                b_id_b,
            )
        )

    # Read DTB
    dtb_file = meta_dir / "dtb"
    dtb_bytes = dtb_file.read_bytes() if dtb_file.is_file() else b""

    # Read Bootconfig
    bc_file = meta_dir / "bootconfig"
    bc_bytes = bc_file.read_bytes() if bc_file.is_file() else b""

    # Read editable header fields (cmdline / name)
    hdr_file = meta_dir.parent / "header"
    if not hdr_file.is_file():
        hdr_file = meta_dir / "header"
    cmdline_str = header_info["cmdline"]
    name_str = header_info["name"]

    if hdr_file.is_file():
        for line in hdr_file.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("cmdline="):
                cmdline_str = line[len("cmdline=") :]
            elif line.startswith("name="):
                name_str = line[len("name=") :]

    # With no visible edits, copy the stock image byte for byte. This also
    # preserves any original padding and signed AVB metadata.
    source_image = meta_dir / mbt.SOURCE_IMAGE
    if (not ops and cmdline_str == header_info["cmdline"] and
            name_str == header_info["name"]):
        source_page = header_info["page_size"]
        aligned = lambda size: ((size + source_page - 1) // source_page) * source_page
        dtb_offset = aligned(header_info["header_size"]) + aligned(header_info["vendor_ramdisk_size"])
        table_offset = dtb_offset + aligned(header_info["dtb_size"])
        bootconfig_offset = table_offset + aligned(header_info["table_size"])
        with source_image.open("rb") as original:
            original.seek(dtb_offset)
            original_dtb = original.read(header_info["dtb_size"])
            original.seek(bootconfig_offset)
            original_bootconfig = original.read(header_info["bootconfig_size"])
        if dtb_bytes == original_dtb and bc_bytes == original_bootconfig:
            shutil.copyfile(source_image, out_img)
            return True, str(out_img)

    cmdline_b = cmdline_str.encode("latin1")[:2048].ljust(2048, b"\x00")
    name_b = name_str.encode("latin1")[:16].ljust(16, b"\x00")
    page_size = header_info["page_size"]

    # Build Header v4
    hdr_bytes = struct.pack(
        HDR_FORMAT,
        b"VNDRBOOT",
        header_info["header_version"],
        page_size,
        header_info["kernel_addr"],
        header_info["ramdisk_addr"],
        total_ramdisk_size,
        cmdline_b,
        header_info["tags_addr"],
        name_b,
        header_info["header_size"],
        len(dtb_bytes),
        header_info["dtb_addr"],
        len(tbl_bytes),
        len(updated_entries),
        TABLE_ENTRY_SIZE,
        len(bc_bytes),
    )
    hdr_bytes = hdr_bytes.ljust(header_info["header_size"], b"\x00")

    # Assemble core image
    assembled = bytearray()
    assembled.extend(pad(hdr_bytes, page_size))
    assembled.extend(pad(concatenated_ramdisk, page_size))
    assembled.extend(pad(dtb_bytes, page_size))
    assembled.extend(pad(bytes(tbl_bytes), page_size))
    assembled.extend(pad(bc_bytes, page_size))

    source_size = (meta_dir / mbt.SOURCE_IMAGE).stat().st_size

    # AVB Handling
    if v4_meta.get("has_avb") and (meta_dir / "vbmeta.bin").is_file():
        vbmeta_blob = bytearray((meta_dir / "vbmeta.bin").read_bytes())
        avb_info = v4_meta["avb_info"]
        original_vbmeta_offset = avb_info["vbmeta_offset"]
        original_image_size = avb_info["original_image_size"]
        if not (0 < original_image_size <= original_vbmeta_offset <= source_size - 64):
            return False, "Invalid original AVB layout"
        max_payload_size = source_size - 64 - len(vbmeta_blob)
        if len(assembled) > max_payload_size:
            return False, (
                f"Repacked vendor_boot needs {len(assembled)} bytes before AVB, "
                f"but the original partition allows {max_payload_size}. "
                "Reduce ramdisk content or compression size."
            )
        image_before_vbmeta_size = max(original_image_size, len(assembled))
        vbmeta_offset = max(original_vbmeta_offset, image_before_vbmeta_size)
        assembled.extend(b"\x00" * (image_before_vbmeta_size - len(assembled)))
        # AvbHashDescriptor starts at descriptors_offset within the auxiliary
        # block. Its variable fields follow a 60-byte reserved field.
        if len(vbmeta_blob) < 256 or vbmeta_blob[:4] != b"AVB0":
            return False, "Invalid original AVB metadata"
        auth_size = struct.unpack_from(">Q", vbmeta_blob, 12)[0]
        desc_offset, desc_size = struct.unpack_from(">QQ", vbmeta_blob, 96)
        pos = 256 + auth_size + desc_offset
        desc_end = pos + desc_size
        if desc_end > len(vbmeta_blob):
            return False, "Invalid original AVB descriptor range"
        while pos + 16 <= desc_end:
            tag, nbytes = struct.unpack_from(">QQ", vbmeta_blob, pos)
            body = pos + 16
            body_end = body + nbytes
            if nbytes == 0 or body_end > desc_end:
                return False, "Invalid original AVB descriptor"
            if tag == 2 and nbytes >= 116:
                algorithm = bytes(vbmeta_blob[body + 8 : body + 40]).split(b"\x00", 1)[0]
                name_len, salt_len, digest_len = struct.unpack_from(">III", vbmeta_blob, body + 40)
                name_pos = body + 116
                salt_pos = name_pos + name_len
                digest_pos = salt_pos + salt_len
                if digest_pos + digest_len > body_end:
                    return False, "Invalid original AVB hash descriptor"
                partition = bytes(vbmeta_blob[name_pos:salt_pos]).rstrip(b"\x00")
                if partition.startswith(b"vendor_boot"):
                    try:
                        digest = hashlib.new(algorithm.decode("ascii"))
                    except (UnicodeDecodeError, ValueError):
                        return False, "Unsupported original AVB hash algorithm"
                    digest.update(vbmeta_blob[salt_pos:digest_pos])
                    digest.update(assembled)
                    if digest.digest_size != digest_len:
                        return False, "Invalid original AVB digest size"
                    struct.pack_into(">Q", vbmeta_blob, body, image_before_vbmeta_size)
                    vbmeta_blob[digest_pos:digest_pos + digest_len] = digest.digest()
            pos = body_end + ((8 - (body_end % 8)) % 8)

        if vbmeta_offset + len(vbmeta_blob) > source_size - 64:
            return False, "Repacked AVB metadata does not fit in the original partition"
        assembled.extend(b"\x00" * (vbmeta_offset - len(assembled)))
        assembled.extend(vbmeta_blob)
        assembled.extend(b"\x00" * (source_size - 64 - len(assembled)))

        # Pack 64-byte AVB footer at end
        # >4sIIQQQ: magic(4), vmaj(4), vmin(4), orig_sz(8), vbmeta_off(8), vbmeta_sz(8)
        footer = struct.pack(
            ">4sIIQQQ",
            b"AVBf",
            v4_meta["avb_info"]["version_major"],
            v4_meta["avb_info"]["version_minor"],
            image_before_vbmeta_size,
            vbmeta_offset,
            len(vbmeta_blob),
        )
        footer = footer.ljust(64, b"\x00")
        assembled.extend(footer)
    elif v4_meta.get("has_avb"):
        return False, "Original AVB metadata is missing"
    else:
        if len(assembled) > source_size:
            return False, (
                f"Repacked vendor_boot needs {len(assembled)} bytes, "
                f"but the original image is {source_size} bytes. "
                "Reduce ramdisk content or compression size."
            )
        assembled.extend(b"\x00" * (source_size - len(assembled)))

    Path(out_img).write_bytes(bytes(assembled))
    return True, str(out_img)
