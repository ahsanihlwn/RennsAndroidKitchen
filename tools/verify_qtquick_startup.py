# SPDX-License-Identifier: MPL-2.0
"""Verify first-frame presentation of the real launcher and loading window."""
import json
from pathlib import Path
import sys
import time


def main():
    repo=Path(__file__).resolve().parents[1];sys.path.insert(0,str(repo))
    from core.fonts import configure_text_rendering_env,apply_app_font
    configure_text_rendering_env()
    from PySide6.QtCore import QObject,Qt
    from PySide6.QtGui import QGuiApplication,QColor
    from PySide6.QtTest import QTest
    from ui.presentation import LauncherModel
    from ui.runtime import create_launcher
    from ui.loading import QuickLoadingWindow
    app=QGuiApplication([]);app.setQuitOnLastWindowClosed(False);apply_app_font(app)
    model=LauncherModel(palette='dark_green');model.setStartupActive(True)
    engine,window=create_launcher(model,visible=False);errors=[];loader=None
    engine.warnings.connect(lambda values:errors.extend(error.toString() for error in values))
    artifacts=repo/'build/qtquick_migration/startup';artifacts.mkdir(parents=True,exist_ok=True)
    def wait(predicate):
        deadline=time.monotonic()+6
        while not predicate():
            assert time.monotonic()<deadline,errors
            QTest.qWait(5)
    def present(target,presenter):
        assert target.opacity()==0 and not presenter.ready
        early=[]
        target.opacityChanged.connect(lambda value:early.append((value,target.property('firstFrameReady'))))
        target.show()
        assert target.opacity()==0,'Window became visible before its scene was composed'
        wait(lambda:presenter.ready)
        assert target.opacity()==1
        assert all(value==0 or ready for value,ready in early),early
    try:
        window.setFlags(window.flags()|Qt.Tool|Qt.WindowDoesNotAcceptFocus)
        present(window,engine._frame_presenter)
        for _ in range(6):
            frame=window.grabWindow()
            assert frame.pixelColor(4,4)==QColor(model.colors['BG']),'Startup clear color flashed'
            assert window.findChild(QObject,'startupSplash').property('visible')
            QTest.qWait(10)
        frame.save(str(artifacts/'first_launcher.png'))
        loader=QuickLoadingWindow(engine,model,'C:/fixture/Fixture project')
        present(loader.window,loader._presenter)
        spinner=loader.window.findChild(QObject,'loadingSpinner')
        assert spinner.property('curveRenderer'),'Actual loading window used aliased geometry'
        loader.window.grabWindow().save(str(artifacts/'first_loading.png'))
        assert not errors,errors
        print(json.dumps({'result':'passed','checks':['launcher hidden until first composed frame','saved startup background in first six frames','real loading window first-frame gate','real loading spinner CurveRenderer']}),flush=True)
    finally:
        if loader is not None:loader.close();loader.deleteLater()
        engine._dialog_stack.close();window.close();engine.deleteLater();app.processEvents()


if __name__=='__main__':main()
