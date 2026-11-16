# SPDX-License-Identifier: MPL-2.0
import unittest
from unittest.mock import patch

from core.terminal_pty import PtySignals
from ui.editor import EditorSession
from ui.terminal import TerminalSession


class FakePty:
    def __init__(self, fail=False):
        self.signals = PtySignals()
        self.fail = fail
        self.starts = 0
        self.stops = 0
        self.writes = []
    def start(self, **kwargs):
        self.starts += 1
        if self.fail:
            raise RuntimeError("fixture start failure")
    def shutdown(self):
        self.stops += 1
    def resize(self, *args):
        pass
    def write(self, text):
        self.writes.append(text)


class QuickSessionTests(unittest.TestCase):
    def test_editor_reload_keeps_unsaved_buffers_and_active_tab(self):
        session = EditorSession()
        self.assertEqual(session.backend, "quill")
        with patch.object(session.bridge, "_resolve_icon_b64", return_value=""):
            session.bridge.open_virtual("first.prop", "first", icon_b64="fixture")
            session.bridge.open_virtual("second.prop", "second", icon_b64="fixture")
            session.bridge.on_active_changed("first.prop")
            session.bridge.on_content_changed("first.prop", "unsaved buffer")
            session.reloadTheme()
        self.assertTrue(session.bridge.is_unsaved("first.prop"))
        self.assertEqual(session.bridge._tabs["first.prop"]["content"], "unsaved buffer")
        session.suspend()
        self.assertFalse(session.active)

    def test_terminal_starts_lazily_and_closes_once(self):
        manager = FakePty()
        session = TerminalSession(manager=manager)
        self.assertEqual(manager.starts, 0)
        self.assertTrue(session.start())
        self.assertTrue(session.start())
        self.assertEqual(manager.starts, 1)
        session.runCommand("echo fixture")
        self.assertEqual(manager.writes, [])
        session.bridge.onTerminalReady(80, 24)
        self.assertEqual(manager.writes, ["echo fixture\r\n"])
        session.stop()
        session.stop()
        self.assertEqual(manager.stops, 1)

    def test_failed_terminal_start_cleans_up_without_an_active_view(self):
        manager = FakePty(fail=True)
        session = TerminalSession(manager=manager)
        errors = []
        session.warningRaised.connect(lambda *args: errors.append(args))
        self.assertFalse(session.start())
        self.assertFalse(session.active)
        self.assertEqual(manager.stops, 1)
        self.assertEqual(errors, [("Terminal", "fixture start failure")])

    def test_dropped_file_urls_decode_and_quote_without_running_a_command(self):
        from PySide6.QtCore import QUrl
        manager=FakePty();session=TerminalSession(manager=manager)
        self.addCleanup(session.stop);self.assertTrue(session.start())
        session.insertDroppedPaths([QUrl.fromLocalFile('C:/ROM Folder/a&b.txt').toString(),
                                    QUrl.fromLocalFile('D:/port/source.prop')])
        self.assertEqual(manager.writes,['"C:\\ROM Folder\\a&b.txt" D:\\port\\source.prop '])

    def test_dropped_file_url_uses_wsl_path_and_shell_quoting(self):
        from PySide6.QtCore import QUrl
        manager=FakePty();session=TerminalSession(manager=manager,wsl_mode=True)
        self.addCleanup(session.stop);self.assertTrue(session.start())
        session.insertDroppedPaths([QUrl.fromLocalFile('D:/ROM Folder/build.prop').toString()])
        self.assertEqual(manager.writes,["'/mnt/d/ROM Folder/build.prop' "])


if __name__ == "__main__":
    unittest.main()
