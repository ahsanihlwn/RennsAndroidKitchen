# SPDX-License-Identifier: MPL-2.0
"""Binary Lab's shared read-only engine for GUI and agents.

Rust handles structural parsing; Ghidra is an optional external process.
ELF-specific DWARF and resource inspection remains in the existing adapter.
"""
import hashlib
import json
import os

from core import native_backend, so_core
from core.command_context import OperationError

MAX_BYTES = 512 * 1024 * 1024


def identify(path):
    with open(path, 'rb') as stream:
        magic = stream.read(4)
    if magic == b'\x7fELF': return 'ELF'
    if magic[:2] == b'MZ': return 'PE'
    if magic in (b'\xfe\xed\xfa\xce', b'\xce\xfa\xed\xfe', b'\xfe\xed\xfa\xcf', b'\xcf\xfa\xed\xfe', b'\xca\xfe\xba\xbe', b'\xbe\xba\xfe\xca'): return 'Mach-O'
    return ''


def fingerprint(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def analyze_file(path, minimum=4):
    from elftools.common.exceptions import ELFError
    try:return _analyze_file(path,minimum)
    except ELFError as error:raise OperationError('ELF inspection failed: '+str(error)) from error


def _analyze_file(path, minimum=4):
    path = os.path.abspath(os.fspath(path))
    if not 4 <= minimum <= 1024: raise ValueError('String minimum must be 4..1024')
    if os.path.getsize(path) > MAX_BYTES: raise OperationError('Binary exceeds the 512 MiB analysis limit. Use Hex for larger files.')
    kind = identify(path)
    if not kind: raise OperationError('Unsupported binary format. Use Hex for raw data.')
    module = native_backend._get_native()
    parser = getattr(module, 'binary_analyze_json', None)
    if parser:
        result = json.loads(parser(path, minimum))
        native_backend._record('binary_analyze')
        result['addr_symbol_map'] = so_core.build_address_symbol_map(result['symbols'])
        # Preserve the ELF resources view without reparsing all symbols in Python.
        embedded = []
        if kind == 'ELF':
            from elftools.elf.elffile import ELFFile
            with open(path, 'rb') as stream:
                elf = ELFFile(stream)
                result['dwarf'] = so_core.analyze_dwarf_paths(elf)
                debug = so_core.analyze_gnu_debugdata(elf)
                result['addr_symbol_map'].update(so_core.build_plt_map(elf, result['relocations']))
            excluded = [(debug[1], debug[2])] if debug else []
            embedded = so_core.scan_embedded_files(path, exclude_ranges=excluded)
            if debug: embedded.insert(0, debug[0])
        entries = result['string_entries']
        result['all_strings'] = [s['value'] for s in entries]
        result['total_strings'] = len(entries)
        result['path_like_strings'] = [s for s in result['all_strings'] if so_core.is_path_like(s)]
        result['embedded_files'] = embedded
        result['path_tree'] = so_core.build_path_tree(result['path_like_strings'], embedded)
    elif kind == 'ELF':
        result = so_core.analyze_file(path, minimum)
        result.update(format=kind, engine='python/pyelftools', schema_version=1, sha256=fingerprint(path), warnings=['Rust engine unavailable; rebuild with python tools/build_native.py --dev'])
    else:
        raise OperationError('PE/Mach-O inspection requires the Rust engine. Run python tools/build_native.py --dev')
    # Symbol aliases and duplicate dynamic/static entries describe one function.
    functions = {}
    for f in result['functions']:
        functions.setdefault(f['addr'], f)
    result['functions'] = sorted(functions.values(), key=lambda f: f['addr'])
    return result


def section_bytes(path, section):
    size = section.get('file_size', section['size'])
    if section['type'] == 'SHT_NOBITS' or not size: return None
    if size > 32 * 1024 * 1024: raise OperationError('Section preview exceeds 32 MiB. Use Hex to inspect it.')
    offset = int(section['offset'], 16)
    with open(path, 'rb') as stream:
        stream.seek(offset)
        data = stream.read(size)
    if len(data) != size: raise OperationError('Section extends beyond the file')
    return data


def disassemble(path, result, section_name, maximum=4096):
    if not 16 <= maximum <= 65536: raise ValueError('Disassembly size must be 16..65536')
    section = next((s for s in result['sections'] if s['name'] == section_name), None)
    if section is None: raise OperationError('Section not found: ' + section_name)
    so_core._setup_capstone_path()
    try: import capstone as cs
    except ImportError as error: raise OperationError('Install capstone for disassembly') from error
    h = result['header']; arch = h['abi'].upper().replace('EM_', '')
    choices = {'X86_64': (cs.CS_ARCH_X86, cs.CS_MODE_64), '386': (cs.CS_ARCH_X86, cs.CS_MODE_32),
               'X86': (cs.CS_ARCH_X86, cs.CS_MODE_32), 'X64': (cs.CS_ARCH_X86, cs.CS_MODE_64),
               'ARM': (cs.CS_ARCH_ARM, cs.CS_MODE_ARM), 'AARCH64': (cs.CS_ARCH_ARM64, cs.CS_MODE_ARM)}
    if arch not in choices: raise OperationError('Use Ghidra for this architecture: ' + arch)
    architecture, mode = choices[arch]
    if h['endianness'] == 'big': mode |= cs.CS_MODE_BIG_ENDIAN
    length = min(maximum, section.get('file_size', section['size']))
    with open(path, 'rb') as stream:
        stream.seek(int(section['offset'], 16)); data = stream.read(length)
    decoder = cs.Cs(architecture, mode)
    instructions = [{'addr': i.address, 'mnemonic': i.mnemonic, 'op_str': i.op_str} for i in decoder.disasm(data, int(section['addr'], 16))]
    so_core._annotate_targets(instructions, result['addr_symbol_map'])
    return '\n'.join(f"{i['addr']:#010x}:  {i['mnemonic']:<10} {i['op_str']}" + (f"  ; {i['annotation']}" if i['annotation'] else '') for i in instructions)
