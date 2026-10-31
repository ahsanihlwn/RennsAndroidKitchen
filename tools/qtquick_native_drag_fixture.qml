// SPDX-License-Identifier: MPL-2.0
import QtQuick
import QtQuick.Window
import "../ui/controls"
Window {
    id: window
    required property var workspace
    required property var preferences
    property var dragTerminal: null
    property var dialogStack: null
    readonly property var managedDialogs: [settings]
    readonly property Item backdropSurface: scene
    title: "RAK native drag verification - temporary files"
    width: 1080; height: 700; visible: true; color: preferences.colors.BG
    Item {
        id: scene; anchors.fill: parent
        WorkspaceTree {x:11;y:48;width:(parent.width-28)/2;height:parent.height-59-(window.dragTerminal?100:0);workspace:window.workspace;preferences:window.preferences;directory:workspace.treeModel;treeId:0}
        WorkspaceTree {x:17+(parent.width-28)/2;y:48;width:(parent.width-28)/2;height:parent.height-59-(window.dragTerminal?100:0);workspace:window.workspace;preferences:window.preferences;directory:workspace.secondTreeModel;treeId:1}
        Loader {
            x: 11; y: parent.height-100; width: parent.width-22; height: 89; active: !!window.dragTerminal
            sourceComponent: TerminalPane {objectName: "dragTerminalPane";session:window.dragTerminal;fillColor:window.preferences.colors.BG3}
        }
        ActionButton {x:11;y:10;colors:window.preferences.colors;text:"Settings";onClicked:window.workspace.dispatch('settings',{})}
    }
    WorkspaceSettings {id:settings;workspace:window.workspace;model:window.preferences;hostWindow:window}
    Connections {target:window.workspace;function onPresentationRequested(kind,data){if(kind==='settings')settings.showSettings()}}
}
