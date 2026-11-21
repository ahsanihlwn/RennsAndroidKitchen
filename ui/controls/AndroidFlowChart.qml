// SPDX-License-Identifier: MPL-2.0
import QtQuick
import QtQuick.Shapes
import QtQuick.Effects

Item {
    id: chart
    required property var backend
    required property var preferences
    readonly property var c: preferences.colors
    readonly property color purple: c.PURPLE
    readonly property color accent: c.accent_hex
    property var layout: backend.chart
    property real progress: 0
    readonly property real phase: Math.max(0,Math.min(1,(progress-.12)/.88))
    readonly property real eased: phase<.5?2*phase*phase:-1+(4-2*phase)*phase
    readonly property real flowY: 58+eased*(layout.spineBottom-58)
    width: 408; height: layout.height
    function restart() { timeline.restart() }
    Component.onCompleted: restart()
    NumberAnimation { id: timeline; target: chart; property: "progress"; from: 0; to: 1; duration: chart.preferences.settings.animations?2400:0 }
    Shape {
        anchors.fill: parent; preferredRendererType: Shape.CurveRenderer
        ShapePath { strokeColor: "#373e55"; strokeWidth: 1.5; fillColor: "transparent"; startX: 204; startY: 58; PathLine { x: 204; y: chart.layout.spineBottom } }
        ShapePath { strokeColor: chart.c.accent_hex; strokeWidth: 2; fillColor: "transparent"; startX: 204; startY: 58; PathLine { x: 204; y: chart.flowY } }
    }
    Repeater {
        model: chart.layout.edges
        delegate: Item {
            id: branch
            required property var modelData
            readonly property real amount: Math.max(0,Math.min(1,(chart.flowY-modelData.y)/(Math.abs(modelData.x-204)*.45)))
            width: chart.width; height: chart.height
            Shape {
                anchors.fill: parent; preferredRendererType: Shape.CurveRenderer
                ShapePath { strokeColor: "#373e55"; strokeWidth: 1.2; fillColor: "transparent"; startX: 204; startY: modelData.y; PathLine { x: modelData.x; y: modelData.y } }
            }
            Shape {
                anchors.fill: parent; preferredRendererType: Shape.CurveRenderer
                ShapePath {
                    strokeColor: Qt.rgba(chart.accent.r,chart.accent.g,chart.accent.b,modelData.active?.88:.22)
                    strokeWidth: modelData.active?2:1.2; fillColor: "transparent"; startX: 204; startY: modelData.y
                    PathLine { x: 204+(modelData.x-204)*branch.amount; y: modelData.y }
                }
            }
        }
    }
    SmoothRectangle {
        x: 129; y: 20; width: 150; height: 38; radius: 8; color: c.BG4; border.width: 1; border.color: c.a40
        opacity: chart.progress>.02?1:0
        Behavior on opacity { NumberAnimation { duration: chart.preferences.settings.animations?180:0 } }
        RakLabel { anchors.fill: parent; anchors.margins: 8; text: chart.backend.badge.split("  ·  ")[0]; elide: Text.ElideRight; horizontalAlignment: Text.AlignHCenter; font.pixelSize: 11; font.weight: Font.DemiBold; color: chart.c.FG }
    }
    Repeater {
        model: chart.layout.nodes
        delegate: RakButton {
            id: node
            required property var modelData
            objectName: "androidNode_"+modelData.name
            readonly property color stateColor: modelData.state==="flashing"?"#edaa4b":modelData.state==="done"?"#4dc47b":modelData.state==="error"?"#e95761":chart.c.accent_hex
            readonly property bool nodeHighlighted: node.visualHovered || drop.containsDrag || modelData.state!=="idle" || modelData.active
            x: modelData.x+3; y: modelData.y+3; width: 106; height: 44; implicitWidth: 106; implicitHeight: 44
            leftPadding: 0; rightPadding: 0; topPadding: 0; bottomPadding: 0
            cornerRadius: 7; edgeWidth: 1
            animatePress: chart.preferences.settings.animations
            fillColor: modelData.state==="flashing"?"#1e1a14":modelData.state==="done"?"#131d14":modelData.state==="error"?"#1d1213":chart.c.BG4
            hoverFillColor: fillColor; downFillColor: fillColor
            edgeColor: node.nodeHighlighted?Qt.rgba(node.stateColor.r,node.stateColor.g,node.stateColor.b,node.visualHovered?.75:.5):Qt.rgba(chart.purple.r,chart.purple.g,chart.purple.b,.28)
            hoverEdgeColor: Qt.rgba(node.stateColor.r,node.stateColor.g,node.stateColor.b,.75)
            opacity: chart.progress>=1 || chart.flowY>=modelData.centerY+26?1:0
            Behavior on opacity { NumberAnimation { duration: chart.preferences.settings.animations?135:0 } }
            layer.enabled: node.nodeHighlighted && chart.preferences.settings.effects!=="low"
            layer.effect: MultiEffect { shadowEnabled: true; shadowColor: node.stateColor; shadowOpacity: .18; shadowBlur: .4; shadowHorizontalOffset: 0; shadowVerticalOffset: 0 }
            onClicked: chart.backend.assign(modelData.name,"","")
            contentItem: Item {
                RakLabel { x: 5; y: 8; width: 96; height: 14; text: node.modelData.name.length>14?node.modelData.name.slice(0,12)+"…":node.modelData.name; font.pixelSize: 10; font.weight: Font.DemiBold; horizontalAlignment: Text.AlignHCenter; color: chart.c.FG; transformed: node.transformed }
                RakLabel { x: 5; y: 23; width: 96; height: 12; text: node.modelData.image?"← "+node.modelData.image:node.modelData.size; elide: Text.ElideRight; font.pixelSize: 9; horizontalAlignment: Text.AlignHCenter; color: node.modelData.image?chart.c.FG2:chart.c.FG3; transformed: node.transformed }
            }
            DropArea {
                id: drop; anchors.fill: parent; keys: ["application/x-rak-image"]
                onDropped: event => {const value=event.getDataAsString("application/x-rak-image").split("\n");if(value.length>1){chart.backend.assign(modelData.name,value[0],value.slice(1).join("\n"));event.acceptProposedAction()}}
            }
        }
    }
    SmoothRectangle {
        x: 200; y: chart.flowY-4; width: 8; height: 8; radius: 4; color: "#ebb4dcff"
        visible: chart.progress>=.12 && chart.progress<.97
        layer.enabled: true
        layer.effect: MultiEffect { shadowEnabled: true; shadowColor: "#b4dcff"; shadowOpacity: .58; shadowBlur: .5; shadowHorizontalOffset: 0; shadowVerticalOffset: 0 }
    }
}
