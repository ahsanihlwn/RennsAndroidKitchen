# SPDX-License-Identifier: MPL-2.0
import contextlib
import io
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from core import binary_core, binary_session, native_backend, so_core
from core.command_context import OperationError
from tools.audit_so_viewer import fixture_bytes


class BinaryLabTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.path = self.root / 'sample.so'
        self.path.write_bytes(fixture_bytes())
        patched = patch.object(binary_session, 'storage_root', return_value=self.root / 'sessions')
        patched.start(); self.addCleanup(patched.stop)

    def test_native_elf_preserves_sections_symbols_relocations_and_string_offsets(self):
        module = native_backend._get_native()
        if not getattr(module, 'binary_analyze_json', None): self.skipTest('Build Rust engine first')
        result = binary_core.analyze_file(self.path)
        old = so_core.analyze_file(self.path)
        self.assertEqual(result['engine'], 'rust/goblin')
        self.assertEqual(result['symbols'], old['symbols'])
        self.assertEqual(result['security']['stack_canary'], True)
        self.assertEqual(result['security']['nx_stack'], True)
        self.assertEqual(result['relocations'][0]['symbol'], '__stack_chk_fail')
        raw = self.path.read_bytes()
        for row in result['string_entries']:
            offset = int(row['offset'], 16)
            self.assertEqual(raw[offset:offset+row['length']], row['value'].encode())
        self.assertEqual(len(result['embedded_files']), 2)
        self.assertEqual(binary_core.section_bytes(self.path, result['sections'][1]), b'\xb8\x2a\0\0\0\xc3')

    def test_native_reads_real_pe_and_minimal_mach_o(self):
        module = native_backend._get_native()
        if not getattr(module, 'binary_analyze_json', None): self.skipTest('Build Rust engine first')
        pe = binary_core.analyze_file(sys.executable)
        self.assertEqual(pe['format'], 'PE'); self.assertTrue(pe['sections'])
        self.assertTrue(pe['dynamic']['needed_libraries'])
        self.path.write_bytes(struct.pack('<8I', 0xfeedfacf, 0x1000007, 3, 2, 0, 0, 0x200000, 0))
        macho = binary_core.analyze_file(self.path)
        self.assertEqual(macho['format'], 'Mach-O'); self.assertEqual(macho['header']['class'], 64)

    def test_fallback_and_unsupported_inputs(self):
        with patch.object(native_backend, '_get_native', return_value=None):
            result = binary_core.analyze_file(self.path)
            self.assertEqual(result['engine'], 'python/pyelftools')
        self.path.write_bytes(b'not an executable')
        with self.assertRaises(OperationError): binary_core.analyze_file(self.path)

    def test_stale_annotations_preserve_other_agents_notes_and_survive_new_session(self):
        session = binary_session.BinarySession(self.path)
        data = session.annotate('0x001000', 'check_config', 'Evidence from caller', 0)
        self.assertEqual(data['revision'], 1)
        with self.assertRaisesRegex(OperationError, 'changed'):
            session.annotate('1000', 'stale name', 'stale comment', 0)
        other = binary_session.BinarySession(self.path)
        self.assertEqual(other.annotations()['entries']['1000']['name'], 'check_config')
        self.assertFalse((self.path.parent / 'annotations.json').exists())

    def test_changed_binary_and_invalid_addresses_fail_without_overwriting(self):
        session = binary_session.BinarySession(self.path)
        self.path.write_bytes(fixture_bytes('changed_export'))
        with self.assertRaisesRegex(OperationError, 'changed'):
            binary_session.BinarySession(self.path, session.sha256)
        with self.assertRaises(ValueError): session.annotate('1000;quit', 'bad', '', 0)

    def test_cross_process_lock_rejects_simultaneous_writer(self):
        folder = self.root / 'locked'
        with binary_session._lock(folder):
            code = 'from pathlib import Path;from core.binary_session import _lock;\nwith _lock(Path(__import__("sys").argv[1])): pass'
            result = subprocess.run([sys.executable, '-c', code, str(folder)], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0); self.assertIn('busy', result.stderr)

    def test_headless_reuses_project_and_cached_exports_and_preserves_failure_logs(self):
        session = binary_session.BinarySession(self.path)
        launcher = self.root / 'ghidra' / 'support' / 'analyzeHeadless.bat'
        launcher.parent.mkdir(parents=True); launcher.write_text('fixture')
        calls = []
        def run(command, **kwargs):
            calls.append(command)
            folder = Path(command[1]); (folder / 'rak.gpr').write_text('fixture')
            at = command.index('-postScript')
            output = Path(command[at+2]); operation = command[at+3]
            data = {'schema_version': 1, 'functions': [{'address': '001000', 'name': 'f', 'size': 6}]} if operation == 'index' else {'schema_version': 1, 'pseudocode': 'int f(void) { return 42; }'}
            output.write_text(json.dumps(data))
            return SimpleNamespace(returncode=0, stdout='full analysis output', stderr='')
        with patch.object(binary_session, 'ghidra_status', return_value={'available': True, 'launcher': str(launcher)}), \
             patch.object(binary_session.kitchen_process, 'run', side_effect=run):
            index = session.analyze(); self.assertEqual(session.analyze(), index)
            function = session.analyze('0x1000'); self.assertEqual(session.analyze('1000'), function)
        self.assertEqual(len(calls), 2)
        self.assertIn('-import', calls[0]); self.assertIn('-process', calls[1]); self.assertIn('-noanalysis', calls[1])
        self.assertEqual(Path(index['session'], 'input.bin').read_bytes(), self.path.read_bytes())
        with patch.object(binary_session, 'ghidra_status', return_value={'available': True, 'launcher': str(launcher)}), \
             patch.object(binary_session.kitchen_process, 'run', return_value=SimpleNamespace(returncode=7, stdout='full stdout', stderr='full stderr')):
            with self.assertRaisesRegex(OperationError, 'full stderr'): session.analyze('2000')
        self.assertFalse(Path(index['session'], 'function-2000.json').exists())
        self.assertTrue(Path(index['session'], 'rak.gpr').exists())
        self.assertIn('full stdout', Path(index['session'], 'last_process.json').read_text())

    def test_cli_shares_annotations_and_reports_stale_write_as_failure(self):
        from core.binary_cli import run
        with contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(run(['annotate', str(self.path), '--address', '1000', '--name', 'read_config', '--comment', 'agent evidence', '--revision', '0']), 0)
        self.assertTrue(json.loads(output.getvalue())['ok'])
        self.assertEqual(binary_session.BinarySession(self.path).annotations()['revision'], 1)
        with contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(run(['annotate', str(self.path), '--address', '1000', '--name', 'stale', '--comment', '', '--revision', '0']), 1)
        self.assertFalse(json.loads(output.getvalue())['application_error'])


if __name__ == '__main__': unittest.main()
