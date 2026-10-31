# SPDX-License-Identifier: MPL-2.0
"""Compare Quick scene plans with the actual animation classes in the original tree."""
import argparse
import importlib.util
import json
from pathlib import Path
import sys


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--original',type=Path,default=Path(r'C:\Users\ahsanihlwn\PROJ\rennsandroidkitchen'))
    args=parser.parse_args()
    repo=Path(__file__).resolve().parents[1]
    sys.dont_write_bytecode=True
    from PySide6.QtCore import Qt, QSize, QRect
    from PySide6.QtGui import QColor, QPixmap
    from PySide6.QtQml import QJSEngine
    from PySide6.QtWidgets import QApplication, QListWidget, QListWidgetItem, QWidget
    app=QApplication([])
    js=QJSEngine()
    program=(repo/'ui/motion.js').read_text().replace('.pragma library','')
    result=js.evaluate('var Qt={point:(x,y)=>({x:x,y:y}),rect:(x,y,w,h)=>({x:x,y:y,width:w,height:h})};\n'+program)
    assert not result.isError(),result.toString()

    def load(name):
        path=args.original/'app_ext'/('class_'+name+'.py')
        spec=importlib.util.spec_from_file_location('_reference_'+name,path)
        module=importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return getattr(module,name)

    Project=load('ProjectCardsTransition')
    Disk=load('DiskCardsTransition')
    checked=[]
    for label,before,after,scroll in [
        ('alternate drive hide',list(range(12)),list(range(1,12,2)),0),
        ('drive return',list(range(1,12,2)),list(range(12)),0),
        ('mixed drive switch',list(range(0,12,2)),list(range(1,12,2)),0),
        ('delete first',list(range(12)),list(range(1,12)),0),
        ('scroll clamp brings old offscreen rows back',list(range(12)),[0,1,4,6,7],368),
    ]:
        view=QListWidget()
        view.setAttribute(Qt.WA_DontShowOnScreen)
        view.setStyleSheet('QListWidget {border: none; padding: 0;}')
        view.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        view.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        view.setVerticalScrollMode(QListWidget.ScrollPerPixel)
        view.resize(1008,520)
        items=[]
        for index in range(12):
            item=QListWidgetItem(str(index),view)
            item.setSizeHint(QSize(1008,74))
            item.setHidden(index not in before)
            items.append(item)
        view.show(); app.processEvents(); view.doItemsLayout()
        view.verticalScrollBar().setValue(scroll)
        actual_scroll=view.verticalScrollBar().value()
        def snapshot(project_list,item):
            image=QPixmap(project_list.visualItemRect(item).size())
            image.fill(QColor(items.index(item)+1,0,0))
            return project_list.visualItemRect(item),image
        changes=[(item,index not in after) for index,item in enumerate(items) if (index in before)!=(index in after)]
        reference=Project(view,changes,int(Qt.UserRole)+11,snapshot)
        original=[]
        for (source,target,image,kind),delay in zip(reference._scene,reference._move_delays):
            original.append({'path':str(image.toImage().pixelColor(0,0).red()-1),'sourceY':source.y(),'targetY':target.y(),'kind':kind,'delay':delay})
        args_js=json.dumps([[{'path':str(i)} for i in before],[{'path':str(i)} for i in after],view.viewport().width(),view.viewport().height(),actual_scroll])
        plan=js.evaluate('projectScene.apply(null,'+args_js+')').toVariant()
        quick=[{'path':row['row']['path'],**{key:row[key] for key in ('sourceY','targetY','kind','delay')}} for row in plan['rows']]
        assert original==quick,(label,original,quick)
        assert reference._clock.duration()==plan['duration'],(label,reference._clock.duration(),plan)
        assert reference._enter_delay==plan['enterDelay']
        reference.stop(); view.close(); app.processEvents()
        checked.append(label)

    for label,before,after in [('disk unplug',[0,1,2,3],[0,3]),('disk plug',[0,3],[0,1,2,3]),('disk replacement',[0,1,2],[0,2,3])]:
        host=QWidget();host.setAttribute(Qt.WA_DontShowOnScreen);host.resize(132,520);host.show()
        source={}
        for index,key in enumerate(before):
            image=QPixmap(128,44);image.fill(QColor(key+1,0,0))
            source[str(key)]=(QRect(0,index*52,128,44),image)
        cards={}
        for index,key in enumerate(after):
            widget=QWidget(host);widget.setGeometry(0,index*52,128,44)
            widget.setStyleSheet('background: rgb('+str(key+1)+',0,0);');widget.show()
            cards[str(key)]={'widget':widget}
        app.processEvents()
        reference=Disk(host,source,cards)
        original=[{'source':[a.x(),a.y()],'target':[b.x(),b.y()],'kind':kind,'delay':delay} for a,b,_,kind,delay in reference._scene]
        data=json.dumps([[{'key':str(i)} for i in before],[{'key':str(i)} for i in after]])
        plan=js.evaluate('(function(a){return diskScene(a[0],a[1],i=>Qt.point(0,i*52),Qt.rect(0,0,132,520));})('+data+')').toVariant()
        quick=[{'source':[row['source']['x'],row['source']['y']],'target':[row['target']['x'],row['target']['y']],'kind':row['kind'],'delay':row['delay']} for row in plan['rows']]
        assert original==quick,(label,original,quick)
        assert reference._clock.duration()==plan['duration']
        reference.stop();host.close();app.processEvents();checked.append(label)
    print(json.dumps({'result':'passed','reference':str(args.original),'checks':checked}),flush=True)


if __name__=='__main__':
    main()
