# SPDX-License-Identifier: MPL-2.0
from collections import OrderedDict
import weakref

import shiboken6
from PySide6.QtCore import QObject
from core.threader import run_in_pool


class AsyncJobs(QObject):
    def __init__(self, parent, *, limit=2):
        super().__init__(parent)
        self.limit = limit
        self._serial = 0
        self._pending = OrderedDict()
        self._running = {}
        self._latest = {}
        self._stopped = False

    def submit(self, key, fn, arg, callback):
        if self._stopped:
            return None
        self._serial += 1
        serial = self._serial
        self._latest[key] = serial
        self._pending[key] = (serial, fn, arg, callback)
        self._pump()
        return serial

    def _pump(self):
        while not self._stopped and len(self._running) < self.limit:
            key = next((key for key in self._pending if key not in self._running), None)
            if key is None:
                return
            serial, fn, arg, callback = self._pending.pop(key)
            self._running[key] = serial
            owner_ref = weakref.ref(self)
            def finished(token, ok, payload, key=key, serial=serial, callback=callback):
                owner = owner_ref()
                if owner is None or not shiboken6.isValid(owner):
                    return
                owner._running.pop(key, None)
                try:
                    if not owner._stopped and owner._latest.get(key) == serial:
                        owner._latest.pop(key, None)
                        callback(ok, payload)
                finally:
                    owner._pump()
            run_in_pool((key, serial), fn, arg, finished)

    def reset(self):
        self._pending.clear()
        self._latest.clear()

    def stop(self):
        self._stopped = True
        self.reset()

    def busy(self):
        return bool(self._pending or self._running)
