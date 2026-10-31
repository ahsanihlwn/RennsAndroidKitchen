# SPDX-License-Identifier: MPL-2.0
"""Read-only captures of every launcher window from the authoritative source tree."""
import argparse
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import patch


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-root',type=Path,default=Path(__file__).resolve().parents[2]/'rennsandroidkitchen')
    parser.add_argument('--artifacts',type=Path,default=Path('build/qtquick_migration/original_windows'))
    args=parser.parse_args()
    source=args.source_root.resolve();artifacts=args.artifacts.resolve();artifacts.mkdir(parents=True,exist_ok=True)
    sys.path.insert(0,str(source));sys.dont_write_bytecode=True;os.chdir(source)
    from core.fonts import configure_text_rendering_env,apply_app_font
    configure_text_rendering_env()
    from PySide6.QtCore import Qt,QPoint
    from PySide6.QtWidgets import QApplication,QWidget,QAbstractButton,QLabel,QLineEdit,QDialog,QStackedLayout
    from PySide6.QtTest import QTest
    app=QApplication([]);app.setQuitOnLastWindowClosed(False);apply_app_font(app)
    from main_ext.class_ThemeUi import theme
    theme.load()
    from core import ui_lang
    ui_lang.init()
    from app import AppUI
    def capture(name,window):
        window.setAttribute(Qt.WA_DontShowOnScreen);window.show();QTest.qWait(30)
        window.grab().save(str(artifacts/(name+'.png')))
        rows=[]
        for widget in [window,*window.findChildren(QWidget)]:
            if not widget.isVisibleTo(window):continue
            point=widget.mapTo(window,QPoint())
            row={'class':type(widget).__name__,'name':widget.objectName(),'rect':[point.x(),point.y(),widget.width(),widget.height()],'font':widget.font().toString()}
            if isinstance(widget,(QAbstractButton,QLabel,QLineEdit)):row['text']=widget.text()
            rows.append(row)
        (artifacts/(name+'.json')).write_text(json.dumps(rows,indent=2,ensure_ascii=False),encoding='utf-8')
        print(name,window.width(),window.height(),flush=True)
    with patch.object(theme,'_animations_enabled',False):
        for module,name,cls in [('windows.license_window','license','LicenseWindow'),('windows.credit_window','credit','CreditWindow'),('app_ext.class_LoadingWindow','loading','LoadingWindow')]:
            window=getattr(__import__(module,fromlist=[cls]),cls)(*(['Fixture Project'] if name=='loading' else []))
            capture(name,window);window.close();window.deleteLater()
        import windows.dependency_window as dependency
        packages=[{'name':'fixture_'+state,'display':'Android Tool '+str(index+1),'author':'Example','license':'GPL-3.0' if index%2 else 'MIT','version':'1.0','state':state} for index,state in enumerate(('installed','not_installed','update_available','anomaly'))]
        with patch.object(dependency.DependencyWindow,'_start_fetch',lambda self:None),patch.object(dependency.DependencyWindow,'_start_wsl_detect',lambda self:self._on_wsl_detected({'installed':True,'detail':'WSL 2 is installed'})),patch.object(dependency,'DependencyManager',lambda **_:SimpleNamespace(list_packages=lambda:packages)):
            window=dependency.DependencyWindow();capture('packages_loading',window);window._on_registry_failed();capture('packages',window);window.close();window.deleteLater()
        import rennsproject.app_ui as accounts
        fixture={'username':'Fixture','employee_status':'Tester','avatar':'001','dev_id':'LOCAL_DEV','id':'LOCAL_ID'}
        with patch.object(accounts,'ensure_account_file',return_value=fixture):
            owner=AppUI(offscreen_mode=True);owner.setAttribute(Qt.WA_DontShowOnScreen);owner.show()
        def profile_exec(dialog):
            capture('profile',dialog)
            stack=dialog.findChild(QStackedLayout)
            stack.setCurrentIndex(1);capture('avatars',dialog)
            dialog.hide();return QDialog.Rejected
        with patch.object(QDialog,'exec',profile_exec),patch('app_ext.class_BlurOverlay.install_window_fade',lambda *a,**kw:None):
            owner.open_profile_editor()
        owner.close();owner.deleteLater()
        from rennsproject.core.login_window import LoginWindow
        window=LoginWindow();capture('login',window);window.set_signup();capture('signup',window);window.go_local();capture('local',window);window.show_success('fixture@example.invalid');capture('signup_success',window);window.close();window.deleteLater()
    app.processEvents()


if __name__=='__main__':main()
