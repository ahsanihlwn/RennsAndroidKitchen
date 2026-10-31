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

from PySide6.QtCore import QCoreApplication
from core.agent_process import AgentProcess,hidden_startup_options


@unittest.skipUnless(os.name=='nt','Windows console inheritance')
class HiddenAgentProcessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QCoreApplication.instance() or QCoreApplication([])

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
            deadline=time.monotonic()+15
            while not any(kind in ('exit','launch_error') for kind,_ in packets):
                self.app.processEvents();self.assertLess(time.monotonic(),deadline);time.sleep(.005)
            self.assertFalse([p for p in packets if p[0]=='launch_error'],packets)
            result=next(payload for kind,payload in packets if kind=='message')
            self.assertEqual(result,{'parent':{'console':True,'visible':False},'child':{'console':True,'visible':False}})
            self.assertEqual(next(payload for kind,payload in packets if kind=='exit'),0)
            process.close_input()

    def test_model_probe_uses_the_same_hidden_startup_policy(self):
        options=hidden_startup_options()
        self.assertEqual(options['creationflags'],subprocess.CREATE_NEW_CONSOLE)
        self.assertTrue(options['startupinfo'].dwFlags & subprocess.STARTF_USESHOWWINDOW)
        self.assertEqual(options['startupinfo'].wShowWindow,subprocess.SW_HIDE)

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
