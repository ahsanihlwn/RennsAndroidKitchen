# SPDX-License-Identifier: MPL-2.0
import hashlib
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from PySide6.QtCore import QCoreApplication
from core.hex_document import find_pattern,search_pattern,read_page
from ui.hex_viewer import HexDocument
from tools.audit_hex_viewer import fixture_bytes


class HexDocumentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QCoreApplication.instance() or QCoreApplication([])

    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory();self.path=Path(self.temporary.name)/'fixture.bin'
        self.path.write_bytes(fixture_bytes());self.document=HexDocument();self.errors=[]
        self.document.messageRequested.connect(lambda *args:self.errors.append(args))

    def tearDown(self):
        self.document.close();self.app.processEvents();self.temporary.cleanup()

    def wait(self,predicate):
        deadline=time.monotonic()+5
        while not predicate():
            self.assertLess(time.monotonic(),deadline,(self.document.status,self.errors))
            self.app.processEvents();time.sleep(.002)

    def load(self):self.document.load(str(self.path));self.wait(lambda:not self.document.busy);self.assertFalse(self.errors)

    def test_analysis_hashes_empty_and_final_page_keep_reference_data(self):
        self.load();self.assertEqual(self.document.stringCount,5)
        self.assertEqual(self.document.analysis['hashes']['sha256'],hashlib.sha256(fixture_bytes()).hexdigest())
        self.assertEqual(self.document.pageCount,5)
        self.document.jump('999999999');self.wait(lambda:not self.document.busy)
        self.assertEqual(self.document.page,4);self.assertTrue(self.document.pageText.startswith('00004000'))
        self.path.write_bytes(b'');self.document.reload();self.wait(lambda:not self.document.busy)
        self.assertEqual(self.document.page,0);self.assertEqual(self.document.pageText,'');self.assertEqual(self.document.pageCount,1)

    def test_streaming_search_overlap_and_original_reverse_limit(self):
        data=bytearray(65536*2);data[65534:65542]=b'PATTERNS';self.path.write_bytes(data)
        self.assertEqual(find_pattern(str(self.path),b'PATTERNS',0),65534)
        self.assertEqual(find_pattern(str(self.path),b'PATTERNS',65543,False),65534)
        self.assertEqual(search_pattern('50 41 54'),b'PAT');self.assertEqual(search_pattern('literal'),b'literal')
        cancel=threading.Event();cancel.set();self.assertEqual(find_pattern(str(self.path),b'PATTERNS',0,cancel=cancel),-1)

    def test_filter_minimum_export_and_string_jump_use_current_results(self):
        self.load();self.document.filterStrings('0x00001068');self.assertEqual(self.document.stringCount,1)
        self.document.openString(0);self.wait(lambda:not self.document.busy)
        self.assertEqual(self.document.page,1);self.assertIn('0x00001068',self.document.status)
        destination=self.path.with_suffix('.txt');self.document.exportStrings(str(destination));self.wait(lambda:not self.document.busy)
        self.assertEqual(destination.read_text(),'0x00001068\t[ASCII]\tSECOND_NATIVE_STRING\n')
        self.document.filterStrings('');self.document.setMinimumLength(20);self.wait(lambda:not self.document.busy)
        self.assertEqual([row['text'] for row in self.document.rows.snapshot()],['SECOND_NATIVE_STRING'])

    def test_closed_document_ignores_worker_completion(self):
        entered=threading.Event();release=threading.Event();finished=threading.Event()
        def delayed(path):
            entered.set();release.wait(3);finished.set();return {'strings':[],'format':{},'overview':'late'}
        with patch('ui.hex_viewer.analyze_binary',delayed):
            self.document.load(str(self.path));self.wait(entered.is_set)
            self.document.close();release.set();self.wait(finished.is_set)
            for _ in range(10):self.app.processEvents();time.sleep(.002)
        self.assertEqual(self.document.analysis,{})

    def test_copy_during_page_read_uses_clicked_page_not_previous_cache(self):
        self.load();entered=threading.Event();release=threading.Event();copied=[]
        self.document.copyRequested.connect(copied.append)
        def delayed(path,page,size):
            if page==1:entered.set();release.wait(3)
            return read_page(path,page,size)
        with patch('ui.hex_viewer.read_page',delayed):
            self.document.goPage(1);self.wait(entered.is_set)
            self.document.copy('ascii');self.document.goPage(2);release.set()
            self.wait(lambda:not self.document.busy)
        self.assertEqual(copied,[''.join(chr(b) if 32<=b<=126 else '.' for b in fixture_bytes()[4096:8192])])


if __name__=='__main__':unittest.main()
