"""Original local picker locations and size labels."""
import os,sys

def _get_drives():
    drives = []
    if sys.platform == 'win32':
        # Query Windows' logical-drive table without touching each volume.
        # stat/exists on an unavailable mapped/removable drive can block for
        # minutes, even when a caller only needs its sidebar label.
        drives = os.listdrives()
    else:
        drives.append('/')
        for base in ('/media', '/mnt', '/Volumes', '/run/media'):
            if os.path.isdir(base):
                try:
                    for name in sorted(os.listdir(base)):
                        full = os.path.join(base, name)
                        if os.path.isdir(full):
                            drives.append(full)
                except PermissionError:
                    pass
    return drives

def _quick_access():
    entries = []
    home = os.path.expanduser('~')
    candidates = [('Home', home), ('Desktop', os.path.join(home, 'Desktop')), ('Downloads', os.path.join(home, 'Downloads')), ('Documents', os.path.join(home, 'Documents'))]
    if sys.platform == 'win32':
        candidates += [('Pictures', os.path.join(home, 'Pictures')), ('Videos', os.path.join(home, 'Videos')), ('Music', os.path.join(home, 'Music'))]
    for label, path in candidates:
        if os.path.isdir(path):
            entries.append((label, path))
    return entries

def _file_size_str(path):
    try:
        b = os.path.getsize(path)
        for unit in ('B', 'KB', 'MB', 'GB'):
            if b < 1024:
                return f'{b:.0f} {unit}'
            b /= 1024
        return f'{b:.1f} TB'
    except Exception:
        return ''
