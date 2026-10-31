# SPDX-License-Identifier: MPL-2.0
"""Exercise original launcher motion through real Qt Quick frames and input."""
import json
import os
from pathlib import Path
import sys
import time


def main():
    repo = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(repo))
    os.chdir(repo)
    from core.fonts import configure_text_rendering_env, apply_app_font
    configure_text_rendering_env()
    from PySide6.QtCore import QObject, Qt, QMetaObject, Q_ARG
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtQuick import QQuickItem
    from ui.presentation import LauncherModel
    from ui.runtime import create_launcher
    app = QGuiApplication([])
    app.setQuitOnLastWindowClosed(False)
    apply_app_font(app)
    fixture = json.loads((repo / "build/qtquick_migration/original/fixture.json").read_text(encoding="utf-8"))
    fixture["settings"]["animations"] = True
    # Give the fixture projects explicit drive identities so clicks exercise
    # the same filter route as LauncherApplication._update_drives().
    if not fixture["drives"]:
        fixture["drives"]=[{"key":"C:","name":"C:","active":True,"used":50*1024**3,"total":100*1024**3,"fraction":.5,"percent":50,"fill":"#232323","barColor":"#53ab73","distro":""}]
    for index,row in enumerate(fixture["projects"]):
        row["drive"]=fixture["drives"][index%len(fixture["drives"])]["key"]
    model = LauncherModel(**fixture)
    excluded=set()
    model.setStartupActive(True)
    commands = []
    def dispatch(command, data):
        commands.append((command, data))
        if command == "startupRevealed":
            model.setStartupActive(False)
        elif command == "newProject":
            model.presentationRequested.emit("newProject", {})
        elif command == "toggleDrive":
            key=data["key"].casefold()
            if key in excluded: excluded.remove(key)
            else: excluded.add(key)
            model.replaceDrives([{**row,"active":row["key"].casefold() not in excluded} for row in model.drives],excluded)
        elif command == "projectMenu":
            model.presentationRequested.emit("projectMenu",{**data,"actions":[{"action":"open","label":"Open"},{"action":"pin","label":"Pin"},{"action":"delete","label":"Delete","danger":True}]})
    model.command.connect(dispatch)
    engine, window = create_launcher(model, visible=False)
    assert "PySide6.QtWidgets" not in sys.modules
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication
    diagnostics = []
    engine.warnings.connect(lambda errors: diagnostics.extend(error.toString() for error in errors))
    artifacts = repo / "build/qtquick_migration/motion"
    artifacts.mkdir(parents=True, exist_ok=True)
    window.setFlags(window.flags() | Qt.Tool | Qt.WindowDoesNotAcceptFocus)
    window.setPosition(app.primaryScreen().availableGeometry().topLeft())
    window.show()

    def obj(name):
        result = window.findChild(QObject, name)
        if result is not None:
            return result
        pending = [window.contentItem()]
        while pending:
            candidate = pending.pop()
            if candidate.objectName() == name:
                return candidate
            pending.extend(candidate.childItems())
        raise AssertionError(name)

    def click(name):
        target = obj(name)
        QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, target.mapToScene(target.boundingRect().center()).toPoint())

    def capture(name):
        frame = window.grabWindow()
        assert not frame.isNull()
        frame.save(str(artifacts / (name + ".png")))

    def wait_for(predicate):
        deadline=time.monotonic()+5
        while not predicate():
            assert time.monotonic()<deadline,"Animation did not settle"
            QTest.qWait(25)

    try:
        QTest.qWait(150)
        splash, glint = obj("startupSplash"), obj("splashGlint")
        assert splash.property("visible") and splash.property("opacity") == 1
        assert obj("splashLogo").property("width") == 68
        before = glint.property("phase")
        QTest.qWait(100)
        assert glint.property("phase") != before
        capture("splash")
        model.presentationRequested.emit("startupReady", {})
        QTest.qWait(80)
        assert 0 < splash.property("opacity") < 1
        capture("startup_reveal")
        wait_for(lambda: not model.startupActive and not obj("pageMotion").property("active"))
        assert not splash.property("visible")
        click("settingsButton")
        QTest.qWait(100)
        assert obj("pageMotion").property("active")
        assert obj("settingsProfileCard").property("opacity") == 0
        capture("settings_mid")
        wait_for(lambda: not obj("pageMotion").property("active"))
        assert not obj("pageMotion").property("active")
        assert obj("settingsProfileCard").property("opacity") == 1
        model.back()
        wait_for(lambda: not obj("pageMotion").property("active"))
        click("createButton")
        QTest.qWait(100)
        popup = obj("createProjectPopup")
        surface = popup.property("surfaceRect")
        source=popup.property("sourceRect")
        anchor=obj("createButton").mapToScene(obj("createButton").boundingRect().topLeft())
        assert abs(popup.property("x")+source.x()-anchor.x())<5, (source,anchor)
        assert 0<=popup.property("x")+surface.x()<1080
        assert 0<=popup.property("y")+surface.y()<700
        assert 32 < surface.height() < 500
        assert obj("createButton").property("morphHidden")
        assert popup.property("formAlpha") == 0
        capture("create_mid")
        QTest.qWait(450)
        wait_for(lambda: popup.property("opened"))
        assert popup.property("opened") and popup.property("formAlpha") == 1
        capture("create_open")
        QMetaObject.invokeMethod(popup, "close")
        QTest.qWait(100)
        assert popup.property("visible") and popup.property("surfaceRect").width() < 440
        QTest.qWait(450)
        wait_for(lambda: not popup.property("visible"))
        assert not popup.property("visible") and not obj("createButton").property("morphHidden")
        model.navigate("settings")
        wait_for(lambda: not obj("pageMotion").property("active"))
        obj("settingsScroll").setProperty("contentY", 200)
        QTest.qWait(20)
        choice=obj("effectLevelChoice")
        QTest.mousePress(window,Qt.LeftButton,Qt.NoModifier,choice.mapToScene(choice.boundingRect().center()).toPoint())
        QTest.qWait(70)
        assert choice.property("scale")<1
        QTest.mouseRelease(window,Qt.LeftButton,Qt.NoModifier,choice.mapToScene(choice.boundingRect().center()).toPoint())
        dropdown=obj("choicePopup_effectLevelChoice")
        assert choice.property("scale")==1
        bounds=(dropdown.property("x"),dropdown.property("y"),dropdown.property("width"),dropdown.property("height"))
        anchor=dropdown.property("sourceRect")
        wait_for(lambda: dropdown.property("progress")>.9)
        capture("dropdown_late")
        wait_for(lambda: dropdown.property("opened"))
        assert bounds==(dropdown.property("x"),dropdown.property("y"),dropdown.property("width"),dropdown.property("height"))
        assert anchor==dropdown.property("sourceRect"), "Dropdown anchor drifted while the button released"
        assert choice.property("arrowReady")
        capture("dropdown_open")
        click("choice_effectLevelChoice_low")
        wait_for(lambda: not obj("choicePopup_effectLevelChoice").property("visible"))
        assert model.settings["effects"] == "low"
        model.navigate("projects")
        wait_for(lambda: not obj("pageMotion").property("active"))
        # A drive filter owns the complete old/new scene, including survivors
        # whose old positions are outside the viewport.
        first_key=model.drives[0]["key"]
        before=model.projects
        click("drive_"+first_key)
        motion=obj("projectListMotion")
        clock=obj("projectCardsClock")
        assert motion.property("active")
        scene=motion.property("scene").toVariant()
        removed=[row for row in scene if row["kind"]=="remove"]
        moving=[row for row in scene if row["kind"]=="keep" and row["sourceY"]!=row["targetY"]]
        assert removed and moving
        assert [row["delay"] for row in moving]==[round(168+i*15.5) for i in range(len(moving))]
        QMetaObject.invokeMethod(clock,"seek",Q_ARG("QVariant",160))
        for row in moving:
            visual=obj("projectMotionRow_"+row["row"]["path"])
            assert visual.property("y")==row["sourceY"]
        QMetaObject.invokeMethod(clock,"seek",Q_ARG("QVariant",200))
        assert 0<obj("projectMotionRow_"+removed[0]["row"]["path"]).property("opacity")<1
        assert obj("projectMotionRow_"+moving[0]["row"]["path"]).property("y")!=moving[0]["sourceY"]
        capture("disk_filter_overlap")
        QMetaObject.invokeMethod(clock,"finish")
        assert not motion.property("active")
        click("drive_"+first_key)
        assert motion.property("active")
        assert motion.property("enterDelay")==155
        QMetaObject.invokeMethod(clock,"seek",Q_ARG("QVariant",120))
        incoming=[row for row in motion.property("scene").toVariant() if row["kind"]=="add"]
        assert all(obj("projectMotionRow_"+row["row"]["path"]).property("opacity")==0 for row in incoming)
        QMetaObject.invokeMethod(clock,"seek",Q_ARG("QVariant",200))
        capture("disk_filter_return")
        QMetaObject.invokeMethod(clock,"finish")
        assert len(model.projects)==len(before)
        # Delete exactly one row. The spinner is verified by the production
        # integration harness; this checks the exit/reflow scene after success.
        model.replaceProjects(before[1:])
        assert motion.property("active")
        QMetaObject.invokeMethod(clock,"seek",Q_ARG("QVariant",200))
        capture("delete_overlap")
        QMetaObject.invokeMethod(clock,"finish")
        model.replaceProjects(before)
        QMetaObject.invokeMethod(clock,"finish")
        click("projectMenu_0")
        wait_for(lambda: obj("projectActionsPopup").property("opened"))
        capture("project_menu")
        QMetaObject.invokeMethod(obj("projectActionsPopup"),"close")
        wait_for(lambda: not obj("projectActionsPopup").property("visible"))
        # The real disk controls travel on the original cubic path. The actual
        # project list is resized; its text is never stretched.
        layout=obj("driveLayoutClock")
        window.resize(1440,800)
        QTest.qWait(30)
        QMetaObject.invokeMethod(layout,"finish")
        assert obj("projectList").property("x")==36
        window.resize(550,400)
        QTest.qWait(30)
        assert obj("driveShelf").property("layoutActive")
        QMetaObject.invokeMethod(layout,"seek",Q_ARG("QVariant",200))
        expected=round(36+(180-36)*(1-(1-200/layout.property("duration"))**3))
        assert obj("projectList").property("x")==expected
        capture("sidebar_mid")
        QMetaObject.invokeMethod(layout,"finish")
        assert obj("projectList").property("x")==180
        click("trashDrive")
        assert obj("driveShelf").property("diskActive")
        disks=obj("diskCardsClock")
        QMetaObject.invokeMethod(disks,"seek",Q_ARG("QVariant",190))
        assert abs(obj("diskMotionRow_"+first_key).property("opacity")-.5)<.001
        assert obj("projectList").property("x")==180
        capture("trash_disks_mid")
        QMetaObject.invokeMethod(disks,"finish")
        QMetaObject.invokeMethod(clock,"finish")
        assert obj("projectList").property("x")==180
        click("trashDrive")
        QMetaObject.invokeMethod(disks,"finish")
        QMetaObject.invokeMethod(clock,"finish")
        window.resize(1080,700)
        QMetaObject.invokeMethod(layout,"finish")
        for name in ("settings", "projects", "settings", "projects"):
            model.navigate(name)
            QTest.qWait(30)
        window.resize(550,400)
        wait_for(lambda: not obj("pageMotion").property("active"))
        assert not obj("pageMotion").property("active")
        model.navigate("settings")
        model.chooseSetting("animations", False)
        QTest.qWait(20)
        assert not obj("pageMotion").property("active")
        assert not QApplication.allWidgets()
        assert not diagnostics, diagnostics
        print(json.dumps({"result":"passed","checks":["original splash/glint", "startup fade/reveal", "page bands", "profile/footer handoff", "440 ms spring popup", "240 ms choice opening / 440 ms button return", "drive filter overlap and return", "deletion reflow", "three-dot menu", "disk header/sidebar path", "Trash drive fade", "rapid navigation", "resize cancellation", "animations off"]}), flush=True)
    finally:
        window.close()
        engine.deleteLater()
        app.processEvents()


if __name__ == "__main__":
    main()

