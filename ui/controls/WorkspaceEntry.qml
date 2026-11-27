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
    readonly property bool dummyLoading: empty && !!(entryState ? entryState.loading : d.loading)
    readonly property bool isLight: !!(c && c.is_light)
    property real skelPhase: 0.4
    SequentialAnimation on skelPhase {
        loops: Animation.Infinite
        running: entry.visible && entry.dummyLoading && !(preferences && preferences.settings && preferences.settings.animations === false)
        NumberAnimation { from: 0.0; to: 1.0; duration: 750; easing.type: Easing.InOutSine }
        NumberAnimation { from: 1.0; to: 0.0; duration: 750; easing.type: Easing.InOutSine }
    }
    readonly property color boneColor: isLight
        ? Qt.rgba(0.08, 0.10, 0.14, 0.07 + 0.055 * skelPhase)
        : Qt.rgba(1.0, 1.0, 1.0, 0.05 + 0.045 * skelPhase)
    readonly property color boneSoftColor: isLight
        ? Qt.rgba(0.08, 0.10, 0.14, 0.04 + 0.035 * skelPhase)
        : Qt.rgba(1.0, 1.0, 1.0, 0.028 + 0.03 * skelPhase)
    property int depth: 0
    property bool treeMode: false
    property bool settled: true
    readonly property int layoutWidth: Math.floor(width)
    readonly property var c: preferences.colors
    readonly property var entryState: d.presentation || null
    readonly property string thumbnail: (entryState?entryState.thumbnailUrl:d.thumbnailUrl) || ""
    readonly property bool gkiBadge: !!(entryState ? entryState.gkiBadge : d.gkiBadge)
    readonly property var layout: workspace.entryLayout(d.name || "",d.sizeLabel || "",d.badge || "",!!d.symlinkExists,
                                                        d.branchColor || "",layoutWidth,38+(treeMode?depth*20+5:0),gkiBadge)
    readonly property var chipColors: workspace.badgeColors(d.badge || "",c)
    readonly property var gkiColors: gkiBadge ? workspace.badgeColors("GKI",c) : ({})
    readonly property var branchColors: ({red:"#e05555",blue:"#4d8ef0",yellow:"#f0c040",green:"#4db860",orange:"#f08030",purple:"#9060d0",pink:"#e070a0"})
    property int opRevision: 0
    property int lockRevision: 0
    readonly property string opPath: d.path || ""
    readonly property bool entryLocked: {
        opRevision
        lockRevision
        if (entry.empty) return false
        if (!!(entryState ? entryState.readOnly : d.readOnly)) return true
        return !!(opPath && workspace.isPathLocked(opPath))
    }
    readonly property string opState: {
        opRevision
        return (!entry.empty && opPath) ? workspace.pathHighlightState(opPath) : ""
    }
    readonly property int opProgress: {
        opRevision
        return (opPath && opState === "loading") ? workspace.pathProgress(opPath) : -1
    }
    property string shownOpState: ""
    onOpStateChanged: if (opState !== "") shownOpState = opState
    Connections {
        target: entry.workspace
        function onHighlightsChanged() { entry.opRevision++ }
        function onStatusChanged() { entry.opRevision++ }
        function onLocksChanged() { entry.lockRevision++ }
    }
    Connections {
        target: (entry.workspace && entry.workspace.mounts) ? entry.workspace.mounts : null
        function onChanged() { entry.lockRevision++ }
    }
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
    Item {
        visible: entry.dummyLoading
        anchors.fill: parent
        SmoothRectangle {
            x: entry.treeMode ? 17 + entry.depth * 20 : 16; y: 6
            width: 16; height: 16; radius: 4
            color: entry.boneColor
        }
        SmoothRectangle {
            x: entry.layout.textX; y: 9
            width: Math.min(148, Math.max(56, entry.width - x - 20))
            height: 10; radius: 5
            color: entry.boneSoftColor
        }
    }
    Image { objectName: "treeEntryIcon"; x: treeMode?16+depth*20:15; y: 4; width: 20; height: 20; visible: !entry.empty && !entry.thumbnail.length; source: entry.empty?"":workspace.fileIconUrl(d.iconKey || 'file',preferences.paletteName); sourceSize: Qt.size(28,28); fillMode: Image.PreserveAspectFit; smooth: true; asynchronous: true; cache: true }
    Image { x: treeMode?16+depth*20:15; y: 4; width: 20; height: 20; visible: !!entry.thumbnail.length; source: entry.thumbnail; sourceSize: Qt.size(28,28); fillMode: Image.PreserveAspectFit; smooth: true; asynchronous: true; cache: true }
    Item {
        id: lockBadge
        objectName: "treeEntryLockBadge"
        visible: entry.entryLocked
        x: (entry.treeMode ? 16 + entry.depth * 20 : 15) + 10
        y: 14
        width: 11; height: 11
        z: 2
        SmoothRectangle {
            anchors.fill: parent
            radius: 3.5
            color: entry.c.BG3
            border.width: 1
            border.color: entry.c.b18
        }
        WorkspaceNativeIcon {
            anchors.centerIn: parent
            iconKey: "lock"
            iconSize: 8
            strokeColor: entry.c.FG_BRIGHT
            isLight: !!(entry.c && entry.c.is_light)
        }
    }
    RakLabel { id: treeEntryNameLabel; objectName: "treeEntryName"; x: entry.layout.textX; y: 1; height: 28; visible: !entry.dummyLoading; text: entry.layout.text; color: entry.empty?Qt.rgba(200/255,200/255,200/255,130/255):c.FG; font.pixelSize: 13; font.italic: entry.empty; verticalAlignment: Text.AlignVCenter }
    RakLabel { x: entry.layout.sizeX; y: 0; height: 28; width: entry.layout.sizeWidth; text: entry.d.sizeLabel || ""; color: c.FG3; font.pixelSize: 12; verticalAlignment: Text.AlignVCenter }
    SmoothRectangle {
        visible: entry.layout.hasBadge; x: entry.layout.chipX; y: entry.layout.chipY
        width: entry.layout.chipWidth; height: entry.layout.chipHeight; radius: 3; color: entry.chipColors.background
        RakLabel { x: 5; y: 2+entry.layout.chipAscent-baselineOffset; text: entry.d.badge || ""; color: entry.chipColors.foreground; font.pixelSize: 9; font.bold: true }
    }
    SmoothRectangle {
        objectName: "gkiBadge"
        visible: entry.gkiBadge; x: entry.layout.gkiX; y: entry.layout.chipY
        width: entry.layout.gkiWidth; height: entry.layout.chipHeight; radius: 3
        color: entry.gkiColors.background || "transparent"
        RakLabel { x: 5; y: 2+entry.layout.chipAscent-baselineOffset; text: "GKI"; color: entry.gkiColors.foreground || entry.c.FG; font.pixelSize: 9; font.bold: true }
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
    Item {
        id: opIndicator
        objectName: "treeEntryOpIndicator"
        readonly property real anchorX: entry.d.branchColor
            ? (entry.layout.branchX + 10)
            : entry.d.symlinkExists
                ? (entry.layout.dotX + 10)
                : entry.gkiBadge
                    ? (entry.layout.gkiX + entry.layout.gkiWidth + 6)
                : entry.layout.hasBadge
                    ? (entry.layout.chipX + entry.layout.chipWidth + 6)
                    : (entry.layout.sizeWidth > 0 ? (entry.layout.sizeX + entry.layout.sizeWidth + 6) : (treeEntryNameLabel.x + treeEntryNameLabel.implicitWidth + 6))
        x: Math.min(Math.max(0, entry.layoutWidth - width - 4), anchorX)
        y: 6; width: 16; height: 16
        opacity: entry.opState !== "" ? 1 : 0
        visible: opacity > 0.01
        Behavior on opacity { NumberAnimation { duration: 480; easing.type: Easing.InOutQuad } }

        readonly property bool isLoading: entry.shownOpState === "loading" && entry.opState === "loading"
        readonly property bool isDone: entry.shownOpState === "done"
        readonly property bool isError: entry.shownOpState === "error"

        Spinner {
            anchors.centerIn: parent
            width: 14; height: 14
            accent: entry.c.accent_hex
            thickness: 2.0; inset: 2.0
            visible: opIndicator.isLoading && entry.opProgress < 0
            spinning: visible
            opacity: visible ? 1 : 0
            Behavior on opacity { NumberAnimation { duration: 180 } }
        }
        Shape {
            id: entryProgressRing
            anchors.centerIn: parent
            width: 15; height: 15
            preferredRendererType: Shape.CurveRenderer
            antialiasing: true
            readonly property color ringColor: entry.c.accent_hex
            opacity: (opIndicator.isLoading && entry.opProgress >= 0) ? 1 : 0
            scale: opacity > 0.01 ? 1 : 0.65
            visible: opacity > 0.01
            Behavior on opacity { NumberAnimation { duration: 200 } }
            Behavior on scale { NumberAnimation { duration: 200; easing.type: Easing.OutCubic } }
            ShapePath {
                strokeColor: Qt.rgba(entryProgressRing.ringColor.r, entryProgressRing.ringColor.g, entryProgressRing.ringColor.b, 0.22)
                strokeWidth: 2.1; capStyle: ShapePath.RoundCap; fillColor: "transparent"
                PathAngleArc { centerX: 7.5; centerY: 7.5; radiusX: 5.5; radiusY: 5.5; startAngle: 0; sweepAngle: 360 }
            }
            ShapePath {
                strokeColor: entryProgressRing.ringColor
                strokeWidth: 2.1; capStyle: ShapePath.RoundCap; fillColor: "transparent"
                PathAngleArc {
                    centerX: 7.5; centerY: 7.5; radiusX: 5.5; radiusY: 5.5
                    startAngle: -90
                    sweepAngle: 360 * Math.max(0, Math.min(100, entry.opProgress)) / 100
                    Behavior on sweepAngle { NumberAnimation { duration: 180; easing.type: Easing.OutCubic } }
                }
            }
        }
        Shape {
            anchors.centerIn: parent
            width: 14; height: 14
            preferredRendererType: Shape.CurveRenderer
            antialiasing: true
            opacity: opIndicator.isDone ? 1 : 0
            scale: opIndicator.isDone ? 1 : 0.55
            visible: opacity > 0.01
            Behavior on opacity { NumberAnimation { duration: 240; easing.type: Easing.OutCubic } }
            Behavior on scale { NumberAnimation { duration: 260; easing.type: Easing.OutBack } }
            ShapePath {
                strokeColor: entry.c.GREEN_STATUS || "#22c55e"
                strokeWidth: 2.1; fillColor: "transparent"
                capStyle: ShapePath.RoundCap; joinStyle: ShapePath.RoundJoin
                PathSvg { path: "M 2.8 7.5 L 5.8 10.5 L 11.4 4.2" }
            }
        }
        Shape {
            anchors.centerIn: parent
            width: 14; height: 14
            preferredRendererType: Shape.CurveRenderer
            antialiasing: true
            opacity: opIndicator.isError ? 1 : 0
            scale: opIndicator.isError ? 1 : 0.55
            visible: opacity > 0.01
            Behavior on opacity { NumberAnimation { duration: 220 } }
            Behavior on scale { NumberAnimation { duration: 240; easing.type: Easing.OutBack } }
            ShapePath {
                strokeColor: entry.c.RED_STATUS || "#ef4444"
                strokeWidth: 2.1; fillColor: "transparent"
                capStyle: ShapePath.RoundCap; joinStyle: ShapePath.RoundJoin
                PathSvg { path: "M 3.8 3.8 L 10.2 10.2 M 10.2 3.8 L 3.8 10.2" }
            }
        }
    }
}
