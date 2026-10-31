# SPDX-License-Identifier: MPL-2.0
import os
from PySide6.QtCore import QObject,Property,Signal,Slot
from core.file_picker import _quick_access,_get_drives,_file_size_str
from services.async_jobs import AsyncJobs
from ui.rows import RowsModel


class RenxecPicker(QObject):
    changed=Signal()
    locationsChanged=Signal()
    chosen=Signal(str)
    def __init__(self,parent):
        super().__init__(parent);self._jobs=AsyncJobs(self);self._rows=RowsModel(parent=self)
        self._directory=os.path.expanduser('~');self._query='';self._selected='';self._all=[];self._error=''
        self._locations=[];self._locations_requested=False
    @Property(QObject,constant=True)
    def rows(self):return self._rows
    @Property(str,notify=changed)
    def directory(self):return self._directory
    @Property(str,notify=changed)
    def selected(self):return self._selected
    @Property(str,notify=changed)
    def error(self):return self._error
    @Property('QVariantList',notify=locationsChanged)
    def locations(self):return self._locations
    @Slot()
    def refreshLocations(self):
        # QML reads this property while constructing the still-hidden picker.
        # Filesystem discovery belongs to an explicit open, never a getter.
        self._locations_requested=True
        def work():
            rows=[{'header':'QUICK ACCESS','path':'','name':''}]
            rows.extend({'name':name,'path':path,'header':''} for name,path in _quick_access())
            rows.append({'header':'DRIVES','path':'','name':''})
            rows.extend({'name':path,'path':path,'header':''} for path in _get_drives())
            return True,rows
        def done(ok,result):
            if ok:self._locations=result;self.locationsChanged.emit()
        self._jobs.submit('locations',work,None,done)
    @Slot(str)
    def load(self,path):
        if not os.path.isdir(path):return
        if not self._locations_requested:self.refreshLocations()
        self._directory=os.path.abspath(path);self._selected='';self._query='';self._error='';self.changed.emit()
        def work():
            rows=[]
            with os.scandir(path) as entries:
                for entry in entries:
                    if entry.name.startswith('.'):continue
                    folder=entry.is_dir()
                    if not folder and os.path.splitext(entry.name)[1].lower() not in ('.renxec','.zip'):continue
                    rows.append({'key':entry.path,'name':entry.name,'path':entry.path,'folder':folder,'size':'' if folder else _file_size_str(entry.path)})
            return True,sorted(rows,key=lambda row:(not row['folder'],row['name'].lower()))
        def done(ok,result):
            self._all=result if ok else [];self._error='' if ok else '⚠ Permission denied'
            self.filter('');self.changed.emit()
        self._jobs.submit('directory',work,None,done)
    @Slot(str)
    def filter(self,text):self._query=text;self._rows.replace([row for row in self._all if text.lower() in row['name'].lower()],animate=False)
    @Slot(str)
    def select(self,path):self._selected=path;self.changed.emit()
    @Slot(str)
    def activate(self,path):
        if os.path.isdir(path):self.load(path)
        elif os.path.isfile(path):self.chosen.emit(path)
    @Slot()
    def up(self):self.load(os.path.dirname(self._directory))
    @Slot(result=bool)
    def canOpen(self):return os.path.isfile(self._selected)
    def close(self):self._jobs.stop()
