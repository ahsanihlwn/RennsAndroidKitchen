// SPDX-License-Identifier: MPL-2.0
import QtQuick
import QtQuick.Controls as C
import QtQuick.Dialogs as D
import QtQuick.Window
import Renns.Workspace
import ".."

Item {
    id: viewer
    objectName: "workspaceSoViewer"
    required property var workspace
    required property var preferences
    required property var details
    readonly property var c: preferences.colors
    readonly property var s: preferences.strings
    readonly property var documentModel: document
    readonly property var inspectionWindow: embedded
    property alias currentTab: tabs.currentIndex
    SoDocument { id: document }
    Component.onCompleted: document.load(details.documentPath || details.path)
    Component.onDestruction: {document.close();embedded.close();pathDialog.close();nested.close();compareDialog.close()}
    Connections {
        target: document
        function onCopyRequested(text) {viewer.workspace.copyText(text)}
        function onMessageRequested(title,text) {
            if(embedded.visible)embeddedMessage.showMessage({title:title,text:text})
            else viewer.workspace.showMessage(title,text)
        }
        function onEmbeddedRequested() {embedded.open()}
        function onPathRequested(path) {pathDialog.fullPath=path;pathDialog.open()}
        function onNestedRequested(text) {nested.analysisText=text;nested.open()}
        function onCompareRequested() {compareDialog.open()}
    }
    SmoothRectangle { anchors.fill: parent; color: viewer.c.BG3 }
    SmoothRectangle {
        width: parent.width; height: 64; color: viewer.c.BG2
        RakLabel { x: 16; y: 10; text: "Binary Lab"; font.pixelSize: 16; font.weight: Font.DemiBold; color: viewer.c.FG_BRIGHT }
        RakLabel { x: 16; y: 37; width: parent.width-32; text: document.ready?document.summary.name+"  ·  "+document.summary.format+"  ·  "+document.summary.architecture+" / "+document.summary.bits+"-bit  ·  "+document.summary.size:document.status; font.pixelSize: 11; color: viewer.c.FG_MUTED; elide: Text.ElideMiddle }
        SmoothRectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: viewer.c.b08 }
    }
    SmoothRectangle {
        id: tabs; y: 64; width: viewer.width<800?124:154; height: Math.max(0,viewer.height-92); color: viewer.c.BG2
        property int currentIndex: 0
        readonly property var titles: [viewer.s.so_tab_overview,viewer.s.so_tab_symbols,viewer.s.so_tab_paths,viewer.s.so_tab_sections,viewer.s.so_tab_segments,viewer.s.so_tab_relocations,viewer.s.so_tab_security,viewer.s.so_tab_disasm,viewer.s.so_tab_strings,"Functions"]
        ListView {
            x: 6; y: 10; width: parent.width-12; height: parent.height-52; clip: true; model: tabs.titles; spacing: 3
            C.ScrollBar.vertical: RakScrollBar { width: 4; handleColor: viewer.c.b30; hoverColor: viewer.c.b50 }
            delegate: RakButton {
                required property string modelData; required property int index
                objectName: "soSubTab_"+index; width: ListView.view.width; height: 33; text: modelData.replace(/&/g,""); font.pixelSize: 11
                leftPadding: 12; edgeWidth: 0; cornerRadius: 6; fillColor: tabs.currentIndex===index?viewer.c.a20:"transparent"; hoverFillColor: viewer.c.b04; downFillColor: viewer.c.b08
                labelColor: tabs.currentIndex===index?viewer.c.FG_BRIGHT:viewer.c.FG_MUTED; hoverLabelColor: viewer.c.FG_BRIGHT
                onClicked: tabs.currentIndex=index
                SmoothRectangle { visible: tabs.currentIndex===parent.index; x: 0; y: 9; width: 2; height: 15; radius: 1; color: viewer.c.accent_hex }
            }
        }
        RakLabel { x: 12; y: parent.height-36; width: parent.width-24; height: 30; text: document.summary.engine || ""; color: viewer.c.FG_MUTED; font.pixelSize: 9; wrapMode: Text.WordWrap }
        SmoothRectangle { anchors.right: parent.right; width: 1; height: parent.height; color: viewer.c.b08 }
    }
    Item {
        id: content; x: tabs.width; y: 64; width: Math.max(0,parent.width-x); height: Math.max(0,parent.height-92)
        Flickable {
            anchors.fill: parent; visible: viewer.currentTab===0; clip: true; contentWidth: width; contentHeight: overviewCard.y+overviewCard.height+12; boundsBehavior: Flickable.StopAtBounds
            C.ScrollBar.vertical: RakScrollBar { width: 5; handleColor: viewer.c.b30; hoverColor: viewer.c.b50 }
            Row {
                x: 12; y: 12; width: parent.width-24; spacing: 8
                Repeater { model: [{label:viewer.s.so_tab_sections,value:document.summary.sections || 0},{label:"Functions",value:document.summary.functions || 0},{label:viewer.s.so_tab_strings,value:document.summary.strings || 0}]
                    delegate: SmoothRectangle { required property var modelData; width: (parent.width-16)/3; height: 72; radius: 8; color: viewer.c.BG2; border.width: 1; border.color: viewer.c.b08
                        RakLabel { x: 12; y: 10; text: String(parent.modelData.value); font.pixelSize: 21; font.weight: Font.DemiBold; color: viewer.c.FG_BRIGHT }
                        RakLabel { x: 12; y: 44; width: parent.width-24; text: parent.modelData.label; font.pixelSize: 10; color: viewer.c.FG_MUTED; elide: Text.ElideRight }
                    }
                }
            }
            SmoothRectangle {
                id: overviewCard; x: 12; y: 96; width: parent.width-24; height: Math.max(360,content.height-108); radius: 8; color: viewer.c.BG2; border.width: 1; border.color: viewer.c.b08
                RakLabel { x: 13; y: 11; text: viewer.s.so_tab_overview; font.pixelSize: 12; font.weight: Font.DemiBold; color: viewer.c.FG_BRIGHT }
                ViewerTextArea { objectName: "soOverview"; x: 13; y: 34; width: parent.width-26; height: parent.height-48; colors: viewer.c; text: document.ready ? (document.overview+"\n\nSHA-256  "+(document.summary.sha256 || "")+(document.summary.warnings?"\n\n"+document.summary.warnings:"")) : document.overview; wrapText: true }
            }
        }
        Item {
            anchors.fill: parent; visible: viewer.currentTab===1
            Row {
                id: symbolTools; x: 10; y: 10; width: parent.width-20; height: 30; spacing: 6
                RakLabel { id: symbolCaption; text: viewer.s.so_search; width: Math.ceil(implicitWidth); height: 30; color: viewer.c.FG2; font.pixelSize: 11; verticalAlignment: Text.AlignVCenter }
                ViewerField { id: symbolFilter; objectName: "soSymbolFilter"; colors: viewer.c; width: Math.max(40,symbolTools.width-symbolCaption.width-kindCaption.width-kind.width-mangled.width-symbolCount.width-30); placeholderText: viewer.s.so_filter_symbols; onTextChanged: viewer.refreshSymbols() }
                RakLabel { id: kindCaption; text: viewer.s.so_kind; width: Math.ceil(implicitWidth); height: 30; color: viewer.c.FG2; font.pixelSize: 11; verticalAlignment: Text.AlignVCenter }
                ViewerCombo { id: kind; objectName: "soSymbolKind"; y: 1; colors: viewer.c; model: [viewer.s.so_kind_all,"export","import","local"]; onActivated: viewer.refreshSymbols() }
                RakButton {
                    id: mangled; objectName: "soMangled"; y: 3; height: 24; implicitHeight: 24
                    checkable: true; leftPadding: 3; rightPadding: 6; cornerRadius: 5; edgeWidth: 0
                    fillColor: "transparent"; hoverFillColor: viewer.c.b05; downFillColor: viewer.c.b09
                    implicitWidth: mangledContent.implicitWidth + leftPadding + rightPadding
                    onToggled: viewer.refreshSymbols()
                    contentItem: Row {
                        id: mangledContent; spacing: 6
                        SmoothRectangle {
                            anchors.verticalCenter: parent.verticalCenter
                            width: 16; height: 16; radius: 4
                            color: mangled.checked ? viewer.c.a20 : "transparent"
                            border.width: 1; border.color: mangled.checked ? viewer.c.accent_hex : viewer.c.FG_MUTED
                            RakLabel { anchors.centerIn: parent; text: "✓"; color: viewer.c.accent_hex; visible: mangled.checked; font.pixelSize: 12; transformed: mangled.transformed }
                        }
                        RakLabel { anchors.verticalCenter: parent.verticalCenter; text: viewer.s.so_mangled; font.pixelSize: 11; color: mangled.visualHovered ? viewer.c.FG : viewer.c.FG_MUTED; transformed: mangled.transformed }
                    }
                }
                RakLabel { id: symbolCount; text: document.symbolCount; width: Math.ceil(implicitWidth); height: 30; font.pixelSize: 11; color: viewer.c.FG_MUTED; verticalAlignment: Text.AlignVCenter }
            }
            ViewerTable {
                objectName: "soSymbolsTable"; x: 10; y: 46; width: parent.width-20; height: Math.max(1,parent.height-56); colors: viewer.c; rows: document.symbols; gutter: 33
                columns: mangled.checked?[{title:viewer.s.so_col_kind},{title:viewer.s.so_col_name,stretch:true},{title:viewer.s.so_col_mangled,stretch:true},{title:viewer.s.so_col_value},{title:viewer.s.so_col_size}]:[{title:viewer.s.so_col_kind},{title:viewer.s.so_col_name,stretch:true},{title:viewer.s.so_col_value},{title:viewer.s.so_col_size}]
            }
        }
        Item {
            anchors.fill: parent; visible: viewer.currentTab===2
            Row {
                id: pathTools; x: 10; y: 10; width: parent.width-20; height: 30; spacing: 8
                RakLabel { id: pathCaption; text: viewer.s.so_search; width: Math.ceil(implicitWidth); height: 30; color: viewer.c.FG2; font.pixelSize: 11; verticalAlignment: Text.AlignVCenter }
                ViewerField { id: pathFilter; objectName: "soPathFilter"; colors: viewer.c; width: Math.max(120, pathTools.width - pathCaption.width - 220); placeholderText: viewer.s.so_filter_path; onTextChanged: document.filterPaths(text) }
                ViewerToolButton {
                    objectName: "soReconstructTree"
                    colors: viewer.c
                    primary: true
                    text: document.busy ? "Reconstructing…" : "Reconstruct Source Tree"
                    enabled: document.ready && !document.busy
                    onClicked: reconstructFolderDialog.open()
                }
            }
            SmoothRectangle {
                id: pathBox; x: 10; y: 46; width: parent.width-20; height: Math.max(1,parent.height-56); radius: 10; color: viewer.c.BG3; border.width: 1; border.color: viewer.c.b13; clip: true
                readonly property real kindWidth: 126
                readonly property real sizeWidth: 84
                readonly property real firstWidth: Math.max(160,width-6-kindWidth-sizeWidth)
                readonly property color accentColor: viewer.c.accent_hex
                readonly property color selectedColor: Qt.rgba(accentColor.r,accentColor.g,accentColor.b,41/255)
                Item {
                    id: pathHeader; x: 1; y: 1; width: parent.width-2; height: 40; clip: true
                    Row {
                        anchors.fill: parent
                        Repeater {
                            model: [{title:viewer.s.so_tab_paths,width:pathBox.firstWidth},{title:viewer.s.so_col_kind,width:pathBox.kindWidth},{title:viewer.s.so_col_size,width:pathBox.sizeWidth}]
                            delegate: Item {
                                required property var modelData
                                width: modelData.width; height: 40; clip: true
                                RakLabel { x: 8; width: parent.width-14; height: parent.height; text: modelData.title; font.pixelSize: 11; font.weight: Font.DemiBold; color: viewer.c.FG2; verticalAlignment: Text.AlignVCenter; elide: Text.ElideRight }
                            }
                        }
                    }
                    SmoothRectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: viewer.c.b08 }
                }
                ListView {
                    id: paths; objectName: "soPathTree"; x: 3; y: 42; width: parent.width-6; height: Math.max(1,parent.height-48); model: document.paths; clip: true
                    currentIndex: -1; keyNavigationEnabled: true; boundsBehavior: Flickable.StopAtBounds
                    C.ScrollBar.vertical: RakScrollBar { width: 5; handleColor: viewer.c.b30; hoverColor: viewer.c.b50 }
                    delegate: Item {
                        id: pathRow; required property int index; required property var modelData; width: paths.width; height: 28
                        SmoothRectangle { anchors.fill: parent; radius: 6; color: paths.currentIndex===pathRow.index?pathBox.selectedColor:pathMouse.containsMouse?viewer.c.b07:"transparent" }
                        Image { visible: pathRow.modelData.folder; x: 4+pathRow.modelData.depth*20; y: 8; width: 12; height: 12; rotation: pathRow.modelData.expanded?90:0; opacity: 0.8; source: "image://icons/arrow_right?color="+encodeURIComponent(viewer.c.treeArrow); sourceSize: Qt.size(24,24); smooth: true }
                        Image { x: 20+pathRow.modelData.depth*20; y: 6; width: 16; height: 16; source: "image://icons/"+pathRow.modelData.icon; sourceSize: Qt.size(16,16); smooth: true }
                        RakLabel { x: 42+pathRow.modelData.depth*20; y: 0; height: 28; width: Math.max(1,pathBox.firstWidth-x-6); text: pathRow.modelData.name; font.pixelSize: 13; font.bold: !!pathRow.modelData.isEmbedded; color: pathRow.modelData.isEmbedded?viewer.c.FG_BRIGHT:viewer.c.FG; verticalAlignment: Text.AlignVCenter; elide: Text.ElideRight }
                        RakLabel { x: pathBox.firstWidth+6; y: 0; height: 28; width: pathBox.kindWidth-12; text: pathRow.modelData.kind; font.pixelSize: 12; color: pathRow.modelData.isEmbedded?viewer.c.FG2:viewer.c.FG_MUTED; verticalAlignment: Text.AlignVCenter; elide: Text.ElideRight }
                        RakLabel { x: pathBox.firstWidth+pathBox.kindWidth+6; y: 0; height: 28; width: pathBox.sizeWidth-12; text: pathRow.modelData.size; font.pixelSize: 12; color: viewer.c.FG2; verticalAlignment: Text.AlignVCenter; elide: Text.ElideRight }
                        MouseArea {
                            id: pathMouse; anchors.fill: parent; hoverEnabled: true
                            onClicked: mouse=>{paths.currentIndex=pathRow.index;paths.forceActiveFocus();if(pathRow.modelData.folder && mouse.x<22+pathRow.modelData.depth*20)document.togglePath(pathRow.index)}
                            onDoubleClicked: document.openPath(pathRow.index)
                            HoverTip { hovering: pathMouse.containsMouse && !pathRow.modelData.folder; label: pathRow.modelData.path || pathRow.modelData.name+" ("+pathRow.modelData.kind+", "+pathRow.modelData.size+")" }
                        }
                    }
                    Keys.onReturnPressed: document.openPath(currentIndex)
                    Keys.onRightPressed: {const row=document.paths.snapshot()[currentIndex];if(row && row.folder && !row.expanded)document.togglePath(currentIndex)}
                    Keys.onLeftPressed: {const row=document.paths.snapshot()[currentIndex];if(row && row.folder && row.expanded)document.togglePath(currentIndex)}
                }
            }
        }
        ViewerTable {
            objectName: "soSectionsTable"; visible: viewer.currentTab===3; x: 10; y: 10; width: parent.width-20; height: Math.max(1,parent.height-20); colors: viewer.c; rows: document.sections; gutter: 34
            columns: [{title:viewer.s.so_tab_sections,stretch:true},{title:viewer.s.so_col_kind},{title:"Addr (VA)"},{title:"Offset"},{title:viewer.s.so_col_size},{title:"Flags"}]
            onRowActivated: row=>document.openSection(row)
        }
        ViewerTable {
            objectName: "soSegmentsTable"; visible: viewer.currentTab===4; x: 10; y: 10; width: parent.width-20; height: Math.max(1,parent.height-20); colors: viewer.c; rows: document.segments; gutter: 32
            columns: [{title:viewer.s.so_tab_segments,stretch:true},{title:"Addr (VA)"},{title:"Offset"},{title:viewer.s.so_col_size},{title:"Flags (R/W/X)"}]
        }
        Item {
            anchors.fill: parent; visible: viewer.currentTab===5
            Row {
                id: relocTools; x: 10; y: 10; width: parent.width-20; height: 30; spacing: 6
                RakLabel { id: relocCaption; text: viewer.s.so_search; width: Math.ceil(implicitWidth); height: 30; color: viewer.c.FG2; font.pixelSize: 11; verticalAlignment: Text.AlignVCenter }
                ViewerField { objectName: "soRelocFilter"; colors: viewer.c; width: relocTools.width-relocCaption.width-6; placeholderText: viewer.s.so_filter_symbols; onTextChanged: document.filterRelocations(text) }
            }
            ViewerTable {
                objectName: "soRelocationsTable"; x: 10; y: 46; width: parent.width-20; height: Math.max(1,parent.height-56); colors: viewer.c; rows: document.relocations; gutter: 30
                columns: [{title:"Offset"},{title:viewer.s.so_col_name,stretch:true},{title:"Section"},{title:viewer.s.so_col_kind}]
            }
        }
        Flickable {
            anchors.fill: parent; visible: viewer.currentTab===6; clip: true; contentWidth: width; contentHeight: securityCard.height+24; boundsBehavior: Flickable.StopAtBounds
            C.ScrollBar.vertical: RakScrollBar { width: 5; handleColor: viewer.c.b30; hoverColor: viewer.c.b50 }
            SmoothRectangle {
                id: securityCard; x: 12; y: 12; width: parent.width-24; height: Math.max(215,securityNote.y+securityNote.implicitHeight+9); radius: 6; color: viewer.c.BG2; border.width: 1; border.color: viewer.c.b08
                RakLabel { x: 15; y: 13; text: viewer.s.so_tab_security; font.pixelSize: 12; font.weight: Font.DemiBold; color: viewer.c.FG_BRIGHT }
                Repeater {
                    model: [{key:"stripped",label:"Symbol Table Stripped"},{key:"nx_stack",label:"NX Stack (Non-Executable Stack)"},{key:"relro",label:"RELRO (Relocation Read-Only)"},{key:"stack_canary",label:"Stack Canary (__stack_chk_fail)"},{key:"fortify_source",label:"FORTIFY_SOURCE (_chk functions)"},{key:"rpath_or_runpath",label:"RPATH / RUNPATH"}]
                    delegate: Item { required property var modelData; required property int index; x: 15; y: 38+index*24; width: securityCard.width-30; height: 16
                        RakLabel { text: parent.modelData.label+":"; height: 16; font.pixelSize: 11; color: viewer.c.FG2 }
                        RakLabel { anchors.right: parent.right; text: document.security[parent.modelData.key] || "-"; height: 16; font.pixelSize: 11; font.weight: Font.Medium; color: viewer.c.FG_BRIGHT }
                    }
                }
                RakLabel { id: securityNote; x: 15; y: 186; width: parent.width-30; height: Math.max(20,implicitHeight); text: document.security.note_pie || ""; font.pixelSize: 11; topPadding: 4; color: viewer.c.FG_MUTED; wrapMode: Text.WordWrap }
            }
        }
        Item {
            anchors.fill: parent; visible: viewer.currentTab===7
            Row {
                x: 10; y: 10; height: 28; spacing: 8
                RakLabel { text: "Section:"; height: 28; color: viewer.c.FG; font.pixelSize: 12; verticalAlignment: Text.AlignVCenter }
                ViewerCombo { id: disasmSection; objectName: "soDisasmSection"; colors: viewer.c; model: document.executableSections }
                RakLabel { text: viewer.s.so_col_size+":"; height: 28; color: viewer.c.FG; font.pixelSize: 12; verticalAlignment: Text.AlignVCenter }
                C.SpinBox {
                    id: disasmBytes; objectName: "soDisasmBytes"; width: 103; height: 28; from: 16; to: 65536; value: 512; editable: true; leftPadding: 7; rightPadding: 23
                    textFromValue: function(value,locale){return String(value)}
                    valueFromText: function(text,locale){return Number(text)}
                    contentItem: TextInput { objectName: "soDisasmBytesInput"; text: parent.textFromValue(parent.value,parent.locale); font.family: "Poppins"; font.pixelSize: 11; color: viewer.c.FG; verticalAlignment: Qt.AlignVCenter; validator: parent.validator; inputMethodHints: Qt.ImhDigitsOnly }
                    background: SmoothRectangle { radius: 4; color: viewer.c.BG_INPUT; border.width: 1; border.color: viewer.c.b10 }
                    up.indicator: RakLabel { x: parent.width-20; width: 16; height: 14; text: "▴"; color: viewer.c.FG2; horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter }
                    down.indicator: RakLabel { x: parent.width-20; y: 14; width: 16; height: 14; text: "▾"; color: viewer.c.FG2; horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter }
                }
                ViewerToolButton { objectName: "soDisassemble"; colors: viewer.c; primary: true; text: viewer.s.so_btn_disasm_action; onClicked: document.disassemble(disasmSection.currentText,disasmBytes.value) }
            }
            ViewerTextArea { objectName: "soDisasmOutput"; x: 10; y: 44; width: parent.width-20; height: Math.max(1,parent.height-54); colors: viewer.c; text: document.disassembly; font.family: "Consolas" }
        }
        BinaryFunctionsPane { anchors.fill: parent; visible: viewer.currentTab===9; colors: viewer.c; document: document; workspace: viewer.workspace }
        Item {
            anchors.fill: parent; visible: viewer.currentTab===8
            Row {
                id: stringTools; x: 10; y: 10; width: parent.width-20; height: 30; spacing: 6
                RakLabel { id: stringCaption; text: viewer.s.so_search; width: Math.ceil(implicitWidth); height: 30; color: viewer.c.FG2; font.pixelSize: 11; verticalAlignment: Text.AlignVCenter }
                ViewerField { id: stringFilter; objectName: "soStringFilter"; colors: viewer.c; width: stringTools.width-stringCaption.width-6; placeholderText: viewer.s.so_tab_strings+"..."; onTextChanged: document.filterStrings(text) }
            }
            ViewerTextArea { objectName: "soRawStrings"; x: 10; y: 46; width: parent.width-20; height: Math.max(1,parent.height-56); colors: viewer.c; text: document.stringsText; wrapText: true }
        }
    }
    function refreshSymbols() {document.filterSymbols(symbolFilter.text,kind.currentIndex>0?kind.currentText:"",mangled.checked)}
    SmoothRectangle {
        y: parent.height-28; width: parent.width; height: 28; color: viewer.c.BG2
        SmoothRectangle { width: parent.width; height: 1; color: viewer.c.b06 }
        RakLabel { x: 10; height: 28; width: Math.max(0,footerActions.x-22); text: document.status; color: viewer.c.FG3; font.pixelSize: 11; verticalAlignment: Text.AlignVCenter; elide: Text.ElideRight }
        Row {
            id: footerActions; anchors.right: parent.right; anchors.rightMargin: 8; y: 2; spacing: 12
            ViewerToolButton { objectName: "soExportJson"; colors: viewer.c; text: viewer.s.so_btn_export; onClicked: {if(document.ready)exportDialog.open();else viewer.workspace.showMessage("Info",viewer.s.so_not_ready)} }
            ViewerToolButton { objectName: "soCompare"; colors: viewer.c; text: viewer.s.so_btn_compare; onClicked: if(document.ready)compareFile.open() }
            ViewerToolButton { objectName: "soReload"; colors: viewer.c; text: viewer.s.so_btn_reload; onClicked: document.reload() }
        }
    }
    D.FileDialog { id: exportDialog; objectName: "soExportDialog"; title: viewer.s.so_btn_export; fileMode: D.FileDialog.SaveFile; nameFilters: ["JSON (*.json)"]; currentFile: document.exportUrl; onAccepted: document.exportJson(selectedFile.toString()) }
    D.FileDialog { id: compareFile; objectName: "soCompareFileDialog"; title: viewer.s.so_btn_compare; fileMode: D.FileDialog.OpenFile; currentFolder: document.folderUrl; nameFilters: ["Binaries (*.so *.so.* *.elf *.exe *.dll *.dylib *.o)","All Files (*)"]; onAccepted: document.compare(selectedFile.toString()) }
    D.FolderDialog { id: reconstructFolderDialog; title: "Select Destination Folder for Reconstructed Source Tree"; currentFolder: document.folderUrl; onAccepted: document.reconstructSourceTree(selectedFolder.toString()) }
    ViewerDialog {
        id: embedded; objectName: "soEmbeddedDialog"; colors: viewer.c; ownerWindow: viewer.Window.window
        width: 720; height: 500; title: document.embeddedInfo.title || ""
        SmoothRectangle {
            x: 14; y: 14; width: parent.width-28; height: 63; radius: 6; color: viewer.c.BG2; border.width: 1; border.color: viewer.c.b08
            RakLabel { x: 13; y: 11; width: parent.width-26; text: document.embeddedInfo.title || ""; font.pixelSize: 13; font.bold: true; color: viewer.c.FG_BRIGHT; elide: Text.ElideRight }
            RakLabel { x: 13; y: 35; width: parent.width-26; text: "<b>"+viewer.s.so_col_kind+":</b> "+(document.embeddedInfo.type || "")+" &nbsp;&nbsp;|&nbsp;&nbsp; <b>Offset:</b> "+(document.embeddedInfo.offset || "")+" &nbsp;&nbsp;|&nbsp;&nbsp; <b>"+viewer.s.so_col_size+":</b> "+(document.embeddedInfo.size || "")+" ("+(document.embeddedInfo.precision || "")+")"; textFormat: Text.RichText; font.pixelSize: 11; color: viewer.c.FG2; wrapMode: Text.WordWrap }
        }
        SmoothRectangle {
            id: embeddedImage; visible: !!document.embeddedInfo.image; x: 14; y: 87; width: parent.width-28; height: 220; radius: 4; color: viewer.c.BG2; border.width: 1; border.color: viewer.c.b08
            Image { anchors.centerIn: parent; width: Math.min(300,implicitWidth*200/Math.max(1,implicitHeight)); height: Math.min(200,implicitHeight*300/Math.max(1,implicitWidth)); source: document.embeddedInfo.image || ""; smooth: true; mipmap: true; fillMode: Image.PreserveAspectFit }
        }
        ViewerTextArea { objectName: "soEmbeddedHex"; x: 14; y: embeddedImage.visible?317:87; width: parent.width-28; height: Math.max(1,parent.height-y-52); colors: viewer.c; text: document.embeddedInfo.hex || ""; wrapText: true }
        Row {
            x: 14; y: parent.height-42; spacing: 8
            ViewerToolButton { objectName: "soEmbeddedSave"; colors: viewer.c; primary: true; text: viewer.s.so_btn_save_as; onClicked: embeddedSave.open() }
            ViewerToolButton { objectName: "soAnalyzeNested"; visible: !!document.embeddedInfo.nested; y: 2; colors: viewer.c; text: viewer.s.so_btn_analyze_nested; onClicked: document.analyzeNested() }
        }
        ViewerToolButton { objectName: "soEmbeddedClose"; x: parent.width-width-14; y: parent.height-40; colors: viewer.c; text: viewer.s.so_btn_close; onClicked: embedded.close() }
        D.FileDialog { id: embeddedSave; objectName: "soEmbeddedSaveDialog"; title: viewer.s.so_btn_save_as; fileMode: D.FileDialog.SaveFile; currentFile: document.embeddedSaveUrl; onAccepted: document.saveEmbedded(selectedFile.toString()) }
        MessageWindow { id: embeddedMessage; model: viewer.preferences; hostWindow: embedded }
    }
    ViewerDialog {
        id: pathDialog; objectName: "soPathDialog"; colors: viewer.c; ownerWindow: viewer.Window.window
        width: 760; height: 520; title: viewer.s.so_path_str_title
        property string fullPath: ""
        property bool copied: false
        onVisibleChanged: if(visible){copied=false;copyTimer.stop()}
        SmoothRectangle {
            x: 14; y: 14; width: parent.width-28; height: 118; radius: 6; color: viewer.c.BG2; border.width: 1; border.color: viewer.c.b08
            RakLabel { x: 13; y: 8; height: 28; text: viewer.s.so_path_str_title; font.pixelSize: 13; font.weight: Font.DemiBold; color: viewer.c.FG_BRIGHT; verticalAlignment: Text.AlignVCenter }
            ViewerField { objectName: "soPathText"; x: 13; y: 38; width: parent.width-26; colors: viewer.c; text: pathDialog.fullPath; readOnly: true; selectByMouse: true }
            RakLabel { x: 13; y: 74; width: parent.width-26; text: viewer.s.so_path_str_info; color: viewer.c.FG2; font.pixelSize: 11; wrapMode: Text.WordWrap; maximumLineCount: 2; elide: Text.ElideRight }
        }
        ViewerTextArea {
            objectName: "soPathXrefOutput"
            x: 14; y: 142; width: parent.width-28; height: Math.max(1,parent.height-194)
            colors: viewer.c; font.family: "Consolas"; font.pixelSize: 11
            text: document.pathXrefs && document.pathXrefs.summary_text ? document.pathXrefs.summary_text : "Scanning string locations & cross-references…"
        }
        Row {
            x: 14; y: parent.height-42; spacing: 8
            ViewerToolButton { objectName: "soCopyPath"; colors: viewer.c; primary: true; text: pathDialog.copied?viewer.s.so_copied_toast:viewer.s.so_btn_copy_path; onClicked: {document.copyText(pathDialog.fullPath);pathDialog.copied=true;copyTimer.restart()} }
            ViewerToolButton { objectName: "soPathRawStrings"; y: 2; colors: viewer.c; text: viewer.s.so_btn_view_raw_strings; onClicked: {pathDialog.close();viewer.currentTab=8;stringFilter.text=pathDialog.fullPath} }
            ViewerToolButton {
                objectName: "soPathOpenFunction"; y: 2; colors: viewer.c; text: "Inspect Xref Function"
                enabled: !!(document.pathXrefs && document.pathXrefs.function_addresses && document.pathXrefs.function_addresses.length && document.canDecompile)
                onClicked: {
                    const addrs = document.pathXrefs.function_addresses
                    if (addrs && addrs.length) { pathDialog.close(); viewer.currentTab=9; document.inspectFunctionAtAddress(addrs[0]) }
                }
            }
            ViewerToolButton {
                objectName: "soPathAskAgent"; y: 2; colors: viewer.c; text: "Ask Agent (REA)"
                enabled: !!(document.pathXrefs && document.pathXrefs.agent_prompt)
                onClicked: {
                    const promptText = document.pathXrefs.agent_prompt
                    const chat = viewer.workspace && viewer.workspace.agentChat
                    pathDialog.close()
                    if (chat && chat.available) {
                        if (!chat.opened) chat.toggle()
                        chat.setDraft(promptText)
                    } else {
                        document.copyText(promptText)
                        if (viewer.workspace && viewer.workspace.showMessage) viewer.workspace.showMessage("REA Context Copied", "Xref & disassembly context copied to clipboard.")
                    }
                }
            }
            ViewerToolButton {
                objectName: "soPathExportTree"; y: 2; colors: viewer.c; text: "Export All Sources"
                enabled: document.ready && !document.busy
                onClicked: { pathDialog.close(); reconstructFolderDialog.open() }
            }
        }
        Timer { id: copyTimer; interval: 1500; onTriggered: pathDialog.copied=false }
        ViewerToolButton { objectName: "soPathClose"; x: parent.width-width-14; y: parent.height-40; colors: viewer.c; text: viewer.s.so_btn_close; onClicked: pathDialog.close() }
    }
    ViewerDialog {
        id: nested; objectName: "soNestedDialog"; colors: viewer.c; ownerWindow: embedded
        width: 600; height: 440; title: viewer.s.so_nested_elf_title; property string analysisText: ""
        ViewerTextArea { objectName: "soNestedText"; x: 14; y: 14; width: parent.width-28; height: parent.height-60; colors: viewer.c; text: nested.analysisText; wrapText: true }
        ViewerToolButton { objectName: "soNestedClose"; x: parent.width-width-14; y: parent.height-38; colors: viewer.c; text: viewer.s.so_btn_close; onClicked: nested.close() }
    }
    ViewerDialog {
        id: compareDialog; objectName: "soCompareDialog"; colors: viewer.c; ownerWindow: viewer.Window.window
        width: 760; height: 520; title: viewer.s.so_compare_title
        SmoothRectangle {
            x: 14; y: 14; width: parent.width-28; height: 90; radius: 6; color: viewer.c.BG2; border.width: 1; border.color: viewer.c.b08
            Repeater {
                model: ["<b>File A:</b> "+(document.comparison.fileA || ""),"<b>File B:</b> "+(document.comparison.fileB || ""),viewer.s.so_compare_common+" <b>"+(document.comparison.common_count || 0)+"</b>"]
                delegate: SmoothRectangle { required property string modelData; required property int index; x: 13; y: 11+index*24; width: parent.width-26; height: 20; radius: 6; color: viewer.c.BG2; border.width: 1; border.color: viewer.c.b08
                    RakLabel { x: 3; height: 20; text: parent.modelData; textFormat: Text.RichText; color: viewer.c.FG; font.pixelSize: 12 }
                }
            }
        }
        ViewerTextArea { objectName: "soCompareText"; x: 14; y: 114; width: parent.width-28; height: parent.height-162; colors: viewer.c; text: document.comparison.text || ""; wrapText: true }
        ViewerToolButton { objectName: "soCompareClose"; x: parent.width-width-14; y: parent.height-38; colors: viewer.c; text: viewer.s.so_btn_close; onClicked: compareDialog.close() }
    }
}
