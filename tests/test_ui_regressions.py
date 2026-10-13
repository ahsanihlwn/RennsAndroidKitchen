# SPDX-License-Identifier: MPL-2.0
"""Dialog centering/morph boundaries and visible native drag feedback."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

if os.environ.get("RAK_TEST_SKIA_GPU") != "1":
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt, QEvent, QCoreApplication, QPoint, QMimeData, QUrl
from PySide6.QtGui import QDragMoveEvent, QDragLeaveEvent, QPixmap
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget, QPushButton, QTreeWidgetItem, QPlainTextEdit, QDialog
from main_ext.class_ThemeUi import theme
from shiboken6 import isValid
from main_ext.class_AppDialog import AppDialog, _Dlg, _dialog_bounds
from main_ext import class_FileTree as tree_module
from main_ext.class_NameBadgeDelegate import IS_FOLDER_ROLE, SIZE_LABEL_ROLE
from app_ext import class_CreateProjectDialog as create_module
from app_ext.class_BlurOverlay import BlurOverlay
from app_ext.class_ButtonCardMorph import ButtonCardMorph


class UiRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def setUp(self):
        self.root = QWidget()
        self.root.setAttribute(Qt.WA_DontShowOnScreen)
        self.root.setGeometry(130, 80, 800, 600)
        self.root.setStyleSheet(theme.app_stylesheet())
        self.root.show()
        self.windows = []
        self.tree = None
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        if self.tree is not None:
            self.tree.stop_background_work()
        for window in self.windows:
            if isValid(window):
                window.hide()
                window.deleteLater()
        self.root.hide()
        self.root.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        self.app.processEvents()
        self.tmp.cleanup()

    def assert_centered(self, dialog):
        bounds = _dialog_bounds(dialog.parentWidget())
        self.assertTrue(bounds.contains(dialog.geometry()), (bounds, dialog.geometry()))
        self.assertLessEqual((bounds.center()-dialog.geometry().center()).manhattanLength(), 2)

    def test_fatal_and_nested_log_share_main_window_center(self):
        captured = []
        def present(parent, dialog, blur_kwargs=None):
            self.windows.append(dialog)
            dialog.setAttribute(Qt.WA_DontShowOnScreen)
            dialog.show()
            QTest.qWait(30)
            self.assert_centered(dialog)
            captured.append(dialog.geometry())
            if dialog.findChild(QPlainTextEdit) is None:
                dialog._default_btn.click()
            else:
                self.assertTrue(dialog.findChild(QPlainTextEdit).isReadOnly())
                self.assertTrue(dialog.geometry().contains(captured[0]))
            return QDialog.Accepted
        with patch.object(AppDialog, "_run", present):
            AppDialog.fatal_crash(self.root, "Traceback\n  Qt widget was deleted")
        self.assertEqual(len(captured), 2)

    def test_dialog_resize_remains_centered_and_corners_stay_clear(self):
        dialog = _Dlg(self.root, "Crash", "Native Qt message")
        self.windows.append(dialog)
        dialog.setAttribute(Qt.WA_DontShowOnScreen)
        dialog._btn("Close", "neutral", QDialog.Rejected)
        dialog.show()
        QTest.qWait(20)
        self.assert_centered(dialog)
        dialog.resize(dialog.width(), dialog.height()+30)
        QTest.qWait(20)
        self.assert_centered(dialog)
        self.assertEqual(dialog.grab().toImage().pixelColor(0, 0).alpha(), 0)

    def test_create_morph_glass_stays_inside_card_after_layout_request(self):
        self.root.btn_create = QPushButton("Create Project", self.root)
        self.root.btn_create.setGeometry(620, 545, 160, 34)
        self.root.btn_create.show()
        with patch.object(theme, "_animations_enabled", False), patch.object(theme, "_blur_enabled", True), patch.object(create_module, "_load_default_location", return_value=self.tmp.name):
            dialog = create_module.CreateProjectDialog(self.root, as_popup=True)
            self.windows.append(dialog)
            dialog.setAttribute(Qt.WA_DontShowOnScreen)
            overlay = BlurOverlay(self.root)
            dialog._overlay = overlay
            try:
                dialog.show()
                QTest.qWait(50)
                host = dialog._mask_host
                surface = host._morph_blur_surface
                self.assertIsNotNone(surface)
                expected = dialog._box.geometry().adjusted(1, 1, -1, -1)
                self.assertEqual(surface.geometry(), expected)
                QCoreApplication.sendEvent(host, QEvent(QEvent.LayoutRequest))
                QTest.qWait(20)
                self.assertEqual(surface.geometry(), expected)
                self.assertEqual(dialog.grab().toImage().pixelColor(0, 0).alpha(), 0)
                ButtonCardMorph.expand_backdrop(host)
                self.assertEqual(surface.geometry(), host.rect())
                ButtonCardMorph.settle_card(host, dialog._box)
                QTest.qWait(20)
                self.assertEqual(surface.geometry(), expected)
            finally:
                overlay._final_close()

    def make_tree(self, rows=2):
        self.tree = tree_module.FileTree()
        self.tree.setParent(self.root)
        self.tree.setGeometry(10, 10, 780, 390)
        self.tree.base = self.tmp.name
        source = Path(self.tmp.name, "source.txt")
        source.write_text("fixture", encoding="utf-8")
        destination = Path(self.tmp.name, "destination")
        destination.mkdir()
        items = []
        for number in range(rows):
            folder = number > 0
            path = destination if folder else source
            item = QTreeWidgetItem([path.name if rows == 2 else f"entry {number}", "context", "rw-r--r--"])
            item.setData(0, Qt.UserRole, str(path))
            item.setData(0, IS_FOLDER_ROLE, folder)
            item.setData(0, SIZE_LABEL_ROLE, "")
            item.setIcon(0, self.tree.icon_folder if folder else self.tree.icon_file)
            self.tree.addTopLevelItem(item)
            items.append(item)
        self.tree.show()
        self.app.processEvents()
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(str(source))])
        return items, mime

    def test_drag_target_repaints_opaque_viewport_and_clears_on_leave(self):
        items, mime = self.make_tree()
        self.root.drag_src_path = items[0].data(0, Qt.UserRole)
        before = self.tree.viewport().grab().toImage()
        point = self.tree.visualItemRect(items[1]).center()
        move = QDragMoveEvent(point, Qt.CopyAction | Qt.MoveAction, mime, Qt.LeftButton, Qt.NoModifier)
        with patch.object(self.tree.viewport(), "update", wraps=self.tree.viewport().update) as update:
            self.tree.dragMoveEvent(move)
            update.assert_called()
        self.app.processEvents()
        self.assertTrue(move.isAccepted())
        self.assertEqual(self.tree.drag_hover_items, {items[1]})
        self.assertNotEqual(before, self.tree.viewport().grab().toImage())
        self.tree.dragLeaveEvent(QDragLeaveEvent())
        self.app.processEvents()
        self.assertFalse(self.tree.drag_hover_items)
        self.assertEqual(before, self.tree.viewport().grab().toImage())

    def test_file_drag_has_icon_name_preview_without_grabbing_tree_rows(self):
        items, mime = self.make_tree()
        self.tree.setCurrentItem(items[0])
        items[0].setSelected(True)
        with patch.object(tree_module, "QDrag") as factory, patch.object(self.tree.viewport(), "grab", side_effect=AssertionError("Do not capture row UI")):
            drag = factory.return_value
            def execute(*_):
                preview = drag.setPixmap.call_args.args[0]
                self.assertIsInstance(preview, QPixmap)
                self.assertFalse(preview.isNull())
                self.assertLess(preview.deviceIndependentSize().width(), 350)
                self.assertEqual(os.path.normcase(os.path.normpath(drag.setMimeData.call_args.args[0].urls()[0].toLocalFile())),
                                 os.path.normcase(os.path.normpath(items[0].data(0, Qt.UserRole))))
                self.assertTrue(self.root.drag_src_paths)
                return Qt.IgnoreAction
            drag.exec.side_effect = execute
            self.tree.startDrag(Qt.CopyAction | Qt.MoveAction)
            drag.setHotSpot.assert_called_once()
        self.assertIsNone(self.root.drag_src_paths)
        self.assertFalse(self.tree.drag_hover_items)

    def test_mount_drag_uses_same_native_icon_name_preview(self):
        items, mime = self.make_tree()
        with patch.object(self.tree, "mount_ref", return_value=("image.img", "folder/source.txt")), patch("PySide6.QtGui.QDrag") as factory:
            self.tree._start_mount_drag([items[0]], Qt.CopyAction)
            preview = factory.return_value.setPixmap.call_args.args[0]
            self.assertFalse(preview.isNull())
            self.assertTrue(factory.return_value.setMimeData.call_args.args[0].hasFormat("application/x-rdt-mount"))
            factory.return_value.exec.assert_called_once_with(Qt.CopyAction)

    def test_autoscroll_retargets_destination_under_stationary_mouse(self):
        items, mime = self.make_tree(40)
        point = QPoint(120, self.tree.viewport().height()-12)
        event = QDragMoveEvent(point, Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier)
        self.tree.dragMoveEvent(event)
        old = self.tree.drag_hover_item
        self.tree._drag_scroll_speed = 30
        self.tree._drag_scroll_tick()
        self.assertIsNot(self.tree.drag_hover_item, old)
        self.assertIs(self.tree.drag_hover_item, self.tree.itemAt(point))
        self.tree._clear_drag_hover()

    def test_black_texture_overlay_tracks_parent_resize_in_both_directions(self):
        overlay = BlurOverlay(self.root, animate=False)
        try:
            for width, height in ((1000, 710), (550, 400), (900, 620)):
                self.root.resize(width, height)
                self.app.processEvents()
                self.assertEqual(overlay.geometry(), self.root.rect())
                image = overlay.grab().toImage()
                self.assertGreater(image.pixelColor(image.width()-2, image.height()-2).alpha(), 0)
        finally:
            overlay._final_close()


if __name__ == "__main__":
    unittest.main()
