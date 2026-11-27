# SPDX-License-Identifier: MPL-2.0
"""Provider-specific wire protocols, normalized for the native chat view.

These are independently authored clients of published protocols. They do not
embed provider implementations or assume TUI slash commands work over pipes.
"""
import json
from core.agent_tool_paths import extract as tool_paths
import uuid


def text_of(content):
    if isinstance(content, str): return content
    if isinstance(content, list): return '\n'.join(text_of(part) for part in content if part)
    if isinstance(content, dict): return str(content.get('text', content.get('content', '')))
    return ''


class Protocol:
    interruptible = True
    supports_steer = False

    def __init__(self, send, emit, cwd, instructions, session='', model='', *, lazy_thread=False):
        self.send=send;self.emit=emit;self.cwd=cwd;self.instructions=instructions
        self.session=session;self.model=model;self.turn='';self.serial=0
        self.pending={};self.requests={};self._part=0;self.effort='';self.lazy_thread=lazy_thread

    def steer(self, text, on_fallback=None):
        if on_fallback: on_fallback(text)

    def event(self, kind, **fields): self.emit({'type':kind, **fields})
    def rpc(self, method, params, callback=None, *, optional=False, on_error=None):
        self.serial+=1;key=self.serial
        self.pending[key]=(method,callback,optional,on_error)
        self.send({'jsonrpc':'2.0','id':key,'method':method,'params':params})
        return key

    def response(self, message):
        if 'method' in message or 'id' not in message: return False
        pending=self.pending.pop(message['id'],None)
        if pending is None:return True
        method,callback,optional,on_error=pending
        if 'error' in message:
            err=message['error']
            text=str(err.get('message',err)) if isinstance(err,dict) else str(err)
            if on_error and on_error(text,err):return True
            self.event('notice' if optional else 'error',text=method+': '+text)
        elif callback:callback(message.get('result') or {})
        return True

    def reply(self, key, result):self.send({'jsonrpc':'2.0','id':key,'result':result})
    def unsupported(self, key):self.send({'jsonrpc':'2.0','id':key,'error':{'code':-32601,'message':'Client capability is not supported'}})
    def ready(self):self.event('session',session=self.session,model=self.model);self.event('ready')
    def set_model(self, model):raise ValueError('This backend requires a new connection to select a model')
    def set_mode(self, mode):raise ValueError('This backend does not expose mode selection')
    def set_effort(self,effort):raise ValueError('This backend requires reconnecting to change thinking level')
    def refresh_limits(self):raise ValueError('This backend does not expose account limits through its live protocol')


class CodexProtocol(Protocol):
    supports_steer = True
    def __init__(self,*args,**kwargs):super().__init__(*args,**kwargs);self.items={};self._pending_prompt=None

    def start(self):
        self.rpc('initialize',{'clientInfo':{'name':'renns_kitchen','title':'Renns Android Kitchen','version':'1.0.0'}},self._initialized)

    def _thread_params(self):
        params={'cwd':self.cwd,'developerInstructions':self.instructions}
        if self.model:params['model']=self.model
        return params

    def _start_thread(self):
        self.rpc('thread/start',self._thread_params(),self._thread)

    def _on_resume_error(self, text, _raw):
        lower=text.lower()
        if any(token in lower for token in ('no rollout found','not found','unknown thread','does not exist','no such thread')):
            self.session=''
            self.event('notice',text='Previous Codex session could not be resumed ('+text+'). Started a new conversation.',resume_fallback=True)
            self._start_thread()
            return True
        return False

    def _initialized(self, result):
        self.send({'method':'initialized','params':{}})
        self.rpc('model/list',{'limit':100},self._models,optional=True)
        self.rpc('account/read',{},lambda r:self.event('account',authenticated=bool(r.get('account')),method=(r.get('account') or {}).get('type','')),optional=True)
        if self.session:
            params=self._thread_params();params['threadId']=self.session
            self.rpc('thread/resume',params,self._thread,on_error=self._on_resume_error)
        elif self.lazy_thread:
            self.ready()
        else:
            self._start_thread()

    def _models(self, result):
        self.event('models',rows=[{'id':m.get('id',m.get('model','')),'label':m.get('displayName',m.get('id','')),'description':m.get('description',''),
            'efforts':[{'id':e['reasoningEffort'],'label':e['reasoningEffort'],'description':e.get('description','')} for e in m.get('supportedReasoningEfforts',[])],
            'defaultEffort':m.get('defaultReasoningEffort',''),'default':m.get('isDefault',False)} for m in result.get('data',[]) if not m.get('hidden')])

    def _thread(self, result):
        self.session=result['thread']['id'];self.model=result.get('model',self.model)
        access=result.get('sandbox',{}).get('type','configured')
        access={'workspaceWrite':'Workspace files','readOnly':'Read only','dangerFullAccess':'Full filesystem'}.get(access,access)
        self.event('policy',text='Approval: '+str(result.get('approvalPolicy','configured'))+' · Access: '+str(access))
        if self._pending_prompt is not None:
            pending=self._pending_prompt;self._pending_prompt=None
            self.event('session',session=self.session,model=self.model)
            self.prompt(pending)
            return
        self.ready()

    def prompt(self, text):
        if not self.session and self.lazy_thread:
            self._pending_prompt=text
            self._start_thread()
            return
        params={'threadId':self.session,'input':[{'type':'text','text':text,'text_elements':[]}]}
        if self.model:params['model']=self.model
        if self.effort:params['effort']=self.effort
        def started(r):self.turn=r['turn']['id'];self.event('turn_started',turn=self.turn)
        self.rpc('turn/start',params,started)

    def steer(self, text, on_fallback=None):
        if not self.session or not self.turn:
            if on_fallback: on_fallback(text)
            return
        def fallback(_msg, _raw):
            if on_fallback:
                on_fallback(text)
                return True
            return False
        self.rpc('turn/steer',{'threadId':self.session,'expectedTurnId':self.turn,'input':[{'type':'text','text':text,'text_elements':[]}]},on_error=fallback)

    def interrupt(self):
        if self.turn:self.rpc('turn/interrupt',{'threadId':self.session,'turnId':self.turn},optional=True)

    def set_model(self, model):
        self.model=model;self.event('model',model=model)

    def set_effort(self,effort):self.effort=effort;self.event('effort',effort=effort)

    def refresh_limits(self):self.rpc('account/rateLimits/read',{},lambda r:self.event('limits',data=r),optional=True)

    def receive(self, message):
        if self.response(message):return
        method=message.get('method','');p=message.get('params') or {}
        if p.get('threadId') and self.session and p['threadId']!=self.session:return
        if 'id' in message:
            key=message['id'];self.requests[str(key)]=(method,key,p)
            if method in ('item/commandExecution/requestApproval','item/fileChange/requestApproval','item/permissions/requestApproval'):
                decisions=p.get('availableDecisions')
                detail=[]
                if p.get('reason'):detail.append(p['reason'])
                if p.get('command'):detail.append(p['command'])
                if p.get('cwd'):detail.append('Working directory: '+p['cwd'])
                item=self.items.get(p.get('itemId'),{})
                for change in item.get('changes',[]):
                    detail.append(str(change.get('path',''))+'\n'+str(change.get('diff','')))
                if p.get('permissions'):detail.append(json.dumps(p['permissions'],ensure_ascii=False,indent=2))
                if p.get('networkApprovalContext'):detail.append(json.dumps(p['networkApprovalContext'],ensure_ascii=False,indent=2))
                self.event('approval',id=str(key),title=p.get('command') or p.get('reason') or method,
                    detail='\n\n'.join(detail) or 'The backend requests permission for this operation.',options=[o for o in [{'id':'acceptForSession','kind':'save'},{'id':'accept','kind':'yes'},{'id':'decline','kind':'no'}] if decisions is None or o['id'] in decisions])
            elif method=='item/tool/requestUserInput':
                self.event('question',id=str(key),questions=p.get('questions',[]),title='Agent question')
            else:self.requests.pop(str(key),None);self.unsupported(key);self.event('notice',text='Backend requested an unsupported client action: '+method)
        elif method=='item/agentMessage/delta':self.event('text',id=p['itemId'],text=p.get('delta',''),append=True)
        elif method=='item/reasoning/summaryTextDelta':self.event('thinking',id='codex:'+p['itemId'],active=True,text=p.get('delta',''),append=True)
        elif method=='item/reasoning/summaryPartAdded':self.event('thinking',id='codex:'+p['itemId'],active=True,text='\n\n' if p.get('summaryIndex',0)>0 else '',append=True)
        elif method in ('item/started','item/completed'):
            item=p.get('item',{});kind=item.get('type','')
            if item.get('id'):self.items[item['id']]=item
            if kind=='agentMessage':
                if method=='item/completed':self.event('text',id=item['id'],text=item.get('text',''),append=False)
            elif kind=='reasoning':
                self.event('thinking',id='codex:'+item['id'],active=method=='item/started',text='\n\n'.join(item.get('summary') or []),append=False)
            elif kind not in ('userMessage','reasoning'):
                paths=tool_paths({'path':item.get('path',''),'command':item.get('command','')},cwd=item.get('cwd') or self.cwd,
                    locations=item.get('changes',[]))
                self.event('tool',id=item.get('id',kind),title=item.get('command') or kind,
                    text=item.get('aggregatedOutput') or json.dumps({k:v for k,v in item.items() if k!='id'},ensure_ascii=False,indent=2),
                    tool_kind=kind,command=item.get('command',''),cwd=item.get('cwd',''),exit_code=item.get('exitCode'),path=item.get('path') or next((c.get('path','') for c in item.get('changes',[]) if c.get('path')),''),
                    paths=paths,
                    status=item.get('status','completed' if method=='item/completed' else 'running'))
        elif method=='item/commandExecution/outputDelta':self.event('tool_delta',id=p['itemId'],text=p.get('delta',''))
        elif method=='turn/started':self.turn=p['turn']['id'];self.event('turn_started',turn=self.turn)
        elif method=='turn/completed':
            turn=p['turn'];self.event('done',status=turn.get('status','failed'),error=(turn.get('error') or {}).get('message',''));self.turn=''
        elif method=='error':self.event('notice',text=str((p.get('error') or {}).get('message',p)))
        elif method=='thread/tokenUsage/updated':self.event('usage',usage=p.get('tokenUsage',{}))
        elif method=='account/rateLimits/updated':self.event('limits',data=p)
        elif method=='serverRequest/resolved':self.event('request_resolved',id=str(p.get('requestId','')))

    def answer(self, request_id, choice, values):
        method,key,p=self.requests[request_id]
        if method=='item/tool/requestUserInput':result={'answers':{k:{'answers':[str(v)]} for k,v in values.items()}}
        elif method=='item/permissions/requestApproval':
            if choice not in ('accept','acceptForSession','decline'):raise ValueError('Unknown permission decision')
            result={'permissions':p.get('permissions',{}) if choice in ('accept','acceptForSession') else {},'scope':'session' if choice=='acceptForSession' else 'turn'}
        else:
            allowed=p.get('availableDecisions')
            if choice not in ('accept','acceptForSession','decline','cancel') or allowed is not None and choice not in allowed:raise ValueError('This decision is not available for the request')
            result={'decision':choice}
        self.reply(key,result)
        self.requests.pop(request_id,None)


class AcpProtocol(Protocol):
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs);self.capabilities={};self.configs=[];self._message=0;self.mode=''

    def start(self):
        self.rpc('initialize',{'protocolVersion':1,'clientInfo':{'name':'renns_kitchen','version':'1.0.0'},
            'clientCapabilities':{'fs':{'readTextFile':False,'writeTextFile':False},'terminal':False}},self._initialized)

    def _on_load_error(self,text,_raw):
        if self.session:
            self.session=''
            self.event('notice',text='Previous ACP session could not be loaded ('+text+'). Started a new session.',resume_fallback=True)
            self.rpc('session/new',{'cwd':self.cwd,'mcpServers':[]},self._session)
            return True
        return False

    def _initialized(self,result):
        self.capabilities=result.get('agentCapabilities',{})
        params={'cwd':self.cwd,'mcpServers':[]}
        if self.session and self.capabilities.get('loadSession'):
            params['sessionId']=self.session
            self.rpc('session/load',params,self._session,on_error=self._on_load_error)
        else:
            if self.session:self.event('notice',text='This ACP backend cannot load the previous session. A new conversation was created.',resume_fallback=True)
            self.rpc('session/new',params,self._session)

    def _session(self,result):
        self.session=result.get('sessionId',self.session);self._configuration(result)
        if self.mode:
            try:self.set_mode(self.mode)
            except Exception:pass
        self.ready()

    def _configuration(self,result):
        def clean_label(val):
            return val[9:] if isinstance(val,str) and val.lower().startswith('opencode/') else val
        self.configs=result.get('configOptions',self.configs)
        models=result.get('models') or {}
        rows=[{'id':m['modelId'],'label':clean_label(m.get('name') or m['modelId'])} for m in models.get('availableModels',[])]
        self.model=models.get('currentModelId',self.model)
        for option in self.configs:
            if option.get('category')=='model' or option.get('id')=='model':
                rows=[]
                for item in option.get('options',[]):
                    for entry in item.get('options',[item]):
                        if 'value' in entry:rows.append({'id':entry['value'],'label':clean_label(entry.get('name') or entry['value'])})
                self.model=option.get('currentValue',self.model)
            elif option.get('category')=='mode' or option.get('id')=='mode':
                modes=[{'id':m['value'],'label':m.get('name',m['value'])} for m in option.get('options',[]) if 'value' in m]
                if modes:self.event('modes',rows=modes,current=option.get('currentValue',''))
            elif option.get('category')=='thought_level' or option.get('id') in ('effort','variant'):
                options=[e for item in option.get('options',[]) for e in item.get('options',[item]) if 'value' in e]
                self.event('efforts',rows=[{'id':e['value'],'label':e.get('name',e['value'])} for e in options],current=option.get('currentValue',''))
        if rows:self.event('models',rows=rows)
        modes=result.get('modes') or {}
        if modes.get('availableModes'):self.event('modes',rows=[{'id':m['id'],'label':m.get('name',m['id'])} for m in modes['availableModes']],current=modes.get('currentModeId',''))
        self.event('model',model=self.model)

    def prompt(self,text):
        self._message+=1;self._part=0;self.turn=str(self._message)
        def done(r):
            reason=r.get('stopReason','')
            self.event('done',status='interrupted' if reason=='cancelled' else 'failed' if reason in ('refusal','max_turn_requests','max_tokens') else 'completed',error=reason if reason not in ('end_turn','cancelled') else '')
        self.rpc('session/prompt',{'sessionId':self.session,'prompt':[{'type':'text','text':text}]},done)
        self.event('turn_started',turn=self.turn)

    def interrupt(self):self.send({'jsonrpc':'2.0','method':'session/cancel','params':{'sessionId':self.session}})

    def set_model(self,model):
        config=next((c for c in self.configs if c.get('category')=='model' or c.get('id')=='model'),None)
        def done(r):self.model=model;self._configuration(r);self.event('model',model=model)
        if config:self.rpc('session/set_config_option',{'sessionId':self.session,'configId':config['id'],'value':model},done)
        else:self.rpc('session/set_model',{'sessionId':self.session,'modelId':model},done)

    def set_mode(self,mode):
        self.mode=mode
        config=next((c for c in self.configs if c.get('category')=='mode' or c.get('id')=='mode'),None)
        def done(r):
            if isinstance(r,dict) and ('configOptions' in r or 'modes' in r):self._configuration(r)
            self.event('mode',mode=mode)
        if config:self.rpc('session/set_config_option',{'sessionId':self.session,'configId':config['id'],'value':mode},done)
        else:self.rpc('session/set_mode',{'sessionId':self.session,'modeId':mode},done)

    def set_effort(self,effort):
        config=next((c for c in self.configs if c.get('category')=='thought_level' or c.get('id') in ('effort','variant')),None)
        if not config:raise ValueError('This model does not advertise thinking levels')
        def done(r):self.effort=effort;self._configuration(r);self.event('effort',effort=effort)
        self.rpc('session/set_config_option',{'sessionId':self.session,'configId':config['id'],'value':effort},done)

    def receive(self,message):
        if self.response(message):return
        method=message.get('method');p=message.get('params') or {}
        if p.get('sessionId') and self.session and p['sessionId']!=self.session:return
        if 'id' in message:
            if method=='session/request_permission':
                key=message['id'];self.requests[str(key)]=(key,p)
                call=p.get('toolCall',{})
                self.event('approval',id=str(key),title=call.get('title','Tool permission'),detail=json.dumps({k:v for k,v in call.items() if k not in ('toolCallId','title')},ensure_ascii=False,indent=2),
                    options=[{'id':o['optionId'],'kind':{'allow_always':'save','allow_once':'yes','reject_once':'no'}[o['kind']]} for o in p.get('options',[]) if o.get('kind') in ('allow_always','allow_once','reject_once')])
            else:self.unsupported(message['id'])
            return
        if method!='session/update':return
        u=p.get('update') or {};kind=u.get('sessionUpdate')
        if kind=='agent_thought_chunk':self.event('thinking',id=f'acp:{self.turn}:{self._part}',active=True,text=text_of(u.get('content')),append=True)
        elif kind=='agent_message_chunk':self.event('text',id=f'acp:{self.turn}:{self._part}',text=text_of(u.get('content')),append=True)
        elif kind in ('tool_call','tool_call_update'):
            if kind=='tool_call':self._part+=1
            raw=u.get('rawInput') or {};raw=raw if isinstance(raw,dict) else {}
            paths=tool_paths(raw,cwd=self.cwd,locations=u.get('locations',[]))
            self.event('tool',id=u['toolCallId'],title=u.get('title',''),text=text_of(u.get('content')) or (json.dumps(u['rawInput'],ensure_ascii=False,indent=2) if 'rawInput' in u else ''),status=u.get('status','running'),tool_kind=u.get('kind',''),command=raw.get('command',''),
                path=paths[0] if paths else '',paths=paths)
        elif kind=='available_commands_update':self.event('commands',rows=[{'name':c['name'],'description':c.get('description','')} for c in u.get('availableCommands',[])])
        elif kind=='config_option_update':self._configuration(u)
        elif kind=='current_mode_update':self.event('mode',mode=u.get('currentModeId',''))
        elif kind=='usage_update':self.event('usage',usage=u)
        elif kind=='plan':self.event('tool',id='plan:'+self.turn,title='Plan',text='\n'.join(e.get('content','') for e in u.get('entries',[])),status='running')

    def answer(self,request_id,choice,values):
        key,p=self.requests[request_id]
        valid={o['optionId'] for o in p.get('options',[])}
        if choice not in valid:raise ValueError('Permission choice is no longer available')
        self.reply(key,{'outcome':{'outcome':'selected','optionId':choice}})
        self.requests.pop(request_id,None)


class ClaudeProtocol(Protocol):
    supports_steer = True
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self.message_id='claude';self._turn_serial=0;self._thinking_blocks={};self._tool_blocks={}
        self._streamed_text_messages=set()

    def _tool_use(self,block):
        inputs=block.get('input') or {};inputs=inputs if isinstance(inputs,dict) else {}
        paths=tool_paths(inputs,cwd=self.cwd)
        self.event('tool',id=block['id'],title=block.get('name','Tool'),tool_kind=block.get('name',''),command=inputs.get('command',''),
            path=paths[0] if paths else '',paths=paths,text=json.dumps(inputs,ensure_ascii=False,indent=2),status='running')

    def control(self,subtype,callback=None,**fields):
        key=uuid.uuid4().hex;self.pending[key]=(subtype,callback,False)
        self.send({'type':'control_request','request_id':key,'request':{'subtype':subtype,**fields}})

    def start(self):self.control('initialize',self._initialized,hooks={})

    def _initialized(self,result):
        self.event('models',rows=[{'id':m['value'],'resolved':m.get('resolvedModel',''),'label':m.get('display_name',m.get('displayName',m['value'])),
            'description':m.get('description',''),'efforts':[{'id':level,'label':level} for level in m.get('supportedEffortLevels',[]) if m.get('supportsEffort',True)]} for m in result.get('models',[]) if 'value' in m])
        self.event('commands',rows=[{'name':c['name'],'description':c.get('description','')} for c in result.get('commands',[])])
        self.ready()

    def prompt(self,text):
        self._turn_serial+=1;self.turn=str(self._turn_serial)
        self._streamed_text_messages.clear()
        self.send({'type':'user','session_id':self.session,'message':{'role':'user','content':text},'parent_tool_use_id':None})
        self.event('turn_started',turn=self.turn)

    def steer(self,text,on_fallback=None):
        if not self.session and not self.turn:
            if on_fallback:on_fallback(text)
            return
        self.send({'type':'user','session_id':self.session,'message':{'role':'user','content':text},'parent_tool_use_id':None})

    def interrupt(self):self.control('interrupt')
    def set_model(self,model):
        def done(r):self.model=model;self.event('model',model=model)
        self.control('set_model',done,model=model)

    def receive(self,message):
        kind=message.get('type')
        if kind=='control_response':
            response=message.get('response') or {};pending=self.pending.pop(response.get('request_id'),None)
            if pending:
                method,callback,_=pending
                if response.get('subtype')=='error':self.event('error',text=method+': '+str(response.get('error','Unknown control error')))
                elif callback:callback(response.get('response') or {})
        elif kind=='control_request':
            key=message['request_id'];request=message.get('request') or {};self.requests[key]=request
            if request.get('subtype')=='can_use_tool':
                if request.get('tool_name')=='AskUserQuestion':
                    questions=[]
                    for i,q in enumerate(request.get('input',{}).get('questions',[])):
                        questions.append({**q,'id':str(i)})
                    self.event('question',id=key,title='Agent question',questions=questions)
                else:self.event('approval',id=key,title=request.get('tool_name','Tool permission'),detail=json.dumps(request.get('input',{}),ensure_ascii=False,indent=2),options=([{'id':'save','kind':'save'}] if self._saved_rules(request) else [])+[{'id':'accept','kind':'yes'},{'id':'decline','kind':'no'}])
            else:
                self.requests.pop(key,None)
                self.send({'type':'control_response','response':{'subtype':'error','request_id':key,'error':'Unsupported host control request'}})
        elif kind=='system' and message.get('subtype')=='init':
            self.session=message.get('session_id',self.session);self.model=message.get('model',self.model)
            self.event('session',session=self.session,model=self.model)
            self.event('policy',text='Permissions: '+message.get('permissionMode','configured'))
        elif kind=='stream_event':
            event=message.get('event') or {};etype=event.get('type');index=event.get('index',0)
            if etype=='message_start':self.message_id=event.get('message',{}).get('id',self.message_id);self._tool_blocks.clear()
            elif etype=='content_block_delta' and event.get('delta',{}).get('type')=='text_delta':
                self._streamed_text_messages.add(self.message_id)
                self.event('text',id=self.message_id+':'+str(index),text=event['delta'].get('text',''),append=True)
            elif etype=='content_block_delta' and event.get('delta',{}).get('type')=='input_json_delta' and index in self._tool_blocks:
                block=self._tool_blocks[index];block['partial']=(block['partial']+event['delta'].get('partial_json',''))[-2*1024*1024:]
            elif etype=='content_block_start':
                block=event.get('content_block') or {}
                if block.get('type')=='thinking':
                    key=self.message_id+':'+str(index);self._thinking_blocks[index]=key
                    self.event('thinking',id=key,active=True,text=block.get('summary',''),append=False)
                elif block.get('type')=='tool_use':self._tool_blocks[index]={'block':block,'partial':''};self._tool_use(block)
            elif etype=='content_block_stop' and index in self._thinking_blocks:self.event('thinking',id=self._thinking_blocks.pop(index),active=False,text='',append=False)
            elif etype=='content_block_stop' and index in self._tool_blocks:
                pending=self._tool_blocks.pop(index);block=pending['block']
                if pending['partial']:
                    try:block={**block,'input':json.loads(pending['partial'])}
                    except ValueError:pass
                self._tool_use(block)
        elif kind=='assistant':
            m=message.get('message') or {}
            msg_id=m.get('id',self.message_id)
            already_streamed=msg_id in self._streamed_text_messages
            for index,block in enumerate(m.get('content',[])):
                if block.get('type')=='text' and not message.get('parent_tool_use_id'):
                    if not already_streamed:
                        self.event('text',id=msg_id+':'+str(index),text=block.get('text',''),append=False)
                elif block.get('type')=='tool_use':self._tool_use(block)
        elif kind=='user':
            for block in (message.get('message') or {}).get('content',[]):
                if isinstance(block,dict) and block.get('type')=='tool_result':self.event('tool',id=block['tool_use_id'],title='',text=text_of(block.get('content')),status='failed' if block.get('is_error') else 'completed')
        elif kind=='result':
            self.session=message.get('session_id',self.session);self.event('session',session=self.session,model=self.model)
            self.event('usage',usage=message.get('usage') or {})
            failed=bool(message.get('is_error')) or message.get('subtype','success')!='success'
            error='\n'.join(map(str,message.get('errors') or [])) or message.get('result','') if failed else ''
            self.event('done',status='failed' if failed else 'completed',error=error,answer=message.get('result',''))

    @staticmethod
    def _saved_rules(request):
        return [{**s,'destination':'session'} for s in request.get('permission_suggestions',[]) if isinstance(s,dict) and s.get('type')=='addRules' and s.get('behavior')=='allow' and s.get('rules')]

    def answer(self,request_id,choice,values):
        request=self.requests[request_id]
        if request.get('tool_name')=='AskUserQuestion':
            data=dict(request.get('input',{}));questions=data.get('questions',[])
            data['answers']={q['question']:str(values.get(str(i),'')) for i,q in enumerate(questions)}
            response={'behavior':'allow','updatedInput':data}
        elif choice in ('accept','save'):
            response={'behavior':'allow','updatedInput':request.get('input',{})}
            if choice=='save':
                rules=self._saved_rules(request)
                if not rules:raise ValueError('This request does not offer a permission to remember')
                response['updatedPermissions']=rules
        elif choice=='decline':response={'behavior':'deny','message':'User denied this request in Kitchen'}
        else:raise ValueError('Unknown permission decision')
        self.send({'type':'control_response','response':{'subtype':'success','request_id':request_id,'response':response}})
        self.requests.pop(request_id,None)


class AgyProtocol(Protocol):
    interruptible=False

    def start(self):
        self._initialized=False
        self.event('policy',text='AGY unrestricted: tool permission requests are approved automatically for this connection. Stream turn interruption is unavailable.')

    def prompt(self,text):
        if not getattr(self,'_initialized',False):raise ValueError('Wait for the AGY workspace handshake before submitting a prompt')
        self.send({'event':'user','message':{'content':text}});self.event('turn_started',turn='agy')
    def interrupt(self):raise ValueError('AGY cannot interrupt through this protocol; finish the current turn before stopping')

    def receive(self,message):
        kind=message.get('event')
        if kind=='init':
            reported=(message.get('init') or {}).get('cwd')
            if reported:
                from core.agent_workspace import path_key
                if path_key(reported)!=path_key(self.cwd):
                    self._initialized=False
                    self.event('workspace_error',text='AGY reported a different working directory: '+reported+'; expected '+self.cwd+'. No prompt was submitted.')
                    return
            self.session=message.get('conversation_id',self.session);self.model=(message.get('init') or {}).get('model',self.model)
            self.event('session',session=self.session,model=self.model)
            if not getattr(self,'_initialized',False):self._initialized=True;self.ready()
        elif kind=='step_update':
            step=message.get('step_update') or {};key=str(step.get('step_index',0))
            if step.get('step_type')=='agent_response' and step.get('text_delta'):self.event('text',id='agy:'+key,text=step['text_delta'],append=True)
            elif step.get('step_type')=='tool':
                info=step.get('tool_info') or {}
                inputs=info.get('parameters') or info.get('input') or info.get('arguments') or {}
                if isinstance(inputs,str):
                    try:inputs=json.loads(inputs)
                    except ValueError:inputs={}
                name=step.get('tool_name',info.get('name','Tool'))
                command=(inputs.get('command') or inputs.get('CommandLine') or '') if isinstance(inputs,dict) else ''
                paths=tool_paths(inputs,cwd=self.cwd)
                self.event('tool',id='agy-tool:'+key,title=step.get('tool_name',info.get('name','Tool')),
                    tool_kind=name,command=command,exit_code=info.get('exit_code'),path=paths[0] if paths else '',paths=paths,
                    text=json.dumps(info,ensure_ascii=False,indent=2),status='failed' if info.get('error') or step.get('state')=='ERROR' else 'completed' if step.get('state')=='DONE' else 'running')
        elif kind=='result':
            result=message.get('result') or {};self.session=result.get('conversation_id',self.session)
            self.event('session',session=self.session,model=self.model);self.event('usage',usage=result.get('usage') or {})
            status=result.get('status','ERROR')
            answer=result.get('response') or ''
            denied=result.get('denied_actions') or []
            failure='permission_denied' if denied and not answer.strip() else ''
            error=str(result.get('error',''))
            if failure:error='Antigravity did not return an answer because a required action was denied permission.'
            self.event('done',status='failed' if failure else {'SUCCESS':'completed','CANCELED':'interrupted','INTERRUPTED':'interrupted'}.get(status,'failed'),error=error,answer=answer,failure_kind=failure)


PROTOCOLS={'codex':CodexProtocol,'acp':AcpProtocol,'claude':ClaudeProtocol,'agy':AgyProtocol}
