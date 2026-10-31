# SPDX-License-Identifier: MPL-2.0
"""Isolated native workspace preview; all writable data is temporary."""
import argparse
from pathlib import Path
import sys
import os
import tempfile
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifacts", type=Path, default=Path("build/qtquick_migration/quick_workspace"))
    parser.add_argument("--size", default="1080x700")
    parser.add_argument("--reference", action="store_true", help="Match audit_workspace's inert twenty-row fixture")
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[1]
    os.chdir(repo);sys.path.insert(0,str(repo))
    from core.fonts import configure_text_rendering_env, apply_app_font
    configure_text_rendering_env()
    from ui.runtime import initialize_quick_runtime
    initialize_quick_runtime(webengine=True)
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication
    from PySide6.QtTest import QTest
    from main_ext.class_ThemeUi import theme
    from core import ui_lang
    from ui.presentation import LauncherModel
    from ui.workspace_window import QuickWorkspace
    app=QApplication([]);app.setQuitOnLastWindowClosed(False);apply_app_font(app)
    theme.load();ui_lang.init()
    diagnostics=[]
    with tempfile.TemporaryDirectory(prefix="rak-native-workspace-") as temporary:
        root=Path(temporary)
        for name in ("system","vendor"):
            folder=root/name;folder.mkdir()
            (folder/"build.prop").write_text("ro.fixture=qtquick\n",encoding="utf-8")
            (folder/"documents").mkdir()
            for number in range(20):
                (folder/f"partition_{number:02}.txt").write_text("Native workspace fixture\n",encoding="utf-8")
        avatar=""
        try:
            from rennsproject.avatar.handler import resolve_avatar_path
            from PySide6.QtCore import QUrl
            avatar=QUrl.fromLocalFile(resolve_avatar_path('001',{'avatar':'001'})).toString()
        except ImportError:pass
        prefs=LauncherModel(palette='dark_green' if args.reference else theme.name,language=ui_lang._current_lang,settings={"effects":"medium","animations":True,"treeMode":"single_dir"},
                            profile={"name":"Fixture","subtitle":"Developer","avatar":avatar})
        window=QuickWorkspace(str(root),"system","vendor",preferences=prefs,settings_path=str(root/"settings.json"))
        window.engine.warnings.connect(lambda errors: diagnostics.extend(error.toString() for error in errors))
        window.window.resize(*map(int,args.size.split("x")))
        try:
            deadline=time.monotonic()+35
            while not window._ready_emitted and time.monotonic()<deadline:QTest.qWait(25)
            assert window._ready_emitted, ("Workspace not ready",diagnostics)
            if args.reference:
                from tools.quick_test_utils import visual,popup_frame
                from PySide6.QtCore import Qt
                from ui.directory import DirectoryEntry
                from core.tree_entries import IS_FOLDER_ROLE,SIZE_LABEL_ROLE
                for directory in window.session.directories:
                    directory._clear_watches();directory.beginResetModel()
                    directory._root.children=[]
                    directory._nodes={directory._root.path:directory._root}
                    for number in range(20):
                        name=f'partition_{number:02}.img'
                        entry=DirectoryEntry([name,'u:object_r:system_file:s0','rw-r--r--'],parent=directory._root,row=number)
                        entry.setData(0,Qt.UserRole,str(root/'system'/name))
                        entry.setData(0,IS_FOLDER_ROLE,False);entry.setData(0,SIZE_LABEL_ROLE,'340.0 MB');entry.setIcon(0,'file')
                        directory._root.children.append(entry);directory._nodes[entry.path]=entry
                    directory.endResetModel();directory.changed.emit()
                window.session._layout=1;window.session.changed.emit()
            window.reveal();QTest.qWait(700)
            args.artifacts.mkdir(parents=True,exist_ok=True)
            window.window.grabWindow().save(str(args.artifacts/"workspace.png"))
            if args.reference:
                window.session._layout=2;window.session.changed.emit();QTest.qWait(120)
                window.window.grabWindow().save(str(args.artifacts/'workspace_split.png'))
                window.session._layout=3;window.session.changed.emit();QTest.qWait(120)
                window.window.grabWindow().save(str(args.artifacts/'workspace_side_by_side.png'))
                window.session.dispatch('settings',{});QTest.qWait(400)
                window.window.grabWindow().save(str(args.artifacts/'project_settings.png'))
                window.engine._dialog_stack.close();QTest.qWait(150)
                window.session.presentationRequested.emit('makeFilesystem',{'path':str(root/'system'/'product'),'name':'product'})
                QTest.qWait(400)
                popup_frame(window.window,visual(window.window,'workspaceMakeFilesystem')).save(str(args.artifacts/'make_filesystem.png'))
                window.engine._dialog_stack.close();QTest.qWait(150)
                window.session.presentationRequested.emit('neobranchCreate',{'root':'system'})
                QTest.qWait(400)
                popup_frame(window.window,visual(window.window,'workspaceBranchCreate')).save(str(args.artifacts/'neobranch_create.png'))
                window.engine._dialog_stack.close();QTest.qWait(150)
                window.session.presentationRequested.emit('neobranchPicker',{'root':'system','branches':[{'name':'nfc_test','color':'blue'},{'name':'camera_fix','color':'green'}],'active':'nfc_test'})
                QTest.qWait(400)
                popup_frame(window.window,visual(window.window,'workspaceBranchPicker')).save(str(args.artifacts/'neobranch_picker.png'))
                window.engine._dialog_stack.close();QTest.qWait(150)
            assert not QApplication.allWidgets(), "Workspace instantiated QWidget presentation"
            window.session.openPath(str(root/"system"/"build.prop"));QTest.qWait(700)
            window.window.grabWindow().save(str(args.artifacts/"editor.png"))
            assert not diagnostics,diagnostics
            print("Native workspace ready; real Monaco, directory models and zero QWidgets",flush=True)
        finally:
            window._shutdown_and_quit=app.quit
            window.closed.connect(app.quit)
            window.dispose()
            QTimer.singleShot(5000,app.quit);app.exec()


if __name__=="__main__":main()
