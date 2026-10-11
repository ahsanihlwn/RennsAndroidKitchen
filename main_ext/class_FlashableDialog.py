# SPDX-License-Identifier: MPL-2.0
import os
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QWidget,
    QScrollArea, QFrame, QLineEdit, QPlainTextEdit, QFileDialog, QMessageBox,
    QProgressBar,
)
from PySide6.QtCore import Qt, Signal, QSize, QThread
from PySide6.QtGui import QFont, QIcon

from main_ext.class_ThemeUi import theme as _th
from main_ext.class_AppDialog import AppDialog as _AppDialog
from core.ui_lang import t
from core import flashable_size as _flsz
from core.flashable_gen import PartSpec, FlashableConfig, generate
from core.threader import run_in_thread

print("[MAIN STAGE] FLASHABLE_DIALOG")


_DYNAMIC_NAMES = {
    "system", "vendor", "product", "system_ext", "odm", "vendor_dlkm",
    "odm_dlkm", "system_dlkm", "my_engineering", "my_heytap", "my_bigball",
    "my_carrier", "my_company", "my_manifest", "my_preload", "my_product",
    "my_region", "my_stock",
}
# both_slot yang ditaruh di ROOT zip (bukan firmware/) — boot/dtbo/vbmeta dkk.
_ROOT_BOTHSLOT_NAMES = {
    "boot", "vendor_boot", "init_boot", "dtbo", "recovery", "vbmeta",
    "vbmeta_system", "vbmeta_vendor", "exaid", "vbmeta_product",
}
_PART_EXTS = (".img", ".zst", ".zstd", ".br", ".new.dat", ".new.dat.br", ".bin")
_IGNORE_PARTITION_NAMES = {
    "payload.bin", "care_map.pb", "apex_info.pb", "compatibility_matrix.xml",
    "manifest.xml", "otacerts.zip", "metadata",
}


def _guess_partition_name(path):
    base = os.path.basename(path)
    for suf in (".new.dat.br", ".new.dat", ".img.zst", ".img.br", ".img",
                ".zst", ".zstd", ".br", ".bin"):
        if base.lower().endswith(suf):
            return base[: -len(suf)]
    return os.path.splitext(base)[0]


def _guess_role(name, badge, in_firmware_dir=False):

    low = name.lower()
    if in_firmware_dir:
        return "both_slot"   # file di folder firmware/ → by-name both slot
    if low.startswith("tr_") or low.startswith("my_") or low in _DYNAMIC_NAMES:
        return "dynamic"
    if low in _ROOT_BOTHSLOT_NAMES or badge in ("BOOT", "VNDRBOOT"):
        return "both_slot"
    if badge in ("EROFS", "EXT2", "EXT3", "EXT4", "SPARSE", "DAT", "DAT.BR", "BR"):
        return "dynamic"
    return "both_slot"


def _guess_in_firmware(name, in_firmware_dir):

    low = name.lower()
    if in_firmware_dir:
        return True
    # boot/dtbo/vbmeta/dst → root zip; sisanya (dpm/gz/lk/logo/...) → firmware/.
    return low not in _ROOT_BOTHSLOT_NAMES and not low.startswith("vbmeta")


def _src_ext(path):

    low = path.lower()
    if low.endswith(".bin"):
        return ".bin"
    if low.endswith(".img"):
        return ".img"
    return ".img"


def _arc_for(name, role, zstd, src="", in_firmware=False):
    if role == "both_slot":
        if in_firmware:
            return f"firmware/{name}{_src_ext(src)}"
        return f"{name}{_src_ext(src)}"
    # dynamic → image final selalu .img (di-materialize), zstd → .img.zst.
    return f"{name}.img.zst" if zstd else f"{name}.img"


def _looks_like_partition(path):
    base = os.path.basename(path).lower()
    if base in _IGNORE_PARTITION_NAMES:
        return False
    low = path.lower()
    return any(low.endswith(e) for e in _PART_EXTS)


def _scan_siblings(seed_path, badge_fn):

    seed_path = os.path.abspath(seed_path)
    seed_dir = os.path.dirname(seed_path)
    found = []
    seen = set()

    def _add(p, in_fw):
        ap = os.path.abspath(p)
        if ap in seen or not os.path.isfile(ap) or not _looks_like_partition(ap):
            return
        seen.add(ap)
        try:
            b = badge_fn(ap) if badge_fn else ""
        except Exception:
            b = ""
        found.append((ap, b, in_fw))

    _add(seed_path, False)

    try:
        for name in sorted(os.listdir(seed_dir)):
            _add(os.path.join(seed_dir, name), False)
    except Exception:
        pass

    fw_dir = os.path.join(seed_dir, "firmware")
    if os.path.isdir(fw_dir):
        try:
            for name in sorted(os.listdir(fw_dir)):
                _add(os.path.join(fw_dir, name), True)
        except Exception:
            pass

    return found


# NATIVE CHECKBOX (pattern PermDialog)

def _check_qss():
    # radius = setengah ukuran (20/2=10) → BULAT (circle), persis PermDialog.
    return f"""
        QPushButton {{
            background: {_th.border_color(0.05)};
            border: 1px solid {_th.border_color(0.14)};
            border-radius: 10px;
        }}
        QPushButton:hover {{ border-color: {_th.accent(0.50)}; background: {_th.accent(0.08)}; }}
        QPushButton:checked {{ background: {_th.accent(0.55)}; border: 1px solid {_th.accent(0.80)}; }}
        QPushButton:checked:hover {{ background: {_th.accent(0.65)}; border-color: {_th.accent(0.90)}; }}
        QPushButton:disabled {{ background: {_th.border_color(0.03)}; border-color: {_th.border_color(0.05)}; }}
    """


def _make_check(checked=True):
    b = QPushButton()
    b.setCheckable(True)
    b.setChecked(checked)
    b.setFixedSize(20, 20)
    b.setCursor(Qt.PointingHandCursor)
    b.setObjectName("permCheck")
    b.setIconSize(QSize(13, 13))
    # Style ditempel langsung ke widget biar gak ke-shadow stylesheet parent
    # (row punya setStyleSheet sendiri) — konsisten di semua tempat.
    b.setStyleSheet(_check_qss())
    try:
        from icons.handler import icon as _icon
        _ic = QIcon(_icon("check_white"))
    except Exception:
        _ic = QIcon()

    def _toggle_icon(v, btn=b, ic=_ic):
        btn.setIcon(ic if v else QIcon())
    b.toggled.connect(_toggle_icon)
    _toggle_icon(checked)
    return b


# STYLE

def _card_qss():
    _hex = _th.BG2.lstrip("#")
    _r, _g, _b = int(_hex[0:2], 16), int(_hex[2:4], 16), int(_hex[4:6], 16)
    return f"""
        QWidget#fl_card {{
            background: {_th.panel_rgba(_r, _g, _b, 170)};
            border: 1px solid {_th.border_color(0.22)};
            border-radius: 14px;
        }}
        QWidget#fl_card QWidget {{
            background: transparent;
            color: {_th.FG};
            font-family: 'Poppins','Inter','Segoe UI',sans-serif;
            font-size: 12px;
        }}
        QWidget#fl_card QLabel {{ background: transparent; border: none; color: {_th.FG}; }}

        QPushButton#permCheck {{
            background: {_th.border_color(0.05)};
            border: 1px solid {_th.border_color(0.14)};
            border-radius: 10px;
        }}
        QPushButton#permCheck:hover {{ border-color: {_th.accent(0.50)}; background: {_th.accent(0.08)}; }}
        QPushButton#permCheck:checked {{ background: {_th.accent(0.55)}; border: 1px solid {_th.accent(0.80)}; }}
        QPushButton#permCheck:checked:hover {{ background: {_th.accent(0.65)}; border-color: {_th.accent(0.90)}; }}
        QPushButton#permCheck:disabled {{ background: {_th.border_color(0.03)}; border-color: {_th.border_color(0.05)}; }}

        QLineEdit {{
            background: {_th.border_color(0.05)}; border: 1px solid {_th.border_color(0.10)};
            border-radius: 6px; color: {_th.FG}; font-size: 12px; padding: 3px 8px; min-height: 22px;
        }}
        QLineEdit:focus {{ border-color: {_th.accent(0.45)}; }}
        QLineEdit:disabled {{ color: {_th.FG_DISABLED2}; }}

        QScrollArea {{ background: transparent; border: none; }}
        QScrollBar:vertical {{ background: transparent; width: 6px; margin: 0; border: none; }}
        QScrollBar::handle:vertical {{ background: {_th.border_color(0.20)}; border-radius: 3px; min-height: 20px; }}
        QScrollBar::handle:vertical:hover {{ background: {_th.border_color(0.35)}; }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; border: none; background: transparent; }}
        QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
    """

def _ss_primary():
    return f"""
        QPushButton {{ background:{_th.accent(0.18)}; border:1px solid {_th.accent(0.38)};
            border-radius:6px; color:{_th.FG_BLUE}; font-size:12px; font-weight:600; padding:0 14px; height:30px; }}
        QPushButton:hover {{ background:{_th.accent(0.32)}; border-color:{_th.accent(0.60)}; color:{_th.FG_BLUE_LIGHT}; }}
        QPushButton:pressed {{ background:{_th.accent(0.48)}; }}
    """

def _ss_neutral():
    return f"""
        QPushButton {{ background:{_th.border_color(0.06)}; border:1px solid {_th.border_color(0.10)};
            border-radius:6px; color:{getattr(_th,'FG3',_th.FG_MUTED)}; font-size:12px; font-weight:400; padding:0 14px; height:30px; }}
        QPushButton:hover {{ background:{_th.border_color(0.10)}; border-color:{_th.border_color(0.18)}; color:{getattr(_th,'FG_HOVER',_th.FG)}; }}
        QPushButton:pressed {{ background:{_th.border_color(0.14)}; }}
        QPushButton:disabled {{ background:{_th.border_color(0.03)}; border-color:{_th.border_color(0.08)}; color:{_th.FG_DESC}; }}
    """

def _editor_scrollbar_qss():
    # scrollbar tipis custom buat QPlainTextEdit — vertical & horizontal
    # ("tiduran"), biar gak native jelek.
    return f"""
        QPlainTextEdit QScrollBar:vertical {{
            background: transparent; width: 7px; margin: 2px 0; border: none;
        }}
        QPlainTextEdit QScrollBar::handle:vertical {{
            background: {_th.border_color(0.20)}; border-radius: 3px; min-height: 24px;
        }}
        QPlainTextEdit QScrollBar::handle:vertical:hover {{ background: {_th.border_color(0.38)}; }}
        QPlainTextEdit QScrollBar:horizontal {{
            background: transparent; height: 7px; margin: 0 2px; border: none;
        }}
        QPlainTextEdit QScrollBar::handle:horizontal {{
            background: {_th.border_color(0.20)}; border-radius: 3px; min-width: 24px;
        }}
        QPlainTextEdit QScrollBar::handle:horizontal:hover {{ background: {_th.border_color(0.38)}; }}
        QPlainTextEdit QScrollBar::add-line, QPlainTextEdit QScrollBar::sub-line {{
            width: 0; height: 0; border: none; background: transparent;
        }}
        QPlainTextEdit QScrollBar::add-page, QPlainTextEdit QScrollBar::sub-page {{ background: transparent; }}
        QPlainTextEdit QAbstractScrollArea::corner {{ background: transparent; }}
    """


def _section_style():
    return f"color:{_th.FG_BLUE_MUTED}; font-size:10px; font-weight:600; letter-spacing:1px; background:transparent; border:none;"

def _hint_style():
    return f"color:{_th.FG_DESC}; font-size:10px; background:transparent; border:none;"


# PARTITION ROW

class _PartRow(QWidget):

    changed = Signal()

    ROLES = ("dynamic", "both_slot")
    _ROLE_LABEL = {"dynamic": "dynamic", "both_slot": "both slot"}

    def __init__(self, src_path, badge, checked=True, in_firmware_dir=False, parent=None):
        super().__init__(parent)
        self.src_path = src_path
        self.badge = badge
        self.kind = _flsz.classify(src_path)
        self.raw_size = None

        name = _guess_partition_name(src_path)
        self.role = _guess_role(name, badge, in_firmware_dir)
        # in_firmware cuma relevan utk both_slot (firmware/ vs root zip).
        self.in_firmware = (self.role == "both_slot") and _guess_in_firmware(name, in_firmware_dir)

        self.setStyleSheet(f"background:{_th.border_color(0.03)}; border:1px solid {_th.border_color(0.06)}; border-radius:8px;")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 5, 8, 5)
        lay.setSpacing(8)

        self.chk = _make_check(checked)
        self.chk.toggled.connect(lambda *_: self.changed.emit())
        lay.addWidget(self.chk)

        self.name_edit = QLineEdit(name)
        self.name_edit.setFixedWidth(150)
        self.name_edit.textChanged.connect(lambda *_: self.changed.emit())
        lay.addWidget(self.name_edit)

        self.role_btn = QPushButton(self._ROLE_LABEL[self.role])
        self.role_btn.setCursor(Qt.PointingHandCursor)
        self.role_btn.setFixedWidth(84)
        self.role_btn.setStyleSheet(_ss_neutral())
        self.role_btn.clicked.connect(self._cycle_role)
        lay.addWidget(self.role_btn)

        self.size_lbl = QLabel("…")
        self.size_lbl.setStyleSheet(f"color:{_th.FG_BLUE_MUTED}; font-size:11px; background:transparent; border:none;")
        self.size_lbl.setFixedWidth(90)
        self.size_lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        lay.addWidget(self.size_lbl)

        zlbl = QLabel(t("fl_col_zstd"))
        zlbl.setStyleSheet(f"color:{_th.FG_DESC}; font-size:10px; background:transparent; border:none;")
        lay.addWidget(zlbl)
        self.zstd_chk = _make_check(self.role == "dynamic")
        self.zstd_chk.toggled.connect(lambda *_: self.changed.emit())
        lay.addWidget(self.zstd_chk)

        lay.addStretch()

        rm = QPushButton("✕")
        rm.setCursor(Qt.PointingHandCursor)
        rm.setFixedSize(22, 22)
        rm.setStyleSheet(f"QPushButton{{background:transparent;border:none;color:{_th.FG_DESC};font-size:13px;}}"
                         f"QPushButton:hover{{color:{_th.RED_LIGHT};}}")
        rm.clicked.connect(self._remove)
        lay.addWidget(rm)

        self._sync_zstd_enabled()

    def _cycle_role(self):
        i = self.ROLES.index(self.role)
        self.role = self.ROLES[(i + 1) % len(self.ROLES)]
        self.role_btn.setText(self._ROLE_LABEL[self.role])
        # recompute in_firmware kalau jadi both_slot.
        if self.role == "both_slot":
            self.in_firmware = _guess_in_firmware(self.name_edit.text().strip(), False)
        else:
            self.in_firmware = False
        self._sync_zstd_enabled()
        self.changed.emit()

    def _sync_zstd_enabled(self):
        # zstd cuma masuk akal buat dynamic (flash_partition_zstd ke mapper).
        on = self.role == "dynamic"
        self.zstd_chk.setEnabled(on)
        if not on:
            self.zstd_chk.setChecked(False)

    def _remove(self):
        self.setParent(None)
        self.changed.emit()
        self.deleteLater()

    def compute_size(self):
        try:
            self.raw_size = _flsz.raw_image_size(self.src_path, self.kind)
            self.size_lbl.setText(_flsz.human_size(self.raw_size))
        except Exception:
            self.raw_size = None
            self.size_lbl.setText("ERR")
            self.size_lbl.setStyleSheet(f"color:{_th.RED_LIGHT}; font-size:11px; background:transparent; border:none;")

    def set_zstd_allowed(self, allowed):
        if not allowed and self.role == "dynamic":
            self.zstd_chk.setChecked(False)
            self.zstd_chk.setEnabled(False)
        elif allowed:
            self._sync_zstd_enabled()

    def to_spec(self):
        if not self.chk.isChecked():
            return None
        name = self.name_edit.text().strip()
        if not name:
            return None
        zstd = self.zstd_chk.isChecked() and self.role == "dynamic"
        return PartSpec(role=self.role, name=name,
                        arc=_arc_for(name, self.role, zstd, self.src_path, self.in_firmware),
                        size=int(self.raw_size or 0), zstd=zstd, src=self.src_path)


# BUILD WORKER THREAD

class _FlashableBuildThread(QThread):
    progress = Signal(int, str, str)   # (pct, status_text, pct_text)
    finished = Signal(bool, str)        # (ok, res_path_or_err)

    def __init__(self, out_zip, parts, script_text, parent=None):
        super().__init__(parent)
        self.out_zip = out_zip
        self.parts = parts
        self.script_text = script_text
        self._is_cancelled = False

    def cancel(self):
        self._is_cancelled = True

    def run(self):
        from core.flashable_build import build_flashable
        from core.flashable_size import human_size

        def on_prog(part_idx, total_parts, label, pct, part_bytes_done, part_bytes_total, bytes_done, total_bytes):
            if self._is_cancelled:
                raise InterruptedError("Cancelled by user")

            pct_int = int(max(0, min(100, pct)))
            pct_text = f"{pct_int}%"

            if label in ("update-binary", "Finalizing"):
                status_text = f"{t('fl_finalizing')} ({label})"
            elif label == "Done":
                status_text = t("fl_done")
            elif part_bytes_total > 0:
                p_done_str = human_size(part_bytes_done)
                p_tot_str = human_size(part_bytes_total)
                status_text = f"{t('fl_building')}: {label} ({p_done_str} / {p_tot_str})"
            else:
                status_text = f"{t('fl_building')}: {label}"

            self.progress.emit(pct_int, status_text, pct_text)

        ok, res = build_flashable(
            self.out_zip, self.parts, self.script_text,
            progress=on_prog,
            is_cancelled=lambda: self._is_cancelled
        )
        self.finished.emit(ok, res)


# DIALOG

class FlashableDialog(QDialog):
    def __init__(self, parent, first_path, badge, main_win):
        super().__init__(parent)
        self._tree = parent
        self._main = main_win
        self._rows = []
        self._script_dirty = False
        self._vendor_64 = True
        self._build_thread = None
        self._task_id = None

        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setModal(True)
        self.setFixedWidth(760)

        # tinggi window di-cap ~88% layar; isi yang melebihi → outer scroll.
        try:
            from PySide6.QtWidgets import QApplication
            scr = QApplication.primaryScreen()
            _avail_h = scr.availableGeometry().height() if scr else 900
        except Exception:
            _avail_h = 900
        self.setMaximumHeight(int(_avail_h * 0.88))

        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(0)

        card = QWidget()
        card.setObjectName("fl_card")
        card.setStyleSheet(_card_qss())
        outer.addWidget(card)

        card_lay = QVBoxLayout(card)
        card_lay.setContentsMargins(20, 16, 20, 16)
        card_lay.setSpacing(10)

        # ── title TETAP di atas (gak ikut scroll) ──
        title = QLabel(t("fl_title"))
        title.setStyleSheet(f"color:{_th.FG}; font-size:14px; font-weight:600; background:transparent; border:none;")
        card_lay.addWidget(title)

        # ── OUTER SCROLL: semua isi window bisa di-scroll atas-bawah ──
        self.outer_scroll = QScrollArea()
        self.outer_scroll.setWidgetResizable(True)
        self.outer_scroll.setFrameShape(QFrame.NoFrame)
        self.outer_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        card_lay.addWidget(self.outer_scroll, 1)

        content = QWidget()
        self.outer_scroll.setWidget(content)
        root = QVBoxLayout(content)
        root.setContentsMargins(0, 0, 6, 0)   # ruang buat scrollbar kanan
        root.setSpacing(10)

        sec = QLabel(t("fl_partitions"))
        sec.setStyleSheet(_section_style())
        root.addWidget(sec)
        hint = QLabel(t("fl_hint_parts"))
        hint.setWordWrap(True)
        hint.setStyleSheet(_hint_style())
        root.addWidget(hint)

        hdr = QWidget()
        hl = QHBoxLayout(hdr)
        hl.setContentsMargins(10, 0, 8, 0)
        hl.setSpacing(8)
        h_part = QLabel(t("fl_col_partition")); h_part.setFixedWidth(20 + 8 + 150)
        h_role = QLabel(t("fl_col_role"));      h_role.setFixedWidth(70)
        h_size = QLabel(t("fl_col_size"));      h_size.setFixedWidth(90); h_size.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        for l in (h_part, h_role, h_size):
            l.setStyleSheet(f"color:{_th.FG_DESC};font-size:9px;font-weight:600;background:transparent;border:none;")
            hl.addWidget(l)
        hl.addStretch()
        root.addWidget(hdr)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.NoFrame)
        self._scroll.setFixedHeight(190)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._list = QWidget()
        self._list_lay = QVBoxLayout(self._list)
        self._list_lay.setContentsMargins(0, 0, 0, 0)
        self._list_lay.setSpacing(4)
        self._list_lay.addStretch()
        self._scroll.setWidget(self._list)
        root.addWidget(self._scroll)

        add_btn = QPushButton(t("fl_add_partition"))
        add_btn.setCursor(Qt.PointingHandCursor)
        add_btn.setStyleSheet(_ss_neutral())
        add_btn.clicked.connect(self._add_partition_dialog)
        root.addWidget(add_btn)

        s1 = QWidget(); s1.setFixedHeight(1); s1.setStyleSheet(f"background:{_th.border_color(0.07)};border:none;")
        root.addWidget(s1)

        gp_row = QHBoxLayout(); gp_row.setSpacing(8)
        self.getprop_chk = _make_check(False)
        gp_row.addWidget(self.getprop_chk)
        gp_lbl = QLabel(t("fl_getprop")); gp_lbl.setStyleSheet(f"color:{_th.FG_MUTED};font-size:12px;background:transparent;border:none;")
        gp_row.addWidget(gp_lbl)
        gp_row.addStretch()
        root.addLayout(gp_row)
        self.getprop_chk.toggled.connect(self._on_getprop_toggle)

        dc_row = QHBoxLayout(); dc_row.setSpacing(8)
        dc_lbl = QLabel(t("fl_device_code")); dc_lbl.setStyleSheet(_hint_style()); dc_lbl.setFixedWidth(180)
        dc_row.addWidget(dc_lbl)
        self.device_edit = QLineEdit(); self.device_edit.setEnabled(False)
        self.device_edit.textChanged.connect(self._regen_if_clean)
        dc_row.addWidget(self.device_edit)
        self.read_vendor_btn = QPushButton(t("fl_read_vendor"))
        self.read_vendor_btn.setCursor(Qt.PointingHandCursor)
        self.read_vendor_btn.setStyleSheet(_ss_neutral())
        self.read_vendor_btn.setEnabled(False)
        self.read_vendor_btn.clicked.connect(self._read_from_vendor)
        dc_row.addWidget(self.read_vendor_btn)
        root.addLayout(dc_row)

        z_row = QHBoxLayout(); z_row.setSpacing(8)
        self.zstd_global_chk = _make_check(True)
        z_row.addWidget(self.zstd_global_chk)
        z_lbl = QLabel(t("fl_zstd_compress")); z_lbl.setStyleSheet(f"color:{_th.FG_MUTED};font-size:12px;background:transparent;border:none;")
        z_row.addWidget(z_lbl)
        z_row.addStretch()
        root.addLayout(z_row)
        self.zstd_global_chk.toggled.connect(self._on_zstd_global)
        self.zstd_warn = QLabel(t("fl_zstd_no64"))
        self.zstd_warn.setStyleSheet(f"color:{_th.RED_LIGHT};font-size:10px;background:transparent;border:none;")
        self.zstd_warn.setVisible(False)
        root.addWidget(self.zstd_warn)

        s2 = QWidget(); s2.setFixedHeight(1); s2.setStyleSheet(f"background:{_th.border_color(0.07)};border:none;")
        root.addWidget(s2)

        sp_row = QHBoxLayout()
        # ── checkbox "Show script" (default OFF → editor disembunyiin) ──
        self.show_script_chk = _make_check(False)
        sp_row.addWidget(self.show_script_chk)
        sp_lbl = QLabel(t("fl_script_preview")); sp_lbl.setStyleSheet(_section_style())
        sp_row.addWidget(sp_lbl); sp_row.addStretch()
        self.regen_btn = QPushButton(t("fl_regenerate"))
        self.regen_btn.setCursor(Qt.PointingHandCursor)
        self.regen_btn.setStyleSheet(_ss_neutral())
        self.regen_btn.clicked.connect(self._force_regen)
        sp_row.addWidget(self.regen_btn)
        root.addLayout(sp_row)
        self.show_script_chk.toggled.connect(self._on_show_script)

        self.editor = QPlainTextEdit()
        self.editor.setStyleSheet(f"""
            QPlainTextEdit {{ background:{_th.border_color(0.04)}; border:1px solid {_th.border_color(0.10)};
                border-radius:8px; color:{_th.FG}; selection-background-color:{_th.accent(0.30)}; }}
            QPlainTextEdit:focus {{ border-color:{_th.accent(0.35)}; }}
            {_editor_scrollbar_qss()}
        """)
        mono = QFont("Cascadia Code", 9); mono.setStyleHint(QFont.Monospace)
        self.editor.setFont(mono)
        self.editor.setFixedHeight(200)
        self.editor.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.editor.textChanged.connect(self._on_editor_changed)
        root.addWidget(self.editor)
        # default: script ketutup.
        self.editor.setVisible(False)
        self.regen_btn.setVisible(False)

        try:
            from core.code_editor import BashHighlighter
            self._hl = BashHighlighter(self.editor.document())
            self._hl._hl_enabled = True
        except Exception:
            self._hl = None

        # ── FOOTER: nempel di bawah card (DI LUAR outer_scroll, gak ikut scroll) ──
        foot_sep = QWidget(); foot_sep.setFixedHeight(1)
        foot_sep.setStyleSheet(f"background:{_th.border_color(0.07)};border:none;")
        card_lay.addWidget(foot_sep)

        # ── PROGRESS (hidden saat belum build) ──
        self._prog_box = QWidget()
        prog_lay = QVBoxLayout(self._prog_box)
        prog_lay.setContentsMargins(0, 4, 0, 4)
        prog_lay.setSpacing(4)

        prog_head = QHBoxLayout()
        self._prog_lbl = QLabel("")
        self._prog_lbl.setStyleSheet(f"color:{_th.FG_BLUE_MUTED}; font-size:11px; font-weight:500; background:transparent; border:none;")
        self._prog_pct_lbl = QLabel("")
        self._prog_pct_lbl.setStyleSheet(f"color:{_th.FG_BLUE_LIGHT}; font-size:11px; font-weight:600; background:transparent; border:none;")
        self._prog_pct_lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        prog_head.addWidget(self._prog_lbl, 1)
        prog_head.addWidget(self._prog_pct_lbl)
        prog_lay.addLayout(prog_head)

        self._prog_bar = QProgressBar()
        self._prog_bar.setTextVisible(False)
        self._prog_bar.setFixedHeight(6)
        self._prog_bar.setStyleSheet(
            f"QProgressBar {{ background:{_th.border_color(0.10)}; border:none; border-radius:3px; }}"
            f" QProgressBar::chunk {{ background:{_th.accent_hex}; border-radius:3px; }}")
        self._prog_bar.setRange(0, 100)
        self._prog_bar.setValue(0)
        prog_lay.addWidget(self._prog_bar)

        self._prog_box.setVisible(False)
        card_lay.addWidget(self._prog_box)

        btn_row = QHBoxLayout(); btn_row.addStretch()
        self.cancel_btn = QPushButton(t("fl_cancel"))
        self.cancel_btn.setCursor(Qt.PointingHandCursor)
        self.cancel_btn.setStyleSheet(_ss_neutral())
        self.cancel_btn.clicked.connect(self._on_cancel_clicked)
        btn_row.addWidget(self.cancel_btn)
        self.create_btn = QPushButton(t("fl_create"))
        self.create_btn.setCursor(Qt.PointingHandCursor)
        self.create_btn.setStyleSheet(_ss_primary())
        self.create_btn.clicked.connect(self._do_create)
        btn_row.addWidget(self.create_btn)
        card_lay.addLayout(btn_row)

        # ── auto-scan sibling partitions → multi-part langsung kebawa ──
        # Semua partisi yang ke-detect dicentang default (full ROM = 1 klik).
        badge_fn = getattr(self._tree, "get_image_badge", None)
        siblings = _scan_siblings(first_path, badge_fn)
        for path, b, in_fw in siblings:
            self._add_row(path, b, checked=True, in_firmware_dir=in_fw)

        self.adjustSize()
        self._regen()

    # rows
    def _add_row(self, path, badge, checked=True, in_firmware_dir=False):
        row = _PartRow(path, badge, checked=checked, in_firmware_dir=in_firmware_dir)
        row.changed.connect(self._regen_if_clean)
        row.compute_size()
        if not self._vendor_64:
            row.set_zstd_allowed(False)
        self._rows.append(row)
        self._list_lay.insertWidget(self._list_lay.count() - 1, row)

    def _add_partition_dialog(self):
        start = os.path.dirname(self._rows[0].src_path) if self._rows else ""
        files, _ = QFileDialog.getOpenFileNames(
            self, t("fl_add_partition"), start,
            "Partition images (*.img *.zst *.zstd *.br *.dat *.bin);;All files (*)")
        badge_fn = getattr(self._tree, "get_image_badge", None)
        for f in files:
            try:
                b = badge_fn(f) if badge_fn else ""
            except Exception:
                b = ""
            self._add_row(f, b, checked=True)
        self._regen_if_clean()

    def _live_rows(self):
        return [r for r in self._rows if r.parent() is not None]

    # toggles
    def _on_getprop_toggle(self, on):
        self.device_edit.setEnabled(on)
        self.read_vendor_btn.setEnabled(on)
        self._regen_if_clean()

    def _on_zstd_global(self, on):
        for r in self._live_rows():
            if r.role == "dynamic" and r.zstd_chk.isEnabled():
                r.zstd_chk.setChecked(on and self._vendor_64)
        self._regen_if_clean()

    def _set_vendor_64(self, is64):
        self._vendor_64 = is64
        self.zstd_warn.setVisible(not is64)
        if not is64:
            self.zstd_global_chk.setChecked(False)
            self.zstd_global_chk.setEnabled(False)
            for r in self._live_rows():
                r.set_zstd_allowed(False)

    def _read_from_vendor(self):
        target = None
        for r in self._live_rows():
            if r.name_edit.text().strip().lower() == "vendor":
                target = r; break
        if target is None:
            for r in self._live_rows():
                if r.kind in ("raw", "sparse") and r.badge in ("EROFS", "EXT2", "EXT3", "EXT4", "SPARSE"):
                    target = r; break
        if target is None:
            _AppDialog.error(self, t("fl_read_vendor"), t("fl_vendor_read_fail"))
            return

        def worker():
            from core.flashable_probe import probe
            return True, probe(target.src_path)

        def after(ok, res):
            if not ok or not isinstance(res, dict) or not res.get("ok"):
                _AppDialog.error(self, t("fl_read_vendor"), f"{t('fl_vendor_read_fail')}\n\n{res}")
                return
            code = res.get("device_code")
            if code:
                self.getprop_chk.setChecked(True)
                self.device_edit.setText(code)
                QMessageBox.information(self, t("fl_read_vendor"), t("fl_vendor_read_ok", code=code))
            self._set_vendor_64(bool(res.get("is_64bit", True)))

        run_in_thread(worker, None, callback=after)

    # script
    def _build_config(self):
        parts = [s for s in (r.to_spec() for r in self._live_rows()) if s]
        return FlashableConfig(
            parts=parts,
            device_code=self.device_edit.text().strip(),
            getprop_check=self.getprop_chk.isChecked(),
            banner_title="Flashable ROM",
            banner_device=self.device_edit.text().strip() or "-",
            banner_version="-",
            use_zstd=any(p.zstd for p in parts),
        )

    def _regen(self):
        cfg = self._build_config()
        if not cfg.parts:
            return
        self.editor.blockSignals(True)
        self.editor.setPlainText(generate(cfg))
        self.editor.blockSignals(False)
        self._script_dirty = False

    def _regen_if_clean(self):
        if not self._script_dirty:
            self._regen()

    def _force_regen(self):
        self._regen()

    def _on_show_script(self, on):
        self.editor.setVisible(on)
        self.regen_btn.setVisible(on)

    def _on_editor_changed(self):
        self._script_dirty = True

    # create
    def _set_building_state(self, building: bool):
        self.outer_scroll.setEnabled(not building)
        self.create_btn.setEnabled(not building)
        if building:
            self.create_btn.setText(t("fl_building"))
        else:
            self.create_btn.setText(t("fl_create"))
            self.cancel_btn.setText(t("fl_cancel"))
            self.cancel_btn.setEnabled(True)

    def _on_cancel_clicked(self):
        if self._build_thread and self._build_thread.isRunning():
            self._prog_lbl.setText(t("fl_cancelling"))
            self.cancel_btn.setEnabled(False)
            self._build_thread.cancel()
            return
        self.reject()

    def reject(self):
        if self._build_thread and self._build_thread.isRunning():
            self._on_cancel_clicked()
            return
        super().reject()

    def _do_create(self):
        if self._build_thread and self._build_thread.isRunning():
            return

        cfg = self._build_config()
        if not cfg.parts:
            _AppDialog.warning(self, t("fl_title"), t("fl_no_parts"))
            return

        default_name = "flashable.zip"
        base = os.path.dirname(self._rows[0].src_path) if self._rows else ""
        proj = getattr(self._tree, "base", None)
        if proj:
            default_name = f"flashable_{os.path.basename(os.path.normpath(proj))}.zip"
            base = proj
        out, _ = QFileDialog.getSaveFileName(
            self, t("fl_save_as"), os.path.join(base, default_name), "Flashable zip (*.zip)")
        if not out:
            return
        if not out.lower().endswith(".zip"):
            out += ".zip"

        script_text = self.editor.toPlainText()
        parts = cfg.parts
        main = self._main
        task_id = f"flashable:{out}"
        self._task_id = task_id
        if main:
            main.push_task(task_id)

        self._set_building_state(True)
        self._prog_lbl.setText(t("fl_building"))
        self._prog_pct_lbl.setText("0%")
        self._prog_bar.setValue(0)
        self._prog_box.setVisible(True)

        self._build_thread = _FlashableBuildThread(out, parts, script_text, parent=self)
        self._build_thread.progress.connect(self._on_build_progress)
        self._build_thread.finished.connect(self._on_build_finished)
        self._build_thread.start()

    def _on_build_progress(self, pct, status_text, pct_text):
        self._prog_bar.setValue(pct)
        self._prog_lbl.setText(status_text)
        self._prog_pct_lbl.setText(pct_text)

    def _on_build_finished(self, ok, res):
        main = self._main
        if main and self._task_id:
            main.pop_task(self._task_id, ok)
            self._task_id = None

        self._set_building_state(False)

        if not ok:
            self._prog_box.setVisible(False)
            if res == "Cancelled":
                return
            _AppDialog.error(self, t("fl_failed"), str(res))
            if main:
                main.set_loading_failed(t("fl_failed"))
            return

        self._prog_bar.setValue(100)
        self._prog_pct_lbl.setText("100%")
        self._prog_lbl.setText(t("fl_done"))
        if main:
            main.maybe_finish_loading()
        QMessageBox.information(self, t("fl_done"), f"{t('fl_done')}\n{res}")
        self.accept()

    # launcher
    @staticmethod
    def launch(tree_widget, full_path, fs_badge):
        from app_ext.class_BlurOverlay import BlurOverlay as _BlurOverlay, install_window_fade
        main_win = tree_widget.window()
        # parent dialog ke main window biar ke-center & modal di seluruh app;
        # blur juga di-anchor ke main window (bukan cuma pane yang diklik).
        dlg = FlashableDialog(main_win, full_path, fs_badge, main_win)
        dlg._tree = tree_widget   # tetap pakai tree buat badge/base/scan
        _ov = _BlurOverlay(main_win) if main_win else None
        if _ov:
            dlg._overlay = _ov
        try:
            install_window_fade(dlg)
        except Exception:
            pass
        dlg.exec()
        if _ov:
            _ov.close_overlay()
