import QtQuick
import QtQuick.Window
import QtTerm 1.0

Window {
    id: win
    width: 980
    height: 620
    visible: true
    color: "#1e1e2e"
    title: term.title !== "" ? term.title : "QtTerm"

    Terminal {
        id: term
        anchors.fill: parent
        focus: true
        program: cmdProgram
        arguments: cmdArguments
        fontFamily: fontOverride
        fontPixelSize: 15
        padding: 8
        foreground: "#cdd6f4"
        background: "#1e1e2e"
        cursorColor: "#f5e0dc"
        selectionColor: "#585b7099"
        palette: ["#45475a", "#f38ba8", "#a6e3a1", "#f9e2af", "#89b4fa", "#f5c2e7", "#94e2d5", "#bac2de",
                  "#585b70", "#f38ba8", "#a6e3a1", "#f9e2af", "#89b4fa", "#f5c2e7", "#94e2d5", "#a6adc8"]
        onBell: bellFlash.restart()
    }

    // Overlay scrollbar (only when there is scrollback and the app is not tracking the mouse)
    Rectangle {
        id: track
        visible: term.historySize > 0 && !term.mouseTracking
        anchors { right: parent.right; top: parent.top; bottom: parent.bottom; margins: 2 }
        width: 8
        radius: 4
        color: "#20ffffff"
        Rectangle {
            readonly property real total: term.historySize + term.rows
            width: parent.width
            radius: 4
            color: dragArea.pressed ? "#99cdd6f4" : "#55cdd6f4"
            height: Math.max(24, parent.height * term.rows / total)
            y: (parent.height - height) * (1 - term.scrollOffset / Math.max(1, term.historySize))
        }
        MouseArea {
            id: dragArea
            anchors.fill: parent
            anchors.leftMargin: -6
            function seek(my) {
                var f = 1 - Math.min(1, Math.max(0, my / height))
                term.scrollOffset = Math.round(f * term.historySize)
            }
            onPressed: (m) => seek(m.y)
            onPositionChanged: (m) => { if (pressed) seek(m.y) }
        }
    }

    Rectangle {
        id: bellFlash
        anchors.fill: parent
        color: "white"
        opacity: 0
        function restart() { anim.restart() }
        NumberAnimation on opacity { id: anim; running: false; from: 0.18; to: 0; duration: 180 }
    }

    Rectangle {
        visible: !term.running
        anchors { bottom: parent.bottom; right: parent.right; margins: 12 }
        width: msg.width + 24
        height: msg.height + 14
        radius: 6
        color: "#313244"
        border.color: "#585b70"
        Text {
            id: msg
            anchors.centerIn: parent
            color: "#cdd6f4"
            font.pixelSize: 13
            text: "Process exited (" + term.exitCode + ")  ·  click to restart"
        }
        MouseArea { anchors.fill: parent; onClicked: { term.start(); term.forceActiveFocus() } }
    }
}
