# SPDX-License-Identifier: MPL-2.0
import os
import datetime

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QWidget,
    QScrollArea, QFrame, QCheckBox
)
from PySide6.QtCore import Qt, QThread, QObject, Signal, QEventLoop
from PySide6.QtGui import QIcon
from main_ext.class_ThemeUi import theme as _th
from app_ext.class_BlurOverlay import BlurOverlay as _BlurOverlay

print("[MAIN STAGE] CONFLICTDIALOG")

# Icon handler

try:
    from icons.handler import icon as _icon_path
    _ICONS_OK = True
except Exception:
    _ICONS_OK = False

_EXT_ICON = {
    ".png": "image", ".jpg": "image", ".jpeg": "image", ".webp": "image",
    ".bmp": "image", ".gif": "image", ".tiff": "image",
    ".zip": "archive", ".jar": "archive", ".rar": "archive", ".7z": "archive",
    ".tar": "archive", ".gz": "archive", ".tgz": "archive", ".bz2": "archive",
    ".xz": "archive", ".lz": "archive", ".lz4": "archive", ".zst": "archive",
    ".mp3": "icon_audio", ".wav": "icon_audio", ".ogg": "icon_audio",
    ".flac": "icon_audio", ".m4a": "icon_audio",
    ".mp4": "icon_video", ".mkv": "icon_video", ".avi": "icon_video",
    ".mov": "icon_video", ".wmv": "icon_video",
    ".img": "drive", ".bin": "drive", ".iso": "drive",
    ".apk": "apk",
    ".json": "icon_json",
    ".py": "icon_py", ".pyw": "icon_py", ".pyi": "icon_py",
    ".cpp": "icon_cpp", ".cc": "icon_cpp", ".cxx": "icon_cpp",
    ".c": "icon_c",
    ".cs": "icon_cs",
    ".java": "icon_java", ".smali": "icon_java",
    ".js": "icon_js",
    ".html": "icon_html", ".htm": "icon_html",
    ".css": "icon_css",
    ".txt": "txt", ".md": "txt", ".xml": "txt", ".yaml": "txt",
    ".yml": "txt", ".ini": "txt", ".cfg": "txt", ".conf": "txt",
    ".prop": "txt", ".properties": "txt", ".sh": "txt", ".bat": "txt",
    ".log": "txt", ".rc": "txt",
}
_FS_META = ("_file_contexts", "_fs_config", "_fs_options", "_info")


def _get_file_icon(filepath) -> QIcon | None:
    if not _ICONS_OK:
        return None
    name = os.path.basename(filepath)
    ext  = os.path.splitext(filepath)[1].lower()
    if any(name.endswith(s) for s in _FS_META):
        key = "linux_metadata"
    else:
        key = _EXT_ICON.get(ext, "file")
    try:
        return QIcon(_icon_path(key))
    except Exception:
        return None


def _icon_lbl(filepath, size=18, is_folder=False) -> QLabel:
    lbl = QLabel()
    lbl.setFixedSize(size, size)
    lbl.setAlignment(Qt.AlignCenter)
    lbl.setStyleSheet("background:transparent; border:none;")
    try:
        if is_folder:
            ico = QIcon(_icon_path("folder")) if _ICONS_OK else None
        else:
            ico = _get_file_icon(filepath)
        if ico:
            lbl.setPixmap(ico.pixmap(size, size))
    except Exception:
        pass
    return lbl


# Style

_BTN = """
    QPushButton {{
        background: {bg};
        border: 1px solid {br};
        border-radius: 6px;
        color: {fg};
        font-size: 12px;
        font-weight: {w};
        padding: 0 14px;
        height: 30px;
    }}
    QPushButton:hover {{ background: {bg_h}; border-color: {br_h}; color: {fg_h}; }}
    QPushButton:pressed {{ background: {bg_p}; }}
"""

def _style(v):
    if v == "danger":
        return _BTN.format(
            bg=_th.red(0.18),  br=_th.red(0.38),  fg=_th.RED_LIGHT, w=600,
            bg_h=_th.red(0.32), br_h=_th.red(0.60), fg_h=_th.RED_LIGHTER,
            bg_p=_th.red(0.48))
    if v == "primary":
        return _BTN.format(
            bg=_th.accent(0.18), br=_th.accent(0.38), fg=_th.FG_BLUE, w=600,
            bg_h=_th.accent(0.32), br_h=_th.accent(0.60), fg_h=_th.FG_BLUE_LIGHT,
            bg_p=_th.accent(0.48))
    return _BTN.format(
        bg=_th.border_color(0.06), br=_th.border_color(0.10), fg=_th.FG3, w=400,
        bg_h=_th.border_color(0.10), br_h=_th.border_color(0.18), fg_h=_th.FG_HOVER,
        bg_p=_th.border_color(0.14))

def _active_danger():
    return _BTN.format(
        bg=_th.red(0.32),  br=_th.red(0.72),  fg=_th.RED_LIGHTER, w=700,
        bg_h=_th.red(0.40), br_h=_th.red(0.85), fg_h=_th.RED_LIGHTER,
        bg_p=_th.red(0.50))

def _active_neutral():
    return _BTN.format(
        bg=_th.border_color(0.12), br=_th.border_color(0.28), fg=_th.FG_LABEL, w=600,
        bg_h=_th.border_color(0.16), br_h=_th.border_color(0.40), fg_h=_th.FG_BRIGHT,
        bg_p=_th.border_color(0.20))

def _box_style():
    # Convert _th.BG2 → rgba untuk efek kaca di atas BlurOverlay
    _hex = _th.BG2.lstrip("#")
    _r, _g, _b = int(_hex[0:2], 16), int(_hex[2:4], 16), int(_hex[4:6], 16)
    return f"""
        QWidget#dlg_box {{
            background: {_th.panel_rgba(_r, _g, _b, 170)};
            border: 1px solid {_th.border_color(0.22)};
            border-radius: 14px;
        }}
    """

def _scroll_style():
    return f"""
        QScrollArea {{ border: none; background: transparent; }}
        QScrollBar:vertical {{
            background: transparent; width: 5px; margin: 0;
        }}
        QScrollBar::handle:vertical {{
            background: {_th.border_color(0.14)}; border-radius: 2px; min-height: 20px;
        }}
        QScrollBar::handle:vertical:hover {{ background: {_th.border_color(0.24)}; }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
    """


# Helpers

def _fmt_size(path):
    try:
        sz = os.path.getsize(path)
        for unit in ("B", "KB", "MB", "GB"):
            if sz < 1024:
                return f"{sz:.1f} {unit}"
            sz /= 1024
        return f"{sz:.1f} GB"
    except Exception:
        return "?"

def _fmt_mtime(path):
    try:
        t = os.path.getmtime(path)
        return datetime.datetime.fromtimestamp(t).strftime("%d %b %Y  %H:%M")
    except Exception:
        return "?"

def _lbl(text, color=None, size=12, bold=False):
    if color is None:
        color = _th.FG
    l = QLabel(text)
    l.setStyleSheet(
        f"color:{color}; font-size:{size}px; font-weight:{'600' if bold else '400'};"
        " border:none; background:transparent;"
    )
    l.setWordWrap(True)
    l.setTextFormat(Qt.RichText)
    return l

def _divider():
    d = QFrame()
    d.setFixedHeight(1)
    d.setStyleSheet(f"background: {_th.border_color(0.07)}; border:none;")
    return d

def _btn(label, variant, cb, min_w=None):
    b = QPushButton(label)
    b.setFixedHeight(30)
    b.setMinimumWidth(min_w or max(76, len(label) * 9))
    b.setCursor(Qt.PointingHandCursor)
    b.setStyleSheet(_style(variant))
    b.clicked.connect(cb)
    return b


def _compare_card(src_path, dst_path):

    w = QWidget()
    w.setStyleSheet(f"""
        QWidget {{
            background: {_th.border_color(0.03)};
            border: 1px solid {_th.border_color(0.07)};
            border-radius: 6px;
        }}
    """)
    row = QHBoxLayout(w)
    row.setContentsMargins(10, 8, 10, 8)
    row.setSpacing(12)

    def _col(header, path):
        col = QVBoxLayout()
        col.setSpacing(4)

        h = QLabel(header)
        h.setStyleSheet(
            f"color:{_th.accent_hex}; font-size:10px; font-weight:700;"
            " border:none; background:transparent;")
        col.addWidget(h)

        # icon + nama file
        name_row = QHBoxLayout()
        name_row.setSpacing(5)
        name_row.setContentsMargins(0, 0, 0, 0)
        name_row.addWidget(_icon_lbl(path, size=15))
        nm = QLabel(os.path.basename(path) if path else "—")
        nm.setStyleSheet(
            f"color:{_th.FG_BRIGHT}; font-size:11px; font-weight:600;"
            " border:none; background:transparent;")
        nm.setWordWrap(True)
        name_row.addWidget(nm, 1)
        col.addLayout(name_row)

        # size + mtime
        if path and os.path.isfile(path):
            info = f"{_fmt_size(path)}   ·   {_fmt_mtime(path)}"
        else:
            info = "—"
        meta = QLabel(info)
        meta.setStyleSheet(f"color:{_th.FG_MUTED}; font-size:10px; border:none; background:transparent;")
        col.addWidget(meta)

        cw = QWidget()
        cw.setStyleSheet("background:transparent; border:none;")
        cw.setLayout(col)
        return cw

    row.addWidget(_col("↓  INCOMING", src_path), 1)

    sep = QFrame()
    sep.setFixedWidth(1)
    sep.setStyleSheet(f"background: {_th.border_color(0.09)}; border:none;")
    row.addWidget(sep)

    row.addWidget(_col("📁  EXISTING", dst_path), 1)
    return w


# Base

class _BaseDlg(QDialog):
    def __init__(self, parent, width=430):
        super().__init__(parent)
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setModal(True)
        self.setFixedWidth(width)
        self.result_policy = "cancel"

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        self._box = QWidget()
        self._box.setObjectName("dlg_box")
        self._box.setStyleSheet(_box_style())
        outer.addWidget(self._box)

        self._lay = QVBoxLayout(self._box)
        self._lay.setContentsMargins(20, 18, 20, 16)
        self._lay.setSpacing(12)


# 1. Single File Conflict

class FileConflictDialog(_BaseDlg):
    def __init__(self, filename, src_path=None, dst_path=None,
                 show_bulk=False, folder_context=False, remaining=0,
                 parent=None):
        super().__init__(parent, width=430)

        # Title row: icon + teks
        title_row = QHBoxLayout()
        title_row.setSpacing(8)
        title_row.setContentsMargins(0, 0, 0, 0)
        icon_src = src_path or dst_path or filename
        title_row.addWidget(_icon_lbl(icon_src, size=20))
        title_txt = "File conflict in folder merge" if folder_context else "File already exists"
        title_row.addWidget(_lbl(title_txt, _th.FG_BRIGHT, 13, bold=True), 1)
        self._lay.addLayout(title_row)

        # Subtitle
        msg = f"<b>{filename}</b> already exists in the destination."
        if folder_context and remaining > 1:
            msg += (f"<br><span style='color:{_th.FG_MUTED}; font-size:11px;'>"
                    f"{remaining - 1} more conflict(s) remaining</span>")
        self._lay.addWidget(_lbl(msg, _th.FG, 12))

        # Compare card
        if src_path and dst_path and os.path.isfile(src_path) and os.path.isfile(dst_path):
            self._lay.addWidget(_compare_card(src_path, dst_path))

        self._lay.addWidget(_divider())

        # Checkbox "apply to all" — hanya tampil kalau ada bulk/folder context
        self._apply_all = False
        if show_bulk or folder_context:
            chk_row = QHBoxLayout()
            chk_row.setContentsMargins(0, 0, 0, 0)
            chk_row.setSpacing(0)
            self._chk = QCheckBox("Apply to all remaining files")
            from icons.handler import icon as _icon
            _check_path = _icon("check_white").replace("\\", "/")
            self._chk.setStyleSheet(f"""
                QCheckBox {{
                    color: {_th.FG_BLUE_MUTED};
                    font-size: 11px;
                    spacing: 6px;
                    border: none;
                    background: transparent;
                }}
                QCheckBox::indicator {{
                    width: 14px;
                    height: 14px;
                    border: 1px solid {_th.border_color(0.20)};
                    border-radius: 7px;
                    background: {_th.border_color(0.05)};
                }}
                QCheckBox::indicator:hover {{
                    border-color: {_th.border_color(0.38)};
                }}
                QCheckBox::indicator:checked {{
                    background: {_th.accent(0.55)};
                    border-color: {_th.accent(0.80)};
                    image: url({_check_path});
                }}
                QCheckBox:hover {{ color: {_th.FG}; }}
            """)
            self._chk.stateChanged.connect(lambda s: setattr(self, "_apply_all", bool(s)))
            chk_row.addWidget(self._chk)
            chk_row.addStretch()
            self._lay.addLayout(chk_row)

        # Buttons — hanya Skip + Replace, tanpa Skip All / Replace All
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(_btn("Cancel", "neutral", self._cancel))
        row.addStretch()
        row.addWidget(_btn("Skip",    "neutral", self._skip))
        row.addWidget(_btn("Replace", "danger",  self._replace))

        self._lay.addLayout(row)
        self.adjustSize()

    def _cancel(self):      self.result_policy = "cancel"; self.reject()
    def _skip(self):
        self.result_policy = "skip_all" if self._apply_all else "skip"
        self.accept()
    def _replace(self):
        self.result_policy = "replace_all" if self._apply_all else "replace"
        self.accept()


# 2. Multi File Conflict

class MultiFileConflictDialog(_BaseDlg):


    def __init__(self, conflicts, parent=None):
        super().__init__(parent, width=460)
        self._states = {fname: "replace" for fname, _, _ in conflicts}
        self._btns   = {}
        self.per_file = {}

        self._lay.addWidget(
            _lbl(f"{len(conflicts)} files already exist", _th.FG_BRIGHT, 13, bold=True))
        self._lay.addWidget(
            _lbl("Choose what to do for each file:", _th.FG_BLUE_MUTED, 12))
        self._lay.addWidget(_divider())

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet(_scroll_style())
        scroll.setMaximumHeight(300)

        inner = QWidget()
        inner.setStyleSheet("background: transparent;")
        ilay = QVBoxLayout(inner)
        ilay.setContentsMargins(2, 4, 2, 4)
        ilay.setSpacing(6)

        for fname, src, dst in conflicts:
            card = QWidget()
            card.setStyleSheet(f"""
                QWidget {{
                    background: {_th.border_color(0.03)};
                    border: 1px solid {_th.border_color(0.06)};
                    border-radius: 6px;
                }}
            """)
            clay = QVBoxLayout(card)
            clay.setContentsMargins(10, 8, 10, 8)
            clay.setSpacing(6)

            # Filename + icon
            fname_row = QHBoxLayout()
            fname_row.setSpacing(6)
            fname_row.setContentsMargins(0, 0, 0, 0)
            fname_row.addWidget(_icon_lbl(src or dst or fname, size=15))
            fname_row.addWidget(_lbl(f"<b>{fname}</b>", _th.FG, 12), 1)
            clay.addLayout(fname_row)

            # Compare info compact
            info_row = QHBoxLayout()
            info_row.setSpacing(10)

            def _info_col(header, path, parent_layout=info_row):
                col = QVBoxLayout()
                col.setSpacing(2)
                h = QLabel(header)
                h.setStyleSheet(
                    f"color:{_th.accent_hex}; font-size:10px; font-weight:700;"
                    " border:none; background:transparent;")
                col.addWidget(h)
                if path and os.path.isfile(path):
                    m = QLabel(f"{_fmt_size(path)}  ·  {_fmt_mtime(path)}")
                else:
                    m = QLabel("—")
                m.setStyleSheet(f"color:{_th.FG_MUTED}; font-size:10px; border:none; background:transparent;")
                col.addWidget(m)
                cw = QWidget()
                cw.setStyleSheet("background:transparent; border:none;")
                cw.setLayout(col)
                parent_layout.addWidget(cw, 1)

            _info_col("↓ INCOMING", src)
            vsep = QFrame()
            vsep.setFixedWidth(1)
            vsep.setFixedHeight(28)
            vsep.setStyleSheet(f"background: {_th.border_color(0.09)}; border:none;")
            info_row.addWidget(vsep)
            _info_col("📁 EXISTING", dst)
            clay.addLayout(info_row)

            brow = QHBoxLayout()
            brow.setSpacing(6)
            brow.addStretch()
            skip_b    = _btn("Skip",    "neutral",
                             lambda _=None, f=fname: self._set(f, "skip"),    70)
            replace_b = _btn("Replace", "danger",
                             lambda _=None, f=fname: self._set(f, "replace"), 80)
            brow.addWidget(skip_b)
            brow.addWidget(replace_b)
            clay.addLayout(brow)

            self._btns[fname] = (replace_b, skip_b)
            ilay.addWidget(card)
            self._refresh(fname)

        ilay.addStretch()
        scroll.setWidget(inner)
        self._lay.addWidget(scroll)
        self._lay.addWidget(_divider())

        grow = QHBoxLayout()
        grow.setSpacing(8)
        grow.addWidget(_btn("Cancel",      "neutral", self._cancel))
        grow.addStretch()
        grow.addWidget(_btn("Skip All",    "neutral", self._skip_all))
        grow.addWidget(_btn("Replace All", "danger",  self._replace_all))
        grow.addWidget(_btn("Apply",       "primary", self._apply))
        self._lay.addLayout(grow)
        self.adjustSize()

    def _set(self, fname, state):
        self._states[fname] = state
        self._refresh(fname)

    def _refresh(self, fname):
        rb, sb = self._btns[fname]
        if self._states[fname] == "replace":
            rb.setStyleSheet(_active_danger())
            sb.setStyleSheet(_style("neutral"))
        else:
            rb.setStyleSheet(_style("danger"))
            sb.setStyleSheet(_active_neutral())

    def _cancel(self):
        self.result_policy = "cancel"; self.reject()

    def _skip_all(self):
        for f in self._states:
            self._states[f] = "skip"; self._refresh(f)
        self.per_file = dict(self._states)
        self.result_policy = "skip_all"; self.accept()

    def _replace_all(self):
        for f in self._states:
            self._states[f] = "replace"; self._refresh(f)
        self.per_file = dict(self._states)
        self.result_policy = "replace_all"; self.accept()

    def _apply(self):
        self.per_file = dict(self._states)
        self.result_policy = "apply"; self.accept()


# Thread-safe public API

class _MainThreadBridge(QObject):

    request = Signal(object)

    def __init__(self):
        super().__init__()
        self.request.connect(self._run, Qt.QueuedConnection)

    def _run(self, fn):
        fn()


_bridge = _MainThreadBridge()


def _on_main(widget, fn):
    if QThread.currentThread() == widget.thread():
        fn()
        return


    import threading
    done = threading.Event()
    def _invoke():
        fn()
        done.set()
    _bridge.request.emit(_invoke)
    done.wait()


def ask_file_conflict(parent, filename, src_path=None, dst_path=None,
                      show_bulk=False, folder_context=False, remaining=0) -> str:
    result = {"v": "cancel"}
    def _show():
        main_win = parent.window() if parent else None
        ov = _BlurOverlay(main_win) if main_win else None
        dlg = FileConflictDialog(
            filename, src_path=src_path, dst_path=dst_path,
            show_bulk=show_bulk, folder_context=folder_context,
            remaining=remaining, parent=parent
        )
        if ov:
            dlg._overlay = ov
        from app_ext.class_BlurOverlay import install_window_fade
        install_window_fade(dlg)
        dlg.exec()
        result["v"] = dlg.result_policy
        if ov: ov.close_overlay()
    _on_main(parent, _show)
    return result["v"]


def ask_multi_file_conflict(parent, conflicts) -> tuple:
    result = {"policy": "cancel", "per_file": {}}
    def _show():
        main_win = parent.window() if parent else None
        ov = _BlurOverlay(main_win) if main_win else None
        dlg = MultiFileConflictDialog(conflicts, parent=parent)
        if ov:
            dlg._overlay = ov
        from app_ext.class_BlurOverlay import install_window_fade
        install_window_fade(dlg)
        dlg.exec()
        result["policy"]   = dlg.result_policy
        result["per_file"] = dlg.per_file
        if ov: ov.close_overlay()
    _on_main(parent, _show)
    return result["policy"], result["per_file"]
