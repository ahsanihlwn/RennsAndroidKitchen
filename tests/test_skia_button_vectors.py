# SPDX-License-Identifier: MPL-2.0
"""Regression for vector borders, fractional widths and Qt-only labels."""
import os
import unittest
from unittest.mock import patch

_NATIVE = os.environ.get("RAK_TEST_SKIA_GPU") == "1"
if not _NATIVE:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt, QCoreApplication, QEvent
from PySide6.QtGui import QColor, QImage, QPainter, QIcon, QPixmap
from PySide6.QtWidgets import QApplication, QPushButton, QLabel, QWidget, QStyle, QStyleOptionButton

from core.skia_button import button_surface, button_label, render_button
from core.skia_gpu import gpu_backend_status
from app_ext.class_ButtonPressAnimation import install_button_press_animations, PressScaleEffect


class ButtonVectorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def setUp(self):
        self.root = QWidget()
        self.button = QPushButton("Qt fi caf\u00e9 \u0627\u0644\u0639\u0631\u0628\u064a\u0629", self.root)
        self.button.resize(240, 38)
        self.button.setAutoDefault(False)
        self.button.setStyleSheet("QPushButton { background: transparent; border:1.5px solid white; border-radius:8px; }")
        self.button.ensurePolished()

    def tearDown(self):
        self.root.hide()
        self.root.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        self.app.processEvents()

    def option(self):
        option = QStyleOptionButton()
        self.button.initStyleOption(option)
        return option

    def test_fractional_border_and_qss_alpha_are_preserved(self):
        self.button.setStyleSheet("QPushButton { background:rgba(135,206,220,46); border:1.5px solid rgba(135,206,220,128); border-radius:8px; }")
        surface = button_surface(self.button)
        self.assertEqual(surface.width, 1.5)
        self.assertEqual(surface.radius, 8)
        self.assertEqual(surface.background.getRgb(), (135, 206, 220, 46))
        self.assertEqual(surface.border.alpha(), 128)

    def test_scope_overrides_parent_id_specificity(self):
        self.root.setStyleSheet("QPushButton#special { background:red; border:2px solid blue; border-radius:12px; }")
        self.button.setObjectName("special")
        surface = button_surface(self.button)
        self.assertEqual(surface.background.alpha(), 0)
        self.assertEqual(surface.width, 1.5)
        self.assertEqual(surface.radius, 8)

    def test_property_and_combined_pseudo_state_specificity(self):
        self.button.setProperty("role", "primary")
        self.button.setStyleSheet('QPushButton { background:red; border:none; } QPushButton[role="primary"] { background:blue; } QPushButton:checked { background:green; } QPushButton[role="primary"]:checked:hover { background:yellow; }')
        option = self.option()
        self.assertEqual(button_surface(self.button, option).background, QColor("blue"))
        option.state |= QStyle.State_On | QStyle.State_MouseOver
        self.assertEqual(button_surface(self.button, option).background, QColor("yellow"))

    def test_disabled_rule_and_negated_enabled_state(self):
        self.button.setStyleSheet("QPushButton { background:red; border:none; } QPushButton:disabled { background:blue; } QPushButton:!enabled { border:1px solid yellow; }")
        self.button.setEnabled(False)
        surface = button_surface(self.button)
        self.assertEqual(surface.background, QColor("blue"))
        self.assertEqual(surface.border, QColor("yellow"))

    def test_descendant_and_direct_child_selectors(self):
        self.root.setObjectName("host")
        self.button.setStyleSheet("QWidget#host > QPushButton {background:blue; border:none;} QWidget#other QPushButton {background:red;}")
        self.assertEqual(button_surface(self.button).background, QColor("blue"))

    def test_surface_cache_refreshes_when_widget_or_ancestor_name_changes(self):
        self.button.setStyleSheet("QPushButton {background:red; border:none;} QPushButton#special {background:blue;} QWidget#host QPushButton {background:yellow;}")
        self.assertEqual(button_surface(self.button).background, QColor("red"))
        self.button.setObjectName("special")
        self.assertEqual(button_surface(self.button).background, QColor("blue"))
        self.button.setObjectName("")
        self.root.setObjectName("host")
        self.assertEqual(button_surface(self.button).background, QColor("yellow"))

    def test_unsupported_decorations_keep_native_qt_path(self):
        for extra in ("background:qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 red,stop:1 blue);",
                      "border-bottom:2px solid red;", "border-image:url(missing.png);",
                      "border-radius:8px 4px;"):
            with self.subTest(extra=extra):
                self.button.setStyleSheet("QPushButton {background:blue; border:1px solid white;" + extra + "}")
                self.assertIsNone(button_surface(self.button))

    def test_custom_button_paint_is_not_replaced_by_qss_surface(self):
        class CustomButton(QPushButton):
            def paintEvent(self, event):
                painter = QPainter(self)
                painter.fillRect(self.rect(), Qt.magenta)

        button = CustomButton(self.root)
        button.resize(50, 30)
        button.setStyleSheet("QPushButton {background:transparent; border:none;}")
        self.assertIsNone(render_button(button))
        image = button.grab().toImage()
        self.assertEqual(image.pixelColor(25, 15), QColor(Qt.magenta))

    def test_qt_label_matches_native_transparent_style_including_states_and_rtl(self):
        pixmap = QPixmap(12, 12)
        pixmap.fill(Qt.yellow)
        self.button.setIcon(QIcon(pixmap))
        for direction in (Qt.LeftToRight, Qt.RightToLeft):
            for state in (QStyle.State_None, QStyle.State_Sunken, QStyle.State_On, QStyle.State_MouseOver):
                for ratio in (1.0, 1.5, 2.0):
                    with self.subTest(direction=direction, state=state, dpr=ratio):
                        self.button.setLayoutDirection(direction)
                        self.button.setStyleSheet("QPushButton { background:transparent; border:1.5px solid transparent; border-radius:8px; padding:2px 14px; color:#87cedc; font-size:13px; font-weight:600; } QPushButton:hover {color:yellow;} QPushButton:pressed {color:red;}")
                        self.button.ensurePolished()
                        option = self.option()
                        option.state |= state
                        source = button_label(self.button, option, ratio)
                        expected = QImage(source.size(), source.format())
                        expected.setDevicePixelRatio(ratio)
                        expected.fill(Qt.transparent)
                        painter = QPainter(expected)
                        painter.setFont(self.button.font())
                        painter.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing | QPainter.SmoothPixmapTransform)
                        self.button.style().drawControl(QStyle.CE_PushButton, option, painter, self.button)
                        painter.end()
                        self.assertEqual(bytes(source.constBits()), bytes(expected.constBits()))
                        self.assertEqual(source.pixelColor(0, 0).alpha(), 0)

    def test_label_cache_refreshes_after_theme_font_text_and_property_changes(self):
        self.button.setStyleSheet('QPushButton {background:transparent; border:none; color:white;} QPushButton[role="primary"] {color:yellow;}')
        first = button_label(self.button, self.option(), 1)
        self.assertIs(first, button_label(self.button, self.option(), 1))
        self.button.setProperty("role", "primary")
        self.button.style().unpolish(self.button)
        self.button.style().polish(self.button)
        second = button_label(self.button, self.option(), 1)
        self.assertNotEqual(first, second)
        self.button.setText("Changed")
        third = button_label(self.button, self.option(), 1)
        self.assertNotEqual(second, third)
        font = self.button.font()
        font.setPixelSize(20)
        self.button.setFont(font)
        self.assertNotEqual(third, button_label(self.button, self.option(), 1))

    def test_dropdown_child_text_and_arrow_are_qt_rendered_and_cached_during_press(self):
        self.button.setText("")
        text = QLabel("English", self.button)
        text.setGeometry(12, 3, 160, 30)
        text.setStyleSheet("color:white; background:transparent; border:none; font-size:12px;")
        arrow = QLabel("\u2304", self.button)
        arrow.setGeometry(200, 3, 20, 30)
        arrow.setStyleSheet(text.styleSheet())
        self.root.show()
        self.app.processEvents()
        label = button_label(self.button, self.option(), 1, include_children=True)
        self.assertTrue(any(label.pixelColor(x, y).alpha() for y in range(8, 25) for x in range(12, 170)))
        self.assertTrue(any(label.pixelColor(x, y).alpha() for y in range(3, 33) for x in range(200, 220)))
        with patch.object(text, "render", wraps=text.render) as render:
            for _ in range(5):
                self.assertIs(label, button_label(self.button, self.option(), 1, include_children=True))
            render.assert_not_called()
        text.setText("Tree")
        changed = button_label(self.button, self.option(), 1, include_children=True)
        self.assertNotEqual(label, changed)
        arrow.hide()
        self.assertNotEqual(changed, button_label(self.button, self.option(), 1, include_children=True))
        parent_only = button_label(self.button, self.option(), 1, include_children=False)
        self.assertFalse(any(bytes(parent_only.constBits())))

    @unittest.skipUnless(_NATIVE, "native GPU required for coverage proof")
    def test_gpu_corners_have_coverage_and_fractional_border_width_at_all_dpi(self):
        self.button.setText("")
        for ratio in (1.0, 1.25, 1.5, 2.0):
            with self.subTest(dpr=ratio), patch.object(self.button, "devicePixelRatioF", return_value=ratio):
                image = render_button(self.button)
                self.assertIsNotNone(image, str(gpu_backend_status()))
                self.assertEqual(image.pixelColor(0, 0).alpha(), 0)
                corner = {image.pixelColor(x, y).alpha() for y in range(int(10 * ratio)) for x in range(int(10 * ratio))}
                self.assertGreater(len(corner - {0, 255}), 8)
                # Straight border integrates to the exact fractional width.
                coverage = sum(image.pixelColor(x, int(19 * ratio)).alpha() / 255 for x in range(8))
                self.assertAlmostEqual(coverage, 1.5 * ratio, delta=.01)
                self.assertEqual(image.pixelColor(int(20 * ratio), int(19 * ratio)).alpha(), 0)

    @unittest.skipUnless(_NATIVE, "native GPU required for ordinary button integration")
    def test_plain_button_idle_and_press_effect_use_vector_frames(self):
        install_button_press_animations(self.app)
        self.root.resize(260, 70)
        self.root.show()
        self.app.processEvents()
        self.app.processEvents()
        self.button.grab()
        self.assertIsNone(self.button.graphicsEffect())
        self.assertIsNotNone(getattr(self.button, "_skia_vector_frame", None))
        self.button._button_press_feedback._ensure_effect()
        self.button.graphicsEffect().scale = .96
        self.button.grab()
        self.assertEqual(self.button._skia_vector_frame[0][3], .96)
        self.assertEqual(gpu_backend_status()["backend"], "skia-gpu")
        self.assertIsNone(gpu_backend_status()["last_error"])


if __name__ == "__main__":
    unittest.main()
