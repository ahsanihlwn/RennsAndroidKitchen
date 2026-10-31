# SPDX-License-Identifier: MPL-2.0
import json
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from PySide6.QtCore import QCoreApplication,QObject,Signal
from core.agent_events import AgentEvents
from core.agent_cli import parse
from ui.agent import WorkspaceAgent
from ui.editor import EditorSession
from ui.workspace_tasks import WorkspaceTasks


class EventsTests(unittest.TestCase):
    def test_long_wait_wakes_on_matching_event_without_polling(self):
        events=AgentEvents();result=[]
        thread=threading.Thread(target=lambda:result.append(events.read(types=('document',),wait=2)))
        thread.start();events.publish('task.finished',{'ok':True});events.publish('document.saved',{'path':'x'})
        thread.join(1);self.assertFalse(thread.is_alive())
        self.assertEqual([row['type'] for row in result[0]['events']],['document.saved'])
    def test_history_gap_pagination_and_restart_cursor(self):
        events=AgentEvents(capacity=3)
        for i in range(5):events.publish('task.finished',{'i':i})
        page=events.read(0,limit=2);self.assertTrue(page['gap'])
        self.assertEqual([row['id'] for row in page['events']],[3,4])
        self.assertEqual(events.read(page['cursor'])['events'][0]['id'],5)
        self.assertTrue(events.read(999)['gap'])
    def test_close_releases_waiters_and_does_not_mutate_published_data(self):
        events=AgentEvents();data={'paths':['original']};events.publish('selection.changed',data)
        data['paths'].clear();self.assertEqual(events.read()['events'][0]['data']['paths'],['original'])
        result=[];thread=threading.Thread(target=lambda:result.append(events.read(1,wait=3)))
        thread.start();events.close();thread.join(1)
        self.assertTrue(result[0]['closed'])


class Directory(QObject):
    ready=Signal();metadataChanged=Signal();directoryLoaded=Signal(str)
    def __init__(self,path):
        super().__init__();self.base=path;self.location=path;self.engine=SimpleNamespace(root_map_ctx={})
    def revealPath(self,path):self.revealed=path


class Owner(QObject):
    selectionChanged=Signal();tabsChanged=Signal();ready=Signal();presentationRequested=Signal(str,'QVariantMap')
    focusChanged=Signal(str)
    def __init__(self,root):
        super().__init__();self.project=str(root);self._ready=True;self._selection=[];self.activeTreeId=0;self.activeTab='';self._focus_area='editor'
        self._tab_data=[];self.editor=EditorSession(self);self.directories=tuple(Directory(str(root/name)) for name in ('PORT','STOCK'))
        self._tasks=WorkspaceTasks(self,runner=lambda *_args,**_kwargs:None)
        class Android(QObject):
            changed=Signal()
            mode='';badge='no device';running=False;refreshing=False
        class Mounts(QObject):
            changed=Signal()
            _image_base={}
            def reference(self,*_args,**_kwargs):return None
        self._android_tools=Android(self);self._mounts=Mounts(self)
    def openAs(self,path,kind):
        self.editor.bridge.open_file(path)
        self._tab_data.append({'path':path,'documentPath':path,'kind':'editor','title':Path(path).name})
        self.activeTab=path;self.tabsChanged.emit()
    def selectTree(self,index):self.activeTreeId=index;self.selectionChanged.emit()


class AgentApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QCoreApplication.instance() or QCoreApplication([])
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        for name in ('PORT','STOCK'):(self.root/name).mkdir()
        self.path=str(self.root/'PORT'/'test.txt');Path(self.path).write_text('saved\n')
        state=patch('ui.inbox.inbox_path',return_value=self.root/'agent_inbox.json');state.start();self.addCleanup(state.stop)
        self.owner=Owner(self.root);self.api=WorkspaceAgent(self.owner)
        self.addCleanup(self.owner._tasks.close);self.addCleanup(self.api.close)
        icon=patch.object(self.owner.editor.bridge,'_resolve_icon_b64',return_value='fixture');icon.start();self.addCleanup(icon.stop)
        self.owner.openAs(self.path,'editor');self.owner.editor.bridge.on_editor_ready()
        self.presentations=[];self.owner.presentationRequested.connect(lambda kind,data:self.presentations.append((kind,data)))
    def command(self,action,payload=None,target=None):
        return self.api.handle({'command':'ui','action':action,'payload':payload,'target':target},str(self.root))
    def test_context_reads_unsaved_buffer_selection_and_revision(self):
        bridge=self.owner.editor.bridge;before=bridge._tabs[self.path]['revision']
        bridge.on_content_changed(self.path,'unsaved\n')
        bridge.on_selection_changed(self.path,json.dumps({'startLineNumber':1,'startColumn':1,'endLineNumber':1,'endColumn':4,'text':'uns'}))
        doc=self.api.context(True)['documents'][0]
        self.assertTrue(doc['unsaved']);self.assertEqual(doc['content'],'unsaved\n')
        self.assertNotEqual(doc['revision'],before);self.assertEqual(doc['selection']['text'],'uns')
        self.assertEqual(Path(self.path).read_text(),'saved\n')
    def test_two_questions_keep_separate_ids_and_explicit_answers(self):
        first=self.command('ask',{'message':'First?','choices':['A','B']})
        second=self.command('ask',{'message':'Second?'})
        self.assertNotEqual(first['id'],second['id']);self.assertEqual(len(self.presentations),1)
        result=[];thread=threading.Thread(target=lambda:result.append(self.api.response(first['id'],2)));thread.start()
        self.api.answer(first['id'],True,{'answer':'B'});thread.join(1)
        self.assertEqual(result[0]['response']['answer'],'B')
        self.assertEqual(self.presentations[-1][1]['id'],second['id'])
        self.api.answer(second['id'],False,{})
        self.assertEqual(self.api.response(second['id'])['state'],'cancelled')
    def test_review_rechecks_document_version_before_any_edit(self):
        bridge=self.owner.editor.bridge;edits=[];bridge.agentEditRequested.connect(lambda *args:edits.append(args))
        review=self.command('review',{'path':self.path,'revision':bridge._tabs[self.path]['revision'],'content':'proposed\n'})
        self.assertIn('-saved',self.presentations[-1][1]['diff'])
        bridge.on_content_changed(self.path,'user typing\n')
        self.api.answer(review['id'],True,{})
        self.assertEqual(self.api.response(review['id'])['state'],'conflict');self.assertEqual(edits,[])
        self.assertEqual(bridge._tabs[self.path]['content'],'user typing\n')
    def test_edit_waits_for_monaco_ack_and_does_not_write_disk(self):
        self.owner.projectSettings={'agent':{'autosave':False}}
        bridge=self.owner.editor.bridge;edits=[];bridge.agentEditRequested.connect(lambda *args:edits.append(args))
        edit=self.command('edit',{'path':self.path,'revision':bridge._tabs[self.path]['revision'],'content':'agent\n'})
        self.assertEqual(edit['state'],'applying');self.assertEqual(bridge._tabs[self.path]['content'],'saved\n')
        key,path,before,after=edits[0];self.assertEqual(before,'saved\n')
        bridge.on_content_changed(path,after);bridge.on_agent_edit_finished(key,path,True,'')
        self.assertEqual(self.api.response(key)['state'],'applied');self.assertEqual(Path(path).read_text(),'saved\n')
        with self.assertRaises(ValueError):self.command('edit',{'path':self.path,'revision':'stale','content':'bad'})
    def test_closed_session_releases_pending_question(self):
        question=self.command('ask',{'message':'Wait?'})
        result=[];thread=threading.Thread(target=lambda:result.append(self.api.response(question['id'],2)));thread.start()
        self.api.close();thread.join(1)
        self.assertEqual(result[0]['state'],'cancelled')
    def test_outside_project_and_internal_store_are_rejected(self):
        for path in (str(self.root/'outside.txt'),str(self.root/'PORT'/'neobranch'/'secret.txt')):
            with self.assertRaises(ValueError):self.command('read',target=path)
    def test_parser_keeps_windows_paths_and_rejects_invalid_wait(self):
        options=parse('ui','open "PORT/a file.txt" --line 5',str(self.root))
        self.assertEqual(options['target'],'PORT/a file.txt');self.assertEqual(options['line'],5)
        with self.assertRaises(ValueError):parse('events','--wait 31',str(self.root))
        with self.assertRaises(ValueError):parse('ui','edit',str(self.root))

    def test_inbox_composer_preserves_the_original_selection_and_draft(self):
        bridge=self.owner.editor.bridge
        bridge.on_content_changed(self.path,'original selected draft\n')
        bridge.on_selection_changed(self.path,json.dumps({'startLineNumber':1,'startColumn':1,'endLineNumber':1,'endColumn':9,'text':'original'}))
        inbox=self.api.inbox;inbox.show('editor');inbox.setDraft('Check the selection')
        bridge.on_content_changed(self.path,'new typing after composer opened\n')
        self.owner._selection=['other'];inbox.show()
        self.assertEqual(inbox.draft,'Check the selection');self.assertTrue(inbox.send())
        message=inbox.store.next('a')['message']
        self.assertEqual(message['context']['paths'],[self.path])
        self.assertEqual(message['context']['documents'][0]['content'],'original selected draft\n')
        self.assertEqual(message['context']['documents'][0]['selection']['text'],'original')
        self.assertEqual(Path(self.path).read_text(),'saved\n')

    def test_viewing_pending_work_does_not_mark_a_future_reply_read(self):
        inbox=self.api.inbox;row=inbox.store.send('work',{})
        inbox.select(row['id']);claim=inbox.store.next('a')
        inbox.store.update(row['id'],'a',claim['claim'],'reply',{'answer':'Finished'})
        self.assertEqual(inbox.notificationCount,1)
        inbox.select(row['id']);self.assertEqual(inbox.notificationCount,0)


if __name__=='__main__':unittest.main()
