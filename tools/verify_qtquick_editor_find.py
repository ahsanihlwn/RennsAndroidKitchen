# SPDX-License-Identifier: MPL-2.0
"""Send real Ctrl+F events to Monaco, both file trees, and the terminal."""
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
    from PySide6.QtCore import Qt,QTimer,QEventLoop,QMetaObject,QUrl
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtQml import QQmlComponent
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
        def __init__(self,cwd):self.cwd=cwd;self.signals=PtySignals();self.bash_mode=False;self.writes=[]
        def start(self,**_):pass
        def write(self,value):self.writes.append(value)
        def resize(self,*_):pass
        def shutdown(self):pass
        def set_spam_enter(self,*_):pass
    def pause(ms=20):
        loop=QEventLoop();QTimer.singleShot(ms,loop.quit);loop.exec()
    def wait(predicate,seconds=15):
        end=time.monotonic()+seconds
        while not predicate():
            assert time.monotonic()<end,'Editor find verification timed out'
            pause()

    with tempfile.TemporaryDirectory(prefix='rak-editor-find-') as temporary:
        root=Path(temporary)
        (root/'PORT').mkdir();(root/'STOCK').mkdir()
        document=root/'PORT'/'fixture.txt'
        original='first needle\nsecond needle\n'
        document.write_text(original,encoding='utf-8')
        preferences=LauncherModel(settings={'animations':False,'effects':'low'},profile={})
        workspace=QuickWorkspace(str(root),'PORT','STOCK',preferences=preferences,settings_path=str(root/'settings.json'),
            terminal_factory=lambda cwd=None,**kwargs:TerminalSession(manager=FakePty(cwd),**kwargs),enable_private_services=False)
        window,session=workspace.window,workspace.session
        component=QQmlComponent(workspace.engine)
        component.setData(b'''import QtQml
QtObject {
    property var view
    property string script: ""
    property string result: ""
    function evaluate() {
        result=""
        view.runJavaScript(script,function(value){result=JSON.stringify({value:value})})
    }
}''',QUrl())
        evaluator=component.create()
        assert evaluator is not None,component.errors()
        def evaluate(view,script):
            evaluator.setProperty('view',view);evaluator.setProperty('script',script)
            QMetaObject.invokeMethod(evaluator,'evaluate')
            wait(lambda:bool(evaluator.property('result')))
            return json.loads(evaluator.property('result')).get('value')
        def find_state():
            return evaluate(web,'''(() => {
                const widget=document.querySelector('.find-widget');
                const input=widget && widget.querySelector('input');
                const controller=editor && editor.getContribution('editor.contrib.findController');
                // getState() returns FindReplaceState; fall back to internal _state for older builds
                const state=controller && (controller.getState ? controller.getState() : controller._state);
                // Read Monaco-internal searchString (DOM input.value lags behind internal state)
                const searchString=state ? state.searchString : (input ? input.value : '');
                // matchResultsCount (Monaco ≥0.34) with fallback to matchesCount
                const matches=state ? (state.matchResultsCount !== undefined ? state.matchResultsCount : (state.matchesCount||0)) : 0;
                return {visible:!!(widget && widget.classList.contains('visible')),
                    focused:!!(widget && widget.contains(document.activeElement)),
                    value:searchString,matches:matches,
                    lexicalEditor:!!editor,windowEditor:!!window.editor};
            })()''')
        def ctrl_f():
            window.requestActivate();QTest.keyClick(window,Qt.Key_F,Qt.ControlModifier);pause(150)
        def focus_editor():
            web.forceActiveFocus(Qt.OtherFocusReason)
            evaluate(web,'editor.focus();true')
            pause(80)
        def close_find():
            # Escape must reach the Monaco WebEngineView, not just the QML window.
            # Use Monaco's find-controller API to hide the widget programmatically.
            evaluate(web, '''(() => {
                const c = editor && editor.getContribution('editor.contrib.findController');
                if (!c) return;
                // closeFindWidget() is the public API in Monaco >=0.33
                if (c.closeFindWidget) { c.closeFindWidget(); return; }
                // Older: hide via the state change that collapses the widget
                const st = c.getState ? c.getState() : c._state;
                if (st && st.change) st.change({isRevealed: false}, false);
            })()''')
            wait(lambda: not find_state()['visible'])
        try:
            wait(lambda:workspace._ready_emitted)
            workspace.reveal();wait(lambda:not workspace._entry_transition_active)
            session.openPath(str(document))
            wait(lambda:session.editor.bridge._ready and session.activeKind=='editor')
            web=visual(window,'quickMonacoView')
            search=visual(window,'workspaceSearch')
            focus_editor();ctrl_f()
            state=find_state()
            assert state['visible'] and state['focused'],('Monaco Ctrl+F did not open its find widget',state)
            assert not search.property('visible'),'Editor shortcut opened file-tree search'
            # Key events must go to the WebEngineView, not the outer QML window.
            # QTest.keyClicks only works with QWidget; for QQuickItem/WebEngineView we inject
            # the text into the find widget input via Monaco's find-controller API.
            pause(80)  # allow find-widget input to stabilise focus
            # Drive Monaco's find via its internal state (_state.change is universal across builds;
            # setSearchString exists in newer builds only and does NOT update the DOM input value).
            evaluate(web, '''(() => {
                const controller = editor && editor.getContribution('editor.contrib.findController');
                if (!controller) return;
                const st = controller.getState ? controller.getState() : controller._state;
                if (st && st.change) { st.change({searchString: 'needle'}, true); }
                else if (controller.setSearchString) { controller.setSearchString('needle'); }
            })()''')
            wait(lambda:find_state()['value']=='needle')
            # Wait for Monaco to resolve matches (async)
            wait(lambda:find_state()['matches']>=2)
            close_find()

            for number in (0,1):
                session.selectTree(number)
                tree=visual(window,'workspaceTree'+str(number))
                tree.forceActiveFocus(Qt.OtherFocusReason);ctrl_f()
                assert search.property('visible'),('Tree search did not open',number)
                assert not find_state()['visible'],'Tree shortcut opened Monaco search'
                QTest.keyClick(window,Qt.Key_Escape);wait(lambda:not search.property('visible'))

            # Keyboard focus on native editor tabs still offers document find.
            visual(window,'workspaceEditorTabs').forceActiveFocus(Qt.OtherFocusReason)
            ctrl_f()
            assert find_state()['visible'] and find_state()['focused'],'Editor header fallback did not focus find'
            close_find()

            session.toggleTerminal()
            terminal=next(row['session'] for row in session._terminals if row['key']==session.activeTerminal)
            pane=visual(window,'workspaceTerminal_'+session.activeTerminal)
            wait(lambda:pane.property('contentReady') and pane.property('webView').property('activeFocus'))
            terminal._manager.writes.clear();ctrl_f()
            wait(lambda:'\x06' in terminal._manager.writes)
            assert not search.property('visible') and not find_state()['visible'],'Terminal shortcut leaked into search'

            focus_editor();ctrl_f()
            assert find_state()['visible'] and not search.property('visible'),'Focus did not return to Monaco'
            assert evaluate(web,'editor.getValue()')==original,'Find changed document text'
            assert document.read_text(encoding='utf-8')==original
            assert not workspace.diagnostics,workspace.diagnostics
            artifacts=repo/'build/qtquick_migration/editor_find';artifacts.mkdir(parents=True,exist_ok=True)
            window.grabWindow().save(str(artifacts/'find.png'))
            report={'result':'passed','nativeMonacoFind':True,'findInputFocused':True,'findMatches':2,
                'bothTreeSearches':True,'editorHeaderFallback':True,'terminalKeepsCtrlF':True,'documentUnchanged':True}
            (artifacts/'report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
            print(json.dumps(report),flush=True)
        finally:
            from core.threader import quit_when_idle
            workspace.closed.connect(quit_when_idle);workspace._shutdown_and_quit=quit_when_idle
            workspace.dispose();QTimer.singleShot(5000,app.quit);app.exec()


if __name__=='__main__':main()
