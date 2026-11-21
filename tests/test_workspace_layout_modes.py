# SPDX-License-Identifier: MPL-2.0
"""Verify 4-mode workspace tree layout cycling, swapped/hidden panes, and toggle icons."""
import os
from pathlib import Path
import sys
import time

os.environ["QSG_RENDER_LOOP"] = "basic"

from PySide6.QtCore import QUrl
from PySide6.QtQml import QQmlApplicationEngine, QQmlComponent
from PySide6.QtQuick import QQuickItem
from PySide6.QtWidgets import QApplication

from core.fonts import apply_app_font, configure_text_rendering_env
from ui.presentation import LauncherModel
from ui.runtime import initialize_quick_runtime
from ui.workspace import WorkspaceSession


def _wait(app, predicate, timeout=15.0):
    deadline = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < deadline, "Timed out waiting for condition"
        app.processEvents()
        time.sleep(0.01)


def _setup_app():
    repo = Path(__file__).resolve().parents[1]
    os.chdir(repo)
    if str(repo) not in sys.path:
        sys.path.insert(0, str(repo))
    configure_text_rendering_env()
    initialize_quick_runtime(webengine=False)
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    apply_app_font(app, polish_widgets=False)
    return repo, app


def test_dual_and_single_tree_layout_cycles_and_restore(tmp_path):
    repo, app = _setup_app()
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    (tmp_path / "a" / "f.txt").write_text("hi\n", encoding="utf-8")

    prefs = LauncherModel()
    prefs.chooseSetting("animations", False)

    # Dual-tree cycle: 2 (tree1|tree2) -> 3 (tree1/tree2) -> 4 (tree2|tree1) -> 0 (hide) -> 2
    ws2 = WorkspaceSession(str(tmp_path), "a", "b", preferences=prefs, enable_private_services=False)
    engine = QQmlApplicationEngine()
    comp = QQmlComponent(engine, QUrl.fromLocalFile(str(repo / "ui" / "Workspace.qml")))
    assert comp.status() == QQmlComponent.Status.Ready, comp.errorString()
    win = comp.createWithInitialProperties({"workspace": ws2, "preferences": prefs})
    assert win is not None, comp.errorString()

    try:
        win.show()
        ws2.start()
        _wait(app, lambda: ws2.prepared)

        motion = win.findChild(QQuickItem, "workspaceLayoutMotion")
        p1 = win.findChild(QQuickItem, "workspaceFirstTreeMotion")
        p2 = win.findChild(QQuickItem, "workspaceSecondTreeMotion")
        assert motion is not None and p1 is not None and p2 is not None

        def settle():
            _wait(app, lambda: not motion.property("running") and not motion.property("pending"))
            app.processEvents()
            time.sleep(0.05)
            app.processEvents()
            _wait(app, lambda: not motion.property("running") and not motion.property("pending"))

        settle()
        assert ws2.layoutMode == 2
        assert p1.isVisible() and p2.isVisible()
        assert p1.x() < p2.x()

        # 2 -> 3 (top/bottom)
        ws2.toggleLayout()
        settle()
        assert ws2.layoutMode == 3
        assert p1.isVisible() and p2.isVisible()
        assert p1.y() < p2.y()

        # 3 -> 4 (tree2 | tree1 swapped)
        ws2.toggleLayout()
        settle()
        assert ws2.layoutMode == 4
        assert p1.isVisible() and p2.isVisible()
        assert p2.x() < p1.x()

        # Opening editor during swapped side-by-side (4) collapses to vertical (3), then restores 4 on close
        ws2.openPath(str(tmp_path / "a" / "f.txt"))
        settle()
        assert ws2.layoutMode == 3
        ws2.closeTab(str(tmp_path / "a" / "f.txt"))
        settle()
        assert ws2.layoutMode == 4
        assert p2.x() < p1.x()

        # 4 -> 0 (hide both trees)
        ws2.toggleLayout()
        settle()
        assert ws2.layoutMode == 0
        assert not p1.isVisible() and not p2.isVisible()

        # 0 -> 2 (back to tree1 | tree2)
        ws2.toggleLayout()
        settle()
        assert ws2.layoutMode == 2
        assert p1.isVisible() and p2.isVisible()
        assert p1.x() < p2.x()
    finally:
        win.hide()
        win.deleteLater()
        engine.deleteLater()
        ws2.stop()
        app.processEvents()

    # Single-tree cycle: 2 (tree1|tree1) -> 3 (tree1/tree1) -> 1 (tree1) -> 0 (hide) -> 2
    ws1 = WorkspaceSession(str(tmp_path), "a", None, preferences=prefs, enable_private_services=False)
    try:
        assert ws1.layoutMode == 1
        ws1.toggleLayout()
        assert ws1.layoutMode == 0
        ws1.toggleLayout()
        assert ws1.layoutMode == 2
        ws1.toggleLayout()
        assert ws1.layoutMode == 3
        ws1.toggleLayout()
        assert ws1.layoutMode == 1
    finally:
        ws1.stop()
        app.processEvents()


def test_layout_toggle_icon_opacities_and_hide_slash():
    repo, app = _setup_app()
    engine = QQmlApplicationEngine()
    comp = QQmlComponent(engine, QUrl.fromLocalFile(str(repo / "ui" / "controls" / "WorkspaceNativeIcon.qml")))
    assert comp.status() == QQmlComponent.Status.Ready, comp.errorString()
    icon = comp.createWithInitialProperties({"iconKey": "tree_mode", "iconSize": 24, "layoutMode": 2})
    assert icon is not None, comp.errorString()

    try:
        app.processEvents()
        # Mode 2: left 1.0, right 0.5
        shapes = [c for c in icon.findChildren(QQuickItem) if c.metaObject().className().startswith("QQuickShape")]
        visible_m2 = [s for s in shapes if s.isVisible()]
        assert sorted(round(s.opacity(), 2) for s in visible_m2) == [0.5, 1.0]

        # Mode 4 (swapped): left 0.5, right 1.0
        icon.setProperty("layoutMode", 4)
        app.processEvents()
        visible_m4 = [s for s in shapes if s.isVisible()]
        assert [round(s.opacity(), 2) for s in visible_m4] == [0.5, 1.0]

        # Mode 0 (hide): 50% single rect + 100% bold slash
        icon.setProperty("layoutMode", 0)
        app.processEvents()
        visible_m0 = [s for s in shapes if s.isVisible()]
        assert [round(s.opacity(), 2) for s in visible_m0] == [0.5, 1.0]

        # Mode 1 (single): 100% single rect
        icon.setProperty("layoutMode", 1)
        app.processEvents()
        visible_m1 = [s for s in shapes if s.isVisible()]
        assert [round(s.opacity(), 2) for s in visible_m1] == [1.0]
    finally:
        icon.deleteLater()
        engine.deleteLater()
        app.processEvents()


def test_workspace_and_tree_transitions_disable_when_animations_off(tmp_path):
    from PySide6.QtCore import QPoint, QPointF, Qt
    from PySide6.QtGui import QWheelEvent
    from PySide6.QtTest import QTest

    repo, app = _setup_app()
    root_a = tmp_path / "a"
    root_a.mkdir()
    for i in range(40):
        (root_a / f"file_{i:02d}.txt").write_text("x\n", encoding="utf-8")

    prefs = LauncherModel()
    prefs.chooseSetting("animations", False)

    ws = WorkspaceSession(str(tmp_path), "a", None, preferences=prefs, enable_private_services=False)
    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty("rakPreferences", prefs)
    comp = QQmlComponent(engine, QUrl.fromLocalFile(str(repo / "ui" / "Workspace.qml")))
    assert comp.status() == QQmlComponent.Status.Ready, comp.errorString()
    win = comp.createWithInitialProperties({"workspace": ws, "preferences": prefs})
    assert win is not None, comp.errorString()

    try:
        win.show()
        ws.start()
        _wait(app, lambda: ws.prepared)

        motion = win.findChild(QQuickItem, "workspaceLayoutMotion")
        wheel = win.findChild(QQuickItem, "treeWheelScroll0")
        tree = win.findChild(QQuickItem, "workspaceTree0")
        side_panel = win.findChild(QQuickItem, "workspaceSidePanel")
        assert motion is not None and wheel is not None and tree is not None and side_panel is not None

        assert not motion.property("animationsEnabled")
        assert motion.property("duration") == 0
        assert not wheel.property("animationsEnabled")
        assert not side_panel.property("anims")

        # Wheel scroll in file tree jumps immediately without starting FrameAnimation when animations are off.
        tree.forceLayout()
        app.processEvents()
        before_y = tree.property("contentY")
        tree_pos = tree.mapToScene(QPointF(tree.width() / 2, tree.height() / 2))
        QTest.qWait(20)
        event = QWheelEvent(
            tree_pos, win.mapToGlobal(tree_pos.toPoint()),
            QPoint(0, 0), QPoint(0, -120),
            Qt.NoButton, Qt.NoModifier, Qt.NoScrollPhase, False,
        )
        app.sendEvent(win, event)
        app.processEvents()
        assert not wheel.property("running")
        assert tree.property("contentY") > before_y

        # Turning animations on then off mid-layout-transition stops and settles immediately.
        prefs.chooseSetting("animations", True)
        app.processEvents()
        assert motion.property("animationsEnabled")
        assert motion.property("duration") == 240
        assert wheel.property("animationsEnabled")
        ws.toggleLayout()
        _wait(app, lambda: motion.property("running") or ws.layoutMode == 0)
        prefs.chooseSetting("animations", False)
        app.processEvents()
        assert not motion.property("running")
        assert motion.property("progress") == 1
        assert motion.property("duration") == 0

        # Interrupting an animated terminal open mid-scale continues back with proportional remaining duration < 240ms.
        prefs.chooseSetting("animations", True)
        app.processEvents()
        term_pane = win.findChild(QQuickItem, "workspaceTerminalMotion")
        assert term_pane is not None
        ws.toggleTerminal()
        _wait(app, lambda: motion.property("running") and 0.05 < term_pane.property("opacity") < 0.95)
        ws.toggleTerminal()
        _wait(app, lambda: motion.property("running") and not motion.property("pending"))
        assert 0 < motion.property("activeDuration") < 240
        _wait(app, lambda: not motion.property("running") and not motion.property("pending"))
        assert not term_pane.isVisible()
    finally:
        win.hide()
        win.deleteLater()
        engine.deleteLater()
        ws.stop()
        app.processEvents()
