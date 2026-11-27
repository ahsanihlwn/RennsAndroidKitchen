# SPDX-License-Identifier: MPL-2.0
"""Binary Lab's shared read-only engine for GUI and agents.

Rust handles structural parsing; Ghidra is an optional external process.
ELF-specific DWARF and resource inspection remains in the existing adapter.
"""
import hashlib
import json
import os
import re
import time

from core import native_backend, so_core
from core.command_context import OperationError

MAX_BYTES = 512 * 1024 * 1024
RAW_ARM64_BASE = 0x100000


def _is_fat_macho(path, magic):
    if len(magic) < 28:
        return False
    endian = 'big' if magic[:4] == b'\xca\xfe\xba\xbe' else 'little'
    nfat_arch = int.from_bytes(magic[4:8], endian)
    # Java .class files share 0xCAFEBABE with minor/major version (major >= 45) at bytes 4..8.
    if not 1 <= nfat_arch <= 32:
        return False
    file_size = os.path.getsize(path)
    if file_size < 8 + nfat_arch * 20:
        return False
    offset = int.from_bytes(magic[16:20], endian)
    size = int.from_bytes(magic[20:24], endian)
    return offset >= 8 + nfat_arch * 20 and size > 0 and offset + size <= file_size


def identify(path):
    with open(path, 'rb') as stream:
        magic = stream.read(64)
    if magic[:4] == b'\x7fELF': return 'ELF'
    if magic[:2] == b'MZ': return 'PE'
    if magic[:4] in (b'\xfe\xed\xfa\xce', b'\xce\xfa\xed\xfe', b'\xfe\xed\xfa\xcf', b'\xcf\xfa\xed\xfe'): return 'Mach-O'
    if magic[:4] in (b'\xca\xfe\xba\xbe', b'\xbe\xba\xfe\xca') and _is_fat_macho(path, magic): return 'Mach-O'
    if len(magic) >= 64 and magic[0x38:0x3c] == b'ARM\x64': return 'ARM64 Image'
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
    if kind == 'ARM64 Image':
        return _analyze_arm64_image(path, minimum)
    module = native_backend._get_native()
    parser = getattr(module, 'binary_analyze_json', None)
    if parser:
        try:
            result = json.loads(parser(path, minimum))
        except ValueError as error:
            raise OperationError(str(error)) from error
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


def _analyze_arm64_image(path, minimum):
    with open(path, 'rb') as stream:
        data = stream.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise OperationError('Binary exceeds the 512 MiB analysis limit.')
    if data[24] & 1:
        raise OperationError('Raw big-endian ARM64 Image analysis is unavailable')
    pattern = re.compile(rb'[ -~]{%d,}' % minimum)
    spans = []
    data_len = len(data)
    for match in pattern.finditer(data):
        s, e = match.start(), match.end()
        ln = e - s
        nul = e < data_len and data[e] == 0
        raw = match[0]
        tier = 0 if (nul and (ln >= 6 or b'/' in raw)) else (1 if nul else (2 if ln >= 8 else 3))
        spans.append((s, e, tier))
        if (len(spans) & 0x7FFF) == 0:
            time.sleep(0)
    if len(spans) > 200_000:
        chosen = []
        for tier in range(4):
            for s, e, t in spans:
                if t == tier:
                    chosen.append((s, e))
                    if len(chosen) == 200_000:
                        break
            if len(chosen) == 200_000:
                break
        chosen.sort()
    else:
        chosen = [(s, e) for s, e, _ in spans]
    entries = [{
        'offset': hex(s), 'length': e - s,
        'encoding': 'ascii', 'value': data[s:e].decode('ascii'),
    } for s, e in chosen]
    strings = [row['value'] for row in entries]
    paths = [value for value in strings if so_core.is_path_like(value)]
    return {
        'format': 'ARM64 Image', 'engine': 'python/arm64-image', 'schema_version': 1,
        'filepath': path, 'sha256': hashlib.sha256(data).hexdigest(),
        'header': {
            'file': path, 'size': len(data), 'abi': 'AARCH64', 'machine': 'EM_AARCH64', 'class': 64,
            'endianness': 'little', 'file_type': 'Kernel Image', 'entry_point': hex(RAW_ARM64_BASE),
            'num_sections': 1, 'num_segments': 0,
        },
        'sections': [{
            'name': '.image', 'type': 'RAW_IMAGE', 'flags': 6, 'addr': hex(RAW_ARM64_BASE),
            'offset': '0x0', 'size': len(data), 'file_size': len(data),
        }],
        'exec_sections': ['.image'], 'segments': [], 'relocations': [],
        'symbols': {'total': 0, 'exported': [], 'local': [], 'imported': []},
        'dynamic': {'soname': '', 'needed_libraries': [], 'rpath': [], 'runpath': []},
        'security': {}, 'dwarf': {'has_debug_info': False, 'source_files': []},
        'appended_data': {'trailing_bytes': 0},
        'functions': [], 'addr_symbol_map': {}, 'string_entries': entries, 'all_strings': strings,
        'total_strings': len(entries), 'path_like_strings': paths, 'embedded_files': [],
        'path_tree': so_core.build_path_tree(paths, []),
        'warnings': ['The raw Image uses a synthetic base and has no section boundaries.'],
    }


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


def _capstone_decoder(result):
    so_core._setup_capstone_path()
    try: import capstone as cs
    except ImportError as error: raise OperationError('Install capstone for disassembly') from error
    h = result['header']; arch = h['abi'].upper().replace('EM_', '')
    choices = {'X86_64': (cs.CS_ARCH_X86, cs.CS_MODE_64), '386': (cs.CS_ARCH_X86, cs.CS_MODE_32),
               'X86': (cs.CS_ARCH_X86, cs.CS_MODE_32), 'X64': (cs.CS_ARCH_X86, cs.CS_MODE_64),
               'ARM': (cs.CS_ARCH_ARM, cs.CS_MODE_ARM), 'AARCH64': (cs.CS_ARCH_ARM64, cs.CS_MODE_ARM),
               'ARM64': (cs.CS_ARCH_ARM64, cs.CS_MODE_ARM)}
    if arch not in choices: raise OperationError('Use Ghidra for this architecture: ' + arch)
    architecture, mode = choices[arch]
    if h['endianness'] == 'big': mode |= cs.CS_MODE_BIG_ENDIAN
    return cs.Cs(architecture, mode)


def _format_disassembly(instructions, addr_map, highlight_va=None):
    so_core._annotate_targets(instructions, addr_map)
    lines = []
    for i in instructions:
        marker = '>> ' if highlight_va is not None and i['addr'] == highlight_va else '   '
        prefix = marker if highlight_va is not None else ''
        note = f"  ; {i['annotation']}" if i.get('annotation') else ''
        lines.append(f"{prefix}{i['addr']:#010x}:  {i['mnemonic']:<10} {i['op_str']}{note}")
    return '\n'.join(lines)


def disassemble(path, result, section_name, maximum=4096):
    if not 16 <= maximum <= 65536: raise ValueError('Disassembly size must be 16..65536')
    section = next((s for s in result['sections'] if s['name'] == section_name), None)
    if section is None: raise OperationError('Section not found: ' + section_name)
    decoder = _capstone_decoder(result)
    length = min(maximum, section.get('file_size', section['size']))
    with open(path, 'rb') as stream:
        stream.seek(int(section['offset'], 16)); data = stream.read(length)
    instructions = [{'addr': i.address, 'mnemonic': i.mnemonic, 'op_str': i.op_str} for i in decoder.disasm(data, int(section['addr'], 16))]
    return _format_disassembly(instructions, result['addr_symbol_map'])


def disassemble_va(path, result, va, maximum=256, highlight_va=None):
    instructions = [{'addr': i.address, 'mnemonic': i.mnemonic, 'op_str': i.op_str}
                    for i in instructions_va(path, result, va, maximum)]
    return _format_disassembly(instructions, result['addr_symbol_map'], highlight_va=highlight_va)


def instructions_va(path, result, va, maximum=256):
    if not 4 <= maximum <= 65536: raise ValueError('Disassembly size must be 4..65536')
    if isinstance(va, str): va = int(va, 16) if va.lower().startswith('0x') else int(va)
    section = None
    for sec in result.get('sections', []):
        size = sec.get('file_size', sec.get('size', 0))
        if sec.get('type') == 'SHT_NOBITS' or not size: continue
        start = int(sec['addr'], 16)
        if start and start <= va < start + size:
            section = sec; break
    if section is None: raise OperationError(f'Address {hex(va)} is outside mapped file sections')
    decoder = _capstone_decoder(result)
    sec_addr = int(section['addr'], 16)
    sec_off = int(section['offset'], 16)
    sec_size = section.get('file_size', section['size'])
    length = min(maximum, (sec_addr + sec_size) - va)
    with open(path, 'rb') as stream:
        stream.seek(sec_off + (va - sec_addr)); data = stream.read(length)
    return list(decoder.disasm(data, va))
