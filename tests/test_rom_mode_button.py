# SPDX-License-Identifier: MPL-2.0
"""ROM selector captions remain centered beside their Qt dot icons."""
import os
import unittest
from unittest.mock import patch

if os.environ.get("RAK_TEST_SKIA_GPU") != "1":
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt, QCoreApplication, QEvent
from PySide6.QtGui import QImage, QPainter
from PySide6.QtWidgets import QApplication, QWidget, QStyleOptionButton
from core.fonts import apply_app_font
from core.skia_button import button_label
from app_ext.class_RomModeButton import RomModeButton, bind_rom_mode_button


class RomButtonTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        apply_app_font(cls.app)

    def setUp(self):
        self.root = QWidget()
        self.root.setAttribute(Qt.WA_DontShowOnScreen)
        self.root.resize(440, 200)
        self.root.show()

    def tearDown(self):
        self.root.hide()
        self.root.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)

    def button(self, count):
        button = bind_rom_mode_button(RomModeButton(self.root), count, lambda: True,
            lambda key: "⬤ ROM Tunggal" if count == 1 else "⬤ ⬤ ROM Ganda")
        button.setGeometry(10, 10, 180, 38)
        button.setStyleSheet("QPushButton {background:#303a42; border:1.5px solid #88c0d0;"
                            "border-radius:8px;color:#88c0d0;font-size:13px;font-weight:600;padding:8px 0;}")
        button.show()
        self.app.processEvents()
        return button

    def test_single_and_dual_icon_caption_groups_are_centered_and_fit_180px(self):
        for count in (1, 2):
            button = self.button(count)
            option = QStyleOptionButton()
            button.initStyleOption(option)
            image = button_label(button, option, button.devicePixelRatioF())
            columns = [x for x in range(image.width())
                       if any(image.pixelColor(x, y).alpha() > 40 for y in range(image.height()))]
            ratio = image.devicePixelRatioF()
            self.assertGreater(min(columns) / ratio, 15)
            self.assertLess(max(columns) / ratio, 165)
            self.assertLess(abs((min(columns) + max(columns)) / (2 * ratio) - 90), 3)
            self.assertNotIn("⬤", button.text())

    def test_qt_fallback_remains_visible_and_keeps_full_caption(self):
        button = self.button(1)
        with patch("app_ext.class_RomModeButton.render_button", return_value=None):
            self.assertFalse(button.grab().isNull())
        self.assertEqual(button.text(), "ROM Tunggal")
        self.assertEqual(button.font().family(), "Poppins")


if __name__ == "__main__":
    unittest.main()
