# SPDX-License-Identifier: MPL-2.0
"""CLI projection of the same live activity and history shown by the workspace."""
import json
import shlex


COMMANDS=('activity','log','logs')
HELP=('usage: rak activity [list|status|show <id>|clear --yes] '
      '[--json] [--failed] [--limit N]')


def parse_options(text):
    tokens=[token.strip('"\'') for token in shlex.split(text,posix=False)]
    options={'action':'list','json':False,'failed':False,'limit':20,'yes':False}
    positional=[];index=0
    while index<len(tokens):
        token=tokens[index]
        if token in ('--json','--failed','--yes'):options[token[2:]]=True
        elif token=='--limit':
            index+=1
            if index>=len(tokens):raise ValueError('--limit requires a number')
            options['limit']=int(tokens[index])
            if not 1<=options['limit']<=200:raise ValueError('--limit must be between 1 and 200')
        elif token in ('--help','-h','help'):return {'help':True}
        elif token.startswith('-'):raise ValueError('Unknown activity option: '+token)
        else:positional.append(token)
        index+=1
    if positional:options['action']=positional[0].lower()
    if options['action'] not in ('list','status','show','clear'):raise ValueError(HELP)
    if options['action']=='show':
        if len(positional)!=2:raise ValueError('usage: rak activity show <id> [--json]')
        options['id']=positional[1]
    elif len(positional)>1:raise ValueError(HELP)
    if options['action']=='clear' and not options['yes']:raise ValueError('Use rak activity clear --yes to clear completed history')
    return options


def collect(owner,options):
    tasks=getattr(owner,'_tasks',None)
    if tasks is None:raise ValueError('Activity API is unavailable in this Kitchen session')
    snapshot=tasks.activity_snapshot()
    snapshot.update(version=1,project=getattr(owner,'project',''))
    android=getattr(owner,'_android_tools',None)
    if android is not None:
        snapshot['android']={'mode':android.mode,'running':android.running,'progress':android.flashProgress,
                             'status':android.status,'queue':android.queue.snapshot()}
    action=options['action']
    if action=='clear':
        count=len(snapshot['history']);tasks.clear_history()
        if hasattr(owner,'_history'):owner._history=[]
        if hasattr(owner,'statusChanged'):owner.statusChanged.emit()
        return {'version':1,'project':snapshot['project'],'cleared':count,'busy':tasks.busy}
    if action=='show':
        entry=next((row for row in [*snapshot['tasks'],*snapshot['history']] if row['id']==options['id']),None)
        if entry is None:raise ValueError('Activity not found: '+options['id'])
        return {'version':1,'project':snapshot['project'],'entry':entry}
    snapshot['history_total']=len(snapshot['history'])
    history=snapshot['history']
    if options['failed']:history=[row for row in history if not row['ok']]
    snapshot['history']=[] if action=='status' else history[:options['limit']]
    return snapshot


def render(snapshot,as_json=False):
    if as_json:return json.dumps(snapshot,ensure_ascii=False,indent=2)
    if 'cleared' in snapshot:return f"Cleared {snapshot['cleared']} completed activity entries."
    lines=['Project: '+snapshot.get('project','')]
    if 'entry' in snapshot:
        row=snapshot['entry']
        lines.extend([row['id']+'  '+row.get('label',row.get('name','')),
                      'State: '+row.get('state','done' if row.get('ok') else 'failed')])
        for key in ('stage','detail','output'):
            if row.get(key) and row[key] not in lines:lines.append(str(row[key]))
    else:
        lines.append('State: '+snapshot['state']+(' · '+snapshot['status'] if snapshot['status'] else ''))
        if snapshot['halt_reason']:lines.append(snapshot['halt_reason'])
        for row in snapshot['tasks']:
            percent=f" {row['progress']}%" if row['progress']>=0 else ''
            lines.append(f"{row['id']}  {row['state'].upper():7}  {row['label']}{percent}"+(' · '+row['stage'] if row['stage'] else ''))
        for row in snapshot['history']:
            lines.append(f"{row['id']}  {'OK' if row['ok'] else 'FAILED':7}  {row['name']}  {row['time']}  {row['elapsed']:.1f}s")
            if row.get('detail'):lines.extend('  '+line for line in row['detail'].splitlines())
        if not snapshot['tasks'] and not snapshot['history']:lines.append('No activity entries.')
    return '\n'.join(lines)
