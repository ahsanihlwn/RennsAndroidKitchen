# SPDX-License-Identifier: MPL-2.0
"""Choice popup input, focus, clipping, source restoration and GPU regressions.

Portable: python -m unittest discover -s tests -p test_choice_morph_gpu.py -v
Set RAK_TEST_SKIA_GPU=1 for native source/caption/card GPU proof.
Theme preferences stay in memory, and blur capture is disabled in the fixture.
"""
import os
import types
import unittest
from unittest.mock import patch

_NATIVE = os.environ.get("RAK_TEST_SKIA_GPU") == "1"
if not _NATIVE:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent, QPoint, QTimer, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QGraphicsOpacityEffect, QPushButton, QWidget
from shiboken6 import isValid

from app_ext import class_BlurOverlay as blur_module
from app_ext import class_ButtonPressAnimation as feedback
from app_ext import class_ChoiceMorphPopup as popup_module
from core import skia_gpu
from main_ext import class_ThemeUi as theme_module


class ChoiceMorphTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)
        feedback.install_button_press_animations(cls.app)

    def setUp(self):
        self.theme = types.SimpleNamespace(
            animations_enabled=True, blur_enabled=False,
            BG="#20232b", BG2="#252932", BG_MODAL="#252932",
            FG="#eeeeee", FG_BLUE="#79acff", FG_BRIGHT="#ffffff",
            _palette={"_border_rgb": (255, 255, 255), "_accent_rgb": (79, 140, 249),
                      "_is_light": False},
            accent=lambda opacity: f"rgba(79,140,249,{round(opacity * 255)})",
            border_color=lambda opacity: f"rgba(255,255,255,{round(opacity * 255)})",
            app_stylesheet=lambda: "QPushButton { color: #eeeeee; background: #303540; }",
        )
        self.patches = [
            patch.object(theme_module, "theme", self.theme),
            patch.object(popup_module, "theme", self.theme),
            patch.object(blur_module, "_th", self.theme),
        ]
        for item in self.patches:
            item.start()
        self.root = QWidget()
        self.root.resize(700, 500)
        self.root.setStyleSheet(self.theme.app_stylesheet())
        self.anchor = QPushButton("Qt choice café العربية", self.root)
        self.anchor.setAutoDefault(False)
        self.anchor.setGeometry(460, 360, 180, 34)
        self.root.show()
        self.app.processEvents()
        self.popups = []

    def tearDown(self):
        for popup in self.popups:
            if isValid(popup):
                popup._finalizing = True
                popup.close()
        if isValid(self.root):
            self.root.hide()
            self.root.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        self.app.processEvents()
        for item in reversed(self.patches):
            item.stop()
        self.assertEqual(self.app._button_press_manager._active, set())

    def make_popup(self, count=3, current=1):
        popup = popup_module.ChoiceMorphPopup(
            self.root, self.anchor, [(index, f"Qt choice {index}") for index in range(count)],
            current,
        )
        self.popups.append(popup)
        return popup

    def run_popup(self, popup, action, delay=290, expected_effect=None):
        failures = []
        closed = []
        popup.closed.connect(lambda: closed.append(True))

        def perform():
            # Native GPU capture can delay animation startup. Wait for the
            # actual open state, while explicit early-close probes keep their
            # short delay and deliberately act during the transition.
            if (delay >= 240 and self.theme.animations_enabled
                    and isValid(popup) and popup._progress < 1):
                action_timer.start(10)
                return
            try:
                action()
            except BaseException as exc:
                failures.append(exc)
                if isValid(popup):
                    popup._finalizing = True
                    popup.close()

        timeout = QTimer(self.root)
        timeout.setSingleShot(True)

        def expire():
            failures.append(AssertionError("popup did not close before timeout"))
            if isValid(popup):
                popup._finalizing = True
                popup.close()

        timeout.timeout.connect(expire)
        action_timer = QTimer(self.root)
        action_timer.setSingleShot(True)
        action_timer.timeout.connect(perform)
        action_timer.start(delay)
        timeout.start(3000)
        try:
            selected = popup.exec()
        finally:
            timeout.stop()
            action_timer.stop()
        if failures:
            raise failures[0]
        self.assertEqual(closed, [True])
        self.assertIs(self.anchor.graphicsEffect(), expected_effect)
        return selected

    def test_keyboard_navigation_wraps_and_enter_selects_native_option(self):
        popup = self.make_popup()

        def select():
            self.assertIs(self.app.focusWidget(), popup.buttons[1])
            QTest.keyClick(popup.buttons[1], Qt.Key_Down)
            self.assertIs(self.app.focusWidget(), popup.buttons[2])
            QTest.keyClick(popup.buttons[2], Qt.Key_Down)
            self.assertIs(self.app.focusWidget(), popup.buttons[0])
            QTest.keyClick(popup.buttons[0], Qt.Key_Up)
            self.assertIs(self.app.focusWidget(), popup.buttons[2])
            QTest.keyClick(popup.buttons[2], Qt.Key_Home)
            self.assertIs(self.app.focusWidget(), popup.buttons[0])
            QTest.keyClick(popup.buttons[0], Qt.Key_End)
            self.assertIs(self.app.focusWidget(), popup.buttons[2])
            QTest.keyClick(popup.buttons[2], Qt.Key_Return)

        self.assertEqual(self.run_popup(popup, select), 2)

    def test_escape_cancels_and_restores_anchor_source(self):
        popup = self.make_popup()

        def cancel():
            self.assertIsInstance(self.anchor.graphicsEffect(), skia_gpu.GpuOpacityEffect)
            self.assertEqual(self.anchor.graphicsEffect().opacity(), 0)
            QTest.keyClick(popup.buttons[1], Qt.Key_Escape)

        self.assertIsNone(self.run_popup(popup, cancel))
        self.assertFalse(self.anchor.grab().isNull())

    def test_early_escape_cancels_without_finishing_opening(self):
        popup = self.make_popup()

        def cancel():
            self.assertLess(popup._progress, 1)
            QTest.keyClick(popup, Qt.Key_Escape)

        self.assertIsNone(self.run_popup(popup, cancel, delay=40))

    def test_outside_click_cancels_without_activating_root_button(self):
        popup = self.make_popup()
        root_clicks = []
        outside_button = QPushButton("Outside", self.root)
        outside_button.setGeometry(20, 20, 100, 34)
        outside_button.clicked.connect(lambda: root_clicks.append(True))
        outside_button.show()

        def outside():
            QTest.mouseClick(outside_button, Qt.LeftButton)

        self.assertIsNone(self.run_popup(popup, outside))
        self.assertEqual(root_clicks, [])

    def test_root_resize_finalizes_popup_and_restores_anchor(self):
        popup = self.make_popup()
        self.assertIsNone(self.run_popup(popup, lambda: self.root.resize(680, 480)))

    def test_animation_disabled_selection_keeps_focus_and_source_restore(self):
        self.theme.animations_enabled = False
        popup = self.make_popup()

        def select():
            self.assertEqual(popup._progress, 1)
            self.assertIs(self.app.focusWidget(), popup.buttons[1])
            QTest.keyClick(popup.buttons[1], Qt.Key_Return)

        self.assertEqual(self.run_popup(popup, select, delay=30), 1)

    def test_progress_clips_fixed_option_layout_and_supports_upward_growth(self):
        popup = self.make_popup(count=8)
        self.assertTrue(popup._upward)
        final_size = popup._body.size()
        popup._set_progress(0.15)
        self.assertEqual(popup._body.size(), final_size)
        self.assertFalse(popup._body.isVisible())
        self.assertGreater(popup._caption_effect.opacity(), 0)
        popup._set_progress(0.55)
        self.assertEqual(popup._body.size(), final_size)
        self.assertLess(popup._body.mask().boundingRect().height(), final_size.height())
        self.assertEqual(popup._caption_effect.opacity(), 0)
        popup._set_progress(1)
        self.assertEqual(popup._body.size(), final_size)
        self.assertEqual(popup.card.geometry(), popup._target_rect)
        self.assertEqual(popup.buttons[0].height(), 32)

    def test_source_restore_preserves_a_foreign_effect(self):
        popup = self.make_popup()
        foreign = QGraphicsOpacityEffect(self.anchor)
        foreign.setOpacity(0.75)
        self.anchor.setGraphicsEffect(foreign)

        def cancel():
            self.assertIsNone(popup._source_effect)
            self.assertIs(self.anchor.graphicsEffect(), foreign)
            QTest.keyClick(popup.buttons[1], Qt.Key_Escape)

        # A foreign effect belongs to its owner and is deliberately retained.
        self.assertIsNone(self.run_popup(popup, cancel, expected_effect=foreign))

    @unittest.skipUnless(_NATIVE, "set RAK_TEST_SKIA_GPU=1 for native popup GPU proof")
    def test_native_source_caption_and_card_use_actual_gpu_frames(self):
        popup = self.make_popup()
        before = skia_gpu.gpu_backend_status().get("frames", 0)

        def inspect():
            popup._animation.stop()
            popup._set_progress(0.15)
            popup._source_caption.grab()
            popup.card.grab()
            self.assertIsNotNone(popup._caption_effect._frame)
            self.assertIsNotNone(popup.card._skia_card_frame)
            self.assertIsNone(popup._card_gpu_effect._frame)  # No redundant opaque composition.
            self.assertIsInstance(popup._source_effect, skia_gpu.GpuOpacityEffect)
            self.assertEqual(popup._source_effect.opacity(), 0)
            # Opacity zero intentionally does no work; probe a visible source
            # to prove the same effect uploads Qt pixels and draws on the GPU.
            popup._source_effect.setOpacity(0.5)
            self.anchor.grab()
            self.assertIsNotNone(popup._source_effect._frame)
            popup._source_effect.setOpacity(0)
            popup._set_progress(1)
            popup.card.grab()
            status = skia_gpu.gpu_backend_status()
            self.assertEqual(status["backend"], "skia-gpu", str(status))
            self.assertGreater(status["frames"], before)
            self.assertIsNone(status["last_error"], str(status))
            QTest.keyClick(popup.buttons[1], Qt.Key_Escape)

        self.assertIsNone(self.run_popup(popup, inspect, delay=60))


if __name__ == "__main__":
    unittest.main()
