# SPDX-License-Identifier: MPL-2.0
"""Verify tree column cells and Name/Size entry layout stay aligned while moving/resizing."""
from pathlib import Path
import tempfile
import time
import unittest

from PySide6.QtCore import QUrl
from PySide6.QtQml import QQmlApplicationEngine, QQmlComponent
from PySide6.QtWidgets import QApplication

from core.terminal_pty import PtySignals
from ui.presentation import LauncherModel
from ui.runtime import initialize_quick_runtime
from ui.terminal import TerminalSession
from ui.workspace import WorkspaceSession


class _FakePty:
    def __init__(self):
        self.signals = PtySignals()
    def start(self, **_): pass
    def shutdown(self): pass
    def resize(self, *_): pass
    def write(self, _): pass


class TreeColumnSyncTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        initialize_quick_runtime(webengine=False)
        cls.app = QApplication.instance() or QApplication([])

    def test_entry_layout_width_and_selinux_column_stay_synced_during_animation(self):
        repo = Path(__file__).resolve().parents[1]
        temp = tempfile.TemporaryDirectory(prefix='rak-tree-sync-')
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        (root / 'PORT').mkdir()
        (root / 'STOCK').mkdir()
        (root / 'PORT' / 'sample_file.txt').write_text('hello world\n', encoding='utf-8')

        prefs = LauncherModel()
        self.addCleanup(prefs.deleteLater)
        prefs.chooseSetting('animations', True)

        session = WorkspaceSession(
            str(root),
            'PORT',
            'STOCK',
            preferences=prefs,
            settings_path=str(root / 'settings.json'),
            terminal_factory=lambda cwd=None, **o: TerminalSession(manager=_FakePty(), **o),
            enable_private_services=False,
        )
        self.addCleanup(session.stop)

        engine = QQmlApplicationEngine()
        comp = QQmlComponent(engine, QUrl.fromLocalFile(str(repo / 'ui' / 'Workspace.qml')))
        self.assertEqual(comp.status(), QQmlComponent.Status.Ready, comp.errorString())
        win = comp.createWithInitialProperties({'workspace': session, 'preferences': prefs})
        self.assertIsNotNone(win, comp.errorString())

        def find_all(pred):
            out = []
            stack = [win.contentItem()]
            while stack:
                item = stack.pop()
                if pred(item):
                    out.append(item)
                stack.extend(item.childItems())
            return out

        try:
            win.show()
            session.start()
            deadline = time.monotonic() + 10.0
            while not session.prepared:
                self.assertLess(time.monotonic(), deadline)
                self.app.processEvents()
                time.sleep(0.01)
            for _ in range(15):
                self.app.processEvents()
                time.sleep(0.01)

            tree = find_all(lambda it: it.objectName() == 'workspaceTree0')[0]
            col0 = find_all(lambda it: it.objectName() == 'workspaceTreeColumn0_0')[0]
            col1 = find_all(lambda it: it.objectName() == 'workspaceTreeColumn0_1')[0]
            col2 = find_all(lambda it: it.objectName() == 'workspaceTreeColumn0_2')[0]

            session.toggleLayout()
            for _ in range(16):
                self.app.processEvents()
                time.sleep(0.02)
                entries = find_all(lambda it: it.objectName() == 'treeEntryName')
                self.assertTrue(entries)
                entry = entries[0].parentItem()
                self.assertEqual(entry.property('layoutWidth'), int(entry.width()))

                cell0 = entry.parentItem().parentItem()
                row_cells = sorted(
                    [it for it in tree.property('contentItem').childItems() if abs(it.y() - cell0.y()) < 0.5],
                    key=lambda item: item.x(),
                )
                self.assertEqual(len(row_cells), 3)
                self.assertAlmostEqual(row_cells[0].width(), col0.width(), delta=0.5)
                self.assertAlmostEqual(row_cells[1].x(), col1.x(), delta=0.5)
                self.assertAlmostEqual(row_cells[1].width(), col1.width(), delta=0.5)
                self.assertAlmostEqual(row_cells[2].x(), col2.x(), delta=0.5)
                self.assertAlmostEqual(row_cells[2].width(), col2.width(), delta=0.5)
        finally:
            win.hide()
            win.deleteLater()
            engine.deleteLater()


if __name__ == '__main__':
    unittest.main()
