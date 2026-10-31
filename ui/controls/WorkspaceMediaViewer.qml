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
    Item {
        width: parent.width; height: parent.height-80
        SmoothRectangle { anchors.fill: parent; visible: viewer.videoMode; color: "black" }
        VideoOutput { id: video; anchors.fill: parent; visible: viewer.videoMode; fillMode: VideoOutput.PreserveAspectFit }
        MouseArea { anchors.fill: parent; visible: viewer.videoMode; onClicked: viewer.playPause() }
        Column {
            visible: !viewer.videoMode; width: parent.width-48; anchors.centerIn: parent; spacing: 16
            Image { anchors.horizontalCenter: parent.horizontalCenter; source: "image://icons/icon_audio"; width: 96; height: 96; sourceSize: Qt.size(96,96) }
            RakLabel { width: parent.width; text: document.name; font.pixelSize: 14; font.weight: Font.DemiBold; color: viewer.c.FG; wrapMode: Text.WordWrap; horizontalAlignment: Text.AlignHCenter }
        }
        RakLabel { anchors.centerIn: parent; width: parent.width-48; visible: player.error!==MediaPlayer.NoError; text: "⚠ "+(player.errorString || "Format ini tidak didukung untuk diputar."); wrapMode: Text.WordWrap; horizontalAlignment: Text.AlignHCenter; color: viewer.videoMode?"#cfd4dc":viewer.c.FG_MUTED; font.pixelSize: viewer.videoMode?13:12 }
    }
    SmoothRectangle {
        y: parent.height-80; width: parent.width; height: 54; color: viewer.c.BG2
        SmoothRectangle { width: parent.width; height: 1; color: viewer.c.b08 }
        Row {
            x: 12; y: 10; height: 34; spacing: 10
            RakButton { objectName: "mediaPlay"; width: 34; height: 34; implicitWidth: 34; cornerRadius: 17; leftPadding: 0; rightPadding: 0; iconSource: "image://icons/"+(player.playbackState===MediaPlayer.PlayingState?"pause":"play"); iconWidth: 16; iconHeight: 16; fillColor: viewer.c.a18; hoverFillColor: viewer.c.a30; downFillColor: viewer.c.a44; edgeColor: viewer.c.a40; hoverEdgeColor: viewer.c.a60; enabled: player.error===MediaPlayer.NoError; onClicked: viewer.playPause() }
            RakLabel { width: viewer.videoMode?54:48; height: 34; font.pixelSize: 11; color: viewer.c.FG_MUTED; text: viewer.time(seek.pressed?seek.value:player.position); horizontalAlignment: Text.AlignHCenter }
            ViewerSlider { id: seek; objectName: "mediaSeek"; y: 4; width: Math.max(40,viewer.width-(viewer.videoMode?449:300)); colors: viewer.c; from: 0; to: Math.max(1,player.duration); value: player.position; enabled: player.error===MediaPlayer.NoError; onPressedChanged: if(!pressed)player.setPosition(value) }
            RakLabel { width: viewer.videoMode?54:48; height: 34; font.pixelSize: 11; color: viewer.c.FG_MUTED; text: viewer.time(player.duration); horizontalAlignment: Text.AlignHCenter }
            SmoothRectangle { width: 1; height: 34; color: viewer.c.b10 }
            ImageToolButton { objectName: "mediaMute"; y: 4; width: 30; colors: viewer.c; iconSource: "image://icons/"+(audio.muted?"mute":"volume"); iconWidth: 15; iconHeight: 15; onClicked: audio.muted=!audio.muted }
            ViewerSlider { objectName: "mediaVolume"; y: 4; width: 90; colors: viewer.c; from: 0; to: 1; value: .8; onMoved: {audio.volume=value;if(value>0)audio.muted=false} }
            SmoothRectangle { visible: viewer.videoMode; width: 1; height: 34; color: viewer.c.b10 }
            ImageToolButton { objectName: "mediaToImages"; visible: viewer.videoMode; y: 4; colors: viewer.c; text: "  To Images"; iconSource: "image://icons/convert_frames"; onClicked: conversion.open() }
        }
    }
    SmoothRectangle { y: parent.height-26; width: parent.width; height: 1; color: viewer.c.b08 }
    RakLabel { x: 12; y: parent.height-26; height: 26; width: parent.width-24; font.pixelSize: 11; color: viewer.c.FG_MUTED; text: document.name+"   ·   "+(player.duration?viewer.time(player.duration):"—")+"   ·   "+document.fileInfo; elide: Text.ElideRight }
    DialogWindow {
        id: conversion; objectName: "videoConvertDialog"; model: viewer.preferences; hostWindow: viewer.Window.window
        implicitWidth: 420; implicitHeight: document.progress.length?410:360
        property string mode: "fps"; property string format: "png"
        onAboutToShow: document.resetProgress()
        onDismissed: document.cancel()
        Column {
            x: 28; y: 26; width: parent.width-56; spacing: 14
            RakLabel { text: "Convert to Images"; font.pixelSize: 13; font.weight: Font.DemiBold; color: viewer.c.FG }
            RakLabel { text: "Frames"; font.pixelSize: 11; font.weight: Font.DemiBold; color: viewer.c.FG_BLUE_MUTED }
            Row { spacing: 6; enabled: !document.busy
                ImageToolButton { colors: viewer.c; text: "All frames"; selected: conversion.mode==="all"; onClicked: conversion.mode="all" }
                ImageToolButton { colors: viewer.c; text: "Target FPS"; selected: conversion.mode==="fps"; onClicked: conversion.mode="fps" }
                ViewerField { id: fps; colors: viewer.c; width: 56; text: "30"; visible: conversion.mode==="fps"; validator: IntValidator { bottom: 1; top: 240 } }
                RakLabel { height: 30; visible: fps.visible; text: "fps"; font.pixelSize: 11; color: viewer.c.FG_MUTED }
            }
            RakLabel { text: "Format"; font.pixelSize: 11; font.weight: Font.DemiBold; color: viewer.c.FG_BLUE_MUTED }
            Row { spacing: 6; enabled: !document.busy
                ImageToolButton { colors: viewer.c; text: "PNG"; selected: conversion.format==="png"; onClicked: conversion.format="png" }
                ImageToolButton { colors: viewer.c; text: "JPG"; selected: conversion.format==="jpg"; onClicked: conversion.format="jpg" }
                RakLabel { visible: conversion.format==="jpg"; height: 26; text: "Quality "+Math.round(quality.value); font.pixelSize: 11; color: viewer.c.FG_MUTED }
                ViewerSlider { id: quality; visible: conversion.format==="jpg"; colors: viewer.c; width: 100; from: 50; to: 100; value: 92; stepSize: 1 }
            }
            RakLabel { text: "Output folder"; font.pixelSize: 11; font.weight: Font.DemiBold; color: viewer.c.FG_BLUE_MUTED }
            Row { spacing: 6; enabled: !document.busy
                ViewerField { id: output; objectName: "videoOutputFolder"; width: 270; colors: viewer.c; text: document.outputFolder }
                ImageToolButton { colors: viewer.c; text: "Browse"; onClicked: outputPicker.open() }
            }
            RakLabel { visible: document.progress.length>0; text: document.progress; font.pixelSize: 11; color: viewer.c.FG_BLUE_MUTED }
            C.ProgressBar { width: parent.width; height: 6; visible: document.progress.length>0; indeterminate: document.busy; value: document.busy?0:1 }
            Row { anchors.right: parent.right; spacing: 8
                DialogButton { objectName: "videoCancelConversion"; colors: viewer.c; text: document.busy?"Stop":"Cancel"; onClicked: conversion.dismiss() }
                DialogButton { objectName: "videoStartConversion"; visible: !document.busy; colors: viewer.c; primary: true; text: "Convert"; onClicked: document.convert(output.text,conversion.mode,Number(fps.text),conversion.format,Math.round(quality.value)) }
            }
        }
        Connections { target: document; function onConversionFinished(){conversion.dismiss()} }
    }
    D.FolderDialog { id: outputPicker; title: "Output folder"; onAccepted: output.text=document.localPath(selectedFolder) }
}
