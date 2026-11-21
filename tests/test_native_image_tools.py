# SPDX-License-Identifier: MPL-2.0
import hashlib
import json
import os
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

from core import binary_handler
from core import check_ext4
from core import cli_environment
from core import dependency_manager
from core import img2simg as img2simg_mod
from core import native_backend
from core import oem_containers
from core import pac_lister
from core import partition_viewer
from core import repack_ext4
from core import sparse_reader
from core import super_logic
from core import unpack_ext4
from tools.mount_fixture import build_fixture
from ui import archive as ui_archive


def _build_super_fixture(path: Path, *, sparse: bool = False):
    """Build a valid minimal A/B super.img fixture matching liblp v10.0."""
    sector = 512
    geom_magic = 0x616C4467
    hdr_magic = 0x414C5030
    meta_max = 65536
    slot_count = 2
    block_size = 4096
    device_size = 4 * 1024 * 1024

    # Data starts after reserved (4096) + 2*geometry (8192) + 2*slots*meta_max
    data_start_bytes = 4096 + 8192 + (meta_max * slot_count * 2)
    sys_a_sector = data_start_bytes // sector
    sys_a_len = 128 * 1024  # 128 KiB
    sys_b_sector = sys_a_sector + (sys_a_len // sector)
    sys_b_len = 128 * 1024
    ven_b_sector = sys_b_sector + (sys_b_len // sector)
    ven_b_len = 64 * 1024

    sys_a_bytes = (b"SYS_A_DATA_" * ((sys_a_len // 11) + 1))[:sys_a_len]
    sys_b_bytes = (b"SYS_B_DATA_" * ((sys_b_len // 11) + 1))[:sys_b_len]
    ven_b_bytes = (b"VEN_B_DATA_" * ((ven_b_len // 11) + 1))[:ven_b_len]

    def fixed(name: str) -> bytes:
        raw = name.encode("ascii")
        return raw + b"\x00" * (36 - len(raw))

    # 4 partitions: system_a (ext 0), system_b (ext 1), vendor_a (empty), vendor_b (ext 2)
    parts = b"".join([
        fixed("system_a") + struct.pack("<IIII", 0x1, 0, 1, 0),
        fixed("system_b") + struct.pack("<IIII", 0x1, 1, 1, 1),
        fixed("vendor_a") + struct.pack("<IIII", 0x1, 2, 0, 0),
        fixed("vendor_b") + struct.pack("<IIII", 0x1, 2, 1, 1),
    ])
    extents = b"".join([
        struct.pack("<QIQI", sys_a_len // sector, 0, sys_a_sector, 0),
        struct.pack("<QIQI", sys_b_len // sector, 0, sys_b_sector, 0),
        struct.pack("<QIQI", ven_b_len // sector, 0, ven_b_sector, 0),
    ])
    groups = b"".join([
        fixed("main_a") + struct.pack("<IQ", 0, 2 * 1024 * 1024),
        fixed("main_b") + struct.pack("<IQ", 0, 2 * 1024 * 1024),
    ])
    bdevs = struct.pack("<QIIQ", data_start_bytes // sector, 1024 * 1024, 0, device_size) + fixed("super") + struct.pack("<I", 0)

    tables = parts + extents + groups + bdevs
    tables_sha = hashlib.sha256(tables).digest()

    part_desc = struct.pack("<III", 0, 4, 52)
    ext_desc = struct.pack("<III", len(parts), 3, 24)
    grp_desc = struct.pack("<III", len(parts) + len(extents), 2, 48)
    bdev_desc = struct.pack("<III", len(parts) + len(extents) + len(groups), 1, 64)

    hdr_zero = (
        struct.pack("<IHHI", hdr_magic, 10, 0, 128)
        + b"\x00" * 32
        + struct.pack("<I", len(tables))
        + tables_sha
        + part_desc + ext_desc + grp_desc + bdev_desc
    )
    hdr_sha = hashlib.sha256(hdr_zero).digest()
    header = hdr_zero[:12] + hdr_sha + hdr_zero[44:]

    geom_zero = struct.pack("<II", geom_magic, 52) + b"\x00" * 32 + struct.pack("<III", meta_max, slot_count, block_size)
    geom_sha = hashlib.sha256(geom_zero).digest()
    geom = (geom_zero[:8] + geom_sha + geom_zero[40:]).ljust(4096, b"\x00")

    raw = bytearray(device_size)
    # Primary & backup geometry
    raw[4096:8192] = geom
    raw[8192:12288] = geom
    # Primary slot 0 metadata & backup slot 0 metadata
    prim_slot0 = 4096 + 8192
    back_slot0 = prim_slot0 + meta_max * slot_count
    meta_blob = header + tables
    raw[prim_slot0:prim_slot0 + len(meta_blob)] = meta_blob
    raw[back_slot0:back_slot0 + len(meta_blob)] = meta_blob
    # Partition payloads
    raw[sys_a_sector * sector:sys_a_sector * sector + sys_a_len] = sys_a_bytes
    raw[sys_b_sector * sector:sys_b_sector * sector + sys_b_len] = sys_b_bytes
    raw[ven_b_sector * sector:ven_b_sector * sector + ven_b_len] = ven_b_bytes

    if not sparse:
        path.write_bytes(bytes(raw))
    else:
        raw_path = path.with_suffix(".raw.tmp")
        raw_path.write_bytes(bytes(raw))
        try:
            native_backend.sparse_img2simg(str(raw_path), str(path), block_size=block_size)
        finally:
            raw_path.unlink(missing_ok=True)
    return {
        "system_a": sys_a_bytes,
        "system_b": sys_b_bytes,
        "vendor_b": ven_b_bytes,
    }


class NativeImageToolsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_tools_are_native_and_excluded_from_external_packages(self):
        empty_bin = self.root / "empty_bin"
        empty_bin.mkdir()
        for name in (
            "lpunpack.exe",
            "lpmake.exe",
            "ext_cli.exe",
            "ext_tool.exe",
            "make_ext4fs.exe",
            "img2simg.exe",
            "simg2img.exe",
            "brotli.exe",
        ):
            self.assertTrue(binary_handler.is_available(name, bin_root=str(empty_bin)), name)

        reg_path = Path(__file__).resolve().parents[1] / "app_ext" / "dependency_registry" / "packagesa.json"
        reg_data = json.loads(reg_path.read_text(encoding="utf-8"))
        for pkg in ("ext_tool", "make_ext4fs", "lpunpack", "brotli_env"):
            self.assertNotIn(pkg, reg_data)

        with patch.object(dependency_manager, "BIN_ROOT", str(empty_bin)), \
             patch.object(dependency_manager, "LOCAL_CACHE_JSON", str(empty_bin / "package.json")):
            mgr = dependency_manager.DependencyManager(allow_remote=False)
            mgr.registry = {
                "ext_tool": {"display": "EXT Unpacker", "binaries": [{"name": "ext_tool.exe"}]},
                "make_ext4fs": {"display": "EXT Repacker", "binaries": [{"name": "make_ext4fs.exe"}]},
                "lpunpack": {"display": "Lpunpack", "binaries": [{"name": "lpunpack.exe"}]},
                "brotli_env": {"display": "Brotli", "binaries": [{"name": "brotli.exe"}]},
                "erofs_env": {"display": "EROFS Environment", "binaries": [{"name": "mkfs.erofs.exe"}]},
            }
            names = [pkg["name"] for pkg in mgr.list_packages()]
            self.assertEqual(names, ["erofs_env"])

        env_info = cli_environment.describe(str(self.root))
        self.assertEqual(env_info["tools"]["lpunpack.exe"].get("backend"), "rak_native")
        self.assertEqual(env_info["tools"]["make_ext4fs.exe"].get("backend"), "rak_native")
        self.assertEqual(env_info["tools"]["ext_cli.exe"].get("backend"), "rak_native")
        self.assertEqual(env_info["tools"]["brotli.exe"].get("backend"), "rak_native")

    def test_ext4_roundtrip_native(self):
        contexts = self.root / "file_contexts"
        contexts.write_text("/system(/.*)? u:object_r:system_file:s0\n", encoding="utf-8")
        img = build_fixture(self.root, contexts=contexts, name="system.img")
        self.assertEqual(check_ext4.check_ext4_magic(str(img)), "EXT4")

        ok, out_dir = unpack_ext4.main(str(img))
        self.assertTrue(ok, out_dir)
        out_path = Path(out_dir)
        self.assertEqual((out_path / "etc" / "native.txt").read_bytes(), b"Original mounted document\n")
        self.assertTrue((self.root / "config" / "system_info").is_file())
        self.assertTrue((self.root / "config" / "system_fs_config").is_file())
        self.assertTrue((self.root / "config" / "system_file_contexts").is_file())

        # Modify unpacked tree and repack natively
        (out_path / "etc" / "added.txt").write_bytes(b"Repacked by native Rust\n")
        ok_repack, rebuilt = repack_ext4.main(str(out_path))
        self.assertTrue(ok_repack, rebuilt)
        rebuilt_path = Path(rebuilt)
        self.assertTrue(rebuilt_path.is_file())
        self.assertEqual(check_ext4.check_ext4_magic(str(rebuilt_path)), "EXT4")

        ok_reunpack, reunpack_dir = unpack_ext4.main(str(rebuilt_path))
        self.assertTrue(ok_reunpack, reunpack_dir)
        self.assertEqual((Path(reunpack_dir) / "etc" / "added.txt").read_bytes(), b"Repacked by native Rust\n")

    def test_sparse_img2simg_and_simg2img_roundtrip_native(self):
        raw_src = self.root / "raw.img"
        payload = (b"\x00" * 4096) + (b"SPARSE_BLOCK_" * 400)[:4096] + (b"\xAA\xBB\xCC\xDD" * 1024)
        raw_src.write_bytes(payload)

        sparse_dst = self.root / "sparse.img"
        ok_s, res_s = img2simg_mod.img2simg(str(raw_src), str(sparse_dst))
        self.assertTrue(ok_s, res_s)
        self.assertEqual(sparse_dst.read_bytes()[:4], b"\x3a\xff\x26\xed")

        raw_dst = self.root / "restored.img"
        ok_u, res_u = sparse_reader.simg2img(str(sparse_dst), str(raw_dst))
        self.assertTrue(ok_u, res_u)
        self.assertEqual(raw_dst.read_bytes(), payload)

    def test_super_logic_normalize_and_unsparse_native(self):
        super_sparse = self.root / "super.img"
        expected = _build_super_fixture(super_sparse, sparse=True)

        info = super_logic.read_super_info(str(super_sparse))
        self.assertEqual(info.partitions, ["system_a", "system_b", "vendor_a", "vendor_b"])
        self.assertEqual(info.active_slot, "B")

        settings_path = self.root / "project_settings.json"
        settings_path.write_text(json.dumps({"super": {"norm": True}}), encoding="utf-8")

        norm_out = self.root / "out_norm"
        norm_out.mkdir()
        with patch.object(super_logic, "SETTINGS_FILE", str(settings_path)):
            ok, res = super_logic.extract_all(str(super_sparse), str(norm_out))
        self.assertTrue(ok, res)
        self.assertEqual((norm_out / "system.img").read_bytes(), expected["system_a"])
        self.assertEqual((norm_out / "vendor.img").read_bytes(), expected["vendor_b"])
        self.assertFalse((norm_out / "system_a.img").exists())
        self.assertFalse((norm_out / "system_b.img").exists())
        self.assertFalse((norm_out / "vendor_a.img").exists())

        # With norm=False, raw slot names are preserved
        settings_path.write_text(json.dumps({"super": {"norm": False}}), encoding="utf-8")
        raw_out = self.root / "out_raw_slots"
        raw_out.mkdir()
        with patch.object(super_logic, "SETTINGS_FILE", str(settings_path)):
            ok2, res2 = super_logic.extract_all(str(super_sparse), str(raw_out))
        self.assertTrue(ok2, res2)
        self.assertEqual((raw_out / "system_a.img").read_bytes(), expected["system_a"])
        self.assertEqual((raw_out / "system_b.img").read_bytes(), expected["system_b"])
        self.assertEqual((raw_out / "vendor_b.img").read_bytes(), expected["vendor_b"])

        # partition_viewer super_info & extract_super (non-normalized viewer path)
        pv_info = partition_viewer.super_info(str(super_sparse))
        self.assertEqual(pv_info["partitions"], ["system_a", "system_b", "vendor_a", "vendor_b"])
        pv_out = self.root / "out_pv"
        pv_out.mkdir()
        partition_viewer.extract_super(str(super_sparse), "system_b", str(pv_out))
        self.assertEqual((pv_out / "system_b.img").read_bytes(), expected["system_b"])

        # Unsparse in-place via super_logic.convert_to_raw
        ok_raw, msg_raw = super_logic.convert_to_raw(str(super_sparse), str(self.root))
        self.assertTrue(ok_raw, msg_raw)
        self.assertNotEqual(super_sparse.read_bytes()[:4], b"\x3a\xff\x26\xed")
        info_after = super_logic.read_super_info(str(super_sparse))
        self.assertEqual(info_after.partitions, ["system_a", "system_b", "vendor_a", "vendor_b"])

    def test_pac_native_inspect_list_verify_and_extract(self):
        def crc16_arc(data: bytes) -> int:
            crc = 0
            for b in data:
                crc ^= b
                for _ in range(8):
                    crc = (crc >> 1) ^ 0xA001 if (crc & 1) else (crc >> 1)
            return crc

        def utf16_fixed(text: str, byte_len: int) -> bytes:
            raw = text.encode("utf-16le")
            return raw[:byte_len].ljust(byte_len, b"\x00")

        entries_spec = [
            ("FLASH", "", b""),
            ("FDL1", "fdl1.bin", b"FDL1_NATIVE_PAC_TEST"),
            ("BOOT", "boot.img", b"BOOT_PAC_DATA_" * 64),
        ]
        table_start = 2124
        entry_size = 2580
        payload_offset = table_start + len(entries_spec) * entry_size
        total_size = payload_offset + sum(len(d) for _, _, d in entries_spec)
        buf = bytearray(total_size)

        off = payload_offset
        for idx, (pid, fname, data) in enumerate(entries_spec):
            base = table_start + idx * entry_size
            struct.pack_into("<I", buf, base, entry_size)
            buf[base + 4:base + 516] = utf16_fixed(pid, 512)
            buf[base + 516:base + 1028] = utf16_fixed(fname, 512)
            sz = len(data)
            struct.pack_into("<IIIIII", buf, base + 1532,
                             sz >> 32, off >> 32, sz & 0xFFFFFFFF,
                             1 if sz else 0, 1, off & 0xFFFFFFFF)
            buf[off:off + sz] = data
            off += sz

        buf[0:44] = utf16_fixed("BP_R2.0.1", 44)
        struct.pack_into("<II", buf, 44, total_size >> 32, total_size & 0xFFFFFFFF)
        buf[52:564] = utf16_fixed("UMS9230_Test", 512)
        buf[564:1076] = utf16_fixed("PAC_TEST_FW_V1", 512)
        struct.pack_into("<II", buf, 1076, len(entries_spec), table_start)
        buf[1104:1304] = utf16_fixed("TestAlias", 200)
        struct.pack_into("<I", buf, 2116, 0xFFFAFFFA)
        crc1 = crc16_arc(bytes(buf[:2120]))
        crc2 = crc16_arc(bytes(buf[2124:]))
        struct.pack_into("<HH", buf, 2120, crc1, crc2)

        pac_path = self.root / "firmware.pac"
        pac_path.write_bytes(bytes(buf))

        self.assertEqual(oem_containers.detect_oem_kind(str(pac_path)), "PAC")
        inspected = oem_containers.inspect_container(str(pac_path))
        self.assertEqual(inspected["kind"], "PAC")
        self.assertEqual(inspected["metadata"]["header_crc_valid"], "true")
        self.assertEqual([e["name"] for e in inspected["entries"]], ["fdl1.bin", "boot.img"])

        crc_res = oem_containers.verify_pac_crc(str(pac_path))
        self.assertTrue(crc_res["ok"])
        self.assertEqual(crc_res["crc1_computed"], crc1)
        self.assertEqual(crc_res["crc2_computed"], crc2)

        listing = pac_lister.list_pac(pac_path)
        self.assertEqual(listing.header.product_name, "UMS9230_Test")
        self.assertEqual([e.file_name for e in listing.files], ["fdl1.bin", "boot.img"])
        self.assertEqual(len(ui_archive.list_archive(str(pac_path))), 3)

        out_oem = self.root / "pac_oem"
        ext_res = oem_containers.extract_container(str(pac_path), str(out_oem), selected=["boot.img"])
        self.assertEqual(ext_res["extracted"], ["boot.img"])
        self.assertEqual((out_oem / "boot.img").read_bytes(), entries_spec[2][2])
        self.assertFalse((out_oem / "fdl1.bin").exists())

        out_arc = self.root / "pac_arc"
        ui_archive.extract_archive(str(pac_path), str(out_arc))
        self.assertEqual((out_arc / "fdl1.bin").read_bytes(), entries_spec[1][2])
        self.assertEqual((out_arc / "boot.img").read_bytes(), entries_spec[2][2])

        # Corrupt payload byte -> verify_pac_crc rejects CRC2 mismatch
        buf[-1] ^= 0xFF
        bad_pac = self.root / "bad.pac"
        bad_pac.write_bytes(bytes(buf))
        with self.assertRaises(Exception):
            oem_containers.verify_pac_crc(str(bad_pac))

    def test_boot_native_compress_decompress_detect_and_cpio(self):
        # 1. Format detection and compression / decompression roundtrip
        raw_data = b"Hello Android Boot Native Magiskboot!" * 50
        raw_file = self.root / "payload.raw"
        raw_file.write_bytes(raw_data)

        self.assertEqual(native_backend.boot_detect_format(str(raw_file)), "raw")

        for fmt in ("gzip", "bzip2", "xz", "lz4", "zstd"):
            comp_file = self.root / f"payload.{fmt}"
            comp_res = native_backend.boot_compress(str(raw_file), str(comp_file), fmt)
            self.assertEqual(comp_res["format"], fmt)
            self.assertTrue(comp_file.is_file())
            self.assertGreater(comp_file.stat().st_size, 0)

            detected = native_backend.boot_detect_format(str(comp_file))
            self.assertEqual(detected, fmt)

            decomp_file = self.root / f"payload_decomp_{fmt}.raw"
            decomp_res = native_backend.boot_decompress(str(comp_file), str(decomp_file))
            self.assertEqual(decomp_file.read_bytes(), raw_data)

        # 2. Native CPIO operations
        cpio_dir = self.root / "test_cpio"
        cpio_dir.mkdir(parents=True, exist_ok=True)
        init_bin = cpio_dir / "init"
        init_bin.write_bytes(b"#!/system/bin/sh\necho boot\n")

        archive_file = cpio_dir / "ramdisk.cpio"
        # Create empty file first
        archive_file.write_bytes(b"")

        # Use native CPIO add command
        res = native_backend.boot_cpio(str(archive_file), [f"add 0755 init {init_bin.as_posix()}"])
        self.assertTrue(res.get("ok", False) or res.get("exit_code") == 0)

    def test_erofs_native_roundtrip_pack_inspect_read_verify_unpack_repack(self):
        src_dir = self.root / "erofs_product"
        (src_dir / "etc").mkdir(parents=True, exist_ok=True)
        (src_dir / "etc" / "build.prop").write_text("ro.product.model=Pixel\nro.build.version=15\n", encoding="utf-8")
        (src_dir / "test.txt").write_bytes(b"Hello EROFS Native Rust!\n")

        img_path = self.root / "product.img"
        # 1. Pack
        rep = native_backend.erofs_pack(
            str(src_dir), str(img_path), algo="lz4hc", mount_point="/product"
        )
        self.assertTrue(img_path.is_file())
        self.assertGreater(rep["image_bytes"], 0)
        self.assertIn("lz4hc", rep["algorithm"])

        # 2. Detect & Info
        det = native_backend.erofs_detect(str(img_path))
        self.assertTrue(det["is_erofs"])
        self.assertFalse(det["is_sparse"])

        info = native_backend.erofs_info(str(img_path))
        self.assertEqual(info["block_size"], 4096)
        self.assertTrue(info["uuid"])

        # 3. List & Stat & Read
        entries = native_backend.erofs_list(str(img_path))
        paths = [e["path"] for e in entries]
        self.assertIn("/etc/build.prop", paths)

        stat = native_backend.erofs_stat(str(img_path), "/etc/build.prop")
        self.assertEqual(stat["path"], "/etc/build.prop")

        data = native_backend.erofs_read_bytes(str(img_path), "/etc/build.prop")
        self.assertIn(b"ro.product.model=Pixel", data)

        # 4. Verify
        v_rep = native_backend.erofs_verify(str(img_path))
        self.assertTrue(v_rep["ok"])

        # 5. Direct edit without unpacking
        new_data = b"ro.product.model=PixelUpdated\nro.build.version=15\n"
        w_rep = native_backend.erofs_edit_write(str(img_path), "/etc/build.prop", new_data)
        self.assertEqual(w_rep["path"], "/etc/build.prop")
        data_after = native_backend.erofs_read_bytes(str(img_path), "/etc/build.prop")
        self.assertIn(b"PixelUpdated", data_after)

        # 6. Usage & Compact
        usage = native_backend.erofs_usage(str(img_path))
        self.assertGreater(usage["total_blocks"], 0)

        compact = native_backend.erofs_compact(str(img_path))
        self.assertIn("moves", compact)

        # 7. Unpack via core.erofs.unpack_erofs
        from core.erofs import unpack_erofs, repack_erofs
        unpack_ok, unpack_msg = unpack_erofs(str(img_path))
        self.assertTrue(unpack_ok, unpack_msg)
        self.assertTrue((self.root / "product" / "etc" / "build.prop").is_file())
        cfg_dir = self.root / "config"
        self.assertTrue((cfg_dir / "product_fs_config").is_file())
        self.assertTrue((cfg_dir / "product_file_contexts").is_file())
        self.assertTrue((cfg_dir / "product_fs_options").is_file())

        # 8. Repack via core.erofs.repack_erofs
        repack_ok, repack_msg = repack_erofs(str(self.root / "product"))
        self.assertTrue(repack_ok, repack_msg)
        new_img = self.root / "product_new.img"
        self.assertTrue(new_img.is_file())
        v_new = native_backend.erofs_verify(str(new_img))
        self.assertTrue(v_new["ok"])

    def test_erofs_binary_resolver_and_cli_environment(self):
        from core.app_paths import BIN_DIR
        for name in ("mkfs.erofs.exe", "extract.erofs.exe", "dump.erofs.exe", "fsck.erofs.exe"):
            path = binary_handler.resolve_binary(BIN_DIR, name)
            self.assertIsNotNone(path, f"resolve_binary failed for {name}")
            self.assertTrue(path.endswith("__init__.py"), f"Expected native module path, got {path}")

    def test_erofs_imgfs_and_mounts_use_100pct_native_rust(self):
        import importlib
        from core import imgfs
        from ui.mounts import WorkspaceMounts

        # 1. Verify legacy Python EROFS modules and ErofsReader are gone
        self.assertFalse(hasattr(imgfs, "ErofsReader"))
        for legacy in ("core.erofs_write", "core.erofs_compact", "core.erofs_z"):
            with self.assertRaises(ModuleNotFoundError):
                importlib.import_module(legacy)

        # 2. Pack an uncompressed EROFS image + a sparse copy for read/browse testing
        src_dir = self.root / "mnt_src"
        (src_dir / "etc").mkdir(parents=True, exist_ok=True)
        (src_dir / "etc" / "prop.default").write_bytes(b"ro.rak.native=1\n")
        (src_dir / "etc" / "extra.txt").write_bytes(b"delete_me\n")
        (src_dir / "readme.txt").write_bytes(b"hello mount\n")

        raw_img = self.root / "raw_erofs.img"
        sparse_img = self.root / "sparse_erofs.img"
        native_backend.erofs_pack(str(src_dir), str(raw_img), algo="none", mount_point="/system")
        native_backend.sparse_img2simg(str(raw_img), str(sparse_img), block_size=4096)

        # 3. Verify detect/list/read/extract/usage on sparse EROFS image
        self.assertEqual(imgfs.detect_fs(str(sparse_img)), "EROFS")
        sparse_entries = imgfs.list_image(str(sparse_img))
        self.assertIn("etc/prop.default", [e["path"] for e in sparse_entries])
        sparse_bytes, sparse_node = imgfs.read_path(str(sparse_img), "etc/prop.default")
        self.assertEqual(sparse_bytes, b"ro.rak.native=1\n")
        self.assertFalse(sparse_node.is_dir)
        with self.assertRaises(NotImplementedError):
            imgfs.write_path_data(str(sparse_img), "etc/prop.default", b"blocked")

        # 4. In-memory bytes reader (APEX payload flow)
        raw_blob = raw_img.read_bytes()
        with native_backend.erofs_open_bytes_reader(raw_blob) as mem_r:
            root_kids = json.loads(mem_r.children_json(mem_r.root_nid))
            self.assertIn("etc", [k["name"] for k in root_kids])
            self.assertEqual(mem_r.read_path("/readme.txt"), b"hello mount\n")

        # 5. Full imgfs read/plan/preflight/write/chmod/delete/compact/extract/usage
        self.assertEqual(imgfs.detect_fs(str(raw_img)), "EROFS")
        with self.assertRaises(FileNotFoundError):
            imgfs.resolve_node(str(raw_img), "does/not/exist")
        with self.assertRaises(IsADirectoryError):
            imgfs.read_path(str(raw_img), "etc")

        plan = imgfs.plan_path_write(str(raw_img), "etc/prop.default", 32)
        self.assertIn("mode", plan)
        pre = imgfs.preflight_write_path_data(str(raw_img), "etc/prop.default", b"ro.rak.native=2\n")
        self.assertEqual(pre["grow"], pre["grow_bytes"])

        written_len = imgfs.write_path_data(str(raw_img), "etc/prop.default", b"ro.rak.native=updated\n")
        self.assertEqual(written_len, len(b"ro.rak.native=updated\n"))
        read_back, _ = imgfs.read_path(str(raw_img), "etc/prop.default")
        self.assertEqual(read_back, b"ro.rak.native=updated\n")

        new_mode = imgfs.set_perm(str(raw_img), "etc/prop.default", "0640")
        self.assertEqual(new_mode & 0o7777, 0o640)

        with self.assertRaises(ValueError):
            imgfs.delete_path(str(raw_img), "etc", recursive=False)
        del_res = imgfs.delete_path(str(raw_img), "etc/extra.txt", recursive=False)
        self.assertEqual(del_res["removed"], "etc/extra.txt")

        usage = imgfs.usage_report(str(raw_img))
        self.assertEqual(usage["fs"], "EROFS")
        for key in ("used_blocks", "free_blocks", "used_bytes", "wasted_bytes", "links", "files", "dirs"):
            self.assertIn(key, usage)

        compact = imgfs.compact_image(str(raw_img), dry_run=True)
        self.assertIn("moves", compact)

        out_dir = self.root / "extracted_mnt"
        ext_res = imgfs.extract_path(str(raw_img), "etc/prop.default", str(out_dir))
        self.assertEqual(ext_res["files"], 1)
        self.assertEqual((out_dir / "prop.default").read_bytes(), b"ro.rak.native=updated\n")

        # 6. Virtual Device ImageSource browse/read + rak_img_cli info/stat on EROFS
        from core.vdev.source import ImageSource
        from core import rak_img_cli

        vsrc = ImageSource(str(raw_img))
        vsrc_entries = vsrc.entries("etc")
        self.assertIn("prop.default", vsrc_entries)
        self.assertEqual(vsrc.read("etc/prop.default"), b"ro.rak.native=updated\n")

        ok_info, info_out = rak_img_cli.cmd_info([str(raw_img)], str(self.root))
        self.assertTrue(ok_info, info_out)
        self.assertIn("Filesystem:  EROFS", info_out)
        ok_stat, stat_out = rak_img_cli.cmd_stat([str(raw_img), "/etc/prop.default", "--json"], str(self.root))
        self.assertTrue(ok_stat, stat_out)
        self.assertEqual(json.loads(stat_out)["size"], len(b"ro.rak.native=updated\n"))

        desc = cli_environment.describe(str(self.root))
        tools = desc["tools"]
        for tool_name in ("mkfs.erofs.exe", "dump.erofs.exe"):
            self.assertIn(tool_name, tools)
            self.assertTrue(tools[tool_name]["available"])
            self.assertEqual(tools[tool_name].get("backend"), "rak_native")

    def test_sdat_native_roundtrip_img2sdat_and_sdat2img(self):
        # 1. Create a dummy partition image of 16 blocks (64 KiB)
        block_size = 4096
        num_blocks = 16
        img_data = bytearray(block_size * num_blocks)
        for b in range(num_blocks):
            pattern = (f"BLK_{b:04d}_TEST_".encode("ascii") * 300)[:block_size]
            img_data[b * block_size : (b + 1) * block_size] = pattern

        raw_img = self.root / "system.img"
        raw_img.write_bytes(img_data)

        # 2. img2sdat via native_backend
        ota_dir = self.root / "ota"
        prefix_out = native_backend.img2sdat(
            str(raw_img), str(ota_dir), version=4, prefix="system"
        )
        self.assertTrue(prefix_out.endswith("system"))

        tl_path = ota_dir / "system.transfer.list"
        dat_path = ota_dir / "system.new.dat"
        patch_path = ota_dir / "system.patch.dat"

        self.assertTrue(tl_path.is_file())
        self.assertTrue(dat_path.is_file())
        self.assertTrue(patch_path.is_file())

        # 3. Inspect transfer list
        info = native_backend.sdat_transfer_list_info(str(tl_path))
        self.assertEqual(info["version"], 4)
        self.assertEqual(info["total_bytes"], len(img_data))
        self.assertGreater(info["commands_count"], 0)

        # 4. sdat2img via native_backend
        restored_img = self.root / "system_restored.img"
        out_res = native_backend.sdat2img(str(tl_path), str(dat_path), str(restored_img))
        self.assertTrue(restored_img.is_file())
        self.assertEqual(restored_img.read_bytes(), bytes(img_data))

    def test_sdat_binary_resolver(self):
        from core.app_paths import BIN_DIR
        for name in ("sdat2img.exe", "img2sdat.exe", "sdatimg.exe"):
            path = binary_handler.resolve_binary(BIN_DIR, name)
            self.assertIsNotNone(path, f"resolve_binary failed for {name}")
            self.assertTrue(path.endswith("__init__.py"), f"Expected native module path, got {path}")

    def test_brotli_native_roundtrip_and_no_subprocess(self):
        from core import brotli_tool, flashable_build, flashable_size, kitchen_conversions

        # 1. Verify no subprocess module or execution in brotli_tool
        self.assertFalse(hasattr(brotli_tool, "subprocess"))

        # 2. Bytes roundtrip across qualities, modes, and large-window lgwin=26
        sample_text = (b"ro.build.display.id=RAK_NATIVE_BROTLI\n" * 512) + bytes(range(256)) * 64
        for quality in (0, 1, 6, 11):
            for mode in ("generic", "text"):
                comp = native_backend.brotli_compress_bytes(sample_text, quality=quality, mode=mode)
                self.assertEqual(native_backend.brotli_decompress_bytes(comp), sample_text)
        comp_lw = native_backend.brotli_compress_bytes(sample_text, quality=4, lgwin=26)
        self.assertEqual(native_backend.brotli_decompress_bytes(comp_lw), sample_text)

        # 3. File roundtrip, decompressed size probe, and chunked/full BrotliFileReader
        raw_path = self.root / "sample.bin"
        raw_data = (b"BROTLI_STREAM_BLOCK_" * 4000) + bytes(range(256)) * 128
        raw_path.write_bytes(raw_data)
        br_path = self.root / "sample.bin.br"

        stats_c = native_backend.brotli_compress_file(str(raw_path), str(br_path), quality=3)
        self.assertEqual(stats_c["bytes_in"], len(raw_data))
        self.assertTrue(br_path.is_file())
        self.assertEqual(native_backend.brotli_decompressed_size(str(br_path)), len(raw_data))
        self.assertEqual(flashable_size.raw_image_size(str(br_path)), len(raw_data))

        out_path = self.root / "sample_out.bin"
        stats_d = native_backend.brotli_decompress_file(str(br_path), str(out_path))
        self.assertEqual(stats_d["bytes_out"], len(raw_data))
        self.assertEqual(out_path.read_bytes(), raw_data)

        # Chunked reading + close() lock release + full read(None)
        chunks = []
        with native_backend.brotli_open_reader(str(br_path)) as reader:
            while True:
                piece = reader.read(777)
                if not piece:
                    break
                self.assertLessEqual(len(piece), 777)
                chunks.append(piece)
        self.assertEqual(b"".join(chunks), raw_data)

        stream, closer = flashable_build._open_raw_stream(str(br_path), "br")
        try:
            self.assertEqual(stream.read(None), raw_data)
            self.assertEqual(stream.read(), b"")
        finally:
            closer()

        # 4. core.brotli_tool API, settings custom level, keep_source=True/False, corrupt cleanup
        settings_path = self.root / "project_settings.json"
        settings_path.write_text(json.dumps({"brotli": {"default": False, "level": 4}}), encoding="utf-8")

        with patch("subprocess.run", side_effect=AssertionError("subprocess.run must not be called")), \
             patch.object(brotli_tool, "SETTINGS_FILE", str(settings_path)):
            self.assertEqual(brotli_tool.get_brotli_level(), 4)

            tool_src = self.root / "partition.img"
            tool_src.write_bytes(raw_data)
            ok_c, produced_br = brotli_tool.compress_to_br(str(tool_src), keep_source=True)
            self.assertTrue(ok_c, produced_br)
            self.assertTrue(tool_src.is_file())
            self.assertTrue(Path(produced_br).is_file())

            tool_src.unlink()
            ok_d, produced_img = brotli_tool.decompress_from_br(produced_br, keep_source=False)
            self.assertTrue(ok_d, produced_img)
            self.assertFalse(Path(produced_br).exists())
            self.assertEqual(Path(produced_img).read_bytes(), raw_data)

            # keep_source=False on compress
            ok_c2, produced_br2 = brotli_tool.compress_to_br(str(tool_src), keep_source=False)
            self.assertTrue(ok_c2, produced_br2)
            self.assertFalse(tool_src.exists())

            # Corrupt .br returns (False, ...) and leaves no partial output file
            corrupt_br = self.root / "broken.img.br"
            corrupt_br.write_bytes(b"\xff\x00\xab\xcd_corrupt_brotli")
            ok_bad, msg_bad = brotli_tool.decompress_from_br(str(corrupt_br), keep_source=True)
            self.assertFalse(ok_bad)
            self.assertIn("brotli decompress failed", msg_bad)
            self.assertFalse((self.root / "broken.img").exists())

        # 5. End-to-end DAT + Brotli roundtrip via kitchen_conversions
        block_size = 4096
        conv_data = bytearray(block_size * 8)
        for b in range(8):
            pat = (f"BR_DAT_BLK_{b:02d}_".encode("ascii") * 400)[:block_size]
            conv_data[b * block_size : (b + 1) * block_size] = pat

        conv_dir = self.root / "conv"
        conv_dir.mkdir()
        conv_img = conv_dir / "vendor.img"
        conv_img.write_bytes(conv_data)
        with patch("subprocess.run", side_effect=AssertionError("subprocess.run must not be called")):
            dat_br = kitchen_conversions.image_to_dat(str(conv_img), brotli=True)
            self.assertTrue(dat_br.endswith(".new.dat.br"))
            self.assertTrue(Path(dat_br).is_file())
            conv_img.unlink()
            rebuilt_img = kitchen_conversions.dat_to_image(dat_br)
            self.assertEqual(Path(rebuilt_img).read_bytes(), bytes(conv_data))


if __name__ == "__main__":
    unittest.main()
