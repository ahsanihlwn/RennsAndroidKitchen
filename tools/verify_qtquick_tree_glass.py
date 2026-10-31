# SPDX-License-Identifier: MPL-2.0
"""Check live scrolling behind the tree header and solid Medium/Low fallbacks."""
import json
import os
from pathlib import Path
import sys
import tempfile
import time


def main():
    repo=Path(__file__).resolve().parents[1];sys.path.insert(0,str(repo));os.chdir(repo)
    from ui.runtime import initialize_quick_runtime,IconProvider
    initialize_quick_runtime()
    from PySide6.QtCore import Qt,QUrl,QTimer,QEventLoop,QCoreApplication,QEvent,QPointF
    from PySide6.QtGui import QColor
    from PySide6.QtQml import QQmlApplicationEngine
    from PySide6.QtWidgets import QApplication
    from PySide6.QtTest import QTest
    from core.fonts import apply_app_font
    from ui.presentation import LauncherModel
    from ui.workspace import WorkspaceSession
    from tools.quick_test_utils import visual
    app=QApplication([]);app.setQuitOnLastWindowClosed(False);apply_app_font(app,polish_widgets=False)
    def wait_ms(ms):
        loop=QEventLoop();QTimer.singleShot(ms,loop.quit);loop.exec()
    def wait(predicate):
        end=time.monotonic()+8
        while not predicate():
            assert time.monotonic()<end,'Tree glass check timed out'
            wait_ms(10)
    out=repo/'build/qtquick_migration/tree_glass';out.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='rak-tree-glass-') as temporary:
        root=Path(temporary);base=root/'system';base.mkdir()
        for number in range(90):(base/f'entry_{number:03d}_scrolling_behind_the_header.txt').write_text('fixture')
        prefs=LauncherModel(palette='dark_green',settings={'effects':'high','animations':False,'treeMode':'tree'})
        session=WorkspaceSession(str(root),'system',preferences=prefs,enable_private_services=False,settings_path=str(root/'settings.json'))
        engine=QQmlApplicationEngine();errors=[];engine.warnings.connect(lambda items:errors.extend(item.toString() for item in items))
        engine.addImageProvider('icons',IconProvider())
        engine.setInitialProperties({'workspace':session,'preferences':prefs});engine.load(QUrl.fromLocalFile(str(repo/'tools/qtquick_tree_fixture.qml')))
        assert engine.rootObjects(),errors
        window=engine.rootObjects()[0];window.requestActivate();wait(lambda:window.isActive())
        try:
            session.tree.setDisplayMode('tree');session.tree.load(str(base));wait(lambda:not session.tree.busy);wait_ms(100)
            tree=visual(window,'workspaceTree0');columns=visual(window,'workspaceTreeColumns0');glass=visual(window,'workspaceTreeHeaderGlass0')
            assert glass.property('live') and glass.property('rendering') and glass.property('sampleInSourceCoordinates')
            assert glass.property('refracting'),'High header did not enable liquid refraction'
            assert tree.y()==columns.y() and tree.property('contentY')==-columns.height()
            assert glass.property('backgroundColor')==QColor(prefs.colors['BG3'])
            assert glass.property('tintColor').red()==QColor(prefs.colors['BG3']).red()
            bar=visual(window,'treeVerticalScrollBar0');assert bar.y()==columns.height()
            def header_frame():
                origin=columns.mapToScene(QPointF(0,0)).toPoint()
                return window.grabWindow().copy(origin.x(),origin.y(),round(columns.width()),round(columns.height()))
            blank=header_frame();tree.setProperty('contentY',140);wait_ms(120)
            scrolling=header_frame();assert scrolling!=blank,'Scrolled rows never appeared behind the header'
            window.grabWindow().save(str(out/'high_scrolled.png'))
            def rows_behind_header():
                labels=[];pending=[tree]
                while pending:
                    item=pending.pop();pending.extend(item.childItems())
                    if item.objectName()=='treeEntryName':labels.append(item)
                origin=columns.mapToScene(QPointF(0,0)).y()
                return [item for item in labels if origin<=item.mapToScene(QPointF(0,0)).y()<origin+columns.height()]
            behind=rows_behind_header()
            assert behind,'No live row was rendered under the header'
            for item in behind:item.setProperty('color',QColor('#ff2020'))
            wait_ms(100);updated=header_frame();assert updated!=scrolling,'Header used a frozen row capture'
            window.grabWindow().save(str(out/'high_live_update.png'))
            selection=list(session._selection)
            QTest.mouseClick(window,Qt.LeftButton,Qt.NoModifier,columns.mapToScene(QPointF(20,20)).toPoint())
            assert session._selection==selection,'Clicking the header selected a hidden row'
            QTest.mouseMove(window,columns.mapToScene(QPointF(-5,-5)).toPoint());wait_ms(30)
            for effect in ('medium','low'):
                prefs.chooseSetting('effects',effect);wait_ms(50)
                assert not glass.property('rendering') and not glass.property('refracting')
                flat=header_frame();assert flat.pixelColor(150,20)==QColor(prefs.colors['BG3'])
                for item in rows_behind_header():item.setProperty('color',QColor('blue' if effect=='medium' else 'yellow'))
                wait_ms(50);assert header_frame()==flat
                window.grabWindow().save(str(out/(effect+'.png')))
            prefs.chooseSetting('effects','high');wait_ms(100)
            assert glass.property('rendering') and glass.property('refracting') and header_frame()!=flat
            window.setWidth(650);wait_ms(80);assert abs(glass.width()-tree.width())<.01
            # Revealed paths must land below the header, not behind its glass.
            first=base/'entry_000_scrolling_behind_the_header.txt'
            session.tree.pathRevealed.emit(str(first));wait_ms(350)
            row=tree.property('contentY')
            assert 28-row>=columns.height(),('Revealed file was hidden by the header',row)
            for palette in ('light','dark_green'):
                prefs.choosePalette(palette);wait_ms(80)
                background=QColor(prefs.colors['BG3'])
                assert glass.property('backgroundColor')==background and glass.property('flatColor')==background
                assert glass.property('tintColor').getRgb()[:3]==background.getRgb()[:3]
                window.grabWindow().save(str(out/(palette+'.png')))
            assert not errors,errors
            report={'result':'passed','liveHeaderOnHigh':True,'liquidRefractionOnlyOnHigh':True,'sourceUpdatesWithoutCapture':True,'mediumAndLowSolid':True,'tintMatchesPanelAcrossThemes':True,'headerBlocksHiddenRowClicks':True,'resizeAndReveal':True}
            (out/'report.json').write_text(json.dumps(report,indent=2));print(json.dumps(report),flush=True)
        finally:
            session.stop();window.close();engine.deleteLater();session.deleteLater()
            QCoreApplication.sendPostedEvents(None,QEvent.DeferredDelete);app.processEvents()


if __name__=='__main__':main()
