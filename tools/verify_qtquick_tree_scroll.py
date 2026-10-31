# SPDX-License-Identifier: MPL-2.0
"""Exercise original wheel input and the explicit, bounded Ctrl fast mode."""
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
    from PySide6.QtCore import Qt,QUrl,QTimer,QEventLoop,QCoreApplication,QEvent,QPoint,QPointF
    from PySide6.QtGui import QWheelEvent
    from PySide6.QtQml import QQmlApplicationEngine
    from PySide6.QtWidgets import QApplication
    from PySide6.QtTest import QTest
    from ui.presentation import LauncherModel
    from ui.workspace import WorkspaceSession
    from tools.quick_test_utils import visual
    app=QApplication([]);app.setQuitOnLastWindowClosed(False)
    def wait_ms(ms):
        loop=QEventLoop();QTimer.singleShot(ms,loop.quit);loop.exec()
    def wait(predicate):
        end=time.monotonic()+8
        while not predicate():
            assert time.monotonic()<end,'Tree scroll check timed out'
            wait_ms(10)
    with tempfile.TemporaryDirectory(prefix='rak-tree-scroll-') as temporary:
        root=Path(temporary);base=root/'system';base.mkdir()
        for number in range(500):(base/f'entry_{number:03d}.txt').write_text('fixture')
        prefs=LauncherModel(palette='dark_green',settings={'effects':'high','animations':False,'treeMode':'tree'})
        session=WorkspaceSession(str(root),'system',preferences=prefs,enable_private_services=False,settings_path=str(root/'settings.json'))
        engine=QQmlApplicationEngine();errors=[];engine.warnings.connect(lambda items:errors.extend(item.toString() for item in items))
        engine.addImageProvider('icons',IconProvider())
        engine.setInitialProperties({'workspace':session,'preferences':prefs});engine.load(QUrl.fromLocalFile(str(repo/'tools/qtquick_tree_fixture.qml')))
        assert engine.rootObjects(),errors
        window=engine.rootObjects()[0];window.requestActivate();wait(lambda:window.isActive())
        try:
            session.tree.load(str(base));wait(lambda:not session.tree.busy);wait_ms(100)
            tree=visual(window,'workspaceTree0');controller=visual(window,'treeWheelScroll0')
            point=tree.mapToScene(QPointF(200,200));global_point=window.mapToGlobal(point.toPoint())
            def wheel(angle=-120,pixels=0,horizontal=0,modifiers=Qt.NoModifier):
                event=QWheelEvent(point,QPointF(global_point),QPoint(0,pixels),QPoint(horizontal,angle),
                                  Qt.NoButton,modifiers,Qt.NoScrollPhase,False)
                event.setTimestamp(int(time.monotonic()*1000))
                QCoreApplication.sendEvent(window,event)
                return event
            def at(position):
                controller.reset();tree.cancelFlick();tree.setProperty('contentY',position);wait_ms(30)
            at(500)
            wheel();assert not controller.property('running')
            wait(lambda:not tree.property('moving'));wait_ms(50)
            single=tree.property('contentY')-500
            assert single>0,('Native wheel did not scroll',single)
            at(500)
            for _ in range(8):
                wheel();assert not controller.property('running');wait_ms(45)
            wait(lambda:not tree.property('moving'))
            normal_burst=tree.property('contentY')-500
            assert normal_burst>single*5,('Native wheel burst stopped scrolling',single,normal_burst)
            at(500)
            wheel(modifiers=Qt.ControlModifier)
            wait(lambda:not controller.property('running'))
            fast=tree.property('contentY')-500
            assert single*1.5<fast<single*4,(single,fast)
            # Repeated Ctrl notches keep the same step instead of building gain.
            wait_ms(260);before=tree.property('contentY');wheel(modifiers=Qt.ControlModifier)
            wait(lambda:not controller.property('running'))
            assert abs(tree.property('contentY')-before-fast)<1
            # A dense free-spin burst has limited travel and settles promptly.
            at(500);start=time.monotonic()
            for _ in range(40):
                wheel(modifiers=Qt.ControlModifier);wait_ms(8)
                # Allow one render frame at the sampling boundary, rather than
                # treating a 16 ms movement sampled after 8 ms as twice as fast.
                assert tree.property('contentY')-500<=2400*(time.monotonic()-start)+80
            released=tree.property('contentY');released_at=time.monotonic()
            wait(lambda:not controller.property('running'))
            tail=tree.property('contentY')-released
            assert tail<=225 and time.monotonic()-released_at<.4,tail
            wheel(modifiers=Qt.ControlModifier);wait_ms(25);before_reverse=tree.property('contentY')
            wheel(angle=120,modifiers=Qt.ControlModifier)
            wait_ms(35);assert tree.property('contentY')<before_reverse,'Reversing wheel retained forward momentum'
            # The first wheel event without Ctrl immediately returns control to Qt.
            wheel();assert not controller.property('running')
            wait(lambda:not tree.property('moving'))
            at(700);wheel(pixels=-17,modifiers=Qt.ControlModifier)
            assert abs(tree.property('contentY')-(700+17*2.25))<.1
            assert not controller.property('running')
            wheel(modifiers=Qt.ControlModifier);wait_ms(20);tree.setProperty('contentY',900);wait_ms(100)
            assert not controller.property('running') and tree.property('contentY')==900
            wheel(modifiers=Qt.ControlModifier);wait_ms(20)
            QTest.mouseClick(window,Qt.LeftButton,Qt.NoModifier,point.toPoint());wait_ms(80)
            assert not controller.property('running') and session._selection
            at(-tree.property('topMargin'));wheel(angle=120,modifiers=Qt.ControlModifier);wait_ms(100)
            assert tree.property('contentY')==-tree.property('topMargin')
            bottom=tree.property('contentHeight')-tree.height();at(bottom);wheel(modifiers=Qt.ControlModifier);wait_ms(100)
            assert abs(tree.property('contentY')-bottom)<.1
            assert not errors,errors
            report={'result':'passed','nativeOrdinaryNotchPixels':round(single,1),'nativeBurstPixels':round(normal_burst,1),
                    'ctrlNotchPixels':round(fast,1),'normalUsesNativeScroll':True,'ctrlOnlyFastMode':True,
                    'noAutomaticAcceleration':True,'boundedBurst':True,'directionReset':True,
                    'precisionCtrlBoost':True,'clickAndScrollbarTakeover':True,'bounds':True}
            out=repo/'build/qtquick_migration/tree_scroll';out.mkdir(parents=True,exist_ok=True)
            (out/'report.json').write_text(json.dumps(report,indent=2));print(json.dumps(report),flush=True)
        finally:
            session.stop();window.close();engine.deleteLater();session.deleteLater()
            QCoreApplication.sendPostedEvents(None,QEvent.DeferredDelete);app.processEvents()


if __name__=='__main__':main()
