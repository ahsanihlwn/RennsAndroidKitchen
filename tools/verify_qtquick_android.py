# SPDX-License-Identifier: MPL-2.0
"""Exercise Android Tools with real QProcesses, confined to an inert protocol fixture."""
import json
import os
from pathlib import Path
import sys
import tempfile
import time

RAW='''(bootloader) product: Fixture Phone
(bootloader) serialno: FIXTURE
(bootloader) current-slot: b
(bootloader) unlocked: yes
(bootloader) has-slot:system: yes
(bootloader) partition-size:system_a: 0x400000
(bootloader) partition-size:system_b: 0x400000
(bootloader) is-logical:system_a: yes
(bootloader) partition-size:vendor: 0x200000
(bootloader) is-logical:vendor: yes
(bootloader) partition-size:super: 0x1000000
(bootloader) partition-type:super: raw
'''


def main():
    root=Path(__file__).resolve().parents[1];sys.path.insert(0,str(root));os.chdir(root)
    from core.fonts import configure_text_rendering_env,apply_app_font
    configure_text_rendering_env()
    from ui.runtime import initialize_quick_runtime,IconProvider,DialogStack
    initialize_quick_runtime()
    from PySide6.QtCore import QObject,Property,QUrl,QMetaObject,Q_ARG,Qt,QCoreApplication,QEvent
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtWidgets import QApplication
    from PySide6.QtQml import QQmlApplicationEngine
    from PySide6.QtTest import QTest
    from ui.presentation import LauncherModel
    from ui.android_tools import AndroidTools
    from ui.workspace_tasks import WorkspaceTasks
    from tools.quick_test_utils import visual,popup_frame
    app=QGuiApplication([]);apply_app_font(app)
    def wait(predicate,seconds=10):
        deadline=time.monotonic()+seconds
        while not predicate():
            assert time.monotonic()<deadline,'Timed out'
            QTest.qWait(20)
    with tempfile.TemporaryDirectory(prefix='rak-android-native-') as temp:
        project=Path(temp)
        for name in ('system','vendor'):
            port=project/name;port.mkdir();(port/'system.img').write_bytes(b'fixture');(port/'vendor.img').write_bytes(b'fixture')
        calls=project/'calls.jsonl'
        program=project/'device_fixture.py'
        program.write_text('import json,sys,time\nfrom pathlib import Path\nargs=sys.argv[2:]\n'
            +f'with Path({str(calls)!r}).open("a") as out:out.write(json.dumps([sys.argv[1],*args])+"\\n")\n'
            +f'if args==["getvar","all"]: print({RAW!r})\n'
            +'elif args and args[0]=="flash":\n'
            +' print("Sending sparse 1/2",flush=True);time.sleep(.12)\n'
            +' print("Writing OKAY",flush=True);time.sleep(.12)\n'
            +' print("Sending sparse 2/2",flush=True);print("Writing OKAY",flush=True)\n'
            +' if args[1]=="vendor":print("FAILED fixture rejection",flush=True);sys.exit(1)\n'
            +'else:print("OKAY")\n',encoding='utf-8')
        class Workspace(QObject):
            def __init__(self):
                super().__init__();self.project=temp;self.p1='system';self.p2='vendor'
                self._tasks=WorkspaceTasks(self);self.tools=AndroidTools(self,self)
            @Property(QObject,constant=True)
            def androidTools(self):return self.tools
        workspace=Workspace();backend=workspace.tools
        launch=backend._launch
        def fixture_launch(program_name,args,callback,**options):
            return launch(sys.executable,[str(program),Path(program_name).stem,*args],callback,**options)
        backend._launch=fixture_launch
        prefs=LauncherModel(palette='dark_green',settings={'effects':'high','animations':True})
        engine=QQmlApplicationEngine();diagnostics=[]
        engine.warnings.connect(lambda errors:diagnostics.extend(e.toString() for e in errors))
        engine.rootContext().setContextProperty('rakPreferences',prefs)
        engine.addImageProvider('icons',IconProvider())
        engine.setInitialProperties({'preferences':prefs,'workspace':workspace})
        engine.load(QUrl.fromLocalFile(str(root/'tools/qtquick_android_fixture.qml')))
        assert engine.rootObjects(),diagnostics
        window=engine.rootObjects()[0];stack=DialogStack(window)
        dialog=window.findChild(QObject,'workspaceAndroidTools')
        QMetaObject.invokeMethod(dialog,'showTools')
        def click(name):
            QTest.qWait(35);item=visual(window,name)
            QTest.mouseClick(window,Qt.LeftButton,Qt.NoModifier,item.mapToScene(item.boundingRect().center()).toPoint());QTest.qWait(50)
        try:
            wait(lambda:backend.images.rowCount()==2);QTest.qWait(350)
            click('androidRefresh');wait(lambda:backend.mode=='fastboot' and not backend.refreshing)
            assert backend.slot=='b' and backend.hasSlot and 'Fixture Phone' in backend.badge
            assert len(backend.chart['nodes'])==2
            assert not backend.queue.rowCount()
            click('androidPage1');QTest.qWait(2500)
            click('androidImage0');click('androidNode_system')
            assert backend.queue.snapshot()[0]['partition']=='system'
            # Assignments must never start flashing before Flash All.
            logged=[json.loads(line) for line in calls.read_text().splitlines()]
            assert not any('flash' in row for row in logged)
            click('androidImage1');click('androidNode_vendor')
            assert backend.queue.rowCount()==2
            click('androidFlashAll');wait(lambda:backend.failed and not backend.running)
            assert [r['state'] for r in backend.queue.snapshot()]==['done','error']
            assert [row['ok'] for row in workspace._tasks.loading.get_history()]==[True,False]
            assert 'FAILED fixture rejection' in workspace._tasks.loading.get_history()[-1]['detail']
            assert not workspace._tasks.busy
            click('androidClearQueue');assert backend.queue.rowCount()==0
            assert backend.createPartition('new','1.5 GB',True)
            assert backend.resizePartition('system_b','0x400000')
            assert not backend.resizePartition('system_b','.. MB')
            backend.deletePartition('vendor',False)
            wait(lambda:not backend._processes)
            logged=[json.loads(line) for line in calls.read_text().splitlines()]
            assert ['fastboot','create-logical-partition','new_b',str(int(1.5*1024**3))] in logged
            assert ['fastboot','resize-logical-partition','system_b','4194304'] in logged
            assert ['fastboot','delete-logical-partition','vendor'] in logged
            click('androidPage0');QTest.qWait(300)
            output=root/'build/qtquick_migration/android';output.mkdir(parents=True,exist_ok=True)
            popup_frame(window,dialog).save(str(output/'partition_utility.png'))
            click('androidPage1');QTest.qWait(300)
            popup_frame(window,dialog).save(str(output/'flash_tools.png'))
            assert not diagnostics,diagnostics
            assert not QApplication.allWidgets()
            print(json.dumps({'result':'passed','realQProcessProtocolFixture':True,'explicitFlashAll':True,'queueErrorStop':True,'slotCommands':True,'qwidgetCount':0}),flush=True)
        finally:
            backend.close();workspace._tasks.close();stack.close();window.close();engine.deleteLater()
            QCoreApplication.sendPostedEvents(None,QEvent.DeferredDelete)
            QTest.qWait(100)


if __name__=='__main__':main()
