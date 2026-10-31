# SPDX-License-Identifier: MPL-2.0
"""Run the actual original dragMoveEvent + paintEvent against a Quick model oracle."""
import ast
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace


def main():
    repo=Path(__file__).resolve().parents[1];sys.path.insert(0,str(repo))
    from PySide6.QtCore import Qt,QRect,QPointF
    from PySide6.QtGui import QImage,QColor,QPainter
    from ui.directory import DirectoryEntry,DirectoryModel
    from core.tree_entries import IS_FOLDER_ROLE,APKTOOL_LINK_ROLE
    source=repo.parent/'rennsandroidkitchen/main_ext/class_FileTree.py'
    module=ast.parse(source.read_text(encoding='utf-8-sig'))
    cls=next(node for node in module.body if isinstance(node,ast.ClassDef) and node.name=='FileTree')
    methods=[node for node in cls.body if isinstance(node,ast.FunctionDef) and node.name in ('dragMoveEvent','paintEvent')]
    # QTreeWidget's base paint is irrelevant to the overlay. Preserve every
    # statement from the actual custom paint, including its fallback branch.
    for method in methods:
        if method.name=='paintEvent':method.body=method.body[1:]
    scope={'os':os,'Qt':Qt,'QColor':QColor,'QPainter':QPainter,
           '_th':SimpleNamespace(_palette={'_accent_rgb':(62,207,142),'_border_rgb':(255,255,255)}),
           'FileTreeContextMixin':SimpleNamespace(_global_right_click_active=False),'APKTOOL_LINK_ROLE':APKTOOL_LINK_ROLE}
    exec(compile(ast.fix_missing_locations(ast.Module(body=methods,type_ignores=[])),str(source),'exec'),scope)
    with tempfile.TemporaryDirectory(prefix='rak-source-drag-') as temporary:
        base=Path(temporary);(base/'folder').mkdir();(base/'folder/a.txt').write_text('a');(base/'folder/b.txt').write_text('b');(base/'other.txt').write_text('c')
        def entry(path,folder,parent):
            item=DirectoryEntry([path.name,'',''],parent=parent)
            item.setData(0,Qt.UserRole,str(path));item.setData(0,IS_FOLDER_ROLE,folder)
            return item
        root=entry(base,True,None);folder=entry(base/'folder',True,root)
        a=entry(base/'folder/a.txt',False,folder);b=entry(base/'folder/b.txt',False,folder);other=entry(base/'other.txt',False,root)
        root.children=[folder,other];folder.children=[a,b];flat=[folder,a,b,other]
        quick=SimpleNamespace(_node=lambda node:node,_root=root,_history=[],base=str(base),mounts=None)
        class RefItem:
            def __init__(self,node):self.node=node
            def data(self,column,role):return self.node.data(column,role)
            def parent(self):return wrapped.get(id(self.node.parent)) if self.node.parent is not root else None
            def isExpanded(self):return expanded and self.node is folder
            def childCount(self):return len(self.node.children)
            def child(self,index):return wrapped[id(self.node.children[index])]
        wrapped={id(node):RefItem(node) for node in [root]+flat}
        image=QImage(300,112,QImage.Format_ARGB32_Premultiplied)
        timer=SimpleNamespace(isActive=lambda:False,start=lambda:None,stop=lambda:None)
        results=[]
        for target,src,expanded in ((folder,other.path,True),(folder,other.path,False),(folder,a.path,True),(a,other.path,False),(a,b.path,False)):
            image.fill(Qt.transparent)
            tree=SimpleNamespace(display_mode='tree',itemAt=lambda pos:wrapped[id(target)],
                topLevelItem=lambda index:wrapped[id(root.children[index])],topLevelItemCount=lambda:len(root.children),
                window=lambda:SimpleNamespace(drag_src_path=src),viewport=lambda:image,update=lambda:None,
                _drag_scroll_speed=0.,_drag_scroll_accum=0.,_drag_scroll_timer=timer,
                selectedItems=lambda:[],_acknowledge_paint_barrier=lambda:None,
                visualItemRect=lambda item:QRect(0,flat.index(item.node)*28,300,28))
            event=SimpleNamespace(position=lambda:QPointF(50,56),mimeData=lambda:SimpleNamespace(hasFormat=lambda kind:False),accept=lambda:None)
            scope['dragMoveEvent'](tree,event);scope['paintEvent'](tree,None)
            actual=[row for row in range(len(flat)) if image.pixelColor(150,row*28+14).alpha()==56]
            info=DirectoryModel.dragTarget(quick,target,src,expanded)
            first=next((row for row,node in enumerate(flat) if node.path==info.get('first')),None)
            last=next((row for row,node in enumerate(flat) if node.path==info.get('last')),None)
            expected=list(range(first,last+1)) if first is not None and last is not None else []
            assert actual==expected,(target.path,src,expanded,actual,info)
            results.append({'target':os.path.relpath(target.path,base),'source':os.path.relpath(src,base),'expanded':expanded,'paintedRows':actual})
        report={'result':'passed','actualSourceMethods':['dragMoveEvent','paintEvent'],'cases':results}
        out=repo/'build/qtquick_migration/drag';out.mkdir(parents=True,exist_ok=True)
        (out/'reference_logic.json').write_text(json.dumps(report,indent=2));print(json.dumps(report))


if __name__=='__main__':main()
