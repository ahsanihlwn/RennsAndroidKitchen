# SPDX-License-Identifier: MPL-2.0
"""Native pointer/keyboard and inspector-window checks for all nine ELF tabs."""
import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
import time


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--artifacts',default='build/qtquick_migration/quick_so');parser.add_argument('--scale',type=float,default=1)
    args=parser.parse_args();os.environ['QT_SCALE_FACTOR']=str(args.scale)
    repo=Path(__file__).resolve().parents[1];os.chdir(repo);sys.path.insert(0,str(repo))
    from core.fonts import configure_text_rendering_env,apply_app_font
    configure_text_rendering_env()
    from ui.runtime import initialize_quick_runtime,IconProvider,DialogStack
    initialize_quick_runtime()
    from PySide6.QtCore import QObject,Signal,Slot,QUrl,Qt,QTimer,QEventLoop,QPoint,QMetaObject,QCoreApplication,QEvent
    from PySide6.QtGui import QKeyEvent
    from PySide6.QtQml import QQmlApplicationEngine,qmlRegisterType
    from PySide6.QtWidgets import QApplication
    from PySide6.QtTest import QTest
    from core import ui_lang
    from ui.presentation import LauncherModel
    from ui.so_viewer import SoDocument
    from tools.quick_test_utils import visual
    from tools.audit_so_viewer import fixture_bytes
    app=QApplication([]);app.setQuitOnLastWindowClosed(False);apply_app_font(app);ui_lang.init()
    class Commands(QObject):
        message=Signal(str,str)
        copied=''
        @Slot(str)
        def copyText(self,text):self.copied=text
        @Slot(str,str)
        def showMessage(self,title,text):self.message.emit(title,text)
        @Slot(str,'QVariantMap')
        def dispatch(self,*args):pass
    def wait_ms(ms):
        loop=QEventLoop();QTimer.singleShot(ms,loop.quit);loop.exec()
    def wait(predicate,seconds=15):
        deadline=time.monotonic()+seconds
        while not predicate():
            if time.monotonic()>deadline:raise AssertionError('Native SoViewer did not settle')
            wait_ms(20)
    with tempfile.TemporaryDirectory(prefix='rak-quick-so-') as temporary:
        path=Path(temporary)/'fixture.so';path.write_bytes(fixture_bytes())
        other=Path(temporary)/'other.so';other.write_bytes(fixture_bytes('other_export'))
        prefs=LauncherModel(palette='dark_green',language='id',settings={'effects':'high','animations':True},profile={})
        commands=Commands();qmlRegisterType(SoDocument,'Renns.Workspace',1,0,'SoDocument')
        engine=QQmlApplicationEngine();diagnostics=[]
        engine.warnings.connect(lambda errors:diagnostics.extend(error.toString() for error in errors))
        engine.rootContext().setContextProperty('rakPreferences',prefs)
        engine.addImageProvider('icons',IconProvider());engine.addImageProvider('rak',IconProvider())
        engine.setInitialProperties({'preferences':prefs,'commands':commands,'details':{'path':str(path),'documentPath':str(path),'title':path.name}})
        engine.load(QUrl.fromLocalFile(str(repo/'tools/qtquick_so_fixture.qml')))
        assert engine.rootObjects(),diagnostics
        window=engine.rootObjects()[0];stack=DialogStack(window)
        viewer=visual(window,'workspaceSoViewer');document=viewer.property('documentModel')
        artifacts=repo/args.artifacts;artifacts.mkdir(parents=True,exist_ok=True)
        def click(name,owner=window,point=None,double=False):
            owner.requestActivate();wait(lambda:owner.isActive());wait_ms(40);target=visual(owner,name)
            assert target.isVisible() and target.isEnabled(),name
            point=target.mapToScene(point if point is not None else target.boundingRect().center()).toPoint()
            QTest.mouseMove(owner,point);wait_ms(20)
            if double:QTest.mouseDClick(owner,Qt.LeftButton,Qt.NoModifier,point)
            else:
                QTest.mousePress(owner,Qt.LeftButton,Qt.NoModifier,point);wait_ms(25);QTest.mouseRelease(owner,Qt.LeftButton,Qt.NoModifier,point)
            wait_ms(70)
        def text(name,value,owner=window):
            click(name,owner);QTest.keyClick(owner,Qt.Key_A,Qt.ControlModifier)
            QTest.keyClick(owner,Qt.Key_Backspace)
            for char in value:
                QCoreApplication.sendEvent(owner,QKeyEvent(QEvent.KeyPress,0,Qt.NoModifier,char));QCoreApplication.sendEvent(owner,QKeyEvent(QEvent.KeyRelease,0,Qt.NoModifier,char))
        try:
            wait(lambda:not document.busy);assert document.ready and document.symbols.rowCount()==3,document.status
            for index,name in enumerate(('overview','symbols','paths','sections','segments','relocations','security','disasm','strings')):
                click('soSubTab_'+str(index));wait_ms(80);window.grabWindow().save(str(artifacts/(name+'.png')))
            click('soSubTab_1');text('soSymbolFilter','native');assert document.symbols.rowCount()==1
            text('soSymbolFilter','');click('soSymbolsTableRows',point=QPoint(170,15))
            assert document.symbols.rowCount()==3
            QTest.keyClick(window,Qt.Key_Down,Qt.ShiftModifier)
            wait_ms(50)
            assert list(visual(window,'soSymbolsTable').property('selectedRows').toVariant())==[0,1]
            QTest.keyClick(window,Qt.Key_Down);wait_ms(50);assert list(visual(window,'soSymbolsTable').property('selectedRows').toVariant())==[2]
            click('soSymbolKind');kind_menu=visual(window,'choicePopup_soSymbolKind');wait(lambda:kind_menu.property('opened'))
            assert kind_menu.property('morphOnClose')
            QTest.keyClick(window,Qt.Key_Down);QTest.keyClick(window,Qt.Key_Return);wait(lambda:not kind_menu.property('visible'))
            assert document.symbols.rowCount()==1 and document.symbols.snapshot()[0]['cells'][0]=='export'
            click('soMangled');assert len(document.symbols.snapshot()[0]['cells'])==5
            click('soSubTab_2');text('soPathFilter','libnative.so');assert document.paths.rowCount()==3
            click('soPathTree',point=QPoint(150,65),double=True)
            path_dialog=visual(window,'soPathDialog');wait(lambda:path_dialog.isVisible())
            wait_ms(150);path_dialog.grabWindow().save(str(artifacts/'path_dialog.png'))
            click('soCopyPath',path_dialog);assert commands.copied=='system/lib64/libnative.so'
            click('soPathRawStrings',path_dialog);wait_ms(100)
            assert viewer.property('currentTab')==8 and '[path?] /system/lib64/libnative.so' in document.stringsText
            click('soSubTab_2');text('soPathFilter','PNG image');assert document.paths.rowCount()==2
            click('soPathTree',point=QPoint(180,39),double=True)
            embedded=visual(window,'soEmbeddedDialog');wait(lambda:embedded.isVisible())
            assert document.embeddedInfo['image'].startswith('data:')
            embedded.grabWindow().save(str(artifacts/'embedded_image.png'))
            saved=Path(temporary)/'image.png';document.saveEmbedded(str(saved));wait(lambda:saved.exists() and not document.busy)
            assert saved.read_bytes().startswith(b'\x89PNG')
            click('launcherMessageAccept',embedded);wait_ms(180);click('soEmbeddedClose',embedded)
            text('soPathFilter','Nested ELF');click('soPathTree',point=QPoint(180,39),double=True);wait(lambda:embedded.isVisible())
            click('soAnalyzeNested',embedded);nested=visual(window,'soNestedDialog');wait(lambda:nested.isVisible())
            assert 'nested_export' in nested.property('analysisText');nested.grabWindow().save(str(artifacts/'nested.png'))
            click('soNestedClose',nested);click('soEmbeddedClose',embedded)
            click('soSubTab_3');click('soSectionsTableRows',point=QPoint(160,45),double=True);wait(lambda:embedded.isVisible())
            assert document.embeddedInfo['title']=='Section .text';click('soEmbeddedClose',embedded)
            click('soSubTab_5');text('soRelocFilter','stack_chk');assert document.relocations.rowCount()==1
            click('soSubTab_7');click('soDisassemble');wait(lambda:not document.busy);assert document.disassembly
            window.grabWindow().save(str(artifacts/'disassembled.png'))
            click('soExportJson');export_dialog=visual(window,'soExportDialog');wait(lambda:export_dialog.property('visible'));QMetaObject.invokeMethod(export_dialog,'reject');wait_ms(100)
            export=Path(temporary)/'analysis.json';document.exportJson(str(export));wait(lambda:export.exists() and not document.busy)
            data=json.loads(export.read_text());assert data['symbols']['total']==3 and len(data['embedded_files'])==2
            click('launcherMessageAccept');wait_ms(180)
            click('soCompare');compare_file=visual(window,'soCompareFileDialog');wait(lambda:compare_file.property('visible'));QMetaObject.invokeMethod(compare_file,'reject');wait_ms(100)
            document.compare(str(other));compare_dialog=visual(window,'soCompareDialog');wait(lambda:compare_dialog.isVisible())
            assert document.comparison['only_in_a']==['native_export'] and document.comparison['only_in_b']==['other_export']
            wait_ms(150);compare_dialog.grabWindow().save(str(artifacts/'compare.png'));click('soCompareClose',compare_dialog)
            click('soReload');wait(lambda:not document.busy);assert document.ready and document.disassembly==''
            # Validate the new workbench against a fixture adapter, keeping the
            # unavailable real Ghidra runtime distinct from this UI check.
            from unittest.mock import patch
            from core import binary_session
            click('soSubTab_9');wait_ms(100)
            window.grabWindow().save(str(artifacts/'functions.png'))
            def analyze(session,address=None):
                if address is None:return {'functions':[{'address':'1000','name':'native_export','size':6}]}
                return {'function':{'address':'1000','name':'native_export','signature':'int native_export(void)'},'pseudocode':'int native_export(void) { return 42; }',
                        'instructions':[{'address':'1000','text':'RET'}],'references':[],'callers':[],'callees':[]}
            with patch.object(binary_session,'configure_ghidra',return_value={'available':True,'message':'Fixture adapter'}), \
                 patch.object(binary_session.BinarySession,'analyze',analyze):
                document.configureGhidra('fixture');wait(lambda:not document.busy)
                click('binaryAnalyzeGhidra');wait(lambda:not document.busy)
                assert document.functions.rowCount()==1
                click('binaryFunctionsTableRows',point=QPoint(80,15),double=True);wait(lambda:not document.busy)
                assert 'return 42' in document.functionDetail['pseudocode']
                window.grabWindow().save(str(artifacts/'function_pseudocode.png'))
            window.setWidth(758);wait_ms(100);click('soSubTab_8');window.grabWindow().save(str(artifacts/'narrow.png'))
            assert not diagnostics,diagnostics
            assert not QApplication.allWidgets()
            report={'result':'passed','nineTabs':True,'binaryFunctionsFixture':True,'realGhidraRuntime':False,'symbolFiltersAndMangled':True,'keyboardRowSelection':True,'pathsAndRawStrings':True,'imageSaveAndNestedInspection':True,'sectionDoubleClick':True,'disassembly':True,'exportCompareReload':True,'qwidgetCount':0,'devicePixelRatio':window.devicePixelRatio(),'graphicsApi':window.rendererInterface().graphicsApi().name}
            (artifacts/'report.json').write_text(json.dumps(report,indent=2),encoding='utf-8');print(json.dumps(report),flush=True)
        except Exception:
            print('DIAGNOSTICS',diagnostics,flush=True);window.grabWindow().save(str(artifacts/'failure.png'));raise
        finally:
            document.close();stack.close();window.close();engine.deleteLater();wait_ms(150)


if __name__=='__main__':main()
