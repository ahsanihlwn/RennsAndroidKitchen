# SPDX-License-Identifier: MPL-2.0
"""Verify the compact toolbar uses the global dropdown's two-way button morph."""
import json
from pathlib import Path
import sys
import time


def main():
    repo=Path(__file__).resolve().parents[1];sys.path.insert(0,str(repo))
    from ui.runtime import initialize_quick_runtime,IconProvider,DialogStack
    initialize_quick_runtime()
    from PySide6.QtCore import QUrl,QTimer,QEventLoop,Qt,QPoint,QPointF,QCoreApplication,QEvent
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtQml import QQmlApplicationEngine
    from PySide6.QtTest import QTest
    from core.fonts import apply_app_font
    from ui.presentation import LauncherModel
    from tools.quick_test_utils import visual
    app=QGuiApplication([]);apply_app_font(app)
    prefs=LauncherModel(palette='dark_green',settings={'effects':'high','animations':True})
    engine=QQmlApplicationEngine();engine.rootContext().setContextProperty('rakPreferences',prefs)
    engine.addImageProvider('icons',IconProvider());errors=[]
    engine.warnings.connect(lambda values:errors.extend(value.toString() for value in values))
    engine.setInitialProperties({'preferences':prefs})
    engine.load(QUrl.fromLocalFile(str(repo/'tools/qtquick_toolbar_fixture.qml')))
    assert engine.rootObjects(),errors
    window=engine.rootObjects()[0];stack=DialogStack(window)
    def wait_ms(ms):
        loop=QEventLoop();QTimer.singleShot(ms,loop.quit);loop.exec()
    def wait(predicate):
        deadline=time.monotonic()+5
        while not predicate():
            assert time.monotonic()<deadline,'Toolbar morph timed out'
            wait_ms(5)
    def click(item):
        if isinstance(item,str):item=visual(window,item)
        point=item.mapToScene(item.boundingRect().center()).toPoint()
        QTest.mouseMove(window,point);QTest.mouseClick(window,Qt.LeftButton,Qt.NoModifier,point)
    artifacts=repo/'build/qtquick_migration/toolbar';artifacts.mkdir(parents=True,exist_ok=True)
    try:
        window.requestActivate();wait(lambda:window.isActive());wait_ms(80)
        toolbar=visual(window,'fixtureToolbar');button=visual(window,'workspaceToolbarExpand')
        popup=visual(window,'workspaceToolbarPopup')
        assert toolbar.property('compact') and button.isVisible()
        surface=visual(window,'workspaceToolbarModeSurface')
        # Opening/closing the editor and dragging through either width threshold
        # animate one surface; glyphs never inherit a squeezed toolbar scale.
        assert toolbar.property('modeAmount')==1 and not toolbar.property('modeAnimating')
        window.setProperty('rightOpen',False);wait_ms(65)
        amount=toolbar.property('modeAmount')
        assert 0<amount<1 and toolbar.property('modeAnimating') and not toolbar.property('compact')
        assert 28<surface.width()<toolbar.property('inlineWidth') and surface.height()>28
        window.grabWindow().save(str(artifacts/'mode_expanding.png'))
        wait(lambda:not toolbar.property('modeAnimating'))
        assert toolbar.property('modeAmount')==0 and surface.width()==toolbar.property('inlineWidth')
        assert not button.isVisible()
        first=visual(window,'workspaceAction_back');last=visual(window,'workspaceAction_import')
        assert first.width()==28 and last.width()==28 and last.x()-first.x()==5*28
        window.setProperty('rightOpen',True);wait_ms(65)
        assert 0<toolbar.property('modeAmount')<1 and toolbar.property('modeAnimating')
        assert first.width()==28 and last.width()==28
        assert 0<last.x()-first.x()<5*28 and not button.isEnabled()
        window.grabWindow().save(str(artifacts/'mode_collapsing.png'))
        # Reverse twice mid-flight: the displayed geometry cannot reset to an
        # endpoint and the last splitter position determines the final mode.
        before=toolbar.property('modeAmount');toolbar.setWidth(600)
        assert abs(toolbar.property('modeAmount')-before)<.00001
        wait_ms(35);reversed_amount=toolbar.property('modeAmount');assert reversed_amount<before
        toolbar.setWidth(230);assert abs(toolbar.property('modeAmount')-reversed_amount)<.00001
        wait(lambda:not toolbar.property('modeAnimating'))
        assert toolbar.property('modeAmount')==1 and button.isEnabled() and not surface.isVisible()
        # Keep the reference's 56/100px hysteresis so a splitter near the
        # threshold does not restart the animation on every pointer movement.
        toolbar.setWidth(269);assert toolbar.property('compact') and not toolbar.property('modeAnimating')
        toolbar.setWidth(600);wait(lambda:not toolbar.property('modeAnimating'))
        toolbar.setWidth(269);assert not toolbar.property('compact') and not toolbar.property('modeAnimating')
        toolbar.setWidth(230);wait(lambda:not toolbar.property('modeAnimating'))
        click(button);wait_ms(60)
        assert popup.property('visible') and popup.property('opening')
        assert popup.property('sourceItem')==button and popup.property('morphOnClose')
        assert button.property('morphHidden') and button.property('pressScale')==1
        surface=popup.property('surfaceRect')
        assert button.width()<surface.width()<popup.property('width'),(surface,popup.property('progress'))
        window.grabWindow().save(str(artifacts/'opening.png'))
        wait(lambda:popup.property('opened'))
        assert abs(popup.property('surfaceRect').width()-popup.property('width'))<.01
        assert abs(popup.property('y')-button.mapToScene(QPointF(0,0)).y())<.01
        assert popup in stack.popups
        window.grabWindow().save(str(artifacts/'expanded.png'))
        click('workspaceToolbarCollapse');wait_ms(90)
        assert popup.property('visible') and popup.property('buttonReturning') and button.property('morphHidden')
        assert visual(window,'returningButtonCaption').property('item') is not None
        window.grabWindow().save(str(artifacts/'closing.png'))
        wait(lambda:not popup.property('visible'))
        assert not button.property('morphHidden') and button.opacity()==1 and button.property('pressScale')==1
        assert popup not in stack.popups
        # Dismissing outside consumes the press; it does not select an action.
        click(button);wait(lambda:popup.property('opened'))
        QTest.mouseClick(window,Qt.LeftButton,Qt.NoModifier,QPoint(700,300));wait(lambda:not popup.property('visible'))
        assert window.property('dispatchCount')==0
        # Selection waits for the return morph before opening a new dialog.
        click(button);wait(lambda:popup.property('opened'))
        click('workspaceExpandedAction_settings');wait_ms(65)
        assert popup.property('buttonReturning') and window.property('dispatchCount')==0
        wait(lambda:not popup.property('visible'))
        assert window.property('dispatchCount')==1 and window.property('lastAction')=='settings'
        click(button);wait(lambda:popup.property('opened'))
        QTest.keyClick(window,Qt.Key_Escape);wait(lambda:not popup.property('visible'))
        assert window.property('dispatchCount')==1 and not button.property('morphHidden')
        # Resizing out of compact mode restores the source without a ghost.
        click(button);wait(lambda:popup.property('opened'))
        toolbar.setWidth(600);wait(lambda:not popup.property('visible'))
        assert not toolbar.property('compact') and not button.property('morphHidden')
        toolbar.setWidth(230);wait(lambda:toolbar.property('compact'))
        prefs.refreshPreferences('light','en',{'effects':'low','animations':False})
        assert not toolbar.property('modeAnimating') and toolbar.property('modeAmount')==1
        toolbar.setWidth(600)
        assert not toolbar.property('modeAnimating') and toolbar.property('modeAmount')==0
        toolbar.setWidth(230)
        assert not toolbar.property('modeAnimating') and toolbar.property('modeAmount')==1
        click(button);wait(lambda:popup.property('opened'))
        assert not popup.property('effectsEnabled') and popup.property('progress')==1
        click('workspaceToolbarCollapse');wait(lambda:not popup.property('visible'))
        assert button.opacity()==1 and not button.property('morphHidden')
        assert not errors,errors
        print(json.dumps({'result':'passed','inlineCompactMorph':True,'editorOpenCloseModeMorph':True,'splitterReversalWithoutJump':True,'nativeIconSize':True,'overflowHysteresis':True,'globalDropdownMorph':True,'openingAndClosing':True,'sourceButtonRestored':True,'deferredSelection':True,'outsideEscapeResize':True,'lowLightAnimationsOff':True,'qmlWarnings':errors}),flush=True)
    finally:
        stack.close();window.close();engine.deleteLater()
        QCoreApplication.sendPostedEvents(None,QEvent.DeferredDelete);app.processEvents()


if __name__=='__main__':main()
