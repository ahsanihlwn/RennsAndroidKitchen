// SPDX-License-Identifier: MPL-2.0
import QtQuick
import QtQuick.Controls as C

SmoothRectangle {
    id: root
    required property var colors
    property var blocks: []
    signal blockSelected(string address)

    readonly property var layoutData: {
        const list = Array.isArray(blocks) ? blocks : []
        if (!list.length) return { nodes: [], edges: [], width: 400, height: 200 }
        const indexByAddr = {}
        for (let i = 0; i < list.length; ++i) {
            indexByAddr[String(list[i].address || "").toLowerCase()] = i
        }
        const level = new Array(list.length).fill(-1)
        const queue = [0]
        level[0] = 0
        let head = 0
        while (head < queue.length) {
            const u = queue[head++]
            const dests = list[u].destinations || []
            for (let j = 0; j < dests.length; ++j) {
                const v = indexByAddr[String(dests[j]).toLowerCase()]
                if (v !== undefined && level[v] === -1) {
                    level[v] = level[u] + 1
                    queue.push(v)
                }
            }
        }
        let maxLevel = 0
        for (let i = 0; i < list.length; ++i) {
            if (level[i] < 0) level[i] = maxLevel + 1
            if (level[i] > maxLevel) maxLevel = level[i]
        }
        const lanes = {}
        for (let i = 0; i < list.length; ++i) {
            const lv = level[i]
            if (!lanes[lv]) lanes[lv] = []
            lanes[lv].push(i)
        }
        const nodeW = 156, nodeH = 44, gapX = 28, gapY = 42, pad = 20
        let maxCols = 1
        for (const k in lanes) maxCols = Math.max(maxCols, lanes[k].length)
        const totalW = Math.max(420, maxCols * (nodeW + gapX) - gapX + pad * 2)
        const nodes = new Array(list.length)
        for (let lv = 0; lv <= maxLevel; ++lv) {
            const row = lanes[lv] || []
            const rowW = row.length * (nodeW + gapX) - gapX
            const startX = Math.max(pad, Math.floor((totalW - rowW) / 2))
            for (let c = 0; c < row.length; ++c) {
                const idx = row[c]
                nodes[idx] = {
                    address: String(list[idx].address || ""),
                    destCount: (list[idx].destinations || []).length,
                    x: startX + c * (nodeW + gapX),
                    y: pad + lv * (nodeH + gapY),
                    w: nodeW,
                    h: nodeH,
                    level: lv
                }
            }
        }
        const edges = []
        for (let i = 0; i < list.length; ++i) {
            const dests = list[i].destinations || []
            for (let j = 0; j < dests.length; ++j) {
                const v = indexByAddr[String(dests[j]).toLowerCase()]
                if (v === undefined) continue
                edges.push({ from: i, to: v, back: nodes[v].y <= nodes[i].y })
            }
        }
        return {
            nodes: nodes,
            edges: edges,
            width: totalW,
            height: Math.max(240, (maxLevel + 1) * (nodeH + gapY) - gapY + pad * 2)
        }
    }

    onLayoutDataChanged: edgeCanvas.requestPaint()
    onColorsChanged: edgeCanvas.requestPaint()

    radius: 8; color: colors.BG3; border.width: 1; border.color: colors.b10; clip: true

    RakLabel {
        anchors.centerIn: parent
        visible: !blocks || !blocks.length
        width: Math.min(parent.width - 32, 360)
        wrapMode: Text.WordWrap
        horizontalAlignment: Text.AlignHCenter
        color: colors.FG_MUTED
        font.pixelSize: 11
        text: "No basic-block CFG loaded. Run Analyze with Ghidra and open a function to render control-flow blocks."
    }

    Flickable {
        id: scroller
        anchors.fill: parent
        visible: !!(blocks && blocks.length)
        contentWidth: Math.max(width, root.layoutData.width)
        contentHeight: Math.max(height, root.layoutData.height)
        boundsBehavior: Flickable.StopAtBounds
        clip: true
        C.ScrollBar.vertical: RakScrollBar { width: 5; handleColor: root.colors.b30; hoverColor: root.colors.b50 }
        C.ScrollBar.horizontal: RakScrollBar { height: 5; handleColor: root.colors.b30; hoverColor: root.colors.b50 }

        Item {
            width: scroller.contentWidth
            height: scroller.contentHeight

            Canvas {
                id: edgeCanvas
                anchors.fill: parent
                onPaint: {
                    const ctx = getContext("2d")
                    ctx.clearRect(0, 0, width, height)
                    const nodes = root.layoutData.nodes
                    const edges = root.layoutData.edges
                    if (!nodes || !edges) return
                    for (let i = 0; i < edges.length; ++i) {
                        const e = edges[i]
                        const a = nodes[e.from], b = nodes[e.to]
                        if (!a || !b) continue
                        const x1 = a.x + a.w / 2, y1 = a.y + a.h
                        const x2 = b.x + b.w / 2, y2 = b.y
                        ctx.strokeStyle = e.back ? String(root.colors.accent_hex) : String(root.colors.FG_MUTED)
                        ctx.lineWidth = e.back ? 1.5 : 1.2
                        ctx.beginPath()
                        if (e.back) {
                            const sideX = Math.max(a.x + a.w, b.x + b.w) + 22
                            ctx.moveTo(a.x + a.w, a.y + a.h / 2)
                            ctx.bezierCurveTo(sideX, a.y + a.h / 2, sideX, b.y + b.h / 2, b.x + b.w, b.y + b.h / 2)
                        } else {
                            const midY = (y1 + y2) / 2
                            ctx.moveTo(x1, y1)
                            ctx.bezierCurveTo(x1, midY, x2, midY, x2, y2)
                        }
                        ctx.stroke()
                    }
                }
            }

            Repeater {
                model: root.layoutData.nodes
                delegate: SmoothRectangle {
                    required property var modelData
                    required property int index
                    x: modelData.x; y: modelData.y; width: modelData.w; height: modelData.h
                    radius: 6
                    color: blockMouse.containsMouse ? root.colors.BG4 : root.colors.BG2
                    border.width: 1
                    border.color: index === 0 ? root.colors.accent_hex : root.colors.b13
                    RakLabel {
                        x: 10; y: 6; width: parent.width - 20
                        text: "0x" + String(modelData.address).replace(/^0x/i, "")
                        font.family: "Consolas"; font.pixelSize: 11; font.weight: Font.DemiBold
                        color: root.colors.FG_BRIGHT; elide: Text.ElideRight
                    }
                    RakLabel {
                        x: 10; y: 23; width: parent.width - 20
                        text: index === 0 ? "entry · " + modelData.destCount + " edge(s)" : modelData.destCount + " edge(s)"
                        font.pixelSize: 10; color: root.colors.FG_MUTED; elide: Text.ElideRight
                    }
                    MouseArea {
                        id: blockMouse; anchors.fill: parent; hoverEnabled: true
                        onClicked: root.blockSelected("0x" + String(modelData.address).replace(/^0x/i, ""))
                    }
                }
            }
        }
    }
}
