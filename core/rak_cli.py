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
from contextvars import copy_context
from core.command_context import (CommandContext, CommandFailure, current_command,
                                  command_scope, failure_from_exception)

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
        self._requests = {}
        self._request_lock = threading.Lock()

    def invoke(self, method, *args, **kwargs):
        request = {'method':method,'args':args,'kwargs':kwargs,'context':copy_context()}
        if QThread.currentThread() == self.thread():
            self._execute_request(request)
        else:
            token = uuid.uuid4().hex
            with self._request_lock:self._requests[token] = request
            try:
                QMetaObject.invokeMethod(self, '_do_request', Qt.BlockingQueuedConnection, Q_ARG(str,token))
            finally:
                with self._request_lock:self._requests.pop(token,None)
        return request['result']

    def _execute_request(self, request):
        def execute():
            try:return getattr(self,request['method'])(*request['args'],**request['kwargs'])
            except Exception as error:return False,failure_from_exception(error)
        request['result'] = request['context'].run(execute)

    @Slot(str)
    def _do_request(self, token):
        with self._request_lock:request = self._requests[token]
        self._execute_request(request)

    def _read_badge(self, path):
        self._do_badge(path)
        return self._badge_result

    def _refresh(self):
        self._do_refresh()
        return self._result

    def _run_activity(self,options):
        from core.activity_log import collect,render
        owner=getattr(_main_ref,'session',_main_ref)
        return True,render(collect(owner,options),options['json'])

    def _run_kitchen(self,options):
        adapter=getattr(_main_ref,'quick_cli',None)
        if adapter is None:return False,CommandFailure('Open a Qt Quick Kitchen project or use standalone CLI for structured Kitchen commands.')
        if options.get('no_wait') and getattr(adapter.session,'headless',False):
            return False,CommandFailure('--no-wait requires a live Kitchen session so the task can continue after the CLI exits.')
        kind=options['namespace'];action=options['action']
        if kind in ('archive','payload','super'):return adapter.kitchen(options)
        aliases={'apk':{'decompile':'apktool_decompile','build':'apktool_build','build-sign':'apktool_build_sign',
                        'build-keeporig':'apktool_build_keeporig','sign':'apktool_sign','unsign':'apktool_unsign'},
                 'br':{'compress':'convert_img_to_br','decompress':'unpack_br_to_image'},
                 'dat':{'to-image':'unpack_dat_to_image','to-folder':'unpack_dat_to_folder'}}
        if kind=='dat' and options['path'].lower().endswith('.br'):
            return adapter.single('unpack_dat_br_to_'+('image' if action=='to-image' else 'folder'),options['path'])
        return adapter.single(aliases[kind][action],options['path'])

    def _run_unix(self,options):
        adapter=getattr(_main_ref,'quick_cli',None)
        if adapter is None:return False,CommandFailure('Unix-style transactions require a Qt Quick workspace or standalone CLI.')
        return adapter.unix(options)

    def _run_android_metadata(self,path,column,value):
        adapter=getattr(_main_ref,'quick_cli',None)
        if adapter is None:raise ValueError('Android metadata requires a Kitchen workspace')
        return adapter.android_metadata(path,column,value)

    def _project_root(self,path):
        return _main_ref.quick_cli.files(path)[0].base

    def _inspect_path(self,path,follow=False):
        from core.kitchen_inspect import describe
        adapter=getattr(_main_ref,'quick_cli',None)
        if adapter is None:return False,CommandFailure('inspect requires a Qt Quick workspace or standalone CLI')
        return True,describe(adapter,path,follow=follow)

    def _run_repack_output(self,options):
        adapter=getattr(_main_ref,'quick_cli',None)
        if adapter is None:return False,CommandFailure('Explicit repack output requires a Qt Quick workspace or standalone CLI')
        return adapter.repack_output(options)

    def _agent_service(self):
        owner=getattr(_main_ref,'session',_main_ref)
        service=getattr(owner,'_agent',None)
        if service is None:return False,CommandFailure('Open a Qt Quick Kitchen project to use the agent UI API.')
        return True,service

    def _agent_open_document(self,path):
        ok,service=self._agent_service()
        return (True,bool(service._document_row(path))) if ok else (ok,service)

    def _agent_transform_document(self,path):
        ok,service=self._agent_service()
        return (True,service.read_for_transform(path)) if ok else (ok,service)

    def _run_agent(self,options,cwd):
        ok,service=self._agent_service()
        if not ok:return ok,service
        return True,service.handle(options,cwd)

    def _agent_touch(self,path,kind):
        ok,service=self._agent_service()
        if ok:service.activity.touch(path,kind)
        return True,''

    # dipanggil via invokeMethod; hasil disimpan ke self._result
    @Slot(str, str)
    def _do_single(self, action, path):
        self._result = self._run_single(action, path)

    @Slot(str, str, str)
    def _do_single_named(self, action, path, name):
        self._result = self._run_single(action, path, name=name or None)

    @Slot(str)
    def _do_badge(self, path):
        if getattr(_main_ref,"quick_cli",None):
            self._badge_result=_main_ref.quick_cli.badge(path);return
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
        if getattr(_main_ref,"quick_cli",None):
            _main_ref.active_tree.reloadMetadata();self._result=(True,"Tree refreshed");return
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
        if getattr(_main_ref,"quick_cli",None):return _main_ref.quick_cli.single(action,path,name)
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
        if getattr(_main_ref,"quick_cli",None):return _main_ref.quick_cli.batch(action,paths)
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
        if getattr(_main_ref,"quick_cli",None):return _main_ref.quick_cli.transfer(mode,source,destination)

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
        if getattr(_main_ref,"quick_cli",None):return _main_ref.quick_cli.make_filesystem(folder,fs_type,mount_point)

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
        if getattr(_main_ref,"quick_cli",None):return _main_ref.quick_cli.symlink(folder,name,target)
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
        if getattr(_main_ref,"quick_cli",None):return _main_ref.quick_cli.write(path,content)

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
        if getattr(_main_ref,"quick_cli",None):return _main_ref.quick_cli.replace(path,old_text,new_text,replace_all)
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
        if getattr(_main_ref,"quick_cli",None):return _main_ref.quick_cli.branch("create",folder,branch_name,color,cwd)
        tree, tree_base, fs_root, err = _resolve_branch_target(folder, cwd=cwd)
        if err:
            return False, err
        from main_ext.class_NeoBranchManager import NeoBranchManager, BRANCH_COLORS
        color = (color or "blue").lower().strip()
        if color not in BRANCH_COLORS:
            color = "blue"
        nb_mgr = NeoBranchManager(tree_base)
        try:
            nb_mgr.create_checkout_branch(fs_root, branch_name, color)
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
        if getattr(_main_ref,"quick_cli",None):return _main_ref.quick_cli.branch("switch",folder,branch_name,cwd=cwd)
        tree, tree_base, fs_root, err = _resolve_branch_target(folder, cwd=cwd)
        if err:
            return False, err
        from main_ext.class_NeoBranchManager import NeoBranchManager
        nb_mgr = NeoBranchManager(tree_base)

        target_lower = branch_name.lower().strip()
        is_source = target_lower in ("source", "none", "--source", "0")

        if is_source:
            try:
                nb_mgr.checkout_branch(fs_root, None)
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
            nb_mgr.checkout_branch(fs_root, branch_name)
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
        if getattr(_main_ref,"quick_cli",None):return _main_ref.quick_cli.branch("delete",folder,branch_name,cwd=cwd)
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
    return _ensure_bridge().invoke('_run_single',action,path,name=name)


def run_batch(action, paths):
    return _ensure_bridge().invoke('_run_batch',action,paths)


def transfer(mode, source, destination):
    return _ensure_bridge().invoke('_run_transfer',mode,source,destination)


def transfer_and_wait(mode, source, destination, timeout=120.0):
    ok, msg = transfer(mode, source, destination)
    if not ok:
        return ok, msg
    return _wait_dispatched(msg, timeout)


def make_filesystem(folder, fs_type, mount_point=None):
    return _ensure_bridge().invoke('_run_make_filesystem',folder,fs_type,mount_point)


def make_filesystem_and_wait(folder, fs_type, mount_point=None, timeout=300.0):
    ok, msg = make_filesystem(folder, fs_type, mount_point=mount_point)
    if not ok:
        return ok, msg
    return _wait_dispatched(msg, timeout)


def create_symlink(folder, name, target):
    return _ensure_bridge().invoke('_run_create_symlink',folder,name,target)


def write_text(path, content):
    return _ensure_bridge().invoke('_run_write_text',path,str(content))


def replace_text(path, old_text, new_text, replace_all=False):
    return _ensure_bridge().invoke('_run_replace_text',path,old_text,new_text,replace_all)


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
    return _ensure_bridge().invoke('_run_branch_create',folder,branch_name,color=color,cwd=cwd)


def branch_switch(folder, branch_name, cwd=None):
    return _ensure_bridge().invoke('_run_branch_switch',folder,branch_name,cwd=cwd)


def branch_delete(folder, branch_name, cwd=None):
    return _ensure_bridge().invoke('_run_branch_delete',folder,branch_name,cwd=cwd)


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
            changes='physical worktree; native disk edits checkpoint on switch/repack' if nb_mgr.is_physical_checkout(part) else f'edits: {edits} | deletes: {deletes}'
            lines.append(f"  {marker}{name:<14} [{color}] created: {created} | {changes}")

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
        if nb_mgr.is_physical_checkout(fs_root):
            return True,f"Active branch for '{fs_root}': '{name}' [{color}] (physical worktree at {os.path.join(tree_base,fs_root)}; native disk edits checkpoint on switch/repack)"
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
    deadline = None if timeout is None else time.time() + timeout
    # beri sedikit waktu supaya dispatch sempat enqueue
    time.sleep(0.15)
    while deadline is None or time.time() < deadline:
        if _is_idle():
            # konfirmasi dua kali biar tidak ketipu jeda antar-task
            time.sleep(poll)
            if _is_idle():
                return True
        time.sleep(poll)
    return False


def _dispatched_result(message, queue):
    task_ids = getattr(message, 'task_id', None)
    if not task_ids or queue is None:return True,message
    if isinstance(task_ids,str):task_ids = (task_ids,)
    results = {entry[1]:entry for entry in queue.results_since(0) if entry[1] in task_ids}
    for task_id in task_ids:
        if task_id not in results:
            return False,CommandFailure('Queued command finished without a recorded result: '+task_id,application_error=True)
        _,_,ok,detail = results[task_id]
        if not ok:
            return False,detail if isinstance(detail,CommandFailure) else CommandFailure(detail)
    return True,message


def _wait_dispatched(message, timeout, *, return_output=False, as_json=False):
    queue=getattr(_main_ref,'task_queue',None)
    task_ids=getattr(message,'task_id',None)
    if isinstance(task_ids,str):task_ids=(task_ids,)
    if task_ids and queue is not None and hasattr(queue,'wait_for_tasks'):
        if _on_qt_thread():raise RuntimeError('Cannot wait for Kitchen work on the Qt main thread')
        finished=queue.wait_for_tasks(task_ids,timeout)
    else:finished=wait_idle(timeout=timeout)
    if not finished:
        return False,CommandFailure(f'{message} (wait timed out after {timeout}s; task may still be running). '
                                    f'Task IDs: {task_ids}. Inspect rak activity status --json; do not retry automatically.',code='timeout')
    ok,detail = _dispatched_result(message,queue)
    if not ok:return False,detail
    if task_ids and queue is not None:
        results={entry[1]:entry[3] for entry in queue.results_since(0) if entry[1] in task_ids}
        output='\n'.join(str(results[key]) for key in task_ids)
        if return_output:
            if as_json:
                try:json.loads(output)
                except ValueError:output=json.dumps({'outputs':[str(results[key]) for key in task_ids]},ensure_ascii=False)
            return True,output
        return True,f'{message} (selesai)\n{output}'
    return True,f'{message} (selesai)'


def run_and_wait(action, path, name=None, timeout=120.0):

    queue = getattr(_main_ref, "task_queue", None)
    cursor = queue.result_cursor() if queue is not None else None
    ok, msg = run(action, path, name=name)
    if not ok:
        return ok, msg
    if getattr(msg,'task_id',None):return _wait_dispatched(msg,timeout)
    finished = wait_idle(timeout=timeout)
    if not finished:
        return False, CommandFailure(f"{msg} (TIMEOUT setelah {timeout}s)",code='timeout')
    if getattr(msg,'task_id',None):
        task_ok,detail = _dispatched_result(msg,queue)
        return (True,f'{msg} (selesai)') if task_ok else (False,detail)
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
        if getattr(_main_ref,"quick_cli",None):
            results=[result for result in queue.results_since(cursor) if result[1]==_main_ref.quick_cli.last_task]
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
    return _ensure_bridge().invoke('_read_badge',path)


def actions():
    from core.rak_actions import RC_CLI_ACTIONS,RC_CLI_BATCH_ACTIONS
    return {"single":list(RC_CLI_ACTIONS),"batch":list(RC_CLI_BATCH_ACTIONS)}


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


def _execute_fs_command(rest, *, wait=True, timeout=120.0, cwd=None,namespace='fs'):

    from core.cli_arguments import tokens as argument_tokens
    try:
        tokens = argument_tokens(namespace,rest)
    except ValueError as exc:
        return False, f"invalid fs command: {exc}"
    if not tokens:
        return False, "usage: rak fs <ls|cat|grep|write|replace|copy|move|delete|mkdir|touch|rename|make-filesystem> ..."
    op = tokens[0].lower()

    from core.unix_verify import COMMANDS as VERIFY_COMMANDS
    if op in ("ls", "list", "cat", "read", "grep",*VERIFY_COMMANDS):
        return _execute_fs_read_command(op, tokens[1:], cwd=cwd)

    if op in ('context','permission'):
        if len(tokens)!=3:return False,f'usage: rak fs {op} PATH '+('SELINUX_LABEL' if op=='context' else 'OCTAL_MODE')
        ok,message=_ensure_bridge().invoke('_run_android_metadata',_cli_path(tokens[1],cwd),1 if op=='context' else 2,tokens[2])
        return _wait_dispatched(message,timeout) if ok and wait else (ok,message)

    if op == "write":
        if len(tokens) != 4 or tokens[2] != "--from":
            return False, "usage: rak fs write <logical-file> --from <utf8-input-file>"
        target = _cli_path(tokens[1], cwd)
        input_file = _cli_path(tokens[3], cwd)
        try:
            from core.rak_neobranch_view import project_root, ActiveProjectView
            try:
                input_root = project_root(input_file, cwd=cwd)
                input_physical, _ = ActiveProjectView(input_root).resolve_following(input_file)
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
        if len(tokens)==4 and tokens[2]=='--from':
            source=_cli_path(tokens[3],cwd)
            from core.rak_neobranch_view import project_root,ActiveProjectView
            try:source=ActiveProjectView(project_root(source,cwd=cwd)).resolve_following(source)[0]
            except ValueError:pass
            if not source:raise FileNotFoundError(tokens[3])
            if os.path.getsize(source)>8*1024*1024:raise ValueError('Replacement request exceeds 8 MiB')
            try:
                with open(source,encoding='utf-8-sig') as stream:request=json.load(stream)
            except json.JSONDecodeError as error:raise ValueError('Invalid replacement JSON: '+str(error)) from error
            if not isinstance(request,dict) or not isinstance(request.get('old_text'),str) or not isinstance(request.get('new_text'),str) or not isinstance(request.get('replace_all',False),bool):
                raise ValueError('Replacement request requires old_text, new_text strings and optional replace_all boolean')
            return replace_text(_cli_path(tokens[1],cwd),request['old_text'],request['new_text'],replace_all=request.get('replace_all',False))
        if len(tokens) not in (4, 5) or (len(tokens) == 5 and tokens[4] != "--all"):
            return False, "usage: rak fs replace <logical-file> --from <replacement.json> (old_text, new_text, replace_all); or <old-text> <new-text> [--all]"
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
        ok,message = create_symlink(parent, tokens[2], tokens[3])
        return _wait_dispatched(message,timeout) if ok and wait else (ok,message)

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


def _execute_fs_read_command(op, args, *, cwd=None,standalone_stdin=False):
    from core.unix_read import execute
    from core.rak_neobranch_view import project_root
    roots={}
    def root_for(target):
        directory=target if os.path.isdir(target) else os.path.dirname(target)
        key=os.path.normcase(directory)
        if key in roots:return roots[key]
        if getattr(_main_ref, 'quick_cli', None):
            result=_ensure_bridge().invoke('_project_root', target)
            if isinstance(result,tuple) and result[0] is False:raise ValueError(str(result[1]))
        else:result=project_root(target,cwd=cwd or os.getcwd())
        roots[key]=result
        return result
    from core.unix_verify import COMMANDS as VERIFY_COMMANDS,execute as verify
    if op in VERIFY_COMMANDS:return verify(op,args,cwd or os.getcwd(),root_for,allow_stdin=standalone_stdin)
    return execute(op,args,cwd or os.getcwd(),root_for)


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
    branch_timeout=None if timeout==120.0 else timeout
    no_wait=False
    def completed(result):
        ok,message=result
        if ok and no_wait:
            return True,json.dumps({'state':'dispatched','task_id':getattr(message,'task_id',None),'operation':str(message)})
        return _wait_dispatched(message,branch_timeout) if ok and wait and getattr(message,'task_id',None) else result
    try:
        tokens = [_cli_unquote(t) for t in shlex.split(rest, posix=False)]
    except ValueError as exc:
        return False, f"invalid branch command: {exc}"

    if '--timeout' in tokens:
        index=tokens.index('--timeout')
        if index+1>=len(tokens):raise ValueError('usage: --timeout SECONDS')
        branch_timeout=float(tokens[index+1])
        if not 0<branch_timeout<float('inf'):raise ValueError('Timeout must be finite and positive')
        del tokens[index:index+2]
    if '--no-wait' in tokens:
        if getattr(_main_ref,'headless',False):raise ValueError('--no-wait requires a live Kitchen session')
        tokens.remove('--no-wait');no_wait=True;wait=False

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
        return completed(branch_create(folder, name, color=color, cwd=cwd))

    if op in ("switch", "checkout", "set"):
        if len(tokens) < 3:
            return False, "usage: rak branch switch <folder> <branch_name|source>"
        folder = tokens[1]
        name = tokens[2]
        return completed(branch_switch(folder, name, cwd=cwd))

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
        return completed(branch_delete(folder, name, cwd=cwd))

    # Alternate syntax: `rak branch <folder> <op> ...`
    if len(tokens) >= 2:
        sub_op = tokens[1].lower()
        folder = tokens[0]
        if sub_op in ("create", "new", "add") and len(tokens) >= 3:
            name = tokens[2]
            color = tokens[3] if len(tokens) > 3 else "blue"
            return completed(branch_create(folder, name, color=color, cwd=cwd))
        if sub_op in ("switch", "checkout", "set") and len(tokens) >= 3:
            return completed(branch_switch(folder, tokens[2], cwd=cwd))
        if sub_op in ("list", "ls", "show"):
            return branch_list(folder, cwd=cwd)
        if sub_op in ("current", "status", "active"):
            return branch_current(folder, cwd=cwd)
        if sub_op in ("delete", "rm", "remove", "del") and len(tokens) >= 3 and "--yes" in tokens:
            return completed(branch_delete(folder, tokens[2], cwd=cwd))

    return False, f"unknown branch operation: {op!r} (use: create, switch, list, current, delete)"


def execute_command(line, wait=True, timeout=120.0, cwd=None):
    if current_command() is not None:
        return _execute_command(line,wait=wait,timeout=timeout,cwd=cwd)
    context = CommandContext(line)
    with command_scope(context):
        try:ok,message = _execute_command(line,wait=wait,timeout=timeout,cwd=cwd)
        except Exception as error:ok,message = False,failure_from_exception(error)
        response = _command_response(context,{'ok':ok,'msg':message})
    if not response['ok']:
        return False,CommandFailure(response.get('log') or response['msg'],
            application_error=response.get('application_error',False),code=response.get('error_code','command_failed'))
    return True,response['msg']


def _execute_command(line, wait=True, timeout=120.0, cwd=None):

    line = (line or "").strip()
    if not line or line.startswith("#"):
        return True, ""

    parts = line.split(None, 1)
    cmd = parts[0].lower()
    rest = parts[1].strip() if len(parts) > 1 else ""

    if cmd in ("help", "?"):
        return True, _help_text()

    if cmd=='doctor':
        from core.cli_arguments import tokens as argument_tokens
        tokens=argument_tokens(cmd,rest)
        if any(token not in ('--json','--help','-h') for token in tokens):raise ValueError('usage: rak doctor [--json]')
        if '--help' in tokens or '-h' in tokens:return True,'rak doctor [--json]: installed tools, active branches and live-session requirements'
        from core.cli_environment import describe,render
        return True,render(describe(cwd or os.getcwd(),getattr(_main_ref,'session',_main_ref)),'--json' in tokens)

    if cmd=='repack':
        from core.cli_arguments import tokens as argument_tokens
        arguments=argument_tokens(cmd,rest)
        if '-o' in arguments or '--to' in arguments:
            from core.kitchen_repack_cli import parse
            try:options=parse(rest,cwd or os.getcwd())
            except ValueError as error:return False,CommandFailure(str(error))
            ok,message=_ensure_bridge().invoke('_run_repack_output',options)
            if not ok or not wait:return ok,message
            return _wait_dispatched(message,timeout,return_output=True,as_json=options.get('json',False))

    if cmd=='inspect':
        from core.cli_arguments import tokens as argument_tokens
        tokens=argument_tokens(cmd,rest)
        if tokens in (['--help'],['-h']):return True,'rak inspect [-L] PATH [--json]: live format, symlink status, active branch and exact usable operations[].argv'
        paths=[token for token in tokens if token not in ('--json','-L','--dereference')]
        if len(paths)!=1:return False,CommandFailure('inspect: expected one logical path')
        ok,result=_ensure_bridge().invoke('_inspect_path',_cli_path(paths[0],cwd),'-L' in tokens or '--dereference' in tokens)
        if not ok:return ok,result
        if '--json' in tokens:return True,json.dumps(result,ensure_ascii=False)
        link=result.get('symlink');detail=''
        if link:detail=' -> '+link['target']+' [symlink:'+('directory' if link['is_dir'] else 'file' if link['is_file'] else 'unavailable')+'; '+link['status']+']'
        return True,result['kind']+' '+result['path']+detail+'\n'+'\n'.join(item['command']+'  # '+item.get('description','') for item in result['operations'])

    from core.unix_verify import COMMANDS as VERIFY_COMMANDS
    if cmd in VERIFY_COMMANDS:
        from core.cli_arguments import tokens as argument_tokens
        return _execute_fs_read_command(cmd,argument_tokens(cmd,rest),cwd=cwd)

    from core.unix_cli import COMMANDS as UNIX_COMMANDS
    if cmd in UNIX_COMMANDS:
        from core.unix_cli import parse,execute,HELP
        try:options=parse(cmd,rest,cwd or os.getcwd())
        except ValueError as error:return False,CommandFailure(str(error))
        if options.get('help'):return True,HELP
        return execute(_ensure_bridge(),options,wait=wait,timeout=timeout,cwd=cwd or os.getcwd(),on_qt_thread=_on_qt_thread())

    from core.kitchen_cli import NAMESPACES
    if cmd in NAMESPACES:
        from core.kitchen_cli import parse,HELP
        try:options=parse(cmd,rest,cwd or os.getcwd())
        except ValueError as error:return False,CommandFailure(str(error))
        if options.get('help'):return True,HELP
        ok,message=_ensure_bridge().invoke('_run_kitchen',options)
        if ok and options.get('no_wait'):
            result={'state':'dispatched','task_id':getattr(message,'task_id',None),'operation':str(message)}
            return True,json.dumps(result,ensure_ascii=False)
        if not ok or not wait:return ok,message
        return _wait_dispatched(message,options.get('timeout') or timeout,return_output=True,as_json=options.get('json',False))

    if cmd in ('context','capabilities','events','ui','inbox'):
        from core.agent_cli import parse,execute,HELP
        options=parse(cmd,rest,cwd or os.getcwd())
        if options.get('help'):return True,HELP
        return execute(_ensure_bridge(),options,cwd or os.getcwd(),on_qt_thread=_on_qt_thread())

    if cmd in ('activity','log','logs'):
        from core.activity_log import parse_options,HELP
        options=parse_options(rest)
        if options.get('help'):return True,HELP
        if _main_ref is None:return False,CommandFailure('No live Kitchen activity session. Open a project in Kitchen.')
        return _ensure_bridge().invoke('_run_activity',options)

    if cmd in ("fs", "file", "files"):
        return _execute_fs_command(rest, wait=wait, timeout=timeout, cwd=cwd or os.getcwd(),namespace=cmd)

    if cmd in ("ls", "cat", "grep"):
        from core.cli_arguments import tokens as argument_tokens
        return _execute_fs_read_command(cmd,argument_tokens(cmd,rest),cwd=cwd or os.getcwd())

    if cmd in ("branch", "neobranch"):
        return _execute_branch_command(rest, wait=wait, timeout=timeout, cwd=cwd or os.getcwd())

    if cmd in ("list","actions"):
        if rest.strip()!='--all':return True,_help_text()
        a = actions()
        return True, ("safe project-tree transactions (recommended):\n"
                      "  archive|payload|super|apk|br|dat --help (non-dialog Kitchen file tools)\n"
                      "  activity [--json] [--failed] [--limit N]\n"
                      "  activity status|show <id>|clear --yes\n"
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
            return _wait_dispatched(msg,timeout)
        return ok, msg

    if cmd in ("doc", "docs", "agent", "init-agent"):
        from core.rak_agent_docs import update_all_agent_files
        updated = update_all_agent_files(cwd or os.getcwd())
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
        return run_img_cli(tokens, cwd=cwd or os.getcwd(), main_ref=_main_ref)

    if cmd in ("mounted", "mounts"):
        from core.rak_img_cli import cmd_mounted
        return cmd_mounted(main_ref=_main_ref)


    if cmd not in actions()['single']:
        return False,CommandFailure('Unknown command: '+cmd+'. Use rak inspect PATH --json for file operations, or rak list for primary commands.')
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
    from core.cli_contract import help_text
    return help_text()


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
    from PySide6.QtCore import QCoreApplication
    from ui.workspace_cli import headless_workspace
    app=QCoreApplication.instance() or QCoreApplication(sys.argv[:1])
    base=os.getcwd()
    if target_path:
        norm=os.path.abspath(target_path)
        try:
            from core.rak_neobranch_view import project_root
            base=project_root(norm)
        except ValueError:
            base=norm if os.path.isdir(norm) else os.path.dirname(norm)
    if not os.path.isdir(base):base=os.getcwd()
    owner=headless_workspace(base)
    attach(owner)
    return app,owner,owner.tree


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
            if not isinstance(data,dict):raise ValueError('IPC request must be a JSON object')
            if data.get("token") != self.token:
                res = {"ok": False, "msg": "Invalid auth token"}
            else:
                cmd = data.get("cmd", "")
                cwd = data.get("cwd", "")
                res = _handle_ipc_command(cmd, cwd, argv=data.get('argv'))
            resp_bytes = json.dumps(res).encode("utf-8") + b"\n"
            client.sendall(resp_bytes)
        except Exception as e:
            context = CommandContext('IPC request')
            with command_scope(context):
                res = _command_response(context,{'ok':False,'msg':failure_from_exception(e)})
            try:
                err_resp = json.dumps(res).encode("utf-8") + b"\n"
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


def _command_response(context, result):
    if not result.get('ok') and not result.get('project_mismatch') and getattr(result.get('msg'),'code',None) not in ('no_match','different'):
        message = result.get('msg','Command failed')
        reason = str(message).split(' gagal: ',1)[-1]
        if not any(reason in text for text,_ in context.records()):
            from core.error_reporting import report_fatal
            report_fatal('RAK CLI: '+str(context.command)+'\n\n'+str(message),
                application_error=getattr(message,'application_error',False))
    return context.response(result)


def _handle_ipc_command(cmd, cwd="", *, argv=None):
    context = CommandContext(cmd)
    from core.cli_arguments import argument_scope
    with command_scope(context),argument_scope(argv):
        try:result = _dispatch_ipc_command(cmd,cwd)
        except Exception as error:result = {'ok':False,'msg':failure_from_exception(error)}
        return _command_response(context,result)


def _dispatch_ipc_command(cmd, cwd=""):
    cmd = (cmd or "").strip()
    if not cmd:
        return {"ok": True, "msg": ""}

    parts = cmd.split(None, 1)
    action = parts[0].lower()
    rest = parts[1].strip() if len(parts) > 1 else ""

    # Perintah khusus GUI: refresh
    if action == "refresh":
        ok, msg = _ensure_bridge().invoke('_refresh')
        return {"ok": ok, "msg": msg}

    if action in ('activity','log','logs'):
        ok,msg=execute_command(cmd,wait=False,cwd=cwd)
        return {'ok':ok,'msg':msg}

    if action in ('context','capabilities','events','ui','inbox'):
        ok,msg=execute_command(cmd,wait=False,cwd=cwd)
        return {'ok':ok,'msg':msg}

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
                from core.unix_cli import COMMANDS as UNIX_COMMANDS,parse as parse_unix
                from core.unix_verify import COMMANDS as VERIFY_COMMANDS,_parser as verification_parser
                if action in UNIX_COMMANDS:
                    options=parse_unix(action,rest,cwd or os.getcwd())
                    target=options.get('path') or (options.get('paths') or [''])[0]
                elif action in VERIFY_COMMANDS:
                    options=verification_parser(action).parse_args(tokens)
                    target=(options.paths or [''])[0]
                elif action=='inspect':
                    operands=[token for token in tokens if token not in ('--json','-L','--dereference')]
                    target=operands[0] if len(operands)==1 else ''
                elif action in ("fs", "file", "files", "branch", "neobranch", "archive", "payload", "super", "apk", "br", "dat"):
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
        ok, msg = execute_command(cmd,wait=True,cwd=cwd or os.getcwd())
        return {"ok": ok, "msg": msg}

    if action in ("mounted", "mounts"):
        from core.rak_img_cli import cmd_mounted
        ok, msg = cmd_mounted(main_ref=_main_ref)
        return {"ok": ok, "msg": msg}

    # `fs` has two positional paths (or a destructive acknowledgement), so it
    # must be parsed before the legacy one-path IPC normalizer below.
    from core.unix_cli import COMMANDS as UNIX_COMMANDS
    if action in UNIX_COMMANDS or action in ("inspect", "list", "fs", "file", "files", "branch", "neobranch", "archive", "payload", "super", "apk", "br", "dat"):
        ok, msg = execute_command(cmd, wait=True, cwd=cwd or os.getcwd())
        return {"ok": ok, "msg": msg}

    # Each command parser resolves quoted logical paths against the client's cwd.
    ok, msg = execute_command(cmd, wait=True, cwd=cwd or os.getcwd())
    if ok and action in ("unpack", "repack", "batch"):
        try:
            from core.rak_agent_docs import update_all_agent_files
            update_all_agent_files(cwd)
        except Exception:
            pass
    return {"ok": ok, "msg": msg}


def _call_ipc_server(cmd_line, cwd="", *, argv=None):

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
        socket_timeout=600.0
        from core.kitchen_cli import NAMESPACES,parse
        command_parts=cmd_line.split(None,1)
        if command_parts and command_parts[0].lower() in NAMESPACES:
            try:
                options=parse(command_parts[0].lower(),command_parts[1] if len(command_parts)>1 else '',cwd or os.getcwd())
                socket_timeout=max(socket_timeout,(options.get('timeout') or 0)+30)
            except ValueError:pass
        with socket.create_connection(("127.0.0.1", port), timeout=socket_timeout) as s:
            payload = json.dumps({"cmd": cmd_line, "cwd": cwd or os.getcwd(), "token": token}) + "\n"
            if argv is not None:
                payload=json.dumps({'cmd':cmd_line,'cwd':cwd or os.getcwd(),'token':token,'argv':argv})+'\n'
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


def _print_cli_response(response, origin='GUI'):
    ok = response.get('ok',False)
    message = response.get('msg','')
    log = response.get('log','')
    if log and message in log:
        message = log
    elif log:
        message += '\n\n'+log
    print(f"[RAK_CLI:{origin}] {'OK ' if ok else 'ERR'} | {message}",
          file=sys.stdout if ok else sys.stderr)


def _main_cli():

    _force_utf8_stdio()
    args = sys.argv[1:]
    from core.unix_cli import COMMANDS as UNIX_COMMANDS
    from core.unix_verify import COMMANDS as VERIFY_COMMANDS
    from core.cli_arguments import argument_scope
    if not args:
        print(_help_text())
        sys.exit(0)


    cmd_line = (subprocess.list2cmdline(args)
                if args[0].lower() in ("fs", "file", "files", "branch", "neobranch",
                                       "policy", "sepolicy", "neocat_policy",
                                       "automation", "renxec", "inspect", "unpack", "repack", "archive", "payload", "super", "apk", "br", "dat",*UNIX_COMMANDS,*VERIFY_COMMANDS) else " ".join(args))
    first = args[0].lower()
    if first=='doctor':
        response=_call_ipc_server(cmd_line,os.getcwd(),argv=args)
        if response is not None and not response.get('project_mismatch'):
            print(response['msg'] if response.get('ok') else json.dumps({'ok':False,'error':response.get('msg','')},ensure_ascii=False))
            sys.exit(0 if response.get('ok') else 1)
        output=sys.stdout
        try:
            sys.stdout=sys.stderr
            with argument_scope(args):ok,message=execute_command(cmd_line,cwd=os.getcwd())
        finally:sys.stdout=output
        print(message if ok else json.dumps({'ok':False,'error':str(message)},ensure_ascii=False),file=sys.stdout if ok else sys.stderr)
        sys.exit(0 if ok else 1)
    if first == 'binary':
        from core.binary_cli import run
        sys.exit(run(args[1:]))
    if first in ("help", "-h", "--help"):
        print(_help_text())
        sys.exit(0)

    from core.kitchen_cli import NAMESPACES,parse as parse_kitchen,HELP as KITCHEN_HELP
    from core.cli_arguments import argument_scope
    if first in NAMESPACES:
        try:
            with argument_scope(args):kitchen_options=parse_kitchen(first,subprocess.list2cmdline(args[1:]),os.getcwd())
        except ValueError as error:
            if '--json' in args:print(json.dumps({'ok':False,'error':str(error)},ensure_ascii=False))
            else:print(str(error),file=sys.stderr)
            sys.exit(1)
        if kitchen_options.get('help'):print(KITCHEN_HELP);sys.exit(0)
    if first in UNIX_COMMANDS:
        from core.unix_cli import parse as parse_unix,HELP as UNIX_HELP
        try:
            with argument_scope(args):unix_options=parse_unix(first,subprocess.list2cmdline(args[1:]),os.getcwd())
        except ValueError as error:print(str(error),file=sys.stderr);sys.exit(1)
        if unix_options.get('help'):print(UNIX_HELP);sys.exit(0)
    if first=='repack' and ('-o' in args or '--to' in args):
        from core.kitchen_repack_cli import parse as parse_repack
        try:
            with argument_scope(args):repack_options=parse_repack(subprocess.list2cmdline(args[1:]),os.getcwd())
        except ValueError as error:print(str(error),file=sys.stderr);sys.exit(1)

    if first in ('context','capabilities','events','ui','inbox'):
        from core.agent_cli import HELP,parse
        try:options=parse(first,subprocess.list2cmdline(args[1:]),os.getcwd())
        except (ValueError,OSError) as error:
            print(json.dumps({'ok':False,'error':str(error)},ensure_ascii=False));sys.exit(1)
        if options.get('help'):print(HELP);sys.exit(0)
        if first=='events' and options.get('stream'):
            cursor=options['since'];event_session=None
            try:
                while True:
                    command=['events','--since',str(cursor),'--wait','30','--limit',str(options['limit']),'--json']
                    if options['types']:command+=['--types',','.join(options['types'])]
                    response=_call_ipc_server(subprocess.list2cmdline(command),os.getcwd())
                    if not response or not response.get('ok'):
                        print(json.dumps({'ok':False,'error':(response or {}).get('msg','No live Kitchen session')}),flush=True);sys.exit(1)
                    batch=json.loads(response['msg'])
                    if event_session is not None and event_session!=batch['session_id']:
                        print(json.dumps({'type':'events.reset','session_id':batch['session_id']}),flush=True)
                        event_session=batch['session_id'];cursor=0;continue
                    event_session=batch['session_id']
                    if batch['gap']:print(json.dumps({'type':'events.gap','session_id':batch['session_id'],'cursor':cursor}),flush=True)
                    for event in batch['events']:print(json.dumps({'session_id':batch['session_id'],**event},ensure_ascii=False),flush=True)
                    cursor=batch['cursor']
                    if batch['closed']:sys.exit(0)
            except KeyboardInterrupt:sys.exit(0)
        response=_call_ipc_server(subprocess.list2cmdline(args),os.getcwd())
        if response is None and first=='capabilities':
            output=sys.stdout
            try:
                sys.stdout=sys.stderr
                from core.cli_environment import describe
                standalone=describe(os.getcwd())
            finally:sys.stdout=output
            print(json.dumps(standalone,ensure_ascii=False));sys.exit(0)
        if response is None:response={'ok':False,'msg':'No live Kitchen session. Open a project in Kitchen. Use rak doctor --json for standalone tools and branch information.'}
        print(response['msg'] if response.get('ok') else json.dumps({'ok':False,'error':response.get('msg',''),'log':response.get('log','')},ensure_ascii=False))
        sys.exit(0 if response.get('ok') else 1)

    if first in ('activity','log','logs'):
        from core.activity_log import HELP
        if any(value in ('--help','-h','help') for value in args[1:]):print(HELP);sys.exit(0)
        response=_call_ipc_server(subprocess.list2cmdline(args),os.getcwd())
        if response is None:response={'ok':False,'msg':'No live Kitchen activity session. Open a project in Kitchen.'}
        if '--json' in args:
            print(response['msg'] if response.get('ok') else json.dumps({'ok':False,'error':response.get('msg',''),'log':response.get('log','')},ensure_ascii=False))
        else:_print_cli_response(response)
        sys.exit(0 if response.get('ok') else 1)

    if (first in ("ls", "cat", "grep",*VERIFY_COMMANDS) or
            (first in ("fs", "file", "files") and len(args) > 1 and args[1].lower() in ("ls", "list", "cat", "read", "grep",*VERIFY_COMMANDS))):
        read_op = first if first in ("ls", "cat", "grep",*VERIFY_COMMANDS) else args[1].lower()
        read_args = args[1:] if first in ("ls", "cat", "grep",*VERIFY_COMMANDS) else args[2:]
        read_stdout=sys.stdout
        try:
            sys.stdout=sys.stderr
            ok, msg = _execute_fs_read_command(read_op, read_args, cwd=os.getcwd(),standalone_stdin=True)
        finally:sys.stdout=read_stdout
        stream=sys.stdout if ok else sys.stderr
        if msg:
            stream.write(str(msg))
            if read_op in ('ls','list') or not ok and not str(msg).endswith('\n'):stream.write('\n')
        sys.stdout=sys.stderr
        sys.exit(0 if ok else 2 if read_op in ('grep','cmp','diff') and getattr(msg,'code',None) not in ('no_match','different') else 1)

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
    ipc_res = _call_ipc_server(cmd_line, os.getcwd(), argv=args)
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
            if (first in NAMESPACES or first=='inspect' or first=='repack' and ('-o' in args or '--to' in args)) and '--json' in args:
                print(ipc_res['msg'] if ok else json.dumps({'ok':False,'error':ipc_res.get('msg',''),'log':ipc_res.get('log','')},ensure_ascii=False))
            elif first=='readlink':print(msg,file=sys.stdout if ok else sys.stderr)
            else:_print_cli_response(ipc_res)
            sys.exit(0 if ok else 1)

    # 2. GUI tidak sedang berjalan: fallback ke mode headless standalone
    if first == "mount" and len(args) > 1 and not is_attached():
        print(f"[RAK_CLI] Virtual tree mounting is a Kitchen GUI feature. The GUI is not currently running.")
        print(f"          However, you can read and edit this image directly via CLI without GUI:")
        print(f"          - Inspect files:  rak img ls {args[1]}")
        print(f"          - Read file:      rak img cat {args[1]} <internal_path>")
        print(f"          - Edit in-place:  rak img write {args[1]} <internal_path> <local_file>")
        print(f"          - Show usage:     rak img info {args[1]}")
        sys.exit(1)

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
    if first=='inspect':
        inspect_paths=[arg for arg in args[1:] if arg not in ('--json','-L','--dereference')]
        target_path=inspect_paths[0] if len(inspect_paths)==1 else None
    if first in NAMESPACES:target_path=kitchen_options['path']
    if first=='repack' and ('-o' in args or '--to' in args):target_path=repack_options['path']
    if first in UNIX_COMMANDS:target_path=unix_options.get('path') or unix_options['paths'][0]
    if first in ("fs", "file", "files") and len(args) > 2:
        target_path = args[2]
    if first in ("branch", "neobranch"):
        if len(args) > 2 and args[1].lower() in ("create", "new", "add", "switch", "checkout", "set", "delete", "rm", "current", "status"):
            target_path = args[2]
        elif len(args) > 1:
            target_path = args[1]
    headless_app = None
    cli_stdout=sys.stdout
    if first=='readlink' or (first in NAMESPACES or first=='inspect' or first=='repack' and ('-o' in args or '--to' in args)) and '--json' in args:sys.stdout=sys.stderr
    if not is_attached():
        headless_app, headless_owner, _ = _bootstrap_headless(target_path)

    if headless_app is not None:
        # FileTree actions marshal back to Qt's main thread. Keep that event
        # loop pumping while the command waits for queued filesystem tasks.
        result = {}
        def _execute_headless():
            try:
                from core.cli_arguments import argument_scope
                with argument_scope(args):result["value"] = execute_command(cmd_line, wait=True)
            except BaseException as exc:
                result["error"] = exc
        worker = threading.Thread(target=_execute_headless, daemon=True)
        worker.start()
        while worker.is_alive():
            headless_app.processEvents()
            worker.join(0.02)
        headless_app.processEvents()
        try:
            if "error" in result:raise result["error"]
            ok, msg = result["value"]
        finally:
            headless_owner.stop()
            from core.threader import drain_background_work
            drain_background_work(headless_app)
            detach(headless_owner)
    else:
        with argument_scope(args):ok, msg = execute_command(cmd_line, wait=True)
    if ok and first in ("branch", "neobranch"):
        try:
            _call_ipc_server("refresh", os.getcwd())
        except Exception:
            pass
    sys.stdout=cli_stdout
    tag = "OK " if ok else "ERR"
    if first=='readlink':
        print(msg,file=sys.stdout if ok else sys.stderr)
        sys.stdout=sys.stderr
        sys.exit(0 if ok else 1)
    if (first in NAMESPACES or first=='inspect' or first=='repack' and ('-o' in args or '--to' in args)) and '--json' in args:
        print(msg if ok else json.dumps({'ok':False,'error':str(msg)},ensure_ascii=False))
        sys.stdout=sys.stderr  # Keep standalone shutdown diagnostics off JSON stdout.
        sys.exit(0 if ok else 1)
    print(f"[RAK_CLI:STANDALONE] {tag} | {msg}")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    _main_cli()
