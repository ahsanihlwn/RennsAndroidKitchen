# SPDX-License-Identifier: MPL-2.0
"""Exercise toolbar mode changes through the real editor and SplitView handle."""
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
        def start(self,**kwargs):self.signals.data_ready.emit('TERMINAL_CARD_INSET_FIXTURE\r\n')
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
            assert time.monotonic()<deadline,'Workspace toolbar timed out'
            wait_ms(10)
    with tempfile.TemporaryDirectory(prefix='rak-toolbar-workspace-') as temporary:
        root=Path(temporary);(root/'system').mkdir();(root/'vendor').mkdir()
        path=root/'system/build.prop';path.write_text('ro.fixture=toolbar\n')
        prefs=LauncherModel(palette='dark_green',settings={'effects':'high','animations':True},profile={'name':'ahsanihlwn','subtitle':'rennsyt'})
        workspace=QuickWorkspace(str(root),'system','vendor',preferences=prefs,settings_path=str(root/'settings.json'),terminal_factory=terminal_factory,enable_private_services=False)
        window=workspace.window;session=workspace.session;samples=[];gestures=[]
        try:
            wait(lambda:workspace._ready_emitted,35);workspace.reveal();wait(lambda:not workspace._entry_transition_active)
            window.requestActivate();wait(lambda:window.isActive())
            motion=visual(window,'workspaceLayoutMotion')
            wait(lambda:motion.property('initialized') and not motion.property('running') and not motion.property('pending'))
            toolbar=visual(window,'workspaceToolbar')
            toolbar.modeAmountChanged.connect(lambda:samples.append(toolbar.property('modeAmount')))
            assert toolbar.property('modeAmount')==0
            session.openPath(str(path))
            wait(lambda:not motion.property('running') and not motion.property('pending') and toolbar.property('compact') and not toolbar.property('modeAnimating'))
            assert toolbar.property('modeAmount')==1 and any(0<value<1 for value in samples)
            for distance,compact in ((300,False),(-300,True)):
                divider=visual(window,'workspaceMainDivider')
                start=divider.mapToScene(divider.boundingRect().center()).toPoint()
                before=len(samples);widths=[toolbar.width()]
                QTest.mouseMove(window,start);wait_ms(30)
                QTest.mousePress(window,Qt.LeftButton,Qt.NoModifier,start)
                for step in range(1,11):
                    QTest.mouseMove(window,start+QPoint(round(distance*step/10),0),15);wait_ms(10)
                    widths.append(toolbar.width())
                QTest.mouseRelease(window,Qt.LeftButton,Qt.NoModifier,start+QPoint(distance,0))
                gesture={'distance':distance,'start':[start.x(),start.y()],'widths':widths,'compact':toolbar.property('compact')}
                gestures.append(gesture);print('SPLITTER',json.dumps(gesture),flush=True)
                assert len(set(widths))>3,gesture
                wait(lambda:toolbar.property('compact')==compact and not toolbar.property('modeAnimating'))
                assert any(0<value<1 for value in samples[before:]),samples[before:]
            session.toggleTerminal();wait(lambda:session._terminals and session._terminals[0]['session'].bridge.is_ready)
            wait(lambda:not motion.property('running') and not motion.property('pending'))
            for kind in ('Editor','Terminal'):
                card=visual(window,'workspace'+kind+'Card');tabs=visual(window,'workspace'+kind+'Tabs')
                position=tabs.mapToItem(card,QPointF(0,0))
                assert position==QPointF(1,1),(kind,position)
                viewport=visual(window,'workspaceTabViewport_workspace'+kind+'Tabs')
                assert viewport.x()==0 and viewport.width()==tabs.width()
                assert viewport.mapToItem(card,QPointF(0,0)).x()==1
                key=session.activeTab if kind=='Editor' else session.activeTerminal
                tab=visual(window,'workspaceTab_'+key);face=visual(window,'workspaceTabFace_'+key)
                mask=visual(window,'workspaceTabCornerMask_workspace'+kind+'Tabs')
                assert face.property('radius')==0 and mask.property('radius')==7
                assert mask.property('topRightRadius')==(7 if kind=='Editor' else 0) and mask.property('bottomLeftRadius')==0
                background=prefs.colors['BG'];expected=prefs.colors['accent_hex']
                from PySide6.QtGui import QColor
                def difference(a,b):return max(abs(a.red()-b.red()),abs(a.green()-b.green()),abs(a.blue()-b.blue()))
                def check_corner():
                    origin=viewport.mapToScene(QPointF(0,0)).toPoint();image=window.grabWindow()
                    corner=image.pixelColor(origin.x(),origin.y());accent=image.pixelColor(origin.x()+8,origin.y()+1)
                    assert difference(corner,QColor(background))<5,(kind,corner.name(),background)
                    assert difference(accent,QColor(expected))<8,(kind,accent.name(),expected)
                    if tabs.property('rightCornerRadius'):
                        right=image.pixelColor(origin.x()+round(viewport.width())-1,origin.y())
                        assert difference(right,QColor(background))<5,(kind,'right frame corner',right.name(),background)
                check_corner()
                # The corner belongs to the viewport even when it cuts through
                # a tab after scrolling, rather than to each tab's own top.
                original_width=tabs.width();tabs.setWidth(80);wait_ms(30)
                viewport.setProperty('contentX',16);wait_ms(30)
                assert viewport.property('contentX')==16
                check_corner()
                viewport.setProperty('contentX',0);tabs.setWidth(original_width);wait_ms(30)
            artifacts=repo/'build/qtquick_migration/toolbar';artifacts.mkdir(parents=True,exist_ok=True)
            window.grabWindow().save(str(artifacts/'editor_terminal_flush.png'))
            session.toggleTerminal();session.closeTab(str(path));wait(lambda:not session.rightOpen and not motion.property('running') and not motion.property('pending') and not toolbar.property('modeAnimating'))
            assert toolbar.property('modeAmount')==0 and not toolbar.property('compact')
            assert not workspace.diagnostics,workspace.diagnostics
            assert not QApplication.allWidgets()
            print(json.dumps({'result':'passed','editorOpenCloseModeMorph':True,'actualSplitterDragBothDirections':True,'editorAndTerminalHeaderInset':1,'tabGutter':0,'onlyViewportLeftCornerRounded':True,'scrolledTabCornerPixels':True,'gestures':gestures,'qwidgetCount':0}),flush=True)
        finally:
            workspace.closed.connect(app.quit);workspace._shutdown_and_quit=app.quit
            workspace.dispose();QTimer.singleShot(5000,app.quit);app.exec()


if __name__=='__main__':main()
