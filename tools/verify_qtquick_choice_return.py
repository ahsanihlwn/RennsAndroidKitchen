# SPDX-License-Identifier: MPL-2.0
"""Compare the returning button with the actual reference ButtonCardMorph."""
import ast
import json
import math
from pathlib import Path
import sys
from types import SimpleNamespace


def main():
    repo=Path(__file__).resolve().parents[1];sys.path.insert(0,str(repo))
    source=repo.parent/'rennsandroidkitchen/app_ext/class_ButtonCardMorph.py'
    module=ast.parse(source.read_text(encoding='utf-8'))
    cls=next(node for node in module.body if isinstance(node,ast.ClassDef) and node.name=='ButtonCardMorph')
    functions=[next(node for node in module.body if isinstance(node,ast.FunctionDef) and node.name=='_spring_progress')]
    for name in ['_opacity','_advance']:
        function=next(node for node in cls.body if isinstance(node,ast.FunctionDef) and node.name==name)
        function.decorator_list=[];functions.append(function)
    from core.fonts import configure_text_rendering_env,apply_app_font
    configure_text_rendering_env()
    from PySide6.QtCore import QEasingCurve,QPointF,QRectF,QUrl,Qt,QMetaObject
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtQml import QQmlApplicationEngine
    from PySide6.QtTest import QTest
    from ui.presentation import LauncherModel
    from ui.runtime import IconProvider,initialize_quick_runtime
    from quick_test_utils import visual
    scope={'QRectF':QRectF,**{name:getattr(math,name) for name in ['cos','exp','sin','sqrt']}}
    exec(compile(ast.Module(body=functions,type_ignores=[]),str(source),'exec'),scope)
    app=QGuiApplication([]);apply_app_font(app);initialize_quick_runtime()
    model=LauncherModel(palette='dark_green',settings={'animations':False,'effects':'medium'})
    engine=QQmlApplicationEngine();engine.addImageProvider('rak',IconProvider())
    engine.rootContext().setContextProperty('rakPreferences',model);engine.setInitialProperties({'uiModel':model})
    errors=[];engine.warnings.connect(lambda values:errors.extend(value.toString() for value in values))
    engine.load(QUrl.fromLocalFile(str(repo/'tools/qtquick_choice_fixture.qml')))
    window=engine.rootObjects()[0];window.show();QTest.qWait(80)
    choice=visual(window,'fixtureChoice');popup=visual(window,'choicePopup_fixtureChoice')
    checked=[]
    try:
        for upward,y in [(False,150),(True,470)]:
            choice.setY(y);QMetaObject.invokeMethod(choice,'openChoices');QTest.qWait(25)
            assert popup.property('upward')==upward
            target=QRectF(0,0,popup.property('width'),popup.property('height'))
            destination=popup.property('sourceRect')
            reference=SimpleNamespace(_opening=False,_card_rect=target,_button_rect=destination,_launch_scale=1.,_growth_width=max(1.,target.width()-destination.width()),_fade_easing=QEasingCurve(QEasingCurve.OutCubic),_opacity=scope['_opacity'],_source_effect=None,update=lambda:None)
            popup.setProperty('closeRect',target);popup.setProperty('closeAlpha',1)
            popup.setProperty('opening',False)
            for selected in [False,True]:
                start=QPointF(target.width()/2,20 if upward else 86)
                popup.setProperty('returnToSelection',selected)
                popup.setProperty('captionStart',start if selected else None)
                for phase in [0,.05,.12,.2,.35,.55,.68,.76,.85,1]:
                    popup.setProperty('progress',phase);app.processEvents();scope['_advance'](reference,phase)
                    rect=popup.property('surfaceRect');expected=reference._surface_rect
                    assert max(abs(a-b) for a,b in zip(rect.getRect(),expected.getRect()))<1e-8,(upward,phase,rect,expected)
                    assert abs(popup.property('returnCaptionScale')-reference._caption_scale)<1e-8
                    expected_center=start+(destination.center()-start)*scope['_spring_progress'](min(1,phase/.68)) if selected else expected.center()
                    center=popup.property('returnCaptionCenter')
                    assert abs(center.x()-expected_center.x())<1e-8 and abs(center.y()-expected_center.y())<1e-8,(upward,selected,phase,center,expected_center,popup.property('captionStart'),errors)
                    blend=max(0,min(1,(phase-.68)/.17))
                    assert abs(popup.property('formAlpha')-reference._form_opacity*(1-blend))<1e-8
                checked.append({'upward':upward,'selected':selected,'samples':10})
            QMetaObject.invokeMethod(popup,'close');QTest.qWait(20)
        assert not errors,errors
        print(json.dumps({'result':'passed','reference':str(source),'springDuration':440,'buttonAndCaptionPaths':checked}),flush=True)
    finally:
        window.close();engine.deleteLater();app.processEvents()


if __name__=='__main__':main()
