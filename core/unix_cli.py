# SPDX-License-Identifier: MPL-2.0
"""Familiar Unix operands translated into managed Kitchen transactions."""
import argparse
import os

COMMANDS=('cp','mv','rm','mkdir','touch','ln','readlink','sed','chmod')
HELP="""Unix-style project commands (logical paths, active NeoBranch aware):
  rak cp [-r|-a] [-f|-n] SOURCE... DEST
  rak mv [-f|-n] SOURCE... DEST
  rak rm [-rf] PATH...
  rak mkdir [-p] DIR...
  rak touch [-c] FILE...
  rak ln -s TARGET LINK_NAME
  rak readlink [-f|-e] LINK_NAME
  rak chmod OCTAL PATH...
  rak sed [-En] [-i] SCRIPT FILE
  rak sed [-En] [-i] -e SCRIPT [-e SCRIPT ...] FILE
sed -i uses the current editor revision and Monaco acknowledgment when open.
Scripts run in installed GNU sed; script file I/O and shell execution are disabled.
Folder copies require -r/-a and merge existing directories as cp does.
Existing RAK fs/action names remain available as compatibility aliases.
"""


class Parser(argparse.ArgumentParser):
    def error(self,message):raise ValueError(self.prog+': '+message)


def parse(command,rest,cwd):
    from core.cli_arguments import tokens as argument_tokens
    tokens=argument_tokens(command,rest)
    if not tokens:raise ValueError(command+': missing operand')
    if tokens[0] in ('--help','-h'):return {'help':True}
    parser=Parser(prog='rak '+command,add_help=False,allow_abbrev=False)
    if command in ('cp','mv'):
        if command=='cp':
            parser.add_argument('-r','-R','--recursive',action='store_true')
            parser.add_argument('-a','--archive',action='store_true')
            parser.add_argument('-p','--preserve',action='store_true')
        group=parser.add_mutually_exclusive_group()
        group.add_argument('-f','--force',action='store_true')
        group.add_argument('-n','--no-clobber',action='store_true')
        parser.add_argument('-v','--verbose',action='store_true')
        parser.add_argument('-T','--no-target-directory',action='store_true')
        parser.add_argument('paths',nargs='+')
    elif command=='rm':
        parser.add_argument('-r','-R','--recursive',action='store_true')
        parser.add_argument('-f','--force',action='store_true')
        parser.add_argument('--yes',action='store_true')
        parser.add_argument('paths',nargs='+')
    elif command in ('mkdir','touch'):
        parser.add_argument('-p' if command=='mkdir' else '-c',dest='parents' if command=='mkdir' else 'no_create',action='store_true')
        parser.add_argument('paths',nargs='+')
    elif command=='ln':
        parser.add_argument('-s','--symbolic',action='store_true',required=True)
        parser.add_argument('target');parser.add_argument('path')
    elif command=='readlink':
        group=parser.add_mutually_exclusive_group()
        group.add_argument('-f','--canonicalize',action='store_true')
        group.add_argument('-e','--canonicalize-existing',action='store_true')
        parser.add_argument('path')
    elif command=='chmod':
        parser.add_argument('mode');parser.add_argument('paths',nargs='+')
    else:
        parser.add_argument('-i','--in-place',action='store_true')
        parser.add_argument('-E','-r','--regexp-extended',action='store_true')
        parser.add_argument('-n','--quiet','--silent',dest='quiet',action='store_true')
        parser.add_argument('-s','--separate',action='store_true')
        parser.add_argument('-e','--expression',dest='expressions',action='append',default=[])
        parser.add_argument('operands',nargs='+')
    options=vars(parser.parse_args(tokens));options['command']=command
    if command in ('cp','mv') and len(options['paths'])<2:raise ValueError('Missing destination operand')
    if command=='chmod' and (len(options['mode']) not in (3,4) or any(char not in '01234567' for char in options['mode'])):raise ValueError('chmod requires an octal mode, e.g. 0644')
    if command=='sed':
        operands=options.pop('operands')
        if not options['expressions']:
            if len(operands)!=2:raise ValueError('sed expects one expression and one file')
            expression,options['path']=operands;options['expressions']=[expression]
        else:
            if len(operands)!=1:raise ValueError('sed expects one file')
            options['path']=operands[0]
        options['expression']=options['expressions'][0]
    if command in ('cp','mv'):
        options['contents']=[value.rstrip('/\\').endswith(('/.','\\.')) or value=='.' for value in options['paths'][:-1]]
    for key in ('path','paths'):
        if key in options:
            values=options[key] if key=='paths' else [options[key]]
            values=[os.path.abspath(os.path.join(cwd,value)) for value in values]
            options[key]=values if key=='paths' else values[0]
    return options


def substitution(expression,extended=False):
    from core.unix_tools import sed_transform
    return sed_transform({'expression':expression,'regexp_extended':extended})


def execute(bridge,options,*,wait=True,timeout=120,cwd=None,on_qt_thread=False):
    from core.command_context import CommandFailure
    from core import rak_cli
    if options['command']=='sed':
        ok,agent=bridge.invoke('_agent_service')
        if ok:
            ok,opened=bridge.invoke('_agent_open_document',options['path'])
            if not ok:return ok,opened
            if not opened:
                if options['in_place'] and not agent.settings_info()['agent']['autosave']:
                    return False,CommandFailure('Agent autosave is disabled. sed -i requires saving; use ui edit for a buffer-only edit.')
                return _dispatch(bridge,options,wait,timeout)
            ok,document=bridge.invoke('_agent_transform_document',options['path'])
            if not ok:return ok,document
            from core.unix_tools import sed_transform
            content=sed_transform(options)(document['content'])
            if not options['in_place']:return True,content
            if document.get('truncated'):return False,CommandFailure('The document read was truncated; refusing to replace its full contents')
            if not agent.settings_info()['agent']['autosave']:
                return False,CommandFailure('Agent autosave is disabled. sed -i requires saving; use ui edit for a buffer-only edit.')
            payload={'path':document['path'],'revision':document['revision'],'content':content}
            ok,result=bridge.invoke('_run_agent',{'command':'ui','action':'edit','payload':payload},cwd)
            if not ok:return ok,result
            if wait and not on_qt_thread:result=agent.response(result['id'],timeout)
            if result['state']=='applied' and result.get('response',{}).get('saved'):
                return True,'updated '+document['path']
            if result['state'] in ('pending','applying'):
                return False,CommandFailure('Edit is still applying; request '+result['id']+'. Inspect rak ui response '+result['id']+' before retrying.',code='pending')
            response=result.get('response') or {}
            return False,CommandFailure(str(response.get('error',response)),application_error=response.get('application_error',False),code=response.get('error_code','edit_failed'))
    return _dispatch(bridge,options,wait,timeout)


def _dispatch(bridge,options,wait,timeout):
    from core import rak_cli
    ok,message=bridge.invoke('_run_unix',options)
    if not ok or not wait:return ok,message
    if getattr(message,'task_id',None):return rak_cli._wait_dispatched(message,timeout,return_output=True)
    return ok,message
