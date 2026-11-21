// SPDX-License-Identifier: MPL-2.0
import QtQuick

Item {
    id: caption
    required property var button
    property bool moving: false
    implicitWidth: row.implicitWidth
    implicitHeight: row.implicitHeight
    Row {
        id: row
        anchors.verticalCenter: parent.verticalCenter
        x: caption.button.leftAligned ? 0 : (parent.width-width)/2
        spacing: ((caption.button.iconKey && caption.button.iconKey.length > 0) || (caption.button.iconSource.toString().length > 0)) && caption.button.text.length ? caption.button.spacing : 0
        Loader {
            active: !!(caption.button.iconKey && caption.button.iconKey.length > 0)
            visible: active
            width: active ? (caption.button.iconWidth || 16) : 0
            height: active ? (caption.button.iconWidth || 16) : 0
            anchors.verticalCenter: parent.verticalCenter
            sourceComponent: WorkspaceNativeIcon {
                iconKey: caption.button.iconKey || ""
                iconSize: caption.button.iconWidth || 16
                color: !caption.button.enabled ? caption.button.disabledLabelColor : caption.button.visualHovered ? caption.button.hoverLabelColor : caption.button.labelColor
                hovered: caption.button.visualHovered
                down: caption.button.down
                enabled: caption.button.enabled
            }
        }
        Image {
            visible: (!caption.button.iconKey || caption.button.iconKey.length === 0) && caption.button.iconSource.toString().length > 0
            source: caption.button.iconSource
            width: visible ? caption.button.iconWidth : 0
            height: caption.button.iconHeight
            sourceSize: Qt.size(width,height)
            anchors.verticalCenter: parent.verticalCenter
            fillMode: Image.PreserveAspectFit; smooth: true
        }
        Text {
            objectName: "buttonCaption_"+caption.button.objectName
            text: caption.button.text
            color: !caption.button.enabled ? caption.button.disabledLabelColor : caption.button.visualHovered ? caption.button.hoverLabelColor : caption.button.labelColor
            font: caption.button.font
            renderType: caption.moving ? Text.CurveRendering : Text.NativeRendering
            verticalAlignment: Text.AlignVCenter
            anchors.verticalCenter: parent.verticalCenter
        }
    }
}
