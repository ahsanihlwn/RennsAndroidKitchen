# SPDX-License-Identifier: MPL-2.0
"""Logical Android links retain their identity while following active branches."""
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch
import zipfile
from PySide6.QtCore import QCoreApplication
from tests import test_kitchen_cli as fixtures
from tests import test_agent_api as agent_fixtures
from core.make_symlink import make_symlink_bytes
from core.workspace_files import WorkspaceFiles
from core.agent_documents import read,apply


class SymlinkCliTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QCoreApplication.instance() or QCoreApplication([])
    setUp=fixtures.KitchenCliTests.setUp
    tearDown=fixtures.KitchenCliTests.tearDown
    wait=fixtures.KitchenCliTests.wait
    command=fixtures.KitchenCliTests.command

    def fixture(self):
        folder=self.root/'vendor/etc';folder.mkdir(parents=True)
        (folder/'build.prop').write_text('value=source\n',newline='\n')
        link=self.root/'system/etc';link.parent.mkdir()
        link.write_bytes(make_symlink_bytes('/vendor/etc'))
        return folder,link

    def branch(self):
        from main_ext.class_NeoBranchManager import NeoBranchManager
        manager=NeoBranchManager(str(self.root));manager.create_branch('vendor','work','blue');manager.set_active_branch('vendor','work')
        return manager

    def test_folder_listing_announces_link_even_when_entering_it(self):
        folder,link=self.fixture()
        for command in ('ls system','ls system/etc','ls -ld system/etc'):
            result=self.command(command);self.assertTrue(result['ok'],result)
            self.assertIn('-> /vendor/etc [symlink:directory; resolved]',result['msg'])
        result=self.command('ls system/etc');self.assertIn('build.prop',result['msg'])
        result=self.command('cat system/etc/build.prop');self.assertEqual(result['msg'],'value=source\n')
        info=json.loads(self.command('inspect system/etc --json')['msg'])
        self.assertEqual(info['kind'],'SYMLINK');self.assertEqual(info['behaves_as'],'directory')
        self.assertEqual(info['symlink']['resolved_path'],str(folder))
        info=json.loads(self.command('inspect system/etc/build.prop --json')['msg'])
        self.assertEqual(info['resolved_path'],str(folder/'build.prop'));self.assertEqual(len(info['symlinks']),1)
        self.assertEqual(self.command('readlink system/etc')['msg'],'/vendor/etc')
        self.assertEqual(self.command('readlink -e system/etc')['msg'],str(folder))
        self.assertIn('SYMLINK',self.command('inspect system/etc')['msg'])
        followed=json.loads(self.command('inspect -L system/etc --json')['msg'])
        self.assertEqual(followed['kind'],'directory');self.assertTrue(followed['symlink']['is_dir'])
        result=self.command('inspect -L "'+str(link)+'" --json',cwd=self.root.parent)
        self.assertTrue(result['ok'],result);self.assertNotIn('project_mismatch',result)
        self.assertEqual(json.loads(result['msg'])['resolved_path'],str(folder))

    def test_branch_edits_through_folder_link_preserve_source_and_link(self):
        folder,link=self.fixture();before=link.read_bytes();manager=self.branch()
        self.assertTrue(self.command("sed -i 's/source/branch/' system/etc/build.prop")['ok'])
        self.assertEqual((folder/'build.prop').read_text(),'value=source\n');self.assertEqual(link.read_bytes(),before)
        files=WorkspaceFiles(str(self.root));physical,origin=files.view.resolve(str(link/'build.prop'))
        self.assertEqual(origin,'branch');self.assertEqual(Path(physical).read_text(),'value=branch\n')
        self.assertTrue(self.command('touch system/etc/new.txt')['ok'])
        self.assertFalse((folder/'new.txt').exists())
        self.assertIn('new.txt',self.command('ls system/etc')['msg'])
        self.assertIn('target_origin=branch',self.command('ls system/etc')['msg'])
        branch_dir=Path(manager._branch_fs_dir('vendor','work'))
        self.assertTrue((branch_dir/'etc/new.txt').is_file())
        self.assertFalse((self.root/'neobranch/system').exists())

    def test_file_link_revision_edits_follow_target_and_retarget_conflicts(self):
        folder,link=self.fixture();pointer=self.root/'system/prop'
        pointer.write_bytes(make_symlink_bytes('etc/build.prop'));self.branch()
        files=WorkspaceFiles(str(self.root));document=read(files,str(pointer))[0]
        self.assertEqual(document['content'],'value=source\n');self.assertEqual(len(document['symlinks']),1)
        original=pointer.read_bytes()
        apply(files,str(pointer),document['revision'],'value=branch\n')
        self.assertEqual(pointer.read_bytes(),original);self.assertEqual((folder/'build.prop').read_text(),'value=source\n')
        document=read(files,str(pointer))[0]
        (self.root/'vendor/other.prop').write_text('value=branch\n')
        pointer.write_bytes(make_symlink_bytes('/vendor/other.prop'))
        with self.assertRaisesRegex(ValueError,'revision conflict'):apply(files,str(pointer),document['revision'],'wrong')
        self.assertEqual((self.root/'vendor/other.prop').read_text(),'value=branch\n')

    def test_copy_and_archive_preserve_link_payload_and_rm_unlinks_only(self):
        folder,link=self.fixture();original=link.read_bytes()
        self.assertTrue(self.command('cp -a system copied')['ok'])
        self.assertEqual((self.root/'copied/etc').read_bytes(),original)
        result=self.command('repack system -o packed.zip');self.assertTrue(result['ok'],result)
        with zipfile.ZipFile(self.root/'packed.zip') as archive:
            self.assertEqual(archive.read('etc'),original);self.assertNotIn('etc/build.prop',archive.namelist())
        self.assertTrue(self.command('rm system/etc')['ok']);self.assertFalse(link.exists())
        self.assertEqual((folder/'build.prop').read_text(),'value=source\n')

    def test_copy_into_linked_folder_and_nested_creation_use_target_branch(self):
        folder,link=self.fixture();self.branch();(self.root/'input.txt').write_text('copied')
        for command in ('cp input.txt system/etc','mkdir -p system/etc/nested/child','ln -s ../build.prop system/etc/nested/prop'):
            result=self.command(command);self.assertTrue(result['ok'],result)
        files=WorkspaceFiles(str(self.root))
        self.assertEqual(Path(files.view.resolve(str(folder/'input.txt'))[0]).read_text(),'copied')
        self.assertTrue(os.path.isdir(files.view.resolve(str(folder/'nested/child'))[0]))
        self.assertEqual(self.command('readlink system/etc/nested/prop')['msg'],'../build.prop')
        self.assertFalse((folder/'input.txt').exists());self.assertFalse((folder/'nested').exists())

    def test_builtin_tools_report_link_traversal_and_same_live_identity(self):
        from core import rak_bridge,rak_fs_tools
        self.fixture()
        info=rak_bridge.inspect('system/etc')
        self.assertEqual(info['kind'],'SYMLINK');self.assertEqual(info['behaves_as'],'directory')
        listing=rak_bridge.list_dir('system/etc')
        self.assertEqual(listing['kind'],'SYMLINK');self.assertTrue(listing['symlink']['is_dir'])
        self.assertEqual(listing['entries'][0]['name'],'build.prop')
        document=rak_fs_tools.read_file('system/etc/build.prop')
        self.assertTrue(document['ok']);self.assertEqual(document['symlinks'][0]['target'],'/vendor/etc')

    def test_standalone_readlink_has_plain_target_stdout(self):
        self.fixture()
        script='from core import rak_cli; rak_cli._call_ipc_server=lambda *a,**k:None; rak_cli._main_cli()'
        result=subprocess.run([sys.executable,'-c',script,'readlink',str(self.root/'system/etc')],capture_output=True,text=True,timeout=25)
        self.assertEqual(result.returncode,0,result.stderr);self.assertEqual(result.stdout,'/vendor/etc\n')

    def test_broken_cycles_and_escape_are_explicit_normal_failures(self):
        (self.root/'config').mkdir(exist_ok=True)
        for name,target in (('broken','/absent'),('loop','loop'),('escape','../outside')):
            (self.root/name).write_bytes(make_symlink_bytes(target))
        for name,status in (('broken','missing'),('loop','cycle'),('escape','outside_workspace')):
            info=json.loads(self.command('inspect '+name+' --json')['msg'])
            self.assertEqual(info['symlink']['status'],status)
            self.assertIn(status,self.command('ls -ld '+name)['msg'])
            result=self.command('ls '+name);self.assertFalse(result['ok']);self.assertFalse(result['application_error'],result)
            self.assertTrue(self.command('readlink '+name)['ok'])
        self.assertFalse(self.command('readlink -e broken')['ok'])

    def test_android_absolute_sar_mapping_and_native_directory_link(self):
        folder=self.root/'system/system/etc';folder.mkdir(parents=True);(folder/'test.txt').write_text('SAR')
        link=self.root/'etc';link.write_bytes(make_symlink_bytes('/system/etc'))
        self.assertEqual(self.command('cat etc/test.txt')['msg'],'SAR')
        native=self.root/'native'
        try:os.symlink(str(folder),native,target_is_directory=True)
        except OSError:return
        info=json.loads(self.command('inspect native --json')['msg'])
        self.assertEqual(info['symlink']['representation'],'native')
        self.assertEqual(self.command('cat native/test.txt')['msg'],'SAR')

    def test_type_is_live_header_magic_and_extension_evidence_is_separate(self):
        path=self.root/'renamed.zip';path.write_bytes(b'ANDROID!'+b'\0'*100)
        info=json.loads(self.command('inspect renamed.zip --json')['msg'])
        self.assertEqual(info['kind'],'BOOT');self.assertEqual(info['header_magic'],'BOOT')
        self.assertEqual(info['detected_by'],'header_magic');self.assertEqual(info['extension_hint'],'archive')
        with patch('core.header_magic.check_magic',return_value='EROFS') as magic:
            self.assertEqual(self.owner.quick_cli.badge(str(path)),'EROFS');magic.assert_called_once_with(str(path))
        path.write_bytes(b'plain')
        self.assertEqual(json.loads(self.command('inspect renamed.zip --json')['msg'])['detected_by'],'filename_hint')
        (self.root/'system.new.dat.br').write_bytes(b'plain')
        info=json.loads(self.command('inspect system.new.dat.br --json')['msg'])
        self.assertEqual(info['kind'],'DAT.BR');self.assertIsNone(info['header_magic'])


class OpenBufferSymlinkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QCoreApplication.instance() or QCoreApplication([])
    setUp=agent_fixtures.AgentApiTests.setUp
    command=agent_fixtures.AgentApiTests.command

    def test_read_and_edit_alias_use_open_unsaved_buffer_and_monaco_ack(self):
        alias=Path(self.path).parent/'alias';alias.write_bytes(make_symlink_bytes('test.txt'))
        bridge=self.owner.editor.bridge;bridge.on_content_changed(self.path,'unsaved user text\n')
        document=self.command('read',target=str(alias))
        self.assertEqual(document['content'],'unsaved user text\n');self.assertTrue(document['unsaved'])
        self.assertEqual(document['path'],self.path);self.assertEqual(document['requested_path'],str(alias))
        self.assertEqual(document['symlinks'][0]['target'],'test.txt')
        edits=[];bridge.agentEditRequested.connect(lambda *args:edits.append(args))
        result=self.command('edit',{'path':str(alias),'revision':document['revision'],'content':'edited\n'})
        self.assertEqual(result['state'],'applying');self.assertEqual(edits[-1][1],self.path)
        self.assertEqual(Path(self.path).read_text(),'saved\n')

    def test_direct_disk_write_via_alias_does_not_overwrite_open_buffer(self):
        from ui.workspace_cli import WorkspaceCli
        alias=Path(self.path).parent/'alias';alias.write_bytes(make_symlink_bytes('test.txt'))
        ok,result=WorkspaceCli(self.owner).write(str(alias),'wrong')
        self.assertFalse(ok);self.assertIn('open in Kitchen',str(result))
        self.assertEqual(Path(self.path).read_text(),'saved\n')

    def test_ui_open_alias_uses_canonical_text_target_and_reports_link(self):
        alias=Path(self.path).parent/'alias';alias.write_bytes(make_symlink_bytes('test.txt'))
        self.owner.projectSettings={'agent':{'ui_triggers':True}}
        with patch.object(self.owner,'openAs') as opening:
            result=self.command('open',target=str(alias))
        opening.assert_called_once_with(self.path,'editor')
        self.assertEqual(result['path'],self.path);self.assertEqual(result['requested_path'],str(alias))
        self.assertEqual(result['symlinks'][0]['target'],'test.txt')
