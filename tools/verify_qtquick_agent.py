# SPDX-License-Identifier: MPL-2.0
"""Exercise a real CLI agent against live Qt Quick dialogs, events and Monaco."""
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
    from PySide6.QtTest import QTest
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtQml import QQmlComponent
    from core import ui_lang,rak_cli
    from core.proc_util import CREATE_NO_WINDOW
    from core.terminal_pty import PtySignals
    from main_ext.class_ThemeUi import theme
    from ui.presentation import LauncherModel
    from ui.terminal import TerminalSession
    from ui.workspace_window import QuickWorkspace
    from tools.quick_test_utils import visual,popup_frame
    from tools.mount_fixture import build_fixture
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
            assert time.monotonic()<end,'Agent UI verification timed out'
            pause()
    def click(window,name):
        item=visual(window,name);window.requestActivate();wait(window.isActive);window.grabWindow()
        QTest.mouseClick(window,Qt.LeftButton,Qt.NoModifier,item.mapToScene(item.boundingRect().center()).toPoint())
    with tempfile.TemporaryDirectory(prefix='rak-agent-ui-') as temporary:
        root=Path(temporary);port=root/'PORT';port.mkdir();(root/'STOCK').mkdir()
        document=port/'test.txt';document.write_text('alpha\nbeta\n',encoding='utf-8')
        partition=port/'vendor';partition.mkdir();(partition/'build.prop').write_text('ro.test=source\n')
        config=port/'config';config.mkdir()
        (config/'vendor_file_contexts').write_text('/vendor(/.*)? u:object_r:vendor_file:s0\n')
        (config/'vendor_fs_config').write_text('vendor 0 0 0755\n')
        image=build_fixture(port)
        endpoint=root/'ipc.json';helper=root/'cli.py'
        helper.write_text('import sys\nsys.path.insert(0,'+repr(str(repo))+')\nfrom core import rak_cli\n'
                          'rak_cli._ipc_file_path=lambda:'+repr(str(endpoint))+'\nrak_cli._main_cli()\n',encoding='utf-8')
        preferences=LauncherModel(settings={'animations':False,'effects':'low'},profile={})
        artifacts=repo/'build/qtquick_migration/agent';artifacts.mkdir(parents=True,exist_ok=True)
        with patch.object(rak_cli,'_ipc_file_path',return_value=str(endpoint)):
            workspace=QuickWorkspace(str(root),'PORT','STOCK',preferences=preferences,settings_path=str(root/'settings.json'),
                terminal_factory=lambda cwd=None,**kwargs:TerminalSession(manager=FakePty(cwd),**kwargs),enable_private_services=False)
            window,session=workspace.window,workspace.session
            clients=[]
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
            def rejected(*args,contains):
                process,output=start_cli(*args);wait(lambda:process.poll() is not None)
                output.seek(0);text=output.read().decode('utf-8',errors='replace')
                assert process.returncode!=0 and contains in text,text
            def payload(name,data):
                path=root/(name+'.json');path.write_text(json.dumps(data,ensure_ascii=False),encoding='utf-8');return str(path)
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
            try:
                wait(lambda:workspace._ready_emitted);workspace.reveal();wait(lambda:not workspace._entry_transition_active)
                rak_cli.attach(workspace);rak_cli.start_ipc_server()
                capabilities=cli('capabilities','--json');assert capabilities['editing']['undo']
                assert capabilities['settings']['agent']=={'autosave':True,'highlight':True,'ui_triggers':False}
                rejected('ui','open',str(document),contains='UI access is disabled')
                rejected('ui','trigger','workspaceToolbarExpand',contains='UI access is disabled')
                assert not session._tab_data
                background=partition/'agent.txt';background.write_text('background original\n')
                disk=cli('ui','read',str(background));assert disk['background'] and not session._tab_data
                replacement=root/'replacement text.txt';replacement_text="# Data only: Remove-Item '\\n'\nbackground saved\n";replacement.write_text(replacement_text,encoding='utf-8')
                edit_client=start_cli('ui','edit',str(background),'--revision',disk['revision'],'--content-file',str(replacement),'--wait','5')
                assert 'Remove-Item' not in ' '.join(edit_client[0].args),'Replacement contents must never enter a shell command'
                edited=result(edit_client)
                assert edited['state']=='applied' and edited['response']['saved'],edited
                assert background.read_text()==replacement_text and not session._tab_data
                assert session.agentActivity.paths.get(session.agentActivity.key(str(background)),{}).get('state')!='held','Completed edits must release ownership'
                # The user can see activity without an editor opening.
                session.tree.revealPath(str(background));wait(lambda:not session.tree.busy)
                session._agent.activity.begin('fixture:activity',[str(background)],'write');pause()
                wave=visual(window,'agentFileHighlight_'+str(background));assert wave.property('active')
                assert visual(window,'agentFileHighlight_'+str(partition)).property('active')
                assert visual(window,'agentTreeHighlight_'+str(port)).property('active')
                click(window,'treeBranchArrow:'+str(partition));pause()
                assert visual(window,'agentFileHighlight_'+str(partition)).property('active')
                click(window,'treeBranchArrow:'+str(partition));pause()
                wave=visual(window,'agentFileHighlight_'+str(background));assert wave.property('active'),'Row paths must refresh after expansion'
                preferences.refreshPreferences(preferences.paletteName,preferences.strings['languageKey'],{'animations':True})
                phase=wave.property('phase');wait(lambda:wave.property('phase')!=phase,seconds=3)
                wait(lambda:wave.opacity()>.099)
                window.grabWindow().save(str(artifacts/'agent_file_activity.png'))
                session._agent.activity.end('fixture:activity')
                wait(lambda:0<wave.opacity()<.095)
                assert wave.isVisible(),'Release must fade the full highlight out'
                wait(lambda:not wave.isVisible())
                preferences.refreshPreferences(preferences.paletteName,preferences.strings['languageKey'],{'animations':False})
                highlight_settings=session.projectSettings;highlight_settings['agent']['highlight']=False;session.applySettings(highlight_settings)
                assert not session.agentActivity.paths and not wave.property('active')
                highlight_settings['agent']['highlight']=True;session.applySettings(highlight_settings)
                cli('ui','guide','agent.ui_triggers')
                settings_dialog=visual(window,'workspaceSettings');wait(lambda:settings_dialog.property('opened'))
                settings_scroll=visual(window,'workspaceSettingsScroll');assert settings_scroll.property('contentY')>0
                assert settings_dialog.property('guidedTarget')=='agent.ui_triggers'
                window.grabWindow().save(str(artifacts/'agent_settings_guide.png'))
                assert not session.projectSettings['agent']['ui_triggers']
                click(window,'workspaceSettingsToggle_agent_ui_triggers');click(window,'workspaceSettingsApply')
                wait(lambda:not settings_dialog.property('visible'))
                assert session.projectSettings['agent']['ui_triggers']
                assert json.loads((root/'settings.json').read_text())['agent']['ui_triggers']
                controls=cli('ui','controls')['controls']
                assert all(not item['name'].startswith('workspaceSettings') for item in controls)
                target=next(item for item in controls if item['name'] in ('workspaceToolbarExpand','workspaceAction_layout') and item['available'])
                scrolled=cli('ui','scroll',target['id']);assert scrolled['state']=='activated',scrolled
                activated=cli('ui','trigger',target['id']);assert activated['state']=='activated',activated
                if target['name']=='workspaceToolbarExpand':
                    wait(lambda:visual(window,'workspaceToolbarPopup').property('opened'))
                    QTest.keyClick(window,Qt.Key_Escape);pause()
                else:cli('ui','trigger',target['id'])
                cli('ui','guide','agent.highlight');wait(lambda:settings_dialog.property('opened'))
                rejected('ui','trigger','workspaceSettingsApply',contains='user-only dialog')
                click(window,'workspaceSettingsCancel');wait(lambda:not settings_dialog.property('visible'))
                # Keep the existing manual-save/undo regression as an explicit
                # preference, then cover default autosave below.
                preferences_data=session.projectSettings;preferences_data['agent']['autosave']=False;session.applySettings(preferences_data)
                cli('ui','open',str(document),'--line','2','--column','1','--end-column','5')
                wait(lambda:session.editor.bridge._ready)
                wait(lambda:session.editor.bridge._selections.get(str(document),{}).get('text')=='beta')
                evaluate("editor.executeEdits('user',[{range:new monaco.Range(1,1,1,6),text:'user draft'}]);true")
                wait(lambda:session.editor.bridge._tabs[str(document)]['content'].startswith('user draft'))
                context=cli('context','--content','--json');doc=context['documents'][0]
                assert doc['unsaved'] and doc['content'].startswith('user draft'),context
                assert doc['selection']['startLineNumber']==1 or doc['selection']['text']=='beta'
                assert document.read_text()=='alpha\nbeta\n'
                assert context['focus']=='editor',context['focus']

                cursor=cli('events','--json')['cursor']
                waiting=start_cli('events','--since',str(cursor),'--types','document','--wait','5','--json')
                evaluate("editor.executeEdits('user',[{range:new monaco.Range(1,1,1,1),text:'event '}]);true")
                events=result(waiting);assert any(row['type']=='document.changed' for row in events['events'])

                ask=start_cli('ui','ask','--from',payload('ask',{'title':'Choose a branch','message':'Which branch should I use?','choices':['Source','Work branch']}),'--wait','5')
                dialog=visual(window,'workspaceAgentDialog');wait(lambda:dialog.property('opened'))
                assert dialog.property('answer')=='','A choice was silently selected'
                dialog.setProperty('answer','Work branch');QMetaObject.invokeMethod(dialog,'submit')
                answer=result(ask);assert answer['state']=='answered' and answer['response']['answer']=='Work branch'
                wait(lambda:not dialog.property('visible'))

                current=cli('ui','read',str(document));before=current['content'];proposed=before+'agent line\n'
                review=cli('ui','review','--from',payload('review',{'title':'Review agent edit','path':str(document),'revision':current['revision'],'content':proposed}))
                wait(lambda:dialog.property('opened'))
                assert '+agent line' in visual(window,'workspaceAgentDiff').property('text')
                popup_frame(window,dialog).save(str(artifacts/'review_dark.png'))
                initial_palette=preferences.paletteName
                preferences.choosePalette('light');wait(lambda:preferences.paletteName=='light');pause()
                popup_frame(window,dialog).save(str(artifacts/'review_light.png'))
                preferences.choosePalette(initial_palette);pause()
                QMetaObject.invokeMethod(dialog,'submit')
                wait(lambda:session._agent.response(review['id'])['state']=='applied')
                assert evaluate('editor.getValue()')==proposed
                assert document.read_text()=='alpha\nbeta\n'
                evaluate("editor.trigger('keyboard','undo',null);true")
                wait(lambda:session.editor.bridge._tabs[str(document)]['content']==before)
                assert evaluate('editor.getValue()')==before,'Agent edit did not create one undo step'

                current=cli('ui','read',str(document))
                stale=cli('ui','review','--from',payload('stale',{'path':str(document),'revision':current['revision'],'content':'should not overwrite user'}))
                wait(lambda:dialog.property('opened'))
                evaluate("editor.executeEdits('user',[{range:new monaco.Range(1,1,1,1),text:'new typing '}]);true")
                wait(lambda:session.editor.bridge._tabs[str(document)]['content'].startswith('new typing'))
                QMetaObject.invokeMethod(dialog,'submit');wait(lambda:session._agent.response(stale['id'])['state']=='conflict')
                assert evaluate('editor.getValue()').startswith('new typing')
                wait(lambda:not dialog.property('visible'))

                first=cli('ui','ask','--from',payload('first',{'message':'First queued question?'}))
                second=cli('ui','ask','--from',payload('second',{'message':'Second queued question?'}))
                wait(lambda:dialog.property('opened'));assert session._agent._presented==first['id']
                QMetaObject.invokeMethod(dialog,'dismiss')
                wait(lambda:session._agent._presented==second['id'] and dialog.property('opened'))
                cli('ui','cancel',second['id']);wait(lambda:not dialog.property('visible'))
                assert cli('ui','response',first['id'])['state']=='cancelled'
                assert cli('ui','response',second['id'])['state']=='cancelled'
                preferences_data=session.projectSettings;preferences_data['agent']['autosave']=True;session.applySettings(preferences_data)
                current=cli('ui','read',str(document));autosaved=current['content']+'autosaved agent line\n'
                replacement.write_text(autosaved,encoding='utf-8')
                autosave=cli('ui','edit',str(document),'--revision',current['revision'],'--content-file',str(replacement),'--wait','5')
                assert autosave['state']=='applied' and autosave['response']['saved'],autosave
                assert document.read_text()==autosaved and not session.editor.bridge.is_unsaved(str(document))
                cli('ui','reveal',str(document));wait(lambda:not session.tree.busy)
                branch_cursor=cli('events','--json')['cursor']
                # Existing branch commands and the new context/event API share one state.
                branch_client=start_cli('branch','create',str(partition),'agent_work','purple')
                process,output=branch_client;wait(lambda:process.poll() is not None)
                output.seek(0);branch_output=output.read().decode('utf-8',errors='replace')
                assert process.returncode==0,branch_output
                wait(lambda:all(not directory.busy for directory in session.directories))
                branches=cli('context','--json')['branches']
                assert any(part['active_branch']=='agent_work' for tree in branches for part in tree['partitions']),branches
                branch_events=cli('events','--since',str(branch_cursor),'--types','branch','--json')
                assert branch_events['events'],'Branch change did not reach the agent event journal'
                cli('ui','open',str(partition/'build.prop'))
                wait(lambda:session.activeTab==str(partition/'build.prop'))
                branch_document=cli('ui','read')
                assert branch_document['branch']['branch_name']=='agent_work',branch_document
                edited=cli('ui','edit','--from',payload('branch_edit',{'path':branch_document['path'],
                    'revision':branch_document['revision'],'content':'ro.test=agent\n'}),'--wait','5')
                assert edited['state']=='applied',edited
                assert (partition/'build.prop').read_text()=='ro.test=source\n'
                session.dispatch('mount',{'path':str(image)})
                wait(lambda:session.mounts.reference(str(image)) is not None)
                mounted='mount://'+str(image)+'//etc/native.txt'
                cli('ui','open',mounted,'--line','1','--end-column','9')
                wait(lambda:session.activeTab==mounted)
                mounted_document=cli('ui','read',mounted)
                assert mounted_document['virtual'] and not mounted_document['read_only'],mounted_document
                mounted_edit=cli('ui','edit','--from',payload('mounted_edit',{'path':mounted,'revision':mounted_document['revision'],'content':'Mounted agent draft\n'}),'--wait','5')
                assert mounted_edit['state']=='applied',mounted_edit
                assert cli('ui','read',mounted)['content']=='Mounted agent draft\n'
                assert not visual(window,'workspaceFatalMessage').property('visible')
                assert not workspace.diagnostics,workspace.diagnostics
                report={'result':'passed','realCliProcesses':True,'authenticatedIpc':True,'liveUnsavedContext':True,
                    'monacoSelectionAndNavigation':True,'eventLongPoll':True,'uiQuestionResponse':True,
                    'reviewDiff':True,'revisionConflictPreservesTyping':True,'singleStepUndo':True,
                    'requestsQueuedAndCancellable':True,'fileTreeReveal':True,'manualSavePreferencePreserved':True,
                    'neoBranchContextEventsAndBufferEdits':True,'darkAndLightReview':True,
                    'liveFocusContext':True,'mountedDocumentReadAndEdit':True,'agentAutosave':True,
                    'backgroundEditWithoutOpeningEditor':True,'activityIncludesParentFolder':True,'uiPermissionGatesNavigationAndControls':True,
                    'settingsGuideScrollsAndHighlights':True,'settingsRemainUserOnly':True,'registeredControlActivation':True,'settingsPersistInJson':True,
                    'fullRowRainbowCrossfades':True,'activityReleasesAfterAcknowledgment':True,'editWorksWithUiAccessOffAndOn':True,
                    'replacementTextNeverEntersShell':True,'highlightFollowsExpandedRowPaths':True,'treeHeaderShowsActivity':True}
                (artifacts/'report.json').write_text(json.dumps(report,indent=2),encoding='utf-8');print(json.dumps(report),flush=True)
            finally:
                for process,output in clients:
                    if process.poll() is None:process.kill();process.wait()
                    output.close()
                rak_cli.detach(workspace);evaluator.deleteLater()
                from core.threader import quit_when_idle
                workspace.closed.connect(quit_when_idle);workspace._shutdown_and_quit=quit_when_idle
                workspace.dispose();QTimer.singleShot(5000,app.quit);app.exec()


if __name__=='__main__':main()
