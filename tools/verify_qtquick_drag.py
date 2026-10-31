# SPDX-License-Identifier: MPL-2.0
"""Native drag-event delivery, source highlight/scroll rules and temporary moves."""
import json
import os
from pathlib import Path
import sys
import tempfile
import time


def main():
    repo=Path(__file__).resolve().parents[1];os.chdir(repo);sys.path.insert(0,str(repo))
    from ui.runtime import initialize_quick_runtime,IconProvider
    initialize_quick_runtime()
    from PySide6.QtCore import Qt,QTimer,QEventLoop,QUrl,QMimeData,QCoreApplication,QEvent,QPoint,QPointF,QMetaObject,Q_ARG
    from PySide6.QtGui import QDragEnterEvent,QDragMoveEvent,QDragLeaveEvent,QDropEvent,QDrag,QWheelEvent
    from PySide6.QtWidgets import QApplication
    from PySide6.QtQml import QQmlApplicationEngine
    from PySide6.QtTest import QTest,QSignalSpy
    from core.fonts import apply_app_font
    from core import ui_lang
    from main_ext.class_ThemeUi import theme
    from ui.presentation import LauncherModel
    from ui.workspace import WorkspaceSession
    from ui.image_viewer import DocumentImages
    from tools.quick_test_utils import visual
    app=QApplication([]);app.setQuitOnLastWindowClosed(False);apply_app_font(app,polish_widgets=False);theme.load();ui_lang.init()
    def wait_ms(ms):
        loop=QEventLoop();QTimer.singleShot(ms,loop.quit);loop.exec()
    def wait(predicate,seconds=15):
        deadline=time.monotonic()+seconds
        while not predicate():
            assert time.monotonic()<deadline,'Drag fixture timed out'
            wait_ms(10)
    artifacts=repo/'build/qtquick_migration/drag';artifacts.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='rak-quick-drag-') as temporary:
        root=Path(temporary);base=root/'system';base.mkdir()
        source=base/'aa_source';target=base/'bb_target';source.mkdir();target.mkdir();(target/'deep').mkdir()
        (target/'deep/nested.txt').write_text('nested connector fixture')
        path=source/'moved.txt';path.write_text('native move');leaf=target/'leaf.txt';leaf.write_text('leaf')
        repeat_file=base/'drag_repeat.txt';repeat_file.write_text('repeated connector drag')
        for number in range(100):(base/f'entry_{number:03d}.txt').write_text('scroll')
        prefs=LauncherModel(palette='dark_green',language='id',settings={'effects':'medium','animations':True,'treeMode':'tree'})
        session=WorkspaceSession(str(root),'system',preferences=prefs,enable_private_services=False,settings_path=str(root/'settings.json'))
        session._preferences_changed()
        engine=QQmlApplicationEngine();errors=[];engine.warnings.connect(lambda items:errors.extend(item.toString() for item in items))
        engine.addImageProvider('icons',IconProvider());engine.addImageProvider('documents',DocumentImages())
        engine.setInitialProperties({'workspace':session,'preferences':prefs});engine.load(QUrl.fromLocalFile(str(repo/'tools/qtquick_tree_fixture.qml')))
        assert engine.rootObjects(),errors
        window=engine.rootObjects()[0];window.requestActivate();wait(lambda:window.isActive())
        try:
            session.tree.load(str(base));wait(lambda:not session.tree.busy);wait_ms(100)
            tree=visual(window,'workspaceTree0');panel=tree.parentItem();highlight=visual(window,'treeDropHighlight0')
            arrow=visual(window,'treeBranchArrow:'+str(target));QTest.mouseClick(window,Qt.LeftButton,Qt.NoModifier,arrow.mapToScene(arrow.boundingRect().center()).toPoint())
            wait(lambda:session.tree._nodes[str(target)].loaded);wait_ms(100)
            def mime(paths):
                value=QMimeData();value.setData('application/x-rak-tree',json.dumps({'base':str(base),'paths':[str(path) for path in paths]}).encode())
                value.setUrls([QUrl.fromLocalFile(str(path)) for path in paths]);return value
            def point_for(path):
                # Name cells expose their own current view row, including expansion.
                label=visual(window,'treeBranchArrow:'+str(path))
                return label.mapToScene(label.boundingRect().center()).toPoint()+QPoint(60,0)
            actions=Qt.CopyAction|Qt.MoveAction
            # Hover, selection and drop paint only in the fixed viewport;
            # recycling cells must never tint the title or column header.
            feedback=visual(window,'treeFeedbackViewport0');hover=visual(window,'treeHoverHighlight0')
            assert feedback.parentItem()==tree and feedback.property('clip')
            assert hover.property('radius')==1 and highlight.property('radius')==1
            header_origin=panel.mapToScene(QPointF(0,0)).toPoint()
            def header_pixels():return window.grabWindow().copy(header_origin.x(),header_origin.y(),180,77)
            baseline_header=header_pixels()
            QTest.mouseMove(window,point_for(source));wait_ms(40)
            assert hover.isVisible() and header_pixels()==baseline_header
            QTest.mouseClick(window,Qt.LeftButton,Qt.NoModifier,point_for(source));wait_ms(40)
            assert not hover.isVisible() and header_pixels()==baseline_header
            tree.setProperty('contentY',280);wait_ms(40)
            assert header_pixels()==baseline_header
            tree.setProperty('contentY',-tree.property('topMargin'));wait_ms(40)
            assert header_pixels()==baseline_header
            window.grabWindow().save(str(artifacts/'hover_selection_viewport.png'))
            target_point=point_for(target);payload=mime([path])
            QCoreApplication.sendEvent(window,QDragEnterEvent(target_point,actions,payload,Qt.LeftButton,Qt.NoModifier));wait_ms(50)
            assert panel.property('dropActive') and panel.property('dropDestination')==str(target)
            assert highlight.isVisible() and round(highlight.height())==84,highlight.height()
            assert header_pixels()==baseline_header
            window.grabWindow().save(str(artifacts/'folder_and_children.png'))
            # Hovering a file lights its sibling group, with no parent-folder row.
            leaf_index=session.tree._nodes[str(leaf)].row
            leaf_point=QPoint(target_point.x(),target_point.y()+28*(leaf_index+1))
            QCoreApplication.sendEvent(window,QDragMoveEvent(leaf_point,actions,payload,Qt.LeftButton,Qt.NoModifier));wait_ms(30)
            assert panel.property('dropDestination')==str(target)
            assert round(highlight.height())==56,highlight.height()
            QCoreApplication.sendEvent(window,QDragLeaveEvent());assert not panel.property('dropActive')
            # The source paintEvent falls back to one hovered row when its
            # same-directory group is empty, despite the dragMoveEvent comment.
            same=mime([leaf]);QCoreApplication.sendEvent(window,QDragEnterEvent(target_point,actions,same,Qt.LeftButton,Qt.NoModifier));wait_ms(20)
            assert highlight.isVisible() and round(highlight.height())==28;QCoreApplication.sendEvent(window,QDragLeaveEvent())
            # A connector targets its directory and lights the complete shared
            # stem and direct-child junctions, including across nested rows.
            deep=target/'deep';arrow=visual(window,'treeBranchArrow:'+str(deep))
            QTest.mouseClick(window,Qt.LeftButton,Qt.NoModifier,arrow.mapToScene(arrow.boundingRect().center()).toPoint())
            wait(lambda:session.tree._nodes[str(deep)].loaded);wait_ms(80)
            nested=deep/'nested.txt';nested_point=point_for(nested)
            connector=QPoint(tree.mapToScene(QPointF(10,0)).toPoint().x(),nested_point.y())
            QCoreApplication.sendEvent(window,QDragEnterEvent(connector,actions,payload,Qt.LeftButton,Qt.NoModifier));wait_ms(30)
            guide=visual(window,'treeDropGuide0')
            branches=panel.property('dropGuideBranches');branches=branches.toVariant() if hasattr(branches,'toVariant') else branches
            assert panel.property('dropDestination')==str(target) and guide.isVisible()
            assert len(branches)==2 and round(guide.height())==70,(branches,guide.height())
            assert highlight.isVisible() and round(highlight.height())==112
            frame=window.grabWindow();sample=guide.mapToScene(QPointF(1,6)).toPoint()
            from PySide6.QtGui import QColor
            assert frame.pixelColor(sample)==QColor(prefs.colors['accent_hex'])
            assert header_pixels()==baseline_header
            frame.save(str(artifacts/'directory_connected_drop.png'))
            # Moving within the same stem keeps the entire group lit.
            leaf_point=point_for(leaf);QCoreApplication.sendEvent(window,QDragMoveEvent(QPoint(connector.x(),leaf_point.y()),actions,payload,Qt.LeftButton,Qt.NoModifier));wait_ms(20)
            assert panel.property('dropDestination')==str(target) and round(guide.height())==70
            # The deeper connector switches both the directory and its group.
            inner=QPoint(connector.x()+20,nested_point.y())
            QCoreApplication.sendEvent(window,QDragMoveEvent(inner,actions,payload,Qt.LeftButton,Qt.NoModifier));wait_ms(20)
            branches=panel.property('dropGuideBranches');branches=branches.toVariant() if hasattr(branches,'toVariant') else branches
            assert panel.property('dropDestination')==str(deep) and len(branches)==1
            assert round(guide.height())==14 and round(highlight.height())==56
            window.grabWindow().save(str(artifacts/'nested_directory_connected_drop.png'))
            QCoreApplication.sendEvent(window,QDragLeaveEvent());assert not guide.isVisible() and not highlight.isVisible()
            # After a first drop, the next source can already belong to this
            # directory. The connector must still light on every re-entry.
            for cycle in range(3):
                QCoreApplication.sendEvent(window,QDragEnterEvent(connector,actions,same,Qt.LeftButton,Qt.NoModifier));wait_ms(20)
                assert guide.isVisible(),f'Connector failed to light again on cycle {cycle}'
                assert panel.property('dropDestination')==str(target) and round(highlight.height())==112
                QCoreApplication.sendEvent(window,QDragLeaveEvent());assert not guide.isVisible()
            # Complete pointer gestures must survive a real drop/model refresh,
            # a same-directory no-op, and repeated cancellation/re-entry.
            def connector_gesture(source_path, *, cancel=False):
                wait_ms(60)
                start=point_for(source_path)
                destination=QPoint(tree.mapToScene(QPointF(10,0)).toPoint().x(),point_for(leaf).y())
                QTest.mousePress(window,Qt.LeftButton,Qt.NoModifier,start)
                QTest.mouseMove(window,start+QPoint(30,0),20);QTest.mouseMove(window,destination,20)
                wait(lambda:panel.property('dragging') and guide.isVisible())
                assert panel.property('dropDestination')==str(target) and highlight.isVisible()
                QTest.mouseMove(window,point_for(target),20);wait_ms(10)
                assert not guide.isVisible()
                QTest.mouseMove(window,destination,20);wait(lambda:guide.isVisible())
                if cancel:QTest.keyClick(window,Qt.Key_Escape)
                QTest.mouseRelease(window,Qt.LeftButton,Qt.NoModifier,destination)
                wait(lambda:not panel.property('dragging'))
                assert not guide.isVisible() and not highlight.isVisible()
            identity=repeat_file.stat().st_ino
            connector_gesture(repeat_file)
            repeated=target/repeat_file.name
            wait(lambda:repeated.exists() and not repeat_file.exists())
            wait(lambda:not session._operation_count and not session.tree.busy);wait_ms(60)
            serial=session.transfers._serial
            connector_gesture(repeated)
            assert session.transfers._serial==serial and repeated.stat().st_ino==identity
            for _ in range(3):connector_gesture(repeated,cancel=True)
            assert repeated.read_text()=='repeated connector drag' and repeated.stat().st_ino==identity
            repeated.unlink()
            arrow=visual(window,'treeBranchArrow:'+str(deep))
            QTest.mouseClick(window,Qt.LeftButton,Qt.NoModifier,arrow.mapToScene(arrow.boundingRect().center()).toPoint());wait_ms(30)
            nested.unlink();session.tree.reloadMetadata();wait(lambda:not session.tree.busy);wait_ms(30)
            # Scroll starts inside the wider band, accelerates towards either
            # edge, and leaves a stationary center for precise row targeting.
            speeds=[]
            for fraction in (.50,.65,.80,.99,.35,.20,.01):
                point=tree.mapToScene(QPointF(90,tree.property('topMargin')+(tree.height()-tree.property('topMargin'))*fraction)).toPoint()
                QCoreApplication.sendEvent(window,QDragEnterEvent(point,actions,payload,Qt.LeftButton,Qt.NoModifier))
                speeds.append(panel.property('scrollSpeed'))
                QCoreApplication.sendEvent(window,QDragLeaveEvent())
            assert speeds[0]==0 and 0<speeds[1]<speeds[2]<speeds[3]
            assert 0>speeds[4]>speeds[5]>speeds[6] and speeds[3]>20 and speeds[6]<-20,speeds
            tree.setProperty('contentY',-tree.property('topMargin'))
            bottom=tree.mapToScene(QPointF(90,tree.height()-1)).toPoint()
            QCoreApplication.sendEvent(window,QDragEnterEvent(bottom,actions,payload,Qt.LeftButton,Qt.NoModifier))
            wait(lambda:tree.property('contentY')>20);window.grabWindow().save(str(artifacts/'edge_scroll.png'))
            bar=visual(window,'treeVerticalScrollBar0');assert bar.property('shown')
            QCoreApplication.sendEvent(window,QDragLeaveEvent());position=tree.property('contentY');wait_ms(100)
            assert tree.property('contentY')==position and panel.property('scrollSpeed')==0
            wait(lambda:not bar.property('shown'),2)
            QTest.mouseMove(window,bar.mapToScene(bar.boundingRect().center()).toPoint());wait(lambda:bar.property('hovered'))
            assert bar.property('shown')
            QTest.mouseMove(window,tree.mapToScene(QPointF(90,100)).toPoint());wait(lambda:not bar.property('shown'),2)
            # The persistent input area must pass wheel events to the view and
            # follow the row under a stationary pointer after scrolling.
            tree.setProperty('contentY',-tree.property('topMargin'));wait_ms(30)
            wheel_point=tree.mapToScene(QPointF(90,100)).toPoint();QTest.mouseMove(window,wheel_point)
            QCoreApplication.sendEvent(window,QWheelEvent(QPointF(wheel_point),QPointF(window.mapToGlobal(wheel_point)),QPoint(),QPoint(0,-120),Qt.NoButton,Qt.NoModifier,Qt.NoScrollPhase,False))
            wait(lambda:tree.property('contentY')>0);wait_ms(50)
            assert panel.property('hoverRow')==int((100+tree.property('contentY'))//28)
            tree.setProperty('contentY',-tree.property('topMargin'));wait_ms(50);target_point=point_for(target)
            # Real QWindow drag/drop delivery invokes the actual transfer worker.
            QCoreApplication.sendEvent(window,QDragEnterEvent(target_point,actions,payload,Qt.LeftButton,Qt.NoModifier))
            drop=QDropEvent(QPointF(target_point),actions,payload,Qt.LeftButton,Qt.NoModifier);QCoreApplication.sendEvent(window,drop)
            wait(lambda:(target/'moved.txt').exists() and not path.exists());wait(lambda:not session._operation_count and not session.tree.busy)
            assert (target/'moved.txt').read_text()=='native move'
            # Cut to the same directory is a consumed no-op; copy becomes (2).
            moved=str(target/'moved.txt');session.selectPaths(0,[moved]);session.dispatch('cut',{})
            assert session._clipboard
            session.dispatch('paste',{'path':str(target)});assert session._clipboard is None and Path(moved).exists()
            assert not session._operation_count
            session.dispatch('copy',{'path':moved});session.dispatch('paste',{'path':str(target)})
            wait(lambda:(target/'moved(2).txt').exists());wait(lambda:not session._operation_count and not session.tree.busy)
            assert (target/'moved(2).txt').read_text()=='native move' and Path(moved).exists()
            contexts=[]
            session.presentationRequested.connect(lambda kind,data:contexts.append(data) if kind=='contextMenu' else None)
            first=base/'entry_000.txt';second=base/'entry_001.txt'
            a=point_for(first);b=point_for(second)
            QTest.mouseClick(window,Qt.LeftButton,Qt.NoModifier,a)
            QTest.mouseClick(window,Qt.LeftButton,Qt.ControlModifier,b)
            assert set(session._selection)=={str(first),str(second)}
            # Start the actual in-scene gesture from each column, then cancel.
            # Complete pointer drops have a separate two-ROM verifier.
            for x_offset in (0,330,round(tree.width())-155):
                meta=panel.metaObject();signal=meta.property(meta.indexOfProperty('dragging')).notifySignal()
                spy=QSignalSpy(panel,signal);assert spy.isValid()
                start=a+QPoint(x_offset,0)
                try:
                    QTest.mousePress(window,Qt.LeftButton,Qt.NoModifier,start)
                    QTest.mouseMove(window,start+QPoint(30,0),20)
                    QTest.mouseMove(window,start+QPoint(45,0),20)
                    wait(lambda:panel.property('dragging'))
                    QTest.keyClick(window,Qt.Key_Escape);wait(lambda:spy.count()>=2)
                finally:
                    QTest.mouseRelease(window,Qt.LeftButton,Qt.NoModifier,start+QPoint(45,0))
                assert not panel.property('nativeDragging') and not panel.property('previewPending')
                paths=panel.property('draggingPaths');paths=paths.toVariant() if hasattr(paths,'toVariant') else paths
                assert set(paths)=={str(first),str(second)},paths
                assert not panel.property('dropActive')
            # Release before the asynchronous drag image is ready, then press
            # again without dragging. The old callback must not start a drag.
            QTest.mousePress(window,Qt.LeftButton,Qt.NoModifier,a)
            QTest.mouseMove(window,a+QPoint(30,0));QTest.mouseMove(window,a+QPoint(45,0))
            QTest.mouseRelease(window,Qt.LeftButton,Qt.NoModifier,a+QPoint(45,0))
            QTest.mousePress(window,Qt.LeftButton,Qt.NoModifier,a);wait_ms(100)
            assert not panel.property('dragging') and not panel.property('previewPending')
            QTest.mouseRelease(window,Qt.LeftButton,Qt.NoModifier,a)
            QTest.mouseClick(window,Qt.LeftButton,Qt.NoModifier,a)
            QTest.mouseClick(window,Qt.LeftButton,Qt.ControlModifier,b)
            QMetaObject.invokeMethod(panel,'prepareDragPreview',Q_ARG('QVariant',[str(first),str(second)]))
            preview=visual(window,'treeDragPreview0');ready=[];result=preview.grabToImage();result.ready.connect(lambda:ready.append(True));wait(lambda:ready)
            assert result.image().height()==56 and result.image().width()>=tree.width()
            assert result.image().save(str(artifacts/'multi_drag_preview.png'))
            panel.setProperty('previewRows',[])
            QTest.mousePress(window,Qt.LeftButton,Qt.NoModifier,a)
            assert set(session._selection)=={str(first),str(second)},'Pressing a selected row destroyed the multi-drag selection'
            QTest.mouseRelease(window,Qt.LeftButton,Qt.NoModifier,a)
            QTest.mousePress(window,Qt.RightButton,Qt.NoModifier,a);assert not contexts
            QTest.mouseRelease(window,Qt.RightButton,Qt.NoModifier,a);wait(lambda:len(contexts)==1)
            assert contexts[-1]['path']==str(first)
            # Empty viewport still targets the current directory on right release.
            prefs.chooseSetting('treeMode','single_dir');wait(lambda:not session.tree.busy)
            session.tree.activate(str(target));wait(lambda:not session.tree.busy)
            session.tree.activate(str(target/'deep'));wait(lambda:not session.tree.busy);wait_ms(50)
            cells=[];icons=[];pending=[tree]
            while pending:
                item=pending.pop();pending.extend(item.childItems())
                if item.objectName()=='treeEntryName':cells.append(item)
                if item.objectName()=='treeEntryIcon':icons.append(item)
            empty=next((item for item in cells if item.isVisible() and item.property('text')=='Empty'),None)
            assert empty is not None,[(item.property('text'),item.isVisible()) for item in cells]
            assert empty.property('font').italic() and empty.property('color').alpha()==130
            assert any(not item.isVisible() and not item.property('source').toString() for item in icons)
            window.grabWindow().save(str(artifacts/'empty_row.png'))
            blank=tree.mapToScene(QPointF(90,tree.height()-20)).toPoint()
            QTest.mouseClick(window,Qt.RightButton,Qt.NoModifier,blank);wait(lambda:len(contexts)==2)
            assert contexts[-1]['path']==str(target/'deep')
            # A narrow tree cannot paint Selinux/Permission headers over the
            # editor gutter. Header columns also follow horizontal scrolling.
            window.setProperty('treeWidth',220);wait_ms(60)
            columns=visual(window,'workspaceTreeColumns0');assert columns.property('clip')
            for offset in (0,80):
                tree.setProperty('contentX',offset);wait_ms(30)
                header=visual(window,'workspaceTreeColumn0_1')
                assert abs(header.x()-(panel.property('nameWidth')-tree.property('contentX')))<.01
                origin=columns.mapToScene(QPointF(0,0)).toPoint()
                rect=(round(panel.x()+panel.width()+2),origin.y(),200,round(columns.height()))
                shown=window.grabWindow().copy(*rect)
                columns.setVisible(False);wait_ms(20)
                hidden=window.grabWindow().copy(*rect)
                columns.setVisible(True);wait_ms(20)
                assert shown==hidden,'A column header painted outside the tree viewport'
            window.grabWindow().save(str(artifacts/'narrow_headers_clipped.png'))
            assert not errors,errors;assert not QApplication.allWidgets()
            report={'result':'passed','folderAndChildrenHighlight':True,'connectedDirectoryDropLines':True,'connectorDropGroupHighlight':True,'connectorReentryAfterDropAndCancel':True,'sameDirectoryConnectorHighlight':True,'nestedConnectorSwitchAndLeave':True,'fileSiblingHighlight':True,'sameDirectoryHoveredRow':True,'dragStartsFromAllThreeColumns':True,
                'edgeScrollAndLeaveStop':True,'widerProgressiveEdgeScroll':True,'edgeScrollSamples':speeds,'actualDropMoves':True,'cutSameDirectoryNoOp':True,'copySameDirectoryNumbered':True,
                'multiDragPressPreservesSelection':True,'multiRowDragPreview':True,'rightMenuOnRelease':True,'emptyViewportContext':True,'emptyRowReferenceStyle':True,'scrollbarIdleAndHover':True,'narrowHeaderPixelClipping':True,'horizontalHeaderAlignment':True,'hoverSelectionDropHeaderPixelClipping':True,'allFeedbackRadiusOne':True,'qwidgetCount':0}
            (artifacts/'report.json').write_text(json.dumps(report,indent=2),encoding='utf-8');print(json.dumps(report),flush=True)
        finally:
            session.stop();window.close();engine.deleteLater();session.deleteLater()
            QCoreApplication.sendPostedEvents(None,QEvent.DeferredDelete);app.processEvents()


if __name__=='__main__':main()
