# SPDX-License-Identifier: MPL-2.0
"""Event-driven Source recovery shared by project loading and task recovery."""
import threading

from PySide6.QtCore import QObject, Property, Signal, Slot

from core.workspace_preparation import prepare_sources
from main_ext.class_NeoBranchManager import SourceRestoreError, SourceRecoveryCancelled, _retryable_fs_error
from services.async_jobs import AsyncJobs


class SourceRecovery(QObject):
    activeChanged = Signal()
    progress = Signal(str)
    finished = Signal(bool, object)

    def __init__(self, bases, parent=None):
        super().__init__(parent)
        self.bases = tuple(bases)
        self._active = False
        self._closed = False
        self._cancel = threading.Event()
        self._jobs = AsyncJobs(self, limit=1)

    @Property(bool, notify=activeChanged)
    def active(self):
        return self._active

    @Slot()
    def start(self):
        if self._closed or self._active:
            return
        self._active = True
        self._cancel.clear()
        self.activeChanged.emit()
        self.progress.emit('Checking Source recovery...')

        def progress(text):
            if not self._cancel.is_set():
                self.progress.emit(text)

        def work():
            for attempt in range(3):
                try:
                    return prepare_sources(self.bases, progress=progress, cancelled=self._cancel.is_set)
                except SourceRestoreError as error:
                    cause = error
                    retryable = False
                    while cause is not None:
                        if _retryable_fs_error(cause):retryable = True;break
                        cause = cause.__cause__
                    if not retryable or attempt == 2:raise
                    progress('Recovering Source: '+error.root+' — waiting for files to be released')
                    if self._cancel.wait(.25*(attempt+1)):raise SourceRecoveryCancelled('Source recovery cancelled')

        def done(ok, result):
            if self._closed:
                return
            self._active = False
            self.activeChanged.emit()
            self.finished.emit(ok, result)

        self._jobs.submit('source-recovery', work, None, done)

    def close(self):
        self._closed = True
        self._cancel.set()
        self._jobs.stop()
        if self._active:
            self._active = False
            self.activeChanged.emit()

