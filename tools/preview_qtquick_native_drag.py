# SPDX-License-Identifier: MPL-2.0
"""Owned temporary-file window for real Windows mouse drag/drop diagnostics."""
import json
import os
from pathlib import Path
import sys
import tempfile


def main():
    repo=Path(__file__).resolve().parents[1];sys.path.insert(0,str(repo));os.chdir(repo)
    from ui.runtime import initialize_quick_runtime,IconProvider,DialogStack
    initialize_quick_runtime(webengine=True)
    from PySide6.QtCore import QObject,QEvent,QUrl,QTimer,QCoreApplication
    from PySide6.QtQml import QQmlApplicationEngine
    from PySide6.QtWidgets import QApplication
    from core.fonts import apply_app_font
    from core import ui_lang
    from main_ext.class_ThemeUi import theme
    from ui.presentation import LauncherModel
    from ui.workspace import WorkspaceSession
    from ui.image_viewer import DocumentImages
    from tools.quick_test_utils import visual
    app=QApplication([]);apply_app_font(app,polish_widgets=False);theme.load();ui_lang.init()
    out=repo/'build/qtquick_migration/drag_native';out.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='rak-native-pointer-') as temporary:
        root=Path(temporary)
        for port in ('ROM_Left','ROM_Right'):
            base=root/port;(base/'01_Source').mkdir(parents=True);(base/'02_Target').mkdir()
            (base/'drag_me.txt').write_text('Real Windows drag fixture\n')
            (base/'01_Source/nested.txt').write_text('Nested native drag fixture\n')
        prefs=LauncherModel(palette='dark_green',language='id',settings={'effects':'high','animations':True,'treeMode':'tree'})
        session=WorkspaceSession(str(root),'ROM_Left','ROM_Right',preferences=prefs,settings_path=str(root/'settings.json'),enable_private_services=False)
        session._preferences_changed()
        engine=QQmlApplicationEngine();errors=[];engine.warnings.connect(lambda values:errors.extend(value.toString() for value in values))
        engine.rootContext().setContextProperty('rakPreferences',prefs)
        engine.addImageProvider('icons',IconProvider());engine.addImageProvider('rak',IconProvider());engine.addImageProvider('documents',DocumentImages())
        engine.setInitialProperties({'workspace':session,'preferences':prefs});engine.load(QUrl.fromLocalFile(str(repo/'tools/qtquick_native_drag_fixture.qml')))
        assert engine.rootObjects(),errors
        window=engine.rootObjects()[0];stack=DialogStack(window);events=[]
        class Trace(QObject):
            def eventFilter(self,watched,event):
                if event.type() in (QEvent.MouseButtonPress,QEvent.MouseButtonRelease,QEvent.MouseMove):
                    events.append({'event':event.type().name,'position':[event.position().x(),event.position().y()],'buttons':event.buttons().value})
                if event.type() in (QEvent.DragEnter,QEvent.DragMove,QEvent.Drop,QEvent.DragLeave):
                    data={'event':event.type().name}
                    if hasattr(event,'mimeData'):
                        mime=event.mimeData();data.update(formats=mime.formats(),position=[event.position().x(),event.position().y()],payload=bytes(mime.data('application/x-rak-tree')).decode(errors='replace'))
                    events.append(data)
                return False
        trace=Trace(window);window.installEventFilter(trace)
        for number in (0,1):
            panel=visual(window,'workspaceTree'+str(number)).parentItem()
            for key in ('dragging','dragPressActive','previewPending'):
                prop=panel.metaObject().property(panel.metaObject().indexOfProperty(key))
                getattr(panel,bytes(prop.notifySignal().name()).decode()).connect(lambda panel=panel,key=key,number=number:events.append({'property':key,'tree':number,'value':panel.property(key)}))
        session.tree.load(str(root/'ROM_Left'));session.tree2.load(str(root/'ROM_Right'))
        def snapshot():
            state={'root':str(root),'files':{port:[str(path.relative_to(root/port)) for path in (root/port).rglob('*') if path.is_file()] for port in ('ROM_Left','ROM_Right')},'events':events[-30:],'errors':errors,'history':session.history}
            for number in (0,1):
                panel=visual(window,'workspaceTree'+str(number)).parentItem()
                state['tree'+str(number)]={key:panel.property(key) for key in ('nativeDragging','dropActive','dropAccepted','dropDestination')}
            (out/'state.json').write_text(json.dumps(state,indent=2),encoding='utf-8')
        timer=QTimer();timer.setInterval(300);timer.timeout.connect(snapshot);timer.start()
        print('NATIVE_DRAG_FIXTURE',root,flush=True)
        try:app.exec()
        finally:
            timer.stop();snapshot();stack.close();session.stop();window.close();engine.deleteLater();session.deleteLater()
            QCoreApplication.sendPostedEvents(None,QEvent.DeferredDelete);app.processEvents()


if __name__=='__main__':main()
