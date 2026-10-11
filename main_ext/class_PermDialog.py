# SPDX-License-Identifier: MPL-2.0
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QGridLayout, QWidget
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon

import core.flag as flag
from main_ext.class_ThemeUi import theme as _th

print("[MAIN STAGE] PERMDIALOG")

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

def _ss_perm_check():
    return f"""
        QPushButton#permCheck {{
            background: {_th.border_color(0.05)};
            border: 1px solid {_th.border_color(0.14)};
            border-radius: 10px;
        }}
        QPushButton#permCheck:hover {{
            border-color: {_th.accent(0.50)};
            background: {_th.accent(0.08)};
        }}
        QPushButton#permCheck:checked {{
            background: {_th.accent(0.55)};
            border: 1px solid {_th.accent(0.80)};
        }}
        QPushButton#permCheck:checked:hover {{
            background: {_th.accent(0.65)};
            border-color: {_th.accent(0.90)};
        }}
    """


class PermDialog(QDialog):
    def __init__(self, current_perm, parent=None):
        super().__init__(parent)

        self.setWindowTitle("Permissions")
        self.setWindowIcon(QIcon(flag.APP_ICON))
        self.setModal(True)
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setStyleSheet("background: transparent;")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # Card semi-transparan — sama persis pattern open_project_settings
        _hex = _th.BG2.lstrip("#")
        _r, _g, _b = int(_hex[0:2], 16), int(_hex[2:4], 16), int(_hex[4:6], 16)

        card = QWidget()
        card.setObjectName("perm_card")
        card.setFixedWidth(316)
        card.setStyleSheet(f"""
            QWidget#perm_card {{
                background: {_th.panel_rgba(_r, _g, _b, 170)};
                border: 1px solid {_th.border_color(0.22)};
                border-radius: 14px;
            }}
            QWidget {{
                background: transparent;
                color: {_th.FG};
                font-family: 'Poppins', 'Inter', 'Segoe UI', sans-serif;
                font-size: 13px;
            }}
            QLabel {{
                background: transparent;
                border: none;
                color: {_th.FG};
            }}
            {_ss_perm_check()}
        """)
        outer.addWidget(card)

        root = QVBoxLayout(card)
        root.setContentsMargins(20, 18, 20, 16)
        root.setSpacing(12)

        # ── header ──
        title = QLabel("File Permissions")
        title.setStyleSheet(f"font-size: 14px; font-weight: 600; color: {_th.FG};")

        subtitle = QLabel(f"Current: {current_perm}")
        subtitle.setStyleSheet(f"color: {_th.FG_DESC}; font-size: 11px;")

        root.addWidget(title)
        root.addWidget(subtitle)

        # ── divider ──
        div = QWidget()
        div.setFixedHeight(1)
        div.setStyleSheet(f"background: {_th.border_color(0.07)};")
        root.addWidget(div)

        # ── grid ──
        grid = QGridLayout()
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(10)

        grid.addWidget(QLabel(""),               0, 0)
        grid.addWidget(self._hdr("Read"),        0, 1)
        grid.addWidget(self._hdr("Write"),       0, 2)
        grid.addWidget(self._hdr("Exec"),        0, 3)

        self.owner = self._perm_row(grid, "Owner",  1)
        self.group = self._perm_row(grid, "Group",  2)
        self.other = self._perm_row(grid, "Others", 3)

        root.addLayout(grid)
        root.addStretch()

        # ── footer divider ──
        fdiv = QWidget()
        fdiv.setFixedHeight(1)
        fdiv.setStyleSheet(f"background: {_th.border_color(0.07)};")
        root.addWidget(fdiv)

        # ── buttons ──
        btns = QHBoxLayout()
        btns.setSpacing(8)
        btns.addStretch()

        cancel = QPushButton("Cancel")
        cancel.setFixedHeight(30)
        cancel.setCursor(Qt.PointingHandCursor)
        cancel.setStyleSheet(_ss_neutral())
        cancel.clicked.connect(self.reject)

        apply_btn = QPushButton("Apply")
        apply_btn.setFixedHeight(30)
        apply_btn.setCursor(Qt.PointingHandCursor)
        apply_btn.setStyleSheet(_ss_primary())
        apply_btn.setDefault(True)
        apply_btn.clicked.connect(self.accept)

        btns.addWidget(cancel)
        btns.addWidget(apply_btn)
        root.addLayout(btns)

        self._apply_initial_perm(current_perm)
        self.adjustSize()

    def _hdr(self, text):
        lbl = QLabel(text)
        lbl.setAlignment(Qt.AlignCenter)
        lbl.setStyleSheet(f"color: {_th.FG_DESC}; font-size: 11px;")
        return lbl

    def _perm_row(self, grid, label, row):
        lbl = QLabel(label)
        lbl.setStyleSheet(f"font-weight: 500; color: {_th.FG};")

        from PySide6.QtGui import QIcon
        from PySide6.QtCore import QSize
        from icons.handler import icon as _icon
        _check_icon = QIcon(_icon("check_white"))

        r = QPushButton()
        w = QPushButton()
        x = QPushButton()

        for b in (r, w, x):
            b.setCheckable(True)
            b.setFixedSize(20, 20)
            b.setCursor(Qt.PointingHandCursor)
            b.setObjectName("permCheck")
            b.setIconSize(QSize(14, 14))
            def _toggle_icon(v, btn=b, ic=_check_icon):
                btn.setIcon(ic if v else QIcon())
            b.toggled.connect(_toggle_icon)

        grid.addWidget(lbl, row, 0)
        grid.addWidget(r,   row, 1, Qt.AlignCenter)
        grid.addWidget(w,   row, 2, Qt.AlignCenter)
        grid.addWidget(x,   row, 3, Qt.AlignCenter)

        return [r, w, x]

    def _apply_initial_perm(self, rwx):
        if not rwx or len(rwx) != 9:
            return
        boxes = self.owner + self.group + self.other
        for cb, ch in zip(boxes, rwx):
            cb.setChecked(ch != "-")

    def get_octal(self):
        def val(b):
            r, w, x = b
            return (4 if r.isChecked() else 0) + \
                   (2 if w.isChecked() else 0) + \
                   (1 if x.isChecked() else 0)
        return f"0{val(self.owner)}{val(self.group)}{val(self.other)}"
