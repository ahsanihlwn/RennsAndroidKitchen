# SPDX-License-Identifier: MPL-2.0
"""Native Qt typography/input and Skia widget surface regressions."""
import os
import types
import unittest
from unittest.mock import patch

_NATIVE = os.environ.get("RAK_TEST_SKIA_GPU") == "1"
if not _NATIVE:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent, QPoint, Qt
from PySide6.QtGui import QColor, QPainter, QPixmap, QBrush, QLinearGradient
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication, QWidget, QLabel, QLineEdit, QVBoxLayout, QScrollArea,
    QScrollBar, QSlider, QProgressBar, QPushButton, QStyle, QGraphicsOpacityEffect,
)
from core import skia_widgets as widgets
from core.skia_button import _source_sheet, _style_revision
from core.skia_gpu import gpu_backend_status, GpuOpacityEffect
from app_ext import class_BlurOverlay as blur


class WidgetSurfaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def setUp(self):
        self.root = QWidget()
        self.root.setAttribute(Qt.WA_DontShowOnScreen)
        self.root.resize(360, 240)
        self.manager = widgets._WidgetGpuManager(self.app)

    def tearDown(self):
        self.app.removeEventFilter(self.manager)
        self.manager._timer.stop()
        self.manager._pending.clear()
        self.manager.deleteLater()
        self.root.hide()
        self.root.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        self.app.processEvents()

    def prepare(self, widget):
        widget.ensurePolished()
        self.manager._prepare(widget)
        return widget

    def test_inline_qss_preserves_original_22px_header_font_and_layout(self):
        layout = QVBoxLayout(self.root)
        heading = QLabel("My Projects", self.root)
        original = "font-size: 22px; font-weight: 600; background:transparent; border:none;"
        heading.setStyleSheet(original)
        # Use a visible border to exercise surface suppression on this label.
        heading.setStyleSheet(original + "border:1px solid white;")
        original = heading.styleSheet()
        layout.addWidget(heading)
        self.root.show()
        self.app.processEvents()
        before = (heading.font().toString(), heading.sizeHint(), heading.geometry(), heading.contentsRect())
        self.prepare(heading)
        self.app.processEvents()
        self.assertEqual(heading.font().pixelSize(), 22)
        self.assertEqual(before, (heading.font().toString(), heading.sizeHint(), heading.geometry(), heading.contentsRect()))
        self.assertEqual(_source_sheet(heading), original)
        self.assertIn("* {" + original + "}", heading.styleSheet())

    def test_scope_override_keeps_children_and_padding(self):
        card = QWidget(self.root)
        card.setObjectName("card")
        card.setStyleSheet('QWidget#card {background:#303540; border:1px solid white; border-radius:10px; padding:4px;} QLabel {font-size:18px; color:yellow; background:transparent; border:none;}')
        label = QLabel("Qt text", card)
        card.ensurePolished()
        label.ensurePolished()
        before = (card.contentsRect(), label.font().toString(), label.palette().cacheKey())
        self.prepare(card)
        self.assertEqual(before[:2], (card.contentsRect(), label.font().toString()))
        self.assertEqual(label.font().pixelSize(), 18)
        self.assertEqual(widgets.button_surface(card).background, QColor("#303540"))

    def test_external_theme_rule_replaces_original_surface(self):
        card = self.prepare(QWidget(self.root))
        card.setStyleSheet("background:red; border:1px solid white; border-radius:8px;")
        self.prepare(card)
        self.assertEqual(widgets.button_surface(card).background, QColor("red"))
        card.setStyleSheet("background:blue; border:2px solid yellow; border-radius:12px;")
        self.prepare(card)
        self.assertEqual(_source_sheet(card), "background:blue; border:2px solid yellow; border-radius:12px;")
        surface = widgets.button_surface(card)
        self.assertEqual((surface.background, surface.width, surface.radius), (QColor("blue"), 2, 12))

    def test_unstyled_widget_does_not_schedule_preparation_on_every_paint(self):
        label = QLabel("Plain Qt", self.root)
        self.prepare(label)
        self.assertEqual(label._skia_no_surface_revision, _style_revision())
        with patch.object(self.manager, "_queue") as queue:
            for _ in range(20):
                self.manager.eventFilter(label, QEvent(QEvent.Paint))
            queue.assert_not_called()
        label.setStyleSheet("background:red; border:none;")
        self.manager.eventFilter(label, QEvent(QEvent.StyleChange))
        with patch.object(self.manager, "_queue") as queue:
            self.manager.eventFilter(label, QEvent(QEvent.Paint))
            queue.assert_called_once_with(label)

    def test_custom_painter_buttons_and_native_viewports_are_preserved(self):
        class Custom(QWidget):
            def paintEvent(self, event):
                painter = QPainter(self)
                painter.fillRect(self.rect(), Qt.magenta)
        self.assertFalse(self.manager._eligible(Custom(self.root)))
        self.assertFalse(self.manager._eligible(QPushButton(self.root)))
        area = QScrollArea(self.root)
        self.assertFalse(self.manager._eligible(area.viewport()))

    def test_app_filter_does_not_pin_base_wrapper_during_scrollbar_construction(self):
        self.app.installEventFilter(self.manager)
        area = QScrollArea(self.root)
        bar = area.verticalScrollBar()
        self.assertIsInstance(bar, QScrollBar)
        bar.setRange(0, 50)
        bar.setValue(23)
        self.assertEqual(bar.value(), 23)

    def test_opaque_effect_keeps_native_pixels_without_extra_gpu_pass(self):
        label = QLabel("Native Qt text", self.root)
        label.resize(240, 36)
        label.setStyleSheet("background:#303540; color:white; font-size:16px;")
        before = label.grab().toImage()
        effect = GpuOpacityEffect(label)
        effect.setOpacity(1)
        label.setGraphicsEffect(effect)
        with patch("core.skia_gpu.gpu_available", side_effect=AssertionError("Opaque effect must not initialize GPU")):
            after = label.grab().toImage()
        self.assertEqual(before, after)
        self.assertIsNone(effect._frame)

    def test_opacity_mask_keeps_native_qt_gradient_and_font_pixels(self):
        label = QLabel("Native Qt text", self.root)
        label.resize(240, 36)
        label.setAttribute(Qt.WA_TranslucentBackground)
        label.setStyleSheet("background:#303540; color:white; font-size:16px;")
        gradient = QLinearGradient(0, 0, 240, 0)
        gradient.setColorAt(0, Qt.transparent)
        gradient.setColorAt(1, Qt.black)
        mask = QBrush(gradient)
        native = QGraphicsOpacityEffect(label)
        native.setOpacity(.75)
        native.setOpacityMask(mask)
        label.setGraphicsEffect(native)
        before = label.grab().toImage()
        effect = GpuOpacityEffect(label)
        effect.setOpacity(.75)
        effect.setOpacityMask(mask)
        label.setGraphicsEffect(effect)
        with patch("core.skia_gpu.gpu_available", side_effect=AssertionError("Qt owns opacity mask")):
            after = label.grab().toImage()
        self.assertEqual(before, after)
        self.assertIsNone(effect._frame)

    def test_failed_frame_restores_qss_once_and_uses_native_fallback(self):
        card = QWidget(self.root)
        original = "background:red; border:1px solid white; border-radius:8px;"
        card.setStyleSheet(original)
        self.prepare(card)
        with patch.object(widgets, "render_widget_surface", return_value=None):
            self.manager.eventFilter(card, QEvent(QEvent.Paint))
        self.app.processEvents()
        self.assertEqual(card.styleSheet(), original)
        self.assertFalse(self.manager._eligible(card))
        with patch.object(self.manager, "_queue") as queue:
            self.manager.eventFilter(card, QEvent(QEvent.Paint))
            queue.assert_not_called()

    def test_slider_uses_native_geometry_and_busy_progress_stays_native(self):
        slider = QSlider(Qt.Horizontal, self.root)
        slider.resize(220, 26)
        slider.setStyleSheet('QSlider {background:transparent; border:none;} QSlider::groove:horizontal {background:#303540; height:4px; border:none; border-radius:2px;} QSlider::handle:horizontal {background:white; width:12px; margin:-4px 0; border:none; border-radius:6px;} QSlider::sub-page:horizontal {background:#87cedc; border:none; border-radius:2px;}')
        slider.setValue(25)
        option = widgets._option(slider)
        layers = widgets._layers(slider, option)
        expected = slider.style().subControlRect(QStyle.CC_Slider, option, QStyle.SC_SliderHandle, slider)
        self.assertEqual(layers[-1][1].toRect(), expected)
        slider.setTickPosition(QSlider.TicksBelow)
        self.assertIsNone(widgets._layers(slider, widgets._option(slider)))
        progress = QProgressBar(self.root)
        progress.setStyleSheet('QProgressBar {background:#303540; border:none;} QProgressBar::chunk {background:#87cedc; border:none;}')
        progress.setRange(0, 0)
        self.assertIsNone(widgets._layers(progress, widgets._option(progress)))

    @unittest.skipUnless(_NATIVE, "native GPU required")
    def test_native_input_text_selection_caret_and_keyboard_remain_qt(self):
        edit = QLineEdit("Qt cafe text", self.root)
        edit.setGeometry(20, 20, 280, 36)
        edit.setStyleSheet('QLineEdit {background:#303540; border:1px solid #656b76; border-radius:8px; padding:4px 12px; color:white; font-size:16px; selection-background-color:#87cedc; selection-color:black;}')
        self.root.show()
        edit.setFocus()
        edit.setSelection(3, 4)
        self.app.processEvents()
        before = edit.grab().toImage().copy(15, 8, 240, 20)
        geometry = edit.geometry()
        font = edit.font().toString()
        self.prepare(edit)
        self.app.installEventFilter(self.manager)
        self.app.processEvents()
        after = edit.grab().toImage().copy(15, 8, 240, 20)
        difference = max(abs(a - b) for a, b in zip(bytes(before.constBits()), bytes(after.constBits())))
        self.assertLessEqual(difference, 1)
        self.assertEqual(edit.geometry(), geometry)
        self.assertEqual(edit.font().toString(), font)
        self.assertEqual(edit.selectedText(), "cafe")
        QTest.keyClicks(edit, "new")
        self.assertEqual(edit.text(), "Qt new text")
        self.assertEqual(edit.cursorPosition(), 6)
        self.assertIsNotNone(edit._skia_widget_frame)
        self.assertIsNone(edit.graphicsEffect())

    @unittest.skipUnless(_NATIVE, "native GPU required")
    def test_progress_label_cache_refreshes_format_font_and_visibility(self):
        progress = QProgressBar(self.root)
        progress.resize(260, 34)
        progress.setStyleSheet('QProgressBar {background:#303540; color:white; border:1px solid #656b76; border-radius:8px; text-align:center;} QProgressBar::chunk {background:#87cedc; border:none; border-radius:8px;}')
        progress.setValue(50)
        self.prepare(progress)
        first = widgets.render_widget_surface(progress)
        self.assertIsNotNone(first, str(gpu_backend_status()))
        self.assertIs(first, widgets.render_widget_surface(progress))
        progress.setFormat("Qt %v of %m")
        second = widgets.render_widget_surface(progress)
        self.assertNotEqual(first, second)
        font = progress.font()
        font.setPixelSize(20)
        progress.setFont(font)
        third = widgets.render_widget_surface(progress)
        self.assertNotEqual(second, third)
        progress.setTextVisible(False)
        self.assertNotEqual(third, widgets.render_widget_surface(progress))
        self.assertIsNone(gpu_backend_status()["last_error"])

    @unittest.skipUnless(_NATIVE, "native GPU required")
    def test_popup_card_vectors_preserve_backdrop_tint_and_cached_idle_frame(self):
        theme = types.SimpleNamespace(blur_enabled=True, BG_MODAL="#252932")
        with patch.object(blur, "_th", theme):
            card = blur.BlurCard(self.root, tint=QColor(0, 0, 0, 64), corner=10)
            card.resize(120, 60)
            backdrop = QPixmap(120, 60)
            backdrop.fill(QColor("#a0b0c0"))
            first = blur._gpu_card_frame(card, backdrop, QPoint())
            self.assertIsNotNone(first, str(gpu_backend_status()))
            self.assertIs(first, blur._gpu_card_frame(card, backdrop, QPoint()))
            self.assertEqual(first.pixelColor(0, 0).alpha(), 0)
            self.assertEqual(first.pixelColor(60, 30), QColor(120, 132, 144))
            card._flat = True
            second = blur._gpu_card_frame(card, backdrop, QPoint())
            self.assertEqual(second.pixelColor(60, 30), QColor("#252932"))
            self.assertIsNone(gpu_backend_status()["last_error"])


if __name__ == "__main__":
    unittest.main()
