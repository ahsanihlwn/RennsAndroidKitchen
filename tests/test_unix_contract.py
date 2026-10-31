# SPDX-License-Identifier: MPL-2.0
"""GNU behavior and self-describing Kitchen operations over active branches."""
import json
from pathlib import Path
import subprocess
import sys
import threading
import unittest
from unittest.mock import patch
from unittest.mock import Mock
from types import SimpleNamespace
import zipfile
from PySide6.QtCore import QCoreApplication
from tests import test_kitchen_cli as fixtures
from core.workspace_files import WorkspaceFiles


class UnixContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QCoreApplication.instance() or QCoreApplication([])
    setUp=fixtures.KitchenCliTests.setUp
    tearDown=fixtures.KitchenCliTests.tearDown
    wait=fixtures.KitchenCliTests.wait
    command=fixtures.KitchenCliTests.command

    def test_bulk_copy_keeps_display_output_separate_from_each_activity_path(self):
        source=self.root/'incoming';source.mkdir();destination=self.root/'vendor';destination.mkdir()
        for number in range(4):(source/f'{number}.txt').write_text(f'fixture {number}')
        activity=Mock()
        self.owner._agent=SimpleNamespace(activity=activity,_protect_settings_path=lambda _:None)
        response=self.command('cp -rn incoming/. vendor/')
        self.assertTrue(response['ok'],response)
        expected={str(destination/f'{number}.txt') for number in range(4)}
        paths,kind=activity.touch_many.call_args.args
        self.assertEqual(set(paths),expected);self.assertEqual(kind,'write')
        activity.touch.assert_not_called()
        for number in range(4):
            self.assertEqual((destination/f'{number}.txt').read_text(),f'fixture {number}')
            self.assertIn(str(destination/f'{number}.txt'),response['msg'])

    def test_gnu_sed_addresses_delete_multiple_programs_and_hold_space(self):
        path=self.root/'text.txt';path.write_text('one\ntwo\nthree\nfour\n')
        result=self.command("sed -n '2,3p' text.txt")
        self.assertTrue(result['ok'],result);self.assertEqual(result['msg'],'two\nthree\n')
        result=self.command("sed -i -e '1d' -e 's/four/FOUR/' text.txt")
        self.assertTrue(result['ok'],result);self.assertEqual(path.read_text(),'two\nthree\nFOUR\n')
        result=self.command(r"sed '1h;1!H;$!d;g;s/\n/,/g' text.txt")
        self.assertTrue(result['ok'],result);self.assertEqual(result['msg'],'two,three,FOUR\n')

    def test_gnu_sed_rejects_script_side_effects_without_mutating_file(self):
        path=self.root/'text.txt';path.write_text('keep\n')
        result=self.command("sed -i 'w escaped.txt' text.txt")
        self.assertFalse(result['ok']);self.assertFalse(result['application_error'])
        self.assertIn('sandbox',result['msg']);self.assertEqual(path.read_text(),'keep\n')
        self.assertFalse((self.root/'escaped.txt').exists())

    def test_no_partial_sed_fallback_when_engine_is_missing(self):
        (self.root/'text.txt').write_text('a\n')
        with patch('core.unix_tools.resolve_tool',return_value=None):result=self.command("sed -i 's/a/b/' text.txt")
        self.assertFalse(result['ok']);self.assertFalse(result['application_error'])
        self.assertIn('GNU sed is unavailable',result['msg']);self.assertEqual((self.root/'text.txt').read_text(),'a\n')

    def test_gnu_grep_bre_ere_and_normal_no_match_status(self):
        (self.root/'text.txt').write_text('foofoo\n(foo)\nbar\n')
        result=self.command(r"grep '\(foo\)\1' text.txt")
        self.assertTrue(result['ok'],result);self.assertEqual(result['msg'],'foofoo\n')
        result=self.command("grep -nE '^(foo)+$' text.txt")
        self.assertTrue(result['ok'],result);self.assertEqual(result['msg'],'1:foofoo\n')
        result=self.command('grep absent text.txt')
        self.assertFalse(result['ok']);self.assertEqual(result['error_code'],'no_match');self.assertNotIn('log',result)
        self.assertEqual(result['msg'],'')

    def test_cp_recursive_merges_without_removing_existing_files(self):
        source=self.root/'src';source.mkdir();(source/'new.txt').write_text('new');(source/'same.txt').write_text('source')
        target=self.root/'dest/src';target.mkdir(parents=True);(target/'keep.txt').write_text('keep');(target/'same.txt').write_text('old')
        result=self.command('cp -rn src dest')
        self.assertTrue(result['ok'],result);self.assertEqual((target/'same.txt').read_text(),'old')
        self.assertEqual((target/'new.txt').read_text(),'new');self.assertEqual((target/'keep.txt').read_text(),'keep')
        self.assertTrue(self.command('cp -r src dest')['ok']);self.assertEqual((target/'same.txt').read_text(),'source')

    def test_cp_contents_and_merge_work_in_branch_and_preserve_source(self):
        source=self.root/'system/src';source.mkdir(parents=True);(source/'change.txt').write_text('new');(source/'add.txt').write_text('added')
        target=self.root/'system/dest';target.mkdir();(target/'change.txt').write_text('old');(target/'keep.txt').write_text('keep')
        from main_ext.class_NeoBranchManager import NeoBranchManager
        manager=NeoBranchManager(str(self.root));manager.create_branch('system','work','blue');manager.set_active_branch('system','work')
        result=self.command('cp -a system/src/. system/dest')
        self.assertTrue(result['ok'],result)
        files=WorkspaceFiles(str(self.root));physical,origin=files.view.resolve(str(target/'change.txt'))
        self.assertEqual(origin,'branch');self.assertEqual(Path(physical).read_text(),'new')
        self.assertEqual((target/'change.txt').read_text(),'old');self.assertFalse((target/'add.txt').exists())
        self.assertEqual(Path(files.view.resolve(str(target/'keep.txt'))[0]).read_text(),'keep')

    def test_inspect_returns_usable_argv_and_default_help_hides_legacy_actions(self):
        path=self.root/'ROM archive.zip'
        with zipfile.ZipFile(path,'w') as archive:archive.writestr('inside.txt','data')
        result=self.command('inspect "ROM archive.zip" --json')
        self.assertTrue(result['ok'],result);description=json.loads(result['msg'])
        action=next(row for row in description['operations'] if row['action']=='unpack')
        self.assertEqual(action['argv'],['rak','unpack',str(path)])
        result=self.command(subprocess.list2cmdline(action['argv'][1:]))
        self.assertTrue(result['ok'],result);self.assertEqual((self.root/'ROM archive/inside.txt').read_text(),'data')
        self.assertIn('inspect PATH',self.command('list')['msg']);self.assertNotIn('apktool_decompile',self.command('list')['msg'])
        self.assertIn('apktool_decompile',self.command('list --all')['msg'])

    def test_inspect_does_not_offer_unimplemented_extraction(self):
        (self.root/'disk.iso').write_bytes(b'fixture')
        result=self.command('inspect disk.iso --json')
        self.assertTrue(result['ok'],result);description=json.loads(result['msg'])
        self.assertEqual(description['operations'],[]);self.assertIn('not implemented',description['unsupported'])

    def test_repack_generic_folder_uses_output_format_and_inspect_supplies_command(self):
        folder=self.root/'plain folder';folder.mkdir();(folder/'hello.txt').write_text('hello')
        description=json.loads(self.command('inspect "plain folder" --json')['msg'])
        action=next(row for row in description['operations'] if row['action']=='pack')
        result=self.command(subprocess.list2cmdline(action['argv'][1:]));self.assertTrue(result['ok'],result)
        with zipfile.ZipFile(action['argv'][-1]) as archive:self.assertEqual(archive.read('hello.txt'),b'hello')

    def test_inspect_branch_folder_supplies_pack_destination_outside_partition(self):
        folder=self.root/'system/etc';folder.mkdir(parents=True);(folder/'config.txt').write_text('source')
        from main_ext.class_NeoBranchManager import NeoBranchManager
        manager=NeoBranchManager(str(self.root));manager.create_branch('system','pack','blue');manager.set_active_branch('system','pack')
        physical=manager.record_edit('system','pack',str(folder/'config.txt'));Path(physical).write_text('branch')
        description=json.loads(self.command('inspect system/etc --json')['msg'])
        action=next(row for row in description['operations'] if row['action']=='pack')
        self.assertEqual(Path(action['argv'][-1]).parent,self.root)
        result=self.command(subprocess.list2cmdline(action['argv'][1:]));self.assertTrue(result['ok'],result)
        with zipfile.ZipFile(action['argv'][-1]) as archive:self.assertEqual(archive.read('config.txt'),b'branch')
        self.assertEqual((folder/'config.txt').read_text(),'source')

    def test_inspect_json_remains_clean_in_standalone_cli(self):
        path=self.root/'text.txt';path.write_text('data')
        script='from core import rak_cli; rak_cli._call_ipc_server=lambda *a,**k:None; rak_cli._main_cli()'
        result=subprocess.run([sys.executable,'-c',script,'inspect',str(path),'--json'],capture_output=True,text=True,timeout=20)
        self.assertEqual(result.returncode,0,result.stderr);self.assertEqual(json.loads(result.stdout)['path'],str(path))

    def test_invalid_flag_reports_only_specific_error(self):
        result=self.command('cp --does-not-exist first second')
        self.assertFalse(result['ok']);self.assertIn('--does-not-exist',result['msg'])
        self.assertNotIn('Unix-style project commands',result['msg']);self.assertLess(len(result['msg']),250)

    def test_standalone_preserves_quotes_spaces_and_bre_in_actual_argv(self):
        path=self.root/'text.txt';path.write_text('"old" foofoo\n')
        script='from core import rak_cli; rak_cli._call_ipc_server=lambda *a,**k:None; rak_cli._main_cli()'
        result=subprocess.run([sys.executable,'-c',script,'sed','-i','-e','s/"old"/"new word"/g','-e',r's/\(foo\)\1/double/g',str(path)],capture_output=True,text=True,timeout=20)
        self.assertEqual(result.returncode,0,result.stderr);self.assertEqual(path.read_text(),'"new word" double\n')

    def test_ipc_preserves_actual_argv_instead_of_reparsing_windows_quotes(self):
        from core import rak_cli
        path=self.root/'quoted file.txt';path.write_text('"old" foofoo\n')
        argv=['sed','-i','-e','s/"old"/"new word"/g','-e',r's/\(foo\)\1/double/g',str(path)]
        results=[]
        thread=threading.Thread(target=lambda:results.append(rak_cli._handle_ipc_command(subprocess.list2cmdline(argv),str(self.root),argv=argv)))
        thread.start();self.wait(lambda:not thread.is_alive());thread.join()
        self.assertTrue(results[0]['ok'],results);self.assertEqual(path.read_text(),'"new word" double\n')

    def test_standalone_grep_stream_and_exit_codes_match_gnu(self):
        path=self.root/'text.txt';path.write_text('match\nother\n')
        script='from core import rak_cli; rak_cli._call_ipc_server=lambda *a,**k:None; rak_cli._main_cli()'
        for pattern,code,output in [('match',0,'match\n'),('missing',1,''),('[',2,'')]:
            with self.subTest(pattern=pattern):
                result=subprocess.run([sys.executable,'-c',script,'grep',pattern,str(path)],capture_output=True,text=True,timeout=20)
                self.assertEqual(result.returncode,code,result.stderr);self.assertEqual(result.stdout,output)

    def test_cp_archive_preserves_android_metadata_when_replacing(self):
        root=self.root/'system';root.mkdir();(root/'first.txt').write_text('first');(root/'second.txt').write_text('second')
        config=self.root/'config';config.mkdir(exist_ok=True)
        (config/'system_fs_config').write_text('system 0 0 0755\nsystem/first.txt 1000 1001 0644\nsystem/second.txt 0 0 0600\n')
        (config/'system_file_contexts').write_text('/system(/.*)? u:object_r:system_file:s0\n/system/first\\.txt u:object_r:source_file:s0\n/system/second\\.txt u:object_r:old_file:s0\n')
        from main_ext.class_NeoBranchManager import NeoBranchManager
        manager=NeoBranchManager(str(self.root));manager.create_branch('system','attrs','blue');manager.set_active_branch('system','attrs')
        source_config=(config/'system_fs_config').read_bytes()
        result=self.command('cp -a system/first.txt system/second.txt');self.assertTrue(result['ok'],result)
        branch=Path(manager._branch_config_dir('system','attrs'))
        self.assertIn('system/second.txt 1000 1001 0644',(branch/'system_fs_config').read_text())
        self.assertIn(r'/system/second\.txt u:object_r:source_file:s0',(branch/'system_file_contexts').read_text())
        self.assertEqual((config/'system_fs_config').read_bytes(),source_config)


if __name__=='__main__':unittest.main()
