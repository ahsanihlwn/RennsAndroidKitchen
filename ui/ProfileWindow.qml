// SPDX-License-Identifier: MPL-2.0
import QtQuick
import QtQuick.Controls as C
import QtQuick.Shapes
import "controls"

DialogWindow {
    id: dialog
    objectName: "profileWindow"
    property var backend: null
    property bool avatarPage: false
    property bool photoPage: false
    property string selectedAvatar: "000"
    property string pendingAction: ""
    readonly property bool anims: !!(model && model.settings && model.settings.animations)
    readonly property string authProvider: (backend && backend.draft && backend.draft.auth_provider) ? String(backend.draft.auth_provider) : ""
    readonly property string googleBadge: (authProvider === "google" && backend && backend.googleBadgeUrl) ? String(backend.googleBadgeUrl) : ""
    readonly property string previewUrl: backend ? String(backend.preview || "") : ""
    implicitWidth: 360; implicitHeight: 378
    title: "Edit Profile"
    panelAlpha: 180/255

    function begin() {
        if (!backend) return
        backend.begin()
        username.text = backend.draft.username || ""
        role.text = backend.draft.employee_status || ""
        selectedAvatar = backend.draft.avatar || "000"
        avatarPage = false; photoPage = false; pendingAction = ""
        open()
    }
    onPresentedChanged: {
        if (!anims) {
            stage.y = 0
            avatarHero.scale = 1
            return
        }
        if (presented) {
            openAnim.restart()
        } else {
            closeAnim.restart()
        }
    }
    onPreviewUrlChanged: {
        if (presented && anims && !avatarPage) {
            avatarChangeAnim.restart()
        }
    }
    onDismissed: {
        if (pendingAction === "save") backend.save(username.text, role.text)
        else if (pendingAction === "logout") backend.logoutRequested()
        pendingAction = ""
    }

    ParallelAnimation {
        id: openAnim
        NumberAnimation { target: stage; property: "y"; from: 8; to: 0; duration: 200; easing.type: Easing.OutCubic }
        SequentialAnimation {
            PauseAnimation { duration: 40 }
            NumberAnimation { target: avatarHero; property: "scale"; from: 0.86; to: 1.0; duration: 240; easing.type: Easing.OutBack }
        }
    }
    NumberAnimation { id: closeAnim; target: stage; property: "y"; to: 4; duration: 120; easing.type: Easing.InCubic }
    ParallelAnimation {
        id: avatarChangeAnim
        NumberAnimation { target: avatarPreview; property: "opacity"; from: 0.35; to: 1.0; duration: 160; easing.type: Easing.OutCubic }
        NumberAnimation { target: avatarHero; property: "scale"; from: 0.86; to: 1.0; duration: 240; easing.type: Easing.OutBack }
    }

    Item {
        id: stage
        width: parent.width; height: parent.height
        transformOrigin: Item.Center

        // Page 1: Edit Profile
        Item {
            id: editPage
            width: parent.width; height: parent.height
            readonly property bool current: !dialog.avatarPage && !dialog.photoPage
            enabled: current
            opacity: current ? 1 : 0
            x: current ? 0 : -14
            scale: current ? 1 : 0.97
            visible: current || opacity > 0.01
            Behavior on opacity { NumberAnimation { duration: dialog.anims ? 170 : 0; easing.type: Easing.OutCubic } }
            Behavior on x { NumberAnimation { duration: dialog.anims ? 190 : 0; easing.type: Easing.OutCubic } }
            Behavior on scale { NumberAnimation { duration: dialog.anims ? 190 : 0; easing.type: Easing.OutCubic } }

            RakLabel {
                x: 24; y: 17; height: 24
                text: "Edit Profile"
                font.pixelSize: 15; font.weight: Font.DemiBold
                color: dialog.c.FG_BRIGHT
            }
            Image {
                visible: !!dialog.googleBadge
                x: 316; y: 19; width: 20; height: 20
                source: dialog.googleBadge
                fillMode: Image.PreserveAspectFit
                smooth: true; mipmap: true
            }
            SmoothRectangle { x: 24; y: 50; width: 312; height: 1; color: dialog.c.b07 }

            Item {
                id: avatarHero
                x: 142; y: 66; width: 76; height: 76
                transformOrigin: Item.Center

                RakButton {
                    objectName: "profileAvatarView"
                    anchors.fill: parent
                    cornerRadius: 38
                    edgeWidth: 1.5
                    fillColor: dialog.c.b04; hoverFillColor: dialog.c.b08; downFillColor: dialog.c.b12
                    edgeColor: dialog.c.b14; hoverEdgeColor: dialog.c.accent_hex
                    leftPadding: 4; rightPadding: 4; topPadding: 4; bottomPadding: 4
                    toolTip: "View profile photo"
                    contentItem: RoundedImage {
                        id: avatarPreview
                        source: dialog.previewUrl
                        samplingExtent: 68; radius: 34
                    }
                    onClicked: dialog.photoPage = true
                }

                RakButton {
                    id: editBtn
                    objectName: "profileAvatarEdit"
                    x: 52; y: 52; width: 26; height: 26; cornerRadius: 13
                    leftPadding: 0; rightPadding: 0; topPadding: 0; bottomPadding: 0
                    edgeWidth: 1
                    fillColor: dialog.c.b08; hoverFillColor: dialog.c.b14; downFillColor: dialog.c.b18
                    edgeColor: dialog.c.b18; hoverEdgeColor: dialog.c.accent_hex
                    toolTip: "Change avatar"
                    onClicked: dialog.avatarPage = true
                    contentItem: Item {
                        Item {
                            width: 24; height: 24
                            anchors.centerIn: parent
                            scale: 12.5 / 24.0
                            transformOrigin: Item.Center
                            Shape {
                                anchors.fill: parent
                                preferredRendererType: Shape.CurveRenderer
                                ShapePath {
                                    fillColor: editBtn.hovered ? dialog.c.accent_hex : dialog.c.FG_BRIGHT
                                    strokeColor: "transparent"
                                    PathSvg {
                                        path: "M 14.2 6.2 L 17.8 9.8 L 8.2 19.4 L 3.8 20.2 L 4.6 15.8 Z M 15.4 5.0 L 16.9 3.5 Q 17.7 2.8 18.5 3.5 L 20.5 5.5 Q 21.2 6.3 20.5 7.1 L 19.0 8.6 Z"
                                    }
                                }
                            }
                        }
                    }
                }
            }

            RakLabel {
                x: 24; y: 156; height: 18
                text: "Username"
                font.pixelSize: 12; font.weight: Font.Medium
                color: dialog.c.FG_MUTED
            }
            RakLabel {
                x: 24; y: 226; height: 18
                text: "Role"
                font.pixelSize: 12; font.weight: Font.Medium
                color: dialog.c.FG_MUTED
            }
            component ProfileField: RakField {
                x: 24; width: 312; height: 36
                fillColor: dialog.c.BG_INPUT; focusFillColor: dialog.c.BG_SELECTED
                edgeColor: dialog.c.b12; focusEdgeColor: dialog.c.a55
                labelColor: dialog.c.FG_BRIGHT; hintColor: dialog.c.placeholder
                onAccepted: { dialog.pendingAction = "save"; dialog.dismiss() }
            }
            ProfileField { id: username; objectName: "profileUsername"; y: 178; placeholderText: "Username" }
            ProfileField { id: role; objectName: "profileRole"; y: 248; placeholderText: "Role" }

            SmoothRectangle { x: 24; y: 306; width: 312; height: 1; color: dialog.c.b07 }
            DialogButton {
                objectName: "profileLogout"
                x: 24; y: 324; width: 78; height: 30
                colors: dialog.c; text: "Log out"; danger: true
                onClicked: { dialog.pendingAction = "logout"; dialog.dismiss() }
            }
            DialogButton { objectName: "profileCancel"; x: 185; y: 324; width: 74; height: 30; colors: dialog.c; text: "Cancel"; onClicked: dialog.dismiss() }
            DialogButton { objectName: "profileSave"; x: 269; y: 324; width: 67; height: 30; colors: dialog.c; primary: true; text: "Save"; onClicked: { dialog.pendingAction = "save"; dialog.dismiss() } }
        }

        // Page 2: HD Photo Preview
        Item {
            id: photoViewPage
            width: parent.width; height: parent.height
            readonly property bool current: dialog.photoPage && !dialog.avatarPage
            enabled: current
            opacity: current ? 1 : 0
            scale: current ? 1 : 0.90
            visible: current || opacity > 0.01
            Behavior on opacity { NumberAnimation { duration: dialog.anims ? 180 : 0; easing.type: Easing.OutCubic } }
            Behavior on scale { NumberAnimation { duration: dialog.anims ? 220 : 0; easing.type: Easing.OutCubic } }

            RakLabel {
                x: 24; y: 17; height: 24; width: 270
                elide: Text.ElideRight
                text: username.text.trim() ? username.text.trim() : "Profile Photo"
                font.pixelSize: 15; font.weight: Font.DemiBold
                color: dialog.c.FG_BRIGHT
            }
            Image {
                visible: !!dialog.googleBadge
                x: 316; y: 19; width: 20; height: 20
                source: dialog.googleBadge
                fillMode: Image.PreserveAspectFit
                smooth: true; mipmap: true
            }
            SmoothRectangle { x: 24; y: 50; width: 312; height: 1; color: dialog.c.b07 }

            Item {
                x: 70; y: 68; width: 220; height: 220
                SmoothRectangle {
                    anchors.fill: parent; radius: 110
                    color: dialog.c.b04; border.width: 1.5; border.color: dialog.c.b14
                }
                RoundedImage {
                    objectName: "profilePhotoHd"
                    anchors.fill: parent; anchors.margins: 4
                    source: ((dialog.photoPage || photoViewPage.visible) && dialog.backend) ? dialog.backend.previewHd : ""
                    samplingExtent: 212; radius: 106
                }
                MouseArea {
                    anchors.fill: parent; cursorShape: Qt.PointingHandCursor
                    onClicked: dialog.photoPage = false
                }
            }

            SmoothRectangle { x: 24; y: 306; width: 312; height: 1; color: dialog.c.b07 }
            DialogButton {
                objectName: "profilePhotoBack"
                x: 262; y: 324; width: 74; height: 30
                colors: dialog.c; text: "Back"
                onClicked: dialog.photoPage = false
            }
        }

        // Page 3: Choose Avatar
        Item {
            id: pickerPage
            width: parent.width; height: parent.height
            readonly property bool current: dialog.avatarPage
            enabled: current
            opacity: current ? 1 : 0
            x: current ? 0 : 14
            scale: current ? 1 : 0.98
            visible: current || opacity > 0.01
            Behavior on opacity { NumberAnimation { duration: dialog.anims ? 170 : 0; easing.type: Easing.OutCubic } }
            Behavior on x { NumberAnimation { duration: dialog.anims ? 190 : 0; easing.type: Easing.OutCubic } }
            Behavior on scale { NumberAnimation { duration: dialog.anims ? 190 : 0; easing.type: Easing.OutCubic } }

            RakLabel {
                x: 24; y: 17; height: 24
                text: "Choose Avatar"
                font.pixelSize: 15; font.weight: Font.DemiBold
                color: dialog.c.FG_BRIGHT
            }
            RakLabel {
                x: 336 - width; y: 20; height: 20
                text: avatarChoices.count + " options"
                font.pixelSize: 11
                color: dialog.c.FG_MUTED
            }
            SmoothRectangle { x: 24; y: 50; width: 312; height: 1; color: dialog.c.b07 }

            SmoothRectangle {
                x: 24; y: 62; width: 312; height: 234; radius: 10
                color: dialog.c.b04; border.width: 1; border.color: dialog.c.b07

                Flickable {
                    id: avatars
                    objectName: "profileAvatars"
                    anchors.fill: parent
                    anchors.margins: 6
                    clip: true
                    contentHeight: Math.ceil(avatarChoices.count / 3) * 74 + 4
                    boundsBehavior: Flickable.StopAtBounds
                    Repeater {
                        id: avatarChoices
                        model: dialog.backend ? dialog.backend.avatars : []
                        delegate: RakButton {
                            id: avatar
                            required property var modelData
                            required property int index
                            readonly property bool chosen: dialog.selectedAvatar === modelData.key
                            objectName: "profileAvatar_" + modelData.key
                            x: 20 + (index % 3) * 92
                            y: 4 + Math.floor(index / 3) * 74
                            width: 68; height: 68; cornerRadius: 34
                            scale: chosen ? 1.05 : avatar.hovered ? 1.02 : 1.0
                            Behavior on scale { NumberAnimation { duration: dialog.anims ? 160 : 0; easing.type: Easing.OutBack } }
                            fillColor: chosen ? dialog.c.a12 : "transparent"
                            hoverFillColor: chosen ? dialog.c.a18 : dialog.c.b06
                            downFillColor: dialog.c.a18
                            edgeColor: chosen ? dialog.c.accent_hex : dialog.c.b12
                            hoverEdgeColor: chosen ? dialog.c.accent_hex : dialog.c.a70
                            edgeWidth: chosen ? 2 : 1
                            leftPadding: 4; rightPadding: 4; topPadding: 4; bottomPadding: 4
                            contentItem: RoundedImage {
                                source: avatar.modelData.url
                                samplingExtent: 60; radius: 30
                            }
                            onClicked: dialog.selectedAvatar = modelData.key
                        }
                    }
                    C.ScrollBar.vertical: RakScrollBar {
                        width: 4
                        handleColor: dialog.c.b14
                        minimumSize: Math.min(1, 24 / Math.max(1, height))
                    }
                }
            }

            SmoothRectangle { x: 24; y: 306; width: 312; height: 1; color: dialog.c.b07 }
            DialogButton { objectName: "profileAvatarCancel"; x: 185; y: 324; width: 74; height: 30; colors: dialog.c; text: "Cancel"; onClicked: dialog.avatarPage = false }
            DialogButton { objectName: "profileAvatarAccept"; x: 269; y: 324; width: 67; height: 30; colors: dialog.c; primary: true; text: "OK"; onClicked: { dialog.backend.chooseAvatar(dialog.selectedAvatar); dialog.avatarPage = false } }
        }
    }
}
