# SPDX-License-Identifier: MPL-2.0
"""Native partition-viewer selection and explicit extraction routing checks."""
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from unittest.mock import patch


def main():
    repo=Path(__file__).resolve().parents[1];os.chdir(repo);sys.path.insert(0,str(repo))
    from core.fonts import configure_text_rendering_env,apply_app_font
    configure_text_rendering_env()
    from ui.runtime import initialize_quick_runtime,IconProvider
    initialize_quick_runtime()
    from PySide6.QtCore import QObject,Slot,QUrl,Qt,QTimer,QEventLoop,QPoint
    from PySide6.QtQml import QQmlApplicationEngine,qmlRegisterType
    from PySide6.QtWidgets import QApplication
    from PySide6.QtTest import QTest
    from core import ui_lang
    from ui.presentation import LauncherModel
    from ui.partition_viewer import PartitionDocument
    from tools.quick_test_utils import visual
    from tools.audit_partition_viewers import payload_fixture,SUPER_PARTS
    app=QApplication([]);app.setQuitOnLastWindowClosed(False);apply_app_font(app);ui_lang.init()
    def wait_ms(ms):
        loop=QEventLoop();QTimer.singleShot(ms,loop.quit);loop.exec()
    def wait(predicate):
        deadline=time.monotonic()+10
        while not predicate():
            assert time.monotonic()<deadline,'Partition viewer did not settle';wait_ms(20)
    class Commands(QObject):
        calls=[]
        @Slot(str,str)
        def showMessage(self,*args):raise AssertionError(args)
        @Slot(str,str,'QStringList',bool)
        def extractPartitions(self,path,kind,parts,all_parts):self.calls.append((path,kind,parts,all_parts))
    qmlRegisterType(PartitionDocument,'Renns.Workspace',1,0,'PartitionDocument')
    prefs=LauncherModel(palette='dark_green',language='id',settings={'effects':'high','animations':True},profile={})
    artifacts=repo/'build/qtquick_migration/quick_partitions';artifacts.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='rak-quick-partitions-') as temporary:
        for kind in ('payload','super'):
            path=Path(temporary)/('payload.bin' if kind=='payload' else 'super.img');path.write_bytes(payload_fixture() if kind=='payload' else b'inert super metadata fixture')
            commands=Commands();commands.calls=[];diagnostics=[];engine=QQmlApplicationEngine()
            engine.warnings.connect(lambda errors:diagnostics.extend(e.toString() for e in errors))
            engine.rootContext().setContextProperty('rakPreferences',prefs);engine.addImageProvider('icons',IconProvider())
            engine.setInitialProperties({'preferences':prefs,'commands':commands,'details':{'path':str(path),'documentPath':str(path),'kind':kind}})
            with patch('ui.partition_viewer.super_info',return_value={'partitions':SUPER_PARTS,'active_slot':'A'}):
                engine.load(QUrl.fromLocalFile(str(repo/'tools/qtquick_partitions_fixture.qml')))
                assert engine.rootObjects(),diagnostics
                window=engine.rootObjects()[0];viewer=visual(window,'workspacePartitionViewer');document=viewer.property('documentModel')
                wait(lambda:not document.busy)
            window.requestActivate();wait(lambda:window.isActive());wait_ms(100)
            def click(name,row=None,modifiers=Qt.NoModifier):
                item=visual(window,name);point=item.boundingRect().center() if row is None else QPoint(100,round((row+.5)*(26 if kind=='super' else 32)))
                point=item.mapToScene(point).toPoint();QTest.mouseMove(window,point);wait_ms(20)
                QTest.mouseClick(window,Qt.LeftButton,modifiers,point);wait_ms(70)
            try:
                window.grabWindow().save(str(artifacts/(kind+'.png')))
                assert not document.selected
                if kind=='payload':
                    assert document.total==4 and not visual(window,'payloadExtractSelected').isEnabled()
                    click('payloadPartitions',0);click('payloadPartitions',2,Qt.ShiftModifier);assert document.selected==['system','vendor','product']
                    click('partitionDeselect');assert not document.selected
                    click('partitionSelectAll');assert document.selected==['system','vendor','product','boot']
                    # Move away so the selected screenshot matches the source's non-hover state.
                    QTest.mouseMove(window,QPoint(500,400));wait_ms(100);window.grabWindow().save(str(artifacts/'payload_selected.png'))
                    click('payloadExtractSelected');assert commands.calls[-1][2]==document.selected and not commands.calls[-1][3]
                else:
                    assert document.partsA==['system_a','vendor_a','product_a'] and document.partsB==['system_b','vendor_b']
                    click('superPartitionsA',0);QTest.mouseMove(window,QPoint(500,400));wait_ms(100);window.grabWindow().save(str(artifacts/'super_selected.png'))
                    click('superPartitionsB',0,Qt.ControlModifier);assert document.selected==['system_a','system_b']
                    click('superPartitionsB',1);assert document.selected==['vendor_b']
                    click('superExtractSelected');assert commands.calls[-1][2]==['vendor_b'] and not commands.calls[-1][3]
                    click('superExtractAll');assert commands.calls[-1][3]
                assert not diagnostics,diagnostics
                assert not QApplication.allWidgets()
            except Exception:
                print('DIAGNOSTICS',diagnostics);window.grabWindow().save(str(artifacts/'failure.png'));raise
            finally:document.close();window.close();engine.deleteLater();wait_ms(100)
    report={'result':'passed','payloadRangeSelectAllNone':True,'superCrossSlotControlSelection':True,'explicitExtractSelectedAndAll':True,'zeroQWidgets':True}
    (artifacts/'report.json').write_text(json.dumps(report,indent=2),encoding='utf-8');print(json.dumps(report))


if __name__=='__main__':main()
