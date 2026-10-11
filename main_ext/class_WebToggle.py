# SPDX-License-Identifier: MPL-2.0
from PySide6.QtWidgets import QWidget
from PySide6.QtCore    import (
    Qt, Signal, QSize, QPropertyAnimation,
    QEasingCurve, Property, QRect, QEvent
)
from PySide6.QtGui import (
    QPainter, QColor, QPainterPath,
    QMouseEvent
)
from main_ext.class_ThemeUi import theme as _th


def _accent_color(alpha: int = 230) -> QColor:
    r, g, b = _th._palette["_accent_rgb"]
    return QColor(r, g, b, alpha)


def _neutral_color(alpha: int = 235) -> QColor:

    c = QColor(_th.FG_MUTED)
    c.setAlpha(alpha)
    return c


def _off_color(alpha: int = 30) -> QColor:

    r, g, b = _th._palette["_border_rgb"]
    return QColor(r, g, b, alpha)


class WebToggle(QWidget):


    clicked = Signal()

    # Colors yang fixed (KNOB & shadow). BG_ON & BG_OFF dihitung per-paint.
    _KNOB    = QColor(255, 255, 255)
    _KNOB_SH = QColor(0, 0, 0, 80)

    def __init__(self, checked: bool = False, size: QSize = QSize(42, 24),
                 neutral: bool = False):


        super().__init__()
        self.setProperty("buttonPressFeedback", True)
        self._checked  = checked
        self._neutral  = neutral
        self._w        = size.width()
        self._h        = size.height()

        # _pos: 0.0 = off, 1.0 = on  (di-animasikan)
        self._pos: float = 1.0 if checked else 0.0

        self.setFixedSize(size)
        self.setCursor(Qt.PointingHandCursor)
        self.setAttribute(Qt.WA_OpaquePaintEvent, False)
        self.setStyleSheet("background: transparent;")

        # animasi geser knob
        self._anim = QPropertyAnimation(self, b"knob_pos", self)
        self._anim.setDuration(200)
        self._anim.setEasingCurve(QEasingCurve.Type.InOutCubic)

    # Qt Property untuk animasi
    def _get_pos(self) -> float:
        return self._pos

    def _set_pos(self, v: float):
        self._pos = v
        self.update()

    knob_pos = Property(float, _get_pos, _set_pos)

    # paint
    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        if not self.isEnabled():
            p.setOpacity(0.35)

        w, h = self._w, self._h
        r    = h / 2          # track corner radius
        pad  = max(2, h // 10)
        kd   = h - pad * 2    # knob diameter
        travel = w - kd - pad * 2

        # track background (lerp antara off dan on)
        # Theme-aware: ON = current accent color, OFF = light overlay
        t       = self._pos
        bg_on   = _neutral_color(200) if self._neutral else _accent_color(230)
        bg_off  = _off_color(30)
        bg  = QColor(
            int(bg_off.red()   + t * (bg_on.red()   - bg_off.red())),
            int(bg_off.green() + t * (bg_on.green() - bg_off.green())),
            int(bg_off.blue()  + t * (bg_on.blue()  - bg_off.blue())),
            int(bg_off.alpha() + t * (bg_on.alpha() - bg_off.alpha())),
        )

        track_path = QPainterPath()
        track_path.addRoundedRect(0, 0, w, h, r, r)
        p.fillPath(track_path, bg)

        # knob
        kx = pad + self._pos * travel
        ky = pad

        # shadow (offset 1px ke bawah)
        shadow_path = QPainterPath()
        shadow_path.addEllipse(kx + 0.5, ky + 1.5, kd, kd)
        p.fillPath(shadow_path, self._KNOB_SH)

        knob_path = QPainterPath()
        knob_path.addEllipse(kx, ky, kd, kd)
        p.fillPath(knob_path, self._KNOB)

        p.end()

    # interaction
    def changeEvent(self, e):
        if e.type() == QEvent.Type.EnabledChange:
            self.setCursor(Qt.PointingHandCursor if self.isEnabled() else Qt.ArrowCursor)
            self.update()
        super().changeEvent(e)

    def mousePressEvent(self, e: QMouseEvent):
        if not self.isEnabled():
            return
        if e.button() == Qt.MouseButton.LeftButton:
            self._toggle()
        super().mousePressEvent(e)

    def _toggle(self):
        self._checked = not self._checked
        self._animate_to(1.0 if self._checked else 0.0)
        self.clicked.emit()

    def _animate_to(self, target: float):
        if not _th.animations_enabled:
            self._anim.stop()
            self._set_pos(target)
            return
        self._anim.stop()
        self._anim.setStartValue(self._pos)
        self._anim.setEndValue(target)
        self._anim.start()

    # public API
    def isChecked(self) -> bool:
        return self._checked

    def setChecked(self, val: bool):
        if val == self._checked:
            return
        self._checked = val
        self._animate_to(1.0 if val else 0.0)

    def blockSignals(self, block: bool) -> bool:
        return super().blockSignals(block)
