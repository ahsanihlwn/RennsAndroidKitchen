# SPDX-License-Identifier: MPL-2.0
import traceback
import time
import logging
from contextvars import copy_context
from PySide6.QtCore import (
    QCoreApplication,
    QObject,
    QThread,
    QRunnable,
    QThreadPool,
    QTimer,
    Qt,
    Signal,
    Slot,
)

from core.threader_control import threader_control

_active_threads = set()

print("[CORE STAGE] THREADER CALLED")


def has_active_threads():
    return bool(_active_threads)


def drain_background_work(app):
    """Keep Qt alive until owned workers and their queued callbacks finish."""
    from PySide6.QtCore import QEvent
    while _active_threads or _active_pool_signals or _thumb_pool.activeThreadCount():
        app.processEvents()
        _thumb_pool.waitForDone(10)
        for thread in tuple(_active_threads):thread.wait(10)
        QCoreApplication.sendPostedEvents(None,QEvent.DeferredDelete)
    app.processEvents()


class Worker(QObject):
    finished = Signal(bool, object)    # (success, payload)
    progress = Signal(float)        # optional (tidak dipakai erofs saat ini)

    def __init__(self, fn, arg):
        super().__init__()
        self.fn = fn        # fungsi normal (unpack_erofs / repack_erofs / dll)
        self.arg = arg      # argumen fungsi

    @Slot()
    def run(self):
        print("[THREAD] worker.run() START")
        try:
            # Call zero-argument workers without passing None.
            if self.arg is None:
                ok, msg = self.fn()
            else:
                ok, msg = self.fn(self.arg)
            print("[THREAD] worker.run() END")
        except Exception as e:
            from core.command_context import failure_from_exception
            ok,msg=False,failure_from_exception(e)
        self.finished.emit(ok, msg)


class _ThreadRelay(QObject):
    def __init__(self, thread, worker, callback):
        super().__init__()
        self._thread = thread
        self._worker = worker
        self.callback = callback
        self._has_result = False
        self._ok = False
        self._payload = None
        self._thread_finished = False
        self._delivered = False

    @Slot(bool, object)
    def on_worker_finished(self, ok, payload):
        if self._delivered:
            return
        self._has_result = True
        self._ok = ok
        self._payload = payload
        self._try_finish()

    @Slot()
    def on_thread_finished(self):
        if self._delivered:
            return
        self._thread_finished = True
        self._try_finish()

    def _try_finish(self):
        if self._delivered:
            return
        cancelled = getattr(self._thread, "_callbacks_cancelled", False)
        if not self._thread_finished:
            return
        if not self._has_result and not cancelled:
            return
        self._delivered = True
        strong_relay = self
        try:
            if self.callback and not cancelled:
                from core.error_reporting import error_checkpoint, error_reported_since, report_fatal
                checkpoint = error_checkpoint()
                try:
                    self.callback(self._ok, self._payload)
                finally:
                    if not self._ok and not error_reported_since(checkpoint, self._payload) and str(self._payload) != 'Cancelled':
                        report_fatal('Background worker failed\n\n'+str(self._payload),application_error=getattr(self._payload,'application_error',False))
        except Exception as e:
            trace = traceback.format_exc()
            from core.error_reporting import report_fatal
            report_fatal('Worker completion callback failed\n\n'+trace)
        finally:
            control_thread = self._thread
            worker = self._worker
            if worker is not None:
                try:
                    worker.fn = None
                    worker.arg = None
                except Exception:
                    pass
            if control_thread is not None:
                try:
                    control_thread.wait()
                except Exception:
                    pass
                _active_threads.discard(control_thread)
                control_thread.deleteLater()
                if hasattr(control_thread, "_completion_relay"):
                    try:
                        del control_thread._completion_relay
                    except AttributeError:
                        pass
            self.deleteLater()
            self.callback = None
            self._worker = None
            self._thread = None


def run_in_thread(fn, arg, callback=None):
    app = QCoreApplication.instance()
    if app is None:
        raise RuntimeError("QCoreApplication must be initialized before running background tasks")
    app_thread = app.thread()

    thread = QThread()
    worker = Worker(fn, arg)
    relay = _ThreadRelay(thread, worker, callback)

    relay.moveToThread(app_thread)
    thread.moveToThread(app_thread)
    worker.moveToThread(thread)

    # SIMPAN BIAR TIDAK KE-GC
    _active_threads.add(thread)
    thread._completion_relay = relay

    worker.finished.connect(relay.on_worker_finished, Qt.QueuedConnection)
    worker.finished.connect(thread.quit, Qt.DirectConnection)
    thread.finished.connect(relay.on_thread_finished, Qt.QueuedConnection)
    thread.finished.connect(worker.deleteLater)

    thread.started.connect(worker.run)
    thread.start()

    return thread, worker


_thumb_pool = QThreadPool()
_thumb_pool.setMaxThreadCount(threader_control.pool_limit())
_active_pool_signals = set()


def sync_pool_limits(limit=None):
    """Synchronize QThreadPool thread limit with hardware-aware threader_control."""
    target = int(limit) if limit is not None else threader_control.pool_limit()
    _thumb_pool.setMaxThreadCount(max(1, target))
    return _thumb_pool.maxThreadCount()


threader_control.register_pool_hook(sync_pool_limits)


class _PoolSignals(QObject):
    finished = Signal(object, bool, object)   # (token, success, payload)

    def __init__(self, callback=None):
        super().__init__()
        self.callback = callback
        self.cancelled = False

    @Slot(object, bool, object)
    def on_finished(self, tok, ok, payload):
        try:
            if self.callback and not self.cancelled:
                self.callback(tok, ok, payload)
        except Exception as e:
            trace = traceback.format_exc()
            from core.error_reporting import report_fatal
            report_fatal('Pool completion callback failed\n\n'+trace)
        finally:
            _active_pool_signals.discard(self)
            self.deleteLater()


class _PoolRunnable(QRunnable):


    def __init__(self, token, fn, arg, signals):
        super().__init__()
        self.token = token
        self.fn = fn
        self.arg = arg
        self.signals = signals

    def run(self):
        try:
            if self.signals.cancelled:
                ok, payload = False, "Task cancelled"
            else:
                ok, payload = (self.fn() if self.arg is None else self.fn(self.arg))
        except Exception as e:
            from core.command_context import failure_from_exception
            ok,payload=False,failure_from_exception(e)
        try:
            self.signals.finished.emit(self.token, ok, payload)
        except RuntimeError:
            pass


def run_in_pool(token, fn, arg, callback):

    app = QCoreApplication.instance()
    if app is None:
        raise RuntimeError("QCoreApplication must be initialized before running background tasks")
    app_thread = app.thread()

    context = copy_context()
    def scoped_callback(*args):
        def deliver():
            try:return callback(*args)
            except Exception:
                from core.error_reporting import report_fatal
                report_fatal('Pool completion callback failed\n\n'+traceback.format_exc())
        return context.copy().run(deliver)
    def scoped_work():
        def execute():
            try:return fn() if arg is None else fn(arg)
            except Exception as error:
                from core.command_context import failure_from_exception
                return False,failure_from_exception(error)
        return context.copy().run(execute)
    signals = _PoolSignals(scoped_callback)
    signals.moveToThread(app_thread)
    _active_pool_signals.add(signals)

    signals.finished.connect(signals.on_finished)

    sync_pool_limits()
    runnable = _PoolRunnable(token, scoped_work, None, signals)
    runnable.setAutoDelete(True)
    _thumb_pool.start(runnable)


def has_active_pool_jobs():
    return bool(_active_pool_signals)


def quit_when_idle():

    app = QCoreApplication.instance()
    if app is None:
        return
    if has_active_threads() or has_active_pool_jobs():
        QTimer.singleShot(50, app, quit_when_idle)
        return
    app.quit()


def stop_all_threads(timeout_ms=800):

    for signals in tuple(_active_pool_signals):
        signals.cancelled = True
    threads = list(_active_threads)
    if not threads:
        return

    for thread in threads:
        try:
            thread._callbacks_cancelled = True
            thread.requestInterruption()
            thread.quit()
        except Exception:
            pass

    deadline = time.monotonic() + max(0, timeout_ms) / 1000
    for thread in threads:
        try:
            remaining = max(0, round((deadline - time.monotonic()) * 1000))
            if remaining:
                thread.wait(remaining)
        except Exception as e:
            print(f"[THREAD] stop_all_threads error: {e}")

    # GUI cleanup removes each thread only after it has actually finished.
