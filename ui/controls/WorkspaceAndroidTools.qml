// SPDX-License-Identifier: MPL-2.0
import QtQuick
import QtQuick.Controls as C

DialogWindow {
    id: dialog
    objectName: "workspaceAndroidTools"
    required property var workspace
    readonly property var backend: workspace.androidTools
    property int page: 0
    property string operation: ""
    property string partition: ""
    property bool operationVisible: false
    implicitWidth: Math.max(600,Math.min(hostWindow.width*.72,920))
    implicitHeight: Math.max(420,Math.min(hostWindow.height*.78,610))
    panelRadius: 12
    function showTools() {page=0;operationVisible=false;backend.open();open()}
    function showOperation(kind,name) {operation=kind;partition=name;opName.text="";opSize.text="";appendSlot.checked=backend.hasSlot;operationVisible=true;opSize.forceActiveFocus()}
    onDismissed: backend.dismiss()
    Item {
        id: body; anchors.fill: parent
        Item {
            id: titlebar; width: parent.width; height: 48
            Row {
                x: 18; height: parent.height; spacing: 10
                WorkspaceNativeIcon {
                    width: 16; height: 16; iconSize: 16
                    iconKey: "android_tools"
                    strokeColor: dialog.c.accent_hex || dialog.c.FG
                    accentColor: dialog.c.accent_hex || "#38bdf8"
                    isLight: !!(dialog.c && dialog.c.is_light)
                    anchors.verticalCenter: parent.verticalCenter
                }
                RakLabel { text: "Android Tools"; font.pixelSize: 13; font.weight: Font.Bold; font.letterSpacing: .3; height: parent.height; color: dialog.c.FG }
                SmoothRectangle {
                    width: Math.min(200,badgeText.implicitWidth+18); height: 22; anchors.verticalCenter: parent.verticalCenter
                    color: dialog.backend.mode?dialog.c.a08:dialog.c.b04; radius: 5; border.width: 1; border.color: dialog.c.b10
                    RakLabel { id: badgeText; anchors.fill: parent; anchors.margins: 8; text: dialog.backend.badge; elide: Text.ElideRight; font.pixelSize: 9; color: dialog.backend.mode?dialog.c.accent_hex:dialog.c.FG3 }
                }
            }
            Row {
                anchors.right: parent.right; anchors.rightMargin: 18; anchors.verticalCenter: parent.verticalCenter; spacing: 6
                Repeater {
                    model: dialog.backend.mode==="adb"?["→ Fastboot","→ Bootloader","→ Recovery","Reboot"]:["Bootloader","Fastboot","Recovery","Reboot"]
                    delegate: AndroidToolButton { required property int index; required property string modelData; colors: dialog.c; text: modelData; danger: index===3; enabled: !!dialog.backend.mode; onClicked: dialog.backend.reboot(index) }
                }
            }
        }
        SmoothRectangle { y: 48; width: parent.width; height: 1; color: dialog.c.b09 }
        Item {
            y: 49; width: parent.width; height: 38
            Row {
                x: 12; height: parent.height; spacing: 6
                Repeater {
                    model: dialog.backend.trees.length>1?dialog.backend.trees:[]
                    delegate: AndroidToolButton { required property int index; required property string modelData; anchors.verticalCenter: parent.verticalCenter; colors: dialog.c; text: modelData; selected: dialog.backend.treeIndex===index; onClicked: dialog.backend.selectTree(index) }
                }
                Repeater {
                    model: ["Partition Utility","Flash Tools"]
                    delegate: AndroidToolButton { required property int index; required property string modelData; objectName: "androidPage"+index; anchors.verticalCenter: parent.verticalCenter; colors: dialog.c; text: modelData; selected: dialog.page===index; onClicked: dialog.page=index }
                }
            }
            AndroidToolButton { objectName: "androidRefresh"; anchors.right: parent.right; anchors.rightMargin: 14; anchors.verticalCenter: parent.verticalCenter; colors: dialog.c; text: dialog.backend.refreshing?"Refreshing…":"↻  Refresh"; enabled: !dialog.backend.refreshing; onClicked: dialog.backend.refresh() }
        }
        SmoothRectangle { y: 87; width: parent.width; height: 1; color: dialog.c.b09 }
        Item {
            id: content; y: 88; width: parent.width; height: parent.height-y-45-queuePanel.height
            ListView {
                id: images; objectName: "androidImages"; width: 210; height: parent.height; clip: true
                model: dialog.backend.images; topMargin: 8; bottomMargin: 8; spacing: 1
                C.ScrollBar.vertical: RakScrollBar { handleColor: dialog.c.BG_SCROLL }
                delegate: SmoothRectangle {
                    id: imageRow; required property var modelData; required property int index
                    objectName: "androidImage"+index
                    x: 8; width: 194; height: 32; radius: 5
                    color: dialog.backend.selectedImage===modelData.path?dialog.c.a10:imageMouse.containsMouse?dialog.c.b04:"transparent"
                    border.width: dialog.backend.selectedImage===modelData.path?1:0; border.color: dialog.c.a25
                    RakLabel { x: 10; height: parent.height; width: parent.width-sizeLabel.width-26; text: modelData.name; font.pixelSize: 11; elide: Text.ElideRight; color: dialog.c.FG2 }
                    RakLabel { id: sizeLabel; anchors.right: parent.right; anchors.rightMargin: 10; height: parent.height; text: modelData.size; font.pixelSize: 9; color: dialog.c.FG3 }
                    Item {
                        id: dragTarget
                        Drag.active: imageMouse.drag.active; Drag.dragType: Drag.Automatic; Drag.supportedActions: Qt.CopyAction
                        Drag.mimeData: ({"application/x-rak-image":modelData.name+"\n"+modelData.path,"text/plain":modelData.name})
                    }
                    MouseArea { id: imageMouse; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.OpenHandCursor; drag.target: dragTarget; drag.threshold: 8; onPressed: dialog.backend.selectImage(modelData.path); onReleased: {dragTarget.x=0;dragTarget.y=0} }
                }
                RakLabel { x: 20; y: 20; width: parent.width-40; visible: images.count===0; text: "No .img files found."; font.pixelSize: 11; wrapMode: Text.WordWrap; horizontalAlignment: Text.AlignHCenter; color: dialog.c.FG3 }
            }
            SmoothRectangle { x: 209; width: 1; height: parent.height; color: dialog.c.b09 }
            Item {
                x: 210; width: parent.width-x; height: parent.height
                RakLabel { anchors.centerIn: parent; width: parent.width-32; text: dialog.backend.placeholder; visible: text.length>0; horizontalAlignment: Text.AlignHCenter; wrapMode: Text.WordWrap; font.pixelSize: 11; color: dialog.c.FG3 }
                RakScrollView {
                    colors: dialog.c
                    id: partitionScroll; anchors.fill: parent; anchors.leftMargin: 16; anchors.rightMargin: 16; anchors.bottomMargin: partitionLog.visible?partitionLog.height+8:12
                    visible: dialog.page===0 && !dialog.backend.placeholder.length; clip: true
                    contentWidth: availableWidth
                    Column {
                        width: partitionScroll.availableWidth; spacing: 2
                        Repeater {
                            model: dialog.backend.partitions
                            delegate: SmoothRectangle {
                                id: partitionRow; required property var modelData
                                property bool confirmed: false
                                property bool removed: false
                                readonly property bool logical: modelData.kind==="logical"
                                readonly property bool header: modelData.kind==="header"
                                width: parent.width; height: logical || modelData.kind==="unallocated"?34:30; radius: 5
                                color: header?"transparent":logical?dialog.c.b02:dialog.c.b015
                                border.width: header?0:1; border.color: logical?dialog.c.b05:dialog.c.b04
                                enabled: !removed
                                Timer { id: confirmTimer; interval: 3000; onTriggered: partitionRow.confirmed=false }
                                RakLabel { x: header?0:10; y: header?8:0; width: header?implicitWidth:Math.max(30,parent.width-250); height: parent.height-y; text: modelData.name; elide: Text.ElideRight; font.pixelSize: header?(modelData.create?10:9):11; font.weight: header?Font.Bold:Font.Normal; color: header?(modelData.create?dialog.c.PURPLE:dialog.c.FG3):logical?dialog.c.FG:dialog.c.FG2 }
                                RakLabel { x: header?115:0; y: header?8:0; anchors.right: header?create.left:actions.left; anchors.rightMargin: 8; height: parent.height-y; text: header?modelData.info:(modelData.slotted?dialog.backend.slot.toUpperCase()+"  ":"")+modelData.size; horizontalAlignment: Text.AlignRight; font.pixelSize: 10; color: modelData.slotted?dialog.c.accent_hex:dialog.c.FG3 }
                                AndroidToolButton { id: create; anchors.right: parent.right; y: 6; width: 22; height: 22; colors: dialog.c; text: "+"; visible: header && !!modelData.create; onClicked: dialog.showOperation("create","") }
                                Row {
                                    id: actions; anchors.right: parent.right; anchors.rightMargin: 10; height: parent.height; spacing: 8; visible: !header
                                    AndroidToolButton { visible: partitionRow.logical; anchors.verticalCenter: parent.verticalCenter; colors: dialog.c; danger: true; text: partitionRow.confirmed?"Confirm?":"Delete"; onClicked: {if(!partitionRow.confirmed){partitionRow.confirmed=true;confirmTimer.restart()}else{dialog.backend.deletePartition(modelData.name,modelData.slotted);partitionRow.removed=true}} }
                                    AndroidToolButton { visible: partitionRow.logical; anchors.verticalCenter: parent.verticalCenter; colors: dialog.c; text: "Resize"; onClicked: dialog.showOperation("resize",modelData.name+(modelData.slotted?"_"+dialog.backend.slot:"")) }
                                    RakLabel { visible: modelData.kind==="physical"; anchors.verticalCenter: parent.verticalCenter; text: modelData.type || ""; font.pixelSize: 9; color: dialog.c.FG3 }
                                }
                            }
                        }
                    }
                }
                RakLabel { id: partitionLog; x: 16; anchors.bottom: parent.bottom; width: parent.width-32; height: 28; visible: dialog.page===0 && !!text; text: dialog.backend.partitionLog; elide: Text.ElideRight; font.pixelSize: 10; color: dialog.c.FG3 }
                RakScrollView {
                    colors: dialog.c
                    id: chartScroll; anchors.fill: parent; clip: true; visible: dialog.page===1 && !dialog.backend.placeholder.length
                    contentWidth: Math.max(availableWidth,408); contentHeight: flow.height
                    AndroidFlowChart { id: flow; x: Math.max(0,(chartScroll.availableWidth-408)/2); backend: dialog.backend; preferences: dialog.model }
                }
            }
        }
        SmoothRectangle {
            id: queuePanel; y: content.y+content.height; width: parent.width
            height: queueList.count?Math.min(178,66+queueList.count*28):0
            Behavior on height { NumberAnimation { duration: dialog.model.settings.animations?200:0; easing.type: Easing.OutCubic } }
            clip: true; color: dialog.c.b02
            SmoothRectangle { x: 16; y: 35; width: parent.width-32; height: 3; radius: 1; color: dialog.c.b10; visible: queueList.count>0 }
            SmoothRectangle {
                x: 16; y: 35; height: 3; radius: 1
                width: (parent.width-32)*(dialog.backend.flashProgress<0?0:dialog.backend.flashProgress/100)
                color: dialog.backend.failed?dialog.c.RED_STATUS:dialog.c.accent_hex
                visible: queueList.count>0
                Behavior on width {
                    enabled: dialog.model.settings.animations && dialog.backend.flashProgress >= 0
                    NumberAnimation { duration: 110; easing.type: Easing.OutCubic }
                }
            }
            SmoothRectangle {
                id: queueMarquee
                property real phase: 0
                x: 16+phase*Math.max(0,parent.width-32-width); y: 35
                width: Math.min(90,(parent.width-32)*.22); height: 3; radius: 1; color: dialog.c.accent_hex
                visible: dialog.backend.running && dialog.backend.flashProgress<0
                NumberAnimation on phase { from: 0; to: 1; duration: 1000; loops: Animation.Infinite; running: queueMarquee.visible }
            }
            Item {
                width: parent.width; height: 38
                RakLabel { x: 16; height: parent.height; text: "FLASH QUEUE"; font.pixelSize: 9; font.weight: Font.Bold; color: dialog.c.FG3 }
                Row {
                    anchors.right: parent.right; anchors.rightMargin: 16; anchors.verticalCenter: parent.verticalCenter; spacing: 6
                    AndroidToolButton { objectName: "androidFlashAll"; colors: dialog.c; text: dialog.backend.failed?"Skip":"Flash All"; selected: true; enabled: !dialog.backend.running; onClicked: dialog.backend.flashAll() }
                    AndroidToolButton { objectName: "androidClearQueue"; colors: dialog.c; text: "Clear"; enabled: !dialog.backend.running; onClicked: dialog.backend.clearQueue() }
                }
            }
            ListView {
                id: queueList; x: 16; y: 38; width: parent.width-32; height: parent.height-y-8; clip: true; model: dialog.backend.queue
                delegate: Item {
                    required property var modelData; width: queueList.width; height: 28
                    RakLabel { width: parent.width-90; height: 24; text: modelData.partition+"  ←  "+modelData.image; font.pixelSize: 10; elide: Text.ElideRight; color: dialog.c.FG2 }
                    RakLabel { anchors.right: parent.right; height: 24; text: modelData.state==="running"?(modelData.progress<0?"…":modelData.progress+"%"):modelData.state; font.pixelSize: 9; color: modelData.state==="error"?dialog.c.RED_STATUS:modelData.state==="done"?dialog.c.GREEN_STATUS:dialog.c.FG3 }
                }
            }
        }
        SmoothRectangle { anchors.bottom: footer.top; width: parent.width; height: 1; color: dialog.c.b09 }
        Item {
            id: footer; anchors.bottom: parent.bottom; width: parent.width; height: 44
            RakLabel { x: 16; width: parent.width-110; height: parent.height; text: dialog.backend.status; elide: Text.ElideRight; font.pixelSize: 10; color: dialog.c.FG3 }
            AndroidToolButton { objectName: "androidClose"; anchors.right: parent.right; anchors.rightMargin: 16; anchors.verticalCenter: parent.verticalCenter; colors: dialog.c; text: "Close"; onClicked: dialog.dismiss() }
        }
        SmoothRectangle {
            anchors.fill: parent; color: "#99000000"; visible: dialog.operationVisible; radius: 12
            MouseArea { anchors.fill: parent }
            SmoothRectangle {
                width: Math.min(380,parent.width-40); height: opForm.height+40; anchors.centerIn: parent; radius: 10; color: dialog.c.BG2; border.width: 1; border.color: dialog.c.b18
                Column {
                    id: opForm; x: 20; y: 20; width: parent.width-40; spacing: 12
                    RakLabel { text: dialog.operation==="create"?"Create Logical Partition":"Resize Partition"; font.pixelSize: 13; font.weight: Font.DemiBold; color: dialog.c.FG }
                    RakLabel { text: dialog.operation==="create"?"Partition name:":"New size for "+dialog.partition+":"; font.pixelSize: 11; color: dialog.c.FG2 }
                    RakField { id: opName; objectName: "androidPartitionName"; width: parent.width; height: 30; visible: dialog.operation==="create"; fillColor: dialog.c.BG3; edgeColor: dialog.c.b12; labelColor: dialog.c.FG; placeholderText: "e.g. system"; font.pixelSize: 11 }
                    RakField { id: opSize; objectName: "androidPartitionSize"; width: parent.width; height: 30; fillColor: dialog.c.BG3; edgeColor: dialog.c.b12; labelColor: dialog.c.FG; placeholderText: "e.g. size or 1.5 GB or 0x4000000"; font.pixelSize: 11 }
                    Row {
                        spacing: 8; visible: dialog.operation==="create" && dialog.backend.hasSlot
                        RakSwitch { id: appendSlot; width: 30; height: 17; animationsEnabled: dialog.model.settings.animations }
                        RakLabel { text: "Auto-append slot _"+dialog.backend.slot; font.pixelSize: 10; color: dialog.c.FG3 }
                    }
                    Row {
                        anchors.right: parent.right; spacing: 8
                        AndroidToolButton { colors: dialog.c; text: "Cancel"; onClicked: dialog.operationVisible=false }
                        AndroidToolButton { objectName: "androidPartitionApply"; colors: dialog.c; selected: true; text: dialog.operation==="create"?"Create":"Resize"; onClicked: {if(dialog.operation==="create"?dialog.backend.createPartition(opName.text,opSize.text,appendSlot.checked):dialog.backend.resizePartition(dialog.partition,opSize.text))dialog.operationVisible=false} }
                    }
                }
            }
        }
    }
    Connections { target: dialog.backend; function onDeviceChanged() {flow.restart()} }
}
