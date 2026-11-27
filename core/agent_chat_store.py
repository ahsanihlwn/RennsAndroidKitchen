# SPDX-License-Identifier: MPL-2.0
"""Project-scoped chat history, kept in AppData rather than ROM directories."""
import hashlib
import json
import os
from pathlib import Path
import tempfile
import re

MAX_HISTORY_BYTES=24*1024*1024


def project_folder(project):
    from core.app_paths import USER_STATE_DIR
    digest=hashlib.sha256(os.path.normcase(os.path.realpath(project)).encode()).hexdigest()
    return Path(USER_STATE_DIR)/'agent_chat'/digest


def probe_folder():
    from core.app_paths import AGENT_PROBE_DIR
    folder=Path(AGENT_PROBE_DIR)
    folder.mkdir(parents=True,exist_ok=True)
    return folder


def probe_log_path(provider,operation,suffix='.json'):
    safe_provider=re.sub(r'[^A-Za-z0-9_.-]+','_',str(provider or 'agent')).strip('._') or 'agent'
    safe_op=re.sub(r'[^A-Za-z0-9_.-]+','_',str(operation or 'probe')).strip('._') or 'probe'
    folder=probe_folder()/'logs'
    folder.mkdir(parents=True,exist_ok=True)
    return folder/f'{safe_provider}_{safe_op}{suffix}'


def get_probe_session(provider):
    if not provider:return ''
    try:
        path=probe_folder()/'probe_sessions.json'
        if not path.is_file():return ''
        data=json.loads(path.read_text(encoding='utf-8'))
        sessions=data.get('sessions') if isinstance(data,dict) else None
        value=sessions.get(provider,'') if isinstance(sessions,dict) else ''
        return value if isinstance(value,str) else ''
    except (OSError,ValueError):
        return ''


def set_probe_session(provider,session):
    if not provider:return
    try:
        folder=probe_folder()
        path=folder/'probe_sessions.json'
        sessions={}
        if path.is_file():
            try:
                raw=json.loads(path.read_text(encoding='utf-8'))
                if isinstance(raw,dict) and isinstance(raw.get('sessions'),dict):
                    sessions={str(k):str(v) for k,v in raw['sessions'].items() if isinstance(k,str) and isinstance(v,str) and v}
            except (OSError,ValueError):
                sessions={}
        if session:sessions[str(provider)]=str(session)
        else:sessions.pop(str(provider),None)
        descriptor,name=tempfile.mkstemp(prefix='.probe-',suffix='.tmp',dir=folder)
        try:
            with os.fdopen(descriptor,'w',encoding='utf-8') as stream:
                stream.write(json.dumps({'version':1,'sessions':sessions},ensure_ascii=False))
                stream.flush();os.fsync(stream.fileno())
            os.replace(name,path)
        finally:
            if os.path.exists(name):os.remove(name)
    except OSError:
        pass


class ChatStore:
    def __init__(self,project):
        self.folder=project_folder(project);self.path=self.folder/'history.json'
        self.data={'version':1,'selected':'','draft':'','attachment':None,'chats':[]}
        if not self.path.exists():return
        if self.path.stat().st_size>MAX_HISTORY_BYTES:raise ValueError('Agent chat history exceeds 24 MiB')
        data=json.loads(self.path.read_text(encoding='utf-8'))
        if not isinstance(data,dict) or data.get('version')!=1 or not isinstance(data.get('chats'),list):raise ValueError('Invalid agent chat history')
        if len(data['chats'])>24:raise ValueError('Too many stored agent chats')
        if not isinstance(data.get('draft',''),str) or not isinstance(data.get('selected',''),str) or data.get('attachment') is not None and not isinstance(data['attachment'],dict):raise ValueError('Invalid saved chat draft/context')
        data.setdefault('draft','');data.setdefault('selected','');data.setdefault('attachment',None)
        for chat in data['chats']:
            if not isinstance(chat,dict) or not all(isinstance(chat.get(k),str) for k in ('id','title','provider','session','model','state')) or not isinstance(chat.get('messages'),list):raise ValueError('Invalid chat entry')
            if len(chat['messages'])>400:raise ValueError('Too many chat messages')
            if not re.fullmatch('[0-9a-f]{32}',chat['id']):raise ValueError('Invalid chat identifier')
            efforts=chat.get('efforts',{})
            if not isinstance(efforts,dict) or not all(isinstance(k,str) and isinstance(v,str) for k,v in efforts.items()):raise ValueError('Invalid saved thinking levels')
            for message in chat['messages']:
                if not isinstance(message,dict) or not all(isinstance(message.get(k),str) for k in ('key','role','text')):raise ValueError('Invalid chat message')
                if message['role']=='work' and message.get('status') in ('connecting','working','permission','stopping'):message['status']='interrupted';message['thinking_active']=False
                if message['role']=='thought' and message.get('status')=='thinking':message['status']='interrupted'
            if chat['state'] in ('connecting','working','stopping','permission'):chat['state']='interrupted'
        self.data=data

    def save(self):
        serialized=json.dumps(self.data,ensure_ascii=False)
        # Retain native session IDs and full per-connection logs when old UI
        # previews grow too large; never produce a history we cannot reopen.
        for chat in self.data['chats']:
            if len(serialized.encode('utf-8'))<=MAX_HISTORY_BYTES:break
            if chat['id']==self.data['selected'] or not chat['messages']:continue
            chat['messages']=[];chat['preview_archived']=True
            serialized=json.dumps(self.data,ensure_ascii=False)
        if len(serialized.encode('utf-8'))>MAX_HISTORY_BYTES:raise OSError('Current agent chat preview exceeds the history size limit')
        self.folder.mkdir(parents=True,exist_ok=True)
        descriptor,name=tempfile.mkstemp(prefix='.chat-',suffix='.tmp',dir=self.folder)
        try:
            with os.fdopen(descriptor,'w',encoding='utf-8') as stream:
                stream.write(serialized);stream.flush();os.fsync(stream.fileno())
            os.replace(name,self.path)
        finally:
            if os.path.exists(name):os.remove(name)
