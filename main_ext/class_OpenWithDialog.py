# SPDX-License-Identifier: MPL-2.0
import os
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QWidget, QSizePolicy, QFrame, QLayout
)
from PySide6.QtCore import Qt, Signal, QEvent, QRectF, QRect, QPoint
from PySide6.QtGui import QIcon, QColor, QPainter

from icons.handler import icon
from main_ext.class_ThemeUi import theme as _th
from core.ui_lang import t
from app_ext.class_SmoothScrollArea import SmoothScrollArea
from app_ext.class_BlurOverlay import BlurCard
from core.skia_button import ButtonSurface
from core.skia_patch import SurfacePatch


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


def _header_style():
    return f"color:{_th.FG}; font-size:13px; font-weight:600; background:transparent; border:none;"


def _hint_style():
    return f"color:{_th.FG_BLUE_MUTED}; font-size:11px; background:transparent; border:none;"


def _sep_style():
    return f"background:{_th.border_color(0.06)}; border:none;"


def _btn_neutral():
    return f"""
        QPushButton {{
            background:{_th.border_color(0.05)}; border:1px solid {_th.border_color(0.08)};
            border-radius:7px; color:{_th.FG_MUTED}; font-size:12px; font-weight:400;
            padding:0 16px; height:30px;
        }}
        QPushButton:hover   {{ background:{_th.border_color(0.09)}; border-color:{_th.border_color(0.14)}; color:{_th.FG_BLUE_MUTED}; }}
        QPushButton:pressed {{ background:{_th.border_color(0.13)}; }}
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


class _OptionCard(QWidget):

    selected = Signal(str)
    chosen   = Signal(str)

    def __init__(self, key: str, icon_name: str, label: str, desc: str, parent=None):
        super().__init__(parent)
        self._key = key
        self._checked = False
        self._hover = False
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(56)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        # WA_Hover → Qt kirim HoverEnter/HoverLeave berbasis posisi mouse,
        # ga peduli child. State di-track manual, digambar di paintEvent.
        self.setAttribute(Qt.WA_Hover, True)

        row = QHBoxLayout(self)
        row.setContentsMargins(12, 0, 12, 0)
        row.setSpacing(12)

        ic_lbl = QLabel()
        ic_lbl.setFixedSize(28, 28)
        ic_lbl.setScaledContents(True)
        # Icon polos, tanpa background/border/dekorasi apa pun.
        ic_lbl.setStyleSheet("background:transparent; border:none;")
        _ic = QIcon(icon(icon_name))
        if not _ic.isNull():
            ic_lbl.setPixmap(_ic.pixmap(28, 28))
        ic_lbl.setAttribute(Qt.WA_TransparentForMouseEvents)
        row.addWidget(ic_lbl)

        text_col = QVBoxLayout()
        text_col.setContentsMargins(0, 0, 0, 0)
        text_col.setSpacing(1)

        name_lbl = QLabel(label)
        name_lbl.setStyleSheet(f"color:{_th.FG}; font-size:13px; font-weight:600; background:transparent; border:none;")
        name_lbl.setAttribute(Qt.WA_TransparentForMouseEvents)
        text_col.addWidget(name_lbl)

        if desc:
            desc_lbl = QLabel(desc)
            desc_lbl.setStyleSheet(f"color:{_th.FG_MUTED}; font-size:10px; background:transparent; border:none;")
            desc_lbl.setAttribute(Qt.WA_TransparentForMouseEvents)
            text_col.addWidget(desc_lbl)

        row.addLayout(text_col, 1)

    def set_checked(self, val: bool):
        self._checked = val
        self.update()

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.selected.emit(self._key)
        super().mousePressEvent(e)

    def mouseDoubleClickEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.chosen.emit(self._key)
        super().mouseDoubleClickEvent(e)

    def event(self, e):
        et = e.type()
        if et == QEvent.HoverEnter:
            self._hover = True
            self.update()
        elif et == QEvent.HoverLeave:
            self._hover = False
            self.update()
        return super().event(e)

    @staticmethod
    def _qcolor(css):
        # _th.accent()/border_color() mengembalikan string "rgba(r,g,b,a)" dgn
        # alpha 0..1 — QColor() tidak bisa parse format itu, jadi parse manual.
        s = str(css).strip()
        if s.startswith("rgba(") and s.endswith(")"):
            parts = s[5:-1].split(",")
            r, g, b = int(float(parts[0])), int(float(parts[1])), int(float(parts[2]))
            a = int(float(parts[3]) * 255) if len(parts) > 3 else 255
            return QColor(r, g, b, a)
        if s.startswith("rgb(") and s.endswith(")"):
            parts = s[4:-1].split(",")
            return QColor(int(float(parts[0])), int(float(parts[1])), int(float(parts[2])))
        return QColor(s)

    def paintEvent(self, _ev):
        # Gambar background + border manual (bukan stylesheet) supaya state
        # hover/selected selalu ke-repaint dengan benar.
        if self._checked:
            bg = self._qcolor(_th.accent(0.15)); border = self._qcolor(_th.accent(0.40))
        elif self._hover:
            bg = self._qcolor(_th.border_color(0.07)); border = self._qcolor(_th.border_color(0.10))
        else:
            bg = self._qcolor(_th.border_color(0.03)); border = self._qcolor(_th.border_color(0.06))

        key = (bg.rgba(), border.rgba(), self.devicePixelRatioF())
        if key != getattr(self, "_surface_key", None):
            self._surface_patch = SurfacePatch(ButtonSurface(bg, border, 1, 8), key[2])
            self._surface_key = key
        painter = QPainter(self)
        self._surface_patch.draw(painter, QRectF(self.rect()))


class OpenWithDialog(QDialog):
    _MAX_VISIBLE_OPTIONS = 5
    # (key, icon_name) — label & desc diambil dari translasi saat build
    _OPTIONS = [
        ("editor",  "edit"),
        ("so",      "icon_so"),
        ("bin",     "bin"),
        ("image",   "image"),
        ("archive", "archive"),
        ("payload", "icon_bin"),
        ("super",   "drive"),
    ]

    def __init__(self, parent, file_name: str = ""):
        super().__init__(parent)
        self._choice = None
        self._cards = []
        self._active_card = None

        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setObjectName("openWithDialog")
        self.setStyleSheet("QDialog#openWithDialog {background:transparent; border:none;}")
        self.setModal(True)
        self.setFixedWidth(380)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSizeConstraint(QLayout.SetNoConstraint)

        card = QWidget()
        card.setObjectName("dlg_card")
        card.setStyleSheet(_card_style())
        outer.addWidget(card)
        self._card = card
        tint = QColor(_th.BG2)
        tint.setAlpha(72)
        self._blur_surface = BlurCard(self, radius=18, tint=tint,
                                      border=QColor(0, 0, 0, 0), corner=14)
        self._blur_surface.setAttribute(Qt.WA_TransparentForMouseEvents)
        self._blur_surface.hide()

        root = QVBoxLayout(card)
        root.setSizeConstraint(QLayout.SetNoConstraint)
        root.setContentsMargins(20, 18, 20, 18)
        root.setSpacing(12)

        title = QLabel(t("ow_title"))
        title.setStyleSheet(_header_style())
        root.addWidget(title)

        if file_name:
            hint = QLabel(file_name)
            hint.setStyleSheet(_hint_style())
            hint.setWordWrap(True)
            hint.setToolTip(file_name)
            hint.ensurePolished()
            hint.setMaximumHeight(hint.fontMetrics().lineSpacing() * 3)
            root.addWidget(hint)

        sep = QWidget()
        sep.setFixedHeight(1)
        sep.setStyleSheet(_sep_style())
        root.addWidget(sep)

        _label = {
            "editor":  t("ow_editor"),
            "so":      t("ow_so"),
            "bin":     t("ow_bin"),
            "image":   t("ow_image"),
            "archive": t("ow_archive"),
            "payload": t("ow_payload"),
            "super":   t("ow_super"),
        }
        self._scroll = SmoothScrollArea()
        self._scroll.setObjectName("openWithOptions")
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        # Keep the list scrollable by wheel without showing a scrollbar hint.
        self._scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._scroll.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._scroll.setStyleSheet("QScrollArea#openWithOptions {background:transparent; border:none;}")
        self._scroll.viewport().setStyleSheet("background:transparent; border:none;")
        self._options_body = QWidget()
        self._options_body.setStyleSheet("background:transparent; border:none;")
        options_layout = QVBoxLayout(self._options_body)
        options_layout.setContentsMargins(0, 0, 0, 0)
        options_layout.setSpacing(12)
        options_layout.setAlignment(Qt.AlignTop)
        for key, icon_name in self._OPTIONS:
            c = _OptionCard(key, icon_name, _label[key], "")
            c.selected.connect(self._on_card_selected)
            c.chosen.connect(self._on_card_chosen)
            options_layout.addWidget(c)
            self._cards.append(c)
        self._scroll.setWidget(self._options_body)
        root.addWidget(self._scroll, 1)

        sep2 = QWidget()
        sep2.setFixedHeight(1)
        sep2.setStyleSheet(_sep_style())
        root.addWidget(sep2)

        # Custom "Always" CheckBox
        from PySide6.QtCore import QSize
        from icons.handler import icon as _icon
        _check_icon = QIcon(_icon("check_white"))

        self.chk_always = QPushButton()
        self.chk_always.setCheckable(True)
        self.chk_always.setFixedSize(20, 20)
        self.chk_always.setCursor(Qt.PointingHandCursor)
        self.chk_always.setObjectName("permCheck")
        self.chk_always.setStyleSheet(_ss_perm_check())
        self.chk_always.setIconSize(QSize(14, 14))
        def _toggle_icon(v):
            self.chk_always.setIcon(_check_icon if v else QIcon())
        self.chk_always.toggled.connect(_toggle_icon)

        chk_label = QLabel(t("ow_always"))
        chk_label.setStyleSheet(f"color:{_th.FG_MUTED}; font-size:12px; background:transparent;")
        chk_label.setCursor(Qt.PointingHandCursor)
        chk_label.setWordWrap(True)

        # Biar klik label juga nge-toggle checkbox
        chk_label.mousePressEvent = lambda e: self.chk_always.toggle()

        chk_layout = QHBoxLayout()
        chk_layout.setContentsMargins(0, 0, 0, 0)
        chk_layout.setSpacing(10)
        chk_layout.addWidget(self.chk_always)
        chk_layout.addWidget(chk_label, 1)
        root.addLayout(chk_layout)

        foot = QHBoxLayout()
        foot.setSpacing(8)
        foot.addStretch()

        btn_cancel = QPushButton(t("btn_cancel"))
        btn_cancel.setStyleSheet(_btn_neutral())
        btn_cancel.setFixedHeight(30)
        btn_cancel.setCursor(Qt.PointingHandCursor)
        btn_cancel.clicked.connect(self.reject)

        self._btn_ok = QPushButton(t("ow_open"))
        self._btn_ok.setStyleSheet(self._btn_primary())
        self._btn_ok.setFixedHeight(30)
        self._btn_ok.setEnabled(False)
        self._btn_ok.setCursor(Qt.PointingHandCursor)
        self._btn_ok.clicked.connect(self._confirm)

        foot.addWidget(btn_cancel)
        foot.addWidget(self._btn_ok)
        root.addLayout(foot)

        self._fit_to_bounds()

    def _available_bounds(self):
        parent = self.parentWidget()
        screen = parent.screen() if parent else self.screen()
        bounds = screen.availableGeometry()
        if parent is not None:
            window = parent.window()
            rect = QRect(window.mapToGlobal(QPoint()), window.size())
            intersection = bounds.intersected(rect)
            if not intersection.isEmpty():
                bounds = intersection
        return bounds.adjusted(12, 12, -12, -12)

    def _fit_to_bounds(self):
        bounds = self._available_bounds()
        self.setFixedWidth(max(1, min(380, bounds.width())))
        self.ensurePolished()
        for child in self._card.findChildren(QWidget):
            child.ensurePolished()
        self.setMinimumHeight(0)
        self.setMaximumHeight(16777215)
        visible = min(len(self._cards), self._MAX_VISIBLE_OPTIONS)
        wanted = visible * 56 + max(0, visible-1) * 12
        self._scroll.setFixedHeight(wanted)
        self._card.layout().invalidate()
        self.layout().invalidate()
        self._card.layout().activate()
        self.layout().activate()
        desired = self.layout().totalHeightForWidth(self.width())
        if desired < 0:
            desired = self.layout().sizeHint().height()
        chrome = desired - wanted
        # Only the option viewport shrinks; title, remember toggle and actions
        # stay outside it. QScrollArea supplies native child clipping.
        self._scroll.setFixedHeight(max(1, min(wanted, bounds.height()-chrome)))
        self.setFixedHeight(max(1, min(desired, bounds.height())))
        self._card.layout().activate()
        self.layout().activate()
        self.move(bounds.center() - self.rect().center())
        self._sync_blur_surface()

    def _sync_blur_surface(self):
        self._blur_surface.setGeometry(self._card.geometry())
        self._blur_surface.lower()
        self._blur_surface.show()
        self._card.raise_()

    @staticmethod
    def _btn_primary():
        return f"""
            QPushButton {{
                background:{_th.accent(0.15)}; border:1px solid {_th.accent(0.30)};
                border-radius:7px; color:{_th.FG_BLUE}; font-size:12px; font-weight:600;
                padding:0 16px; height:30px;
            }}
            QPushButton:hover   {{ background:{_th.accent(0.27)}; border-color:{_th.accent(0.52)}; }}
            QPushButton:pressed {{ background:{_th.accent(0.40)}; }}
            QPushButton:disabled {{ background:{_th.border_color(0.07)}; border:1px solid {_th.border_color(0.12)};
                                    color:{_th.FG_MUTED}; }}
        """

    def _on_card_selected(self, key: str):
        if self._active_card:
            self._active_card.set_checked(False)
        for c in self._cards:
            if c._key == key:
                c.set_checked(True)
                self._active_card = c
                break
        self._choice = key
        self._btn_ok.setEnabled(True)

    def _on_card_chosen(self, key: str):
        self._choice = key
        self.accept()

    def _confirm(self):
        if not self._choice:
            return
        self.accept()

    def showEvent(self, e):
        super().showEvent(e)
        self._fit_to_bounds()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "_blur_surface"):
            self._sync_blur_surface()

    @staticmethod
    def pick(parent, file_name: str = "", with_blur: bool = True):

        from app_ext.class_BlurOverlay import BlurOverlay as _BlurOverlay, install_window_fade
        dlg = OpenWithDialog(parent, file_name=file_name)
        _ov = _BlurOverlay(parent) if (parent and with_blur) else None
        if _ov:
            dlg._overlay = _ov
        install_window_fade(dlg)
        result = dlg.exec()
        if _ov:
            _ov.close_overlay()
        if result == QDialog.Accepted:
            return dlg._choice, dlg.chk_always.isChecked()
        return None, False
