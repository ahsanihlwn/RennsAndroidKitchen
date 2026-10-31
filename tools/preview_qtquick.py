# SPDX-License-Identifier: MPL-2.0
"""Review native Qt Quick launcher forms without modifying application state.

Run audit_ui.py first to capture the original UI and supply --fixture with its
fixture.json. Commands from the forms are recorded, never dispatched to jobs.
"""
import argparse
import json
import os
from pathlib import Path
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path)
    parser.add_argument("--capture", type=Path, help="Capture all launcher pages then exit")
    parser.add_argument("--size", default="1080x700")
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(repo))
    os.chdir(repo)
    from core.fonts import configure_text_rendering_env, apply_app_font
    configure_text_rendering_env()
    from PySide6.QtCore import QMetaObject, Qt, QObject
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtTest import QTest
    from ui.presentation import LauncherModel
    from ui.runtime import create_launcher

    fixture = json.loads(args.fixture.read_text(encoding="utf-8")) if args.fixture else {
        "palette": "dark_green", "language": "id",
        "projects": [{"project_name": f"Firmware project {i:02d}", "path": f"C:/fixture/project_{i:02d}",
                      "port1": "system", "port2": "vendor" if i % 2 else "", "pinned": i < 2,
                      "parentPath": "C:/fixture", "sizeLabel": "Ukuran: Calculating..."} for i in range(12)],
        "location": "C:/Projects", "settings": {"animations": False},
    }
    app = QGuiApplication([])
    apply_app_font(app)
    if args.capture:
        fixture.setdefault("settings", {})["animations"] = False
    model = LauncherModel(**fixture)
    commands = []
    model.command.connect(lambda name, data: commands.append({"command": name, "data": data}))
    engine, window = create_launcher(model, visible=False)
    width, height = map(int, args.size.lower().split("x"))
    window.resize(width, height)
    def text_geometry(origin=(0,0)):
        from PySide6.QtCore import QPointF
        result=[]
        def walk(item):
            if not item.isVisible(): return
            if item.inherits("QQuickText") or item.inherits("QQuickTextInput"):
                point=item.mapToScene(QPointF(0,0))
                result.append({"text":item.property("text"),"class":item.metaObject().className(),"name":item.objectName(),"rect":[point.x()-origin[0],point.y()-origin[1],item.width(),item.height()],"font":item.property("font").toString(),"implicitHeight":item.implicitHeight(),"baseline":item.baselineOffset()})
            for child in item.childItems(): walk(child)
        walk(window.contentItem())
        return result
    if args.capture:
        artifacts = args.capture.resolve()
        artifacts.mkdir(parents=True, exist_ok=True)
        window.setFlags(window.flags() | Qt.WindowDoesNotAcceptFocus | Qt.Tool)
        # D3D11 can omit deferred scene graph content in an unexposed window.
        # A non-activating tool window gives a real GPU frame for visual QA.
        window.setPosition(app.primaryScreen().availableGeometry().topLeft())
        window.show()
        QTest.qWait(350)
        for name in ("projects", "create", "settings"):
            model.navigate(name)
            QTest.qWait(150)
            image = window.grabWindow()
            if image.isNull():
                raise RuntimeError(f"Qt Quick produced no frame for {name}")
            image.save(str(artifacts / (name + ".png")))
            (artifacts/(name+".geometry.json")).write_text(json.dumps(text_geometry(),indent=2,ensure_ascii=False),encoding="utf-8")
            if name=="projects" and model.settings['effects']=='high':
                project_list=window.findChild(QObject,"projectList")
                project_list.setProperty('contentY',120);QTest.qWait(75)
                window.grabWindow().save(str(artifacts/'projects_scroll.png'))
                project_list.setProperty('contentY',0)
            if name == "settings":
                scroll = window.findChild(QObject, "settingsScroll")
                scroll.setProperty("contentY", max(0, scroll.property("contentHeight") - scroll.property("height")))
                QTest.qWait(100)
                window.grabWindow().save(str(artifacts / "settings_bottom.png"))
        model.navigate("create")
        dual = window.findChild(QObject, "dualRomButton")
        QMetaObject.invokeMethod(dual, "click")
        QTest.qWait(100)
        window.grabWindow().save(str(artifacts / "create_dual.png"))
        model.navigate("settings")
        picker = window.findChild(QObject, "themePicker")
        QMetaObject.invokeMethod(picker, "open")
        QTest.qWait(100)
        window.grabWindow().copy(round(picker.property("x")),round(picker.property("y")),round(picker.property("width")),round(picker.property("height"))).save(str(artifacts / "theme_picker.png"))
        QMetaObject.invokeMethod(picker, "close")
        model.navigate("projects")
        model.presentationRequested.emit("newProject", {})
        QTest.qWait(150)
        popup = window.findChild(QObject, "createProjectPopup")
        x, y = round(popup.property("x")), round(popup.property("y"))
        pw, ph = round(popup.property("width")), round(popup.property("height"))
        window.grabWindow().copy(x - 12, y - 12, pw + 24, ph + 24).save(str(artifacts / "create_popup.png"))
        (artifacts/"create_popup.geometry.json").write_text(json.dumps(text_geometry((x-12,y-12)),indent=2,ensure_ascii=False),encoding="utf-8")
        field=window.findChild(QObject,"popupProjectName")
        field.setProperty("text","Redmi Note 12")
        QTest.qWait(50)
        window.grabWindow().copy(x - 12, y - 12, pw + 24, ph + 24).save(str(artifacts / "create_popup_typed.png"))
        (artifacts/"create_popup_typed.geometry.json").write_text(json.dumps(text_geometry((x-12,y-12)),indent=2,ensure_ascii=False),encoding="utf-8")
        field.setProperty("text","")
        popup.setProperty("portMode", 2)
        QTest.qWait(100)
        window.grabWindow().copy(x - 12, y - 12, pw + 24, ph + 24).save(str(artifacts / "create_popup_dual.png"))
        (artifacts/"create_popup_dual.geometry.json").write_text(json.dumps(text_geometry((x-12,y-12)),indent=2,ensure_ascii=False),encoding="utf-8")
        report = {"size": [width, height], "backend": "Qt Quick", "commands": commands}
        (artifacts / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps(report), flush=True)
        window.close()
        engine.deleteLater()
        app.processEvents()
        return
    window.show()
    raise SystemExit(app.exec())


if __name__ == "__main__":
    main()
