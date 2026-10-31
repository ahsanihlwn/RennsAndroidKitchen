# SPDX-License-Identifier: MPL-2.0
"""Revision-checked background text edits through the logical project view."""
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile
from core.editor_document import _decode_bytes,_detect_eol,encode_text_meta

MAX_BYTES=262144


def read(files,path,*,max_bytes=MAX_BYTES):
    resolved=files.view.canonical(path)
    logical,relative,root,branch=files.branch(resolved)
    physical,_=files.view.resolve_following(path)
    if not physical or not os.path.isfile(physical):raise FileNotFoundError(path)
    with open(physical,'rb') as stream:raw=stream.read() if max_bytes is None else stream.read(max_bytes+1)
    if max_bytes is not None and len(raw)>max_bytes:raise ValueError('Background text editing is limited to 256 KiB; use rak sed for larger unopened text files')
    text,encoding,bom=_decode_bytes(raw)
    if '\x00' in text:raise ValueError('This file is binary; use the binary tools')
    eol=_detect_eol(text);text=text.replace('\r\n','\n').replace('\r','\n')
    identity={'tree_base':os.path.normcase(files.base),'fs_root':root,'branch_name':branch,
              'physical':os.path.normcase(os.path.abspath(physical))}
    revision='disk:'+hashlib.sha256(json.dumps(identity,sort_keys=True).encode()+b'\0'+raw).hexdigest()
    return {'path':path,'resolved_path':resolved,'symlinks':files.view.link_chain(path),
            'revision':revision,'content':text,'unsaved':False,'read_only':not os.access(physical,os.W_OK),
            'virtual':False,'background':True,'encoding':encoding,'eol':eol,'bom':bom,'truncated':False,
            'branch':{'tree_base':files.base,'fs_root':root,'branch_name':branch,'source_path':logical}},physical


def apply(files,path,revision,content,*,max_bytes=MAX_BYTES,before_save=None):
    logical,relative,root,branch=files.branch(files.view.canonical(path))
    with files.view.manager.repack_transaction(root):
        current,physical=read(files,path,max_bytes=max_bytes)
        if current['revision']!=revision:raise ValueError('Document revision conflict. Read the current file first.')
        if current['read_only']:raise ValueError('This file is read-only')
        target=files.write_path(logical)
        data=encode_text_meta(content,current['encoding'],current['eol'],current['bom'])
        if max_bytes is not None and len(data)>max_bytes:raise ValueError('Background text editing is limited to 256 KiB')
        Path(target).parent.mkdir(parents=True,exist_ok=True)
        descriptor,temporary=tempfile.mkstemp(prefix='.rak-agent-',dir=os.path.dirname(target))
        try:
            with os.fdopen(descriptor,'wb') as stream:stream.write(data);stream.flush();os.fsync(stream.fileno())
            os.chmod(temporary,stat.S_IMODE(os.stat(physical).st_mode))
            if read(files,path,max_bytes=max_bytes)[0]['revision']!=revision:raise ValueError('Document changed before saving. Read it again.')
            if files.view.canonical(path)!=logical:raise ValueError('Symlink target changed before saving')
            if files.branch(logical)[3]!=branch:raise ValueError('Active branch changed before saving')
            if before_save:before_save()
            from main_ext.class_NeoBranchManager import _retry_fs_op
            _retry_fs_op(lambda:os.replace(temporary,target),target)
            files._record(logical,target)
        finally:
            if os.path.exists(temporary):os.remove(temporary)
    return read(files,path,max_bytes=max_bytes)[0]
