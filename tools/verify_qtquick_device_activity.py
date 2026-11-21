# SPDX-License-Identifier: MPL-2.0
"""Verify main loading, mounted-editor actions and activity CLI with inert device processes."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
from unittest.mock import patch


def main():
    repo=Path(__file__).resolve().parents[1];os.chdir(repo);sys.path.insert(0,str(repo))
    from core.fonts import configure_text_rendering_env,apply_app_font
    configure_text_rendering_env()
    from ui.runtime import initialize_quick_runtime
    initialize_quick_runtime(webengine=True)
    from PySide6.QtCore import Qt,QTimer,QEventLoop,QMetaObject
    from PySide6.QtGui import QGuiApplication
    from core import ui_lang,rak_cli
    from core.proc_util import CREATE_NO_WINDOW
    from core.terminal_pty import PtySignals
    from main_ext.class_ThemeUi import theme
    from ui.presentation import LauncherModel
    from ui.terminal import TerminalSession
    from ui.workspace_window import QuickWorkspace
    from tools.mount_fixture import build_fixture
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
    def wait(predicate,seconds=20):
        end=time.monotonic()+seconds
        while not predicate():
            assert time.monotonic()<end,'Device/activity UI verification timed out'
            pause()
    with tempfile.TemporaryDirectory(prefix='rak-device-ui-') as temporary:
        root=Path(temporary);port=root/'PORT';port.mkdir();(root/'STOCK').mkdir()
        image=build_fixture(port)
        endpoint=root/'private-ipc.json';helper=root/'cli.py';release_flash=threading.Event()
        helper.write_text('import sys\nsys.path.insert(0,'+repr(str(repo))+')\nfrom core import rak_cli\n'
            'rak_cli._ipc_file_path=lambda:'+repr(str(endpoint))+'\nrak_cli._main_cli()\n',encoding='utf-8')
        prefs=LauncherModel(settings={'animations':False,'effects':'low','treeMode':'single_dir'},profile={})
        release_file=threading.Event()
        with patch.object(rak_cli,'_ipc_file_path',return_value=str(endpoint)):
            workspace=QuickWorkspace(str(root),'PORT','STOCK',preferences=prefs,settings_path=str(root/'settings.json'),
                terminal_factory=lambda cwd=None,**kwargs:TerminalSession(manager=FakePty(cwd),**kwargs),enable_private_services=False)
            window,session=workspace.window,workspace.session
            backend=session._android_tools;calls=[]
            def fixture_run_native(tool,args,*,cwd=None,timeout_ms=None,cancel=None,on_output=None,on_progress=None):
                arg_list=list(args);calls.append(arg_list);buf=[]
                def emit(text):
                    buf.append(text)
                    if on_output:on_output(text)
                if arg_list==['getvar','all']:
                    emit('< waiting for any device >\n')
                    return {'exit_code':1,'stdout':''.join(buf),'stderr':'','output':''.join(buf)}
                if arg_list==['devices']:
                    emit('List of devices attached\n')
                    return {'exit_code':0,'stdout':''.join(buf),'stderr':'','output':''.join(buf)}
                if arg_list and arg_list[0]=='flash':
                    emit('Sending sparse 1/2\n');emit('Writing OKAY\n')
                    if arg_list[1]=='vendor':
                        emit('FAILED (AdbWriteEndpointSync failed: The semaphore timeout period has expired. (121))\n')
                        return {'exit_code':1,'stdout':'','stderr':''.join(buf),'output':''.join(buf)}
                    while not release_flash.is_set() and not (cancel is not None and cancel.is_cancelled()):
                        time.sleep(.02)
                    emit('Sending sparse 2/2\n');emit('Writing OKAY\n')
                    return {'exit_code':0,'stdout':''.join(buf),'stderr':'','output':''.join(buf)}
                return {'exit_code':0,'stdout':'OKAY\n','stderr':'','output':'OKAY\n'}
            backend._run_native=fixture_run_native
            def cli(*args,ok=True):
                with tempfile.TemporaryFile() as output:
                    process=subprocess.Popen([sys.executable,str(helper),*args],cwd=repo,stdout=output,stderr=subprocess.STDOUT,creationflags=CREATE_NO_WINDOW)
                    try:wait(lambda:process.poll() is not None)
                    finally:
                        if process.poll() is None:process.kill();process.wait()
                    output.seek(0);text=output.read().decode('utf-8',errors='replace')
                assert (process.returncode==0)==ok,(args,text)
                return json.loads(text)
            try:
                wait(lambda:workspace._ready_emitted);workspace.reveal();wait(lambda:not workspace._entry_transition_active)
                rak_cli.attach(workspace);rak_cli.start_ipc_server()
                fatal=visual(window,'workspaceFatalMessage');normal=visual(window,'launcherMessage')
                backend.refresh();wait(lambda:not backend.refreshing)
                assert backend.status=='No device connected.'
                assert not fatal.property('visible') and not normal.property('visible')

                session.dispatch('mount',{'path':str(image)})
                wait(lambda:session.mounts.reference(str(image)) is not None and not session.tree.busy)
                session.tree.activate(str(image))
                mounted_etc='mount://'+str(image)+'//etc';session.tree.activate(mounted_etc)
                mounted_file=mounted_etc+'/native.txt';session.openPath(mounted_file)
                wait(lambda:session.activeTab==mounted_file and session.editor.bridge._ready)
                session.editor.bridge.on_content_changed(mounted_file,'Mounted editor buffer\n')
                session.dispatch('android',{'path':mounted_file})
                dialog=visual(window,'workspaceAndroidTools');wait(lambda:dialog.property('opened'))
                assert not fatal.property('visible')
                QMetaObject.invokeMethod(dialog,'dismiss');wait(lambda:not dialog.property('visible'))

                session._tasks.enqueue('Filesystem operation',lambda:(True,release_file.wait(20)),lambda *_:None)
                queue_before=session.task_queue.snapshot()
                backend.assign('system','system.img',str(image));backend.flashAll()
                wait(lambda:any(row['label']=='Flash system' and row['progress']==50 for row in session.taskActivity))
                assert session.task_queue.snapshot()==queue_before
                assert 'Flash system 50%' in session.status,session.status
                query_count=len(calls);backend.refresh();assert len(calls)==query_count
                status=cli('activity','status','--json')
                assert status['busy'] and len(status['tasks'])==2,status
                assert status['android']['running'] and status['android']['progress']==50,status
                assert any(row['label']=='Flash system' and row['state']=='running' for row in status['tasks'])
                terminal_row=next(row for row in session._terminals if row['key']==session._process_terminal_key)
                terminal_manager=terminal_row['session']._manager
                assert 'Sending sparse 1/2' in terminal_manager.get_plain_text()
                pane=visual(window,'workspaceTerminal_'+terminal_row['key'])
                wait(lambda:pane.property('contentReady'))
                artifacts=repo/'build/qtquick_migration/device_activity';artifacts.mkdir(parents=True,exist_ok=True)
                window.grabWindow().save(str(artifacts/'main_flash_progress.png'))
                release_flash.set();wait(lambda:not backend.running)

                backend.assign('vendor','vendor.img',str(image));backend.flashAll()
                wait(lambda:not backend.running and normal.property('opened'))
                assert not fatal.property('visible'),'Transport failure was classified as an application crash'
                details=normal.property('details');details=details.toVariant() if hasattr(details,'toVariant') else details
                assert 'AdbWriteEndpointSync' in details['log']
                failed=cli('activity','--failed','--json')
                entry=failed['history'][0]
                assert not entry['ok'] and '(121)' in entry['detail']
                detail=cli('activity','show',entry['id'],'--json')
                assert 'Sending sparse' in detail['entry']['output']
                assert sum(args and args[0]=='flash' and args[1]=='vendor' for args in calls)==1
                cleared=cli('activity','clear','--yes','--json')
                assert cleared['busy'] and not session.taskHistory
                assert session.task_queue.snapshot()==queue_before
                QMetaObject.invokeMethod(normal,'acceptMessage');wait(lambda:not normal.property('visible'))
                release_file.set();wait(lambda:not session._tasks.busy)
                from core import kitchen_process
                session._tasks.enqueue('Kitchen process fixture',lambda:kitchen_process.run(
                    [sys.executable,'-c','print("CORE_TOOL_THROUGH_RAK_TERMINAL")'],capture_output=True,text=True),lambda *_:None)
                wait(lambda:not session._tasks.busy and 'CORE_TOOL_THROUGH_RAK_TERMINAL' in terminal_manager.get_plain_text())
                assert not workspace.diagnostics,workspace.diagnostics
                report={'result':'passed','mountedEditorAndroidTools':True,'parallelDiscoveryNoFatal':True,
                    'flashBypassesFileQueue':True,'mainLoadingShowsRealProgress':True,'liveActivityCliJson':True,
                    'fullFailureOutput':True,'transportFailureIsNotCrash':True,'noAutomaticFlashRetry':True,
                    'clearHistoryKeepsRunningTasks':True,'fastbootRunsInRakTerminal':True,
                    'workerToolOutputInRakTerminal':True,'hardwareCommandsIssued':False}
                (artifacts/'report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
                print(json.dumps(report),flush=True)
            finally:
                release_file.set();release_flash.set()
                rak_cli.detach(workspace)
                from core.threader import quit_when_idle
                workspace.closed.connect(quit_when_idle);workspace._shutdown_and_quit=quit_when_idle
                workspace.dispose();QTimer.singleShot(5000,app.quit);app.exec()


if __name__=='__main__':main()
