# SPDX-License-Identifier: MPL-2.0
import json
import os
from pathlib import Path
import tempfile
import unittest
from core.agent_cli import parse
from core.agent_protocols import ClaudeProtocol,AgyProtocol
from core.agent_tool_paths import extract
from core.agent_backends import launch_arguments


class FileTransportTests(unittest.TestCase):
    def test_claude_receives_kitchen_contract_as_native_system_instructions(self):
        instructions='File editing works with UI triggers off.\nUse file_path and content.'
        args=launch_arguments({'protocol':'claude'},instructions=instructions)
        self.assertEqual(args[args.index('--append-system-prompt')+1],instructions)
    def test_replacement_file_is_data_not_shell_code_or_inline_json(self):
        with tempfile.TemporaryDirectory() as root:
            scratch=Path(root)/'replacement # text.txt'
            content="# Remove-Item on system path '\\n' is blocked\nprint('unicode: ü')\n"
            scratch.write_text(content,encoding='utf-8')
            options=parse('ui','edit "target file.py" --revision disk:abc --content-file "'+str(scratch)+'" --wait 5',root)
            self.assertEqual(options['payload'],{'path':'target file.py','revision':'disk:abc','content':content})
            self.assertEqual(options['wait'],5)
            with self.assertRaisesRegex(ValueError,'not inline JSON'):parse('ui',"edit --from '{\"path\":\"target.py\"}'",root)
    def test_missing_edit_fields_fail_before_any_file_operation(self):
        with self.assertRaisesRegex(ValueError,'--revision'):parse('ui','edit file.py --content-file missing.txt',os.getcwd())
        with self.assertRaisesRegex(ValueError,'--content-file'):parse('ui','edit file.py --revision old',os.getcwd())
    def test_claude_partial_read_input_reports_path_before_tool_result(self):
        events=[];protocol=ClaudeProtocol(lambda _:None,events.append,os.getcwd(),'Kitchen')
        protocol.receive({'type':'stream_event','event':{'type':'content_block_start','index':0,'content_block':{'type':'tool_use','id':'read','name':'Read','input':{}}}})
        protocol.receive({'type':'stream_event','event':{'type':'content_block_delta','index':0,'delta':{'type':'input_json_delta','partial_json':json.dumps({'file_path':'player.py'})}}})
        protocol.receive({'type':'stream_event','event':{'type':'content_block_stop','index':0}})
        self.assertEqual(events[-1]['paths'],[os.path.join(os.getcwd(),'player.py')]);self.assertEqual(events[-1]['status'],'running')
    def test_literal_powershell_reads_and_agy_path_field_are_observed(self):
        expected=os.path.join(os.getcwd(),'player.py')
        self.assertEqual(extract({'command':'rak ui read "player.py"'},cwd=os.getcwd()),[expected])
        self.assertEqual(extract({'command':"Get-Content -LiteralPath 'player.py' -Raw"},cwd=os.getcwd()),[expected])
        self.assertEqual(extract({'command':"$text = 'Get-Content player.py'; Write-Host $text"},cwd=os.getcwd()),[])
        self.assertEqual(extract({'command':'Get-Content $unknown'},cwd=os.getcwd()),[])
        events=[];protocol=AgyProtocol(lambda _:None,events.append,os.getcwd(),'Kitchen')
        protocol.receive({'event':'step_update','step_update':{'step_type':'tool','step_index':1,'state':'RUNNING','tool_name':'read_file','tool_info':{'parameters':{'Path':'player.py'}}}})
        self.assertEqual(events[-1]['paths'],[expected])
