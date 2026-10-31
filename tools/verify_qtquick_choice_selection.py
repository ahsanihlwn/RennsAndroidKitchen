# SPDX-License-Identifier: MPL-2.0
"""Check short/long selections through the final button frame and commit."""
import json
from pathlib import Path
import sys
import time


def main():
    repo=Path(__file__).resolve().parents[1];sys.path.insert(0,str(repo))
    from core.fonts import configure_text_rendering_env,apply_app_font
    configure_text_rendering_env()
    from PySide6.QtCore import QUrl,Qt
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtQml import QQmlApplicationEngine
    from PySide6.QtTest import QTest
    from ui.presentation import LauncherModel
    from ui.runtime import IconProvider,initialize_quick_runtime
    from quick_test_utils import visual
    app=QGuiApplication([]);apply_app_font(app);initialize_quick_runtime()
    model=LauncherModel(palette='dark_green',settings={'animations':True,'effects':'medium'})
    engine=QQmlApplicationEngine();engine.addImageProvider('rak',IconProvider())
    engine.rootContext().setContextProperty('rakPreferences',model)
    engine.setInitialProperties({'uiModel':model})
    errors=[];engine.warnings.connect(lambda values:errors.extend(value.toString() for value in values))
    engine.load(QUrl.fromLocalFile(str(repo/'tools/qtquick_choice_fixture.qml')))
    assert engine.rootObjects(),errors
    window=engine.rootObjects()[0];window.show();QTest.qWait(120)
    choice=visual(window,'widthChoice');popup=visual(window,'choicePopup_widthChoice')
    artifacts=repo/'build/qtquick_migration/choice_selection';artifacts.mkdir(parents=True,exist_ok=True)
    def wait(predicate):
        deadline=time.monotonic()+4
        while not predicate():
            assert time.monotonic()<deadline,errors
            QTest.qWait(5)
    def click(item):
        QTest.mouseClick(window,Qt.LeftButton,Qt.NoModifier,item.mapToScene(item.boundingRect().center()).toPoint())
    def rect():
        return choice.mapRectToScene(choice.boundingRect()).toAlignedRect()
    def open_popup():
        click(choice);wait(lambda:popup.property('opened'))
    results=[]
    try:
        for key,label,delayed in [('long','A much longer translated option',False),('id','ID',False),('long','A much longer translated option',True),('en','English',False)]:
            window.setProperty('commitDelay',120 if delayed else 0)
            old_key=window.property('widthKey');old_width=choice.width()
            open_popup()
            click(visual(window,'choice_widthChoice_'+key))
            assert not popup.property('opening')
            assert choice.property('displayText')==label,'Close returned to the old caption'
            assert window.property('widthKey')==old_key,'Model committed before the close finished'
            target=rect();source=popup.property('sourceRect')
            assert target.right()==639,(key,target)
            assert abs(source.width()-choice.width())<.01,(key,source,choice.width())
            exact_target=choice.mapRectToScene(choice.boundingRect())
            assert abs(source.x()+popup.property('x')-exact_target.x())<.01,(key,source,exact_target)
            assert choice.width()!=old_width,(key,choice.width(),old_width)
            # Observe real rendered exit frames; do not replace the animation
            # with a geometry-only simulation. Retain its last visible frame.
            before=None;last_progress=0;deadline=time.monotonic()+4;motion_samples=[];saved=set();handoff_frames=[]
            while popup.property('visible'):
                assert time.monotonic()<deadline
                frame=window.grabWindow()
                assert choice.property('displayText')==label
                assert rect()==target,'Destination moved during the closing animation'
                if popup.property('visible'):
                    before=frame.copy(target);last_progress=popup.property('progress')
                    center=popup.property('returnCaptionCenter')
                    motion_samples.append((last_progress,center.x(),center.y(),popup.property('returnCaptionScale')))
                    if .68<=last_progress<=.85:handoff_frames.append((last_progress,frame.copy(target)))
                    for stage in [.08,.22,.48,.8]:
                        if last_progress>=stage and stage not in saved:
                            saved.add(stage)
                            frame.save(str(artifacts/(key+('_delayed' if delayed else '')+'_morph_'+str(stage)+'.png')))
                QTest.qWait(4)
            assert before is not None and last_progress>.8,last_progress
            before.save(str(artifacts/(key+('_delayed' if delayed else '')+'_return.png')))
            assert not choice.property('morphHidden')
            assert choice.property('displayText')==label
            assert rect()==target,'Button geometry changed at handoff'
            if delayed:
                assert window.property('widthKey')==old_key
                QTest.qWait(35)
                assert choice.property('displayText')==label,'Deferred commit brought the old caption back'
            wait(lambda:window.property('widthKey')==key)
            assert choice.property('text')==label and choice.property('displayText')==label
            assert choice.property('previewCaption') is None
            assert rect()==target,'Button geometry changed when the selection committed'
            QTest.qWait(20)
            after=window.grabWindow().copy(target)
            after.save(str(artifacts/(key+('_delayed' if delayed else '')+'_settled.png')))
            difference=sum(abs(before.pixelColor(x,y).green()-after.pixelColor(x,y).green()) for x in range(target.width()) for y in range(target.height()))/(target.width()*target.height())
            assert difference<1,('Final popup button differs from the live destination',key,difference,last_progress)
            def contrast(image):
                return sum(max(0,image.pixelColor(x,y).green()-image.pixelColor(x,y).red()) for y in range(image.height()) for x in range(image.width()))
            final_contrast=contrast(after)
            contrast_ratio=min(contrast(frame)/final_contrast for _,frame in handoff_frames)
            assert contrast_ratio>.9,('Button blinked during the final handoff',key,contrast_ratio)
            destination=popup.property('sourceRect')
            moving=[sample for sample in motion_samples if .03<sample[0]<.3]
            assert len(moving)>2
            assert max(sample[2] for sample in moving)-min(sample[2] for sample in moving)>2,'Selected caption stayed stationary'
            assert max(sample[3] for sample in moving)>1,'Selected caption never participated in the morph scale'
            results.append({'key':key,'delayed':delayed,'oldWidth':old_width,'newWidth':choice.width(),'lastExitProgress':last_progress,'handoffMeanDifference':difference,'minimumHandoffContrastRatio':contrast_ratio})
        open_popup();before=rect();label=choice.property('displayText')
        QTest.keyClick(window,Qt.Key_Escape)
        cancel_frames=[]
        while popup.property('visible'):
            frame=window.grabWindow()
            if .68<=popup.property('progress')<=.85:cancel_frames.append(frame.copy(before))
            QTest.qWait(4)
        assert rect()==before and choice.property('displayText')==label
        settled=window.grabWindow().copy(before)
        cancel_contrast_ratio=min(contrast(frame)/contrast(settled) for frame in cancel_frames)
        assert cancel_contrast_ratio>.9,('Cancel blinked during handoff',cancel_contrast_ratio)
        # Retarget an opening card without jumping to a geometry derived from
        # the newly resized source before the return animation has started.
        click(choice);QTest.qWait(35)
        start=popup.property('surfaceRect')
        from PySide6.QtCore import QMetaObject,Q_ARG
        QMetaObject.invokeMethod(choice,'pickChoice',Q_ARG('QVariant',1))
        assert popup.property('surfaceRect')==start,'Early selection jumped before shrinking'
        wait(lambda:not popup.property('visible'))
        assert choice.property('text')=='A much longer translated option'
        model.chooseSetting('animations',False)
        click(choice);click(visual(window,'choice_widthChoice_id'))
        assert not popup.property('visible') and choice.property('displayText')=='ID'
        assert not choice.property('morphHidden') and choice.property('previewCaption') is None
        assert not errors,errors
        print(json.dumps({'result':'passed','selections':results,'cancelPreservesValue':True,'cancelMinimumHandoffContrastRatio':cancel_contrast_ratio,'earlySelectionDoesNotJump':True,'animationsOff':True,'graphicsApi':str(window.rendererInterface().graphicsApi())}),flush=True)
    finally:
        window.close();engine.deleteLater();app.processEvents()


if __name__=='__main__':main()
