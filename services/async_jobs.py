# SPDX-License-Identifier: MPL-2.0
from collections import OrderedDict
from contextvars import copy_context
import weakref

import shiboken6
from PySide6.QtCore import QObject
from core.threader import run_in_pool
from core.threader_control import threader_control


class AsyncJobs(QObject):
    def __init__(self, parent, *, limit=2):
        super().__init__(parent)
        self.limit = limit
        self._serial = 0
        self._pending = OrderedDict()
        self._running = {}
        self._latest = {}
        self._stopped = False

    def effective_limit(self):
        if self.limit is None:
            return max(1, threader_control.pool_limit())
        return max(1, int(self.limit))

    def submit(self, key, fn, arg, callback, *, priority=False, report_operation_errors=True):
        if self._stopped:
            return None
        self._serial += 1
        serial = self._serial
        self._latest[key] = serial
        self._pending[key] = (serial, fn, arg, callback, copy_context(), report_operation_errors)
        if priority:self._pending.move_to_end(key,last=False)
        self._pump()
        return serial

    def _pump(self):
        while not self._stopped and len(self._running) < self.effective_limit():
            key = next((key for key in self._pending if key not in self._running), None)
            if key is None:
                return
            serial, fn, arg, callback, context, report_operations = self._pending.pop(key)
            self._running[key] = serial
            owner_ref = weakref.ref(self)
            def finished(token, ok, payload, key=key, serial=serial, callback=callback, report_operations=report_operations):
                owner = owner_ref()
                if owner is None or not shiboken6.isValid(owner):
                    return
                owner._running.pop(key, None)
                try:
                    if not owner._stopped and owner._latest.get(key) == serial:
                        owner._latest.pop(key, None)
                        from core.error_reporting import error_checkpoint, error_reported_since, report_fatal
                        checkpoint = error_checkpoint()
                        try:
                            callback(ok, payload)
                        finally:
                            if not ok and (report_operations or getattr(payload,'application_error',False)) and not error_reported_since(checkpoint, payload) and str(payload) != 'Cancelled' and not str(payload).startswith('Queue halted:'):
                                report_fatal('Background task: '+str(key)+'\n\n'+str(payload), owner=owner,
                                             application_error=getattr(payload,'application_error',False))
                finally:
                    owner._pump()
            context.run(run_in_pool, (key, serial), fn, arg, finished)

    def reset(self):
        self._pending.clear()
        self._latest.clear()

    def stop(self):
        self._stopped = True
        self.reset()

    def busy(self):
        return bool(self._pending or self._running)
