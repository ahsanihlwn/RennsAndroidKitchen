# SPDX-License-Identifier: MPL-2.0
import os
from PySide6.QtCore import QObject,Property,Signal,Slot,Qt,QTimer
from services.async_jobs import AsyncJobs
from ui.file_icons import file_icon
from ui.partition_picker import PartitionPicker
from ui.rows import RowsModel


class DiffHtml(QObject):
    changed=Signal()
    activeChanged=Signal()
    def __init__(self,parent):
        super().__init__(parent)
        self._html=''
        self._active=False
        self._side_a={'text':'','marks':[],'lineNumbers':[],'path':'','binary':False}
        self._side_b={'text':'','marks':[],'lineNumbers':[],'path':'','binary':False}
    @Property(str,notify=changed)
    def html(self):return self._html
    @Property('QVariantMap',notify=changed)
    def sideA(self):return self._side_a
    @Property('QVariantMap',notify=changed)
    def sideB(self):return self._side_b
    @Property(bool,notify=activeChanged)
    def active(self):return self._active
    def load(self,html='',*,side_a=None,side_b=None):
        self._html=html or ''
        if side_a is not None:self._side_a=side_a
        if side_b is not None:self._side_b=side_b
        self._active=True;self.activeChanged.emit();self.changed.emit()
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
    progressChanged=Signal()
    saveRequested=Signal()
    closeRequested=Signal('QVariantMap')
    def __init__(self,private):
        super().__init__(private);self.private=private;self.workspace=private.workspace
        self._a=PartitionPicker(self.workspace);self._b=PartitionPicker(self.workspace)
        self._a.changed.connect(self.changed);self._b.changed.connect(self.changed)
        self._jobs=AsyncJobs(self,limit=1);self._engine=None;self._closed=False
        self._busy=False;self._visible=False;self._available=False;self._status=''
        self._live_status='';self._progress=0.0;self._phase='idle';self._stats={}
        self._logs=RowsModel(parent=self);self._diffs=RowsModel(parent=self);self._entries=[];self._selected=-1;self._rings={}
        self._web=DiffHtml(self);self._sig=CompareSignals(self)
        self._pending_logs=[];self._log_serial=0
        self._flush_timer=QTimer(self);self._flush_timer.setSingleShot(True);self._flush_timer.timeout.connect(self._flush_logs)
        self._progress_timer=QTimer(self);self._progress_timer.setInterval(40);self._progress_timer.timeout.connect(self._poll_progress)
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
    @Property(str,notify=progressChanged)
    def liveStatus(self):return self._live_status
    @Property(float,notify=progressChanged)
    def progress(self):return self._progress
    @Property(str,notify=progressChanged)
    def phase(self):return self._phase
    @Property('QVariantMap',notify=changed)
    def stats(self):return self._stats
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
        self._busy=True;self._available=True;self._visible=True;self._status='Scanning\u2026'
        self._phase='scanning';self._progress=-1.0;self._live_status='Scanning directories\u2026';self._stats={}
        self.changed.emit();self.progressChanged.emit()
        self._progress_timer.start()
        engine=self._engine
        def work():engine._worker();return True,engine._report
        def done(ok,result):
            self._progress_timer.stop()
            self._flush_logs();self._busy=False
            if ok:
                summary=(engine._report or {}).get('summary',{})
                path_diffs=int(summary.get('only_a_paths',0) or 0)+int(summary.get('only_b_paths',0) or 0)+int(summary.get('type_changed',0) or 0)
                ctx_cnt=summary.get('context_diff')
                perm_cnt=summary.get('perm_diff')
                cfg_diffs=(int(ctx_cnt or 0)+int(perm_cnt or 0)) if ctx_cnt is not None else None
                self._stats={
                    'onlyA':int(summary.get('only_a_paths',0) or 0),
                    'onlyB':int(summary.get('only_b_paths',0) or 0),
                    'typeChanged':int(summary.get('type_changed',0) or 0),
                    'ctxDiffs':ctx_cnt,
                    'permDiffs':perm_cnt,
                    'contentDiffs':len(self._entries),
                    'sharedEntries':int(summary.get('shared_entries',0) or 0),
                    'filesA':int(((engine._report or {}).get('a') or {}).get('files',0) or 0),
                    'filesB':int(((engine._report or {}).get('b') or {}).get('files',0) or 0),
                }
                self._phase='done';self._progress=1.0
                parts=[f"{len(self._entries):,} content"]
                if path_diffs:parts.append(f"{path_diffs:,} paths")
                if cfg_diffs:parts.append(f"{cfg_diffs:,} config")
                self._live_status=f"Done  \u00b7  {'  \u00b7  '.join(parts)}" if (len(self._entries) or path_diffs or cfg_diffs) else "Done  \u00b7  No differences"
                self._status='Done'
            else:
                self._phase='error';self._progress=0.0
                self._live_status=str(result)
                self._status=str(result)
            self.changed.emit();self.progressChanged.emit()
        self._jobs.submit('compare',work,None,done);return True
    def _poll_progress(self):
        if not self._busy or not self._engine or self._closed:return
        snap=self._engine.progress_snapshot()
        phase=snap['phase'];sa=snap['scanned_a'];sb=snap['scanned_b']
        cd=snap['cmp_done'] or snap['phase_done'];ct=snap['cmp_total'] or snap['phase_total']
        if phase=='scanning':
            text=f"Scanning  \u00b7  A: {sa:,}  \u00b7  B: {sb:,}" if (sa or sb) else "Scanning directories\u2026"
            prog=-1.0
        elif phase=='ring1':
            text=f"Analyzing structure  \u00b7  A: {sa:,}  \u00b7  B: {sb:,}"
            prog=0.28
        elif phase=='ring2':
            text="Checking contexts & permissions\u2026"
            prog=0.38
        elif phase=='content_scan':
            text=f"Comparing content  {cd:,} / {ct:,}" if ct else "Comparing content\u2026"
            prog=0.40+0.45*(cd/ct) if ct else 0.40
        elif phase=='diffing':
            pd=snap['phase_done'];pt=snap['phase_total'];item=os.path.basename(snap['item']) if snap['item'] else ''
            tail=f"  \u00b7  {item}" if item else ''
            text=f"Building diffs  {pd:,} / {pt:,}{tail}" if pt else "Building diffs\u2026"
            prog=0.85+0.15*(pd/pt) if pt else 0.85
        else:
            return
        if text!=self._live_status or abs(prog-self._progress)>0.005 or phase!=self._phase:
            self._live_status=text;self._progress=prog;self._phase=phase;self._status=text
            self.progressChanged.emit();self.changed.emit()
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
        self._rings[str(ring)]='skipped' if a<0 else (f'({a+b})' if a+b else 'done')
        self.changed.emit()
    @Slot(object)
    def _entry(self,entry):
        if self._closed:return
        self._entries.append(entry)
        self._diffs.extend([{
            'key':entry.rel,'path':entry.rel,
            'name':os.path.basename(entry.rel),'dir':os.path.dirname(entry.rel),
            'icon':file_icon(entry.rel),
            'binary':entry.is_binary,'sizeA':entry.size_a,'sizeB':entry.size_b,
            'delta':entry.size_b-entry.size_a,
            'hunks':sum(1 for op in entry.opcodes if op[0]!='equal'),
        }])
        if self._selected<0:self.select(0)
    @Slot(int)
    def select(self,index):
        if not 0<=index<len(self._entries):return
        entry=self._entries[index];self._selected=index
        if entry.is_binary:
            diff_a={i for i,row in enumerate(entry.hex_a) if i>=len(entry.hex_b) or row!=entry.hex_b[i]}
            diff_b={i for i,row in enumerate(entry.hex_b) if i>=len(entry.hex_a) or row!=entry.hex_a[i]}
            side_a={
                'text':'\n'.join(entry.hex_a),
                'marks':['changed' if i in diff_a else 'normal' for i in range(len(entry.hex_a))],
                'lineNumbers':[str(i+1) for i in range(len(entry.hex_a))],
                'path':entry.rel,'binary':True,
            }
            side_b={
                'text':'\n'.join(entry.hex_b),
                'marks':['changed' if i in diff_b else 'normal' for i in range(len(entry.hex_b))],
                'lineNumbers':[str(i+1) for i in range(len(entry.hex_b))],
                'path':entry.rel,'binary':True,
            }
        else:
            texts_a=[];marks_a=[];nums_a=[]
            texts_b=[];marks_b=[];nums_b=[]
            ln_a=0;ln_b=0
            for tag,i1,i2,j1,j2 in entry.opcodes:
                if tag=='equal':
                    for line in entry.lines_a[i1:i2]:
                        ln_a+=1;texts_a.append(line.rstrip('\r\n'));marks_a.append('normal');nums_a.append(str(ln_a))
                    for line in entry.lines_b[j1:j2]:
                        ln_b+=1;texts_b.append(line.rstrip('\r\n'));marks_b.append('normal');nums_b.append(str(ln_b))
                elif tag=='replace':
                    na,nb=i2-i1,j2-j1;pairs=min(na,nb)
                    for k in range(pairs):
                        ln_a+=1;ln_b+=1
                        texts_a.append(entry.lines_a[i1+k].rstrip('\r\n'));marks_a.append('changed');nums_a.append(str(ln_a))
                        texts_b.append(entry.lines_b[j1+k].rstrip('\r\n'));marks_b.append('changed');nums_b.append(str(ln_b))
                    for k in range(pairs,na):
                        ln_a+=1
                        texts_a.append(entry.lines_a[i1+k].rstrip('\r\n'));marks_a.append('changed');nums_a.append(str(ln_a))
                        texts_b.append('');marks_b.append('ghost');nums_b.append('')
                    for k in range(pairs,nb):
                        ln_b+=1
                        texts_a.append('');marks_a.append('ghost');nums_a.append('')
                        texts_b.append(entry.lines_b[j1+k].rstrip('\r\n'));marks_b.append('changed');nums_b.append(str(ln_b))
                elif tag=='delete':
                    for line in entry.lines_a[i1:i2]:
                        ln_a+=1
                        texts_a.append(line.rstrip('\r\n'));marks_a.append('changed');nums_a.append(str(ln_a))
                        texts_b.append('');marks_b.append('ghost');nums_b.append('')
                elif tag=='insert':
                    for line in entry.lines_b[j1:j2]:
                        ln_b+=1
                        texts_a.append('');marks_a.append('ghost');nums_a.append('')
                        texts_b.append(line.rstrip('\r\n'));marks_b.append('changed');nums_b.append(str(ln_b))
            side_a={'text':'\n'.join(texts_a),'marks':marks_a,'lineNumbers':nums_a,'path':entry.rel,'binary':False}
            side_b={'text':'\n'.join(texts_b),'marks':marks_b,'lineNumbers':nums_b,'path':entry.rel,'binary':False}
        self._web.load(side_a=side_a,side_b=side_b);self.changed.emit()
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
        self._progress_timer.stop()
        if self._engine:self._engine.cancel()
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
        self._progress_timer.stop()
        if self._engine:self._engine.cancel()
        self._flush_timer.stop();self._jobs.stop();self._a.close();self._b.close()
