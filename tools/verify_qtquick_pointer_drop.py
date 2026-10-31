# SPDX-License-Identifier: MPL-2.0
"""Complete pointer press/move/release gestures through production tree controls."""
import json
import os
from pathlib import Path
import sys
import tempfile
import time


def main():
    repo=Path(__file__).resolve().parents[1];sys.path.insert(0,str(repo));os.chdir(repo)
    from ui.runtime import initialize_quick_runtime,IconProvider,DialogStack
    initialize_quick_runtime(webengine=True)
    from PySide6.QtCore import Qt,QUrl,QTimer,QEventLoop,QCoreApplication,QEvent,QPoint,QPointF,QObject,Property,Slot
    from PySide6.QtQml import QQmlApplicationEngine
    from PySide6.QtWidgets import QApplication
    from PySide6.QtTest import QTest
    from core.fonts import apply_app_font
    from core import ui_lang
    from main_ext.class_ThemeUi import theme
    from ui.presentation import LauncherModel
    from ui.workspace import WorkspaceSession
    from ui.image_viewer import DocumentImages
    from tools.quick_test_utils import visual
    app=QApplication([]);app.setQuitOnLastWindowClosed(False);apply_app_font(app,polish_widgets=False);theme.load();ui_lang.init()
    def wait_ms(ms):
        loop=QEventLoop();QTimer.singleShot(ms,loop.quit);loop.exec()
    def wait(predicate):
        end=time.monotonic()+10
        while not predicate():
            assert time.monotonic()<end,'Pointer drop timed out'
            wait_ms(10)
    class TerminalTarget(QObject):
        @Property(bool,constant=True)
        def active(self):return False
        @Slot('QVariantList')
        def insertDroppedPaths(self,paths):self.paths=paths
        def __init__(self):super().__init__();self.paths=[]
    with tempfile.TemporaryDirectory(prefix='rak-pointer-drop-') as temporary:
        root=Path(temporary);left=root/'ROM_Left';right=root/'ROM_Right'
        for base in (left,right):(base/'01_Source').mkdir(parents=True);(base/'02_Target').mkdir()
        from core.make_symlink import make_symlink_bytes
        for name,target in (('00_broken_link','/missing_target/'),('00_empty_link','/02_Target/')):
            (left/name).write_bytes(make_symlink_bytes(target))
        for name in ('drag_me.txt','other.txt','last.txt','terminal path.txt'):(left/name).write_text(name+' contents\n')
        (right/'return.txt').write_text('return.txt contents\n')
        filesystem=left/'03_Filesystem';(filesystem/'lifted').mkdir(parents=True)
        (filesystem/'lifted/a.prop').write_text('filesystem drag payload\n')
        (filesystem/'keep.txt').write_text('filesystem root payload\n')
        cfg=left/'config';cfg.mkdir(exist_ok=True)
        (cfg/'03_Filesystem_file_contexts').write_text('/03_Filesystem u:object_r:system_file:s0\n/03_Filesystem/lifted u:object_r:custom_file:s0\n/03_Filesystem/lifted/a\\.prop u:object_r:custom_file:s0\n/03_Filesystem/keep\\.txt u:object_r:system_file:s0\n')
        (cfg/'03_Filesystem_fs_config').write_text('03_Filesystem 0 0 0755\n03_Filesystem/lifted 1000 1001 0750\n03_Filesystem/lifted/a.prop 1000 1001 0640 capabilities=0x40\n03_Filesystem/keep.txt 0 0 0644\n')
        (cfg/'03_Filesystem_fs_options').write_text('mkfs.erofs options: --mount-point=/03_Filesystem\n')
        from core import apktool_tool
        apk=left/'folder.apk';apk.write_bytes(b'Original APK bytes')
        mirror=Path(apktool_tool.mirror_dir_for(str(left),str(apk)));mirror.mkdir(parents=True)
        (mirror/apktool_tool.APKTOOL_MARKER).write_text('{}')
        (left/'apk_drop.txt').write_text('APK folder drop\n')
        prefs=LauncherModel(palette='dark_green',language='id',settings={'effects':'high','animations':True,'treeMode':'tree'})
        session=WorkspaceSession(str(root),'ROM_Left','ROM_Right',preferences=prefs,settings_path=str(root/'settings.json'),enable_private_services=False);session._preferences_changed()
        engine=QQmlApplicationEngine();errors=[];engine.warnings.connect(lambda items:errors.extend(item.toString() for item in items))
        engine.rootContext().setContextProperty('rakPreferences',prefs)
        engine.addImageProvider('icons',IconProvider());engine.addImageProvider('rak',IconProvider());engine.addImageProvider('documents',DocumentImages())
        terminal=TerminalTarget()
        engine.setInitialProperties({'workspace':session,'preferences':prefs,'dragTerminal':terminal});engine.load(QUrl.fromLocalFile(str(repo/'tools/qtquick_native_drag_fixture.qml')));assert engine.rootObjects(),errors
        window=engine.rootObjects()[0];stack=DialogStack(window);window.requestActivate();wait(lambda:window.isActive())
        session.tree.load(str(left));session.tree2.load(str(right));wait(lambda:not session.tree.busy and not session.tree2.busy);wait_ms(100)
        tree=visual(window,'workspaceTree0');panel=tree.parentItem();right_tree=visual(window,'workspaceTree1');right_panel=right_tree.parentItem();results=[]
        def point_for(path):
            arrow=visual(window,'treeBranchArrow:'+str(path));return arrow.mapToScene(arrow.boundingRect().center()).toPoint()+QPoint(60,0)
        try:
            messages=[];session.presentationRequested.connect(lambda kind,data:messages.append(data) if kind=='message' else None)
            activated=[];session.tree.fileActivated.connect(activated.append)
            for name in ('00_broken_link','00_empty_link'):
                path=str(left/name);arrow=visual(window,'treeBranchArrow:'+path)
                QTest.mouseClick(window,Qt.LeftButton,Qt.NoModifier,arrow.mapToScene(arrow.boundingRect().center()).toPoint())
                wait(lambda:session.tree._nodes[path].loaded and not session.tree.busy)
                assert [child.texts[0] for child in session.tree._nodes[path].children]==['Empty'] and not messages
                wait_ms(60)
                arrow=visual(window,'treeBranchArrow:'+path)
                QTest.mouseClick(window,Qt.LeftButton,Qt.NoModifier,arrow.mapToScene(arrow.boundingRect().center()).toPoint())
                wait_ms(40)
            # A folder/file dropped onto itself never queues work or a modal.
            for source in (left/'01_Source',left/'drag_me.txt'):
                start=point_for(source);serial=session.transfers._serial;identity=source.stat().st_ino
                QTest.mousePress(window,Qt.LeftButton,Qt.NoModifier,start)
                QTest.mouseMove(window,start+QPoint(40,0));QTest.mouseMove(window,start)
                wait(lambda:panel.property('dragging'))
                assert panel.property('dropDestination')==str(source if source.is_dir() else source.parent)
                QTest.mouseRelease(window,Qt.LeftButton,Qt.NoModifier,start);wait_ms(60)
                assert source.stat().st_ino==identity and session.transfers._serial==serial and not messages
                assert not activated,'A self-drop opened the dragged file'
                assert not panel.property('dragging') and not panel.property('dropActive')
            for source,destination in ((right/'return.txt',left/'02_Target'),(left/'drag_me.txt',left/'02_Target'),(left/'other.txt',right/'02_Target'),(left/'last.txt',right)):
                # File worker completion precedes TreeView's next layout/polish.
                wait_ms(80)
                identity=source.stat().st_ino
                source_panel=panel if source.is_relative_to(left) else right_panel
                header_before=window.grabWindow().copy(0,48,40,28)
                target=right_tree.mapToScene(QPointF(90,right_tree.height()-20)).toPoint() if destination==right else point_for(destination)
                start=point_for(source);QTest.mousePress(window,Qt.LeftButton,Qt.NoModifier,start)
                QTest.mouseMove(window,start+QPoint(30,0),20);QTest.mouseMove(window,start+QPoint(45,0),20)
                wait(lambda:source_panel.property('dragging'));assert not source_panel.property('nativeDragging')
                for n in range(1,11):
                    point=QPoint(round(start.x()+(target.x()-start.x())*n/10),round(start.y()+(target.y()-start.y())*n/10))
                    QTest.mouseMove(window,point,20);wait_ms(10)
                owner=panel if destination.is_relative_to(left) else right_panel
                wait(lambda:owner.property('dropActive'))
                print('POINTER_TARGET',source.name,owner.property('dropDestination'),owner.property('dropAccepted'),errors,flush=True)
                assert owner.property('dropDestination')==str(destination)
                if source.is_relative_to(right):
                    out=repo/'build/qtquick_migration/drag_native';out.mkdir(parents=True,exist_ok=True)
                    frame=window.grabWindow();frame.save(str(out/'right_to_left_drag.png'))
                    assert frame.copy(0,48,40,28)==header_before,'Right drag preview leaked into the left header'
                if destination==right/'02_Target':
                    out=repo/'build/qtquick_migration/drag_native';out.mkdir(parents=True,exist_ok=True)
                    window.grabWindow().save(str(out/'pointer_drag_preview.png'))
                QTest.mouseRelease(window,Qt.LeftButton,Qt.NoModifier,target)
                expected=destination/source.name
                wait(lambda:expected.exists() and not source.exists());wait(lambda:not session._operation_count and not session.tree.busy and not session.tree2.busy)
                assert expected.read_text()==source.name+' contents\n'
                assert expected.stat().st_ino==identity,'Internal drop copied bytes instead of moving the file'
                assert not activated,'A drag opened the dragged file'
                assert not source_panel.property('dragging') and not owner.property('dropActive')
                results.append(str(expected.relative_to(root)))
            wait_ms(80)
            source=left/'terminal path.txt';start=point_for(source)
            pane=visual(window,'dragTerminalPane');target=pane.mapToScene(pane.boundingRect().center()).toPoint()
            QTest.mousePress(window,Qt.LeftButton,Qt.NoModifier,start)
            QTest.mouseMove(window,start+QPoint(30,0),20);QTest.mouseMove(window,start+QPoint(45,0),20)
            wait(lambda:panel.property('dragging'))
            for n in range(1,11):
                QTest.mouseMove(window,QPoint(round(start.x()+(target.x()-start.x())*n/10),round(start.y()+(target.y()-start.y())*n/10)),20);wait_ms(10)
            QTest.mouseRelease(window,Qt.LeftButton,Qt.NoModifier,target)
            wait(lambda:terminal.paths)
            assert [QUrl(path).toLocalFile() for path in terminal.paths]==[str(source).replace('\\','/')]
            assert source.read_text()=='terminal path.txt contents\n' and not panel.property('dragging')
            # A single coalesced mouse movement must start and complete a drop.
            # The APK row represents its decompiled folder, not the .apk file.
            source=left/'apk_drop.txt';start=point_for(source);target=point_for(apk)
            identity=source.stat().st_ino
            QTest.mousePress(window,Qt.LeftButton,Qt.NoModifier,start)
            QTest.mouseMove(window,target)
            QTest.mouseRelease(window,Qt.LeftButton,Qt.NoModifier,target)
            wait(lambda:(mirror/'apk_drop.txt').exists() and not source.exists())
            wait(lambda:not session._operation_count and not session.tree.busy)
            assert (mirror/'apk_drop.txt').read_text()=='APK folder drop\n'
            assert (mirror/'apk_drop.txt').stat().st_ino==identity
            assert apk.read_bytes()==b'Original APK bytes'
            assert not list((left/'config').glob('apktool*'))
            # Drag a filesystem child out, then into a folder without metadata.
            wait_ms(100)
            arrow=visual(window,'treeBranchArrow:'+str(filesystem))
            QTest.mouseClick(window,Qt.LeftButton,Qt.NoModifier,arrow.mapToScene(arrow.boundingRect().center()).toPoint())
            wait(lambda:session.tree._nodes[str(filesystem)].loaded);wait_ms(100)
            original_metadata={path.name:path.read_bytes() for path in cfg.glob('03_Filesystem*')}
            for source,destination in ((filesystem/'lifted',left),(left/'lifted',left/'02_Target'),(filesystem,right/'02_Target')):
                wait_ms(100);identity=source.stat().st_ino
                start=point_for(source)
                target=tree.mapToScene(QPointF(90,tree.height()-20)).toPoint() if destination==left else point_for(destination)
                QTest.mousePress(window,Qt.LeftButton,Qt.NoModifier,start)
                QTest.mouseMove(window,start+QPoint(30,0),20);QTest.mouseMove(window,target,20)
                wait(lambda:panel.property('dragging'))
                owner=right_panel if destination.is_relative_to(right) else panel
                wait(lambda:owner.property('dropDestination')==str(destination))
                QTest.mouseRelease(window,Qt.LeftButton,Qt.NoModifier,target)
                expected=destination/source.name
                wait(lambda:expected.exists() and not source.exists())
                wait(lambda:not session._operation_count and not session.tree.busy and not session.tree2.busy);wait_ms(100)
                assert expected.stat().st_ino==identity
                assert {name:(cfg/name).read_bytes() for name in original_metadata}==original_metadata
                if destination==left:
                    wait(lambda:session.tree._nodes[str(expected)].data(0,Qt.UserRole+1)=='EROFS')
                    assert 'lifted/a.prop 1000 1001 0640 capabilities=0x40' in (cfg/'lifted_fs_config').read_text()
                    assert '/lifted/a\\.prop u:object_r:custom_file:s0' in (cfg/'lifted_file_contexts').read_text()
                elif destination==left/'02_Target':
                    assert (expected/'a.prop').read_text()=='filesystem drag payload\n'
                    assert not (cfg/'02_Target_fs_config').exists() and not (cfg/'02_Target_file_contexts').exists()
                else:
                    assert (expected/'keep.txt').read_text()=='filesystem root payload\n'
                    assert not list((right/'config').glob('03_Filesystem*'))
            # Keep dragging while TreeView recycles the original source row.
            source_folder=left/'01_Source';(source_folder/'scroll_move.txt').write_bytes(b'Long viewport drag\n')
            folder_identity=source_folder.stat().st_ino;child_identity=(source_folder/'scroll_move.txt').stat().st_ino
            for number in range(50):(left/f'50_scroll_{number:02d}').mkdir()
            scroll_target=left/'99_ScrollTarget';scroll_target.mkdir()
            session.tree.refresh();wait(lambda:not session.tree.busy);wait_ms(100)
            tree.setProperty('contentY',-tree.property('topMargin'));wait_ms(50)
            start=point_for(source_folder);bottom=tree.mapToScene(QPointF(90,tree.height()-8)).toPoint()
            QTest.mousePress(window,Qt.LeftButton,Qt.NoModifier,start)
            QTest.mouseMove(window,start+QPoint(30,0),20);QTest.mouseMove(window,start+QPoint(45,0),20)
            wait(lambda:panel.property('dragging'))
            QTest.mouseMove(window,bottom,20)
            deadline=time.monotonic()+12;target=None
            while target is None:
                assert panel.property('dragging'),'Source delegate recycling cancelled the drag'
                assert time.monotonic()<deadline,'Auto-scroll never reached the target folder'
                try:
                    candidate=point_for(scroll_target)
                    if candidate.y()<bottom.y()-20:target=candidate
                except AssertionError:pass
                if target is None:wait_ms(20)
            assert tree.property('contentY')>tree.height(),'Source never left the recycled viewport'
            QTest.mouseMove(window,target,20)
            wait(lambda:panel.property('dropDestination')==str(scroll_target))
            QTest.mouseRelease(window,Qt.LeftButton,Qt.NoModifier,target)
            expected=scroll_target/'01_Source'/'scroll_move.txt'
            wait(lambda:expected.exists() and not source_folder.exists())
            wait(lambda:not session._operation_count and not session.tree.busy)
            assert expected.read_bytes()==b'Long viewport drag\n'
            assert expected.stat().st_ino==child_identity and expected.parent.stat().st_ino==folder_identity
            assert not errors,errors;assert not QApplication.allWidgets()
            out=repo/'build/qtquick_migration/drag_native';out.mkdir(parents=True,exist_ok=True)
            report={'result':'passed','completePointerDrops':results,'filesystemChildOutAndIntoPlainFolder':True,'filesystemCrossRomIntoPlainFolder':True,'rightToLeftPreviewNoHeaderArtifact':True,'selfDropNoWorkOrError':True,'missingAndEmptySymlinkFolders':True,'singleMoveApkFolderDrop':True,'terminalPathDrop':True,'autoScrollAndRecycledSourceDrop':True,'sameVolumeFileAndFolderIdentityPreserved':True,'hardwareMouse':False,'qwidgetCount':0}
            (out/'pointer_report.json').write_text(json.dumps(report,indent=2));print(json.dumps(report),flush=True)
        finally:
            stack.close();session.stop();window.close();engine.deleteLater();session.deleteLater()
            QCoreApplication.sendPostedEvents(None,QEvent.DeferredDelete);app.processEvents()


if __name__=='__main__':main()
