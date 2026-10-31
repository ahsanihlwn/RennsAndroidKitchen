// SPDX-License-Identifier: MPL-2.0
import QtQuick
import QtQuick.Controls as C

DialogWindow {
    id: panel
    objectName: "workspaceInboxPanel"
    required property var workspace
    readonly property var inbox: workspace.agentInbox
    modal: false
    closePolicy: C.Popup.CloseOnEscape|C.Popup.CloseOnPressOutside
    implicitWidth: 440; implicitHeight: body.height+72
    x: Math.max(12,hostWindow.width-width-12); y: 12
    function showInbox() {open()}
    onOpened: {inbox.setShowing(true);composer.forceActiveFocus(Qt.PopupFocusReason)}
    onDismissed: inbox.setShowing(false)
    function sendMessage() {if(inbox.send())composer.forceActiveFocus(Qt.OtherFocusReason)}
    RakLabel {x: 18;y: 17;text: "Agent inbox";font.pixelSize: 14;font.weight: Font.DemiBold;color: panel.c.FG}
    WorkspaceIconButton {
        objectName: "workspaceInboxClose";x: parent.width-width-12;y: 10
        preferences: panel.model;iconKey: "close_rounded";tint: panel.c.FG_MUTED
        iconWidth: 13;iconHeight: 13;toolTip: "Close";onClicked: panel.dismiss()
    }
    Column {
        id: body;x: 18;y: 49;width: parent.width-36;spacing: 10
        RakLabel {
            width: parent.width;text: panel.inbox.availability;textFormat: Text.PlainText
            font.pixelSize: 11;color: panel.c.FG2;wrapMode: Text.WordWrap
        }
        DialogButton {objectName: "workspaceInboxSetup";visible: panel.inbox.availability.indexOf("Waiting for")===0;colors: panel.c;text: "Copy setup instruction for agent";onClicked: panel.inbox.copyInstructions()}
        C.ScrollView {
            width: parent.width;height: Math.min(96,Math.max(56,messages.contentHeight));clip: true
            ListView {
                id: messages;objectName: "workspaceInboxMessages";spacing: 5;model: panel.inbox.messages
                boundsBehavior: Flickable.StopAtBounds
                delegate: Item {
                    id: row;required property var modelData
                    width: messages.width;height: 53
                    SmoothRectangle {
                        anchors.fill: parent;radius: 6
                        color: panel.inbox.detail.id===row.modelData.id?panel.c.a12:panel.c.b04
                        border.width: 1;border.color: panel.inbox.detail.id===row.modelData.id?panel.c.a25:panel.c.b08
                    }
                    RakLabel {
                        x: 9;y: 7;width: parent.width-18;text: row.modelData.label;textFormat: Text.PlainText
                        font.pixelSize: 12;color: panel.c.FG;elide: Text.ElideRight
                    }
                    RakLabel {
                        x: 9;y: 29;width: parent.width-18
                        text: row.modelData.state_label+(row.modelData.agent?" · "+row.modelData.agent:"")
                        textFormat: Text.PlainText;font.pixelSize: 10
                        color: row.modelData.state==="failed"?panel.c.RED_STATUS:panel.c.FG2;elide: Text.ElideRight
                    }
                    MouseArea {anchors.fill: parent;onClicked: panel.inbox.select(row.modelData.id)}
                }
                RakLabel {
                    anchors.centerIn: parent;visible: messages.count===0;text: "Your requests and agent replies appear here."
                    width: parent.width-20;wrapMode: Text.WordWrap;horizontalAlignment: Text.AlignHCenter
                    font.pixelSize: 11;color: panel.c.FG2
                }
            }
        }
        Column {
            width: parent.width;spacing: 6;visible: !!panel.inbox.detail.id
            Row {
                width: parent.width;spacing: 8
                RakLabel {
                    width: parent.width-130;text: panel.inbox.detail.state_label || "";textFormat: Text.PlainText
                    font.pixelSize: 11;font.weight: Font.DemiBold;color: panel.c.FG
                }
                DialogButton {
                    objectName: "workspaceInboxCancel";colors: panel.c
                    visible: ["queued","claimed","running"].indexOf(panel.inbox.detail.state)>=0
                    enabled: !panel.inbox.detail.cancel_requested
                    text: panel.inbox.detail.state==="queued"?"Cancel":"Request stop"
                    onClicked: panel.inbox.cancel(panel.inbox.detail.id)
                }
            }
            C.ScrollView {
                width: parent.width;height: 88;clip: true
                C.TextArea {
                    objectName: "workspaceInboxResult";readOnly: true;selectByMouse: true
                    text: (panel.inbox.detail.message || "")+"\n\n"+(panel.inbox.detail.answer || panel.inbox.detail.status || "")
                    textFormat: TextEdit.PlainText;wrapMode: TextEdit.Wrap
                    font.family: "Poppins";font.pixelSize: 12;color: panel.c.FG
                    background: SmoothRectangle {color: panel.c.BG_INPUT;radius: 6}
                }
            }
            Repeater {
                model: (panel.inbox.detail.attachments || []).concat((panel.inbox.detail.paths || []).filter(path=>!(panel.inbox.detail.attachments || []).some(row=>row.path===path)).map(path=>({path:path,label:path.replace(/\\/g,"/").split("/").pop()})))
                delegate: ActionButton {
                    required property var modelData
                    width: body.width;height: 27;colors: panel.c;text: modelData.label;font.pixelSize: 10
                    onClicked: panel.inbox.openPath(modelData.path)
                }
            }
            RakLabel {
                visible: !!panel.inbox.detail.cancel_requested && ["claimed","running"].indexOf(panel.inbox.detail.state)>=0
                width: parent.width;text: "Waiting for the agent to acknowledge stopping. An active device command is not stopped forcibly."
                font.pixelSize: 10;color: panel.c.FG2;wrapMode: Text.WordWrap
            }
        }
        SmoothRectangle {width: parent.width;height: 1;color: panel.c.b10}
        Row {
            spacing: 8
            RakLabel {text: "New request";font.pixelSize: 12;font.weight: Font.DemiBold;color: panel.c.FG}
            DialogButton {colors: panel.c;text: "Use current selection";onClicked: panel.inbox.refreshAttachments()}
            WorkspaceIconButton {
                visible: panel.inbox.attachments.length>0;preferences: panel.model;iconKey: "close_rounded";tint: panel.c.FG2
                iconWidth: 12;iconHeight: 12;toolTip: "Remove attachments";onClicked: panel.inbox.removeAttachments()
            }
        }
        RakLabel {
            objectName: "workspaceInboxAttachments";width: parent.width
            text: panel.inbox.attachments.map(row=>row.label).join("\n") || "Project context"
            textFormat: Text.PlainText;wrapMode: Text.WordWrap;font.pixelSize: 10;color: panel.c.FG2
        }
        C.ScrollView {
            width: parent.width;height: 76;clip: true
            C.TextArea {
                id: composer;objectName: "workspaceInboxComposer"
                text: panel.inbox.draft;placeholderText: "Ask the agent to work on this…"
                textFormat: TextEdit.PlainText;wrapMode: TextEdit.Wrap;selectByMouse: true
                font.family: "Poppins";font.pixelSize: 12;color: panel.c.FG;placeholderTextColor: panel.c.FG2
                onTextChanged: panel.inbox.setDraft(text)
                background: SmoothRectangle {color: panel.c.BG_INPUT;radius: 6;border.width: 1;border.color: composer.activeFocus?panel.c.a45:panel.c.b14}
                Keys.onReturnPressed: event => {if(event.modifiers & Qt.ControlModifier){panel.sendMessage();event.accepted=true}else event.accepted=false}
            }
        }
        RakLabel {visible: text.length>0;width: parent.width;text: panel.inbox.error;textFormat: Text.PlainText;font.pixelSize: 11;color: panel.c.RED_STATUS;wrapMode: Text.WordWrap}
        Row {
            width: parent.width;spacing: 10
            RakLabel {width: parent.width-110;text: "Ctrl+Enter to send";font.pixelSize: 10;color: panel.c.FG2}
            DialogButton {
                objectName: "workspaceInboxSend";colors: panel.c;primary: true;text: "Send to agent"
                enabled: panel.inbox.draft.trim().length>0;onClicked: panel.sendMessage()
            }
        }
    }
}
