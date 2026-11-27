# SPDX-License-Identifier: MPL-2.0
"""Integration tests for WorkspaceSession recursive OS watcher and terminal activity sync."""
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest

from PySide6.QtCore import QEvent
from PySide6.QtWidgets import QApplication

from core import filesystem_map
from core.terminal_execution import ProcessTerminalManager
from core.terminal_pty import PtySignals
from ui.presentation import LauncherModel
from ui.runtime import initialize_quick_runtime
from ui.terminal import TerminalSession
from ui.workspace import WorkspaceSession


class _FakePty:
    def __init__(self):
        self.signals = PtySignals()
        self.writes = []

    def start(self, **_):
        pass

    def shutdown(self):
        pass

    def resize(self, *_):
        pass

    def write(self, text):
        self.writes.append(text)


class WorkspaceSessionWatcherTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        initialize_quick_runtime(webengine=False)
        cls.app = QApplication.instance() or QApplication([])

    def _wait(self, pred, timeout=8.0):
        deadline = time.monotonic() + timeout
        while not pred():
            self.assertLess(time.monotonic(), deadline)
            self.app.processEvents()
            time.sleep(0.01)

    @unittest.skipUnless(sys.platform == "win32", "ReadDirectoryChangesW watcher is Windows-specific")
    def test_workspace_session_watcher_and_terminal_sync_and_clean_stop(self):
        filesystem_map.clear_memory_cache()
        temp = tempfile.TemporaryDirectory(prefix="rak-ws-watch-")
        self.addCleanup(temp.cleanup)
        self.addCleanup(filesystem_map.clear_memory_cache)

        root = Path(temp.name)
        port = root / "PORT"
        (port / "system" / "etc").mkdir(parents=True)
        (port / "system" / "etc" / "base.prop").write_text("base=1\n", encoding="utf-8")

        prefs = LauncherModel()
        self.addCleanup(prefs.deleteLater)

        session = WorkspaceSession(
            str(root),
            "PORT",
            None,
            preferences=prefs,
            settings_path=str(root / "settings.json"),
            terminal_factory=lambda cwd=None, **o: TerminalSession(manager=_FakePty(), **o),
            enable_private_services=False,
        )
        try:
            session.start()
            self._wait(lambda: session.prepared and not session.tree.busy, timeout=10.0)
            self.assertGreaterEqual(len(session._fs_watchers), 1)

            sys_path = str(port / "system")
            etc_path = str(port / "system" / "etc")
            sys_node = session.tree._nodes[sys_path]
            session.tree._scan(sys_node)
            self._wait(lambda: sys_node.loaded and not session.tree.busy)
            etc_node = session.tree._nodes[etc_path]
            session.tree._scan(etc_node)
            self._wait(lambda: etc_node.loaded and not session.tree.busy)

            # 1. External file creation triggers OS watcher -> filesystem_map + DirectoryModel update
            (Path(etc_path) / "from_shell.prop").write_text("shell=1\n", encoding="utf-8")
            self._wait(lambda: any(c.texts[0] == "from_shell.prop" for c in etc_node.children), timeout=5.0)
            self.assertEqual(session.tree.error, "")

            # 2. ProcessTerminalManager exited triggers debounced sync cleanly
            proc_mgr = ProcessTerminalManager(str(port))
            row_proc = session._create_terminal("tree1", cwd=str(port), title="Kitchen", manager=proc_mgr)
            self.assertIsNotNone(row_proc)
            proc_obj = object()
            proc_mgr.observe(proc_obj, "echo tool")
            proc_mgr.complete(proc_obj, 0)

            # 3. Interactive terminal command settles and triggers clean sync without error
            row_shell = session._create_terminal("tree1", cwd=str(port), title="Shell")
            self.assertIsNotNone(row_shell)
            settled_events = []
            row_shell["session"].commandActivitySettled.connect(lambda: settled_events.append(True))
            row_shell["session"].runCommand("ls")
            self._wait(lambda: bool(settled_events), timeout=3.0)
            self._wait(lambda: not session.tree.busy, timeout=3.0)
        finally:
            session.stop()
            self.assertEqual(session._fs_watchers, {})
            session.deleteLater()
            self.app.processEvents()
            QApplication.sendPostedEvents(None, QEvent.DeferredDelete)


if __name__ == "__main__":
    unittest.main()
