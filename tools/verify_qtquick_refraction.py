# SPDX-License-Identifier: MPL-2.0
"""Render the lens port and optionally compare it to the original AGSL in Skia."""
import argparse
import json
from pathlib import Path
import re
import sys


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--reference',type=Path,help='Path to the original AndroidLiquidGlass-kmp checkout')
    args=parser.parse_args()
    repo=Path(__file__).resolve().parents[1];sys.path.insert(0,str(repo))
    from ui.runtime import initialize_quick_runtime
    initialize_quick_runtime()
    import numpy as np
    from PySide6.QtCore import QObject,QUrl,QCoreApplication,QEvent
    from PySide6.QtGui import QColor,QImage
    from PySide6.QtQml import QQmlApplicationEngine
    from PySide6.QtWidgets import QApplication
    from PySide6.QtTest import QTest
    app=QApplication([])
    engine=QQmlApplicationEngine();errors=[]
    engine.warnings.connect(lambda values:errors.extend(error.toString() for error in values))
    engine.load(QUrl.fromLocalFile(str(repo/'tools/qtquick_refraction_fixture.qml')))
    assert engine.rootObjects(),errors
    window=engine.rootObjects()[0]
    glass=window.findChild(QObject,'refractionProbe');scene=window.findChild(QObject,'refractionScene')
    out=repo/'build/qtquick_migration/refraction';out.mkdir(parents=True,exist_ok=True)
    def array(image):
        converted=image.convertToFormat(QImage.Format_RGBA8888)
        return np.frombuffer(converted.constBits(),np.uint8).reshape(converted.height(),converted.width(),4).copy()
    def frame():
        QTest.qWait(100)
        return window.grabWindow().copy(round(glass.x()),round(glass.y()),round(glass.width()),round(glass.height()))
    try:
        plain=frame();plain.save(str(out/'plain.png'))
        glass.setProperty('refractionEnabled',True)
        refracted=frame();refracted.save(str(out/'liquid.png'))
        assert glass.property('live') and glass.property('refracting')
        normal=array(plain);liquid=array(refracted)
        difference=np.abs(normal.astype(float)-liquid)
        edge_difference=float(difference[1:10,30:-30,:3].mean())
        center_difference=float(difference[35:-35,35:-35,:3].mean())
        assert edge_difference>5,('Lens did not bend the backdrop at its edge',edge_difference)
        assert center_difference<.5,('Lens warped the unaffected center',center_difference)
        assert liquid[0,0].tolist()==normal[0,0].tolist(),'Rounded mask leaked'
        report={'result':'passed','edgeMeanChannelDifference':edge_difference,
                'centerMeanChannelDifference':center_difference,'liveSource':True,
                'roundedMask':True,'fallbackSkipsRefraction':True}
        if args.reference:
            # Execute the unmodified reference shader, not a second copy of the port.
            import skia
            source=(args.reference/'backdrop/src/commonMain/kotlin/com/kyant/backdrop/internal/Shaders.kt').read_text(encoding='utf-8')
            sdf=re.search(r'private const val RoundedRectSDF = """(.*?)"""',source,re.S).group(1)
            body=re.search(r'const val RoundedRectRefractionShaderString = """(.*?)"""',source,re.S).group(1).replace('$RoundedRectSDF',sdf)
            builder=skia.RuntimeShaderBuilder(skia.RuntimeEffect.MakeForShader(body))
            width,height=plain.width(),plain.height()
            image=skia.Image.frombytes(normal,(width,height),skia.ColorType.kRGBA_8888_ColorType)
            builder.setChild('content',image.makeShader(skia.SamplingOptions(skia.FilterMode.kLinear)))
            builder.setUniform('size',skia.V2(width,height));builder.setUniform('offset',skia.V2(0,0))
            builder.setUniform('cornerRadii',skia.V4(20,20,20,20))
            builder.setUniform('refractionHeight',12.0);builder.setUniform('refractionAmount',-24.0)
            builder.setUniform('depthEffect',0.0)
            surface=skia.Surface(width,height)
            surface.getCanvas().drawPaint(skia.Paint(Shader=builder.makeShader()))
            expected=surface.makeImageSnapshot()
            expected.save(str(out/'original_agsl.png'),skia.EncodedImageFormat.kPNG)
            reference=expected.toarray(colorType=skia.ColorType.kRGBA_8888_ColorType)
            # Ignore the antialiased mask at the rounded corners.
            port_error=float(np.abs(reference[2:-2,25:-25,:3].astype(float)-liquid[2:-2,25:-25,:3]).mean())
            assert port_error<1.5,('Qt port differs from the original AGSL shader',port_error)
            report['originalAgslMeanChannelDifference']=port_error
        scene.setProperty('phase',1);updated=frame()
        assert updated!=refracted,'Lens froze its input texture'
        glass.setProperty('effectsEnabled',False);flat=frame()
        assert not glass.property('refracting') and flat.pixelColor(100,80)==QColor('#202020')
        glass.setProperty('effectsEnabled',True);assert frame()!=flat
        glass.setProperty('width',350);glass.setProperty('height',70);resized=frame()
        assert glass.property('refracting') and resized.width()==350 and resized.height()==70
        assert not errors,errors
        (out/'report.json').write_text(json.dumps(report,indent=2));print(json.dumps(report),flush=True)
    finally:
        window.close();engine.deleteLater();QCoreApplication.sendPostedEvents(None,QEvent.DeferredDelete);app.processEvents()


if __name__=='__main__':main()
