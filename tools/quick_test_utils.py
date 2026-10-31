# SPDX-License-Identifier: MPL-2.0
"""Read frames and visual children of Qt Quick Item popups in GUI checks."""
def popup_frame(window, popup):
    return window.grabWindow().copy(*(round(popup.property(key)) for key in ('x','y','width','height')))


def visual(owner, name):
    from PySide6.QtCore import QObject
    root=owner.contentItem() if hasattr(owner,'contentItem') else owner.property('contentItem')
    pending=[root]
    hidden=[]
    while pending:
        item=pending.pop()
        if item.objectName()==name:
            if item.isVisible():return item
            hidden.append(item)
        pending.extend(item.childItems())
    # Replaced delegates can remain in the QObject hierarchy until deferred
    # deletion. Input must target the item attached to the current scene.
    if hidden:return hidden[0]
    child=owner.findChild(QObject,name)
    if child is not None:return child
    stack=owner.property('dialogStack')
    if stack is not None:
        for dialog in stack.dialogs+getattr(stack,'popups',[]):
            if dialog.objectName()==name:return dialog
    raise AssertionError(name)
