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
    readonly property bool isImage: (preview.kind === "image") || (preview.name && /\.(png|jpe?g|webp|gif|bmp|ico|svg)$/i.test(preview.name)) || (attachment && attachment.path && /\.(png|jpe?g|webp|gif|bmp|ico|svg)$/i.test(attachment.path))
    objectName: "agentAttachment_" + attachment.path

    width: isImage ? 56 : 174
    height: 56
    activeFocusOnTab: true

    Keys.onPressed: event => {
        if (event.key === Qt.Key_C && (event.modifiers & Qt.ControlModifier)) {
            chat.copyAttachment(attachment.path)
            event.accepted = true
        }
    }

    SmoothRectangle {
        anchors.fill: parent
        radius: 8
        color: chip.c.b04
        border.width: 1
        border.color: chip.activeFocus ? chip.c.a40 : chip.c.b09
    }

    MouseArea {
        anchors.fill: parent
        onPressed: chip.forceActiveFocus()
        hoverEnabled: true
        cursorShape: Qt.PointingHandCursor
        HoverTip {
            label: chip.preview.name + (chip.preview.sizeLabel ? " (" + chip.preview.sizeLabel + ")" : "") + "\n" + chip.tr.copy_attachment
            hovering: parent.containsMouse
        }
    }

    // Photo-only layout for images
    Item {
        anchors.fill: parent
        visible: chip.isImage

        SmoothRectangle {
            anchors.fill: parent
            anchors.margins: 3
            radius: 6
            color: chip.c.b03
            clip: true

            Image {
                id: imagePreview
                objectName: "agentAttachmentImage"
                anchors.fill: parent
                source: chip.isImage ? (chip.preview.previewUrl || (chip.attachment && chip.attachment.path ? (chip.attachment.path.indexOf("/") === 0 ? ("file://" + chip.attachment.path) : ("file:///" + chip.attachment.path.replace(/\\/g, "/"))) : "")) : ""
                sourceSize: Qt.size(112, 112)
                fillMode: Image.PreserveAspectCrop
                asynchronous: true
                visible: status === Image.Ready
            }
            Image {
                anchors.centerIn: parent
                width: 24; height: 24
                source: chip.preview.icon
                sourceSize: Qt.size(32, 32)
                fillMode: Image.PreserveAspectFit
                visible: imagePreview.status !== Image.Ready
            }
        }

        // Circular badge remove button for photo
        Rectangle {
            objectName: "agentAttachmentRemove_" + chip.attachment.path
            visible: chip.removable
            anchors.top: parent.top; anchors.topMargin: 4
            anchors.right: parent.right; anchors.rightMargin: 4
            width: 16; height: 16; radius: 8
            color: imgRemoveMouse.containsMouse ? (chip.c.RED_STATUS || "#ff4d4f") : Qt.rgba(0, 0, 0, 0.65)
            z: 10

            Text {
                anchors.centerIn: parent
                anchors.verticalCenterOffset: -1
                text: "×"
                font.pixelSize: 13
                font.weight: Font.Bold
                color: "#ffffff"
            }
            MouseArea {
                id: imgRemoveMouse
                anchors.fill: parent
                hoverEnabled: true
                cursorShape: Qt.PointingHandCursor
                onClicked: chip.chat.removeAttachment(chip.attachment.path)
            }
        }
    }

    // Standard layout for non-image files / folders
    Item {
        anchors.fill: parent
        visible: !chip.isImage

        SmoothRectangle {
            x: 7; y: 7; width: 42; height: 42; radius: 6; color: chip.c.b03
            Image {
                anchors.centerIn: parent
                width: 24; height: 24
                source: chip.preview.icon
                sourceSize: Qt.size(32, 32)
                fillMode: Image.PreserveAspectFit
            }
        }

        Column {
            x: 56; y: 11; width: parent.width - x - (chip.removable ? 22 : 8); spacing: 3
            RakLabel {
                width: parent.width
                text: chip.preview.name
                textFormat: Text.PlainText
                color: chip.c.FG
                font.pixelSize: 10
                elide: Text.ElideMiddle
            }
            RakLabel {
                width: parent.width
                text: chip.preview.sizeLabel || (chip.preview.kind === "folder" ? chip.tr.add_folder.replace("…", "") : chip.preview.kind)
                color: chip.c.FG_MUTED
                font.pixelSize: 9
                elide: Text.ElideRight
            }
        }

        ViewerToolButton {
            objectName: "agentAttachmentRemoveFile_" + chip.attachment.path
            visible: chip.removable
            x: parent.width - 21; y: 3; width: 18; height: 18
            colors: chip.c
            text: "×"
            font.pixelSize: 12
            toolTip: chip.tr.remove_attachment
            onClicked: chip.chat.removeAttachment(chip.attachment.path)
        }
    }
}
