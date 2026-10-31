# SPDX-License-Identifier: MPL-2.0
"""Exercise live header/footer glass over scrolling cards in High mode."""
import json
from pathlib import Path
import sys


def main():
    repo=Path(__file__).resolve().parents[1];sys.path.insert(0,str(repo))
    from core.fonts import configure_text_rendering_env,apply_app_font
    configure_text_rendering_env()
    from PySide6.QtCore import QObject,Qt,QPointF
    from PySide6.QtGui import QGuiApplication,QColor
    from PySide6.QtQuick import QQuickItem
    from PySide6.QtTest import QTest
    from ui.presentation import LauncherModel
    from ui.runtime import create_launcher
    app=QGuiApplication([]);apply_app_font(app)
    rows=[dict(project_name=f'Project {i}',path=f'C:/fixture/{i}',port1='system',parentPath='C:/fixture') for i in range(24)]
    model=LauncherModel(palette='dark_green',profile={'name':'Fixture','subtitle':'Developer'},projects=rows,settings={'effects':'high','animations':False})
    engine,window=create_launcher(model,visible=False);errors=[]
    engine.warnings.connect(lambda values:errors.extend(error.toString() for error in values))
    window.setFlags(window.flags()|Qt.Tool|Qt.WindowDoesNotAcceptFocus);window.show();QTest.qWait(200)
    artifacts=repo/'build/qtquick_migration/chrome';artifacts.mkdir(parents=True,exist_ok=True)
    def obj(name):
        result=window.findChild(QObject,name);assert result is not None,name;return result
    def colored(frame,x,y,channel):
        c=frame.pixelColor(x,y);values=(c.red(),c.green(),c.blue())
        return values[channel]>max(value for i,value in enumerate(values) if i!=channel)+25
    try:
        view=obj('projectList');view.setProperty('contentY',72);QTest.qWait(50)
        pending=[window.contentItem()];cards={}
        while pending:
            item=pending.pop();pending.extend(item.childItems())
            if item.property('presentationCopy') is False:cards[item.property('index')]=item
        cards[0].setProperty('color',QColor('red'));cards[8].setProperty('color',QColor('blue'));QTest.qWait(100)
        frame=window.grabWindow()
        assert colored(frame,500,50,0),'Header did not blur the card scrolling behind it'
        assert colored(frame,500,660,2),'Footer did not blur the card behind it'
        frame.save(str(artifacts/'projects_high.png'))
        cards[0].setProperty('color',QColor('blue'));QTest.qWait(75)
        assert colored(window.grabWindow(),500,50,2),'Header texture froze'
        for name in ('projectsHeaderGlass','projectsFooterGlass'):
            assert obj(name).property('rendering') and obj(name).property('live')
        model.chooseSetting('effects','medium');QTest.qWait(50)
        assert not obj('projectsHeaderGlass').property('rendering')
        assert window.grabWindow().pixelColor(500,50)==QColor(model.colors['BG'])
        model.chooseSetting('effects','high');model.navigate('settings');QTest.qWait(75)
        obj('settingsScroll').setProperty('contentY',65);QTest.qWait(50)
        profile=obj('settingsProfileCard');profile.setProperty('color',QColor('red'))
        lower=next(item for item in profile.parentItem().childItems() if item.inherits('QQuickRectangle') and item.mapToScene(QPointF()).y()<660<item.mapToScene(QPointF()).y()+item.height())
        lower.setProperty('color',QColor('blue'));QTest.qWait(100)
        frame=window.grabWindow()
        assert colored(frame,500,40,0),'Settings header has no live source'
        assert colored(frame,500,660,2),'Settings footer has no live source'
        frame.save(str(artifacts/'settings_high.png'))
        for name in ('settingsHeaderGlass','settingsFooterGlass'):assert obj(name).property('rendering')
        model.chooseSetting('effects','low');QTest.qWait(30)
        assert not obj('settingsHeaderGlass').property('rendering')
        assert not errors,errors
        print(json.dumps({'result':'passed','checks':['High live project header/footer','automatic texture updates','High settings header/footer','Medium/Low keep flat chrome','no feedback warnings']}),flush=True)
    finally:
        engine._dialog_stack.close();window.close();engine.deleteLater();app.processEvents()


if __name__=='__main__':main()
