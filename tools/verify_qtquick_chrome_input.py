# SPDX-License-Identifier: MPL-2.0
"""Exercise real pointer input on content passing under launcher chrome."""
import json
import os
from pathlib import Path
import sys
import time


def main():
    repo=Path(__file__).resolve().parents[1];sys.path.insert(0,str(repo));os.chdir(repo)
    os.environ.setdefault('QSG_RENDER_LOOP','basic')
    from ui.runtime import initialize_quick_runtime,create_launcher
    initialize_quick_runtime()
    from PySide6.QtCore import Qt,QPointF,QMetaObject,Q_ARG,QCoreApplication,QEvent
    from PySide6.QtWidgets import QApplication
    from PySide6.QtTest import QTest
    from core.fonts import apply_app_font
    from ui.presentation import LauncherModel
    from tools.quick_test_utils import visual
    app=QApplication([]);app.setQuitOnLastWindowClosed(False);apply_app_font(app,polish_widgets=False)
    rows=[dict(project_name=f'Input test {i:02d}',path=f'C:/fixture/{i}',port1='system',parentPath='C:/fixture') for i in range(24)]
    model=LauncherModel(palette='dark_orange',projects=rows,profile={'name':'Input fixture','subtitle':'Developer'},settings={'effects':'high','animations':True})
    engine,window=create_launcher(model);out=repo/'build/qtquick_migration/chrome_input';out.mkdir(parents=True,exist_ok=True)
    commands=[]
    def record(name,data):
        commands.append({'name':name,'data':data})
        (out/'commands.json').write_text(json.dumps(commands,indent=2),encoding='utf-8')
        if name=='newProject':model.presentationRequested.emit('newProject',{})
        elif name in ('themePicker','license','credit','packages'):
            model.presentationRequested.emit('showDialog',{'kind':{'themePicker':'theme'}.get(name,name)})
        elif name=='projectMenu':
            model.presentationRequested.emit('projectMenu',{**data,'actions':[{'action':'open','label':'Open'},{'action':'delete','label':'Delete','danger':True}]})
    model.command.connect(record)
    if '--interactive' in sys.argv:
        app.setQuitOnLastWindowClosed(True);window.setTitle('Chrome input QA');window.requestActivate()
        try:app.exec()
        finally:
            engine._appearance_captures.close();engine._dialog_stack.close();window.close()
        return
    results=[]
    def center(item):return item.mapToScene(QPointF(item.width()/2,item.height()/2)).toPoint()
    def wait(predicate):
        end=time.monotonic()+5
        while not predicate():
            assert time.monotonic()<end,'Input check timed out'
            QTest.qWait(10)
    def click_at(point,button=Qt.LeftButton):
        QTest.mouseMove(window,point);QTest.qWait(15)
        QTest.mouseClick(window,button,Qt.NoModifier,point);QTest.qWait(20)
    try:
        QTest.qWait(200);window.requestActivate();wait(window.isActive)
        # The footer morph draws visual copies while its real buttons have
        # zero opacity. Those hidden targets must not accept another click.
        model.navigate('settings');clock=visual(window,'pageMotionClock')
        QMetaObject.invokeMethod(clock,'seek',Q_ARG('QVariant',140));QTest.qWait(30)
        click_at(center(visual(window,'settingsBackButton')))
        assert model.page=='settings','Invisible footer button accepted a click during its morph'
        model.navigate('projects');QMetaObject.invokeMethod(clock,'seek',Q_ARG('QVariant',140));QTest.qWait(30)
        before=len(commands);click_at(center(visual(window,'createButton')))
        assert len(commands)==before and not visual(window,'createProjectPopup').property('visible'),'Invisible Create target accepted a click'
        page=visual(window,'driveShelf').parentItem();trash=page.property('trash')
        click_at(center(visual(window,'trashDrive')))
        assert page.property('trash')==trash and not visual(window,'projectSearch').isEnabled(),'Invisible header controls accepted input'
        results.append('invisible footer and header morph targets blocked')
        model.chooseSetting('animations',False);model.navigate('projects');QTest.qWait(30)
        projects=visual(window,'projectList');projects.setProperty('contentY',92);QTest.qWait(60)
        for y in (30,60,80,622,645,680,698):
            for x in (300,500,850,970,1000):
                # Keep the genuine foreground footer controls out of this grid.
                if 636<=y<=668 and x>=792:continue
                before=len(commands);click_at(QPointF(x,y).toPoint())
                assert len(commands)==before,('Project content received a click through glass',x,y,commands[before:])
        results.append('project card/background grid blocked')
        projects.setProperty('contentY',0);QTest.qWait(50)
        card=visual(window,'projectCard_C:/fixture/0')
        buttons=[item for item in card.childItems() if item.inherits('QQuickAbstractButton') and item.isVisible()]
        assert len(buttons)==2
        for item in buttons:
            before=len(commands);click_at(center(item));assert len(commands)>before,'Exposed project action was blocked'
            popup=visual(window,'projectActionsPopup')
            if popup.property('visible'):click_at(QPointF(10,400).toPoint());wait(lambda:not popup.property('visible'))
        before=len(commands);click_at(card.mapToScene(QPointF(300,30)).toPoint())
        assert len(commands)>before and commands[-1]['name']=='openProject','Exposed project card was blocked'
        for target in (True,False):
            click_at(center(visual(window,'trashDrive')));assert page.property('trash')==target,'Exposed Trash header was blocked'
        results.append('exposed project card, menu, open arrow and Trash header work')
        model.chooseSetting('animations',False);model.navigate('settings');QTest.qWait(60)
        scroll=visual(window,'settingsScroll')
        controls=[];pending=[scroll]
        while pending:
            item=pending.pop();pending.extend(item.childItems())
            if item.inherits('QQuickAbstractButton') and item.isVisible():controls.append(item)
        assert len(controls)>=12,('Not all settings controls were found',len(controls))
        for item in controls:
            name=item.objectName() or item.property('text') or item.metaObject().className()
            for y in (20,60,75,625,675,695):
                scroll.setProperty('contentY',scroll.property('contentY')+center(item).y()-y);QTest.qWait(25)
                point=center(item)
                if not (0<=point.y()<77 or 620<=point.y()<700):continue
                before=len(commands);settings=dict(model.settings);language=model.strings['languageKey']
                click_at(point)
                assert len(commands)==before and model.settings==settings and model.strings['languageKey']==language,('Hidden settings control was activated',name,point,commands[before:],settings,model.settings)
                if item.objectName().endswith('Choice'):
                    assert not visual(window,'choicePopup_'+name).property('visible'),('Hidden dropdown opened',name,point)
        results.append(f'all {len(controls)} settings buttons, dropdowns and switch blocked')
        # A slight pointer drift can cross a strip while the same partially
        # obscured button still owns the press grab.
        for item in controls:
            name=item.objectName() or item.property('text') or item.metaObject().className()
            for press_y,release_y,move in ((80,74,True),(616,624,True),(80,74,False),(616,624,False)):
                scroll.setProperty('contentY',scroll.property('contentY')+center(item).y()-(press_y+release_y)/2);QTest.qWait(35)
                x=center(item).x();before=len(commands);settings=dict(model.settings)
                QTest.mousePress(window,Qt.LeftButton,Qt.NoModifier,QPointF(x,press_y).toPoint())
                if move:QTest.mouseMove(window,QPointF(x,release_y).toPoint());QTest.qWait(15)
                QTest.mouseRelease(window,Qt.LeftButton,Qt.NoModifier,QPointF(x,release_y).toPoint());QTest.qWait(25)
                assert len(commands)==before and model.settings==settings,('A held button activated after entering glass',name,press_y,release_y,commands[before:])
                assert item.isEnabled() and not item.property('pressed'),('Cancelled control did not recover',name)
                if item.objectName().endswith('Choice'):
                    assert not visual(window,'choicePopup_'+name).property('visible'),('Held dropdown opened through glass',name,release_y)
        results.append('press grab cancelled on entering chrome')
        # The same controls still respond when their real surface is exposed.
        visible=[]
        for item in controls:
            name=item.objectName() or item.property('text') or item.metaObject().className()
            scroll.setProperty('contentY',scroll.property('contentY')+center(item).y()-300);QTest.qWait(40)
            before=len(commands);click_at(center(item));QTest.qWait(30)
            if item.objectName().endswith('Choice'):
                popup=visual(window,'choicePopup_'+name)
                assert popup.property('visible'),('Visible dropdown stopped working',name)
                QTest.keyClick(window,Qt.Key_Escape);wait(lambda:not popup.property('visible'))
            else:
                assert len(commands)>before,('Visible settings control stopped working',name)
            for dialog in engine._dialog_stack.dialogs:
                if dialog.property('presented'):QMetaObject.invokeMethod(dialog,'close')
            wait(lambda:not window.property('nativeDialogActive'))
            model.chooseSetting('animations',False);QTest.qWait(30)
            visible.append(name)
        results.append('all 12 exposed settings controls still work')
        click_at(center(visual(window,'settingsBackButton')));assert model.page=='projects'
        click_at(center(visual(window,'createButton')));wait(lambda:visual(window,'createProjectPopup').property('visible'))
        QTest.keyClick(window,Qt.Key_Escape);wait(lambda:not visual(window,'createProjectPopup').property('visible'))
        click_at(center(visual(window,'settingsButton')));assert model.page=='settings'
        results.append('native Settings, Back and Create footer actions still work')
        model.replaceProjects([]);model.navigate('create');QTest.qWait(80)
        click_at(center(visual(window,'createSettingsButton')));assert model.page=='settings','First-run Settings footer failed'
        results.append('first-run Settings footer still works')
        assert not engine._diagnostics,engine._diagnostics
        report={'result':'passed','checks':results,'visibleSettingsControls':visible}
        (out/'report.json').write_text(json.dumps(report,indent=2),encoding='utf-8');print(json.dumps(report),flush=True)
    finally:
        engine._appearance_captures.close();engine._dialog_stack.close();window.close();engine.deleteLater()
        QCoreApplication.sendPostedEvents(None,QEvent.DeferredDelete);app.processEvents()


if __name__=='__main__':main()
