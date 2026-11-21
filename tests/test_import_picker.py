# SPDX-License-Identifier: MPL-2.0
"""Verify the custom glass Import Files picker replaces the black Qt fallback dialog."""
import os
from pathlib import Path
import sys
import time

os.environ["QSG_RENDER_LOOP"] = "basic"

from PySide6.QtCore import QObject, Qt, QUrl
from PySide6.QtQml import QQmlApplicationEngine, QQmlComponent
from PySide6.QtQuick import QQuickItem
from PySide6.QtTest import QTest
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


def test_import_picker_opens_glass_dialog_and_imports_files(tmp_path):
    repo = Path(__file__).resolve().parents[1]
    os.chdir(repo)
    if str(repo) not in sys.path:
        sys.path.insert(0, str(repo))

    configure_text_rendering_env()
    initialize_quick_runtime(webengine=False)
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    apply_app_font(app, polish_widgets=False)

    port = tmp_path / "PORT"
    port.mkdir()
    incoming = tmp_path / "incoming"
    incoming.mkdir()
    file_a = incoming / "alpha.txt"
    file_b = incoming / "beta.prop"
    file_a.write_text("alpha-content\n", encoding="utf-8")
    file_b.write_text("ro.beta=1\n", encoding="utf-8")

    prefs = LauncherModel()
    prefs.chooseSetting("animations", False)
    ws = WorkspaceSession(str(tmp_path), "PORT", None, preferences=prefs, enable_private_services=False)

    engine = QQmlApplicationEngine()
    comp = QQmlComponent(engine, QUrl.fromLocalFile(str(repo / "ui" / "Workspace.qml")))
    assert comp.status() == QQmlComponent.Status.Ready, comp.errorString()
    win = comp.createWithInitialProperties({"workspace": ws, "preferences": prefs})
    assert win is not None, comp.errorString()

    try:
        win.show()
        ws.start()
        _wait(app, lambda: ws.prepared)

        picker = win.findChild(QObject, "workspaceImportPicker")
        assert picker is not None
        assert picker.metaObject().className().startswith("WorkspaceFilePickerDialog") or picker.property("pickerTitle") == "Import files"

        picker.setProperty("currentFolder", QUrl.fromLocalFile(str(incoming)))
        ws.dispatch("import", {})
        _wait(app, lambda: bool(picker.property("visible")) and not ws.importPicker._jobs.busy())

        assert ws.importPicker.rows.rowCount() == 2
        # Multi-select both files and click Import button
        ws.importPicker.select(str(file_a), False, False)
        ws.importPicker.select(str(file_b), True, False)
        app.processEvents()
        assert len(ws.importPicker.selectedPaths) == 2

        open_btn = picker.findChild(QQuickItem, "renxecOpen")
        assert open_btn is not None and open_btn.isEnabled()
        win.requestActivate()
        _wait(app, lambda: win.isActive())
        QTest.mouseClick(win, Qt.LeftButton, Qt.NoModifier, open_btn.mapToScene(open_btn.boundingRect().center()).toPoint())

        _wait(app, lambda: (port / "alpha.txt").is_file() and (port / "beta.prop").is_file() and not ws.task_queue.is_busy())
        assert (port / "alpha.txt").read_text(encoding="utf-8") == "alpha-content\n"
        assert (port / "beta.prop").read_text(encoding="utf-8") == "ro.beta=1\n"
        assert not picker.property("visible")
    finally:
        win.hide()
        win.deleteLater()
        engine.deleteLater()
        ws.stop()
        app.processEvents()
