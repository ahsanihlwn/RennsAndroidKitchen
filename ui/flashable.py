# SPDX-License-Identifier: MPL-2.0
"""Flashable builder state shared by its native dialog and workspace task queue."""
from copy import deepcopy
import os
import threading

from PySide6.QtCore import QObject,Property,Signal,Slot,QUrl,Qt
from core import flashable_size,ui_lang
from core.flashable_gen import PartSpec,FlashableConfig,generate
from core.flashable_selection import _scan_siblings,_guess_partition_name,_guess_role,_guess_in_firmware,_arc_for
from services.async_jobs import AsyncJobs
from ui.rows import RowsModel


class FlashableBuilder(QObject):
    changed=Signal()
    scriptChanged=Signal()
    progressReady=Signal(int,str,str)
    dismissRequested=Signal()

    def __init__(self,workspace,parent=None):
        super().__init__(parent);self.workspace=workspace
        self._jobs=AsyncJobs(self);self._rows=RowsModel(parent=self)
        self._script='';self._dirty=False;self._getprop=False;self._device='';self._vendor64=True
        self._busy=False;self._loading=False;self._progress=0;self._status='';self._base=''
        self._cancel=threading.Event();self._closed=False
        self.progressReady.connect(self._progress_ready,Qt.QueuedConnection)
    @Property(QObject,constant=True)
    def rows(self):return self._rows
    @Property(str,notify=scriptChanged)
    def script(self):return self._script
    @Property(bool,notify=changed)
    def dirty(self):return self._dirty
    @Property(bool,notify=changed)
    def busy(self):return self._busy
    @Property(bool,notify=changed)
    def loading(self):return self._loading
    @Property(bool,notify=changed)
    def vendor64(self):return self._vendor64
    @Property(bool,notify=changed)
    def getprop(self):return self._getprop
    @Property(str,notify=changed)
    def device(self):return self._device
    @Property(int,notify=changed)
    def progress(self):return self._progress
    @Property(str,notify=changed)
    def status(self):return self._status
    @Property(QUrl,notify=changed)
    def defaultOutput(self):return QUrl.fromLocalFile(os.path.join(self._base,'flashable_'+os.path.basename(self._base)+'.zip'))

    @Slot(str,str)
    def prepare(self,path,base):
        if self._busy:return
        self._base=base;self._dirty=False;self._getprop=False;self._device='';self._vendor64=True
        self._status='';self._progress=0;self._rows.replace([],animate=False)
        self._script='';self.scriptChanged.emit();self._loading=True;self.changed.emit()
        def work():
            from core.fsdetect import detect_fs_type
            return True,[self._row(path,badge,firmware) for path,badge,firmware in _scan_siblings(path,detect_fs_type)]
        def done(ok,result):
            self._loading=False
            if ok:self._rows.replace(result,animate=False);self.regenerate()
            else:self.workspace.message(str(result),ui_lang.t('fl_title'))
            self.changed.emit()
        self._jobs.submit('selection',work,None,done)

    @staticmethod
    def _row(path,badge='',firmware=False):
        name=_guess_partition_name(path);role=_guess_role(name,badge,firmware)
        kind=flashable_size.classify(path)
        try:size=flashable_size.raw_image_size(path,kind);label=flashable_size.human_size(size)
        except Exception:size=0;label='ERR'
        return {'key':path,'path':path,'name':name,'badge':badge,'role':role,'kind':kind,
                'firmware':role=='both_slot' and _guess_in_firmware(name,firmware),
                'checked':True,'zstd':role=='dynamic','size':size,'sizeLabel':label}

    @Slot('QVariantList')
    def addFiles(self,urls):
        paths=[u.toLocalFile() if isinstance(u,QUrl) else QUrl(str(u)).toLocalFile() for u in urls]
        def work():
            from core.fsdetect import detect_fs_type
            return True,[self._row(path,detect_fs_type(path)) for path in paths if path]
        def done(ok,result):
            if ok:self._rows.replace(self._rows.snapshot()+result,animate=False);self._regen_if_clean()
            else:self.workspace.message(str(result),ui_lang.t('fl_title'))
        self._jobs.submit('add',work,None,done)

    @Slot(int,str,'QVariant')
    def edit(self,index,key,value):
        if self._busy:return
        rows=self._rows.snapshot()
        if not 0<=index<len(rows) or key not in ('name','role','checked','zstd'):return
        row=rows[index];row[key]=value
        if key=='role':
            row['firmware']=value=='both_slot' and _guess_in_firmware(row['name'],False)
            if value!='dynamic':row['zstd']=False
        if not self._vendor64:row['zstd']=False
        self._rows.replace(rows,animate=False);self._regen_if_clean()

    @Slot(int)
    def remove(self,index):
        if self._busy:return
        rows=self._rows.snapshot()
        if 0<=index<len(rows):rows.pop(index);self._rows.replace(rows,animate=False);self._regen_if_clean()

    @Slot(bool)
    def setGetprop(self,value):self._getprop=value;self.changed.emit();self._regen_if_clean()
    @Slot(str)
    def setDevice(self,value):self._device=value;self.changed.emit();self._regen_if_clean()
    @Slot(bool)
    def setZstd(self,value):
        rows=self._rows.snapshot()
        for row in rows:
            if row['role']=='dynamic':row['zstd']=value and self._vendor64
        self._rows.replace(rows,animate=False);self._regen_if_clean()
    @Slot(str)
    def editScript(self,value):
        if value!=self._script:self._script=value;self._dirty=True;self.changed.emit()

    def config(self):
        parts=[]
        for row in self._rows.snapshot():
            name=row['name'].strip()
            if not row['checked'] or not name:continue
            from core.workspace_files import validate_name
            validate_name(name)
            zstd=row['zstd'] and row['role']=='dynamic' and self._vendor64
            parts.append(PartSpec(role=row['role'],name=name,arc=_arc_for(name,row['role'],zstd,row['path'],row['firmware']),size=row['size'],zstd=zstd,src=row['path']))
        if len({p.arc for p in parts})!=len(parts):raise ValueError('Duplicate partition names')
        return FlashableConfig(parts=parts,device_code=self._device.strip(),getprop_check=self._getprop,
            banner_title='Flashable ROM',banner_device=self._device.strip() or '-',banner_version='-',use_zstd=any(p.zstd for p in parts))
    def _regen_if_clean(self):
        if not self._dirty:self.regenerate()
    @Slot()
    def regenerate(self):
        try:config=self.config()
        except ValueError:return
        if config.parts:self._script=generate(config);self._dirty=False;self.scriptChanged.emit();self.changed.emit()

    @Slot()
    def readVendor(self):
        rows=self._rows.snapshot()
        target=next((r for r in rows if r['name'].strip().lower()=='vendor'),None)
        if target is None:target=next((r for r in rows if r['kind'] in ('raw','sparse') and r['badge'] in ('EROFS','EXT2','EXT3','EXT4','SPARSE')),None)
        if target is None:self.workspace.message(ui_lang.t('fl_vendor_read_fail'),ui_lang.t('fl_read_vendor'));return
        def work():
            from core.flashable_probe import probe
            return True,probe(target['path'])
        def done(ok,result):
            if not ok or not isinstance(result,dict) or not result.get('ok'):
                self.workspace.message(ui_lang.t('fl_vendor_read_fail')+'\n'+str(result),ui_lang.t('fl_read_vendor'));return
            code=result.get('device_code')
            if code:
                self._getprop=True;self._device=code
                self.workspace.message(ui_lang.t('fl_vendor_read_ok',code=code),ui_lang.t('fl_read_vendor'),error=False)
            self._vendor64=bool(result.get('is_64bit',True))
            if not self._vendor64:self.setZstd(False)
            self.changed.emit();self._regen_if_clean()
        self._jobs.submit('vendor',work,None,done)

    @Slot(QUrl)
    def build(self,url):
        if self._busy:return
        path=url.toLocalFile()
        if not path:return
        if not path.lower().endswith('.zip'):path+='.zip'
        try:config=self.config()
        except ValueError as error:self.workspace.message(str(error),ui_lang.t('fl_title'));return
        if not config.parts:self.workspace.message(ui_lang.t('fl_no_parts'),ui_lang.t('fl_title'));return
        parts=deepcopy(config.parts);script=self._script
        self._cancel.clear();self._busy=True;self._progress=0;self._status=ui_lang.t('fl_building');self.changed.emit()
        def worker():
            from core.flashable_build import build_flashable
            def progress(part,total,label,pct,done,amount,*_):
                if self._closed:return
                text=ui_lang.t('fl_done') if label=='Done' else ui_lang.t('fl_building')+': '+label
                if amount>0:text+=' ('+flashable_size.human_size(done)+' / '+flashable_size.human_size(amount)+')'
                self.progressReady.emit(int(pct),text,str(pct)+'%')
            return build_flashable(path,parts,script,progress=progress,is_cancelled=self._cancel.is_set)
        def done(ok,result):
            self._busy=False;self.changed.emit()
            if self._closed:return
            if ok:
                self._progress=100;self._status=ui_lang.t('fl_done');self.changed.emit()
                self.workspace.message(ui_lang.t('fl_done')+'\n'+str(result),ui_lang.t('fl_done'),dismissed=self.dismissRequested.emit,error=False)
            elif result!='Cancelled':self.workspace.message(str(result),ui_lang.t('fl_failed'))
            else:self._status='';self.changed.emit()
        self.workspace._tasks.enqueue('flashable:'+path,worker,done,lane='heavy',targets=(path,))

    @Slot(int,str,str)
    def _progress_ready(self,pct,text,_):
        if self._closed:return
        self._progress=pct;self._status=text;self.changed.emit()
    @Slot(result=bool)
    def cancel(self):
        if not self._busy:return True
        self._cancel.set();self._status=ui_lang.t('fl_cancelling');self.changed.emit();return False
    def close(self):self._closed=True;self._cancel.set();self._jobs.stop()
