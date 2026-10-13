# SPDX-License-Identifier: MPL-2.0
"""Button semantics and Skia GPU feedback regressions.

Run the portable suite with::

    python -m unittest discover -s tests -p test_skia_button_feedback.py -v

Set RAK_TEST_SKIA_GPU=1 to additionally verify a native OpenGL-backed Skia
render target. The default run uses Qt's offscreen platform and leaves theme
preferences untouched.
"""

import os
import time
import types
import unittest
from unittest.mock import patch


_TEST_NATIVE_GPU = os.environ.get("RAK_TEST_SKIA_GPU") == "1"
if not _TEST_NATIVE_GPU:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent, QPoint, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPixmap
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QDialog, QGraphicsOpacityEffect, QPushButton,
    QStyle, QStyleOptionButton, QWidget,
)
from shiboken6 import isValid

from app_ext import class_ButtonPressAnimation as feedback
from core import skia_gpu
from main_ext import class_ThemeUi as theme_module


class _RecordingCanvas:
    def __init__(self):
        self.transforms = []

    def translate(self, x, y):
        self.transforms.append(("translate", x, y))

    def scale(self, x, y):
        self.transforms.append(("scale", x, y))


class _RecordingBridge:
    def __init__(self):
        self.sources = []

    def draw_image(self, _canvas, source, _destination, opacity=1.0):
        image = source.toImage() if isinstance(source, QPixmap) else source
        self.sources.append(image.copy())


class ButtonFeedbackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)
        feedback.install_button_press_animations(cls.app)

    def setUp(self):
        self.theme = types.SimpleNamespace(animations_enabled=True)
        self.theme_patch = patch.object(theme_module, "theme", self.theme)
        self.theme_patch.start()
        self.windows = []

    def tearDown(self):
        for window in self.windows:
            if isValid(window):
                window.hide()
                window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        self.app.processEvents()
        self.theme_patch.stop()
        self.assertEqual(self.app._button_press_manager._active, set())

    def make_button(self, button_class=QPushButton, dialog=False):
        window = QDialog() if dialog else QWidget()
        window.resize(260, 120)
        button = button_class("Qt café", window)
        button.setGeometry(20, 20, 140, 44)
        if isinstance(button, QPushButton):
            button.setAutoDefault(False)
        self.windows.append(window)
        window.show()
        button.setFocus()
        self.app.processEvents()
        return button, window

    def watch_signals(self, button):
        events = []
        button.pressed.connect(lambda: events.append("pressed"))
        button.released.connect(lambda: events.append("released"))
        button.clicked.connect(lambda checked: events.append(("clicked", checked)))
        return events

    def wait_until(self, predicate, timeout_ms=1200):
        deadline = time.monotonic() + timeout_ms / 1000
        while not predicate() and time.monotonic() < deadline:
            QTest.qWait(10)
        self.assertTrue(predicate(), "condition did not become true before timeout")

    def hold(self, button, position=None):
        QTest.mousePress(button, Qt.LeftButton, pos=position or button.rect().center())
        self.wait_until(lambda: feedback.button_press_scale(button) <= 0.9601)

    def is_active(self, button):
        controller = getattr(button, "_button_press_feedback", None)
        return controller in self.app._button_press_manager._active

    def wait_for_idle(self, button):
        # Native GPU rendering may retain a passive scale-1 effect at idle.
        self.wait_until(lambda: not self.is_active(button))
        self.assertEqual(feedback.button_press_scale(button), 1.0)
        controller = getattr(button, "_button_press_feedback", None)
        if controller is not None:
            self.assertFalse(controller._pressed)
            self.assertFalse(controller._release_timer.isActive())

    def test_mouse_click_keeps_native_signals_and_check_state(self):
        button, _ = self.make_button()
        button.setCheckable(True)
        events = self.watch_signals(button)
        self.hold(button)
        self.assertFalse(button.isChecked())
        QTest.mouseRelease(button, Qt.LeftButton, pos=button.rect().center())
        self.assertTrue(button.isChecked())
        self.assertEqual(events, ["pressed", "released", ("clicked", True)])
        self.wait_for_idle(button)
        effect = button.graphicsEffect()
        if effect is not None:
            self.assertIsInstance(effect, feedback.PressScaleEffect)
            self.assertEqual(effect.scale, 1.0)

    def test_visual_scale_keeps_layout_and_edge_hit_area(self):
        button, _ = self.make_button()
        events = self.watch_signals(button)
        geometry = button.geometry()
        edge = QPoint(1, button.height() // 2)
        self.hold(button, edge)
        self.assertEqual(button.geometry(), geometry)
        QTest.mouseRelease(button, Qt.LeftButton, pos=edge)
        self.assertIn(("clicked", False), events)
        self.wait_for_idle(button)

    def test_drag_release_outside_cancels_native_click(self):
        button, _ = self.make_button()
        events = self.watch_signals(button)
        self.hold(button)
        outside = QPoint(button.width() + 20, button.height() // 2)
        QTest.mouseMove(button, outside)
        QTest.mouseRelease(button, Qt.LeftButton, pos=outside)
        self.assertFalse(any(isinstance(event, tuple) for event in events))
        self.wait_for_idle(button)

    def test_space_keyboard_activation_uses_same_feedback(self):
        button, _ = self.make_button()
        events = self.watch_signals(button)
        QTest.keyPress(button, Qt.Key_Space)
        self.wait_until(lambda: feedback.button_press_scale(button) <= 0.9601)
        QTest.keyRelease(button, Qt.Key_Space)
        self.assertEqual(events, ["pressed", "released", ("clicked", False)])
        self.wait_for_idle(button)

    def test_enter_activates_native_dialog_default_button(self):
        button, _ = self.make_button(dialog=True)
        button.setDefault(True)
        events = self.watch_signals(button)
        QTest.keyClick(button, Qt.Key_Return)
        self.assertEqual(events, ["pressed", "released", ("clicked", False)])
        self.assertTrue(self.is_active(button))
        self.wait_for_idle(button)

    def test_programmatic_click_and_set_checked_keep_qt_semantics(self):
        button, _ = self.make_button()
        button.setCheckable(True)
        events = self.watch_signals(button)
        button.setChecked(True)
        self.assertEqual(events, [])
        self.assertFalse(self.is_active(button))
        button.click()
        self.assertEqual(events, ["pressed", "released", ("clicked", False)])
        self.assertFalse(button.isChecked())
        self.assertTrue(self.is_active(button))
        self.wait_for_idle(button)

    def test_checkbox_keeps_checked_state_and_feedback(self):
        checkbox, _ = self.make_button(QCheckBox)
        events = self.watch_signals(checkbox)
        position = QPoint(8, checkbox.height() // 2)
        QTest.mouseClick(checkbox, Qt.LeftButton, pos=position)
        self.assertTrue(checkbox.isChecked())
        self.assertEqual(events, ["pressed", "released", ("clicked", True)])
        self.wait_for_idle(checkbox)

    def test_right_mouse_button_does_not_activate_feedback(self):
        button, _ = self.make_button()
        events = self.watch_signals(button)
        QTest.mouseClick(button, Qt.RightButton)
        self.assertEqual(events, [])
        self.assertFalse(self.is_active(button))

    def test_fast_release_holds_then_restores_and_repress_cancels_timer(self):
        button, _ = self.make_button()
        # Native GPU painting can take longer than the 120 ms hold during
        # event dispatch. Give this fast-input case a deterministic clock;
        # Qt still delivers real mouse events and runs the actual timers.
        with patch.object(feedback.time, "monotonic", return_value=123.0):
            QTest.mousePress(button, Qt.LeftButton)
            controller = button._button_press_feedback
            effect = controller._effect
            QTest.mouseRelease(button, Qt.LeftButton)
        self.assertTrue(controller._release_timer.isActive())
        QTest.mousePress(button, Qt.LeftButton)
        self.assertTrue(controller._pressed)
        self.assertFalse(controller._release_timer.isActive())
        self.assertIs(controller._effect, effect)
        self.wait_until(lambda: feedback.button_press_scale(button) <= 0.9601)
        QTest.mouseRelease(button, Qt.LeftButton)
        self.wait_for_idle(button)

    def test_hide_disable_and_window_deactivation_clear_feedback(self):
        for action in ("hide", "disable", "deactivate"):
            with self.subTest(action=action):
                button, window = self.make_button()
                self.hold(button)
                if action == "hide":
                    button.hide()
                elif action == "disable":
                    button.setEnabled(False)
                else:
                    QCoreApplication.sendEvent(window, QEvent(QEvent.WindowDeactivate))
                self.assertFalse(feedback.has_button_press_feedback(button))
                self.assertIsNone(button.graphicsEffect())
                QTest.mouseRelease(button, Qt.LeftButton)
                window.hide()

    def test_disabled_or_hidden_buttons_do_not_create_effect(self):
        button, _ = self.make_button()
        button.setEnabled(False)
        button.pressed.emit()
        self.assertFalse(self.is_active(button))
        button.setEnabled(True)
        button.hide()
        button.pressed.emit()
        self.assertFalse(self.is_active(button))

    def test_foreign_effect_has_priority_and_reset_preserves_it(self):
        button, _ = self.make_button()
        opacity = QGraphicsOpacityEffect(button)
        opacity.setOpacity(0.5)
        button.setGraphicsEffect(opacity)
        QTest.mousePress(button, Qt.LeftButton)
        self.assertFalse(feedback.has_button_press_feedback(button))
        feedback.reset_button_press_feedback(button)
        self.assertIs(button.graphicsEffect(), opacity)
        self.assertEqual(opacity.opacity(), 0.5)
        QTest.mouseRelease(button, Qt.LeftButton)

    def test_transition_takeover_then_reset_preserves_transition(self):
        button, _ = self.make_button()
        self.hold(button)
        scale = feedback.button_press_scale(button)
        self.assertLess(scale, 1.0)
        opacity = QGraphicsOpacityEffect(button)
        button.setGraphicsEffect(opacity)
        self.assertEqual(feedback.button_press_scale(button), 1.0)
        feedback.reset_button_press_feedback(button)
        self.assertIs(button.graphicsEffect(), opacity)
        QTest.mouseRelease(button, Qt.LeftButton)

    def test_explicit_reset_supports_morph_scale_handoff(self):
        button, _ = self.make_button()
        self.hold(button)
        launch_scale = feedback.button_press_scale(button)
        self.assertAlmostEqual(launch_scale, 0.96, places=3)
        feedback.reset_button_press_feedback(button)
        self.assertEqual(feedback.button_press_scale(button), 1.0)
        self.assertIsNone(button.graphicsEffect())
        QTest.mouseRelease(button, Qt.LeftButton)

    def test_animation_preference_preserves_click_and_resets_on_release(self):
        button, _ = self.make_button()
        events = self.watch_signals(button)
        self.theme.animations_enabled = False
        QTest.mouseClick(button, Qt.LeftButton)
        self.assertEqual(events, ["pressed", "released", ("clicked", False)])
        self.assertFalse(self.is_active(button))
        self.theme.animations_enabled = True
        self.hold(button)
        self.theme.animations_enabled = False
        QTest.mouseRelease(button, Qt.LeftButton)
        self.assertFalse(self.is_active(button))

    def test_animated_button_keeps_qt_click_and_lifecycle(self):
        button, _ = self.make_button(feedback.PressAnimatedButton)
        events = self.watch_signals(button)
        self.hold(button)
        self.assertFalse(button.grab().isNull())
        QTest.mouseRelease(button, Qt.LeftButton)
        self.assertEqual(events, ["pressed", "released", ("clicked", False)])
        self.wait_for_idle(button)

    def test_qt_fallback_discards_press_effect_after_recovery(self):
        with patch.object(feedback, "gpu_available", return_value=False), \
                patch.object(feedback, "render_gpu", return_value=None):
            button, _ = self.make_button()
            self.hold(button)
            QTest.mouseRelease(button, Qt.LeftButton)
            self.wait_for_idle(button)
            self.assertIsNone(button.graphicsEffect())

    def test_idle_button_and_skip_property_do_not_create_graphics_effects(self):
        button, _ = self.make_button()
        feedback.reset_button_press_feedback(button)
        with patch.object(feedback, "gpu_available", return_value=True):
            button.setProperty("skipButtonGpuRendering", True)
            self.app.processEvents()
        self.assertIsNone(button.graphicsEffect())

    def test_destroyed_button_leaves_no_active_controller(self):
        button, window = self.make_button()
        self.hold(button)
        controller = button._button_press_feedback
        window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        self.assertNotIn(controller, self.app._button_press_manager._active)

    def test_animated_button_uploads_qt_style_glyph_pixels(self):
        button, _ = self.make_button(feedback.PressAnimatedButton)
        button.setText("Qt fi café العربية")
        button.setStyleSheet(
            "QPushButton { background: transparent; border: none; color: white; }"
        )
        font = button.font()
        font.setPixelSize(18)
        button.setFont(font)
        captured = []

        def capture(_size, _ratio, draw):
            bridge = _RecordingBridge()
            draw(_RecordingCanvas(), bridge)
            for source in bridge.sources:
                # Independently ask Qt to paint the same native button state.
                expected = QImage(source.size(), source.format())
                expected.setDevicePixelRatio(source.devicePixelRatio())
                expected.fill(Qt.transparent)
                option = QStyleOptionButton()
                button.initStyleOption(option)
                painter = QPainter(expected)
                painter.setFont(button.font())
                button.style().drawControl(QStyle.CE_PushButton, option, painter, button)
                painter.end()
                captured.append((source, expected))
            return None  # Exercise the Qt fallback after observing the upload.

        # This regression covers the native full-style texture fallback. The
        # vector path's Qt-only label fidelity is checked independently.
        with patch.object(feedback, "render_button", return_value=None), \
                patch.object(feedback, "render_gpu", side_effect=capture):
            button.grab()
            button.setText("Qt changed glyphs")
            button.grab()
            font.setPixelSize(22)
            button.setFont(font)
            button.grab()
            button.setStyleSheet(
                "QPushButton { background: transparent; border: none; color: #ffee88; }"
            )
            button.grab()
            button.setCheckable(True)
            button.setChecked(True)
            button.grab()
            self.hold(button)
            button.grab()
            QTest.mouseRelease(button, Qt.LeftButton)
            self.wait_for_idle(button)
        self.assertGreaterEqual(len(captured), 2)
        for source, expected in captured:
            self.assertEqual(bytes(source.constBits()), bytes(expected.constBits()))
        self.assertTrue(any(captured[0][0].pixelColor(x, y).alpha()
                            for y in range(button.height())
                            for x in range(button.width())))

    def test_scale_effect_uploads_qt_source_without_redrawing_glyphs(self):
        button, _ = self.make_button()
        source = QImage(140, 44, QImage.Format_RGBA8888_Premultiplied)
        source.fill(Qt.transparent)
        painter = QPainter(source)
        painter.setPen(Qt.white)
        painter.drawText(source.rect(), Qt.AlignCenter, "Qt café العربية")
        painter.end()
        pixmap = QPixmap.fromImage(source)

        class StaticSourceEffect(feedback.PressScaleEffect):
            def sourceBoundingRect(self, _coordinates):
                return QRectF(0, 0, 140, 44)

            def sourcePixmap(self, _coordinates, offset, _padding):
                offset.setX(0)
                offset.setY(0)
                return pixmap

        effect = StaticSourceEffect(button)
        effect.scale = 0.96
        captured = _RecordingBridge()
        canvas = _RecordingCanvas()

        def capture(_size, _ratio, draw):
            draw(canvas, captured)
            return None

        output = QImage(source.size(), source.format())
        output.fill(Qt.transparent)
        with patch.object(feedback, "render_gpu", side_effect=capture):
            painter = QPainter(output)
            effect.draw(painter)
            painter.end()
        self.assertEqual(len(captured.sources), 1)
        uploaded = captured.sources[0].convertToFormat(source.format())
        self.assertEqual(bytes(uploaded.constBits()), bytes(source.constBits()))
        self.assertIn(("scale", 0.96, 0.96), canvas.transforms)


class SkiaFallbackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_missing_skia_returns_none_without_executing_draw(self):
        called = []
        with patch.object(skia_gpu, "_RENDERER", None), \
                patch.object(skia_gpu, "_UNAVAILABLE", None), \
                patch.object(skia_gpu, "skia", None):
            result = skia_gpu.render_gpu(QSize(20, 12), 1.0, lambda *_: called.append(True))
            self.assertIsNone(result)
            self.assertEqual(called, [])
            self.assertEqual(skia_gpu.gpu_backend_status()["backend"], "qt")

    def test_disable_switch_uses_qt_without_creating_gpu_renderer(self):
        with patch.object(skia_gpu, "_RENDERER", None), \
                patch.object(skia_gpu, "_UNAVAILABLE", None), \
                patch.dict(os.environ, {"RAK_DISABLE_SKIA_GPU": "1"}), \
                patch.object(skia_gpu, "_SkiaGpuRenderer") as renderer:
            self.assertIsNone(skia_gpu.render_gpu(QSize(20, 12), 1.0, lambda *_: None))
            renderer.assert_not_called()
            self.assertEqual(skia_gpu.gpu_backend_status()["backend"], "qt")


@unittest.skipUnless(_TEST_NATIVE_GPU, "set RAK_TEST_SKIA_GPU=1 for native GPU proof")
class SkiaHardwareTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        feedback.install_button_press_animations(cls.app)

    def require_gpu_image(self, logical_size, ratio, draw):
        result = skia_gpu.render_gpu(logical_size, ratio, draw)
        status = skia_gpu.gpu_backend_status()
        self.assertIsNotNone(result, str(status))
        self.assertEqual(status["backend"], "skia-gpu", str(status))
        self.assertIsNone(status["last_error"], str(status))
        self.assertFalse(result.isNull())
        return result

    def test_native_gpu_render_target_readback_and_fractional_dpr(self):
        import skia

        for ratio in (1.0, 1.5, 2.0):
            with self.subTest(dpr=ratio):
                def draw(canvas, _bridge):
                    canvas.drawRect(
                        skia.Rect.MakeXYWH(0, 0, 8, 12),
                        skia.Paint(Color=skia.ColorRED),
                    )

                output = self.require_gpu_image(QSize(20, 12), ratio, draw)
                self.assertEqual(output.size(), QSize(int(20 * ratio), int(12 * ratio)))
                self.assertEqual(output.devicePixelRatio(), ratio)
                self.assertEqual(output.pixelColor(2, 2), QColor(255, 0, 0, 255))
                self.assertEqual(output.pixelColor(int(15 * ratio), 2).alpha(), 0)
        status = skia_gpu.gpu_backend_status()
        self.assertGreaterEqual(status["frames"], 3)
        self.assertTrue(status["renderer"])

    def test_qt_glyph_texture_roundtrip_is_pixel_exact(self):
        # The glyphs and shaping are created by Qt before Skia ever sees them.
        source = QImage(300, 64, QImage.Format_RGBA8888_Premultiplied)
        source.fill(Qt.transparent)
        painter = QPainter(source)
        font = QFont("Segoe UI")
        font.setPixelSize(22)
        painter.setFont(font)
        painter.setPen(Qt.white)
        painter.drawText(source.rect(), Qt.AlignCenter, "Qt fi café العربية")
        painter.end()
        self.assertTrue(any(source.pixelColor(x, y).alpha()
                            for y in range(source.height())
                            for x in range(source.width())))

        def draw(canvas, bridge):
            bridge.draw_image(canvas, source, QRectF(0, 0, 300, 64))

        output = self.require_gpu_image(source.size(), 1.0, draw)
        self.assertEqual(bytes(output.constBits()), bytes(source.constBits()))

    def test_plain_button_renders_idle_on_gpu_and_recovers_without_effect(self):
        theme = types.SimpleNamespace(animations_enabled=True)
        window = QWidget()
        window.resize(220, 100)
        button = QPushButton("Qt font", window)
        button.setGeometry(20, 20, 160, 44)
        button.setStyleSheet(
            "QPushButton { background: #cc2200; border: none; color: white; }"
        )
        before = skia_gpu.gpu_backend_status().get("frames", 0)
        try:
            with patch.object(theme_module, "theme", theme):
                window.show()
                self.app.processEvents()
                QTest.qWait(30)
                button.grab()
                status = skia_gpu.gpu_backend_status()
                self.assertEqual(status["backend"], "skia-gpu", str(status))
                self.assertGreater(status["frames"], before)
                self.assertIsNone(button.graphicsEffect())
                self.assertEqual(feedback.button_press_scale(button), 1.0)
                self.assertNotIn(button._button_press_feedback,
                                 self.app._button_press_manager._active)
                QTest.mousePress(button, Qt.LeftButton)
                QTest.qWait(150)
                self.assertLess(feedback.button_press_scale(button), 1.0)
                QTest.mouseRelease(button, Qt.LeftButton)
                QTest.qWait(250)
                self.assertEqual(feedback.button_press_scale(button), 1.0)
                self.assertIsNone(button.graphicsEffect())
                self.assertNotIn(button._button_press_feedback,
                                 self.app._button_press_manager._active)
        finally:
            window.hide()
            window.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)


if __name__ == "__main__":
    unittest.main()
