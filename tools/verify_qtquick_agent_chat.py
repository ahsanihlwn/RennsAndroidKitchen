# SPDX-License-Identifier: MPL-2.0
"""Real hidden sidecar, native chat, frozen buffers, permission and stop checks."""
import json
import os
from pathlib import Path
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
    from PySide6.QtCore import QTimer,QEventLoop,Qt,QUrl,QMetaObject,QMimeData,QPointF
    from PySide6.QtGui import QGuiApplication,QImage,QColor,QDragEnterEvent,QDropEvent
    from PySide6.QtQml import QQmlComponent
    from PySide6.QtTest import QTest
    from core import ui_lang,terminal_execution
    from core.terminal_pty import PtySignals
    from ui.presentation import LauncherModel
    from ui.terminal import TerminalSession
    from ui.workspace_window import QuickWorkspace
    from tools.quick_test_utils import visual
    app=QGuiApplication([]);app.setQuitOnLastWindowClosed(False);apply_app_font(app,polish_widgets=False);ui_lang.init()
    def pause():
        loop=QEventLoop();QTimer.singleShot(15,loop.quit);loop.exec()
    def wait(test,seconds=20):
        until=time.monotonic()+seconds
        while not test():
            assert time.monotonic()<until,'Chat verification timed out';pause()
    def click(window,name):
        def exists():
            try:return visual(window,name) is not None
            except AssertionError:return False
        wait(exists)
        item=visual(window,name);assert item is not None,name
        wait(lambda:item.isVisible() and item.isEnabled())
        window.requestActivate();wait(window.isActive)
        # grabWindow synchronizes layout even when Windows occludes the fixture;
        # an update on a static/occluded scene need not emit frameSwapped.
        window.grabWindow()
        QTest.mouseClick(window,Qt.LeftButton,Qt.NoModifier,item.mapToScene(item.boundingRect().center()).toPoint())
    class FakePty:
        def __init__(self,cwd):self.signals=PtySignals();self.cwd=cwd;self.bash_mode=False
        def start(self,**_):pass
        def shutdown(self):pass
        def resize(self,*_):pass
        def set_spam_enter(self,*_):pass
        def write(self,*_):pass
    artifacts=repo/'build/agent_chat/ui';artifacts.mkdir(parents=True,exist_ok=True)
    (artifacts/'report.json').unlink(missing_ok=True)
    provider={'id':'codex','label':'Codex fixture','protocol':'codex','argv':[sys.executable,str(repo/'tools/agent_chat_fixture.py'),'--fixture-thinking'],'path':str(repo/'tools/agent_chat_fixture.py')}
    with tempfile.TemporaryDirectory(prefix='rak-agent-chat-ui-') as temporary:
        root=Path(temporary);(root/'PORT').mkdir();(root/'STOCK').mkdir();path=root/'PORT/build.prop';path.write_text('ro.fixture=original\n')
        prefs=LauncherModel(palette='dark_purple',language='id',settings={'animations':False,'effects':'low'},profile={})
        with patch('core.agent_backends.discover',return_value=[provider]),patch('core.agent_chat_store.project_folder',return_value=root/'chat'),patch('ui.inbox.inbox_path',return_value=root/'inbox.json'):
            workspace=QuickWorkspace(str(root),'PORT','STOCK',preferences=prefs,settings_path=str(root/'settings.json'),
                terminal_factory=lambda cwd=None,**kwargs:TerminalSession(manager=FakePty(cwd),**kwargs),enable_private_services=False)
            session=workspace.session;window=workspace.window;chat=session.agentChat;diagnostics=[]
            workspace.engine.warnings.connect(lambda errors:diagnostics.extend(e.toString() for e in errors))
            original_clipboard=QMimeData();clipboard=app.clipboard();initial_mime=clipboard.mimeData()
            if initial_mime:
                for format_name in initial_mime.formats():original_clipboard.setData(format_name,initial_mime.data(format_name))
                if initial_mime.hasImage():original_clipboard.setImageData(initial_mime.imageData())
            try:
                wait(lambda:workspace._ready_emitted);workspace.reveal();wait(lambda:not workspace._entry_transition_active);wait(lambda:chat.available)
                session.openPath(str(path));wait(lambda:session.editor.bridge._ready)
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
                evaluate("editor.executeEdits('fixture',[{range:editor.getModel().getFullModelRange(),text:'ro.fixture=unsaved\\n'}]);editor.setSelection(new monaco.Range(1,1,1,11));true")
                wait(lambda:session.editor.bridge._tabs[str(path)]['unsaved'])
                baseline=session.terminalSessions.rowCount()
                click(window,'workspaceSendToAgent');wait(lambda:chat.opened and visual(window,'workspaceAgentChatPanel').width()>100)
                assert chat.attachments and 'unsaved' in chat.attachments[0]['label']
                composer=visual(window,'agentChatComposer');composer.setProperty('text','Inspect this selected property')
                with patch.object(terminal_execution,'publisher_for',side_effect=AssertionError('Agent protocol opened a task terminal')):
                    click(window,'agentChatSend');wait(lambda:any(r.get('thinking') for r in chat.messages.snapshot() if r['role']=='thought'))
                    click(window,'agentChatThinking');wait(lambda:visual(window,'agentChatThinkingText').isVisible() and 'Checking the selected property' in visual(window,'agentChatThinkingText').property('text'))
                    assert chat.state=='working' and not chat.pendingRequest
                    window.grabWindow().save(str(artifacts/'thinking.png'))
                    click(window,'agentChatThinking')
                    chat._transport.send({'method':'fixture/continue'})
                    provider['argv']=[arg for arg in provider['argv'] if arg!='--fixture-thinking']
                    wait(lambda:bool(chat.pendingRequest))
                    assert not session.terminalOpen,('terminalOpen',session.terminalOpen,session._terminals)
                    assert session.terminalSessions.rowCount()==baseline,('terminal sessions',baseline,session.terminalSessions.snapshot())
                    for action,label in [('save','Simpen'),('yes','Ya'),('no','Tidak')]:
                        assert visual(window,'agentChatApproval_'+action).property('text')==label
                    deny=visual(window,'agentChatApproval_no');card=visual(window,'agentChatPermissionCard')
                    assert abs(deny.mapToScene(deny.boundingRect().bottomRight()).x()-card.mapToScene(card.boundingRect().bottomRight()).x()+12)<2
                    elapsed=next(r['elapsed'] for r in chat._messages.values() if r['role']=='work')
                    wait(lambda:next(r['elapsed'] for r in chat._messages.values() if r['role']=='work')>elapsed,seconds=3)
                    window.grabWindow().save(str(artifacts/'permission.png'))
                    click(window,'agentChatApproval_save')
                    wait(lambda:chat.state=='completed')
                    assert any('Unsaved buffer preserved ✓' in r['text'] for r in chat.messages.snapshot())
                    wait(lambda:session.agentInbox.detail['state']=='completed')
                    assert chat.messages.snapshot()[-1]['title'].startswith('Selesai dalam ')
                    tool=next(r for r in chat.messages.snapshot() if r['role']=='tool')
                    assert not visual(window,'agentChatTool_'+tool['key']).parentItem().parentItem().property('expanded')
                    assert visual(window,'agentChatWorkStatus').metaObject().className().startswith('RakLabel')
                    assert '›' not in visual(window,'agentChatWorkStatus').property('text')
                    window.grabWindow().save(str(artifacts/'timeline.png'))
                    click(window,'agentChatTool_'+tool['key'])
                    wait(lambda:visual(window,'agentChatToolText').isVisible())
                    assert visual(window,'agentChatTool_'+tool['key']).property('text').startswith('Ran fixture inspect')
                    for other in chat.messages.snapshot():
                        if other['role']=='tool' and other['key']!=tool['key']:assert not visual(window,'agentChatTool_'+other['key']).parentItem().parentItem().property('expanded')
                    window.grabWindow().save(str(artifacts/'activity_logs.png'))
                    window.grabWindow().save(str(artifacts/'conversation.png'))
                    composer.setProperty('text','Decline inspection');click(window,'agentChatSend');wait(lambda:bool(chat.pendingRequest))
                    click(window,'agentChatApproval_no');wait(lambda:chat.state=='failed')
                    assert chat.messages.snapshot()[-1]['title'].startswith('Gagal setelah ')
                    composer.setProperty('text','Approve once');click(window,'agentChatSend');wait(lambda:bool(chat.pendingRequest))
                    click(window,'agentChatApproval_yes');wait(lambda:chat.state=='completed')
                    composer.setProperty('text','Another inspection');click(window,'agentChatSend');wait(lambda:bool(chat.pendingRequest))
                    chat.stop();assert chat.state=='stopping';wait(lambda:chat.state=='interrupted')
                    wait(lambda:session.agentInbox.detail['state']=='cancelled')
                assert path.read_text()=='ro.fixture=original\n'
                assert session.editor.bridge._tabs[str(path)]['content']=='ro.fixture=unsaved\n'
                events=json.dumps(session._agent.events.read(0,limit=512));assert '"claim"' not in events
                composer.setProperty('text','/status');assert chat.send();assert not session.terminalOpen
                window.grabWindow().save(str(artifacts/'status.png'))
                assert evaluate("!!editor.getAction('rak.sendToAgent')")
                with patch('core.agent_backends.discover',return_value=[]):
                    chat.refreshBackends();wait(lambda:not chat.discovering and not chat.available)
                wait(lambda:not evaluate("!!editor.getAction('rak.sendToAgent')"))
                assert not visual(window,'workspaceSendToAgent').isVisible()
                chat.refreshBackends();wait(lambda:not chat.discovering and chat.available)
                # Recreate the old empty AGY response in an isolated test project.
                # Never replay the denied command or modify real user history.
                chat._providers.append({'id':'agy','label':'Antigravity','protocol':'agy','argv':[sys.executable,str(repo/'tools/agent_chat_fixture.py'),'--fixture-agy']})
                chat.selectProvider('agy');chat.changed.emit()
                wait(lambda:not chat.busy and len(chat.models)>1)
                assert chat._transport is not None and chat._request_id==''
                expected_root=Path(session._agent.workspace_context()['working_directory'])
                assert chat._protocol.cwd==str(expected_root) and expected_root!=root
                assert '--new-project' in chat._transport._process.args and '--add-dir' in chat._transport._process.args
                composer.setProperty('text','Fixture workspace path probe');click(window,'agentChatSend');wait(lambda:not chat.running)
                probe=expected_root/'agent_workspace_probe.txt';assert probe.read_text(encoding='utf-8')==str(expected_root)
                assert not (root/'agent_workspace_probe.txt').exists(),'The backend must not write into the Kitchen container'
                old_tab=session.activeTab;composer.setProperty('text','Fixture instant read probe');click(window,'agentChatSend');wait(lambda:not chat.running)
                wave=visual(window,'agentTreeHighlight_'+str(expected_root))
                wait(lambda:wave.property('active'));assert session.activeTab==old_tab
                assert not session._agent.activity._held,'Instant read is completed; only its visual tail remains'
                wait(lambda:wave.opacity()>.02)
                wait(lambda:not wave.property('active'))
                chat._messages.clear();chat._sync_rows()
                assert visual(window,'agentChatModel').isVisible()
                chat._append('legacy:user','user','tes')
                chat._append('legacy:empty','assistant','')
                chat._append('legacy:tool','tool','permission check failed: denied permission',title='run_command',status='failed')
                chat._append('legacy:completed','system','Turn completed',status='completed')
                chat.changed.emit()
                from PySide6.QtCore import QObject
                assert window.findChild(QObject,'agentChatPermissionMode') is None
                assert '--dangerously-skip-permissions' in chat._transport._process.args
                assert [r['role'] for r in chat.messages.snapshot()]==['user','tool','problem']
                pause();window.grabWindow().save(str(artifacts/'no_reply.png'))
                # Long conversation: genuine wheel input suspends following;
                # incoming text must preserve the reading position until Jump.
                chat._messages.clear();chat._sync_rows();chat.clearAttachments();composer.setProperty('text','')
                for number in range(9):
                    chat._append(f'demo:{number}:user','user','Jelaskan properti build.prop yang dipilih.')
                    chat._append(f'demo:{number}:tool','tool','Read-only fixture activity',title='Read selected file',status='completed')
                    chat._append(f'demo:{number}:answer','assistant','File ini menyimpan konfigurasi perangkat Android.\n\n### Yang perlu diperiksa\n\n- **Identitas perangkat**: merek, model, dan nama produk.\n- **Arsitektur CPU**: harus sesuai dengan library vendor.\n- **Versi sistem**: cocokkan dengan target ROM.\n\nPerubahan di editor masih berupa draft dan belum disimpan ke disk.')
                panel=visual(window,'workspaceAgentChatPanel');conversation=visual(window,'agentChatMessages')
                wait(lambda:conversation.property('atYEnd'))
                assert not visual(window,'agentChatJumpLatest').isVisible()
                assert visual(window,'agentChatBackend').width()<=94
                assert visual(window,'agentChatSend').property('iconSource').toString().startswith('image://rak/chat_arrow_up')
                point=conversation.mapToScene(conversation.boundingRect().center()).toPoint()
                from PySide6.QtCore import QPoint
                QTest.wheelEvent(window,point,QPoint(0,480))
                wait(lambda:visual(window,'agentChatJumpLatest').isVisible() and not conversation.property('moving'))
                position=conversation.property('contentY');assert not panel.property('followTail')
                chat._messages['demo:8:answer']['text']+='\n\nTambahan output dari fixture.';chat._sync_rows();pause();pause()
                assert abs(conversation.property('contentY')-position)<2
                window.grabWindow().save(str(artifacts/'reference_scrolled.png'))
                click(window,'agentChatJumpLatest');wait(lambda:conversation.property('atYEnd'))
                wait(lambda:not visual(window,'agentChatJumpLatest').isVisible())
                click(window,'agentChatModel');wait(lambda:visual(window,'agentModel_fixture-model').isVisible())
                assert not visual(window,'agentChatRefreshUsage').isVisible()
                click(window,'agentChatToggleLimits')
                wait(lambda:chat.efforts and chat.limits.get('groups'))
                assert not visual(window,'agentChatViewUsage').isVisible(),'AGY quota bars must not duplicate the usage report'
                native_limits=chat._limits
                # A report remains available as a fallback when no quota was supplied.
                chat._limits={'groups':[],'report':'Fixture usage-only report'};chat.changed.emit();pause()
                click(window,'agentChatViewUsage');wait(lambda:visual(window,'agentChatUsageReport').isVisible())
                chat._limits=native_limits;chat.changed.emit();pause()
                assert not visual(window,'agentChatViewUsage').isVisible() and not visual(window,'agentChatUsageReport').isVisible()
                click(window,'agentChatEffort');click(window,'choice_agentChatEffort_high');wait(lambda:chat.effort=='high')
                window.grabWindow().save(str(artifacts/'reference_models.png'))
                click(window,'agentModel_fixture-model')
                click(window,'agentChatAttach');wait(lambda:visual(window,'choice_agentChatAttach_files').isVisible())
                window.grabWindow().save(str(artifacts/'attachments.png'))
                click(window,'choice_agentChatAttach_clear')
                extra=root/'PORT/notes.txt';extra.write_text('fixture attachment')
                chat.addAttachments([QUrl.fromLocalFile(str(extra)).toString()]);assert str(extra) in [a['path'] for a in chat.attachments]
                # Native drag/drop references external files, never moves them.
                chat.clearAttachments();composer.setProperty('text','')
                image=QImage(128,80,QImage.Format_ARGB32);image.fill(QColor('#9c7bed'));image.setPixelColor(0,0,QColor(1,2,3,17))
                external_image=root/'gambar # luar.png';assert image.save(str(external_image))
                external_text=root/'catatan # luar.txt';external_text.write_text('private fixture bytes must not be inlined')
                mime=QMimeData();mime.setUrls([QUrl.fromLocalFile(str(external_image)),QUrl.fromLocalFile(str(external_text))])
                position=composer.mapToScene(composer.boundingRect().center()).toPoint()
                drag=QDragEnterEvent(position,Qt.CopyAction|Qt.MoveAction,mime,Qt.LeftButton,Qt.NoModifier)
                app.sendEvent(window,drag);assert drag.isAccepted(),'Native drag must enter the chat drop target'
                drop=QDropEvent(QPointF(position),Qt.CopyAction|Qt.MoveAction,mime,Qt.LeftButton,Qt.NoModifier)
                app.sendEvent(window,drop);assert drop.isAccepted() and drop.dropAction()==Qt.CopyAction
                wait(lambda:len(chat.attachments)==2)
                assert all(row['external'] for row in chat.attachments)
                assert {row['path'] for row in chat.attachments}=={str(external_image),str(external_text)}
                assert external_image.exists() and external_text.read_text()=='private fixture bytes must not be inlined'
                # Real Ctrl+V pastes pixels as a PNG path and keeps image alpha.
                clipboard.setImage(image);click(window,'agentChatComposer');QTest.keyClick(window,Qt.Key_V,Qt.ControlModifier)
                wait(lambda:len(chat.attachments)==3)
                cached=next(row['path'] for row in chat.attachments if row.get('source')=='clipboard')
                assert Path(cached).parent==root/'chat/attachments'
                assert QImage(cached).pixelColor(0,0)==image.pixelColor(0,0)
                assert composer.property('text')==''
                pause();pause();window.grabWindow().save(str(artifacts/'file_drop_clipboard.png'))
                click(window,'agentAttachmentRemove_'+str(external_text));wait(lambda:len(chat.attachments)==2)
                assert external_text.exists()
                chat.connectBackend();wait(lambda:chat._protocol is not None and chat.state=='ready')
                captured=[];native_prompt=chat._protocol.prompt
                def record_prompt(text):captured.append(text);native_prompt(text)
                with patch.object(chat._protocol,'prompt',side_effect=record_prompt):
                    click(window,'agentChatSend');wait(lambda:captured and not chat.running)
                assert not chat.attachments and str(external_image).replace('\\','\\\\') in captured[0]
                assert 'private fixture bytes' not in captured[0] and 'previewUrl' not in captured[0]
                user_row=next(row for row in reversed(list(chat._messages.values())) if row['role']=='user')
                assert user_row['text']=='' and len(user_row['attachments'])==2 and Path(cached).exists()
                chat._save()
                from core.agent_chat_store import ChatStore
                reopened=ChatStore(str(root))
                persisted=next(row for row in reopened.data['chats'] if row['id']==chat.selectedChat)
                assert next(row for row in persisted['messages'] if row['key']==user_row['key'])['attachments']==user_row['attachments']
                # History image selection can copy its pixels back to clipboard.
                wait(lambda:conversation.property('atYEnd'))
                pause();window.grabWindow().save(str(artifacts/'sent_attachments.png'))
                click(window,'agentAttachment_'+cached);QTest.keyClick(window,Qt.Key_C,Qt.ControlModifier)
                assert clipboard.mimeData().hasImage() and clipboard.image().pixelColor(0,0)==image.pixelColor(0,0)
                click(window,'agentChatComposer');QTest.keyClick(window,Qt.Key_V,Qt.ControlModifier)
                wait(lambda:len(chat.attachments)==1);assert chat.attachments[0]['path']!=cached
                chat.clearAttachments();file_clipboard=QMimeData();file_clipboard.setUrls([QUrl.fromLocalFile(str(external_text))]);clipboard.setMimeData(file_clipboard)
                QTest.keyClick(window,Qt.Key_V,Qt.ControlModifier);wait(lambda:len(chat.attachments)==1)
                assert chat.attachments[0]['path']==str(external_text) and composer.property('text')==''
                chat.clearAttachments();clipboard.setText('Plain clipboard fixture');QTest.keyClick(window,Qt.Key_V,Qt.ControlModifier)
                wait(lambda:composer.property('text')=='Plain clipboard fixture');assert not chat.attachments
                chat._messages=type(chat._messages)((key,value) for key,value in chat._messages.items() if key.startswith('demo:8:'))
                chat._sync_rows();composer.setProperty('text','Jelaskan perbedaan properti sistem dan vendor')
                wait(lambda:conversation.property('atYEnd'))
                pause();window.grabWindow().save(str(artifacts/'reference_chat.png'))
                window.setWidth(758)
                panel=visual(window,'workspaceAgentChatPanel')
                wait(lambda:panel.mapToScene(panel.boundingRect().bottomRight()).x()<=window.width())
                pause();window.grabWindow().save(str(artifacts/'narrow.png'))
                composer.setProperty('text','Hang until stopped');click(window,'agentChatSend');wait(lambda:chat.state=='working')
                transport=chat._transport;click(window,'agentChatSend');wait(lambda:chat.state=='interrupted',seconds=5)
                assert transport._process.poll() is not None
                # Same two-window Codex layout as the reported screenshot.
                # Display-only fixture: no real account values or model turn.
                from core.agent_metadata import codex_limits
                prefs.refreshPreferences('dark','en',{'animations':False,'effects':'low'})
                chat._provider='codex';chat._chat()['model']='fixture-luna'
                chat._models=[{'id':'fixture-'+name.lower(),'label':label,'efforts':[{'id':'low','label':'low'},{'id':'high','label':'high'}]}
                    for name,label in [('sol61','GPT-6.1-Sol'),('astra','GPT-6-Astra'),('terra','GPT-6.1-Terra'),('sol6','GPT-6-Sol'),('luna','GPT-6-Luna'),('sol56','GPT-5.6-Sol')]]
                chat._chat()['efforts']={'fixture-luna':'low'}
                chat._limits=codex_limits({'rateLimits':{'limitId':'codex','primary':{'windowDurationMins':300,'usedPercent':0,'resetsAt':1910000000},'secondary':{'windowDurationMins':10080,'usedPercent':68,'resetsAt':1910400000}}})
                chat.changed.emit()
                def quota_labels(popup):
                    pending=[popup.property('contentItem')];labels=[]
                    while pending:
                        item=pending.pop(0)
                        if item.objectName()=='agentQuotaWindow' and item.isVisible():labels.append(item.property('text'))
                        pending.extend(item.childItems())
                    return labels
                with patch.object(chat,'refreshUsage') as refresh_usage,patch.object(chat,'refreshEfforts'):
                    click(window,'agentChatModel');popup=visual(window,'agentModelPopup');pause()
                    assert quota_labels(popup)==[],'Quota details must start collapsed'
                    assert popup.property('height')<=340,'Default model menu must stay compact'
                    refresh_usage.assert_not_called()
                    shot=window.grabWindow()
                    shot.copy(*(round(popup.property(k)) for k in ('x','y','width','height'))).save(str(artifacts/'codex_menu_compact.png'))
                    click(window,'agentChatToggleLimits');pause()
                    self_check=refresh_usage.call_count;assert self_check==1
                    windows=quota_labels(popup)
                    assert windows==['5 hours','Weekly'],windows
                    assert not visual(window,'agentChatViewUsage').isVisible(),'Do not offer an empty usage report'
                    shot=window.grabWindow();shot.save(str(artifacts/'codex_menu_fixture.png'))
                    shot.copy(*(round(popup.property(k)) for k in ('x','y','width','height'))).save(str(artifacts/'codex_menu_detail.png'))
                    prefs.refreshPreferences('dark','id',{});pause()
                    windows=quota_labels(popup)
                    assert windows==['5 jam','Mingguan'],windows
                    window.setHeight(500);wait(lambda:popup.property('y')+popup.property('height')<=window.height())
                    window.grabWindow().save(str(artifacts/'codex_menu_small.png'))
                    click(window,'agentChatToggleLimits');pause();assert quota_labels(popup)==[]
                    QTest.keyClick(window,Qt.Key_Escape);click(window,'agentChatModel');pause()
                    assert quota_labels(popup)==[] and refresh_usage.call_count==self_check
                assert not diagnostics,diagnostics
                report={'result':'passed','realHiddenSidecar':True,'nativeQtQuickChat':True,'permissionRoundTrip':True,'cooperativeStop':True,
                        'frozenUnsavedContext':True,'noTerminalCreated':True,'privateClaimsNotPublished':True,'availabilityGatesToolbarAndMonaco':True,
                        'agyAlwaysUnrestrictedWithoutDropdown':True,'saveYesNoBottomRight':True,'nativeSaveApprovalRoundTrip':True,
                        'workingAndFinalElapsed':True,'workingIsPlainNonClickableStatus':True,'individualTimelineEntriesCollapsed':True,
                        'ranCommandOutputLog':True,'thinkingClickRequiresNativeEvent':True,'liveReadableThinkingSummary':True,'legacyNoReplyExplained':True,
                        'automaticProviderConnection':True,'alwaysVisibleCompactModelPicker':True,'svgSendButton':True,
                        'nativeEffortSelection':True,'nativeQuotaInModelPopup':True,'attachmentsMenu':True,'agyStopTerminatesOwnedProcess':True,
                        'nativeFileDropReferencesOriginalPaths':True,'dropNeverMovesFiles':True,'clipboardImageRoundTrip':True,
                        'attachmentOnlySend':True,'individualAttachmentRemoval':True,'plainTextPasteUnchanged':True,
                        'attachmentHistorySurvivesReload':True,'clipboardFileReferencesOriginalPath':True,
                        'humanQuotaWindowsEnglishAndIndonesian':True,'noEmptyUsageAction':True,'modelPopupFitsSmallWindow':True,
                        'noDuplicateReportWithQuotaBars':True,'usageOnlyBackendRetainsReport':True,
                        'accountLimitsCollapsedUntilClicked':True,'compactDefaultModelMenu':True,
                        'backendCwdIsOpenedTree':True,'agyNativeWorkspaceExplicitlyScoped':True,'fixtureCreatesOnlyInsideOpenedTree':True,
                        'nativeInstantReadProducesVisibleHighlight':True,'readHighlightDoesNotNavigate':True,
                        'wheelScrollPreservesReadingPosition':True,'jumpToLatestResumesFollowing':True,'qmlDiagnostics':diagnostics}
                (artifacts/'report.json').write_text(json.dumps(report,indent=2));print(json.dumps(report),flush=True)
            except Exception:
                print('DIAGNOSTICS',diagnostics,'STATE',chat.state,'ERROR',chat.error,flush=True);window.grabWindow().save(str(artifacts/'failure.png'));raise
            finally:
                clipboard.setMimeData(original_clipboard)
                transport=chat._transport
                workspace.dispose()
                if transport and transport._process:
                    wait(lambda:transport._process.poll() is not None)
                pause()


if __name__=='__main__':main()
