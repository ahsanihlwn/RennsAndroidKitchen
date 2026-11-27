# SPDX-License-Identifier: MPL-2.0
"""Verify native Rust ext_cli mount, inspect, in-place edit, chmod, selinux, and unlink via imgfs."""
from pathlib import Path
import tempfile
import unittest

from core import imgfs, native_backend
from tools.mount_fixture import build_fixture


class NativeExtEditTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="rak-ext-native-")
        self.root = Path(self.tmp.name)
        contexts = self.root / "file_contexts"
        contexts.write_text("/system(/.*)? u:object_r:system_file:s0\n", encoding="utf-8")
        self.img = build_fixture(self.root, contexts=contexts, name="system.img")

    def tearDown(self):
        self.tmp.cleanup()

    def test_mount_list_read_usage_edit_chmod_selinux_and_delete_native(self):
        # 1. Native reader mount/list/read/usage
        fs_kind, reader = imgfs.open_reader(str(self.img))
        try:
            self.assertEqual(fs_kind, "EXT")
            self.assertIsInstance(reader, imgfs._NativeExtHandle)
            nodes = reader.walk()
            paths = {n.path for n in nodes}
            self.assertIn("etc/native.txt", paths)
            raw, node = imgfs.read_path(str(self.img), "etc/native.txt")
            self.assertEqual(raw, b"Original mounted document\n")
            self.assertEqual(node.selinux, "u:object_r:system_file:s0")
        finally:
            reader.close()

        usage = imgfs.usage_report(str(self.img))
        self.assertEqual(usage["fs"], "EXT")
        self.assertGreaterEqual(usage["files"], 1)

        # 2. Direct in-place write via native Rust
        plan = imgfs.plan_path_write(str(self.img), "etc/native.txt", 14)
        self.assertEqual(plan["mode"], "inplace_ext")
        written = imgfs.write_path_data(str(self.img), "etc/native.txt", b"Native Rust!\n")
        self.assertEqual(written, 13)
        self.assertEqual(imgfs.read_path(str(self.img), "etc/native.txt")[0], b"Native Rust!\n")

        # 3. Direct chmod & SELinux update via native Rust
        imgfs.set_perm(str(self.img), "etc/native.txt", "0600")
        imgfs.set_selinux(str(self.img), "etc/native.txt", "u:object_r:sys_file:s0")
        _, updated_node = imgfs.read_path(str(self.img), "etc/native.txt")
        self.assertEqual(updated_node.perm[-9:], "rw-------")
        self.assertEqual(updated_node.selinux, "u:object_r:sys_file:s0")

        # 4. Journaled image rejects structural unlink; non-journaled unlinks cleanly in Rust
        with self.assertRaisesRegex(ValueError, "journal"):
            imgfs.delete_path(str(self.img), "etc/native.txt", recursive=True)

        nojournal = self.root / "system_nojournal.img"
        raw_img = bytearray(self.img.read_bytes())
        # Clear COMPAT_HAS_JOURNAL (0x04) at superblock offset 1024 + 0x5C
        import struct
        compat = struct.unpack_from("<I", raw_img, 1024 + 0x5C)[0]
        struct.pack_into("<I", raw_img, 1024 + 0x5C, compat & ~0x4)
        nojournal.write_bytes(bytes(raw_img))

        rm_res = imgfs.delete_path(str(nojournal), "etc/native.txt", recursive=True)
        self.assertEqual(rm_res["entries_removed"], 1)
        with self.assertRaises(FileNotFoundError):
            imgfs.read_path(str(nojournal), "etc/native.txt")

        # 5. Native in-memory reader (used for APEX payloads)
        raw_bytes = self.img.read_bytes()
        mem_reader = imgfs._NativeExtHandle(native_backend.ext_open_bytes_reader(raw_bytes))
        try:
            self.assertIsNotNone(mem_reader.stat_ino(2))
        finally:
            mem_reader.close()


if __name__ == "__main__":
    unittest.main()
