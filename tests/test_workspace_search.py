# SPDX-License-Identifier: MPL-2.0
from pathlib import Path
import tempfile
import time
import unittest
import threading
from unittest.mock import patch

from PySide6.QtCore import QCoreApplication
from PySide6.QtTest import QTest
from ui.search import WorkspaceSearch

_app=QCoreApplication.instance() or QCoreApplication([])


class WorkspaceSearchTests(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory(prefix='rak-search-')
        self.root=Path(self.temporary.name)
        (self.root/'config').mkdir();(self.root/'first').mkdir();(self.root/'other').mkdir()
        (self.root/'first'/'fixture-one.txt').write_text('one')
        (self.root/'other'/'fixture-two.txt').write_text('two')
        self.model=WorkspaceSearch()

    def tearDown(self):
        self.model.close();QTest.qWait(50);self.temporary.cleanup()

    def wait(self,predicate):
        deadline=time.monotonic()+5
        while not predicate():
            if time.monotonic()>deadline:
                import faulthandler
                faulthandler.dump_traceback()
                self.fail(f'Search did not finish: busy={self.model.busy} rows={self.model.rows.snapshot()} error={self.model.error}')
            QTest.qWait(10)
            time.sleep(.001)

    def test_priority_separator_and_global_search_are_distinct(self):
        self.model.prepare(str(self.root),str(self.root/'first'))
        self.model.query('FiXtUrE');self.wait(lambda:len(self.model.rows.snapshot())==3 and not self.model.busy)
        rows=self.model.rows.snapshot()
        self.assertTrue(rows[0]['path'].endswith('fixture-one.txt'))
        self.assertTrue(rows[1]['separator'])
        self.assertTrue(rows[2]['path'].endswith('fixture-two.txt'))

    def test_superseded_query_and_cancel_do_not_publish_stale_rows(self):
        self.model.prepare(str(self.root),'')
        self.model.query('fixture');QTest.qWait(100)
        self.model.query('two');self.wait(lambda:len(self.model.rows.snapshot())==1 and not self.model.busy)
        self.assertTrue(self.model.rows.snapshot()[0]['path'].endswith('fixture-two.txt'))
        self.model.query('fixture');self.model.cancel();QTest.qWait(350)
        self.assertFalse(self.model.busy)
        self.assertEqual(self.model.rows.snapshot(),[])

    def test_first_match_is_published_while_a_later_folder_is_still_scanning(self):
        from core.rak_neobranch_view import ActiveProjectView
        original=ActiveProjectView.list_dir
        blocked,release=threading.Event(),threading.Event()
        def list_dir(view,path):
            if Path(path)==self.root/'other':
                blocked.set();release.wait(5)
            return original(view,path)
        self.model.prepare(str(self.root),'')
        with patch.object(ActiveProjectView,'list_dir',list_dir):
            try:
                self.model.query('fixture-one')
                self.wait(blocked.is_set)
                QTest.qWait(60)
                self.assertTrue(self.model.rows.snapshot(),'First match is being buffered until the entire scan finishes')
                self.assertTrue(self.model.busy)
            finally:
                release.set()
                self.wait(lambda:not self.model.busy)

    def test_relative_paths_and_separate_terms_find_nested_files(self):
        self.model.prepare(str(self.root),'')
        for query in ('OTHER\\FIXTURE-TWO','other two',str(self.root/'other'/'fixture-two.txt')):
            self.model.query(query)
            self.wait(lambda:self.model.resultCount==1 and not self.model.busy)
            row=self.model.rows.snapshot()[0]
            self.assertEqual(row['path'],str(self.root/'other'/'fixture-two.txt'))
            self.assertEqual(row['parent'],'other')

    def test_search_includes_files_created_only_in_the_active_neobranch(self):
        from main_ext.class_NeoBranchManager import NeoBranchManager
        from core.workspace_files import WorkspaceFiles
        manager=NeoBranchManager(str(self.root))
        manager.create_branch('first','work','green');manager.set_active_branch('first','work')
        logical=WorkspaceFiles(str(self.root)).create(str(self.root/'first'),'branch-only.prop')
        self.assertFalse(Path(logical).exists())
        self.model.prepare(str(self.root),'')
        self.model.query('branch-only')
        self.wait(lambda:self.model.resultCount==1 and not self.model.busy)
        self.assertEqual(self.model.rows.snapshot()[0]['path'],logical)


if __name__=='__main__':unittest.main()
