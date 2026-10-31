# SPDX-License-Identifier: MPL-2.0
"""Check that Create darkens the page outside its live-glass card."""
import json
from pathlib import Path
import sys
import time


def main():
    repo=Path(__file__).resolve().parents[1];sys.path.insert(0,str(repo))
    from core.fonts import configure_text_rendering_env,apply_app_font
    configure_text_rendering_env()
    from PySide6.QtCore import QObject,QMetaObject,Qt
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtTest import QTest
    from ui.presentation import LauncherModel
    from ui.runtime import create_launcher
    from quick_test_utils import visual
    app=QGuiApplication([]);apply_app_font(app)
    model=LauncherModel(palette='dark_green',settings={'animations':True,'effects':'medium'})
    engine,window=create_launcher(model);errors=[]
    engine.warnings.connect(lambda values:errors.extend(value.toString() for value in values))
    popup=window.findChild(QObject,'createProjectPopup')
    def wait(predicate):
        deadline=time.monotonic()+4
        while not predicate():
            assert time.monotonic()<deadline,errors;QTest.qWait(5)
    artifacts=repo/'build/qtquick_migration/create_backdrop';artifacts.mkdir(parents=True,exist_ok=True)
    try:
        QTest.qWait(100);before=window.grabWindow()
        model.presentationRequested.emit('newProject',{})
        QTest.qWait(50)
        backdrop=visual(window,'createProjectBackdrop')
        opening_opacity=backdrop.property('opacity')
        assert 0<opening_opacity<1,('Backdrop did not fade in',opening_opacity)
        wait(lambda:popup.property('opened'));after=window.grabWindow()
        assert popup.property('modal') and popup.property('dim')
        assert popup.property('backdropDim')==0,'Black backdrop is being composited into the card'
        a,b=before.pixelColor(8,100).green(),after.pixelColor(8,100).green()
        assert abs(b-round(a*(1-150/255)))<=1,(a,b)
        glass=popup.findChild(QObject,'popupGlass')
        assert glass.property('backdropDim')==0 and glass.property('rendering')
        after.save(str(artifacts/'create_open.png'))
        QMetaObject.invokeMethod(popup,'close');QTest.qWait(60)
        closing_opacity=backdrop.property('opacity')
        assert .8<closing_opacity<1,('Backdrop did not use the source InCubic fade out',closing_opacity)
        wait(lambda:not popup.property('visible'))
        QTest.qWait(30);restored=window.grabWindow()
        assert restored.pixelColor(8,100)==before.pixelColor(8,100),'Backdrop remained after close'
        assert not errors,errors
        print(json.dumps({'result':'passed','outsideGreenBefore':a,'outsideGreenAfter':b,'backdropAlpha':150,'cardBackdropDim':0,'midOpenOpacity':opening_opacity,'midCloseOpacity':closing_opacity,'pageRestored':True}),flush=True)
    finally:
        engine._dialog_stack.close();window.close();engine.deleteLater();app.processEvents()


if __name__=='__main__':main()
