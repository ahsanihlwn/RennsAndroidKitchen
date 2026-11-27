# SPDX-License-Identifier: MPL-2.0
"""Use installed Unix engines without passing logical ROM paths to a shell."""
import os
from pathlib import Path
import shutil
import tempfile
from core.command_context import OperationError


def resolve_tool(name):
    from core.app_paths import BIN_DIR
    from core.binary_handler import resolve_external_binary
    installed=resolve_external_binary(BIN_DIR,name+'.exe' if os.name=='nt' else name)
    if installed:return installed
    installed=shutil.which(name)
    if installed:return installed
    # Git for Windows ships real GNU tools outside its public cmd/ PATH entry.
    git=shutil.which('git')
    if git and os.name=='nt':
        for root in list(Path(git).parents)[:3]:
            candidate=root/'usr/bin'/(name+'.exe')
            if candidate.is_file():return str(candidate)
    return None


def sed(text,expressions,*,extended=False,quiet=False,separate=False):
    from core import kitchen_process
    from core.proc_util import CREATE_NO_WINDOW
    binary=resolve_tool('sed')
    if not binary:raise OperationError('GNU sed is unavailable. Install the Unix tools package or Git for Windows; no partial sed emulation was used.')
    arguments=[binary,'--sandbox']
    if extended:arguments.append('-E')
    if quiet:arguments.append('-n')
    if separate:arguments.append('-s')
    # MSYS argument parsing can consume unquoted BRE backslashes in Windows
    # command lines. A script file keeps the exact GNU program as data.
    with tempfile.TemporaryDirectory(prefix='rak-sed-') as temporary:
        script=Path(temporary)/'script.sed';script.write_text('\n'.join(expressions)+'\n',encoding='utf-8',newline='\n')
        result=kitchen_process.run([*arguments,'-f',script.as_posix()],input=text,stdout=kitchen_process.PIPE,stderr=kitchen_process.PIPE,
                                   text=True,encoding='utf-8',errors='strict',timeout=120,creationflags=CREATE_NO_WINDOW)
    if result.returncode:
        raise OperationError('sed exited with status '+str(result.returncode)+'\nScripts: '+repr(expressions)+'\n'+(result.stderr or '')+(result.stdout or ''))
    return result.stdout


def sed_transform(options):
    """Compatibility for callers that previously passed one substitution."""
    expressions=options.get('expressions') or [options['expression']]
    return lambda text:sed(text,expressions,extended=options.get('regexp_extended',False),
                           quiet=options.get('quiet',False),separate=options.get('separate',False))


def grep(arguments,files):
    """Run grep against resolved files; only filename prefixes are relabelled."""
    from core import kitchen_process
    from core.proc_util import CREATE_NO_WINDOW
    binary=resolve_tool('grep')
    if not binary:raise OperationError('GNU grep is unavailable. Install the Unix tools package or Git for Windows.')
    output=[];matched=False
    # Keep Windows command lines bounded, including projects with long paths.
    batches=[];batch=[];size=0
    for physical,logical in files:
        if batch and size+len(physical)>18000:batches.append(batch);batch=[];size=0
        batch.append((physical,logical));size+=len(physical)+3
    if batch:batches.append(batch)
    expression_index=arguments.index('-e');pattern=arguments[expression_index+1]
    arguments=arguments[:expression_index]+arguments[expression_index+2:]
    with tempfile.TemporaryDirectory(prefix='rak-grep-') as temporary:
        script=Path(temporary)/'pattern';script.write_text(pattern+'\n',encoding='utf-8',newline='\n')
        for batch in batches:
            result=kitchen_process.run([binary,*arguments,'-f',script.as_posix(),'--',*[path for path,_ in batch]],
                                       stdout=kitchen_process.PIPE,stderr=kitchen_process.PIPE,text=True,
                                       encoding='utf-8',errors='replace',timeout=120,creationflags=CREATE_NO_WINDOW)
            if result.returncode not in (0,1):raise OperationError('grep exited with status '+str(result.returncode)+'\n'+result.stderr+result.stdout)
            matched=matched or result.returncode==0
            for line in result.stdout.splitlines(keepends=True):
                for physical,logical in batch:
                    if line.startswith(physical+':') or line.startswith(physical+'-') or line.rstrip('\r\n')==physical:
                        line=logical+line[len(physical):];break
                    prefix='Binary file '+physical+' matches'
                    if line.startswith(prefix):line='Binary file '+logical+' matches'+line[len(prefix):];break
                output.append(line)
    return matched,''.join(output)
