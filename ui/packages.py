# SPDX-License-Identifier: MPL-2.0
"""Package and WSL operations for native Quick views, without QWidget owners."""
import json
import logging
import sys
import threading
import traceback
from PySide6.QtCore import QObject, Property, Signal, Slot, QThread, QTimer, QUrl
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkRequest, QNetworkReply
from core.dependency_manager import DependencyManager, REMOTE_REGISTRY_URL
from core import wsl_manager
from ui.rows import RowsModel


class PackageJob(QThread):
    done = Signal(object, bool, object)

    def __init__(self, operation, parent):
        super().__init__(parent)
        self.operation = operation

    def run(self):
        try:
            result = self.operation()
        except Exception as error:
            logging.exception('Package operation failed')
            self.done.emit(self,False,traceback.format_exc())
        else:
            self.done.emit(self,True,result)


class PackagesModel(QObject):
    changed = Signal()

    def __init__(self, parent=None, *, network=True, manager_factory=None, wsl_probe=None):
        super().__init__(parent)
        self._network = network
        self._manager_factory = manager_factory or (lambda:DependencyManager(allow_remote=False))
        self._wsl_probe = wsl_probe or wsl_manager.detect
        self._manager = None
        self._rows = RowsModel(parent=self)
        self._loading, self._error = False, False
        self._wsl = {"checking":True}
        self._jobs, self._busy, self._failed = {}, set(), set()
        self._lock = threading.Lock()
        self._generation = 0
        self._reply = None
        self._nam = QNetworkAccessManager(self)
        self._timeout = QTimer(self)
        self._timeout.setSingleShot(True)
        self._timeout.timeout.connect(self._fallback)

    @Property(QObject, constant=True)
    def rows(self):
        return self._rows

    @Property(bool, notify=changed)
    def loading(self):
        return self._loading

    @Property(bool, notify=changed)
    def error(self):
        return self._error

    @Property(bool, constant=True)
    def showWsl(self):
        return sys.platform == "win32"

    @Property("QVariantMap", notify=changed)
    def wsl(self):
        return self._wsl

    @property
    def running(self):
        return bool(self._jobs)

    @Slot()
    def refresh(self):
        if self._busy:
            return
        self.close()
        self._generation += 1
        self._loading, self._error = True, False
        self.changed.emit()
        self._manager = self._manager_factory()
        if not self._network:
            self._load_rows(self._generation)
            return
        request = QNetworkRequest(QUrl(REMOTE_REGISTRY_URL))
        request.setRawHeader(b"Cache-Control",b"no-cache")
        request.setRawHeader(b"Pragma",b"no-cache")
        self._reply = self._nam.get(request)
        reply = self._reply
        reply.finished.connect(lambda:self._network_done(reply))
        self._timeout.start(6000)

    def _network_done(self, reply):
        if reply is not self._reply:
            reply.deleteLater()
            return
        self._reply = None
        self._timeout.stop()
        if reply.error()==QNetworkReply.NoError:
            try:
                registry = json.loads(bytes(reply.readAll()).decode("utf-8"))
                if isinstance(registry,dict) and registry:
                    self._manager.registry = registry
            except (ValueError,UnicodeError):
                logging.warning('Package registry response is invalid; falling back to bundled registry', exc_info=True)
        else:
            logging.warning('Package registry request failed: %s', reply.errorString())
        if not self._manager.registry and hasattr(self._manager, 'load_bundled_registry'):
            self._manager.registry = self._manager.load_bundled_registry()
        reply.deleteLater()
        self._load_rows(self._generation)

    def _fallback(self):
        reply, self._reply = self._reply, None
        if reply is not None:
            logging.warning('Package registry request timed out; falling back to bundled registry')
            reply.abort()
        if not self._manager.registry and hasattr(self._manager, 'load_bundled_registry'):
            self._manager.registry = self._manager.load_bundled_registry()
        self._load_rows(self._generation)

    def _job(self, operation, callback):
        job = PackageJob(operation,self)
        self._jobs[job] = callback
        job.done.connect(self._done)
        job.finished.connect(lambda:self._jobs.pop(job,None))
        job.finished.connect(job.deleteLater)
        job.start()

    @Slot(object,bool,object)
    def _done(self, job, ok, result):
        callback = self._jobs.get(job)
        if callback is not None:
            callback(ok,result)

    def _load_rows(self, generation):
        manager = self._manager
        def read():
            with self._lock:
                return manager.list_packages()
        def ready(ok, result):
            if generation!=self._generation:
                return
            self._loading = False
            self._error = not ok or not result
            self._replace(result if ok else [])
            if sys.platform == "win32":
                self._wsl = {"checking":True}
                self.changed.emit()
                if not self._error:
                    self._job(self._wsl_probe,lambda success,value:self._wsl_ready(success,value,generation))
            else:
                self._wsl = {"checking":False,"installed":False,"present":False}
                self.changed.emit()
        self._job(read,ready)

    def _replace(self, rows):
        self._rows.replace([{**row,"key":row["name"],"busy":row["name"] in self._busy,
                             "installError":row["name"] in self._failed} for row in rows])

    def _wsl_ready(self, ok, result, generation):
        if generation!=self._generation:
            return
        self._wsl = {**(result if ok else {"detail":str(result)}),"checking":False,"busy":False}
        self.changed.emit()

    @Slot(str)
    def install(self, name):
        if name in self._busy or self._manager is None:
            return
        self._busy.add(name); self._failed.discard(name)
        self._replace(self._rows.snapshot())
        manager = self._manager
        def operation():
            with self._lock:
                manager.install_package(name)
                return manager.list_packages()
        def done(ok, result):
            self._busy.discard(name)
            if not ok:
                self._failed.add(name)
            self._replace(result if ok else self._rows.snapshot())
            self.changed.emit()
        self._job(operation,done)

    @Slot()
    def installWsl(self):
        if self._wsl.get("busy"):
            return
        self._wsl = {**self._wsl,"busy":True}
        self.changed.emit()
        def done(ok, result):
            success,message = result if ok else (False,str(result))
            if success:
                self._job(self._wsl_probe,lambda success,value:self._wsl_ready(success,value,self._generation))
            else:
                if ok:logging.error('WSL installation failed: %s', message)
                self._wsl = {**self._wsl,"busy":False,"detail":message}
                self.changed.emit()
        self._job(wsl_manager.install,done)

    @Slot()
    def close(self):
        self._timeout.stop()
        reply,self._reply = self._reply,None
        if reply is not None:
            reply.abort()
        # Running installs retain this owner and finish safely after the window
        # closes. Application shutdown waits for them instead of deleting QThreads.
