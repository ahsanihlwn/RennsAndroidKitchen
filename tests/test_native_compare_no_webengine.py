# SPDX-License-Identifier: MPL-2.0
"""Verify native Rust Compare Diff Viewer runs without loading QtWebEngine."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from pathlib import Path
import sys
import unittest
from types import SimpleNamespace

from PySide6.QtCore import QObject, QUrl, Qt
from PySide6.QtGui import QColor, QGuiApplication, QImage, QKeyEvent, QPainter
from PySide6.QtQml import QQmlApplicationEngine, QQmlComponent

from ui.compare import DiffHtml, WorkspaceCompare
from ui.quill_editor import NativeDiffEditor
from ui.runtime import initialize_quick_runtime


class _MockWorkspace(QObject):
    def __init__(self):
        super().__init__()
        self.p1 = "a"
        self.p2 = "b"


class _MockPrivate(QObject):
    def __init__(self):
        super().__init__()
        self.workspace = _MockWorkspace()


class _MockEntry:
    def __init__(self, rel, *, is_binary=False, lines_a=None, lines_b=None, opcodes=None, hex_a=None, hex_b=None):
        self.rel = rel
        self.is_binary = is_binary
        self.lines_a = lines_a or []
        self.lines_b = lines_b or []
        self.opcodes = opcodes or []
        self.hex_a = hex_a or []
        self.hex_b = hex_b or []


class NativeCompareNoWebEngineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QGuiApplication.instance() or QGuiApplication(sys.argv)
        initialize_quick_runtime(webengine=False)

    def test_no_webengine_imported_on_runtime_init(self):
        for mod in (
            "PySide6.QtWebEngineQuick",
            "PySide6.QtWebEngineCore",
            "PySide6.QtWebEngineWidgets",
            "PySide6.QtWebChannel",
        ):
            self.assertNotIn(mod, sys.modules, f"{mod} should not be loaded")

    def test_diff_html_builds_aligned_text_and_hex_sides(self):
        private = _MockPrivate()
        cmp = WorkspaceCompare(private)
        cmp._entries = [
            _MockEntry(
                "system/build.prop",
                lines_a=["ro.a=1\n", "ro.old=2\n", "ro.same=3\n"],
                lines_b=["ro.a=1\n", "ro.new=2\n", "ro.extra=9\n", "ro.same=3\n"],
                opcodes=[
                    ("equal", 0, 1, 0, 1),
                    ("replace", 1, 2, 1, 3),
                    ("equal", 2, 3, 3, 4),
                ],
            ),
            _MockEntry(
                "system/bin/app_process",
                is_binary=True,
                hex_a=["0000: 01 02", "0010: aa bb"],
                hex_b=["0000: 01 02", "0010: cc bb", "0020: ff"],
            ),
        ]

        cmp.select(0)
        self.assertTrue(cmp.web.active)
        a = cmp.web.sideA
        b = cmp.web.sideB
        self.assertEqual(a["marks"], ["normal", "changed", "ghost", "normal"])
        self.assertEqual(b["marks"], ["normal", "changed", "changed", "normal"])
        self.assertEqual(a["lineNumbers"], ["1", "2", "", "3"])
        self.assertEqual(b["lineNumbers"], ["1", "2", "3", "4"])
        self.assertFalse(a["binary"])

        cmp.select(1)
        a_bin = cmp.web.sideA
        b_bin = cmp.web.sideB
        self.assertTrue(a_bin["binary"])
        self.assertEqual(a_bin["marks"], ["normal", "changed"])
        self.assertEqual(b_bin["marks"], ["normal", "changed", "changed"])

    def test_native_diff_editor_qml_renders_and_syncs_scroll_without_webengine(self):
        engine = QQmlApplicationEngine()
        qml = """
        import QtQuick
        import Quill.Native 1.0

        Item {
            id: root
            width: 800; height: 400
            property bool syncing: false
            NativeDiffEditor {
                id: paneA
                objectName: "paneA"
                x: 0; y: 0; width: 400; height: 400
                onScrollYChanged: {
                    if (root.syncing) return
                    root.syncing = true
                    paneB.syncViewport(scrollX, scrollY)
                    root.syncing = false
                }
            }
            NativeDiffEditor {
                id: paneB
                objectName: "paneB"
                x: 400; y: 0; width: 400; height: 400
                onScrollYChanged: {
                    if (root.syncing) return
                    root.syncing = true
                    paneA.syncViewport(scrollX, scrollY)
                    root.syncing = false
                }
            }
        }
        """
        comp = QQmlComponent(engine)
        comp.setData(qml.encode("utf-8"), QUrl(""))
        self.assertFalse(comp.isError(), [e.toString() for e in comp.errors()])
        root = comp.create()
        self.assertIsNotNone(root)

        pane_a = root.findChild(NativeDiffEditor, "paneA")
        pane_b = root.findChild(NativeDiffEditor, "paneB")
        self.assertIsNotNone(pane_a)
        self.assertIsNotNone(pane_b)
        self.assertTrue(pane_a.readOnly)

        lines = [f"key_{i}=value_{i}" for i in range(120)]
        marks = ["changed" if i % 10 == 0 else "normal" for i in range(120)]
        nums = [str(i + 1) for i in range(120)]
        pane_a.diffData = {"text": "\n".join(lines), "marks": marks, "lineNumbers": nums, "path": "build.prop", "binary": False}
        pane_b.diffData = {"text": "\n".join(lines), "marks": marks, "lineNumbers": nums, "path": "build.prop", "binary": False}
        self.app.processEvents()

        # Paint both panes cleanly to an offscreen image
        img = QImage(400, 400, QImage.Format.Format_RGBA8888)
        img.fill(QColor("#171717"))
        p = QPainter(img)
        pane_a.paint(p)
        p.end()

        # Verify read-only rejects typed text
        pane_a.forceActiveFocus()
        before = pane_a.getText()
        pane_a.keyPressEvent(QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_X, Qt.KeyboardModifier.NoModifier, "x"))
        self.assertEqual(pane_a.getText(), before)

        # Verify scroll sync
        pane_a.scrollY = 200.0
        self.app.processEvents()
        self.assertAlmostEqual(pane_b.scrollY, 200.0, delta=1.0)

        # Verify WorkspaceCompareWindow.qml loads cleanly and has no QtWebEngine import
        qml_path = Path(__file__).resolve().parents[1] / "ui" / "WorkspaceCompareWindow.qml"
        qml_text = qml_path.read_text("utf-8")
        self.assertNotIn("QtWebEngine", qml_text)
        self.assertNotIn("WebEngineView", qml_text)
        cmp_win = QQmlComponent(engine, QUrl.fromLocalFile(str(qml_path)))
        self.assertFalse(cmp_win.isError(), [e.toString() for e in cmp_win.errors()])

        for mod in (
            "PySide6.QtWebEngineQuick",
            "PySide6.QtWebEngineCore",
            "PySide6.QtWebEngineWidgets",
            "PySide6.QtWebChannel",
        ):
            self.assertNotIn(mod, sys.modules)

    def test_rust_compare_engine_live_progress_and_window_ui(self):
        import os
        import shutil
        import tempfile
        from PySide6.QtCore import Property
        from rennsproject.core.compare_document import CompareEngine
        from ui.compare import CompareSignals

        root = Path(tempfile.mkdtemp(prefix="test_rust_cmp_"))
        try:
            a = root / "sys_a"
            b = root / "sys_b"
            (a / "etc").mkdir(parents=True)
            (b / "etc").mkdir(parents=True)
            (a / "etc" / "build.prop").write_text("ro.x=1\nro.y=old\n", encoding="utf-8")
            (b / "etc" / "build.prop").write_text("ro.x=1\nro.y=new\n", encoding="utf-8")
            (a / "only_a.txt").write_text("only a\n", encoding="utf-8")
            (b / "only_b.txt").write_text("only b\n", encoding="utf-8")

            sig = CompareSignals()
            entries = []
            sig.diff_entry.connect(entries.append)
            eng = CompareEngine(str(a), str(b), str(root), str(root), sig)
            eng._worker()
            self.app.processEvents()
            self.assertIsNotNone(eng._native_job, "Expected Rust CompareJob backend to be active")
            self.assertEqual(eng._phase, "done")
            self.assertEqual([e.rel for e in entries], ["etc/build.prop"])
            snap = eng.progress_snapshot()
            self.assertGreater(snap["scanned_a"], 0)
            self.assertGreater(snap["cmp_total"], 0)

            # Verify live 1-line status updates across phases and QML bindings
            class _Priv(QObject):
                def __init__(self, ws):
                    super().__init__(ws)
                    self.workspace = ws
                    self._cmp = WorkspaceCompare(self)
                @Property(QObject, constant=True)
                def compare(self): return self._cmp

            from PySide6.QtCore import QUrl
            from PySide6.QtQuick import QQuickWindow
            from ui.file_icons import file_icon
            from main_ext.class_ThemeUi import _PALETTES

            class _WS(QObject):
                def __init__(self):
                    super().__init__()
                    self.p1 = "sys_a"
                    self.p2 = "sys_b"
                    self._priv = _Priv(self)
                @Property(QObject, constant=True)
                def privateFeatures(self): return self._priv
                @Property("QVariantMap", constant=True)
                def androidTools(self): return {"trees": ["sys_a", "sys_b"]}
                def fileIconUrl(self, key, palette):
                    from icons.handler import icon
                    return QUrl.fromLocalFile(icon(key, light=_PALETTES.get(palette, {}).get("_is_light", False)))
            from PySide6.QtCore import Slot
            _WS.fileIconUrl = Slot(str, str, result=QUrl)(_WS.fileIconUrl)

            from ui.presentation import LauncherModel
            prefs = LauncherModel(palette="default")
            self.addCleanup(prefs.deleteLater)

            ws = _WS()
            cmp_ctrl = ws.privateFeatures.compare
            cmp_ctrl._engine = eng
            cmp_ctrl._entries = entries
            cmp_ctrl._busy = True
            eng._phase = "content_scan"
            cmp_ctrl._poll_progress()
            self.assertIn("Comparing content", cmp_ctrl.liveStatus)

            eng._phase = "scanning"
            cmp_ctrl._poll_progress()
            self.assertIn("Scanning", cmp_ctrl.liveStatus)

            cmp_ctrl._busy = False
            cmp_ctrl._live_status = "Scanning  \u00b7  A: 10,393  \u00b7  B: 20,200"
            cmp_ctrl._progress = 0.52
            cmp_ctrl.select(0)

            from ui.runtime import IconProvider
            qml_engine = QQmlApplicationEngine()
            qml_engine.addImageProvider("rak", IconProvider())
            warnings = []
            qml_engine.warnings.connect(lambda errs: warnings.extend(e.toString() for e in errs))

            host_comp = QQmlComponent(qml_engine)
            host_comp.setData(b"""
            import QtQuick
            Window {
                visible: true; width: 1080; height: 700
                readonly property Item backdropSurface: bg
                property var dialogStack: null
                Rectangle { id: bg; anchors.fill: parent; color: "#12131a" }
            }
            """, QUrl(""))
            self.assertFalse(host_comp.isError(), [e.toString() for e in host_comp.errors()])
            host = host_comp.create()

            qml_path = Path(__file__).resolve().parents[1] / "ui" / "WorkspaceCompareWindow.qml"
            comp = QQmlComponent(qml_engine, QUrl.fromLocalFile(str(qml_path)))
            self.assertFalse(comp.isError(), [e.toString() for e in comp.errors()])
            win = comp.createWithInitialProperties({"workspace": ws, "model": prefs, "hostWindow": host})
            self.assertIsNotNone(win)
            self.app.processEvents()

            self.assertLessEqual(win.property("width"), 840)
            self.assertLessEqual(win.property("height"), 540)
            self.assertNotIn("\u2714", str(cmp_ctrl.rings))
            self.assertIn("check_white", qml_path.read_text("utf-8"))
            self.assertEqual(file_icon("etc/init.rc"), "icon_rc")
            glass = win.findChild(QObject, "dialogGlass")
            self.assertIsNotNone(glass)
            self.assertTrue(bool(glass.property("effectsEnabled")))

            win.setProperty("backdropSource", host.property("backdropSurface"))
            win.setProperty("tab", 1)
            win.open()
            self.app.processEvents()

            # Verify dragging stays clamped strictly inside hostWindow bounds
            win.moveByDelta(-5000, -5000)
            self.app.processEvents()
            self.assertEqual(round(win.property("x")), 8)
            self.assertEqual(round(win.property("y")), 8)
            win.moveByDelta(5000, 5000)
            self.app.processEvents()
            self.assertEqual(round(win.property("x")), round(host.width() - win.property("width") - 8))
            self.assertEqual(round(win.property("y")), round(host.height() - win.property("height") - 8))

            live = win.findChild(QObject, "compareLiveStatus")
            self.assertIsNotNone(live)
            self.assertEqual(live.property("text"), "Scanning  \u00b7  A: 10,393  \u00b7  B: 20,200")

            # Verify clickable vertical & horizontal scroll hints
            long_lines_a = [f"key_{i}={'a' * 140}" for i in range(120)]
            long_lines_b = [f"key_{i}={'b' * 140}" if i % 25 == 0 else f"key_{i}={'a' * 140}" for i in range(120)]
            marks_a = ["changed" if i % 25 == 0 else "normal" for i in range(120)]
            nums = [str(i + 1) for i in range(120)]
            cmp_ctrl.web.load(
                side_a={"text": "\n".join(long_lines_a), "marks": marks_a, "lineNumbers": nums, "path": "build.prop", "binary": False},
                side_b={"text": "\n".join(long_lines_b), "marks": marks_a, "lineNumbers": nums, "path": "build.prop", "binary": False},
            )
            self.app.processEvents()

            pane_a = win.findChild(NativeDiffEditor, "compareDiffPaneA")
            pane_b = win.findChild(NativeDiffEditor, "compareDiffPaneB")
            v_scroll = win.findChild(QObject, "compareDiffVScroll")
            h_scroll_a = win.findChild(QObject, "compareDiffHScrollA")
            self.assertIsNotNone(pane_a)
            self.assertIsNotNone(pane_b)
            self.assertIsNotNone(v_scroll)
            self.assertIsNotNone(h_scroll_a)
            self.assertTrue(len(pane_a.diffMarkers) >= 4)

            from PySide6.QtCore import QPoint
            from PySide6.QtTest import QTest
            v_pos = v_scroll.mapToScene(QPoint(int(v_scroll.width() / 2), int(v_scroll.height() * 0.7))).toPoint()
            QTest.mouseClick(host, Qt.MouseButton.LeftButton, pos=v_pos)
            self.app.processEvents()
            self.assertGreater(pane_a.scrollY, 100.0)
            self.assertAlmostEqual(pane_a.scrollY, pane_b.scrollY, delta=1.0)

            h_pos = h_scroll_a.mapToScene(QPoint(int(h_scroll_a.width() * 0.7), int(h_scroll_a.height() / 2))).toPoint()
            QTest.mouseClick(host, Qt.MouseButton.LeftButton, pos=h_pos)
            self.app.processEvents()
            self.assertGreater(pane_a.scrollX, 40.0)
            self.assertAlmostEqual(pane_a.scrollX, pane_b.scrollX, delta=1.0)

            real_warnings = [w for w in warnings if "Unable to assign [undefined] to QWindow*" not in w]
            self.assertEqual(real_warnings, [])
            win.close()
            win.deleteLater()
            host.close()
            host.deleteLater()
            self.app.processEvents()
        finally:
            os.environ.pop("RAK_NATIVE_BACKEND", None)
            shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
