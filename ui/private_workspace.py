# SPDX-License-Identifier: MPL-2.0
"""Optional official services behind Qt Quick presentation and original DevGuard."""
import os
import shutil
import tempfile

from PySide6.QtCore import QObject,Property,Signal,Slot,QTimer,Qt
from core import ui_lang
from core.rennsproject_bridge import private_bundle_enabled
from ui.file_picker import RenxecPicker


class PrivateWorkspace(QObject):
    changed=Signal()
    logLine=Signal(str)
    logAppended=Signal(str)
    guardState=Signal()
    def __init__(self,workspace,profile_provider=None):
        super().__init__(workspace);self.workspace=workspace;self.provider=profile_provider
        self.guard=None;self._closed=False;self._running=False;self._logs=[]
        self._picker=RenxecPicker(self);self._picker.chosen.connect(self._picked)
        self.logLine.connect(self._append_log,Qt.QueuedConnection)
        self.guardState.connect(self._refresh_guard,Qt.QueuedConnection)
        self._allowed=False
        from ui.compare import WorkspaceCompare
        self._compare=WorkspaceCompare(self)
        self._timer=QTimer(self);self._timer.setInterval(500);self._timer.timeout.connect(self._refresh_guard)
    @Property(QObject,constant=True)
    def filePicker(self):return self._picker
    @Property(QObject,constant=True)
    def compare(self):return self._compare
    @Property(bool,notify=changed)
    def allowed(self):return self._allowed
    @Property(bool,notify=changed)
    def running(self):return self._running
    @Property('QStringList',notify=changed)
    def logs(self):return list(self._logs)
    def start(self):
        if not private_bundle_enabled():return
        from rennsproject.core.dev_guard import DevGuard
        self.guard=DevGuard()
        self.guard.add_on_revoke(lambda reason:self.guardState.emit() if not self._closed else None)
        self.guard.add_on_allow(lambda:self.guardState.emit() if not self._closed else None)
        if self.provider:self.guard.set_verified_level(getattr(self.provider,'verified_level',None))
        self._refresh_guard();self.guard.start();self._timer.start()
    @Slot()
    def _refresh_guard(self):
        if self._closed or self.guard is None:return
        if self.provider:
            level=getattr(self.provider,'verified_level',None)
            if level!=self.guard.verified_level:self.guard.set_verified_level(level)
        value=self.guard.allowed is True
        if value!=self._allowed:
            revoked=self._allowed and not value;self._allowed=value;self.changed.emit()
            if revoked:self.workspace.message('Official feature access has been revoked.\nAdvanced automation features are no longer available.\nThe kitchen remains available.','Official Access Revoked',error=False)
    def _check(self):
        if self._allowed and self.guard and self.guard.allowed is True:return True
        self.workspace.message('Automation and compare access is unavailable.','Official Access Revoked',error=False);return False
    @Slot()
    def pickAutomation(self):
        if not self._check() or self._running:return
        self._picker.refreshLocations()
        self._picker.load(self._picker.directory)
        self.workspace.presentationRequested.emit('renxecPicker',{})
    @Slot(str)
    def _picked(self,path):
        if not self._check():return
        self.workspace.presentationRequested.emit('renxecPicked',{})
        self.workspace.partitionPicker.show(lambda base,name:self.runAutomation(path,base,name))
    def runAutomation(self,path,base,name):
        if not self._check() or self._running:return
        self._running=True;self._logs=[];self.changed.emit()
        self.workspace.presentationRequested.emit('automationLog',{})
        def work():
            from core.archive_safety import safe_extract_zip
            from rennsproject.core.rak_automation_view import run_automation_in_view
            root=os.path.join(base,'automation');os.makedirs(root,exist_ok=True)
            with tempfile.TemporaryDirectory(prefix='renxec_',dir=root) as destination:
                safe_extract_zip(path,destination,max_members=10000,max_uncompressed_bytes=4*1024**3)
                command=os.path.join(destination,'main.rensh')
                if not os.path.isfile(command):raise FileNotFoundError(ui_lang.t('main_rensh_not_found'))
                def log(line):
                    if not self._closed:self.logLine.emit(str(line))
                ok=run_automation_in_view(base,name,destination,command,log)
                return bool(ok),'Automation complete' if ok else 'Automation failed (END! not reached)'
        def done(ok,result):
            self._running=False;self._append_log(str(result));self.changed.emit()
            for tree in self.workspace.directories:
                if tree.base==base:tree.reloadMetadata()
        self.workspace._tasks.enqueue('automation:'+name,work,done)
    @Slot(str)
    def _append_log(self,line):
        if self._closed:return
        self._logs.append(line);self.logAppended.emit(line)
    def close(self):
        self._closed=True;self._timer.stop();self._picker.close();self._compare.close()
        if self.guard:self.guard.stop()
