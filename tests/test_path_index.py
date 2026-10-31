# SPDX-License-Identifier: MPL-2.0
import json
import os
import subprocess
from pathlib import Path
import tempfile
import time
import unittest

from core.native_backend import _load_native, directory_entries
from core.make_symlink import make_symlink_bytes


class PathIndexTests(unittest.TestCase):
    def setUp(self):
        module,error=_load_native()
        self.assertIsNone(error)
        self.assertTrue(hasattr(module,'PathIndex'),'Build the Rust runtime: python tools/build_native.py --dev')
        self.native=module;self.tmp=tempfile.TemporaryDirectory(prefix='rak-path-map-')
        self.root=Path(self.tmp.name);self.index=None

    def tearDown(self):
        if self.index is not None:
            self.index.close();self.assertTrue(self.index.wait_closed(3000))
        self.tmp.cleanup()

    def wait(self,predicate):
        deadline=time.monotonic()+10
        while not predicate():
            self.assertLess(time.monotonic(),deadline)
            time.sleep(.002)

    def stats(self):return json.loads(self.index.stats_json())

    def test_path_map_does_not_resolve_placeholder_links(self):
        (self.root/'target').mkdir();(self.root/'target'/'text.txt').write_text('target')
        (self.root/'link').write_bytes(make_symlink_bytes('/target'))
        self.index=self.native.PathIndex([str(self.root)])
        self.wait(lambda:self.stats()['complete'])
        rows=json.loads(self.index.paths_json(str(self.root)))
        link=next(row for row in rows if row['name']=='link')
        self.assertEqual(set(link),{'name','path','is_dir','is_link'})
        self.assertFalse(link['is_dir'])
        # Only opening this directory runs the original link/header enrichment.
        enriched=directory_entries(self.root,self.root,path_index=self.index)
        link=next(row for row in enriched if row['name']=='link')
        self.assertTrue(link['symlink']['is_dir']);self.assertTrue(link['symlink']['exists'])

    def test_foreground_directory_preempts_and_background_resumes(self):
        for number in range(1024):
            folder=self.root/f'folder_{number:04d}';folder.mkdir()
            (folder/'first.txt').write_text('one');(folder/'second.bin').write_bytes(b'two')
        target=self.root/'folder_1023'
        self.index=self.native.PathIndex([str(self.root)],background_workers=1)
        self.index.prioritize(str(target))
        rows=json.loads(self.index.paths_json(str(target)))
        self.assertEqual({row['name'] for row in rows},{'first.txt','second.bin'})
        foreground=self.stats()
        self.assertTrue(any(row['path']==str(target) and row['foreground'] for row in foreground['recent']))
        self.assertFalse(foreground['complete'],'Foreground must finish before the entire map')
        self.wait(lambda:self.stats()['complete'])
        self.assertEqual(self.stats()['directories'],1025)
        self.assertGreater(self.stats()['entries'],3000)

    def test_directory_changes_and_explicit_refresh_replace_cached_names(self):
        (self.root/'old.txt').write_text('old')
        self.index=self.native.PathIndex([str(self.root)])
        self.assertEqual(json.loads(self.index.paths_json(str(self.root)))[0]['name'],'old.txt')
        (self.root/'old.txt').unlink();(self.root/'new.txt').write_text('new')
        rows=json.loads(self.index.paths_json(str(self.root),True))
        self.assertEqual([row['name'] for row in rows],['new.txt'])

    def test_native_directory_link_is_not_followed_in_background(self):
        outside=self.root/'outside';outside.mkdir();(outside/'secret.txt').write_text('outside')
        base=self.root/'base';base.mkdir()
        try:os.symlink(outside,base/'shortcut',target_is_directory=True)
        except OSError as error:
            if os.name!='nt':raise
            result=subprocess.run(['cmd','/d','/c','mklink','/J',str(base/'shortcut'),str(outside)],
                capture_output=True,creationflags=subprocess.CREATE_NO_WINDOW)
            if result.returncode:self.skipTest(str(error))
        self.index=self.native.PathIndex([str(base)])
        self.wait(lambda:self.stats()['complete'])
        self.assertEqual(self.stats()['directories'],1)
        row=json.loads(self.index.paths_json(str(base)))[0]
        self.assertTrue(row['is_link'])


if __name__=='__main__':unittest.main()
