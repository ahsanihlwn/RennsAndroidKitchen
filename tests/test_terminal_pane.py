# SPDX-License-Identifier: MPL-2.0
"""Test suite for native TerminalPane.qml integration with TerminalSession."""
import os
import sys
import time
import unittest
from pathlib import Path

from PySide6.QtGui import QGuiApplication, QColor
from PySide6.QtQml import QQmlApplicationEngine, QQmlComponent
from PySide6.QtCore import QUrl, QTimer

from ui.runtime import initialize_quick_runtime
from ui.terminal import TerminalSession


class TerminalPaneIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        initialize_quick_runtime(webengine=False)
        cls.app = QGuiApplication.instance() or QGuiApplication(sys.argv)

    def setUp(self):
        self.engine = QQmlApplicationEngine()
        root_dir = Path(__file__).resolve().parent.parent
        self.engine.addImportPath(str(root_dir / "ui"))
        self.engine.addImportPath(str(root_dir / "ui" / "controls"))

    def tearDown(self):
        self.engine = None

    def test_terminal_pane_loads_and_binds_session(self):
        session = TerminalSession()
        self.addCleanup(session.stop)
        self.assertTrue(session.start())
        session.applyTheme({
            "is_light": False,
            "BG": "#1e1e2e",
            "FG": "#cdd6f4",
            "ACCENT": "#38bdf8",
            "BG_SELECTED": "rgba(56, 189, 248, 0.35)",
        })

        self.engine.rootContext().setContextProperty("testSession", session)

        qml = """
        import QtQuick
        import "ui/controls"

        Item {
            width: 800; height: 600
            TerminalPane {
                id: pane
                objectName: "testTerminalPane"
                anchors.fill: parent
                session: testSession
                fillColor: "#1e1e2e"
            }
        }
        """
        comp = QQmlComponent(self.engine)
        comp.setData(qml.encode("utf-8"), QUrl.fromLocalFile(str(Path(__file__).resolve().parent.parent / "test.qml")))
        self.assertFalse(comp.isError(), [e.toString() for e in comp.errors()])

        root = comp.create()
        self.assertIsNotNone(root)

        pane = root.findChild(object, "testTerminalPane")
        self.assertIsNotNone(pane)

        # Check native terminal view child
        term = root.findChild(object, "quickTerminalView")
        self.assertIsNotNone(term)

        # Check that session was bound to terminal item
        self.assertIsNotNone(session._term_item)
        self.assertEqual(session._term_item, term)

        # Process Qt events
        for _ in range(20):
            self.app.processEvents()
            time.sleep(0.02)

        # Terminal should be running native ConPTY process
        self.assertTrue(term.running)

        # Run command via session
        session.runCommand("echo TEST_PANE_SUCCESS")
        found_echo = False
        for _ in range(40):
            self.app.processEvents()
            text = term.screenText()
            if "TEST_PANE_SUCCESS" in text:
                found_echo = True
                break
            time.sleep(0.05)

        self.assertTrue(found_echo, f"Command output not found: {term.screenText()!r}")

        # Feed direct text
        session._term_item.feedText("\r\nFEED_INTO_PANE\r\n")
        self.app.processEvents()
        self.assertIn("FEED_INTO_PANE", term.screenText())


if __name__ == "__main__":
    unittest.main()
