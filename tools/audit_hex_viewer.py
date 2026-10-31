# SPDX-License-Identifier: MPL-2.0
"""Capture the authoritative HexViewer with inert, deterministic binary data."""
import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
import time


def fixture_bytes():
    data=bytearray(18000)
    for offset,text in ((32,b'FIRST_NATIVE_STRING'),(4200,b'SECOND_NATIVE_STRING'),
                        (8200,b'THIRD_NATIVE_STRING'),(16380,b'BOUNDARY_PATTERN')):
        data[offset:offset+len(text)]=text
    data[9000:9020]='WideString'.encode('utf-16le')
    return bytes(data)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--artifacts',default='build/qtquick_migration/original_hex')
    args=parser.parse_args()
    repo=Path(__file__).resolve().parents[1];artifacts=(repo/args.artifacts).resolve();artifacts.mkdir(parents=True,exist_ok=True)
    source=repo.parent/'rennsandroidkitchen';sys.path.insert(0,str(source));os.chdir(source);sys.dont_write_bytecode=True
    from core.fonts import configure_text_rendering_env,apply_app_font
    configure_text_rendering_env()
    from PySide6.QtCore import Qt,QPoint,QTimer,QEventLoop
    from PySide6.QtWidgets import QApplication,QWidget,QVBoxLayout,QLabel,QAbstractButton,QLineEdit,QPlainTextEdit,QTableView
    from main_ext.class_ThemeUi import theme,_PALETTES
    from core import ui_lang
    app=QApplication([]);app.setQuitOnLastWindowClosed(False);apply_app_font(app)
    theme._name='dark_green';theme._apply_palette(_PALETTES['dark_green']);ui_lang.init()
    from main_ext.class_HexViewer import HexViewer
    def wait(ms):
        loop=QEventLoop();QTimer.singleShot(ms,loop.quit);loop.exec()
    with tempfile.TemporaryDirectory(prefix='rak-hex-reference-') as temporary:
        path=Path(temporary)/'fixture.bin';path.write_bytes(fixture_bytes())
        host=QWidget();host.setStyleSheet(f'background-color:{theme.BG3};');host.resize(1080,650)
        layout=QVBoxLayout(host);layout.setContentsMargins(0,0,0,0)
        viewer=HexViewer(str(path),host);layout.addWidget(viewer);host.show()
        deadline=time.monotonic()+15
        while viewer.has_pending_task() and time.monotonic()<deadline:wait(20)
        assert not viewer.has_pending_task()
        for index,name in enumerate(('overview','hex','strings','hashes')):
            viewer.tabs.setCurrentIndex(index);wait(100)
            viewer.grab().save(str(artifacts/(name+'.png')))
            rows=[]
            for widget in [viewer,*viewer.findChildren(QWidget)]:
                if not widget.isVisibleTo(viewer):continue
                point=widget.mapTo(viewer,QPoint())
                row={'class':type(widget).__name__,'rect':[point.x(),point.y(),widget.width(),widget.height()],'font':widget.font().toString()}
                row['fontDetails']={'kerning':widget.font().kerning(),'fixedPitch':widget.font().fixedPitch(),'letterSpacing':widget.font().letterSpacing(),'wordSpacing':widget.font().wordSpacing()}
                if isinstance(widget,(QLabel,QAbstractButton,QLineEdit)):row['text']=widget.text()
                if isinstance(widget,QPlainTextEdit):row['text']=widget.toPlainText()[:200]
                if isinstance(widget,QTableView):row['columns']=[widget.columnWidth(n) for n in range(4)];row['rowHeight']=widget.rowHeight(0)
                rows.append(row)
            (artifacts/(name+'.json')).write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({'size':[viewer.width(),viewer.height()],'strings':len(viewer._strings_cache),'artifacts':str(artifacts)}),flush=True)
        host.close();host.deleteLater();wait(100)


if __name__=='__main__':main()
