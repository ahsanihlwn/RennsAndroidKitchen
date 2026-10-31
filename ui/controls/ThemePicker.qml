// SPDX-License-Identifier: MPL-2.0
import QtQuick
import QtQuick.Controls as C

DialogWindow {
    id: picker
    implicitWidth: 520; implicitHeight: 460; title: s.thm_title
    objectName: "themePicker"
    Item {
        anchors.fill: parent
        Image { sourceSize.width: width; sourceSize.height: height; x: 20; y: 17; width: 15; height: 15; source: picker.model.icon("set_project", picker.c.FG) }
        RakLabel { x: 46; y: 16; height: 20; text: picker.s.thm_title; color: picker.c.FG_BRIGHT; font.pixelSize: 14; font.weight: Font.DemiBold }
        Flickable {
            id: scroll
            objectName: "themeScroll"
            x: 20; y: 46; width: parent.width - 40; height: 358
            contentHeight: sections.height + 8; clip: true; boundsBehavior: Flickable.StopAtBounds
            Column {
                id: sections
                x: 2; y: 4; width: parent.width - 4; spacing: 14
                Repeater {
                    model: [false, true]
                    delegate: Column {
                        required property bool modelData
                        width: sections.width; spacing: 14
                        RakLabel { height: 14; text: parent.modelData ? picker.s.thm_section_light : picker.s.thm_section_dark; color: picker.c.FG_MUTED; font.pixelSize: 10; font.weight: Font.Bold; font.letterSpacing: 1 }
                        Grid {
                            columns: 4; columnSpacing: 12; rowSpacing: 12
                            Repeater {
                                model: picker.model.palettes.filter(p => !!p._is_light === parent.parent.modelData)
                                delegate: Column {
                                    required property var modelData
                                    spacing: 5
                                    ThemePreview { objectName: "themePreview_" + parent.modelData.key; colors: parent.modelData; selected: picker.model.paletteName === colors.key; onChosen: picker.model.choosePalette(colors.key) }
                                    RakLabel { width: 96; height: 14; horizontalAlignment: Text.AlignHCenter; text: parent.modelData.label; color: picker.c.FG_MUTED; font.pixelSize: 10 }
                                }
                            }
                        }
                    }
                }
            }
            C.ScrollBar.vertical: RakScrollBar { handleColor: picker.c.b18; hoverColor: picker.c.a50 }
        }
        RakLabel { x: 20; y: 414; height: 30; text: picker.s.thm_active.replace("{name}", picker.s.themeName); color: picker.c.FG_MUTED; font.pixelSize: 11 }
        ActionButton { objectName: "themeDone"; x: 418; y: 414; width: 82; height: 30; colors: picker.c; primary: true; cornerRadius: 6; text: picker.s.thm_btn_done; onClicked: picker.close() }
    }
}
