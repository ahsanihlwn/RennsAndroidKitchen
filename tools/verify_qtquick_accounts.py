# SPDX-License-Identifier: MPL-2.0
"""Exercise account drafts, auth states and native input with inert services."""
import json
import os
from pathlib import Path
import sys
import time


def main():
    repo=Path(__file__).resolve().parents[1];sys.path.insert(0,str(repo));os.chdir(repo)
    from core.fonts import configure_text_rendering_env,apply_app_font
    configure_text_rendering_env()
    from PySide6.QtCore import QObject,QMetaObject,Qt,Signal
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtQuick import QQuickWindow
    from PySide6.QtTest import QTest
    from ui.presentation import LauncherModel
    from ui.runtime import create_launcher
    from ui.accounts import ProfileModel
    from ui.auth import AuthModel
    app=QGuiApplication([]);app.setQuitOnLastWindowClosed(False);apply_app_font(app)
    class Accounts(QObject):
        changed=Signal()
        account={"username":"Fixture","employee_status":"Developer","avatar":"000","id":"fixture_id","dev_id":"fixture_dev"}
        def update_account(self,value):self.account=dict(value)
        @property
        def profile_provider(self):return self
    class AuthService(QObject):
        signin_success=Signal(dict);google_success=Signal(dict);signup_success=Signal(dict)
        signup_pending=Signal(str,str,str)
        signin_error=Signal(str);signup_error=Signal(str);google_error=Signal(str)
        online=True
        def has_internet(self):return self.online
        def signin(self,email,password):self.signin_error.emit("Incorrect email or password.")
        def signup(self,email,password,user,role):self.signup_pending.emit(email,user,role)
        def google(self):self.google_error.emit("Fixture browser cancelled")
    saves=[];syncs=[];pending=[]
    accounts=Accounts();service=AuthService()
    def save(user,role,avatar,dev,uid,**kwargs):
        row=dict(username=user,employee_status=role,avatar=avatar,dev_id=dev,id=uid,**kwargs)
        saves.append(row);return row
    profile=ProfileModel(accounts,save=save,sync=lambda row:syncs.append(row))
    auth=AuthModel(service=service,save=save,pending=lambda **row:pending.append(row))
    model=LauncherModel(palette='dark_ocean',settings={'animations':True,'effects':'medium'})
    engine,window=create_launcher(model,visible=False)
    window.setProperty('profileBackend',profile);window.setProperty('authBackend',auth)
    errors=[];engine.warnings.connect(lambda values:errors.extend(error.toString() for error in values))
    window.show();QTest.qWait(100)
    artifacts=repo/'build/qtquick_migration/quick_windows';artifacts.mkdir(parents=True,exist_ok=True)
    from quick_test_utils import visual,popup_frame

    def wait_for(predicate):
        deadline=time.monotonic()+8
        while not predicate():
            assert time.monotonic()<deadline,errors
            QTest.qWait(15)
    def click(owner,name):
        item=visual(owner,name)
        QTest.mouseClick(window,Qt.LeftButton,Qt.NoModifier,item.mapToScene(item.boundingRect().center()).toPoint())
    dialog=window.findChild(QObject,'profileWindow')
    try:
        model.presentationRequested.emit('showDialog',{'kind':'profile'})
        wait_for(lambda:dialog.property("visible") and dialog.property("opacity")==1)
        assert (dialog.property("width"),dialog.property("height"))==(360,378)
        popup_frame(window,dialog).save(str(artifacts/'profile.png'))
        click(dialog,'profileAvatarEdit');QTest.qWait(20)
        popup_frame(window,dialog).save(str(artifacts/'avatars.png'))
        choice=profile.avatars[-1]['key']
        # Select a visible second registry avatar; cancelling only the picker
        # must preserve the form's accepted avatar.
        choice=profile.avatars[1]['key']
        click(dialog,'profileAvatar_'+choice);click(dialog,'profileAvatarCancel')
        assert profile.draft['avatar']=='000' and not saves
        click(dialog,'profileAvatarEdit');click(dialog,'profileAvatarAccept')
        assert profile.draft['avatar']==choice
        click(dialog,'profileCancel');wait_for(lambda:not dialog.property("visible"))
        assert not saves
        model.presentationRequested.emit('showDialog',{'kind':'profile'})
        wait_for(lambda:dialog.property("visible") and dialog.property("opacity")==1)
        visual(dialog,'profileUsername').setProperty('text','Edited')
        click(dialog,'profileSave');wait_for(lambda:not dialog.property("visible") and bool(syncs) and not profile.running)
        assert saves[0]['username']=='Edited' and saves[0]['id']=='fixture_id'
        assert accounts.account['username']=='Edited'
        model.presentationRequested.emit('login',{})
        login=visual(window,'loginOverlay');card=visual(window,'loginCard')
        wait_for(lambda:login.isVisible() and login.property('cardOpacity')==1)
        assert (card.width(),card.height())==(320,435),(card.width(),card.height())
        window.grabWindow().save(str(artifacts/'login_overlay.png'))
        click(window,'loginSubmit');QTest.qWait(30)
        assert auth.alert=='Email and password are required.'
        assert visual(window,'loginEmail').hasActiveFocus()
        auth.clearAlert()
        click(window,'loginSwitch');QTest.qWait(30)
        assert auth.mode=='signup' and card.height()==422,card.height()
        window.grabWindow().save(str(artifacts/'signup_overlay.png'))
        for name,text in [('loginEmail','fixture@example.invalid'),('loginPassword','fixture_password'),('loginUsername','Fixture')]:visual(window,name).setProperty('text',text)
        click(window,'loginSubmit');QTest.qWait(30)
        assert auth.mode=='success' and card.height()==313
        assert pending[0]['email']=='fixture@example.invalid'
        window.grabWindow().save(str(artifacts/'signup_success_overlay.png'))
        click(window,'loginSuccessBack');QTest.qWait(20)
        service.online=False
        click(window,'loginSubmit');wait_for(lambda:auth.mode=='local' and not auth.running)
        assert auth.offline
        auth.setMode('local');QTest.qWait(20)
        assert card.height()==246,card.height()
        window.grabWindow().save(str(artifacts/'local_overlay.png'))
        complete=[];auth.accountSaved.connect(complete.append)
        click(window,'loginSubmit');QTest.qWait(20)
        assert complete==[True] and saves[-1]['auth_provider']=='local'
        auth.dismissRequested.emit();wait_for(lambda:not login.isVisible())
        model.presentationRequested.emit('message',{'title':'Fixture','text':'A short <b>confirmation</b>.','confirm':True})
        message=window.findChild(QObject,'launcherMessage')
        wait_for(lambda:message.property("visible") and message.property("opacity")==1)
        assert not message.findChild(QObject,'dialogScrollBar').property('visible'),'Short message leaked a scrollbar'
        commands=[];model.command.connect(lambda name,data:commands.append(name))
        QTest.keyClick(window,Qt.Key_Return);wait_for(lambda:not message.property("visible"))
        assert commands==['confirm']
        model.presentationRequested.emit('message',{'title':'Fatal Crash!','text':'<b>Fatal Crash!</b><br><br>Fixture error.','fatal':True,'danger':True,'width':380,'log':'Fixture traceback\nNo operation was performed.'})
        wait_for(lambda:message.property("visible") and message.property("opacity")==1)
        model.presentationRequested.emit('errorLog',{'text':'Fixture traceback\nNo operation was performed.'})
        log=window.findChild(QObject,'errorLogWindow')
        wait_for(lambda:log.property("visible") and log.property("opacity")==1)
        assert visual(log,'fatalLogText').property('readOnly')
        assert visual(log,'fatalLogText').property('text').startswith('Fixture traceback')
        popup_frame(window,log).save(str(artifacts/'error_log.png'))
        area=visual(log,'fatalLogText')
        QTest.mouseClick(window,Qt.LeftButton,Qt.NoModifier,area.mapToScene(area.boundingRect().center()).toPoint())
        QTest.qWait(30)
        assert engine._dialog_stack.stack==[message,log] and area.hasActiveFocus()
        QTest.keyClick(window,Qt.Key_Escape)
        wait_for(lambda:not log.property("visible"))
        QTest.keyClick(window,Qt.Key_Escape);wait_for(lambda:not message.property("visible"))
        assert not errors,errors
        print(json.dumps({'result':'passed','checks':['profile and avatar source dimensions','picker and form cancellation','explicit save preserves account identity','background sync','login source card dimensions in four modes','validation focus and shake','pending signup','offline fallback','local save','native confirmation keyboard','nested error log focus and Escape']}),flush=True)
    finally:
        wait_for(lambda:not profile.running and not auth.running)
        engine._dialog_stack.close();window.close();engine.deleteLater();app.processEvents()


if __name__=='__main__':main()
