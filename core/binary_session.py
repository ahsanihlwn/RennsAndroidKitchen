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
from threading import Lock

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
def _lock(folder, *, wait=False):
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / 'session.lock').open('a+b') as stream:
        if stream.tell() == 0: stream.write(b'0'); stream.flush()
        stream.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK if wait else msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | (0 if wait else fcntl.LOCK_NB))
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


def rea_status():
    from core.rea_ghidra_client import REA_BRIDGE_SCRIPT
    bridge_ok = REA_BRIDGE_SCRIPT.is_file()
    found = shutil.which('rea.cmd' if os.name == 'nt' else 'rea') or shutil.which('rea')
    return {
        'available': bridge_ok,
        'bridge_script': str(REA_BRIDGE_SCRIPT) if bridge_ok else '',
        'cli_available': found is not None,
        'launcher': str(Path(found).resolve()) if found else '',
        'message': 'REA resident Ghidra bridge built-in (MIT)' if bridge_ok else 'REA bridge scripts missing.',
    }


def _address(text):
    if type(text) is int:
        if not 0 <= text < 1 << 64:
            raise ValueError('Address must be a hexadecimal virtual address')
        return f'{text:x}'
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
        self._native_batch_write = Lock()

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

    def _prepare_snapshot(self, folder):
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
        return snapshot

    def resident_client(self):
        from core import rea_ghidra_client
        status = ghidra_status()
        if not status['available']:
            raise OperationError(status['message'])
        launcher = Path(status['launcher'])
        folder = self._engine_folder(launcher)
        with _lock(folder):
            snapshot = self._prepare_snapshot(folder)
            reusable = (folder / 'rak.gpr').is_file() and (folder / 'index.json').is_file()
        return rea_ghidra_client.get_resident_client(
            launcher, folder, snapshot, self.sha256, existing_project=reusable
        )

    def analyze_resident(self, address=None):
        status = ghidra_status()
        if not status['available']:
            raise OperationError(status['message'])
        launcher = Path(status['launcher'])
        folder = self._engine_folder(launcher)
        norm_addr = _address(address) if address is not None else None
        output = folder / ('index.json' if norm_addr is None else 'function-' + norm_addr + '.json')
        cached = _read(output, None)
        if cached is not None:
            return cached
        client = self.resident_client()
        if norm_addr is None:
            functions = client.list_procedures()
            data = {
                'schema_version': SCHEMA,
                'sha256': self.sha256,
                'session': str(folder),
                'engine': 'rea-ghidra-bridge',
                'functions': functions,
            }
        else:
            data = client.analyze_function('0x' + norm_addr)
            data.update(sha256=self.sha256, session=str(folder))
        _write(output, data)
        return data

    def rea_rpc(self, method, params=None):
        client = self.resident_client()
        return client.call(method, params or {})

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
            snapshot = self._prepare_snapshot(folder)
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

    def _current_engine_folder(self):
        status = ghidra_status()
        if not status['available']:
            return None
        return self._engine_folder(Path(status['launcher']))

    def cached_index(self):
        folder = self._current_engine_folder()
        if folder is None:
            return None
        return _read(folder / 'index.json', None)

    def cached_functions(self):
        folder = self._current_engine_folder()
        if folder is None or not folder.is_dir():
            return {}
        out = {}
        for item in folder.glob('function-*.json'):
            key = item.stem.removeprefix('function-')
            try:
                data = _read(item, None)
                if isinstance(data, dict):
                    out[key] = data
            except OperationError:
                continue
        return out

    def decompile_native(self, result, address):
        """Pseudo-C from the vendored Ghidra decompiler; cached per binary hash."""
        from core import native_decompiler
        norm = _address(address)
        folder = self.folder / 'native' / result.get('_native_cache_tag', '')
        output = folder / ('function-' + norm + '.json')
        cached = _read(output, None)
        if cached is not None:
            return cached
        data = native_decompiler.decompile(str(self.path), result, int(norm, 16))
        # Native decompilation uses private inputs. Lock only the shared cache write.
        with self._native_batch_write, _lock(folder, wait=True):
            cached = _read(output, None)
            if cached is not None:
                return cached
            _write(output, data)
        return data

    def decompile_native_batch(self, result, addresses):
        """Batch decompile multiple functions in a single decompiler process."""
        from core import native_decompiler
        folder = self.folder / 'native' / result.get('_native_cache_tag', '')
        folder.mkdir(parents=True, exist_ok=True)
        needed = []
        out = {}
        for a in addresses:
            norm = _address(a)
            cached = _read(folder / ('function-' + norm + '.json'), None)
            if cached is not None:
                out[int(norm, 16)] = cached
            else:
                needed.append(int(norm, 16))
        if needed:
            decompiled = native_decompiler.decompile_batch(str(self.path), result, needed)
            with self._native_batch_write, _lock(folder, wait=True):
                for addr_int, data in decompiled.items():
                    norm = f"{addr_int:x}"
                    output = folder / ('function-' + norm + '.json')
                    cached = _read(output, None)
                    if cached is None:
                        _write(output, data)
                    out[addr_int] = cached if cached is not None else data
        return out

    def native_function(self, result, address):
        """Function dossier from the native decompiler plus a Capstone listing."""
        data = self.decompile_native(result, address)
        entry = int(_address(address), 16)
        instructions = []
        try:
            text = binary_core.disassemble_va(str(self.path), result, entry, 2048)
        except OperationError:
            text = ''
        for line in text.splitlines():
            head, _, body = line.partition(':')
            instructions.append({'address': head.strip(), 'text': body.strip()})
        return {
            'schema_version': SCHEMA, 'engine': 'ghidra-native', 'sha256': self.sha256, 'session': str(self.folder),
            'function': {'address': data['address'], 'name': data['name'], 'signature': data['signature'], 'size': 0},
            'pseudocode': data['pseudocode'], 'instructions': instructions,
            'references': [], 'callers': [], 'callees': [], 'blocks': [],
        }

    def find_xrefs(self, result, query):
        from core import binary_xref, native_decompiler
        decompile = (lambda addr: self.decompile_native(result, addr)) if native_decompiler.status()['available'] else None
        return binary_xref.reconstruct_path_context(
            str(self.path),
            result,
            query,
            ghidra_index=self.cached_index(),
            cached_functions=self.cached_functions(),
            decompile=decompile,
        )

    def reconstruct_tree(self, result, output_dir=None, progress_callback=None, max_files=None):
        from core import binary_reconstruct
        return binary_reconstruct.reconstruct_source_tree(
            str(self.path),
            output_dir=output_dir,
            progress_callback=progress_callback,
            max_files=max_files,
            analysis=result,
        )
