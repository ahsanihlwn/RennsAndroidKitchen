// SPDX-License-Identifier: MPL-2.0
import QtQuick
import QtQuick.Controls as C
import QtQuick.Dialogs as D
import "controls"
import "pages"

Item {
    id: content
    required property var hostWindow
    readonly property Item backdropSurface: pageSurface
    readonly property var managedDialogs: [licenseDialog, creditDialog, themeDialog, packagesDialog, profileDialog, message, errorLog]
    readonly property bool nativeDialogActive: licenseDialog.presented || creditDialog.presented || themeDialog.presented || packagesDialog.presented || profileDialog.presented || message.presented || errorLog.presented
    function pageItem(name) { return name==="settings" ? settingsPage : name==="create" ? createPage : projectsPage }
    function cancelPageMotion() { pageMotion.cancel() }
    Component.onCompleted: hostWindow.previousPage=hostWindow.uiModel.page

    // ── Left sidebar (layout-only change) ────────────────────────────────────
    // Collapsible sidebar: Projects / Settings nav + drives info.
    // No button styles or themes are modified here.
    AppSidebar {
        id: appSidebar
        objectName: "appSidebar"
        z: 20
        anchors.top: parent.top
        anchors.bottom: parent.bottom
        anchors.left: parent.left
        width: implicitWidth
        model: content.hostWindow.uiModel
        onTrashClicked: projectsPage.toggleTrash()
    }

    SmoothRectangle {
        id: pageSurface
        anchors.left: appSidebar.right
        anchors.right: parent.right
        anchors.top: parent.top
        anchors.bottom: parent.bottom
        color: content.hostWindow.colors.BG
        ProjectsPage { id: projectsPage; anchors.fill: parent; model: content.hostWindow.uiModel; presentationMotion: pageMotion; visible: model.page === "projects" }
        CreatePage { id: createPage; anchors.fill: parent; model: content.hostWindow.uiModel; visible: model.page === "create" }
        SettingsPage { id: settingsPage; anchors.fill: parent; model: content.hostWindow.uiModel; presentationMotion: pageMotion; visible: model.page === "settings" }
    }
    PageMotion { id: pageMotion; anchors.fill: pageSurface; model: content.hostWindow.uiModel; pages: [projectsPage,createPage,settingsPage] }
    SmoothRectangle {
        anchors.fill: parent; z: 30
        color: "#96000000"; opacity: content.hostWindow.nativeDialogActive?1:0; visible: opacity>0
        Behavior on opacity { NumberAnimation { duration: content.hostWindow.uiModel.settings.animations?180:0; easing.type: Easing.OutCubic } }
    }
    LicenseWindow { id: licenseDialog; model: content.hostWindow.uiModel; hostWindow: content.hostWindow }
    CreditWindow { id: creditDialog; model: content.hostWindow.uiModel; hostWindow: content.hostWindow }
    ThemePicker { id: themeDialog; model: content.hostWindow.uiModel; hostWindow: content.hostWindow }
    PackagesWindow { id: packagesDialog; model: content.hostWindow.uiModel; hostWindow: content.hostWindow; backend: content.hostWindow.packageBackend }
    ProfileWindow { id: profileDialog; model: content.hostWindow.uiModel; hostWindow: content.hostWindow; backend: content.hostWindow.profileBackend }
    MessageWindow { id: message; model: content.hostWindow.uiModel; hostWindow: content.hostWindow }
    ErrorLogWindow { id: errorLog; model: content.hostWindow.uiModel; hostWindow: content.hostWindow }
    Item {
        id: overlayHost
        anchors.fill: parent
        CreateProjectPopup { id: createPopup; model: content.hostWindow.uiModel; sourceItem: projectsPage.createButton; backdropSource: pageSurface; onClosed: content.hostWindow.uiModel.dispatch("createPopupClosed", {}) }
        MorphPopup {
            id: projectMenu
            objectName: "projectActionsPopup"
            property var details: ({})
            property var pendingAction: null
            readonly property real optionWidth: {
                let result=0
                for (let i=0;i<menuOptions.count;++i) result=Math.max(result,menuOptions.itemAt(i).implicitWidth)
                return result
            }
            springMotion: false
            backdropSource: pageSurface
            effectsEnabled: content.hostWindow.uiModel.settings.effects!=="low"
            animationsEnabled: content.hostWindow.uiModel.settings.animations
            padding: 0
            width: Math.max(120,optionWidth+8)
            height: menuItems.implicitHeight+8
            panelColor: content.hostWindow.colors.BG_MODAL
            tintBaseColor: content.hostWindow.colors.BG2
            panelBorder: content.hostWindow.uiModel.panelBorder
            panelRadius: 18
            closePolicy: C.Popup.CloseOnEscape | C.Popup.CloseOnPressOutside
            onAboutToShow: pendingAction=null
            onClosed: if (pendingAction) { const action=pendingAction; pendingAction=null; content.hostWindow.uiModel.dispatch("projectAction",action) }
            Column {
                id: menuItems
                x: 4; y: 4; spacing: 0
                Repeater {
                    id: menuOptions
                    model: projectMenu.details.actions || []
                    delegate: Item {
                        id: option
                        required property var modelData
                        required property int index
                        width: projectMenu.width-8; height: index ? 35 : 32
                        implicitWidth: action.implicitWidth
                        SmoothRectangle { visible: option.index>0; width: parent.width; height: 1; color: content.hostWindow.colors.b07 }
                        RakButton {
                        id: action
                        objectName: "projectAction_" + modelData.action
                        y: option.index ? 3 : 0
                        width: parent.width; height: 32; leftAligned: true; leftPadding: 14
                        text: option.modelData.label; enabled: option.modelData.enabled !== false
                        font.pixelSize: 12; cornerRadius: 6
                        labelColor: option.modelData.danger ? content.hostWindow.colors.RED_LIGHT : content.hostWindow.colors.FG
                        hoverFillColor: content.hostWindow.colors.a12
                        animatePress: content.hostWindow.uiModel.settings.animations
                        onClicked: {
                            projectMenu.pendingAction={action: option.modelData.action, path: projectMenu.details.path, id: projectMenu.details.id || ""}
                            projectMenu.close()
                        }
                        }
                    }
                }
            }
        }
        D.FolderDialog {
            id: folder
            property string purpose: ""
            title: content.hostWindow.strings.dlg_choose_folder
            onAccepted: content.hostWindow.uiModel.dispatch("folderSelected", {purpose: purpose, url: selectedFolder.toString()})
        }
    }
    Connections {
        target: content.hostWindow.uiModel
        function onPageChanged() {
            const next=content.hostWindow.uiModel.page
            if (!content.hostWindow.uiModel.startupActive) pageMotion.begin(content.hostWindow.pageItem(content.hostWindow.previousPage),content.hostWindow.pageItem(next))
            content.hostWindow.previousPage=next
        }
        function onSettingsChanged() { if (!content.hostWindow.uiModel.settings.animations) pageMotion.cancel() }
        function onPresentationRequested(command, data) {
            if (command === "startupReady") { pageMotion.begin(null,content.hostWindow.pageItem(content.hostWindow.uiModel.page)); projectsPage.restartDriveCounter(); content.hostWindow.revealSplash() }
            else if (command === "newProject") createPopup.open()
            else if (command === "showDialog") {
                if (data.kind==="license") licenseDialog.open()
                else if (data.kind==="credit") creditDialog.open()
                else if (data.kind==="theme") themeDialog.open()
                else if (data.kind==="packages") packagesDialog.open()
                else if (data.kind==="profile") profileDialog.begin()
            }
            else if (command === "created") createPopup.close()
            else if (command === "projectMenu") {
                projectMenu.details = data
                projectMenu.sourceItem = projectsPage.menuAnchor(data.path)
                projectMenu.x = Math.max(8, Math.min(data.x || 0, overlayHost.width - projectMenu.width - 8))
                projectMenu.y = Math.max(8, Math.min(data.y || 0, overlayHost.height - projectMenu.height - 8))
                projectMenu.open()
            } else if (command === "message") message.showMessage(data)
            else if (command === "errorLog") errorLog.showLog(data.text)
            else if (command === "chooseFolder") { folder.purpose = data.purpose; folder.currentFolder = data.url; folder.open() }
        }
    }
    LoginOverlay { id: login; anchors.fill: parent; z: 90; model: content.hostWindow.uiModel; backend: content.hostWindow.authBackend; backdropSource: pageSurface }
    Connections { target: content.hostWindow.uiModel; function onPresentationRequested(command,data) { if (command==="login") login.open() } }
}
