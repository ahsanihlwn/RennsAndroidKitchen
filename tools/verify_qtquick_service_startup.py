# SPDX-License-Identifier: MPL-2.0
"""Exercise production startup/prewarm and animation continuity in a fresh process."""
import faulthandler
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
from unittest.mock import patch


def main():
    repo = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(repo))
    os.chdir(repo)
    from app import AppUI, _end_startup_import_window
    from core.fonts import apply_app_font
    from core import ui_lang
    from PySide6.QtCore import QObject, Qt, QTimer, QtMsgType, qInstallMessageHandler
    from PySide6.QtWidgets import QApplication
    from PySide6.QtTest import QTest
    from ui.application import LauncherApplication, AccountState
    from ui.project_store import ProjectStore
    from tools.quick_test_utils import visual
    import main_ext.class_ThemeUi as theme_module
    import icons.handler as icons
    from core import disk_monitor

    errors = []
    def message(kind, context, text):
        if kind in (QtMsgType.QtWarningMsg, QtMsgType.QtCriticalMsg, QtMsgType.QtFatalMsg):
            errors.append(text)
            print(text, flush=True)
    previous_handler = qInstallMessageHandler(message)
    from ui.runtime import initialize_quick_runtime
    initialize_quick_runtime(webengine=True)
    app = QApplication([])
    app.setQuitOnLastWindowClosed(False)
    apply_app_font(app)
    faulthandler.dump_traceback_later(40)
    with tempfile.TemporaryDirectory(prefix='rak-quick-startup-') as temporary:
        root = Path(temporary)
        (root/'theme.json').write_text(json.dumps({'theme':'dark_green'}))
        (root/'settings.json').write_text(json.dumps({'effect_level':'high','animations_enabled':True}))
        release_warmup = threading.Event()
        release_verification = threading.Event()
        verification_started = threading.Event()
        release_disk = threading.Event()
        disk_started = threading.Event()
        original_warmup = icons.warm_icon_catalog
        def slow_warmup():
            assert release_warmup.wait(35), 'Test did not release optional editor prewarm'
            original_warmup()
        def slow_verification(*args):
            verification_started.set()
            assert release_verification.wait(35), 'Test did not release startup verification worker'
            return {'verified':False,'offline':True}
        def fixture_usage(path):
            if path == 'E:\\':
                disk_started.set()
                assert release_disk.wait(35), 'Test did not release blocked E: query'
            return SimpleNamespace(total=100*1024**3, used=50*1024**3)
        with patch.object(theme_module, '_THEME_FILE', str(root/'theme.json')), \
             patch.object(theme_module, '_SETTINGS_FILE', str(root/'settings.json')), \
             patch.object(ui_lang, '_LANG_FILE', str(root/'lang.json')), \
             patch.object(AccountState, '_start_dev_authorization_check', lambda self: None), \
             patch.object(AccountState, 'begin_restore_session', lambda self: None), \
             patch('rennsproject.core.verified.check_verified', slow_verification), \
             patch.object(icons, 'warm_icon_catalog', slow_warmup), \
             patch.object(disk_monitor, '_windows_drive_keys', return_value=['C:', 'D:', 'E:']), \
             patch.object(disk_monitor, '_VOLUME_QUERIES', {}), \
             patch.object(disk_monitor.shutil, 'disk_usage', side_effect=fixture_usage), \
             patch('core.wsl_manager.list_disk_usage', return_value=[]), \
             patch('core.wsl_manager.save_disk_usage_cache'), \
             patch('core.rak_cli.ensure_rak_bat'), patch('core.rak_cli.maybe_autostart'), \
             patch('core.project_metrics.folder_size_str',side_effect=AssertionError('Project size scanning is disabled')) as size_scan:
            store = ProjectStore(root/'metadata')
            store.set_location(str(root/'projects'))
            store.create({'name':'Startup fixture','port1':'system','port2':'','mode':1})
            started = time.monotonic()
            runtime = LauncherApplication(app, AppUI, store=store)
            runtime.accounts.account = {'id':'fixture','dev_id':'LOCAL_DEV','username':'Fixture','avatar':'000'}
            runtime.startupPrepared.connect(lambda error: _end_startup_import_window())
            preparation_errors = []
            runtime.startupPrepared.connect(preparation_errors.append)
            window = runtime.window
            glint = visual(window,'splashGlint')
            first_frame = []
            runtime.engine._frame_presenter.presented.connect(lambda: first_frame.append(time.monotonic()))
            timestamps = []
            phases = []
            pulse = QTimer()
            pulse.setInterval(20)
            def tick():
                timestamps.append(time.monotonic())
                phases.append(glint.property('phase'))
            pulse.timeout.connect(tick)
            pulse.start()
            def wait(predicate, timeout=15):
                deadline = time.monotonic()+timeout
                while not predicate():
                    assert time.monotonic()<deadline, ('startup timeout', errors,{
                        'prewarm':runtime._prewarm_complete,'disks':runtime._startup_disks_ready,
                        'verificationPending':runtime._startup_verification_pending,
                        'framePending':runtime._startup_reveal_pending,'ready':runtime._startup_ready,
                        'visible':window.isVisible(),'exposed':window.isExposed()})
                    QTest.qWait(10)
            try:
                wait(lambda: bool(first_frame) and verification_started.is_set())
                from services.process_manager import get_manager
                wait(lambda: get_manager().proc is not None and get_manager().proc.pid is not None)
                wait(lambda: get_manager().poll_ready() is not None)
                assert window.isVisible() and window.opacity()==1
                assert runtime.model.startupActive and runtime._startup_verification_pending
                assert visual(window,'startupSplash').isVisible()
                assert not runtime._prewarm_complete
                before = glint.property('phase');QTest.qWait(120)
                assert glint.property('phase')!=before, 'Held startup splash stopped animating'
                artifacts=repo/'build/qtquick_migration/startup';artifacts.mkdir(parents=True,exist_ok=True)
                window.grabWindow().save(str(artifacts/'service_ready_splash_held.png'))
                release_verification.set()
                wait(lambda:not runtime._startup_verification_pending)
                QTest.qWait(100)
                assert runtime.model.startupActive, 'Splash revealed before startup imports were ready'
                release_warmup.set()
                wait(lambda: not runtime.model.startupActive,timeout=35)
                elapsed = time.monotonic()-started
                print(json.dumps({'startup_seconds':round(elapsed,3),'ticks':len(timestamps),
                                  'first_frame_seconds':round(first_frame[0]-started,3),
                                  'max_tick_gap':round(max((b-a for a,b in zip(timestamps,timestamps[1:])),default=0),3),
                                  'glint_samples':len(set(phases))}),flush=True)
                assert window.opacity()==1 and runtime._startup_started
                assert len(set(phases))>3, 'Splash animation froze during prewarm'
                assert runtime.model.settings['animations'] is True
                runtime._reload()
                runtime.hide();runtime.show();runtime._reload()
                QTest.qWait(100)
                size_scan.assert_not_called()
                assert not any(thread.name=='rak-quick-project-sizes' for thread in threading.enumerate())
                assert all(not row['sizeLabel'] for row in runtime.model._projects)
                assert runtime._prewarm_complete and runtime._startup_disks_ready
                assert visual(window,'drive_C:').isVisible() and visual(window,'drive_D:').isVisible()
                assert disk_started.is_set() and not release_disk.is_set()
                assert [row['key'] for row in runtime._windows_usage] == ['C:', 'D:']
                window.requestActivate();wait(lambda:window.isActive())
                # Real pointer input must work immediately after the splash,
                # including while decorative page copies are still animating.
                button=visual(window,'createButton')
                QTest.mouseClick(window,Qt.LeftButton,Qt.NoModifier,button.mapToScene(button.boundingRect().center()).toPoint())
                popup=visual(window,'createProjectPopup');wait(lambda:popup.property('opened'))
                QTest.keyClick(window,Qt.Key_Escape);wait(lambda:not popup.property('visible'))
                wait(lambda: not visual(window,'pageMotion').property('active'))
                runtime.model.navigate('settings')
                QTest.qWait(90)
                clock = visual(window,'pageMotionClock')
                before = clock.property('elapsed')
                assert visual(window,'pageMotion').property('active') and before>0
                QTest.qWait(70)
                assert clock.property('elapsed')>before, 'Page clock froze during editor imports'
                wait(lambda: not visual(window,'pageMotion').property('active'))
                runtime.model.navigate('projects')
                wait(lambda: not visual(window,'pageMotion').property('active'))
                # Verify pointer -> project command -> composed live loader ->
                # editor construction. Keep this fixture out of the real Main.
                record = store.projects()[0]
                opened=[];original_construct=runtime._construct_editor
                def capture_open(record,origin,opened_at,loader):
                    opened.append(record['path']);runtime._cancel_open()
                runtime._construct_editor=capture_open
                try:
                    card=visual(window,'projectCard_'+record['path'])
                    QTest.mouseClick(window,Qt.LeftButton,Qt.NoModifier,card.mapToScene(card.boundingRect().center()).toPoint())
                    wait(lambda:bool(opened))
                    assert opened==[record['path']] and window.isVisible() and not runtime.model.busy
                finally:runtime._construct_editor=original_construct
                wait(lambda: not runtime._prewarm_thread.is_alive(),timeout=35)
                faulthandler.cancel_dump_traceback_later()
                wait(lambda: bool(preparation_errors))
                assert preparation_errors==[''], preparation_errors
                wait(lambda: get_manager().poll_ready() is not None)
                assert runtime.editor is None, 'Cancelled preparation opened an editor later'
                assert runtime.accounts.is_offline, 'Real queued account callback did not complete'
                wait(lambda: not visual(window,'pageMotion').property('active'))
                runtime.model.navigate('settings')
                QTest.qWait(90)
                assert visual(window,'pageMotion').property('active'), 'Page animation disappeared after startup'
                wait(lambda: not visual(window,'pageMotion').property('active'))
                runtime.model.navigate('projects')
                wait(lambda: not visual(window,'pageMotion').property('active'))
                runtime.handle('newProject',{})
                popup = visual(window,'createProjectPopup')
                QTest.qWait(100)
                assert 0<popup.property('progress')<1, 'Popup animation disappeared after startup'
                wait(lambda: popup.property('opened'))
                from PySide6.QtCore import QMetaObject
                QMetaObject.invokeMethod(popup,'close')
                QTest.qWait(90)
                assert popup.property('visible') and 0<popup.property('progress')<1
                wait(lambda: not popup.property('visible'))
                assert not errors,errors
                print(json.dumps({'result':'passed','checks':['production prewarm and service spawn','animated splash',
                    'first-frame presentation','splash holds account worker and startup imports',
                    'C and D visible at reveal while E remains blocked','immediate pointer Create Project and Open Project',
                    'no native-style warnings','page animation after startup',
                    'queued account verification','popup enter and exit after startup','no project size scans on startup or refresh']}),flush=True)
            finally:
                release_verification.set()
                release_warmup.set()
                release_disk.set()
                faulthandler.cancel_dump_traceback_later()
                pulse.stop()
                _end_startup_import_window()
                QTimer.singleShot(0,runtime.quit)
                app.exec()
    qInstallMessageHandler(previous_handler)


if __name__=='__main__':
    main()
