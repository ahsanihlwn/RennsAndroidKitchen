# SPDX-License-Identifier: MPL-2.0
from pathlib import Path
import time
import threading
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace
from core.agent_documents import read,apply
from core.workspace_files import WorkspaceFiles
from tests import test_agent_api as fixtures
from PySide6.QtCore import QCoreApplication


class BackgroundDocumentTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.path=self.root/'system/build.prop';self.path.parent.mkdir()
        self.path.write_bytes(b'\xef\xbb\xbfvalue=old\r\n');self.files=WorkspaceFiles(str(self.root))
    def test_background_edit_keeps_bom_eol_and_rejects_stale_revision(self):
        before=read(self.files,str(self.path))[0]
        after=apply(self.files,str(self.path),before['revision'],'value=new\n')
        self.assertEqual(self.path.read_bytes(),b'\xef\xbb\xbfvalue=new\r\n')
        self.assertNotEqual(after['revision'],before['revision'])
        with self.assertRaisesRegex(ValueError,'revision conflict'):apply(self.files,str(self.path),before['revision'],'bad')
        self.assertEqual(self.path.read_bytes(),b'\xef\xbb\xbfvalue=new\r\n')
    def test_failed_replace_keeps_source_and_removes_only_its_staging_file(self):
        before=read(self.files,str(self.path))[0]
        with patch('core.agent_documents.os.replace',side_effect=OSError('disk unavailable')):
            with self.assertRaises(OSError):apply(self.files,str(self.path),before['revision'],'value=new\n')
        self.assertEqual(self.path.read_bytes(),b'\xef\xbb\xbfvalue=old\r\n')
        self.assertFalse(list(self.path.parent.glob('.rak-agent-*')))
    def test_branch_edit_uses_overlay_and_does_not_modify_source(self):
        from main_ext.class_NeoBranchManager import NeoBranchManager
        config=self.root/'config';config.mkdir()
        (config/'system_file_contexts').write_text('/system(/.*)? u:object_r:system_file:s0\n')
        (config/'system_fs_config').write_text('system 0 0 0755\n')
        manager=NeoBranchManager(str(self.root));manager.create_branch('system','work','blue');manager.set_active_branch('system','work')
        files=WorkspaceFiles(str(self.root));before=read(files,str(self.path))[0]
        after=apply(files,str(self.path),before['revision'],'branch=only\n')
        self.assertEqual(after['content'],'branch=only\n')
        self.assertEqual(self.path.read_bytes(),b'\xef\xbb\xbfvalue=old\r\n')
    def test_switching_untouched_branches_invalidates_background_revision(self):
        from main_ext.class_NeoBranchManager import NeoBranchManager
        manager=NeoBranchManager(str(self.root))
        manager.create_branch('system','first','blue');manager.create_branch('system','second','green')
        manager.set_active_branch('system','first')
        before=read(WorkspaceFiles(str(self.root)),str(self.path))[0]
        self.assertEqual(before['branch']['branch_name'],'first')
        manager.set_active_branch('system','second')
        current=read(WorkspaceFiles(str(self.root)),str(self.path))[0]
        self.assertNotEqual(before['revision'],current['revision'])
        with self.assertRaisesRegex(ValueError,'revision conflict'):
            apply(WorkspaceFiles(str(self.root)),str(self.path),before['revision'],'wrong branch\n')
        self.assertEqual(self.path.read_bytes(),b'\xef\xbb\xbfvalue=old\r\n')
        self.assertFalse(Path(manager._branch_fs_dir('system','second'),'build.prop').exists())


class AgentPolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QCoreApplication.instance() or QCoreApplication([])
    setUp=fixtures.AgentApiTests.setUp
    command=fixtures.AgentApiTests.command
    def acknowledge(self,text='agent\n'):
        bridge=self.owner.editor.bridge;edits=[];bridge.agentEditRequested.connect(lambda *args:edits.append(args))
        request=self.command('edit',{'path':self.path,'revision':bridge._tabs[self.path]['revision'],'content':text})
        key,path,before,after=edits[-1];return request,(key,path,after)
    def test_autosave_waits_for_editor_ack_and_saves_exact_result(self):
        request,(key,path,after)=self.acknowledge()
        self.assertEqual(Path(path).read_text(),'saved\n')
        bridge=self.owner.editor.bridge;bridge.on_content_changed(path,after);bridge.on_agent_edit_finished(key,path,True,'')
        result=self.api.response(key);self.assertEqual(result['state'],'applied');self.assertTrue(result['response']['saved'])
        self.assertEqual(Path(path).read_text(),after)
    def test_typing_after_edit_ack_is_never_implicitly_saved(self):
        request,(key,path,after)=self.acknowledge();bridge=self.owner.editor.bridge
        bridge.on_content_changed(path,'new user typing');bridge.on_agent_edit_finished(key,path,True,'')
        self.assertEqual(self.api.response(key)['state'],'conflict');self.assertEqual(Path(path).read_text(),'saved\n')
    def test_autosave_failure_stays_failed_and_preserves_dirty_buffer(self):
        request,(key,path,after)=self.acknowledge();bridge=self.owner.editor.bridge;bridge.on_content_changed(path,after)
        with patch.object(bridge,'save_file',side_effect=lambda path,_:bridge.documentSaveFailed.emit(path,'disk unavailable')):
            bridge.on_agent_edit_finished(key,path,True,'')
        self.assertEqual(self.api.response(key)['state'],'failed');self.assertTrue(bridge.is_unsaved(path))
        self.assertEqual(Path(path).read_text(),'saved\n')
    def test_a_second_edit_cannot_replace_the_save_acknowledgment(self):
        request,(key,path,after)=self.acknowledge();bridge=self.owner.editor.bridge;bridge.on_content_changed(path,after)
        with patch.object(bridge,'save_file'):
            bridge.on_agent_edit_finished(key,path,True,'')
        self.assertEqual(self.api.response(key)['state'],'applying')
        with self.assertRaisesRegex(ValueError,'still applying or saving'):
            self.command('edit',{'path':path,'revision':bridge._tabs[path]['revision'],'content':'second edit'})
        bridge.documentSaveFailed.emit(path,'save unavailable')
        self.assertEqual(self.api.response(key)['state'],'failed')
    def test_ui_default_blocks_navigation_but_settings_guidance_does_not_change_values(self):
        self.assertFalse(self.command('settings')['agent']['ui_triggers'])
        self.assertFalse(self.api.capabilities()['editing']['requires_ui_triggers'])
        self.assertFalse(self.api.capabilities()['ui_controls']['enabled'])
        for action in ('open','reveal'):
            with self.assertRaisesRegex(ValueError,'UI access is disabled'):self.command(action,target=self.path)
        result=self.command('guide',target='agent.ui_triggers')
        self.assertTrue(result['accepted']);self.assertFalse(result['changed'])
        self.assertEqual(self.presentations[-1],('agentSettingGuide',{'target':'agent.ui_triggers'}))
        self.owner.projectSettings={'agent':{'ui_triggers':True}}
        self.assertTrue(self.api.capabilities()['ui_controls']['enabled'])
        self.command('open',target=self.path)
    def test_background_read_and_highlight_never_open_a_tab(self):
        other=Path(self.path).parent/'folder/other.txt';other.parent.mkdir();other.write_text('background')
        before=list(self.owner._tab_data);result=self.command('read',target=str(other))
        self.assertEqual(result['content'],'background');self.assertTrue(result['background'])
        self.assertEqual(self.owner._tab_data,before)
        for path in (other,other.parent):self.assertIn(self.api.activity.key(str(path)),self.api.activity.paths)
        self.owner.projectSettings={'agent':{'highlight':False}};self.api.activity._expire()
        self.assertEqual(self.api.activity.paths,{})
    def test_activity_rejects_multiline_command_output_without_resolving_files(self):
        activity=self.api.activity;cursor=self.api.events.read()['latest']
        with patch.object(self.api,'_path',side_effect=AssertionError('Visual activity must not resolve disk paths')):
            activity.touch(self.path+'\n'+self.path,'write')
            self.assertFalse(activity.paths)
            activity.touch(self.path,'write')
        rows=self.api.events.read(cursor,types=['agent.file'])['events']
        self.assertEqual([row['data']['path'] for row in rows],[self.path])
        self.assertIn(activity.key(self.path),activity.paths)
    def test_touching_tree_root_stops_at_that_root(self):
        activity=self.api.activity;root=str(Path(self.path).parent)
        with patch.object(self.api,'_path',side_effect=AssertionError('No filesystem resolution')):
            activity.touch(root,'read')
        self.assertEqual(set(activity.paths),{activity.key(root)})
    def test_large_activity_list_yields_and_keeps_individual_bounded_paths(self):
        activity=self.api.activity;count=1000
        paths=[str(Path(self.path).parent/'bulk'/f'{index}.txt') for index in range(count)]
        cursor=self.api.events.read()['latest'];signals=[];activity.changed.connect(lambda:signals.append(True))
        with patch.object(self.api,'_path',side_effect=AssertionError('No filesystem resolution on the GUI thread')):
            activity.touch_many(paths,'write')
            self.assertEqual(self.api.events.read()['latest'],cursor)
            activity._drain_touches()
            self.assertLessEqual(self.api.events.read()['latest']-cursor,32)
            self.assertTrue(activity._pending_touches)
            deadline=time.monotonic()+5
            while activity._pending_touches:
                self.assertLess(time.monotonic(),deadline)
                self.app.processEvents()
        self.assertEqual(self.api.events.read()['latest']-cursor,count)
        rows=self.api.events.read(cursor,types=['agent.file'],limit=512)['events']
        self.assertEqual(rows[-1]['data']['path'],paths[-1])
        self.assertLessEqual(len(activity.paths),256);self.assertLessEqual(len(activity._recent),256)
        self.assertIn(activity.key(paths[-1]),activity.paths)
        self.assertLess(len(signals),count//2)
    def test_closing_activity_cancels_pending_visual_updates(self):
        activity=self.api.activity;cursor=self.api.events.read()['latest']
        activity.touch_many([self.path]*1000,'write');activity.close();self.app.processEvents()
        self.assertFalse(activity.paths);self.assertFalse(activity._pending_touches)
        self.assertEqual(self.api.events.read()['latest'],cursor)
    def test_active_tools_hold_their_highlight_until_release_and_share_parent(self):
        activity=self.api.activity;first=Path(self.path).parent/'folder/first.txt';first.parent.mkdir()
        second=first.with_name('second.txt');first.write_text('first');second.write_text('second')
        activity.begin('first',[str(first)],'read');activity.begin('second',[str(second)],'write')
        with patch('ui.agent_activity.time.monotonic',return_value=time.monotonic()+60):activity._expire()
        self.assertIn(activity.key(str(first)),activity.paths)
        with patch('ui.agent_activity.time.monotonic',return_value=time.monotonic()+60):
            activity.end('first')
            self.assertNotIn(activity.key(str(first)),activity.paths)
            self.assertIn(activity.key(str(first.parent)),activity.paths)
            activity.end('second');self.assertFalse(activity.paths)
    def test_native_tool_release_and_turn_cleanup_remove_only_their_paths(self):
        activity=self.api.activity
        activity.tool('chat:turn:read',[self.path],'Read','running')
        self.assertIn(activity.key(self.path),activity.paths)
        activity.tool('chat:turn:read',[],'Read','completed');self.assertFalse(activity._held)
        self.assertEqual(activity.paths[activity.key(self.path)]['state'],'released')
        with patch('ui.agent_activity.time.monotonic',return_value=time.monotonic()+2):activity._expire()
        self.assertFalse(activity.paths)
        activity.tool('chat:turn:read',[self.path],'Read','running')
        activity.end_group('chat:turn:');self.assertFalse(activity._held)
        with patch('ui.agent_activity.time.monotonic',return_value=time.monotonic()+2):activity._expire()
        self.assertFalse(activity.paths)
    def test_settings_file_cannot_be_edited_even_with_ui_permission(self):
        self.owner.projectSettings={'agent':{'ui_triggers':True}}
        self.owner.settings_path=lambda:self.path
        with self.assertRaisesRegex(ValueError,'only be changed by the user'):
            self.command('edit',{'path':self.path,'revision':self.command('read',target=self.path)['revision'],'content':'settings changed'})

    def branch_document(self,opened):
        from main_ext.class_NeoBranchManager import NeoBranchManager
        base=self.root/'PORT';path=base/'system/build.prop';path.parent.mkdir();path.write_bytes(b'\xef\xbb\xbfvalue=source\r\n')
        manager=NeoBranchManager(str(base));manager.create_branch('system','work','blue');manager.set_active_branch('system','work')
        if opened:
            physical=manager.record_edit('system','work',str(path))
            bridge=self.owner.editor.bridge;bridge.open_file(physical)
            bridge.bind_neobranch_tab(physical,str(base),'system',str(path),'work')
            self.owner._tab_data.append({'path':str(path),'documentPath':physical,'kind':'editor','title':'build.prop'})
            self.owner.tabsChanged.emit()
        return path,manager

    def test_open_editor_agent_edit_autosaves_only_to_active_branch(self):
        logical,manager=self.branch_document(True)
        before=self.command('read',target=str(logical));self.assertEqual(before['branch']['branch_name'],'work')
        bridge=self.owner.editor.bridge;edits=[];bridge.agentEditRequested.connect(lambda *args:edits.append(args))
        request=self.command('edit',{'path':str(logical),'revision':before['revision'],'content':'value=branch\n'})
        key,physical,before_text,after=edits[-1]
        bridge.on_content_changed(physical,after);bridge.on_agent_edit_finished(key,physical,True,'')
        response=self.api.response(request['id']);self.assertEqual(response['state'],'applied');self.assertTrue(response['response']['saved'])
        self.assertEqual(Path(physical).read_bytes(),b'\xef\xbb\xbfvalue=branch\r\n')
        self.assertEqual(logical.read_bytes(),b'\xef\xbb\xbfvalue=source\r\n')

    def test_background_agent_edit_queues_and_saves_branch_without_opening_editor(self):
        logical,manager=self.branch_document(False)
        self.owner._closing=False;self.owner._operation_serial=0;self.owner._history=[]
        self.owner.statusChanged=SimpleNamespace(emit=lambda:None)
        for directory in self.owner.directories:directory.reloadMetadata=lambda:None
        self.owner._tasks._runner=None
        tabs=list(self.owner._tab_data)
        before=self.command('read',target=str(logical));self.assertTrue(before['background'])
        request=self.command('edit',{'path':str(logical),'revision':before['revision'],'content':'background=branch\n'})
        deadline=time.monotonic()+5
        while self.api.response(request['id'])['state']=='applying':
            self.assertLess(time.monotonic(),deadline);self.app.processEvents();time.sleep(.002)
        response=self.api.response(request['id']);self.assertEqual(response['state'],'applied',response)
        self.assertTrue(response['response']['saved']);self.assertEqual(self.owner._tab_data,tabs)
        physical,_=WorkspaceFiles(str(self.root/'PORT')).view.resolve(str(logical))
        self.assertEqual(Path(physical).read_bytes(),b'\xef\xbb\xbfbackground=branch\r\n')
        self.assertEqual(logical.read_bytes(),b'\xef\xbb\xbfvalue=source\r\n')

    def test_editor_branch_switch_before_save_preserves_source_and_dirty_buffer(self):
        logical,manager=self.branch_document(True)
        before=self.command('read',target=str(logical));bridge=self.owner.editor.bridge;edits=[]
        bridge.agentEditRequested.connect(lambda *args:edits.append(args))
        request=self.command('edit',{'path':str(logical),'revision':before['revision'],'content':'value=proposed\n'})
        key,physical,_,after=edits[-1]
        manager.set_active_branch('system',None)
        bridge.on_content_changed(physical,after);bridge.on_agent_edit_finished(key,physical,True,'')
        self.assertEqual(self.api.response(request['id'])['state'],'failed')
        self.assertTrue(bridge.is_unsaved(physical))
        self.assertEqual(logical.read_bytes(),b'\xef\xbb\xbfvalue=source\r\n')

    def test_unix_sed_ipc_waits_for_monaco_and_saves_branch_overlay(self):
        from core import rak_cli
        logical,manager=self.branch_document(True)
        self.owner._agent=self.api;rak_cli.attach(self.owner);self.addCleanup(rak_cli.detach,self.owner)
        bridge=self.owner.editor.bridge;edits=[];bridge.agentEditRequested.connect(lambda *args:edits.append(args))
        results=[]
        thread=threading.Thread(target=lambda:results.append(rak_cli._handle_ipc_command('sed -i "s/source/cli/g" "'+str(logical)+'"',str(self.root/'PORT'))))
        thread.start();deadline=time.monotonic()+5
        while not edits:
            self.assertLess(time.monotonic(),deadline);self.app.processEvents();time.sleep(.002)
        self.assertTrue(thread.is_alive());self.assertEqual(logical.read_bytes(),b'\xef\xbb\xbfvalue=source\r\n')
        key,physical,before,after=edits[-1]
        bridge.on_content_changed(physical,after);bridge.on_agent_edit_finished(key,physical,True,'')
        while thread.is_alive():
            self.assertLess(time.monotonic(),deadline);self.app.processEvents();time.sleep(.002)
        thread.join();self.assertTrue(results[0]['ok'],results)
        self.assertEqual(Path(physical).read_bytes(),b'\xef\xbb\xbfvalue=cli\r\n')
        self.assertEqual(logical.read_bytes(),b'\xef\xbb\xbfvalue=source\r\n')

    def test_unix_sed_internal_save_failure_keeps_cli_type_and_traceback(self):
        from core import rak_cli
        logical,manager=self.branch_document(True)
        self.owner._agent=self.api;rak_cli.attach(self.owner);self.addCleanup(rak_cli.detach,self.owner)
        bridge=self.owner.editor.bridge;edits=[];bridge.agentEditRequested.connect(lambda *args:edits.append(args))
        results=[]
        thread=threading.Thread(target=lambda:results.append(rak_cli._handle_ipc_command('sed -i "s/source/failed/g" "'+str(logical)+'"',str(self.root/'PORT'))))
        thread.start();deadline=time.monotonic()+5
        while not edits:
            self.assertLess(time.monotonic(),deadline);self.app.processEvents();time.sleep(.002)
        key,physical,before,after=edits[-1]
        with patch('core.editor_document.encode_text_meta',side_effect=TypeError('SED_SAVE_INTERNAL_BUG')):
            bridge.on_content_changed(physical,after);bridge.on_agent_edit_finished(key,physical,True,'')
        while thread.is_alive():
            self.assertLess(time.monotonic(),deadline);self.app.processEvents();time.sleep(.002)
        thread.join();self.assertFalse(results[0]['ok']);self.assertTrue(results[0]['application_error'],results)
        self.assertIn('TypeError: SED_SAVE_INTERNAL_BUG',results[0]['log'])
        self.assertTrue(bridge.is_unsaved(physical));self.assertEqual(logical.read_bytes(),b'\xef\xbb\xbfvalue=source\r\n')

    def test_unix_sed_uses_full_large_unsaved_model_and_waits_for_monaco(self):
        from core import rak_cli
        self.owner._agent=self.api;rak_cli.attach(self.owner);self.addCleanup(rak_cli.detach,self.owner)
        bridge=self.owner.editor.bridge;original='keep user text\n'*42000+'FINAL_MARKER\n'
        bridge.on_content_changed(self.path,original)
        self.assertTrue(self.command('read',target=self.path)['truncated'])
        edits=[];bridge.agentEditRequested.connect(lambda *args:edits.append(args));results=[]
        argv=['sed','-i','s/FINAL_MARKER/OPEN_BUFFER_VERIFIED/',self.path]
        thread=threading.Thread(target=lambda:results.append(rak_cli._handle_ipc_command(__import__('subprocess').list2cmdline(argv),str(self.root/'PORT'),argv=argv)))
        thread.start();deadline=time.monotonic()+10
        while not edits and thread.is_alive():
            self.assertLess(time.monotonic(),deadline);self.app.processEvents();time.sleep(.002)
        self.assertTrue(edits,results)
        key,physical,before,after=edits[-1]
        self.assertEqual(before,original);self.assertEqual(after,original.replace('FINAL_MARKER','OPEN_BUFFER_VERIFIED'))
        self.assertEqual(Path(self.path).read_text(),'saved\n')
        bridge.on_content_changed(physical,after);bridge.on_agent_edit_finished(key,physical,True,'')
        while thread.is_alive():
            self.assertLess(time.monotonic(),deadline);self.app.processEvents();time.sleep(.002)
        thread.join();self.assertTrue(results[0]['ok'],results)
        self.assertEqual(Path(self.path).read_text(),after)
