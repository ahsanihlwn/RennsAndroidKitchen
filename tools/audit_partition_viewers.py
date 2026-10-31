# SPDX-License-Identifier: MPL-2.0
"""Original Payload/Super layout captures with inert partition metadata."""
import json
import os
from pathlib import Path
import struct
import sys
import tempfile


PARTS=['system','vendor','product','boot']
SUPER_PARTS=['system_a','vendor_a','product_a','system_b','vendor_b','main_a','default','ignored']


def payload_fixture():
    from core import update_metadata_pb2 as pb
    manifest=pb.DeltaArchiveManifest(block_size=4096,minor_version=0);data=b''
    for index,name in enumerate(PARTS):
        content=(f'NATIVE_PARTITION_{name}\n'.encode()*4096)[:4096]
        part=manifest.partitions.add(partition_name=name);part.new_partition_info.size=len(content)
        operation=part.operations.add(type=pb.InstallOperation.REPLACE,data_offset=len(data),data_length=len(content));operation.dst_extents.add(start_block=0,num_blocks=1)
        data+=content
    encoded=manifest.SerializeToString()
    return struct.pack('>4sQQI',b'CrAU',2,len(encoded),1)+encoded+b'\0'+data


def main():
    repo=Path(__file__).resolve().parents[1];artifacts=repo/'build/qtquick_migration/original_partitions';artifacts.mkdir(parents=True,exist_ok=True)
    source=repo.parent/'rennsandroidkitchen';sys.path.insert(0,str(source));os.chdir(source);sys.dont_write_bytecode=True
    from core.fonts import configure_text_rendering_env,apply_app_font
    configure_text_rendering_env()
    from PySide6.QtCore import QPoint,QTimer,QEventLoop
    from PySide6.QtWidgets import QApplication,QWidget,QVBoxLayout,QLabel,QAbstractButton,QListWidget
    from main_ext.class_ThemeUi import theme,_PALETTES
    from core import ui_lang
    from unittest.mock import patch
    app=QApplication([]);app.setQuitOnLastWindowClosed(False);apply_app_font(app);ui_lang.init()
    theme._name='dark_green';theme._apply_palette(_PALETTES['dark_green'])
    from main_ext.class_PayloadViewer import PayloadViewer
    from main_ext.class_SuperViewer import SuperViewer
    def wait(ms):
        loop=QEventLoop();QTimer.singleShot(ms,loop.quit);loop.exec()
    def capture(viewer,name):
        wait(100);viewer.grab().save(str(artifacts/(name+'.png')));rows=[]
        for widget in [viewer,*viewer.findChildren(QWidget)]:
            if not widget.isVisibleTo(viewer):continue
            p=widget.mapTo(viewer,QPoint());row={'class':type(widget).__name__,'rect':[p.x(),p.y(),widget.width(),widget.height()],'font':widget.font().toString()}
            if isinstance(widget,(QLabel,QAbstractButton)):row['text']=widget.text()
            if isinstance(widget,QListWidget) and widget.count():row['itemRect']=widget.visualItemRect(widget.item(0)).getRect()
            rows.append(row)
        (artifacts/(name+'.json')).write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding='utf-8')
    with tempfile.TemporaryDirectory(prefix='rak-partitions-reference-') as temporary:
        host=QWidget();host.setStyleSheet(f'background-color:{theme.BG3};');host.resize(1080,650)
        layout=QVBoxLayout(host);layout.setContentsMargins(0,0,0,0)
        viewer=PayloadViewer(str(Path(temporary)/'payload.bin'),PARTS);layout.addWidget(viewer);host.show();capture(viewer,'payload')
        viewer._select_all();capture(viewer,'payload_selected');layout.removeWidget(viewer);viewer.hide();viewer.deleteLater();wait(50)
        with patch.object(SuperViewer,'read_super_info',lambda self:{'partitions':SUPER_PARTS,'active_slot':'A'}):
            viewer=SuperViewer(str(Path(temporary)/'super.img'));layout.addWidget(viewer);wait(200)
        capture(viewer,'super');viewer.list_a.item(0).setSelected(True);capture(viewer,'super_selected')
        host.close();host.deleteLater();wait(100)


if __name__=='__main__':main()
