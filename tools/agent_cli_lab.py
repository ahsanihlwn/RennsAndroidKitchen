# SPDX-License-Identifier: MPL-2.0
"""Owned black-box CLI experiments. Each invocation records real stdout/status."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile

REPO=Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:sys.path.insert(0,str(REPO))


def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def create(*,scale=0):
    root=Path(tempfile.mkdtemp(prefix='rak-luna-lab-'))
    (root/'.rak-agent-lab.json').write_text(json.dumps({'repository':str(REPO),'created':time.time(),'scale':scale}),encoding='utf-8')
    (root/'config').mkdir()
    text=root/'vendor/etc';text.mkdir(parents=True)
    (text/'build.prop').write_bytes(b'ro.fixture.name=Scout\nro.fixture.enabled=false\n# keep this comment\n')
    (text/'unchanged.txt').write_bytes(b'untouched source\n')
    (root/'config/vendor_fs_config').write_text('vendor 0 0 0755\nvendor/etc 0 0 0755\nvendor/etc/build.prop 0 0 0644\n',encoding='utf-8')
    (root/'config/vendor_file_contexts').write_text('/vendor(/.*)? u:object_r:vendor_file:s0\n',encoding='utf-8')
    from core.make_symlink import make_symlink_bytes
    system=root/'system';system.mkdir();(system/'vendor-etc').write_bytes(make_symlink_bytes('/vendor/etc'))
    (system/'prop').write_bytes(make_symlink_bytes('/vendor/etc/build.prop'))
    (system/'broken').write_bytes(make_symlink_bytes('/missing'))
    (system/'loop').write_bytes(make_symlink_bytes('loop'))
    contexts=root/'fixture_contexts';contexts.write_text('/system(/.*)? u:object_r:system_file:s0\n',encoding='utf-8')
    from tools.mount_fixture import build_fixture
    image=build_fixture(root,contexts=contexts,name='labfs.img')
    shutil.copy2(image,root/'mystery.zip')
    from core.brotli_tool import compress_to_br
    shutil.copy2(image,root/'compressed.img');ok,output=compress_to_br(str(root/'compressed.img'),keep_source=True)
    if not ok:raise RuntimeError(output)
    (root/'compressed.img').unlink()
    from core.kitchen_conversions import image_to_dat
    shutil.copy2(image,root/'datcopy.img');image_to_dat(str(root/'datcopy.img'));(root/'datcopy.img').unlink()
    # Embed real filesystems, rather than merely naming inert test bytes .img.
    import bz2
    import struct
    from core import update_metadata_pb2 as pb
    manifest=pb.DeltaArchiveManifest(block_size=4096,minor_version=0);payload=b'';content=image.read_bytes()
    for name in ('system','vendor'):
        compressed=bz2.compress(content)
        partition=manifest.partitions.add(partition_name=name);partition.new_partition_info.size=len(content)
        operation=partition.operations.add(type=pb.InstallOperation.REPLACE_BZ,data_offset=len(payload),data_length=len(compressed))
        operation.dst_extents.add(start_block=0,num_blocks=len(content)//4096);payload+=compressed
    encoded=manifest.SerializeToString()
    (root/'payload.bin').write_bytes(struct.pack('>4sQQI',b'CrAU',2,len(encoded),1)+encoded+b'\0'+payload)
    (root/'broken_payload.bin').write_bytes(b'CrAU')
    with zipfile.ZipFile(root/'ROM archive.zip','w') as archive:
        archive.writestr('nested/file with spaces.txt','archive content\n');archive.writestr('second.txt','second\n')
    with zipfile.ZipFile(root/'test.apk','w') as archive:
        archive.writestr('AndroidManifest.xml','<manifest package="rak.fixture"/>');archive.writestr('assets/test.txt','APK data')
    with zipfile.ZipFile(root/'unsafe.zip','w') as archive:archive.writestr('../escaped.txt','must not publish')
    if scale:
        stress=root/'vendor/stress';stress.mkdir()
        for index in range(scale):
            folder=stress/f'group-{index%32:02}';folder.mkdir(exist_ok=True)
            (folder/f'file {index:05}.txt').write_bytes(f'fixture {index}\n'.encode()+b'preserved content\n'*40)
        (root/'large text.txt').write_bytes(b''.join(f'{index:06} verification line\n'.encode() for index in range(100000))+b'FINAL_MARKER\n')
        (root/'large binary.bin').write_bytes(bytes(range(256))*100000)
    files=[root/'vendor/etc/build.prop',root/'vendor/etc/unchanged.txt',root/'config/vendor_fs_config',root/'config/vendor_file_contexts',system/'vendor-etc',system/'prop',image]
    (root/'baseline.json').write_text(json.dumps({str(path.relative_to(root)):digest(path) for path in files},indent=2),encoding='utf-8')
    from core import rak_agent_docs,rak_cli
    from types import SimpleNamespace
    previous=rak_cli._main_ref;rak_cli._main_ref=SimpleNamespace(tree=SimpleNamespace(base=str(root)),tree2=None,active_tree=None,project=str(root))
    try:
        block=rak_agent_docs.generate_dynamic_instructions(str(root))
        for name in rak_agent_docs.AGENT_FILES:rak_agent_docs.update_agent_file(str(root/name),str(root),block=block)
    finally:rak_cli._main_ref=previous
    runner=Path(__file__).resolve()
    (root/'rak.cmd').write_text('@echo off\r\n"'+sys.executable+'" "'+str(runner)+'" run --root "%~dp0." -- %*\r\n',encoding='utf-8',newline='')
    return root


def run(root,arguments):
    root=Path(root).resolve()
    if not (root/'.rak-agent-lab.json').is_file():raise ValueError('This is not an owned RAK agent lab')
    if arguments[:1]==['--']:arguments=arguments[1:]
    # This is the production CLI; the lab cannot contact a user's live ROM.
    script='from core import rak_cli; rak_cli._call_ipc_server=lambda *a,**k:None; rak_cli._main_cli()'
    environment={**os.environ,'PYTHONPATH':str(REPO)}
    started=time.monotonic()
    process=subprocess.run([sys.executable,'-c',script,*arguments],cwd=root,env=environment,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=float(os.environ.get('RAK_LAB_TIMEOUT','900')))
    with (root/'transcript.jsonl').open('a',encoding='utf-8') as stream:
        stream.write(json.dumps({'argv':arguments,'exit_code':process.returncode,'stdout':process.stdout,'stderr':process.stderr,'seconds':round(time.monotonic()-started,3)},ensure_ascii=False)+'\n')
    sys.stdout.write(process.stdout);sys.stderr.write(process.stderr)
    return process.returncode


def audit(root,expected_scale=None,expected_in_place=()):
    root=Path(root).resolve()
    if not (root/'.rak-agent-lab.json').is_file():raise ValueError('This is not an owned RAK agent lab')
    baseline=json.loads((root/'baseline.json').read_text(encoding='utf-8'))
    changed=[name for name,expected in baseline.items() if not (root/name).is_file() or digest(root/name)!=expected]
    if expected_scale is None:expected_scale=json.loads((root/'.rak-agent-lab.json').read_text(encoding='utf-8')).get('scale',0)
    if expected_scale:
        for index in range(expected_scale):
            name=f'vendor/stress/group-{index%32:02}/file {index:05}.txt'
            expected=f'fixture {index}\n'.encode()+b'preserved content\n'*40
            if not (root/name).is_file() or (root/name).read_bytes()!=expected:changed.append(name)
        actual=sum(1 for path in (root/'vendor/stress').rglob('*') if path.is_file())
        if actual!=expected_scale:changed.append('vendor/stress: unexpected source file count '+str(actual))
    transcript=[json.loads(line) for line in (root/'transcript.jsonl').read_text(encoding='utf-8').splitlines()]
    expected_changes=[]
    for name in expected_in_place:
        target=(root/name).resolve()
        if not target.is_relative_to(root):raise ValueError('Expected in-place target is outside the owned lab')
        evidence=[row for row in transcript if len(row['argv'])>=3 and row['argv'][:2] in (['img','write'],['img','push']) and
                  row['exit_code']==0 and (root/row['argv'][2]).resolve()==target]
        if name in changed and evidence:expected_changes.append({'path':name,'argv':evidence[-1]['argv'],'exit_code':0})
    unexpected=[name for name in changed if name not in {row['path'] for row in expected_changes}]
    commands={}
    for row in transcript:
        name=row['argv'][0] if row['argv'] else '(empty)';commands[name]=commands.get(name,0)+1
    inspections=[row for row in transcript if row['argv'][:1]==['inspect'] and row['exit_code']==0][-8:]
    report={'source_baseline_preserved':not changed,'changed_source_files':changed,'expected_in_place_changes':expected_changes,
            'unexpected_source_changes':unexpected,'audit_passed':not unexpected,
            'recorded_commands':len(transcript),'commands_exercised':commands,'stress_source_files_checked':expected_scale,
            'latest_successful_inspections_checked':len(inspections),
            'latest_inspection_shutdown_clean':all('Signal source has been deleted' not in row['stderr'] for row in inspections) if inspections else None,
            'note':'Expected validation failures and differences remain in the full transcript; this audit does not turn them into successful operations.'}
    (root/'AUDIT.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    return report


def main():
    for stream in (sys.stdout,sys.stderr):
        if hasattr(stream,'reconfigure'):stream.reconfigure(encoding='utf-8',errors='replace')
    parser=argparse.ArgumentParser();sub=parser.add_subparsers(dest='action',required=True)
    builder=sub.add_parser('create');builder.add_argument('--scale',type=int,default=0)
    checker=sub.add_parser('audit');checker.add_argument('--root',required=True);checker.add_argument('--expected-scale',type=int)
    checker.add_argument('--expected-in-place',action='append',default=[])
    invoke=sub.add_parser('run');invoke.add_argument('--root',required=True);invoke.add_argument('arguments',nargs=argparse.REMAINDER)
    options=parser.parse_args()
    if options.action=='create':print(create(scale=options.scale));return 0
    if options.action=='audit':
        report=audit(options.root,options.expected_scale,options.expected_in_place);print(json.dumps(report,indent=2));return 0 if report['audit_passed'] else 1
    return run(options.root,options.arguments)


if __name__=='__main__':raise SystemExit(main())
