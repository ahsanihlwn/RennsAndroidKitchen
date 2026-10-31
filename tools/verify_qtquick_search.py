# SPDX-License-Identifier: MPL-2.0
"""Exercise the file-search popup with native text input and visible results."""
import json
import os
from pathlib import Path
import sys
import tempfile
import time


def main():
    repo=Path(__file__).resolve().parents[1]
    os.chdir(repo);sys.path.insert(0,str(repo))
    from core.fonts import configure_text_rendering_env,apply_app_font
    configure_text_rendering_env()
    from ui.runtime import initialize_quick_runtime
    initialize_quick_runtime(webengine=True)
    from PySide6.QtCore import Qt,QTimer,QEventLoop,QCoreApplication,QEvent
    from PySide6.QtGui import QGuiApplication,QKeyEvent
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
    def pause(ms=20):
        loop=QEventLoop();QTimer.singleShot(ms,loop.quit);loop.exec()
    def wait(predicate,seconds=12):
        end=time.monotonic()+seconds
        while not predicate():
            assert time.monotonic()<end,'File search verification timed out'
            pause()
    def type_text(text):
        for char in text:
            QCoreApplication.sendEvent(window,QKeyEvent(QEvent.KeyPress,0,Qt.NoModifier,char))
            QCoreApplication.sendEvent(window,QKeyEvent(QEvent.KeyRelease,0,Qt.NoModifier,char))

    with tempfile.TemporaryDirectory(prefix='rak-search-ui-') as temporary:
        root=Path(temporary)
        for folder in ('PORT/vendor/etc/init','PORT/system','STOCK/system'):(root/folder).mkdir(parents=True)
        match=root/'PORT/vendor/etc/init/init.memfusion.rc';match.write_text('on boot\n')
        (root/'PORT/system/memfusion.conf').write_text('enabled=1\n')
        (root/'STOCK/system/stock-only.prop').write_text('ro.fixture=stock\n')
        preferences=LauncherModel(settings={'animations':True,'effects':'low'},profile={})
        workspace=QuickWorkspace(str(root),'PORT','STOCK',preferences=preferences,settings_path=str(root/'settings.json'),
            terminal_factory=lambda cwd=None,**kwargs:TerminalSession(manager=FakePty(cwd),**kwargs),enable_private_services=False)
        window,session=workspace.window,workspace.session
        artifacts=repo/'build/qtquick_migration/search';artifacts.mkdir(parents=True,exist_ok=True)
        try:
            wait(lambda:workspace._ready_emitted)
            workspace.reveal();wait(lambda:not workspace._entry_transition_active)
            tree=visual(window,'workspaceTree0');tree.forceActiveFocus(Qt.OtherFocusReason)
            QTest.keyClick(window,Qt.Key_F,Qt.ControlModifier)
            popup=visual(window,'workspaceSearch');field=visual(window,'workspaceSearchInput')
            opening_opacity=popup.property('opacity')
            wait(lambda:popup.property('opened'))
            window.grabWindow().save(str(artifacts/'empty.png'))
            assert field.property('activeFocus'),'Search input did not receive focus'
            type_text('memfusion')
            wait(lambda:not session.search.busy and len(session.search.rows.snapshot())>=2)
            rows=session.search.rows.snapshot()
            results=visual(window,'workspaceSearchResults')
            assert field.property('text')=='memfusion',field.property('text')
            assert results.property('count')>=2,(rows,results.property('count'))
            wait(lambda:results.property('contentHeight')>0)
            assert any(row.get('path')==str(match) for row in rows),rows
            window.grabWindow().save(str(artifacts/'results.png'))

            clear=visual(window,'workspaceSearchClear')
            assert 'close_rounded' in clear.property('iconSource').toString()
            initial_palette=preferences.paletteName
            dark_tint=clear.property('tint')
            preferences.choosePalette('light')
            wait(lambda:clear.property('tint')!=dark_tint)
            pause(100)
            window.grabWindow().save(str(artifacts/'results_light.png'))
            preferences.choosePalette(initial_palette)
            wait(lambda:clear.property('tint')==dark_tint)

            # Clear and repeat through the actual field, including path search.
            clear=visual(window,'workspaceSearchClear')
            QTest.mouseClick(window,Qt.LeftButton,Qt.NoModifier,clear.mapToScene(clear.boundingRect().center()).toPoint())
            wait(lambda:field.property('text')=='' and session.search.resultCount==0)
            assert field.property('activeFocus')
            type_text('vendor/etc/init/init.memfusion')
            wait(lambda:not session.search.busy and session.search.resultCount==1)
            assert session.search.rows.snapshot()[0]['path']==str(match)
            revealed=[];session.tree.pathRevealed.connect(revealed.append)
            QTest.keyClick(window,Qt.Key_Down);QTest.keyClick(window,Qt.Key_Return)
            closing_opacity=popup.property('opacity')
            wait(lambda:not popup.property('visible') and str(match) in revealed)
            assert str(match) in session.tree._nodes

            # No-match feedback and disabled-animation settings remain usable.
            preferences.chooseSetting('animations',False)
            tree.forceActiveFocus(Qt.OtherFocusReason);QTest.keyClick(window,Qt.Key_F,Qt.ControlModifier)
            wait(lambda:popup.property('opened'))
            QTest.keyClick(window,Qt.Key_A,Qt.ControlModifier);type_text('no-such-file-987')
            wait(lambda:not session.search.busy and session.search.resultCount==0)
            assert '0 results' in visual(window,'workspaceSearchStatus').property('text')
            window.grabWindow().save(str(artifacts/'no_matches.png'))
            QTest.keyClick(window,Qt.Key_Escape);wait(lambda:not popup.property('visible'))
            assert not workspace.diagnostics,workspace.diagnostics
            report={'result':'passed','nativeKeyboardInput':True,'visibleResultCount':len(rows),'nestedFileFound':True,
                'pathSearch':True,'clearRefocusesInput':True,'keyboardReveal':True,'noMatchFeedback':True,
                'animationsDisabledSupported':True,'themeAwareRoundedSvgClear':True,
                'openingOpacity':opening_opacity,'closingOpacity':closing_opacity}
            (artifacts/'report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
            print(json.dumps(report),flush=True)
        finally:
            from core.threader import quit_when_idle
            workspace.closed.connect(quit_when_idle);workspace._shutdown_and_quit=quit_when_idle
            workspace.dispose();QTimer.singleShot(5000,app.quit);app.exec()


if __name__=='__main__':main()
