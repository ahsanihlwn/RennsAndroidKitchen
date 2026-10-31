# SPDX-License-Identifier: MPL-2.0
"""CLI adapter for native directory/session APIs, without widget tree methods."""
import os
import tempfile
from core.workspace_files import WorkspaceFiles,validate_name
from core.command_context import DispatchedCommand, OperationError, failure_from_exception


class FileCommandOutput(str):
    """Keep display text separate from the worker's actual logical outputs."""
    def __new__(cls,text,paths=()):
        value=super().__new__(cls,text);value.paths=tuple(paths);return value


class WorkspaceCli:
    def __init__(self,session):self.session=session;self.last_task=None
    def files(self,path):
        for directory in self.session.directories:
            if not directory.base:continue
            try:
                files=WorkspaceFiles(directory.base);files.logical(path,allow_root=True)
                return files,directory
            except ValueError:continue
        raise ValueError('Path is outside the open project')
    def badge(self,path):
        from core.file_detection import identify
        files,_=self.files(path)
        try:kind=identify(files,path)['kind']
        except FileNotFoundError:return ''
        return '' if kind in ('file','directory','archive') else kind
    def _open_document(self,path,*,follow_final=True,descendants=False):
        files,_=self.files(path)
        key=os.path.normcase(files.view.canonical(path,follow_final=follow_final))
        for row in getattr(self.session,'_tab_data',[]):
            if row['path'].startswith('mount://'):continue
            try:other,_=self.files(row['path'])
            except ValueError:continue
            candidate=os.path.normcase(other.view.canonical(row['path']))
            if candidate==key or descendants and candidate.startswith(key+os.sep):return row
        return None
    def _enqueue(self,label,work):
        from ui.workspace_actions import operation
        def done(result):
            agent=getattr(self.session,'_agent',None)
            if agent and isinstance(result,str):
                if isinstance(result,FileCommandOutput):agent.activity.touch_many(result.paths,'write')
                elif '\n' not in result and '\r' not in result and os.path.isabs(result):agent.activity.touch(result,'write')
        self.last_task=operation(self.session,label,work,done)
        if self.last_task is None:return False,OperationError('Kitchen task was not accepted; the workspace may be closing')
        return True,DispatchedCommand('dispatched '+label,self.last_task)

    def android_metadata(self,path,column,value):
        files,_=self.files(path)
        def work():
            if not files.view.resolve(path)[0]:raise FileNotFoundError(path)
            files.edit_metadata(path,column,value)
            return path
        return self._enqueue('Android '+('SELinux' if column==1 else 'permissions'),work)
    def kitchen(self,options):
        """Dispatch explicit tool operations without triggering GUI prompts."""
        try:
            from core.kitchen_files import archive,container,render
            from core.terminal_execution import library_operation
            kind=options['namespace'];action=options['action'];path=options['path']
            files,_=self.files(path)
            def work():
                if files.view.link_info(path):raise ValueError('This input is a symlink. Use rak inspect -L PATH --json and operate on its resolved logical target.')
                physical,_=files.view.resolve(path)
                if not physical:raise FileNotFoundError(path)
                agent=getattr(self.session,'_agent',None)
                if agent:agent._protect_settings_path(path)
                if action!='list':
                    if action!='pack':self._tool_target(files,path)
                    if options.get('to'):
                        target_files,_=self.files(options['to'])
                        self._tool_target(target_files,options['to'])
                        if agent:agent._protect_settings_path(options['to'])
                def validate_output(target):
                    target_files,_=self.files(target)
                    self._tool_target(target_files,target)
                    if agent:agent._protect_settings_path(target)
                label='rak '+kind+' '+action+' '+path
                with library_operation(label) as output:
                    if action=='pack':
                        import tempfile
                        from core.project_view_work import _copy_view
                        from core.kitchen_files import pack
                        if os.path.commonpath((os.path.normcase(options['to']),os.path.normcase(path)))==os.path.normcase(path):
                            raise ValueError('Archive output must be outside its input folder')
                        with tempfile.TemporaryDirectory(prefix='rak-pack-view-') as staging:
                            _copy_view(files.view,path,staging)
                            result=pack(staging,options['to'])
                    else:
                        result=(archive(physical,action,options.get('to'),options.get('members'),validate_output) if kind=='archive' else
                                container(physical,kind,action,options.get('to'),options.get('partitions'),validate_output,output))
                    result['path']=path
                    text=render(result,options.get('json',False));output(text+'\n')
                    return FileCommandOutput(text,result.get('outputs',()))
            return self._enqueue('rak '+kind+' '+action+' '+path,work)
        except Exception as error:return False,failure_from_exception(error)

    @staticmethod
    def _tool_target(files,path):
        from core.rak_neobranch_view import source_image_branch
        path=files.view.canonical(path)
        if os.path.normcase(path)==os.path.normcase(files.base):return
        _logical,_relative,root,branch=files.branch(path)
        if (branch and not files.view.manager.is_physical_checkout(root)) or source_image_branch(path):
            raise ValueError('Tool output/input belongs to an active NeoBranch. Repack or switch to Source first; extract outside the active partition.')

    def unix(self,options):
        try:
            command=options['command'];paths=options.get('paths') or [options['path']]
            # Validate every logical operand before accepting the task.
            for path in paths:self.files(path)
            if command=='readlink':
                files,_=self.files(options['path']);link=files.view.link_info(options['path'])
                if options['canonicalize'] or options['canonicalize_existing']:
                    resolved=files.view.canonical(options['path'])
                    if options['canonicalize_existing'] and not files.view.resolve_following(options['path'])[0]:raise FileNotFoundError(options['path'])
                    parent,_=files.view.resolve_following(os.path.dirname(resolved))
                    if not parent or not os.path.isdir(parent):raise NotADirectoryError(os.path.dirname(resolved))
                    return True,resolved
                if not link:raise ValueError('Not a symlink: '+options['path'])
                return True,link['target']
            def work():
                output=[]
                def protected(path):
                    agent=getattr(self.session,'_agent',None)
                    if agent:agent._protect_settings_path(path)
                    if command in ('cp','mv','rm') and self._open_document(path,follow_final=False,descendants=True):
                        raise ValueError('This path has an open Kitchen document. Close it before replacing/moving/deleting it; use rak ui edit or sed -i for text edits.')
                if command in ('cp','mv'):
                    destination=paths[-1];target_files,_=self.files(destination)
                    destination_physical,_=target_files.view.resolve_following(destination)
                    directory=bool(destination_physical and os.path.isdir(destination_physical) and not options.get('no_target_directory'))
                    if len(paths)>2 and not directory:raise NotADirectoryError(destination)
                    from core.unix_copy import copy,move
                    for index,source in enumerate(paths[:-1]):
                        source_files,_=self.files(source);physical,_=source_files.view.resolve(source)
                        if not physical:raise FileNotFoundError(source)
                        contents=command=='cp' and options.get('contents',[False]*len(paths))[index]
                        if contents:source=source_files.view.canonical(source)
                        target=os.path.join(destination,os.path.basename(source)) if directory and not contents else destination
                        if command=='cp':output.extend(copy(source_files,source,target_files,target,recursive=options['recursive'] or options['archive'],
                                                            no_clobber=options['no_clobber'],preserve=options['preserve'] or options['archive'],protect=protected,contents=contents))
                        else:output.extend(move(source_files,source,target_files,target,no_clobber=options['no_clobber'],protect=protected))
                elif command=='ln':
                    path=options['path'];protected(path);files,_=self.files(path)
                    if files.view.resolve(path)[0]:raise FileExistsError(path)
                    from core.make_symlink import make_symlink
                    physical=files.write_path(path)
                    parent,_=files.view.resolve_following(os.path.dirname(path))
                    if not parent or not os.path.isdir(parent):raise NotADirectoryError(os.path.dirname(path))
                    os.makedirs(os.path.dirname(physical),exist_ok=True)
                    if not make_symlink(physical,options['target']):raise OperationError('Cannot create symlink')
                    files._record(path,physical);output.append(path)
                elif command=='sed':
                    from core.agent_documents import read,apply
                    from core.unix_tools import sed_transform
                    path=options['path'];files,_=self.files(path);protected(path)
                    def check_open():
                        if self._open_document(path):raise ValueError('This file is now open in Kitchen. Read its current buffer revision and use ui edit.')
                    check_open()
                    agent=getattr(self.session,'_agent',None)
                    if options['in_place'] and agent:
                        from core.agent_policy import settings
                        if not settings(self.session)['autosave']:raise ValueError('Agent autosave is disabled; sed -i requires saving')
                    document=read(files,path,max_bytes=None)[0]
                    content=sed_transform(options)(document['content'])
                    if not options['in_place']:return FileCommandOutput(content)
                    apply(files,path,document['revision'],content,max_bytes=None,before_save=check_open);output.append(path)
                else:
                    for path in paths:
                        protected(path);files,_=self.files(path);physical,_=files.view.resolve(path)
                        if command=='rm':
                            if not physical and options['force']:continue
                            if not physical:raise FileNotFoundError(path)
                            if not files.view.link_info(path) and os.path.isdir(physical) and not options['recursive']:raise IsADirectoryError(path)
                            output.append(files.delete(path) or path)
                        elif command=='mkdir':
                            if options['parents']:
                                missing=[];parent=path
                                while not files.view.resolve(parent)[0]:missing.append(parent);parent=os.path.dirname(parent)
                                for directory in reversed(missing):files.create(os.path.dirname(directory),os.path.basename(directory),folder=True)
                                if not os.path.isdir(files.view.resolve_following(path)[0]):raise NotADirectoryError(path)
                                output.append(path)
                            else:output.append(files.create(os.path.dirname(path),os.path.basename(path),folder=True))
                        elif command=='touch':
                            path=files.view.canonical(path);physical,_=files.view.resolve(path)
                            if not physical:
                                if options['no_create']:continue
                                output.append(files.create(os.path.dirname(path),os.path.basename(path)))
                            else:
                                import shutil
                                target=files.write_path(path)
                                if target!=physical:
                                    os.makedirs(os.path.dirname(target),exist_ok=True)
                                    if os.path.isdir(physical):os.makedirs(target,exist_ok=True)
                                    else:shutil.copy2(physical,target)
                                os.utime(target,None);files._record(path,target);output.append(path)
                        elif command=='chmod':
                            path=files.view.canonical(path)
                            if not physical:raise FileNotFoundError(path)
                            files.edit_metadata(path,2,options['mode']);output.append(path)
                return FileCommandOutput('\n'.join(output),output)
            return self._enqueue('rak '+command+' '+' '.join(paths),work)
        except Exception as error:return False,failure_from_exception(error)

    def repack_output(self,options):
        try:
            path=options['path'];output=options['output'];files,_=self.files(path)
            physical,_=files.view.resolve(path)
            if not physical or not os.path.isdir(physical):raise NotADirectoryError(path)
            target_files,_=self.files(output);self._tool_target(target_files,output)
            if os.path.lexists(output):raise FileExistsError(output)
            lower=output.lower()
            if lower.endswith(('.zip','.7z','.tar','.tgz','.tar.gz','.tar.bz2','.tar.xz')):
                return self.kitchen({'namespace':'archive','action':'pack','path':path,'to':output,'json':options.get('json',False)})
            formats=(('.new.dat.br','dat_br'),('.new.dat','new_dat'),('.img.br','brotli'),('.img','image'))
            suffix=next((kind for extension,kind in formats if lower.endswith(extension)),None)
            if suffix is None:raise ValueError('Output must be .img, .img.br, .new.dat, .new.dat.br or a supported archive')
            badge=self.badge(path)
            if badge in ('BOOT','VNDRBOOT'):
                if suffix!='image':raise ValueError('Boot/vendor_boot rebuilds produce .img; filesystem DAT/Brotli outputs do not apply to ramdisks')
                action='repack_boot'
            else:
                kind='erofs' if badge=='EROFS' else 'ext' if badge in ('EXT2','EXT3','EXT4') else None
                if kind is None:raise ValueError('This is not an unpacked filesystem root with Kitchen metadata. Use rak inspect on the folder first.')
                action='repack_'+kind+'_to_'+suffix
            return self.single(action,path,name=output)
        except Exception as error:return False,failure_from_exception(error)

    def single(self,action,path,name=None):
        try:
            from core.rak_actions import RC_CLI_ACTIONS
            if action not in RC_CLI_ACTIONS:raise ValueError('Unknown action: '+action)
            from core.agent_policy import UI_ACTIONS,require_ui
            if action in UI_ACTIONS:require_ui(self.session)
            path=os.path.abspath(path);files,directory=self.files(path);physical,_=files.view.resolve(path)
            agent=getattr(self.session,'_agent',None)
            if agent:
                agent._protect_settings_path(path)
                agent.activity.touch(path,action)
            if not physical:raise FileNotFoundError(path)
            if action in ('rename','rename_force'):
                name=validate_name(name)
                return self._enqueue('Rename '+os.path.basename(path),lambda:files.rename(path,name,force=action=='rename_force'))
            if action in ('new_file','new_folder'):
                name=validate_name(name);folder=path if os.path.isdir(physical) else os.path.dirname(path)
                return self._enqueue('Create '+name,lambda:files.create(folder,name,folder=action=='new_folder'))
            if action=='delete':return self._enqueue('Delete '+os.path.basename(path),lambda:files.delete(path))
            if action=='duplicate':return self._enqueue('Duplicate '+os.path.basename(path),lambda:files.duplicate(path))
            if action in ('copy','cut'):
                self.session._clipboard={'paths':[path],'base':files.base,'move':action=='cut'}
                return True,'staged '+action
            if action=='paste':
                clipboard=self.session._clipboard
                if not clipboard:return False,'Clipboard is empty'
                return self.transfer_many(clipboard['move'],clipboard['base'],clipboard['paths'],path)
            if action in ('unpack','repack'):
                if files.view.link_info(path):raise ValueError('This input is a symlink. Use rak inspect -L PATH --json and operate on its resolved logical target.')
                badge=self.badge(path)
                if action=='unpack':
                    from core.file_types import ARCHIVE_EXT
                    action={'EROFS':'unpack_erofs','EXT2':'unpack_ext','EXT3':'unpack_ext','EXT4':'unpack_ext','SPARSE':'convert_sparse','SUPER':'unpack_super','PAYLOAD':'unpack_payload','BOOT':'unpack_boot','VNDRBOOT':'unpack_boot','DAT':'unpack_dat_to_image','DAT.BR':'unpack_dat_br_to_image','BR':'unpack_br_to_image','APK':'apktool_decompile'}.get(badge,'')
                    if not action and path.lower().endswith(ARCHIVE_EXT):action='extract_to'
                    if not action:
                        from core.file_detection import identify
                        if identify(files,path)['kind']=='archive':action='extract_to'
                else:
                    if path.lower().endswith('.apk') and os.path.isfile(physical):action='apktool_build'
                    elif os.path.isdir(physical) and os.path.isfile(os.path.join(physical,'apktool.yml')):
                        from core.apktool_tool import APKTOOL_DEC_DIR
                        mirror_root=os.path.join(files.base,APKTOOL_DEC_DIR)
                        relative=os.path.relpath(path,mirror_root)
                        if relative=='..' or relative.startswith('..'+os.sep):raise ValueError('Use rak apk build <original.apk> for this apktool folder')
                        return self.single('apktool_build',os.path.join(files.base,relative))
                    else:action={'EROFS':'repack_erofs','EXT2':'repack_ext','EXT3':'repack_ext','EXT4':'repack_ext','BOOT':'repack_boot','VNDRBOOT':'repack_boot'}.get(badge,'')
                if not action:raise ValueError('Cannot determine a compatible kitchen action')
            if action in ('unpack_payload','extract_here','extract_to'):
                kind='payload' if action=='unpack_payload' else 'archive'
                destination=os.path.dirname(path)
                if action=='extract_to':destination=os.path.join(destination,os.path.splitext(os.path.basename(path))[0])
                return self.kitchen({'namespace':kind,'action':'extract','path':path,'to':destination})
            aliases={'unpack_sparse':'convert_sparse','open_in_explorer':'explorer','create_symlink':'symlink','inspect_bin':'openHex'}
            action=aliases.get(action,action)
            if action=='mount':
                if files.view.link_info(path):raise ValueError('This input is a symlink. Use rak inspect -L PATH --json and mount its supported resolved IMG target.')
                from core.rak_neobranch_view import source_image_branch
                if source_image_branch(path):raise ValueError('Repack the active NeoBranch before mounting its source image')
                badge=self.badge(path)
                if badge=='SPARSE':
                    from core import imgfs
                    supported=imgfs.detect_fs(physical) in ('EXT','EROFS')
                else:supported=badge in ('EROFS','EXT2','EXT3','EXT4')
                if not supported:raise ValueError('Mount requires an EXT/EROFS filesystem image. Convert BR/DAT or extract payload/SUPER to partition IMG first; use rak inspect for the next step.')
            if getattr(self.session,'headless',False) and action in ('mount','unmount','open_super','open_payload','symlink','neobranch','make_filesystem','android','find'):
                return False,'This action requires the kitchen window or explicit filesystem arguments'
            previous=self.session.active_tree
            try:
                self.session.active_tree=directory
                before=self.session._tasks._serial
                data={'path':path}
                if name and action.startswith('repack_'):data['output']=name
                self.session.dispatch(action,data)
                if self.session._tasks._serial>before:self.last_task='workspace:'+str(self.session._tasks._serial)
                return True,DispatchedCommand('dispatched '+action+' on '+os.path.basename(path),
                    self.last_task if self.session._tasks._serial>before else None)
            finally:self.session.active_tree=previous
        except Exception as error:return False,failure_from_exception(error)


    def batch(self,action,paths):
        if action in ('copy','cut') and paths:
            files,_=self.files(paths[0]);self.session._clipboard={'paths':paths,'base':files.base,'move':action=='cut'}
            return True,'staged '+action
        task_ids=[]
        for path in paths:
            ok,message=self.single(action,path)
            if not ok:return ok,message
            if getattr(message,'task_id',None):task_ids.append(message.task_id)
        return True,DispatchedCommand(f'dispatched {action} on {len(paths)} items',tuple(task_ids))
    def transfer_many(self,move,source_base,paths,destination):
        from core.workspace_transfer import plan_transfer
        target,_=self.files(destination)
        plan=plan_transfer(target.base,[(source_base,path) for path in paths],destination,move=move)
        if plan.conflicts:return False,'Destination already contains one or more items; rename them first'
        return self._enqueue('Move' if move else 'Copy',lambda:plan.execute({}))
    def transfer(self,mode,source,destination):
        try:
            files,_=self.files(source)
            if os.path.normcase(os.path.dirname(source))==os.path.normcase(destination):
                if mode=='move':return True,'move is a no-op: source is already in destination'
                return self.single('duplicate',source)
            return self.transfer_many(mode=='move',files.base,[source],destination)
        except Exception as error:return False,failure_from_exception(error)
    def make_filesystem(self,folder,kind,mount=None):
        try:
            files,_=self.files(folder);logical,relative=files.logical(folder)
            if '/' in relative or relative=='config' or not os.path.isdir(logical):raise ValueError('Select a partition root')
            if files.branch(folder)[3] and not files.view.manager.is_physical_checkout(relative):raise ValueError('Switch to Source before creating a filesystem')
            if kind.upper() not in ('EROFS','EXT4'):raise ValueError('Filesystem must be EROFS or EXT4')
            from core.make_filesystem import make_filesystem
            return self._enqueue('make-filesystem:'+folder,lambda:make_filesystem(folder,kind.upper(),mount or '/'+relative))
        except Exception as error:return False,failure_from_exception(error)
    def symlink(self,folder,name,target):
        try:
            files,_=self.files(folder);name=validate_name(name)
            if not target:raise ValueError('Symlink target is empty')
            logical=os.path.join(folder,name)
            if files.view.resolve(logical)[0]:raise FileExistsError(logical)
            def work():
                from core.make_symlink import make_symlink
                physical=files.write_path(logical)
                if not make_symlink(physical,target):raise OperationError('Cannot create symlink')
                files._record(logical,physical)
            return self._enqueue('Symlink '+name,work)
        except Exception as error:return False,failure_from_exception(error)
    def write(self,path,content):
        try:
            agent=getattr(self.session,'_agent',None)
            if agent:agent._protect_settings_path(path)
            # An open editor owns its buffer, including unsaved user changes.
            files,directory=self.files(path);resolved=files.view.canonical(path)
            if self._open_document(path):
                raise ValueError('This file is open in Kitchen. Use rak ui read and revision-checked ui edit; do not overwrite its buffer through disk.')
            logical,relative=files.logical(resolved)
            physical=files.write_path(logical)
            existed=bool(files.view.resolve(logical)[0])
            if os.path.isdir(physical):raise IsADirectoryError(path)
            parent,_=files.view.resolve_following(os.path.dirname(path))
            if not parent or not os.path.isdir(parent):raise NotADirectoryError(os.path.dirname(path))
            os.makedirs(os.path.dirname(physical),exist_ok=True)
            fd,temporary=tempfile.mkstemp(prefix='.rak-write-',dir=os.path.dirname(physical))
            try:
                with os.fdopen(fd,'w',encoding='utf-8',newline='\n') as stream:stream.write(content)
                from main_ext.class_NeoBranchManager import _retry_fs_op
                _retry_fs_op(lambda:os.replace(temporary,physical),physical)
            finally:
                if os.path.exists(temporary):os.remove(temporary)
            files._record(logical,physical)
            if not existed:files.engine(logical).apply(relative)
            directory.reloadMetadata()
            if agent:agent.activity.touch(path,'write')
            return True,'wrote '+os.path.basename(path)
        except Exception as error:return False,failure_from_exception(error)
    def replace(self,path,old,new,replace_all=False):
        try:
            if not old or old==new:raise ValueError('Replacement text must differ')
            agent=getattr(self.session,'_agent',None)
            if agent:agent._protect_settings_path(path)
            def guard():
                if self._open_document(path):raise ValueError('This file is open in Kitchen. Use rak ui read and revision-checked ui edit.')
            guard()
            from core.agent_documents import read,apply
            files,directory=self.files(path);document=read(files,path,max_bytes=None)[0];content=document['content']
            old=old.replace('\r\n','\n').replace('\r','\n');new=new.replace('\r\n','\n').replace('\r','\n')
            count=content.count(old)
            if not count:raise ValueError('old_text was not found')
            if count>1 and not replace_all:raise ValueError('old_text occurs more than once; set replace_all explicitly')
            apply(files,path,document['revision'],content.replace(old,new,-1 if replace_all else 1),max_bytes=None,before_save=guard)
            directory.reloadMetadata()
            if agent:agent.activity.touch(path,'write')
            return True,'replaced '+str(count if replace_all else 1)+' occurrence(s) in '+os.path.basename(path)
        except Exception as error:return False,failure_from_exception(error)
    def branch(self,action,folder,name,color='blue',cwd=None):
        try:
            path=os.path.abspath(os.path.join(cwd or os.getcwd(),folder))
            files,_=self.files(path);logical,root=files.logical(path)
            if '/' in root or root in ('config','neobranch'):raise ValueError('Select a partition root')
            coordinator=self.session.branches;coordinator._context=(files.base,root)
            if not coordinator._prepare_switch():return False,'Save edited tabs and wait for pending tasks before changing branches'
            manager,_=coordinator._manager()
            def work():
                from ui.workspace_branches import run_branch_operation
                target=None if action=='switch' and name.lower() in ('source','none','--source','0') else name
                run_branch_operation(manager,root,action,target,color,headless=getattr(self.session,'headless',False))
                return 'NeoBranch '+action+' completed'
            return self._enqueue('NeoBranch '+action,work)
        except Exception as error:return False,failure_from_exception(error)


def headless_workspace(base,parent=None):
    """QCoreApplication-only owner for standalone CLI commands."""
    from types import SimpleNamespace
    from PySide6.QtCore import QObject,Signal
    from ui.directory import DirectoryModel
    from ui.workspace_tasks import WorkspaceTasks
    from ui.workspace_branches import WorkspaceBranches
    class HeadlessWorkspace(QObject):
        statusChanged=Signal()
        presentationRequested=Signal(str,'QVariantMap')
        def __init__(self):
            super().__init__(parent)
            self.headless=True;self.session=self;self.project=base;self.p1=os.path.basename(base);self.p2=None
            self.tree=DirectoryModel(self,watch=False);self.tree2=None;self.directories=(self.tree,)
            self.tree.load(base);self.active_tree=self.tree
            self._tasks=WorkspaceTasks(self);self.task_queue=self._tasks.queue
            self._closing=False;self._operation_serial=0;self._history=[];self._clipboard=None;self._selection=[];self._tab_data=[]
            self.editor=SimpleNamespace(bridge=SimpleNamespace(neobranch_tabs=lambda *_:[],is_unsaved=lambda _:False))
            self.branches=WorkspaceBranches(self);self.quick_cli=WorkspaceCli(self)
            self.last_failed=None;self.super_tabs=[]
        def message(self,text,title='',**_):self.last_failed=str(text);print(f'[RAK_CLI] {title}: {text}')
        def dispatch(self,action,data):
            from ui.workspace_tools import dispatch_tool
            dispatch_tool(self,action,data['path'],data)
        def stop(self):self._closing=True;self._tasks.close();self.tree.close()
    return HeadlessWorkspace()
