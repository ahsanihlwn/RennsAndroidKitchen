# SPDX-License-Identifier: MPL-2.0
"""The live, semantic agent interface to the same workspace used by the user."""
from collections import OrderedDict,deque
from copy import deepcopy
import difflib
import json
import os
import threading
import time
import uuid

from PySide6.QtCore import QObject
from core.agent_events import AgentEvents
from core.agent_policy import settings as agent_settings,require_ui,SETTING_KEYS
from core.command_context import current_command,command_scope


class WorkspaceAgent(QObject):
    def __init__(self,owner):
        super().__init__(owner)
        self.owner=owner;self.events=AgentEvents();self._closed=False
        self._condition=threading.Condition();self._requests=OrderedDict();self._payloads={}
        self._prompts=deque();self._presented=None;self._navigation={};self._seen={}
        self._saving={}
        self._command_contexts={}
        from ui.agent_activity import AgentActivity
        from ui.agent_controls import AgentControls
        self.activity=AgentActivity(self);self.controls=AgentControls(self)
        owner.selectionChanged.connect(self._selection_changed)
        owner.focusChanged.connect(lambda area:self.events.publish('focus.changed',{'area':area}))
        owner.tabsChanged.connect(self._tabs_changed)
        owner._tasks.changed.connect(self._tasks_changed)
        owner._android_tools.changed.connect(self._device_changed)
        owner._mounts.changed.connect(self._mounts_changed)
        owner.ready.connect(lambda:self.events.publish('workspace.ready',{}))
        settings_signal=getattr(owner,'settingsChanged',None)
        if settings_signal is not None:settings_signal.connect(lambda:self.events.publish('settings.changed',self.settings_info()))
        bridge=owner.editor.bridge
        bridge.documentChanged.connect(self._document_changed)
        bridge.selectionChanged.connect(lambda path,data:self.events.publish('editor.selection',{'path':self.logical(path),'selection':data}))
        bridge.documentSaved.connect(lambda path:self.events.publish('document.saved',{'path':self.logical(path),'revision':self._revision(path)}))
        bridge.agentEditFinished.connect(self._edit_finished)
        bridge.documentSaved.connect(self._agent_saved)
        bridge.documentSaveFailed.connect(self._agent_save_failed)
        for directory in owner.directories:
            directory.ready.connect(self._branches_changed)
            directory.metadataChanged.connect(self._branches_changed)
            directory.directoryLoaded.connect(lambda _path,directory=directory:self._emit_changed(
                'location:'+directory.base,'tree.location',{'tree':directory.base,'location':directory.location}))
        self.events.publish('workspace.opened',{'project':owner.project})
        from ui.inbox import WorkspaceInbox
        self.inbox=WorkspaceInbox(self)
        from ui.agent_chat import WorkspaceAgentChat
        self.chat=WorkspaceAgentChat(self)

    def _emit_changed(self,key,kind,data):
        if self._seen.get(key)!=data:
            self._seen[key]=deepcopy(data);self.events.publish(kind,data)

    def logical(self,document):
        return next((row['path'] for row in self.owner._tab_data if row['documentPath']==document),document)

    def _revision(self,document):return self.owner.editor.bridge._tabs.get(document,{}).get('revision','')

    def _selection_changed(self):
        self._emit_changed('selection','selection.changed',{'tree':self.owner.activeTreeId,'paths':list(self.owner._selection)})

    def _tabs_changed(self):
        tabs=[{'path':row['path'],'kind':row['kind'],'unsaved':bool(row.get('unsaved'))} for row in self.owner._tab_data]
        self._emit_changed('tabs','editor.tabs',{'active':self.owner.activeTab,'tabs':tabs})
        open_paths={row['path'] for row in self.owner._tab_data if row['kind']=='editor'}
        for request in self._pending_requests():
            if request['state']=='applying' and self._payloads[request['id']]['path'] not in open_paths:
                self._finish(request['id'],'conflict',{'error':'The document was closed before the edit completed.'})
        for path,position in list(self._navigation.items()):
            row=next((row for row in self.owner._tab_data if row['path']==path and row['kind']=='editor'),None)
            if row:
                self._navigation.pop(path,None)
                self.owner.editor.bridge.request_navigation(row['documentPath'],*position)

    def _document_changed(self,document):
        tab=self.owner.editor.bridge._tabs.get(document,{})
        self.events.publish('document.changed',{'path':self.logical(document),'revision':tab.get('revision',''),
                                                'unsaved':bool(tab.get('unsaved'))})

    def _tasks_changed(self):
        snapshot=self.owner._tasks.activity_snapshot()
        state={key:snapshot[key] for key in ('state','busy','tasks')}
        self._emit_changed('tasks','task.changed',state)
        history={row['id']:row for row in snapshot['history']}
        previous=self._seen.get('history',set())
        for key,row in reversed(list(history.items())):
            if key not in previous:self.events.publish('task.finished',row)
        self._seen['history']=set(history)

    def _device_changed(self):
        android=self.owner._android_tools
        self._emit_changed('device','device.changed',{'mode':android.mode,'badge':android.badge,'refreshing':android.refreshing})

    def _mounts_changed(self):self.events.publish('mounts.changed',{'mounts':self._mounts()})

    def _branches(self):
        from main_ext.class_NeoBranchManager import NeoBranchManager
        result=[]
        for directory in self.owner.directories[:2]:
            if not directory.base:continue
            manager=NeoBranchManager(directory.base)
            roots=set(getattr(directory.engine,'root_map_ctx',{}))
            if os.path.isdir(manager._neobranch_root):
                roots.update(row.name for row in os.scandir(manager._neobranch_root) if row.is_dir())
            result.append({'tree':directory.base,'partitions':[
                {'name':root,'active_branch':(manager.get_active_branch(root) or {}).get('name')}
                for root in sorted(roots)]})
        return result

    def _branches_changed(self):self._emit_changed('branches','branch.changed',{'trees':self._branches()})

    def _mounts(self):
        mounts=self.owner._mounts
        return [{'image':image,'tree':base,'writable':mounts.writable(image)} for image,base in mounts._image_base.items()]

    def _file_key(self,path):
        if path.startswith('mount://'):return path
        try:path=self._files(path).view.canonical(path)
        except ValueError:pass
        return os.path.normcase(os.path.abspath(path))

    def _document_row(self,path):
        direct=next((row for row in self.owner._tab_data if row['path']==path),None)
        if direct:return direct
        key=self._file_key(path)
        return next((row for row in self.owner._tab_data if self._file_key(row['path'])==key),None)

    def _document(self,path=None):
        path=path or self.owner.activeTab
        row=self._document_row(path)
        if row is None:raise ValueError('Open this file in Kitchen first: '+str(path))
        if row['kind']!='editor':raise ValueError('This tab is not a text editor')
        document=row['documentPath'];tab=self.owner.editor.bridge._tabs.get(document)
        if tab is None:raise ValueError('The editor buffer is not ready')
        return row,tab

    def _document_info(self,row,tab,*,content=False):
        value={'path':row['path'],'document_path':row['documentPath'],'revision':tab.get('revision',''),
               'unsaved':bool(tab.get('unsaved')),'read_only':bool(tab.get('read_only')),
               'virtual':bool(tab.get('virtual')),'language':tab.get('language',''),
               'selection':deepcopy(self.owner.editor.bridge._selections.get(row['documentPath'],{})),
               'branch':deepcopy(tab.get('neobranch'))}
        if content:
            value['content']=tab['content'][:262144];value['truncated']=len(tab['content'])>262144
        return value

    def read_for_transform(self,path):
        """Local tool transforms get the full model, without widening ui read."""
        row,tab=self._document(path)
        value=self._document_info(row,tab)
        value.update(content=tab['content'],truncated=False)
        self.activity.touch(row['path'],'read')
        return value

    def context(self,content=False):
        owner=self.owner
        documents=[self._document_info(row,owner.editor.bridge._tabs[row['documentPath']],content=content and row['path']==owner.activeTab)
                   for row in owner._tab_data if row['kind']=='editor' and row['documentPath'] in owner.editor.bridge._tabs]
        return {'version':1,'session_id':self.events.session_id,'project':owner.project,'ready':owner._ready,
                'active_tree':owner.activeTreeId,'selection':list(owner._selection),'active_tab':owner.activeTab,'focus':owner._focus_area,
                'trees':[{'id':index,'name':os.path.basename(directory.base),'path':directory.base,'location':directory.location}
                         for index,directory in enumerate(owner.directories[:2]) if directory.base],
                'branches':self._branches(),'documents':documents,'mounts':self._mounts(),
                'activity':owner._tasks.activity_snapshot(),
                'device':{'mode':owner._android_tools.mode,'badge':owner._android_tools.badge,
                          'flashing':owner._android_tools.running,'refreshing':owner._android_tools.refreshing},
                'event_cursor':self.events.read()['latest'],'requests':self._pending_requests(),
                'inbox':self.inbox.store.snapshot(limit=20),'settings':self.settings_info(),'workspace':self.workspace_context()}

    def workspace_context(self):
        from core.agent_workspace import describe
        return describe(self.owner)

    def settings_info(self):
        return {'agent':agent_settings(self.owner),'project':deepcopy(getattr(self.owner,'projectSettings',{})),
                'agent_can_change_settings':False,'guide_command':'rak ui guide agent.ui_triggers'}

    def capabilities(self):
        from core.agent_cli import HELP
        from core.cli_contract import PRIMARY
        from core.unix_tools import resolve_tool
        return {'version':1,'session_id':self.events.session_id,'transport':'authenticated-local-ipc',
                'commands':HELP.splitlines(),'events':{'wait_max_seconds':30,'capacity':512,'gap_requires_context_refresh':True},
                'primary_commands':PRIMARY,'path_discovery':'rak inspect <logical-path> --json; use operations[].argv',
                'request_schemas':{
                    'ask':{'title':'Question','message':'Choose or enter an answer','choices':['Option A','Option B']},
                    'notify':{'title':'Result','message':'Details for the user'},
                    'edit':{'path':'logical path from context','revision':'revision from ui read','content':'complete replacement text'},
                    'review':{'title':'Review changes','path':'logical path','revision':'revision from ui read','content':'proposed text'}},
                'editing':{'requires_revision':True,'writes':'Monaco buffer or revision-checked background file','undo':True,'save':'automatic' if agent_settings(self.owner)['autosave'] else 'user Ctrl+S; background edits require autosave',
                           'requires_ui_triggers':False,'preferred_cli':'rak ui edit <path> --revision <revision> --content-file <utf8-file> --wait 30',
                           'scratch_directory':self.workspace_context()['scratch_directory'],
                           'review_rechecks_revision':True},
                'settings':self.settings_info(),'ui_controls':{'discovery':'rak ui controls','requires':'agent.ui_triggers','enabled':agent_settings(self.owner)['ui_triggers'],'settings_mutation':False},
                'existing_commands':['doctor','inspect','ls','cat','grep','cp','mv','rm','mkdir','touch','ln','readlink','chmod','sed','stat','sha256sum','cmp','diff','find','head','tail','wc','activity','branch','img','unpack','repack','mount','unmount'],
                'verification':{'paths':'merged on-disk NeoBranch view','metadata':'rak stat PATH --json',
                    'content_hash':'rak sha256sum FILE','differences':'rak cmp FILE1 FILE2; rak diff -ur DIR1 DIR2',
                    'status_1':'cmp/diff differences are normal results, not operation errors','diagnostics':'rak doctor --json'},
                'unix_commands':{'paths':'logical active NeoBranch view','text_edit':"rak sed -i 's/old/new/g' <file>",
                                 'open_buffers':'revision checked, Monaco acknowledgment, autosave required for -i',
                                 'folder_copy':'-r/-a required; existing directories merge; -n skips only existing files',
                                 'sed':{'engine':'GNU sed','available':bool(resolve_tool('sed')),'script_io':False,'backup_suffix':False},
                                 'grep':{'engine':'GNU grep','available':bool(resolve_tool('grep')),'status_1':'no matches'},
                                 'chmod':'octal Android filesystem metadata'},
                'kitchen_files':{'help':'rak archive --help','headless':True,'partition_selection':True,
                                 'payload_sources':['payload.bin','OTA.zip'],'results':'--json; waits for actual task result',
                                 'archive_pack':['zip','7z','tar','tar.gz','tar.bz2','tar.xz'],
                                 'output_conflicts':'extraction and pack reject existing files',
                                 'branch_outputs':'extract outside active partitions; archive pack reads merged logical view'},
                'inbox':{'delivery':'cooperative CLI consumer','persistent':self.inbox.store.path is not None,
                         'atomic_claims':True,'stop':'request and agent acknowledgment; does not kill tools'},
                'job_cancellation':False,'device_command_api':False}

    def _pending_requests(self):
        with self._condition:return [deepcopy(row) for row in self._requests.values() if row['state'] in ('pending','applying')]

    def _new_request(self,kind,data):
        with self._condition:
            if sum(row['state'] in ('pending','applying') for row in self._requests.values())>=32:
                raise ValueError('Too many pending agent requests')
            for key in list(self._requests):
                if len(self._requests)<128:break
                if self._requests[key]['state'] not in ('pending','applying'):self._requests.pop(key,None)
            key=uuid.uuid4().hex
            row={'id':key,'kind':kind,'state':'pending','created':time.time(),'session_id':self.events.session_id}
            self._requests[key]=row;self._payloads[key]=data
            self._command_contexts[key]=current_command()
        self.events.publish('request.created',row)
        return deepcopy(row)

    def response(self,key,wait=0):
        end=time.monotonic()+wait
        with self._condition:
            if key not in self._requests:raise ValueError('Unknown agent request: '+key)
            while self._requests[key]['state'] in ('pending','applying') and not self._closed and time.monotonic()<end:
                self._condition.wait(end-time.monotonic())
            return deepcopy(self._requests[key])

    def _finish(self,key,state,response=None):
        self.activity.end('edit:'+key)
        with self._condition:
            row=self._requests.get(key)
            if row is None or row['state'] not in ('pending','applying'):return
            context=self._command_contexts.pop(key,None)
            if context is not None and state=='failed' and response:
                context.record('Agent '+row['kind']+'\n\n'+str(response.get('error','')),
                               application_error=response.get('application_error',False))
            row.update(state=state,response=response,finished=time.time())
            self._payloads.pop(key,None);self._condition.notify_all();result=deepcopy(row)
        self.events.publish('request.finished',result)

    def _show_next(self):
        if self._closed or self._presented:return
        while self._prompts:
            key=self._prompts.popleft()
            if self.response(key)['state']!='pending':continue
            self._presented=key
            self.owner.presentationRequested.emit('agentPrompt',{'id':key,'kind':self._requests[key]['kind'],**self._payloads[key]})
            break

    def answer(self,key,accepted,values):
        if key!=self._presented:return
        self._presented=None
        row=self.response(key)
        if row['state']=='pending':
            if not accepted:self._finish(key,'cancelled')
            elif row['kind']=='review':self._apply(key)
            else:self._finish(key,'answered',{'answer':str(values.get('answer',''))})
        self._show_next()

    def _apply(self,key):
        with command_scope(self._command_contexts.get(key)):return self._apply_request(key)

    def _apply_request(self,key):
        payload=self._payloads[key]
        try:
            self._check_edit_available(payload['path'],exclude=key)
            if payload.get('background'):
                if not agent_settings(self.owner)['autosave']:raise ValueError('Enable Agent autosave to edit unopened files, or edit an already open buffer.')
                if self._document_row(payload['path']):raise ValueError('The file is now open. Read its current editor revision first.')
                from core.agent_documents import apply
                from ui.workspace_actions import operation
                files=self._files(payload['path'])
                with self._condition:self._requests[key]['state']='applying'
                self.activity.begin('edit:'+key,[payload['path']],'write')
                def settled(ok,result):
                    if ok:
                        self.events.publish('document.saved',{'path':payload['path'],'revision':result['revision'],'background':True})
                        self._finish(key,'applied',{'path':payload['path'],'revision':result['revision'],'saved':True,'background':True})
                    else:self._finish(key,'failed',{'saved':False,'error':str(result),
                                                   'application_error':getattr(result,'application_error',False),
                                                   'error_code':getattr(result,'code','operation_failed')})
                operation(self.owner,'Agent edit '+os.path.basename(payload['path']),
                          lambda:apply(files,payload['path'],payload['revision'],payload['content']),settled=settled)
                return
            row,tab=self._document(payload['path'])
            if tab.get('read_only'):raise ValueError('This document is read-only')
            if tab.get('revision')!=payload['revision']:raise ValueError('Document changed. Read the current buffer and prepare a new edit.')
        except (ValueError,OSError) as error:
            self._finish(key,'conflict',{'error':str(error)});return
        with self._condition:self._requests[key]['state']='applying'
        self.activity.begin('edit:'+key,[payload['path']],'write')
        self.owner.editor.bridge.request_agent_edit(key,row['documentPath'],tab['content'],payload['content'])

    def _edit_finished(self,key,path,ok,reason):
        with command_scope(self._command_contexts.get(key)):return self._edit_finished_request(key,path,ok,reason)

    def _edit_finished_request(self,key,path,ok,reason):
        payload=self._payloads.get(key)
        if not payload:return
        if not ok:self._finish(key,'conflict',{'error':reason});return
        tab=self.owner.editor.bridge._tabs.get(path)
        if tab is None or tab['content']!=payload['content']:
            self._finish(key,'conflict',{'error':'The buffer changed after the edit. It was not autosaved.'});return
        if not agent_settings(self.owner)['autosave']:
            self._finish(key,'applied',{'path':self.logical(path),'revision':self._revision(path),'saved':False});return
        self._saving[path]=(key,self._revision(path))
        self.owner.editor.bridge.save_file(path,tab['content'])

    def _agent_saved(self,path):
        pending=self._saving.pop(path,None)
        if pending:
            key,revision=pending
            if self._revision(path)!=revision:self._finish(key,'conflict',{'error':'The buffer changed while saving. Read the current revision.','saved_revision':revision})
            else:self._finish(key,'applied',{'path':self.logical(path),'revision':revision,'saved':True})

    def _agent_save_failed(self,path,message):
        pending=self._saving.pop(path,None)
        if pending:
            failure=self.owner.editor.bridge.save_failure(path)
            self._finish(pending[0],'failed',{'path':self.logical(path),'saved':False,'error':message,
                                             'application_error':getattr(failure,'application_error',False),
                                             'error_code':getattr(failure,'code','save_failed')})

    def _files(self,path):
        from core.workspace_files import WorkspaceFiles
        for directory in self.owner.directories[:2]:
            if not directory.base:continue
            files=WorkspaceFiles(directory.base)
            try:files.logical(path)
            except ValueError:continue
            return files
        raise ValueError('Path is outside the project')

    def _protect_settings_path(self,path):
        getter=getattr(self.owner,'settings_path',None)
        if getter and not path.startswith('mount://') and self._file_key(path)==self._file_key(getter()):
            raise ValueError('Project settings can only be changed by the user. Use rak ui guide to show a setting.')

    def _check_edit_available(self,path,exclude=None):
        if any(row['id']!=exclude and row['state']=='applying' and self._file_key(self._payloads.get(row['id'],{}).get('path',''))==self._file_key(path)
               for row in self._requests.values()):raise ValueError('An agent edit is still applying or saving this file. Wait for its result.')

    def _path(self,path,cwd):
        if not isinstance(path,str) or not path:raise ValueError('A project path is required')
        if path.startswith('mount://'):
            if not self.owner._mounts.reference(path,resolve=False):raise ValueError('This image is not mounted')
            return path
        value=os.path.abspath(os.path.join(cwd,path))
        # Resolve the logical active branch view, including symlink boundaries.
        from core.workspace_files import WorkspaceFiles
        for directory in self.owner.directories[:2]:
            if not directory.base:continue
            try:WorkspaceFiles(directory.base).view.resolve(value)
            except ValueError:continue
            return value
        raise ValueError('Path is outside the open project trees')

    def handle(self,options,cwd):
        if self._closed:raise ValueError('Kitchen session is closed')
        command=options['command']
        if command=='context':return self.context(options['content'])
        if command=='capabilities':return self.capabilities()
        if command=='inbox':
            action=options['action'];store=self.inbox.store
            if action=='list':return store.snapshot(state=options['state'],limit=options['limit'])
            if action=='get':return store.get(options['target'])
            if action=='cancel':return store.cancel(options['target'])
            return store.update(options['target'],options['agent'],options['claim'],action,options.get('payload'))
        action=options['action']
        if action=='cancel':
            key=options['target']
            if self.response(key)['state']=='applying':raise ValueError('Edit is already applying. Wait for its result; use editor Undo to revert it.')
            self._finish(key,'cancelled')
            if self._presented==key:self.owner.presentationRequested.emit('agentDismiss',{'id':key})
            return self.response(key)
        if action=='read':
            path=self._path(options['target'],cwd) if options['target'] else None
            requested=path
            if path and not self._document_row(path) and not path.startswith('mount://'):
                from core.agent_documents import read
                result=read(self._files(path),path)[0]
            else:
                row,tab=self._document(path);path=row['path'];result=self._document_info(row,tab,content=True)
            if requested and not requested.startswith('mount://'):
                result['requested_path']=requested;result['symlinks']=self._files(requested).view.link_chain(requested)
            self.activity.touch(path,'read');return result
        if action=='settings':return self.settings_info()
        if action=='controls':return {'controls':self.controls.snapshot(),'settings':self.settings_info()}
        if action=='guide':
            target=options['target']
            if target not in SETTING_KEYS:raise ValueError('Unknown setting; use agent.autosave, agent.highlight or agent.ui_triggers')
            self.owner.presentationRequested.emit('agentSettingGuide',{'target':target})
            return {'accepted':True,'setting':target,'changed':False,'user_action_required':True}
        if action in ('trigger','scroll'):
            return self.controls.invoke(options['target'],options.get('payload') or {},scroll=action=='scroll')
        if action in ('open','reveal'):
            require_ui(self.owner)
            path=self._path(options['target'],cwd)
            requested=path;traversed=[]
            if action=='open' and not path.startswith('mount://'):
                view=self._files(path).view
                traversed=view.link_chain(path);path=view.canonical(path)
                row=self._document_row(path)
                if row:path=row['path']
            if action=='reveal':
                if path.startswith('mount://'):raise ValueError('Use ui open for mounted image files')
                for index,directory in enumerate(self.owner.directories[:2]):
                    if not directory.base:continue
                    from core.workspace_files import WorkspaceFiles
                    try:WorkspaceFiles(directory.base).view.resolve(path)
                    except ValueError:continue
                    self.owner.selectTree(index);directory.revealPath(path);break
            else:
                line=options.get('line') or 1;column=options.get('column') or 1
                self._navigation[path]=(line,column,options.get('end_line') or line,options.get('end_column') or column)
                if path.startswith('mount://'):self.owner.openPath(path)
                else:
                    from core.workspace_files import WorkspaceFiles
                    physical=None
                    for directory in self.owner.directories[:2]:
                        if not directory.base:continue
                        try:physical=WorkspaceFiles(directory.base).view.resolve(path)[0];break
                        except ValueError:pass
                    if not physical or not os.path.isfile(physical):raise FileNotFoundError(path)
                    self.owner.openAs(path,'editor')
                self._tabs_changed()
            return {'accepted':True,'action':action,'path':path,'requested_path':requested,'symlinks':traversed}
        payload=deepcopy(options['payload'])
        if action in ('edit','review'):
            payload={key:value for key,value in payload.items() if key in ('path','revision','content','title')}
            payload['path']=self._path(payload.get('path'),cwd)
            self._protect_settings_path(payload['path'])
            self._check_edit_available(payload['path'])
            if not self._document_row(payload['path']) and not payload['path'].startswith('mount://'):
                from core.agent_documents import read
                tab=read(self._files(payload['path']),payload['path'])[0];row={'title':os.path.basename(payload['path'])};payload['background']=True
            else:
                row,tab=self._document(payload['path']);payload['path']=row['path']
            if tab.get('read_only'):raise ValueError('This document is read-only')
            if not payload.get('revision') or payload['revision']!=tab.get('revision'):raise ValueError('Document revision conflict. Read the current buffer first.')
            if not isinstance(payload.get('content'),str):raise ValueError('content must be text')
            payload['title']=str(payload.get('title','Review changes'))[:120]
            if action=='review':
                payload['diff']='\n'.join(difflib.unified_diff(tab['content'].splitlines(),payload['content'].splitlines(),
                                                          fromfile=row['title'],tofile=row['title']+' (proposed)',lineterm=''))[:262144]
                if not payload['diff'] and tab['content']!=payload['content']:payload['diff']='Line endings or the final newline will change.'
        elif action in ('ask','notify'):
            if not isinstance(payload.get('message'),str) or not payload['message'].strip():raise ValueError('message is required')
            choices=payload.get('choices',[])
            if not isinstance(choices,list) or len(choices)>12 or any(not isinstance(value,str) or not value.strip() for value in choices):raise ValueError('choices must contain up to 12 non-empty strings')
            payload={'title':str(payload.get('title','Agent'))[:120],'message':payload['message'][:12000],'choices':choices}
        request=self._new_request(action,payload)
        if action=='edit':self._apply(request['id'])
        else:self._prompts.append(request['id']);self._show_next()
        return self.response(request['id'])

    def close(self):
        self._closed=True
        self.activity.close()
        self._saving.clear()
        self.chat.close()
        self.inbox.close()
        for row in self._pending_requests():self._finish(row['id'],'cancelled',{'reason':'Kitchen session closed'})
        self.events.publish('workspace.closed',{});self.events.close()
