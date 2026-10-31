// SPDX-License-Identifier: MPL-2.0
import QtQuick
import QtQuick.Controls as C

Flickable {
    id: control
    required property var colors
    required property var titles
    property int currentIndex: 0
    property string namePrefix: "viewerTab_"
    height: 34; contentWidth: tabs.width; contentHeight: height
    clip: true; boundsBehavior: Flickable.StopAtBounds
    Row {
        id: tabs; height: 34
        Repeater {
            model: control.titles
            delegate: RakButton {
                required property string modelData
                required property int index
                objectName: control.namePrefix+index
                text: modelData.replace(/&/g,""); height: 34; width: Math.ceil(contentItem.implicitWidth)+24; implicitWidth: width
                font.pixelSize: 11; font.weight: control.currentIndex===index?Font.DemiBold:Font.Medium
                leftPadding: 12; rightPadding: 12; edgeWidth: 0; cornerRadius: 0
                fillColor: control.currentIndex===index?control.colors.b02:control.colors.BG2
                hoverFillColor: control.colors.b04; downFillColor: hoverFillColor
                labelColor: control.currentIndex===index?control.colors.FG_BRIGHT:control.colors.FG_MUTED
                hoverLabelColor: control.colors.FG_HOVER
                onClicked: control.currentIndex=index
                SmoothRectangle { y: parent.height-1; width: parent.width; height: 1; color: control.colors.b08 }
                SmoothRectangle { visible: control.currentIndex===index; y: parent.height-2; width: parent.width; height: 2; color: control.colors.accent_hex }
            }
        }
    }
    onCurrentIndexChanged: Qt.callLater(function() {
        const child=tabs.children[currentIndex]
        if(!child)return
        if(child.x<contentX)contentX=child.x
        else if(child.x+child.width>contentX+width)contentX=Math.min(contentWidth-width,child.x+child.width-width)
    })
}
