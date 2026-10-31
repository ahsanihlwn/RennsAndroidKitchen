# SPDX-License-Identifier: MPL-2.0
"""Capture the original workspace in isolation, with inert services and data."""
import argparse
from contextlib import ExitStack
import gc
import json
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-root',type=Path,default=Path(__file__).resolve().parents[2]/'rennsandroidkitchen')
    parser.add_argument('--artifacts',type=Path,default=Path('build/qtquick_migration/original_workspace'))
    parser.add_argument('--size',default='1080x700')
    parser.add_argument('--hex',action='store_true',help='Also capture a real HexViewer in the original workspace')
    args=parser.parse_args()
    artifacts=args.artifacts.resolve();artifacts.mkdir(parents=True,exist_ok=True)
    source=args.source_root.resolve();sys.path.insert(0,str(source));sys.dont_write_bytecode=True;os.chdir(source)
    # Keep first imports and PySide wrapper collection serialized. The original
    # application's background startup is deliberately not imported here.
    gc.disable()
    from core.fonts import configure_text_rendering_env,apply_app_font
    configure_text_rendering_env()
    from PySide6.QtCore import Qt,QPoint,QCoreApplication,QEvent
    from PySide6.QtWidgets import QApplication,QWidget,QDialog,QLabel,QAbstractButton,QLineEdit,QTreeWidgetItem
    from PySide6.QtTest import QTest
    app=QApplication([]);app.setQuitOnLastWindowClosed(False);apply_app_font(app)
    from main_ext.class_ThemeUi import theme,_PALETTES
    theme.load()
    # Deterministic visual fixture, without saving over the user's preferences.
    theme._name='dark_green';theme._apply_palette(_PALETTES['dark_green'])
    from core import ui_lang
    ui_lang.init()
    from main import Main
    from rennsproject.app_ext.class_ProfileMiniProfider import ProfileMiniProvider
    from main_ext.class_NameBadgeDelegate import SIZE_LABEL_ROLE,IS_FOLDER_ROLE
    provider=ProfileMiniProvider({'username':'Fixture','employee_status':'Developer','avatar':'001'},None,None)
    with tempfile.TemporaryDirectory(prefix='rak-workspace-audit-') as temporary,ExitStack() as scope:
        for name in ('_build_after_init','_start_private_features'):
            scope.enter_context(patch.object(Main,name,lambda self:None))
        scope.enter_context(patch.object(Main,'ensure_project_settings',lambda self:self.default_project_settings()))
        scope.enter_context(patch.object(Main,'project_settings_path',lambda self:str(Path(temporary)/'settings.json')))
        workspace=Main(project='C:/__rak_visual_fixture__',p1='system',p2='vendor',profile_provider=provider)
        workspace.setAttribute(Qt.WA_DontShowOnScreen);workspace.resize(*map(int,args.size.split('x')))
        def populate(tree,base):
            tree.base=base;tree.set_display_mode('single_dir')
            for index in range(20):
                item=QTreeWidgetItem([f'partition_{index:02d}.img','u:object_r:system_file:s0','rw-r--r--'])
                item.setData(0,Qt.UserRole,base+f'/partition_{index:02d}.img')
                item.setData(0,IS_FOLDER_ROLE,False);item.setData(0,SIZE_LABEL_ROLE,'340.0 MB')
                item.setIcon(0,tree.icon_file);tree.addTopLevelItem(item)
        populate(workspace.tree,'C:/__rak_visual_fixture__/system')
        populate(workspace.tree2,'C:/__rak_visual_fixture__/vendor')
        populate(workspace.tree_mirror,'C:/__rak_visual_fixture__/system')
        def capture(name,window):
            window.ensurePolished();app.processEvents();window.grab().save(str(artifacts/(name+'.png')))
            rows=[]
            for widget in [window,*window.findChildren(QWidget)]:
                if not widget.isVisibleTo(window):continue
                pos=widget.mapTo(window,QPoint())
                row={'class':type(widget).__name__,'name':widget.objectName(),'rect':[pos.x(),pos.y(),widget.width(),widget.height()],'font':widget.font().toString(),'tooltip':widget.toolTip()}
                if isinstance(widget,(QAbstractButton,QLabel,QLineEdit)):row['text']=widget.text()
                if isinstance(widget,QAbstractButton):row['iconSize']=[widget.iconSize().width(),widget.iconSize().height()]
                if hasattr(widget,'display_mode'):
                    row['columns']=[widget.columnWidth(i) for i in range(3)]
                    row['scrollStep']=widget.verticalScrollBar().singleStep()
                if widget.layout():
                    lay=widget.layout();margins=lay.contentsMargins()
                    row['layout']={'margins':[margins.left(),margins.top(),margins.right(),margins.bottom()],'spacing':lay.spacing()}
                rows.append(row)
            (artifacts/(name+'.json')).write_text(json.dumps(rows,indent=2,ensure_ascii=False),encoding='utf-8')
            print(name,window.width(),window.height(),flush=True)
        workspace.show();QTest.qWait(150);capture('workspace',workspace)
        workspace.tree_mode=2;workspace.apply_tree_mode();QTest.qWait(100);capture('workspace_split',workspace)
        workspace.tree_mode=3;workspace.apply_tree_mode();QTest.qWait(100);capture('workspace_side_by_side',workspace)
        def modal_exec(dialog,name):
            dialog.setAttribute(Qt.WA_DontShowOnScreen);dialog.show();QTest.qWait(100)
            capture(name,dialog)
            dialog.hide();return QDialog.Rejected
        with patch.object(QDialog,'exec',lambda dialog:modal_exec(dialog,'project_settings')):workspace.open_project_settings()
        from main_ext.class_NeoBranchDialog import NeoBranchDialog,NeoBranchPickerDialog
        with patch.object(QDialog,'exec',lambda dialog:modal_exec(dialog,'neobranch_create')):
            NeoBranchDialog.ask(workspace,'system')
        branches=[{'name':'nfc_test','color':'blue'},{'name':'camera_fix','color':'green'}]
        with patch.object(QDialog,'exec',lambda dialog:modal_exec(dialog,'neobranch_picker')):
            NeoBranchPickerDialog.ask(workspace,'system',branches,'nfc_test')
        from main_ext.class_MakeFilesystemDialog import MakeFilesystemDialog
        filesystem=MakeFilesystemDialog(workspace,'C:/__rak_visual_fixture__/system/product',workspace)
        modal_exec(filesystem,'make_filesystem')
        QTest.qWait(300)
        if args.hex:
            binary=Path(temporary)/'fixture.bin';binary.write_bytes(b'Native binary fixture\x00'*1000)
            workspace.open_bin_tab(str(binary));QTest.qWait(500)
            capture('hex_workspace',workspace)
            from main_ext.class_HexViewer import HexViewer
            workspace.findChild(HexViewer).tabs.setCurrentIndex(1);QTest.qWait(100)
            capture('hex_workspace_dump',workspace)
        workspace.hide();workspace.abort_project_preparation();workspace.deleteLater()
        QCoreApplication.sendPostedEvents(None,QEvent.DeferredDelete);app.processEvents()


if __name__=='__main__':main()
