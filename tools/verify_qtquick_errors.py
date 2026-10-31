# SPDX-License-Identifier: MPL-2.0
"""Verify workspace fatal dialogs, nested full logs, debug output and crash.log."""
import contextlib
import io
import json
import logging
import os
from pathlib import Path
import sys
import tempfile
import time


def main():
    repo = Path(__file__).resolve().parents[1]
    os.chdir(repo)
    sys.path.insert(0, str(repo))
    from core.fonts import configure_text_rendering_env, apply_app_font
    configure_text_rendering_env()
    from ui.runtime import initialize_quick_runtime
    initialize_quick_runtime(webengine=True)
    from PySide6.QtCore import Qt, QTimer, QEventLoop, QMetaObject
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtTest import QTest
    from core import ui_lang
    from core.terminal_pty import PtySignals
    from main_ext.class_ThemeUi import theme
    from ui.presentation import LauncherModel
    from ui.terminal import TerminalSession
    from ui.workspace_window import QuickWorkspace
    from ui.workspace_actions import operation
    from tools.quick_test_utils import visual

    app = QGuiApplication([])
    app.setQuitOnLastWindowClosed(False)
    apply_app_font(app, polish_widgets=False)
    theme.load()
    ui_lang.init()

    class FakePty:
        def __init__(self, cwd):
            self.cwd = cwd
            self.signals = PtySignals()
            self.bash_mode = False
        def start(self, **_): pass
        def write(self, *_): pass
        def resize(self, *_): pass
        def shutdown(self): pass
        def set_spam_enter(self, *_): pass

    def pause(milliseconds=20):
        loop = QEventLoop()
        QTimer.singleShot(milliseconds, loop.quit)
        loop.exec()

    def wait(predicate):
        deadline = time.monotonic() + 15
        while not predicate():
            assert time.monotonic() < deadline, 'Fatal dialog verification timed out'
            pause()

    def details(dialog):
        result = dialog.property('details')
        return result.toVariant() if hasattr(result, 'toVariant') else result

    with tempfile.TemporaryDirectory(prefix='rak-errors-qa-') as temporary:
        root = Path(temporary)
        (root / 'PORT').mkdir()
        preferences = LauncherModel(settings={'animations': False}, profile={})
        workspace = QuickWorkspace(str(root), 'PORT', preferences=preferences,
            terminal_factory=lambda cwd=None, **options: TerminalSession(manager=FakePty(cwd), **options),
            settings_path=str(root / 'settings.json'), enable_private_services=False)
        window, session = workspace.window, workspace.session
        crash_log = root / 'crash.log'
        sink = logging.FileHandler(crash_log, encoding='utf-8')
        logging.getLogger().addHandler(sink)
        debug = io.StringIO()
        try:
            with contextlib.redirect_stdout(debug):
                wait(lambda: workspace._ready_emitted)
                workspace.reveal()
                wait(lambda: not workspace._entry_transition_active)
                window.requestActivate()
                wait(window.isActive)
                fatal = visual(window, 'workspaceFatalMessage')
                log = visual(window, 'errorLogWindow')
                normal = visual(window, 'launcherMessage')

                def click(name):
                    pause(50)
                    item = visual(window, name)
                    assert item.isVisible() and item.isEnabled(), name
                    QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier,
                                     item.mapToScene(item.boundingRect().center()).toPoint())

                error = ('mkfs.erofs error: D:\\PORT\\HiOS 17\\PORT\\config\\vendor_file_contexts:\n'
                         'line 4 has invalid file type \\-\\\n'
                         '<E> erofs: failed to open file contexts\n'
                         '[Error 22] Invalid argument\nmkfs.erofs 1.8.10-gee46dd74')
                def internal_failure():raise TypeError(error)
                operation(session, 'Repack Erofs', internal_failure)
                wait(lambda: fatal.property('opened'))
                assert not normal.property('presented'), 'Error used the ordinary message popup'
                assert details(fatal)['fatal'] and error in details(fatal)['log']
                assert session.statusState == 'error' and not session.history[-1]['ok']
                assert window.property('nativeDialogActive')
                click('fatalSeeLog')
                wait(lambda: log.property('opened'))
                assert error in log.property('logText')
                assert error in visual(window, 'fatalLogText').property('text')
                click('fatalLogCopy')
                assert error in app.clipboard().text()
                artifacts = repo / 'build' / 'qtquick_migration' / 'errors'
                artifacts.mkdir(parents=True, exist_ok=True)
                window.grabWindow().save(str(artifacts / 'fatal_log.png'))
                QMetaObject.invokeMethod(log, 'dismiss')
                wait(lambda: not log.property('visible'))

                # A second failure queues without replacing the log being read.
                def async_internal_bug():raise AttributeError('ASYNC_VIEWER_FAILURE')
                session._jobs.submit('viewer-fixture', async_internal_bug, None, lambda *_: None)
                wait(lambda: any('ASYNC_VIEWER_FAILURE' in entry for entry in session._fatal_pending))
                assert error in details(fatal)['log']
                QMetaObject.invokeMethod(fatal, 'acceptMessage')
                wait(lambda: fatal.property('opened') and 'ASYNC_VIEWER_FAILURE' in details(fatal)['log'])
                QMetaObject.invokeMethod(fatal, 'acceptMessage')
                wait(lambda: not session._fatal_visible and not fatal.property('visible'))

                def logged_python_bug():
                    try:raise TypeError('DIRECT_LOGGING_FAILURE')
                    except TypeError:logging.getLogger('fixture').exception('Python callback failed')
                logged_python_bug()
                wait(lambda: fatal.property('opened') and 'DIRECT_LOGGING_FAILURE' in details(fatal)['log'])
                logged_python_bug()
                pause(80)
                assert not session._fatal_pending, 'Repeated visible error duplicated the popup'
                QMetaObject.invokeMethod(fatal, 'acceptMessage')
                wait(lambda: not fatal.property('visible'))

                # Expected operation errors retain their full log but never
                # enter the fatal queue, even without exception metadata.
                logging.getLogger('fixture').error('EXPECTED_TOOL_FAILURE')
                wait(lambda:normal.property('opened'))
                assert not fatal.property('visible') and not session._fatal_pending
                assert 'EXPECTED_TOOL_FAILURE' in details(normal)['log']
                QMetaObject.invokeMethod(normal,'acceptMessage');wait(lambda:not normal.property('visible'))

                # Reproduce the reported access-state notification through
                # the actual PrivateWorkspace -> message -> QML route.
                from types import SimpleNamespace
                private=session.privateFeatures
                private.guard=SimpleNamespace(allowed=True,stop=lambda:None)
                private._refresh_guard()
                private.guard.allowed=False;private._refresh_guard()
                wait(lambda:normal.property('opened'))
                assert details(normal)['title']=='Official Access Revoked'
                assert 'The kitchen remains available.' in details(normal)['text']
                assert not details(normal).get('fatal') and not details(normal).get('log')
                assert not fatal.property('visible') and not session._fatal_pending
                window.grabWindow().save(str(artifacts/'access_notice.png'))
                QMetaObject.invokeMethod(normal,'acceptMessage');wait(lambda:not normal.property('visible'))
                private._refresh_guard();pause(80)
                assert not normal.property('visible'),'Unchanged revoked state repeated its notice'

                session.message('Saved successfully', 'Done', error=False)
                wait(lambda: normal.property('opened'))
                assert not details(normal).get('fatal')
                QMetaObject.invokeMethod(normal, 'acceptMessage')
                wait(lambda: not normal.property('visible'))
                accepted = []
                session.message('Continue?', 'Confirm', confirm=lambda: accepted.append(True))
                wait(lambda: normal.property('opened'))
                assert details(normal)['confirm'] and not fatal.property('visible')
                QMetaObject.invokeMethod(normal, 'acceptMessage')
                wait(lambda: accepted == [True])

                sink.flush()
                for text in (error, 'ASYNC_VIEWER_FAILURE', 'DIRECT_LOGGING_FAILURE'):
                    assert text in debug.getvalue(), text
                    assert text in crash_log.read_text(encoding='utf-8'), text
                assert debug.getvalue().count(error) == 1, 'Task error was reported twice'
                assert not workspace.diagnostics, workspace.diagnostics
                report = {'result': 'passed', 'workspaceFatalDialog': True, 'fullLogAndCopy': True,
                          'debugOutput': True, 'crashLog': True, 'queuedFailures': True,
                          'backgroundFailures': True, 'loggedErrors': True, 'duplicateDialogSuppressed': True,
                          'confirmationAndSuccessMessagesPreserved': True,
                          'officialAccessRevocationIsNormalDialog':True,'noRepeatedAccessNotice':True,
                          'plainToolErrorsNeverFatal':True,'fatalUsesActualPythonTracebacks':True}
                (artifacts / 'report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
            print(json.dumps(report), flush=True)
        finally:
            logging.getLogger().removeHandler(sink)
            sink.close()
            from core.threader import quit_when_idle
            workspace._shutdown_and_quit = quit_when_idle
            workspace.closed.connect(quit_when_idle)
            workspace.dispose()
            QTimer.singleShot(5000, app.quit)
            app.exec()


if __name__ == '__main__':
    main()
