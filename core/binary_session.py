# SPDX-License-Identifier: MPL-2.0
"""Local analysis sessions shared by Binary Lab and the CLI.

Ghidra is discovered externally, never linked or bundled by this module.
Sessions are content-addressed, cross-process locked, and kept outside ROMs.
"""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile

from core import binary_core, kitchen_process
from core.app_paths import APP_ROOT
from core.command_context import OperationError

SCRIPT = Path(__file__).with_name('ghidra_scripts') / 'RakBinaryExport.java'
SCHEMA = 1


def storage_root():
    return Path(APP_ROOT) / 'data' / 'binary_lab'


def _read(path, default):
    try: return json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError: return default
    except (UnicodeError, json.JSONDecodeError) as error:
        raise OperationError('Invalid Binary Lab state file: ' + str(path)) from error


def _write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(data, stream, ensure_ascii=False, indent=2)
        os.replace(temporary, path)
    finally:
        if temporary is not None: temporary.unlink(missing_ok=True)


@contextmanager
def _lock(folder):
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / 'session.lock').open('a+b') as stream:
        if stream.tell() == 0: stream.write(b'0'); stream.flush()
        stream.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise OperationError('This binary session is busy in another window or agent. Retry after that operation finishes.') from error
        try: yield
        finally:
            stream.seek(0)
            if os.name == 'nt': msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else: fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _headless(home):
    home = Path(home).expanduser()
    executable = 'analyzeHeadless.bat' if os.name == 'nt' else 'analyzeHeadless'
    candidate = home / 'support' / executable
    return candidate.resolve() if candidate.is_file() else None


def ghidra_status():
    config = _read(storage_root() / 'config.json', {})
    home = os.environ.get('GHIDRA_INSTALL_DIR') or os.environ.get('GHIDRA_HOME') or config.get('ghidra_home')
    launcher = _headless(home) if home else None
    if launcher is None and not home:
        found = shutil.which('analyzeHeadless.bat' if os.name == 'nt' else 'analyzeHeadless')
        if found: launcher = Path(found).resolve()
    return {'available': launcher is not None, 'launcher': str(launcher) if launcher else '',
            'message': 'Ghidra ready' if launcher else 'Choose an installed Ghidra folder to enable decompilation. Ghidra also needs its supported JDK.'}


def configure_ghidra(home):
    if _headless(home) is None: raise OperationError('The selected folder has no support/analyzeHeadless launcher')
    _write(storage_root() / 'config.json', {'ghidra_home': str(Path(home).resolve())})
    return ghidra_status()


def _address(text):
    text = str(text).lower().removeprefix('0x')
    if not re.fullmatch('[0-9a-f]{1,16}', text): raise ValueError('Address must be a hexadecimal virtual address')
    return text.lstrip('0') or '0'


class BinarySession:
    def __init__(self, path, expected_sha256=None):
        self.path = Path(path).resolve()
        self.sha256 = binary_core.fingerprint(self.path)
        if expected_sha256 and self.sha256 != expected_sha256:
            raise OperationError('Binary changed since inspection. Reload before continuing analysis.')
        self.folder = storage_root() / self.sha256

    def annotations(self):
        return _read(self.folder / 'annotations.json', {'schema_version': SCHEMA, 'sha256': self.sha256, 'revision': 0, 'entries': {}})

    def annotate(self, address, name, comment, expected_revision):
        address = _address(address)
        if len(name) > 512 or len(comment) > 32768: raise ValueError('Annotation exceeds the name/comment limit')
        with _lock(self.folder):
            data = self.annotations()
            if data['revision'] != expected_revision: raise OperationError('Annotations changed in another window or agent. Refresh before saving.')
            data['entries'][address] = {'address': '0x' + address, 'name': name, 'comment': comment}
            data['revision'] += 1
            _write(self.folder / 'annotations.json', data)
            return data

    def _engine_folder(self, launcher):
        properties = launcher.parent.parent / 'Ghidra' / 'application.properties'
        identity = str(launcher) + (properties.read_text(encoding='utf-8') if properties.is_file() else '')
        identity += hashlib.sha256(SCRIPT.read_bytes()).hexdigest()
        key = hashlib.sha256(identity.encode()).hexdigest()[:20]
        return self.folder / ('ghidra-' + key)

    def analyze(self, address=None):
        status = ghidra_status()
        if not status['available']: raise OperationError(status['message'])
        launcher = Path(status['launcher'])
        folder = self._engine_folder(launcher)
        address = _address(address) if address is not None else None
        output = folder / ('index.json' if address is None else 'function-' + address + '.json')
        with _lock(folder):
            cached = _read(output, None)
            if cached is not None: return cached
            index = folder / 'index.json'
            project = folder / 'rak.gpr'
            # Only a successful index makes a project reusable. Failed imports
            # remain preserved in this folder for diagnostics and manual recovery.
            reusable = project.is_file() and index.is_file()
            if address is not None and not reusable:
                raise OperationError('Run Ghidra analysis before requesting a function')
            snapshot = folder / 'input.bin'
            if not snapshot.is_file():
                partial = folder / 'input.partial'
                shutil.copyfile(self.path, partial)
                if binary_core.fingerprint(partial) != self.sha256:
                    partial.unlink(missing_ok=True)
                    raise OperationError('Binary changed while creating the analysis snapshot. Reload it.')
                os.replace(partial, snapshot)
            elif binary_core.fingerprint(snapshot) != self.sha256:
                raise OperationError('Analysis snapshot is damaged. Preserve this session and create a fresh cache before retrying.')
            partial_output = folder / 'export.partial.json'
            partial_output.unlink(missing_ok=True)
            command = [str(launcher), str(folder), 'rak']
            if reusable: command += ['-process', 'input.bin', '-noanalysis', '-readOnly']
            else: command += ['-import', str(snapshot), '-overwrite', '-analysisTimeoutPerFile', '600']
            command += ['-scriptPath', str(SCRIPT.parent), '-postScript', SCRIPT.name,
                        str(partial_output), address if address is not None else 'index',
                        '-log', str(folder / 'ghidra.log'), '-scriptlog', str(folder / 'script.log')]
            try:
                process = kitchen_process.run(command, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=900)
            except kitchen_process.TimeoutExpired as error:
                _write(folder / 'last_process.json', {'command': command, 'timeout': True, 'stdout': str(error.stdout or ''), 'stderr': str(error.stderr or '')})
                raise OperationError('Ghidra analysis timed out. Logs and project are preserved at ' + str(folder)) from error
            _write(folder / 'last_process.json', {'command': command, 'exit_code': process.returncode, 'stdout': process.stdout, 'stderr': process.stderr})
            if process.returncode or not partial_output.is_file():
                raise OperationError(f'Ghidra analysis failed (exit {process.returncode}). Logs: {folder}\n\n{process.stdout}\n{process.stderr}')
            data = _read(partial_output, {})
            if not isinstance(data,dict):raise OperationError('Ghidra returned an invalid JSON object. Logs: '+str(folder))
            if data.get('schema_version') != SCHEMA or data.get('error'):
                raise OperationError('Ghidra export failed: ' + str(data.get('error', 'Invalid export schema')) + '\nLogs: ' + str(folder))
            data.update(sha256=self.sha256, session=str(folder), engine='ghidra')
            _write(output, data)
            partial_output.unlink(missing_ok=True)
            return data
