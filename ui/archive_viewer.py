# SPDX-License-Identifier: MPL-2.0
import os
from PySide6.QtCore import QObject,Property,Signal,Slot,Qt
from core.archive_document import ArchiveReader
from services.async_jobs import AsyncJobs
from ui.rows import RowsModel
from ui.file_icons import file_icon

class ArchiveDocument(QObject):
    changed=Signal()
    def __init__(self,parent=None):
        super().__init__(parent);self._jobs=AsyncJobs(self,limit=1);self._closed=False;self._nodes=[];self._visible=[];self._expanded=set();self._selected=set();self._anchor=-1;self._kind='';self._status='';self._rows=RowsModel(parent=self)
    @Property(QObject,constant=True)
    def rows(self):return self._rows
    @Property('QStringList',notify=changed)
    def selected(self):return [r['path'] for r in self._nodes if r['path'] in self._selected]
    @Property(str,notify=changed)
    def status(self):return self._status
    @Slot(str)
    def load(self,path):
        def work(_=None):
            with ArchiveReader(path) as reader:return True,(reader.archive_type,reader.members)
        def done(ok,result):
            if self._closed:return
            if not ok:self._status='Error: '+str(result);self.changed.emit();return
            self._kind,members=result;tree={}
            for path in members:
                node=tree
                for name in path.strip('/').split('/'):node=node.setdefault(name,{})
            self._nodes=[]
            def visit(nodes,parent='',depth=0):
                for name in sorted(nodes,key=lambda name:(not bool(nodes[name]),name)):
                    path=parent+'/'+name if parent else name;folder=bool(nodes[name])
                    self._nodes.append({'key':path,'path':path,'parent':parent,'name':name,'depth':depth,'folder':folder,'icon':'folder' if folder else file_icon(name)})
                    if folder:visit(nodes[name],path,depth+1)
            visit(tree);self._status=f'{len(members)} entries  ·  {(self._kind or "").upper()}';self._refresh()
        self._jobs.submit('archive',work,None,done)
    def _refresh(self):
        self._visible=[];nodes={r['path']:r for r in self._nodes}
        for row in self._nodes:
            parent=row['parent'];show=True
            while parent:
                if parent not in self._expanded:show=False;break
                parent=nodes[parent]['parent']
            if show:self._visible.append(row)
        self._rows.replace([{**row,'expanded':row['path'] in self._expanded,'selected':row['path'] in self._selected} for row in self._visible],animate=False);self.changed.emit()
    @Slot(int)
    def toggle(self,index):
        if not 0<=index<len(self._visible):return
        row=self._visible[index]
        if not row['folder']:return
        if row['path'] in self._expanded:self._expanded.remove(row['path'])
        else:self._expanded.add(row['path'])
        self._refresh()
    @Slot(int,int)
    def select(self,index,modifiers):
        if not 0<=index<len(self._visible):return
        path=self._visible[index]['path']
        if modifiers & Qt.ShiftModifier.value and self._anchor>=0:
            self._selected={r['path'] for r in self._visible[min(self._anchor,index):max(self._anchor,index)+1]}
        elif modifiers & Qt.ControlModifier.value:
            if path in self._selected:self._selected.remove(path)
            else:self._selected.add(path)
            self._anchor=index
        else:self._selected={path};self._anchor=index
        self._status=self.selected[0] if len(self._selected)==1 else f'{len(self._selected)} dipilih';self._refresh()
    @Slot()
    def close(self):self._closed=True;self._jobs.stop()
