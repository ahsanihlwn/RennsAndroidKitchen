# SPDX-License-Identifier: MPL-2.0
"""Verify realtime preview/lock/highlight Session state & QML compilation for modified panels."""
import os
from pathlib import Path
import time

os.environ["QSG_RENDER_LOOP"] = "basic"

from PySide6.QtCore import Qt, QUrl
from PySide6.QtQml import QQmlApplicationEngine, QQmlComponent
from PySide6.QtWidgets import QApplication

from core import native_backend
from core.fonts import apply_app_font, configure_text_rendering_env
from ui.presentation import LauncherModel
from ui.runtime import initialize_quick_runtime
from ui.workspace import WorkspaceSession


def _setup_app():
    repo = Path(__file__).resolve().parents[1]
    os.chdir(repo)
    configure_text_rendering_env()
    initialize_quick_runtime(webengine=False)
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    apply_app_font(app, polish_widgets=False)
    return repo, app


def _wait(app, predicate, timeout=15.0):
    deadline = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < deadline, "Timed out"
        app.processEvents()
        time.sleep(0.01)


def test_qml_controls_compile_without_errors():
    repo, app = _setup_app()
    engine = QQmlApplicationEngine()
    engine.addImportPath(str(repo / "ui" / "controls"))
    for rel in (
        "ui/controls/WorkspaceSidePanel.qml",
        "ui/controls/WorkspaceStatusBar.qml",
        "ui/controls/WorkspaceHistory.qml",
        "ui/controls/WorkspaceTree.qml",
        "ui/controls/WorkspaceEntry.qml",
    ):
        comp = QQmlComponent(engine, QUrl.fromLocalFile(str(repo / rel)))
        assert not comp.isError(), f"{rel} QML error: {comp.errors()}"
    engine.deleteLater()


def test_session_lock_read_only_tab_promotion_and_highlights(tmp_path):
    repo, app = _setup_app()
    (tmp_path / "rom").mkdir()
    src = tmp_path / "src_img"
    (src / "etc").mkdir(parents=True)
    (src / "etc" / "build.prop").write_text("ro.build.id=TEST1\n", encoding="utf-8")
    img = tmp_path / "rom" / "system.img"
    native_backend.erofs_pack(str(src), str(img), mount_point="/system")

    prefs = LauncherModel()
    prefs.chooseSetting("animations", False)
    session = WorkspaceSession(str(tmp_path), "rom", preferences=prefs, enable_private_services=False)
    session.start()
    try:
        _wait(app, lambda: session.prepared)
        target_dir = str(tmp_path / "rom" / "system")
        prop_path = os.path.join(target_dir, "etc", "build.prop")

        # 1. Start preview + lock + loading highlight before target_dir exists on disk
        session.lock_path(target_dir)
        session.set_path_highlight(target_dir, "loading")
        assert session.isPathLocked(target_dir)
        assert session.isPathLocked(prop_path)
        assert session.pathHighlightState(target_dir) == "loading"

        assert session.mounts.begin_unpack_preview(str(img), target_dir, str(tmp_path / "rom"))
        _wait(app, lambda: not session.tree.busy)
        node = session.tree._nodes.get(os.path.normpath(target_dir))
        assert node is not None
        idx = session.tree._index(node, 0)
        assert not (session.tree.flags(idx) & Qt.ItemIsEditable)

        # Open virtual preview file -> read-only tab
        session.openPath(prop_path)
        _wait(app, lambda: session._find_tab(prop_path) is not None)
        tab_key, tab = session.editor.bridge._find_tab(prop_path)
        assert tab is not None and tab.get("read_only") is True

        # 2. Extract finishes -> end preview -> unlock -> promote tab to writable disk file -> green -> fades
        native_backend.erofs_unpack(str(img), str(tmp_path / "rom"), image_base_name="system")
        session.mounts.end_unpack_preview(target_dir)
        session.unlock_path(target_dir)
        session.set_path_highlight(target_dir, "done", fade_ms=80)
        _wait(app, lambda: not session.tree.busy)

        assert not session.isPathLocked(target_dir)
        _tab_k, updated_tab = session.editor.bridge._find_tab(prop_path)
        assert updated_tab is not None
        assert updated_tab.get("read_only") is False
        assert updated_tab.get("virtual") is False

        assert session.pathHighlightState(target_dir) == "done"
        _wait(app, lambda: session.pathHighlightState(target_dir) == "", timeout=2.0)

        # 3. Error highlight remains persistent until cleared
        session.set_path_highlight(target_dir, "error")
        assert session.pathHighlightState(target_dir) == "error"
    finally:
        session.stop()
        session.deleteLater()
        app.processEvents()
