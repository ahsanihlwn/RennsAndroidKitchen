# SPDX-License-Identifier: MPL-2.0
"""Render AgentRainbowShadow component and verify outer rainbow shadow glow."""
import json
from pathlib import Path
import sys
import time

def main():
    repo = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(repo))
    from PySide6.QtCore import QUrl
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtQml import QQmlApplicationEngine
    from PySide6.QtQuick import QQuickWindow
    from PySide6.QtTest import QTest
    from core.fonts import configure_text_rendering_env, apply_app_font

    configure_text_rendering_env()
    app = QGuiApplication([])
    apply_app_font(app, polish_widgets=False)
    engine = QQmlApplicationEngine()
    diagnostics = []
    engine.warnings.connect(lambda errors: diagnostics.extend(error.toString() for error in errors))

    qml = b'''import QtQuick
import QtQuick.Window
import "../ui/controls"

Window {
    visible: true; width: 400; height: 200; color: "#13141b"; title: "Agent rainbow shadow fixture"
    Item {
        id: container
        x: 50; y: 50; width: 300; height: 100

        AgentRainbowShadow {
            id: shadow
            objectName: "borderGlow"
            anchors.fill: parent
            radius: box.radius
        }

        Rectangle {
            id: box
            anchors.fill: parent
            radius: 18
            color: "#22232a"
            border.width: 1
            border.color: "#33ffffff"
        }
    }
}'''
    engine.loadData(qml, QUrl.fromLocalFile(str(repo / 'tools/agent_rainbow_border_fixture.qml')))
    assert engine.rootObjects(), diagnostics
    window = engine.rootObjects()[0]
    from tools.quick_test_utils import visual
    shadow = visual(window, 'borderGlow')
    artifacts = repo / 'build/agent_chat/rainbow_border'
    artifacts.mkdir(parents=True, exist_ok=True)

    def wait(predicate, seconds=3):
        deadline = time.monotonic() + seconds
        while not predicate():
            assert time.monotonic() < deadline, ('Rainbow shadow verification timed out', shadow.property('active'), shadow.opacity(), shadow.isVisible(), diagnostics)
            QTest.qWait(10)

    try:
        QTest.qWait(100)
        before = window.grabWindow()
        before.save(str(artifacts / 'inactive.png'))
        assert not shadow.isVisible()

        # Activate shadow
        shadow.setProperty('active', True)
        wait(lambda: shadow.isVisible() and 0.05 < shadow.opacity() < 0.99)
        wait(lambda: shadow.opacity() >= 0.99)
        frame = window.grabWindow()
        frame.save(str(artifacts / 'active.png'))

        # Check center is not painted by rainbow because box covers it
        center_old = before.pixelColor(200, 100)
        center_new = frame.pixelColor(200, 100)
        diff_center = sum(abs(a - b) for a, b in zip(center_old.getRgb()[:3], center_new.getRgb()[:3]))
        assert diff_center < 5, f"Center of container should remain unpainted, diff was {diff_center}"

        # Check shadow outside container (x=46, y=100) has vibrant rainbow glow
        glow_old = before.pixelColor(46, 100)
        glow_new = frame.pixelColor(46, 100)
        diff_glow = sum(abs(a - b) for a, b in zip(glow_old.getRgb()[:3], glow_new.getRgb()[:3]))
        assert diff_glow > 10, f"Outer rainbow shadow should be visible outside container, diff was {diff_glow}"

        # Check phase animation
        phase = shadow.property('phase')
        wait(lambda: shadow.property('phase') != phase)

        # Deactivate
        shadow.setProperty('active', False)
        wait(lambda: 0 < shadow.opacity() < 0.95)
        assert shadow.isVisible()
        wait(lambda: not shadow.isVisible())
        assert shadow.opacity() < 0.001

        # Check reduced motion
        shadow.setProperty('animated', False)
        shadow.setProperty('active', True)
        QTest.qWait(20)
        assert abs(shadow.opacity() - 1.0) < 0.001
        shadow.setProperty('active', False)
        assert not shadow.isVisible()

        assert not diagnostics, diagnostics
        report = {'result': 'passed', 'rainbowShadow': True, 'outerGlow': True, 'centerClean': True, 'qmlDiagnostics': diagnostics}
        (artifacts / 'report.json').write_text(json.dumps(report, indent=2))
        print(json.dumps(report))
    finally:
        window.close()
        engine.deleteLater()
        QTest.qWait(30)

if __name__ == '__main__':
    main()
