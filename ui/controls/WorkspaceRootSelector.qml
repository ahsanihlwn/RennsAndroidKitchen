// SPDX-License-Identifier: MPL-2.0
import QtQuick

WorkspaceChoiceButton {
    id: control
    objectName: "workspaceRootSelector"
    width: workspace.terminalRoot==="linux"?70:44; height: 24
    implicitWidth: width; implicitHeight: 24
    text: workspace.terminalRoot==="linux"?"Linux":workspace.terminalRoot==="tree2"?"2":"1"
    choices: workspace.terminalRoots; currentKey: workspace.terminalRoot
    font.pixelSize: 11; font.weight: Font.Medium
    leftPadding: 2; rightPadding: 5
    fillColor: colors.b06; hoverFillColor: colors.b12; downFillColor: colors.b16
    edgeColor: colors.b12; hoverEdgeColor: Qt.rgba(neutral.r,neutral.g,neutral.b,.24)
    readonly property color neutral: colors.b12
    labelColor: colors.FG2; hoverLabelColor: colors.FG_BRIGHT
    toolTip: workspace.terminalRoot==="linux"?"Terminal mode: Linux (WSL)":"Terminal mode"
    function selectionCaption(choice){return choice.key==='linux'?'Linux':choice.key==='tree2'?'2':'1'}
    onChosen: key => workspace.switchTerminalRoot(key)
    onPressed: workspace.probeWsl()
}
