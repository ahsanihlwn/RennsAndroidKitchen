# SPDX-License-Identifier: MPL-2.0
"""Original partition selection, naming and archive policy."""
import os

_DYNAMIC_NAMES = {'system', 'vendor', 'product', 'system_ext', 'odm', 'vendor_dlkm', 'odm_dlkm', 'system_dlkm', 'my_engineering', 'my_heytap', 'my_bigball', 'my_carrier', 'my_company', 'my_manifest', 'my_preload', 'my_product', 'my_region', 'my_stock'}
_ROOT_BOTHSLOT_NAMES = {'boot', 'vendor_boot', 'init_boot', 'dtbo', 'recovery', 'vbmeta', 'vbmeta_system', 'vbmeta_vendor', 'exaid', 'vbmeta_product'}
_PART_EXTS = ('.img', '.zst', '.zstd', '.br', '.new.dat', '.new.dat.br', '.bin')
_IGNORE_PARTITION_NAMES = {'payload.bin', 'care_map.pb', 'apex_info.pb', 'compatibility_matrix.xml', 'manifest.xml', 'otacerts.zip', 'metadata'}

def _guess_partition_name(path):
    base = os.path.basename(path)
    for suf in ('.new.dat.br', '.new.dat', '.img.zst', '.img.br', '.img', '.zst', '.zstd', '.br', '.bin'):
        if base.lower().endswith(suf):
            return base[:-len(suf)]
    return os.path.splitext(base)[0]

def _guess_role(name, badge, in_firmware_dir=False):
    low = name.lower()
    if in_firmware_dir:
        return 'both_slot'
    if low.startswith('tr_') or low.startswith('my_') or low in _DYNAMIC_NAMES:
        return 'dynamic'
    if low in _ROOT_BOTHSLOT_NAMES or badge in ('BOOT', 'VNDRBOOT'):
        return 'both_slot'
    if badge in ('EROFS', 'EXT2', 'EXT3', 'EXT4', 'SPARSE', 'DAT', 'DAT.BR', 'BR'):
        return 'dynamic'
    return 'both_slot'

def _guess_in_firmware(name, in_firmware_dir):
    low = name.lower()
    if in_firmware_dir:
        return True
    return low not in _ROOT_BOTHSLOT_NAMES and (not low.startswith('vbmeta'))

def _src_ext(path):
    low = path.lower()
    if low.endswith('.bin'):
        return '.bin'
    if low.endswith('.img'):
        return '.img'
    return '.img'

def _arc_for(name, role, zstd, src='', in_firmware=False):
    if role == 'both_slot':
        if in_firmware:
            return f'firmware/{name}{_src_ext(src)}'
        return f'{name}{_src_ext(src)}'
    return f'{name}.img.zst' if zstd else f'{name}.img'

def _looks_like_partition(path):
    base = os.path.basename(path).lower()
    if base in _IGNORE_PARTITION_NAMES:
        return False
    low = path.lower()
    return any((low.endswith(e) for e in _PART_EXTS))

def _scan_siblings(seed_path, badge_fn):
    seed_path = os.path.abspath(seed_path)
    seed_dir = os.path.dirname(seed_path)
    found = []
    seen = set()

    def _add(p, in_fw):
        ap = os.path.abspath(p)
        if ap in seen or not os.path.isfile(ap) or (not _looks_like_partition(ap)):
            return
        seen.add(ap)
        try:
            b = badge_fn(ap) if badge_fn else ''
        except Exception:
            b = ''
        found.append((ap, b, in_fw))
    _add(seed_path, False)
    try:
        for name in sorted(os.listdir(seed_dir)):
            _add(os.path.join(seed_dir, name), False)
    except Exception:
        pass
    fw_dir = os.path.join(seed_dir, 'firmware')
    if os.path.isdir(fw_dir):
        try:
            for name in sorted(os.listdir(fw_dir)):
                _add(os.path.join(fw_dir, name), True)
        except Exception:
            pass
    return found
