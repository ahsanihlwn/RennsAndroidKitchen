# SPDX-License-Identifier: MPL-2.0
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QWidget
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon, QPixmap
from main_ext.class_ThemeUi import theme as _th
from core.ui_lang import t


def _btn_neutral():
    return f"""
    QPushButton {{
        background: {_th.border_color(0.06)};
        border: 1px solid {_th.border_color(0.10)};
        border-radius: 6px; color: {_th.FG_MUTED};
        font-size: 12px; font-weight: 400;
        padding: 0 14px; height: 30px;
    }}
    QPushButton:hover   {{ background: {_th.border_color(0.10)}; border-color: {_th.border_color(0.18)}; color: {_th.FG}; }}
    QPushButton:pressed {{ background: {_th.border_color(0.14)}; }}
    """

def _lbl(text, size=12, color=None, bold=False, selectable=False):
    c = color or _th.FG_MUTED
    l = QLabel(text)
    l.setStyleSheet(
        f"color:{c}; font-size:{size}px; font-weight:{'600' if bold else '400'};"
        " background:transparent; border:none;"
    )
    l.setTextFormat(Qt.RichText)
    l.setWordWrap(True)
    if selectable:
        l.setTextInteractionFlags(Qt.TextSelectableByMouse)
    return l

def _sep():
    d = QWidget()
    d.setFixedHeight(1)
    d.setStyleSheet(f"background: {_th.border_color(0.07)}; border:none;")
    return d


class CreditWindow(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(t("crd_title"))
        self.setModal(True)
        self.setFixedWidth(360)
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        # Convert _th.BG2 → rgba (efek kaca di atas BlurOverlay)
        _hex = _th.BG2.lstrip("#")
        _r, _g, _b = int(_hex[0:2], 16), int(_hex[2:4], 16), int(_hex[4:6], 16)

        box = QWidget()
        box.setObjectName("credit_box")
        box.setStyleSheet(f"""
            QWidget#credit_box {{
                background: {_th.panel_rgba(_r, _g, _b, 170)};
                border: 1px solid {_th.panel_border_color()};
                border-radius: 14px;
            }}
            QLabel {{ background: transparent; border: none; }}
        """)
        outer.addWidget(box)

        lay = QVBoxLayout(box)
        lay.setContentsMargins(22, 20, 22, 18)
        lay.setSpacing(0)

        # logo
        try:
            from icons.handler import icon as _icon
            pix = QIcon(_icon("renns_project")).pixmap(48, 48)
        except Exception:
            pix = QPixmap()

        logo_row = QHBoxLayout()
        logo_row.setContentsMargins(0, 0, 0, 0)
        logo_row.addStretch()
        if not pix.isNull():
            logo_lbl = QLabel()
            logo_lbl.setFixedSize(48, 48)
            logo_lbl.setAlignment(Qt.AlignCenter)

            from PySide6.QtGui import QPainter, QBrush
            rounded = QPixmap(48, 48)
            rounded.fill(Qt.transparent)
            painter = QPainter(rounded)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setBrush(QBrush(pix.scaled(48, 48, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)))
            painter.setPen(Qt.NoPen)
            painter.drawEllipse(0, 0, 48, 48)
            painter.end()
            logo_lbl.setPixmap(rounded)
            logo_row.addWidget(logo_lbl)
        logo_row.addStretch()
        lay.addLayout(logo_row)
        lay.addSpacing(12)

        title = _lbl("Renn's Project", size=15, color=_th.FG, bold=True)
        title.setAlignment(Qt.AlignCenter)
        lay.addWidget(title)
        lay.addSpacing(2)

        handle = _lbl("@ahsanihlwn", size=12, color=_th.FG_BLUE)
        handle.setAlignment(Qt.AlignCenter)
        lay.addWidget(handle)
        lay.addSpacing(16)
        lay.addWidget(_sep())
        lay.addSpacing(16)

        badge_row = QHBoxLayout()
        badge_row.setContentsMargins(0, 0, 0, 0)
        badge_lbl = QLabel("MPL-2.0")
        badge_lbl.setStyleSheet(f"""
            color: {_th.FG_BLUE};
            font-size: 11px; font-weight: 600;
            background: {_th.accent(0.12)};
            border: 1px solid {_th.accent(0.25)};
            border-radius: 4px; padding: 2px 10px;
        """)
        badge_lbl.setAlignment(Qt.AlignCenter)
        badge_row.addStretch()
        badge_row.addWidget(badge_lbl)
        badge_row.addStretch()
        lay.addLayout(badge_row)
        lay.addSpacing(10)

        mpl_title = _lbl("Mozilla Public License 2.0", size=12, color=_th.FG, bold=True)
        mpl_title.setAlignment(Qt.AlignCenter)
        lay.addWidget(mpl_title)
        lay.addSpacing(8)

        mpl_notice = _lbl(t("lic_notice"), size=11, color=_th.FG_MUTED)
        mpl_notice.setAlignment(Qt.AlignCenter)
        lay.addWidget(mpl_notice)
        lay.addSpacing(8)

        mpl_link = _lbl(
            f"<a href='https://www.mozilla.org/en-US/MPL/2.0/' style='color:{_th.FG_BLUE}; text-decoration:none;'>mozilla.org/MPL/2.0</a>",
            size=11, color=_th.FG_MUTED,
        )
        mpl_link.setAlignment(Qt.AlignCenter)
        mpl_link.setOpenExternalLinks(True)
        mpl_link.setTextInteractionFlags(Qt.TextBrowserInteraction)
        mpl_link.setCursor(Qt.PointingHandCursor)
        lay.addWidget(mpl_link)

        lay.addSpacing(16)
        lay.addWidget(_sep())
        lay.addSpacing(14)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(0)
        btn_row.addStretch()
        btn_close = QPushButton(t("crd_btn_close"))
        btn_close.setMinimumWidth(80)
        btn_close.setFixedHeight(30)
        btn_close.setCursor(Qt.PointingHandCursor)
        btn_close.setStyleSheet(_btn_neutral())
        btn_close.clicked.connect(self.accept)
        btn_row.addWidget(btn_close)
        lay.addLayout(btn_row)

        self.adjustSize()
