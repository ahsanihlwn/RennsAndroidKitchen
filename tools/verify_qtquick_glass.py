# SPDX-License-Identifier: MPL-2.0
"""Render live GPU glass and compare its shader blur with the reference."""
import json
import ast
from pathlib import Path


def main():
    from PySide6.QtCore import QObject, QUrl, Qt, QMetaObject, QRectF
    from PySide6.QtGui import QColor,QPixmap,QPainter
    from PySide6.QtWidgets import QApplication,QGraphicsScene,QGraphicsPixmapItem,QGraphicsBlurEffect
    from PySide6.QtQml import QQmlApplicationEngine
    from PySide6.QtQuick import QQuickWindow
    from PySide6.QtTest import QTest
    app=QApplication([])
    engine=QQmlApplicationEngine()
    errors=[]
    engine.warnings.connect(lambda values: errors.extend(error.toString() for error in values))
    engine.load(QUrl.fromLocalFile(str(Path(__file__).with_name('qtquick_glass_fixture.qml'))))
    window=engine.rootObjects()[0]
    window.setFlags(window.flags()|Qt.Tool|Qt.WindowDoesNotAcceptFocus)
    window.show();QTest.qWait(150)
    glass=window.findChild(QObject,'glassProbe')
    scene=window.findChild(QObject,'glassScene')
    try:
        QTest.qWait(100)
        first=window.grabWindow()
        samples=[first.pixelColor(x,160).red() for x in range(130,290)]
        # A transparent, missing, flat, or unblurred source each fails this.
        assert min(samples)>35 and max(samples)<200,(min(samples),max(samples))
        assert max(samples)-min(samples)<30,(min(samples),max(samples))
        assert first.pixelColor(101,81).red() in (16,255),'Rounded mask leaked'
        original=Path(__file__).resolve().parents[2]/'rennsandroidkitchen/app_ext/class_BlurOverlay.py'
        module=ast.parse(original.read_text(encoding='utf-8'))
        helper=next(node for node in module.body if isinstance(node,ast.FunctionDef) and node.name=='make_blur_pixmap')
        scope=dict(QPixmap=QPixmap,QPainter=QPainter,QGraphicsScene=QGraphicsScene,QGraphicsPixmapItem=QGraphicsPixmapItem,QGraphicsBlurEffect=QGraphicsBlurEffect,Qt=Qt,QRectF=QRectF)
        exec(compile(ast.Module(body=[helper],type_ignores=[]),str(original),'exec'),scope)
        raw=QPixmap(420,320);raw.fill(QColor('#101010'))
        painter=QPainter(raw)
        for index in range(21):painter.fillRect(index*20,0,10,320,QColor('white'))
        painter.end()
        reference=scope['make_blur_pixmap'](raw,18)
        painter=QPainter(reference);painter.fillRect(reference.rect(),QColor('#80202020'));painter.end()
        reference=reference.toImage()
        error=sum(abs(first.pixelColor(x,160).red()-reference.pixelColor(x,160).red()) for x in range(130,290))/160
        assert error<4,('Original blur profile differs',error)
        scene.setProperty('stripeColor',QColor('red'));QTest.qWait(100)
        updated=window.grabWindow()
        assert updated.pixelColor(180,160).green()<first.pixelColor(180,160).green()-20,'Shader did not follow the live source'
        assert glass.property('live') and glass.property('rendering')
        glass.setProperty('effectsEnabled',False);QTest.qWait(30)
        flat=window.grabWindow()
        assert flat.pixelColor(180,160)==QColor('#202020')
        assert not glass.property('rendering')
        glass.setProperty('effectsEnabled',True);QTest.qWait(100)
        assert window.grabWindow().pixelColor(180,160).green()<first.pixelColor(180,160).green()-20
        artifacts=Path(__file__).resolve().parents[1]/'build/qtquick_migration/glass'
        artifacts.mkdir(parents=True,exist_ok=True)
        first.save(str(artifacts/'live.png'));updated.save(str(artifacts/'updated.png'));flat.save(str(artifacts/'low.png'))
        assert not errors,errors
        print(json.dumps({'result':'passed','originalBlurMeanChannelDifference':error,'checks':['live GPU texture','original QualityHint blur profile','rounded mask','source changes reflected automatically','Low skips live texture','re-enable live shader']}),flush=True)
    finally:
        window.close();engine.deleteLater();app.processEvents()


if __name__=='__main__':
    main()
