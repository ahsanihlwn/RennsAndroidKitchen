# SPDX-License-Identifier: MPL-2.0
import os
from PySide6.QtWidgets import (
    QDialog, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QLineEdit, QStackedWidget, QScrollArea, QCheckBox
)
from PySide6.QtCore import Qt, QPoint
from PySide6.QtGui  import QColor

from main_ext.class_ThemeUi  import theme as _th
from app_ext.class_BlurOverlay import BlurCard, BlurOverlay
from core.ui_lang            import t
from core.make_symlink       import (
    make_symlink, ANDROID_ROOT_SYMLINKS, TOYBOX_SYMLINKS
)


# shared styles
def _input_ss():
    return f"""
        QLineEdit {{
            background: {_th.border_color(0.06)};
            border: 1px solid {_th.border_color(0.12)};
            border-radius: 6px;
            color: {_th.FG};
            font-size: 12px;
            padding: 0 10px;
            selection-background-color: {_th.accent(0.35)};
        }}
        QLineEdit:focus {{
            border-color: {_th.accent(0.55)};
            background: {_th.border_color(0.10)};
        }}
        QLineEdit::placeholder {{ color: {_th.FG_MUTED}; }}
    """


def _btn_ss(variant="default"):
    if variant == "primary":
        return f"""
            QPushButton {{
                background: {_th.accent(0.20)};
                border: 1px solid {_th.accent(0.40)};
                border-radius: 6px;
                color: {_th.FG_BLUE};
                font-size: 12px;
                font-weight: 600;
                padding: 0 14px;
                height: 30px;
            }}
            QPushButton:hover {{
                background: {_th.accent(0.32)};
                border-color: {_th.accent(0.60)};
                color: {_th.FG_BLUE_LIGHT};
            }}
            QPushButton:pressed {{ background: {_th.accent(0.48)}; }}
            QPushButton:disabled {{ color: {_th.FG_MUTED}; background: transparent; border-color: {_th.border_color(0.10)}; }}
        """
    return f"""
        QPushButton {{
            background: {_th.border_color(0.06)};
            border: 1px solid {_th.border_color(0.10)};
            border-radius: 6px;
            color: {_th.FG3};
            font-size: 12px;
            padding: 0 14px;
            height: 30px;
        }}
        QPushButton:hover {{
            background: {_th.border_color(0.10)};
            border-color: {_th.border_color(0.18)};
            color: {_th.FG_HOVER};
        }}
        QPushButton:pressed {{ background: {_th.border_color(0.14)}; }}
    """


def _tab_btn_ss(active: bool):
    fg = _th.FG_BLUE if active else _th.FG_MUTED
    bg = _th.accent(0.14) if active else "transparent"
    return f"""
        QPushButton {{
            background: {bg};
            border: none;
            border-radius: 6px;
            color: {fg};
            font-size: 12px;
            font-weight: {"600" if active else "500"};
            padding: 0 14px;
            height: 28px;
        }}
        QPushButton:hover {{ background: {_th.accent(0.10)}; color: {_th.FG}; }}
    """


def _checkbox_ss():
    from icons.handler import icon as _icon
    _check = _icon("check_white").replace("\\", "/")
    return f"""
        QCheckBox {{
            color: {_th.FG};
            font-size: 12px;
            spacing: 8px;
            padding: 4px 0;
        }}
        QCheckBox::indicator {{
            width: 14px; height: 14px;
            border: 1px solid {_th.border_color(0.20)};
            border-radius: 7px;
            background: {_th.border_color(0.06)};
        }}
        QCheckBox::indicator:hover {{ border-color: {_th.accent(0.50)}; }}
        QCheckBox::indicator:checked {{
            background: {_th.accent(0.70)};
            border-color: {_th.accent(0.85)};
            image: url({_check});
        }}
    """


# preset row widget (checkbox + label + target hint)
class _PresetRow(QWidget):
    def __init__(self, name, target, parent=None):
        super().__init__(parent)
        self.name   = name
        self.target = target

        hl = QHBoxLayout(self)
        hl.setContentsMargins(8, 0, 8, 0)
        hl.setSpacing(8)

        self.cb = QCheckBox(name)
        self.cb.setStyleSheet(_checkbox_ss())
        hl.addWidget(self.cb)

        hl.addStretch()

        arrow = QLabel("→")
        arrow.setStyleSheet(f"color:{_th.FG_MUTED}; font-size:11px; background:transparent;")
        hl.addWidget(arrow)

        tgt = QLabel(target)
        tgt.setStyleSheet(f"color:{_th.FG_BLUE_MUTED}; font-size:11px; background:transparent;")
        hl.addWidget(tgt)

    def is_checked(self):
        return self.cb.isChecked()

    def set_checked(self, v):
        self.cb.setChecked(v)


# dialog
class SymlinkDialog(QDialog):
    def __init__(self, parent, target_dir):
        super().__init__(parent)
        self.target_dir = target_dir
        self.created_count = 0

        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setModal(True)

        # BLUR OVERLAY (full bg blur dimming)
        self._overlay = BlurOverlay(parent) if parent else None
        if self._overlay:
            self._overlay.show()

        self._build_ui()


    def _build_ui(self):
        self.setFixedSize(480, 420)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)

        _br, _bg, _bb = _th._palette["_border_rgb"]
        tint = QColor(_th.BG2); tint.setAlpha(170)
        self._card = BlurCard(
            radius=20.0,
            tint=tint,
            border=QColor(_br, _bg, _bb, int(0.22 * 255)),
        )
        outer.addWidget(self._card)

        root = QVBoxLayout(self._card)
        root.setContentsMargins(20, 16, 20, 16)
        root.setSpacing(10)

        # ── header ──
        title = QLabel(t("sym_title"))
        title.setStyleSheet(
            f"color:{_th.FG_BRIGHT}; font-size:14px; font-weight:600; background:transparent;"
        )
        root.addWidget(title)

        # path display
        path_row = QHBoxLayout()
        path_row.setSpacing(6)
        in_lbl = QLabel(t("sym_in"))
        in_lbl.setStyleSheet(f"color:{_th.FG_MUTED}; font-size:11px; background:transparent;")
        path_row.addWidget(in_lbl)
        path_val = QLabel(self.target_dir)
        path_val.setStyleSheet(
            f"color:{_th.FG_BLUE_MUTED}; font-size:11px; background:transparent;"
        )
        path_val.setWordWrap(False)
        path_val.setTextInteractionFlags(Qt.TextSelectableByMouse)
        path_row.addWidget(path_val, 1)
        root.addLayout(path_row)

        # ── tab bar ──
        tab_row = QHBoxLayout()
        tab_row.setSpacing(2)
        self._btn_custom  = QPushButton(t("sym_tab_custom"))
        self._btn_android = QPushButton(t("sym_tab_android"))
        self._btn_toybox  = QPushButton(t("sym_tab_toybox"))
        for b in (self._btn_custom, self._btn_android, self._btn_toybox):
            b.setCursor(Qt.PointingHandCursor)
            tab_row.addWidget(b)
        tab_row.addStretch()
        root.addLayout(tab_row)

        self._btn_custom.clicked.connect(lambda: self._set_tab(0))
        self._btn_android.clicked.connect(lambda: self._set_tab(1))
        self._btn_toybox.clicked.connect(lambda: self._set_tab(2))

        # ── stacked content ──
        self._stack = QStackedWidget()
        self._stack.setStyleSheet("background:transparent;")
        self._stack.addWidget(self._build_custom_page())
        self._stack.addWidget(self._build_preset_page(ANDROID_ROOT_SYMLINKS, kind="android"))
        self._stack.addWidget(self._build_preset_page(TOYBOX_SYMLINKS, kind="toybox"))
        root.addWidget(self._stack, 1)

        # ── footer ──
        footer = QHBoxLayout()
        footer.setSpacing(8)

        self._lbl_count = QLabel("")
        self._lbl_count.setStyleSheet(f"color:{_th.FG_MUTED}; font-size:11px; background:transparent;")
        footer.addWidget(self._lbl_count)

        footer.addStretch()

        btn_cancel = QPushButton(t("btn_cancel"))
        btn_cancel.setCursor(Qt.PointingHandCursor)
        btn_cancel.setStyleSheet(_btn_ss("default"))
        btn_cancel.clicked.connect(self.reject)
        footer.addWidget(btn_cancel)

        self._btn_create = QPushButton(t("sym_btn_create"))
        self._btn_create.setCursor(Qt.PointingHandCursor)
        self._btn_create.setStyleSheet(_btn_ss("primary"))
        self._btn_create.clicked.connect(self._on_create)
        footer.addWidget(self._btn_create)

        root.addLayout(footer)

        self._set_tab(0)
        self._refresh_count()


    def _build_custom_page(self):
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 12, 0, 0)
        lay.setSpacing(8)

        name_lbl = QLabel(t("sym_name_label"))
        name_lbl.setStyleSheet(f"color:{_th.FG}; font-size:11px; font-weight:600; background:transparent;")
        lay.addWidget(name_lbl)
        self._ed_name = QLineEdit()
        self._ed_name.setPlaceholderText(t("sym_name_ph"))
        self._ed_name.setFixedHeight(30)
        self._ed_name.setStyleSheet(_input_ss())
        lay.addWidget(self._ed_name)

        lay.addSpacing(4)

        tgt_lbl = QLabel(t("sym_target_label"))
        tgt_lbl.setStyleSheet(f"color:{_th.FG}; font-size:11px; font-weight:600; background:transparent;")
        lay.addWidget(tgt_lbl)
        self._ed_target = QLineEdit()
        self._ed_target.setPlaceholderText(t("sym_target_ph"))
        self._ed_target.setFixedHeight(30)
        self._ed_target.setStyleSheet(_input_ss())
        lay.addWidget(self._ed_target)

        lay.addStretch()
        return page


    def _build_preset_page(self, preset, kind):
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 8, 0, 0)
        lay.setSpacing(6)

        # action row: select all / clear
        act_row = QHBoxLayout()
        act_row.setSpacing(6)
        b_all = QPushButton(t("sym_select_all"))
        b_clr = QPushButton(t("sym_clear_all"))
        for b in (b_all, b_clr):
            b.setCursor(Qt.PointingHandCursor)
            b.setStyleSheet(_btn_ss("default"))
            b.setFixedHeight(24)
            act_row.addWidget(b)
        act_row.addStretch()
        lay.addLayout(act_row)

        # scroll list
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setStyleSheet(f"""
            QScrollArea {{
                background: transparent;
                border: 1px solid {_th.border_color(0.10)};
                border-radius: 6px;
            }}
            QScrollArea > QWidget > QWidget {{ background: transparent; }}

            QScrollBar:vertical {{
                background: transparent;
                width: 8px;
                margin: 4px 2px 4px 0;
                border: none;
            }}
            QScrollBar::handle:vertical {{
                background: {_th.border_color(0.18)};
                border-radius: 3px;
                min-height: 24px;
            }}
            QScrollBar::handle:vertical:hover  {{ background: {_th.border_color(0.30)}; }}
            QScrollBar::handle:vertical:pressed{{ background: {_th.accent(0.55)}; }}

            QScrollBar::add-line:vertical,
            QScrollBar::sub-line:vertical,
            QScrollBar::add-page:vertical,
            QScrollBar::sub-page:vertical {{
                background: none;
                border: none;
                height: 0;
                width: 0;
            }}
        """)

        inner = QWidget()
        inner.setStyleSheet("background:transparent;")
        ilay = QVBoxLayout(inner)
        ilay.setContentsMargins(8, 4, 8, 4)
        ilay.setSpacing(0)

        rows = []
        for name, target in preset:
            r = _PresetRow(name, target)
            r.cb.stateChanged.connect(self._refresh_count)
            ilay.addWidget(r)
            rows.append(r)
        ilay.addStretch()

        scroll.setWidget(inner)
        lay.addWidget(scroll, 1)

        b_all.clicked.connect(lambda: [r.set_checked(True)  for r in rows])
        b_clr.clicked.connect(lambda: [r.set_checked(False) for r in rows])

        # simpan rows ke page supaya bisa diakses saat Create
        page._rows = rows
        page._kind = kind
        return page


    def _set_tab(self, idx):
        self._stack.setCurrentIndex(idx)
        self._btn_custom.setStyleSheet (_tab_btn_ss(idx == 0))
        self._btn_android.setStyleSheet(_tab_btn_ss(idx == 1))
        self._btn_toybox.setStyleSheet (_tab_btn_ss(idx == 2))
        self._refresh_count()


    def _refresh_count(self):
        idx = self._stack.currentIndex()
        if idx == 0:
            self._lbl_count.setText("")
            return
        page = self._stack.widget(idx)
        n = sum(1 for r in page._rows if r.is_checked())
        self._lbl_count.setText(t("sym_count_selected", n=n) if n else "")


    def _on_create(self):
        from main_ext.class_AppDialog import AppDialog

        idx = self._stack.currentIndex()
        items = []  # list of (name, target)

        if idx == 0:
            name   = self._ed_name.text().strip()
            target = self._ed_target.text().strip()
            if not name:
                AppDialog.error(self, t("sym_title"), t("sym_err_empty_name"))
                return
            if not target:
                AppDialog.error(self, t("sym_title"), t("sym_err_empty_target"))
                return
            items.append((name, target))
        else:
            page = self._stack.widget(idx)
            items = [(r.name, r.target) for r in page._rows if r.is_checked()]
            if not items:
                AppDialog.error(self, t("sym_title"), t("sym_err_no_selection"))
                return

        ok = 0
        try:
            for name, target in items:
                out = os.path.join(self.target_dir, name)
                if make_symlink(out, target, overwrite=True):
                    ok += 1
        except Exception as e:
            AppDialog.error(self, t("sym_title"), t("sym_err_failed", e=str(e)))
            return

        self.created_count = ok
        self.accept()


    def _kill_overlay(self):
        ov = getattr(self, "_overlay", None)
        if ov:
            try:
                if hasattr(ov, "close_overlay"):
                    ov.close_overlay()
                    self._overlay = None
                    return
                else:
                    ov.close()
            except Exception:
                pass
            try: ov.deleteLater()
            except Exception: pass
            self._overlay = None

    def done(self, r):
        self._kill_overlay()
        super().done(r)

    def closeEvent(self, e):
        self._kill_overlay()
        super().closeEvent(e)


    @staticmethod
    def run(parent, target_dir):

        if not target_dir or not os.path.isdir(target_dir):
            return 0
        dlg = SymlinkDialog(parent, target_dir)
        from app_ext.class_BlurOverlay import install_window_fade
        install_window_fade(dlg)
        dlg.exec()
        return dlg.created_count
