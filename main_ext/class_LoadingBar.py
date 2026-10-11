# SPDX-License-Identifier: MPL-2.0
from PySide6.QtWidgets import (QWidget, QHBoxLayout, QVBoxLayout, QLabel,
                                QFrame, QPushButton, QScrollArea)
from PySide6.QtCore import Qt, QRectF, QPoint, Signal
from PySide6.QtGui import QPainter, QColor
from main_ext.class_ThemeUi import theme as _th
from app_ext.class_LoadingWindow import _Spinner
from app_ext.class_BlurOverlay import BlurCard


class _ClickableLabel(QLabel):

    clicked = Signal()

    def mousePressEvent(self, ev):
        if ev.button() == Qt.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(ev)


class _StatusIndicator(QWidget):

    SIZE = 14

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(self.SIZE, self.SIZE)
        self._state = "idle"
        ar, ag, ab = _th._palette["_accent_rgb"]


        self._spinner = _Spinner(size=self.SIZE, accent=QColor(ar, ag, ab),
                                 thickness=3, parent=self)
        self._spinner.move(0, 0)
        self._spinner.stop()
        self._spinner.hide()

    def apply_theme(self):

        ar, ag, ab = _th._palette["_accent_rgb"]
        self._spinner.set_accent(QColor(ar, ag, ab))
        self.update()

    def set_active(self):
        self._state = "active"
        try:
            self._spinner._timer.start()
        except Exception:
            pass
        self._spinner.show()
        self.show()
        self.update()

    def set_state(self, state):
        self._state = state
        self._spinner.stop()
        self._spinner.hide()
        self.show()
        self.update()

    def set_idle(self):
        self._state = "idle"
        self._spinner.stop()
        self._spinner.hide()
        self.hide()

    def paintEvent(self, _ev):
        if self._state in ("idle", "active"):
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)

        if self._state == "done":
            c = QColor(52, 199, 120)
        else:
            c = QColor(220, 80, 80)

        p.setBrush(c)
        d = 8
        off = (self.SIZE - d) / 2
        p.drawEllipse(QRectF(off, off, d, d))
        p.end()


class LoadingBar(QWidget):


    clear_history_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(20)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet("background: transparent;")

        self._pending_labels = []
        self._history_lines  = []
        # Ref popup ••• yg lagi kebuka (utk refresh realtime saat state berubah).
        self._popup = None


        self._text_tier = "dim"

        root = QHBoxLayout(self)
        root.setContentsMargins(10, 0, 10, 2)
        root.setSpacing(7)

        self._dot  = _StatusIndicator(self)
        self._dot.hide()

        self._text = QLabel("")
        self._text.setStyleSheet(self._text_ss(_th.FG_DIM))

        # ••• marker — klik buka popup Pending + History (+ tombol Clear).
        self._dots = _ClickableLabel("•••")
        self._dots.setStyleSheet(self._text_ss(_th.FG_DIM))
        self._dots.setCursor(Qt.PointingHandCursor)
        self._dots.clicked.connect(self._open_history_popup)
        self._dots.hide()

        root.addWidget(self._dot, 0, Qt.AlignVCenter)
        root.addWidget(self._text, 0, Qt.AlignVCenter)
        root.addWidget(self._dots, 0, Qt.AlignVCenter)
        root.addStretch()

        # Ikut theme switch: warna teks + ring accent ke-refresh live (dulu cuma
        # ke-apply pas state berubah, jadi nyangkut warna theme lama).
        try:
            _th.on_change(self.apply_theme)
        except Exception:
            pass

    # theme
    def _tier_color(self, tier):
        return {
            "label": _th.FG_LABEL,
            "green": _th.GREEN_STATUS,
            "red":   _th.RED_STATUS,
        }.get(tier, _th.FG_DIM)

    def apply_theme(self):

        self._text.setStyleSheet(self._text_ss(self._tier_color(self._text_tier)))
        self._dots.setStyleSheet(self._text_ss(_th.FG_DIM))
        self._dot.apply_theme()

    # helpers
    def _text_ss(self, color):
        return (
            f"color:{color}; font-size:11px; font-weight:700;"
            " font-family:'Poppins','Inter','Segoe UI',system-ui,sans-serif;"
            " background:transparent; border:none; letter-spacing:0.1px;"
        )

    # public API
    def _hide_dots(self):
        self._pending_labels = []
        self._history_lines  = []
        self._dots.hide()
        # ••• ilang → popup yg masih kebuka ikut ditutup (mis. abis Clear history).
        if self._popup is not None:
            try:
                self._popup.close()
            except Exception:
                pass
            self._popup = None

    def _apply_dots(self, pending_labels, history_lines):

        pending_labels = pending_labels or []
        history_lines  = history_lines or []
        if not pending_labels and not history_lines:
            self._hide_dots()
            return
        self._pending_labels = pending_labels
        self._history_lines  = history_lines
        self._dots.show()


    POPUP_W      = 260   # lebar tetap popup
    POPUP_BODY_H = 240   # tinggi tetap area scroll (isi pending+history)

    def _section(self, lay, title, lines, bullet=False, history=False):

        t = QLabel(title)
        t.setStyleSheet(
            f"color:{_th.FG_DIM}; font-size:10px; font-weight:700;"
            " background:transparent; border:none; letter-spacing:0.3px;")
        lay.addWidget(t)
        if history:


            check_white      = _th.white(0.92)   # ✓ putih solid
            check_white_lite = _th.white(0.55)   # ✗ putih redup
            for ln in lines:
                s = ln.lstrip()
                is_fail = s.startswith("✗")
                mark = "✗" if is_fail else "✓"
                rest = s[len(mark):].lstrip() if s[:1] in ("✓", "✗") else s
                mark_col = check_white_lite if is_fail else check_white
                text_col = _th.RED_STATUS if is_fail else _th.FG_LABEL
                # html-escape nama supaya '<'/'&' di path/label ga ngerusak markup
                from html import escape as _esc
                b = QLabel(
                    f"<span style='color:{mark_col};'>{mark}</span> "
                    f"<span style='color:{text_col};'>{_esc(rest)}</span>")
                b.setTextFormat(Qt.RichText)
                b.setWordWrap(True)
                b.setStyleSheet(
                    "font-size:11px; background:transparent; border:none;")
                lay.addWidget(b)
        else:
            body = "\n".join((("• " + ln) if bullet else ln) for ln in lines)
            b = QLabel(body)
            b.setWordWrap(True)
            b.setStyleSheet(
                f"color:{_th.FG_LABEL}; font-size:11px;"
                " background:transparent; border:none;")
            lay.addWidget(b)

    def _build_popup_body(self, card, popup):

        lay = QVBoxLayout(card)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(6)


        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll.setFixedHeight(self.POPUP_BODY_H)
        scroll.setStyleSheet(
            "QScrollArea{background:transparent; border:none;}"
            " QScrollArea>QWidget>QWidget{background:transparent;}"
            " QScrollBar:vertical{background:transparent; width:7px; margin:2px;}"
            f" QScrollBar::handle:vertical{{background:{_th.white(0.28)};"
            "   border-radius:3px; min-height:28px;}"
            f" QScrollBar::handle:vertical:hover{{background:{_th.white(0.42)};}}"
            " QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{height:0;}"
            " QScrollBar::add-page:vertical,QScrollBar::sub-page:vertical{background:transparent;}")

        body = QWidget()
        body.setStyleSheet("background:transparent;")
        body_lay = QVBoxLayout(body)
        body_lay.setContentsMargins(0, 0, 0, 0)
        body_lay.setSpacing(6)

        if self._pending_labels:
            self._section(body_lay, "PENDING", self._pending_labels, bullet=True)
        if self._history_lines:
            self._section(body_lay, "HISTORY", self._history_lines, history=True)
        body_lay.addStretch()

        scroll.setWidget(body)
        lay.addWidget(scroll)

        if self._history_lines:
            clear_btn = QPushButton("Clear history")
            clear_btn.setCursor(Qt.PointingHandCursor)
            clear_btn.setFixedHeight(26)
            clear_btn.setStyleSheet(f"""
                QPushButton {{
                    background: {_th.border_color(0.06)};
                    border: 1px solid {_th.border_color(0.14)};
                    border-radius: 6px; color: {_th.FG};
                    font-size: 11px; font-weight: 600; padding: 0 12px;
                }}
                QPushButton:hover {{ background: {_th.border_color(0.10)}; }}
                QPushButton:pressed {{ background: {_th.border_color(0.16)}; }}
            """)

            def _clear(_=False):
                self.clear_history_requested.emit()
                popup.close()
            clear_btn.clicked.connect(_clear)

            lay.addSpacing(4)
            lay.addWidget(clear_btn, 0, Qt.AlignRight)

    def _refresh_popup(self):

        popup = self._popup
        if popup is None:
            return
        # Habis di-clear → _hide_dots udah nutup popup; guard saja.
        if not self._pending_labels and not self._history_lines:
            return
        card = popup.findChild(BlurCard, "loadinghistorycard")
        if card is None:
            return
        # Buang layout + child lama, lalu bangun ulang dari state terkini.
        old = card.layout()
        if old is not None:
            while old.count():
                it = old.takeAt(0)
                w = it.widget()
                if w is not None:
                    w.setParent(None)
                    w.deleteLater()
            # Detach layout lama dari card (reparent ke widget buang) supaya
            # bisa pasang layout baru — Qt larang 2 layout di 1 widget.
            QWidget().setLayout(old)
        self._build_popup_body(card, popup)


    def _open_history_popup(self):
        if not self._pending_labels and not self._history_lines:
            return

        popup = QWidget(self.window(), Qt.Popup | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint)
        popup.setAttribute(Qt.WA_TranslucentBackground)
        popup.setAttribute(Qt.WA_DeleteOnClose)

        outer = QVBoxLayout(popup)
        outer.setContentsMargins(0, 0, 0, 0)

        # BlurCard: latar frosted-glass (capture area di balik card lalu blur).
        card = BlurCard(corner=10.0,
                        tint=QColor(18, 20, 26, 210),
                        border=QColor(255, 255, 255, 30))
        card.setObjectName("loadinghistorycard")
        outer.addWidget(card)

        self._build_popup_body(card, popup)


        btn_h = (4 + 26) if self._history_lines else 0
        ph = 10 + self.POPUP_BODY_H + 10 + btn_h
        pw = self.POPUP_W
        popup.setFixedSize(pw, ph)


        card.setFixedSize(pw, ph)

        global_pos = self._dots.mapToGlobal(self._dots.rect().topRight())
        x = global_pos.x() - pw
        y = global_pos.y() - ph - 6

        win = self.window()
        tl = win.mapToGlobal(win.rect().topLeft())
        brc = win.mapToGlobal(win.rect().bottomRight())
        x = max(tl.x(), min(x, brc.x() - pw))
        y = max(tl.y(), min(y, brc.y() - ph))
        popup.move(QPoint(x, y))


        try:
            card.freeze(win, QPoint(x, y))
        except Exception:
            pass

        # Lacak popup aktif utk refresh realtime + bersihin ref saat tutup.
        self._popup = popup
        popup.destroyed.connect(lambda *_: setattr(self, "_popup", None))
        popup.show()

    def set_clean(self, history_lines: list | None = None):


        self._text.setText("")
        self._text_tier = "dim"
        self._text.setStyleSheet(self._text_ss(_th.FG_DIM))
        self._dot.set_idle()
        self._apply_dots(None, history_lines)
        self._refresh_popup()

    def set_active(self, tasks: list, pending_labels: list | None = None,
                   history_lines: list | None = None):
        if not tasks:
            self.set_clean()
            return
        self._text.setText("  ·  ".join(tasks))
        self._text_tier = "label"
        self._text.setStyleSheet(self._text_ss(_th.FG_LABEL))
        self._dot.set_active()
        self._apply_dots(pending_labels, history_lines)
        self._refresh_popup()

    def set_finished(self, text="Done", history_lines: list | None = None,
                     has_fail: bool = False):


        self._text.setText(text)
        if has_fail:
            self._text_tier = "red"
            self._text.setStyleSheet(self._text_ss(_th.RED_STATUS))
            self._dot.set_state("failed")
        else:
            self._text_tier = "green"
            self._text.setStyleSheet(self._text_ss(_th.GREEN_STATUS))
            self._dot.set_state("done")
        # ••• tetap tampil kalau ada history — biar bisa dilihat setelah selesai
        self._apply_dots(None, history_lines)
        self._refresh_popup()

    def set_failed(self, text="Failed", history_lines: list | None = None):
        # CATATAN: bar TIDAK auto-clean lewat QTimer. Manager yang trigger.
        self._text.setText(text)
        self._text_tier = "red"
        self._text.setStyleSheet(self._text_ss(_th.RED_STATUS))
        self._dot.set_state("failed")
        self._apply_dots(None, history_lines)
        self._refresh_popup()
