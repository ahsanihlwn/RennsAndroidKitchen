# SPDX-License-Identifier: MPL-2.0
"""Exercise native slider/switch input, spring thumbs and all three effect levels."""
import json
import os
from pathlib import Path
import sys
import time


def main():
    repo=Path(__file__).resolve().parents[1];sys.path.insert(0,str(repo));os.chdir(repo)
    from ui.runtime import initialize_quick_runtime
    initialize_quick_runtime()
    from PySide6.QtCore import Qt,QUrl,QPointF,QCoreApplication,QEvent
    from PySide6.QtGui import QColor
    from PySide6.QtQml import QQmlApplicationEngine
    from PySide6.QtWidgets import QApplication
    from PySide6.QtTest import QTest
    from core.fonts import apply_app_font
    from ui.presentation import LauncherModel
    from tools.quick_test_utils import visual
    app=QApplication([]);app.setQuitOnLastWindowClosed(False);apply_app_font(app,polish_widgets=False)
    model=LauncherModel(palette='dark_green',settings={'effects':'high','animations':True,'blurRadius':5})
    engine=QQmlApplicationEngine();errors=[]
    engine.warnings.connect(lambda values:errors.extend(item.toString() for item in values))
    engine.rootContext().setContextProperty('rakPreferences',model)
    engine.setInitialProperties({'preferences':model});engine.load(QUrl.fromLocalFile(str(repo/'tools/qtquick_knob_fixture.qml')))
    assert engine.rootObjects(),errors
    window=engine.rootObjects()[0];window.requestActivate()
    out=repo/'build/qtquick_migration/knobs';out.mkdir(parents=True,exist_ok=True)
    def wait(predicate):
        end=time.monotonic()+4
        while not predicate():
            assert time.monotonic()<end,('Knob check timed out',errors)
            QTest.qWait(10)
    def center(item):return item.mapToScene(QPointF(item.width()/2,item.height()/2)).toPoint()
    def frame(knob):
        # Compare the unscaled inner square, which stays inside the enlarged
        # thumb and excludes the exposed track and antialiased outer edge.
        origin=knob.mapToScene(QPointF(0,0)).toPoint()
        return window.grabWindow().copy(origin.x(),origin.y(),round(knob.width()),round(knob.height()))
    def released(knob):
        wait(lambda:not knob.property('glassActive') and abs(knob.property('visualWidth')-knob.width())<.05)
    try:
        QTest.qWait(120)
        slider=visual(window,'motionSlider');toggle=visual(window,'motionSwitch')
        slider_knob=visual(window,'sliderKnob_motionSlider');switch_knob=visual(window,'switchKnob_motionSwitch')
        levels=[];pressed_images={}
        for level in ('low','medium','high'):
            model.chooseSetting('effects',level)
            slider.setProperty('value',50);toggle.setProperty('checked',False);QTest.qWait(25)
            released(switch_knob);QTest.qWait(50)
            assert slider_knob.width()==12 and switch_knob.width()==20
            assert not slider_knob.property('glassActive') and not switch_knob.property('glassActive')
            idle=window.grabWindow();idle.save(str(out/(level+'_idle.png')))
            slider_point=center(slider_knob)
            QTest.mousePress(window,Qt.LeftButton,Qt.NoModifier,slider_point);QTest.qWait(350)
            slider_scale=slider_knob.property('visualWidth')/slider_knob.width()
            assert 1.4<slider_scale<1.65,(level,slider_scale)
            assert slider_knob.property('glassActive')==(level!='low')
            assert slider_knob.property('refracting')==(level=='high')
            pressed_images[level]=frame(slider_knob)
            window.grabWindow().save(str(out/(level+'_slider_pressed.png')))
            before=frame(slider_knob);slider.setProperty('trackFillColor',QColor('#ff3040'));QTest.qWait(90)
            after=frame(slider_knob)
            assert (after!=before)==(level!='low'),('Thumb did not follow its live track, or Low sampled it',level)
            slider.setProperty('trackFillColor',QColor(model.colors['a55']))
            ratios=[]
            for step in range(1,9):
                target=slider.mapToScene(QPointF(slider.width()/2+step*18,13)).toPoint()
                QTest.mouseMove(window,target,8)
                QTest.qWait(12)
                ratios.append(slider_knob.property('visualWidth')/slider_knob.property('visualHeight'))
            assert slider.property('value')>75 and window.property('sliderMoves')>0
            assert max(ratios)-min(ratios)>.015,('Dragging did not stretch the thumb',level,ratios)
            QTest.mouseRelease(window,Qt.LeftButton,Qt.NoModifier,target);released(slider_knob)
            # Native switch drag still follows the thumb and commits once.
            toggles=window.property('switchToggles');switch_point=center(switch_knob)
            QTest.mousePress(window,Qt.LeftButton,Qt.NoModifier,switch_point);QTest.qWait(350)
            switch_scale=switch_knob.property('visualWidth')/switch_knob.width()
            assert 1.7<switch_scale<1.95,(level,switch_scale)
            assert 1.25<switch_knob.property('visualHeight')/switch_knob.height()<1.45
            assert switch_knob.property('glassActive')==(level!='low')
            assert switch_knob.property('refracting')==(level=='high')
            window.grabWindow().save(str(out/(level+'_switch_pressed.png')))
            target=toggle.mapToScene(QPointF(toggle.width()-3,toggle.height()/2)).toPoint()
            QTest.mouseMove(window,target,50);QTest.qWait(50)
            assert toggle.property('position')>.7 and 0<toggle.property('knobPosition')<toggle.property('position')
            assert toggle.property('thumbActive'),'Toggle shrank while its position spring was still catching up'
            QTest.mouseRelease(window,Qt.LeftButton,Qt.NoModifier,target);released(switch_knob)
            assert toggle.property('checked') and window.property('switchToggles')==toggles+1
            toggle.forceActiveFocus();QTest.keyClick(window,Qt.Key_Space);QTest.qWait(50)
            assert not toggle.property('checked') and window.property('switchToggles')==toggles+2
            released(switch_knob)
            # A normal quick click must produce the same swell/travel/settle
            # sequence as a held drag, rather than skipping press feedback.
            QTest.mouseClick(window,Qt.LeftButton,Qt.NoModifier,center(toggle))
            click_samples=[]
            for _ in range(30):
                QTest.qWait(16)
                click_samples.append((toggle.property('knobPosition'),switch_knob.property('visualWidth')/switch_knob.width(),toggle.property('thumbActive')))
            assert toggle.property('checked') and window.property('switchToggles')==toggles+3
            assert max(sample[1] for sample in click_samples)>1.4,(level,click_samples)
            assert any(.1<position<.95 and scale>1.1 and held for position,scale,held in click_samples)
            released(switch_knob)
            slider.forceActiveFocus();value=slider.property('value');QTest.keyClick(window,Qt.Key_Left)
            assert slider.property('value')<value
            levels.append({'level':level,'sliderPressedScale':round(slider_scale,3),'switchPressedScale':round(switch_scale,3)})
        assert pressed_images['high']!=pressed_images['medium'],'High and Medium rendered the same thumb'
        # Disabling animations still changes press state immediately, without a lingering glass layer.
        model.chooseSetting('animations',False);slider.setProperty('value',50);QTest.qWait(100)
        point=center(slider_knob);QTest.mousePress(window,Qt.LeftButton,Qt.NoModifier,point)
        assert slider_knob.property('visualWidth')==18
        QTest.mouseRelease(window,Qt.LeftButton,Qt.NoModifier,point)
        assert slider_knob.property('visualWidth')==12 and not slider_knob.property('glassActive')
        point=center(switch_knob);QTest.mousePress(window,Qt.LeftButton,Qt.NoModifier,point)
        assert switch_knob.property('visualWidth')==36 and switch_knob.property('visualHeight')==27
        QTest.mouseRelease(window,Qt.LeftButton,Qt.NoModifier,point)
        assert switch_knob.property('visualWidth')==20 and not switch_knob.property('glassActive')
        toggle.setProperty('enabled',False);toggles=window.property('switchToggles')
        QTest.mouseClick(window,Qt.LeftButton,Qt.NoModifier,center(toggle));assert window.property('switchToggles')==toggles
        assert not errors,errors
        report={'result':'passed','levels':levels,'nativeSliderDragAndKeys':True,'nativeSwitchDragClickAndKeys':True,
                'liveTrackWithoutFeedback':True,'highOnlyRefraction':True,'lowUsesSolidColor':True,
                'dragStretchAndSpringRelease':True,'quickToggleClickSwellTravelSettle':True,'animationsOffAndDisabled':True}
        (out/'report.json').write_text(json.dumps(report,indent=2));print(json.dumps(report),flush=True)
    finally:
        window.close();engine.deleteLater();QCoreApplication.sendPostedEvents(None,QEvent.DeferredDelete);app.processEvents()


if __name__=='__main__':main()
