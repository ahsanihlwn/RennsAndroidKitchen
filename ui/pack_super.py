# SPDX-License-Identifier: MPL-2.0
"""Native Pack Super dialog controller shared with the workspace task queue."""
from __future__ import annotations

import os

from PySide6.QtCore import Property, QObject, QUrl, Signal, Slot

from core import flashable_size, super_logic, ui_lang
from core.command_context import OperationError
from core.workspace_files import WorkspaceFiles
from services.async_jobs import AsyncJobs
from ui.rows import RowsModel


def _inspect_partition_image(path: str, badge_hint: str = "") -> dict | None:
    if not path or not os.path.isfile(path):
        return None
    from core import imgfs
    from core.fsdetect import detect_fs_type

    badge = (badge_hint or "").upper().strip()
    fs_kind = ""
    is_sparse = False
    if badge in ("EROFS", "EXT2", "EXT3", "EXT4"):
        fs_kind = badge
    elif badge == "SPARSE":
        is_sparse = True
        inner = imgfs.detect_fs(path)
        if inner not in ("EROFS", "EXT"):
            return None
        fs_kind = f"SPARSE ({inner})"
    else:
        kind = flashable_size.classify(path)
        if kind == "sparse":
            is_sparse = True
            inner = imgfs.detect_fs(path)
            if inner not in ("EROFS", "EXT"):
                return None
            fs_kind = f"SPARSE ({inner})"
        else:
            detected = detect_fs_type(path)
            if detected not in ("EROFS", "EXT2", "EXT3", "EXT4"):
                return None
            fs_kind = detected

    raw_size = super_logic.partition_raw_size(path)
    base, slot = super_logic.strip_image_partition_name(path)
    return {
        "key": os.path.normcase(os.path.normpath(path)),
        "path": path,
        "name": os.path.basename(path),
        "baseName": base,
        "slot": slot or "",
        "fsType": fs_kind,
        "badge": "SPARSE" if is_sparse else ("EROFS" if "EROFS" in fs_kind else "EXT4"),
        "rawSize": int(raw_size),
        "sizeLabel": flashable_size.human_size(raw_size),
        "checked": True,
    }


class PackSuperController(QObject):
    changed = Signal()
    dismissRequested = Signal()

    def __init__(self, workspace, parent=None):
        super().__init__(parent)
        self.workspace = workspace
        self._jobs = AsyncJobs(self, limit=1)
        self._rows = RowsModel(parent=self)
        self._items: list[dict] = []
        self._query = ""
        self._base = ""
        self._output_dir = ""
        self._output_name = "super.img"
        self._super_type = "a_only"
        self._attrib = "readonly"
        self._group_name = "qti_dynamic_partitions"
        self._super_size = "0"
        self._size_manual = False
        self._min_super_size = 0
        self._total_raw_size = 0
        self._sparse = False
        self._delete_sources = False
        self._loading = False
        self._closed = False

    @Property(QObject, constant=True)
    def rows(self):
        return self._rows

    @Property(str, notify=changed)
    def superType(self):
        return self._super_type

    @Property(str, notify=changed)
    def attrib(self):
        return self._attrib

    @Property(str, notify=changed)
    def groupName(self):
        return self._group_name

    @Property(str, notify=changed)
    def superSize(self):
        return self._super_size

    @Property(str, notify=changed)
    def superSizeLabel(self):
        try:
            val = int(self._super_size or "0")
        except ValueError:
            val = 0
        return flashable_size.human_size(val) if val > 0 else "0 B"

    @Property(str, notify=changed)
    def minSuperSizeLabel(self):
        return flashable_size.human_size(self._min_super_size) if self._min_super_size > 0 else "0 B"

    @Property(str, notify=changed)
    def totalRawLabel(self):
        return flashable_size.human_size(self._total_raw_size) if self._total_raw_size > 0 else "0 B"

    @Property(str, notify=changed)
    def outputDir(self):
        return self._output_dir

    @Property(str, notify=changed)
    def outputName(self):
        return self._output_name

    @Property(bool, notify=changed)
    def sparse(self):
        return self._sparse

    @Property(bool, notify=changed)
    def deleteSources(self):
        return self._delete_sources

    @Property(bool, notify=changed)
    def loading(self):
        return self._loading

    @Property(str, notify=changed)
    def searchQuery(self):
        return self._query

    @Property(int, notify=changed)
    def selectedCount(self):
        return sum(1 for item in self._items if item["checked"])

    @Property(bool, notify=changed)
    def allSelected(self):
        return bool(self._items) and all(item["checked"] for item in self._items)

    @Property(bool, notify=changed)
    def hasMinPartitions(self):
        bases = {
            item["baseName"].lower()
            for item in self._items
            if item["checked"] and item.get("baseName")
        }
        return set(super_logic.REQUIRED_SUPER_PARTITIONS).issubset(bases)

    @Property(str, notify=changed)
    def validationHint(self):
        if self._loading:
            return ""
        checked = [item for item in self._items if item["checked"]]
        if not checked:
            return "Select partitions to pack."
        bases = {item["baseName"].lower() for item in checked if item.get("baseName")}
        missing = [p for p in super_logic.REQUIRED_SUPER_PARTITIONS if p not in bases]
        if missing:
            return f"{ui_lang.t('ps_min_hint')} (Missing: {', '.join(missing)})"
        try:
            sz = int(self._super_size or "0")
        except ValueError:
            sz = 0
        if sz <= 0:
            return "Enter a valid super size in bytes."
        if sz < self._min_super_size:
            return f"Super size must be at least {self.minSuperSizeLabel} ({self._min_super_size} bytes)."
        if not (self._output_name or "").strip():
            return "Enter an output filename."
        return ""

    @Property(bool, notify=changed)
    def canPack(self):
        return (not self._loading) and bool(self._items) and self.validationHint == ""

    def _filtered_items(self):
        q = self._query.strip().casefold()
        if not q:
            return [dict(item) for item in self._items]
        return [
            dict(item)
            for item in self._items
            if q in item["name"].casefold()
            or q in item["baseName"].casefold()
            or q in item["fsType"].casefold()
        ]

    def _recompute_sizes(self):
        checked = [item for item in self._items if item["checked"]]
        self._total_raw_size = sum(int(item["rawSize"]) for item in checked)
        if checked:
            specs = [
                {"name": item["name"], "path": item["path"], "size": item["rawSize"]}
                for item in checked
            ]
            self._min_super_size = super_logic.calculate_min_super_size(
                specs, super_type=self._super_type
            )
            if not self._size_manual:
                self._super_size = str(super_logic.suggest_super_size(self._min_super_size))
        else:
            self._min_super_size = 0
            if not self._size_manual:
                self._super_size = "0"

    def _sync_rows(self):
        self._recompute_sizes()
        self._rows.replace(self._filtered_items(), animate=False)
        self.changed.emit()

    @Slot('QVariantList', str)
    def prepare(self, paths, base):
        self._base = str(base or "")
        self._query = ""
        self._items = []
        self._rows.replace([], animate=False)
        self._attrib = "readonly"
        self._group_name = "qti_dynamic_partitions"
        self._output_name = "super.img"
        self._sparse = False
        self._delete_sources = False
        self._size_manual = False
        self._super_size = "0"
        self._min_super_size = 0
        self._total_raw_size = 0
        self._loading = True
        self._output_dir = ""
        self.changed.emit()

        raw_paths = [str(p) for p in (paths or []) if p]
        badges = {}
        tree = getattr(self.workspace, "active_tree", None)
        files = WorkspaceFiles(self._base) if self._base else None
        resolved_pairs = []
        for p in raw_paths:
            phys = p
            if files is not None:
                try:
                    resolved, _ = files.view.resolve(p)
                    if resolved:
                        phys = resolved
                except Exception:
                    pass
            badge = ""
            if tree is not None:
                node = tree._nodes.get(p)
                if node is not None:
                    badge = (node.data(0, 257) or "")
            badges[phys] = badge
            resolved_pairs.append(phys)

        if resolved_pairs:
            self._output_dir = os.path.dirname(os.path.abspath(resolved_pairs[0]))

        def work():
            inspected = []
            seen = set()
            for phys in resolved_pairs:
                row = _inspect_partition_image(phys, badges.get(phys, ""))
                if row and row["key"] not in seen:
                    seen.add(row["key"])
                    inspected.append(row)
            return True, inspected

        def done(ok, result):
            if self._closed:
                return
            self._loading = False
            if ok:
                self._items = result
                has_slots = any(item["slot"] in ("a", "b") for item in self._items)
                self._super_type = "virtual_ab" if has_slots else "a_only"
                self._size_manual = False
                self._sync_rows()
            else:
                self.workspace.message(str(result), ui_lang.t("ps_title"))
                self.changed.emit()

        self._jobs.submit("prepare", work, None, done)

    @Slot('QVariantList')
    def addFiles(self, urls):
        paths = [
            u.toLocalFile() if isinstance(u, QUrl) else QUrl(str(u)).toLocalFile()
            for u in (urls or [])
        ]
        paths = [p for p in paths if p]
        if not paths:
            return

        def work():
            added = []
            for p in paths:
                row = _inspect_partition_image(p, "")
                if row:
                    added.append(row)
            return True, added

        def done(ok, result):
            if self._closed:
                return
            if not ok:
                self.workspace.message(str(result), ui_lang.t("ps_title"))
                return
            existing = {item["key"] for item in self._items}
            new_rows = [row for row in result if row["key"] not in existing]
            if not new_rows:
                return
            self._items.extend(new_rows)
            if not self._output_dir:
                self._output_dir = os.path.dirname(os.path.abspath(new_rows[0]["path"]))
            self._sync_rows()

        self._jobs.submit("add", work, None, done)

    @Slot(str)
    def setSearchQuery(self, query):
        q = str(query or "")
        if q != self._query:
            self._query = q
            self._rows.replace(self._filtered_items(), animate=False)
            self.changed.emit()

    @Slot(str, bool)
    def toggleKey(self, key, checked):
        for item in self._items:
            if item["key"] == key:
                item["checked"] = bool(checked)
                break
        self._sync_rows()

    @Slot(bool)
    def setSelectAll(self, checked):
        flag = bool(checked)
        for item in self._items:
            item["checked"] = flag
        self._sync_rows()

    @Slot(str)
    def setSuperType(self, value):
        if value in ("a_only", "virtual_ab", "ab") and value != self._super_type:
            self._super_type = value
            self._sync_rows()

    @Slot(str)
    def setAttrib(self, value):
        val = "none" if value == "none" else "readonly"
        if val != self._attrib:
            self._attrib = val
            self.changed.emit()

    @Slot(str)
    def setGroupName(self, value):
        text = str(value or "").strip()
        if text != self._group_name:
            self._group_name = text
            self.changed.emit()

    @Slot(str)
    def setSuperSize(self, value):
        digits = "".join(ch for ch in str(value or "") if ch.isdigit())
        self._size_manual = True
        if digits != self._super_size:
            self._super_size = digits
            self.changed.emit()
        else:
            self.changed.emit()

    @Slot()
    def recalcSize(self):
        self._size_manual = False
        self._sync_rows()

    @Slot(str)
    def setOutputName(self, value):
        text = str(value or "").strip()
        if text != self._output_name:
            self._output_name = text
            self.changed.emit()

    @Slot(bool)
    def setSparse(self, value):
        flag = bool(value)
        if flag != self._sparse:
            self._sparse = flag
            self.changed.emit()

    @Slot(bool)
    def setDeleteSources(self, value):
        flag = bool(value)
        if flag != self._delete_sources:
            self._delete_sources = flag
            self.changed.emit()

    @Slot()
    def pack(self):
        if not self.canPack:
            return
        checked = [dict(item) for item in self._items if item["checked"]]
        out_name = (self._output_name or "super.img").strip()
        if os.path.isabs(out_name):
            out_path = os.path.abspath(out_name)
        else:
            out_dir = self._output_dir or self._base or os.path.dirname(checked[0]["path"])
            out_path = os.path.abspath(os.path.join(out_dir, out_name))

        out_norm = os.path.normcase(os.path.normpath(out_path))
        if any(os.path.normcase(os.path.normpath(item["path"])) == out_norm for item in checked):
            self.workspace.message(
                "Output file cannot overwrite one of the input partition images.",
                ui_lang.t("ps_title"),
            )
            return

        super_type = self._super_type
        attrib = self._attrib
        group_name = (self._group_name or "qti_dynamic_partitions").strip() or "qti_dynamic_partitions"
        super_size = int(self._super_size)
        sparse = self._sparse
        delete_sources = self._delete_sources
        total_raw = max(1024 * 1024, self._total_raw_size)

        def start_pack():
            self.dismissRequested.emit()
            from core.terminal_execution import library_operation
            from core.threader_control import get_progress_reporter
            from ui.workspace_actions import operation

            part_specs = [
                {"name": item["name"], "path": item["path"], "size": item["rawSize"]}
                for item in checked
            ]
            targets = tuple([out_path, *(item["path"] for item in checked)])

            def worker():
                cb = get_progress_reporter()
                with library_operation(f"rak repack_super {out_path}") as output:
                    ok, detail = super_logic.pack_super(
                        part_specs,
                        out_path,
                        super_type=super_type,
                        group_name=group_name,
                        super_size=super_size,
                        attrib=attrib,
                        sparse=sparse,
                        delete_sources=delete_sources,
                        progress=cb,
                    )
                    if not ok:
                        output(str(detail) + "\n")
                        output.failed = True
                        raise OperationError(str(detail))
                    mode_tag = "sparse" if sparse else "raw"
                    output(
                        f"Packed {len(part_specs)} partition(s) [{super_type}, {mode_tag}] -> {detail}\n"
                    )
                    return True, detail

            def settled(_ok, _res):
                out_parent = os.path.dirname(out_path)
                for d in getattr(self.workspace, "directories", ()):
                    if d.base:
                        parent_node = d._nodes.get(out_parent) or d._nodes.get(d.base)
                        if parent_node is not None:
                            d._scan(parent_node, refresh=True)
                        else:
                            d.refresh()

            operation(
                self.workspace,
                "Repack SUPER",
                worker,
                settled=settled,
                lane="heavy",
                targets=targets,
                weight=float(total_raw),
                member_label=os.path.basename(out_path),
            )

        if os.path.exists(out_path):
            self.workspace.message(
                f"{os.path.basename(out_path)} already exists in {os.path.dirname(out_path)}.\nOverwrite it?",
                ui_lang.t("ps_title"),
                confirm=start_pack,
                danger=True,
                accept="Overwrite",
            )
            return

        start_pack()

    def close(self):
        self._closed = True
        self._jobs.stop()
