// SPDX-License-Identifier: MPL-2.0
import QtQuick
import QtQuick.Controls as C

MorphPopup {
    id: projectMenu
    objectName: "projectActionsPopup"
    required property var hostWindow
    required property var pageSurface
    property var details: ({})
    property var pendingAction: null
    readonly property real optionWidth: {
        let result = 0
        for (let i = 0; i < menuOptions.count; ++i) result = Math.max(result, menuOptions.itemAt(i).implicitWidth)
        return result
    }
    springMotion: false
    backdropSource: pageSurface
    effectsEnabled: hostWindow.uiModel.settings.effects !== "low"
    animationsEnabled: hostWindow.uiModel.settings.animations
    padding: 0
    width: Math.max(120, optionWidth + 8)
    height: menuItems.implicitHeight + 8
    panelColor: hostWindow.colors.BG_MODAL
    tintBaseColor: hostWindow.colors.BG2
    panelBorder: hostWindow.uiModel.panelBorder
    panelRadius: 18
    closePolicy: C.Popup.CloseOnEscape | C.Popup.CloseOnPressOutside
    onAboutToShow: pendingAction = null
    onClosed: if (pendingAction) { const action = pendingAction; pendingAction = null; hostWindow.uiModel.dispatch("projectAction", action) }
    Column {
        id: menuItems
        x: 4; y: 4; spacing: 0
        Repeater {
            id: menuOptions
            model: projectMenu.details.actions || []
            delegate: Item {
                id: option
                required property var modelData
                required property int index
                width: projectMenu.width - 8; height: index ? 35 : 32
                implicitWidth: action.implicitWidth
                SmoothRectangle { visible: option.index > 0; width: parent.width; height: 1; color: hostWindow.colors.b07 }
                RakButton {
                    id: action
                    objectName: "projectAction_" + modelData.action
                    y: option.index ? 3 : 0
                    width: parent.width; height: 32; leftAligned: true; leftPadding: 14
                    text: option.modelData.label; enabled: option.modelData.enabled !== false
                    font.pixelSize: 12; cornerRadius: 6
                    labelColor: option.modelData.danger ? hostWindow.colors.RED_LIGHT : hostWindow.colors.FG
                    hoverFillColor: hostWindow.colors.a12
                    animatePress: hostWindow.uiModel.settings.animations
                    onClicked: {
                        projectMenu.pendingAction = {action: option.modelData.action, path: projectMenu.details.path, id: projectMenu.details.id || ""}
                        projectMenu.close()
                    }
                }
            }
        }
    }
}
