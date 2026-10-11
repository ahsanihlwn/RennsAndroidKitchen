# SPDX-License-Identifier: MPL-2.0
import os
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QWidget, QScrollArea, QSizePolicy, QFrame
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui  import QIcon

from icons.handler import icon
from main_ext.class_ThemeUi import theme as _th

print("[MAIN STAGE] FS_PICKER_DIALOG")


def _card_style():
    _hex = _th.BG2.lstrip("#")
    _r, _g, _b = int(_hex[0:2], 16), int(_hex[2:4], 16), int(_hex[4:6], 16)
    return f"""
        QWidget#dlg_card {{
            background: {_th.panel_rgba(_r, _g, _b, 128)};
            border: 1px solid {_th.border_color(0.22)};
            border-radius: 14px;
        }}
    """

def _scroll_style():
    return f"""
        QScrollArea,
        QScrollArea > QWidget,
        QScrollArea > QWidget > QWidget {{
            background: transparent;
            border: none;
            outline: none;
        }}
        QScrollArea QScrollBar:vertical {{
            background: transparent;
            width: 6px; margin: 0; border: none;
        }}
        QScrollArea QScrollBar::handle:vertical {{
            background: {_th.border_color(0.20)};
            border-radius: 3px;
            min-height: 20px;
        }}
        QScrollArea QScrollBar::handle:vertical:hover {{
            background: {_th.border_color(0.35)};
        }}
        QScrollArea QScrollBar::add-line:vertical,
        QScrollArea QScrollBar::sub-line:vertical {{
            height: 0; border: none; background: transparent;
        }}
        QScrollArea QScrollBar::add-page:vertical,
        QScrollArea QScrollBar::sub-page:vertical {{
            background: transparent;
        }}
    """

def _label_style():
    return f"color:{_th.FG_BLUE_MUTED}; font-size:11px; background:transparent; border:none;"

def _header_style():
    return f"color:{_th.FG}; font-size:13px; font-weight:600; background:transparent; border:none;"

def _sep_style():
    return f"background:{_th.border_color(0.06)}; border:none;"

def _btn_style(variant="neutral"):
    if variant == "primary":
        p = dict(
            bg=_th.accent(0.15), br=_th.accent(0.30), fg=_th.FG_BLUE, w=600,
            bg_h=_th.accent(0.27), br_h=_th.accent(0.52), fg_h=_th.FG_BLUE_LIGHT,
            bg_p=_th.accent(0.40))
    else:
        p = dict(
            bg=_th.border_color(0.05), br=_th.border_color(0.08), fg=_th.FG_MUTED, w=400,
            bg_h=_th.border_color(0.09), br_h=_th.border_color(0.14), fg_h=_th.FG_BLUE_MUTED,
            bg_p=_th.border_color(0.13))
    return f"""
        QPushButton {{
            background:{p['bg']}; border:1px solid {p['br']}; border-radius:7px;
            color:{p['fg']}; font-size:12px; font-weight:{p['w']};
            padding:0 16px; height:30px;
        }}
        QPushButton:hover   {{ background:{p['bg_h']}; border-color:{p['br_h']}; color:{p['fg_h']}; }}
        QPushButton:pressed {{ background:{p['bg_p']}; }}
        QPushButton:disabled {{ background:{_th.border_color(0.03)}; border-color:{_th.border_color(0.04)};
                                color:{_th.FG_DISABLED2}; }}
    """

def _tab_active():
    return f"""QPushButton {{
        background:{_th.accent(0.18)}; border:1px solid {_th.accent(0.40)};
        border-radius:7px; color:{_th.FG_BLUE}; font-size:12px; font-weight:600;
        padding:0 14px; height:28px; min-width:64px;
    }}"""

def _tab_inactive():
    return f"""QPushButton {{
        background:transparent; border:1px solid transparent;
        border-radius:7px; color:{_th.FG_DESC}; font-size:12px; font-weight:400;
        padding:0 14px; height:28px; min-width:64px;
    }}
    QPushButton:hover {{ background:{_th.border_color(0.05)}; color:{_th.FG_BLUE_MUTED}; }}"""


class _PartitionCard(QWidget):


    selected = Signal(str)

    # State styles are computed per-instance (theme-dependent) → use methods
    @staticmethod
    def _normal_ss():
        return f"background:{_th.border_color(0.03)}; border:1px solid {_th.border_color(0.06)}; border-radius:8px;"

    @staticmethod
    def _selected_ss():
        return f"background:{_th.accent(0.15)}; border:1px solid {_th.accent(0.40)}; border-radius:8px;"

    @staticmethod
    def _hover_ss():
        return f"background:{_th.border_color(0.07)}; border:1px solid {_th.border_color(0.10)}; border-radius:8px;"

    # Type-specific badges — semantic colors kept as fixed for clarity
    _BADGE = {
        "EROFS":  ("#3a5f8a", "#7eb8f7"),
        "EXT4":   ("#3a6b4a", "#7fcf9a"),
        "EXT4L":  ("#2e5c3e", "#6abf85"),
        "EXT3":   ("#5a5330", "#c8b85a"),
        "EXT2":   ("#4a4030", "#b09060"),
        "SUPER":  ("#5a3a70", "#b07ae0"),
        "SPARSE": ("#4a3020", "#c07040"),
    }

    def __init__(self, fs_name: str, fs_type: str, folder_icon: QIcon, parent=None):
        super().__init__(parent)
        self._fs_name   = fs_name
        self._checked   = False
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(48)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setStyleSheet(self._normal_ss())

        row = QHBoxLayout(self)
        row.setContentsMargins(12, 0, 12, 0)
        row.setSpacing(10)

        # folder icon
        ic_lbl = QLabel()
        ic_lbl.setFixedSize(20, 20)
        ic_lbl.setScaledContents(True)
        if not folder_icon.isNull():
            ic_lbl.setPixmap(folder_icon.pixmap(20, 20))
        ic_lbl.setAttribute(Qt.WA_TransparentForMouseEvents)
        row.addWidget(ic_lbl)

        # name
        name_lbl = QLabel(fs_name)
        name_lbl.setStyleSheet(f"color:{_th.FG}; font-size:13px; background:transparent; border:none;")
        name_lbl.setAttribute(Qt.WA_TransparentForMouseEvents)
        row.addWidget(name_lbl, 1)

        # badge (semantic color per fs type)
        if fs_type:
            bg, fg = self._BADGE.get(fs_type.upper(), (_th.FG_DISABLED2, _th.FG_BLUE_MUTED))
            badge = QLabel(fs_type)
            badge.setStyleSheet(
                f"background:{bg}; color:{fg}; font-size:10px; font-weight:600;"
                f" border-radius:4px; padding:2px 6px; border:none;"
            )
            badge.setFixedHeight(18)
            badge.setAttribute(Qt.WA_TransparentForMouseEvents)
            row.addWidget(badge)

    def set_checked(self, val: bool):
        self._checked = val
        self.setStyleSheet(self._selected_ss() if val else self._normal_ss())

    def is_checked(self) -> bool:
        return self._checked

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.selected.emit(self._fs_name)
        super().mousePressEvent(e)

    def enterEvent(self, e):
        if not self._checked:
            self.setStyleSheet(self._hover_ss())
        super().enterEvent(e)

    def leaveEvent(self, e):
        self.setStyleSheet(self._selected_ss() if self._checked else self._normal_ss())
        super().leaveEvent(e)


class FsPickerDialog(QDialog):
    def __init__(self, parent, main_win, callback):
        super().__init__(parent)
        self._main        = main_win
        self._cb          = callback
        self._sel_tree    = None
        self._sel_fs      = None
        self._cards       = []
        self._active_card = None

        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setModal(True)
        self.setFixedWidth(400)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)

        card = QWidget()
        card.setObjectName("dlg_card")
        card.setStyleSheet(_card_style())
        outer.addWidget(card)

        self._root_lay = QVBoxLayout(card)
        self._root_lay.setContentsMargins(20, 18, 20, 18)
        self._root_lay.setSpacing(12)

        # ── title ──
        title = QLabel("Select Partition")
        title.setStyleSheet(_header_style())
        self._root_lay.addWidget(title)

        # ── tree tab switcher ──
        self._tab_row = QWidget()
        self._tab_row.setStyleSheet("background:transparent; border:none;")
        tab_lay = QHBoxLayout(self._tab_row)
        tab_lay.setContentsMargins(0, 0, 0, 0)
        tab_lay.setSpacing(6)

        self._tab1 = QPushButton("Tree 1")
        self._tab2 = QPushButton("Tree 2")
        for tb in (self._tab1, self._tab2):
            tb.setCursor(Qt.PointingHandCursor)
            tb.setCheckable(True)
            tb.setAutoExclusive(True)
            tb.setStyleSheet(_tab_inactive())

        self._tab1.toggled.connect(lambda c: (
            self._tab1.setStyleSheet(_tab_active() if c else _tab_inactive()),
            c and self._load_tree(self._main.tree)
        ))
        self._tab2.toggled.connect(lambda c: (
            self._tab2.setStyleSheet(_tab_active() if c else _tab_inactive()),
            c and self._load_tree(self._main.tree2)
        ))

        tab_lay.addWidget(self._tab1)
        tab_lay.addWidget(self._tab2)
        tab_lay.addStretch()
        self._root_lay.addWidget(self._tab_row)

        # ── separator ──
        sep = QWidget()
        sep.setFixedHeight(1)
        sep.setStyleSheet(_sep_style())
        self._root_lay.addWidget(sep)

        # ── hint label ──
        self._hint_lbl = QLabel("Choose a filesystem partition:")
        self._hint_lbl.setStyleSheet(_label_style())
        self._root_lay.addWidget(self._hint_lbl)

        # ── scroll area (no border anywhere) ──
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setStyleSheet(_scroll_style())
        self._scroll.setFrameShape(QFrame.NoFrame)
        self._scroll.setFrameShadow(QFrame.Plain)
        self._scroll.setLineWidth(0)
        self._scroll.setMidLineWidth(0)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._scroll.setFixedHeight(220)
        self._scroll.viewport().setStyleSheet("background:transparent; border:none;")

        self._list_widget = QWidget()
        self._list_widget.setStyleSheet("background:transparent; border:none;")
        self._list_lay = QVBoxLayout(self._list_widget)
        self._list_lay.setContentsMargins(0, 2, 0, 2)
        self._list_lay.setSpacing(4)
        self._list_lay.addStretch()

        self._scroll.setWidget(self._list_widget)
        self._root_lay.addWidget(self._scroll)

        # ── separator ──
        sep2 = QWidget()
        sep2.setFixedHeight(1)
        sep2.setStyleSheet(_sep_style())
        self._root_lay.addWidget(sep2)

        # ── footer ──
        foot = QHBoxLayout()
        foot.setSpacing(8)
        foot.addStretch()

        btn_cancel = QPushButton("Cancel")
        btn_cancel.setStyleSheet(_btn_style("neutral"))
        btn_cancel.setFixedHeight(30)
        btn_cancel.setCursor(Qt.PointingHandCursor)
        btn_cancel.clicked.connect(self.reject)

        self._btn_ok = QPushButton("Select")
        self._btn_ok.setStyleSheet(_btn_style("primary"))
        self._btn_ok.setFixedHeight(30)
        self._btn_ok.setEnabled(False)
        self._btn_ok.setCursor(Qt.PointingHandCursor)
        self._btn_ok.clicked.connect(self._confirm)

        foot.addWidget(btn_cancel)
        foot.addWidget(self._btn_ok)
        self._root_lay.addLayout(foot)

        # ── init ──
        self._setup_trees()
        self.adjustSize()

    # setup

    def _setup_trees(self):
        tree1 = self._main.tree
        tree2 = self._main.tree2

        base1 = os.path.normpath(tree1.base) if tree1.base else None
        base2 = os.path.normpath(tree2.base) if tree2.base else None
        has_two = base2 and base1 and base1 != base2

        if has_two:
            self._tab_row.show()
            # block tab2 signal to avoid double _load_tree on init
            self._tab2.blockSignals(True)
            self._tab1.setChecked(True)
            self._tab2.setChecked(False)
            self._tab2.blockSignals(False)
            self._load_tree(tree1)
        else:
            self._tab_row.hide()
            self._load_tree(tree1 if tree1.base else tree2)

    def _load_tree(self, tree):
        self._sel_tree    = tree
        self._sel_fs      = None
        self._active_card = None
        self._btn_ok.setEnabled(False)

        for c in self._cards:
            c.setParent(None)
        self._cards.clear()

        while self._list_lay.count():
            item = self._list_lay.takeAt(0)
            if item.widget():
                item.widget().setParent(None)

        if not tree or not tree.base:
            self._hint_lbl.setText("No project loaded.")
            self._list_lay.addStretch()
            return

        entries = self._collect_partitions(tree.base)

        if not entries:
            lbl = QLabel("No filesystem partitions found.")
            lbl.setStyleSheet(_label_style())
            lbl.setAlignment(Qt.AlignCenter)
            self._list_lay.addWidget(lbl)
            self._list_lay.addStretch()
            return

        self._hint_lbl.setText(
            f"Project: <span style='color:{_th.FG};'>{os.path.basename(tree.base)}</span>"
        )

        for fs_name, fs_type in entries:
            folder_icon = self._get_folder_icon(tree, fs_type)
            c = _PartitionCard(fs_name, fs_type, folder_icon)
            c.selected.connect(self._on_card_selected)
            self._list_lay.addWidget(c)
            self._cards.append(c)

        self._list_lay.addStretch()

    # data helpers

    def _collect_partitions(self, base):
        from core.fsdetect import detect_fs_root
        from core.rak_neobranch_view import ActiveProjectView
        entries = []
        try:
            view = ActiveProjectView(base)
            for entry in view.list_dir(base):
                name = entry["name"]
                if entry["kind"] != "dir":
                    continue
                if name in ("config", "neocat_config", "automation", ".git"):
                    continue
                fs_type = ""
                try:
                    fs_type = detect_fs_root(base, name) or ""
                except Exception:
                    pass
                entries.append((name, fs_type))
        except Exception:
            pass
        return entries

    def _get_folder_icon(self, tree, fs_type: str) -> QIcon:
        if fs_type:
            return getattr(tree, "icon_fs_folder", QIcon(icon("fs_folder")))
        return getattr(tree, "icon_folder", QIcon(icon("folder")))

    # interaction

    def _on_card_selected(self, fs_name: str):
        # deselect previous
        if self._active_card:
            self._active_card.set_checked(False)
        # select new
        for c in self._cards:
            if c._fs_name == fs_name:
                c.set_checked(True)
                self._active_card = c
                break
        self._sel_fs = fs_name
        self._btn_ok.setEnabled(True)

    def _confirm(self):
        if not self._sel_fs or not self._sel_tree:
            return
        project_base = self._sel_tree.base
        self.accept()
        self._cb(project_base, self._sel_tree, self._sel_fs)

    # show centered (fixed)

    def showEvent(self, e):
        super().showEvent(e)
        p = self.parent()
        if p:
            try:
                g = p.geometry()
                self.move(
                    g.x() + (g.width() - self.width()) // 2,
                    g.y() + (g.height() - self.height()) // 2,
                )
            except Exception:
                pass

    # static factory

    @staticmethod
    def pick(parent, main_win, callback, with_blur=True, overlay=None):

        from app_ext.class_BlurOverlay import BlurOverlay as _BlurOverlay, install_window_fade
        dlg = FsPickerDialog(parent, main_win, callback)
        owned_ov = _BlurOverlay(parent) if (parent and with_blur and overlay is None) else None
        _ov = overlay or owned_ov
        if _ov:
            dlg._overlay = _ov
        install_window_fade(dlg)
        dlg.exec()
        if owned_ov:
            owned_ov.close_overlay()
