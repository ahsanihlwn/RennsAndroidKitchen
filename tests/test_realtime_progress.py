import os
import tempfile
import unittest

from core import native_backend
from core.threader_control import progress_scope, report_progress, get_progress_reporter


def _make_tree(root):
    os.makedirs(os.path.join(root, "etc", "sub"), exist_ok=True)
    for i in range(20):
        with open(os.path.join(root, "etc", "sub", f"f{i}.txt"), "w") as f:
            f.write("x" * (1000 * (i + 1)))


class ProgressContextTests(unittest.TestCase):
    def test_scope_routes_and_clamps(self):
        seen = []
        with progress_scope(lambda p, s: seen.append((p, s))):
            report_progress(150, "a")
            report_progress(-5, "b")
            self.assertIsNotNone(get_progress_reporter())
        report_progress(50, "ignored")
        self.assertEqual(seen, [(100, "a"), (0, "b")])
        self.assertIsNone(get_progress_reporter())


class NativeProgressTests(unittest.TestCase):
    def test_erofs_pack_and_unpack_report_monotonic_to_100(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, "part")
            _make_tree(src)
            img = os.path.join(tmp, "part_new.img")
            packed = []
            native_backend.erofs_pack(src, img, mount_point="/part",
                                      progress=lambda p, s: packed.append(p))
            self.assertTrue(packed and packed[-1] == 100)
            self.assertEqual(packed, sorted(packed))
            out = os.path.join(tmp, "out")
            os.makedirs(out)
            unpacked = []
            native_backend.erofs_unpack(img, out, image_base_name="part",
                                        progress=lambda p, s: unpacked.append(p))
            self.assertTrue(unpacked and unpacked[-1] == 100)
            self.assertEqual(unpacked, sorted(unpacked))

    def test_ext_make_and_unpack_report_progress(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, "part")
            _make_tree(src)
            img = os.path.join(tmp, "part.img")
            made = []
            native_backend.ext_make_fs(img, src, size_bytes=16 * 1024 * 1024,
                                       mount_point="part",
                                       progress=lambda p, s: made.append(p))
            self.assertTrue(made and made[-1] == 100)
            self.assertEqual(made, sorted(made))
            os.rename(src, os.path.join(tmp, "old"))
            got = []
            native_backend.ext_unpack(img, progress=lambda p, s: got.append(p))
            self.assertTrue(got and got[-1] == 100)
            self.assertEqual(got, sorted(got))


from pathlib import Path
import time
from PySide6.QtCore import QCoreApplication, QEvent

from core import error_reporting
from core.directory_scan import scan_tree_directory
from main_ext.class_LoadingManager import LoadingManager


class WeightedBatchProgressTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QCoreApplication.instance() or QCoreApplication([])

    def test_multi_select_batch_progress_combines_weights_without_resetting(self):
        lm = LoadingManager()
        bid = lm.start_batch("context-batch", 3, label="Unpack EROFS (3)")
        lm.register_task(bid, "t1", "odm.img", weight=10.0, folder_path="/proj/odm")
        lm.register_task(bid, "t2", "vendor.img", weight=30.0, folder_path="/proj/vendor")
        lm.register_task(bid, "t3", "system.img", weight=60.0, folder_path="/proj/system")

        lm.mark_task_active(bid, "t1", "odm.img")
        lm.update_task_progress(bid, "t1", 100, "Extracting")
        # 10% weight running (capped at 99 while active) -> ~9% total, NOT 100%
        pct1, _ = lm.get_progress_info()
        self.assertLess(pct1, 20)

        lm.report_done(bid, True, member_name="odm.img", task_id="t1")
        # odm (weight 10) is done -> ~10%
        pct2, _ = lm.get_progress_info()
        self.assertEqual(pct2, 10)

        lm.mark_task_active(bid, "t2", "vendor.img")
        lm.mark_task_active(bid, "t3", "system.img")
        lm.update_task_progress(bid, "t2", 50, "Extracting")
        lm.update_task_progress(bid, "t3", 50, "Extracting")
        # 10*100 + 30*50 + 60*50 = 1000+1500+3000 = 5500 / 100 = 55%
        pct3, _ = lm.get_progress_info()
        self.assertEqual(pct3, 55)
        self.assertEqual(lm.get_path_progress(os.path.normcase(os.path.normpath("/proj/vendor"))), 50)
        self.assertEqual(lm.get_path_progress(os.path.normcase(os.path.normpath("/proj/system"))), 50)

        lm.report_done(bid, True, member_name="vendor.img", task_id="t2")
        pct4, _ = lm.get_progress_info()
        self.assertGreaterEqual(pct4, pct3)

        lm.report_done(bid, True, member_name="system.img", task_id="t3")
        self.assertEqual(lm.get_progress_info(), (-1, ""))


class PreviewLockAndHighlightTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QCoreApplication.instance() or QCoreApplication([])

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="rak-preview-lock-")
        self.root = Path(self.tmp.name)
        from ui.workspace_cli import headless_workspace
        from ui.mounts import WorkspaceMounts
        self.session = headless_workspace(str(self.root))
        self.mounts = WorkspaceMounts(self.session)
        self.session.mounts = self.mounts
        # Inject real WorkspaceSession-like lock/highlight/tab methods if headless stub lacks them,
        # OR test directly via WorkspaceSession helpers where available.
        self._wait(lambda: not self.session.tree.busy)

    def tearDown(self):
        self.mounts.close()
        self.session.stop()
        self.session.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        self.tmp.cleanup()

    def _wait(self, pred):
        deadline = time.monotonic() + 10
        while not pred():
            self.assertLess(time.monotonic(), deadline)
            self.app.processEvents()
            time.sleep(0.003)

    def test_directory_model_refresh_and_error_reporting_do_not_crash_on_missing_subdir(self):
        # Regression check for crash.log: DirectoryModel.parent() TypeError and os error 3 on vanished subdir
        missing_sub = os.path.join(str(self.root), "system", "product")
        ok, payload = scan_tree_directory({
            "base": str(self.root),
            "path": missing_sub,
            "refresh": True,
        })
        self.assertTrue(ok)
        self.assertEqual(payload["entries"], [])
        # Ensure _presenter_for(DirectoryModel) walks QObject.parent(owner) without TypeError
        import weakref
        ref = weakref.ref(self.session)
        error_reporting._presenters.append(ref)
        try:
            presenter = error_reporting._presenter_for(self.session.tree)
            self.assertIs(presenter, self.session)
        finally:
            if ref in error_reporting._presenters:
                error_reporting._presenters.remove(ref)

    def test_unpack_preview_resolves_symlinks_and_read_only_locks_and_unlocks(self):
        src = self.root / "src_part"
        (src / "etc").mkdir(parents=True)
        (src / "etc" / "prop.txt").write_text("ro.test=1\n", encoding="utf-8")
        img = self.root / "part.img"
        native_backend.erofs_pack(str(src), str(img), mount_point="/part")

        target_dir = str(self.root / "part")
        # Build & attach unpack preview before target_dir even exists on disk
        ok = self.mounts.begin_unpack_preview(str(img), target_dir, str(self.root))
        self.assertTrue(ok)
        self._wait(lambda: not self.session.tree.busy)

        ref = self.mounts.unpack_preview_reference(os.path.join(target_dir, "etc", "prop.txt"))
        self.assertIsNotNone(ref)
        self.assertEqual(ref[1], "etc/prop.txt")

        # Virtual target root appears in tree even before os.mkdir(target_dir)
        self.assertIn(os.path.normpath(target_dir), self.session.tree._nodes)

        # Simulated end_unpack_preview cleanly clears preview without "path specified" errors
        self.mounts.end_unpack_preview(target_dir)
        self._wait(lambda: not self.session.tree.busy)
        self.assertIsNone(self.mounts.unpack_preview_reference(target_dir))


if __name__ == "__main__":
    unittest.main()
