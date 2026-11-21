# SPDX-License-Identifier: MPL-2.0
import os
from PySide6.QtCore import QObject, Property, Signal, Slot, QUrl
from core.file_picker import _quick_access, _get_drives, _file_size_str
from services.async_jobs import AsyncJobs
from ui.file_icons import file_icon
from ui.rows import RowsModel


class LocalFilePicker(QObject):
    changed = Signal()
    locationsChanged = Signal()
    chosen = Signal(str)
    chosenUrls = Signal("QVariantList")

    def __init__(self, parent, *, extensions=None, multi_select=False):
        super().__init__(parent)
        self._extensions = tuple(extensions) if extensions is not None else None
        self._multi_select = bool(multi_select)
        self._jobs = AsyncJobs(self)
        self._rows = RowsModel(parent=self)
        self._directory = os.path.expanduser("~")
        self._query = ""
        self._selected = ""
        self._selected_paths = []
        self._anchor = ""
        self._all = []
        self._error = ""
        self._locations = []
        self._locations_requested = False

    @Property(QObject, constant=True)
    def rows(self):
        return self._rows

    @Property(str, notify=changed)
    def directory(self):
        return self._directory

    @Property(str, notify=changed)
    def selected(self):
        return self._selected

    @Property("QStringList", notify=changed)
    def selectedPaths(self):
        return list(self._selected_paths)

    @Property(str, notify=changed)
    def selectedLabel(self):
        if not self._selected_paths:
            return ""
        if len(self._selected_paths) == 1:
            return self._selected_paths[0]
        return f"{len(self._selected_paths)} files selected"

    @Property(str, notify=changed)
    def error(self):
        return self._error

    @Property("QVariantList", notify=locationsChanged)
    def locations(self):
        return self._locations

    @Slot(QUrl, result=str)
    def localPath(self, url):
        if isinstance(url, QUrl):
            return url.toLocalFile()
        return QUrl(str(url)).toLocalFile() if url else ""

    @Slot()
    def refreshLocations(self):
        # QML reads this property while constructing the still-hidden picker.
        # Filesystem discovery belongs to an explicit open, never a getter.
        self._locations_requested = True

        def work():
            rows = [{"header": "QUICK ACCESS", "path": "", "name": "", "kind": ""}]
            rows.extend({"name": name, "path": path, "header": "", "kind": "folder"} for name, path in _quick_access())
            rows.append({"header": "DRIVES", "path": "", "name": "", "kind": ""})
            rows.extend({"name": path, "path": path, "header": "", "kind": "drive"} for path in _get_drives())
            return True, rows

        def done(ok, result):
            if ok:
                self._locations = result
                self.locationsChanged.emit()

        self._jobs.submit("locations", work, None, done)

    @Slot(str)
    def load(self, path):
        if not path or not os.path.isdir(path):
            return
        if not self._locations_requested:
            self.refreshLocations()
        self._directory = os.path.abspath(path)
        self._selected = ""
        self._selected_paths = []
        self._anchor = ""
        self._query = ""
        self._error = ""
        self.changed.emit()

        extensions = self._extensions

        def work():
            rows = []
            with os.scandir(path) as entries:
                for entry in entries:
                    if entry.name.startswith("."):
                        continue
                    folder = entry.is_dir()
                    if not folder and extensions is not None:
                        if os.path.splitext(entry.name)[1].lower() not in extensions:
                            continue
                    icon = "folder" if folder else ("archive" if extensions is not None else file_icon(entry.name))
                    rows.append({
                        "key": entry.path,
                        "name": entry.name,
                        "path": entry.path,
                        "folder": folder,
                        "icon": icon,
                        "size": "" if folder else _file_size_str(entry.path),
                    })
            return True, sorted(rows, key=lambda row: (not row["folder"], row["name"].lower()))

        def done(ok, result):
            self._all = result if ok else []
            self._error = "" if ok else "⚠ Permission denied"
            self.filter("")
            self.changed.emit()

        self._jobs.submit("directory", work, None, done)

    @Slot(str)
    def filter(self, text):
        self._query = text
        self._rows.replace([row for row in self._all if text.lower() in row["name"].lower()], animate=False)

    @Slot(str)
    @Slot(str, bool, bool)
    def select(self, path, multi=None, range_select=None):
        if not path:
            return
        if multi is None or range_select is None:
            from PySide6.QtCore import Qt
            from PySide6.QtGui import QGuiApplication
            mods = QGuiApplication.keyboardModifiers()
            if multi is None:
                multi = bool(mods & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.MetaModifier))
            if range_select is None:
                range_select = bool(mods & Qt.KeyboardModifier.ShiftModifier)
        if not self._multi_select:
            self._selected = path
            self._selected_paths = [path]
            self._anchor = path
            self.changed.emit()
            return
        visible = [row["path"] for row in self._rows.snapshot()]
        if range_select and self._anchor in visible and path in visible:
            a, b = visible.index(self._anchor), visible.index(path)
            lo, hi = (a, b) if a <= b else (b, a)
            span = [p for p in visible[lo:hi + 1] if os.path.isfile(p)]
            if multi:
                merged = list(dict.fromkeys(self._selected_paths + span))
            else:
                merged = span
            self._selected_paths = merged
        elif multi:
            if path in self._selected_paths:
                self._selected_paths = [p for p in self._selected_paths if p != path]
            elif os.path.isfile(path):
                self._selected_paths = self._selected_paths + [path]
            self._anchor = path
        else:
            self._selected_paths = [path] if os.path.isfile(path) else []
            self._anchor = path
        self._selected = self._selected_paths[-1] if self._selected_paths else ""
        self.changed.emit()

    @Slot(str)
    def activate(self, path):
        if not path:
            return
        if os.path.isdir(path):
            self.load(path)
        elif os.path.isfile(path):
            self.chosen.emit(path)
            self.chosenUrls.emit([QUrl.fromLocalFile(path).toString()])

    @Slot()
    def openSelected(self):
        if not self._multi_select:
            if self._selected:
                self.activate(self._selected)
            return
        targets = [p for p in self._selected_paths if os.path.isfile(p)]
        if not targets:
            return
        self.chosen.emit(targets[0])
        self.chosenUrls.emit([QUrl.fromLocalFile(p).toString() for p in targets])

    @Slot()
    def up(self):
        self.load(os.path.dirname(self._directory))

    @Slot(result=bool)
    def canOpen(self):
        if self._multi_select:
            return any(os.path.isfile(p) for p in self._selected_paths)
        return os.path.isfile(self._selected)

    def close(self):
        self._jobs.stop()


class RenxecPicker(LocalFilePicker):
    def __init__(self, parent):
        super().__init__(parent, extensions=(".renxec", ".zip"), multi_select=False)


class ImportFilePicker(LocalFilePicker):
    def __init__(self, parent):
        super().__init__(parent, extensions=None, multi_select=True)
