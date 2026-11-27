// SPDX-License-Identifier: MPL-2.0
import QtQuick
import QtQuick.Controls as C
import Renns.Workspace
Item {
    id: viewer
    required property var workspace
    required property var preferences
    required property var details
    readonly property string kind: details.kind || ""
    property string _loadedComponent: ""
    function componentPathForKind(k) {
        if (k === "hex") return "WorkspaceHexViewer.qml"
        if (k === "elf") return "WorkspaceSoViewer.qml"
        if (k === "image") return "WorkspaceImageViewer.qml"
        if (k === "markdown") return "WorkspaceMarkdownViewer.qml"
        if (k === "audio" || k === "video") return "WorkspaceMediaViewer.qml"
        if (k === "payload" || k === "super" || k === "oem") return "WorkspacePartitionViewer.qml"
        return "WorkspaceArchiveViewer.qml"
    }
    function syncViewer() {
        const comp = componentPathForKind(viewer.kind)
        if (_loadedComponent === comp && contentLoader.status === Loader.Ready && contentLoader.item) {
            contentLoader.item.details = viewer.details
            return
        }
        _loadedComponent = comp
        contentLoader.setSource(
            comp,
            {
                workspace: viewer.workspace,
                preferences: viewer.preferences,
                details: viewer.details
            }
        )
    }
    Component.onCompleted: syncViewer()
    onDetailsChanged: syncViewer()
    onKindChanged: syncViewer()
    Loader {
        id: contentLoader
        anchors.fill: parent
    }
    RakLabel {
        anchors.centerIn: parent
        visible: contentLoader.status === Loader.Error
        width: Math.min(parent.width - 48, 520)
        wrapMode: Text.WordWrap
        horizontalAlignment: Text.AlignHCenter
        color: viewer.preferences.colors.FG2
        font.pixelSize: 12
        text: "Viewer component unavailable (" + viewer.kind + ")."
    }
}
