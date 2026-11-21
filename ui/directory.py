# SPDX-License-Identifier: MPL-2.0
"""Native hierarchical directory data, sharing the original scan/entry rules."""
import os
import json
import time
from dataclasses import dataclass,field
from types import SimpleNamespace

from PySide6.QtCore import QObject,QAbstractItemModel,QModelIndex,Property,Signal,Slot,Qt,QFileSystemWatcher,QTimer
from PySide6.QtGui import QColor
from core.tree_entries import build_tree_entry,entry_metadata,fmt_size,IS_FOLDER_ROLE,SIZE_LABEL_ROLE,APKTOOL_LINK_ROLE
from core.directory_scan import scan_tree_directory
from services.async_jobs import AsyncJobs
from ui.file_icons import file_icon,ICONS

_TRANSPARENT=QColor('transparent')
_PATH_ROLE=int(Qt.UserRole)
_PHYSICAL_ROLE=_PATH_ROLE+20
_TARGET_ROLE=_PATH_ROLE+50
_EXISTS_ROLE=_PATH_ROLE+51
_BADGE_ROLE=_PATH_ROLE+1
_BRANCH_COLOR_ROLE=_PATH_ROLE+10
_UI_STABLE_ROLE=_PATH_ROLE+9


@dataclass(eq=False)
class DirectoryEntry:
    """Plain data consumed by the same builder as QTreeWidgetItem."""
    texts:list
    values:dict=field(default_factory=dict)
    icons:dict=field(default_factory=dict)
    colors:dict=field(default_factory=dict)
    children:list=field(default_factory=list)
    parent:object=None
    row:int=0
    loaded:bool=False
    loading:bool=False
    fingerprint:str=''
    thumbnail_key:object=None
    thumbnail_url:str=''
    scan_serial:int=0
    branch_lines:object=None
    last_sibling:bool=True
    indexes:dict=field(default_factory=dict)
    presentation:object=None
    view_items:object=None
    view_dummy:bool=False
    watch_path:str=''
    up:bool=False
    empty:bool=False
    item_flags:object=Qt.ItemIsEnabled|Qt.ItemIsSelectable
    indicator:int=0
    ShowIndicator=1
    DontShowIndicator=0
    def setData(self,column,role,value):self.values[column,int(role)]=value
    def data(self,column,role):return self.values.get((column,int(role)))
    def setIcon(self,column,value):self.icons[column]=value
    def setForeground(self,column,value):self.colors[column]=value
    def setFlags(self,value):self.item_flags=value
    def flags(self):return self.item_flags
    def setChildIndicatorPolicy(self,value):self.indicator=value
    @property
    def path(self):return self.values.get((0,_PATH_ROLE)) or ''
    @property
    def folder(self):return bool(self.values.get((0,IS_FOLDER_ROLE)))


def _metadata(base):
    from core.config_engine_pure import ConfigEngine
    if not os.path.isdir(base):raise FileNotFoundError(base)
    engine=ConfigEngine(base)
    return True,(engine,engine.load_full_state())


def _probe_magic(phys):
    from core.header_magic import check_magic
    t0=time.perf_counter()
    try:magic=check_magic(phys)
    except OSError:magic=''
    return True,(magic,(time.perf_counter()-t0)*1000.0)


class EntryPresentation(QObject):
    """Visual changes do not invalidate TreeView's flattened hierarchy."""
    changed=Signal()
    retiring=Signal()
    def __init__(self,node,parent):super().__init__(parent);self._node=node;self._alive=True
    @Property(bool,notify=retiring)
    def alive(self):return self._alive
    def retire(self):
        if not self._alive:return
        self._alive=False;self.retiring.emit();self.deleteLater()
    @Property(QObject,constant=True)
    def presentation(self):return self
    @Property(str,notify=changed)
    def name(self):return self._node.texts[0]
    @Property(str,notify=changed)
    def context(self):return self._node.texts[1]
    @Property(str,notify=changed)
    def permissions(self):return self._node.texts[2]
    @Property(str,notify=changed)
    def path(self):return self._node.path
    @Property(bool,notify=changed)
    def empty(self):return self._node.empty
    @Property(bool,notify=changed)
    def folder(self):return self._node.folder or bool(self._node.data(0,APKTOOL_LINK_ROLE))
    @Property(str,notify=changed)
    def iconKey(self):return self._node.icons.get(0,'file')
    @Property(str,notify=changed)
    def sizeLabel(self):return self._node.values.get((0,SIZE_LABEL_ROLE)) or ''
    @Property(str,notify=changed)
    def badge(self):return self._node.values.get((0,_BADGE_ROLE)) or ''
    @Property(str,notify=changed)
    def branchColor(self):return self._node.values.get((0,_BRANCH_COLOR_ROLE)) or ''
    @Property(QColor,notify=changed)
    def contextColor(self):return self._node.colors.get(1,_TRANSPARENT)
    @Property(bool,notify=changed)
    def symlinkExists(self):return bool(self._node.values.get((0,_EXISTS_ROLE)))
    @Property(str,notify=changed)
    def thumbnailUrl(self):return self._node.thumbnail_url
    @Property('QVariantList',notify=changed)
    def branchLines(self):return list(self._node.branch_lines or ())
    @Property(bool,notify=changed)
    def lastSibling(self):return self._node.last_sibling


class DirectoryModel(QAbstractItemModel):
    metadataChanged=Signal()
    changed=Signal()
    ready=Signal()
    failed=Signal(str)
    fileActivated=Signal(str)
    metadataEditRequested=Signal(str,int,str)
    directoryLoaded=Signal(str)
    pathRevealed=Signal(str)
    _ROLE=int(Qt.UserRole)+1000
    _FIELDS=('name','context','permissions','path','physicalPath','folder','iconKey','sizeLabel','badge',
             'branchColor','contextColor','symlinkTarget','symlinkExists','up','empty','loading','thumbnailUrl','branchLines','lastSibling','presentation','entry')
    NON_FS_BADGE={'SUPER','SPARSE','BOOT','VNDRBOOT','PAYLOAD','VBMETA',
                  'OZIP','OFP','OFP_QC','OFP_MTK','OFP_ZIP','OPS','KDZ','DZ',
                  'UPDATE_APP','PAC','CPB','AMLOGIC','ALLWINNER','ROMFS','SQUASHFS'}

    def __init__(self,parent=None,*,watch=True):
        super().__init__(parent)
        self.base='';self.engine=None;self.ctx={};self.ctx_neocat={};self.fs={}
        self.superdetect_cache={};self.show_apktool_dec=False
        self._unpack_watcher=SimpleNamespace(get_fs_type=lambda _: '')
        for attribute,key in ICONS.items():setattr(self,attribute,key)
        self._jobs=AsyncJobs(self,limit=2)
        self._magic_jobs=AsyncJobs(self,limit=2)
        self._magic_cache={}
        self._magic_pending=set()
        from ui.thumbnails import ThumbnailCache
        self._thumbnails=ThumbnailCache(self,self._thumbnail_ready)
        self._thumbnail_subscribers={};self._thumbnail_dirty=set()
        self._thumbnail_timer=QTimer(self);self._thumbnail_timer.setSingleShot(True);self._thumbnail_timer.setInterval(16)
        self._thumbnail_timer.timeout.connect(self._flush_thumbnails)
        self._generation=0;self._mode='single_dir';self._history=[];self._error=''
        self._draft=None
        self._rendering={}
        self._render_aborts=[]
        self.mounts=None
        self._draft_after=''
        self._reveal_serial=0
        self._root=DirectoryEntry(['','','']);self._nodes={}
        from ui.directory_view import DirectoryView
        self._view=DirectoryView(self)
        self._watch_enabled=watch
        self._watcher=QFileSystemWatcher(self)
        self._dirty=set()
        self._watcher.directoryChanged.connect(self._watch_changed)
        self._debounce=QTimer(self);self._debounce.setSingleShot(True);self._debounce.setInterval(120)
        self._debounce.timeout.connect(self._refresh_dirty)

    @Property(str,notify=changed)
    def location(self):return self._root.path
    @Property(QObject,constant=True)
    def viewModel(self):return self._view.enable()
    @Property(str,notify=changed)
    def basePath(self):return self.base
    @Property(str,notify=changed)
    def title(self):return os.path.basename(os.path.normpath(self.base)) if self.base else ''
    @Property(str,notify=changed)
    def error(self):return self._error
    @Property(bool,notify=changed)
    def busy(self):return self._jobs.busy() or bool(self._rendering)
    @Property(int,notify=changed)
    def count(self):return sum(not node.up and not node.empty for node in self._root.children)
    @Property(str,notify=changed)
    def displayMode(self):return self._mode
    @Property(bool,notify=changed)
    def canGoUp(self):return bool(self._history)

    def roleNames(self):return {self._ROLE+i:name.encode() for i,name in enumerate(self._FIELDS)}
    def columnCount(self,parent=QModelIndex()):return 3
    def rowCount(self,parent=QModelIndex()):
        if parent.isValid() and parent.column()!=0:return 0
        return len(self._node(parent).children)
    def _node(self,index):
        if not index.isValid():return self._root
        if index.model() is not self:
            from ui.directory_view import NODE_ROLE
            return self._view.nodes.get(index.data(NODE_ROLE)) or self._root
        return index.internalPointer()
    def index(self,row,column,parent=QModelIndex()):
        node=self._node(parent)
        if 0<=row<len(node.children) and 0<=column<3 and (not parent.isValid() or parent.column()==0):
            return self._index(node.children[row],column)
        return QModelIndex()
    def _index(self,node,column=0):
        if node is self._root:return QModelIndex()
        index=node.indexes.get(column)
        if index is None or index.row()!=node.row:
            index=self.createIndex(node.row,column,node);node.indexes[column]=index
        return index
    def parent(self,index):
        if not index.isValid():return QModelIndex()
        node=self._node(index).parent
        return self._index(node) if node is not None else QModelIndex()
    def hasChildren(self,parent=QModelIndex()):
        return parent.column()==0 and self._may_have_children(self._node(parent)) if parent.isValid() else bool(self.base)
    @staticmethod
    def _may_have_children(node):return not node.up and not node.empty and (node.folder or bool(node.data(0,APKTOOL_LINK_ROLE)))
    def canFetchMore(self,parent):
        node=self._node(parent)
        return bool(self.base and self._may_have_children(node) and not node.loaded and not node.loading)
    def fetchMore(self,parent):
        if self.canFetchMore(parent):self._scan(self._node(parent))
    def headerData(self,section,orientation,role=Qt.DisplayRole):
        if orientation==Qt.Horizontal and role==Qt.DisplayRole:
            return ('Name','Selinux','Permission')[section] if 0<=section<3 else None
    def flags(self,index):
        if not index.isValid():return Qt.NoItemFlags
        value=Qt.ItemIsSelectable|Qt.ItemIsEnabled
        node=self._node(index)
        ref=self.mounts.reference(node.path,resolve=False) if self.mounts else None
        if not node.up and not node.empty and (not ref or index.column()>0 and self.mounts.writable(ref[0])):value|=Qt.ItemIsEditable
        return value
    def data(self,index,role=Qt.DisplayRole):
        if not index.isValid():return None
        node=self._node(index)
        if role in (0,2):return node.texts[index.column()]
        if role==3:return node.path
        field=role-self._ROLE
        # QML asks for individual roles repeatedly during TreeView layout. Do
        # not rebuild every role (and QColor/path/enum values) for each request.
        if field==0:return node.texts[0]
        if field==1:return node.texts[1]
        if field==2:return node.texts[2]
        if field==3:return node.path
        if field==4:return node.values.get((0,_PHYSICAL_ROLE)) or node.values.get((0,_TARGET_ROLE)) or node.path
        if field==5:return node.folder or bool(node.data(0,APKTOOL_LINK_ROLE))
        if field==6:return node.icons.get(0,'file')
        if field==7:return node.values.get((0,SIZE_LABEL_ROLE)) or ''
        if field==8:return node.values.get((0,_BADGE_ROLE)) or ''
        if field==9:return node.values.get((0,_BRANCH_COLOR_ROLE)) or ''
        if field==10:return node.colors.get(1,_TRANSPARENT)
        if field==11:return node.values.get((0,_TARGET_ROLE)) or ''
        if field==12:return node.values.get((0,_EXISTS_ROLE))
        if field==13:return node.up
        if field==14:return node.empty
        if field==15:return node.loading
        if field==16:return node.thumbnail_url
        if field==17:return list(node.branch_lines or ())
        if field==18:return node.last_sibling
        if field in (19,20):
            return self._presentation_for(node)
        return None
    def setData(self,index,value,role=Qt.EditRole):
        if role!=Qt.EditRole or not index.isValid():return False
        node=self._node(index)
        self.metadataEditRequested.emit(node.path,index.column(),str(value))
        return True

    @Slot(str,result=QModelIndex)
    def indexForPath(self,path):
        node=self._nodes.get(path)
        return self._public_index(node) if node is not None else QModelIndex()

    @Slot(str,int,result=QModelIndex)
    def cellForPath(self,path,column):
        node=self._nodes.get(path)
        return self._public_index(node,column) if node is not None else QModelIndex()

    def _public_index(self,node,column=0):return self._view.index(node,column) if self._view.enabled else self._index(node,column)
    def _presentation_for(self,node):
        if node.presentation is None:node.presentation=EntryPresentation(node,self)
        return node.presentation
    @staticmethod
    def _new_view_dummy(parent):return DirectoryEntry(['Empty','',''],parent=parent,empty=True)

    @Slot(QModelIndex)
    def ensureLoaded(self,index):
        node=self._node(index)
        if self._may_have_children(node) and not node.loaded and not node.loading:self._scan(node)

    @Slot(str,result=str)
    def parentPath(self,path):
        node=self._nodes.get(path)
        return node.parent.path if node is not None and node.parent is not None else ''

    @Slot(QModelIndex,str,bool,result='QVariantMap')
    def dragTarget(self,index,source_path,expanded):
        node=self._node(index)
        ref=self.mounts.reference(node.path,resolve=False) if self.mounts else None
        if ref and ref[1]:return {'destination':node.path,'accepted':False}
        if node.up:
            return {'destination':self._history[-1].path if self._history else self.base}
        if node.empty:node=node.parent or self._root
        apk=bool(node.data(0,APKTOOL_LINK_ROLE))
        folder=node.folder and not node.data(0,_TARGET_ROLE) and not ref
        destination=node if apk or folder else node.parent or self._root
        result={'destination':node.data(0,APKTOOL_LINK_ROLE) if apk else destination.path}
        # Reference suppresses the whole target highlight for a same-directory
        # internal move. APK mirrors deliberately retain their own highlight.
        if not apk and source_path and os.path.normcase(os.path.dirname(source_path))==os.path.normcase(destination.path):
            # paintEvent falls back to drag_hover_item when its group is empty.
            if node.path and node is not self._root:result.update(first=node.path,last=node.path)
            return result
        rows=([node]+node.children if expanded else [node]) if apk or folder else list(destination.children)
        rows=[row for row in rows if not row.up and not row.empty and row is not self._root]
        if rows:result.update(first=rows[0].path,last=rows[-1].path)
        return result

    @Slot(str,bool,result=str)
    @Slot(str,bool,str,result=str)
    def beginDraft(self,parent_path,folder,after_path=''):
        self.cancelDraft()
        parent=self._nodes.get(parent_path)
        if parent is None:return ''
        node=DirectoryEntry(['','',''],parent=parent)
        path=os.path.join(parent_path,'.__rak_new_item__')
        node.setData(0,Qt.UserRole,path);node.setData(0,IS_FOLDER_ROLE,folder)
        node.setIcon(0,'folder' if folder else 'file')
        position=1 if parent.children and parent.children[0].up else 0
        after=self._nodes.get(after_path)
        if after is not None and after.parent is parent:position=after.row+1
        self.beginInsertRows(self._index(parent),position,position)
        parent.children.insert(position,node);self._nodes[path]=node;self._draft=node
        self._draft_after=after_path
        self._update_rows(parent);self.endInsertRows()
        self._view.insert(parent,position,node)
        return path

    @Slot()
    def cancelDraft(self):
        node=self._draft
        if node is None:return
        self._draft=None
        self._draft_after=''
        if self._nodes.get(node.path) is not node:return
        parent=node.parent
        self.beginRemoveRows(self._index(parent),node.row,node.row)
        parent.children.remove(node);self._nodes.pop(node.path,None)
        self._view.remove(parent,node.row)
        if node.presentation is not None:node.presentation.retire();node.presentation=None
        self._update_rows(parent);self.endRemoveRows()

    @Slot(str)
    def cancelDraftForPath(self,path):
        if self._draft is not None and self._draft.path==path:self.cancelDraft()

    @Slot(str)
    def load(self,base,*,shared_metadata=None):
        t0=time.perf_counter()
        base=os.path.abspath(base)
        self._generation+=1;generation=self._generation
        self._jobs.reset();self._magic_jobs.reset();self._magic_pending.clear()
        self._history=[];self._error='';self.engine=None;self.ctx={};self.fs={};self.ctx_neocat={};self.base=base
        self._clear_watches()
        self._reset_root(base)
        if not os.path.isdir(base):
            self._fail(str(FileNotFoundError(base)))
            return
        if shared_metadata is not None:
            engine,ctx,fs,neocat=shared_metadata
            self.engine=engine;self.ctx=ctx;self.fs=fs;self.ctx_neocat=neocat
            self.metadataChanged.emit()
        else:
            def loaded(ok,payload):
                if generation!=self._generation:return
                if not ok:self._fail(payload);return
                self.engine,state=payload
                self.ctx=state['ctx'];self.fs=state['fs'];self.ctx_neocat=state['neocat']
                self._apply_loaded_metadata(generation)
                self.metadataChanged.emit()
            self._jobs.submit('metadata',_metadata,base,loaded)
        self._scan(self._root)
        self.changed.emit()

    def _apply_loaded_metadata(self,generation):
        if generation!=self._generation or self.engine is None:return
        t0=time.perf_counter()
        updated=0
        for node in tuple(self._nodes.values()):
            if node is self._root or node.up or node.empty:continue
            _,ctx,perm,ctx_src=self.get_meta(node.path)
            if node.texts[1]!=ctx or node.texts[2]!=perm:
                node.texts[1]=ctx;node.texts[2]=perm
                node.colors.pop(1,None)
                self._apply_ctx_color(node,ctx_src)
                self._view.sync(node)
                if node.presentation is not None:node.presentation.changed.emit()
                row_idx=self._index(node,1)
                perm_idx=self._index(node,2)
                if row_idx.isValid() and perm_idx.isValid():
                    self.dataChanged.emit(row_idx,perm_idx,[])
                updated+=1
        cfg_dir=os.path.join(self.base,'config')
        if not self._history and self._root.loaded and not self._root.loading and os.path.isdir(cfg_dir) and cfg_dir not in self._nodes:
            self._scan(self._root,refresh=True)

    def _reset_root(self,path,*,template=None):
        self._jobs.reset();self._magic_jobs.reset();self._magic_pending.clear()
        self._rendering.clear()
        self.beginResetModel()
        if self._view.enabled:
            for node in self._nodes.values():
                node.view_items=None;node.view_dummy=False
                if node.presentation is not None:node.presentation.retire();node.presentation=None
            self._view.reset()
        root=DirectoryEntry([os.path.basename(path),'',''])
        if template:root.values=dict(template.values)
        root.setData(0,Qt.UserRole,path);root.setData(0,IS_FOLDER_ROLE,True)
        self._root=root;self._nodes={path:root}
        if self._history:
            up=DirectoryEntry(['..','',''],parent=root,up=True)
            up.setData(0,Qt.UserRole,'..')
            up.setData(0,IS_FOLDER_ROLE,True);up.setIcon(0,'project_ic')
            root.children.append(up)
        self._view.append(root,list(root.children))
        self.endResetModel();self.changed.emit()

    @Slot(str)
    def setDisplayMode(self,mode):
        mode='single_dir' if mode=='single_dir' else 'tree'
        if mode==self._mode:return
        self._mode=mode
        if self.base:
            self._history=[];self._reset_root(self.base);self._scan(self._root)
        self.changed.emit()

    @Slot(str)
    def activate(self,path):
        if path=='..' and self._history:self.goUp();return
        node=self._nodes.get(path)
        if node is None or node.empty:return
        if node.up:self.goUp();return
        if node.folder or node.data(0,APKTOOL_LINK_ROLE):
            if self._mode!='single_dir':return
            self._history.append(self._root)
            self._reset_root(node.path,template=node);self._scan(self._root)
        else:self.fileActivated.emit(node.path)

    @Slot()
    def goUp(self):
        if not self._history:return
        previous=self._history.pop()
        self._reset_root(previous.path,template=previous);self._scan(self._root)

    @Slot(str)
    def revealPath(self,path):
        self._reveal_serial+=1;serial=self._reveal_serial
        path=os.path.normpath(path)
        try:
            if os.path.commonpath([os.path.abspath(path),self.base])!=self.base:return
        except ValueError:return
        if self._mode=='single_dir':
            parent=os.path.dirname(path)
            self._history=[]
            cursor=self.base
            rel=os.path.relpath(parent,self.base)
            for part in ([] if rel=='.' else rel.split(os.sep)):
                history=DirectoryEntry([os.path.basename(cursor),'',''])
                history.setData(0,Qt.UserRole,cursor);history.setData(0,IS_FOLDER_ROLE,True)
                self._history.append(history);cursor=os.path.join(cursor,part)
            self._reset_root(parent)
            def loaded(location):
                if serial!=self._reveal_serial:
                    self.directoryLoaded.disconnect(loaded);return
                if location==parent:
                    self.directoryLoaded.disconnect(loaded);self.pathRevealed.emit(path)
            self.directoryLoaded.connect(loaded);self._scan(self._root)
        else:
            def reveal(_=''):
                if serial!=self._reveal_serial:
                    self.directoryLoaded.disconnect(reveal);return
                if path in self._nodes:
                    self.directoryLoaded.disconnect(reveal);self.pathRevealed.emit(path);return
                cursor=path
                while cursor not in self._nodes and os.path.dirname(cursor)!=cursor:cursor=os.path.dirname(cursor)
                node=self._nodes.get(cursor)
                if node and not node.loading and not node.loaded:self._scan(node)
            self.directoryLoaded.connect(reveal);reveal()

    @Slot()
    def refresh(self):
        if self.base:self._scan(self._root,refresh=True)

    @Slot()
    def reloadMetadata(self):
        if not self.base:return
        generation=self._generation
        def loaded(ok,payload):
            if generation!=self._generation:return
            if not ok:self._fail(payload);return
            self.engine,state=payload
            self.ctx=state['ctx'];self.fs=state['fs'];self.ctx_neocat=state['neocat']
            self.metadataChanged.emit()
            for node in tuple(self._nodes.values()):
                if node.loaded:self._scan(node,refresh=True)
        self._jobs.submit('metadata',_metadata,self.base,loaded)

    def _scan(self,node,*,refresh=False):
        ref=self.mounts.reference(node.path) if self.mounts else None
        if ref and ref[0]!='disk':self._scan_mount(node,ref);return
        t_scan=time.perf_counter()
        generation=self._generation
        node.scan_serial+=1;serial=node.scan_serial
        node.loading=True;self._error=''
        target=node.data(0,Qt.UserRole+50) or node.data(0,APKTOOL_LINK_ROLE)
        if ref and ref[0]=='disk':target=ref[1]
        spec=dict(base=self.base,path=node.path,branch=node.data(0,Qt.UserRole+20),target=target,
                  folder_symlink=bool(target),show_apktool_dec=self.show_apktool_dec,
                  refresh=refresh)
        def completed(ok,payload):
            if generation!=self._generation or serial!=node.scan_serial:return
            # A single-directory navigation can invalidate a still-running
            # scan without changing the project generation.
            if self._nodes.get(node.path) is not node:return
            if not ok:node.loading=False;self._fail(payload);return
            def applied():
                node.loading=False;node.loaded=True
                node.watch_path=payload['physical']
                if self._watch_enabled:
                    watch=payload['physical']
                    if os.path.isdir(watch) and watch not in self._watcher.directories():self._watcher.addPath(watch)
                self.changed.emit()
                self.directoryLoaded.emit(node.path)
                if node is self._root:
                    cfg_dir=os.path.join(self.base,'config')
                    if not self._history and self.engine is not None and os.path.isdir(cfg_dir) and cfg_dir not in self._nodes:
                        self._scan(self._root,refresh=True);return
                    self.ready.emit()
            self._reconcile(node,payload,applied)
        self._jobs.submit(node.path,scan_tree_directory,spec,completed,priority=True)
        self.changed.emit()

    def _reconcile(self,parent,payload,completed):
        desired=[]
        if parent.children and parent.children[0].up:desired.append(parent.children[0])
        generation=self._generation;serial=parent.scan_serial
        self._rendering[parent.path]=serial
        initial=all(child.up for child in parent.children)
        entries=iter(payload['entries'])
        magic_candidates=[]
        def build(entry):
            row=build_tree_entry(self,(entry['priority'],entry['name'],entry['path'],entry['is_dir'],entry['symlink'],entry['source'],entry['branch']),
                payload['is_root'],payload['branch_only'],None,{entry['source']:fmt_size(entry['size'] or 0)},
                entry_info=entry,item_factory=DirectoryEntry)
            if row is None:return None
            row.fingerprint=entry['fingerprint']
            if self.mounts and self.mounts.reference(row.path,resolve=False):
                row.setData(0,IS_FOLDER_ROLE,True);row.setIcon(0,'drive_mounted')
            if payload['is_root']:row.setData(0,Qt.UserRole+10,payload['colors'].get(entry['name']))
            low_name=entry['name'].lower()
            if not entry['is_dir'] and not entry.get('symlink') and low_name.endswith(('.img','.bin','.ozip','.ofp','.ops','.kdz','.dz','.app','.pac','.cpb','.romfs','.squashfs','.sqsh')):
                phys=entry.get('branch') or entry['path']
                cache_key=(phys,entry.get('mtime_ns'),entry.get('size'))
                if cache_key in self._magic_cache:
                    cached_badge=self._magic_cache[cache_key]
                    if cached_badge:self._apply_badge_to_node(row,cached_badge)
                elif not entry['badge']:
                    magic_candidates.append((row.path,phys,low_name,cache_key))
            if initial and self._view.enabled:
                row.parent=parent;row.row=len(desired)
                self._view.items(row)
            return row
        def valid():
            return generation==self._generation and parent.scan_serial==serial and self._nodes.get(parent.path) is parent
        def finish_rows():
            if self._draft is not None and self._draft.parent is parent:
                position=1 if desired and desired[0].up else 0
                for number,node in enumerate(desired):
                    if node.path==self._draft_after:position=number+1;break
                desired.insert(position,self._draft)
            if not payload['entries']:desired.append(DirectoryEntry(['Empty','',''],empty=True))
            if initial:
                if not payload['entries']:self._append_rows(parent,[desired[-1]])
                return iter(())
            return self._reconcile_rows_iter(parent,desired)
        applying=None
        pending_rows=[]
        def pump():
            nonlocal applying
            if not valid():
                self._view.discard_prepared(desired)
                self._render_aborts.append((parent.path,generation,self._generation,serial,parent.scan_serial,self._nodes.get(parent.path) is parent))
                del self._render_aborts[:-8]
                if self._rendering.get(parent.path)==serial:self._rendering.pop(parent.path,None)
                return
            try:
                deadline=time.perf_counter()+.008;processed=0;append=[]
                while processed<256 and time.perf_counter()<deadline:
                    if applying is not None:
                        try:next(applying)
                        except StopIteration:
                            self._rendering.pop(parent.path,None);completed()
                            self._schedule_magic_scans(magic_candidates,generation,is_root=payload['is_root'])
                            return
                    else:
                        try:entry=next(entries)
                        except StopIteration:
                            if initial:
                                pending_rows.extend(append);append=[]
                                if pending_rows:self._append_rows(parent,pending_rows);pending_rows.clear()
                            applying=finish_rows();continue
                        row=build(entry)
                        if row is not None:
                            desired.append(row)
                            if initial:append.append(row)
                    processed+=1
                if append:
                    pending_rows.extend(append)
                    # TreeView's flattened proxy revisits the hierarchy for
                    # each insertion. Coalesce prepared rows, preserving the
                    # first visible batch and the 8ms construction budget.
                    batch_size=1024 if self._view.enabled else 256
                    if len(pending_rows)>=batch_size or all(child.up for child in parent.children):
                        self._append_rows(parent,pending_rows);pending_rows.clear()
                QTimer.singleShot(0,self,pump)
            except Exception as error:
                self._view.discard_prepared(desired)
                self._rendering.pop(parent.path,None);parent.loading=False;self._fail(str(error))
        pump()

    def _append_rows(self,parent,rows):
        first=len(parent.children)
        self.beginInsertRows(self._index(parent),first,first+len(rows)-1)
        for number,node in enumerate(rows,first):
            node.parent=parent;node.row=number;parent.children.append(node)
            if not node.empty:self._nodes[node.path]=node
        self.endInsertRows()
        self._refresh_branches(parent,parent.children[max(0,first-1):])
        self._view.append(parent,rows)

    def _scan_mount(self,parent,ref):
        image,internal,children=self.mounts.children(*ref)
        desired=[parent.children[0]] if parent.children and parent.children[0].up else []
        registry=self.mounts.registry(self.mounts._image_base[image]) if children else None
        prepared=[]
        for child in children:
            is_dir=bool(child['is_dir'])
            link_info=None
            if child.get('is_link'):
                virtual,physical,is_dir,exists=registry._resolve_symlink_target_for_mount(image,child['path'],child.get('target') or '')
                link_info=(bool(is_dir),exists,child.get('target') or '')
            priority=-1 if not internal and child['name']=='config' and is_dir else 0 if is_dir else 1
            prepared.append((priority,child['name'].lower(),child,bool(is_dir),link_info))
        prepared.sort(key=lambda item:(item[0],item[1]))
        for _,_,child,is_dir,link_info in prepared:
            path='mount://'+image+'//'+child['path'].strip('/')
            row=DirectoryEntry([child['name'],child.get('selinux') or '',(child.get('perm') or '')[-9:]])
            row.setData(0,Qt.UserRole,path);row.setData(0,IS_FOLDER_ROLE,is_dir)
            row.setData(0,SIZE_LABEL_ROLE,fmt_size(child.get('size') or 0))
            if link_info is not None:
                _,exists,target=link_info
                row.setData(0,Qt.UserRole+51,exists)
                row.setData(0,Qt.UserRole+53,target)
                row.setIcon(0,'symlink' if is_dir else 'symlink_file')
            else:
                row.setIcon(0,'folder' if is_dir else file_icon(child['name']))
                if not is_dir:
                    row.thumbnail_key,row.thumbnail_url=self._thumbnails.request(child['path'],mounted=(image,child['path']))
                    if row.thumbnail_key:self._thumbnail_subscribers.setdefault(row.thumbnail_key,set()).add(row.path)
            row.fingerprint=repr(child);desired.append(row)
        if not children:desired.append(DirectoryEntry(['Empty','',''],empty=True))
        self._reconcile_rows(parent,desired);parent.loaded=True;parent.loading=False
        self.changed.emit();self.directoryLoaded.emit(parent.path)

    def _reconcile_rows(self,parent,desired):
        for _ in self._reconcile_rows_iter(parent,desired):pass

    def _reconcile_rows_iter(self,parent,desired):
        existing={node.path:node for node in parent.children if not node.up}
        wanted={node.path for node in desired if not node.up}
        parent_index=self._index(parent)
        for number in range(len(parent.children)-1,-1,-1):
            node=parent.children[number]
            if not node.up and node.path not in wanted:
                self.beginRemoveRows(parent_index,number,number)
                parent.children.pop(number);self._drop_node(node)
                self._view.remove(parent,number)
                self._update_rows(parent)
                self.endRemoveRows()
                yield
        for number,fresh in enumerate(desired):
            node=existing.get(fresh.path) if not fresh.up else fresh
            if node is None:
                node=fresh;node.parent=parent
                self.beginInsertRows(parent_index,number,number);parent.children.insert(number,node)
                if not node.empty:self._nodes[node.path]=node
                self._update_rows(parent);self.endInsertRows()
                self._view.insert(parent,number,node)
            else:
                current=node.row
                if current!=number:
                    self.beginMoveRows(parent_index,current,current,parent_index,number)
                    parent.children.insert(number,parent.children.pop(current));self._update_rows(parent);self.endMoveRows()
                    self._view.move(parent,current,number)
                if not node.up and (node.fingerprint!=fresh.fingerprint or node.texts!=fresh.texts or node.values!=fresh.values or node.icons!=fresh.icons or node.thumbnail_url!=fresh.thumbnail_url):
                    if self._may_have_children(node) and not self._may_have_children(fresh):
                        had_children=bool(node.children)
                        if had_children:self.beginRemoveRows(self._index(node),0,len(node.children)-1)
                        for child in node.children:self._drop_node(child)
                        self._view.clear_children(node)
                        node.children=[];node.loaded=False
                        if had_children:self.endRemoveRows()
                    node.texts=fresh.texts;node.values=fresh.values;node.icons=fresh.icons;node.colors=fresh.colors;node.fingerprint=fresh.fingerprint
                    node.thumbnail_key=fresh.thumbnail_key;node.thumbnail_url=fresh.thumbnail_url
                    self._view.sync(node)
                    if node.presentation is not None:node.presentation.changed.emit()
                    self.dataChanged.emit(self.createIndex(number,0,node),self.createIndex(number,2,node),[])
            node.row=number
            yield
        self._update_rows(parent)
        self._refresh_branches(parent)

    def _refresh_branches(self,parent,nodes=None):
        pending=list(parent.children if nodes is None else nodes)
        while pending:
            node=pending.pop();ancestors=[];ancestor=node.parent
            while ancestor is not None and ancestor is not self._root:
                ancestors.append(ancestor.row<len(ancestor.parent.children)-1);ancestor=ancestor.parent
            lines=tuple(reversed(ancestors));last=node.row==len(node.parent.children)-1
            changed=node.branch_lines!=lines or node.last_sibling!=last
            node.branch_lines=lines;node.last_sibling=last
            if changed and node.presentation is not None:node.presentation.changed.emit()
            pending.extend(node.children)

    @staticmethod
    def _update_rows(parent):
        for number,node in enumerate(parent.children):node.row=number

    def _drop_node(self,node):
        if node.presentation is not None:node.presentation.retire();node.presentation=None
        if self._draft is node:self._draft=None
        self._nodes.pop(node.path,None)
        if node.watch_path in self._watcher.directories():self._watcher.removePath(node.watch_path)
        for child in node.children:self._drop_node(child)

    def _fail(self,error):
        self._error=str(error);self.changed.emit();self.failed.emit(self._error)

    def get_meta(self,path):return entry_metadata(self,path)
    @staticmethod
    def _apply_ctx_color(row,source):
        if source=='neocat':row.setForeground(1,QColor(140,220,140))
    def _resolve_file_icon(self,path):return file_icon(path)
    def _resolve_folder_icon(self,path,fs_type):
        if fs_type:return 'fs_folder'
        if os.path.normpath(path)==os.path.normpath(self.base):return 'project_ic'
        if os.path.dirname(os.path.normpath(path))==os.path.normpath(self.base):
            name=os.path.basename(path)
            if name in ('config','apktool_dec'):return 'special_folder'
            if name=='neocat_config':return 'folder_neocat'
        return 'folder'
    def _schedule_file_thumbnail(self,row,path,*,entry_info=None):
        row.thumbnail_key,row.thumbnail_url=self._thumbnails.request(path,info=entry_info)
        if row.thumbnail_key:self._thumbnail_subscribers.setdefault(row.thumbnail_key,set()).add(row.path)

    def _thumbnail_ready(self,key,url):
        for path in self._thumbnail_subscribers.pop(key,()):
            node=self._nodes.get(path)
            if node is not None and node.thumbnail_key==key:
                node.thumbnail_url=url
                self._thumbnail_dirty.add(path)
        if self._thumbnail_dirty and not self._thumbnail_timer.isActive():self._thumbnail_timer.start()

    def _flush_thumbnails(self):
        if self._rendering:self._thumbnail_timer.start();return
        for path in self._thumbnail_dirty:
            node=self._nodes.get(path)
            if node is not None and node.presentation is not None:node.presentation.changed.emit()
        self._thumbnail_dirty.clear()
    def is_apk_folder_mode(self,path):
        from core.apktool_tool import APKTOOL_DEC_DIR
        try:
            with open(os.path.join(self.base,APKTOOL_DEC_DIR,'.view_modes.json'),encoding='utf-8') as source:
                modes=json.load(source)
            key=os.path.relpath(path,self.base).replace('\\','/')
            return not isinstance(modes,dict) or modes.get(key,'folder')!='apk'
        except (OSError,ValueError,TypeError):return True

    def set_apk_view_mode(self,path,mode):
        from core.apktool_tool import APKTOOL_DEC_DIR
        target=os.path.join(self.base,APKTOOL_DEC_DIR,'.view_modes.json')
        try:
            with open(target,encoding='utf-8') as source:modes=json.load(source)
            if not isinstance(modes,dict):modes={}
        except (OSError,ValueError):modes={}
        modes[os.path.relpath(path,self.base).replace('\\','/')]=mode
        temporary=target+'.tmp'
        with open(temporary,'w',encoding='utf-8') as output:json.dump(modes,output,indent=2)
        os.replace(temporary,target)

    def _watch_changed(self,path):self._dirty.add(path);self._debounce.start()
    def _refresh_dirty(self):
        dirty=self._dirty;self._dirty=set()
        for node in tuple(self._nodes.values()):
            if node.watch_path in dirty and node.loaded:self._scan(node,refresh=True)
    def _clear_watches(self):
        self._debounce.stop();self._dirty.clear()
        paths=self._watcher.directories()
        if paths:self._watcher.removePaths(paths)
    def close(self):
        self._thumbnail_timer.stop();self._thumbnail_dirty.clear();self._thumbnail_subscribers.clear()
        self._generation+=1;self._rendering.clear();self._jobs.stop();self._magic_jobs.stop();self._magic_pending.clear();self._thumbnails.close();self._clear_watches()

    def _apply_badge_to_node(self,node,badge):
        if badge in ('EROFS','EXT4','EXT3','EXT2','F2FS','PAYLOAD'):
            node.setData(0,_BADGE_ROLE,badge)
        elif badge in self.NON_FS_BADGE or badge in ('BR','DAT','DAT.BR'):
            node.setData(0,_BADGE_ROLE,badge)
            node.setData(0,_UI_STABLE_ROLE,True)
        else:
            node.values.pop((0,_BADGE_ROLE),None)
            node.values.pop((0,_UI_STABLE_ROLE),None)

    def _schedule_magic_scans(self,candidates,generation,*,is_root=False):
        if not candidates or generation!=self._generation:return
        for node_path,phys,low_name,cache_key in candidates:
            pending_key=(generation,node_path,cache_key)
            if pending_key in self._magic_pending:continue
            self._magic_pending.add(pending_key)
            def done(ok,payload,node_path=node_path,low_name=low_name,cache_key=cache_key,pending_key=pending_key):
                self._magic_pending.discard(pending_key)
                if generation!=self._generation or not ok:return
                magic,elapsed_ms=payload
                badge=magic if low_name.endswith(('.img','.ozip','.ofp','.ops','.kdz','.dz','.app','.pac','.cpb','.romfs','.squashfs','.sqsh')) or (low_name.endswith('.bin') and magic in ('PAYLOAD','AMLOGIC','ALLWINNER','OFP','OFP_QC','OFP_MTK','OFP_ZIP','ROMFS','SQUASHFS')) else ''
                self._magic_cache[cache_key]=badge
                node=self._nodes.get(node_path)
                if node is not None and (node.values.get((0,_BADGE_ROLE)) or '')!=badge:
                    self._apply_badge_to_node(node,badge)
                    self._view.sync(node)
                    if node.presentation is not None:node.presentation.changed.emit()
                    idx=self._index(node,0)
                    if idx.isValid():self.dataChanged.emit(idx,idx,[])
            self._magic_jobs.submit(f'magic:{node_path}',_probe_magic,phys,done,priority=is_root,report_operation_errors=False)
