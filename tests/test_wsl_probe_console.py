# SPDX-License-Identifier: MPL-2.0
"""Startup WSL probes must never reveal the Kitchen process console."""
import subprocess
import sys
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from core import kitchen_process, terminal_execution, wsl_manager
from core.command_context import CommandContext, command_scope, current_command


class WslProbeConsoleTests(unittest.TestCase):
    def setUp(self):
        self.events = []
        self.publisher = SimpleNamespace(event=SimpleNamespace(emit=self.events.append))
        patched = patch.object(terminal_execution, 'publisher_for', return_value=self.publisher)
        patched.start()
        self.addCleanup(patched.stop)

    def wait_done(self):
        deadline = time.monotonic() + 4
        while not any(e[0] == 'done' for e in self.events):
            self.assertLess(time.monotonic(), deadline)
            time.sleep(.005)

    def test_actual_probe_keeps_output_and_logs_without_terminal_events(self):
        command = ['-c', 'import sys;print("Ubuntu");print("probe stderr",file=sys.stderr)']
        with patch.object(wsl_manager, '_wsl_path', return_value=sys.executable), \
             self.assertLogs('core.wsl_manager', level='DEBUG') as log:
            self.assertEqual(wsl_manager._run(command, probe=True), (0, 'Ubuntu'))
        self.assertEqual(self.events, [])
        self.assertIn('probe stderr', '\n'.join(log.output))
        self.assertIn('exit=0', '\n'.join(log.output))

    def test_explicit_operation_still_publishes_after_probe_failure(self):
        with patch.object(wsl_manager, '_wsl_path', return_value=sys.executable):
            rc, output = wsl_manager._run(['-c', 'print("bad probe");raise SystemExit(3)'], probe=True)
            self.assertEqual((rc, output), (3, 'bad probe'))
            self.assertFalse(self.events)
            self.assertEqual(wsl_manager._run(['-c', 'print("user operation")']), (0, 'user operation'))
        self.wait_done()
        self.assertTrue(any(e[0] == 'start' for e in self.events))
        self.assertIn('user operation', ''.join(e[2] for e in self.events if e[0] == 'data'))

    def test_probe_scope_preserves_command_context_and_unrelated_thread_output(self):
        context = CommandContext('fixture')
        with command_scope(context), kitchen_process.background_probe():
            self.assertIs(current_command(), context)
            with kitchen_process.background_probe():
                self.assertIs(current_command(), context)
            worker = threading.Thread(target=lambda: kitchen_process.run(
                [sys.executable, '-c', 'print("independent tool")'], capture_output=True))
            worker.start(); worker.join(4)
            self.assertFalse(worker.is_alive())
        self.wait_done()
        self.assertTrue(any(e[0] == 'start' for e in self.events))

    def test_scope_resets_after_exception(self):
        with self.assertRaises(ValueError), kitchen_process.background_probe():
            raise ValueError('fixture')
        kitchen_process.run([sys.executable, '-c', 'pass'], capture_output=True)
        self.wait_done()
        self.assertTrue(any(e[0] == 'start' for e in self.events))

    def test_every_discovery_call_is_explicitly_a_probe_but_install_is_not(self):
        commands = []
        def run(args, timeout=20, *, probe=False):
            commands.append((args, probe))
            if args == ['-l', '-q']: return 0, 'Ubuntu'
            if args == ['--version']: return 0, 'WSL version: 2.0.0'
            if 'df' in args: return 0, 'Filesystem 1024-blocks Used Available Capacity Mounted on\n/dev/sda 100 40 60 40% /'
            return 0, 'OK'
        with patch.object(wsl_manager, '_wsl_path', return_value='fixture-wsl'), \
             patch.object(wsl_manager, '_run', side_effect=run):
            self.assertTrue(wsl_manager.terminal_is_ready())
            self.assertEqual(wsl_manager.list_disk_usage()[0]['key'], 'WSL:Ubuntu')
            self.assertTrue(commands)
            self.assertTrue(all(probe for _, probe in commands), commands)
            commands.clear()
            self.assertTrue(wsl_manager.install()[0])
            self.assertEqual(commands, [(['--install', '--no-launch'], False)])


if __name__ == '__main__': unittest.main()
