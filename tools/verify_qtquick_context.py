# SPDX-License-Identifier: MPL-2.0
"""Compare real Quick menus with captured reference metrics and pointer behavior."""
import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from unittest.mock import patch


def main():
    repo=Path(__file__).resolve().parents[1];os.chdir(repo);sys.path.insert(0,str(repo))
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--palette',default='dark_green');parser.add_argument('--language',default='id')
    args=parser.parse_args()
    from ui.runtime import initialize_quick_runtime,IconProvider
    initialize_quick_runtime()
    from PySide6.QtCore import QTimer,QEventLoop,QUrl,QCoreApplication,QEvent,Qt,QPoint
    from PySide6.QtWidgets import QApplication
    from PySide6.QtQml import QQmlApplicationEngine
    from PySide6.QtTest import QTest
    from core.fonts import apply_app_font
    from core import ui_lang
    from main_ext.class_ThemeUi import theme,_PALETTES
    from ui.presentation import LauncherModel
    from ui.workspace import WorkspaceSession
    from tools.quick_test_utils import visual,popup_frame
    app=QApplication([]);app.setQuitOnLastWindowClosed(False);apply_app_font(app,polish_widgets=False)
    theme.load();theme._name=args.palette;theme._apply_palette(_PALETTES[args.palette]);ui_lang.init();ui_lang._current_lang=args.language
    def wait_ms(ms):
        loop=QEventLoop();QTimer.singleShot(ms,loop.quit);loop.exec()
    def wait(predicate,seconds=10):
        deadline=time.monotonic()+seconds
        while not predicate():
            assert time.monotonic()<deadline,'Context menu timed out'
            wait_ms(5)
    reference=json.loads((repo/'build/qtquick_migration/context_reference'/args.palette/args.language/'reference.json').read_text(encoding='utf-8'))
    artifacts=repo/'build/qtquick_migration/context_quick'/args.palette/args.language;artifacts.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='rak-context-quick-') as temporary:
        root=Path(temporary);base=root/'system';base.mkdir()
        for name in ('notes.txt','other.txt','system.img'):(base/name).write_text('fixture')
        prefs=LauncherModel(palette=args.palette,language=args.language,settings={'effects':'low','animations':True,'treeMode':'single_dir'})
        session=WorkspaceSession(str(root),'system',preferences=prefs,enable_private_services=False,settings_path=str(root/'preferences.json'))
        engine=QQmlApplicationEngine();errors=[];engine.warnings.connect(lambda items:errors.extend(item.toString() for item in items))
        engine.addImageProvider('icons',IconProvider());engine.setInitialProperties({'workspace':session,'preferences':prefs})
        engine.load(QUrl.fromLocalFile(str(repo/'tools/qtquick_context_fixture.qml')));assert engine.rootObjects(),errors
        window=engine.rootObjects()[0];window.requestActivate();wait(lambda:window.isActive())
        session.tree.load(str(base));wait(lambda:not session.tree.busy)
        session.tree._nodes[str(base/'system.img')].setData(0,257,'EXT4')
        menu=visual(window,'workspaceContextMenu');results={}
        try:
            with patch('core.binary_handler.is_available',lambda *args:True):
                for name in ('single_top','image_bottom','single_bottom','batch_bottom'):
                    expected=reference[name];filename='system.img' if name=='image_bottom' else 'notes.txt'
                    path=str(base/filename);paths=[path,str(base/'other.txt')] if name=='batch_bottom' else [path]
                    session.selectPaths(0,paths);x,y=expected['requested']
                    session.dispatch('contextMenu',{'path':path,'paths':paths,'x':x,'y':y})
                    wait(lambda:menu.property('opened'));wait_ms(80)
                    rect=[round(menu.property(field)) for field in ('x','y','width','height')]
                    assert rect==expected['rect'],(name,rect,expected['rect'],menu.property('details'))
                    details=menu.property('details');details=details.toVariant() if hasattr(details,'toVariant') else details
                    rows=details['actions']
                    assert [row.get('label') for row in rows if not row.get('separator')]==[row['text'] for row in expected['rows']]
                    popup_frame(window,menu).save(str(artifacts/(name+'.png')));results[name]={'rect':rect}
                    if name=='image_bottom':
                        button=visual(window,'workspaceContextAction_convert_img');point=button.mapToScene(button.boundingRect().center()).toPoint()
                        QTest.mouseMove(window,point);submenu=visual(window,'workspaceContextSubmenu');wait(lambda:submenu.property('opened'))
                        subrect=[round(submenu.property(field)) for field in ('x','y','width','height')]
                        assert subrect==expected['submenu']['rect'],(subrect,expected['submenu'])
                        popup_frame(window,submenu).save(str(artifacts/'submenu_bottom.png'));results[name]['submenu']=subrect
                        # Moving onto another parent row dismisses the hovered submenu.
                        rename=visual(window,'workspaceContextAction_rename');QTest.mouseMove(window,rename.mapToScene(rename.boundingRect().center()).toPoint())
                        wait(lambda:not submenu.property('visible'))
                    if name=='single_top':
                        disabled=visual(window,'workspaceContextAction_paste').property('disabledLabelColor')
                        assert disabled.alpha()==128,(disabled,disabled.alpha())
                    QTest.keyClick(window,Qt.Key_Escape);wait(lambda:not menu.property('visible'))
                # Pick a real submenu entry at both edges, including the source's
                # clamped overlap at the bottom/right. The tool itself is inert.
                for x,y in ((20,20),(1070,690)):
                    path=str(base/'system.img');session.selectPaths(0,[path])
                    session.dispatch('contextMenu',{'path':path,'paths':[path],'x':x,'y':y})
                    wait(lambda:menu.property('opened'));wait_ms(80)
                    QTest.mouseMove(window,QPoint(500,200));wait_ms(25)
                    button=visual(window,'workspaceContextAction_convert_img')
                    QTest.mouseMove(window,button.mapToScene(button.boundingRect().center()).toPoint())
                    submenu=visual(window,'workspaceContextSubmenu');wait(lambda:submenu.property('opened'))
                    rows=submenu.property('rows');rows=rows.toVariant() if hasattr(rows,'toVariant') else rows
                    action=next(row['key'] for row in rows if not row.get('separator'))
                    target=visual(window,'workspaceContextSubAction_'+action)
                    point=target.mapToScene(target.boundingRect().center()).toPoint();picked=[]
                    with patch('ui.workspace_tools.dispatch_tool',side_effect=lambda owner,key,target,data:picked.append((key,target))):
                        QTest.mouseMove(window,point);QTest.mousePress(window,Qt.LeftButton,Qt.NoModifier,point)
                        assert target.property('pressed')
                        QTest.mouseRelease(window,Qt.LeftButton,Qt.NoModifier,point)
                        wait(lambda:not menu.property('visible') and not submenu.property('visible'))
                    assert picked==[(action,path)],picked
                    assert not session.contextMenuActive
                # Opening before the next polish must already use the new menu height.
                session.selectPaths(0,[str(base/'notes.txt')]);session.dispatch('contextMenu',{'x':1070,'y':690})
                assert round(menu.property('y'))==reference['single_bottom']['rect'][1]
                wait_ms(20);window.setWidth(900);wait(lambda:not menu.property('visible'));window.setWidth(1080)
                session.dispatch('contextMenu',{'x':20,'y':20});wait(lambda:menu.property('opened'))
                QTest.mouseClick(window,Qt.LeftButton,Qt.NoModifier,QPoint(700,500));wait(lambda:not menu.property('visible'))
                # Transparent effects retain the same disabled alpha and neutral border.
                for effect in ('medium','high'):
                    prefs.chooseSetting('effects',effect);session.dispatch('contextMenu',{'x':20,'y':20});wait_ms(100)
                    assert menu.property('visible')
                    disabled=visual(window,'workspaceContextAction_paste').property('disabledLabelColor');assert disabled.alpha()==128
                    glass=visual(window,'contextMenuGlass');assert glass.property('live') and glass.property('cornerRadius')==10
                    popup_frame(window,menu).save(str(artifacts/('single_'+effect+'.png')))
                    QTest.keyClick(window,Qt.Key_Escape);wait(lambda:not menu.property('visible'))
            assert not QApplication.allWidgets();assert not errors,errors
            report={'result':'passed','rectangles':results,'qwidgetCount':0,'noStaleHeight':True,'submenuHover':True,'submenuPickAtBothEdges':True,'outsideAndResizeClose':True,'disabledAlpha':128}
            (artifacts/'report.json').write_text(json.dumps(report,indent=2),encoding='utf-8');print(json.dumps(report),flush=True)
        finally:
            session.stop();window.close();engine.deleteLater();session.deleteLater()
            QCoreApplication.sendPostedEvents(None,QEvent.DeferredDelete);app.processEvents()


if __name__=='__main__':main()
