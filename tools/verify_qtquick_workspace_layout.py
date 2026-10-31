# SPDX-License-Identifier: MPL-2.0
"""Exercise actual workspace panel reflow, reversal, new tabs and splitter input."""
import json
import os
from pathlib import Path
import sys
import tempfile
import time


def main():
    repo=Path(__file__).resolve().parents[1];sys.path.insert(0,str(repo));os.chdir(repo)
    from ui.runtime import initialize_quick_runtime
    initialize_quick_runtime(webengine=True)
    from PySide6.QtCore import QTimer,QEventLoop,Qt,QPoint,QPointF
    from PySide6.QtWidgets import QApplication
    from PySide6.QtTest import QTest
    from core.fonts import apply_app_font
    from core import ui_lang
    from main_ext.class_ThemeUi import theme
    from ui.presentation import LauncherModel
    from ui.workspace_window import QuickWorkspace
    from core.terminal_pty import PtySignals
    from ui.terminal import TerminalSession
    from tools.quick_test_utils import visual
    app=QApplication([]);app.setQuitOnLastWindowClosed(False);apply_app_font(app,polish_widgets=False);theme.load();ui_lang.init()
    class FixturePty:
        def __init__(self,cwd):self.cwd=cwd;self.signals=PtySignals();self.bash_mode=False
        def start(self,**kwargs):self.signals.data_ready.emit('WORKSPACE_LAYOUT_FIXTURE\r\n')
        def resize(self,*args):pass
        def write(self,*args):pass
        def shutdown(self):pass
        def set_spam_enter(self,*args):pass
    def terminal_factory(cwd=None,**options):return TerminalSession(manager=FixturePty(cwd),**options)
    def wait_ms(ms):
        loop=QEventLoop();QTimer.singleShot(ms,loop.quit);loop.exec()
    def wait(predicate,seconds=10):
        deadline=time.monotonic()+seconds
        while not predicate():
            assert time.monotonic()<deadline,'Workspace layout timed out'
            wait_ms(10)
    with tempfile.TemporaryDirectory(prefix='rak-workspace-layout-') as temporary:
        root=Path(temporary);(root/'system').mkdir();(root/'vendor').mkdir()
        path=root/'system/build.prop';path.write_text('ro.fixture=layout\n');other=root/'system/second.txt';other.write_text('Second tab\n')
        moving=root/'system/move.txt';moving.write_text('Move after layout\n');identity=moving.stat().st_ino
        prefs=LauncherModel(palette='dark_green',settings={'effects':'high','animations':True},profile={'name':'ahsanihlwn','subtitle':'rennsyt'})
        workspace=QuickWorkspace(str(root),'system','vendor',preferences=prefs,settings_path=str(root/'settings.json'),terminal_factory=terminal_factory,enable_private_services=False)
        window=workspace.window;session=workspace.session
        frames={};artifacts=repo/'build/qtquick_migration/layout';artifacts.mkdir(parents=True,exist_ok=True)
        try:
            wait(lambda:workspace._ready_emitted,35)
            assert not session.editor.active and not session.editor.bridge._ready,'File-tree readiness waited for Monaco'
            workspace.reveal();wait(lambda:not workspace._entry_transition_active)
            window.requestActivate();wait(lambda:window.isActive())
            motion=visual(window,'workspaceLayoutMotion');toolbar=visual(window,'workspaceToolbar')
            names=('FirstTree','SecondTree','Editor','Terminal')
            panes={name:visual(window,'workspace'+name+'Motion') for name in names}
            def settled():return motion.property('initialized') and not motion.property('running') and not motion.property('pending')
            def settle():
                wait_ms(30);wait(settled)
                wait(lambda:not toolbar.property('modeAnimating'))
            def geometry(item):return [item.x(),item.y(),item.width(),item.height(),item.opacity(),item.scale()]
            def snapshot():return {name:geometry(item) for name,item in panes.items()}
            def check_scale_anchor(item):
                from PySide6.QtQuick import QQuickItem
                lower=item.property('transformOrigin')==QQuickItem.BottomLeft
                anchor=QPointF(0,item.height() if lower else 0)
                parent=item.parentItem();actual=item.mapToItem(parent,anchor)
                expected=QPointF(item.x(),item.y()+anchor.y())
                assert abs(actual.x()-expected.x())<.001 and abs(actual.y()-expected.y())<.001,(item.objectName(),actual,expected)
            def check_slots():
                for name,item in panes.items():
                    if not item.property('requestedVisible'):continue
                    target=item.property('layoutTarget');parent=item.parentItem()
                    point=target.mapToItem(parent,QPointF(0,0))
                    expected=[point.x(),point.y(),target.width(),target.height()]
                    assert all(abs(a-b)<=1 for a,b in zip(geometry(item),expected)),(name,geometry(item),expected)
                    assert item.opacity()==1 and item.scale()==1
            def transition(label,action):
                rows=[snapshot()];captured=False;anchor_errors=[]
                def sample():
                    nonlocal captured
                    rows.append(snapshot())
                    try:
                        for item in panes.values():
                            if item.opacity()>0:check_scale_anchor(item)
                    except AssertionError as error:anchor_errors.append(str(error))
                    if not captured and motion.property('running') and .35<motion.property('progress')<.90:
                        window.grabWindow().save(str(artifacts/(label+'_during.png')));captured=True
                timer=QTimer();timer.setInterval(16);timer.timeout.connect(sample);timer.start()
                action();wait(lambda:motion.property('running'));wait(lambda:not motion.property('running') and not motion.property('pending'))
                timer.stop();rows.append(snapshot());frames[label]=rows;check_slots()
                assert not anchor_errors,(label,anchor_errors)
                assert len(rows)>5,(label,len(rows))
                window.grabWindow().save(str(artifacts/(label+'_after.png')))
                print('LAYOUT',label,'frames',len(rows),'from',rows[0],'to',rows[-1],flush=True)
                return rows
            settle();check_slots();original_tree=visual(window,'workspaceTree1')
            assert session.layoutMode==2
            rows=transition('editor_open',lambda:session.openPath(str(path)))
            start=rows[0]['SecondTree'];end=rows[-1]['SecondTree']
            assert end[0]<start[0] and end[1]>start[1]
            assert any(end[0]<row['SecondTree'][0]<start[0] and start[1]<row['SecondTree'][1]<end[1] for row in rows)
            assert any(0<row['Editor'][4]<1 for row in rows)
            assert any(.96<row['Editor'][5]<1 for row in rows)
            assert all(row['Editor'][:2]==rows[-1]['Editor'][:2] for row in rows if row['Editor'][4]>0)
            assert visual(window,'workspaceTree1') is original_tree
            # Cross-tree pointer drops still reach the moved native tree.
            arrow=visual(window,'treeBranchArrow:'+str(moving))
            point=arrow.mapToScene(arrow.boundingRect().center()).toPoint()+QPoint(60,0)
            destination=original_tree.mapToScene(QPointF(90,original_tree.height()-20)).toPoint()
            panel=visual(window,'workspaceTree0').parentItem()
            QTest.mousePress(window,Qt.LeftButton,Qt.NoModifier,point)
            QTest.mouseMove(window,point+QPoint(35,0),20);wait(lambda:panel.property('dragging'))
            for step in range(1,9):
                target=QPoint(round(point.x()+(destination.x()-point.x())*step/8),round(point.y()+(destination.y()-point.y())*step/8))
                QTest.mouseMove(window,target,20)
            QTest.mouseRelease(window,Qt.LeftButton,Qt.NoModifier,destination)
            wait(lambda:not moving.exists() and (root/'vendor/move.txt').exists())
            assert (root/'vendor/move.txt').stat().st_ino==identity
            rows=transition('terminal_open',session.toggleTerminal)
            assert rows[0]['Editor'][3]>rows[-1]['Editor'][3]
            assert any(0<row['Terminal'][4]<1 for row in rows)
            assert all(row['Editor'][1]+row['Editor'][3]<=row['Terminal'][1]+1 for row in rows if row['Terminal'][4]>0)
            wait(lambda:session._terminals and session._terminals[0]['session'].bridge.is_ready)
            assert visual(window,'workspaceEditorCard').property('clip') and visual(window,'workspaceTerminalCard').property('clip')
            # New tabs animate even while the containing pane is already open.
            session.openPath(str(other));newtab=visual(window,'workspaceTab_'+str(other))
            assert newtab.opacity()<1 and newtab.scale()<1
            check_scale_anchor(newtab)
            wait(lambda:newtab.opacity()==1 and newtab.scale()==1)
            first_terminal=session.activeTerminal;session.addTerminal()
            assert session.activeTerminal!=first_terminal
            newtab=visual(window,'workspaceTab_'+session.activeTerminal)
            assert newtab.opacity()<1 and newtab.scale()<1
            check_scale_anchor(newtab)
            wait(lambda:newtab.opacity()==1 and newtab.scale()==1)
            rows=transition('terminal_close',session.toggleTerminal)
            assert any(0<row['Terminal'][4]<1 for row in rows)
            assert all(row['Editor'][1]+row['Editor'][3]<=row['Terminal'][1]+1 for row in rows if row['Terminal'][4]>0)
            session.closeTab(str(other));settle()
            rows=transition('editor_close',lambda:session.closeTab(str(path)))
            assert any(.96<row['Editor'][5]<1 for row in rows) and panes['Editor'].scale()==.96
            assert all(row['Editor'][:2]==rows[0]['Editor'][:2] for row in rows)
            assert session.layoutMode==2 and not session.rightOpen
            assert rows[-1]['SecondTree'][:4]==start[:4]
            # Reverse while the two trees are still moving; no reset to either end.
            session.openPath(str(path));wait(lambda:motion.property('running') and .10<motion.property('progress')<.65)
            before=geometry(panes['SecondTree']);session.closeTab(str(path))
            assert geometry(panes['SecondTree'])==before
            wait_ms(60);assert panes['SecondTree'].x()!=start[0]
            session.openPath(str(path));settle();check_slots()
            assert session.layoutMode==3 and panes['Editor'].opacity()==1
            # All three native splitter handles remain usable after reflow.
            gestures=[]
            for handle_name,distance,axis in (('workspaceMainDivider',140,'x'),('workspaceTreeDivider',65,'y'),('workspaceEditorDivider',45,'y')):
                if handle_name=='workspaceEditorDivider':transition('terminal_reopen',session.toggleTerminal)
                settle();handle=visual(window,handle_name)
                point=handle.mapToScene(handle.boundingRect().center()).toPoint()
                pane=panes['FirstTree'] if handle_name!='workspaceEditorDivider' else panes['Editor']
                dimension='width' if axis=='x' else 'height'
                values=[pane.property(dimension)]
                QTest.mouseMove(window,point);wait_ms(30);QTest.mousePress(window,Qt.LeftButton,Qt.NoModifier,point)
                for step in range(1,11):
                    offset=QPoint(round(distance*step/10) if axis=='x' else 0,round(distance*step/10) if axis=='y' else 0)
                    QTest.mouseMove(window,point+offset,18);wait_ms(18)
                    values.append(pane.property(dimension));assert not motion.property('running')
                QTest.mouseRelease(window,Qt.LeftButton,Qt.NoModifier,point+offset);settle();check_slots()
                assert len(set(values))>3,(handle_name,values)
                assert abs(values[-1]-values[0])>distance*.6,(handle_name,values)
                gestures.append({'handle':handle_name,'sizes':values})
            # A resize during reflow settles to the current layout immediately.
            session.toggleTerminal();wait(lambda:motion.property('running'))
            window.resize(940,640);wait_ms(50);wait(settled);check_slots()
            assert window.width()==940
            # Disabling animation mid-flight finishes at native geometry.
            session.closeTab(str(path));wait(lambda:motion.property('running'))
            prefs.chooseSetting('animations',False);wait_ms(40);wait(settled);check_slots()
            assert not session.rightOpen
            session.openPath(str(path));wait_ms(50);assert settled();check_slots()
            assert panes['Editor'].opacity()==1
            session.closeTab(str(path));settle()
            # Single-tree layout must not revive the hidden second tree.
            prefs.chooseSetting('animations',True)
            transition('layout_vertical',session.toggleLayout)
            transition('layout_single',session.toggleLayout)
            assert session.layoutMode==1 and not panes['SecondTree'].isVisible()
            transition('single_tree_terminal',session.toggleTerminal)
            assert session.layoutMode==1 and not panes['SecondTree'].isVisible()
            rows=transition('editor_above_terminal',lambda:session.openPath(str(path)))
            assert all(row['Editor'][1]+row['Editor'][3]<=row['Terminal'][1]+1 for row in rows if row['Editor'][4]>0)
            rows=transition('editor_closes_above_terminal',lambda:session.closeTab(str(path)))
            assert all(row['Editor'][1]+row['Editor'][3]<=row['Terminal'][1]+1 for row in rows if row['Editor'][4]>0)
            assert visual(window,'workspaceTree1') is original_tree
            assert not workspace.diagnostics,workspace.diagnostics
            assert not QApplication.allWidgets()
            (artifacts/'frames.json').write_text(json.dumps(frames),encoding='utf-8')
            print(json.dumps({'result':'passed','treeMovesInTwoAxes':True,'editorTerminalOpenClose':True,'crossTreePointerMovePreservesIdentity':True,'nativeTabEntry':True,'rapidReversal':True,'liveSplitterGestures':gestures,'resizeDuringMotion':True,'animationsOff':True,'singleTree':True,'qwidgetCount':0}),flush=True)
        except Exception:
            print('DIAGNOSTICS',workspace.diagnostics,flush=True)
            window.grabWindow().save(str(artifacts/'failure.png'))
            raise
        finally:
            workspace.closed.connect(app.quit);workspace._shutdown_and_quit=app.quit
            workspace.dispose();QTimer.singleShot(5000,app.quit);app.exec()


if __name__=='__main__':main()
