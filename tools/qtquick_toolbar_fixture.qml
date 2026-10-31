// SPDX-License-Identifier: MPL-2.0
import QtQuick
import QtQuick.Window
import "../ui/controls"

Window {
    id: window
    required property var preferences
    property var dialogStack: null
    readonly property var managedDialogs: []
    readonly property Item backdropSurface: page
    property alias lastAction: stubWorkspace.lastAction
    property alias dispatchCount: stubWorkspace.dispatchCount
    property alias rightOpen: stubWorkspace.rightOpen
    width: 800; height: 400; visible: true; color: preferences.colors.BG
    title: "RAK toolbar morph verification"
    QtObject {
        id: stubWorkspace
        property var privateFeatures: ({allowed:false})
        property bool rightOpen: true
        property bool prepared: true
        property string lastAction: ""
        property int dispatchCount: 0
        signal changed()
        onRightOpenChanged: changed()
        function dispatch(key,data){lastAction=key;dispatchCount++}
    }
    Rectangle {
        id: page; anchors.fill: parent; color: preferences.colors.BG
        WorkspaceToolbar {
            objectName: "fixtureToolbar"
            x: 16; y: 10; width: 230; height: 60
            workspace: stubWorkspace; preferences: window.preferences; hostWindow: window
        }
    }
}
