// SPDX-License-Identifier: MPL-2.0
import QtQuick
import QtQuick.Controls as C
import "../motion.js" as Motion

MorphPopup {
    id: popup
    required property var model
    readonly property var c: model.colors
    readonly property var s: model.strings
    property int portMode: 1
    property bool creating: false
    property double createStartTime: 0
    readonly property bool canCreate: !creating && name.text.trim().length > 0 && port1.text.trim().length > 0 && (portMode === 1 || port2.text.trim().length > 0)
    objectName: "createProjectPopup"
    popupType: C.Popup.Item
    width: Math.min(440, parent.width - 32)
    height: Math.min(560, parent.height - 40)
    x: Math.max(16, parent.width - 16 - width)
    y: Math.max(16, parent.height - 20 - height)
    padding: 0; focus: true
    modal: true; dim: true
    C.Overlay.modal: SmoothRectangle {
        objectName: "createProjectBackdrop"
        color: "#96000000"
        Behavior on opacity {
            id: backdropFade
            NumberAnimation {
                duration: popup.animationsEnabled?180:0
                easing.type: backdropFade.targetValue>0?Easing.OutCubic:Easing.InCubic
            }
        }
    }
    animationsEnabled: model.settings.animations
    showOpenCaption: false
    morphOnClose: true
    trackReturnHover: true
    effectsEnabled: model.settings.effects!=="low"
    panelColor: popup.c.BG2
    // Original glass tint (72) underneath the panel tint (120).
    tintAlpha: 1-(1-72/255)*(1-120/255)
    backdropDim: 0
    panelBorder: model.panelBorder
    closePolicy: popup.creating ? C.Popup.NoAutoClose : (C.Popup.CloseOnEscape | C.Popup.CloseOnPressOutside)
    function finishCreate() {
        if (!creating) {
            close()
            return
        }
        const elapsed = Date.now() - createStartTime
        const minDuration = popup.animationsEnabled ? 800 : 0
        const remaining = Math.max(0, minDuration - elapsed)
        if (remaining > 0) {
            closeDelayTimer.interval = remaining
            closeDelayTimer.restart()
        } else {
            close()
        }
    }
    Timer {
        id: closeDelayTimer
        repeat: false
        onTriggered: popup.close()
    }
    Connections {
        target: popup.model
        function onPresentationRequested(command, data) {
            if (command === "message" || command === "errorLog") {
                if (popup.creating) {
                    popup.creating = false
                    closeDelayTimer.stop()
                }
            }
        }
    }
    onAboutToShow: {
        creating = false
        closeDelayTimer.stop()
        name.text = ""; port1.text = ""; port2.text = ""
        portMode = 1; scroll.contentY = 0
    }
    onAboutToHide: {
        closeDelayTimer.stop()
        creating = false
    }
    function contentProgress(start, span) {
        if (!popup.animationsEnabled || !popup.opening) return 1
        return Motion.spring(Motion.clamp((popup.progress - start) / span))
    }
    function contentOpacity(start, span) {
        if (!popup.animationsEnabled || !popup.opening) return 1
        return Motion.outCubic(Motion.clamp((popup.progress - start) / span))
    }
    component Label: RakLabel {
        height: 16; font.pixelSize: 11; font.weight: Font.DemiBold; color: popup.c.FG_MUTED
    }
    component Field: RakField {
        height: 38; cornerRadius: 8; leftPadding: 15; rightPadding: 15
        fillColor: popup.c.BG_INPUT; focusFillColor: popup.c.BG_SELECTED
        edgeColor: popup.c.b10; focusEdgeColor: popup.c.a55
        labelColor: popup.c.FG; hintColor: popup.c.placeholder; selectionColor: popup.c.a35
    }
    component Card: RakCard {
        property int cardIndex: 0
        readonly property real startDelay: 0.02 + cardIndex * 0.03
        readonly property real enterProgress: popup.contentProgress(startDelay, 1.0 - startDelay)
        readonly property real fadeProgress: popup.contentOpacity(startDelay, 0.28)
        width: form.width; radius: 12; color: popup.c.popupCard; border.color: popup.c.b10
        opacity: fadeProgress
        transform: Translate {
            x: (36 + cardIndex * 6) * (1 - enterProgress)
            y: (36 + cardIndex * 8) * (1 - enterProgress)
        }
    }
    Item {
        x: 1; y: 1; width: popup.width-2; height: popup.height-2
        Item {
            id: header
            readonly property real enterProgress: popup.contentProgress(0.0, 1.0)
            readonly property real fadeProgress: popup.contentOpacity(0.0, 0.26)
            width: parent.width; height: 56
            opacity: fadeProgress
            transform: Translate {
                x: 34 * (1 - header.enterProgress)
                y: 34 * (1 - header.enterProgress)
            }
            RakLabel { x: 20; y: 16; height: 28; text: popup.s.hdr_new_project; color: popup.c.FG_BRIGHT; font.pixelSize: 17; font.weight: Font.Bold }
        }
        SmoothRectangle {
            y: header.height; width: parent.width; height: 1; color: popup.c.b07
            opacity: header.fadeProgress
        }
        Flickable {
            id: scroll
            objectName: "popupCreateScroll"
            x: 0; y: header.height + 1; width: parent.width
            height: Math.max(1, parent.height - header.height - footer.height - 2)
            contentHeight: form.height + 28; clip: true; boundsBehavior: Flickable.StopAtBounds
            C.ScrollBar.vertical: RakScrollBar { handleColor: popup.c.BG_SCROLL }
            Column {
                id: form
                x: 16; y: 14; width: scroll.width - 32; spacing: 12
                enabled: !popup.creating
                Card {
                    cardIndex: 0
                    height: 191
                    Label { x: 18; y: 16; text: popup.s.lbl_project_name }
                    Field { id: name; objectName: "popupProjectName"; x: 18; y: 44; width: parent.width - 36; placeholderText: popup.s.placeholder_project_name }
                    SmoothRectangle { x: 18; y: 94; width: parent.width - 36; height: 1; color: popup.c.b07 }
                    Label { x: 18; y: 107; text: popup.s.lbl_porting_mode }
                    Row {
                        x: 18; y: 135; width: parent.width - 36; spacing: 8
                        RomMode { objectName: "popupSingleRom"; width: (parent.width - 8) / 2; height: 38; colors: popup.c; selected: popup.portMode === 1; dots: 1; text: popup.s.btn_single_rom.replace(/^[\u2b24 ]+/, ""); animatePress: popup.model.settings.animations; onClicked: { popup.portMode = 1; port2.text = "" } }
                        RomMode { objectName: "popupDualRom"; width: (parent.width - 8) / 2; height: 38; colors: popup.c; selected: popup.portMode === 2; dots: 2; text: popup.s.btn_dual_rom.replace(/^[\u2b24 ]+/, ""); animatePress: popup.model.settings.animations; onClicked: popup.portMode = 2 }
                    }
                }
                Card {
                    cardIndex: 1
                    height: port1.y + 38 + 18 + (popup.portMode === 2 ? 95 : 0)
                    Label { x: 18; y: 16; height: popup.portMode === 2 ? 20 : 22; text: popup.s.lbl_folder_names }
                    RakLabel { id: folderHint; x: 18; y: popup.portMode === 2 ? 48 : 50; width: parent.width - 36; height: Math.max(21, implicitHeight); text: popup.s.hint_folder_names; wrapMode: Text.WordWrap; font.pixelSize: 11; color: popup.c.FG_MUTED }
                    SmoothRectangle { id: firstDivider; x: 18; y: folderHint.y + folderHint.height + 12; width: parent.width - 36; height: 1; color: popup.c.b07 }
                    Label { id: firstLabel; x: 18; y: firstDivider.y + 13; height: popup.portMode === 2 ? 20 : 22; text: popup.s.lbl_ft1_folder }
                    Field { id: port1; objectName: "popupPort1"; x: 18; y: firstLabel.y + firstLabel.height + 12; width: parent.width - 36; placeholderText: popup.s.placeholder_ft1 }
                    SmoothRectangle { id: secondDivider; visible: popup.portMode === 2; x: 18; y: port1.y + 50; width: parent.width - 36; height: 1; color: popup.c.b07 }
                    Label { id: secondLabel; visible: popup.portMode === 2; x: 18; y: secondDivider.y + 13; height: 20; text: popup.s.lbl_ft2_folder }
                    Field { id: port2; objectName: "popupPort2"; visible: popup.portMode === 2; x: 18; y: secondLabel.y + 32; width: parent.width - 36; placeholderText: popup.s.placeholder_ft2 }
                }
                Card {
                    cardIndex: 2
                    height: 92
                    Label { x: 18; y: 16; text: popup.s.lbl_output_dir }
                    RakLabel { x: 18; y: 42; width: parent.width - 106; height: 32; text: popup.model.location; elide: Text.ElideRight; color: popup.c.FG_MUTED; font.pixelSize: 12 }
                    ActionButton { objectName: "popupBrowse"; anchors.right: parent.right; anchors.rightMargin: 18; y: 42; width: 62; height: 32; colors: popup.c; text: popup.s.btn_browse; animatePress: popup.model.settings.animations; onClicked: popup.model.dispatch("chooseOutput", {}) }
                }
            }
        }
        SmoothRectangle {
            anchors.bottom: footer.top; width: parent.width; height: 1; color: popup.c.b07
            opacity: footer.fadeProgress
        }
        Item {
            id: footer
            readonly property real enterProgress: popup.contentProgress(0.0, 1.0)
            readonly property real fadeProgress: popup.contentOpacity(0.0, 0.26)
            anchors.bottom: parent.bottom; width: parent.width; height: 56
            opacity: fadeProgress
            transform: Translate {
                x: 28 * (1 - footer.enterProgress)
                y: 28 * (1 - footer.enterProgress)
            }
            Row {
                anchors.right: parent.right; anchors.rightMargin: 16; anchors.verticalCenter: parent.verticalCenter; spacing: 8
                ActionButton { objectName: "popupBackProject"; width: 99; height: 34; colors: popup.c; text: popup.s.btn_back; enabled: !popup.creating; iconSource: popup.model.icon("arrow_right", popup.c.BTN_SEC_FG || popup.c.FG); iconWidth: 15; iconHeight: 15; animatePress: popup.model.settings.animations; onClicked: popup.close() }
                ActionButton {
                    objectName: "popupConfirmProject"
                    width: 116; height: 34; colors: popup.c; primary: true
                    text: popup.s.btn_confirm_project
                    loading: popup.creating
                    enabled: popup.canCreate && !popup.model.busy && !popup.creating
                    iconSource: popup.model.icon("check_white", enabled ? (popup.c.BTN_PRIMARY_FG || popup.c.FG_BRIGHT) : (popup.c.BTN_PRIMARY_DISABLED_FG || popup.c.a30)); iconWidth: 15; iconHeight: 15
                    animatePress: popup.model.settings.animations
                    onClicked: {
                        popup.createStartTime = Date.now()
                        popup.creating = true
                        popup.model.dispatch("createProject", {name: name.text.trim(), port1: port1.text.trim(), port2: popup.portMode === 2 ? port2.text.trim() : "", mode: popup.portMode})
                    }
                }
            }
        }
    }
}
