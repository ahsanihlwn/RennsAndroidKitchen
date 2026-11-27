// SPDX-License-Identifier: MPL-2.0
import QtQuick
import "controls"
import "pages"

Item {
    id: content
    required property var hostWindow
    readonly property Item backdropSurface: pageSurface
    readonly property var managedDialogs: {
        const list = [message]
        const loaders = [licenseLoader, creditLoader, themeLoader, packagesLoader, profileLoader, errorLogLoader]
        for (let i = 0; i < loaders.length; ++i) {
            if (loaders[i] && loaders[i].item) list.push(loaders[i].item)
        }
        return list
    }
    readonly property bool nativeDialogActive: {
        for (let i = 0; i < managedDialogs.length; ++i) {
            const d = managedDialogs[i]
            if (d && d.presented) return true
        }
        return false
    }
    readonly property real dialogScrimOpacity: {
        let peak = 0
        for (let i = 0; i < managedDialogs.length; ++i) {
            const d = managedDialogs[i]
            if (d && d.visible && d.modal) peak = Math.max(peak, d.opacity)
        }
        return peak
    }
    function ensureCreatePage() {
        if (!createPageLoader.item) {
            createPageLoader.setSource("pages/CreatePage.qml", {
                model: content.hostWindow.uiModel,
                presentationMotion: pageMotion
            })
        }
        return createPageLoader.item
    }
    function pageItem(name) {
        if (name === "settings") return settingsPage
        if (name === "create") return ensureCreatePage()
        return projectsPage
    }
    function cancelPageMotion() { pageMotion.cancel() }
    Component.onCompleted: {
        hostWindow.previousPage = hostWindow.uiModel.page
        ensureCreatePopup()
    }

    // ── Left sidebar (layout-only change) ────────────────────────────────────
    // Collapsible sidebar: Projects / Settings nav + drives info.
    // No button styles or themes are modified here.
    AppSidebar {
        id: appSidebar
        objectName: "appSidebar"
        z: 20
        anchors.top: parent.top; anchors.topMargin: 16
        anchors.bottom: parent.bottom; anchors.bottomMargin: 16
        anchors.left: parent.left; anchors.leftMargin: 16
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
        ProjectsPage {
            id: projectsPage
            anchors.fill: parent
            model: content.hostWindow.uiModel
            presentationMotion: pageMotion
            visible: model.page === "projects" || pageMotion.involves(projectsPage)
            enabled: model.page === "projects"
            z: pageMotion.isExiting(projectsPage) ? 2 : 1
            layer.enabled: pageMotion.isExiting(projectsPage)
            opacity: pageMotion.pageOpacity(projectsPage)
            transform: Translate { y: pageMotion.pageOffsetY(projectsPage) }
        }
        Loader {
            id: createPageLoader
            anchors.fill: parent
            readonly property Item pageObj: item
            visible: content.hostWindow.uiModel.page === "create" || (!!item && pageMotion.involves(item))
            enabled: content.hostWindow.uiModel.page === "create"
            z: (!!item && pageMotion.isExiting(item)) ? 2 : 1
            layer.enabled: !!item && pageMotion.isExiting(item)
            opacity: item ? pageMotion.pageOpacity(item) : 0
            transform: Translate { y: createPageLoader.item ? pageMotion.pageOffsetY(createPageLoader.item) : 0 }
            Component.onCompleted: if (visible && !item) content.ensureCreatePage()
            onVisibleChanged: if (visible && !item) content.ensureCreatePage()
        }
        SettingsPage {
            id: settingsPage
            anchors.fill: parent
            model: content.hostWindow.uiModel
            presentationMotion: pageMotion
            visible: model.page === "settings" || pageMotion.involves(settingsPage)
            enabled: model.page === "settings"
            z: pageMotion.isExiting(settingsPage) ? 2 : 1
            layer.enabled: pageMotion.isExiting(settingsPage)
            opacity: pageMotion.pageOpacity(settingsPage)
            transform: Translate { y: pageMotion.pageOffsetY(settingsPage) }
        }
    }
    readonly property var pageList: createPageLoader.item ? [projectsPage, createPageLoader.item, settingsPage] : [projectsPage, settingsPage]
    PageMotion { id: pageMotion; anchors.fill: pageSurface; model: content.hostWindow.uiModel; pages: content.pageList }
    SmoothRectangle {
        anchors.fill: parent; z: 30
        color: "#96000000"; opacity: content.dialogScrimOpacity; visible: opacity>0.001
    }
    function ensureDialog(loader, qmlPath, props) {
        if (!loader) return null
        if (!loader.item) {
            const merged = Object.assign({model: content.hostWindow.uiModel, hostWindow: content.hostWindow}, props || ({}))
            loader.setSource(qmlPath, merged)
        }
        return loader.item
    }
    function ensureCreatePopup() {
        if (!createPopupLoader.item) {
            createPopupLoader.setSource("controls/CreateProjectPopup.qml", {
                model: content.hostWindow.uiModel,
                sourceItem: projectsPage.createButton,
                backdropSource: pageSurface
            })
        }
        return createPopupLoader.item
    }
    function ensureLoginOverlay() {
        if (!loginOverlayLoader.item) {
            loginOverlayLoader.setSource("LoginOverlay.qml", {
                model: content.hostWindow.uiModel,
                backend: content.hostWindow.authBackend,
                backdropSource: pageSurface
            })
        }
        return loginOverlayLoader.item
    }
    Loader { id: licenseLoader; active: true }
    Loader { id: creditLoader; active: true }
    Loader { id: themeLoader; active: true }
    Loader { id: packagesLoader; active: true }
    Loader { id: profileLoader; active: true }
    MessageWindow { id: message; model: content.hostWindow.uiModel; hostWindow: content.hostWindow }
    Loader { id: errorLogLoader; active: true }
    Item {
        id: overlayHost
        anchors.fill: parent
        Loader { id: createPopupLoader; anchors.fill: parent; active: true }
        Connections {
            target: createPopupLoader.item
            function onClosed() { content.hostWindow.uiModel.dispatch("createPopupClosed", {}) }
        }
        Loader { id: projectMenuLoader; anchors.fill: parent; active: true }
        Loader {
            id: folderDialogLoader
            asynchronous: false
            function openFolder(purpose, currentUrl) {
                if (!item) {
                    setSource(Qt.resolvedUrl("controls/FolderPickerProxy.qml"), {hostWindow: content.hostWindow})
                }
                if (item) {
                    item.purpose = purpose
                    item.currentFolder = currentUrl
                    item.open()
                }
            }
        }
    }
    Connections {
        target: content.hostWindow
        function onContentRevealedChanged() {
            if (content.hostWindow.contentRevealed) {
                appSidebar.beginStartupReveal()
                projectsPage.restartDriveCounter()
            }
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
            if (command === "startupReady") {
                content.hostWindow.revealContent()
            }
            else if (command === "newProject") {
                const p = content.ensureCreatePopup()
                if (p) p.open()
            }
            else if (command === "showDialog") {
                if (data.kind==="license") { const d = content.ensureDialog(licenseLoader, "controls/LicenseWindow.qml"); if (d) d.open() }
                else if (data.kind==="credit") { const d = content.ensureDialog(creditLoader, "controls/CreditWindow.qml"); if (d) d.open() }
                else if (data.kind==="theme") { const d = content.ensureDialog(themeLoader, "controls/ThemePicker.qml"); if (d) d.open() }
                else if (data.kind==="packages") { const d = content.ensureDialog(packagesLoader, "controls/PackagesWindow.qml", {backend: content.hostWindow.packageBackend}); if (d) d.open() }
                else if (data.kind==="profile") { const d = content.ensureDialog(profileLoader, "ProfileWindow.qml", {backend: content.hostWindow.profileBackend}); if (d) d.begin() }
            }
            else if (command === "created") {
                if (createPopupLoader.item) createPopupLoader.item.finishCreate()
            }
            else if (command === "launcherHiding") {
                if (createPopupLoader.item && createPopupLoader.item.visible) createPopupLoader.item.dismissImmediate()
            }
            else if (command === "projectMenu") {
                if (!projectMenuLoader.item) {
                    projectMenuLoader.setSource("controls/ProjectActionsMenu.qml", {hostWindow: content.hostWindow, pageSurface: pageSurface})
                }
                const menu = projectMenuLoader.item
                if (menu) {
                    menu.details = data
                    menu.sourceItem = projectsPage.menuAnchor(data.path)
                    menu.x = Math.max(8, Math.min(data.x || 0, overlayHost.width - menu.width - 8))
                    menu.y = Math.max(8, Math.min(data.y || 0, overlayHost.height - menu.height - 8))
                    menu.open()
                }
            } else if (command === "message") message.showMessage(data)
            else if (command === "errorLog") {
                const e = content.ensureDialog(errorLogLoader, "ErrorLogWindow.qml")
                if (e) e.showLog(data.text, data.fatal)
            }
            else if (command === "chooseFolder") { folderDialogLoader.openFolder(data.purpose, data.url) }
        }
    }
    Loader { id: loginOverlayLoader; anchors.fill: parent; z: 90; active: true }
    Connections {
        target: content.hostWindow.uiModel
        function onPresentationRequested(command,data) {
            if (command==="login") {
                const l = content.ensureLoginOverlay()
                if (l) l.open()
            }
        }
    }
    Connections {
        target: content.hostWindow
        function onVisibleChanged() {
            if (!content.hostWindow.visible && createPopupLoader.item && createPopupLoader.item.visible) {
                createPopupLoader.item.dismissImmediate()
            }
        }
    }
}
