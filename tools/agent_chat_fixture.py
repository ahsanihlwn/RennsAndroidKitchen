# SPDX-License-Identifier: MPL-2.0
"""Inert Codex-shaped sidecar for the native chat integration check."""
import json
import os
import sys


def send(data):print(json.dumps(data,ensure_ascii=False),flush=True)
def reply(key,result):send({'id':key,'result':result})
def event(method,params):send({'method':method,'params':{'threadId':'fixture-thread',**params}})
def begin_inspection(turn):
    event('item/started',{'turnId':turn,'item':{'id':'tool-'+turn,'type':'commandExecution','command':'fixture inspect','status':'inProgress'}})
    send({'id':900+serial,'method':'item/commandExecution/requestApproval','params':{'threadId':'fixture-thread','turnId':turn,'itemId':'tool-'+turn,'command':'fixture inspect','reason':'Read-only fixture permission'}})
if '--fixture-agy' in sys.argv:
    if 'models' in sys.argv:print('fixture-model\tFixture model');sys.exit(0)
    if '/effort' in sys.argv:
        send({'status':'SUCCESS','command':{'name':'effort','data':{'adjustable':True,'current':'medium','available':['low','medium','high']}}});sys.exit(0)
    if '/usage' in sys.argv:
        send({'status':'SUCCESS','response':'Fixture usage report','command':{'name':'usage','data':{'groups':[{'name':'Fixture shared quota','buckets':[{'name':'5h','remaining_fraction':.75,'reset_time':'2030-01-01T00:00:00Z'}]}]}}});sys.exit(0)
    send({'event':'init','conversation_id':'agy-fixture','init':{'model':'fixture-model','cwd':os.getcwd()}})
    for line in sys.stdin:
        data=json.loads(line)
        if data.get('event')=='user':
            if 'Fixture instant read probe' in data.get('message',{}).get('content',''):
                from pathlib import Path
                path=Path.cwd()/'agent_workspace_probe.txt';path.read_text(encoding='utf-8')
                for state in ('RUNNING','DONE'):
                    send({'event':'step_update','step_update':{'step_index':3,'step_type':'tool','tool_name':'read_file','state':state,'tool_info':{'parameters':{'Path':str(path)}}}})
                send({'event':'result','result':{'conversation_id':'agy-fixture','status':'SUCCESS','response':'Instant read complete'}})
                continue
            if 'Fixture workspace path probe' in data.get('message',{}).get('content',''):
                from pathlib import Path
                path=Path.cwd()/'agent_workspace_probe.txt';path.write_text(str(Path.cwd()),encoding='utf-8')
                send({'event':'step_update','step_update':{'step_index':1,'step_type':'tool','tool_name':'write_to_file','state':'DONE','tool_info':{'parameters':{'Path':str(path)}}}})
                send({'event':'result','result':{'conversation_id':'agy-fixture','status':'SUCCESS','response':'Fixture wrote '+str(path)}})
                continue
            if 'Hang until stopped' in data.get('message',{}).get('content',''):
                import time
                while True:time.sleep(60)
            send({'event':'result','result':{'conversation_id':'agy-fixture','status':'SUCCESS','response':'AGY fixture reply'}})
    sys.exit(0)
pending=None;serial=0
for line in sys.stdin:
    data=json.loads(line);method=data.get('method','');key=data.get('id')
    if method=='initialize':reply(key,{'userAgent':'RAK fixture'})
    elif method=='model/list':reply(key,{'data':[{'id':'fixture-model','displayName':'Fixture model','defaultReasoningEffort':'low','supportedReasoningEfforts':[{'reasoningEffort':'low'},{'reasoningEffort':'high'}]}]})
    elif method=='account/rateLimits/read':reply(key,{'rateLimits':{'limitId':'fixture-shared','primary':{'usedPercent':25,'windowDurationMins':300}}})
    elif method=='account/read':reply(key,{'account':{'type':'fixture'}})
    elif method in ('thread/start','thread/resume'):reply(key,{'thread':{'id':'fixture-thread'},'model':'fixture-model','approvalPolicy':'on-request','sandbox':{'type':'workspaceWrite'}})
    elif method=='turn/start':
        serial+=1;turn='turn-'+str(serial);pending=turn
        reply(key,{'turn':{'id':turn,'status':'inProgress'}})
        if '--fixture-thinking' in sys.argv and serial==1:
            event('item/started',{'item':{'id':'reasoning-'+turn,'type':'reasoning','summary':[]}})
            event('item/reasoning/summaryTextDelta',{'itemId':'reasoning-'+turn,'summaryIndex':0,'delta':'Checking the selected property before running the read-only inspection.'})
        else:begin_inspection(turn)
    elif method=='fixture/continue' and pending:
        event('item/completed',{'item':{'id':'reasoning-'+pending,'type':'reasoning','summary':['Checking the selected property before running the read-only inspection.']}})
        begin_inspection(pending)
    elif method=='turn/interrupt':
        reply(key,{})
        if pending:event('turn/completed',{'turn':{'id':pending,'status':'interrupted'}});pending=None
    elif method=='initialized':pass
    elif key and 'result' in data and pending:
        event('serverRequest/resolved',{'requestId':key})
        decision=data['result'].get('decision')
        if decision in ('accept','acceptForSession'):
            event('item/completed',{'item':{'id':'tool-'+pending,'type':'commandExecution','command':'fixture inspect','status':'completed','aggregatedOutput':'Read-only check complete ✓','exitCode':0}})
            event('item/started',{'item':{'id':'check-'+pending,'type':'reasoning','summary':[]}})
            event('item/completed',{'item':{'id':'check-'+pending,'type':'reasoning','summary':['Checking the inspection output.']}})
            event('item/completed',{'item':{'id':'summary-'+pending,'type':'commandExecution','command':'fixture summarize','status':'completed','aggregatedOutput':'Read-only fixture summary','exitCode':0}})
            event('item/agentMessage/delta',{'itemId':'answer-'+pending,'delta':'Selection received. '})
            event('item/agentMessage/delta',{'itemId':'answer-'+pending,'delta':'Unsaved buffer preserved ✓'})
            event('item/completed',{'item':{'id':'answer-'+pending,'type':'agentMessage','text':'Selection received. Unsaved buffer preserved ✓'}})
            event('turn/completed',{'turn':{'id':pending,'status':'completed'}})
        else:event('turn/completed',{'turn':{'id':pending,'status':'failed','error':{'message':'Permission denied'}}})
        pending=None
