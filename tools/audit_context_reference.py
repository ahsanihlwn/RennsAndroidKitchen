# SPDX-License-Identifier: MPL-2.0
"""Capture actual reference context popups against inert temporary tree items."""
import argparse
import gc
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import patch


def main():
    repo=Path(__file__).resolve().parents[1]
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--palette',default='dark_green');parser.add_argument('--language',default='id')
    args=parser.parse_args()
    source=repo.parent/'rennsandroidkitchen';sys.path.insert(0,str(source));sys.dont_write_bytecode=True;os.chdir(source)
    gc.disable()
    from PySide6.QtCore import Qt,QTimer,QPoint,QPointF,QEvent,QCoreApplication
    from PySide6.QtGui import QHoverEvent,QColor
    from PySide6.QtWidgets import QApplication,QWidget,QVBoxLayout,QTreeWidget,QTreeWidgetItem,QPushButton
    from PySide6.QtTest import QTest
    from core.fonts import apply_app_font
    from core import ui_lang
    from main_ext.class_ThemeUi import theme,_PALETTES
    from main_ext.FileTree_ext.RightClick import FileTreeContextMixin
    import main_ext.FileTree_ext.RightClick as original
    app=QApplication([]);app.setQuitOnLastWindowClosed(False);apply_app_font(app)
    theme.load();theme._name=args.palette;theme._apply_palette(_PALETTES[args.palette]);theme.set_blur_enabled(False)
    ui_lang.init();ui_lang._current_lang=args.language
    artifacts=repo/'build/qtquick_migration/context_reference'/args.palette/args.language;artifacts.mkdir(parents=True,exist_ok=True)
    class Tree(QTreeWidget,FileTreeContextMixin):
        ARCHIVE_EXT={'.zip','.rar','.7z'}
        def itemAt(self,*args):return self.fixture
        def is_mount_node(self,item):return False
        def is_mounted(self,path):return False
        def can_paste(self):return False
        def _dispatch_rc_action(self,*args):raise AssertionError('Capture must not execute a command')
    with tempfile.TemporaryDirectory(prefix='rak-context-reference-') as temporary:
        root=Path(temporary);(root/'notes.txt').write_text('fixture');(root/'other.txt').write_text('other');(root/'system.img').write_bytes(b'fixture')
        window=QWidget();window.resize(1080,700);window.move(60,60)
        window.setStyleSheet(f'background:{theme.BG3};color:{theme.FG};')
        layout=QVBoxLayout(window);tree=Tree();layout.addWidget(tree);tree.base=str(root);tree.display_mode='single_dir'
        tree.engine=SimpleNamespace(root_map_ctx={},root_map_fs={});window.show();window.activateWindow();QTest.qWait(100)
        results={};failures=[]
        for name,filename,batch,x,y in [('single_top','notes.txt',False,20,20),('single_bottom','notes.txt',False,1050,680),
                                      ('image_bottom','system.img',False,1050,680),('batch_bottom','notes.txt',True,1050,680)]:
            tree.clear();item=QTreeWidgetItem([filename]);item.setData(0,Qt.UserRole,str(root/filename));tree.addTopLevelItem(item);tree.fixture=item
            item.setSelected(True)
            if filename.endswith('.img'):item.setData(0,Qt.UserRole+1,'EXT4')
            if batch:
                second=QTreeWidgetItem(['other.txt']);second.setData(0,Qt.UserRole,str(root/'other.txt'));tree.addTopLevelItem(second);second.setSelected(True)
            requested=window.mapToGlobal(QPoint(x,y));point=tree.viewport().mapFromGlobal(requested)
            def capture():
                try:
                    popups=[widget for widget in QApplication.topLevelWidgets() if widget.isVisible() and widget.findChild(QWidget,'popup_card')]
                    assert len(popups)==1,len(popups)
                    popup=popups[0];card=popup.findChild(QWidget,'popup_card');buttons=card.findChildren(QPushButton)
                    origin=window.mapFromGlobal(popup.pos())
                    results[name]={'requested':[x,y],'rect':[origin.x(),origin.y(),popup.width(),popup.height()],
                        'corner':card._corner,'border':card._border.name(QColor.HexArgb),'tint':card._tint.name(QColor.HexArgb),
                        'rows':[{'text':button.text(),'rect':[button.x(),button.y(),button.width(),button.height()],
                            'textWidth':button.fontMetrics().horizontalAdvance(button.text()),'enabled':button.isEnabled(),
                            'font':button.font().toString(),'style':button.styleSheet()} for button in buttons]}
                    popup.grab().save(str(artifacts/(name+'.png')))
                    if filename.endswith('.img'):
                        convert=next(button for button in buttons if button.text()==ui_lang.t('rc_convert').rstrip(' ▸>›'))
                        QCoreApplication.sendEvent(convert,QHoverEvent(QEvent.HoverEnter,QPointF(3,3),QPointF(-1,-1)))
                        QTest.qWait(30)
                        subs=[widget for widget in QApplication.topLevelWidgets() if widget is not popup and widget.isVisible() and widget.findChild(QWidget,'popup_card')]
                        assert len(subs)==1,len(subs)
                        sub=subs[0];at=window.mapFromGlobal(sub.pos());sub.grab().save(str(artifacts/'submenu_bottom.png'))
                        results[name]['submenu']={'rect':[at.x(),at.y(),sub.width(),sub.height()],
                            'texts':[button.text() for button in sub.findChildren(QPushButton)]}
                    popup.close()
                except Exception as error:
                    failures.append(repr(error))
                    for popup in QApplication.topLevelWidgets():
                        if popup is not window:popup.close()
            QTimer.singleShot(150,capture)
            with patch.object(original,'is_available',lambda *args:True):tree.RightClick(point)
            QTest.qWait(30)
        window.close();(artifacts/'reference.json').write_text(json.dumps(results,indent=2,ensure_ascii=False),encoding='utf-8')
        assert not failures,failures
        print(json.dumps({key:{field:value for field,value in row.items() if field!='rows'} for key,row in results.items()}),flush=True)


if __name__=='__main__':main()
