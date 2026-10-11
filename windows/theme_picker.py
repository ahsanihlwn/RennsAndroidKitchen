# SPDX-License-Identifier: MPL-2.0
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QWidget,
    QPushButton, QFrame, QButtonGroup, QGridLayout, QScrollArea
)
from PySide6.QtCore import Qt, QRectF, QTimer
from PySide6.QtGui import QPainter, QBrush, QPen, QColor, QPainterPath, QIcon
from main_ext.class_ThemeUi import theme as _th, _PALETTES
from core.theme_binding import bind_style, bind_pixmap, refresh_theme
from core.language_binding import bind_text, bind_language, refresh_language
from core.ui_lang import t
from icons.handler import icon


_DARK_ORDER  = ["default", "dark", "dark_green", "dark_purple", "dark_red",
                "dark_orange", "dark_ocean"]
_LIGHT_ORDER = ["light", "light_warm", "light_rose", "light_green",
                "light_purple", "light_gray"]
COLS = 4

_SCROLLBAR_QSS = """
QScrollBar:vertical {{
    background: transparent;
    width: 5px;
    margin: 0;
    border: none;
}}
QScrollBar::handle:vertical {{
    background: {handle};
    border-radius: 2px;
    min-height: 20px;
}}
QScrollBar::handle:vertical:hover {{
    background: {handle_hover};
}}
QScrollBar::add-line:vertical,
QScrollBar::sub-line:vertical {{
    height: 0;
    border: none;
    background: transparent;
}}
QScrollBar::add-page:vertical,
QScrollBar::sub-page:vertical {{
    background: transparent;
}}
QScrollBar:horizontal {{ height: 0; }}
"""


class _ThemePreview(QPushButton):
    W, H = 96, 68

    def __init__(self, palette_info, name):
        super().__init__()
        self._p    = palette_info
        self._name = name
        self.setCheckable(True)
        self.setFixedSize(self.W, self.H)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip(palette_info["label"])
        self.setStyleSheet("background:transparent; border:none;")

    def paintEvent(self, _ev):
        p        = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        pal      = self._p
        c_bg     = QColor(pal["BG"])
        c_bg2    = QColor(pal["BG2"])
        c_bg3    = QColor(pal.get("BG_DARK", pal["BG"]))
        c_accent = QColor(pal["accent_hex"])
        c_fg     = QColor(pal["FG"])
        c_fg2    = QColor(pal.get("FG2", pal["FG"]))
        c_bdr    = QColor(pal["BG2"]).lighter(130) if pal.get("_is_light") \
                   else QColor(255, 255, 255, 18)
        W, H     = self.W, self.H
        SIDEBAR  = 22
        TITLEBAR = 12

        is_light = (c_bg.red() + c_bg.green() + c_bg.blue()) > 500
        if self.isChecked():
            ring_col, ring_w = c_accent, 2.5
        else:
            ring_col = QColor(0, 0, 0, 80) if is_light else QColor(255, 255, 255, 30)
            ring_w   = 1.0

        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(ring_col, ring_w))
        p.drawRoundedRect(QRectF(0.5, 0.5, W - 1, H - 1), 6, 6)

        clip = QPainterPath()
        clip.addRoundedRect(QRectF(1, 1, W - 2, H - 2), 5, 5)
        p.setClipPath(clip)

        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(c_bg3))
        p.drawRect(0, 0, W, TITLEBAR)
        dot_y = TITLEBAR // 2
        for i, dc in enumerate([QColor(255,95,87), QColor(255,188,46), QColor(40,200,64)]):
            p.setBrush(QBrush(dc))
            p.drawEllipse(4 + i * 8, dot_y - 2, 5, 5)

        p.setBrush(QBrush(c_bg2))
        p.drawRect(0, TITLEBAR, SIDEBAR, H - TITLEBAR)
        p.setBrush(QBrush(QColor(c_fg2.red(), c_fg2.green(), c_fg2.blue(), 50)))
        for i in range(3):
            p.drawRoundedRect(3, TITLEBAR + 5 + i * 9, SIDEBAR - 6, 5, 1, 1)
        p.setBrush(QBrush(QColor(c_accent.red(), c_accent.green(), c_accent.blue(), 150)))
        p.drawRoundedRect(3, TITLEBAR + 5, SIDEBAR - 6, 5, 1, 1)

        CX = SIDEBAR
        p.setBrush(QBrush(c_bg))
        p.drawRect(CX, TITLEBAR, W - CX, H - TITLEBAR)
        lx, lw = CX + 5, W - CX - 10
        p.setBrush(QBrush(QColor(c_fg.red(), c_fg.green(), c_fg.blue(), 65)))
        p.drawRoundedRect(lx, TITLEBAR + 6,  int(lw * 0.7), 3, 1, 1)
        p.setBrush(QBrush(QColor(c_fg.red(), c_fg.green(), c_fg.blue(), 35)))
        p.drawRoundedRect(lx, TITLEBAR + 13, int(lw * 0.9), 2, 1, 1)
        p.drawRoundedRect(lx, TITLEBAR + 19, int(lw * 0.6), 2, 1, 1)
        p.setBrush(QBrush(QColor(c_accent.red(), c_accent.green(), c_accent.blue(), 210)))
        p.drawRoundedRect(lx, TITLEBAR + 26, int(lw * 0.45), 6, 2, 2)
        p.setPen(QPen(c_bdr, 0.5))
        p.drawLine(SIDEBAR, TITLEBAR, SIDEBAR, H)
        p.end()


class ThemePickerDialog(QDialog):


    def __init__(self, parent=None):
        super().__init__(parent)
        self._app_parent = parent

        bind_language(self, "setWindowTitle", lambda: t("thm_title"))
        self.setFixedSize(520, 460)
        self.setModal(True)
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_DeleteOnClose, False)

        self._shell = QVBoxLayout(self)
        self._shell.setContentsMargins(0, 0, 0, 0)

        self._build_ui()
        self._center()

        # Daftarkan ke app parent supaya tidak ke-delete saat _reinit
        try:
            if self._app_parent is not None:
                lst = getattr(self._app_parent, "_persistent_dialogs", None)
                if lst is None:
                    lst = []
                    self._app_parent._persistent_dialogs = lst
                if self not in lst:
                    lst.append(self)
        except Exception:
            pass
        self.finished.connect(self._forget_persistent)

    def _forget_persistent(self, _result):
        if self._app_parent is not None:
            dialogs = getattr(self._app_parent, "_persistent_dialogs", None)
            if dialogs and self in dialogs:
                dialogs.remove(self)

    # build / rebuild
    def _build_ui(self, skip_widgets=None):
        skip = set(skip_widgets) if skip_widgets else set()
        for child in self.findChildren(QWidget):
            if child in skip:
                continue
            child.setParent(None)
            child.deleteLater()
        while self._shell.count():
            item = self._shell.takeAt(0)
            w = item.widget()
            if w and w not in skip:
                w.setParent(None)

        box = QWidget()
        box.setObjectName("tp_box")
        bind_style(box, lambda: f"""
            QWidget#tp_box {{
                background: {_th.BG2};
                border: 1px solid {_th.border_color(0.22)};
                border-radius: 14px;
            }}
            QLabel {{ background: transparent; border: none; }}
        """)
        self._shell.addWidget(box)

        root = QVBoxLayout(box)
        root.setContentsMargins(20, 16, 20, 16)
        root.setSpacing(10)

        # header
        hdr = QHBoxLayout()
        hdr.setSpacing(8)

        ic = QLabel()
        bind_pixmap(ic, lambda: QIcon(icon("set_project")).pixmap(15, 15))
        ic.setFixedSize(18, 18)
        ic.setAlignment(Qt.AlignVCenter)
        hdr.addWidget(ic)

        title = bind_text(QLabel(), lambda: t("thm_title"))
        bind_style(title, lambda: f"color:{_th.FG_BRIGHT}; font-size:14px; font-weight:600; background:transparent;")
        hdr.addWidget(title)
        hdr.addStretch()
        root.addLayout(hdr)

        # scroll
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        scroll.viewport().setStyleSheet("background: transparent;")

        sb_qss = lambda: _SCROLLBAR_QSS.format(
            handle       = _th.border_color(0.18),
            handle_hover = _th.accent(0.50),
        )
        bind_style(scroll.verticalScrollBar(), sb_qss)

        content = QWidget()
        content.setStyleSheet("background: transparent;")
        cv = QVBoxLayout(content)
        cv.setContentsMargins(2, 4, 2, 4)
        cv.setSpacing(14)

        old_group = getattr(self, "_btn_group", None)
        if old_group is not None:
            old_group.deleteLater()
        self._btn_group = QButtonGroup(self)
        self._btn_group.setExclusive(True)
        self._btn_group.buttonClicked.connect(self._on_pick)

        for section_key, order in [("thm_section_dark", _DARK_ORDER),
                                   ("thm_section_light", _LIGHT_ORDER)]:
            sec_lbl = bind_text(QLabel(), lambda section_key=section_key: t(section_key))
            bind_style(sec_lbl, lambda: f"font-size:10px; font-weight:700; color:{_th.FG_MUTED}; letter-spacing:1px;")
            cv.addWidget(sec_lbl)

            grid = QWidget()
            grid.setStyleSheet("background: transparent;")
            gl = QGridLayout(grid)
            gl.setContentsMargins(0, 0, 0, 0)
            gl.setSpacing(12)

            for idx, pname in enumerate(p for p in order if p in _PALETTES):
                cell = QWidget()
                cell.setStyleSheet("background: transparent;")
                cl = QVBoxLayout(cell)
                cl.setContentsMargins(0, 0, 0, 0)
                cl.setSpacing(5)
                cl.setAlignment(Qt.AlignHCenter)

                btn = _ThemePreview(_PALETTES[pname], pname)
                if pname == _th.name:
                    btn.setChecked(True)
                self._btn_group.addButton(btn)

                lbl = QLabel(_PALETTES[pname]["label"])
                lbl.setAlignment(Qt.AlignCenter)
                bind_style(lbl, lambda: f"font-size:10px; color:{_th.FG_MUTED}; background:transparent; border:none;")
                cl.addWidget(btn, alignment=Qt.AlignHCenter)
                cl.addWidget(lbl)
                gl.addWidget(cell, idx // COLS, idx % COLS)

            gl.setColumnStretch(COLS, 1)
            cv.addWidget(grid)

        cv.addStretch()
        scroll.setWidget(content)
        root.addWidget(scroll, 1)

        # footer
        footer = QHBoxLayout()
        footer.setSpacing(8)

        self._lbl_active = bind_text(QLabel(), lambda: t("thm_active", name=_PALETTES[_th.name]['label']))
        bind_style(self._lbl_active, lambda: f"font-size:11px; color:{_th.FG_MUTED}; background:transparent;")
        footer.addWidget(self._lbl_active)
        footer.addStretch()

        btn_done = bind_text(QPushButton(), lambda: t("thm_btn_done"))
        btn_done.setFixedHeight(30)
        btn_done.setCursor(Qt.PointingHandCursor)
        bind_style(btn_done, lambda: f"""
            QPushButton {{
                background: {_th.accent(0.20)};
                border: 1px solid {_th.accent(0.40)};
                border-radius: 6px;
                color: {_th.FG_BLUE};
                font-size: 12px;
                font-weight: 600;
                padding: 0 18px;
            }}
            QPushButton:hover {{
                background: {_th.accent(0.32)};
                border-color: {_th.accent(0.60)};
                color: {_th.FG_BLUE_LIGHT};
            }}
            QPushButton:pressed {{ background: {_th.accent(0.48)}; }}
        """)
        btn_done.clicked.connect(self.accept)
        footer.addWidget(btn_done)
        root.addLayout(footer)

    # theme change
    def _refresh_for_language(self):
        refresh_language(self)

    def _refresh_for_theme(self):

        refresh_theme(self)
        for button in self._btn_group.buttons():
            button.setChecked(button._name == _th.name)
        self._lbl_active.setText(t("thm_active", name=_PALETTES[_th.name]["label"]))

    def _rebuild_for_theme(self):

        scroll_val = 0
        for sa in self.findChildren(QScrollArea):
            scroll_val = sa.verticalScrollBar().value()
            break

        snapshot = getattr(self, "_theme_snapshot_overlay", None)
        self._build_ui(skip_widgets={snapshot} if snapshot is not None else None)
        self._center()

        def _restore_scroll():
            for sa in self.findChildren(QScrollArea):
                sa.verticalScrollBar().setValue(scroll_val)
                break
        QTimer.singleShot(0, _restore_scroll)

    # pick
    def _on_pick(self, btn):
        if btn._name == _th.name:
            return
        if self._app_parent is not None:
            self._app_parent._prepare_theme_snapshot(self)
        _th.set(btn._name)

    # center
    def _center(self):
        p = self._app_parent
        if p:
            try:
                pg = p.geometry()
                self.move(
                    pg.x() + (pg.width()  - self.width())  // 2,
                    pg.y() + (pg.height() - self.height()) // 2,
                )
            except Exception:
                pass
