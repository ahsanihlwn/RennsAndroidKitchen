# SPDX-License-Identifier: MPL-2.0
import os, re, threading

from PySide6.QtWidgets import (
    QDialog, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QScrollArea, QFrame, QLineEdit, QSizePolicy, QApplication,
)
from PySide6.QtCore import (
    Qt, Signal, QThread, QObject, QFileSystemWatcher, QTimer, QSize,
    QRectF, QPoint, QPointF, QMimeData, QElapsedTimer, QPropertyAnimation,
    QEasingCurve, QEvent,
)
from PySide6.QtGui import (
    QColor, QPainter, QPainterPath, QPen, QBrush, QFont, QFontMetrics,
    QDrag, QPixmap, QIcon, QRadialGradient,
)

from main_ext.class_ThemeUi import theme as _th
from core.app_paths import BIN_DIR as BIN_ROOT

print("[MAIN STAGE] ANDROID_TOOLS_DIALOG  (native edition)")

IMG_MIME = "application/x-rak-image"


def _normalize_line(line):
    line = line.strip()
    if not line.startswith("(bootloader)"): return None
    rest = line[len("(bootloader)"):].strip()
    if ":" not in rest: return None
    idx = rest.rindex(":")
    return rest[:idx].strip(), rest[idx+1:].strip()


def _ep():
    return {"size_a":None,"size_b":None,"size":None,"type":"raw","has_slot":False,"is_logical":False}


def _ss(name):
    if name.endswith("_a"): return name[:-2], "a"
    if name.endswith("_b"): return name[:-2], "b"
    return name, None


def parse_fastboot_output(raw):
    r = {"device":"","slot":None,"has_slot":False,"slot_count":0,
         "unlocked":False,"secure":False,"is_userspace":False,
         "serialno":"","partitions":{}}
    p = r["partitions"]
    for line in raw.splitlines():
        kv = _normalize_line(line)
        if not kv: continue
        k, v = kv
        if   k == "product":       r["device"] = v
        elif k == "current-slot":  r["slot"] = v; r["has_slot"] = True
        elif k == "slot-count":
            try: r["slot_count"] = int(v)
            except: pass
        elif k == "unlocked":      r["unlocked"] = (v.lower() == "yes")
        elif k == "secure":        r["secure"]   = (v.lower() == "yes")
        elif k == "is-userspace":  r["is_userspace"] = (v.lower() == "yes")
        elif k == "serialno":      r["serialno"] = v
        elif k.startswith("has-slot:"):
            pn = k[9:]; p.setdefault(pn, _ep())["has_slot"] = (v.lower() == "yes")
        elif k.startswith("is-logical:"):
            pn, _ = _ss(k[11:]); p.setdefault(pn, _ep())
            if v.lower() == "yes": p[pn]["is_logical"] = True
        elif k.startswith("partition-size:"):
            pn, sl = _ss(k[15:]); p.setdefault(pn, _ep())
            try: sz = int(v.strip(), 16)
            except: sz = 0
            if sl == "a":   p[pn]["size_a"] = sz
            elif sl == "b": p[pn]["size_b"] = sz
            else:           p[pn]["size"]   = sz
        elif k.startswith("partition-type:"):
            pn, _ = _ss(k[15:]); p.setdefault(pn, _ep())
            if v: p[pn]["type"] = v
    return r


def _fmt_size(n):
    if n is None: return "?"
    if n == 0:    return "dynamic"
    for u in ("B","KB","MB","GB"):
        if n < 1024: return f"{n:.0f} {u}"
        n /= 1024
    return f"{n:.1f} GB"


def _fmt_sz2(n):

    if n is None: return "?"
    if n == 0:    return "dynamic"
    v = float(n)
    for u in ("B","KB","MB","GB"):
        if v < 1024:
            return (f"{v:.0f} {u}" if u == "B" else f"{v:.2f} {u}")
        v /= 1024
    return f"{v:.2f} GB"


def _parse_size_arg(s: str) -> int:

    s = s.strip()
    if not s: return -1
    if s.startswith("0x") or s.startswith("0X"):
        try: return int(s, 16)
        except: return -1
    m = re.match(r"^([\d.]+)\s*(B|KB|MB|GB)?$", s, re.I)  # noqa
    if not m: return -1
    val  = float(m.group(1))
    unit = (m.group(2) or "B").upper()
    mult = {"B":1,"KB":1024,"MB":1024**2,"GB":1024**3}.get(unit, 1)
    return int(val * mult)


class _FlashJobThread(QThread):
    progress = Signal(int)        # 0-100, -1=indeterminate
    log_line = Signal(str)
    done     = Signal(bool, str)  # success, message

    def __init__(self, program, part_name, img_path, parent=None):
        super().__init__(parent)
        self._program   = program
        self._part_name = part_name
        self._img_path  = img_path

    def run(self):
        import subprocess
        cmd = [self._program, "flash", self._part_name, self._img_path]
        print(f"[FLASH] {cmd}")
        from core.proc_util import CREATE_NO_WINDOW
        try:
            proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, errors="ignore", creationflags=CREATE_NO_WINDOW
            )
            _total_chunks = 0
            _done_chunks  = 0
            for raw_line in proc.stdout:
                line = raw_line.rstrip()
                if not line: continue
                self.log_line.emit(line)
                if "Sending sparse" in line:
                    m = re.search(r"(\d+)/(\d+)", line)
                    if m:
                        cur = int(m.group(1)); _total_chunks = int(m.group(2))
                        pct = max(1, int((cur - 1) / _total_chunks * 100))
                        self.progress.emit(pct)
                    continue
                if line.startswith("Sending") and "sparse" not in line:
                    self.progress.emit(-1); continue
                if "Writing" in line and "OKAY" in line:
                    if _total_chunks > 0:
                        _done_chunks += 1
                        pct = min(99, int(_done_chunks / _total_chunks * 100))
                        self.progress.emit(pct)
                    continue
                if "OKAY" in line and "Writing" not in line: continue
                if "FAILED" in line or "ERROR" in line:
                    proc.wait(); self.done.emit(False, line.strip()); return
            proc.wait()
            if proc.returncode == 0: self.done.emit(True, "Done")
            else: self.done.emit(False, f"exit code {proc.returncode}")
        except Exception as ex:
            self.done.emit(False, str(ex))


class _FbCmdThread(QThread):
    finished = Signal(list, int, str, str)

    def __init__(self, program, args, parent=None):
        super().__init__(parent)
        self._p = program; self._a = args

    def run(self):
        import subprocess
        from core.proc_util import CREATE_NO_WINDOW
        try:
            r = subprocess.run([self._p] + self._a, timeout=15, capture_output=True,
                                creationflags=CREATE_NO_WINDOW)
            self.finished.emit(self._a, r.returncode,
                               r.stdout.decode(errors="ignore"),
                               r.stderr.decode(errors="ignore"))
        except Exception as ex:
            self.finished.emit(self._a, -1, "", str(ex))


def _qc(css_rgba_or_hex, fallback=None):

    s = str(css_rgba_or_hex).strip()
    m = re.match(r"rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*(?:,\s*([\d.]+)\s*)?\)", s)
    if m:
        r, g, b = int(m.group(1)), int(m.group(2)), int(m.group(3))
        a = float(m.group(4)) if m.group(4) is not None else 1.0
        return QColor(r, g, b, max(0, min(255, int(round(a * 255)))))
    c = QColor(s)
    return c if c.isValid() else (fallback or QColor(0, 0, 0))


def _accent(a):      return _qc(_th.accent(a))
def _border_c(a):    return _qc(_th.border_color(a))
def _white(a):       return _qc(_th.white(a))
def _purple(a):      return _qc(_th.purple(a))
def _green(a):       return _qc(_th.green(a))
def _red_alt(a):     return _qc(_th.red_alt(a))
def _orange(a):      return _qc(_th.orange(a))


def _font(px, weight=QFont.Normal):
    f = QFont("Poppins")
    f.setPixelSize(px)
    f.setWeight(weight)
    return f


def _card_qss():
    _hex = _th.BG2.lstrip("#")
    _r, _g, _b = int(_hex[0:2], 16), int(_hex[2:4], 16), int(_hex[4:6], 16)
    return f"""
        QWidget#at_card {{
            background: {_th.panel_rgba(_r, _g, _b, 170)};
            border: 1px solid {_th.border_color(0.22)};
            border-radius: 12px;
        }}
        QWidget#at_card QWidget {{
            background: transparent;
            color: {_th.FG};
            font-family: 'Poppins','Inter','Segoe UI',sans-serif;
            font-size: 12px;
        }}
        QWidget#at_card QLabel {{ background: transparent; border: none; color: {_th.FG}; }}
        QScrollArea {{ background: transparent; border: none; }}
        QScrollBar:vertical {{ background: transparent; width: 4px; margin: 0; border: none; }}
        QScrollBar::handle:vertical {{ background: {_th.SCROLL_HANDLE}; border-radius: 2px; min-height: 20px; }}
        QScrollBar::handle:vertical:hover {{ background: {_th.accent(0.45)}; }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical,
        QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
            height: 0; border: none; background: transparent; }}
        QScrollBar:horizontal {{ background: transparent; height: 4px; margin: 0; border: none; }}
        QScrollBar::handle:horizontal {{ background: {_th.SCROLL_HANDLE}; border-radius: 2px; min-width: 20px; }}
        QScrollBar::handle:horizontal:hover {{ background: {_th.accent(0.45)}; }}
        QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal,
        QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{
            width: 0; border: none; background: transparent; }}
    """


def _ss_reboot(danger=False):
    hov = _th.RED if danger else _th.FG_BLUE_MUTED
    return f"""
        QPushButton {{ background:transparent; border:none; color:{_th.FG3};
            font-size:10px; padding:0 8px; height:22px; border-radius:4px; }}
        QPushButton:hover {{ color:{hov}; background:{_th.border_color(0.05)}; }}
        QPushButton:disabled {{ color:{_th.FG_DISABLED}; background:transparent; }}
    """


def _ss_refresh():
    return f"""
        QPushButton {{ background:{_th.accent(0.10)}; border:1px solid {_th.accent(0.25)};
            color:{_th.accent_hex}; font-size:11px; padding:0 14px; height:26px;
            border-radius:5px; font-weight:500; }}
        QPushButton:hover {{ background:{_th.accent(0.20)}; border-color:{_th.accent(0.50)}; }}
        QPushButton:disabled {{ color:{_th.FG_DISABLED}; background:transparent;
            border-color:{_th.border_color(0.09)}; }}
    """


def _ss_tree_tab(active):
    if active:
        return f"""
            QPushButton {{ background:{_th.border_color(0.08)}; border:1px solid {_th.border_color(0.11)};
                color:{_th.FG}; font-size:11px; padding:0 10px; height:24px; border-radius:5px; }}
        """
    return f"""
        QPushButton {{ background:transparent; border:1px solid transparent;
            color:{_th.FG3}; font-size:11px; padding:0 10px; height:24px; border-radius:5px; }}
        QPushButton:hover {{ color:{_th.FG_BLUE_MUTED}; }}
    """


def _ss_page_tab(active):
    if active:
        return f"""
            QPushButton {{ background:transparent; border:none;
                border-bottom:2px solid {_th.accent_hex}; border-radius:0;
                color:{_th.FG}; font-size:11px; font-weight:600; padding:0 4px; height:38px; }}
        """
    return f"""
        QPushButton {{ background:transparent; border:none;
            border-bottom:2px solid transparent; border-radius:0;
            color:{_th.FG3}; font-size:11px; font-weight:500; padding:0 4px; height:38px; }}
        QPushButton:hover {{ color:{_th.FG_BLUE_MUTED}; border-bottom-color:{_th.accent(0.20)}; }}
    """


def _ss_btn_sm_danger(confirm=False):
    if confirm:
        return f"""
            QPushButton {{ background:{_th.red_alt(0.55)}; border:1px solid {_th.red_alt(0.80)};
                color:#ffffff; font-size:10px; font-weight:600; height:22px; padding:0 8px;
                border-radius:4px; }}
        """
    return f"""
        QPushButton {{ background:{_th.red_alt(0.08)}; border:1px solid {_th.red_alt(0.20)};
            color:{_th.RED}; font-size:10px; font-weight:500; height:22px; padding:0 8px;
            border-radius:4px; }}
        QPushButton:hover {{ background:{_th.red_alt(0.18)}; border-color:{_th.red_alt(0.40)}; }}
    """


def _ss_btn_sm_ghost():
    return f"""
        QPushButton {{ background:transparent; border:1px solid {_th.border_color(0.08)};
            color:{_th.FG3}; font-size:10px; font-weight:500; height:22px; padding:0 8px;
            border-radius:4px; }}
        QPushButton:hover {{ color:{_th.FG_BLUE_MUTED}; background:{_th.border_color(0.06)}; }}
    """


def _ss_btn_create():
    return f"""
        QPushButton {{ background:{_th.border_color(0.06)}; border:1px solid {_th.border_color(0.10)};
            color:{_th.FG_BLUE_MUTED}; font-size:15px; font-weight:300; padding:0;
            border-radius:5px; }}
        QPushButton:hover {{ background:{_th.border_color(0.11)}; color:{_th.FG}; }}
    """


def _ss_fq_primary():
    return f"""
        QPushButton {{ background:{_th.accent(0.10)}; border:1px solid {_th.accent(0.25)};
            color:{_th.accent_hex}; font-size:10px; font-weight:500; height:20px;
            padding:0 10px; border-radius:4px; }}
        QPushButton:hover {{ background:{_th.accent(0.22)}; }}
    """


def _ss_fq_orange():
    return f"""
        QPushButton {{ background:{_th.orange(0.10)}; border:1px solid {_th.orange(0.28)};
            color:{_th.ORANGE}; font-size:10px; font-weight:500; height:20px;
            padding:0 10px; border-radius:4px; }}
        QPushButton:hover {{ background:{_th.orange(0.20)}; }}
    """


def _ss_fq_ghost():
    return f"""
        QPushButton {{ background:transparent; border:1px solid {_th.border_color(0.08)};
            color:{_th.FG3}; font-size:10px; font-weight:500; height:20px;
            padding:0 10px; border-radius:4px; }}
        QPushButton:hover {{ color:{_th.FG_BLUE_MUTED}; background:{_th.border_color(0.06)}; }}
    """


def _ss_close():
    return f"""
        QPushButton {{ background:{_th.border_color(0.05)}; border:1px solid {_th.border_color(0.08)};
            color:{_th.FG_BLUE_MUTED}; font-size:11px; font-weight:500; height:28px;
            padding:0 18px; border-radius:5px; }}
        QPushButton:hover {{ background:{_th.border_color(0.10)}; color:{_th.FG}; }}
    """


def _ss_modal_input():
    return f"""
        QLineEdit {{ background:{_th.border_color(0.05)}; border:1px solid {_th.border_color(0.10)};
            border-radius:5px; color:{_th.FG}; font-size:11px; padding:0 10px; height:32px; }}
        QLineEdit:focus {{ border-color:{_th.accent(0.50)}; }}
    """


def _ss_modal_ok():
    return f"""
        QPushButton {{ background:{_th.accent(0.10)}; border:1px solid {_th.accent(0.25)};
            color:{_th.accent_hex}; font-size:11px; font-weight:500; height:28px;
            padding:0 14px; border-radius:5px; }}
        QPushButton:hover {{ background:{_th.accent(0.22)}; }}
    """


def _ss_modal_cancel():
    return f"""
        QPushButton {{ background:transparent; border:1px solid {_th.border_color(0.08)};
            color:{_th.FG3}; font-size:11px; height:28px; padding:0 14px; border-radius:5px; }}
        QPushButton:hover {{ color:{_th.FG_BLUE_MUTED}; background:{_th.border_color(0.06)}; }}
    """


def _make_check(checked=True):

    b = QPushButton()
    b.setCheckable(True)
    b.setChecked(checked)
    b.setFixedSize(20, 20)
    b.setCursor(Qt.PointingHandCursor)
    b.setIconSize(QSize(13, 13))
    b.setStyleSheet(f"""
        QPushButton {{ background:{_th.border_color(0.05)};
            border:1px solid {_th.border_color(0.14)}; border-radius:10px; }}
        QPushButton:hover {{ border-color:{_th.accent(0.50)}; background:{_th.accent(0.08)}; }}
        QPushButton:checked {{ background:{_th.accent(0.55)}; border:1px solid {_th.accent(0.80)}; }}
        QPushButton:checked:hover {{ background:{_th.accent(0.65)}; border-color:{_th.accent(0.90)}; }}
    """)
    try:
        from icons.handler import icon as _icon
        _ic = QIcon(_icon("check_white"))
    except Exception:
        _ic = QIcon()

    def _toggle_icon(v, btn=b, ic=_ic):
        btn.setIcon(ic if v else QIcon())
    b.toggled.connect(_toggle_icon)
    _toggle_icon(checked)
    return b


def _elide(text, font, width):
    return QFontMetrics(font).elidedText(text, Qt.ElideRight, max(0, int(width)))


def _refresh_icon(color: QColor, px: int = 13) -> QIcon:

    dpr = 2.0
    pm = QPixmap(int(px * dpr), int(px * dpr))
    pm.setDevicePixelRatio(dpr)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    pen = QPen(color, 1.4)
    pen.setCapStyle(Qt.RoundCap)
    p.setPen(pen); p.setBrush(Qt.NoBrush)
    m = 2.0
    rect = QRectF(m, m, px - m * 2, px - m * 2)
    # busur ~285°, sisain celah buat kepala panah
    p.drawArc(rect, int(80 * 16), int(-290 * 16))
    # kepala panah di ujung atas busur
    import math
    cx, cy = rect.center().x(), rect.center().y()
    r = rect.width() / 2
    a = math.radians(80)
    tipx, tipy = cx + r * math.cos(a), cy - r * math.sin(a)
    p.setPen(Qt.NoPen); p.setBrush(QBrush(color))
    path = QPainterPath()
    path.moveTo(tipx + 2.4, tipy - 0.6)
    path.lineTo(tipx - 2.4, tipy - 1.4)
    path.lineTo(tipx - 0.2, tipy + 2.6)
    path.closeSubpath()
    p.drawPath(path)
    p.end()
    return QIcon(pm)


class _DeviceBadge(QWidget):


    def __init__(self, parent=None):
        super().__init__(parent)
        self._text  = "no device"
        self._state = "idle"          # idle | connected | locked
        self.setFixedHeight(20)
        self._font = _font(10)
        # Maximum = sizeHint jadi batas ATAS; boleh nyusut kalau titlebar sempit
        # (teks-nya di-elide di paintEvent) biar ga nabrak tombol reboot.
        self.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        self.setMinimumWidth(46)

    def sizeHint(self):
        return QSize(QFontMetrics(self._font).horizontalAdvance(self._text) + 34, 20)

    def set_info(self, text, state):
        self._text  = text or ""
        self._state = state
        self.updateGeometry()
        self.update()

    def _colors(self):
        if self._state == "connected":
            return _green(0.10), _green(0.25), _qc(_th.GREEN)
        if self._state == "locked":
            return _red_alt(0.10), _red_alt(0.20), _qc(_th.RED_BADGE)
        return _border_c(0.04), _border_c(0.09), _qc(_th.FG3)

    def paintEvent(self, _ev):
        bg, br, fg = self._colors()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(QPen(br, 1)); p.setBrush(QBrush(bg))
        p.drawRoundedRect(r, 5, 5)
        # dot
        p.setPen(Qt.NoPen); p.setBrush(QBrush(fg))
        p.drawEllipse(QRectF(10, self.height() / 2 - 3, 6, 6))
        # text (elide — window bisa lebih sempit dari teks penuh)
        avail = self.width() - 28
        p.setPen(QPen(fg)); p.setFont(self._font)
        p.drawText(QRectF(22, 0, avail, self.height()),
                   Qt.AlignVCenter | Qt.AlignLeft,
                   _elide(self._text, self._font, avail))
        p.end()


class _HSep(QWidget):
    def __init__(self, parent=None, alpha=0.09):
        super().__init__(parent)
        self.setFixedHeight(1)
        self._a = alpha

    def paintEvent(self, _ev):
        p = QPainter(self)
        p.fillRect(self.rect(), _border_c(self._a))
        p.end()


class _VSep(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(1, 18)

    def paintEvent(self, _ev):
        p = QPainter(self)
        p.fillRect(self.rect(), _border_c(0.08))
        p.end()


class _BandRow(QWidget):


    def __init__(self, parent=None, bg=None, border=None, radius=5, dashed=False):
        super().__init__(parent)
        self._bg     = bg
        self._border = border
        self._radius = radius
        self._dashed = dashed

    def set_band(self, bg=None, border=None, dashed=None):
        self._bg = bg; self._border = border
        if dashed is not None: self._dashed = dashed
        self.update()

    def paintEvent(self, _ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        if self._bg is not None:
            p.setPen(Qt.NoPen); p.setBrush(QBrush(self._bg))
            p.drawRoundedRect(r, self._radius, self._radius)
        if self._border is not None:
            pen = QPen(self._border, 1)
            if self._dashed:
                pen.setStyle(Qt.DashLine); pen.setDashPattern([3, 3])
            p.setPen(pen); p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(r, self._radius, self._radius)
        p.end()


class _TypeBadge(QWidget):


    def __init__(self, text, parent=None):
        super().__init__(parent)
        self._text = text or "raw"
        self._font = _font(9)
        fm = QFontMetrics(self._font)
        self.setFixedSize(fm.horizontalAdvance(self._text) + 12, 15)

    def paintEvent(self, _ev):
        raw = (self._text == "raw")
        fg  = _accent(1.0) if raw else _qc(_th.GREEN_BADGE)
        bg  = _accent(0.10) if raw else _green(0.10)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen); p.setBrush(QBrush(bg))
        p.drawRoundedRect(QRectF(self.rect()), 3, 3)
        p.setPen(QPen(fg)); p.setFont(self._font)
        p.drawText(self.rect(), Qt.AlignCenter, self._text)
        p.end()


class _ProgBar(QWidget):


    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(2)
        self._pct   = 0.0
        self._state = ""          # "" | done | error
        self._indet = False
        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self.update)
        self._clock = QElapsedTimer()

    def set_pct(self, pct):
        self._indet = False
        self._state = ""
        self._timer.stop()
        self._pct = max(0.0, min(100.0, float(pct)))
        self.update()

    def set_indeterminate(self, on=True):
        self._indet = bool(on)
        if on:
            self._state = ""
            self._clock.restart()
            self._timer.start()
        else:
            self._timer.stop()
        self.update()

    def set_state(self, state):
        self._state = state or ""
        if state:
            self._indet = False
            self._timer.stop()
            if state == "done": self._pct = 100.0
        self.update()

    def reset(self):
        self._timer.stop()
        self._pct = 0.0; self._state = ""; self._indet = False
        self.update()

    def paintEvent(self, _ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(_border_c(0.06)))
        p.drawRoundedRect(QRectF(0, 0, w, h), 1, 1)

        if self._state == "done":
            p.setBrush(QBrush(_qc(_th.GREEN_DONE)))
            p.drawRoundedRect(QRectF(0, 0, w, h), 1, 1)
        elif self._state == "error":
            fw = max(2.0, w * self._pct / 100.0) if self._pct else w
            p.setBrush(QBrush(_qc(_th.RED_ALT)))
            p.drawRoundedRect(QRectF(0, 0, fw, h), 1, 1)
        elif self._indet:
            seg = w * 0.35
            span = self._clock.elapsed() % 1200 / 1200.0
            x = -seg * 2 + span * (seg * 6)
            p.setBrush(QBrush(_qc(_th.BLUE_MID)))
            p.drawRoundedRect(QRectF(x, 0, seg, h), 1, 1)
        elif self._pct > 0:
            p.setBrush(QBrush(_qc(_th.BLUE_MID)))
            p.drawRoundedRect(QRectF(0, 0, w * self._pct / 100.0, h), 1, 1)
        p.end()


class _ImgItem(QWidget):


    picked = Signal(object)     # img dict

    def __init__(self, img, parent=None):
        super().__init__(parent)
        self.img = img
        self.setFixedHeight(32)
        self.setCursor(Qt.OpenHandCursor)
        self.setToolTip(img.get("path", ""))
        self._sel   = False
        self._hover = False
        self._press = None
        self._f_name = _font(11)
        self._f_size = _font(9)

    def set_selected(self, v):
        if self._sel != v:
            self._sel = v
            self.update()

    def enterEvent(self, _ev): self._hover = True;  self.update()
    def leaveEvent(self, _ev): self._hover = False; self.update()

    def mousePressEvent(self, ev):
        if ev.button() == Qt.LeftButton:
            self._press = ev.position().toPoint()
            self.picked.emit(self.img)

    def mouseMoveEvent(self, ev):
        if self._press is None or not (ev.buttons() & Qt.LeftButton):
            return
        if (ev.position().toPoint() - self._press).manhattanLength() < 8:
            return
        self._start_drag()

    def mouseReleaseEvent(self, _ev):
        self._press = None

    def _start_drag(self):
        self._press = None
        mime = QMimeData()
        mime.setData(IMG_MIME, f"{self.img['name']}\n{self.img['path']}".encode("utf-8"))
        mime.setText(self.img["name"])
        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.setPixmap(self._ghost_pixmap())
        gp = drag.pixmap()
        drag.setHotSpot(QPoint(gp.width() // 2, int(gp.height() * 1.4)))
        drag.exec(Qt.CopyAction)

    def _ghost_pixmap(self):
        f  = _font(10)
        fm = QFontMetrics(f)
        txt = self.img["name"]
        w, h = fm.horizontalAdvance(txt) + 24, 20
        dpr = self.devicePixelRatioF() if hasattr(self, "devicePixelRatioF") else 1.0
        pm = QPixmap(int(w * dpr), int(h * dpr))
        pm.setDevicePixelRatio(dpr)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(0.5, 0.5, w - 1, h - 1)
        p.setPen(QPen(_accent(0.65), 1))
        p.setBrush(QBrush(_accent(0.32)))
        p.drawRoundedRect(r, 5, 5)
        p.setPen(QPen(_qc(_th.FG_BLUE_LIGHT))); p.setFont(f)
        p.drawText(r, Qt.AlignCenter, txt)
        p.end()
        return pm

    def paintEvent(self, _ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        if self._sel:
            p.setPen(QPen(_accent(0.25), 1)); p.setBrush(QBrush(_accent(0.10)))
            p.drawRoundedRect(r, 5, 5)
        elif self._hover:
            p.setPen(Qt.NoPen); p.setBrush(QBrush(_border_c(0.04)))
            p.drawRoundedRect(r, 5, 5)

        size_txt = self.img.get("size", "")
        p.setFont(self._f_size)
        sw = QFontMetrics(self._f_size).horizontalAdvance(size_txt)
        p.setPen(QPen(_qc(_th.FG3)))
        p.drawText(QRectF(self.width() - 10 - sw, 0, sw, self.height()),
                   Qt.AlignVCenter | Qt.AlignRight, size_txt)

        avail = self.width() - 26 - sw
        p.setFont(self._f_name)
        p.setPen(QPen(_qc(_th.FG2)))
        p.drawText(QRectF(10, 0, avail, self.height()), Qt.AlignVCenter | Qt.AlignLeft,
                   _elide(self.img["name"], self._f_name, avail))
        p.end()


class _ImgSidebar(QWidget):


    selected = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedWidth(210)
        self._items = []
        self._sel_img = None

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0); lay.setSpacing(0)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._host = QWidget()
        self._lay  = QVBoxLayout(self._host)
        self._lay.setContentsMargins(8, 8, 8, 8); self._lay.setSpacing(1)
        self._lay.addStretch()
        self._scroll.setWidget(self._host)
        lay.addWidget(self._scroll)

        self.set_images([], "No project loaded.")

    def paintEvent(self, _ev):
        p = QPainter(self)
        p.fillRect(self.rect().adjusted(self.width() - 1, 0, 0, 0), _border_c(0.09))
        p.end()

    def set_images(self, images, empty_msg="No .img files found."):
        while self._lay.count() > 1:
            it = self._lay.takeAt(0)
            w = it.widget()
            if w:
                w.hide()
                w.deleteLater()
        self._items = []
        self._sel_img = None
        self._placeholder = None

        if not images:
            ph = QLabel(empty_msg)
            ph.setAlignment(Qt.AlignCenter)
            ph.setWordWrap(True)
            ph.setStyleSheet(f"color:{_th.FG3}; font-size:11px; padding:20px 12px; background:transparent;")
            self._lay.insertWidget(0, ph)
            self._placeholder = ph
            return

        self.append_images(images)

    def append_images(self, images):
        if not images:
            return
        if self._placeholder is not None:
            self._lay.removeWidget(self._placeholder)
            self._placeholder.hide()
            self._placeholder.deleteLater()
            self._placeholder = None
        for img in images:
            it = _ImgItem(img)
            it.picked.connect(self._on_pick)
            self._lay.insertWidget(self._lay.count() - 1, it)
            self._items.append(it)

    def _on_pick(self, img):
        self._sel_img = img
        for it in self._items:
            it.set_selected(it.img is img)
        self.selected.emit(img)

    def current(self):
        return self._sel_img


NW_, NH_ = 106, 44
DW_, DH_ = 150, 38
VG_, BRANCH_LEN_, PX_, PY_, GAP_ = 18, 72, 20, 20, 12


class _DevBox(QWidget):
    def __init__(self, name, parent=None):
        super().__init__(parent)
        self._name = name
        self.opacity = 0.0
        self._font = _font(12, QFont.Bold)

    def paintEvent(self, _ev):
        if self.opacity <= 0.01: return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setOpacity(self.opacity)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        # glow ring
        p.setPen(QPen(_accent(0.22 * self.opacity), 2)); p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(r.adjusted(-1.5, -1.5, 1.5, 1.5), 9, 9)
        p.setPen(QPen(_accent(0.25), 1)); p.setBrush(QBrush(_accent(0.10)))
        p.drawRoundedRect(r, 8, 8)
        p.setPen(QPen(_qc(_th.FG))); p.setFont(self._font)
        p.drawText(r, Qt.AlignCenter, _elide(self._name, self._font, self.width() - 14))
        p.end()


class _Node(QWidget):


    assign = Signal(str, object)      # node_id, img dict

    def __init__(self, nid, label, size, active, get_selected, parent=None):
        super().__init__(parent)
        self.nid    = nid
        self.label  = label
        self.size   = size
        self.active = active
        self._get_selected = get_selected
        self.opacity  = 0.0
        self._sub     = size
        self._sub_q   = False
        self._state   = ""            # "" | queued | flashing | done | error
        self._hover   = False
        self._dragover = False
        self._flash_until = 0
        self.setAcceptDrops(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip(label)
        self._f_name = _font(10, QFont.DemiBold)
        self._f_sub  = _font(9)

    # state
    def set_state(self, state):
        self._state = state or ""
        self.update()

    def set_queued_img(self, img_name):
        self._sub = "← " + img_name
        self._sub_q = True
        self._state = "queued"
        self.update()

    def reset(self):
        self._sub = self.size
        self._sub_q = False
        self._state = ""
        self.update()

    def flash_border(self):
        self._flash_until = 1
        self.update()
        QTimer.singleShot(160, self._clear_flash)

    def _clear_flash(self):
        self._flash_until = 0
        self.update()

    # interaction
    def enterEvent(self, _ev): self._hover = True;  self.update()
    def leaveEvent(self, _ev): self._hover = False; self.update()

    def mousePressEvent(self, ev):
        if ev.button() != Qt.LeftButton: return
        img = self._get_selected()
        if img: self.assign.emit(self.nid, img)

    def dragEnterEvent(self, ev):
        if ev.mimeData().hasFormat(IMG_MIME):
            ev.acceptProposedAction()
            self._dragover = True
            self.update()

    def dragLeaveEvent(self, _ev):
        self._dragover = False
        self.update()

    def dropEvent(self, ev):
        self._dragover = False
        self.update()
        if not ev.mimeData().hasFormat(IMG_MIME): return
        raw = bytes(ev.mimeData().data(IMG_MIME)).decode("utf-8")
        name, _, path = raw.partition("\n")
        ev.acceptProposedAction()
        self.assign.emit(self.nid, {"name": name, "path": path})

    # paint
    def _skin(self):

        bg = _qc(_th.BG4)
        if self._state == "flashing":
            return _orange(0.78), _qc("#1e1a14"), _orange(0.28), False
        if self._state == "done":
            return _green(0.70), _qc("#131d14"), _green(0.24), False
        if self._state == "error":
            return _red_alt(0.70), _qc("#1d1213"), _red_alt(0.24), False
        if self._dragover:
            return _accent(0.90), _accent(0.06), _accent(0.20), True
        if self._flash_until:
            return _accent(1.0), bg, _accent(0.28), False
        if self._hover:
            return _accent(0.75), bg, _accent(0.24), False
        if self._state == "queued":
            return _border_c(0.25), bg, None, False
        if self.active:
            return _accent(0.50), bg, _accent(0.18), False
        return _purple(0.28), bg, None, False

    def paintEvent(self, _ev):
        if self.opacity <= 0.01: return
        border, bg, glow, dashed = self._skin()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setOpacity(self.opacity)
        r = QRectF(self.rect()).adjusted(2.5, 2.5, -2.5, -2.5)

        if glow is not None:
            g = QColor(glow); g.setAlphaF(glow.alphaF() * 0.9)
            p.setPen(QPen(g, 2)); p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(r.adjusted(-1.5, -1.5, 1.5, 1.5), 8.5, 8.5)

        pen = QPen(border, 1)
        if dashed:
            pen.setStyle(Qt.DashLine); pen.setDashPattern([3, 2])
        p.setPen(pen); p.setBrush(QBrush(bg))
        p.drawRoundedRect(r, 7, 7)

        lbl = self.label if len(self.label) <= 14 else self.label[:12] + "…"
        p.setFont(self._f_name); p.setPen(QPen(_qc(_th.FG)))
        p.drawText(QRectF(r.x() + 4, r.y() + 8, r.width() - 8, 14),
                   Qt.AlignCenter, _elide(lbl, self._f_name, r.width() - 8))

        p.setFont(self._f_sub)
        p.setPen(QPen(_qc(_th.FG2) if self._sub_q else _qc(_th.FG3)))
        p.drawText(QRectF(r.x() + 4, r.y() + 23, r.width() - 8, 12),
                   Qt.AlignCenter, _elide(self._sub, self._f_sub, r.width() - 8))
        p.end()


class _FlowChart(QWidget):


    assign = Signal(str, object)

    def __init__(self, get_selected, parent=None):
        super().__init__(parent)
        self._get_selected = get_selected
        self.nodes  = {}          # nid → _Node
        self._segs  = []          # {"x","y1","y2"}
        self._edges = []          # {"x1","y1","x2","active","node"}
        self._spine_x = self._spine_top = self._spine_bot = 0
        self._devbox  = None
        self._t       = 1.0
        self._fy      = 0.0
        self._clock   = QElapsedTimer()
        self._timer   = QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._tick)
        self.setMinimumSize(10, 10)

    # build
    def build(self, parsed):
        # hide dulu sebelum deleteLater — kalau enggak, node lama masih ke-paint
        # sampai event loop beneran hapus (kelihatan numpuk pas Refresh ke-2).
        for n in list(self.nodes.values()): n.hide(); n.deleteLater()
        self.nodes.clear()
        if self._devbox:
            self._devbox.hide(); self._devbox.deleteLater(); self._devbox = None
        self._segs = []; self._edges = []

        parts    = parsed.get("partitions", {}) or {}
        cur_slot = (parsed.get("slot") or "a").lower()
        device   = parsed.get("device") or "Device"

        # ── kumpulin dynamic partition doang (sama persis logic JS lama) ──
        seen, dual, singles = set(), [], []
        for k, v in parts.items():
            if not v.get("is_logical"): continue
            if k.endswith("_a"):
                base = k[:-2]
                if base in seen: continue
                seen.add(base)
                vb = parts.get(base + "_b")
                if vb and vb.get("is_logical"): dual.append(base)
                else: singles.append(k)
            elif k.endswith("_b"):
                base = k[:-2]
                if base in seen: continue
                seen.add(base)
                va = parts.get(base + "_a")
                if not (va and va.get("is_logical")): singles.append(k)
            else:
                if k not in seen:
                    seen.add(k); singles.append(k)
        dual.sort(); singles.sort()

        col_w    = BRANCH_LEN_ + NW_
        spine_x  = PX_ + col_w + GAP_ / 2
        total_w  = PX_ + col_w + GAP_ + col_w + PX_
        self._spine_x   = spine_x
        self._spine_top = PY_ + DH_
        cur_y = PY_ + DH_ + VG_

        def _add_node(nid, label, size, cx, cy, active):
            n = _Node(nid, label, size, active, self._get_selected, self)
            n.setGeometry(int(cx - NW_ / 2) - 3, int(cy - NH_ / 2) - 3, NW_ + 6, NH_ + 6)
            n.assign.connect(self.assign)
            n.show()
            self.nodes[nid] = n
            return n

        for base in dual:
            pa = parts.get(base + "_a", {})
            pb = parts.get(base + "_b", {})
            sz_a = pa.get("size_a_fmt") or (_fmt_sz2(pa["size_a"]) if pa.get("size_a") is not None else "")
            sz_b = pb.get("size_b_fmt") or (_fmt_sz2(pb["size_b"]) if pb.get("size_b") is not None else "")
            row_cy = cur_y + NH_ / 2
            cx_a = spine_x - GAP_ / 2 - BRANCH_LEN_ - NW_ / 2
            cx_b = spine_x + GAP_ / 2 + BRANCH_LEN_ + NW_ / 2
            seg_y1 = self._spine_top if not self._segs else cur_y - VG_ / 2
            self._segs.append({"x": spine_x, "y1": seg_y1, "y2": row_cy})

            na = _add_node(base + "_a", base + "_a", sz_a, cx_a, row_cy, cur_slot == "a")
            nb = _add_node(base + "_b", base + "_b", sz_b, cx_b, row_cy, cur_slot == "b")
            self._edges.append({"x1": spine_x, "y1": row_cy, "x2": cx_a - NW_ / 2,
                                "active": cur_slot == "a", "node": na})
            self._edges.append({"x1": spine_x, "y1": row_cy, "x2": cx_b + NW_ / 2,
                                "active": cur_slot == "b", "node": nb})
            cur_y += NH_ + VG_

        for pname in singles:
            pp = parts.get(pname, {})
            size = (pp.get("size_fmt") or pp.get("size_a_fmt") or pp.get("size_b_fmt") or
                    (_fmt_sz2(pp["size"]) if pp.get("size") is not None else
                     (_fmt_sz2(pp["size_a"]) if pp.get("size_a") is not None else "")))
            row_cy = cur_y + NH_ / 2
            seg_y1 = self._spine_top if not self._segs else cur_y - VG_ / 2
            self._segs.append({"x": spine_x, "y1": seg_y1, "y2": row_cy})
            _add_node(pname, pname, size, spine_x, row_cy, False)
            cur_y += NH_ + VG_

        self._spine_bot = cur_y - VG_ / 2
        total_h = cur_y + PY_

        self._devbox = _DevBox(device, self)
        self._devbox.setGeometry(int(spine_x - DW_ / 2), PY_, DW_, DH_)
        self._devbox.show()

        self.setFixedSize(int(total_w), int(total_h))
        self._start_anim()

    def clear(self):
        for n in list(self.nodes.values()): n.hide(); n.deleteLater()
        self.nodes.clear()
        if self._devbox:
            self._devbox.hide(); self._devbox.deleteLater(); self._devbox = None
        self._segs = []; self._edges = []
        self._timer.stop()
        self.setFixedSize(10, 10)
        self.update()

    # animation
    def _start_anim(self):
        self._t = 0.0
        self._fy = self._spine_top
        for n in self.nodes.values(): n.opacity = 0.0
        if self._devbox: self._devbox.opacity = 0.0
        self._clock.restart()
        self._timer.start()

    def _tick(self):
        DUR = 2400.0
        self._t = min(1.0, self._clock.elapsed() / DUR)
        t = self._t
        if self._devbox:
            self._devbox.opacity = min(1.0, self._devbox.opacity + 0.09) if t > 0.02 else 0.0
            self._devbox.update()
        if t >= 0.12:
            x = (t - 0.12) / 0.88
            ft = 2 * x * x if x < 0.5 else -1 + (4 - 2 * x) * x
            self._fy = self._spine_top + ft * (self._spine_bot - self._spine_top)
            for e in self._edges:
                n = e["node"]
                if n.opacity >= 1.0: continue
                blen = abs(e["x2"] - e["x1"])
                pct = min(1.0, (self._fy - e["y1"]) / max(1.0, blen * 0.45))
                if pct >= 1.0:
                    n.opacity = min(1.0, n.opacity + 0.12); n.update()
            for nid, n in self.nodes.items():
                if n.opacity >= 1.0: continue
                cx = n.x() + n.width() / 2
                if abs(cx - self._spine_x) < 1.0:
                    cy = n.y() + n.height() / 2
                    if self._fy >= cy + NH_ * 0.6:
                        n.opacity = min(1.0, n.opacity + 0.12); n.update()
        self.update()
        if t >= 1.0:
            # pastikan semua kelar solid
            for n in self.nodes.values():
                if n.opacity < 1.0: n.opacity = 1.0; n.update()
            if self._devbox: self._devbox.opacity = 1.0; self._devbox.update()
            self._timer.stop()

    # paint (garis di belakang node)
    def paintEvent(self, _ev):
        if not self._segs: return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        track = _qc("#373e55")
        lit   = _accent(1.0)

        # spine track
        pen = QPen(track, 1.5); pen.setCapStyle(Qt.SquareCap)
        p.setPen(pen)
        p.drawLine(QPointF(self._spine_x, self._spine_top),
                   QPointF(self._spine_x, self._spine_bot))

        # branch tracks
        pen = QPen(track, 1.2); pen.setCapStyle(Qt.RoundCap)
        p.setPen(pen)
        for e in self._edges:
            p.drawLine(QPointF(e["x1"], e["y1"]), QPointF(e["x2"], e["y1"]))

        fy = self._fy if self._t < 1.0 else self._spine_bot

        # spine lit
        c = QColor(lit); c.setAlphaF(0.90)
        pen = QPen(c, 2); pen.setCapStyle(Qt.RoundCap)
        p.setPen(pen)
        for s in self._segs:
            if fy < s["y1"]: continue
            prog = min(1.0, (fy - s["y1"]) / max(1.0, s["y2"] - s["y1"]))
            p.drawLine(QPointF(s["x"], s["y1"]),
                       QPointF(s["x"], s["y1"] + prog * (s["y2"] - s["y1"])))

        # branch lit
        for e in self._edges:
            if fy < e["y1"]: continue
            blen = abs(e["x2"] - e["x1"])
            pct = min(1.0, (fy - e["y1"]) / max(1.0, blen * 0.45))
            c = QColor(lit); c.setAlphaF(0.88 if e["active"] else 0.22)
            pen = QPen(c, 2 if e["active"] else 1.2); pen.setCapStyle(Qt.RoundCap)
            p.setPen(pen)
            x2 = e["x1"] - pct * blen if e["x2"] < e["x1"] else e["x1"] + pct * blen
            p.drawLine(QPointF(e["x1"], e["y1"]), QPointF(x2, e["y1"]))

        # travelling dot (glow)
        if 0.12 <= self._t < 0.97:
            gr = QRadialGradient(QPointF(self._spine_x, fy), 9)
            gc = QColor(180, 220, 255)
            gc.setAlpha(150); gr.setColorAt(0.0, gc)
            gc2 = QColor(180, 220, 255, 0); gr.setColorAt(1.0, gc2)
            p.setPen(Qt.NoPen); p.setBrush(QBrush(gr))
            p.drawEllipse(QPointF(self._spine_x, fy), 9, 9)
            p.setBrush(QBrush(QColor(180, 220, 255, 235)))
            p.drawEllipse(QPointF(self._spine_x, fy), 4, 4)
        p.end()


class _FlashQueue(QObject):
    enqueued = Signal(int, str, str, bool, int)     # idx, part, img, replaced, total
    progress = Signal(int, str, int, int, int)      # idx, part, pct, glob, total
    job_done = Signal(int, str, bool, str, int)     # idx, part, ok, msg, total
    cleared  = Signal()
    log      = Signal(str)
    all_done = Signal()

    def __init__(self, get_fastboot_fn, parent=None):
        super().__init__(parent)
        self._get_fb = get_fastboot_fn
        self._queue: list[tuple[str, str, str]] = []
        self._thread: _FlashJobThread | None = None
        self._running = False
        self._done_indices: set[int] = set()
        self._stopped_err = False

    def enqueue(self, part_name, img_name, img_path):
        for i, (pn, fn, fp) in enumerate(self._queue):
            if pn == part_name:
                self._queue[i] = (part_name, img_name, img_path)
                self.enqueued.emit(i, part_name, img_name, True, len(self._queue))
                return i, True
        self._queue.append((part_name, img_name, img_path))
        idx = len(self._queue) - 1
        self.enqueued.emit(idx, part_name, img_name, False, len(self._queue))
        return idx, False

    def clear(self):
        if self._running: return False
        self._queue.clear()
        self._done_indices.clear()
        self._running = False
        self._stopped_err = False
        self.cleared.emit()
        return True

    def start_next(self):
        if self._running: return
        self._stopped_err = False
        nxt = self._find_next_queued()
        if nxt == -1:
            self.all_done.emit()
            return
        self._run_job(nxt)

    def skip(self):
        self._stopped_err = False
        self.start_next()

    def _find_next_queued(self):
        for i in range(len(self._queue)):
            if i not in self._done_indices: return i
        return -1

    def _run_job(self, idx):
        pn, fn, fp = self._queue[idx]
        t = _FlashJobThread(self._get_fb(), pn, fp)
        t.progress.connect(lambda pct, i=idx: self._on_progress(i, pct))
        t.log_line.connect(self.log)
        t.done.connect(lambda ok, msg, i=idx: self._on_done(i, ok, msg))
        self._thread  = t
        self._running = True
        t.start()
        self.progress.emit(idx, pn, -1, -1, len(self._queue))

    def _on_progress(self, idx, pct):
        pn    = self._queue[idx][0]
        total = max(1, len(self._queue))
        glob  = int((idx + pct / 100.0) / total * 100) if pct >= 0 else -1
        self.progress.emit(idx, pn, pct, glob, total)

    def _on_done(self, idx, ok, msg):
        pn = self._queue[idx][0]
        self._done_indices.add(idx)
        self._running = False           # MUST clear before start_next
        self.job_done.emit(idx, pn, ok, msg, len(self._queue))
        if ok:
            QTimer.singleShot(200, self.start_next)
        else:
            self._stopped_err = True


class _FqRow(_BandRow):


    def __init__(self, part, img, parent=None):
        super().__init__(parent, radius=4)
        self.setFixedHeight(26)
        self.part = part
        self.img  = img
        self.status = "queued"
        self._msg = ""
        self._f_part   = _font(10, QFont.DemiBold)
        self._f_img    = _font(10)
        self._f_status = _font(9)
        self._apply_band()

    def set_img(self, img):
        self.img = img
        self.update()

    def set_state(self, state, status_text=None, msg=""):
        self.status = state
        if status_text is not None:
            self._status_txt = status_text
        elif state == "done":  self._status_txt = "done"
        elif state == "error": self._status_txt = "err"
        elif state == "queued": self._status_txt = "queued"
        if msg: self.setToolTip(msg)
        self._apply_band()
        self.update()

    def set_status_text(self, txt):
        self._status_txt = txt
        self.update()

    _status_txt = "queued"

    def _apply_band(self):
        if   self.status == "running": self.set_band(bg=_accent(0.10))
        elif self.status == "done":    self.set_band(bg=_green(0.07))
        elif self.status == "error":   self.set_band(bg=_red_alt(0.07))
        else:                          self.set_band(bg=None)

    def _accent_fg(self):
        if self.status == "running": return _accent(1.0)
        if self.status == "done":    return _qc(_th.GREEN)
        if self.status == "error":   return _qc(_th.RED)
        return _qc(_th.FG3)

    def paintEvent(self, ev):
        super().paintEvent(ev)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        fg = self._accent_fg()
        h = self.height()

        p.setPen(QPen(fg)); p.setFont(_font(14))
        p.drawText(QRectF(8, 0, 10, h), Qt.AlignCenter, "·")

        p.setFont(self._f_part); p.setPen(QPen(_qc(_th.FG)))
        p.drawText(QRectF(24, 0, 110, h), Qt.AlignVCenter | Qt.AlignLeft,
                   _elide(self.part, self._f_part, 110))

        st_w = 48
        img_w = self.width() - 24 - 110 - 6 - st_w - 8
        p.setFont(self._f_img); p.setPen(QPen(_qc(_th.FG3)))
        p.drawText(QRectF(140, 0, img_w, h), Qt.AlignVCenter | Qt.AlignLeft,
                   _elide(self.img, self._f_img, img_w))

        p.setFont(self._f_status)
        p.setPen(QPen(fg if self.status in ("running", "done", "error") else _qc(_th.FG3)))
        p.drawText(QRectF(self.width() - st_w - 8, 0, st_w, h),
                   Qt.AlignVCenter | Qt.AlignRight, self._status_txt)
        p.end()


class _FlashQueuePanel(QWidget):


    flash_all = Signal()
    skip      = Signal()
    clear     = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rows = {}
        self.setMaximumHeight(0)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0); root.setSpacing(0)
        root.addWidget(_HSep(self, 0.09))

        inner = QWidget()
        lay = QVBoxLayout(inner)
        lay.setContentsMargins(14, 8, 14, 8); lay.setSpacing(6)
        root.addWidget(inner)

        hdr = QHBoxLayout(); hdr.setSpacing(8)
        title = QLabel("Flash Queue")
        title.setStyleSheet(f"color:{_th.FG_BLUE_MUTED}; font-size:10px; font-weight:700;"
                            f" letter-spacing:0.5px; background:transparent;")
        hdr.addWidget(title)
        self.cur_lbl = QLabel("")
        self.cur_lbl.setStyleSheet(f"color:{_th.FG3}; font-size:10px; background:transparent;")
        hdr.addWidget(self.cur_lbl, 1)

        self.btn_flash = QPushButton("Flash All"); self.btn_flash.setStyleSheet(_ss_fq_primary())
        self.btn_skip  = QPushButton("Skip →");    self.btn_skip.setStyleSheet(_ss_fq_orange())
        self.btn_clear = QPushButton("Clear");     self.btn_clear.setStyleSheet(_ss_fq_ghost())
        for b in (self.btn_flash, self.btn_skip, self.btn_clear):
            b.setCursor(Qt.PointingHandCursor)
            hdr.addWidget(b)
        self.btn_skip.hide()
        self.btn_flash.clicked.connect(self.flash_all)
        self.btn_skip.clicked.connect(self.skip)
        self.btn_clear.clicked.connect(self.clear)
        lay.addLayout(hdr)

        self.bar = _ProgBar()
        lay.addWidget(self.bar)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._scroll.setMaximumHeight(88)
        self._scroll.setFixedHeight(28)
        host = QWidget()
        self._list = QVBoxLayout(host)
        self._list.setContentsMargins(0, 0, 0, 0); self._list.setSpacing(1)
        self._list.addStretch()
        self._scroll.setWidget(host)
        lay.addWidget(self._scroll)

        self._anim = QPropertyAnimation(self, b"maximumHeight", self)
        self._anim.setDuration(250)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)

    def _animate_to(self, h):
        self._anim.stop()
        self._anim.setStartValue(self.maximumHeight())
        self._anim.setEndValue(h)
        self._anim.start()

    def show_panel(self):
        if self.maximumHeight() < 200: self._animate_to(200)

    def hide_panel(self):
        self._animate_to(0)

    # rows
    def add_row(self, idx, part, img, replaced):
        self.show_panel()
        disp = re.sub(r"\.img$", "", img, flags=re.I).split("/")[-1]
        if replaced and idx in self._rows:
            self._rows[idx].set_img(disp)
            self._rows[idx].set_state("queued")
            return
        row = _FqRow(part, disp)
        self._rows[idx] = row
        self._list.insertWidget(self._list.count() - 1, row)
        self._sync_list_height()

    def _sync_list_height(self):
        n = len(self._rows)
        self._scroll.setFixedHeight(min(88, max(28, n * 27)))

    def row(self, idx):
        return self._rows.get(idx)

    def reset(self):
        for r in self._rows.values():
            self._list.removeWidget(r)
            r.hide()
            r.deleteLater()
        self._rows.clear()
        self._sync_list_height()
        self.cur_lbl.setText("")
        self.bar.reset()
        self.btn_flash.show()
        self.btn_skip.hide()
        self.hide_panel()


class _ModalCard(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedWidth(340)

    def paintEvent(self, _ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(QPen(_border_c(0.13), 1))
        p.setBrush(QBrush(_qc(_th.BG2)))
        p.drawRoundedRect(r, 10, 10)
        p.end()


class _ModalHost(QWidget):


    def __init__(self, parent=None):
        super().__init__(parent)
        self.hide()
        self._card = None
        self._on_ok = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setAlignment(Qt.AlignCenter)
        self._lay = lay

    def open_card(self, build_fn, on_ok):
        self.close_card()
        card = _ModalCard(self)
        inner = QVBoxLayout(card)
        inner.setContentsMargins(20, 20, 20, 20)
        inner.setSpacing(0)
        build_fn(card, inner)
        self._card  = card
        self._on_ok = on_ok
        self._lay.addWidget(card)
        self.show()
        self.raise_()

    def close_card(self):
        if self._card:
            self._lay.removeWidget(self._card)
            self._card.hide()
            self._card.deleteLater()
            self._card = None
        self._on_ok = None
        self.hide()

    def accept(self):
        if self._on_ok and self._on_ok(self._card):
            self.close_card()

    def mousePressEvent(self, ev):
        if self._card and not self._card.geometry().contains(ev.position().toPoint()):
            self.close_card()

    def keyPressEvent(self, ev):
        if ev.key() == Qt.Key_Escape:
            self.close_card()
        else:
            super().keyPressEvent(ev)


class AndroidToolsDialog(QDialog):
    tree_batch_ready = Signal(int, int, list)
    tree_scan_finished = Signal(int, int)

    REBOOT_DEFS = {
        "fastboot": [("Bootloader", "bootloader"), ("Fastboot", "fastboot"),
                     ("Recovery", "recovery"),     ("Reboot", "system")],
        "adb":      [("→ Fastboot", "adb:fastboot"), ("→ Bootloader", "adb:bootloader"),
                     ("→ Recovery", "adb:recovery"), ("Reboot", "adb:")],
    }

    def __init__(self, parent, main_win):
        super().__init__(parent)
        self._main          = main_win
        self._fastboot_proc = None
        self._raw_out       = ""
        self._parsed        = {}
        self._cmd_threads: list[_FbCmdThread] = []
        trees = (getattr(main_win, "tree", None), getattr(main_win, "tree2", None))
        self._tree_bases = {
            idx: os.path.normpath(os.fspath(tree.base)) if getattr(tree, "base", None) else None
            for idx, tree in enumerate(trees, start=1)
        }
        b1, b2 = self._tree_bases[1], self._tree_bases[2]
        self._has_two_trees = bool(
            b1 and b2 and os.path.normcase(os.path.abspath(b1)) != os.path.normcase(os.path.abspath(b2))
        )
        self._img_cache: dict[int, list] = {1: [], 2: []}
        self._scan_generation = {1: 0, 2: 0}
        self._scan_cancel = {}
        self._scan_done = set()
        self._loading_started = False
        self._closed = False
        self._active_tree = 1
        self._active_page = 0
        self._drag_pos    = None

        self._watcher = QFileSystemWatcher(self)
        self._watcher.directoryChanged.connect(self._on_dir_changed)
        self._rescan_timers = {}
        for idx in (1, 2):
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.timeout.connect(lambda idx=idx: self._start_tree_scan(idx))
            self._rescan_timers[idx] = timer
        self.tree_batch_ready.connect(self._on_tree_batch)
        self.tree_scan_finished.connect(self._on_tree_scan_finished)
        self.finished.connect(self._cancel_tree_scans)

        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setModal(True)
        self.setMinimumSize(600, 420)
        self._apply_initial_size()
        self._placed = False

        self._build_ui()

        self._fq = _FlashQueue(self._get_fastboot_path, self)
        self._fq.enqueued.connect(self._on_fq_enqueued)
        self._fq.progress.connect(self._on_fq_progress)
        self._fq.job_done.connect(self._on_fq_job_done)
        self._fq.cleared.connect(self._on_fq_cleared)
        self._fq.log.connect(self._log)
        self._fq.all_done.connect(lambda: self._set_status("All queued jobs are done."))

        # theme listener
        self._theme_cb = self._reload_for_theme
        _th.on_change(self._theme_cb)
        self.finished.connect(lambda *_: _th.remove_listener(self._theme_cb))

    # UI BUILD
    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(0)

        try:
            from app_ext.class_BlurOverlay import BlurCard
            _br, _bg, _bb = _th._palette["_border_rgb"]
            tint = QColor(_th.BG2); tint.setAlpha(170)
            self._card = BlurCard(radius=20.0, tint=tint,
                                  border=QColor(_br, _bg, _bb, int(0.22 * 255)),
                                  corner=12.0)
        except Exception:
            self._card = QWidget()
        self._card.setObjectName("at_card")
        self._card.setStyleSheet(_card_qss())
        outer.addWidget(self._card)

        root = QVBoxLayout(self._card)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._build_titlebar())
        root.addWidget(_HSep(self, 0.09))
        root.addWidget(self._build_navbar())
        root.addWidget(_HSep(self, 0.09))

        # ── content: sidebar + pages ──
        content = QWidget()
        c_lay = QHBoxLayout(content)
        c_lay.setContentsMargins(0, 0, 0, 0); c_lay.setSpacing(0)

        self.sidebar = _ImgSidebar()
        self.sidebar.set_images([], "Scanning images..." if self._tree_bases[1] else "No project loaded.")
        c_lay.addWidget(self.sidebar)

        self._pages = QWidget()
        self._pages_lay = QVBoxLayout(self._pages)
        self._pages_lay.setContentsMargins(0, 0, 0, 0); self._pages_lay.setSpacing(0)
        self._page_part  = self._build_partition_page()
        self._page_flash = self._build_flash_page()
        self._pages_lay.addWidget(self._page_part)
        self._pages_lay.addWidget(self._page_flash)
        self._page_flash.hide()
        c_lay.addWidget(self._pages, 1)

        root.addWidget(content, 1)

        # ── flash queue ──
        self.fq_panel = _FlashQueuePanel()
        self.fq_panel.flash_all.connect(lambda: self._fq.start_next())
        self.fq_panel.skip.connect(lambda: self._fq.skip())
        self.fq_panel.clear.connect(self._flash_queue_clear)
        root.addWidget(self.fq_panel)

        # ── footer ──
        root.addWidget(_HSep(self, 0.09))
        root.addWidget(self._build_footer())

        # ── modal host (nutup seluruh card) ──
        self.modal = _ModalHost(self._card)
        self.modal.setGeometry(self._card.rect())
        self._card.installEventFilter(self)

    def eventFilter(self, obj, ev):
        if obj is self._card and ev.type() == QEvent.Type.Resize:
            self.modal.setGeometry(self._card.rect())
        return super().eventFilter(obj, ev)

    # title bar
    def _build_titlebar(self):
        bar = QWidget()
        bar.setFixedHeight(48)
        bar.mousePressEvent   = self._title_press
        bar.mouseMoveEvent    = self._title_move
        bar.mouseReleaseEvent = self._title_release
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(18, 0, 18, 0); lay.setSpacing(10)

        self._app_icon = QLabel()
        self._app_icon.setFixedSize(16, 16)
        self._app_icon.setStyleSheet("background:transparent;")
        self._set_app_icon()
        lay.addWidget(self._app_icon)

        title = QLabel("Android Tools")
        title.setStyleSheet(f"color:{_th.FG}; font-size:13px; font-weight:700;"
                            f" letter-spacing:0.3px; background:transparent;")
        lay.addWidget(title)

        self.badge = _DeviceBadge()
        self.badge.set_info("no device", "idle")
        lay.addWidget(self.badge)
        lay.addStretch()

        self._reboot_btns = []
        for i in range(4):
            b = QPushButton()
            b.setCursor(Qt.PointingHandCursor)
            b.setStyleSheet(_ss_reboot(danger=(i == 3)))
            b.setEnabled(False)
            lay.addWidget(b)
            self._reboot_btns.append(b)
        self._set_reboot_mode("fastboot", enabled=False)
        return bar

    def _set_app_icon(self):
        try:
            from icons.handler import icon as _icon
            pm = QIcon(_icon("android_tools")).pixmap(16, 16)
            if not pm.isNull():
                self._app_icon.setPixmap(pm)
                return
        except Exception:
            pass
        self._app_icon.setText("")

    # ukuran & batas geser
    def _host_rect(self):

        try:
            w = self._main.window() if self._main else None
            if w is not None and w.isVisible():
                return w.frameGeometry()
        except Exception:
            pass
        scr = self.screen() or QApplication.primaryScreen()
        return scr.availableGeometry() if scr else None

    def _apply_initial_size(self):
        base = self._host_rect()
        if base is None or base.width() <= 0:
            self.resize(880, 570)
            return
        w = max(600, min(int(base.width()  * 0.72), 920))
        h = max(420, min(int(base.height() * 0.78), 610))
        # jangan pernah lebih gede dari wadahnya
        self.resize(min(w, base.width()), min(h, base.height()))

    def _clamp_pos(self, pos):

        b = self._host_rect()
        if b is None:
            return pos
        x, y = pos.x(), pos.y()
        lo_x, hi_x = b.left(), b.right()  - self.width()  + 1
        lo_y, hi_y = b.top(),  b.bottom() - self.height() + 1
        # kalau window lebih gede dari wadah, tempel ke pojok kiri-atas wadah
        x = lo_x if hi_x < lo_x else max(lo_x, min(x, hi_x))
        y = lo_y if hi_y < lo_y else max(lo_y, min(y, hi_y))
        return QPoint(x, y)

    def showEvent(self, ev):
        super().showEvent(ev)
        if not getattr(self, "_placed", False):
            self._placed = True
            b = self._host_rect()
            if b is not None:
                self.move(self._clamp_pos(QPoint(
                    b.left() + (b.width()  - self.width())  // 2,
                    b.top()  + (b.height() - self.height()) // 2)))
        if not self._loading_started:
            QTimer.singleShot(0, self._begin_tree_loading)

    def _title_press(self, ev):
        if ev.button() == Qt.LeftButton:
            self._drag_pos = ev.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def _title_move(self, ev):
        if self._drag_pos is not None and (ev.buttons() & Qt.LeftButton):
            self.move(self._clamp_pos(ev.globalPosition().toPoint() - self._drag_pos))

    def _title_release(self, _ev):
        self._drag_pos = None

    # nav bar
    def _build_navbar(self):
        bar = QWidget()
        bar.setFixedHeight(38)
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(18, 0, 18, 0); lay.setSpacing(0)

        b1 = self._tree_bases[1]
        b2 = self._tree_bases[2]

        self.tab_t1 = QPushButton(os.path.basename(b1) if b1 else "Tree 1")
        self.tab_t2 = QPushButton(os.path.basename(b2) if b2 else "Tree 2")
        for i, b in enumerate((self.tab_t1, self.tab_t2), start=1):
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _=False, n=i: self._switch_tree(n))
            lay.addWidget(b)
        self._tree_div = _VSep()
        lay.addWidget(self._tree_div)
        lay.addSpacing(10)

        self.tab_t1.hide(); self.tab_t2.hide(); self._tree_div.hide()
        self._sync_tree_tabs()

        self.tab_part  = QPushButton("Partition Utility")
        self.tab_flash = QPushButton("Flash Tools")
        for i, b in enumerate((self.tab_part, self.tab_flash)):
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _=False, n=i: self._switch_page(n))
            lay.addWidget(b)
            lay.addSpacing(16)
        self._sync_page_tabs()

        lay.addStretch()
        self.btn_refresh = QPushButton("Refresh")
        self.btn_refresh.setCursor(Qt.PointingHandCursor)
        self.btn_refresh.setStyleSheet(_ss_refresh())
        self.btn_refresh.setIconSize(QSize(13, 13))
        self.btn_refresh.setIcon(_refresh_icon(_qc(_th.accent_hex)))
        self.btn_refresh.clicked.connect(self._run_fastboot)
        lay.addWidget(self.btn_refresh)
        return bar

    def _sync_tree_tabs(self):
        self.tab_t1.setStyleSheet(_ss_tree_tab(self._active_tree == 1))
        self.tab_t2.setStyleSheet(_ss_tree_tab(self._active_tree == 2))

    def _sync_page_tabs(self):
        self.tab_part.setStyleSheet(_ss_page_tab(self._active_page == 0))
        self.tab_flash.setStyleSheet(_ss_page_tab(self._active_page == 1))

    def _switch_page(self, idx):
        self._active_page = idx
        self._page_part.setVisible(idx == 0)
        self._page_flash.setVisible(idx == 1)
        self._sync_page_tabs()

    # page 0: partition utility
    def _build_partition_page(self):
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0); lay.setSpacing(0)

        self._part_scroll = QScrollArea()
        self._part_scroll.setWidgetResizable(True)
        self._part_scroll.setFrameShape(QFrame.NoFrame)
        self._part_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        host = QWidget()
        self._part_lay = QVBoxLayout(host)
        self._part_lay.setContentsMargins(16, 0, 16, 12); self._part_lay.setSpacing(2)
        self._part_lay.addStretch()
        self._part_scroll.setWidget(host)
        lay.addWidget(self._part_scroll, 1)

        self._part_log = QLabel("")
        self._part_log.setStyleSheet(f"color:{_th.FG3}; font-size:10px; padding:4px 16px 6px;"
                                     f" background:transparent;")
        self._part_log.hide()
        lay.addWidget(_HSep(self, 0.09))
        lay.addWidget(self._part_log)
        self._part_sep = lay.itemAt(1).widget()
        self._part_sep.hide()

        self._part_placeholder("Connect a device in fastboot mode,\nthen press Refresh.")
        return page

    def _part_clear(self):


        while self._part_lay.count() > 1:
            it = self._part_lay.takeAt(0)
            w = it.widget()
            if w:
                w.hide()
                w.deleteLater()

    def _part_placeholder(self, msg):
        self._part_clear()
        lbl = QLabel(msg)
        lbl.setAlignment(Qt.AlignCenter)
        lbl.setStyleSheet(f"color:{_th.FG3}; font-size:11px; padding:20px 12px;"
                          f" background:transparent;")
        self._part_lay.insertWidget(0, lbl)

    def _part_log_set(self, msg):
        self._part_log.setText(msg or "")
        self._part_log.setVisible(bool(msg))
        self._part_sep.setVisible(bool(msg))

    # page 1: flash tools
    def _build_flash_page(self):
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0); lay.setSpacing(0)

        self._chart_scroll = QScrollArea()
        self._chart_scroll.setWidgetResizable(False)
        self._chart_scroll.setFrameShape(QFrame.NoFrame)
        self._chart_scroll.setAlignment(Qt.AlignHCenter | Qt.AlignTop)

        self.chart = _FlowChart(self.sidebar.current)
        self.chart.assign.connect(self._on_node_assign)
        self._chart_scroll.setWidget(self.chart)
        lay.addWidget(self._chart_scroll, 1)

        self._chart_status = QLabel("Connect a device in fastboot mode, then press Refresh.")
        self._chart_status.setAlignment(Qt.AlignCenter)
        self._chart_status.setStyleSheet(f"color:{_th.FG3}; font-size:11px; padding:20px;"
                                         f" background:transparent;")
        self._chart_status.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        lay.insertWidget(0, self._chart_status)
        self._chart_scroll.hide()
        return page

    def _chart_show_status(self, msg):
        self._chart_status.setText(msg)
        self._chart_status.show()
        self._chart_scroll.hide()

    # footer
    def _build_footer(self):
        bar = QWidget()
        bar.setFixedHeight(44)
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(18, 0, 18, 0); lay.setSpacing(8)
        self._footer_log = QLabel("Ready.")
        self._footer_log.setStyleSheet(f"color:{_th.FG3}; font-size:10px; background:transparent;")
        lay.addWidget(self._footer_log, 1)
        btn = QPushButton("Close")
        btn.setCursor(Qt.PointingHandCursor)
        btn.setStyleSheet(_ss_close())
        btn.clicked.connect(self.reject)
        lay.addWidget(btn)
        return bar

    # PARTITION PANEL BUILD
    def _build_partition_panel(self, parsed):
        self._part_clear()
        parts    = parsed.get("partitions", {}) or {}
        has_slot = bool(parsed.get("has_slot"))
        cur_slot = (parsed.get("slot") or "a").lower()

        log_map, phys_map = {}, {}
        for k, v in parts.items():
            if not v.get("is_logical"):
                phys_map[k] = v
                continue
            log_map[k] = {
                "szA": v.get("size_a") if v.get("size_a") is not None else (v.get("size") or 0),
                "szB": v.get("size_b") or 0,
                "isSlotted": bool(v.get("has_slot")),
            }

        super_raw = (parts.get("super") or {}).get("size") or 0
        log_sum   = sum((v["szA"] or 0) for v in log_map.values())

        row = 0
        log_names = sorted(log_map.keys())
        if log_names:
            info = ""
            super_sz = super_raw or log_sum
            if super_sz:
                info = "super  " + _fmt_sz2(super_sz)
                if super_raw:
                    ua = max(0, super_raw - log_sum)
                    if ua > 0: info += "   ·   unallocated  " + _fmt_sz2(ua)
            self._part_lay.insertWidget(row, self._mk_section_hdr(
                "Logical Partitions", info, kind="logical",
                on_create=lambda: self._open_create_modal(cur_slot, has_slot)))
            row += 1

            for pname in log_names:
                inf = log_map[pname]
                disp = (inf["szB"] if (inf["isSlotted"] and cur_slot == "b") else inf["szA"])
                self._part_lay.insertWidget(row, self._mk_log_row(
                    pname, disp, cur_slot, inf["isSlotted"]))
                row += 1

            if super_raw:
                ua = max(0, super_raw - log_sum)
                if ua > 0:
                    self._part_lay.insertWidget(row, self._mk_unalloc_row(ua)); row += 1

        phys_names = sorted(phys_map.keys())
        if phys_names:
            self._part_lay.insertWidget(row, self._mk_section_hdr(
                "PHYSICAL PARTITIONS", "", kind="physical")); row += 1
            for pname in phys_names:
                self._part_lay.insertWidget(row, self._mk_phys_row(
                    pname, phys_map[pname], has_slot, cur_slot)); row += 1

        if not log_names and not phys_names:
            self._part_placeholder("No partition info returned by the device.")

    def _mk_section_hdr(self, title, info, kind="logical", on_create=None):
        w = QWidget()
        w.setFixedHeight(30)
        lay = QHBoxLayout(w)
        lay.setContentsMargins(0, 8, 0, 0); lay.setSpacing(8)
        lbl = QLabel(title)
        if kind == "logical":
            lbl.setStyleSheet(f"color:{_th.PURPLE}; font-size:10px; font-weight:700;"
                              f" letter-spacing:0.4px; background:transparent;")
        else:
            lbl.setStyleSheet(f"color:{_th.FG3}; font-size:9px; font-weight:700;"
                              f" letter-spacing:1px; background:transparent;")
        lay.addWidget(lbl)
        if info:
            i = QLabel(info)
            i.setStyleSheet(f"color:{_th.FG3}; font-size:10px; background:transparent;")
            lay.addWidget(i)
        lay.addStretch()
        if on_create:
            b = QPushButton("+")
            b.setFixedSize(22, 22)
            b.setCursor(Qt.PointingHandCursor)
            b.setToolTip("Create logical partition")
            b.setStyleSheet(_ss_btn_create())
            b.clicked.connect(lambda: on_create())
            lay.addWidget(b)
        return w

    def _mk_log_row(self, pname, sz, cur_slot, is_slotted):
        row = _BandRow(bg=_border_c(0.02), border=_border_c(0.05))
        row.setFixedHeight(34)
        lay = QHBoxLayout(row)
        lay.setContentsMargins(10, 0, 10, 0); lay.setSpacing(8)

        name = QLabel(pname)
        name.setStyleSheet(f"color:{_th.FG}; font-size:11px; background:transparent;")
        lay.addWidget(name, 1)

        if is_slotted:
            chip = QLabel(f"{cur_slot.upper()}  {_fmt_sz2(sz)}")
            chip.setStyleSheet(f"color:{_th.accent_hex}; font-size:10px; background:transparent;")
        else:
            chip = QLabel(_fmt_sz2(sz))
            chip.setStyleSheet(f"color:{_th.FG3}; font-size:10px; background:transparent;")
        lay.addWidget(chip)

        dele = QPushButton("Delete")
        dele.setCursor(Qt.PointingHandCursor)
        dele.setStyleSheet(_ss_btn_sm_danger(False))
        dele.setProperty("confirm", False)
        lay.addWidget(dele)

        rez = QPushButton("Resize")
        rez.setCursor(Qt.PointingHandCursor)
        rez.setStyleSheet(_ss_btn_sm_ghost())
        rez.clicked.connect(lambda: self._open_resize_modal(pname, sz, is_slotted, cur_slot))
        lay.addWidget(rez)

        def _reset_confirm():
            if dele.property("confirm"):
                dele.setProperty("confirm", False)
                dele.setText("Delete")
                dele.setStyleSheet(_ss_btn_sm_danger(False))

        def _on_delete():
            if not dele.property("confirm"):
                dele.setProperty("confirm", True)
                dele.setText("Confirm?")
                dele.setStyleSheet(_ss_btn_sm_danger(True))
                QTimer.singleShot(3000, _reset_confirm)
                return
            self._delete_partition(pname, is_slotted)
            final = f"{pname}_{cur_slot}" if is_slotted else pname
            self._part_log_set("→  fastboot delete-logical-partition " + final)
            row.setEnabled(False)
            row.set_band(bg=_border_c(0.01), border=_border_c(0.03))

        dele.clicked.connect(_on_delete)
        return row

    def _mk_unalloc_row(self, sz):
        row = _BandRow(bg=_qc("rgba(28,32,48,0.7)"), border=_border_c(0.07), dashed=True)
        row.setFixedHeight(34)
        lay = QHBoxLayout(row)
        lay.setContentsMargins(10, 0, 10, 0); lay.setSpacing(8)

        dot = QWidget(); dot.setFixedSize(8, 8)

        def _paint(_ev, w=dot):
            p = QPainter(w)
            p.setRenderHint(QPainter.Antialiasing)
            p.setPen(QPen(_border_c(0.15), 1.5)); p.setBrush(Qt.NoBrush)
            p.drawEllipse(QRectF(0.75, 0.75, 6.5, 6.5))
            p.end()
        dot.paintEvent = _paint
        lay.addWidget(dot)

        lbl = QLabel("Unallocated")
        f = lbl.font(); f.setItalic(True); lbl.setFont(f)
        lbl.setStyleSheet(f"color:{_th.FG3}; font-size:11px; background:transparent;")
        lay.addWidget(lbl, 1)

        sz_lbl = QLabel(_fmt_sz2(sz))
        sz_lbl.setStyleSheet(f"color:{_th.FG3}; font-size:10px; background:transparent;")
        lay.addWidget(sz_lbl)
        return row

    def _mk_phys_row(self, pname, pinfo, has_slot, cur_slot):
        row = _BandRow(bg=_border_c(0.012), border=_border_c(0.04))
        row.setFixedHeight(30)
        lay = QHBoxLayout(row)
        lay.setContentsMargins(10, 0, 10, 0); lay.setSpacing(8)

        name = QLabel(pname)
        name.setStyleSheet(f"color:{_th.FG2}; font-size:11px; background:transparent;")
        lay.addWidget(name, 1)

        if has_slot and pinfo.get("has_slot"):
            act = pinfo.get("size_b") if cur_slot == "b" else pinfo.get("size_a")
            chip = QLabel(f"{cur_slot.upper()}  {_fmt_sz2(act)}")
            chip.setStyleSheet(f"color:{_th.accent_hex}; font-size:10px; background:transparent;")
        else:
            v = pinfo.get("size") if pinfo.get("size") is not None else pinfo.get("size_a")
            chip = QLabel(_fmt_sz2(v))
            chip.setStyleSheet(f"color:{_th.FG3}; font-size:10px; background:transparent;")
        lay.addWidget(chip)
        lay.addWidget(_TypeBadge(pinfo.get("type") or "raw"))
        return row

    # MODALS
    def _open_resize_modal(self, pname, cur_sz, is_slotted, cur_slot):
        target = f"{pname}_{cur_slot or 'a'}" if is_slotted else pname
        cur_txt = _fmt_sz2(cur_sz).replace(" ", "")

        def _build(card, lay):
            t = QLabel("Resize Partition")
            t.setStyleSheet(f"color:{_th.FG}; font-size:13px; font-weight:600; background:transparent;")
            lay.addWidget(t); lay.addSpacing(12)

            l = QLabel(f"New size for <b style='color:{_th.FG}'>{target}</b>:")
            l.setTextFormat(Qt.RichText)
            l.setStyleSheet(f"color:{_th.FG2}; font-size:11px; background:transparent;")
            lay.addWidget(l); lay.addSpacing(6)

            inp = QLineEdit()
            inp.setObjectName("m_size")
            inp.setPlaceholderText(f"e.g. {cur_txt} or 1.5 GB or 0x4000000")
            inp.setStyleSheet(_ss_modal_input())
            inp.returnPressed.connect(self.modal.accept)
            lay.addWidget(inp)
            lay.addSpacing(14)
            lay.addLayout(self._modal_btns(card, "Resize"))
            QTimer.singleShot(60, inp.setFocus)

        def _ok(card):
            inp = card.findChild(QLineEdit, "m_size")
            v = inp.text().strip() if inp else ""
            if not v: return False
            self._resize_partition(target, v)
            self._part_log_set(f"→  fastboot resize-logical-partition {target} {v}")
            return True

        self.modal.open_card(_build, _ok)

    def _open_create_modal(self, cur_slot, has_slot):
        state = {}

        def _build(card, lay):
            t = QLabel("Create Logical Partition")
            t.setStyleSheet(f"color:{_th.FG}; font-size:13px; font-weight:600; background:transparent;")
            lay.addWidget(t); lay.addSpacing(12)

            l1 = QLabel("Partition name:")
            l1.setStyleSheet(f"color:{_th.FG2}; font-size:11px; background:transparent;")
            lay.addWidget(l1); lay.addSpacing(6)
            name = QLineEdit(); name.setObjectName("m_name")
            name.setPlaceholderText("e.g. my_partition")
            name.setStyleSheet(_ss_modal_input())
            lay.addWidget(name)
            lay.addSpacing(8)

            l2 = QLabel("Size:")
            l2.setStyleSheet(f"color:{_th.FG2}; font-size:11px; background:transparent;")
            lay.addWidget(l2); lay.addSpacing(6)
            size = QLineEdit(); size.setObjectName("m_size")
            size.setPlaceholderText("bytes: 0  131072  or  1.5 GB  0x20000000")
            size.setStyleSheet(_ss_modal_input())
            size.returnPressed.connect(self.modal.accept)
            lay.addWidget(size)
            lay.addSpacing(8)

            crow = QHBoxLayout(); crow.setSpacing(8)
            chk = _make_check(has_slot)
            crow.addWidget(chk)
            cl = QLabel("Auto-append slot suffix")
            cl.setStyleSheet(f"color:{_th.FG2}; font-size:11px; background:transparent;")
            crow.addWidget(cl); crow.addStretch()
            lay.addLayout(crow)

            hint = QLabel(" ")
            hint.setMinimumHeight(16)
            hint.setStyleSheet(f"color:{_th.FG3}; font-size:10px; background:transparent;")
            lay.addWidget(hint)

            state["name"], state["size"], state["chk"] = name, size, chk

            def _upd():
                raw = name.text().strip()
                if not raw:
                    hint.setText(" "); return
                if chk.isChecked() and has_slot:
                    sfx = "_" + cur_slot
                    final = raw if raw.endswith(sfx) else raw + sfx
                    hint.setText("Will create:  " + final)
                else:
                    hint.setText("Will create:  " + raw)
            name.textChanged.connect(_upd)
            chk.toggled.connect(_upd)

            lay.addSpacing(14)
            lay.addLayout(self._modal_btns(card, "Create"))
            QTimer.singleShot(60, name.setFocus)

        def _ok(_card):
            nm = state["name"].text().strip()
            sz = state["size"].text().strip()
            if not nm or sz == "": return False
            self._create_partition(nm, sz, state["chk"].isChecked())
            return True

        self.modal.open_card(_build, _ok)

    def _modal_btns(self, card, ok_text):
        row = QHBoxLayout(); row.setSpacing(8); row.addStretch()
        cancel = QPushButton("Cancel")
        cancel.setCursor(Qt.PointingHandCursor)
        cancel.setStyleSheet(_ss_modal_cancel())
        cancel.clicked.connect(self.modal.close_card)
        row.addWidget(cancel)
        ok = QPushButton(ok_text)
        ok.setCursor(Qt.PointingHandCursor)
        ok.setStyleSheet(_ss_modal_ok())
        ok.clicked.connect(self.modal.accept)
        row.addWidget(ok)
        return row

    # TREE / FS HELPERS
    def _begin_tree_loading(self):
        if self._closed or self._loading_started:
            return
        self._loading_started = True
        if self._has_two_trees:
            self.tab_t1.show()
            self._tree_div.show()
        self._start_tree_scan(1)
        if self._has_two_trees:
            QTimer.singleShot(80, lambda: self._start_tree_scan(2))

    def _switch_tree(self, idx):
        self._active_tree = idx
        self._sync_tree_tabs()
        self._push_images()

    def _push_images(self):
        idx = self._active_tree
        empty = "No project loaded." if not self._tree_bases[idx] else (
            "No .img files found." if idx in self._scan_done else "Scanning images..."
        )
        self.sidebar.set_images(self._img_cache[idx], empty)

    def _start_tree_scan(self, idx):
        if self._closed:
            return
        old_cancel = self._scan_cancel.get(idx)
        if old_cancel is not None:
            old_cancel.set()
        self._scan_generation[idx] += 1
        generation = self._scan_generation[idx]
        self._scan_done.discard(idx)
        self._img_cache[idx] = []
        if idx == self._active_tree:
            self._push_images()
        base = self._tree_bases[idx]
        if not base:
            self._on_tree_scan_finished(idx, generation)
            return
        cancel = threading.Event()
        self._scan_cancel[idx] = cancel

        def scan():
            batch = []
            sent_first = False
            try:
                for image in self._scan_imgs(base, cancel):
                    batch.append(image)
                    if not sent_first or len(batch) >= 20:
                        self.tree_batch_ready.emit(idx, generation, batch)
                        batch = []
                        sent_first = True
                        if cancel.wait(0.01):
                            return
            except (OSError, RuntimeError):
                pass
            finally:
                if not cancel.is_set():
                    try:
                        if batch:
                            self.tree_batch_ready.emit(idx, generation, batch)
                        self.tree_scan_finished.emit(idx, generation)
                    except RuntimeError:
                        pass

        threading.Thread(target=scan, name=f"android-tools-tree-{idx}", daemon=True).start()

    def _on_tree_batch(self, idx, generation, images):
        if self._closed or generation != self._scan_generation[idx]:
            return
        self._img_cache[idx].extend(images)
        if idx == 2 and self._has_two_trees:
            self.tab_t2.show()
        if idx == self._active_tree:
            self.sidebar.append_images(images)

    def _on_tree_scan_finished(self, idx, generation):
        if self._closed or generation != self._scan_generation[idx]:
            return
        self._scan_done.add(idx)
        if idx == 2 and self._has_two_trees:
            self.tab_t2.show()
        if idx == self._active_tree and not self._img_cache[idx]:
            self._push_images()
        base = self._tree_bases[idx]
        if base and os.path.isdir(base) and base not in self._watcher.directories():
            self._watcher.addPath(base)

    def _on_dir_changed(self, path):
        changed = os.path.normcase(os.path.abspath(path))
        for idx, base in self._tree_bases.items():
            if base and os.path.normcase(os.path.abspath(base)) == changed:
                self._rescan_timers[idx].start(200)

    @staticmethod
    def _scan_imgs(base, cancel):
        skip = {"config", "neocat_config", "automation", ".git", "neobranch"}
        for root, dirs, files in os.walk(base):
            if cancel.is_set():
                return
            dirs[:] = sorted(
                (d for d in dirs if d.casefold() not in skip and
                 not os.path.islink(os.path.join(root, d)) and
                 not (hasattr(os.path, "isjunction") and os.path.isjunction(os.path.join(root, d)))),
                key=str.casefold,
            )
            for name in sorted(files, key=str.casefold):
                if cancel.is_set():
                    return
                if not name.lower().endswith(".img"):
                    continue
                full = os.path.join(root, name)
                if os.path.islink(full):
                    continue
                try:
                    size = _fmt_size(os.path.getsize(full))
                except OSError:
                    continue
                rel = os.path.relpath(full, base).replace("\\", "/")
                yield {"name": rel[:-4], "path": full, "size": size}

    def _cancel_tree_scans(self, *_args):
        self._closed = True
        for idx in (1, 2):
            self._scan_generation[idx] += 1
            self._rescan_timers[idx].stop()
        for cancel in self._scan_cancel.values():
            cancel.set()

    # FASTBOOT / ADB
    def _get_fastboot_path(self):
        try:
            from core.binary_handler import resolve_binary
            return resolve_binary(BIN_ROOT, "fastboot.exe") or "fastboot"
        except Exception:
            return "fastboot"

    def _get_adb_path(self):
        try:
            from core.binary_handler import resolve_binary
            return resolve_binary(BIN_ROOT, "adb.exe") or "adb"
        except Exception:
            return "adb"

    def _set_refreshing(self, on):
        self.btn_refresh.setEnabled(not on)
        self.btn_refresh.setText("…" if on else "Refresh")
        self.btn_refresh.setIcon(_refresh_icon(
            _qc(_th.FG_DISABLED) if on else _qc(_th.accent_hex)))

    def _run_fastboot(self):
        from PySide6.QtCore import QProcess
        self._set_refreshing(True)
        self._raw_out = ""
        fb = self._get_fastboot_path()
        self._fastboot_proc = QProcess(self)
        self._fastboot_proc.setProcessChannelMode(QProcess.SeparateChannels)
        self._fastboot_proc.readyReadStandardOutput.connect(self._fb_stdout)
        self._fastboot_proc.readyReadStandardError.connect(self._fb_stderr)
        self._fastboot_proc.finished.connect(self._fb_done)
        self._fastboot_proc.errorOccurred.connect(self._fb_error)
        self._fastboot_proc.start(fb, ["getvar", "all"])
        self._refresh_timeout = QTimer(self)
        self._refresh_timeout.setSingleShot(True)
        self._refresh_timeout.timeout.connect(self._fb_timeout)
        self._refresh_timeout.start(5000)

    def _fb_timeout(self):
        if self._fastboot_proc:
            from PySide6.QtCore import QProcess
            if self._fastboot_proc.state() != QProcess.NotRunning:
                self._fastboot_proc.kill()
        self._try_adb_fallback()

    def _fb_stdout(self):
        self._raw_out += self._fastboot_proc.readAllStandardOutput().data().decode(errors="ignore")

    def _fb_stderr(self):
        self._raw_out += self._fastboot_proc.readAllStandardError().data().decode(errors="ignore")

    def _fb_error(self, _err):
        if hasattr(self, "_refresh_timeout"): self._refresh_timeout.stop()
        self._set_status("Error starting fastboot — is it installed?")

    def _fb_done(self, _code, _st):
        if hasattr(self, "_refresh_timeout"): self._refresh_timeout.stop()
        if not self._raw_out.strip():
            self._try_adb_fallback()
            return
        self._parsed = parse_fastboot_output(self._raw_out)

        parts = {}
        for k, v in self._parsed.get("partitions", {}).items():
            pv = dict(v)
            if pv.get("size_a") is not None: pv["size_a_fmt"] = _fmt_size(pv["size_a"])
            if pv.get("size_b") is not None: pv["size_b_fmt"] = _fmt_size(pv["size_b"])
            if pv.get("size")   is not None: pv["size_fmt"]   = _fmt_size(pv["size"])
            parts[k] = pv
        enriched = dict(self._parsed)
        enriched["partitions"] = parts
        self._on_device_data(enriched)

    def _on_device_data(self, parsed):
        self._set_refreshing(False)
        label = parsed.get("device") or "unknown"
        if parsed.get("serialno"): label += "  ·  " + parsed["serialno"]
        if parsed.get("slot"):     label += "  ·  slot " + parsed["slot"]
        label += "  ·  " + ("unlocked" if parsed.get("unlocked") else "locked")
        self.badge.set_info(label, "connected" if parsed.get("unlocked") else "locked")

        self._set_reboot_mode("fastboot", enabled=True)
        self._build_partition_panel(parsed)

        self._chart_status.hide()
        self._chart_scroll.show()
        self.chart.build(parsed)

    # ADB fallback
    def _try_adb_fallback(self):
        self._set_refreshing(True)
        adb = self._get_adb_path()
        t = _FbCmdThread(adb, ["devices"], self)

        def _on_adb_done(_args, _code, stdout, _stderr):
            lines = [l.strip() for l in stdout.splitlines() if l.strip()
                     and not l.startswith("List of") and "\t" in l]
            if not lines:
                self._set_status("No device found (fastboot or adb).")
                return
            serial, mode = lines[0].split("\t", 1)
            self._on_adb_device(serial, mode)
        t.finished.connect(_on_adb_done)
        t.start()
        self._cmd_threads.append(t)

    def _on_adb_device(self, serial, mode):
        self._set_refreshing(False)
        self.badge.set_info(f"{serial or 'unknown'}  ·  adb  ·  {mode or ''}", "connected")
        self._set_reboot_mode("adb", enabled=True)
        self._log("ADB device detected — reboot buttons switched to adb mode.")

    def _run_adb_reboot(self, target):
        adb = self._get_adb_path()
        args = ["reboot"] if not target else ["reboot", target]
        t = _FbCmdThread(adb, args, self)

        def _done(a, code, o, e):
            out = (o + e).strip()
            self._log(f"adb {' '.join(a)}  (code {code})" + (f": {out}" if out else ""))
            if t in self._cmd_threads: self._cmd_threads.remove(t)
        t.finished.connect(_done)
        self._cmd_threads.append(t)
        t.start()

    # reboot / generic cmd
    def _set_reboot_mode(self, mode_key, enabled=True):
        defs = self.REBOOT_DEFS.get(mode_key) or self.REBOOT_DEFS["fastboot"]
        for btn, (label, mode) in zip(self._reboot_btns, defs):
            btn.setText(label)
            if btn.property("wired"):
                try: btn.clicked.disconnect()
                except (RuntimeError, TypeError): pass
            btn.clicked.connect(lambda _=False, m=mode: self._reboot(m))
            btn.setProperty("wired", True)
            btn.setEnabled(enabled)

    def _reboot(self, mode):
        if mode.startswith("adb:"):
            self._run_adb_reboot(mode[4:])
        else:
            args = ["reboot"] if mode == "system" else ["reboot", mode]
            self._run_cmd_thread(args)

    def _run_cmd_thread(self, args, log=""):
        fb = self._get_fastboot_path()
        print(f"[CMD] >>> {fb} {' '.join(str(a) for a in args)}")
        t = _FbCmdThread(fb, args, self)

        def _done(a, code, o, e):
            print(f"[CMD] <<< {a}  code={code}")
            if o.strip(): print(f"[CMD]  stdout: {o.strip()}")
            if e.strip(): print(f"[CMD]  stderr: {e.strip()}")
            if log: self._log(f"done: {log}  (code {code})")
            if t in self._cmd_threads: self._cmd_threads.remove(t)
        t.finished.connect(_done)
        self._cmd_threads.append(t)
        t.start()

    # partition ops
    def _delete_partition(self, pname, is_slotted):
        print(f"[DELETE] called  pname={pname!r}  is_slotted={is_slotted}")
        if is_slotted:
            slot  = (self._parsed.get("slot") or "a").lower()
            final = f"{pname}_{slot}"
            print(f"[DELETE] slotted → slot={slot!r}  final={final!r}")
        else:
            final = pname
            print(f"[DELETE] not slotted → deleting {pname!r}")
        self._run_cmd_thread(["delete-logical-partition", final],
                             log=f"fastboot delete-logical-partition {final}")

    def _resize_partition(self, pname, size_str):
        sz = _parse_size_arg(size_str)
        if sz >= 0:
            self._run_cmd_thread(["resize-logical-partition", pname, str(sz)],
                                 log=f"fastboot resize-logical-partition {pname} {sz}")

    def _create_partition(self, pname, size_str, append_slot):
        sz = _parse_size_arg(size_str)
        if sz < 0: return          # -1 = parse error; 0 = dynamic (valid)
        final = pname
        if append_slot and self._parsed.get("has_slot"):
            slot = (self._parsed.get("slot") or "a").lower()
            sfx = f"_{slot}"
            if not pname.endswith(sfx): final = pname + sfx
        self._run_cmd_thread(["create-logical-partition", final, str(sz)],
                             log=f"fastboot create-logical-partition {final} {sz}")

    # FLASH QUEUE WIRING
    def _on_node_assign(self, node_id, img):
        node = self.chart.nodes.get(node_id)
        if node:
            node.set_queued_img(img["name"])
            node.flash_border()
        self._fq.enqueue(node_id, img["name"], img["path"])

    def _on_fq_enqueued(self, idx, part, img, replaced, _total):
        self.fq_panel.add_row(idx, part, img, replaced)

    def _on_fq_progress(self, idx, part, pct, glob, total):
        row = self.fq_panel.row(idx)
        if not row: return
        node = self.chart.nodes.get(part)
        if node and (pct == -1 or pct > 0):
            node.set_state("flashing")
        row.set_state("running")
        if pct >= 0:
            row.set_status_text(f"{pct}%")
            self.fq_panel.bar.set_pct(glob if glob >= 0 else pct)
        else:
            row.set_status_text("…")
            self.fq_panel.bar.set_indeterminate(True)
        self.fq_panel.cur_lbl.setText(f"{part}  {idx + 1}/{total}" if part else "")

    def _on_fq_job_done(self, idx, part, ok, msg, total):
        row  = self.fq_panel.row(idx)
        node = self.chart.nodes.get(part)
        if ok:
            if row: row.set_state("done")
            if node: node.set_state("done")
            if idx + 1 >= total:
                self.fq_panel.bar.set_state("done")
                self.fq_panel.cur_lbl.setText("All done.")
        else:
            if row: row.set_state("error", msg=msg)
            if node: node.set_state("error")
            self.fq_panel.bar.set_state("error")
            self.fq_panel.cur_lbl.setText("Error: " + part)
            self.fq_panel.btn_flash.hide()
            self.fq_panel.btn_skip.show()
        self._log(f"[FLASH] {part} → " + ("done" if ok else msg))

    def _flash_queue_clear(self):
        self._fq.clear()

    def _on_fq_cleared(self):
        self.fq_panel.reset()
        for n in self.chart.nodes.values():
            n.reset()

    # STATUS / LOG
    def _log(self, msg):
        self._footer_log.setText(msg)

    def _set_status(self, msg):
        self._set_refreshing(False)
        self._chart_show_status(msg)
        self._log(msg)

    # THEME
    def _reload_for_theme(self):

        try:
            self._card.setStyleSheet(_card_qss())
            try:
                _br, _bg, _bb = _th._palette["_border_rgb"]
                tint = QColor(_th.BG2); tint.setAlpha(170)
                self._card._tint   = tint
                self._card._border = QColor(_br, _bg, _bb, int(0.22 * 255))
            except Exception:
                pass
            for i, b in enumerate(self._reboot_btns):
                b.setStyleSheet(_ss_reboot(danger=(i == 3)))
            self.btn_refresh.setStyleSheet(_ss_refresh())
            self.btn_refresh.setIcon(_refresh_icon(_qc(_th.accent_hex)))
            self._set_app_icon()
            self._sync_tree_tabs()
            self._sync_page_tabs()
            self.fq_panel.btn_flash.setStyleSheet(_ss_fq_primary())
            self.fq_panel.btn_skip.setStyleSheet(_ss_fq_orange())
            self.fq_panel.btn_clear.setStyleSheet(_ss_fq_ghost())
            if self._parsed:
                self._build_partition_panel(self._parsed)
            self.update()
            for w in self.findChildren(QWidget):
                w.update()
        except Exception as e:
            print(f"[AndroidTools._reload_for_theme] {e}")

    # LIFECYCLE
    def keyPressEvent(self, ev):
        if ev.key() == Qt.Key_Escape and self.modal.isVisible():
            self.modal.close_card()
            return
        super().keyPressEvent(ev)

    def closeEvent(self, ev):
        self._cancel_tree_scans()
        try:
            if self._fastboot_proc:
                from PySide6.QtCore import QProcess
                if self._fastboot_proc.state() != QProcess.NotRunning:
                    self._fastboot_proc.kill()
        except Exception:
            pass
        super().closeEvent(ev)

    # static opener
    @staticmethod
    def open(parent, main_win):
        from app_ext.class_BlurOverlay import BlurOverlay as _BlurOverlay
        ov = _BlurOverlay(main_win, backdrop="texture") if main_win else None
        dlg = AndroidToolsDialog(parent, main_win)
        if ov:
            dlg._overlay = ov
        try:
            from app_ext.class_BlurOverlay import install_window_fade
            install_window_fade(dlg, fade_in_delay=0, fade_in_ms=0)
        except Exception:
            pass
        dlg.exec()
        if ov: ov.close_overlay()
