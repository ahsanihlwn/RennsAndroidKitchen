# SPDX-License-Identifier: MPL-2.0
"""HexViewer's binary operations, independent of its presentation."""
import os
from core import bin_analyzer

PAGE_SIZES=(1024,2048,4096,8192,16384)


def analyze_binary(path):
    size=os.path.getsize(path)
    fmt=bin_analyzer.detect_binary_format(path)
    with open(path,'rb') as source:entropy=bin_analyzer.calc_entropy(source.read(min(size,65536)))
    label,color=bin_analyzer.get_entropy_label(entropy)
    hashes=bin_analyzer.calc_hashes(path,max_bytes=20*1024*1024)
    strings=bin_analyzer.extract_strings(path,min_len=4,max_strings=10000)
    if size<1024:formatted=f'{size} B'
    else:
        power=1 if size<1024**2 else 2 if size<1024**3 else 3
        formatted=f'{size/1024**power:.2f} {("KB","MB","GB")[power-1]} ({size:,} B)'
    lines=[f'File Name: {os.path.basename(path)}',f'File Path: {path}',f'File Size: {formatted}','',
           f'Detected Format: {fmt.get("format","Generic Binary")}',
           f'Vendor / Subsystem: {fmt.get("vendor","")} ({fmt.get("category","")})',
           f'Description: {fmt.get("description","")}']
    if fmt.get('extra'):
        lines+=['','Detected Headers / Tags:',*[f'  [{key}] {value}' for key,value in fmt['extra'].items()]]
    lines+=['',f'Extracted Strings: {len(strings):,} found',f'MD5: {hashes.get("md5","-")}',f'SHA-256: {hashes.get("sha256","-")}']
    return {'overview':'\n'.join(lines),'format':fmt,'entropy':entropy,'entropyLabel':label,
            'entropyColor':color,'hashes':hashes,'strings':strings}


def read_page(path,page,size):
    raw=bin_analyzer.read_hex_chunk(path,page*size,size)
    lines=bin_analyzer.format_hex_lines(raw,base_offset=page*size)
    return raw,'\n'.join(f'{line["offset_str"]}  {line["hex_str"]}  |{line["ascii_str"]}|' for line in lines)


def search_pattern(query):
    query=query.strip();hex_clean=query.replace(' ','').replace('0x','')
    if len(hex_clean)>=2 and len(hex_clean)%2==0 and all(ch in '0123456789abcdefABCDEF' for ch in hex_clean):
        return bytes.fromhex(hex_clean)
    return query.encode('utf-8',errors='ignore')


def find_pattern(path,pattern,start,forward=True,cancel=None):
    # Keep the reference's current-page origin and reverse-search limit.
    with open(path,'rb') as source:
        if not forward:
            data=source.read(min(os.path.getsize(path),10*1024*1024))
            return data[:max(0,start)].rfind(pattern)
        source.seek(max(0,start));position=max(0,start);tail=b'';overlap=len(pattern)-1
        while cancel is None or not cancel.is_set():
            chunk=source.read(65536)
            if not chunk:break
            data=tail+chunk;found=data.find(pattern)
            if found>=0:return position-len(tail)+found
            tail=data[-overlap:] if overlap else b'';position+=len(chunk)
    return -1
