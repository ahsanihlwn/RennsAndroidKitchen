# SPDX-License-Identifier: MPL-2.0
import os
import re

from PySide6.QtCore import Qt

from core.ui_lang import t


class _FakeDropEvent:


    def acceptProposedAction(self):
        pass

    def ignore(self):
        pass


class FileTreeClipboardMixin:


    def _clip_owner(self):
        return self.window()

    def clip_get(self):

        main = self._clip_owner()
        clip = getattr(main, "fs_clipboard", None) if main else None
        if not clip or not clip.get("paths"):
            return None

        paths, phys = [], []
        for logical, physical in zip(clip["paths"], clip.get("phys") or clip["paths"]):
            if os.path.exists(physical) or os.path.exists(logical):
                paths.append(logical)
                phys.append(physical)
        if not paths:
            self.clip_clear()
            return None

        return {"mode": clip.get("mode", "copy"), "paths": paths, "phys": phys}

    def clip_clear(self):
        main = self._clip_owner()
        if main:
            main.fs_clipboard = None

    def can_paste(self) -> bool:
        return self.clip_get() is not None


    def _clip_collect(self, items):

        logical, physical = [], []
        for it in items or []:
            if it is None or self.is_mount_node(it):
                continue
            full = it.data(0, Qt.UserRole)
            if not full or not isinstance(full, str):
                continue
            if self.base and os.path.normpath(full) == os.path.normpath(self.base):
                continue
            if self.base:
                rel = os.path.relpath(full, self.base).replace("\\", "/")
                if rel == "config":
                    continue
            branch_abs = it.data(0, Qt.UserRole + 20)
            phys = branch_abs if branch_abs and os.path.exists(branch_abs) else full
            if not (os.path.exists(phys) or os.path.exists(full)):
                continue
            if full not in logical:
                logical.append(full)
                physical.append(phys)
        return logical, physical

    def _clip_set(self, items, mode):
        paths, phys = self._clip_collect(items)
        if not paths:
            return False
        main = self._clip_owner()
        if not main:
            return False
        main.fs_clipboard = {"mode": mode, "paths": paths, "phys": phys}
        try:
            n = len(paths)
            label = t("rc_copy") if mode == "copy" else t("rc_cut")
            main.set_loading_finished(f"{label}: {n} item{'s' if n != 1 else ''}")
        except Exception:
            pass
        return True

    def clip_copy(self, items=None):
        return self._clip_set(items if items is not None else self._clip_target_items(), "copy")

    def clip_cut(self, items=None):
        return self._clip_set(items if items is not None else self._clip_target_items(), "cut")

    def _clip_target_items(self):

        selected = self.selectedItems()
        if selected:
            return selected
        cur = self.currentItem()
        return [cur] if cur else []


    def _paste_target_dir(self, target_item):

        if target_item is None or self.is_mount_node(target_item):
            return None
        full = target_item.data(0, Qt.UserRole)
        if not full or not isinstance(full, str):
            return None
        branch_abs = target_item.data(0, Qt.UserRole + 20)
        phys = branch_abs if branch_abs and os.path.exists(branch_abs) else full
        if os.path.isdir(phys) or os.path.isdir(full):
            return full
        return os.path.dirname(full)

    @staticmethod
    def _is_inside(child, parent):
        try:
            c = os.path.normpath(os.path.abspath(child))
            p = os.path.normpath(os.path.abspath(parent))
        except Exception:
            return False
        return c == p or c.startswith(p + os.sep)

    def _paste_unique_name(self, target_dir, base_name, is_file, taken=None):

        taken = taken or set()
        if is_file:
            stem, ext = os.path.splitext(base_name)
            m = re.match(r'^(.*?)\((\d+)\)$', stem.rstrip())
            clean = m.group(1).rstrip() if m else stem
        else:
            ext = ""
            m = re.match(r'^(.*?)\((\d+)\)$', base_name.rstrip())
            clean = m.group(1).rstrip() if m else base_name

        n = 2
        while True:
            candidate = f"{clean}({n}){ext}"
            logical = os.path.join(target_dir, candidate)
            physical, _, _ = self._nb_resolve_write_path(logical)
            if (candidate.lower() not in taken
                    and not os.path.exists(logical) and not os.path.exists(physical)):
                return candidate
            n += 1

    def _paste_dest_name(self, target_dir, src_logical, src_phys, taken=None):

        taken = taken if taken is not None else set()
        name = os.path.basename(src_logical)
        same_dir = (os.path.normpath(os.path.dirname(src_logical))
                    == os.path.normpath(target_dir))
        if not same_dir:
            return name
        is_file = os.path.isfile(src_phys) or os.path.isfile(src_logical)
        name = self._paste_unique_name(target_dir, name, is_file, taken=taken)
        taken.add(name.lower())
        return name

    def clip_paste(self, target_item=None):

        from main_ext.class_AppDialog import AppDialog

        clip = self.clip_get()
        if not clip:
            return

        if target_item is None:
            target_item = self.currentItem() or self.topLevelItem(0)
        target_dir = self._paste_target_dir(target_item)
        if not target_dir or not self.base:
            return

        # Folder tujuan harus benar-benar ada sebagai folder (logical atau branch)
        target_phys, _, _ = self._nb_resolve_write_path(target_dir)
        if not (os.path.isdir(target_dir) or os.path.isdir(target_phys)):
            return

        # Guard: folder tidak boleh ditempel ke dalam dirinya sendiri / turunannya
        for src in clip["paths"]:
            if os.path.isdir(src) and self._is_inside(target_dir, src):
                AppDialog.warning(self.window(), t("rc_paste"), t("rc_paste_into_self"))
                return

        main = self.window()
        expanded = self.save_expanded_paths()
        self.drop_conflict_policy = None

        if clip["mode"] == "cut":
            # Sumber yang sudah ada di folder tujuan = tidak ada yang perlu dipindah.
            paths, phys = [], []
            for logical, physical in zip(clip["paths"], clip["phys"]):
                if os.path.normpath(os.path.dirname(logical)) == os.path.normpath(target_dir):
                    continue
                paths.append(logical)
                phys.append(physical)
            # Clipboard cut habis begitu ditempel (sekali pakai, seperti Explorer)
            self.clip_clear()
            if not paths:
                return
            self._drop_multi_internal(
                paths, target_dir, target_item, expanded,
                _FakeDropEvent(), main, phys,
            )
            return

        self._paste_copy_internal(clip["paths"], clip["phys"], target_dir, expanded, main)

    def _paste_copy_internal(self, src_paths, src_phys_paths, target_path, expanded, main):

        from core.task_queue import TaskItem
        from main_ext.class_ConfigEngine import load_file_context, load_fs_config

        dst_tree = self
        dst_base = self.base

        # src_tree dideteksi dari prefix path — persis cara _drop_multi_internal
        src_tree = None
        if main and src_paths:
            first = src_paths[0]
            if hasattr(main, "tree") and main.tree.base and first.startswith(main.tree.base):
                src_tree = main.tree
            elif hasattr(main, "tree2") and main.tree2.base and first.startswith(main.tree2.base):
                src_tree = main.tree2
        if not src_tree:
            src_tree = dst_tree
        src_base = src_tree.base
        is_cross = src_tree is not dst_tree

        def _branch_state(tree, base, logical_path):
            try:
                rel = os.path.relpath(logical_path, base).replace("\\", "/").strip("/")
            except Exception:
                return "", "", None, None
            if not rel or rel == ".":
                return rel, "", None, None
            root = rel.split("/")[0]
            if not root or not tree.nb_is_active(root):
                return rel, root, None, None
            nb = tree._get_nb_manager()
            active = nb.get_active_branch(root) if nb else None
            return rel, root, nb, active

        # Nama tujuan dihitung di main thread. Bentrok (termasuk paste ke folder
        # asal, yang otomatis bentrok dengan dirinya sendiri) selalu jadi "(N)".
        jobs, taken = [], set()
        for logical, physical in zip(src_paths, src_phys_paths):
            jobs.append((logical, physical,
                         self._paste_dest_name(target_path, logical, physical,
                                               taken=taken)))

        total = len(jobs)
        done = [0]
        failed = [0]


        batch_id = None
        if main and hasattr(main, "_ensure_loading_manager"):
            main._ensure_loading_manager()
            batch_id = main.loading_manager.start_batch(
                "pasting", total, label="Pasting", region="light"
            )

        def _make_worker(src, src_phys, dst_name):
            def worker(s=src, sp=src_phys, n=dst_name):
                try:
                    dst_logical = os.path.join(target_path, n)
                    dst_phys, dst_nb, dst_active = dst_tree._nb_resolve_write_path(dst_logical)

                    rel_src, old_root, src_nb, src_active = _branch_state(src_tree, src_base, s)
                    rel_dst, new_root, _, _ = _branch_state(dst_tree, dst_base, dst_logical)
                    if not rel_src or not rel_dst:
                        return False, "Invalid paste path"

                    # Copy isi (source + overlay branch aktif) — helper yang sama
                    # dipakai move multi, jadi perilaku overlay-nya identik.
                    dst_tree._nb_copy_branch_aware_source(
                        s, sp, dst_phys, rel_src, old_root, src_nb, src_active,
                    )

                    src_cfg_dir = (
                        src_nb._branch_config_dir(old_root, src_active["name"])
                        if (src_nb and src_active)
                        else os.path.join(src_base, "config")
                    )
                    dst_cfg_dir = (
                        dst_nb._branch_config_dir(new_root, dst_active["name"])
                        if (dst_nb and dst_active)
                        else os.path.join(dst_base, "config")
                    )


                    if dst_nb and dst_active:
                        dst_tree._nb_append_remapped_entries(
                            src_cfg_dir, dst_cfg_dir, old_root, new_root, rel_src, rel_dst,
                        )
                        if os.path.exists(dst_logical):
                            dst_nb.record_delete(new_root, dst_active["name"], rel_dst,
                                                 prune_overlay=False)
                        dst_tree._nb_record_output_tree(dst_logical, dst_phys)
                    elif dst_tree.is_inside_fs_root(rel_dst):
                        dst_tree._nb_append_remapped_entries(
                            src_cfg_dir, dst_cfg_dir, old_root, new_root, rel_src, rel_dst,
                        )
                        # Isi yang belum kebagian entri (mis. file yang lahir dari
                        # overlay) dilengkapi default oleh engine.
                        dst_tree.engine.handle_fs_transfer(
                            old_rel=None, new_rel=rel_dst,
                            old_base=None, new_base=dst_base,
                        )
                    return True, ""
                except RuntimeError as e:
                    # dibatalkan / di-skip user di dialog konflik
                    return False, str(e)
                except Exception as e:
                    return False, str(e)
            return worker

        def _make_callback(task_id, dst_name):
            def cb(ok, msg, tid=task_id):
                if main:
                    if batch_id is not None:
                        main.loading_manager.report_done(batch_id, bool(ok), member_name=dst_name)
                    else:
                        main.pop_task(tid)
                if ok:
                    done[0] += 1
                else:
                    failed[0] += 1
                    print(f"[PASTE] failed: {msg}")

                try:
                    dst_tree.reload_config_cache()
                except Exception:
                    dst_tree.ctx = load_file_context(dst_base)
                    dst_tree.fs = load_fs_config(dst_base)
                if getattr(dst_tree, "engine", None):
                    dst_tree.engine.ctx = dst_tree.ctx
                    dst_tree.engine.fs = dst_tree.fs
                    dst_tree.engine.rebuild_root_map()

                p = dst_tree.find_item_by_path(dst_tree.topLevelItem(0), target_path)
                if p:
                    dst_tree.refresh_folder(p)

                if done[0] + failed[0] >= total:
                    dst_tree.resume_size_probe()
                    if is_cross:
                        src_tree.resume_size_probe()
                    dst_tree.restore_expanded_paths(expanded)
                    dst_tree.drop_conflict_policy = None
                    if main:
                        if failed[0] == 0:
                            main.set_loading_finished(
                                f"Pasted {done[0]} item{'s' if done[0] != 1 else ''}")
                        else:
                            main.set_loading_failed(
                                f"{failed[0]} item(s) failed to paste")
            return cb

        if total == 0:
            return

        dst_tree.pause_size_probe()
        if is_cross:
            src_tree.pause_size_probe()

        for logical, physical, dst_name in jobs:
            task_id = f"pasting:{os.path.join(target_path, dst_name)}"
            if main:
                main.task_queue.enqueue(TaskItem(
                    task_id=task_id,
                    worker=_make_worker(logical, physical, dst_name),
                    callback=_make_callback(task_id, dst_name),
                ))
