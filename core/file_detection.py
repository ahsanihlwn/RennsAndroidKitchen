# SPDX-License-Identifier: MPL-2.0
"""Live file identity: links first, then the Kitchen's header-magic service."""
import os
import zipfile


def identify(files,path):
    link=files.view.link_info(path)
    if link:
        return {'kind':'SYMLINK','detected_by':'native_symlink' if link['representation']=='native' else 'symlink_header',
                'header_magic':None,'extension_hint':None,'symlink':link,'origin':link['origin']}
    logical=files.view.canonical(path,follow_final=False)
    physical,origin=files.view.resolve(logical)
    if not physical:raise FileNotFoundError(path)
    if os.path.isdir(physical):
        from core.fsdetect import detect_fs_root
        kind=detect_fs_root(os.path.dirname(logical),os.path.basename(logical))
        if not kind and physical!=logical:kind=detect_fs_root(os.path.dirname(physical),os.path.basename(physical))
        return {'kind':kind or 'directory','detected_by':'filesystem_metadata' if kind else 'filesystem',
                'header_magic':None,'extension_hint':None,'symlink':None,'origin':origin}
    from core import header_magic
    magic=header_magic.check_magic(physical)
    lower=path.lower();hint=None
    if lower.endswith('.new.dat.br'):hint='DAT.BR'
    elif lower.endswith('.new.dat'):hint='DAT'
    elif lower.endswith('.br'):hint='BR'
    elif lower.endswith('.apk'):hint='APK'
    else:
        from core.file_types import ARCHIVE_EXT
        if lower.endswith(ARCHIVE_EXT):hint='archive'
    if magic:kind=magic;method='header_magic'
    elif zipfile.is_zipfile(physical):
        with zipfile.ZipFile(physical) as archive:kind='APK' if 'AndroidManifest.xml' in archive.namelist() else 'archive'
        method='zip_structure'
    else:kind=hint or 'file';method='filename_hint' if hint else 'unknown'
    return {'kind':kind,'detected_by':method,'header_magic':magic or None,'extension_hint':hint,
            'symlink':None,'origin':origin,'size':os.path.getsize(physical)}
