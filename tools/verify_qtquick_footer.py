# SPDX-License-Identifier: MPL-2.0
"""Check actual footer presses and both sides of the page-action morph."""
import json
import importlib.util
from pathlib import Path
import sys
import time


def main():
    repo=Path(__file__).resolve().parents[1];sys.path.insert(0,str(repo))
    from core.fonts import configure_text_rendering_env,apply_app_font
    configure_text_rendering_env()
    from PySide6.QtCore import Qt,QMetaObject,Q_ARG,QPoint
    from PySide6.QtWidgets import QApplication,QWidget,QPushButton
    from PySide6.QtTest import QTest
    from ui.presentation import LauncherModel
    from ui.runtime import create_launcher
    from quick_test_utils import visual
    app=QApplication([]);apply_app_font(app)
    model=LauncherModel(projects=[{'path':'C:/Fixture','project_name':'Fixture'}],palette='dark_green')
    engine,window=create_launcher(model);errors=[]
    engine.warnings.connect(lambda values: errors.extend(value.toString() for value in values))
    motion=visual(window,'pageMotion');clock=visual(window,'pageMotionClock')
    artifacts=repo/'build/qtquick_migration/footer';artifacts.mkdir(parents=True,exist_ok=True)
    def wait(predicate):
        deadline=time.monotonic()+4
        while not predicate():
            assert time.monotonic()<deadline,errors
            QTest.qWait(5)
    def actions():
        return motion.property('actions').toVariant()
    def click(name,hold=0):
        button=visual(window,name)
        point=button.mapToScene(button.boundingRect().center()).toPoint()
        QTest.mousePress(window,Qt.LeftButton,Qt.NoModifier,point)
        if hold:
            QTest.qWait(hold)
            assert .95<button.property('pressScale')<.98,'Footer had no visible press response'
            window.grabWindow().save(str(artifacts/'pressed.png'))
        QTest.mouseRelease(window,Qt.LeftButton,Qt.NoModifier,point)
        return button
    try:
        wait(lambda: engine._frame_presenter.ready)
        source=click('settingsButton',80)
        pairs=actions()
        assert sum(pair['old'] is not None for pair in pairs)==2,('Source footer disappeared from the morph',pairs)
        assert sum(pair['next'] is not None for pair in pairs)==1,pairs
        assert pairs[0]['old']['text']==model.strings['btn_create_project']
        assert pairs[0]['next']['text']==model.strings['btn_back']
        assert abs(pairs[0]['old']['rect'].width()-121)<.01,'Press scale changed the layout endpoint'
        QTest.qWait(25)
        assert source.property('pressScale')==1,'Hidden button restarted its release animation after reset'
        QTest.qWait(100)
        assert motion.property('active') and 0<motion.property('footerProgress')<1
        window.grabWindow().save(str(artifacts/'projects_to_settings.png'))
        wait(lambda:not motion.property('active'))
        assert source.property('pressScale')==1
        # Execute the authoritative class, including its unmatched full-button
        # fade, instead of comparing a second copy of our interpolation formula.
        source_file=repo.parent/'rennsandroidkitchen/app_ext/class_FooterActionTransition.py'
        spec=importlib.util.spec_from_file_location('_footer_reference',source_file)
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        host=QWidget();host.setAttribute(Qt.WA_DontShowOnScreen);host.resize(1080,700)
        old_page,new_page=QWidget(host),QWidget(host)
        old_page.setGeometry(host.rect());new_page.setGeometry(host.rect())
        def buttons(page,field):
            result=[]
            for pair in pairs:
                if pair[field] is None:continue
                endpoint=pair[field];button=QPushButton(endpoint['text'],page)
                button.setGeometry(endpoint['rect'].toAlignedRect());button.show();result.append(button)
            return result
        old_buttons,new_buttons=buttons(old_page,'old'),buttons(new_page,'next')
        old_page.hide();new_page.show()
        assert not old_buttons[0].isVisible() and not old_buttons[0].isHidden()
        reference=module.FooterActionTransition(host,old_buttons,new_buttons)
        reference._group.start();reference._group.pause()
        unmatched=[button for button in reference.findChildren(QPushButton)
                   if button not in reference._shape_views and button not in reference._label_views][0]
        for milliseconds in [0,28,70,140,210,279]:
            reference._group.setCurrentTime(milliseconds)
            QMetaObject.invokeMethod(clock,'seek',Q_ARG('QVariant',milliseconds))
            for index,side in enumerate(['Old','New']):
                shape=visual(window,'footer'+side+'Surface_0')
                actual=reference._shape_views[index]
                origin=actual.mapTo(host,QPoint())
                expected=[origin.x(),origin.y(),actual.width(),actual.height()]
                assert max(abs(a-b) for a,b in zip([shape.x(),shape.y(),shape.width(),shape.height()],expected))<=1
                label=visual(window,'footer'+side+'Caption_0')
                assert abs(label.property('opacity')-reference._label_views[index].graphicsEffect().opacity())<1e-6
            for part in ['Surface','Caption']:
                assert abs(visual(window,'footerOld'+part+'_1').property('opacity')-unmatched.graphicsEffect().opacity())<1e-6
        reference.stop();host.close();host.deleteLater()
        QMetaObject.invokeMethod(clock,'seek',Q_ARG('QVariant',clock.property('duration')))
        click('settingsBackButton')
        pairs=actions()
        assert sum(pair['old'] is not None for pair in pairs)==1,pairs
        assert sum(pair['next'] is not None for pair in pairs)==2,pairs
        assert pairs[0]['old']['text']==model.strings['btn_back']
        assert pairs[0]['next']['text']==model.strings['btn_create_project']
        wait(lambda:not motion.property('active'))
        # A quick click still morphs both action slots even when no frame was
        # painted between press and release.
        click('settingsButton')
        assert actions()[0]['old'] is not None and actions()[0]['next'] is not None
        wait(lambda:not motion.property('active'))
        model.chooseSetting('animations',False)
        click('settingsBackButton')
        assert not motion.property('active') and model.page=='projects'
        model.chooseSetting('animations',True)
        model.replaceProjects([]);model.navigate('create')
        wait(lambda:not motion.property('active'))
        click('createSettingsButton',80)
        pairs=actions()
        assert sum(pair['old'] is not None for pair in pairs)==2,pairs
        assert all(pair['old'] is None or pair['old']['text']!=model.strings['btn_back'] for pair in pairs)
        wait(lambda:not motion.property('active'))
        click('settingsBackButton')
        wait(lambda:not motion.property('active'))
        assert model.page=='create'
        visual(window,'projectName').setProperty('text','Footer fixture')
        visual(window,'port1Name').setProperty('text','system')
        commands=[];model.command.connect(lambda name,data:commands.append(name))
        click('confirmProjectButton',80)
        assert 'createProject' in commands
        assert not errors,errors
        print(json.dumps({'result':'passed','checks':['80 ms footer press','source and destination survive page hiding',
             'rightmost action pairing in both directions','unscaled layout endpoints','six phases against original FooterActionTransition',
             'unmatched full-button fade','quick-click morph','first-run footer visibility','create confirmation press','animations disabled']}),flush=True)
    finally:
        engine._appearance_captures.close();engine._dialog_stack.close()
        window.close();engine.deleteLater();app.processEvents()


if __name__=='__main__':main()
