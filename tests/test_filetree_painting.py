# SPDX-License-Identifier: MPL-2.0
"""Dialog transparency and cached native Qt tree painting/input regressions."""
import math
import os
import unittest
from unittest.mock import patch

_NATIVE = os.environ.get("RAK_TEST_SKIA_GPU") == "1"
if not _NATIVE:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt, QCoreApplication, QEvent, QPoint, QPointF, QSignalBlocker, QAbstractAnimation
from PySide6.QtGui import QImage, QPainter, QWheelEvent, QMouseEvent, QColor, QFont
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget, QVBoxLayout, QTreeWidgetItem, QLabel, QAbstractItemView, QDialog
from main_ext.class_FileTree import FileTree
from main_ext.class_NameBadgeDelegate import IS_FOLDER_ROLE, SIZE_LABEL_ROLE
from main_ext.class_AppDialog import _Dlg
from main_ext.class_ThemeUi import theme
from core.skia_widgets import _WidgetGpuManager
from core.skia_gpu import gpu_backend_status


class TreePaintingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)
        from core.fonts import apply_app_font
        apply_app_font(cls.app)

    def setUp(self):
        self.root = QWidget()
        self.root.setAttribute(Qt.WA_DontShowOnScreen)
        self.root.resize(640, 320)
        self.root.setStyleSheet("background-color:#12151b;")
        self.tree = FileTree()
        QVBoxLayout(self.root).addWidget(self.tree)
        self.items = []
        with QSignalBlocker(self.tree):
            for number in range(50):
                item = QTreeWidgetItem([f"Qt cafe \u0627\u0644\u0639\u0631\u0628\u064a\u0629 file {number}.img", "u:object_r:system_file:s0", "rw-r--r--"])
                item.setData(0, Qt.UserRole, f"mount://test/{number}")
                item.setData(0, IS_FOLDER_ROLE, False)
                item.setData(0, SIZE_LABEL_ROLE, "123.0 MB")
                item.setData(0, Qt.UserRole + 1, "EROFS")
                item.setData(0, Qt.UserRole + 51, number % 3 == 0)
                item.setData(0, Qt.UserRole + 10, "blue" if number % 4 == 0 else None)
                item.setIcon(0, self.tree.icon_file)
                self.items.append(item)
            self.tree.addTopLevelItems(self.items)
        self.root.show()
        self.app.processEvents()
        self.app.processEvents()
        self.delegate = self.tree._badge_delegate

    def tearDown(self):
        self.tree.stop_background_work()
        self.root.hide()
        self.root.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        self.app.processEvents()

    def snapshot(self, cached=True):
        self.delegate._cache_enabled = cached
        return self.root.grab().toImage()

    def assert_native_pixels(self):
        before = self.snapshot(False)
        after = self.snapshot(True)
        self.assertEqual(before, after)
        return after

    def test_cached_rows_keep_exact_native_font_icon_badge_and_context_pixels(self):
        self.assert_native_pixels()
        hits = self.delegate._cell_cache_hits
        self.assert_native_pixels()
        self.assertGreater(self.delegate._cell_cache_hits, hits)

    def test_cache_invalidates_size_badge_branch_symlink_font_and_text_changes(self):
        before = self.assert_native_pixels()
        item = self.items[0]
        for role, value in ((SIZE_LABEL_ROLE, "999.0 GB"), (Qt.UserRole + 1, "SPARSE"),
                            (Qt.UserRole + 10, "red"), (Qt.UserRole + 51, False)):
            item.setData(0, role, value)
            after = self.assert_native_pixels()
            self.assertNotEqual(before, after, f"role {role}, value {value}")
            before = after
        item.setText(0, "Changed Qt filename")
        self.assertNotEqual(before, self.assert_native_pixels())
        font = QFont(item.font(0))
        font.setBold(True)
        item.setFont(0, font)
        self.assert_native_pixels()

    def test_selected_rtl_resized_and_pixel_scrolled_rows_keep_native_pixels(self):
        self.items[2].setSelected(True)
        self.tree.setCurrentItem(self.items[2])
        self.assert_native_pixels()
        self.tree.setColumnWidth(0, 313)
        self.assert_native_pixels()
        self.tree.verticalScrollBar().setValue(13)
        self.assert_native_pixels()
        self.tree.setLayoutDirection(Qt.RightToLeft)
        self.assert_native_pixels()

    def test_scaled_snapshot_uses_native_font_path_without_bitmap_resampling(self):
        for ratio in (1.25, 1.5, 2.0):
            def capture(cached):
                self.delegate._cache_enabled = cached
                image = QImage(math.ceil(self.root.width()*ratio), math.ceil(self.root.height()*ratio), QImage.Format_RGB32)
                image.setDevicePixelRatio(ratio)
                image.fill(Qt.black)
                self.root.render(image)
                return image
            self.assertEqual(capture(False), capture(True))

    def test_model_removal_reset_and_cache_limit_do_not_keep_stale_cells(self):
        self.snapshot()
        self.assertTrue(self.delegate._cell_cache)
        self.tree.takeTopLevelItem(0)
        self.assertFalse(self.delegate._cell_cache)
        self.assert_native_pixels()
        self.delegate._cell_cache_limit = 96 * 1024
        for value in range(0, 600, 30):
            self.tree.verticalScrollBar().setValue(value)
            self.snapshot()
        self.assertLessEqual(self.delegate._cell_cache_bytes, self.delegate._cell_cache_limit)
        self.assertLessEqual(len(self.delegate._cell_cache), 256)
        self.tree.clear()
        self.assertFalse(self.delegate._cell_cache)
        self.assertFalse(self.delegate._cell_keys)

    def test_hover_updates_only_old_and_new_full_width_rows(self):
        viewport = self.tree.viewport()
        with patch.object(viewport, "update", wraps=viewport.update) as update:
            for item in self.items[:3]:
                point = self.tree.visualItemRect(item).center()
                event = QMouseEvent(QEvent.MouseMove, QPointF(point), QPointF(viewport.mapToGlobal(point)), Qt.NoButton, Qt.NoButton, Qt.NoModifier)
                QApplication.sendEvent(viewport, event)
            self.assertTrue(update.call_args_list)
            for call in update.call_args_list:
                self.assertEqual(len(call.args), 1)
                rect = call.args[0]
                self.assertEqual(rect.width(), viewport.width())
                self.assertLessEqual(rect.height(), self.tree.visualItemRect(self.items[0]).height())

    def wheel(self, delta=-120, pixel=0):
        point = QPointF(100, 80)
        event = QWheelEvent(point, point, QPoint(0, pixel), QPoint(0, delta),
                            Qt.NoButton, Qt.NoModifier, Qt.ScrollUpdate, False)
        QApplication.sendEvent(self.tree.viewport(), event)
        return event

    def test_wheel_accumulates_smooth_pixel_motion_and_preserves_selection(self):
        self.assertEqual(self.tree.verticalScrollMode(), QAbstractItemView.ScrollPerPixel)
        self.items[1].setSelected(True)
        with patch.object(theme, "_animations_enabled", True):
            self.wheel()
            first = self.tree._wheel_target
            self.wheel()
            self.assertGreater(self.tree._wheel_target, first)
            QTest.qWait(180)
            self.assertEqual(self.tree.verticalScrollBar().value(), self.tree._wheel_target)
            self.assertTrue(self.items[1].isSelected())
            self.assertLess(self.tree.verticalScrollBar().value(), self.tree.verticalScrollBar().maximum())

    def test_keyboard_and_trackpad_cancel_pending_wheel_animation(self):
        with patch.object(theme, "_animations_enabled", True):
            self.wheel()
            self.assertEqual(self.tree._wheel_animation.state(), QAbstractAnimation.Running)
            QTest.keyClick(self.tree, Qt.Key_Down)
            self.assertEqual(self.tree._wheel_animation.state(), QAbstractAnimation.Stopped)
            self.wheel()
            self.wheel(delta=0, pixel=-8)
            self.assertEqual(self.tree._wheel_animation.state(), QAbstractAnimation.Stopped)

    def test_disabled_animation_uses_immediate_native_scrolling(self):
        with patch.object(theme, "_animations_enabled", False):
            self.wheel()
            self.assertGreater(self.tree.verticalScrollBar().value(), 0)
            self.assertEqual(self.tree._wheel_animation.state(), QAbstractAnimation.Stopped)

    def test_fast_jump_uses_native_pixels_without_allocating_unseen_cell_tiles(self):
        self.snapshot()
        self.tree.verticalScrollBar().setValue(900)
        self.assertFalse(self.delegate._cache_new_cells)
        before = self.delegate._cell_cache_bytes
        self.assert_native_pixels()
        self.assertEqual(self.delegate._cell_cache_bytes, before)
        QTest.qWait(150)
        self.assertTrue(self.delegate._cache_new_cells)

    def test_clearing_rows_erases_old_text_from_opaque_viewport(self):
        self.snapshot()
        self.tree.clear()
        image = self.tree.viewport().grab().toImage()
        expected = self.tree.palette().color(self.tree.backgroundRole())
        self.assertEqual(image.pixelColor(80, 15), expected)
        self.assertEqual(image.pixelColor(80, image.height()-15), expected)

    def test_translucent_dialog_has_clear_corners_full_message_and_native_keyboard(self):
        dialog = _Dlg(self.root, "Return", "Are you sure you want to return to the project picker?<br><br>All open editors and sessions will be closed.")
        dialog.setAttribute(Qt.WA_DontShowOnScreen)
        dialog._btn("Cancel", "neutral", 0)
        dialog._btn("Return", "primary", 1)
        dialog.adjustSize()
        dialog.show()
        self.app.processEvents()
        self.app.processEvents()
        label = dialog._box.findChild(QLabel)
        self.assertGreaterEqual(label.height(), label.heightForWidth(label.width()))
        manager = _WidgetGpuManager(self.app)
        self.assertFalse(manager._eligible(dialog))
        manager._prepare(dialog)
        self.assertFalse(hasattr(dialog, "_skia_surface_original"))
        image = QImage(dialog.size(), QImage.Format_ARGB32_Premultiplied)
        image.fill(Qt.transparent)
        dialog.render(image)
        for x, y in ((0, 0), (image.width()-1, 0), (0, image.height()-1), (image.width()-1, image.height()-1)):
            self.assertEqual(image.pixelColor(x, y).alpha(), 0)
        with patch.object(dialog, "done") as done:
            QTest.keyClick(dialog, Qt.Key_Escape)
            done.assert_called_once_with(QDialog.Rejected)
        dialog.hide()
        dialog.deleteLater()
        manager.deleteLater()

    @unittest.skipUnless(_NATIVE, "native GPU required")
    def test_gpu_dialog_round_box_has_no_opaque_root_after_repeated_resize(self):
        manager = _WidgetGpuManager(self.app)
        self.app.installEventFilter(manager)
        dialog = _Dlg(self.root, "Return", "Qt rounded dialog<br><br>Native fonts")
        dialog.setAttribute(Qt.WA_DontShowOnScreen)
        dialog._btn("Cancel", "neutral", 0)
        dialog.show()
        try:
            self.app.processEvents()
            self.app.processEvents()
            for height in (180, 210, 180):
                dialog.resize(dialog.width(), height)
                self.app.processEvents()
                self.app.processEvents()
                image = dialog.grab().toImage()
                self.assertEqual(image.pixelColor(image.width()-1, image.height()-1).alpha(), 0)
            self.assertFalse(hasattr(dialog, "_skia_surface_original"))
            self.assertTrue(hasattr(dialog._box, "_skia_widget_frame"))
            self.assertIsNone(gpu_backend_status()["last_error"])
        finally:
            self.app.removeEventFilter(manager)
            manager._timer.stop()
            manager.deleteLater()
            dialog.hide()
            dialog.deleteLater()


if __name__ == "__main__":
    unittest.main()
