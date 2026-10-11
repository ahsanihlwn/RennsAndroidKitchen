# SPDX-License-Identifier: MPL-2.0
import os
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QGraphicsView, QGraphicsScene, QGraphicsPixmapItem, QSizePolicy,
    QLineEdit, QFileDialog
)
from PySide6.QtCore import Qt, QRectF, QSize
from PySide6.QtGui import QPixmap, QPainter, QColor, QImage, QIcon, QIntValidator

import numpy as np
from icons.handler import icon
from main_ext.class_ThemeUi import theme as _th


def _qimage_to_ndarray(img: QImage):

    if img.format() != QImage.Format_ARGB32:
        img = img.convertToFormat(QImage.Format_ARGB32)
    w, h = img.width(), img.height()
    bpl = img.bytesPerLine()
    ptr = img.bits()                       # sip: writable buffer
    buf = np.frombuffer(ptr, dtype=np.uint8, count=bpl * h)
    arr = buf.reshape(h, bpl)[:, : w * 4].reshape(h, w, 4)
    return arr, img
from core.ui_lang import t

print("[MAIN STAGE] IMAGE_VIEWER")

RASTER_EXT = (".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp", ".ico", ".tiff", ".tif")
SVG_EXT = (".svg",)
IMAGE_EXT = RASTER_EXT + SVG_EXT

_MIN_SCALE = 0.05
_MAX_SCALE = 40.0


def _human_size(n: int) -> str:
    f = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if f < 1024.0 or unit == "GB":
            return f"{f:.0f} {unit}" if unit == "B" else f"{f:.1f} {unit}"
        f /= 1024.0
    return f"{f:.1f} GB"


class _ImageCanvas(QGraphicsView):


    def __init__(self, on_zoom_changed=None, on_tool_point=None, parent=None):
        super().__init__(parent)
        self._on_zoom_changed = on_zoom_changed
        self._on_tool_point = on_tool_point   # callback(scene_x, scene_y, phase)
        self._tool = None                      # None=pan, "erase", "magic"
        self._dragging_tool = False
        self._scale = 1.0
        self._checker_tile = self._make_checker_tile()
        self.setScene(QGraphicsScene(self))
        self.setRenderHints(
            QPainter.Antialiasing | QPainter.SmoothPixmapTransform
        )
        self.setDragMode(QGraphicsView.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.AnchorViewCenter)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.setFrameShape(QGraphicsView.NoFrame)
        self.setStyleSheet(f"""
            QGraphicsView {{ background: transparent; border: none; }}
            QScrollBar:vertical, QScrollBar:horizontal {{
                background: transparent; width: 6px; height: 6px; margin: 0;
            }}
            QScrollBar::handle:vertical, QScrollBar::handle:horizontal {{
                background: {_th.border_color(0.28)}; border-radius: 3px; min-height: 20px; min-width: 20px;
            }}
            QScrollBar::handle:vertical:hover, QScrollBar::handle:horizontal:hover {{
                background: {_th.border_color(0.45)};
            }}
            QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
            QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
        """)

    @staticmethod
    def _make_checker_tile(cell=12):


        c1 = QColor(60, 64, 72)
        c2 = QColor(44, 48, 56)
        tile = QPixmap(cell * 2, cell * 2)
        tile.fill(c2)
        p = QPainter(tile)
        p.fillRect(0, 0, cell, cell, c1)
        p.fillRect(cell, cell, cell, cell, c1)
        p.end()
        return tile

    # ── checkerboard background (viewport-space, zoom-independent) ──
    def drawBackground(self, painter, rect):
        vp = self.viewport().rect()
        painter.save()
        # Reset ke koordinat device supaya pola tidak terkena transform zoom.
        painter.resetTransform()
        painter.fillRect(vp, QColor(_th.BG2))
        painter.drawTiledPixmap(vp, self._checker_tile)
        painter.restore()

    def wheelEvent(self, e):
        factor = 1.25 if e.angleDelta().y() > 0 else 1 / 1.25
        self.zoom_by(factor)

    def zoom_by(self, factor):
        new_scale = self._scale * factor
        if new_scale < _MIN_SCALE or new_scale > _MAX_SCALE:
            return
        self._scale = new_scale
        self.scale(factor, factor)
        if self._on_zoom_changed:
            self._on_zoom_changed(self._scale)

    def set_scale_absolute(self, s):
        if s <= 0:
            return
        factor = s / self._scale
        self._scale = s
        self.scale(factor, factor)
        if self._on_zoom_changed:
            self._on_zoom_changed(self._scale)

    def reset_scale_flag(self, s):
        self._scale = s
        if self._on_zoom_changed:
            self._on_zoom_changed(self._scale)

    # ── tool mode ──
    def set_tool(self, tool):

        self._tool = tool
        if tool is None:
            self.setDragMode(QGraphicsView.ScrollHandDrag)
            self.viewport().setCursor(Qt.OpenHandCursor)
        else:
            self.setDragMode(QGraphicsView.NoDrag)
            self.viewport().setCursor(Qt.CrossCursor)

    def mousePressEvent(self, e):
        if self._tool and e.button() == Qt.LeftButton and self._on_tool_point:
            self._dragging_tool = True
            sp = self.mapToScene(e.position().toPoint())
            self._on_tool_point(sp.x(), sp.y(), "press")
            return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._dragging_tool and self._on_tool_point:
            sp = self.mapToScene(e.position().toPoint())
            self._on_tool_point(sp.x(), sp.y(), "move")
            return
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        if self._dragging_tool and e.button() == Qt.LeftButton:
            self._dragging_tool = False
            if self._on_tool_point:
                sp = self.mapToScene(e.position().toPoint())
                self._on_tool_point(sp.x(), sp.y(), "release")
            return
        super().mouseReleaseEvent(e)


class ImageViewer(QWidget):
    def set_read_only(self, ro=True):

        try:
            if hasattr(self, "_btn_editor") and self._btn_editor is not None:
                self._btn_editor.setChecked(False)
                self._btn_editor.setVisible(not ro)
            if ro and getattr(self, "_resize_bar", None) is not None:
                self._resize_bar.setVisible(False)
        except Exception:
            pass

    def __init__(self, filepath, loading_handler=None):
        super().__init__()
        self.unsaved = False
        self.filepath = filepath
        self._item = None
        self._nat_w = 0
        self._nat_h = 0
        self._is_svg = os.path.splitext(filepath)[1].lower() in SVG_EXT
        self._fitted_once = False
        self._cur_image = None          # QImage kerja (raster), dasar scale/crop/save
        self._resize_mode = "scale"     # "scale" | "crop"
        self._lock_aspect = True
        self._sync_guard = False        # cegah loop saat auto-isi W/H
        # editor tools
        self._active_tool = "pan"       # "pan" | "erase" | "magic"
        self._brush_size = 24
        self._threshold = 10            # persen (0-100)
        self._smooth = 50               # kekuatan Smooth Edge / defringe (1-100%)
        self._undo_stack = []           # list[QImage] snapshot sebelum aksi
        self._stroke_active = False     # True selama 1 stroke erase (1 undo entry)
        self._magic_busy = False        # True selama flood-fill di worker thread

        self.setFocusPolicy(Qt.StrongFocus)  # supaya Ctrl+Z (keyPressEvent) kena

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── toolbar ──
        bar = QWidget()
        bar.setStyleSheet(f"background:{_th.BG2}; border:none; border-bottom:1px solid {_th.border_color(0.08)};")
        bar_lay = QHBoxLayout(bar)
        bar_lay.setContentsMargins(8, 6, 8, 6)
        bar_lay.setSpacing(6)

        self._btn_out = self._tool_btn("zoom_out", "−", text_btn=True)
        self._btn_in = self._tool_btn("zoom_in", "+", text_btn=True)
        self._btn_fit = self._tool_btn("fit", t("iv_fit"), text_btn=True)
        self._btn_100 = self._tool_btn("reset", "1:1", text_btn=True)
        self._btn_out.clicked.connect(lambda: self._canvas.zoom_by(1 / 1.25))
        self._btn_in.clicked.connect(lambda: self._canvas.zoom_by(1.25))
        self._btn_fit.clicked.connect(self.fit_to_window)
        self._btn_100.clicked.connect(self.zoom_actual)

        bar_lay.addWidget(self._btn_out)
        bar_lay.addWidget(self._btn_in)
        bar_lay.addWidget(self._btn_fit)
        bar_lay.addWidget(self._btn_100)
        bar_lay.addStretch()

        self._zoom_lbl = QLabel("100%")
        self._zoom_lbl.setStyleSheet(f"color:{_th.FG_MUTED}; font-size:11px; background:transparent; border:none;")
        bar_lay.addWidget(self._zoom_lbl)

        # Tombol Editor — toggle panel editor (resize, dll). Raster only.
        self._btn_editor = None
        if not self._is_svg:
            sep_e = QWidget(); sep_e.setFixedWidth(1)
            sep_e.setStyleSheet(f"background:{_th.border_color(0.10)};")
            bar_lay.addWidget(sep_e)
            self._btn_editor = QPushButton(t("iv_editor"))
            self._btn_editor.setCheckable(True)
            self._btn_editor.setCursor(Qt.PointingHandCursor)
            self._btn_editor.setFixedHeight(26)
            _eic = QIcon(icon("edit"))
            if not _eic.isNull():
                self._btn_editor.setIcon(_eic)
                self._btn_editor.setIconSize(QSize(14, 14))
            self._btn_editor.toggled.connect(self._toggle_editor_panel)
            self._style_editor_btn()
            bar_lay.addWidget(self._btn_editor)

        root.addWidget(bar)

        # ── editor panel (raster only) — hidden sampai tombol Editor diklik ──
        self._resize_bar = None
        if not self._is_svg:
            self._resize_bar = self._build_editor_panel()
            self._resize_bar.setVisible(False)
            root.addWidget(self._resize_bar)

        # ── canvas ──
        self._canvas = _ImageCanvas(
            on_zoom_changed=self._update_zoom_label,
            on_tool_point=self._on_tool_point,
        )
        self._canvas.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        root.addWidget(self._canvas, 1)

        # ── info bar ──
        info = QWidget()
        info.setStyleSheet(f"background:{_th.BG2}; border:none; border-top:1px solid {_th.border_color(0.08)};")
        info_lay = QHBoxLayout(info)
        info_lay.setContentsMargins(12, 5, 12, 5)
        info_lay.setSpacing(14)

        self._info_lbl = QLabel("")
        self._info_lbl.setStyleSheet(f"color:{_th.FG_MUTED}; font-size:11px; background:transparent; border:none;")
        info_lay.addWidget(self._info_lbl)
        info_lay.addStretch()

        root.addWidget(info)

        self._load_image()

    # ── toolbar button helper ──
    def _tool_btn(self, icon_name, fallback_text, text_btn=False):
        b = QPushButton()
        b.setCursor(Qt.PointingHandCursor)
        b.setFixedHeight(26)
        _ic = QIcon(icon(icon_name))
        if not text_btn and not _ic.isNull():
            b.setIcon(_ic)
            b.setIconSize(QSize(15, 15))
            b.setFixedWidth(30)
        else:
            b.setText(fallback_text)
            b.setMinimumWidth(34)
        b.setStyleSheet(f"""
            QPushButton {{
                background:{_th.border_color(0.05)}; border:1px solid {_th.border_color(0.08)};
                border-radius:6px; color:{_th.FG_BLUE_MUTED}; font-size:12px; padding:0 8px;
            }}
            QPushButton:hover   {{ background:{_th.border_color(0.09)}; border-color:{_th.border_color(0.14)}; color:{_th.FG}; }}
            QPushButton:pressed {{ background:{_th.border_color(0.13)}; }}
        """)
        return b

    # ── editor panel toggle ──
    def _style_editor_btn(self):
        on = self._btn_editor.isChecked()
        self._btn_editor.setStyleSheet(f"""
            QPushButton {{ background:{_th.accent(0.18) if on else _th.border_color(0.05)};
                border:1px solid {_th.accent(0.40) if on else _th.border_color(0.08)};
                border-radius:6px; color:{_th.FG_BLUE if on else _th.FG_BLUE_MUTED};
                font-size:12px; padding:0 10px; }}
            QPushButton:hover {{ border-color:{_th.border_color(0.16)}; color:{_th.FG}; }}
        """)

    def _toggle_editor_panel(self, _checked):
        if self._resize_bar is not None:
            self._resize_bar.setVisible(self._btn_editor.isChecked())
        self._style_editor_btn()

    # editor panel (2 baris: tools + resize)
    def _build_editor_panel(self):
        panel = QWidget()
        panel.setStyleSheet(f"background:{_th.BG2}; border:none; border-bottom:1px solid {_th.border_color(0.08)};")
        v = QVBoxLayout(panel)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        v.addWidget(self._build_tools_bar())
        v.addWidget(self._build_resize_bar())
        return panel

    def _build_tools_bar(self):
        bar = QWidget()
        bar.setStyleSheet("background:transparent; border:none;")
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(8, 5, 8, 5)
        lay.setSpacing(6)

        # tool toggles (icon-only; Pan default aktif supaya bisa langsung digeser)
        self._btn_pan = self._icon_tool_btn("pan", t("iv_pan"), active=True)
        self._btn_erase = self._icon_tool_btn("eraser", t("iv_eraser"), active=False)
        self._btn_magic = self._icon_tool_btn("magic_erase", t("iv_magic"), active=False)
        self._btn_pan.clicked.connect(lambda: self.set_active_tool("pan"))
        self._btn_erase.clicked.connect(lambda: self.set_active_tool("erase"))
        self._btn_magic.clicked.connect(lambda: self.set_active_tool("magic"))
        lay.addWidget(self._btn_pan)
        lay.addWidget(self._btn_erase)
        lay.addWidget(self._btn_magic)

        sep = QWidget(); sep.setFixedWidth(1)
        sep.setStyleSheet(f"background:{_th.border_color(0.10)};")
        lay.addWidget(sep)

        # brush size (untuk eraser)
        lay.addWidget(self._dim_label(t("iv_brush")))
        from PySide6.QtWidgets import QSlider
        self._sld_brush = QSlider(Qt.Horizontal)
        self._sld_brush.setRange(2, 200)
        self._sld_brush.setValue(self._brush_size)
        self._sld_brush.setFixedWidth(90)
        self._sld_brush.setStyleSheet(self._slider_ss())
        self._sld_brush.valueChanged.connect(self._on_brush_changed)
        lay.addWidget(self._sld_brush)
        self._lbl_brush_val = QLabel(str(self._brush_size))
        self._lbl_brush_val.setStyleSheet(f"color:{_th.FG_MUTED}; font-size:11px; background:transparent; border:none;")
        self._lbl_brush_val.setFixedWidth(26)
        lay.addWidget(self._lbl_brush_val)

        sep2 = QWidget(); sep2.setFixedWidth(1)
        sep2.setStyleSheet(f"background:{_th.border_color(0.10)};")
        lay.addWidget(sep2)

        # threshold (untuk magic erase)
        lay.addWidget(self._dim_label(t("iv_threshold")))
        self._sld_thresh = QSlider(Qt.Horizontal)
        self._sld_thresh.setRange(0, 100)
        self._sld_thresh.setValue(self._threshold)
        self._sld_thresh.setFixedWidth(90)
        self._sld_thresh.setStyleSheet(self._slider_ss())
        self._sld_thresh.valueChanged.connect(self._on_thresh_changed)
        lay.addWidget(self._sld_thresh)
        self._lbl_thresh_val = QLabel(f"{self._threshold}%")
        self._lbl_thresh_val.setStyleSheet(f"color:{_th.FG_MUTED}; font-size:11px; background:transparent; border:none;")
        self._lbl_thresh_val.setFixedWidth(34)
        lay.addWidget(self._lbl_thresh_val)

        sep3 = QWidget(); sep3.setFixedWidth(1)
        sep3.setStyleSheet(f"background:{_th.border_color(0.10)};")
        lay.addWidget(sep3)

        # Smooth Edge (defringe) — operasi terpisah ke SELURUH gambar:
        # bersihin sisa warna seuprit di tepi area transparan.
        lay.addWidget(self._dim_label(t("iv_smooth_edge")))
        self._sld_smooth = QSlider(Qt.Horizontal)
        self._sld_smooth.setRange(1, 100)
        self._sld_smooth.setValue(self._smooth)
        self._sld_smooth.setFixedWidth(80)
        self._sld_smooth.setStyleSheet(self._slider_ss())
        self._sld_smooth.valueChanged.connect(self._on_smooth_changed)
        self._sld_smooth.setToolTip(t("iv_smooth_hint"))
        lay.addWidget(self._sld_smooth)
        self._lbl_smooth_val = QLabel(f"{self._smooth}%")
        self._lbl_smooth_val.setStyleSheet(f"color:{_th.FG_MUTED}; font-size:11px; background:transparent; border:none;")
        self._lbl_smooth_val.setFixedWidth(34)
        lay.addWidget(self._lbl_smooth_val)

        self._btn_smooth_apply = self._tool_btn("apply", t("iv_smooth_edge"), text_btn=True)
        self._btn_smooth_apply.setToolTip(t("iv_smooth_hint"))
        self._btn_smooth_apply.clicked.connect(self._apply_smooth_edge)
        lay.addWidget(self._btn_smooth_apply)

        lay.addStretch()

        # Undo (icon-only)
        self._btn_undo = self._icon_tool_btn("undo", t("iv_undo"), active=False, toggle=False)
        self._btn_undo.clicked.connect(self.undo)
        lay.addWidget(self._btn_undo)

        return bar

    def _set_tools_enabled(self, on):
        # Disable interaksi tool selama proses thread berjalan (cegah aksi dobel).
        for w in (self._btn_pan, self._btn_erase, self._btn_magic, self._sld_brush,
                  self._sld_thresh, self._sld_smooth, self._btn_smooth_apply, self._btn_undo):
            w.setEnabled(on)

    @staticmethod
    def _slider_ss():
        return f"""
            QSlider::groove:horizontal {{ height:4px; background:{_th.border_color(0.12)}; border-radius:2px; }}
            QSlider::handle:horizontal {{ width:12px; height:12px; margin:-5px 0;
                background:{_th.accent_hex}; border-radius:6px; }}
            QSlider::sub-page:horizontal {{ background:{_th.accent(0.55)}; border-radius:2px; }}
        """

    def _on_brush_changed(self, v):
        self._brush_size = v
        self._lbl_brush_val.setText(str(v))

    def _on_thresh_changed(self, v):
        self._threshold = v
        self._lbl_thresh_val.setText(f"{v}%")

    def _on_smooth_changed(self, v):
        self._smooth = v
        self._lbl_smooth_val.setText(f"{v}%")

    def _sync_tool_buttons(self):
        for btn, key in ((self._btn_pan, "pan"), (self._btn_erase, "erase"), (self._btn_magic, "magic")):
            on = self._active_tool == key
            btn.setChecked(on)
            self._style_icon_tool_btn(btn, on)

    # resize toolbar
    def _build_resize_bar(self):
        bar = QWidget()
        bar.setStyleSheet(f"background:transparent; border:none; border-top:1px solid {_th.border_color(0.06)};")
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(8, 5, 8, 5)
        lay.setSpacing(6)

        # mode toggle: Scale / Crop
        self._btn_mode_scale = self._mode_btn(t("iv_scale"), active=True)
        self._btn_mode_crop = self._mode_btn(t("iv_crop"), active=False)
        self._btn_mode_scale.clicked.connect(lambda: self._set_mode("scale"))
        self._btn_mode_crop.clicked.connect(lambda: self._set_mode("crop"))
        lay.addWidget(self._btn_mode_scale)
        lay.addWidget(self._btn_mode_crop)

        sep = QWidget(); sep.setFixedWidth(1)
        sep.setStyleSheet(f"background:{_th.border_color(0.10)};")
        lay.addWidget(sep)

        # W input
        lay.addWidget(self._dim_label("W"))
        self._inp_w = self._dim_input()
        self._inp_w.textEdited.connect(lambda _t: self._on_dim_edited("w"))
        lay.addWidget(self._inp_w)

        # lock aspect toggle (icon gembok, bukan emoji char)
        self._btn_lock = QPushButton()
        _lic = QIcon(icon("lock"))
        if not _lic.isNull():
            self._btn_lock.setIcon(_lic)
            self._btn_lock.setIconSize(QSize(15, 15))
        else:
            self._btn_lock.setText("L")
        self._btn_lock.setCheckable(True)
        self._btn_lock.setChecked(True)
        self._btn_lock.setCursor(Qt.PointingHandCursor)
        self._btn_lock.setFixedSize(28, 26)
        self._btn_lock.setToolTip(t("iv_lock_aspect"))
        self._btn_lock.toggled.connect(self._on_lock_toggled)
        self._style_lock_btn()
        lay.addWidget(self._btn_lock)

        # H input
        lay.addWidget(self._dim_label("H"))
        self._inp_h = self._dim_input()
        self._inp_h.textEdited.connect(lambda _t: self._on_dim_edited("h"))
        lay.addWidget(self._inp_h)

        # Apply
        self._btn_apply = self._tool_btn("apply", t("iv_apply"), text_btn=True)
        self._btn_apply.clicked.connect(self._apply_resize)
        lay.addWidget(self._btn_apply)

        lay.addStretch()

        # Save / Save As
        self._btn_save = self._tool_btn("save", t("iv_save"), text_btn=True)
        self._btn_save_as = self._tool_btn("save_as", t("iv_save_as"), text_btn=True)
        self._btn_save.clicked.connect(self._save_overwrite)
        self._btn_save_as.clicked.connect(self._save_as)
        lay.addWidget(self._btn_save)
        lay.addWidget(self._btn_save_as)

        return bar

    def _mode_btn(self, text, active):
        b = QPushButton(text)
        b.setCursor(Qt.PointingHandCursor)
        b.setFixedHeight(26)
        b.setMinimumWidth(54)
        self._style_mode_btn(b, active)
        return b

    def _style_mode_btn(self, b, active):
        if active:
            b.setStyleSheet(f"""
                QPushButton {{ background:{_th.accent(0.18)}; border:1px solid {_th.accent(0.40)};
                    border-radius:6px; color:{_th.FG_BLUE}; font-size:12px; font-weight:600; padding:0 10px; }}
            """)
        else:
            b.setStyleSheet(f"""
                QPushButton {{ background:transparent; border:1px solid {_th.border_color(0.08)};
                    border-radius:6px; color:{_th.FG_MUTED}; font-size:12px; padding:0 10px; }}
                QPushButton:hover {{ background:{_th.border_color(0.06)}; color:{_th.FG}; }}
            """)

    def _icon_tool_btn(self, icon_name, tooltip, active=False, toggle=True):
        b = QPushButton()
        b.setCursor(Qt.PointingHandCursor)
        b.setFixedSize(30, 26)
        b.setToolTip(tooltip)
        _ic = QIcon(icon(icon_name))
        if not _ic.isNull():
            b.setIcon(_ic)
            b.setIconSize(QSize(16, 16))
        else:
            b.setText(tooltip[:1])  # fallback huruf pertama kalau icon hilang
        if toggle:
            b.setCheckable(True)
            b.setChecked(active)
        self._style_icon_tool_btn(b, active)
        return b

    def _style_icon_tool_btn(self, b, active):
        if active:
            b.setStyleSheet(f"""
                QPushButton {{ background:{_th.accent(0.18)}; border:1px solid {_th.accent(0.40)};
                    border-radius:6px; }}
            """)
        else:
            b.setStyleSheet(f"""
                QPushButton {{ background:transparent; border:1px solid {_th.border_color(0.08)};
                    border-radius:6px; }}
                QPushButton:hover {{ background:{_th.border_color(0.07)}; border-color:{_th.border_color(0.14)}; }}
            """)

    def _dim_label(self, txt):
        l = QLabel(txt)
        l.setStyleSheet(f"color:{_th.FG_MUTED}; font-size:11px; font-weight:600; background:transparent; border:none;")
        return l

    def _dim_input(self):
        e = QLineEdit()
        e.setFixedWidth(58)
        e.setFixedHeight(26)
        e.setValidator(QIntValidator(1, 30000, self))
        e.setAlignment(Qt.AlignCenter)
        e.setStyleSheet(f"""
            QLineEdit {{ background:{_th.BG_INPUT}; border:1px solid {_th.border_color(0.10)};
                border-radius:6px; color:{_th.FG}; font-size:12px; padding:0 4px; }}
            QLineEdit:focus {{ border-color:{_th.accent(0.55)}; }}
        """)
        return e

    def _style_lock_btn(self):
        on = self._btn_lock.isChecked()
        self._btn_lock.setStyleSheet(f"""
            QPushButton {{ background:{_th.accent(0.18) if on else _th.border_color(0.05)};
                border:1px solid {_th.accent(0.40) if on else _th.border_color(0.08)};
                border-radius:6px; font-size:12px; }}
            QPushButton:hover {{ border-color:{_th.border_color(0.16)}; }}
        """)

    # ── resize interactions ──
    def _set_mode(self, mode):
        self._resize_mode = mode
        self._style_mode_btn(self._btn_mode_scale, mode == "scale")
        self._style_mode_btn(self._btn_mode_crop, mode == "crop")
        # Crop tidak menjaga rasio (potong canvas bebas). Scale boleh lock.
        if mode == "crop":
            self._btn_lock.setEnabled(False)
        else:
            self._btn_lock.setEnabled(True)

    def _on_lock_toggled(self, _checked):
        self._lock_aspect = self._btn_lock.isChecked()
        self._style_lock_btn()
        # Saat baru di-lock, samakan H mengikuti W (anchor ke W).
        if self._lock_aspect:
            self._on_dim_edited("w")

    def _on_dim_edited(self, which):
        if self._sync_guard or not self._lock_aspect or self._resize_mode != "scale":
            return
        if self._nat_w <= 0 or self._nat_h <= 0:
            return
        ratio = self._nat_w / self._nat_h
        self._sync_guard = True
        try:
            if which == "w":
                w = self._read_int(self._inp_w)
                if w:
                    self._inp_h.setText(str(max(1, round(w / ratio))))
            else:
                h = self._read_int(self._inp_h)
                if h:
                    self._inp_w.setText(str(max(1, round(h * ratio))))
        finally:
            self._sync_guard = False

    @staticmethod
    def _read_int(line_edit):
        try:
            return int(line_edit.text())
        except (ValueError, TypeError):
            return 0

    def _reset_dim_fields(self):
        # Isi field W/H dgn ukuran image saat ini.
        if self._cur_image is None:
            return
        self._sync_guard = True
        self._inp_w.setText(str(self._cur_image.width()))
        self._inp_h.setText(str(self._cur_image.height()))
        self._sync_guard = False

    def _apply_resize(self):
        if self._cur_image is None:
            return
        w = self._read_int(self._inp_w)
        h = self._read_int(self._inp_h)
        if w <= 0 or h <= 0:
            return
        if self._resize_mode == "scale":
            new_img = self._cur_image.scaled(
                w, h, Qt.IgnoreAspectRatio, Qt.SmoothTransformation
            )
        else:
            new_img = self._crop_canvas(self._cur_image, w, h)

        self._cur_image = new_img
        self._nat_w = new_img.width()
        self._nat_h = new_img.height()
        self.unsaved = True
        self._refresh_item()
        self._reset_dim_fields()

    @staticmethod
    def _crop_canvas(src, w, h):


        out = QImage(w, h, QImage.Format_ARGB32)
        out.fill(Qt.transparent)
        p = QPainter(out)
        x = (w - src.width()) // 2
        y = (h - src.height()) // 2
        p.drawImage(x, y, src)
        p.end()
        return out

    def _refresh_item(self):
        scene = self._canvas.scene()
        scene.clear()
        pix = QPixmap.fromImage(self._cur_image)
        self._item = QGraphicsPixmapItem(pix)
        self._item.setTransformationMode(Qt.SmoothTransformation)
        scene.addItem(self._item)
        scene.setSceneRect(QRectF(0, 0, self._nat_w, self._nat_h))
        self._update_info()
        self.fit_to_window()

    # ── save ──
    def _save_overwrite(self):
        self._write_image(self.filepath)

    def _save_as(self):
        start = self.filepath
        path, _ = QFileDialog.getSaveFileName(
            self, t("iv_save_as"), start,
            "Images (*.png *.jpg *.jpeg *.bmp *.webp);;All files (*)"
        )
        if path:
            self._write_image(path)

    def _write_image(self, path):
        if self._cur_image is None:
            return
        ok = self._cur_image.save(path)
        if ok:
            self.unsaved = False
            print(f"[ImageViewer] saved: {path}")
            # Kalau overwrite file yang sedang dibuka, info-nya refresh.
            if os.path.normpath(path) == os.path.normpath(self.filepath):
                self._update_info()
        else:
            from main_ext.class_AppDialog import AppDialog
            AppDialog.warning(self.window(), t("iv_save_failed"),
                              f"{t('iv_save_failed')}: {os.path.basename(path)}")

    # undo
    def _push_undo(self):
        if self._cur_image is None:
            return
        # Batasi tumpukan agar tidak makan memori tanpa batas.
        self._undo_stack.append(self._cur_image.copy())
        if len(self._undo_stack) > 30:
            self._undo_stack.pop(0)

    def undo(self):
        if not self._undo_stack:
            return
        self._cur_image = self._undo_stack.pop()
        self._nat_w = self._cur_image.width()
        self._nat_h = self._cur_image.height()
        self.unsaved = True
        self._refresh_item_keep_view()
        self._reset_dim_fields()

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Z and (e.modifiers() & Qt.ControlModifier):
            self.undo()
            return
        super().keyPressEvent(e)

    def _refresh_item_keep_view(self):
        # Update pixmap tanpa mengubah zoom/scroll (beda dgn _refresh_item yang refit).
        scene = self._canvas.scene()
        scene.clear()
        pix = QPixmap.fromImage(self._cur_image)
        self._item = QGraphicsPixmapItem(pix)
        self._item.setTransformationMode(Qt.SmoothTransformation)
        scene.addItem(self._item)
        scene.setSceneRect(QRectF(0, 0, self._nat_w, self._nat_h))
        self._update_info()

    # editor tool dispatch
    def set_active_tool(self, tool):
        # tool: "pan" | "erase" | "magic". Klik tool sama lagi -> balik ke pan.
        if self._active_tool == tool and tool != "pan":
            tool = "pan"
        self._active_tool = tool
        # "pan" => canvas pan mode (set_tool None). erase/magic => tangkap mouse.
        self._canvas.set_tool(None if tool == "pan" else tool)
        self._sync_tool_buttons()

    def _on_tool_point(self, sx, sy, phase):
        if self._cur_image is None or self._active_tool is None:
            return
        x, y = int(sx), int(sy)
        if self._active_tool == "erase":
            if phase == "press":
                self._push_undo()      # 1 stroke = 1 undo entry
                self._stroke_active = True
            if self._stroke_active:
                self._erase_at(x, y)
                if phase == "release":
                    self._stroke_active = False
        elif self._active_tool == "magic":
            if phase == "press":
                self._magic_erase_at(x, y)

    # ── eraser brush (tepi tajam) ──
    def _erase_at(self, cx, cy):
        img = self._cur_image
        if img.format() != QImage.Format_ARGB32:
            img = img.convertToFormat(QImage.Format_ARGB32)
            self._cur_image = img
        r = max(1, self._brush_size // 2)
        p = QPainter(img)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setCompositionMode(QPainter.CompositionMode_Clear)
        p.setPen(Qt.NoPen)
        p.setBrush(Qt.black)
        p.drawEllipse(cx - r, cy - r, r * 2, r * 2)
        p.end()
        self.unsaved = True
        self._update_pixmap_only()

    def _update_pixmap_only(self):
        # Refresh tampilan tanpa rebuild scene (cepat untuk stroke real-time).
        if self._item is not None:
            self._item.setPixmap(QPixmap.fromImage(self._cur_image))

    # ── magic erase (warna) — SELALU contiguous (flood-fill), di worker thread ──
    def _magic_erase_at(self, cx, cy):
        if self._magic_busy:
            return
        img = self._cur_image
        w, h = img.width(), img.height()
        if not (0 <= cx < w and 0 <= cy < h):
            return
        if img.format() != QImage.Format_ARGB32:
            img = img.convertToFormat(QImage.Format_ARGB32)
            self._cur_image = img

        target = img.pixelColor(cx, cy)
        # Kalau titik klik sudah transparan, ga ada yang perlu dihapus.
        if target.alpha() == 0:
            return
        tr, tg, tb = target.red(), target.green(), target.blue()
        # threshold persen -> jarak max. 441.67 = sqrt(255^2*3) (jarak RGB maksimum)
        max_dist = (self._threshold / 100.0) * 441.673
        max_dist_sq = max_dist * max_dist

        self._push_undo()


        work = img.copy()
        self._magic_busy = True
        self._set_tools_enabled(False)

        def _job():
            # 1) match-mask warna (vektorisasi numpy)
            arr, _ref = _qimage_to_ndarray(work)
            b = arr[:, :, 0].astype(np.int32)
            g = arr[:, :, 1].astype(np.int32)
            r = arr[:, :, 2].astype(np.int32)
            dist_sq = (r - tr) ** 2 + (g - tg) ** 2 + (b - tb) ** 2
            match = dist_sq <= max_dist_sq
            # 2) flood-fill scanline -> region nyambung dari titik klik
            filled = self._scanline_fill(match, cx, cy)
            # 3) apply: set pixel area kena -> transparan penuh (vektorisasi)
            arr[filled] = 0
            return True, ""

        def _done(ok, msg):
            self._magic_busy = False
            self._set_tools_enabled(True)
            if ok:
                self._cur_image = work
                self.unsaved = True
                self._update_pixmap_only()
            else:
                if self._undo_stack:
                    self._undo_stack.pop()
                print(f"[ImageViewer] magic erase failed: {msg}")

        from core.threader import run_in_thread
        run_in_thread(_job, None, callback=_done)

    @staticmethod
    def _scanline_fill(match, sx, sy):

        h, w = match.shape
        filled = np.zeros((h, w), dtype=bool)
        if not (0 <= sx < w and 0 <= sy < h) or not match[sy, sx]:
            return filled
        stack = [(sx, sy)]
        while stack:
            x, y = stack.pop()
            row = match[y]
            if filled[y, x] or not row[x]:
                continue
            # Batas span dicari via numpy (bukan while per-pixel): cari posisi
            # False terdekat kiri & kanan dari x pada baris ini.
            left_part = row[:x]
            # index False terakhir sebelum x -> batas kiri = setelahnya
            lf = np.flatnonzero(~left_part)
            xl = (lf[-1] + 1) if lf.size else 0
            right_part = row[x + 1:]
            rf = np.flatnonzero(~right_part)
            xr = (x + int(rf[0])) if rf.size else (w - 1)
            filled[y, xl : xr + 1] = True
            # seed baris atas & bawah: titik awal tiap run True yang belum terisi
            for ny in (y - 1, y + 1):
                if ny < 0 or ny >= h:
                    continue
                seedable = match[ny, xl : xr + 1] & ~filled[ny, xl : xr + 1]
                idx = np.flatnonzero(seedable)
                if idx.size:
                    starts = idx[np.concatenate(([True], np.diff(idx) > 1))]
                    for sxi in starts:
                        stack.append((xl + int(sxi), ny))
        return filled

    # ── smooth edge / defringe (seluruh gambar, di worker thread) ──
    def _apply_smooth_edge(self):
        if self._magic_busy or self._cur_image is None:
            return
        img = self._cur_image
        if img.format() != QImage.Format_ARGB32:
            img = img.convertToFormat(QImage.Format_ARGB32)
            self._cur_image = img

        # level 1..100 -> ambang alpha 1..255. Pixel tepi dgn alpha DI BAWAH
        # ambang = sisa warna seuprit -> dibuang (jadi transparan penuh).
        alpha_thr = max(1, int(self._smooth / 100.0 * 255))

        self._push_undo()
        work = img.copy()
        self._magic_busy = True
        self._set_tools_enabled(False)

        def _job():
            self._defringe(work, alpha_thr)
            return True, ""

        def _done(ok, msg):
            self._magic_busy = False
            self._set_tools_enabled(True)
            if ok:
                self._cur_image = work
                self.unsaved = True
                self._update_pixmap_only()
            else:
                if self._undo_stack:
                    self._undo_stack.pop()
                print(f"[ImageViewer] smooth edge failed: {msg}")

        from core.threader import run_in_thread
        run_in_thread(_job, None, callback=_done)

    @staticmethod
    def _defringe(img, alpha_thr):


        arr, _ref = _qimage_to_ndarray(img)
        a = arr[:, :, 3]
        mask = (a > 0) & (a < alpha_thr)
        arr[mask] = 0   # set BGRA = 0 (transparan penuh) untuk pixel fringe

    # ── load ──
    def _load_image(self):
        scene = self._canvas.scene()
        scene.clear()
        self._item = None

        ok = False
        if self._is_svg:
            ok = self._load_svg(scene)
        else:
            ok = self._load_raster(scene)

        if not ok:
            err = scene.addText("Cannot load image")
            err.setDefaultTextColor(QColor(_th.FG_MUTED))
            self._info_lbl.setText(os.path.basename(self.filepath))
            return

        scene.setSceneRect(QRectF(0, 0, self._nat_w, self._nat_h))
        self._update_info()
        # Isi field W/H resize bar dgn ukuran asli (raster only)
        if self._resize_bar is not None:
            self._reset_dim_fields()
        # Fit di awal (ditunda sampai widget punya ukuran nyata)
        from PySide6.QtCore import QTimer
        QTimer.singleShot(0, self._initial_fit)

    def _load_raster(self, scene):
        img = QImage(self.filepath)
        if img.isNull():
            return False
        # Simpan image kerja (current) untuk dasar scale/crop & save.
        self._cur_image = img
        pix = QPixmap.fromImage(img)
        self._nat_w = pix.width()
        self._nat_h = pix.height()
        self._item = QGraphicsPixmapItem(pix)
        self._item.setTransformationMode(Qt.SmoothTransformation)
        scene.addItem(self._item)
        return True

    def _load_svg(self, scene):
        try:
            from PySide6.QtSvg import QSvgRenderer
            from PySide6.QtSvgWidgets import QGraphicsSvgItem
        except Exception as e:
            print(f"[ImageViewer] QtSvg unavailable: {e}")
            return False
        renderer = QSvgRenderer(self.filepath)
        if not renderer.isValid():
            return False
        item = QGraphicsSvgItem()
        item.setSharedRenderer(renderer)
        self._renderer = renderer  # keep ref alive
        size = renderer.defaultSize()
        self._nat_w = size.width() or 512
        self._nat_h = size.height() or 512
        self._item = item
        scene.addItem(item)
        return True

    # ── fit / zoom ──
    def _initial_fit(self):
        if not self._item:
            return
        self.fit_to_window()
        self._fitted_once = True

    def fit_to_window(self):
        if not self._item or self._nat_w <= 0:
            return
        self._canvas.resetTransform()
        self._canvas.fitInView(self._item, Qt.KeepAspectRatio)
        # Sinkronkan flag skala internal dgn skala hasil fitInView
        m = self._canvas.transform().m11()
        self._canvas.reset_scale_flag(m if m > 0 else 1.0)

    def zoom_actual(self):
        if not self._item:
            return
        self._canvas.resetTransform()
        self._canvas.reset_scale_flag(1.0)
        self._canvas.centerOn(self._item)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        # Auto-refit hanya kalau belum pernah user zoom manual (first layout).
        if self._item and not self._fitted_once:
            self.fit_to_window()

    # ── info / labels ──
    def _update_zoom_label(self, scale):
        self._zoom_lbl.setText(f"{scale * 100:.0f}%")

    def _update_info(self):
        try:
            sz = os.path.getsize(self.filepath)
        except OSError:
            sz = 0
        fmt = os.path.splitext(self.filepath)[1].lstrip(".").upper() or "?"
        self._info_lbl.setText(
            f"{os.path.basename(self.filepath)}   ·   {self._nat_w}×{self._nat_h}"
            f"   ·   {_human_size(sz)}   ·   {fmt}"
        )
