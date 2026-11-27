# SPDX-License-Identifier: MPL-2.0
import subprocess
import sys
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from core import kitchen_process
from core import terminal_execution


class KitchenProcessTests(unittest.TestCase):
    def setUp(self):
        self.events=[]
        publisher=SimpleNamespace(event=SimpleNamespace(emit=self.events.append))
        patched=patch.object(terminal_execution,'publisher_for',return_value=publisher)
        patched.start();self.addCleanup(patched.stop)
    def wait_done(self):
        end=time.monotonic()+4
        while not any(event[0]=='done' for event in self.events):
            self.assertLess(time.monotonic(),end);time.sleep(.005)
    def test_text_stdout_and_stderr_are_unchanged_and_mirrored(self):
        result=kitchen_process.run([sys.executable,'-c','import sys; print("hello"); print("error",file=sys.stderr)'],capture_output=True,text=True)
        self.assertEqual(result.stdout,'hello\n');self.assertEqual(result.stderr,'error\n');self.wait_done()
        text=''.join(event[2] for event in self.events if event[0]=='data')
        self.assertIn('hello',text);self.assertIn('error',text)
    @unittest.skipUnless(sys.platform == 'win32', 'Windows console allocation')
    def test_kitchen_tool_does_not_allocate_a_windows_console(self):
        command = [sys.executable, '-c', 'import ctypes; print(ctypes.windll.kernel32.GetConsoleWindow())']
        result = kitchen_process.run(command, capture_output=True, text=True, creationflags=0)
        self.assertEqual(result.stdout.strip(), '0')
        self.wait_done()
        self.assertTrue(any(event[0] == 'data' and '0' in event[2] for event in self.events))
    def test_output_reaches_terminal_before_the_process_exits(self):
        process=kitchen_process.Popen([sys.executable,'-u','-c','import time;print("first",flush=True);time.sleep(.3);print("last")'],stdout=subprocess.PIPE,text=True)
        end=time.monotonic()+3
        while not any(event[0]=='data' and 'first' in event[2] for event in self.events):
            self.assertLess(time.monotonic(),end);time.sleep(.005)
        self.assertIsNone(process.poll())
        self.assertEqual(process.communicate()[0],'first\nlast\n');self.wait_done()
    def test_binary_input_and_output_remain_bytes(self):
        result=kitchen_process.run([sys.executable,'-c','import sys;sys.stdout.buffer.write(sys.stdin.buffer.read().upper())'],input=b'abc\x00def',capture_output=True)
        self.assertEqual(result.stdout,b'ABC\x00DEF');self.wait_done()
    def test_inherited_output_does_not_fill_a_hidden_pipe(self):
        result=kitchen_process.run([sys.executable,'-c','print("x"*200000)'],timeout=4)
        self.assertIsNone(result.stdout);self.assertEqual(result.returncode,0);self.wait_done()
    def test_timeout_preserves_partial_output_and_stops_process(self):
        with self.assertRaises(subprocess.TimeoutExpired) as raised:
            kitchen_process.run([sys.executable,'-u','-c','import time;print("first",flush=True);time.sleep(5)'],capture_output=True,text=True,timeout=.15)
        self.assertIn('first',raised.exception.stdout);self.wait_done()
    def test_nonzero_exit_keeps_full_output(self):
        with self.assertRaises(subprocess.CalledProcessError) as raised:
            kitchen_process.check_output([sys.executable,'-c','print("failed");raise SystemExit(3)'],text=True)
        self.assertEqual(raised.exception.returncode,3);self.assertIn('failed',raised.exception.output);self.wait_done()
    def test_library_jobs_publish_operation_result_and_full_exception(self):
        with terminal_execution.library_operation('rak archive extract ROM.zip') as output:output('written: system.img\n')
        text=''.join(event[2] for event in self.events if event[0]=='library')
        self.assertIn('rak archive extract ROM.zip',text);self.assertIn('system.img',text);self.assertIn('[operation completed]',text)
        self.events.clear()
        with self.assertRaises(TypeError),terminal_execution.library_operation('rak payload extract payload.bin'):
            raise TypeError('LIBRARY_INTERNAL_BUG')
        text=''.join(event[2] for event in self.events if event[0]=='library')
        self.assertIn('Traceback',text);self.assertIn('TypeError: LIBRARY_INTERNAL_BUG',text)
        self.assertIn('[operation failed]',text);self.assertNotIn('[operation completed]',text)


if __name__=='__main__':unittest.main()
