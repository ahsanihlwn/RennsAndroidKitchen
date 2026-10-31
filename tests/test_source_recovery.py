# SPDX-License-Identifier: MPL-2.0
import errno
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from PySide6.QtCore import QCoreApplication, QObject, Signal

from main_ext.class_NeoBranchManager import NeoBranchManager, SourceRestoreError
from ui.source_recovery import SourceRecovery
from ui.workspace_tasks import WorkspaceTasks


class RecoverySignalFixture(QObject):
    finished = Signal(bool, object)
    progress = Signal(str)
    def __init__(self):
        super().__init__()
        self.starts = 0
    def start(self):
        self.starts += 1


class SourceRecoveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QCoreApplication.instance() or QCoreApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='rak-recovery-')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.live = self.base / 'system'
        self.live.mkdir()
        (self.live / 'file.txt').write_text('original')
        config = self.base / 'config'
        config.mkdir()
        (config / 'system_fs_config').write_text('system 0 0 0755\nsystem/file.txt 0 0 0644\n')
        self.manager = NeoBranchManager(str(self.base))
        self.manager.create_branch('system', 'work', 'green')
        self.manager.set_active_branch('system', 'work')
        overlay = self.manager.record_edit('system', 'work', str(self.live / 'file.txt'))
        Path(overlay).write_text('branch edit')
        self.manager.prepare_for_repack('system', 'work')
        self.manifest = Path(self.manager._source_dir('system')) / 'repack_manifest.json'

    def wait(self, predicate, seconds=8):
        deadline = time.monotonic() + seconds
        while not predicate():
            self.assertLess(time.monotonic(), deadline)
            self.app.processEvents()
            time.sleep(.005)

    def test_rename_failure_keeps_the_cause_and_reuses_completed_staging_on_retry(self):
        original_replace = os.replace
        blocked = [True]
        def replace(src, dst):
            if blocked[0] and os.path.normpath(src) == str(self.live):
                raise PermissionError(errno.EACCES, 'File is locked by another process', str(self.live))
            return original_replace(src, dst)
        import main_ext.class_NeoBranchManager as module
        with patch.object(module.os, 'replace', side_effect=replace), \
             patch.object(module.time, 'sleep'), \
             patch.object(module.shutil, 'copytree', wraps=module.shutil.copytree) as copytree:
            with self.assertRaises(SourceRestoreError) as raised:
                self.manager.restore_after_repack('system')
            self.assertIn('File is locked', str(raised.exception))
            journal = json.loads(self.manifest.read_text())
            self.assertTrue(journal['restore_copy_complete'])
            self.assertEqual(journal['last_restore_error']['step'], 'Move current tree aside')
            self.assertEqual((self.live / 'file.txt').read_text(), 'branch edit')
            calls = copytree.call_count
            blocked[0] = False
            self.manager.restore_after_repack('system')
            self.assertEqual(copytree.call_count, calls)
        self.assertEqual((self.live / 'file.txt').read_text(), 'original')
        self.assertFalse(self.manifest.exists())

    def test_metadata_retry_does_not_swap_or_recopy_the_restored_source(self):
        import main_ext.class_NeoBranchManager as module
        copy = module._copy2_with_retry
        blocked = [True]
        target = self.base / 'config' / 'system_fs_config'
        def fail_metadata(src, dst):
            if blocked[0] and os.path.normpath(dst) == str(target):
                raise PermissionError(errno.EACCES, 'Config locked', str(target))
            return copy(src, dst)
        with patch.object(module, '_copy2_with_retry', side_effect=fail_metadata), \
             patch.object(module.shutil, 'copytree', wraps=module.shutil.copytree) as copytree:
            with self.assertRaises(SourceRestoreError):self.manager.restore_after_repack('system')
            journal = json.loads(self.manifest.read_text())
            self.assertTrue(journal['restore_fs_installed'])
            self.assertEqual(journal['last_restore_error']['path'], str(target))
            self.assertEqual((self.live / 'file.txt').read_text(), 'original')
            calls = copytree.call_count
            blocked[0] = False
            self.manager.restore_after_repack('system')
            self.assertEqual(copytree.call_count, calls)
        self.assertFalse(self.manifest.exists())

    def test_failed_install_rolls_back_and_retry_preserves_branch_edits(self):
        import main_ext.class_NeoBranchManager as module
        replace = os.replace
        blocked = [True]
        def fail_install(src, dst):
            if blocked[0] and os.path.basename(src) == 'staged' and os.path.normpath(dst) == str(self.live):
                raise PermissionError(errno.EACCES, 'Install temporarily locked', str(self.live))
            return replace(src, dst)
        with patch.object(module.os, 'replace', side_effect=fail_install), patch.object(module.time, 'sleep'):
            with self.assertRaises(SourceRestoreError):self.manager.restore_after_repack('system')
            self.assertEqual((self.live/'file.txt').read_text(), 'branch edit')
            journal=json.loads(self.manifest.read_text())
            self.assertEqual(journal['last_restore_error']['step'], 'Install Source tree')
            blocked[0]=False
            self.manager.restore_after_repack('system')
        self.assertEqual((self.live/'file.txt').read_text(), 'original')
        active = self.manager.get_active_branch('system')
        self.assertEqual(active['name'], 'work')
        self.assertEqual(Path(self.manager._branch_fs_dir('system','work'),'file.txt').read_text(), 'branch edit')

    @unittest.skipUnless(sys.platform == 'win32', 'Windows sharing semantics')
    def test_real_windows_directory_lock_is_retried_without_reloading(self):
        import ctypes
        from ctypes import wintypes
        kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        kernel.CreateFileW.argtypes=[wintypes.LPCWSTR,wintypes.DWORD,wintypes.DWORD,ctypes.c_void_p,wintypes.DWORD,wintypes.DWORD,wintypes.HANDLE]
        kernel.CreateFileW.restype=wintypes.HANDLE
        kernel.CloseHandle.argtypes=[wintypes.HANDLE]
        handle=kernel.CreateFileW(str(self.live),0x80000000,3,None,3,0x02000000,None)
        self.assertNotEqual(handle,wintypes.HANDLE(-1).value,ctypes.FormatError(ctypes.get_last_error()))
        recovery=SourceRecovery([str(self.base)])
        finished, causes=[],[]
        recovery.finished.connect(lambda ok,value:finished.append((ok,value)))
        def release_when_retrying(text):
            nonlocal handle
            if handle is not None and 'waiting for files' in text:
                causes.append(json.loads(self.manifest.read_text())['last_restore_error'])
                kernel.CloseHandle(handle);handle=None
        recovery.progress.connect(release_when_retrying)
        try:
            recovery.start()
            self.wait(lambda:bool(finished))
            self.assertTrue(finished[0][0],finished)
            self.assertEqual(len(causes),1)
            self.assertIn(causes[0]['winerror'],(5,32,33))
            self.assertEqual(causes[0]['step'],'Move current tree aside')
            self.assertTrue(self.manager.is_physical_checkout('system'))
            self.assertEqual((self.live/'file.txt').read_text(),'branch edit')
            self.manager.checkout_branch('system',None)
            self.assertEqual((self.live/'file.txt').read_text(),'original')
            self.assertFalse(recovery.active)
            self.assertFalse(self.manifest.exists())
        finally:
            if handle is not None:kernel.CloseHandle(handle)
            recovery.close()

    def test_another_process_waits_for_the_source_lock_and_api_finishes_once(self):
        marker = self.base / 'lock-held'
        script = ('import sys\nfrom pathlib import Path\n'
                  'from main_ext.class_NeoBranchManager import NeoBranchManager\n'
                  'with NeoBranchManager(sys.argv[1]).repack_transaction("system"):\n'
                  ' Path(sys.argv[2]).write_text("held")\n sys.stdin.read(1)\n')
        from core.proc_util import CREATE_NO_WINDOW
        process = subprocess.Popen([sys.executable, '-c', script, str(self.base), str(marker)],
                                   stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                   creationflags=CREATE_NO_WINDOW)
        recovery = SourceRecovery([str(self.base)])
        progress, finished = [], []
        recovery.progress.connect(progress.append)
        recovery.finished.connect(lambda ok, value: finished.append((ok, value)))
        try:
            self.wait(marker.exists)
            recovery.start();recovery.start()
            self.wait(lambda: any('Waiting for Source' in text for text in progress))
            self.assertTrue(recovery.active)
            self.assertFalse(finished)
            self.assertEqual((self.live / 'file.txt').read_text(), 'branch edit')
            process.stdin.write(b'x');process.stdin.flush()
            self.wait(lambda: bool(finished))
            self.assertTrue(finished[0][0], finished)
            self.assertFalse(recovery.active)
            self.assertEqual(len(finished), 1)
            self.assertTrue(self.manager.is_physical_checkout('system'))
            self.assertEqual((self.live / 'file.txt').read_text(), 'branch edit')
            self.manager.checkout_branch('system',None)
            self.assertEqual((self.live / 'file.txt').read_text(), 'original')
            self.assertFalse(self.manifest.exists())
        finally:
            recovery.close()
            if process.poll() is None:process.kill()
            process.communicate(timeout=5)

    def test_task_queue_resumes_only_from_recovery_finished_signal(self):
        recovery = RecoverySignalFixture()
        running, results, drained = [], [], []
        tasks = WorkspaceTasks(runner=lambda worker, arg, callback: running.append((worker, callback)), recovery=recovery)
        self.addCleanup(tasks.close)
        tasks.drained.connect(lambda: drained.append(True))
        tasks.enqueue('Repack', lambda: (False, 'NEOBRANCH_RESTORE_FAILED: locked file'), lambda *result: results.append(result))
        tasks.enqueue('Next', lambda: (True, 'next completed'), lambda *result: results.append(result))
        work, callback = running.pop(0);callback(*work())
        self.assertEqual(recovery.starts, 1)
        self.assertTrue(tasks.queue.is_busy())
        self.assertFalse(running);self.assertFalse(drained)
        tasks.enqueue('Third', lambda: (True, 'third completed'), lambda *result: results.append(result))
        recovery.progress.emit('Recovering Source: system')
        self.assertEqual(tasks.state, 'active')
        recovery.finished.emit(True, {'system': ['system']})
        self.assertFalse(tasks.halted)
        self.assertEqual(len(running), 1)
        while running:
            work, callback = running.pop(0);callback(*work())
        self.assertEqual(results[-2:], [(True, 'next completed'), (True, 'third completed')])
        self.assertFalse(tasks.queue.is_busy())
        self.assertEqual(drained, [True])

    def test_failed_recovery_retries_on_the_next_request_without_project_reload(self):
        recovery = RecoverySignalFixture()
        running, results = [], []
        tasks = WorkspaceTasks(runner=lambda worker, arg, callback: running.append((worker, callback)), recovery=recovery)
        self.addCleanup(tasks.close)
        tasks.enqueue('Repack', lambda: (False, 'NEOBRANCH_RESTORE_FAILED: locked'), lambda *_: None)
        work, callback = running.pop(0);callback(*work())
        recovery.finished.emit(False, 'Backup access denied: system/file.txt')
        self.assertFalse(tasks.queue.is_busy())
        self.assertTrue(tasks.halted)
        tasks.enqueue('Retry operation', lambda: (True, 'done'), lambda *result: results.append(result))
        self.assertEqual(recovery.starts, 2)
        self.assertFalse(running)
        recovery.finished.emit(True, {})
        work, callback = running.pop(0);callback(*work())
        self.assertEqual(results, [(True, 'done')])
        self.assertFalse(tasks.halted)


if __name__ == '__main__':unittest.main()
