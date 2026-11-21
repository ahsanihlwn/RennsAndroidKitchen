// SPDX-License-Identifier: MPL-2.0
import QtQuick
import QtQuick.Shapes

Item {
    id: entry
    required property var workspace
    required property var preferences
    required property var details
    readonly property var d: details || ({})
    readonly property bool empty: !!d.empty
    property int depth: 0
    property bool treeMode: false
    property bool settled: true
    readonly property int layoutWidth: Math.floor(width)
    readonly property var c: preferences.colors
    readonly property var entryState: d.presentation || null
    readonly property string thumbnail: (entryState?entryState.thumbnailUrl:d.thumbnailUrl) || ""
    readonly property var layout: workspace.entryLayout(d.name || "",d.sizeLabel || "",d.badge || "",!!d.symlinkExists,
                                                        d.branchColor || "",layoutWidth,38+(treeMode?depth*20+5:0))
    readonly property var chipColors: workspace.badgeColors(d.badge || "",c)
    readonly property var branchColors: ({red:"#e05555",blue:"#4d8ef0",yellow:"#f0c040",green:"#4db860",orange:"#f08030",purple:"#9060d0",pink:"#e070a0"})
    clip: true
    Item {
        visible: entry.treeMode; width: (entry.depth+1)*20; height: 28
        SmoothRectangle { anchors.fill: parent; color: entry.c.BG3 }
        Repeater {
            model: entry.entryState?entry.entryState.branchLines:(entry.d.branchLines || [])
            delegate: SmoothRectangle {
                required property int index; required property bool modelData
                visible: modelData; x: index*20+10; y: 0; width: 1; height: 28; color: entry.c.b18
            }
        }
        SmoothRectangle { visible: entry.depth>0; x: (entry.depth-1)*20+10; y: 0; width: 1; height: (entry.entryState?entry.entryState.lastSibling:entry.d.lastSibling)?14:28; color: entry.c.b18 }
        SmoothRectangle { visible: entry.depth>0; x: (entry.depth-1)*20+10; y: 14; width: 10; height: 1; color: entry.c.b18 }
    }
    Image { objectName: "treeEntryIcon"; x: treeMode?16+depth*20:15; y: 4; width: 20; height: 20; visible: !entry.empty && !entry.thumbnail.length; source: entry.empty?"":workspace.fileIconUrl(d.iconKey || 'file',preferences.paletteName); sourceSize: Qt.size(28,28); fillMode: Image.PreserveAspectFit; smooth: true; asynchronous: true; cache: true }
    Image { x: treeMode?16+depth*20:15; y: 4; width: 20; height: 20; visible: !!entry.thumbnail.length; source: entry.thumbnail; sourceSize: Qt.size(28,28); fillMode: Image.PreserveAspectFit; smooth: true; asynchronous: true; cache: true }
    RakLabel { objectName: "treeEntryName"; x: entry.layout.textX; y: 1; height: 28; text: entry.layout.text; color: entry.empty?Qt.rgba(200/255,200/255,200/255,130/255):c.FG; font.pixelSize: 13; font.italic: entry.empty; verticalAlignment: Text.AlignVCenter }
    RakLabel { x: entry.layout.sizeX; y: 0; height: 28; width: entry.layout.sizeWidth; text: entry.d.sizeLabel || ""; color: c.FG3; font.pixelSize: 12; verticalAlignment: Text.AlignVCenter }
    SmoothRectangle {
        visible: entry.layout.hasBadge; x: entry.layout.chipX; y: entry.layout.chipY
        width: entry.layout.chipWidth; height: entry.layout.chipHeight; radius: 3; color: entry.chipColors.background
        RakLabel { x: 5; y: 2+entry.layout.chipAscent-baselineOffset; text: entry.d.badge || ""; color: entry.chipColors.foreground; font.pixelSize: 9; font.bold: true }
    }
    SmoothRectangle {
        visible: !!entry.d.symlinkExists
        x: entry.layout.dotX; y: 11.5; width: 5; height: 5; radius: 2.5
        color: "#43b77a"; border.width: .5; border.color: "#46000000"
        SmoothRectangle { x: 1.3; y: 1.3; width: 2.4; height: 2.4; radius: 1.2; color: "white" }
    }
    Item {
        id: branch
        visible: !!entry.d.branchColor
        x: entry.layout.branchX-4; y: 9; width: 8; height: 10
        readonly property color color: entry.branchColors[entry.d.branchColor] || "#4d8ef0"
        Shape {
            anchors.fill: parent; preferredRendererType: Shape.CurveRenderer
            ShapePath {
                strokeColor: branch.color; strokeWidth: 1.4; fillColor: "transparent"; capStyle: ShapePath.RoundCap
                startX: 0; startY: 6.4
                PathLine { x: 0; y: 3 }
                PathCubic { control1X: 0; control1Y: -2; control2X: 8; control2Y: 6.8; x: 8; y: 3.6 }
            }
        }
        SmoothRectangle { x: -1.8; y: 6.4; width: 3.6; height: 3.6; radius: 1.8; color: branch.color }
        SmoothRectangle { x: 6.2; y: 0; width: 3.6; height: 3.6; radius: 1.8; color: branch.color }
    }
}
