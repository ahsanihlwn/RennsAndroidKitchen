# SPDX-License-Identifier: MPL-2.0
"""Check Create's pressed frame, closing morph and native-button handoff."""
import json
import os
from pathlib import Path
import sys
import time


def main():
    repo=Path(__file__).resolve().parents[1];sys.path.insert(0,str(repo));os.chdir(repo)
    os.environ.setdefault('QSG_RENDER_LOOP','basic')
    from ui.runtime import initialize_quick_runtime,create_launcher
    initialize_quick_runtime()
    from PySide6.QtCore import Qt,QPointF,QMetaObject,QCoreApplication,QEvent
    from PySide6.QtWidgets import QApplication
    from PySide6.QtTest import QTest
    from core.fonts import apply_app_font
    from ui.presentation import LauncherModel
    from tools.quick_test_utils import visual
    app=QApplication([]);app.setQuitOnLastWindowClosed(False);apply_app_font(app,polish_widgets=False)
    projects=[{'path':f'C:/fixture/project_{i}','project_name':f'Fixture project {i}','port1':'system','parentPath':'C:/fixture'} for i in range(24)]
    model=LauncherModel(palette='dark_orange',projects=projects,settings={'effects':'medium','animations':True})
    commands=[];closed_states=[]
    def handle(name,data):
        commands.append(name)
        if name=='newProject':model.presentationRequested.emit('newProject',{})
        elif name=='createProject':model.presentationRequested.emit('created',{})
        elif name=='createPopupClosed':closed_states.append((button.property('morphHidden'),button.property('pressScale')))
    model.command.connect(handle)
    engine,window=create_launcher(model);errors=[]
    engine.warnings.connect(lambda values:errors.extend(item.toString() for item in values))
    out=repo/'build/qtquick_migration/create_morph';out.mkdir(parents=True,exist_ok=True)
    def wait(predicate):
        deadline=time.monotonic()+5
        while not predicate():
            assert time.monotonic()<deadline,('Create morph timed out',errors,engine._diagnostics)
            QTest.qWait(5)
    def center(item):return item.mapToScene(QPointF(item.width()/2,item.height()/2)).toPoint()
    def crop():
        bounds=button.parentItem().mapRectToScene(button.boundingRect().translated(button.position())).toAlignedRect()
        frame=window.grabWindow();ratio=frame.devicePixelRatio()
        return frame.copy(round(bounds.x()*ratio),round(bounds.y()*ratio),round(bounds.width()*ratio),round(bounds.height()*ratio))
    def difference(a,b):
        assert a.size()==b.size()
        return sum(abs(x-y) for py in range(a.height()) for px in range(a.width())
                   for x,y in zip(a.pixelColor(px,py).getRgb()[:3],b.pixelColor(px,py).getRgb()[:3]))/(a.width()*a.height()*3)
    results=[]
    try:
        QTest.qWait(180);window.requestActivate();wait(window.isActive)
        button=visual(window,'createButton');popup=visual(window,'createProjectPopup')
        for level,method,hold in [('medium','escape',80),('high','close',170),('low','back',0),('high','outside',50),('medium','escape',0),('high','confirm',35),('high','escape_hover',50)]:
            model.chooseSetting('effects',level);QTest.qWait(260)
            point=center(button);QTest.mouseMove(window,point);QTest.qWait(25)
            QTest.mousePress(window,Qt.LeftButton,Qt.NoModifier,point);QTest.qWait(hold)
            launch=button.mapRectToScene(button.boundingRect());scale=button.property('pressScale')
            QTest.mouseRelease(window,Qt.LeftButton,Qt.NoModifier,point)
            assert popup.property('visible') and button.property('morphHidden')
            assert button.property('pressScale')==1,('Press animation kept running underneath the morph',button.property('pressScale'))
            source=popup.property('sourceRect')
            assert abs(source.width()-launch.width())<.01
            assert abs(popup.property('launchScale')-scale)<.001
            wait(lambda:popup.property('opened'))
            if method=='close':window.grabWindow().save(str(out/'high_open.png'))
            if method=='confirm':
                visual(window,'popupProjectName').setProperty('text','Fixture project')
                visual(window,'popupPort1').setProperty('text','system')
            QTest.mouseMove(window,QPointF(8,100).toPoint());QTest.qWait(20)
            if method.startswith('escape'):QTest.keyClick(window,Qt.Key_Escape)
            elif method=='outside':QTest.mouseClick(window,Qt.LeftButton,Qt.NoModifier,QPointF(8,100).toPoint())
            else:QTest.mouseClick(window,Qt.LeftButton,Qt.NoModifier,center(visual(window,{'close':'popupCloseProject','back':'popupBackProject','confirm':'popupConfirmProject'}[method])))
            if method=='escape_hover':
                QTest.qWait(120);QTest.mouseMove(window,center(button));QTest.qWait(30)
                QTest.mouseMove(window,QPointF(8,100).toPoint());QTest.qWait(25)
                QTest.mouseMove(window,center(button));QTest.qWait(25)
            wait(lambda:not popup.property('opening') and popup.property('progress')>=.88)
            assert popup.property('visible') and button.property('morphHidden')
            surface=popup.property('surfaceRect');target=popup.property('sourceRect')
            assert max(abs(a-b) for a,b in zip(surface.getRect(),target.getRect()))<1e-7,('Button did not settle before handoff',surface,target)
            late=crop();late.save(str(out/(level+'_'+method+'_handoff.png')))
            if method=='close':window.grabWindow().save(str(out/'high_closing_handoff.png'))
            wait(lambda:not popup.property('visible'));QTest.qWait(30)
            assert not button.property('morphHidden') and button.property('morphHoverState')==-1,(level,method,button.property('morphHidden'),button.property('morphHoverState'))
            assert button.property('pressScale')==1 and not button.property('pressFeedback')
            restored=crop();error=difference(late,restored)
            restored.save(str(out/(level+'_'+method+'_restored.png')))
            assert error<1,('Closing drawing did not match the restored button',level,method,error)
            QTest.qWait(200);assert button.property('pressScale')==1
            results.append({'effects':level,'close':method,'pressMs':hold,'handoffError':round(error,4)})
        # Cancel an unfinished opening, then immediately use the same button.
        for _ in range(2):
            QTest.mouseClick(window,Qt.LeftButton,Qt.NoModifier,center(button));QTest.qWait(50)
            assert popup.property('visible') and popup.property('progress')<1
            QTest.keyClick(window,Qt.Key_Escape);wait(lambda:not popup.property('visible'))
            assert button.property('pressScale')==1 and not button.property('morphHidden')
        model.chooseSetting('animations',False)
        QTest.mouseClick(window,Qt.LeftButton,Qt.NoModifier,center(button));QTest.qWait(20)
        assert popup.property('opened')
        QMetaObject.invokeMethod(popup,'close');QTest.qWait(20)
        assert not popup.property('visible') and not button.property('morphHidden') and button.property('pressScale')==1
        assert len(closed_states)==len(results)+3 and all(not hidden and scale==1 for hidden,scale in closed_states),('Completion reached the backend before the button was restored',closed_states)
        assert commands.count('newProject')==len(results)+3 and commands.count('createProject')==1,commands
        assert not errors and not engine._diagnostics,(errors,engine._diagnostics)
        report={'result':'passed','palette':model.paletteName,'cycles':results,'restoredBeforeCompletion':True,'earlyCloseAndReopen':True,'animationsOff':True}
        (out/'report.json').write_text(json.dumps(report,indent=2));print(json.dumps(report),flush=True)
    finally:
        engine._appearance_captures.close();engine._dialog_stack.close();window.close();engine.deleteLater()
        QCoreApplication.sendPostedEvents(None,QEvent.DeferredDelete);app.processEvents()


if __name__=='__main__':main()
