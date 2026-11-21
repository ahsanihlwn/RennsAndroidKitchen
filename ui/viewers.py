# SPDX-License-Identifier: MPL-2.0
"""Data for workspace viewers, with no QWidget presentation."""
import os

from PySide6.QtCore import QObject, Property, Signal, Slot
from services.async_jobs import AsyncJobs
from ui.rows import RowsModel
from core.file_types import TEXT_EXT,AUDIO_EXT,VIDEO_EXT,ARCHIVE_EXT

IMAGE_EXT = (".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp", ".ico", ".tiff", ".tif", ".svg")


def viewer_kind(path):
    ext = os.path.splitext(path)[1].lower()
    if ext in IMAGE_EXT: return "image"
    if ext in AUDIO_EXT: return "audio"
    if ext in VIDEO_EXT: return "video"
    if ext in (".ofp", ".ops", ".ozip", ".kdz", ".dz", ".cpb", ".pac") or os.path.basename(path).upper() == "UPDATE.APP":
        from core.oem_containers import OEM_CONTAINER_KINDS, detect_oem_kind
        try:
            if detect_oem_kind(path) in OEM_CONTAINER_KINDS:
                return "oem"
        except OSError:
            pass
    if ext in ARCHIVE_EXT: return "archive"
    if ext in ('.so','.elf','.exe','.dll','.dylib','.o') or '.so.' in os.path.basename(path): return 'elf'
    if ext in (".bin", ".fsm", ".ccu", ".blob", ".img", ".romfs", ".sqsh", ".squashfs"):
        from core.header_magic import check_magic
        try: magic = check_magic(path)
        except OSError: magic = ""
        if magic == "PAYLOAD": return "payload"
        if magic == "SUPER": return "super"
        from core.oem_containers import OEM_CONTAINER_KINDS
        if magic in OEM_CONTAINER_KINDS: return "oem"
        from core.binary_core import identify
        try:
            if identify(path): return 'elf'
        except OSError:pass
        if ext != ".img":
            return "hex"
    name=os.path.basename(path)
    if ext in TEXT_EXT or ext in ('.json','.jsonc','.kt','.kts','.smali','.rs','.php','.php3','.php4','.php5','.phtml','.qss','.h','.hpp','.hxx','.hh','.sh','.bash','.zsh','.fish','.ksh','.csh','.tcsh','.dash') or name.endswith(('_file_contexts','_fs_config','_fs_options','_info')):
        return "editor"
    if os.path.isfile(path):
        from core.binary_core import identify
        try:
            if identify(path):return 'elf'
        except OSError:pass
    return "choose"


def _inspect(spec):
    path, kind = spec
    if kind == "hex":
        from core.bin_analyzer import detect_binary_format, format_hex_lines, read_hex_chunk
        header = detect_binary_format(path)
        rows = format_hex_lines(read_hex_chunk(path, 0, 65536))
        return True, {"summary": str(header), "rows": rows}
    if kind == "elf":
        from core.so_core import analyze_file, result_to_jsonable
        result = result_to_jsonable(analyze_file(path))
        return True, {"summary": str(result.get("header", {})), "rows": result.get("sections", [])}
    if kind == "payload":
        from core.payload_tool import list_partitions
        partitions = list_partitions(path)
        return True, {"rows": [{"name": name} for name in partitions]}
    if kind == "super":
        from core.super_logic import read_super_info
        info = read_super_info(path)
        return True, {"rows": [{"name": name} for name in info.partitions]}
    if kind == "archive":
        from ui.archive import list_archive
        return True, {"rows": list_archive(path)}
    return True, {"rows": []}


class ViewerDocument(QObject):
    changed = Signal()
    def __init__(self, parent=None):
        super().__init__(parent)
        self._jobs = AsyncJobs(self, limit=1)
        self._rows = RowsModel(parent=self)
        self._path = ""
        self._summary = ""
        self._error = ""
        self._busy = False

    @Property(QObject, constant=True)
    def rows(self): return self._rows
    @Property(str, notify=changed)
    def summary(self): return self._summary
    @Property(str, notify=changed)
    def error(self): return self._error
    @Property(bool, notify=changed)
    def busy(self): return self._busy

    @Slot(str, str)
    def load(self, path, kind):
        if self._path == path: return
        self._path = path
        self._busy = True
        self._error = ""
        def finished(ok, result):
            self._busy = False
            if ok:
                self._summary = result.get("summary", "")
                self._rows.replace([{**row, "key": str(index)} for index, row in enumerate(result.get("rows", []))], animate=False)
            else: self._error = str(result)
            self.changed.emit()
        self._jobs.submit("inspect", _inspect, (path, kind), finished)
        self.changed.emit()

    @Slot()
    def close(self): self._jobs.stop()
