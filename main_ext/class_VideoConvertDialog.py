# SPDX-License-Identifier: MPL-2.0
import os
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QWidget,
    QLineEdit, QSlider, QProgressBar, QFileDialog
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QIntValidator

from main_ext.class_ThemeUi import theme as _th


def _card_style():
    _hex = _th.BG2.lstrip("#")
    _r, _g, _b = int(_hex[0:2], 16), int(_hex[2:4], 16), int(_hex[4:6], 16)
    return f"""
        QWidget#dlg_card {{
            background: {_th.panel_rgba(_r, _g, _b, 170)};
            border: 1px solid {_th.border_color(0.22)};
            border-radius: 14px;
        }}
    """


def _header_style():
    return f"color:{_th.FG}; font-size:13px; font-weight:600; background:transparent; border:none;"


def _label_style():
    return f"color:{_th.FG_BLUE_MUTED}; font-size:11px; font-weight:600; background:transparent; border:none;"


def _btn_style(variant="neutral"):
    if variant == "primary":
        p = dict(bg=_th.accent(0.15), br=_th.accent(0.30), fg=_th.FG_BLUE, w=600,
                 bg_h=_th.accent(0.27), br_h=_th.accent(0.52), fg_h=_th.FG_BLUE_LIGHT,
                 bg_p=_th.accent(0.40))
    else:
        p = dict(bg=_th.border_color(0.05), br=_th.border_color(0.08), fg=_th.FG_MUTED, w=400,
                 bg_h=_th.border_color(0.09), br_h=_th.border_color(0.14), fg_h=_th.FG_BLUE_MUTED,
                 bg_p=_th.border_color(0.13))
    return f"""
        QPushButton {{ background:{p['bg']}; border:1px solid {p['br']}; border-radius:7px;
            color:{p['fg']}; font-size:12px; font-weight:{p['w']}; padding:0 16px; height:30px; }}
        QPushButton:hover   {{ background:{p['bg_h']}; border-color:{p['br_h']}; color:{p['fg_h']}; }}
        QPushButton:pressed {{ background:{p['bg_p']}; }}
        QPushButton:disabled {{ background:{_th.border_color(0.03)}; border-color:{_th.border_color(0.04)};
                                color:{_th.FG_DISABLED2}; }}
    """


def _toggle_active():
    return (f"QPushButton {{ background:{_th.accent(0.18)}; border:1px solid {_th.accent(0.40)};"
            f" border-radius:7px; color:{_th.FG_BLUE}; font-size:12px; font-weight:600;"
            f" padding:0 14px; height:28px; }}")


def _toggle_inactive():
    return (f"QPushButton {{ background:transparent; border:1px solid {_th.border_color(0.08)};"
            f" border-radius:7px; color:{_th.FG_DESC}; font-size:12px; padding:0 14px; height:28px; }}"
            f" QPushButton:hover {{ background:{_th.border_color(0.05)}; color:{_th.FG_BLUE_MUTED}; }}")


def _input_style():
    return (f"QLineEdit {{ background:{_th.BG_INPUT}; border:1px solid {_th.border_color(0.10)};"
            f" border-radius:6px; color:{_th.FG}; font-size:12px; padding:4px 8px; }}"
            f" QLineEdit:focus {{ border-color:{_th.accent(0.55)}; }}")


def _slider_style():
    return (f"QSlider::groove:horizontal {{ height:4px; background:{_th.border_color(0.12)}; border-radius:2px; }}"
            f" QSlider::handle:horizontal {{ width:12px; height:12px; margin:-5px 0;"
            f" background:{_th.accent_hex}; border-radius:6px; }}"
            f" QSlider::sub-page:horizontal {{ background:{_th.accent(0.55)}; border-radius:2px; }}")


class VideoConvertDialog(QDialog):
    def __init__(self, parent, src):
        super().__init__(parent)
        self._src = src
        self._mode = "fps"          # "all" | "fps"
        self._fmt = "png"           # "png" | "jpg"
        self._extractor = None

        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setModal(True)
        self.setFixedWidth(420)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)
        card = QWidget(); card.setObjectName("dlg_card"); card.setStyleSheet(_card_style())
        outer.addWidget(card)
        lay = QVBoxLayout(card)
        lay.setContentsMargins(20, 18, 20, 18)
        lay.setSpacing(14)

        title = QLabel("Convert to Images")
        title.setStyleSheet(_header_style())
        lay.addWidget(title)

        # ── MODE ──
        lay.addWidget(self._mk_label("Frames"))
        mode_row = QHBoxLayout(); mode_row.setSpacing(6)
        self._btn_all = QPushButton("All frames")
        self._btn_fps = QPushButton("Target FPS")
        for b in (self._btn_all, self._btn_fps):
            b.setCursor(Qt.PointingHandCursor)
        self._btn_all.clicked.connect(lambda: self._set_mode("all"))
        self._btn_fps.clicked.connect(lambda: self._set_mode("fps"))
        mode_row.addWidget(self._btn_all)
        mode_row.addWidget(self._btn_fps)

        self._fps_input = QLineEdit("30")
        self._fps_input.setValidator(QIntValidator(1, 240, self))
        self._fps_input.setFixedWidth(56)
        self._fps_input.setStyleSheet(_input_style())
        self._fps_input.setAlignment(Qt.AlignCenter)
        mode_row.addWidget(self._fps_input)
        self._fps_unit = QLabel("fps")
        self._fps_unit.setStyleSheet(f"color:{_th.FG_MUTED}; font-size:11px; background:transparent; border:none;")
        mode_row.addWidget(self._fps_unit)
        mode_row.addStretch()
        lay.addLayout(mode_row)

        # ── FORMAT ──
        lay.addWidget(self._mk_label("Format"))
        fmt_row = QHBoxLayout(); fmt_row.setSpacing(6)
        self._btn_png = QPushButton("PNG")
        self._btn_jpg = QPushButton("JPG")
        for b in (self._btn_png, self._btn_jpg):
            b.setCursor(Qt.PointingHandCursor)
        self._btn_png.clicked.connect(lambda: self._set_fmt("png"))
        self._btn_jpg.clicked.connect(lambda: self._set_fmt("jpg"))
        fmt_row.addWidget(self._btn_png)
        fmt_row.addWidget(self._btn_jpg)
        fmt_row.addStretch()

        self._q_lbl = QLabel("Quality 92")
        self._q_lbl.setStyleSheet(f"color:{_th.FG_MUTED}; font-size:11px; background:transparent; border:none;")
        self._q_sld = QSlider(Qt.Horizontal)
        self._q_sld.setRange(50, 100)
        self._q_sld.setValue(92)
        self._q_sld.setFixedWidth(100)
        self._q_sld.setStyleSheet(_slider_style())
        self._q_sld.valueChanged.connect(lambda v: self._q_lbl.setText(f"Quality {v}"))
        fmt_row.addWidget(self._q_lbl)
        fmt_row.addWidget(self._q_sld)
        lay.addLayout(fmt_row)

        # ── OUTPUT FOLDER ──
        lay.addWidget(self._mk_label("Output folder"))
        out_row = QHBoxLayout(); out_row.setSpacing(6)
        base = os.path.splitext(os.path.basename(src))[0]
        default_out = os.path.join(os.path.dirname(src), f"{base}_frames")
        self._out_input = QLineEdit(default_out)
        self._out_input.setStyleSheet(_input_style())
        out_row.addWidget(self._out_input, 1)
        btn_browse = QPushButton("Browse")
        btn_browse.setStyleSheet(_btn_style("neutral"))
        btn_browse.setFixedHeight(30)
        btn_browse.setCursor(Qt.PointingHandCursor)
        btn_browse.clicked.connect(self._browse)
        out_row.addWidget(btn_browse)
        lay.addLayout(out_row)

        # ── PROGRESS (hidden sampai convert jalan) ──
        self._prog_lbl = QLabel("")
        self._prog_lbl.setStyleSheet(f"color:{_th.FG_BLUE_MUTED}; font-size:11px; background:transparent; border:none;")
        self._prog_lbl.setVisible(False)
        lay.addWidget(self._prog_lbl)
        self._prog = QProgressBar()
        self._prog.setTextVisible(False)
        self._prog.setFixedHeight(6)
        self._prog.setStyleSheet(
            f"QProgressBar {{ background:{_th.border_color(0.10)}; border:none; border-radius:3px; }}"
            f" QProgressBar::chunk {{ background:{_th.accent_hex}; border-radius:3px; }}")
        self._prog.setVisible(False)
        lay.addWidget(self._prog)

        # ── FOOTER ──
        foot = QHBoxLayout(); foot.setSpacing(8); foot.addStretch()
        self._btn_cancel = QPushButton("Cancel")
        self._btn_cancel.setStyleSheet(_btn_style("neutral"))
        self._btn_cancel.setFixedHeight(30)
        self._btn_cancel.setCursor(Qt.PointingHandCursor)
        self._btn_cancel.clicked.connect(self._on_cancel)
        self._btn_ok = QPushButton("Convert")
        self._btn_ok.setStyleSheet(_btn_style("primary"))
        self._btn_ok.setFixedHeight(30)
        self._btn_ok.setCursor(Qt.PointingHandCursor)
        self._btn_ok.clicked.connect(self._start_convert)
        foot.addWidget(self._btn_cancel)
        foot.addWidget(self._btn_ok)
        lay.addLayout(foot)

        self._set_mode("fps")
        self._set_fmt("png")
        self.adjustSize()

    # ── helpers ──
    def _mk_label(self, txt):
        l = QLabel(txt); l.setStyleSheet(_label_style()); return l

    def _set_mode(self, mode):
        self._mode = mode
        self._btn_all.setStyleSheet(_toggle_active() if mode == "all" else _toggle_inactive())
        self._btn_fps.setStyleSheet(_toggle_active() if mode == "fps" else _toggle_inactive())
        on = mode == "fps"
        self._fps_input.setEnabled(on)
        self._fps_input.setVisible(on)
        self._fps_unit.setVisible(on)

    def _set_fmt(self, fmt):
        self._fmt = fmt
        self._btn_png.setStyleSheet(_toggle_active() if fmt == "png" else _toggle_inactive())
        self._btn_jpg.setStyleSheet(_toggle_active() if fmt == "jpg" else _toggle_inactive())
        on = fmt == "jpg"
        self._q_lbl.setVisible(on)
        self._q_sld.setVisible(on)

    def _browse(self):
        start = self._out_input.text() or os.path.dirname(self._src)
        d = QFileDialog.getExistingDirectory(self, "Select output folder", start)
        if d:
            self._out_input.setText(d)

    # ── convert flow ──
    def _start_convert(self):
        out_dir = self._out_input.text().strip()
        if not out_dir:
            return
        try:
            fps = int(self._fps_input.text()) if self._mode == "fps" else 30
        except ValueError:
            fps = 30

        # masuk mode progress: kunci opsi, ubah tombol
        self._set_inputs_enabled(False)
        self._btn_ok.setVisible(False)
        self._btn_cancel.setText("Stop")
        self._prog_lbl.setVisible(True)
        self._prog_lbl.setText("Starting…")
        self._prog.setVisible(True)
        self._prog.setRange(0, 0)   # indeterminate sampai tahu durasi
        self.adjustSize()

        from main_ext.class_FrameExtractor import FrameExtractor
        self._extractor = FrameExtractor(
            self._src, out_dir, mode=self._mode, target_fps=fps,
            fmt=self._fmt, jpg_quality=self._q_sld.value(),
            on_progress=self._on_progress, on_done=self._on_done, parent=self,
        )
        self._extractor.start()

    def _on_progress(self, saved, total_ms, cur_ms):
        self._prog_lbl.setText(f"{saved} frames saved…")

    def _on_done(self, ok, saved, msg):
        self._prog.setRange(0, 1)
        self._prog.setValue(1)
        if ok:
            self._prog_lbl.setText(f"Done — {saved} frames")
            from PySide6.QtCore import QTimer
            QTimer.singleShot(700, self.accept)
        else:
            self._prog_lbl.setText(f"Failed: {msg}")
            self._btn_cancel.setText("Close")
            self._extractor = None

    def _on_cancel(self):
        if self._extractor is not None:
            # sedang konversi → stop extractor lalu tutup
            try:
                self._extractor.cancel()
            except Exception:
                pass
            self._extractor = None
        self.reject()

    def _set_inputs_enabled(self, on):
        for w in (self._btn_all, self._btn_fps, self._fps_input, self._btn_png,
                  self._btn_jpg, self._q_sld, self._out_input):
            w.setEnabled(on)

    def showEvent(self, e):
        super().showEvent(e)
        p = self.parent()
        if p:
            try:
                g = p.window().geometry()
                self.move(g.x() + (g.width() - self.width()) // 2,
                          g.y() + (g.height() - self.height()) // 2)
            except Exception:
                pass

    # ── factory ──
    @staticmethod
    def open(parent, src):
        from app_ext.class_BlurOverlay import BlurOverlay as _BlurOverlay, install_window_fade
        dlg = VideoConvertDialog(parent, src)
        ov = _BlurOverlay(parent.window()) if parent else None
        if ov:
            dlg._overlay = ov
        install_window_fade(dlg)
        dlg.exec()
        if ov:
            try:
                ov._final_close()
            except Exception:
                ov.close_overlay()
