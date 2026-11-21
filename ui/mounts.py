# SPDX-License-Identifier: MPL-2.0
"""Virtual mounted filesystems integrated into the native directory and editor."""
import hashlib
import os
from PySide6.QtCore import QObject,Signal,Slot,QUrl,Qt
from core.mount_index import MountIndex,_build_child_index
from core import imgfs
from services.async_jobs import AsyncJobs

MOUNT_ROLE=int(Qt.UserRole)+60


class WorkspaceMounts(QObject):
    changed=Signal()
    def __init__(self,session):
        super().__init__(session);self.session=session;self._jobs=AsyncJobs(self)
        self._registries={};self._image_base={};self._closed=False
        for directory in session.directories:directory.mounts=self
    def registry(self,base):return self._registries.setdefault(base,MountIndex(base))
    def reference(self,path,*,resolve=True):
        if path.startswith('mount://'):
            image,_,internal=path[8:].partition('//');image=os.path.normpath(image)
        else:image=os.path.normpath(path);internal=''
        base=self._image_base.get(image)
        if base is None:return None
        registry=self.registry(base)
        if image not in registry.indexes:return None
        if resolve:
            parent=internal.rsplit('/',1)[0] if '/' in internal else ''
            row=next((row for row in registry.indexes[image].get(parent,[]) if row['path'].strip('/')==internal),None)
            if row and row.get('is_link') and row.get('target'):
                virtual,physical,is_dir,exists=registry._resolve_symlink_target_for_mount(image,internal,row['target'])
                if virtual:return tuple(virtual)
                if physical and exists:return ('disk',physical)
        return image,internal
    def writable(self,image):
        base=self._image_base.get(os.path.normpath(image))
        return bool(base and self.registry(base).metadata.get(os.path.normpath(image),{}).get('writable'))
    def index(self,image):return self.registry(self._image_base[os.path.normpath(image)]).indexes[os.path.normpath(image)]
    def _reload_views(self):
        for directory in self.session.directories:
            if directory.base:directory.refresh()
        self.changed.emit()
    @Slot(str)
    def mount(self,path):
        path=os.path.normpath(path)
        directory=self.session.active_tree
        from core.workspace_files import WorkspaceFiles
        files=WorkspaceFiles(directory.base);physical,_=files.view.resolve(path)
        if not physical or not os.path.isfile(physical):return
        from core.rak_neobranch_view import source_image_branch
        if source_image_branch(path):self.session.message('Repack the active NeoBranch before mounting its source image.','Mount image');return
        base=directory.base
        def work():
            current=WorkspaceFiles(base);physical,_=current.view.resolve(path)
            if not physical or not os.path.isfile(physical):raise FileNotFoundError(path)
            if source_image_branch(path):raise ValueError('Repack the active NeoBranch before mounting its source image')
            if imgfs.detect_fs(physical) not in ('EXT','EROFS'):raise ValueError('Mount requires an EXT/EROFS filesystem image; convert containers to partition IMG first')
            entries=imgfs.list_image(physical);kind=imgfs.detect_fs(physical);sparse=imgfs._is_sparse_file(physical)
            return True,(_build_child_index(entries),{'fs':kind,'sparse':sparse,'writable':kind in ('EXT','EROFS') and not sparse,'physical':physical})
        def done(result):
            index,meta=result;registry=self.registry(base)
            registry.indexes[path]=index;registry.metadata[path]=meta;self._image_base[path]=base
            self._reload_views()
        from ui.workspace_actions import operation
        return operation(self.session,'Mount image '+os.path.basename(path),work,done)
    @Slot(str)
    def unmount(self,path):
        ref=self.reference(path,resolve=False)
        if not ref:return
        image,_=ref;base=self._image_base.pop(image)
        registry=self.registry(base);registry.indexes.pop(image,None);registry.metadata.pop(image,None)
        for directory in self.session.directories:
            if directory.base==base and (directory.location.startswith('mount://') or directory.location==image):
                directory._history=[];directory._reset_root(directory.base)
        self._reload_views()
    def children(self,image,internal):
        registry=self.registry(self._image_base[image]);rows=registry.indexes[image].get(internal.strip('/'),[])
        if not rows:
            redirected=registry._mountpoint_redirect(image,internal)
            if redirected:image,internal=redirected;rows=registry.indexes[image].get(internal.strip('/'),[])
        return image,internal,rows
    def open(self,path):
        ref=self.reference(path)
        if not ref:return
        image,internal=ref
        if image=='disk':self.session.openPath(internal);return
        if not imgfs.is_text_filename(internal):self.session.message(os.path.basename(internal)+' is not a text file.','Mounted image');return
        tab_id='mount://'+image+'//'+internal
        if any(tab['path']==tab_id for tab in self.session._tab_data):self.session.selectTab(tab_id);return
        def work():
            raw,_=imgfs.read_path(image,internal)
            if not imgfs.is_text_bytes(raw):raise ValueError('This is a binary file')
            return True,imgfs.decode_text(raw)[0]
        def done(ok,text):
            if not ok:self.session.message(str(text),'Mounted image');return
            if any(tab['path']==tab_id for tab in self.session._tab_data):
                self.session.selectTab(tab_id);return
            from core.editor_document import _detect_language
            writable=self.writable(image)
            def save(tab_id,content):self.save_text(image,internal,tab_id,content);return False
            self.session.editor.bridge.open_virtual(tab_id,text,language=_detect_language(internal),read_only=not writable,on_save=save if writable else None)
            self.session._tab_data.append({'key':tab_id,'path':tab_id,'documentPath':tab_id,'title':os.path.basename(internal),'kind':'editor','icon':'txt','unsaved':False,'url':''})
            self.session._active_tab=tab_id;self.session._refresh_tabs();self.session._reveal_right()
            self.session.editor.openFile(tab_id)
        self._jobs.submit('read:'+tab_id,work,None,done)
    def _mutate(self,label,image,work,done=None):
        if not self.writable(image):self.session.message('This mounted image is read-only.','Mounted image');return
        from ui.workspace_actions import operation
        def finished(result):
            if done:done(result)
            self.mount(image)
        operation(self.session,label,work,finished)
    def save_text(self,image,internal,tab_id,content):
        bridge=self.session.editor.bridge
        if not self.writable(image):bridge.documentSaveFailed.emit(tab_id,'This mounted image is read-only');return
        def work():return imgfs.write_path_data(image,internal,content.encode('utf-8'))
        def done(_):
            bridge=self.session.editor.bridge;tab=bridge._tabs.get(tab_id)
            if tab is None:return
            tab['hash']=hashlib.md5(content.encode('utf-8',errors='ignore')).hexdigest()
            tab['unsaved']=hashlib.md5(tab['content'].encode('utf-8',errors='ignore')).hexdigest()!=tab['hash']
            (bridge.fileUnsaved if tab['unsaved'] else bridge.fileSaved).emit(tab_id)
            bridge.documentSaved.emit(tab_id)
        from ui.workspace_actions import operation
        def settled(ok,result):
            if not ok:bridge.documentSaveFailed.emit(tab_id,str(result))
        operation(self.session,'Save '+internal,work,lambda result:(done(result),self.mount(image)),settled=settled)
    def metadata(self,path,column,value):
        image,internal=self.reference(path)
        if column==0:return
        self._mutate('Mounted metadata '+internal,image,lambda:imgfs.set_selinux(image,internal,value) if column==1 else imgfs.set_perm(image,internal,value))
    @Slot(str,QUrl)
    def extract(self,path,url):
        ref=self.reference(path);destination=url.toLocalFile()
        if ref and destination:
            from ui.workspace_actions import operation
            operation(self.session,'Extract '+ref[1],lambda:imgfs.extract_path(ref[0],ref[1],destination))
    @Slot(str)
    def delete(self,path):
        ref=self.reference(path,resolve=False)
        if not ref or not ref[1]:return
        image,internal=ref
        self.session.message("Delete '"+internal+"' from "+os.path.basename(image)+"?\nChanges are written directly to the image.",'Delete from image',
            confirm=lambda:self._mutate('Delete '+internal,image,lambda:imgfs.delete_path(image,internal,recursive=True)),danger=True,accept='Delete')
    @Slot(str)
    def usage(self,path):
        ref=self.reference(path,resolve=False);image=ref[0] if ref else path
        from ui.workspace_actions import operation
        def done(data):
            fmt=imgfs.fmt_size
            lines=[os.path.basename(image)+' ('+data['fs']+')','',f"Image size: {fmt(data['file_size'])}",f"Used: {fmt(data['used_bytes'])}"]
            if data['fs']=='EROFS':lines+=[f"Wasted: {fmt(data['wasted_bytes'])}",f"Reclaimable: {fmt(data['reclaimable_bytes'])}"]
            else:lines+=[f"Free: {fmt(data.get('free_bytes',0))}"]
            lines+=['',f"{data['files']} files, {data['dirs']} folders, {data['links']} symlinks",f"Logical content: {fmt(data['logical_bytes'])}"]
            compact=data['fs']=='EROFS' and data['reclaimable_bytes']>=1024**2
            self.session.message('\n'.join(lines),'Space usage',confirm=(lambda:self._mutate('Compact '+os.path.basename(image),image,lambda:imgfs.compact_image(image))) if compact else None,accept='Compact' if compact else '',cancel_label='Later' if compact else '',error=False)
        operation(self.session,'Space usage '+os.path.basename(image),lambda:imgfs.usage_report(image),done)
    def close(self):self._closed=True;self._jobs.stop()
