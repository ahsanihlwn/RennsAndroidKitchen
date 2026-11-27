# SPDX-License-Identifier: MPL-2.0
"""Verify Rust-native Markdown viewer, edit-mode toggle, and lock/read-only padlock icons."""
import os
from pathlib import Path
import stat
import tempfile
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QSG_RENDER_LOOP", "basic")

from PySide6.QtCore import Qt, QUrl
from PySide6.QtQml import QQmlApplicationEngine, QQmlComponent
from PySide6.QtQuick import QQuickItem
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from core import native_backend
from core.terminal_pty import PtySignals
from ui.presentation import LauncherModel
from ui.runtime import IconProvider, initialize_quick_runtime
from ui.terminal import TerminalSession
from ui.viewers import viewer_kind
from ui.workspace import WorkspaceSession
from ui.workspace_window import (
    ArchiveDocument,
    DocumentImages,
    HexDocument,
    ImageDocument,
    MarkdownDocument,
    MediaDocument,
    PartitionDocument,
    SoDocument,
    ViewerDocument,
)


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


class MarkdownViewerAndLockBadgeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        initialize_quick_runtime(webengine=False)
        cls.app = QApplication.instance() or QApplication([])

    def test_rust_markdown_parser_renders_blocks_and_headings(self):
        md = (
            "# Kitchen Guide\n\n"
            "Use **native** *Rust* with `markdown_render_json` and [docs](https://example.com).\n\n"
            "- [x] Build Rust\n"
            "- [ ] Open Preview\n\n"
            "```python\n"
            "def hello():\n"
            "    return 1\n"
            "```\n\n"
            "| Feature | Status |\n"
            "| :--- | ---: |\n"
            "| Viewer | Ready |\n"
        )
        doc = native_backend.render_markdown(md, path="README.md")
        diag = native_backend.diagnostics()
        self.assertEqual(diag["backend"], "rust")
        self.assertGreater(diag["native_calls"].get("markdown_render", 0), 0)
        self.assertEqual(doc["title"], "Kitchen Guide")
        self.assertEqual(len(doc["headings"]), 1)
        self.assertEqual(doc["headings"][0]["anchor"], "kitchen-guide")
        kinds = [b["kind"] for b in doc["blocks"]]
        self.assertEqual(kinds, ["heading", "paragraph", "list", "code", "table"])
        self.assertIn("<b>native</b>", doc["blocks"][1]["html"])
        self.assertTrue(doc["blocks"][2]["items"][0]["checked"])
        self.assertEqual(doc["blocks"][3]["language"], "python")
        self.assertEqual(doc["blocks"][4]["alignments"], ["left", "right"])
        self.assertEqual(viewer_kind("README.md"), "markdown")

    def test_markdown_viewer_edit_toggle_and_padlock_badges(self):
        temp = tempfile.TemporaryDirectory(prefix="rak-md-lock-", ignore_cleanup_errors=True)
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        port = root / "PORT"
        port.mkdir()
        md_file = port / "README.md"
        md_file.write_text("# Hello MD\n\n## Section Two\n\nInitial **body**.\n", encoding="utf-8")
        ro_file = port / "locked_ro.txt"
        ro_file.write_text("read only text\n", encoding="utf-8")
        os.chmod(ro_file, stat.S_IREAD)
        self.addCleanup(lambda: os.chmod(ro_file, stat.S_IWRITE) if ro_file.exists() else None)

        prefs = LauncherModel(
            palette="dark_green",
            settings={"effects": "low", "blurRadius": 0, "animations": False, "treeMode": "single_dir"},
        )
        self.addCleanup(prefs.deleteLater)
        session = WorkspaceSession(
            str(root),
            "PORT",
            preferences=prefs,
            settings_path=str(root / "settings.json"),
            terminal_factory=lambda **kw: TerminalSession(manager=_FakePty(), **kw),
            enable_private_services=False,
        )
        session._agent.close()

        engine = QQmlApplicationEngine()
        engine.rootContext().setContextProperty("rakPreferences", prefs)
        engine.rootContext().setContextProperty("rakAgentControls", session.agentControls)
        engine.addImageProvider("icons", IconProvider())
        engine.addImageProvider("rak", IconProvider())
        engine.addImageProvider("documents", DocumentImages())
        for cls_type, name in (
            (ViewerDocument, "ViewerDocument"),
            (HexDocument, "HexDocument"),
            (SoDocument, "SoDocument"),
            (PartitionDocument, "PartitionDocument"),
            (ImageDocument, "ImageDocument"),
            (MarkdownDocument, "MarkdownDocument"),
            (MediaDocument, "MediaDocument"),
            (ArchiveDocument, "ArchiveDocument"),
        ):
            qml_type = cls_type
            from PySide6.QtQml import qmlRegisterType
            qmlRegisterType(qml_type, "Renns.Workspace", 1, 0, name)

        repo = Path(__file__).resolve().parents[1]
        comp = QQmlComponent(engine, QUrl.fromLocalFile(str(repo / "ui" / "Workspace.qml")))
        self.assertEqual(comp.status(), QQmlComponent.Status.Ready, comp.errorString())
        win = comp.createWithInitialProperties({"workspace": session, "preferences": prefs})
        self.assertIsNotNone(win)

        def pump_until(pred, timeout=8.0, msg="timeout"):
            deadline = time.monotonic() + timeout
            while not pred():
                if time.monotonic() >= deadline:
                    self.fail(msg)
                self.app.processEvents()
                time.sleep(0.01)

        def iter_visuals():
            pending = [win.contentItem()]
            while pending:
                item = pending.pop()
                if item is not None:
                    yield item
                    pending.extend(item.childItems())

        def find_visual(object_name, root_item=None):
            pending = [root_item or win.contentItem()]
            while pending:
                item = pending.pop()
                if item is None:
                    continue
                if item.objectName() == object_name:
                    return item
                pending.extend(item.childItems())
            return None

        def find_entry_for_path(target_path):
            norm = os.path.normcase(os.path.normpath(target_path))
            for item in iter_visuals():
                op_path = item.property("opPath")
                if op_path and os.path.normcase(os.path.normpath(str(op_path))) == norm:
                    return item
            return None

        motion = find_visual("workspaceLayoutMotion")
        self.assertIsNotNone(motion)

        def settle():
            pump_until(lambda: not motion.property("running") and not motion.property("pending"), msg="layout motion timeout")
            self.app.processEvents()
            time.sleep(0.05)
            self.app.processEvents()
            pump_until(lambda: not motion.property("running") and not motion.property("pending"), msg="layout motion settle timeout")

        def click_item(item):
            self.assertIsNotNone(item)
            self.assertTrue(item.isVisible())
            self.assertTrue(item.isEnabled())
            self.assertGreater(item.width(), 0)
            self.assertGreater(item.height(), 0)
            win.requestActivate()
            self.app.processEvents()
            pt = item.mapToScene(item.boundingRect().center()).toPoint()
            QTest.mouseMove(win, pt)
            self.app.processEvents()
            QTest.mouseClick(win, Qt.LeftButton, Qt.NoModifier, pt)
            self.app.processEvents()

        try:
            win.show()
            session.start()
            pump_until(lambda: session.prepared, msg="session not prepared")
            settle()
            pump_until(lambda: session.tree.viewModel.rowCount() >= 2, msg="tree model did not populate")
            tree0 = find_visual("workspaceTree0")
            self.assertIsNotNone(tree0)
            tree0.forceLayout()
            self.app.processEvents()

            # Wait for tree entries to populate
            pump_until(lambda: find_entry_for_path(str(ro_file)) is not None and find_entry_for_path(str(md_file)) is not None,
                       msg="tree entries did not appear")

            ro_entry = find_entry_for_path(str(ro_file))
            md_entry = find_entry_for_path(str(md_file))
            self.assertTrue(ro_entry.property("entryLocked"))
            ro_badge = find_visual("treeEntryLockBadge", ro_entry)
            self.assertIsNotNone(ro_badge)
            self.assertTrue(ro_badge.isVisible())

            self.assertFalse(md_entry.property("entryLocked"))
            md_badge = find_visual("treeEntryLockBadge", md_entry)
            self.assertIsNotNone(md_badge)
            self.assertFalse(md_badge.isVisible())

            # Open read-only file -> editor tab shows padlock icon badge & read-only editor
            session.openPath(str(ro_file))
            pump_until(lambda: session.activeKind == "editor", msg="ro_file did not open in editor")
            settle()
            quill = find_visual("quickQuillView")
            self.assertIsNotNone(quill)
            pump_until(lambda: bool(quill.property("readOnly")), msg="ro_file editor not readOnly")
            self.assertTrue(session.editor.bridge._tabs[str(ro_file)]["read_only"])
            ro_tab = find_visual("workspaceTab_" + str(ro_file))
            self.assertIsNotNone(ro_tab)
            self.assertTrue(bool(ro_tab.property("tabLocked")))
            ro_tab_lock = find_visual("tabEntryLockBadge", ro_tab)
            self.assertIsNotNone(ro_tab_lock)
            pump_until(lambda: ro_tab_lock.isVisible(), msg="ro_file tab padlock badge did not become visible")

            # Open Markdown file -> opens in Rust Markdown Viewer tab
            session.openPath(str(md_file))
            self.assertEqual(session.activeKind, "markdown")
            settle()
            def md_ready():
                v = find_visual("workspaceMarkdownViewer")
                d = v.property("documentModel") if v is not None else None
                return bool(d is not None and d.property("ready"))

            pump_until(md_ready, msg="markdown doc did not render")
            md_viewer = find_visual("workspaceMarkdownViewer")

            # Outline TOC toggle
            outline_btn = find_visual("markdownOutlineToggle")
            self.assertIsNotNone(outline_btn)
            self.assertTrue(outline_btn.isVisible())
            self.assertFalse(bool(md_viewer.property("outlineOpen")))
            click_item(outline_btn)
            pump_until(lambda: bool(md_viewer.property("outlineOpen")), msg="outline panel did not open")

            def md_tab_lock_visible():
                t = find_visual("workspaceTab_" + str(md_file))
                b = find_visual("tabEntryLockBadge", t) if t is not None else None
                return bool(b is not None and b.isVisible())

            self.assertFalse(md_tab_lock_visible())

            # Lock folder dynamically -> tree padlock, tab padlock, and viewer locked pill all become visible
            session.lock_path(str(port))
            pump_until(lambda: md_entry.property("entryLocked") and md_badge.isVisible() and md_tab_lock_visible(),
                       msg="dynamic lock did not update padlock badges")
            locked_pill = find_visual("markdownLockedPill")
            self.assertIsNotNone(locked_pill)
            self.assertTrue(locked_pill.isVisible())

            # Entering Edit Mode while locked keeps editor readOnly
            edit_btn = find_visual("markdownEditToggle")
            click_item(edit_btn)
            pump_until(lambda: session.activeKind == "editor", msg="did not switch to native editor while locked")
            settle()
            pump_until(lambda: bool(quill.property("readOnly")), msg="locked markdown editor not readOnly")
            self.assertTrue(session.editor.bridge._tabs[str(md_file)]["read_only"])

            # Switch back to Preview and unlock
            preview_btn = find_visual("markdownPreviewToggle")
            click_item(preview_btn)
            pump_until(lambda: session.activeKind == "markdown", msg="did not switch back to markdown preview")
            settle()

            session.unlock_path(str(port))
            pump_until(lambda: not md_entry.property("entryLocked") and not md_badge.isVisible() and not md_tab_lock_visible(),
                       msg="unlock did not clear padlock badges")

            # Click Edit Mode toggle button -> switches to Native Editor (Quill.Native) writable
            edit_btn = find_visual("markdownEditToggle")
            click_item(edit_btn)
            pump_until(lambda: session.activeKind == "editor", msg="did not switch to native editor")
            settle()
            pump_until(lambda: not bool(quill.property("readOnly")), msg="unlocked markdown editor still readOnly")
            self.assertFalse(session.editor.bridge._tabs[str(md_file)]["read_only"])

            preview_btn = find_visual("markdownPreviewToggle")
            self.assertIsNotNone(preview_btn)
            self.assertTrue(preview_btn.isVisible())

            # Edit text in native editor buffer and switch back to Preview
            session.editor.setContent(str(md_file), "# Updated Title\n\nLive **edited** buffer.\n")
            click_item(preview_btn)
            pump_until(lambda: session.activeKind == "markdown", msg="did not switch back to markdown preview")
            def md_updated():
                v = find_visual("workspaceMarkdownViewer")
                d = v.property("documentModel") if v is not None else None
                return bool(d is not None and d.property("ready") and d.property("title") == "Updated Title")

            pump_until(md_updated, msg="markdown preview did not reflect live editor buffer")
        finally:
            session.stop()
            win.hide()
            win.deleteLater()
            engine.deleteLater()
            self.app.processEvents()
            time.sleep(0.05)
            self.app.processEvents()


if __name__ == "__main__":
    unittest.main()
