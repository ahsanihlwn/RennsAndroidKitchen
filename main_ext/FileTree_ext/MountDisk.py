# SPDX-License-Identifier: MPL-2.0
import os
import re
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QTreeWidgetItem

from core.threader import run_in_thread


# Role: tuple (img_path, internal_path) untuk tiap node di dalam mount.
# internal_path "" = root mount (item image itu sendiri).
MOUNT_ROLE = Qt.UserRole + 60


def _build_child_index(entries):

    index = {}
    for e in entries:
        p = e["path"].strip("/")
        if not p:
            continue
        if "/" in p:
            parent, name = p.rsplit("/", 1)
        else:
            parent, name = "", p
        child = dict(e)
        child["name"] = name
        index.setdefault(parent, []).append(child)
    # urutkan tiap level: folder dulu, lalu nama (case-insensitive)
    for lst in index.values():
        lst.sort(key=lambda c: (not c["is_dir"], c["name"].lower()))
    return index


class MountDiskMixin:
    # helpers
    def _ensure_mounts(self):
        if not hasattr(self, "_mounts"):
            self._mounts = {}
        return self._mounts

    def _ensure_mounts_meta(self):
        if not hasattr(self, "_mounts_meta"):
            self._mounts_meta = {}
        return self._mounts_meta

    @staticmethod
    def is_mount_node(item):

        return item is not None and item.data(0, MOUNT_ROLE) is not None

    @staticmethod
    def mount_ref(item):

        return item.data(0, MOUNT_ROLE) if item is not None else None

    def is_mounted(self, img_path):
        return os.path.normpath(img_path) in self._ensure_mounts()

    def mount_writable(self, img_path):

        m = self._ensure_mounts_meta().get(os.path.normpath(img_path))
        return bool(m and m.get("writable"))

    def patch_mount_entry(self, img_path, internal, **fields):

        key = os.path.normpath(img_path)
        index = self._ensure_mounts().get(key)
        if not index:
            return
        internal = internal.strip("/").replace("\\", "/")
        parent = internal.rsplit("/", 1)[0] if "/" in internal else ""
        name = internal.rsplit("/", 1)[-1]
        for child in index.get(parent, []):
            if child["name"] == name:
                child.update(fields)
                break

    # rebuild tree
    def prune_dead_mounts(self, in_dir=None):

        mounts = self._ensure_mounts()
        if not mounts:
            return
        base = os.path.normpath(in_dir) if in_dir else None
        for key in list(mounts.keys()):
            if base and os.path.normpath(os.path.dirname(key)) != base:
                continue
            if os.path.isfile(key):
                continue
            mounts.pop(key, None)
            self._ensure_mounts_meta().pop(key, None)

    def reattach_mount_item(self, item, img_path):

        if item is None or not img_path or not self.is_mounted(img_path):
            return False
        item.setData(0, MOUNT_ROLE, (img_path, ""))
        # sama kayak mount_image: simpan status editable lama, lalu matikan
        # supaya double-click/F2 gak buka rename di root mount.
        item.setData(0, Qt.UserRole + 61, bool(item.flags() & Qt.ItemIsEditable))
        item.setFlags(item.flags() & ~Qt.ItemIsEditable)
        item.setChildIndicatorPolicy(QTreeWidgetItem.ShowIndicator)
        item.takeChildren()
        self._populate_mount_children(item, img_path, "")
        try:
            self._refresh_mount_badge(item)
        except Exception:
            pass
        return True

    # mount
    def mount_image(self, item):

        from core import imgfs

        img_path = item.data(0, Qt.UserRole)
        if not img_path or not os.path.isfile(img_path):
            return
        key = os.path.normpath(img_path)
        mounts = self._ensure_mounts()
        if key in mounts:
            return  # sudah ke-mount

        # tampilkan loading sementara baca image di background
        item.takeChildren()
        self.add_loading(item)
        item.setChildIndicatorPolicy(QTreeWidgetItem.ShowIndicator)
        item.setExpanded(False)

        def work():
            # Worker mengharap return (ok, payload)
            entries = imgfs.list_image(img_path)
            fs = imgfs.detect_fs(img_path)
            sparse = imgfs._is_sparse_file(img_path)
            # EXT & EROFS sama-sama bisa ditulis in-place; sparse selalu
            # read-only (butuh unsparse dulu).
            writable = (fs in ("EXT", "EROFS")) and not sparse
            meta = {"fs": fs, "sparse": sparse, "writable": writable}
            return True, (_build_child_index(entries), meta)

        def done(payload):
            try:
                index, meta = payload
                mounts[key] = index
                self._ensure_mounts_meta()[key] = meta
                target_item = item
                try:
                    _ = target_item.flags()
                except (RuntimeError, Exception):
                    target_item = self.find_item_by_path(self.topLevelItem(0), img_path)

                if target_item:
                    # tandai item image sbg root mount (internal "")
                    target_item.setData(0, MOUNT_ROLE, (img_path, ""))


                    target_item.setData(0, Qt.UserRole + 61, bool(target_item.flags() & Qt.ItemIsEditable))
                    target_item.setFlags(target_item.flags() & ~Qt.ItemIsEditable)
                    if self.display_mode == "single_dir":


                        target_item.takeChildren()
                        target_item.setChildIndicatorPolicy(QTreeWidgetItem.ShowIndicator)
                    else:
                        self._populate_mount_children(target_item, img_path, "")
                        target_item.setExpanded(True)
                    try:
                        self._refresh_mount_badge(target_item)
                    except Exception:
                        pass
            except Exception as e:
                print(f"[MountDisk] done callback error: {e}")

        def fail(err):
            try:
                target_item = item
                try:
                    _ = target_item.flags()
                except (RuntimeError, Exception):
                    target_item = self.find_item_by_path(self.topLevelItem(0), img_path)

                if target_item:
                    target_item.takeChildren()
                    msg = QTreeWidgetItem([f"Mount gagal: {err}", "", ""])
                    f = msg.font(0); f.setItalic(True); msg.setFont(0, f)
                    msg.setForeground(0, QColor(200, 120, 120))
                    target_item.addChild(msg)
            except Exception as e:
                print(f"[MountDisk] fail callback error: {e}")

        run_in_thread(work, None,
                      callback=lambda ok, payload: done(payload) if ok else fail(payload))

    def unmount_image(self, item):

        img_path = None
        ref = self.mount_ref(item)
        if ref:
            img_path = ref[0]
        else:
            img_path = item.data(0, Qt.UserRole)
        if not img_path:
            return
        key = os.path.normpath(img_path)
        self._ensure_mounts().pop(key, None)
        self._ensure_mounts_meta().pop(key, None)

        item.takeChildren()
        item.setData(0, MOUNT_ROLE, None)
        # balikin editable kolom 0 kalau sebelum mount memang editable
        if item.data(0, Qt.UserRole + 61):
            item.setFlags(item.flags() | Qt.ItemIsEditable)
        item.setData(0, Qt.UserRole + 61, None)
        # balikin ikon disk normal (dari 'mounted')
        ic = getattr(self, "icon_drive", None)
        if ic:
            item.setIcon(0, ic)
        # file biasa: tidak ada child indicator
        item.setChildIndicatorPolicy(QTreeWidgetItem.DontShowIndicatorWhenChildless)
        item.setExpanded(False)

    # expand
    @staticmethod
    def _lookup_in_mount_index(index, subpath):

        sub = subpath.strip("/").replace("\\", "/")
        if not sub:
            return True, True, ""
        if sub in index:
            return True, True, sub
        parent = sub.rsplit("/", 1)[0] if "/" in sub else ""
        name = sub.rsplit("/", 1)[-1]
        for c in index.get(parent, []):
            if c["name"].lower() == name.lower():
                actual_sub = (parent + "/" + c["name"]).strip("/") if parent else c["name"]
                return True, c["is_dir"], actual_sub
        return False, False, ""

    @staticmethod
    def _match_mount_partition(img_key, partition_name):

        base = os.path.basename(img_key).lower()
        p_lower = partition_name.lower()
        stem = os.path.splitext(base)[0]
        while "." in stem:
            stem = os.path.splitext(stem)[0]
        stem_clean = re.sub(r'(_slot)?[_-][ab]$', '', stem)
        if stem_clean == p_lower:
            return True


        if base.startswith(p_lower + "."):
            return True
        if base.startswith(p_lower + "-"):
            return True
        norm_p = p_lower.replace("_", "").replace("-", "")
        norm_stem = stem_clean.replace("_", "").replace("-", "")
        if norm_p == norm_stem:
            return True
        if p_lower == "product" and stem_clean in ("my_product", "mi_ext"):
            return True
        if p_lower == "system_ext" and stem_clean in ("mi_ext",):
            return True
        return False

    @classmethod
    def _match_score(cls, img_key, partition_name):

        if not cls._match_mount_partition(img_key, partition_name):
            return -1
        base = os.path.basename(img_key).lower()
        stem = os.path.splitext(base)[0]
        while "." in stem:
            stem = os.path.splitext(stem)[0]
        stem_clean = re.sub(r'(_slot)?[_-][ab]$', '', stem)
        p_lower = partition_name.lower()
        if stem_clean == p_lower:
            return 2
        if stem_clean.replace("_", "").replace("-", "") == \
                p_lower.replace("_", "").replace("-", ""):
            return 1
        return 0

    def _best_mount_for_partition(self, partition_name, exclude_key=None):

        best, best_score = None, -1
        for m_key in self._ensure_mounts():
            if exclude_key and os.path.normpath(m_key) == exclude_key:
                continue
            score = self._match_score(m_key, partition_name)
            if score > best_score:
                best, best_score = m_key, score
        return best if best_score >= 0 else None

    def _resolve_symlink_target_for_mount(self, img_path, internal, target):
        key = os.path.normpath(img_path)
        norm_target = target.replace("\\", "/").strip()

        # 1. Normalize clean target path
        if norm_target.startswith("/"):
            clean = norm_target.lstrip("/")
        else:
            parent = internal.rsplit("/", 1)[0] if "/" in internal else ""
            parts = parent.split("/") if parent else []
            for p in norm_target.split("/"):
                if p in ("", "."):
                    continue
                elif p == "..":
                    if parts:
                        parts.pop()
                else:
                    parts.append(p)
            clean = "/".join(parts)

        clean_norm = clean.strip("/")

        # Generate candidate subpaths in current mount
        current_cand_subs = [clean_norm]
        if clean_norm.startswith("system/"):
            current_cand_subs.append(clean_norm[7:])
        else:
            current_cand_subs.append("system/" + clean_norm)


        curr_index = self._ensure_mounts().get(key, {})
        stub_hit = None
        for cand in current_cand_subs:
            if cand == internal.strip("/"):
                continue  # Don't resolve to self
            found, is_d, actual = self._lookup_in_mount_index(curr_index, cand)
            if not found:
                continue
            if is_d and not curr_index.get(actual.strip("/")):
                if stub_hit is None:
                    stub_hit = (actual, is_d)
                continue
            return (key, actual), None, is_d, True


        KNOWN_PARTITIONS = [
            "system_ext", "system_dlkm", "vendor_dlkm", "odm_dlkm",
            "my_product", "my_stock", "my_heytap", "my_carrier", "my_region",
            "my_engineering", "my_preload", "mi_ext", "system", "product",
            "vendor", "odm", "oem", "cust", "preload", "apex", "data"
        ]

        split_candidates = []

        # A. If target starts with system/ (e.g. system/product/app/YouTube or system/product)
        if clean_norm.startswith("system/"):
            sub_after_system = clean_norm[7:].strip("/")
            for p in KNOWN_PARTITIONS:
                if p == "system":
                    continue
                if sub_after_system == p:
                    split_candidates.append((p, ["", p]))
                elif sub_after_system.startswith(p + "/"):
                    sub_p = sub_after_system[len(p) + 1:]
                    split_candidates.append((p, [sub_p, p + "/" + sub_p]))

        # B. If target starts with vendor/ (e.g. vendor/odm/etc)
        if clean_norm.startswith("vendor/"):
            sub_after_vendor = clean_norm[7:].strip("/")
            for p in KNOWN_PARTITIONS:
                if p == "vendor":
                    continue
                if sub_after_vendor == p:
                    split_candidates.append((p, ["", p]))
                elif sub_after_vendor.startswith(p + "/"):
                    sub_p = sub_after_vendor[len(p) + 1:]
                    split_candidates.append((p, [sub_p, p + "/" + sub_p]))

        # C. Direct known partition match
        for p in KNOWN_PARTITIONS:
            if clean_norm == p:
                split_candidates.append((p, ["", p]))
            elif clean_norm.startswith(p + "/"):
                sub_p = clean_norm[len(p) + 1:]
                split_candidates.append((p, [sub_p, p + "/" + sub_p]))

        # D. First segment as partition name fallback
        if "/" in clean_norm:
            first, rest = clean_norm.split("/", 1)
            split_candidates.append((first, [rest, clean_norm]))
        elif clean_norm:
            split_candidates.append((clean_norm, ["", clean_norm]))

        # Android root dirs pointing to system
        if any(clean_norm.startswith(pre) for pre in ("bin/", "etc/", "framework/", "lib/", "lib64/", "xbin/", "priv-app/", "app/", "media/", "usr/", "fonts/")):
            split_candidates.append(("system", [clean_norm, "system/" + clean_norm]))

        all_mounts = self._ensure_mounts()
        for target_partition, subpath_cands in split_candidates:


            ranked = sorted(
                all_mounts.items(),
                key=lambda kv: self._match_score(kv[0], target_partition),
                reverse=True)
            for m_key, m_index in ranked:
                if m_key == key and target_partition != "system":
                    continue  # Skip current mount unless checking system
                if self._match_score(m_key, target_partition) < 0:
                    continue

                # Found matching image! Now check candidate subpaths inside it
                for sub in subpath_cands:
                    if not sub:
                        return (m_key, ""), None, True, True
                    found, is_d, actual = self._lookup_in_mount_index(m_index, sub)
                    if found:
                        return (m_key, actual), None, is_d, True

                # If pointing to partition root:
                if "" in subpath_cands or not subpath_cands[0]:
                    return (m_key, ""), None, True, True


        if stub_hit is not None:
            actual, is_d = stub_hit
            return (key, actual), None, is_d, True


        base = getattr(self, "base", None)
        if base:
            project_root = base
            base_dir = os.path.dirname(os.path.join(base, internal))
            disk_candidates = []

            if norm_target.startswith("/"):
                if clean_norm.startswith("system/"):
                    disk_candidates.append(os.path.normpath(os.path.join(project_root, clean_norm)))
                    sub = clean_norm[7:]
                    if sub:
                        disk_candidates.append(os.path.normpath(os.path.join(project_root, sub)))
                    disk_candidates.append(os.path.normpath(os.path.join(project_root, "system", clean_norm)))
                else:
                    disk_candidates.append(os.path.normpath(os.path.join(project_root, clean_norm)))
                    disk_candidates.append(os.path.normpath(os.path.join(project_root, "system", clean_norm)))
                    parent = os.path.dirname(project_root)
                    if parent and parent != project_root:
                        disk_candidates.append(os.path.normpath(os.path.join(parent, clean_norm)))
            else:
                disk_candidates.append(os.path.normpath(os.path.join(base_dir, norm_target)))
                disk_candidates.append(os.path.normpath(os.path.join(project_root, norm_target)))
                disk_candidates.append(os.path.normpath(os.path.join(project_root, "system", norm_target)))

            for c in disk_candidates:
                if os.path.exists(c):
                    return None, c, os.path.isdir(c), True

            fallback = disk_candidates[0] if disk_candidates else os.path.normpath(os.path.join(project_root, clean_norm))
        else:
            fallback = clean_norm


        clean_t = norm_target.rstrip("/")
        last_part = clean_t.split("/")[-1].lower() if clean_t else ""
        name_clean = internal.rsplit("/", 1)[-1].lower()

        if norm_target.endswith("/"):
            is_dir_fallback = True
        elif "." in last_part or "." in name_clean:
            is_dir_fallback = False
        elif any(p in norm_target.lower() for p in ("/bin/", "/xbin/", "/sbin/")):
            is_dir_fallback = False
        else:
            is_dir_fallback = True

        return None, fallback, is_dir_fallback, False

    def expand_mount_node(self, item):

        ref = self.mount_ref(item)
        if not ref:
            return False
        img_path, internal = ref
        # sudah terisi (bukan placeholder)?
        if item.childCount() > 0 and item.child(0).text(0) != "Empty":
            return True

        symlink_virtual = item.data(0, Qt.UserRole + 50)
        raw_target = item.data(0, Qt.UserRole + 53)
        orig_ref = item.data(0, Qt.UserRole + 54)

        # Dynamic re-resolution at expand time
        if raw_target and orig_ref:
            orig_img, orig_internal = orig_ref
            s_virt, s_phys, is_d, exists = self._resolve_symlink_target_for_mount(orig_img, orig_internal, raw_target)
            if s_virt:
                target_img_path, target_internal = s_virt
                item.setData(0, MOUNT_ROLE, (target_img_path, target_internal))
                item.setData(0, Qt.UserRole + 50, s_virt)
                item.setData(0, Qt.UserRole + 51, exists)
                item.setData(0, Qt.UserRole + 52, False)
            elif isinstance(symlink_virtual, (list, tuple)):
                target_img_path, target_internal = symlink_virtual
            elif isinstance(symlink_virtual, str):
                target_img_path = img_path
                target_internal = symlink_virtual
            else:
                target_img_path = img_path
                target_internal = internal
        elif isinstance(symlink_virtual, (list, tuple)):
            target_img_path, target_internal = symlink_virtual
        elif isinstance(symlink_virtual, str):
            target_img_path = img_path
            target_internal = symlink_virtual
        else:
            target_img_path = img_path
            target_internal = internal

        item.takeChildren()
        self._populate_mount_children(item, target_img_path, target_internal)
        return True

    def _mountpoint_redirect(self, key, internal):

        name = internal.strip("/")
        if not name or "/" in name:
            return None                     # cuma titik mount tingkat atas
        best = self._best_mount_for_partition(name, exclude_key=key)
        return (best, "") if best else None

    def _populate_mount_children(self, parent_item, img_path, internal):
        key = os.path.normpath(img_path)
        index = self._ensure_mounts().get(key, {})
        children = index.get(internal.strip("/"), [])

        if not children:
            redirect = self._mountpoint_redirect(key, internal)
            if redirect:
                img_path, internal = redirect
                key = os.path.normpath(img_path)
                index = self._ensure_mounts().get(key, {})
                children = index.get(internal.strip("/"), [])

        writable = self.mount_writable(img_path)   # EXT/EROFS non-sparse -> kolom 1&2 editable


        bucket_folders = []
        bucket_files = []

        for c in children:
            name = c["name"]
            child_internal = (internal.strip("/") + "/" + name).strip("/")

            is_dir = c["is_dir"]
            is_link = c["is_link"]
            target = c["target"]

            symlink_virtual = None
            symlink_phys = None
            symlink_exists = False
            is_dir_symlink = False

            if is_link and target:
                symlink_virtual, symlink_phys, is_dir_symlink, symlink_exists = self._resolve_symlink_target_for_mount(img_path, child_internal, target)

            item_data = (c, child_internal, symlink_virtual, symlink_phys, is_dir_symlink, symlink_exists)

            is_root_level = not internal.strip("/")
            if is_dir and name.lower() == "config" and is_root_level:
                priority = -1
                bucket_folders.append((priority, name.lower(), item_data))
            elif is_dir or is_dir_symlink:
                priority = 0
                bucket_folders.append((priority, name.lower(), item_data))
            else:
                priority = 1
                bucket_files.append((priority, name.lower(), item_data))

        bucket_folders.sort(key=lambda x: (x[0], x[1]))
        bucket_files.sort(key=lambda x: x[1])

        sorted_items = bucket_folders + bucket_files

        for _, _, item_data in sorted_items:
            c, child_internal, symlink_virtual, symlink_phys, is_dir_symlink, symlink_exists = item_data

            name = c["name"]
            label = name

            # Resolving linker metadata
            source_full = os.path.normpath(os.path.join(getattr(self, "base", ""), child_internal))
            if hasattr(self, "get_meta"):
                _, ctx, perm, ctx_src = self.get_meta(source_full)
                final_ctx = ctx if ctx else c["selinux"]
                final_perm = perm if perm else c["perm"]
            else:
                final_ctx = c["selinux"]
                final_perm = c["perm"]
                ctx_src = ""

            is_dir = c["is_dir"]
            is_link = c["is_link"]
            target = c["target"]

            it = QTreeWidgetItem([label, final_ctx, final_perm])
            it.setData(0, MOUNT_ROLE, (img_path, child_internal))
            # UserRole: path virtual (BUKAN path disk) supaya tidak disangka file nyata
            it.setData(0, Qt.UserRole, f"mount://{key}//{child_internal}")

            if is_link and target:
                it.setData(0, Qt.UserRole + 50, symlink_virtual or symlink_phys)
                it.setData(0, Qt.UserRole + 51, symlink_exists)
                it.setData(0, Qt.UserRole + 52, bool(symlink_phys))
                it.setData(0, Qt.UserRole + 53, target)
                it.setData(0, Qt.UserRole + 54, (img_path, child_internal))
                if isinstance(symlink_virtual, (list, tuple)):
                    it.setData(0, MOUNT_ROLE, (symlink_virtual[0], symlink_virtual[1]))
                elif isinstance(symlink_virtual, str):
                    it.setData(0, MOUNT_ROLE, (img_path, symlink_virtual))
                if target:
                    it.setToolTip(0, f"Symlink -> {target}")

            # kolom 0 (Name) tidak pernah editable di mount (no rename).
            # kolom 1 (SELinux) & 2 (Permission) editable HANYA kalau writable.
            it.setFlags(it.flags() & ~Qt.ItemIsEditable)
            if writable:
                it.setFlags(it.flags() | Qt.ItemIsEditable)
            self._style_mount_item(it, c, is_link, is_dir, is_dir_symlink, img_path, child_internal)

            if hasattr(self, "_apply_ctx_color"):
                self._apply_ctx_color(it, ctx_src)

            if is_dir or is_dir_symlink:


                it.setData(0, Qt.UserRole + 30, "")
                it.setChildIndicatorPolicy(QTreeWidgetItem.ShowIndicator)
                self.add_loading(it)
            parent_item.addChild(it)

        # Refresh the expansion indicator after loading.
        # Folder kosong tetap nunjukin "Empty" supaya panah tidak hilang.
        if parent_item.childCount() == 0 and self.is_mount_node(parent_item):
            self.add_loading(parent_item)

    def _style_mount_item(self, it, child, is_link, is_dir, is_dir_symlink, img_path=None, internal_path=None):

        if is_link:
            if is_dir_symlink:
                ic = getattr(self, "icon_symlink", None)
            else:
                ic = getattr(self, "icon_symlink_file", None)
        elif is_dir:
            ic = getattr(self, "icon_folder", None)
        else:
            ic = self._resolve_file_icon(child["name"]) if hasattr(self, "_resolve_file_icon") else getattr(self, "icon_file", None)
        if ic:
            it.setIcon(0, ic)

        # Preview asli (resolusi kecil) buat file gambar di dalam image
        # mounted — dibaca langsung dari image (imgfs), tanpa unpack.
        if not is_link and not is_dir and img_path is not None and hasattr(self, "_schedule_mount_thumbnail"):
            self._schedule_mount_thumbnail(it, img_path, internal_path or child["name"], child.get("ino"))

    def _refresh_mount_badge(self, item):

        ic = getattr(self, "icon_drive_mounted", None) or getattr(self, "icon_drive", None)
        if ic:
            item.setIcon(0, ic)

    # drag KELUAR
    def _start_mount_drag(self, items, supportedActions):

        from PySide6.QtCore import QMimeData, QPoint
        from PySide6.QtGui import QDrag
        import json

        refs, ref_items = [], []
        for it in items:
            ref = self.mount_ref(it)
            # Jika ref berupa (target_img, "") (root cross-portal), ref[1] bernilai ""
            if ref and ref[0]:
                refs.append((ref[0], ref[1], it.text(0)))
                ref_items.append(it)
        if not refs:
            return

        payload = json.dumps(refs)
        mime = QMimeData()
        mime.setData("application/x-rdt-mount", payload.encode("utf-8"))

        main = self.window()
        # Bersihkan sisa state drag internal supaya dropEvent tak salah
        # mengiranya sebagai pemindahan file di dalam project.
        for attr in ("drag_src_paths", "drag_src_phys_paths",
                     "drag_src_path", "drag_src_phys_path", "drag_src_parent"):
            try:
                setattr(main, attr, None)
            except Exception:
                pass

        drag = QDrag(self)
        drag.setMimeData(mime)


        pm = self._mount_drag_pixmap(ref_items)
        if pm is not None:
            drag.setPixmap(pm)
            drag.setHotSpot(QPoint(min(12, pm.width() - 1),
                                   min(10, pm.height() - 1)))

        drag.exec(Qt.CopyAction)

    def drop_mount_extract(self, items_info, dest_dir,
                           target_phys=None, branch_target=False,
                           expanded=None):

        from core import imgfs
        from main_ext.class_AppDialog import AppDialog

        phys = target_phys or dest_dir
        main = self.window()
        label = f"drop-mount-{id(items_info)}"
        if hasattr(main, "push_task"):
            try:
                main.push_task(label)
            except Exception:
                pass

        def work():
            os.makedirs(phys, exist_ok=True)
            done_, failed_ = [], []


            selected = []
            for img_path, internal, name in items_info:
                internal = internal.strip("/").replace("\\", "/")
                img_key = os.path.normcase(os.path.abspath(img_path))
                if any(key == img_key and parent == internal
                       for key, parent, _n, _p in selected):
                    continue
                if any(key == img_key and parent == "" for key, parent, _n, _p
                       in selected):
                    continue
                if internal == "":
                    selected = [entry for entry in selected if entry[0] != img_key]
                elif any(key == img_key and internal.startswith(parent + "/")
                         for key, parent, _n, _p in selected if parent):
                    continue
                else:
                    selected = [entry for entry in selected
                                if not (entry[0] == img_key and entry[1] and
                                        entry[1].startswith(internal + "/"))]
                selected.append((img_key, internal, name, img_path))

            output_groups = {}
            for entry in selected:
                _img_key, internal, name, _img_path = entry
                output = name if not internal else internal.rsplit("/", 1)[-1]
                output_groups.setdefault(output.casefold(), []).append(entry)
            blocked = set()
            for output, group in output_groups.items():
                if len(group) > 1:
                    paths = ", ".join(internal or "/" for _key, internal, _name, _path
                                      in group)
                    failed_.append(
                        f"output '{output}' bertabrakan ({paths}); pilih tujuan terpisah")
                    blocked.update((key, internal) for key, internal, _name, _path
                                   in group)

            for _key, internal, name, img_path in selected:
                if (_key, internal) in blocked:
                    continue
                try:
                    if internal == "":
                        target_path = os.path.join(phys, name)
                        os.makedirs(target_path, exist_ok=True)
                        res = imgfs.extract_path(img_path, internal, target_path)
                    else:
                        res = imgfs.extract_path(img_path, internal, phys)
                    done_.append(res)
                except Exception as ex:
                    failed_.append(f"{internal}: {type(ex).__name__}: {ex}")
            return True, (done_, failed_)

        def finish(payload):
            done_, failed_ = payload
            if hasattr(main, "pop_task"):
                try:
                    main.pop_task(label)
                except Exception:
                    pass

            # Catat hasil ke NeoBranch — tanpa ini, isi yang baru ditulis
            # tidak dikenali branch dan bisa tertimpa/hilang saat switch.
            if branch_target:
                try:
                    for name in os.listdir(phys):
                        logical = os.path.join(dest_dir, name)
                        physical = os.path.join(phys, name)
                        rel, fs_root, nb, active = \
                            self._nb_active_state_for_logical(logical)
                        if nb and active and os.path.exists(logical):
                            nb.record_delete(fs_root, active["name"], rel,
                                             prune_overlay=False)
                        self._nb_record_output_tree(logical, physical)
                except Exception:
                    pass

            try:
                node = self.find_item_by_path(self.topLevelItem(0), dest_dir)
                if node is not None:
                    self.refresh_folder(node)
                if expanded:
                    self.restore_expanded_paths(expanded)
            except Exception:
                pass

            if failed_:
                try:
                    AppDialog.warning(self.window(), "Sebagian gagal diekstrak",
                                      "\n".join(failed_[:5]))
                except Exception:
                    print("[MOUNT] drop gagal:", failed_[:5])
            elif hasattr(main, "set_loading_finished"):
                try:
                    main.set_loading_finished("Extracted from image")
                except Exception:
                    pass

        def fail(err):
            if hasattr(main, "pop_task"):
                try:
                    main.pop_task(label)
                except Exception:
                    pass
            try:
                AppDialog.warning(self.window(), "Extract gagal", str(err))
            except Exception:
                print("[MOUNT] drop gagal:", err)

        run_in_thread(work, None,
                      callback=lambda ok, p: finish(p) if ok else fail(p))

    def _mount_drag_pixmap(self, items, max_rows=6):

        from PySide6.QtCore import QRect
        from PySide6.QtGui import QPixmap, QPainter, QFontMetrics

        rows = []
        for it in items[:max_rows]:
            r = self.visualItemRect(it)
            if not r.isValid() or r.isEmpty():
                continue                      # baris tak terlihat (collapsed)
            rows.append((it, r.height() or self.fontMetrics().height() + 8))
        if not rows:
            return None

        fm = QFontMetrics(self.font())
        icon_sz = self.iconSize()
        isz = icon_sz.height() if icon_sz.isValid() and icon_sz.height() > 0 else 16
        gap = 6

        # lebar = ikon + teks terpanjang (dibatasi supaya tak jadi pita panjang)
        text_w = 0
        for (it, _h) in rows:
            text_w = max(text_w, fm.horizontalAdvance(it.text(0)))
        text_w = min(text_w, 320)
        w = isz + gap + text_w + 2
        h = sum(rh for (_it, rh) in rows)
        if w < 1 or h < 1 or w > 4096 or h > 4096:
            return None

        pm = QPixmap(w, h)
        pm.fill(Qt.transparent)
        painter = QPainter(pm)
        try:
            painter.setOpacity(0.85)
            painter.setFont(self.font())
            painter.setPen(self.palette().text().color())
            y = 0
            for (it, rh) in rows:
                ic = it.icon(0)
                if ic is not None and not ic.isNull():
                    ic.paint(painter,
                             QRect(0, y + (rh - isz) // 2, isz, isz))
                label = fm.elidedText(it.text(0), Qt.ElideMiddle, text_w)
                painter.drawText(QRect(isz + gap, y, text_w, rh),
                                 Qt.AlignVCenter | Qt.AlignLeft, label)
                y += rh
        finally:
            painter.end()
        return pm

    # klik-kanan
    def _mount_rightclick(self, item, ref, global_pos):

        from PySide6.QtWidgets import QWidget, QVBoxLayout, QPushButton
        from PySide6.QtGui import QColor as _QC
        from PySide6.QtCore import QEventLoop
        from main_ext.class_ThemeUi import theme as _th
        from app_ext.class_BlurOverlay import BlurCard
        from core.ui_lang import t

        img_path, internal = ref
        writable = self.mount_writable(img_path)
        is_folder = item.childIndicatorPolicy() == QTreeWidgetItem.ShowIndicator

        popup = QWidget(None, Qt.Tool | Qt.FramelessWindowHint |
                        Qt.WindowStaysOnTopHint | Qt.NoDropShadowWindowHint)
        popup.setAttribute(Qt.WA_TranslucentBackground)
        popup.setAttribute(Qt.WA_DeleteOnClose)
        outer = QVBoxLayout(popup)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        _br, _bg, _bb = _th._palette["_border_rgb"]
        _tint = _QC(_th.BG2); _tint.setAlpha(128)
        card = BlurCard(radius=18.0, tint=_tint,
                        border=_QC(_br, _bg, _bb, int(0.18 * 255)))
        card.setObjectName("popup_card")
        outer.addWidget(card)
        lay = QVBoxLayout(card)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(1)

        chosen = [None]

        def _btn(label, enabled=True, danger=False):
            b = QPushButton(label)
            b.setFixedHeight(32)
            b.setCursor(Qt.PointingHandCursor)
            b.setEnabled(enabled)
            fg   = _th.FG if not danger else _th.RED_LIGHT
            fg_h = _th.FG_BRIGHT if not danger else _th.RED_LIGHTER
            b.setStyleSheet(
                f"QPushButton{{background:transparent;border:none;color:{fg};"
                f"text-align:left;padding:0 12px;border-radius:12px;}}"
                f"QPushButton:hover{{background:{_th.BG3};color:{fg_h};}}"
                f"QPushButton:disabled{{color:{_th.FG_DIM};}}")
            lay.addWidget(b)
            return b

        def _pick(action):
            chosen[0] = action
            popup.close()

        from core.imgfs import is_text_filename
        is_text = is_folder or is_text_filename(internal)

        b_open = _btn("Expand" if is_folder else "Open (Text)", enabled=is_text)
        b_open.clicked.connect(lambda: _pick("open"))

        b_ext = _btn("Extract to...")
        b_ext.clicked.connect(lambda: _pick("extract"))

        b_del = _btn(t("rc_delete"), enabled=writable, danger=True)
        b_del.clicked.connect(lambda: _pick("delete"))

        popup.adjustSize()
        from main_ext.FileTree_ext.RightClick import _clamp_popup_pos
        _clamp_popup_pos(popup, global_pos, self.window())
        popup.show()

        # Tutup saat klik di luar / window bergeser, supaya popup tak nyangkut.
        from PySide6.QtCore import QEvent, QObject
        from PySide6.QtWidgets import QApplication

        closing = [False]

        def _close():
            if closing[0]:
                return
            closing[0] = True
            try:
                popup.close()
            except RuntimeError:
                pass

        class _Outside(QObject):
            def eventFilter(self, obj, event):
                if closing[0]:
                    return False
                et = event.type()
                if et == QEvent.ApplicationDeactivate:
                    _close()
                elif et == QEvent.MouseButtonPress:
                    try:
                        gp = event.globalPosition().toPoint()
                    except AttributeError:
                        gp = event.globalPos()
                    r = popup.rect()
                    r.moveTopLeft(popup.mapToGlobal(r.topLeft()))
                    if not r.contains(gp):
                        _close()
                return False

        app = QApplication.instance()
        filt = _Outside(app) if app is not None else None
        if filt is not None:
            app.installEventFilter(filt)

        loop = QEventLoop()
        popup.destroyed.connect(loop.quit)
        loop.exec()

        if filt is not None:
            try:
                app.removeEventFilter(filt)
            except RuntimeError:
                pass

        if chosen[0] == "open":
            if is_folder:
                item.setExpanded(not item.isExpanded())
            else:
                win = self.window()
                if hasattr(win, "open_mount_file_in_editor"):
                    win.open_mount_file_in_editor(img_path, internal,
                                                  read_only=not writable)
        elif chosen[0] == "extract":
            self._mount_extract(ref)
        elif chosen[0] == "delete":
            self._mount_delete(item, ref, is_folder)

    def show_mount_usage(self, img_path):

        from core import imgfs
        from main_ext.class_AppDialog import AppDialog

        main = self.window()
        label = f"usage-{os.path.normpath(img_path)}"
        if hasattr(main, "push_task"):
            try:
                main.push_task(label)
            except Exception:
                pass

        def work():
            return True, imgfs.usage_report(img_path)

        def _mb(n):
            if n < 1024:
                return f"{n} B"
            x = float(n)
            for u in ("KB", "MB", "GB"):
                x /= 1024.0
                if x < 1024:
                    return f"{x:.1f} {u}"
            return f"{x:.1f} TB"

        def done(u):
            if hasattr(main, "pop_task"):
                try:
                    main.pop_task(label)
                except Exception:
                    pass
            lines = [
                f"{os.path.basename(img_path)}  ({u['fs']})",
                "",
                f"Ukuran image   : {_mb(u['file_size'])}",
                f"Terpakai       : {_mb(u['used_bytes'])}",
            ]
            reclaim = 0
            if u["fs"] == "EROFS":
                reclaim = u["reclaimable_bytes"]
                lines += [
                    f"Terbuang       : {_mb(u['wasted_bytes'])}",
                    "",
                    f"Bisa dikembalikan: {_mb(reclaim)}",
                    "",
                    "Hapus di EROFS hanya melepas entri direktori; bloknya",
                    "tetap menempati image sampai dipadatkan.",
                ]
            else:
                lines += [
                    f"Ruang bebas    : {_mb(u.get('free_bytes', 0))}",
                    "",
                    "Ruang hasil penghapusan sudah benar-benar bebas",
                    "(tak perlu dipadatkan).",
                ]
            lines += [
                "",
                f"Isi: {u['files']} file, {u['dirs']} folder, {u['links']} symlink",
                f"Total ukuran isi (logis): {_mb(u['logical_bytes'])}",
            ]
            msg = "\n".join(lines)
            try:
                # Tawarkan pemadatan hanya bila ada yang bisa dikembalikan.
                if u["fs"] == "EROFS" and reclaim >= 1024 * 1024:
                    if AppDialog.confirm(
                            self.window(), "Space usage",
                            msg + f"\n\nPadatkan sekarang agar berkas .img "
                                  f"mengecil ~{_mb(reclaim)}?",
                            yes_label="Padatkan", no_label="Nanti"):
                        self.compact_mount_image(img_path)
                else:
                    AppDialog.warning(self.window(), "Space usage", msg)
            except Exception:
                print("[MOUNT]", " | ".join(lines))

        def fail(err):
            if hasattr(main, "pop_task"):
                try:
                    main.pop_task(label)
                except Exception:
                    pass
            try:
                AppDialog.warning(self.window(), "Gagal menghitung", str(err))
            except Exception:
                print("[MOUNT] usage gagal:", err)

        run_in_thread(work, None,
                      callback=lambda ok, payload: done(payload) if ok else fail(payload))

    def compact_mount_image(self, img_path):

        from core import imgfs
        from main_ext.class_AppDialog import AppDialog

        main = self.window()
        label = f"compact-{os.path.normpath(img_path)}"
        if hasattr(main, "push_task"):
            try:
                main.push_task(label)
            except Exception:
                pass

        def _mb(n):
            x = float(n)
            for u in ("B", "KB", "MB", "GB"):
                if x < 1024 or u == "GB":
                    return f"{x:.1f} {u}"
                x /= 1024.0

        def work():
            return True, imgfs.compact_image(img_path)

        def done(res):
            if hasattr(main, "pop_task"):
                try:
                    main.pop_task(label)
                except Exception:
                    pass
            # Index mount memuat nid lama — muat ulang supaya tetap sahih.
            key = os.path.normpath(img_path)
            # .img nyusut di tempat (nama & entri direktori gak berubah), jadi
            # label size-nya gak otomatis dihitung ulang — paksa di sini.
            try:
                self.invalidate_size(img_path)
            except Exception:
                pass
            try:
                item = self._find_mount_root_item(key)
                if item is not None:
                    self.unmount_image(item)
                    self.mount_image(item)
            except Exception:
                pass
            try:
                AppDialog.warning(
                    self.window(), "Selesai dipadatkan",
                    f"{os.path.basename(img_path)}\n\n"
                    f"{_mb(res['before'])} → {_mb(res['after'])}\n"
                    f"Hemat {_mb(res['saved'])}")
            except Exception:
                print("[MOUNT] compact:", res)

        def fail(err):
            if hasattr(main, "pop_task"):
                try:
                    main.pop_task(label)
                except Exception:
                    pass
            try:
                AppDialog.warning(self.window(), "Gagal memadatkan", str(err))
            except Exception:
                print("[MOUNT] compact gagal:", err)

        run_in_thread(work, None,
                      callback=lambda ok, p: done(p) if ok else fail(p))

    def _find_mount_root_item(self, key):

        stack = [self.topLevelItem(i) for i in range(self.topLevelItemCount())]
        while stack:
            it = stack.pop()
            if it is None:
                continue
            ref = self.mount_ref(it)
            if ref and not ref[1] and os.path.normpath(ref[0]) == key:
                return it
            for i in range(it.childCount()):
                stack.append(it.child(i))
        return None

    def _mount_extract(self, ref):

        from PySide6.QtWidgets import QFileDialog
        from core import imgfs
        from main_ext.class_AppDialog import AppDialog

        img_path, internal = ref
        name = internal.rsplit("/", 1)[-1]

        dest = QFileDialog.getExistingDirectory(
            self.window(), f"Extract '{name}' ke folder...",
            os.path.dirname(img_path))
        if not dest:
            return

        main = self.window()
        label = f"extract-mount-{id(ref)}"
        if hasattr(main, "push_task"):
            try:
                main.push_task(label)
            except Exception:
                pass

        def work():
            res = imgfs.extract_path(img_path, internal, dest)
            return True, res

        def done(res):
            if hasattr(main, "pop_task"):
                try:
                    main.pop_task(label)
                except Exception:
                    pass
            msg = (f"'{name}' diekstrak ke:\n{res['root']}\n\n"
                   f"{res['files']} file, {res['dirs']} folder")
            if res.get("skipped_links"):
                msg += (f"\n\n{res['skipped_links']} symlink tak bisa dibuat "
                        f"di Windows — targetnya dicatat di file metadata.")
            try:
                AppDialog.warning(self.window(), "Extract selesai", msg)
            except Exception:
                print("[MOUNT]", msg)

        def fail(err):
            if hasattr(main, "pop_task"):
                try:
                    main.pop_task(label)
                except Exception:
                    pass
            try:
                AppDialog.warning(self.window(), "Extract gagal", str(err))
            except Exception:
                print("[MOUNT] extract gagal:", err)

        run_in_thread(work, None,
                      callback=lambda ok, payload: done(payload) if ok else fail(payload))

    def _mount_delete(self, item, ref, is_folder):

        from core import imgfs
        from main_ext.class_AppDialog import AppDialog
        from core.ui_lang import t

        img_path, internal = ref
        name = internal.rsplit("/", 1)[-1]

        # hitung isi folder supaya user tahu persis yang bakal hilang
        extra = ""
        if is_folder:
            index = self._ensure_mounts().get(os.path.normpath(img_path), {})
            n = sum(1 for parent in index
                    for c in index[parent]
                    if parent == internal or parent.startswith(internal + "/"))
            if n:
                extra = f"\n\nFolder ini berisi {n} item yang ikut terhapus."

        if not AppDialog.confirm(
                self.window(), "Hapus dari image",
                f"Hapus '{name}' dari {os.path.basename(img_path)}?{extra}\n\n"
                f"Perubahan langsung ditulis ke image.",
                yes_label=t("rc_delete")):
            return

        try:
            imgfs.delete_path(img_path, internal, recursive=True)
        except (ValueError, NotImplementedError, FileNotFoundError, OSError) as ex:
            AppDialog.warning(self.window(), "Tidak bisa menghapus", str(ex))
            return

        # buang dari index mount + tree, termasuk seluruh keturunannya
        key = os.path.normpath(img_path)
        index = self._ensure_mounts().get(key, {})
        parent_internal = internal.rsplit("/", 1)[0] if "/" in internal else ""
        siblings = index.get(parent_internal, [])
        index[parent_internal] = [c for c in siblings if c["name"] != name]
        for p in [k for k in index
                  if k == internal or k.startswith(internal + "/")]:
            index.pop(p, None)

        parent_item = item.parent()
        if parent_item is not None:
            parent_item.removeChild(item)
            if parent_item.childCount() == 0 and self.is_mount_node(parent_item):
                self.add_loading(parent_item)
        else:
            idx = self.indexOfTopLevelItem(item)
            if idx >= 0:
                self.takeTopLevelItem(idx)
