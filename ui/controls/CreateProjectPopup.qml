// SPDX-License-Identifier: MPL-2.0
import QtQuick
import QtQuick.Controls as C

MorphPopup {
    id: popup
    required property var model
    readonly property var c: model.colors
    readonly property var s: model.strings
    property int portMode: 1
    readonly property bool canCreate: name.text.trim().length > 0 && port1.text.trim().length > 0 && (portMode === 1 || port2.text.trim().length > 0)
    objectName: "createProjectPopup"
    popupType: C.Popup.Item
    width: Math.min(440, parent.width - 16)
    height: Math.min(500, parent.height - 24)
    x: Math.max(8, parent.width - 36 - width)
    y: parent.height - 8 - height
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
    morphOnClose: true
    trackReturnHover: true
    effectsEnabled: model.settings.effects!=="low"
    panelColor: popup.c.BG2
    // Original glass tint (72) underneath the panel tint (120).
    tintAlpha: 1-(1-72/255)*(1-120/255)
    backdropDim: 0
    panelBorder: model.panelBorder
    closePolicy: C.Popup.CloseOnEscape | C.Popup.CloseOnPressOutside
    onAboutToShow: {
        name.text = ""; port1.text = ""; port2.text = ""
        portMode = 1; scroll.contentY = 0
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
        width: form.width; radius: 12; color: popup.c.popupCard; border.color: popup.c.b10
    }
    Item {
        x: 1; y: 1; width: popup.width-2; height: popup.height-2
        Item {
            id: header
            width: parent.width; height: 56
            RakLabel { x: 20; y: 16; height: 28; text: popup.s.hdr_new_project; color: popup.c.FG_BRIGHT; font.pixelSize: 17; font.weight: Font.Bold }
            RakButton { objectName: "popupCloseProject"; x: parent.width - 44; y: 16; width: 28; height: 28; text: "\u2715"; font.pixelSize: 14; labelColor: popup.c.FG_MUTED; hoverLabelColor: popup.c.FG; hoverFillColor: popup.c.b10; onClicked: popup.close() }
        }
        SmoothRectangle { y: header.height; width: parent.width; height: 1; color: popup.c.b07 }
        Flickable {
            id: scroll
            objectName: "popupCreateScroll"
            x: 0; y: header.height + 1; width: parent.width
            height: Math.max(1, parent.height - header.height - 76)
            contentHeight: form.height + 38; clip: true; boundsBehavior: Flickable.StopAtBounds
            C.ScrollBar.vertical: RakScrollBar { handleColor: popup.c.BG_SCROLL }
            Column {
                id: form
                x: 16; y: 14; width: scroll.width - 32; spacing: 12
                Card {
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
                    height: 92
                    Label { x: 18; y: 16; text: popup.s.lbl_output_dir }
                    RakLabel { x: 18; y: 42; width: parent.width - 106; height: 32; text: popup.model.location; elide: Text.ElideRight; color: popup.c.FG_MUTED; font.pixelSize: 12 }
                    ActionButton { objectName: "popupBrowse"; anchors.right: parent.right; anchors.rightMargin: 18; y: 42; width: 62; height: 32; colors: popup.c; text: popup.s.btn_browse; animatePress: popup.model.settings.animations; onClicked: popup.model.dispatch("chooseOutput", {}) }
                }
            }
        }
        SmoothRectangle { anchors.bottom: footer.top; width: parent.width; height: 1; color: popup.c.b07 }
        Item {
            id: footer
            anchors.bottom: parent.bottom; width: parent.width; height: 74
            Row {
                anchors.right: parent.right; anchors.rightMargin: 16; y: 19; spacing: 8
                ActionButton { objectName: "popupBackProject"; width: 99; height: 34; colors: popup.c; text: popup.s.btn_back; iconSource: popup.model.icon("arrow_right", popup.c.FG2); iconWidth: 15; iconHeight: 15; animatePress: popup.model.settings.animations; onClicked: popup.close() }
                ActionButton {
                    objectName: "popupConfirmProject"
                    width: 116; height: 34; colors: popup.c; primary: true
                    text: popup.s.btn_confirm_project; enabled: popup.canCreate && !popup.model.busy
                    iconSource: popup.model.icon("check_white", enabled ? popup.c.FG_BLUE : popup.c.a30); iconWidth: 15; iconHeight: 15
                    animatePress: popup.model.settings.animations
                    onClicked: popup.model.dispatch("createProject", {name: name.text.trim(), port1: port1.text.trim(), port2: popup.portMode === 2 ? port2.text.trim() : "", mode: popup.portMode})
                }
            }
        }
    }
}
