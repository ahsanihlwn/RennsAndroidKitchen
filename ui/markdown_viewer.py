# SPDX-License-Identifier: MPL-2.0
"""Native Markdown viewer document backed by Rust rak_native."""
import os

from PySide6.QtCore import QObject, Property, Signal, Slot, QUrl

from core.command_context import failure_summary
from core.native_backend import render_markdown, render_markdown_file
from services.async_jobs import AsyncJobs
from ui.rows import RowsModel


def _markdown_theme(colors):
    c = colors or {}
    is_light = bool(c.get("is_light", False))
    return {
        "is_light": is_light,
        "fg": str(c.get("FG") or ("#24292f" if is_light else "#e2e8f0")),
        "fg_bright": str(c.get("FG_BRIGHT") or ("#090a0c" if is_light else "#ffffff")),
        "fg_muted": str(c.get("FG_MUTED") or ("#57606a" if is_light else "#94a3b8")),
        "accent": str(c.get("accent_hex") or c.get("FG_BLUE") or "#38bdf8"),
        "code_bg": str(c.get("BG_DARK") or c.get("BG4") or ("#f3f4f6" if is_light else "#161b22")),
        "code_fg": str(c.get("FG_BRIGHT") or ("#1f2328" if is_light else "#e6edf3")),
        "quote_fg": str(c.get("FG2") or ("#4b5563" if is_light else "#cbd5e1")),
        "link": str(c.get("accent_hex") or c.get("FG_BLUE") or "#38bdf8"),
    }


class MarkdownDocument(QObject):
    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._jobs = AsyncJobs(self, limit=2)
        self._closed = False
        self._generation = 0
        self._path = ""
        self._source = ""
        self._title = "Markdown"
        self._blocks = RowsModel(parent=self)
        self._headings = []
        self._stats = {"headingsCount": 0, "words": 0, "lines": 0, "readMinutes": 0}
        self._error = ""
        self._busy = False
        self._ready = False
        self._theme = _markdown_theme(None)

    @Property(QObject, constant=True)
    def blocks(self):
        return self._blocks

    @Property("QVariantList", notify=changed)
    def headings(self):
        return self._headings

    @Property(str, notify=changed)
    def title(self):
        return self._title

    @Property("QVariantMap", notify=changed)
    def stats(self):
        return self._stats

    @Property(str, notify=changed)
    def source(self):
        return self._source

    @Property(str, notify=changed)
    def error(self):
        return self._error

    @Property(bool, notify=changed)
    def busy(self):
        return self._busy

    @Property(bool, notify=changed)
    def ready(self):
        return self._ready

    @Property(str, notify=changed)
    def path(self):
        return self._path

    @Slot(str, "QVariantMap")
    @Slot(str, "QVariantMap", str)
    def load(self, path, colors, override_text=""):
        if self._closed or not path:
            return
        self._path = path
        self._theme = _markdown_theme(colors)
        self._generation += 1
        generation = self._generation
        self._busy = True
        self._error = ""
        has_override = override_text is not None and override_text != ""
        self.changed.emit()

        def work():
            if has_override:
                doc = render_markdown(override_text, path=path, theme=self._theme)
                return True, (override_text, doc)
            payload = render_markdown_file(path, theme=self._theme)
            return True, (payload.get("source", ""), payload.get("document", {}))

        def finished(ok, result):
            if self._closed or generation != self._generation:
                return
            self._busy = False
            if ok:
                source_text, doc = result
                self._source = source_text
                self._title = doc.get("title") or os.path.basename(path)
                self._headings = doc.get("headings") or []
                self._stats = doc.get("stats") or {"headingsCount": 0, "words": 0, "lines": 0, "readMinutes": 0}
                rows = [{**block, "key": str(idx)} for idx, block in enumerate(doc.get("blocks") or [])]
                self._blocks.replace(rows, animate=False)
                self._ready = True
            else:
                self._error = failure_summary(result) or str(result)
                self._ready = False
            self.changed.emit()

        self._jobs.submit("markdown", work, None, finished, report_operation_errors=False)

    @Slot("QVariantMap")
    def refreshTheme(self, colors):
        if self._closed or not self._path:
            return
        theme = _markdown_theme(colors)
        if theme == self._theme:
            return
        self._theme = theme
        if not self._ready:
            return
        self._generation += 1
        generation = self._generation
        source_snapshot = self._source
        path_snapshot = self._path

        def work():
            return True, render_markdown(source_snapshot, path=path_snapshot, theme=theme)

        def finished(ok, doc):
            if self._closed or generation != self._generation or not ok:
                return
            self._title = doc.get("title") or os.path.basename(path_snapshot)
            self._headings = doc.get("headings") or []
            self._stats = doc.get("stats") or self._stats
            rows = [{**block, "key": str(idx)} for idx, block in enumerate(doc.get("blocks") or [])]
            self._blocks.replace(rows, animate=False)
            self.changed.emit()

        self._jobs.submit("markdown-theme", work, None, finished, report_operation_errors=False)

    @Slot(str, result=str)
    def resolveImageUrl(self, raw_url):
        url = str(raw_url or "").strip()
        if not url:
            return ""
        if url.startswith(("file://", "qrc:/", "image://")):
            return url
        if url.startswith(("http://", "https://", "data:")):
            return ""
        base_dir = os.path.dirname(self._path) if self._path else ""
        candidate = url if os.path.isabs(url) else (os.path.normpath(os.path.join(base_dir, url)) if base_dir else "")
        if candidate and os.path.isfile(candidate):
            return QUrl.fromLocalFile(candidate).toString()
        return ""

    @Slot()
    def close(self):
        self._closed = True
        self._jobs.stop()
