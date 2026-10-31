// SPDX-License-Identifier: MPL-2.0
import QtQuick
import QtQuick.Controls as C
import "controls"

DialogWindow {
    id: dialog
    objectName: "profileWindow"
    property var backend: null
    property bool avatarPage: false
    property string selectedAvatar: "000"
    property string pendingAction: ""
    implicitWidth: 360; implicitHeight: 378
    title: "Edit Profile"
    panelAlpha: 180/255
    function begin() {
        if (!backend) return
        backend.begin()
        username.text=backend.draft.username || ""
        role.text=backend.draft.employee_status || ""
        selectedAvatar=backend.draft.avatar || "000"
        avatarPage=false; pendingAction=""
        open()
    }
    onDismissed: {
        if (pendingAction==="save") backend.save(username.text,role.text)
        else if (pendingAction==="logout") backend.logoutRequested()
        pendingAction=""
    }
    Item {
        anchors.fill: parent; visible: !dialog.avatarPage
        RakButton {
            x: 144; y: 29; width: 72; height: 72
            contentItem: RoundedImage { source: dialog.backend?dialog.backend.preview:""; width: 64; height: 64; radius: 32 }
            leftPadding: 4; rightPadding: 4; topPadding: 4; bottomPadding: 4
            cornerRadius: 36
        }
        RakButton {
            objectName: "profileAvatarEdit"
            x: 188; y: 73; width: 32; height: 32; cornerRadius: 16
            iconSource: dialog.model.icon("edit", ""); iconWidth: 20; iconHeight: 20
            hoverFillColor: dialog.c.b10; downFillColor: dialog.c.b18
            onClicked: dialog.avatarPage=true
        }
        RakLabel { x: 29; y: 139; height: 33; text: "Username"; color: dialog.c.FG; font.pixelSize: 13 }
        RakLabel { x: 29; y: 184; height: 33; text: "Role"; color: dialog.c.FG; font.pixelSize: 13 }
        component ProfileField: RakField {
            x: 106; width: 225; height: 35
            fillColor: dialog.c.BG_INPUT; focusFillColor: dialog.c.BG_SELECTED
            edgeColor: dialog.c.b12; focusEdgeColor: dialog.c.a55
            labelColor: dialog.c.FG; hintColor: dialog.c.placeholder
            onAccepted: { dialog.pendingAction="save"; dialog.dismiss() }
        }
        ProfileField { id: username; objectName: "profileUsername"; y: 139; placeholderText: "Username" }
        ProfileField { id: role; objectName: "profileRole"; y: 184; placeholderText: "Role" }
        DialogButton {
            objectName: "profileLogout"
            x: 29; y: 321; width: 75; height: 30
            colors: dialog.c; text: "Log out"; danger: true
            onClicked: { dialog.pendingAction="logout"; dialog.dismiss() }
        }
        DialogButton { objectName: "profileCancel"; x: 183; y: 321; width: 73; colors: dialog.c; text: "Cancel"; onClicked: dialog.dismiss() }
        DialogButton { objectName: "profileSave"; x: 270; y: 321; width: 61; colors: dialog.c; primary: true; text: "Save"; onClicked: { dialog.pendingAction="save"; dialog.dismiss() } }
    }
    Item {
        anchors.fill: parent; visible: dialog.avatarPage
        Flickable {
            id: avatars
            objectName: "profileAvatars"
            x: 29; y: 29; width: 302; height: 280
            clip: true; contentHeight: Math.ceil(avatarChoices.count/3)*84
            boundsBehavior: Flickable.StopAtBounds
            Repeater {
                id: avatarChoices
                model: dialog.backend?dialog.backend.avatars:[]
                delegate: RakButton {
                    id: avatar
                    required property var modelData
                    required property int index
                    objectName: "profileAvatar_"+modelData.key
                    x: 17+(index%3)*95; y: 6+Math.floor(index/3)*84
                    width: 72; height: 72; cornerRadius: 36
                    edgeColor: dialog.selectedAvatar===modelData.key?dialog.c.accent_hex:dialog.c.b12
                    hoverEdgeColor: dialog.selectedAvatar===modelData.key?dialog.c.accent_hex:dialog.c.a70
                    edgeWidth: dialog.selectedAvatar===modelData.key?2:1
                    leftPadding: 4; rightPadding: 4; topPadding: 4; bottomPadding: 4
                    contentItem: RoundedImage { source: avatar.modelData.url; radius: 32 }
                    onClicked: dialog.selectedAvatar=modelData.key
                }
            }
            C.ScrollBar.vertical: RakScrollBar { width: 4; handleColor: dialog.c.b12; minimumSize: Math.min(1,24/Math.max(1,height)) }
        }
        DialogButton { objectName: "profileAvatarCancel"; x: 198; y: 321; width: 73; colors: dialog.c; text: "Cancel"; onClicked: dialog.avatarPage=false }
        DialogButton { objectName: "profileAvatarAccept"; x: 283; y: 321; width: 48; colors: dialog.c; text: "OK"; primary: true; onClicked: { dialog.backend.chooseAvatar(dialog.selectedAvatar); dialog.avatarPage=false } }
    }
}

