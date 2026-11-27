# SPDX-License-Identifier: MPL-2.0
"""Read and verification commands over the same logical branch view as edits."""
import argparse
from contextlib import ExitStack
import fnmatch
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile

from core.command_context import CommandFailure,OperationError,failure_from_exception
from core.rak_neobranch_view import ActiveProjectView,active_config_file

COMMANDS=('stat','sha256sum','cmp','diff','find','head','tail','wc')


class Parser(argparse.ArgumentParser):
    def error(self,message):raise ValueError(self.prog+': '+message+'\n'+self.format_usage().strip())


def _parser(command):
    parser=Parser(prog='rak '+command,add_help=False,allow_abbrev=False)
    parser.add_argument('--help','-h',action='store_true')
    if command=='stat':
        parser.add_argument('-L','--dereference',action='store_true');parser.add_argument('--json',action='store_true')
    elif command=='sha256sum':parser.add_argument('-c','--check',action='store_true')
    elif command=='cmp':parser.add_argument('-s','--silent','--quiet',action='store_true')
    elif command=='diff':
        for short,long in (('-u','--unified'),('-q','--brief'),('-r','--recursive'),('-s','--report-identical-files'),('-i','--ignore-case'),('-w','--ignore-all-space'),('-b','--ignore-space-change'),('-B','--ignore-blank-lines')):
            parser.add_argument(short,long,action='store_true')
        parser.add_argument('-U',dest='context',type=int)
        parser.add_argument('--strip-trailing-cr',action='store_true')
    elif command=='find':
        parser.add_argument('-L',action='store_true');parser.add_argument('-name');parser.add_argument('-iname')
        parser.add_argument('-type',choices=('f','d','l'));parser.add_argument('-maxdepth',type=int);parser.add_argument('-mindepth',type=int,default=0)
        parser.add_argument('-print',action='store_true');parser.add_argument('-print0',action='store_true')
    elif command in ('head','tail'):
        parser.add_argument('-n','--lines',default='10');parser.add_argument('-q','--quiet',action='store_true');parser.add_argument('-v','--verbose',action='store_true')
    elif command=='wc':
        for short,long in (('-l','--lines'),('-w','--words'),('-c','--bytes'),('-m','--chars')):parser.add_argument(short,long,action='store_true')
        parser.add_argument('--files0-from')
    parser.add_argument('paths',nargs='*')
    return parser


def execute(command,args,cwd,root_for,*,allow_stdin=False):
    try:
        parser=_parser(command);options=parser.parse_args(args)
        if options.help:return True,parser.format_help()
        if command in ('cmp','diff') and len(options.paths)!=2:raise ValueError(command+' requires two paths')
        if not options.paths and command!='find' and not (command=='wc' and options.files0_from):raise ValueError(command+': missing file operand (stdin is not accepted over IPC)')
        views={};directories={};bulk=False
        def locate(operand,follow=True):
            logical=os.path.abspath(os.path.join(cwd,operand));root=root_for(logical)
            if root not in views:views[root]=ActiveProjectView(root)
            view=views[root];entry=None
            if bulk:
                parent=os.path.dirname(logical);key=(root,parent)
                if key not in directories:
                    directories[key]={os.path.normcase(row['name']):row for row in view.list_dir(parent,_with_physical=True)}
                entry=directories[key].get(os.path.normcase(os.path.basename(logical)))
            if entry is not None and (not follow or not entry.get('symlink')):physical,origin=entry['_physical'],entry['origin']
            else:physical,origin=(view.resolve_following(logical) if follow else view.resolve(logical))
            if not physical:raise FileNotFoundError(logical)
            return view,logical,physical,origin
        if command=='wc' and options.files0_from:
            if options.paths:raise ValueError('wc --files0-from cannot be combined with file operands')
            if options.files0_from=='-':
                if not allow_stdin:raise ValueError('wc --files0-from=- requires a standalone terminal pipe; IPC requests have no stdin')
                raw=sys.stdin.buffer.read()
            else:
                with open(locate(options.files0_from)[2],'rb') as stream:raw=stream.read()
            options.paths=[part.decode('utf-8') for part in raw.rstrip(b'\0').split(b'\0')] if raw else []
            if any(not path for path in options.paths):raise ValueError('Empty filename in wc --files0-from')
            if not options.paths:return True,''
        bulk=len(options.paths)>16
        if command=='stat':
            results=[_stat(*locate(path,options.dereference),follow=options.dereference) for path in options.paths]
            if options.json:return True,json.dumps(results[0] if len(results)==1 else results,ensure_ascii=False)
            return True,'\n'.join(_format_stat(row) for row in results)+'\n'
        if command=='sha256sum':
            if options.check:
                lines=[];different=False
                for manifest in options.paths:
                    _,_,physical,_=locate(manifest)
                    with open(physical,encoding='utf-8-sig') as stream:
                        for line in stream:
                            if not line.strip():continue
                            match=re.fullmatch(r'([0-9a-fA-F]{64}) [ *](.+)',line.rstrip('\r\n'))
                            if not match:raise ValueError('Malformed SHA256 checksum line in '+manifest)
                            expected,name=match.groups();_,_,target,_=locate(name)
                            same=_hash(target)==expected.lower();different=different or not same
                            lines.append(name+(': OK' if same else ': FAILED'))
                output='\n'.join(lines)+'\n'
                return (False,CommandFailure(output,code='different')) if different else (True,output)
            return True,''.join(_hash(locate(path)[2])+'  '+path+'\n' for path in options.paths)
        if command=='cmp':
            left=locate(options.paths[0])[2];right=locate(options.paths[1])[2];offset=0
            with open(left,'rb') as first,open(right,'rb') as second:
                while True:
                    a=first.read(1024*1024);b=second.read(1024*1024)
                    if a!=b:
                        position=next((index for index,(x,y) in enumerate(zip(a,b)) if x!=y),min(len(a),len(b)))
                        text='' if options.silent else options.paths[0]+' '+options.paths[1]+' differ: byte '+str(offset+position+1)+'\n'
                        return False,CommandFailure(text,code='different')
                    if not a:return True,''
                    offset+=len(a)
        if command=='find':return True,_find(options,cwd,locate)
        return _gnu(command,options,locate)
    except (OSError,ValueError,OperationError) as error:
        failure=failure_from_exception(error)
        return False,CommandFailure(failure,application_error=failure.application_error,code='probe_error')


def _hash(path):
    with open(path,'rb') as stream:
        before=os.fstat(stream.fileno());digest=hashlib.sha256()
        for block in iter(lambda:stream.read(1024*1024),b''):digest.update(block)
        after=os.fstat(stream.fileno())
        if (before.st_size,before.st_mtime_ns)!=(after.st_size,after.st_mtime_ns):raise ValueError('File changed while hashing: '+path)
    return digest.hexdigest()


def _stat(view,logical,physical,origin,*,follow):
    link=view.link_info(logical);resolved=view.canonical(logical,follow_final=follow)
    info=os.stat(physical,follow_symlinks=follow)
    rel=os.path.relpath(resolved,view.root).replace(os.sep,'/');root=rel.split('/')[0]
    branch=view._branch(root)[0] if rel!='.' else None
    android={'uid':None,'gid':None,'mode':None,'selinux':None,'selinux_candidates':[]}
    if rel!='.':
        fs_config=active_config_file(view,root,'_fs_config')
        contexts=active_config_file(view,root,'_file_contexts');metadata_base=view.root
        if not os.path.isfile(fs_config):
            candidate=resolved if os.path.isdir(physical) and not link else os.path.dirname(resolved)
            while os.path.normcase(candidate)!=os.path.normcase(view.root):
                parent=os.path.dirname(candidate)
                if parent==candidate:break
                logical_config=os.path.join(parent,'config',os.path.basename(candidate)+'_fs_config')
                local,_=view.resolve(logical_config)
                if local:
                    fs_config=local;contexts=view.resolve(logical_config[:-len('_fs_config')]+'_file_contexts')[0] or ''
                    metadata_base=parent;rel=os.path.relpath(resolved,parent).replace(os.sep,'/');break
                candidate=parent
        if os.path.isfile(fs_config):
            with open(fs_config,encoding='utf-8') as stream:
                for line in stream:
                    fields=line.split()
                    if len(fields)>=4 and fields[0]==rel:android.update(uid=fields[1],gid=fields[2],mode=fields[3])
        from core.boot_image import is_boot_ramdisk_rel
        if os.path.isfile(contexts) and not is_boot_ramdisk_rel(metadata_base,rel):
            with open(contexts,encoding='utf-8') as stream:
                for line in stream:
                    fields=line.split()
                    if len(fields)<2 or fields[0].startswith('#'):continue
                    pattern=fields[0];label=fields[-1]
                    if pattern=='/'+rel or pattern=='/'+re.escape(rel):android['selinux']=label
                    try:matched=re.fullmatch(pattern,'/'+rel) is not None
                    except re.error:matched=False
                    if matched:android['selinux_candidates'].append({'pattern':pattern,'context':label})
    return {'path':logical,'resolved_path':resolved,'kind':'symlink' if link and not follow else 'directory' if os.path.isdir(physical) else 'file',
            'size':info.st_size,'mtime_ns':info.st_mtime_ns,'origin':origin,'branch':branch,
            'symlink':link,'symlinks':view.link_chain(logical),'android':android}


def _format_stat(row):
    android=row['android'];link=row['symlink']
    suffix=' -> '+link['target']+' ['+link['status']+']' if link else ''
    return ('  File: '+row['path']+suffix+'\n  Type: '+row['kind']+'  Size: '+str(row['size'])+'  Origin: '+row['origin']+
            '  Branch: '+str(row['branch'])+'\n  Android mode: '+str(android['mode'])+'  UID: '+str(android['uid'])+'  GID: '+str(android['gid'])+
            '  SELinux exact entry: '+str(android['selinux'])+'\n')


def _find(options,cwd,locate):
    if options.mindepth<0 or options.maxdepth is not None and options.maxdepth<0:raise ValueError('find depth must be nonnegative')
    output=[]
    for operand in options.paths or ['.']:
        view,start,_,_=locate(operand,False)
        def visit(logical,depth,ancestors,entry=None):
            if entry is None:
                link=view.link_info(logical);physical,_=view.resolve(logical)
                if not physical:raise FileNotFoundError(logical)
                folder=bool(link['is_dir'] if link else os.path.isdir(physical))
            else:link=entry.get('symlink');folder=entry['kind']=='dir'
            if link and options.L and link['status']=='cycle':raise ValueError('find: symlink cycle at '+logical)
            kind='l' if link and (not options.L or not link['exists']) else 'd' if folder else 'f'
            name=os.path.basename(logical)
            matches=(not options.name or fnmatch.fnmatchcase(name,options.name)) and (not options.iname or fnmatch.fnmatchcase(name.casefold(),options.iname.casefold()))
            if depth>=options.mindepth and matches and (not options.type or options.type==kind):
                output.append(logical if os.path.isabs(operand) else os.path.join(operand,os.path.relpath(logical,start)) if depth else operand)
            if folder and (not link or options.L) and (options.maxdepth is None or depth<options.maxdepth):
                key=os.path.normcase(view.canonical(logical))
                if key in ancestors:raise ValueError('find: directory symlink cycle at '+logical)
                for entry in view.list_dir(logical):visit(entry['path'],depth+1,ancestors|{key},entry)
        visit(start,0,frozenset())
    return ''.join(path.replace(os.sep,'/')+('\0' if options.print0 else '\n') for path in output)


def _gnu(command,options,locate):
    from core.unix_tools import resolve_tool
    from core import kitchen_process
    from core.proc_util import CREATE_NO_WINDOW
    binary=resolve_tool(command)
    if not binary:raise OperationError('GNU '+command+' is unavailable. Install the Unix tools package or Git for Windows.')
    arguments=[]
    if command in ('head','tail'):
        if not re.fullmatch(r'[+-]?\d+',options.lines):raise ValueError('Line count must be an integer')
        arguments=['-n',options.lines]
        if options.quiet:arguments.append('-q')
        if options.verbose:arguments.append('-v')
    elif command=='wc':
        arguments=[flag for name,flag in (('lines','-l'),('words','-w'),('bytes','-c'),('chars','-m')) if getattr(options,name)]
    else:
        from core.unix_tools import is_gnu_tool
        diff_is_gnu = (sys.platform != 'darwin') or is_gnu_tool(binary)
        for name,flag in (('unified','-u'),('brief','-q'),('recursive','-r'),('report_identical_files','-s'),('ignore_case','-i'),('ignore_all_space','-w'),('ignore_space_change','-b'),('ignore_blank_lines','-B')):
            if getattr(options,name):arguments.append(flag)
        if getattr(options,'strip_trailing_cr') and diff_is_gnu:
            arguments.append('--strip-trailing-cr')
        if options.context is not None:
            if options.context<0:raise ValueError('diff context must be nonnegative')
            arguments.extend(['-U',str(options.context)])
    mapping=[];operands=[]
    with ExitStack() as stack:
        located=[locate(label) for label in options.paths]
        staged=None
        if command=='diff' and options.recursive and not options.report_identical_files and all(os.path.isdir(row[2]) for row in located):
            staged=[stack.enter_context(tempfile.TemporaryDirectory(prefix='rak-diff-view-')) for _ in located]
            _stage_differences(located[0][0],located[0][1],located[1][0],located[1][1],*staged)
        for index,(label,(view,logical,physical,_)) in enumerate(zip(options.paths,located)):
            if os.path.isdir(physical):
                if command!='diff' or not options.recursive:raise IsADirectoryError(logical)
                if staged:physical=staged[index]
                else:
                    staging=stack.enter_context(tempfile.TemporaryDirectory(prefix='rak-diff-view-'))
                    from core.project_view_work import _copy_view
                    _copy_view(view,logical,staging);physical=staging
            if command=='wc':physical=physical.replace(os.sep,'/')
            operands.append(physical);mapping.append((physical,label))
        if command=='diff' and not options.recursive:
            for _,label in mapping:arguments.extend(['-L',label])
        if command=='wc':
            from core.unix_tools import is_gnu_tool
            use_files0 = (sys.platform != 'darwin') or is_gnu_tool(binary)
            if use_files0:
                folder=stack.enter_context(tempfile.TemporaryDirectory(prefix='rak-wc-paths-'))
                manifest=Path(folder)/'files';manifest.write_bytes(b''.join(path.encode('utf-8')+b'\0' for path in operands))
                invocation=[binary,*arguments,'--files0-from='+manifest.as_posix()]
            else:
                invocation=[binary,*arguments,'--',*operands]
        else:invocation=[binary,*arguments,'--',*operands]
        result=kitchen_process.run(invocation,stdout=kitchen_process.PIPE,stderr=kitchen_process.PIPE,
            text=True,encoding='utf-8',errors='replace',timeout=120,creationflags=CREATE_NO_WINDOW)
    output=result.stdout
    if command=='wc':
        labels=dict(mapping);lines=[]
        for line in output.splitlines(keepends=True):
            match=re.fullmatch(r'([ \t]*\d+(?:[ \t]+\d+)*[ \t]+)(.*?)(\r?\n)?',line)
            if match:
                prefix,name,newline=match.groups();line=prefix+labels.get(name,name)+(newline or '')
            lines.append(line)
        output=''.join(lines)
    elif command in ('head','tail'):
        headers={'==> '+physical+' <==':'==> '+label+' <==' for physical,label in mapping}
        output=''.join(headers.get(line.rstrip('\r\n'),line.rstrip('\r\n'))+line[len(line.rstrip('\r\n')):] for line in output.splitlines(keepends=True))
    if command=='diff':
        lines=[]
        for line in output.splitlines(keepends=True):
            if line.startswith(('diff ','Only in ','Binary files ','Files ','--- ','+++ ')):
                for physical,label in mapping:line=line.replace(physical,label)
            lines.append(line)
        output=''.join(lines)
        if result.returncode==1:return False,CommandFailure(output,code='different')
    if result.returncode:raise OperationError(command+' exited with status '+str(result.returncode)+'\n'+result.stderr+output)
    return True,output


def _stage_differences(left_view,left,right_view,right,left_out,right_out):
    """GNU still renders differences; unchanged files need no staging copy."""
    left_entries={row['name']:row for row in left_view.list_dir(left,_with_physical=True)}
    right_entries={row['name']:row for row in right_view.list_dir(right,_with_physical=True)}
    for name in sorted(left_entries.keys()|right_entries.keys()):
        a=left_entries.get(name);b=right_entries.get(name)
        for row in (a,b):
            if row and os.path.islink(row['_physical']):raise OperationError('Recursive diff materialization does not support native symlinks: '+row['path'])
        a_dir=bool(a and a['kind']=='dir' and not a.get('symlink'))
        b_dir=bool(b and b['kind']=='dir' and not b.get('symlink'))
        x=os.path.join(left_out,name);y=os.path.join(right_out,name)
        if a and b and a_dir and b_dir:
            os.mkdir(x);os.mkdir(y);_stage_differences(left_view,a['path'],right_view,b['path'],x,y)
        elif a and b and not a_dir and not b_dir:
            if not _equal_files(a['_physical'],b['_physical']):shutil.copy2(a['_physical'],x);shutil.copy2(b['_physical'],y)
        else:
            if a:
                if a_dir:os.mkdir(x)
                else:Path(x).touch()
            if b:
                if b_dir:os.mkdir(y)
                else:Path(y).touch()


def _equal_files(left,right):
    with open(left,'rb') as a,open(right,'rb') as b:
        before_a=os.fstat(a.fileno());before_b=os.fstat(b.fileno());equal=True
        if before_a.st_size!=before_b.st_size:return False
        remaining=before_a.st_size
        while remaining:
            count=min(1024*1024,remaining);x=a.read(count);y=b.read(count)
            if x!=y:equal=False;break
            if len(x)!=count:raise ValueError('A file changed during recursive comparison')
            remaining-=count
        for before,stream in ((before_a,a),(before_b,b)):
            after=os.fstat(stream.fileno())
            if (before.st_size,before.st_mtime_ns)!=(after.st_size,after.st_mtime_ns):raise ValueError('A file changed during recursive comparison')
        return equal
