# SPDX-License-Identifier: MPL-2.0
import os
from main_ext.class_NeoBranchManager import NeoBranchManager, NB_STORE_DIR

# Qt.UserRole + 10 -> branch color name string (used by delegate dot)
BRANCH_COLOR_ROLE = 10


class NeoBranchInterceptorMixin:


    # Lazy manager
    def _get_nb_manager(self):
        if not self.base:
            return None
        base_norm = os.path.normpath(self.base)
        if (not hasattr(self, "_nb_manager")
                or self._nb_manager is None
                or self._nb_manager.tree_base != base_norm):
            self._nb_manager = NeoBranchManager(self.base)
        return self._nb_manager

    # fs_root from absolute path
    def _nb_fs_root_for_path(self, abs_path: str):
        if not self.base:
            return None
        try:
            rel = os.path.relpath(abs_path, self.base).replace("\\", "/")
        except ValueError:
            return None
        parts = rel.split("/")
        if not parts:
            return None
        fs_root = parts[0]
        if hasattr(self, "engine") and self.engine:
            if fs_root in self.engine.root_map_ctx:
                return fs_root
        return None

    # Find tree item for fs_root
    def _nb_item_for_fs_root(self, fs_root: str):
        from PySide6.QtCore import Qt
        for i in range(self.topLevelItemCount()):
            top = self.topLevelItem(i)
            if top is None:
                continue
            for j in range(top.childCount()):
                child = top.child(j)
                if child is None:
                    continue
                p = child.data(0, Qt.UserRole) or ""
                if os.path.basename(p) == fs_root:
                    return child
        return None


    def nb_resolve_open(self, abs_path: str) -> str:
        nb = self._get_nb_manager()
        if not nb:
            return abs_path
        fs_root = self._nb_fs_root_for_path(abs_path)
        if not fs_root:
            return abs_path
        active = nb.get_active_branch(fs_root)
        if not active:
            return abs_path
        return nb.get_branch_file_path(fs_root, active["name"], abs_path)


    def nb_record_save(self, original_source_path: str):
        nb = self._get_nb_manager()
        if not nb:
            return
        fs_root = self._nb_fs_root_for_path(original_source_path)
        if not fs_root:
            return
        active = nb.get_active_branch(fs_root)
        if not active:
            return
        try:
            nb.record_edit(fs_root, active["name"], original_source_path)
            print(f"[NeoBranch] recorded save: "
                  f"{os.path.relpath(original_source_path, self.base)}")
        except Exception as e:
            print(f"[NeoBranch] record_save error: {e}")


    def nb_is_deleted(self, abs_path: str) -> bool:
        nb = self._get_nb_manager()
        if not nb:
            return False
        fs_root = self._nb_fs_root_for_path(abs_path)
        if not fs_root:
            return False
        active = nb.get_active_branch(fs_root)
        if not active:
            return False
        try:
            rel = os.path.relpath(abs_path, self.base).replace("\\", "/")
        except ValueError:
            return False
        return nb.is_deleted_in_branch(fs_root, active["name"], rel)

    def nb_deleted_lookup_for_dir(self, dir_abs_path: str):

        nb = self._get_nb_manager()
        if not nb:
            return None
        fs_root = self._nb_fs_root_for_path(dir_abs_path)
        if not fs_root:
            return None
        active = nb.get_active_branch(fs_root)
        if not active:
            return None
        branch_name = active["name"]
        deleted_set = nb.deleted_set_for_branch(fs_root, branch_name)
        if not deleted_set:
            return lambda _src: False

        base = self.base

        def is_deleted(source_abs_path: str) -> bool:
            try:
                rel = os.path.relpath(source_abs_path, base).replace("\\", "/")
            except ValueError:
                return False
            norm = nb._normalize_branch_rel(fs_root, branch_name, rel)
            return nb.is_deleted_in_set(norm, deleted_set)

        return is_deleted

    def nb_record_delete(self, abs_path: str) -> bool:
        nb = self._get_nb_manager()
        if not nb:
            return False
        fs_root = self._nb_fs_root_for_path(abs_path)
        if not fs_root:
            return False
        active = nb.get_active_branch(fs_root)
        if not active:
            return False
        try:
            rel = os.path.relpath(abs_path, self.base).replace("\\", "/")
        except ValueError:
            return False
        nb.record_delete(fs_root, active["name"], rel)
        return True


    def nb_get_live_config_dir(self, fs_root: str) -> str:
        nb = self._get_nb_manager()
        if not nb:
            return os.path.join(self.base, "config")
        return nb.get_live_config_dir(fs_root)

    def nb_resolve_config(self, fs_root: str, config_fname: str):
        nb = self._get_nb_manager()
        if not nb:
            return None
        active = nb.get_active_branch(fs_root)
        if not active:
            return None
        return nb.get_branch_config_path(fs_root, active["name"], config_fname)


    def nb_is_active_for_path(self, abs_path: str) -> bool:
        nb = self._get_nb_manager()
        if not nb:
            return False
        fs_root = self._nb_fs_root_for_path(abs_path)
        if not fs_root:
            return False
        return nb.get_active_branch(fs_root) is not None

    def nb_is_active(self, fs_root: str) -> bool:
        nb = self._get_nb_manager()
        if not nb:
            return False
        return nb.get_active_branch(fs_root) is not None


    def nb_refresh_all_dots(self):

        from PySide6.QtCore import Qt
        import json as _json

        if not self.base:
            return

        # NB_STORE_DIR = invisible Unicode chars (\u2060\u200b\u2060)
        nb_root = os.path.join(self.base, NB_STORE_DIR)
        if not os.path.isdir(nb_root):
            return

        active_map = {}
        for fs_root_name in os.listdir(nb_root):
            fs_root_dir = os.path.join(nb_root, fs_root_name)
            if not os.path.isdir(fs_root_dir):
                continue
            state_path = os.path.join(fs_root_dir, "neobranch_state.json")
            if not os.path.isfile(state_path):
                continue
            try:
                with open(state_path, "r", encoding="utf-8") as _f:
                    state = _json.load(_f)
            except Exception:
                continue
            active_branch = state.get("active_branch")
            if not active_branch:
                active_map[fs_root_name] = None
                continue
            branch_json_path = os.path.join(
                fs_root_dir, f"branch_{active_branch}", "branch.json"
            )
            color = "blue"
            if os.path.isfile(branch_json_path):
                try:
                    with open(branch_json_path, "r", encoding="utf-8") as _f:
                        bdata = _json.load(_f)
                    color = bdata.get("color", "blue")
                except Exception:
                    pass
            active_map[fs_root_name] = {"name": active_branch, "color": color}

        for fs_root_name, active in active_map.items():
            item = self._nb_item_for_fs_root(fs_root_name)
            if item is None:
                continue
            if active:
                item.setData(0, Qt.UserRole + BRANCH_COLOR_ROLE, active.get("color", "blue"))
            else:
                item.setData(0, Qt.UserRole + BRANCH_COLOR_ROLE, None)

        self.viewport().update()
