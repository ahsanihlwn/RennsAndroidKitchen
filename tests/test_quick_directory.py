# SPDX-License-Identifier: MPL-2.0
"""Native model protocol, source metadata and asynchronous directory navigation."""
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from PySide6.QtCore import QCoreApplication,QModelIndex,QPersistentModelIndex,Qt,qInstallMessageHandler
from ui.directory import DirectoryModel
from PySide6.QtTest import QTest,QAbstractItemModelTester


class DirectoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QCoreApplication.instance() or QCoreApplication([])
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.base=Path(self.temp.name)
        (self.base/'system'/'etc').mkdir(parents=True)
        (self.base/'system'/'etc'/'file.txt').write_text('fixture',encoding='utf-8')
        (self.base/'config').mkdir()
        (self.base/'config'/'system_file_contexts').write_text('/system/etc/file\\.txt u:object_r:test_file:s0\n',encoding='utf-8')
        (self.base/'config'/'system_fs_config').write_text('system/etc/file.txt 0 0 0644\n',encoding='utf-8')
        (self.base/'z.txt').write_text('old',encoding='utf-8')
        self.model=DirectoryModel(watch=False);self.failures=[];self.messages=[]
        self.model.failed.connect(self.failures.append)
        self.previous=qInstallMessageHandler(lambda mode,context,text:self.messages.append(text))
        self.tester=QAbstractItemModelTester(self.model,QAbstractItemModelTester.FailureReportingMode.Warning)
        self.tester.setUseFetchMore(False)
    def tearDown(self):
        self.wait(lambda:not self.model.busy)
        self.model.close();self.tester.deleteLater();self.model.deleteLater();self.app.processEvents()
        qInstallMessageHandler(self.previous)
        self.temp.cleanup()
        self.assertFalse([value for value in self.messages if 'FAIL!' in value],self.messages)
    def wait(self,predicate):
        deadline=time.monotonic()+8
        while not predicate():
            self.assertLess(time.monotonic(),deadline,(self.failures,self.messages))
            QTest.qWait(10)
    def load(self):self.model.load(str(self.base));self.wait(lambda:not self.model.busy);self.assertFalse(self.failures)
    def names(self,parent=QModelIndex()):return [self.model.data(self.model.index(row,0,parent)) for row in range(self.model.rowCount(parent))]
    def find(self,name,parent=QModelIndex()):return self.model.index(self.names(parent).index(name),0,parent)

    def test_source_sort_filter_and_no_widget_owner(self):
        (self.base/'AGENTS.md').write_text('fixture',encoding='utf-8')
        (self.base/'CLAUDE.md').write_text('fixture',encoding='utf-8')
        (self.base/'CODEX.md').write_text('fixture',encoding='utf-8')
        (self.base/'GEMINI.md').write_text('fixture',encoding='utf-8')
        (self.base/'.rak-agent-docs-active').write_text('fixture',encoding='utf-8')
        (self.base/'.rak_hidden').write_text('fixture',encoding='utf-8')
        self.load()
        self.assertEqual(self.names(),['config','system','z.txt'])
        self.assertEqual(self.model.count,3)
    def test_extract_staging_files_are_hidden_without_hiding_user_part_files(self):
        for name in ('.rak_extract-active.part', '.rak-extract-legacy.part', 'download.part'):
            (self.base/name).write_bytes(b'fixture')
        self.load()
        self.assertEqual(self.names(),['config','system','download.part','z.txt'])
    def test_tree_fetch_uses_real_context_permission_and_parent_indexes(self):
        self.model.setDisplayMode('tree');self.load()
        system=self.find('system');self.model.fetchMore(system);self.wait(lambda:not self.model.busy)
        etc=self.find('etc',system);self.model.fetchMore(etc);self.wait(lambda:not self.model.busy)
        leaf=self.find('file.txt',etc)
        self.assertEqual(self.model.parent(leaf),etc)
        self.assertEqual(self.model.data(self.model.index(0,1,etc)),'u:object_r:test_file:s0')
        self.assertEqual(self.model.data(self.model.index(0,2,etc)),'rw-r--r--')
        node=self.model._node(leaf)
        self.assertEqual(node.branch_lines,(True,False))
        self.assertTrue(node.last_sibling)
    def test_single_directory_navigation_up_and_file_activation(self):
        self.load();self.model.activate(str(self.base/'system'));self.wait(lambda:not self.model.busy)
        self.assertEqual(self.names(),['..','etc']);self.assertTrue(self.model.canGoUp)
        self.model.activate(str(self.base/'system'/'etc'));self.wait(lambda:not self.model.busy)
        activated=[];self.model.fileActivated.connect(activated.append)
        self.model.activate(str(self.base/'system'/'etc'/'file.txt'))
        self.assertEqual(activated,[str(self.base/'system'/'etc'/'file.txt')])
        self.model.activate('..');self.wait(lambda:not self.model.busy)
        self.assertEqual(self.model.location,str(self.base/'system'))
    def test_refresh_retains_persistent_survivor_indexes(self):
        self.load();index=QPersistentModelIndex(self.find('z.txt'))
        (self.base/'a.txt').write_text('new',encoding='utf-8')
        self.model.refresh();self.wait(lambda:not self.model.busy)
        self.assertTrue(index.isValid());self.assertEqual(index.data(),'z.txt');self.assertEqual(index.row(),3)
        (self.base/'a.txt').unlink();self.model.refresh();self.wait(lambda:not self.model.busy)
        self.assertTrue(index.isValid());self.assertEqual(index.row(),2)
    def test_empty_directory_has_source_empty_row_and_zero_count(self):
        (self.base/'empty').mkdir();self.load();self.model.activate(str(self.base/'empty'))
        self.wait(lambda:not self.model.busy)
        self.assertEqual(self.names(),['..','Empty']);self.assertEqual(self.model.count,0)

    def test_missing_and_empty_symlink_folders_show_empty_on_demand(self):
        from core.make_symlink import make_symlink_bytes
        (self.base/'empty_target').mkdir()
        for name,target in (('broken_link','/missing_target/'),('empty_link','/empty_target/')):
            (self.base/name).write_bytes(make_symlink_bytes(target))
        for mode in ('tree','single_dir'):
            self.model.setDisplayMode(mode);self.load()
            for name in ('broken_link','empty_link'):
                path=str(self.base/name)
                self.assertTrue(self.model._nodes[path].folder)
                if mode=='tree':
                    self.model.ensureLoaded(self.model.indexForPath(path));self.wait(lambda:not self.model.busy)
                    self.assertEqual([row.texts[0] for row in self.model._nodes[path].children],['Empty'])
                else:
                    self.model.activate(path);self.wait(lambda:not self.model.busy)
                    self.assertEqual(self.names(),['..','Empty']);self.assertEqual(self.model.count,0)
                    self.model.goUp();self.wait(lambda:not self.model.busy)
                self.assertFalse(self.failures);self.assertEqual(self.model.error,'')

    def test_stale_folder_symlink_becomes_empty_but_real_access_errors_propagate(self):
        from core.make_symlink import make_symlink_bytes
        from core.directory_scan import scan_tree_directory
        target=self.base/'target';target.mkdir();link=self.base/'stale_link';link.write_bytes(make_symlink_bytes('/target/'))
        self.load();target.rmdir()
        self.model.activate(str(link));self.wait(lambda:not self.model.busy)
        self.assertEqual(self.names(),['..','Empty']);self.assertFalse(self.failures)
        target.mkdir()
        with patch('core.directory_scan.directory_entries',side_effect=PermissionError('fixture access denied')):
            with self.assertRaises(PermissionError):scan_tree_directory({'base':str(self.base),'path':str(link),'target':str(target),'folder_symlink':True})

    def test_watcher_refresh_preserves_inline_draft_and_original_indexes(self):
        self.load();original=QPersistentModelIndex(self.find('config'))
        path=self.model.beginDraft(str(self.base),False)
        self.model.refresh();self.wait(lambda:not self.model.busy)
        self.assertTrue(original.isValid());self.assertEqual(original.data(),'config')
        draft=self.model.cellForPath(path,0)
        self.assertTrue(draft.isValid());self.assertEqual(draft.data(),'')
        changes=[];self.model.metadataEditRequested.connect(lambda *args:changes.append(args))
        self.model.setData(draft,'new.txt')
        self.assertEqual(changes,[(path,0,'new.txt')])
        self.model.cancelDraft()
        self.assertFalse(self.model.indexForPath(path).isValid())
        self.assertEqual(self.names(),['config','system','z.txt'])
    def test_missing_project_reports_error_without_creating_directories(self):
        missing=self.base/'absent';self.model.load(str(missing));self.wait(lambda:not self.model.busy)
        self.assertTrue(self.failures);self.assertFalse(missing.exists())

    def test_new_sibling_stays_after_selected_file_during_refresh(self):
        self.load();selected=str(self.base/'z.txt')
        draft=self.model.beginDraft(str(self.base),False,selected)
        self.assertEqual(self.names(),['config','system','z.txt',''])
        self.model.refresh();self.wait(lambda:not self.model.busy)
        self.assertEqual(self.names(),['config','system','z.txt',''])
        self.model.cancelDraftForPath('another-editor')
        self.assertTrue(self.model.indexForPath(draft).isValid())
        self.model.cancelDraftForPath(draft)
        self.assertEqual(self.names(),['config','system','z.txt'])

    def test_late_scan_from_previous_project_cannot_replace_current_rows(self):
        other=self.base/'other-project';other.mkdir()
        (other/'unique.txt').write_text('new project',encoding='utf-8')
        self.model.load(str(self.base));self.model.load(str(other))
        self.wait(lambda:not self.model.busy)
        self.assertEqual(self.model.location,str(other))
        self.assertEqual(self.names(),['config','unique.txt'])
        self.assertFalse(self.failures)

    def test_only_opened_directories_are_enumerated(self):
        from core.native_backend import directory_entries
        with patch('core.directory_scan.directory_entries',wraps=directory_entries) as enumerate_directory:
            self.load()
            QTest.qWait(100)
            self.assertEqual([call.args[0] for call in enumerate_directory.call_args_list],[str(self.base)])
            target=str(self.base/'system')
            self.model.activate(target);self.wait(lambda:not self.model.busy)
            self.assertEqual([call.args[0] for call in enumerate_directory.call_args_list],[str(self.base),target])
            self.assertEqual(self.names(),['..','etc'])
            self.assertFalse(self.model._nodes[str(self.base/'system'/'etc')].loaded)

    def test_cpp_view_lazy_expansion_metadata_and_persistent_indexes(self):
        view=self.model.viewModel
        tester=QAbstractItemModelTester(view,QAbstractItemModelTester.FailureReportingMode.Warning,view)
        self.load();system=self.model.indexForPath(str(self.base/'system'))
        self.assertIs(system.model(),view);self.assertTrue(view.hasChildren(system))
        self.model.ensureLoaded(system);self.wait(lambda:not self.model.busy)
        etc=self.model.indexForPath(str(self.base/'system'/'etc'))
        self.model.ensureLoaded(etc);self.wait(lambda:not self.model.busy)
        leaf=self.model.indexForPath(str(self.base/'system'/'etc'/'file.txt'))
        self.assertEqual(view.parent(leaf),etc)
        self.assertEqual(self.model.data(self.model.cellForPath(str(self.base/'system'/'etc'/'file.txt'),1)),'u:object_r:test_file:s0')
        survivor=QPersistentModelIndex(self.model.indexForPath(str(self.base/'z.txt')))
        (self.base/'a.txt').write_text('new');self.model.refresh();self.wait(lambda:not self.model.busy)
        self.assertTrue(survivor.isValid());self.assertEqual(self.model.data(survivor),'z.txt')
        self.assertFalse(self.failures)

    def test_cpp_view_single_directory_up_and_draft_cancel(self):
        view=self.model.viewModel;self.load()
        self.model.activate(str(self.base/'system'));self.wait(lambda:not self.model.busy)
        self.assertEqual(view.data(view.index(0,0)),'..')
        draft=self.model.beginDraft(str(self.base/'system'),False)
        self.assertTrue(self.model.indexForPath(draft).isValid())
        self.model.cancelDraft();self.assertFalse(self.model.indexForPath(draft).isValid())
        self.assertEqual([view.data(view.index(row,0)) for row in range(view.rowCount())],['..','etc'])

    def test_cpp_view_folder_replaced_by_file_retires_children_and_placeholder(self):
        view=self.model.viewModel;self.model.setDisplayMode('tree')
        for expanded in (False,True):
            folder=self.base/('opened' if expanded else 'closed');folder.mkdir()
            leaf=folder/'leaf.txt';leaf.write_text('fixture')
            self.load();index=self.model.indexForPath(str(folder))
            if expanded:self.model.ensureLoaded(index);self.wait(lambda:not self.model.busy)
            self.assertTrue(view.hasChildren(index))
            leaf.unlink();folder.rmdir();folder.write_text('now a file')
            self.model.refresh();self.wait(lambda:not self.model.busy)
            index=self.model.indexForPath(str(folder))
            self.assertEqual(view.rowCount(index),0);self.assertFalse(view.hasChildren(index))
            self.assertNotIn(str(leaf),[node.path for node in self.model._view.nodes.values()])
            self.assertFalse(self.failures)

    def test_drag_target_matches_folder_children_file_siblings_and_same_directory(self):
        self.model.setDisplayMode('tree');self.load()
        system=self.find('system');self.model.ensureLoaded(system);self.wait(lambda:not self.model.busy)
        target=str(self.base/'system');etc=str(self.base/'system'/'etc')
        self.assertEqual(self.model.dragTarget(system,str(self.base/'z.txt'),True),{'destination':target,'first':target,'last':etc})
        self.assertEqual(self.model.dragTarget(system,str(self.base/'system'/'source.txt'),True),{'destination':target,'first':target,'last':target})
        self.assertEqual(self.model.dragTarget(system,str(self.base/'z.txt'),False),{'destination':target,'first':target,'last':target})
    def test_apk_mirror_switch_preserves_data_and_clears_cpp_children(self):
        from core import apktool_tool
        from core.tree_entries import APKTOOL_LINK_ROLE
        path=self.base/'fixture.apk';path.write_bytes(b'fixture APK')
        mirror=Path(apktool_tool.mirror_dir_for(str(self.base),str(path)));mirror.mkdir(parents=True)
        (mirror/apktool_tool.APKTOOL_MARKER).write_text('{}');(mirror/'contents.txt').write_text('decompiled')
        view=self.model.viewModel;self.model.setDisplayMode('tree');self.load()
        self.assertTrue(self.model._nodes[str(path)].data(0,APKTOOL_LINK_ROLE))
        self.assertEqual(self.model.dragTarget(self.model.indexForPath(str(path)),str(self.base/'z.txt'),False)['destination'],str(mirror))
        self.model.ensureLoaded(self.model.indexForPath(str(path)));self.wait(lambda:not self.model.busy)
        self.assertGreater(view.rowCount(self.model.indexForPath(str(path))),0)
        self.model.set_apk_view_mode(str(path),'apk');self.model.refresh();self.wait(lambda:not self.model.busy)
        self.assertFalse(view.hasChildren(self.model.indexForPath(str(path))))
        self.assertFalse(self.model._nodes[str(path)].data(0,APKTOOL_LINK_ROLE))
        self.model.set_apk_view_mode(str(path),'folder');self.model.refresh();self.wait(lambda:not self.model.busy)
        self.assertTrue(view.hasChildren(self.model.indexForPath(str(path))))
        self.assertEqual((mirror/'contents.txt').read_text(),'decompiled');self.assertEqual(path.read_bytes(),b'fixture APK')


    def test_streaming_magic_badges_do_not_block_root_ready_and_skip_child_folders(self):
        import threading
        from core import header_magic
        boot = self.base / 'boot.img'; boot.write_bytes(b'ANDROID!' + b'\x00' * 120)
        payload = self.base / 'payload.bin'; payload.write_bytes(b'CrAU' + b'\x00' * 124)
        system_img = self.base / 'system.img'; system_img.write_bytes(b'\x00' * 1024 + b'\xe2\xe1\xf5\xe0' + b'\x00' * 120)
        super_img = self.base / 'super.img'; super_img.write_bytes(b'\x00' * 4096 + b'\x00PLA' + b'\x00' * 120)
        nested_dir = self.base / 'system' / 'nested'; nested_dir.mkdir(parents=True)
        nested_img = nested_dir / 'vendor.img'; nested_img.write_bytes(b'ANDROID!' + b'\x00' * 120)
        self.model.setDisplayMode('tree')
        ready_fired = []
        self.model.ready.connect(lambda: ready_fired.append(True))
        gate = threading.Event()
        seen_probes = []
        badge_updates = []
        orig_check_magic = header_magic.check_magic
        def gated_magic(path):
            seen_probes.append(path)
            gate.wait(timeout=5.0)
            return orig_check_magic(path)
        with patch('core.header_magic.check_magic', side_effect=gated_magic):
            self.model.load(str(self.base))
            self.wait(lambda: bool(ready_fired))
            self.assertFalse(gate.is_set())
            self.assertFalse(self.model._magic_jobs._stopped)
            for name, node in [(p.name, self.model._nodes[str(p)]) for p in (boot, payload, system_img, super_img)]:
                pres = self.model._presentation_for(node)
                pres.changed.connect(lambda n=name, p=pres: badge_updates.append((n, p.badge)) if p.badge else None)
                self.assertEqual(pres.badge, '')
            self.assertNotIn(str(nested_img), seen_probes)
            gate.set()
            self.wait(lambda: not self.model._magic_jobs._running and not self.model._magic_jobs._pending)
        self.assertEqual(self.model._presentation_for(self.model._nodes[str(boot)]).badge, 'BOOT')
        self.assertEqual(self.model._presentation_for(self.model._nodes[str(payload)]).badge, 'PAYLOAD')
        self.assertEqual(self.model._presentation_for(self.model._nodes[str(system_img)]).badge, 'EROFS')
        self.assertEqual(self.model._presentation_for(self.model._nodes[str(super_img)]).badge, 'SUPER')
        self.assertEqual(len(badge_updates), 4)
        self.assertNotIn(str(nested_img), seen_probes)
        self.assertFalse(self.failures)


if __name__=='__main__':unittest.main()
