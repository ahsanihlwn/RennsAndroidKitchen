// SPDX-License-Identifier: MPL-2.0
import QtQuick
import QtQuick.Controls as C
import QtQuick.Shapes
import QtQuick.Window

RakButton {
    id: control
    required property var chat
    required property var preferences
    readonly property var colors: preferences.colors
    readonly property bool isBypass: chat.autoApprove
    readonly property bool isId: preferences && preferences.strings && preferences.strings.languageKey === "id"
    property Item popupBoundsItem: null
    property string pickedKey: ""

    height: 26
    font.pixelSize: 11
    leftPadding: 7
    rightPadding: 7
    cornerRadius: 7
    edgeWidth: 0
    labelColor: colors.FG2
    hoverLabelColor: colors.FG
    hoverFillColor: colors.b06
    downFillColor: colors.b10
    enabled: true
    toolTip: isId
        ? (isBypass ? "Mode Turbo: Perintah & edit dijalankan otomatis" : "Mode Ask: Minta izin sebelum tindakan dijalankan")
        : (isBypass ? "Turbo mode: Automatically approve tools and edits" : "Ask mode: Ask before taking actions")

    // Native QML Lightning Bolt (Petir) Component
    Component {
        id: boltComponent
        Item {
            property color iconColor: control.visualHovered ? control.hoverLabelColor : control.labelColor
            width: 11; height: 12
            Shape {
                anchors.centerIn: parent
                width: 11; height: 12
                preferredRendererType: Shape.CurveRenderer
                ShapePath {
                    fillColor: iconColor
                    strokeColor: "transparent"
                    startX: 6.2; startY: 0.5
                    PathLine { x: 1.5; y: 6.2 }
                    PathLine { x: 5.2; y: 6.2 }
                    PathLine { x: 4.2; y: 11.5 }
                    PathLine { x: 9.8; y: 5.0 }
                    PathLine { x: 6.2; y: 5.0 }
                    PathLine { x: 6.2; y: 0.5 }
                }
            }
        }
    }

    // Native QML Lock (Gembok) Component
    Component {
        id: lockComponent
        Item {
            property color iconColor: control.visualHovered ? control.hoverLabelColor : control.labelColor
            property color holeColor: control.colors.BG3 || "#181824"
            width: 11; height: 12
            // Shackle (gagang lengkung gembok)
            Rectangle {
                anchors.horizontalCenter: parent.horizontalCenter
                y: 0.8
                width: 7.2; height: 6.0
                radius: 3.6
                color: "transparent"
                border.width: 1.3
                border.color: iconColor
            }
            // Body (badan gembok)
            Rectangle {
                anchors.horizontalCenter: parent.horizontalCenter
                y: 5.2
                width: 9.6; height: 6.2
                radius: 1.6
                color: iconColor
                // Keyhole
                Rectangle {
                    anchors.centerIn: parent
                    width: 1.6; height: 2.2
                    radius: 0.8
                    color: holeColor
                }
            }
        }
    }

    contentItem: Item {
        implicitWidth: iconLoader.width + 5 + caption.implicitWidth + 14
        Loader {
            id: iconLoader
            anchors.left: parent.left
            anchors.verticalCenter: parent.verticalCenter
            sourceComponent: control.isBypass ? boltComponent : lockComponent
        }
        Text {
            id: caption
            anchors.left: iconLoader.right
            anchors.leftMargin: 5
            anchors.right: arrow.left
            anchors.rightMargin: 4
            anchors.verticalCenter: parent.verticalCenter
            text: control.isBypass ? "Turbo" : "Ask"
            font: control.font
            color: control.visualHovered ? control.hoverLabelColor : control.labelColor
            renderType: control.transformed ? Text.CurveRendering : Text.NativeRendering
            elide: Text.ElideRight
        }
        Image {
            id: arrow
            anchors.right: parent.right
            anchors.verticalCenter: parent.verticalCenter
            width: 8; height: 8
            sourceSize: Qt.size(16, 16)
            rotation: 90
            source: "image://rak/arrow_right?color=" + encodeURIComponent(control.visualHovered ? control.hoverLabelColor : control.labelColor)
        }
    }

    function positionPopup() {
        const window = control.Window.window
        if (!window) return
        const host = window.contentItem
        const bounds = popupBoundsItem ? popupBoundsItem.mapToItem(host, 0, 0, popupBoundsItem.width, popupBoundsItem.height) : Qt.rect(0, 0, host.width, host.height)
        const anchor = control.mapToItem(host, 0, 0, control.width, control.height)
        menu.width = 244
        menu.height = 108

        const below = bounds.y + bounds.height - anchor.y
        const above = anchor.y + anchor.height - bounds.y
        menu.upward = above >= below || menu.height > below
        menu.x = Math.max(bounds.x + 8, Math.min(anchor.x + anchor.width - menu.width, bounds.x + bounds.width - menu.width - 8))
        menu.y = menu.upward ? anchor.y + anchor.height - menu.height : anchor.y
    }

    function openChoices() {
        control.resetPressFeedback()
        positionPopup()
        menu.open()
    }

    onClicked: openChoices()
    onVisibleChanged: if (!visible) menu.dismissImmediate()

    Connections {
        target: control.Window.window
        function onWidthChanged() { menu.dismissImmediate() }
        function onHeightChanged() { menu.dismissImmediate() }
        function onXChanged() { menu.dismissImmediate() }
        function onYChanged() { menu.dismissImmediate() }
    }

    MorphPopup {
        id: menu
        z: 10000
        objectName: "agentPermissionPopup"
        parent: control.Window.window ? control.Window.window.contentItem : null
        sourceItem: control
        springMotion: false
        morphOnClose: false
        animationsEnabled: control.animatePress
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
            control.pickedKey = ""
            if (control.Window.window && control.Window.window.dialogStack) {
                control.Window.window.dialogStack.registerPopup(menu)
            }
        }
        onClosed: {
            if (control.Window.window && control.Window.window.dialogStack) {
                control.Window.window.dialogStack.unregisterPopup(menu)
            }
            if (control.pickedKey.length > 0) {
                const key = control.pickedKey
                control.pickedKey = ""
                control.chat.setAutoApprove(key === "turbo" || key === "bypass")
            }
        }

        Column {
            x: 6; y: 6
            width: menu.width - 12
            spacing: 4

            // Option 1: Turbo (Petir)
            RakButton {
                id: turboBtn
                objectName: "choice_agentChatPermission_turbo"
                width: parent.width; height: 46
                leftPadding: 10; rightPadding: 10; cornerRadius: 8; edgeWidth: 0
                fillColor: control.isBypass ? control.colors.b07 : "transparent"
                hoverFillColor: control.colors.b09

                contentItem: Item {
                    Loader {
                        id: popTurboIcon
                        anchors.left: parent.left
                        anchors.top: parent.top
                        anchors.topMargin: 8
                        sourceComponent: boltComponent
                    }
                    Column {
                        anchors.left: popTurboIcon.right
                        anchors.leftMargin: 9
                        anchors.right: checkTurbo.left
                        anchors.rightMargin: 6
                        anchors.verticalCenter: parent.verticalCenter
                        spacing: 2
                        RakLabel {
                            text: "Turbo"
                            font.pixelSize: 11
                            font.weight: control.isBypass ? Font.DemiBold : Font.Medium
                            color: control.colors.FG
                        }
                        RakLabel {
                            text: control.isId ? "Jalankan perintah & edit secara otomatis" : "Automatically run tools and edits"
                            font.pixelSize: 10
                            color: control.colors.FG_MUTED
                            elide: Text.ElideRight
                            width: parent.width
                        }
                    }
                    Image {
                        id: checkTurbo
                        visible: control.isBypass
                        anchors.right: parent.right
                        anchors.verticalCenter: parent.verticalCenter
                        width: 13; height: 13
                        sourceSize: Qt.size(16, 16)
                        source: "image://rak/check_white?color=" + encodeURIComponent(control.colors.FG2)
                    }
                }

                onClicked: {
                    control.pickedKey = "turbo"
                    menu.close()
                }
            }

            // Option 2: Ask (Gembok)
            RakButton {
                id: askBtn
                objectName: "choice_agentChatPermission_ask"
                width: parent.width; height: 46
                leftPadding: 10; rightPadding: 10; cornerRadius: 8; edgeWidth: 0
                fillColor: !control.isBypass ? control.colors.b07 : "transparent"
                hoverFillColor: control.colors.b09

                contentItem: Item {
                    Loader {
                        id: popAskIcon
                        anchors.left: parent.left
                        anchors.top: parent.top
                        anchors.topMargin: 8
                        sourceComponent: lockComponent
                    }
                    Column {
                        anchors.left: popAskIcon.right
                        anchors.leftMargin: 9
                        anchors.right: checkAsk.left
                        anchors.rightMargin: 6
                        anchors.verticalCenter: parent.verticalCenter
                        spacing: 2
                        RakLabel {
                            text: "Ask"
                            font.pixelSize: 11
                            font.weight: !control.isBypass ? Font.DemiBold : Font.Medium
                            color: control.colors.FG
                        }
                        RakLabel {
                            text: control.isId ? "Minta izin sebelum menjalankan tindakan" : "Ask for confirmation before actions"
                            font.pixelSize: 10
                            color: control.colors.FG_MUTED
                            elide: Text.ElideRight
                            width: parent.width
                        }
                    }
                    Image {
                        id: checkAsk
                        visible: !control.isBypass
                        anchors.right: parent.right
                        anchors.verticalCenter: parent.verticalCenter
                        width: 13; height: 13
                        sourceSize: Qt.size(16, 16)
                        source: "image://rak/check_white?color=" + encodeURIComponent(control.colors.FG2)
                    }
                }

                onClicked: {
                    control.pickedKey = "ask"
                    menu.close()
                }
            }
        }
    }
}
