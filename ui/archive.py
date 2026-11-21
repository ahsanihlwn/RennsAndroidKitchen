# SPDX-License-Identifier: MPL-2.0
"""Archive listing independent of the former QWidget tree."""
import os
import shutil
import tarfile
import tempfile
import zipfile
from contextlib import contextmanager


@contextmanager
def _lz4_tar(path):
    from lz4.frame import open as open_lz4
    with tempfile.TemporaryFile() as expanded:
        with open_lz4(path,'rb') as source:shutil.copyfileobj(source,expanded,1024*1024)
        expanded.seek(0)
        with tarfile.open(fileobj=expanded,mode='r:') as archive:yield archive


def _single_file_compression(path):
    lower = os.fspath(path).lower()
    if lower.endswith(('.tar.gz', '.tgz', '.tar.bz2', '.tbz2', '.tar.xz', '.txz',
                       '.tar.zst', '.tar.zstd', '.tar.lz4')):
        return None
    extension = os.path.splitext(lower)[1]
    return extension if extension in ('.gz', '.bz2', '.xz', '.zst', '.zstd', '.lz4') else None


def list_archive(path):
    path = os.fspath(path)
    if path.lower().endswith('.tar.lz4'):
        with _lz4_tar(path) as source:
            return [{'name':item.name,'size':item.size,'folder':item.isdir()} for item in source]
    # An Android image can start with zero-filled blocks. TAR probing treats
    # those as an empty archive, even inside a valid compressed image.
    if _single_file_compression(path):
        return [{"name": os.path.splitext(os.path.basename(path))[0],
                 "size": os.path.getsize(path), "folder": False}]
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as source:
            return [{"name": item.filename, "size": item.file_size, "compressed": item.compress_size,
                     "folder": item.is_dir()} for item in source.infolist()]
    if tarfile.is_tarfile(path):
        with tarfile.open(path) as source:
            return [{"name": item.name, "size": item.size, "folder": item.isdir()} for item in source]
    if path.lower().endswith(".7z"):
        import py7zr
        with py7zr.SevenZipFile(path) as source:
            return [{"name": item.filename, "size": item.uncompressed, "folder": item.is_directory} for item in source.list()]
    if path.lower().endswith(".rar"):
        import rarfile
        with rarfile.RarFile(path) as source:
            return [{"name": item.filename, "size": item.file_size, "folder": item.isdir()} for item in source.infolist()]
    if path.lower().endswith(".pac"):
        from core.pac_lister import list_pac
        return [{"name": entry.archive_path, "size": entry.size} for entry in list_pac(path).entries]
    raise ValueError('Unsupported archive: '+path)


def extract_archive(path,destination,*,members=None):
    path, destination = os.fspath(path), os.fspath(destination)
    from core.archive_safety import validate_archive_member_names
    rows=list_archive(path)
    validate_archive_member_names(row['name'] for row in rows)
    selected=None
    if members:
        validate_archive_member_names(members)
        selected=set()
        for requested in members:
            requested=requested.replace('\\','/').rstrip('/')
            matches=[row['name'] for row in rows if row['name'].replace('\\','/').rstrip('/')==requested or row['name'].replace('\\','/').startswith(requested+'/')]
            if not matches:raise FileNotFoundError('Archive member: '+requested)
            selected.update(matches)
    os.makedirs(destination,exist_ok=True)
    compression = _single_file_compression(path)
    if compression:
        import gzip, bz2, lzma
        openers = {'.gz': gzip.open, '.bz2': bz2.open, '.xz': lzma.open}
        if compression in ('.zst', '.zstd'):
            # ZstdFile streams bounded chunks and rejects truncated frames.
            from compression.zstd import open as open_zstd
            opener = open_zstd
        elif compression == '.lz4':
            from lz4.frame import open as open_lz4
            opener = open_lz4
        else:
            opener = openers[compression]
        output = os.path.join(destination, rows[0]['name'])
        if os.path.normcase(os.path.realpath(output)) == os.path.normcase(os.path.realpath(path)):
            raise ValueError('Archive output overlaps source')
        temporary = None
        try:
            with opener(path, 'rb') as source, tempfile.NamedTemporaryFile(
                    mode='wb', dir=destination, prefix='.rak_extract-', suffix='.part', delete=False) as target:
                temporary = target.name
                shutil.copyfileobj(source, target, length=1024 * 1024)
            os.replace(temporary, output)
        finally:
            if temporary is not None and os.path.exists(temporary):
                os.remove(temporary)
    elif path.lower().endswith('.tar.lz4'):
        with _lz4_tar(path) as source:source.extractall(destination,members=[item for item in source if selected is None or item.name in selected],filter='data')
    elif zipfile.is_zipfile(path):
        from core.archive_safety import safe_extract_zip
        safe_extract_zip(path,destination,members=selected)
    elif tarfile.is_tarfile(path):
        with tarfile.open(path) as source:source.extractall(destination,members=[item for item in source if selected is None or item.name in selected],filter='data')
    elif path.lower().endswith('.7z'):
        import py7zr
        with py7zr.SevenZipFile(path) as source:
            if selected is None:source.extractall(destination)
            else:source.extract(path=destination,targets=sorted(selected))
    elif path.lower().endswith('.rar'):
        import rarfile
        with rarfile.RarFile(path) as source:source.extractall(destination,members=selected)
    elif path.lower().endswith('.pac'):
        if os.environ.get("RAK_NATIVE_BACKEND", "auto").lower() != "python":
            from core.native_backend import native_module_path, pac_extract

            if native_module_path("pac_extract_json") is not None:
                pac_extract(path, destination, selected=sorted(selected) if selected is not None else None)
                return True, destination
        from core.archive_document import ArchiveReader
        with ArchiveReader(path) as source:
            for entry in source._pac_entries():
                if entry.is_file and (selected is None or entry.archive_path in selected):source._extract_pac_entry_to(entry,os.path.join(destination,entry.archive_path))
    else:
        raise ValueError('Unsupported archive: '+path)
    return True,destination
