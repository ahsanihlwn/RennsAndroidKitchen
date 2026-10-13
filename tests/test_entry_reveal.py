# SPDX-License-Identifier: MPL-2.0
"""Entry fade pixels, live-tree repaint isolation, deferred icons and teardown."""
import os
import time
import ast
from pathlib import Path
import types
import unittest
from unittest.mock import patch, Mock
from shiboken6 import isValid

_NATIVE = os.environ.get("RAK_TEST_SKIA_GPU") == "1"
if not _NATIVE:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt, QEvent, QRect, QCoreApplication, QObject, QSignalBlocker, QPersistentModelIndex, QTimer, QPropertyAnimation, QEasingCurve, Signal, Slot
from PySide6.QtGui import QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget, QVBoxLayout, QTreeWidgetItem, QLabel

from app_ext.class_StartupVisual import StartupContentReveal
from app_ext.class_WindowContentReveal import WindowContentReveal
from core.skia_gpu import gpu_available
from main_ext.class_FileTree import FileTree
from main_ext.class_NameBadgeDelegate import IS_FOLDER_ROLE, SIZE_LABEL_ROLE
from main_ext.FileTree_ext.DirectoryLoading import ICON_PENDING_ROLE, ENTRY_SOURCE_ROLE, ENTRY_FINGERPRINT_ROLE


def isolated_method(filename, class_name, method_name):
    """Run production handoff methods without app.py's global IO/crash hooks."""
    path = Path(__file__).resolve().parent.parent / filename
    source = ast.parse(path.read_text(encoding="utf-8"))
    cls = next(node for node in source.body if isinstance(node, ast.ClassDef) and node.name == class_name)
    method = next(node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == method_name)
    namespace = {"StartupContentReveal": StartupContentReveal, "QTimer": QTimer,
                 "WindowContentReveal": WindowContentReveal, "Slot": Slot,
                 "QPropertyAnimation": QPropertyAnimation, "QEasingCurve": QEasingCurve,
                 "log_project_event": Mock()}
    exec(compile(ast.Module(body=[method], type_ignores=[]), str(path), "exec"), namespace)
    return namespace[method_name]


class PaintRecorder(QObject):
    def __init__(self):
        super().__init__()
        self.count = 0

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Paint:
            self.count += 1
        return False


class DeferredFrame(QWidget):
    frameSwapped = Signal()
    error = None

    def shutdown(self):
        pass


class EntryRevealTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)
        from core.fonts import apply_app_font
        apply_app_font(cls.app)

    def setUp(self):
        self.root = QWidget()
        self.root.setAttribute(Qt.WA_DontShowOnScreen)
        self.root.resize(481, 321)
        self.root.setStyleSheet("background-color:#12151b;color:#cdd6e2;")
        layout = QVBoxLayout(self.root)
        layout.addWidget(QLabel("Native Qt: cafe \u0627\u0644\u0639\u0631\u0628\u064a\u0629", self.root))
        self.tree = FileTree()
        self.tree.base = "mount://entry-test"
        layout.addWidget(self.tree)
        self.items = []
        with QSignalBlocker(self.tree):
            for number in range(1000):
                item = QTreeWidgetItem([f"Qt_file_{number}.img", "u:object_r:system_file:s0", "rw-r--r--"])
                path = self.tree.base + f"/{number}.img"
                item.setData(0, Qt.UserRole, path)
                item.setData(0, IS_FOLDER_ROLE, False)
                item.setData(0, SIZE_LABEL_ROLE, "340.0 MB")
                item.setIcon(0, self.tree.icon_file)
                self.items.append(item)
            self.tree.addTopLevelItems(self.items)
        self.root.show()
        self.app.processEvents()
        self.reveal = None

    def tearDown(self):
        if self.reveal is not None:
            self.reveal.stop()
        if isValid(self.tree):
            self.tree.stop_background_work()
        if isValid(self.root):
            self.root.hide()
            self.root.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        self.app.processEvents()

    def frozen_reveal(self, native=False):
        self.reveal = StartupContentReveal(self.root, cover_until_start=True, freeze_content=True)
        if native:
            # This fixture has no Qt Quick/WebEngine child; test the direct
            # renderer independently from the application's backend choice.
            with patch.object(self.app, "_skia_direct_composition_allowed", True, create=True):
                self.reveal.prepare_snapshot()
        else:
            with patch("core.skia_gpu.gpu_available", return_value=False):
                self.reveal.prepare_snapshot()
        return self.reveal

    def test_frozen_fade_does_not_repaint_live_tree_even_when_rows_change(self):
        reveal = self.frozen_reveal()
        snapshot = QImage(reveal._snapshot)
        recorder = PaintRecorder()
        self.tree.viewport().installEventFilter(recorder)
        reveal.start()
        self.items[0].setText(0, "Changed behind snapshot")
        QTest.qWait(130)
        self.assertEqual(recorder.count, 0)
        self.assertEqual(reveal._snapshot, snapshot)
        reveal.stop()
        self.app.processEvents()
        self.assertGreater(recorder.count, 0)
        self.assertEqual(self.items[0].text(0), "Changed behind snapshot")

    def test_full_reveal_preserves_exact_qt_snapshot_pixels(self):
        reveal = self.frozen_reveal()
        with patch("core.skia_gpu.render_gpu", return_value=None):
            image = reveal._compose_snapshot(reveal._bands(), reveal._FIRST_DELAY + reveal._DURATION + 1)
        self.assertEqual(image, reveal._snapshot)

    def test_partial_native_fallback_keeps_exact_full_frame_font_and_blend_pixels(self):
        reveal = self.frozen_reveal()
        self.assert_partial_pixels(reveal, gpu=False)

    def assert_partial_pixels(self, reveal, gpu):
        def check():
            ratio = reveal.devicePixelRatioF()
            for elapsed, top in ((.14, 11), (.27, 79), (.46, 201)):
                full = reveal._compose_snapshot(reveal._bands(), elapsed)
                damage = QRect(0, top, reveal.width(), 63)
                tile = reveal._compose_snapshot(reveal._bands(), elapsed, damage)
                import math
                physical_top = math.floor(top * ratio)
                physical_bottom = math.ceil((top + damage.height()) * ratio)
                expected = full.copy(0, physical_top, tile.width(), physical_bottom - physical_top)
                self.assertEqual(tile, expected)
        if gpu:
            check()
        else:
            with patch("core.skia_gpu.render_gpu", return_value=None):
                check()

    def test_covered_frame_hides_qt_content_before_start(self):
        reveal = self.frozen_reveal()
        with patch("core.skia_gpu.render_gpu", return_value=None):
            reveal._snapshot_frame_key = None
            image = reveal._compose_snapshot(reveal._bands(), None)
        for x, y in ((0, 0), (20, 50), (480, 320)):
            self.assertEqual(image.pixelColor(round(x * image.devicePixelRatio()), round(y * image.devicePixelRatio())), reveal._background)

    def test_resize_releases_freeze_and_finishes_once(self):
        reveal = self.frozen_reveal()
        finished = []
        reveal.finished.connect(lambda: finished.append(True))
        reveal.start()
        self.root.resize(521, 351)
        self.app.processEvents()
        reveal.stop()
        self.assertEqual(len(finished), 1)
        self.assertIsNone(reveal._snapshot)
        self.assertTrue(not isValid(reveal._timer) or not reveal._timer.isActive())

    def test_dpr_change_releases_snapshot_instead_of_resampling_qt_text(self):
        reveal = self.frozen_reveal()
        finished = []
        reveal.finished.connect(lambda: finished.append(True))
        reveal.start()
        QApplication.sendEvent(self.root, QEvent(QEvent.DevicePixelRatioChange))
        self.assertEqual(len(finished), 1)
        self.assertIsNone(reveal._snapshot)

    def test_snapshot_preparation_is_idempotent(self):
        reveal = self.frozen_reveal()
        original = reveal._snapshot.cacheKey()
        self.items[0].setText(0, "Must not recapture during fade")
        reveal.prepare_snapshot()
        self.assertEqual(reveal._snapshot.cacheKey(), original)

    def test_native_fade_clock_waits_for_first_presented_frame(self):
        reveal = self.frozen_reveal()
        frame = DeferredFrame(reveal)
        reveal._gpu_frame = frame
        frame.frameSwapped.connect(reveal._on_snapshot_presented)
        with patch.object(self.root, "testAttribute", return_value=False):
            reveal.start()
        self.assertIsNone(reveal._started_at)
        self.assertFalse(reveal._timer.isActive())
        self.assertTrue(reveal._presentation_timer.isActive())
        with patch.object(self.root, "windowHandle", return_value=types.SimpleNamespace(isExposed=lambda: True)):
            frame.frameSwapped.emit()
        self.assertIsNotNone(reveal._started_at)
        self.assertTrue(reveal._timer.isActive())
        self.assertFalse(reveal._presentation_timer.isActive())

    def test_missing_native_presentation_falls_back_instead_of_stalling_handoff(self):
        reveal = self.frozen_reveal()
        reveal._gpu_frame = DeferredFrame(reveal)
        reveal._presentation_timer.setInterval(20)
        with patch.object(self.root, "testAttribute", return_value=False):
            reveal.start()
        QTest.qWait(70)
        self.assertIsNone(reveal._gpu_frame)
        self.assertIsNotNone(reveal._started_at)
        self.assertTrue(reveal._timer.isActive())
        self.assertIsNotNone(reveal._snapshot)

    def test_early_stop_is_idempotent_and_cancels_timer_without_completion(self):
        reveal = self.frozen_reveal()
        finished = []
        reveal.finished.connect(lambda: finished.append(True))
        reveal.start()
        reveal.stop()
        reveal.stop()
        reveal.start()
        self.assertFalse(reveal._timer.isActive())
        self.assertIsNone(reveal._snapshot)
        self.assertFalse(finished)

    def test_excluded_live_widgets_keep_original_transparent_reveal(self):
        self.reveal = StartupContentReveal(self.root, excluded_widgets=(self.tree,), freeze_content=True)
        self.reveal.prepare_snapshot()
        self.assertIsNone(self.reveal._snapshot)
        self.assertFalse(self.reveal.testAttribute(Qt.WA_OpaquePaintEvent))

    def queue_icon(self, item, symlink=False):
        path = item.data(0, Qt.UserRole)
        item.setData(0, ICON_PENDING_ROLE, True)
        item.setData(0, ENTRY_SOURCE_ROLE, path)
        item.setData(0, ENTRY_FINGERPRINT_ROLE, "fp")
        task = (QPersistentModelIndex(self.tree.indexFromItem(item)), self.tree._icon_generation,
                self.tree.base, path, "fp", {"path": path, "symlink": {"is_dir": False} if symlink else None})
        (self.tree._yellow_icon_queue if symlink else self.tree._icon_queue).append(task)

    def test_icon_queue_pauses_without_losing_tasks_then_resumes(self):
        self.queue_icon(self.items[0])
        self.tree._start_icon_drain()
        self.tree.set_entry_transition_active(True)
        self.assertFalse(self.tree._icon_timer.isActive())
        self.tree._drain_icon_tasks()
        self.tree._acknowledge_paint_barrier()
        self.assertEqual(len(self.tree._icon_queue), 1)
        self.assertTrue(self.items[0].data(0, ICON_PENDING_ROLE))
        self.tree.set_entry_transition_active(False)
        QTest.qWait(60)
        self.assertFalse(self.tree._icon_queue)
        self.assertFalse(self.items[0].data(0, ICON_PENDING_ROLE))

    def test_yellow_icons_resume_after_first_live_paint_and_reset_discards_stale_tasks(self):
        self.queue_icon(self.items[0], symlink=True)
        self.tree._paint_barrier_pending = True
        self.tree.set_entry_transition_active(True)
        self.tree.set_entry_transition_active(False)
        QTest.qWait(60)
        self.assertFalse(self.tree._yellow_icon_queue)
        self.assertFalse(self.items[0].data(0, ICON_PENDING_ROLE))
        self.queue_icon(self.items[1])
        self.tree.set_entry_transition_active(True)
        self.tree._reset_icon_work()
        self.tree.set_entry_transition_active(False)
        self.assertFalse(self.tree._icon_queue)
        self.assertFalse(self.tree._icon_timer.isActive())

    def test_close_during_entry_never_restarts_background_work(self):
        self.queue_icon(self.items[0])
        self.tree.set_entry_transition_active(True)
        self.tree.stop_background_work()
        self.tree.set_entry_transition_active(False)
        self.assertFalse(self.tree._icon_timer.isActive())
        self.assertFalse(self.tree._icon_queue)

    def handoff_host(self, loader=None):
        self.root.hide()
        self.root.tree = self.tree
        self.root.tree_mirror = self.root.tree2 = self.root.p2 = None
        self.root._pending_prescan_result = None
        self.root._preparation_aborted = False
        self.root._closing = False
        self.root._entry_content_reveal = None
        for name in ("_get_active_trees", "_begin_entry_transition", "_complete_entry_transition"):
            setattr(self.root, name, types.MethodType(isolated_method("main.py", "Main", name), self.root))
        return types.SimpleNamespace(editor=self.root, loader=loader, _log_project_entered=Mock())

    def wait_until(self, predicate, timeout=3000):
        deadline = time.monotonic() + timeout / 1000
        while not predicate() and time.monotonic() < deadline:
            QTest.qWait(10)
        self.assertTrue(predicate())

    def test_production_handoff_without_loader_releases_icons_and_restores_opacity(self):
        self.queue_icon(self.items[0])
        host = self.handoff_host()
        handoff = isolated_method("app.py", "AppUI", "_show_editor_with_reveal")
        with patch("core.skia_gpu.gpu_available", return_value=False):
            handoff(host, "mount://test", time.perf_counter(), "existing")
        self.reveal = self.root._entry_content_reveal
        self.assertIsInstance(self.reveal, WindowContentReveal)
        self.assertTrue(self.root._entry_transition_active)
        self.assertTrue(self.tree._entry_transition_active)
        self.wait_until(lambda: self.root._entry_content_reveal is None)
        self.wait_until(lambda: not self.tree._icon_queue)
        self.assertEqual(self.root.windowOpacity(), 1.0)
        self.assertIsNone(self.root._entry_content_reveal)
        self.assertFalse(self.tree._entry_transition_active)
        self.assertFalse(self.tree._icon_queue)
        host._log_project_entered.assert_called_once()

    def test_production_handoff_paints_main_before_fading_loader(self):
        from app_ext.class_LoadingWindow import LoadingWindow
        loader = LoadingWindow()
        loader.setAttribute(Qt.WA_DontShowOnScreen)
        loader.show()
        host = self.handoff_host(loader)
        handoff = isolated_method("app.py", "AppUI", "_show_editor_with_reveal")
        with patch("core.skia_gpu.gpu_available", return_value=False):
            handoff(host, "mount://test", time.perf_counter(), "existing")
        self.reveal = self.root._entry_content_reveal
        self.assertIsNone(self.reveal._started_at)
        self.assertTrue(loader._block_close)
        self.assertTrue(self.root.isVisible())
        self.assertEqual(self.root.windowOpacity(), 0.0)
        self.assertFalse(loader._handoff_fade.state() == QPropertyAnimation.Running)
        self.wait_until(lambda: host.loader is None)
        self.assertIsNone(host.loader)
        self.assertTrue(self.root.isVisible())
        self.assertTrue(self.tree._entry_transition_active)
        self.reveal.stop()
        self.root._complete_entry_transition()

    def test_loader_dismissal_during_handoff_does_not_reopen_main(self):
        from app_ext.class_LoadingWindow import LoadingWindow
        loader = LoadingWindow()
        loader.setAttribute(Qt.WA_DontShowOnScreen)
        loader.show()
        host = self.handoff_host(loader)
        handoff = isolated_method("app.py", "AppUI", "_show_editor_with_reveal")
        with patch("core.skia_gpu.gpu_available", return_value=False):
            handoff(host, "mount://test", time.perf_counter(), "existing")
        self.reveal = self.root._entry_content_reveal
        production_cancel = isolated_method("app.py", "AppUI", "_cancel_project_preparation")
        def cancel():
            self.root._closing = True
            self.reveal.stop()
            self.root._entry_content_reveal = None
            self.root._complete_entry_transition()
            production_cancel(host)
        loader._on_hide_requested = cancel
        loader.close()
        QTest.qWait(240)
        self.assertIsNone(host.loader)
        self.assertFalse(isValid(self.root))
        self.assertIsNone(host.editor)
        host._log_project_entered.assert_not_called()

    def test_native_window_fade_does_not_repaint_tree_or_read_back_gpu(self):
        from core.skia_gpu import gpu_backend_status
        self.reveal = WindowContentReveal(self.root)
        self.reveal.prepare()
        self.wait_until(lambda: self.reveal._prepared)
        recorder = PaintRecorder()
        self.tree.viewport().installEventFilter(recorder)
        frames = gpu_backend_status().get("frames", 0)
        done = []
        self.reveal.finished.connect(lambda: done.append(True))
        self.reveal.start()
        self.wait_until(lambda: bool(done))
        self.assertEqual(recorder.count, 0)
        self.assertEqual(gpu_backend_status().get("frames", 0), frames)
        self.assertEqual(self.root.windowOpacity(), 1.0)

    def test_window_fade_disabled_animation_and_cancel_before_prepared(self):
        from main_ext.class_ThemeUi import theme
        self.reveal = WindowContentReveal(self.root)
        prepared = []
        self.reveal.prepared.connect(lambda: prepared.append(True))
        self.reveal.prepare()
        self.reveal.stop()
        QTest.qWait(100)
        self.assertFalse(prepared)
        self.assertFalse(self.root.isVisible())
        self.assertEqual(self.root.windowOpacity(), 1.0)
        self.reveal = WindowContentReveal(self.root)
        self.reveal.prepare()
        self.wait_until(lambda: self.reveal._prepared)
        done = []
        self.reveal.finished.connect(lambda: done.append(True))
        with patch.object(theme, "_animations_enabled", False):
            self.reveal.start()
        self.assertTrue(done)
        self.assertEqual(self.root.windowOpacity(), 1.0)

    @unittest.skipUnless(_NATIVE, "requires native Qt OpenGL framebuffer")
    def test_partial_gpu_frames_keep_exact_full_frame_qt_font_and_blend_pixels(self):
        self.assertTrue(gpu_available())
        self.assert_partial_pixels(self.frozen_reveal(), gpu=True)

    @unittest.skipUnless(_NATIVE, "requires native Qt OpenGL framebuffer")
    def test_direct_context_failure_uses_frozen_fallback_and_keeps_qt_pixels(self):
        self.assertTrue(gpu_available())
        with patch("core.skia_snapshot.skia.GrDirectContext.MakeGL", return_value=None):
            reveal = self.frozen_reveal(native=True)
        self.assertIsNone(reveal._gpu_frame)
        image = reveal._compose_snapshot(reveal._bands(), 2)
        self.assertEqual(image.copy(0, 0, reveal._snapshot.width(), reveal._snapshot.height()), reveal._snapshot)
        reveal.start()
        QTest.qWait(50)
        self.assertFalse(reveal._stopped)

    @unittest.skipUnless(_NATIVE, "requires native Qt OpenGL framebuffer")
    def test_direct_gpu_fade_preserves_qt_pixels_without_recapture_or_readback(self):
        if not gpu_available():
            self.fail("Native GPU test requires an available Skia context")
        reveal = self.frozen_reveal(native=True)
        frame = reveal._gpu_frame
        self.assertIsNotNone(frame)
        self.assertIsNone(frame.error)
        self.assertGreater(frame.frames, 0)
        snapshot = QImage(reveal._snapshot)
        recorder = PaintRecorder()
        self.tree.viewport().installEventFilter(recorder)
        with patch("core.skia_gpu.render_gpu", side_effect=AssertionError("fade must present directly")):
            reveal.start()
            QTest.qWait(130)
        self.assertEqual(recorder.count, 0)
        self.assertGreater(frame.frames, 2)
        self.assertIsNone(frame.error)
        reveal._timer.stop()
        reveal._started_at = time.monotonic() - 2
        final = frame.grabFramebuffer().convertToFormat(snapshot.format())
        self.assertEqual(final, snapshot)
        reveal.stop()
        self.assertIsNone(frame._gpu)


if __name__ == "__main__":
    unittest.main()
