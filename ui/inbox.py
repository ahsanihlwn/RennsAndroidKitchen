# SPDX-License-Identifier: MPL-2.0
"""User-facing composer, context attachments and the shared agent inbox model."""
from copy import deepcopy
import hashlib
import os
from pathlib import Path
import time

from PySide6.QtCore import QObject,Property,Signal,Slot,Qt
from core.agent_inbox import AgentInbox
from ui.rows import RowsModel


def inbox_path(project):
    from core.app_paths import USER_STATE_DIR
    key=hashlib.sha256(os.path.normcase(os.path.realpath(project)).encode('utf-8')).hexdigest()
    return Path(USER_STATE_DIR)/'agent_inbox'/(key+'.json')


class WorkspaceInbox(QObject):
    changed=Signal()
    storeChanged=Signal()
    def __init__(self,agent):
        super().__init__(agent)
        self.agent=agent;self.owner=agent.owner;self._rows=RowsModel(parent=self)
        self._draft='';self._context=None;self._selected='';self._detail={};self._error='';self._load_error=''
        self._showing=False
        self.storeChanged.connect(self._sync,Qt.QueuedConnection)
        options={'notify':self.storeChanged.emit,'publish':agent.events.publish}
        try:self.store=AgentInbox(inbox_path(self.owner.project),**options)
        except (OSError,ValueError) as error:
            self._load_error='Inbox history could not be loaded: '+str(error)+'. New requests stay in memory for this session.'
            self.store=AgentInbox(**options)
            self.owner.ready.connect(lambda:self._report_error(self._load_error))
        self._seen_results={row['id'] for row in self.store.snapshot()['messages'] if row['state'] in ('completed','failed')}
        self._sync()

    def _report_error(self,text):
        from core.error_reporting import report_fatal
        report_fatal('Agent inbox\n\n'+text,owner=self.owner,application_error=False)

    @Property(QObject,constant=True)
    def messages(self):return self._rows
    @Property(str,notify=changed)
    def draft(self):return self._draft
    @Property(str,notify=changed)
    def error(self):return self._error or self._load_error
    @Property('QVariantMap',notify=changed)
    def detail(self):return self._detail
    @Property('QVariantList',notify=changed)
    def attachments(self):return self._attachments(self._context or {})
    @Property(str,notify=changed)
    def availability(self):
        snapshot=self.store.snapshot()
        if snapshot['waiting_agents']:return ', '.join(snapshot['waiting_agents'])+' is waiting for a request'
        busy=sorted({row['agent'] for row in snapshot['messages'] if row['state'] in ('claimed','running')})
        if busy:return ', '.join(busy)+' is working'
        return 'Waiting for an agent to read the inbox'
    @Property(int,notify=changed)
    def pendingCount(self):return sum(row['state'] in ('queued','claimed','running') for row in self.store.snapshot()['messages'])
    @Property(int,notify=changed)
    def notificationCount(self):return sum(row['state'] in ('completed','failed') and row['id'] not in self._seen_results for row in self.store.snapshot()['messages'])

    @Slot(bool)
    def setShowing(self,showing):self._showing=showing;self._sync()

    @staticmethod
    def _attachments(context):
        docs={row['path']:row for row in context.get('documents',[])}
        rows=[]
        for path in context.get('paths',[]):
            doc=docs.get(path,{})
            selection=doc.get('selection',{})
            caption=path.replace('\\','/').rsplit('/',1)[-1]
            if selection.get('text'):caption+=' · lines '+str(selection['startLineNumber'])+'–'+str(selection['endLineNumber'])
            if doc.get('unsaved'):caption+=' · unsaved'
            rows.append({'path':path,'label':caption})
        return rows

    def capture(self,origin='',paths=()):
        snapshot=self.agent.context(content=False)
        origin=origin or getattr(self.owner,'_context_focus_area','tree0')
        if not paths:
            paths=[snapshot['active_tab']] if origin=='editor' and snapshot['active_tab'] else snapshot['selection']
        paths=list(dict.fromkeys(str(path) for path in paths if path))[:20]
        documents=[];remaining=32768
        for doc in snapshot['documents']:
            if doc['path'] not in paths:continue
            doc=deepcopy(doc)
            tab=self.owner.editor.bridge._tabs.get(doc['document_path'],{})
            doc['content']=tab.get('content','')[:remaining];doc['truncated']=len(tab.get('content',''))>remaining
            remaining-=len(doc['content'])
            if doc.get('selection'):doc['selection']['text']=doc['selection'].get('text','')[:2048]
            # Send logical references; the agent must use the branch-aware API.
            doc.pop('document_path',None)
            documents.append(doc)
        return {'session_id':snapshot['session_id'],'project':snapshot['project'],'origin':origin,'captured':time.time(),
                'active_tree':snapshot['active_tree'],'paths':paths,'documents':documents,'branches':snapshot['branches'],
                'device':snapshot['device'],'workspace':snapshot.get('workspace',{})}

    def show(self,origin='',paths=()):
        if origin or paths or self._context is None or not self._draft.strip():self._context=self.capture(origin,paths)
        self._error='';self.changed.emit();self.owner.presentationRequested.emit('agentInbox',{})

    @Slot(str)
    def setDraft(self,text):
        if text!=self._draft:self._draft=text;self.changed.emit()

    @Slot()
    def refreshAttachments(self):
        self._context=self.capture();self.changed.emit()

    @Slot()
    def removeAttachments(self):
        context=deepcopy(self._context or self.capture());context['paths']=[];context['documents']=[]
        self._context=context;self.changed.emit()

    @Slot()
    def copyInstructions(self):
        self.owner.copyText(
            'Serve the user requests in this Kitchen inbox while I work in its UI. '
            'First read rak capabilities --json and rak context --json. '
            'Run rak inbox next --agent kitchen-agent --wait 30 to receive one request atomically; '
            'if no message is available, wait again until I ask you to stop. '
            'Keep the returned message id and private claim token. Read current Kitchen context before acting: '
            'the attached selection/buffer is a frozen user snapshot. '
            'Use rak inbox start <id> --agent kitchen-agent --claim <token>, and report progress with '
            'rak inbox progress <id> --agent kitchen-agent --claim <token> --from status.json '
            '(JSON: {"message":"what you are doing"}). '
            'Use the existing rak commands to do the work; use rak ui ask for questions and ui edit/review for revision-checked edits. '
            'Read rak ui settings: successful edits autosave when enabled. UI navigation and controls require agent.ui_triggers. '
            'File edits do not require UI access. Write replacement UTF-8 text to the workspace scratch directory, then use rak ui edit <path> --revision <revision> --content-file <replacement.txt> --wait 30. '
            'Do not inline file contents in shell commands; --from expects a JSON file path. Claude Write requires file_path and content. '
            'Use rak ui controls, ui trigger and ui scroll for registered project controls when enabled. '
            'Never change project Settings or their JSON; ask the user and use rak ui guide agent.ui_triggers to point to the switch. '
            'Check rak inbox get <id> before further operations; if cancel_requested is true, stop further work and use '
            'rak inbox ack-cancel <id> --agent kitchen-agent --claim <token> after stopping safely. '
            'Return actual results through rak inbox reply or fail <id> --agent kitchen-agent --claim <token> '
            '--from result.json (JSON: {"answer":"full result","paths":[]}). '
            'Do not mark a failed or unfinished task completed, do not force-kill a device operation, '
            'and never retry a failed flash automatically. Then wait for the next inbox request.')

    @Slot(result=bool)
    def send(self):
        try:
            row=self.store.send(self._draft,self._context if self._context is not None else self.capture())
        except (ValueError,OSError) as error:
            self._error=str(error);self.changed.emit()
            if isinstance(error,OSError):self._report_error(str(error))
            return False
        self._selected=row['id'];self._draft='';self._context=None;self._error='';self._sync();return True

    @Slot(str)
    def select(self,key):
        self._selected=key
        if self.store.get(key)['state'] in ('completed','failed'):self._seen_results.add(key)
        self._sync()

    @Slot(str)
    def cancel(self,key):
        try:self.store.cancel(key);self._error=''
        except (ValueError,OSError) as error:
            self._error=str(error)
            if isinstance(error,OSError):self._report_error(str(error))
        self._sync()

    @Slot(str)
    def openPath(self,path):
        try:
            path=self.agent._path(path,self.owner.project)
            self.owner.openPath(path)
        except (ValueError,OSError) as error:self._error=str(error);self.changed.emit()

    @Slot()
    def _sync(self):
        labels={'queued':'Waiting','claimed':'Received','running':'Working','completed':'Completed',
                'failed':'Failed','cancelled':'Cancelled','interrupted':'Interrupted'}
        rows=[]
        for row in self.store.snapshot()['messages']:
            row.update(key=row['id'],label=row['message'].splitlines()[0][:160],
                       state_label='Stop requested' if row['cancel_requested'] and row['state'] in ('claimed','running') else labels[row['state']])
            rows.append(row)
        self._rows.replace(rows,animate=False)
        if self._selected:
            try:
                self._detail=self.store.get(self._selected)
                self._detail['attachments']=self._attachments(self._detail['context'])
                self._detail['state_label']=next(row['state_label'] for row in rows if row['id']==self._selected)
                if self._showing and self._detail['state'] in ('completed','failed'):self._seen_results.add(self._selected)
            except (ValueError,StopIteration):self._selected='';self._detail={}
        self.changed.emit()

    def close(self):
        try:self.store.close()
        except OSError as error:self._report_error(str(error))
