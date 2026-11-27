// SPDX-License-Identifier: MPL-2.0
import QtQuick
import QtQuick.Controls as C
import QtQuick.Dialogs as D

Item {
    id: pane
    required property var colors
    required property var document
    property var workspace: null
    property bool draftDirty: false
    property string draftAddress: ""
    property string draftSha256: ""
    property int draftRevision: 0
    property int detailTab: 0
    function readAnnotation() {
        const f=document.functionDetail.function
        if(!f)return
        draftAddress=f.address
        draftSha256=document.summary.sha256
        const key=f.address.toLowerCase().replace(/^0x/,"").replace(/^0+/,"") || "0"
        const note=document.annotations.entries[key] || {}
        noteName.text=note.name || f.name;noteComment.text=note.comment || ""
        draftRevision=document.annotations.revision;draftDirty=false
    }
    Connections {
        target: document
        function onFunctionChanged() {if(!pane.draftDirty)pane.readAnnotation()}
        function onAnnotationSaved(address,revision) {
            if(address!==pane.draftAddress)return
            pane.draftRevision=revision
            const key=address.toLowerCase().replace(/^0x/,"").replace(/^0+/,"") || "0"
            const saved=document.annotations.entries[key]
            if(saved && saved.name===noteName.text && saved.comment===noteComment.text)pane.draftDirty=false
        }
    }
    function askRea(promptText) {
        if (!promptText) return
        const chat = pane.workspace && pane.workspace.agentChat
        if (chat && chat.available) {
            if (!chat.opened) chat.toggle()
            chat.setDraft(promptText)
        } else {
            document.copyText(promptText)
            if (pane.workspace && pane.workspace.showMessage) pane.workspace.showMessage("REA Context Copied", "Analysis context copied to clipboard. Paste into your AI/REA session.")
        }
    }
    Row {
        id: tools; x: 12; y: 10; spacing: 8; height: 30
        ViewerToolButton { objectName: "binaryAnalyzeGhidra"; colors: pane.colors; primary: true; text: document.busy?"Working…":"Analyze with Ghidra"; enabled: document.ready && !document.busy && !!document.ghidra.available; onClicked: document.analyzeGhidra() }
        ViewerToolButton { objectName: "binaryAnalyzeResident"; colors: pane.colors; text: "Resident Bridge (REA)"; enabled: document.ready && !document.busy && !!document.ghidra.available && !!(document.rea && document.rea.available); onClicked: document.analyzeResidentGhidra() }
        ViewerToolButton { objectName: "binaryConfigureGhidra"; colors: pane.colors; text: "Ghidra folder"; enabled: !document.busy; onClicked: ghidraFolder.open() }
        ViewerToolButton { objectName: "binaryAskAgent"; colors: pane.colors; text: "Ask Agent (REA)"; enabled: !!(document.functionDetail && document.functionDetail.reaPrompt); onClicked: pane.askRea(document.functionDetail.reaPrompt) }
    }
    RakLabel { x: 12; y: 45; width: parent.width-24; height: 32; text: document.ghidra.available ? (document.ghidra.message || "") : (document.nativeDecompiler.available ? "Native Ghidra decompiler active (no JVM). Select a function and press Enter. Ghidra install adds CFG, callers and callees." : ((document.nativeDecompiler.message || "")+" · "+(document.ghidra.message || ""))); color: pane.colors.FG_MUTED; font.pixelSize: 10; wrapMode: Text.WordWrap }
    Item {
        id: browser; x: 12; y: 83; width: Math.max(150,Math.floor((parent.width-32)*0.37)); height: Math.max(1,parent.height-y-12)
        ViewerField { id: filter; objectName: "binaryFunctionFilter"; width: parent.width; colors: pane.colors; placeholderText: "Function or address…"; onTextChanged: document.filterFunctions(text) }
        ViewerTable { objectName: "binaryFunctionsTable"; y: 38; width: parent.width; height: Math.max(1,parent.height-38); colors: pane.colors; rows: document.functions; gutter: 28
            columns: [{title:"Address",width:105},{title:"Function",stretch:true}]
            onRowActivated: index=>document.inspectFunction(index)
        }
    }
    Item {
        x: browser.x+browser.width+10; y: browser.y; width: Math.max(1,parent.width-x-12); height: browser.height
        RakLabel { width: parent.width; height: 28; text: document.functionDetail.function?document.functionDetail.function.signature:"Select a function and press Enter"; color: pane.colors.FG_BRIGHT; font.pixelSize: 11; elide: Text.ElideRight }
        ViewerTabs { id: views; y: 30; width: parent.width; colors: pane.colors; titles: ["Pseudocode","Assembly","References","Calls","CFG","Notes"]; onCurrentIndexChanged: pane.detailTab=currentIndex }
        ViewerTextArea { objectName: "binaryFunctionOutput"; y: 66; width: parent.width; height: Math.max(1,parent.height-66); visible: pane.detailTab<4; colors: pane.colors; font.family: "Consolas"; font.pixelSize: 12
            text: pane.detailTab===0?(document.functionDetail.pseudocode || document.functionDetail.decompile_error || "Run analysis once, then open a function. Cached results are shared with rak binary."):
                  pane.detailTab===1?(document.functionDetail.listing || ""):
                  pane.detailTab===2?(document.functionDetail.referencesText || ""):(document.functionDetail.callsText || "")
        }
        BinaryCfgView {
            objectName: "binaryCfgView"
            y: 66; width: parent.width; height: Math.max(1,parent.height-66)
            visible: pane.detailTab===4
            colors: pane.colors
            blocks: document.functionDetail.blocks || []
            onBlockSelected: address=>document.copyText(address)
        }
        Item {
            y: 72; width: parent.width; height: Math.max(1,parent.height-y); visible: pane.detailTab===5
            RakLabel { width: parent.width; height: 38; text: "Notes for "+(pane.draftAddress || "selected function")+" · Saved locally; shared with agents."; color: pane.colors.FG_MUTED; font.pixelSize: 11; wrapMode: Text.WordWrap }
            ViewerField { id: noteName; objectName: "binaryNoteName"; y: 42; width: parent.width; colors: pane.colors; placeholderText: "Function name"; onTextEdited: pane.draftDirty=true }
            C.ScrollView { y: 80; width: parent.width; height: Math.max(40,parent.height-130); clip: true
                C.TextArea { id: noteComment; objectName: "binaryNoteComment"; color: pane.colors.FG; selectionColor: pane.colors.a40; selectedTextColor: pane.colors.FG_BRIGHT; wrapMode: TextEdit.Wrap; font.pixelSize: 12; placeholderText: "Evidence, hypothesis, next steps…"; placeholderTextColor: pane.colors.FG_MUTED
                    onTextChanged: if(activeFocus)pane.draftDirty=true
                    background: SmoothRectangle { color: pane.colors.BG_INPUT; radius: 6; border.width: 1; border.color: pane.colors.b10 }
                }
            }
            Row { y: parent.height-36; spacing: 8
                ViewerToolButton { objectName: "binarySaveNote"; colors: pane.colors; primary: true; text: "Save notes"; enabled: !!pane.draftAddress && pane.draftSha256===document.summary.sha256 && !document.busy; onClicked: document.annotate(pane.draftAddress,noteName.text,noteComment.text,pane.draftRevision) }
                ViewerToolButton { colors: pane.colors; text: "Refresh"; enabled: !document.busy; onClicked: document.refreshAnnotations() }
                ViewerToolButton { colors: pane.colors; text: "Load selected"; enabled: !document.busy; onClicked: pane.readAnnotation() }
            }
        }
    }
    D.FolderDialog { id: ghidraFolder; title: "Select Ghidra installation"; onAccepted: document.configureGhidra(selectedFolder.toString()) }
}
