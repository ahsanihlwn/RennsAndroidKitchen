# SPDX-License-Identifier: MPL-2.0
from pathlib import Path
import tempfile
import time
import unittest
import threading
import json
from unittest.mock import patch
from PySide6.QtCore import QCoreApplication,QEvent
from ui.workspace_cli import headless_workspace
from core import rak_cli


class QuickCliTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QCoreApplication.instance() or QCoreApplication([])
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='rak-cli-native-');self.root=Path(self.tmp.name)
        (self.root/'folder').mkdir();(self.root/'folder'/'source.txt').write_text('source')
        (self.root/'target').mkdir();self.owner=headless_workspace(str(self.root));rak_cli.attach(self.owner)
        self.wait(lambda:not self.owner.tree.busy)
    def tearDown(self):
        self.owner.stop();rak_cli.detach(self.owner);self.owner.deleteLater()
        QCoreApplication.sendPostedEvents(None,QEvent.DeferredDelete);self.tmp.cleanup()
    def wait(self,predicate):
        deadline=time.monotonic()+8
        while not predicate():
            self.assertLess(time.monotonic(),deadline);self.app.processEvents();time.sleep(.003)
    def idle(self):self.wait(lambda:not self.owner.task_queue.is_busy() and not self.owner.tree.busy)
    def command(self, text):
        result=[]
        thread=threading.Thread(target=lambda:result.append(rak_cli._handle_ipc_command(text,str(self.root))))
        thread.start()
        self.wait(lambda:not thread.is_alive())
        thread.join()
        return result[0]
    def test_bridge_create_rename_delete_without_widget_tree(self):
        bridge=rak_cli._ensure_bridge()
        ok,_=bridge._run_single('new_file',str(self.root/'folder'),name='created.txt');self.assertTrue(ok);self.idle()
        path=self.root/'folder'/'created.txt';self.assertTrue(path.exists())
        ok,_=bridge._run_single('rename',str(path),name='renamed.txt');self.assertTrue(ok);self.idle()
        path=path.with_name('renamed.txt');self.assertTrue(path.exists())
        ok,_=bridge._run_single('delete',str(path));self.assertTrue(ok);self.idle();self.assertFalse(path.exists())
    def test_bridge_text_and_transfer_do_not_open_dialogs(self):
        bridge=rak_cli._ensure_bridge();path=str(self.root/'folder'/'source.txt')
        self.assertTrue(bridge._run_write_text(path,'changed')[0]);self.wait(lambda:not self.owner.tree.busy)
        self.assertTrue(bridge._run_replace_text(path,'changed','native')[0]);self.wait(lambda:not self.owner.tree.busy)
        self.assertEqual(Path(path).read_text(),'native')
        self.assertTrue(bridge._run_transfer('copy',path,str(self.root/'target'))[0]);self.idle()
        self.assertEqual((self.root/'target'/'source.txt').read_text(),'native')
        self.assertFalse(bridge._run_transfer('copy',path,str(self.root/'target'))[0])
    def test_command_inventory_and_refresh_are_native(self):
        self.assertIn('unpack_dat_to_image',rak_cli.actions()['single'])
        bridge=rak_cli._ensure_bridge();bridge._do_refresh();self.assertTrue(bridge._result[0]);self.idle()
        self.assertFalse(bridge._run_single('not_an_action',str(self.root/'folder'))[0])

    def test_branch_commands_wait_for_physical_checkout_and_android_metadata(self):
        config=self.root/'config';config.mkdir(exist_ok=True)
        (config/'folder_fs_config').write_text('folder 0 0 0755\nfolder/source.txt 0 0 0644\n')
        (config/'folder_file_contexts').write_text('/folder/source\\.txt u:object_r:system_file:s0\n')
        response=self.command('branch create folder port blue')
        self.assertTrue(response['ok'],response)
        self.assertFalse(self.owner.task_queue.is_busy())
        path=self.root/'folder/source.txt';path.write_text('native host edit')
        response=self.command('fs permission folder/source.txt 0600')
        self.assertTrue(response['ok'],response)
        response=self.command('fs context folder/source.txt u:object_r:vendor_file:s0')
        self.assertTrue(response['ok'],response)
        self.assertIn('folder/source.txt 0 0 0600',(config/'folder_fs_config').read_text())
        self.assertIn('u:object_r:vendor_file:s0',(config/'folder_file_contexts').read_text())
        response=self.command('branch switch folder source')
        self.assertTrue(response['ok'],response)
        self.assertEqual(path.read_text(),'source')
        self.assertIn('0644',(config/'folder_fs_config').read_text())
        response=self.command('branch switch folder port')
        self.assertTrue(response['ok'],response)
        self.assertEqual(path.read_text(),'native host edit')
        self.assertIn('0600',(config/'folder_fs_config').read_text())
        spaced=path.with_name('with space.txt');spaced.write_text('ordinary file remains supported')
        before=(config/'folder_fs_config').read_bytes()
        response=self.command('fs permission "folder/with space.txt" 0600')
        self.assertFalse(response['ok'],response)
        self.assertFalse(response['application_error'])
        self.assertIn('whitespace',response['msg'])
        self.assertEqual((config/'folder_fs_config').read_bytes(),before)

    def test_branch_wait_options_do_not_abandon_standalone_tasks(self):
        response=self.command('branch create folder port --no-wait')
        self.assertFalse(response['ok'],response)
        self.assertIn('live Kitchen',response['msg'])
        self.assertFalse((self.root/'neobranch/folder/branch_port').exists())
        response=self.command('branch create folder port --timeout 20')
        self.assertTrue(response['ok'],response)
        self.assertFalse(self.owner.task_queue.is_busy())

    def test_missing_path_is_a_clear_command_failure(self):
        response=self.command('fs rename "folder/missing.txt" renamed.txt')
        self.assertFalse(response['ok'])
        self.assertFalse(response['application_error'])
        self.assertIn('Path not found',response['msg'])
        self.assertIn('missing.txt',response['log'])

    def test_worker_rename_conflict_is_not_reported_as_cli_success(self):
        occupied=self.root/'folder'/'occupied.txt';occupied.write_text('keep')
        response=self.command('fs rename "folder/source.txt" occupied.txt')
        self.assertFalse(response['ok'])
        self.assertFalse(response['application_error'])
        self.assertIn('Destination already exists',response['msg'])
        self.assertEqual(occupied.read_text(),'keep')
        self.assertTrue((self.root/'folder/source.txt').is_file())

    def test_internal_worker_exception_keeps_its_traceback_and_category(self):
        with patch('core.workspace_files.WorkspaceFiles.rename',side_effect=TypeError('CLI_APP_BUG')):
            response=self.command('fs rename "folder/source.txt" renamed.txt')
        self.assertFalse(response['ok'])
        self.assertTrue(response['application_error'])
        self.assertIn('TypeError: CLI_APP_BUG',response['log'])
        self.assertIn('Traceback',response['log'])

    def test_parallel_commands_do_not_share_results_or_error_logs(self):
        (self.root/'folder/occupied.txt').write_text('occupied')
        (self.root/'folder/second.txt').write_text('second')
        results={}
        commands={'failed':'fs rename "folder/source.txt" occupied.txt',
                  'passed':'fs rename "folder/second.txt" renamed.txt'}
        threads=[threading.Thread(target=lambda key=key,command=command:results.update({key:rak_cli._handle_ipc_command(command,str(self.root))}))
                 for key,command in commands.items()]
        for thread in threads:thread.start()
        self.wait(lambda:all(not thread.is_alive() for thread in threads))
        for thread in threads:thread.join()
        self.assertFalse(results['failed']['ok'])
        self.assertTrue(results['passed']['ok'],results)
        self.assertIn('occupied.txt',results['failed']['log'])
        self.assertNotIn('log',results['passed'])
        self.assertEqual((self.root/'folder/renamed.txt').read_text(),'second')

    def test_activity_reads_gui_history_full_failure_and_clears_same_history(self):
        self.assertTrue(self.command('fs touch folder activity.txt')['ok'])
        self.assertFalse(self.command('fs touch folder activity.txt')['ok'])
        response=self.command('activity --json --failed')
        self.assertTrue(response['ok'])
        snapshot=json.loads(response['msg'])
        self.assertEqual(len(snapshot['history']),1)
        entry=snapshot['history'][0]
        self.assertFalse(entry['ok'])
        detail=self.command('activity show '+entry['id']+' --json')
        self.assertIn('Destination already exists',json.loads(detail['msg'])['entry']['detail'])
        self.assertFalse(self.command('activity clear')['ok'])
        self.assertTrue(self.command('activity clear --yes')['ok'])
        self.assertEqual(self.owner._tasks.loading.get_history(),[])


if __name__=='__main__':unittest.main()
