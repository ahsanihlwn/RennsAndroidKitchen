# SPDX-License-Identifier: MPL-2.0
"""Native workspace state. Files, documents and PTYs outlive their delegates."""
from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import time
import weakref

from PySide6.QtCore import QObject, Property, Signal, Slot, QTimer, QUrl, Qt
from PySide6.QtGui import QDesktopServices,QGuiApplication

from core import ui_lang
from core.project_config import resolve_tree_target
from main_ext.class_ThemeUi import theme
from services.async_jobs import AsyncJobs
from ui.directory import DirectoryModel
from ui.editor import EditorSession
from ui.file_icons import file_icon
from ui.rows import RowsModel
from ui.terminal import TerminalSession


def default_project_settings():
    return {"erofs": {"default": False, "method": "lz4hc", "level": 1},
            "brotli": {"default": True, "level": 1},
            "selinux": {"enabled": False, "ai": False, "plain": True},
            "super": {"norm": True},
            "agent": {"autosave":True,"highlight":True,"ui_triggers":False}}


class WorkspaceSession(QObject):
    changed = Signal()
    tabsChanged = Signal()
    selectionChanged = Signal()
    focusChanged = Signal(str)
    terminalsChanged = Signal()
    settingsChanged = Signal()
    statusChanged = Signal()
    locksChanged = Signal()
    highlightsChanged = Signal()
    unpackPreviewReady = Signal(str, str, str, object)
    ready = Signal()
    preparation_status = Signal(str)
    preparation_failed = Signal(str)
    presentationRequested = Signal(str, "QVariantMap")
    fatalRequested = Signal(str)
    operationErrorRequested = Signal(str)
    returnRequested = Signal()
    quitRequested = Signal()
    entryFinished = Signal()
    editCellRequested = Signal(int,str,int)
    _externalFsSync = Signal(str, object)

    def __init__(self, project, p1, p2=None, *, preferences, settings_path=None,
                 terminal_factory=TerminalSession, profile_provider=None, enable_private_services=True, parent=None):
        super().__init__(parent)
        self.project = os.path.abspath(project)
        self.p1, self.p2 = p1, p2
        self.preferences = preferences
        self.tree = DirectoryModel(self)
        self.tree2 = DirectoryModel(self)
        self.tree_mirror = DirectoryModel(self)
        self.active_tree = self.tree
        self._active_tree_id = 0
        self._selection = []
        self._focus_area='workspace'
        self._context_focus_area='tree0'
        self._name_column_width=270
        self._permission_column_width=95
        self._context_menu_active=False
        self._layout = 2 if p2 else 1
        self._restore_layout = 0
        self._editor = EditorSession(self)
        self._tabs = RowsModel(parent=self)
        self._tab_data = []
        self._active_tab = ""
        self._fs_watchers = {}
        self._terminal_sync_timers = {}
        self._externalFsSync.connect(self._apply_external_fs_sync, Qt.QueuedConnection)
        self._terminal_factory = terminal_factory
        self._terminals = []
        self._terminal_rows = RowsModel(parent=self)
        self._terminal_views = RowsModel(parent=self)
        self._terminal_open = False
        self._terminal_root = "tree1"
        self._terminal_active = {}
        self._terminal_indices = {"tree1": 0, "tree2": 0, "linux": 0}
        self._process_terminal_key=None
        from core.terminal_execution import register as register_terminal_execution
        self._terminal_publisher=register_terminal_execution(self)
        self._file_run_terminals={}
        self._linux_source_root='tree1'
        self._wsl_probe_time=None
        self._wsl_probe_running=False
        self._settings_path = settings_path
        self._settings = self._read_settings()
        self._pending_confirmation = None
        self._pending_confirmation_cancel = None
        self._pending_message_dismiss = None
        self._fatal_pending = []
        self._fatal_active = set()
        self._fatal_visible = False
        self.fatalRequested.connect(self._present_fatal, Qt.QueuedConnection)
        self.operationErrorRequested.connect(self._present_operation_error, Qt.QueuedConnection)
        self._jobs = AsyncJobs(self, limit=2)
        self._status = ""
        self._status_state = "idle"
        self._status_progress = -1
        self._status_stage = ""
        self._locked_paths = {}
        self._path_highlights = {}
        self._highlight_seq = 0
        self.unpackPreviewReady.connect(self._on_unpack_preview_ready, Qt.QueuedConnection)
        self._history = []
        self._started = False
        self._closing = False
        self._ready = False
        self._preparation_error = False
        self._loaded_trees = set()
        self._started_at = None
        self._clipboard = None
        from ui.search import WorkspaceSearch
        self._search=WorkspaceSearch(self)
        self._search.selected.connect(lambda path:self.active_tree.revealPath(path))
        self._operation_serial = 0
        from ui.workspace_tasks import WorkspaceTasks
        from ui.source_recovery import SourceRecovery
        self._source_recovery = SourceRecovery([resolve_tree_target(self.project,name) for name in (self.p1,self.p2) if name],self)
        self._source_recovery.progress.connect(self.preparation_status)
        self._source_recovery.finished.connect(self._sources_prepared)
        self._tasks=WorkspaceTasks(self,recovery=self._source_recovery)
        self.task_queue=self._tasks.queue
        self._tasks.changed.connect(self._task_state_changed)
        from ui.workspace_branches import WorkspaceBranches
        self._branches=WorkspaceBranches(self)
        from ui.android_tools import AndroidTools
        self._android_tools=AndroidTools(self,self)
        from ui.boot_analyzer import BootAnalyzerController
        self._boot_analyzer=BootAnalyzerController(self,self)
        from ui.toolkit import WorkspaceToolkit
        self._toolkit=WorkspaceToolkit(self)
        from ui.flashable import FlashableBuilder
        self._flashable=FlashableBuilder(self,self)
        from ui.pack_super import PackSuperController
        self._pack_super=PackSuperController(self,self)
        from ui.transfers import WorkspaceTransfers
        self._transfers=WorkspaceTransfers(self)
        from ui.partition_picker import PartitionPicker
        self._partition_picker=PartitionPicker(self)
        from ui.file_picker import ImportFilePicker
        self._import_picker=ImportFilePicker(self)
        from ui.private_workspace import PrivateWorkspace
        self._private=PrivateWorkspace(self,profile_provider)
        from ui.mounts import WorkspaceMounts
        self._mounts=WorkspaceMounts(self)
        if enable_private_services:self.ready.connect(self._private.start)
        self._editor.warningRaised.connect(lambda title, text: self.message(text, title))
        bridge = self._editor.bridge
        bridge.editorReady.connect(self._check_ready)
        bridge.fileUnsaved.connect(self._refresh_tabs)
        bridge.fileSaved.connect(self._refresh_tabs)
        bridge.fileSaved.connect(self._on_editor_file_saved)
        bridge.tabClosed.connect(self._tab_closed)
        for number, directory in enumerate(self.directories):
            directory.ready.connect(lambda number=number: self._tree_ready(number))
            directory.failed.connect(self._tree_failed)
            directory.fileActivated.connect(self.openPath)
            directory.metadataEditRequested.connect(
                lambda path, column, value, directory=directory: self.editMetadata(directory, path, column, value))
        preferences.settingsChanged.connect(self._preferences_changed)
        preferences.colorsChanged.connect(self._theme_changed)
        from ui.agent import WorkspaceAgent
        self._agent=WorkspaceAgent(self)
        bridge.sendToAgentRequested.connect(lambda:self._agent.chat.show('editor'))
        self._agent.chat.changed.connect(lambda:bridge.set_agent_available(self._agent.chat.available))

    @property
    def directories(self):
        return (self.tree, self.tree2, self.tree_mirror)

    @Property(QObject, constant=True)
    def treeModel(self): return self.tree
    @Property(QObject,constant=True)
    def search(self):return self._search
    @Property(QObject,constant=True)
    def branches(self):return self._branches
    @Property(QObject,constant=True)
    def androidTools(self):return self._android_tools
    @Property(QObject,constant=True)
    def bootAnalyzer(self):return self._boot_analyzer
    @Property(QObject,constant=True)
    def toolkit(self):return self._toolkit
    @Property(QObject,constant=True)
    def flashableBuilder(self):return self._flashable
    @Property(QObject,constant=True)
    def packSuper(self):return self._pack_super
    @Property(QObject,constant=True)
    def transfers(self):return self._transfers
    @Property(QObject,constant=True)
    def partitionPicker(self):return self._partition_picker
    @Property(QObject,constant=True)
    def importPicker(self):return self._import_picker
    @Property(QObject,constant=True)
    def privateFeatures(self):return self._private
    @Property(QObject,constant=True)
    def mounts(self):return self._mounts
    @Property(QObject,constant=True)
    def agentInbox(self):return self._agent.inbox
    @Property(QObject,constant=True)
    def agentChat(self):return self._agent.chat
    @Property(QObject,constant=True)
    def agentActivity(self):return self._agent.activity
    @Property(QObject,constant=True)
    def agentControls(self):return self._agent.controls
    @Property('QVariantMap',constant=True)
    def symlinkPresets(self):
        from core.make_symlink import ANDROID_ROOT_SYMLINKS,TOYBOX_SYMLINKS
        return {kind:[{'name':name,'target':target} for name,target in values]
                for kind,values in (('android',ANDROID_ROOT_SYMLINKS),('toybox',TOYBOX_SYMLINKS))}
    @Property(QObject, constant=True)
    def secondTreeModel(self): return self.tree2 if self.p2 else self.tree_mirror
    @Property(QObject, constant=True)
    def editor(self): return self._editor
    @Property(QObject, constant=True)
    def tabs(self): return self._tabs
    @Property(QObject, constant=True)
    def terminalRows(self): return self._terminal_rows
    @Property(int, notify=changed)
    def layoutMode(self): return self._layout
    @Property(str, notify=changed)
    def layoutLabel(self):
        return {
            0: "Layout: Hide Trees" if self.p2 else "Layout: Hide Tree",
            1: "Layout: Single Tree",
            2: "Layout: Side-by-Side Split",
            3: "Layout: Top-Bottom Split",
            4: "Layout: Swapped Split (Tree 2 | Tree 1)",
        }.get(self._layout, "Layout: Side-by-Side Split")
    @Property(int, notify=changed)
    def activeTreeId(self): return self._active_tree_id
    @Property(float,notify=changed)
    def nameColumnWidth(self):return self._name_column_width
    @Property(float,notify=changed)
    def permissionColumnWidth(self):return self._permission_column_width
    @Property(bool,notify=changed)
    def contextMenuActive(self):return self._context_menu_active

    @Slot(int,float)
    def resizeColumn(self,column,width):
        if column==0:self._name_column_width=max(70,round(width))
        elif column==2:self._permission_column_width=max(50,round(width))
        self.changed.emit()

    @Slot(bool)
    def setContextMenuActive(self,active):
        self._context_menu_active=active;self.changed.emit()

    @Slot('QVariantList',str,result='QVariantMap')
    def contextMenuMetrics(self,rows,kind):
        from ui.menu_metrics import menu_metrics
        return menu_metrics(rows,kind)

    @Slot(int,'QVariantList',result='QVariantMap')
    def treeDragMimeData(self,tree_id,paths):
        directory=self.tree if tree_id==0 else self.secondTreeModel
        refs=[self.mounts.reference(path,resolve=False) for path in paths]
        if refs and all(ref and ref[1] for ref in refs):
            return {'application/x-rdt-mount':json.dumps([(ref[0],ref[1],os.path.basename(ref[1])) for ref in refs])}
        urls=[self.physicalUrl(path) for path,ref in zip(paths,refs) if not (ref and ref[1])]
        normal=[path for path,ref in zip(paths,refs) if not (ref and ref[1])]
        if not normal:return {}
        return {'application/x-rak-tree':json.dumps({'base':directory.base,'paths':normal}),
                'text/uri-list':'\r\n'.join(urls)}

    @Slot(str,str,str,bool,str,int,int,result="QVariantMap")
    @Slot(str,str,str,bool,str,int,int,bool,result="QVariantMap")
    def entryLayout(self,name,size,badge,symlink,branch,width,origin,gki=False):
        from ui.tree_entry_layout import entry_layout
        return entry_layout(name,size,badge,symlink,branch,width,origin,gki)

    @Slot(str,str,result=QUrl)
    def fileIconUrl(self,key,palette):
        from icons.handler import icon
        from main_ext.class_ThemeUi import _PALETTES
        return QUrl.fromLocalFile(icon(key,light=_PALETTES.get(palette,{}).get('_is_light',False)))

    @Slot(str,"QVariantMap",result="QVariantMap")
    def badgeColors(self,badge,colors):
        from ui.tree_entry_layout import badge_colors
        from PySide6.QtGui import QColor
        return badge_colors(badge,colors['accent_hex'],colors.get('_gauge_warn','#e5a03c'),colors.get('_gauge_safe',colors['accent_hex']),
                            colors.get('_gauge_danger','#e06c75'),QColor(colors['BG']).lightnessF()>.7)
    @Property(bool, notify=tabsChanged)
    def hasTabs(self): return bool(self._tab_data)
    @Property(str, notify=tabsChanged)
    def activeTab(self): return self._active_tab
    @Property(str, notify=tabsChanged)
    def activeKind(self):
        tab = self._find_tab(self._active_tab)
        return tab["kind"] if tab else "editor"
    @Property("QVariantMap", notify=tabsChanged)
    def activeDetails(self):
        tab = self._find_tab(self._active_tab)
        return dict(tab) if tab else {}
    @Property(bool,notify=tabsChanged)
    def activeRunnable(self):
        from main_ext.file_run_commands import is_runnable_file
        return self.activeKind=='editor' and is_runnable_file(self.activeDetails.get('documentPath',''))
    @Property(bool, notify=terminalsChanged)
    def terminalOpen(self): return self._terminal_open
    @Property(bool, notify=changed)
    def rightOpen(self): return bool(self._tab_data or self._terminal_open)
    @Property(str, notify=terminalsChanged)
    def terminalRoot(self): return self._terminal_root
    @Property(str, notify=terminalsChanged)
    def terminalRootCaption(self):
        return "Linux" if self._terminal_root == "linux" else (self.p2 or "") if self._terminal_root == "tree2" else self.p1
    @Property("QVariantList", notify=changed)
    def terminalRoots(self):
        result = [{"value": "tree1", "label": self.p1}]
        if self.p2: result.append({"value": "tree2", "label": self.p2})
        if getattr(self, "_wsl_available", False) or any(row['root']=='linux' for row in self._terminals): result.append({"value": "linux", "label": "Linux"})
        return result
    @Property(QObject, constant=True)
    def terminalSessions(self):
        return self._terminal_views
    @Property(str, notify=terminalsChanged)
    def activeTerminal(self): return self._terminal_active.get(self._terminal_root, "")
    @Property("QVariantMap", notify=settingsChanged)
    def projectSettings(self): return deepcopy(self._settings)
    @Property(str, notify=statusChanged)
    def status(self): return self._status
    @Property(str, notify=statusChanged)
    def statusState(self): return self._status_state
    @Property(int, notify=statusChanged)
    def statusProgress(self): return self._status_progress
    @Property(str, notify=statusChanged)
    def statusStage(self): return self._status_stage
    @Property("QVariantList", notify=statusChanged)
    def history(self): return list(self._history)
    @Property('QVariantList', notify=statusChanged)
    def pendingLabels(self): return list(self._tasks.pending)
    @Property('QVariantList', notify=statusChanged)
    def historyLines(self): return list(self._tasks.history_lines)
    @Property('QVariantList', notify=statusChanged)
    def taskHistory(self): return self._tasks.loading.get_history()
    @Property('QVariantList', notify=statusChanged)
    def taskActivity(self): return self._tasks.loading.get_activity()
    @property
    def _operation_count(self): return self._tasks.count

    def _task_state_changed(self):
        self._status=self._tasks.text
        self._status_state=self._tasks.state
        self._status_progress=self._tasks.progress
        self._status_stage=self._tasks.stage
        self.statusChanged.emit()

    @staticmethod
    def _norm_key(path):
        if not path or str(path).startswith('mount://'):
            return ''
        return os.path.normcase(os.path.normpath(str(path)))

    @Slot(str, result=bool)
    def isPathLocked(self, path):
        key = self._norm_key(path)
        if not key or not self._locked_paths:
            return False
        for locked_key, count in tuple(self._locked_paths.items()):
            if count <= 0:
                continue
            if key == locked_key or key.startswith(locked_key + os.sep):
                return True
        return False

    def _is_source_read_only(self, path):
        target = str(path or '')
        if not target:
            return False
        if target.startswith('mount://'):
            ref = self._mounts.reference(target, resolve=False)
            return bool(ref and not self._mounts.writable(ref[0]))
        if self._mounts.unpack_preview_reference(target, resolve=False):
            return True
        if self.isPathLocked(target):
            return True
        norm = os.path.normpath(target)
        return os.path.isfile(norm) and not os.access(norm, os.W_OK)

    @Slot(str, result=bool)
    def isFileReadOnly(self, path):
        target = str(path or '')
        if not target:
            return False
        if self._is_source_read_only(target):
            return True
        norm = os.path.normpath(target)
        bridge = self._editor.bridge
        _, tab = bridge._find_tab(norm)
        return bool(tab and tab.get('read_only'))

    def lock_path(self, path):
        key = self._norm_key(path)
        if not key:
            return
        prev = self._locked_paths.get(key, 0)
        self._locked_paths[key] = prev + 1
        if prev == 0:
            self._sync_open_tab_locks(key, True)
            self.locksChanged.emit()

    def unlock_path(self, path):
        key = self._norm_key(path)
        if not key:
            return
        cur = self._locked_paths.get(key, 0)
        if cur <= 1:
            self._locked_paths.pop(key, None)
            self._sync_open_tab_locks(key, False)
            self.locksChanged.emit()
        else:
            self._locked_paths[key] = cur - 1

    def _sync_open_tab_locks(self, folder_key, locked):
        bridge = self._editor.bridge
        changed = False
        for tab in list(self._tab_data):
            doc_path = tab.get('documentPath') or tab.get('path') or ''
            doc_key = self._norm_key(doc_path)
            path_key = self._norm_key(tab.get('path') or '')
            if not doc_key and not path_key:
                continue
            matches = any(
                k and (k == folder_key or k.startswith(folder_key + os.sep))
                for k in (doc_key, path_key)
            )
            if not matches:
                continue
            now_locked = bool(locked or self._is_source_read_only(tab.get('path') or '') or self._is_source_read_only(doc_path))
            if bool(tab.get('readOnly')) != now_locked:
                tab['readOnly'] = now_locked
                changed = True
            if doc_path in bridge._tabs:
                if locked:
                    bridge.set_read_only(doc_path, True)
                else:
                    bridge.promote_to_disk(doc_path, read_only=now_locked)
        if changed:
            self._refresh_tabs()

    @Slot(str, result=str)
    def pathHighlightState(self, path):
        key = self._norm_key(path)
        if not key:
            return ''
        entry = self._path_highlights.get(key)
        return entry['state'] if entry else ''

    @Slot(str, result=int)
    def pathProgress(self, path):
        key = self._norm_key(path)
        if not key:
            return -1
        return self._tasks.loading.get_path_progress(key)

    def set_path_highlight(self, path, state, *, fade_ms=2800):
        key = self._norm_key(path)
        if not key:
            return
        self._highlight_seq += 1
        seq = self._highlight_seq
        state_str = str(state or '').strip()
        if not state_str or state_str == 'clear':
            if key in self._path_highlights:
                self._path_highlights.pop(key, None)
                self.highlightsChanged.emit()
            return
        self._path_highlights[key] = {'state': state_str, 'seq': seq}
        self.highlightsChanged.emit()
        if state_str == 'done' and fade_ms > 0:
            def clear_if_same():
                if self._closing:
                    return
                current = self._path_highlights.get(key)
                if current and current.get('seq') == seq and current.get('state') == 'done':
                    self._path_highlights.pop(key, None)
                    self.highlightsChanged.emit()
            QTimer.singleShot(int(fade_ms), clear_if_same)

    @Slot(str, str, str, object)
    def _on_unpack_preview_ready(self, image, target_dir, base, payload):
        if self._closing:
            return
        if payload is not None:
            self._mounts.begin_unpack_preview(image, target_dir, base, preloaded=payload)
    @Property(bool, notify=changed)
    def prepared(self): return self._ready

    @Slot()
    def start(self):
        if self._started or self._closing: return
        self._started = True
        self._started_at = time.monotonic()
        self.preparation_status.emit("Scanning environment...")
        self._preferences_changed()
        self._source_recovery.start()

    @Slot(bool,object)
    def _sources_prepared(self, ok, result):
        if self._closing or self._preparation_error or self._ready:return
        if not ok:self._tree_failed(str(result));return
        self.preparation_status.emit('Loading project files...')
        bases=[resolve_tree_target(self.project,name) for name in (self.p1,self.p2) if name]
        self._ensure_fs_watcher(bases[0])
        self.tree.load(bases[0])
        if self.p2:
            self._ensure_fs_watcher(bases[1])
            self.tree2.load(bases[1])
            self.tree_mirror.load(bases[0])

    def _tree_ready(self, number):
        self._loaded_trees.add(number)
        if number == 0 and not self.p2 and not self._closing:
            base = resolve_tree_target(self.project, self.p1)
            if self.tree.engine is not None:
                self.tree_mirror.load(base, shared_metadata=(self.tree.engine, self.tree.ctx, self.tree.fs, self.tree.ctx_neocat))
            else:
                self.tree_mirror.load(base)
        self._check_ready()

    def _tree_failed(self, text):
        if not self._ready and not self._closing and not self._preparation_error:
            self._preparation_error = True
            self.preparation_failed.emit(text)
        elif not self._closing: self.message(text)

    @Slot()
    def _check_ready(self):
        if self._ready or self._closing or self._preparation_error or self._started_at is None: return
        expected = {0} | ({1} if self.p2 else set())
        if not expected.issubset(self._loaded_trees): return
        # File browsing is ready independently of the hidden Monaco renderer.
        # Text documents already queue their buffers until its WebChannel joins.
        self._ready = True
        self.changed.emit()
        self.ready.emit()
        QTimer.singleShot(0, self.prepareTerminal)

    def _preferences_changed(self):
        for directory in self.directories:
            directory.setDisplayMode(self.preferences.settings.get("treeMode", "single_dir"))

    def _theme_changed(self):
        self._editor.reloadTheme()
        for row in self._terminals: row["session"].applyTheme(self.preferences.colors)

    @Slot(int)
    def selectTree(self, number):
        self._active_tree_id = number
        self.active_tree = self.tree if number == 0 else self.tree2 if self.p2 else self.tree_mirror
        self._selection = []
        self.changed.emit()
        self.selectionChanged.emit()

    @Slot(str)
    def setFocusArea(self,area):
        if area in ('editor','terminal','tree0','tree1','dialog','search','workspace','agent','inactive') and area!=self._focus_area:
            if self._focus_area=='inactive' and area!='inactive' and hasattr(self,'_agent'):self._agent.chat.refreshBackends()
            if area in ('editor','tree0','tree1'):self._context_focus_area=area
            self._focus_area=area;self.focusChanged.emit(area)

    @Slot(int, "QVariantList")
    def selectPaths(self, number, paths):
        if number != self._active_tree_id: self.selectTree(number)
        selection = [str(path) for path in paths if path and path != ".."]
        if selection!=self._selection:
            self._selection=selection;self.selectionChanged.emit()

    @Slot()
    def toggleLayout(self):
        if self.p2:
            order = {2: 3, 3: 4, 4: 0, 0: 2}
        else:
            order = {2: 3, 3: 1, 1: 0, 0: 2}
        self._layout = order.get(self._layout, 2)
        self._restore_layout = 0
        self.changed.emit()

    def _reveal_right(self):
        if self._layout in (2, 4):
            self._restore_layout = self._layout
            self._layout = 3
        self.changed.emit()

    def _restore_tree(self):
        if not self.rightOpen and self._restore_layout:
            self._layout = self._restore_layout
            self._restore_layout = 0
        self.changed.emit()

    @Slot(str)
    def openPath(self, path):
        if self._mounts.reference(path):self._mounts.open(path);return
        if self._mounts.unpack_preview_reference(path):
            from ui.viewers import viewer_kind
            kind = viewer_kind(path)
            if kind in ('editor', 'choose', 'txt'):
                if self._mounts.open_preview(path):
                    return
        from ui.viewers import viewer_kind
        kind=viewer_kind(path)
        if kind=='choose':
            kind=self._file_associations().get(os.path.splitext(path)[1].lower(),'')
            if not kind:
                self.presentationRequested.emit('openWith',{'path':path,'name':os.path.basename(path)})
                return
        self.openAs(path, {'so':'elf','bin':'hex'}.get(kind,kind))

    def _association_path(self):
        if self._settings_path:return str(Path(self._settings_path).with_name('file_associations.json'))
        from core.app_paths import USER_STATE_DIR,APP_ROOT,migrate_legacy_file
        path=os.path.join(USER_STATE_DIR,'file_associations.json')
        migrate_legacy_file(os.path.join(APP_ROOT,'file_associations.json'),path)
        return path

    def _file_associations(self):
        try:
            data=json.loads(Path(self._association_path()).read_text(encoding='utf-8'))
            return data if isinstance(data,dict) else {}
        except (OSError,ValueError):return {}

    @Slot(str,str,bool)
    def chooseViewer(self,path,kind,always):
        if kind not in ('editor','so','bin','image','archive','payload','super'):return
        if always and os.path.splitext(path)[1]:
            associations=self._file_associations()
            associations[os.path.splitext(path)[1].lower()]=kind
            destination=Path(self._association_path());destination.parent.mkdir(parents=True,exist_ok=True)
            destination.write_text(json.dumps(associations,indent=2),encoding='utf-8')
        self.openAs(path,{'so':'elf','bin':'hex'}.get(kind,kind))

    def _find_tab(self, path):
        if not path:
            return None
        norm = os.path.normcase(os.path.normpath(path))
        for tab in self._tab_data:
            if tab.get("key") == path or tab.get("path") == path:
                return tab
            tpath = tab.get("path")
            if tpath and os.path.normcase(os.path.normpath(tpath)) == norm:
                return tab
            dpath = tab.get("documentPath")
            if dpath and os.path.normcase(os.path.normpath(dpath)) == norm:
                return tab
        return None

    def _resolve_physical_target(self, path, *, record_edit=False):
        directory = next((d for d in self.directories if d.base and
                          os.path.commonpath([os.path.abspath(path), d.base]) == d.base), None)
        physical = path
        branch_context = None
        if directory:
            from core.workspace_files import WorkspaceFiles
            files = WorkspaceFiles(directory.base)
            physical, _ = files.view.resolve(path)
            if physical and record_edit:
                _logical, _rel, root, branch = files.branch(path)
                if branch:
                    physical = files.view.manager.record_edit(root, branch, path)
                if root in (directory.engine.root_map_ctx if directory.engine else {}):
                    branch_context = (directory.base, root, path, branch)
                elif root == 'config':
                    from main_ext.class_NeoBranchManager import CONFIG_SUFFIXES
                    store = os.path.join(directory.base, 'neobranch')
                    for owner in os.listdir(store) if os.path.isdir(store) else ():
                        if (files.view.manager.is_physical_checkout(owner) and
                                os.path.basename(path) in {owner + suffix for suffix in CONFIG_SUFFIXES}):
                            active = files.view.manager.get_active_branch(owner)
                            branch_context = (directory.base, owner, path, active['name'] if active else None)
                            break
        return physical, branch_context

    @Slot(str, str)
    def openAs(self, path, kind):
        path = os.path.normpath(path)
        existing = self._find_tab(path)
        if existing is not None:
            if kind and existing.get("kind") != kind and existing.get("markdown") and kind in ("editor", "markdown"):
                self.setMarkdownEditMode(existing["path"], kind == "editor")
                return
            self.selectTab(existing["path"])
            return
        if kind == "editor" and self._mounts.unpack_preview_reference(path):
            if self._mounts.open_preview(path):
                return
        physical, branch_context = self._resolve_physical_target(path, record_edit=(kind == "editor"))
        if not physical or not os.path.isfile(physical):
            self.message("File not found: " + path)
            return
        from ui.viewers import is_markdown_path
        locked = self.isFileReadOnly(path) or self.isFileReadOnly(physical)
        if kind == "editor" and not self._editor.openFile(physical, read_only=locked): return
        if branch_context:self._editor.bridge.bind_neobranch_tab(physical,*branch_context)
        tab = {"key": path, "path": path, "title": os.path.basename(path), "kind": kind,
               "documentPath": physical, "icon": file_icon(path), "unsaved": False,
               "markdown": is_markdown_path(path), "readOnly": locked,
               "url": QUrl.fromLocalFile(physical).toString()}
        self._tab_data.append(tab)
        self._active_tab = path
        self._refresh_tabs()
        self._reveal_right()

    @Slot(str, bool)
    def setMarkdownEditMode(self, path, editing):
        tab = self._find_tab(path)
        if tab is None:
            return
        target_kind = "editor" if editing else "markdown"
        if tab.get("kind") == target_kind:
            self.selectTab(tab["path"])
            return
        if editing:
            physical, branch_context = self._resolve_physical_target(tab["path"], record_edit=True)
            if not physical or not os.path.isfile(physical):
                self.message("File not found: " + tab["path"])
                return
            tab["documentPath"] = physical
            tab["url"] = QUrl.fromLocalFile(physical).toString()
            locked = self.isFileReadOnly(tab["path"]) or self.isFileReadOnly(physical)
            tab["readOnly"] = locked
            if not self._editor.openFile(physical, read_only=locked):
                return
            if branch_context:
                self._editor.bridge.bind_neobranch_tab(physical, *branch_context)
        tab["kind"] = target_kind
        tab["markdown"] = True
        self._active_tab = tab["path"]
        self._refresh_tabs()

    @Slot(str, result=str)
    def markdownSource(self, path):
        tab = self._find_tab(path)
        doc_path = tab.get("documentPath") if tab else path
        _, bridge_tab = self._editor.bridge._find_tab(doc_path or "")
        if bridge_tab is not None:
            return bridge_tab.get("content", "")
        return ""

    @Slot(str)
    def selectTab(self, path):
        tab = self._find_tab(path)
        if not tab: return
        self._active_tab = tab["path"]
        if tab.get("kind") == "editor":
            locked = self.isFileReadOnly(tab["path"]) or self.isFileReadOnly(tab["documentPath"])
            tab["readOnly"] = locked
            self._editor.openFile(tab["documentPath"], read_only=locked)
        self.tabsChanged.emit()

    @Slot(str,str,'QStringList',bool)
    def extractPartitions(self,path,kind,parts,all_parts):
        from ui.partition_viewer import extract
        extract(self,path,kind,parts,all_parts)

    @Slot(str)
    def closeTab(self, path):
        tab = self._find_tab(path)
        document = tab["documentPath"] if tab else path
        tab_path = tab["path"] if tab else path
        if self._editor.bridge.is_unsaved(document) or (tab and tab.get('viewerDirty')):
            self.message(ui_lang.t("utc_unsaved_msg", filename=os.path.basename(path)), "Close Tab",
                         confirm=lambda: self._close_tab(tab_path), danger=True,
                         accept=ui_lang.t("utc_btn_close"))
        else: self._close_tab(tab_path)

    def _close_tab(self, path):
        tab = self._find_tab(path)
        document = tab["documentPath"] if tab else path
        tab_path = tab["path"] if tab else path
        if document in self._editor.bridge._tabs:
            self._editor.closeFile(document)
        else: self._tab_closed(tab_path)

    def _tab_closed(self, path):
        tab = self._find_tab(path)
        index = self._tab_data.index(tab) if (tab and tab in self._tab_data) else -1
        if index < 0: return
        closed = self._tab_data.pop(index)
        if self._active_tab == closed["path"]:
            self._active_tab = ""
            if self._tab_data: self.selectTab(self._tab_data[min(index, len(self._tab_data)-1)]["path"])
        self._refresh_tabs()
        self._restore_tree()

    def _refresh_tabs(self, *_):
        for tab in self._tab_data:
            tab["unsaved"] = self._editor.bridge.is_unsaved(tab["documentPath"]) or bool(tab.get('viewerDirty'))
            tab["readOnly"] = self.isFileReadOnly(tab.get("path") or "") or self.isFileReadOnly(tab.get("documentPath") or "")
        self._tabs.replace(self._tab_data, animate=False)
        self.tabsChanged.emit()

    @Slot(str,bool)
    def setViewerUnsaved(self,path,dirty):
        tab = self._find_tab(path)
        if tab is not None and bool(tab.get('viewerDirty'))!=dirty:
            tab['viewerDirty']=dirty;self._refresh_tabs()

    @Slot()
    def saveCurrent(self):
        if self.activeKind=='editor':self._editor.saveCurrent()
        elif self.activeKind=='markdown' and self.activeDetails.get('documentPath') in self._editor.bridge._tabs:self._editor.saveCurrent()
        else:self.presentationRequested.emit('saveViewer',{'path':self.activeTab})

    @Slot()
    def toggleTerminal(self):
        self.probeWsl()
        if self._terminal_open:
            self._terminal_open = False
            self.terminalsChanged.emit()
            self._restore_tree()
        else:
            if not self._ensure_terminal(): return
            self._terminal_open = True
            self._reveal_right()
            self.terminalsChanged.emit()

    @staticmethod
    def _alpha(number):
        result = ""
        while number:
            number, remainder = divmod(number-1, 26)
            result = chr(65+remainder) + result
        return result

    def _ensure_terminal(self):
        if self._terminal_active.get(self._terminal_root): return True
        return self.addTerminal()

    @Slot()
    def prepareTerminal(self):
        if not self._closing:
            self._ensure_terminal()

    @Slot(result=bool)
    def addTerminal(self):
        return self._create_terminal(self._terminal_root) is not None

    def _create_terminal(self,root,*,cwd=None,title='New Session',icon_key=None,manager=None,activate=True):
        base_root=self._linux_source_root if root=='linux' else root
        base = cwd or resolve_tree_target(self.project, self.p2 if base_root == "tree2" else self.p1)
        session = TerminalSession(manager=manager,parent=self) if manager is not None else self._terminal_factory(cwd=base, wsl_mode=root == "linux", parent=self)
        session.warningRaised.connect(lambda title, text: self.message(text, title))
        session.applyTheme(self.preferences.colors)
        if not session.start(): session.deleteLater(); return None
        self._terminal_indices[root] += 1
        key = root + ":" + str(self._terminal_indices[root])
        title=self._unique_terminal_title(title,root)
        row={"key": key, "root": root, "title": title,
             "icon":icon_key or "terminal","session": session}
        self._terminals.append(row)
        session.firstCommandSubmitted.connect(lambda command,key=key:self._name_terminal(key,command))
        if manager is not None and hasattr(manager, "signals") and hasattr(manager.signals, "process_exited"):
            def _on_terminal_exit(_code=0, b=base):
                try:
                    self._schedule_terminal_fs_sync(b)
                except Exception:
                    pass
            manager.signals.process_exited.connect(_on_terminal_exit)
        elif hasattr(session, "commandActivitySettled"):
            session.commandActivitySettled.connect(lambda b=base: self._schedule_terminal_fs_sync(b))
        if activate:
            self._terminal_active[root] = key
        self._sync_terminals()
        return row

    def processTerminal(self):
        if self._closing:return None
        row=next((row for row in self._terminals if row['key']==self._process_terminal_key),None)
        if row is None:
            from core.terminal_execution import ProcessTerminalManager
            root='tree2' if self.activeTreeId==1 and self.p2 else 'tree1'
            row=self._create_terminal(root,cwd=self.project,title='Kitchen',icon_key='terminal',
                                      manager=ProcessTerminalManager(self.project),activate=False)
            if row is None:return None
            self._process_terminal_key=row['key']
        if self._terminal_open and (self._terminal_root!=row['root'] or self.activeTerminal!=row['key']):
            root_changed=self._terminal_root!=row['root']
            self._terminal_root=row['root'];self._terminal_active[row['root']]=row['key']
            if root_changed:self._sync_terminals()
            else:self.terminalsChanged.emit()
        return row['session']._manager

    def runTerminalProcess(self,process,program,args):
        manager=self.processTerminal()
        if manager is None:return None
        process.setWorkingDirectory(self.project)
        manager.launch(process,program,args)
        return manager

    def runTerminalNative(self,job,tool,args):
        manager=self.processTerminal()
        if manager is None:return None
        manager.observe_native(job,subprocess.list2cmdline([os.path.basename(str(tool)),*map(str,args)]))
        return manager

    def _unique_terminal_title(self,title,root,exclude=''):
        existing={row['title'] for row in self._terminals if row['root']==root and row['key']!=exclude}
        result=title;number=2
        while result in existing:result=f'{title} {number}';number+=1
        return result

    def _name_terminal(self,key,command):
        from core.terminal_titles import command_name
        row=next((row for row in self._terminals if row['key']==key),None)
        if row:
            row['title']=self._unique_terminal_title(command_name(command),row['root'],key)
            self._sync_terminals()

    def _sync_terminals(self):
        self._terminal_views.replace([{name:value for name,value in row.items() if name in ("key","root","session")}
                                      for row in self._terminals],animate=False)
        self._terminal_rows.replace([{name: value for name, value in row.items() if name != "session"}
                                     for row in self._terminals if row["root"] == self._terminal_root], animate=False)
        self.terminalsChanged.emit()

    @Slot(str)
    def selectTerminal(self, key):
        if any(row["key"] == key and row["root"] == self._terminal_root for row in self._terminals):
            self._terminal_active[self._terminal_root] = key
            self.terminalsChanged.emit()

    @Slot(str)
    def closeTerminal(self, key):
        row = next((row for row in self._terminals if row["key"] == key), None)
        if row is None: return
        # Retire the WebEngine delegate before stopping its PTY. Deleting a tab
        # never stops an unrelated root or a hidden session.
        self._terminals.remove(row)
        siblings = [item for item in self._terminals if item["root"] == row["root"]]
        if self._terminal_active.get(row["root"]) == key:
            self._terminal_active[row["root"]] = siblings[-1]["key"] if siblings else ""
        self._sync_terminals()
        QTimer.singleShot(0, row["session"].stop)
        if not siblings and row["root"] == self._terminal_root:
            if self._terminals:
                for root in ('tree1','tree2','linux'):
                    if any(item['root']==root for item in self._terminals):
                        self._terminal_root=root;self._sync_terminals();break
            else:
                self._terminal_open = False
                self.terminalsChanged.emit()
                self._restore_tree()

    @Slot(str)
    def switchTerminalRoot(self, root):
        if root not in {item["value"] for item in self.terminalRoots}: return
        previous = self._terminal_root
        if root=='linux' and previous!='linux':self._linux_source_root=previous
        self._terminal_root = root
        if not self._ensure_terminal(): self._terminal_root = previous
        self._sync_terminals()

    @Slot()
    def probeWsl(self):
        if self._terminal_factory is not TerminalSession:return
        if self._wsl_probe_running or self._wsl_probe_time is not None and time.monotonic()-self._wsl_probe_time<30:return
        self._wsl_probe_running=True
        def probe():
            from core.wsl_manager import terminal_is_ready
            return True,terminal_is_ready()
        def done(ok,result):
            self._wsl_probe_running=False;self._wsl_probe_time=time.monotonic()
            self._wsl_available=bool(ok and result);self.changed.emit()
        self._jobs.submit('terminal-wsl',probe,None,done)

    @Slot()
    def runActive(self):
        if not self.activeRunnable:return
        from main_ext.file_run_commands import build_run_command
        path=self.activeDetails['documentPath']
        root='tree1'
        if self.p2:
            try:
                if os.path.commonpath([path,self.tree2.base])==self.tree2.base:root='tree2'
            except ValueError:pass
        key=self._file_run_terminals.get(os.path.normcase(path))
        row=next((row for row in self._terminals if row['key']==key),None)
        if row is None:
            row=self._create_terminal(root,cwd=os.path.dirname(path),title=os.path.basename(path),icon_key='start')
            if row is None:return
            self._file_run_terminals[os.path.normcase(path)]=row['key']
        self._terminal_root=root;self._terminal_active[root]=row['key'];self._terminal_open=True
        self._sync_terminals();self._reveal_right()
        row['session'].runCommand(build_run_command(path))

    def _read_settings(self):
        data = default_project_settings()
        try:
            saved = json.loads(Path(self.settings_path()).read_text(encoding="utf-8"))
            for key in data:
                if isinstance(saved.get(key), dict): data[key].update(saved[key])
        except (OSError, ValueError, TypeError): pass
        from core.agent_policy import normalize
        data['agent']=normalize(data['agent'])
        return data

    def settings_path(self):
        if self._settings_path: return self._settings_path
        from core.app_paths import PROJECT_DATA_DIR
        return os.path.join(PROJECT_DATA_DIR, "project_settings.json")

    @Slot("QVariantMap")
    def applySettings(self, data):
        result = default_project_settings()
        for key in result:
            if isinstance(data.get(key), dict): result[key].update(data[key])
        from core.agent_policy import normalize
        result['agent']=normalize(result['agent'])
        path = Path(self.settings_path())
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(result, indent=2), encoding="utf-8")
        os.replace(temporary, path)
        self._settings = result
        self.settingsChanged.emit()

    @Slot(str)
    def _present_fatal(self, text):
        if self._closing:return
        from core.error_reporting import is_agent_error
        if is_agent_error(text):return
        if text and text not in self._fatal_active and text not in self._fatal_pending:
            self._fatal_pending.append(text)
        if self._fatal_visible or not self._fatal_pending:return
        from html import escape
        batch,self._fatal_pending = self._fatal_pending,[]
        self._fatal_active = set(batch)
        self._fatal_visible = True
        self.presentationRequested.emit('fatal', {'title':'Fatal Crash!','fatal':True,'danger':True,'width':380,
            'log':'\n\n'.join(batch),
            'text':'<span style="color:#f08080; font-size:18px; font-weight:600;">Fatal Crash!</span><br><br>'+escape(ui_lang.t('dlg_crash_message'))})

    @Slot(str)
    def _present_operation_error(self,text):
        from core.error_reporting import is_agent_error
        if is_agent_error(text):return
        from html import escape
        lines=[line.strip() for line in text.splitlines() if line.strip()]
        summary=next((line for line in lines if 'FAILED' in line or 'error:' in line.lower()),lines[-1] if lines else text)
        self.presentationRequested.emit('message',{'title':'Operation failed','text':escape(summary[:500]),
            'log':text,'width':440,'danger':True})

    def message(self, text, title="", *, confirm=None, cancel=None, danger=False, accept="", cancel_label="", dismissed=None,
                error=False, width=None):
        if (error or hasattr(text,'application_error')) and confirm is None:
            from core.error_reporting import report_fatal
            report_fatal((str(title)+'\n\n' if title else '')+str(text), owner=self,
                         application_error=getattr(text,'application_error',False))
            return
        self._pending_confirmation = confirm
        self._pending_confirmation_cancel = cancel
        self._pending_message_dismiss = dismissed
        self.presentationRequested.emit("message", {"text": str(text), "title": title or ui_lang.t("lbl_error"),
                                         "confirm": confirm is not None, "danger": danger, "acceptLabel": accept,
                                         "cancelLabel":cancel_label,"notifyDismiss": dismissed is not None,
                                         **({"width": width} if width is not None else {})})

    @Slot(str,str)
    def showMessage(self,title,text):self.message(text,title)

    @Slot(str)
    def copyText(self,text):QGuiApplication.clipboard().setText(text)

    @Slot(str,result=str)
    def physicalUrl(self,path):
        from core.workspace_files import WorkspaceFiles
        for directory in self.directories:
            if not directory.base:continue
            try:
                files=WorkspaceFiles(directory.base)
                physical,_=files.view.resolve(path)
                if physical:return QUrl.fromLocalFile(physical).toString()
            except ValueError:continue
        return QUrl.fromLocalFile(path).toString()

    @Slot('QVariantList')
    def createSymlinks(self,items):
        from core.workspace_files import WorkspaceFiles,validate_name
        from ui.workspace_actions import operation
        if not items:self.message(ui_lang.t('sym_err_no_selection'),ui_lang.t('sym_title'));return
        try:
            selected=[(validate_name(item.get('name','')),str(item.get('target','')).strip()) for item in items]
            if any(not target for _,target in selected):raise ValueError(ui_lang.t('sym_err_empty_target'))
            base,directory=self._symlink_target
        except (AttributeError,ValueError) as error:self.message(str(error),ui_lang.t('sym_title'));return
        files=WorkspaceFiles(base)
        def work():
            from core.make_symlink import make_symlink
            count=0
            for name,target in selected:
                logical=os.path.join(directory,name);physical=files.write_path(logical)
                if make_symlink(physical,target,overwrite=True):
                    files._record(logical,physical)
                    if not files.branch(logical)[3]:
                        files.engine(logical).apply(os.path.relpath(logical,base).replace(os.sep,'/'))
                    from core import filesystem_map
                    filesystem_map.record_entry(base,logical,physical)
                    count+=1
            return count
        operation(self,ui_lang.t('sym_title'),work,lambda count:self.presentationRequested.emit('symlinksCreated',{'count':count}),
                  lane='light',targets=tuple(os.path.join(directory,name) for name,_ in selected))

    @Slot(bool)
    def answer(self, accepted):
        callback = self._pending_confirmation if accepted else self._pending_confirmation_cancel
        self._pending_confirmation = self._pending_confirmation_cancel = None
        if callback: callback()

    @Slot(str, "QVariantMap")
    def dispatch(self, action, data):
        if action == 'agentInbox':
            if self._agent.chat.opened and not data.get('paths') and not data.get('origin'):
                self._agent.chat.hide()
            else:
                self._agent.chat.show(data.get('origin',''),data.get('paths') or ([data['path']] if data.get('path') else []))
        elif action in ('agentSend','agentShow'):
            if data.get('path') and data.get('selection'):
                self.editor.bridge.update_selection(data['path'], data['selection'])
            paths=data.get('paths') or ([data['path']] if data.get('path') else [])
            paths=[self._agent.logical(self._editor.bridge._find_tab(p)[0] or p) for p in paths]
            self._agent.chat.show(data.get('origin',''),paths)
        elif action == 'agentAnswer':self._agent.answer(data['id'],bool(data.get('accepted')),data.get('values',{}))
        elif action == 'fatalDismissed':
            self._fatal_visible = False;self._fatal_active.clear();self._present_fatal('')
        elif action == 'errorLog':self.presentationRequested.emit('errorLog',data)
        elif action == 'copyText':self.copyText(data['text'])
        elif action in ("messageAccepted","confirm"): self.answer(True)
        elif action=='messageDismissed':
            callback,self._pending_message_dismiss=self._pending_message_dismiss,None
            if callback:callback()
        elif action in ("messageCancelled","cancelConfirmation"): self.answer(False)
        elif action == "back": self.requestReturn()
        elif action == "quit": self.requestQuit()
        elif action == "settings": self.presentationRequested.emit("settings", {})
        elif action == "bootAnalyzer": self.presentationRequested.emit("bootAnalyzer", data or {})
        elif action == "terminal": self.toggleTerminal()
        elif action == "layout": self.toggleLayout()
        elif action == "save": self.saveCurrent()
        elif action == "refresh": self.active_tree.refresh()
        elif action == "explorer": QDesktopServices.openUrl(QUrl.fromLocalFile(data.get("path") or self.active_tree.location))
        else:
            from ui.workspace_actions import dispatch
            dispatch(self, action, data)

    @Slot()
    def requestReturn(self):
        self.message(ui_lang.t("main_return_msg"),ui_lang.t("main_return_title"),
                     confirm=lambda:self._confirm_leave(self.returnRequested.emit,returning=True),accept=ui_lang.t("main_btn_return"))

    @Slot()
    def requestQuit(self):
        self._confirm_leave(self.quitRequested.emit)

    def _confirm_leave(self, callback, *, returning=False):
        if self._operation_count:
            self.message(ui_lang.t('main_tasks_running_msg'),ui_lang.t('main_tasks_running_title'),
                         confirm=lambda:self._confirm_documents(lambda:self._force_leave(callback,returning)),
                         danger=True,accept=ui_lang.t('main_btn_force_close'),cancel_label=ui_lang.t('main_btn_wait'))
            return
        self._confirm_documents(callback)

    def _confirm_documents(self, callback):
        dirty=[tab['title'] for tab in self._tab_data if tab.get('viewerDirty') or self._editor.bridge.is_unsaved(tab['documentPath'])]
        if dirty:
            self.message(ui_lang.t("utc_unsaved_msg", filename=", ".join(dirty)),
                         "Unsaved changes", confirm=callback, danger=True, accept=ui_lang.t("utc_btn_close"))
        else: callback()

    def _force_leave(self, callback, returning):
        self._stop_background_work(returning=returning)
        callback()

    def _stop_background_work(self, *, returning):
        # Match Main's Force Close / Return distinction: cancel project jobs and
        # managed child tools in both cases; keep the shared service on Return.
        self._tasks.close()
        from core.lock_recovery import force_release_managed_tools
        from core.threader import stop_all_threads
        for label,stop in (('managed tools',lambda:force_release_managed_tools(only_children=True)),
                           ('worker threads',stop_all_threads)):
            try:stop()
            except Exception as error:print(f'[Workspace] stop {label}: {error}')
        if not returning:
            from services.process_manager import get_manager
            try:get_manager().stop(timeout=.4)
            except Exception as error:print(f'[Workspace] stop service: {error}')

    def editMetadata(self, directory, path, column, value):
        if self._mounts.reference(path):self._mounts.metadata(path,column,value);return
        if column==0:
            from ui.workspace_actions import operation
            from core.workspace_files import WorkspaceFiles
            files=WorkspaceFiles(directory.base)
            if directory._draft and directory._draft.path==path:
                parent=directory._draft.parent.path;folder=directory._draft.folder
                directory.cancelDraft()
                if value.strip():operation(self,"Create "+value,lambda:files.create(parent,value,folder=folder),
                                           lane="light",targets=(os.path.join(parent,value),))
            elif value!=os.path.basename(path):operation(self,"Rename "+os.path.basename(path),lambda:files.rename(path,value),
                                                        lane="light",targets=(path,os.path.join(os.path.dirname(path),value)))
            return
        from ui.workspace_actions import edit_metadata
        edit_metadata(self, directory, path, column, value)

    def beginInlineEdit(self,path,column=0):
        number=0 if self.active_tree is self.tree else 1
        self.editCellRequested.emit(number,path,column)

    @Slot(str,str,str)
    def createFilesystem(self,path,kind,mount_point):
        from core.workspace_files import WorkspaceFiles
        from ui.workspace_actions import operation
        def failed(error):
            self.presentationRequested.emit('filesystemFinished',{'path':path,'ok':False})
            self.message(str(error),ui_lang.t('mf_failed'))
        directory=next((item for item in self.directories if item.base and
                        os.path.commonpath([os.path.abspath(path),item.base])==item.base),None)
        if directory is None or kind not in ('EROFS','EXT4'):
            failed('Select a partition folder and filesystem type');return
        files=WorkspaceFiles(directory.base)
        try:
            logical,relative=files.logical(path)
            if '/' in relative or relative in ('config','neobranch') or not os.path.isdir(logical):
                raise ValueError('Select a partition folder')
            if files.branch(logical)[3]:raise ValueError('Switch to Source before creating the filesystem')
        except (OSError,ValueError) as error:failed(error);return
        self.lock_path(logical)
        self.set_path_highlight(logical,'loading')
        def work():
            from core.make_filesystem import make_filesystem
            return make_filesystem(logical,kind,mount_point.strip() or '/'+relative)
        def settled(ok,result):
            self.unlock_path(logical)
            self.set_path_highlight(logical,'done' if ok else 'error')
            self.presentationRequested.emit('filesystemFinished',{'path':path,'ok':ok})
            if ok:
                self.message(ui_lang.t('mf_done')+'\n'+str(result),ui_lang.t('mf_done'),
                             dismissed=lambda:self.presentationRequested.emit('filesystemDismiss',{'path':path}))
        operation(self,'make_filesystem:'+path,work,settled=settled,lane='heavy',targets=(logical,logical+'.img'))

    def beginCreate(self,path,folder):
        directory=self.active_tree
        selected=directory._nodes.get(self._selection[0]) if self._selection else None
        after=selected.path if selected is not None and not selected.folder else ''
        def create():
            draft=directory.beginDraft(path,folder,after)
            if draft:self.beginInlineEdit(draft)
        if directory.displayMode=='single_dir' and directory.location!=path:
            def ready():
                directory.ready.disconnect(ready)
                if not self._closing:create()
            directory.ready.connect(ready)
            directory.activate(path)
        else:create()

    @Slot(str)
    def _on_editor_file_saved(self, filepath):
        if not filepath or str(filepath).startswith("mount://"):
            return
        try:
            logical = self._agent.logical(filepath) if hasattr(self, "_agent") else filepath
        except Exception:
            logical = filepath
        for directory in self.directories:
            base = directory.base
            if not base:
                continue
            try:
                base_abs = os.path.abspath(base)
                check = os.path.abspath(logical)
                if os.path.commonpath([check, base_abs]) == base_abs:
                    from core import filesystem_map
                    filesystem_map.record_entry(base, logical, filepath)
            except (OSError, ValueError):
                continue

    def _ensure_fs_watcher(self, base):
        if self._closing or not base or not os.path.isdir(base):
            return
        norm = os.path.normcase(os.path.abspath(os.path.normpath(base)))
        if norm in self._fs_watchers:
            return
        from core.fs_watcher import ProjectFsWatcher
        watcher = ProjectFsWatcher(
            base,
            lambda events, overflowed, b=base: self._on_watcher_batch(b, events, overflowed),
            is_locked_fn=self.isPathLocked,
        )
        if watcher.start():
            self._fs_watchers[norm] = watcher
            weakref.finalize(self, watcher.stop)

    def _on_watcher_batch(self, base, events, overflowed):
        if self._closing:
            return
        from core import filesystem_map
        dirty_dirs = filesystem_map.sync_external_paths(
            base, [p for p, _ in events], overflowed=overflowed
        )
        try:
            self._externalFsSync.emit(base, dirty_dirs)
        except RuntimeError:
            pass

    @Slot(str, object)
    def _apply_external_fs_sync(self, base, dirty_dirs):
        if self._closing or not base:
            return
        norm_base = os.path.normcase(os.path.abspath(os.path.normpath(base)))
        for directory in self.directories:
            if directory.base and os.path.normcase(os.path.abspath(os.path.normpath(directory.base))) == norm_base:
                directory.notify_external_changes(dirty_dirs)

    def _schedule_terminal_fs_sync(self, base):
        if self._closing or not base:
            return
        norm = os.path.normcase(os.path.abspath(os.path.normpath(base)))
        timer = self._terminal_sync_timers.get(norm)
        if timer is None:
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.setInterval(300)
            timer.timeout.connect(lambda b=base: self._apply_external_fs_sync(b, None))
            self._terminal_sync_timers[norm] = timer
        timer.start()

    def _stop_fs_watchers(self, *_):
        for timer in tuple(self._terminal_sync_timers.values()):
            try:
                timer.stop()
            except RuntimeError:
                pass
        self._terminal_sync_timers.clear()
        for watcher in tuple(self._fs_watchers.values()):
            try:
                watcher.stop()
            except Exception:
                pass
        self._fs_watchers.clear()

    @Slot()
    def stop(self):
        if self._closing: return
        self._closing = True
        self._stop_fs_watchers()
        self._agent.close()
        self._source_recovery.close()
        self._jobs.stop()
        self._search.close()
        self._android_tools.close()
        self._toolkit.close()
        self._flashable.close()
        self._pack_super.close()
        self._boot_analyzer.stop()
        self._transfers.close()
        self._partition_picker.close()
        self._import_picker.close()
        self._private.close()
        self._mounts.close()
        self._tasks.close()
        for directory in self.directories: directory.close()

    def web_sessions(self):
        return (self._editor, *(row["session"] for row in self._terminals))
