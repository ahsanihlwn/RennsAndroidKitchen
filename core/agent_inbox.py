# SPDX-License-Identifier: MPL-2.0
"""Durable user requests, atomically claimed by cooperative Kitchen agents."""
from collections import OrderedDict
from copy import deepcopy
import json
import os
from pathlib import Path
import re
import tempfile
import threading
import time
import uuid

ACTIVE=('queued','claimed','running')
FINISHED=('completed','failed','cancelled','interrupted')


class AgentInbox:
    def __init__(self,path=None,*,notify=None,publish=None):
        self.path=Path(path) if path else None
        self._condition=threading.Condition();self._rows=OrderedDict();self._closed=False
        self._waiting={};self._notify=notify or (lambda:None);self._publish=publish or (lambda *_:None)
        if self.path and self.path.exists():
            saved=json.loads(self.path.read_text(encoding='utf-8'))
            if not isinstance(saved,dict) or saved.get('version')!=1 or not isinstance(saved.get('messages'),list):
                raise ValueError('Invalid Kitchen agent inbox history')
            if len(saved['messages'])>128:raise ValueError('Agent inbox history exceeds its entry limit')
            for row in saved['messages']:
                fields=('id','message','context','state','created','updated','target','agent','status','answer','paths','cancel_requested','claim')
                if not isinstance(row,dict) or not all(key in row for key in fields):
                    raise ValueError('Invalid agent inbox entry')
                if row['state'] not in (*ACTIVE,*FINISHED) or not isinstance(row['context'],dict):raise ValueError('Invalid agent inbox state')
                if not all(isinstance(row[key],str) for key in ('id','message','target','agent','status','answer')) or not isinstance(row['paths'],list):raise ValueError('Invalid agent inbox content')
                if row['id'] in self._rows:raise ValueError('Duplicate agent inbox request ID')
                self._rows[row['id']]=row
            interrupted=False
            for row in self._rows.values():
                if row['state'] in ('claimed','running'):
                    row.update(state='interrupted',status='Kitchen session ended before a result was reported.',claim=None,updated=time.time())
                    interrupted=True
            if interrupted:self._save()

    @staticmethod
    def _public(row,with_context=True):return deepcopy({key:value for key,value in row.items() if key!='claim' and (with_context or key!='context')})

    def _save(self):
        if not self.path:return
        self.path.parent.mkdir(parents=True,exist_ok=True)
        descriptor,temporary=tempfile.mkstemp(prefix='.inbox-',suffix='.tmp',dir=self.path.parent)
        try:
            with os.fdopen(descriptor,'w',encoding='utf-8') as stream:
                json.dump({'version':1,'messages':list(self._rows.values())},stream,ensure_ascii=False)
                stream.flush();os.fsync(stream.fileno())
            os.replace(temporary,self.path)
        finally:
            if os.path.exists(temporary):os.remove(temporary)

    def _change(self,kind,mutate):
        with self._condition:
            if self._closed:raise ValueError('Kitchen inbox is closed')
            before=deepcopy(self._rows)
            try:
                row=mutate();self._save()
            except Exception:
                self._rows=before;raise
            self._condition.notify_all()
            result=self._public(row)
            self._publish(kind,result);self._notify()
            return result

    def send(self,message,context,*,target=''):
        if not isinstance(message,str) or not message.strip():raise ValueError('Write a request for the agent first.')
        if len(message)>8000:raise ValueError('The request exceeds 8000 characters.')
        if target:self._agent_name(target)
        context=deepcopy(context)
        if len(json.dumps(context,ensure_ascii=False))>131072:raise ValueError('Attached context is too large.')
        def create():
            if sum(row['state'] in ACTIVE for row in self._rows.values())>=32:raise ValueError('The inbox already has 32 unfinished requests.')
            for key in list(self._rows):
                if len(self._rows)<128:break
                if self._rows[key]['state'] in FINISHED:self._rows.pop(key)
            key=uuid.uuid4().hex;now=time.time()
            row={'id':key,'message':message.strip(),'context':context,'target':target,'agent':'','state':'queued',
                 'status':'','answer':'','paths':[],'cancel_requested':False,'created':now,'updated':now,'claim':None}
            self._rows[key]=row;return row
        return self._change('inbox.received',create)

    @staticmethod
    def _agent_name(agent):
        if not isinstance(agent,str) or not re.fullmatch(r'[\w][\w .-]{0,63}',agent):raise ValueError('Use an agent name of 1..64 letters, digits, spaces, dots or dashes.')

    def _row(self,key):
        row=self._rows.get(key)
        if row is None:raise ValueError('Inbox request not found: '+str(key))
        return row

    def get(self,key):
        with self._condition:return self._public(self._row(key))

    def snapshot(self,*,state=None,limit=128):
        with self._condition:
            rows=[self._public(row,False) for row in reversed(list(self._rows.values())) if state is None or row['state']==state]
            return {'version':1,'messages':rows[:limit],'waiting_agents':sorted(self._waiting),'closed':self._closed}

    def next(self,agent,wait=0):
        self._agent_name(agent);end=time.monotonic()+wait
        with self._condition:
            self._waiting[agent]=self._waiting.get(agent,0)+1;self._notify()
            try:
                while not self._closed:
                    row=next((row for row in self._rows.values() if row['state']=='queued' and row['target'] in ('',agent)),None)
                    if row is not None:
                        token=uuid.uuid4().hex
                        def claim():
                            row.update(state='claimed',agent=agent,claim=token,updated=time.time());return row
                        message=self._change('inbox.claimed',claim)
                        return {'version':1,'message':message,'claim':token,'closed':False}
                    remaining=end-time.monotonic()
                    if remaining<=0:break
                    self._condition.wait(remaining)
                return {'version':1,'message':None,'closed':self._closed}
            finally:
                self._waiting[agent]-=1
                if not self._waiting[agent]:self._waiting.pop(agent)
                self._notify()

    def update(self,key,agent,claim,action,payload=None):
        payload=payload or {};self._agent_name(agent)
        if action not in ('start','progress','reply','fail','ack-cancel'):raise ValueError('Unknown inbox update')
        text=payload.get('answer' if action in ('reply','fail') else 'message','')
        if not isinstance(text,str) or len(text)>32000:raise ValueError('Result/status must be text of at most 32000 characters.')
        paths=payload.get('paths',[])
        if not isinstance(paths,list) or len(paths)>20 or any(not isinstance(path,str) or not path for path in paths):raise ValueError('paths must contain up to 20 file paths.')
        def change():
            row=self._row(key)
            if not claim or row.get('claim')!=claim or row['agent']!=agent:raise ValueError('This request belongs to another agent or an expired claim.')
            if row['state'] not in ('claimed','running'):raise ValueError('The request is no longer active.')
            if action=='ack-cancel':
                if not row['cancel_requested']:raise ValueError('There is no stop request to acknowledge.')
                row.update(state='cancelled',status='Agent acknowledged the stop request.',claim=None)
            elif action in ('reply','fail'):
                if not text.strip():raise ValueError('A result message is required.')
                row.update(state='completed' if action=='reply' else 'failed',answer=text,paths=paths,claim=None)
            else:
                if row['cancel_requested']:raise ValueError('The user requested a stop. Stop further work and acknowledge cancellation.')
                row.update(state='running',status=text[:2000])
            row['updated']=time.time();return row
        return self._change('inbox.updated',change)

    def cancel(self,key):
        def change():
            row=self._row(key)
            if row['state']=='queued':row.update(state='cancelled',status='Cancelled before an agent received it.')
            elif row['state'] in ('claimed','running'):row['cancel_requested']=True
            else:raise ValueError('This request has already finished.')
            row['updated']=time.time();return row
        return self._change('inbox.updated',change)

    def close(self):
        with self._condition:
            if self._closed:return
            try:
                def interrupt():
                    rows=[row for row in self._rows.values() if row['state'] in ('claimed','running')]
                    for row in rows:row.update(state='interrupted',status='Kitchen session ended before a result was reported.',claim=None,updated=time.time())
                    return {'id':'','state':'closed'}
                if any(row['state'] in ('claimed','running') for row in self._rows.values()):self._change('inbox.closed',interrupt)
            finally:
                self._closed=True;self._condition.notify_all();self._notify()
