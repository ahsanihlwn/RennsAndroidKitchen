# SPDX-License-Identifier: MPL-2.0
"""Compare the original dropdown opening and full source-button image."""
import ast
import json
from pathlib import Path
import sys
import time
from types import SimpleNamespace


def main():
    repo=Path(__file__).resolve().parents[1];sys.path.insert(0,str(repo))
    source=repo.parent/'rennsandroidkitchen/app_ext/class_ChoiceMorphPopup.py'
    module=ast.parse(source.read_text(encoding='utf-8'))
    cls=next(node for node in module.body if isinstance(node,ast.ClassDef) and node.name=='ChoiceMorphPopup')
    method=next(node for node in cls.body if isinstance(node,ast.FunctionDef) and node.name=='_set_progress')
    from core.fonts import configure_text_rendering_env,apply_app_font
    configure_text_rendering_env()
    from PySide6.QtCore import QObject,QRect,QPointF,Qt,QMetaObject
    from PySide6.QtGui import QGuiApplication,QRegion,QImage,QPainter,QColor
    from PySide6.QtTest import QTest
    from ui.presentation import LauncherModel
    from ui.runtime import create_launcher
    app=QGuiApplication([]);apply_app_font(app)
    scope={'QRect':QRect,'QRegion':QRegion}
    exec(compile(ast.Module(body=[method],type_ignores=[]),str(source),'exec'),scope)
    model=LauncherModel(palette='dark_green',settings={'animations':False,'effects':'medium'})
    model.navigate('settings');engine,window=create_launcher(model,visible=False)
    errors=[];engine.warnings.connect(lambda values:errors.extend(error.toString() for error in values))
    window.setFlags(window.flags()|Qt.Tool|Qt.WindowDoesNotAcceptFocus);window.show();QTest.qWait(200)
    def obj(name):
        item=window.findChild(QObject,name)
        if item is not None:return item
        pending=[window.contentItem()]
        while pending:
            item=pending.pop();pending.extend(item.childItems())
            if item.objectName()==name:return item
        raise AssertionError(name)
    def wait(predicate):
        deadline=time.monotonic()+5
        while not predicate():
            assert time.monotonic()<deadline,errors
            QTest.qWait(5)
    results=[]
    try:
        obj('settingsScroll').setProperty('contentY',200);QTest.qWait(20)
        choice=obj('effectLevelChoice');original_y=choice.y()
        for upward,position in ((False,320),(True,600)):
            choice.setY(position-choice.parentItem().mapToScene(QPointF()).y());QTest.qWait(25)
            anchor=choice.mapToScene(QPointF()).toPoint()
            # QWidget.grab() in the reference captures the complete anchor,
            # including a part clipped by its scrolling parent. Use a full
            # item grab for the corresponding source-image comparison.
            capture=choice.grabToImage();wait(lambda:not capture.image().isNull())
            before=QImage(round(choice.width()),round(choice.height()),QImage.Format_ARGB32_Premultiplied)
            before.fill(QColor(model.colors['BG2']))
            painter=QPainter(before);painter.drawImage(0,0,capture.image());painter.end()
            QMetaObject.invokeMethod(choice,'openChoices');QTest.qWait(35)
            popup=obj('choicePopup_effectLevelChoice')
            assert popup.property('upward')==upward
            source_rect=popup.property('sourceRect');w=round(popup.property('width'));h=round(popup.property('height'))
            reference=SimpleNamespace(_source_rect=QRect(round(source_rect.x()),round(source_rect.y()),round(source_rect.width()),round(source_rect.height())),_target_rect=QRect(0,0,w,h),height=lambda:h)
            reference.card=SimpleNamespace(setGeometry=lambda rect:setattr(reference,'cardRect',rect),update=lambda:None)
            reference._body=SimpleNamespace(move=lambda x,y:setattr(reference,'bodyY',y),y=lambda:reference.bodyY,setMask=lambda mask:setattr(reference,'bodyMask',mask.boundingRect()),setVisible=lambda visible:setattr(reference,'bodyVisible',visible))
            reference._caption_effect=SimpleNamespace(setOpacity=lambda value:setattr(reference,'captionAlpha',value))
            reference._upward=upward
            popup.setProperty('opening',True)
            clip=popup.findChild(QObject,'popupFormClip');form=popup.findChild(QObject,'popupForm');caption=popup.findChild(QObject,'morphSourceCaption')
            for elapsed in (0,.05,.2,.4,.7,.95,1):
                popup.setProperty('progress',elapsed);phase=popup.property('choicePhase')
                scope['_set_progress'](reference,phase)
                rect=popup.property('surfaceRect')
                assert (round(rect.x()),round(rect.y()),round(rect.width()),round(rect.height()))==reference.cardRect.getRect(),(upward,elapsed,rect,reference.cardRect)
                assert clip.property('x')==rect.x()+4 and clip.property('y')==rect.y()+4
                assert clip.property('width')==max(0,rect.width()-8) and clip.property('height')==max(0,rect.height()-8)
                assert form.property('y')==reference.bodyY-4
                assert bool(popup.property('formAlpha'))==reference.bodyVisible
                assert abs(caption.property('opacity')-reference.captionAlpha)<1e-8
                assert (caption.property('x'),caption.property('y'))==(source_rect.x(),source_rect.y())
                assert (caption.property('width'),caption.property('height'))==(source_rect.width(),source_rect.height())
            popup.setProperty('progress',0);QTest.qWait(30)
            after=window.grabWindow().copy(anchor.x(),anchor.y(),round(choice.width()),round(choice.height()))
            error=sum(abs(before.pixelColor(x,y).green()-after.pixelColor(x,y).green()) for x in range(before.width()) for y in range(before.height()))/(before.width()*before.height())
            assert error<1,('Restored source caption differs',upward,error)
            results.append({'upward':upward,'openingSamples':7,'restoredCaptionMeanDifference':error})
            QMetaObject.invokeMethod(popup,'close');QTest.qWait(20)
        choice.setY(original_y);model.chooseSetting('animations',True)
        QMetaObject.invokeMethod(choice,'openChoices');popup=obj('choicePopup_effectLevelChoice');wait(lambda:popup.property('opened'))
        option=obj('choice_effectLevelChoice_medium');point=option.mapToScene(option.boundingRect().center()).toPoint()
        QTest.mousePress(window,Qt.LeftButton,Qt.NoModifier,point);QTest.qWait(70)
        assert option.property('scale')<1
        QTest.mouseRelease(window,Qt.LeftButton,Qt.NoModifier,point)
        assert option.property('scale')==1 and not popup.property('opening'),'Pressed option continued scaling during close'
        wait(lambda:not popup.property('visible'))
        assert not errors,errors
        print(json.dumps({'result':'passed','source':str(source),'geometryAndCaption':results,'pressedOptionResetsBeforeClose':True}),flush=True)
    finally:
        engine._dialog_stack.close();window.close();engine.deleteLater();app.processEvents()


if __name__=='__main__':main()
