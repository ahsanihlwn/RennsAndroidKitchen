# SPDX-License-Identifier: MPL-2.0
"""Original mounted-image index and Android symlink resolution, without widget items."""
import os,re

def _build_child_index(entries):
    index = {}
    for e in entries:
        p = e['path'].strip('/')
        if not p:
            continue
        if '/' in p:
            parent, name = p.rsplit('/', 1)
        else:
            parent, name = ('', p)
        child = dict(e)
        child['name'] = name
        index.setdefault(parent, []).append(child)
    for lst in index.values():
        lst.sort(key=lambda c: (not c['is_dir'], c['name'].lower()))
    return index

class MountIndex:

    def __init__(self, base):
        self.base = base
        self.indexes = {}
        self.metadata = {}

    def _ensure_mounts(self):
        return self.indexes

    def _ensure_mounts_meta(self):
        return self.metadata

    @staticmethod
    def _lookup_in_mount_index(index, subpath):
        sub = subpath.strip('/').replace('\\', '/')
        if not sub:
            return (True, True, '')
        if sub in index:
            return (True, True, sub)
        parent = sub.rsplit('/', 1)[0] if '/' in sub else ''
        name = sub.rsplit('/', 1)[-1]
        for c in index.get(parent, []):
            if c['name'].lower() == name.lower():
                actual_sub = (parent + '/' + c['name']).strip('/') if parent else c['name']
                return (True, c['is_dir'], actual_sub)
        return (False, False, '')

    @staticmethod
    def _match_mount_partition(img_key, partition_name):
        base = os.path.basename(img_key).lower()
        p_lower = partition_name.lower()
        stem = os.path.splitext(base)[0]
        while '.' in stem:
            stem = os.path.splitext(stem)[0]
        stem_clean = re.sub('(_slot)?[_-][ab]$', '', stem)
        if stem_clean == p_lower:
            return True
        if base.startswith(p_lower + '.'):
            return True
        if base.startswith(p_lower + '-'):
            return True
        norm_p = p_lower.replace('_', '').replace('-', '')
        norm_stem = stem_clean.replace('_', '').replace('-', '')
        if norm_p == norm_stem:
            return True
        if p_lower == 'product' and stem_clean in ('my_product', 'mi_ext'):
            return True
        if p_lower == 'system_ext' and stem_clean in ('mi_ext',):
            return True
        return False

    @classmethod
    def _match_score(cls, img_key, partition_name):
        if not cls._match_mount_partition(img_key, partition_name):
            return -1
        base = os.path.basename(img_key).lower()
        stem = os.path.splitext(base)[0]
        while '.' in stem:
            stem = os.path.splitext(stem)[0]
        stem_clean = re.sub('(_slot)?[_-][ab]$', '', stem)
        p_lower = partition_name.lower()
        if stem_clean == p_lower:
            return 2
        if stem_clean.replace('_', '').replace('-', '') == p_lower.replace('_', '').replace('-', ''):
            return 1
        return 0

    def _best_mount_for_partition(self, partition_name, exclude_key=None):
        best, best_score = (None, -1)
        for m_key in self._ensure_mounts():
            if exclude_key and os.path.normpath(m_key) == exclude_key:
                continue
            score = self._match_score(m_key, partition_name)
            if score > best_score:
                best, best_score = (m_key, score)
        return best if best_score >= 0 else None

    def _resolve_symlink_target_for_mount(self, img_path, internal, target):
        key = os.path.normpath(img_path)
        norm_target = target.replace('\\', '/').strip()
        if norm_target.startswith('/'):
            clean = norm_target.lstrip('/')
        else:
            parent = internal.rsplit('/', 1)[0] if '/' in internal else ''
            parts = parent.split('/') if parent else []
            for p in norm_target.split('/'):
                if p in ('', '.'):
                    continue
                elif p == '..':
                    if parts:
                        parts.pop()
                else:
                    parts.append(p)
            clean = '/'.join(parts)
        clean_norm = clean.strip('/')
        current_cand_subs = [clean_norm]
        if clean_norm.startswith('system/'):
            current_cand_subs.append(clean_norm[7:])
        else:
            current_cand_subs.append('system/' + clean_norm)
        curr_index = self._ensure_mounts().get(key, {})
        stub_hit = None
        for cand in current_cand_subs:
            if cand == internal.strip('/'):
                continue
            found, is_d, actual = self._lookup_in_mount_index(curr_index, cand)
            if not found:
                continue
            if is_d and (not curr_index.get(actual.strip('/'))):
                if stub_hit is None:
                    stub_hit = (actual, is_d)
                continue
            return ((key, actual), None, is_d, True)
        KNOWN_PARTITIONS = ['system_ext', 'system_dlkm', 'vendor_dlkm', 'odm_dlkm', 'my_product', 'my_stock', 'my_heytap', 'my_carrier', 'my_region', 'my_engineering', 'my_preload', 'mi_ext', 'system', 'product', 'vendor', 'odm', 'oem', 'cust', 'preload', 'apex', 'data']
        split_candidates = []
        if clean_norm.startswith('system/'):
            sub_after_system = clean_norm[7:].strip('/')
            for p in KNOWN_PARTITIONS:
                if p == 'system':
                    continue
                if sub_after_system == p:
                    split_candidates.append((p, ['', p]))
                elif sub_after_system.startswith(p + '/'):
                    sub_p = sub_after_system[len(p) + 1:]
                    split_candidates.append((p, [sub_p, p + '/' + sub_p]))
        if clean_norm.startswith('vendor/'):
            sub_after_vendor = clean_norm[7:].strip('/')
            for p in KNOWN_PARTITIONS:
                if p == 'vendor':
                    continue
                if sub_after_vendor == p:
                    split_candidates.append((p, ['', p]))
                elif sub_after_vendor.startswith(p + '/'):
                    sub_p = sub_after_vendor[len(p) + 1:]
                    split_candidates.append((p, [sub_p, p + '/' + sub_p]))
        for p in KNOWN_PARTITIONS:
            if clean_norm == p:
                split_candidates.append((p, ['', p]))
            elif clean_norm.startswith(p + '/'):
                sub_p = clean_norm[len(p) + 1:]
                split_candidates.append((p, [sub_p, p + '/' + sub_p]))
        if '/' in clean_norm:
            first, rest = clean_norm.split('/', 1)
            split_candidates.append((first, [rest, clean_norm]))
        elif clean_norm:
            split_candidates.append((clean_norm, ['', clean_norm]))
        if any((clean_norm.startswith(pre) for pre in ('bin/', 'etc/', 'framework/', 'lib/', 'lib64/', 'xbin/', 'priv-app/', 'app/', 'media/', 'usr/', 'fonts/'))):
            split_candidates.append(('system', [clean_norm, 'system/' + clean_norm]))
        all_mounts = self._ensure_mounts()
        for target_partition, subpath_cands in split_candidates:
            ranked = sorted(all_mounts.items(), key=lambda kv: self._match_score(kv[0], target_partition), reverse=True)
            for m_key, m_index in ranked:
                if m_key == key and target_partition != 'system':
                    continue
                if self._match_score(m_key, target_partition) < 0:
                    continue
                for sub in subpath_cands:
                    if not sub:
                        return ((m_key, ''), None, True, True)
                    found, is_d, actual = self._lookup_in_mount_index(m_index, sub)
                    if found:
                        return ((m_key, actual), None, is_d, True)
                if '' in subpath_cands or not subpath_cands[0]:
                    return ((m_key, ''), None, True, True)
        if stub_hit is not None:
            actual, is_d = stub_hit
            return ((key, actual), None, is_d, True)
        base = getattr(self, 'base', None)
        if base:
            project_root = base
            base_dir = os.path.dirname(os.path.join(base, internal))
            disk_candidates = []
            if norm_target.startswith('/'):
                if clean_norm.startswith('system/'):
                    disk_candidates.append(os.path.normpath(os.path.join(project_root, clean_norm)))
                    sub = clean_norm[7:]
                    if sub:
                        disk_candidates.append(os.path.normpath(os.path.join(project_root, sub)))
                    disk_candidates.append(os.path.normpath(os.path.join(project_root, 'system', clean_norm)))
                else:
                    disk_candidates.append(os.path.normpath(os.path.join(project_root, clean_norm)))
                    disk_candidates.append(os.path.normpath(os.path.join(project_root, 'system', clean_norm)))
                    parent = os.path.dirname(project_root)
                    if parent and parent != project_root:
                        disk_candidates.append(os.path.normpath(os.path.join(parent, clean_norm)))
            else:
                disk_candidates.append(os.path.normpath(os.path.join(base_dir, norm_target)))
                disk_candidates.append(os.path.normpath(os.path.join(project_root, norm_target)))
                disk_candidates.append(os.path.normpath(os.path.join(project_root, 'system', norm_target)))
            for c in disk_candidates:
                if os.path.exists(c):
                    return (None, c, os.path.isdir(c), True)
            fallback = disk_candidates[0] if disk_candidates else os.path.normpath(os.path.join(project_root, clean_norm))
        else:
            fallback = clean_norm
        clean_t = norm_target.rstrip('/')
        last_part = clean_t.split('/')[-1].lower() if clean_t else ''
        name_clean = internal.rsplit('/', 1)[-1].lower()
        if norm_target.endswith('/'):
            is_dir_fallback = True
        elif '.' in last_part or '.' in name_clean:
            is_dir_fallback = False
        elif any((p in norm_target.lower() for p in ('/bin/', '/xbin/', '/sbin/'))):
            is_dir_fallback = False
        else:
            is_dir_fallback = True
        return (None, fallback, is_dir_fallback, False)

    def _mountpoint_redirect(self, key, internal):
        name = internal.strip('/')
        if not name or '/' in name:
            return None
        best = self._best_mount_for_partition(name, exclude_key=key)
        return (best, '') if best else None
