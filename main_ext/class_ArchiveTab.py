# SPDX-License-Identifier: MPL-2.0
import os
import zipfile
import traceback

try:
    import rarfile
except ImportError:
    rarfile = None

try:
    import py7zr
except ImportError:
    py7zr = None

from PySide6.QtCore import Qt, QSize, QMimeData
from PySide6.QtGui import QIcon, QPainter, QDrag, QTransform
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QTreeWidget, QTreeWidgetItem, QSplitter,
)
from main_ext.class_ThemeUi import theme as _th
from core.threader import run_in_thread

print("[MAIN STAGE] ARCHIVETAB")


def _fmt_size(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    for unit in ("KB", "MB", "GB"):
        n /= 1024.0
        if n < 1024:
            return f"{n:.1f} {unit}"
    return f"{n:.1f} GB"


def _detect_archive_type(filepath: str):
    ext = filepath.lower()

    if ext.endswith(".pac"):
        from core.pac_lister import list_pac
        listing = list_pac(filepath)
        return "pac", [entry.archive_path for entry in listing.entries]

    if ext.endswith(".tar.gz") or ext.endswith(".tgz"):
        import tarfile
        with tarfile.open(filepath, "r:gz") as tf:
            return "tar.gz", tf.getnames()

    if ext.endswith(".tar.bz2") or ext.endswith(".tbz2"):
        import tarfile
        with tarfile.open(filepath, "r:bz2") as tf:
            return "tar.bz2", tf.getnames()

    if ext.endswith(".tar.xz") or ext.endswith(".txz"):
        import tarfile
        with tarfile.open(filepath, "r:xz") as tf:
            return "tar.xz", tf.getnames()

    if ext.endswith(".tar.lz4"):
        try:
            import lz4.frame
            import tarfile, io
            with open(filepath, "rb") as fh:
                raw = lz4.frame.decompress(fh.read())
            with tarfile.open(fileobj=io.BytesIO(raw)) as tf:
                return "tar.lz4", tf.getnames()
        except ImportError:
            return "tar.lz4", [os.path.basename(filepath)]

    if ext.endswith(".tar"):
        import tarfile
        with tarfile.open(filepath, "r:") as tf:
            return "tar", tf.getnames()

    if ext.endswith(".zst") or ext.endswith(".zstd"):
        return "zstd", [os.path.basename(filepath).removesuffix(".zst").removesuffix(".zstd")]

    if ext.endswith(".gz"):
        return "gz", [os.path.basename(filepath)[:-3]]

    if ext.endswith(".bz2"):
        return "bz2", [os.path.basename(filepath)[:-4]]

    if ext.endswith(".xz"):
        return "xz", [os.path.basename(filepath)[:-3]]

    if ext.endswith(".lz4"):
        return "lz4", [os.path.basename(filepath)[:-4]]

    if ext.endswith(".zip") or ext.endswith(".jar") or ext.endswith(".apk"):
        with zipfile.ZipFile(filepath, "r") as z:
            return "zip", z.namelist()

    if ext.endswith(".rar"):
        if rarfile is None:
            raise RuntimeError("rarfile not installed: pip install rarfile")
        with rarfile.RarFile(filepath) as r:
            return "rar", r.namelist()

    if ext.endswith(".7z"):
        if py7zr is None:
            raise RuntimeError("py7zr not installed: pip install py7zr")
        with py7zr.SevenZipFile(filepath, mode="r") as z:
            return "7z", z.getnames()

    return None, []


class _ArchiveTree(QTreeWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        from icons.handler import icon_pixmap
        _ap  = icon_pixmap("arrow_right")
        _sz  = 9
        self._arrow_closed = _ap.scaled(_sz, _sz, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self._arrow_open   = self._arrow_closed.transformed(QTransform().rotate(90), Qt.SmoothTransformation)

    def drawBranches(self, painter, rect, index):
        item = self.itemFromIndex(index)
        if not item:
            return

        bg = self.palette().color(self.backgroundRole())
        painter.save()
        painter.fillRect(rect, bg)
        painter.restore()

        has_children = (
            item.childCount() > 0 or
            item.childIndicatorPolicy() == QTreeWidgetItem.ShowIndicator
        )
        if not has_children:
            return

        indent    = self.indentation()
        slot_left = rect.right() - indent
        pix = self._arrow_open if item.isExpanded() else self._arrow_closed

        x = slot_left + (indent - pix.width()) // 2
        y = rect.y()  + (rect.height() - pix.height()) // 2

        painter.save()
        painter.setOpacity(0.8)
        painter.drawPixmap(x, y, pix)
        painter.restore()


class _StatusBar(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(24)
        self.setStyleSheet(f"""
            QWidget {{
                background: {_th.BG2};
                border-top: 1px solid {_th.border_color(0.06)};
            }}
        """)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 0, 10, 0)
        layout.setSpacing(0)

        self._lbl = QLabel()
        self._lbl.setStyleSheet(f"color: {_th.FG3}; font-size: 10px; background: transparent; border: none;")
        layout.addWidget(self._lbl)
        layout.addStretch()

    def set_text(self, text: str):
        self._lbl.setText(text)


from main_ext.class_DeferredCloseViewer import DeferredCloseViewer


class ArchiveTab(DeferredCloseViewer, QWidget):
    def __init__(self, filepath, filetree_icons):
        super().__init__()

        self.filepath     = filepath
        self.unsaved      = False
        self.icons        = filetree_icons
        self.archive      = None
        self.archive_type = None
        self.pac_listing  = None

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.tree = _ArchiveTree()
        self.tree.setDragEnabled(True)
        self.tree.setSelectionMode(QTreeWidget.ExtendedSelection)
        self.tree.setDefaultDropAction(Qt.CopyAction)
        self.tree.setHeaderHidden(True)
        self.tree.setIndentation(14)
        self.tree.setAnimated(True)
        self.tree.setStyleSheet(f"""
            QTreeWidget {{
                font-size: 13px;
                color: {_th.FG};
                border: none;
                outline: none;
                padding: 0px;
            }}
            QTreeWidget::item {{
                height: 20px;
                padding: 4px;
                border: none;
                color: {_th.FG};
            }}
            QTreeWidget::item:hover:!selected {{
                background: {_th.border_color(0.06)};
            }}
            QTreeWidget::item:selected {{
                background: {_th.accent(0.35)};
                color: {_th.FG_BRIGHT};
            }}
            QTreeWidget::item:selected:hover {{
                background: {_th.accent(0.42)};
            }}
        """)

        _sb_style = f"""
            QScrollBar:vertical {{
                background: transparent; width: 4px; margin: 0;
            }}
            QScrollBar::handle:vertical {{
                background: {_th.border_color(0.35)};
                border-radius: 2px; min-height: 20px;
            }}
            QScrollBar::handle:vertical:hover {{ background: {_th.border_color(0.55)}; }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
        """
        _sbh_style = f"""
            QScrollBar:horizontal {{
                background: transparent; height: 4px; margin: 0;
            }}
            QScrollBar::handle:horizontal {{
                background: {_th.border_color(0.35)};
                border-radius: 2px; min-width: 20px;
            }}
            QScrollBar::handle:horizontal:hover {{ background: {_th.border_color(0.55)}; }}
            QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}
            QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{ background: transparent; }}
        """
        self.tree.verticalScrollBar().setStyleSheet(_sb_style)
        self.tree.horizontalScrollBar().setStyleSheet(_sbh_style)

        root.addWidget(self.tree, 1)

        self._status = _StatusBar()
        root.addWidget(self._status)

        self.load_archive(filepath)
        self.tree.itemSelectionChanged.connect(self._on_selection_changed)

    def load_archive(self, filepath):
        def worker():
            try:
                archive_type, files = _detect_archive_type(filepath)
            except Exception as e:
                return False, e

            if archive_type is None:
                return False, None

            archive = None
            pac_listing = None
            if archive_type == "zip":
                archive = zipfile.ZipFile(filepath, "r")
            elif archive_type == "rar":
                if rarfile is not None:
                    archive = rarfile.RarFile(filepath)
            elif archive_type == "pac":
                from core.pac_lister import list_pac
                pac_listing = list_pac(filepath)

            tree = {}
            for path in files:
                parts = path.strip("/").split("/")
                cur = tree
                for p in parts:
                    if p not in cur:
                        cur[p] = {}
                    cur = cur[p]

            return True, (archive_type, archive, pac_listing, tree, len(files))

        def after(ok, payload):
            if not ok:
                if payload is None:
                    self._status.set_text("Format tidak dikenali")
                else:
                    self._status.set_text(f"Error: {payload}")
                return

            archive_type, archive, pac_listing, tree, n_files = payload
            self.archive_type = archive_type
            self.archive      = archive
            self.pac_listing  = pac_listing

            self.populate_tree(self.tree, tree)
            self._status.set_text(f"{n_files} entries  ·  {archive_type.upper()}")

        self.mark_task_started()
        run_in_thread(worker, None, callback=self.guard_task_callback(after))

    def _icon_for(self, name: str, is_folder: bool):
        ic = self.icons
        if is_folder:
            return ic.get("folder", QIcon())
        ext = os.path.splitext(name)[1].lower()

        if any(name.endswith(s) for s in ("_file_contexts", "_fs_config", "_fs_options", "_info")):
            return ic.get("linux_meta", ic.get("file", QIcon()))

        mapping = {
            ".img":  "drive",
            ".bin":  "bin",
            ".apk":  "apk",
            ".jar":  "archive_jar",
            ".7z":   "archive_7z",
            ".rar":  "archive_rar",
            ".gz":   "archive_gz",
            ".iso":  "archive_iso",
            ".tar":  "archive_tar", ".tgz": "archive_tar",
            ".bz2":  "archive_tar", ".xz":  "archive_tar",
            ".lz":   "archive_tar", ".lz4": "archive_tar", ".zst": "archive_tar",
            ".zip":  "archive",
            ".py":   "py",
            ".cpp":  "cpp",  ".cc":  "cpp",  ".cxx": "cpp",
            ".c":    "c",
            ".h":    "h",
            ".hpp":  "hpp",  ".hxx": "hpp",  ".hh":  "hpp",
            ".cs":   "cs",
            ".java": "java",
            ".js":   "js",   ".ts":  "js",   ".jsx": "js",  ".tsx": "js",
            ".html": "html", ".htm": "html",
            ".css":  "css",
            ".qss":  "qss",
            ".xml":  "xml",  ".xsl": "xml",  ".rss": "xml",
            ".json": "json", ".jsonc": "json",
            ".cfg":  "cfg",  ".conf": "cfg",  ".ini": "cfg",
            ".csv":  "csv",
            ".prop": "prop", ".properties": "prop",
            ".rc":   "rc",
            ".rs":   "rust",
            ".php":  "php",
            ".kt":   "kt",   ".kts": "kt",
            ".smali":"smali",
            ".sh":   "sh",   ".bash": "sh",  ".ksh": "sh",
            ".zsh":  "zsh",
            ".fish": "fish",
            ".swift":"swift",
            ".asm":  "asm",  ".s":   "asm",
            ".txt":  "txt",  ".md":  "txt",  ".log": "txt",
            ".png":  "image",".jpg": "image",".jpeg":"image",
            ".gif":  "image",".webp":"image",".bmp": "image",
            ".svg":  "image",".ico": "image",
            ".mp3":  "audio",".wav": "audio",".ogg": "audio",".flac":"audio",
            ".mp4":  "video",".mkv": "video",".avi": "video",".mov": "video",
        }
        key = mapping.get(ext)
        if key and key in ic:
            return ic[key]
        return ic.get("file", QIcon())

    def populate_tree(self, parent, struct):
        folders = sorted(k for k, v in struct.items() if v)
        files   = sorted(k for k, v in struct.items() if not v)

        def _add(name, is_folder):
            item = QTreeWidgetItem([name])
            item.setIcon(0, self._icon_for(name, is_folder))
            if isinstance(parent, QTreeWidget):
                parent.addTopLevelItem(item)
            else:
                parent.addChild(item)
            return item

        for f in folders:
            item = _add(f, True)
            self.populate_tree(item, struct[f])
        for f in files:
            _add(f, False)

    def _on_selection_changed(self):
        selected = self.tree.selectedItems()
        if not selected:
            self._status.set_text(f"{self.tree.topLevelItemCount()} entries  ·  {(self.archive_type or '').upper()}")
            return
        if len(selected) == 1:
            self._status.set_text(self.full_path(selected[0]))
        else:
            self._status.set_text(f"{len(selected)} dipilih")

    def full_path(self, item):
        parts = []
        while item:
            parts.insert(0, item.text(0))
            item = item.parent()
        return "/".join(parts)

    def _pac_entries(self):
        if self.pac_listing is None:
            from core.pac_lister import list_pac
            self.pac_listing = list_pac(self.filepath)
        return self.pac_listing.entries

    def _pac_entry_by_path(self, internal_path: str):
        for entry in self._pac_entries():
            if entry.archive_path == internal_path:
                return entry
        return None

    def _extract_pac_entry_to(self, entry, out_path: str):
        if not entry.is_file:
            return None
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        remaining  = entry.size
        chunk_size = 1024 * 1024
        with open(self.filepath, "rb") as src, open(out_path, "wb") as dst:
            src.seek(entry.data_offset)
            while remaining > 0:
                chunk = src.read(min(chunk_size, remaining))
                if not chunk:
                    raise RuntimeError(f"PAC payload ended early: {entry.file_name}")
                dst.write(chunk)
                remaining -= len(chunk)
        return out_path

    def _read_member(self, internal_path: str) -> bytes:
        at = self.archive_type

        if at == "zip":
            return self.archive.read(internal_path)
        if at == "rar":
            return self.archive.read(internal_path)
        if at == "7z":
            if py7zr is None:
                raise RuntimeError("py7zr not installed")
            with py7zr.SevenZipFile(self.filepath, mode="r") as z:
                data_map = z.read([internal_path])
            bio = data_map.get(internal_path)
            if bio is None:
                raise RuntimeError(f"7z: member not found: {internal_path}")
            return bio.read() if hasattr(bio, "read") else bytes(bio)

        if at in ("tar", "tar.gz", "tar.bz2", "tar.xz"):
            import tarfile
            mode_map = {"tar": "r:", "tar.gz": "r:gz", "tar.bz2": "r:bz2", "tar.xz": "r:xz"}
            with tarfile.open(self.filepath, mode_map[at]) as tf:
                m = tf.getmember(internal_path)
                f = tf.extractfile(m)
                if f is None:
                    raise RuntimeError(f"tar: cannot extract: {internal_path}")
                return f.read()

        if at == "tar.lz4":
            import lz4.frame, tarfile, io
            with open(self.filepath, "rb") as fh:
                raw = lz4.frame.decompress(fh.read())
            with tarfile.open(fileobj=io.BytesIO(raw)) as tf:
                m = tf.getmember(internal_path)
                f = tf.extractfile(m)
                if f is None:
                    raise RuntimeError(f"tar.lz4: cannot extract: {internal_path}")
                return f.read()

        if at == "zstd":
            import zstandard as zstd
            with open(self.filepath, "rb") as fh:
                return zstd.ZstdDecompressor().decompress(fh.read(), max_length=64 * 1024 * 1024)

        if at == "gz":
            import gzip
            with gzip.open(self.filepath, "rb") as fh:
                return fh.read(4 * 1024 * 1024)

        if at == "bz2":
            import bz2
            with bz2.open(self.filepath, "rb") as fh:
                return fh.read(4 * 1024 * 1024)

        if at == "xz":
            import lzma
            with lzma.open(self.filepath, "rb") as fh:
                return fh.read(4 * 1024 * 1024)

        if at == "lz4":
            import lz4.frame
            with open(self.filepath, "rb") as fh:
                return lz4.frame.decompress(fh.read())

        raise RuntimeError(f"Unsupported archive type: {at}")

    def extract_one_to(self, internal_path: str, target_dir: str):
        from core.archive_safety import validate_archive_member_names
        os.makedirs(target_dir, exist_ok=True)
        members_to_extract = self._resolve_members(internal_path)
        if not members_to_extract:
            return None


        validate_archive_member_names(members_to_extract)

        if self.archive_type == "pac":
            last_path = None
            for member_path in members_to_extract:
                entry = self._pac_entry_by_path(member_path)
                if entry is None or not entry.is_file:
                    continue
                out_path  = os.path.join(target_dir, entry.file_name)
                last_path = self._extract_pac_entry_to(entry, out_path)
            return last_path

        last_path = None
        for member_path in members_to_extract:
            if len(members_to_extract) == 1:
                filename = os.path.basename(member_path) or os.path.basename(self.filepath)
                out_path = os.path.join(target_dir, filename)
            else:
                rel = member_path
                if internal_path and member_path.startswith(internal_path):
                    rel = member_path[len(internal_path):].lstrip("/")
                    folder_name = internal_path.rstrip("/").split("/")[-1]
                    rel = os.path.join(folder_name, rel)
                out_path = os.path.join(target_dir, rel.replace("/", os.sep))

            os.makedirs(os.path.dirname(out_path), exist_ok=True)
            try:
                data = self._read_member(member_path)
                with open(out_path, "wb") as f:
                    f.write(data)
                last_path = out_path
            except Exception as e:
                print(f"[ArchiveTab] extract error for '{member_path}': {e}")
                traceback.print_exc()

        return last_path

    def _resolve_members(self, internal_path: str):
        all_members = self._get_all_member_paths()
        if internal_path in all_members:
            return [internal_path]
        prefix   = internal_path.rstrip("/") + "/"
        children = [m for m in all_members if m.startswith(prefix)]
        return children if children else [internal_path]

    def _get_all_member_paths(self):
        at = self.archive_type

        if at == "zip":
            return [n for n in self.archive.namelist() if not n.endswith("/")]
        if at == "pac":
            return [entry.archive_path for entry in self._pac_entries()]
        if at == "rar" and self.archive:
            return [n for n in self.archive.namelist() if not n.endswith("/")]
        if at == "7z":
            if py7zr is None:
                return []
            with py7zr.SevenZipFile(self.filepath, mode="r") as z:
                return z.getnames()
        if at in ("tar", "tar.gz", "tar.bz2", "tar.xz"):
            import tarfile
            mode_map = {"tar": "r:", "tar.gz": "r:gz", "tar.bz2": "r:bz2", "tar.xz": "r:xz"}
            with tarfile.open(self.filepath, mode_map[at]) as tf:
                return [m.name for m in tf.getmembers() if m.isfile()]
        return []

    def get_selected_paths(self):
        return [self.full_path(item) for item in self.tree.selectedItems() if self.full_path(item)]

    def startDrag(self, supportedActions):
        paths = self.get_selected_paths()
        if not paths:
            return
        from PySide6.QtGui import QDrag
        from PySide6.QtCore import QMimeData
        mime = QMimeData()
        mime.setData("application/x-rdt-archive", "\n".join(paths).encode("utf-8"))
        drag = QDrag(self.tree)
        drag.setMimeData(mime)
        drag.exec(Qt.CopyAction)
