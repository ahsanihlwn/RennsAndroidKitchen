# SPDX-License-Identifier: MPL-2.0
"""Verify Kitchen process console logs quietly without auto-opening closed terminals."""
from pathlib import Path
import tempfile
import time
import unittest

from PySide6.QtCore import QUrl
from PySide6.QtQml import QQmlApplicationEngine, QQmlComponent
from PySide6.QtQuick import QQuickItem
from PySide6.QtWidgets import QApplication

from core.qtterm import Terminal
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

    def start(self, **kwargs):
        pass

    def shutdown(self):
        pass

    def resize(self, *args):
        pass

    def write(self, text):
        self.writes.append(text)


class ProcessTerminalBehaviorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        initialize_quick_runtime(webengine=False)
        cls.app = QApplication.instance() or QApplication([])

    def _make_session(self):
        temp = tempfile.TemporaryDirectory(prefix='rak-proc-term-')
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        (root / 'PORT').mkdir()
        (root / 'STOCK').mkdir()
        (root / 'PORT' / 'script.py').write_text('print("hi")\n', encoding='utf-8')
        prefs = LauncherModel()
        self.addCleanup(prefs.deleteLater)
        session = WorkspaceSession(
            str(root),
            'PORT',
            'STOCK',
            preferences=prefs,
            settings_path=str(root / 'settings.json'),
            terminal_factory=lambda cwd=None, **options: TerminalSession(manager=_FakePty(), **options),
            enable_private_services=False,
        )
        self.addCleanup(session.stop)
        return root, session

    def test_process_terminal_does_not_open_closed_terminal_or_steal_shell_tab(self):
        _, session = self._make_session()
        session.prepareTerminal()
        shell_key = session.activeTerminal
        self.assertTrue(shell_key)
        self.assertFalse(session.terminalOpen)
        self.assertFalse(session.rightOpen)
        initial_layout = session.layoutMode

        manager = session.processTerminal()
        self.assertIsNotNone(manager)
        self.assertFalse(session.terminalOpen)
        self.assertFalse(session.rightOpen)
        self.assertEqual(session.layoutMode, initial_layout)
        self.assertEqual(session.activeTerminal, shell_key)
        self.assertIsNotNone(session._process_terminal_key)
        self.assertNotEqual(session._process_terminal_key, shell_key)

    def test_process_terminal_before_prepare_leaves_interactive_shell_separate(self):
        _, session = self._make_session()
        manager = session.processTerminal()
        self.assertIsNotNone(manager)
        self.assertFalse(session.terminalOpen)
        self.assertEqual(session.activeTerminal, '')

        session.toggleTerminal()
        self.assertTrue(session.terminalOpen)
        self.assertTrue(session.activeTerminal)
        self.assertNotEqual(session.activeTerminal, session._process_terminal_key)

    def test_process_terminal_switches_to_kitchen_tab_when_terminal_already_open(self):
        _, session = self._make_session()
        session.toggleTerminal()
        shell_key = session.activeTerminal
        self.assertTrue(session.terminalOpen)

        manager = session.processTerminal()
        self.assertIsNotNone(manager)
        self.assertTrue(session.terminalOpen)
        self.assertEqual(session.activeTerminal, session._process_terminal_key)
        self.assertNotEqual(session.activeTerminal, shell_key)

    def test_kitchen_renders_buffered_and_live_output_without_starting_a_shell(self):
        manager = ProcessTerminalManager('.')
        session = TerminalSession(manager=manager)
        terminal = Terminal()
        self.addCleanup(session.stop)
        self.assertTrue(session.start())
        manager.feed('buffered log\n')

        session.bindTerminal(terminal)
        manager.feed('live log\n')

        self.assertFalse(terminal.running)
        self.assertIn('buffered log\nlive log', terminal.screenText())

    def test_run_active_still_opens_terminal_and_selects_script_tab(self):
        root, session = self._make_session()
        script = str(root / 'PORT' / 'script.py')
        session.openPath(script)
        self.assertFalse(session.terminalOpen)

        session.runActive()
        self.assertTrue(session.terminalOpen)
        self.assertIn(session.activeTerminal, [row['key'] for row in session._terminals])

    def test_terminal_header_uses_native_close_rounded_add_next_to_tab_and_no_spam_enter(self):
        repo = Path(__file__).resolve().parents[1]
        _, session = self._make_session()
        prefs = session.preferences
        prefs.chooseSetting('animations', False)
        engine = QQmlApplicationEngine()
        comp = QQmlComponent(engine, QUrl.fromLocalFile(str(repo / 'ui' / 'Workspace.qml')))
        self.assertEqual(comp.status(), QQmlComponent.Status.Ready, comp.errorString())
        win = comp.createWithInitialProperties({'workspace': session, 'preferences': prefs})
        self.assertIsNotNone(win, comp.errorString())
        try:
            def find_visual(name):
                pending = [win.contentItem()]
                while pending:
                    item = pending.pop()
                    if item.objectName() == name:
                        return item
                    pending.extend(item.childItems())
                return None

            win.show()
            session.start()
            deadline = time.monotonic() + 10.0
            while not session.prepared:
                self.assertLess(time.monotonic(), deadline)
                self.app.processEvents()
                time.sleep(0.01)
            session.toggleTerminal()
            deadline = time.monotonic() + 5.0
            while not session.activeTerminal or find_visual('workspaceTab_' + session.activeTerminal) is None:
                self.assertLess(time.monotonic(), deadline)
                self.app.processEvents()
                time.sleep(0.01)
            for _ in range(10):
                self.app.processEvents()
                time.sleep(0.01)

            self.assertIsNone(find_visual('workspaceSpamEnter'))
            tabs = find_visual('workspaceTerminalTabs')
            new_btn = find_visual('workspaceNewTerminal')
            root_sel = find_visual('workspaceRootSelector')
            close_btn = find_visual('closeWorkspaceTab_' + session.activeTerminal)
            self.assertIsNotNone(tabs)
            self.assertIsNotNone(new_btn)
            self.assertIsNotNone(root_sel)
            self.assertIsNotNone(close_btn)

            self.assertFalse(new_btn.property('circular'))
            self.assertEqual(new_btn.property('cornerRadius'), 6)
            self.assertEqual(close_btn.property('iconKey'), 'close_rounded')

            first_tab = find_visual('workspaceTab_' + session.activeTerminal)
            self.assertIsNotNone(first_tab)
            self.assertTrue(first_tab.property('nativeTabIcon'))
            native_icons = [c for c in first_tab.findChildren(QQuickItem) if c.property('iconKey') == 'terminal']
            svg_images = [c for c in first_tab.findChildren(QQuickItem) if c.metaObject().className().startswith('QQuickImage')]
            self.assertEqual(len(native_icons), 1)
            self.assertTrue(native_icons[0].isVisible())
            self.assertEqual(len(svg_images), 1)
            self.assertFalse(svg_images[0].isVisible())
            self.assertEqual(svg_images[0].property('source').toString(), '')
            self.assertAlmostEqual(new_btn.x(), min(tabs.property('contentWidth'), tabs.width()) + 6, delta=1.0)
            self.assertAlmostEqual(new_btn.x(), first_tab.width() + 6, delta=1.0)

            session.addTerminal()
            for _ in range(10):
                self.app.processEvents()
                time.sleep(0.01)
            second_tab = find_visual('workspaceTab_' + session.activeTerminal)
            self.assertIsNotNone(second_tab)
            self.assertTrue(second_tab.property('nativeTabIcon'))
            second_native_icons = [c for c in second_tab.findChildren(QQuickItem) if c.property('iconKey') == 'terminal']
            self.assertEqual(len(second_native_icons), 1)
            self.assertTrue(second_native_icons[0].isVisible())
            self.assertAlmostEqual(new_btn.x(), min(tabs.property('contentWidth'), tabs.width()) + 6, delta=1.0)
            self.assertLess(new_btn.x() + new_btn.width(), root_sel.x())
        finally:
            win.hide()
            win.deleteLater()
            engine.deleteLater()


if __name__ == '__main__':
    unittest.main()
