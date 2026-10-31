# SPDX-License-Identifier: MPL-2.0
"""Check real GPU spinner edges against the original antialiased painter arc."""
import json
from pathlib import Path


def main():
    from PySide6.QtCore import QObject,QUrl,Qt,QRectF
    from PySide6.QtGui import QGuiApplication,QImage,QPainter,QColor,QPen
    from PySide6.QtQml import QQmlApplicationEngine
    from PySide6.QtQuick import QQuickItem
    from PySide6.QtTest import QTest
    app=QGuiApplication([]);engine=QQmlApplicationEngine();errors=[]
    engine.warnings.connect(lambda values:errors.extend(error.toString() for error in values))
    engine.load(QUrl.fromLocalFile(str(Path(__file__).with_name('qtquick_spinner_fixture.qml'))))
    window=engine.rootObjects()[0];window.show();QTest.qWait(100)
    artifacts=Path(__file__).resolve().parents[1]/'build/qtquick_migration/spinner'
    artifacts.mkdir(parents=True,exist_ok=True)
    results=[]
    try:
        for name in ('largeSpinner','mediumSpinner','smallSpinner'):
            item=window.findChild(QObject,name)
            assert item.property('curveRenderer'),'Spinner fell back to aliased triangle geometry'
            size=round(item.width());inset=item.property('inset');thickness=item.property('thickness')
            for angle in (0,45,137):
                item.setProperty('phase',angle);QTest.qWait(25)
                frame=window.grabWindow().copy(round(item.x()),round(item.y()),size,size)
                reference=QImage(size,size,QImage.Format_ARGB32_Premultiplied);reference.fill(QColor('#1a1a1a'))
                painter=QPainter(reference);painter.setRenderHint(QPainter.Antialiasing)
                rect=QRectF(inset,inset,size-2*inset,size-2*inset)
                for color,arc in ((QColor(62,207,142,46),False),(QColor('#3ecf8e'),True)):
                    pen=QPen(color);pen.setWidthF(thickness);pen.setCapStyle(Qt.RoundCap);painter.setPen(pen)
                    if arc:painter.drawArc(rect,-angle*16,105*16)
                    else:painter.drawEllipse(rect)
                painter.end()
                difference=sum(abs(frame.pixelColor(x,y).green()-reference.pixelColor(x,y).green()) for x in range(size) for y in range(size))/(size*size)
                assert difference<5,(name,angle,'raster error',difference)
                results.append({'size':size,'angle':angle,'meanGreenDifference':round(difference,3)})
                if angle==45:frame.save(str(artifacts/(name+'.png')))
        item=window.findChild(QObject,'largeSpinner');item.setProperty('spinning',True)
        phase=item.property('phase');QTest.qWait(100)
        assert abs(item.property('phase')-phase)>5,'Rotation stopped'
        assert not errors,errors
        print(json.dumps({'result':'passed','checks':['curve shader anti-aliasing','original radius, stroke and round caps','rotated edges at three sizes','continuous rotation'],'rasterComparisons':results}),flush=True)
    finally:
        window.close();engine.deleteLater();app.processEvents()


if __name__=='__main__':main()
