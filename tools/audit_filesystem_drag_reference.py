# SPDX-License-Identifier: MPL-2.0
"""Compare filesystem moves with the actual worker from the reference dropEvent."""
import argparse
import ast
import difflib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import uuid
from unittest.mock import patch


def seed(base, *, partial=False, metadata=True):
    (base/'system/etc').mkdir(parents=True)
    (base/'system/etc/a.prop').write_text('fixture bytes\n')
    (base/'plain').mkdir(); (base/'vendor').mkdir(); (base/'config').mkdir()
    if metadata:
        (base/'config/system_file_contexts').write_text('# source rules\n/system u:object_r:system_file:s0\n/system/etc u:object_r:custom_file:s0\n/system/etc/a\\.prop u:object_r:custom_file:s0\n')
        (base/'config/system_fs_config').write_text('system 0 0 0755\nsystem/etc 1000 1001 0750\nsystem/etc/a.prop 1000 1001 0640 capabilities=0x40\n')
        (base/'config/system_fs_options').write_text('--mount-point=/system\n')
    (base/'config/vendor_file_contexts').write_text('/vendor u:object_r:vendor_file:s0\n')
    (base/'config/vendor_fs_config').write_text('vendor 0 0 0755\n')
    (base/'config/vendor_info').write_text('Format: EXT4\n')
    if partial:
        (base/'config/plain_file_contexts').write_text('# partial destination\n/plain u:object_r:plain_file:s0\n')


def reference_move(worker, base, source, directory):
    from core.config_engine_pure import ConfigEngine
    old_rel=source.relative_to(base).as_posix()
    target=directory/source.name; new_rel=target.relative_to(base).as_posix()
    engine=ConfigEngine(str(base)); root=new_rel.split('/')[0]
    scope={'os':os,'shutil':shutil,'captured_conn':None,
           'captured_src_base':str(base),'captured_dst_base':str(base),
           'captured_src_path':str(source),'captured_new_full':str(target),
           'captured_old_root':old_rel.split('/')[0],'captured_old_rel':old_rel,
           'captured_root_changed':old_rel.split('/')[0]!=root,
           'rel_old_f':old_rel,'rel_new_f':new_rel,
           'dest_is_inside_fs':root in engine.root_map_ctx and root in engine.root_map_fs}
    exec(compile(ast.Module(body=[worker],type_ignores=[]),'reference_drop_worker','exec'),scope)
    scope['_worker_same_move']()


def snapshot(base):
    return {path.relative_to(base).as_posix():path.read_text().replace(str(base),'<ROM>')
            for path in sorted(base.rglob('*')) if path.is_file()}


def main():
    repo=Path(__file__).resolve().parents[1];sys.path.insert(0,str(repo));os.chdir(repo)
    parser=argparse.ArgumentParser()
    parser.add_argument('--reference',type=Path,default=repo.parent/'rennsandroidkitchen')
    args=parser.parse_args()
    tree=ast.parse((args.reference/'main_ext/class_FileTree.py').read_text(encoding='utf-8-sig'))
    owner=next(node for node in tree.body if isinstance(node,ast.ClassDef) and node.name=='FileTree')
    drop=next(node for node in owner.body if isinstance(node,ast.FunctionDef) and node.name=='dropEvent')
    worker=next(node for node in ast.walk(drop) if isinstance(node,ast.FunctionDef) and node.name=='_worker_same_move')
    from core.workspace_transfer import plan_transfer
    import main_ext.class_FilesystemBuilder as builder
    cases=[('pull_subfolder_out','system/etc','',False,True),
           ('filesystem_into_plain_folder','system','plain',False,True),
           ('filesystem_into_partial_metadata','system','plain',True,True),
           ('filesystem_into_existing_filesystem','system','vendor',False,True),
           ('subfolder_into_plain_folder','system/etc','plain',False,True),
           ('subfolder_without_source_metadata','system/etc','',False,False)]
    passed=[]
    with tempfile.TemporaryDirectory(prefix='rak-filesystem-drag-reference-') as temporary, \
         patch.object(builder,'DEBUG_FS_BUILDER',False), \
         patch('uuid.uuid4',return_value=uuid.UUID('00000000-0000-0000-0000-000000000001')):
        for name,source,directory,partial,metadata in cases:
            expected=Path(temporary)/name/'reference';actual=Path(temporary)/name/'quick'
            for base in (expected,actual):seed(base,partial=partial,metadata=metadata)
            identity=(actual/source).stat().st_ino
            reference_move(worker,expected,expected/source,expected/directory)
            plan_transfer(str(actual),[(str(actual),str(actual/source))],str(actual/directory),move=True,drag=True).execute({})
            wanted,got=snapshot(expected),snapshot(actual)
            for path in sorted(wanted.keys() | got.keys()):
                if wanted.get(path)!=got.get(path):
                    difference='\n'.join(difflib.unified_diff(wanted.get(path,'<missing>').splitlines(),got.get(path,'<missing>').splitlines(),fromfile='reference/'+path,tofile='quick/'+path))
                    raise AssertionError(name+'\n'+difference)
            assert (actual/directory/Path(source).name).stat().st_ino==identity
            passed.append(name)
    print(json.dumps({'result':'passed','referenceWorker':'FileTree.dropEvent._worker_same_move','cases':passed,'sameVolumeFolderIdentityPreserved':True}),flush=True)


if __name__=='__main__':main()
