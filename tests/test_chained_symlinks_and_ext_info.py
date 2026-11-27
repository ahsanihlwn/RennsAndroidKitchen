# SPDX-License-Identifier: MPL-2.0
"""Tests for chained/intermediate symlink resolution across all layers and EXT early _info + preview."""
import os
from pathlib import Path
import tempfile
import threading
import unittest

from PySide6.QtCore import QCoreApplication, QEvent

from core import filesystem_map, fsdetect, native_backend, symlink
from core.directory_scan import scan_tree_directory
from core.make_symlink import make_symlink, make_symlink_bytes
from core.mount_index import MountIndex, _build_child_index
from core.workspace_files import WorkspaceFiles
from ui.mounts import WorkspaceMounts
from ui.workspace_cli import headless_workspace


class ChainedSymlinkResolutionTests(unittest.TestCase):
    def setUp(self):
        filesystem_map.clear_memory_cache()
        symlink.clear_symlink_cache()
        self.tmp = tempfile.TemporaryDirectory(prefix="rak-chain-sym-")
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(filesystem_map.clear_memory_cache)
        self.addCleanup(symlink.clear_symlink_cache)
        self.root = Path(self.tmp.name)

        # Build the exact scenario from user request:
        #   system/odm/app -> /vendor/odm/app
        #   vendor/odm     -> /odm
        #   odm/app/App.apk (real folder + APK)
        (self.root / "system" / "odm").mkdir(parents=True)
        (self.root / "vendor").mkdir(parents=True)
        (self.root / "odm" / "app").mkdir(parents=True)
        (self.root / "odm" / "app" / "App.apk").write_bytes(b"PK_DUMMY_APK")

        make_symlink(str(self.root / "vendor" / "odm"), "/odm", overwrite=True)
        make_symlink(str(self.root / "system" / "odm" / "app"), "/vendor/odm/app", overwrite=True)

        # Direct symlink-to-symlink folder: system/link_a -> /vendor/link_b -> /odm/app
        make_symlink(str(self.root / "vendor" / "link_b"), "/odm/app", overwrite=True)
        make_symlink(str(self.root / "system" / "link_a"), "/vendor/link_b", overwrite=True)

        # Chained symlink to FILE: system/bin/sh -> /vendor/bin/toolbox,
        # vendor/bin -> /odm/bin, odm/bin/toolbox -> toybox, odm/bin/toybox (real file)
        (self.root / "system" / "bin").mkdir(parents=True)
        (self.root / "odm" / "bin").mkdir(parents=True)
        (self.root / "odm" / "bin" / "toybox").write_bytes(b"ELF" * 300)
        make_symlink(str(self.root / "odm" / "bin" / "toolbox"), "toybox", overwrite=True)
        make_symlink(str(self.root / "vendor" / "bin"), "/odm/bin", overwrite=True)
        make_symlink(str(self.root / "system" / "bin" / "sh"), "/vendor/bin/toolbox", overwrite=True)

        # Symlink cycle must terminate cleanly with exists=False
        make_symlink(str(self.root / "system" / "loop_a"), "/vendor/loop_b", overwrite=True)
        make_symlink(str(self.root / "vendor" / "loop_b"), "/system/loop_a", overwrite=True)

    def test_python_symlink_and_filesystem_map_and_directory_scan_and_project_view(self):
        app_link = str(self.root / "system" / "odm" / "app")
        expected_app = os.path.normpath(str(self.root / "odm" / "app"))
        sh_link = str(self.root / "system" / "bin" / "sh")
        expected_toybox = os.path.normpath(str(self.root / "odm" / "bin" / "toybox"))

        # 1. core.symlink.check_symlink
        sym_app = symlink.check_symlink(app_link, str(self.root), target_cache={})
        self.assertIsNotNone(sym_app)
        self.assertTrue(sym_app["exists"])
        self.assertTrue(sym_app["is_dir"])
        self.assertFalse(sym_app["is_file"])
        self.assertEqual(os.path.normpath(sym_app["resolved"]), expected_app)

        sym_direct = symlink.check_symlink(str(self.root / "system" / "link_a"), str(self.root), target_cache={})
        self.assertTrue(sym_direct["exists"])
        self.assertTrue(sym_direct["is_dir"])
        self.assertEqual(os.path.normpath(sym_direct["resolved"]), expected_app)

        sym_sh = symlink.check_symlink(sh_link, str(self.root), target_cache={})
        self.assertTrue(sym_sh["exists"])
        self.assertTrue(sym_sh["is_file"])
        self.assertFalse(sym_sh["is_dir"])
        self.assertEqual(os.path.normpath(sym_sh["resolved"]), expected_toybox)

        sym_loop = symlink.check_symlink(str(self.root / "system" / "loop_a"), str(self.root), target_cache={})
        self.assertIsNotNone(sym_loop)
        self.assertFalse(sym_loop["exists"])

        # 2. core.filesystem_map + core.directory_scan (entering the symlink opens odm/app/App.apk)
        sys_odm_dir = str(self.root / "system" / "odm")
        rows = filesystem_map.scan_and_revalidate_dir(str(self.root), sys_odm_dir, sys_odm_dir)
        app_row = next(r for r in rows if r["name"] == "app")
        self.assertTrue(app_row["symlink"]["exists"])
        self.assertTrue(app_row["symlink"]["is_dir"])
        self.assertEqual(os.path.normpath(app_row["symlink"]["resolved"]), expected_app)

        ok, opened = scan_tree_directory({
            "base": str(self.root),
            "path": app_link,
            "target": app_row["symlink"]["resolved"],
            "folder_symlink": True,
            "show_apktool_dec": False,
        })
        self.assertTrue(ok)
        self.assertEqual([e["name"] for e in opened["entries"]], ["App.apk"])

        # 3. core.rak_neobranch_view (WorkspaceFiles.view) resolves through intermediate symlink
        files = WorkspaceFiles(str(self.root))
        resolved_apk, _ = files.view.resolve_following(str(self.root / "system" / "odm" / "app" / "App.apk"))
        self.assertEqual(os.path.normpath(resolved_apk), os.path.normpath(str(self.root / "odm" / "app" / "App.apk")))

        # 4. filesystem_map.sync_external_paths fan-out when intermediate hop `vendor/odm` is deleted!
        filesystem_map.scan_and_revalidate_dir(str(self.root), str(self.root / "vendor"), str(self.root / "vendor"))
        vendor_odm_path = self.root / "vendor" / "odm"
        vendor_odm_path.unlink()
        dirty = filesystem_map.sync_external_paths(str(self.root), [str(vendor_odm_path)])
        self.assertIn(os.path.normcase(os.path.normpath(sys_odm_dir)), {os.path.normcase(os.path.normpath(d)) for d in dirty})
        peek_broken = filesystem_map.peek_cached_rows(str(self.root), sys_odm_dir, sys_odm_dir)
        self.assertFalse(next(r for r in peek_broken if r["name"] == "app")["symlink"]["exists"])

    def test_rust_native_directory_and_resolve_symlinks_chain(self):
        sys_odm_dir = str(self.root / "system" / "odm")
        expected_app = os.path.normpath(str(self.root / "odm" / "app"))
        expected_toybox = os.path.normpath(str(self.root / "odm" / "bin" / "toybox"))

        entries = native_backend.directory_entries(sys_odm_dir, str(self.root), include_magic=False)
        app_entry = next(e for e in entries if e["name"] == "app")
        self.assertTrue(app_entry["symlink"]["exists"])
        self.assertTrue(app_entry["symlink"]["is_dir"])
        self.assertFalse(app_entry["symlink"]["is_file"])
        self.assertEqual(os.path.normpath(app_entry["symlink"]["resolved"]), expected_app)

        batch = native_backend.resolve_symlinks(
            [
                str(self.root / "system" / "odm" / "app"),
                str(self.root / "system" / "link_a"),
                str(self.root / "system" / "bin" / "sh"),
                str(self.root / "system" / "loop_a"),
            ],
            str(self.root),
        )
        self.assertTrue(batch[os.path.normpath(str(self.root / "system" / "odm" / "app"))]["is_dir"])
        self.assertTrue(batch[os.path.normpath(str(self.root / "system" / "link_a"))]["is_dir"])
        sh_res = batch[os.path.normpath(str(self.root / "system" / "bin" / "sh"))]
        self.assertTrue(sh_res["exists"])
        self.assertTrue(sh_res["is_file"])
        self.assertEqual(os.path.normpath(sh_res["resolved"]), expected_toybox)
        self.assertFalse(batch[os.path.normpath(str(self.root / "system" / "loop_a"))]["exists"])

    def test_mount_index_chained_symlinks_across_mounts_and_disk(self):
        sys_img = str(self.root / "system.img")
        vnd_img = str(self.root / "vendor.img")
        odm_img = str(self.root / "odm.img")

        # Scenario A: system.img mounted -> /vendor/odm/app -> disk intermediate vendor/odm -> disk odm/app
        idx = MountIndex(str(self.root))
        idx.indexes[sys_img] = _build_child_index([
            {"path": "odm", "is_dir": True, "is_link": False, "size": 0},
            {"path": "odm/app", "is_dir": False, "is_link": True, "target": "/vendor/odm/app", "size": 15},
        ])
        v, p, is_d, ex = idx._resolve_symlink_target_for_mount(sys_img, "odm/app", "/vendor/odm/app")
        self.assertIsNone(v)
        self.assertTrue(ex)
        self.assertTrue(is_d)
        self.assertEqual(os.path.normpath(p), os.path.normpath(str(self.root / "odm" / "app")))

        # Scenario B: system.img + vendor.img + odm.img ALL mounted (without disk odm)
        import shutil
        shutil.rmtree(self.root / "odm")
        shutil.rmtree(self.root / "vendor")
        idx.indexes[vnd_img] = _build_child_index([
            {"path": "odm", "is_dir": False, "is_link": True, "target": "/odm", "size": 4},
        ])
        idx.indexes[odm_img] = _build_child_index([
            {"path": "app", "is_dir": True, "is_link": False, "size": 0},
            {"path": "app/App.apk", "is_dir": False, "is_link": False, "size": 123},
        ])
        v2, p2, is_d2, ex2 = idx._resolve_symlink_target_for_mount(sys_img, "odm/app", "/vendor/odm/app")
        self.assertIsNone(p2)
        self.assertTrue(ex2)
        self.assertTrue(is_d2)
        self.assertEqual(v2, (os.path.normpath(odm_img), "app"))


class ExtEarlyInfoAndPreviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QCoreApplication.instance() or QCoreApplication([])

    def setUp(self):
        filesystem_map.clear_memory_cache()
        self.tmp = tempfile.TemporaryDirectory(prefix="rak-ext-preview-")
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(filesystem_map.clear_memory_cache)
        self.root = Path(self.tmp.name)

    def test_ext4_unpack_writes_info_before_extraction_and_preview_has_ext4_badge_and_fsmap(self):
        src = self.root / "_src_vendor"
        (src / "etc").mkdir(parents=True)
        for i in range(12):
            (src / "etc" / f"f_{i:02d}.prop").write_text(f"ro.idx={i}\n" * 40, encoding="utf-8")
        make_symlink(str(src / "bin_link"), "/odm/bin", overwrite=True)

        img = self.root / "vendor.img"
        native_backend.ext_make_fs(str(img), str(src), size_bytes=16 * 1024 * 1024, mount_point="/vendor")

        # 1. Instant preview payload detects exact EXT4 variant + child index
        payload = WorkspaceMounts.build_preview_payload(str(img))
        self.assertIsNotNone(payload)
        child_index, kind, _ = payload
        self.assertEqual(kind, "EXT4")
        self.assertIn("etc", child_index)
        self.assertTrue(any(c["name"] == "bin_link" and c["is_link"] for c in child_index[""]))

        # 2. EXT unpack writes config/vendor_info BEFORE file extraction finishes (< 90%)
        seen_early_info = []
        info_path = self.root / "config" / "vendor_info"

        def on_progress(pct, stage):
            if "Extracting EXT files" in stage and int(pct) < 90:
                seen_early_info.append((int(pct), info_path.is_file(), fsdetect.detect_fs_root(str(self.root), "vendor")))

        native_backend.ext_unpack(str(img), progress=on_progress)
        self.assertTrue(seen_early_info, "expected Extracting EXT files progress updates")
        self.assertTrue(all(exists and fmt == "EXT4" for _, exists, fmt in seen_early_info), seen_early_info)

        # 3. Preview end seeds filesystem_map cleanly for EXT4
        session = headless_workspace(str(self.root))
        mounts = WorkspaceMounts(session)
        session.mounts = mounts
        try:
            target_dir = str(self.root / "vendor")
            self.assertTrue(mounts.begin_unpack_preview(str(img), target_dir, str(self.root), preloaded=payload))
            mounts.end_unpack_preview(target_dir, ok=True, refresh_views=False)
            pmap = filesystem_map.get_partition_map(str(self.root), "vendor")
            self.assertIsNotNone(pmap)
            self.assertEqual(pmap.fs_type, "EXT4")
            self.assertIn("etc", pmap.dirs)
        finally:
            mounts.close()
            session.stop()
            session.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)


if __name__ == "__main__":
    unittest.main()
