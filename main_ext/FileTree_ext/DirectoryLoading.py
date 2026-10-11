# SPDX-License-Identifier: MPL-2.0
import os
import time
import shiboken6

from collections import deque
from PySide6.QtCore import QModelIndex, QPersistentModelIndex, QSignalBlocker, QTimer, Qt
from PySide6.QtWidgets import QTreeWidgetItem
from core.directory_scan import scan_tree_directory
from core.symlink import MAX_SIZE, _symlink_cache, invalidate_symlink_cache
from main_ext.class_NameBadgeDelegate import SIZE_LABEL_ROLE, IS_FOLDER_ROLE, _fmt_size
from services.async_jobs import AsyncJobs

ENTRY_FINGERPRINT_ROLE = Qt.UserRole + 96
ENTRY_KIND_ROLE = Qt.UserRole + 97
ICON_PENDING_ROLE = Qt.UserRole + 120
ENTRY_SOURCE_ROLE = Qt.UserRole + 99
EMPTY_ROW_ROLE = Qt.UserRole + 121


class DirectoryLoadingMixin:
    def _init_directory_loading(self):
        self._directory_jobs = AsyncJobs(self, limit=2)
        self._directory_serial = 0
        self._directory_tokens = {}
        self._directory_handles = {}
        self._directory_rendering = set()
        self._directory_last = {}
        self._directory_focus = {}
        self._directory_errors = 0
        self._metadata_refresh = None
        self._icon_queue = deque()
        self._yellow_icon_queue = deque()
        self._icon_generation = 0
        self._paint_barrier_pending = False
        self._paint_update_requested = False
        self._icon_timer = QTimer(self)
        self._icon_timer.setSingleShot(True)
        self._icon_timer.setTimerType(Qt.PreciseTimer)
        self._icon_timer.timeout.connect(self._drain_icon_tasks)

    def directory_work_pending(self):
        return (self._directory_jobs.busy() or bool(self._directory_rendering)
                or self._metadata_refresh is not None)

    def icon_work_pending(self):
        return (
            bool(getattr(self, "_icon_queue", None))
            or bool(getattr(self, "_yellow_icon_queue", None))
            or (hasattr(self, "_icon_timer") and self._icon_timer.isActive())
        )

    def _reset_icon_work(self):
        self._icon_generation += 1
        if hasattr(self, "_icon_timer"):
            self._icon_timer.stop()
        if hasattr(self, "_icon_queue"):
            self._icon_queue.clear()
        if hasattr(self, "_yellow_icon_queue"):
            self._yellow_icon_queue.clear()
        self._paint_barrier_pending = False
        self._paint_update_requested = False

    def _reset_directory_loading(self):
        self._reset_icon_work()
        self._metadata_refresh = None
        self._directory_jobs.reset()
        self._directory_tokens.clear()
        self._directory_handles.clear()
        self._directory_rendering.clear()
        self._directory_last.clear()
        self._directory_focus.clear()
        self._directory_errors = 0

    def _acknowledge_paint_barrier(self):
        if getattr(self, "_background_stopped", False):
            return
        self._paint_update_requested = False
        if getattr(self, "_icon_queue", None) or self.directory_work_pending():
            return
        if getattr(self, "_paint_barrier_pending", False):
            self._paint_barrier_pending = False
            self._paint_update_requested = False
        if getattr(self, "_yellow_icon_queue", None):
            self._start_icon_drain(1)

    def _start_icon_drain(self, delay_ms=1):
        if getattr(self, "_background_stopped", False) or not (
            getattr(self, "_icon_queue", None) or getattr(self, "_yellow_icon_queue", None)
        ):
            return
        if not getattr(self, "_icon_queue", None) and getattr(self, "_yellow_icon_queue", None):
            if self.directory_work_pending():
                return
            if not self.isVisible():
                self._paint_barrier_pending = True
                self._paint_update_requested = False
                return
            if getattr(self, "_paint_barrier_pending", False):
                if not getattr(self, "_paint_update_requested", False):
                    self._paint_update_requested = True
                    self.viewport().update()
                return
        if hasattr(self, "_icon_timer") and not self._icon_timer.isActive():
            self._icon_timer.start(max(1, delay_ms))

    def _drain_icon_tasks(self):
        if getattr(self, "_background_stopped", False):
            return

        if getattr(self, "_icon_queue", None):
            if getattr(self, "_directory_rendering", None):
                self._start_icon_drain(5)
                return

            deadline = time.perf_counter() + 0.004
            processed = 0

            while self._icon_queue and processed < 40 and time.perf_counter() < deadline:
                p_idx, task_gen, task_base, task_source, task_fp, entry = self._icon_queue.popleft()

                if getattr(self, "_background_stopped", False) or task_gen != self._icon_generation or task_base != self.base:
                    continue
                if not p_idx.isValid():
                    continue
                item = self.itemFromIndex(QModelIndex(p_idx))
                if item is None or not shiboken6.isValid(item) or item.treeWidget() is not self:
                    continue
                if not item.data(0, ICON_PENDING_ROLE):
                    continue
                src = item.data(0, ENTRY_SOURCE_ROLE) or item.data(0, Qt.UserRole)
                if src != task_source:
                    continue
                if item.data(0, ENTRY_FINGERPRINT_ROLE) != task_fp:
                    continue
                if item.childIndicatorPolicy() == QTreeWidgetItem.ShowIndicator:
                    item.setData(0, ICON_PENDING_ROLE, False)
                    continue
                if item.data(0, Qt.UserRole + 60) or item.data(0, Qt.UserRole + 71):
                    item.setData(0, ICON_PENDING_ROLE, False)
                    continue

                link = entry.get("symlink") if isinstance(entry, dict) else None
                if link:
                    icon = self.icon_symlink if link.get("is_dir") else self.icon_symlink_file
                    item.setIcon(0, icon)
                else:
                    phys = entry.get("path") or item.data(0, Qt.UserRole + 20) or item.data(0, Qt.UserRole)
                    icon = self._resolve_file_icon(phys)
                    item.setIcon(0, icon)
                    self._schedule_file_thumbnail(item, phys, entry_info=entry)
                item.setData(0, ICON_PENDING_ROLE, False)
                processed += 1

            if processed > 0:
                self._paint_barrier_pending = True
                self._paint_update_requested = False

            if self._icon_queue:
                self._start_icon_drain(1)
            elif self._yellow_icon_queue:
                self._start_icon_drain(1)
            return

        elif getattr(self, "_yellow_icon_queue", None):
            if self.directory_work_pending():
                return
            if not self.isVisible():
                self._paint_barrier_pending = True
                self._paint_update_requested = False
                return
            if getattr(self, "_paint_barrier_pending", False):
                if not getattr(self, "_paint_update_requested", False):
                    self._paint_update_requested = True
                    self.viewport().update()
                return

            deadline = time.perf_counter() + 0.004
            processed = 0

            while self._yellow_icon_queue and processed < 40 and time.perf_counter() < deadline:
                p_idx, task_gen, task_base, task_source, task_fp, entry = self._yellow_icon_queue.popleft()

                if getattr(self, "_background_stopped", False) or task_gen != self._icon_generation or task_base != self.base:
                    continue
                if not p_idx.isValid():
                    continue
                item = self.itemFromIndex(QModelIndex(p_idx))
                if item is None or not shiboken6.isValid(item) or item.treeWidget() is not self:
                    continue
                if not item.data(0, ICON_PENDING_ROLE):
                    continue
                src = item.data(0, ENTRY_SOURCE_ROLE) or item.data(0, Qt.UserRole)
                if src != task_source:
                    continue
                if item.data(0, ENTRY_FINGERPRINT_ROLE) != task_fp:
                    continue
                if item.childIndicatorPolicy() == QTreeWidgetItem.ShowIndicator:
                    item.setData(0, ICON_PENDING_ROLE, False)
                    continue
                if item.data(0, Qt.UserRole + 60) or item.data(0, Qt.UserRole + 71):
                    item.setData(0, ICON_PENDING_ROLE, False)
                    continue

                item.setIcon(0, self.icon_symlink_file)
                item.setData(0, ICON_PENDING_ROLE, False)
                processed += 1

            if self._yellow_icon_queue:
                self._start_icon_drain(1)
            else:
                self._paint_barrier_pending = False
                self._paint_update_requested = False

    def _schedule_metadata_refresh(self, root):

        state = dict(base=self.base, frames=[dict(handle=self._directory_handle(root), child=-1)])
        self._metadata_refresh = state
        self._render_metadata_chunk(state)

    def _render_metadata_chunk(self, state):
        if self._metadata_refresh is not state:
            return
        if self._background_stopped or state["base"] != self.base:
            self._metadata_refresh = None
            return
        deadline, processed = time.perf_counter() + 0.006, 0
        with QSignalBlocker(self):
            while state["frames"] and processed < 60 and time.perf_counter() < deadline:
                frame = state["frames"][-1]
                item = self._directory_item(frame["handle"])
                processed += 1
                if item is None:
                    state["frames"].pop()
                    continue
                if frame["child"] < 0:
                    frame["child"] = 0
                    if not self._update_ctx_perm_item(item):
                        state["frames"].pop()
                        continue
                if frame["child"] >= item.childCount():
                    state["frames"].pop()
                    continue
                child = item.child(frame["child"])
                frame["child"] += 1
                state["frames"].append(dict(handle=self._directory_handle(child), child=-1))
        self.viewport().update()
        if state["frames"]:
            QTimer.singleShot(0, self, lambda: self._render_metadata_chunk(state))
        elif self._metadata_refresh is state:
            self._metadata_refresh = None
            if not self._directory_rendering and (getattr(self, "_icon_queue", None) or getattr(self, "_yellow_icon_queue", None)):
                self._start_icon_drain(1)

    def _directory_handle(self, item):
        path = item.data(0, Qt.UserRole)
        return (item is self.invisibleRootItem(),
                QPersistentModelIndex(self.indexFromItem(item)), path, self.base)

    def _directory_item(self, handle):
        invisible, index, path, base = handle
        if self._background_stopped or self.base != base:
            return None
        item = self.invisibleRootItem() if invisible else (
            self.itemFromIndex(QModelIndex(index)) if index.isValid() else None)
        return item if item is not None and item.data(0, Qt.UserRole) == path else None

    def _request_directory(self, item, *, refresh=False, focus_path=None):
        if self._background_stopped or item is None:
            return
        handle = self._directory_handle(item)
        path = handle[2]
        if not path:
            return
        key = os.path.normcase(os.path.normpath(path))
        focus = self._directory_focus.get(key)
        if focus and focus[0] != handle:
            self._directory_focus.pop(key, None)
        if focus_path:
            self._directory_focus[key] = handle, focus_path
        if not refresh and ((key in self._directory_tokens and self._directory_handles.get(key) == handle) or any(
                item.child(i).data(0, Qt.UserRole) for i in range(item.childCount()))):
            return
        self._directory_serial += 1
        token = self._directory_serial
        self._directory_tokens[key] = token
        self._directory_handles[key] = handle
        self._directory_rendering.discard(key)
        spec = dict(base=self.base, path=path, branch=item.data(0, Qt.UserRole + 20),
                    target=item.data(0, Qt.UserRole + 50) or item.data(0, Qt.UserRole + 71),
                    folder_symlink=bool(item.data(0, Qt.UserRole + 50) and item.data(0, IS_FOLDER_ROLE)),
                    show_apktool_dec=self.show_apktool_dec)
        spec["dropped_badge_paths"] = tuple(getattr(self, "_dropped_badge_paths", ()))
        last = self._directory_last.get(key)
        if last and last[0] == handle:
            spec["previous"] = last[1]
        self._directory_jobs.submit(key, scan_tree_directory, spec,
            lambda ok, payload: self._apply_directory(handle, key, token, ok, payload))

    def _apply_directory(self, handle, key, token, ok, payload):
        parent = self._directory_item(handle)
        if parent is None or self._directory_tokens.get(key) != token:
            if self._directory_tokens.get(key) == token:
                self._directory_tokens.pop(key, None)
                self._directory_handles.pop(key, None)
                self._directory_focus.pop(key, None)
            if not self._directory_rendering and (getattr(self, "_icon_queue", None) or getattr(self, "_yellow_icon_queue", None)):
                self._start_icon_drain(1)
            return
        if not ok:
            self._directory_errors += 1
            self._directory_tokens.pop(key, None)
            self._directory_handles.pop(key, None)
            self._directory_focus.pop(key, None)
            print(f"[FileTree] directory scan failed for {handle[2]}: {payload}")
            if not self._directory_rendering and (getattr(self, "_icon_queue", None) or getattr(self, "_yellow_icon_queue", None)):
                self._start_icon_drain(1)
            return  # Keep previous rows; a later watcher event can retry.
        entries = payload["entries"]


        invalidate_symlink_cache(payload["physical"])
        _symlink_cache.update((os.path.normpath(entry["path"]), entry["symlink"])
            for entry in entries if not entry["is_dir"] and entry["size"] is not None
            and entry["size"] <= MAX_SIZE)
        up_row = int(parent.childCount() > 0 and bool(parent.child(0).data(0, self._SD_UP_ROLE)))
        has_empty = bool(parent.childCount() > up_row and parent.child(up_row).data(0, EMPTY_ROW_ROLE))
        if payload["unchanged"] and (parent.childCount() == len(entries) + up_row if entries else (parent.childCount() == 1 + up_row and has_empty)):
            self._directory_tokens.pop(key, None)
            self._directory_handles.pop(key, None)
            self._finish_directory_focus(handle, key)
            if (getattr(self, "_icon_queue", None) or getattr(self, "_yellow_icon_queue", None)) and not self._directory_rendering:
                self._start_icon_drain(1)
            return
        self._directory_last.pop(key, None)
        wanted = {entry["source"] for entry in entries}
        existing, remove = {}, []
        kept_empty = False
        for i in range(parent.childCount()):
            child = parent.child(i)
            if child.data(0, self._SD_UP_ROLE):
                continue
            if child.data(0, EMPTY_ROW_ROLE):
                if not entries and not kept_empty:
                    kept_empty = True
                    continue
                remove.append(QPersistentModelIndex(self.indexFromItem(child)))
                continue
            source = child.data(0, ENTRY_SOURCE_ROLE) or child.data(0, Qt.UserRole)
            if source in wanted and source not in existing:
                existing[source] = child
            else:
                remove.append(QPersistentModelIndex(self.indexFromItem(child)))
        state = dict(handle=handle, key=key, token=token, payload=payload, entries=entries,
                     existing=existing, remove=remove, offset=0, pending_icons=[])
        self._directory_rendering.add(key)
        self.folder_has_child[os.path.normpath(payload["physical"])] = bool(entries)
        self.child_snapshot[os.path.normpath(payload["physical"])] = {
            os.path.normpath(entry["path"]) for entry in entries if entry["is_dir"]}
        self._render_directory_chunk(state)

    def _finish_directory_focus(self, handle, key):
        focus = self._directory_focus.pop(key, None)
        parent = self._directory_item(handle)
        if not focus or focus[0] != handle or parent is None:
            return
        item = self.find_item_by_path(parent, focus[1])
        if item is not None:
            self.expandItem(parent)
            self.setCurrentItem(item)
            self.scrollToItem(item)

    def _render_directory_chunk(self, state):
        try:
            state["painting_suspended"] = False
            self._render_directory_chunk_impl(state)
        except Exception as error:
            self._directory_errors += 1
            key = state["key"]
            if self._directory_tokens.get(key) == state["token"]:
                self._directory_tokens.pop(key, None)
                self._directory_handles.pop(key, None)
                self._directory_rendering.discard(key)
                self._directory_focus.pop(key, None)
            print(f"[FileTree] directory render failed: {error}")
        finally:
            if state.get("painting_suspended"):
                self.setUpdatesEnabled(True)
                self.viewport().update()

    def _suspend_directory_paint(self, state):
        if not state["painting_suspended"]:
            self.setUpdatesEnabled(False)
            state["painting_suspended"] = True

    def _render_directory_chunk_impl(self, state):
        key, token = state["key"], state["token"]
        parent = self._directory_item(state["handle"])
        if parent is None or self._directory_tokens.get(key) != token:
            if self._directory_tokens.get(key) == token:
                self._directory_rendering.discard(key)
                self._directory_tokens.pop(key, None)
                self._directory_handles.pop(key, None)
                self._directory_focus.pop(key, None)
            return
        deadline = time.perf_counter() + 0.008
        restores = []
        append_batch = []
        batch_candidates = []
        was_expanded = parent.isExpanded()
        row_limit = (
            1
            if (
                state["offset"] == 0
                and not state["existing"]
                and not state["remove"]
                and self.isVisible()
                and parent.isExpanded()
            )
            else 80
        )
        with QSignalBlocker(self):
            # Removal and construction share an 8 ms GUI budget per turn.
            while state["remove"] and time.perf_counter() < deadline:
                self._suspend_directory_paint(state)
                index = state["remove"].pop()
                child = self.itemFromIndex(QModelIndex(index)) if index.isValid() else None
                if child is not None and child.parent() is parent:
                    parent.takeChild(parent.indexOfChild(child))
                elif child is not None and parent is self.invisibleRootItem():
                    parent.takeChild(parent.indexOfChild(child))
            processed = 0
            while not state["remove"] and state["offset"] < len(state["entries"]):
                if processed and (processed >= row_limit or time.perf_counter() >= deadline):
                    break
                entry = state["entries"][state["offset"]]
                source = entry["source"]
                link = entry["symlink"]
                kind = (entry["is_dir"], entry["branch"], link.get("resolved") if link else None,
                        bool(link and link["is_dir"]), entry["collapse"])
                fingerprint = entry["fingerprint"]
                old = state["existing"].get(source)
                if old is not None and (not shiboken6.isValid(old) or old.treeWidget() is not self
                        or parent.indexOfChild(old) < 0):
                    old = None
                # QVariants convert tuples to lists. Store kind as repr for equality.
                kind = repr(kind)
                if old is not None and old.data(0, ENTRY_FINGERPRINT_ROLE) == fingerprint:
                    child = old
                else:
                    self._suspend_directory_paint(state)
                    size_labels = {source: _fmt_size(entry["size"])} if entry["size"] is not None else {}
                    tup = (entry["priority"], entry["name"].lower(), entry["path"],
                           entry["is_dir"], link, source, entry["branch"])
                    child = self._build_single_tree_item(tup, state["payload"]["is_root"],
                            state["payload"]["branch_only"], None, size_labels, entry_info=entry,
                            defer_file_icon=True)
                    if old is not None:
                        if old.data(0, ENTRY_KIND_ROLE) == kind and (entry["is_dir"] or (link and link["is_dir"])):
                            # Keep expanded directory descendants and their live indexes.
                            for col in range(3):
                                old.setText(col, child.text(col))
                                old.setForeground(col, child.foreground(col))
                            old.setIcon(0, child.icon(0))
                            for role in (Qt.UserRole + 1, Qt.UserRole + 9, Qt.UserRole + 20,
                                         Qt.UserRole + 50, Qt.UserRole + 51, SIZE_LABEL_ROLE, IS_FOLDER_ROLE):
                                old.setData(0, role, child.data(0, role))
                            child = old
                        else:
                            expanded = self._save_state_recursive(old)
                            selected = self.currentItem() is old
                            pos = parent.indexOfChild(old)
                            parent.takeChild(pos)
                            parent.insertChild(pos, child)
                            if selected:
                                self.setCurrentItem(child)
                            restores.append((child, expanded))
                    child.setData(0, ENTRY_FINGERPRINT_ROLE, fingerprint)
                    child.setData(0, ENTRY_KIND_ROLE, kind)
                    child.setData(0, ENTRY_SOURCE_ROLE, source)
                position = state["offset"] + int(parent.childCount() > 0 and
                              bool(parent.child(0).data(0, self._SD_UP_ROLE)))
                old_position = parent.indexOfChild(child) if old is not None else -1
                if not state["existing"]:
                    append_batch.append(child)
                elif old_position < 0:
                    parent.insertChild(position, child)
                elif old_position != position:
                    self._suspend_directory_paint(state)
                    parent.takeChild(old_position)
                    parent.insertChild(position, child)
                if state["payload"]["is_root"]:
                    color = state["payload"]["colors"].get(entry["name"])
                    if child.data(0, Qt.UserRole + 10) != color:
                        self._suspend_directory_paint(state)
                        child.setData(0, Qt.UserRole + 10, color)
                if child.data(0, ICON_PENDING_ROLE):
                    batch_candidates.append((child, entry))
                state["offset"] += 1
                processed += 1
            if append_batch:
                parent.addChildren(append_batch)
            if not state["entries"] and not state["remove"]:
                if not any(parent.child(i).data(0, EMPTY_ROW_ROLE) for i in range(parent.childCount())):
                    self._suspend_directory_paint(state)
                    self.add_loading(parent)
            if was_expanded and not parent.isExpanded():
                parent.setExpanded(True)
            for item, ent in batch_candidates:
                idx = self.indexFromItem(item)
                if idx.isValid():
                    p_idx = QPersistentModelIndex(idx)
                    src = item.data(0, ENTRY_SOURCE_ROLE) or item.data(0, Qt.UserRole) or ent["source"]
                    fp = item.data(0, ENTRY_FINGERPRINT_ROLE) or ent["fingerprint"]
                    state["pending_icons"].append((p_idx, self._icon_generation, self.base, src, fp, ent))
        for child, expanded in restores:
            self._restore_state_recursive(child, expanded)
        if state["remove"] or state["offset"] < len(state["entries"]):
            QTimer.singleShot(1, self, lambda: self._render_directory_chunk(state))
        else:
            self._directory_rendering.discard(key)
            self._directory_tokens.pop(key, None)
            self._directory_handles.pop(key, None)
            self._directory_last[key] = state["handle"], state["payload"]["signature"]
            physical = state["payload"]["physical"]
            if physical not in self.watcher.directories() and not self._unpack_watcher.is_watched(physical):
                self.watcher.addPath(physical)
            self._finish_directory_focus(state["handle"], key)
            if state.get("pending_icons"):
                for task in state["pending_icons"]:
                    entry = task[5]
                    link = entry.get("symlink") if isinstance(entry, dict) else None
                    if link and not link.get("is_dir"):
                        self._yellow_icon_queue.append(task)
                    else:
                        self._icon_queue.append(task)
                self._paint_barrier_pending = True
                self._paint_update_requested = False
            if not self._directory_rendering:
                self._start_icon_drain(1)
