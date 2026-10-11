# SPDX-License-Identifier: MPL-2.0
import os
import sys
import subprocess

_this_dir = os.path.dirname(os.path.abspath(__file__))
_proj_root = os.path.dirname(_this_dir)
if _proj_root not in sys.path:
    sys.path.insert(0, _proj_root)

import time
import threading
import socket
import json
import uuid
import atexit

from PySide6.QtCore import QObject, Signal, Slot, QThread, Qt, QMetaObject, Q_ARG
from core.app_paths import APP_ROOT, IPC_RUNTIME_DIR, migrate_legacy_file


def _force_utf8_stdio():

    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream is None:
            continue
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


# Instance Main yang lagi hidup. Diisi attach() saat editor siap.
_main_ref = None
_bridge = None


class _Bridge(QObject):


    def __init__(self):
        super().__init__()
        self._result = (False, "no result")
        self._badge_result = None

    # dipanggil via invokeMethod; hasil disimpan ke self._result
    @Slot(str, str)
    def _do_single(self, action, path):
        self._result = self._run_single(action, path)

    @Slot(str, str, str)
    def _do_single_named(self, action, path, name):
        self._result = self._run_single(action, path, name=name or None)

    @Slot(str)
    def _do_badge(self, path):
        tree, err = self._tree()
        if err:
            self._badge_result = None
            return
        it = tree.rc_cli_resolve_item(os.path.normpath(path))
        self._badge_result = it.data(0, Qt.UserRole + 1) if it is not None else None

    @Slot(str, str)
    def _do_batch(self, action, paths_joined):
        paths = [p.strip() for p in paths_joined.split("\x00") if p.strip()]
        self._result = self._run_batch(action, paths)

    @Slot()
    def _do_refresh(self):
        main = _main_ref
        tree = getattr(main, "active_tree", None) if main is not None else None
        if tree is None:
            self._result = (False, "Tidak ada active_tree di GUI")
            return
        try:
            if tree.display_mode == "single_dir":
                tree._sd_render(tree._sd_state_from_item(tree.invisibleRootItem()))
            else:
                root = tree.topLevelItem(0)
                if root is None:
                    self._result = (False, "FileTree belum siap")
                    return
                tree.refresh_folder(root)
            self._result = (True, "Tree di UI berhasil di-refresh")
        except Exception as exc:
            self._result = (False, f"Refresh error: {exc}")

    @Slot(str, str, str)
    def _do_transfer(self, mode, source, destination):
        self._result = self._run_transfer(mode, source, destination)

    @Slot(str, str, str)
    def _do_make_filesystem(self, folder, fs_type, mount_point):
        self._result = self._run_make_filesystem(folder, fs_type, mount_point or None)

    @Slot(str, str, str)
    def _do_create_symlink(self, folder, name, target):
        self._result = self._run_create_symlink(folder, name, target)

    @Slot(str, str)
    def _do_write_text(self, path, content):
        self._result = self._run_write_text(path, content)

    @Slot(str, str, str, bool)
    def _do_replace_text(self, path, old_text, new_text, replace_all):
        self._result = self._run_replace_text(path, old_text, new_text, replace_all)

    @Slot(str, str, str, str)
    def _do_branch_create(self, folder, branch_name, color, cwd):
        self._result = self._run_branch_create(folder, branch_name, color or "blue", cwd or None)

    @Slot(str, str, str)
    def _do_branch_switch(self, folder, branch_name, cwd):
        self._result = self._run_branch_switch(folder, branch_name, cwd or None)

    @Slot(str, str, str)
    def _do_branch_delete(self, folder, branch_name, cwd):
        self._result = self._run_branch_delete(folder, branch_name, cwd or None)

    # implementasi sebenarnya (selalu di Qt thread)
    def _tree(self, path=None):
        main = _main_ref
        if main is None:
            return None, "no live editor (attach() belum dipanggil)"
        if path:
            norm = os.path.normcase(os.path.normpath(path))
            t2 = getattr(main, "tree2", None)
            if t2 and getattr(t2, "base", None):
                b2 = os.path.normcase(os.path.normpath(t2.base))
                if norm == b2 or norm.startswith(b2 + os.sep):
                    return t2, None
            t1 = getattr(main, "tree", None)
            if t1 and getattr(t1, "base", None):
                b1 = os.path.normcase(os.path.normpath(t1.base))
                if norm == b1 or norm.startswith(b1 + os.sep):
                    return t1, None
        tree = getattr(main, "active_tree", None) or getattr(main, "tree", None)
        if tree is None:
            return None, "no active_tree on editor"
        return tree, None

    def _run_single(self, action, path, name=None):
        tree, err = self._tree(path)
        if err:
            return False, err
        try:
            return tree.rc_cli(action, path, name=name)
        except Exception as e:
            import traceback
            traceback.print_exc()
            return False, f"rc_cli error: {e}"

    def _run_batch(self, action, paths):
        first_p = paths[0] if paths else None
        tree, err = self._tree(first_p)
        if err:
            return False, err
        try:
            return tree.rc_cli_batch(action, paths)
        except Exception as e:
            import traceback
            traceback.print_exc()
            return False, f"rc_cli_batch error: {e}"

    def _run_transfer(self, mode, source, destination):

        if mode not in ("copy", "move"):
            return False, f"unsupported fs transfer: {mode}"
        source = os.path.normpath(source)
        destination = os.path.normpath(destination)
        from core.rak_neobranch_view import source_image_branch
        source_branch = source_image_branch(source)
        if source_branch:
            return False, (f"source image belongs to active NeoBranch '{source_branch}'; "
                           "use the unpacked partition tree")
        src_tree, src_err = self._tree(source)
        dst_tree, dst_err = self._tree(destination)
        if src_err:
            return False, src_err
        if dst_err:
            return False, dst_err
        src_item = src_tree.rc_cli_resolve_item(source)
        dst_item = dst_tree.rc_cli_resolve_item(destination)
        if src_item is None:
            return False, f"source is not a managed project item: {source}"
        if dst_item is None:
            return False, f"destination is not a managed project folder: {destination}"
        if src_tree.is_mount_node(src_item) or dst_tree.is_mount_node(dst_item):
            return False, "fs copy/move does not modify virtual mounted images; use 'rak img'"

        src_logical = src_item.data(0, Qt.UserRole)
        src_branch = src_item.data(0, Qt.UserRole + 20)
        src_physical = src_branch if src_branch and os.path.exists(src_branch) else src_logical
        dst_logical = dst_item.data(0, Qt.UserRole)
        dst_branch = dst_item.data(0, Qt.UserRole + 20)
        dst_physical = dst_branch if dst_branch and os.path.exists(dst_branch) else dst_logical
        if not src_logical or not dst_logical:
            return False, "source or destination is not a managed project item"
        from core.rak_neobranch_view import ActiveProjectView
        src_visible, _ = ActiveProjectView(src_tree.base).resolve(source)
        dst_visible, _ = ActiveProjectView(dst_tree.base).resolve(destination)
        if not src_visible or not dst_visible:
            return False, "source or destination is hidden in the active NeoBranch view"
        src_stale = (os.path.normcase(os.path.realpath(src_physical)) != os.path.normcase(os.path.realpath(src_visible))
                     and not (os.path.isdir(src_physical) and os.path.isdir(src_visible)))
        dst_stale = (os.path.normcase(os.path.realpath(dst_physical)) != os.path.normcase(os.path.realpath(dst_visible))
                     and not (os.path.isdir(dst_physical) and os.path.isdir(dst_visible)))
        if src_stale or dst_stale:
            return False, "tree item is stale for active NeoBranch; run rak refresh"
        if not src_logical or not os.path.exists(src_physical):
            return False, f"source path not found: {source}"
        if not dst_logical or not os.path.isdir(dst_physical):
            return False, "destination must be an existing project folder"
        if (os.path.normcase(os.path.normpath(src_logical)) ==
                os.path.normcase(os.path.normpath(getattr(src_tree, "base", "")))):
            return False, "project root cannot be copied or moved"
        try:
            rel_source = os.path.relpath(src_logical, src_tree.base).replace("\\", "/")
            if rel_source == "config":
                return False, "root config folder cannot be copied or moved"
        except Exception:
            return False, "source is outside its project root"

        # Never open a conflict/overwrite dialog from a non-interactive CLI.
        same_dir = (os.path.normcase(os.path.normpath(os.path.dirname(src_logical))) ==
                    os.path.normcase(os.path.normpath(dst_logical)))
        if os.path.isdir(src_physical):
            try:
                if os.path.commonpath((os.path.abspath(dst_logical), os.path.abspath(src_logical))) == os.path.abspath(src_logical):
                    return False, "cannot copy or move a folder into itself or its descendant"
            except ValueError:
                return False, "source and destination are on incompatible paths"
        target_logical = os.path.join(dst_logical, os.path.basename(src_logical))
        target_physical, _, _ = dst_tree._nb_resolve_write_path(target_logical)
        if not same_dir and (os.path.exists(target_logical) or os.path.exists(target_physical)):
            return False, f"destination already contains {os.path.basename(src_logical)!r}; rename it first"
        if mode == "move" and same_dir:
            return True, "move is a no-op: source is already in destination"

        main = _main_ref
        old_clipboard = getattr(main, "fs_clipboard", None)
        try:
            staged = (src_tree.clip_copy([src_item]) if mode == "copy"
                      else src_tree.clip_cut([src_item]))
            if not staged:
                return False, "source is protected or cannot be staged for transfer"
            dst_tree.clip_paste(dst_item)
            return True, f"dispatched fs {mode}: {os.path.basename(src_logical)}"
        except Exception as exc:
            import traceback
            traceback.print_exc()
            return False, f"fs {mode} dispatch error: {exc}"
        finally:
            # A terminal command must not clobber the user's Ctrl+C/Ctrl+X state.
            if main is not None:
                main.fs_clipboard = old_clipboard

    def _run_make_filesystem(self, folder, fs_type, mount_point=None):

        fs_type = (fs_type or "").upper()
        if fs_type not in ("EROFS", "EXT4"):
            return False, "filesystem type must be EROFS or EXT4"
        folder = os.path.normpath(folder)
        tree, err = self._tree(folder)
        if err:
            return False, err
        item = tree.rc_cli_resolve_item(folder)
        if item is None or not os.path.isdir(folder):
            return False, "folder must be an existing managed project folder"
        try:
            rel = os.path.relpath(folder, tree.base).replace("\\", "/")
        except Exception:
            return False, "folder is outside the project root"
        if not rel or rel == "." or "/" in rel or rel == "config":
            return False, "make-filesystem only accepts a direct project-root partition folder"
        if tree.nb_is_active(rel):
            return False, "cannot build a source filesystem while its NeoBranch is active"

        main = _main_ref
        from core.task_queue import TaskItem
        task_id = f"make-filesystem:{folder}"

        def worker():
            from core.make_filesystem import make_filesystem
            return make_filesystem(folder, fs_type, mount_point=mount_point)

        def after(ok, msg):
            if main and hasattr(main, "pop_task"):
                main.pop_task(task_id, bool(ok))
            try:
                tree.reload_config_cache()
                tree.engine.rebuild_root_map()
                tree.refresh_folder(tree.topLevelItem(0))
            except Exception:
                pass
            if main:
                if ok and hasattr(main, "set_loading_finished"):
                    main.set_loading_finished(f"Filesystem created: {os.path.basename(folder)}")
                elif not ok and hasattr(main, "set_loading_failed"):
                    main.set_loading_failed("Make filesystem failed")

        if main and hasattr(main, "task_queue"):
            if hasattr(main, "push_task"):
                main.push_task(task_id)
            main.task_queue.enqueue(TaskItem(task_id=task_id, worker=worker, callback=after))
        else:
            from core.threader import run_in_thread
            run_in_thread(worker, None, callback=after)
        return True, f"dispatched make-filesystem {fs_type} for {os.path.basename(folder)}"

    def _run_create_symlink(self, folder, name, target):
        folder = os.path.normpath(folder)
        tree, err = self._tree(folder)
        if err:
            return False, err
        item = tree.rc_cli_resolve_item(folder)
        if item is None:
            return False, "symlink destination is not a managed project folder"
        from core.rak_neobranch_view import ActiveProjectView
        visible, _ = ActiveProjectView(tree.base).resolve(folder)
        if not visible or not os.path.isdir(visible):
            return False, "symlink destination is not visible in the active NeoBranch view"
        if tree.is_mount_node(item):
            return False, "virtual mounted images are not modified by fs symlink; use 'rak img'"
        try:
            return tree.rc_cli_create_symlink(item, name, target)
        except Exception as exc:
            import traceback
            traceback.print_exc()
            return False, f"create symlink dispatch error: {exc}"

    def _run_write_text(self, path, content):

        import tempfile
        path = os.path.normpath(path)
        tree, err = self._tree(path)
        if err:
            return False, err
        parent_path = os.path.dirname(path)
        parent_item = tree.rc_cli_resolve_item(parent_path)
        if parent_item is None or tree.is_mount_node(parent_item):
            return False, "write destination parent must be an existing managed project folder"
        from core.rak_neobranch_view import ActiveProjectView
        parent_visible, _ = ActiveProjectView(tree.base).resolve(parent_path)
        if not parent_visible or not os.path.isdir(parent_visible):
            return False, "write destination parent is not visible in the active NeoBranch view"
        parent_branch = parent_item.data(0, Qt.UserRole + 20)
        parent_physical = parent_branch if parent_branch and os.path.exists(parent_branch) else parent_path
        if not os.path.isdir(parent_physical):
            return False, "write destination parent is not a folder"
        try:
            rel = os.path.relpath(path, tree.base).replace("\\", "/")
        except Exception:
            return False, "write path is outside the project root"
        if not rel or rel == "." or rel == "config":
            return False, "cannot replace the project root or root config folder"

        existing_item = tree.rc_cli_resolve_item(path)
        if existing_item is not None and tree.is_mount_node(existing_item):
            return False, "virtual mounted images are not modified by fs write; use 'rak img write'"
        existing_branch = (existing_item.data(0, Qt.UserRole + 20)
                           if existing_item is not None else None)
        existing_physical = (existing_branch if existing_branch and os.path.exists(existing_branch)
                             else path)
        if os.path.isdir(existing_physical):
            return False, "write path is a folder"
        existed_before = os.path.exists(existing_physical) or os.path.exists(path)
        physical, _, _ = tree._nb_resolve_write_path(path)
        try:
            os.makedirs(os.path.dirname(physical), exist_ok=True)
            fd, temporary = tempfile.mkstemp(prefix=".rak-write-", dir=os.path.dirname(physical), text=True)
            try:
                with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                    handle.write(content if isinstance(content, str) else str(content))
                os.replace(temporary, physical)
            except Exception:
                try:
                    os.unlink(temporary)
                except Exception:
                    pass
                raise

            # New source files need metadata; branch output also records edits
            # and routes the metadata write to branch-local config.
            if not existed_before:
                tree._nb_apply(rel)
            tree._nb_record_output_tree(path, physical)
            if rel.startswith("config/"):
                tree.reload_config_cache()
            tree.refresh_folder(parent_item)
            return True, f"wrote {os.path.basename(path)}"
        except Exception as exc:
            return False, f"write failed: {exc}"

    def _run_replace_text(self, path, old_text, new_text, replace_all=False):
        path = os.path.normpath(path)
        tree, err = self._tree(path)
        if err:
            return False, err
        item = tree.rc_cli_resolve_item(path)
        if item is None or tree.is_mount_node(item):
            return False, "replace path must be an existing managed text file"
        from core.rak_neobranch_view import ActiveProjectView
        physical, _ = ActiveProjectView(tree.base).resolve(path)
        if not physical or not os.path.isfile(physical):
            return False, "replace path is not a file"
        if old_text == new_text:
            return False, "old_text and new_text are identical"
        try:
            with open(physical, "r", encoding="utf-8", errors="replace") as handle:
                old_content = handle.read()
            count = old_content.count(old_text)
            if count == 0:
                return False, "old_text was not found"
            if count > 1 and not replace_all:
                return False, f"old_text occurs {count} times; set replace_all explicitly"
            new_content = old_content.replace(old_text, new_text, -1 if replace_all else 1)
            ok, msg = self._run_write_text(path, new_content)
            return (ok, f"{msg}; replacements={count if replace_all else 1}" if ok else msg)
        except Exception as exc:
            return False, f"replace failed: {exc}"

    def _run_branch_create(self, folder, branch_name, color="blue", cwd=None):
        tree, tree_base, fs_root, err = _resolve_branch_target(folder, cwd=cwd)
        if err:
            return False, err
        from main_ext.class_NeoBranchManager import NeoBranchManager, BRANCH_COLORS
        color = (color or "blue").lower().strip()
        if color not in BRANCH_COLORS:
            color = "blue"
        nb_mgr = NeoBranchManager(tree_base)
        try:
            nb_mgr.create_branch(fs_root, branch_name, color)
            nb_mgr.set_active_branch(fs_root, branch_name)
        except Exception as exc:
            return False, f"Failed to create branch: {exc}"

        if tree is not None:
            try:
                item = tree._nb_item_for_fs_root(fs_root)
                if item:
                    item.setData(0, Qt.UserRole + 10, color)
                    tree._neobranch_on_switch(item, fs_root)
                else:
                    tree.nb_refresh_all_dots()
                    tree.reload_config_cache()
            except Exception as e:
                print(f"[RAK_CLI] UI sync warning: {e}")

        return True, f"NeoBranch '{branch_name}' created and activated for '{fs_root}' (color: {color})"

    def _run_branch_switch(self, folder, branch_name, cwd=None):
        tree, tree_base, fs_root, err = _resolve_branch_target(folder, cwd=cwd)
        if err:
            return False, err
        from main_ext.class_NeoBranchManager import NeoBranchManager
        nb_mgr = NeoBranchManager(tree_base)

        target_lower = branch_name.lower().strip()
        is_source = target_lower in ("source", "none", "--source", "0")

        if is_source:
            try:
                nb_mgr.set_active_branch(fs_root, None)
            except Exception as exc:
                return False, f"Failed to switch to source: {exc}"
            if tree is not None:
                try:
                    item = tree._nb_item_for_fs_root(fs_root)
                    if item:
                        item.setData(0, Qt.UserRole + 10, None)
                        tree._neobranch_on_switch(item, fs_root)
                    else:
                        tree.nb_refresh_all_dots()
                        tree.reload_config_cache()
                except Exception as e:
                    print(f"[RAK_CLI] UI sync warning: {e}")
            return True, f"Switched '{fs_root}' to source (no active branch)"

        existing = {b["name"]: b for b in nb_mgr.list_branches(fs_root)}
        if branch_name not in existing:
            avail = ", ".join(existing.keys()) or "none"
            return False, f"Branch '{branch_name}' not found for '{fs_root}'. Available branches: {avail}"

        try:
            nb_mgr.set_active_branch(fs_root, branch_name)
        except Exception as exc:
            return False, f"Failed to switch branch: {exc}"

        color = existing[branch_name].get("color", "blue")
        if tree is not None:
            try:
                item = tree._nb_item_for_fs_root(fs_root)
                if item:
                    item.setData(0, Qt.UserRole + 10, color)
                    tree._neobranch_on_switch(item, fs_root)
                else:
                    tree.nb_refresh_all_dots()
                    tree.reload_config_cache()
            except Exception as e:
                print(f"[RAK_CLI] UI sync warning: {e}")

        return True, f"Switched '{fs_root}' to branch '{branch_name}'"

    def _run_branch_delete(self, folder, branch_name, cwd=None):
        tree, tree_base, fs_root, err = _resolve_branch_target(folder, cwd=cwd)
        if err:
            return False, err
        from main_ext.class_NeoBranchManager import NeoBranchManager
        nb_mgr = NeoBranchManager(tree_base)

        existing = {b["name"]: b for b in nb_mgr.list_branches(fs_root)}
        if branch_name not in existing:
            return False, f"Branch '{branch_name}' not found for '{fs_root}'"

        active = nb_mgr.get_active_branch(fs_root)
        was_active = active is not None and active.get("name") == branch_name

        try:
            nb_mgr.delete_branch(fs_root, branch_name)
        except Exception as exc:
            return False, f"Failed to delete branch '{branch_name}': {exc}"

        if was_active and tree is not None:
            try:
                item = tree._nb_item_for_fs_root(fs_root)
                if item:
                    item.setData(0, Qt.UserRole + 10, None)
                    tree._neobranch_on_switch(item, fs_root)
                else:
                    tree.nb_refresh_all_dots()
                    tree.reload_config_cache()
            except Exception as e:
                print(f"[RAK_CLI] UI sync warning: {e}")

        return True, f"Deleted branch '{branch_name}' for '{fs_root}'"

    def take_result(self):
        return self._result


def _ensure_bridge():
    global _bridge
    if _bridge is None:
        _bridge = _Bridge()
        # Pastikan bridge tinggal di main thread (objek dibuat dari main thread
        # saat attach()). Kalau tidak, invokeMethod tetap antri ke affinity-nya.
    return _bridge


def attach(main):

    global _main_ref
    _force_utf8_stdio()
    _main_ref = main
    _ensure_bridge()
    return _bridge


def detach(main):

    global _main_ref
    if _main_ref is main:
        _main_ref = None
        stop_ipc_server()


def is_attached():
    return _main_ref is not None


def _on_qt_thread():
    bridge = _ensure_bridge()
    app_thread = bridge.thread()
    return QThread.currentThread() == app_thread


def run(action, path, name=None):

    bridge = _ensure_bridge()
    if _on_qt_thread():
        return bridge._run_single(action, path, name=name)
    if name is not None:
        QMetaObject.invokeMethod(
            bridge, "_do_single_named", Qt.BlockingQueuedConnection,
            Q_ARG(str, action), Q_ARG(str, path), Q_ARG(str, name),
        )
    else:
        QMetaObject.invokeMethod(
            bridge, "_do_single", Qt.BlockingQueuedConnection,
            Q_ARG(str, action), Q_ARG(str, path),
        )
    return bridge.take_result()


def run_batch(action, paths):

    bridge = _ensure_bridge()
    joined = "\x00".join(paths)
    if _on_qt_thread():
        return bridge._run_batch(action, paths)
    QMetaObject.invokeMethod(
        bridge, "_do_batch", Qt.BlockingQueuedConnection,
        Q_ARG(str, action), Q_ARG(str, joined),
    )
    return bridge.take_result()


def transfer(mode, source, destination):

    bridge = _ensure_bridge()
    if _on_qt_thread():
        return bridge._run_transfer(mode, source, destination)
    QMetaObject.invokeMethod(
        bridge, "_do_transfer", Qt.BlockingQueuedConnection,
        Q_ARG(str, mode), Q_ARG(str, source), Q_ARG(str, destination),
    )
    return bridge.take_result()


def transfer_and_wait(mode, source, destination, timeout=120.0):
    ok, msg = transfer(mode, source, destination)
    if not ok:
        return ok, msg
    if not wait_idle(timeout=timeout):
        return False, f"{msg} (TIMEOUT setelah {timeout}s)"
    return True, f"{msg} (selesai)"


def make_filesystem(folder, fs_type, mount_point=None):

    bridge = _ensure_bridge()
    if _on_qt_thread():
        return bridge._run_make_filesystem(folder, fs_type, mount_point)
    QMetaObject.invokeMethod(
        bridge, "_do_make_filesystem", Qt.BlockingQueuedConnection,
        Q_ARG(str, folder), Q_ARG(str, fs_type), Q_ARG(str, mount_point or ""),
    )
    return bridge.take_result()


def make_filesystem_and_wait(folder, fs_type, mount_point=None, timeout=300.0):
    ok, msg = make_filesystem(folder, fs_type, mount_point=mount_point)
    if not ok:
        return ok, msg
    if not wait_idle(timeout=timeout):
        return False, f"{msg} (TIMEOUT setelah {timeout}s)"
    return True, f"{msg} (selesai)"


def create_symlink(folder, name, target):

    bridge = _ensure_bridge()
    if _on_qt_thread():
        return bridge._run_create_symlink(folder, name, target)
    QMetaObject.invokeMethod(
        bridge, "_do_create_symlink", Qt.BlockingQueuedConnection,
        Q_ARG(str, folder), Q_ARG(str, name), Q_ARG(str, target),
    )
    return bridge.take_result()


def write_text(path, content):

    bridge = _ensure_bridge()
    if _on_qt_thread():
        return bridge._run_write_text(path, content)
    QMetaObject.invokeMethod(
        bridge, "_do_write_text", Qt.BlockingQueuedConnection,
        Q_ARG(str, path), Q_ARG(str, content if isinstance(content, str) else str(content)),
    )
    return bridge.take_result()


def replace_text(path, old_text, new_text, replace_all=False):

    bridge = _ensure_bridge()
    if _on_qt_thread():
        return bridge._run_replace_text(path, old_text, new_text, replace_all)
    QMetaObject.invokeMethod(
        bridge, "_do_replace_text", Qt.BlockingQueuedConnection,
        Q_ARG(str, path), Q_ARG(str, old_text), Q_ARG(str, new_text),
        Q_ARG(bool, bool(replace_all)),
    )
    return bridge.take_result()


def _resolve_branch_target(target, cwd=None):

    cwd = os.path.normpath(cwd or os.getcwd())
    target_str = (target or "").strip()
    if not target_str:
        return None, None, None, "Target partition/folder cannot be empty"

    main = _main_ref
    tree = None
    tree_base = None

    cand = os.path.normpath(target_str if os.path.isabs(target_str) else os.path.join(cwd, target_str))

    # 1. Try finding live GUI tree that actually contains cand
    if main is not None:
        try:
            bridge = _ensure_bridge()
            t, _ = bridge._tree(cand)
            if t and getattr(t, "base", None):
                b = os.path.normpath(t.base)
                cand_norm = os.path.normcase(cand)
                b_norm = os.path.normcase(b)
                if cand_norm == b_norm or cand_norm.startswith(b_norm + os.sep):
                    tree = t
                    tree_base = b
        except Exception:
            pass

    # 2. If tree_base still unknown, infer from cand or filesystem
    if not tree_base:
        parent = os.path.dirname(cand)
        if parent and (os.path.isdir(os.path.join(parent, "config")) or os.path.isdir(os.path.join(parent, "neobranch"))):
            tree_base = parent
        elif os.path.isdir(cand) and (os.path.isdir(os.path.join(cand, "config")) or os.path.isdir(os.path.join(cand, "neobranch"))):
            tree_base = cand
        elif os.path.isdir(os.path.join(cwd, "config")) or os.path.isdir(os.path.join(cwd, "neobranch")):
            tree_base = cwd
        else:
            p = os.path.dirname(cwd)
            if p and (os.path.isdir(os.path.join(p, "config")) or os.path.isdir(os.path.join(p, "neobranch"))):
                tree_base = p
            elif os.path.isdir(cand):
                tree_base = parent if parent else cwd
            else:
                tree_base = cwd

    # 3. Resolve fs_root relative to tree_base
    if os.path.normcase(cand) == os.path.normcase(tree_base):
        return None, None, None, f"Target '{target_str}' is the project root; please specify a partition folder (e.g. 'vendor', 'system')"

    norm_target = target_str.replace("\\", "/").strip("/")
    if "/" not in norm_target and norm_target not in (".", ".."):
        fs_root = norm_target
    else:
        try:
            rel = os.path.relpath(cand, tree_base).replace("\\", "/")
            if rel.startswith(".."):
                fs_root = os.path.basename(cand)
            else:
                fs_root = rel.split("/")[0]
        except Exception:
            fs_root = os.path.basename(cand)

    if not fs_root or fs_root in (".", ".."):
        return None, None, None, f"Cannot determine partition name from '{target_str}'"

    # Verify partition folder exists on disk
    fs_path = os.path.join(tree_base, fs_root)
    nb_path = os.path.join(tree_base, "neobranch", fs_root)
    if not os.path.isdir(fs_path) and not os.path.isdir(nb_path):
        return None, None, None, f"Partition folder '{fs_root}' does not exist in '{tree_base}'"

    return tree, tree_base, fs_root, None


def branch_create(folder, branch_name, color="blue", cwd=None):

    if is_attached():
        bridge = _ensure_bridge()
        if _on_qt_thread():
            return bridge._run_branch_create(folder, branch_name, color=color, cwd=cwd)
        QMetaObject.invokeMethod(
            bridge, "_do_branch_create", Qt.BlockingQueuedConnection,
            Q_ARG(str, str(folder)), Q_ARG(str, str(branch_name)),
            Q_ARG(str, str(color or "blue")), Q_ARG(str, str(cwd or "")),
        )
        return bridge.take_result()
    bridge = _ensure_bridge()
    return bridge._run_branch_create(folder, branch_name, color=color, cwd=cwd)


def branch_switch(folder, branch_name, cwd=None):

    if is_attached():
        bridge = _ensure_bridge()
        if _on_qt_thread():
            return bridge._run_branch_switch(folder, branch_name, cwd=cwd)
        QMetaObject.invokeMethod(
            bridge, "_do_branch_switch", Qt.BlockingQueuedConnection,
            Q_ARG(str, str(folder)), Q_ARG(str, str(branch_name)),
            Q_ARG(str, str(cwd or "")),
        )
        return bridge.take_result()
    bridge = _ensure_bridge()
    return bridge._run_branch_switch(folder, branch_name, cwd=cwd)


def branch_delete(folder, branch_name, cwd=None):

    if is_attached():
        bridge = _ensure_bridge()
        if _on_qt_thread():
            return bridge._run_branch_delete(folder, branch_name, cwd=cwd)
        QMetaObject.invokeMethod(
            bridge, "_do_branch_delete", Qt.BlockingQueuedConnection,
            Q_ARG(str, str(folder)), Q_ARG(str, str(branch_name)),
            Q_ARG(str, str(cwd or "")),
        )
        return bridge.take_result()
    bridge = _ensure_bridge()
    return bridge._run_branch_delete(folder, branch_name, cwd=cwd)


def branch_list(folder=None, cwd=None):

    cwd = os.path.normpath(cwd or os.getcwd())
    main = _main_ref
    tree_base = None
    if main is not None:
        try:
            bridge = _ensure_bridge()
            cand = folder if (folder and os.path.isabs(folder)) else (os.path.join(cwd, folder) if folder else cwd)
            t, _ = bridge._tree(cand)
            if t and getattr(t, "base", None):
                tree_base = os.path.normpath(t.base)
        except Exception:
            pass

    from main_ext.class_NeoBranchManager import NeoBranchManager

    if folder and folder.lower() != "all":
        _, tree_base, fs_root, err = _resolve_branch_target(folder, cwd=cwd)
        if err:
            return False, err
        nb_mgr = NeoBranchManager(tree_base)
        branches = nb_mgr.list_branches(fs_root)
        active = nb_mgr.get_active_branch(fs_root)
        active_name = active["name"] if active else None

        lines = [f"NeoBranches for '{fs_root}' (in {tree_base}):"]
        if not branches:
            lines.append("  (no branches created yet; currently on source)")
            return True, "\n".join(lines)

        for b in branches:
            name = b.get("name", "unknown")
            color = b.get("color", "blue")
            is_active = (name == active_name)
            marker = "* " if is_active else "  "
            edits = len(b.get("edited_files", []))
            deletes = len(b.get("deleted_paths", []))
            created = b.get("created_at", "")[:19].replace("T", " ")
            status = " (ACTIVE)" if is_active else ""
            lines.append(f"{marker}{name:<16} [{color}] created: {created} | edits: {edits} | deletes: {deletes}{status}")

        if active_name is None:
            lines.append("  * [active: source - untouched snapshot]")
        return True, "\n".join(lines)

    # All partitions in project
    if not tree_base:
        if os.path.isdir(os.path.join(cwd, "config")) or os.path.isdir(os.path.join(cwd, "neobranch")):
            tree_base = cwd
        else:
            p = os.path.dirname(cwd)
            if os.path.isdir(os.path.join(p, "config")) or os.path.isdir(os.path.join(p, "neobranch")):
                tree_base = p
            else:
                tree_base = cwd

    nb_mgr = NeoBranchManager(tree_base)
    nb_root = os.path.join(tree_base, "neobranch")
    partitions = []
    if os.path.isdir(nb_root):
        partitions = [d for d in sorted(os.listdir(nb_root)) if os.path.isdir(os.path.join(nb_root, d))]

    if not partitions:
        return True, f"No NeoBranches exist yet in project '{tree_base}' (all partitions on source)."

    lines = [f"NeoBranches in project '{tree_base}':"]
    for part in partitions:
        branches = nb_mgr.list_branches(part)
        active = nb_mgr.get_active_branch(part)
        active_name = active["name"] if active else "source"
        lines.append(f"\n[{part}] (active: {active_name})")
        if not branches:
            lines.append("  (no branches)")
            continue
        for b in branches:
            name = b.get("name", "unknown")
            color = b.get("color", "blue")
            is_active = (name == (active["name"] if active else None))
            marker = "* " if is_active else "  "
            edits = len(b.get("edited_files", []))
            deletes = len(b.get("deleted_paths", []))
            created = b.get("created_at", "")[:19].replace("T", " ")
            lines.append(f"  {marker}{name:<14} [{color}] created: {created} | edits: {edits} | deletes: {deletes}")

    return True, "\n".join(lines)


def branch_current(folder, cwd=None):

    _, tree_base, fs_root, err = _resolve_branch_target(folder, cwd=cwd)
    if err:
        return False, err
    from main_ext.class_NeoBranchManager import NeoBranchManager
    nb_mgr = NeoBranchManager(tree_base)
    active = nb_mgr.get_active_branch(fs_root)
    if active:
        name = active.get("name", "unknown")
        color = active.get("color", "blue")
        created = active.get("created_at", "")[:19].replace("T", " ")
        edits = len(active.get("edited_files", []))
        deletes = len(active.get("deleted_paths", []))
        return True, f"Active branch for '{fs_root}': '{name}' [{color}] (created: {created}, edits: {edits}, deletes: {deletes})"
    return True, f"Active branch for '{fs_root}': source (no active branch)"


def _is_idle():

    main = _main_ref
    busy = False
    if main is not None:
        tq = getattr(main, "task_queue", None)
        if tq is not None:
            try:
                busy = busy or tq.is_busy()
            except Exception:
                pass
    try:
        from core.threader import has_active_threads
        busy = busy or has_active_threads()
    except Exception:
        pass
    return not busy


def wait_idle(timeout=120.0, poll=0.1):

    if _on_qt_thread():
        raise RuntimeError("wait_idle() tidak boleh dipanggil dari Qt main thread")
    deadline = time.time() + timeout
    # beri sedikit waktu supaya dispatch sempat enqueue
    time.sleep(0.15)
    while time.time() < deadline:
        if _is_idle():
            # konfirmasi dua kali biar tidak ketipu jeda antar-task
            time.sleep(poll)
            if _is_idle():
                return True
        time.sleep(poll)
    return False


def run_and_wait(action, path, name=None, timeout=120.0):

    queue = getattr(_main_ref, "task_queue", None)
    cursor = queue.result_cursor() if queue is not None else None
    ok, msg = run(action, path, name=name)
    if not ok:
        return ok, msg
    finished = wait_idle(timeout=timeout)
    if not finished:
        return False, f"{msg} (TIMEOUT setelah {timeout}s)"
    kind = action.split("_", 1)[0]
    if kind in ("unpack", "repack"):
        if queue is None or cursor is None:
            if kind == "repack":
                return False, f"{msg} (hasil repack tidak tersedia)"
            return True, f"{msg} (selesai)"
        target = os.path.normcase(os.path.normpath(path))
        results = [result for result in queue.results_since(cursor)
                   if result[1].startswith(kind)
                   and os.path.normcase(os.path.normpath(result[1].split(":", 1)[-1])) == target]
        if not results:
            # Some unpack actions open a container viewer rather than queue a
            # filesystem extraction. Preserve those dispatch-only actions.
            if kind == "repack":
                return False, f"{msg} (hasil repack tidak tercatat)"
        else:
            _, _, task_ok, task_msg = results[-1]
            if not task_ok:
                return False, f"{kind} gagal: {task_msg or 'worker tidak memberi detail error'}"
    return True, f"{msg} (selesai)"


def badge(path):

    bridge = _ensure_bridge()

    def _do():
        tree, err = bridge._tree()
        if err:
            return None
        it = tree.rc_cli_resolve_item(os.path.normpath(path))
        if it is None:
            return None
        return it.data(0, Qt.UserRole + 1)

    if _on_qt_thread():
        return _do()


    QMetaObject.invokeMethod(
        bridge, "_do_badge", Qt.BlockingQueuedConnection, Q_ARG(str, path),
    )
    return bridge._badge_result


def actions():

    main = _main_ref
    tree = getattr(main, "active_tree", None) if main else None
    if tree is None:
        from main_ext.FileTree_ext.RightClick import FileTreeContextMixin as _M
        single = list(_M.RC_CLI_ACTIONS)
        batch = list(_M.RC_CLI_BATCH_ACTIONS)
    else:
        single = list(tree.RC_CLI_ACTIONS)
        batch = list(tree.RC_CLI_BATCH_ACTIONS)
    return {"single": single, "batch": batch}


def _cli_unquote(value):
    value = (value or "").strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("\"", "'"):
        return value[1:-1]
    return value


def _cli_path(value, cwd=None):
    value = _cli_unquote(value)
    if not value:
        return value


    if cwd is None:
        cwd = os.getcwd()
    if cwd and not os.path.isabs(value):
        value = os.path.join(cwd, value)
    return os.path.normpath(value)


def _execute_fs_command(rest, *, wait=True, timeout=120.0, cwd=None):

    import shlex
    try:
        tokens = [_cli_unquote(token) for token in shlex.split(rest, posix=False)]
    except ValueError as exc:
        return False, f"invalid fs command: {exc}"
    if not tokens:
        return False, "usage: rak fs <ls|cat|grep|write|replace|copy|move|delete|mkdir|touch|rename|make-filesystem> ..."
    op = tokens[0].lower()

    if op in ("ls", "list", "cat", "read", "grep"):
        return _execute_fs_read_command(op, tokens[1:], cwd=cwd)

    if op == "write":
        if len(tokens) != 4 or tokens[2] != "--from":
            return False, "usage: rak fs write <logical-file> --from <utf8-input-file>"
        target = _cli_path(tokens[1], cwd)
        input_file = _cli_path(tokens[3], cwd)
        try:
            from core.rak_neobranch_view import project_root, ActiveProjectView
            try:
                input_root = project_root(input_file, cwd=cwd)
                input_physical, _ = ActiveProjectView(input_root).resolve(input_file)
            except ValueError:
                input_physical = input_file
            if not input_physical or not os.path.isfile(input_physical):
                return False, f"input file not found in active view: {input_file}"
            if os.path.getsize(input_physical) > 8 * 1024 * 1024:
                return False, "input file exceeds 8 MiB"
            with open(input_physical, "r", encoding="utf-8") as handle:
                content = handle.read()
        except (OSError, UnicodeError) as exc:
            return False, f"cannot read input file: {exc}"
        return write_text(target, content)

    if op == "replace":
        if len(tokens) not in (4, 5) or (len(tokens) == 5 and tokens[4] != "--all"):
            return False, "usage: rak fs replace <logical-file> <old-text> <new-text> [--all]"
        target = _cli_path(tokens[1], cwd)
        return replace_text(target, tokens[2], tokens[3], replace_all=len(tokens) == 5)

    if op in ("copy", "move"):
        if len(tokens) != 3:
            return False, f"usage: rak fs {op} <source> <destination-folder>"
        source, destination = _cli_path(tokens[1], cwd), _cli_path(tokens[2], cwd)
        return (transfer_and_wait(op, source, destination, timeout=timeout)
                if wait else transfer(op, source, destination))

    if op in ("delete", "rm", "remove"):
        if len(tokens) != 3 or tokens[2] != "--yes":
            return False, "usage: rak fs delete <path> --yes (permanent; no overwrite/undo)"
        target = _cli_path(tokens[1], cwd)
        return (run_and_wait("delete", target, timeout=timeout)
                if wait else run("delete", target))

    if op in ("mkdir", "folder"):
        if len(tokens) != 3:
            return False, "usage: rak fs mkdir <parent-folder> <name>"
        parent = _cli_path(tokens[1], cwd)
        return (run_and_wait("new_folder", parent, name=tokens[2], timeout=timeout)
                if wait else run("new_folder", parent, name=tokens[2]))

    if op in ("touch", "file"):
        if len(tokens) != 3:
            return False, "usage: rak fs touch <parent-folder> <name>"
        parent = _cli_path(tokens[1], cwd)
        return (run_and_wait("new_file", parent, name=tokens[2], timeout=timeout)
                if wait else run("new_file", parent, name=tokens[2]))

    if op in ("symlink", "link"):
        if len(tokens) != 4:
            return False, "usage: rak fs symlink <parent-folder> <name> <android-target>"
        parent = _cli_path(tokens[1], cwd)
        return create_symlink(parent, tokens[2], tokens[3])

    if op in ("rename", "mvname"):
        force = len(tokens) == 4 and tokens[3] == "--force"
        if len(tokens) != 3 and not force:
            return False, "usage: rak fs rename <path> <new-name> [--force]"
        target = _cli_path(tokens[1], cwd)
        action = "rename_force" if force else "rename"
        return (run_and_wait(action, target, name=tokens[2], timeout=timeout)
                if wait else run(action, target, name=tokens[2]))

    if op in ("make-filesystem", "makefs", "mkfs"):
        if len(tokens) < 4 or "--yes" not in tokens or "--type" not in tokens:
            return False, ("usage: rak fs make-filesystem <partition-folder> "
                           "--type <erofs|ext4> [--mount-point /path] --yes")
        type_pos = tokens.index("--type")
        if type_pos + 1 >= len(tokens):
            return False, "usage: --type <erofs|ext4>"
        fs_type = tokens[type_pos + 1]
        mount_point = None
        if "--mount-point" in tokens:
            mount_pos = tokens.index("--mount-point")
            if mount_pos + 1 >= len(tokens):
                return False, "usage: --mount-point </partition>"
            mount_point = tokens[mount_pos + 1]
        allowed = {"--type", fs_type, "--yes"}
        if mount_point is not None:
            allowed.update({"--mount-point", mount_point})
        unexpected = [token for token in tokens[2:] if token not in allowed]
        if unexpected:
            return False, f"unexpected make-filesystem option(s): {' '.join(unexpected)}"
        folder = _cli_path(tokens[1], cwd)
        return (make_filesystem_and_wait(folder, fs_type, mount_point=mount_point,
                                         timeout=max(timeout, 300.0))
                if wait else make_filesystem(folder, fs_type, mount_point=mount_point))

    if op in ("branch", "neobranch"):
        return _execute_branch_command(" ".join(tokens[1:]), wait=wait, timeout=timeout, cwd=cwd)

    return False, f"unknown fs operation: {op!r}"


def _execute_fs_read_command(op, args, *, cwd=None):

    import re
    from core.rak_neobranch_view import ActiveProjectView, project_root

    cwd = cwd or os.getcwd()
    if op in ("ls", "list"):
        branch_only = "--branch-only" in args
        paths = [arg for arg in args if arg != "--branch-only"]
        if len(paths) > 1:
            return False, "usage: rak fs ls [folder] [--branch-only]"
        target = _cli_path(paths[0] if paths else ".", cwd)
    elif op in ("cat", "read"):
        if len(args) != 1:
            return False, "usage: rak fs cat <file>"
        target = _cli_path(args[0], cwd)
    else:
        if len(args) not in (1, 2):
            return False, "usage: rak fs grep <regex> [file-or-folder]"
        target = _cli_path(args[1] if len(args) == 2 else ".", cwd)

    try:
        root = project_root(target, cwd=cwd)
        view = ActiveProjectView(root)
        if op in ("ls", "list"):
            entries = view.list_dir(target)
            if branch_only:
                entries = [entry for entry in entries if entry["origin"] == "branch"]
            return True, "\n".join(
                f"{entry['name']}{'/' if entry['kind'] == 'dir' else ''}\t[{entry['origin']}]"
                for entry in entries
            ) or "(empty)"
        if op in ("cat", "read"):
            physical, _ = view.resolve(target)
            if not physical or not os.path.isfile(physical):
                return False, f"file not found in active project view: {target}"
            limit = 256 * 1024
            with open(physical, "rb") as handle:
                raw = handle.read(limit + 1)
            output = raw[:limit].decode("utf-8", errors="replace")
            if len(raw) > limit:
                output += "\n[truncated at 256 KiB]"
            return True, output
        rx = re.compile(args[0])
        matches = []
        for logical in view.walk_files(target):
            physical, _ = view.resolve(logical)
            if not physical or os.path.getsize(physical) > 4 * 1024 * 1024:
                continue
            with open(physical, "r", encoding="utf-8", errors="replace") as handle:
                for line_number, line in enumerate(handle, 1):
                    if rx.search(line):
                        matches.append(f"{os.path.relpath(logical, root)}:{line_number}:{line.rstrip()[:300]}")
                        if len(matches) >= 400:
                            return True, "\n".join(matches) + "\n[truncated at 400 matches]"
        return True, "\n".join(matches) or "(no matches)"
    except (OSError, ValueError, re.error) as exc:
        return False, str(exc)


def _execute_compare_command(args, *, cwd=None, main_ref=None):

    from core.rennsproject_bridge import private_features_available
    if not private_features_available():
        return False, "This feature requires the Official edition bundle."
    cwd = cwd or os.getcwd()
    if not args and main_ref is not None:
        try:
            QMetaObject.invokeMethod(main_ref, "_open_compare_window", Qt.QueuedConnection)
            return True, "Jendela Compare Filesystem dibuka di GUI."
        except Exception as exc:
            return False, f"Gagal membuka jendela compare: {exc}"
    if len(args) < 2:
        return False, "usage: rak compare <dir_a> <dir_b> [--renxec out.renxec] [--json out.json]"
    out_rx = out_js = None
    index = 2
    while index < len(args):
        if args[index] not in ("--renxec", "--json") or index + 1 >= len(args):
            return False, f"invalid compare option: {args[index]}"
        if args[index] == "--renxec":
            out_rx = _cli_path(args[index + 1], cwd)
        else:
            out_js = _cli_path(args[index + 1], cwd)
        index += 2
    from core.rennsproject_bridge import run_compare_cli
    return run_compare_cli(
        _cli_path(args[0], cwd), _cli_path(args[1], cwd),
        out_renxec=out_rx, out_json=out_js,
        project_base=cwd, main_ref=main_ref,
    )


def _execute_branch_command(rest, *, wait=True, timeout=120.0, cwd=None):

    import shlex
    try:
        tokens = [_cli_unquote(t) for t in shlex.split(rest, posix=False)]
    except ValueError as exc:
        return False, f"invalid branch command: {exc}"

    if not tokens or tokens[0].lower() in ("help", "-h", "--help"):
        return True, (
            "RAK NeoBranch commands:\n"
            "  rak branch create <folder> <name> [color]   create and activate new branch (colors: blue, green, red, yellow, orange, purple, pink)\n"
            "  rak branch switch <folder> <name|source>    switch active branch (alias: checkout). Use 'source' to switch back to source\n"
            "  rak branch list [folder]                    list branches for partition or all partitions\n"
            "  rak branch current <folder>                 show currently active branch (alias: status)\n"
            "  rak branch delete <folder> <name> --yes     permanently delete branch"
        )

    op = tokens[0].lower()

    if op in ("create", "new", "add"):
        if len(tokens) < 3:
            return False, "usage: rak branch create <folder> <branch_name> [color]"
        folder = tokens[1]
        name = tokens[2]
        color = tokens[3] if len(tokens) > 3 else "blue"
        return branch_create(folder, name, color=color, cwd=cwd)

    if op in ("switch", "checkout", "set"):
        if len(tokens) < 3:
            return False, "usage: rak branch switch <folder> <branch_name|source>"
        folder = tokens[1]
        name = tokens[2]
        return branch_switch(folder, name, cwd=cwd)

    if op in ("list", "ls", "show"):
        folder = tokens[1] if len(tokens) > 1 else None
        return branch_list(folder, cwd=cwd)

    if op in ("current", "status", "active"):
        if len(tokens) < 2:
            return False, "usage: rak branch current <folder>"
        folder = tokens[1]
        return branch_current(folder, cwd=cwd)

    if op in ("delete", "rm", "remove", "del"):
        if len(tokens) < 3 or "--yes" not in tokens:
            return False, "usage: rak branch delete <folder> <branch_name> --yes (permanent; deletes branch overlay)"
        folder = tokens[1]
        name = tokens[2]
        return branch_delete(folder, name, cwd=cwd)

    # Alternate syntax: `rak branch <folder> <op> ...`
    if len(tokens) >= 2:
        sub_op = tokens[1].lower()
        folder = tokens[0]
        if sub_op in ("create", "new", "add") and len(tokens) >= 3:
            name = tokens[2]
            color = tokens[3] if len(tokens) > 3 else "blue"
            return branch_create(folder, name, color=color, cwd=cwd)
        if sub_op in ("switch", "checkout", "set") and len(tokens) >= 3:
            return branch_switch(folder, tokens[2], cwd=cwd)
        if sub_op in ("list", "ls", "show"):
            return branch_list(folder, cwd=cwd)
        if sub_op in ("current", "status", "active"):
            return branch_current(folder, cwd=cwd)
        if sub_op in ("delete", "rm", "remove", "del") and len(tokens) >= 3 and "--yes" in tokens:
            return branch_delete(folder, tokens[2], cwd=cwd)

    return False, f"unknown branch operation: {op!r} (use: create, switch, list, current, delete)"


def execute_command(line, wait=True, timeout=120.0, cwd=None):

    line = (line or "").strip()
    if not line or line.startswith("#"):
        return True, ""

    parts = line.split(None, 1)
    cmd = parts[0].lower()
    rest = parts[1].strip() if len(parts) > 1 else ""

    if cmd in ("help", "?"):
        return True, _help_text()

    if cmd in ("fs", "file", "files"):
        return _execute_fs_command(rest, wait=wait, timeout=timeout, cwd=cwd or os.getcwd())

    if cmd in ("ls", "cat", "grep"):
        return _execute_fs_command(f"{cmd} {rest}", wait=wait, timeout=timeout, cwd=cwd or os.getcwd())

    if cmd in ("branch", "neobranch"):
        return _execute_branch_command(rest, wait=wait, timeout=timeout, cwd=cwd or os.getcwd())

    if cmd == "list":
        a = actions()
        return True, ("safe project-tree transactions (recommended):\n"
                      "  ls [folder] [--branch-only] (alias: fs ls)\n"
                      "  cat <file> (alias: fs cat)\n"
                      "  grep <regex> [path] (alias: fs grep)\n"
                      "  fs write <file> --from <utf8-input-file>\n"
                      "  fs replace <file> <old-text> <new-text> [--all]\n"
                      "  branch create <partition-folder> <name> [color]\n"
                      "  branch switch <partition-folder> <name|source>\n"
                      "  branch list [partition-folder]\n"
                      "  branch current <partition-folder>\n"
                      "  branch delete <partition-folder> <name> --yes\n"
                      "  fs copy <source> <destination-folder>\n"
                      "  fs move <source> <destination-folder>\n"
                      "  fs delete <path> --yes\n"
                      "  fs mkdir|touch <parent-folder> <name>\n"
                      "  fs rename <path> <new-name> [--force]\n"
                      "  fs symlink <parent-folder> <name> <android-target>\n"
                      "  fs make-filesystem <partition-folder> --type <erofs|ext4> --yes\n\n"
                      "single:\n  " + "\n  ".join(a["single"]) +
                      "\n\nbatch:\n  " + "\n  ".join(a["batch"]))

    if cmd == "security":
        if rest.strip().lower() not in ("check", "self-test", "selftest"):
            return False, "usage: rak security check"
        from core.security_check import run_security_check
        return run_security_check()

    if cmd in ("benchmark", "perf"):
        if rest.strip().lower() not in ("startup", "start"):
            return False, "usage: rak benchmark startup"
        from core.startup_benchmark import run_startup_benchmark
        return run_startup_benchmark()

    if cmd == "badge":
        path = _cli_path(rest, cwd)
        b = badge(path)
        return True, f"badge({path}) = {b!r}"

    if cmd == "batch":
        sub = rest.split(None, 1)
        if len(sub) < 2:
            return False, "usage: batch <action> <path1> | <path2> ..."
        baction = sub[0]
        paths = [_cli_path(p, cwd) for p in sub[1].split("|") if p.strip()]
        ok, msg = run_batch(baction, paths)
        if ok and wait:
            wait_idle(timeout=timeout)
        return ok, msg

    if cmd in ("doc", "docs", "agent", "init-agent"):
        from core.rak_agent_docs import update_all_agent_files
        updated = update_all_agent_files(os.getcwd())
        return True, f"Agent instructions synchronized ({', '.join(updated)})"

    if cmd in ("policy", "sepolicy", "neocat_policy"):
        import shlex
        try:
            tokens = [_cli_unquote(token) for token in shlex.split(rest, posix=False)]
        except ValueError as exc:
            return False, f"invalid policy command: {exc}"
        if len(tokens) < 2 or tokens[0].lower() not in ("plan", "scan"):
            return False, "usage: rak policy plan <unpacked_partition> [--apply] [--json report.json]"
        fs_root = tokens[1]
        apply = "--apply" in tokens
        json_path = ""
        if "--json" in tokens:
            pos = tokens.index("--json")
            if pos + 1 >= len(tokens):
                return False, "usage: --json <report.json>"
            json_path = tokens[pos + 1]
        from core.rak_policy_view import run_policy_in_view
        try:
            return run_policy_in_view(cwd or os.getcwd(), fs_root,
                                      apply=apply, json_path=json_path)
        except Exception as exc:
            return False, f"Policy plan gagal: {exc}"

    if cmd in ("automation", "renxec"):
        from core.rennsproject_bridge import run_automation_cli
        import shlex
        try:
            tokens = [_cli_unquote(token) for token in shlex.split(rest, posix=False)]
        except ValueError as exc:
            return False, f"invalid automation command: {exc}"
        if not tokens:
            return False, "usage: rak automation <path_to_renxec> [target_fs]"
        renxec_file = tokens[0]
        fs_target = tokens[1] if len(tokens) > 1 else None
        return run_automation_cli(renxec_file, fs_name=fs_target, project_base=os.getcwd(), main_ref=_main_ref)

    if cmd in ("compare", "compare_fs"):
        import shlex
        try:
            tokens = [_cli_unquote(token) for token in shlex.split(rest, posix=False)]
        except ValueError as exc:
            return False, f"invalid compare command: {exc}"
        return _execute_compare_command(tokens, cwd=cwd or os.getcwd(), main_ref=_main_ref)
    if cmd in ("img", "vfs", "image"):
        from core.rak_img_cli import run_img_cli
        import shlex
        try:
            tokens = [_cli_unquote(token) for token in shlex.split(rest, posix=False)]
        except ValueError as exc:
            return False, f"invalid image command: {exc}"
        return run_img_cli(tokens, cwd=os.getcwd(), main_ref=_main_ref)

    if cmd in ("mounted", "mounts"):
        from core.rak_img_cli import cmd_mounted
        return cmd_mounted(main_ref=_main_ref)


    action = cmd
    path = rest
    name = None
    # ekstrak trailing "name=..." kalau ada
    if " name=" in (" " + path):
        # pisah token name= terakhir
        idx = path.rfind("name=")
        if idx >= 0 and (idx == 0 or path[idx - 1] == " "):
            name = path[idx + 5:].strip().strip('"').strip("'")
            path = path[:idx].strip()
    if not path:
        return False, f"usage: {action} <path> [name=...]"
    if action == "delete":
        return False, "use: rak fs delete <path> --yes"
    path = _cli_path(path, cwd)
    if wait:
        return run_and_wait(action, path, name=name, timeout=timeout)
    return run(action, path, name=name)


def _help_text():
    return (
        "RAK_CLI commands:\n"
        "  unpack <file>                   auto-detect & unpack partition / archive\n"
        "  repack <folder>                 auto-detect & repack partition directory\n"
        "  compare <dir_a> <dir_b>         compare active NeoBranch views of two partitions\n"
        "  automation <file.renxec> [fs]   run automation on selected active NeoBranch or source\n"
        "  policy plan <folder> [--apply]  plan NeoCat patch on active NeoBranch view\n"
        "  mount <image>                   mount image as virtual folder in GUI tree\n"
        "  unmount <image>                 unmount image from GUI tree\n"
        "  mounted                         list currently mounted images in GUI\n"
        "  img ls <image> [subpath] [-l|-r]list files inside image (no unpack!)\n"
        "  img cat <image> <path>          print file content from image to stdout\n"
        "  img stat <image> <path>         show metadata (perm, size, selinux)\n"
        "  img extract <image> <path> [dst]extract file or folder from image\n"
        "  img write <image> <path> <file> edit image in-place (active branch blocked)\n"
        "  img perm <image> <path> <octal> change file mode/permissions in image\n"
        "  img selinux <image> <p> <ctx>   change SELinux context in image\n"
        "  img rm <image> <path> [-r]      delete file/folder inside image\n"
        "  img info <image>                show image filesystem & block usage info\n"
        "  branch create <fs> <name> [col] create and activate a new NeoBranch\n"
        "  branch switch <fs> <name|src>   switch between branches or back to source\n"
        "  branch list [fs]                list NeoBranches and show active branch\n"
        "  branch current <fs>             show currently active NeoBranch\n"
        "  branch delete <fs> <name> --yes permanently delete a NeoBranch\n"
        "  ls [folder] [--branch-only]    list active NeoBranch project view\n"
        "  cat <file>                     read active NeoBranch file content\n"
        "  grep <regex> [path]            search active NeoBranch project view\n"
        "  fs ls [folder] [--branch-only] list active NeoBranch project view\n"
        "  fs cat <file>                  read active NeoBranch file content\n"
        "  fs grep <regex> [path]         search active NeoBranch project view\n"
        "  (legacy convert/unpack/APK actions are blocked on active branch targets)\n"
        "  fs write <file> --from <input>   write UTF-8 text through NeoBranch\n"
        "  fs replace <file> <old> <new>   replace text through NeoBranch [--all]\n"
        "  fs copy <src> <folder>          copy through NeoBranch/config-aware tree pipeline\n"
        "  fs move <src> <folder>          move through NeoBranch/config-aware tree pipeline\n"
        "  fs mkdir <folder> <name>        create a branch-aware folder\n"
        "  fs touch <folder> <name>        create a branch-aware empty file\n"
        "  fs symlink <folder> <name> <target>  create non-overwriting Android symlink\n"
        "  fs rename <path> <new-name> [--force] rename with Android config remapping\n"
        "  fs delete <path> --yes          permanently delete through RAK task queue\n"
        "  fs make-filesystem <folder> --type <erofs|ext4> [--mount-point /p] --yes\n"
        "  <action> <path>                 single-target action (e.g. convert_sparse, delete)\n"
        "  batch <action> <p1> | <p2> ...  multi-target action (e.g. batch unpack a.img | b.img)\n"
        "  list                            list available actions\n"
        "  refresh                         refresh GUI FileTree\n"
        "  doc                             generate/update CLAUDE.md & AGENTS.md\n"
        "  badge <path>                    show live fs_badge\n"
        "  security check                  run local security smoke checks\n"
        "  benchmark startup               measure cold launcher/editor startup paths\n"
        "  help                            this help\n"
    )


class _StdinListener(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True, name="RAK_CLI-stdin")
        self._stop = threading.Event()

    def run(self):
        import sys
        print("[RAK_CLI] stdin listener aktif — ketik 'help'")
        while not self._stop.is_set():
            try:
                line = sys.stdin.readline()
            except Exception:
                break
            if not line:
                break
            line = line.strip()
            if line in ("exit", "quit"):
                break
            if not line:
                continue
            ok, msg = execute_command(line)
            tag = "OK " if ok else "ERR"
            print(f"[RAK_CLI] {tag} | {msg}")

    def stop(self):
        self._stop.set()


_listener = None


def start_stdin():
    global _listener
    if _listener is None:
        _listener = _StdinListener()
        _listener.start()
    return _listener


def maybe_autostart():

    if os.environ.get("RAK_CLI", "").strip() in ("1", "true", "on", "yes"):
        start_stdin()


def _bootstrap_headless(target_path=None):

    from PySide6.QtWidgets import QApplication, QWidget
    from main_ext.class_FileTree import FileTree
    from main_ext.class_ConfigEngine import ConfigEngine
    from core.task_queue import TaskQueue
    from core.threader import run_in_thread

    app = QApplication.instance() or QApplication(sys.argv[:1])

    class _HeadlessMain(QWidget):
        def __init__(self):
            super().__init__()
            self.task_queue = TaskQueue(run_in_thread)
            self._tasks = set()
            self.last_failed = None
            self.super_tabs = []
            self.active_tree = None

        def push_task(self, name): self._tasks.add(name)
        def pop_task(self, name, ok=True): self._tasks.discard(name)
        def maybe_finish_loading(self): pass
        def set_loading_failed(self, text="Failed"): self.last_failed = text
        def set_loading_finished(self, text="Done"): pass
        def hide_erofs_loading(self): pass
        def open_super_tab(self, img_path): self.super_tabs.append(img_path)

    fake = _HeadlessMain()
    tree = FileTree()
    tree.setParent(fake)
    fake.active_tree = tree

    base = os.getcwd()
    if target_path:
        norm = os.path.normpath(target_path)
        try:
            from core.rak_neobranch_view import project_root
            base = project_root(norm)
        except ValueError:
            cand = norm if os.path.isdir(norm) else os.path.dirname(norm)
            base = cand
    if not os.path.isdir(base):
        base = os.getcwd()

    tree.base = base
    tree.engine = ConfigEngine(base)
    try:
        tree.engine.tree_ref = tree
    except Exception:
        pass
    try:
        _full = tree.engine.load_full_state()
        tree.ctx = _full.get("ctx", {})
        tree.fs = _full.get("fs", {})
        tree.ctx_neocat = _full.get("neocat", {})
    except Exception:
        pass
    tree.folder_has_child = {}
    tree.fsdetect_cache = {}
    tree._skip_initial_reload = True
    try:
        tree.populate_root()
    except Exception:
        pass
    attach(fake)
    return app, fake, tree


def _ipc_file_path():
    path = os.path.join(IPC_RUNTIME_DIR, "rak_ipc.json")
    migrate_legacy_file(os.path.join(APP_ROOT, "rak_ipc.json"), path)
    os.makedirs(IPC_RUNTIME_DIR, exist_ok=True)
    return path


class _IpcServer(threading.Thread):

    def __init__(self, host="127.0.0.1"):
        super().__init__(daemon=True, name="RAK_CLI-IPC-Server")
        self.host = host
        self.server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server_sock.bind((self.host, 0))
        self.port = self.server_sock.getsockname()[1]
        self.token = uuid.uuid4().hex
        self._running = True
        self.server_sock.listen(5)
        self._save_info()

    def run(self):
        while self._running:
            try:
                client, _ = self.server_sock.accept()
                threading.Thread(target=self._handle_client, args=(client,), daemon=True).start()
            except Exception:
                break

    def _save_info(self):
        try:
            info = {
                "port": self.port,
                "pid": os.getpid(),
                "token": self.token,
            }
            with open(_ipc_file_path(), "w", encoding="utf-8") as f:
                json.dump(info, f, indent=2)
        except Exception as e:
            print(f"[RAK_CLI] Gagal simpan IPC info: {e}")

    def _handle_client(self, client):
        try:
            client.settimeout(600.0)  # 10 menit max untuk unpack/repack besar
            raw = b""
            while b"\n" not in raw:
                chunk = client.recv(4096)
                if not chunk:
                    break
                raw += chunk
            if not raw:
                client.close()
                return
            data = json.loads(raw.decode("utf-8", errors="replace").strip())
            if data.get("token") != self.token:
                res = {"ok": False, "msg": "Invalid auth token"}
            else:
                cmd = data.get("cmd", "")
                cwd = data.get("cwd", "")
                res = _handle_ipc_command(cmd, cwd)
            resp_bytes = json.dumps(res).encode("utf-8") + b"\n"
            client.sendall(resp_bytes)
        except Exception as e:
            try:
                err_resp = json.dumps({"ok": False, "msg": f"IPC error: {e}"}).encode("utf-8") + b"\n"
                client.sendall(err_resp)
            except Exception:
                pass
        finally:
            try:
                client.close()
            except Exception:
                pass

    def stop(self):
        self._running = False
        try:
            self.server_sock.close()
        except Exception:
            pass
        try:
            p = _ipc_file_path()
            if os.path.exists(p):
                with open(p, "r", encoding="utf-8") as f:
                    info = json.load(f)
                if info.get("token") == self.token:
                    os.remove(p)
        except Exception:
            pass


_ipc_server = None


def start_ipc_server():
    global _ipc_server
    if _ipc_server is None:
        try:
            _ipc_server = _IpcServer()
            _ipc_server.start()
            atexit.register(stop_ipc_server)
            print(f"[RAK_CLI] IPC server live di 127.0.0.1:{_ipc_server.port}")
        except Exception as e:
            print(f"[RAK_CLI] Gagal start IPC server: {e}")
    return _ipc_server


def stop_ipc_server():
    global _ipc_server
    if _ipc_server is not None:
        try:
            _ipc_server.stop()
        except Exception:
            pass
        _ipc_server = None


def _handle_ipc_command(cmd, cwd=""):
    cmd = (cmd or "").strip()
    if not cmd:
        return {"ok": True, "msg": ""}

    parts = cmd.split(None, 1)
    action = parts[0].lower()
    rest = parts[1].strip() if len(parts) > 1 else ""

    # Perintah khusus GUI: refresh
    if action == "refresh":
        bridge = _ensure_bridge()
        if _on_qt_thread():
            bridge._do_refresh()
        else:
            QMetaObject.invokeMethod(bridge, "_do_refresh", Qt.BlockingQueuedConnection)
        ok, msg = bridge._result
        return {"ok": ok, "msg": msg}

    if action in ("doc", "docs", "agent", "init-agent"):
        from core.rak_agent_docs import update_all_agent_files
        updated = update_all_agent_files(cwd or os.getcwd())
        return {"ok": True, "msg": f"Agent instructions synchronized ({', '.join(updated)})"}


    if action not in ("help", "?", "list", "actions", "mounted", "mounts", "refresh"):
        bases = [getattr(tree, "base", None) for tree in (
            getattr(_main_ref, "tree", None), getattr(_main_ref, "tree2", None),
            getattr(_main_ref, "active_tree", None),
        ) if tree is not None]
        bases = [os.path.realpath(base) for base in bases if base]
        if bases:
            import shlex
            target = ""
            try:
                tokens = [_cli_unquote(token) for token in shlex.split(rest, posix=False)]
                if action in ("fs", "file", "files", "branch", "neobranch"):
                    target = tokens[1] if len(tokens) > 1 else ""
                elif tokens:
                    target = tokens[0]
            except ValueError:
                pass
            target = _cli_path(target, cwd) if target and "|" not in target else ""

            def _in_open_tree(path):
                if not path:
                    return False
                candidate = os.path.normcase(os.path.realpath(path))
                for base in bases:
                    root = os.path.normcase(base)
                    try:
                        if os.path.commonpath((candidate, root)) == root:
                            return True
                    except ValueError:
                        continue
                return False

            if not _in_open_tree(target) and not _in_open_tree(cwd):
                return {"ok": False, "project_mismatch": True,
                        "msg": "GUI is showing another ROM project"}

    if action in ("automation", "renxec"):
        from core.rennsproject_bridge import run_automation_cli
        import shlex
        try:
            tokens = [_cli_unquote(token) for token in shlex.split(rest, posix=False)]
        except ValueError as exc:
            return {"ok": False, "msg": f"invalid automation command: {exc}"}
        if not tokens:
            return {"ok": False, "msg": "usage: rak automation <path_to_renxec> [target_fs]"}
        renxec_file = _cli_path(tokens[0], cwd)
        fs_target = tokens[1] if len(tokens) > 1 else None
        ok, msg = run_automation_cli(renxec_file, fs_name=fs_target, project_base=cwd or os.getcwd(), main_ref=_main_ref)
        return {"ok": ok, "msg": msg}

    if action in ("policy", "sepolicy", "neocat_policy"):
        ok, msg = execute_command(cmd, wait=True, cwd=cwd or os.getcwd())
        return {"ok": ok, "msg": msg}

    if action in ("compare", "compare_fs"):
        import shlex
        try:
            tokens = [_cli_unquote(token) for token in shlex.split(rest, posix=False)]
        except ValueError as exc:
            return {"ok": False, "msg": f"invalid compare command: {exc}"}
        ok, msg = _execute_compare_command(tokens, cwd=cwd or os.getcwd(), main_ref=_main_ref)
        return {"ok": ok, "msg": msg}

    if action in ("img", "vfs", "image"):
        from core.rak_img_cli import run_img_cli
        tokens = rest.split()
        ok, msg = run_img_cli(tokens, cwd=cwd, main_ref=_main_ref)
        return {"ok": ok, "msg": msg}

    if action in ("mounted", "mounts"):
        from core.rak_img_cli import cmd_mounted
        ok, msg = cmd_mounted(main_ref=_main_ref)
        return {"ok": ok, "msg": msg}

    # `fs` has two positional paths (or a destructive acknowledgement), so it
    # must be parsed before the legacy one-path IPC normalizer below.
    if action in ("fs", "file", "files", "branch", "neobranch"):
        ok, msg = execute_command(cmd, wait=True, cwd=cwd or os.getcwd())
        return {"ok": ok, "msg": msg}

    # Resolusi path relatif menjadi absolut menggunakan cwd klien
    if cwd and rest and not action.startswith("-") and action not in ("help", "list", "?", "badge"):
        if action == "batch":
            sub = rest.split(None, 1)
            if len(sub) >= 2:
                baction = sub[0]
                bpaths = [p.strip() for p in sub[1].split("|") if p.strip()]
                bpaths_abs = [
                    os.path.normpath(os.path.join(cwd, p)) if not os.path.isabs(p) else p
                    for p in bpaths
                ]
                rest = f"{baction} " + " | ".join(bpaths_abs)
                cmd = f"batch {rest}"
        else:
            name_part = ""
            p = rest
            if " name=" in (" " + rest):
                idx = rest.rfind("name=")
                if idx >= 0:
                    name_part = " " + rest[idx:]
                    p = rest[:idx].strip()
            if not os.path.isabs(p):
                p = os.path.normpath(os.path.join(cwd, p))
            cmd = f"{action} {p}{name_part}"
    elif action == "badge" and cwd and rest and not os.path.isabs(rest):
        rest = os.path.normpath(os.path.join(cwd, rest))
        cmd = f"badge {rest}"

    ok, msg = execute_command(cmd, wait=True, cwd=cwd or os.getcwd())
    if ok and action in ("unpack", "repack", "batch"):
        try:
            from core.rak_agent_docs import update_all_agent_files
            update_all_agent_files(cwd)
        except Exception:
            pass
    return {"ok": ok, "msg": msg}


def _call_ipc_server(cmd_line, cwd=""):

    ipc_file = _ipc_file_path()
    if not os.path.exists(ipc_file):
        return None
    live_endpoint = False
    try:
        with open(ipc_file, "r", encoding="utf-8") as f:
            info = json.load(f)
        port = info.get("port")
        token = info.get("token")
        pid = info.get("pid")
        if not port or not token:
            return None

        # Cek apakah PID GUI masih hidup di Windows
        if sys.platform == "win32" and pid:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            h_proc = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if not h_proc:
                try:
                    os.remove(ipc_file)
                except Exception:
                    pass
                return None
            kernel32.CloseHandle(h_proc)

        live_endpoint = True
        with socket.create_connection(("127.0.0.1", port), timeout=600.0) as s:
            payload = json.dumps({"cmd": cmd_line, "cwd": cwd or os.getcwd(), "token": token}) + "\n"
            s.sendall(payload.encode("utf-8"))

            raw = b""
            while b"\n" not in raw:
                chunk = s.recv(4096)
                if not chunk:
                    break
                raw += chunk
        if raw:
            return json.loads(raw.decode("utf-8", errors="replace").strip())
    except Exception as exc:
        if live_endpoint:
            return {"ok": False, "msg": f"GUI IPC tidak dapat dihubungi: {exc}"}
        return None
    return None


def _bundled_cli_path():

    executable = os.path.abspath(sys.executable)
    if os.path.basename(executable).lower() == "run.exe" and os.path.isfile(executable):
        return executable
    return None


def _rak_bat_content(cli_exe=None):

    if cli_exe:
        return (
            "@echo off\r\n"
            f'"{cli_exe}" --rak-cli %*\r\n'
            "exit /b %ERRORLEVEL%\r\n"
        )
    return (
        "@echo off\r\n"
        f'set "PYTHONPATH={_proj_root};%PYTHONPATH%"\r\n'
        f'"{sys.executable}" "{os.path.join(_this_dir, "rak_cli.py")}" %*\r\n'
        "exit /b %ERRORLEVEL%\r\n"
    )


def _ensure_user_path_entry(path_entry):

    normalized = os.path.normcase(os.path.normpath(path_entry))

    current = os.environ.get("PATH", "")
    current_entries = [p for p in current.split(os.pathsep) if p]
    if not any(
        os.path.normcase(os.path.normpath(os.path.expandvars(p))) == normalized
        for p in current_entries
    ):
        os.environ["PATH"] = current + (os.pathsep if current else "") + path_entry

    if os.name != "nt":
        return

    try:
        import winreg
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            "Environment",
            0,
            winreg.KEY_READ | winreg.KEY_SET_VALUE,
        ) as key:
            try:
                stored_path, value_type = winreg.QueryValueEx(key, "Path")
            except FileNotFoundError:
                stored_path, value_type = "", winreg.REG_EXPAND_SZ

            stored_entries = [p for p in str(stored_path).split(";") if p]
            exists = any(
                os.path.normcase(os.path.normpath(os.path.expandvars(p))) == normalized
                for p in stored_entries
            )
            if not exists:
                new_path = str(stored_path).rstrip(";")
                new_path += (";" if new_path else "") + path_entry
                winreg.SetValueEx(key, "Path", 0, value_type, new_path)

        # Tell Explorer/new processes that the User environment changed.
        import ctypes
        result = ctypes.c_ulong()
        ctypes.windll.user32.SendMessageTimeoutW(
            0xFFFF, 0x001A, 0, "Environment", 0x0002, 2000,
            ctypes.byref(result),
        )
    except Exception as e:


        print(f"[RAK_CLI] PATH launcher registration skipped: {e}")


def ensure_rak_bat():

    cli_exe = _bundled_cli_path()
    bat_content = _rak_bat_content(cli_exe)

    # 1. Local launcher: source root in development, executable directory in a
    # compiled distribution.
    local_dir = os.path.dirname(os.path.abspath(sys.executable)) if cli_exe else _proj_root
    local_bat = os.path.join(local_dir, "rak.bat")
    try:
        with open(local_bat, "w", encoding="utf-8") as f:
            f.write(bat_content)
    except Exception:
        pass

    # 2. Persistent command shim used by ordinary CMD/PowerShell windows.
    app_data = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    bin_dir = os.path.join(app_data, "RennsAndroidKitchen", "bin")
    os.makedirs(bin_dir, exist_ok=True)
    bin_bat = os.path.join(bin_dir, "rak.bat")
    try:
        with open(bin_bat, "w", encoding="utf-8") as f:
            f.write(bat_content)
        _ensure_user_path_entry(bin_dir)
    except Exception as e:
        print(f"[RAK_CLI] Gagal membuat launcher rak: {e}")


def _main_cli():

    _force_utf8_stdio()
    args = sys.argv[1:]
    if not args:
        print(_help_text())
        sys.exit(0)


    cmd_line = (subprocess.list2cmdline(args)
                if args[0].lower() in ("fs", "file", "files", "branch", "neobranch",
                                       "policy", "sepolicy", "neocat_policy",
                                       "automation", "renxec") else " ".join(args))
    first = args[0].lower()
    if first in ("help", "-h", "--help"):
        print(_help_text())
        sys.exit(0)

    if (first in ("ls", "cat", "grep") or
            (first in ("fs", "file", "files") and len(args) > 1 and args[1].lower() in ("ls", "list", "cat", "read", "grep"))):
        read_op = first if first in ("ls", "cat", "grep") else args[1].lower()
        read_args = args[1:] if first in ("ls", "cat", "grep") else args[2:]
        ok, msg = _execute_fs_read_command(read_op, read_args, cwd=os.getcwd())
        print(msg, file=sys.stdout if ok else sys.stderr)
        sys.exit(0 if ok else 1)

    if first in ("branch", "neobranch") and len(args) > 1:
        branch_op = args[1].lower()
        if branch_op in ("list", "ls", "show") and len(args) <= 3:
            ok, msg = branch_list(args[2] if len(args) == 3 else None, cwd=os.getcwd())
            print(msg, file=sys.stdout if ok else sys.stderr)
            sys.exit(0 if ok else 1)
        if branch_op in ("current", "status", "active") and len(args) == 3:
            ok, msg = branch_current(args[2], cwd=os.getcwd())
            print(msg, file=sys.stdout if ok else sys.stderr)
            sys.exit(0 if ok else 1)

    if first in ("compare", "compare_fs") and len(args) > 1:
        ok, msg = _execute_compare_command(args[1:], cwd=os.getcwd())
        print(msg, file=sys.stdout if ok else sys.stderr)
        sys.exit(0 if ok else 1)

    # Fast-path: Virtual In-Image (VFS) operations run directly in pure Python
    if first in ("img", "vfs", "image"):
        from core.rak_img_cli import run_img_cli
        ok, msg = run_img_cli(args[1:], cwd=os.getcwd(), main_ref=_main_ref)
        if msg:
            if not ok:
                print(f"[RAK_CLI:IMG] ERR | {msg}", file=sys.stderr)
            else:
                print(msg)
        sys.exit(0 if ok else 1)


    if first in ("policy", "sepolicy", "neocat_policy"):
        ok, msg = execute_command(cmd_line)
        tag = "OK " if ok else "ERR"
        print(f"[RAK_CLI:POLICY] {tag} | {msg}")
        sys.exit(0 if ok else 1)


    if first == "security":
        ok, msg = execute_command(cmd_line)
        tag = "OK " if ok else "ERR"
        print(f"[RAK_CLI:SECURITY] {tag} | {msg}")
        sys.exit(0 if ok else 1)

    if first in ("benchmark", "perf"):
        ok, msg = execute_command(cmd_line)
        tag = "OK " if ok else "ERR"
        print(f"[RAK_CLI:BENCHMARK] {tag} | {msg}")
        sys.exit(0 if ok else 1)

    if first in ("mounted", "mounts") or (first == "mount" and len(args) == 1):
        ipc_res = _call_ipc_server("mounted", os.getcwd())
        if ipc_res is not None and "usage: mounted" not in ipc_res.get("msg", "").lower():
            print(ipc_res.get("msg", ""))
            sys.exit(0 if ipc_res.get("ok", False) else 1)
        from core.rak_img_cli import cmd_mounted
        ok, msg = cmd_mounted(main_ref=_main_ref)
        print(msg)
        sys.exit(0 if ok else 1)

    # 1. Coba panggil GUI yang sedang hidup lewat IPC
    ipc_res = _call_ipc_server(cmd_line, os.getcwd())
    if ipc_res is not None and ipc_res.get("project_mismatch"):
        ipc_res = None
    if ipc_res is not None and first in ("fs", "file", "files") and not ipc_res.get("ok", False):
        # An older GUI may still own the global endpoint but have another ROM
        # open. Retry structured filesystem work in this CLI's own project.
        message = ipc_res.get("msg", "").lower()
        if any(marker in message for marker in (
                "not a managed project item", "not a managed project folder",
                "could not resolve tree item", "unknown action: 'fs'")):
            ipc_res = None
    if ipc_res is not None:
        msg = ipc_res.get("msg", "")
        # Older GUI in memory might return generic action error / usage string
        if first in ("branch", "neobranch") and (
            ("usage: " in msg and "[name=...]" in msg)
            or "unknown action:" in msg
            or "tidak dikenal" in msg
        ):
            ipc_res = None
        else:
            ok = ipc_res.get("ok", False)
            tag = "OK " if ok else "ERR"
            print(f"[RAK_CLI:GUI] {tag} | {msg}")
            sys.exit(0 if ok else 1)

    # 2. GUI tidak sedang berjalan: fallback ke mode headless standalone
    if first == "mount" and len(args) > 1 and not is_attached():
        print(f"[RAK_CLI] Virtual tree mounting is a Kitchen GUI feature. The GUI is not currently running.")
        print(f"          However, you can read and edit this image directly via CLI without GUI:")
        print(f"          - Inspect files:  rak img ls {args[1]}")
        print(f"          - Read file:      rak img cat {args[1]} <internal_path>")
        print(f"          - Edit in-place:  rak img write {args[1]} <internal_path> <local_file>")
        print(f"          - Show usage:     rak img info {args[1]}")
        sys.exit(0)

    if first == "unmount" and not is_attached():
        print(f"[RAK_CLI] Kitchen GUI is not currently running. No active virtual mounts to unmount.")
        sys.exit(0)
    if first in ("doc", "docs", "agent", "init-agent"):
        from core.rak_agent_docs import update_all_agent_files
        updated = update_all_agent_files(os.getcwd())
        print(f"[RAK_CLI] OK | Agent instructions synchronized ({', '.join(updated)})")
        sys.exit(0)

    if first in ("list", "actions"):
        ok, msg = execute_command(cmd_line)

        print(msg)
        sys.exit(0 if ok else 1)

    target_path = args[1] if len(args) > 1 else None
    if first in ("fs", "file", "files") and len(args) > 2:
        target_path = args[2]
    if first in ("branch", "neobranch"):
        if len(args) > 2 and args[1].lower() in ("create", "new", "add", "switch", "checkout", "set", "delete", "rm", "current", "status"):
            target_path = args[2]
        elif len(args) > 1:
            target_path = args[1]
    headless_app = None
    if not is_attached():
        headless_app, _, _ = _bootstrap_headless(target_path)

    if headless_app is not None:
        # FileTree actions marshal back to Qt's main thread. Keep that event
        # loop pumping while the command waits for queued filesystem tasks.
        result = {}
        def _execute_headless():
            try:
                result["value"] = execute_command(cmd_line, wait=True)
            except BaseException as exc:
                result["error"] = exc
        worker = threading.Thread(target=_execute_headless, daemon=True)
        worker.start()
        while worker.is_alive():
            headless_app.processEvents()
            worker.join(0.02)
        headless_app.processEvents()
        if "error" in result:
            raise result["error"]
        ok, msg = result["value"]
    else:
        ok, msg = execute_command(cmd_line, wait=True)
    if ok and first in ("branch", "neobranch"):
        try:
            _call_ipc_server("refresh", os.getcwd())
        except Exception:
            pass
    tag = "OK " if ok else "ERR"
    print(f"[RAK_CLI:STANDALONE] {tag} | {msg}")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    _main_cli()
