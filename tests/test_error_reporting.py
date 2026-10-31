# SPDX-License-Identifier: MPL-2.0
import contextlib
import io
import logging
from pathlib import Path
import tempfile
import threading
import time
import unittest

from PySide6.QtCore import QObject, Signal, Slot, Qt, QThread
from PySide6.QtGui import QGuiApplication

from core.error_reporting import register_error_presenter, unregister_error_presenter, report_fatal
from services.async_jobs import AsyncJobs
from ui.workspace_tasks import WorkspaceTasks
from core.command_context import CommandContext, CommandFailure, command_scope


class Presenter(QObject):
    fatalRequested = Signal(str)
    operationErrorRequested = Signal(str)

    def __init__(self):
        super().__init__()
        self._closing = False
        self.messages = []
        self.operations = []
        self.threads = []
        self.fatalRequested.connect(self.receive, Qt.QueuedConnection)
        self.operationErrorRequested.connect(self.receive_operation,Qt.QueuedConnection)

    @Slot(str)
    def receive_operation(self,text):
        self.operations.append(text);self.threads.append(QThread.currentThread())

    @Slot(str)
    def receive(self, text):
        self.messages.append(text)
        self.threads.append(QThread.currentThread())


class ErrorReportingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QGuiApplication.instance() or QGuiApplication([])

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.log = Path(self.temporary.name) / 'crash.log'
        self.file_handler = logging.FileHandler(self.log, encoding='utf-8')
        logging.getLogger().addHandler(self.file_handler)
        self.addCleanup(self.close_log)
        self.presenter = Presenter()
        register_error_presenter(self.presenter)
        self.addCleanup(unregister_error_presenter, self.presenter)
        self.console = io.StringIO()
        redirect = contextlib.redirect_stdout(self.console)
        redirect.__enter__()
        self.addCleanup(redirect.__exit__, None, None, None)

    def close_log(self):
        logging.getLogger().removeHandler(self.file_handler)
        self.file_handler.close()

    def wait(self, predicate):
        deadline = time.monotonic() + 5
        while not predicate():
            self.assertLess(time.monotonic(), deadline)
            self.app.processEvents()
            time.sleep(.005)

    def assert_all_outputs(self, text, *, fatal=True):
        messages=self.presenter.messages if fatal else self.presenter.operations
        self.wait(lambda: bool(messages))
        self.file_handler.flush()
        self.assertIn(text, '\n'.join(messages))
        self.assertIn(text, self.console.getvalue())
        self.assertIn(text, self.log.read_text(encoding='utf-8'))
        self.assertTrue(all(thread == self.app.thread() for thread in self.presenter.threads))

    def test_plain_failure_reaches_operation_ui_debug_and_log_without_fatal(self):
        text = 'mkfs.erofs error: vendor_file_contexts line 4 has invalid file type'
        report_fatal(text, owner=self.presenter)
        self.assertFalse(self.presenter.messages)
        self.assert_all_outputs(text,fatal=False)
        self.assertFalse(self.presenter.messages)

    def test_logged_exception_preserves_traceback_in_every_output(self):
        try:
            raise RuntimeError('fixture decoder failed')
        except RuntimeError:
            logging.getLogger('fixture').exception('Archive extraction failed')
        self.assert_all_outputs('RuntimeError: fixture decoder failed')
        self.assertIn('Traceback', self.presenter.messages[0])

    def test_worker_thread_reports_are_presented_on_the_gui_thread(self):
        worker = threading.Thread(target=lambda: report_fatal('Threaded failure'))
        worker.start()
        worker.join(3)
        self.assertFalse(worker.is_alive())
        self.assert_all_outputs('Threaded failure',fatal=False)
        self.assertFalse(self.presenter.messages)

    def test_silent_background_failure_is_an_operation_failure(self):
        jobs = AsyncJobs(self.presenter)
        self.addCleanup(jobs.stop)
        jobs.submit('viewer-analysis', lambda: (False, 'Malformed image'), None, lambda *_: None)
        self.assert_all_outputs('Malformed image',fatal=False)
        self.assertEqual(len(self.presenter.operations), 1)
        self.assertFalse(self.presenter.messages)

    def test_callback_that_reports_an_error_does_not_generate_a_second_report(self):
        jobs = AsyncJobs(self.presenter)
        self.addCleanup(jobs.stop)
        jobs.submit('fixture', lambda: (False, 'Bad archive'), None,
                    lambda ok, text: report_fatal('Extract\n\n' + text, owner=self.presenter))
        self.assert_all_outputs('Bad archive',fatal=False)
        self.assertEqual(len(self.presenter.operations), 1)
        self.assertEqual(self.console.getvalue().count('[FATAL]'), 0)

    def test_task_exceptions_keep_the_traceback_and_operation_label(self):
        tasks = WorkspaceTasks(self.presenter)
        self.addCleanup(tasks.close)
        def work():
            raise ValueError('Invalid SELinux context')
        tasks.enqueue('Repack Erofs', work, lambda *_: None)
        self.assert_all_outputs('ValueError: Invalid SELinux context',fatal=False)
        self.assertIn('Repack Erofs', self.presenter.operations[0])
        self.assertIn('Traceback', self.presenter.operations[0])
        self.assertFalse(self.presenter.messages)

    def test_another_error_in_the_callback_does_not_hide_the_original_failure(self):
        jobs = AsyncJobs(self.presenter)
        self.addCleanup(jobs.stop)
        jobs.submit('fixture', lambda: (False, 'ORIGINAL_JOB_FAILURE'), None,
                    lambda *_: report_fatal('OTHER_CALLBACK_FAILURE', owner=self.presenter))
        self.wait(lambda: len(self.presenter.operations) == 2)
        self.assert_all_outputs('ORIGINAL_JOB_FAILURE',fatal=False)
        self.assert_all_outputs('OTHER_CALLBACK_FAILURE',fatal=False)

    def test_cli_worker_failure_is_returned_in_logs_without_crash_ui(self):
        context = CommandContext('fs rename fixture.txt occupied.txt')
        tasks = WorkspaceTasks(self.presenter)
        self.addCleanup(tasks.close)
        with command_scope(context):
            tasks.enqueue('Rename', lambda: (False, 'Destination already exists: occupied.txt'), lambda *_: None)
        self.wait(lambda: not tasks.queue.is_busy())
        self.app.processEvents()
        response = context.response({'ok': False, 'msg': 'Rename failed'})
        self.assertIn('Destination already exists', response['log'])
        self.assertFalse(response['application_error'])
        self.assertFalse(self.presenter.messages)
        self.assertIn('[RAK_CLI:ERROR]', self.console.getvalue())
        self.file_handler.flush()
        self.assertIn('Destination already exists', self.log.read_text(encoding='utf-8'))

    def test_cli_internal_bug_keeps_crash_ui_and_returns_traceback(self):
        context = CommandContext('fs rename fixture.txt renamed.txt')
        tasks = WorkspaceTasks(self.presenter)
        self.addCleanup(tasks.close)
        def broken():raise TypeError('INTERNAL_CLI_WORKER_BUG')
        with command_scope(context):tasks.enqueue('Rename', broken, lambda *_: None)
        self.assert_all_outputs('TypeError: INTERNAL_CLI_WORKER_BUG')
        response = context.response({'ok': False, 'msg': 'Rename failed'})
        self.assertTrue(response['application_error'])
        self.assertIn('Traceback', response['log'])
        self.assertIn('INTERNAL_CLI_WORKER_BUG', response['log'])

    def test_cli_context_survives_pool_work_and_completion_callback(self):
        context = CommandContext('repack vendor')
        jobs = AsyncJobs(self.presenter)
        self.addCleanup(jobs.stop)
        finished = []
        def work():
            logging.getLogger('tool').error('mkfs stderr: invalid file_contexts line 4')
            return False, CommandFailure('mkfs returned exit status 1')
        def done(ok, value):
            report_fatal(value, application_error=False)
            finished.append(True)
        with command_scope(context):jobs.submit('cli-tool', work, None, done)
        self.wait(lambda: bool(finished))
        self.app.processEvents()
        text = context.response({'ok': False, 'msg': 'Repack failed'})['log']
        self.assertIn('invalid file_contexts line 4', text)
        self.assertIn('exit status 1', text)
        self.assertFalse(self.presenter.messages)

    def test_cli_context_does_not_hide_simultaneous_or_queued_gui_errors(self):
        context = CommandContext('repack vendor')
        tasks = WorkspaceTasks(self.presenter)
        self.addCleanup(tasks.close)
        started, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        def command_work():
            started.set()
            release.wait(4)
            return False, 'CLI_ONLY_FAILURE'
        with command_scope(context):tasks.enqueue('CLI operation', command_work, lambda *_: None)
        def gui_bug():raise TypeError('QUEUED_GUI_FAILURE')
        tasks.enqueue('GUI operation', gui_bug, lambda *_: None)
        self.wait(started.is_set)
        try:raise TypeError('SIMULTANEOUS_GUI_FAILURE')
        except TypeError:report_fatal('SIMULTANEOUS_GUI_FAILURE')
        self.wait(lambda: bool(self.presenter.messages))
        release.set()
        self.wait(lambda: not tasks.queue.is_busy() and len(self.presenter.messages) == 2)
        self.assertIn('SIMULTANEOUS_GUI_FAILURE', '\n'.join(self.presenter.messages))
        self.assertIn('QUEUED_GUI_FAILURE', '\n'.join(self.presenter.messages))
        self.assertNotIn('CLI_ONLY_FAILURE', '\n'.join(self.presenter.messages))
        cli_log = '\n'.join(text for text, _ in context.records())
        self.assertIn('CLI_ONLY_FAILURE', cli_log)
        self.assertNotIn('GUI_FAILURE', cli_log)

    def test_cli_callback_bug_is_also_returned_to_the_cli(self):
        context = CommandContext('fs touch folder fixture.txt')
        jobs = AsyncJobs(self.presenter)
        self.addCleanup(jobs.stop)
        def broken_callback(*_):raise AttributeError('INTERNAL_CALLBACK_BUG')
        with command_scope(context):jobs.submit('cli-callback', lambda: (True, ''), None, broken_callback)
        self.assert_all_outputs('AttributeError: INTERNAL_CALLBACK_BUG')
        self.assertIn('INTERNAL_CALLBACK_BUG', context.response({'ok':False,'msg':'Failed'})['log'])

    def test_pending_async_gui_job_keeps_its_origin_after_a_cli_job(self):
        context = CommandContext('fs copy source target')
        jobs = AsyncJobs(self.presenter,limit=1)
        self.addCleanup(jobs.stop)
        started,release = threading.Event(),threading.Event()
        self.addCleanup(release.set)
        def cli_work():
            started.set();release.wait(4)
            return False,CommandFailure('FIRST_CLI_FAILURE')
        with command_scope(context):jobs.submit('cli',cli_work,None,lambda *_:None)
        jobs.submit('gui',lambda:(False,CommandFailure('PENDING_GUI_FAILURE')),None,lambda *_:None)
        self.wait(started.is_set);release.set()
        self.assert_all_outputs('PENDING_GUI_FAILURE',fatal=False)
        self.assertNotIn('FIRST_CLI_FAILURE','\n'.join(self.presenter.messages))
        self.assertNotIn('PENDING_GUI_FAILURE','\n'.join(text for text,_ in context.records()))


    def test_plain_logging_error_does_not_claim_a_python_crash(self):
        logging.error('Official Access Revoked: access is unavailable')
        self.assert_all_outputs('Official Access Revoked',fatal=False)
        self.assertFalse(self.presenter.messages)

    def test_real_python_exception_keeps_full_traceback_in_fatal(self):
        try:raise TypeError('ACTUAL_INTERNAL_PYTHON_BUG')
        except TypeError:report_fatal('Callback failed')
        self.assert_all_outputs('TypeError: ACTUAL_INTERNAL_PYTHON_BUG')
        self.assertIn('Traceback (most recent call last)',self.presenter.messages[0])
        self.assertFalse(self.presenter.operations)

    def test_notices_and_access_state_use_normal_message_api(self):
        from types import SimpleNamespace
        from ui.workspace import WorkspaceSession
        from ui.application import LauncherApplication
        from ui.private_workspace import PrivateWorkspace
        presentations=[]
        signal=SimpleNamespace(emit=lambda *args:presentations.append(args))
        workspace=SimpleNamespace(presentationRequested=signal)
        workspace.message=lambda *args,**kwargs:WorkspaceSession.message(workspace,*args,**kwargs)
        state=SimpleNamespace(_closed=False,provider=None,guard=SimpleNamespace(allowed=False),_allowed=True,
                              changed=SimpleNamespace(emit=lambda:None),workspace=workspace)
        PrivateWorkspace._refresh_guard(state)
        self.assertEqual(presentations[-1][1]['title'],'Official Access Revoked')
        self.assertEqual(presentations[-1][0],'message')
        self.assertIn('The kitchen remains available',presentations[-1][1]['text'])
        self.assertFalse(PrivateWorkspace._check(state));self.assertEqual(len(presentations),2)
        launcher=SimpleNamespace(window=SimpleNamespace(property=lambda _:True),model=SimpleNamespace(presentationRequested=signal))
        LauncherApplication._message(launcher,'ordinary notice','Notification')
        self.assertEqual(presentations[-1][1]['title'],'Notification')
        self.app.processEvents()
        self.assertFalse(self.presenter.messages);self.assertFalse(self.presenter.operations)
        self.assertNotIn('[FATAL]',self.console.getvalue())

    def test_missing_operation_signal_never_falls_back_to_fatal(self):
        class Legacy(QObject):
            fatalRequested=Signal(str)
        legacy=Legacy();legacy._closing=False;messages=[];legacy.fatalRequested.connect(messages.append)
        register_error_presenter(legacy)
        try:
            report_fatal('An ordinary tool failed',owner=legacy,application_error=False)
            self.app.processEvents();self.assertEqual(messages,[])
        finally:unregister_error_presenter(legacy)


if __name__ == '__main__':
    unittest.main()
