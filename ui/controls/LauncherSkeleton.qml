// SPDX-License-Identifier: MPL-2.0
import QtQuick

Item {
    id: skeleton
    required property var uiModel
    readonly property var c: uiModel.colors
    readonly property bool anims: uiModel && uiModel.settings ? uiModel.settings.animations : true
    readonly property bool isLight: !!(c && c.is_light)

    // Single shared breathing phase so all placeholder bones pulse in sync cleanly.
    property real phase: 0.0
    SequentialAnimation on phase {
        loops: Animation.Infinite
        running: skeleton.visible && skeleton.opacity > 0.01 && skeleton.anims
        NumberAnimation { from: 0.0; to: 1.0; duration: 750; easing.type: Easing.InOutSine }
        NumberAnimation { from: 1.0; to: 0.0; duration: 750; easing.type: Easing.InOutSine }
    }

    readonly property color boneColor: isLight
        ? Qt.rgba(0.08, 0.10, 0.14, 0.07 + 0.055 * phase)
        : Qt.rgba(1.0, 1.0, 1.0, 0.05 + 0.045 * phase)
    readonly property color boneSoftColor: isLight
        ? Qt.rgba(0.08, 0.10, 0.14, 0.04 + 0.035 * phase)
        : Qt.rgba(1.0, 1.0, 1.0, 0.028 + 0.03 * phase)

    // Background matching Launcher surface
    SmoothRectangle {
        anchors.fill: parent
        color: skeleton.c.BG
    }

    // Left AppSidebar skeleton (matches AppSidebar.qml: x: 16, y: 16..bottom-16, w: 228, r: 16)
    SmoothRectangle {
        id: sidebarSkeleton
        x: 16; y: 16
        width: 228
        height: Math.max(200, parent.height - 32)
        radius: 16
        color: skeleton.c.BG2
        border.color: skeleton.c.b08
        border.width: 1

        // Top account profile row (y: 12, h: 44, avatar: 34x34)
        Item {
            x: 8; y: 12
            width: parent.width - 16
            height: 44

            SmoothRectangle {
                x: 5; y: 5
                width: 34; height: 34; radius: 17
                color: skeleton.boneColor
            }
            SmoothRectangle {
                x: 49; y: 9
                width: 96; height: 11; radius: 5.5
                color: skeleton.boneColor
            }
            SmoothRectangle {
                x: 49; y: 25
                width: 68; height: 9; radius: 4.5
                color: skeleton.boneSoftColor
            }
        }

        // Divider under account (y: 64)
        SmoothRectangle {
            x: 8; y: 64
            width: parent.width - 16; height: 1
            color: skeleton.c.b06
        }

        // Navigation items (Projects & Settings) (y: 73, h: 40)
        Column {
            x: 8; y: 73
            width: parent.width - 16
            spacing: 5

            // Projects button placeholder (active)
            Item {
                width: parent.width; height: 40
                SmoothRectangle {
                    anchors.fill: parent; radius: 8
                    color: skeleton.c.a12 || skeleton.boneSoftColor
                    border.color: skeleton.c.a35 || "transparent"
                    border.width: 1
                }
                SmoothRectangle {
                    x: 13; y: 11
                    width: 18; height: 18; radius: 5
                    color: skeleton.boneColor
                }
                SmoothRectangle {
                    x: 41; y: 14
                    width: 84; height: 12; radius: 6
                    color: skeleton.boneColor
                }
                SmoothRectangle {
                    anchors.right: parent.right; anchors.rightMargin: 10
                    anchors.verticalCenter: parent.verticalCenter
                    width: 22; height: 18; radius: 9
                    color: skeleton.c.a20 || skeleton.boneSoftColor
                }
            }

            // Settings button placeholder
            Item {
                width: parent.width; height: 40
                SmoothRectangle {
                    x: 13; y: 11
                    width: 18; height: 18; radius: 5
                    color: skeleton.boneColor
                }
                SmoothRectangle {
                    x: 41; y: 14
                    width: 64; height: 12; radius: 6
                    color: skeleton.boneColor
                }
            }
        }

        // Divider before drives (y: 166)
        SmoothRectangle {
            x: 8; y: 166
            width: parent.width - 16; height: 1
            color: skeleton.c.b06
        }

        // Drives storage skeleton rows (y: 175, h: 44, spacing: 5)
        Column {
            x: 8; y: 175
            width: parent.width - 16
            spacing: 5

            Repeater {
                model: 3
                delegate: Item {
                    required property int index
                    width: parent.width; height: 44

                    // Disk icon placeholder
                    SmoothRectangle {
                        x: 13; y: 13
                        width: 18; height: 18; radius: 5
                        color: skeleton.boneColor
                    }

                    // Title & usage text row
                    SmoothRectangle {
                        x: 41; y: 11
                        width: index === 2 ? 80 : 72; height: 11; radius: 5.5
                        color: skeleton.boneColor
                    }
                    SmoothRectangle {
                        anchors.right: parent.right; anchors.rightMargin: 10
                        y: 12; width: 48; height: 9; radius: 4.5
                        color: skeleton.boneSoftColor
                    }

                    // Capacity bar track & fill
                    SmoothRectangle {
                        x: 41; y: 28
                        width: parent.width - 51; height: 3; radius: 1.5
                        color: skeleton.c.b08

                        SmoothRectangle {
                            width: parent.width * (index === 0 ? 0.76 : index === 1 ? 0.64 : 0.40)
                            height: parent.height; radius: parent.radius
                            color: skeleton.boneSoftColor
                        }
                    }
                }
            }
        }

        // Bottom sidebar collapse button placeholder (x: 8, bottomMargin: 8, 44x40)
        SmoothRectangle {
            x: 8
            anchors.bottom: parent.bottom; anchors.bottomMargin: 8
            width: 44; height: 40; radius: 8
            color: skeleton.boneSoftColor

            SmoothRectangle {
                anchors.centerIn: parent
                width: 18; height: 18; radius: 4
                color: skeleton.boneColor
            }
        }
    }

    // Right ProjectsPage skeleton (x: 244)
    Item {
        x: sidebarSkeleton.x + sidebarSkeleton.width
        y: 0
        width: Math.max(0, parent.width - x)
        height: parent.height

        // Heading "My Projects" (x: 16, y: 16..48)
        SmoothRectangle {
            x: 16; y: 21
            width: 140; height: 22; radius: 7
            color: skeleton.boneColor
        }

        // Filter pills bar (x: 16, y: 60, h: 32)
        Row {
            id: filterBarSkeleton
            x: 16; y: 60
            spacing: 8

            // Pill: Semua (active)
            SmoothRectangle {
                width: 92; height: 32; radius: 16
                color: skeleton.c.a14
                border.color: skeleton.c.a35
                border.width: 1

                Row {
                    anchors.centerIn: parent
                    spacing: 6
                    SmoothRectangle { width: 13; height: 13; radius: 3; color: skeleton.boneColor }
                    SmoothRectangle { width: 36; height: 11; radius: 5.5; color: skeleton.boneColor }
                    SmoothRectangle { width: 18; height: 18; radius: 9; color: skeleton.c.a25 || skeleton.boneSoftColor }
                }
            }

            // Pill: Favorit
            SmoothRectangle {
                width: 86; height: 32; radius: 16
                color: skeleton.c.BG2
                border.color: skeleton.c.b08
                border.width: 1

                Row {
                    anchors.centerIn: parent
                    spacing: 6
                    SmoothRectangle { width: 13; height: 13; radius: 3; color: skeleton.boneSoftColor }
                    SmoothRectangle { width: 34; height: 11; radius: 5.5; color: skeleton.boneSoftColor }
                    SmoothRectangle { width: 18; height: 18; radius: 9; color: skeleton.c.b08 }
                }
            }

            // Pill: Sampah
            SmoothRectangle {
                width: 92; height: 32; radius: 16
                color: skeleton.c.BG2
                border.color: skeleton.c.b08
                border.width: 1

                Row {
                    anchors.centerIn: parent
                    spacing: 6
                    SmoothRectangle { width: 13; height: 13; radius: 3; color: skeleton.boneSoftColor }
                    SmoothRectangle { width: 38; height: 11; radius: 5.5; color: skeleton.boneSoftColor }
                    SmoothRectangle { width: 18; height: 18; radius: 9; color: skeleton.c.b08 }
                }
            }
        }

        // Search compact pill button placeholder (aligned with filter bar)
        SmoothRectangle {
            anchors.right: parent.right; anchors.rightMargin: 16
            anchors.verticalCenter: filterBarSkeleton.verticalCenter
            width: 32; height: 32; radius: 16
            color: skeleton.c.BG2
            border.color: skeleton.c.b08
            border.width: 1

            SmoothRectangle {
                anchors.centerIn: parent
                width: 14; height: 14; radius: 7
                color: skeleton.boneSoftColor
            }
        }

        // Project cards list skeleton (y: 100, h: 66, spacing: 8)
        Column {
            x: 16; y: 100
            width: Math.max(0, parent.width - 32)
            height: Math.max(0, parent.height - 100 - 72)
            clip: true
            spacing: 8

            Repeater {
                model: Math.max(1, Math.min(7, Math.floor(parent.height / 74)))
                delegate: SmoothRectangle {
                    required property int index
                    width: parent.width
                    height: 66
                    radius: 10
                    color: skeleton.c.BG2
                    border.color: skeleton.c.b07
                    border.width: 1
                    opacity: Math.max(0.35, 1.0 - index * 0.11)

                    // Folder icon placeholder
                    SmoothRectangle {
                        x: 22; y: 17
                        width: 32; height: 32; radius: 8
                        color: skeleton.boneColor
                    }

                    // Project title & partition subtitle
                    SmoothRectangle {
                        x: 71; y: 17
                        width: Math.min(220, Math.max(90, parent.width * 0.28 - (index % 3) * 24))
                        height: 14; radius: 6
                        color: skeleton.boneColor
                    }
                    SmoothRectangle {
                        x: 71; y: 37
                        width: Math.min(130, Math.max(70, parent.width * 0.16))
                        height: 10; radius: 5
                        color: skeleton.boneSoftColor
                    }

                    // Disk icon, path & size on right
                    Item {
                        anchors.right: parent.right; anchors.rightMargin: 93
                        y: 17; width: 96; height: 33

                        Row {
                            spacing: 5
                            SmoothRectangle { width: 12; height: 12; radius: 3; color: skeleton.boneSoftColor }
                            SmoothRectangle { width: 72; height: 11; radius: 5; color: skeleton.boneSoftColor }
                        }
                        SmoothRectangle {
                            y: 17; width: 48; height: 10; radius: 5
                            color: skeleton.boneSoftColor
                        }
                    }

                    // More button placeholder
                    SmoothRectangle {
                        anchors.right: parent.right; anchors.rightMargin: 63
                        y: 19; width: 24; height: 28; radius: 6
                        color: skeleton.boneSoftColor
                    }

                    // Open arrow button placeholder
                    SmoothRectangle {
                        anchors.right: parent.right; anchors.rightMargin: 29
                        y: 19; width: 28; height: 28; radius: 8
                        color: skeleton.boneSoftColor
                    }
                }
            }
        }

        // Footer placeholders (h: 72, Import 95px + Create 121px)
        Item {
            anchors.bottom: parent.bottom
            width: parent.width; height: 72

            Row {
                anchors.right: parent.right; anchors.rightMargin: 16
                anchors.verticalCenter: parent.verticalCenter
                spacing: 12

                SmoothRectangle {
                    width: 95; height: 32; radius: 8
                    color: skeleton.boneSoftColor
                    border.color: skeleton.c.b08
                    border.width: 1
                }
                SmoothRectangle {
                    width: 121; height: 32; radius: 8
                    color: skeleton.c.a35 || skeleton.boneColor
                }
            }
        }
    }
}
