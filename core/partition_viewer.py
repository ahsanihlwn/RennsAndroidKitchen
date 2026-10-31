# SPDX-License-Identifier: MPL-2.0
"""Payload and Super viewer operations without their QWidget owners."""
import re
from core import kitchen_process as subprocess


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
    from core.app_paths import BIN_DIR
    from core.binary_handler import resolve_binary
    from core.proc_util import CREATE_NO_WINDOW
    binary=resolve_binary(BIN_DIR,'lpunpack.exe')
    if not binary:raise FileNotFoundError('LPUNPACK NOT FOUND')
    with subprocess.Popen([binary,*arguments],stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,creationflags=CREATE_NO_WINDOW) as process:
        lines=[]
        for line in process.stdout:
            lines.append(line.rstrip());print('[SUPER][RAW]',line.rstrip(),flush=True)
        process.stdout.close();rc=process.wait()
    if rc:
        from core.command_context import OperationError
        raise OperationError(f'lpunpack failed ({rc}): '+str(arguments)+'\n'+'\n'.join(lines))
    return lines


def super_info(path):
    lines=_super_command(path,'--info');partitions=[];active=''
    for line in lines:
        match=re.match(r'^\s*Name:\s+([a-zA-Z0-9_]+)',line)
        if match:partitions.append(match[1]);continue
        match=re.match(r'^\s*Group:\s+([a-zA-Z0-9_]+)',line)
        if match and match[1].endswith(('_a','_b')):active=match[1][-1].upper()
    return {'partitions':partitions,'active_slot':active,'raw':lines}


def extract_super(path,partition,out_dir):
    # The viewer's Extract All does not use the separate --norm conversion path.
    _super_command(*(['-p',partition,path,out_dir] if partition else [path,out_dir]))
    return partition
