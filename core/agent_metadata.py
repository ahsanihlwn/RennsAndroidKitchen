# SPDX-License-Identifier: MPL-2.0
"""Normalize public provider metadata without inventing quotas or capability."""
import json
from datetime import datetime


def codex_limits(data):
    groups=[];buckets=data.get('rateLimitsByLimitId') or {}
    if not buckets and data.get('rateLimits'):buckets={'account':data['rateLimits']}
    for key,limit in buckets.items():
        windows=[]
        for name in ('primary','secondary'):
            window=limit.get(name)
            if not window or not isinstance(window.get('usedPercent'),(int,float)):continue
            reset=window.get('resetsAt')
            windows.append({'label':str(window.get('windowDurationMins') or '')+' min' if window.get('windowDurationMins') else name,
                'windowMinutes':window.get('windowDurationMins'),
                'remaining':max(0,min(100,100-window['usedPercent'])),'resetAt':reset,
                'reset':datetime.fromtimestamp(reset).astimezone().strftime('%d %b %H:%M') if reset else ''})
        if windows:groups.append({'label':limit.get('limitName') or limit.get('limitId') or key,'scope':'shared account quota','buckets':windows})
    return {'groups':groups,'quotaAvailable':bool(groups),'report':'','source':'Codex account/rateLimits/read'}


def cli_usage(provider,output):
    try:data=json.loads(output)
    except ValueError:data=None
    groups=[]
    if provider=='agy' and isinstance(data,dict):
        for group in (data.get('command') or {}).get('data',{}).get('groups',[]):
            windows=[]
            for bucket in group.get('buckets',[]):
                remaining=bucket.get('remaining_fraction')
                if isinstance(remaining,(int,float)):windows.append({'label':bucket.get('window') or bucket.get('name',''),'remaining':round(remaining*100,1),'reset':bucket.get('reset_time','')})
            if windows:groups.append({'label':group.get('name',''),'scope':group.get('description',''),'buckets':windows})
    report=(data.get('response') or data.get('result') or '') if isinstance(data,dict) else output
    if not report and data is not None:report=json.dumps(data,ensure_ascii=False,indent=2)
    return {'groups':groups,'quotaAvailable':bool(groups),'report':str(report).replace('\\n','\n')[:16000],
        'source':{'agy':'AGY /usage','claude':'Claude /usage','opencode':'OpenCode project stats'}.get(provider,provider)}


def agy_efforts(output):
    data=json.loads(output);command=data.get('command') or {};info=command.get('data') or {}
    if command.get('name')!='effort' or data.get('status')!='SUCCESS':raise ValueError('AGY did not return effort capabilities')
    return {'rows':[{'id':str(level),'label':str(level)} for level in info.get('available',[]) if isinstance(level,str)] if info.get('adjustable') else [],'current':info.get('current','')}
