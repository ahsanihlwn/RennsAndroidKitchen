# SPDX-License-Identifier: MPL-2.0
"""Render portrait masks and high-frequency minification against Qt AA references."""
import json
from pathlib import Path
import sys
import time


def main():
    repo=Path(__file__).resolve().parents[1];sys.path.insert(0,str(repo))
    from PySide6.QtCore import QObject,QRectF,QSize,QUrl,Qt
    from PySide6.QtGui import QGuiApplication,QImage,QPainter,QPainterPath,QColor
    from PySide6.QtQml import QQmlApplicationEngine
    from PySide6.QtTest import QTest
    from ui.runtime import IconProvider
    from icons.handler import icon
    app=QGuiApplication([])
    artifacts=repo/'build/qtquick_migration/portraits';artifacts.mkdir(parents=True,exist_ok=True)
    white=QImage(512,512,QImage.Format_ARGB32_Premultiplied);white.fill(Qt.white)
    stripes=QImage(512,512,QImage.Format_ARGB32_Premultiplied);stripes.fill(Qt.black)
    painter=QPainter(stripes)
    for x in range(0,512,2):painter.fillRect(x,0,1,512,Qt.white)
    painter.end();white.save(str(artifacts/'white.png'));stripes.save(str(artifacts/'stripes.png'))
    engine=QQmlApplicationEngine();engine.addImageProvider('rak',IconProvider())
    engine.setInitialProperties({'whiteSource':QUrl.fromLocalFile(str(artifacts/'white.png')),'stripeSource':QUrl.fromLocalFile(str(artifacts/'stripes.png'))})
    errors=[];engine.warnings.connect(lambda values:errors.extend(value.toString() for value in values))
    engine.load(QUrl.fromLocalFile(str(repo/'tools/qtquick_portraits_fixture.qml')))
    window=engine.rootObjects()[0];window.show();QTest.qWait(100)
    def grab(name):
        item=window.findChild(QObject,name);result=item.grabToImage();deadline=time.monotonic()+3
        while result.image().isNull():
            assert time.monotonic()<deadline;QTest.qWait(5)
        result.image().save(str(artifacts/(name+'.png')))
        return result.image()
    def circular(source,size):
        result=QImage(size,size,QImage.Format_ARGB32_Premultiplied);result.fill(Qt.transparent)
        painter=QPainter(result);painter.setRenderHints(QPainter.Antialiasing|QPainter.SmoothPixmapTransform)
        path=QPainterPath();path.addEllipse(QRectF(0,0,size,size));painter.setClipPath(path)
        painter.drawImage(0,0,source.scaled(size,size,Qt.IgnoreAspectRatio,Qt.SmoothTransformation));painter.end()
        return result
    try:
        circle=grab('circleProbe');reference=circular(white,48)
        partial=sum(0<circle.pixelColor(x,y).alpha()<255 for x in range(48) for y in range(48))
        assert partial>60,('Portrait mask lost antialiasing',partial)
        alpha_error=sum(abs(circle.pixelColor(x,y).alpha()-reference.pixelColor(x,y).alpha()) for x in range(48) for y in range(48))/48**2
        assert alpha_error<5,alpha_error
        credit=grab('creditProbe');reference=circular(QImage(icon('renns_project')),48)
        color_error=sum(abs(credit.pixelColor(x,y).green()-reference.pixelColor(x,y).green()) for x in range(6,42) for y in range(6,42))/36**2
        assert color_error<4,('Credit photo differs from smooth source scaling',color_error)
        avatar=grab('avatarProbe')
        values=[avatar.pixelColor(x,32).red() for x in range(8,56)]
        assert max(abs(value-128) for value in values)<=2,('Avatar minification aliases fine detail',min(values),max(values))
        assert not errors,errors
        print(json.dumps({'result':'passed','partialAlphaEdgePixels':partial,'circleAlphaMeanDifference':alpha_error,'creditInteriorMeanDifference':color_error,'avatarStripeRange':[min(values),max(values)]}),flush=True)
    finally:
        window.close();engine.deleteLater();app.processEvents()


if __name__=='__main__':main()
