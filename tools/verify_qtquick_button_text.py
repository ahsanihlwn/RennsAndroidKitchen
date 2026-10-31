# SPDX-License-Identifier: MPL-2.0
"""Render real press/release text and verify that scaling preserves its layout."""
import json
from pathlib import Path
import sys
import time


def main():
    repo=Path(__file__).resolve().parents[1];sys.path.insert(0,str(repo))
    from core.fonts import configure_text_rendering_env,apply_app_font
    configure_text_rendering_env()
    from PySide6.QtCore import QUrl,Qt,QPoint,QPointF,QObject
    from PySide6.QtGui import QGuiApplication,QColor,QImage,QPainter,QPainterPath
    from PySide6.QtQml import QQmlApplicationEngine
    from PySide6.QtTest import QTest
    from ui.presentation import LauncherModel
    from ui.runtime import IconProvider,initialize_quick_runtime
    from quick_test_utils import visual
    app=QGuiApplication([]);apply_app_font(app);initialize_quick_runtime()
    model=LauncherModel(palette='dark_green')
    engine=QQmlApplicationEngine();errors=[]
    engine.warnings.connect(lambda values:errors.extend(value.toString() for value in values))
    engine.addImageProvider('rak',IconProvider());engine.rootContext().setContextProperty('rakPreferences',model)
    engine.setInitialProperties({'uiModel':model})
    engine.load(QUrl.fromLocalFile(str(repo/'tools/qtquick_button_fixture.qml')))
    assert engine.rootObjects(),errors
    window=engine.rootObjects()[0];window.show();QTest.qWait(120)
    artifacts=repo/'build/qtquick_migration/button_text';artifacts.mkdir(parents=True,exist_ok=True)
    results=[]
    try:
        for palette in ['dark_green','light','dark_ocean','light_lavender']:
            model.choosePalette(palette);QTest.qWait(20)
            for name in ['normal','primary','rom','choice','small','drive_C:','projectCard_C:/fixture']:
                button=visual(window,name)
                is_card=name.startswith(('drive_','projectCard_'))
                if is_card:
                    text='C:' if name.startswith('drive_') else 'Fixture project'
                    caption=next(item for item in button.findChildren(QObject) if item.inherits('QQuickText') and item.property('text')==text and item.metaObject().indexOfProperty('transformed')>=0)
                else:caption=visual(window,'choiceCaption_dropdownArrow_choice' if name=='choice' else 'buttonCaption_'+name)
                layout=(caption.property('implicitWidth'),caption.property('implicitHeight'),button.width(),button.height())
                filename=name.replace(':','_').replace('/','_')
                point=button.mapToScene(button.boundingRect().center()).toPoint()
                QTest.mouseMove(window,point);QTest.qWait(30)
                window.grabWindow().save(str(artifacts/(palette+'_'+filename+'_hover.png')))
                QTest.mousePress(window,Qt.LeftButton,Qt.NoModifier,point);QTest.qWait(120)
                target_scale=.97 if name.startswith('projectCard_') else .96
                assert abs(button.property('scale')-target_scale)<.001
                assert engine.newQObject(caption).property('renderType').toInt()==2,('Scaled caption used bitmap glyphs',name)
                after=(caption.property('implicitWidth'),caption.property('implicitHeight'),button.width(),button.height())
                assert max(abs(a-b) for a,b in zip(layout,after))<.01,('Text layout shifted when rendering changed',name,layout,after)
                window.grabWindow().save(str(artifacts/(palette+'_'+filename+'_pressed.png')))
                QTest.mouseMove(window,QPoint(690,250))
                QTest.mouseRelease(window,Qt.LeftButton,Qt.NoModifier,QPoint(690,250))
                deadline=time.monotonic()+3
                while button.property('scale')!=1:
                    assert time.monotonic()<deadline;QTest.qWait(5)
                assert engine.newQObject(caption).property('renderType').toInt()==1
                results.append({'palette':palette,'button':name,'nativeLayout':layout,'pressScale':target_scale})
        model.choosePalette('dark_green');QTest.qWait(20)
        normal=visual(window,'normal');bitmap=visual(window,'legacyBitmap')
        for button in [normal,bitmap]:button.setProperty('down',True);button.setProperty('pressScale',.96)
        QTest.qWait(30)
        actual=window.grabWindow();actual.save(str(artifacts/'vector_and_legacy_pressed.png'))
        differences=[]
        for button,caption in [(normal,visual(window,'buttonCaption_normal')),(bitmap,bitmap.property('contentItem').childItems()[0])]:
            reference=QImage(actual.size(),QImage.Format_ARGB32_Premultiplied)
            reference.setDevicePixelRatio(actual.devicePixelRatio());reference.fill(QColor(model.colors['BG2']))
            painter=QPainter(reference);painter.setRenderHint(QPainter.Antialiasing)
            painter.fillRect(0,0,window.width(),window.height(),button.property('downFillColor'))
            origin=caption.mapToScene(QPointF())
            painter.translate(origin);painter.scale(.96,.96)
            path=QPainterPath();path.addText(QPointF(0,caption.property('baselineOffset')),caption.property('font'),button.property('text'))
            painter.fillPath(path,button.property('labelColor'));painter.end()
            logical=caption.mapRectToScene(caption.boundingRect())
            ratio=actual.devicePixelRatio()
            rect=type(logical)(logical.x()*ratio,logical.y()*ratio,logical.width()*ratio,logical.height()*ratio).toAlignedRect()
            a,b=actual.copy(rect),reference.copy(rect)
            difference=sum(abs(a.pixelColor(x,y).green()-b.pixelColor(x,y).green()) for y in range(a.height()) for x in range(a.width()))/(a.width()*a.height())
            a.save(str(artifacts/(button.objectName()+'_pressed_crop.png')))
            differences.append(difference)
        assert differences[0]<differences[1],('Vector press did not improve against direct glyph outlines',differences)
        assert not errors,errors
        report={'result':'passed','devicePixelRatio':window.devicePixelRatio(),'directOutlineError':{'vector':differences[0],'legacyBitmap':differences[1]},'checks':results}
        (artifacts/('report_'+str(round(window.devicePixelRatio()*100))+'.json')).write_text(json.dumps(report,indent=2),encoding='utf-8')
        print(json.dumps({key:value for key,value in report.items() if key!='checks'}|{'controls':len(results)}),flush=True)
    finally:
        window.close();engine.deleteLater();app.processEvents()


if __name__=='__main__':main()
