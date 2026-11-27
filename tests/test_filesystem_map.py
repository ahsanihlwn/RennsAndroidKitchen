# SPDX-License-Identifier: MPL-2.0
"""Tests for Tahap 1 persistent filesystem_map cache, validation, and zero-flicker post-unpack."""
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from PySide6.QtCore import QCoreApplication, QEvent

from core import filesystem_map, native_backend, symlink
from core.app_paths import project_cache_dir
from core.directory_scan import cached_tree_directory_payload, scan_tree_directory
from core.make_symlink import make_symlink, make_symlink_bytes
from core.workspace_files import WorkspaceFiles
from main_ext.class_NeoBranchManager import NeoBranchManager
from ui.mounts import WorkspaceMounts
from ui.workspace_cli import headless_workspace


class FilesystemMapCoreTests(unittest.TestCase):
    def setUp(self):
        filesystem_map.clear_memory_cache()
        self.tmp = tempfile.TemporaryDirectory(prefix="rak-fsmap-")
        self.root = Path(self.tmp.name)

    def tearDown(self):
        filesystem_map.clear_memory_cache()
        self.tmp.cleanup()

    def _pack_sample_erofs(self, name="system"):
        src = self.root / f"_src_{name}"
        (src / "bin").mkdir(parents=True)
        (src / "etc").mkdir(parents=True)
        (src / "empty_dir").mkdir(parents=True)
        (src / "etc" / "build.prop").write_text("ro.build.display.id=RAK\n" * 40, encoding="utf-8")
        (src / "bin" / "toolbox").write_bytes(b"ELF_BIN_" * 150)
        make_symlink(str(src / "bin" / "sh"), "/system/bin/toolbox", overwrite=True)
        make_symlink(str(src / "product_link"), "/product", overwrite=True)
        img = self.root / f"{name}.img"
        native_backend.erofs_pack(str(src), str(img), mount_point=f"/{name}")
        return img

    def test_seed_from_unpack_persists_in_appdata_never_in_rom_tree_and_avoids_reopening(self):
        img = self._pack_sample_erofs("system")
        payload = WorkspaceMounts.build_preview_payload(str(img))
        self.assertIsNotNone(payload)
        child_index, kind, _ = payload

        native_backend.erofs_unpack(str(img), str(self.root), image_base_name="system")
        part_dir = self.root / "system"
        pmap = filesystem_map.seed_from_mount_index(str(self.root), str(part_dir), child_index, kind)
        self.assertIsNotNone(pmap)
        # Cache resides strictly in PROJECT_CACHE_ROOT, never inside partition folder.
        self.assertFalse(any(p.name.startswith("filesystem_map") for p in part_dir.iterdir()))
        expected_cache = Path(project_cache_dir(str(self.root), "filesystem_map")) / "system.json"
        self.assertTrue(expected_cache.is_file())

        # Reload from disk cleanly
        filesystem_map.clear_memory_cache()
        bin_dir = str(part_dir / "bin")
        with patch("core.symlink.read_symlink_target", side_effect=AssertionError("must not read files during peek")):
            peeked = filesystem_map.peek_cached_rows(str(self.root), bin_dir, bin_dir)
        self.assertIsNotNone(peeked)
        by_name = {r["name"]: r for r in peeked}
        self.assertIsNotNone(by_name["sh"]["symlink"])
        self.assertEqual(by_name["sh"]["symlink"]["target"], "/system/bin/toolbox")
        self.assertIsNone(by_name["toolbox"]["symlink"])

        # First disk scan hydrates mtime_ns/size without reading seeded symlinks or >512B files
        orig_read_sym = symlink.read_symlink_target
        with patch("core.symlink.read_symlink_target", wraps=orig_read_sym) as mock_read:
            rows1 = filesystem_map.scan_and_revalidate_dir(str(self.root), bin_dir, bin_dir)
            self.assertEqual(mock_read.call_count, 0)
            self.assertTrue(any(r["name"] == "sh" and r["symlink"] for r in rows1))

            # Second scan sees identical mtime/size -> zero file opens again
            rows2 = filesystem_map.scan_and_revalidate_dir(str(self.root), bin_dir, bin_dir)
            self.assertEqual(mock_read.call_count, 0)
            self.assertEqual({r["name"]: r["size"] for r in rows1}, {r["name"]: r["size"] for r in rows2})

    def test_detects_new_symlink_binary_and_broken_target_dynamically(self):
        part_dir = self.root / "vendor"
        (part_dir / "bin").mkdir(parents=True)
        (part_dir / "bin" / "real_tool").write_bytes(b"A" * 1024)

        # Legacy folder with no map -> lazy backfill builds cache on open
        bin_dir = str(part_dir / "bin")
        rows = filesystem_map.scan_and_revalidate_dir(str(self.root), bin_dir, bin_dir)
        self.assertEqual([r["name"] for r in rows], ["real_tool"])
        self.assertIsNotNone(filesystem_map.get_partition_map(str(self.root), "vendor"))

        # Add a new <=512B binary that is actually a Cygwin symlink to real_tool
        new_link = part_dir / "bin" / "toy"
        new_link.write_bytes(make_symlink_bytes("/vendor/bin/real_tool"))

        rows_after = filesystem_map.scan_and_revalidate_dir(str(self.root), bin_dir, bin_dir)
        by_name = {r["name"]: r for r in rows_after}
        self.assertIsNotNone(by_name["toy"]["symlink"])
        self.assertTrue(by_name["toy"]["symlink"]["exists"])

        # Delete target -> broken symlink reported cleanly on next peek & revalidate
        (part_dir / "bin" / "real_tool").unlink()
        peeked = filesystem_map.peek_cached_rows(str(self.root), bin_dir, bin_dir)
        toy_peek = next(r for r in peeked if r["name"] == "toy")
        self.assertFalse(toy_peek["symlink"]["exists"])

        rows_broken = filesystem_map.scan_and_revalidate_dir(str(self.root), bin_dir, bin_dir)
        toy_scan = next(r for r in rows_broken if r["name"] == "toy")
        self.assertFalse(toy_scan["symlink"]["exists"])

    def test_workspace_files_mutations_update_cache_realtime_and_neobranch_is_isolated(self):
        part_dir = self.root / "product"
        (part_dir / "etc").mkdir(parents=True)
        etc_dir = str(part_dir / "etc")
        filesystem_map.scan_and_revalidate_dir(str(self.root), etc_dir, etc_dir)

        files = WorkspaceFiles(str(self.root))
        created = files.create(etc_dir, "custom.prop", folder=False)
        Path(created).write_text("k=v\n", encoding="utf-8")
        filesystem_map.record_entry(str(self.root), created, created)  # already called on create; re-stat after write

        peek = filesystem_map.peek_cached_rows(str(self.root), etc_dir, etc_dir)
        self.assertEqual([r["name"] for r in peek], ["custom.prop"])

        renamed = files.rename(created, "custom2.prop")
        peek = filesystem_map.peek_cached_rows(str(self.root), etc_dir, etc_dir)
        self.assertEqual([r["name"] for r in peek], ["custom2.prop"])

        dup = files.duplicate(renamed)
        peek = filesystem_map.peek_cached_rows(str(self.root), etc_dir, etc_dir)
        self.assertEqual(sorted(r["name"] for r in peek), ["custom2 - Copy.prop", "custom2.prop"])

        files.delete(dup)
        peek = filesystem_map.peek_cached_rows(str(self.root), etc_dir, etc_dir)
        self.assertEqual([r["name"] for r in peek], ["custom2.prop"])

        # Active NeoBranch partition must bypass cached peek/revalidation cleanly
        (self.root / "config").mkdir(exist_ok=True)
        (self.root / "config" / "product_fs_config").write_text("product 0 0 0755\n", encoding="utf-8")
        (self.root / "config" / "product_file_contexts").write_text("/product u:object_r:system_file:s0\n", encoding="utf-8")
        nb = NeoBranchManager(str(self.root))
        nb.create_branch("product", "dev", "blue")
        nb.set_active_branch("product", "dev")
        self.assertIsNone(cached_tree_directory_payload({"base": str(self.root), "path": etc_dir, "show_apktool_dec": False}))


class DirectoryModelCacheIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QCoreApplication.instance() or QCoreApplication([])

    def setUp(self):
        filesystem_map.clear_memory_cache()
        self.tmp = tempfile.TemporaryDirectory(prefix="rak-dirmap-")
        self.root = Path(self.tmp.name)
        self.session = headless_workspace(str(self.root))
        self.mounts = WorkspaceMounts(self.session)
        self.session.mounts = self.mounts
        self._wait(lambda: not self.session.tree.busy)

    def tearDown(self):
        self.mounts.close()
        self.session.stop()
        self.session.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        filesystem_map.clear_memory_cache()
        self.tmp.cleanup()

    def _wait(self, pred, timeout=15.0):
        deadline = time.monotonic() + timeout
        while not pred():
            self.assertLess(time.monotonic(), deadline)
            self.app.processEvents()
            time.sleep(0.003)

    def test_post_unpack_keeps_expanded_real_folders_and_seeds_map_without_errors(self):
        src = self.root / "_src"
        (src / "etc").mkdir(parents=True)
        (src / "etc" / "prop").write_text("a=1\n" * 30, encoding="utf-8")
        make_symlink(str(src / "vendor_link"), "/vendor", overwrite=True)
        img = self.root / "system.img"
        native_backend.erofs_pack(str(src), str(img), mount_point="/system")

        target_dir = str(self.root / "system")
        self.assertTrue(self.mounts.begin_unpack_preview(str(img), target_dir, str(self.root)))
        self._wait(lambda: not self.session.tree.busy)

        # Expand system -> etc during unpack preview
        sys_node = self.session.tree._nodes[target_dir]
        self.session.tree._scan(sys_node)
        self._wait(lambda: sys_node.loaded and not self.session.tree.busy)
        etc_dir = os.path.join(target_dir, "etc")
        etc_node = self.session.tree._nodes[etc_dir]
        self.session.tree._scan(etc_node)
        self._wait(lambda: etc_node.loaded and not self.session.tree.busy)

        # Finish real unpack on disk -> end_unpack_preview(ok=True)
        native_backend.erofs_unpack(str(img), str(self.root), image_base_name="system")
        self.mounts.end_unpack_preview(target_dir, ok=True, refresh_views=False)
        self.session.tree.reloadMetadata()
        self._wait(lambda: not self.session.tree.busy)

        # Real expanded folders stayed loaded cleanly and filesystem_map is persisted
        self.assertTrue(etc_node.loaded)
        self.assertEqual(self.session.tree.error, "")
        pmap = filesystem_map.get_partition_map(str(self.root), "system")
        self.assertIsNotNone(pmap)
        self.assertIn("etc", pmap.dirs)

        # Fresh DirectoryModel expands `etc` optimistically from cache even before background revalidate
        filesystem_map.clear_memory_cache()
        spec = {"base": str(self.root), "path": etc_dir, "show_apktool_dec": False}
        cached = cached_tree_directory_payload(spec)
        self.assertIsNotNone(cached)
        self.assertEqual([e["name"] for e in cached["entries"]], ["prop"])
        ok, real_payload = scan_tree_directory({**spec, "previous": cached["signature"]})
        self.assertTrue(ok)
        self.assertEqual([e["name"] for e in real_payload["entries"]], ["prop"])


if __name__ == "__main__":
    unittest.main()
