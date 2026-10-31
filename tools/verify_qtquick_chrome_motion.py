# SPDX-License-Identifier: MPL-2.0
"""Check live chrome against the same page/row pixels throughout animation."""
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
    from PySide6.QtCore import QMetaObject,Q_ARG,QPointF,QCoreApplication,QEvent
    from PySide6.QtWidgets import QApplication
    from PySide6.QtTest import QTest
    from core.fonts import apply_app_font
    from ui.presentation import LauncherModel
    from tools.quick_test_utils import visual
    app=QApplication([]);app.setQuitOnLastWindowClosed(False);apply_app_font(app,polish_widgets=False)
    rows=[dict(project_name=f'Project {i}',path=f'C:/fixture/{i}',port1='system',parentPath='C:/fixture') for i in range(24)]
    radius=17 if '--frost' in sys.argv else 6
    model=LauncherModel(palette='dark_orange',projects=rows,settings={'effects':'high','blurRadius':radius,'animations':False})
    # Saturated cards make faint stale/missing pixels measurable through real blur.
    model._color_cache={**model.colors,'BG2':'#e030d0','BG_SELECTED':'#e030d0'}
    engine,window=create_launcher(model)
    out=repo/'build/qtquick_migration/chrome_motion'/('frost' if radius==17 else 'clear');out.mkdir(parents=True,exist_ok=True)
    def seek(clock,moment):
        QMetaObject.invokeMethod(clock,'seek',Q_ARG('QVariant',moment));QTest.qWait(45)
    def channel(frame,y):return frame.pixelColor(500,y).red()
    def normalized(frame,steady,y):return (channel(frame,y)-30)/max(1,channel(steady,y)-30)
    def check_navigation(page,scroll_y,pairs):
        model.chooseSetting('animations',False);model.navigate(page);QTest.qWait(40)
        visual(window,'settingsScroll' if page=='settings' else 'projectList').setProperty('contentY',scroll_y)
        QTest.qWait(80);steady=window.grabWindow();steady.save(str(out/f'{page}_steady.png'))
        model.navigate('projects' if page=='settings' else 'settings');QTest.qWait(40)
        model.chooseSetting('animations',True);model.navigate(page)
        clock=visual(window,'pageMotionClock');duration=int(clock.property('duration'))
        samples=[];largest=0
        for moment in range(0,duration+1,40):
            seek(clock,moment);frame=window.grabWindow()
            values=[[round(normalized(frame,steady,y),4) for y in pair] for pair in pairs]
            largest=max(largest,*(abs(a-b) for a,b in values))
            assert all(abs(a-b)<.08 for a,b in values),('Glass and visible part of the same card used different phases',page,moment,values)
            samples.append((moment,values))
            if moment in (160,280,480):frame.save(str(out/f'{page}_{moment}.png'))
        assert all(max(v[p][0] for _,v in samples)>.98 for p in range(len(pairs))),('Page never finished revealing',page,samples)
        QMetaObject.invokeMethod(clock,'finish');QTest.qWait(40)
        # Also sample consecutive live frames, including interruption/reversal.
        # This catches stale GPU captures that seeking a settled frame can hide.
        model.navigate('projects' if page=='settings' else 'settings');QTest.qWait(35);model.navigate(page)
        live_max=0;count=0;deadline=time.monotonic()+4
        while visual(window,'pageMotion').property('active'):
            assert time.monotonic()<deadline,'Live page animation did not finish'
            QTest.qWait(10);frame=window.grabWindow();count+=1
            live_max=max(live_max,*(abs(normalized(frame,steady,a)-normalized(frame,steady,b)) for a,b in pairs))
        assert count>4 and live_max<.08,('Live glass capture lagged behind its content',page,count,live_max)
        return {'page':page,'maximumPhaseDifference':round(largest,4),'liveMaximumPhaseDifference':round(live_max,4),'liveFrames':count,'samples':samples}
    try:
        QTest.qWait(200)
        projects=visual(window,'projectList');projects.setProperty('contentY',92);QTest.qWait(80)
        before=window.grabWindow();before.save(str(out/'rows_before.png'))
        model.chooseSetting('animations',True);model.replaceProjects([])
        motion=visual(window,'projectListMotion');clock=visual(window,'projectCardsClock')
        assert motion.property('active')
        seek(clock,0);start=window.grabWindow();start.save(str(out/'rows_start.png'))
        probes=(40,200,686)
        starts=[(y,channel(before,y),channel(start,y)) for y in probes]
        assert all(abs(a-b)<=3 for _,a,b in starts),('Rows behind chrome vanished before their exit animation',starts)
        snapshots=[]
        for moment in (0,60,120,180,240):
            seek(clock,moment);frame=window.grabWindow();frame.save(str(out/f'rows_{moment}.png'))
            snapshots.append((moment,[channel(frame,y) for y in probes]))
        for index in range(len(probes)):
            values=[sample[1][index] for sample in snapshots]
            assert values[0]>values[2]>values[-1]+5,('Row did not fade continuously through chrome',probes[index],values)
        phase_error=max(max((value-30)/(starts[i][1]-30) for i,value in enumerate(values))-
                        min((value-30)/(starts[i][1]-30) for i,value in enumerate(values)) for _,values in snapshots)
        assert phase_error<.04,('Exit opacity differed behind chrome',phase_error)
        QMetaObject.invokeMethod(clock,'finish');QTest.qWait(50)
        model.chooseSetting('animations',False);model.replaceProjects(rows);QTest.qWait(80)
        model.navigate('settings');QTest.qWait(50)
        scroll=visual(window,'settingsScroll');scroll.setProperty('contentY',200);QTest.qWait(80)
        # Each pair samples one card both in the content and under real glass.
        # Include a row that straddles the header boundary on the Projects page.
        nav=[check_navigation('settings',100,((60,100),(600,660))),
             check_navigation('projects',40,((60,100),(600,628)))]
        # Disabling animation during a page change must clear every cover.
        model.navigate('settings');QTest.qWait(60);model.chooseSetting('animations',False);QTest.qWait(50)
        assert not visual(window,'pageMotion').property('active')
        report={'result':'passed','radius':radius,'exitStart':starts,'exitPhaseDifference':round(phase_error,4),'exit':snapshots,'navigation':nav}
        (out/'report.json').write_text(json.dumps(report,indent=2))
        print(json.dumps({**{k:v for k,v in report.items() if k not in ('exit','navigation')},'navigation':[{k:v for k,v in entry.items() if k!='samples'} for entry in nav]}),flush=True)
        assert not engine._diagnostics,engine._diagnostics
    finally:
        engine._appearance_captures.close();engine._dialog_stack.close();window.close();engine.deleteLater()
        QCoreApplication.sendPostedEvents(None,QEvent.DeferredDelete);app.processEvents()


if __name__=='__main__':main()
