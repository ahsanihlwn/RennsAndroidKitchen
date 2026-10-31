# SPDX-License-Identifier: MPL-2.0
"""Capture every authoritative SoViewer tab using a generated, inert ELF."""
import json
import os
from pathlib import Path
import struct
import sys
import tempfile
import time
import zlib


def fixture_bytes(export='native_export',embedded=True):
    """Small ELF64 shared object with real symbols, relocations and image data."""
    def png_chunk(kind,data):
        return struct.pack('>I',len(data))+kind+data+struct.pack('>I',zlib.crc32(kind+data)&0xffffffff)
    png=b'\x89PNG\r\n\x1a\n'+png_chunk(b'IHDR',struct.pack('>2I5B',2,2,8,2,0,0,0))+png_chunk(b'IDAT',zlib.compress((b'\0'+b'\x22\xaa\x66'*2)*2))+png_chunk(b'IEND',b'')
    names=['','.text','.rodata','.dynstr','.dynsym','.rela.text','.bss','.shstrtab']
    shstr=b'\0';name_offsets={}
    for name in names[1:]:name_offsets[name]=len(shstr);shstr+=name.encode()+b'\0'
    dynstr=b'\0'+export.encode()+b'\0__stack_chk_fail\0local_helper\0'
    local_at=dynstr.index(b'local_helper');import_at=dynstr.index(b'__stack_chk_fail')
    symbols=b'\0'*24+struct.pack('<IBBHQQ',local_at,2,0,1,0x1000,1)+struct.pack('<IBBHQQ',1,0x12,0,1,0x1000,6)+struct.pack('<IBBHQQ',import_at,0x12,0,0,0,0)
    chunks=[b'',b'\xb8\x2a\0\0\0\xc3',b'/system/lib64/libnative.so\0/vendor/etc/native/config.xml\0NATIVE_ELF_STRING\0',dynstr,symbols,struct.pack('<QQq',0x1000,(3<<32)|7,0),b'',shstr]
    data=bytearray(64+56*2);offsets=[0]
    for chunk in chunks[1:]:
        data.extend(b'\0'*((-len(data))%8));offsets.append(len(data));data.extend(chunk)
    data.extend(b'\0'*((-len(data))%8));shoff=len(data)
    sections=[(0,0,0,0,0,0,0,0,0,0)]
    specs=[(1,6,0x1000,0,0,1,0),(1,2,0x2000,0,0,1,0),(3,2,0x3000,0,0,1,0),(11,2,0x4000,3,2,8,24),(4,2,0x5000,4,1,8,24),(8,3,0x6000,0,0,8,0),(3,0,0,0,0,1,0)]
    for i,(kind,flags,addr,link,info,align,entsize) in enumerate(specs,1):
        sections.append((name_offsets[names[i]],kind,flags,addr,offsets[i],128 if i==6 else len(chunks[i]),link,info,align,entsize))
    for section in sections:data.extend(struct.pack('<IIQQQQIIQQ',*section))
    ident=b'\x7fELF\x02\x01\x01'+b'\0'*9
    data[:64]=struct.pack('<16sHHIQQQIHHHHHH',ident,3,62,1,0x1000,64,shoff,0,64,56,2,64,len(sections),7)
    data[64:120]=struct.pack('<IIQQQQQQ',1,5,0,0,0,len(data),len(data)+128,0x1000)
    data[120:176]=struct.pack('<IIQQQQQQ',0x6474e551,6,0,0,0,0,0,16)
    if embedded:data.extend(png);data.extend(b'\0'*16);data.extend(fixture_bytes('nested_export',False))
    return bytes(data)


def main():
    repo=Path(__file__).resolve().parents[1];artifacts=repo/'build/qtquick_migration/original_so';artifacts.mkdir(parents=True,exist_ok=True)
    source=repo.parent/'rennsandroidkitchen';sys.path.insert(0,str(source));os.chdir(source);sys.dont_write_bytecode=True
    from core.fonts import configure_text_rendering_env,apply_app_font
    configure_text_rendering_env()
    from PySide6.QtCore import QPoint,QTimer,QEventLoop
    from PySide6.QtWidgets import QApplication,QWidget,QVBoxLayout,QLabel,QAbstractButton,QLineEdit,QPlainTextEdit,QTableWidget,QTreeWidget
    from main_ext.class_ThemeUi import theme,_PALETTES
    from core import ui_lang
    app=QApplication([]);app.setQuitOnLastWindowClosed(False);apply_app_font(app)
    theme._name='dark_green';theme._apply_palette(_PALETTES['dark_green']);ui_lang.init()
    from main_ext.class_SoViewer import SoViewer,EmbeddedFileDialog,PathStringDialog,CompareSymbolsDialog
    from core import so_core
    def wait(ms):
        loop=QEventLoop();QTimer.singleShot(ms,loop.quit);loop.exec()
    def capture(widget,name):
        wait(100);widget.grab().save(str(artifacts/(name+'.png')));rows=[]
        for child in [widget,*widget.findChildren(QWidget)]:
            if not child.isVisibleTo(widget):continue
            p=child.mapTo(widget,QPoint());row={'class':type(child).__name__,'rect':[p.x(),p.y(),child.width(),child.height()],'font':child.font().toString()}
            if isinstance(child,(QLabel,QAbstractButton,QLineEdit)):row['text']=child.text()
            if isinstance(child,QPlainTextEdit):row['text']=child.toPlainText()[:200]
            if isinstance(child,(QTableWidget,QTreeWidget)):
                row['columns']=[child.columnWidth(n) for n in range(child.columnCount())]
                if isinstance(child,QTableWidget):row['rowHeight']=child.rowHeight(0);row['gutter']=child.verticalHeader().width()
            rows.append(row)
        (artifacts/(name+'.json')).write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding='utf-8')
    with tempfile.TemporaryDirectory(prefix='rak-so-reference-') as temporary:
        path=Path(temporary)/'fixture.so';path.write_bytes(fixture_bytes())
        host=QWidget();host.setStyleSheet(f'background-color:{theme.BG3};');host.resize(1080,650)
        layout=QVBoxLayout(host);layout.setContentsMargins(0,0,0,0);viewer=SoViewer(str(path));layout.addWidget(viewer);host.show()
        deadline=time.monotonic()+15
        while viewer.has_pending_task() and time.monotonic()<deadline:wait(20)
        assert viewer.result is not None
        for i,name in enumerate(('overview','symbols','paths','sections','segments','relocations','security','disasm','strings')):
            viewer.tabs.setCurrentIndex(i);capture(viewer,name)
        for i,ef in enumerate(viewer.result['embedded_files']):
            dialog=EmbeddedFileDialog(ef,viewer);dialog.show();capture(dialog,'embedded_'+str(i));dialog.close()
        dialog=PathStringDialog('system/lib64/libnative.so',viewer);dialog.show();capture(dialog,'path_dialog');dialog.close()
        dialog=CompareSymbolsDialog(so_core.compare_symbols(viewer.result,viewer.result),viewer);dialog.show();capture(dialog,'compare_dialog');dialog.close()
        print(json.dumps({'size':[viewer.width(),viewer.height()],'symbols':viewer.result['symbols']['total'],'embedded':len(viewer.result['embedded_files'])}),flush=True)
        host.close();host.deleteLater();wait(100)


if __name__=='__main__':main()
