// SPDX-License-Identifier: MPL-2.0
import QtQuick
import QtQuick.Shapes

Item {
    id: root
    property string iconKey: ""
    property real iconSize: 16
    property bool isLight: false
    property bool hovered: false
    property bool down: false
    property bool enabled: true
    property string paletteName: "default"
    property color strokeColor: isLight ? "#24292f" : "#c9d1d9"
    property alias color: root.strokeColor
    property color accentColor: "#38bdf8"
    property color fillColor: "transparent"
    property color secondaryColor: "transparent"
    property int layoutMode: 2
    property bool collapsed: false
    property bool animationsEnabled: typeof rakPreferences !== "undefined" && rakPreferences && rakPreferences.settings ? rakPreferences.settings.animations : true

    implicitWidth: iconSize
    implicitHeight: iconSize
    width: iconSize
    height: iconSize

    // Render directly with GPU CurveRenderer at native display resolution
    layer.enabled: false

    // Unified Theme Color with subtle elevation dynamics (efek turun/naek)
    // - Normal: Theme palette accent color (effectiveAccent)
    // - Hover (naek): Sedikit lebih terang / kontras
    // - Down / Pressed (turun): Sedikit lebih gelap
    // - Disabled: Muted
    readonly property color finalColor: {
        if (!enabled) {
            return isLight ? "#9ca3af" : "#4b5563";
        }
        if (down) {
            return isLight ? Qt.darker(strokeColor, 1.25) : Qt.darker(strokeColor, 1.25);
        }
        if (hovered && strokeColor === accentColor) {
            return isLight ? Qt.darker(strokeColor, 1.20) : Qt.lighter(strokeColor, 1.25);
        }
        return strokeColor;
    }

    Item {
        id: scaler
        width: 24
        height: 24
        anchors.centerIn: parent
        scale: root.iconSize / 24.0
        transformOrigin: Item.Center
        rotation: (root.iconKey === "collapse") ? 180 : 0

        Loader {
            anchors.fill: parent
            sourceComponent: {
                switch (root.iconKey) {
            case "back_to_proj": return ic_0_back_to_proj;
            case "back": return ic_0_back_to_proj;
            case "set_project": return ic_1_set_project;
            case "settings": return ic_1_set_project;
            case "tree_mode": return ic_2_tree_mode;
            case "layout": return ic_2_tree_mode;
            case "terminal": return ic_3_terminal;
            case "android_tools": return ic_4_android_tools;
            case "android": return ic_4_android_tools;
            case "import": return ic_5_import;
            case "import_project": return ic_5_import;
            case "send": return ic_6_send;
            case "agentInbox": return ic_6_send;
            case "agent": return ic_6_send;
            case "sparkle": return ic_6_send;
            case "automation": return ic_7_automation;
            case "compare": return ic_8_compare;
            case "expand": return ic_9_expand;
            case "collapse": return ic_9_expand;
            case "start": return ic_10_start;
            case "play": return ic_10_start;
            case "add_session": return ic_11_add_session;
            case "plus": return ic_11_add_session;
            case "close": return ic_12_close;
            case "close_rounded": return ic_12_close;
            case "user": return ic_13_user;
            case "profile": return ic_13_user;
            case "project_list": return ic_14_project_list;
            case "project": return ic_14_project_list;
            case "rocket": return ic_14_project_list;
            case "info": return ic_15_info;
            case "package": return ic_16_package;
            case "packages": return ic_16_package;
            case "default_location": return ic_17_default_location;
            case "location": return ic_17_default_location;
            case "theme": return ic_18_theme;
            case "appearance": return ic_18_theme;
            case "language": return ic_19_language;
            case "key": return ic_20_key;
            case "activation": return ic_20_key;
            case "legal": return ic_21_legal;
            case "license": return ic_21_legal;
            case "drive": return ic_22_drive;
            case "project_disk": return ic_22_drive;
            case "disk": return ic_22_drive;
            case "storage": return ic_22_drive;
            case "sidebar": return ic_23_sidebar;
            case "sidebar_toggle": return ic_23_sidebar;
            case "project_folder": return ic_24_project_folder;
            case "folder": return ic_24_project_folder;
            case "project_card": return ic_24_project_folder;
            case "folder_project": return ic_24_project_folder;
            case "linux": return ic_25_linux;
            case "tux": return ic_25_linux;
            case "wsl": return ic_25_linux;
                default: return null;
                }
            }
        }
    }

    Component {
        id: ic_0_back_to_proj
            Shape {
                anchors.fill: parent
                preferredRendererType: Shape.CurveRenderer
                ShapePath {
                    fillColor: root.finalColor
                    strokeColor: "transparent"
                    PathSvg {
                        path: "M 9.0 21.0 L 6.5 21.0 Q 4.0 21.0 4.0 18.5 L 4.0 9.8 Q 4.0 7.3 6.4 6.2 L 10.2 4.1 Q 12.0 3.1 13.8 4.1 L 17.6 6.2 Q 20.0 7.3 20.0 9.8 L 20.0 18.5 Q 20.0 21.0 17.5 21.0 L 15.0 21.0 Q 14.5 21.0 14.5 20.0 L 14.5 15.2 Q 14.5 13.5 13.2 13.1 Q 12.0 12.7 10.8 13.1 Q 9.5 13.5 9.5 15.2 L 9.5 20.0 Q 9.5 21.0 9.0 21.0 Z"
                    }
                }
            }
    }

    Component {
        id: ic_1_set_project
            Shape {
                anchors.fill: parent
                preferredRendererType: Shape.CurveRenderer
                ShapePath {
                    fillColor: root.finalColor
                    strokeColor: "transparent"
                    fillRule: ShapePath.OddEvenFill
                    PathSvg {
                        path: "M 15.66 3.17 C 16.55 3.17 17.38 3.64 17.82 4.42 L 21.48 10.75 C 21.92 11.52 21.92 12.48 21.48 13.25 L 17.82 19.58 C 17.38 20.36 16.55 20.83 15.66 20.83 L 8.34 20.83 C 7.45 20.83 6.62 20.36 6.18 19.58 L 2.52 13.25 C 2.08 12.48 2.08 11.52 2.52 10.75 L 6.18 4.42 C 6.62 3.64 7.45 3.17 8.34 3.17 Z M 12.00 8.00 C 14.21 8.00 16.00 9.79 16.00 12.00 C 16.00 14.21 14.21 16.00 12.00 16.00 C 9.79 16.00 8.00 14.21 8.00 12.00 C 8.00 9.79 9.79 8.00 12.00 8.00 Z"
                    }
                }
            }
    }

    Component {
        id: ic_2_tree_mode
            Item {
                anchors.fill: parent

                // State 0 & 1: Single Panel Mode (50% when hidden + bold 100% slash)
                Shape {
                    anchors.fill: parent
                    visible: root.layoutMode === 1 || root.layoutMode === 0
                    opacity: root.layoutMode === 0 ? 0.5 : 1.0
                    preferredRendererType: Shape.CurveRenderer
                    ShapePath {
                        fillColor: root.finalColor
                        strokeColor: "transparent"
                        PathSvg {
                            path: "M 6.50 3.50 L 17.50 3.50 C 19.16 3.50 20.50 4.84 20.50 6.50 L 20.50 17.50 C 20.50 19.16 19.16 20.50 17.50 20.50 L 6.50 20.50 C 4.84 20.50 3.50 19.16 3.50 17.50 L 3.50 6.50 C 3.50 4.84 4.84 3.50 6.50 3.50 Z"
                        }
                    }
                }

                // State 0: Bold 100% Slash Overlay for Hide Mode
                Shape {
                    anchors.fill: parent
                    visible: root.layoutMode === 0
                    opacity: 1.0
                    preferredRendererType: Shape.CurveRenderer
                    ShapePath {
                        fillColor: "transparent"
                        strokeColor: root.finalColor
                        strokeWidth: 2.8
                        capStyle: ShapePath.RoundCap
                        PathSvg {
                            path: "M 4.00 20.00 L 20.00 4.00"
                        }
                    }
                }

                // State 2 & 4: Vertical Left Pane (100% normal, 50% when swapped)
                Shape {
                    anchors.fill: parent
                    visible: root.layoutMode === 2 || root.layoutMode === 4 || (root.layoutMode !== 0 && root.layoutMode !== 1 && root.layoutMode !== 3)
                    opacity: root.layoutMode === 4 ? 0.5 : 1.0
                    preferredRendererType: Shape.CurveRenderer
                    ShapePath {
                        fillColor: root.finalColor
                        strokeColor: "transparent"
                        PathSvg {
                            path: "M 6.50 3.50 L 8.00 3.50 C 9.66 3.50 11.00 4.84 11.00 6.50 L 11.00 17.50 C 11.00 19.16 9.66 20.50 8.00 20.50 L 6.50 20.50 C 4.84 20.50 3.50 19.16 3.50 17.50 L 3.50 6.50 C 3.50 4.84 4.84 3.50 6.50 3.50 Z"
                        }
                    }
                }

                // State 2 & 4: Vertical Right Pane (50% normal, 100% when swapped)
                Shape {
                    anchors.fill: parent
                    visible: root.layoutMode === 2 || root.layoutMode === 4 || (root.layoutMode !== 0 && root.layoutMode !== 1 && root.layoutMode !== 3)
                    opacity: root.layoutMode === 4 ? 1.0 : 0.5
                    preferredRendererType: Shape.CurveRenderer
                    ShapePath {
                        fillColor: root.finalColor
                        strokeColor: "transparent"
                        PathSvg {
                            path: "M 16.00 3.50 L 17.50 3.50 C 19.16 3.50 20.50 4.84 20.50 6.50 L 20.50 17.50 C 20.50 19.16 19.16 20.50 17.50 20.50 L 16.00 20.50 C 14.34 20.50 13.00 19.16 13.00 17.50 L 13.00 6.50 C 13.00 4.84 14.34 3.50 16.00 3.50 Z"
                        }
                    }
                }

                // State 3: Horizontal Top & Bottom Stacked Panes (bottom 50% pembeda)
                Shape {
                    anchors.fill: parent
                    visible: root.layoutMode === 3
                    opacity: 1.0
                    preferredRendererType: Shape.CurveRenderer
                    ShapePath {
                        fillColor: root.finalColor
                        strokeColor: "transparent"
                        PathSvg {
                            path: "M 6.50 3.50 L 17.50 3.50 C 19.16 3.50 20.50 4.84 20.50 6.50 L 20.50 8.00 C 20.50 9.66 19.16 11.00 17.50 11.00 L 6.50 11.00 C 4.84 11.00 3.50 9.66 3.50 8.00 L 3.50 6.50 C 3.50 4.84 4.84 3.50 6.50 3.50 Z"
                        }
                    }
                }
                Shape {
                    anchors.fill: parent
                    visible: root.layoutMode === 3
                    opacity: 0.5
                    preferredRendererType: Shape.CurveRenderer
                    ShapePath {
                        fillColor: root.finalColor
                        strokeColor: "transparent"
                        PathSvg {
                            path: "M 6.50 13.00 L 17.50 13.00 C 19.16 13.00 20.50 14.34 20.50 16.00 L 20.50 17.50 C 20.50 19.16 19.16 20.50 17.50 20.50 L 6.50 20.50 C 4.84 20.50 3.50 19.16 3.50 17.50 L 3.50 16.00 C 3.50 14.34 4.84 13.00 6.50 13.00 Z"
                        }
                    }
                }
            }
    }

    Component {
        id: ic_3_terminal
            Shape {
                anchors.fill: parent
                preferredRendererType: Shape.CurveRenderer
                ShapePath {
                    fillColor: root.finalColor
                    strokeColor: "transparent"
                    fillRule: ShapePath.OddEvenFill
                    PathSvg {
                        path: "M 6.70 3.50 L 17.30 3.50 C 19.62 3.50 21.50 5.38 21.50 7.70 L 21.50 16.30 C 21.50 18.62 19.62 20.50 17.30 20.50 L 6.70 20.50 C 4.38 20.50 2.50 18.62 2.50 16.30 L 2.50 7.70 C 2.50 5.38 4.38 3.50 6.70 3.50 Z M 7.68 6.52 L 12.28 11.12 C 12.77 11.61 12.77 12.39 12.28 12.88 L 7.68 17.48 C 7.19 17.97 6.41 17.97 5.92 17.48 C 5.43 16.99 5.43 16.21 5.92 15.72 L 9.64 12.00 L 5.92 8.28 C 5.43 7.79 5.43 7.01 5.92 6.52 C 6.41 6.03 7.19 6.03 7.68 6.52 Z M 14.40 14.80 L 17.20 14.80 C 17.97 14.80 18.60 15.43 18.60 16.20 C 18.60 16.97 17.97 17.60 17.20 17.60 L 14.40 17.60 C 13.63 17.60 13.00 16.97 13.00 16.20 C 13.00 15.43 13.63 14.80 14.40 14.80 Z"
                    }
                }
            }
    }

    Component {
        id: ic_4_android_tools
            Shape {
                anchors.fill: parent
                preferredRendererType: Shape.CurveRenderer
                ShapePath {
                    fillColor: root.finalColor
                    strokeColor: "transparent"
                    fillRule: ShapePath.OddEvenFill
                    PathSvg {
                        path: "M 6.70 7.00 L 7.00 7.00 L 7.00 3.60 C 7.00 2.50 9.00 2.50 9.00 3.60 L 9.00 7.00 L 15.00 7.00 L 15.00 3.60 C 15.00 2.50 17.00 2.50 17.00 3.60 L 17.00 7.00 L 17.30 7.00 C 19.62 7.00 21.50 8.88 21.50 11.20 L 21.50 16.30 C 21.50 18.62 19.62 20.50 17.30 20.50 L 6.70 20.50 C 4.38 20.50 2.50 18.62 2.50 16.30 L 2.50 11.20 C 2.50 8.88 4.38 7.00 6.70 7.00 Z M 8.00 11.90 C 9.02 11.90 9.85 12.73 9.85 13.75 C 9.85 14.77 9.02 15.60 8.00 15.60 C 6.98 15.60 6.15 14.77 6.15 13.75 C 6.15 12.73 6.98 11.90 8.00 11.90 Z M 16.00 11.90 C 17.02 11.90 17.85 12.73 17.85 13.75 C 17.85 14.77 17.02 15.60 16.00 15.60 C 14.98 15.60 14.15 14.77 14.15 13.75 C 14.15 12.73 14.98 11.90 16.00 11.90 Z"
                    }
                }
            }
    }

    Component {
        id: ic_5_import
            Shape {
                anchors.fill: parent
                preferredRendererType: Shape.CurveRenderer
                ShapePath {
                    fillColor: root.finalColor
                    strokeColor: "transparent"
                    PathSvg {
                        path: "M 12.00 3.00 L 17.00 3.00 C 19.32 3.00 21.20 4.88 21.20 7.20 L 21.20 16.80 C 21.20 19.12 19.32 21.00 17.00 21.00 L 12.00 21.00 C 9.68 21.00 7.80 19.12 7.80 16.80 L 7.80 14.20 L 12.00 14.20 L 12.00 16.80 C 12.00 17.40 12.65 17.75 13.12 17.35 L 17.35 12.55 C 17.72 12.25 17.72 11.75 17.35 11.45 L 13.12 6.65 C 12.65 6.25 12.00 6.60 12.00 7.20 L 12.00 9.80 L 7.80 9.80 L 7.80 7.20 C 7.80 4.88 9.68 3.00 12.00 3.00 Z M 7.80 9.80 L 4.20 9.80 C 2.98 9.80 2.00 10.78 2.00 12.00 C 2.00 13.22 2.98 14.20 4.20 14.20 L 7.80 14.20 Z"
                    }
                }
            }
    }

    Component {
        id: ic_6_send
            Item {
                anchors.fill: parent
    
                // Primary Sparkle Star (100% Solid)
                Shape {
                    anchors.fill: parent
                    preferredRendererType: Shape.CurveRenderer
                    ShapePath {
                        fillColor: root.finalColor
                        strokeColor: "transparent"
                        PathSvg {
                            path: "M 8.65 3.85 C 9.46 2.50 10.54 2.50 11.35 3.85 C 11.35 7.15 12.85 8.65 16.15 8.65 C 17.50 9.46 17.50 10.54 16.15 11.35 C 12.85 11.35 11.35 12.85 11.35 16.15 C 10.54 17.50 9.46 17.50 8.65 16.15 C 8.65 12.85 7.15 11.35 3.85 11.35 C 2.50 10.54 2.50 9.46 3.85 8.65 C 7.15 8.65 8.65 7.15 8.65 3.85 Z"
                        }
                    }
                }
    
                // Satellite Sparkle Star (50% Opacity)
                Shape {
                    anchors.fill: parent
                    opacity: 0.50
                    preferredRendererType: Shape.CurveRenderer
                    ShapePath {
                        fillColor: root.finalColor
                        strokeColor: "transparent"
                        PathSvg {
                            path: "M 17.20 14.80 C 17.68 14.00 18.32 14.00 18.80 14.80 C 18.80 16.36 19.44 17.00 21.00 17.00 C 21.80 17.48 21.80 18.12 21.00 18.60 C 19.44 18.60 18.80 19.24 18.80 20.80 C 18.32 21.60 17.68 21.60 17.20 20.80 C 17.20 19.24 16.56 18.60 15.00 18.60 C 14.20 18.12 14.20 17.48 15.00 17.00 C 16.56 17.00 17.20 16.36 17.20 14.80 Z"
                        }
                    }
                }
            }
    }

    Component {
        id: ic_7_automation
            Shape {
                anchors.fill: parent
                preferredRendererType: Shape.CurveRenderer
                ShapePath {
                    fillColor: root.finalColor
                    strokeColor: "transparent"
                    PathSvg {
                        path: "M 11.5 21 C 11.1 21 10.9 20.6 11 20.3 L 12.4 13.5 H 7.8 C 7.2 13.5 6.8 12.8 7.2 12.3 L 13.2 3.4 C 13.5 2.9 14.3 3.2 14.2 3.8 L 12.8 10.5 H 17.5 C 18.1 10.5 18.4 11.3 18 11.7 L 12.2 20.7 C 12 20.9 11.8 21 11.5 21 Z"
                    }
                }
            }
    }

    Component {
        id: ic_8_compare
            Item {
                anchors.fill: parent
    
                // Left Pane (100% Solid)
                Shape {
                    anchors.fill: parent
                    preferredRendererType: Shape.CurveRenderer
                    ShapePath {
                        fillColor: root.finalColor
                        strokeColor: "transparent"
                        fillRule: ShapePath.OddEvenFill
                        PathSvg {
                            path: "M 7.50 3.50 L 10.40 3.50 C 10.84 3.50 11.20 3.86 11.20 4.30 L 11.20 19.70 C 11.20 20.14 10.84 20.50 10.40 20.50 L 7.50 20.50 C 5.29 20.50 3.50 18.71 3.50 16.50 L 3.50 7.50 C 3.50 5.29 5.29 3.50 7.50 3.50 Z M 6.80 7.50 L 9.30 11.10 C 9.70 11.60 9.70 12.40 9.30 12.90 L 6.80 16.50 C 6.35 17.00 5.60 17.00 5.15 16.50 C 4.70 16.05 4.70 15.30 5.15 14.85 L 7.20 12.00 L 5.15 9.15 C 4.70 8.70 4.70 7.95 5.15 7.50 C 5.60 7.00 6.35 7.00 6.80 7.50 Z"
                        }
                    }
                }
    
                // Right Pane (50% Opacity)
                Shape {
                    anchors.fill: parent
                    opacity: 0.50
                    preferredRendererType: Shape.CurveRenderer
                    ShapePath {
                        fillColor: root.finalColor
                        strokeColor: "transparent"
                        fillRule: ShapePath.OddEvenFill
                        PathSvg {
                            path: "M 13.60 3.50 L 16.50 3.50 C 18.71 3.50 20.50 5.29 20.50 7.50 L 20.50 16.50 C 20.50 18.71 18.71 20.50 16.50 20.50 L 13.60 20.50 C 13.16 20.50 12.80 20.14 12.80 19.70 L 12.80 4.30 C 12.80 3.86 13.16 3.50 13.60 3.50 Z M 17.20 7.50 L 14.70 11.10 C 14.30 11.60 14.30 12.40 14.70 12.90 L 17.20 16.50 C 17.65 17.00 18.40 17.00 18.85 16.50 C 19.30 16.05 19.30 15.30 18.85 14.85 L 16.80 12.00 L 18.85 9.15 C 19.30 8.70 19.30 7.95 18.85 7.50 C 18.40 7.00 17.65 7.00 17.20 7.50 Z"
                        }
                    }
                }
            }
    }

    Component {
        id: ic_9_expand
            Shape {
                anchors.fill: parent
                preferredRendererType: Shape.CurveRenderer
                ShapePath {
                    strokeColor: root.finalColor
                    strokeWidth: 2.2
                    capStyle: ShapePath.RoundCap
                    joinStyle: ShapePath.RoundJoin
                    PathSvg {
                        path: "M 7 6 L 12.5 12 L 7 18 M 12.5 6 L 18 12 L 12.5 18"
                    }
                }
            }
    }

    Component {
        id: ic_10_start
            Shape {
                anchors.fill: parent
                preferredRendererType: Shape.CurveRenderer
                ShapePath {
                    fillColor: root.finalColor
                    strokeColor: "transparent"
                    PathSvg {
                        path: "M 8 6.82 V 17.18 C 8 17.97 8.87 18.45 9.54 18.02 L 17.68 12.84 C 18.3 12.45 18.3 11.55 17.68 11.16 L 9.54 5.98 C 8.87 5.55 8 6.03 8 6.82 Z"
                    }
                }
            }
    }

    Component {
        id: ic_11_add_session
            Shape {
                anchors.fill: parent
                preferredRendererType: Shape.CurveRenderer
                ShapePath {
                    strokeColor: root.finalColor
                    strokeWidth: 2.4
                    capStyle: ShapePath.RoundCap
                    PathSvg { path: "M 12 4.5 V 19.5 M 4.5 12 H 19.5" }
                }
            }
    }

    Component {
        id: ic_12_close
            Shape {
                anchors.fill: parent
                preferredRendererType: Shape.CurveRenderer
                ShapePath {
                    strokeColor: root.finalColor
                    strokeWidth: 2.3
                    capStyle: ShapePath.RoundCap
                    PathSvg { path: "M 6 6 L 18 18 M 18 6 L 6 18" }
                }
            }
    }

    Component {
        id: ic_13_user
            Shape {
                anchors.fill: parent
                preferredRendererType: Shape.CurveRenderer
                ShapePath {
                    strokeColor: root.finalColor
                    strokeWidth: 2.0
                    fillColor: "transparent"
                    capStyle: ShapePath.RoundCap
                    PathSvg { path: "M 12 4 A 4 4 0 1 0 12 12 A 4 4 0 1 0 12 4 Z M 4 20 C 4 16 7.5 14 12 14 C 16.5 14 20 16 20 20" }
                }
            }
    }

    Component {
        id: ic_14_project_list
            Shape {
                anchors.fill: parent
                preferredRendererType: Shape.CurveRenderer
                ShapePath {
                    fillColor: root.finalColor
                    strokeColor: "transparent"
                    fillRule: ShapePath.OddEvenFill
                    PathSvg {
                        path: "M 22.604 1.200 C 22.604 4.350 21.650 8.030 19.265 11.215 C 17.820 13.140 16.320 14.420 14.749 15.390 C 14.749 18.120 13.533 19.500 12.000 21.033 C 11.392 21.641 10.757 22.440 10.036 22.800 C 9.408 21.543 10.020 19.800 9.520 17.460 C 9.060 17.610 8.650 17.695 8.269 17.695 C 7.748 17.173 6.672 16.414 6.502 15.731 C 6.450 15.520 6.530 15.420 6.610 15.230 C 6.380 14.850 6.180 14.553 5.913 14.553 C 4.957 14.553 4.006 14.567 3.164 14.356 C 2.528 14.197 1.973 14.448 1.396 14.160 C 1.757 13.439 2.555 12.805 3.164 12.196 C 3.995 11.365 4.908 10.146 5.913 9.644 C 6.429 9.385 7.257 9.447 7.876 9.447 L 8.714 9.420 C 10.620 6.720 13.180 4.340 15.927 2.967 C 17.920 1.970 20.150 1.480 22.604 1.200 Z M 16.929 9.015 C 16.929 10.013 16.120 10.822 15.122 10.822 C 14.124 10.822 13.316 10.013 13.316 9.015 C 13.316 8.018 14.124 7.209 15.122 7.209 C 16.120 7.209 16.929 8.018 16.929 9.015 Z M 4.735 17.302 C 6.224 17.302 6.698 18.287 6.698 19.658 C 6.698 20.907 4.991 21.425 3.949 21.425 C 3.698 21.551 2.542 22.114 2.378 21.622 C 2.263 21.277 2.494 21.550 2.575 21.229 C 2.678 20.815 2.771 20.245 2.771 19.855 C 3.127 19.141 3.138 18.309 3.753 17.695 C 3.991 17.456 4.465 17.571 4.735 17.302 Z"
                    }
                }
            }
    }

    Component {
        id: ic_15_info
            Shape {
                anchors.fill: parent
                preferredRendererType: Shape.CurveRenderer
                ShapePath {
                    fillColor: root.finalColor
                    strokeColor: "transparent"
                    fillRule: ShapePath.OddEvenFill
                    PathSvg {
                        path: "M 12 2.5 A 9.5 9.5 0 1 0 12 21.5 A 9.5 9.5 0 1 0 12 2.5 Z M 12 5.85 A 1.35 1.35 0 1 0 12 8.55 A 1.35 1.35 0 1 0 12 5.85 Z M 10.7 11.8 A 1.3 1.3 0 0 1 13.3 11.8 L 13.3 15.7 A 1.3 1.3 0 0 1 10.7 15.7 Z"
                    }
                }
            }
    }

    Component {
        id: ic_16_package
            Shape {
                anchors.fill: parent
                preferredRendererType: Shape.CurveRenderer
                ShapePath {
                    fillColor: root.finalColor
                    strokeColor: "transparent"
                    fillRule: ShapePath.OddEvenFill
                    PathSvg {
                        path: "M 9.300 3.959 C 11.100 2.920 12.900 2.920 14.700 3.959 L 17.614 5.641 C 19.414 6.680 20.314 8.239 20.314 10.318 L 20.314 13.682 C 20.314 15.761 19.414 17.320 17.614 18.359 L 14.700 20.041 C 12.900 21.080 11.100 21.080 9.300 20.041 L 6.386 18.359 C 4.586 17.320 3.686 15.761 3.686 13.682 L 3.686 10.318 C 3.686 8.239 4.586 6.680 6.386 5.641 L 9.300 3.959 Z M 12.000 10.961 L 17.266 7.921 A 0.900 0.900 0 0 1 18.166 9.479 L 12.900 12.520 L 12.900 18.600 A 0.900 0.900 0 0 1 11.100 18.600 L 11.100 12.520 L 5.834 9.479 A 0.900 0.900 0 0 1 6.734 7.921 Z"
                    }
                }
            }
    }

    Component {
        id: ic_17_default_location
            Shape {
                anchors.fill: parent
                preferredRendererType: Shape.CurveRenderer
                ShapePath {
                    fillColor: root.finalColor
                    strokeColor: "transparent"
                    fillRule: ShapePath.OddEvenFill
                    PathSvg {
                        path: "M 6.0 4.5 L 9.2 4.5 A 1.8 1.8 0 0 1 10.7 5.4 L 11.9 7.0 A 1.8 1.8 0 0 0 13.4 7.8 L 18.5 7.8 A 2.5 2.5 0 0 1 21.0 10.3 L 21.0 17.0 A 2.5 2.5 0 0 1 18.5 19.5 L 5.5 19.5 A 2.5 2.5 0 0 1 3.0 17.0 L 3.0 7.5 A 2.5 2.5 0 0 1 5.5 5.0 L 6.0 4.5 Z M 12.0 16.8 L 9.6 13.2 A 2.8 2.8 0 1 1 14.4 13.2 Z M 12.0 10.6 A 1.2 1.2 0 1 0 12.0 13.0 A 1.2 1.2 0 1 0 12.0 10.6 Z"
                    }
                }
            }
    }

    Component {
        id: ic_18_theme
            Shape {
                anchors.fill: parent
                preferredRendererType: Shape.CurveRenderer
                ShapePath {
                    fillColor: root.finalColor
                    strokeColor: "transparent"
                    fillRule: ShapePath.OddEvenFill
                    PathSvg {
                        path: "M 12.0 2.8 C 17.6 2.8 21.5 6.8 21.5 12.2 C 21.5 15.5 19.5 17.5 18.0 18.0 C 16.5 18.5 16.0 19.8 16.2 20.6 C 16.4 21.4 15.4 22.0 14.0 22.0 C 7.5 22.0 2.5 17.4 2.5 12.0 C 2.5 6.8 6.8 2.8 12.0 2.8 Z M 15.2 13.2 A 1.8 1.8 0 1 0 15.2 16.8 A 1.8 1.8 0 1 0 15.2 13.2 Z M 7.8 6.4 A 1.4 1.4 0 1 0 7.8 9.2 A 1.4 1.4 0 1 0 7.8 6.4 Z M 13.2 5.4 A 1.4 1.4 0 1 0 13.2 8.2 A 1.4 1.4 0 1 0 13.2 5.4 Z M 6.8 11.8 A 1.4 1.4 0 1 0 6.8 14.6 A 1.4 1.4 0 1 0 6.8 11.8 Z"
                    }
                }
            }
    }

    Component {
        id: ic_19_language
            Shape {
                anchors.fill: parent
                preferredRendererType: Shape.CurveRenderer
                ShapePath {
                    fillColor: root.finalColor
                    strokeColor: "transparent"
                    fillRule: ShapePath.OddEvenFill
                    PathSvg {
                        path: "M 12 2.5 A 9.5 9.5 0 1 0 12 21.5 A 9.5 9.5 0 1 0 12 2.5 Z M 2.5 11.2 L 21.5 11.2 L 21.5 12.8 L 2.5 12.8 Z M 12 2.8 A 4.8 9.2 0 0 0 12 21.2 A 6.2 9.2 0 0 1 12 2.8 Z M 12 2.8 A 4.8 9.2 0 0 1 12 21.2 A 6.2 9.2 0 0 0 12 2.8 Z"
                    }
                }
            }
    }

    Component {
        id: ic_20_key
            Shape {
                anchors.fill: parent
                preferredRendererType: Shape.CurveRenderer
                ShapePath {
                    fillColor: root.finalColor
                    strokeColor: "transparent"
                    fillRule: ShapePath.OddEvenFill
                    PathSvg {
                        path: "M 16.38 3.22 A 4.4 4.4 0 1 0 16.38 12.02 A 4.4 4.4 0 1 0 16.38 3.22 Z M 16.38 5.62 A 2.0 2.0 0 1 0 16.38 9.62 A 2.0 2.0 0 1 0 16.38 5.62 Z M 14.62 11.08 L 7.05 18.65 L 5.64 18.36 L 5.35 16.95 L 5.64 16.67 L 3.80 14.83 L 4.79 13.84 L 6.63 15.68 L 7.47 14.83 L 5.07 12.42 L 6.20 11.29 L 8.61 13.70 L 12.92 9.38 Z"
                    }
                }
            }
    }

    Component {
        id: ic_21_legal
            Shape {
                anchors.fill: parent
                preferredRendererType: Shape.CurveRenderer
                ShapePath {
                    fillColor: root.finalColor
                    strokeColor: "transparent"
                    fillRule: ShapePath.OddEvenFill
                    PathSvg {
                        path: "M 6.5 3.0 L 15.0 3.0 L 20.0 8.0 L 20.0 18.5 A 2.5 2.5 0 0 1 17.5 21.0 L 6.5 21.0 A 2.5 2.5 0 0 1 4.0 18.5 L 4.0 5.5 A 2.5 2.5 0 0 1 6.5 3.0 Z M 15.0 3.0 L 15.0 7.2 A 0.8 0.8 0 0 0 15.8 8.0 L 20.0 8.0 Z M 7.8 10.7 L 16.2 10.7 A 0.8 0.8 0 0 1 16.2 12.3 L 7.8 12.3 A 0.8 0.8 0 0 1 7.8 10.7 Z M 7.8 13.7 L 16.2 13.7 A 0.8 0.8 0 0 1 16.2 15.3 L 7.8 15.3 A 0.8 0.8 0 0 1 7.8 13.7 Z M 7.8 16.7 L 12.2 16.7 A 0.8 0.8 0 0 1 12.2 18.3 L 7.8 18.3 A 0.8 0.8 0 0 1 7.8 16.7 Z"
                    }
                }
            }
    }

    Component {
        id: ic_22_drive
            Item {
                anchors.fill: parent
    
                // Top Body Lid (50% Opacity)
                Shape {
                    anchors.fill: parent
                    opacity: 0.50
                    preferredRendererType: Shape.CurveRenderer
                    ShapePath {
                        fillColor: root.finalColor
                        strokeColor: "transparent"
                        PathSvg {
                            path: "M 8.300 3.200 L 15.700 3.200 C 17.300 3.200 18.400 3.800 19.000 5.000 L 21.700 17.200 L 2.300 17.200 L 5.000 5.000 C 5.600 3.800 6.700 3.200 8.300 3.200 Z"
                        }
                    }
                }
    
                // Front Face Bezel (100% Solid Opacity with 4 Negative Space Indicator Dots)
                Shape {
                    anchors.fill: parent
                    preferredRendererType: Shape.CurveRenderer
                    ShapePath {
                        fillColor: root.finalColor
                        strokeColor: "transparent"
                        fillRule: ShapePath.OddEvenFill
                        PathSvg {
                            path: "M 2.300 17.200 C 2.300 15.101 4.001 13.400 6.100 13.400 L 17.900 13.400 C 19.999 13.400 21.700 15.101 21.700 17.200 C 21.700 19.299 19.999 21.000 17.900 21.000 L 6.100 21.000 C 4.001 21.000 2.300 19.299 2.300 17.200 M 10.550 16.050 C 10.550 15.691 10.841 15.400 11.200 15.400 C 11.559 15.400 11.850 15.691 11.850 16.050 L 11.850 18.350 C 11.850 18.709 11.559 19.000 11.200 19.000 C 10.841 19.000 10.550 18.709 10.550 18.350 L 10.550 16.050 M 12.850 16.050 C 12.850 15.691 13.141 15.400 13.500 15.400 C 13.859 15.400 14.150 15.691 14.150 16.050 L 14.150 18.350 C 14.150 18.709 13.859 19.000 13.500 19.000 C 13.141 19.000 12.850 18.709 12.850 18.350 L 12.850 16.050 M 15.150 16.050 C 15.150 15.691 15.441 15.400 15.800 15.400 C 16.159 15.400 16.450 15.691 16.450 16.050 L 16.450 18.350 C 16.450 18.709 16.159 19.000 15.800 19.000 C 15.441 19.000 15.150 18.709 15.150 18.350 L 15.150 16.050 M 17.450 16.050 C 17.450 15.691 17.741 15.400 18.100 15.400 C 18.459 15.400 18.750 15.691 18.750 16.050 L 18.750 18.350 C 18.750 18.709 18.459 19.000 18.100 19.000 C 17.741 19.000 17.450 18.709 17.450 18.350 L 17.450 16.050 Z"
                        }
                    }
                }
            }
    }

    Component {
        id: ic_23_sidebar
            Item {
                anchors.fill: parent
    
                property real leftBarWidth: root.collapsed ? 2.8 : 6.4
                property real leftBarRadius: root.collapsed ? 1.4 : 2.4
                readonly property real rightPanelsX: 3.5 + leftBarWidth + 2.0
                readonly property real rightPanelsWidth: 20.5 - rightPanelsX
    
                Behavior on leftBarWidth {
                    enabled: root.animationsEnabled
                    NumberAnimation { duration: root.animationsEnabled ? 200 : 0; easing.type: Easing.InOutCubic }
                }
    
                // Left Sidebar Panel (50% Opacity)
                Rectangle {
                    x: 3.5
                    y: 3.5
                    width: parent.leftBarWidth
                    height: 17.0
                    radius: parent.leftBarRadius
                    color: root.finalColor
                    opacity: 0.50
                }
    
                // Right Top Panel (100% Solid)
                Rectangle {
                    x: parent.rightPanelsX
                    y: 3.5
                    width: parent.rightPanelsWidth
                    height: 5.8
                    radius: 2.4
                    color: root.finalColor
                }
    
                // Right Bottom Workspace Panel (100% Solid)
                Rectangle {
                    x: parent.rightPanelsX
                    y: 11.3
                    width: parent.rightPanelsWidth
                    height: 9.2
                    radius: 2.4
                    color: root.finalColor
                }
            }
    }

    Component {
        id: ic_24_project_folder
            Item {
                anchors.fill: parent
    
                // Back Tab (50% Opacity)
                Shape {
                    anchors.fill: parent
                    preferredRendererType: Shape.CurveRenderer
                    opacity: 0.50
                    ShapePath {
                        fillColor: root.finalColor
                        strokeColor: "transparent"
                        PathSvg {
                            path: "M 5.8 4.2 L 9.6 4.2 C 10.6 4.2 11.2 4.6 11.8 5.3 L 12.8 6.5 C 13.3 7.1 13.9 7.5 14.8 7.5 L 18.2 7.5 C 19.8 7.5 21.0 8.7 21.0 10.3 L 21.0 14.0 L 3.0 14.0 L 3.0 7.0 C 3.0 5.4 4.2 4.2 5.8 4.2 Z"
                        }
                    }
                }
    
                // Front Pocket (100% Solid) with Cutout Pill
                Shape {
                    anchors.fill: parent
                    preferredRendererType: Shape.CurveRenderer
                    ShapePath {
                        fillColor: root.finalColor
                        strokeColor: "transparent"
                        fillRule: ShapePath.OddEvenFill
                        PathSvg {
                            path: "M 5.8 8.5 L 18.2 8.5 C 19.8 8.5 21.0 9.7 21.0 11.3 L 21.0 16.7 C 21.0 18.3 19.8 19.5 18.2 19.5 L 5.8 19.5 C 4.2 19.5 3.0 18.3 3.0 16.7 L 3.0 11.3 C 3.0 9.7 4.2 8.5 5.8 8.5 Z " +
                                  "M 9.2 14.0 L 14.8 14.0 A 1.2 1.2 0 0 1 14.8 16.4 L 9.2 16.4 A 1.2 1.2 0 0 1 9.2 14.0 Z"
                        }
                    }
                }
            }
    }

    Component {
        id: ic_25_linux
            Shape {
                anchors.fill: parent
                preferredRendererType: Shape.CurveRenderer
                ShapePath {
                    fillColor: root.finalColor
                    strokeColor: "transparent"
                    PathSvg {
                        path: "M12.504 0c-.155 0-.315.008-.48.021-4.226.333-3.105 4.807-3.17 6.298-.076 1.092-.3 1.953-1.05 3.02-.885 1.051-2.127 2.75-2.716 4.521-.278.832-.41 1.684-.287 2.489a.424.424 0 00-.11.135c-.26.268-.45.6-.663.839-.199.199-.485.267-.797.4-.313.136-.658.269-.864.68-.09.189-.136.394-.132.602 0 .199.027.4.055.536.058.399.116.728.04.97-.249.68-.28 1.145-.106 1.484.174.334.535.47.94.601.81.2 1.91.135 2.774.6.926.466 1.866.67 2.616.47.526-.116.97-.464 1.208-.946.587-.003 1.23-.269 2.26-.334.699-.058 1.574.267 2.577.2.025.134.063.198.114.333l.003.003c.391.778 1.113 1.132 1.884 1.071.771-.06 1.592-.536 2.257-1.306.631-.765 1.683-1.084 2.378-1.503.348-.199.629-.469.649-.853.023-.4-.2-.811-.714-1.376v-.097l-.003-.003c-.17-.2-.25-.535-.338-.926-.085-.401-.182-.786-.492-1.046h-.003c-.059-.054-.123-.067-.188-.135a.357.357 0 00-.19-.064c.431-1.278.264-2.55-.173-3.694-.533-1.41-1.465-2.638-2.175-3.483-.796-1.005-1.576-1.957-1.56-3.368.026-2.152.236-6.133-3.544-6.139zm.529 3.405h.013c.213 0 .396.062.584.198.19.135.33.332.438.533.105.259.158.459.166.724 0-.02.006-.04.006-.06v.105a.086.086 0 01-.004-.021l-.004-.024a1.807 1.807 0 01-.15.706.953.953 0 01-.213.335.71.71 0 00-.088-.042c-.104-.045-.198-.064-.284-.133a1.312 1.312 0 00-.22-.066c.05-.06.146-.133.183-.198.053-.128.082-.264.088-.402v-.02a1.21 1.21 0 00-.061-.4c-.045-.134-.101-.2-.183-.333-.084-.066-.167-.132-.267-.132h-.016c-.093 0-.176.03-.262.132a.8.8 0 00-.205.334 1.18 1.18 0 00-.09.4v.019c.002.089.008.179.02.267-.193-.067-.438-.135-.607-.202a1.635 1.635 0 01-.018-.2v-.02a1.772 1.772 0 01.15-.768c.082-.22.232-.406.43-.533a.985.985 0 01.594-.2zm-2.962.059h.036c.142 0 .27.048.399.135.146.129.264.288.344.465.09.199.14.4.153.667v.004c.007.134.006.2-.002.266v.08c-.03.007-.056.018-.083.024-.152.055-.274.135-.393.2.012-.09.013-.18.003-.267v-.015c-.012-.133-.04-.2-.082-.333a.613.613 0 00-.166-.267.248.248 0 00-.183-.064h-.021c-.071.006-.13.04-.186.132a.552.552 0 00-.12.27.944.944 0 00-.023.33v.015c.012.135.037.2.08.334.046.134.098.2.166.268.01.009.02.018.034.024-.07.057-.117.07-.176.136a.304.304 0 01-.131.068 2.62 2.62 0 01-.275-.402 1.772 1.772 0 01-.155-.667 1.759 1.759 0 01.08-.668 1.43 1.43 0 01.283-.535c.128-.133.26-.2.418-.2zm1.37 1.706c.332 0 .733.065 1.216.399.293.2.523.269 1.052.468h.003c.255.136.405.266.478.399v-.131a.571.571 0 01.016.47c-.123.31-.516.643-1.063.842v.002c-.268.135-.501.333-.775.465-.276.135-.588.292-1.012.267a1.139 1.139 0 01-.448-.067 3.566 3.566 0 01-.322-.198c-.195-.135-.363-.332-.612-.465v-.005h-.005c-.4-.246-.616-.512-.686-.71-.07-.268-.005-.47.193-.6.224-.135.38-.271.483-.336.104-.074.143-.102.176-.131h.002v-.003c.169-.202.436-.47.839-.601.139-.036.294-.065.466-.065zm2.8 2.142c.358 1.417 1.196 3.475 1.735 4.473.286.534.855 1.659 1.102 3.024.156-.005.33.018.513.064.646-1.671-.546-3.467-1.089-3.966-.22-.2-.232-.335-.123-.335.59.534 1.365 1.572 1.646 2.757.13.535.16 1.104.021 1.67.067.028.135.06.205.067 1.032.534 1.413.938 1.23 1.537v-.043c-.06-.003-.12 0-.18 0h-.016c.151-.467-.182-.825-1.065-1.224-.915-.4-1.646-.336-1.77.465-.008.043-.013.066-.018.135-.068.023-.139.053-.209.064-.43.268-.662.669-.793 1.187-.13.533-.17 1.156-.205 1.869v.003c-.02.334-.17.838-.319 1.35-1.5 1.072-3.58 1.538-5.348.334a2.645 2.645 0 00-.402-.533 1.45 1.45 0 00-.275-.333c.182 0 .338-.03.465-.067a.615.615 0 00.314-.334c.108-.267 0-.697-.345-1.163-.345-.467-.931-.995-1.788-1.521-.63-.4-.986-.87-1.15-1.396-.165-.534-.143-1.085-.015-1.645.245-1.07.873-2.11 1.274-2.763.107-.065.037.135-.408.974-.396.751-1.14 2.497-.122 3.854a8.123 8.123 0 01.647-2.876c.564-1.278 1.743-3.504 1.836-5.268.048.036.217.135.289.202.218.133.38.333.59.465.21.201.477.335.876.335.039.003.075.006.11.006.412 0 .73-.134.997-.268.29-.134.52-.334.74-.4h.005c.467-.135.835-.402 1.044-.7zm2.185 8.958c.037.6.343 1.245.882 1.377.588.134 1.434-.333 1.791-.765l.211-.01c.315-.007.577.01.847.268l.003.003c.208.199.305.53.391.876.085.4.154.78.409 1.066.486.527.645.906.636 1.14l.003-.007v.018l-.003-.012c-.015.262-.185.396-.498.595-.63.401-1.746.712-2.457 1.57-.618.737-1.37 1.14-2.036 1.191-.664.053-1.237-.2-1.574-.898l-.005-.003c-.21-.4-.12-1.025.056-1.69.176-.668.428-1.344.463-1.897.037-.714.076-1.335.195-1.814.12-.465.308-.797.641-.984l.045-.022zm-10.814.049h.01c.053 0 .105.005.157.014.376.055.706.333 1.023.752l.91 1.664.003.003c.243.533.754 1.064 1.189 1.637.434.598.77 1.131.729 1.57v.006c-.057.744-.48 1.148-1.125 1.294-.645.135-1.52.002-2.395-.464-.968-.536-2.118-.469-2.857-.602-.369-.066-.61-.2-.723-.4-.11-.2-.113-.602.123-1.23v-.004l.002-.003c.117-.334.03-.752-.027-1.118-.055-.401-.083-.71.043-.94.16-.334.396-.4.69-.533.294-.135.64-.202.915-.47h.002v-.002c.256-.268.445-.601.668-.838.19-.201.38-.336.663-.336zm7.159-9.074c-.435.201-.945.535-1.488.535-.542 0-.97-.267-1.28-.466-.154-.134-.28-.268-.373-.335-.164-.134-.144-.333-.074-.333.109.016.129.134.199.2.096.066.215.2.36.333.292.2.68.467 1.167.467.485 0 1.053-.267 1.398-.466.195-.135.445-.334.648-.467.156-.136.149-.267.279-.267.128.016.034.134-.147.332a8.097 8.097 0 01-.69.468zm-1.082-1.583V5.64c-.006-.02.013-.042.029-.05.074-.043.18-.027.26.004.063 0 .16.067.15.135-.006.049-.085.066-.135.066-.055 0-.092-.043-.141-.068-.052-.018-.146-.008-.163-.065zm-.551 0c-.02.058-.113.049-.166.066-.047.025-.086.068-.14.068-.05 0-.13-.02-.136-.068-.01-.066.088-.133.15-.133.08-.031.184-.047.259-.005.019.009.036.03.03.05v.02h.003z"
                    }
                }
            }
    }
}
