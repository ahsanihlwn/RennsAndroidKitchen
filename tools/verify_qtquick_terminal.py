# SPDX-License-Identifier: MPL-2.0
"""Check standby, native keyboard focus and resize against real workspace PTYs."""
import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--palette', default='default')
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[1]
    os.chdir(repo)
    sys.path.insert(0, str(repo))
    from core.fonts import configure_text_rendering_env, apply_app_font
    configure_text_rendering_env()
    from ui.runtime import initialize_quick_runtime
    initialize_quick_runtime(webengine=True)
    from PySide6.QtCore import Qt, QTimer, QEventLoop, QMetaObject, QUrl, QCoreApplication, QEvent
    from PySide6.QtGui import QGuiApplication, QKeyEvent
    from PySide6.QtQml import QQmlComponent
    from PySide6.QtTest import QTest
    from core import ui_lang
    from main_ext.class_ThemeUi import theme
    from ui.presentation import LauncherModel
    from ui.workspace_window import QuickWorkspace
    from tools.quick_test_utils import visual

    app = QGuiApplication([])
    app.setQuitOnLastWindowClosed(False)
    apply_app_font(app, polish_widgets=False)
    theme.load()
    ui_lang.init()

    def pause(milliseconds=20):
        loop = QEventLoop()
        QTimer.singleShot(milliseconds, loop.quit)
        loop.exec()

    def wait(predicate, seconds=15):
        deadline = time.monotonic() + seconds
        while not predicate():
            assert time.monotonic() < deadline, "Terminal verification timed out"
            pause()

    with tempfile.TemporaryDirectory(prefix="rak-terminal-qa-") as temporary:
        root = Path(temporary)
        (root / "system").mkdir()
        (root / "vendor").mkdir()
        document = root / "system" / "build.prop"
        document.write_text("ro.fixture=terminal\n", encoding="utf-8")
        preferences = LauncherModel(palette=args.palette, settings={"animations": True}, profile={})
        workspace = QuickWorkspace(str(root), "system", "vendor", preferences=preferences,
                                   settings_path=str(root / "settings.json"), enable_private_services=False)
        window, session = workspace.window, workspace.session
        artifacts = repo / "build" / "qtquick_migration" / "terminal" / args.palette
        artifacts.mkdir(parents=True, exist_ok=True)
        component = QQmlComponent(workspace.engine)
        component.setData(b'''import QtQml
QtObject {
    property var view
    property string script: ""
    property string result: ""
    function evaluate() {
        result = ""
        view.runJavaScript(script, function(value) { result = JSON.stringify({value:value}) })
    }
}''', QUrl())
        evaluator = component.create()
        assert evaluator is not None, component.errors()

        def evaluate(pane, script):
            evaluator.setProperty("view", pane.property("webView"))
            evaluator.setProperty("script", script)
            QMetaObject.invokeMethod(evaluator, "evaluate")
            wait(lambda: bool(evaluator.property("result")))
            return json.loads(evaluator.property("result")).get("value")

        def pane_for(key):
            return visual(window, "workspaceTerminal_" + key)

        def snapshot(pane):
            return evaluate(pane, '''(() => {
                const buffer=term.buffer.active;
                const container=document.getElementById('terminal-container');
                const screen=term.element.querySelector('.xterm-screen').getBoundingClientRect();
                return {cols:term.cols,rows:term.rows,proposed:fitAddon.proposeDimensions(),
                    focused:document.activeElement===term.textarea,viewport:buffer.viewportY,base:buffer.baseY,
                    content:Array.from({length:buffer.length},(_,i)=>buffer.getLine(i).translateToString(true)).join('\\n'),
                    screen:[screen.width,screen.height],container:[container.clientWidth,container.clientHeight],
                    windowsPty:term.options.windowsPty,background:term.options.theme.background};
            })()''')

        def settle():
            motion = visual(window, "workspaceLayoutMotion")
            pause(80)
            wait(lambda: not motion.property("running") and not motion.property("pending"))
            pause(120)

        def check_geometry(pane, terminal):
            settle()
            state = snapshot(pane)
            actual = (terminal._manager._last_cols, terminal._manager._last_rows)
            assert actual == (state["cols"], state["rows"]), (actual, state)
            assert state["proposed"] == {"cols": state["cols"], "rows": state["rows"]}, state
            assert all(screen <= container + 1 for screen, container in zip(state["screen"], state["container"])), state
            return state

        def type_without_click(pane, terminal, marker):
            wait(lambda: pane.property("contentReady"))
            wait(lambda: pane.property("webView").property("activeFocus"))
            wait(lambda: snapshot(pane)["focused"])
            # Deliver real native key events to the window, with no terminal click.
            for char in "echo " + marker:
                QCoreApplication.sendEvent(window, QKeyEvent(QEvent.KeyPress, 0, Qt.NoModifier, char))
                QCoreApplication.sendEvent(window, QKeyEvent(QEvent.KeyRelease, 0, Qt.NoModifier, char))
            QTest.keyClick(window, Qt.Key_Return)
            try:
                wait(lambda: marker in snapshot(pane)["content"].splitlines())
            except AssertionError:
                print("INPUT_FAILURE", repr(terminal._manager.get_plain_text()), snapshot(pane), flush=True)
                raise

        try:
            wait(lambda: workspace._ready_emitted)
            workspace.reveal()
            wait(lambda: not workspace._entry_transition_active)
            wait(lambda: len(session._terminals) == 1 and session._terminals[0]["session"].bridge.is_ready)
            assert not session.terminalOpen and not session.rightOpen
            first_key = session.activeTerminal
            first_terminal = session._terminals[0]["session"]
            first_pane = pane_for(first_key)
            assert not first_pane.isVisible()
            assert not first_pane.property("webView").property("activeFocus"), "Standby terminal stole focus"

            window.requestActivate()
            wait(window.isActive)
            first_frames = []
            def capture_open():
                if first_pane.width() <= 20 or first_pane.height() <= 20:
                    return
                image = window.grabWindow()
                if image.isNull():
                    return
                index = len(first_frames)
                image.save(str(artifacts / ("first_open_%02d.png" % index)))
                first_frames.append({"ready": first_pane.property("contentReady"),
                                     "layoutSettled": first_pane.property("layoutSettled"),
                                     "opacity": first_pane.property("webView").parentItem().opacity()})
            capture = QTimer()
            capture.setInterval(16)
            capture.timeout.connect(capture_open)
            capture.start()
            session.toggleTerminal()
            settle()
            wait(lambda: first_pane.property("contentReady"))
            pause(80)
            capture.stop()
            assert first_frames and any(frame['ready'] for frame in first_frames), first_frames
            assert all(frame['opacity'] == 0 for frame in first_frames if not frame['ready']), first_frames
            assert all(frame['layoutSettled'] for frame in first_frames if frame['ready']), first_frames
            assert snapshot(first_pane)['background'] == preferences.colors['BG']
            type_without_click(first_pane, first_terminal, "QA_FIRST_INPUT")
            check_geometry(first_pane, first_terminal)

            # Long output must survive narrow/wide round trips without losing text.
            wrapped = "QA_WRAP_BEGIN_" + "0123456789" * 22 + "_QA_WRAP_END"
            first_terminal.runCommand("echo " + wrapped)
            wait(lambda: wrapped in first_terminal._manager.get_plain_text())
            sizes = [(550, 400), (1320, 820), (650, 450), (1080, 700)]
            grids = []
            for width, height in sizes:
                window.resize(width, height)
                state = check_geometry(first_pane, first_terminal)
                assert wrapped in state["content"].replace("\n", ""), state
                grids.append([state["cols"], state["rows"]])
            assert any(cols < 80 for cols, _ in grids), grids
            for index in range(20):
                window.resize(550 + index * 27, 400 + index * 9)
                pause(8)
            check_geometry(first_pane, first_terminal)

            # Editor/terminal split must support fewer than the old 15-row floor.
            session.openPath(str(document))
            window.resize(650, 400)
            state = check_geometry(first_pane, first_terminal)
            assert state["rows"] < 15, state
            session.closeTab(str(document))
            settle()

            session.toggleTerminal()
            settle()
            session.toggleTerminal()
            settle()
            assert session.activeTerminal == first_key
            type_without_click(first_pane, first_terminal, "QA_REOPEN_INPUT")

            session.addTerminal()
            second_key = session.activeTerminal
            second_terminal = session._terminals[-1]["session"]
            second_pane = pane_for(second_key)
            wait(lambda: second_terminal.bridge.is_ready)
            settle()
            type_without_click(second_pane, second_terminal, "QA_NEW_TAB_INPUT")
            session.selectTerminal(first_key)
            settle()
            type_without_click(first_pane, first_terminal, "QA_SWITCH_INPUT")

            # A tab becoming ready behind a modal must leave its input alone.
            session.showMessage("Terminal QA", "Focus must stay in this dialog")
            wait(lambda: window.property("nativeDialogActive"))
            session.addTerminal()
            third_terminal = session._terminals[-1]["session"]
            third_pane = pane_for(session.activeTerminal)
            wait(lambda: third_terminal.bridge.is_ready)
            pause(200)
            assert not third_pane.property("webView").property("activeFocus"), "Terminal stole modal focus"
            QTest.keyClick(window, Qt.Key_Escape)
            wait(lambda: not window.property("nativeDialogActive"))
            type_without_click(third_pane, third_terminal, "QA_AFTER_MODAL")
            session.selectTerminal(first_key)
            settle()

            # Resizing must preserve a reader's scrollback position.
            first_terminal.runCommand("for /l %i in (1,1,100) do @echo QA_SCROLL_%i")
            wait(lambda: "QA_SCROLL_100" in snapshot(first_pane)["content"])
            evaluate(first_pane, "term.scrollToTop(); true")
            pause(100)
            before = snapshot(first_pane)
            assert before["viewport"] == 0 and before["base"] > 0, before
            window.resize(1080, 700)
            after = check_geometry(first_pane, first_terminal)
            assert after["viewport"] < after["base"], after
            assert not workspace.diagnostics, workspace.diagnostics
            window.grabWindow().save(str(artifacts / "terminal.png"))
            report = {"result": "passed", "realPty": True, "standby": True,
                      "keyboardWithoutClick": True, "resizeGrids": grids, "rapidResize": True,
                      "shortSplit": True, "wrappedOutput": True, "scrollbackPreserved": True,
                      "tabFocus": True, "modalFocus": True, "firstPaintReady": True,
                      "palette": args.palette, "firstOpenFrames": first_frames}
            (artifacts / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
            print(json.dumps(report), flush=True)
        finally:
            from core.threader import quit_when_idle
            evaluator.deleteLater()
            workspace.closed.connect(quit_when_idle)
            workspace._shutdown_and_quit = quit_when_idle
            workspace.dispose()
            QTimer.singleShot(5000, app.quit)
            app.exec()


if __name__ == "__main__":
    main()
