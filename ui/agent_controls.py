# SPDX-License-Identifier: MPL-2.0
"""Declarative controls register their own semantic actions; no widget scanning."""
import uuid
from PySide6.QtCore import QObject,Slot
from core.agent_policy import protected_control,require_ui,settings


class AgentControls(QObject):
    def __init__(self,agent):
        super().__init__(agent);self.agent=agent;self._bindings={};self._protected=set()
    @Slot(QObject,result=str)
    def register(self,binding):
        key=uuid.uuid4().hex;self._bindings[key]=binding
        binding.destroyed.connect(lambda *_:self._bindings.pop(key,None))
        return key
    @Slot(str,bool)
    def protect(self,surface,active):
        if active:self._protected.add(surface)
        else:self._protected.discard(surface)
    def snapshot(self):
        rows=[]
        for key,binding in list(self._bindings.items()):
            descriptor=binding.property('descriptor')
            if hasattr(descriptor,'toVariant'):descriptor=descriptor.toVariant()
            if not descriptor or not descriptor.get('name') or not descriptor.get('visible'):continue
            descriptor=dict(descriptor)
            if protected_control(descriptor['name']):continue
            rows.append({'id':key,**descriptor,'available':settings(self.agent.owner)['ui_triggers'] and bool(descriptor.get('enabled')) and not self._protected})
        return rows[:512]
    def invoke(self,target,payload,*,scroll=False):
        require_ui(self.agent.owner)
        if self._protected:raise ValueError('A user-only dialog is open. The agent cannot interact with Settings, approvals, reviews or error dialogs.')
        matches=[row for row in self.snapshot() if target in (row['id'],row['name'])]
        if len(matches)!=1:raise ValueError('Control is unavailable or ambiguous. Refresh rak ui controls and use its exact id.')
        row=matches[0]
        if not row['available']:raise ValueError('This control is disabled')
        request=self.agent._new_request('control',{'target':row['name']})
        self._bindings[row['id']].actionRequested.emit(request['id'],{'scroll':scroll,'value':payload.get('value')})
        return self.agent.response(request['id'])
    @Slot(str,bool,str)
    def finished(self,key,ok,message):
        if key not in self.agent._requests or self.agent._requests[key]['kind']!='control':return
        self.agent._finish(key,'activated' if ok else 'failed',{'message':message})
