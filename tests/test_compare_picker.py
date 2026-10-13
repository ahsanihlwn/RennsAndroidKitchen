# SPDX-License-Identifier: MPL-2.0
"""Actual private compare picker: native scrolling, names, selection and cleanup."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

_NATIVE = os.environ.get("RAK_TEST_SKIA_GPU") == "1"
if not _NATIVE:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt, QEvent, QCoreApplication, QPoint, QPointF, QObject
from PySide6.QtGui import QEnterEvent, QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget, QStyleOptionButton
from shiboken6 import isValid

from core.fonts import apply_app_font
from core.skia_widgets import install_widget_gpu_rendering
from app_ext.class_ButtonPressAnimation import install_button_press_animations
from main_ext.class_ThemeUi import theme
try:
    from rennsproject.main_ext.class_CompareFilesystem import FolderPickerDialog, _FolderColumn
except ModuleNotFoundError:
    FolderPickerDialog = _FolderColumn = None


@unittest.skipIf(FolderPickerDialog is None, "private compare bundle not installed")
class ComparePickerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)
        apply_app_font(cls.app)
        install_button_press_animations(cls.app)
        if _NATIVE:
            install_widget_gpu_rendering(cls.app)

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.base_a = Path(self.directory.name, "bedah")
        self.base_b = Path(self.directory.name, "target")
        self.base_a.mkdir()
        self.base_b.mkdir()
        self.root = QWidget()
        self.root.setAttribute(Qt.WA_DontShowOnScreen)
        self.root.resize(1000, 760)
        self.root.show()
        self.dialog = None

    def tearDown(self):
        if self.dialog is not None:
            self.dialog.hide()
            self.dialog.deleteLater()
        self.root.hide()
        self.root.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        self.app.processEvents()
        self.directory.cleanup()

    def picker(self, count=12, second=True):
        self.entries = [(f"system_{index:03}", "EROFS" if index % 2 else "") for index in range(count)]
        with patch.object(_FolderColumn, "_collect_partitions", return_value=self.entries) as scan:
            self.dialog = FolderPickerDialog(str(self.base_a), str(self.base_b) if second else None, self.root)
            self.assertEqual(scan.call_count, 2)  # Once per column, no signal-triggered duplicate scans.
        self.dialog.setAttribute(Qt.WA_DontShowOnScreen)
        self.dialog.show()
        QTest.qWait(80)
        self.dialog.grab()
        return self.dialog

    def wheel(self, area, pixels=0, angle=0):
        position = QPointF(area.viewport().rect().center())
        event = QWheelEvent(position, QPointF(area.viewport().mapToGlobal(position.toPoint())),
                            QPoint(0, pixels), QPoint(0, angle), Qt.NoButton, Qt.NoModifier, Qt.ScrollUpdate, False)
        QApplication.sendEvent(area.viewport(), event)
        return event

    def test_both_columns_use_folder_names_and_missing_second_source_is_disabled(self):
        dialog = self.picker(second=False)
        for column in (dialog._col_a, dialog._col_b):
            self.assertEqual(column._tab1.text(), "bedah")
            self.assertEqual(column._tab1.toolTip(), str(self.base_a))
            self.assertEqual(column._tab2.text(), "—")
            self.assertFalse(column._tab2.isEnabled())

    def test_long_folder_names_fit_fixed_dialog_and_keep_full_tooltips(self):
        self.base_a = Path(self.directory.name, "source_firmware_" + "android_" * 7)
        self.base_b = Path(self.directory.name, "target_firmware_" + "vendor_" * 7)
        self.base_a.mkdir()
        self.base_b.mkdir()
        dialog = self.picker()
        for column in (dialog._col_a, dialog._col_b):
            for tab, base in ((column._tab1, self.base_a), (column._tab2, self.base_b)):
                self.assertEqual(tab.text(), base.name)
                self.assertEqual(tab.toolTip(), str(base))
                self.assertTrue(column.rect().contains(tab.geometry()))
                option = QStyleOptionButton()
                tab.initStyleOption(option)
                self.assertIn("…", option.text)
        self.assertEqual(dialog.width(), 580)

    def test_switching_source_deletes_previous_cards_and_comparison_keeps_actual_paths(self):
        dialog = self.picker()
        column = dialog._col_a
        previous = list(column._cards)
        with patch.object(_FolderColumn, "_collect_partitions", return_value=self.entries):
            QTest.mouseClick(column._tab2, Qt.LeftButton)
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        self.app.processEvents()
        self.assertTrue(all(not isValid(card) for card in previous))
        self.assertEqual(column.selected_folder(), (str(self.base_b / "system_000"), str(self.base_b)))
        QTest.mouseClick(column._cards[2], Qt.LeftButton)
        dialog._on_compare()
        self.assertEqual(dialog.result_a, str(self.base_b / "system_002"))
        self.assertEqual(dialog.result_b, str(self.base_b / "system_000"))
        self.assertEqual(dialog.result_tree_a, str(self.base_b))
        self.assertEqual(dialog.result_tree_b, str(self.base_b))

    def test_hover_selection_and_scroll_do_not_repolish_styles_or_move_footer(self):
        dialog = self.picker(128)
        QTest.qWait(150)

        class Counter(QObject):
            changes = 0
            details = []
            def eventFilter(self, obj, event):
                if event.type() == QEvent.StyleChange:
                    self.changes += 1
                    self.details.append((type(obj).__name__, obj.objectName()))
                return False
        counter = Counter()
        self.app.installEventFilter(counter)
        try:
            card = dialog._col_a._cards[1]
            point = QPointF(card.rect().center())
            QApplication.sendEvent(card, QEnterEvent(point, point, point))
            QTest.mouseClick(card, Qt.LeftButton)
            QApplication.sendEvent(card, QEvent(QEvent.Leave))
            area = dialog._col_a._scroll
            before = dialog.grab().toImage()
            with patch.object(theme, "_animations_enabled", False):
                self.assertTrue(self.wheel(area, pixels=-180).isAccepted())
                self.assertEqual(area.verticalScrollBar().value(), 180)
                self.assertTrue(self.wheel(area, angle=-120).isAccepted())
            self.app.processEvents()
            after = dialog.grab().toImage()
            ratio = after.devicePixelRatioF()
            top = dialog._btn_cmp.mapTo(dialog, QPoint()).y()
            from PySide6.QtCore import QRect
            footer = QRect(0, round(top * ratio), after.width(), after.height() - round(top * ratio))
            self.assertEqual(before.copy(footer), after.copy(footer))
            self.assertEqual(counter.changes, 0, counter.details)
            self.assertFalse(dialog._col_a._cards[0].visibleRegion().intersects(dialog._col_a._cards[0].rect()))
        finally:
            self.app.removeEventFilter(counter)
