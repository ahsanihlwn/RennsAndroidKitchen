# SPDX-License-Identifier: MPL-2.0
import os
from PySide6.QtCore import QObject,Property,Signal,Slot
from core.rak_neobranch_view import ActiveProjectView
from core.fsdetect import detect_fs_root
from services.async_jobs import AsyncJobs
from ui.rows import RowsModel


class PartitionPicker(QObject):
    changed=Signal()
    dismissRequested=Signal()
    def __init__(self,workspace):
        super().__init__(workspace);self.workspace=workspace;self._jobs=AsyncJobs(self)
        self._rows=RowsModel(parent=self);self._tree=0;self._selected='';self._callback=None;self._busy=False
    @Property(QObject,constant=True)
    def rows(self):return self._rows
    @Property(bool,constant=True)
    def dual(self):return bool(self.workspace.p2)
    @Property('QStringList',constant=True)
    def treeLabels(self):
        return [os.path.basename(os.path.normpath(name)) for name in (self.workspace.p1,self.workspace.p2) if name]
    @Property(int,notify=changed)
    def tree(self):return self._tree
    @Property(str,notify=changed)
    def selected(self):return self._selected
    @Property(str,notify=changed)
    def caption(self):return os.path.basename(self.base)
    @Property(bool,notify=changed)
    def busy(self):return self._busy
    @property
    def base(self):return os.path.join(self.workspace.project,self.workspace.p1 if self._tree==0 else self.workspace.p2)
    def show(self,callback):
        self._callback=callback;self.load(0);self.workspace.presentationRequested.emit('pickFilesystem',{})
    @Slot(int)
    def load(self,index):
        if index not in (0,1) or index==1 and not self.dual:return
        self._tree=index;self._selected='';self._busy=True;self.changed.emit();base=self.base
        def work():
            entries=[]
            for entry in ActiveProjectView(base).list_dir(base):
                if entry['kind']!='dir' or entry['name'] in ('config','neocat_config','automation','.git'):continue
                try:kind=detect_fs_root(base,entry['name']) or ''
                except Exception:kind=''
                entries.append({'key':entry['name'],'name':entry['name'],'type':kind,'icon':'fs_folder' if kind else 'folder'})
            return True,entries
        def done(ok,result):
            self._busy=False;self._rows.replace(result if ok else [],animate=False);self.changed.emit()
            if not ok:self.workspace.message(str(result),'Select Partition')
        self._jobs.submit('partitions',work,None,done)
    @Slot(str)
    def choose(self,name):
        if any(row['name']==name for row in self._rows.snapshot()):self._selected=name;self.changed.emit()
    @Slot(bool)
    def finish(self,accepted):
        callback,self._callback=self._callback,None
        if accepted and self._selected and callback:callback(self.base,self._selected)
    def close(self):self._callback=None;self._jobs.stop()
