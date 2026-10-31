# SPDX-License-Identifier: MPL-2.0
"""Describe usable Kitchen operations from logical workspace paths."""
import os
import shlex


def unpack_workflow(path,kind):
    parent=os.path.dirname(path)
    if kind in ('BR','DAT','DAT.BR'):
        suffix='.new.dat.br' if kind=='DAT.BR' else '.new.dat' if kind=='DAT' else '.br'
        image=path[:-len(suffix)]+'.img' if kind in ('DAT','DAT.BR') else path[:-3]
        result={'result_kind':'image','outputs':[image],
                'next_argv':['rak','inspect',image,'--json'],
                'note':'This step produces IMG, not an unpacked filesystem folder. Inspect the IMG, then unpack or mount that supported filesystem image.'}
        if kind in ('DAT','DAT.BR'):
            transfer=path[:-len(suffix)]+'.transfer.list'
            result['requires']=[{'path':transfer,'exists':os.path.isfile(transfer)}]
        return result
    if kind in ('PAYLOAD','SUPER'):
        return {'result_kind':'partition_images','output_directory':parent,'output_pattern':'<partition>.img',
                'note':'Extract partition IMG files first. Inspect each resulting IMG before unpacking or mounting it; this container is not mountable.'}
    if kind=='SPARSE':
        return {'result_kind':'raw_image','outputs':[path],'replaces_input':True,
                'next_argv':['rak','inspect',path,'--json'],
                'note':'The default unpack action unsparses this file in place. Inspect its raw type again: it may be a filesystem or SUPER image.'}
    if kind in ('EROFS','EXT2','EXT3','EXT4','BOOT','VNDRBOOT'):
        folder=os.path.splitext(path)[0]
        return {'result_kind':'boot_directory' if kind in ('BOOT','VNDRBOOT') else 'filesystem_directory',
                'outputs':[folder],'metadata_directory':os.path.join(parent,'config'),
                'next_argv':['rak','repack',folder],'repack_output':folder+'_new.img',
                'note':'Preserve Kitchen metadata. Repack this extracted root to IMG; do not zip it to rebuild a partition.'}
    return None


def describe(cli,path,*,follow=False):
    files,_=cli.files(path);physical,origin=files.view.resolve(path)
    if not physical:raise FileNotFoundError(path)
    from core.file_detection import identify
    identity=identify(files,path)
    if identity['symlink']:
        link=identity['symlink']
        if follow:
            if not link['exists']:raise ValueError('Cannot follow symlink: '+link['status'])
            result=describe(cli,link['resolved_path'])
            return {**result,'path':path,'resolved_path':link['resolved_path'],'symlink':link,'symlinks':files.view.link_chain(path)}
        operations=[{'action':'readlink','argv':['rak','readlink',path],'command':shlex.join(['rak','readlink',path])}]
        if link['is_dir']:operations.append({'action':'list','argv':['rak','ls','-la',path],'command':shlex.join(['rak','ls','-la',path])})
        if link['exists']:operations.append({'action':'inspect_target','argv':['rak','inspect','-L',path,'--json'],'command':shlex.join(['rak','inspect','-L',path,'--json'])})
        return {'path':path,**identity,'symlinks':files.view.link_chain(path),
                'behaves_as':'directory' if link['is_dir'] else 'file' if link['is_file'] else 'unavailable',
                'mountable':False,'unpack_result':None,'operations':operations,'next':operations[0]['command']}
    resolved=files.view.canonical(path)
    if os.path.normcase(resolved)!=os.path.normcase(path):
        return {**describe(cli,resolved),'path':path,'resolved_path':resolved,'symlinks':files.view.link_chain(path)}
    folder=os.path.isdir(physical);lower=path.lower();kind='directory' if folder else 'file'
    from core.file_types import ARCHIVE_EXT
    from core.rak_neobranch_view import source_image_branch
    branch=None if path==files.base else files.branch(path)[3]
    blocked=source_image_branch(path)
    if folder:
        if os.path.isfile(os.path.join(physical,'apktool.yml')):kind='apktool-directory'
        else:kind=cli.badge(path) or kind
    else:kind=identity['kind']
    operations=[]
    workflow=None if folder else unpack_workflow(path,kind)
    output_branch=None
    if workflow:
        for output in workflow.get('outputs',[]):
            output_branch=source_image_branch(output)
            if not output_branch:
                try:output_branch=files.branch(output)[3]
                except ValueError:pass
            if output_branch:break
    supported_archive=kind=='archive' and not lower.endswith(('.iso','.lz'))
    decompiled=False
    if kind=='APK':
        from core.apktool_tool import is_decompiled
        decompiled=is_decompiled(files.base,path)
    def add(action,args,description,**extra):
        argv=['rak',*args]
        operations.append({'action':action,'argv':argv,'command':shlex.join(argv),'description':description,**extra})
    if blocked:
        root=os.path.splitext(path)[0]
        if not files.view.resolve(root)[0] and root.endswith(('_a','_b')):root=root[:-2]
        local,_=files.view.resolve(root)
        if local and os.path.isdir(local) and cli.badge(root) in ('EROFS','EXT2','EXT3','EXT4','BOOT','VNDRBOOT'):
            add('repack',['repack',root],'Rebuild the active partition before operating on its Source image')
    if not folder and (supported_archive or kind in ('APK','PAYLOAD','SUPER','SPARSE','EROFS','EXT2','EXT3','EXT4','BOOT','VNDRBOOT','DAT','DAT.BR','BR')) and not branch and not blocked and not output_branch:
        add('unpack',['unpack',path],'Extract/decompile using automatic format detection')
    if (folder and kind in ('EROFS','EXT2','EXT3','EXT4','BOOT','VNDRBOOT','apktool-directory')) or (decompiled and not branch):
        add('repack',['repack',path],'Rebuild using the existing format and metadata')
    can_mount=not folder and kind in ('EROFS','EXT2','EXT3','EXT4')
    if not folder and kind=='SPARSE':
        from core import imgfs
        try:can_mount=imgfs.detect_fs(physical) in ('EXT','EROFS')
        except (OSError,ValueError):can_mount=False
    if can_mount and not blocked:
        add('mount',['mount',path],'Browse the filesystem IMG directly; this does not extract a folder',requires_gui=True,read_only=kind=='SPARSE')
    if folder and kind in ('EROFS','EXT2','EXT3','EXT4'):
        for suffix,label in (('.img','image'),('.img.br','brotli'),('.new.dat','new_dat'),('.new.dat.br','dat_br')):
            stem=path+'_new';output=stem+suffix;index=2
            while os.path.lexists(output):output=stem+'_'+str(index)+suffix;index+=1
            add('repack_'+label,['repack',path,'-o',output],'Rebuild '+kind+' using Kitchen metadata, then export '+suffix,output=output)
    if supported_archive:add('list',['archive','list',path,'--json'],'Read archive members')
    if kind in ('PAYLOAD','SUPER'):add('list',[kind.lower(),'list',path,'--json'],'Read available partitions')
    if folder:
        if kind=='directory' and os.path.normcase(path)!=os.path.normcase(files.base):
            stem=os.path.join(files.base,os.path.relpath(path,files.base).replace(os.sep,'_')) if branch else path
            destination=stem+'_new.zip';index=2
            while os.path.lexists(destination):destination=stem+'_new_'+str(index)+'.zip';index+=1
            add('pack',['repack',path,'-o',destination],'Build a ZIP archive; output extension chooses the archive format')
        add('list',['ls','-la',path],'List the merged logical directory')
    elif kind=='file':
        from core.imgfs import is_text_filename
        if is_text_filename(path):
            add('read',['cat',path],'Read the complete on-disk logical text file')
            if getattr(getattr(cli,'session',None),'_agent',None):add('read_buffer',['ui','read',path],'Read the current editor buffer or logical file, including its revision',requires_gui=True)
    return {'path':path,**identity,'symlinks':files.view.link_chain(path),
            'resolved_path':files.view.canonical(path),'kind':kind,'origin':origin,'branch':branch,'source_image_blocked_by_branch':blocked or None,
            'mountable':can_mount and not bool(blocked),'unpack_result':workflow,'unpack_output_blocked_by_branch':output_branch or None,
            'default_repack_output':path+'_new.img' if folder and kind in ('EROFS','EXT2','EXT3','EXT4','BOOT','VNDRBOOT') else None,
            'verification':{'stat_argv':['rak','stat',path,'--json'],**({'sha256sum_argv':['rak','sha256sum',path]} if not folder else {})},
            'operations':operations,'next':operations[0]['command'] if operations else None,
            'unsupported':('ISO/lzip extraction is not implemented' if lower.endswith(('.iso','.lz')) else None)}
