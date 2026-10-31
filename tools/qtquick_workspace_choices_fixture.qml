// SPDX-License-Identifier: MPL-2.0
import QtQuick
import QtQuick.Window
import "../ui/controls"
Window {
    id: window
    required property var workspace
    required property var preferences
    property var dialogStack: null
    readonly property var managedDialogs: [settings,form]
    readonly property Item backdropSurface: page
    width: 1080; height: 700; visible: true; color: preferences.colors.BG3
    Rectangle {id: page;anchors.fill: parent;color: window.preferences.colors.BG3}
    WorkspaceSettings {
        id: settings;workspace: window.workspace;model: window.preferences;hostWindow: window
        Rectangle {objectName:"modalBackdropMarker";visible:false;z:99;color:"red"}
    }
    WorkspaceActionDialog {id: form;workspace: window.workspace;model: window.preferences;hostWindow: window}
    Connections {
        target: window.workspace
        function onPresentationRequested(kind,data) {
            if(kind==="settings")settings.showSettings()
            else if(kind==="actionDialog")form.showForm(data)
        }
    }
}
