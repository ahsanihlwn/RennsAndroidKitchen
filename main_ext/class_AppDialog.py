# SPDX-License-Identifier: MPL-2.0
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QWidget, QPlainTextEdit, QApplication
)
from PySide6.QtCore import Qt, QPropertyAnimation, QEasingCurve, QTimer, QRect, QPoint
from PySide6.QtGui import QColor, QFontDatabase
from main_ext.class_ThemeUi import theme as _th
from app_ext.class_BlurOverlay import BlurOverlay, BlurCard


def _dialog_bounds(parent):
    owner = parent.window() if parent is not None else None
    while isinstance(owner, QDialog) and owner.parentWidget() is not None:
        owner = owner.parentWidget().window()
    screen = owner.screen() if owner is not None else QApplication.primaryScreen()
    bounds = screen.availableGeometry()
    if owner is not None:
        rect = QRect(owner.mapToGlobal(QPoint()), owner.size())
        intersection = bounds.intersected(rect)
        if not intersection.isEmpty():
            bounds = intersection
    return bounds


_BTN = """
    QPushButton {{
        background: {bg};
        border: 1px solid {br};
        border-radius: 6px;
        color: {fg};
        font-size: 12px;
        font-weight: {w};
        padding: 0 14px;
        height: 30px;
    }}
    QPushButton:hover {{ background: {bg_h}; border-color: {br_h}; color: {fg_h}; }}
    QPushButton:pressed {{ background: {bg_p}; }}
"""

def _style(v):
    if v == "danger":
        return _BTN.format(
            bg=_th.red(0.18),  br=_th.red(0.38),  fg=_th.RED_LIGHT, w=600,
            bg_h=_th.red(0.32), br_h=_th.red(0.60), fg_h=_th.RED_LIGHTER,
            bg_p=_th.red(0.48))
    if v == "primary":
        return _BTN.format(
            bg=_th.accent(0.18), br=_th.accent(0.38), fg=_th.FG_BLUE, w=600,
            bg_h=_th.accent(0.32), br_h=_th.accent(0.60), fg_h=_th.FG_BLUE_LIGHT,
            bg_p=_th.accent(0.48))
    return _BTN.format(
        bg=_th.border_color(0.06), br=_th.border_color(0.10), fg=_th.FG3, w=400,
        bg_h=_th.border_color(0.10), br_h=_th.border_color(0.18), fg_h=_th.FG_HOVER,
        bg_p=_th.border_color(0.14))


class _Dlg(QDialog):
    def __init__(self, parent, title, message, width=320):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.setFixedWidth(max(1, min(width, _dialog_bounds(parent).width()-24)))
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        # Main's background stylesheet is inherited by child dialogs. Keep
        # the native window transparent; only the rounded box/blur card paints
        # a surface, including at its corners during fade and resize.
        self.setStyleSheet("QDialog { background:transparent; border:none; }")
        self._default_btn = None   # tombol "OK/Yes" yang di-trigger saat Enter ditekan

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        # Convert _th.BG2 (hex) → rgba dengan alpha
        # Box semi-transparan — dapet "kaca" feel dari BlurOverlay di belakangnya
        _blur_tint = QColor(_th.BG2)
        _blur_tint.setAlpha(72)
        self._blur_surface = BlurCard(
            self,
            radius=18.0,
            tint=_blur_tint,
            border=QColor(0, 0, 0, 0),
            corner=14.0,
        )
        self._blur_surface.setAttribute(Qt.WA_TransparentForMouseEvents)

        _hex = _th.BG2.lstrip("#")
        _r, _g, _b = int(_hex[0:2], 16), int(_hex[2:4], 16), int(_hex[4:6], 16)
        self._box = QWidget()
        self._box.setObjectName("dlg_box")
        self._box.setStyleSheet(f"""
            QWidget#dlg_box {{
                background: {_th.panel_rgba(_r, _g, _b, 170)};
                border: 1px solid {_th.border_color(0.22)};
                border-radius: 14px;
            }}
        """)
        outer.addWidget(self._box)

        lay = QVBoxLayout(self._box)
        lay.setContentsMargins(20, 18, 20, 16)
        lay.setSpacing(14)

        lbl = QLabel(message)
        lbl.setStyleSheet(f"color: {_th.FG}; font-size: 13px; border: none; background: transparent;")
        lbl.setWordWrap(True)
        lbl.setTextFormat(Qt.RichText)
        lay.addWidget(lbl)

        self._row = QHBoxLayout()
        self._row.setSpacing(8)
        self._row.addStretch()
        lay.addLayout(self._row)

        self._chosen = None
        self.adjustSize()

        # Fade in/out via windowOpacity
        self._fade_anim = None
        self.setWindowOpacity(0.0)

    def keyPressEvent(self, event):

        key = event.key()
        if key in (Qt.Key_Return, Qt.Key_Enter):
            if self._default_btn is not None:
                self._default_btn.click()
            else:
                self.done(QDialog.Accepted)
            return
        if key == Qt.Key_Escape:
            self.done(QDialog.Rejected)
            return
        super().keyPressEvent(event)

    def adjustSize(self):
        # Rich-text word wrapping must use the final Qt font/style before the
        # dialog chooses its height. Deferred polish can otherwise clip the
        # second paragraph (especially after loading application fonts).
        self.ensurePolished()
        for child in self._box.findChildren(QWidget):
            child.ensurePolished()
        self._box.ensurePolished()
        self._box.layout().invalidate()
        self.layout().invalidate()
        super().adjustSize()
        height = self.layout().totalHeightForWidth(self.width())
        if height >= 0:
            self.resize(self.width(), max(self.minimumHeight(), height))

    def showEvent(self, e):
        super().showEvent(e)
        self.adjustSize()
        self._center_on_owner()
        self._sync_blur_surface()
        ov = getattr(self, "_overlay", None)
        if ov is not None and hasattr(ov, "attach_window_surface"):
            try:
                ov.attach_window_surface(self)
            except Exception:
                pass
        # Grab keyboard focus ke dialog (bukan ke button anak)
        # supaya keyPressEvent dialog yang handle Enter/Escape.
        self.setFocus()
        # Delay sedikit biar BlurOverlay dapet fade-in dulu, baru dialog masuk
        QTimer.singleShot(120, self._fade_in)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self.isVisible():
            self._center_on_owner()
        QTimer.singleShot(0, self._sync_blur_surface)

    def _center_on_owner(self):
        bounds = _dialog_bounds(self.parentWidget())
        pos = bounds.center() - self.rect().center()
        pos.setX(max(bounds.left(), min(pos.x(), bounds.right()-self.width()+1)))
        pos.setY(max(bounds.top(), min(pos.y(), bounds.bottom()-self.height()+1)))
        self.move(pos)

    def _sync_blur_surface(self):

        try:
            self._blur_surface.setGeometry(self._box.geometry())
            self._blur_surface.lower()
            self._box.raise_()
            self._blur_surface.show()
        except RuntimeError:
            pass

    def _fade_in(self):
        self._fade_anim = QPropertyAnimation(self, b"windowOpacity", self)
        self._fade_anim.setDuration(140)
        self._fade_anim.setStartValue(0.0)
        self._fade_anim.setEndValue(1.0)
        self._fade_anim.setEasingCurve(QEasingCurve.OutCubic)
        self._fade_anim.start()

    def done(self, result):
        # Override done untuk fade-out sebelum close
        if getattr(self, "_closing", False):
            super().done(result)
            return
        self._closing = True
        # Trigger overlay fade-out bareng — parallel close
        ov = getattr(self, "_overlay", None)
        if ov is not None and hasattr(ov, "close_overlay"):
            try: ov.close_overlay()
            except Exception: pass
            self._overlay = None
        self._fade_anim = QPropertyAnimation(self, b"windowOpacity", self)
        self._fade_anim.setDuration(120)
        self._fade_anim.setStartValue(self.windowOpacity())
        self._fade_anim.setEndValue(0.0)
        self._fade_anim.setEasingCurve(QEasingCurve.InCubic)
        self._fade_anim.finished.connect(lambda: super(_Dlg, self).done(result))
        self._fade_anim.start()

    def _btn(self, label, variant, result_code, w=None, *, close_on_click=True):
        b = QPushButton(label)
        b.setMinimumWidth(w or max(80, len(label) * 9))
        b.setFixedHeight(30)
        b.setCursor(Qt.PointingHandCursor)
        b.setStyleSheet(_style(variant))


        b.setFocusPolicy(Qt.NoFocus)

        if callable(result_code) and not isinstance(result_code, int):
            _pre = result_code
            if close_on_click:
                b.clicked.connect(lambda: (_pre(), self.done(QDialog.Accepted)))
            else:
                b.clicked.connect(_pre)
        else:
            code = result_code
            b.clicked.connect(lambda: self.done(code))

        # Tombol Accepted (primary/danger) jadi default → bisa di-Enter
        if result_code == QDialog.Accepted:
            self._default_btn = b

        self._row.addWidget(b)
        return b


class AppDialog:

    @staticmethod
    def _run(parent, d: "_Dlg", blur_kwargs: dict = None) -> int:
        from PySide6.QtWidgets import QApplication
        blur_target = parent.window() if parent else None


        ov_kwargs = dict(blur_kwargs) if blur_kwargs else {}
        if ov_kwargs.get("source_widget") == "__window__":
            # resolve sentinel → window target yang sebenarnya
            ov_kwargs["source_widget"] = blur_target
        ov = BlurOverlay(blur_target, **ov_kwargs) if blur_target else None
        if ov:
            d._overlay = ov
        result = d.exec()


        if ov:
            try:
                ov._final_close()
            except Exception:
                ov.close_overlay()
            QApplication.processEvents()
        return result

    @staticmethod
    def info(parent, title: str, message: str):
        from core.ui_lang import t
        d = _Dlg(parent, title, message)
        d._btn(t("dlg_btn_ok"), "primary", QDialog.Accepted)
        d.adjustSize()
        AppDialog._run(parent, d)

    @staticmethod
    def warning(parent, title: str, message: str):
        from core.ui_lang import t
        d = _Dlg(parent, title, message)
        d._btn(t("dlg_btn_ok"), "neutral", QDialog.Accepted)
        d.adjustSize()
        AppDialog._run(parent, d)

    @staticmethod
    def error(parent, title: str, message: str):
        import logging

        log_text = f"{title}\n\n{message}"
        logging.error("APPLICATION ERROR:\n%s", log_text)
        AppDialog.fatal_crash(parent, log_text)

    @staticmethod
    def fatal_crash(parent, log_text: str):

        from core.ui_lang import t
        from html import escape

        message = (
            f'<span style="color:{_th.RED_LIGHT}; font-size:18px; font-weight:600;">'
            f'Fatal Crash!</span><br><br>{escape(t("dlg_crash_message"))}'
        )
        d = _Dlg(parent, "Fatal Crash!", message, width=380)
        see_log = d._btn(
            t("dlg_btn_see_log"), "primary",
            lambda: AppDialog.error_log(d, log_text), close_on_click=False,
        )
        d._btn(t("dlg_btn_close"), "danger", QDialog.Rejected)
        d._default_btn = see_log
        d.adjustSize()
        try:
            AppDialog._run(parent, d)
        finally:
            d.deleteLater()

    @staticmethod
    def error_log(parent, log_text: str):

        from core.ui_lang import t

        available = _dialog_bounds(parent)
        width = max(1, min(680, available.width() - 48))
        log_height = min(330, max(80, available.height() - 180))
        d = _Dlg(parent, "Fatal Crash! — Log", "<b>Fatal Crash! — Log</b>", width=width)
        log = QPlainTextEdit(d._box)
        log.setReadOnly(True)
        log.setLineWrapMode(QPlainTextEdit.NoWrap)
        log.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        log.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        log.setFixedHeight(log_height)
        font = QFontDatabase.systemFont(QFontDatabase.FixedFont)
        font.setPointSize(10)
        log.setFont(font)
        family = font.family().replace("'", "")
        # Reuse the application's scrollbar palette for this separate dialog too.
        log.setStyleSheet(_th.app_stylesheet() + f"""
            QPlainTextEdit {{
                background-color: {_th.BG3}; color: {_th.FG};
                border: 1px solid {_th.border_color(0.14)};
                border-radius: 7px; padding: 8px;
                font-family: '{family}'; font-size: 10pt;
            }}
        """)
        log.setPlainText(log_text)
        body = d._box.layout()
        body.insertWidget(body.count() - 1, log)

        def copy_log():
            QApplication.clipboard().setText(log_text)
            copy_btn.setText(t("dlg_btn_copied"))
            QTimer.singleShot(1500, d, lambda: copy_btn.setText(t("dlg_btn_copy")))

        copy_btn = d._btn(t("dlg_btn_copy"), "primary", copy_log, close_on_click=False)
        d._btn(t("dlg_btn_close"), "neutral", QDialog.Accepted)
        d.adjustSize()
        try:
            AppDialog._run(parent, d)
        finally:
            d.deleteLater()

    @staticmethod
    def confirm(parent, title: str, message: str,
                yes_label=None, no_label=None) -> bool:
        from core.ui_lang import t
        d = _Dlg(parent, title, message)
        d._btn(no_label  or t("dlg_btn_no"),  "neutral", QDialog.Rejected)
        d._btn(yes_label or t("dlg_btn_yes"), "primary",  QDialog.Accepted)
        d.adjustSize()
        return AppDialog._run(parent, d) == QDialog.Accepted

    @staticmethod
    def confirm_close(parent, title: str, message: str,
                      close_label=None, cancel_label=None,
                      blur_kwargs: dict = None) -> bool:
        from core.ui_lang import t
        d = _Dlg(parent, title, message)
        d._btn(cancel_label or t("dlg_btn_cancel"), "neutral", QDialog.Rejected)
        d._btn(close_label  or t("dlg_btn_close"),  "danger",  QDialog.Accepted)
        d.adjustSize()
        return AppDialog._run(parent, d, blur_kwargs=blur_kwargs) == QDialog.Accepted

    @staticmethod
    def choose(parent, title: str, message: str,
               options: list, width=380):
        from core.ui_lang import t
        d = _Dlg(parent, title, message, width=width)
        for opt in options:
            def _pre(o=opt):
                d._chosen = o
            d._btn(opt, "primary", _pre, w=max(90, len(opt) * 9))
        d._btn(t("dlg_btn_cancel"), "neutral", QDialog.Rejected)
        d.adjustSize()
        return d._chosen if AppDialog._run(parent, d) == QDialog.Accepted else None
