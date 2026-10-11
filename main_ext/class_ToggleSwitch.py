# SPDX-License-Identifier: MPL-2.0
from PySide6.QtWidgets import (
    QWidget, QFrame, QComboBox, QVBoxLayout,
    QSlider, QHBoxLayout, QLabel, QPushButton
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from main_ext.class_ThemeUi import theme as _th


def build_card(title, subtitle, toggle_btn):
    card = QFrame()
    card.setObjectName("card")
    card.setStyleSheet(f"""
        QFrame#card {{
            background: {_th.border_color(0.03)};
            border: 1px solid {_th.border_color(0.08)};
            border-radius: 10px;
        }}
    """)

    outer = QVBoxLayout(card)
    outer.setContentsMargins(14, 12, 14, 12)
    outer.setSpacing(8)

    # HEADER
    header = QWidget()
    header.setStyleSheet("background: transparent;")
    header_layout = QVBoxLayout(header)
    header_layout.setContentsMargins(0, 0, 0, 0)
    header_layout.setSpacing(4)

    title_lbl = QLabel(title)
    title_lbl.setStyleSheet(f"""
        background: transparent;
        color: {_th.FG};
        font-size: 13px;
        font-weight: 600;
        border: none;
    """)

    row = QHBoxLayout()
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(8)

    sub_lbl = QLabel(subtitle)
    sub_lbl.setStyleSheet(f"""
        background: transparent;
        color: {_th.FG_DESC};
        font-size: 11px;
        border: none;
    """)

    row.addWidget(sub_lbl)
    row.addStretch()
    row.addWidget(toggle_btn)

    header_layout.addWidget(title_lbl)
    header_layout.addLayout(row)

    outer.addWidget(header)

    # SEPARATOR
    sep = QWidget()
    sep.setFixedHeight(1)
    sep.setStyleSheet(f"background: {_th.border_color(0.06)};")
    outer.addWidget(sep)

    # BODY
    body = QWidget()
    body.setAttribute(Qt.WA_StyledBackground, True)
    body.setStyleSheet(f"""
        QWidget {{
            background: transparent;
        }}
        QLabel {{
            background: transparent;
            border: none;
            color: {_th.FG_BLUE_MUTED};
            font-size: 12px;
        }}
        QComboBox {{
            background-color: {_th.border_color(0.05)};
            border: 1px solid {_th.border_color(0.10)};
            border-radius: 6px;
            padding: 4px 24px 4px 8px;
            color: {_th.FG};
            font-size: 12px;
            min-height: 26px;
        }}
        QComboBox:hover {{
            background-color: {_th.border_color(0.08)};
            border-color: {_th.border_color(0.16)};
        }}
        QComboBox:focus {{
            border-color: {_th.accent(0.55)};
        }}
        QComboBox::drop-down {{
            width: 18px;
            border-left: none;
            background: transparent;
        }}
        QComboBox::down-arrow {{
            image: none;
            width: 0; height: 0;
            border-left: 4px solid transparent;
            border-right: 4px solid transparent;
            border-top: 5px solid {_th.GRAY_MID2};
        }}
        QComboBox QAbstractItemView {{
            background-color: {_th.BG4};
            border: 1px solid {_th.border_color(0.09)};
            border-radius: 6px;
            padding: 3px;
            outline: none;
        }}
        QComboBox QAbstractItemView::item {{
            padding: 6px 10px;
            border-radius: 5px;
            color: {_th.FG};
        }}
        QComboBox QAbstractItemView::item:hover {{
            background: {_th.accent(0.14)};
        }}
        QComboBox QAbstractItemView::item:selected {{
            background: {_th.accent(0.22)};
            color: {_th.FG_BLUE_LIGHT};
        }}
        QComboBox QAbstractItemView QScrollBar:vertical {{
            width: 4px;
            background: transparent;
        }}
        QComboBox QAbstractItemView QScrollBar::handle:vertical {{
            background: {_th.border_color(0.15)};
            border-radius: 2px;
        }}
    """)

    body_layout = QVBoxLayout(body)
    body_layout.setContentsMargins(0, 6, 0, 0)
    body_layout.setSpacing(8)

    outer.addWidget(body)

    return card, body


def build_level_slider():
    s = QSlider(Qt.Horizontal)
    s.setRange(1, 9)
    s.setFixedHeight(24)
    s.setSingleStep(1)
    s.setPageStep(1)
    s.setStyleSheet(f"""
        QSlider::groove:horizontal {{
            height: 4px;
            background: {_th.border_color(0.10)};
            border-radius: 2px;
        }}
        QSlider::sub-page:horizontal {{
            background: {_th.accent(0.70)};
            border-radius: 2px;
        }}
        QSlider::handle:horizontal {{
            width: 16px;
            height: 16px;
            margin: -6px 0;
            background: {_th.FG_BLUE};
            border-radius: 8px;
            border: 2px solid {_th.BG4};
        }}
        QSlider::handle:horizontal:hover {{
            background: {_th.FG_BLUE_LIGHT};
        }}
    """)
    return s


def set_slider_active(slider, active: bool):
    slider.setEnabled(active)
