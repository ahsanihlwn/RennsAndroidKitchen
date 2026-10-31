// SPDX-License-Identifier: MPL-2.0
import QtQuick
import QtQuick.Controls as C
import QtQuick.Dialogs as D
import Renns.Workspace

Item {
    id: viewer
    objectName: "workspaceHexViewer"
    required property var workspace
    required property var preferences
    required property var details
    readonly property var c: preferences.colors
    readonly property var s: preferences.strings
    readonly property var analysis: document.analysis
    readonly property var documentModel: document
    property int currentTab: 0
    readonly property string physicalPath: details.documentPath || details.path
    HexDocument { id: document }
    Component.onCompleted: document.load(physicalPath)
    Component.onDestruction: document.close()
    Connections {
        target: document
        function onJumpToHex() {viewer.currentTab=1}
        function onCopyRequested(text) {viewer.workspace.copyText(text)}
        function onMessageRequested(title,text) {viewer.workspace.showMessage(title,text)}
    }
    SmoothRectangle { anchors.fill: parent; color: c.BG3 }
    Flickable {
        id: tabs; x: 0; y: 0; width: parent.width; height: 34; clip: true
        contentWidth: tabRow.width; contentHeight: height; boundsBehavior: Flickable.StopAtBounds
        Row {
            id: tabRow; height: 34
            Repeater {
                model: [viewer.s.so_tab_overview,"Hex Dump",viewer.s.so_tab_strings,"Hashes && Security"]
                delegate: RakButton {
                    required property int index
                    required property string modelData
                    objectName: "hexSubTab_"+index
                    text: modelData.replace("&&",""); height: 34
                    width: Math.ceil(contentItem.implicitWidth)+24; implicitWidth: width
                    font.pixelSize: 11; font.weight: viewer.currentTab===index?Font.DemiBold:Font.Medium
                    edgeWidth: 0; cornerRadius: 0; leftPadding: 12; rightPadding: 12
                    fillColor: viewer.currentTab===index?viewer.c.b02:viewer.c.BG2
                    hoverFillColor: viewer.c.b04; downFillColor: hoverFillColor
                    labelColor: viewer.currentTab===index?viewer.c.FG_BRIGHT:viewer.c.FG_MUTED
                    hoverLabelColor: viewer.c.FG_HOVER
                    onClicked: viewer.currentTab=index
                    SmoothRectangle { y: parent.height-1; width: parent.width; height: 1; color: viewer.c.b08 }
                    SmoothRectangle { visible: viewer.currentTab===index; y: parent.height-2; width: parent.width; height: 2; color: viewer.c.accent_hex }
                }
            }
        }
    }
    Item {
        id: content; y: 34; width: parent.width; height: Math.max(0,parent.height-62)
        Flickable {
            anchors.fill: parent; visible: viewer.currentTab===0; clip: true
            contentWidth: width; contentHeight: overviewCards.height+24; boundsBehavior: Flickable.StopAtBounds
            C.ScrollBar.vertical: RakScrollBar { width: 5; handleColor: viewer.c.b30; hoverColor: viewer.c.b50 }
            Column {
                id: overviewCards; x: 12; y: 12; width: parent.width-24; spacing: 10
                SmoothRectangle {
                    width: parent.width; height: 237; radius: 6; color: viewer.c.BG2; border.width: 1; border.color: viewer.c.b08
                    RakLabel { x: 13; y: 11; text: viewer.s.so_tab_overview; font.pixelSize: 12; font.weight: Font.DemiBold; color: viewer.c.FG_BRIGHT }
                    ViewerTextArea { objectName: "hexOverview"; x: 13; y: 34; width: parent.width-26; height: 192; colors: viewer.c; text: viewer.analysis.overview || ""; wrapText: true }
                }
                SmoothRectangle {
                    width: parent.width; height: Math.max(97,entropyDescription.y+entropyDescription.implicitHeight+11); radius: 6; color: viewer.c.BG2; border.width: 1; border.color: viewer.c.b08
                    RakLabel { x: 13; y: 11; text: "Shannon Entropy & Data Complexity"; font.pixelSize: 12; font.weight: Font.DemiBold; color: viewer.c.FG_BRIGHT }
                    RakLabel { x: 13; y: 34; text: viewer.analysis.entropyLabel?"Shannon Entropy: "+Number(viewer.analysis.entropy).toFixed(2)+" / 8.00 ("+viewer.analysis.entropyLabel+")":"Entropy: Calculating..."; font.pixelSize: 11; color: viewer.c.FG }
                    SmoothRectangle { x: 13; y: 56; width: parent.width-26; height: 8; radius: 3; color: "black"; SmoothRectangle { width: Math.max(4,parent.width*(viewer.analysis.entropy || 0)/8); height: 8; radius: 3; color: viewer.analysis.entropyColor || "#2196f3" } }
                    RakLabel { id: entropyDescription; x: 13; y: 70; width: parent.width-26; text: viewer.analysis.entropyLabel?"Statistical data randomness rating: "+viewer.analysis.entropyLabel+". Indicates machine code density, compression, or waveform table.":"Analyzing data complexity..."; wrapMode: Text.WordWrap; font.pixelSize: 11; color: viewer.c.FG2 }
                }
            }
        }
        Item {
            id: hexPage; anchors.fill: parent; visible: viewer.currentTab===1
            Flickable {
                id: hexTools
                readonly property real lead: pageInfo.width-53
                x: 10; y: 8; width: parent.width-20; height: 30; contentWidth: Math.max(width,875+lead); contentHeight: height; clip: true
                boundsBehavior: Flickable.StopAtBounds
                ViewerToolButton { objectName: "hexPrevPage"; x: 0; y: 3; width: 49; colors: viewer.c; text: "◀ Prev"; enabled: document.page>0; onClicked: document.goPage(document.page-1) }
                RakLabel { id: pageInfo; x: 57; width: Math.ceil(implicitWidth); height: 30; text: "Page "+(document.page+1)+" / "+document.pageCount; color: viewer.c.FG; font.pixelSize: 11; font.weight: Font.DemiBold; verticalAlignment: Text.AlignVCenter }
                ViewerToolButton { objectName: "hexNextPage"; x: 118+hexTools.lead; y: 3; width: 49; colors: viewer.c; text: "Next ▶"; enabled: document.page<document.pageCount-1; onClicked: document.goPage(document.page+1) }
                RakLabel { x: 183+hexTools.lead; height: 30; text: "Page Size:"; color: viewer.c.FG2; font.pixelSize: 11; verticalAlignment: Text.AlignVCenter }
                ViewerCombo {
                    id: pageSize; objectName: "hexPageSize"; x: 246+hexTools.lead; y: 1; width: 118; height: 28; currentIndex: 2
                    colors: viewer.c; textRole: "label"; valueRole: "value"
                    model: [{value:1024,label:"1024 B (1 KB)"},{value:2048,label:"2048 B (2 KB)"},{value:4096,label:"4096 B (4 KB)"},{value:8192,label:"8192 B (8 KB)"},{value:16384,label:"16384 B (16 KB)"}]
                    onActivated: document.setPageSize(currentValue)
                }
                RakLabel { x: 382+hexTools.lead; height: 30; text: "Go to:"; color: viewer.c.FG2; font.pixelSize: 11; verticalAlignment: Text.AlignVCenter }
                ViewerField { id: jump; objectName: "hexJumpInput"; x: 422+hexTools.lead; width: 80; colors: viewer.c; placeholderText: "0x0000"; Keys.onReturnPressed: document.jump(text) }
                ViewerToolButton { objectName: "hexJumpGo"; x: 510+hexTools.lead; y: 1; width: 38; primary: true; colors: viewer.c; text: "Go"; onClicked: document.jump(jump.text) }
                RakLabel { x: hexTools.contentWidth-313; height: 30; text: "Search:"; color: viewer.c.FG2; font.pixelSize: 11; verticalAlignment: Text.AlignVCenter }
                ViewerField { id: search; objectName: "hexSearchInput"; x: hexTools.contentWidth-264; width: 200; colors: viewer.c; placeholderText: "Text or Hex (e.g. 41 4C 50 53)..."; Keys.onReturnPressed: document.find(text,true) }
                ViewerToolButton { objectName: "hexFindPrevious"; x: hexTools.contentWidth-56; y: 3; width: 24; colors: viewer.c; text: "▲"; toolTip: "Find Previous"; onClicked: document.find(search.text,false) }
                ViewerToolButton { objectName: "hexFindNext"; x: hexTools.contentWidth-24; y: 3; width: 24; colors: viewer.c; text: "▼"; toolTip: "Find Next"; onClicked: document.find(search.text,true) }
            }
            RakLabel { x: 18; y: 46; width: parent.width-36; height: 16; text: "Offset     00 01 02 03 04 05 06 07  08 09 0A 0B 0C 0D 0E 0F Decoded Text    "; color: viewer.c.FG_LABEL; font.pixelSize: 11; font.bold: true }
            ViewerTextArea { objectName: "hexDump"; x: 10; y: 70; width: parent.width-20; height: Math.max(1,parent.height-78); colors: viewer.c; text: document.pageText }
        }
        Item {
            anchors.fill: parent; visible: viewer.currentTab===2
            Row {
                id: stringTools; x: 10; y: 8; width: parent.width-20; height: 30; spacing: 8
                RakLabel { id: filterCaption; text: viewer.s.so_search; width: Math.ceil(implicitWidth); height: 30; font.pixelSize: 11; color: viewer.c.FG2; verticalAlignment: Text.AlignVCenter }
                ViewerField { objectName: "hexStringFilter"; width: Math.max(80,stringTools.width-filterCaption.width-minimumCaption.width-82-89-32); colors: viewer.c; placeholderText: "Filter raw strings..."; onTextChanged: document.filterStrings(text) }
                RakLabel { id: minimumCaption; text: "Min Length:"; width: Math.ceil(implicitWidth); height: 30; font.pixelSize: 11; color: viewer.c.FG2; verticalAlignment: Text.AlignVCenter }
                C.SpinBox {
                    objectName: "hexMinimumLength"; width: 82; height: 30; from: 3; to: 32; value: 4; editable: true
                    leftPadding: 12; rightPadding: 32
                    onValueModified: document.setMinimumLength(value)
                    contentItem: TextInput { objectName: "hexMinimumInput"; text: parent.textFromValue(parent.value,parent.locale); font.family: "Poppins"; font.pixelSize: 11; color: viewer.c.FG; horizontalAlignment: Qt.AlignLeft; verticalAlignment: Qt.AlignVCenter; readOnly: !parent.editable; validator: parent.validator; inputMethodHints: Qt.ImhDigitsOnly }
                    background: SmoothRectangle { radius: 4; color: viewer.c.BG_INPUT; border.width: 1; border.color: viewer.c.b10 }
                    up.indicator: RakLabel { objectName: "hexMinimumUp"; x: parent.width-32; width: 16; height: 30; text: "+"; color: viewer.c.FG2; horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter }
                    down.indicator: RakLabel { objectName: "hexMinimumDown"; x: parent.width-16; width: 16; height: 30; text: "−"; color: viewer.c.FG2; horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter }
                }
                ViewerToolButton { objectName: "hexCopyFiltered"; y: 3; width: 89; colors: viewer.c; text: "Copy Filtered"; onClicked: document.copy("strings") }
            }
            SmoothRectangle {
                x: 10; y: 46; width: parent.width-20; height: Math.max(1,parent.height-54); radius: 4; color: viewer.c.BG3; border.width: 1; border.color: viewer.c.b06
                Row {
                    x: 22; y: 1; height: 38
                    Repeater {
                        model: [{title:"Offset (Hex)",width:100},{title:"Encoding",width:83},{title:"Length",width:68},{title:"String Content",width:Math.max(200,stringsList.width-251)}]
                        delegate: SmoothRectangle { required property var modelData; width: modelData.width; height: 38; color: viewer.c.BG2; border.width: 1; border.color: viewer.c.b12; RakLabel { anchors.fill: parent; text: parent.modelData.title; font.pixelSize: 11; font.bold: true; color: viewer.c.FG_LABEL; horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter } }
                    }
                }
                ListView {
                    id: stringsList; objectName: "hexStringsTable"; x: 22; y: 39; width: parent.width-23; height: parent.height-40; clip: true; model: document.rows
                    keyNavigationEnabled: true; currentIndex: -1; focus: true; boundsBehavior: Flickable.StopAtBounds
                    C.ScrollBar.vertical: RakScrollBar { width: 5; handleColor: viewer.c.b30; hoverColor: viewer.c.b50 }
                    delegate: Item {
                        required property var modelData; required property int index
                        width: stringsList.width; height: 30
                        SmoothRectangle { anchors.fill: parent; color: stringsList.currentIndex===parent.index?viewer.c.a20:"transparent" }
                        Repeater {
                            model: [{x:0,w:100,text:modelData.offsetHex,color:viewer.c.FG_BLUE,mono:true},{x:100,w:83,text:modelData.encoding,color:viewer.c.FG_MUTED,mono:false},{x:183,w:68,text:String(modelData.length),color:viewer.c.FG_MUTED,mono:false},{x:251,w:Math.max(100,stringsList.width-251),text:modelData.text,color:viewer.c.FG,mono:true}]
                            delegate: Item { required property var modelData; x: modelData.x; width: modelData.w; height: 30; RakLabel { x: 4; width: parent.width-8; height: parent.height; text: parent.modelData.text; color: parent.modelData.color; font.family: parent.modelData.mono?"Consolas":"Poppins"; font.pixelSize: parent.modelData.mono?13:12; verticalAlignment: Text.AlignVCenter; elide: Text.ElideRight } SmoothRectangle { anchors.right: parent.right; width: 1; height: parent.height; color: viewer.c.b03 } }
                        }
                        SmoothRectangle { y: 29; width: parent.width; height: 1; color: viewer.c.b03 }
                        MouseArea { anchors.fill: parent; onClicked: stringsList.currentIndex=parent.index; onDoubleClicked: document.openString(parent.index) }
                    }
                    Keys.onReturnPressed: document.openString(currentIndex)
                }
            }
        }
        Flickable {
            anchors.fill: parent; visible: viewer.currentTab===3; clip: true
            contentWidth: width; contentHeight: hashCard.height+24; boundsBehavior: Flickable.StopAtBounds
            C.ScrollBar.vertical: RakScrollBar { width: 5; handleColor: viewer.c.b30; hoverColor: viewer.c.b50 }
            SmoothRectangle {
                id: hashCard; x: 12; y: 12; width: parent.width-24; height: 135; radius: 6; color: viewer.c.BG2; border.width: 1; border.color: viewer.c.b08
                RakLabel { x: 13; y: 11; text: "Cryptographic Hashes & Integrity"; font.pixelSize: 12; font.weight: Font.DemiBold; color: viewer.c.FG_BRIGHT }
                Repeater {
                    model: [{key:"md5",title:"MD5"},{key:"sha1",title:"SHA-1"},{key:"sha256",title:"SHA-256"}]
                    delegate: Item {
                        required property var modelData; required property int index
                        x: 13; y: 36+index*32; width: hashCard.width-26; height: 24
                        SmoothRectangle { width: 65; height: 24; radius: 6; color: viewer.c.BG2; border.width: 1; border.color: viewer.c.b08 }
                        SmoothRectangle { x: 73; width: parent.width-128; height: 24; radius: 6; color: viewer.c.BG2; border.width: 1; border.color: viewer.c.b08 }
                        RakLabel { x: 3; width: 62; height: 24; text: parent.modelData.title+":"; color: viewer.c.FG2; font.pixelSize: 11; verticalAlignment: Text.AlignVCenter }
                        RakLabel { x: 76; width: parent.width-134; height: 24; text: viewer.analysis.hashes?viewer.analysis.hashes[parent.modelData.key]:"Calculating..."; color: viewer.c.FG; font.pixelSize: 11; verticalAlignment: Text.AlignVCenter; elide: Text.ElideRight }
                        ViewerToolButton { objectName: "hexCopyHash_"+parent.modelData.key; anchors.right: parent.right; colors: viewer.c; text: "Copy"; onClicked: document.copy(parent.modelData.key) }
                    }
                }
            }
        }
    }
    SmoothRectangle {
        y: parent.height-28; width: parent.width; height: 28; color: viewer.c.BG2
        SmoothRectangle { width: parent.width; height: 1; color: viewer.c.b06 }
        RakLabel { x: 10; height: 28; width: Math.max(0,footerActions.x-22); text: document.status; color: viewer.c.FG3; font.pixelSize: 11; verticalAlignment: Text.AlignVCenter; elide: Text.ElideRight }
        Row {
            id: footerActions; anchors.right: parent.right; anchors.rightMargin: 8; y: 2; spacing: 12
            ViewerToolButton { objectName: "hexCopyHex"; colors: viewer.c; text: "Copy Hex"; onClicked: document.copy("hex") }
            ViewerToolButton { objectName: "hexCopyAscii"; colors: viewer.c; text: "Copy ASCII"; onClicked: document.copy("ascii") }
            ViewerToolButton { objectName: "hexExportStrings"; colors: viewer.c; text: "Export Strings..."; onClicked: if(document.canExport)exportDialog.open() }
            ViewerToolButton { objectName: "hexReload"; colors: viewer.c; text: viewer.s.so_btn_reload; onClicked: document.reload() }
        }
    }
    D.FileDialog { id: exportDialog; objectName: "hexExportDialog"; title: "Export Extracted Strings"; fileMode: D.FileDialog.SaveFile; nameFilters: ["Text files (*.txt)","All files (*)"]; currentFile: document.exportUrl; onAccepted: document.exportStrings(selectedFile.toString()) }
}
