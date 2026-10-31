// SPDX-License-Identifier: MPL-2.0
import QtQuick
import "controls"

DialogWindow {
    id: dialog
    agentProtected: true
    objectName: "launcherMessage"
    property var details: ({})
    property bool accepted: false
    property var commandTarget: model
    implicitWidth: Math.min(details.width || 320,transientParent?transientParent.width-24:320)
    implicitHeight: bodyText.height+78
    title: details.title || ""
    function showMessage(data) { details=data; accepted=false; open() }
    function acceptMessage() { accepted=true; dismiss() }
    onDismissed: {
        if (details.confirm) commandTarget.dispatch(accepted?"confirm":"cancelConfirmation",{})
        else if (details.fatal) commandTarget.dispatch("fatalDismissed",{})
        else if (details.notifyDismiss) commandTarget.dispatch("messageDismissed",{})
    }
    RakLabel {
        id: bodyText
        x: 20; y: 18; width: parent.width-40; height: implicitHeight
        font.pixelSize: 13; color: dialog.c.FG
        text: dialog.details.text || ""; textFormat: Text.AutoText; wrapMode: Text.WordWrap
    }
    Row {
        anchors.right: parent.right; anchors.rightMargin: 20
        y: bodyText.y+bodyText.height+14; spacing: 8
        DialogButton { objectName: "fatalSeeLog"; visible: !!dialog.details.log; colors: dialog.c; primary: true; text: dialog.s.dlg_btn_see_log; onClicked: dialog.commandTarget.dispatch("errorLog",{text:dialog.details.log}) }
        DialogButton {
            objectName: "launcherMessageCancel"
            visible: !!dialog.details.confirm; colors: dialog.c
            text: dialog.details.cancelLabel || dialog.s.dlg_btn_cancel
            onClicked: dialog.dismiss()
        }
        DialogButton {
            objectName: "launcherMessageAccept"
            colors: dialog.c; primary: !danger; danger: !!dialog.details.danger
            text: dialog.details.acceptLabel || (dialog.details.fatal?dialog.s.dlg_btn_close:dialog.details.confirm?dialog.s.dlg_btn_yes:dialog.s.dlg_btn_ok)
            onClicked: dialog.acceptMessage()
        }
    }
    Shortcut { sequence: "Return"; context: Qt.WindowShortcut; enabled: dialog.visible && dialog.active; onActivated: dialog.acceptMessage() }
    Shortcut { sequence: "Enter"; context: Qt.WindowShortcut; enabled: dialog.visible && dialog.active; onActivated: dialog.acceptMessage() }
}
