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
        java_class = self.root / 'Calculator.class'
        java_class.write_bytes(b'\xca\xfe\xba\xbe\x00\x00\x00A\x003\n\x00\x02\x00\x03\x07' + b'\x00' * 128)
        self.assertEqual(binary_core.identify(java_class), '')
        with self.assertRaises(OperationError): binary_core.analyze_file(java_class)
        fat_macho = self.root / 'universal'
        fat_header = struct.pack('>IIIIIII', 0xcafebabe, 1, 0x0100000c, 0, 0x20, 0x10, 14)
        fat_macho.write_bytes(fat_header + b'\x00' * 32)
        self.assertEqual(binary_core.identify(fat_macho), 'Mach-O')
        if getattr(native_backend._get_native(), 'binary_analyze_json', None):
            with self.assertRaisesRegex(OperationError, 'Universal Mach-O'):
                binary_core.analyze_file(fat_macho)

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

    def test_path_filter_rejects_sentence_and_numeric_noise(self):
        self.assertFalse(so_core.is_path_like('Please fix kernel include/linux/bpf_types.h'))
        self.assertFalse(so_core.is_path_like('R/i.8'))
        self.assertTrue(so_core.is_path_like('arch/arm64/crypto/sha2-ce-glue.c'))
        self.assertTrue(so_core.is_path_like('rust/kernel/device.rs'))
        self.assertTrue(so_core.is_path_like('arch/arm64/kernel/probes/kprobes_trampoline.S'))
        self.assertTrue(so_core.is_path_like('/system/lib64/libnative.so'))

    def _write_arm64_sample(self):
        # Minimal ELF64 AArch64 shared object with ADRP+ADD referencing a .rodata string.
        names = ['', '.text', '.rodata', '.dynstr', '.dynsym', '.shstrtab']
        shstr = b'\0'
        name_off = {}
        for name in names[1:]:
            name_off[name] = len(shstr)
            shstr += name.encode() + b'\0'
        dynstr = b'\0sha2_init\0'
        dynsym = b'\0' * 24 + struct.pack('<IBBHQQ', 1, 0x12, 0, 1, 0x1000, 12)
        text = struct.pack('<III', 0xB0000000, 0x91000000, 0xD65F03C0)  # adrp x0, 0x2000; add x0, x0, #0; ret
        rodata = b'arch/arm64/crypto/sha2-ce-glue.c\0'
        chunks = [b'', text, rodata, dynstr, dynsym, shstr]
        data = bytearray(64 + 56)
        offsets = [0]
        for chunk in chunks[1:]:
            data.extend(b'\0' * ((-len(data)) % 8))
            offsets.append(len(data))
            data.extend(chunk)
        data.extend(b'\0' * ((-len(data)) % 8))
        shoff = len(data)
        sections = [(0, 0, 0, 0, 0, 0, 0, 0, 0, 0)]
        specs = [(1, 6, 0x1000, 0, 0, 4, 0), (1, 2, 0x2000, 0, 0, 1, 0), (3, 2, 0x3000, 0, 0, 1, 0), (11, 2, 0x4000, 3, 1, 8, 24), (3, 0, 0, 0, 0, 1, 0)]
        for i, (kind, flags, addr, link, info, align, entsize) in enumerate(specs, 1):
            sections.append((name_off[names[i]], kind, flags, addr, offsets[i], len(chunks[i]), link, info, align, entsize))
        for sec in sections:
            data.extend(struct.pack('<IIQQQQIIQQ', *sec))
        ident = b'\x7fELF\x02\x01\x01' + b'\0' * 9
        data[:64] = struct.pack('<16sHHIQQQIHHHHHH', ident, 3, 183, 1, 0x1000, 64, shoff, 0, 64, 56, 1, 64, len(sections), 5)
        data[64:120] = struct.pack('<IIQQQQQQ', 1, 5, 0, 0, 0, len(data), len(data), 0x1000)

        arm64_path = self.root / 'kernel_sample.elf'
        arm64_path.write_bytes(bytes(data))
        return arm64_path

    def test_native_decompiler_reconstructs_xref_function(self):
        from core import binary_xref, native_decompiler
        if not native_decompiler.status()['available']:
            self.skipTest('Build the native decompiler: python tools/build_ghidra_native.py')
        path = self._write_arm64_sample()
        analysis = binary_core.analyze_file(path)
        session = binary_session.BinarySession(path)
        ctx = session.find_xrefs(analysis, 'arch/arm64/crypto/sha2-ce-glue.c')
        self.assertIn('[Decompiled Pseudo-C]', ctx['summary_text'])
        self.assertIn('sha2_init', ctx['pseudocode'])
        self.assertIn('0x2000', ctx['pseudocode'])
        cached = session.decompile_native(analysis, '0x1000')
        self.assertEqual(cached['name'], 'sha2_init')
        self.assertTrue((session.folder / 'native' / 'function-1000.json').is_file())
        # A stripped caller is recovered from its prologue instead of being dropped.
        self.assertIsNone(binary_xref.guess_function_start(str(path), analysis, 0x3000))
        from core.binary_cli import run
        with contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(run(['decompile', str(path), '--address', '0x1000', '--native']), 0)
        payload = json.loads(output.getvalue())['result']
        self.assertEqual(payload['engine'], 'ghidra-native')
        self.assertIn('return 0x2000', payload['pseudocode'])
        self.assertTrue(any('ret' in row['text'].lower() for row in payload['instructions']))

    def test_local_aarch64_xref_reconstruction_and_cli_commands(self):
        from core import binary_xref
        from core.binary_cli import run
        arm64_path = self._write_arm64_sample()
        analysis = binary_core.analyze_file(arm64_path)
        ctx = binary_xref.reconstruct_path_context(str(arm64_path), analysis, 'arch/arm64/crypto/sha2-ce-glue.c')
        self.assertEqual(ctx['locations'][0]['va'], '0x2000')
        self.assertEqual(ctx['locations'][0]['section'], '.rodata')
        self.assertTrue(any(x['kind'] == 'ADRP+ADD' and x['function'] == 'sha2_init' and x['to'] == '0x2000' for x in ctx['xrefs']))
        self.assertIn('adrp', ctx['disassembly'].lower())
        self.assertIn('sha2_init', ctx['summary_text'])

        for argv, check in (
            (['engines'], lambda r: 'capstone' in r and 'rea' in r and r['rea']['available']),
            (['strings', str(arm64_path), '--paths'], lambda r: any(row['va'] == '0x2000' for row in r)),
            (['xref', str(arm64_path), '--query', 'sha2-ce-glue.c'], lambda r: len(r['xrefs']) >= 1),
            (['disasm', str(arm64_path), '--address', '0x1000', '--bytes', '12'], lambda r: 'ret' in r['disassembly'].lower()),
            (['search-bytes', str(arm64_path), '--pattern', 'sha2-ce-glue'], lambda r: any(row['va'] == '0x2012' and row['section'] == '.rodata' and row['function'] == '' for row in r)),
            (['search-bytes', str(arm64_path), '--pattern', 'c0 03 5f d6', '--hex'], lambda r: any(row['function'] == 'sha2_init' for row in r)),
        ):
            with contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(run(argv), 0)
            payload = json.loads(output.getvalue())
            self.assertTrue(payload['ok'])
            self.assertTrue(check(payload['result']), (argv, payload))

    def test_rea_ghidra_bridge_client_protocol_and_normalization(self):
        import socket
        import threading
        from core import rea_ghidra_client

        self.assertTrue(binary_session.rea_status()['available'])
        launcher = self.root / 'ghidra' / 'support' / ('analyzeHeadless.bat' if sys.platform == 'win32' else 'analyzeHeadless')
        launcher.parent.mkdir(parents=True, exist_ok=True)
        launcher.write_text('fixture')
        (self.root / 'ghidra' / 'Ghidra').mkdir(parents=True, exist_ok=True)
        (self.root / 'ghidra' / 'Ghidra' / 'application.properties').write_text('application.version=12.1\n')

        client = rea_ghidra_client.ReaGhidraClient(
            launcher, self.root / 'proj', self.path, binary_core.fingerprint(self.path), startup_timeout=5.0
        )

        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.bind(('127.0.0.1', 0))
        srv.listen(1)
        port = srv.getsockname()[1]
        server_errors = []

        def serve():
            try:
                conn, _ = srv.accept()
                with conn, conn.makefile('r', encoding='utf-8') as reader:
                    for raw in reader:
                        req = json.loads(raw)
                        if req['token'] != client._token:
                            resp = {'id': req['id'], 'ok': False, 'error': {'code': 'auth', 'message': 'bad token'}}
                        elif req['method'] == 'ping':
                            resp = {'id': req['id'], 'ok': True, 'result': {'name': 'REA Ghidra bridge', 'analysis_complete': True}}
                        elif req['method'] == 'list_procedures':
                            resp = {'id': req['id'], 'ok': True, 'result': [{'address': '0x1000', 'value': 'native_export', 'facts': {'size': 6, 'external': False}}]}
                        elif req['method'] == 'analyze_function':
                            resp = {'id': req['id'], 'ok': True, 'result': {
                                'procedure': {'address': '0x1000', 'name': 'native_export', 'signature': 'int native_export(void)', 'size': 6, 'locals': [{'name': 'x', 'type': 'int', 'storage': 'eax'}]},
                                'pseudocode': 'int native_export(void) { return 42; }',
                                'assembly': ['0x1000  MOV EAX, 0x2a', '0x1005  RET'],
                                'outgoing_references': [{'from': '0x1000', 'to': '0x2000', 'type': 'DATA', 'target_name': 's_path', 'string_value': '/system/lib64/libnative.so'}],
                                'callers': [], 'callees': [],
                                'basic_blocks': [{'start': '0x1000', 'end': '0x1006', 'successors': []}],
                                'referenced_strings': [{'address': '0x2000', 'value': '/system/lib64/libnative.so'}],
                                'native_value_flow': {'nodes': 1},
                            }}
                        elif req['method'] == 'shutdown':
                            resp = {'id': req['id'], 'ok': True, 'result': {'shutdown': True}}
                            conn.sendall((json.dumps(resp) + '\n').encode())
                            break
                        else:
                            resp = {'id': req['id'], 'ok': False, 'error': {'code': 'unknown', 'message': 'unsupported'}}
                        conn.sendall((json.dumps(resp) + '\n').encode())
            except Exception as exc:
                server_errors.append(exc)
            finally:
                srv.close()

        thread = threading.Thread(target=serve, daemon=True)
        thread.start()

        class FakeProc:
            returncode = None
            def poll(self): return None
            def wait(self, timeout=None): return 0
            def terminate(self): pass

        def fake_popen(cmd, **kwargs):
            client._runtime_dir.mkdir(parents=True, exist_ok=True)
            client._endpoint_path.write_text(json.dumps({'host': '127.0.0.1', 'port': port}), encoding='utf-8')
            return FakeProc()

        with patch.object(rea_ghidra_client.subprocess, 'Popen', side_effect=fake_popen):
            info = client.start()
            self.assertTrue(info['analysis_complete'])
            procs = client.list_procedures()
            self.assertEqual(procs[0]['name'], 'native_export')
            fn = client.analyze_function('0x1000')
            self.assertEqual(fn['blocks'], [{'address': '0x1000', 'end': '0x1006', 'destinations': []}])
            self.assertEqual(fn['references'][0]['string_value'], '/system/lib64/libnative.so')
            self.assertEqual(fn['function']['locals'][0]['name'], 'x')
            client.close()
        thread.join(timeout=3)
        self.assertFalse(server_errors)

    def test_reconstruct_source_tree_creates_files_and_manifest(self):
        from core import binary_reconstruct, binary_cli
        out_dir = self.root / 'reconstructed'
        res = binary_reconstruct.reconstruct_source_tree(str(self.path), output_dir=str(out_dir))
        self.assertTrue((out_dir / 'reconstruction_manifest.json').is_file())
        self.assertTrue((out_dir / 'README.md').is_file())
        self.assertGreaterEqual(res['total_files'], 1)
        # Verify CLI subcommand
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            exit_code = binary_cli.run(['reconstruct-tree', str(self.path), '--output', str(out_dir / 'cli_out')])
        self.assertEqual(exit_code, 0)
        payload = json.loads(stdout.getvalue())
        self.assertTrue(payload['ok'])
        self.assertTrue((out_dir / 'cli_out' / 'reconstruction_manifest.json').is_file())


if __name__ == '__main__': unittest.main()
