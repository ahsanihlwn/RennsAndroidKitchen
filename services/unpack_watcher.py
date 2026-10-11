# SPDX-License-Identifier: MPL-2.0
import os
import time
import weakref
import shiboken6
from PySide6.QtCore import QObject, QTimer, Qt
from core.native_backend import directory_snapshots
from services.async_jobs import AsyncJobs


def _snapshot_work(arg):
    return True, directory_snapshots(*arg)


def _paths_match(path1: str | None, path2: str | None) -> bool:
    if not path1 or not path2:
        return False
    if path1 == path2:
        return True
    return os.path.normcase(os.path.normpath(path1)) == os.path.normcase(os.path.normpath(path2))


class _FolderTask:
    __slots__ = (
        "folder", "tree_ref", "initial_base", "version", "final", "cancelled",
        "phase", "locator_stack", "found_folder_handle", "found_parent_handle",
        "collector_stack", "opened"
    )

    def __init__(self, folder: str, tree, version: int, final: bool):
        self.folder = folder
        self.tree_ref = weakref.ref(tree)
        base = getattr(tree, "base", None)
        self.initial_base = str(base) if base is not None else None
        self.version = version
        self.final = final
        self.cancelled = False
        self.phase = "locate"
        self.locator_stack: list[tuple[tuple, int]] = []
        self.found_folder_handle = None
        self.found_parent_handle = None
        self.collector_stack: list[tuple[tuple, int]] = []
        self.opened: dict[str, tuple] = {}


class UnpackWatcher(QObject):


    INTERVAL_MS = 5000  # 5 detik
    MAX_NODES_PER_CHUNK = 128  # Bounded Qt tree rows visited per turn (~4ms)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._timer = QTimer(self)
        self._timer.setInterval(self.INTERVAL_MS)
        self._timer.timeout.connect(self._on_tick)

        # {normpath(output_folder): FileTree reference}
        self._jobs: dict[str, object] = {}
        self._refs: dict[str, int] = {}
        # {normpath(output_folder): fs_type string ("EROFS", "EXT4", etc)}
        self._fs_types: dict[str, str] = {}
        # Hidden apktool workspaces need event suppression, but have no visible
        # row for the 5-second poll to refresh.
        self._no_ui_refresh: set[str] = set()
        self._snapshots: dict[tuple[str, str], tuple] = {}
        self._snapshot_jobs = AsyncJobs(self, limit=1)
        self._folder_serial: dict[str, int] = {}
        self._stopped = False

        # Persistent handles cache and active incremental tasks
        self._folder_cache: dict[str, tuple] = {}
        self._parent_cache: dict[str, tuple] = {}
        self._active_tasks: dict[str, _FolderTask] = {}


    def register(self, output_folder: str, tree, fs_type: str = "",
                 refresh_ui: bool = True) -> None:

        norm = os.path.normpath(output_folder)
        if self._stopped:
            return
        if norm in self._jobs:
            self._refs[norm] += 1
            if refresh_ui:
                self._no_ui_refresh.discard(norm)
            return
        self._jobs[norm] = tree
        self._folder_serial[norm] = self._folder_serial.get(norm, 0) + 1
        self._refs[norm] = 1
        if refresh_ui:
            self._no_ui_refresh.discard(norm)
        else:
            self._no_ui_refresh.add(norm)
        if fs_type:
            self._fs_types[norm] = fs_type
        print(f"[UnpackWatcher] register: {norm}  (fs_type={fs_type}, total jobs: {len(self._jobs)})")

        if not self._timer.isActive():
            self._timer.start()
            print(f"[UnpackWatcher] timer STARTED (interval={self.INTERVAL_MS}ms)")

    def unregister(self, output_folder: str) -> None:

        norm = os.path.normpath(output_folder)
        if self._refs.get(norm, 0) > 1:
            self._refs[norm] -= 1
            return
        self._refs.pop(norm, None)
        self._folder_serial[norm] = self._folder_serial.get(norm, 0) + 1
        tree = self._jobs.pop(norm, None)
        self._fs_types.pop(norm, None)
        refresh_ui = norm not in self._no_ui_refresh
        self._no_ui_refresh.discard(norm)

        # Invalidate in-flight collector for this folder
        old_task = self._active_tasks.pop(norm, None)
        if old_task is not None:
            old_task.cancelled = True

        self._parent_cache.pop(norm, None)
        self._snapshots = {key: value for key, value in self._snapshots.items() if key[0] != norm}

        print(f"[UnpackWatcher] unregister: {norm}  (remaining jobs: {len(self._jobs)})")

        # Final refresh — supaya tree menampilkan state terakhir
        if tree is not None and refresh_ui:
            self._refresh_if_expanded(norm, tree, final=True)
        self._snapshots = {key: value for key, value in self._snapshots.items() if key[0] != norm}

        # Stop timer kalau tidak ada job lagi
        if not self._jobs and self._timer.isActive():
            self._timer.stop()
            print("[UnpackWatcher] timer STOPPED (no more jobs)")

    def get_fs_type(self, path: str) -> str:

        norm = os.path.normpath(path)
        return self._fs_types.get(norm, "")

    def is_watched(self, path: str) -> bool:

        norm = os.path.normpath(path)
        for watched_folder in self._jobs:
            if norm == watched_folder or norm.startswith(watched_folder + os.sep):
                return True
        return False

    def has_jobs(self) -> bool:

        return bool(self._jobs)

    def is_collecting(self, folder: str | None = None) -> bool:

        if folder is not None:
            norm = os.path.normpath(folder)
            task = self._active_tasks.get(norm)
            return task is not None and not task.cancelled
        return any(not t.cancelled for t in self._active_tasks.values())

    def collector_busy(self, folder: str | None = None) -> bool:

        return self.is_collecting(folder)

    def stop(self):

        self._timer.stop()
        self._stopped = True
        self._snapshot_jobs.stop()
        for task in self._active_tasks.values():
            task.cancelled = True
        self._active_tasks.clear()
        self._folder_cache.clear()
        self._parent_cache.clear()
        self._jobs.clear()
        self._refs.clear()
        self._fs_types.clear()
        self._no_ui_refresh.clear()
        self._snapshots.clear()
        self._folder_serial.clear()


    def _on_tick(self):

        if not self._jobs:
            return

        # Snapshot keys dulu — supaya aman kalau dict berubah di tengah jalan
        for folder, tree in list(self._jobs.items()):
            if folder not in self._no_ui_refresh:
                self._refresh_if_expanded(folder, tree)

    def _refresh_if_expanded(self, folder: str, tree, *, final=False) -> None:

        if self._stopped or getattr(tree, "_background_stopped", False) or not shiboken6.isValid(tree):
            return

        norm = os.path.normpath(folder)

        # Coalesce concurrent collections per folder
        active = self._active_tasks.get(norm)
        if active is not None and not active.cancelled:
            if not final:
                return
            active.cancelled = True
            self._active_tasks.pop(norm, None)

        version = self._folder_serial.get(norm, 0)
        task = _FolderTask(norm, tree, version, final)
        self._active_tasks[norm] = task

        # Quick cache resolution for folder handle
        cached_handle = self._folder_cache.get(norm)
        if cached_handle is not None:
            item = tree._directory_item(cached_handle)
            if item is not None:
                if not item.isExpanded() and item is not tree.invisibleRootItem():
                    self._active_tasks.pop(norm, None)
                    return
                task.phase = "collect"
                task.found_folder_handle = cached_handle
                fpath = item.data(0, Qt.UserRole)
                if fpath:
                    task.opened[os.path.normpath(fpath)] = tree._directory_handle(item)
                task.collector_stack.append((tree._directory_handle(item), 0))
            else:
                self._folder_cache.pop(norm, None)
                for k in [k for k in list(self._snapshots) if (isinstance(k, tuple) and k and k[0] == norm) or k == norm]:
                    self._snapshots.pop(k, None)

        self._step_task(norm)

    def _step_task(self, folder: str, expected_task=None) -> None:
        task = self._active_tasks.get(folder)
        if expected_task is not None and task is not expected_task:
            return

        if self._stopped:
            self._active_tasks.pop(folder, None)
            return

        if task is None or task.cancelled:
            return

        if task.version != self._folder_serial.get(folder, 0):
            self._active_tasks.pop(folder, None)
            return

        tree = task.tree_ref()
        if tree is None or not shiboken6.isValid(tree) or getattr(tree, "_background_stopped", False):
            self._active_tasks.pop(folder, None)
            return

        if getattr(tree, "base", None) != task.initial_base:
            self._active_tasks.pop(folder, None)
            return

        budget = getattr(self, "MAX_NODES_PER_CHUNK", 128)
        visited = 0
        deadline = time.perf_counter() + 0.004

        # PHASE 1: LOCATE
        if task.phase == "locate":
            parent_path = os.path.dirname(folder)

            if not task.locator_stack:
                cached_parent = self._parent_cache.get(folder)
                p_item = tree._directory_item(cached_parent) if cached_parent else None
                if p_item is not None:
                    task.found_parent_handle = cached_parent
                    task.locator_stack.append((cached_parent, 0))
                else:
                    root = tree.invisibleRootItem() if getattr(tree, "display_mode", "tree") == "single_dir" else tree.topLevelItem(0)
                    if root is None:
                        self._active_tasks.pop(folder, None)
                        return
                    rpath = root.data(0, Qt.UserRole)
                    if rpath:
                        if _paths_match(rpath, parent_path):
                            task.found_parent_handle = tree._directory_handle(root)
                        if _paths_match(rpath, folder):
                            task.found_folder_handle = tree._directory_handle(root)
                    if task.found_folder_handle is None:
                        task.locator_stack.append((tree._directory_handle(root), 0))

            while task.locator_stack and visited < budget and time.perf_counter() < deadline:
                top_handle, next_idx = task.locator_stack[-1]
                node = tree._directory_item(top_handle)
                if node is None:
                    task.locator_stack.pop()
                    continue
                if next_idx >= node.childCount():
                    task.locator_stack.pop()
                    continue

                child = node.child(next_idx)
                task.locator_stack[-1] = (top_handle, next_idx + 1)
                visited += 1

                cpath = child.data(0, Qt.UserRole)
                if cpath:
                    if task.found_parent_handle is None and _paths_match(cpath, parent_path):
                        task.found_parent_handle = tree._directory_handle(child)
                    if _paths_match(cpath, folder):
                        task.found_folder_handle = tree._directory_handle(child)
                        task.locator_stack.clear()
                        break

                if child.childCount() > 0:
                    task.locator_stack.append((tree._directory_handle(child), 0))

            if task.locator_stack and task.found_folder_handle is None:
                QTimer.singleShot(1, self, lambda f=folder, t=task: self._step_task(f, t))
                return

            if task.found_folder_handle is None:
                self._folder_cache.pop(folder, None)
                parent_handle = task.found_parent_handle or self._parent_cache.get(folder)
                if parent_handle is not None:
                    self._parent_cache[folder] = parent_handle
                    parent_item = tree._directory_item(parent_handle)
                    if parent_item is not None and (parent_item.isExpanded() or parent_item is tree.invisibleRootItem()):
                        tree.refresh_folder(parent_item)
                self._active_tasks.pop(folder, None)
                return

            self._folder_cache[folder] = task.found_folder_handle
            if task.found_parent_handle is not None:
                self._parent_cache[folder] = task.found_parent_handle

            folder_item = tree._directory_item(task.found_folder_handle)
            if folder_item is None or (not folder_item.isExpanded() and folder_item is not tree.invisibleRootItem()):
                self._active_tasks.pop(folder, None)
                return

            task.phase = "collect"
            fpath = folder_item.data(0, Qt.UserRole)
            if fpath:
                task.opened[os.path.normpath(fpath)] = tree._directory_handle(folder_item)
            task.collector_stack.append((tree._directory_handle(folder_item), 0))

        # PHASE 2: COLLECT
        if task.phase == "collect":
            root_handle = task.found_folder_handle or self._folder_cache.get(folder)
            if root_handle is not None:
                root_item = tree._directory_item(root_handle)
                if root_item is None or (not root_item.isExpanded() and root_item is not tree.invisibleRootItem()):
                    self._active_tasks.pop(folder, None)
                    return

            while task.collector_stack and visited < budget and time.perf_counter() < deadline:
                top_handle, next_idx = task.collector_stack[-1]
                node = tree._directory_item(top_handle)
                if node is None:
                    task.collector_stack.pop()
                    continue
                if node is not tree.invisibleRootItem() and not node.isExpanded():
                    task.collector_stack.pop()
                    continue
                if next_idx >= node.childCount():
                    task.collector_stack.pop()
                    continue

                child = node.child(next_idx)
                task.collector_stack[-1] = (top_handle, next_idx + 1)
                visited += 1

                if child.isExpanded():
                    cpath = child.data(0, Qt.UserRole)
                    if cpath:
                        task.opened[os.path.normpath(cpath)] = tree._directory_handle(child)
                    if child.childCount() > 0:
                        task.collector_stack.append((tree._directory_handle(child), 0))

            if task.collector_stack:
                QTimer.singleShot(1, self, lambda f=folder, t=task: self._step_task(f, t))
                return

            root_handle = task.found_folder_handle or self._folder_cache.get(folder)
            if root_handle is not None:
                root_item = tree._directory_item(root_handle)
                if root_item is None or (not root_item.isExpanded() and root_item is not tree.invisibleRootItem()):
                    self._active_tasks.pop(folder, None)
                    return

            self._active_tasks.pop(folder, None)
            opened = task.opened
            version = task.version
            final = task.final
            initial_base = str(task.initial_base) if task.initial_base is not None else None
            previous = {path: self._snapshots[(folder, path)] for path in opened
                        if (folder, path) in self._snapshots}
            self._snapshot_jobs.submit(folder, _snapshot_work, (list(opened), previous),
                lambda ok, payload, ib=initial_base: self._apply_snapshots(folder, version, tree, opened, ok, payload, initial_base=ib, final=final))

    def _apply_snapshots(self, folder, version, tree, opened, ok, payload, initial_base=None, *, final=False):
        if self._stopped or not shiboken6.isValid(tree) or getattr(tree, "_background_stopped", False):
            return
        if version != self._folder_serial.get(folder, 0):
            return
        current_base = getattr(tree, "base", None)
        current_base_str = str(current_base) if current_base is not None else None
        if initial_base is None or current_base_str != initial_base:
            return
        if not ok:
            print(f"[UnpackWatcher] snapshot failed for {folder}: {payload}")
            return
        for path in payload["missing"]:
            self._snapshots.pop((folder, path), None)
        if not final:
            for path, signature in payload["snapshots"].items():
                self._snapshots[(folder, path)] = signature
        else:
            self._folder_cache.pop(folder, None)
            self._parent_cache.pop(folder, None)
        for path in payload["changed"]:
            handle = opened.get(path) or opened.get(os.path.normpath(path))
            if handle is None:
                continue
            current = tree._directory_item(handle)
            if current is not None and (current.isExpanded() or current is tree.invisibleRootItem()):
                # Incremental reconciliation preserves descendants, so each
                # changed expanded directory needs its own fresh listing.
                tree.refresh_folder(current)
