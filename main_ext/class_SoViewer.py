# SPDX-License-Identifier: MPL-2.0
import os
import sys
import json
import tempfile
from typing import Optional

from PySide6.QtCore import Qt, QSize, Signal
from PySide6.QtGui import (
    QIcon, QFont, QPixmap, QColor, QPainter, QPainterPath, QPen,
    QClipboard, QAction, QCursor
)
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTabWidget, QTabBar, QTableWidget, QTableWidgetItem,
    QTreeWidget, QTreeWidgetItem, QPlainTextEdit, QLineEdit,
    QComboBox, QSpinBox, QCheckBox, QGroupBox, QDialog,
    QFileDialog, QMessageBox, QHeaderView, QStyle, QScrollArea,
    QFrame, QSizePolicy, QApplication
)

try:
    from core import so_core
except ImportError:
    import so_core

from main_ext.class_ThemeUi import theme as _th
from main_ext.class_AppDialog import AppDialog as _AppDialog
from main_ext.class_DeferredCloseViewer import DeferredCloseViewer
from core.ui_lang import t
from core.threader import run_in_thread
from icons.handler import icon

print("[MAIN STAGE] SO_VIEWER")

MONO_FONT = QFont("Consolas" if sys.platform == "win32" else "Monospace", 10)
MONO_FONT.setStyleHint(QFont.TypeWriter)


class _FileTreeIconResolver:

    _CACHE = {}

    @classmethod
    def resolve_icon(cls, name: str, is_dir: bool = False, is_embedded: bool = False, file_type: str = "") -> QIcon:
        name_lower = name.lower()
        key = (name_lower, is_dir, is_embedded, file_type.lower())
        if key in cls._CACHE:
            return cls._CACHE[key]

        if is_dir:
            ic = QIcon(icon("folder"))
            cls._CACHE[key] = ic
            return ic

        if is_embedded:
            ft = file_type.lower()
            if "image" in ft or any(ext in name_lower for ext in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg")):
                ic = QIcon(icon("image"))
            elif "archive" in ft or "zip" in ft or "apk" in ft or "jar" in ft:
                ic = QIcon(icon("archive"))
            elif "elf" in ft or "so" in ft:
                ic = QIcon(icon("icon_so"))
            else:
                ic = QIcon(icon("drive"))
            cls._CACHE[key] = ic
            return ic

        ext = os.path.splitext(name_lower)[1]

        # FS metadata check
        if (
            name_lower.endswith("_file_contexts") or
            name_lower.endswith("_fs_config") or
            name_lower.endswith("_fs_options") or
            name_lower.endswith("_info")
        ):
            ic = QIcon(icon("linux_metadata"))
            cls._CACHE[key] = ic
            return ic

        # Direct extension map to icon keys
        ext_map = {
            ".img": "drive",
            ".br": "icon_brotli", ".brotli": "icon_brotli",
            ".bin": "icon_bin",
            ".so": "icon_so",
            ".apk": "apk",
            ".jar": "archive_jar",
            ".7z": "archive_7z",
            ".tar": "archive_tar", ".tgz": "archive_tar",
            ".bz2": "archive_gz", ".xz": "archive_gz", ".lz": "archive_gz", ".lz4": "archive_gz", ".zst": "archive_gz", ".gz": "archive_gz",
            ".rar": "archive_rar",
            ".iso": "archive_iso",
            ".json": "icon_json", ".jsonc": "icon_json",
            ".xml": "icon_xml", ".xsl": "icon_xml", ".xslt": "icon_xml", ".xsd": "icon_xml",
            ".cfg": "icon_cfg", ".conf": "icon_cfg", ".ini": "icon_cfg",
            ".csv": "icon_csv",
            ".py": "icon_py",
            ".asm": "icon_asm", ".s": "icon_asm", ".nasm": "icon_asm",
            ".hpp": "icon_hpp", ".hxx": "icon_hpp", ".hh": "icon_hpp",
            ".h": "icon_h",
            ".cpp": "icon_cpp", ".cc": "icon_cpp", ".cxx": "icon_cpp",
            ".c": "icon_c",
            ".cs": "icon_cs",
            ".java": "icon_java",
            ".js": "icon_js", ".mjs": "icon_js", ".cjs": "icon_js",
            ".html": "icon_html", ".htm": "icon_html",
            ".css": "icon_css",
            ".qss": "icon_qss",
            ".swift": "icon_swift",
            ".cobol": "icon_cobol",
            ".rc": "icon_rc",
            ".prop": "icon_prop",
            ".rs": "icon_rust",
            ".php": "icon_php",
            ".kt": "icon_kt", ".kts": "icon_kt",
            ".smali": "icon_smali",
            ".sh": "icon_sh", ".bash": "icon_sh", ".zsh": "icon_zsh", ".fish": "icon_fish",
            ".txt": "txt",
            ".mp3": "icon_audio", ".wav": "icon_audio", ".ogg": "icon_audio", ".flac": "icon_audio", ".m4a": "icon_audio", ".aac": "icon_audio",
            ".mp4": "icon_video", ".mkv": "icon_video", ".avi": "icon_video", ".mov": "icon_video", ".webm": "icon_video",
            ".png": "image", ".jpg": "image", ".jpeg": "image", ".gif": "image", ".webp": "image", ".bmp": "image", ".svg": "image",
        }

        icon_key = ext_map.get(ext)
        if not icon_key:
            if name_lower.startswith("."):
                icon_key = "file"
            else:
                icon_key = "file"

        icon_path = icon(icon_key)
        if icon_path and os.path.exists(icon_path):
            ic = QIcon(icon_path)
        else:
            ic = QIcon(icon("file"))
        cls._CACHE[key] = ic
        return ic


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


def _tree_style():
    return f"""
        QTreeWidget {{
            background: {_th.BG};
            border: 1px solid {_th.border_color(0.06)};
            border-radius: 4px;
            color: {_th.FG};
            font-size: 12px;
            outline: none;
            padding: 2px;
        }}
        QTreeWidget::item {{
            height: 22px;
            padding: 2px 4px;
            border-radius: 4px;
            color: {_th.FG};
        }}
        QTreeWidget::item:hover {{
            background: {_th.border_color(0.05)};
            color: {_th.FG_BRIGHT};
        }}
        QTreeWidget::item:selected {{
            background: {_th.accent(0.20)};
            color: {_th.FG_BRIGHT};
        }}
        QHeaderView::section {{
            background: {_th.BG2};
            color: {_th.FG_LABEL};
            font-weight: bold;
            font-size: 11px;
            padding: 4px 8px;
            border: none;
            border-right: 1px solid {_th.border_color(0.12)};
            border-bottom: 1px solid {_th.border_color(0.12)};
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


class EmbeddedFileDialog(QDialog):
    def __init__(self, ef: "so_core.EmbeddedFile", parent=None):
        super().__init__(parent)
        self.ef = ef
        self.setWindowTitle(f"{ef.label}")
        self.resize(720, 500)
        self.setStyleSheet(f"""
            QDialog {{
                background: {_th.BG};
                color: {_th.FG};
            }}
            {_scroll_bar_style()}
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        # Info card
        info_card = QFrame()
        info_card.setStyleSheet(_card_style(radius=6))
        ic_layout = QVBoxLayout(info_card)
        ic_layout.setContentsMargins(12, 10, 12, 10)
        ic_layout.setSpacing(4)

        title_lbl = QLabel(f"<b>{ef.label}</b>")
        title_lbl.setStyleSheet(f"color: {_th.FG_BRIGHT}; font-size: 13px; border: none; background: transparent;")
        ic_layout.addWidget(title_lbl)

        precision_str = t("so_precision_exact") if ef.exact_length else t("so_precision_approx")
        details_lbl = QLabel(
            f"<b>{t('so_col_kind')}:</b> {ef.file_type} &nbsp;&nbsp;|&nbsp;&nbsp; "
            f"<b>Offset:</b> {hex(ef.offset)} &nbsp;&nbsp;|&nbsp;&nbsp; "
            f"<b>{t('so_col_size')}:</b> {so_core.human_size(len(ef.data))} ({precision_str})"
        )
        details_lbl.setStyleSheet(f"color: {_th.FG2}; font-size: 11px; border: none; background: transparent;")
        details_lbl.setWordWrap(True)
        ic_layout.addWidget(details_lbl)
        layout.addWidget(info_card)

        # Image preview if image format
        if ef.file_type in ("PNG image", "JPEG image", "GIF image"):
            pix = QPixmap()
            if pix.loadFromData(ef.data):
                img_box = QFrame()
                img_box.setStyleSheet(_card_style(radius=4))
                ib_lay = QVBoxLayout(img_box)
                preview = QLabel()
                scaled = pix.scaled(300, 200, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                preview.setPixmap(scaled)
                preview.setAlignment(Qt.AlignCenter)
                preview.setStyleSheet("border: none; background: transparent;")
                ib_lay.addWidget(preview)
                layout.addWidget(img_box)

        # Hex dump
        hex_view = QPlainTextEdit()
        hex_view.setReadOnly(True)
        hex_view.setFont(MONO_FONT)
        hex_view.setStyleSheet(_text_view_style())
        limit = 8192
        hex_view.setPlainText(self._hexdump(ef.data[:limit]))
        if len(ef.data) > limit:
            hex_view.appendPlainText(f"\n... ({so_core.human_size(len(ef.data) - limit)})")
        layout.addWidget(hex_view, 1)

        # Bottom buttons
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        save_btn = QPushButton(t("so_btn_save_as"))
        save_btn.setCursor(Qt.PointingHandCursor)
        save_btn.setStyleSheet(_btn_primary())
        save_btn.clicked.connect(self._save_as)
        btn_row.addWidget(save_btn)

        if ef.file_type.startswith("Nested ELF"):
            analyze_btn = QPushButton(t("so_btn_analyze_nested"))
            analyze_btn.setCursor(Qt.PointingHandCursor)
            analyze_btn.setStyleSheet(_footer_btn())
            analyze_btn.clicked.connect(self._analyze_nested)
            btn_row.addWidget(analyze_btn)

        btn_row.addStretch()
        close_btn = QPushButton(t("so_btn_close"))
        close_btn.setCursor(Qt.PointingHandCursor)
        close_btn.setStyleSheet(_footer_btn())
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)

    @staticmethod
    def _hexdump(data: bytes, width: int = 16) -> str:
        lines = []
        for i in range(0, len(data), width):
            chunk = data[i:i + width]
            hex_part = " ".join(f"{b:02x}" for b in chunk)
            ascii_part = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
            lines.append(f"{i:08x}  {hex_part:<{width*3}}  {ascii_part}")
        return "\n".join(lines) if lines else t("so_none")

    def _save_as(self):
        default_name = self.ef.label.split(" @ ")[0].split(" (")[0].strip()
        if not os.path.splitext(default_name)[1]:
            ext_map = {
                "PNG image": ".png", "JPEG image": ".jpg", "GIF image": ".gif",
                "ZIP/JAR/APK archive": ".zip", "Nested ELF (XZ compressed)": ".elf",
                "Nested ELF": ".elf"
            }
            default_name += ext_map.get(self.ef.file_type, ".bin")
        path, _ = QFileDialog.getSaveFileName(self, t("so_btn_save_as"), default_name)
        if path:
            with open(path, "wb") as f:
                f.write(self.ef.data)
            QMessageBox.information(self, "OK", t("so_saved_ok", path=path))

    def _analyze_nested(self):
        with tempfile.NamedTemporaryFile(suffix=".so", delete=False) as tmp:
            tmp.write(self.ef.data)
            tmp_path = tmp.name
        try:
            result = so_core.analyze_file(tmp_path)
        except Exception as e:
            _AppDialog.error(self, t("lbl_error"), t("so_parse_fail", err=str(e)))
            return
        finally:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass

        dlg = QDialog(self)
        dlg.setWindowTitle(t("so_nested_elf_title"))
        dlg.resize(600, 440)
        dlg.setStyleSheet(f"QDialog {{ background: {_th.BG}; color: {_th.FG}; }} {_scroll_bar_style()}")
        v = QVBoxLayout(dlg)
        v.setContentsMargins(14, 14, 14, 14)
        v.setSpacing(8)

        text = QPlainTextEdit()
        text.setReadOnly(True)
        text.setFont(MONO_FONT)
        text.setStyleSheet(_text_view_style())
        h = result["header"]
        lines = [
            f"Arch: {h['abi']} ({h['class']}-bit, {h['endianness']})",
            f"Type: {h['file_type']}",
            f"Sections: {h['num_sections']}  Segments: {h['num_segments']}",
            "",
            f"Symbols: {result['symbols']['total']}",
            "Exported:",
        ]
        for s in result["symbols"]["exported"][:50]:
            lines.append(f"  [export] {s['demangled']}")
        if len(result["symbols"]["exported"]) > 50:
            lines.append("  " + t("so_and_more_items", count=len(result['symbols']['exported']) - 50))
        text.setPlainText("\n".join(lines))
        v.addWidget(text)

        close_btn = QPushButton(t("so_btn_close"))
        close_btn.setStyleSheet(_footer_btn())
        close_btn.clicked.connect(dlg.accept)
        v.addWidget(close_btn, 0, Qt.AlignRight)
        dlg.exec()


class CompareSymbolsDialog(QDialog):
    def __init__(self, diff: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle(t("so_compare_title"))
        self.resize(760, 520)
        self.setStyleSheet(f"QDialog {{ background: {_th.BG}; color: {_th.FG}; }} {_scroll_bar_style()}")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        info_card = QFrame()
        info_card.setStyleSheet(_card_style(radius=6))
        ic_lay = QVBoxLayout(info_card)
        ic_lay.setContentsMargins(12, 10, 12, 10)
        ic_lay.setSpacing(4)

        ic_lay.addWidget(QLabel(f"<b>File A:</b> {os.path.basename(diff['file_a'])}"))
        ic_lay.addWidget(QLabel(f"<b>File B:</b> {os.path.basename(diff['file_b'])}"))
        ic_lay.addWidget(QLabel(f"{t('so_compare_common')} <b>{diff['common_count']}</b>"))
        layout.addWidget(info_card)

        text = QPlainTextEdit()
        text.setReadOnly(True)
        text.setFont(MONO_FONT)
        text.setStyleSheet(_text_view_style())

        lines = [f"=== ONLY IN {os.path.basename(diff['file_a'])} ({len(diff['only_in_a'])}) ===\n"]
        lines += [f"  + {s}" for s in diff["only_in_a"]]
        lines.append(f"\n=== ONLY IN {os.path.basename(diff['file_b'])} ({len(diff['only_in_b'])}) ===\n")
        lines += [f"  + {s}" for s in diff["only_in_b"]]
        text.setPlainText("\n".join(lines))
        layout.addWidget(text, 1)

        close_btn = QPushButton(t("so_btn_close"))
        close_btn.setStyleSheet(_footer_btn())
        close_btn.clicked.connect(self.accept)
        layout.addWidget(close_btn, 0, Qt.AlignRight)


class PathStringDialog(QDialog):
    def __init__(self, full_path: str, viewer=None, parent=None):
        super().__init__(parent or viewer)
        self.full_path = full_path
        self.viewer = viewer
        self.setWindowTitle(t("so_path_str_title"))
        self.resize(540, 220)
        self.setStyleSheet(f"QDialog {{ background: {_th.BG}; color: {_th.FG}; }} {_scroll_bar_style()}")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        # Card container
        card = QFrame()
        card.setStyleSheet(_card_style(radius=6))
        c_lay = QVBoxLayout(card)
        c_lay.setContentsMargins(12, 10, 12, 10)
        c_lay.setSpacing(8)

        title_lbl = QLabel(t("so_path_str_title"))
        title_lbl.setStyleSheet(f"color: {_th.FG_BRIGHT}; font-size: 13px; font-weight: 600; border: none; background: transparent;")
        c_lay.addWidget(title_lbl)

        path_box = QLineEdit(full_path)
        path_box.setReadOnly(True)
        path_box.setFont(MONO_FONT)
        path_box.setStyleSheet(_input_style())
        c_lay.addWidget(path_box)

        desc_lbl = QLabel(t("so_path_str_info"))
        desc_lbl.setStyleSheet(f"color: {_th.FG2}; font-size: 11px; border: none; background: transparent;")
        desc_lbl.setWordWrap(True)
        c_lay.addWidget(desc_lbl)

        layout.addWidget(card)

        # Buttons
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        copy_btn = QPushButton(t("so_btn_copy_path"))
        copy_btn.setStyleSheet(_btn_primary())
        copy_btn.setCursor(Qt.PointingHandCursor)
        def _copy():
            QApplication.clipboard().setText(self.full_path)
            copy_btn.setText(t("so_copied_toast"))
            QTimer.singleShot(1500, lambda: copy_btn.setText(t("so_btn_copy_path")))
        copy_btn.clicked.connect(_copy)
        btn_row.addWidget(copy_btn)

        find_btn = QPushButton(t("so_btn_view_raw_strings"))
        find_btn.setStyleSheet(_footer_btn())
        find_btn.setCursor(Qt.PointingHandCursor)
        def _jump():
            self.accept()
            if self.viewer and hasattr(self.viewer, "jump_to_raw_strings"):
                self.viewer.jump_to_raw_strings(self.full_path)
        find_btn.clicked.connect(_jump)
        btn_row.addWidget(find_btn)

        btn_row.addStretch()

        close_btn = QPushButton(t("so_btn_close"))
        close_btn.setStyleSheet(_footer_btn())
        close_btn.setCursor(Qt.PointingHandCursor)
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(close_btn)

        layout.addLayout(btn_row)


class SoViewer(DeferredCloseViewer, QWidget):


    def __init__(self, filepath: str, loading_handler=None):
        super().__init__()
        self.unsaved = False
        self.filepath = filepath
        self.loading_handler = loading_handler
        self.result: Optional[dict] = None

        self._build_ui()
        self._load_analysis()


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
        self.tabs.addTab(self._build_symbols_tab(), t("so_tab_symbols"))
        self.tabs.addTab(self._build_paths_tab(), t("so_tab_paths"))
        self.tabs.addTab(self._build_sections_tab(), t("so_tab_sections"))
        self.tabs.addTab(self._build_segments_tab(), t("so_tab_segments"))
        self.tabs.addTab(self._build_relocations_tab(), t("so_tab_relocations"))
        self.tabs.addTab(self._build_security_tab(), t("so_tab_security"))
        self.tabs.addTab(self._build_disasm_tab(), t("so_tab_disasm"))
        self.tabs.addTab(self._build_strings_tab(), t("so_tab_strings"))

        root.addWidget(self.tabs, 1)

        # ── 2. Bottom Section: Minimal Status & Action Bar (Clean & Themed) ──
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

        # Status text on the left (integrated, minimal, no loud boxes)
        self.status_lbl = QLabel(t("so_loading"))
        self.status_lbl.setStyleSheet(f"color: {_th.FG3}; font-size: 11px; background: transparent; border: none;")
        fl.addWidget(self.status_lbl)

        fl.addStretch()

        # Action Buttons on the right (sleek transparent buttons matching theme)
        export_btn = QPushButton(t("so_btn_export"))
        export_btn.setCursor(Qt.PointingHandCursor)
        export_btn.setStyleSheet(_footer_btn())
        export_btn.clicked.connect(self.export_json)
        fl.addWidget(export_btn)

        compare_btn = QPushButton(t("so_btn_compare"))
        compare_btn.setCursor(Qt.PointingHandCursor)
        compare_btn.setStyleSheet(_footer_btn())
        compare_btn.clicked.connect(self.compare_with_file)
        fl.addWidget(compare_btn)

        reload_btn = QPushButton(t("so_btn_reload"))
        reload_btn.setCursor(Qt.PointingHandCursor)
        reload_btn.setStyleSheet(_footer_btn())
        reload_btn.clicked.connect(self._load_analysis)
        fl.addWidget(reload_btn)

        root.addWidget(self.footer_widget)


    def _build_overview_tab(self):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("background: transparent; border: none;")

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        card = QFrame()
        card.setStyleSheet(_card_style(radius=6))
        card_lay = QVBoxLayout(card)
        card_lay.setContentsMargins(12, 10, 12, 10)
        card_lay.setSpacing(6)

        card_title = QLabel(t("so_tab_overview"))
        card_title.setStyleSheet(f"color: {_th.FG_BRIGHT}; font-size: 12px; font-weight: 600; border: none; background: transparent;")
        card_lay.addWidget(card_title)

        self.overview_text = QPlainTextEdit()
        self.overview_text.setReadOnly(True)
        self.overview_text.setFont(MONO_FONT)
        self.overview_text.setStyleSheet(_text_view_style())
        self.overview_text.setMinimumHeight(260)
        card_lay.addWidget(self.overview_text)

        layout.addWidget(card)
        layout.addStretch()

        scroll.setWidget(container)
        return scroll

    def _build_symbols_tab(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(10, 10, 10, 10)
        v.setSpacing(6)

        # Toolbar / filter row
        row = QHBoxLayout()
        row.setSpacing(6)

        lbl_search = QLabel(t("so_search"))
        lbl_search.setStyleSheet(f"color: {_th.FG2}; font-size: 11px;")
        row.addWidget(lbl_search)

        self.symbol_filter = QLineEdit()
        self.symbol_filter.setPlaceholderText(t("so_filter_symbols"))
        self.symbol_filter.setStyleSheet(_input_style())
        self.symbol_filter.textChanged.connect(self._refresh_symbol_table)
        row.addWidget(self.symbol_filter, 1)

        lbl_kind = QLabel(t("so_kind"))
        lbl_kind.setStyleSheet(f"color: {_th.FG2}; font-size: 11px;")
        row.addWidget(lbl_kind)

        self.symbol_kind_combo = QComboBox()
        self.symbol_kind_combo.addItems([t("so_kind_all"), "export", "import", "local"])
        self.symbol_kind_combo.setStyleSheet(_combo_style())
        self.symbol_kind_combo.currentTextChanged.connect(self._refresh_symbol_table)
        row.addWidget(self.symbol_kind_combo)

        self.mangled_checkbox = QCheckBox(t("so_mangled"))
        self.mangled_checkbox.setStyleSheet(f"color: {_th.FG_MUTED}; font-size: 11px;")
        self.mangled_checkbox.stateChanged.connect(self._refresh_symbol_table)
        row.addWidget(self.mangled_checkbox)

        self.symbol_count_lbl = QLabel("")
        self.symbol_count_lbl.setStyleSheet(f"color: {_th.FG_MUTED}; font-size: 11px;")
        row.addWidget(self.symbol_count_lbl)

        v.addLayout(row)

        self.symbol_table = QTableWidget(0, 5)
        self.symbol_table.setHorizontalHeaderLabels([
            t("so_col_kind"), t("so_col_name"), t("so_col_mangled"), t("so_col_value"), t("so_col_size")
        ])
        self.symbol_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.symbol_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.symbol_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.symbol_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.symbol_table.setStyleSheet(_table_style())
        self.symbol_table.setColumnHidden(2, True)
        v.addWidget(self.symbol_table)
        return w

    def _build_paths_tab(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(10, 10, 10, 10)
        v.setSpacing(6)

        # Search box for paths
        row = QHBoxLayout()
        lbl_p = QLabel(t("so_search"))
        lbl_p.setStyleSheet(f"color: {_th.FG2}; font-size: 11px;")
        row.addWidget(lbl_p)

        self.path_filter = QLineEdit()
        self.path_filter.setPlaceholderText(t("so_filter_path"))
        self.path_filter.setStyleSheet(_input_style())
        self.path_filter.textChanged.connect(self._on_path_filter_changed)
        row.addWidget(self.path_filter)
        v.addLayout(row)

        self.path_tree = QTreeWidget()
        self.path_tree.setHeaderLabels([t("so_tab_paths"), t("so_col_kind"), t("so_col_size")])
        self.path_tree.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self.path_tree.header().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.path_tree.header().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.path_tree.setIconSize(QSize(16, 16))
        self.path_tree.setIndentation(18)
        self.path_tree.setStyleSheet(_tree_style())
        self.path_tree.itemDoubleClicked.connect(self._on_tree_item_double_clicked)
        v.addWidget(self.path_tree)
        return w

    def _build_sections_tab(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(10, 10, 10, 10)
        v.setSpacing(6)

        self.section_table = QTableWidget(0, 6)
        self.section_table.setHorizontalHeaderLabels([
            t("so_tab_sections"), t("so_col_kind"), "Addr (VA)", "Offset", t("so_col_size"), "Flags"
        ])
        self.section_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.section_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.section_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.section_table.setStyleSheet(_table_style())
        self.section_table.cellDoubleClicked.connect(self._on_section_double_clicked)
        v.addWidget(self.section_table)
        return w

    def _build_segments_tab(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(10, 10, 10, 10)
        v.setSpacing(6)

        self.segment_table = QTableWidget(0, 5)
        self.segment_table.setHorizontalHeaderLabels([
            t("so_tab_segments"), "Addr (VA)", "Offset", t("so_col_size"), "Flags (R/W/X)"
        ])
        self.segment_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.segment_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.segment_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.segment_table.setStyleSheet(_table_style())
        v.addWidget(self.segment_table)
        return w

    def _build_relocations_tab(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(10, 10, 10, 10)
        v.setSpacing(6)

        row = QHBoxLayout()
        lbl_r = QLabel(t("so_search"))
        lbl_r.setStyleSheet(f"color: {_th.FG2}; font-size: 11px;")
        row.addWidget(lbl_r)

        self.reloc_filter = QLineEdit()
        self.reloc_filter.setPlaceholderText(t("so_filter_symbols"))
        self.reloc_filter.setStyleSheet(_input_style())
        self.reloc_filter.textChanged.connect(self._refresh_relocation_table)
        row.addWidget(self.reloc_filter)
        v.addLayout(row)

        self.reloc_table = QTableWidget(0, 4)
        self.reloc_table.setHorizontalHeaderLabels(["Offset", t("so_col_name"), "Section", t("so_col_kind")])
        self.reloc_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.reloc_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.reloc_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.reloc_table.setStyleSheet(_table_style())
        v.addWidget(self.reloc_table)
        return w

    def _build_security_tab(self):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("background: transparent; border: none;")

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        card = QFrame()
        card.setStyleSheet(_card_style(radius=6))
        card_lay = QVBoxLayout(card)
        card_lay.setContentsMargins(14, 12, 14, 12)
        card_lay.setSpacing(8)

        title = QLabel(t("so_tab_security"))
        title.setStyleSheet(f"color: {_th.FG_BRIGHT}; font-size: 12px; font-weight: 600; border: none; background: transparent;")
        card_lay.addWidget(title)

        self.sec_rows = {}
        for key, label in [
            ("stripped", "Symbol Table Stripped"),
            ("nx_stack", "NX Stack (Non-Executable Stack)"),
            ("relro", "RELRO (Relocation Read-Only)"),
            ("stack_canary", "Stack Canary (__stack_chk_fail)"),
            ("fortify_source", "FORTIFY_SOURCE (_chk functions)"),
            ("rpath_or_runpath", "RPATH / RUNPATH"),
        ]:
            row = QHBoxLayout()
            row.setSpacing(8)
            lbl = QLabel(label + ":")
            lbl.setStyleSheet(f"color: {_th.FG2}; font-size: 11px; border: none; background: transparent;")
            val_lbl = QLabel("-")
            val_lbl.setStyleSheet(f"color: {_th.FG_BRIGHT}; font-size: 11px; font-weight: 500; border: none; background: transparent;")
            row.addWidget(lbl)
            row.addStretch()
            row.addWidget(val_lbl)
            card_lay.addLayout(row)
            self.sec_rows[key] = val_lbl

        self.sec_note = QLabel("")
        self.sec_note.setStyleSheet(f"color: {_th.FG_MUTED}; font-size: 11px; margin-top: 4px; border: none; background: transparent;")
        self.sec_note.setWordWrap(True)
        card_lay.addWidget(self.sec_note)

        layout.addWidget(card)
        layout.addStretch()

        scroll.setWidget(container)
        return scroll

    def _build_disasm_tab(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(10, 10, 10, 10)
        v.setSpacing(6)

        row = QHBoxLayout()
        row.setSpacing(8)

        row.addWidget(QLabel("Section:"))
        self.disasm_section_combo = QComboBox()
        self.disasm_section_combo.setStyleSheet(_combo_style())
        row.addWidget(self.disasm_section_combo)

        row.addWidget(QLabel(t("so_col_size") + ":"))
        self.disasm_max_bytes = QSpinBox()
        self.disasm_max_bytes.setRange(16, 65536)
        self.disasm_max_bytes.setValue(512)
        self.disasm_max_bytes.setStyleSheet(_spin_style())
        row.addWidget(self.disasm_max_bytes)

        run_btn = QPushButton(t("so_btn_disasm_action"))
        run_btn.setCursor(Qt.PointingHandCursor)
        run_btn.setStyleSheet(_btn_primary())
        run_btn.clicked.connect(self._run_disassemble)
        row.addWidget(run_btn)

        row.addStretch()
        v.addLayout(row)

        self.disasm_output = QPlainTextEdit()
        self.disasm_output.setReadOnly(True)
        self.disasm_output.setFont(MONO_FONT)
        self.disasm_output.setStyleSheet(_text_view_style())
        v.addWidget(self.disasm_output)
        return w

    def _build_strings_tab(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(10, 10, 10, 10)
        v.setSpacing(6)

        row = QHBoxLayout()
        lbl_s = QLabel(t("so_search"))
        lbl_s.setStyleSheet(f"color: {_th.FG2}; font-size: 11px;")
        row.addWidget(lbl_s)

        self.string_filter = QLineEdit()
        self.string_filter.setPlaceholderText(t("so_tab_strings") + "...")
        self.string_filter.setStyleSheet(_input_style())
        self.string_filter.textChanged.connect(self._refresh_string_list)
        row.addWidget(self.string_filter)
        v.addLayout(row)

        self.string_list = QPlainTextEdit()
        self.string_list.setReadOnly(True)
        self.string_list.setFont(MONO_FONT)
        self.string_list.setStyleSheet(_text_view_style())
        v.addWidget(self.string_list)
        return w


    def _load_analysis(self):
        if not os.path.isfile(self.filepath):
            self.status_lbl.setText(f"{os.path.basename(self.filepath)} (Not found)")
            return

        self.mark_task_started()
        self.status_lbl.setText(t("so_loading"))

        def _worker():
            try:
                res = so_core.analyze_file(self.filepath)
                return True, res
            except Exception as e:
                return False, str(e)

        def _after(ok, data):
            if not ok:
                self.status_lbl.setText(f"Error: {data}")
                _AppDialog.error(self, t("lbl_error"), t("so_parse_fail", err=str(data)))
                return
            self.result = data
            self._populate_all()

        run_in_thread(_worker, None, callback=self.guard_task_callback(_after))

    def _populate_all(self):
        if not self.result:
            return

        h = self.result["header"]
        sec = self.result["security"]

        # Build clean, minimal bullet-separated metadata line
        sec_parts = []
        if sec.get("nx_stack"):
            sec_parts.append("NX")
        if sec.get("stack_canary"):
            sec_parts.append("Canary")
        if sec.get("relro") and str(sec.get("relro")).lower() not in ("none", "tidak ada"):
            sec_parts.append(f"RELRO: {sec['relro']}")

        sec_summary = " • ".join(sec_parts) if sec_parts else "No Mitigations"
        status_line = (
            f"{h['abi']} ({h['class']}-bit, {h['endianness']} endian)  •  "
            f"{so_core.human_size(h['size'])}  •  "
            f"{sec_summary}"
        )
        self.status_lbl.setText(status_line)

        self._refresh_overview()
        self._refresh_symbol_table()
        self._refresh_path_tree()
        self._refresh_section_table()
        self._refresh_segment_table()
        self._refresh_relocation_table()
        self._refresh_security_tab()
        self._refresh_disasm_sections()
        self._refresh_string_list()


    def _refresh_overview(self):
        if not self.result:
            return
        r = self.result
        h = r["header"]
        dyn = r["dynamic"]
        dw = r["dwarf"]
        ap = r["appended_data"]

        lines = [
            f"{t('so_overview_file_path')}   : {r['filepath']}",
            f"{t('so_overview_file_size')}   : {so_core.human_size(h['size'])} ({h['size']:,} bytes)",
            f"{t('so_overview_arch')}        : {h['abi']} ({h['class']}-bit, {h['endianness']} endian)",
            f"{t('so_overview_file_type')}   : {h['file_type']}",
            f"{t('so_overview_entry_point')} : {h['entry_point']}",
            f"{t('so_overview_sections')}    : {h['num_sections']} {t('so_overview_sections').lower()}",
            f"{t('so_overview_segments')}    : {h['num_segments']} {t('so_overview_segments').lower()}",
            "",
            f"{t('so_overview_soname')}      : {dyn['soname'] or t('so_none')}",
            f"{t('so_overview_dependencies')}: {', '.join(dyn['needed_libraries']) or t('so_none')}",
            "",
            f"{t('so_overview_dwarf')} : {t('so_yes') if dw['has_debug_info'] else t('so_no')}",
        ]
        if dw["source_files"]:
            lines.append(f"{t('so_overview_source_files')}:")
            for f in dw["source_files"][:25]:
                lines.append(f"  • {f}")
            if len(dw["source_files"]) > 25:
                lines.append("  " + t("so_and_more_files", count=len(dw["source_files"]) - 25))

        lines.append("")
        lines.append(f"{t('so_overview_total_strings')} : {r['total_strings']}")
        lines.append(f"{t('so_overview_path_strings')}  : {len(r['path_like_strings'])}")
        lines.append(f"{t('so_overview_embedded')}      : {len(r['embedded_files'])}")
        for ef in r["embedded_files"]:
            lines.append(f"  • {ef.label}  [{ef.file_type}, {so_core.human_size(len(ef.data))}]")

        if ap["trailing_bytes"] > 0:
            lines.append(f"\n{t('so_overview_trailing_warn')} ({so_core.human_size(ap['trailing_bytes'])}).")

        self.overview_text.setPlainText("\n".join(lines))

        self.overview_text.setPlainText("\n".join(lines))

    def _refresh_symbol_table(self):
        if not self.result:
            return
        show_mangled = self.mangled_checkbox.isChecked()
        self.symbol_table.setColumnHidden(2, not show_mangled)

        query = self.symbol_filter.text().lower()
        kind_filter = self.symbol_kind_combo.currentText()
        all_text = t("so_kind_all")

        rows = []
        for s in self.result["symbols"]["exported"]:
            rows.append(("export", s))
        for s in self.result["symbols"]["imported"]:
            rows.append(("import", s))
        for s in self.result["symbols"]["local"]:
            rows.append(("local", s))

        if kind_filter != all_text and kind_filter != "Semua":
            rows = [(k, s) for k, s in rows if k == kind_filter]
        if query:
            rows = [(k, s) for k, s in rows if query in s["demangled"].lower() or query in s["name"].lower()]

        self.symbol_count_lbl.setText(f"({len(rows)}/{self.result['symbols']['total']})")

        self.symbol_table.setRowCount(len(rows))
        for i, (kind, s) in enumerate(rows):
            kind_item = QTableWidgetItem(kind)
            # Use clean palette values: bright for export, secondary for import, muted for local
            if kind == "export":
                kind_item.setForeground(_qcolor(_th.FG_BRIGHT))
            elif kind == "import":
                kind_item.setForeground(_qcolor(_th.FG2))
            else:
                kind_item.setForeground(_qcolor(_th.FG_MUTED))

            self.symbol_table.setItem(i, 0, kind_item)
            self.symbol_table.setItem(i, 1, QTableWidgetItem(s["demangled"]))
            self.symbol_table.setItem(i, 2, QTableWidgetItem(s["name"]))
            self.symbol_table.setItem(i, 3, QTableWidgetItem(s["value"]))
            self.symbol_table.setItem(i, 4, QTableWidgetItem(str(s["size"])))

    def _refresh_path_tree(self):
        self.path_tree.clear()
        if not self.result:
            return
        root_node = self.result["path_tree"]
        self._add_tree_children(self.path_tree.invisibleRootItem(), root_node)
        self.path_tree.expandAll()

    def _on_path_filter_changed(self, text: str):
        query = text.lower()
        def _filter_item(item):
            match = False
            for col in range(item.columnCount()):
                if query in item.text(col).lower():
                    match = True
                    break
            child_matched = False
            for i in range(item.childCount()):
                if _filter_item(item.child(i)):
                    child_matched = True
            visible = match or child_matched or not query
            item.setHidden(not visible)
            if child_matched and query:
                item.setExpanded(True)
            return visible

        for i in range(self.path_tree.topLevelItemCount()):
            _filter_item(self.path_tree.topLevelItem(i))

    def jump_to_raw_strings(self, search_text: str):
        for i in range(self.tabs.count()):
            if self.tabs.tabText(i) == t("so_tab_strings"):
                self.tabs.setCurrentIndex(i)
                break
        self.string_filter.setText(search_text)

    def _add_tree_children(self, parent_item, node: "so_core.PathNode", current_dir: str = ""):
        resolver = _FileTreeIconResolver

        for name, child in sorted(node.children.items()):
            folder_item = QTreeWidgetItem(parent_item, [name, "Folder", ""])
            folder_item.setIcon(0, resolver.resolve_icon(name, is_dir=True))
            folder_item.setForeground(0, _qcolor(_th.FG_LABEL))
            folder_item.setForeground(1, _qcolor(_th.FG_MUTED))
            next_dir = "" if name == "[embedded_in_binary]" else (f"{current_dir}/{name}" if current_dir else name)
            self._add_tree_children(folder_item, child, next_dir)

        for fname in sorted(node.files):
            leaf = QTreeWidgetItem(parent_item, [fname, "String", ""])
            leaf.setIcon(0, resolver.resolve_icon(fname, is_dir=False))
            full_path = f"{current_dir}/{fname}" if current_dir else fname
            leaf.setData(0, Qt.UserRole + 1, full_path)
            leaf.setToolTip(0, f"{full_path}\n({t('so_path_str_title')})")
            leaf.setForeground(0, _qcolor(_th.FG))
            leaf.setForeground(1, _qcolor(_th.FG_MUTED))

        for ef in node.embedded:
            leaf = QTreeWidgetItem(parent_item, [ef.label, ef.file_type, so_core.human_size(len(ef.data))])
            leaf.setIcon(0, resolver.resolve_icon(ef.label, is_embedded=True, file_type=ef.file_type))
            leaf.setData(0, Qt.UserRole, ef)
            leaf.setToolTip(0, f"{ef.label} ({ef.file_type}, {so_core.human_size(len(ef.data))})")
            leaf.setForeground(0, _qcolor(_th.FG_BRIGHT))
            font = leaf.font(0)
            font.setBold(True)
            leaf.setFont(0, font)
            leaf.setForeground(1, _qcolor(_th.FG2))
            leaf.setForeground(2, _qcolor(_th.FG2))

    def _on_tree_item_double_clicked(self, item: QTreeWidgetItem, _col):
        ef = item.data(0, Qt.UserRole)
        if ef is not None:
            dlg = EmbeddedFileDialog(ef, self)
            dlg.exec()
            return

        full_path = item.data(0, Qt.UserRole + 1)
        if full_path:
            dlg = PathStringDialog(full_path, self)
            dlg.exec()
            return

    def _refresh_section_table(self):
        self.section_table.setRowCount(0)
        if not self.result:
            return
        sections = self.result["sections"]
        self.section_table.setRowCount(len(sections))
        for i, sec in enumerate(sections):
            flags_int = sec["flags"]
            flag_str = "".join([
                "W" if flags_int & 0x1 else "-",
                "A" if flags_int & 0x2 else "-",
                "X" if flags_int & 0x4 else "-",
            ])
            self.section_table.setItem(i, 0, QTableWidgetItem(sec["name"] or "(no name)"))
            self.section_table.setItem(i, 1, QTableWidgetItem(sec["type"]))
            self.section_table.setItem(i, 2, QTableWidgetItem(sec["addr"]))
            self.section_table.setItem(i, 3, QTableWidgetItem(sec["offset"]))
            self.section_table.setItem(i, 4, QTableWidgetItem(so_core.human_size(sec["size"])))
            self.section_table.setItem(i, 5, QTableWidgetItem(flag_str))

    def _on_section_double_clicked(self, row, _col):
        if not self.result:
            return
        name = self.section_table.item(row, 0).text()
        if name == "(no name)":
            return
        data = so_core.get_section_bytes(self.filepath, name)
        if data is None:
            QMessageBox.information(self, "Info", "Section ini tidak memiliki data di file (SHT_NOBITS).")
            return
        ef = so_core.EmbeddedFile(
            label=f"Section {name}", kind="section", file_type=f"Raw: {name}",
            offset=0, data=data, exact_length=True
        )
        dlg = EmbeddedFileDialog(ef, self)
        dlg.exec()

    def _refresh_segment_table(self):
        self.segment_table.setRowCount(0)
        if not self.result:
            return
        segments = self.result["segments"]
        self.segment_table.setRowCount(len(segments))
        for i, seg in enumerate(segments):
            flags_int = seg["flags"]
            flag_str = "".join([
                "R" if flags_int & 0x4 else "-",
                "W" if flags_int & 0x2 else "-",
                "X" if flags_int & 0x1 else "-",
            ])
            self.segment_table.setItem(i, 0, QTableWidgetItem(seg["type"]))
            self.segment_table.setItem(i, 1, QTableWidgetItem(seg["vaddr"]))
            self.segment_table.setItem(i, 2, QTableWidgetItem(seg["offset"]))
            self.segment_table.setItem(i, 3, QTableWidgetItem(so_core.human_size(seg["filesz"])))
            self.segment_table.setItem(i, 4, QTableWidgetItem(flag_str))

    def _refresh_relocation_table(self):
        self.reloc_table.setRowCount(0)
        if not self.result:
            return
        query = self.reloc_filter.text().lower()
        rels = self.result["relocations"]
        if query:
            rels = [r for r in rels if query in r["demangled"].lower() or query in r["symbol"].lower()]
        self.reloc_table.setRowCount(len(rels))
        for i, r in enumerate(rels):
            self.reloc_table.setItem(i, 0, QTableWidgetItem(r["offset"]))
            self.reloc_table.setItem(i, 1, QTableWidgetItem(r["demangled"] or r["symbol"]))
            self.reloc_table.setItem(i, 2, QTableWidgetItem(r["reloc_section"]))
            self.reloc_table.setItem(i, 3, QTableWidgetItem(str(r["type"])))

    def _refresh_security_tab(self):
        if not self.result:
            return
        sec = self.result["security"]
        for key, lbl in self.sec_rows.items():
            val = sec.get(key)
            if key == "relro":
                text = str(val).upper()
            elif isinstance(val, bool):
                text = "Yes" if val else "No"
            else:
                text = str(val)

            lbl.setText(text)

        self.sec_note.setText(sec.get("note_pie", ""))

    def _refresh_disasm_sections(self):
        self.disasm_section_combo.clear()
        if not self.result:
            return
        self.disasm_section_combo.addItems(self.result["exec_sections"])
        self.disasm_output.clear()

    def _run_disassemble(self):
        if not self.result:
            return
        section = self.disasm_section_combo.currentText()
        if not section:
            QMessageBox.information(self, "Info", t("so_no_exec_sec"))
            return
        max_bytes = self.disasm_max_bytes.value()

        from elftools.elf.elffile import ELFFile
        try:
            with open(self.filepath, "rb") as f:
                elf = ELFFile(f)
                result = so_core.disassemble_section(elf, section, max_bytes)
        except Exception as e:
            self.disasm_output.setPlainText(t("so_error_disasm", err=str(e)))
            return

        if "error" in result:
            self.disasm_output.setPlainText(t("so_error_disasm", err=str(result['error'])))
            return

        lines = [f"{i['addr']:#010x}:\t{i['mnemonic']:<10}\t{i['op_str']}" for i in result["instructions"]]
        footer = "\n" + t("so_section_bytes_shown", instr=len(lines), bytes=max_bytes, sec=section)
        lines.append(footer)
        self.disasm_output.setPlainText("\n".join(lines))

    def _refresh_string_list(self):
        if not self.result:
            return
        query = self.string_filter.text().lower()
        all_strings = self.result["all_strings"]
        path_like_set = set(self.result["path_like_strings"])
        if query:
            all_strings = [s for s in all_strings if query in s.lower()]

        limit = 3000
        shown = all_strings[:limit]
        lines = [(f"[path?] {s}" if s in path_like_set else s) for s in shown]
        header = t("so_total_strings_count", total=len(self.result['all_strings']))
        if query:
            header += t("so_matching_filter", count=len(all_strings))
        if len(all_strings) > limit:
            header += t("so_first_limit", limit=limit)
        self.string_list.setPlainText(header + "\n\n" + "\n".join(lines))


    def export_json(self):
        if not self.result:
            QMessageBox.information(self, "Info", t("so_not_ready"))
            return
        default_name = os.path.splitext(os.path.basename(self.filepath))[0] + "_analysis.json"
        path, _ = QFileDialog.getSaveFileName(self, t("so_btn_export"), default_name, "JSON (*.json)")
        if not path:
            return
        data = so_core.result_to_jsonable(self.result)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, default=str)
        QMessageBox.information(self, "OK", t("so_saved_ok", path=path))

    def compare_with_file(self):
        if not self.result:
            return
        other_path, _ = QFileDialog.getOpenFileName(
            self, t("so_btn_compare"), os.path.dirname(self.filepath), "Shared Objects (*.so *.so.*);;All Files (*)"
        )
        if not other_path or not os.path.isfile(other_path):
            return

        try:
            other_result = so_core.analyze_file(other_path)
            diff = so_core.compare_symbols(self.result, other_result)
            dlg = CompareSymbolsDialog(diff, self)
            dlg.exec()
        except Exception as e:
            _AppDialog.error(self, t("lbl_error"), t("so_parse_fail", err=str(e)))
