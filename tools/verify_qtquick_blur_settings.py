# SPDX-License-Identifier: MPL-2.0
"""Check presets, chrome crossfades, occluded input and shared page motion."""
import json
import os
from pathlib import Path
import sys
import time


def main():
    repo=Path(__file__).resolve().parents[1];sys.path.insert(0,str(repo));os.chdir(repo)
    from ui.runtime import initialize_quick_runtime,create_launcher
    initialize_quick_runtime()
    from PySide6.QtCore import Qt,QUrl,QPointF,QCoreApplication,QEvent,QMetaObject,Q_ARG
    from PySide6.QtGui import QColor
    from PySide6.QtQml import QQmlApplicationEngine
    from PySide6.QtWidgets import QApplication
    from PySide6.QtTest import QTest
    from core.fonts import apply_app_font
    from ui.presentation import LauncherModel
    from tools.quick_test_utils import visual
    app=QApplication([]);app.setQuitOnLastWindowClosed(False);apply_app_font(app,polish_widgets=False)
    rows=[dict(project_name=f'Project {i}',path=f'C:/fixture/{i}',port1='system',parentPath='C:/fixture') for i in range(24)]
    model=LauncherModel(language='id',projects=rows,profile={'name':'Fixture','subtitle':'Developer'},settings={'effects':'medium','animations':False})
    model.navigate('settings');commands=[];model.command.connect(lambda name,data:commands.append((name,data)))
    engine,window=create_launcher(model)
    probe_engine=QQmlApplicationEngine();probe_engine.rootContext().setContextProperty('rakPreferences',model)
    errors=[];probe_engine.warnings.connect(lambda values:errors.extend(item.toString() for item in values))
    probe_engine.load(QUrl.fromLocalFile(str(repo/'tools/qtquick_glass_fixture.qml')))
    probe_window=probe_engine.rootObjects()[0]
    out=repo/'build/qtquick_migration/blur_settings';out.mkdir(parents=True,exist_ok=True)
    def wait(predicate):
        end=time.monotonic()+6
        while not predicate():
            assert time.monotonic()<end,('Settings check timed out',errors,engine._diagnostics)
            QTest.qWait(10)
    def center(item):return item.mapToScene(QPointF(item.width()/2,item.height()/2)).toPoint()
    def click(item):
        QTest.mouseClick(window,Qt.LeftButton,Qt.NoModifier,center(item));QTest.qWait(40)
    try:
        QTest.qWait(180);window.requestActivate();QTest.qWait(50)
        choice=visual(window,'blurStyleChoice');row=visual(window,'blurRadiusSettings');scroll=visual(window,'settingsScroll')
        glass=visual(probe_window,'glassProbe')
        assert not row.isVisible() and glass.property('blurRadius')==18 and not glass.property('refracting')
        model.chooseSetting('effects','high');QTest.qWait(80)
        header=visual(window,'settingsHeaderGlass');footer=visual(window,'settingsFooterGlass')
        assert row.isVisible() and header.property('refracting') and footer.property('refracting')
        scroll.setProperty('contentY',scroll.property('contentY')+choice.mapToItem(scroll,QPointF()).y()-150);QTest.qWait(40)
        for key,radius in (('frost',17),('clear',6)):
            click(choice);click(visual(window,'choice_blurStyleChoice_'+key))
            wait(lambda:model.settings['blurRadius']==radius)
            assert choice.property('text')==key.capitalize()
            assert glass.property('blurRadius')==radius and header.property('blurRadius')==radius and footer.property('blurRadius')==radius
        window.grabWindow().save(str(out/'preset_clear.png'))
        language=visual(window,'languageChoice')
        for target_y in (65,690):
            scroll.setProperty('contentY',scroll.property('contentY')+center(language).y()-target_y);QTest.qWait(40)
            QTest.mouseMove(window,center(language));QTest.qWait(80)
            assert not language.property('hovered') and not language.property('visualHovered'),('Covered settings button hovered',target_y)
            click(language)
            assert not visual(window,'choicePopup_languageChoice').property('visible')
        scroll.setProperty('contentY',scroll.property('contentY')+center(language).y()-250);QTest.qWait(40)
        QTest.mouseMove(window,center(language));QTest.qWait(40);assert language.property('hovered')
        click(language);assert visual(window,'choicePopup_languageChoice').property('visible')
        QTest.keyClick(window,Qt.Key_Escape);QTest.qWait(50)
        model.chooseSetting('effects','low');model.chooseSetting('animations',True);QTest.qWait(40)
        model.chooseSetting('effects','high');QTest.qWait(70)
        assert 0<header.property('effectMix')<1
        assert abs(header.property('effectMix')-footer.property('effectMix'))<.001
        wait(lambda:header.property('effectMix')==1)
        model.chooseSetting('blurRadius',17);QTest.qWait(80)
        assert 6<header.property('renderedBlurRadius')<17 and abs(header.property('renderedBlurRadius')-footer.property('renderedBlurRadius'))<.001
        wait(lambda:header.property('renderedBlurRadius')==17)
        model.chooseSetting('effects','medium');QTest.qWait(60)
        assert header.property('rendering') and 0<header.property('effectMix')<1
        model.chooseSetting('effects','high');wait(lambda:header.property('effectMix')==1)
        model.chooseSetting('effects','low');QTest.qWait(60)
        assert 0<header.property('effectMix')<1
        model.chooseSetting('animations',False);QTest.qWait(10)
        assert header.property('effectMix')==0 and footer.property('effectMix')==0, [(name,[(key,item.property(key)) for key in ('effectMix','animateChanges','effectsEnabled')]) for name,item in [('header',header),('footer',footer)]]
        assert not row.isVisible()
        model.chooseSetting('effects','high');model.chooseSetting('animations',True)
        model.chooseSetting('effects','medium');wait(lambda:not header.property('rendering'))
        assert not row.isVisible() and glass.property('blurRadius')==18 and not glass.property('refracting')
        assert not header.property('refracting') and not footer.property('refracting')
        model.chooseSetting('animations',False);model.chooseSetting('effects','high');model.navigate('projects');QTest.qWait(80)
        projects=visual(window,'projectList');projects.setProperty('contentY',72);QTest.qWait(60)
        menu=visual(window,'projectMenu_0');card=visual(window,'projectCard_C:/fixture/0')
        QTest.mouseMove(window,center(menu));QTest.qWait(80)
        assert not menu.property('hovered') and card.property('color')==QColor(model.colors['BG2'])
        before=len(commands)
        QTest.mouseClick(window,Qt.LeftButton,Qt.NoModifier,center(menu));QTest.qWait(30)
        assert not any(name=='projectMenu' for name,_ in commands[before:])
        QTest.mouseMove(window,QPointF(500,650).toPoint());QTest.qWait(40)
        before=len(commands);QTest.mouseClick(window,Qt.LeftButton,Qt.NoModifier,QPointF(500,650).toPoint());QTest.qWait(30)
        assert not any(name=='openProject' for name,_ in commands[before:])
        model.navigate('settings');QTest.qWait(40);scroll.setProperty('contentY',65);QTest.qWait(40)
        profile=visual(window,'settingsProfileCard');profile.setProperty('color',QColor('red'))
        lower=next(item for item in profile.parentItem().childItems() if item.inherits('QQuickRectangle') and item.mapToScene(QPointF()).y()<660<item.mapToScene(QPointF()).y()+item.height())
        lower.setProperty('color',QColor('blue'))
        model.navigate('projects');model.chooseSetting('animations',True);model.navigate('settings');QTest.qWait(20)
        motion=visual(window,'pageMotion');clock=visual(window,'pageMotionClock')
        assert motion.property('active')
        samples=[]
        for moment in (0,140,280,480,int(clock.property('duration'))):
            QMetaObject.invokeMethod(clock,'seek',Q_ARG('QVariant',moment));QTest.qWait(40)
            frame=window.grabWindow();samples.append((moment,frame.pixelColor(500,40).red(),frame.pixelColor(500,660).blue()))
            if moment==140:frame.save(str(out/'synchronized_page_reveal.png'))
        assert samples[0][2]<samples[1][2]<samples[-1][2],('Footer sampled the completed page during its animation',samples)
        assert samples[0][1]<samples[-2][1]<=samples[-1][1],('Header missed the page reveal',samples)
        assert samples[2][2]<samples[-1][2] and abs(samples[3][2]-samples[-1][2])<2,('Footer backdrop did not follow its content reveal',samples)
        model.chooseSetting('animations',False);model.chooseSetting('effects','low');QTest.qWait(30)
        assert not header.property('rendering') and not row.isVisible() and not glass.property('rendering')
        assert probe_window.grabWindow().pixelColor(180,160)==QColor('#202020')
        assert not errors and not engine._diagnostics,(errors,engine._diagnostics)
        report={'result':'passed','presets':{'Clear':6,'Frost':17},'mediumFixedRadius':18,
                'chromeCrossfadeAndReversal':True,'occludedHoverAndClicksBlocked':True,'visibleControlsStillWork':True,
                'synchronizedPageReveal':samples,'lowDisablesBlur':True}
        (out/'report.json').write_text(json.dumps(report,indent=2));print(json.dumps(report),flush=True)
    finally:
        engine._appearance_captures.close();engine._dialog_stack.close();window.close();probe_window.close()
        engine.deleteLater();probe_engine.deleteLater();QCoreApplication.sendPostedEvents(None,QEvent.DeferredDelete);app.processEvents()


if __name__=='__main__':main()
