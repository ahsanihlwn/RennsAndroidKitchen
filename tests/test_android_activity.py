# SPDX-License-Identifier: MPL-2.0
import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from PySide6.QtCore import QCoreApplication,QObject,Signal
from shiboken6 import isValid
from ui.android_tools import AndroidTools
from ui.workspace_tasks import WorkspaceTasks
from core.activity_log import collect,parse_options
from core.error_reporting import error_checkpoint


class Owner(QObject):
    statusChanged=Signal()
    def __init__(self,project):
        super().__init__()
        self.project=project;self.p1='PORT';self.p2='STOCK';self._history=[];self.pending=[]
        self._tasks=WorkspaceTasks(self,runner=lambda worker,arg,callback:self.pending.append((worker,callback)))
        self._android_tools=AndroidTools(self,self)


class AndroidActivityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QCoreApplication.instance() or QCoreApplication([])
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='rak-android-unit-');self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.owner=Owner(str(self.root));self.backend=self.owner._android_tools
        self.addCleanup(self.owner._tasks.close);self.addCleanup(self.backend.close)
        self.case='normal';self.calls=[]
        def sleep_cancelable(seconds,cancel):
            deadline=time.monotonic()+seconds
            while time.monotonic()<deadline:
                if cancel is not None and cancel.is_cancelled():
                    return True
                time.sleep(0.01)
            return cancel is not None and cancel.is_cancelled()

        def run_native(tool,args,*,cwd=None,timeout_ms=None,cancel=None,on_output=None,on_progress=None):
            arg_list=list(args)
            self.calls.append((tool,arg_list))
            out=[]
            def emit(text):
                out.append(text)
                if on_output:on_output(text)
            if arg_list==['getvar','all']:
                if self.case=='late-adb':
                    return {'exit_code':1,'stdout':'','stderr':'','output':''}
                if self.case in ('empty','adb'):
                    emit('< waiting for any device >\n')
                    sleep_cancelable(0.2,cancel)
                    return {'exit_code':1,'stdout':'','stderr':'','output':''.join(out)}
                emit('(bootloader) partition-size:system_a:0x400000\n')
                emit('(bootloader) battery-part-status:unsupported\n')
                return {'exit_code':1 if self.case=='partial' else 0,'stdout':''.join(out),'stderr':'','output':''.join(out)}
            if arg_list==['devices']:
                if self.case=='late-adb':
                    sleep_cancelable(0.15,cancel)
                emit('List of devices attached\n')
                if self.case in ('adb','late-adb'):
                    emit('FIXTURE\tdevice\n')
                return {'exit_code':0,'stdout':''.join(out),'stderr':'','output':''.join(out)}
            if arg_list and arg_list[0]=='flash':
                emit("Sending sparse 'system' 1/2\n")
                if on_progress and self.case=='smooth':
                    on_progress({'partition':arg_list[1],'stage':'downloading','chunk_index':1,'chunk_total':2,'bytes_sent':256,'bytes_total':1024,'percent':25.0,'speed_bps':102400.0,'detail':'downloading'})
                sleep_cancelable(0.07,cancel)
                emit("Writing 'system' OKAY\n")
                if on_progress and self.case=='smooth':
                    on_progress({'partition':arg_list[1],'stage':'writing','chunk_index':1,'chunk_total':2,'bytes_sent':512,'bytes_total':1024,'percent':50.0,'speed_bps':102400.0,'detail':'writing'})
                sleep_cancelable(0.07,cancel)
                if arg_list[1]=='vendor':
                    emit('FAILED (AdbWriteEndpointSync failed: The semaphore timeout period has expired. (121))\n')
                    return {'exit_code':1,'stdout':'','stderr':''.join(out),'output':''.join(out)}
                emit("Sending sparse 'system' 2/2\n")
                if on_progress and self.case=='smooth':
                    on_progress({'partition':arg_list[1],'stage':'downloading','chunk_index':2,'chunk_total':2,'bytes_sent':900,'bytes_total':1024,'percent':88.0,'speed_bps':102400.0,'detail':'downloading'})
                emit("Writing 'system' OKAY\n")
                return {'exit_code':0,'stdout':''.join(out),'stderr':'','output':''.join(out)}
            return {'exit_code':0,'stdout':'OKAY\n','stderr':'','output':'OKAY\n'}
        self.backend._run_native=run_native
    def wait(self,predicate):
        end=time.monotonic()+6
        while not predicate():
            self.assertLess(time.monotonic(),end)
            self.app.processEvents();time.sleep(.004)
    def test_no_device_probes_start_together_and_never_report_fatal(self):
        self.case='empty';checkpoint=error_checkpoint()
        self.backend.refresh()
        self.assertEqual([call[0] for call in self.calls],['fastboot','adb'])
        self.wait(lambda:not self.backend.refreshing)
        self.assertEqual(self.backend.status,'No device connected.')
        self.assertEqual(error_checkpoint(),checkpoint)
    def test_partial_fastboot_data_is_valid_even_when_process_exits_nonzero(self):
        self.case='partial';checkpoint=error_checkpoint();self.backend.refresh()
        self.wait(lambda:not self.backend.refreshing)
        self.assertEqual(self.backend.mode,'fastboot')
        self.assertIn('system',self.backend._parsed['partitions'])
        self.assertEqual(error_checkpoint(),checkpoint)
    def test_adb_response_does_not_wait_for_fastboot_timeout(self):
        self.case='adb';self.backend.refresh()
        self.wait(lambda:self.backend.mode=='adb')
        self.assertIn('FIXTURE',self.backend.badge)
        self.wait(lambda:not self.backend._processes)
    def test_adb_response_after_fastboot_has_already_been_deleted(self):
        errors=[];self.case='late-adb'
        with patch.object(sys,'excepthook',side_effect=lambda *error:errors.append(error)):
            self.backend.refresh()
            self.wait(lambda:self.backend.mode=='adb')
            self.wait(lambda:not self.backend._processes)
        self.assertEqual(errors,[])
    def test_binary_resolution_uses_the_same_registry_as_widgets(self):
        with patch('core.binary_handler.resolve_binary',return_value='nested/platform-tools/fastboot.exe') as resolve:
            self.assertEqual(self.backend._program('fastboot'),'nested/platform-tools/fastboot.exe')
            self.assertEqual(resolve.call_args.args[1],'fastboot.exe')
    def test_closing_with_live_probes_does_not_deliver_stale_callbacks(self):
        errors=[];self.case='empty';self.backend.refresh()
        jobs=tuple(self.backend._processes)
        with patch.object(sys,'excepthook',side_effect=lambda *error:errors.append(error)):
            self.backend.close()
            self.wait(lambda:all(not isValid(job) or not job.isRunning() for job in jobs))
            self.app.processEvents()
        self.assertEqual(errors,[])

    def test_smooth_byte_progress_updates_flash_progress_and_loading(self):
        self.case='smooth';tasks=self.owner._tasks;samples=[]
        tasks.changed.connect(lambda:samples.extend(row.get('progress') for row in tasks.loading.get_activity() if row['label'].startswith('Flash')))
        self.backend.assign('system','system.img',str(self.root/'system.img'))
        self.backend.flashAll()
        self.wait(lambda:not self.backend.running)
        self.assertIn(25,samples)
        self.assertIn(88,samples)
        self.assertEqual(self.backend.flashProgress,100)

    def test_no_qprocess_in_android_tools_or_toolkit(self):
        repo=Path(__file__).resolve().parents[1]
        for rel in ('ui/android_tools.py','ui/toolkit.py'):
            src=(repo/rel).read_text(encoding='utf-8')
            self.assertNotIn('QProcess',src,f'Unexpected QProcess reference in {rel}')
    def test_flash_bypasses_file_queue_but_reports_main_loading_progress(self):
        tasks=self.owner._tasks
        tasks.enqueue('Held file operation',lambda:True,lambda *_:None)
        queue_before=tasks.queue.snapshot()
        samples=[]
        tasks.changed.connect(lambda:samples.extend(row.get('progress') for row in tasks.loading.get_activity() if row['label'].startswith('Flash')))
        self.backend.assign('system','system.img',str(self.root/'system.img'))
        self.backend.flashAll()
        self.assertTrue(self.backend.running)
        self.assertEqual(tasks.queue.snapshot(),queue_before)
        self.assertIn('Flash system',tasks.text)
        self.backend.refresh()
        self.assertFalse(any(args==['getvar','all'] for _,args in self.calls))
        self.wait(lambda:not self.backend.running)
        self.assertIn(50,samples)
        self.assertEqual(tasks.loading.get_history()[-1]['ok'],True)
        self.assertTrue(tasks.busy)  # held filesystem task still exists
        self.assertEqual(tasks.queue.snapshot(),queue_before)
    def test_failed_flash_keeps_full_output_and_does_not_retry_or_run_next(self):
        self.backend.assign('vendor','vendor.img',str(self.root/'vendor.img'))
        self.backend.assign('system','system.img',str(self.root/'system.img'))
        with contextlib.redirect_stdout(io.StringIO()):
            self.backend.flashAll();self.wait(lambda:not self.backend.running)
        self.assertTrue(self.backend.failed)
        self.assertEqual([row['state'] for row in self.backend.queue.snapshot()],['error','queued'])
        history=self.owner._tasks.loading.get_history()
        self.assertFalse(history[-1]['ok'])
        self.assertIn('AdbWriteEndpointSync',history[-1]['detail'])
        self.assertIn('Sending sparse',history[-1]['detail'])
        self.assertEqual(sum(args[0]=='flash' for _,args in self.calls),1)
    def test_clearing_activity_history_keeps_active_flash_and_file_tasks(self):
        tasks=self.owner._tasks
        tasks.enqueue('File operation',lambda:True,lambda *_:None)
        self.backend.assign('system','system.img',str(self.root/'system.img'));self.backend.flashAll()
        before=tasks.activity_snapshot()
        self.assertEqual(len(before['tasks']),2)
        cleared=collect(self.owner,parse_options('clear --yes'))
        self.assertTrue(cleared['busy'])
        self.assertEqual(len(tasks.activity_snapshot()['tasks']),2)
        self.wait(lambda:not self.backend.running)
        snapshot=collect(self.owner,parse_options('--json'))
        self.assertTrue(snapshot['busy'])
        self.assertEqual(snapshot['android']['progress'],100)
        self.assertTrue(snapshot['history'])
    def test_global_android_action_accepts_mounted_editor_paths(self):
        from types import SimpleNamespace
        from ui.workspace_tools import dispatch_tool
        events=[]
        owner=SimpleNamespace(active_tree=SimpleNamespace(base=str(self.root)),
            presentationRequested=SimpleNamespace(emit=lambda *args:events.append(args)))
        dispatch_tool(owner,'android','mount://C:/ROM/vendor.img//etc/init.rc',{})
        self.assertEqual(events,[('android',{})])


if __name__=='__main__':unittest.main()
