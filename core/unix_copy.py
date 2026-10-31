# SPDX-License-Identifier: MPL-2.0
"""cp directory merging over the managed logical filesystem and Android metadata."""
import errno
import os


def copy(source_files,source,target_files,target,*,recursive=False,no_clobber=False,preserve=False,protect=lambda path:None,contents=False):
    physical,_=source_files.view.resolve(source)
    if not physical:raise FileNotFoundError(source)
    if os.path.normcase(source)==os.path.normcase(target):raise ValueError('cp: source and destination are the same file: '+source)
    folder=os.path.isdir(physical)
    if folder and not recursive:raise ValueError('cp: omitting directory; use -r or -a: '+source)
    if folder:
        try:inside=os.path.commonpath((os.path.normcase(target),os.path.normcase(source)))==os.path.normcase(source)
        except ValueError:inside=False
        if inside:raise ValueError('cp: cannot copy a directory into itself: '+target)
    existing,_=target_files.view.resolve(target)
    if folder and existing and not os.path.isdir(existing):raise NotADirectoryError(target)
    if not folder and existing and os.path.isdir(existing):raise IsADirectoryError(target)
    if not folder and existing and no_clobber:return []
    if folder and (contents or existing):
        if not existing:
            protect(target);target_files.create(os.path.dirname(target),os.path.basename(target),folder=True)
        output=[]
        for entry in source_files.view.list_dir(source):
            output.extend(copy(source_files,entry['path'],target_files,os.path.join(target,entry['name']),
                               recursive=True,no_clobber=no_clobber,preserve=preserve,protect=protect))
        if preserve:
            source_engine=source_files.engine(source);target_engine=target_files.engine(target)
            target_files._copy_metadata(source_engine,target_engine,os.path.relpath(source,source_files.base).replace(os.sep,'/'),
                                        os.path.relpath(target,target_files.base).replace(os.sep,'/'),exact=True,replace_existing=True)
        return output
    protect(target)
    return [target_files.transfer(source_files,source,os.path.dirname(target),name=os.path.basename(target),replace=bool(existing),preserve=preserve)]


def move(source_files,source,target_files,target,*,no_clobber=False,protect=lambda path:None):
    physical,_=source_files.view.resolve(source)
    if not physical:raise FileNotFoundError(source)
    existing,_=target_files.view.resolve(target)
    if existing and no_clobber:return []
    protect(source);protect(target)
    if existing and os.path.isdir(existing):
        if not os.path.isdir(physical):raise IsADirectoryError(target)
        if target_files.view.list_dir(target):raise OSError(errno.ENOTEMPTY,'Directory not empty',target)
    elif existing and os.path.isdir(physical):raise NotADirectoryError(target)
    return [target_files.transfer(source_files,source,os.path.dirname(target),name=os.path.basename(target),move=True,replace=bool(existing),preserve=True)]
