// SPDX-License-Identifier: MPL-2.0
import QtQuick
import QtQuick.Controls as C

Item {
    id: control
    required property var colors
    property alias text: textArea.text
    property alias font: textArea.font
    property bool wrapText: false
    property alias textItem: textArea
    SmoothRectangle { anchors.fill: parent; color: control.colors.BG }
    Flickable {
        id: viewport
        anchors.fill: parent; anchors.margins: 6; clip: true
        contentWidth: textArea.width; contentHeight: textArea.height
        boundsBehavior: Flickable.StopAtBounds
        C.ScrollBar.vertical: RakScrollBar { width: 5; handleColor: control.colors.b30; hoverColor: control.colors.b50 }
        C.ScrollBar.horizontal: RakScrollBar { height: 5; orientation: Qt.Horizontal; handleColor: control.colors.b30; hoverColor: control.colors.b50 }
        C.TextArea {
            id: textArea
            width: control.wrapText?viewport.width:Math.max(viewport.width,implicitWidth)
            height: Math.max(viewport.height,implicitHeight)
            textFormat: TextEdit.PlainText; wrapMode: control.wrapText?TextEdit.Wrap:TextEdit.NoWrap
            readOnly: true; selectByMouse: true; font.family: "Poppins"; font.pixelSize: 12
            renderType: Text.NativeRendering; font.hintingPreference: Font.PreferVerticalHinting
            color: control.colors.FG; selectionColor: control.colors.a40; selectedTextColor: control.colors.FG_BRIGHT
            leftPadding: 5; rightPadding: 5; topPadding: 5; bottomPadding: 5; background: null
            onTextChanged: {viewport.contentX=0;viewport.contentY=0;cursorPosition=0}
            onCursorRectangleChanged: {
                if(!activeFocus)return
                if(cursorRectangle.y<viewport.contentY)viewport.contentY=cursorRectangle.y
                else if(cursorRectangle.y+cursorRectangle.height>viewport.contentY+viewport.height)viewport.contentY=cursorRectangle.y+cursorRectangle.height-viewport.height
            }
        }
    }
}
