# SPDX-License-Identifier: MPL-2.0
"""Presentation text shared by the native ELF inspector's asynchronous jobs."""
import os
import tempfile

from core import so_core
from core.ui_lang import t


def overview(result):
    r=result;h=r['header'];dyn=r['dynamic'];dw=r['dwarf'];ap=r['appended_data']
    lines=[f"{t('so_overview_file_path')}   : {r['filepath']}",
           f"{t('so_overview_file_size')}   : {so_core.human_size(h['size'])} ({h['size']:,} bytes)",
           f"{t('so_overview_arch')}        : {h['abi']} ({h['class']}-bit, {h['endianness']} endian)",
           f"{t('so_overview_file_type')}   : {h['file_type']}",
           f"{t('so_overview_entry_point')} : {h['entry_point']}",
           f"{t('so_overview_sections')}    : {h['num_sections']} {t('so_overview_sections').lower()}",
           f"{t('so_overview_segments')}    : {h['num_segments']} {t('so_overview_segments').lower()}", '',
           f"{t('so_overview_soname')}      : {dyn['soname'] or t('so_none')}",
           f"{t('so_overview_dependencies')}: {', '.join(dyn['needed_libraries']) or t('so_none')}", '',
           f"{t('so_overview_dwarf')} : {t('so_yes') if dw['has_debug_info'] else t('so_no')}"]
    if dw['source_files']:
        lines.append(t('so_overview_source_files')+':')
        lines.extend('  • '+path for path in dw['source_files'][:25])
        if len(dw['source_files'])>25:lines.append('  '+t('so_and_more_files',count=len(dw['source_files'])-25))
    lines.extend(['',f"{t('so_overview_total_strings')} : {r['total_strings']}",f"{t('so_overview_path_strings')}  : {len(r['path_like_strings'])}",f"{t('so_overview_embedded')}      : {len(r['embedded_files'])}"])
    lines.extend(f'  • {ef.label}  [{ef.file_type}, {so_core.human_size(len(ef.data))}]' for ef in r['embedded_files'])
    if ap['trailing_bytes']>0:lines.append(f"\n{t('so_overview_trailing_warn')} ({so_core.human_size(ap['trailing_bytes'])}).")
    return '\n'.join(lines)


def status(result):
    h=result['header'];sec=result['security'];parts=[]
    if sec.get('nx_stack'):parts.append('NX')
    if sec.get('stack_canary'):parts.append('Canary')
    if sec.get('relro') and str(sec['relro']).lower() not in ('none','tidak ada'):parts.append('RELRO: '+sec['relro'])
    return f"{result.get('format','ELF')} · {h['abi']} ({h['class']}-bit, {h['endianness']} endian)  •  {so_core.human_size(h['size'])}"+('  •  '+' • '.join(parts) if parts else '')


def strings_text(result,query=''):
    query=query.lower();strings=result['all_strings'];paths=set(result['path_like_strings'])
    filtered=[s for s in strings if query in s.lower()] if query else strings
    header=t('so_total_strings_count',total=len(strings))
    if query:header+=t('so_matching_filter',count=len(filtered))
    if len(filtered)>3000:header+=t('so_first_limit',limit=3000)
    return header+'\n\n'+'\n'.join('[path?] '+s if s in paths else s for s in filtered[:3000])


def hexdump(data):
    lines=[]
    for offset in range(0,min(len(data),8192),16):
        chunk=data[offset:offset+16];hexes=' '.join(f'{b:02x}' for b in chunk)
        text=''.join(chr(b) if 32<=b<127 else '.' for b in chunk)
        lines.append(f'{offset:08x}  {hexes:<48}  {text}')
    if len(data)>8192:lines.append(f'\n... ({so_core.human_size(len(data)-8192)})')
    return '\n'.join(lines) if lines else t('so_none')


def nested_analysis(data):
    with tempfile.TemporaryDirectory(prefix='rak-nested-elf-') as temporary:
        path=os.path.join(temporary,'embedded.so')
        with open(path,'wb') as output:output.write(data)
        result=so_core.analyze_file(path)
    h=result['header'];exports=result['symbols']['exported']
    lines=[f"Arch: {h['abi']} ({h['class']}-bit, {h['endianness']})",f"Type: {h['file_type']}",f"Sections: {h['num_sections']}  Segments: {h['num_segments']}",'',f"Symbols: {result['symbols']['total']}",'Exported:']
    lines.extend('  [export] '+symbol['demangled'] for symbol in exports[:50])
    if len(exports)>50:lines.append('  '+t('so_and_more_items',count=len(exports)-50))
    return '\n'.join(lines)


def disassemble(path,section,maximum):
    from elftools.elf.elffile import ELFFile
    try:
        with open(path,'rb') as stream:result=so_core.disassemble_section(ELFFile(stream),section,maximum)
        if 'error' in result:raise ValueError(result['error'])
    except Exception as error:return t('so_error_disasm',err=str(error))
    lines=[f"{i['addr']:#010x}:\t{i['mnemonic']:<10}\t{i['op_str']}" for i in result['instructions']]
    lines.append('\n'+t('so_section_bytes_shown',instr=len(lines),bytes=maximum,sec=section))
    return '\n'.join(lines)
