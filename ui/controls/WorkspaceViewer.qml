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
    Loader {
        anchors.fill: parent
        sourceComponent: viewer.kind==="hex"?hex:viewer.kind==="elf"?elf:viewer.kind==="image"?image:viewer.kind==="audio" || viewer.kind==="video"?media:viewer.kind==="payload" || viewer.kind==="super"?partition:archive
    }
    Component { id: hex; WorkspaceHexViewer {workspace:viewer.workspace;preferences:viewer.preferences;details:viewer.details} }
    Component { id: elf; WorkspaceSoViewer {workspace:viewer.workspace;preferences:viewer.preferences;details:viewer.details} }
    Component { id: image; WorkspaceImageViewer {workspace:viewer.workspace;preferences:viewer.preferences;details:viewer.details} }
    Component { id: media; WorkspaceMediaViewer {workspace:viewer.workspace;preferences:viewer.preferences;details:viewer.details} }
    Component { id: partition; WorkspacePartitionViewer {workspace:viewer.workspace;preferences:viewer.preferences;details:viewer.details} }
    Component { id: archive; WorkspaceArchiveViewer {workspace:viewer.workspace;preferences:viewer.preferences;details:viewer.details} }
}
