# SPDX-License-Identifier: MPL-2.0
"""Persistent resident Python client for the MIT-licensed REA Ghidra bridge."""
from __future__ import annotations

import atexit
import hashlib
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import threading
import time
import uuid

from core.command_context import OperationError

REA_SCRIPTS_DIR = Path(__file__).resolve().parent / 'ghidra_scripts' / 'rea'
REA_BRIDGE_SCRIPT = REA_SCRIPTS_DIR / 'ReaGhidraBridge.java'


def ghidra_version(launcher: Path) -> str:
    props = launcher.parent.parent / 'Ghidra' / 'application.properties'
    if props.is_file():
        for line in props.read_text(encoding='utf-8', errors='replace').splitlines():
            if line.startswith('application.version='):
                return line.split('=', 1)[1].strip() or 'unknown'
    return 'unknown'


def normalize_function_dossier(dossier: dict) -> dict:
    proc = dossier.get('procedure', {})
    instructions = []
    for line in dossier.get('assembly', []):
        parts = str(line).split(None, 1)
        instructions.append({'address': parts[0] if parts else '', 'text': parts[1] if len(parts) > 1 else ''})

    refs = []
    for ref in dossier.get('outgoing_references', []):
        refs.append({
            'from': ref.get('from', ''),
            'to': ref.get('to', ''),
            'type': ref.get('type', ''),
            'to_name': ref.get('target_name', '') or ref.get('symbol', ''),
            'string_value': ref.get('string_value', ''),
        })

    blocks = []
    for blk in dossier.get('basic_blocks', []):
        blocks.append({
            'address': blk.get('start', blk.get('address', '')),
            'end': blk.get('end', ''),
            'destinations': list(blk.get('successors', blk.get('destinations', []))),
        })

    return {
        'schema_version': 1,
        'engine': 'rea-ghidra-bridge',
        'function': {
            'address': proc.get('address', ''),
            'name': proc.get('name', ''),
            'signature': proc.get('signature', ''),
            'size': proc.get('size', 0),
            'external': bool(proc.get('external', False)),
            'locals': proc.get('locals', []),
        },
        'pseudocode': dossier.get('pseudocode', ''),
        'instructions': instructions,
        'references': refs,
        'callers': dossier.get('callers', []),
        'callees': dossier.get('callees', []),
        'blocks': blocks,
        'referenced_strings': dossier.get('referenced_strings', []),
        'referenced_names': dossier.get('referenced_names', []),
        'native_value_flow': dossier.get('native_value_flow', {}),
        'native_api': dossier.get('native_api', {}),
        'unresolved_calls': dossier.get('unresolved_calls', []),
        'limitations': dossier.get('limitations', []),
    }


class ReaGhidraClient:
    """Owns one resident headless Ghidra JVM running ReaGhidraBridge.java."""

    def __init__(self, launcher: Path, project_dir: Path, binary_path: Path, sha256: str, *, existing_project: bool = False, startup_timeout: float = 180.0):
        self.launcher = Path(launcher)
        self.project_dir = Path(project_dir)
        self.binary_path = Path(binary_path)
        self.sha256 = sha256.lower()
        self.existing_project = existing_project
        self.startup_timeout = startup_timeout
        self._lock = threading.Lock()
        self._next_id = 1
        self._proc = None
        self._sock = None
        self._reader = None
        self._token = secrets.token_hex(32)
        self._runtime_dir = self.project_dir / f'rea-runtime-{uuid.uuid4().hex[:12]}'
        self._descriptor_path = self._runtime_dir / 'session.json'
        self._endpoint_path = self._runtime_dir / ('endpoint.json' if os.name == 'nt' else 'bridge.sock')
        self._log_path = self.project_dir / 'rea-bridge.log'
        self._info = None
        self._closed = False

    def start(self) -> dict:
        if self._sock is not None and self._info is not None:
            return self._info
        if not REA_BRIDGE_SCRIPT.is_file():
            raise OperationError('REA Ghidra bridge script is missing: ' + str(REA_BRIDGE_SCRIPT))
        self.project_dir.mkdir(parents=True, exist_ok=True)
        self._runtime_dir.mkdir(parents=True, exist_ok=True)

        transport = 'authenticated-loopback-tcp' if os.name == 'nt' else 'unix-socket'
        descriptor = {
            'transport': transport,
            'endpoint_path': str(self._endpoint_path),
            'token': self._token,
            'run_id': str(uuid.uuid4()),
            'target_sha256': self.sha256,
            'provider_version': ghidra_version(self.launcher),
            'profile_digest': hashlib.sha256(REA_BRIDGE_SCRIPT.read_bytes()).hexdigest(),
            'existing_project': bool(self.existing_project),
        }
        self._descriptor_path.write_text(json.dumps(descriptor), encoding='utf-8')

        cmd = [str(self.launcher), str(self.project_dir), 'rak']
        if self.existing_project:
            cmd += ['-process', self.binary_path.name, '-noanalysis', '-readOnly']
        else:
            cmd += ['-import', str(self.binary_path), '-overwrite', '-analysisTimeoutPerFile', '600']
        cmd += [
            '-scriptPath', str(REA_SCRIPTS_DIR),
            '-postScript', REA_BRIDGE_SCRIPT.name, str(self._descriptor_path),
            '-log', str(self.project_dir / 'ghidra.log'),
            '-scriptlog', str(self.project_dir / 'script.log'),
        ]

        kwargs = {}
        if os.name == 'nt':
            kwargs['creationflags'] = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
        self._log_file = self._log_path.open('w', encoding='utf-8', errors='replace')
        self._proc = subprocess.Popen(cmd, stdout=self._log_file, stderr=subprocess.STDOUT, **kwargs)
        try:
            self._connect_and_handshake()
        except Exception:
            self.close()
            raise
        return self._info

    def _connect_and_handshake(self):
        deadline = time.monotonic() + self.startup_timeout
        while time.monotonic() < deadline:
            if self._proc.poll() is not None:
                log_tail = self._read_log_tail()
                raise OperationError(f'REA Ghidra bridge exited prematurely (code {self._proc.returncode}).\n{log_tail}')
            if self._endpoint_path.exists():
                try:
                    if os.name == 'nt':
                        raw = self._endpoint_path.read_text(encoding='utf-8').strip()
                        if raw:
                            ep = json.loads(raw)
                            sock = socket.create_connection((ep['host'], int(ep['port'])), timeout=30.0)
                            self._attach_socket(sock)
                            self._info = self.call('ping', {})
                            return
                    else:
                        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                        sock.settimeout(30.0)
                        sock.connect(str(self._endpoint_path))
                        self._attach_socket(sock)
                        self._info = self.call('ping', {})
                        return
                except (OSError, json.JSONDecodeError, KeyError):
                    pass
            time.sleep(0.1)
        raise OperationError('Timed out waiting for REA Ghidra bridge startup. Log: ' + str(self._log_path))

    def _attach_socket(self, sock: socket.socket):
        sock.settimeout(120.0)
        self._sock = sock
        self._reader = sock.makefile('r', encoding='utf-8', newline='\n')

    def _read_log_tail(self, limit: int = 4000) -> str:
        try:
            text = self._log_path.read_text(encoding='utf-8', errors='replace')
            return text[-limit:]
        except OSError:
            return ''

    @property
    def alive(self) -> bool:
        return not self._closed and self._sock is not None and self._proc is not None and self._proc.poll() is None

    @property
    def document_name(self) -> str:
        return self.binary_path.name

    def call(self, method: str, params: dict | None = None):
        with self._lock:
            if self._sock is None or self._reader is None:
                raise OperationError('REA Ghidra bridge is not connected')
            req_id = self._next_id
            self._next_id += 1
            payload = json.dumps({
                'id': req_id,
                'token': self._token,
                'method': method,
                'params': params or {},
            }, ensure_ascii=False) + '\n'
            try:
                self._sock.sendall(payload.encode('utf-8'))
                line = self._reader.readline()
            except OSError as error:
                raise OperationError(f'REA Ghidra bridge I/O error on {method}: {error}') from error
            if not line:
                raise OperationError('REA Ghidra bridge closed the connection unexpectedly')
            reply = json.loads(line)
            if not reply.get('ok'):
                err = reply.get('error', {})
                raise OperationError(f"Ghidra bridge error [{err.get('code', 'unknown')}]: {err.get('message', 'failed')}")
            return reply.get('result')

    def list_procedures(self) -> list[dict]:
        rows = self.call('list_procedures', {'document': self.document_name})
        out = []
        for item in rows:
            facts = item.get('facts', {})
            out.append({
                'address': item.get('address', ''),
                'name': item.get('value', item.get('name', '')),
                'size': facts.get('size', 0),
                'external': bool(facts.get('external', False)),
            })
        return out

    def analyze_function(self, procedure: str) -> dict:
        dossier = self.call('analyze_function', {'procedure': procedure})
        return normalize_function_dossier(dossier)

    def xrefs_to(self, address: str) -> list[str]:
        return self.call('xrefs', {'document': self.document_name, 'address': address})

    def search_strings(self, query: str) -> list[dict]:
        return self.call('search_strings', {'document': self.document_name, 'query': query, 'mode': 'contains'})

    def close(self):
        self._closed = True
        if self._sock is not None:
            try:
                self.call('shutdown', {})
            except Exception:
                pass
            try:
                if self._reader is not None:
                    self._reader.close()
            except Exception:
                pass
            try:
                self._sock.close()
            except Exception:
                pass
            self._sock = None
            self._reader = None
        if self._proc is not None:
            try:
                self._proc.wait(timeout=5)
            except Exception:
                self._proc.terminate()
            self._proc = None
        if hasattr(self, '_log_file') and self._log_file is not None:
            try:
                self._log_file.close()
            except Exception:
                pass
            self._log_file = None
        for p in (self._descriptor_path, self._endpoint_path):
            try:
                p.unlink(missing_ok=True)
            except OSError:
                pass
        try:
            self._runtime_dir.rmdir()
        except OSError:
            pass


_RESIDENT_POOL: dict[tuple[str, str], ReaGhidraClient] = {}
_POOL_LOCK = threading.Lock()


def get_resident_client(launcher: Path, project_dir: Path, binary_path: Path, sha256: str, *, existing_project: bool = False) -> ReaGhidraClient:
    key = (sha256.lower(), str(project_dir.resolve()))
    with _POOL_LOCK:
        client = _RESIDENT_POOL.get(key)
        if client is not None and client.alive:
            return client
        if client is not None:
            client.close()
        client = ReaGhidraClient(launcher, project_dir, binary_path, sha256, existing_project=existing_project)
        client.start()
        _RESIDENT_POOL[key] = client
        return client


def shutdown_all_residents():
    with _POOL_LOCK:
        clients = list(_RESIDENT_POOL.values())
        _RESIDENT_POOL.clear()
    for client in clients:
        client.close()


atexit.register(shutdown_all_residents)
