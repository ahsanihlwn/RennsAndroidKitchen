# SPDX-License-Identifier: MPL-2.0
from __future__ import annotations

import os
import re
import math
import time
import bisect
from collections import OrderedDict
from pathlib import Path

RE_WORD = re.compile(r'\S+')

from PySide6.QtCore import Property, QPointF, QRectF, Qt, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import (
    QColor, QFont, QFontDatabase, QFontMetricsF, QGuiApplication,
    QImage, QKeySequence, QPainter, QPen, QTextCharFormat, QTextLayout, QTextOption,
)
from PySide6.QtQuick import QQuickItem, QQuickPaintedItem

from ._engine import Document, languages

# Fallback theme colors derived from RAK's One Dark palette
try:
    from main_ext.class_ThemeUi import theme as _th
    THEME_BG = QColor(_th.BG3)
    THEME_GUTTER = QColor(_th.BG2)
    THEME_TEXT = QColor(_th.FG)
    THEME_MUTED = QColor(_th.FG_MUTED)
    THEME_ACCENT = QColor(_th.accent_hex)
    THEME_SELECTED = QColor(_th.BG_SELECTED)
except Exception:
    THEME_BG = QColor("#1a1b25")
    THEME_GUTTER = QColor("#1a1b22")
    THEME_TEXT = QColor("#c9d1d9")
    THEME_MUTED = QColor("#666c80")
    THEME_ACCENT = QColor("#528bff")
    THEME_SELECTED = QColor("#1e2030")

# Monaco vs-dark standard syntax token colors
DARK_COLORS = [
    "#d4d4d4",  # 0: plain text / default
    "#569cd6",  # 1: keyword / boolean
    "#ce9178",  # 2: string
    "#6a9955",  # 3: comment (italic)
    "#b5cea8",  # 4: number / constant
    "#4ec9b0",  # 5: type / class
    "#dcdcaa",  # 6: function
    "#d4d4d4",  # 7: operator / delimiter
    "#9cdcfe",  # 8: tag / property / variable
]

LIGHT_COLORS = [
    "#24292f",  # 0: plain text
    "#cf222e",  # 1: keyword
    "#0a3069",  # 2: string
    "#6e7781",  # 3: comment
    "#0550ae",  # 4: number
    "#953800",  # 5: type
    "#6639ba",  # 6: function
    "#0550ae",  # 7: operator
    "#116329",  # 8: tag
]


def utf16_length(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


class NativeEditor(QQuickPaintedItem):
    stateChanged = Signal()
    viewportChanged = Signal()
    fontChanged = Signal()
    themeChanged = Signal()
    errorOccurred = Signal(str)
    notice = Signal(str)
    openRequested = Signal()
    saveAsRequested = Signal()
    confirmRequested = Signal()
    closeApproved = Signal()
    findRequested = Signal()
    replaceRequested = Signal()
    contextMenuRequested = Signal(float, float)
    selectionChanged = Signal("QVariantMap")
    foldingChanged = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFlag(QQuickItem.Flag.ItemAcceptsInputMethod, False)
        self.setAcceptedMouseButtons(Qt.MouseButton.LeftButton | Qt.MouseButton.RightButton)
        self.setAcceptHoverEvents(True)
        self.setCursor(Qt.CursorShape.IBeamCursor)
        self.setOpaquePainting(True)
        self.setAntialiasing(True)
        self.setClip(True)

        # Dynamic Theme Colors
        try:
            from main_ext.class_ThemeUi import theme as _th
            is_light = bool(_th._palette.get("_is_light", False))
            self._is_light = is_light
            self._token_hex = LIGHT_COLORS if is_light else DARK_COLORS
            self._fill_color = QColor(_th.BG3)
            self._gutter_color = QColor(_th.BG3)
            self._text_color = QColor(_th.FG)
            self._line_number_color = QColor(_th.FG_MUTED)
            self._active_line_number_color = QColor(_th.FG)
            self._cursor_color = QColor(_th.accent_hex)
            self._selection_color = QColor(_th.BG_SELECTED)
            self._line_highlight_color = QColor(0, 0, 0, 10) if is_light else QColor(255, 255, 255, 8)
            self._gutter_divider_color = QColor(0, 0, 0, 20) if is_light else QColor(255, 255, 255, 18)
        except Exception:
            self._is_light = False
            self._token_hex = DARK_COLORS
            self._fill_color = QColor("#171717")
            self._gutter_color = QColor("#171717")
            self._text_color = QColor("#d4d4d4")
            self._line_number_color = QColor("#6e7681")
            self._active_line_number_color = QColor("#ffffff")
            self._cursor_color = QColor("#3ecf8e")
            self._selection_color = QColor(38, 79, 120, 180)
            self._line_highlight_color = QColor(255, 255, 255, 6)
            self._gutter_divider_color = QColor(255, 255, 255, 18)
        self.setFillColor(self._fill_color)

        self._doc = Document()
        self._docs = {}
        self._scroll_states = {}
        self._path = ""
        self._pending = None
        self._scroll_y = 0.0
        self._scroll_x = 0.0
        self._max_width = 0.0
        self._layouts = OrderedDict()
        self._rows = []
        self._fold_ranges = {}
        self._folds_dirty = True
        self._folded = set()
        self._hidden_rows = set()
        self._visible_rows_map = []
        self._row_to_visual = {}
        self._hover_gutter = False
        self._hovered_row = -1
        self._hovered_fold_btn = -1

        # Code Minimap states
        self._minimap_enabled = True
        self._minimap_width = 76.0
        self._minimap_line_h = 2.0
        self._minimap_image = None
        self._minimap_dirty = True
        self._hover_minimap = False
        self._dragging_minimap = False
        self._minimap_timer = QTimer(self)
        self._minimap_timer.setSingleShot(True)
        self._minimap_timer.setInterval(60)
        self._minimap_timer.timeout.connect(self._rebuild_minimap)

        # Font configuration matching Monaco: Consolas / Cascadia Code / Monospace
        self._font = QFont()
        self._font.setFamilies(["JetBrains Mono", "Cascadia Code", "Fira Code", "Consolas", "Courier New", "monospace"])
        self._font.setPixelSize(13)
        self._font.setStyleHint(QFont.StyleHint.Monospace)
        self._font.setFixedPitch(True)
        self._font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias | QFont.StyleStrategy.PreferQuality)
        self._font.setHintingPreference(QFont.HintingPreference.PreferVerticalHinting)
        self._metrics = QFontMetricsF(self._font)
        self._line_height = 22.0  # Exact match for Monaco's lineHeight: 22
        self._gutter = 66.0
        self._caret_on = True
        self._dragging = False
        self._preedit = ""
        self._preedit_cursor = 0
        self._preedit_formats = []
        self._paint_count = 0
        self._long_line_noticed = False
        self._prepare_count = 0

        # Smooth wheel scrolling state
        self._target_scroll_y = 0.0
        self._last_scroll_time = 0.0
        self._smooth_timer = QTimer(self)
        self._smooth_timer.setInterval(16)
        self._smooth_timer.timeout.connect(self._animate_smooth_scroll)

        self._blink = QTimer(self)
        self._blink.setInterval(550)
        self._blink.timeout.connect(self._blink_caret)
        self._prepare_timer = QTimer(self)
        self._prepare_timer.setSingleShot(True)
        self._prepare_timer.timeout.connect(self._prepare)
        self._lex_timer = QTimer(self)
        self._lex_timer.setSingleShot(True)
        self._lex_timer.setInterval(16)
        self._lex_timer.timeout.connect(self._prepare)
        self.activeFocusChanged.connect(self._focus_changed)
        self.widthChanged.connect(self._on_geometry_changed)
        self.heightChanged.connect(self._on_geometry_changed)
        self._drag_timer = QTimer(self)
        self._drag_timer.setInterval(35)
        self._drag_timer.timeout.connect(self._autoscroll)
        self._last_mouse = QPointF()

    # ── Theme Properties ──────────────────────────────────────────────────
    @Property(QColor, notify=themeChanged)
    def fillColor(self):
        return self._fill_color

    @fillColor.setter
    def fillColor(self, val):
        c = QColor(val)
        if c.isValid() and c != self._fill_color:
            self._fill_color = c
            self._gutter_color = c
            super().setFillColor(c)
            self.update()

    @Property(QColor, notify=themeChanged)
    def gutterColor(self):
        return self._gutter_color

    @gutterColor.setter
    def gutterColor(self, val):
        c = QColor(val)
        if c.isValid() and c != self._gutter_color:
            self._gutter_color = c
            self.update()

    @Property(QColor, notify=themeChanged)
    def cursorColor(self):
        return self._cursor_color

    @cursorColor.setter
    def cursorColor(self, val):
        c = QColor(val)
        if c.isValid() and c != self._cursor_color:
            self._cursor_color = c
            self.update()

    @Property(QColor, notify=themeChanged)
    def selectionColor(self):
        return self._selection_color

    @selectionColor.setter
    def selectionColor(self, val):
        c = QColor(val)
        if c.isValid() and c != self._selection_color:
            self._selection_color = c
            self.update()

    @Property(QColor, notify=themeChanged)
    def lineHighlightColor(self):
        return self._line_highlight_color

    @lineHighlightColor.setter
    def lineHighlightColor(self, val):
        c = QColor(val)
        if c.isValid() and c != self._line_highlight_color:
            self._line_highlight_color = c
            self.update()

    @Property(bool, notify=themeChanged)
    def isLight(self):
        return self._is_light

    @isLight.setter
    def isLight(self, val):
        flag = bool(val)
        if self._is_light != flag:
            self._is_light = flag
            self._token_hex = LIGHT_COLORS if flag else DARK_COLORS
            self._layouts.clear()
            self._minimap_dirty = True
            self._schedule()
            self.update()

    @Property(bool, notify=stateChanged)
    def minimapEnabled(self):
        return self._minimap_enabled

    @minimapEnabled.setter
    def minimapEnabled(self, val):
        flag = bool(val)
        if self._minimap_enabled != flag:
            self._minimap_enabled = flag
            self._schedule()
            self.stateChanged.emit()
            self.update()

    @Property(float, notify=stateChanged)
    def minimapWidth(self):
        return self._minimap_width

    @minimapWidth.setter
    def minimapWidth(self, val):
        v = max(40.0, min(160.0, float(val)))
        if abs(self._minimap_width - v) > 0.5:
            self._minimap_width = v
            self._minimap_dirty = True
            self._schedule()
            self.stateChanged.emit()
            self.update()

# Dynamic Theme Properties
    @Property(QColor, notify=themeChanged)
    def textColor(self):
        return self._text_color

    @textColor.setter
    def textColor(self, val):
        c = QColor(val)
        if c.isValid() and c != self._text_color:
            self._text_color = c
            self._layouts.clear()
            self.update()

    @Property(QColor, notify=themeChanged)
    def lineNumberColor(self):
        return self._line_number_color

    @lineNumberColor.setter
    def lineNumberColor(self, val):
        c = QColor(val)
        if c.isValid() and c != self._line_number_color:
            self._line_number_color = c
            self.update()

    @Property(QColor, notify=themeChanged)
    def activeLineNumberColor(self):
        return self._active_line_number_color

    @activeLineNumberColor.setter
    def activeLineNumberColor(self, val):
        c = QColor(val)
        if c.isValid() and c != self._active_line_number_color:
            self._active_line_number_color = c
            self.update()

    @Property(QColor, notify=themeChanged)
    def gutterDividerColor(self):
        return self._gutter_divider_color

    @gutterDividerColor.setter
    def gutterDividerColor(self, val):
        c = QColor(val)
        if c.isValid() and c != self._gutter_divider_color:
            self._gutter_divider_color = c
            self.update()

        # ── Document & Viewport Properties ────────────────────────────────────
    @Property(str, notify=stateChanged)
    def fileName(self):
        return Path(self._path).name if self._path else "Untitled"

    @Property(str, notify=stateChanged)
    def filePath(self):
        return self._path

    @Property(bool, notify=stateChanged)
    def dirty(self):
        return self._doc.dirty()

    @Property(str, notify=stateChanged)
    def language(self):
        return self._doc.language()

    @Property("QStringList", constant=True)
    def availableLanguages(self):
        return languages()

    @Property(int, notify=stateChanged)
    def lineCount(self):
        return self._doc.line_count()

    @Property(str, notify=stateChanged)
    def positionLabel(self):
        row, column = self._doc.caret()
        return f"Ln {row + 1}, Col {column + 1}"

    @Property(str, notify=stateChanged)
    def sizeLabel(self):
        count = self._doc.byte_count()
        return f"{count / 1024 / 1024:.1f} MB" if count >= 1024 * 1024 else f"{count / 1024:.1f} KB"

    @Property(str, notify=stateChanged)
    def eolLabel(self):
        return "CRLF" if self._doc.eol() == "\r\n" else "LF"

    @Property(str, notify=stateChanged)
    def indentLabel(self):
        unit = self._doc.indent_unit()
        return "Tabs: 4" if "\t" in unit else f"Spaces: {len(unit)}"

    @Property(float, notify=viewportChanged)
    def totalHeight(self):
        vis_count = len(self._visible_rows_map) if self._visible_rows_map else self.lineCount
        return max(self.height(), vis_count * self._line_height + self.height() * 0.4)

    @Property(float, notify=viewportChanged)
    def totalWidth(self):
        return max(self.width(), self._max_width + self._gutter + 80)

    @Property(float, notify=viewportChanged)
    def scrollY(self):
        return self._scroll_y

    @scrollY.setter
    def scrollY(self, value):
        value = max(0.0, min(float(value), self.totalHeight - self.height()))
        if abs(value - self._scroll_y) > 0.1:
            self._scroll_y = value
            self.viewportChanged.emit()
            self._schedule()

    @Property(float, notify=viewportChanged)
    def scrollX(self):
        return self._scroll_x

    @scrollX.setter
    def scrollX(self, value):
        value = max(0.0, min(float(value), self.totalWidth - self.width()))
        if abs(value - self._scroll_x) > 0.1:
            self._scroll_x = value
            self.viewportChanged.emit()
            self._schedule()

    @Property(bool, notify=stateChanged)
    def canUndo(self):
        return self._doc.can_undo()

    @Property(bool, notify=stateChanged)
    def canRedo(self):
        return self._doc.can_redo()

    @Property(bool, notify=stateChanged)
    def hasSelection(self):
        return self._doc.has_selection()

    @Property(str, notify=stateChanged)
    def selectedText(self):
        return self._doc.selected_text() if self._doc.has_selection() else ""

    @Property(bool, notify=stateChanged)
    def readOnly(self):
        return False

    @Property("QVariantMap", notify=stateChanged)
    def selectionRange(self):
        caret_row, caret_col = self._doc.caret()
        if not self._doc.has_selection():
            return {
                "startLineNumber": caret_row + 1,
                "startColumn": caret_col + 1,
                "endLineNumber": caret_row + 1,
                "endColumn": caret_col + 1,
                "text": ""
            }
        ar, ac, br, bc = self._doc.selection()
        if (ar, ac) > (br, bc):
            (ar, ac), (br, bc) = (br, bc), (ar, ac)
        return {
            "startLineNumber": ar + 1,
            "startColumn": ac + 1,
            "endLineNumber": br + 1,
            "endColumn": bc + 1,
            "text": self._doc.selected_text()
        }

    def _schedule(self):
        if not self._prepare_timer.isActive():
            self._prepare_timer.start(0)

    def _on_geometry_changed(self):
        self._schedule()
        if self._minimap_enabled:
            self._minimap_dirty = True
            if not self._minimap_timer.isActive():
                self._minimap_timer.start(40)

    def _compute_folding(self):
        if not self._folds_dirty:
            return
        n = self._doc.line_count()
        if n <= 1:
            self._fold_ranges = {}
            self._folds_dirty = False
            self._recompute_visible_rows()
            return

        unit = self._doc.indent_unit()
        tab_size = 4 if "\t" in unit else max(1, len(unit) if unit else 4)

        indents = []
        lines = []
        for i in range(n):
            l = self._doc.line(i)
            lines.append(l)
            exp = l.expandtabs(tab_size)
            strp = exp.lstrip(" ")
            indents.append(len(exp) - len(strp) if strp else -1)

        bracket_stack = []
        bracket_folds = {}
        for i, line in enumerate(lines):
            for ch in line:
                if ch == "{":
                    bracket_stack.append(i)
                elif ch == "}":
                    if bracket_stack:
                        start_line = bracket_stack.pop()
                        if i > start_line:
                            bracket_folds[start_line] = i

        fold_ranges = {}
        for i in range(n):
            if i in bracket_folds:
                fold_ranges[i] = bracket_folds[i]
                continue
            cur = indents[i]
            if cur < 0:
                continue
            next_nb = i + 1
            while next_nb < n and indents[next_nb] < 0:
                next_nb += 1
            if next_nb >= n or indents[next_nb] <= cur:
                continue
            end = next_nb
            for j in range(next_nb, n):
                j_ind = indents[j]
                if j_ind >= 0:
                    if j_ind > cur:
                        end = j
                    else:
                        break
                else:
                    has_deeper = any(indents[k] > cur for k in range(j + 1, min(n, j + 50)) if indents[k] >= 0)
                    if has_deeper:
                        end = j
                    else:
                        break
            while end > i and indents[end] < 0:
                end -= 1
            if end > i:
                fold_ranges[i] = end

        self._fold_ranges = fold_ranges
        self._folds_dirty = False
        self._folded = {s for s in self._folded if s in self._fold_ranges}
        self._recompute_visible_rows()

    def _recompute_visible_rows(self):
        hidden = set()
        for start in sorted(self._folded):
            end = self._fold_ranges.get(start, start)
            for r in range(start + 1, end + 1):
                hidden.add(r)
        self._hidden_rows = hidden
        n = self._doc.line_count()
        self._visible_rows_map = [r for r in range(n) if r not in hidden]
        self._row_to_visual = {r: vi for vi, r in enumerate(self._visible_rows_map)}

        caret_row, caret_col = self._doc.caret()
        if caret_row in self._hidden_rows:
            for start in sorted(self._folded, reverse=True):
                end = self._fold_ranges.get(start, start)
                if start < caret_row <= end:
                    self._doc.set_cursor(start, min(caret_col, self._doc.line_length(start)), False)
                    break

    @Slot(int)
    def toggleFold(self, row):
        self._compute_folding()
        if row not in self._fold_ranges:
            return
        if row in self._folded:
            self._folded.remove(row)
        else:
            self._folded.add(row)
        self._recompute_visible_rows()
        self._minimap_dirty = True
        self._rebuild_minimap()
        self.foldingChanged.emit()
        self.viewportChanged.emit()
        self._schedule()
        self.update()

    @Slot()
    def foldAll(self):
        self._compute_folding()
        self._folded = set(self._fold_ranges.keys())
        self._recompute_visible_rows()
        self._minimap_dirty = True
        self._rebuild_minimap()
        self.foldingChanged.emit()
        self.viewportChanged.emit()
        self._schedule()
        self.update()

    @Slot()
    def unfoldAll(self):
        self._folded.clear()
        self._recompute_visible_rows()
        self._minimap_dirty = True
        self._rebuild_minimap()
        self.foldingChanged.emit()
        self.viewportChanged.emit()
        self._schedule()
        self.update()

    @Slot(int, result=bool)
    def isFolded(self, row):
        return row in self._folded

    @Slot(int, result=bool)
    def isFoldable(self, row):
        self._compute_folding()
        return row in self._fold_ranges

    def _scroll_from_minimap(self, y):
        max_editor_scroll = max(0.0, self.totalHeight - self.height())
        if max_editor_scroll <= 0:
            self.scrollY = 0.0
            return

        total_lines = len(self._visible_rows_map) if self._visible_rows_map else self.lineCount
        line_h = self._minimap_line_h
        total_minimap_h = max(1.0, total_lines * line_h)

        if total_minimap_h <= self.height():
            vis_in_view = self.height() / max(1.0, self._line_height)
            slider_h = max(20.0, min(float(self.height()), vis_in_view * line_h))
            target_vi = (y - slider_h / 2.0) / max(0.1, line_h)
            self.scrollY = max(0.0, min(max_editor_scroll, target_vi * self._line_height))
        else:
            visible_ratio = min(1.0, self.height() / max(self.height(), self.totalHeight))
            slider_h = max(24.0, visible_ratio * self.height())
            travel = max(1.0, self.height() - slider_h)
            target_slider_y = y - slider_h / 2.0
            ratio = max(0.0, min(1.0, target_slider_y / travel))
            self.scrollY = ratio * max_editor_scroll

    def _rebuild_minimap(self):
        if not self._minimap_enabled:
            self._minimap_dirty = False
            return
        if self.width() < 300 or self.height() <= 0:
            # Layout not ready yet: keep _minimap_dirty = True so it rebuilds once dimensions are valid
            return

        visible_rows = self._visible_rows_map if self._visible_rows_map else list(range(self.lineCount))
        vis_count = len(visible_rows)
        if vis_count == 0:
            self._minimap_image = None
            self._minimap_dirty = False
            self.update()
            return

        width = int(self._minimap_width)
        h = max(100.0, float(self.height()))
        line_h = 2.0
        bar_h = 1.2
        img_h = max(int(h), int(vis_count * line_h))

        img = QImage(width, img_h, QImage.Format.Format_ARGB32_Premultiplied)
        img.fill(0)
        painter = QPainter(img)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)

        # Muted syntax colors with ~65% opacity matching VS Code's delicate minimap look
        colors = []
        for h_color in self._token_hex:
            c = QColor(h_color)
            c.setAlpha(170)
            colors.append(c)

        char_w = 0.95
        left_m = 4.0
        unit = self._doc.indent_unit()
        tab_size = 4 if "\t" in unit else max(1, len(unit) if unit else 4)

        # Query syntax tokens in batches of 300
        for chunk_start in range(0, vis_count, 300):
            chunk_rows = visible_rows[chunk_start:chunk_start + 300]
            contiguous = []
            if chunk_rows:
                cs = chunk_rows[0]
                cc = 1
                for r in chunk_rows[1:]:
                    if r == cs + cc:
                        cc += 1
                    else:
                        contiguous.append((cs, cc))
                        cs = r
                        cc = 1
                contiguous.append((cs, cc))

            chunk_data = {}
            for start, count in contiguous:
                lines, _ = self._doc.viewport(start, count)
                for off, item in enumerate(lines):
                    chunk_data[start + off] = item

            for vi_offset, row in enumerate(chunk_rows):
                vi = chunk_start + vi_offset
                y = vi * line_h
                text, spans = chunk_data.get(row, (self._doc.line(row), []))
                if spans:
                    for s_start, s_len, kind in spans:
                        col = colors[kind] if kind < len(colors) else colors[0]
                        span_text = text[s_start : s_start + s_len]
                        for match in RE_WORD.finditer(span_text):
                            w_start = s_start + match.start()
                            w_len = len(match.group(0))
                            col_idx = len(text[:w_start].expandtabs(tab_size))
                            x = left_m + col_idx * char_w
                            w_px = max(1.0, w_len * char_w - 0.4)
                            if x < width:
                                painter.fillRect(QRectF(x, y, min(w_px, width - x - 2), bar_h), col)
                else:
                    for match in RE_WORD.finditer(text):
                        col_idx = len(text[:match.start()].expandtabs(tab_size))
                        x = left_m + col_idx * char_w
                        w_px = max(1.0, len(match.group(0)) * char_w - 0.4)
                        if x < width:
                            painter.fillRect(QRectF(x, y, min(w_px, width - x - 2), bar_h), colors[0])

        painter.end()
        self._minimap_line_h = line_h
        self._minimap_image = img
        self._minimap_dirty = False
        self.update()

    def _layout(self, row, text, spans):
        key = (text, tuple(spans))
        cached = self._layouts.get(row)
        if cached and cached[0] == key:
            self._layouts.move_to_end(row)
            return cached[1]
        layout = QTextLayout(text, self._font)
        option = QTextOption()
        option.setWrapMode(QTextOption.WrapMode.NoWrap)
        option.setTabStopDistance(self._metrics.horizontalAdvance(" ") * 4)
        layout.setTextOption(option)
        formats = []
        for start, length, kind in spans:
            region = QTextLayout.FormatRange()
            region.start, region.length = start, length
            region.format = QTextCharFormat()
            hex_color = self._token_hex[kind] if kind < len(self._token_hex) else self._token_hex[0]
            region.format.setForeground(QColor(hex_color))
            if kind == 3:  # comment italic
                region.format.setFontItalic(True)
            formats.append(region)
        layout.setFormats(formats)
        layout.setCacheEnabled(True)
        layout.beginLayout()
        line = layout.createLine()
        if line.isValid():
            line.setLineWidth(1e9)
        layout.endLayout()
        self._layouts[row] = (key, layout)
        while len(self._layouts) > 1024:
            self._layouts.popitem(last=False)
        return layout

    def _prepare(self, trigger_update=True):
        if self.width() <= 0 or self.height() <= 0:
            return
        self._compute_folding()
        self._prepare_count += 1
        self._gutter = max(64.0, self._metrics.horizontalAdvance(str(self.lineCount)) + 28.0)
        self._scroll_y = min(self._scroll_y, max(0, self.totalHeight - self.height()))

        vis_total = len(self._visible_rows_map)
        if vis_total == 0:
            self._rows = []
            if trigger_update:
                self.update()
            return

        first_vi = max(0, int(self._scroll_y // self._line_height))
        count = min(300, math.ceil(self.height() / self._line_height) + 2)
        end_vi = min(vis_total, first_vi + count)

        needed_vis = list(range(first_vi, end_vi))
        needed_rows = [self._visible_rows_map[vi] for vi in needed_vis]

        contiguous = []
        if needed_rows:
            c_start = needed_rows[0]
            c_count = 1
            for r in needed_rows[1:]:
                if r == c_start + c_count:
                    c_count += 1
                else:
                    contiguous.append((c_start, c_count))
                    c_start = r
                    c_count = 1
            contiguous.append((c_start, c_count))

        line_data = {}
        all_ready = True
        for start, cnt in contiguous:
            chunk, ready = self._doc.viewport(start, cnt)
            if not ready:
                all_ready = False
            for offset, item in enumerate(chunk):
                line_data[start + offset] = item

        rows = []
        old_width = self._max_width
        for vi, row in zip(needed_vis, needed_rows):
            text, spans = line_data.get(row, (self._doc.line(row), []))
            if not self._long_line_noticed and len(text) == 8192 and self._doc.line_length(row) > 8192:
                self._long_line_noticed = True
                self.notice.emit("Long line preview: first 8,192 characters. Full contents are preserved.")
            layout = self._layout(row, text, spans)
            if layout.lineCount():
                self._max_width = max(self._max_width, layout.lineAt(0).naturalTextWidth())
            rows.append((row, vi, text, layout))
        self._rows = rows
        if self._max_width != old_width:
            self.viewportChanged.emit()
        if not all_ready:
            self._lex_timer.start()
        if trigger_update:
            self.update()

    def _cursor_x(self, layout, column):
        if not layout.lineCount():
            return 0.0
        column = min(column, utf16_length(layout.text()))
        result = layout.lineAt(0).cursorToX(column)
        return result[0] if isinstance(result, tuple) else result

    def _ensure_visible(self):
        row, column = self._doc.caret()
        vi = self._row_to_visual.get(row)
        if vi is None:
            if self._visible_rows_map:
                idx = bisect.bisect_left(self._visible_rows_map, row)
                idx = min(len(self._visible_rows_map) - 1, max(0, idx))
                vi = idx
            else:
                vi = 0
        top = vi * self._line_height
        if top < self._scroll_y:
            self.scrollY = top
        elif top + self._line_height > self._scroll_y + self.height():
            self.scrollY = top + self._line_height - self.height()
        text = self._doc.line(row)
        cached = self._layouts.get(row)
        layout = cached[1] if cached and cached[0][0] == text else self._layout(row, text, [])
        x = self._cursor_x(layout, column)
        self._max_width = max(self._max_width, x + 40)
        mm_deduct = self._minimap_width if (self._minimap_enabled and self.width() >= 300) else 0.0
        available = max(20, self.width() - self._gutter - 30 - mm_deduct)
        if x < self._scroll_x:
            self.scrollX = max(0, x - 20)
        elif x > self._scroll_x + available:
            self.scrollX = x - available + 20

    def _changed(self, first_row=None, *, reveal=True):
        if first_row is not None:
            self._folds_dirty = True
            for key in list(self._layouts):
                if key >= first_row:
                    del self._layouts[key]
        self._caret_on = True
        self._minimap_dirty = True
        self._minimap_timer.start(60)
        if reveal:
            self._ensure_visible()
        self.stateChanged.emit()
        self.viewportChanged.emit()
        self.selectionChanged.emit(self.selectionRange)
        if self._preedit:
            QTimer.singleShot(0, lambda: self.updateInputMethod(Qt.InputMethodQuery.ImQueryInput))
        self._schedule()

    def paint(self, painter: QPainter):
        self._paint_count += 1
        if not self._rows and self.width() > 0 and self.height() > 0:
            self._prepare(trigger_update=False)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

        # Editor background
        painter.fillRect(self.boundingRect(), self._fill_color)
        painter.setFont(self._font)

        caret_row, caret_col = self._doc.caret()
        ar, ac, br, bc = self._doc.selection()
        has_selection = self._doc.has_selection()
        origin_x = self._gutter + 14 - self._scroll_x

        unit = self._doc.indent_unit()
        tab_size = 4 if "\t" in unit else max(1, len(unit) if unit else 4)
        space_w = self._metrics.horizontalAdvance(" ")
        step_w = tab_size * space_w

        guide_normal_color = QColor(0, 0, 0, 16) if self._is_light else QColor(255, 255, 255, 22)
        guide_active_color = QColor(0, 0, 0, 52) if self._is_light else QColor(255, 255, 255, 65)

        # Active guide level from caret
        caret_text = self._doc.line(caret_row)
        exp_caret = caret_text.expandtabs(tab_size)
        strp_caret = exp_caret.lstrip(" ")
        caret_lvl = (len(exp_caret) - len(strp_caret)) // tab_size if strp_caret else -1
        active_lvl = caret_lvl - 1 if caret_lvl > 0 else -1
        if caret_text.rstrip().endswith(":") or "{" in caret_text:
            active_lvl = caret_lvl

        mm_w = self._minimap_width if (self._minimap_enabled and self.width() >= 300) else 0.0
        content_w = max(0.0, self.width() - self._gutter - mm_w)

        for row, vi, text, layout in self._rows:
            y = vi * self._line_height - self._scroll_y

            # Active line background highlight
            if row == caret_row:
                painter.fillRect(QRectF(0, y, self.width() - mm_w, self._line_height), self._line_highlight_color)

            painter.save()
            painter.setClipRect(QRectF(self._gutter, 0, content_w, self.height()))

            # Indent guides
            expanded = text.expandtabs(tab_size)
            stripped = expanded.lstrip(" ")
            if stripped:
                indent_lvl = (len(expanded) - len(stripped)) // tab_size
            else:
                prev_lvl = 0
                for pr in range(row - 1, max(-1, row - 30), -1):
                    pl = self._doc.line(pr).expandtabs(tab_size)
                    ps = pl.lstrip(" ")
                    if ps:
                        prev_lvl = (len(pl) - len(ps)) // tab_size
                        break
                next_lvl = 0
                for nr in range(row + 1, min(self.lineCount, row + 30)):
                    nl = self._doc.line(nr).expandtabs(tab_size)
                    ns = nl.lstrip(" ")
                    if ns:
                        next_lvl = (len(nl) - len(ns)) // tab_size
                        break
                indent_lvl = min(prev_lvl, next_lvl)

            for lvl in range(indent_lvl):
                gx = round(origin_x + lvl * step_w)
                if self._gutter <= gx < self.width():
                    is_active = (lvl == active_lvl)
                    painter.setPen(guide_active_color if is_active else guide_normal_color)
                    painter.drawLine(QPointF(gx + 0.5, y), QPointF(gx + 0.5, y + self._line_height))

            selections = []
            if has_selection and ar <= row <= br:
                start = ac if row == ar else 0
                end = bc if row == br else utf16_length(text) + 1
                region = QTextLayout.FormatRange()
                region.start = start
                region.length = max(0, end - start)
                region.format = QTextCharFormat()
                region.format.setBackground(self._selection_color)
                if not self._is_light:
                    region.format.setForeground(QColor("#ffffff"))
                selections.append(region)
                if row < br:
                    painter.fillRect(QRectF(origin_x + self._cursor_x(layout, start), y, self.width(), self._line_height), self._selection_color)

            painter.setPen(self._text_color)
            text_y = y + (self._line_height - self._metrics.height()) / 2
            layout.draw(painter, QPointF(origin_x, text_y), selections)

            # Collapsed inline badge [...] at end of folded line
            if row in self._folded:
                text_len = utf16_length(text)
                end_x = origin_x + self._cursor_x(layout, text_len) + 6
                badge_h = 15.0
                badge_w = 26.0
                badge_y = y + (self._line_height - badge_h) / 2
                badge_rect = QRectF(end_x, badge_y, badge_w, badge_h)
                bg_col = QColor(0, 0, 0, 20) if self._is_light else QColor(255, 255, 255, 25)
                border_col = QColor(0, 0, 0, 40) if self._is_light else QColor(255, 255, 255, 50)
                painter.save()
                painter.setPen(border_col)
                painter.setBrush(bg_col)
                painter.drawRoundedRect(badge_rect, 3, 3)
                painter.setPen(self._line_number_color)
                painter.drawText(badge_rect, Qt.AlignmentFlag.AlignCenter, "...")
                painter.restore()

            # Caret cursor
            if row == caret_row and self.hasActiveFocus() and self._caret_on:
                caret_x = origin_x + self._cursor_x(layout, caret_col)
                painter.fillRect(QRectF(caret_x, y + 2, 2.0, self._line_height - 4), self._cursor_color)

            # Preedit (IME)
            if row == caret_row and self._preedit:
                pre_x = origin_x + self._cursor_x(layout, caret_col)
                pre = QTextLayout(self._preedit, self._font)
                pre.setFormats(self._preedit_formats)
                pre.beginLayout()
                pre_line = pre.createLine()
                pre_line.setLineWidth(1e6)
                pre.endLayout()
                painter.fillRect(QRectF(pre_x, y, pre_line.naturalTextWidth() + 3, self._line_height), self._fill_color)
                pre.draw(painter, QPointF(pre_x, text_y))
                painter.setPen(self._cursor_color)
                painter.drawLine(QPointF(pre_x, y + self._line_height - 3), QPointF(pre_x + pre_line.naturalTextWidth(), y + self._line_height - 3))
                pre_cursor_x = pre_x + self._cursor_x(pre, self._preedit_cursor)
                painter.fillRect(QRectF(pre_cursor_x, y + 2, 2.0, self._line_height - 4), self._cursor_color)
            painter.restore()

        # Gutter background: only fill if explicitly different from editor fill
        if self._gutter_color != self._fill_color:
            painter.fillRect(QRectF(0, 0, self._gutter, self.height()), self._gutter_color)

        # Line numbers and fold indicators
        for row, vi, _, _ in self._rows:
            y = vi * self._line_height - self._scroll_y
            is_active = (row == caret_row)
            painter.setPen(self._active_line_number_color if is_active else self._line_number_color)
            painter.drawText(
                QRectF(0, y, self._gutter - 22, self._line_height),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                str(row + 1)
            )

            # Fold indicator in gutter
            btn_x = self._gutter - 19
            btn_y = y + (self._line_height - 12) / 2
            cx = btn_x + 6
            cy = btn_y + 6

            if row in self._folded:
                # Collapsed state: draw chevron pointing right (rotated 90deg)
                is_hovered = (self._hovered_fold_btn == row or (self._hover_gutter and self._hovered_row == row))
                pen_col = QColor(0, 0, 0, 220) if self._is_light else (QColor(255, 255, 255, 240) if is_hovered else QColor(200, 200, 200, 180))
                pen_w = 1.6 if is_hovered else 1.3
                painter.save()
                pen = QPen(pen_col, pen_w)
                pen.setCapStyle(Qt.PenCapStyle.RoundCap)
                pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
                painter.setPen(pen)
                painter.drawPolyline([QPointF(cx - 2, cy - 3.5), QPointF(cx + 2, cy), QPointF(cx - 2, cy + 3.5)])
                painter.restore()
            elif row in self._fold_ranges:
                # Foldable state: draw chevron 'v' when hovering gutter or this row
                if self._hover_gutter or self._hovered_row == row:
                    is_hovered = (self._hovered_fold_btn == row or self._hovered_row == row)
                    pen_col = QColor(0, 0, 0, 220) if self._is_light else (QColor(255, 255, 255, 240) if is_hovered else QColor(180, 180, 180, 140))
                    pen_w = 1.6 if is_hovered else 1.3
                    painter.save()
                    pen = QPen(pen_col, pen_w)
                    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
                    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
                    painter.setPen(pen)
                    painter.drawPolyline([QPointF(cx - 3.5, cy - 2), QPointF(cx, cy + 2), QPointF(cx + 3.5, cy - 2)])
                    painter.restore()

        # Subtle 1px vertical divider between gutter line numbers and code
        painter.setPen(self._gutter_divider_color)
        painter.drawLine(QPointF(self._gutter - 1, 0), QPointF(self._gutter - 1, self.height()))

        # Code Minimap rendering
        if self._minimap_enabled and self.width() >= 300:
            minimap_w = self._minimap_width
            minimap_x = self.width() - minimap_w

            # Always ensure minimap image is built
            if self._minimap_image is None or self._minimap_dirty:
                self._rebuild_minimap()

            # Subtle trough background
            trough_col = QColor(0, 0, 0, 22) if not self._is_light else QColor(0, 0, 0, 10)
            painter.fillRect(QRectF(minimap_x, 0, minimap_w, self.height()), trough_col)

            # Left 1px divider line
            painter.setPen(self._gutter_divider_color)
            painter.drawLine(QPointF(minimap_x, 0), QPointF(minimap_x, self.height()))

            total_lines = len(self._visible_rows_map) if self._visible_rows_map else self.lineCount
            line_h = self._minimap_line_h
            total_minimap_h = max(1.0, total_lines * line_h)
            max_editor_scroll = max(0.0, self.totalHeight - self.height())
            scroll_ratio = (self._scroll_y / max_editor_scroll) if max_editor_scroll > 0 else 0.0

            top_vi = self._scroll_y / max(1.0, self._line_height)
            vis_in_view = self.height() / max(1.0, self._line_height)

            if total_minimap_h <= self.height():
                minimap_scroll_y = 0.0
                slider_y = top_vi * line_h
                slider_h = max(20.0, min(float(self.height()) - slider_y, vis_in_view * line_h))
            else:
                visible_ratio = min(1.0, self.height() / max(self.height(), self.totalHeight))
                slider_h = max(24.0, visible_ratio * self.height())
                slider_travel = self.height() - slider_h
                slider_y = scroll_ratio * slider_travel
                line_y = top_vi * line_h
                minimap_scroll_y = max(0.0, min(total_minimap_h - self.height(), line_y - slider_y))

            # Draw cached minimap image
            if self._minimap_image is not None and not self._minimap_image.isNull():
                src_y = minimap_scroll_y
                src_h = min(float(self.height()), float(self._minimap_image.height()) - src_y)
                if src_h > 0:
                    dest_rect = QRectF(minimap_x, 0, minimap_w, src_h)
                    src_rect = QRectF(0, src_y, minimap_w, src_h)
                    painter.drawImage(dest_rect, self._minimap_image, src_rect)

            # Viewport Slider Box
            slider_rect = QRectF(minimap_x + 1, slider_y, minimap_w - 2, slider_h)

            is_active_slider = (self._hover_minimap or self._dragging_minimap)
            if self._is_light:
                slider_bg = QColor(0, 0, 0, 24) if is_active_slider else QColor(0, 0, 0, 14)
                slider_border = QColor(0, 0, 0, 48) if is_active_slider else QColor(0, 0, 0, 28)
            else:
                slider_bg = QColor(255, 255, 255, 36) if is_active_slider else QColor(255, 255, 255, 18)
                slider_border = QColor(255, 255, 255, 75) if is_active_slider else QColor(255, 255, 255, 38)

            painter.save()
            painter.setPen(slider_border)
            painter.setBrush(slider_bg)
            painter.drawRoundedRect(slider_rect, 2, 2)
            painter.restore()

    def _focus_changed(self):
        if self.hasActiveFocus():
            self._caret_on = True
            self._blink.start()
        else:
            self._blink.stop()
            self._preedit = ""
        self.update()

    def _blink_caret(self):
        self._caret_on = not self._caret_on
        self.update()

    def _hit(self, point):
        vi = max(0, min(len(self._visible_rows_map) - 1, int((point.y() + self._scroll_y) // self._line_height))) if self._visible_rows_map else 0
        row = self._visible_rows_map[vi] if self._visible_rows_map else 0
        cached = self._layouts.get(row)
        layout = cached[1] if cached else self._layout(row, self._doc.line(row), [])
        x = max(0, point.x() - self._gutter - 14 + self._scroll_x)
        column = layout.lineAt(0).xToCursor(x) if layout.lineCount() else 0
        return row, column

    def _is_inside_selection(self, row: int, col: int) -> bool:
        if not self._doc.has_selection():
            return False
        ar, ac, br, bc = self._doc.selection()
        if (ar, ac) > (br, bc):
            (ar, ac), (br, bc) = (br, bc), (ar, ac)
        if row < ar or row > br:
            return False
        if row == ar and col < ac:
            return False
        if row == br and col > bc:
            return False
        return True

    def mousePressEvent(self, event):
        self.forceActiveFocus()
        self._smooth_timer.stop()
        self._target_scroll_y = self._scroll_y
        self._preedit = ""
        self._last_mouse = event.position()
        pos = event.position()

        # Check if clicking in minimap
        if self._minimap_enabled and self.width() >= 300 and pos.x() >= self.width() - self._minimap_width:
            if event.button() == Qt.MouseButton.LeftButton:
                self._dragging_minimap = True
                self._scroll_from_minimap(pos.y())
                event.accept()
                return

        # Check if clicking in gutter
        if pos.x() < self._gutter:
            vi = int((pos.y() + self._scroll_y) // self._line_height)
            if self._visible_rows_map and 0 <= vi < len(self._visible_rows_map):
                row = self._visible_rows_map[vi]
                # If clicking near fold button (or if line is folded), toggle fold
                if pos.x() >= self._gutter - 24 or row in self._folded:
                    if row in self._fold_ranges:
                        self.toggleFold(row)
                        event.accept()
                        return
                # Clicking line number selects the entire line
                if event.button() == Qt.MouseButton.LeftButton:
                    self._doc.set_cursor(row, 0, False)
                    self._doc.set_cursor(row, self._doc.line_length(row), True)
                    self._changed()
                    event.accept()
                    return

        # Check if clicking on folded inline badge [...]
        if self._folded:
            vi = int((pos.y() + self._scroll_y) // self._line_height)
            if self._visible_rows_map and 0 <= vi < len(self._visible_rows_map):
                row = self._visible_rows_map[vi]
                if row in self._folded:
                    text = self._doc.line(row)
                    cached = self._layouts.get(row)
                    layout = cached[1] if cached else self._layout(row, text, [])
                    origin_x = self._gutter + 14 - self._scroll_x
                    badge_x = origin_x + self._cursor_x(layout, utf16_length(text)) + 6
                    badge_rect = QRectF(badge_x, vi * self._line_height - self._scroll_y + 2, 28, self._line_height - 4)
                    if badge_rect.contains(pos):
                        self.toggleFold(row)
                        event.accept()
                        return

        if event.button() == Qt.MouseButton.RightButton:
            row, col = self._hit(event.position())
            if not self._is_inside_selection(row, col):
                self._doc.set_cursor(row, col, False)
                self._changed()
            self.contextMenuRequested.emit(event.position().x(), event.position().y())
            event.accept()
            return
        self._doc.set_cursor(*self._hit(event.position()), bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier))
        self._dragging = True
        self._drag_timer.start()
        self._changed()
        event.accept()

    def mouseMoveEvent(self, event):
        if self._dragging_minimap:
            self._scroll_from_minimap(event.position().y())
            event.accept()
            return
        if self._dragging:
            self._last_mouse = event.position()
            self._doc.set_cursor(*self._hit(event.position()), True)
            self._changed(reveal=False)
            event.accept()

    def mouseReleaseEvent(self, event):
        if self._dragging_minimap:
            self._dragging_minimap = False
            self.update()
            event.accept()
            return
        if event.button() == Qt.MouseButton.RightButton:
            event.accept()
            return
        self._dragging = False
        self._drag_timer.stop()
        event.accept()

    def _autoscroll(self):
        if not self._dragging:
            return
        y = self._last_mouse.y()
        if y < 0 or y > self.height():
            self.scrollY += -self._line_height if y < 0 else self._line_height
            self._doc.set_cursor(*self._hit(self._last_mouse), True)
            self._changed(reveal=False)

    def mouseDoubleClickEvent(self, event):
        self._doc.select_word(*self._hit(event.position()))
        self._changed()
        event.accept()

    def hoverMoveEvent(self, event):
        pos = event.position()
        old_gutter = self._hover_gutter
        old_row = self._hovered_row
        old_btn = self._hovered_fold_btn
        old_minimap = self._hover_minimap

        self._hover_minimap = (self._minimap_enabled and self.width() >= 300 and pos.x() >= self.width() - self._minimap_width)
        if self._hover_minimap:
            self._hover_gutter = False
            self._hovered_row = -1
            self._hovered_fold_btn = -1
            self.setCursor(Qt.CursorShape.PointingHandCursor)
            if self._hover_minimap != old_minimap:
                self.update()
            event.accept()
            return

        self._hover_gutter = (pos.x() < self._gutter)
        vi = int((pos.y() + self._scroll_y) // self._line_height)
        if self._visible_rows_map and 0 <= vi < len(self._visible_rows_map):
            row = self._visible_rows_map[vi]
            self._hovered_row = row
            if self._hover_gutter and pos.x() >= self._gutter - 24 and row in self._fold_ranges:
                self._hovered_fold_btn = row
            else:
                self._hovered_fold_btn = -1
        else:
            self._hovered_row = -1
            self._hovered_fold_btn = -1

        if self._hover_gutter and self._hovered_fold_btn != -1:
            self.setCursor(Qt.CursorShape.PointingHandCursor)
        elif self._hover_gutter:
            self.setCursor(Qt.CursorShape.ArrowCursor)
        else:
            self.setCursor(Qt.CursorShape.IBeamCursor)

        if (self._hover_gutter != old_gutter or
            self._hovered_row != old_row or
            self._hovered_fold_btn != old_btn or
            self._hover_minimap != old_minimap):
            self.update()
        event.accept()

    def hoverLeaveEvent(self, event):
        need_update = False
        if self._hover_minimap:
            self._hover_minimap = False
            need_update = True
        if self._hover_gutter or self._hovered_row != -1 or self._hovered_fold_btn != -1:
            self._hover_gutter = False
            self._hovered_row = -1
            self._hovered_fold_btn = -1
            need_update = True
        if need_update:
            self.setCursor(Qt.CursorShape.IBeamCursor)
            self.update()
        event.accept()

    def wheelEvent(self, event):
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.zoom(1 if event.angleDelta().y() > 0 else -1)
        else:
            pixel = event.pixelDelta()
            if not pixel.isNull():
                self._smooth_timer.stop()
                self._target_scroll_y = max(0.0, min(self.totalHeight - self.height(), self._scroll_y - pixel.y()))
                self.scrollY = self._target_scroll_y
                self.scrollX -= pixel.x()
            elif event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                self.scrollX -= event.angleDelta().y() / 120 * 70
            else:
                notches = -event.angleDelta().y() / 120
                if notches:
                    if not self._smooth_timer.isActive():
                        self._target_scroll_y = self._scroll_y
                    step = notches * self._line_height * 3.0
                    max_scroll = max(0.0, self.totalHeight - self.height())
                    self._target_scroll_y = max(0.0, min(max_scroll, self._target_scroll_y + step))
                    self._last_scroll_time = time.monotonic()
                    if not self._smooth_timer.isActive():
                        self._smooth_timer.start()
                if event.angleDelta().x():
                    self.scrollX -= event.angleDelta().x() / 120 * 70
        event.accept()

    def _animate_smooth_scroll(self):
        now = time.monotonic()
        elapsed = max(0.001, min(0.032, now - self._last_scroll_time))
        self._last_scroll_time = now
        remaining = self._target_scroll_y - self._scroll_y
        if abs(remaining) < 0.5:
            self.scrollY = self._target_scroll_y
            self._smooth_timer.stop()
            return
        # Smooth exponential decay animation matching WorkspaceTreeWheel
        max_speed = 3600.0  # px/s
        decay = 1.0 - math.exp(-elapsed / 0.035)
        step = math.copysign(min(abs(remaining), max_speed * elapsed, max(abs(remaining) * decay, 1.5)), remaining)
        self.scrollY = self._scroll_y + step

    def keyPressEvent(self, event):
        if not self.hasActiveFocus():
            event.ignore()
            return
        key, modifiers = event.key(), event.modifiers()
        shift = bool(modifiers & Qt.KeyboardModifier.ShiftModifier)
        ctrl = bool(modifiers & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.MetaModifier))
        if event.matches(QKeySequence.StandardKey.Copy): self.copy()
        elif event.matches(QKeySequence.StandardKey.Cut): self.cut()
        elif event.matches(QKeySequence.StandardKey.Paste): self.paste()
        elif event.matches(QKeySequence.StandardKey.SelectAll): self._doc.select_all(); self._changed()
        elif event.matches(QKeySequence.StandardKey.Undo): self.undo()
        elif event.matches(QKeySequence.StandardKey.Redo): self.redo()
        elif ctrl and key == Qt.Key.Key_F: self.findRequested.emit()
        elif ctrl and key == Qt.Key.Key_H: self.replaceRequested.emit()
        elif key in (Qt.Key.Key_Left, Qt.Key.Key_Right, Qt.Key.Key_Up, Qt.Key.Key_Down, Qt.Key.Key_Home, Qt.Key.Key_End):
            direction = {Qt.Key.Key_Left: "left", Qt.Key.Key_Right: "right", Qt.Key.Key_Up: "up", Qt.Key.Key_Down: "down", Qt.Key.Key_Home: "home", Qt.Key.Key_End: "end"}[key]
            if ctrl and direction in ("left", "right"): direction = "word_" + direction
            elif ctrl and direction in ("home", "end"): direction = "doc_" + direction
            self._doc.move_cursor(direction, shift)
            if key in (Qt.Key.Key_Up, Qt.Key.Key_Down) and self._hidden_rows:
                caret_row, caret_col = self._doc.caret()
                if caret_row in self._hidden_rows and self._visible_rows_map:
                    if direction == "up":
                        idx = bisect.bisect_right(self._visible_rows_map, caret_row) - 1
                        target_row = self._visible_rows_map[max(0, idx)]
                    else:
                        idx = bisect.bisect_left(self._visible_rows_map, caret_row)
                        target_row = self._visible_rows_map[min(len(self._visible_rows_map) - 1, idx)]
                    self._doc.set_cursor(target_row, caret_col, shift)
            self._changed()
        elif key in (Qt.Key.Key_PageUp, Qt.Key.Key_PageDown):
            row, column = self._doc.caret()
            step = max(1, int(self.height() // self._line_height) - 2)
            cur_vi = self._row_to_visual.get(row, 0)
            target_vi = max(0, min(len(self._visible_rows_map) - 1, cur_vi + (-step if key == Qt.Key.Key_PageUp else step))) if self._visible_rows_map else 0
            target_row = self._visible_rows_map[target_vi] if self._visible_rows_map else 0
            self._doc.set_cursor(target_row, column, shift)
            self._changed()
        elif key == Qt.Key.Key_Backspace: self._changed(self._doc.delete(True))
        elif key == Qt.Key.Key_Delete: self._changed(self._doc.delete(False))
        elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter): self._changed(self._doc.newline())
        elif key == Qt.Key.Key_Backtab: self._changed(self._doc.indent(True))
        elif key == Qt.Key.Key_Tab:
            if self._doc.has_selection(): self._changed(self._doc.indent(False))
            else: self._changed(self._doc.insert(self._doc.indent_unit()))
        elif key == Qt.Key.Key_Escape: self._preedit = ""; self.update()
        elif event.text() and (not ctrl or bool(modifiers & Qt.KeyboardModifier.AltModifier)):
            if all(ord(char) >= 32 for char in event.text()):
                self._changed(self._doc.insert(event.text(), True))
            else:
                event.ignore(); return
        else:
            event.ignore(); return
        event.accept()

    @Slot()
    def copy(self):
        if self._doc.has_selection():
            QGuiApplication.clipboard().setText(self._doc.selected_text())

    @Slot()
    def cut(self):
        if self._doc.has_selection():
            self.copy()
            self._changed(self._doc.insert(""))

    @Slot()
    def paste(self):
        self._changed(self._doc.insert(QGuiApplication.clipboard().text()))

    @Slot()
    def selectAll(self):
        self._doc.select_all()
        self._changed()

    @Slot()
    def undo(self):
        if self._doc.undo(): self._changed(0)

    @Slot()
    def redo(self):
        if self._doc.redo(): self._changed(0)

    @Slot(result=str)
    def getText(self):
        if not self._doc:
            return ""
        return "\n".join(self._doc.line(i) for i in range(self._doc.line_count()))

    @Slot(str)
    def setText(self, text):
        if not self._doc:
            return
        self._doc.select_all()
        self._changed(self._doc.insert(text))

    @Slot(str, str, result=bool)
    def applyEdit(self, before, after):
        if not self._doc:
            return False
        current = "\n".join(self._doc.line(i) for i in range(self._doc.line_count()))
        if current != before:
            return False
        self._doc.select_all()
        self._changed(self._doc.insert(after))
        return True

    @Slot(int)
    @Slot(int, int)
    @Slot(int, int, int, int)
    def navigateTo(self, line, column=1, end_line=None, end_column=None):
        if not self._doc or self.lineCount <= 0:
            return
        r1 = max(0, min(self.lineCount - 1, line - 1))
        c1 = max(0, min(self._doc.line_length(r1), (column or 1) - 1))
        if end_line is not None and end_column is not None and (end_line != line or end_column != column):
            r2 = max(0, min(self.lineCount - 1, end_line - 1))
            c2 = max(0, min(self._doc.line_length(r2), (end_column or 1) - 1))
            self._doc.set_cursor(r1, c1, False)
            self._doc.set_cursor(r2, c2, True)
        else:
            self._doc.set_cursor(r1, c1, False)
        if r1 in self._hidden_rows:
            for start in sorted(self._folded):
                end = self._fold_ranges.get(start, start)
                if start < r1 <= end:
                    self.toggleFold(start)
                    break
        self._changed(0, reveal=True)
        self.forceActiveFocus()

    @Slot(str)
    def setLanguage(self, language):
        self._doc.set_language(language)
        self._layouts.clear()
        self._changed(0, reveal=False)

    @Slot(str, bool, result=bool)
    def findNext(self, text, backwards=False):
        if not text:
            return False
        result = self._doc.find(text, backwards)
        if result:
            self._changed()
        else:
            self.notice.emit("No match")
        return result

    @Slot(str, result=int)
    def countMatches(self, text):
        if not text:
            return 0
        count = 0
        for i in range(self.lineCount):
            count += self._doc.line(i).count(text)
        return count

    @Slot(str, str, result=bool)
    def replaceNext(self, needle, replacement):
        if not needle:
            return False
        if self._doc.has_selection() and self._doc.selected_text() == needle:
            self._changed(self._doc.insert(replacement))
            self._doc.find(needle, False)
            self._changed()
            return True
        if self._doc.find(needle, False):
            self._changed(self._doc.insert(replacement))
            self._doc.find(needle, False)
            self._changed()
            return True
        return False

    @Slot(str, str, result=int)
    def replaceAll(self, needle, replacement):
        if not needle:
            return 0
        count = 0
        self._doc.set_cursor(0, 0, False)
        seen = set()
        while self._doc.find(needle, False):
            pos = self._doc.caret()
            if pos in seen:
                break
            seen.add(pos)
            self._changed(self._doc.insert(replacement))
            count += 1
            if count > 50000:
                break
        self._changed()
        return count

    @Slot(int)
    def zoom(self, delta):
        size = max(10, min(28, self._font.pixelSize() + delta))
        self._font.setPixelSize(size)
        self._metrics = QFontMetricsF(self._font)
        self._line_height = math.ceil(self._metrics.height() + 7)
        self._max_width = 0
        self._layouts.clear()
        self.fontChanged.emit()
        self._changed(0)

    @Slot(str)
    def request(self, action):
        self._request(action)

    @Slot(QUrl)
    def openUrl(self, url):
        self._request("load", url.toLocalFile())

    def _request(self, action, path=""):
        if self.dirty and action in ("new", "open", "load", "close"):
            self._pending = (action, path)
            self.confirmRequested.emit()
        else:
            self._perform(action, path)

    def _perform(self, action, path=""):
        if action == "new": self._doc = Document(); self._reset("")
        elif action == "open": self.openRequested.emit()
        elif action == "load": self.loadPath(path)
        elif action == "close": self.closeApproved.emit()

    @Slot(str)
    def resolveUnsaved(self, decision):
        if decision == "cancel":
            self._pending = None
        elif decision == "save":
            self.saveCurrent()
        elif decision == "discard" and self._pending:
            action, path = self._pending
            self._pending = None
            self._perform(action, path)

    @Slot()
    def cancelSave(self):
        self._pending = None

    @Slot(str)
    def loadPath(self, path):
        if not path:
            return
        norm_path = os.path.normpath(str(Path(path).resolve()))
        cache_key = os.path.normcase(norm_path)
        if self._path and os.path.normcase(self._path) == cache_key:
            return
        if self._path and self._doc:
            self._scroll_states[os.path.normcase(self._path)] = (self._scroll_y, self._scroll_x, *self._doc.caret())

        if cache_key in self._docs:
            self._doc = self._docs[cache_key]
            prev_state = self._scroll_states.get(cache_key)
            self._reset(norm_path, scroll_state=prev_state)
            return

        try:
            doc = Document()
            doc.load(norm_path)
            self._docs[cache_key] = doc
            self._doc = doc
            self._reset(norm_path)
        except (OSError, ValueError) as error:
            self.errorOccurred.emit(f"Could not open file: {error}")

    @Slot(str)
    def closeFile(self, path):
        if not path:
            return
        norm_path = os.path.normpath(str(Path(path).resolve()))
        cache_key = os.path.normcase(norm_path)
        self._docs.pop(cache_key, None)
        self._scroll_states.pop(cache_key, None)
        if self._path and os.path.normcase(self._path) == cache_key:
            self._path = ""
            self._doc = Document()
            self._reset("")

    def _reset(self, path, scroll_state=None):
        self._path = path
        self._pending = None
        self._smooth_timer.stop()
        self._folds_dirty = True
        self._folded.clear()
        self._hidden_rows.clear()
        self._visible_rows_map = list(range(self.lineCount))
        self._row_to_visual = {r: r for r in range(self.lineCount)}
        self._minimap_dirty = True
        self._minimap_timer.start(10)
        if scroll_state:
            sy, sx, cr, cc = scroll_state
            self._scroll_y = sy
            self._scroll_x = sx
            self._doc.set_cursor(cr, cc, False)
        else:
            self._scroll_y = self._scroll_x = self._max_width = self._target_scroll_y = 0.0
        self._layouts.clear()
        self._rows = []
        self._preedit = ""
        self._long_line_noticed = False
        self._changed(0, reveal=bool(scroll_state))
        self._prepare(trigger_update=True)
        # Note: Do not force active focus here to preserve dialog/parent focus flow.

    @Slot()
    def saveCurrent(self):
        if self._path:
            self._save(self._path)
        else:
            self.saveAsRequested.emit()

    @Slot(QUrl)
    def saveUrl(self, url):
        self._save(url.toLocalFile())

    def _save(self, path):
        try:
            self._doc.save(path)
        except OSError as error:
            self.errorOccurred.emit(f"Could not save file: {error}")
            return
        self._path = str(Path(path).resolve())
        if self._doc.language() == "Plain Text":
            from ._engine import Document as Detector
            self._doc.set_language(Detector("", path).language())
            self._layouts.clear()
        self.stateChanged.emit()
        self.notice.emit("Saved")
        self._schedule()
        if self._pending:
            action, pending_path = self._pending
            self._pending = None
            self._perform(action, pending_path)

    def inputMethodEvent(self, event):
        if event.commitString() or event.replacementLength():
            if event.replacementStart() or event.replacementLength():
                row, column = self._doc.caret()
                a = max(0, column + event.replacementStart())
                self._doc.set_cursor(row, a, False)
                self._doc.set_cursor(row, a + event.replacementLength(), True)
            first = self._doc.insert(event.commitString())
            self._changed(first)
        self._preedit = event.preeditString()
        self._preedit_cursor = utf16_length(self._preedit)
        self._preedit_formats = []
        from PySide6.QtGui import QInputMethodEvent
        for attribute in event.attributes():
            if attribute.type == QInputMethodEvent.AttributeType.Cursor:
                self._preedit_cursor = attribute.start
            elif attribute.type == QInputMethodEvent.AttributeType.TextFormat:
                region = QTextLayout.FormatRange()
                region.start, region.length = attribute.start, attribute.length
                value = attribute.value
                region.format = value.toCharFormat() if hasattr(value, "toCharFormat") else QTextCharFormat(value)
                self._preedit_formats.append(region)
        self.update()
        if self._preedit:
            QTimer.singleShot(0, lambda: self.updateInputMethod(Qt.InputMethodQuery.ImQueryInput))
        event.accept()

    def inputMethodQuery(self, query):
        row, column = self._doc.caret()
        if query == Qt.InputMethodQuery.ImEnabled: return True
        if query == Qt.InputMethodQuery.ImFont: return self._font
        if query == Qt.InputMethodQuery.ImHints: return Qt.InputMethodHint.ImhNoPredictiveText
        if query == Qt.InputMethodQuery.ImSurroundingText: return self._doc.line(row)
        if query == Qt.InputMethodQuery.ImCursorPosition: return column
        if query == Qt.InputMethodQuery.ImAnchorPosition:
            ar, ac, br, bc = self._doc.selection()
            return ac if row == ar and (row, column) == (br, bc) else bc if row == br else column
        if query == Qt.InputMethodQuery.ImCurrentSelection: return self._doc.selected_text()
        if query == Qt.InputMethodQuery.ImCursorRectangle:
            cached = self._layouts.get(row)
            x = self._cursor_x(cached[1], column) if cached else 0
            vi = self._row_to_visual.get(row, row)
            return QRectF(self._gutter + 14 + x - self._scroll_x, vi * self._line_height - self._scroll_y, 2, self._line_height)
        return super().inputMethodQuery(query)

    def diagnostics(self):
        return {
            "bytes": self._doc.byte_count(),
            "lines": self.lineCount,
            "cached_layouts": len(self._layouts),
            "visible_rows": len(self._rows),
            "paint_count": self._paint_count,
            "prepare_count": self._prepare_count,
            "language": self.language,
            "editor_core": self._doc.backend(),
            "history": self._doc.history_info(),
            "folds": len(self._fold_ranges),
            "folded": len(self._folded),
            "minimap_enabled": self._minimap_enabled,
            "minimap_cached": self._minimap_image is not None,
        }
