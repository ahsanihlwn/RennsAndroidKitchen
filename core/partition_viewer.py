# SPDX-License-Identifier: MPL-2.0
"""Payload and Super viewer operations without their QWidget owners."""
import re


def payload_partitions(path):
    from core.payload_extract import init_payload_info
    with open(path,'rb') as stream:return [p.partition_name for p in init_payload_info(stream).partitions]


def _payload_service():
    try:
        from main_ext.config_engine_client import _ServiceConnection,payload_extract_partition
        if _ServiceConnection.get().is_connected():return payload_extract_partition
    except Exception:pass
    return None


def extract_payload(path,partition,out_dir):
    from core.storage_detector import get_optimal_io_workers
    workers=get_optimal_io_workers(out_dir,max_cap=64,hdd_cap=4);service=_payload_service()
    if service:service(payload_path=path,partition=partition,out_dir=out_dir,max_workers=workers)
    else:
        from core.payload_extract import extract_partitions_from_payload
        with open(path,'rb') as stream:extract_partitions_from_payload(stream,[partition],out_dir,max_workers=workers,on_partition_done=None)
    return partition


def _super_command(*arguments):
    from core.command_context import OperationError
    from core.native_backend import super_extract, super_info as native_super_info

    args = list(arguments)
    show_info = False
    partitions = []
    positionals = []
    i = 0
    while i < len(args):
        arg = str(args[i])
        if arg == '--info':
            show_info = True
        elif arg == '-p' and i + 1 < len(args):
            i += 1
            partitions.extend(p.strip() for p in str(args[i]).split(',') if p.strip())
        elif arg.startswith('-p') and len(arg) > 2:
            partitions.extend(p.strip() for p in arg[2:].split(',') if p.strip())
        else:
            positionals.append(arg)
        i += 1

    if not positionals:
        raise OperationError('lpunpack missing super image argument')

    img_path = positionals[0]
    try:
        if show_info:
            info = native_super_info(img_path)
            lines = info.get('raw', '').splitlines()
            for line in lines:
                print('[SUPER][RAW]', line, flush=True)
            return lines
        out_dir = positionals[1] if len(positionals) > 1 else '.'
        super_extract(img_path, out_dir, partitions=partitions or None, normalize=False)
        return []
    except Exception as error:
        raise OperationError(f'lpunpack failed: {arguments}\n{error}') from error


def super_info(path):
    lines=_super_command(path,'--info');partitions=[];active='';in_partition_table=True
    for line in lines:
        stripped=line.strip()
        if stripped=='Partition table:':in_partition_table=True;continue
        if stripped in ('Super partition layout:','Block device table:','Group table:'):
            in_partition_table=False;continue
        if in_partition_table:
            match=re.match(r'^\s*Name:\s+([a-zA-Z0-9_]+)',line)
            if match:partitions.append(match[1]);continue
        match=re.match(r'^\s*Group:\s+([a-zA-Z0-9_]+)',line)
        if match and match[1].endswith(('_a','_b')):active=match[1][-1].upper()
    return {'partitions':partitions,'active_slot':active,'raw':lines}


def extract_super(path,partition,out_dir):
    # The viewer's Extract All does not use the separate --norm conversion path.
    _super_command(*(['-p',partition,path,out_dir] if partition else [path,out_dir]))
    return partition
