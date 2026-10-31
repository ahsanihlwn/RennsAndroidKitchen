# SPDX-License-Identifier: MPL-2.0
"""Native-file checkout roundtrip benchmark in a disposable owned workspace."""
import argparse
import cProfile
import faulthandler
import json
from pathlib import Path
import shutil
import sys
import tempfile
import time

REPO=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(REPO))
from main_ext.class_NeoBranchManager import NeoBranchManager


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--files',type=int,default=6400)
    options=parser.parse_args()
    if not 1<=options.files<=100000:parser.error('files must be 1..100000')
    stack_log=(REPO/'build/physical_checkout_benchmark_stacks.log').open('w')
    faulthandler.dump_traceback_later(45,repeat=True,file=stack_log)
    result={'files':options.files,'seconds':{}}
    with tempfile.TemporaryDirectory(prefix='rak-checkout-benchmark-') as folder:
        base=Path(folder);vendor=base/'vendor';vendor.mkdir();(base/'config').mkdir()
        tree=vendor/'files';tree.mkdir()
        for index in range(options.files):(tree/f'file {index}.txt').write_bytes(b'source\n'*100)
        (base/'config/vendor_fs_config').write_text('vendor 0 0 0755\n')
        manager=NeoBranchManager(folder)
        def timed(label,callback):
            started=time.monotonic();callback()
            result['seconds'][label]=round(time.monotonic()-started,3)
            print(label+': '+str(result['seconds'][label]),flush=True)
        def progress(message):print(message,flush=True)
        timed('create',lambda:manager.create_checkout_branch('vendor','port','blue',progress=progress))
        timed('native_copy',lambda:shutil.copytree(tree,vendor/'copied'))
        (tree/'file 0.txt').write_text('native branch edit')
        profiler=cProfile.Profile();profiler.enable()
        timed('switch_source',lambda:manager.checkout_branch('vendor',None,progress=progress))
        profiler.disable();profiler.dump_stats(str(REPO/'build/physical_checkout_engine.prof'))
        assert (tree/'file 0.txt').read_bytes()==b'source\n'*100
        assert not (vendor/'copied').exists()
        timed('switch_branch',lambda:manager.checkout_branch('vendor','port',progress=progress))
        assert (tree/'file 0.txt').read_text()=='native branch edit'
        assert sum(1 for path in (vendor/'copied').iterdir() if path.is_file())==options.files
        result['roundtrip_passed']=True
        print(json.dumps(result),flush=True)
        (REPO/'build/physical_checkout_benchmark.json').write_text(json.dumps(result,indent=2))
    faulthandler.cancel_dump_traceback_later()
    stack_log.close()


if __name__=='__main__':main()
