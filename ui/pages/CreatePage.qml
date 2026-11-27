// SPDX-License-Identifier: MPL-2.0
import QtQuick
import "../controls"

Item {
    id: page
    required property var model
    readonly property var c: model.colors
    readonly property var s: model.strings
    property var presentationMotion: null
    readonly property real headingOpacity: presentationMotion ? presentationMotion.headerOpacity(page) : 1
    readonly property real headingOffsetY: presentationMotion ? presentationMotion.headerOffsetY(page) : 0
    property alias footer: footer
    readonly property var identityItem: footer.identityItem
    function identityEndpoint(host) { return footer.identityEndpoint(host) }
    property int portMode: 1
    readonly property bool canCreate: name.text.trim().length > 0 && port1.text.trim().length > 0 && (portMode === 1 || port2.text.trim().length > 0)
    component SectionLabel: RakLabel {
        height: 16; font.pixelSize: 11; font.weight: Font.DemiBold; color: page.c.FG_MUTED
    }
    component Field: RakField {
        height: 38; cornerRadius: 8; leftPadding: 15; rightPadding: 15
        fillColor: page.c.BG_INPUT; focusFillColor: page.c.BG_SELECTED
        edgeColor: page.c.b10; focusEdgeColor: page.c.a55
        labelColor: page.c.FG; hintColor: page.c.placeholder; selectionColor: page.c.a35
    }
    component Card: RakCard {
        property int cardIndex: 0
        width: form.width; color: page.c.BG2; border.color: page.c.b08; radius: 12
        opacity: page.presentationMotion ? page.presentationMotion.itemOpacity(page, cardIndex) : 1
        transform: Translate { y: page.presentationMotion ? page.presentationMotion.itemOffsetY(page, cardIndex) : 0 }
    }
    SmoothRectangle { anchors.fill: parent; color: page.c.BG }
    RakLabel {
        x: 36; y: 32; height: 31; text: page.s.hdr_new_project
        font.pixelSize: 22; font.weight: Font.DemiBold; color: page.c.FG
        opacity: page.headingOpacity
        transform: Translate { y: page.headingOffsetY }
    }
    Flickable {
        objectName: "createScroll"
        x: 20; y: 77; width: parent.width - 40; height: Math.max(0, parent.height - 77 - footer.height - 22)
        clip: true; contentHeight: form.height + 24; boundsBehavior: Flickable.StopAtBounds
        Column {
            id: form
            x: 16; y: 8; width: parent.width - 32; spacing: 24
            Card {
                objectName: "projectInfoCard"
                cardIndex: 0
                height: 201
                SectionLabel { x: 18; y: 16; text: page.s.lbl_project_name }
                Field { id: name; objectName: "projectName"; x: 18; y: 46; width: parent.width - 36; placeholderText: page.s.placeholder_project_name }
                SmoothRectangle { x: 18; y: 98; width: parent.width - 36; height: 1; color: page.c.b07 }
                SectionLabel { x: 18; y: 113; text: page.s.lbl_porting_mode }
                Row {
                    x: 18; y: 143; width: parent.width - 36; spacing: 8
                    RomMode {
                        objectName: "singleRomButton"
                        width: (parent.width - 8) / 2; height: 40; colors: page.c
                        selected: page.portMode === 1; dots: 1
                        text: page.s.btn_single_rom.replace(/^[⬤ ]+/, "")
                        animatePress: page.model.settings.animations
                        onClicked: page.portMode = 1
                    }
                    RomMode {
                        objectName: "dualRomButton"
                        width: (parent.width - 8) / 2; height: 40; colors: page.c
                        selected: page.portMode === 2; dots: 2
                        text: page.s.btn_dual_rom.replace(/^[⬤ ]+/, "")
                        animatePress: page.model.settings.animations
                        onClicked: page.portMode = 2
                    }
                }
            }
            Card {
                objectName: "portsCard"
                cardIndex: 1
                height: page.portMode === 2 ? 278 : 181
                SectionLabel { x: 18; y: 16; text: page.s.lbl_folder_names }
                RakLabel { x: 18; y: 46; height: 16; text: page.s.hint_folder_names; color: page.c.FG_MUTED; font.pixelSize: 11 }
                SmoothRectangle { x: 18; y: 78; width: parent.width - 36; height: 1; color: page.c.b07 }
                SectionLabel { x: 18; y: 95; text: page.s.lbl_ft1_folder }
                Field { id: port1; objectName: "port1Name"; x: 18; y: 125; width: parent.width - 36; placeholderText: page.s.placeholder_ft1 }
                SmoothRectangle { visible: page.portMode === 2; x: 18; y: 177; width: parent.width - 36; height: 1; color: page.c.b07 }
                SectionLabel { visible: page.portMode === 2; x: 18; y: 192; text: page.s.lbl_ft2_folder }
                Field { id: port2; objectName: "port2Name"; visible: page.portMode === 2; x: 18; y: 222; width: parent.width - 36; placeholderText: page.s.placeholder_ft2 }
            }
            Card {
                cardIndex: 2
                height: 92
                SectionLabel { x: 18; y: 16; text: page.s.lbl_output_dir }
                RakLabel { x: 18; y: 42; width: parent.width - 101; height: 32; text: page.model.location; color: page.c.FG_MUTED; font.pixelSize: 12; elide: Text.ElideRight }
                ActionButton {
                    anchors.right: parent.right; anchors.rightMargin: 18; y: 42
                    width: 57; height: 32; colors: page.c; text: page.s.btn_browse; cornerRadius: 6; leftPadding: 12; rightPadding: 12
                    animatePress: page.model.settings.animations
                    onClicked: page.model.dispatch("chooseOutput", {})
                }
            }
        }
    }
    PageFooter {
        id: footer
        width: parent.width; anchors.bottom: parent.bottom; model: page.model
        transform: Translate { y: page.presentationMotion ? page.presentationMotion.footerOffsetY(page) : 0 }
        ActionButton {
            objectName: "createBackButton"
            actionVisible: page.model.hasProjects
            width: 99; height: 32; colors: page.c; text: page.s.btn_back
            iconSource: page.model.icon("arrow_right", page.c.BTN_SEC_FG || page.c.FG); iconWidth: 15; iconHeight: 15
            animatePress: page.model.settings.animations; onClicked: page.model.back()
        }
        ActionButton {
            objectName: "createSettingsButton"
            actionVisible: !page.model.hasProjects
            width: 120; height: 32; colors: page.c; text: page.s.btn_settings
            iconKey: "settings"; iconWidth: 15; iconHeight: 15
            animatePress: page.model.settings.animations; onClicked: page.model.navigate("settings")
        }
        ActionButton {
            objectName: "confirmProjectButton"
            width: 116; height: 32; colors: page.c; primary: true
            text: page.s.btn_confirm_project
            loading: page.model.busy
            enabled: page.canCreate && !page.model.busy
            iconSource: page.model.icon("check_white", enabled ? (page.c.BTN_PRIMARY_FG || page.c.FG_BRIGHT) : (page.c.BTN_PRIMARY_DISABLED_FG || page.c.a30)); iconWidth: 15; iconHeight: 15
            animatePress: page.model.settings.animations
            onClicked: page.model.dispatch("createProject", {"name": name.text.trim(), "port1": port1.text.trim(), "port2": page.portMode === 2 ? port2.text.trim() : "", "mode": page.portMode})
        }
    }
}
