# SPDX-License-Identifier: MPL-2.0
import tempfile
from pathlib import Path
import unittest

from PySide6.QtCore import QUrl
from PySide6.QtGui import QColor,QImage
from core.agent_attachments import local_path,reference,save_clipboard_image


class AttachmentTests(unittest.TestCase):
    def test_paths_with_spaces_unicode_and_hash_remain_local_references(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'file # ünicode.txt';path.write_text('content must not be embedded')
            resolved=local_path(QUrl.fromLocalFile(str(path)).toString(),folder)
            row=reference(resolved,external=True)
            self.assertEqual(row['path'],str(path));self.assertEqual(row['name'],path.name)
            self.assertNotIn('content',row);self.assertTrue(path.exists())
            with self.assertRaises(ValueError):local_path('https://example.com/image.png',folder)

    def test_clipboard_png_roundtrip_preserves_pixels_and_transparency(self):
        with tempfile.TemporaryDirectory() as folder:
            image=QImage(23,17,QImage.Format_ARGB32);image.fill(QColor(34,90,150,71))
            path=save_clipboard_image(image,Path(folder)/'attachments')
            saved=QImage(path)
            self.assertEqual(saved.size(),image.size());self.assertEqual(saved.pixelColor(2,3),image.pixelColor(2,3))
            self.assertEqual(reference(path)['kind'],'image')


if __name__=='__main__':unittest.main()
