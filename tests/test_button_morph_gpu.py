# SPDX-License-Identifier: MPL-2.0
"""Morph lifecycle and Qt-font fidelity, with optional native GPU comparisons.

Portable: python -m unittest discover -s tests -p test_button_morph_gpu.py -v
Native GPU: set RAK_TEST_SKIA_GPU=1 before running the same command.
These tests keep theme preferences in memory and create no project files.
"""
import os
import sys
import time
import types
import unittest
from unittest.mock import patch

_NATIVE_GPU = os.environ.get("RAK_TEST_SKIA_GPU") == "1"
if not _NATIVE_GPU:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent, QPoint, QRect, QRectF, Qt
from PySide6.QtGui import QImage, QPainter
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication, QGraphicsOpacityEffect, QLabel, QPushButton, QStackedWidget, QWidget,
)
from shiboken6 import isValid

from app_ext import class_ButtonCardMorph as card_module
from app_ext import class_FooterActionTransition as footer_module
from core import skia_gpu
from core.skia_button import button_label
from PySide6.QtWidgets import QStyle, QStyleOptionButton


class MorphTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def setUp(self):
        theme_module = types.ModuleType("main_ext.class_ThemeUi")
        theme_module.theme = types.SimpleNamespace(
            animations_enabled=True, blur_enabled=False, BG="#20232b",
            app_stylesheet=lambda: """
                QPushButton { background:rgba(255,255,255,14);
                    border:1px solid rgba(255,255,255,24); border-radius:7px;
                    color:#eeeeee; padding:0 14px; font-size:12px; }
                QPushButton[role="primary"] { background:rgba(64,136,255,46);
                    border-color:rgba(64,136,255,97); color:#79acff; }
            """,
        )
        self.theme = theme_module.theme
        self.theme_patch = patch.dict(sys.modules, {"main_ext.class_ThemeUi": theme_module})
        self.theme_patch.start()
        self.root = QWidget()
        self.root.resize(700, 600)
        self.root.setStyleSheet(self.theme.app_stylesheet())
        self.root.show()
        self.app.processEvents()

    def tearDown(self):
        if isValid(self.root):
            self.root.hide()
            self.root.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        self.app.processEvents()
        self.theme_patch.stop()

    def button(self, text, parent, geometry):
        button = QPushButton(text, parent)
        button.setProperty("skipButtonGpuRendering", True)
        button.setAutoDefault(False)
        button.setGeometry(geometry)
        button.show()
        return button

    def make_card(self, opening=True, source_effect=None):
        host = QWidget(self.root)
        host.setGeometry(0, 0, 500, 450)
        card = QWidget(host)
        card.setObjectName("testMorphCard")
        card.setGeometry(12, 12, 440, 400)
        card.setStyleSheet("""
            QWidget#testMorphCard { background:rgba(36,40,49,185);
                border:1px solid #555966; border-radius:14px; }
        """)
        label = QLabel("Qt fi cafe\u0301 \u0627\u0644\u0639\u0631\u0628\u064a\u0629", card)
        label.setGeometry(24, 24, 350, 32)
        label.setStyleSheet("color:white; font-size:18px; background:transparent; border:none;")
        button = self.button("Create Project", self.root, QRect(350, 400, 130, 34))
        host.show()
        card.show()
        self.app.processEvents()
        if source_effect:
            source_effect = QGraphicsOpacityEffect(button)
            source_effect.setOpacity(0)
            button.setGraphicsEffect(source_effect)
        morph = card_module.ButtonCardMorph(
            host, card, button, QRect(350, 400, 130, 34), opening,
            source_effect=source_effect, launch_scale=.96,
        )
        return morph, card, button, source_effect

    def make_footer(self):
        stack = QStackedWidget(self.root)
        stack.setGeometry(0, 0, 600, 500)
        old, new = QWidget(), QWidget()
        stack.addWidget(old)
        stack.addWidget(new)
        left = self.button("Settings", old, QRect(270, 450, 100, 34))
        right = self.button("Create Project", old, QRect(380, 450, 140, 34))
        target = self.button("Confirm Project", new, QRect(360, 450, 160, 34))
        target.setProperty("role", "primary")
        stack.show()
        stack.setCurrentWidget(new)
        self.app.processEvents()
        footer = footer_module.FooterActionTransition(stack, (left, right), (target,))
        return footer, right, target

    def wait_until(self, condition, timeout_ms=1200):
        deadline = time.monotonic() + timeout_ms / 1000
        while not condition() and time.monotonic() < deadline:
            QTest.qWait(10)
        self.assertTrue(condition(), "animation did not finish before timeout")

    def test_card_fallback_keeps_alpha_final_form_size_and_launch_scale(self):
        morph, card, _, _ = self.make_card()
        self.assertAlmostEqual(morph._surface_rect.width(), 130 * .96)
        self.assertEqual(morph._form_pixmap.deviceIndependentSize(), card.size().toSizeF())
        self.assertTrue(morph.testAttribute(Qt.WA_TransparentForMouseEvents))
        with patch.object(card_module, "render_gpu", return_value=None):
            morph._advance(1)
            image = morph.grab().toImage()
        self.assertFalse(morph._skia_gpu_active)
        self.assertEqual(image.pixelColor(0, 0).alpha(), 0)
        self.assertGreater(image.pixelColor(60, 60).alpha(), 0)
        self.assertEqual(morph._surface_rect, QRectF(card.geometry()))
        morph.stop()
        self.assertIsNone(card.graphicsEffect())

    def test_early_cancel_restores_card_without_finishing_or_removing_foreign_effect(self):
        morph, card, _, _ = self.make_card()
        finished = []
        morph.finished.connect(lambda: finished.append(True))
        morph.start()
        QTest.qWait(30)
        morph.stop()
        self.assertEqual(finished, [])
        self.assertIsNone(card.graphicsEffect())
        another, card, _, _ = self.make_card()
        foreign = QGraphicsOpacityEffect(card)
        card.setGraphicsEffect(foreign)
        another.stop()
        self.assertIs(card.graphicsEffect(), foreign)

    def test_open_finish_restores_live_card_and_emits_once(self):
        morph, card, _, _ = self.make_card()
        finished = []
        morph.finished.connect(lambda: finished.append(True))
        morph.start()
        self.wait_until(lambda: bool(finished))
        self.assertEqual(finished, [True])
        self.assertIsNone(card.graphicsEffect())
        self.assertTrue(morph._stopped)

    def test_close_handoff_keeps_drawing_until_owner_hides_window(self):
        morph, card, button, effect = self.make_card(opening=False, source_effect=True)
        handoff = []

        def finish():
            handoff.append((effect.opacity(), self.root.windowOpacity(), morph._stopped))
            button.setGraphicsEffect(None)
            morph.stop()
            self.root.setWindowOpacity(1)

        morph.finished.connect(finish)
        self.assertFalse(morph.testAttribute(Qt.WA_TransparentForMouseEvents))
        morph.start()
        self.wait_until(lambda: bool(handoff))
        self.assertEqual(handoff, [(1.0, 0.0, False)])
        self.assertIsNone(button.graphicsEffect())
        self.assertIsNone(card.graphicsEffect())

    def test_footer_hidden_page_pairs_right_slot_and_keeps_destination_clickable(self):
        footer, source, target = self.make_footer()
        self.assertFalse(source.isVisible())
        self.assertEqual(footer._label_views[0].text(), "Create Project")
        self.assertTrue(footer.testAttribute(Qt.WA_TransparentForMouseEvents))
        clicked = []
        target.clicked.connect(lambda: clicked.append(True))
        footer.start()
        QTest.mouseClick(target, Qt.LeftButton)
        self.assertEqual(clicked, [True])
        footer.stop()

    def test_footer_caches_fixed_qt_labels_and_portable_fallback_is_visible(self):
        footer, _, _ = self.make_footer()
        footer._group.start()
        footer._group.pause()
        footer._group.setCurrentTime(70)
        labels = [(view.geometry(), footer._view_image(view).cacheKey())
                  for view in footer._label_views]
        footer._group.setCurrentTime(210)
        for view, (rect, image_key) in zip(footer._label_views, labels):
            self.assertEqual(view.geometry(), rect)
            self.assertEqual(footer._view_image(view).cacheKey(), image_key)
            self.assertTrue(view.property("skipButtonGpuRendering"))
        with patch.object(footer_module, "render_gpu", return_value=None):
            image = footer.grab().toImage()
        self.assertFalse(footer._skia_gpu_active)
        self.assertEqual(image.pixelColor(0, 0).alpha(), 255)
        footer.stop()

    def test_footer_completion_and_interruption_do_not_leak_overlays(self):
        footer, _, _ = self.make_footer()
        finished = []
        footer.finished.connect(lambda: finished.append(True))
        footer.start()
        self.wait_until(lambda: bool(finished))
        self.assertEqual(finished, [True])
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        self.assertFalse(isValid(footer))
        footer, _, _ = self.make_footer()
        footer.finished.connect(lambda: finished.append(True))
        footer.start()
        footer.stop()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        self.assertFalse(isValid(footer))
        self.assertEqual(finished, [True])

    @unittest.skipUnless(_NATIVE_GPU, "set RAK_TEST_SKIA_GPU=1 for native GPU comparisons")
    def test_native_card_gpu_matches_qt_at_animation_points_and_fractional_dpr(self):
        import numpy as np
        morph, _, _, _ = self.make_card()
        for ratio in (1.0, 1.5, 2.0):
            for progress in (0, .12, .35, .82, 1):
                with self.subTest(dpr=ratio, progress=progress), \
                        patch.object(morph, "devicePixelRatioF", return_value=ratio):
                    morph._advance(progress)
                    output = skia_gpu.render_gpu(morph.size(), ratio, morph._paint_gpu)
                    status = skia_gpu.gpu_backend_status()
                    self.assertIsNotNone(output, str(status))
                    self.assertEqual(status["backend"], "skia-gpu")
                    self.assertIsNone(status["last_error"], str(status))
                    expected = QImage(output.size(), output.format())
                    expected.setDevicePixelRatio(ratio)
                    expected.fill(Qt.transparent)
                    painter = QPainter(expected)
                    morph._paint_qt(painter)
                    painter.end()
                    actual_pixels = np.frombuffer(output.constBits(), dtype=np.uint8).astype(np.int16)
                    expected_pixels = np.frombuffer(expected.constBits(), dtype=np.uint8).astype(np.int16)
                    self.assertLess(float(np.abs(actual_pixels - expected_pixels).mean()), 1.5)
                    self.assertEqual(output.devicePixelRatio(), ratio)
                    self.assertEqual(output.pixelColor(0, 0).alpha(), 0)
        morph.stop()

    @unittest.skipUnless(_NATIVE_GPU, "set RAK_TEST_SKIA_GPU=1 for native GPU comparisons")
    def test_native_footer_gpu_renders_without_child_painting_and_keeps_qt_glyphs(self):
        footer, _, _ = self.make_footer()
        footer._group.start()
        footer._group.pause()
        footer._group.setCurrentTime(140)
        for view in footer._views:
            self.assertTrue(view.isHidden())
        expected = QImage(footer.size(), QImage.Format_RGBA8888_Premultiplied)
        expected.fill(footer._background)
        painter = QPainter(expected)
        for view in footer._views:
            opacity = view.graphicsEffect().opacity()
            if opacity > 0:
                painter.setOpacity(opacity)
                painter.drawImage(QPoint(view.x(), view.y()), footer._view_image(view))
        painter.end()
        output = skia_gpu.render_gpu(footer.size(), 1.0, footer._paint_gpu)
        status = skia_gpu.gpu_backend_status()
        self.assertIsNotNone(output, str(status))
        self.assertIsNone(status["last_error"], str(status))
        # Borders now intentionally differ: Skia draws antialiased vectors.
        # Compare the native Qt glyph pixels rather than requiring the old
        # raster border's stair steps to survive the GPU pass.
        checked_pixels = 0
        for view in footer._label_views:
            if view.graphicsEffect().opacity() <= 0:
                continue
            option = QStyleOptionButton()
            view.initStyleOption(option)
            option.state &= ~(QStyle.State_MouseOver | QStyle.State_HasFocus | QStyle.State_Sunken)
            label = button_label(view, option, 1.0)
            native = footer._view_image(view).convertToFormat(label.format())
            self.assertEqual(bytes(label.constBits()), bytes(native.constBits()))
            for y in range(label.height()):
                for x in range(label.width()):
                    if label.pixelColor(x, y).alpha() == 0:
                        continue
                    actual = output.pixelColor(view.x() + x, view.y() + y).getRgb()
                    reference = expected.pixelColor(view.x() + x, view.y() + y).getRgb()
                    self.assertLessEqual(max(abs(a - b) for a, b in zip(actual, reference)), 2)
                    checked_pixels += 1
        self.assertGreater(checked_pixels, 100)
        footer.stop()


if __name__ == "__main__":
    unittest.main()
