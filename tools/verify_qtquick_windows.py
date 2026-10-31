# SPDX-License-Identifier: MPL-2.0
"""Exercise in-scene launcher dialogs and live GPU glass without QWidget owners."""
import json
import os
from pathlib import Path
import sys
import time


def main():
    repo=Path(__file__).resolve().parents[1];sys.path.insert(0,str(repo));os.chdir(repo)
    from core.fonts import configure_text_rendering_env,apply_app_font
    configure_text_rendering_env()
    from PySide6.QtCore import QObject,Qt,QMetaObject
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtQuick import QQuickWindow
    from ui.presentation import LauncherModel
    from ui.runtime import create_launcher
    app=QGuiApplication([]);app.setQuitOnLastWindowClosed(False);apply_app_font(app)
    model=LauncherModel(palette='dark_ocean',language='id',settings={'animations':True,'effects':'medium'})
    engine,owner=create_launcher(model,visible=False)
    from ui.packages import PackagesModel
    fixture_rows=[{"name":"fixture_"+state,"display":"Android Tool "+str(index+1),"author":"Example","license":"GPL-3.0" if index%2 else "MIT","version":"1.0","state":state} for index,state in enumerate(("installed","not_installed","update_available","anomaly"))]
    installs=[]
    class FixturePackages:
        registry={}
        def list_packages(self):return [dict(row) for row in fixture_rows]
        def install_package(self,name):
            installs.append(name)
            next(row for row in fixture_rows if row["name"]==name)["state"]="installed"
    packages=PackagesModel(network=False,manager_factory=FixturePackages,wsl_probe=lambda:{"installed":True,"detail":"WSL 2 is installed"})
    owner.setProperty("packageBackend",packages)
    assert 'PySide6.QtWidgets' not in sys.modules
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication
    owner.setFlags(owner.flags()|Qt.Tool|Qt.WindowDoesNotAcceptFocus);owner.show();QTest.qWait(100)
    errors=[];engine.warnings.connect(lambda values:errors.extend(error.toString() for error in values))
    artifacts=repo/'build/qtquick_migration/quick_windows';artifacts.mkdir(parents=True,exist_ok=True)
    from quick_test_utils import visual,popup_frame

    def wait_for(predicate):
        deadline=time.monotonic()+8
        while not predicate():
            assert time.monotonic()<deadline,'Native dialog did not settle'
            QTest.qWait(15)
    try:
        assert len(engine._dialog_stack.dialogs)==7
        for name,size in [('license',(400,534)),('credit',(360,421)),('theme',(520,460)),('packages',(540,420))]:
            dialog=owner.findChild(QObject,'themePicker' if name=='theme' else name+'Window')
            assert dialog is not None
            model.presentationRequested.emit('showDialog',{'kind':name})
            wait_for(lambda:dialog.property("visible") and dialog.property("opacity")>0)
            assert (dialog.property("width"),dialog.property("height"))==size
            wait_for(lambda:dialog.property("opacity")==1)
            if name=='packages':wait_for(lambda:not packages.loading and not packages.running)
            frame=popup_frame(owner,dialog);assert not frame.isNull()
            glass=dialog.findChild(QObject,'dialogGlass')
            assert glass.property('live') and glass.property('rendering')
            assert frame.pixelColor(0,0)!=frame.pixelColor(20,20),'Rounded mask did not expose backdrop at its corner' 
            frame.save(str(artifacts/(name+'.png')))
            assert engine._dialog_stack.stack==[dialog]
            if name=='packages':
                action=visual(dialog,'packageInstall_fixture_not_installed')
                QTest.mouseClick(owner,Qt.LeftButton,Qt.NoModifier,action.mapToScene(action.boundingRect().center()).toPoint())
                wait_for(lambda:not packages.running and packages.rows.snapshot()[1]['state']=='installed')
                assert installs==['fixture_not_installed']
            close=dialog.findChild(QObject,'themeDone' if name=='theme' else name+'Close')
            QTest.mouseClick(owner,Qt.LeftButton,Qt.NoModifier,close.mapToScene(close.boundingRect().center()).toPoint())
            wait_for(lambda:not dialog.property("visible"))
            assert not engine._dialog_stack.stack
        model.chooseSetting('animations',False);model.chooseSetting('effects','low')
        model.presentationRequested.emit('showDialog',{'kind':'license'})
        dialog=owner.findChild(QObject,'licenseWindow');wait_for(lambda:dialog.property("visible"))
        assert dialog.property("opacity")==1
        assert not dialog.findChild(QObject,'dialogGlass').property('rendering')
        owner.resize(550,400);QTest.qWait(50)
        assert dialog.property('height')==376
        assert dialog.property('y')==12
        assert dialog.findChild(QObject,'dialogScrollBar').property('visible')
        scroll=dialog.findChild(QObject,'dialogScroll')
        scroll.setProperty('contentY',scroll.property('contentHeight')-scroll.property('height'))
        close=dialog.findChild(QObject,'licenseClose')
        y=close.mapToScene(close.boundingRect().center()).y()
        assert 12<y<388,('Small-window dialog footer clipped',y)
        owner.resize(1440,900);QTest.qWait(50)
        assert dialog.property('height')==534
        assert dialog.property('y')==183
        QTest.keyClick(owner,Qt.Key_Escape);wait_for(lambda:not dialog.property("visible"))
        assert not errors,errors
        assert not QApplication.allWidgets()
        print(json.dumps({'result':'passed','checks':['native license, credits, theme picker and packages','original window dimensions','rounded mask','live GPU shader backdrop','fade and close','Low effect','animation disable','package action job and refresh','no QWidget owners']}),flush=True)
    finally:
        packages.close();wait_for(lambda:not packages.running)
        engine._dialog_stack.close();owner.close();engine.deleteLater();app.processEvents()


if __name__=='__main__':main()
