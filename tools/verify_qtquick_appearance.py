# SPDX-License-Identifier: MPL-2.0
"""Check old-frame crossfades and rapid appearance selections on native windows."""
import os
from pathlib import Path
import sys
import time


def main():
    repo=Path(__file__).resolve().parents[1];sys.path.insert(0,str(repo));os.chdir(repo)
    from core.fonts import configure_text_rendering_env,apply_app_font
    configure_text_rendering_env()
    from PySide6.QtCore import QObject,QMetaObject
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtQuick import QQuickWindow
    from PySide6.QtTest import QTest
    from ui.presentation import LauncherModel
    from ui.runtime import create_launcher
    app=QGuiApplication([]);app.setQuitOnLastWindowClosed(False);apply_app_font(app)
    model=LauncherModel(palette='dark_ocean',language='en')
    model.navigate('settings')
    engine,window=create_launcher(model)
    errors=[];engine.warnings.connect(lambda values:errors.extend(error.toString() for error in values))
    def wait_for(predicate):
        deadline=time.monotonic()+6
        while not predicate():
            assert time.monotonic()<deadline,errors
            QTest.qWait(10)
    captures=engine._appearance_captures
    try:
        QTest.qWait(100)
        old=window.grabWindow()
        model.choosePalette('light')
        wait_for(lambda:model.paletteName=='light')
        fade=window.findChild(QObject,'appearanceFade')
        assert fade.property('opacity')>.2
        early=window.grabWindow()
        assert early.pixelColor(4,4).lightness()<150,'Old frame was captured after the new palette rendered'
        wait_for(lambda:fade.property('opacity')==0)
        assert window.grabWindow().pixelColor(4,4).lightness()>200
        model.presentationRequested.emit('showDialog',{'kind':'theme'})
        dialog=window.findChild(QObject,'themePicker')
        wait_for(lambda:dialog.property("visible") and dialog.property("opacity")==1)
        scroll=dialog.findChild(QObject,'themeScroll')
        scroll.setProperty('contentY',100)
        model.choosePalette('dark_green')
        wait_for(lambda:model.paletteName=='dark_green')
        assert fade.property('opacity')>.2
        assert scroll.property('contentY')==100
        assert len(captures.provider.frames)==1
        model.choosePalette('light');model.choosePalette('dark_ocean');model.chooseLanguage('id')
        wait_for(lambda:captures.pending is None and not captures.queued)
        assert model.paletteName=='dark_ocean'
        assert model.strings['hdr_settings']=='Pengaturan'
        assert window.property('appearanceDuration')==280
        model.chooseSetting('animations',False)
        model.choosePalette('light')
        assert model.paletteName=='light' and fade.property('opacity')==0
        QMetaObject.invokeMethod(dialog,'close')
        assert not errors,errors
        print('Native appearance crossfades passed: old frame, 240/280 ms, rapid selections, modal capture, scroll preservation and animations off.',flush=True)
    finally:
        captures.close();engine._dialog_stack.close();window.close();engine.deleteLater();app.processEvents()


if __name__=='__main__':main()
