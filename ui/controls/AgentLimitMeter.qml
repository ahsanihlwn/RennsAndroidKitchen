// SPDX-License-Identifier: MPL-2.0
import QtQuick
import QtQuick.Controls as C
import QtQuick.Window

RakButton {
    id: control
    objectName: "agentChatLimitMeter"
    required property var chat
    required property var preferences
    property Item panelItem: null
    readonly property var colors: preferences.colors
    readonly property var tr: chat.labels(preferences.strings.languageKey)
    readonly property var limits: chat.limits || ({})
    readonly property bool loading: chat.limitsLoading
    readonly property string error: chat.limitsError
    readonly property var groups: limits.groups || []
    readonly property bool hasQuota: !!limits.quotaAvailable && groups.length > 0
    readonly property string currentModel: chat.model || ""
    readonly property string currentModelLabel: {
        const m = (chat.models || []).find(x => x.id === currentModel)
        return m ? (m.label || m.id) : currentModel
    }

    // Active Group matching current model
    readonly property var activeGroup: {
        if (!groups || groups.length === 0) return null
        if (groups.length === 1) return groups[0]
        const cur = currentModel.toLowerCase()
        const lbl = currentModelLabel.toLowerCase()
        for (let i = 0; i < groups.length; i++) {
            const g = groups[i]
            const desc = (g.scope || g.description || "").toLowerCase()
            const name = (g.label || g.name || "").toLowerCase()
            if (cur && desc.indexOf(cur) !== -1) return g
            if (lbl && (desc.indexOf(lbl) !== -1 || name.indexOf(lbl) !== -1)) return g
            if ((cur.indexOf("gemini") !== -1 || lbl.indexOf("gemini") !== -1) && (name.indexOf("gemini") !== -1 || desc.indexOf("gemini") !== -1)) return g
            if ((cur.indexOf("claude") !== -1 || cur.indexOf("gpt") !== -1 || lbl.indexOf("claude") !== -1 || lbl.indexOf("gpt") !== -1) &&
                (name.indexOf("claude") !== -1 || name.indexOf("gpt") !== -1 || desc.indexOf("claude") !== -1 || desc.indexOf("gpt") !== -1)) return g
        }
        return groups[0]
    }

    readonly property var activeBuckets: activeGroup ? (activeGroup.buckets || []) : []
    readonly property var fiveHourBucket: {
        if (!activeBuckets || activeBuckets.length === 0) return null
        for (let i = 0; i < activeBuckets.length; i++) {
            const b = activeBuckets[i]
            const win = (b.window || b.label || b.name || "").toLowerCase()
            if (win.indexOf("5h") !== -1 || win.indexOf("5-hour") !== -1 || win.indexOf("five hour") !== -1 || win.indexOf("primary") !== -1 || win.indexOf("hour") !== -1) return b
        }
        return activeBuckets[0]
    }
    readonly property var weeklyBucket: {
        if (!activeBuckets || activeBuckets.length === 0) return null
        for (let i = 0; i < activeBuckets.length; i++) {
            const b = activeBuckets[i]
            const win = (b.window || b.label || b.name || "").toLowerCase()
            if (win.indexOf("week") !== -1 || win.indexOf("7 day") !== -1 || win.indexOf("secondary") !== -1) return b
        }
        return activeBuckets.length > 1 ? activeBuckets[1] : activeBuckets[0]
    }

    readonly property real fiveHourRemaining: fiveHourBucket && fiveHourBucket.remaining !== undefined ? Number(fiveHourBucket.remaining) : -1
    readonly property real weeklyRemaining: weeklyBucket && weeklyBucket.remaining !== undefined ? Number(weeklyBucket.remaining) : -1

    // Strict theme palette helpers (using app's colors)
    function colorFor5h(pct) {
        if (pct < 0) return colors.b14
        if (pct <= 10) return colors.RED_STATUS || "#ef4444"
        return colors.accent_hex || "#4d8ef0"
    }

    function colorForWeekly(pct) {
        if (pct < 0) return colors.b14
        if (pct <= 10) return colors.RED_STATUS || "#ef4444"
        return colors.FG_BLUE || colors.a70 || "#7eb8f7"
    }

    function formatReset(val) {
        if (!val) return ""
        if (val.indexOf("T") !== -1 && val.indexOf("Z") !== -1) {
            try {
                const d = new Date(val)
                const now = new Date()
                const diffMs = d - now
                if (diffMs > 0) {
                    const diffMins = Math.floor(diffMs / 60000)
                    const hours = Math.floor(diffMins / 60)
                    const days = Math.floor(hours / 24)
                    if (days > 0) return tr.quota_days ? tr.quota_days.replace("{n}", days) : (days + " d")
                    if (hours > 0) return tr.quota_hours ? tr.quota_hours.replace("{n}", hours) : (hours + " h")
                    return tr.quota_minutes ? tr.quota_minutes.replace("{n}", Math.max(1, diffMins)) : (diffMins + " m")
                }
            } catch (e) {}
        }
        return String(val)
    }

    width: 24; height: 26; cornerRadius: 7; edgeWidth: 0
    leftPadding: 4; rightPadding: 4
    fillColor: "transparent"; hoverFillColor: colors.b06; downFillColor: colors.b09
    disabledFillColor: "transparent"
    enabled: !chat.busy

    toolTip: {
        if (control.loading) return control.tr.limits_loading || "Checking usage…"
        if (control.hasQuota && control.activeGroup) {
            let tip = (control.activeGroup.label || "Limits") + " · "
            if (control.fiveHourRemaining >= 0) tip += "5h: " + Math.round(control.fiveHourRemaining) + "% "
            if (control.weeklyRemaining >= 0) tip += "Weekly: " + Math.round(control.weeklyRemaining) + "%"
            return tip
        }
        return control.tr.account_limits || "Account limits"
    }

    // Skeleton shimmer/pulse for loading state
    SequentialAnimation on opacity {
        running: control.loading
        loops: Animation.Infinite
        NumberAnimation { to: 0.35; duration: 600; easing.type: Easing.InOutQuad }
        NumberAnimation { to: 0.90; duration: 600; easing.type: Easing.InOutQuad }
    }
    onLoadingChanged: if (!loading) control.opacity = 1.0

    // Circular reciprocal 2-arc gauge
    contentItem: Item {
        implicitWidth: 16; implicitHeight: 16

        Canvas {
            id: meterCanvas
            anchors.centerIn: parent
            width: 18; height: 18
            renderTarget: Canvas.Image

            property bool hasQuotaProp: control.hasQuota
            property real fiveHourProp: control.fiveHourRemaining
            property real weeklyProp: control.weeklyRemaining
            property bool loadingProp: control.loading
            property var colorsProp: control.colors

            onHasQuotaPropChanged: requestPaint()
            onFiveHourPropChanged: requestPaint()
            onWeeklyPropChanged: requestPaint()
            onLoadingPropChanged: requestPaint()
            onColorsPropChanged: requestPaint()
            onAvailableChanged: if (available) requestPaint()
            Component.onCompleted: requestPaint()

            onPaint: {
                const ctx = getContext("2d")
                ctx.reset()
                const cx = width / 2
                const cy = height / 2
                const r = 5.7
                ctx.lineWidth = 2.6
                ctx.lineCap = "round"

                const gap = 0.46 // gap between the two opposite semicircles

                // 1. TOP ARC (5-Hour Limit) - spans top half
                const sTop = Math.PI + gap
                const eTop = 2 * Math.PI - gap
                const spanTop = eTop - sTop

                // Top track (dark background)
                ctx.strokeStyle = control.loading ? (control.colors.b18 || "#33ffffff") : (control.colors.b12 || "#1fffffff")
                ctx.beginPath()
                ctx.arc(cx, cy, r, sTop, eTop)
                ctx.stroke()

                // Top fill
                if (control.hasQuota && control.fiveHourRemaining >= 0 && !control.loading) {
                    ctx.strokeStyle = control.colorFor5h(control.fiveHourRemaining)
                    ctx.beginPath()
                    const fillTop = Math.max(0.04, Math.min(1.0, control.fiveHourRemaining / 100)) * spanTop
                    ctx.arc(cx, cy, r, sTop, sTop + fillTop)
                    ctx.stroke()
                }

                // 2. BOTTOM ARC (Weekly Limit) - spans bottom half ("bolak-balik ngebentuk circle")
                const sBot = gap
                const eBot = Math.PI - gap
                const spanBot = eBot - sBot

                // Bottom track (dark background)
                ctx.strokeStyle = control.loading ? (control.colors.b18 || "#33ffffff") : (control.colors.b12 || "#1fffffff")
                ctx.beginPath()
                ctx.arc(cx, cy, r, sBot, eBot)
                ctx.stroke()

                // Bottom fill
                if (control.hasQuota && control.weeklyRemaining >= 0 && !control.loading) {
                    ctx.strokeStyle = control.colorForWeekly(control.weeklyRemaining)
                    ctx.beginPath()
                    const fillBot = Math.max(0.04, Math.min(1.0, control.weeklyRemaining / 100)) * spanBot
                    ctx.arc(cx, cy, r, sBot, sBot + fillBot)
                    ctx.stroke()
                }
            }
        }
    }

    // Model Change Detection (asynchronous debounced timer, ZERO synchronous recursion)
    property string lastKnownModel: ""
    onCurrentModelChanged: {
        if (lastKnownModel === "") {
            lastKnownModel = currentModel
            return
        }
        if (currentModel !== lastKnownModel) {
            lastKnownModel = currentModel
            modelChangeTimer.restart()
        }
    }

    Timer {
        id: modelChangeTimer
        interval: 1000
        repeat: false
        onTriggered: {
            if (control.chat && control.chat.opened && !control.chat.busy && !control.loading) {
                control.chat.refreshUsage()
            }
        }
    }

    // Turn Finish Detection (refresh usage when assistant stops working)
    property bool lastKnownRunning: false
    readonly property bool isRunning: control.chat ? control.chat.running : false
    onIsRunningChanged: {
        if (lastKnownRunning && !isRunning) {
            turnRefreshTimer.restart()
        }
        lastKnownRunning = isRunning
    }

    Timer {
        id: turnRefreshTimer
        interval: 1500
        repeat: false
        onTriggered: {
            if (control.chat && !control.chat.busy && !control.loading) {
                control.chat.refreshUsage()
            }
        }
    }

    // Delayed initial refresh only when chat panel is opened and limits not yet fetched
    Timer {
        id: initialRefreshTimer
        interval: 800
        repeat: false
        running: !!(control.chat && control.chat.opened)
        onTriggered: {
            if (control.chat && control.chat.opened && !control.chat.busy && !control.loading && !(control.limits && control.limits.source)) {
                control.chat.refreshUsage()
            }
        }
    }

    function positionPopup() {
        const window = control.Window.window
        if (!window) return
        const host = window.contentItem
        const bounds = control.panelItem ? control.panelItem.mapToItem(host, 0, 0, control.panelItem.width, control.panelItem.height) : Qt.rect(0, 0, host.width, host.height)
        const anchor = control.mapToItem(host, 0, 0, control.width, control.height)
        menu.boundsRect = bounds
        menu.anchorRectInHost = anchor
        menu.width = Math.min(300, Math.max(250, bounds.width - 24))
        menu.maxPopupHeight = Math.min(480, bounds.height - 30)
        menu.syncPosition()
    }

    function openDetails() {
        control.resetPressFeedback()
        positionPopup()
        if (chat && !chat.busy && !loading) chat.refreshUsage()
        menu.open()
    }

    onVisibleChanged: if (!visible) menu.dismissImmediate()
    Connections {
        target: control.Window.window
        function onWidthChanged() { menu.dismissImmediate() }
        function onHeightChanged() { menu.dismissImmediate() }
        function onXChanged() { menu.dismissImmediate() }
        function onYChanged() { menu.dismissImmediate() }
    }

    onClicked: openDetails()

    MorphPopup {
        id: menu
        z: 10000
        objectName: "agentChatLimitPopup"
        property rect boundsRect: Qt.rect(0, 0, 400, 600)
        property rect anchorRectInHost: Qt.rect(0, 0, 24, 26)
        property real maxPopupHeight: 480
        function syncPosition() {
            const b = boundsRect
            const a = anchorRectInHost
            x = Math.max(b.x + 8, Math.min(a.x + (a.width - width) / 2, b.x + b.width - width - 8))
            y = Math.max(b.y + 8, Math.min(a.y + a.height + 6, b.y + b.height - height - 8))
        }
        height: Math.max(80, Math.min(body.implicitHeight + 24, maxPopupHeight))
        onHeightChanged: if (visible) syncPosition()
        parent: control.Window.window ? control.Window.window.contentItem : null
        sourceItem: control
        springMotion: false
        morphOnClose: false
        animationsEnabled: control.preferences && control.preferences.settings ? control.preferences.settings.animations : true
        backdropSource: control.Window.window ? control.Window.window.backdropSurface : null
        effectsEnabled: typeof rakPreferences !== "undefined" ? rakPreferences.settings.effects !== "low" : true
        panelColor: control.colors.BG_MODAL
        tintBaseColor: control.colors.BG2
        panelBorder: typeof rakPreferences !== "undefined" ? rakPreferences.panelBorder : control.colors.b10
        panelRadius: 13
        padding: 0
        modal: true
        dim: false
        focus: true
        closePolicy: C.Popup.CloseOnEscape | C.Popup.CloseOnPressOutside

        onAboutToShow: {
            if (control.Window.window && control.Window.window.dialogStack) {
                control.Window.window.dialogStack.registerPopup(menu)
            }
        }
        onClosed: {
            if (control.Window.window && control.Window.window.dialogStack) {
                control.Window.window.dialogStack.unregisterPopup(menu)
            }
        }

        C.ScrollView {
            anchors.fill: parent
            anchors.margins: 12
            clip: true
            contentWidth: availableWidth
            C.ScrollBar.vertical: RakScrollBar {
                width: 3
                handleColor: control.colors.b20
                hoverColor: control.colors.b35
            }

        Column {
            id: body
            width: menu.width - 24
            spacing: 10

            // Header: Title + Provider + Refresh
            Item {
                width: body.width
                height: 28

                Column {
                    anchors.left: parent.left
                    anchors.right: refreshBtn.left
                    anchors.rightMargin: 6
                    anchors.verticalCenter: parent.verticalCenter
                    spacing: 1

                    RakLabel {
                        width: parent.width
                        text: control.tr.account_limits || "Account limits"
                        font.pixelSize: 12
                        font.weight: Font.DemiBold
                        color: control.colors.FG_BRIGHT
                        elide: Text.ElideRight
                    }
                    RakLabel {
                        width: parent.width
                        text: (control.chat.providers.find(p => p.id === control.chat.provider) || {}).label || control.chat.provider
                        font.pixelSize: 10
                        color: control.colors.FG_MUTED
                        elide: Text.ElideRight
                    }
                }

                RakButton {
                    id: refreshBtn
                    anchors.right: parent.right
                    anchors.verticalCenter: parent.verticalCenter
                    width: 24; height: 24; cornerRadius: 6; edgeWidth: 0
                    fillColor: "transparent"; hoverFillColor: control.colors.b06; downFillColor: control.colors.b10
                    toolTip: control.tr.refresh_usage || "Refresh"
                    enabled: !control.chat.busy && !control.loading

                    contentItem: Item {
                        anchors.centerIn: parent
                        width: 14; height: 14
                        Image {
                            id: refreshIcon
                            anchors.centerIn: parent
                            width: 13; height: 13
                            sourceSize: Qt.size(16, 16)
                            source: "image://rak/history?color=" + encodeURIComponent(refreshBtn.visualHovered ? control.colors.FG_BRIGHT : control.colors.FG_MUTED)
                            rotation: control.loading ? spinAnim.currentAngle : 0

                            NumberAnimation on rotation {
                                id: spinAnim
                                property real currentAngle: 0
                                from: 0; to: 360; duration: 800
                                loops: Animation.Infinite
                                running: control.loading
                            }
                        }
                    }
                    onClicked: control.chat.refreshUsage()
                }
            }

            SmoothRectangle {
                width: body.width; height: 1
                color: control.colors.b08
            }

            // Quota Available: Active Group Card
            Column {
                width: body.width
                spacing: 8
                visible: control.hasQuota && !!control.activeGroup

                SmoothRectangle {
                    width: body.width
                    height: activeGroupCol.height + 20
                    radius: 10
                    color: control.colors.b04
                    border.width: 1
                    border.color: control.colors.b08

                    Column {
                        id: activeGroupCol
                        x: 12; y: 10
                        width: parent.width - 24
                        spacing: 10

                        // Active Group Title + Model Badge
                        Item {
                            width: parent.width
                            height: 20

                            RakLabel {
                                anchors.left: parent.left
                                anchors.right: modelBadge.left
                                anchors.rightMargin: 6
                                anchors.verticalCenter: parent.verticalCenter
                                text: control.activeGroup ? (control.activeGroup.label || "Model Quota") : ""
                                font.pixelSize: 11
                                font.weight: Font.DemiBold
                                color: control.colors.FG_BRIGHT
                                elide: Text.ElideRight
                            }

                            SmoothRectangle {
                                id: modelBadge
                                anchors.right: parent.right
                                anchors.verticalCenter: parent.verticalCenter
                                width: badgeLabel.implicitWidth + 10
                                height: 18
                                radius: 4
                                color: control.colors.b08

                                RakLabel {
                                    id: badgeLabel
                                    anchors.centerIn: parent
                                    text: control.currentModelLabel
                                    font.pixelSize: 9
                                    font.weight: Font.Medium
                                    color: control.colors.FG2
                                    elide: Text.ElideRight
                                }
                            }
                        }

                        // 5-Hour Limit Bar
                        Column {
                            width: parent.width
                            spacing: 4
                            visible: control.fiveHourRemaining >= 0

                            Row {
                                width: parent.width
                                RakLabel {
                                    text: control.fiveHourBucket ? (control.fiveHourBucket.name || "5-Hour Limit") : "5-Hour Limit"
                                    font.pixelSize: 10
                                    font.weight: Font.Medium
                                    color: control.colors.FG
                                }
                                Item { width: Math.max(0, parent.width - childrenRect.width); height: 1 }
                                RakLabel {
                                    text: Math.round(control.fiveHourRemaining) + "%"
                                    font.pixelSize: 10
                                    font.weight: Font.DemiBold
                                    color: control.colorFor5h(control.fiveHourRemaining)
                                }
                            }

                            SmoothRectangle {
                                width: parent.width; height: 5; radius: 2.5
                                color: control.colors.b08
                                SmoothRectangle {
                                    width: Math.max(control.fiveHourRemaining > 0 ? 5 : 0, parent.width * Math.min(1.0, control.fiveHourRemaining / 100))
                                    height: parent.height; radius: 2.5
                                    color: control.colorFor5h(control.fiveHourRemaining)
                                    Behavior on width { enabled: !!(control.preferences && control.preferences.settings && control.preferences.settings.animations); NumberAnimation { duration: 250; easing.type: Easing.OutCubic } }
                                }
                            }

                            RakLabel {
                                visible: !!control.fiveHourBucket && !!control.fiveHourBucket.reset
                                text: (control.tr.reset || "Reset") + ": " + control.formatReset(control.fiveHourBucket ? control.fiveHourBucket.reset : "")
                                font.pixelSize: 9
                                color: control.colors.FG_MUTED
                            }
                        }

                        // Weekly Limit Bar
                        Column {
                            width: parent.width
                            spacing: 4
                            visible: control.weeklyRemaining >= 0

                            Row {
                                width: parent.width
                                RakLabel {
                                    text: control.weeklyBucket ? (control.weeklyBucket.name || "Weekly Limit") : "Weekly Limit"
                                    font.pixelSize: 10
                                    font.weight: Font.Medium
                                    color: control.colors.FG
                                }
                                Item { width: Math.max(0, parent.width - childrenRect.width); height: 1 }
                                RakLabel {
                                    text: Math.round(control.weeklyRemaining) + "%"
                                    font.pixelSize: 10
                                    font.weight: Font.DemiBold
                                    color: control.colorForWeekly(control.weeklyRemaining)
                                }
                            }

                            SmoothRectangle {
                                width: parent.width; height: 5; radius: 2.5
                                color: control.colors.b08
                                SmoothRectangle {
                                    width: Math.max(control.weeklyRemaining > 0 ? 5 : 0, parent.width * Math.min(1.0, control.weeklyRemaining / 100))
                                    height: parent.height; radius: 2.5
                                    color: control.colorForWeekly(control.weeklyRemaining)
                                    Behavior on width { enabled: !!(control.preferences && control.preferences.settings && control.preferences.settings.animations); NumberAnimation { duration: 250; easing.type: Easing.OutCubic } }
                                }
                            }

                            RakLabel {
                                visible: !!control.weeklyBucket && !!control.weeklyBucket.reset
                                text: (control.tr.reset || "Reset") + ": " + control.formatReset(control.weeklyBucket ? control.weeklyBucket.reset : "")
                                font.pixelSize: 9
                                color: control.colors.FG_MUTED
                            }
                        }
                    }
                }

                // Other Model Groups (Specifically for AGY)
                Column {
                    width: body.width
                    spacing: 6
                    visible: control.groups.length > 1

                    Item {
                        width: parent.width; height: 18
                        RakLabel {
                            anchors.left: parent.left; anchors.verticalCenter: parent.verticalCenter
                            text: "Grup Model Lainnya"
                            font.pixelSize: 10
                            font.weight: Font.Medium
                            color: control.colors.FG_MUTED
                        }
                    }

                    Repeater {
                        model: control.groups.filter(g => g !== control.activeGroup)
                        delegate: SmoothRectangle {
                            id: otherGroupCard
                            required property var modelData
                            width: body.width
                            height: otherGroupCol.height + 16
                            radius: 9
                            color: control.colors.b03
                            border.width: 1
                            border.color: control.colors.b06

                            Column {
                                id: otherGroupCol
                                x: 10; y: 8
                                width: parent.width - 20
                                spacing: 8

                                Column {
                                    width: parent.width; spacing: 1
                                    RakLabel {
                                        width: parent.width
                                        text: otherGroupCard.modelData.label || ""
                                        font.pixelSize: 10
                                        font.weight: Font.DemiBold
                                        color: control.colors.FG2
                                        elide: Text.ElideRight
                                    }
                                    RakLabel {
                                        width: parent.width
                                        text: otherGroupCard.modelData.scope || ""
                                        font.pixelSize: 9
                                        color: control.colors.FG_MUTED
                                        elide: Text.ElideRight
                                    }
                                }

                                Repeater {
                                    model: otherGroupCard.modelData.buckets || []
                                    delegate: Column {
                                        required property var modelData
                                        width: otherGroupCol.width
                                        spacing: 3

                                        Row {
                                            width: parent.width
                                            RakLabel {
                                                text: modelData.name || modelData.label || "Limit"
                                                font.pixelSize: 9
                                                color: control.colors.FG_MUTED
                                            }
                                            Item { width: Math.max(0, parent.width - childrenRect.width); height: 1 }
                                            RakLabel {
                                                text: Math.round(Number(modelData.remaining || 0)) + "%"
                                                font.pixelSize: 9
                                                font.weight: Font.DemiBold
                                                color: modelData.window === "5h" ? control.colorFor5h(Number(modelData.remaining || 0)) : control.colorForWeekly(Number(modelData.remaining || 0))
                                            }
                                        }

                                        SmoothRectangle {
                                            width: parent.width; height: 4; radius: 2
                                            color: control.colors.b08
                                            SmoothRectangle {
                                                width: Math.max(Number(modelData.remaining || 0) > 0 ? 4 : 0, parent.width * Math.min(1.0, Number(modelData.remaining || 0) / 100))
                                                height: parent.height; radius: 2
                                                color: modelData.window === "5h" ? control.colorFor5h(Number(modelData.remaining || 0)) : control.colorForWeekly(Number(modelData.remaining || 0))
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }

            // Quota Not Available or Fallback / Report Viewer
            Column {
                width: body.width
                spacing: 8
                visible: !control.hasQuota

                RakLabel {
                    width: parent.width
                    text: control.error || control.tr.limits_unavailable || "Quota is not reported by this backend."
                    font.pixelSize: 11
                    color: control.colors.FG_MUTED
                    wrapMode: Text.WordWrap
                }

                C.ScrollView {
                    width: parent.width
                    height: Math.min(160, reportText.implicitHeight)
                    clip: true
                    visible: !!(control.limits && control.limits.report)

                    C.ScrollBar.vertical: RakScrollBar {
                        width: 3
                        handleColor: control.colors.b20
                        hoverColor: control.colors.b35
                    }

                    C.TextArea {
                        id: reportText
                        text: (control.limits && control.limits.report) || ""
                        font.family: "Consolas"
                        font.pixelSize: 10
                        color: control.colors.FG2
                        wrapMode: TextEdit.Wrap
                        readOnly: true
                        selectByMouse: true
                        background: null
                        padding: 6
                    }
                }
            }
        }
        }
    }
}
