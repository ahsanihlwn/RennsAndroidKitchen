# SPDX-License-Identifier: MPL-2.0
"""Render task transitions and verify history content follows the morph clip."""
import json
import os
from pathlib import Path
import sys
import tempfile
import time


def main():
    repo=Path(__file__).resolve().parents[1];os.chdir(repo);sys.path.insert(0,str(repo))
    from ui.runtime import initialize_quick_runtime,IconProvider
    initialize_quick_runtime()
    from PySide6.QtCore import Qt,QUrl,QTimer,QEventLoop,QPointF,QCoreApplication,QEvent
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
    def wait(predicate,seconds=5):
        end=time.monotonic()+seconds
        while not predicate():
            assert time.monotonic()<end,'Status animation timed out'
            wait_ms(10)
    artifacts=repo/'build/qtquick_migration/status';artifacts.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='rak-status-motion-') as temporary:
        root=Path(temporary);(root/'system').mkdir()
        prefs=LauncherModel(palette='dark_orange',language='id',settings={'effects':'high','animations':True})
        session=WorkspaceSession(str(root),'system',preferences=prefs,enable_private_services=False,settings_path=str(root/'settings.json'))
        engine=QQmlApplicationEngine();errors=[];engine.warnings.connect(lambda items:errors.extend(item.toString() for item in items))
        engine.rootContext().setContextProperty('rakPreferences',prefs);engine.addImageProvider('icons',IconProvider())
        engine.setInitialProperties({'workspace':session,'preferences':prefs});engine.load(QUrl.fromLocalFile(str(repo/'tools/qtquick_status_fixture.qml')))
        assert engine.rootObjects(),errors
        window=engine.rootObjects()[0];window.requestActivate();wait(lambda:window.isActive())
        try:
            bar=visual(window,'workspaceStatusBar');slot=visual(window,'workspaceHistorySlot');dots=visual(window,'workspaceHistoryButton')
            status=visual(window,'workspaceStatusContent');spinner=visual(window,'workspaceStatusSpinner')
            assert status.opacity()==0 and slot.opacity()==0
            manager=session._tasks.loading
            batch=manager.start_batch('moving',2,label='Move 2 files');manager.mark_group_active(batch,'system/etc/first.txt')
            wait_ms(55)
            assert 0<status.opacity()<1 and .86<status.scale()<1
            assert 0<slot.opacity()<1 and .72<slot.scale()<1
            window.grabWindow().save(str(artifacts/'loading_appearing.png'))
            wait_ms(300);phase=spinner.property('phase');wait_ms(80)
            assert spinner.property('phase')!=phase and spinner.property('spinning')
            manager.report_done(batch,True,'system/etc/first.txt')
            wait_ms(300)
            QTest.mouseClick(window,Qt.LeftButton,Qt.NoModifier,dots.mapToScene(dots.boundingRect().center()).toPoint())
            popup=visual(window,'workspaceHistory');wait(lambda:popup.property('opened'))
            content=visual(window,'workspaceHistoryContent');form=visual(window,'popupForm');clip=visual(window,'popupFormClip')
            assert content.parentItem()==form and form.parentItem()==clip and clip.property('clip')
            assert dots.property('morphHidden') and popup.property('sourceItem')==dots
            assert dots.property('returnCaption') is not None
            window.grabWindow().save(str(artifacts/'activity_log_open.png'))
            QTest.keyClick(window,Qt.Key_Escape);wait_ms(80)
            assert popup.property('visible') and not popup.property('opening')
            assert form.opacity()<.05,'History text remained opaque outside the shrinking card'
            rect=popup.property('surfaceRect')
            assert abs(clip.x()-rect.x())<.1 and abs(clip.width()-rect.width())<.1
            window.grabWindow().save(str(artifacts/'activity_log_closing.png'))
            wait(lambda:not popup.property('visible'));assert not dots.property('morphHidden')
            expanded_x=slot.x();assert expanded_x>10 and status.opacity()==1
            finished_at=time.monotonic()
            manager.report_done(batch,True,'system/etc/second.txt');wait_ms(300)
            result_icon=visual(window,'workspaceStatusResult')
            from urllib.parse import parse_qs,urlsplit
            assert parse_qs(urlsplit(result_icon.property('source').toString()).query)['color']==[bar.property('textColor').name()]
            assert status.opacity()==1 and status.scale()==1 and slot.x()>10
            finished_x=slot.x()
            assert bar.property('currentState')=='done' and not spinner.property('spinning')
            window.grabWindow().save(str(artifacts/'loading_finished.png'))
            wait_ms(3300)
            assert status.opacity()==1 and session.statusState=='done','Finished result closed before its hold elapsed'
            wait(lambda:session.statusState=='idle')
            assert time.monotonic()-finished_at>=4,'Finished result held for less than 4 seconds'
            wait_ms(70)
            assert 0<status.opacity()<1 and .86<status.scale()<1
            assert 10<slot.x()<finished_x and slot.opacity()==1
            window.grabWindow().save(str(artifacts/'loading_collapsing.png'))
            wait_ms(350);assert status.opacity()==0 and abs(slot.x()-10)<.05
            assert slot.opacity()==1 and slot.scale()==1 and not spinner.property('spinning')
            manager.set_clean()
            # A returning popup follows the moving status button during idle.
            batch=manager.start_batch('moving',1,label='Move another file');manager.mark_group_active(batch,'another.txt');wait_ms(300)
            QTest.mouseClick(window,Qt.LeftButton,Qt.NoModifier,dots.mapToScene(dots.boundingRect().center()).toPoint());wait(lambda:popup.property('opened'))
            failed_at=time.monotonic()
            manager.report_done(batch,False,'another.txt',detail='Target directory is read-only');wait_ms(300)
            assert status.opacity()==1 and bar.property('currentState')=='error'
            window.grabWindow().save(str(artifacts/'loading_failed.png'))
            wait_ms(3300)
            assert status.opacity()==1 and session.statusState=='error','Failed result closed before its hold elapsed'
            wait(lambda:session.statusState=='idle')
            assert time.monotonic()-failed_at>=4,'Failed result held for less than 4 seconds'
            QTest.keyClick(window,Qt.Key_Escape);wait_ms(100)
            anchor=popup.property('anchorInParent');point=dots.mapToScene(QPointF(0,0))
            assert abs(anchor.x()-point.x())<15,'Return morph kept its old source position'
            wait(lambda:not popup.property('visible'))
            prefs.chooseSetting('animations',False)
            session.dispatch('clearHistory',{});assert slot.opacity()==0 and status.opacity()==0
            batch=manager.start_batch('moving',1,label='Instant status');assert status.opacity()==1 and slot.opacity()==1
            manager.report_done(batch,True,'instant.txt');assert status.opacity()==1
            manager.set_clean();assert status.opacity()==0 and abs(slot.x()-10)<.05
            manager.set_finished('Copied 2 items');assert status.opacity()==1
            manager.set_clean();assert status.opacity()==0
            assert not errors,errors
            report={'result':'passed','terminalHoldSeconds':4,'finishedHold':True,'failedHold':True,
                    'spinnerRuns':True,'statusCrossfadeAndScale':True,'dotsSlideLeft':True,
                    'dotsScaleIn':True,'historyUsesMorphClip':True,'returnFollowsMovingButton':True,'animationsDisabled':True}
            (artifacts/'report.json').write_text(json.dumps(report,indent=2),encoding='utf-8');print(json.dumps(report),flush=True)
        finally:
            session.stop();window.close();engine.deleteLater();session.deleteLater()
            QCoreApplication.sendPostedEvents(None,QEvent.DeferredDelete);app.processEvents()


if __name__=='__main__':main()
