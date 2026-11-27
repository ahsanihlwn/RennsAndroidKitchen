// SPDX-License-Identifier: MPL-2.0
import QtQuick
import QtQuick.Controls as C

DialogWindow {
    id: dialog
    property var backend: null
    property color accentColor: c.accent_hex
    objectName: "packagesWindow"
    implicitWidth: 540; implicitHeight: 420; title: s.dep_title
    onPrepareRequested: if (backend) backend.refresh()
    onDismissed: if (backend) backend.close()
    WorkspaceNativeIcon {
        x: 18; y: 18; width: 15; height: 15; iconSize: 15
        iconKey: "package"
        strokeColor: dialog.c.FG
        accentColor: dialog.accentColor
        isLight: (dialog.c && dialog.c.is_light !== undefined) ? dialog.c.is_light : false
    }
    RakLabel { x: 44; y: 16; height: 20; text: dialog.s.dep_title; font.pixelSize: 14; font.weight: Font.DemiBold; color: dialog.c.FG }
    SmoothRectangle { x: 18; y: 48; width: 504; height: 1; color: dialog.c.b07 }
    Flickable {
        id: scroll
        x: 18; y: 61; width: 504; height: 288; clip: true
        contentHeight: list.height; boundsBehavior: Flickable.StopAtBounds
        visible: dialog.backend && !dialog.backend.loading && !dialog.backend.error
        C.ScrollBar.vertical: RakScrollBar { handleColor: dialog.c.b13; hoverColor: dialog.c.a45 }
        Column {
            id: list
            width: scroll.width; spacing: 6
            SmoothRectangle {
                width: list.width; height: rowsColumn.height+12; radius: 10; color: dialog.c.b02; border.color: dialog.c.b07
                Column {
                    id: rowsColumn
                    y: 6; width: parent.width
                    Repeater {
                        model: dialog.backend?dialog.backend.rows:null
                        delegate: Item {
                            id: row
                            required property var modelData
                            required property int index
                            readonly property string packageState: modelData.state
                            readonly property color stateColor: packageState==="installed"?"#7ec699":packageState==="anomaly"?"#e5c07b":packageState==="update_available"?"#61afef":dialog.c.b18
                            readonly property string actionText: modelData.busy?dialog.s.dep_installing:modelData.installError?dialog.s.dep_btn_try_again:packageState==="anomaly"?dialog.s.dep_btn_redownload:packageState==="update_available"?dialog.s.dep_btn_update:dialog.s.dep_btn_install
                            width: rowsColumn.width; height: (modelData.license?84:69)+(index?1:0)
                            SmoothRectangle { visible: row.index>0; width: parent.width; height: 1; color: dialog.c.b05 }
                            Item {
                                y: row.index?1:0; width: parent.width; height: parent.height-y
                                SmoothRectangle { x: 10; y: 8; width: 3; height: parent.height-16; radius: 2; color: row.stateColor }
                                Image { x: 13; y: (parent.height-17)/2; width: 15; height: 15; sourceSize.width: width; sourceSize.height: height; source: dialog.model.icon(row.packageState==="installed"?"check":row.packageState==="anomaly"?"warning":row.packageState==="update_available"?"update":"download","") }
                                Column {
                                    x: 40; y: 8; width: parent.width-action.width-68; spacing: 1
                                    RakLabel { width: parent.width; height: 19; text: row.modelData.display; color: dialog.c.FG; font.pixelSize: 13; font.weight: Font.DemiBold; elide: Text.ElideRight }
                                    RakLabel { width: parent.width; height: 16; text: row.modelData.author||dialog.s.dep_unknown_author; color: dialog.c.FG_DESC; font.pixelSize: 11; elide: Text.ElideRight }
                                    RakLabel { width: parent.width; height: 16; text: "v"+row.modelData.version+" · "+(row.packageState==="installed"?dialog.s.dep_status_installed:row.packageState==="anomaly"?dialog.s.dep_status_anomaly:row.packageState==="update_available"?dialog.s.dep_status_update:dialog.s.dep_status_not_installed); color: dialog.c.FG_MUTED; font.pixelSize: 11; elide: Text.ElideRight }
                                    RakLabel { visible: !!row.modelData.license; height: 14; text: row.modelData.license||""; color: text.indexOf("GPL")===0?"#e5c07b":dialog.c.FG_MUTED; font.pixelSize: 10 }
                                }
                                DialogButton {
                                    id: action
                                    objectName: "packageInstall_"+row.modelData.name
                                    anchors.right: parent.right; anchors.rightMargin: 12; anchors.verticalCenter: parent.verticalCenter
                                    width: Math.max(80,text.length*9); height: 26; cornerRadius: 13
                                    visible: row.packageState!=="installed"; enabled: !row.modelData.busy
                                    text: row.actionText; colors: dialog.c; primary: true; font.pixelSize: 11
                                    onClicked: dialog.backend.install(row.modelData.name)
                                }
                                RakLabel { visible: row.packageState==="installed"; anchors.right: parent.right; anchors.rightMargin: 16; height: parent.height; text: dialog.s.dep_status_installed; font.pixelSize: 11; color: dialog.c.FG_MUTED }
                            }
                        }
                    }
                }
            }
            RakLabel { visible: Boolean(dialog.backend && dialog.backend.showWsl); width: list.width; height: 22; text: dialog.s.dep_wsl_section; font.pixelSize: 10; font.weight: Font.DemiBold; color: dialog.c.FG_DESC }
            SmoothRectangle {
                visible: Boolean(dialog.backend && dialog.backend.showWsl)
                readonly property var info: dialog.backend?dialog.backend.wsl:({checking:true})
                width: list.width; height: Math.max(64,wslText.height+28); radius: 10; color: dialog.c.b02; border.color: dialog.c.b07
                Image { visible: !!(parent.info.installed || parent.info.present); x: 13; y: (parent.height-17)/2; width: 15; height: 15; sourceSize.width: width; sourceSize.height: height; source: visible ? dialog.model.icon(parent.info.installed?"check":"warning","") : "" }
                WorkspaceNativeIcon {
                    visible: !parent.info.installed && !parent.info.present
                    x: 13; y: (parent.height-17)/2; width: 15; height: 15; iconSize: 15
                    iconKey: "package"
                    strokeColor: dialog.c.FG_MUTED
                    accentColor: dialog.accentColor
                    isLight: (dialog.c && dialog.c.is_light !== undefined) ? dialog.c.is_light : false
                }
                Column {
                    id: wslText
                    x: 40; y: 14; width: parent.width-64-(wslAction.visible?wslAction.width+10:0); spacing: 1
                    RakLabel { width: parent.width; height: 19; text: dialog.s.dep_wsl_name; color: dialog.c.FG; font.pixelSize: 13; font.weight: Font.DemiBold }
                    RakLabel { width: parent.width; text: parent.parent.info.checking?dialog.s.dep_wsl_checking:parent.parent.info.detail||(parent.parent.info.installed?dialog.s.dep_wsl_installed:dialog.s.dep_wsl_not_installed); height: Math.max(16,implicitHeight); wrapMode: Text.WordWrap; font.pixelSize: 11; color: dialog.c.FG_MUTED }
                }
                DialogButton { id: wslAction; anchors.right: parent.right; anchors.rightMargin: 12; anchors.verticalCenter: parent.verticalCenter; width: Math.max(80,text.length*9); height: 26; cornerRadius: 13; primary: true; colors: dialog.c; visible: !parent.info.checking && !parent.info.installed; enabled: !parent.info.busy; text: parent.info.busy?dialog.s.dep_installing:dialog.s.dep_btn_install; onClicked: dialog.backend.installWsl() }
            }
        }
    }
    Column {
        x: 18; y: 176; width: 504; spacing: 12
        visible: !dialog.backend || dialog.backend.loading
        Spinner { anchors.horizontalCenter: parent.horizontalCenter; width: 28; height: 28; thickness: 2; inset: 3; accent: Qt.rgba(dialog.accentColor.r,dialog.accentColor.g,dialog.accentColor.b,200/255); trackColor: dialog.c.b07; sweep: -90; rotationDuration: 1152 }
        RakLabel { width: parent.width; height: 18; text: dialog.s.dep_fetching; color: dialog.c.FG_MUTED; font.pixelSize: 12; horizontalAlignment: Text.AlignHCenter }
    }
    Column {
        x: 50; y: 117; width: 440; spacing: 14
        visible: dialog.backend && dialog.backend.error && !dialog.backend.loading
        Image { anchors.horizontalCenter: parent.horizontalCenter; width: 52; height: 52; sourceSize.width: width; sourceSize.height: height; source: dialog.model.icon("not_found","") }
        RakLabel { width: parent.width; text: dialog.s.dep_fetch_error; wrapMode: Text.WordWrap; horizontalAlignment: Text.AlignHCenter; color: dialog.c.FG_MUTED; font.pixelSize: 12 }
        DialogButton { anchors.horizontalCenter: parent.horizontalCenter; width: 100; height: 30; colors: dialog.c; text: dialog.s.dep_btn_retry; onClicked: dialog.backend.refresh() }
    }
    SmoothRectangle { x: 18; y: 361; width: 504; height: 1; color: dialog.c.b07 }
    DialogButton { objectName: "packagesClose"; anchors.right: parent.right; anchors.rightMargin: 18; y: 374; height: 30; colors: dialog.c; text: dialog.s.dep_btn_close; onClicked: dialog.dismiss() }
}
