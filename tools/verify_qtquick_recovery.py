# SPDX-License-Identifier: MPL-2.0
"""Verify recovery completion drives the real loader handoff and queued tasks."""
import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
from types import SimpleNamespace, MethodType
from unittest.mock import patch


def main():
    repo = Path(__file__).resolve().parents[1]
    os.chdir(repo);sys.path.insert(0,str(repo))
    from core.fonts import configure_text_rendering_env, apply_app_font
    configure_text_rendering_env()
    from ui.runtime import initialize_quick_runtime
    initialize_quick_runtime(webengine=True)
    from PySide6.QtCore import QTimer,QEventLoop,QMetaObject
    from PySide6.QtGui import QGuiApplication
    from core import ui_lang
    from core.terminal_pty import PtySignals
    from core.workspace_preparation import prepare_sources
    from main_ext.class_ThemeUi import theme
    from main_ext.class_NeoBranchManager import NeoBranchManager
    from ui.application import LauncherApplication
    from ui.loading import QuickLoadingWindow
    from ui.presentation import LauncherModel
    from ui.terminal import TerminalSession
    from ui.workspace_window import QuickWorkspace
    from tools.quick_test_utils import visual
    app=QGuiApplication([]);app.setQuitOnLastWindowClosed(False)
    apply_app_font(app,polish_widgets=False);theme.load();ui_lang.init()

    class FakePty:
        def __init__(self,cwd):self.cwd=cwd;self.signals=PtySignals();self.bash_mode=False
        def start(self,**_):pass
        def write(self,*_):pass
        def resize(self,*_):pass
        def shutdown(self):pass
        def set_spam_enter(self,*_):pass
    def pause(ms=20):
        loop=QEventLoop();QTimer.singleShot(ms,loop.quit);loop.exec()
    def wait(predicate):
        end=time.monotonic()+15
        while not predicate():
            assert time.monotonic()<end,'Recovery UI verification timed out'
            pause()

    with tempfile.TemporaryDirectory(prefix='rak-recovery-ui-') as temporary:
        root=Path(temporary);base=root/'PORT';(base/'system').mkdir(parents=True);(base/'config').mkdir()
        source=base/'system/file.txt';source.write_text('original')
        (base/'config/system_fs_config').write_text('system 0 0 0755\nsystem/file.txt 0 0 0644\n')
        manager=NeoBranchManager(str(base));manager.create_branch('system','work','green');manager.set_active_branch('system','work')
        overlay=manager.record_edit('system','work',str(source));Path(overlay).write_text('branch edit')
        manager.prepare_for_repack('system','work')
        release,started=threading.Event(),threading.Event()
        def held_prepare(bases,*,progress,cancelled):
            progress('Recovering Source: system — fixture copy in progress')
            started.set()
            while not release.wait(.05):
                if cancelled():return False,'Cancelled'
            return prepare_sources(bases,progress=progress,cancelled=cancelled)
        preferences=LauncherModel(settings={'animations':False},profile={})
        with patch('ui.source_recovery.prepare_sources',side_effect=held_prepare):
            workspace=QuickWorkspace(str(root),'PORT',preferences=preferences,settings_path=str(root/'settings.json'),
                terminal_factory=lambda cwd=None,**options:TerminalSession(manager=FakePty(cwd),**options),enable_private_services=False)
            session,window=workspace.session,workspace.window
            loader=QuickLoadingWindow(workspace.engine,preferences,str(root))
            loader._block_close=True;loader.show()
            entered=[]
            handoff=SimpleNamespace(editor=workspace,loader=loader,model=preferences,_log_project_entered=lambda *_:entered.append(True))
            handoff._show_editor_with_reveal=MethodType(LauncherApplication._show_editor_with_reveal,handoff)
            workspace.ready.connect(lambda:LauncherApplication._editor_ready(handoff,workspace,str(root),time.perf_counter(),'fixture'))
            workspace.preparation_status.connect(loader.set_status)
            errors=[];workspace.preparation_failed.connect(errors.append)
            finished=[];session._source_recovery.finished.connect(lambda ok,value:finished.append((ok,value)))
            debug=io.StringIO()
            try:
                with contextlib.redirect_stdout(debug):
                    wait(started.is_set)
                    # The old implementation timed out here despite an active copy.
                    session._started_at=time.monotonic()-120
                    session._check_ready();pause(200)
                    assert not errors and not workspace._ready_emitted,errors
                    assert session._source_recovery.active and loader._block_close
                    assert loader.isVisible() and 'Recovering Source' in loader.window.property('statusText')
                    release.set()
                    wait(lambda:handoff.loader is None and bool(entered))
                    wait(lambda:not workspace._entry_transition_active)
                    assert not loader._block_close and not session._source_recovery.active
                    assert workspace._ready_emitted and finished[0][0] and not errors
                    assert source.read_text()=='original' and Path(overlay).read_text()=='branch edit'

                    # The same completion API releases the operation queue.
                    manager.prepare_for_repack('system','work')
                    release.clear();started.clear()
                    tasks=session._tasks;continued=[];recovered=[]
                    tasks.recoveryFinished.connect(lambda ok,value:recovered.append(ok))
                    tasks.enqueue('Failed restore',lambda:(False,'NEOBRANCH_RESTORE_FAILED: fixture locked file'),lambda *_:None)
                    tasks.enqueue('Queued operation',lambda:continued.append(True),lambda *_:None)
                    wait(started.is_set)
                    assert tasks.queue.is_busy() and not continued and not recovered
                    release.set()
                    wait(lambda:continued==[True] and not tasks.queue.is_busy())
                    assert recovered==[True] and not tasks.halted and not tasks._recovering
                    assert source.read_text()=='original'
                    fatal=visual(window,'workspaceFatalMessage')
                    if fatal.property('visible'):QMetaObject.invokeMethod(fatal,'acceptMessage')
                    assert not workspace.diagnostics,workspace.diagnostics
                    artifacts=repo/'build/qtquick_migration/recovery';artifacts.mkdir(parents=True,exist_ok=True)
                    window.grabWindow().save(str(artifacts/'recovered.png'))
                    report={'result':'passed','loaderWaitsForRecoveryApi':True,'noThirtySecondRecoveryTimeout':True,
                            'completionDismissesLoader':True,'waitingFlagCleared':True,'queuedTasksResumeFromSignal':True,
                            'sourceAndBranchPreserved':True,'pollingReadinessTimer':False}
                    (artifacts/'report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
                print(json.dumps(report),flush=True)
            finally:
                release.set()
                if handoff.loader is not None:
                    handoff.loader._block_close=False;handoff.loader.close()
                from core.threader import quit_when_idle
                workspace.closed.connect(quit_when_idle);workspace._shutdown_and_quit=quit_when_idle
                workspace.dispose();QTimer.singleShot(5000,app.quit);app.exec()


if __name__=='__main__':main()
