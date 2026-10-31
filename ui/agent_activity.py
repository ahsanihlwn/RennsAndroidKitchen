# SPDX-License-Identifier: MPL-2.0
"""Bounded visual activity from actual agent file events, without navigation."""
import os
import time
from collections import deque
from PySide6.QtCore import QObject,Property,Signal,Slot,QTimer
from core.agent_policy import settings


class AgentActivity(QObject):
    changed=Signal()
    def __init__(self,agent):
        super().__init__(agent);self.agent=agent;self._paths={};self._recent={};self._held={}
        self._pending_touches=deque()
        self._touch_timer=QTimer(self);self._touch_timer.setSingleShot(True);self._touch_timer.setInterval(0)
        self._touch_timer.timeout.connect(self._drain_touches)
        self._timer=QTimer(self);self._timer.setInterval(100);self._timer.timeout.connect(self._expire)
        signal=getattr(agent.owner,'settingsChanged',None)
        if signal is not None:signal.connect(self._expire)
    @Property('QVariantMap',notify=changed)
    def paths(self):return dict(self._paths)
    @Slot(str,result=str)
    def key(self,path):return os.path.normcase(path).replace('\\','/')
    def _references(self,path,kind):
        # This is visual bookkeeping, not a file operation. Never resolve/stat
        # disk paths here: native tools can report thousands of outputs, while
        # authoritative I/O validation remains in their filesystem services.
        if not isinstance(path,str) or not path or len(path)>32767 or any(ord(c)<32 for c in path):return {}
        path=self.agent.logical(path)
        if not path.startswith('mount://'):path=os.path.abspath(os.path.join(self.agent.owner.project,path))
        roots=[directory.base for directory in self.agent.owner.directories[:2] if directory.base];base=None
        if not path.startswith('mount://'):
            for root in roots:
                try:inside=os.path.commonpath((os.path.normcase(path),os.path.normcase(root)))==os.path.normcase(root)
                except ValueError:inside=False
                if inside:
                    if os.path.relpath(path,root).split(os.sep)[0]=='neobranch':return {}
                    base=root
                    break
            else:return {}
        elif not self.agent.owner._mounts.reference(path,resolve=False):return {}
        self.agent.events.publish('agent.file',{'path':path,'kind':kind})
        now=time.monotonic();values=[path]
        if path.startswith('mount://'):
            while '/' in values[-1][8:]:values.append(values[-1].rsplit('/',1)[0])
        else:
            # Start at the touched path: touching the tree root must not walk
            # above it, where dirname eventually returns the same drive root.
            parent=path
            while os.path.normcase(parent)!=os.path.normcase(base):
                previous=parent;parent=os.path.dirname(parent)
                if not parent or parent==previous:break
                values.append(parent)
        return {self.key(value):{'kind':kind,'at':now} for value in values}
    def touch(self,path,kind='read'):
        # Completed one-shot CLI operations get a short visual pulse. Native
        # tools and applying edits instead hold their paths until acknowledgment.
        if settings(self.agent.owner)['highlight']:self._recent.update(self._references(path,kind))
        else:self._references(path,kind)
        self._expire()
    def touch_many(self,paths,kind='read'):
        """Deliver large completed-operation lists in bounded GUI turns."""
        self._pending_touches.append((iter(paths),kind))
        if not self._touch_timer.isActive():self._touch_timer.start()
    def _drain_touches(self):
        deadline=time.perf_counter()+.004;processed=0;highlight=settings(self.agent.owner)['highlight']
        while self._pending_touches and processed<32 and time.perf_counter()<deadline:
            paths,kind=self._pending_touches[0]
            try:path=next(paths)
            except StopIteration:self._pending_touches.popleft();continue
            references=self._references(path,kind)
            if highlight:self._recent.update(references)
            processed+=1
        self._expire()
        if self._pending_touches:self._touch_timer.start()
    def begin(self,token,paths,kind='tool'):
        held=self._held.get(token,{})
        for path in paths:
            if path:
                for key,row in self._references(path,kind).items():
                    row['started']=held.get(key,{}).get('started',row['at']);row['state']='held';held[key]=row
        if held:self._held[token]=held
        self._expire()
    def end(self,token):
        self._release([token])
    def _release(self,tokens):
        released={}
        for token in tokens:released.update(self._held.pop(token,{}))
        still_held={path for rows in self._held.values() for path in rows}
        now=time.monotonic()
        for path,row in released.items():
            if path in still_held:continue
            until=row.get('started',row['at'])+.8
            if until>now:self._recent[path]={**row,'state':'released','expires':until}
            else:self._recent.pop(path,None)
        self._expire()
    def end_group(self,prefix):
        self._release([token for token in self._held if token.startswith(prefix)])
    def tool(self,token,paths,kind,status):
        terminal=status.lower() in ('completed','complete','success','done','failed','error','declined','denied','rejected','interrupted','cancelled','canceled')
        if terminal:
            if token not in self._held:
                for path in paths:
                    if path:self.touch(path,kind)
            self.end(token)
        else:self.begin(token,paths,kind)
    def _expire(self):
        now=time.monotonic()
        self._recent={path:row for path,row in self._recent.items() if row.get('expires',row['at']+1.2)>now}
        if len(self._recent)>256:self._recent=dict(sorted(self._recent.items(),key=lambda item:item[1]['at'])[-256:])
        combined=dict(self._recent)
        for rows in self._held.values():combined.update(rows)
        next_paths=dict(sorted(combined.items(),key=lambda item:item[1]['at'])[-256:]) if settings(self.agent.owner)['highlight'] else {}
        if next_paths!=self._paths:self._paths=next_paths;self.changed.emit()
        if self._recent:self._timer.start()
        else:self._timer.stop()
    def close(self):
        self._touch_timer.stop();self._pending_touches.clear()
        self._timer.stop();self._recent.clear();self._held.clear();self._paths.clear();self.changed.emit()
