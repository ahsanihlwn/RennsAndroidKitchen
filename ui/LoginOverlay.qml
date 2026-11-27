// SPDX-License-Identifier: MPL-2.0
import QtQuick
import QtQuick.Window
import "controls"

Item {
    id: overlay
    required property var model
    required property var backend
    property Item backdropSource
    readonly property var c: model.colors
    readonly property string mode: backend?backend.mode:"signin"
    property real cardOpacity: 0
    property real cardScale: 1
    property real shadeOpacity: cardOpacity
    property real shakeProgress: 0
    readonly property real shakeOffset: shakeProgress<.25?-20*shakeProgress:shakeProgress<.5?-5+40*(shakeProgress-.25):shakeProgress<.75?5-32*(shakeProgress-.5):-3+12*(shakeProgress-.75)
    visible: false
    objectName: "loginOverlay"
    function open() {
        if (!backend || visible) return
        backend.reset(); email.clear(); password.clear(); user.clear(); role.clear()
        passwordVisible=false; cardOpacity=model.settings.animations?0:1; cardScale=model.settings.animations?0.94:1; visible=true
        focus=true
        if (model.settings.animations) cardReveal.restart()
    }
    function dismiss() { if (visible && !closeMotion.running) closeMotion.start() }
    property bool passwordVisible: false
    MouseArea { anchors.fill: parent; acceptedButtons: Qt.AllButtons; onWheel: wheel=>wheel.accepted=true }
    LiveGlass {
        id: glass; anchors.fill: parent; opacity: overlay.shadeOpacity
        preferences: overlay.model
        visible: overlay.model.settings.effects!=="low"
        backdropSource: overlay.backdropSource; origin: Qt.point(0,0); cornerRadius: 0
        effectsEnabled: overlay.model.settings.effects!=="low"
        flatColor: overlay.c.BG; tintColor: "#80000000"; edgeColor: "transparent"
    }
    SmoothRectangle { anchors.fill: parent; color: "#80000000"; opacity: overlay.shadeOpacity; visible: overlay.model.settings.effects==="low" }
    Item {
        id: container
        x: Math.max(0,(parent.width-420)/2); y: Math.max(0,(parent.height-480)/2)
        width: 420; height: 480; opacity: overlay.cardOpacity; scale: overlay.cardScale; transformOrigin: Item.Center
        SmoothRectangle {
            id: card
            objectName: "loginCard"
            x: 50; y: Math.round((parent.height-height)/2)
            width: 320; height: overlay.mode==="success"?313:form.height+48
            radius: 14; color: overlay.model.settings.effects==="low"?overlay.c.BG_MODAL:Qt.rgba(panel.r,panel.g,panel.b,175/255)
            property color panel: overlay.c.BG2
            border.color: overlay.model.panelBorder
            Column {
                id: form
                x: 26; y: 26; width: 268; spacing: 9
                visible: overlay.mode!=="success"
                RakLabel { width: parent.width; height: 26; text: overlay.mode==="signup"?"Create account":overlay.mode==="local"?"Local mode":"Sign in"; color: overlay.c.FG_BRIGHT; font.pixelSize: 18; font.weight: Font.DemiBold }
                Item { width: parent.width; height: 23
                    RakLabel { width: parent.width; height: 17; text: overlay.mode==="signup"?"Fill in your details below":overlay.mode==="local"?"Enter a username to continue offline":"Enter your credentials to continue"; color: overlay.c.FG_MUTED; font.pixelSize: 12 }
                }
                SmoothRectangle {
                    width: parent.width; height: alertText.implicitHeight+18
                    visible: !!(overlay.backend && overlay.backend.alert)
                    radius: 7; color: overlay.c.red10; border.color: overlay.c.red22
                    RakLabel { id: alertText; x: 11; y: 8; width: parent.width-22; height: implicitHeight; wrapMode: Text.WordWrap; text: overlay.backend?overlay.backend.alert:""; font.pixelSize: 12; color: overlay.c.RED_LIGHT }
                }
                component LoginField: RakField {
                    width: 268; height: 41; cornerRadius: 8
                    leftPadding: 14; rightPadding: 14
                    font.pixelSize: 13; labelColor: overlay.c.FG; hintColor: overlay.c.b20
                    fillColor: overlay.c.b05; edgeColor: overlay.c.b10; focusFillColor: overlay.c.b08; focusEdgeColor: overlay.c.a65
                    selectionColor: overlay.c.a40
                    onTextChanged: if (overlay.backend) overlay.backend.clearAlert()
                    onAccepted: overlay.submit()
                }
                LoginField { id: email; objectName: "loginEmail"; placeholderText: "Email address"; visible: overlay.mode!=="local"; inputMethodHints: Qt.ImhEmailCharactersOnly }
                LoginField {
                    id: password; objectName: "loginPassword"; placeholderText: "Password"; visible: overlay.mode!=="local"
                    echoMode: overlay.passwordVisible?TextInput.Normal:TextInput.Password; rightPadding: 36
                    RakButton { anchors.right: parent.right; anchors.rightMargin: 6; anchors.verticalCenter: parent.verticalCenter; width: 24; height: 24; iconWidth: 16; iconHeight: 16; iconSource: overlay.model.authIcon("eye"); onClicked: overlay.passwordVisible=!overlay.passwordVisible }
                }
                LoginField { id: user; objectName: "loginUsername"; placeholderText: "Username"; visible: overlay.mode!=="signin" }
                LoginField { id: role; objectName: "loginRole"; placeholderText: "Role"; visible: overlay.mode==="signup" }
                Item {
                    width: parent.width; height: 21; visible: overlay.mode==="signin"
                    Link { anchors.right: parent.right; text: "Forgot password?" }
                }
                Item {
                    width: parent.width; height: 43
                RakButton {
                    id: main
                    y: 2
                    objectName: "loginSubmit"
                    x: overlay.shakeOffset; width: parent.width; height: 41; cornerRadius: 8
                    enabled: !!overlay.backend && !overlay.backend.busy
                    text: overlay.backend?(overlay.backend.busy?"    ":"")+overlay.backend.label:"Sign in"
                    fillColor: overlay.c.BTN_PRIMARY_BG || overlay.c.a24; edgeColor: overlay.c.BTN_PRIMARY_EDGE || overlay.c.a45; hoverFillColor: overlay.c.BTN_PRIMARY_HOVER || overlay.c.a35; hoverEdgeColor: overlay.c.BTN_PRIMARY_EDGE_HOVER || overlay.c.a65; downFillColor: overlay.c.BTN_PRIMARY_DOWN || overlay.c.a45
                    disabledFillColor: overlay.c.BTN_PRIMARY_DISABLED_BG || overlay.c.a12; disabledEdgeColor: overlay.c.BTN_PRIMARY_DISABLED_EDGE || overlay.c.a18; disabledLabelColor: overlay.c.BTN_PRIMARY_DISABLED_FG || "#8cffffff"; labelColor: overlay.c.BTN_PRIMARY_FG || "white"; hoverLabelColor: overlay.c.BTN_PRIMARY_FG_HOVER || "white"
                    font.pixelSize: 13; font.weight: Font.DemiBold
                    onClicked: overlay.submit()
                    Spinner { x: 12; anchors.verticalCenter: parent.verticalCenter; width: 16; height: 16; thickness: 3; inset: 3; accent: "white"; visible: !!overlay.backend && overlay.backend.busy }
                }
                }
                Item {
                    width: parent.width; height: 26; visible: overlay.mode==="signin"
                    SmoothRectangle { x: 0; y: 14; height: 1; width: 116; color: overlay.c.b08 }
                    RakLabel { x: 126; y: 6; width: 16; height: 16; text: "OR"; font.pixelSize: 11; color: overlay.c.FG_MUTED }
                    SmoothRectangle { x: 152; y: 14; height: 1; width: 116; color: overlay.c.b08 }
                }
                RakButton {
                    objectName: "loginGoogle"
                    visible: overlay.mode==="signin"; width: parent.width; height: 39; cornerRadius: 8
                    text: "  Continue with Google"; iconSource: overlay.model.authIcon("google"); iconWidth: 16; iconHeight: 16
                    font.pixelSize: 13; font.weight: Font.Medium; labelColor: overlay.c.BTN_SEC_FG || overlay.c.FG; hoverLabelColor: overlay.c.BTN_SEC_FG_HOVER || overlay.c.FG_BRIGHT
                    enabled: !!overlay.backend && !overlay.backend.busy
                    fillColor: overlay.c.BTN_SEC_BG || overlay.c.b04; hoverFillColor: overlay.c.BTN_SEC_HOVER || overlay.c.b08; downFillColor: overlay.c.BTN_SEC_DOWN || overlay.c.b12; edgeColor: overlay.c.BTN_SEC_EDGE || overlay.c.b12; hoverEdgeColor: overlay.c.BTN_SEC_EDGE_HOVER || overlay.c.b20
                    onClicked: overlay.backend.google()
                }
                Column {
                    width: parent.width; spacing: 5
                    Item { width: 1; height: 3 }
                    Link { objectName: "loginSwitch"; anchors.horizontalCenter: parent.horizontalCenter; visible: overlay.mode!=="local"; accent: true; text: overlay.mode==="signin"?"Don't have an account? Sign up":"Already have an account? Sign in"; onClicked: overlay.backend.setMode(overlay.mode==="signin"?"signup":"signin") }
                    Link { objectName: "loginLocal"; anchors.horizontalCenter: parent.horizontalCenter; visible: overlay.mode!=="local"; text: "Continue without account"; onClicked: overlay.backend.setMode("local") }
                    Link { objectName: "loginBack"; anchors.horizontalCenter: parent.horizontalCenter; visible: overlay.mode==="local" && !overlay.backend.offline; accent: true; iconSource: overlay.model.authIcon("arrow"); iconWidth: 12; iconHeight: 12; text: "Back to sign in"; onClicked: overlay.backend.setMode("signin") }
                }
                RakLabel { width: parent.width; height: implicitHeight; visible: overlay.mode==="local" && overlay.backend.offline; text: "No internet detected â€” running in local mode."; wrapMode: Text.WordWrap; horizontalAlignment: Text.AlignHCenter; font.pixelSize: 11; font.italic: true; color: overlay.c.b25 }
            }
            Item {
                x: 26; width: 268; height: 313; visible: overlay.mode==="success"
                SmoothRectangle { x: 106; y: 30; width: 56; height: 56; radius: 28; color: overlay.c.a15; border.color: overlay.c.a40; Image { anchors.centerIn: parent; width: 26; height: 26; source: overlay.model.authIcon("mail") } }
                RakLabel { y: 86; width: parent.width; height: 23; text: "Check your inbox"; horizontalAlignment: Text.AlignHCenter; color: overlay.c.FG_BRIGHT; font.pixelSize: 16; font.weight: Font.DemiBold }
                RakLabel { x: 29; y: 117; width: 210; height: 19; text: "We've sent a verification link to:"; color: overlay.c.FG; font.pixelSize: 13; horizontalAlignment: Text.AlignHCenter }
                SmoothRectangle { anchors.horizontalCenter: parent.horizontalCenter; y: 144; width: Math.min(242,successEmail.implicitWidth+26); height: successEmail.implicitHeight+14; radius: 6; color: overlay.c.a10; border.color: overlay.c.a25; RakLabel { id: successEmail; anchors.centerIn: parent; text: overlay.backend?overlay.backend.successEmail:""; color: overlay.c.FG_BLUE; font.pixelSize: 12; font.weight: Font.Medium } }
                RakLabel { x: 29; y: 183; width: 210; height: 48; text: "Click the link in the email to activate your account, then sign in below."; wrapMode: Text.WordWrap; horizontalAlignment: Text.AlignHCenter; color: overlay.c.FG; font.pixelSize: 13 }
                RakLabel { x: 29; y: 239; width: 210; height: 16; text: "Can't find it? Check your spam folder."; font.italic: true; horizontalAlignment: Text.AlignHCenter; color: overlay.c.FG_MUTED; font.pixelSize: 11 }
                Link { objectName: "loginSuccessBack"; anchors.horizontalCenter: parent.horizontalCenter; y: 269; accent: true; text: "Back to sign in"; iconSource: overlay.model.authIcon("arrow"); iconWidth: 12; iconHeight: 12; onClicked: overlay.backend.setMode("signin") }
            }
        }
    }
    component Link: RakButton {
        property bool accent: false
        implicitWidth: contentItem.implicitWidth+4; height: 21
        leftPadding: 2; rightPadding: 2; font.pixelSize: 12; font.weight: accent?Font.Medium:Font.Normal
        labelColor: accent?overlay.c.FG_BLUE:overlay.c.FG_MUTED; hoverLabelColor: accent?overlay.c.accent_hex:overlay.c.FG
    }
    function submit() { if (backend) backend.submit(email.text,password.text,user.text,role.text) }
    ParallelAnimation {
        id: cardReveal
        NumberAnimation { target: overlay; property: "cardOpacity"; to: 1; duration: overlay.model.settings.animations?190:0; easing.type: Easing.OutQuad }
        NumberAnimation { target: overlay; property: "cardScale"; to: 1; duration: overlay.model.settings.animations?190:0; easing.type: Easing.OutCubic }
    }
    ParallelAnimation {
        id: closeMotion
        NumberAnimation { target: overlay; property: "cardOpacity"; to: 0; duration: overlay.model.settings.animations?140:0; easing.type: Easing.InQuad }
        NumberAnimation { target: overlay; property: "cardScale"; to: 0.94; duration: overlay.model.settings.animations?140:0; easing.type: Easing.InCubic }
        onFinished: { overlay.visible=false; overlay.cardScale=1; password.clear() }
    }
    NumberAnimation { id: shake; target: overlay; property: "shakeProgress"; from: 0; to: 1; duration: overlay.model.settings.animations?220:0; easing.type: Easing.OutQuad; onRunningChanged: if (!running) overlay.shakeProgress=0 }
    Connections {
        target: overlay.backend
        function onDismissRequested() { overlay.dismiss() }
        function onShake(field) {
            shake.stop(); shake.start()
            if (field==="email") email.forceActiveFocus()
            else if (field==="user") user.forceActiveFocus()
        }
    }
}
