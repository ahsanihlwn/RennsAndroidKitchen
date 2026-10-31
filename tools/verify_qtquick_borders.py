# SPDX-License-Identifier: MPL-2.0
"""Audit live panel outlines across every production palette and effect level."""
import json
from pathlib import Path
import sys
import time


def main():
    repo=Path(__file__).resolve().parents[1];sys.path.insert(0,str(repo))
    from core.fonts import configure_text_rendering_env,apply_app_font
    configure_text_rendering_env()
    from PySide6.QtCore import Qt,QMetaObject
    from PySide6.QtGui import QGuiApplication,QColor
    from PySide6.QtTest import QTest
    from main_ext.class_ThemeUi import _PALETTES
    from ui.presentation import LauncherModel
    from ui.runtime import create_launcher
    from quick_test_utils import visual
    app=QGuiApplication([]);apply_app_font(app)
    model=LauncherModel(palette='dark_green',settings={'animations':False})
    engine,window=create_launcher(model);errors=[]
    engine.warnings.connect(lambda values:errors.extend(value.toString() for value in values))
    artifacts=repo/'build/qtquick_migration/borders';artifacts.mkdir(parents=True,exist_ok=True)
    def wait(predicate):
        deadline=time.monotonic()+5
        while not predicate():
            assert time.monotonic()<deadline,errors
            QTest.qWait(5)
    def border(item):
        return engine.newQObject(item).property('border').property('color').toVariant()
    results=[]
    try:
        wait(lambda: engine._frame_presenter.ready)
        model.presentationRequested.emit('newProject',{})
        create=visual(window,'createProjectPopup')
        wait(lambda:create.property('opened'))
        create.setProperty('x',628);create.setProperty('y',100)
        model.presentationRequested.emit('showDialog',{'kind':'license'})
        dialog=visual(window,'licenseWindow');wait(lambda:dialog.property('opened'))
        dialog.setProperty('x',20);dialog.setProperty('y',80)
        create_glass=visual(create,'popupGlass');dialog_glass=visual(dialog,'dialogGlass')
        login_card=visual(window,'loginCard')
        choice=visual(window,'choicePopup_effectLevelChoice')
        menu=visual(window,'projectActionsPopup')
        tip=visual(window,'buttonToolTip_settingsButton')
        for name,palette in _PALETTES.items():
            for level in ['low','medium','high']:
                model.choosePalette(name);model.chooseSetting('effects',level);QTest.qWait(15)
                expected=QColor(*palette['_border_rgb']);expected.setAlpha(13 if level=='high' else 26)
                outlines=[QColor(model.panelBorder),create_glass.property('edgeColor'),dialog_glass.property('edgeColor'),
                          choice.property('panelBorder'),menu.property('panelBorder'),border(login_card)]
                assert all(color==expected for color in outlines), (name,level,expected,outlines)
                assert create_glass.property('rendering')==(level!='low')
                assert dialog_glass.property('rendering')==(level!='low')
                assert QColor(engine.newQObject(tip).property('colors').property('BG_MODAL').toString())==QColor(palette['BG_MODAL'])
                if name in ['dark_green','dark_ocean','light','light_lavender']:
                    frame=window.grabWindow();frame.save(str(artifacts/(name+'_'+level+'.png')))
                results.append({'palette':name,'effects':level,'border':expected.name(QColor.HexArgb)})
        assert not errors,errors
        (artifacts/'report.json').write_text(json.dumps(results,indent=2),encoding='utf-8')
        print(json.dumps({'result':'passed','combinations':len(results),'panelsPerCombination':6,
              'checks':['palette neutral RGB','consistent panel hierarchy','live mode and palette updates',
                        'Low disables glass','hover tooltip follows palette']}),flush=True)
    finally:
        engine._appearance_captures.close();engine._dialog_stack.close()
        window.setProperty('managedClose',False);window.close();engine.deleteLater();app.processEvents()


if __name__=='__main__':main()
