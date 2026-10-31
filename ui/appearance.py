# SPDX-License-Identifier: MPL-2.0
"""Capture the old rendered appearance before committing a palette/language."""
from PySide6.QtCore import QObject,QMetaObject,QTimer,QUrl
from ui.runtime import FrozenImageProvider


class AppearanceCaptures(QObject):
    def __init__(self,engine,owner,model,dialogs):
        super().__init__(owner)
        self.owner,self.model,self.dialogs=owner,model,dialogs
        self.provider=FrozenImageProvider()
        engine.addImageProvider("appearance",self.provider)
        self.pending=None;self.queued={};self.serial=0;self.closed=False
        model.appearance_interceptor=self.request

    def request(self,kind,commit):
        if self.closed or not self.model.settings['animations'] or not self.owner.isVisible():
            commit();return
        if self.pending is not None:
            self.queued[kind]=commit;return
        windows=[self.owner]+[dialog for dialog in self.dialogs if dialog.isVisible()]
        batch={'kind':kind,'commit':commit,'grabs':{},'images':{}}
        self.pending=batch
        for window in windows:
            result=window.contentItem().grabToImage()
            if result:
                batch['grabs'][window]=result
                result.ready.connect(lambda window=window,result=result:self._ready(batch,window,result))
        if not batch['grabs']:self._finish(batch)

    def _ready(self,batch,window,result):
        if self.pending is not batch:return
        batch['images'][window]=result.image()
        if len(batch['images'])==len(batch['grabs']):self._finish(batch)

    def _finish(self,batch):
        if self.pending is not batch:return
        # Existing overlays are part of grabToImage, so rapid selections begin
        # from exactly the frame currently visible, including an unfinished fade.
        self.provider.frames.clear()
        for window,image in batch['images'].items():
            self.serial+=1;key=str(self.serial)
            self.provider.frames[key]=image
            window.setProperty('appearanceFrame',QUrl('image://appearance/'+key))
            window.setProperty('appearanceDuration',240 if batch['kind']=='theme' else 280)
            QMetaObject.invokeMethod(window,'startAppearanceFade')
        batch['commit']()
        self.pending=None
        if self.queued:
            kind=next(iter(self.queued));commit=self.queued.pop(kind)
            self.request(kind,commit)

    def close(self):
        self.closed=True;self.pending=None;self.queued.clear();self.provider.frames.clear()
        self.model.appearance_interceptor=None
