// SPDX-License-Identifier: MPL-2.0
import QtQuick

ChoiceButton {
    id: control
    property bool showArrow: true
    property string iconSource: ""
    height: 26
    font.pixelSize: 10
    font.weight: Font.Normal
    leftPadding: iconSource ? 4 : 6; rightPadding: iconSource ? 4 : 6
    cornerRadius: 7; edgeWidth: 0
    fillColor: "transparent"; hoverFillColor: colors.b06; downFillColor: colors.b09
    disabledFillColor: "transparent"
    labelColor: colors.FG_MUTED; hoverLabelColor: colors.FG
    disabledLabelColor: colors.FG_MUTED
    contentItem: Item {
        implicitWidth: control.iconSource ? 16 : (label.implicitWidth+15)
        Image {
            id: icon
            visible: !!control.iconSource
            anchors.centerIn: parent
            width: 14; height: 14; sourceSize: Qt.size(16,16)
            source: control.iconSource
        }
        Text {
            id: label
            visible: !control.iconSource
            anchors.left: parent.left; anchors.right: parent.right; anchors.rightMargin: control.showArrow?14:0
            anchors.verticalCenter: parent.verticalCenter
            text: control.displayText; font: control.font; elide: Text.ElideRight
            color: control.visualHovered?control.hoverLabelColor:control.labelColor
            renderType: control.transformed?Text.CurveRendering:Text.NativeRendering
        }
        Image {
            id: arrow; visible: control.showArrow && !control.iconSource; anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter
            width: 8; height: 8; sourceSize: Qt.size(16,16); rotation: 90
            source: "image://rak/arrow_right?color="+encodeURIComponent(control.labelColor)
        }
    }
    returnCaption: Component {
        Item {
            Image {
                visible: !!control.iconSource
                anchors.centerIn: parent
                width: 14; height: 14; sourceSize: Qt.size(16,16)
                source: control.iconSource
            }
            Text {
                visible: !control.iconSource
                x: control.leftPadding; width: parent.width-control.leftPadding-control.rightPadding-(control.showArrow?14:0)
                anchors.verticalCenter: parent.verticalCenter
                text: control.displayText; font: control.font; color: control.labelColor
                elide: Text.ElideRight; renderType: Text.CurveRendering
            }
            Image {
                visible: control.showArrow && !control.iconSource
                x: parent.width-control.rightPadding-8; anchors.verticalCenter: parent.verticalCenter
                width: 8; height: 8; sourceSize: Qt.size(16,16); rotation: 90
                source: "image://rak/arrow_right?color="+encodeURIComponent(control.labelColor)
            }
        }
    }
}
