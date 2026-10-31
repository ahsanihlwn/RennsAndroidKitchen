// SPDX-License-Identifier: MPL-2.0
import QtQuick
import QtQuick.Controls as C

C.Popup {
    id: dialog
    required property var model
    required property var hostWindow
    readonly property var c: model.colors
    readonly property var s: model.strings
    property Item backdropSource: null
    // Popup's visual root contains both its glass background and its content.
    // Nested menus live beside it in Overlay, so sampling this cannot recurse.
    readonly property Item backdropSurface: scroll.parent
    property string title: ""
    property bool agentProtected: false
    readonly property var agentApi: typeof rakAgentControls!=="undefined"?rakAgentControls:null
    property string agentSurfaceId: objectName || "dialog-"+Math.random().toString(16).slice(2)
    onPresentedChanged: if(agentApi)agentApi.protect(agentSurfaceId,agentProtected && presented)
    readonly property var transientParent: hostWindow
    property bool presented: false
    property bool finalClose: false
    property bool closing: false
    property bool active: false
    property real panelAlpha: 170/255
    property real panelRadius: 14
    property color panelColor: c.BG2
    signal prepareRequested()
    signal dismissed()
    default property alias body: content.data
    parent: hostWindow.contentItem
    width: Math.min(implicitWidth,hostWindow.width-24)
    height: Math.min(implicitHeight,hostWindow.height-24)
    x: Math.round((hostWindow.width-width)/2)
    y: Math.round((hostWindow.height-height)/2)
    popupType: C.Popup.Item
    padding: 0; focus: true; modal: true; dim: false
    closePolicy: C.Popup.CloseOnEscape
    function dismiss() { close() }
    function registerToStack() {
        if(hostWindow && hostWindow.dialogStack)hostWindow.dialogStack.registerDialog(dialog)
    }
    Component.onCompleted: registerToStack()
    Component.onDestruction: {
        if(hostWindow && hostWindow.dialogStack)hostWindow.dialogStack.unregisterDialog(dialog)
    }
    onAboutToShow: {
        registerToStack()
        finalClose=false; closing=false; presented=true
        scroll.contentY=0
        prepareRequested()
    }
    onOpened: content.forceActiveFocus(Qt.PopupFocusReason)
    onAboutToHide: { closing=true; presented=false }
    onClosed: dismissed()
    enter: Transition {
        SequentialAnimation {
            PropertyAction { target: dialog; property: "opacity"; value: dialog.model.settings.animations?0:1 }
            PauseAnimation { duration: dialog.model.settings.animations?120:0 }
            NumberAnimation { target: dialog; property: "opacity"; to: 1; duration: dialog.model.settings.animations?140:0; easing.type: Easing.OutCubic }
        }
    }
    exit: Transition {
        NumberAnimation { target: dialog; property: "opacity"; to: 0; duration: dialog.model.settings.animations && !dialog.finalClose?120:0; easing.type: Easing.OutCubic }
    }
    background: LiveGlass {
        id: surface
        objectName: "dialogGlass"
        preferences: dialog.model
        backdropSource: dialog.backdropSource; origin: Qt.point(dialog.x,dialog.y)
        effectsEnabled: dialog.model.settings.effects!=="low"
        flatColor: dialog.c.BG_MODAL
        tintColor: Qt.rgba(dialog.panelColor.r,dialog.panelColor.g,dialog.panelColor.b,dialog.panelAlpha)
        cornerRadius: dialog.panelRadius
        edgeColor: dialog.model.panelBorder
    }
    contentItem: Flickable {
        id: scroll
        objectName: "dialogScroll"
        contentWidth: width; contentHeight: dialog.implicitHeight
        clip: true; boundsBehavior: Flickable.StopAtBounds
        focus: true
        Keys.onEscapePressed: dialog.dismiss()
        C.ScrollBar.vertical: RakScrollBar {
            objectName: "dialogScrollBar"
            policy: C.ScrollBar.AsNeeded
            visible: scroll.contentHeight>scroll.height+.5
            handleColor: dialog.c.BG_SCROLL
        }
        Item { id: content; width: parent.width; height: dialog.implicitHeight; focus: true }
    }
}
