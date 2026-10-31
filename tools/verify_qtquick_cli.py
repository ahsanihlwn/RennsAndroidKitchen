# SPDX-License-Identifier: MPL-2.0
"""Run real CLI subprocesses over private IPC against a live Qt Quick workspace."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from unittest.mock import patch


def main():
    repo = Path(__file__).resolve().parents[1]
    os.chdir(repo)
    sys.path.insert(0, str(repo))
    from core.fonts import configure_text_rendering_env, apply_app_font
    configure_text_rendering_env()
    from ui.runtime import initialize_quick_runtime
    initialize_quick_runtime(webengine=True)
    from PySide6.QtCore import Qt, QTimer, QEventLoop
    from PySide6.QtGui import QGuiApplication
    from core import rak_cli, ui_lang
    from core.proc_util import CREATE_NO_WINDOW
    from main_ext.class_ThemeUi import theme
    from main_ext.class_NeoBranchManager import NeoBranchManager
    from ui.presentation import LauncherModel
    from main import Main
    from tools.mount_fixture import build_fixture
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

    def wait(predicate, seconds=30):
        deadline = time.monotonic() + seconds
        while not predicate():
            assert time.monotonic() < deadline, 'Live CLI verification timed out'
            pause()

    with tempfile.TemporaryDirectory(prefix='rak-cli-ui-') as temporary:
        root = Path(temporary) / 'ROM QA'
        port, stock = root / 'PORT', root / 'STOCK'
        port.mkdir(parents=True)
        stock.mkdir()
        image = build_fixture(port)
        partition = image.with_suffix('')
        ipc_file = Path(temporary) / 'private-ipc.json'
        helper = Path(temporary) / 'cli.py'
        helper.write_text('import sys\nsys.path.insert(0,' + repr(str(repo)) + ')\n'
                          'from core import rak_cli\nrak_cli._ipc_file_path=lambda:' + repr(str(ipc_file)) + '\n'
                          'rak_cli._main_cli()\n', encoding='utf-8')
        commands = []

        def cli(*arguments, ok=True):
            with tempfile.TemporaryFile() as output:
                process = subprocess.Popen([sys.executable, str(helper), *map(str, arguments)],
                    cwd=port, stdout=output, stderr=subprocess.STDOUT, creationflags=CREATE_NO_WINDOW)
                try:
                    wait(lambda: process.poll() is not None, seconds=90)
                finally:
                    if process.poll() is None:
                        process.kill()
                        process.wait()
                output.seek(0)
                text = output.read().decode('utf-8', errors='replace')
            assert (process.returncode == 0) == ok, (arguments, process.returncode, text)
            assert '[RAK_CLI:GUI]' in text, ('CLI missed the live GUI', arguments, text)
            commands.append({'arguments': list(map(str, arguments)), 'ok': ok})
            print('CLI', arguments[0], process.returncode, text.strip(), flush=True)
            return text

        preferences = LauncherModel(settings={'animations': False}, profile={})
        with patch.object(rak_cli, '_ipc_file_path', return_value=str(ipc_file)), \
             patch.object(rak_cli, 'ensure_rak_bat') as register_cli:
            workspace = Main(str(root), 'PORT', 'STOCK', preferences=preferences,
                             settings_path=str(root / 'settings.json'),
                             enable_private_services=False)
            session, window = workspace.session, workspace.window
            states = []
            session.statusChanged.connect(lambda: states.append(session.statusState))
            try:
                wait(lambda: workspace._ready_emitted)
                workspace.reveal()
                wait(lambda: not workspace._entry_transition_active)
                register_cli.assert_called_once_with()
                assert rak_cli._main_ref is workspace and rak_cli._ipc_server is not None

                cli('unpack', image)
                wait(lambda: str(partition) in session.tree._nodes and not session.tree.busy)
                source = partition / 'etc' / 'native.txt'
                original = source.read_bytes()
                assert original == b'Original mounted document\n'
                assert any(row['ok'] and row['label'] == 'Unpack Ext' for row in session._history), session._history
                assert 'active' in states and 'done' in states, states
                # The generic mount fixture has no Android SELinux xattrs.
                # Supply its context metadata before testing a kitchen repack.
                contexts = port / 'config' / (partition.name + '_file_contexts')
                contexts.write_text('/' + partition.name + '(/.*)? u:object_r:system_file:s0\n', encoding='utf-8')

                cli('branch', 'create', partition.name, 'cli_branch', 'purple')
                manager = NeoBranchManager(str(port))
                assert manager.get_active_branch(partition.name)['name'] == 'cli_branch'
                wait(lambda: not session.tree.busy and session.tree._nodes[str(partition)].data(0, Qt.UserRole + 10) == 'purple')

                cli('fs', 'replace', str(source), 'Original', 'CLI')
                from core.rak_neobranch_view import ActiveProjectView
                physical, _ = ActiveProjectView(str(port)).resolve(str(source))
                assert Path(physical).read_bytes() == b'CLI mounted document\n'
                assert source.read_bytes() == original

                cli('repack', partition)
                repacked = port / (partition.name + '_new.img')
                assert repacked.is_file() and repacked.stat().st_size > 0
                from core import imgfs
                assert imgfs.read_path(str(repacked), 'etc/native.txt')[0] == b'CLI mounted document\n'
                assert source.read_bytes() == original, 'Repack did not restore the source'
                assert manager.get_active_branch(partition.name)['name'] == 'cli_branch'
                assert any(row['ok'] and row['label'] == 'Repack Ext' for row in session._history), session._history

                cli('branch', 'switch', partition.name, 'source')
                wait(lambda: not session.tree.busy and not session.tree._nodes[str(partition)].data(0, Qt.UserRole + 10))
                assert manager.get_active_branch(partition.name) is None

                cli('fs', 'touch', '.', 'cli_visible.txt')
                wait(lambda: str(port / 'cli_visible.txt') in session.tree._nodes and not session.tree.busy)
                assert (port / 'cli_visible.txt').is_file()

                # Rejected paths and failures inside queued CLI workers stay
                # in the terminal, with a failing exit status and clear cause.
                fatal = visual(window, 'workspaceFatalMessage')
                missing_output = cli('fs', 'rename', partition / 'etc/missing.txt', 'renamed.txt', ok=False)
                assert 'Path not found' in missing_output
                conflict_output = cli('fs', 'touch', '.', 'cli_visible.txt', ok=False)
                assert 'Destination already exists' in conflict_output
                pause(120)
                assert not fatal.property('visible') and not session._fatal_pending

                # An explicit kitchen action must use the same native dialog.
                cli('create_symlink', partition)
                picker = visual(window, 'workspaceSymlink')
                wait(lambda: picker.property('presented'))
                assert window.property('nativeDialogActive')
                from PySide6.QtCore import QMetaObject
                QMetaObject.invokeMethod(picker, 'dismiss')
                wait(lambda: not picker.property('presented'))

                tool_output = cli('repack_ext', port / 'mount_source', ok=False)
                assert 'mount_source_fs_config missing' in tool_output
                pause(120)
                assert not fatal.property('visible') and not session._fatal_pending
                assert not session.history[-1]['ok']

                # An actual application defect still opens Fatal Crash and its
                # complete traceback is also returned through the CLI process.
                with patch('core.workspace_files.WorkspaceFiles.rename', side_effect=TypeError('CLI_INTERNAL_APPLICATION_BUG')):
                    crash_output = cli('fs', 'rename', source, 'renamed.txt', ok=False)
                assert 'TypeError: CLI_INTERNAL_APPLICATION_BUG' in crash_output
                assert 'Traceback' in crash_output
                wait(lambda: fatal.property('opened'))
                assert len(session._fatal_active) == 1 and not session._fatal_pending
                assert not session.history[-1]['ok']
                QMetaObject.invokeMethod(fatal, 'acceptMessage')
                wait(lambda: not fatal.property('visible'))

                assert not workspace.diagnostics, workspace.diagnostics
                artifacts = repo / 'build' / 'qtquick_migration' / 'cli'
                artifacts.mkdir(parents=True, exist_ok=True)
                window.grabWindow().save(str(artifacts / 'live_cli.png'))
                report = {'result': 'passed', 'realCliProcesses': True, 'privateIpc': True,
                          'realExtUnpackRepack': True, 'activityLog': True, 'branchDotSync': True,
                          'branchSourcePreserved': True, 'fileTreeRefresh': True, 'nativeDialog': True,
                          'launcherRegisteredOnProjectOpen': True,
                          'cliOperationFailureIsTerminalOnly': True,
                          'queuedFailureExitStatus': True, 'fullErrorOutputInCli': True,
                          'applicationCrashUsesFatalDialog': True,
                          'commands': commands}
                (artifacts / 'report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
                print(json.dumps(report), flush=True)
            finally:
                from core.threader import quit_when_idle
                workspace._shutdown_and_quit = quit_when_idle
                workspace.closed.connect(quit_when_idle)
                workspace.dispose()
                QTimer.singleShot(5000, app.quit)
                app.exec()


if __name__ == '__main__':
    main()
