# SPDX-License-Identifier: MPL-2.0
import json
import sys
import threading
import types
import unittest

from core import import_gate


class ImportGateTests(unittest.TestCase):
    def test_cached_import_can_complete_while_editor_owns_gate(self):
        acquired, release, completed = threading.Event(), threading.Event(), threading.Event()
        def editor():
            with import_gate._LOCK:
                acquired.set()
                release.wait(3)
        def gui():
            self.assertIs(import_gate._guarded_import('json', fromlist=('loads',)), json)
            completed.set()
        worker = threading.Thread(target=editor)
        reader = threading.Thread(target=gui)
        worker.start()
        self.assertTrue(acquired.wait(1))
        try:
            reader.start()
            self.assertTrue(completed.wait(1), 'Cached GUI imports blocked behind editor preparation')
        finally:
            release.set()
            worker.join(3)
            reader.join(3)

    def test_partial_and_lazy_imports_still_require_gate(self):
        module = types.ModuleType('_rak_gate_fixture')
        module.__spec__ = types.SimpleNamespace(_initializing=True)
        module.ready = True
        sys.modules[module.__name__] = module
        try:
            self.assertFalse(import_gate._already_loaded(module.__name__, (), 0))
            module.__spec__._initializing = False
            self.assertTrue(import_gate._already_loaded(module.__name__, ('ready',), 0))
            self.assertFalse(import_gate._already_loaded(module.__name__, ('missing',), 0))
            self.assertFalse(import_gate._already_loaded(module.__name__, ('*',), 0))
            self.assertFalse(import_gate._already_loaded(module.__name__, (), 1))
        finally:
            del sys.modules[module.__name__]


if __name__ == '__main__':
    unittest.main()
