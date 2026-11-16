# SPDX-License-Identifier: MPL-2.0
"""Reference QPushButton menu geometry, using QtGui font metrics only."""
from PySide6.QtGui import QFont,QFontMetrics
from PySide6.QtCore import Qt


def menu_metrics(rows,kind='single'):
    left=12 if kind=='mounted' else 14
    right=28 if kind=='single' else left
    width=0;height=8
    for number,row in enumerate(rows):
        if number:height+=1
        height+=1 if row.get('separator') else 28 if row.get('header') else 32
        if row.get('separator'):continue
        font=QFont('Poppins');font.setPixelSize(10 if row.get('header') else 12)
        font.setHintingPreference(QFont.PreferVerticalHinting)
        label_w=QFontMetrics(font).size(Qt.TextShowMnemonic,row.get('label','')).width()
        shortcut_w=0
        if row.get('shortcut'):
            sfont=QFont('Poppins');sfont.setPixelSize(11)
            shortcut_w=QFontMetrics(sfont).size(Qt.TextShowMnemonic,row.get('shortcut','')).width()+24
        width=max(width,label_w+shortcut_w+left+right+8)
    return {'width':max(8,width),'height':height,'left':left,'right':right}
