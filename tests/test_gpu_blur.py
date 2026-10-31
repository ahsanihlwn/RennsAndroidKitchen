# SPDX-License-Identifier: MPL-2.0
"""Native Gaussian backdrop pixels, DPR, caching and Automation handoff."""
import ast
import os
from pathlib import Path
import types
import unittest
from unittest.mock import patch

_NATIVE = os.environ.get("RAK_TEST_SKIA_GPU") == "1"
if not _NATIVE:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent, QPoint, QRect, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPixmap
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget, QLabel
from shiboken6 import isValid
from core import skia_gpu as gpu
from app_ext import class_BlurOverlay as blur
from main_ext.class_ThemeUi import theme
from main_ext.class_FsPickerDialog import FsPickerDialog
try:
    from rennsproject.main_ext.class_AutomationLogWindow import AutomationLogWindow
    from rennsproject.main_ext import class_RenxecPickerDialog as renxec
except ModuleNotFoundError:
    AutomationLogWindow = renxec = None


class PatternRoot(QWidget):
    flipped = False
    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#243040"))
        for y in range(0, self.height(), 24):
            for x in range(0, self.width(), 24):
                painter.fillRect(x, y, 24, 24,
                                 QColor("#daac6e" if bool((x // 24 + y // 24) % 2) != self.flipped else "#264987"))
        painter.end()


class BlurTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def setUp(self):
        blur._BLUR_CACHE.clear()
        blur._BLUR_CACHE_BYTES = 0
        self.root = PatternRoot()
        self.root.setAttribute(Qt.WA_DontShowOnScreen)
        self.root.setGeometry(120, 80, 1000, 720)
        self.root.show()
        self.windows = []
        self.patch_blur = patch.object(theme, "_blur_enabled", True)
        self.patch_blur.start()
        self.patch_level = patch.object(theme, "_effect_level", "medium")
        self.patch_level.start()
        self.app.processEvents()

    def tearDown(self):
        self.patch_blur.stop()
        self.patch_level.stop()
        for window in self.windows:
            if isValid(window):
                window.hide()
                window.deleteLater()
        self.root.hide()
        self.root.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        self.app.processEvents()

    def require_gpu(self):
        if not _NATIVE:
            self.skipTest("requires the native Skia OpenGL backend")
        self.assertTrue(gpu.gpu_available(), gpu.gpu_backend_status())

    @staticmethod
    def step(ratio=1):
        source = QPixmap(round(128 * ratio), round(80 * ratio))
        source.setDevicePixelRatio(ratio)
        source.fill(QColor("white"))
        painter = QPainter(source)
        painter.fillRect(64, 0, 64, 80, QColor("black"))
        painter.end()
        return source

    def test_native_shader_spreads_pixels_with_opaque_clamped_edges(self):
        self.require_gpu()
        before = gpu.gpu_backend_status()["blur_frames"]
        source = self.step()
        original = source.toImage()
        with patch.object(blur, "_qt_blur_pixmap", side_effect=AssertionError("CPU blur used")):
            result = blur.make_blur_pixmap(source, 18).toImage()
        self.assertEqual(gpu.gpu_backend_status()["blur_frames"], before + 1)
        self.assertEqual(source.toImage(), original)
        self.assertGreater(result.pixelColor(72, 40).red(), 10)
        self.assertLess(result.pixelColor(55, 40).red(), 245)
        for x in (0, 55, 63, 72, 127):
            self.assertEqual(result.pixelColor(x, 0).alpha(), 255)
        self.assertEqual(result.pixelColor(0, 40), QColor("white"))
        self.assertEqual(result.pixelColor(127, 40), QColor("black"))

    def test_native_gaussian_uses_logical_radius_at_fractional_dpr(self):
        self.require_gpu()
        samples = []
        for ratio in (1, 1.5, 2):
            source = self.step(ratio)
            result = gpu.blur_gpu(source, 18)
            self.assertEqual(result.size(), source.size())
            self.assertEqual(result.devicePixelRatioF(), ratio)
            samples.append(result.pixelColor(round(72 * ratio), round(40 * ratio)).red())
        self.assertLess(max(samples) - min(samples), 9, samples)

    def test_medium_and_high_keep_frozen_pixels_after_motion_source_changes_and_resize(self):
        self.require_gpu()
        for level in ("medium", "high"):
            with self.subTest(level=level):
                theme._effect_level = level
                card = blur.BlurCard(self.root, corner=14)
                self.windows.append(card)
                card.setGeometry(150, 160, 240, 180)
                card.show()
                with patch.object(blur, "make_blur_pixmap", wraps=blur.make_blur_pixmap) as compute:
                    card.freeze_from_widget(self.root, card.mapToGlobal(QPoint()))
                    original = card._blur_pix.cacheKey()
                    card.move(180, 190)
                    self.root.flipped = not self.root.flipped
                    self.root.update()
                    card.resize(280, 200)
                    QTest.qWait(180)
                    image = card.grab().toImage()
                    self.assertEqual(card._blur_pix.cacheKey(), original)
                    self.assertEqual(compute.call_count, 1)
                    self.assertEqual(image.pixelColor(0, 0).alpha(), 0)
                    self.assertEqual(image.pixelColor(image.width()//2, image.height()//2).alpha(), 255)
                    self.assertFalse(hasattr(card, "_live_backdrop"))
                    self.assertFalse(hasattr(card, "_quick_blur"))
                card.hide()

    def test_high_explicit_overlay_uses_a_frozen_backdrop(self):
        self.require_gpu()
        theme._effect_level = "high"
        with patch.object(blur, "make_blur_pixmap", wraps=blur.make_blur_pixmap) as compute:
            overlay = blur.BlurOverlay(self.root, animate=False, source_widget=self.root,
                                       backdrop="blur", sync_blur=True)
            self.windows.append(overlay)
            original = overlay._pix.cacheKey()
            self.assertTrue(overlay._ready)
            self.assertFalse(overlay._pix.isNull())
            self.root.flipped = True
            self.root.update()
            self.root.resize(1020, 740)
            QTest.qWait(100)
            overlay.grab()
            self.assertEqual(overlay._pix.cacheKey(), original)
            self.assertEqual(compute.call_count, 1)
            self.assertFalse(hasattr(overlay, "_live_backdrop"))

    def test_hot_level_switch_restores_static_blur_without_starting_capture(self):
        self.require_gpu()
        card = blur.BlurCard(self.root)
        self.windows.append(card)
        card.setGeometry(150, 160, 240, 180)
        card.show()
        card.freeze_from_widget(self.root, card.mapToGlobal(QPoint()))
        with patch.object(theme, "_save_settings"):
            theme.set_effect_level("low")
            self.assertTrue(card._blur_pix.isNull())
            theme.set_effect_level("high")
            self.assertFalse(card._blur_pix.isNull())
            self.assertFalse(theme.live_blur_enabled)
            original = card._blur_pix.cacheKey()
            self.root.flipped = True
            self.root.update()
            QTest.qWait(100)
            self.assertEqual(card._blur_pix.cacheKey(), original)
            theme.set_effect_level("medium")
            self.assertFalse(card._blur_pix.isNull())
            self.assertFalse(theme.live_blur_enabled)

    def test_high_static_keeps_backdrop_geometry_colors_and_alpha_at_native_dpr(self):
        self.require_gpu()
        card = blur.BlurCard(self.root, corner=24, tint=QColor(0, 0, 0, 0),
                             border=QColor(0, 0, 0, 0))
        self.windows.append(card)
        card.resize(160, 120)
        for ratio in (1, 1.25, 1.5, 2):
            source = QPixmap(round(240 * ratio), round(200 * ratio))
            source.setDevicePixelRatio(ratio)
            source.fill(Qt.black)
            painter = QPainter(source)
            for x in range(240):
                painter.fillRect(x, 0, 1, 200, QColor(x, x // 2, 200, 128))
            painter.end()
            with self.subTest(ratio=ratio), patch.object(card, "devicePixelRatioF", return_value=ratio):
                theme._effect_level = "medium"
                plain = blur._gpu_card_frame(card, source, QPoint(-40, -40))
                theme._effect_level = "high"
                desktop = blur._gpu_card_frame(card, source, QPoint(-40, -40))
                self.assertEqual(desktop, plain)
                self.assertEqual(desktop.pixelColor(0, 0).alpha(), 0)

    def test_high_static_fractional_edge_coverage_matches_plain_card(self):
        self.require_gpu()
        card = blur.BlurCard(self.root, corner=14, tint=QColor(0, 0, 0, 0),
                             border=QColor(0, 0, 0, 0))
        self.windows.append(card)
        card.resize(137, 93)
        source = QPixmap(100, 80)
        source.fill(QColor("#496681"))
        layers = [(source, QRectF(card.rect()), 1.0)]
        for ratio in (1.25, 1.5, 1.75, 2):
            with self.subTest(ratio=ratio), patch.object(card, "devicePixelRatioF", return_value=ratio):
                theme._effect_level = "medium"
                plain = blur._gpu_card_frame(card, source, QPoint(), layers)
                theme._effect_level = "high"
                desktop = blur._gpu_card_frame(card, source, QPoint(), layers)
                for x, y in ((desktop.width()-1, desktop.height()//2), (desktop.width()//2, desktop.height()-1)):
                    self.assertLessEqual(abs(desktop.pixelColor(x, y).alpha()-plain.pixelColor(x, y).alpha()), 1)

    def test_high_border_stays_subtle_and_neutral_with_a_strong_caller_color(self):
        self.require_gpu()
        card = blur.BlurCard(self.root, corner=14, tint=QColor(0, 0, 0, 0),
                             border=QColor(255, 0, 180))
        self.windows.append(card)
        card.resize(160, 120)
        source = QPixmap(160, 120)
        source.fill(QColor(60, 60, 60))
        plain = blur._gpu_card_frame(card, source, QPoint()).pixelColor(80, 0)
        self.assertGreater(plain.red() - plain.green(), 50)
        theme._effect_level = "high"
        for light in (False, True):
            with self.subTest(light=light), patch.object(theme, "_palette", {"_is_light": light}):
                rim = blur._gpu_card_frame(card, source, QPoint()).pixelColor(80, 0)
                self.assertLessEqual(max(rim.red(), rim.green(), rim.blue()) -
                                     min(rim.red(), rim.green(), rim.blue()), 1)
                self.assertLess(abs(rim.red() - 60), 35)


    def test_cached_backdrop_is_reused_and_callers_cannot_modify_cache(self):
        source = self.step()
        with patch.object(blur, "blur_gpu", return_value=source.toImage()) as render:
            first = blur.make_blur_pixmap(source)
            first.fill(QColor("red"))
            second = blur.make_blur_pixmap(source)
            self.assertEqual(render.call_count, 1)
            self.assertEqual(second.toImage().pixelColor(0, 0), QColor("white"))
            # Mutating the source invalidates its Qt cache key.
            source.fill(QColor("blue"))
            blur.make_blur_pixmap(source)
            self.assertEqual(render.call_count, 2)

    def test_gpu_failure_falls_back_to_native_qt_with_dpr_preserved(self):
        source = self.step(1.5)
        with patch.object(blur, "blur_gpu", return_value=None), \
             patch.object(blur, "_qt_blur_pixmap", wraps=blur._qt_blur_pixmap) as fallback:
            result = blur.make_blur_pixmap(source)
            fallback.assert_called_once()
        self.assertFalse(result.isNull())
        self.assertEqual(result.size(), source.size())
        self.assertEqual(result.devicePixelRatioF(), 1.5)
        self.assertGreater(result.toImage().pixelColor(108, 60).red(), 0)

    def test_capture_region_and_crop_match_global_position_at_native_dpr(self):
        position = self.root.mapToGlobal(QPoint(180, 120))
        captured = blur._capture_widget_area(self.root, position, 170, 110, 22)
        cropped = blur._crop_logical(captured, 22, 22, 170, 110)
        reference = self.root.grab(QRect(180, 120, 170, 110))
        self.assertEqual(cropped.devicePixelRatioF(), self.root.devicePixelRatioF())
        self.assertEqual(cropped.toImage(), reference.toImage())

    def test_deferred_blur_cancelled_before_dispatch_has_no_worker_or_callback(self):
        with patch.object(blur, "make_blur_pixmap", wraps=blur.make_blur_pixmap) as compute:
            overlay = blur.BlurOverlay(self.root, animate=False, backdrop="blur",
                                       source_widget=self.root)
            self.windows.append(overlay)
            overlay._final_close()
            self.app.processEvents()
            compute.assert_not_called()


    def surface_dialog(self, level):
        theme._effect_level = level
        overlay = blur.BlurOverlay(self.root, animate=False, capture_without_backdrop=True)
        self.windows.append(overlay)
        dialog = AutomationLogWindow(self.root)
        dialog.setAttribute(Qt.WA_DontShowOnScreen)
        self.windows.append(dialog)
        dialog._overlay = overlay
        with patch.object(theme, "_animations_enabled", False):
            blur.install_window_fade(dialog, freeze_backdrop_immediately=True)
            dialog.show()
        self.app.processEvents()
        return dialog, overlay._surface_card


    def test_rust_shader_preserves_colors_alpha_orientation_and_skia_state(self):
        self.require_gpu()
        source = QPixmap(160, 96)
        source.fill(QColor("#19314b"))
        painter = QPainter(source)
        painter.fillRect(0, 0, 160, 48, QColor("#a93e19"))
        painter.end()
        image = gpu.blur_gpu(source, 18)
        self.assertEqual(gpu.gpu_backend_status()["blur_backend"], "rust-gaussian-opengl")
        self.assertEqual(image.pixelColor(80, 4), QColor("#a93e19"))
        self.assertEqual(image.pixelColor(80, 90), QColor("#19314b"))
        transparent = QPixmap(72, 72)
        transparent.fill(QColor(200, 100, 50, 96))
        alpha = gpu.blur_gpu(transparent, 18)
        self.assertAlmostEqual(alpha.pixelColor(20, 20).alpha(), 96, delta=1)
        def draw(canvas, bridge):
            canvas.drawColor(bridge.color(QColor("#a1c5df")))
        frame = gpu.render_gpu((53, 37), 1.5, draw)
        self.assertEqual(frame.pixelColor(20, 20), QColor("#a1c5df"))
        self.assertIsNone(gpu.gpu_backend_status()["rust_blur_error"])


    def test_medium_resize_stretches_frozen_texture_and_preserves_corner_alpha(self):
        self.require_gpu()
        card = blur.BlurCard(self.root, corner=14)
        card.setGeometry(60, 70, 170, 110)
        card.show()
        self.windows.append(card)
        card.freeze_from_widget(self.root, card.mapToGlobal(QPoint()))
        before = gpu.gpu_backend_status()["blur_frames"]
        card.resize(300, 210)
        image = card.grab().toImage()
        self.assertEqual(gpu.gpu_backend_status()["blur_frames"], before)
        self.assertEqual(image.pixelColor(0, 0).alpha(), 0)
        self.assertEqual(image.pixelColor(image.width()-20, image.height()-20).alpha(), 255)

    def test_cached_resize_mask_matches_skia_aa_at_fractional_dpr(self):
        self.require_gpu()
        card = blur.BlurCard(self.root, corner=14)
        self.windows.append(card)
        card._tint = QColor(20, 30, 50, 255)
        for size in ((137, 93), (138, 94)):
            card.resize(*size)
            for ratio in (1, 1.25, 1.5, 1.75, 2):
                with self.subTest(size=size, ratio=ratio), patch.object(card, "devicePixelRatioF", return_value=ratio):
                    fast = blur._blur_card_resize_frame(card, [])
                    reference = blur._gpu_card_frame(card, QPixmap(), QPoint(), [])
                    self.assertEqual(fast.pixelColor(0, 0).alpha(), 0)
                    self.assertEqual(fast.pixelColor(fast.width()//2, fast.height()//2).alpha(), 255)
                    maximum = max(abs(fast.pixelColor(x, y).alpha()-reference.pixelColor(x, y).alpha())
                        for y in range(fast.height()) for x in range(fast.width()))
                    self.assertLessEqual(maximum, 32)

    def test_frozen_texture_fractional_edges_do_not_blink_at_gpu_handoff(self):
        self.require_gpu()
        card = blur.BlurCard(self.root, corner=14)
        self.windows.append(card)
        card.resize(137, 93)
        source = QPixmap(100, 80)
        source.fill(QColor("#496681"))
        layers = [(source, QRectF(card.rect()), 1.0)]
        for ratio in (1, 1.25, 1.5, 1.75, 2):
            with self.subTest(ratio=ratio), patch.object(card, "devicePixelRatioF", return_value=ratio):
                fast = blur._blur_card_resize_frame(card, layers)
                settled = blur._gpu_card_frame(card, source, QPoint(), layers)
                maximum = max(abs(fast.pixelColor(x, y).alpha()-settled.pixelColor(x, y).alpha())
                    for y in range(fast.height()) for x in range(fast.width()))
                self.assertLessEqual(maximum, 16)
                self.assertGreater(fast.pixelColor(fast.width()-1, fast.height()//2).alpha(), 40)

    @unittest.skipIf(AutomationLogWindow is None, "private Automation bundle not installed")
    def test_log_card_is_blurred_centered_clipped_and_not_recomputed_during_fade(self):
        overlay = blur.BlurOverlay(self.root, capture_without_backdrop=True, animate=False)
        self.windows.append(overlay)
        dialog = AutomationLogWindow(self.root)
        dialog.setAttribute(Qt.WA_DontShowOnScreen)
        self.windows.append(dialog)
        dialog._overlay = overlay
        dialog.append_log("[INFO] Synthetic UI check; no automation executed")
        with patch.object(theme, "_animations_enabled", False):
            blur.install_window_fade(dialog, freeze_backdrop_immediately=True)
            dialog.show()
        self.app.processEvents()
        surface = overlay._surface_card
        card = dialog.layout().itemAt(0).widget()
        self.assertEqual(surface.geometry(), card.geometry())
        self.assertFalse(surface._blur_pix.isNull())
        self.assertLessEqual((dialog.geometry().center() - self.root.geometry().center()).manhattanLength(), 2)
        self.assertEqual(dialog.grab().toImage().pixelColor(0, 0).alpha(), 0)
        self.assertTrue(dialog.view.isReadOnly())
        self.assertIn("Synthetic UI check", dialog.view.toPlainText())
        with patch.object(blur, "make_blur_pixmap", side_effect=AssertionError("reblur during fade")):
            for opacity in (.9, .5, .1, 1):
                dialog.setWindowOpacity(opacity)
                dialog.grab()
                self.app.processEvents()

    @unittest.skipIf(renxec is None, "private Automation bundle not installed")
    def test_shared_picker_overlay_survives_selection_and_cancel_is_delivered_once(self):
        overlay = blur.BlurOverlay(self.root, animate=False, capture_without_backdrop=True)
        self.windows.append(overlay)
        self.root.tree = types.SimpleNamespace(base="project_a")
        self.root.tree2 = types.SimpleNamespace(base=None)
        callbacks = []
        surfaces = []

        def finish_dialog(select):
            dialog = self.app.activeModalWidget()
            surfaces.append(overlay._surface_card)
            self.assertFalse(surfaces[-1]._blur_pix.isNull())
            if select:
                dialog._on_card_selected("system")
                dialog._confirm()
            else:
                dialog.reject()

        with patch.object(theme, "_animations_enabled", False), \
             patch.object(FsPickerDialog, "_collect_partitions", return_value=[("system", "EROFS")]):
            QTimer.singleShot(10, lambda: finish_dialog(True))
            FsPickerDialog.pick(self.root, self.root, lambda *args: callbacks.append(args),
                                with_blur=False, overlay=overlay)
            self.assertTrue(overlay.isVisible())
            self.assertFalse(getattr(overlay, "_closing", False))
            QTimer.singleShot(10, lambda: finish_dialog(False))
            FsPickerDialog.pick(self.root, self.root, lambda *args: callbacks.append(args),
                                with_blur=False, overlay=overlay)
        self.assertEqual(callbacks[0][::2], ("project_a", "system"))
        self.assertEqual(callbacks[1], (None, None, None))
        self.assertEqual(len(callbacks), 2)
        self.assertIsNot(surfaces[0], surfaces[1])
        self.assertTrue(overlay.isVisible())

    @unittest.skipIf(renxec is None, "private Automation bundle not installed")
    def test_automation_entry_passes_same_backdrop_to_both_actual_picker_factories(self):
        # Isolate the production entry method from authentication/services and
        # execution imports; only the UI flow runs with synthetic selections.
        path = Path(__file__).resolve().parent.parent / "rennsproject/main_ui.py"
        module = ast.parse(path.read_text(encoding="utf-8"))
        method = next(node for node in ast.walk(module)
                      if isinstance(node, ast.FunctionDef) and node.name == "run_automation")
        namespace = {}
        exec(compile(ast.Module(body=[method], type_ignores=[]), str(path), "exec"), namespace)
        self.root._private_action_allowed = lambda: True
        ran = []
        self.root._run_automation_exec = lambda *args: ran.append(args)
        captured = []

        def file_picker(parent, callback, **kwargs):
            captured.append(kwargs["overlay"])
            callback("synthetic.renxec")

        def fs_picker(parent, main, callback, **kwargs):
            captured.append(kwargs["overlay"])
            callback("project_a", "synthetic_tree", "system")

        with patch.object(renxec.RenxecPickerDialog, "pick", file_picker), \
             patch.object(FsPickerDialog, "pick", fs_picker):
            namespace["run_automation"](self.root)
        overlay = self.root._automation_blur_ov
        self.windows.append(overlay)
        self.assertEqual(captured, [overlay, overlay])
        self.assertTrue(overlay._capture_without_backdrop)
        self.assertEqual(ran, [("project_a", "synthetic_tree", "system", "synthetic.renxec")])

    @unittest.skipIf(renxec is None, "private Automation bundle not installed")
    def test_renxec_escape_before_opening_fade_delivers_cancel_once_without_closing_shared_overlay(self):
        overlay = blur.BlurOverlay(self.root, animate=False, capture_without_backdrop=True)
        self.windows.append(overlay)
        callbacks = []
        with patch.object(theme, "_animations_enabled", True), \
             patch.object(renxec.RenxecPickerDialog, "_build_sidebar", lambda self: None), \
             patch.object(renxec.RenxecPickerDialog, "_load_dir", lambda self, path: None):
            QTimer.singleShot(10, lambda: QTest.keyClick(self.app.activeModalWidget(), Qt.Key_Escape))
            renxec.RenxecPickerDialog.pick(self.root, callbacks.append, with_blur=False, overlay=overlay)
            QTest.qWait(160)
        self.assertEqual(callbacks, [""])
        self.assertTrue(overlay.isVisible())
        self.assertFalse(getattr(overlay, "_closing", False))


if __name__ == "__main__":
    unittest.main()
