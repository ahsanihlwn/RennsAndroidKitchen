# SPDX-License-Identifier: MPL-2.0
import os
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QApplication
from PySide6.QtCore    import Qt, QTimer, QRectF
from PySide6.QtGui     import QPainter, QColor, QPen, QPixmap, QPainterPath
from core.ui_lang import t

try:
    from icons.handler import icon as _icon
    _ICON_PATH = _icon("ic_launcher")
except Exception:
    _ICON_PATH = None


# Spinner
class _Spinner(QWidget):
    def __init__(self, size=38, accent: QColor = None, thickness=6, parent=None):
        super().__init__(parent)
        self._sz        = size
        self._angle     = 0
        self._accent    = accent or QColor(77, 142, 240)
        self._thickness = thickness
        self.setFixedSize(size, size)
        self._timer = QTimer(self)
        self._timer.setInterval(14)
        self._timer.timeout.connect(self._tick)
        self._timer.start()

    def set_accent(self, accent: QColor):
        self._accent = accent
        self.update()

    def _tick(self):
        self._angle = (self._angle - 5) % 360
        self.update()

    def paintEvent(self, _ev):
        p   = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)


        pad = self._thickness
        r   = QRectF(pad, pad, self._sz - pad * 2, self._sz - pad * 2)

        pen = QPen(QColor(self._accent.red(), self._accent.green(),
                          self._accent.blue(), 46))
        # setWidthF (bukan setWidth): dukung thickness pecahan (mis. 2.5 di bar).
        # setWidth(int) bakal truncate 2.5→2. Int window (5/6) tetap sama.
        pen.setWidthF(self._thickness); pen.setCapStyle(Qt.RoundCap)
        p.setPen(pen); p.drawEllipse(r)

        pen2 = QPen(self._accent)
        pen2.setWidthF(self._thickness); pen2.setCapStyle(Qt.RoundCap)
        p.setPen(pen2)
        p.drawArc(r, self._angle * 16, 105 * 16)
        p.end()

    def stop(self): self._timer.stop()


# Card widget — plain rounded bg + border (no shimmer)
class _Card(QWidget):
    def __init__(self, bg: QColor, border: QColor, accent: QColor,
                 radius: float = 14.0, parent=None):
        super().__init__(parent)
        self._bg      = bg
        self._border  = border
        self._accent  = accent
        self._radius  = radius
        self.setAttribute(Qt.WA_TranslucentBackground)

    def set_colors(self, bg: QColor, border: QColor, accent: QColor):
        self._bg = bg
        self._border = border
        self._accent = accent
        self.update()

    def paintEvent(self, _ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(0.5, 0.5, self.width() - 1, self.height() - 1)

        # card bg
        path = QPainterPath()
        path.addRoundedRect(r, self._radius, self._radius)
        p.fillPath(path, self._bg)

        # border
        pen = QPen(self._border)
        pen.setWidth(1)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(r, self._radius, self._radius)
        p.end()

    def stop(self):
        pass


# LoadingWindow
class LoadingWindow(QWidget):
    def __init__(self, project_path="", parent=None):
        super().__init__(parent)
        from main_ext.class_ThemeUi import theme as _th
        _th.load()
        self._theme = _th

        self.setWindowFlags(Qt.Window | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFixedSize(320, 196)

        self._project_name = os.path.basename(project_path) if project_path else ""
        self._status_text  = t("loading_opening_project")

        accent_qc = QColor(_th.accent_hex)

        # parse border color
        br, bg, bb = _th._palette["_border_rgb"]
        border_qc  = QColor(br, bg, bb, int(0.14 * 255))
        bg_qc      = QColor(_th.BG2)

        # shadow room
        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 10, 10, 10)

        # card — draws bg + border
        self._card = _Card(bg_qc, border_qc, accent_qc, radius=14.0)
        outer.addWidget(self._card)

        root = QVBoxLayout(self._card)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # header
        self._hdr = QWidget()
        self._hdr.setFixedHeight(44)
        hl = QHBoxLayout(self._hdr)
        hl.setContentsMargins(16, 0, 16, 0)
        hl.setSpacing(8)

        if _ICON_PATH and os.path.isfile(_ICON_PATH):
            ic = QLabel()
            pix = QPixmap(_ICON_PATH).scaled(18, 18, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            ic.setPixmap(pix)
            ic.setFixedSize(18, 18)
            ic.setStyleSheet("background:transparent; border:none;")
            hl.addWidget(ic)

        self._app_label = QLabel("Renns Android Kitchen")
        hl.addWidget(self._app_label)
        hl.addStretch()
        root.addWidget(self._hdr)

        # body
        body = QVBoxLayout()
        body.setContentsMargins(24, 20, 24, 20)
        body.setSpacing(0)
        body.setAlignment(Qt.AlignCenter)

        spin_row = QHBoxLayout()
        spin_row.setAlignment(Qt.AlignCenter)
        # spinner putih — gaya sama dgn tombol login (Sign in), cuma lebih gede.
        self._spinner = _Spinner(size=38, accent=accent_qc, thickness=7)
        spin_row.addWidget(self._spinner)
        body.addLayout(spin_row)
        body.addSpacing(14)

        self._project_label = None
        if self._project_name:
            self._project_label = QLabel(self._project_name)
            self._project_label.setAlignment(Qt.AlignCenter)
            body.addWidget(self._project_label)
            body.addSpacing(4)

        self._status_label = QLabel(self._status_text)
        self._status_label.setAlignment(Qt.AlignCenter)
        body.addWidget(self._status_label)

        root.addLayout(body)

        self.apply_theme()
        self._center()

        try:
            _th.on_change(self.apply_theme)
        except Exception:
            pass

    def apply_theme(self):
        _th = self._theme
        accent_qc = QColor(_th.accent_hex)
        br, bg, bb = _th._palette["_border_rgb"]
        border_qc = QColor(br, bg, bb, int(0.14 * 255))

        self._card.set_colors(QColor(_th.BG2), border_qc, accent_qc)
        self._spinner.set_accent(accent_qc)

        self._hdr.setStyleSheet(f"""
            background: {_th.BG_DARK};
            border-bottom: 1px solid {_th.border_color(0.08)};
            border-top-left-radius: 14px;
            border-top-right-radius: 14px;
        """)
        self._app_label.setStyleSheet(f"""
            font-size: 12px; font-weight: 600;
            color: {_th.FG};
            background: transparent; border: none;
        """)
        if self._project_label:
            self._project_label.setStyleSheet(f"""
                font-size: 12px; font-weight: 600;
                color: {_th.FG_BRIGHT};
                background: transparent; border: none;
            """)
        self._status_label.setStyleSheet(f"""
            font-size: 11px; color: {_th.FG_MUTED};
            background: transparent; border: none;
        """)
        self.setStyleSheet(f"""
            QWidget {{
                background: transparent;
                color: {_th.FG};
                font-family: 'Poppins', 'Inter', 'Segoe UI', sans-serif;
            }}
        """)
        self.update()

    def set_status(self, text: str):
        self._status_text = text
        self._status_label.setText(text)

    def _center(self):
        screen = QApplication.primaryScreen()
        if screen:
            g = screen.availableGeometry()
            self.move(g.x() + (g.width()  - self.width())  // 2,
                      g.y() + (g.height() - self.height()) // 2)

    def paintEvent(self, _ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        sr, sg, sb = self._theme._palette.get("_shadow_rgb", (0, 0, 0))
        for i in range(1, 6):
            p.setBrush(QColor(sr, sg, sb, 14 - i * 2))
            p.drawRoundedRect(self.rect().adjusted(i, i, -i, -i), 16, 16)
        p.end()

    def closeEvent(self, event):
        # Kalau _block_close aktif (Main belum ready), hide dulu — jangan close.
        # Nanti pas Main siap, caller yang putuskan: tutup beneran atau show editor.
        if getattr(self, "_block_close", False):
            event.ignore()
            self.hide()
            if callable(getattr(self, "_on_hide_requested", None)):
                self._on_hide_requested()
            return
        try:
            self._theme.remove_listener(self.apply_theme)
        except Exception:
            pass
        self._spinner.stop()
        super().closeEvent(event)
