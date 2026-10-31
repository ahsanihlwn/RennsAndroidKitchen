# SPDX-License-Identifier: MPL-2.0
"""Exercise real QML input and scene graph content using QGuiApplication only."""
import json
import os
from pathlib import Path
import sys


def main():
    repo = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(repo))
    os.chdir(repo)
    from core.fonts import configure_text_rendering_env, apply_app_font
    configure_text_rendering_env()
    from PySide6.QtCore import QObject, Qt, QPoint, QMetaObject
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtQuick import QQuickItem
    from ui.presentation import LauncherModel
    from ui.runtime import create_launcher

    app = QGuiApplication([])
    apply_app_font(app)
    model = LauncherModel(palette="dark_green", language="id", projects=[
        {"project_name": f"Firmware {i}", "path": f"C:/fixture/{i}", "port1": "system", "port2": ""}
        for i in range(80)
    ], settings={"animations": False})
    commands, diagnostics = [], []
    model.command.connect(lambda name, data: commands.append((name, data)))
    engine, window = create_launcher(model, visible=False)
    assert "PySide6.QtWidgets" not in sys.modules, "Qt Quick preview imported QWidget presentation"
    # QtTest itself loads QtWidgets to expose its QWidget overloads. Check the
    # production imports first, then check that the test creates no widgets.
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication
    engine.warnings.connect(lambda errors: diagnostics.extend(error.toString() for error in errors))
    window.setFlags(window.flags() | Qt.Tool | Qt.WindowDoesNotAcceptFocus)
    window.setPosition(app.primaryScreen().availableGeometry().topLeft())
    window.show()
    QTest.qWait(150)

    def item(name):
        result = window.findChild(QQuickItem, name)
        if result is None:
            # Popup delegates can belong to the overlay's visual tree without
            # being QObject children of the application window.
            pending = [window.contentItem()]
            while pending:
                candidate = pending.pop()
                if candidate.objectName() == name:
                    result = candidate
                    break
                pending.extend(candidate.childItems())
        assert result is not None, name
        return result

    def click(name):
        target = item(name)
        point = target.mapToScene(target.boundingRect().center()).toPoint()
        QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, point)
        QTest.qWait(20)

    try:
        assert item("projectList").property("count") == 80
        assert item("settingsButton").mapToScene(item("settingsButton").boundingRect().topLeft()).toPoint() == QPoint(791, 636)
        click("settingsButton")
        assert model.page == "settings"
        model.back()
        click("createButton")
        assert commands[-1] == ("newProject", {})
        # The production action opens a dialog. Its command is preserved;
        # navigate explicitly to review the existing full-page create form.
        model.navigate("create")
        assert model.page == "create"
        assert not item("confirmProjectButton").isEnabled()
        name = item("projectName")
        click("projectName")
        QTest.keyClick(window, Qt.Key_A)
        assert name.property("text") == "a", "Native text input did not receive the key"
        name.setProperty("text", "Fixture")
        item("port1Name").setProperty("text", "system")
        assert item("confirmProjectButton").isEnabled()
        click("dualRomButton")
        assert item("port2Name").isVisible()
        assert not item("confirmProjectButton").isEnabled()
        item("port2Name").setProperty("text", "vendor")
        click("confirmProjectButton")
        assert commands[-1] == ("createProject", {"name": "Fixture", "port1": "system", "port2": "vendor", "mode": 2})
        model.navigate("projects")
        model.search("Firmware 79")
        assert item("projectList").property("count") == 1
        model.search("")
        model.navigate("settings")
        scroll = item("settingsScroll")
        scroll.setProperty("contentY", 200)
        QTest.qWait(30)
        click("animationsSwitch")
        assert model.settings["animations"] is True
        assert commands[-1] == ("setting", {"key": "animations", "value": True})
        model.chooseSetting("animations", False)
        click("effectLevelChoice")
        click("choice_effectLevelChoice_low")
        assert model.settings["effects"] == "low"
        assert commands[-1] == ("setting", {"key": "effects", "value": "low"})
        picker = window.findChild(QObject, "themePicker")
        QMetaObject.invokeMethod(picker, "open")
        QTest.qWait(50)
        assert picker.property("visible")
        QMetaObject.invokeMethod(picker, "close")
        for width, height in ((550, 400), (1440, 900), (1080, 700)):
            window.resize(width, height)
            for page in ("projects", "create", "settings"):
                model.navigate(page)
                QTest.qWait(20)
                assert not window.grabWindow().isNull()
        assert not QApplication.allWidgets(), "Qt Quick created a hidden QWidget frontend"
        assert not diagnostics, diagnostics
        print(json.dumps({"result": "passed", "backend": "Qt Quick", "widget_module_loaded": False,
                          "checks": ["native pointer", "native keyboard", "navigation", "form validation",
                                     "dual ROM", "command payload", "search", "switch", "dropdown", "modal", "resize"]}), flush=True)
    finally:
        window.close()
        engine.deleteLater()
        app.processEvents()


if __name__ == "__main__":
    main()
