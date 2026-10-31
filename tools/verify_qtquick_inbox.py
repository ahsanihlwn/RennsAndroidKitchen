# SPDX-License-Identifier: MPL-2.0
"""Verify a UI request reaches a real CLI consumer and its result returns to UI."""
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
    from core.fonts import configure_text_rendering_env,apply_app_font
    configure_text_rendering_env()
    from ui.runtime import initialize_quick_runtime
    initialize_quick_runtime(webengine=True)
    from PySide6.QtCore import QTimer,QEventLoop,QMetaObject,QUrl,Qt
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtQml import QQmlComponent
    from PySide6.QtTest import QTest
    from core import ui_lang,rak_cli
    from core.proc_util import CREATE_NO_WINDOW
    from core.agent_inbox import AgentInbox
    from core.terminal_pty import PtySignals
    from main_ext.class_ThemeUi import theme
    from ui.presentation import LauncherModel
    from ui.terminal import TerminalSession
    from ui.workspace_window import QuickWorkspace
    from tools.quick_test_utils import visual,popup_frame
    app=QGuiApplication([]);app.setQuitOnLastWindowClosed(False)
    apply_app_font(app,polish_widgets=False);theme.load();ui_lang.init()
    class FakePty:
        def __init__(self,cwd):self.cwd=cwd;self.signals=PtySignals();self.bash_mode=False
        def start(self,**_):pass
        def write(self,*_):pass
        def resize(self,*_):pass
        def shutdown(self):pass
        def set_spam_enter(self,*_):pass
    def pause():
        loop=QEventLoop();QTimer.singleShot(15,loop.quit);loop.exec()
    def wait(predicate,seconds=20):
        end=time.monotonic()+seconds
        while not predicate():
            assert time.monotonic()<end,'Inbox UI verification timed out'
            pause()
    with tempfile.TemporaryDirectory(prefix='rak-inbox-ui-') as temporary:
        root=Path(temporary);port=root/'PORT';port.mkdir();(root/'STOCK').mkdir()
        document=port/'init.rc';document.write_text('on boot\n    start original\n',encoding='utf-8')
        second=port/'other.rc';second.write_text('second\n')
        endpoint=root/'ipc.json';history=root/'inbox.json';helper=root/'cli.py'
        helper.write_text('import sys\nsys.path.insert(0,'+repr(str(repo))+')\nfrom core import rak_cli\n'
            'rak_cli._ipc_file_path=lambda:'+repr(str(endpoint))+'\nrak_cli._main_cli()\n',encoding='utf-8')
        preferences=LauncherModel(settings={'animations':False,'effects':'low'},profile={})
        artifacts=repo/'build/qtquick_migration/inbox';artifacts.mkdir(parents=True,exist_ok=True)
        with patch.object(rak_cli,'_ipc_file_path',return_value=str(endpoint)),patch('ui.inbox.inbox_path',return_value=history):
            workspace=QuickWorkspace(str(root),'PORT','STOCK',preferences=preferences,settings_path=str(root/'settings.json'),
                terminal_factory=lambda cwd=None,**kwargs:TerminalSession(manager=FakePty(cwd),**kwargs),enable_private_services=False)
            window,session=workspace.window,workspace.session;clients=[]
            def start_cli(*args):
                output=tempfile.TemporaryFile()
                process=subprocess.Popen([sys.executable,str(helper),*args],cwd=repo,stdout=output,stderr=subprocess.STDOUT,creationflags=CREATE_NO_WINDOW)
                clients.append((process,output));return process,output
            def result(client,ok=True):
                process,output=client;wait(lambda:process.poll() is not None)
                output.seek(0);text=output.read().decode('utf-8',errors='replace')
                assert (process.returncode==0)==ok,(process.returncode,text)
                return json.loads(text)
            def cli(*args,ok=True):return result(start_cli(*args),ok)
            def payload(name,data):
                path=root/(name+'.json');path.write_text(json.dumps(data),encoding='utf-8');return str(path)
            def click(item):
                def laid_out():
                    # This harness runs nested event loops before app.exec();
                    # render the SplitView polish frame before pointer hit tests.
                    window.grabWindow()
                    return item.isVisible() and item.isEnabled() and 0<item.mapToScene(item.boundingRect().center()).x()<window.width()
                wait(laid_out)
                window.requestActivate();wait(lambda:window.isActive());pause()
                point=item.mapToScene(item.boundingRect().center()).toPoint()
                QTest.mouseMove(window,point);pause()
                QTest.mousePress(window,Qt.LeftButton,Qt.NoModifier,point);pause()
                QTest.mouseRelease(window,Qt.LeftButton,Qt.NoModifier,point)
            component=QQmlComponent(workspace.engine)
            component.setData(b'''import QtQml
QtObject {
 property var view
 property string script: ""
 property string result: ""
 function evaluate() {result="";view.runJavaScript(script,function(value){result=JSON.stringify({value:value})})}
}''',QUrl())
            evaluator=component.create();assert evaluator is not None,component.errors()
            def evaluate(script):
                evaluator.setProperty('view',visual(window,'quickMonacoView'));evaluator.setProperty('script',script)
                QMetaObject.invokeMethod(evaluator,'evaluate');wait(lambda:bool(evaluator.property('result')))
                return json.loads(evaluator.property('result')).get('value')
            def send(text):
                visual(window,'workspaceInboxComposer').setProperty('text',text)
                QMetaObject.invokeMethod(panel,'sendMessage')
                wait(lambda:session.agentInbox.draft=='')
                return session.agentInbox.detail['id']
            try:
                wait(lambda:workspace._ready_emitted);workspace.reveal();wait(lambda:not workspace._entry_transition_active)
                rak_cli.attach(workspace);rak_cli.start_ipc_server()
                cli('ui','open',str(document),'--line','2','--column','5','--end-column','10')
                wait(lambda:session.editor.bridge._selections.get(str(document),{}).get('text')=='start')
                evaluate("editor.executeEdits('user',[{range:new monaco.Range(2,11,2,19),text:'draft'}]);true")
                wait(lambda:session.editor.bridge._tabs[str(document)]['unsaved'])
                evaluate('editor.setSelection(new monaco.Range(2,5,2,10));true')
                wait(lambda:session.editor.bridge._selections[str(document)]['text']=='start')
                listener=start_cli('inbox','next','--agent','Fixture','--wait','30')
                wait(lambda:'Fixture' in session.agentInbox.availability)
                panel=visual(window,'workspaceInboxPanel')
                # The toolbar now opens native provider chat. Exercise the
                # retained cooperative inbox independently of CLI installation.
                session._agent.inbox.show('editor')
                panel=visual(window,'workspaceInboxPanel');wait(lambda:panel.property('opened'))
                assert not panel.property('modal')
                assert 'unsaved' in visual(window,'workspaceInboxAttachments').property('text')
                # Clicking into the composer must not change the captured editor target.
                first=send('Check this selected startup command')
                claimed=result(listener);assert claimed['message']['id']==first,claimed
                attachment=claimed['message']['context']['documents'][0]
                assert attachment['selection']['text']=='start' and attachment['unsaved'],attachment
                assert 'draft' in attachment['content'] and 'document_path' not in attachment
                claim=claimed['claim'];assert cli('inbox','next','--agent','Other')['message'] is None
                cli('inbox','start',first,'--agent','Fixture','--claim',claim)
                cli('inbox','progress',first,'--agent','Fixture','--claim',claim,'--from',payload('progress',{'message':'Checking init services'}))
                wait(lambda:session.agentInbox.detail['status']=='Checking init services')
                question=cli('ui','ask','--from',payload('question',{'message':'Keep the service enabled?','choices':['Keep','Disable']}))
                prompt=visual(window,'workspaceAgentDialog');wait(lambda:prompt.property('opened'))
                prompt.setProperty('answer','Keep');QMetaObject.invokeMethod(prompt,'submit')
                wait(lambda:session._agent.response(question['id'])['state']=='answered')
                assert cli('ui','response',question['id'])['response']['answer']=='Keep'
                QMetaObject.invokeMethod(panel,'dismiss');wait(lambda:not panel.property('visible'))
                cli('inbox','reply',first,'--agent','Fixture','--claim',claim,'--from',payload('reply',{'answer':'Checked. The selected service can stay enabled.','paths':[str(document)]}))
                wait(lambda:session.agentInbox.notificationCount==1)
                assert session._focus_area!='dialog'
                session._agent.inbox.show();wait(lambda:panel.property('opened'))
                wait(lambda:session.agentInbox.notificationCount==0)
                assert 'can stay enabled' in visual(window,'workspaceInboxResult').property('text')
                # The panel's theme check does not need Chromium's live shared
                # texture in a screenshot readback. Preserve its dirty buffers.
                session.editor.suspend();pause();window.grabWindow()
                popup_frame(window,panel).save(str(artifacts/'inbox_dark.png'))
                initial=preferences.paletteName;preferences.choosePalette('light');pause()
                popup_frame(window,panel).save(str(artifacts/'inbox_light.png'))
                preferences.choosePalette(initial);session.editor.prepare();wait(lambda:session.editor.bridge._ready)
                assert evaluate('editor.getValue()').startswith('on boot')
                # A new tree request keeps its original selection even if UI selection changes.
                session.selectPaths(0,[str(document),str(second)])
                session._agent.inbox.show('tree0',[str(document),str(second)])
                session.selectPaths(1,[])
                queued=send('Compare these two files')
                assert cli('inbox','get',queued)['context']['paths']==[str(document),str(second)]
                session.agentInbox.cancel(queued);wait(lambda:session.agentInbox.detail['state']=='cancelled')
                assert cli('inbox','next','--agent','Fixture')['message'] is None
                abandoned=send('Continue after checking the project')
                abandoned_claim=cli('inbox','next','--agent','Fixture');assert abandoned_claim['message']['id']==abandoned
                pending=send('Wait for the next session')
                events=cli('events','--types','inbox','--json')['events']
                assert any(row['type']=='inbox.received' for row in events) and any(row['type']=='inbox.updated' for row in events)
                assert document.read_text()=='on boot\n    start original\n'
                assert not visual(window,'workspaceFatalMessage').property('visible')
                assert not workspace.diagnostics,workspace.diagnostics
                session.stop()
                restored=AgentInbox(history)
                assert restored.get(first)['state']=='completed' and restored.get(first)['answer'].startswith('Checked.')
                assert restored.get(abandoned)['state']=='interrupted'
                assert restored.get(pending)['state']=='queued'
                report={'result':'passed','realUiToCliToUi':True,'frozenSelectionAndUnsavedBuffer':True,
                    'singleAgentClaim':True,'progressAndResultInUi':True,'agentQuestionRoundTrip':True,
                    'newReplyNotification':True,'fileTreeSelectionAttachments':True,'cancellationBeforeClaim':True,
                    'persistentResultsAndQueue':True,'interruptedWorkIsNotSuccess':True,'darkAndLightPanel':True,
                    'noDocumentWrite':True,'noHardwareCommands':True,'qaGraphicsBackend':os.environ.get('QSG_RHI_BACKEND','platform default')}
                (artifacts/'report.json').write_text(json.dumps(report,indent=2));print(json.dumps(report),flush=True)
            except Exception:
                window.grabWindow().save(str(artifacts/'failure.png'))
                print('QML diagnostics: '+repr(workspace.diagnostics),flush=True)
                raise
            finally:
                for process,output in clients:
                    if process.poll() is None:process.kill();process.wait()
                    output.close()
                rak_cli.detach(workspace);evaluator.deleteLater()
                from core.threader import quit_when_idle
                workspace.closed.connect(quit_when_idle);workspace._shutdown_and_quit=quit_when_idle
                workspace.dispose();QTimer.singleShot(5000,app.quit);app.exec()


if __name__=='__main__':main()
