# SPDX-License-Identifier: MPL-2.0
"""Cancellable filename search over the active project/NeoBranch view."""
import os
import threading

from PySide6.QtCore import QObject,Property,Signal,Slot,QTimer
from services.async_jobs import AsyncJobs
from ui.rows import RowsModel
from ui.file_icons import file_icon


class WorkspaceSearch(QObject):
    changed=Signal()
    batchReady=Signal(int,object)
    selected=Signal(str)
    def __init__(self,parent=None):
        super().__init__(parent)
        self._rows=RowsModel(parent=self)
        self._jobs=AsyncJobs(self,limit=1)
        self._cancel=threading.Event()
        self._generation=0;self._query='';self._base='';self._priority='';self._busy=False;self._error=''
        self._timer=QTimer(self);self._timer.setSingleShot(True);self._timer.setInterval(160)
        self._timer.timeout.connect(self._search)
        self.batchReady.connect(self._batch)

    @Property(QObject,constant=True)
    def rows(self):return self._rows
    @Property(bool,notify=changed)
    def busy(self):return self._busy
    @Property(str,notify=changed)
    def error(self):return self._error
    @Property(str,notify=changed)
    def queryText(self):return self._query
    @Property(str,notify=changed)
    def scopeLabel(self):return os.path.basename(os.path.normpath(self._base)) if self._base else ''
    @Property(int,notify=changed)
    def resultCount(self):return sum(not row.get('separator') for row in self._rows.snapshot())

    def prepare(self,base,priority):
        self.cancel();self._base=base;self._priority=priority
        self.changed.emit()

    @Slot(str)
    def query(self,text):
        self._cancel.set();self._query=text.strip();self._generation+=1
        self._rows.replace([],animate=False);self._error=''
        self._busy=bool(self._query and self._base)
        if self._busy:self._timer.start()
        else:self._timer.stop()
        self.changed.emit()

    @Slot()
    def cancel(self):
        self._timer.stop();self._cancel.set();self._jobs.reset();self._generation+=1
        self._busy=False;self.changed.emit()

    @Slot(str)
    def choose(self,path):
        self.cancel();self.selected.emit(path)

    def _batch(self,generation,rows):
        if generation!=self._generation:return
        self._rows.extend(rows)
        self.changed.emit()

    def _search(self):
        if not self._query or not self._base:return
        self._cancel=threading.Event()
        cancel=self._cancel;generation=self._generation
        normalized=self._query.strip('"').replace('\\','/').casefold()
        terms=normalized.split();absolute=os.path.isabs(normalized)
        base=self._base;priority=self._priority
        self._busy=True;self.changed.emit()
        def work():
            from core.rak_neobranch_view import ActiveProjectView
            if not terms:return True,''
            view=ActiveProjectView(base);seen=set();all_rows=[];batch=[]
            def flush():
                nonlocal batch
                if batch:self.batchReady.emit(generation,batch);batch=[]
            def walk(directory,exclude=None):
                found=0;stack=[directory]
                while stack and found<100 and not cancel.is_set():
                    current=stack.pop()
                    try:entries=view.list_dir(current)
                    except (OSError,ValueError):continue
                    directories=[]
                    for entry in entries:
                        if cancel.is_set() or found>=100:break
                        if entry['path'] in seen or entry['path']==exclude:continue
                        relative=os.path.relpath(entry['path'],base).replace('\\','/')
                        candidate=(entry['path'].replace('\\','/') if absolute else relative).casefold()
                        if all(term in candidate for term in terms):
                            row={'key':entry['path'],'path':entry['path'],'name':entry['name']+('/' if entry['kind']=='dir' else ''),
                                 'parent':relative.rsplit('/',1)[0] if '/' in relative else os.path.basename(os.path.normpath(base)),
                                 'icon':'folder' if entry['kind']=='dir' else file_icon(entry['path'])}
                            batch.append(row);all_rows.append(row);seen.add(entry['path']);found+=1
                            if len(all_rows)==1 or len(batch)>=12:flush()
                        if entry['kind']=='dir':
                            physical,_=view.resolve(entry['path'])
                            if physical and not os.path.islink(physical):directories.append(entry['path'])
                    stack.extend(reversed(directories))
                    flush()
                flush()
            if priority and priority!=base:
                walk(priority)
                if all_rows and not cancel.is_set():
                    batch.append({'key':'__separator__','separator':True});flush()
            walk(base,priority if priority!=base else None)
            return True,''
        def finished(ok,payload):
            if generation!=self._generation:return
            self._error='' if ok else str(payload)
            self._busy=False;self.changed.emit()
        self._jobs.submit('search',work,None,finished)

    def close(self):self.cancel();self._jobs.stop()
