# SPDX-License-Identifier: MPL-2.0
"""Tests for Tahap 2 (NeoBranch + Editor/Agent filesystem_map) and Tahap 3 (OS Watcher & DirectoryModel)."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from PySide6.QtCore import QCoreApplication, QEvent, QObject, Qt, Signal

from core import agent_documents, filesystem_map
from core.directory_scan import cached_tree_directory_payload, scan_tree_directory
from core.editor_document import EditorBridge
from core.fs_watcher import ProjectFsWatcher
from core.make_symlink import make_symlink_bytes
from core.workspace_files import WorkspaceFiles
from main_ext.class_NeoBranchManager import NeoBranchManager
from ui.directory import DirectoryModel


class FilesystemMapNeoBranchTests(unittest.TestCase):
    def setUp(self):
        filesystem_map.clear_memory_cache()
        self.tmp = tempfile.TemporaryDirectory(prefix="rak-nb-fsmap-")
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(filesystem_map.clear_memory_cache)
        self.root = Path(self.tmp.name)

        self.vendor = self.root / "vendor"
        (self.vendor / "etc").mkdir(parents=True)
        (self.vendor / "bin").mkdir(parents=True)
        (self.vendor / "etc" / "build.prop").write_text("ro.src=1\n", encoding="utf-8")
        (self.vendor / "bin" / "tool").write_bytes(b"TOOL" * 200)
        (self.root / "config").mkdir()
        (self.root / "config" / "vendor_file_contexts").write_text("/vendor(/.*)? u:object_r:vendor_file:s0\n")
        (self.root / "config" / "vendor_fs_config").write_text("vendor 0 0 0755\n")

    def test_physical_checkout_clones_isolates_switches_and_cleans_map(self):
        etc_dir = str(self.vendor / "etc")
        bin_dir = str(self.vendor / "bin")
        filesystem_map.scan_and_revalidate_dir(str(self.root), str(self.vendor), str(self.vendor))
        filesystem_map.scan_and_revalidate_dir(str(self.root), etc_dir, etc_dir)
        filesystem_map.scan_and_revalidate_dir(str(self.root), bin_dir, bin_dir)

        nb = NeoBranchManager(str(self.root))
        nb.create_checkout_branch("vendor", "feat_port", "blue")

        branch_map = filesystem_map.get_partition_map(str(self.root), "vendor", branch_name="feat_port")
        self.assertIsNotNone(branch_map)
        self.assertTrue(os.path.isfile(branch_map.cache_path))
        self.assertIn("etc", branch_map.dirs)

        peek_branch = filesystem_map.peek_cached_rows(str(self.root), etc_dir, etc_dir)
        self.assertIsNotNone(peek_branch)
        self.assertEqual([r["name"] for r in peek_branch], ["build.prop"])

        # Mutate inside physical checkout branch via WorkspaceFiles
        files = WorkspaceFiles(str(self.root))
        added = files.create(etc_dir, "port.prop", folder=False)
        renamed = files.rename(str(self.vendor / "etc" / "build.prop"), "vendor.prop")
        self.assertTrue(os.path.isfile(added))
        self.assertTrue(os.path.isfile(renamed))

        peek_mutated = filesystem_map.peek_cached_rows(str(self.root), etc_dir, etc_dir)
        self.assertEqual(sorted(r["name"] for r in peek_mutated), ["port.prop", "vendor.prop"])

        # Source map must remain completely untouched
        src_map = filesystem_map.get_partition_map(str(self.root), "vendor", branch_name=None)
        self.assertEqual(sorted(src_map.dirs["etc"]["entries"].keys()), ["build.prop"])

        # Switch back to Source -> Source snapshot is served immediately
        nb.checkout_branch("vendor", None)
        peek_source = filesystem_map.peek_cached_rows(str(self.root), etc_dir, etc_dir)
        self.assertEqual([r["name"] for r in peek_source], ["build.prop"])

        # Delete branch -> branch cache JSON is deleted while Source cache JSON survives
        branch_cache_path = branch_map.cache_path
        src_cache_path = src_map.cache_path
        nb.delete_branch("vendor", "feat_port")
        self.assertFalse(os.path.exists(branch_cache_path))
        self.assertTrue(os.path.isfile(src_cache_path))

    def test_legacy_overlay_uses_immutable_source_map_without_polluting_it(self):
        etc_dir = str(self.vendor / "etc")
        filesystem_map.scan_and_revalidate_dir(str(self.root), etc_dir, etc_dir)

        nb = NeoBranchManager(str(self.root))
        nb.create_branch("vendor", "overlay_dev", "green")
        nb.set_active_branch("vendor", "overlay_dev")
        nb.record_delete("vendor", "overlay_dev", "vendor/etc/build.prop")

        files = WorkspaceFiles(str(self.root))
        overlay_file = files.create(etc_dir, "extra.prop", folder=False)
        phys_overlay, _ = files.view.resolve(overlay_file)
        self.assertTrue(phys_overlay and os.path.isfile(phys_overlay))

        # Source cache stays untouched (still only has build.prop, NOT extra.prop)
        src_map = filesystem_map.get_partition_map(str(self.root), "vendor", branch_name=None)
        self.assertEqual(list(src_map.dirs["etc"]["entries"].keys()), ["build.prop"])

        # Peek stays disabled for legacy overlay, while scan merges delta + overlay cleanly
        self.assertIsNone(cached_tree_directory_payload({"base": str(self.root), "path": etc_dir, "show_apktool_dec": False}))
        ok, payload = scan_tree_directory({"base": str(self.root), "path": etc_dir, "show_apktool_dec": False})
        self.assertTrue(ok)
        self.assertEqual([e["name"] for e in payload["entries"]], ["extra.prop"])

    def test_editor_save_and_agent_apply_update_filesystem_map(self):
        etc_dir = str(self.vendor / "etc")
        prop_path = str(self.vendor / "etc" / "build.prop")
        filesystem_map.scan_and_revalidate_dir(str(self.root), etc_dir, etc_dir)

        nb = NeoBranchManager(str(self.root))
        nb.create_checkout_branch("vendor", "edit_branch", "blue")

        # 1. Background agent document apply updates branch filesystem_map
        files = WorkspaceFiles(str(self.root))
        doc, _ = agent_documents.read(files, prop_path)
        new_agent_text = "ro.src=agent_updated_123456\n"
        agent_documents.apply(files, prop_path, doc["revision"], new_agent_text)

        peeked = filesystem_map.peek_cached_rows(str(self.root), etc_dir, etc_dir)
        self.assertEqual(peeked[0]["size"], Path(prop_path).stat().st_size)

        # 2. EditorBridge.save_file updates branch filesystem_map immediately
        bridge = EditorBridge()
        with patch.object(bridge, "_resolve_icon_b64", return_value=""):
            self.assertTrue(bridge.open_file(prop_path))
        bridge.bind_neobranch_tab(prop_path, str(self.root), "vendor", prop_path, "edit_branch")
        new_editor_text = "ro.src=editor_saved_payload_abcdef\n"
        bridge.save_file(prop_path, new_editor_text)

        peeked2 = filesystem_map.peek_cached_rows(str(self.root), etc_dir, etc_dir)
        self.assertEqual(peeked2[0]["size"], Path(prop_path).stat().st_size)


class _WatcherQtRelay(QObject):
    dirty = Signal(object)


class ProjectFsWatcherTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QCoreApplication.instance() or QCoreApplication([])

    def setUp(self):
        filesystem_map.clear_memory_cache()
        self.tmp = tempfile.TemporaryDirectory(prefix="rak-fswatch-")
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(filesystem_map.clear_memory_cache)
        self.root = Path(self.tmp.name)
        (self.root / "system" / "bin").mkdir(parents=True)
        (self.root / "system" / "etc").mkdir(parents=True)
        (self.root / "system" / "bin" / "target_bin").write_bytes(b"X" * 1024)
        filesystem_map.scan_and_revalidate_dir(str(self.root), str(self.root / "system" / "bin"), str(self.root / "system" / "bin"))
        filesystem_map.scan_and_revalidate_dir(str(self.root), str(self.root / "system" / "etc"), str(self.root / "system" / "etc"))

    def _wait(self, pred, timeout=5.0):
        deadline = time.monotonic() + timeout
        while not pred():
            self.assertLess(time.monotonic(), deadline)
            self.app.processEvents()
            time.sleep(0.01)

    @unittest.skipUnless(sys.platform == "win32", "ReadDirectoryChangesW watcher is Windows-specific")
    def test_watcher_detects_external_file_cygwin_symlink_broken_target_and_lock_suppression(self):
        batches = []
        done_event = threading.Event()
        locked_parts = set()

        def is_locked(path):
            norm = os.path.normcase(os.path.normpath(path))
            return any(norm == lp or norm.startswith(lp + os.sep) for lp in locked_parts)

        def on_batch(events, overflowed):
            dirty = filesystem_map.sync_external_paths(str(self.root), [p for p, _ in events], overflowed=overflowed)
            batches.append((events, overflowed, dirty))
            done_event.set()

        watcher = ProjectFsWatcher(str(self.root), on_batch, is_locked_fn=is_locked, debounce_sec=0.05)
        self.addCleanup(watcher.stop)
        self.assertTrue(watcher.start())

        # 1. External regular file creation
        etc_dir = str(self.root / "system" / "etc")
        ext_file = self.root / "system" / "etc" / "shell_added.txt"
        ext_file.write_text("from shell\n", encoding="utf-8")
        self._wait(lambda: any(r["name"] == "shell_added.txt" for r in (filesystem_map.peek_cached_rows(str(self.root), etc_dir, etc_dir) or ())))

        # 2. External Cygwin symlink creation in a separate process (simulating RAK Terminal)
        sym_path = self.root / "system" / "etc" / "bin_alias"
        sym_bytes = make_symlink_bytes("/system/bin/target_bin")
        subprocess.run(
            [sys.executable, "-c",
             "import sys; open(sys.argv[1], 'wb').write(bytes.fromhex(sys.argv[2]))",
             str(sym_path), sym_bytes.hex()],
            check=True,
        )
        self._wait(lambda: any(r["name"] == "bin_alias" for r in (filesystem_map.peek_cached_rows(str(self.root), etc_dir, etc_dir) or ())))
        peek_etc = filesystem_map.peek_cached_rows(str(self.root), etc_dir, etc_dir)
        alias_row = next(r for r in peek_etc if r["name"] == "bin_alias")
        self.assertIsNotNone(alias_row["symlink"])
        self.assertEqual(alias_row["symlink"]["target"], "/system/bin/target_bin")
        self.assertTrue(alias_row["symlink"]["exists"])

        # 3. Deleting the target in `system/bin` notifies `system/etc` (symlink parent) AND shows broken link
        time.sleep(0.12)
        batches.clear()
        (self.root / "system" / "bin" / "target_bin").unlink()
        self._wait(
            lambda: any(
                dirty and os.path.normcase(etc_dir) in {os.path.normcase(os.path.normpath(d)) for d in dirty}
                for _, _, dirty in batches
            )
        )
        peek_etc_broken = filesystem_map.peek_cached_rows(str(self.root), etc_dir, etc_dir)
        alias_broken = next(r for r in peek_etc_broken if r["name"] == "bin_alias")
        self.assertFalse(alias_broken["symlink"]["exists"])

        # 4. Locked partition suppresses watcher events
        time.sleep(0.12)
        done_event.clear()
        batches.clear()
        locked_parts.add(os.path.normcase(os.path.normpath(str(self.root / "system"))))
        (self.root / "system" / "etc" / "ignored_while_locked.txt").write_text("locked\n", encoding="utf-8")
        self.assertFalse(done_event.wait(0.35))
        self.assertEqual(batches, [])

        # Stop cleanly releases directory handle for TemporaryDirectory cleanup
        watcher.stop()

    @unittest.skipUnless(sys.platform == "win32", "ReadDirectoryChangesW watcher is Windows-specific")
    def test_directory_model_realtime_sync_and_clean_stop(self):
        model = DirectoryModel(watch=True)
        relay = _WatcherQtRelay()
        relay.dirty.connect(model.notify_external_changes, Qt.QueuedConnection)
        watcher = ProjectFsWatcher(
            str(self.root),
            lambda events, overflowed: relay.dirty.emit(
                filesystem_map.sync_external_paths(str(self.root), [p for p, _ in events], overflowed=overflowed)
            ),
            debounce_sec=0.05,
        )
        try:
            model.load(str(self.root))
            self._wait(lambda: not model.busy)
            sys_path = str(self.root / "system")
            etc_path = str(self.root / "system" / "etc")
            sys_node = model._nodes[sys_path]
            model._scan(sys_node)
            self._wait(lambda: sys_node.loaded and not model.busy)
            etc_node = model._nodes[etc_path]
            model._scan(etc_node)
            self._wait(lambda: etc_node.loaded and not model.busy)

            self.assertTrue(watcher.start())
            (Path(etc_path) / "external_sync.prop").write_text("sync=ok\n", encoding="utf-8")
            self._wait(lambda: any(c.texts[0] == "external_sync.prop" for c in etc_node.children), timeout=5.0)
            self.assertEqual(model.error, "")
        finally:
            watcher.stop()
            model.close()
            relay.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)


if __name__ == "__main__":
    unittest.main()
