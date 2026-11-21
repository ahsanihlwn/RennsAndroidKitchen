# SPDX-License-Identifier: MPL-2.0
"""C++ Qt item storage for Quick's flattened tree; no QWidget involvement."""
from PySide6.QtCore import Qt,QModelIndex,QSignalBlocker
from PySide6.QtGui import QStandardItem,QStandardItemModel

ENTRY_ROLE=int(Qt.UserRole)+1020
NODE_ROLE=int(Qt.UserRole)+5000
DUMMY_ROLE=int(Qt.UserRole)+5001


class _LazyItemModel(QStandardItemModel):
    """QStandardItemModel that creates EntryPresentation on first read."""
    def __init__(self,view,parent=None):
        super().__init__(parent);self._view=view
    def data(self,index,role=Qt.DisplayRole):
        if role==ENTRY_ROLE:
            value=super().data(index,role)
            if value is not None:return value
            node_id=super().data(index,NODE_ROLE)
            if node_id is None:return None
            node=self._view.nodes.get(node_id)
            if node is None:return None
            return self._view.owner._presentation_for(node)
        return super().data(index,role)


class DirectoryView:
    def __init__(self,owner):
        self.owner=owner;self.enabled=False;self.nodes={};self.model=_LazyItemModel(self,owner);self.reset()
    def reset(self):
        for node in self.nodes.values():
            node.view_items=None;node.view_dummy=False
            if node.presentation is not None:node.presentation.retire();node.presentation=None
        self.nodes.clear()
        self.model.clear();self.model.setColumnCount(3)
        self.model.setItemRoleNames({ENTRY_ROLE:b'entry',DUMMY_ROLE:b'loadingPlaceholder'})
    def enable(self):
        if not self.enabled:
            self.enabled=True;self.append(self.owner._root,list(self.owner._root.children))
        return self.model
    def parent_item(self,node):
        return self.model.invisibleRootItem() if node is self.owner._root else node.view_items[0]
    def items(self,node,*,dummy=False):
        if node.view_items is not None:return node.view_items
        self.nodes[id(node)]=node
        items=[QStandardItem(text) for text in node.texts];node.view_items=items
        for column,item in enumerate(items):
            item.setData(id(node),NODE_ROLE)
            if dummy:item.setData(True,DUMMY_ROLE)
            flags=Qt.NoItemFlags if dummy or node.empty else self.owner.flags(self.owner._index(node,column))
            if not self.owner._may_have_children(node):flags|=Qt.ItemNeverHasChildren
            item.setFlags(flags)
        if node.children:
            for child in node.children:items[0].appendRow(self.items(child))
        elif self.owner._may_have_children(node) and not node.loaded:self.ensure_dummy(node)
        return items
    def ensure_dummy(self,node):
        parent=self.parent_item(node)
        if parent.rowCount():return
        dummy=self.owner._new_view_dummy(node)
        parent.appendRow(self.items(dummy,dummy=True));node.view_dummy=True
    def clear_dummy(self,node):
        if not node.view_dummy:return
        parent=self.parent_item(node)
        for row in range(parent.rowCount()):
            item=parent.child(row,0)
            if item.data(DUMMY_ROLE):
                dummy=self.nodes.pop(item.data(NODE_ROLE),None)
                if dummy is not None and dummy.presentation is not None:dummy.presentation.retire();dummy.presentation=None
                parent.removeRow(row);break
        node.view_dummy=False
    def append(self,parent,nodes):
        if not self.enabled:return
        target=self.parent_item(parent)
        if nodes:
            rows=[self.items(node) for node in nodes]
            first=target.rowCount();target.insertRows(first,len(rows))
            # Insert one native range, then populate its cells before returning
            # to QML. One data notification avoids a flattened-tree rebuild
            # for every individual cell/row in a large directory.
            with QSignalBlocker(self.model):
                for row,items in enumerate(rows,first):
                    for column,item in enumerate(items):target.setChild(row,column,item)
            self.model.dataChanged.emit(target.child(first,0).index(),target.child(first+len(rows)-1,2).index(),[])
        if nodes:self.clear_dummy(parent)
    def insert(self,parent,row,node):
        if not self.enabled:return
        self.parent_item(parent).insertRow(row+int(parent.view_dummy),self.items(node));self.clear_dummy(parent)
    def remove(self,parent,row):
        if not self.enabled:return
        target=self.parent_item(parent)
        if parent is not self.owner._root and target.rowCount()==1 and self.owner._may_have_children(parent):
            dummy=self.owner._new_view_dummy(parent)
            target.appendRow(self.items(dummy,dummy=True));parent.view_dummy=True
        self._forget_item(target.child(row,0));target.removeRow(row)
    def _forget_item(self,item):
        if item is None:return
        for row in range(item.rowCount()):self._forget_item(item.child(row,0))
        node=self.nodes.pop(item.data(NODE_ROLE),None)
        if node is not None:
            node.view_items=None
            if node.presentation is not None:node.presentation.retire();node.presentation=None
    def clear_children(self,node):
        if not self.enabled or node.view_items is None:return
        parent=self.parent_item(node)
        for row in range(parent.rowCount()):self._forget_item(parent.child(row,0))
        parent.removeRows(0,parent.rowCount());node.view_dummy=False
    def discard_prepared(self,nodes):
        for node in nodes:
            if node.view_items is not None and not node.view_items[0].index().isValid():
                self._forget_item(node.view_items[0])
    def move(self,parent,old,new):
        if self.enabled:
            target=self.parent_item(parent);items=target.takeRow(old);target.insertRow(new,items)
    def sync(self,node):
        if not self.enabled or node.view_items is None:return
        for column,item in enumerate(node.view_items):
            item.setText(node.texts[column]);flags=self.owner.flags(self.owner._index(node,column))
            if not self.owner._may_have_children(node):flags|=Qt.ItemNeverHasChildren
            item.setFlags(flags)
        if self.owner._may_have_children(node) and not node.loaded:self.ensure_dummy(node)
    def index(self,node,column=0):
        return QModelIndex() if node is self.owner._root or node.view_items is None else node.view_items[column].index()
