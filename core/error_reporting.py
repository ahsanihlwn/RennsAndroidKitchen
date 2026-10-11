# SPDX-License-Identifier: MPL-2.0
# Queue errors until the current Qt callback has returned.
import logging

from PySide6.QtCore import QObject, QThread, QTimer, Qt, Signal, Slot
from PySide6.QtWidgets import QApplication


class _CrashReporter(QObject):
    requested = Signal(str)

    def __init__(self, app):
        super().__init__(app)
        self._pending = []
        self._active_logs = set()
        self._presenting = False
        self._scheduled = False
        self._dialog_failed = False
        self.requested.connect(self._enqueue, Qt.QueuedConnection)

    @Slot(str)
    def _enqueue(self, log_text):
        if self._dialog_failed or log_text in self._active_logs:
            return
        if log_text not in self._pending:
            self._pending.append(log_text)
        self._schedule()

    def _schedule(self):
        if self._pending and not self._presenting and not self._scheduled:
            self._scheduled = True
            QTimer.singleShot(0, self, self._show_pending)

    @Slot()
    def _show_pending(self):
        self._scheduled = False
        if self._presenting or not self._pending or QApplication.closingDown():
            return
        batch, self._pending = self._pending, []
        self._active_logs = set(batch)
        self._presenting = True
        try:
            from main_ext.class_AppDialog import AppDialog

            parent = QApplication.activeModalWidget() or QApplication.activeWindow()
            AppDialog.fatal_crash(parent, "\n\n".join(batch))
        except Exception:
            # A broken error dialog must not recursively open more error dialogs.
            logging.exception("Could not display the crash dialog")
            self._dialog_failed = True
            self._pending.clear()
        finally:
            self._presenting = False
            self._active_logs.clear()
            self._schedule()


def install_error_reporter():

    app = QApplication.instance()
    if not isinstance(app, QApplication) or QApplication.closingDown():
        return None
    reporter = getattr(app, "_fatal_error_reporter", None)
    if reporter is None and QThread.currentThread() == app.thread():
        reporter = app._fatal_error_reporter = _CrashReporter(app)
    return reporter


def report_fatal(log_text):

    reporter = install_error_reporter()
    if reporter is not None:
        reporter.requested.emit(str(log_text))
