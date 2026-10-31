# SPDX-License-Identifier: MPL-2.0
"""Select actual Settings dropdowns through their native appearance changes."""
import json
from pathlib import Path
import sys
import time


def main():
    repo=Path(__file__).resolve().parents[1];sys.path.insert(0,str(repo))
    from core.fonts import configure_text_rendering_env,apply_app_font
    configure_text_rendering_env()
    from PySide6.QtCore import QPoint,QPointF,Qt
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtTest import QTest
    from ui.presentation import LauncherModel
    from ui.runtime import create_launcher
    from quick_test_utils import visual
    app=QGuiApplication([]);apply_app_font(app)
    model=LauncherModel(palette='dark_green',language='en',settings={'animations':True,'effects':'medium'})
    model.navigate('settings');engine,window=create_launcher(model)
    errors=[];engine.warnings.connect(lambda values:errors.extend(value.toString() for value in values))
    scroll=visual(window,'settingsScroll');fade=visual(window,'appearanceFade')
    artifacts=repo/'build/qtquick_migration/choice_settings';artifacts.mkdir(parents=True,exist_ok=True)
    def wait(predicate):
        deadline=time.monotonic()+5
        while not predicate():
            assert time.monotonic()<deadline,errors
            QTest.qWait(5)
    def click(item):
        QTest.mouseClick(window,Qt.LeftButton,Qt.NoModifier,item.mapToScene(item.boundingRect().center()).toPoint())
    def reveal(item):
        center=item.mapToScene(item.boundingRect().center()).y()
        maximum=max(0,scroll.property('contentHeight')-scroll.height())
        scroll.setProperty('contentY',max(0,min(maximum,scroll.property('contentY')+center-window.height()/2)))
        QTest.qWait(20)
    results=[];other_choices=[]
    try:
        QTest.qWait(150)
        for size in ([] if '--variants-only' in sys.argv else [(1080,700),(550,400)]):
            window.resize(*size);QTest.qWait(30)
            for key in ['id','vi','zh','ru','hi','pt','tr','ja','en']:
                choice=visual(window,'languageChoice');reveal(choice)
                old_key=model.strings['languageKey']
                assert choice.property('currentKey')==old_key
                click(choice);popup=visual(window,'choicePopup_languageChoice')
                wait(lambda:popup.property('opened'))
                option=visual(window,'choice_languageChoice_'+key)
                # Scrollable menus are exercised using real keyboard routing.
                index=next(i for i,row in enumerate(model.languages) if row['key']==key)
                QTest.keyClick(window,Qt.Key_Home)
                for _ in range(index):QTest.keyClick(window,Qt.Key_Down)
                QTest.keyClick(window,Qt.Key_Return)
                label=next(row['label'] for row in model.languages if row['key']==key)
                assert choice.property('displayText')==label
                target=choice.mapRectToScene(choice.boundingRect())
                deadline=time.monotonic()+5;last=None;frames=0
                while popup.property('visible') or engine._appearance_captures.pending is not None or fade.property('opacity')>0:
                    assert time.monotonic()<deadline,errors
                    assert choice.property('displayText')==label,('Old language caption returned',size,key)
                    if popup.property('visible'):
                        assert choice.mapRectToScene(choice.boundingRect())==target
                    last=window.grabWindow();frames+=1;QTest.qWait(5)
                assert model.strings['languageKey']==key
                assert choice.property('text')==label and choice.property('previewCaption') is None
                assert not choice.property('morphHidden')
                last.save(str(artifacts/(str(size[0])+'_'+key+'.png')))
                results.append({'size':size,'language':key,'renderedFrames':frames,'width':choice.width(),'closeY':target.y(),'settledY':choice.mapToScene(QPointF()).y()})
        for size in [(1080,700),(550,400)]:
            window.resize(*size);QTest.qWait(30)
            for language in ['en','id','vi','zh','ru','hi','pt','tr','ja']:
                model.chooseLanguage(language)
                wait(lambda:engine._appearance_captures.pending is None and fade.property('opacity')==0)
                for name,keys in [('effectLevelChoice',['high','low','medium']),('projectModeChoice',['single_dir','tree'])]:
                    for key in keys:
                        choice=visual(window,name);reveal(choice);click(choice)
                        popup=visual(window,'choicePopup_'+name)
                        wait(lambda:popup.property('opened'))
                        QTest.mouseMove(window,QPoint(2,2))
                        choices=choice.property('choices').toVariant()
                        index=next(i for i,row in enumerate(choices) if row['key']==key)
                        label=choices[index]['label']
                        QTest.keyClick(window,Qt.Key_Home)
                        for _ in range(index):QTest.keyClick(window,Qt.Key_Down)
                        QTest.keyClick(window,Qt.Key_Return)
                        target=choice.mapRectToScene(choice.boundingRect())
                        assert choice.width()>=choice.implicitWidth(),('Caption does not fit like Language',name,language,key,choice.width(),choice.implicitWidth())
                        last=None;deadline=time.monotonic()+5
                        while popup.property('visible'):
                            assert time.monotonic()<deadline,('Dropdown did not finish closing',name,language,key)
                            assert choice.mapRectToScene(choice.boundingRect())==target,('Destination shook while closing',name,language,key)
                            assert choice.property('displayText')==label
                            frame=window.grabWindow()
                            if popup.property('visible'):last=frame.copy(target.toAlignedRect())
                            QTest.qWait(5)
                        assert choice.mapRectToScene(choice.boundingRect())==target,('Destination jumped after commit',name,language,key)
                        assert choice.property('text')==label and choice.property('previewCaption') is None
                        QTest.qWait(30)
                        settled=window.grabWindow().copy(target.toAlignedRect())
                        last.save(str(artifacts/'variant_return.png'))
                        settled.save(str(artifacts/'variant_settled.png'))
                        difference=sum(abs(last.pixelColor(x,y).green()-settled.pixelColor(x,y).green()) for x in range(last.width()) for y in range(last.height()))/(last.width()*last.height())
                        assert difference<1,('Closing drawing differs from final button',name,language,key,difference,target,choice.property('hovered'),choice.property('morphHoverState'))
                        other_choices.append({'size':size,'language':language,'choice':name,'key':key,'width':choice.width(),'handoffMeanDifference':round(difference,4)})
        assert not errors,errors
        print(json.dumps({'result':'passed','languageSelections':results,'effectAndModeSelections':other_choices}),flush=True)
    finally:
        engine._appearance_captures.close();engine._dialog_stack.close()
        window.close();engine.deleteLater();app.processEvents()


if __name__=='__main__':main()
