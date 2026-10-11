# SPDX-License-Identifier: MPL-2.0
import os
import sys
from typing import Optional, List, Dict, Any

from PySide6.QtCore import Qt, QSize, Signal, QTimer, QRectF, QAbstractTableModel, QModelIndex
from PySide6.QtGui import (
    QIcon, QFont, QColor, QPainter, QPainterPath, QPen,
    QClipboard, QCursor
)
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTabWidget, QTabBar, QTableWidget, QTableWidgetItem, QTableView,
    QPlainTextEdit, QLineEdit, QComboBox, QSpinBox, QCheckBox,
    QHeaderView, QScrollArea, QFrame, QSizePolicy, QApplication,
    QFileDialog, QMessageBox
)

from core import bin_analyzer
from main_ext.class_ThemeUi import theme as _th
from main_ext.class_AppDialog import AppDialog as _AppDialog
from main_ext.class_DeferredCloseViewer import DeferredCloseViewer
from core.ui_lang import t
from core.threader import run_in_thread
from icons.handler import icon

print("[MAIN STAGE] HEX_VIEWER")

MONO_FONT = QFont("Consolas" if sys.platform == "win32" else "Monospace", 10)
MONO_FONT.setStyleHint(QFont.TypeWriter)


def _qcolor(val, fallback=None) -> QColor:
    fb = fallback or _th.FG
    if isinstance(val, QColor):
        return val
    s = str(val).strip()
    if s.startswith("rgba(") and s.endswith(")"):
        try:
            parts = s[5:-1].split(",")
            r, g, b = int(float(parts[0])), int(float(parts[1])), int(float(parts[2]))
            a = int(float(parts[3]) * 255) if len(parts) > 3 else 255
            return QColor(r, g, b, a)
        except Exception:
            return QColor(fb)
    if s.startswith("rgb(") and s.endswith(")"):
        try:
            parts = s[4:-1].split(",")
            return QColor(int(float(parts[0])), int(float(parts[1])), int(float(parts[2])))
        except Exception:
            return QColor(fb)
    if s.startswith("#"):
        return QColor(s)
    return QColor(fb)


def _card_style(bg_color=None, border_alpha=0.08, radius=6):
    bg = bg_color or _th.BG2
    return f"""
        QFrame {{
            background: {bg};
            border: 1px solid {_th.border_color(border_alpha)};
            border-radius: {radius}px;
        }}
    """


def _table_style():
    return f"""
        QTableWidget {{
            background: {_th.BG};
            border: 1px solid {_th.border_color(0.06)};
            border-radius: 4px;
            color: {_th.FG};
            gridline-color: {_th.border_color(0.04)};
            font-size: 12px;
            outline: none;
        }}
        QTableWidget::item {{
            padding: 4px 8px;
            border-bottom: 1px solid {_th.border_color(0.03)};
        }}
        QTableWidget::item:hover {{
            background: {_th.border_color(0.04)};
        }}
        QTableWidget::item:selected {{
            background: {_th.accent(0.20)};
            color: {_th.FG_BRIGHT};
        }}
        QHeaderView::section {{
            background: {_th.BG2};
            color: {_th.FG_LABEL};
            font-weight: bold;
            font-size: 11px;
            padding: 6px 8px;
            border: none;
            border-right: 1px solid {_th.border_color(0.12)};
            border-bottom: 1px solid {_th.border_color(0.12)};
        }}
        QHeaderView::section:horizontal {{
            border-top: none;
        }}
    """


def _input_style():
    return f"""
        QLineEdit {{
            background: {_th.BG_INPUT};
            border: 1px solid {_th.border_color(0.10)};
            border-radius: 4px;
            color: {_th.FG};
            padding: 3px 8px;
            font-size: 11px;
            min-height: 22px;
        }}
        QLineEdit:focus {{
            border: 1px solid {_th.accent(0.55)};
        }}
        QLineEdit::placeholder {{
            color: {_th.FG_MUTED};
        }}
    """


def _combo_style():
    return f"""
        QComboBox {{
            background: {_th.BG2};
            border: 1px solid {_th.border_color(0.10)};
            border-radius: 4px;
            color: {_th.FG};
            padding: 2px 8px;
            font-size: 11px;
            min-height: 22px;
        }}
        QComboBox:hover {{
            border-color: {_th.border_color(0.18)};
        }}
        QComboBox::drop-down {{
            border: none;
            width: 18px;
        }}
        QComboBox QAbstractItemView {{
            background: {_th.BG2};
            border: 1px solid {_th.border_color(0.12)};
            color: {_th.FG};
            selection-background-color: {_th.accent(0.25)};
            selection-color: {_th.FG_BRIGHT};
            outline: none;
        }}
    """


def _spin_style():
    return f"""
        QSpinBox {{
            background: {_th.BG_INPUT};
            border: 1px solid {_th.border_color(0.10)};
            border-radius: 4px;
            color: {_th.FG};
            padding: 2px 6px;
            font-size: 11px;
            min-height: 22px;
        }}
        QSpinBox:focus {{
            border-color: {_th.accent(0.55)};
        }}
    """


def _btn_primary():
    return f"""
        QPushButton {{
            background: {_th.accent(0.18)};
            border: 1px solid {_th.accent(0.38)};
            border-radius: 4px;
            color: {_th.FG_BLUE};
            font-size: 11px;
            font-weight: 500;
            padding: 3px 10px;
            min-height: 20px;
        }}
        QPushButton:hover {{
            background: {_th.accent(0.28)};
            border-color: {_th.accent(0.55)};
            color: {_th.FG_BLUE_LIGHT};
        }}
        QPushButton:pressed {{
            background: {_th.accent(0.40)};
        }}
        QPushButton:disabled {{
            background: {_th.border_color(0.04)};
            border-color: {_th.border_color(0.06)};
            color: {_th.FG_MUTED};
        }}
    """


def _footer_btn():
    return f"""
        QPushButton {{
            background: transparent;
            border: none;
            border-radius: 4px;
            color: {_th.FG2};
            font-size: 11px;
            padding: 2px 8px;
            min-height: 20px;
        }}
        QPushButton:hover {{
            background: {_th.border_color(0.08)};
            color: {_th.FG_BRIGHT};
        }}
        QPushButton:pressed {{
            background: {_th.accent(0.20)};
            color: {_th.FG_BRIGHT};
        }}
        QPushButton:disabled {{
            color: {_th.FG_MUTED};
        }}
    """


def _text_view_style():
    return f"""
        QPlainTextEdit {{
            background: {_th.BG};
            border: 1px solid {_th.border_color(0.06)};
            border-radius: 4px;
            color: {_th.FG};
            font-size: 12px;
            padding: 6px;
        }}
    """


def _scroll_bar_style():
    return f"""
        QScrollBar:vertical {{
            background: transparent; width: 5px; margin: 0;
        }}
        QScrollBar::handle:vertical {{
            background: {_th.border_color(0.30)};
            border-radius: 2px; min-height: 20px;
        }}
        QScrollBar::handle:vertical:hover {{ background: {_th.border_color(0.50)}; }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
        QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}

        QScrollBar:horizontal {{
            background: transparent; height: 5px; margin: 0;
        }}
        QScrollBar::handle:horizontal {{
            background: {_th.border_color(0.30)};
            border-radius: 2px; min-width: 20px;
        }}
        QScrollBar::handle:horizontal:hover {{ background: {_th.border_color(0.50)}; }}
        QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}
        QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{ background: transparent; }}
    """


def _sub_tab_style():
    return f"""
        QTabWidget::pane {{
            border: none;
            background: transparent;
            margin: 0;
            padding: 0;
        }}
        QTabBar {{
            background: {_th.BG2};
            border-bottom: 1px solid {_th.border_color(0.08)};
            font-size: 11px;
            font-weight: 500;
        }}
        QTabBar::tab {{
            background: transparent;
            color: {_th.FG_MUTED};
            padding: 6px 12px;
            height: 20px;
            border-bottom: 2px solid transparent;
            margin: 0;
        }}
        QTabBar::tab:hover {{
            color: {_th.FG_HOVER};
            background: {_th.border_color(0.04)};
        }}
        QTabBar::tab:selected {{
            color: {_th.FG_BRIGHT};
            font-weight: 600;
            border-bottom: 2px solid {_th.accent_hex};
            background: {_th.border_color(0.02)};
        }}
    """


class _EntropyMeter(QWidget):

    def __init__(self, parent=None):
        super().__init__(parent)
        self._entropy = 0.0
        self._color = "#2196F3"
        self.setFixedHeight(8)
        self.setMinimumWidth(180)

    def set_entropy(self, entropy: float, color: str = "#2196F3"):
        self._entropy = max(0.0, min(8.0, entropy))
        self._color = color
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = self.rect()

        bg_rect = QRectF(r.x(), r.y(), r.width(), r.height())
        path_bg = QPainterPath()
        path_bg.addRoundedRect(bg_rect, 3, 3)
        p.fillPath(path_bg, QColor(_th.border_color(0.12)))

        pct = self._entropy / 8.0
        fill_w = max(4.0, bg_rect.width() * pct)
        fill_rect = QRectF(bg_rect.x(), bg_rect.y(), fill_w, bg_rect.height())
        path_fill = QPainterPath()
        path_fill.addRoundedRect(fill_rect, 3, 3)
        p.fillPath(path_fill, QColor(self._color))

        p.end()


class StringsTableModel(QAbstractTableModel):
    HEADERS = ["Offset (Hex)", "Encoding", "Length", "String Content"]

    def __init__(self, strings: Optional[List[Dict[str, Any]]] = None, parent=None):
        super().__init__(parent)
        self._strings: List[Dict[str, Any]] = strings if strings is not None else []
        self._mono_font = MONO_FONT
        self._col_blue = _qcolor(_th.FG_BLUE) if hasattr(_th, "FG_BLUE") else QColor("#2196F3")
        self._col_muted = _qcolor(_th.FG_MUTED) if hasattr(_th, "FG_MUTED") else QColor("#888888")
        self._col_fg = _qcolor(_th.FG) if hasattr(_th, "FG") else QColor("#ECEFF4")

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        if parent is not None and parent.isValid():
            return 0
        return len(self._strings)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        if parent is not None and parent.isValid():
            return 0
        return 4

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.DisplayRole):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            if 0 <= section < len(self.HEADERS):
                return self.HEADERS[section]
        return None

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole):
        if not index.isValid():
            return None
        row = index.row()
        col = index.column()
        if not (0 <= row < len(self._strings)):
            return None

        item = self._strings[row]

        if role == Qt.DisplayRole:
            if col == 0:
                return f"0x{item['offset']:08X}"
            elif col == 1:
                return str(item.get("encoding", "ASCII"))
            elif col == 2:
                return str(item.get("length", len(item.get("text", ""))))
            elif col == 3:
                return str(item.get("text", ""))
        elif role == Qt.ForegroundRole:
            if col == 0:
                return self._col_blue
            elif col in (1, 2):
                return self._col_muted
            elif col == 3:
                return self._col_fg
        elif role == Qt.FontRole:
            if col in (0, 3):
                return self._mono_font

        return None

    def set_strings(self, strings: List[Dict[str, Any]]):
        self.beginResetModel()
        self._strings = strings
        self.endResetModel()


class HexViewer(DeferredCloseViewer, QWidget):


    PAGE_SIZE_OPTIONS = [1024, 2048, 4096, 8192, 16384]

    def __init__(self, filepath: str, parent=None, loading_handler=None):
        super().__init__(parent)
        self.filepath = filepath
        self.loading_handler = loading_handler
        self.file_size = os.path.getsize(filepath) if os.path.exists(filepath) else 0

        self.current_page_size = 4096
        self.current_page = 0
        self.total_pages = max(1, (self.file_size + self.current_page_size - 1) // self.current_page_size)

        self._fmt_info = {}
        self._entropy = 0.0
        self._hashes = {}
        self._strings_cache = []
        self._filtered_strings = []
        self._strings_model = StringsTableModel([], self)

        self._build_ui()
        self._load_analysis()
        self._load_page(0)


    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.setStyleSheet(f"""
            QWidget {{
                color: {_th.FG};
                font-family: 'Poppins', 'Inter', 'Segoe UI', sans-serif;
            }}
            {_scroll_bar_style()}
        """)

        # 1. Top Section: Sub-Tabs directly below editor tab bar
        self.tabs = QTabWidget()
        self.tabs.setStyleSheet(_sub_tab_style())

        self.tabs.addTab(self._build_overview_tab(), t("so_tab_overview"))
        self.tabs.addTab(self._build_hex_tab(), "Hex Dump")
        self.tabs.addTab(self._build_strings_tab(), t("so_tab_strings"))
        self.tabs.addTab(self._build_hashes_tab(), "Hashes & Security")

        root.addWidget(self.tabs, 1)

        # ── 2. Bottom Section: Minimal Status & Action Bar (Matching SoViewer) ─
        self.footer_widget = QWidget()
        self.footer_widget.setFixedHeight(28)
        self.footer_widget.setStyleSheet(f"""
            QWidget {{
                background: {_th.BG2};
                border-top: 1px solid {_th.border_color(0.06)};
            }}
        """)
        fl = QHBoxLayout(self.footer_widget)
        fl.setContentsMargins(10, 0, 8, 0)
        fl.setSpacing(12)

        # Status text on the left (integrated, minimal)
        self.status_lbl = QLabel("Analyzing binary...")
        self.status_lbl.setStyleSheet(f"color: {_th.FG3}; font-size: 11px; background: transparent; border: none;")
        fl.addWidget(self.status_lbl)

        fl.addStretch()

        # Action Buttons on the right (sleek transparent buttons matching theme)
        btn_copy_hex = QPushButton("Copy Hex")
        btn_copy_hex.setCursor(Qt.PointingHandCursor)
        btn_copy_hex.setStyleSheet(_footer_btn())
        btn_copy_hex.clicked.connect(self._on_copy_page_hex)
        fl.addWidget(btn_copy_hex)

        btn_copy_ascii = QPushButton("Copy ASCII")
        btn_copy_ascii.setCursor(Qt.PointingHandCursor)
        btn_copy_ascii.setStyleSheet(_footer_btn())
        btn_copy_ascii.clicked.connect(self._on_copy_page_ascii)
        fl.addWidget(btn_copy_ascii)

        btn_export = QPushButton("Export Strings...")
        btn_export.setCursor(Qt.PointingHandCursor)
        btn_export.setStyleSheet(_footer_btn())
        btn_export.clicked.connect(self._on_export_strings)
        fl.addWidget(btn_export)

        reload_btn = QPushButton(t("so_btn_reload"))
        reload_btn.setCursor(Qt.PointingHandCursor)
        reload_btn.setStyleSheet(_footer_btn())
        reload_btn.clicked.connect(self._on_reload)
        fl.addWidget(reload_btn)

        root.addWidget(self.footer_widget)


    def _build_overview_tab(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("background: transparent; border: none;")

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        # ── Card 1: Overview Text (Like SoViewer) ──
        card1 = QFrame()
        card1.setStyleSheet(_card_style(radius=6))
        c1_lay = QVBoxLayout(card1)
        c1_lay.setContentsMargins(12, 10, 12, 10)
        c1_lay.setSpacing(6)

        c1_title = QLabel(t("so_tab_overview"))
        c1_title.setStyleSheet(f"color: {_th.FG_BRIGHT}; font-size: 12px; font-weight: 600; border: none; background: transparent;")
        c1_lay.addWidget(c1_title)

        self.overview_text = QPlainTextEdit()
        self.overview_text.setReadOnly(True)
        self.overview_text.setFont(MONO_FONT)
        self.overview_text.setStyleSheet(_text_view_style())
        self.overview_text.setMinimumHeight(180)
        c1_lay.addWidget(self.overview_text)

        layout.addWidget(card1)

        # ── Card 2: Shannon Entropy & Complexity ──
        card2 = QFrame()
        card2.setStyleSheet(_card_style(radius=6))
        c2_lay = QVBoxLayout(card2)
        c2_lay.setContentsMargins(12, 10, 12, 10)
        c2_lay.setSpacing(6)

        c2_title = QLabel("Shannon Entropy & Data Complexity")
        c2_title.setStyleSheet(f"color: {_th.FG_BRIGHT}; font-size: 12px; font-weight: 600; border: none; background: transparent;")
        c2_lay.addWidget(c2_title)

        self.lbl_entropy_val = QLabel("Entropy: Calculating...")
        self.lbl_entropy_val.setStyleSheet(f"color: {_th.FG}; font-size: 11px; border: none; background: transparent;")
        c2_lay.addWidget(self.lbl_entropy_val)

        self.entropy_meter = _EntropyMeter()
        c2_lay.addWidget(self.entropy_meter)

        self.lbl_entropy_desc = QLabel("Analyzing data complexity...")
        self.lbl_entropy_desc.setStyleSheet(f"color: {_th.FG2}; font-size: 11px; border: none; background: transparent;")
        self.lbl_entropy_desc.setWordWrap(True)
        c2_lay.addWidget(self.lbl_entropy_desc)

        layout.addWidget(card2)
        layout.addStretch()

        scroll.setWidget(container)
        return scroll

    def _build_hex_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(10, 8, 10, 8)
        lay.setSpacing(8)

        # ── Search & Navigation Toolbar ──
        tb = QHBoxLayout()
        tb.setSpacing(8)

        # Paging buttons
        self.btn_prev_page = QPushButton("◀ Prev")
        self.btn_prev_page.setCursor(Qt.PointingHandCursor)
        self.btn_prev_page.setStyleSheet(_footer_btn())
        self.btn_prev_page.clicked.connect(self._on_prev_page)
        tb.addWidget(self.btn_prev_page)

        self.lbl_page_info = QLabel("Page 1 / 1")
        self.lbl_page_info.setStyleSheet(f"color: {_th.FG}; font-size: 11px; font-weight: 600;")
        tb.addWidget(self.lbl_page_info)

        self.btn_next_page = QPushButton("Next ▶")
        self.btn_next_page.setCursor(Qt.PointingHandCursor)
        self.btn_next_page.setStyleSheet(_footer_btn())
        self.btn_next_page.clicked.connect(self._on_next_page)
        tb.addWidget(self.btn_next_page)

        tb.addSpacing(8)

        lbl_psz = QLabel("Page Size:")
        lbl_psz.setStyleSheet(f"color: {_th.FG2}; font-size: 11px;")
        tb.addWidget(lbl_psz)

        self.cb_page_size = QComboBox()
        self.cb_page_size.setStyleSheet(_combo_style())
        for sz in self.PAGE_SIZE_OPTIONS:
            self.cb_page_size.addItem(f"{sz} B ({sz//1024} KB)", sz)
        self.cb_page_size.setCurrentIndex(2)  # 4096 B
        self.cb_page_size.currentIndexChanged.connect(self._on_page_size_changed)
        tb.addWidget(self.cb_page_size)

        tb.addSpacing(10)

        lbl_jump = QLabel("Go to:")
        lbl_jump.setStyleSheet(f"color: {_th.FG2}; font-size: 11px;")
        tb.addWidget(lbl_jump)

        self.txt_jump = QLineEdit()
        self.txt_jump.setPlaceholderText("0x0000")
        self.txt_jump.setFixedWidth(80)
        self.txt_jump.setStyleSheet(_input_style())
        self.txt_jump.returnPressed.connect(self._on_jump_offset)
        tb.addWidget(self.txt_jump)

        btn_jump = QPushButton("Go")
        btn_jump.setCursor(Qt.PointingHandCursor)
        btn_jump.setStyleSheet(_btn_primary())
        btn_jump.clicked.connect(self._on_jump_offset)
        tb.addWidget(btn_jump)

        tb.addStretch()

        lbl_search = QLabel("Search:")
        lbl_search.setStyleSheet(f"color: {_th.FG2}; font-size: 11px;")
        tb.addWidget(lbl_search)

        self.txt_search_hex = QLineEdit()
        self.txt_search_hex.setPlaceholderText("Text or Hex (e.g. 41 4C 50 53)...")
        self.txt_search_hex.setFixedWidth(200)
        self.txt_search_hex.setStyleSheet(_input_style())
        self.txt_search_hex.returnPressed.connect(self._on_search_hex_next)
        tb.addWidget(self.txt_search_hex)

        btn_find_prev = QPushButton("▲")
        btn_find_prev.setToolTip("Find Previous")
        btn_find_prev.setCursor(Qt.PointingHandCursor)
        btn_find_prev.setStyleSheet(_footer_btn())
        btn_find_prev.setFixedWidth(24)
        btn_find_prev.clicked.connect(self._on_search_hex_prev)
        tb.addWidget(btn_find_prev)

        btn_find_next = QPushButton("▼")
        btn_find_next.setToolTip("Find Next")
        btn_find_next.setCursor(Qt.PointingHandCursor)
        btn_find_next.setStyleSheet(_footer_btn())
        btn_find_next.setFixedWidth(24)
        btn_find_next.clicked.connect(self._on_search_hex_next)
        tb.addWidget(btn_find_next)

        lay.addLayout(tb)

        # ── Column Header Label ──
        hdr_row = QHBoxLayout()
        hdr_row.setContentsMargins(8, 0, 8, 0)
        hdr_lbl = QLabel(f"{'Offset':<10} {'00 01 02 03 04 05 06 07  08 09 0A 0B 0C 0D 0E 0F':<48} {'Decoded Text':<16}")
        hdr_lbl.setFont(MONO_FONT)
        hdr_lbl.setStyleSheet(f"color: {_th.FG_LABEL}; font-weight: bold; font-size: 11px;")
        hdr_row.addWidget(hdr_lbl)
        lay.addLayout(hdr_row)

        # ── Hex Dump Body ──
        self.txt_hex_dump = QPlainTextEdit()
        self.txt_hex_dump.setReadOnly(True)
        self.txt_hex_dump.setFont(MONO_FONT)
        self.txt_hex_dump.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.txt_hex_dump.setStyleSheet(_text_view_style())
        lay.addWidget(self.txt_hex_dump, 1)

        return w

    def _build_strings_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(10, 8, 10, 8)
        lay.setSpacing(8)

        # Toolbar
        tb = QHBoxLayout()
        tb.setSpacing(8)

        lbl_filter = QLabel(t("so_search"))
        lbl_filter.setStyleSheet(f"color: {_th.FG2}; font-size: 11px;")
        tb.addWidget(lbl_filter)

        self.txt_str_filter = QLineEdit()
        self.txt_str_filter.setPlaceholderText("Filter raw strings...")
        self.txt_str_filter.setStyleSheet(_input_style())
        self.txt_str_filter.textChanged.connect(self._on_strings_filter_changed)
        tb.addWidget(self.txt_str_filter, 1)

        lbl_min_len = QLabel("Min Length:")
        lbl_min_len.setStyleSheet(f"color: {_th.FG2}; font-size: 11px;")
        tb.addWidget(lbl_min_len)

        self.sp_min_len = QSpinBox()
        self.sp_min_len.setRange(3, 32)
        self.sp_min_len.setValue(4)
        self.sp_min_len.setStyleSheet(_spin_style())
        self.sp_min_len.valueChanged.connect(self._on_min_len_changed)
        tb.addWidget(self.sp_min_len)

        btn_copy_filtered = QPushButton("Copy Filtered")
        btn_copy_filtered.setCursor(Qt.PointingHandCursor)
        btn_copy_filtered.setStyleSheet(_footer_btn())
        btn_copy_filtered.clicked.connect(self._on_copy_filtered_strings)
        tb.addWidget(btn_copy_filtered)

        lay.addLayout(tb)

        # Strings Table (matching SoViewer table style)
        self.tbl_strings = QTableView()
        self.tbl_strings.setModel(self._strings_model)
        self.tbl_strings.setStyleSheet(_table_style())
        header = self.tbl_strings.horizontalHeader()
        header.setResizeContentsPrecision(100)
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.Stretch)
        self.tbl_strings.setSelectionBehavior(QTableView.SelectRows)
        self.tbl_strings.setEditTriggers(QTableView.NoEditTriggers)
        self.tbl_strings.doubleClicked.connect(self._on_strings_table_double_clicked)
        lay.addWidget(self.tbl_strings, 1)

        return w

    def _build_hashes_tab(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("background: transparent; border: none;")

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        card = QFrame()
        card.setStyleSheet(_card_style(radius=6))
        c_lay = QVBoxLayout(card)
        c_lay.setContentsMargins(12, 10, 12, 10)
        c_lay.setSpacing(8)

        card_title = QLabel("Cryptographic Hashes & Integrity")
        card_title.setStyleSheet(f"color: {_th.FG_BRIGHT}; font-size: 12px; font-weight: 600; border: none; background: transparent;")
        c_lay.addWidget(card_title)

        self.lbl_hash_md5 = self._create_hash_row(c_lay, "MD5")
        self.lbl_hash_sha1 = self._create_hash_row(c_lay, "SHA-1")
        self.lbl_hash_sha256 = self._create_hash_row(c_lay, "SHA-256")

        layout.addWidget(card)
        layout.addStretch()

        scroll.setWidget(container)
        return scroll

    def _create_hash_row(self, layout: QVBoxLayout, label_text: str) -> QLabel:
        row = QHBoxLayout()
        row.setSpacing(8)

        lbl_title = QLabel(f"{label_text}:")
        lbl_title.setFixedWidth(65)
        lbl_title.setStyleSheet(f"color: {_th.FG2}; font-size: 11px;")
        row.addWidget(lbl_title)

        lbl_val = QLabel("Calculating...")
        lbl_val.setFont(MONO_FONT)
        lbl_val.setStyleSheet(f"color: {_th.FG}; font-size: 11px;")
        row.addWidget(lbl_val, 1)

        btn_copy = QPushButton("Copy")
        btn_copy.setCursor(Qt.PointingHandCursor)
        btn_copy.setStyleSheet(_footer_btn())
        btn_copy.clicked.connect(lambda: QApplication.clipboard().setText(lbl_val.text()))
        row.addWidget(btn_copy)

        layout.addLayout(row)
        return lbl_val


    def _load_page(self, page_idx: int):
        if page_idx < 0 or (self.file_size > 0 and page_idx >= self.total_pages):
            return

        self.current_page = page_idx
        offset = page_idx * self.current_page_size
        chunk_len = min(self.current_page_size, self.file_size - offset) if self.file_size > 0 else 0

        raw_bytes = bin_analyzer.read_hex_chunk(self.filepath, offset, chunk_len)
        formatted_lines = bin_analyzer.format_hex_lines(raw_bytes, base_offset=offset)

        text_lines = []
        for line in formatted_lines:
            text_lines.append(f"{line['offset_str']}  {line['hex_str']}  |{line['ascii_str']}|")

        self.txt_hex_dump.setPlainText("\n".join(text_lines))

        self.lbl_page_info.setText(f"Page {self.current_page + 1} / {self.total_pages}")
        self.btn_prev_page.setEnabled(self.current_page > 0)
        self.btn_next_page.setEnabled(self.current_page < self.total_pages - 1)

        end_offset = offset + len(raw_bytes) - 1 if len(raw_bytes) > 0 else 0
        self.status_lbl.setText(
            f"Offset: 0x{offset:08X} - 0x{end_offset:08X} ({len(raw_bytes):,} / {self.file_size:,} Bytes) | Total Pages: {self.total_pages}"
        )

    def _on_prev_page(self):
        if self.current_page > 0:
            self._load_page(self.current_page - 1)

    def _on_next_page(self):
        if self.current_page < self.total_pages - 1:
            self._load_page(self.current_page + 1)

    def _on_page_size_changed(self):
        self.current_page_size = self.cb_page_size.currentData()
        self.total_pages = max(1, (self.file_size + self.current_page_size - 1) // self.current_page_size)
        self._load_page(0)

    def _on_jump_offset(self):
        txt = self.txt_jump.text().strip()
        if not txt:
            return
        try:
            if txt.lower().startswith("0x"):
                target_offset = int(txt, 16)
            else:
                target_offset = int(txt)
        except ValueError:
            return

        if target_offset < 0:
            target_offset = 0
        if target_offset >= self.file_size and self.file_size > 0:
            target_offset = self.file_size - 1

        target_page = target_offset // self.current_page_size
        self._load_page(target_page)

    def _on_search_hex_next(self):
        self._do_search_hex(forward=True)

    def _on_search_hex_prev(self):
        self._do_search_hex(forward=False)

    def _do_search_hex(self, forward=True):
        query = self.txt_search_hex.text().strip()
        if not query:
            return

        pattern_bytes = None
        hex_clean = query.replace(" ", "").replace("0x", "")
        if all(c in "0123456789abcdefABCDEF" for c in hex_clean) and len(hex_clean) % 2 == 0 and len(hex_clean) >= 2:
            try:
                pattern_bytes = bytes.fromhex(hex_clean)
            except Exception:
                pattern_bytes = None

        if pattern_bytes is None:
            pattern_bytes = query.encode("utf-8", errors="ignore")

        if not pattern_bytes:
            return

        start_offset = self.current_page * self.current_page_size + (1 if forward else -1)
        found_offset = self._find_in_file(pattern_bytes, start_offset, forward=forward)

        if found_offset >= 0:
            target_page = found_offset // self.current_page_size
            self._load_page(target_page)
            self.status_lbl.setText(f"Match found at offset 0x{found_offset:08X}")
        else:
            self.status_lbl.setText(f"Pattern '{query}' not found.")

    def _find_in_file(self, pattern: bytes, start_pos: int, forward=True) -> int:
        try:
            with open(self.filepath, "rb") as f:
                if forward:
                    f.seek(max(0, start_pos))
                    pos = max(0, start_pos)
                    overlap = len(pattern) - 1
                    buf = b""
                    while True:
                        chunk = f.read(65536)
                        if not chunk:
                            break
                        data = buf + chunk
                        idx = data.find(pattern)
                        if idx >= 0:
                            return pos - len(buf) + idx
                        buf = data[-overlap:] if overlap > 0 else b""
                        pos += len(chunk)
                else:
                    f.seek(0)
                    all_data = f.read(min(self.file_size, 10 * 1024 * 1024))
                    idx = all_data[:max(0, start_pos)].rfind(pattern)
                    if idx >= 0:
                        return idx
        except Exception:
            pass
        return -1


    def _load_analysis(self):
        self.mark_task_started()
        self.status_lbl.setText("Analyzing binary...")

        def _worker():
            fmt = bin_analyzer.detect_binary_format(self.filepath)
            with open(self.filepath, "rb") as fp:
                head = fp.read(min(self.file_size, 65536))
            entropy = bin_analyzer.calc_entropy(head)
            ent_label, ent_color = bin_analyzer.get_entropy_label(entropy)

            hashes = bin_analyzer.calc_hashes(self.filepath, max_bytes=20 * 1024 * 1024)
            strings = bin_analyzer.extract_strings(self.filepath, min_len=4, max_strings=10000)

            return True, {
                "format": fmt,
                "entropy": entropy,
                "entropy_label": ent_label,
                "entropy_color": ent_color,
                "hashes": hashes,
                "strings": strings
            }

        def _on_done(ok, res):
            if not ok or not res:
                self.status_lbl.setText("Analysis failed.")
                return

            self._fmt_info = res.get("format", {})
            self._entropy = res.get("entropy", 0.0)
            self._hashes = res.get("hashes", {})
            self._strings_cache = res.get("strings", [])

            # Populate Overview Text
            fmt_name = self._fmt_info.get("format", "Generic Binary")
            vendor = self._fmt_info.get("vendor", "")
            cat = self._fmt_info.get("category", "")
            desc = self._fmt_info.get("description", "")
            extras = self._fmt_info.get("extra", {})

            overview_lines = [
                f"File Name: {os.path.basename(self.filepath)}",
                f"File Path: {self.filepath}",
                f"File Size: {self._format_size(self.file_size)}",
                "",
                f"Detected Format: {fmt_name}",
                f"Vendor / Subsystem: {vendor} ({cat})",
                f"Description: {desc}",
            ]
            if extras:
                overview_lines.append("")
                overview_lines.append("Detected Headers / Tags:")
                for k, v in extras.items():
                    overview_lines.append(f"  [{k}] {v}")

            overview_lines.append("")
            overview_lines.append(f"Extracted Strings: {len(self._strings_cache):,} found")
            overview_lines.append(f"MD5: {self._hashes.get('md5', '-')}")
            overview_lines.append(f"SHA-256: {self._hashes.get('sha256', '-')}")

            self.overview_text.setPlainText("\n".join(overview_lines))

            # Entropy
            ent_val = res.get("entropy", 0.0)
            ent_desc = res.get("entropy_label", "")
            ent_col = res.get("entropy_color", "#2196F3")
            self.lbl_entropy_val.setText(f"Shannon Entropy: {ent_val:.2f} / 8.00 ({ent_desc})")
            self.entropy_meter.set_entropy(ent_val, ent_col)
            self.lbl_entropy_desc.setText(
                f"Statistical data randomness rating: {ent_desc}. Indicates machine code density, compression, or waveform table."
            )

            # Hashes
            self.lbl_hash_md5.setText(self._hashes.get("md5", "-"))
            self.lbl_hash_sha1.setText(self._hashes.get("sha1", "-"))
            self.lbl_hash_sha256.setText(self._hashes.get("sha256", "-"))

            # Populate strings
            self._populate_strings_table(self._strings_cache)

            self.status_lbl.setText(f"Loaded: {fmt_name} | {len(self._strings_cache):,} strings")

        run_in_thread(_worker, None, callback=self.guard_task_callback(_on_done))


    def _populate_strings_table(self, strings_list: List[Dict[str, Any]]):
        self._filtered_strings = strings_list
        self._strings_model.set_strings(strings_list)

    def _on_strings_filter_changed(self, text: str):
        query = text.strip().lower()
        if not query:
            self._populate_strings_table(self._strings_cache)
            return

        filtered = [s for s in self._strings_cache if query in s["text"].lower() or query in f"0x{s['offset']:08x}"]
        self._populate_strings_table(filtered)

    def _on_min_len_changed(self, val: int):
        self.mark_task_started()

        def _re_extract():
            return True, bin_analyzer.extract_strings(self.filepath, min_len=val, max_strings=15000)

        def _done(ok, res):
            if ok and res is not None:
                self._strings_cache = res
                self._on_strings_filter_changed(self.txt_str_filter.text())

        run_in_thread(_re_extract, None, callback=self.guard_task_callback(_done))

    def _on_strings_table_double_clicked(self, index: QModelIndex):
        if index.isValid():
            self._on_string_double_clicked(index.row(), index.column())

    def _on_string_double_clicked(self, row: int, col: int):
        if 0 <= row < len(self._filtered_strings):
            offset = self._filtered_strings[row]["offset"]
            self.tabs.setCurrentIndex(1)  # Switch to Hex Dump Tab
            target_page = offset // self.current_page_size
            self._load_page(target_page)
            self.status_lbl.setText(f"Jumped to string at offset 0x{offset:08X}")

    def _on_export_strings(self):
        if not self._strings_cache:
            return
        default_name = os.path.splitext(os.path.basename(self.filepath))[0] + "_strings.txt"
        dest, _ = QFileDialog.getSaveFileName(self, "Export Extracted Strings", default_name, "Text files (*.txt);;All files (*)")
        if dest:
            try:
                with open(dest, "w", encoding="utf-8") as f:
                    for s in (self._filtered_strings or self._strings_cache):
                        f.write(f"0x{s['offset']:08X}\t[{s['encoding']}]\t{s['text']}\n")
                QMessageBox.information(self, "Export Complete", f"Exported {len(self._filtered_strings or self._strings_cache)} strings to {dest}")
            except Exception:
                import traceback
                _AppDialog.error(self, "Export Failed", traceback.format_exc())

    def _on_copy_filtered_strings(self):
        lines = [s["text"] for s in (self._filtered_strings or self._strings_cache)]
        QApplication.clipboard().setText("\n".join(lines))
        self.status_lbl.setText(f"Copied {len(lines)} strings to clipboard.")


    def _on_copy_page_hex(self):
        raw = bin_analyzer.read_hex_chunk(self.filepath, self.current_page * self.current_page_size, self.current_page_size)
        hex_str = " ".join(f"{b:02X}" for b in raw)
        QApplication.clipboard().setText(hex_str)
        self.status_lbl.setText("Copied current page hex to clipboard.")

    def _on_copy_page_ascii(self):
        raw = bin_analyzer.read_hex_chunk(self.filepath, self.current_page * self.current_page_size, self.current_page_size)
        ascii_str = "".join(chr(b) if 32 <= b <= 126 else "." for b in raw)
        QApplication.clipboard().setText(ascii_str)
        self.status_lbl.setText("Copied current page ASCII to clipboard.")

    def _on_reload(self):
        self.file_size = os.path.getsize(self.filepath) if os.path.exists(self.filepath) else 0
        self.total_pages = max(1, (self.file_size + self.current_page_size - 1) // self.current_page_size)
        self._load_page(self.current_page)
        self._load_analysis()

    @staticmethod
    def _format_size(sz: int) -> str:
        if sz < 1024:
            return f"{sz} B"
        elif sz < 1024 * 1024:
            return f"{sz / 1024:.2f} KB ({sz:,} B)"
        elif sz < 1024 * 1024 * 1024:
            return f"{sz / (1024 * 1024):.2f} MB ({sz:,} B)"
        else:
            return f"{sz / (1024 * 1024 * 1024):.2f} GB ({sz:,} B)"
