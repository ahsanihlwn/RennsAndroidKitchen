# SPDX-License-Identifier: MPL-2.0
"""Check real console inheritance, rather than only testing launch flags."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

from unittest.mock import patch

from PySide6.QtCore import QCoreApplication
from core import agent_conpty
from core.agent_models import run_cli_query
from core.agent_process import AgentProcess,hidden_startup_options,spawn_agent_process


@unittest.skipUnless(os.name=='nt','Windows console inheritance')
class HiddenAgentProcessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QCoreApplication.instance() or QCoreApplication([])

    def _wait_packets(self,packets,predicate,seconds=15):
        deadline=time.monotonic()+seconds
        while not predicate():
            self.app.processEvents();self.assertLess(time.monotonic(),deadline,packets);time.sleep(.005)

    def test_hidden_console_is_inherited_by_unmodified_child_processes(self):
        console_probe="""import ctypes,json
k=ctypes.windll.kernel32;u=ctypes.windll.user32
k.GetConsoleWindow.restype=ctypes.c_void_p
u.IsWindowVisible.argtypes=[ctypes.c_void_p]
h=k.GetConsoleWindow()
print(json.dumps({'console':bool(h),'visible':bool(u.IsWindowVisible(h))}),flush=True)
"""
        # The inner subprocess deliberately has no custom creation flags.
        # It models a provider spawning its ordinary shell/helper process.
        script='import subprocess,sys,json\n'+console_probe.replace("print(json.dumps({'console':bool(h),'visible':bool(u.IsWindowVisible(h))}),flush=True)","parent={'console':bool(h),'visible':bool(u.IsWindowVisible(h))}")
        script+='child=json.loads(subprocess.check_output([sys.executable,"-c",'+repr(console_probe)+'],text=True))\nprint(json.dumps({"parent":parent,"child":child}),flush=True)\n'
        packets=[]
        with tempfile.TemporaryDirectory() as root:
            process=AgentProcess();process.event.connect(packets.append)
            process.start([sys.executable,'-c',script],root,os.environ.copy(),str(Path(root)/'console.jsonl'))
            self._wait_packets(packets,lambda:any(kind in ('exit','launch_error') for kind,_ in packets))
            self.assertFalse([p for p in packets if p[0]=='launch_error'],packets)
            self.assertEqual(getattr(process._process,'console_mode',None),'conpty')
            self.assertTrue(agent_conpty.agent_terminal_service.is_active())
            result=next(payload for kind,payload in packets if kind=='message')
            self.assertEqual(result,{'parent':{'console':True,'visible':False},'child':{'console':True,'visible':False}})
            self.assertEqual(next(payload for kind,payload in packets if kind=='exit'),0)
            process.close_input()

    def test_model_probe_uses_conpty_host_and_keeps_fallback_policy(self):
        options=hidden_startup_options()
        self.assertEqual(options['creationflags'],subprocess.CREATE_NEW_CONSOLE)
        self.assertTrue(options['startupinfo'].dwFlags & subprocess.STARTF_USESHOWWINDOW)
        self.assertEqual(options['startupinfo'].wShowWindow,subprocess.SW_HIDE)
        probe="""import ctypes,json,subprocess,sys
k=ctypes.windll.kernel32;u=ctypes.windll.user32
k.GetConsoleWindow.restype=ctypes.c_void_p;u.IsWindowVisible.argtypes=[ctypes.c_void_p]
h=k.GetConsoleWindow()
child=subprocess.check_output([sys.executable,'-c',"import ctypes,json;k=ctypes.windll.kernel32;u=ctypes.windll.user32;k.GetConsoleWindow.restype=ctypes.c_void_p;u.IsWindowVisible.argtypes=[ctypes.c_void_p];h=k.GetConsoleWindow();print(json.dumps({'console':bool(h),'visible':bool(u.IsWindowVisible(h))}))"],text=True)
print(json.dumps({'parent':{'console':bool(h),'visible':bool(u.IsWindowVisible(h))},'child':json.loads(child)}),flush=True)
"""
        with tempfile.TemporaryDirectory() as root:
            ok,result=run_cli_query([sys.executable,'-c',probe],root,Path(root)/'probe.json')
            self.assertTrue(ok,result)
            self.assertEqual(json.loads(result['stdout']),{'parent':{'console':True,'visible':False},'child':{'console':True,'visible':False}})

    def test_direct_conout_does_not_corrupt_json_pipes(self):
        script="""import json,sys
with open('CONOUT$','w') as con:con.write('DIRECT_CONSOLE_WRITE\\n')
print('STDERR_LINE',file=sys.stderr,flush=True)
print(json.dumps({'ok':True,'clean':1}),flush=True)
"""
        packets=[]
        with tempfile.TemporaryDirectory() as root:
            process=AgentProcess();process.event.connect(packets.append)
            process.start([sys.executable,'-c',script],root,os.environ.copy(),str(Path(root)/'conout.jsonl'))
            self._wait_packets(packets,lambda:any(kind in ('exit','launch_error') for kind,_ in packets))
            self.assertFalse([p for p in packets if p[0]=='protocol_error'],packets)
            self.assertEqual([p[1] for p in packets if p[0]=='message'],[{'ok':True,'clean':1}])
            self.assertIn('STDERR_LINE',''.join(p[1] for p in packets if p[0]=='diagnostic'))

    def test_concurrent_processes_do_not_inherit_unrelated_pipe_handles(self):
        sleeper=spawn_agent_process([sys.executable,'-c','import time;time.sleep(3)'],stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        try:
            packets=[]
            with tempfile.TemporaryDirectory() as root:
                process=AgentProcess();process.event.connect(packets.append)
                started=time.monotonic()
                process.start([sys.executable,'-c','import json;print(json.dumps({"fast":True}),flush=True)'],root,os.environ.copy(),str(Path(root)/'fast.jsonl'))
                self._wait_packets(packets,lambda:any(kind in ('exit','launch_error') for kind,_ in packets),seconds=1.5)
                self.assertLess(time.monotonic()-started,1.5)
                self.assertEqual([p[1] for p in packets if p[0]=='message'],[{'fast':True}])
        finally:
            sleeper.terminate()
            if sleeper.stdout:sleeper.stdout.close()
            if sleeper.stderr:sleeper.stderr.close()
            sleeper.wait(timeout=5)

    def test_missing_executable_reports_launch_error_without_fallback(self):
        packets=[]
        with tempfile.TemporaryDirectory() as root:
            process=AgentProcess();process.event.connect(packets.append)
            process.start([str(Path(root)/'does_not_exist_agent.exe')],root,os.environ.copy(),str(Path(root)/'missing.jsonl'))
            self._wait_packets(packets,lambda:any(kind in ('exit','launch_error') for kind,_ in packets))
            self.assertTrue(any(kind=='launch_error' for kind,_ in packets),packets)

    def test_fallback_when_conpty_unsupported(self):
        with patch.object(agent_conpty,'is_supported',return_value=False):
            proc=spawn_agent_process([sys.executable,'-c','print("fallback_ok",flush=True)'],stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
            try:
                out,_=proc.communicate(timeout=5)
                self.assertEqual(out.strip(),'fallback_ok')
                self.assertNotEqual(getattr(proc,'console_mode',None),'conpty')
            finally:
                proc.wait(timeout=5)

    def test_stop_is_confirmed_before_descendants_close_inherited_pipes(self):
        child='import json,time;time.sleep(2);print(json.dumps({"child_finished":True}),flush=True)'
        script='import subprocess,sys,json,time\nsubprocess.Popen([sys.executable,"-c",'+repr(child)+'])\nprint(json.dumps({"ready":True}),flush=True)\nwhile True:time.sleep(60)'
        packets=[]
        with tempfile.TemporaryDirectory() as root:
            process=AgentProcess();process.event.connect(packets.append)
            process.start([sys.executable,'-c',script],root,os.environ.copy(),str(Path(root)/'stop.jsonl'))
            def wait_for(predicate,seconds=6):
                deadline=time.monotonic()+seconds
                while not predicate():self.app.processEvents();self.assertLess(time.monotonic(),deadline,packets);time.sleep(.005)
            wait_for(lambda:any(kind=='message' and payload.get('ready') for kind,payload in packets))
            process.terminate();wait_for(lambda:any(kind=='process_exited' for kind,_ in packets),seconds=1)
            self.assertIsNotNone(process._process.poll())
            self.assertFalse(any(kind=='exit' for kind,_ in packets))
            wait_for(lambda:any(kind=='exit' for kind,_ in packets))
            self.assertTrue(any(kind=='message' and payload.get('child_finished') for kind,payload in packets))


if __name__=='__main__':unittest.main()
