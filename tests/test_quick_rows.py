# SPDX-License-Identifier: MPL-2.0
import unittest
from PySide6.QtCore import QPersistentModelIndex, Qt
from ui.rows import RowsModel


class RowsTests(unittest.TestCase):
    def test_size_refresh_preserves_indexes_and_never_resets_the_view(self):
        model = RowsModel([{"path":"C:/a","sizeLabel":"Calculating"}])
        index = QPersistentModelIndex(model.index(0))
        reset = []
        model.modelReset.connect(lambda: reset.append(True))
        model.replace([{"path":"C:/a","sizeLabel":"3 GB"}])
        self.assertTrue(index.isValid())
        self.assertEqual(index.data(int(Qt.UserRole)+1)["sizeLabel"],"3 GB")
        self.assertEqual(reset, [])

    def test_filter_pin_reorder_and_restore_keep_surviving_rows(self):
        model = RowsModel([{"path":f"C:/{name}"} for name in ("a","b","c")])
        surviving = QPersistentModelIndex(model.index(2))
        model.replace([{"path":"C:/c"},{"path":"C:/a"}])
        self.assertTrue(surviving.isValid())
        self.assertEqual(surviving.row(),0)
        self.assertEqual(model.motionKind,"remove")
        model.replace([{"path":"C:/c"},{"path":"C:/b"},{"path":"C:/a"}])
        self.assertEqual(model.motionKind,"insert")
        self.assertEqual(model.rowCount(),3)
        self.assertEqual(surviving.row(),0)

    def test_drive_clicks_during_motion_apply_the_latest_filter_after_finish(self):
        model=RowsModel([{"path":"a"},{"path":"b"},{"path":"c"}])
        model.aboutToReplace.connect(lambda *_: model.setAnimating(True))
        model.replace([{"path":"b"},{"path":"c"}])
        self.assertEqual(model.snapshot(),[{"path":"b"},{"path":"c"}])
        model.replace([{"path":"c"}])
        model.replace([{"path":"a"},{"path":"b"},{"path":"c"}])
        self.assertEqual(model.rowCount(),2)
        model.setAnimating(False)
        self.assertEqual(model.rowCount(),3)
        self.assertTrue(model.animating)
        model.setAnimating(False)
        self.assertFalse(model.animating)

    def test_typing_search_cancels_motion_and_applies_without_a_queued_filter(self):
        model=RowsModel([{"path":"a"},{"path":"b"}])
        model.aboutToReplace.connect(lambda before,after,animate: model.setAnimating(animate))
        model.cancelMotion.connect(lambda: model.setAnimating(False))
        model.replace([{"path":"a"}])
        model.replace([{"path":"b"}])
        model.replace([],animate=False)
        self.assertFalse(model.animating)
        self.assertEqual(model.rowCount(),0)
        model.setAnimating(False)
        self.assertEqual(model.rowCount(),0)


if __name__ == "__main__":
    unittest.main()
