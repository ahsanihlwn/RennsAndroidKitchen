# SPDX-License-Identifier: MPL-2.0
"""Pointer/keyboard checks for all four native HexViewer tabs."""
import json
import os
from pathlib import Path
import sys
import tempfile
import time


def main():
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
    from ui.hex_viewer import HexDocument
    from tools.quick_test_utils import visual
    from tools.audit_hex_viewer import fixture_bytes
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
            if time.monotonic()>deadline:raise AssertionError('Native HexViewer did not settle')
            wait_ms(20)
    with tempfile.TemporaryDirectory(prefix='rak-quick-hex-') as temporary:
        path=Path(temporary)/'fixture.bin';path.write_bytes(fixture_bytes())
        prefs=LauncherModel(palette='dark_green',language='id',settings={'effects':'high','animations':True},profile={})
        commands=Commands();qmlRegisterType(HexDocument,'Renns.Workspace',1,0,'HexDocument')
        engine=QQmlApplicationEngine();diagnostics=[]
        engine.warnings.connect(lambda errors:diagnostics.extend(error.toString() for error in errors))
        engine.rootContext().setContextProperty('rakPreferences',prefs)
        engine.addImageProvider('icons',IconProvider());engine.addImageProvider('rak',IconProvider())
        engine.setInitialProperties({'preferences':prefs,'commands':commands,'details':{'path':str(path),'documentPath':str(path),'title':path.name}})
        engine.load(QUrl.fromLocalFile(str(repo/'tools/qtquick_hex_fixture.qml')))
        assert engine.rootObjects(),diagnostics
        window=engine.rootObjects()[0];stack=DialogStack(window)
        viewer=visual(window,'workspaceHexViewer');document=viewer.property('documentModel')
        artifacts=repo/'build/qtquick_migration/quick_hex';artifacts.mkdir(parents=True,exist_ok=True)
        def click(name):
            wait_ms(40);target=visual(window,name)
            assert target.isVisible() and target.isEnabled(),name
            point=target.mapToScene(target.boundingRect().center()).toPoint()
            QTest.mouseMove(window,point);wait_ms(20)
            QTest.mousePress(window,Qt.LeftButton,Qt.NoModifier,point);wait_ms(25)
            QTest.mouseRelease(window,Qt.LeftButton,Qt.NoModifier,point);wait_ms(50)
        def text(name,value):
            click(name);QTest.keyClick(window,Qt.Key_A,Qt.ControlModifier)
            for char in value:
                QCoreApplication.sendEvent(window,QKeyEvent(QEvent.KeyPress,0,Qt.NoModifier,char))
                QCoreApplication.sendEvent(window,QKeyEvent(QEvent.KeyRelease,0,Qt.NoModifier,char))
        try:
            wait(lambda:not document.busy and document.canExport)
            assert document.pageCount==5 and document.stringCount==5
            for index,name in enumerate(('overview','hex','strings','hashes')):
                click('hexSubTab_'+str(index));wait_ms(80);window.grabWindow().save(str(artifacts/(name+'.png')))
            click('hexCopyHash_sha256');assert commands.copied==document.analysis['hashes']['sha256']
            click('hexSubTab_1');click('hexNextPage');wait(lambda:not document.busy)
            assert document.page==1 and document.pageText.startswith('00001000')
            click('hexPageSize');page_menu=visual(window,'choicePopup_hexPageSize');wait(lambda:page_menu.property('opened'))
            assert page_menu.property('morphOnClose')
            QTest.keyClick(window,Qt.Key_Home);QTest.keyClick(window,Qt.Key_Return)
            wait(lambda:not page_menu.property('visible'));wait(lambda:not document.busy);assert document.pageSize==1024 and document.pageCount==18
            click('hexPageSize');wait(lambda:page_menu.property('opened'))
            QTest.keyClick(window,Qt.Key_Down);QTest.keyClick(window,Qt.Key_Down);QTest.keyClick(window,Qt.Key_Return)
            wait(lambda:not page_menu.property('visible'));wait(lambda:not document.busy);assert document.pageSize==4096
            text('hexJumpInput','0x2008');QTest.keyClick(window,Qt.Key_Return);wait(lambda:not document.busy)
            assert document.page==2
            text('hexSearchInput','BOUNDARY_PATTERN');click('hexFindNext');wait(lambda:not document.busy)
            assert document.page==3 and '0x00003FFC' in document.status
            click('hexCopyAscii');assert commands.copied.endswith('BOUN')
            click('hexCopyHex');assert commands.copied.endswith('42 4F 55 4E')
            text('hexSearchInput','SECOND_NATIVE_STRING');click('hexFindPrevious');wait(lambda:not document.busy)
            assert document.page==1 and '0x00001068' in document.status
            click('hexSubTab_2');text('hexStringFilter','second');wait(lambda:document.stringCount==1)
            click('hexCopyFiltered');assert commands.copied=='SECOND_NATIVE_STRING'
            click('hexMinimumUp');wait(lambda:not document.busy);assert document.minimumLength==5
            text('hexMinimumInput','20');QTest.keyClick(window,Qt.Key_Return)
            wait(lambda:not document.busy);assert document.minimumLength==20
            table=visual(window,'hexStringsTable')
            QTest.mouseDClick(window,Qt.LeftButton,Qt.NoModifier,table.mapToScene(QPoint(350,15)).toPoint())
            wait(lambda:viewer.property('currentTab')==1 and not document.busy)
            assert document.page==1 and '0x00001068' in document.status
            click('hexExportStrings');wait_ms(150)
            export_dialog=visual(window,'hexExportDialog')
            assert export_dialog.property('visible')
            QMetaObject.invokeMethod(export_dialog,'reject');wait_ms(100)
            export=Path(temporary)/'export.txt';document.exportStrings(str(export));wait(lambda:export.exists() and not document.busy)
            assert export.read_text().strip()=='0x00001068\t[ASCII]\tSECOND_NATIVE_STRING'
            click('launcherMessageAccept');wait_ms(180)
            click('hexReload');wait(lambda:not document.busy)
            assert not diagnostics,diagnostics
            assert not QApplication.allWidgets()
            report={'result':'passed','fourTabs':True,'pagingAndJump':True,'searchAcrossBoundary':True,'reverseSearch':True,'copyAndFilter':True,'stringDoubleClick':True,'exportAndReload':True,'qwidgetCount':len(QApplication.allWidgets()),'graphicsApi':window.rendererInterface().graphicsApi().name}
            (artifacts/'report.json').write_text(json.dumps(report,indent=2),encoding='utf-8');print(json.dumps(report),flush=True)
        except Exception:
            print('DIAGNOSTICS',diagnostics,flush=True);window.grabWindow().save(str(artifacts/'failure.png'));raise
        finally:
            document.close();stack.close();window.close();engine.deleteLater();wait_ms(100)


if __name__=='__main__':main()
