# SPDX-License-Identifier: MPL-2.0
"""Verify the production launcher against temporary project/settings files."""
import json
import os
from pathlib import Path
import sys
import tempfile
import shutil
import time
from unittest.mock import patch


def main():
    repo = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(repo))
    os.chdir(repo)
    from core.fonts import configure_text_rendering_env, apply_app_font
    configure_text_rendering_env()
    from ui.runtime import initialize_quick_runtime
    initialize_quick_runtime(webengine=True)
    from PySide6.QtCore import QObject, Qt, QTimer, QMetaObject
    from PySide6.QtQuick import QQuickItem,QQuickWindow
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication
    import main_ext.class_ThemeUi as theme_module
    from core import ui_lang
    from app import AppUI, _end_startup_import_window
    from ui.application import LauncherApplication
    from ui.project_store import ProjectStore

    app = QApplication([])
    app.setQuitOnLastWindowClosed(False)
    apply_app_font(app,polish_widgets=False)
    diagnostics = []
    with tempfile.TemporaryDirectory(prefix="rak-quick-integration-") as temporary:
        root = Path(temporary)
        with patch.object(theme_module, "_THEME_FILE", str(root / "theme.json")), \
             patch.object(theme_module, "_SETTINGS_FILE", str(root / "settings.json")), \
             patch.object(ui_lang, "_LANG_FILE", str(root / "lang.json")), \
             patch("core.rak_cli.ensure_rak_bat"), patch("core.rak_cli.maybe_autostart"), \
             patch('core.native_backend.create_path_index',side_effect=AssertionError('Startup must not build a recursive path map')) as path_map, \
             patch('core.project_metrics.folder_size_str',side_effect=AssertionError('Project size scanning is disabled')) as size_scan:
            store = ProjectStore(root / "metadata")
            store.set_location(str(root / "projects"))
            create=store.create
            def create_populated(arguments,**kwargs):
                record=create(arguments,**kwargs)
                for port in (record['port1'],record.get('port2')):
                    if not port:continue
                    folder=Path(record['path'])/port
                    (folder/'build.prop').write_text('ro.fixture=true\n',encoding='utf-8')
                    nested=folder/'fixture-folder';nested.mkdir()
                    (nested/'notes.txt').write_text('Startup directory fixture\n',encoding='utf-8')
                return record
            store.create=create_populated
            # Real Trash metadata/jobs, with moves confined to this temporary
            # tree instead of leaving test entries in the Windows Recycle Bin.
            class FixtureRecycle:
                def recycle(self,path,*,on_recycled=None):
                    source=Path(path).resolve()
                    assert source.is_relative_to(root.resolve())
                    destination=root/"recycled"/source.name
                    destination.parent.mkdir(exist_ok=True)
                    shutil.move(str(source),str(destination))
                    handle={"fixture":str(destination)}
                    if on_recycled: on_recycled(handle)
                    return handle
                def restore(self,handle,path):
                    source=Path(handle["fixture"]).resolve()
                    assert source.is_relative_to(root.resolve())
                    shutil.move(str(source),path)
            store.trash.backend=FixtureRecycle()
            runtime = LauncherApplication(app, AppUI, store=store, start_services=False)
            runtime.engine.warnings.connect(lambda errors: diagnostics.extend(error.toString() for error in errors))
            window = runtime.window
            runtime._windows_usage=[{'key':'C:','total':1024,'used':512,'percent':50}]
            runtime._update_drives()
            assert len(runtime.model._drives)==1 and runtime.model._drives[0]['fill'].startswith('#')
            QTest.qWait(150)
            _end_startup_import_window()
            assert not QApplication.allWidgets(), "Launcher created legacy QWidget instances"

            def item(name):
                pending = [window.contentItem()]
                while pending:
                    candidate = pending.pop()
                    if candidate.objectName() == name:
                        return candidate
                    pending.extend(candidate.childItems())
                raise AssertionError(name)

            def click(name):
                target = item(name)
                QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier,
                                 target.mapToScene(target.boundingRect().center()).toPoint())
                QTest.qWait(30)

            def wait_for(predicate, timeout=60):
                deadline = time.monotonic() + timeout
                while not predicate():
                    if time.monotonic() >= deadline:
                        message=window.findChild(QObject,"launcherMessage")
                        raise AssertionError(("Timed out waiting for production launcher handoff",{
                            'page':runtime.model.page,'busy':runtime.model.busy,'windowVisible':window.isVisible(),
                            'message':{key:message.property(key) for key in ('visible','opacity','presented','active')},
                            'diagnostics':diagnostics}))
                    QTest.qWait(50)

            try:
                runtime.model.choosePalette("dark_green")
                runtime.model.chooseLanguage("id")
                runtime.model.chooseSetting("effects", "high")
                runtime.model.chooseSetting("blurRadius", 12)
                runtime.model.chooseSetting("animations", False)
                runtime.model.chooseSetting("treeMode", "single_dir")
                wait_for(lambda:runtime.engine._appearance_captures.pending is None and not runtime.engine._appearance_captures.queued)
                assert json.loads((root / "theme.json").read_text())["theme"] == "dark_green"
                assert json.loads((root / "lang.json").read_text())["lang"] == "id"
                saved = json.loads((root / "settings.json").read_text())
                assert saved["effect_level"] == "high" and saved["tree_view_mode"] == "single_dir"
                assert saved["blur_radius"] == 12 and runtime._settings()['blurRadius'] == 12
                assert runtime.model.page=="create", "First run must show the original full create page"
                item("projectName").setProperty("text", "Quick integration")
                item("port1Name").setProperty("text", "system")
                click("dualRomButton")
                item("port2Name").setProperty("text", "vendor")
                assert item("confirmProjectButton").isEnabled()
                click("confirmProjectButton")
                wait_for(lambda: runtime.editor is not None and runtime.editor.isVisible())
                editor = runtime.editor
                wait_for(lambda: not editor._entry_transition_active)
                assert isinstance(editor.window,QQuickWindow)
                path_map.assert_not_called()
                assert all(not node.loaded for directory in editor.session.directories for node in directory._root.children if node.folder), 'Startup descended into unopened folders'
                assert editor.session.prepared and not editor.diagnostics,editor.diagnostics
                assert not QApplication.allWidgets(),"Production Main constructed QWidget UI"
                assert len(store.projects()) == 1
                record = store.projects()[0]
                assert editor.project == record["path"] and record["mode"] == 2
                assert not window.isVisible()
                editor.return_to_project()
                QTest.qWait(50)
                QTest.keyClick(editor.window,Qt.Key_Return)
                wait_for(lambda: window.isVisible() and runtime.editor is None)
                assert runtime.model.page=="projects"
                assert item("projectList").property("count") == 1
                click("createButton")
                popup=window.findChild(QObject,"createProjectPopup")
                assert popup.property("opened")
                item("popupProjectName").setProperty("text","Quick popup")
                item("popupPort1").setProperty("text","system")
                with patch.object(runtime,"_open_project") as opened:
                    click("popupConfirmProject")
                    wait_for(lambda: len(store.projects())==2 and not popup.property("visible") and opened.called)
                    assert opened.call_args.args[1]=="create_popup"
                runtime.handle("projectAction", {"action": "pin", "path": record["path"]})
                assert store.projects()[0]["pinned"]
                runtime.handle("projectMenu", {"path": record["path"], "x": 800, "y": 100})
                QTest.qWait(50)
                assert item("projectAction_pin").property("text") == ui_lang.t("menu_unpin_project")
                runtime.model.search("does not match")
                assert item("projectList").property("count") == 0
                runtime.model.search("")
                assert item("projectList").property("count") == 2
                # Close the menu, enable animation and delete the small fixture
                # through its actual confirmation, worker and native list scene.
                actions=window.findChild(QObject,"projectActionsPopup")
                QMetaObject.invokeMethod(actions,"close")
                runtime.model.chooseSetting("animations",True)
                doomed=next(row for row in store.projects() if row["project_name"]=="Quick popup")
                runtime.handle("projectAction",{"action":"delete","path":doomed["path"]})
                message=window.findChild(QObject,"launcherMessage")
                wait_for(lambda:message.property("visible") and message.property("opacity")==1)
                deletion_start=time.monotonic()
                QTest.keyClick(window,Qt.Key_Return)
                QTest.qWait(230)
                assert runtime.model.busy and runtime.model.deletingPath==doomed["path"]
                assert item("deleteProgress_"+doomed["path"]).isVisible()
                wait_for(lambda: item("projectListMotion").property("active"))
                assert time.monotonic()-deletion_start>=.50
                assert runtime.model.busy, "Deletion input unlocked before the exit scene finished"
                window.grabWindow().save(str(repo/"build/qtquick_migration/application/delete_motion.png"))
                wait_for(lambda: not item("projectListMotion").property("active") and not runtime.model.busy)
                assert item("projectList").property("count")==1 and not Path(doomed["path"]).exists()
                entry=store.trash.entries()[0]
                assert len(runtime.model._trash)==1 and runtime.model._trash[0]['toolTip']
                runtime.handle("restoreProject",{"id":entry["id"],"path":doomed["path"]})
                wait_for(lambda: not runtime.model.busy and not item("projectListMotion").property("active"))
                assert item("projectList").property("count")==2 and Path(doomed["path"]).is_dir()
                assert not store.trash.entries()
                assert not runtime.model._trash
                # Repeat the real launcher handoff with animations enabled.
                runtime._open_project(record)
                wait_for(lambda:runtime.editor is not None and runtime.editor.isVisible())
                reopened=runtime.editor
                wait_for(lambda:not reopened._entry_transition_active)
                assert not QApplication.allWidgets(),"Reopening Main created widgets"
                reopened.session.openAs(str(Path(record['path'])/record['port1']/'build.prop'),'editor')
                wait_for(lambda:bool(reopened.session.activeTab))
                reopened.return_to_project()
                QTest.qWait(400)
                QTest.keyClick(reopened.window,Qt.Key_Return)
                wait_for(lambda:runtime.editor is None and window.isVisible())
                # Cancel after construction and retain owners until web teardown.
                runtime._open_project(record)
                wait_for(lambda:runtime.editor is not None)
                runtime._cancel_open()
                wait_for(lambda:not runtime._retiring_editors)
                assert runtime.editor is None and window.isVisible()
                assert not QApplication.allWidgets()
                artifacts = repo / "build/qtquick_migration/application"
                artifacts.mkdir(parents=True, exist_ok=True)
                window.grabWindow().save(str(artifacts / "launcher.png"))
                assert not diagnostics, diagnostics
                size_scan.assert_not_called()
                print(json.dumps({"result": "passed", "checks": ["no hidden widgets", "real settings persistence", "first-run create page", "native create popup", "real project creation", "Main preparation/reveal without recursive path map", "return to Quick", "pin/menu/search", "550 ms delete indicator", "blocked deletion/reflow", "temporary Trash and restore", "no project size scans"]}), flush=True)
            finally:
                if runtime.editor is not None:
                    runtime._cancel_open()
                QTimer.singleShot(0, runtime.quit)
                app.exec()
                assert runtime.engine is None,"QML engine survived the GUI event loop"
    theme_module.theme.load()
    ui_lang.init()


if __name__ == "__main__":
    main()
