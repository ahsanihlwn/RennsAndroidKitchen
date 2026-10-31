// SPDX-License-Identifier: MPL-2.0
import QtQuick
import QtQuick.Window
import QtQuick.Controls as C
import QtWebEngine
import "controls"

Window {
    id: window; objectName: "workspaceCompareWindow"
    required property var workspace
    required property var preferences
    required property var hostWindow
    readonly property var backend: workspace.privateFeatures.compare
    readonly property var c: preferences.colors
    readonly property var s: preferences.strings
    readonly property Item backdropSurface: card
    property var dialogStack: null
    readonly property var managedDialogs: [confirmation,saveDialog,destinationDialog]
    property bool managedClose: true
    property int tab: 0
    width: Math.min(Screen.desktopAvailableWidth*.72,1020); height: Math.min(Screen.desktopAvailableHeight*.72,640)
    color: "transparent"; flags: Qt.Tool|Qt.FramelessWindowHint; transientParent: hostWindow
    visible: backend.visible
    onVisibleChanged: if(visible){x=hostWindow.x+(hostWindow.width-width)/2;y=hostWindow.y+(hostWindow.height-height)/2;requestActivate()}
    onClosing: event => {if(managedClose){event.accepted=false;backend.requestClose()}}
    SmoothRectangle {
        id: card; anchors.fill: parent; radius: 14; color: window.c.BG2; border.width: 1; border.color: window.preferences.panelBorder
        Item {
            width: parent.width; height: 42
            DragHandler { target: null; onActiveChanged: if(active)window.startSystemMove() }
            RakLabel { x: 16; height: parent.height; text: window.backend.nameA+"  ↔  "+window.backend.nameB; font.pixelSize: 13; font.weight: Font.DemiBold; color: window.c.FG }
            RakLabel { anchors.right: close.left; anchors.rightMargin: 10; height: parent.height; text: window.backend.busy?window.s.cmp_scanning:window.s.cmp_done; font.pixelSize: 11; color: window.backend.busy?window.c.FG_MUTED:window.c.GREEN_STATUS }
            RakButton { id: close; anchors.right: parent.right; anchors.rightMargin: 8; y: 7; width: 28; height: 28; leftPadding: 0; rightPadding: 0; text: "✕"; cornerRadius: 6; edgeWidth: 0; labelColor: window.c.FG_MUTED; hoverFillColor: window.c.red18; hoverLabelColor: window.c.RED_LIGHT; onClicked: window.backend.requestClose() }
        }
        SmoothRectangle { y: 42; width: parent.width; height: 1; color: window.c.b08 }
        Row {
            x: 12; y: 47; height: 26; spacing: 6
            AndroidToolButton { colors: window.c; text: window.s.cmp_tab_log; selected: window.tab===0; onClicked: window.tab=0 }
            AndroidToolButton { objectName: "compareDiffTab"; colors: window.c; text: window.s.cmp_tab_diff; selected: window.tab===1; onClicked: window.tab=1 }
        }
        Row {
            x: 16; y: 83; height: 22; spacing: 12
            Repeater {
                model: [1,2,3]
                delegate: Row {
                    required property int modelData; spacing: 6
                    RakLabel { height: 20; text: "Ring "+modelData+"  "+(window.backend.rings[String(modelData)] || ""); font.pixelSize: 11; color: window.backend.rings[String(modelData)]?window.c.GREEN_STATUS:window.c.FG_MUTED }
                    AndroidToolButton { visible: modelData>1 && !window.backend.rings[String(modelData)]; colors: window.c; height: 20; text: window.s.cmp_btn_skip; onClicked: window.backend.skip(modelData) }
                }
            }
        }
        SmoothRectangle { y: 109; width: parent.width; height: 1; color: window.c.b08 }
        ListView {
            id: log; objectName: "compareLog"; x: 10; y: 116; width: parent.width-20; height: parent.height-y-47; clip: true; visible: window.tab===0; model: window.backend.logs
            C.ScrollBar.vertical: RakScrollBar { handleColor: window.c.BG_SCROLL }
            onCountChanged: Qt.callLater(()=>positionViewAtEnd())
            delegate: RakLabel {
                required property var modelData; width: log.width; height: 18; text: modelData.text; font.family: "Consolas"; font.pixelSize: 12; textFormat: Text.PlainText
                color: modelData.hint==="a"?"#7eb8f7":modelData.hint==="b"?"#6dba8a":modelData.hint==="error"?window.c.RED_LIGHT:modelData.hint==="ok"?window.c.GREEN_STATUS:modelData.hint==="dim"?window.c.FG_MUTED:window.c.FG
            }
        }
        Item {
            x: 6; y: 110; width: parent.width-12; height: parent.height-y-41; visible: window.tab===1
            ListView {
                id: differences; objectName: "compareDiffList"; width: 220; height: parent.height; clip: true; model: window.backend.diffs
                C.ScrollBar.vertical: RakScrollBar { handleColor: window.c.BG_SCROLL }
                delegate: RakButton { required property var modelData; required property int index; width: differences.width; height: 32; text: modelData.path; font.pixelSize: 11; leftAligned: true; edgeWidth: 0; cornerRadius: 4; fillColor: window.backend.selected===index?window.c.a18:"transparent"; hoverFillColor: window.c.b05; labelColor: window.c.FG; onClicked: window.backend.select(index) }
            }
            Item {
                x: 226; width: parent.width-x; height: parent.height
                RakLabel { x: 8; width: parent.width-16; height: 32; text: window.backend.selectedName; elide: Text.ElideRight; font.pixelSize: 11; color: window.c.FG }
                Loader {
                    y: 32; width: parent.width; height: parent.height-y; active: window.backend.web.active
                    sourceComponent: WebEngineView {
                        id: diffWeb; objectName: "compareDiffWeb"; backgroundColor: window.c.BG
                        Component.onCompleted: loadHtml(window.backend.web.html)
                        Connections { target: window.backend.web; function onChanged() {diffWeb.loadHtml(window.backend.web.html)} }
                    }
                }
            }
        }
        SmoothRectangle { anchors.bottom: footer.top; width: parent.width; height: 1; color: window.c.b08 }
        Item {
            id: footer; anchors.bottom: parent.bottom; width: parent.width; height: 40
            RakLabel { x: 16; width: parent.width-230; height: parent.height; text: window.backend.status; font.pixelSize: 11; elide: Text.ElideRight; color: window.c.FG_MUTED }
            Row { anchors.right: parent.right; anchors.rightMargin: 12; anchors.verticalCenter: parent.verticalCenter; spacing: 8; DialogButton { objectName: "compareSave"; colors: window.c; text: window.s.cmp_btn_save; enabled: !window.backend.busy; onClicked: window.backend.requestSave() } DialogButton { colors: window.c; text: window.s.cmp_btn_hide; onClicked: window.backend.hide() } }
        }
    }
    MessageWindow { id: confirmation; model: window.preferences; hostWindow: window; commandTarget: window.backend }
    DialogWindow {
        id: saveDialog; objectName: "compareSaveDialog"; model: window.preferences; hostWindow: window
        property string mode: "log"; property string reference: "a"
        implicitWidth: 400; implicitHeight: 280
        Column {
            x: 20; y: 18; width: parent.width-40; spacing: 12
            RakLabel { text: window.s.cmp_save_what; font.pixelSize: 13; font.weight: Font.DemiBold; color: window.c.FG }
            Row { spacing: 6; AndroidToolButton { colors: window.c; selected: saveDialog.mode==="log"; text: window.s.cmp_mode_log; onClicked: saveDialog.mode="log" } AndroidToolButton { colors: window.c; selected: saveDialog.mode==="automation"; text: window.s.cmp_mode_auto; onClicked: saveDialog.mode="automation" } }
            RakLabel { width: parent.width; text: saveDialog.mode==="automation"?window.s.cmp_auto_hint:window.s.cmp_save_log_title; wrapMode: Text.WordWrap; font.pixelSize: 11; color: window.c.FG_MUTED }
            RakLabel { visible: saveDialog.mode==="automation"; text: window.s.cmp_which_new; font.pixelSize: 11; color: window.c.FG }
            Row { visible: saveDialog.mode==="automation"; spacing: 6; AndroidToolButton { colors: window.c; text: "A · "+window.backend.nameA; selected: saveDialog.reference==="a"; onClicked: saveDialog.reference="a" } AndroidToolButton { colors: window.c; text: "B · "+window.backend.nameB; selected: saveDialog.reference==="b"; onClicked: saveDialog.reference="b" } }
            Row { anchors.right: parent.right; spacing: 8; DialogButton { colors: window.c; text: window.s.cmp_btn_cancel; onClicked: saveDialog.dismiss() } DialogButton { objectName: "compareSaveConfirm"; colors: window.c; primary: true; text: window.s.cmp_btn_save; onClicked: {saveDialog.dismiss();if(window.backend.sideA.dual)destinationDialog.open();else window.backend.save(saveDialog.mode,saveDialog.reference,0)} } }
        }
    }
    DialogWindow {
        id: destinationDialog; model: window.preferences; hostWindow: window; implicitWidth: 320; implicitHeight: 172
        Column {
            x: 20; y: 18; width: parent.width-40; spacing: 12
            RakLabel { text: window.s.cmp_choose_save; font.pixelSize: 13; font.weight: Font.DemiBold; color: window.c.FG }
            DialogButton { colors: window.c; width: parent.width; text: workspace.androidTools.trees[0]; onClicked: {destinationDialog.dismiss();window.backend.save(saveDialog.mode,saveDialog.reference,0)} }
            DialogButton { colors: window.c; width: parent.width; text: workspace.androidTools.trees[1] || ""; onClicked: {destinationDialog.dismiss();window.backend.save(saveDialog.mode,saveDialog.reference,1)} }
            DialogButton { colors: window.c; text: window.s.cmp_btn_cancel; onClicked: destinationDialog.dismiss() }
        }
    }
    Connections { target: backend; function onSaveRequested() {saveDialog.open()} function onCloseRequested(data) {confirmation.showMessage(data)} }
}
