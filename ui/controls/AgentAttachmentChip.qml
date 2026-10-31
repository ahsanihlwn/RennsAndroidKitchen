// SPDX-License-Identifier: MPL-2.0
import QtQuick

FocusScope {
    id: chip
    required property var attachment
    required property var chat
    required property var preferences
    property bool removable: false
    readonly property var c: preferences.colors
    readonly property var tr: chat.labels(preferences.strings.languageKey)
    readonly property var preview: chat.attachmentPreview(attachment)
    objectName: "agentAttachment_"+attachment.path
    width: 174; height: 60; activeFocusOnTab: true
    Keys.onPressed: event=>{if(event.key===Qt.Key_C && (event.modifiers & Qt.ControlModifier)){chat.copyAttachment(attachment.path);event.accepted=true}}
    SmoothRectangle { anchors.fill: parent; radius: 9; color: chip.c.b04; border.width: 1; border.color: chip.activeFocus?chip.c.a40:chip.c.b09 }
    MouseArea { anchors.fill: parent; onPressed: chip.forceActiveFocus(); hoverEnabled: true; cursorShape: Qt.PointingHandCursor
        HoverTip { label: chip.attachment.path+"\n"+chip.tr.copy_attachment; hovering: parent.containsMouse }
    }
    SmoothRectangle { x: 7; y: 8; width: 44; height: 44; radius: 6; color: chip.c.b03
        Image { id: thumbnail; objectName: "agentAttachmentImage"; anchors.fill: parent; anchors.margins: 1; source: chip.preview.previewUrl || ""; sourceSize: Qt.size(88,88); fillMode: Image.PreserveAspectFit; asynchronous: true; visible: status===Image.Ready }
        Image { anchors.centerIn: parent; width: 25; height: 25; source: chip.preview.icon; sourceSize: Qt.size(32,32); fillMode: Image.PreserveAspectFit; visible: thumbnail.status!==Image.Ready }
    }
    Column { x: 59; y: 12; width: parent.width-x-(chip.removable?22:8); spacing: 3
        RakLabel { width: parent.width; text: chip.preview.name; textFormat: Text.PlainText; color: chip.c.FG; font.pixelSize: 10; elide: Text.ElideMiddle }
        RakLabel { width: parent.width; text: chip.preview.sizeLabel || (chip.preview.kind==="folder"?chip.tr.add_folder.replace("…",""):chip.preview.kind); color: chip.c.FG_MUTED; font.pixelSize: 9; elide: Text.ElideRight }
    }
    ViewerToolButton { objectName: "agentAttachmentRemove_"+chip.attachment.path; visible: chip.removable; x: parent.width-21; y: 3; width: 18; height: 18; colors: chip.c; text: "×"; font.pixelSize: 12; toolTip: chip.tr.remove_attachment; onClicked: chip.chat.removeAttachment(chip.attachment.path) }
}
