# SPDX-License-Identifier: MPL-2.0
"""Check shared widget/card masks against antialiased rounded Qt paths."""
import json
from pathlib import Path
import sys
import time


def main():
    repo=Path(__file__).resolve().parents[1];sys.path.insert(0,str(repo))
    from PySide6.QtCore import QObject,QRectF,QUrl,Qt
    from PySide6.QtGui import QGuiApplication,QImage,QPainter,QPainterPath
    from PySide6.QtQml import QQmlApplicationEngine
    from PySide6.QtQuick import QQuickWindow
    from PySide6.QtTest import QTest
    from quick_test_utils import visual
    from ui.runtime import initialize_quick_runtime
    app=QGuiApplication([]);initialize_quick_runtime();engine=QQmlApplicationEngine();errors=[]
    engine.warnings.connect(lambda values:errors.extend(value.toString() for value in values))
    engine.load(QUrl.fromLocalFile(str(repo/'tools/qtquick_corners_fixture.qml')))
    window=engine.rootObjects()[0];window.show();QTest.qWait(100)
    artifacts=repo/'build/qtquick_migration/corners';artifacts.mkdir(parents=True,exist_ok=True)
    results=[]
    try:
        for index,radius in enumerate([4,6,8,10,12,14]):
            for kind in ['rectangle','glass','button']:
                item=visual(window,kind+'_'+str(index));result=item.grabToImage()
                deadline=time.monotonic()+3
                while result.image().isNull():
                    assert time.monotonic()<deadline;QTest.qWait(5)
                image=result.image();dim=image.width();ratio=dim/item.width()
                reference=QImage(dim,dim,QImage.Format_ARGB32_Premultiplied);reference.fill(Qt.transparent)
                painter=QPainter(reference);painter.setRenderHint(QPainter.Antialiasing)
                path=QPainterPath();path.addRoundedRect(QRectF(0,0,dim,dim),radius*ratio,radius*ratio)
                painter.fillPath(path,Qt.white);painter.end()
                partial=sum(0<image.pixelColor(x,y).alpha()<255 for y in range(dim) for x in range(dim))
                error=sum(abs(image.pixelColor(x,y).alpha()-reference.pixelColor(x,y).alpha()) for y in range(dim) for x in range(dim))/dim**2
                image.save(str(artifacts/(kind+'_'+str(radius)+'.png')))
                assert partial>=radius*2,('Rounded corner lost partial alpha',kind,radius,partial)
                assert error<3.5,('Rounded corner differs from Qt path',kind,radius,error)
                results.append({'kind':kind,'radius':radius,'partialAlphaPixels':partial,'alphaMeanDifference':error})
        window.grabWindow().save(str(artifacts/'all_corners.png'))
        assert not errors,errors
        print(json.dumps({'result':'passed','devicePixelRatio':window.devicePixelRatio(),'checks':results}),flush=True)
    finally:
        window.close();engine.deleteLater();app.processEvents()


if __name__=='__main__':main()
