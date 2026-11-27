# SPDX-License-Identifier: MPL-2.0
"""Selection and asynchronous metadata for the two original partition viewers."""
from PySide6.QtCore import QObject,Property,Signal,Slot,Qt
from core.command_context import failure_summary
from core.partition_viewer import payload_partitions,super_info
from services.async_jobs import AsyncJobs


class PartitionDocument(QObject):
    changed=Signal()
    messageRequested=Signal(str,str)
    def __init__(self,parent=None):
        super().__init__(parent);self._jobs=AsyncJobs(self,limit=1);self._closed=False;self._generation=0
        self._kind='';self._parts=[];self._a=[];self._b=[];self._selected=set();self._anchor={'a':-1,'b':-1};self._busy=False;self._active='';self._error=''

    @Property('QStringList',notify=changed)
    def partsA(self):return self._a
    @Property('QStringList',notify=changed)
    def partsB(self):return self._b
    @Property('QStringList',notify=changed)
    def selected(self):return [p for p in self._a+self._b if p in self._selected]
    @Property(int,notify=changed)
    def total(self):return len(self._a)+len(self._b)
    @Property(bool,notify=changed)
    def busy(self):return self._busy
    @Property(str,notify=changed)
    def activeSlot(self):return self._active
    @Property(str,notify=changed)
    def error(self):return self._error

    @Slot(str,str)
    def load(self,path,kind):
        if self._closed:return
        self._generation+=1;generation=self._generation;self._kind=kind;self._busy=True;self._error='';self.changed.emit()
        def worker(_=None):
            if kind=='super':return True,super_info(path)
            if kind=='oem':
                from core.oem_containers import inspect_container
                info=inspect_container(path)
                return True,{'partitions':[e['name'] for e in info.get('entries',[]) if e.get('kind')!='dir']}
            return True,{'partitions':payload_partitions(path)}
        def loaded(ok,result):
            if self._closed or generation!=self._generation:return
            self._busy=False
            if ok:
                self._error=''
                self._parts=result['partitions'];self._active=result.get('active_slot','') or ''
                parts=[p for p in self._parts if not p.startswith(('main_','default'))] if kind=='super' else self._parts
                self._a=[p for p in parts if p.endswith('_a')] if kind=='super' else list(parts)
                self._b=[p for p in parts if p.endswith('_b')] if kind=='super' else []
                self._selected.clear();self._anchor={'a':-1,'b':-1}
            else:
                self._parts=[];self._a=[];self._b=[];self._selected.clear();self._anchor={'a':-1,'b':-1}
                self._error=failure_summary(result) or str(result)
            self.changed.emit()
        self._jobs.submit('load',worker,None,loaded,report_operation_errors=False)

    @Slot(str,int,int)
    def select(self,side,index,modifiers):
        parts=self._a if side=='a' else self._b
        if not 0<=index<len(parts):return
        control=bool(modifiers & Qt.ControlModifier.value);shift=bool(modifiers & Qt.ShiftModifier.value)
        if not control:self._selected.difference_update(self._b if side=='a' else self._a)
        if shift and self._anchor[side]>=0:
            if not control:self._selected.difference_update(parts)
            self._selected.update(parts[min(index,self._anchor[side]):max(index,self._anchor[side])+1])
        elif control:
            if parts[index] in self._selected:self._selected.remove(parts[index])
            else:self._selected.add(parts[index])
            self._anchor[side]=index
        else:
            self._selected=set([parts[index]]);self._anchor[side]=index
        self.changed.emit()

    @Slot(bool)
    def selectAll(self,selected):
        self._selected=set(self._a+self._b) if selected else set();self.changed.emit()

    @Slot()
    def close(self):self._closed=True;self._generation+=1;self._jobs.stop()


def extract(session,logical,kind,parts,all_parts=False):
    """Keep extraction jobs owned by the workspace after a viewer tab closes."""
    import os
    from core import partition_viewer as operations
    tab=next((tab for tab in session._tab_data if tab['path']==logical and tab['kind']==kind),None)
    if not tab or kind not in ('payload','super','oem') or session._closing:return
    path=tab['documentPath'];out_dir=os.path.dirname(path)
    # Names are checked before they can become output filenames.
    import re
    selected=list(dict.fromkeys(parts))
    if kind=='oem':
        if any(not p or '..' in p.replace('\\','/').split('/') or p.startswith(('/','\\')) for p in selected):
            session.message('Invalid container entry name');return
    elif any(not re.fullmatch(r'[A-Za-z0-9_.-]+',p) or p in ('.','..') for p in selected):
        session.message('Invalid partition name');return
    if not selected and not (all_parts and kind in ('super','oem')):return
    workers=[]
    def finished(member,ok,result):
        session._history.append({'label':member,'ok':ok,'text':str(result or '')});session.statusChanged.emit()
        if not ok:session.message(str(result),'Extraction failed')
        else:
            for directory in session.directories:
                if directory.base:directory.reloadMetadata()
    if kind=='oem':
        from core.oem_containers import extract_container
        stem=os.path.splitext(os.path.basename(path))[0] or 'oem_extracted'
        oem_out=os.path.join(out_dir,stem)
        pick=None if all_parts else selected
        workers.append((stem,lambda:extract_container(path,oem_out,selected=pick),lambda ok,result:finished(stem,ok,result),(path,oem_out)))
    elif all_parts and kind=='super':
        workers.append(('all',lambda:operations.extract_super(path,None,out_dir),lambda ok,result:finished('all',ok,result),(path,out_dir)))
    else:
        for partition in selected:
            work=(lambda p=partition:operations.extract_payload(path,p,out_dir)) if kind=='payload' else (lambda p=partition:operations.extract_super(path,p,out_dir))
            workers.append((partition,work,lambda ok,result,p=partition:finished(p,ok,result),(path,os.path.join(out_dir,partition+'.img'))))
    try:session._tasks.enqueue_batch('Extracting '+kind,workers,lane='heavy')
    except RuntimeError as error:session.message(str(error))
