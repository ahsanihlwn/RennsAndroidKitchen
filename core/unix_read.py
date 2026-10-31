# SPDX-License-Identifier: MPL-2.0
"""Common Unix read flags over the logical NeoBranch filesystem."""
import argparse
import os
import re
from core.command_context import CommandFailure,OperationError,failure_from_exception
from core.rak_neobranch_view import ActiveProjectView


class Parser(argparse.ArgumentParser):
    def error(self,message):raise ValueError(message+'\n'+self.format_usage().strip())


def execute(command,args,cwd,root_for):
    command={'list':'ls','read':'cat'}.get(command,command)
    parser=Parser(prog='rak '+command,add_help=False,allow_abbrev=False)
    if command=='ls':
        parser.add_argument('-l',action='store_true');parser.add_argument('-a','-A',action='store_true')
        parser.add_argument('-d','--directory',action='store_true')
        parser.add_argument('-R','--recursive',action='store_true');parser.add_argument('--branch-only',action='store_true')
        parser.add_argument('paths',nargs='*')
    elif command=='cat':
        parser.add_argument('-n','--number',action='store_true');parser.add_argument('paths',nargs='+')
    else:
        parser.add_argument('-r','-R','--recursive',action='store_true')
        parser.add_argument('-n','--line-number',action='store_true');parser.add_argument('-H','--with-filename',action='store_true')
        parser.add_argument('-i','--ignore-case',action='store_true');parser.add_argument('-F','--fixed-strings',action='store_true')
        parser.add_argument('-E','--extended-regexp',action='store_true');parser.add_argument('-v','--invert-match',action='store_true')
        parser.add_argument('-l','--files-with-matches',action='store_true');parser.add_argument('-c','--count',action='store_true')
        parser.add_argument('pattern');parser.add_argument('paths',nargs='*')
    try:
        options=parser.parse_args(args);paths=options.paths or ['.'];output=[]
        grep_files=[];grep_names=command=='grep' and (options.with_filename or len(paths)>1)
        for operand in paths:
            target=os.path.abspath(os.path.join(cwd,operand));root=root_for(target);view=ActiveProjectView(root)
            node,node_origin=view.resolve(target)
            link=view.link_info(target)
            if command=='ls' and options.directory:
                if not node:raise FileNotFoundError(target)
                output.append(os.path.basename(target)+_link_suffix(link)+'\t['+node_origin+']')
                continue
            physical,origin=view.resolve_following(target)
            if not physical:raise FileNotFoundError(target)
            if command=='ls':
                def listing(logical,ancestors=frozenset()):
                    key=os.path.normcase(view.canonical(logical))
                    if key in ancestors:
                        import errno
                        raise OSError(errno.ELOOP,'Symlink directory cycle',logical)
                    ancestors=ancestors|{key}
                    entries=view.list_dir(logical)
                    if options.branch_only:entries=[entry for entry in entries if entry['origin']=='branch']
                    if not options.a:entries=[entry for entry in entries if not entry['name'].startswith('.')]
                    if len(paths)>1 or options.recursive:output.append(logical+':')
                    for traversed in view.link_chain(logical):
                        output.append(traversed['path']+_link_suffix(traversed)+' [target_origin='+str(traversed.get('target_origin'))+']')
                    for entry in entries:
                        detail=''
                        if options.l:
                            local,_=view.resolve(entry['path']);detail=f"{os.path.getsize(local):>10} "
                        link=entry.get('symlink')
                        output.append(detail+entry['name']+('/' if entry['kind']=='dir' else '')+_link_suffix(link)+'\t['+entry['origin']+']')
                    if options.recursive:
                        for entry in entries:
                            if entry['kind']=='dir':listing(entry['path'],ancestors)
                if os.path.isdir(physical):listing(target)
                else:output.append(os.path.basename(target)+_link_suffix(link)+'\t['+origin+']')
            elif command=='cat':
                if not os.path.isfile(physical):raise IsADirectoryError(target)
                with open(physical,'rb') as stream:raw=stream.read()
                text=raw.decode('utf-8',errors='replace')
                if options.number:text=''.join(f'{number:6}\t{line}' for number,line in enumerate(text.splitlines(keepends=True),1))
                output.append(text)
            else:
                if os.path.isdir(physical):
                    if not options.recursive:raise IsADirectoryError(target)
                    grep_names=True
                for logical in view.walk_files(target):
                    local,_=view.resolve_following(logical)
                    if local:grep_files.append((local,os.path.relpath(logical,cwd).replace(os.sep,'/')))
        if command=='grep':
            from core.unix_tools import grep
            arguments=[]
            for name,flag in (('line_number','-n'),('ignore_case','-i'),('fixed_strings','-F'),('extended_regexp','-E'),
                              ('invert_match','-v'),('files_with_matches','-l'),('count','-c')):
                if getattr(options,name):arguments.append(flag)
            if grep_names:arguments.append('-H')
            arguments.extend(['-e',options.pattern])
            matched,text=grep(arguments,grep_files)
            return (True,text) if matched else (False,CommandFailure('',code='no_match'))
        return True,(''.join(output) if command=='cat' else '\n'.join(output) or ('(no matches)' if command=='grep' else '(empty)'))
    except (OSError,ValueError,re.error,OperationError) as error:
        failure=failure_from_exception(error)
        if command=='grep':failure=CommandFailure(failure,application_error=failure.application_error,code='grep_error')
        return False,failure


def _link_suffix(link):
    if not link:return ''
    kind='directory' if link['is_dir'] else 'file' if link['is_file'] else 'unavailable'
    return ' -> '+link['target']+' [symlink:'+kind+'; '+link['status']+']'
