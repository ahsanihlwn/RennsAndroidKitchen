# SPDX-License-Identifier: MPL-2.0
"""Project metadata shared by the Quick launcher and the existing workspace."""
from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile

from core.app_paths import PROJECT_DATA_DIR
from core.project_trash import ProjectTrash, _read, _write, _key


def _write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=".launcher-", suffix=".json", dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


_ACTIVE_STORES: list["ProjectStore"] = []


def find_project_record(path: str) -> dict | None:
    """Find a project record by path across active stores or central projects.json."""
    if not path:
        return None
    req_key = _key(path)
    for store in reversed(_ACTIVE_STORES):
        try:
            for row in store.projects():
                if isinstance(row, dict) and _key(row.get("path", "")) == req_key:
                    return row
        except Exception:
            pass

    default_file = Path(PROJECT_DATA_DIR) / "projects.json"
    if default_file.is_file():
        try:
            for row in _read(default_file):
                if isinstance(row, dict) and _key(row.get("path", "")) == req_key:
                    return row
        except Exception:
            pass
    return None


class ProjectStore:
    def __init__(self, directory=PROJECT_DATA_DIR):
        self.directory = Path(directory)
        self.projects_path = self.directory / "projects.json"
        self.location_path = self.directory / "default_location.json"
        self.trash = ProjectTrash(str(self.directory))
        _ACTIVE_STORES.append(self)

    def projects(self):
        rows = _read(self.projects_path)
        if any(not isinstance(row.get("path"), str) or not row["path"]
               or not isinstance(row.get("project_name"), str)
               or not isinstance(row.get("port1"), str) for row in rows):
            raise ValueError(f"Invalid project metadata: {self.projects_path}")
        return rows

    def default_location(self):
        try:
            value = json.loads(self.location_path.read_text(encoding="utf-8")).get("path", "")
        except FileNotFoundError:
            return ""
        return os.path.normpath(value) if isinstance(value, str) and os.path.isabs(value) else ""

    def resolved_location(self):
        return self.default_location() or str(Path.home() / "Documents")

    def set_location(self, path):
        if path and not os.path.isabs(path):
            raise ValueError("Choose an absolute output directory.")
        _write_json(self.location_path, {"path": os.path.normpath(path) if path else ""})

    @staticmethod
    def _component(value):
        value = str(value).strip()
        # Each field is a folder name, never a path outside its project.
        if (not value or value in (".", "..") or value.endswith((".", " "))
                or any(char in value for char in '<>:"/\\|?*')
                or any(ord(char) < 32 for char in value)
                or value.split(".")[0].upper() in {"CON", "PRN", "AUX", "NUL",
                    *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}):
            raise ValueError(f"Invalid folder name: {value!r}")
        return value

    def create(self, arguments, *, location=None):
        rows = self.projects()  # Validate metadata before touching the filesystem.
        name = self._component(arguments.get("name", ""))
        port1 = self._component(arguments.get("port1", ""))
        mode = arguments.get("mode", 1)
        if mode not in (1, 2):
            raise ValueError("Choose single or dual ROM mode.")
        port2 = self._component(arguments.get("port2", "")) if mode == 2 else ""
        if port2 and port1.casefold() == port2.casefold():
            raise ValueError("The ROM folders must have different names.")
        base = location or self.resolved_location()
        if not os.path.isabs(base):
            raise ValueError("Choose an absolute output directory.")
        root = os.path.join(base, name)
        if any(_key(row["path"]) == _key(root) for row in rows) or os.path.lexists(root):
            raise FileExistsError(f"A project or folder already exists at {root}")
        target1 = arguments.get("target1") or None
        target2 = arguments.get("target2") if mode == 2 else None
        if target1:
            target1 = os.path.normpath(str(target1).strip())
            if not target1 or not os.path.isdir(target1):
                target1 = None
        if target2:
            target2 = os.path.normpath(str(target2).strip())
            if not target2 or not os.path.isdir(target2):
                target2 = None

        from core.project_config import setup_project_container
        setup_project_container(root, name, mode, port1, port2, target1, target2)
        record = {"project_name": name, "path": root, "port1": port1,
                  "port2": port2, "mode": mode}
        if target1:
            record["port1_target"] = target1
        if target2:
            record["port2_target"] = target2
        _write(self.projects_path, [*rows, record])
        return record

    def toggle_pin(self, path):
        rows = self.projects()
        target = next((row for row in rows if _key(row["path"]) == _key(path)), None)
        if target is None:
            raise FileNotFoundError(path)
        if not target.get("pinned") and sum(bool(row.get("pinned")) for row in rows) >= 3:
            raise ValueError("pin_limit")
        target["pinned"] = not target.get("pinned", False)
        _write(self.projects_path, rows)

    def update_cached_size(self, path, size_str):
        rows = self.projects()
        target = next((row for row in rows if _key(row["path"]) == _key(path)), None)
        if target is None:
            return None
        if target.get("cached_size") == size_str:
            return target
        target["cached_size"] = size_str
        _write(self.projects_path, rows)
        return target

    def recycle_missing_projects(self, *, drive_available=None):
        """Move projects whose root folder does not exist on an available drive to Trash.

        Removable/external drives that are currently disconnected/offline are skipped
        so that unplugged storage is never treated as deleted.
        """
        rows = self.projects()
        available = self.trash._drive_available if drive_available is None else drive_available
        missing = []
        for row in rows:
            path = row.get("path")
            if not path or not isinstance(path, str):
                continue
            if not available(path):
                continue
            if not os.path.isdir(path):
                missing.append(row)

        recycled = []
        for row in missing:
            try:
                entry = self.trash.recycle(row)
                recycled.append(entry)
            except Exception:
                pass
        return recycled

    def import_project(self, folder_path):
        folder_path = os.path.normpath(str(folder_path).strip())
        if not os.path.isdir(folder_path):
            raise FileNotFoundError(f"Folder not found: {folder_path}")
        rows = self.projects()
        if any(_key(row["path"]) == _key(folder_path) for row in rows):
            raise ValueError(f"Project is already in the list: {folder_path}")
        name = os.path.basename(folder_path)
        if not name:
            name = folder_path.replace(":", "").replace("\\", "_").replace("/", "_")

        from core.project_config import setup_imported_project
        setup_imported_project(folder_path)

        record = {
            "project_name": name,
            "path": folder_path,
            "port1": name,
            "port2": "",
            "mode": 1,
            "imported": True,
            "port1_target": folder_path,
        }
        _write(self.projects_path, [*rows, record])
        return record

    def remove_from_list(self, path):
        rows = self.projects()
        remaining = [row for row in rows if _key(row["path"]) != _key(path)]
        if len(remaining) != len(rows):
            _write(self.projects_path, remaining)
            return True
        return False
