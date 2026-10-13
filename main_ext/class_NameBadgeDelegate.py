# SPDX-License-Identifier: MPL-2.0
from PySide6.QtWidgets import QStyledItemDelegate
from PySide6.QtGui import QPixmap
from collections import OrderedDict
from PySide6.QtCore import QPersistentModelIndex, QPointF
from PySide6.QtGui import QImage
from main_ext.class_ThemeUi import theme as _th
import sys, os, re, shutil
from core.fsdetect import detect_fs_type
from core.erofs import unpack_erofs, repack_erofs
import json
from PySide6.QtCore import QEvent
import time
from core.threader import run_in_thread
from core.loading import LoadingAnimator
from core.code_editor import CodeHighlighter
from core.fsdetect import detect_fs_root
from PySide6.QtCore import Signal
from core.terminal import Terminal
from PySide6.QtWidgets import (
    QApplication,
    QWidget,
    QVBoxLayout,
    QTreeWidget,
    QTreeWidgetItem,
    QHeaderView,
    QTabWidget,
    QPlainTextEdit,
    QMenu,
    QFileDialog,
    QMessageBox,
    QSplitter,
    QSplitterHandle,
    QTabBar,
    QDialog,
    QHBoxLayout,
    QCheckBox,
    QLabel,
    QPushButton,
    QListWidget,
    QListWidgetItem,
    QTextEdit,
    QScrollArea,
    QStyle,
    QStyleOptionViewItem
)
from PySide6.QtCore import QTimer
from PySide6.QtGui import (
    QIcon,
    QPainter,
    QColor,
    QFont,
    QFontMetrics,
    QAction,
    QKeySequence,
    QTextCursor,
    QCursor
)

from PySide6.QtCore import (
    Qt,
    QRect,
    QRectF,
    QSize,
    QFileSystemWatcher
)

print("[MAIN STAGE] NAMEBADGE")


_NEOBRANCH_COLORS = {
    "red":    QColor("#e05555"),
    "blue":   QColor("#4d8ef0"),
    "yellow": QColor("#f0c040"),
    "green":  QColor("#4db860"),
    "orange": QColor("#f08030"),
    "purple": QColor("#9060d0"),
    "pink":   QColor("#e070a0"),
}

# Tiap tipe memakai warna dasar dari tema aktif dengan pergeseran hue yang
# berbeda. Seluruh badge ikut berubah saat tema diganti.
_FILE_BADGE_TONES = {
    "EROFS":    ("accent_hex", 0),
    "EXT2":     ("accent_hex", -45),
    "EXT3":     ("accent_hex", 35),
    "EXT4":     ("accent_hex", 70),
    "SUPER":    ("warning_hex", 0),
    "SPARSE":   ("warning_hex", 25),
    "PAYLOAD":  ("safe_hex", -25),
    "BOOT":     ("safe_hex", -70),
    "VNDRBOOT": ("danger_hex", 25),
    "BR":       ("danger_hex", -20),
    "DAT":      ("warning_hex", -30),
    "DAT.BR":   ("safe_hex", 60),
}


def _file_badge_colors(badge_flag):
    token, hue_shift = _FILE_BADGE_TONES.get(badge_flag, ("accent_hex", 0))
    base = QColor(getattr(_th, token))
    hue, saturation, value, _ = base.getHsvF()
    if hue < 0:
        hue = max(0.0, QColor(_th.accent_hex).hueF())
    hue = (hue + hue_shift / 360.0) % 1.0
    saturation = max(0.14, min(0.48, saturation * 0.7))
    value = min(value, 0.55) if _th.is_light else min(0.85, max(value, 0.70))
    foreground = QColor.fromHsvF(hue, saturation, value)
    background = QColor(foreground)
    background.setAlpha(30 if _th.is_light else 42)
    return foreground, background

# Qt.UserRole + 10 → nama warna branch aktif (str) atau None
_BRANCH_COLOR_ROLE = Qt.UserRole + 10


SIZE_LABEL_ROLE = Qt.UserRole + 30
IS_FOLDER_ROLE = Qt.UserRole + 98
# alias internal lama (dipakai di modul ini)
_SIZE_LABEL_ROLE = SIZE_LABEL_ROLE

# Placeholder yang ditampilkan selagi size asli masih dihitung di background.
SIZE_PENDING = "0 B"


def is_folder_item(item) -> bool:

    if item is None:
        return False
    known = item.data(0, IS_FOLDER_ROLE)
    if known is not None:
        return bool(known)

    tree = item.treeWidget() if hasattr(item, "treeWidget") else None

    # 1. Project root item atau root invisible
    if tree is not None:
        if (item == tree.topLevelItem(0) and getattr(tree, "display_mode", "tree") != "single_dir") or item == tree.invisibleRootItem():
            return True
        sd_up = getattr(tree, "_SD_UP_ROLE", None)
        if sd_up and item.data(0, sd_up):
            return True

    # 2. Virtual mount folder (child node di dalam image yang di-mount)
    p_user = item.data(0, Qt.UserRole)
    if isinstance(p_user, str) and p_user.startswith("mount://"):
        if item.childIndicatorPolicy() == QTreeWidgetItem.ShowIndicator or item.childCount() > 0:
            return True

    # 3. Cek path fisik disk (UserRole + 20) dan path logis (UserRole)
    for role in (Qt.UserRole + 20, Qt.UserRole):
        p = item.data(0, role)
        if isinstance(p, str) and not p.startswith("mount://"):
            try:
                if os.path.isdir(p):
                    return True
            except Exception:
                pass

    # 4. Symlink ke folder (UserRole + 50)
    sym = item.data(0, Qt.UserRole + 50)
    if isinstance(sym, str) and not sym.startswith("mount://"):
        try:
            if os.path.isdir(sym):
                return True
        except Exception:
            pass

    # 5. Indicator policy: semua folder di FileTree & MountDisk menggunakannya
    if item.childIndicatorPolicy() == QTreeWidgetItem.ShowIndicator:
        if isinstance(p_user, str) and not p_user.startswith("mount://"):
            try:
                if not os.path.isfile(p_user):
                    return True
            except Exception:
                pass
        else:
            return True

    # 6. Memiliki child item (file tidak pernah punya child, kecuali image file yang di-mount)
    if item.childCount() > 0:
        if isinstance(p_user, str) and not p_user.startswith("mount://"):
            try:
                if os.path.isfile(p_user):
                    return False
            except Exception:
                pass
        return True

    return False


def _fmt_size(nbytes: int) -> str:

    if nbytes >= 1024 ** 3:
        return f"{nbytes / 1024 ** 3:.2f} GB"
    if nbytes >= 1024 ** 2:
        return f"{nbytes / 1024 ** 2:.1f} MB"
    # < 1 MB → KB (termasuk file kecil; minimal 0.1 KB biar gak "0.0")
    kb = nbytes / 1024
    if kb < 0.1 and nbytes > 0:
        kb = 0.1
    return f"{kb:.1f} KB"


import queue as _queue
import threading as _threading
from PySide6.QtCore import QObject as _QObject, Signal as _Signal


class SizeProbeService(_QObject):

    resolved = _Signal(str, str)  # (path, size_str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._q: "_queue.Queue[str]" = _queue.Queue()
        # dedup: path yang lagi antri / lagi diproses, biar gak stat berkali2
        self._pending: set[str] = set()
        self._lock = _threading.Lock()
        self._stop_event = _threading.Event()
        lock, pending, work_queue, stop_event = self._lock, self._pending, self._q, self._stop_event
        def shutdown(*_unused):
            # Plain Python state survives QObject/slot metadata teardown. A
            # bound Qt slot can disappear during cyclic GC before destroyed.
            with lock:
                if stop_event.is_set():
                    return
                stop_event.set()
                pending.clear()
                work_queue.put(None)
        self._shutdown = shutdown
        if parent is not None:
            parent.destroyed.connect(shutdown)
        self._thread = _threading.Thread(
            target=self._run, name="SizeProbe", daemon=True
        )
        self._thread.start()

    def enqueue(self, path: str):
        if not path:
            return
        with self._lock:
            if self._stop_event.is_set():
                return
            if path in self._pending:
                return
            self._pending.add(path)
            self._q.put(path)

    def stop(self):

        self._shutdown()

    def _run(self):
        while True:
            path = self._q.get()
            with self._lock:
                if self._stop_event.is_set() or path is None:
                    return


            with self._lock:
                self._pending.discard(path)
            try:
                if os.path.isfile(path) and not os.path.isdir(path):
                    size_str = _fmt_size(os.path.getsize(path))
                else:
                    size_str = ""  # folder / hilang → no label
            except Exception:
                size_str = ""
            with self._lock:
                if not self._stop_event.is_set():
                    self.resolved.emit(path, size_str)


# Branch icon dimensions
_BRANCH_W  = 8    # lebar total icon
_BRANCH_H  = 11   # tinggi total icon
_BRANCH_SW = 1.4  # stroke width garis
_DOT_R     = 1.8  # radius dot commit


class NameBadgeDelegate(QStyledItemDelegate):


    def __init__(self, tree: QTreeWidget, badge_icons: dict, parent=None):
        super().__init__(parent)
        self.tree = tree
        self.badge_keys = set(badge_icons.keys())


        self._chip_font = QFont("Poppins")
        self._chip_font.setPixelSize(9)
        self._chip_font.setWeight(QFont.Weight.Bold)
        self._chip_fm = QFontMetrics(self._chip_font)
        self._size_font_key = None
        self._size_font = None
        self._size_fm = None
        self._cell_cache = OrderedDict()
        self._cell_keys = {}
        self._cell_cache_bytes = 0
        self._cell_cache_limit = 8 * 1024 * 1024
        self._cell_cache_hits = 0
        self._cell_cache_misses = 0
        self._cache_enabled = True
        self._cache_new_cells = True
        tree.installEventFilter(self)
        model = tree.model()
        model.dataChanged.connect(self._invalidate_cells)
        model.modelAboutToBeReset.connect(self._clear_cells)
        model.rowsAboutToBeRemoved.connect(self._clear_cells)
        model.layoutAboutToBeChanged.connect(self._clear_cells)

    def _clear_cells(self, *_args):
        self._cell_cache.clear()
        self._cell_keys.clear()
        self._cell_cache_bytes = 0

    def eventFilter(self, obj, event):
        if event.type() in (QEvent.StyleChange, QEvent.FontChange,
                            QEvent.PaletteChange, QEvent.LayoutDirectionChange):
            self._clear_cells()
        return False

    @staticmethod
    def _cell_id(index):
        return index.internalId(), index.column()

    def _discard_cell(self, cell_id):
        record = self._cell_keys.pop(cell_id, None)
        if record is not None:
            for key in record[1]:
                cached = self._cell_cache.pop(key, None)
                if cached is not None:
                    self._cell_cache_bytes -= cached.sizeInBytes()

    def _invalidate_cells(self, top, bottom, _roles=()):
        if not self._cell_keys:
            return
        if top.row() == bottom.row() and top.parent() == bottom.parent():
            for column in range(top.column(), bottom.column() + 1):
                self._discard_cell((top.internalId(), column))
            return
        parent = top.parent()
        for cell_id, (index, _keys) in tuple(self._cell_keys.items()):
            if (not index.isValid() or (index.parent() == parent
                    and top.row() <= index.row() <= bottom.row()
                    and top.column() <= index.column() <= bottom.column())):
                self._discard_cell(cell_id)

    def paint(self, painter, option, index):
        if not self._cache_enabled:
            return self._paint_uncached(painter, option, index)
        cell_id = self._cell_id(index)
        if not self._cache_new_cells and cell_id not in self._cell_keys:
            return self._paint_uncached(painter, option, index)
        rect = option.rect
        if rect.isEmpty():
            return
        ratio = painter.device().devicePixelRatioF()
        transform = painter.worldTransform()
        device_transform = painter.deviceTransform()
        if (transform.m11() != 1 or transform.m22() != 1
                or transform.m12() != 0 or transform.m21() != 0
                or abs(device_transform.m11() - ratio) > .00001
                or abs(device_transform.m22() - ratio) > .00001
                or option.palette.color(option.palette.ColorRole.Base).alpha() != 255):
            return self._paint_uncached(painter, option, index)
        import math
        origin = painter.deviceTransform().map(QPointF(rect.topLeft()))
        phase_x = round(origin.x() - math.floor(origin.x()), 6)
        phase_y = round(origin.y() - math.floor(origin.y()), 6)
        # Qt's style, metrics and rasterizer draw the complete cell. Cache only
        # its pixels, retaining elision/icons/fonts and all state variants.
        # Model signals invalidate data; widget events invalidate style/font.
        key = (cell_id, rect.width(), rect.height(), ratio, phase_x, phase_y,
               painter.device().logicalDpiX(), painter.device().logicalDpiY(), option.state.value,
               option.direction.value, option.font.toString(), option.palette.cacheKey(),
               option.decorationSize.width(), option.decorationSize.height(),
               option.textElideMode.value,
               (_th.FG3, _th.accent_hex, _th.warning_hex, _th.safe_hex, _th.danger_hex, _th.is_light))
        image = self._cell_cache.pop(key, None)
        if image is None:
            self._cell_cache_misses += 1
            if not self._cache_new_cells:
                return self._paint_uncached(painter, option, index)
            image = QImage(math.ceil(rect.width() * ratio + phase_x), math.ceil(rect.height() * ratio + phase_y),
                           QImage.Format_RGB32)
            if image.sizeInBytes() > self._cell_cache_limit:
                return self._paint_uncached(painter, option, index)
            image.setDevicePixelRatio(ratio)
            image.setDotsPerMeterX(round(painter.device().logicalDpiX() * 1000 / 25.4))
            image.setDotsPerMeterY(round(painter.device().logicalDpiY() * 1000 / 25.4))
            # FileTree has an opaque native Base background. Match that paint
            # device so Qt keeps the same glyph antialiasing as direct painting.
            image.fill(option.palette.color(option.palette.ColorRole.Base))
            capture = QPainter(image)
            capture.setFont(painter.font())
            capture.setRenderHints(painter.renderHints())
            capture.translate(phase_x / ratio - rect.x(), phase_y / ratio - rect.y())
            self._paint_uncached(capture, option, index)
            capture.end()
            self._cell_cache_bytes += image.sizeInBytes()
            record = self._cell_keys.get(cell_id)
            if record is None:
                record = self._cell_keys[cell_id] = (QPersistentModelIndex(index), set())
            record[1].add(key)
        else:
            self._cell_cache_hits += 1
        self._cell_cache[key] = image
        while self._cell_cache_bytes > self._cell_cache_limit or len(self._cell_cache) > 256:
            old_key, old_image = self._cell_cache.popitem(last=False)
            self._cell_cache_bytes -= old_image.sizeInBytes()
            record = self._cell_keys[old_key[0]]
            record[1].discard(old_key)
            if not record[1]:
                self._cell_keys.pop(old_key[0])
        painter.save()
        painter.setClipRect(rect, Qt.IntersectClip)
        painter.drawImage(QPointF(rect.x() - phase_x / ratio, rect.y() - phase_y / ratio), image)
        painter.restore()

    def _item_size_label(self, item):
        if is_folder_item(item):
            if item.data(0, _SIZE_LABEL_ROLE) != "":
                item.setData(0, _SIZE_LABEL_ROLE, "")
            return ""
        size_label = item.data(0, _SIZE_LABEL_ROLE)
        if size_label is None:
            size_label = SIZE_PENDING
            item.setData(0, _SIZE_LABEL_ROLE, size_label)
            probe = getattr(self.tree, "request_size_probe", None)
            if probe is not None:
                probe(item)
        return size_label or ""

    def _draw_branch_icon(self, painter, cx, cy, color):

        from PySide6.QtGui import QPen, QPainterPath, QColor as _QC

        half_w = _BRANCH_W // 2
        half_h = _BRANCH_H // 2

        # Koordinat dua commit dot
        bx = cx - half_w        # commit bawah (main)  — kiri
        by = cy + half_h - _DOT_R
        tx = cx + half_w        # commit atas (branch) — kanan
        ty = cy - half_h + _DOT_R

        # Titik tekuk stem (sebelum cabang) — sepertiga atas dari stem
        kink_y = cy - 2

        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)

        pen = QPen(color)
        pen.setWidthF(_BRANCH_SW)
        pen.setCapStyle(Qt.RoundCap)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)

        # Garis stem: commit_bawah → kink
        painter.drawLine(bx, by - _DOT_R, bx, kink_y)

        # Curve cabang: kink → commit_atas (cubic bezier)
        path = QPainterPath()
        path.moveTo(bx, kink_y)
        # control points: keluar kiri naik lalu belok kanan
        path.cubicTo(bx, kink_y - 5, tx, ty + 5, tx, ty + _DOT_R)
        painter.drawPath(path)

        # Dot commit bawah
        painter.setBrush(color)
        painter.setPen(Qt.NoPen)
        painter.drawEllipse(bx - _DOT_R, by - _DOT_R, _DOT_R * 2, _DOT_R * 2)

        # Dot commit atas
        painter.drawEllipse(tx - _DOT_R, ty - _DOT_R, _DOT_R * 2, _DOT_R * 2)

        painter.restore()

    def _paint_uncached(self, painter, option, index):
        from PySide6.QtWidgets import QStyle as _QStyle, QApplication as _QApplication
        from PySide6.QtGui import QFont as _QFont, QFontMetrics as _QFontMetrics
        from PySide6.QtCore import QRectF as _QRectF

        item = self.tree.itemFromIndex(index)
        if not item:
            super().paint(painter, option, index)
            return

        if index.column() != 0:
            super().paint(painter, option, index)
            return

        badge_flag = item.data(0, Qt.UserRole + 1)
        has_badge = bool(badge_flag and badge_flag in self.badge_keys)
        has_symlink_dot = bool(item.data(0, Qt.UserRole + 51))
        branch_color = item.data(0, _BRANCH_COLOR_ROLE)
        size_label = self._item_size_label(item)

        if not has_badge and not branch_color and not has_symlink_dot and not size_label:
            super().paint(painter, option, index)
            return

        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        widget = opt.widget or self.tree
        style = widget.style() if widget else _QApplication.style()

        text = opt.text or ""
        fm = opt.fontMetrics
        text_rect = style.subElementRect(_QStyle.SE_ItemViewItemText, opt, widget)

        if size_label:
            font_key = opt.font.toString()
            if font_key != self._size_font_key:
                self._size_font = _QFont(opt.font)
                if self._size_font.pixelSize() > 0:
                    self._size_font.setPixelSize(max(9, self._size_font.pixelSize() - 1))
                else:
                    self._size_font.setPointSizeF(max(7.0, self._size_font.pointSizeF() - 0.75))
                self._size_fm = _QFontMetrics(self._size_font)
                self._size_font_key = font_key

        gap = 6
        pad_x = 5
        pad_y = 2
        chip_w = 0
        chip_h = 0
        chip_font = None
        chip_ascent = 0
        size_w = self._size_fm.horizontalAdvance(size_label) if size_label else 0

        if has_badge:
            chip_font = self._chip_font
            fm2 = self._chip_fm
            chip_w = fm2.horizontalAdvance(badge_flag) + pad_x * 2
            chip_h = fm2.ascent() + pad_y * 2
            chip_ascent = fm2.ascent()

        reserve = 0
        if has_badge:
            reserve += gap + chip_w
        if size_label:
            reserve += gap + size_w
        if has_symlink_dot:
            reserve += 6 + 6
        if branch_color:
            reserve += 10 + _BRANCH_W

        if reserve:
            max_text_width = max(0, text_rect.width() - reserve - 4)
            opt.text = fm.elidedText(text, opt.textElideMode, max_text_width)

        style.drawControl(_QStyle.CE_ItemViewItem, opt, painter, widget)

        text_rect = style.subElementRect(_QStyle.SE_ItemViewItemText, opt, widget)
        text_x = text_rect.x()
        text_width = fm.horizontalAdvance(opt.text)
        badge_end_x = text_x + text_width

        if size_label:
            size_x = badge_end_x + gap
            trailing_space = (gap + chip_w if has_badge else 0)
            trailing_space += (6 + 6 if has_symlink_dot else 0)
            trailing_space += (10 + _BRANCH_W if branch_color else 0)
            max_size_x = option.rect.right() - trailing_space - size_w - 4
            size_x = max(text_x, min(size_x, max_size_x))

            painter.save()
            painter.setFont(self._size_font)
            painter.setPen(QColor(_th.FG3))
            painter.drawText(
                _QRectF(size_x, option.rect.y(), size_w, option.rect.height()),
                Qt.AlignLeft | Qt.AlignVCenter,
                size_label,
            )
            painter.restore()

            badge_end_x = size_x + size_w

        if has_badge:
            chip_x = badge_end_x + gap
            trailing_space = (6 + 6 if has_symlink_dot else 0)
            trailing_space += (10 + _BRANCH_W if branch_color else 0)
            max_chip_x = option.rect.right() - trailing_space - chip_w - 4
            chip_x = max(text_x, min(chip_x, max_chip_x))
            chip_y = option.rect.y() + (option.rect.height() - chip_h) // 2

            painter.save()
            painter.setRenderHint(QPainter.Antialiasing, True)
            painter.setPen(Qt.NoPen)
            badge_fg, badge_bg = _file_badge_colors(badge_flag)
            painter.setBrush(badge_bg)
            painter.drawRoundedRect(_QRectF(chip_x, chip_y, chip_w, chip_h), 3, 3)
            painter.setFont(chip_font)
            painter.setPen(badge_fg)
            painter.drawText(chip_x + pad_x, chip_y + pad_y + chip_ascent, badge_flag)
            painter.restore()

            badge_end_x = chip_x + chip_w

        # ── Symlink Dot: Dual-Tone White Core + Emerald Green Ring (0% CPU, 144Hz Smooth) ──
        if has_symlink_dot:
            dot_gap = 6
            dot_r = 2.5
            dot_x = badge_end_x + dot_gap
            dot_y = option.rect.y() + option.rect.height() / 2

            is_light_bg = getattr(_th, "_is_light", False)
            outer_color = QColor("#16A34A") if is_light_bg else QColor("#43B77A")
            inner_color = QColor("#1C1E26") if is_light_bg else QColor("#FFFFFF")
            border_color = QColor(0, 0, 0, 70)

            painter.save()
            painter.setRenderHint(QPainter.Antialiasing, True)

            from PySide6.QtGui import QPen as _QPen
            # 1. Outer Ring (Hijau Emerald)
            painter.setPen(_QPen(border_color, 0.5))
            painter.setBrush(outer_color)
            painter.drawEllipse(_QRectF(dot_x, dot_y - dot_r, dot_r * 2, dot_r * 2))

            # 2. Inner Core (Putih Bersih / High Contrast Center)
            core_r = 1.2
            painter.setPen(Qt.NoPen)
            painter.setBrush(inner_color)
            painter.drawEllipse(_QRectF(dot_x + (dot_r - core_r), dot_y - core_r, core_r * 2, core_r * 2))

            painter.restore()
            badge_end_x = dot_x + dot_r * 2

        if branch_color:
            color = _NEOBRANCH_COLORS.get(branch_color, QColor("#4d8ef0"))
            branch_gap = 10
            cx = badge_end_x + branch_gap + _BRANCH_W // 2
            max_cx = option.rect.right() - _BRANCH_W // 2 - 3
            cx = min(cx, max_cx)
            cy = option.rect.y() + option.rect.height() // 2
            self._draw_branch_icon(painter, cx, cy, color)

        return

        branch_color = None
        item = self.tree.itemFromIndex(index)
        if item and index.column() == 0:
            branch_color = item.data(0, _BRANCH_COLOR_ROLE)


        super().paint(painter, option, index)

        if index.column() != 0:
            return

        if not item:
            return

        badge_flag = item.data(0, Qt.UserRole + 1)

        text       = index.data(Qt.DisplayRole) or ""
        fm         = option.fontMetrics
        text_width = fm.horizontalAdvance(text)

        # Hitung text_x yang benar: ikut indent + decoration dari Qt style
        from PySide6.QtWidgets import QStyle, QApplication
        style = QApplication.style()
        text_rect = style.subElementRect(QStyle.SE_ItemViewItemText, option, self.tree)
        text_x = text_rect.x()

        # 1. Badge FS
        badge_end_x = text_x + text_width

        if badge_flag and badge_flag in self.badge_keys:
            from PySide6.QtGui import QFont, QFontMetrics, QPainterPath
            from PySide6.QtCore import QRectF

            GAP      = 6
            PAD_X    = 5
            PAD_Y    = 2

            font = QFont("Poppins")
            font.setPixelSize(9)
            font.setWeight(QFont.Weight.Bold)
            fm2 = QFontMetrics(font)
            tw  = fm2.horizontalAdvance(badge_flag)
            th  = fm2.ascent()

            chip_w = tw + PAD_X * 2
            chip_h = th + PAD_Y * 2
            chip_x = text_x + text_width + GAP
            chip_y = option.rect.y() + (option.rect.height() - chip_h) // 2

            painter.save()
            painter.setRenderHint(QPainter.Antialiasing, True)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(_th.BG_SCROLL))
            painter.drawRoundedRect(QRectF(chip_x, chip_y, chip_w, chip_h), 3, 3)

            painter.setFont(font)
            painter.setPen(QColor(_th.FG2))
            painter.drawText(chip_x + PAD_X, chip_y + PAD_Y + th, badge_flag)
            painter.restore()

            badge_end_x = chip_x + chip_w

        # ── 2. NeoBranch branch icon — posisi sama seperti dot (setelah nama) ──
        if branch_color:
            color = _NEOBRANCH_COLORS.get(branch_color, QColor("#4d8ef0"))

            GAP = 10
            cx  = badge_end_x + GAP + _BRANCH_W // 2
            cy  = option.rect.y() + option.rect.height() // 2

            self._draw_branch_icon(painter, cx, cy, color)

    def sizeHint(self, option, index):
        return super().sizeHint(option, index)

    def createEditor(self, parent, option, index):

        from PySide6.QtWidgets import QLineEdit
        editor = QLineEdit(parent)
        editor.setFrame(False)
        editor.setStyleSheet(f"""
            QLineEdit {{
                background: {_th.BG_SELECTED};
                border: 1px solid {_th.accent(0.50)};
                border-radius: 4px;
                color: {_th.FG};
                font-size: 13px;
                padding: 0px 4px;
                selection-background-color: {_th.accent(0.55)};
                selection-color: {_th.FG_BRIGHT};
            }}
            QLineEdit:focus {{
                border-color: {_th.accent(0.80)};
                background: {_th.BG_SELECTED};
            }}
        """)
        return editor
