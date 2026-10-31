// SPDX-License-Identifier: MPL-2.0
import QtQuick
import QtQuick.Window
import "../ui/controls"

Window {
    width: 180; height: 90; color: "#1a1a1a"
    flags: Qt.Tool | Qt.FramelessWindowHint | Qt.WindowDoesNotAcceptFocus
    Spinner { objectName: "largeSpinner"; x: 10; y: 10; accent: "#3ecf8e"; spinning: false }
    Spinner { objectName: "mediumSpinner"; x: 65; y: 10; width: 28; height: 28; thickness: 2; inset: 3; accent: "#3ecf8e"; spinning: false }
    Spinner { objectName: "smallSpinner"; x: 110; y: 10; width: 16; height: 16; thickness: 3; inset: 3; accent: "#3ecf8e"; spinning: false }
}
