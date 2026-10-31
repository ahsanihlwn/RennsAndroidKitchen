# SPDX-License-Identifier: MPL-2.0
"""Exercise Main dropdown selection, nested Escape and settings draft lifetime."""
import json
import argparse
import os
from pathlib import Path
import sys
import tempfile
import time


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--palette',default='dark_green');parser.add_argument('--language',default='id');args=parser.parse_args()
    repo=Path(__file__).resolve().parents[1];sys.path.insert(0,str(repo));os.chdir(repo)
    from ui.runtime import initialize_quick_runtime,IconProvider,DialogStack
    initialize_quick_runtime()
    from PySide6.QtCore import Qt,QUrl,QTimer,QEventLoop,QEvent,QCoreApplication,QPoint
    from PySide6.QtQml import QQmlApplicationEngine
    from PySide6.QtWidgets import QApplication
    from PySide6.QtTest import QTest
    from core.fonts import apply_app_font
    from core import ui_lang
    from main_ext.class_ThemeUi import theme
    from ui.presentation import LauncherModel
    from ui.workspace import WorkspaceSession
    from tools.quick_test_utils import visual,popup_frame
    app=QApplication([]);app.setQuitOnLastWindowClosed(False);apply_app_font(app,polish_widgets=False);theme.load();ui_lang.init()
    def wait_ms(ms):
        loop=QEventLoop();QTimer.singleShot(ms,loop.quit);loop.exec()
    def wait(predicate):
        end=time.monotonic()+10
        while not predicate():
            assert time.monotonic()<end,'Workspace choice timed out'
            wait_ms(10)
    with tempfile.TemporaryDirectory(prefix='rak-main-choices-') as temporary:
        root=Path(temporary);(root/'system').mkdir()
        prefs=LauncherModel(palette=args.palette,language=args.language,settings={'effects':'high','animations':True})
        session=WorkspaceSession(str(root),'system',preferences=prefs,settings_path=str(root/'settings.json'),enable_private_services=False)
        engine=QQmlApplicationEngine();errors=[];engine.warnings.connect(lambda items:errors.extend(item.toString() for item in items))
        engine.rootContext().setContextProperty('rakPreferences',prefs)
        engine.addImageProvider('icons',IconProvider());engine.addImageProvider('rak',IconProvider())
        engine.setInitialProperties({'workspace':session,'preferences':prefs})
        engine.load(QUrl.fromLocalFile(str(repo/'tools/qtquick_workspace_choices_fixture.qml')));assert engine.rootObjects(),errors
        window=engine.rootObjects()[0];stack=DialogStack(window);window.requestActivate();wait(lambda:window.isActive())
        artifacts=repo/'build/qtquick_migration/workspace_choices'/args.palette/args.language;artifacts.mkdir(parents=True,exist_ok=True)
        def click(item):
            if isinstance(item,str):item=visual(window,item)
            point=item.mapToScene(item.boundingRect().center()).toPoint();QTest.mouseMove(window,point);wait_ms(20)
            QTest.mouseClick(window,Qt.LeftButton,Qt.NoModifier,point);wait_ms(25)
        def show():
            session.dispatch('settings',{});wait(lambda:settings.property('opened'));wait_ms(50)
        try:
            settings=visual(window,'workspaceSettings');show()
            scroll=visual(window,'workspaceSettingsScroll');hint=visual(window,'workspaceSettingsScrollHint')
            wait(lambda:hint.property('opacity')==0)
            scroll.setProperty('contentY',20);wait(lambda:hint.property('opacity')==1)
            wait(lambda:hint.property('opacity')==0)
            scroll.setProperty('contentY',0)
            method=visual(window,'workspaceCompressionMethod');original=session.projectSettings
            for key in ('lz4','lzma','deflate','zstd','lz4hc'):
                click(method);popup=visual(window,'choicePopup_workspaceCompressionMethod');wait(lambda:popup.property('opened'))
                assert round(popup.property('height'))==172 and popup.property('morphOnClose')
                assert popup.property('width')>=method.width() and method.width()==92
                assert popup.property('y')>=settings.property('y') and popup.property('y')+popup.property('height')<=settings.property('y')+settings.property('height')
                assert popup.property('backdropSource')==settings.property('backdropSurface')
                if key=='lz4':
                    marker=visual(window,'modalBackdropMarker')
                    for name,value in {'x':popup.property('x')-settings.property('x')+8,'y':popup.property('y')-settings.property('y')+8,'width':popup.property('width')-16,'height':popup.property('height')-16,'visible':True}.items():marker.setProperty(name,value)
                    def sample(color):
                        marker.setProperty('color',color);wait_ms(100)
                        frame=window.grabWindow();frame.save(str(artifacts/('modal_blur_'+color+'.png')))
                        scale=frame.width()/window.width()
                        return frame.pixelColor(round((popup.property('x')+popup.property('width')-28)*scale),round((popup.property('y')+popup.property('height')-24)*scale))
                    red=sample('red');blue=sample('blue')
                    assert red.red()-blue.red()>40 and blue.blue()-red.blue()>40,(red.name(),blue.name())
                    marker.setProperty('visible',False)
                popup_frame(window,popup).save(str(artifacts/'method_menu.png'))
                click('choice_workspaceCompressionMethod_'+key);wait(lambda:not popup.property('visible'))
                assert method.property('text')==key and method.property('currentKey')==key
                assert session.projectSettings==original
            click(method);wait(lambda:popup.property('opened'));QTest.keyClick(window,Qt.Key_Escape);wait(lambda:not popup.property('visible'))
            assert settings.property('opened') and len(stack.stack)==1
            click(method);wait(lambda:popup.property('opened'));click('choice_workspaceCompressionMethod_zstd')
            wait(lambda:not popup.property('visible'))
            click('workspaceSettingsCancel');wait(lambda:not settings.property('visible'));show()
            assert method.property('text')==original['erofs']['method'],'Cancel leaked an uncommitted method into the reopened dropdown'
            click(method);wait(lambda:popup.property('opened'));QTest.keyClick(window,Qt.Key_End);QTest.keyClick(window,Qt.Key_Return)
            wait(lambda:not popup.property('visible'));assert method.property('text')=='zstd'
            click('workspaceSettingsApply');wait(lambda:not settings.property('visible'))
            assert json.loads((root/'settings.json').read_text())['erofs']['method']=='zstd'
            show();assert method.property('text')=='zstd'
            click(method);wait(lambda:popup.property('opened'))
            QTest.mouseClick(window,Qt.LeftButton,Qt.NoModifier,QPoint(10,10));wait(lambda:not popup.property('visible'))
            assert settings.property('opened')
            session.presentationRequested.emit('actionDialog',{'title':'Nested fixture','fields':[]})
            nested=visual(window,'workspaceActionDialog');wait(lambda:nested.property('opened'))
            assert nested.property('backdropSource')==settings.property('backdropSurface')
            QTest.keyClick(window,Qt.Key_Escape);wait(lambda:not nested.property('visible'))
            assert settings.property('opened')
            # Visibility/toggle changes still read the current draft after selection.
            click('workspaceSettingsToggle_erofs');assert not method.isVisible()
            click('workspaceSettingsToggle_erofs');assert method.isVisible() and method.property('text')=='zstd'
            click(method);wait(lambda:popup.property('opened'));window.resize(550,400);wait(lambda:not popup.property('visible'))
            QTest.keyClick(window,Qt.Key_Escape);wait(lambda:not settings.property('visible'))
            # Main's generated forms also use value/label choices.
            session.presentationRequested.emit('actionDialog',{'title':'Fixture','fields':[{'key':'mode','label':'Mode','value':'first','choices':[{'value':'first','label':'First'},{'value':'second','label':'Second'}]}]})
            form=visual(window,'workspaceActionDialog');wait(lambda:form.property('opened'));wait_ms(30)
            form_choice=visual(window,'workspaceFormChoice_mode');assert form_choice.property('text')=='First'
            click('workspaceFormChoice_mode');other=visual(window,'choicePopup_workspaceFormChoice_mode');wait(lambda:other.property('opened'))
            click('choice_workspaceFormChoice_mode_second');wait(lambda:not other.property('visible'))
            values=form.property('values');values=values.toVariant() if hasattr(values,'toVariant') else values
            assert values['mode']=='second'
            assert form_choice.property('text')=='Second' and form_choice.property('previewCaption') is None
            assert not errors,errors;assert not QApplication.allWidgets()
            report={'result':'passed','modalContentBlur':True,'nestedModalBackdrop':True,'scrollHintAutoHide':True,'allCompressionMethods':True,'cancelReopen':True,'applyPersistence':True,'nestedEscape':True,'outsideDismiss':True,'resizeDismiss':True,'generatedFormChoices':True,'qwidgetCount':0}
            (artifacts/'report.json').write_text(json.dumps(report,indent=2));print(json.dumps(report),flush=True)
        finally:
            window.grabWindow().save(str(artifacts/'last_frame.png'))
            stack.close();session.stop();window.close();engine.deleteLater();session.deleteLater()
            QCoreApplication.sendPostedEvents(None,QEvent.DeferredDelete);app.processEvents()


if __name__=='__main__':main()
