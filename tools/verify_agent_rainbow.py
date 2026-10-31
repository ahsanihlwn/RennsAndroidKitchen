# SPDX-License-Identifier: MPL-2.0
"""Render the actual rainbow component and verify full fill and both fades."""
import json
from pathlib import Path
import sys
import time


def main():
    repo=Path(__file__).resolve().parents[1];sys.path.insert(0,str(repo))
    from PySide6.QtCore import QUrl
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtQml import QQmlApplicationEngine
    from PySide6.QtQuick import QQuickWindow
    from PySide6.QtTest import QTest
    from core.fonts import configure_text_rendering_env,apply_app_font
    configure_text_rendering_env();app=QGuiApplication([]);apply_app_font(app,polish_widgets=False)
    engine=QQmlApplicationEngine();diagnostics=[]
    engine.warnings.connect(lambda errors:diagnostics.extend(error.toString() for error in errors))
    qml=b'''import QtQuick
import QtQuick.Window
import "../ui/controls"
Window {
    visible: true; width: 640; height: 190; color: "#13141b"; title: "Agent highlight fixture"
    Text { x: 24; y: 19; text: "Agent file activity"; color: "#d6d9e2"; font.family: "Poppins"; font.pixelSize: 13 }
    Column { x: 24; y: 54; width: 592; spacing: 10
        Rectangle { width: parent.width; height: 46; color: "#1a1b22"; radius: 5
            AgentRainbow { objectName: "folder"; anchors.fill: parent }
            Text { x: 16; anchors.verticalCenter: parent.verticalCenter; text: "vendor / etc"; color: "#c9cdd8"; font.family: "Poppins"; font.pixelSize: 12 }
        }
        Rectangle { width: parent.width; height: 46; color: "#1a1b22"; radius: 5
            AgentRainbow { objectName: "file"; anchors.fill: parent }
            Text { x: 16; anchors.verticalCenter: parent.verticalCenter; text: "build.prop"; color: "#c9cdd8"; font.family: "Poppins"; font.pixelSize: 12 }
        }
    }
}'''
    engine.loadData(qml,QUrl.fromLocalFile(str(repo/'tools/agent_rainbow_fixture.qml')))
    assert engine.rootObjects(),diagnostics
    window=engine.rootObjects()[0]
    from tools.quick_test_utils import visual
    folder=visual(window,'folder');file=visual(window,'file')
    artifacts=repo/'build/agent_chat/rainbow';artifacts.mkdir(parents=True,exist_ok=True)
    def wait(predicate,seconds=3):
        deadline=time.monotonic()+seconds
        while not predicate():
            assert time.monotonic()<deadline,('Rainbow verification timed out',file.property('active'),file.opacity(),file.isVisible(),window.isExposed(),diagnostics)
            QTest.qWait(10)
    try:
        QTest.qWait(100);before=window.grabWindow();before.save(str(artifacts/'inactive.png'))
        folder.setProperty('active',True);file.setProperty('active',True)
        wait(lambda:file.isVisible() and .002<file.opacity()<.099)
        assert file.isVisible()
        wait(lambda:file.opacity()>=.099)
        frame=window.grabWindow();frame.save(str(artifacts/'active.png'))
        x=round(file.mapToScene(file.boundingRect().center()).x()+150)
        top=round(file.mapToScene(file.boundingRect().topLeft()).y())
        samples=[]
        for offset in (5,23,40):
            old=before.pixelColor(x,top+offset);new=frame.pixelColor(x,top+offset)
            assert sum(abs(a-b) for a,b in zip(old.getRgb()[:3],new.getRgb()[:3]))>3,'Highlight must fill the whole row'
            samples.append(new.getRgb())
        assert samples[0]==samples[1]==samples[2],'The highlight must not become a bright underline'
        phase=file.property('phase');wait(lambda:file.property('phase')!=phase)
        file.setProperty('active',False);folder.setProperty('active',False)
        wait(lambda:0<file.opacity()<.095)
        assert file.isVisible(),'The item must stay visible while fading out'
        window.grabWindow().save(str(artifacts/'fade_out.png'))
        # Reactivating during a fade continues from its current opacity.
        opacity=file.opacity();file.setProperty('active',True)
        assert abs(file.opacity()-opacity)<.01
        wait(lambda:file.opacity()>=.099)
        file.setProperty('active',False);wait(lambda:not file.isVisible())
        assert file.opacity()<.001
        window.grabWindow().save(str(artifacts/'released.png'))
        file.setProperty('animated',False);file.setProperty('active',True);QTest.qWait(20)
        assert abs(file.opacity()-.1)<.001
        file.setProperty('active',False);assert not file.isVisible()
        assert not diagnostics,diagnostics
        report={'result':'passed','fullRowFill':True,'opacity':.1,'fadeIn':True,'fadeOut':True,'reentryDoesNotBlink':True,'movingRainbow':True,'reducedMotion':True,'qmlDiagnostics':diagnostics}
        (artifacts/'report.json').write_text(json.dumps(report,indent=2));print(json.dumps(report))
    finally:window.close();engine.deleteLater();QTest.qWait(30)


if __name__=='__main__':main()
