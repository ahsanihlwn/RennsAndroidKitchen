# SPDX-License-Identifier: MPL-2.0
"""Verify typing and wheel scrolling through a real Quill workspace."""
import os, sys, tempfile, time
from pathlib import Path
repo = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo)); os.chdir(repo)
from ui.runtime import initialize_quick_runtime
initialize_quick_runtime(webengine=True)
from PySide6.QtCore import Qt, QTimer, QEventLoop, QPoint, QPointF, QCoreApplication
from PySide6.QtGui import QWheelEvent, QKeyEvent
from PySide6.QtCore import QEvent
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest
from ui.presentation import LauncherModel
from ui.workspace_window import QuickWorkspace
from main_ext.class_ThemeUi import theme
from core import ui_lang
from tools.quick_test_utils import visual
app=QApplication([]); app.setQuitOnLastWindowClosed(False); theme.load(); ui_lang.init()
def wait(ms):
    loop=QEventLoop(); QTimer.singleShot(ms,loop.quit); loop.exec()
def until(predicate, seconds=15):
    deadline=time.monotonic()+seconds
    while not predicate():
        if time.monotonic()>deadline: raise AssertionError('Timed out waiting for editor')
        wait(20)
with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
    root=Path(td); (root/'system').mkdir(); (root/'vendor').mkdir()
    path=root/'system'/'file.txt'; path.write_text('line text\n'*150)
    prefs=LauncherModel(palette='dark',settings={'animations':False},profile={})
    ws=QuickWorkspace(str(root),'system','vendor',preferences=prefs,settings_path=str(root/'settings.json'),enable_private_services=False)
    until(lambda: ws._ready_emitted,35)
    ws.reveal(); until(lambda: not ws._entry_transition_active)
    ws.session.openPath(str(path))
    w=ws.window; w.requestActivate(); until(lambda: w.isActive())
    until(lambda: ws.session.editor.active)
    e=visual(w,'quickQuillView'); point=e.mapToScene(QPointF(150,50)).toPoint()
    until(lambda: e.width()>150 and e.height()>50 and e.filePath)
    wait(400); point=e.mapToScene(QPointF(150,50)).toPoint()
    QTest.mouseClick(w,Qt.LeftButton,Qt.NoModifier,point)
    until(lambda: e.hasActiveFocus())
    for c in 'abc':
        QCoreApplication.sendEvent(w,QKeyEvent(QEvent.KeyPress,0,Qt.NoModifier,c))
        QCoreApplication.sendEvent(w,QKeyEvent(QEvent.KeyRelease,0,Qt.NoModifier,c))
    until(lambda: e._doc.line(2).endswith('abc') and any(row[1].endswith('abc') for row in e._rows))
    event=QWheelEvent(QPointF(point),QPointF(w.mapToGlobal(point)),QPoint(0,0),QPoint(0,-120),Qt.NoButton,Qt.NoModifier,Qt.ScrollPhase.NoScrollPhase,False)
    QCoreApplication.sendEvent(w,event)
    until(lambda: e.scrollY > 0 and e._rows[0][0] > 0)
    print('Quill typing, redraw and wheel scrolling passed',flush=True)
    ws.closed.connect(app.quit); ws._shutdown_and_quit=app.quit
    ws.dispose(); QTimer.singleShot(5000,app.quit); app.exec()
