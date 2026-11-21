// SPDX-License-Identifier: MPL-2.0
import QtQuick
import QtQuick.Controls as C
import QtQuick.Window

RakButton {
    id: control
    required property var chat
    required property var preferences
    readonly property var colors: preferences.colors
    readonly property var tr: chat.labels(preferences.strings.languageKey)

    function cleanModelLabel(val) {
        if (!val) return ""
        return String(val)
            .replace(/^opencode\//i, "")
            .replace(/\s*[\(\[]thinking[\)\]]/gi, "")
            .trim()
    }

    function effortLabel(value, id) {
        const key = String(id || value || "").trim().toLowerCase()
        if (key === "xhigh" || key === "extra") return "Extra"
        if (!value) return ""
        const raw = String(value).trim()
        return raw.charAt(0).toUpperCase() + raw.slice(1)
    }

    readonly property var currentModelObj: (chat.models || []).find(m => m.id === chat.model) || {}
    readonly property string currentModelLabel: cleanModelLabel(currentModelObj.label || chat.model) || tr.automatic
    readonly property var currentEfforts: chat.efforts || []
    readonly property var currentEffortObj: currentEfforts.find(e => e.id === chat.effort) || {}
    readonly property string currentEffortLabel: (currentEfforts.length > 0 && chat.effort) ? effortLabel(currentEffortObj.label || chat.effort, chat.effort) : ""

    function effortIndex() {
        if (!currentEfforts || currentEfforts.length === 0) return 0
        const idx = currentEfforts.findIndex(e => e.id === chat.effort)
        if (idx >= 0) return idx
        const midIdx = currentEfforts.findIndex(e => e.id === "medium")
        return midIdx >= 0 ? midIdx : 0
    }

    property string currentMenu: "main" // "main" or "models"
    property Item popupBoundsItem: null
    property string pickedModel: ""

    height: 26
    implicitWidth: contentItem.implicitWidth + leftPadding + rightPadding
    font.pixelSize: 11
    leftPadding: 6
    rightPadding: 6
    cornerRadius: 7
    edgeWidth: 0
    labelColor: colors.FG2
    hoverLabelColor: colors.FG
    hoverFillColor: colors.b06
    downFillColor: colors.b10
    enabled: true

    contentItem: Item {
        implicitWidth: labelRow.implicitWidth + 14
        Row {
            id: labelRow
            anchors.left: parent.left
            anchors.right: arrow.left
            anchors.rightMargin: 4
            anchors.verticalCenter: parent.verticalCenter
            spacing: 5
            clip: true
            Text {
                id: modelCaption
                text: control.currentModelLabel
                font.pixelSize: control.font.pixelSize
                font.weight: Font.Medium
                color: control.visualHovered ? control.hoverLabelColor : control.labelColor
                renderType: control.transformed ? Text.CurveRendering : Text.NativeRendering
                elide: Text.ElideRight
                width: Math.min(implicitWidth, control.currentEffortLabel ? Math.max(38, labelRow.width - effortCaption.implicitWidth - 6) : labelRow.width)
            }
            Text {
                id: effortCaption
                visible: control.currentEffortLabel.length > 0
                text: control.currentEffortLabel
                font.pixelSize: control.font.pixelSize
                color: control.colors.FG_MUTED
                renderType: control.transformed ? Text.CurveRendering : Text.NativeRendering
                elide: Text.ElideRight
            }
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
        menu.width = Math.min(270, Math.max(220, bounds.width - 20))

        const modelCount = (control.chat.models || []).length
        const hasEfforts = (control.currentEfforts || []).length > 0
        const mainH = hasEfforts ? 104 : 68
        const modelsH = Math.min(260, 44 + Math.max(1, modelCount) * 34)
        const desired = control.currentMenu === "models" ? modelsH : mainH

        const below = bounds.y + bounds.height - anchor.y
        const above = anchor.y + anchor.height - bounds.y
        menu.upward = above >= below || desired > below
        menu.height = Math.min(desired, menu.upward ? above : below)
        menu.x = Math.max(bounds.x + 10, Math.min(anchor.x + anchor.width - menu.width, bounds.x + bounds.width - menu.width - 10))
        menu.y = menu.upward ? anchor.y + anchor.height - menu.height : anchor.y
    }

    function openChoices() {
        control.resetPressFeedback()
        control.currentMenu = "main"
        control.chat.refreshEfforts()
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
    onClicked: openChoices()

    MorphPopup {
        id: menu
        z: 10000
        objectName: "agentModelPopup"
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
        panelRadius: 14
        padding: 0
        modal: true
        dim: false
        focus: true
        closePolicy: C.Popup.CloseOnEscape | C.Popup.CloseOnPressOutside

        onAboutToShow: {
            control.pickedModel = ""
            control.currentMenu = "main"
            if (control.Window.window && control.Window.window.dialogStack) {
                control.Window.window.dialogStack.registerPopup(menu)
            }
        }
        onClosed: {
            if (control.Window.window && control.Window.window.dialogStack) {
                control.Window.window.dialogStack.unregisterPopup(menu)
            }
            if (control.pickedModel.length > 0) {
                const id = control.pickedModel
                control.pickedModel = ""
                control.chat.selectModel(id)
            }
        }

        Item {
            anchors.fill: parent

            // VIEW 1: Main Menu (Model + Reasoning Slider)
            Column {
                id: mainView
                visible: control.currentMenu === "main"
                anchors.fill: parent
                anchors.margins: 10
                spacing: 8

                // Top section: Model on top, Think level below
                RakButton {
                    id: modelNavBtn
                    objectName: "agentModelNavBtn"
                    width: parent.width
                    height: 38
                    cornerRadius: 8
                    edgeWidth: 0
                    fillColor: "transparent"
                    hoverFillColor: control.colors.b06
                    downFillColor: control.colors.b10

                    contentItem: Item {
                        anchors.fill: parent
                        Column {
                            anchors.centerIn: parent
                            spacing: 2
                            Row {
                                anchors.horizontalCenter: parent.horizontalCenter
                                spacing: 4
                                Text {
                                    text: control.currentModelLabel
                                    font.pixelSize: 12
                                    font.weight: Font.DemiBold
                                    color: control.colors.FG_BRIGHT
                                    renderType: Text.NativeRendering
                                    elide: Text.ElideRight
                                    maximumLineCount: 1
                                }
                                Image {
                                    anchors.verticalCenter: parent.verticalCenter
                                    width: 7; height: 7
                                    sourceSize: Qt.size(14, 14)
                                    source: "image://rak/arrow_right?color=" + encodeURIComponent(control.colors.FG_MUTED)
                                }
                            }
                            Text {
                                anchors.horizontalCenter: parent.horizontalCenter
                                visible: control.currentEffortLabel.length > 0
                                text: control.currentEffortLabel
                                font.pixelSize: 11
                                font.weight: Font.Normal
                                color: control.colors.FG_MUTED
                                renderType: Text.NativeRendering
                                elide: Text.ElideRight
                                maximumLineCount: 1
                            }
                        }
                    }

                    onClicked: {
                        control.currentMenu = "models"
                        control.positionPopup()
                        Qt.callLater(() => {
                            const index = (control.chat.models || []).findIndex(model => model.id === control.chat.model)
                            if (index >= 0 && typeof modelScroller !== "undefined" && modelScroller) {
                                modelScroller.contentItem.contentY = Math.max(0, Math.min(modelRows.implicitHeight - modelScroller.height, index * 34 + 32 - modelScroller.height))
                            }
                        })
                    }
                }

                // Bottom section: Reasoning capsule slider
                ViewerSlider {
                    id: effortSlider
                    objectName: "agentEffortSlider"
                    visible: control.currentEfforts.length > 0
                    width: parent.width - 8
                    anchors.horizontalCenter: parent.horizontalCenter
                    preferences: control.preferences
                    colors: control.colors
                    capsuleTrack: true
                    stepCount: control.currentEfforts.length
                    from: 0
                    to: Math.max(0, control.currentEfforts.length - 1)
                    value: control.effortIndex()
                    enabled: !control.chat.busy && control.currentEfforts.length > 0

                    onMoved: {
                        const idx = Math.round(value)
                        if (idx >= 0 && idx < control.currentEfforts.length) {
                            const target = control.currentEfforts[idx].id
                            if (target !== control.chat.effort) {
                                control.chat.selectEffort(target)
                            }
                        }
                    }
                }

                RakLabel {
                    visible: control.currentEfforts.length === 0
                    width: parent.width
                    height: 20
                    horizontalAlignment: Text.AlignHCenter
                    verticalAlignment: Text.AlignVCenter
                    text: control.tr.effort_unavailable || "Reasoning effort not supported"
                    font.pixelSize: 10
                    color: control.colors.FG_MUTED
                }
            }

            // VIEW 2: Model Selection List
            Column {
                id: modelsView
                visible: control.currentMenu === "models"
                anchors.fill: parent
                anchors.margins: 10
                spacing: 0

                RakLabel {
                    x: 9
                    height: 27
                    text: control.tr.model || "Model"
                    font.pixelSize: 10
                    color: control.colors.FG_MUTED
                }

                C.ScrollView {
                    id: modelScroller
                    width: parent.width
                    height: Math.min(196, parent.height - 30)
                    clip: true
                    C.ScrollBar.horizontal.policy: C.ScrollBar.AlwaysOff
                    C.ScrollBar.vertical: RakScrollBar { width: 3; handleColor: control.colors.b20; hoverColor: control.colors.b35 }

                    Column {
                        id: modelRows
                        width: modelScroller.width
                        spacing: 2

                        Repeater {
                            model: control.chat.models || []
                            delegate: RakButton {
                                required property var modelData
                                objectName: "agentModel_" + (modelData.id || "__default__")
                                width: modelRows.width
                                height: 32
                                leftPadding: 9; rightPadding: 9; edgeWidth: 0; cornerRadius: 7
                                enabled: !control.chat.busy
                                fillColor: control.chat.model === modelData.id ? control.colors.b07 : "transparent"
                                hoverFillColor: control.colors.b09

                                contentItem: Item {
                                    RakLabel {
                                        width: parent.width - 24
                                        anchors.verticalCenter: parent.verticalCenter
                                        text: control.cleanModelLabel(modelData.label || modelData.id)
                                        font.pixelSize: 11
                                        color: control.colors.FG
                                        elide: Text.ElideRight
                                        toolTip: modelData.description || ""
                                    }
                                    Image {
                                        visible: control.chat.model === modelData.id
                                        anchors.right: parent.right
                                        anchors.verticalCenter: parent.verticalCenter
                                        width: 13; height: 13
                                        sourceSize: Qt.size(16, 16)
                                        source: "image://rak/check_white?color=" + encodeURIComponent(control.colors.FG2)
                                    }
                                }

                                onClicked: {
                                    control.chat.selectModel(modelData.id)
                                    menu.close()
                                }
                            }
                        }
                    }
                }

                ViewerToolButton {
                    objectName: "agentChatRefreshModels"
                    colors: control.colors
                    text: control.tr.retry
                    visible: !!control.chat.modelCatalogError
                    onClicked: control.chat.retryModels()
                }
            }
        }
    }
}
