# SPDX-License-Identifier: MPL-2.0
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QWidget,
    QScrollArea, QPushButton, QSizePolicy, QFrame, QLineEdit,
    QApplication
)
from PySide6.QtCore import Qt, QRect
from PySide6.QtGui import QColor
from main_ext.class_ThemeUi import theme as _th

# shared button styles (lazy → always current theme)
def _ss_primary():
    return f"""
        QPushButton {{
            background: {_th.accent(0.18)};
            border: 1px solid {_th.accent(0.38)};
            border-radius: 6px;
            color: {_th.FG_BLUE};
            font-size: 12px;
            font-weight: 600;
            padding: 0 14px;
            height: 30px;
        }}
        QPushButton:hover {{ background: {_th.accent(0.32)}; border-color: {_th.accent(0.60)}; color: {_th.FG_BLUE_LIGHT}; }}
        QPushButton:pressed {{ background: {_th.accent(0.48)}; }}
        QPushButton:disabled {{
            background: {_th.accent(0.05)};
            border-color: {_th.accent(0.10)};
            color: {_th.FG_DISABLED};
        }}
    """

def _ss_neutral():
    return f"""
        QPushButton {{
            background: {_th.border_color(0.06)};
            border: 1px solid {_th.border_color(0.10)};
            border-radius: 6px;
            color: {_th.FG3};
            font-size: 12px;
            font-weight: 400;
            padding: 0 14px;
            height: 30px;
        }}
        QPushButton:hover {{ background: {_th.border_color(0.10)}; border-color: {_th.border_color(0.18)}; color: {_th.FG_HOVER}; }}
        QPushButton:pressed {{ background: {_th.border_color(0.14)}; }}
    """

# warna branch (sync dengan NeoBranchManager)
BRANCH_COLORS = {
    "red":    "#e05555",
    "blue":   "#4d8ef0",
    "yellow": "#f0c040",
    "green":  "#4db860",
    "orange": "#f08030",
    "purple": "#9060d0",
    "pink":   "#e070a0",
}


# Color dot button
class _ColorDot(QPushButton):
    SIZE = 20

    def __init__(self, color_name: str, hex_color: str, parent=None):
        super().__init__(parent)
        self.color_name = color_name
        self.hex_color  = hex_color
        self._selected  = False
        self.setFixedSize(self.SIZE, self.SIZE)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip(color_name.capitalize())
        self._upd()

    def set_selected(self, v: bool):
        self._selected = v
        self._upd()

    def _upd(self):
        ring = f"2px solid {_th.border_color(0.80)}" if self._selected else "2px solid transparent"
        self.setStyleSheet(f"""
            QPushButton {{
                background: {self.hex_color};
                border-radius: {self.SIZE // 2}px;
                border: {ring};
            }}
            QPushButton:hover {{ border: 2px solid {_th.border_color(0.55)}; }}
        """)


# shared helpers
def _make_shell(parent=None) -> tuple:
    dlg = QDialog(parent)
    dlg.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
    dlg.setAttribute(Qt.WA_TranslucentBackground)

    shell = QVBoxLayout(dlg)
    shell.setContentsMargins(0, 0, 0, 0)

    # Convert _th.BG2 (hex) → rgba supaya semi-transparan di atas BlurOverlay
    _hex = _th.BG2.lstrip("#")
    _r, _g, _b = int(_hex[0:2], 16), int(_hex[2:4], 16), int(_hex[4:6], 16)

    box = QWidget()
    box.setObjectName("nb_box")
    box.setStyleSheet(f"""
        QWidget#nb_box {{
            background: {_th.panel_rgba(_r, _g, _b, 170)};
            border: 1px solid {_th.border_color(0.22)};
            border-radius: 14px;
        }}
        QWidget#nb_box QLabel {{
            background: transparent;
            border: none;
            color: {_th.FG};
        }}
    """)
    shell.addWidget(box)

    lay = QVBoxLayout(box)
    lay.setContentsMargins(18, 16, 18, 16)
    lay.setSpacing(12)

    return dlg, box, lay


def _div() -> QWidget:
    d = QWidget()
    d.setFixedHeight(1)
    d.setStyleSheet(f"background: {_th.border_color(0.07)}; border: none;")
    return d


def _center(dlg: QDialog, parent):

    dlg.adjustSize()
    # Cari main window untuk tahu di screen mana app berada
    screen = None
    if parent:
        top = parent
        while top.parent():
            top = top.parent()
        screen = QApplication.screenAt(top.geometry().center())
    if screen is None:
        screen = QApplication.primaryScreen()
    geo = screen.availableGeometry()
    dlg.move(
        geo.center().x() - dlg.width() // 2,
        geo.center().y() - dlg.height() // 2,
    )


class NeoBranchDialog:


    @staticmethod
    def ask(parent, fs_root: str):
        result = [None]
        dlg, box, lay = _make_shell(parent)
        dlg.setFixedWidth(440)

        # header
        hdr = QHBoxLayout()
        hdr.setSpacing(8)

        ic = QLabel("⑂")
        ic.setStyleSheet(f"font-size: 15px; color: {_th.accent_hex};")
        ic.setFixedSize(20, 20)
        ic.setAlignment(Qt.AlignVCenter)

        title = QLabel("Create NeoBranch")
        title.setStyleSheet(f"font-size: 14px; font-weight: 600; color: {_th.FG};")

        fs_badge = QLabel(fs_root)
        fs_badge.setStyleSheet(f"""
            background: {_th.accent(0.14)};
            border: 1px solid {_th.accent(0.28)};
            border-radius: 5px;
            color: {_th.FG_BLUE};
            font-size: 11px;
            font-weight: 600;
            padding: 1px 7px;
        """)

        hdr.addWidget(ic)
        hdr.addWidget(title)
        hdr.addSpacing(4)
        hdr.addWidget(fs_badge)
        hdr.addStretch()
        lay.addLayout(hdr)
        lay.addWidget(_div())

        # desc
        desc = QLabel("Source filesystem is never modified. All edits are stored in a separate branch layer.")
        desc.setWordWrap(True)
        desc.setStyleSheet(f"color: {_th.FG_DESC}; font-size: 11px;")
        lay.addWidget(desc)

        # branch name
        name_lbl = QLabel("Branch Name")
        name_lbl.setStyleSheet(f"font-size: 12px; font-weight: 600; color: {_th.FG_BLUE_MUTED};")
        lay.addWidget(name_lbl)

        name_input = QLineEdit()
        name_input.setPlaceholderText("nfc_test  (letters, numbers, _ , -)")
        name_input.setFixedHeight(34)
        name_input.setStyleSheet(f"""
            QLineEdit {{
                background: {_th.border_color(0.04)};
                border: 1px solid {_th.border_color(0.10)};
                border-radius: 7px;
                color: {_th.FG};
                font-size: 13px;
                padding: 0 10px;
                font-family: 'Consolas', 'Courier New', monospace;
            }}
            QLineEdit:focus {{
                border: 1px solid {_th.accent(0.50)};
                background: {_th.accent(0.05)};
            }}
        """)
        lay.addWidget(name_input)

        # color picker
        col_lbl = QLabel("Branch Color")
        col_lbl.setStyleSheet(f"font-size: 12px; font-weight: 600; color: {_th.FG_BLUE_MUTED};")
        lay.addWidget(col_lbl)

        col_row = QHBoxLayout()
        col_row.setSpacing(8)
        col_row.setContentsMargins(0, 0, 0, 0)

        selected_color = ["blue"]
        dot_btns: dict[str, _ColorDot] = {}

        def _pick_color(cname):
            selected_color[0] = cname
            for n, b in dot_btns.items():
                b.set_selected(n == cname)

        for cname, chex in BRANCH_COLORS.items():
            d = _ColorDot(cname, chex)
            d.clicked.connect(lambda _, n=cname: _pick_color(n))
            dot_btns[cname] = d
            col_row.addWidget(d)
        col_row.addStretch()
        lay.addLayout(col_row)
        _pick_color("blue")

        # error label
        err_lbl = QLabel("")
        err_lbl.setStyleSheet(f"color: {_th.RED_BTN}; font-size: 11px;")
        err_lbl.hide()
        lay.addWidget(err_lbl)

        lay.addWidget(_div())

        # footer
        footer = QHBoxLayout()
        footer.addStretch()

        btn_cancel = QPushButton("Cancel")
        btn_cancel.setFixedHeight(30)
        btn_cancel.setCursor(Qt.PointingHandCursor)
        btn_cancel.setStyleSheet(_ss_neutral())

        btn_create = QPushButton("Create Branch")
        btn_create.setFixedHeight(30)
        btn_create.setCursor(Qt.PointingHandCursor)
        btn_create.setStyleSheet(_ss_primary())
        btn_create.setEnabled(False)

        footer.addWidget(btn_cancel)
        footer.addWidget(btn_create)
        lay.addLayout(footer)

        # logic
        import re as _re

        def _on_text(text):
            valid = bool(text.strip()) and bool(_re.match(r'^[a-zA-Z0-9_\-]+$', text.strip()))
            btn_create.setEnabled(valid)
            if valid:
                err_lbl.hide()

        def _on_create():
            name = name_input.text().strip()
            if not name:
                err_lbl.setText("Name cannot be empty.")
                err_lbl.show()
                return
            if not _re.match(r'^[a-zA-Z0-9_\-]+$', name):
                err_lbl.setText("Only letters, numbers, underscore, and dash are allowed.")
                err_lbl.show()
                return
            result[0] = (name, selected_color[0])
            dlg.accept()

        name_input.textChanged.connect(_on_text)
        btn_create.clicked.connect(_on_create)
        btn_cancel.clicked.connect(dlg.reject)
        name_input.returnPressed.connect(lambda: _on_create() if btn_create.isEnabled() else None)

        _center(dlg, parent)
        from app_ext.class_BlurOverlay import BlurOverlay as _BO
        _win = parent.window() if parent else None
        _ov  = _BO(_win) if _win else None
        if _ov:
            dlg._overlay = _ov
        from app_ext.class_BlurOverlay import install_window_fade
        install_window_fade(dlg)
        dlg.exec()
        if _ov: _ov.close_overlay()
        return result[0]


class NeoBranchPickerDialog:


    @staticmethod
    def ask(parent, fs_root: str, branches: list, current_active):
        result = [None]
        dlg, box, lay = _make_shell(parent)
        dlg.setFixedWidth(480)
        dlg.setFixedHeight(420)

        # header
        hdr = QHBoxLayout()
        hdr.setSpacing(8)

        ic = QLabel("⑂")
        ic.setStyleSheet(f"font-size: 15px; color: {_th.accent_hex};")
        ic.setFixedSize(20, 20)
        ic.setAlignment(Qt.AlignVCenter)

        title = QLabel("NeoBranch")
        title.setStyleSheet(f"font-size: 14px; font-weight: 600; color: {_th.FG};")

        fs_badge = QLabel(fs_root)
        fs_badge.setStyleSheet(f"""
            background: {_th.accent(0.14)};
            border: 1px solid {_th.accent(0.28)};
            border-radius: 5px;
            color: {_th.FG_BLUE};
            font-size: 11px;
            font-weight: 600;
            padding: 1px 7px;
        """)

        hdr.addWidget(ic)
        hdr.addWidget(title)
        hdr.addSpacing(4)
        hdr.addWidget(fs_badge)
        hdr.addStretch()
        lay.addLayout(hdr)
        lay.addWidget(_div())

        # scroll
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setStyleSheet(f"""
            QScrollArea {{ background: {_th.BG4}; border: none; }}
            QScrollBar:vertical {{
                background: transparent; width: 4px; margin: 0; border: none;
            }}
            QScrollBar::handle:vertical {{
                background: {_th.border_color(0.12)}; border-radius: 2px; min-height: 20px;
            }}
            QScrollBar::handle:vertical:hover {{ background: {_th.accent(0.45)}; }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical,
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
                height: 0; background: transparent;
            }}
        """)
        cont = QWidget()
        cont.setStyleSheet(f"background: {_th.BG4};")
        list_lay = QVBoxLayout(cont)
        list_lay.setSpacing(6)
        list_lay.setContentsMargins(0, 0, 0, 0)
        scroll.setWidget(cont)
        lay.addWidget(scroll, 1)

        # build rows
        def _pick(v):
            result[0] = v
            dlg.accept()

        all_rows = [None] + branches   # None = Source row

        card = QWidget()
        card.setObjectName("nbCard")
        card.setStyleSheet(f"""
            QWidget#nbCard {{
                background: {_th.border_color(0.03)};
                border: 1px solid {_th.border_color(0.07)};
                border-radius: 10px;
            }}
        """)
        cl = QVBoxLayout(card)
        cl.setContentsMargins(0, 6, 0, 6)
        cl.setSpacing(0)

        for i, b in enumerate(all_rows):
            bname   = b["name"] if b else None
            bcolor  = BRANCH_COLORS.get(b.get("color", "blue"), _th.BLUE) if b else _th.FG_DISABLED
            is_act  = (bname == current_active)

            row_w = QWidget()
            row_w.setStyleSheet("background: transparent;")
            ro = QHBoxLayout(row_w)
            ro.setContentsMargins(0, 0, 0, 0)
            ro.setSpacing(0)

            # left accent bar
            bar = QWidget()
            bar.setFixedWidth(3)
            bar.setStyleSheet(f"""
                background: {bcolor};
                border-radius: 2px;
                margin-top: 8px;
                margin-bottom: 8px;
                margin-left: 10px;
            """)
            ro.addWidget(bar)

            # content
            content = QWidget()
            content.setStyleSheet("background: transparent;")
            cc = QHBoxLayout(content)
            cc.setContentsMargins(10, 8, 12, 8)
            cc.setSpacing(10)

            dot_lbl = QLabel("●")
            dot_lbl.setStyleSheet(f"color: {bcolor}; font-size: 12px;")
            dot_lbl.setFixedWidth(14)
            cc.addWidget(dot_lbl)

            tcol = QVBoxLayout()
            tcol.setSpacing(1)

            disp = bname if bname else "Source"
            nl = QLabel(disp)
            nl.setStyleSheet(
                f"font-size: 13px; font-weight: {'700' if is_act else '500'}; "
                f"color: {_th.FG if bname else _th.FG_DESC};"
            )
            tcol.addWidget(nl)

            sub_text = "Active now" if is_act else ("Original filesystem, no branch" if not bname else "")
            sl = QLabel(sub_text)
            sl.setStyleSheet(f"font-size: 11px; color: {_th.accent_hex if is_act else _th.FG_DISABLED};")
            tcol.addWidget(sl)

            cc.addLayout(tcol)
            cc.addStretch()

            if is_act:
                al = QLabel("Active ✓")
                al.setStyleSheet(f"font-size: 11px; color: {_th.accent_hex}; padding: 0 4px;")
                cc.addWidget(al)
            else:
                switch_lbl = "Use Source" if not bname else "Switch"
                bs = QPushButton(switch_lbl)
                bs.setFixedHeight(26)
                bs.setMinimumWidth(80)
                bs.setCursor(Qt.PointingHandCursor)
                bs.setStyleSheet(f"""
                    QPushButton {{
                        background: {_th.accent(0.12)};
                        border: 1px solid {_th.accent(0.28)};
                        border-radius: 13px;
                        color: {_th.FG_BLUE};
                        font-size: 11px;
                        font-weight: 600;
                        padding: 0 12px;
                    }}
                    QPushButton:hover {{
                        background: {_th.accent(0.24)};
                        border-color: {_th.accent(0.50)};
                        color: {_th.FG_BLUE_LIGHT};
                    }}
                    QPushButton:pressed {{ background: {_th.accent(0.36)}; }}
                """)
                _v = bname if bname else "NONE"
                bs.clicked.connect(lambda _, v=_v: _pick(v))
                cc.addWidget(bs)

                if bname:
                    bd = QPushButton("Delete")
                    bd.setFixedHeight(26)
                    bd.setCursor(Qt.PointingHandCursor)
                    bd.setStyleSheet(f"""
                        QPushButton {{
                            background: transparent;
                            border: 1px solid {_th.red(0.20)};
                            border-radius: 13px;
                            color: {_th.RED_BTN};
                            font-size: 11px;
                            padding: 0 10px;
                        }}
                        QPushButton:hover {{
                            background: {_th.red(0.14)};
                            border-color: {_th.red(0.45)};
                        }}
                    """)
                    _vd = f"__DEL__{bname}"
                    bd.clicked.connect(lambda _, v=_vd: _pick(v))
                    cc.addWidget(bd)

            ro.addWidget(content)
            cl.addWidget(row_w)

            if i != len(all_rows) - 1:
                sep = QWidget()
                sep.setFixedHeight(1)
                sep.setStyleSheet(f"background: {_th.border_color(0.05)}; border: none;")
                cl.addWidget(sep)

        list_lay.addWidget(card)
        list_lay.addStretch(1)

        # footer
        lay.addWidget(_div())
        footer = QHBoxLayout()

        btn_new = QPushButton("+ Create New Branch")
        btn_new.setFixedHeight(30)
        btn_new.setCursor(Qt.PointingHandCursor)
        btn_new.setStyleSheet(_ss_primary())
        btn_new.clicked.connect(lambda: _pick("__NEW__"))

        btn_close = QPushButton("Close")
        btn_close.setFixedHeight(30)
        btn_close.setCursor(Qt.PointingHandCursor)
        btn_close.setStyleSheet(_ss_neutral())
        btn_close.clicked.connect(dlg.reject)

        footer.addWidget(btn_new)
        footer.addStretch()
        footer.addWidget(btn_close)
        lay.addLayout(footer)

        _center(dlg, parent)
        from app_ext.class_BlurOverlay import BlurOverlay as _BO
        _win = parent.window() if parent else None
        _ov  = _BO(_win) if _win else None
        if _ov:
            dlg._overlay = _ov
        from app_ext.class_BlurOverlay import install_window_fade
        install_window_fade(dlg)
        dlg.exec()
        if _ov: _ov.close_overlay()
        return result[0]
