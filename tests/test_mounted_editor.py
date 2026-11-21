# SPDX-License-Identifier: MPL-2.0
"""Verify opening, editing, tab-switching, and saving mounted EROFS/EXT text files in Quill."""
import os
from pathlib import Path
import sys
import time

os.environ["QSG_RENDER_LOOP"] = "basic"

from PySide6.QtCore import QUrl
from PySide6.QtQml import QQmlApplicationEngine, QQmlComponent
from PySide6.QtWidgets import QApplication

from core import imgfs, native_backend
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


def _build_erofs_fixture(root: Path) -> Path:
    source = root / "erofs_source"
    (source / "etc").mkdir(parents=True, exist_ok=True)
    (source / "etc" / "build.prop").write_bytes(b"ro.carrier=test\nro.product.model=X6850\n")
    image = root / "tr_carrier.img"
    native_backend.erofs_pack(str(source), str(image), algo="none")
    assert image.is_file()
    return image


def test_mounted_erofs_open_edit_switch_and_save(tmp_path):
    repo = Path(__file__).resolve().parents[1]
    os.chdir(repo)
    if str(repo) not in sys.path:
        sys.path.insert(0, str(repo))

    configure_text_rendering_env()
    initialize_quick_runtime(webengine=False)
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    apply_app_font(app, polish_widgets=False)

    erofs_img = _build_erofs_fixture(tmp_path)
    disk_file = tmp_path / "local.prop"
    disk_file.write_text("ro.local=1\n", encoding="utf-8")

    prefs = LauncherModel()
    ws = WorkspaceSession(str(tmp_path), str(tmp_path), None, preferences=prefs, enable_private_services=False)

    engine = QQmlApplicationEngine()
    component = QQmlComponent(engine, QUrl.fromLocalFile(str(repo / "ui" / "controls" / "EditorPane.qml")))
    assert component.status() == QQmlComponent.Status.Ready, component.errorString()
    pane = component.createWithInitialProperties({"session": ws.editor, "fillColor": "#171717"})
    assert pane is not None, component.errorString()
    quill = pane.findChild(object, "quickQuillView")
    assert quill is not None

    try:
        ws.start()
        _wait(app, lambda: bool(ws.tree.base) and not ws.tree.busy)

        ws.mounts.mount(str(erofs_img))
        _wait(app, lambda: ws.mounts.reference(str(erofs_img)) is not None and not ws.tree.busy)

        mounted_path = f"mount://{erofs_img}//etc/build.prop"
        ws.openPath(mounted_path)
        _wait(app, lambda: ws.activeTab == mounted_path and quill.filePath == mounted_path)

        assert quill.getText() == "ro.carrier=test\nro.product.model=X6850\n"
        assert not quill.dirty
        assert not quill.readOnly

        # Edit mounted document
        updated = "ro.carrier=edited\nro.product.model=X6850\n"
        quill.setText(updated)
        _wait(app, lambda: ws.editor.bridge.is_unsaved(mounted_path))
        assert quill.dirty
        assert ws.editor.bridge._tabs[mounted_path]["content"] == updated

        # Switch to a disk file and back; unsaved mounted buffer must persist
        ws.openPath(str(disk_file))
        _wait(app, lambda: quill.filePath == os.path.normpath(str(disk_file)))
        assert quill.getText() == "ro.local=1\n"

        ws.selectTab(mounted_path)
        _wait(app, lambda: quill.filePath == mounted_path)
        assert quill.getText() == updated
        assert quill.dirty

        # Save mounted document asynchronously back into the EROFS image
        ws.saveCurrent()
        _wait(app, lambda: not ws.editor.bridge.is_unsaved(mounted_path) and not ws.task_queue.is_busy())
        _wait(app, lambda: not quill.dirty)
        assert imgfs.read_path(str(erofs_img), "etc/build.prop")[0] == updated.encode("utf-8")

        # Verify read-only virtual tab prevents edits
        ro_tab = f"mount://{erofs_img}//etc/readonly.prop"
        ws.editor.bridge.open_virtual(ro_tab, "ro.fixed=1\n", read_only=True)
        ws.editor.openFile(ro_tab)
        _wait(app, lambda: quill.filePath == ro_tab)
        assert quill.readOnly
        quill.setText("ro.fixed=2\n")
        assert quill.getText() == "ro.fixed=1\n"

        ws.closeTab(mounted_path)
        _wait(app, lambda: ws._find_tab(mounted_path) is None)

        # Verify borderless 50% translucent minimap painting and visible accent selection color
        from PySide6.QtCore import QSizeF
        from PySide6.QtGui import QColor, QImage, QPainter
        pane.setSize(QSizeF(800, 400))
        app.processEvents()
        quill.loadPath(str(disk_file))
        _wait(app, lambda: quill.filePath == os.path.normpath(str(disk_file)))
        img = QImage(800, 400, QImage.Format.Format_ARGB32_Premultiplied)
        img.fill(0)
        p = QPainter(img)
        quill.paint(p)
        p.end()
        assert quill.minimapEnabled is True
        # Empty row near bottom: slightly darker minimap tint with no 1px divider border
        minimap_x = int(800 - quill.minimapWidth)
        assert img.pixelColor(minimap_x - 1, 350) == QColor(quill.fillColor)
        assert img.pixelColor(minimap_x, 350) == img.pixelColor(minimap_x + 1, 350)
        assert img.pixelColor(minimap_x, 350).lightness() < QColor(quill.fillColor).lightness()
        assert QColor(quill.selectionColor).alphaF() >= 0.25
        assert QColor(quill.selectionColor) != QColor(quill.fillColor)
    finally:
        pane.deleteLater()
        engine.deleteLater()
        ws.stop()
        app.processEvents()
