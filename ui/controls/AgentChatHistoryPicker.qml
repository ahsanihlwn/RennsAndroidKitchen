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

    property string pickedChat: ""
    property bool triggerNewChat: false
    property Component returnCaption: Component {
        Item {
            Image {
                anchors.centerIn: parent
                width: 14; height: 14; sourceSize: Qt.size(16, 16)
                source: "image://rak/history?color=" + encodeURIComponent(control.labelColor)
            }
        }
    }

    function positionPopup() {
        const window = control.Window.window
        if (!window) return
        const host = window.contentItem
        const bounds = control.panelItem ? control.panelItem.mapToItem(host, 0, 0, control.panelItem.width, control.panelItem.height) : Qt.rect(0, 0, host.width, host.height)
        const anchor = control.mapToItem(host, 0, 0, control.width, control.height)
        menu.width = Math.min(260, Math.max(180, bounds.width - 20))
        menu.height = Math.min(body.implicitHeight + 16, Math.min(420, bounds.height - 24))
        menu.x = Math.max(bounds.x + 8, Math.min(anchor.x + anchor.width - menu.width, bounds.x + bounds.width - menu.width - 8))
        menu.y = Math.max(bounds.y + 8, Math.min(anchor.y + anchor.height + 4, bounds.y + bounds.height - menu.height - 8))
    }

    function openChoices() {
        control.resetPressFeedback()
        positionPopup()
        menu.open()
    }

    onVisibleChanged: if (!visible) menu.dismissImmediate()
    Connections {
        target: control.Window.window
        function onWidthChanged() { menu.dismissImmediate() }
        function onHeightChanged() { menu.dismissImmediate() }
        function onXChanged() { menu.dismissImmediate() }
        function onYChanged() { menu.dismissImmediate() }
    }
    Connections {
        target: control.chat
        function onChanged() {
            if (menu.visible) control.positionPopup()
        }
    }

    onClicked: openChoices()

    MorphPopup {
        id: menu
        z: 10000
        objectName: "agentChatHistoryPopup"
        parent: control.Window.window ? control.Window.window.contentItem : null
        sourceItem: control
        springMotion: false
        morphOnClose: false
        animationsEnabled: control.preferences && typeof control.preferences.settings !== "undefined" ? control.preferences.settings.animations : control.animatePress
        backdropSource: control.Window.window ? control.Window.window.backdropSurface : null
        effectsEnabled: typeof rakPreferences !== "undefined" ? rakPreferences.settings.effects !== "low" : true
        panelColor: control.colors.BG_MODAL
        tintBaseColor: control.colors.BG2
        panelBorder: typeof rakPreferences !== "undefined" ? rakPreferences.panelBorder : control.colors.b10
        panelRadius: 12
        padding: 0
        modal: true
        dim: false
        focus: true
        closePolicy: C.Popup.CloseOnEscape | C.Popup.CloseOnPressOutside
        onAboutToShow: {
            control.pickedChat = ""
            control.triggerNewChat = false
            if (control.Window.window && control.Window.window.dialogStack) {
                control.Window.window.dialogStack.registerPopup(menu)
            }
        }
        onClosed: {
            if (control.Window.window && control.Window.window.dialogStack) {
                control.Window.window.dialogStack.unregisterPopup(menu)
            }
            if (control.triggerNewChat) {
                control.triggerNewChat = false
                control.chat.newChat()
                control.newConversationRequested()
            } else if (control.pickedChat.length > 0) {
                const id = control.pickedChat
                control.pickedChat = ""
                control.chat.selectChat(id)
            }
        }
        Column {
            id: body
            x: 8; y: 8
            width: menu.width - 16
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
                    WorkspaceNativeIcon {
                        id: plusIcon
                        anchors.left: parent.left
                        anchors.verticalCenter: parent.verticalCenter
                        width: 14; height: 14; iconSize: 14
                        iconKey: "plus"
                        strokeColor: newChatButton.visualHovered ? control.colors.FG_BRIGHT : control.colors.FG
                        isLight: !!(control.colors && control.colors.is_light)
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
                    control.triggerNewChat = true
                    menu.close()
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
                        delegate: Item {
                            id: chatRowItem
                            required property var modelData
                            width: chatRows.width
                            height: 32
                            implicitWidth: chatRows.width
                            implicitHeight: 32

                            readonly property bool isSelected: control.chat.selectedChat === modelData.id

                            RakButton {
                                id: chatItem
                                objectName: "agentChatHistoryItem_" + chatRowItem.modelData.id
                                anchors.fill: parent
                                leftPadding: 9
                                rightPadding: (chatRowItem.isSelected ? 54 : 36)
                                edgeWidth: 0; cornerRadius: 7
                                enabled: !control.chat.busy
                                fillColor: chatRowItem.isSelected ? control.colors.b07 : "transparent"
                                hoverFillColor: control.colors.b09
                                toolTip: chatRowItem.modelData.label || ""
                                contentItem: Item {
                                    RakLabel {
                                        anchors.left: parent.left
                                        anchors.right: parent.right
                                        anchors.verticalCenter: parent.verticalCenter
                                        text: chatRowItem.modelData.label || control.tr.new
                                        font.pixelSize: 11
                                        font.weight: chatRowItem.isSelected ? Font.DemiBold : Font.Normal
                                        color: chatRowItem.isSelected ? control.colors.FG_BRIGHT : (chatItem.visualHovered ? control.colors.FG_BRIGHT : control.colors.FG)
                                        elide: Text.ElideRight
                                    }
                                }
                                onClicked: {
                                    control.pickedChat = chatRowItem.modelData.id
                                    menu.close()
                                }
                            }

                            Row {
                                id: actionRow
                                anchors.right: parent.right
                                anchors.rightMargin: 6
                                anchors.verticalCenter: parent.verticalCenter
                                spacing: 4

                                Image {
                                    id: checkIcon
                                    visible: chatRowItem.isSelected
                                    anchors.verticalCenter: parent.verticalCenter
                                    width: 13; height: 13
                                    sourceSize: Qt.size(16, 16)
                                    source: "image://rak/check_white?color=" + encodeURIComponent(control.colors.FG2)
                                }

                                RakButton {
                                    id: deleteBtn
                                    objectName: "agentChatDelete_" + chatRowItem.modelData.id
                                    width: 22; height: 22
                                    anchors.verticalCenter: parent.verticalCenter
                                    cornerRadius: 5
                                    edgeWidth: 0
                                    enabled: !control.chat.busy
                                    fillColor: visualHovered ? (control.colors.b12 || "#25272d") : "transparent"
                                    hoverFillColor: control.colors.b12 || "#25272d"
                                    toolTip: control.tr.delete || "Hapus"
                                    opacity: (chatItem.visualHovered || deleteBtn.visualHovered) ? 1.0 : 0.0
                                    visible: opacity > 0.01
                                    Behavior on opacity { enabled: !control.preferences || !control.preferences.settings || control.preferences.settings.animations; NumberAnimation { duration: (!control.preferences || !control.preferences.settings || control.preferences.settings.animations) ? 120 : 0 } }
                                    contentItem: Item {
                                        Image {
                                            anchors.centerIn: parent
                                            width: 13; height: 13
                                            sourceSize: Qt.size(16, 16)
                                            source: "image://rak/project_trash?color=" + encodeURIComponent(deleteBtn.visualHovered ? (control.colors.red_alt || "#ef4444") : control.colors.FG_MUTED)
                                        }
                                    }
                                    onClicked: {
                                        control.chat.deleteChat(chatRowItem.modelData.id)
                                    }
                                }
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
