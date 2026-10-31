# SPDX-License-Identifier: MPL-2.0
import os
from PySide6.QtCore import QObject,Property,Signal,Slot,Qt,QTimer
from services.async_jobs import AsyncJobs
from ui.partition_picker import PartitionPicker
from ui.rows import RowsModel


class DiffHtml(QObject):
    changed=Signal()
    activeChanged=Signal()
    def __init__(self,parent):super().__init__(parent);self._html='';self._active=False
    @Property(str,notify=changed)
    def html(self):return self._html
    @Property(bool,notify=activeChanged)
    def active(self):return self._active
    def load(self,html):
        self._html=html;self._active=True;self.activeChanged.emit();self.changed.emit()
    def stop(self):self._active=False;self.activeChanged.emit()


class CompareSignals(QObject):
    log_line=Signal(str,str)
    ring_done=Signal(int,int,int)
    ring_skip=Signal(int)
    diff_panel_init=Signal(int)
    diff_entry=Signal(object)
    all_done=Signal()


class WorkspaceCompare(QObject):
    changed=Signal()
    saveRequested=Signal()
    closeRequested=Signal('QVariantMap')
    def __init__(self,private):
        super().__init__(private);self.private=private;self.workspace=private.workspace
        self._a=PartitionPicker(self.workspace);self._b=PartitionPicker(self.workspace)
        self._a.changed.connect(self.changed);self._b.changed.connect(self.changed)
        self._jobs=AsyncJobs(self,limit=1);self._engine=None;self._closed=False
        self._busy=False;self._visible=False;self._available=False;self._status=''
        self._logs=RowsModel(parent=self);self._diffs=RowsModel(parent=self);self._entries=[];self._selected=-1;self._rings={}
        self._web=DiffHtml(self);self._sig=CompareSignals(self)
        self._pending_logs=[];self._log_serial=0
        self._flush_timer=QTimer(self);self._flush_timer.setSingleShot(True);self._flush_timer.timeout.connect(self._flush_logs)
        self._sig.log_line.connect(self._log,Qt.QueuedConnection)
        self._sig.ring_done.connect(self._ring,Qt.QueuedConnection)
        self._sig.ring_skip.connect(lambda ring:self._ring(ring,-1,-1),Qt.QueuedConnection)
        self._sig.diff_entry.connect(self._entry,Qt.QueuedConnection)
    @Property(QObject,constant=True)
    def sideA(self):return self._a
    @Property(QObject,constant=True)
    def sideB(self):return self._b
    @Property(QObject,constant=True)
    def web(self):return self._web
    @Property(QObject,constant=True)
    def logs(self):return self._logs
    @Property(QObject,constant=True)
    def diffs(self):return self._diffs
    @Property(bool,notify=changed)
    def busy(self):return self._busy
    @Property(bool,notify=changed)
    def visible(self):return self._visible
    @Property(bool,notify=changed)
    def available(self):return self._available
    @Property(str,notify=changed)
    def status(self):return self._status
    @Property('QVariantMap',notify=changed)
    def rings(self):return self._rings
    @Property(int,notify=changed)
    def selected(self):return self._selected
    @Property(str,notify=changed)
    def selectedName(self):return self._entries[self._selected].rel if 0<=self._selected<len(self._entries) else ''
    @Property(str,notify=changed)
    def nameA(self):return os.path.basename(self._engine.base_a) if self._engine else ''
    @Property(str,notify=changed)
    def nameB(self):return os.path.basename(self._engine.base_b) if self._engine else ''
    @Slot()
    def showPicker(self):
        if not self.private._check():return
        if self._available:self._visible=True;self.changed.emit();return
        self._a.load(0);self._b.load(1 if self.workspace.p2 else 0)
        self.workspace.presentationRequested.emit('comparePicker',{})
    @Slot(result=bool)
    def start(self):
        if not self.private._check() or self._busy:return False
        if not self._a.selected or not self._b.selected:
            self.workspace.message('Select a partition on both sides.','Compare FileSystem');return False
        a=os.path.join(self._a.base,self._a.selected);b=os.path.join(self._b.base,self._b.selected)
        if os.path.normcase(a)==os.path.normcase(b):self.workspace.message('Select two different folders.','Compare FileSystem');return False
        from rennsproject.core.compare_document import CompareEngine
        self._engine=CompareEngine(a,b,self._a.base,self._b.base,self._sig)
        self._pending_logs=[];self._log_serial=0;self._flush_timer.stop()
        self._logs.replace([],animate=False);self._diffs.replace([],animate=False);self._entries=[];self._selected=-1;self._rings={}
        self._busy=True;self._available=True;self._visible=True;self._status='Scanning…';self.changed.emit()
        engine=self._engine
        def work():engine._worker();return True,engine._report
        def done(ok,result):
            self._flush_logs();self._busy=False;self._status='Done' if ok else str(result);self.changed.emit()
        self._jobs.submit('compare',work,None,done);return True
    @Slot(str,str)
    def _log(self,text,hint):
        if self._closed:return
        self._engine._log_12.append(text)
        self._pending_logs.append({'key':str(self._log_serial),'text':text,'hint':hint});self._log_serial+=1
        if not self._flush_timer.isActive():self._flush_timer.start(24)
    def _flush_logs(self):
        rows,self._pending_logs=self._pending_logs,[]
        self._logs.extend(rows)
    @Slot(int,int,int)
    def _ring(self,ring,a,b):
        if self._closed:return
        self._rings[str(ring)]='skipped' if a<0 else '✔'+('  ('+str(a+b)+')' if a+b else '')
        self.changed.emit()
    @Slot(object)
    def _entry(self,entry):
        if self._closed:return
        self._entries.append(entry)
        self._diffs.extend([{'key':entry.rel,'path':entry.rel,'binary':entry.is_binary}])
        if self._selected<0:self.select(0)
    @Slot(int)
    def select(self,index):
        if not 0<=index<len(self._entries):return
        from rennsproject.core.compare_document import _build_monaco_html,_build_hex_html
        entry=self._entries[index];self._selected=index
        if entry.is_binary:
            a={i for i,row in enumerate(entry.hex_a) if i>=len(entry.hex_b) or row!=entry.hex_b[i]}
            b={i for i,row in enumerate(entry.hex_b) if i>=len(entry.hex_a) or row!=entry.hex_a[i]}
            html=_build_hex_html(entry.hex_a,entry.hex_b,a,b)
        else:html=_build_monaco_html(entry.lines_a,entry.lines_b,entry.opcodes)
        self._web.load(html);self.changed.emit()
    @Slot(int)
    def skip(self,ring):
        if self._engine and ring in (2,3):getattr(self._engine,'_skip'+str(ring)).set()
    @Slot()
    def hide(self):self._visible=False;self.changed.emit()
    @Slot()
    def requestClose(self):
        if self._busy:self.closeRequested.emit({'title':'Compare still running','text':'Close this comparison?','confirm':True,'danger':True,'acceptLabel':'Close'})
        else:self._close_comparison()
    @Slot(str,'QVariantMap')
    def dispatch(self,action,data):
        if action=='confirm':self._close_comparison()
    def _close_comparison(self):
        if self._engine:self._engine._cancel.set()
        self._available=False;self._visible=False;self.changed.emit()
    @Slot()
    def requestSave(self):
        if self._engine and not self._busy:self.saveRequested.emit()
    @Slot(str,str,int)
    def save(self,mode,reference,tree):
        if self._busy or not self._engine or not self._engine._report:return
        engine=self._engine;base=os.path.join(self.workspace.project,self.workspace.p1 if tree==0 else self.workspace.p2)
        def work():
            if mode=='log':return True,engine.save_session(base)
            from rennsproject.core.compare_renxec import build_renxec
            from rennsproject.core.compare_document import COMPARE_DIRNAME,_session_dirname,_safe_name
            folder=os.path.join(base,COMPARE_DIRNAME,_session_dirname(self.nameA,self.nameB));os.makedirs(folder,exist_ok=True)
            path=os.path.join(folder,'upgrade_'+_safe_name(engine._report[reference]['name'])+'.renxec')
            result=build_renxec(engine._report,path,reference=reference)
            if not result['operations']:
                os.remove(path);return False,'No changes to include in the automation package.'
            return True,path
        def done(ok,result):self._status=('Saved: ' if ok else 'Save failed: ')+str(result);self.changed.emit()
        self._jobs.submit('save',work,None,done)
    def close(self):
        self._closed=True
        if self._engine:self._engine._cancel.set()
        self._flush_timer.stop();self._jobs.stop();self._a.close();self._b.close()
