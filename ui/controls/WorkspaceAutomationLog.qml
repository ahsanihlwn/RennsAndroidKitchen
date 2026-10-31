// SPDX-License-Identifier: MPL-2.0
import QtQuick
import QtQuick.Controls as C
DialogWindow {
    id: dialog; objectName: "workspaceAutomationLog"
    required property var workspace
    readonly property var backend: workspace.privateFeatures
    implicitWidth: 640; implicitHeight: 444; panelAlpha: 128/255; modal: false
    closePolicy: C.Popup.NoAutoClose
    function showLog() {text.text=backend.logs.join("\n");open()}
    function dismiss() {if(!backend.running)close()}
    RakLabel { x: 28; y: 26; height: 20; text: "Automation Log"; font.pixelSize: 13; font.weight: Font.DemiBold; color: dialog.c.FG }
    SmoothRectangle {
        x: 152; y: 27; width: 82; height: 18; radius: 4; color: dialog.backend.running?"#26f0b432":dialog.c.a15; border.width: 1; border.color: dialog.backend.running?"#66f0b432":dialog.c.a40
        RakLabel { anchors.centerIn: parent; text: dialog.backend.running?"Running…":"Finished"; font.pixelSize: 10; font.weight: Font.DemiBold; color: dialog.backend.running?"#f0c040":dialog.c.FG_BLUE }
    }
    SmoothRectangle { x: 28; y: 58; width: parent.width-56; height: 1; color: dialog.c.b06 }
    RakScrollView {
        colors: dialog.c
        id: scroll; x: 28; y: 71; width: parent.width-56; height: 300; clip: true
        C.TextArea { id: text; objectName: "automationLogText"; readOnly: true; selectByMouse: true; font.family: "Consolas"; font.pixelSize: 11; color: dialog.c.FG; selectionColor: dialog.c.a30; padding: 6; background: SmoothRectangle { color: dialog.c.BG_DARK; radius: 6; border.width: 1; border.color: dialog.c.b10 } }
    }
    SmoothRectangle { x: 28; y: 383; width: parent.width-56; height: 1; color: dialog.c.b06 }
    DialogButton { objectName: "automationLogOK"; anchors.right: parent.right; anchors.rightMargin: 28; y: 396; colors: dialog.c; primary: true; text: "OK"; enabled: !dialog.backend.running; onClicked: dialog.dismiss() }
    Connections { target: dialog.backend; function onLogAppended(line) {text.append(line);text.cursorPosition=text.length} }
}
