# SPDX-License-Identifier: MPL-2.0
from PySide6.QtWidgets import (
    QWidget, QHBoxLayout, QPushButton, QLabel, QGraphicsOpacityEffect
)
from PySide6.QtCore import (
    Qt, QPropertyAnimation, QEasingCurve, QRect, QEvent, QSize, QTimer
)
from PySide6.QtGui import QIcon, QColor, QPixmap, QPainter
from icons.handler import icon
from main_ext.class_ThemeUi import theme as _th


class SmartActionBar(QWidget):

    @staticmethod
    def _button_fill():
        base = QColor(_th.BG)
        raised = QColor(_th.BG4)
        return QColor(
            round(base.red() * 0.45 + raised.red() * 0.55),
            round(base.green() * 0.45 + raised.green() * 0.55),
            round(base.blue() * 0.45 + raised.blue() * 0.55),
        ).name()

    @staticmethod
    def _group_style():
        return f"""
            QWidget#smartActionGroup {{
                background: {SmartActionBar._button_fill()};
                border: 1px solid {_th.border_color(0.06)};
                border-radius: 8px;
            }}
        """

    @staticmethod
    def _segment_style():
        return f"""
            QPushButton {{
                background: transparent;
                border: none;
                border-radius: 5px;
                padding: 0;
            }}
            QPushButton:hover:enabled {{
                background: {_th.border_color(0.08)};
            }}
            QPushButton:pressed:enabled {{
                background: {_th.border_color(0.13)};
            }}
            QPushButton:disabled {{
                background: transparent;
            }}
        """

    @staticmethod
    def _compact_style():
        return f"""
            QPushButton {{
                background: {SmartActionBar._button_fill()};
                border: 1px solid {_th.border_color(0.06)};
                border-radius: 8px;
                padding: 0;
            }}
            QPushButton:hover:enabled {{
                background: {_th.border_color(0.08)};
                border-color: {_th.border_color(0.14)};
            }}
            QPushButton:pressed:enabled {{
                background: {_th.border_color(0.13)};
            }}
        """

    def __init__(self, parent=None):
        super().__init__(parent)

        self.setFixedHeight(32)

        self.root_layout = QHBoxLayout(self)
        self.root_layout.setContentsMargins(0, 0, 0, 0)
        self.root_layout.setSpacing(0)

        # container tombol inline
        self.hidden_container = QWidget(self)
        self.hidden_container.setObjectName("smartActionGroup")
        self.hidden_container.setAttribute(Qt.WA_StyledBackground, True)
        self.hidden_container.setFixedHeight(32)
        self.hidden_container.setStyleSheet(self._group_style())
        self.hidden_layout = QHBoxLayout(self.hidden_container)
        self.hidden_layout.setContentsMargins(2, 2, 2, 2)
        self.hidden_layout.setSpacing(0)

        # tombol compact (expand)
        self.compact_btn = QPushButton()
        self.compact_btn.setIcon(QIcon(icon("expand")))
        self.compact_btn.setIconSize(QSize(16, 16))
        self.compact_btn.setFixedSize(28, 28)
        self.compact_btn.setCursor(Qt.PointingHandCursor)
        self.compact_btn.setStyleSheet(self._compact_style())

        self.root_layout.addWidget(self.hidden_container, 0, Qt.AlignVCenter)
        self.root_layout.addWidget(self.compact_btn, 0, Qt.AlignVCenter)
        self.root_layout.addStretch()

        # default = inline
        self.hidden_container.show()
        self.compact_btn.hide()

        self.compact_btn.clicked.connect(self.toggle_popup)

        self.popup    = None
        self.anim     = None
        self.buttons  = []
        self.is_open  = False

    # force modes

    def show_inline(self):
        if self.is_open:
            if self.anim:
                self.anim.stop()
            if self.popup:
                self.popup.close()
                self.popup = None
            self.is_open = False
        self.compact_btn.hide()
        self.hidden_container.show()
        self.updateGeometry()

    def show_compact(self):
        # Kalau popup lagi terbuka, jangan close+reopen — cukup biarkan
        if not self.is_open:
            pass
        self.hidden_container.hide()
        self.compact_btn.show()

    def check_overflow(self):

        if getattr(self, "_checking", False):
            return
        if not self.buttons:
            return

        # Butuh referensi right_wrap — disimpan lewat set_right_ref()
        right_ref = getattr(self, "_right_ref", None)
        if right_ref is None or not right_ref.isVisible() or not self._right_ref_has_visible_child(right_ref):
            # Area kanan tutup / belum ada → pastikan inline
            if self.compact_btn.isVisible():
                self._checking = True
                self.show_inline()
                self._checking = False
            return
        if right_ref.width() < 10:
            # Area kanan visible tapi terlalu kecil (lagi di-drag mepet) → skip
            return

        # Posisi X kiri right_wrap dalam koordinat window
        win = self.window()
        right_x = right_ref.mapTo(win, right_ref.rect().topLeft()).x()

        # Posisi X kanan button tampak paling kanan dalam koordinat window
        visible_btns = [b for b in self.buttons if not b.isHidden()]
        if not visible_btns:
            return
        last_btn = visible_btns[-1]
        try:
            last_btn_right = last_btn.mapTo(win, last_btn.rect().topRight()).x()
        except RuntimeError:
            return  # button udah deleted (race condition saat hide)

        MARGIN_IN  = 56   # px — masuk compact: trigger sebelum nyentuh
        MARGIN_OUT = 100  # px — keluar compact: butuh lebih jauh buat balik inline

        gap = right_x - last_btn_right
        is_compact = self.compact_btn.isVisible()

        self._checking = True
        try:
            if not is_compact and gap < MARGIN_IN:
                self.hidden_container.hide()
                self.compact_btn.show()
            elif is_compact and gap > MARGIN_OUT:
                self.show_inline()
            elif is_compact and self.is_open and self.popup:
                # Popup lagi buka tapi compact_btn udah geser — reposition popup
                self._reposition_popup()
        finally:
            self._checking = False

    def _reposition_popup(self):

        if not self.popup:
            return
        total_w = self.popup.width()
        total_h = self.popup.height()
        btn_global = self.compact_btn.mapToGlobal(self.compact_btn.rect().topLeft())
        btn_cx = btn_global.x() + self.compact_btn.width() // 2
        btn_cy = btn_global.y() + self.compact_btn.height() // 2
        self.popup.move(btn_cx - self.popup.width() // 2, btn_cy - self.popup.height() // 2)

    def set_right_ref(self, widget):

        self._right_ref = widget

    @staticmethod
    def _right_ref_has_visible_child(widget):

        try:
            count = widget.count()
        except Exception:
            return True

        if count <= 0:
            return True

        try:
            return any(widget.widget(i).isVisible() for i in range(count))
        except Exception:
            return True

    def resizeEvent(self, e):
        super().resizeEvent(e)

    # add button

    def add_button(self, btn):
        btn.setParent(self.hidden_container)
        btn.setFlat(False)
        btn.setFixedSize(28, 28)
        btn.setCursor(Qt.PointingHandCursor)
        btn.setStyleSheet(self._segment_style())
        self.hidden_layout.addWidget(btn)
        self.buttons.append(btn)
        btn.installEventFilter(self)

    def refresh_theme(self):
        self.hidden_container.setStyleSheet(self._group_style())
        self.compact_btn.setStyleSheet(self._compact_style())
        style = self._segment_style()
        for btn in self.buttons:
            btn.setStyleSheet(style)
        if self.is_open and self.popup:
            self.refresh_popup()

    # toggle

    def toggle_popup(self):
        if self.is_open:
            self.close_popup()
        else:
            self.open_popup()

    # open popup

    def open_popup(self):
        if self.popup:
            self.popup.deleteLater()

        from app_ext.class_BlurOverlay import BlurCard

        # outer popup container — frameless, translucent kayak lang selector
        self.popup = QWidget(None, Qt.Popup | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint)
        self.popup.setAttribute(Qt.WA_TranslucentBackground)
        self.popup.setAttribute(Qt.WA_DeleteOnClose)

        outer_lay = QHBoxLayout(self.popup)
        outer_lay.setContentsMargins(0, 0, 0, 0)

        _br, _bg, _bb = _th._palette["_border_rgb"]
        _tint = QColor(_th.BG2); _tint.setAlpha(128)
        card = BlurCard(radius=12.0, tint=_tint,
                        border=QColor(_br, _bg, _bb, int(0.18 * 255)))
        outer_lay.addWidget(card)

        lay = QHBoxLayout(card)
        lay.setContentsMargins(6, 4, 6, 4)
        lay.setSpacing(4)

        # collapse button
        btn_close = QPushButton()
        btn_close.setIcon(QIcon(icon("expand")))
        btn_close.setIconSize(QSize(14, 14))
        btn_close.setFixedSize(28, 28)
        btn_close.setCursor(Qt.PointingHandCursor)
        btn_close.setStyleSheet(self._compact_style())
        btn_close.clicked.connect(self.close_popup)
        lay.addWidget(btn_close)

        sep = QWidget(); sep.setFixedSize(1, 18)
        sep.setStyleSheet(f"background: {_th.border_color(0.08)};")
        lay.addWidget(sep)

        popup_group = QWidget()
        popup_group.setObjectName("smartActionGroup")
        popup_group.setAttribute(Qt.WA_StyledBackground, True)
        popup_group.setFixedHeight(32)
        popup_group.setStyleSheet(self._group_style())
        popup_group_layout = QHBoxLayout(popup_group)
        popup_group_layout.setContentsMargins(2, 2, 2, 2)
        popup_group_layout.setSpacing(0)
        _popup_btn_ss = self._segment_style()
        # Proxy buttons — jangan reparent btn asli ke popup (ntar ke-delete ikut popup).
        # Buat QPushButton baru di popup, forward click + state ke btn asli.
        visible_buttons = [btn for btn in self.buttons if not btn.isHidden()]
        for btn in visible_buttons:
            proxy = QPushButton()
            proxy.setIcon(btn.icon())
            proxy.setIconSize(btn.iconSize())
            proxy.setFixedSize(28, 28)
            proxy.setCursor(Qt.PointingHandCursor)
            proxy.setEnabled(btn.isEnabled())
            if btn.toolTip():
                proxy.setToolTip(btn.toolTip())
            proxy.setStyleSheet(_popup_btn_ss)
            proxy.clicked.connect(btn.click)
            popup_group_layout.addWidget(proxy)
        if visible_buttons:
            lay.addWidget(popup_group)

        card.adjustSize()
        self.popup.adjustSize()

        total_w = self.popup.sizeHint().width()
        total_h = self.popup.sizeHint().height()

        # posisi: center popup tepat di tengah compact_btn
        btn_global = self.compact_btn.mapToGlobal(self.compact_btn.rect().topLeft())
        btn_cx = btn_global.x() + self.compact_btn.width() // 2
        btn_cy = btn_global.y() + self.compact_btn.height() // 2
        end_x  = btn_cx - total_w // 2
        end_y  = btn_cy - total_h // 2

        # freeze blur di posisi final — sebelum show
        from PySide6.QtCore import QPoint
        card.freeze(self.window(), QPoint(end_x, end_y))

        self.popup.move(end_x, end_y)
        self.popup.setWindowOpacity(0.0)
        self.popup.show()
        self.popup.installEventFilter(self)
        # Reset state waktu popup di-destroy (WA_DeleteOnClose)
        self.popup.destroyed.connect(lambda: self._on_popup_destroyed())

        self.anim = QPropertyAnimation(self.popup, b"windowOpacity")
        self.anim.setDuration(160)
        self.anim.setStartValue(0.0)
        self.anim.setEndValue(1.0)
        self.anim.setEasingCurve(QEasingCurve.OutCubic)
        self.anim.start()

        self.is_open = True

    def _on_popup_destroyed(self):
        self.popup = None
        self.is_open = False

    # close popup

    def close_popup(self):
        if not self.popup:
            return
        try:
            self.popup.close()
        except RuntimeError:
            pass
        self.popup = None
        self.is_open = False

    # event filter (click outside = close)

    def refresh_popup(self):

        if self.is_open and self.popup:
            self.close_popup()
            self.open_popup()

    def set_button_visible(self, btn, visible: bool):

        if btn in self.buttons:
            btn.setVisible(visible)
            if self.is_open and self.popup:
                self.refresh_popup()
            self.check_overflow()

    def eventFilter(self, obj, event):
        if obj == self.popup:
            if event.type() == QEvent.MouseButtonPress:
                if not self.popup.geometry().contains(event.globalPosition().toPoint()):
                    self.close_popup()
            if event.type() == QEvent.WindowDeactivate:
                self.close_popup()
        elif obj in getattr(self, "buttons", []):
            if event.type() in (QEvent.ShowToParent, QEvent.HideToParent, QEvent.EnabledChange):
                if not getattr(self, "_event_filtering", False):
                    self._event_filtering = True
                    QTimer.singleShot(0, self._on_button_state_changed)
        return super().eventFilter(obj, event)

    def _on_button_state_changed(self):
        self._event_filtering = False
        if self.is_open and self.popup:
            self.refresh_popup()
        self.check_overflow()
