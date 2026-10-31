# SPDX-License-Identifier: MPL-2.0
"""Measure real Quick TreeView opening and cancellation with 2,500 mixed files."""
import json
import cProfile
from contextlib import nullcontext
import os
from pathlib import Path
import sys
import tempfile
import time


def main():
    repo=Path(__file__).resolve().parents[1];os.chdir(repo);sys.path.insert(0,str(repo))
    import faulthandler
    global _hang_log
    trace_dir=repo/'build'/'qtquick_migration'/'tree';trace_dir.mkdir(parents=True,exist_ok=True)
    _hang_log=(trace_dir/'hang_trace.log').open('w',encoding='utf-8')
    faulthandler.enable(file=_hang_log,all_threads=True)
    faulthandler.dump_traceback_later(30,repeat=True,file=_hang_log)
    from ui.runtime import initialize_quick_runtime,IconProvider
    initialize_quick_runtime()
    from PySide6.QtCore import QTimer,QUrl,QCoreApplication,QEvent,QEventLoop,Qt
    from PySide6.QtGui import QGuiApplication,QImage,QColor
    from PySide6.QtQml import QQmlApplicationEngine
    from PySide6.QtTest import QTest
    from core import ui_lang
    from core.fonts import apply_app_font
    from core.native_backend import diagnostics
    from core.make_symlink import make_symlink_bytes
    from main_ext.class_ThemeUi import theme
    from ui.workspace import WorkspaceSession
    from ui.presentation import LauncherModel
    from ui.image_viewer import DocumentImages
    from tools.quick_test_utils import visual
    app=QGuiApplication([])
    app.setQuitOnLastWindowClosed(False);apply_app_font(app,polish_widgets=False);theme.load();ui_lang.init()
    def wait_ms(milliseconds):
        loop=QEventLoop();QTimer.singleShot(milliseconds,loop.quit);loop.exec()
    def wait(predicate,seconds=20):
        deadline=time.monotonic()+seconds
        while not predicate():
            assert time.monotonic()<deadline,'Tree benchmark timed out'
            wait_ms(5)
    artifacts=repo/'build'/'qtquick_migration'/'tree';artifacts.mkdir(parents=True,exist_ok=True)
    fixture=artifacts/'fixture_data'
    reuse='--reuse-fixture' in sys.argv
    if reuse:fixture.mkdir(exist_ok=True)
    with nullcontext(str(fixture)) if reuse else tempfile.TemporaryDirectory(prefix='rak-tree-stress-') as temporary:
        root=Path(temporary);base=root/'system';base.mkdir(exist_ok=True);large=base/'a_large';large.mkdir(exist_ok=True)
        (base/'target').mkdir(exist_ok=True);(base/'target'/'leaf.txt').write_text('target')
        extensions=['txt','bin','json','xml','prop','apk','img','so','md','rc','png','svg','log','dat','zip','conf']
        image=QImage(16,16,QImage.Format_ARGB32);image.fill(QColor('#34c778'))
        for number in range(2500):
            extension=extensions[number%len(extensions)];path=large/f'entry_{number:04d}.{extension}'
            if reuse and path.exists():continue
            if extension=='png':image.save(str(path))
            elif extension=='svg':path.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16"><circle cx="8" cy="8" r="6" fill="#34c778"/></svg>')
            else:path.write_bytes((b'fixture data '+bytes([number%255]))*64)
        (large/'folder_link').write_bytes(make_symlink_bytes('/target'))
        for number in range(400):
            folder=base/f'background_{number:04d}';folder.mkdir(exist_ok=True)
            if not (folder/'leaf.txt').exists():(folder/'leaf.txt').write_text('background')
        prefs=LauncherModel(palette='default',language='id',settings={'effects':'high','animations':True,'treeMode':'tree'})
        session=WorkspaceSession(str(root),'system',preferences=prefs,enable_private_services=False,settings_path=str(root/'settings.json'))
        session._preferences_changed()
        insertion_times=[];original_append=session.tree._append_rows
        def timed_append(parent,rows):
            began=time.perf_counter();original_append(parent,rows)
            insertion_times.append((began,(time.perf_counter()-began)*1000,len(rows)))
        session.tree._append_rows=timed_append
        engine=QQmlApplicationEngine();errors=[];failures=[]
        session.tree.failed.connect(failures.append)
        engine.rootContext().setContextProperty('rakPreferences',prefs)
        engine.warnings.connect(lambda items:errors.extend(item.toString() for item in items))
        engine.addImageProvider('icons',IconProvider());engine.addImageProvider('documents',DocumentImages())
        engine.setInitialProperties({'workspace':session,'preferences':prefs})
        engine.load(QUrl.fromLocalFile(str(repo/'tools'/'qtquick_tree_fixture.qml')))
        assert engine.rootObjects(),errors
        window=engine.rootObjects()[0]
        ticks=[];pulse=QTimer();pulse.setInterval(5);pulse.timeout.connect(lambda:ticks.append(time.perf_counter()));pulse.start()
        try:
            session.tree.load(str(base));wait(lambda:not session.tree.busy and session.tree._root.loaded)
            wait_ms(100);ticks.clear();wait_ms(500);tree=visual(window,'workspaceTree0')
            idle_gaps=sorted((b-a)*1000 for a,b in zip(ticks,ticks[1:]));ticks.clear()
            batches=[];session.tree.rowsInserted.connect(lambda parent,first,last:batches.append((time.perf_counter(),last-first+1)))
            try:arrow=visual(window,'treeBranchArrow:'+str(large))
            except AssertionError:raise AssertionError((errors,session.tree.count,session.tree.displayMode))
            started=time.perf_counter()
            profile=cProfile.Profile() if '--profile' in sys.argv else None
            if profile:profile.enable()
            point=arrow.mapToScene(arrow.boundingRect().center()).toPoint()
            window.requestActivate();QTest.mouseMove(window,point);wait_ms(25)
            QTest.mousePress(window,Qt.LeftButton,Qt.NoModifier,point);wait_ms(25)
            QTest.mouseRelease(window,Qt.LeftButton,Qt.NoModifier,point)
            try:wait(lambda:session.tree._nodes[str(large)].loaded)
            except AssertionError:
                node=session.tree._nodes[str(large)]
                window.grabWindow().save(str(artifacts/'failure.png'))
                raise AssertionError({'errors':errors,'failures':failures,'loading':node.loading,'children':len(node.children),
                    'point':[point.x(),point.y()],
                    'batches':len(batches),'lastBatches':[(round(at-started,2),count) for at,count in batches[-8:]],
                    'insertions':[(round(at-started,2),round(duration,2),count) for at,duration,count in insertion_times[-8:]],
                    'rendering':dict(session.tree._rendering),'aborts':list(session.tree._render_aborts),'serial':node.scan_serial})
            finally:
                if profile:profile.disable();profile.dump_stats(str(artifacts/'open.prof'))
            elapsed=time.perf_counter()-started
            assert len(session.tree._nodes[str(large)].children)==2501
            assert len(batches)>3,'Large folder was inserted in one blocking pass'
            assert max(count for _,count in batches)<=1104
            open_gaps=sorted((b-a)*1000 for a,b in zip(ticks,ticks[1:]))
            open_end=time.perf_counter()
            slow_ticks=[{'at':round(b-started,3),'gap_ms':round((b-a)*1000,2)} for a,b in zip(ticks,ticks[1:]) if (b-a)>.04]
            wait(lambda:not session.tree._thumbnails._jobs.busy())
            node=session.tree._nodes[str(large/'folder_link')]
            assert node.folder and node.data(0,Qt.UserRole+51)
            assert all(row.branch_lines is not None for row in session.tree._nodes[str(large)].children)
            wait_ms(100)
            window.grabWindow().save(str(artifacts/'mixed_files.png'))
            gaps=sorted((b-a)*1000 for a,b in zip(ticks,ticks[1:]))
            # Navigate out while a new native scan/render is in flight.
            prefs.chooseSetting('treeMode','single_dir');wait(lambda:not session.tree.busy)
            session.tree.activate(str(large));session.tree.goUp()
            wait(lambda:not session.tree.busy)
            assert session.tree.location==str(base)
            assert not any(path.startswith(str(large)+os.sep) for path in session.tree._nodes)
            assert not errors,errors
            window.grabWindow().save(str(artifacts/'navigation_cancel.png'))
            report={'result':'passed','files':2501,'open_seconds':round(elapsed,3),
                'idle_max_tick_gap_ms':round(max(idle_gaps,default=0),2),
                'open_max_tick_gap_ms':round(max(open_gaps,default=0),2),
                'open_p95_tick_gap_ms':round(open_gaps[int(len(open_gaps)*.95)] if open_gaps else 0,2),
                'slowOpenTicks':slow_ticks,
                'insertions':[{'at':round(at-started,3),'ms':round(duration,2),'rows':count} for at,duration,count in insertion_times if started<=at<=open_end],
                'first_batch_seconds':round(batches[0][0]-started,3),'batches':len(batches),
                'max_batch':max(count for _,count in batches),'ui_ticks':len(ticks),
                'max_tick_gap_ms':round(max(gaps,default=0),2),
                'p95_tick_gap_ms':round(gaps[int(len(gaps)*.95)] if gaps else 0,2),
                'cancelledNavigation':True,'backend':diagnostics(),'recursivePathMap':False}
            (artifacts/'report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
            print(json.dumps(report),flush=True)
        finally:
            faulthandler.cancel_dump_traceback_later()
            pulse.stop();session.stop()
            window.close();engine.deleteLater();session.deleteLater()
            QCoreApplication.sendPostedEvents(None,QEvent.DeferredDelete);app.processEvents()


if __name__=='__main__':main()
