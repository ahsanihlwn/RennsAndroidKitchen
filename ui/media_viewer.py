# SPDX-License-Identifier: MPL-2.0
import os
from PySide6.QtCore import QObject,Property,Signal,Slot,QTimer,QUrl
from core.image_edit import _human_size

class MediaDocument(QObject):
    changed=Signal()
    conversionFinished=Signal()
    def __init__(self,parent=None):
        super().__init__(parent);self._path='';self._busy=False;self._progress='';self._extractor=None;self._closed=False
    @Property(str,notify=changed)
    def name(self):return os.path.basename(self._path)
    @Property(str,notify=changed)
    def fileInfo(self):
        try:size=os.path.getsize(self._path)
        except OSError:size=0
        return _human_size(size)+'   ·   '+(os.path.splitext(self._path)[1][1:].upper() or '?')
    @Property(str,notify=changed)
    def outputFolder(self):return os.path.splitext(self._path)[0]+'_frames'
    @Property(bool,notify=changed)
    def busy(self):return self._busy
    @Property(str,notify=changed)
    def progress(self):return self._progress
    @Slot(str)
    def load(self,path):self._path=path;self.changed.emit()
    @Slot()
    def resetProgress(self):
        if not self._busy:self._progress='';self.changed.emit()
    @Slot(QUrl,result=str)
    def localPath(self,url):return url.toLocalFile()
    @Slot(str,str,int,str,int)
    def convert(self,folder,mode,fps,fmt,quality):
        if self._busy or not folder.strip() or self._closed:return
        from main_ext.class_FrameExtractor import FrameExtractor
        self._busy=True;self._progress='Starting…';self.changed.emit()
        def progress(saved,*_):
            if not self._closed:self._progress=f'{saved} frames saved…';self.changed.emit()
        def done(ok,saved,message):
            self._busy=False
            if self._closed:return
            self._progress=f'Done — {saved} frames' if ok else 'Failed: '+message;self.changed.emit()
            if ok:QTimer.singleShot(700,self,self.conversionFinished.emit)
        self._extractor=FrameExtractor(self._path,folder,mode=mode,target_fps=max(1,min(240,fps)),fmt=fmt,jpg_quality=quality,on_progress=progress,on_done=done,parent=self)
        self._extractor.start()
    @Slot()
    def cancel(self):
        if self._extractor is not None:self._extractor.cancel()
        self._busy=False;self.changed.emit()
    @Slot()
    def close(self):self._closed=True;self.cancel()
