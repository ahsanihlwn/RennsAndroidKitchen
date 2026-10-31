# SPDX-License-Identifier: MPL-2.0
"""Exercise real library work through the authenticated command dispatcher."""
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
import zipfile
import subprocess
import sys
from unittest.mock import patch

from PySide6.QtCore import QCoreApplication, QEvent
from core import rak_cli
from core.kitchen_cli import parse
from core.task_queue import TaskItem, TaskQueue
from ui.workspace_cli import headless_workspace
from tools.audit_partition_viewers import PARTS, payload_fixture


class KitchenCliTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QCoreApplication.instance() or QCoreApplication([])

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='rak kitchen cli ')
        self.root=Path(self.temp.name)
        self.owner=headless_workspace(str(self.root));rak_cli.attach(self.owner)
        self.wait(lambda:not self.owner.tree.busy)

    def tearDown(self):
        self.owner.stop();rak_cli.detach(self.owner);self.owner.deleteLater()
        QCoreApplication.sendPostedEvents(None,QEvent.DeferredDelete)
        self.temp.cleanup()

    def wait(self,predicate):
        deadline=time.monotonic()+15
        while not predicate():
            self.assertLess(time.monotonic(),deadline)
            self.app.processEvents();time.sleep(.002)

    def command(self,text,cwd=None):
        results=[]
        thread=threading.Thread(target=lambda:results.append(rak_cli._handle_ipc_command(text,str(cwd or self.root))))
        thread.start();self.wait(lambda:not thread.is_alive());thread.join()
        return results[0]

    def fixture_zip(self,name='ROM archive.zip'):
        path=self.root/name
        with zipfile.ZipFile(path,'w') as target:
            target.writestr('nested/first.txt',b'first\x00bytes')
            target.writestr('second.txt',b'second')
        return path

    def test_zip_list_selected_extraction_and_auto_unpack_with_spaces(self):
        self.fixture_zip()
        result=self.command('archive list "ROM archive.zip" --json')
        self.assertTrue(result['ok'],result)
        self.assertEqual([m['name'] for m in json.loads(result['msg'])['members']],['nested/first.txt','second.txt'])
        result=self.command('archive extract "ROM archive.zip" --to "selected output" --path nested --json')
        self.assertTrue(result['ok'],result)
        self.assertEqual((self.root/'selected output/nested/first.txt').read_bytes(),b'first\x00bytes')
        self.assertFalse((self.root/'selected output/second.txt').exists())
        self.assertIn(str(self.root/'selected output/nested/first.txt'),json.loads(result['msg'])['outputs'])
        result=self.command('unpack "ROM archive.zip"')
        self.assertTrue(result['ok'],result)
        self.assertEqual((self.root/'ROM archive/second.txt').read_bytes(),b'second')

    def test_real_seven_zip_pack_and_auto_unpack_round_trip(self):
        folder=self.root/'source folder';folder.mkdir();(folder/'hello.txt').write_text('seven zip')
        result=self.command('archive pack "source folder" --to "packed file.7z" --json')
        self.assertTrue(result['ok'],result)
        result=self.command('unpack "packed file.7z"')
        self.assertTrue(result['ok'],result)
        self.assertEqual((self.root/'packed file/hello.txt').read_text(),'seven zip')

    def test_output_conflict_preflights_all_members_and_keeps_history_failed(self):
        self.fixture_zip();output=self.root/'out';output.mkdir();(output/'second.txt').write_text('keep')
        result=self.command('archive extract "ROM archive.zip" --to out')
        self.assertFalse(result['ok']);self.assertFalse(result['application_error'])
        self.assertEqual((output/'second.txt').read_text(),'keep')
        self.assertFalse((output/'nested').exists())
        self.assertFalse(self.owner._tasks.loading.get_history()[-1]['ok'])

    def test_unknown_member_and_unsafe_archive_fail_without_output(self):
        self.fixture_zip()
        result=self.command('archive extract "ROM archive.zip" --to out --path missing')
        self.assertFalse(result['ok']);self.assertFalse(result['application_error'])
        self.assertFalse((self.root/'out').exists())
        with zipfile.ZipFile(self.root/'bad.zip','w') as target:target.writestr('../escape.txt','unsafe')
        result=self.command('archive extract bad.zip --to out')
        self.assertFalse(result['ok']);self.assertFalse(result['application_error'],result)
        self.assertFalse((self.root/'escape.txt').exists())

    def test_real_payload_list_selected_ota_zip_and_auto_extract_all(self):
        path=self.root/'payload.bin';path.write_bytes(payload_fixture())
        result=self.command('payload list payload.bin --json')
        self.assertTrue(result['ok'],result);self.assertEqual(json.loads(result['msg'])['partitions'],PARTS)
        with zipfile.ZipFile(self.root/'OTA update.zip','w') as target:target.write(path,'firmware/payload.bin')
        with patch('core.partition_viewer._payload_service',return_value=None):
            result=self.command('payload extract "OTA update.zip" --to selected --partition vendor --json')
            self.assertTrue(result['ok'],result)
            self.assertEqual((self.root/'selected/vendor.img').read_bytes(),(b'NATIVE_PARTITION_vendor\n'*4096)[:4096])
            self.assertFalse((self.root/'selected/system.img').exists())
            result=self.command('unpack payload.bin')
            self.assertTrue(result['ok'],result)
        for name in PARTS:self.assertTrue((self.root/(name+'.img')).is_file())

    def test_unknown_partition_and_bad_payload_are_normal_failures(self):
        (self.root/'payload.bin').write_bytes(payload_fixture())
        result=self.command('payload extract payload.bin --to out --partition absent')
        self.assertFalse(result['ok']);self.assertFalse(result['application_error'])
        self.assertIn('absent',result['msg']);self.assertFalse((self.root/'out').exists())
        (self.root/'broken.bin').write_bytes(b'CrAU')
        result=self.command('payload list broken.bin --json')
        self.assertFalse(result['ok']);self.assertFalse(result['application_error'],result)
        self.assertIn('truncated header',result['msg'])

    def test_super_selected_extraction_checks_actual_output(self):
        (self.root/'super.img').write_bytes(b'fixture')
        def extract(path,partition,out):
            (Path(out)/(partition+'.img')).write_bytes(partition.encode())
            return partition
        with patch('core.partition_viewer.super_info',return_value={'partitions':['system','vendor']}),patch('core.partition_viewer.extract_super',side_effect=extract):
            result=self.command('super extract super.img --to out --partition vendor --json')
        self.assertTrue(result['ok'],result);self.assertEqual((self.root/'out/vendor.img').read_bytes(),b'vendor')
        self.assertFalse((self.root/'out/system.img').exists())
        with patch('core.partition_viewer.super_info',return_value={'partitions':['system']}),patch('core.partition_viewer.extract_super',return_value='system'):
            result=self.command('super extract super.img --to missing --all')
        self.assertFalse(result['ok']);self.assertFalse(result['application_error'])

    def test_apk_auto_unpack_repack_and_alias_keep_worker_failure(self):
        apk=self.root/'test app.apk';apk.write_bytes(b'APK fixture')
        from core import apktool_tool
        with patch.object(apktool_tool,'decompile_apk',return_value=(True,'decompiled')) as decompile:
            result=self.command('unpack "test app.apk"');self.assertTrue(result['ok'],result)
            self.assertEqual(decompile.call_args.args[0],str(apk))
        with patch.object(apktool_tool,'build_apk',return_value=(True,str(apk))) as build:
            result=self.command('repack "test app.apk"');self.assertTrue(result['ok'],result);build.assert_called_once()
        with patch.object(apktool_tool,'sign_apk',return_value=(False,'actual signer output')):
            result=self.command('apk sign "test app.apk" --json')
        self.assertFalse(result['ok']);self.assertFalse(result['application_error'])
        self.assertIn('actual signer output',result['msg'])

    def test_br_and_dat_aliases_and_failure_classification(self):
        (self.root/'system.new.dat.br').write_bytes(b'fixture')
        (self.root/'file.bin').write_bytes(b'input')
        with patch('core.kitchen_conversions.dat_to_image',return_value=str(self.root/'system.img')) as convert:
            result=self.command('dat to-image system.new.dat.br --json')
            self.assertTrue(result['ok'],result);self.assertEqual(convert.call_args.kwargs,{'folder':False})
        with patch('core.brotli_tool.compress_to_br',return_value=(True,str(self.root/'file.bin.br'))) as compress:
            result=self.command('br compress file.bin --json')
            self.assertTrue(result['ok'],result);self.assertEqual(compress.call_args.kwargs,{'keep_source':True})

    def test_container_errors_preserve_internal_tracebacks(self):
        (self.root/'super.img').write_bytes(b'fixture')
        with patch('core.partition_viewer.super_info',side_effect=TypeError('INTERNAL_CONTAINER_BUG')):
            result=self.command('super list super.img --json')
        self.assertFalse(result['ok']);self.assertTrue(result['application_error'])
        self.assertIn('TypeError: INTERNAL_CONTAINER_BUG',result['log'])

    def test_branch_output_guard_checks_members_before_any_write(self):
        self.fixture_zip()
        from ui.workspace_cli import WorkspaceCli
        original=WorkspaceCli._tool_target
        def guard(files,path):
            if path.endswith('second.txt'):raise ValueError('active partition output blocked')
            return original(files,path)
        with patch.object(WorkspaceCli,'_tool_target',staticmethod(guard)):
            result=self.command('archive extract "ROM archive.zip" --to out')
        self.assertFalse(result['ok']);self.assertFalse((self.root/'out').exists())

    def test_explicit_input_path_works_when_client_cwd_is_outside_project(self):
        path=self.fixture_zip()
        result=self.command('archive list "'+str(path)+'" --json',cwd=self.root.parent)
        self.assertTrue(result['ok'],result)

    def test_unix_commands_use_standard_operands_and_preserve_branch_source(self):
        self.assertTrue(self.command('mkdir -p "system/etc/new folder"')['ok'])
        self.assertTrue(self.command('touch "system/etc/new folder/input.txt"')['ok'])
        source=self.root/'system/etc/new folder/input.txt';source.write_text('value=old\nvalue=old\n')
        from main_ext.class_NeoBranchManager import NeoBranchManager
        manager=NeoBranchManager(str(self.root));manager.create_branch('system','unix','blue');manager.set_active_branch('system','unix')
        result=self.command('sed -i "s/old/new/g" "system/etc/new folder/input.txt"')
        self.assertTrue(result['ok'],result);self.assertEqual(source.read_text(),'value=old\nvalue=old\n')
        result=self.command('cp "system/etc/new folder/input.txt" system/etc/copied.txt')
        self.assertTrue(result['ok'],result)
        result=self.command('mv system/etc/copied.txt system/etc/renamed.txt')
        self.assertTrue(result['ok'],result)
        from core.workspace_files import WorkspaceFiles
        files=WorkspaceFiles(str(self.root));physical,_=files.view.resolve(str(self.root/'system/etc/renamed.txt'))
        self.assertEqual(Path(physical).read_text(),'value=new\nvalue=new\n')
        self.assertFalse((self.root/'system/etc/renamed.txt').exists())
        self.assertTrue(self.command('rm system/etc/renamed.txt')['ok'])
        self.assertIsNone(WorkspaceFiles(str(self.root)).view.resolve(str(self.root/'system/etc/renamed.txt'))[0])
        self.assertTrue(self.command('rm -rf system/etc/missing')['ok'])

    def test_unix_copy_overwrite_no_clobber_recursive_and_touch(self):
        (self.root/'first.txt').write_text('first');(self.root/'second.txt').write_text('second')
        self.assertTrue(self.command('cp first.txt second.txt')['ok']);self.assertEqual((self.root/'second.txt').read_text(),'first')
        (self.root/'first.txt').write_text('new')
        self.assertTrue(self.command('cp -n first.txt second.txt')['ok']);self.assertEqual((self.root/'second.txt').read_text(),'first')
        self.assertTrue(self.command('touch second.txt')['ok']);self.assertEqual((self.root/'second.txt').read_text(),'first')
        self.assertTrue(self.command('mkdir original')['ok']);(self.root/'original/a.txt').write_text('a')
        self.assertFalse(self.command('cp original clone')['ok'])
        self.assertTrue(self.command('cp -r original clone')['ok']);self.assertEqual((self.root/'clone/a.txt').read_text(),'a')
        self.assertFalse(self.command('rm clone')['ok'])
        self.assertTrue(self.command('rm -r clone')['ok']);self.assertFalse((self.root/'clone').exists())

    def test_familiar_read_flags_work_on_branch_only_files(self):
        self.assertTrue(self.command('mkdir system')['ok'])
        from main_ext.class_NeoBranchManager import NeoBranchManager
        manager=NeoBranchManager(str(self.root));manager.create_branch('system','read','blue');manager.set_active_branch('system','read')
        from core.workspace_files import WorkspaceFiles
        files=WorkspaceFiles(str(self.root));logical=str(self.root/'system/added.txt')
        physical=files.write_path(logical);Path(physical).parent.mkdir(parents=True,exist_ok=True);Path(physical).write_text('Build=Branch\nother\n');files._record(logical,physical)
        result=self.command('ls -la system');self.assertTrue(result['ok'],result);self.assertIn('added.txt',result['msg'])
        result=self.command('grep -Rni build system');self.assertTrue(result['ok'],result);self.assertIn('added.txt:1:Build=Branch',result['msg'])
        result=self.command('cat -n system/added.txt');self.assertTrue(result['ok'],result);self.assertIn('1\tBuild=Branch',result['msg'])
        self.assertFalse(Path(logical).exists())

    def test_async_headless_is_rejected_before_enqueuing(self):
        self.fixture_zip()
        result=self.command('archive extract "ROM archive.zip" --to out --no-wait --json')
        self.assertFalse(result['ok']);self.assertIn('live Kitchen',result['msg'])
        self.assertEqual(self.owner._tasks._serial,0)

    def test_standalone_stdout_is_valid_json_and_quoted_paths_survive(self):
        path=self.fixture_zip()
        script='from core import rak_cli; rak_cli._call_ipc_server=lambda *a,**k:None; rak_cli._main_cli()'
        result=subprocess.run([sys.executable,'-c',script,'archive','list',str(path),'--json'],capture_output=True,text=True,timeout=20)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(json.loads(result.stdout)['path'],str(path))

    def test_archive_pack_reads_merged_branch_and_extract_cannot_write_source(self):
        folder=self.root/'system';folder.mkdir();(folder/'original.txt').write_text('source')
        from main_ext.class_NeoBranchManager import NeoBranchManager
        manager=NeoBranchManager(str(self.root))
        manager.create_branch('system','edited','blue');manager.set_active_branch('system','edited')
        branch=Path(manager._branch_fs_dir('system','edited'));branch.mkdir(parents=True,exist_ok=True)
        (branch/'original.txt').write_text('branch');(branch/'added.txt').write_text('added')
        result=self.command('archive pack system --to merged.zip --json')
        self.assertTrue(result['ok'],result)
        with zipfile.ZipFile(self.root/'merged.zip') as archive:
            self.assertEqual(archive.read('original.txt'),b'branch');self.assertEqual(archive.read('added.txt'),b'added')
        result=self.command('archive extract merged.zip --to system')
        self.assertFalse(result['ok']);self.assertFalse(result['application_error'])
        self.assertEqual((folder/'original.txt').read_text(),'source')


class KitchenParsingTests(unittest.TestCase):
    def test_missing_or_incompatible_options_rejected(self):
        for command in ('extract payload.bin --to out','extract payload.bin --to out --all --partition system','list payload.bin --partition system'):
            with self.subTest(command=command),self.assertRaises(ValueError):parse('payload',command,'.')

    def test_task_wait_uses_own_callback_not_queue_idle(self):
        pending=[]
        queue=TaskQueue(lambda worker,arg,callback:pending.append(callback))
        queue.enqueue(TaskItem('first',lambda:None,lambda ok,msg:None))
        queue.enqueue(TaskItem('second',lambda:None,lambda ok,msg:None))
        self.assertFalse(queue.wait_for_tasks(['first'],.001))
        pending[0](True,'actual result')
        self.assertTrue(queue.wait_for_tasks(['first'],.001))
        self.assertTrue(queue.is_busy())
        self.assertFalse(queue.wait_for_tasks(['second'],.001))
        queue.cancel_all()
        self.assertTrue(queue.wait_for_tasks(['second'],.001))

    def test_sed_bre_extended_backreferences_and_per_line_semantics(self):
        from core.unix_cli import substitution
        self.assertEqual(substitution('s/a/b/')('aa\naa\n'),'ba\nba\n')
        self.assertEqual(substitution(r's/\(foo\)/[\1:&]/g')('foo foo\n'),'[foo:foo] [foo:foo]\n')
        self.assertEqual(substitution('s/(foo)+/X/g',True)('foofoo foo\n'),'X X\n')
        self.assertEqual(substitution('s|/system|/vendor|g')('/system/bin\n'),'/vendor/bin\n')


if __name__=='__main__':unittest.main()
