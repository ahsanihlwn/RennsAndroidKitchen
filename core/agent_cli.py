# SPDX-License-Identifier: MPL-2.0
"""Structured CLI entry points for the live Kitchen agent API."""
import argparse
import json
import os
from pathlib import Path
import shlex

COMMANDS=('context','capabilities','events','ui','inbox')
HELP='''rak context [--content] [--json]
rak capabilities [--json]
rak events [--since N] [--types document,task] [--wait 30] [--stream] [--json]
rak ui open <path> [--line N] [--column N] [--end-line N] [--end-column N]
rak ui reveal <path>
rak ui settings|controls
rak ui trigger <control-id> [--from <value.json>]
rak ui scroll <control-id>
rak ui guide agent.ui_triggers|agent.autosave|agent.highlight
rak ui read [path]
rak ui ask|notify|edit|review --from <request.json> [--wait 30]
rak ui edit|review <path> --revision <revision> --content-file <utf8-file> [--wait 30]
rak ui response|wait|cancel <request-id> [--wait 30]
rak inbox list [--state queued] [--limit N]
rak inbox get <id>
rak inbox next --agent <name> [--wait 30]
rak inbox start|progress|reply|fail|ack-cancel <id> --agent <name> --claim <token> [--from result.json]
rak inbox cancel <id>
All UI commands return JSON. Agent autosave is controlled by project settings.
UI access must be enabled by the user to open/reveal files or activate controls.
Settings are read-only to agents; guide scrolls to and highlights a user setting.'''


class _Parser(argparse.ArgumentParser):
    def error(self,message):raise ValueError(message+'\n'+HELP)


def parse(command,rest,cwd):
    tokens=[token.strip('"\'') for token in shlex.split(rest,posix=False)]
    if any(token in ('--help','-h') for token in tokens):return {'help':True}
    parser=_Parser(add_help=False)
    parser.add_argument('--json',action='store_true')
    if command=='context':parser.add_argument('--content',action='store_true')
    elif command=='events':
        parser.add_argument('--since',type=int,default=0)
        parser.add_argument('--limit',type=int,default=100)
        parser.add_argument('--types',default='')
        parser.add_argument('--wait',type=float,default=0)
        parser.add_argument('--stream',action='store_true')
    elif command=='ui':
        parser.add_argument('action',choices=('open','reveal','read','ask','notify','edit','review','response','wait','cancel','settings','controls','trigger','scroll','guide'))
        parser.add_argument('target',nargs='?')
        parser.add_argument('--from',dest='source')
        parser.add_argument('--revision')
        parser.add_argument('--content-file')
        for key in ('line','column','end-line','end-column'):parser.add_argument('--'+key,type=int,default=None)
        parser.add_argument('--wait',type=float,default=0)
    elif command=='inbox':
        parser.add_argument('action',choices=('list','get','next','start','progress','reply','fail','ack-cancel','cancel'))
        parser.add_argument('target',nargs='?')
        parser.add_argument('--agent');parser.add_argument('--claim')
        parser.add_argument('--state',choices=('queued','claimed','running','completed','failed','cancelled','interrupted'))
        parser.add_argument('--limit',type=int,default=20)
        parser.add_argument('--wait',type=float,default=0)
        parser.add_argument('--from',dest='source')
    options=vars(parser.parse_args(tokens));options['command']=command
    if not 0<=options.get('wait',0)<=30:raise ValueError('--wait must be between 0 and 30 seconds')
    if command=='events':
        if options['since']<0 or not 1<=options['limit']<=512:raise ValueError('Invalid event cursor or limit (1..512)')
        options['types']=tuple(value.strip() for value in options['types'].split(',') if value.strip())
    if command in ('ui','inbox'):
        action=options['action']
        content_file=options.get('content_file')
        if content_file:
            if action not in ('edit','review') or not options['target'] or not options.get('revision') or options['source']:
                raise ValueError('Use ui edit|review <path> --revision <revision> --content-file <utf8-file>; do not combine it with --from')
            source=Path(content_file)
            if not source.is_absolute():source=Path(cwd)/source
            if source.stat().st_size>2*1024*1024:raise ValueError('Agent replacement text exceeds 2 MiB')
            options['payload']={'path':options['target'],'revision':options['revision'],'content':source.read_text(encoding='utf-8-sig')}
        elif options.get('revision'):raise ValueError('--revision requires --content-file <utf8-file>')
        if action in ('ask','notify','edit','review','reply','fail') and not options['source'] and not content_file:
            raise ValueError(action+' requires --from <request.json>')
        if options['source']:
            if not options['source']:raise ValueError(action+' requires --from <request.json>')
            if options['source'].lstrip().startswith(('{','[')):
                raise ValueError('--from expects the path of a JSON file, not inline JSON. For file edits use <path> --revision <revision> --content-file <utf8-file>.')
            source=Path(options['source'])
            if not source.is_absolute():source=Path(cwd)/source
            if source.stat().st_size>2*1024*1024:raise ValueError('Agent request exceeds 2 MiB')
            payload=json.loads(source.read_text(encoding='utf-8-sig'))
            if not isinstance(payload,dict):raise ValueError('Agent request must be a JSON object')
            options['payload']=payload
        if action not in ('ask','notify','edit','review','read','list','next','settings','controls') and not options['target']:
            raise ValueError(action+' requires a path or request ID')
        for key in ('line','column','end_line','end_column'):
            if options.get(key) is not None and options[key]<1:raise ValueError('Line and column must be positive')
        if command=='inbox':
            if not 1<=options['limit']<=128:raise ValueError('Inbox limit must be between 1 and 128')
            if action in ('next','start','progress','reply','fail','ack-cancel') and not options['agent']:
                raise ValueError('--agent <name> is required')
            if action in ('start','progress','reply','fail','ack-cancel') and not options['claim']:
                raise ValueError('--claim <token> from inbox next is required')
    return options


def execute(bridge,options,cwd,*,on_qt_thread=False):
    ok,service=bridge.invoke('_agent_service')
    if not ok:return ok,service
    if options['command']=='events':
        result=service.events.read(options['since'],limit=options['limit'],types=options['types'],
                                   wait=0 if on_qt_thread else options['wait'])
    elif options['command']=='inbox' and options['action']=='next':
        result=service.inbox.store.next(options['agent'],0 if on_qt_thread else options['wait'])
    elif options['command']=='ui' and options['action'] in ('response','wait'):
        result=service.response(options['target'],0 if on_qt_thread else options['wait'])
    else:
        ok,result=bridge.invoke('_run_agent',options,cwd)
        if not ok:return ok,result
        if options['command']=='ui' and options.get('wait') and isinstance(result,dict) and 'id' in result and not on_qt_thread:
            result=service.response(result['id'],options['wait'])
    return True,json.dumps(result,ensure_ascii=False,indent=2)
