// SPDX-License-Identifier: MPL-2.0
import QtQuick
import QtQuick.Controls as C

DialogWindow {
    id: dialog
    objectName: "licenseWindow"
    readonly property var docs: dialog.model.licenseDocuments || []
    property int currentIndex: 0
    readonly property var activeDoc: docs.length > currentIndex ? docs[currentIndex] : ({name: "", meta: "", summary: "", blocks: []})
    implicitWidth: 700
    implicitHeight: 520
    title: s.lic_title
    onPrepareRequested: {
        currentIndex = 0
        masterWheel.reset()
        licenseWheel.reset()
        licenseList.positionViewAtBeginning()
    }
    onCurrentIndexChanged: {
        licenseWheel.reset()
        licenseList.positionViewAtBeginning()
    }

    RakLabel {
        x: 24; y: 20; width: parent.width - 48; height: 24
        text: dialog.s.lic_title
        font.pixelSize: 16; font.weight: Font.DemiBold; color: dialog.c.FG
    }

    // Left column: Master component list
    ListView {
        id: masterList
        x: 24; y: 58
        width: 200
        height: parent.height - 114
        spacing: 4
        clip: true
        boundsBehavior: Flickable.StopAtBounds
        flickDeceleration: 3800
        maximumFlickVelocity: 9000
        model: dialog.docs
        WorkspaceTreeWheel { id: masterWheel; objectName: "licenseMasterWheel"; view: masterList; useFlickPhysics: true; animationsEnabled: dialog.model ? dialog.model.settings.animations : true }
        MouseArea { anchors.fill: parent; z: 5; acceptedButtons: Qt.NoButton; onWheel: wheel => masterWheel.handle(wheel) }
        delegate: RakButton {
            required property var modelData
            required property int index
            readonly property bool selected: dialog.currentIndex === index
            objectName: "licenseItem_" + index
            width: masterList.width
            height: 50
            cornerRadius: 8
            edgeWidth: 0
            fillColor: selected ? dialog.c.a14 : "transparent"
            hoverFillColor: selected ? dialog.c.a18 : dialog.c.b05
            downFillColor: dialog.c.a20
            onClicked: dialog.currentIndex = index
            contentItem: Item {
                anchors.fill: parent
                RakLabel {
                    x: 12; y: 7; width: parent.width - 24; height: 18
                    text: modelData.name || ""
                    font.pixelSize: 12
                    font.weight: selected ? Font.DemiBold : Font.Medium
                    color: selected ? dialog.c.FG_BRIGHT : dialog.c.FG
                    elide: Text.ElideRight
                }
                RakLabel {
                    x: 12; y: 26; width: parent.width - 24; height: 16
                    text: modelData.meta || ""
                    font.pixelSize: 11
                    color: selected ? dialog.c.FG_BLUE : dialog.c.FG_MUTED
                    elide: Text.ElideRight
                }
            }
        }
    }

    // Thin vertical divider between master and detail columns
    SmoothRectangle {
        x: 235; y: 58; width: 1; height: parent.height - 114
        color: dialog.c.b07
    }

    // Right column: Optional extra module list + verbatim license text reader
    Item {
        x: 248; y: 58
        width: parent.width - 272
        height: parent.height - 114

        RakLabel {
            id: docSummary
            visible: (dialog.activeDoc.summary || "").length > 0
            width: parent.width
            wrapMode: Text.WordWrap
            text: dialog.activeDoc.summary || ""
            font.pixelSize: 11
            color: dialog.c.FG_BLUE_MUTED
        }

        ListView {
            id: licenseList
            y: docSummary.visible ? docSummary.implicitHeight + 10 : 0
            width: parent.width
            height: parent.height - y
            clip: true
            boundsBehavior: Flickable.StopAtBounds
            cacheBuffer: 1200
            flickDeceleration: 3800
            maximumFlickVelocity: 9000
            model: dialog.activeDoc.blocks || []
            WorkspaceTreeWheel { id: licenseWheel; objectName: "licenseDetailWheel"; view: licenseList; useFlickPhysics: true; animationsEnabled: dialog.model ? dialog.model.settings.animations : true }
            MouseArea { anchors.fill: parent; z: 5; acceptedButtons: Qt.NoButton; onWheel: wheel => licenseWheel.handle(wheel) }
            C.ScrollBar.vertical: RakScrollBar {
                handleColor: dialog.c.b20; hoverColor: dialog.c.b35
                autoHide: false
                onPressedChanged: if (pressed) licenseWheel.reset()
            }
            delegate: Item {
                required property var modelData
                readonly property string kind: modelData.kind || "p"
                readonly property real topGap: kind === "h1" ? 2 : (kind === "h2" ? 13 : (kind === "h3" ? 8 : 0))
                readonly property real botGap: kind === "h1" ? 6 : (kind === "h2" ? 4 : (kind === "h3" ? 2 : 7))
                width: licenseList.width - 12
                height: blockText.implicitHeight + topGap + botGap
                TextEdit {
                    id: blockText
                    y: topGap
                    width: parent.width
                    readOnly: true
                    selectByMouse: true
                    wrapMode: TextEdit.Wrap
                    text: modelData.text || ""
                    selectionColor: dialog.c.a30
                    selectedTextColor: dialog.c.FG_BRIGHT
                    font.family: "Poppins"
                    font.pixelSize: kind === "h1" ? 15 : (kind === "h2" ? 13 : 12)
                    font.weight: kind === "h1" ? Font.Bold : ((kind === "h2" || kind === "h3") ? Font.DemiBold : Font.Normal)
                    color: kind === "h1" ? dialog.c.FG_BLUE
                         : kind === "h2" ? dialog.c.FG_BRIGHT
                         : kind === "h3" ? dialog.c.FG
                         : dialog.c.FG_MUTED
                }
            }
        }
    }

    DialogButton {
        objectName: "licenseClose"
        x: parent.width - 104; y: parent.height - 46; width: 80; height: 30
        colors: dialog.c; text: dialog.s.lic_btn_close
        onClicked: dialog.dismiss()
    }
}
