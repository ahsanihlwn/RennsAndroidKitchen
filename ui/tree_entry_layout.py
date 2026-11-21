# SPDX-License-Identifier: MPL-2.0
"""QFontMetrics geometry from the original NameBadgeDelegate, without widgets."""
from functools import lru_cache
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor,QFont,QFontMetrics

_TONES={"EROFS":("accent_hex",0),"EXT2":("accent_hex",-45),"EXT3":("accent_hex",35),
        "EXT4":("accent_hex",70),"F2FS":("accent_hex",110),"VBMETA":("_gauge_danger",0),
        "SUPER":("_gauge_warn",0),"SPARSE":("_gauge_warn",25),
        "PAYLOAD":("_gauge_safe",-25),"BOOT":("_gauge_safe",-70),"VNDRBOOT":("_gauge_danger",25),
        "BR":("_gauge_danger",-20),"DAT":("_gauge_warn",-30),"DAT.BR":("_gauge_safe",60),
        "OZIP":("_gauge_warn",45),"OFP":("_gauge_warn",45),"OFP_QC":("_gauge_warn",45),"OFP_MTK":("_gauge_warn",45),"OFP_ZIP":("_gauge_warn",45),"OPS":("_gauge_warn",45),
        "KDZ":("_gauge_safe",15),"DZ":("_gauge_safe",15),"UPDATE_APP":("_gauge_safe",15),
        "PAC":("_gauge_safe",15),"CPB":("_gauge_safe",15),"AMLOGIC":("_gauge_warn",-15),
        "ALLWINNER":("_gauge_warn",-15),"ROMFS":("accent_hex",-80),"SQUASHFS":("accent_hex",-80)}


@lru_cache(maxsize=3)
def _metrics(size):
    font=QFont("Poppins");font.setPixelSize(size)
    if size==9:font.setWeight(QFont.Bold)
    font.setHintingPreference(QFont.PreferVerticalHinting)
    return QFontMetrics(font)


@lru_cache(maxsize=4096)
def entry_layout(name,size,badge,symlink,branch,width,origin):
    fm,size_fm,chip_fm=_metrics(13),_metrics(12),_metrics(9)
    has_badge=badge in _TONES
    size_width=size_fm.horizontalAdvance(size) if size else 0
    chip_width=chip_fm.horizontalAdvance(badge)+10 if has_badge else 0
    chip_height=chip_fm.ascent()+4 if has_badge else 0
    reserve=(6+chip_width if has_badge else 0)+(6+size_width if size else 0)+(12 if symlink else 0)+(18 if branch else 0)
    caption=fm.elidedText(name,Qt.ElideRight,max(0,width-origin-reserve-4)) if reserve else fm.elidedText(name,Qt.ElideRight,max(0,width-origin-4))
    end=origin+fm.horizontalAdvance(caption)
    size_x=0;chip_x=0;dot_x=0;branch_x=0
    if size:
        trailing=(6+chip_width if has_badge else 0)+(12 if symlink else 0)+(18 if branch else 0)
        size_x=max(origin,min(end+6,width-1-trailing-size_width-4))
        end=size_x+size_width
    if has_badge:
        trailing=(12 if symlink else 0)+(18 if branch else 0)
        chip_x=max(origin,min(end+6,width-1-trailing-chip_width-4))
        end=chip_x+chip_width
    if symlink:dot_x=end+6;end=dot_x+5
    if branch:branch_x=min(end+14,width-1-7)
    return {"text":caption,"textX":origin+3,"sizeX":size_x,"sizeWidth":size_width,
            "chipX":chip_x,"chipWidth":chip_width,"chipHeight":chip_height,"chipY":(28-chip_height)//2,
            "chipAscent":chip_fm.ascent(),"dotX":dot_x,"branchX":branch_x,"hasBadge":has_badge}


@lru_cache(maxsize=256)
def badge_colors(badge,accent,warning,safe,danger,light):
    token,shift=_TONES.get(badge,("accent_hex",0))
    base=QColor({"accent_hex":accent,"_gauge_warn":warning,"_gauge_safe":safe,"_gauge_danger":danger}[token])
    hue,saturation,value,_=base.getHsvF()
    if hue<0:hue=max(0,QColor(accent).hueF())
    hue=(hue+shift/360)%1
    saturation=max(.14,min(.48,saturation*.7))
    value=min(value,.55) if light else min(.85,max(value,.70))
    foreground=QColor.fromHsvF(hue,saturation,value)
    background=QColor(foreground);background.setAlpha(30 if light else 42)
    return {"foreground":foreground.name(),"background":background.name(QColor.HexArgb)}
