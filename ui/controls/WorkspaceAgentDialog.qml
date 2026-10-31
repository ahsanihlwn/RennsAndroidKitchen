// SPDX-License-Identifier: MPL-2.0
import QtQuick
import QtQuick.Controls as C

DialogWindow {
    id: dialog
    agentProtected: true
    objectName: "workspaceAgentDialog"
    required property var workspace
    property var details: ({})
    property bool accepted: false
    property string answer: ""
    implicitWidth: details.kind==="review"?720:440
    implicitHeight: form.height+100
    function showRequest(data) {
        details=data;accepted=false;answer="";answerField.text="";open()
    }
    function submit() {accepted=true;dismiss()}
    onOpened: if(details.kind==="ask")answerField.forceActiveFocus(Qt.PopupFocusReason)
    onDismissed: workspace.dispatch("agentAnswer",{id:details.id,accepted:accepted,values:{answer:answer}})
    RakLabel {
        x: 20; y: 18; width: parent.width-40
        text: dialog.details.title || (dialog.details.kind==="review"?"Review changes":"Agent")
        textFormat: Text.PlainText; elide: Text.ElideRight
        color: dialog.c.FG; font.pixelSize: 14; font.weight: Font.DemiBold
    }
    Column {
        id: form; x: 20; y: 52; width: parent.width-40; spacing: 10
        RakLabel {
            width: parent.width; visible: text.length>0
            text: dialog.details.message || dialog.details.path || ""
            textFormat: Text.PlainText; wrapMode: Text.WrapAnywhere
            color: dialog.c.FG2; font.pixelSize: 12
        }
        Repeater {
            model: dialog.details.kind==="ask"?(dialog.details.choices || []):[]
            delegate: C.RadioButton {
                required property string modelData
                width: form.width; text: modelData; font.family: "Poppins"; font.pixelSize: 12
                palette.windowText: dialog.c.FG
                checked: dialog.answer===modelData
                onClicked: {dialog.answer=modelData;answerField.text=""}
            }
        }
        RakField {
            id: answerField; objectName: "workspaceAgentAnswer"
            visible: dialog.details.kind==="ask"; width: parent.width
            placeholderText: (dialog.details.choices || []).length?"Or enter an answer":"Your answer"
            fillColor: dialog.c.BG_INPUT; edgeColor: dialog.c.b18; focusEdgeColor: dialog.c.a55
            labelColor: dialog.c.FG; hintColor: dialog.c.placeholder
            onTextEdited: dialog.answer=text
            Keys.onReturnPressed: if(dialog.answer.trim().length)dialog.submit()
        }
        C.ScrollView {
            visible: dialog.details.kind==="review"
            width: parent.width; height: visible?Math.min(380,dialog.hostWindow.height-220):0
            clip: true
            C.TextArea {
                objectName: "workspaceAgentDiff"
                text: dialog.details.diff || "No text changes."
                textFormat: TextEdit.PlainText; readOnly: true; selectByMouse: true; wrapMode: TextEdit.NoWrap
                font.family: "Consolas"; font.pixelSize: 12; color: dialog.c.FG
                background: Rectangle {color: dialog.c.BG_INPUT;radius: 6}
            }
        }
        RakLabel {
            visible: dialog.details.kind==="review"; width: parent.width
            text: "Apply updates the editor buffer. Ctrl+S saves the file."
            color: dialog.c.FG2; font.pixelSize: 11; wrapMode: Text.WordWrap
        }
    }
    Row {
        anchors.right: parent.right; anchors.rightMargin: 20; y: form.y+form.height+18; spacing: 8
        DialogButton {
            objectName: "workspaceAgentCancel"; visible: dialog.details.kind!=="notify"
            colors: dialog.c; text: dialog.s.main_btn_cancel; onClicked: dialog.dismiss()
        }
        DialogButton {
            objectName: "workspaceAgentSubmit"; colors: dialog.c; primary: true
            enabled: dialog.details.kind!=="ask" || dialog.answer.trim().length>0
            text: dialog.details.kind==="review"?dialog.s.main_btn_apply:dialog.details.kind==="ask"?"Send":dialog.s.dlg_btn_ok
            onClicked: dialog.submit()
        }
    }
}
