# SPDX-License-Identifier: MPL-2.0
"""Keep optional viewer analysis out of startup and exercise its lazy paths."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class StartupImportTests(unittest.TestCase):
    def test_viewer_imports_do_not_load_optional_analysis_backends(self):
        script = """
import sys
from unittest.mock import patch
with patch('shutil.which',side_effect=AssertionError('Startup searched PATH')):
    import core.so_core
    import core.image_edit
    import ui.image_viewer
    import core.archive_document
for name in ('numpy','elftools','py7zr','rarfile'):
    assert name not in sys.modules, name+' loaded during startup'
"""
        result=subprocess.run([sys.executable,'-c',script],capture_output=True,text=True,timeout=20,
                              cwd=Path(__file__).resolve().parents[1])
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    def test_lazy_image_operations_preserve_pixels_and_fill_boundaries(self):
        import numpy as np
        from PySide6.QtGui import QImage,QColor
        from core.image_edit import _qimage_to_ndarray,_crop_canvas,_scanline_fill,_defringe
        image=QImage(2,2,QImage.Format_ARGB32);image.fill(QColor(10,20,30,100))
        array,owner=_qimage_to_ndarray(image)
        self.assertEqual(array[0,0].tolist(),[30,20,10,100])
        _defringe(owner,150);self.assertEqual(owner.pixelColor(0,0).alpha(),0)
        canvas=_crop_canvas(image,4,4)
        self.assertEqual(canvas.pixelColor(0,0).alpha(),0)
        mask=np.array([[True,False,True],[True,False,True]])
        self.assertEqual(_scanline_fill(mask,0,0).tolist(),[[True,False,False],[True,False,False]])

    def test_lazy_seven_zip_reader_still_reads_real_archive(self):
        import importlib.util
        if importlib.util.find_spec('py7zr') is None:self.skipTest('py7zr unavailable')
        import py7zr
        from core.archive_document import ArchiveReader
        with tempfile.TemporaryDirectory(prefix='rak-lazy-archive-') as temporary:
            root=Path(temporary);source=root/'source.txt';source.write_bytes(b'archive round trip\0')
            archive=root/'fixture.7z'
            with py7zr.SevenZipFile(archive,'w') as target:target.write(source,'inside.txt')
            with ArchiveReader(str(archive)) as reader:
                self.assertEqual(reader.archive_type,'7z')
                self.assertIn('inside.txt',reader.members)
                self.assertEqual(reader._read_member('inside.txt'),source.read_bytes())


if __name__=='__main__':unittest.main()
