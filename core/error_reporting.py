# SPDX-License-Identifier: MPL-2.0
# Queue errors until the current Qt callback has returned.
import logging
import threading
import weakref
import sys
import traceback
from collections import deque

from PySide6.QtCore import QCoreApplication, QObject, QThread, QTimer, Qt, Signal, Slot
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication
import shiboken6


_presenters = []
_error_serial = 0
_recent_errors = deque(maxlen=256)
_error_lock = threading.RLock()


def error_checkpoint():
    with _error_lock:
        return _error_serial


def error_reported_since(checkpoint, text):
    text = str(text)
    with _error_lock:
        return any(serial > checkpoint and text in message for serial, message in _recent_errors)


def is_agent_error(text):
    if not text:
        return False
    s = str(text)
    return (
        s.startswith(('Agent ', 'Agent:', 'Agent\n', 'Agent\r'))
        or 'Agent chat' in s
        or 'Agent inbox' in s
        or 'Agent process' in s
    )


def _presenter_for(owner=None):
    while isinstance(owner, QObject) and shiboken6.isValid(owner):
        if any(reference() is owner for reference in _presenters) and not getattr(owner, '_closing', False):
            return owner
        owner = QObject.parent(owner)
    for reference in reversed(_presenters):
        presenter = reference()
        if presenter is not None and shiboken6.isValid(presenter) and not getattr(presenter, '_closing', False):
            return presenter
    return None


def _present_fatal(text, owner=None):
    if is_agent_error(text):
        return
    app = QCoreApplication.instance()
    if not isinstance(app, QGuiApplication) or QCoreApplication.closingDown():
        return
    presenter = _presenter_for(owner)
    if presenter is not None:
        presenter.fatalRequested.emit(text)
        return
    launcher = getattr(app, '_quick_launcher', None)
    if launcher is not None:
        launcher.fatalRequested.emit(text)
        return
    reporter = install_error_reporter()
    if reporter is not None:
        reporter.requested.emit(text)


def _present_operation(text,owner=None):
    if is_agent_error(text):
        return
    app=QCoreApplication.instance()
    if not isinstance(app,QGuiApplication) or QCoreApplication.closingDown():return
    presenter=_presenter_for(owner)
    if presenter is not None and hasattr(presenter,'operationErrorRequested'):
        presenter.operationErrorRequested.emit(text);return
    launcher=getattr(app,'_quick_launcher',None)
    if launcher is not None and hasattr(launcher,'operationErrorRequested'):
        launcher.operationErrorRequested.emit(text);return
    reporter=install_error_reporter()
    if reporter is not None:reporter.operationRequested.emit(text)


class _FatalLogHandler(logging.Handler):
    """Mirror every logged error to debug output and the active error UI."""
    def __init__(self):
        super().__init__(logging.ERROR)
        self._local = threading.local()

    def emit(self, record):
        global _error_serial
        if getattr(self._local, 'active', False):
            return
        self._local.active = True
        try:
            text = self.format(record)
            from core.command_context import current_command, is_application_exception
            command = current_command()
            application_error = getattr(record, 'fatal_application_error', None)
            if application_error is None:
                application_error = bool(record.exc_info and record.exc_info[1] and is_application_exception(record.exc_info[1]))
            if command is not None:
                command.record(text, application_error=application_error)
            with _error_lock:
                _error_serial += 1
                _recent_errors.append((_error_serial, text))
            try:
                print(('[FATAL] ' if application_error else '[RAK_CLI:ERROR] ' if command is not None else '[OPERATION ERROR] ') + text, flush=True)
            except (OSError, ValueError, RuntimeError):
                pass
            suppress_ui = (
                getattr(record, 'fatal_suppress_ui', False)
                or (getattr(record, 'name', '') or '').startswith(('ui.agent', 'core.agent', 'services.agent'))
                or getattr(record, 'name', '') in ('ui.inbox', 'core.agent_inbox')
                or is_agent_error(text)
            )
            if not suppress_ui and (command is None or application_error):
                owner=getattr(record,'fatal_owner',None)
                if application_error:_present_fatal(text,owner)
                else:_present_operation(text,owner)
        except Exception:
            logging.getLogger(__name__).exception('Could not present the fatal error')
        finally:
            self._local.active = False


def install_error_logging():
    root = logging.getLogger()
    if not any(isinstance(handler, _FatalLogHandler) for handler in root.handlers):
        root.addHandler(_FatalLogHandler())


def register_error_presenter(presenter):
    install_error_logging()
    _presenters[:] = [reference for reference in _presenters if reference() is not None and reference() is not presenter]
    _presenters.append(weakref.ref(presenter))


def unregister_error_presenter(presenter):
    _presenters[:] = [reference for reference in _presenters if reference() is not None and reference() is not presenter]


class _CrashReporter(QObject):
    requested = Signal(str)
    operationRequested = Signal(str)

    def __init__(self, app):
        super().__init__(app)
        self._pending = []
        self._active_logs = set()
        self._presenting = False
        self._scheduled = False
        self._dialog_failed = False
        self.requested.connect(self._enqueue, Qt.QueuedConnection)
        self.operationRequested.connect(self._enqueue_operation,Qt.QueuedConnection)

    @Slot(str)
    def _enqueue(self, log_text):
        self._enqueue_kind('fatal',log_text)

    @Slot(str)
    def _enqueue_operation(self,log_text):self._enqueue_kind('operation',log_text)

    def _enqueue_kind(self,kind,log_text):
        entry=(kind,log_text)
        if self._dialog_failed or entry in self._active_logs:
            return
        if entry not in self._pending:
            self._pending.append(entry)
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
        kind=self._pending[0][0]
        batch=[entry for entry in self._pending if entry[0]==kind]
        self._pending=[entry for entry in self._pending if entry[0]!=kind]
        self._active_logs = set(batch)
        self._presenting = True
        try:
            from main_ext.class_AppDialog import AppDialog

            parent = QApplication.activeModalWidget() or QApplication.activeWindow()
            text="\n\n".join(entry[1] for entry in batch)
            if kind=='fatal':AppDialog.fatal_crash(parent,text)
            else:AppDialog.operation_error(parent,text)
        except Exception:
            # A broken error dialog must not recursively open more error dialogs.
            self._dialog_failed = True
            self._pending.clear()
            logging.exception("Could not display the crash dialog")
        finally:
            self._presenting = False
            self._active_logs.clear()
            self._schedule()


def install_error_reporter():

    install_error_logging()
    app = QApplication.instance()
    if not isinstance(app, QApplication) or QApplication.closingDown():
        return None
    reporter = getattr(app, "_fatal_error_reporter", None)
    if reporter is None and QThread.currentThread() == app.thread():
        reporter = app._fatal_error_reporter = _CrashReporter(app)
    return reporter


def report_fatal(log_text, *, owner=None, application_error=None, suppress_ui=False):
    # Compatibility entry point: an ordinary string is never evidence of a
    # Python application defect. Preserve typed worker failures or an actual
    # exception; uncaught-exception boundaries explicitly mark their traceback.
    if application_error is None:
        application_error=getattr(log_text,'application_error',None)
        if application_error is None:
            from core.command_context import is_application_exception
            error=sys.exc_info()[1]
            application_error=error is not None and is_application_exception(error)
    text=str(log_text)
    if application_error and sys.exc_info()[1] is not None and 'Traceback (most recent call last)' not in text:
        text+='\n\n'+traceback.format_exc().rstrip()
    install_error_logging()
    suppress = bool(suppress_ui or is_agent_error(text))
    logging.getLogger(__name__).error('%s', text, extra={
        'fatal_owner': owner,
        'fatal_application_error': bool(application_error),
        'fatal_suppress_ui': suppress,
    })
