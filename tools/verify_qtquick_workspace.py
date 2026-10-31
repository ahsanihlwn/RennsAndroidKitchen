# SPDX-License-Identifier: MPL-2.0
"""Exercise native workspace input, document lifetime, panes and file operations."""
import json
import os
from pathlib import Path
import sys
import tempfile
import time


def main():
    repo=Path(__file__).resolve().parents[1];os.chdir(repo);sys.path.insert(0,str(repo))
    import faulthandler
    crash_path=repo/'build'/'qtquick_migration'/'workspace'/'native_crash.log'
    crash_path.parent.mkdir(parents=True,exist_ok=True)
    crash_log=crash_path.open('w',encoding='utf-8')
    faulthandler.enable(file=crash_log,all_threads=True)
    from core.fonts import configure_text_rendering_env,apply_app_font
    configure_text_rendering_env()
    from ui.runtime import initialize_quick_runtime
    initialize_quick_runtime(webengine=True)
    from PySide6.QtCore import Qt,QTimer,QPoint,QMetaObject,Q_ARG,QCoreApplication,QEvent,QEventLoop,QUrl,QObject
    from PySide6.QtGui import QKeyEvent
    from PySide6.QtMultimedia import QMediaPlayer
    from PySide6.QtWidgets import QApplication
    from PySide6.QtTest import QTest
    from core import ui_lang
    from main_ext.class_ThemeUi import theme
    from core.terminal_pty import PtySignals
    from ui.presentation import LauncherModel
    from ui.terminal import TerminalSession
    from ui.workspace_window import QuickWorkspace
    from tools.quick_test_utils import visual,popup_frame
    from tools.audit_so_viewer import fixture_bytes as elf_fixture
    from tools.audit_partition_viewers import payload_fixture
    app=QApplication([]);app.setQuitOnLastWindowClosed(False);apply_app_font(app,polish_widgets=False)
    def wait_ms(milliseconds):
        loop=QEventLoop();QTimer.singleShot(milliseconds,loop.quit);loop.exec()
    theme.load();ui_lang.init()
    class FakePty:
        def __init__(self,cwd):
            self.cwd=cwd;self.signals=PtySignals();self.bash_mode=False;self.writes=[];self.stopped=False;self.spam=False
        def start(self,**kwargs):self.signals.data_ready.emit('NATIVE_WORKSPACE_TERMINAL\r\n')
        def write(self,data):self.writes.append(data)
        def resize(self,*args):pass
        def shutdown(self):self.stopped=True
        def set_spam_enter(self,enabled):self.spam=enabled
    def terminal_factory(cwd=None,**options):return TerminalSession(manager=FakePty(cwd),**options)
    def wait(predicate,seconds=15):
        deadline=time.monotonic()+seconds
        while not predicate():
            if time.monotonic()>deadline:raise AssertionError('Timed out')
            wait_ms(25)
    def qml_call(owner,name,*arguments):
        types={str:'QVariant',int:'QVariant',dict:'QVariant',bool:'QVariant'}
        return QMetaObject.invokeMethod(owner,name,*[Q_ARG(types[type(argument)],argument) for argument in arguments])
    def type_text(value):
        for char in value:
            QCoreApplication.sendEvent(window,QKeyEvent(QEvent.KeyPress,0,Qt.NoModifier,char))
            QCoreApplication.sendEvent(window,QKeyEvent(QEvent.KeyRelease,0,Qt.NoModifier,char))
    with tempfile.TemporaryDirectory(prefix='rak-workspace-qa-') as temporary:
        root=Path(temporary)
        import wave,zipfile
        from PySide6.QtGui import QImage,QColor
        for port in ('system','vendor'):
            folder=root/port;folder.mkdir()
            (folder/'build.prop').write_text('ro.fixture=original\n',encoding='utf-8')
            (folder/'notes.txt').write_text('Native workspace\n',encoding='utf-8')
            (folder/'fixture.weird').write_text('Unknown file fixture\n',encoding='utf-8')
            (folder/'run_fixture.py').write_text('print("workspace fixture")\n',encoding='utf-8')
            (folder/'fixture.bin').write_bytes(b'Native binary string\x00'*400)
            (folder/'fixture.so').write_bytes(elf_fixture())
            (folder/'payload.bin').write_bytes(payload_fixture())
            image_fixture=QImage(128,96,QImage.Format_ARGB32);image_fixture.fill(QColor('#ef6742'));image_fixture.save(str(folder/'photo.png'))
            with wave.open(str(folder/'silence.wav'),'wb') as audio_fixture:
                audio_fixture.setnchannels(1);audio_fixture.setsampwidth(2);audio_fixture.setframerate(8000);audio_fixture.writeframes(b'\0'*32000)
            with zipfile.ZipFile(folder/'fixture.zip','w') as archive_fixture:
                archive_fixture.writestr('folder/inside.txt','Archive content');archive_fixture.writestr('readme.txt','Native archive')
            (folder/'nested').mkdir()
            (folder/'nested'/'inside.txt').write_text('Nested file\n',encoding='utf-8')
        partition=root/'system'/'product';(partition/'etc').mkdir(parents=True)
        (partition/'etc'/'branch.txt').write_text('Source partition\n',encoding='utf-8')
        config=root/'system'/'config';config.mkdir()
        (config/'product_fs_config').write_text('product 0 0 0755\nproduct/etc 0 0 0755\nproduct/etc/branch.txt 0 0 0644\n',encoding='utf-8')
        (config/'product_file_contexts').write_text('/product u:object_r:system_file:s0\n/product/etc u:object_r:system_file:s0\n/product/etc/branch\\.txt u:object_r:system_file:s0\n',encoding='utf-8')
        from tools.mount_fixture import build_fixture
        mount_image=build_fixture(root/'system')
        from tools.video_fixture import build_fixture as video_fixture
        video_path=str(video_fixture(root/'system'))
        prefs=LauncherModel(palette='dark',language='id',settings={'effects':'high','animations':True,'treeMode':'single_dir'},profile={})
        workspace=QuickWorkspace(str(root),'system','vendor',preferences=prefs,settings_path=str(root/'preferences.json'),terminal_factory=terminal_factory,enable_private_services=False)
        window=workspace.window;session=workspace.session
        diagnostics=workspace.diagnostics
        last_click={}
        def click(name):
            # Python state changes precede the next Qt Quick layout/polish.
            wait_ms(40)
            motion=visual(window,'workspaceLayoutMotion')
            wait(lambda:not motion.property('running') and not motion.property('pending'))
            stack=workspace.engine._dialog_stack
            while stack.stack:
                top=stack.stack[-1]
                if top.property('closing'):
                    wait(lambda:not top.property('visible'));continue
                wait(lambda:top.property('opened'));break
            target=visual(window,name)
            assert target is not None,name
            assert target.isVisible() and target.isEnabled(),name
            window.requestActivate()
            point=target.mapToScene(target.boundingRect().center()).toPoint()
            QTest.mouseMove(window,point);wait_ms(25)
            point=target.mapToScene(target.boundingRect().center()).toPoint()
            QTest.mousePress(window,Qt.LeftButton,Qt.NoModifier,point);wait_ms(25)
            last_click.update(name=name,point=[point.x(),point.y()],pressed=target.property('pressed'),active=window.isActive(),
                              currentPoint=[target.mapToScene(target.boundingRect().center()).x(),target.mapToScene(target.boundingRect().center()).y()])
            QTest.mouseRelease(window,Qt.LeftButton,Qt.NoModifier,point)
            wait_ms(50)
        try:
            wait(lambda:workspace._ready_emitted,35)
            workspace.reveal();wait(lambda:not workspace._entry_transition_active)
            assert not QApplication.allWidgets()
            thumbnail_path=str(root/'system'/'photo.png')
            wait(lambda:session.tree._nodes[thumbnail_path].thumbnail_url)
            assert session.tree._nodes[thumbnail_path].thumbnail_url.startswith('image://documents/thumb-')
            tree=visual(window,'workspaceTree0')
            prefs.chooseSetting('treeMode','tree');wait(lambda:not session.tree.busy and session.tree._root.loaded)
            wait_ms(100)
            branch_path=str(root/'system'/'nested')
            arrow_name='treeBranchArrow:'+branch_path
            def arrow_rotation():
                try:return visual(window,arrow_name).property('rotation')
                except (RuntimeError,AssertionError):return None
            arrow=visual(window,arrow_name)
            assert arrow.width()==12 and arrow.height()==12 and arrow.property('rotation')==0
            assert 'arrow_right?color=' in arrow.property('source').toString()
            arrow_point=arrow.mapToScene(arrow.boundingRect().center()).toPoint()
            QTest.mouseClick(window,Qt.LeftButton,Qt.NoModifier,arrow_point)
            wait(lambda:arrow_rotation()==90)
            wait(lambda:str(root/'system'/'nested'/'inside.txt') in session.tree._nodes)
            arrow=visual(window,arrow_name);arrow_point=arrow.mapToScene(arrow.boundingRect().center()).toPoint()
            QTest.mouseClick(window,Qt.LeftButton,Qt.NoModifier,arrow_point)
            wait(lambda:arrow_rotation()==0)
            window.grabWindow().save(str(repo/'build/qtquick_migration/workspace/tree_svg_arrows.png'))
            prefs.chooseSetting('treeMode','single_dir');wait(lambda:not session.tree.busy and session.tree._root.loaded)
            wait_ms(80)
            # Real pointer selection -> native inline rename -> queued mutation.
            notes=str(root/'system'/'notes.txt')
            index=session.tree.indexForPath(notes)
            row=index.row()
            point=tree.mapToScene(QPoint(90,round(row*28-tree.property('contentY')+14))).toPoint()
            QTest.mouseClick(window,Qt.LeftButton,Qt.NoModifier,point)
            wait(lambda:session._selection==[notes])
            QTest.keyClick(window,Qt.Key_F2);wait_ms(100)
            QTest.keyClick(window,Qt.Key_A,Qt.ControlModifier)
            type_text('renamed.txt');QTest.keyClick(window,Qt.Key_Return)
            wait(lambda:(root/'system'/'renamed.txt').exists())
            wait(lambda:not session._operation_count)
            wait(lambda:not session.tree.busy)
            assert not (root/'system'/'notes.txt').exists()
            # New item starts as an editable dummy; Escape never creates it.
            session.selectTree(0);session.selectPaths(0,[])
            click('workspaceAction_newFile');type_text('created.txt');QTest.keyClick(window,Qt.Key_Return)
            wait(lambda:(root/'system'/'created.txt').exists())
            wait(lambda:not session._operation_count)
            wait(lambda:not session.tree.busy)
            assert (root/'system'/'config').is_dir(), 'Creating a file must preserve the config folder'
            assert session.history[-1]['label'].startswith('Create'), session.history
            click('workspaceAction_newFolder');type_text('cancelled');QTest.keyClick(window,Qt.Key_Escape)
            wait_ms(150);assert not (root/'system'/'cancelled').exists()
            # The native transfer dialog resolves conflicts before mutation.
            incoming=root/'incoming';incoming.mkdir();(incoming/'same.txt').write_text('incoming')
            target=root/'system'/'same.txt';target.write_text('existing')
            def transfer():session.transfers.start(str(root/'system'),[(str(incoming),str(incoming/'same.txt'))],str(root/'system'))
            conflicts=window.findChild(QObject,'workspaceFileConflicts')
            expected=len(session.history)+1
            transfer();wait(lambda:conflicts.property('presented'));wait_ms(350)
            click('conflictSkip');wait(lambda:len(session.history)>=expected and not session._operation_count);wait_ms(200)
            assert target.read_text()=='existing'
            expected=len(session.history)+1
            transfer();wait(lambda:conflicts.property('presented'));wait_ms(350)
            click('conflictReplace');wait(lambda:len(session.history)>=expected and not session._operation_count);assert target.read_text()=='incoming'
            assert not (incoming/'config').exists()
            # Source symlink preset pages and actual placeholder bytes.
            session.dispatch('symlink',{'path':str(root/'system')});wait_ms(350)
            click('symlinkTab1');click('symlinkSelectAll');click('symlinkClearAll')
            click('symlinkTab0')
            visual(window,'symlinkName').setProperty('text','native_link')
            visual(window,'symlinkTarget').setProperty('text','/system/bin/toybox')
            click('symlinkCreate');wait(lambda:(root/'system'/'native_link').exists());wait(lambda:not session._operation_count)
            from core.make_symlink import make_symlink_bytes
            assert (root/'system'/'native_link').read_bytes()==make_symlink_bytes('/system/bin/toybox')
            wait_ms(200)
            # Build a real ZIP using the original script generator and builder.
            flash=root/'system'/'flash_fixture';flash.mkdir();(flash/'boot.img').write_bytes(b'NATIVE_BOOT_FIXTURE'*256)
            session.dispatch('create_flashable',{'path':str(flash/'boot.img')});wait(lambda:not session.flashableBuilder.loading,45)
            builder=session.flashableBuilder
            wait(lambda:builder.rows.rowCount()==1);wait_ms(350)
            click('flashableShowScript')
            script=builder.script+'\n# custom native script\n';builder.editScript(script)
            builder.setDevice('native_fixture');assert builder.script==script and builder.dirty
            builder.regenerate();assert not builder.dirty and 'native_fixture' in builder.script
            out=root/'native_flashable.zip';builder.build(QUrl.fromLocalFile(str(out)))
            wait(lambda:out.exists() and not builder.busy);wait_ms(350)
            with zipfile.ZipFile(out) as built:
                assert built.read('boot.img')==(flash/'boot.img').read_bytes()
                assert built.read('META-INF/com/google/android/update-binary').decode()==builder.script
            click('launcherMessageAccept');wait(lambda:not workspace.engine._dialog_stack.stack)
            # Double click opens Monaco and applies the original layout switch.
            toolbar=visual(window,'workspaceToolbar');mode_samples=[]
            toolbar.modeAmountChanged.connect(lambda:mode_samples.append(toolbar.property('modeAmount')))
            assert toolbar.property('modeAmount')==0
            prop=str(root/'system'/'build.prop');index=session.tree.indexForPath(prop)
            point=tree.mapToScene(QPoint(90,round(index.row()*28-tree.property('contentY')+14))).toPoint()
            QTest.mouseDClick(window,Qt.LeftButton,Qt.NoModifier,point)
            wait(lambda:session.activeTab==prop)
            assert session.layoutMode==3 and session.rightOpen
            wait(lambda:toolbar.property('compact') and not toolbar.property('modeAnimating'))
            assert toolbar.property('modeAmount')==1 and any(0<value<1 for value in mode_samples),mode_samples
            # Real SplitView pointer gestures cross both toolbar thresholds.
            # Restore the original pane width before continuing the editor QA.
            for distance,compact in ((300,False),(-300,True)):
                divider=visual(window,'workspaceMainDivider')
                start=divider.mapToScene(divider.boundingRect().center()).toPoint()
                before=len(mode_samples)
                QTest.mouseMove(window,start);wait_ms(30)
                QTest.mousePress(window,Qt.LeftButton,Qt.NoModifier,start)
                for step in range(1,11):
                    QTest.mouseMove(window,start+QPoint(round(distance*step/10),0),15);wait_ms(10)
                QTest.mouseRelease(window,Qt.LeftButton,Qt.NoModifier,start+QPoint(distance,0))
                wait(lambda:toolbar.property('compact')==compact and not toolbar.property('modeAnimating'))
                assert any(0<value<1 for value in mode_samples[before:]),(distance,mode_samples[before:])
            wait(lambda:session.editor.bridge._ready)
            session.editor.bridge.on_content_changed(prop,'ro.fixture=modified\n')
            wait(lambda:session.activeDetails.get('unsaved'))
            session.closeTab(prop);wait_ms(400)
            click('launcherMessageCancel');wait_ms(200)
            assert session.hasTabs and session.editor.bridge.is_unsaved(prop)
            session.saveCurrent();wait(lambda:(root/'system'/'build.prop').read_text()=='ro.fixture=modified\n')
            session.closeTab(prop);wait(lambda:not session.hasTabs)
            assert session.layoutMode==2 and not session.rightOpen
            wait(lambda:not toolbar.property('compact') and not toolbar.property('modeAnimating'))
            assert toolbar.property('modeAmount')==0
            # Sessions are independent per ROM and survive hiding the terminal.
            session.toggleTerminal();wait(lambda:len(session._terminals)==1)
            terminal=session._terminals[0]['session'];wait(lambda:terminal.bridge.is_ready)
            pane=visual(window,'workspaceTerminal_'+session._terminals[0]['key'])
            def terminal_visible():
                if 'NATIVE_WORKSPACE_TERMINAL' in pane.property('inspection'):
                    return not pane.property('inspectionPending')
                QMetaObject.invokeMethod(pane,'inspect')
                return False
            wait(terminal_visible)
            first=session.activeTerminal
            root_selector=visual(window,'workspaceRootSelector')
            assert root_selector.width()==44 and root_selector.property('text')=='1'
            click('workspaceRootSelector');wait(lambda:visual(window,'choicePopup_workspaceRootSelector').property('opened'))
            click('choice_workspaceRootSelector_tree2');wait(lambda:len(session._terminals)==2)
            second=session.activeTerminal
            assert root_selector.width()==44 and root_selector.property('text')=='2'
            assert first!=second
            session.switchTerminalRoot('tree1');assert session.activeTerminal==first
            session.toggleTerminal();assert terminal.active and not terminal._manager.stopped
            session.toggleTerminal();assert session.activeTerminal==first
            session.setSpamEnter(True);assert all(row['session']._manager.spam for row in session._terminals)
            session.closeTerminal(first);wait_ms(100)
            assert terminal._manager.stopped and session._terminals[0]['session'].active
            assert session.terminalRoot=='tree2' and session.terminalOpen
            # Settings edit is a draft until Apply.
            click('workspaceToolbarExpand')
            toolbar_popup=visual(window,'workspaceToolbarPopup');wait(lambda:toolbar_popup.property('opened'))
            assert toolbar_popup.property('morphOnClose') and visual(window,'workspaceToolbarExpand').property('morphHidden')
            click('workspaceExpandedAction_settings')
            wait(lambda:visual(window,'workspaceSettings').property('opened'))
            assert not toolbar_popup.property('visible') and not visual(window,'workspaceToolbarExpand').property('morphHidden')
            before=session.projectSettings
            click('workspaceSettingsToggle_super');click('workspaceSettingsCancel');wait_ms(250)
            assert session.projectSettings==before
            session.dispatch('settings',{});wait_ms(400)
            click('workspaceSettingsToggle_super');click('workspaceSettingsApply');wait_ms(250)
            assert session.projectSettings['super']['norm']!=before['super']['norm']
            settings_popup=visual(window,'workspaceSettings')
            assert not settings_popup.property('visible') and not workspace.engine._dialog_stack.stack
            # Filename search selects the matching tree row without opening it.
            session.selectTree(0);session.dispatch('find',{});wait_ms(150)
            visual(window,'workspaceSearchInput').setProperty('text','inside')
            wait(lambda:any(row.get('path','').endswith('inside.txt') for row in session.search.rows.snapshot()))
            wait_ms(150)
            result_view=visual(window,'workspaceSearchResults')
            QTest.mouseClick(window,Qt.LeftButton,Qt.NoModifier,result_view.mapToScene(QPoint(70,14)).toPoint(),30)
            inside=str(root/'system'/'nested'/'inside.txt')
            wait(lambda:session._selection==[inside])
            assert not session.hasTabs
            # Unknown extensions use the original seven-viewer picker and remember choice.
            unknown=str(root/'system'/'fixture.weird');session.openPath(unknown);wait_ms(400)
            click('workspaceOpenWithOption_editor');click('workspaceOpenWithRemember');click('workspaceOpenWithConfirm')
            wait(lambda:session.activeTab==unknown)
            assert json.loads((root/'file_associations.json').read_text())['.weird']=='editor'
            session.closeTab(unknown);session.openPath(unknown);assert session.activeTab==unknown
            session.closeTab(unknown)
            # Run reuses one terminal for the file and adopts the first command title.
            script=str(root/'system'/'run_fixture.py');session.openPath(script)
            wait(lambda:session.activeRunnable)
            wait(lambda:not workspace.engine._dialog_stack.stack)
            previous_terminal=session.activeTerminal
            click('workspaceRunFile');wait(lambda:session.activeTerminal!=previous_terminal);key=session.activeTerminal
            row=next(row for row in session._terminals if row['key']==key)
            wait(lambda:row['session'].bridge.is_ready)
            wait(lambda:row['title']=='python')
            assert row['session']._manager.cwd==str(root/'system')
            count=len(session._terminals);click('workspaceRunFile')
            assert len(session._terminals)==count and session.activeTerminal==key
            session.closeTab(script)
            # The native history popup follows the original loading manager.
            assert len(session.historyLines)>=2
            click('workspaceHistoryButton');wait_ms(100)
            assert visual(window,'workspaceHistory').property('visible')
            click('workspaceHistoryClear');wait(lambda:not visual(window,'workspaceHistory').property('visible'))
            assert not session.historyLines and not visual(window,'workspaceHistory').property('visible')
            # Source and branch editors must never cross their write targets.
            session.selectTree(0)
            branch_file=str(partition/'etc'/'branch.txt')
            session.openPath(branch_file)
            session.editor.bridge.on_content_changed(branch_file,'Unsaved source\n')
            session.branches.show(str(partition));wait_ms(400)
            create_popup=visual(window,'workspaceBranchCreate')
            assert round(create_popup.property('width'))==440 and round(create_popup.property('height'))==318
            popup_frame(window,create_popup).save(str(repo/'build/qtquick_migration/workspace/neobranch_create.png'))
            click('workspaceBranchName');type_text('native_test')
            click('workspaceBranchColor_green');click('workspaceBranchCreateConfirm');wait_ms(350)
            assert session.editor.bridge.has_unsaved()
            assert not (root/'system'/'neobranch'/'product'/'branch_native_test').exists()
            click('launcherMessageAccept');wait_ms(150)
            session.saveCurrent();session.closeTab(branch_file);wait_ms(100)
            session.branches.show(str(partition));wait_ms(350)
            click('workspaceBranchName');type_text('native_test')
            click('workspaceBranchColor_green');click('workspaceBranchCreateConfirm')
            wait(lambda:session._operation_count==0 and session.historyLines)
            from main_ext.class_NeoBranchManager import NeoBranchManager
            manager=NeoBranchManager(str(root/'system'))
            assert manager.get_active_branch('product')['name']=='native_test'
            session.openPath(branch_file)
            document=session.activeDetails['documentPath']
            assert document!=branch_file
            session.editor.bridge.on_content_changed(document,'Branch only\n')
            session.branches.show(str(partition));wait_ms(350)
            popup_frame(window,visual(window,'workspaceBranchPicker')).save(str(repo/'build/qtquick_migration/workspace/neobranch_picker.png'))
            click('workspaceBranchSwitch_source');wait_ms(350)
            assert manager.get_active_branch('product')['name']=='native_test' and session.hasTabs
            click('launcherMessageAccept');wait_ms(150)
            session.saveCurrent()
            assert Path(branch_file).read_text()=='Unsaved source\n'
            assert Path(document).read_text()=='Branch only\n'
            session.branches.show(str(partition));wait_ms(350);click('workspaceBranchSwitch_source')
            wait(lambda:manager.get_active_branch('product') is None and not session.hasTabs)
            session.branches.show(str(partition));wait_ms(350);click('workspaceBranchDelete_native_test');wait_ms(350)
            click('launcherMessageAccept');wait(lambda:not manager.list_branches('product') and session._operation_count==0)
            assert not manager.list_branches('product') and Path(branch_file).read_text()=='Unsaved source\n'
            # Match Make FileSystem's toggles, fixed partition and success lifecycle.
            from unittest.mock import patch
            calls=[]
            def make_fixture(path,kind,mount):
                calls.append((path,kind,mount));return True,'fixture image'
            with patch('core.make_filesystem.make_filesystem',make_fixture):
                session.dispatch('make_filesystem',{'path':str(partition)});wait_ms(400)
                filesystem_popup=visual(window,'workspaceMakeFilesystem')
                assert round(filesystem_popup.property('width'))==420 and round(filesystem_popup.property('height'))==356
                assert not visual(window,'workspaceFilesystemPartition').isEnabled()
                click('workspaceFilesystem_EXT4');click('workspaceFilesystemMount')
                QTest.keyClick(window,Qt.Key_A,Qt.ControlModifier);type_text('/native_mount')
                click('workspaceFilesystemCreate')
                wait(lambda:calls and session._operation_count==0 and visual(window,'launcherMessage').property('presented'))
                assert calls==[(str(partition),'EXT4','/native_mount')],calls
                assert filesystem_popup.property('visible')
                click('launcherMessageAccept');wait(lambda:not filesystem_popup.property('visible'))
            # Binary viewer instances keep their state across document selection.
            binary=str(root/'system'/'fixture.bin');session.openPath(binary)
            wait_ms(300);hex_viewer=visual(window,'workspaceHexViewer')
            hex_document=hex_viewer.property('documentModel');wait(lambda:not hex_document.busy)
            hex_document.goPage(1);wait(lambda:not hex_document.busy)
            session.openPath(prop);wait_ms(150);session.selectTab(binary);wait_ms(150)
            assert hex_document.page==1 and session.activeKind=='hex'
            session.closeTab(prop);session.closeTab(binary);wait_ms(100)
            elf=str(root/'system'/'fixture.so');session.openPath(elf);wait_ms(200)
            elf_viewer=visual(window,'workspaceSoViewer');elf_document=elf_viewer.property('documentModel')
            wait(lambda:not elf_document.busy);assert elf_document.ready
            elf_viewer.setProperty('currentTab',3);session.openPath(prop);wait_ms(150)
            session.selectTab(elf);wait_ms(100)
            assert elf_viewer.property('currentTab')==3 and session.activeKind=='elf'
            elf_document.openSection(1);wait(lambda:not elf_document.busy)
            elf_dialog=elf_viewer.property('inspectionWindow');wait(lambda:elf_dialog.isVisible())
            wait(lambda:elf_dialog.property('active'))
            assert elf_dialog.modality()==Qt.WindowModal
            QTest.keyClick(elf_dialog,Qt.Key_Escape);wait(lambda:not elf_dialog.isVisible())
            session.closeTab(prop);session.closeTab(elf);wait_ms(150)
            image_path=str(root/'system'/'photo.png');session.openPath(image_path);wait_ms(250)
            image_viewer=visual(window,'workspaceImageViewer');image_document=image_viewer.property('documentModel')
            assert image_document.imageWidth==128
            click('imageEditorToggle');click('imageTool_erase')
            picture=visual(window,'imageCanvas');point=picture.mapToScene(picture.boundingRect().center()).toPoint()
            QTest.mouseClick(window,Qt.LeftButton,Qt.NoModifier,point);wait_ms(100)
            assert image_document.dirty and session.activeDetails['unsaved']
            session.saveCurrent();wait_ms(100);assert not image_document.dirty
            assert QImage(image_path).pixelColor(64,48).alpha()==0
            tool_scroll=visual(window,'imageToolsScroll');tool_scroll.setProperty('contentX',max(0,tool_scroll.property('contentWidth')-tool_scroll.width()));wait_ms(80)
            click('imageUndo');assert image_document.dirty
            session.closeTab(image_path);wait_ms(350);click('launcherMessageCancel');wait_ms(180)
            assert session.activeTab==image_path
            session.saveCurrent();wait_ms(80);session.closeTab(image_path);wait_ms(100)
            audio_path=str(root/'system'/'silence.wav');session.openPath(audio_path);wait_ms(300)
            media_viewer=visual(window,'workspaceMediaViewer');player=media_viewer.property('playerModel')
            wait(lambda:player.property('duration')>0);click('mediaPlay');wait(lambda:player.property('position')>0)
            session.dispatch('settings',{});wait(lambda:player.property('playbackState').value==2)
            click('workspaceSettingsCancel');wait(lambda:player.property('playbackState').value==1)
            click('mediaPlay');session.closeTab(audio_path);wait_ms(150)
            session.openPath(video_path);wait_ms(250)
            media_viewer=visual(window,'workspaceMediaViewer');player=media_viewer.property('playerModel')
            media_document=media_viewer.property('documentModel')
            wait(lambda:player.property('duration')>0);click('mediaPlay');wait(lambda:player.property('position')>0)
            click('mediaToImages');conversion=visual(window,'videoConvertDialog')
            wait(lambda:conversion.property('active'))
            assert conversion in workspace.engine._dialog_stack.dialogs
            assert conversion.property('backdropSource') is not None and window.property('nativeDialogActive')
            assert player.property('playbackState').value==2
            frame_folder=root/'frames with spaces'
            visual(window,'videoOutputFolder').setProperty('text',str(frame_folder))
            click('videoStartConversion');wait(lambda:not media_document.busy and media_document.progress.startswith('Done'))
            wait(lambda:not conversion.property('visible'))
            frames=list(frame_folder.glob('*.png'));assert len(frames)>=5
            assert QImage(str(frames[0])).size().width()==128
            session.closeTab(video_path);wait_ms(150)
            archive_path=str(root/'system'/'fixture.zip');session.openPath(archive_path);wait_ms(200)
            archive_viewer=visual(window,'workspaceArchiveViewer');archive_document=archive_viewer.property('documentModel')
            wait(lambda:archive_document.rows.rowCount()==2);archive_document.toggle(0);wait_ms(250)
            assert archive_document.rows.rowCount()==3
            archive_document.select(1,0);assert archive_document.selected==['folder/inside.txt']
            session.closeTab(archive_path);wait_ms(100)
            payload=str(root/'system'/'payload.bin');session.openPath(payload);wait_ms(150)
            payload_viewer=visual(window,'workspacePartitionViewer');payload_document=payload_viewer.property('documentModel')
            wait(lambda:not payload_document.busy);assert payload_document.total==4
            payload_list=visual(window,'payloadPartitions');point=payload_list.mapToScene(QPoint(90,16)).toPoint()
            QTest.mouseClick(window,Qt.LeftButton,Qt.NoModifier,point);wait_ms(80)
            with patch('core.partition_viewer._payload_service',return_value=None):
                click('payloadExtractSelected');wait(lambda:(root/'system'/'system.img').exists() and session._operation_count==0)
            assert (root/'system'/'system.img').read_bytes()==(b'NATIVE_PARTITION_system\n'*4096)[:4096]
            session.closeTab(payload);wait_ms(100)
            # Real EXT image, native directory navigation and deferred Monaco save.
            session.selectTree(0);session.tree.revealPath(str(mount_image))
            wait(lambda:str(mount_image) in session.tree._nodes and not session.tree.busy)
            session.dispatch('mount',{'path':str(mount_image)})
            wait(lambda:session.mounts.reference(str(mount_image)) is not None and not session.tree.busy)
            session.tree.activate(str(mount_image))
            mounted_etc='mount://'+str(mount_image)+'//etc'
            session.tree.activate(mounted_etc)
            mounted_file=mounted_etc+'/native.txt';session.openPath(mounted_file)
            wait(lambda:session.activeTab==mounted_file)
            session.editor.bridge.on_content_changed(mounted_file,'Changed inside the image\n')
            session.saveCurrent();wait(lambda:not session._operation_count and not session.editor.bridge.is_unsaved(mounted_file))
            from core import imgfs
            raw,_=imgfs.read_path(str(mount_image),'etc/native.txt');assert raw==b'Changed inside the image\n'
            session.closeTab(mounted_file);session.dispatch('unmount',{'path':str(mount_image)})
            wait(lambda:not session.tree.busy);assert session.tree.location==str(root/'system')
            # Official controls retain their gate; only this owned fixture uses
            # an inert guard, and the automation package contains PRINT/END only.
            from types import SimpleNamespace
            private=session.privateFeatures
            private.guard=SimpleNamespace(allowed=True,stop=lambda:None)
            private._allowed=True;private.changed.emit()
            package=root/'native.renxec'
            with zipfile.ZipFile(package,'w') as archive:
                archive.writestr('main.rensh','PRINT "NATIVE_AUTOMATION_FIXTURE"\nEND!\n')
            private.filePicker.load(str(root));wait(lambda:private.filePicker.rows.rowCount()>0)
            private.pickAutomation();wait_ms(350)
            private.filePicker.activate(str(package));wait(lambda:session.partitionPicker.rows.rowCount()>0);wait_ms(350)
            session.partitionPicker.choose('product');click('partitionPickerSelect')
            wait(lambda:not private.running and any('NATIVE_AUTOMATION_FIXTURE' in line for line in private.logs))
            wait_ms(300);click('automationLogOK');wait_ms(250)
            comparison=private.compare
            alternate=root/'system'/'nested'/'etc';alternate.mkdir(exist_ok=True)
            (alternate/'branch.txt').write_text('Alternate comparison content\n')
            comparison.showPicker();wait(lambda:comparison.sideA.rows.rowCount()>0 and comparison.sideB.rows.rowCount()>0)
            comparison.sideA.choose('product');comparison.sideB.load(0)
            wait(lambda:comparison.sideB.rows.rowCount()>0);comparison.sideB.choose('nested')
            wait_ms(350);click('compareStart');wait(lambda:comparison.visible and not comparison.busy)
            assert comparison._engine._report['rings']['existence']=='done'
            assert comparison.diffs.rowCount()==1 and comparison.web.active
            assert '1' in comparison.rings and '2' in comparison.rings and '3' in comparison.rings
            comparison.save('log','a',0);wait(lambda:comparison.status.startswith('Saved:'))
            reports=list((root/'system'/'compare').rglob('report.json'));assert reports
            assert json.loads(reports[0].read_text())['schema']=='rak.compare/1'
            compare_window=workspace._compare_window
            compare_window.requestActivate();wait_ms(150)
            diff_tab=visual(compare_window,'compareDiffTab')
            QTest.mouseClick(compare_window,Qt.LeftButton,Qt.NoModifier,diff_tab.mapToScene(diff_tab.boundingRect().center()).toPoint());wait_ms(450)
            assert visual(compare_window,'compareDiffWeb').isVisible()
            comparison.hide();window.requestActivate();wait_ms(200)
            # Two layouts, hidden tree state, resize and live effect changes.
            session.toggleLayout();session.toggleLayout();session.toggleLayout()
            window.resize(550,400);wait_ms(250);window.resize(1080,700);wait_ms(250)
            # Wait cancels the close request. Force Close uses the same dialog,
            # with cleanup limited to this fixture instead of real tool processes.
            import threading
            from ui.workspace_actions import operation
            gate=threading.Event();closed=[]
            operation(session,'Close lifecycle fixture',lambda:(gate.wait(10),'fixture')[1])
            session._confirm_leave(lambda:closed.append(True));wait_ms(350)
            click('launcherMessageCancel');wait_ms(150)
            assert not closed and session._operation_count==1
            gate.set();wait(lambda:session._operation_count==0)
            assert not closed
            gate.clear()
            operation(session,'Force Close fixture',lambda:(gate.wait(10),'fixture')[1])
            cleanup=[]
            def stop_fixture(*,returning):
                cleanup.append(returning);session._tasks.close();gate.set()
            with patch.object(session,'_stop_background_work',stop_fixture):
                session._confirm_leave(lambda:closed.append(True),returning=True);wait_ms(350)
                click('launcherMessageAccept');wait(lambda:closed)
            assert cleanup==[True]
            assert not diagnostics,diagnostics
            artifacts=repo/'build'/'qtquick_migration'/'workspace';artifacts.mkdir(parents=True,exist_ok=True)
            window.grabWindow().save(str(artifacts/'interaction.png'))
            report={'result':'passed','pointerSelection':True,'inlineRename':True,'inlineCreateCancel':True,'monacoSaveUnsavedCancel':True,
                    'layoutRestore':True,'independentTerminals':True,'toolbarEditorModeMorph':True,'toolbarRealSplitterModeMorph':True,'toolbarMorphIntoSettings':True,'settingsDraft':True,'searchSelectsTree':True,'openWithRemember':True,'runFileReusesSession':True,'taskHistoryClear':True,'neoBranchUnsavedGuardAndSourcePreservation':True,'makeFilesystemLifecycle':True,'hexViewerState':True,'elfViewerStateAndInspection':True,'realImageEditSaveAndUnsavedGuard':True,'audioPlaybackAndTeardown':True,'archiveHierarchy':True,'realPayloadExtraction':True,'waitAndForceClose':True,'qwidgetCount':len(QApplication.allWidgets()),
                    'fileConflictSkipReplace':True,'nativeSymlinkPresetsAndBytes':True,'realFlashableZipAndScript':True,'nativeOfficialAutomationAndCompare':True,'realMountedImageMonacoSave':True,'imageThumbnails':True,'dynamicViewerModalFocusAndBlur':True,'mediaPausesForModal':True,'realVideoPlaybackAndFrameExtraction':True,'svgBranchArrowPointerExpandCollapse':True,
                    'graphicsApi':window.rendererInterface().graphicsApi().name}
            (artifacts/'report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
            print(json.dumps(report),flush=True)
        except Exception:
            print('DIAGNOSTICS',diagnostics,flush=True)
            print('LAST_CLICK',last_click,flush=True)
            print('REVEAL',workspace._entry_transition_active,window.opacity(),window.isExposed(),flush=True)
            print('STATE',{'files':[path.name for path in (root/'system').iterdir()],'history':session.history,'selection':session._selection},flush=True)
            print('MODALS',[(dialog.objectName(),dialog.property('visible'),dialog.property('closing')) for dialog in workspace.engine._dialog_stack.dialogs],flush=True)
            if session._terminals:
                pane=visual(window,'workspaceTerminal_'+session._terminals[0]['key'])
                view=visual(window,'quickTerminalView')
                print('TERMINAL',pane,view,view.property('url') if view else None,flush=True)
                if pane:
                    QMetaObject.invokeMethod(pane,'inspect');wait_ms(200)
                    print('WEB',pane.property('inspection'),flush=True)
            artifacts=repo/'build'/'qtquick_migration'/'workspace';artifacts.mkdir(parents=True,exist_ok=True)
            window.grabWindow().save(str(artifacts/'failure.png'))
            raise
        finally:
            workspace.closed.connect(app.quit);workspace._shutdown_and_quit=app.quit
            workspace.dispose();QTimer.singleShot(5000,app.quit);app.exec()


if __name__=='__main__':main()
