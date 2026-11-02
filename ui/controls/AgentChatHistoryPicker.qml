// SPDX-License-Identifier: MPL-2.0
import QtQuick
import QtQuick.Controls as C
import QtQuick.Window

RakButton {
    id: control
    objectName: "agentChatHistory"
    required property var chat
    required property var preferences
    property Item panelItem: null
    readonly property var colors: preferences.colors
    readonly property var tr: chat.labels(preferences.strings.languageKey)
    signal newConversationRequested()

    width: 24; height: 26; cornerRadius: 7; edgeWidth: 0
    leftPadding: 4; rightPadding: 4
    toolTip: tr.history
    fillColor: "transparent"; hoverFillColor: colors.b06; downFillColor: colors.b09
    disabledFillColor: "transparent"
    labelColor: colors.FG_MUTED; hoverLabelColor: colors.FG
    disabledLabelColor: colors.FG_MUTED
    enabled: !chat.busy

    contentItem: Item {
        implicitWidth: 16; implicitHeight: 16
        Image {
            anchors.centerIn: parent
            width: 14; height: 14; sourceSize: Qt.size(16, 16)
            source: "image://rak/history?color=" + encodeURIComponent(control.visualHovered ? control.hoverLabelColor : control.labelColor)
        }
    }

    function positionPopup() {
        const window = control.Window.window
        if (!window) return
        const anchor = control.mapToItem(window.contentItem, 0, 0)
        menu.x = Math.max(10, Math.min(anchor.x + control.width - menu.width, window.width - menu.width - 10))
        menu.y = Math.max(10, Math.min(anchor.y + control.height + 4, window.height - menu.height - 10))
    }

    function openChoices() {
        positionPopup()
        menu.open()
    }

    Connections {
        target: control.Window.window
        function onWidthChanged() { if (menu.opened) Qt.callLater(control.positionPopup) }
        function onHeightChanged() { if (menu.opened) Qt.callLater(control.positionPopup) }
    }

    onClicked: openChoices()

    C.Popup {
        id: menu
        objectName: "agentChatHistoryPopup"
        parent: control.Window.window ? control.Window.window.contentItem : null
        width: Math.min(260, control.panelItem ? Math.max(180, control.panelItem.width - 20) : 260)
        height: Math.min(body.implicitHeight + 16, control.Window.window ? Math.min(420, control.Window.window.height - 24) : 400)
        padding: 8
        modal: true
        dim: false
        focus: true
        closePolicy: C.Popup.CloseOnEscape | C.Popup.CloseOnPressOutside
        onHeightChanged: if (opened) control.positionPopup()
        onAboutToShow: {
            if (control.Window.window && control.Window.window.dialogStack) {
                control.Window.window.dialogStack.registerPopup(menu)
            }
        }
        onClosed: {
            if (control.Window.window && control.Window.window.dialogStack) {
                control.Window.window.dialogStack.unregisterPopup(menu)
            }
        }
        background: SmoothRectangle {
            radius: 12
            color: control.colors.BG2
            border.width: 1
            border.color: control.colors.b14
        }
        Column {
            id: body
            width: menu.availableWidth
            spacing: 4

            RakButton {
                id: newChatButton
                objectName: "agentChatNew"
                width: body.width; height: 32
                leftPadding: 9; rightPadding: 9; edgeWidth: 0; cornerRadius: 7
                fillColor: visualHovered ? control.colors.b07 : "transparent"
                hoverFillColor: control.colors.b07
                enabled: !control.chat.busy
                toolTip: control.tr.new_conversation || control.tr.new || "New conversation"
                contentItem: Item {
                    Image {
                        id: plusIcon
                        anchors.left: parent.left
                        anchors.verticalCenter: parent.verticalCenter
                        width: 14; height: 14
                        sourceSize: Qt.size(16, 16)
                        source: "image://rak/plus?color=" + encodeURIComponent(newChatButton.visualHovered ? control.colors.FG_BRIGHT : control.colors.FG)
                    }
                    RakLabel {
                        anchors.left: plusIcon.right; anchors.leftMargin: 8
                        anchors.right: parent.right
                        anchors.verticalCenter: parent.verticalCenter
                        text: control.tr.new_conversation || control.tr.new || "New conversation"
                        font.pixelSize: 11
                        font.weight: Font.Medium
                        color: newChatButton.visualHovered ? control.colors.FG_BRIGHT : control.colors.FG
                        elide: Text.ElideRight
                    }
                }
                onClicked: {
                    control.chat.newChat()
                    menu.close()
                    control.newConversationRequested()
                }
            }

            SmoothRectangle {
                width: body.width; height: 1
                color: control.colors.b09
                visible: control.chat.chats.length > 0
            }

            RakLabel {
                visible: control.chat.chats.length > 0
                x: 9; height: 20
                text: control.tr.history
                font.pixelSize: 10
                color: control.colors.FG_MUTED
            }

            C.ScrollView {
                id: chatScroller
                width: body.width
                height: Math.min(240, chatRows.implicitHeight)
                clip: true
                visible: control.chat.chats.length > 0
                C.ScrollBar.horizontal.policy: C.ScrollBar.AlwaysOff
                C.ScrollBar.vertical: RakScrollBar {
                    width: 3
                    handleColor: control.colors.b20
                    hoverColor: control.colors.b35
                }
                Column {
                    id: chatRows
                    width: body.width
                    spacing: 2
                    Repeater {
                        model: control.chat.chats
                        delegate: RakButton {
                            id: chatItem
                            required property var modelData
                            objectName: "agentChatHistoryItem_" + modelData.id
                            width: chatRows.width; height: 32
                            leftPadding: 9; rightPadding: 9; edgeWidth: 0; cornerRadius: 7
                            enabled: !control.chat.busy
                            readonly property bool isSelected: control.chat.selectedChat === modelData.id
                            fillColor: isSelected ? control.colors.b07 : "transparent"
                            hoverFillColor: control.colors.b09
                            toolTip: chatItem.modelData.label || ""
                            contentItem: Item {
                                RakLabel {
                                    anchors.left: parent.left
                                    anchors.right: checkIcon.visible ? checkIcon.left : parent.right
                                    anchors.rightMargin: checkIcon.visible ? 6 : 0
                                    anchors.verticalCenter: parent.verticalCenter
                                    text: chatItem.modelData.label || control.tr.new
                                    font.pixelSize: 11
                                    font.weight: chatItem.isSelected ? Font.DemiBold : Font.Normal
                                    color: chatItem.isSelected ? control.colors.FG_BRIGHT : (chatItem.visualHovered ? control.colors.FG_BRIGHT : control.colors.FG)
                                    elide: Text.ElideRight
                                }
                                Image {
                                    id: checkIcon
                                    visible: chatItem.isSelected
                                    anchors.right: parent.right
                                    anchors.verticalCenter: parent.verticalCenter
                                    width: 13; height: 13
                                    sourceSize: Qt.size(16, 16)
                                    source: "image://rak/check_white?color=" + encodeURIComponent(control.colors.FG2)
                                }
                            }
                            onClicked: {
                                control.chat.selectChat(modelData.id)
                                menu.close()
                            }
                        }
                    }
                }
            }

            RakLabel {
                visible: control.chat.chats.length === 0
                x: 9; width: body.width - 18; height: 32
                text: control.tr.no_history || "No conversations yet"
                font.pixelSize: 10
                color: control.colors.FG_MUTED
                verticalAlignment: Text.AlignVCenter
            }
        }
    }
}
