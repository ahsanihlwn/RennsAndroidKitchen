# SPDX-License-Identifier: MPL-2.0
"""Exercise run_launcher: the splash must paint before backend/content setup."""
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from unittest.mock import patch


def main():
    repo=Path(__file__).resolve().parents[1];sys.path.insert(0,str(repo));os.chdir(repo)
    from ui.runtime import initialize_quick_runtime
    initialize_quick_runtime(webengine=True)
    from PySide6.QtCore import QObject,QTimer,QMetaObject,Qt,QtMsgType,qInstallMessageHandler
    from PySide6.QtGui import QColor
    from PySide6.QtWidgets import QApplication
    from PySide6.QtTest import QTest
    from tools.quick_test_utils import visual
    from ui import application as launcher
    from ui.project_store import ProjectStore
    from core import ui_lang,fonts
    import main_ext.class_ThemeUi as themes
    app=QApplication([]);app.setQuitOnLastWindowClosed(False)
    errors=[];phases=[];ready=[];record={};failure=[]
    previous=qInstallMessageHandler(lambda kind,context,text:errors.append(text) if kind in (QtMsgType.QtWarningMsg,QtMsgType.QtCriticalMsg,QtMsgType.QtFatalMsg) else None)
    artifacts=repo/'build/qtquick_migration/startup';artifacts.mkdir(parents=True,exist_ok=True)
    original_font=fonts.apply_app_font
    def apply_font(*args,**kwargs):
        result=original_font(*args,**kwargs);record['fonts']=time.monotonic();return result
    def accounts(self):self.account={};self.profile_provider=None
    with tempfile.TemporaryDirectory(prefix='rak-early-splash-') as temporary:
        root=Path(temporary)
        (root/'theme.json').write_text(json.dumps({'theme':'dark_green'}))
        (root/'settings.json').write_text(json.dumps({'effect_level':'high','animations_enabled':True}))
        store=ProjectStore(root/'metadata')
        store.create({'name':'Early splash fixture','port1':'system','mode':1},location=str(root/'projects'))
        class FixtureLauncher(launcher.LauncherApplication):
            def __init__(self,app,legacy_type=None,**options):
                engine,window,model=options['bootstrap']
                assert engine._frame_presenter.ready and window.opacity()==1
                assert not window.property('contentLoaded')
                assert window.findChild(QObject,'projectList') is None
                splash=window.findChild(QObject,'startupSplash')
                assert splash.property('visible')
                record['firstFrame']=time.monotonic()
                image=window.grabWindow();assert image.pixelColor(4,4)==QColor(model.colors['BG'])
                image.save(str(artifacts/'early_splash_before_backend.png'))
                record['window']=window;record['splash']=splash
                self.pulse=QTimer(app);self.pulse.setInterval(20)
                def sample():
                    glint=window.findChild(QObject,'splashGlint')
                    if glint and splash.property('visible'):phases.append(glint.property('phase'))
                self.pulse.timeout.connect(sample);self.pulse.start()
                super().__init__(app,legacy_type,store=store,**options)
                self._message('EARLY_STARTUP_MESSAGE')
                self.engine.warnings.connect(lambda values:errors.extend(value.toString() for value in values))
                self.startupFinished.connect(self.finished)
            def _start_services(self):
                if self._services_started:return
                self._services_started=True
                assert self.window.property('contentLoaded')
                assert len(self.engine._dialog_stack.dialogs)==7
                assert self.window is record['window']
                self._disk_usage_ready('windows',[
                    {'key':'C:','total':100*1024**3,'used':50*1024**3},
                    {'key':'D:','total':200*1024**3,'used':75*1024**3}])
                QTimer.singleShot(100,lambda:self.startupPrepared.emit(''))
                QTimer.singleShot(180,self._reveal_startup)
            def finished(self):
                try:
                    assert self.window.findChild(QObject,'startupSplash') is record['splash']
                    assert len(set(phases))>3,phases
                    assert ready and not self.model.startupActive
                    message=self.window.findChild(QObject,'launcherMessage')
                    details=message.property('details')
                    if hasattr(details,'toVariant'):details=details.toVariant()
                    assert message.property('presented') and details['text']=='EARLY_STARTUP_MESSAGE'
                    QMetaObject.invokeMethod(message,'acceptMessage')
                    assert not QApplication.allWidgets()
                    QTimer.singleShot(600,self.check_inputs)
                except Exception as error:failure.append(repr(error));self.quit()
            def _construct_editor(self,project,origin,opened_at,loader):
                record['openedProject']=project['path']
                self._cancel_open()
            def check_inputs(self):
                try:
                    self.window.requestActivate()
                    def wait(predicate):
                        end=time.monotonic()+4
                        while not predicate():
                            assert time.monotonic()<end,'Bootstrap pointer input timed out'
                            QTest.qWait(10)
                    wait(lambda:self.window.isActive())
                    assert visual(self.window,'drive_C:').isVisible() and visual(self.window,'drive_D:').isVisible()
                    button=visual(self.window,'createButton')
                    QTest.mouseClick(self.window,Qt.LeftButton,Qt.NoModifier,button.mapToScene(button.boundingRect().center()).toPoint())
                    popup=visual(self.window,'createProjectPopup');wait(lambda:popup.property('opened'))
                    QTest.keyClick(self.window,Qt.Key_Escape);wait(lambda:not popup.property('visible'))
                    project=store.projects()[0]
                    card=visual(self.window,'projectCard_'+project['path'])
                    QTest.mouseClick(self.window,Qt.LeftButton,Qt.NoModifier,card.mapToScene(card.boundingRect().center()).toPoint())
                    wait(lambda:'openedProject' in record)
                    assert record['openedProject']==project['path'] and not self.model.busy
                    button=visual(self.window,'settingsButton')
                    QTest.mouseClick(self.window,Qt.LeftButton,Qt.NoModifier,button.mapToScene(button.boundingRect().center()).toPoint())
                    QTimer.singleShot(600,self.check_settings)
                except Exception as error:failure.append(repr(error));self.quit()
            def check_settings(self):
                try:
                    assert self.model.page=='settings' and not errors,errors
                    record['checked']=True
                    self.window.grabWindow().save(str(artifacts/'early_splash_settings.png'))
                except Exception as error:failure.append(repr(error))
                self.quit()
            def _quit_services(self):self.pulse.stop();self.app.quit()
        with patch.object(themes,'_THEME_FILE',str(root/'theme.json')),patch.object(themes,'_SETTINGS_FILE',str(root/'settings.json')),patch.object(ui_lang,'_LANG_FILE',str(root/'lang.json')),patch.object(fonts,'apply_app_font',apply_font),patch.object(launcher.AccountState,'_init_account_features',accounts),patch.object(launcher,'private_bundle_enabled',lambda:False),patch.object(launcher,'LauncherApplication',FixtureLauncher):
            QTimer.singleShot(15000,lambda:(failure.append('bootstrap timeout'),app.exit(1)))
            result=launcher.run_launcher(app,on_ready=lambda:ready.append(True))
        assert result==0 and record.get('checked') and not failure,(result,record.get('checked'),failure,errors)
    qInstallMessageHandler(previous)
    print(json.dumps({'result':'passed','splashBeforeBackend':True,'contentDeferredUntilFirstFrame':True,'sameWindowAndSplash':True,'dynamicDialogRegistration':True,'animatedGlintSamples':len(set(phases)),'driveCardsReady':True,'pointerCreateAndOpenProject':True,'settingsNavigation':True,'fontsToSplashMs':round((record['firstFrame']-record['fonts'])*1000),'qwidgetCount':0}),flush=True)


if __name__=='__main__':main()
