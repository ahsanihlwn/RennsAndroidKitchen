# SPDX-License-Identifier: MPL-2.0
from __future__ import annotations

import copy
import json
import math
import os
import tempfile
import threading
import time
import uuid

from core.app_paths import PROJECT_DATA_DIR
from core import windows_recycle


RETENTION_SECONDS = 7 * 24 * 60 * 60
TRASH_KEY = "__project_trash__"


def _read(path):
    try:
        with open(path, encoding="utf-8") as stream:
            value = json.load(stream)
    except FileNotFoundError:
        return []
    if not isinstance(value, list) or any(not isinstance(row, dict) for row in value):
        raise ValueError(f"Invalid project metadata: {path}")
    return value


def _write(path, rows):
    parent = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".project-state-", suffix=".json", dir=parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(rows, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _key(path):
    return os.path.normcase(os.path.abspath(path))


class ProjectTrash:
    def __init__(self, directory=PROJECT_DATA_DIR, *, backend=windows_recycle, clock=time.time):
        self.manifest_path = os.path.join(directory, "trash.json")
        self.projects_path = os.path.join(directory, "projects.json")
        self.backend = backend
        self.clock = clock
        self._operation_lock = threading.Lock()
        self._state_lock = threading.RLock()

    def entries(self):
        with self._state_lock:
            rows = _read(self.manifest_path)
        identifiers = set()
        for row in rows:
            identifier = row.get("id")
            project = row.get("project")
            if (not isinstance(identifier, str) or not identifier or identifier in identifiers
                    or not isinstance(project, dict) or not isinstance(project.get("path"), str)
                    or not project["path"]
                    or any(not isinstance(row.get(field), (int, float)) or not math.isfinite(row[field])
                           for field in ("deleted_at", "expires_at"))
                    or row["expires_at"] < row["deleted_at"]):
                raise ValueError(f"Invalid project Trash metadata: {self.manifest_path}")
            identifiers.add(identifier)
        # Persisted order is newest deletion first, independent of disk filters.
        return sorted(copy.deepcopy(rows), key=lambda row: row.get("deleted_at", 0), reverse=True)

    def _save_entry(self, entry):
        with self._state_lock:
            rows = self.entries()
            rows = [row for row in rows if row["id"] != entry["id"]]
            rows.append(entry)
            _write(self.manifest_path, rows)

    def _forget(self, identifier):
        with self._state_lock:
            rows = self.entries()
            _write(self.manifest_path, [row for row in rows if row["id"] != identifier])

    def _remove_active(self, project):
        with self._state_lock:
            rows = _read(self.projects_path)
            target = _key(project["path"])
            retained = [row for row in rows if _key(row.get("path", "")) != target]
            if retained != rows:
                _write(self.projects_path, retained)

    def _add_active(self, entry):
        with self._state_lock:
            rows = _read(self.projects_path)
            target = _key(entry["project"]["path"])
            if not any(_key(row.get("path", "")) == target for row in rows):
                index = max(0, min(len(rows), int(entry.get("original_index", len(rows)))))
                order = entry.get("original_order", [])
                if target in order:
                    position = order.index(target)
                    active = {_key(row["path"]): index for index, row in enumerate(rows)}
                    following = next((path for path in order[position + 1:] if path in active), None)
                    preceding = next((path for path in reversed(order[:position]) if path in active), None)
                    if following is not None:
                        index = active[following]
                    elif preceding is not None:
                        index = active[preceding] + 1
                rows.insert(index, entry["project"])
                _write(self.projects_path, rows)

    def _original_order(self, projects):

        order = [_key(project["path"]) for project in projects]
        for entry in reversed(self.entries()):
            snapshot = entry.get("original_order", [_key(entry["project"]["path"])])
            for position, path in enumerate(snapshot):
                if path in order:
                    continue
                following = next((key for key in snapshot[position + 1:] if key in order), None)
                preceding = next((key for key in reversed(snapshot[:position]) if key in order), None)
                index = (order.index(following) if following is not None else
                         order.index(preceding) + 1 if preceding is not None else len(order))
                order.insert(index, path)
        return order

    def recycle(self, project):
        with self._operation_lock:
            path = project["path"]
            with self._state_lock:
                projects = _read(self.projects_path)
                original_index = next((index for index, row in enumerate(projects)
                                       if _key(row.get("path", "")) == _key(path)), len(projects))
            now = self.clock()
            entry = {"id": uuid.uuid4().hex, "project": copy.deepcopy(project),
                     "original_index": original_index, "original_order": self._original_order(projects), "deleted_at": now,
                     "expires_at": now + RETENTION_SECONDS, "state": "recycling"}
            # Journal metadata before touching files; never delete first and
            # then discover that the Trash manifest cannot be saved.
            self._save_entry(entry)
            captured = []

            def record(handle):
                captured.append(handle)
                entry.update(handle=handle, state="ready")
                self._save_entry(entry)

            try:
                handle = self.backend.recycle(path, on_recycled=record)
                if not captured:
                    record(handle)
            except Exception:
                if captured and not os.path.exists(path):
                    # If journaling failed after Windows moved the folder,
                    # recover it before reporting failure to the UI.
                    self.backend.restore(captured[-1], path)
                if os.path.exists(path):
                    self._forget(entry["id"])
                raise
            self._remove_active(project)
            return entry

    def restore(self, identifier):
        with self._operation_lock:
            entry = next((row for row in self.entries() if row["id"] == identifier), None)
            if entry is None or not entry.get("handle"):
                raise ValueError("This project is no longer available in Trash.")
            path = entry["project"]["path"]
            # Preserve both folders when a new project occupies the old path.
            if os.path.lexists(path):
                raise FileExistsError("The original location is occupied. Move or rename that folder before restoring.")
            entry["state"] = "restoring"
            self._save_entry(entry)
            try:
                self.backend.restore(entry["handle"], path)
            except Exception:
                entry["state"] = "ready"
                self._save_entry(entry)
                raise
            self._add_active(entry)
            self._forget(identifier)
            return entry["project"]

    @staticmethod
    def _drive_available(path):
        drive, _ = os.path.splitdrive(os.path.abspath(path))
        return os.path.isdir(drive + os.sep) if drive else os.path.isdir(os.path.dirname(path))

    def delete(self, identifier):

        with self._operation_lock:
            entry = next((row for row in self.entries() if row["id"] == identifier), None)
            if entry is None or not entry.get("handle"):
                raise ValueError("This project is no longer available in Trash.")
            self.backend.validate_handle(entry["handle"])
            self.backend.purge(entry["handle"])
            self._forget(identifier)
            return entry["project"]

    def maintain(self):

        errors = []
        with self._operation_lock:
            for entry in self.entries():
                try:
                    path = entry["project"]["path"]
                    if not self._drive_available(path):
                        continue
                    handle = entry.get("handle")
                    if not handle:


                        if os.path.isdir(path):
                            self._add_active(entry)
                            self._forget(entry["id"])
                        continue
                    self.backend.validate_handle(handle)
                    recycled_path = handle["filesystem_path"]
                    if not os.path.exists(recycled_path):
                        if os.path.isdir(path):
                            self._add_active(entry)
                        self._forget(entry["id"])
                        continue
                    # Repair interrupted active-metadata removal first.
                    if not os.path.exists(path):
                        self._remove_active(entry["project"])
                    if self.clock() >= entry["expires_at"]:
                        self.backend.purge(handle)
                        self._forget(entry["id"])
                except Exception as exc:
                    errors.append((entry.get("id"), str(exc)))
        return errors
