# SPDX-License-Identifier: MPL-2.0
"""Original archive reader and extraction logic without widget presentation."""
import os,zipfile,traceback
rarfile=None
py7zr=None

def _archive_backend(name):
    # Optional codecs are needed for their archive type, not for startup/ZIP.
    module=globals()[name]
    if module is None:
        from importlib import import_module
        try:module=import_module(name)
        except ImportError as error:
            raise RuntimeError(f'{name} not installed: pip install {name}') from error
        globals()[name]=module
    return module

def _fmt_size(n: int) -> str:
    if n < 1024:
        return f'{n} B'
    for unit in ('KB', 'MB', 'GB'):
        n /= 1024.0
        if n < 1024:
            return f'{n:.1f} {unit}'
    return f'{n:.1f} GB'

def _detect_archive_type(filepath: str):
    ext = filepath.lower()
    if ext.endswith('.pac'):
        from core.pac_lister import list_pac
        listing = list_pac(filepath)
        return ('pac', [entry.archive_path for entry in listing.entries])
    if ext.endswith('.tar.gz') or ext.endswith('.tgz'):
        import tarfile
        with tarfile.open(filepath, 'r:gz') as tf:
            return ('tar.gz', tf.getnames())
    if ext.endswith('.tar.bz2') or ext.endswith('.tbz2'):
        import tarfile
        with tarfile.open(filepath, 'r:bz2') as tf:
            return ('tar.bz2', tf.getnames())
    if ext.endswith('.tar.xz') or ext.endswith('.txz'):
        import tarfile
        with tarfile.open(filepath, 'r:xz') as tf:
            return ('tar.xz', tf.getnames())
    if ext.endswith('.tar.lz4'):
        try:
            import lz4.frame
            import tarfile, io
            with open(filepath, 'rb') as fh:
                raw = lz4.frame.decompress(fh.read())
            with tarfile.open(fileobj=io.BytesIO(raw)) as tf:
                return ('tar.lz4', tf.getnames())
        except ImportError:
            return ('tar.lz4', [os.path.basename(filepath)])
    if ext.endswith('.tar'):
        import tarfile
        with tarfile.open(filepath, 'r:') as tf:
            return ('tar', tf.getnames())
    if ext.endswith('.zst') or ext.endswith('.zstd'):
        return ('zstd', [os.path.basename(filepath).removesuffix('.zst').removesuffix('.zstd')])
    if ext.endswith('.gz'):
        return ('gz', [os.path.basename(filepath)[:-3]])
    if ext.endswith('.bz2'):
        return ('bz2', [os.path.basename(filepath)[:-4]])
    if ext.endswith('.xz'):
        return ('xz', [os.path.basename(filepath)[:-3]])
    if ext.endswith('.lz4'):
        return ('lz4', [os.path.basename(filepath)[:-4]])
    if ext.endswith('.zip') or ext.endswith('.jar') or ext.endswith('.apk'):
        with zipfile.ZipFile(filepath, 'r') as z:
            return ('zip', z.namelist())
    if ext.endswith('.rar'):
        with _archive_backend('rarfile').RarFile(filepath) as r:
            return ('rar', r.namelist())
    if ext.endswith('.7z'):
        with _archive_backend('py7zr').SevenZipFile(filepath, mode='r') as z:
            return ('7z', z.getnames())
    return (None, [])

class ArchiveReader:
    def __init__(self,path):
        self.filepath=path
        self.archive_type,self.members=_detect_archive_type(path)
        self.archive=zipfile.ZipFile(path) if self.archive_type=="zip" else rarfile.RarFile(path) if self.archive_type=="rar" else None
        self.pac_listing=None
    def close(self):
        if self.archive is not None:self.archive.close()
    def __enter__(self):return self
    def __exit__(self,*args):self.close()

    def extract_selection_to(self,selected,target_dir):
        """Keep selected folders together, including folders with one child."""
        from core.archive_safety import validate_archive_member_names
        selected=[path.replace('\\','/').rstrip('/') for path in selected]
        validate_archive_member_names(selected)
        selected=[path for path in selected if not any(path.startswith(other+'/') for other in selected if other!=path)]
        members=[path for path in self.members if not path.endswith('/')]
        outputs=[]
        for path in selected:
            exact=path in members
            children=[path] if exact else [member for member in members if member.startswith(path+'/')]
            if not children:continue
            validate_archive_member_names(children)
            root_name=os.path.basename(path)
            output=os.path.join(target_dir,root_name)
            if not exact:os.makedirs(output,exist_ok=True)
            for member in children:
                target=output if exact else os.path.join(output,*member[len(path)+1:].split('/'))
                os.makedirs(os.path.dirname(target),exist_ok=True)
                if self.archive_type=='pac':
                    entry=self._pac_entry_by_path(member)
                    if entry is None or not entry.is_file:continue
                    self._extract_pac_entry_to(entry,target)
                else:
                    with open(target,'wb') as stream:stream.write(self._read_member(member))
            outputs.append(output)
        return outputs

    def _pac_entries(self):
        if self.pac_listing is None:
            from core.pac_lister import list_pac
            self.pac_listing = list_pac(self.filepath)
        return self.pac_listing.entries

    def _pac_entry_by_path(self, internal_path: str):
        for entry in self._pac_entries():
            if entry.archive_path == internal_path:
                return entry
        return None

    def _extract_pac_entry_to(self, entry, out_path: str):
        if not entry.is_file:
            return None
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        remaining = entry.size
        chunk_size = 1024 * 1024
        with open(self.filepath, 'rb') as src, open(out_path, 'wb') as dst:
            src.seek(entry.data_offset)
            while remaining > 0:
                chunk = src.read(min(chunk_size, remaining))
                if not chunk:
                    raise RuntimeError(f'PAC payload ended early: {entry.file_name}')
                dst.write(chunk)
                remaining -= len(chunk)
        return out_path

    def _read_member(self, internal_path: str) -> bytes:
        at = self.archive_type
        if at == 'zip':
            return self.archive.read(internal_path)
        if at == 'rar':
            return self.archive.read(internal_path)
        if at == '7z':
            if py7zr is None:
                raise RuntimeError('py7zr not installed')
            import tempfile
            from core.archive_safety import validate_archive_member_names
            validate_archive_member_names([internal_path])
            with tempfile.TemporaryDirectory(prefix='rak-archive-member-') as temporary:
                with py7zr.SevenZipFile(self.filepath,mode='r') as archive:archive.extract(path=temporary,targets=[internal_path])
                with open(os.path.join(temporary,*internal_path.split('/')),'rb') as member:return member.read()
        if at in ('tar', 'tar.gz', 'tar.bz2', 'tar.xz'):
            import tarfile
            mode_map = {'tar': 'r:', 'tar.gz': 'r:gz', 'tar.bz2': 'r:bz2', 'tar.xz': 'r:xz'}
            with tarfile.open(self.filepath, mode_map[at]) as tf:
                m = tf.getmember(internal_path)
                f = tf.extractfile(m)
                if f is None:
                    raise RuntimeError(f'tar: cannot extract: {internal_path}')
                return f.read()
        if at == 'tar.lz4':
            import lz4.frame, tarfile, io
            with open(self.filepath, 'rb') as fh:
                raw = lz4.frame.decompress(fh.read())
            with tarfile.open(fileobj=io.BytesIO(raw)) as tf:
                m = tf.getmember(internal_path)
                f = tf.extractfile(m)
                if f is None:
                    raise RuntimeError(f'tar.lz4: cannot extract: {internal_path}')
                return f.read()
        if at == 'zstd':
            import zstandard as zstd
            with open(self.filepath,'rb') as fh,zstd.ZstdDecompressor().stream_reader(fh) as reader:return reader.read()
        if at == 'gz':
            import gzip
            with gzip.open(self.filepath, 'rb') as fh:
                return fh.read()
        if at == 'bz2':
            import bz2
            with bz2.open(self.filepath, 'rb') as fh:
                return fh.read()
        if at == 'xz':
            import lzma
            with lzma.open(self.filepath, 'rb') as fh:
                return fh.read()
        if at == 'lz4':
            import lz4.frame
            with open(self.filepath, 'rb') as fh:
                return lz4.frame.decompress(fh.read())
        raise RuntimeError(f'Unsupported archive type: {at}')

    def extract_one_to(self, internal_path: str, target_dir: str):
        from core.archive_safety import validate_archive_member_names
        os.makedirs(target_dir, exist_ok=True)
        members_to_extract = self._resolve_members(internal_path)
        if not members_to_extract:
            return None
        validate_archive_member_names(members_to_extract)
        if self.archive_type == 'pac':
            last_path = None
            for member_path in members_to_extract:
                entry = self._pac_entry_by_path(member_path)
                if entry is None or not entry.is_file:
                    continue
                out_path = os.path.join(target_dir, entry.file_name)
                last_path = self._extract_pac_entry_to(entry, out_path)
            return last_path
        last_path = None
        for member_path in members_to_extract:
            if len(members_to_extract) == 1:
                filename = os.path.basename(member_path) or os.path.basename(self.filepath)
                out_path = os.path.join(target_dir, filename)
            else:
                rel = member_path
                if internal_path and member_path.startswith(internal_path):
                    rel = member_path[len(internal_path):].lstrip('/')
                    folder_name = internal_path.rstrip('/').split('/')[-1]
                    rel = os.path.join(folder_name, rel)
                out_path = os.path.join(target_dir, rel.replace('/', os.sep))
            os.makedirs(os.path.dirname(out_path), exist_ok=True)
            try:
                data = self._read_member(member_path)
                with open(out_path, 'wb') as f:
                    f.write(data)
                last_path = out_path
            except Exception as e:
                raise RuntimeError(f"Cannot extract '{member_path}': {e}") from e
        return last_path

    def _resolve_members(self, internal_path: str):
        all_members = self._get_all_member_paths()
        if internal_path in all_members:
            return [internal_path]
        prefix = internal_path.rstrip('/') + '/'
        children = [m for m in all_members if m.startswith(prefix)]
        return children if children else [internal_path]

    def _get_all_member_paths(self):
        at = self.archive_type
        if at == 'zip':
            return [n for n in self.archive.namelist() if not n.endswith('/')]
        if at == 'pac':
            return [entry.archive_path for entry in self._pac_entries()]
        if at == 'rar' and self.archive:
            return [n for n in self.archive.namelist() if not n.endswith('/')]
        if at == '7z':
            if py7zr is None:
                return []
            with py7zr.SevenZipFile(self.filepath, mode='r') as z:
                return z.getnames()
        if at in ('tar', 'tar.gz', 'tar.bz2', 'tar.xz'):
            import tarfile
            mode_map = {'tar': 'r:', 'tar.gz': 'r:gz', 'tar.bz2': 'r:bz2', 'tar.xz': 'r:xz'}
            with tarfile.open(self.filepath, mode_map[at]) as tf:
                return [m.name for m in tf.getmembers() if m.isfile()]
        return []
