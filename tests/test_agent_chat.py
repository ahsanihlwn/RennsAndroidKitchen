# SPDX-License-Identifier: MPL-2.0
from copy import deepcopy
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from PySide6.QtCore import QObject,Signal,QCoreApplication
from core import agent_backends
from core.agent_chat_store import ChatStore
from core.agent_events import AgentEvents
from core.agent_inbox import AgentInbox
from core.agent_protocols import CodexProtocol,AcpProtocol,ClaudeProtocol,AgyProtocol
from core.agent_chat_presentation import transcript
from core.command_context import CommandFailure
from core.error_reporting import register_error_presenter, unregister_error_presenter
from ui.agent_chat import WorkspaceAgentChat


class ProtocolTests(unittest.TestCase):
    def setup_protocol(self,cls):
        self.sent=[];self.events=[]
        return cls(self.sent.append,self.events.append,os.path.abspath('.'),'Kitchen guidance')
    def rpc_result(self,protocol,method,result):
        request=next(r for r in reversed(self.sent) if r.get('method')==method)
        protocol.receive({'id':request['id'],'result':result})

    def test_discovery_does_not_execute_a_shell(self):
        with patch('core.agent_backends.resolve_cli',return_value=None):self.assertEqual(agent_backends.discover(),[])

    def test_npm_wrapper_resolves_to_native_binary(self):
        if os.name!='nt':self.skipTest('Windows shim resolution')
        with tempfile.TemporaryDirectory() as root:
            root=Path(root);shim=root/'claude.cmd';shim.write_text('fixture')
            native=root/'node_modules/@anthropic-ai/claude-code/bin/claude.exe';native.parent.mkdir(parents=True);native.write_bytes(b'fixture')
            self.assertEqual(agent_backends.resolve_cli('claude',lambda _:str(shim)),[str(native)])

    def test_agy_models_accept_tabs_and_never_parse_the_progress_banner(self):
        self.assertEqual(agent_backends.parse_agy_models('Fetching available models...\ngemini-test\tGemini Test\nother-model  Other Model\n'),
            [{'id':'gemini-test','label':'Gemini Test'},{'id':'other-model','label':'Other Model'}])
        for protocol in ('codex','acp','claude'):
            self.assertNotIn('--dangerously-skip-permissions',agent_backends.launch_arguments({'protocol':protocol}))

    def test_agy_models_collapse_effort_suffixes_into_single_base_model(self):
        raw = (
            "Fetching available models...\n"
            "gemini-3.8-flash-high\tGemini 3.8 Flash (High)\n"
            "gemini-3.8-flash-medium\tGemini 3.8 Flash (Medium)\n"
            "gemini-3.8-flash-low\tGemini 3.8 Flash (Low)\n"
            "claude-sonnet-4-6\tClaude Sonnet 4.6 (Thinking)\n"
            "claude-opus-4-6-thinking\tClaude Opus 4.6 (thinking)\n"
            "gpt-oss-120b-medium\tGPT-OSS 120B (Medium)\n"
        )
        parsed = agent_backends.parse_agy_models(raw)
        self.assertEqual(len(parsed), 4)
        self.assertEqual(parsed[0], {
            'id': 'gemini-3.8-flash',
            'label': 'Gemini 3.8 Flash',
            'efforts': [
                {'id': 'low', 'label': 'low'},
                {'id': 'medium', 'label': 'medium'},
                {'id': 'high', 'label': 'high'},
            ]
        })
        self.assertEqual(parsed[1], {'id': 'claude-sonnet-4-6', 'label': 'Claude Sonnet 4.6'})
        self.assertEqual(parsed[2], {'id': 'claude-opus-4-6-thinking', 'label': 'Claude Opus 4.6'})
        self.assertEqual(parsed[3], {
            'id': 'gpt-oss-120b',
            'label': 'GPT-OSS 120B',
            'efforts': [{'id': 'medium', 'label': 'medium'}],
        })

    def test_codex_handshake_model_list_and_authoritative_failure(self):
        p=self.setup_protocol(CodexProtocol);p.start();self.rpc_result(p,'initialize',{})
        self.rpc_result(p,'model/list',{'data':[{'id':'test-model','displayName':'Test'}]})
        self.rpc_result(p,'thread/start',{'thread':{'id':'thread'},'model':'test-model','sandbox':{'type':'workspaceWrite'}})
        self.assertEqual(p.session,'thread');self.assertTrue(any(e['type']=='ready' for e in self.events))
        p.prompt('hello');self.rpc_result(p,'turn/start',{'turn':{'id':'turn'}})
        p.receive({'method':'turn/completed','params':{'threadId':'thread','turn':{'id':'turn','status':'failed','error':{'message':'Tool failed'}}}})
        self.assertEqual(self.events[-1],{'type':'done','status':'failed','error':'Tool failed'})

    def test_codex_effort_and_limits_use_native_rpc_without_model_prompts(self):
        p=self.setup_protocol(CodexProtocol);p.session='thread';p.set_effort('high');p.prompt('hello')
        self.assertEqual(self.sent[-1]['params']['effort'],'high')
        p.refresh_limits();self.assertEqual(self.sent[-1]['method'],'account/rateLimits/read')
        self.rpc_result(p,'account/rateLimits/read',{'rateLimits':{'primary':{'usedPercent':25}}})
        self.assertEqual(self.events[-1]['type'],'limits')

    def test_codex_resume_no_rollout_falls_back_to_new_thread(self):
        p=self.setup_protocol(CodexProtocol);p.session='01a1f2b9-3f10-7701-9bc9-b5e5adc98121'
        p.start();self.rpc_result(p,'initialize',{})
        resume_req=next(r for r in self.sent if r.get('method')=='thread/resume')
        p.receive({'id':resume_req['id'],'error':{'code':-32600,'message':'no rollout found for thread id 01a1f2b9-3f10-7701-9bc9-b5e5adc98121'}})
        self.assertEqual(p.session,'')
        self.assertTrue(any(e['type']=='notice' and 'no rollout found' in e['text'] for e in self.events))
        self.assertFalse(any(e['type']=='error' for e in self.events))
        self.rpc_result(p,'thread/start',{'thread':{'id':'new-thread'},'model':'test-model','sandbox':{'type':'workspaceWrite'}})
        self.assertEqual(p.session,'new-thread')
        self.assertTrue(any(e['type']=='ready' for e in self.events))

    def test_codex_resume_non_missing_error_still_fails(self):
        p=self.setup_protocol(CodexProtocol);p.session='stale-thread'
        p.start();self.rpc_result(p,'initialize',{})
        resume_req=next(r for r in self.sent if r.get('method')=='thread/resume')
        p.receive({'id':resume_req['id'],'error':{'code':-32000,'message':'Unauthorized'}})
        self.assertEqual(self.events[-1],{'type':'error','text':'thread/resume: Unauthorized'})
        self.assertFalse(any(r.get('method')=='thread/start' for r in self.sent))

    def test_acp_thinking_levels_are_acknowledged_by_config_option(self):
        p=self.setup_protocol(AcpProtocol);p.session='s'
        p._configuration({'configOptions':[{'id':'effort','category':'thought_level','currentValue':'default','options':[{'value':'low','name':'Low'},{'value':'high','name':'High'}]}]})
        self.assertEqual(next(e for e in self.events if e['type']=='efforts')['rows'],[{'id':'low','label':'Low'},{'id':'high','label':'High'}])
        p.set_effort('high');self.assertEqual(self.sent[-1]['params'],{'sessionId':'s','configId':'effort','value':'high'})
        self.rpc_result(p,'session/set_config_option',{});self.assertEqual(self.events[-1],{'type':'effort','effort':'high'})

    def test_acp_models_strip_opencode_prefix_from_labels(self):
        p=self.setup_protocol(AcpProtocol);p.session='s'
        p._configuration({
            'models':{
                'availableModels':[{'modelId':'opencode/Beam Free','name':'opencode/Beam Free'}],
                'currentModelId':'opencode/Beam Free'
            },
            'configOptions':[{
                'id':'model',
                'category':'model',
                'currentValue':'opencode/Flint 40B Free',
                'options':[
                    {'value':'opencode/Flint 40B Free','name':'opencode/Flint 40B Free'},
                    {'value':'opencode/Big Pickle','name':'opencode/Big Pickle'},
                ]
            }]
        })
        model_event=next(e for e in self.events if e['type']=='models')
        self.assertEqual(model_event['rows'],[
            {'id':'opencode/Flint 40B Free','label':'Flint 40B Free'},
            {'id':'opencode/Big Pickle','label':'Big Pickle'},
        ])
        self.assertEqual(p.model,'opencode/Flint 40B Free')

    def test_cli_effort_override_is_scoped_to_each_launch(self):
        for protocol in ('claude','agy'):
            args=agent_backends.launch_arguments({'protocol':protocol},'model','session',effort='high')
            self.assertEqual(args[args.index('--effort')+1],'high')
            self.assertNotIn('--effort',agent_backends.launch_arguments({'protocol':protocol},'model','session'))

    def test_codex_approval_ids_are_independent_and_foreign_thread_is_ignored(self):
        p=self.setup_protocol(CodexProtocol);p.session='thread'
        for key in (10,11):p.receive({'id':key,'method':'item/commandExecution/requestApproval','params':{'threadId':'thread','command':'read fixture'}})
        p.receive({'id':12,'method':'item/commandExecution/requestApproval','params':{'threadId':'other'}})
        self.assertEqual(len(self.events),2)
        p.answer('10','decline',{});p.answer('11','accept',{})
        self.assertEqual([m['result']['decision'] for m in self.sent],['decline','accept'])
        self.assertEqual([m['id'] for m in self.sent],[10,11])

    def test_codex_questions_keep_question_ids(self):
        p=self.setup_protocol(CodexProtocol)
        p.receive({'id':'question','method':'item/tool/requestUserInput','params':{'questions':[{'id':'q','question':'Which?'}]}})
        p.answer('question','answer',{'q':'Keep'})
        self.assertEqual(self.sent[-1]['result'],{'answers':{'q':{'answers':['Keep']}}})

    def test_codex_readable_thinking_has_a_real_lifecycle_and_excludes_raw_content(self):
        p=self.setup_protocol(CodexProtocol)
        p.receive({'method':'item/started','params':{'item':{'id':'r','type':'reasoning','summary':[]}}})
        self.assertEqual(self.events[-1]['type'],'thinking');self.assertTrue(self.events[-1]['active'])
        p.receive({'method':'item/reasoning/summaryTextDelta','params':{'itemId':'r','delta':'Checking configuration','summaryIndex':0}})
        self.assertEqual(self.events[-1]['text'],'Checking configuration')
        p.receive({'method':'item/reasoning/textDelta','params':{'itemId':'r','delta':'raw-unexposed-content'}})
        p.receive({'method':'item/completed','params':{'item':{'id':'r','type':'reasoning','summary':['Checking configuration'],'content':['raw-unexposed-content']}}})
        self.assertFalse(self.events[-1]['active']);self.assertNotIn('raw-unexposed-content',json.dumps(self.events))

    def test_acp_advertised_model_config_permission_and_cancel(self):
        p=self.setup_protocol(AcpProtocol);p.start();self.rpc_result(p,'initialize',{'agentCapabilities':{}})
        self.rpc_result(p,'session/new',{'sessionId':'session','configOptions':[{'id':'model','category':'model','currentValue':'a','options':[{'value':'a','name':'A'}]}]})
        p.set_model('a');self.assertEqual(self.sent[-1]['method'],'session/set_config_option')
        p.receive({'id':55,'method':'session/request_permission','params':{'sessionId':'session','toolCall':{'title':'Read'},'options':[{'optionId':'yes-1','kind':'allow_once','name':'Allow'}]}})
        with self.assertRaises(ValueError):p.answer('55','accept',{})
        self.assertIn('55',p.requests)
        p.answer('55','yes-1',{});self.assertEqual(self.sent[-1]['result']['outcome']['optionId'],'yes-1')
        p.interrupt();self.assertEqual(self.sent[-1]['method'],'session/cancel')

    def test_claude_live_control_preserves_input_and_error_result(self):
        p=self.setup_protocol(ClaudeProtocol);p.start();key=self.sent[-1]['request_id']
        p.receive({'type':'control_response','response':{'request_id':key,'subtype':'success','response':{'models':[{'value':'a','display_name':'A'}],'commands':[{'name':'compact'}]}}})
        self.assertTrue(any(e['type']=='ready' for e in self.events))
        original={'file_path':'logical/branch.rc','content':'draft'}
        p.receive({'type':'control_request','request_id':'req','request':{'subtype':'can_use_tool','tool_name':'Write','input':original}})
        p.answer('req','accept',{})
        self.assertEqual(self.sent[-1]['response']['response']['updatedInput'],original)
        p.receive({'type':'result','subtype':'success','is_error':True,'result':'Model unavailable'})
        self.assertEqual(self.events[-1]['status'],'failed');self.assertEqual(self.events[-1]['error'],'Model unavailable')

    def test_claude_streamed_text_is_not_duplicated_by_assistant_snapshot(self):
        p=self.setup_protocol(ClaudeProtocol)
        p.receive({'type':'stream_event','event':{'type':'message_start','message':{'id':'msg_1'}}})
        p.receive({'type':'stream_event','event':{'type':'content_block_start','index':0,'content_block':{'type':'thinking','thinking':''}}})
        p.receive({'type':'stream_event','event':{'type':'content_block_stop','index':0}})
        p.receive({'type':'stream_event','event':{'type':'content_block_start','index':1,'content_block':{'type':'text','text':''}}})
        p.receive({'type':'stream_event','event':{'type':'content_block_delta','index':1,'delta':{'type':'text_delta','text':'Hai! '}}})
        p.receive({'type':'stream_event','event':{'type':'content_block_delta','index':1,'delta':{'type':'text_delta','text':'Siap.'}}})
        p.receive({'type':'stream_event','event':{'type':'content_block_stop','index':1}})
        # Claude CLI subsequently emits an assistant block snapshot at content index 0.
        p.receive({'type':'assistant','message':{'id':'msg_1','content':[{'type':'text','text':'Hai! Siap.'}]}})
        text_events=[e for e in self.events if e['type']=='text']
        self.assertEqual(len(text_events),2)
        self.assertEqual({e['id'] for e in text_events},{'msg_1:1'})
        # Non-streamed assistant messages (e.g. a different message id) still render normally.
        p.receive({'type':'assistant','message':{'id':'msg_2','content':[{'type':'text','text':'Lanjut.'}]}})
        self.assertEqual(self.events[-1],{'type':'text','id':'msg_2:0','text':'Lanjut.','append':False})

    def test_agy_reports_only_terminal_success_and_does_not_invent_interrupt(self):
        p=self.setup_protocol(AgyProtocol);self.assertFalse(p.interruptible)
        with self.assertRaises(ValueError):p.interrupt()
        for status in ('RUNNING','WAITING','ERROR','INVALID'):
            p.receive({'event':'result','result':{'status':status}});self.assertEqual(self.events[-1]['status'],'failed')
        p.receive({'event':'result','result':{'status':'SUCCESS','response':'OK'}})
        self.assertEqual(self.events[-1]['status'],'completed')

    def test_agy_empty_stream_and_permission_denial_do_not_claim_success(self):
        p=self.setup_protocol(AgyProtocol)
        p.receive({'event':'step_update','step_update':{'step_type':'agent_response','state':'DONE'}})
        self.assertFalse(any(e['type']=='text' for e in self.events))
        p.receive({'event':'step_update','step_update':{'step_type':'agent_response','state':'ACTIVE','usage':{'thinking_tokens':100}}})
        self.assertFalse(any(e['type']=='thinking' for e in self.events))
        p.receive({'event':'result','result':{'status':'SUCCESS','response':'','denied_actions':[{'action':'command','display_name':'RunCommand'}]}})
        self.assertEqual(self.events[-1]['status'],'failed')
        self.assertEqual(self.events[-1]['failure_kind'],'permission_denied')

    def test_agy_always_uses_unrestricted_permissions_without_changing_others(self):
        self.assertIn('--dangerously-skip-permissions',agent_backends.launch_arguments({'protocol':'agy'}))

    def test_agy_workspace_handshake_precedes_any_prompt_and_checks_reported_cwd(self):
        p=self.setup_protocol(AgyProtocol);p.start()
        self.assertFalse(any(event['type']=='ready' for event in self.events))
        with self.assertRaises(ValueError):p.prompt('must wait')
        p.receive({'event':'init','conversation_id':'foreign','init':{'cwd':os.path.dirname(p.cwd)}})
        self.assertEqual(self.events[-1]['type'],'workspace_error');self.assertEqual(self.sent,[])
        p.receive({'event':'init','conversation_id':'bound','init':{'cwd':p.cwd}})
        self.assertEqual(self.events[-1]['type'],'ready');p.prompt('hello')
        self.assertEqual(self.sent[-1]['message']['content'],'hello')
        for kind in ('codex','acp','claude'):
            self.assertNotIn('--dangerously-skip-permissions',agent_backends.launch_arguments({'protocol':kind}))

    def test_save_permission_uses_native_session_decision_and_rejects_unavailable(self):
        p=self.setup_protocol(CodexProtocol)
        p.receive({'id':20,'method':'item/commandExecution/requestApproval','params':{}})
        self.assertEqual([o['kind'] for o in self.events[-1]['options']],['save','yes','no'])
        p.answer('20','acceptForSession',{});self.assertEqual(self.sent[-1]['result'],{'decision':'acceptForSession'})
        p.receive({'id':21,'method':'item/commandExecution/requestApproval','params':{'availableDecisions':['accept','decline']}})
        with self.assertRaises(ValueError):p.answer('21','acceptForSession',{})
        self.assertIn('21',p.requests)
        p.receive({'id':22,'method':'item/permissions/requestApproval','params':{'permissions':{'network':{'enabled':True}}}})
        p.answer('22','acceptForSession',{});self.assertEqual(self.sent[-1]['result']['scope'],'session')

    def test_acp_save_preserves_advertised_always_allow_id(self):
        p=self.setup_protocol(AcpProtocol)
        p.receive({'id':20,'method':'session/request_permission','params':{'options':[{'optionId':'save-20','kind':'allow_always','name':'Always allow'}]}})
        self.assertEqual(self.events[-1]['options'],[{'id':'save-20','kind':'save'}])
        p.answer('20','save-20',{});self.assertEqual(self.sent[-1]['result']['outcome']['optionId'],'save-20')

    def test_claude_save_uses_offered_rules_in_session_and_preserves_tool_input(self):
        p=self.setup_protocol(ClaudeProtocol);rule={'type':'addRules','behavior':'allow','rules':[{'toolName':'Bash','ruleContent':'git status'}],'destination':'localSettings'}
        p.receive({'type':'control_request','request_id':'save','request':{'subtype':'can_use_tool','tool_name':'Bash','input':{'command':'git status'},'permission_suggestions':[rule]}})
        p.answer('save','save',{});response=self.sent[-1]['response']['response']
        self.assertEqual(response['updatedPermissions'],[{**rule,'destination':'session'}])
        self.assertEqual(response['updatedInput'],{'command':'git status'})

    def test_activity_summary_contains_real_command_output_and_exit_status(self):
        rows=[{'key':'u','role':'user','text':'inspect'}, {'key':'w','role':'work','text':'','elapsed':65,'status':'completed'},
              {'key':'t','role':'tool','title':'commandExecution','command':'python --version','tool_kind':'commandExecution','status':'completed','text':'Python fixture','exit_code':0}]
        shown=transcript(rows);activity=shown[-1];tool=next(r for r in shown if r['role']=='tool')
        self.assertEqual(activity['title'],'Worked for 1m 5s')
        self.assertEqual(activity['role'],'status')
        self.assertEqual(tool['title'],'Ran python --version')
        self.assertIn('Python fixture',tool['text']);self.assertIn('Exit code: 0',tool['text'])
        rows[1]['status']='failed';rows[2]['status']='failed';rows[2]['exit_code']=1
        shown=transcript(rows);self.assertEqual(shown[-1]['title'],'Failed after 1m 5s');self.assertTrue(next(r for r in shown if r['role']=='tool')['failed'])

    def test_legacy_empty_reply_is_explained_without_modifying_history(self):
        rows=[{'key':'u','role':'user','text':'tes'}, {'key':'a','role':'assistant','text':''},
              {'key':'t','role':'tool','title':'run_command','status':'failed','text':'permission check failed: denied permission'},
              {'key':'s','role':'system','status':'completed','text':'Turn completed'}]
        before=deepcopy(rows);shown=transcript(rows,'id')
        self.assertEqual(rows,before)
        self.assertEqual([r['role'] for r in shown],['user','tool','problem'])
        self.assertEqual(shown[-1]['title'],'Belum ada jawaban')
        self.assertEqual(shown[-1]['failure_kind'],'permission_denied')
        self.assertEqual(shown[1]['status'],'failed')


class Owner(QObject):
    ready=Signal()
    def __init__(self,root):
        super().__init__();self.project=str(root);self.copied='';self.activeTreeId=0
        self.directories=[SimpleNamespace(base=str(root),location=str(root))]
    def copyText(self,text):self.copied=text


class FakeInbox:
    _attachments=staticmethod(lambda context:[{'path':p,'label':p} for p in context.get('paths',[])])
    def __init__(self,root):self.store=AgentInbox(Path(root)/'inbox.json');self.detail={};self.context={'paths':['logical/a.rc'],'documents':[{'path':'logical/a.rc','revision':'r1','unsaved':True,'content':'draft'}]}
    def capture(self,*_):return deepcopy(self.context)
    def select(self,key):self.detail=self.store.get(key)
    def setShowing(self,*_):pass
    def openPath(self,*_):pass
    def show(self):pass


class Agent(QObject):
    def __init__(self,owner):super().__init__(owner);self.owner=owner;self.events=AgentEvents();self.inbox=FakeInbox(owner.project)
    def workspace_context(self):
        from core.agent_workspace import describe
        return describe(self.owner)


class FakeProcess(QObject):
    event=Signal(object)
    def __init__(self,parent=None):super().__init__(parent);self.sent=[];self.closed=False;self.terminated=False
    def start(self,argv,cwd,environment,log_path):self.argv=argv;self.cwd=cwd;self.environment=environment
    def send(self,message):self.sent.append(message)
    def close_input(self):self.closed=True
    def terminate(self):self.terminated=True;self.close_input()


class ControllerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QCoreApplication.instance() or QCoreApplication([])
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);root=Path(self.temp.name)
        self.folder=patch('core.agent_chat_store.project_folder',return_value=root/'chat');self.folder.start();self.addCleanup(self.folder.stop)
        self.process=patch('ui.agent_chat.AgentProcess',FakeProcess);self.process.start();self.addCleanup(self.process.stop)
        self.owner=Owner(root);self.agent=Agent(self.owner);self.chat=WorkspaceAgentChat(self.agent);self.addCleanup(self.chat.close)
        self.chat._providers=[{'id':'codex','label':'Codex','protocol':'codex','argv':['fixture']},{'id':'agy','label':'AGY','protocol':'agy','argv':['fixture']}];self.chat._provider='codex'
    def connected(self):
        self.chat.connectBackend();self.chat._protocol_event({'type':'session','session':'session','model':'model'});self.chat._protocol_event({'type':'ready'})
    def send(self,text='Check this'):
        self.chat.setDraft(text);self.assertTrue(self.chat.send())

    def test_load_agy_models_migrates_effort_suffixed_chat_models(self):
        self.chat._providers = [{'id': 'agy', 'label': 'AGY', 'protocol': 'agy', 'argv': ['fixture']}]
        self.chat._provider = 'agy'
        self.chat._create_chat()
        self.chat._chat()['model'] = 'gemini-3.8-flash-high'
        provider = self.chat._providers[0]
        sample_models = [
            {'id': 'gemini-3.8-flash', 'label': 'Gemini 3.8 Flash', 'efforts': [{'id': 'low', 'label': 'low'}, {'id': 'high', 'label': 'high'}]}
        ]
        with patch.object(self.chat._jobs, 'submit', side_effect=lambda name, fn, p, done, **kw: done(True, sample_models)):
            self.chat._load_agy_models(provider)
        self.assertEqual(self.chat.model, 'gemini-3.8-flash')
        self.assertEqual(self.chat.effort, 'high')
        self.assertEqual(self.chat._current_model()['id'], 'gemini-3.8-flash')
        self.assertEqual([e['id'] for e in self.chat.efforts], ['low', 'high'])

    def test_opencode_models_property_and_selection_strip_prefix(self):
        self.chat._providers = [{'id': 'opencode', 'label': 'OpenCode', 'protocol': 'acp', 'argv': ['fixture']}]
        self.chat._provider = 'opencode'
        self.chat._create_chat()
        self.chat._models = [
            {'id': 'opencode/Flint 40B Free', 'label': 'Flint 40B Free'},
            {'id': 'opencode/Big Pickle', 'label': 'opencode/Big Pickle'},
        ]
        labels = [m['label'] for m in self.chat.models if m['id']]
        self.assertEqual(labels, ['Flint 40B Free', 'Big Pickle'])
        self.chat.selectModel('Big Pickle')
        self.assertEqual(self.chat.model, 'opencode/Big Pickle')

    def test_codex_model_switch_syncs_efforts_and_live_protocol_effort(self):
        self.chat._providers = [{'id': 'codex', 'label': 'Codex', 'protocol': 'codex', 'argv': ['fixture']}]
        self.chat._provider = 'codex'
        self.connected()
        self.chat._protocol_event({'type': 'models', 'rows': [
            {'id': 'm1', 'label': 'M1', 'efforts': [{'id': 'low', 'label': 'low'}, {'id': 'high', 'label': 'high'}], 'defaultEffort': ''},
            {'id': 'm2', 'label': 'M2', 'efforts': [{'id': 'medium', 'label': 'medium'}], 'defaultEffort': ''},
        ]})
        self.chat.selectModel('m1')
        self.chat.selectEffort('high')
        self.assertEqual(self.chat.effort, 'high')
        self.assertEqual(self.chat._protocol.effort, 'high')
        self.chat.selectModel('m2')
        self.assertEqual([e['id'] for e in self.chat.efforts], ['medium'])
        self.assertEqual(self.chat.effort, 'medium')
        self.assertEqual(self.chat._protocol.effort, 'medium')
        self.chat.selectModel('m1')
        self.assertEqual(self.chat.effort, 'high')
        self.assertEqual(self.chat._protocol.effort, 'high')

    def test_opencode_effort_syncs_from_native_event_and_clears_automatic(self):
        self.chat._providers = [{'id': 'opencode', 'label': 'OpenCode', 'protocol': 'acp', 'argv': ['fixture']}]
        self.chat._provider = 'opencode'
        self.connected()
        self.chat._protocol_event({'type': 'model', 'model': 'opencode/Flint'})
        self.chat._protocol_event({'type': 'efforts', 'rows': [{'id': 'low', 'label': 'Low'}, {'id': 'high', 'label': 'High'}, {'id': 'default', 'label': 'Default'}], 'current': 'high'})
        # 'default' must be filtered out dynamically
        self.assertEqual([e['id'] for e in self.chat.efforts], ['low', 'high'])
        self.assertEqual(self.chat.effort, 'high')
        self.chat.selectEffort('')
        self.assertEqual(self.chat.effort, 'low')

    def test_chat_toggle_and_minimize_keeps_backend_connected(self):
        self.chat._providers = [{'id': 'codex', 'label': 'Codex', 'protocol': 'codex', 'argv': ['fixture']}]
        self.chat._provider = 'codex'
        self.connected()
        self.chat.show()
        self.assertTrue(self.chat.opened)
        self.chat.toggle()
        self.assertFalse(self.chat.opened)
        self.assertIsNotNone(self.chat._transport)
        self.chat.toggle()
        self.assertTrue(self.chat.opened)

    def tree_workspace(self):
        root=Path(self.owner.project)/'erf';root.mkdir()
        self.owner.directories=[SimpleNamespace(base=str(root),location=str(root))]
        return root

    def test_backend_and_each_turn_use_the_opened_tree_even_after_attachments_are_cleared(self):
        root=self.tree_workspace();self.connected()
        self.assertEqual(self.chat._transport.cwd,str(root))
        self.assertEqual(self.chat._transport.environment['RAK_WORKSPACE_ROOT'],str(root))
        self.send('first')
        self.chat._protocol_event({'type':'done','status':'completed','answer':'First response'})
        self.assertFalse(self.chat.attachments)
        self.send('create another file')
        turn=next(message for message in reversed(self.chat._transport.sent) if message.get('method')=='turn/start')
        prompt=turn['params']['input'][0]['text']
        marker='Authoritative Kitchen workspace (current for this turn):\n'
        workspace=json.loads(prompt.split(marker,1)[1].split('\n\nUser request:',1)[0])
        self.assertEqual(workspace['default_output_directory'],str(root))
        self.assertEqual(workspace['project_container'],self.owner.project)
        self.assertEqual(self.agent.inbox.store.get(self.chat._request_id)['context']['live_workspace'],workspace)

    def test_unbound_agy_resume_is_archived_and_new_session_is_project_scoped(self):
        root=self.tree_workspace();self.chat._provider='agy';self.chat._create_chat()
        self.chat._chat()['session']='legacy-global-session';self.chat._append('old-user','user','Earlier request')
        with patch.object(self.chat,'_load_agy_models'):
            self.chat.connectBackend();transport=self.chat._transport
            self.assertIn('--new-project',transport.argv);self.assertNotIn('--conversation',transport.argv)
            self.assertEqual(transport.argv[transport.argv.index('--add-dir')+1],str(root))
            self.assertEqual(self.chat._chat()['previous_sessions'][0]['id'],'legacy-global-session')
            self.assertTrue(any(row['key']=='old-user' for row in self.chat._messages.values()))
            self.chat._protocol.start();self.chat._protocol.receive({'event':'init','conversation_id':'scoped-session','init':{'cwd':str(root)}})
            self.chat._disconnect();self.chat.connectBackend();args=self.chat._transport.argv
            self.assertEqual(args[args.index('--conversation')+1],'scoped-session');self.assertNotIn('--new-project',args)

    def test_switching_tree_rebinds_backend_without_reusing_its_old_session(self):
        root=self.tree_workspace();other=root.with_name('other');other.mkdir()
        self.owner.directories.append(SimpleNamespace(base=str(other),location=str(other)))
        self.connected();transport=self.chat._transport;self.owner.activeTreeId=1
        self.send()
        self.assertTrue(transport.closed);self.assertEqual(self.chat._transport.cwd,str(other))
        self.assertEqual(self.chat._chat()['session'],'')
        self.assertEqual(self.chat._chat()['previous_sessions'][0]['id'],'session')

    def test_wrong_agy_init_cwd_fails_without_sending_queued_user_request(self):
        self.chat._provider='agy'
        with patch.object(self.chat,'_load_agy_models'):
            self.chat.connectBackend();transport=self.chat._transport;protocol=self.chat._protocol;protocol.start()
            self.send('Create a file');request=self.chat._request_id
            self.assertEqual(transport.sent,[])
            with self.assertLogs('ui.agent_chat',level='ERROR'):
                protocol.receive({'event':'init','conversation_id':'wrong','init':{'cwd':os.path.dirname(self.owner.project)}})
        self.assertTrue(transport.terminated);self.assertEqual(transport.sent,[])
        self.assertEqual(self.chat.state,'failed');self.assertIsNone(self.chat._pending_prompt)
        self.assertEqual(self.agent.inbox.store.get(request)['state'],'failed')

    def test_claimed_context_is_frozen_and_private_tokens_never_enter_public_events(self):
        self.chat.show('editor');self.agent.inbox.context['documents'][0]['content']='changed'
        self.send();request=self.agent.inbox.store.get(self.chat._request_id)
        self.assertEqual(request['context']['documents'][0]['content'],'draft')
        self.assertEqual(request['state'],'running');self.assertNotIn('claim',request)
        self.assertNotIn('"claim"',json.dumps(self.agent.events.read(0)))
        self.chat._protocol_event({'type':'ready'})
        self.assertTrue(any(m.get('method')=='turn/start' for m in self.chat._transport.sent))

    def test_open_prepares_models_and_send_waits_for_handshake(self):
        self.chat.show();self.assertEqual(self.chat.state,'connecting');self.assertFalse(self.chat.running)
        self.send('A message while connecting');self.assertTrue(self.chat.running)
        transport=self.chat._transport
        self.assertFalse(any(m.get('method')=='turn/start' for m in transport.sent))
        self.chat._protocol_event({'type':'ready'})
        self.assertEqual(sum(m.get('method')=='turn/start' for m in transport.sent),1)

    def test_switch_provider_starts_connection_without_sending_the_draft(self):
        self.chat.setDraft('Keep this draft')
        with patch.object(self.chat,'_load_agy_models'):self.chat.selectProvider('agy')
        self.assertEqual(self.chat.state,'connecting');self.assertFalse(self.chat.running)
        self.assertEqual(self.chat.draft,'Keep this draft')
        self.assertEqual(self.chat._transport.sent,[])
        self.assertFalse(self.agent.inbox.store.snapshot()['messages'])

    def test_streamed_text_is_replaced_by_authoritative_final_text(self):
        self.connected();self.send()
        for event in ({'type':'text','id':'a','text':'Hel','append':True},{'type':'text','id':'a','text':'lo','append':True},{'type':'text','id':'a','text':'Hello','append':False}):self.chat._protocol_event(event)
        request=self.chat._request_id
        self.chat._protocol_event({'type':'done','status':'completed'})
        self.assertEqual(self.agent.inbox.store.get(request)['answer'],'Hello')
        self.assertEqual([r['text'] for r in self.chat.messages.snapshot() if r['role']=='assistant'],['Hello'])

    def test_stop_before_turn_start_never_submits_the_prompt(self):
        self.send();transport=self.chat._transport;request=self.chat._request_id
        self.chat.stop();self.chat._protocol_event({'type':'ready'})
        self.assertFalse(any(m.get('method')=='turn/start' for m in transport.sent))
        self.assertEqual(self.agent.inbox.store.get(request)['state'],'cancelled')

    def test_running_stop_waits_for_actual_backend_completion(self):
        self.connected();self.send();self.chat._protocol.turn='turn'
        request=self.chat._request_id;self.chat.stop()
        self.assertEqual(self.chat.state,'stopping');self.assertEqual(self.agent.inbox.store.get(request)['state'],'running')
        self.chat._protocol_event({'type':'done','status':'interrupted'})
        self.assertEqual(self.agent.inbox.store.get(request)['state'],'cancelled')

    def test_exit_zero_without_result_is_failure(self):
        self.connected();self.send();request=self.chat._request_id
        self.chat._receive(self.chat._generation,('exit',0))
        self.assertEqual(self.agent.inbox.store.get(request)['state'],'failed')

    def test_no_reply_is_a_failed_inbox_task(self):
        self.connected();self.send();request=self.chat._request_id
        self.chat._protocol_event({'type':'done','status':'completed','answer':''})
        self.assertEqual(self.chat.state,'failed')
        self.assertEqual(self.agent.inbox.store.get(request)['state'],'failed')
        self.assertEqual(next(r for r in self.chat.messages.snapshot() if r['role']=='problem')['failure_kind'],'no_reply')

    def test_agent_error_shows_in_detail_and_never_opens_modal_dialog(self):
        class Presenter(QObject):
            fatalRequested = Signal(str)
            operationErrorRequested = Signal(str)
        presenter = Presenter()
        presentations = []
        presenter.fatalRequested.connect(lambda t: presentations.append(('fatal', t)))
        presenter.operationErrorRequested.connect(lambda t: presentations.append(('operation', t)))
        register_error_presenter(presenter)
        try:
            self.connected()
            self.send()
            err_msg = 'session/prompt: Internal error: Free usage limit reached. Free models work in OpenCode (https://opencode.ai); subscribe to OpenCode Go to keep going: https://opencode.ai/go'
            self.chat._protocol_event({'type': 'error', 'text': err_msg})
            self.app.processEvents()
            self.assertEqual(self.chat.state, 'failed')
            problem = next(r for r in self.chat.messages.snapshot() if r['role'] == 'problem')
            self.assertIn('Free usage limit reached', problem['text'])
            self.assertEqual(problem['failure_kind'], 'operation_failed')
            self.assertEqual(presentations, [])
        finally:
            unregister_error_presenter(presenter)

    def test_final_reply_survives_an_old_empty_placeholder(self):
        self.connected();self.send()
        self.chat._append(self.chat._turn_key+':empty','assistant','')
        self.chat._protocol_event({'type':'done','status':'completed','answer':'Actual reply'})
        self.assertEqual([r['text'] for r in self.chat.messages.snapshot() if r['role']=='assistant'],['Actual reply'])
        self.assertEqual(self.chat.state,'completed')

    def test_deprecated_restricted_setting_cannot_disable_agy_unrestricted(self):
        self.chat._data['agy_permission_mode']='restricted';self.chat._provider='agy'
        with patch.object(self.chat,'_load_agy_models'):
            self.chat.connectBackend();self.assertIn('--dangerously-skip-permissions',self.chat._transport.argv)
        self.assertFalse(self.agent.inbox.store.snapshot()['messages'])

    def test_model_catalog_failure_does_not_fail_active_turn_or_open_a_modal(self):
        self.chat._provider='agy';failure=CommandFailure('model query timed out',code='TimeoutExpired')
        with patch('ui.agent_chat.load_agy_models',return_value=(False,failure)) as loader,patch('core.error_reporting.report_fatal') as presenter:
            self.chat.connectBackend();self.chat._protocol.start()
            self.chat._protocol.receive({'event':'init','conversation_id':'fixture','init':{'cwd':self.chat._transport.cwd}})
            self.send();request=self.chat._request_id
            deadline=time.monotonic()+3
            while self.chat._catalog_pending:self.app.processEvents();self.assertLess(time.monotonic(),deadline);time.sleep(.005)
            self.assertEqual(self.chat.state,'working');self.assertEqual(self.agent.inbox.store.get(request)['state'],'running')
            self.assertTrue(self.chat.modelCatalogError);presenter.assert_not_called()
            provider=next(p for p in self.chat._providers if p['id']=='agy');self.chat._load_agy_models(provider)
            self.assertEqual(loader.call_count,1) # No automatic retry every reconnect.
            self.chat._protocol_event({'type':'done','status':'completed','answer':'Still works'})

    def test_effort_is_per_model_and_claude_resolved_alias_does_not_lose_it(self):
        self.chat._provider='claude';self.chat._create_chat()
        self.chat._models=[{'id':'sonnet','resolved':'resolved-sonnet','label':'Sonnet','efforts':[{'id':'high','label':'High'}]}]
        self.chat._chat()['model']='sonnet';self.chat.selectEffort('high')
        self.chat._protocol_event({'type':'session','session':'s','model':'resolved-sonnet'})
        self.assertEqual(self.chat.model,'sonnet');self.assertEqual(self.chat.effort,'high')
        self.chat._chat()['model']='other';self.assertEqual(self.chat.effort,'medium')

    def test_unsupported_effort_command_preserves_draft(self):
        self.chat.setDraft('/effort imaginary');self.assertFalse(self.chat.send())
        self.assertEqual(self.chat.draft,'/effort imaginary')

    def test_external_attachment_is_sent_by_path_and_removed_only_from_next_draft(self):
        self.chat._data['attachment']={'paths':[],'documents':[]};path=Path(self.owner.project)/'outside # file.txt';path.write_text('do not inline this content')
        self.agent._path=lambda *_:(_ for _ in ()).throw(ValueError('outside project trees'))
        self.assertTrue(self.chat.addAttachments([str(path),str(path)]));self.assertEqual(len(self.chat.attachments),1)
        self.assertTrue(self.chat._data['attachment']['attachment_files'][0]['external'])
        self.connected();self.chat.setDraft('');self.assertTrue(self.chat.send())
        request=self.agent.inbox.store.get(self.chat._request_id)
        self.assertEqual(request['context']['paths'],[str(path)])
        self.assertNotIn('do not inline this content',str(self.chat._transport.sent))
        self.assertFalse(self.chat.attachments)
        user=next(r for r in self.chat.messages.snapshot() if r['role']=='user')
        self.assertEqual(user['text'],'');self.assertEqual(user['attachments'][0]['path'],str(path))
        self.assertTrue(path.exists())

    def test_failed_attachment_batch_preserves_existing_frozen_context_and_file(self):
        self.agent._path=lambda *_:(_ for _ in ()).throw(ValueError('outside project trees'))
        path=Path(self.owner.project)/'attachment.txt';path.write_text('original')
        before=deepcopy(self.chat._data.get('attachment'))
        self.assertFalse(self.chat.addAttachments([str(path),str(path.with_name('absent.txt'))]))
        self.assertEqual(self.chat._data.get('attachment'),before);self.assertEqual(path.read_text(),'original')

    def test_new_attachment_preserves_frozen_buffer_and_detaching_never_deletes_file(self):
        self.chat.show('editor');self.agent.inbox.context['documents'][0]['content']='new editor content'
        self.agent._path=lambda *_:(_ for _ in ()).throw(ValueError('outside project trees'))
        path=Path(self.owner.project)/'external.txt';path.write_text('original external bytes')
        self.assertTrue(self.chat.addAttachments([str(path)]))
        self.assertEqual(self.chat._data['attachment']['documents'][0]['content'],'draft')
        self.chat.removeAttachment(str(path))
        self.assertEqual(self.chat._data['attachment']['paths'],['logical/a.rc'])
        self.assertEqual(path.read_text(),'original external bytes')

    def test_work_timer_tracks_elapsed_but_only_native_result_finishes_it(self):
        self.connected()
        with patch('ui.agent_chat.time.monotonic',return_value=100):self.send()
        with patch('ui.agent_chat.time.monotonic',return_value=165):self.chat._tick_work()
        activity=self.chat.messages.snapshot()[-1];self.assertEqual(activity['title'],'Working for 1m 5s')
        self.assertEqual(self.chat.state,'working');self.assertTrue(self.chat._work_timer.isActive())
        with patch('ui.agent_chat.time.monotonic',return_value=172):self.chat._protocol_event({'type':'done','status':'completed','answer':'Done'})
        self.assertEqual(self.chat.messages.snapshot()[-1]['title'],'Worked for 1m 12s')
        self.assertFalse(self.chat._work_timer.isActive());self.assertIsNone(self.chat._work_clock)
        reopened=ChatStore(self.owner.project)
        self.assertEqual(next(r for r in reopened.data['chats'][0]['messages'] if r['role']=='work')['elapsed'],72)

    def test_thinking_is_clickable_only_after_a_real_phase_or_supplied_details(self):
        self.connected();self.send()
        self.chat._tick_work();activity=self.chat.messages.snapshot()[-1]
        self.assertEqual(activity['role'],'status');self.assertNotIn('expandable',activity)
        self.assertFalse(any(r['role']=='thought' for r in self.chat.messages.snapshot()))
        self.chat._protocol_event({'type':'thinking','id':'r','active':True,'text':''})
        thought=next(r for r in self.chat.messages.snapshot() if r['role']=='thought')
        self.assertTrue(thought['thinking']);self.assertEqual(thought['text'],'')
        self.chat._protocol_event({'type':'thinking','id':'r','active':True,'text':'Readable summary','append':True})
        self.assertEqual(next(r for r in self.chat.messages.snapshot() if r['role']=='thought')['text'],'Readable summary')
        self.chat._protocol_event({'type':'thinking','id':'r','active':False,'text':''})
        self.assertFalse(next(r for r in self.chat.messages.snapshot() if r['role']=='thought')['thinking'])
        self.chat._protocol_event({'type':'done','status':'completed','answer':'Done'})
        activity=self.chat.messages.snapshot()[-1]
        self.assertEqual(activity['role'],'status');self.assertNotIn('expandable',activity)

    def test_native_thoughts_and_commands_keep_their_actual_timeline_order(self):
        self.connected()
        with patch('ui.agent_chat.time.monotonic',return_value=100):
            self.send();self.chat._protocol_event({'type':'thinking','id':'one','active':True,'text':'Checking configuration'})
        with patch('ui.agent_chat.time.monotonic',return_value=107):
            self.chat._protocol_event({'type':'tool','id':'one','title':'List source files','text':'file.py','status':'completed','command':'rg --files'})
        with patch('ui.agent_chat.time.monotonic',return_value=110):
            self.chat._protocol_event({'type':'thinking','id':'two','active':True,'text':'Checking the listed file'})
        with patch('ui.agent_chat.time.monotonic',return_value=114):
            self.chat._protocol_event({'type':'tool','id':'two','title':'Read file','text':'content','status':'completed'})
            self.chat._protocol_event({'type':'done','status':'completed','answer':'Inspection done'})
        rows=self.chat.messages.snapshot()
        self.assertEqual([r['role'] for r in rows],['user','thought','tool','thought','tool','assistant','status'])
        self.assertEqual(rows[1]['title'],'Thought for 7s');self.assertEqual(rows[3]['title'],'Thought for 4s')
        self.assertEqual(rows[2]['title'],'Ran rg --files')

    def test_hiding_panel_leaves_active_turn_and_process_alive(self):
        self.connected();self.send();transport=self.chat._transport
        self.chat.hide();self.assertFalse(transport.closed);self.assertTrue(self.chat.busy)
        self.chat._protocol_event({'type':'done','status':'completed','answer':'done'})

    def test_status_is_local_and_unsupported_slash_commands_keep_draft(self):
        self.chat.setDraft('/status');self.assertTrue(self.chat.send());self.assertIsNone(self.chat._transport)
        self.chat.setDraft('/unknown');self.assertFalse(self.chat.send());self.assertEqual(self.chat.draft,'/unknown')

    def test_missing_backend_does_not_silently_retarget_saved_session(self):
        self.chat._provider='missing';self.chat.setDraft('work');self.assertFalse(self.chat.send());self.assertIsNone(self.chat._transport)

    def test_discovery_without_a_cli_keeps_agent_action_unavailable(self):
        with patch('core.agent_backends.discover',return_value=[]):
            self.chat.refreshBackends();deadline=time.monotonic()+3
            while self.chat.discovering:
                self.app.processEvents();self.assertLess(time.monotonic(),deadline);time.sleep(.002)
        self.assertFalse(self.chat.available)
        self.chat.show();self.assertFalse(self.chat.opened)

    def test_agy_stop_waits_for_actual_owned_process_exit_without_waiting_for_a_reply(self):
        self.chat._provider='agy'
        with patch.object(self.chat,'_load_agy_models'):
            self.chat.connectBackend();self.chat._protocol.start()
            self.chat._protocol.receive({'event':'init','conversation_id':'fixture','init':{'cwd':self.chat._transport.cwd}})
            self.send()
        transport=self.chat._transport;request=self.chat._request_id
        self.chat.stop();self.assertTrue(transport.terminated);self.assertEqual(self.chat.state,'stopping')
        self.assertEqual(self.agent.inbox.store.get(request)['state'],'running')
        generation=self.chat._generation;self.chat._receive(generation,('process_exited',1))
        self.assertEqual(self.agent.inbox.store.get(request)['state'],'cancelled');self.assertIsNone(self.chat._transport)
        self.assertEqual(self.chat.state,'interrupted')
        self.chat._receive(generation,('message',{'event':'result','result':{'status':'SUCCESS','response':'Late buffered answer'}}))
        self.assertEqual(self.chat.state,'interrupted')

    def test_second_stop_can_terminate_a_backend_that_does_not_acknowledge_interrupt(self):
        self.connected();self.send();self.chat._protocol.turn='turn';transport=self.chat._transport
        self.chat.stop();self.assertFalse(transport.terminated);self.assertEqual(self.chat.state,'stopping')
        self.chat.stop();self.assertTrue(transport.terminated);self.assertEqual(self.chat.state,'stopping')
        self.chat._receive(self.chat._generation,('process_exited',1));self.assertEqual(self.chat.state,'interrupted')

    def test_reopen_restores_history_and_draft_without_resuming_work(self):
        self.connected();self.send();self.chat.setDraft('unfinished draft');self.chat._save()
        reopened=ChatStore(self.owner.project)
        self.assertEqual(reopened.data['chats'][0]['state'],'interrupted');self.assertEqual(reopened.data['draft'],'unfinished draft')
        self.chat._protocol_event({'type':'done','status':'completed','answer':'done'})

    def test_history_write_failure_does_not_replace_saved_file(self):
        self.chat.newChat();self.chat._save();before=self.chat._store.path.read_bytes()
        with patch('core.agent_chat_store.os.replace',side_effect=OSError('disk full')):self.chat._save()
        self.assertEqual(self.chat._store.path.read_bytes(),before);self.assertIn('could not be saved',self.chat.error)

    def test_empty_chats_never_persist_resume_or_show_resume_notices_for_any_provider(self):
        self.chat._providers=[
            {'id':'codex','label':'Codex','protocol':'codex','argv':['fixture']},
            {'id':'opencode','label':'OpenCode','protocol':'acp','argv':['fixture']},
            {'id':'claude','label':'Claude','protocol':'claude','argv':['fixture']},
            {'id':'agy','label':'AGY','protocol':'agy','argv':['fixture']},
        ]
        for provider_id in ('codex','opencode','claude','agy'):
            self.chat._disconnect();self.chat._provider=provider_id;self.chat._create_chat()
            self.chat._chat()['session']='stale-empty-session'
            with patch.object(self.chat,'_load_agy_models'):
                self.chat.connectBackend()
            self.assertEqual(self.chat._protocol.session,'')
            self.assertNotIn('--resume',self.chat._transport.argv)
            self.assertNotIn('--conversation',self.chat._transport.argv)
            self.chat._protocol_event({'type':'session','session':'fresh-empty-id','model':'m'})
            self.chat._protocol_event({'type':'notice','text':'Previous session could not be resumed. Started a new conversation.','resume_fallback':True})
            self.assertEqual(self.chat.messages.snapshot(),[])
            self.chat._save()
            reopened=ChatStore(self.owner.project)
            saved_chat=next(c for c in reopened.data['chats'] if c['id']==self.chat.selectedChat)
            self.assertEqual(saved_chat['session'],'')
            self.assertEqual(saved_chat['messages'],[])

    def test_stale_empty_resume_notices_are_cleaned_on_load_while_real_history_keeps_notice(self):
        from collections import OrderedDict
        self.chat.newChat()
        empty_chat=self.chat._chat()
        empty_chat['session']='dead-thread'
        empty_chat['messages']=[{'key':'n1','role':'system','text':'Previous Codex session could not be resumed (no rollout found for thread id dead-thread). Started a new conversation.'}]
        self.chat._messages=OrderedDict((m['key'],m) for m in empty_chat['messages'])
        self.chat._store.save()
        reloaded=WorkspaceAgentChat(self.agent)
        self.addCleanup(reloaded.close)
        self.assertEqual(reloaded.messages.snapshot(),[])
        self.assertEqual(reloaded.sessionId,'')
        # A chat with actual conversation history still surfaces a resume fallback notice.
        reloaded._append('u1','user','Hello')
        reloaded._protocol_event({'type':'notice','text':'Previous Codex session could not be resumed. Started a new conversation.','resume_fallback':True})
        self.assertTrue(any(r['role']=='system' and 'could not be resumed' in r['text'] for r in reloaded.messages.snapshot()))

    def test_synthetic_effort_for_models_without_native_reasoning(self):
        self.chat._create_chat()
        self.chat._models = [
            {'id': 'gemini-3.8-flash', 'label': 'Gemini 3.8 Flash', 'efforts': [{'id': 'low', 'label': 'low'}, {'id': 'high', 'label': 'high'}]},
            {'id': 'gemini-4-neon', 'label': 'Gemini 4 Neon'},
        ]
        self.chat.selectModel('gemini-3.8-flash')
        self.chat.selectEffort('high')
        self.assertEqual(self.chat.effort, 'high')
        self.assertFalse(self.chat._is_synthetic_effort())

        # Switch to gemini-4-neon (model without native reasoning):
        self.chat.selectModel('gemini-4-neon')
        self.assertTrue(self.chat._is_synthetic_effort())
        # Synthetic efforts provide low, medium, high, extra
        self.assertEqual([e['id'] for e in self.chat.efforts], ['low', 'medium', 'high', 'extra'])
        # Defaults to medium
        self.assertEqual(self.chat.effort, 'medium')

        # Select extra
        self.chat.selectEffort('extra')
        self.assertEqual(self.chat.effort, 'extra')

        # Connect backend must NOT pass --effort in CLI argv
        with patch.object(self.chat, '_load_agy_models'):
            self.chat.connectBackend()
        self.assertNotIn('--effort', self.chat._transport.argv)

    def test_connect_backend_claude_sonnet_without_catalog_never_passes_effort(self):
        self.chat._providers = [{'id': 'agy', 'label': 'AGY', 'protocol': 'agy', 'argv': ['agy']}]
        self.chat._provider = 'agy'
        self.chat._create_chat()
        self.chat._models = []
        self.chat._chat()['model'] = 'claude-sonnet-4-6'
        self.chat._chat().setdefault('efforts', {})['claude-sonnet-4-6'] = 'medium'
        with patch.object(self.chat, '_load_agy_models'):
            self.chat.connectBackend()
        self.assertNotIn('--effort', self.chat._transport.argv)
        self.assertIn('--model', self.chat._transport.argv)
        self.assertIn('claude-sonnet-4-6', self.chat._transport.argv)

        # Sending prompt injects cognitive directive into backend payload, but NOT into user transcript
        self.chat.setDraft('Perbaiki bug ini')
        self.chat.send()
        # User message in transcript is purely the user's text
        user_msg = next(m for m in self.chat._messages.values() if m['role'] == 'user')
        self.assertEqual(user_msg['text'], 'Perbaiki bug ini')
        self.assertNotIn('[COGNITIVE DIRECTIVE', user_msg['text'])
        # But backend payload (_pending_prompt) contains the extra critical cognitive directive
        self.assertIn('[COGNITIVE DIRECTIVE - SILENT]: Berpikirlah secara ultra-kritis', self.chat._pending_prompt)
        self.assertIn('Perbaiki bug ini', self.chat._pending_prompt)

    def test_launch_arguments_effort_handling(self):
        args_empty = agent_backends.launch_arguments({'protocol': 'agy'}, model='dynamic-model-1', effort='')
        self.assertNotIn('--effort', args_empty)
        args_valid = agent_backends.launch_arguments({'protocol': 'agy'}, model='dynamic-model-2', effort='high')
        self.assertIn('--effort', args_valid)
        self.assertIn('high', args_valid)

    def test_connect_backend_dynamically_strips_unsupported_effort(self):
        self.chat._create_chat()
        self.chat._models = [
            {'id': 'model-without-effort', 'label': 'Model Without Effort'},
        ]
        self.chat.selectModel('model-without-effort')
        # Inject an effort manually into dict
        self.chat._chat().setdefault('efforts', {})['model-without-effort'] = 'xhigh'
        # effort property falls back to medium because xhigh is not in synthetic efforts
        self.assertEqual(self.chat.effort, 'medium')
        with patch.object(self.chat, '_load_agy_models'):
            self.chat.connectBackend()
        self.assertNotIn('--effort', self.chat._transport.argv)

    def test_connect_backend_claude_sonnet_without_catalog_never_passes_effort(self):
        self.chat._providers = [{'id': 'agy', 'label': 'AGY', 'protocol': 'agy', 'argv': ['agy']}]
        self.chat._provider = 'agy'
        self.chat._create_chat()
        self.chat._models = []
        self.chat._chat()['model'] = 'claude-sonnet-4-6'
        self.chat._chat().setdefault('efforts', {})['claude-sonnet-4-6'] = 'medium'
        with patch.object(self.chat, '_load_agy_models'):
            self.chat.connectBackend()
        self.assertNotIn('--effort', self.chat._transport.argv)
        self.assertIn('--model', self.chat._transport.argv)
        self.assertIn('claude-sonnet-4-6', self.chat._transport.argv)

    def test_model_error_transcript_presentation_and_clean_extraction(self):
        err_text = 'Agent exited (1) before reporting a completed turn. error: invalid model selection (--model "gemini-4-neon" --effort "xhigh"): --effort is not supported for model "gemini-4-neon"\nLog: /path/to/log'
        msg = [{'key': 'p1', 'role': 'problem', 'text': err_text}]
        rows = transcript(msg, 'id')
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0].get('is_model_error'))
        self.assertEqual(rows[0]['title'], 'Kendala Konfigurasi Model')
        self.assertEqual(rows[0]['body'], 'invalid model selection (--model "gemini-4-neon" --effort "xhigh"): --effort is not supported for model "gemini-4-neon"')

    def test_reset_model_to_automatic(self):
        self.chat._create_chat()
        self.chat._models = [
            {'id': 'gemini-3.8-flash', 'label': 'Gemini 3.8 Flash', 'efforts': [{'id': 'high', 'label': 'high'}]},
        ]
        self.chat.selectModel('gemini-3.8-flash')
        self.chat.selectEffort('high')
        self.chat._error = 'Some error'
        self.chat.resetModelToAutomatic()
        self.assertEqual(self.chat.model, '')
        self.assertEqual(self.chat.effort, 'medium')
        self.assertEqual(self.chat.error, '')

    def test_agy_efforts_eliminates_auto_default_and_starts_at_low_with_medium_default(self):
        self.chat._providers = [{'id': 'agy', 'label': 'AGY', 'protocol': 'agy', 'argv': ['fixture']}]
        self.chat._provider = 'agy'
        self.chat._create_chat()
        sample_models = [
            {'id': 'nemo3', 'label': 'NEMO3', 'efforts': [
                {'id': 'auto', 'label': 'Auto'},
                {'id': 'low', 'label': 'low'},
                {'id': 'medium', 'label': 'medium'},
                {'id': 'high', 'label': 'high'},
                {'id': 'default', 'label': 'Default'},
            ]}
        ]
        with patch.object(self.chat, '_metadata_query'), patch.object(self.chat, '_load_agy_models'):
            self.chat._models = sample_models
            self.chat.selectModel('nemo3')
            effort_ids = [e['id'] for e in self.chat.efforts]
            # auto and default must be eliminated
            self.assertEqual(effort_ids, ['low', 'medium', 'high'])
            # Leftmost (index 0) must be low
            self.assertEqual(effort_ids[0], 'low')
            # Default effort must be medium
            self.assertEqual(self.chat.effort, 'medium')
            # Selecting leftmost (low) must stick and not snap back
            self.chat.selectEffort('low')
            self.assertEqual(self.chat.effort, 'low')
            # Clearing effort falls back to medium
            self.chat.selectEffort('')
            self.assertEqual(self.chat.effort, 'medium')


    def test_codex_and_opencode_efforts_never_empty_and_default_to_medium(self):
        # 1. Codex with GPT-6.1 Terra or any dynamic model
        self.chat._providers = [{'id': 'codex', 'label': 'Codex', 'protocol': 'codex', 'argv': ['fixture']}]
        self.chat._provider = 'codex'
        self.chat._create_chat()
        self.chat._models = [
            {'id': 'gpt-6.1-terra', 'label': 'GPT-6.1 Terra', 'efforts': [
                {'id': 'low', 'label': 'Low'},
                {'id': 'medium', 'label': 'Medium'},
                {'id': 'high', 'label': 'High'},
                {'id': 'extra_high', 'label': 'Extra High'},
            ]}
        ]
        self.chat.selectModel('gpt-6.1-terra')
        codex_efforts = [e['id'] for e in self.chat.efforts]
        # Must not contain empty string, auto, or default
        self.assertEqual(codex_efforts, ['low', 'medium', 'high', 'extra_high'])
        self.assertEqual(codex_efforts[0], 'low')
        # Must default to medium (not empty string)
        self.assertEqual(self.chat.effort, 'medium')

        # 2. OpenCode with Tidepool Alpha Free or any dynamic model
        self.chat._providers = [{'id': 'opencode', 'label': 'OpenCode', 'protocol': 'acp', 'argv': ['fixture']}]
        self.chat._provider = 'opencode'
        self.connected()
        self.chat._protocol_event({'type': 'model', 'model': 'opencode/tidepool'})
        self.chat._protocol_event({'type': 'efforts', 'rows': [
            {'id': 'low', 'label': 'Low'},
            {'id': 'medium', 'label': 'Medium'},
            {'id': 'high', 'label': 'High'},
            {'id': 'default', 'label': 'Default'},
        ], 'current': 'default'})
        opencode_efforts = [e['id'] for e in self.chat.efforts]
        # 'default' at the right must be eliminated
        self.assertEqual(opencode_efforts, ['low', 'medium', 'high'])
        self.assertEqual(opencode_efforts[0], 'low')
        # Even if protocol sends current: 'default', UI effort must default to medium
        self.assertEqual(self.chat.effort, 'medium')

    def test_delete_chat_and_empty_chat_pruning(self):
        # 1. newChat() on an already empty chat does not create duplicate entries
        self.chat._create_chat()
        self.assertEqual(len(self.chat._data['chats']), 1)
        self.chat.newChat()
        self.assertEqual(len(self.chat._data['chats']), 1)

        # 2. Add message to first chat, then create new chat
        self.chat._append('u1', 'user', 'Hello first chat')
        chat1_id = self.chat.selectedChat
        self.chat.newChat()
        chat2_id = self.chat.selectedChat
        self.assertNotEqual(chat1_id, chat2_id)
        # chat1 has messages, chat2 is empty
        self.assertEqual(len(self.chat._data['chats']), 2)

        # 3. Switching back to chat1 discards empty chat2
        self.chat.selectChat(chat1_id)
        self.connected()
        self.assertEqual(self.chat.selectedChat, chat1_id)
        self.assertEqual([c['id'] for c in self.chat._data['chats']], [chat1_id])

        # 4. Create another chat, add messages to both, and test deleteChat
        self.chat.newChat()
        chat3_id = self.chat.selectedChat
        self.chat._append('u2', 'user', 'Hello third chat')
        self.assertEqual(len(self.chat._data['chats']), 2)

        # Delete non-selected chat1
        self.chat.deleteChat(chat1_id)
        self.assertNotIn(chat1_id, [c['id'] for c in self.chat._data['chats']])
        self.assertEqual(self.chat.selectedChat, chat3_id)

        # Delete active chat3
        self.chat.deleteChat(chat3_id)
        # Deleting the last chat automatically spawns 1 clean empty chat
        self.assertEqual(len(self.chat._data['chats']), 1)
        self.assertNotEqual(self.chat.selectedChat, chat3_id)

        # 5. Switching provider on empty chat does not spawn duplicate chats
        self.connected()
        self.chat._providers = [
            {'id': 'agy', 'label': 'AGY'},
            {'id': 'codex', 'label': 'Codex'},
        ]
        self.chat.selectProvider('codex')
        self.assertEqual(len(self.chat._data['chats']), 1)
        self.assertEqual(self.chat.provider, 'codex')

    def test_provider_switch_clears_model_and_session_and_select_mode(self):
        self.connected()
        self.chat._providers = [
            {'id': 'codex', 'label': 'Codex', 'protocol': 'codex', 'argv': ['codex']},
            {'id': 'agy', 'label': 'AGY', 'protocol': 'agy', 'argv': ['agy']},
            {'id': 'opencode', 'label': 'OpenCode', 'protocol': 'acp', 'argv': ['opencode']},
        ]
        chat = self.chat._chat()
        chat['model'] = 'codex-only-model'
        chat['session'] = 'old-session'
        with patch.object(self.chat, '_load_agy_models'):
            self.chat.selectProvider('agy')
        self.assertEqual(chat['model'], '')
        self.assertEqual(chat['session'], '')

        # Test selectMode even when disconnected / connecting
        self.chat.selectMode('plan')
        self.assertEqual(self.chat.mode, 'plan')
        self.assertEqual(chat.get('mode'), 'plan')

    def test_load_agy_models_resets_unknown_model_to_automatic(self):
        self.chat._providers = [{'id': 'agy', 'label': 'Antigravity', 'protocol': 'agy', 'argv': ['agy']}]
        self.chat._provider = 'agy'
        self.chat._create_chat()
        chat = self.chat._chat()
        chat['model'] = 'gpt-6.1-terra'
        provider = self.chat._providers[0]
        sample_models = [
            {'id': 'gemini-3.8-flash', 'label': 'Gemini 3.8 Flash', 'efforts': [{'id': 'high', 'label': 'high'}]}
        ]
        with patch.object(self.chat._jobs, 'submit', side_effect=lambda name, fn, p, done, **kw: done(True, sample_models)):
            self.chat._load_agy_models(provider)
        self.assertEqual(chat['model'], '')


if __name__=='__main__':unittest.main()
