# SPDX-License-Identifier: MPL-2.0
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QWidget, QListWidget, QListWidgetItem, QTextEdit
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from main_ext.class_ThemeUi import theme as _th
from core.ui_lang import t
from core.license_texts import license_entries, blocks_to_html


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


class LicenseWindow(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(t("lic_title"))
        self.setModal(True)
        self.setFixedSize(700, 520)
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        _hex = _th.BG2.lstrip("#")
        _r, _g, _b = int(_hex[0:2], 16), int(_hex[2:4], 16), int(_hex[4:6], 16)

        box = QWidget()
        box.setObjectName("license_box")
        box.setStyleSheet(f"""
            QWidget#license_box {{
                background: {_th.panel_rgba(_r, _g, _b, 170)};
                border: 1px solid {_th.panel_border_color()};
                border-radius: 14px;
            }}
            QLabel {{ background: transparent; border: none; }}
        """)
        outer.addWidget(box)

        lay = QVBoxLayout(box)
        lay.setContentsMargins(24, 20, 24, 16)
        lay.setSpacing(12)

        title = QLabel(t("lic_title"))
        title.setStyleSheet(
            f"color:{_th.FG}; font-size:16px; font-weight:600; background:transparent; border:none;"
        )
        lay.addWidget(title)

        body_row = QHBoxLayout()
        body_row.setSpacing(12)

        self._entries = license_entries()
        nav = QListWidget()
        nav.setFixedWidth(200)
        nav.setStyleSheet(f"""
            QListWidget {{
                background: transparent;
                border: none;
                outline: none;
                color: {_th.FG};
                font-family: "Poppins";
                font-size: 12px;
            }}
            QListWidget::item {{
                padding: 8px 10px;
                border-radius: 8px;
            }}
            QListWidget::item:selected {{
                background: {_th.accent(0.14)};
                color: {_th.FG_BRIGHT};
            }}
        """)
        for entry in self._entries:
            nav.addItem(QListWidgetItem(f"{entry['name']}\n{entry['meta']}"))
        body_row.addWidget(nav)

        divider = QWidget()
        divider.setFixedWidth(1)
        divider.setStyleSheet(f"background: {_th.border_color(0.07)}; border: none;")
        body_row.addWidget(divider)

        detail_col = QVBoxLayout()
        detail_col.setSpacing(6)
        doc_summary = QLabel()
        doc_summary.setWordWrap(True)
        doc_summary.setStyleSheet(f"color:{_th.FG_BLUE_MUTED}; font-size:11px;")

        viewer = QTextEdit()
        viewer.setReadOnly(True)
        viewer.setFont(QFont("Poppins", 10))
        viewer.setStyleSheet(
            f"QTextEdit {{ background: transparent; color: {_th.FG_MUTED}; border: none; }}"
        )

        detail_col.addWidget(doc_summary)
        detail_col.addWidget(viewer, 1)
        body_row.addLayout(detail_col, 1)
        lay.addLayout(body_row, 1)

        def select_entry(row: int) -> None:
            if not 0 <= row < len(self._entries):
                return
            entry = self._entries[row]
            summary_text = str(entry["summary"] or "")
            doc_summary.setVisible(bool(summary_text))
            doc_summary.setText(summary_text)
            viewer.setHtml(blocks_to_html(
                entry["blocks"],
                accent=_th.FG_BLUE,
                fg_bright=_th.FG_BRIGHT,
                fg=_th.FG,
                fg_muted=_th.FG_MUTED,
            ))

        nav.currentRowChanged.connect(select_entry)
        nav.setCurrentRow(0)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(0)
        btn_row.addStretch()
        btn_close = QPushButton(t("lic_btn_close"))
        btn_close.setMinimumWidth(80)
        btn_close.setFixedHeight(30)
        btn_close.setCursor(Qt.PointingHandCursor)
        btn_close.setStyleSheet(_btn_neutral())
        btn_close.clicked.connect(self.accept)
        btn_row.addWidget(btn_close)
        lay.addLayout(btn_row)
