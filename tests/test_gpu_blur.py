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
from PySide6.QtWidgets import QApplication, QWidget, QDialog, QLabel, QScrollArea, QPushButton
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

    def test_medium_and_unsupported_embedded_card_use_frozen_gpu_path_on_gui_thread(self):
        self.require_gpu()
        overlay = blur.BlurOverlay(self.root, animate=False, backdrop="blur",
                                   source_widget=self.root, sync_blur=True)
        self.windows.append(overlay)
        self.assertTrue(overlay._ready)
        self.assertEqual(overlay._pix.deviceIndependentSize().toSize(), self.root.size())
        live = blur.LiveBlurCard(self.root)
        self.windows.append(live)
        live.setGeometry(100, 100, 120, 72)
        live.show()
        before = gpu.gpu_backend_status()["blur_frames"]
        with patch.object(theme, "_effect_level", "high"):
            live.freeze_full(self.root, self.root)
        self.assertEqual(gpu.gpu_backend_status()["blur_frames"], before + 1)
        self.assertFalse(live._blur_pix.isNull())
        self.assertEqual(live.grab().deviceIndependentSize().toSize().width(), 120)
        live.cleanup()
        live.refreeze(self.root)
        self.assertEqual(gpu.gpu_backend_status()["blur_frames"], before + 1)

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

    @unittest.skipIf(AutomationLogWindow is None, "private Automation bundle not installed")
    def test_high_tracks_movement_without_recapture_and_updates_only_changed_background(self):
        self.require_gpu()
        dialog, surface = self.surface_dialog("high")
        scene = surface._live_backdrop
        self.assertIsNotNone(scene)
        self.assertFalse(surface._blur_pix.isNull())
        origin = scene.origin_for(surface)
        before = gpu.gpu_backend_status()["blur_frames"]
        with patch.object(blur, "_capture_widget_area", side_effect=AssertionError("moving recaptured root")):
            dialog.move(dialog.pos()+QPoint(13, 17))
            self.app.processEvents()
            self.assertEqual(scene.origin_for(surface), origin-QPoint(13, 17))
            self.assertEqual(gpu.gpu_backend_status()["blur_frames"], before)
        self.root.flipped = True
        self.root.update()
        QTest.qWait(200)
        self.assertGreater(gpu.gpu_backend_status()["blur_frames"], before)
        after = gpu.gpu_backend_status()["blur_frames"]
        QTest.qWait(150)
        self.assertEqual(gpu.gpu_backend_status()["blur_frames"], after)
        self.assertEqual(gpu.gpu_backend_status()["blur_backend"], "rust-gaussian-opengl")
        self.assertFalse(hasattr(surface, "_native_backdrop"))
        self.assertFalse(dialog._overlay.isWindow())

    @unittest.skipIf(AutomationLogWindow is None, "private Automation bundle not installed")
    def test_hot_effect_switch_stops_live_capture_and_keeps_medium_frozen(self):
        self.require_gpu()
        dialog, surface = self.surface_dialog("medium")
        original = surface._blur_pix.toImage()
        dialog.move(dialog.pos()+QPoint(19, 23))
        self.app.processEvents()
        self.assertEqual(surface._blur_pix.toImage(), original)
        with patch.object(theme, "_save_settings"):
            theme.set_effect_level("high")
            scene = surface._live_backdrop
            self.assertIsNotNone(scene)
            theme.set_effect_level("low")
            self.assertIsNone(surface._live_backdrop)
            self.assertTrue(scene._stopped)
            self.assertTrue(surface._blur_pix.isNull())
            theme.set_effect_level("medium")
            self.assertFalse(surface._blur_pix.isNull())
            theme.set_effect_level("high")
            self.assertIs(surface._live_backdrop, scene)

    def test_full_high_overlay_excludes_embedded_qt_foreground_and_keeps_parent(self):
        self.require_gpu()
        theme._effect_level = "high"
        overlay = blur.BlurOverlay(self.root, animate=False, backdrop="blur",
                                   source_widget=self.root, sync_blur=True)
        self.windows.append(overlay)
        foreground = QLabel("Foreground login card", self.root)
        foreground.setGeometry(60, 70, 320, 100)
        foreground.setStyleSheet("background:white; color:black;")
        overlay.exclude_backdrop_widget(foreground)
        foreground.show()
        self.app.processEvents()
        scene = overlay._live_backdrop
        before = gpu.gpu_backend_status()["blur_frames"]
        foreground.setText("Foreground changes never enter the backdrop")
        QTest.qWait(180)
        self.assertEqual(gpu.gpu_backend_status()["blur_frames"], before)
        self.assertIs(foreground.parentWidget(), self.root)
        self.assertIs(overlay.parentWidget(), self.root)
        self.assertFalse(overlay.isWindow())
        self.root.flipped = True
        self.root.update()
        QTest.qWait(200)
        self.assertGreater(gpu.gpu_backend_status()["blur_frames"], before)
        self.assertIs(overlay._live_backdrop, scene)

    def test_staged_capture_matches_frozen_scene_with_scroll_viewport_and_qt_text(self):
        self.require_gpu()
        area = QScrollArea(self.root)
        area.setGeometry(80, 70, 300, 240)
        area.setStyleSheet("background:#222b35; border:1px solid white;")
        content = QWidget()
        content.resize(270, 2200)
        for index in range(100):
            label = QLabel(f"Visible folder {index:03}", content)
            label.setGeometry(4, index*22, 250, 20)
            label.setStyleSheet("background:transparent; color:white; font-size:12px;")
        area.setWidget(content)
        area.show()
        area.verticalScrollBar().setValue(560)
        self.app.processEvents()
        theme._effect_level = "high"
        owner = blur.BlurCard(self.root)
        self.windows.append(owner)
        owner.setGeometry(410, 20, 150, 90)
        owner.show()
        owner.freeze_from_widget(self.root, owner.mapToGlobal(QPoint()))
        scene = owner._live_backdrop
        original = scene._source.copy()
        before = gpu.gpu_backend_status()["blur_frames"]
        scene.refresh()
        QTest.qWait(500)
        self.assertIsNone(scene._capture_jobs)
        self.assertGreater(scene._last_timings["capture_elapsed_ms"], 0)
        self.assertEqual(scene._source, original)
        self.assertEqual(gpu.gpu_backend_status()["blur_frames"], before)
        owner.hide()
        self.assertFalse(scene._capture_timer.isActive())
        self.assertFalse(scene._timer.isActive())

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

    def test_live_dirty_patch_skips_unaffected_widgets_and_matches_full_scene(self):
        self.require_gpu()
        theme._effect_level = "high"
        left = QWidget(self.root)
        right = QWidget(self.root)
        left.setGeometry(20, 20, 180, 140)
        right.setGeometry(700, 20, 180, 140)
        left.setStyleSheet("background:#bf5930; border:none;")
        right.setStyleSheet("background:#34649a; border:none;")
        left.show()
        right.show()
        card = blur.BlurCard(self.root)
        card.setGeometry(400, 300, 140, 90)
        card.show()
        self.windows.append(card)
        card.freeze_from_widget(self.root, card.mapToGlobal(QPoint()))
        scene = card._live_backdrop
        from PySide6.QtGui import QRegion
        # Simulate the actual Qt damaged region after a local source change.
        left.setStyleSheet("background:#724391; border:none;")
        scene._dirty = QRegion(left.geometry())
        jobs = list(scene._jobs(self.root, QPoint(scene.pad, scene.pad),
                                scene._dirty, scene._excluded(), True))
        self.assertIn(left, [job[0] for job in jobs])
        self.assertNotIn(right, [job[0] for job in jobs])
        scene.refresh()
        QTest.qWait(200)
        self.assertIsNone(scene._capture_jobs)
        reference = blur._capture_widget_area(self.root, blur._widget_global_pos(self.root),
            self.root.width(), self.root.height(), scene.pad,
            device_pixel_ratio=scene._capture_ratio, exclude_widgets=scene._excluded())
        self.assertEqual(scene._source, reference.toImage())

    def test_source_resize_reuses_stretched_blur_then_captures_final_size_once(self):
        self.require_gpu()
        theme._effect_level = "high"
        overlay = blur.BlurOverlay(self.root, animate=False, source_widget=self.root,
                                   backdrop="blur", sync_blur=True)
        self.windows.append(overlay)
        scene = overlay._live_backdrop
        original = scene.image.cacheKey()
        before = gpu.gpu_backend_status()["blur_frames"]
        for width, height in ((1100, 700), (1200, 780), (840, 570), (1130, 790)):
            self.root.resize(width, height)
            self.root.flipped = not self.root.flipped
            self.root.update()
            QTest.qWait(20)
            self.assertTrue(scene._resize_active)
            self.assertIsNone(scene._capture_jobs)
            self.assertEqual(scene.image.cacheKey(), original)
            self.assertEqual(gpu.gpu_backend_status()["blur_frames"], before)
            destination = scene.layers_for(overlay)[0][1]
            self.assertTrue(destination.contains(overlay.rect().toRectF()))
            image = overlay.grab().toImage()
            # New exposed edges contain real opaque blur, rather than just
            # the translucent dim fill at the old texture's boundary.
            self.assertEqual(image.pixelColor(image.width()-3, image.height()//2).alpha(), 255)
        QTest.qWait(600)
        self.assertFalse(scene._resize_active)
        self.assertFalse(scene._blend_timer.isActive())
        self.assertIsNone(scene._capture_jobs)
        self.assertEqual(scene._image_size, self.root.size())
        self.assertEqual(gpu.gpu_backend_status()["blur_frames"], before+1)
        self.assertNotEqual(scene.image.cacheKey(), original)

    def test_resize_cancels_queued_capture_and_close_stops_handoff_timers(self):
        self.require_gpu()
        theme._effect_level = "high"
        overlay = blur.BlurOverlay(self.root, animate=False, source_widget=self.root,
                                   backdrop="blur", sync_blur=True)
        self.windows.append(overlay)
        scene = overlay._live_backdrop
        scene.refresh()
        self.assertIsNotNone(scene._capture_jobs)
        self.root.resize(1103, 743)
        self.assertIsNone(scene._capture_jobs)
        scene._advance_capture()  # A late queued callback must be harmless.
        scene._resize_timer.stop()
        scene._settle_resize()
        QTest.qWait(100)
        scene.stop()
        self.assertFalse(scene._resize_timer.isActive())
        self.assertFalse(scene._blend_timer.isActive())
        self.assertFalse(scene._capture_timer.isActive())

    def test_resize_card_uses_cached_skia_aa_corners_without_full_gpu_frames(self):
        self.require_gpu()
        theme._effect_level = "high"
        shell = QWidget(self.root, Qt.Tool | Qt.FramelessWindowHint)
        shell.setAttribute(Qt.WA_DontShowOnScreen)
        shell.setAttribute(Qt.WA_TranslucentBackground)
        shell.setGeometry(180, 160, 400, 320)
        self.windows.append(shell)
        card = blur.BlurCard(shell, corner=14)
        card.setGeometry(shell.rect())
        shell.show()
        card.show()
        card.freeze_from_widget(self.root, card.mapToGlobal(QPoint()))
        scene = card._live_backdrop
        before = gpu.gpu_backend_status()
        sizes = ((401, 321), (477, 358), (530, 392), (438, 345))
        ratio = card.devicePixelRatioF()
        phases = {(round(width*ratio % 1, 6), round(height*ratio % 1, 6))
                  for width, height in sizes}
        with patch.object(blur, "render_gpu", wraps=blur.render_gpu) as full_frame:
            for width, height in sizes:
                card.resize(width, height)
                image = card.grab().toImage()
                self.assertTrue(card._resize_pending)
                self.assertEqual(image.pixelColor(0, 0).alpha(), 0)
                self.assertEqual(image.pixelColor(image.width()//2, image.height()//2).alpha(), 255)
            full_frame.assert_not_called()
        after = gpu.gpu_backend_status()
        self.assertEqual(after["blur_frames"], before["blur_frames"])
        self.assertLessEqual(after["frames"]-before["frames"], 2*len(phases))
        self.assertEqual(scene._image_size, self.root.size())

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
