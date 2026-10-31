# SPDX-License-Identifier: MPL-2.0
"""Plan merges before asking for conflicts; mutate only after a decision."""
import datetime
import os
import re
from dataclasses import dataclass,field
from core.workspace_files import WorkspaceFiles,validate_name
from core.command_context import OperationError


def is_self_drop(source,directory):
    source=os.path.normcase(os.path.abspath(source));directory=os.path.normcase(os.path.abspath(directory))
    try:return os.path.commonpath((source,directory))==source
    except ValueError:return False


def file_details(path):
    try:
        size=os.path.getsize(path);value=float(size)
        for unit in ('B','KB','MB','GB'):
            if value<1024:break
            value/=1024
        stamp=datetime.datetime.fromtimestamp(os.path.getmtime(path)).strftime('%d %b %Y  %H:%M')
        return f'{value:.1f} {unit}   ·   {stamp}'
    except OSError:return '—'


@dataclass
class TransferPlan:
    destination: WorkspaceFiles
    move: bool=False
    duplicate_in_place: bool=False
    drag: bool=False
    items: list=field(default_factory=list)
    conflicts: list=field(default_factory=list)
    merged: list=field(default_factory=list)
    reserved: dict=field(default_factory=dict)
    root_merges: list=field(default_factory=list)

    def add(self,source_files,source,directory):
        source,source_relative=source_files.logical(source)
        if self.move and source_relative=='config':raise ValueError('config folder cannot be moved')
        directory,_=self.destination.logical(directory,allow_root=True)
        target=os.path.join(directory,validate_name(os.path.basename(source)))
        physical,_=source_files.view.resolve(source)
        if not physical:raise FileNotFoundError(source)
        if self.drag and self.move and os.path.isdir(physical) and '/' not in source_relative and source_files.branch(source)[3]:
            raise ValueError('Cannot move a filesystem root with an active NeoBranch')
        existing,_=self.destination.view.resolve(target)
        if self.move and existing:
            try:
                if os.path.samefile(physical,existing):return
            except OSError:pass
        if self.move and source_files.base!=self.destination.base and os.path.isdir(physical) and '/' not in source_relative and directory==self.destination.base:
            if source_files.branch(source)[3] or self.destination.branch(target)[3]:
                raise ValueError('Cannot move a filesystem root with an active NeoBranch')
        if self.duplicate_in_place and not self.move and source_files.base==self.destination.base and os.path.normcase(source)==os.path.normcase(target):
            stem,extension=os.path.splitext(os.path.basename(source)) if not os.path.isdir(physical) else (os.path.basename(source),'')
            stem=re.sub(r'\(\d+\)$','',stem.rstrip()).rstrip();number=2
            while True:
                candidate=os.path.join(directory,f'{stem}({number}){extension}')
                existing,_=self.destination.view.resolve(candidate)
                if not existing and os.path.normcase(candidate) not in self.reserved:break
                number+=1
            target=candidate
        if source_files.base==self.destination.base and os.path.normcase(source)==os.path.normcase(target):
            raise ValueError('Source and destination are the same')
        if os.path.isdir(physical):
            if is_self_drop(source,directory):
                if self.move:return
                raise ValueError('A folder cannot be placed inside itself')
            existing,_=self.destination.view.resolve(target)
            if (existing and os.path.isdir(existing) and directory==self.destination.base and source_files.base!=self.destination.base
                and source_relative==os.path.relpath(target,self.destination.base).replace(os.sep,'/') and source_files.is_filesystem_root(source)):
                self.root_merges.append((source_files,source,target))
        self._add(source_files,source,physical,target)

    def _add(self,source_files,source,physical,target):
        existing,_=self.destination.view.resolve(target)
        existing=self.reserved.get(os.path.normcase(target),existing)
        if existing and os.path.isdir(existing) and os.path.isdir(physical):
            for entry in source_files.view.list_dir(source):
                child,_=source_files.view.resolve(entry['path'])
                self._add(source_files,entry['path'],child,os.path.join(target,entry['name']))
            self.merged.append((source_files,source,target))
            return
        row={'files':source_files,'source':source,'physical':physical,'target':target,'conflict':bool(existing)}
        self.items.append(row)
        if existing:
            self.conflicts.append({'key':target,'name':os.path.basename(target),'source':physical,'destination':existing,
                                   'incoming':file_details(physical),'existing':file_details(existing)})
        self.reserved[os.path.normcase(target)]=physical

    def execute(self,decisions):
        # Validate the complete response before copying a single byte.
        for item in self.items:
            if item['conflict'] and decisions.get(item['target']) not in ('skip','replace'):
                raise ValueError('Missing conflict decision for '+item['target'])
        active_roots=[]
        for files,source,target in self.root_merges:
            children=[item for item in self.items if item['files'] is files and os.path.commonpath((item['source'],source))==source]
            if children and all(item['conflict'] and decisions.get(item['target'])=='skip' for item in children):continue
            source_engine=files.engine(source);destination_engine=self.destination.engine(target)
            relative=os.path.relpath(source,files.base).replace(os.sep,'/')
            dest_relative=os.path.relpath(target,self.destination.base).replace(os.sep,'/')
            self.destination._copy_metadata(source_engine,destination_engine,relative,dest_relative,allow_create=True)
            active_roots.append((files,source,target,source_engine,destination_engine,relative,dest_relative))
        completed=[];skipped=[]
        for item in self.items:
            target=item['target']
            if item['conflict']:
                decision=decisions.get(target)
                if decision=='skip':skipped.append(target);continue
                if decision!='replace':raise ValueError('Missing conflict decision for '+target)
            def transfer():
                self.destination.transfer(item['files'],item['source'],os.path.dirname(target),
                                          name=os.path.basename(target),move=self.move,replace=item['conflict'],drag=self.drag)
                return True,target
            if self.move and item['files'].base!=self.destination.base and item['source'].lower().endswith('.apk'):
                from core.apktool_tool import move_apk_with_mirror
                ok,result=move_apk_with_mirror(item['files'].base,self.destination.base,item['source'],target,transfer)
                if not ok:raise OperationError(result)
            else:transfer()
            completed.append(target)
        # A merged cut retains skipped children. Only empty source directories
        # may be removed, after all of their child transfers have succeeded.
        if self.move:
            for files,source,target in self.merged:
                physical,_=files.view.resolve(source)
                if physical and not files.view.list_dir(source):
                    files.delete(source,keep_metadata=self.drag and not self.destination.has_filesystem_metadata(target))
        for files,source,target,src_engine,dst_engine,relative,dest_relative in active_roots:
            self.destination._copy_identity(src_engine,dst_engine,relative,dest_relative,
                move=self.move and files.view.resolve(source)[0] is None)
        return {'completed':completed,'skipped':skipped}


def plan_transfer(destination_base,sources,directory,*,move=False,duplicate_in_place=False,drag=False):
    plan=TransferPlan(WorkspaceFiles(destination_base),move,duplicate_in_place,drag)
    for base,path in sources:plan.add(WorkspaceFiles(base),path,directory)
    return plan
