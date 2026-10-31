// SPDX-License-Identifier: MPL-2.0
import QtQuick
import QtQuick.Window
import QtQuick.Controls as C
import "controls"

DialogWindow {
    id: dialog
    objectName: "errorLogWindow"
    property string logText: ""
    property bool copied: false
    property var commandTarget: model
    readonly property int logHeight: Math.min(330,Math.max(100,Screen.desktopAvailableHeight-180))
    implicitWidth: Math.min(680,Screen.desktopAvailableWidth-48); implicitHeight: logHeight+111
    title: "Fatal Crash! — Log"
    function showLog(text) { logText=text; copied=false; open() }
    RakLabel { x: 20; y: 18; height: 19; text: "<b>Fatal Crash! — Log</b>"; color: dialog.c.FG; font.pixelSize: 13 }
    RakScrollView {
        colors: dialog.c
        x: 20; y: 51; width: parent.width-40; height: dialog.logHeight
        C.TextArea {
            objectName: "fatalLogText"
            text: dialog.logText; readOnly: true; selectByMouse: true; wrapMode: TextEdit.NoWrap
            font.family: dialog.model.monospaceFamily; font.pixelSize: 13; color: dialog.c.FG
            padding: 8
            selectionColor: dialog.c.a40
            Keys.onEscapePressed: dialog.dismiss()
            background: SmoothRectangle { color: dialog.c.BG3; radius: 7; border.color: dialog.c.b14 }
        }
    }
    Row {
        anchors.right: parent.right; anchors.rightMargin: 20; y: 65+dialog.logHeight; spacing: 8
        DialogButton { objectName: "fatalLogCopy"; colors: dialog.c; primary: true; text: dialog.copied?dialog.s.dlg_btn_copied:dialog.s.dlg_btn_copy; onClicked: { dialog.commandTarget.dispatch("copyText",{text:dialog.logText}); dialog.copied=true; resetCopied.restart() } }
        DialogButton { colors: dialog.c; text: dialog.s.dlg_btn_close; onClicked: dialog.dismiss() }
    }
    Timer { id: resetCopied; interval: 1500; onTriggered: dialog.copied=false }
}
