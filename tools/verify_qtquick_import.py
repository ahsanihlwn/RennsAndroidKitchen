# SPDX-License-Identifier: MPL-2.0
"""Exercise toolbar import, picker acceptance and the asynchronous copy/tree flow."""
import faulthandler
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import zipfile


def accept_native_file(path):
    """Send Open only to this verification process's own Windows file dialog."""
    import ctypes
    from ctypes import wintypes
    user32=ctypes.WinDLL('user32',use_last_error=True)
    callback_type=ctypes.WINFUNCTYPE(wintypes.BOOL,wintypes.HWND,wintypes.LPARAM)
    user32.GetDlgItem.argtypes=[wintypes.HWND,ctypes.c_int];user32.GetDlgItem.restype=wintypes.HWND
    user32.SendMessageTimeoutW.argtypes=[wintypes.HWND,wintypes.UINT,wintypes.WPARAM,wintypes.LPARAM,wintypes.UINT,wintypes.UINT,ctypes.c_void_p]
    user32.PostMessageW.argtypes=[wintypes.HWND,wintypes.UINT,wintypes.WPARAM,wintypes.LPARAM]
    windows=[]
    def inspect(hwnd,_):
        pid=wintypes.DWORD();user32.GetWindowThreadProcessId(hwnd,ctypes.byref(pid))
        name=ctypes.create_unicode_buffer(256);user32.GetClassNameW(hwnd,name,256)
        if pid.value==os.getpid() and name.value=='#32770' and user32.IsWindowVisible(hwnd):windows.append(hwnd)
        return True
    user32.EnumWindows(callback_type(inspect),0)
    if not windows:return False
    dialog=windows[-1];edits=[]
    def inspect_child(hwnd,_):
        name=ctypes.create_unicode_buffer(256);user32.GetClassNameW(hwnd,name,256)
        if name.value=='Edit' and user32.IsWindowVisible(hwnd):edits.append(hwnd)
        return True
    user32.EnumChildWindows(dialog,callback_type(inspect_child),0)
    assert edits,'No filename edit in this process file dialog'
    value=ctypes.create_unicode_buffer(str(path));result=ctypes.c_size_t()
    assert user32.SendMessageTimeoutW(edits[-1],0x000C,0,ctypes.addressof(value),2,2000,ctypes.byref(result))
    button=user32.GetDlgItem(dialog,1);assert button
    assert user32.PostMessageW(button,0x00F5,0,0)  # BM_CLICK, native Open button.
    return True


def main():
    repo=Path(__file__).resolve().parents[1];os.chdir(repo);sys.path.insert(0,str(repo))
    artifacts=repo/'build/qtquick_migration/import'
    if '--native-picker' in sys.argv:artifacts=artifacts/'native_reproduction'
    artifacts.mkdir(parents=True,exist_ok=True)
    crash=(artifacts/'native_crash.log').open('w',encoding='utf-8')
    faulthandler.enable(file=crash,all_threads=True)
    faulthandler.dump_traceback_later(45,file=crash,exit=True)
    from core.fonts import configure_text_rendering_env,apply_app_font
    configure_text_rendering_env()
    from ui.runtime import initialize_quick_runtime
    initialize_quick_runtime(webengine=True)
    from PySide6.QtCore import QTimer,QEventLoop,QUrl,Qt,QObject,QMetaObject
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtTest import QTest
    from core import ui_lang
    from core.terminal_pty import PtySignals
    from main_ext.class_ThemeUi import theme
    from ui.presentation import LauncherModel
    from ui.terminal import TerminalSession
    from ui.workspace_window import QuickWorkspace
    from tools.quick_test_utils import visual
    app=QGuiApplication([]);app.setQuitOnLastWindowClosed(False)
    apply_app_font(app,polish_widgets=False);theme.load();ui_lang.init()
    class FakePty:
        def __init__(self,cwd):self.cwd=cwd;self.signals=PtySignals();self.bash_mode=False
        def start(self,**_):pass
        def write(self,*_):pass
        def resize(self,*_):pass
        def shutdown(self):pass
        def set_spam_enter(self,*_):pass
    def wait(predicate,seconds=20):
        deadline=time.monotonic()+seconds
        while not predicate():
            assert time.monotonic()<deadline,'Import verification timed out'
            loop=QEventLoop();QTimer.singleShot(15,loop.quit);loop.exec()
    with tempfile.TemporaryDirectory(prefix='rak-import-ui-') as temporary:
        root=Path(temporary);port=root/'PORT';port.mkdir();(root/'STOCK').mkdir()
        incoming=root/'incoming';incoming.mkdir()
        image=incoming/'vendor sample.img'
        with image.open('wb') as output:
            output.write(b'\0'*1024+b'\xe2\xe1\xf5\xe0');output.truncate(32*1024*1024)
        text=incoming/'notes.txt';text.write_text('exact import bytes\n',encoding='utf-8')
        package=incoming/'patch.renxec.zip'
        with zipfile.ZipFile(package,'w') as archive:archive.writestr('fixture.txt','Inert import fixture\n'*1000)
        preferences=LauncherModel(settings={'animations':False,'effects':'low','treeMode':'tree'},profile={})
        workspace=QuickWorkspace(str(root),'PORT','STOCK',preferences=preferences,settings_path=str(root/'settings.json'),
            terminal_factory=lambda cwd=None,**kwargs:TerminalSession(manager=FakePty(cwd),**kwargs),enable_private_services=False)
        window,session=workspace.window,workspace.session
        ticks=[];heartbeat=QTimer();heartbeat.setInterval(20);heartbeat.timeout.connect(lambda:ticks.append(time.monotonic()));heartbeat.start()
        try:
            wait(lambda:workspace._ready_emitted);workspace.reveal();wait(lambda:not workspace._entry_transition_active)
            # Locate the declarative dialog without involving a user's desktop.
            picker=window.findChild(QObject,'workspaceImportPicker')
            assert picker is not None
            if '--native-picker' in sys.argv:picker.setProperty('options',0)
            def dialog_item(predicate):
                for candidate_window in app.topLevelWindows():
                    if not hasattr(candidate_window,'contentItem'):continue
                    pending=[candidate_window.contentItem()]
                    while pending:
                        item=pending.pop()
                        if item.isVisible() and predicate(item):return item
                        pending.extend(item.childItems())
            for source in (image,text,package):
                session.selectTree(0)
                print('OPEN_PICKER '+source.name,flush=True)
                picker.setProperty('currentFolder',QUrl.fromLocalFile(str(incoming)))
                button=visual(window,'workspaceAction_import');window.requestActivate();wait(window.isActive)
                QTest.mouseClick(window,Qt.LeftButton,Qt.NoModifier,button.mapToScene(button.boundingRect().center()).toPoint())
                wait(lambda:picker.property('visible'))
                print('ACCEPT_PICKER '+source.name,flush=True)
                before=len(session._history)
                print('DESTINATION '+session.active_tree.location+' SELECTION '+repr(session._selection),flush=True)
                if '--native-picker' in sys.argv and sys.platform=='win32':wait(lambda:accept_native_file(source))
                else:
                    def delegate():
                        return dialog_item(lambda item:item.property('fileName')==source.name)
                    wait(lambda:delegate() is not None)
                    item=delegate();dialog_window=item.window();dialog_window.requestActivate()
                    wait(dialog_window.isActive)
                    QTest.mouseDClick(dialog_window,Qt.LeftButton,Qt.NoModifier,item.mapToScene(item.boundingRect().center()).toPoint())
                wait(lambda:len(session._history)>before and not session._tasks.busy and not session.tree.busy)
                assert session._history[-1]['ok'],session._history[-1]
                destination=port/source.name
                print('COPY_RESULT '+repr(session._history[-1]),flush=True)
                assert destination.read_bytes()==source.read_bytes(),source
                wait(lambda:str(destination) in session.tree._nodes)
                assert not session._tab_data,'Import unexpectedly opened an editor'
                print('IMPORTED '+source.name,flush=True)
            # Cancelling the picker must not enqueue an empty copy or modify files.
            count=len(session._history)
            session.dispatch('import',{});wait(lambda:picker.property('visible'))
            assert QMetaObject.invokeMethod(picker,'reject')
            wait(lambda:not picker.property('visible'))
            assert len(session._history)==count and not session.transfers._jobs.busy()
            assert len(ticks)>5
            gaps=[b-a for a,b in zip(ticks,ticks[1:])]
            assert not workspace.diagnostics,workspace.diagnostics
            report={'result':'passed','toolbarPickerAccepted':True,'imageAndTextCopiedExactly':True,
                'quickPickerAvoidsWindowsShell':'--native-picker' not in sys.argv,
                'zipPackageCopiedExactly':True,'cancelDoesNotCopy':True,
                'treeRefreshed':True,'noEditorOpened':True,'heartbeatMaxGapSeconds':max(gaps,default=0),
                'qmlDiagnostics':workspace.diagnostics}
            (artifacts/'report.json').write_text(json.dumps(report,indent=2),encoding='utf-8');print(json.dumps(report),flush=True)
        finally:
            heartbeat.stop()
            from core.threader import quit_when_idle
            workspace.closed.connect(quit_when_idle);workspace._shutdown_and_quit=quit_when_idle
            workspace.dispose();QTimer.singleShot(5000,app.quit);app.exec()
            faulthandler.cancel_dump_traceback_later()


if __name__=='__main__':main()
