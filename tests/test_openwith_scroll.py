# SPDX-License-Identifier: MPL-2.0
"""Bounded option dialogs, native scroll clipping and shared glass shells."""
import os
import time
import unittest
from unittest.mock import patch

if os.environ.get("RAK_TEST_SKIA_GPU") != "1":
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt, QEvent, QCoreApplication, QPoint, QPointF, QRect, QAbstractAnimation
from PySide6.QtGui import QWheelEvent, QColor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget, QDialog, QVBoxLayout, QLabel

from app_ext.class_BlurOverlay import BlurOverlay, install_window_fade
from app_ext.class_SmoothScrollArea import SmoothScrollArea
from main_ext.class_OpenWithDialog import OpenWithDialog
from main_ext.class_ThemeUi import theme
from core.skia_widgets import install_widget_gpu_rendering
from app_ext.class_ButtonPressAnimation import install_button_press_animations


class OpenWithScrollTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)
        from core.fonts import apply_app_font
        apply_app_font(cls.app)
        if os.environ.get("RAK_TEST_SKIA_GPU") == "1":
            install_widget_gpu_rendering(cls.app)
            install_button_press_animations(cls.app)

    def setUp(self):
        self.root = QWidget()
        self.root.setAttribute(Qt.WA_DontShowOnScreen)
        self.root.setGeometry(100, 70, 720, 500)
        self.root.setStyleSheet("QWidget {background:#101820; color:white;}")
        self.root.show()
        self.dialogs = []

    def tearDown(self):
        for dialog in self.dialogs:
            dialog.hide()
            dialog.deleteLater()
        self.root.hide()
        self.root.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        self.app.processEvents()

    def dialog(self, repeats=1, filename="system_ext_hwservice_contexts"):
        with patch.object(OpenWithDialog, "_OPTIONS", OpenWithDialog._OPTIONS * repeats):
            dialog = OpenWithDialog(self.root, filename)
        self.dialogs.append(dialog)
        dialog.setAttribute(Qt.WA_DontShowOnScreen)
        dialog.show()
        QTest.qWait(60)
        return dialog

    def wheel(self, area, angle=0, pixels=0):
        pos = QPointF(area.viewport().rect().center())
        event = QWheelEvent(pos, QPointF(area.viewport().mapToGlobal(pos.toPoint())),
                            QPoint(0, pixels), QPoint(0, angle), Qt.NoButton,
                            Qt.NoModifier, Qt.ScrollUpdate, False)
        QApplication.sendEvent(area.viewport(), event)
        return event

    def test_many_options_keep_footer_and_window_inside_parent(self):
        dialog = self.dialog(10)
        self.assertEqual(len(dialog._cards), 70)
        bounds = dialog._available_bounds()
        self.assertTrue(bounds.contains(dialog.geometry()), (bounds, dialog.geometry()))
        self.assertGreater(dialog._scroll.verticalScrollBar().maximum(), 3000)
        viewport_bottom = dialog._scroll.mapTo(dialog, dialog._scroll.rect().bottomLeft()).y()
        self.assertGreater(dialog.chk_always.mapTo(dialog, QPoint()).y(), viewport_bottom)
        self.assertGreater(dialog._btn_ok.mapTo(dialog, QPoint()).y(), viewport_bottom)
        self.assertTrue(dialog.rect().contains(QRect(dialog._btn_ok.mapTo(dialog, QPoint()), dialog._btn_ok.size())))

    def test_maximum_five_options_even_with_a_tall_window(self):
        self.root.setGeometry(50, 20, 720, 900)
        dialog = self.dialog(10)
        self.assertEqual(dialog._scroll.height(), 5*56 + 4*12)
        self.assertEqual(dialog._scroll.viewport().height(), 5*56 + 4*12)
        self.assertFalse(dialog._cards[5].visibleRegion().intersects(dialog._cards[5].rect()))
        self.assertTrue(dialog._cards[4].visibleRegion().intersects(dialog._cards[4].rect()))

    def test_native_viewport_clips_option_paint_and_clears_scrolled_pixels(self):
        dialog = self.dialog(10)
        first = dialog._cards[0]
        first.setStyleSheet("background:#ff00ff; border:none;")
        first.findChildren(QLabel)[-1].setText("MARKER")
        self.app.processEvents()
        before = dialog.grab().toImage()
        first_region = QRect(first.mapTo(dialog, QPoint()), first.size())
        ratio = before.devicePixelRatioF()
        bar = dialog._scroll.verticalScrollBar()
        bar.setValue(bar.maximum())
        QTest.qWait(40)
        self.assertFalse(first.visibleRegion().intersects(first.rect()))
        self.assertTrue(dialog._cards[-1].visibleRegion().intersects(dialog._cards[-1].rect()))
        after = dialog.grab().toImage()
        region = QRect(int(first_region.x()*ratio), int(first_region.y()*ratio),
                       int(first_region.width()*ratio), int(first_region.height()*ratio))
        self.assertNotEqual(before.copy(region), after.copy(region))
        # Scrolling may only change the viewport, never the remember/footer area.
        y = dialog.chk_always.mapTo(dialog, QPoint()).y()
        footer = QRect(0, int(y*ratio), after.width(), after.height()-int(y*ratio))
        self.assertEqual(before.copy(footer), after.copy(footer))

    def test_transparent_shell_and_blur_use_exact_card_bounds(self):
        dialog = self.dialog()
        image = dialog.grab().toImage()
        self.assertEqual(image.pixelColor(0, 0).alpha(), 0)
        self.assertEqual(dialog._blur_surface.geometry(), dialog._card.geometry())
        self.assertEqual(image.pixelColor(int(8*image.devicePixelRatioF()), int(8*image.devicePixelRatioF())).alpha(), 0)

    def test_selection_remember_and_double_click_keep_existing_results(self):
        dialog = self.dialog()
        bar = dialog._scroll.verticalScrollBar()
        bar.setValue(bar.maximum())
        QTest.qWait(30)
        QTest.mouseClick(dialog._cards[-1], Qt.LeftButton)
        self.assertEqual(dialog._choice, "super")
        self.assertTrue(dialog._cards[-1]._checked)
        self.assertTrue(dialog._btn_ok.isEnabled())
        QTest.mouseClick(dialog.chk_always, Qt.LeftButton)
        self.assertTrue(dialog.chk_always.isChecked())
        QTest.mouseDClick(dialog._cards[-1], Qt.LeftButton)
        self.assertEqual(dialog.result(), QDialog.Accepted)
        self.assertEqual(dialog._choice, "super")

    def test_trackpad_pixels_and_programmatic_position_are_respected(self):
        dialog = self.dialog(10)
        bar = dialog._scroll.verticalScrollBar()
        bar.setValue(200)
        self.wheel(dialog._scroll, pixels=-25)
        self.assertEqual(bar.value(), 225)
        bar.setValue(300)
        with patch.object(theme, "_animations_enabled", False):
            self.wheel(dialog._scroll, angle=-120)
        self.assertEqual(bar.value(), 340)

    def test_mouse_wheel_accumulates_and_hide_stops_animation(self):
        dialog = self.dialog(10)
        area = dialog._scroll
        self.wheel(area, angle=-120)
        self.wheel(area, angle=-120)
        deadline = time.monotonic() + 1
        while area.verticalScrollBar().value() != 80 and time.monotonic() < deadline:
            QTest.qWait(20)
        self.assertEqual(area.verticalScrollBar().value(), 80)
        self.wheel(area, angle=-120)
        QTest.qWait(30)
        dialog.hide()
        value = area.verticalScrollBar().value()
        QTest.qWait(250)
        self.assertEqual(area.verticalScrollBar().value(), value)
        self.assertFalse(area._scroll_timer.isActive())
        self.assertEqual(area._anim.state(), QAbstractAnimation.State.Stopped)

    def test_long_filename_and_small_parent_still_bound_dialog(self):
        self.root.resize(440, 390)
        dialog = self.dialog(10, "very_long_partition_name_" * 20)
        self.assertTrue(dialog._available_bounds().contains(dialog.geometry()), dialog.geometry())
        self.assertGreater(dialog._scroll.viewport().height(), 0)
        self.assertTrue(dialog._btn_ok.isVisible())

    def test_shared_fade_preserves_caption_and_transparent_shell(self):
        dialog = QDialog(self.root)
        dialog.setAttribute(Qt.WA_TranslucentBackground)
        dialog.setAttribute(Qt.WA_DontShowOnScreen)
        dialog.setStyleSheet("font-size:19px;")
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(8, 8, 8, 8)
        card = QWidget()
        card.setStyleSheet("background:#303540; border-radius:14px;")
        body = QVBoxLayout(card)
        caption = QLabel("Native caption")
        body.addWidget(caption)
        layout.addWidget(card)
        self.dialogs.append(dialog)
        with patch.object(theme, "_animations_enabled", False):
            install_window_fade(dialog)
        dialog.show()
        QTest.qWait(30)
        self.assertEqual(caption.font().pixelSize(), 19)
        self.assertEqual(dialog.grab().toImage().pixelColor(0, 0).alpha(), 0)
        overlay = BlurOverlay(self.root)
        try:
            with patch.object(theme, "_blur_enabled", True):
                overlay._flat = True
                surface = overlay.attach_window_surface(dialog)
                self.assertIsNotNone(surface)
                self.assertEqual(surface.geometry(), card.geometry())
                dialog.resize(dialog.width()+30, dialog.height()+20)
                QTest.qWait(30)
                self.assertEqual(surface.geometry(), card.geometry())
        finally:
            overlay._final_close()


if __name__ == "__main__":
    unittest.main()
