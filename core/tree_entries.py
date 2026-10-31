# SPDX-License-Identifier: MPL-2.0
"""Entry data shared by the QWidget reference and native Quick directory model.

The item factory is the only view-specific dependency. The same metadata,
symlink, filesystem badge, branch and APK rules populate both presentations.
"""
import os
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from core import apktool_tool
from core.config_engine_pure import perm_to_rwx
from core.fsdetect import detect_fs_root

SIZE_LABEL_ROLE = Qt.UserRole + 30
IS_FOLDER_ROLE = Qt.UserRole + 98
UI_STABLE_ROLE = Qt.UserRole + 9
APKTOOL_LINK_ROLE = Qt.UserRole + 71


def _is_physical_checkout(tree, path):
    manager = getattr(tree, "_nb_manager", None)
    if manager is None:
        getter = getattr(tree, "_get_nb_manager", None)
        if getter:
            try:
                manager = getter()
            except Exception:
                return False
    check = getattr(manager, "is_physical_checkout", None)
    if not check:
        return False
    try:
        rel = os.path.relpath(os.path.abspath(path), os.path.abspath(tree.base))
    except (OSError, ValueError, TypeError):
        return False
    if rel == "." or rel.startswith(".." + os.sep):
        return False
    return bool(check(rel.split(os.sep, 1)[0]))

def fmt_size(nbytes: int) -> str:

    if nbytes >= 1024 ** 3:
        return f"{nbytes / 1024 ** 3:.2f} GB"
    if nbytes >= 1024 ** 2:
        return f"{nbytes / 1024 ** 2:.1f} MB"
    # < 1 MB → KB (termasuk file kecil; minimal 0.1 KB biar gak "0.0")
    kb = nbytes / 1024
    if kb < 0.1 and nbytes > 0:
        kb = 0.1
    return f"{kb:.1f} KB"


def entry_metadata(tree, full):
    if full.startswith(tree.base):
        rel = full[len(tree.base):].lstrip("\\/").replace("\\", "/")
    else:
        rel = os.path.relpath(full, tree.base).replace("\\", "/")

    from core.boot_image import is_boot_ramdisk_rel
    boot_ramdisk = is_boot_ramdisk_rel(tree.base, rel)
    raw_ctx_key = tree.engine.escaped_ctx_key(rel)

    # config menang kalau keduanya punya slot di titik yang sama
    # neocat hanya isi slot yang kosong di config
    if boot_ramdisk:
        # CPIO has no security.selinux field. A kitchen-side context entry
        # is not an on-device label and must never be displayed as one.
        ctx_val = ""
        ctx_src = ""
    elif raw_ctx_key in tree.ctx:
        ctx_val = tree.ctx[raw_ctx_key]
        ctx_src = "config"
    elif raw_ctx_key in tree.ctx_neocat:
        ctx_val = tree.ctx_neocat[raw_ctx_key]
        ctx_src = "neocat"
    else:
        ctx_val = ""
        ctx_src = ""

    raw_fs_key = rel
    perm_raw = tree.fs.get(raw_fs_key, "")
    if perm_raw == "":
        perm_raw = ""

    perm = perm_to_rwx(perm_raw) if perm_raw else ""

    return os.path.basename(full), ctx_val, perm, ctx_src


def build_tree_entry(tree, entry_tuple, is_root_parent, is_branch_only_dir, _nb_is_deleted, size_labels=None, *, entry_info=None, defer_file_icon=False, item_factory):
    priority, _, full, is_dir_entry, symlink_data, source_full, branch_only_abs = entry_tuple

    # NeoBranch: skip items deleted in active branch
    if _nb_is_deleted and not branch_only_abs and not _is_physical_checkout(tree, source_full):
        try:
            if _nb_is_deleted(source_full):
                return None
        except Exception:
            pass


    if is_dir_entry or (entry_info is None and branch_only_abs and os.path.isdir(branch_only_abs)):
        base_name, ctx, perm, ctx_src = tree.get_meta(source_full)

        if entry_info is not None:
            fs_type = entry_info["fs_type"] or tree._unpack_watcher.get_fs_type(source_full)
        elif is_root_parent:
            rel = os.path.relpath(source_full, tree.base).replace("\\", "/")
            root_name = rel.split("/")[0]
            expected_root_path = os.path.join(tree.base, root_name)
            if os.path.normpath(source_full) == os.path.normpath(expected_root_path):
                fs_type = detect_fs_root(tree.base, root_name)
                if not fs_type and hasattr(tree, "_unpack_watcher"):
                    fs_type = tree._unpack_watcher.get_fs_type(source_full)
            else:
                fs_type = ""
        else:
            fs_type = ""

        dt_target = entry_info["collapse"] if entry_info is not None else tree._dtgen_collapse_target(full)
        if dt_target:
            child = item_factory([base_name, ctx, perm])
            child.setData(0, IS_FOLDER_ROLE, True)
            child.setData(0, Qt.UserRole, dt_target)
            child.setData(0, SIZE_LABEL_ROLE, "")
            child.setChildIndicatorPolicy(item_factory.ShowIndicator)
            child.setIcon(0, tree._resolve_folder_icon(dt_target, ""))
            child.setFlags(child.flags() & ~Qt.ItemIsEditable)
            if entry_info is None:
                tree.add_loading(child)
            return child

        child = item_factory([base_name, ctx, perm])
        child.setData(0, IS_FOLDER_ROLE, True)
        child.setData(0, Qt.UserRole, source_full)
        child.setData(0, SIZE_LABEL_ROLE, "")
        if branch_only_abs or is_branch_only_dir:
            child.setData(0, Qt.UserRole + 20, branch_only_abs or full)

        if source_full in tree.superdetect_cache:
            badge = tree.superdetect_cache.get(source_full)
            if badge in tree.NON_FS_BADGE:
                child.setData(0, UI_STABLE_ROLE, True)

        child.setChildIndicatorPolicy(item_factory.ShowIndicator)
        phys_dir = branch_only_abs if branch_only_abs else (full if not is_branch_only_dir else full)
        child.setIcon(0, tree._resolve_folder_icon(phys_dir, fs_type))

        if fs_type in ("EROFS", "EXT4", "EXT3", "EXT2") or fs_type in tree.NON_FS_BADGE:
            child.setData(0, Qt.UserRole + 1, fs_type)
            if fs_type in tree.NON_FS_BADGE:
                child.setData(0, UI_STABLE_ROLE, True)

        tree._apply_ctx_color(child, ctx_src)
        child.setFlags(child.flags() & ~Qt.ItemIsEditable)
        for col in (1, 2):
            child.setFlags(child.flags() | Qt.ItemIsEditable)

        if entry_info is None:
            tree.add_loading(child)
        return child


    if symlink_data:
        target_path = symlink_data.get("resolved", "")
        is_dir_symlink = symlink_data.get("is_dir", False)
        symlink_exists = symlink_data.get("exists", False)
        base_name, ctx, perm, ctx_src = tree.get_meta(source_full)

        child = item_factory([base_name, ctx, perm])
        child.setData(0, IS_FOLDER_ROLE, is_dir_symlink)
        child.setData(0, Qt.UserRole, source_full)
        child.setData(0, Qt.UserRole + 50, target_path)
        child.setData(0, Qt.UserRole + 51, symlink_exists)
        if branch_only_abs or is_branch_only_dir:
            child.setData(0, Qt.UserRole + 20, branch_only_abs or full)

        if is_dir_symlink:
            child.setData(0, SIZE_LABEL_ROLE, "")
            child.setIcon(0, tree.icon_symlink)
            child.setChildIndicatorPolicy(item_factory.ShowIndicator)
            if entry_info is None:
                tree.add_loading(child)
        else:
            if defer_file_icon:
                child.setIcon(0, tree.icon_file)
                child.setData(0, Qt.UserRole + 120, True)
            else:
                child.setIcon(0, tree.icon_symlink_file)
                child.setData(0, Qt.UserRole + 120, False)
            if size_labels and source_full in size_labels:
                child.setData(0, SIZE_LABEL_ROLE, size_labels[source_full])

        tree._apply_ctx_color(child, ctx_src)
        child.setFlags(child.flags() & ~Qt.ItemIsEditable)
        for col in (1, 2):
            child.setFlags(child.flags() | Qt.ItemIsEditable)
        return child


    base_name, ctx, perm, ctx_src = tree.get_meta(source_full)

    fs_info = ""
    phys = branch_only_abs if branch_only_abs else full
    _low_phys = phys.lower()
    _is_badgeable = (
        _low_phys.endswith(".img") or _low_phys.endswith(".img.br")
        or _low_phys.endswith(".new.dat") or _low_phys.endswith(".new.dat.br")
    )
    _dropped_paths = getattr(tree, "_dropped_badge_paths", None)
    _dropped_badge = bool(_dropped_paths) and os.path.normpath(phys) in _dropped_paths
    if entry_info is not None:
        fs_info = entry_info["badge"]
    elif _low_phys.endswith(".bin") and tree._detect_magic_once(phys) == "PAYLOAD":
        fs_info = "PAYLOAD"
    elif _is_badgeable or _dropped_badge:
        if source_full in tree.superdetect_cache:
            badge = tree.superdetect_cache[source_full]
        elif phys in tree.superdetect_cache:
            badge = tree.superdetect_cache[phys]
        elif _is_badgeable:
            badge = tree.get_image_badge(phys)
        else:
            badge = tree._detect_magic_once(phys)

        if badge in tree.NON_FS_BADGE:
            fs_info = badge
        elif badge in ("BR", "DAT", "DAT.BR"):
            fs_info = badge
        else:
            fs_type = tree._check_img_cache(phys)
            if fs_type:
                fs_info = fs_type

    child = item_factory([base_name, ctx, perm])
    child.setData(0, IS_FOLDER_ROLE, False)
    child.setData(0, Qt.UserRole, source_full)
    if branch_only_abs or is_branch_only_dir:
        child.setData(0, Qt.UserRole + 20, branch_only_abs or full)


    _size_lbl = size_labels.get(source_full) if size_labels else None
    if _size_lbl is None and branch_only_abs and entry_info is None:
        try:
            _size_lbl = fmt_size(os.path.getsize(branch_only_abs))
        except Exception:
            _size_lbl = None
    if _size_lbl is not None:
        child.setData(0, SIZE_LABEL_ROLE, _size_lbl)

    if defer_file_icon:
        child.setIcon(0, tree.icon_file)
        child.setData(0, Qt.UserRole + 120, True)
    else:
        child.setIcon(0, tree._resolve_file_icon(phys))
        tree._schedule_file_thumbnail(child, phys, entry_info=entry_info)
        child.setData(0, Qt.UserRole + 120, False)

    if fs_info in ("EROFS", "EXT4", "EXT3", "EXT2", "PAYLOAD"):
        child.setData(0, Qt.UserRole + 1, fs_info)
    elif fs_info in tree.NON_FS_BADGE or fs_info in ("BR", "DAT", "DAT.BR"):
        child.setData(0, Qt.UserRole + 1, fs_info)
        child.setData(0, UI_STABLE_ROLE, True)

    tree._apply_ctx_color(child, ctx_src)
    child.setFlags(child.flags() & ~Qt.ItemIsEditable)
    for col in (1, 2):
        child.setFlags(child.flags() | Qt.ItemIsEditable)

    if getattr(tree, "_mounts", None):
        tree.reattach_mount_item(child, source_full)
    if child.data(0, Qt.UserRole + 60):
        child.setData(0, Qt.UserRole + 120, False)

    # 🔥 APK sudah di-decompile → tampil kayak folder symlink jika mode "folder",
    # atau tetap file biasa jika user switch ke mode "apk".
    if source_full.lower().endswith(".apk"):
        if entry_info is not None:
            mirror = entry_info["apk_mirror"]
        else:
            mirror = apktool_tool.mirror_dir_for(tree.base, source_full) if apktool_tool.is_decompiled(tree.base, source_full) else None
        if mirror:
            if tree.is_apk_folder_mode(source_full):
                child.setData(0, APKTOOL_LINK_ROLE, mirror)
                child.setIcon(0, tree.icon_apk_linked)
                child.setChildIndicatorPolicy(item_factory.ShowIndicator)
                if entry_info is None:
                    tree.add_loading(child)
                child.setData(0, Qt.UserRole + 120, False)
            else:
                child.setData(0, APKTOOL_LINK_ROLE, None)
                child.setIcon(0, tree.icon_apk)
                child.setChildIndicatorPolicy(item_factory.DontShowIndicator)
                child.setData(0, Qt.UserRole + 120, False)

    return child

