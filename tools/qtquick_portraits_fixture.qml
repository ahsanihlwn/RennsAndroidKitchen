// SPDX-License-Identifier: MPL-2.0
import QtQuick
import QtQuick.Window
import "../ui/controls"

Window {
    width: 360; height: 180
    required property url whiteSource
    required property url stripeSource
    RoundedImage { objectName: "circleProbe"; x: 10; y: 10; width: 48; height: 48; source: whiteSource }
    RoundedImage { objectName: "creditProbe"; x: 80; y: 10; width: 48; height: 48; source: "image://rak/renns_project" }
    RoundedImage { objectName: "avatarProbe"; x: 150; y: 10; width: 64; height: 64; source: stripeSource }
}
