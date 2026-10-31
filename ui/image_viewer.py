# SPDX-License-Identifier: MPL-2.0
"""Native image document and scene-graph image provider."""
import os
import threading
import uuid
from PySide6.QtCore import QObject,Property,Signal,Slot,QUrl,Qt
from PySide6.QtGui import QImage,QPainter
from PySide6.QtQuick import QQuickImageProvider
from services.async_jobs import AsyncJobs
from core.image_edit import _qimage_to_ndarray,_crop_canvas,_scanline_fill,_defringe,_human_size
from core.ui_lang import t

_images={}
_lock=threading.RLock()


class DocumentImages(QQuickImageProvider):
    def __init__(self):super().__init__(QQuickImageProvider.Image)
    def requestImage(self,identifier,size,requestedSize):
        with _lock:image=QImage(_images.get(identifier.split('?')[0],QImage()))
        if size is not None:size.setWidth(image.width());size.setHeight(image.height())
        return image


class ImageDocument(QObject):
    changed=Signal()
    pixelsChanged=Signal()
    refitRequested=Signal()
    dirtyChanged=Signal(bool)
    messageRequested=Signal(str,str)
    def __init__(self,parent=None):
        super().__init__(parent);self._key=uuid.uuid4().hex;self._image=QImage();self._path='';self._version=0
        self._dirty=False;self._undo=[];self._busy=False;self._closed=False;self._jobs=AsyncJobs(self,limit=1);self._stroke=False
    @Property(str,notify=pixelsChanged)
    def source(self):
        if self._image.isNull():return ''
        if self.svg:return QUrl.fromLocalFile(self._path).toString()
        return 'image://documents/'+self._key+'?v='+str(self._version)
    @Property(int,notify=pixelsChanged)
    def imageWidth(self):return self._image.width()
    @Property(int,notify=pixelsChanged)
    def imageHeight(self):return self._image.height()
    @Property(bool,notify=changed)
    def svg(self):return self._path.lower().endswith('.svg')
    @Property(bool,notify=changed)
    def busy(self):return self._busy
    @Property(bool,notify=changed)
    def dirty(self):return self._dirty
    @Property(bool,notify=changed)
    def canUndo(self):return bool(self._undo)
    @Property(str,notify=pixelsChanged)
    def info(self):
        try:size=os.path.getsize(self._path)
        except OSError:size=0
        return f'{os.path.basename(self._path)}   ·   {self.imageWidth}×{self.imageHeight}   ·   {_human_size(size)}   ·   {os.path.splitext(self._path)[1][1:].upper() or "?"}'
    @Property(QUrl,notify=changed)
    def fileUrl(self):return QUrl.fromLocalFile(self._path)
    def _publish(self,dirty=True,refit=False):
        if self._closed:return
        self._version+=1;self._dirty=dirty
        with _lock:_images[self._key]=QImage(self._image)
        self.pixelsChanged.emit();self.changed.emit();self.dirtyChanged.emit(dirty)
        if refit:self.refitRequested.emit()
    @Slot(str)
    def load(self,path):
        self._path=path;self._image=QImage(path);self._undo=[];self._publish(False,True)
    def _push(self):
        self._undo.append(self._image.copy());self._undo=self._undo[-30:]
    @Slot()
    def undo(self):
        if self._undo and not self._busy:self._image=self._undo.pop();self._publish()
    @Slot(int,int,str)
    def resizeImage(self,width,height,mode):
        if self.svg or self._busy or self._image.isNull() or not 0<width<=30000 or not 0<height<=30000:return
        self._image=self._image.scaled(width,height,Qt.IgnoreAspectRatio,Qt.SmoothTransformation) if mode=='scale' else _crop_canvas(self._image,width,height)
        self._publish(True,True)
    @Slot(float,float,str,str,int,int)
    def toolPoint(self,x,y,phase,tool,brush,threshold):
        if self.svg or self._busy or self._image.isNull():return
        x,y=int(x),int(y)
        if tool=='erase':
            if phase=='press':self._push();self._stroke=True
            if not self._stroke:return
            self._image=self._image.convertToFormat(QImage.Format_ARGB32)
            painter=QPainter(self._image);painter.setRenderHint(QPainter.Antialiasing);painter.setCompositionMode(QPainter.CompositionMode_Clear);painter.setPen(Qt.NoPen);painter.setBrush(Qt.black)
            radius=max(1,brush//2);painter.drawEllipse(x-radius,y-radius,radius*2,radius*2);painter.end();self._publish()
            if phase=='release':self._stroke=False
        elif tool=='magic' and phase=='press' and 0<=x<self.imageWidth and 0<=y<self.imageHeight:
            target=self._image.pixelColor(x,y)
            if target.alpha()==0:return
            def erase(image):
                import numpy as np
                array,ref=_qimage_to_ndarray(image);rgb=array[:,:,:3].astype(np.int32)
                distance=((rgb-np.array([target.blue(),target.green(),target.red()]))**2).sum(axis=2)
                array[_scanline_fill(distance<=(threshold/100*441.673)**2,x,y)]=0
            self._edit_job(erase)
    def _edit_job(self,edit):
        self._push();work=self._image.convertToFormat(QImage.Format_ARGB32).copy();self._busy=True;self.changed.emit()
        def run(_=None):edit(work);return True,work
        def done(ok,result):
            if self._closed:return
            self._busy=False
            if ok:self._image=result;self._publish()
            else:self._undo.pop();self.messageRequested.emit(t('iv_save_failed'),str(result));self.changed.emit()
        self._jobs.submit('pixels',run,None,done)
    @Slot(int)
    def smoothEdges(self,strength):
        if not self.svg and not self._busy and not self._image.isNull():self._edit_job(lambda image:_defringe(image,max(1,int(strength/100*255))))
    @Slot(str)
    def save(self,url=''):
        if self.svg or self._busy or self._image.isNull():return
        path=QUrl(url).toLocalFile() if url.startswith('file:') else url or self._path
        if self._image.save(path):self._publish(False)
        else:self.messageRequested.emit(t('iv_save_failed'),t('iv_save_failed')+': '+os.path.basename(path))
    @Slot()
    def close(self):
        self._closed=True;self._jobs.stop()
        with _lock:_images.pop(self._key,None)
