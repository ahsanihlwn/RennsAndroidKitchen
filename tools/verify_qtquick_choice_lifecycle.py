# SPDX-License-Identifier: MPL-2.0
"""Exercise the source anchor and buttons behind a closing choice popup."""
from pathlib import Path
import json
import subprocess
import sys
import time


def verify_reference(repo):
    """Run the authoritative QWidget lifecycle in its own import context."""
    source=repo.parent/'rennsandroidkitchen'
    sys.path.insert(0,str(source))
    sys.dont_write_bytecode=True
    from core.fonts import configure_text_rendering_env,apply_app_font
    configure_text_rendering_env()
    from PySide6.QtCore import QPoint,QTimer,Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication,QPushButton,QWidget
    from app_ext.class_ChoiceMorphPopup import ChoiceMorphPopup
    from app_ext.class_ButtonPressAnimation import install_button_press_animations
    from main_ext.class_ThemeUi import theme
    app=QApplication([]);apply_app_font(app);install_button_press_animations(app)
    theme._animations_enabled=True;theme._blur_enabled=False
    root=QWidget();root.resize(700,500);root.setStyleSheet(theme.app_stylesheet())
    anchor=QPushButton('Medium',root);anchor.setGeometry(460,300,180,28)
    behind=QPushButton('Behind dropdown',root);behind.setGeometry(20,20,150,32)
    clicks=[];behind.clicked.connect(lambda:clicks.append(True))
    root.show();QTest.qWait(50)
    results={};failures=[]
    popup=ChoiceMorphPopup(root,anchor,[('low','Low'),('medium','Medium'),('high','High')],'medium')
    timer=QTimer(root);timer.setInterval(5)
    stage=0
    def probe():
        nonlocal stage
        try:
            assert anchor.graphicsEffect().opacity()==0
            if stage==0 and popup._progress>=1:
                QTest.mousePress(behind,Qt.LeftButton)
                assert not behind.isDown()
                QTest.mouseRelease(behind,Qt.LeftButton)
                assert not clicks
                assert popup._closing
                stage=1
                results['outsideClickConsumed']=True
            elif stage==1 and .1<popup._progress<.8:
                QTest.mouseClick(behind,Qt.LeftButton)
                assert not clicks
                assert anchor.graphicsEffect().opacity()==0
                results['closingClickConsumed']=True
                stage=2
        except BaseException as error:
            failures.append(repr(error));popup._finalizing=True;popup.close()
    timer.timeout.connect(probe);timer.start()
    timeout=QTimer(root);timeout.setSingleShot(True)
    def expire():
        failures.append('Reference popup timeout');popup._finalizing=True;popup.close()
    timeout.timeout.connect(expire);timeout.start(4000)
    try:
        assert popup.exec() is None
        timer.stop();timeout.stop()
        assert not failures,failures
        assert stage==2,stage
        assert anchor.graphicsEffect() is None
        QTest.mouseClick(behind,Qt.LeftButton)
        assert clicks==[True]
        results['sourceRestoredAndButtonUsable']=True
        popup=ChoiceMorphPopup(root,anchor,[('low','Low'),('medium','Medium'),('high','High')],'medium')
        def press_animated_edge():
            try:
                assert 0<popup._progress<1,popup._progress
                point=QPoint(popup.width()//2,popup.height()-1)
                assert not popup.card.geometry().contains(point)
                QTest.mouseClick(popup,Qt.LeftButton,Qt.NoModifier,point)
                assert popup._closing
                results['animatedBoundsDismiss']=True
            except BaseException as error:
                failures.append(repr(error));popup._finalizing=True;popup.close()
        QTimer.singleShot(35,press_animated_edge)
        timeout.start(4000)
        assert popup.exec() is None
        timeout.stop()
        assert not failures,failures
        print(json.dumps({'reference':str(source),'result':'passed',**results}),flush=True)
    finally:
        root.close();app.processEvents()


def main():
    repo=Path(__file__).resolve().parents[1]
    if '--reference' in sys.argv:
        verify_reference(repo)
        return
    reference=subprocess.run([sys.executable,str(Path(__file__).resolve()),'--reference'],capture_output=True,text=True,timeout=15)
    assert reference.returncode==0,reference.stdout+reference.stderr
    reference_report=json.loads(reference.stdout.strip().splitlines()[-1])
    sys.path.insert(0,str(repo))
    from core.fonts import configure_text_rendering_env,apply_app_font
    configure_text_rendering_env()
    from PySide6.QtCore import QMetaObject,QPoint,QUrl,Qt
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtQml import QQmlApplicationEngine
    from PySide6.QtTest import QTest
    from ui.presentation import LauncherModel
    from ui.runtime import IconProvider,initialize_quick_runtime
    from quick_test_utils import visual
    app=QGuiApplication([]);apply_app_font(app);initialize_quick_runtime()
    model=LauncherModel(palette='dark_green',settings={'animations':True,'effects':'medium'})
    engine=QQmlApplicationEngine()
    engine.rootContext().setContextProperty('rakPreferences',model)
    engine.addImageProvider('rak',IconProvider())
    errors=[]
    engine.warnings.connect(lambda values:errors.extend(value.toString() for value in values))
    engine.setInitialProperties({'uiModel':model})
    engine.load(QUrl.fromLocalFile(str(repo/'tools/qtquick_choice_fixture.qml')))
    assert engine.rootObjects(),errors
    window=engine.rootObjects()[0];window.show();QTest.qWait(100)
    choice=visual(window,'fixtureChoice');behind=visual(window,'behindButton')
    popup=visual(window,'choicePopup_fixtureChoice')
    artifacts=repo/'build/qtquick_migration/choice_lifecycle'
    artifacts.mkdir(parents=True,exist_ok=True)
    def wait(predicate):
        deadline=time.monotonic()+4
        while not predicate():
            assert time.monotonic()<deadline,errors
            QTest.qWait(5)
    def click(item):
        QTest.mouseClick(window,Qt.LeftButton,Qt.NoModifier,item.mapToScene(item.boundingRect().center()).toPoint())
    def open_popup():
        QTest.mouseMove(window,choice.mapToScene(choice.boundingRect().center()).toPoint())
        QTest.qWait(20)
        click(choice);wait(lambda:popup.property('opened'))
        assert choice.property('morphHidden') and choice.property('opacity')==0
    report={}
    try:
        point=choice.mapToScene(choice.boundingRect().center()).toPoint()
        QTest.mouseMove(window,point);QTest.qWait(30)
        rect=choice.mapRectToScene(choice.boundingRect()).toAlignedRect()
        source_image=window.grabWindow().copy(rect)
        source_image.save(str(artifacts/'source_button.png'))
        open_popup()
        # Inspect the cached complete button, with its original hover state.
        # The reference grabs this before its popup takes the mouse away.
        popup.setProperty('opening',False);popup.setProperty('closePhase',1);popup.setProperty('progress',1)
        QTest.qWait(30)
        cached_image=window.grabWindow().copy(rect)
        cached_image.save(str(artifacts/'returning_button.png'))
        source_error=sum(abs(source_image.pixelColor(x,y).green()-cached_image.pixelColor(x,y).green()) for x in range(rect.width()) for y in range(rect.height()))/(rect.width()*rect.height())
        assert source_error<1,('Cached source button lost its opening hover appearance',source_error)
        popup.setProperty('opening',True);popup.setProperty('progress',1)
        report['cachedButtonMeanDifference']=source_error
        # A Qt.Popup consumes the dismissing press even if a real button is
        # underneath. This includes that button's pressed-scale feedback.
        point=behind.mapToScene(behind.boundingRect().center()).toPoint()
        QTest.mousePress(window,Qt.LeftButton,Qt.NoModifier,point)
        assert not behind.property('pressed'),'Outside dismissal pressed the button behind the popup'
        assert not behind.property('pressFeedback'),'Outside dismissal animated the button behind the popup'
        QTest.mouseRelease(window,Qt.LeftButton,Qt.NoModifier,point)
        wait(lambda:not popup.property('visible'))
        assert window.property('behindClicks')==0,'Outside dismissal clicked the button behind the popup'
        assert not choice.property('morphHidden') and choice.property('opacity')==1
        click(behind);assert window.property('behindClicks')==1,'Button did not resume after popup close'
        report['outsideClickConsumed']=True
        open_popup()
        QMetaObject.invokeMethod(popup,'close')
        # Keep the underlying button protected during the exit transition.
        QTest.qWait(35)
        assert popup.property('visible') and choice.property('morphHidden')
        click(behind)
        assert window.property('behindClicks')==1,'Closing popup leaked a click to the button behind it'
        wait(lambda:not popup.property('visible'))
        report['closingClickConsumed']=True
        open_popup()
        click(visual(window,'choice_fixtureChoice_high'))
        assert window.property('choicesCommitted')==0,'Selection committed before the closing animation'
        assert choice.property('morphHidden')
        wait(lambda:not popup.property('visible'))
        assert window.property('choicesCommitted')==1
        assert not window.property('commitWhileHidden'),'Selection committed while its real source button was still hidden'
        assert choice.property('text')=='High' and not choice.property('morphHidden')
        assert choice.property('morphHoverState')==-1
        window.grabWindow().save(str(artifacts/'restored.png'))
        report['sourceRestoredBeforeCommit']=True
        click(choice);QTest.qWait(25)
        surface=popup.property('surfaceRect')
        assert surface.height()<popup.property('height')-2
        edge=QPoint(round(popup.property('x')+popup.property('width')/2),round(popup.property('y')+popup.property('height')-1))
        QTest.mouseClick(window,Qt.LeftButton,Qt.NoModifier,edge)
        assert not popup.property('opening'),'A press outside the animated card did not dismiss the dropdown'
        wait(lambda:not popup.property('visible'))
        report['animatedBoundsDismiss']=True
        assert not errors,errors
        print(json.dumps({'result':'passed','originalLifecycle':reference_report,**report}),flush=True)
    finally:
        window.close();engine.deleteLater();app.processEvents()


if __name__=='__main__':main()
