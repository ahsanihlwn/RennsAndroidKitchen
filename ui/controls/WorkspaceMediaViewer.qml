// SPDX-License-Identifier: MPL-2.0
import QtQuick
import QtQuick.Controls as C
import QtQuick.Dialogs as D
import QtQuick.Window
import QtMultimedia
import Renns.Workspace
Item {
    id: viewer; objectName: "workspaceMediaViewer"
    required property var workspace
    required property var preferences
    required property var details
    readonly property var c: preferences.colors
    readonly property bool videoMode: details.kind==="video"
    readonly property bool anims: preferences && preferences.settings ? preferences.settings.animations : true
    readonly property var playerModel: player
    readonly property var documentModel: document
    property bool wasPlaying: false
    readonly property bool blocked: (Window.window && Window.window.nativeDialogActive) || outputPicker.visible
    onBlockedChanged: {
        if(blocked){wasPlaying=player.playbackState===MediaPlayer.PlayingState;player.pause()}
        else if(wasPlaying && visible){wasPlaying=false;player.play()}
    }
    function time(ms){let seconds=Math.max(0,Math.floor(ms/1000));const h=Math.floor(seconds/3600);seconds%=3600;return(h?h+":"+String(Math.floor(seconds/60)).padStart(2,"0"):String(Math.floor(seconds/60)))+":"+String(seconds%60).padStart(2,"0")}
    function playPause(){if(player.playbackState===MediaPlayer.PlayingState)player.pause();else player.play()}
    MediaDocument { id: document }
    Component.onCompleted: document.load(details.documentPath || details.path)
    Component.onDestruction: {player.stop();player.source="";document.close()}
    MediaPlayer { id: player; source: viewer.details.url; audioOutput: AudioOutput { id: audio; volume: .8 } videoOutput: video }
    SmoothRectangle { anchors.fill: parent; color: viewer.c.BG2 }

    // Media Stage
    Item {
        id: mediaStage
        anchors.top: parent.top; anchors.left: parent.left; anchors.right: parent.right
        anchors.bottom: controlDock.top

        // Video Canvas
        SmoothRectangle { anchors.fill: parent; visible: viewer.videoMode; color: "#0b0d13" }
        VideoOutput { id: video; anchors.fill: parent; visible: viewer.videoMode; fillMode: VideoOutput.PreserveAspectFit }
        MouseArea {
            id: videoStageMouse
            anchors.fill: parent; visible: viewer.videoMode
            hoverEnabled: true
            cursorShape: Qt.PointingHandCursor
            onClicked: viewer.playPause()
        }

        // Center Video Play/Pause Indicator Overlay
        SmoothRectangle {
            visible: viewer.videoMode && player.error === MediaPlayer.NoError && !conversion.visible
            anchors.centerIn: parent
            width: 56; height: 56; radius: 28
            color: Qt.rgba(0.06, 0.08, 0.12, 0.72)
            border.width: 1; border.color: Qt.rgba(1, 1, 1, 0.18)
            opacity: (player.playbackState !== MediaPlayer.PlayingState || videoStageMouse.containsMouse) ? 1.0 : 0.0
            scale: opacity > 0.5 ? 1.0 : 0.90
            Behavior on opacity { NumberAnimation { duration: viewer.anims ? 150 : 0 } }
            Behavior on scale { NumberAnimation { duration: viewer.anims ? 150 : 0; easing.type: Easing.OutCubic } }
            WorkspaceNativeIcon {
                anchors.centerIn: parent
                anchors.horizontalCenterOffset: player.playbackState === MediaPlayer.PlayingState ? 0 : 1
                iconKey: player.playbackState === MediaPlayer.PlayingState ? "pause" : "play"
                iconSize: 22
                strokeColor: "#ffffff"
                isLight: false
            }
        }

        // Audio Stage Card
        SmoothRectangle {
            visible: !viewer.videoMode && player.error === MediaPlayer.NoError
            anchors.centerIn: parent
            width: Math.min(parent.width - 48, 360)
            height: audioCardCol.implicitHeight + 48
            radius: 14
            color: viewer.c.b03; border.width: 1; border.color: viewer.c.b08
            Column {
                id: audioCardCol
                anchors.centerIn: parent; width: parent.width - 40; spacing: 14
                SmoothRectangle {
                    anchors.horizontalCenter: parent.horizontalCenter
                    width: 84; height: 84; radius: 42
                    color: viewer.c.a12; border.width: 1; border.color: viewer.c.a30
                    WorkspaceNativeIcon {
                        anchors.centerIn: parent
                        iconKey: "media_audio"
                        iconSize: 42
                        strokeColor: viewer.c.accent_hex || viewer.c.FG_BLUE
                        accentColor: viewer.c.accent_hex || "#38bdf8"
                        isLight: !!(viewer.c && viewer.c.is_light)
                    }
                }
                RakLabel {
                    width: parent.width; text: document.name
                    font.pixelSize: 14; font.weight: Font.DemiBold; color: viewer.c.FG
                    wrapMode: Text.WordWrap; horizontalAlignment: Text.AlignHCenter
                }
                RakLabel {
                    width: parent.width
                    text: (player.duration ? viewer.time(player.duration) + "  ·  " : "") + document.fileInfo
                    font.pixelSize: 11; color: viewer.c.FG_MUTED
                    horizontalAlignment: Text.AlignHCenter; elide: Text.ElideRight
                }
            }
        }

        // Error Card
        SmoothRectangle {
            visible: player.error !== MediaPlayer.NoError
            anchors.centerIn: parent
            width: Math.min(parent.width - 48, 420)
            height: errorRow.implicitHeight + 28
            radius: 10
            color: viewer.videoMode ? Qt.rgba(0.10, 0.11, 0.15, 0.90) : viewer.c.b04
            border.width: 1; border.color: viewer.c.b12
            Row {
                id: errorRow
                anchors.centerIn: parent; width: parent.width - 32; spacing: 10
                WorkspaceNativeIcon {
                    iconKey: "info"; iconSize: 18
                    strokeColor: "#f59e0b"
                    isLight: !!(viewer.c && viewer.c.is_light)
                    anchors.verticalCenter: parent.verticalCenter
                }
                RakLabel {
                    width: parent.width - 28
                    text: player.errorString || "Format ini tidak didukung untuk diputar."
                    wrapMode: Text.WordWrap
                    color: viewer.videoMode ? "#e2e8f0" : viewer.c.FG
                    font.pixelSize: 12
                }
            }
        }
    }

    // Playback Control Dock
    Item {
        id: controlDock
        anchors.left: parent.left; anchors.right: parent.right; anchors.bottom: statusBar.top
        height: 54
        SmoothRectangle { anchors.fill: parent; color: viewer.c.BG }
        SmoothRectangle { anchors.top: parent.top; width: parent.width; height: 1; color: viewer.c.b08 }

        // Left: Play/Pause + Elapsed Time
        Row {
            id: leftControls
            anchors.left: parent.left; anchors.leftMargin: 12; anchors.verticalCenter: parent.verticalCenter
            spacing: 10
            RakButton {
                objectName: "mediaPlay"
                width: 34; height: 34; implicitWidth: 34; cornerRadius: 17
                leftPadding: 0; rightPadding: 0
                iconKey: player.playbackState === MediaPlayer.PlayingState ? "pause" : "play"
                iconWidth: 16; iconHeight: 16
                fillColor: viewer.c.a18; hoverFillColor: viewer.c.a30; downFillColor: viewer.c.a44
                edgeColor: viewer.c.a40; hoverEdgeColor: viewer.c.a60
                labelColor: viewer.c.accent_hex || viewer.c.FG_BLUE
                hoverLabelColor: viewer.c.FG_BRIGHT
                enabled: player.error === MediaPlayer.NoError
                anchors.verticalCenter: parent.verticalCenter
                onClicked: viewer.playPause()
            }
            RakLabel {
                id: elapsedLabel
                width: viewer.videoMode ? 52 : 46; height: 26
                font.pixelSize: 11; font.weight: Font.Medium; color: viewer.c.FG
                text: viewer.time(seek.pressed ? seek.value : player.position)
                horizontalAlignment: Text.AlignHCenter
                anchors.verticalCenter: parent.verticalCenter
            }
        }

        // Center: Flexible Seek Slider
        ViewerSlider {
            id: seek
            objectName: "mediaSeek"
            anchors.left: leftControls.right; anchors.leftMargin: 6
            anchors.right: rightControls.left; anchors.rightMargin: 6
            anchors.verticalCenter: parent.verticalCenter
            colors: viewer.c; from: 0; to: Math.max(1, player.duration); value: player.position
            enabled: player.error === MediaPlayer.NoError
            onPressedChanged: if (!pressed) player.setPosition(value)
        }

        // Right: Total Duration + Volume Pill + Convert to Images
        Row {
            id: rightControls
            anchors.right: parent.right; anchors.rightMargin: 12; anchors.verticalCenter: parent.verticalCenter
            spacing: 8
            RakLabel {
                width: viewer.videoMode ? 52 : 46; height: 26
                font.pixelSize: 11; color: viewer.c.FG_MUTED
                text: viewer.time(player.duration)
                horizontalAlignment: Text.AlignHCenter
                anchors.verticalCenter: parent.verticalCenter
            }
            SmoothRectangle {
                height: 30; width: volumeRow.implicitWidth + 8; radius: 8
                color: viewer.c.b04; border.width: 1; border.color: viewer.c.b08
                anchors.verticalCenter: parent.verticalCenter
                Row {
                    id: volumeRow
                    anchors.centerIn: parent; spacing: 4
                    ImageToolButton {
                        objectName: "mediaMute"
                        colors: viewer.c; segmented: true; width: 28; height: 24; implicitWidth: 28
                        iconKey: (audio.muted || audio.volume <= 0.001) ? "mute" : "volume"
                        iconWidth: 15; iconHeight: 15
                        toolTip: audio.muted ? "Unmute" : "Mute"
                        anchors.verticalCenter: parent.verticalCenter
                        onClicked: audio.muted = !audio.muted
                    }
                    ViewerSlider {
                        objectName: "mediaVolume"
                        width: 78; colors: viewer.c; from: 0; to: 1; value: .8
                        anchors.verticalCenter: parent.verticalCenter
                        onMoved: { audio.volume = value; if (value > 0) audio.muted = false }
                    }
                }
            }
            ImageToolButton {
                objectName: "mediaToImages"
                visible: viewer.videoMode
                colors: viewer.c; height: 30
                iconKey: "convert_frames"; iconWidth: 15; iconHeight: 15
                text: "Export Frames"
                anchors.verticalCenter: parent.verticalCenter
                onClicked: conversion.open()
            }
        }
    }

    // Bottom Status Bar
    Item {
        id: statusBar
        anchors.left: parent.left; anchors.right: parent.right; anchors.bottom: parent.bottom
        height: 28
        SmoothRectangle { anchors.fill: parent; color: viewer.c.BG }
        SmoothRectangle { anchors.top: parent.top; width: parent.width; height: 1; color: viewer.c.b08 }
        RakLabel {
            anchors.fill: parent; anchors.leftMargin: 12; anchors.rightMargin: 12
            font.pixelSize: 11; color: viewer.c.FG_MUTED
            text: document.name + "   ·   " + (player.duration ? viewer.time(player.duration) : "—") + "   ·   " + document.fileInfo
            elide: Text.ElideRight
        }
    }

    DialogWindow {
        id: conversion; objectName: "videoConvertDialog"; model: viewer.preferences; hostWindow: viewer.Window.window
        backdropSource: (viewer.Window.window && viewer.Window.window.backdropSurface) ? viewer.Window.window.backdropSurface : null
        implicitWidth: 444; implicitHeight: convertCol.implicitHeight + 44
        property string mode: "fps"; property string format: "png"
        onAboutToShow: document.resetProgress()
        onDismissed: document.cancel()
        Column {
            id: convertCol
            x: 22; y: 22; width: parent.width - 44; spacing: 14
            Row {
                spacing: 8
                WorkspaceNativeIcon {
                    iconKey: "convert_frames"; iconSize: 16
                    strokeColor: viewer.c.accent_hex || viewer.c.FG
                    accentColor: viewer.c.accent_hex || "#38bdf8"
                    isLight: !!(viewer.c && viewer.c.is_light)
                    anchors.verticalCenter: parent.verticalCenter
                }
                RakLabel { text: "Export Frames"; font.pixelSize: 14; font.weight: Font.DemiBold; color: viewer.c.FG; anchors.verticalCenter: parent.verticalCenter }
            }
            SmoothRectangle { width: parent.width; height: 1; color: viewer.c.b07 }
            Column {
                width: parent.width; spacing: 6
                RakLabel { text: "Frames"; font.pixelSize: 11; font.weight: Font.DemiBold; color: viewer.c.FG_BLUE_MUTED }
                Row {
                    spacing: 8; enabled: !document.busy
                    SmoothRectangle {
                        height: 30; width: frameModeRow.implicitWidth + 6; radius: 8
                        color: viewer.c.b04; border.width: 1; border.color: viewer.c.b08
                        Row {
                            id: frameModeRow; anchors.centerIn: parent; spacing: 2
                            ImageToolButton { colors: viewer.c; segmented: true; height: 24; text: "All frames"; selected: conversion.mode === "all"; onClicked: conversion.mode = "all" }
                            ImageToolButton { colors: viewer.c; segmented: true; height: 24; text: "Target FPS"; selected: conversion.mode === "fps"; onClicked: conversion.mode = "fps" }
                        }
                    }
                    ViewerField {
                        id: fps; colors: viewer.c; width: 58; height: 30; cornerRadius: 7
                        topPadding: 4; bottomPadding: 4; leftPadding: 6; rightPadding: 6
                        horizontalAlignment: TextInput.AlignHCenter
                        text: "30"; visible: conversion.mode === "fps"
                        validator: IntValidator { bottom: 1; top: 240 }
                        anchors.verticalCenter: parent.verticalCenter
                    }
                    RakLabel { height: 30; visible: fps.visible; text: "fps"; font.pixelSize: 11; color: viewer.c.FG_MUTED; anchors.verticalCenter: parent.verticalCenter }
                }
            }
            Column {
                width: parent.width; spacing: 6
                RakLabel { text: "Format"; font.pixelSize: 11; font.weight: Font.DemiBold; color: viewer.c.FG_BLUE_MUTED }
                Row {
                    width: parent.width; spacing: 8; enabled: !document.busy
                    SmoothRectangle {
                        id: fmtCard
                        height: 30; width: fmtModeRow.implicitWidth + 6; radius: 8
                        color: viewer.c.b04; border.width: 1; border.color: viewer.c.b08
                        anchors.verticalCenter: parent.verticalCenter
                        Row {
                            id: fmtModeRow; anchors.centerIn: parent; spacing: 2
                            ImageToolButton { colors: viewer.c; segmented: true; height: 24; text: "PNG"; selected: conversion.format === "png"; onClicked: conversion.format = "png" }
                            ImageToolButton { colors: viewer.c; segmented: true; height: 24; text: "JPG"; selected: conversion.format === "jpg"; onClicked: conversion.format = "jpg" }
                        }
                    }
                    SmoothRectangle {
                        visible: conversion.format === "jpg"
                        height: 30; width: parent.width - fmtCard.width - 8; radius: 8
                        color: viewer.c.b03; border.width: 1; border.color: viewer.c.b07
                        anchors.verticalCenter: parent.verticalCenter
                        Row {
                            anchors.fill: parent; anchors.leftMargin: 10; anchors.rightMargin: 8; spacing: 8
                            RakLabel {
                                id: qualLbl
                                height: 30; text: "Quality"; font.pixelSize: 11; font.weight: Font.Medium; color: viewer.c.FG_MUTED
                                anchors.verticalCenter: parent.verticalCenter
                            }
                            ViewerSlider {
                                id: quality
                                colors: viewer.c; width: parent.width - qualLbl.implicitWidth - qualBadge.width - 16
                                from: 50; to: 100; value: 92; stepSize: 1
                                anchors.verticalCenter: parent.verticalCenter
                            }
                            SmoothRectangle {
                                id: qualBadge
                                width: 34; height: 20; radius: 5; color: viewer.c.b06
                                anchors.verticalCenter: parent.verticalCenter
                                RakLabel {
                                    anchors.centerIn: parent
                                    text: Math.round(quality.value)
                                    font.pixelSize: 10; font.weight: Font.Medium; color: viewer.c.FG
                                }
                            }
                        }
                    }
                }
            }
            Column {
                width: parent.width; spacing: 6
                RakLabel { text: "Output folder"; font.pixelSize: 11; font.weight: Font.DemiBold; color: viewer.c.FG_BLUE_MUTED }
                Row {
                    width: parent.width; spacing: 6; enabled: !document.busy
                    ViewerField {
                        id: output; objectName: "videoOutputFolder"
                        width: parent.width - browseBtn.width - 6; height: 30; cornerRadius: 7
                        topPadding: 4; bottomPadding: 4; leftPadding: 10; rightPadding: 10
                        colors: viewer.c; text: document.outputFolder
                    }
                    ImageToolButton { id: browseBtn; colors: viewer.c; height: 30; text: "Browse"; onClicked: outputPicker.open() }
                }
            }
            Column {
                visible: document.progress.length > 0
                width: parent.width; spacing: 6
                RakLabel { text: document.progress; font.pixelSize: 11; color: viewer.c.FG_BLUE_MUTED }
                C.ProgressBar { width: parent.width; height: 6; indeterminate: document.busy; value: document.busy ? 0 : 1 }
            }
            SmoothRectangle { width: parent.width; height: 1; color: viewer.c.b06 }
            Row {
                anchors.right: parent.right; spacing: 8
                DialogButton { objectName: "videoCancelConversion"; colors: viewer.c; text: document.busy ? "Stop" : "Cancel"; onClicked: conversion.dismiss() }
                DialogButton { objectName: "videoStartConversion"; visible: !document.busy; colors: viewer.c; primary: true; text: "Export"; onClicked: document.convert(output.text, conversion.mode, Number(fps.text), conversion.format, Math.round(quality.value)) }
            }
        }
        Connections { target: document; function onConversionFinished(){conversion.dismiss()} }
    }
    D.FolderDialog { id: outputPicker; title: "Output folder"; onAccepted: output.text = document.localPath(selectedFolder) }
}
