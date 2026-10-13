# SPDX-License-Identifier: MPL-2.0
"""WebToggle interaction and rendering checks, without reading user settings."""
import importlib.util
import os
from pathlib import Path
import sys
import time
import types
import unittest
from unittest.mock import patch

if os.environ.get("RAK_TEST_SKIA_GPU") != "1":
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, QSize, Qt
from PySide6.QtGui import QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QHBoxLayout, QWidget

try:
    import skia
except ImportError:
    skia = None


class WebToggleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        theme_module = types.ModuleType("main_ext.class_ThemeUi")
        theme_module.theme = types.SimpleNamespace(
            animations_enabled=False,
            _palette={"_accent_rgb": (79, 140, 249), "_border_rgb": (255, 255, 255)},
            FG_MUTED="#808080",
        )
        gpu_module = types.ModuleType("core.skia_gpu")
        gpu_module.skia = skia
        gpu_module.render_gpu = lambda *args: None
        path = Path(__file__).resolve().parents[1] / "main_ext" / "class_WebToggle.py"
        spec = importlib.util.spec_from_file_location("_web_toggle_test_target", path)
        cls.module = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {
            "main_ext.class_ThemeUi": theme_module,
            "core.skia_gpu": gpu_module,
        }):
            spec.loader.exec_module(cls.module)

    def setUp(self):
        self.widgets = []

    def tearDown(self):
        for widget in reversed(self.widgets):
            widget.close()
            widget.deleteLater()
        self.app.processEvents()

    def toggle(self, checked=False, **kwargs):
        widget = self.module.WebToggle(checked, **kwargs)
        self.widgets.append(widget)
        return widget

    def test_child_click_does_not_toggle_clickable_parent(self):
        # Regression for main.py's clickable Spam Enter wrapper.
        wrapper = QWidget()
        self.widgets.append(wrapper)
        toggle = self.toggle()
        QHBoxLayout(wrapper).addWidget(toggle)
        signals = []
        wrapper_clicks = []
        toggle.clicked.connect(lambda: signals.append(toggle.isChecked()))

        def wrapper_press(event):
            if event.button() == Qt.LeftButton:
                wrapper_clicks.append(True)
                toggle.setChecked(not toggle.isChecked())

        wrapper.mousePressEvent = wrapper_press
        wrapper.show()
        self.app.processEvents()
        QTest.mouseClick(toggle, Qt.LeftButton, pos=toggle.rect().center())
        self.assertTrue(toggle.isChecked())
        self.assertEqual(signals, [True])
        self.assertEqual(wrapper_clicks, [])

        QTest.mouseClick(wrapper, Qt.LeftButton, pos=QPoint(1, 1))
        self.assertFalse(toggle.isChecked())
        self.assertEqual(wrapper_clicks, [True])
        self.assertEqual(signals, [True])  # setChecked remains silent.

    def test_setter_disabled_and_non_left_click_keep_signal_contract(self):
        toggle = self.toggle()
        signals = []
        toggle.clicked.connect(lambda: signals.append(toggle.isChecked()))
        toggle.setChecked(True)
        self.assertTrue(toggle.isChecked())
        self.assertEqual(signals, [])

        QTest.mouseClick(toggle, Qt.RightButton)
        self.assertTrue(toggle.isChecked())
        toggle.setEnabled(False)
        self.assertEqual(toggle.cursor().shape(), Qt.ArrowCursor)
        QTest.mouseClick(toggle, Qt.LeftButton)
        self.assertTrue(toggle.isChecked())
        self.assertEqual(signals, [])

        toggle.setChecked(False)
        self.assertFalse(toggle.isChecked())
        toggle.setEnabled(True)
        toggle.blockSignals(True)
        QTest.mouseClick(toggle, Qt.LeftButton)
        toggle.blockSignals(False)
        self.assertTrue(toggle.isChecked())
        self.assertEqual(signals, [])
        QTest.mouseClick(toggle, Qt.LeftButton)
        self.assertEqual(signals, [False])

    @unittest.skipIf(skia is None, "Skia optional dependency is unavailable")
    def test_global_press_feedback_scales_vectors_without_second_gpu_pass(self):
        from app_ext import class_ButtonPressAnimation as feedback

        feedback.install_button_press_animations(self.app)
        toggle = self.toggle()
        signals = []
        toggle.clicked.connect(lambda: signals.append(toggle.isChecked()))
        theme_module = types.ModuleType("main_ext.class_ThemeUi")
        theme_module.theme = self.module._th
        frames = []

        def record_frame(size, ratio, draw):
            from unittest.mock import Mock
            canvas = Mock()
            bridge = types.SimpleNamespace(color=lambda color: skia.Color4f(
                color.redF(), color.greenF(), color.blueF(), color.alphaF(),
            ))
            draw(canvas, bridge)
            frames.append(canvas)
            image = QImage(size, QImage.Format_RGBA8888_Premultiplied)
            image.fill(Qt.transparent)
            return image

        with patch.dict(sys.modules, {"main_ext.class_ThemeUi": theme_module}), \
             patch.object(self.module._th, "animations_enabled", True), \
             patch.object(self.module, "render_gpu", side_effect=record_frame), \
             patch.object(feedback, "render_gpu") as second_pass:
            toggle.show()
            self.app.processEvents()
            QTest.mousePress(toggle, Qt.LeftButton, pos=toggle.rect().center())
            deadline = time.monotonic() + 1.0
            while feedback.button_press_scale(toggle) > .960001 and time.monotonic() < deadline:
                QTest.qWait(10)
            held_scale = feedback.button_press_scale(toggle)
            self.assertAlmostEqual(held_scale, .96, places=5)
            self.assertIsNone(toggle.graphicsEffect())
            before = len(frames)
            self.capture(toggle)
            self.assertEqual(len(frames), before)  # Stable held pixels reuse the GPU frame.
            frames[-1].scale.assert_called_once_with(held_scale, held_scale)
            frames[-1].drawRoundRect.assert_called_once()
            self.assertEqual(frames[-1].drawOval.call_count, 2)
            second_pass.assert_not_called()
            self.assertEqual(signals, [True])

            QTest.mouseRelease(toggle, Qt.LeftButton, pos=toggle.rect().center())
            deadline = time.monotonic() + 1.0
            while feedback.has_button_press_feedback(toggle) and time.monotonic() < deadline:
                QTest.qWait(10)
            self.assertEqual(feedback.button_press_scale(toggle), 1.0)
            self.assertFalse(feedback.has_button_press_feedback(toggle))
            self.assertIsNone(toggle.graphicsEffect())
            self.assertEqual(signals, [True])

    @staticmethod
    def capture(widget):
        widget.setAttribute(Qt.WA_TranslucentBackground)
        image = QImage(widget.size(), QImage.Format_RGBA8888)
        image.fill(Qt.transparent)
        widget.render(image)
        return image

    def test_unavailable_gpu_uses_visible_qt_fallback(self):
        toggle = self.toggle(True)
        with patch.object(self.module, "render_gpu", return_value=None) as renderer:
            image = self.capture(toggle)
        renderer.assert_called_once()
        self.assertEqual(image.pixelColor(30, 12).getRgb(), (255, 255, 255, 255))
        self.assertEqual(image.pixelColor(12, 12).getRgb(), (79, 140, 249, 230))
        self.assertEqual(image.pixelColor(0, 0).alpha(), 0)

    @unittest.skipIf(skia is None, "Skia optional dependency is unavailable")
    def test_vector_render_keeps_dpr_alpha_theme_and_disabled_composition(self):
        # Exercise the real Skia commands on a raster test surface. The shared
        # backend separately tests creating a GPU context; this works headless.
        rendered = []

        def draw_on_test_surface(size, dpr, draw):
            width, height = size.width(), size.height()
            surface = skia.Surface.MakeRasterN32Premul(round(width * dpr), round(height * dpr))
            canvas = surface.getCanvas()
            canvas.clear(skia.ColorTRANSPARENT)
            canvas.scale(dpr, dpr)
            bridge = types.SimpleNamespace(color=lambda color: skia.Color4f(
                color.redF(), color.greenF(), color.blueF(), color.alphaF(),
            ))
            draw(canvas, bridge)
            image = QImage(surface.width(), surface.height(), QImage.Format_RGBA8888_Premultiplied)
            info = skia.ImageInfo.Make(image.width(), image.height(), skia.ColorType.kRGBA_8888_ColorType,
                                       skia.AlphaType.kPremul_AlphaType)
            self.assertTrue(surface.readPixels(info, image.bits(), image.bytesPerLine()))
            image.setDevicePixelRatio(dpr)
            rendered.append(image)
            return image

        toggle = self.toggle(True)
        with patch.object(self.module, "render_gpu", side_effect=draw_on_test_surface), \
             patch.object(toggle, "devicePixelRatioF", return_value=2.0):
            self.capture(toggle)
            image = rendered[-1]
            self.assertEqual(image.size(), QSize(84, 48))
            self.assertEqual(image.devicePixelRatio(), 2.0)
            self.assertEqual(image.pixelColor(60, 24).getRgb(), (255, 255, 255, 255))
            self.assertEqual(image.pixelColor(24, 24).getRgb(), (79, 140, 249, 230))
            self.assertEqual(image.pixelColor(0, 0).alpha(), 0)

            toggle.setEnabled(False)
            self.capture(toggle)
            disabled = rendered[-1]
            # Overlapping disabled primitives are composited individually,
            # matching QPainter opacity rather than dimming a flattened image.
            self.assertGreater(disabled.pixelColor(60, 24).alpha(), 140)
            self.assertLess(disabled.pixelColor(60, 24).alpha(), 165)
            self.assertAlmostEqual(disabled.pixelColor(24, 24).alpha(), 230 * .35, delta=1)

        neutral = self.toggle(True, neutral=True)
        with patch.object(self.module, "render_gpu", side_effect=draw_on_test_surface):
            self.capture(neutral)
        self.assertEqual(rendered[-1].pixelColor(12, 12).getRgb(), (128, 128, 128, 200))


if __name__ == "__main__":
    unittest.main()
