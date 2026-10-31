# SPDX-License-Identifier: MPL-2.0
"""Library operations for the Kitchen CLI. No UI or implicit overwrites."""
from contextlib import contextmanager
import json
import os
import re
import shutil
import tempfile
import zipfile

from core.command_context import OperationError


def render(result, as_json=False):
    if as_json:return json.dumps(result, ensure_ascii=False)
    return json.dumps(result, ensure_ascii=False, indent=2)


def _contained(path, root):
    try:return os.path.commonpath((os.path.normcase(os.path.realpath(path)), os.path.normcase(os.path.realpath(root)))) == os.path.normcase(os.path.realpath(root))
    except ValueError:return False


def publish_tree(staging, destination, validate_output=None):
    """Preflight the entire staged output before publishing any member."""
    paths=[]
    for folder, dirs, files in os.walk(staging):
        for name in dirs+files:
            source=os.path.join(folder,name)
            target=os.path.join(destination,os.path.relpath(source,staging))
            if validate_output:validate_output(target)
            if os.path.islink(source) or not _contained(target,destination):raise ValueError('Unsafe extraction path: '+target)
            if os.path.lexists(target) and (not os.path.isdir(source) or not os.path.isdir(target)):
                raise FileExistsError(target)
            paths.append((source,target))
    os.makedirs(destination,exist_ok=True)
    outputs=[]
    for source,target in paths:
        if os.path.isdir(source):os.makedirs(target,exist_ok=True)
        else:
            # Exclusive creation also protects against a concurrent writer.
            with open(source,'rb') as src, open(target,'xb') as dst:shutil.copyfileobj(src,dst,1024*1024)
            outputs.append(target)
    return outputs


@contextmanager
def payload_file(path):
    if not zipfile.is_zipfile(path):
        yield path
        return
    with zipfile.ZipFile(path) as archive:
        candidates=[info for info in archive.infolist() if not info.is_dir() and info.filename.replace('\\','/').split('/')[-1]=='payload.bin']
        if len(candidates)!=1:raise ValueError('OTA ZIP must contain exactly one payload.bin')
        with tempfile.TemporaryDirectory(prefix='rak-payload-') as folder:
            local=os.path.join(folder,'payload.bin')
            with archive.open(candidates[0]) as source,open(local,'wb') as target:shutil.copyfileobj(source,target,1024*1024)
            yield local


def container(path, kind, action, destination=None, partitions=None, validate_output=None, progress=None):
    from core import partition_viewer
    def run(source):
        available=partition_viewer.payload_partitions(source) if kind=='payload' else partition_viewer.super_info(source)['partitions']
        if any(not re.fullmatch(r'[A-Za-z0-9_.-]+',name) or name in ('.','..') or name.endswith(('.', ' ')) for name in available):
            raise ValueError('Invalid partition name in container')
        if action=='list':return {'kind':kind,'path':path,'partitions':available}
        selected=list(dict.fromkeys(partitions or available))
        if not selected:raise ValueError('Container has no partitions')
        unknown=set(selected)-set(available)
        if unknown:raise ValueError('Unknown partition(s): '+', '.join(sorted(unknown)))
        for name in selected:
            if validate_output:validate_output(os.path.join(destination,name+'.img'))
            if os.path.lexists(os.path.join(destination,name+'.img')):raise FileExistsError(os.path.join(destination,name+'.img'))
        with tempfile.TemporaryDirectory(prefix='rak-extract-') as staging:
            extract=partition_viewer.extract_payload if kind=='payload' else partition_viewer.extract_super
            for name in selected:
                if progress:progress('Extracting '+name+'\n')
                extract(source,name,staging)
                if not os.path.isfile(os.path.join(staging,name+'.img')):
                    raise OperationError('Extraction produced no image: '+name)
            # Internal service metadata belongs beside the images, as in the GUI.
            outputs=publish_tree(staging,destination,validate_output)
        return {'kind':kind,'path':path,'partitions':selected,'outputs':outputs}
    if kind=='payload':
        with payload_file(path) as source:return run(source)
    return run(path)


def archive(path, action, destination=None, members=None, validate_output=None):
    from ui.archive import list_archive, extract_archive
    if action=='list':return {'kind':'archive','path':path,'members':list_archive(path)}
    if action=='pack':return pack(path,destination)
    with tempfile.TemporaryDirectory(prefix='rak-archive-') as staging:
        extract_archive(path,staging,members=members)
        outputs=publish_tree(staging,destination,validate_output)
    return {'kind':'archive','path':path,'outputs':outputs}


def pack(path,destination):
    if not os.path.isdir(path):raise NotADirectoryError(path)
    if _contained(destination,path):raise ValueError('Archive output must be outside its input folder')
    if os.path.lexists(destination):raise FileExistsError(destination)
    lower=destination.lower()
    formats={'.zip':'zip','.tar':'tar','.tar.gz':'gztar','.tgz':'gztar','.tar.bz2':'bztar','.tar.xz':'xztar'}
    extension=next((ext for ext in sorted(formats,key=len,reverse=True) if lower.endswith(ext)),None)
    if not extension and not lower.endswith('.7z'):raise ValueError('Supported pack formats: ZIP, 7z, TAR, TAR.GZ, TAR.BZ2, TAR.XZ')
    with tempfile.TemporaryDirectory(prefix='rak-pack-') as staging:
        if lower.endswith('.7z'):
            import py7zr
            result=os.path.join(staging,'output.7z')
            with py7zr.SevenZipFile(result,'w') as target:
                for name in os.listdir(path):target.writeall(os.path.join(path,name),arcname=name)
        else:result=shutil.make_archive(os.path.join(staging,'output'),formats[extension],root_dir=path)
        os.makedirs(os.path.dirname(destination),exist_ok=True)
        with open(result,'rb') as source,open(destination,'xb') as target:shutil.copyfileobj(source,target,1024*1024)
    return {'kind':'archive','path':path,'outputs':[destination]}
