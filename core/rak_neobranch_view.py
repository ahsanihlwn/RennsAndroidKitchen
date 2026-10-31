# SPDX-License-Identifier: MPL-2.0
import os


def _inside(path, root):
    try:
        path = os.path.normcase(os.path.realpath(path))
        root = os.path.normcase(os.path.realpath(root))
        return os.path.commonpath((path, root)) == root
    except (OSError, ValueError):
        return False


def project_root(path, *, workspace_root=None, cwd=None):

    cwd = os.path.abspath(cwd or os.getcwd())
    path = os.path.abspath(path if os.path.isabs(path) else os.path.join(cwd, path))
    if workspace_root:
        root = os.path.abspath(workspace_root)
        if _inside(path, root):
            return root
        raise ValueError("path is outside the active project")
    candidate = path
    config_root = None
    while True:
        if os.path.isdir(os.path.join(candidate, "neobranch")):
            return candidate
        if config_root is None and os.path.isdir(os.path.join(candidate, "config")):
            config_root = candidate
        parent = os.path.dirname(candidate)
        if parent == candidate:
            break
        candidate = parent
    if config_root:
        return config_root
    raise ValueError("project root not found (expected config/ or neobranch/)")


class ActiveProjectView:


    def __init__(self, root):
        self.root = os.path.abspath(root)
        from main_ext.class_NeoBranchManager import NeoBranchManager
        self.manager = NeoBranchManager(self.root)
        self._branches = {}

    def _logical(self, path):
        full = os.path.abspath(path if os.path.isabs(path) else os.path.join(self.root, path))
        if not _inside(full, self.root):
            raise ValueError("path is outside the project")
        rel = os.path.relpath(full, self.root)
        if rel == "neobranch" or rel.startswith("neobranch" + os.sep):
            raise ValueError("NeoBranch storage is internal; use a logical project path")
        return full, rel

    def _branch(self, fs_root):
        if fs_root not in self._branches:
            active = self.manager.get_active_branch(fs_root)
            name = active.get("name") if active else None
            if name:
                self.manager._validate_branch_name(name)
            self._branches[fs_root] = (
                name,
                self.manager.deleted_set_for_branch(fs_root, name)
                if name and not self._is_physical_checkout(fs_root) else frozenset(),
            )
        return self._branches[fs_root]

    def _is_physical_checkout(self, fs_root):
        """Whether the partition directory itself contains the selected branch."""
        check = getattr(self.manager, "is_physical_checkout", None)
        return bool(check and check(fs_root))

    def active_branches(self):
        roots = set()
        store = os.path.join(self.root, "neobranch")
        if os.path.isdir(store):
            roots.update(
                fs_root for fs_root in os.listdir(store)
                if os.path.isdir(os.path.join(store, fs_root))
            )
        # A physical checkout is visible in the live project tree. Include
        # those roots even when a legacy overlay directory is absent.
        try:
            roots.update(
                name for name in os.listdir(self.root)
                if os.path.isdir(os.path.join(self.root, name))
            )
        except OSError:
            pass
        return {
            fs_root: name
            for fs_root in roots
            for name, _deleted in (self._branch(fs_root),)
            if name
        }

    def resolve(self, path):
        from core.project_links import canonical
        return self._resolve_direct(canonical(self,path,follow_final=False))

    def resolve_following(self,path):
        from core.project_links import canonical
        return self._resolve_direct(canonical(self,path,follow_final=True))

    def canonical(self,path,*,follow_final=True):
        from core.project_links import canonical
        return canonical(self,path,follow_final=follow_final)

    def link_info(self,path):
        from core.project_links import describe
        return describe(self,path)

    def link_chain(self,path):
        """Links traversed by a requested logical path, without storage paths."""
        logical,relative=self._logical(path)
        if relative=='.':return []
        result=[]
        parts=relative.split(os.sep)
        for index in range(len(parts)):
            prefix=os.path.join(self.root,*parts[:index+1])
            link=self.link_info(prefix)
            if link:result.append(link)
        return result

    def _resolve_direct(self, path):

        logical, rel = self._logical(path)
        if rel == ".":
            return self.root, "source"
        fs_root = rel.split(os.sep, 1)[0]
        name, deleted = self._branch(fs_root)
        if not name:
            return (logical, "source") if os.path.lexists(logical) and _inside(logical, self.root) else (None, None)
        if self._is_physical_checkout(fs_root):
            # The checked-out directory is the active branch. Do not apply
            # sparse overlays or tombstones on top of its live bytes.
            return (logical, "branch") if os.path.lexists(logical) and _inside(logical, self.root) else (None, None)
        branch_root = self.manager._branch_dir(fs_root, name)
        overlay = os.path.join(branch_root, rel)
        if os.path.lexists(overlay) and _inside(overlay, branch_root):
            return overlay, "branch"
        # A branch file can replace a source directory. Descendants of that
        # source directory must not leak into the logical view.
        parent = os.path.dirname(overlay)
        while parent != branch_root:
            if os.path.lexists(parent) and not os.path.isdir(parent):
                return None, None
            if os.path.islink(parent) and not _inside(parent,branch_root):return None,None
            parent = os.path.dirname(parent)
        normalized = rel.replace(os.sep, "/")
        if self.manager.is_deleted_in_set(normalized, deleted):
            return None, None
        return (logical, "source") if os.path.lexists(logical) and _inside(logical, self.root) else (None, None)

    def list_dir(self, path, *, _with_physical=False):
        alias,_=self._logical(path)
        logical, rel = self._logical(self.canonical(path))
        physical, _ = self._resolve_direct(logical)
        if not physical or not os.path.isdir(physical):
            raise NotADirectoryError(logical)
        names = set()
        candidates={}
        if rel == ".":
            names.update(os.listdir(self.root))
            names.discard("neobranch")
            nb_store = os.path.join(self.root, "neobranch")
            if os.path.isdir(nb_store):
                for fs_root in os.listdir(nb_store):
                    if not os.path.isdir(os.path.join(nb_store, fs_root)):
                        continue
                    name, _ = self._branch(fs_root)
                    if name and os.path.isdir(self.manager._branch_fs_dir(fs_root, name)):
                        names.add(fs_root)
        else:
            fs_root = rel.split(os.sep, 1)[0]
            name, deleted = self._branch(fs_root)
            if name and self._is_physical_checkout(fs_root):
                if os.path.isdir(logical):
                    with os.scandir(logical) as rows:
                        for row in rows:candidates[os.path.normcase(row.name)]=(row,'branch')
            else:
                source_visible = not (name and self.manager.is_deleted_in_set(rel.replace(os.sep, "/"), deleted))
                if source_visible and os.path.isdir(logical):
                    with os.scandir(logical) as rows:
                        for row in rows:candidates[os.path.normcase(row.name)]=(row,'source')
            if name and not self._is_physical_checkout(fs_root):
                branch_root = self.manager._branch_dir(fs_root, name)
                overlay_dir = os.path.join(branch_root, rel)
                if os.path.isdir(overlay_dir) and _inside(overlay_dir,branch_root):
                    with os.scandir(overlay_dir) as rows:
                        for row in rows:candidates[os.path.normcase(row.name)]=(row,'branch')
            names.update(row.name for row,_origin in candidates.values())
        names=set({os.path.normcase(child):child for child in sorted(names)}.values())
        entries = []
        from core.project_links import raw_link,describe
        for child in sorted(names):
            child_logical = os.path.join(alias, child)
            child_node=os.path.join(logical,child)
            row,origin=candidates.get(os.path.normcase(child),(None,None))
            ordinary=False;row_stat=None
            if row is not None:
                if origin=='source' and name and self.manager.is_deleted_in_set(os.path.join(rel,child).replace(os.sep,'/'),deleted):continue
                # A directory entry's ordinary name cannot escape its already
                # validated parent. Reparse points still take the full check.
                row_stat=row.stat(follow_symlinks=False);attributes=getattr(row_stat,'st_file_attributes',0)
                if row.is_symlink() or attributes&0x400:child_physical,origin=self._resolve_direct(child_node)
                else:child_physical=row.path;ordinary=True
            else:child_physical, origin = self._resolve_direct(child_node)
            if child_physical:
                raw=None if ordinary and (row.is_dir(follow_symlinks=False) or row_stat.st_size>512) else raw_link(self,child_node,resolved=(child_physical,origin))
                link=describe(self,child_logical,node=child_node,link=raw) if raw else None
                entries.append({
                    "name": child,
                    "path": child_logical,
                    "kind": "dir" if (link['is_dir'] if link else row.is_dir(follow_symlinks=False) if ordinary else os.path.isdir(child_physical)) else "file",
                    "origin": origin,
                    **({'_physical':child_physical} if _with_physical else {}),
                    **({'symlink':link} if link else {}),
                })
        return entries

    def walk_files(self, path, *, skip_dirs=()):
        yield from self._walk_files(path, set(), frozenset(skip_dirs))

    def _walk_files(self, path, seen, skip_dirs):
        physical, _ = self.resolve_following(path)
        if not physical:
            raise FileNotFoundError(path)
        if os.path.isfile(physical):
            yield os.path.abspath(path)
            return
        real_dir = os.path.realpath(physical)
        if real_dir in seen:
            return
        seen.add(real_dir)
        for entry in self.list_dir(path):
            if entry["name"] in skip_dirs:
                continue
            if entry["kind"] == "dir":
                yield from self._walk_files(entry["path"], seen, skip_dirs)
            else:
                yield entry["path"]


def view_for_directory(path, project_base=None):

    path = os.path.abspath(path)
    if project_base and _inside(path, project_base):
        root = os.path.abspath(project_base)
    else:
        try:
            root = project_root(path)
        except ValueError:
            root = os.path.dirname(path)
    view = ActiveProjectView(root)
    physical, _ = view.resolve_following(path)
    if not physical or not os.path.isdir(physical):
        raise NotADirectoryError(path)
    return view


def active_branch_for_path(path):

    path = os.path.abspath(path)
    try:
        view = ActiveProjectView(project_root(path))
    except ValueError:
        return ""
    view._logical(path)
    rel = os.path.relpath(path, view.root)
    if rel == ".":
        return ""
    return view._branch(rel.split(os.sep, 1)[0])[0] or ""


def source_image_branch(path):

    path = os.path.abspath(path)
    try:
        view = ActiveProjectView(project_root(path))
    except ValueError:
        return ""
    if os.path.dirname(path) != view.root:
        return ""
    stem, ext = os.path.splitext(os.path.basename(path))
    if ext.lower() not in (".img", ".bin"):
        return ""
    active = view.active_branches()
    return active.get(stem) or (active.get(stem[:-2], "") if stem.endswith(("_a", "_b")) else "")


def scan_directory(view, path):

    result = {}
    stack = [(os.path.abspath(path), "")]
    while stack:
        logical, prefix = stack.pop()
        try:
            entries = view.list_dir(logical)
        except OSError:
            continue
        for entry in entries:
            rel = prefix + entry["name"]
            physical, _ = view.resolve(entry["path"])
            if entry["kind"] == "dir" and not entry.get('symlink'):
                result[rel + "/"] = ("dir", 0)
                if physical and not os.path.islink(physical):
                    stack.append((entry["path"], rel + "/"))
            else:
                try:
                    size = os.stat(physical, follow_symlinks=False).st_size
                except OSError:
                    size = -1
                result[rel] = ("file", size)
    return result


def active_config_file(view, fs_root, suffix):

    branch_name, _ = view._branch(fs_root)
    if branch_name and not view._is_physical_checkout(fs_root):
        branch_file = os.path.join(
            view.manager._branch_config_dir(fs_root, branch_name), fs_root + suffix
        )
        if os.path.isfile(branch_file):
            return branch_file
    return os.path.join(view.root, "config", fs_root + suffix)


def files_differ(path_a, path_b):

    if os.path.samefile(path_a, path_b):
        return False
    with open(path_a, "rb") as file_a, open(path_b, "rb") as file_b:
        while True:
            block_a = file_a.read(1024 * 1024)
            block_b = file_b.read(1024 * 1024)
            if block_a != block_b:
                return True
            if not block_a:
                return False
