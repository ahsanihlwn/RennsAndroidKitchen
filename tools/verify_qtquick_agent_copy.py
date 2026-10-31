# SPDX-License-Identifier: MPL-2.0
"""Regress a real agent CLI bulk copy without invoking a model or user files."""
import faulthandler
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from unittest.mock import patch


def main():
    repo=Path(__file__).resolve().parents[1];os.chdir(repo);sys.path.insert(0,str(repo))
    artifacts=repo/'build/qtquick_migration/agent_copy';artifacts.mkdir(parents=True,exist_ok=True)
    crash=(artifacts/'native_crash.log').open('w',encoding='utf-8')
    faulthandler.enable(file=crash,all_threads=True);faulthandler.dump_traceback_later(150,file=crash,exit=True)
    from core.fonts import configure_text_rendering_env,apply_app_font
    configure_text_rendering_env()
    from ui.runtime import initialize_quick_runtime
    initialize_quick_runtime(webengine=True)
    from PySide6.QtCore import QTimer,QEventLoop
    from PySide6.QtGui import QGuiApplication
    from core import ui_lang,rak_cli
    from core.proc_util import CREATE_NO_WINDOW
    from core.terminal_pty import PtySignals
    from main_ext.class_ThemeUi import theme
    from main_ext.class_NeoBranchManager import NeoBranchManager
    from ui.presentation import LauncherModel
    from ui.terminal import TerminalSession
    from ui.workspace_window import QuickWorkspace
    from ui.workspace_cli import WorkspaceCli
    app=QGuiApplication([]);app.setQuitOnLastWindowClosed(False)
    apply_app_font(app,polish_widgets=False);theme.load();ui_lang.init()
    class FakePty:
        def __init__(self,cwd):self.cwd=cwd;self.signals=PtySignals();self.bash_mode=False
        def start(self,**_):pass
        def write(self,*_):pass
        def resize(self,*_):pass
        def shutdown(self):pass
        def set_spam_enter(self,*_):pass
    def wait(predicate,seconds=120):
        end=time.monotonic()+seconds
        while not predicate():
            assert time.monotonic()<end,'Bulk copy UI verification timed out'
            loop=QEventLoop();QTimer.singleShot(5,loop.quit);loop.exec()
    with tempfile.TemporaryDirectory(prefix='rak-agent-copy-') as temporary:
        root=Path(temporary);port=root/'PORT';port.mkdir();(root/'STOCK').mkdir()
        source=port/'files/vendor';source.mkdir(parents=True)
        destination=port/'vendor-scout';destination.mkdir()
        expected={}
        for number in range(256):
            name=f'fixture_{number:04}.txt';content=f'copied fixture {number}\n'.encode()
            (source/name).write_bytes(content)
            if number<16:(destination/name).write_bytes(b'keep original\n')
            else:expected[str(destination/name)]=content
        document=destination/'build.prop';document.write_text('ro.fixture=source\n')
        config=port/'config';config.mkdir()
        (config/'vendor-scout_file_contexts').write_text('/vendor-scout(/.*)? u:object_r:vendor_file:s0\n')
        (config/'vendor-scout_fs_config').write_text('vendor-scout 0 0 0755\n')
        manager=NeoBranchManager(str(port));manager.create_branch('vendor-scout','agent_work','blue')
        manager.set_active_branch('vendor-scout','agent_work')
        endpoint=root/'ipc.json';helper=root/'cli.py'
        helper.write_text('import sys\nsys.path.insert(0,'+repr(str(repo))+')\nfrom core import rak_cli\n'
                          'rak_cli._ipc_file_path=lambda:'+repr(str(endpoint))+'\nrak_cli._main_cli()\n',encoding='utf-8')
        preferences=LauncherModel(settings={'animations':True,'effects':'low','treeMode':'tree'},profile={})
        with patch.object(rak_cli,'_ipc_file_path',return_value=str(endpoint)):
            workspace=QuickWorkspace(str(root),'PORT','STOCK',preferences=preferences,settings_path=str(root/'settings.json'),
                terminal_factory=lambda cwd=None,**kwargs:TerminalSession(manager=FakePty(cwd),**kwargs),enable_private_services=False)
            session=workspace.session;clients=[];ticks=[]
            workspace.quick_cli=WorkspaceCli(session);workspace.task_queue=session.task_queue
            heartbeat=QTimer();heartbeat.setInterval(10);heartbeat.timeout.connect(lambda:ticks.append(time.monotonic()))
            def client(*args):
                output=tempfile.TemporaryFile()
                process=subprocess.Popen([sys.executable,str(helper),*args],cwd=port,stdout=output,stderr=subprocess.STDOUT,creationflags=CREATE_NO_WINDOW)
                clients.append((process,output));return process,output
            def result(job):
                process,output=job;wait(lambda:process.poll() is not None)
                output.seek(0);text=output.read().decode('utf-8',errors='replace')
                assert process.returncode==0,(process.returncode,text)
                return text
            try:
                wait(lambda:workspace._ready_emitted);workspace.reveal();wait(lambda:not workspace._entry_transition_active)
                session.openPath(str(document));wait(lambda:session.activeTab==str(document))
                rak_cli.attach(workspace);rak_cli.start_ipc_server();cursor=session._agent.events.read()['latest']
                heartbeat.start();started=time.monotonic()
                copy=client('cp','-rn','files/vendor/.','vendor-scout/')
                wait(lambda:session._tasks.busy or copy[0].poll() is not None)
                status=client('activity','status','--json')
                status_text=result(status);assert '"state"' in status_text,status_text
                output=result(copy)
                wait(lambda:not session._tasks.busy and not session.tree.busy and not session._agent.activity._pending_touches)
                elapsed=time.monotonic()-started;heartbeat.stop()
                from core.workspace_files import WorkspaceFiles
                files=WorkspaceFiles(str(port))
                for path,content in expected.items():
                    physical,origin=files.view.resolve(path)
                    assert origin=='branch' and Path(physical).read_bytes()==content,path
                    assert not Path(path).exists(),'Copy changed Source: '+path
                    assert path in output,'CLI output lost a copied path: '+path
                for number in range(16):assert (destination/f'fixture_{number:04}.txt').read_bytes()==b'keep original\n'
                events=session._agent.events.read(cursor,types=['agent.file'],limit=512)['events']
                observed={row['data']['path'] for row in events}
                assert observed==set(expected),(len(observed),len(expected))
                assert all('\n' not in path and '\r' not in path for path in observed)
                assert session.activeTab==str(document)
                assert len(session._agent.activity.paths)<=256
                assert len(ticks)>5
                gaps=[b-a for a,b in zip(ticks,ticks[1:])];max_gap=max(gaps,default=0)
                assert max_gap<.75,('GUI heartbeat stalled',max_gap)
                assert not workspace.diagnostics,workspace.diagnostics
                report={'result':'passed','realAuthenticatedCli':True,'command':'rak cp -rn files/vendor/. vendor-scout/',
                    'copiedFiles':len(expected),'preservedFiles':16,'sourceUnchanged':True,'nativeBranchBytesVerified':True,
                    'eachOutputHasIndividualActivityEvent':True,'activityStatusResponsive':True,'activeEditorPreserved':True,
                    'highlightBounded':True,'elapsedSeconds':elapsed,'heartbeatMaxGapSeconds':max_gap,'qmlDiagnostics':workspace.diagnostics}
                (artifacts/'report.json').write_text(json.dumps(report,indent=2),encoding='utf-8');print(json.dumps(report),flush=True)
            finally:
                heartbeat.stop()
                for process,output in clients:
                    if process.poll() is None:process.kill();process.wait()
                    output.close()
                rak_cli.detach(workspace)
                from core.threader import quit_when_idle
                workspace.closed.connect(quit_when_idle);workspace._shutdown_and_quit=quit_when_idle
                workspace.dispose();QTimer.singleShot(5000,app.quit);app.exec();faulthandler.cancel_dump_traceback_later()


if __name__=='__main__':main()
