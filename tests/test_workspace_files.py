# SPDX-License-Identifier: MPL-2.0
import errno
import os
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from core.workspace_files import WorkspaceFiles, validate_name


class WorkspaceFilesTests(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory(prefix='rak-workspace-files-')
        self.base=Path(self.temporary.name)
        (self.base/'system'/'etc').mkdir(parents=True)
        (self.base/'system'/'etc'/'fixture.txt').write_text('original',encoding='utf-8')
        self.cfg=self.base/'config';self.cfg.mkdir()
        (self.cfg/'system_fs_config').write_text('system 0 0 0755\nsystem/etc 0 0 0755\nsystem/etc/fixture.txt 1000 1001 0644 capabilities=0x0\n',encoding='utf-8')
        (self.cfg/'system_file_contexts').write_text('/system u:object_r:system_file:s0\n/system/etc u:object_r:system_file:s0\n/system/etc/fixture\\.txt u:object_r:system_file:s0\n',encoding='utf-8')

    def tearDown(self):self.temporary.cleanup()

    def test_source_create_rename_metadata_and_delete(self):
        files=WorkspaceFiles(str(self.base))
        target=files.create(str(self.base/'system'/'etc'),'new.txt')
        self.assertTrue(Path(target).is_file())
        original=self.base/'system'/'etc'/'fixture.txt'
        moved=files.rename(str(original),'changed.txt')
        self.assertEqual(Path(moved).read_text(),'original')
        fs=(self.cfg/'system_fs_config').read_text()
        self.assertIn('system/etc/changed.txt 1000 1001 0644 capabilities=0x0',fs)
        self.assertNotIn('system/etc/fixture.txt ',fs)
        files.edit_metadata(moved,2,'0750')
        self.assertIn('1000 1001 0750 capabilities=0x0',(self.cfg/'system_fs_config').read_text())
        files.delete(moved)
        self.assertFalse(Path(moved).exists())
        self.assertNotIn('changed.txt',(self.cfg/'system_fs_config').read_text())

    def test_copy_retains_source_metadata_and_remaps_destination(self):
        files=WorkspaceFiles(str(self.base))
        source=str(self.base/'system'/'etc'/'fixture.txt')
        target=files.transfer(files,source,str(self.base/'system'/'etc'),name='copied.txt')
        self.assertEqual(Path(target).read_text(),'original')
        fs=(self.cfg/'system_fs_config').read_text()
        self.assertIn('system/etc/fixture.txt 1000 1001 0644 capabilities=0x0',fs)
        self.assertIn('system/etc/copied.txt 1000 1001 0644 capabilities=0x0',fs)
        with self.assertRaises(FileExistsError):files.transfer(files,source,str(self.base/'system'/'etc'),name='copied.txt')

    def test_same_volume_move_preserves_file_identity_without_copying_payload(self):
        source=self.base/'system/etc/fixture.txt';identity=source.stat().st_ino
        files=WorkspaceFiles(str(self.base))
        with patch('main_ext.class_NeoBranchManager._copy2_with_retry',side_effect=AssertionError('move copied payload')):
            target=files.transfer(files,str(source),str(self.base/'system'),move=True)
        self.assertFalse(source.exists());self.assertEqual(Path(target).stat().st_ino,identity)
        self.assertEqual(Path(target).read_text(),'original')

    def test_same_volume_folder_move_preserves_directory_and_child_identity(self):
        source=self.base/'system/etc';child=source/'fixture.txt'
        identities=source.stat().st_ino,child.stat().st_ino
        destination=self.base/'system/target';destination.mkdir()
        files=WorkspaceFiles(str(self.base))
        with patch('core.workspace_files._copy_view',side_effect=AssertionError('move copied folder')):
            target=Path(files.transfer(files,str(source),str(destination),move=True))
        self.assertFalse(source.exists());self.assertEqual((target.stat().st_ino,(target/'fixture.txt').stat().st_ino),identities)
        self.assertIn('system/target/etc/fixture.txt 1000 1001 0644 capabilities=0x0',(self.cfg/'system_fs_config').read_text())

    def test_replaced_move_restores_both_file_identities_and_metadata_after_config_failure(self):
        source=self.base/'system/etc/fixture.txt';target=self.base/'system/fixture.txt';target.write_bytes(b'keep destination')
        identities=source.stat().st_ino,target.stat().st_ino
        metadata={path:path.read_bytes() for path in self.cfg.iterdir()}
        files=WorkspaceFiles(str(self.base));original=WorkspaceFiles._write_metadata;writes=[]
        def fail_second_write(path,contents):
            writes.append(path)
            if len(writes)==2:raise PermissionError('fixture metadata locked')
            return original(path,contents)
        with patch.object(WorkspaceFiles,'_write_metadata',side_effect=fail_second_write):
            with self.assertRaisesRegex(PermissionError,'metadata locked'):
                files.transfer(files,str(source),str(target.parent),move=True,replace=True)
        self.assertEqual((source.stat().st_ino,target.stat().st_ino),identities)
        self.assertEqual(source.read_text(),'original');self.assertEqual(target.read_bytes(),b'keep destination')
        self.assertEqual({path:path.read_bytes() for path in self.cfg.iterdir()},metadata)
        self.assertFalse(list(self.base.rglob('.rak-transfer-*')))

    def test_rename_permission_error_does_not_fall_back_to_copy(self):
        source=self.base/'system/etc/fixture.txt';target=self.base/'system/fixture.txt';target.write_text('keep')
        rename=os.rename
        def deny_source(old,new):
            if os.fspath(old)==str(source):raise PermissionError(errno.EACCES,'fixture locked')
            return rename(old,new)
        files=WorkspaceFiles(str(self.base))
        with patch('core.workspace_files.os.rename',side_effect=deny_source),patch('main_ext.class_NeoBranchManager._copy2_with_retry',side_effect=AssertionError('permission error triggered copy')):
            with self.assertRaises(PermissionError):files.transfer(files,str(source),str(target.parent),move=True,replace=True)
        self.assertEqual(source.read_text(),'original');self.assertEqual(target.read_text(),'keep')
        self.assertFalse(list(self.base.rglob('.rak-transfer-*')))

    def test_cross_device_move_copies_payload_then_removes_source(self):
        source=self.base/'system/etc/fixture.txt';rename=os.rename;files=WorkspaceFiles(str(self.base))
        def cross_device(old,new):
            if os.fspath(old)==str(source):raise OSError(errno.EXDEV,'fixture cross-device')
            return rename(old,new)
        with patch('core.workspace_files.os.rename',side_effect=cross_device):
            target=Path(files.transfer(files,str(source),str(self.base/'system'),move=True))
        self.assertFalse(source.exists());self.assertEqual(target.read_text(),'original')
        self.assertIn('system/fixture.txt 1000 1001 0644 capabilities=0x0',(self.cfg/'system_fs_config').read_text())
        self.assertNotIn('system/etc/fixture.txt ',(self.cfg/'system_fs_config').read_text())

    def test_cross_device_copy_failure_keeps_source_and_replaced_destination(self):
        source=self.base/'system/etc/fixture.txt';target=self.base/'system/fixture.txt';target.write_text('keep')
        before={path:path.read_bytes() for path in self.cfg.iterdir()};rename=os.rename
        def cross_device(old,new):
            if os.fspath(old)==str(source):raise OSError(errno.EXDEV,'fixture cross-device')
            return rename(old,new)
        def partial_copy(old,new):
            Path(new).write_bytes(b'partial')
            raise OSError('fixture disk full')
        files=WorkspaceFiles(str(self.base))
        with patch('core.workspace_files.os.rename',side_effect=cross_device),patch('main_ext.class_NeoBranchManager._copy2_with_retry',side_effect=partial_copy):
            with self.assertRaisesRegex(OSError,'disk full'):files.transfer(files,str(source),str(target.parent),move=True,replace=True)
        self.assertEqual(source.read_text(),'original');self.assertEqual(target.read_text(),'keep')
        self.assertEqual({path:path.read_bytes() for path in self.cfg.iterdir()},before)
        self.assertFalse(list(self.base.rglob('.rak-transfer-*')))

    def test_branch_edits_rename_delete_never_touch_source(self):
        from main_ext.class_NeoBranchManager import NeoBranchManager
        manager=NeoBranchManager(str(self.base))
        manager.create_branch('system','native','blue')
        manager.set_active_branch('system','native')
        source=self.base/'system'/'etc'/'fixture.txt'
        original_fs=(self.cfg/'system_fs_config').read_bytes()
        files=WorkspaceFiles(str(self.base))
        target=files.rename(str(source),'branch.txt')
        self.assertEqual(source.read_text(),'original')
        self.assertFalse((self.base/'system'/'etc'/'branch.txt').exists())
        branch_files=WorkspaceFiles(str(self.base))
        self.assertIsNone(branch_files.view.resolve(str(source))[0])
        resolved,origin=branch_files.view.resolve(target)
        self.assertEqual(origin,'branch')
        self.assertEqual(Path(resolved).read_text(),'original')
        branch_files.edit_metadata(target,2,'0600')
        self.assertEqual((self.cfg/'system_fs_config').read_bytes(),original_fs)
        branch_files.delete(target)
        self.assertEqual(source.read_text(),'original')
        self.assertIsNone(WorkspaceFiles(str(self.base)).view.resolve(target)[0])

    def test_names_and_paths_cannot_escape_or_mutate_root(self):
        files=WorkspaceFiles(str(self.base))
        for name in ('..','a/b','a\\b','CON','NUL.txt','x:stream',''):
            with self.subTest(name=name),self.assertRaises(ValueError):validate_name(name)
        with self.assertRaises(ValueError):files.delete(str(self.base))
        with self.assertRaises(ValueError):files.create(str(self.base.parent),'outside.txt')

    def test_partition_rename_keeps_metadata_identity_and_contents(self):
        (self.cfg/'system_fs_options').write_text('--mount-point=/system -L system\n')
        files=WorkspaceFiles(str(self.base))
        renamed=files.rename(str(self.base/'system'),'product')
        self.assertTrue(Path(renamed,'etc','fixture.txt').exists())
        self.assertFalse((self.cfg/'system_fs_config').exists())
        self.assertIn('product/etc/fixture.txt',(self.cfg/'product_fs_config').read_text())
        self.assertIn('/product/etc/fixture\\.txt',(self.cfg/'product_file_contexts').read_text())
        self.assertIn('--mount-point=/product',(self.cfg/'product_fs_options').read_text())

    def test_move_between_partitions_only_updates_the_existing_metadata_files(self):
        vendor=self.base/'vendor';vendor.mkdir()
        (self.cfg/'vendor_fs_config').write_text('vendor 0 0 0755\n')
        (self.cfg/'vendor_file_contexts').write_text('/vendor u:object_r:vendor_file:s0\n')
        before={path.name for path in self.cfg.iterdir()}
        files=WorkspaceFiles(str(self.base));source=self.base/'system/etc/fixture.txt'
        files.transfer(files,str(source),str(vendor),move=True)
        self.assertFalse(source.exists())
        self.assertIn('vendor/fixture.txt 1000 1001 0644 capabilities=0x0',(self.cfg/'vendor_fs_config').read_text())
        self.assertIn('/vendor/fixture\\.txt u:object_r:system_file:s0',(self.cfg/'vendor_file_contexts').read_text())
        self.assertNotIn('fixture',(self.cfg/'system_fs_config').read_text())
        self.assertEqual({path.name for path in self.cfg.iterdir()},before)

    def test_import_folder_adds_metadata_for_every_descendant_without_new_config_files(self):
        with tempfile.TemporaryDirectory() as external:
            source=Path(external)/'incoming';(source/'child').mkdir(parents=True)
            (source/'child/data.bin').write_bytes(b'contents')
            before={path.name for path in self.cfg.iterdir()}
            WorkspaceFiles(str(self.base)).transfer(WorkspaceFiles(external),str(source),str(self.base/'system/etc'))
        fs=(self.cfg/'system_fs_config').read_text()
        self.assertIn('system/etc/incoming/child/data.bin 0 0 0644',fs)
        self.assertIn('/system/etc/incoming/child/data\\.bin ',(self.cfg/'system_file_contexts').read_text())
        self.assertEqual({path.name for path in self.cfg.iterdir()},before)

    def test_active_branch_with_missing_metadata_never_edits_source_config(self):
        from main_ext.class_NeoBranchManager import NeoBranchManager
        manager=NeoBranchManager(str(self.base));manager.create_branch('system','missing','blue');manager.set_active_branch('system','missing')
        branch_config=Path(manager._branch_config_dir('system','missing'))
        (branch_config/'system_fs_config').unlink()
        original=(self.cfg/'system_fs_config').read_bytes()
        WorkspaceFiles(str(self.base)).edit_metadata(str(self.base/'system/etc/fixture.txt'),2,'0600')
        self.assertEqual((self.cfg/'system_fs_config').read_bytes(),original)
        self.assertIn('1000 1001 0600 capabilities=0x0',(branch_config/'system_fs_config').read_text())

    def test_duplicating_filesystem_folder_does_not_invent_a_new_filesystem(self):
        before={path.name:path.read_bytes() for path in self.cfg.iterdir()}
        copy=WorkspaceFiles(str(self.base)).duplicate(str(self.base/'system'))
        self.assertTrue(Path(copy,'etc/fixture.txt').exists())
        self.assertEqual({path.name:path.read_bytes() for path in self.cfg.iterdir()},before)

    def test_bad_metadata_aborts_before_replacing_file_bytes(self):
        source=self.base/'system/etc/fixture.txt';target=self.base/'system/etc/existing.txt'
        target.write_bytes(b'original destination')
        (self.cfg/'system_file_contexts').write_bytes(b'\xffnot utf8')
        files=WorkspaceFiles(str(self.base))
        with self.assertRaises(UnicodeDecodeError):files.transfer(files,str(source),str(target.parent),name=target.name,move=True,replace=True)
        self.assertEqual(target.read_bytes(),b'original destination');self.assertEqual(source.read_text(),'original')

    def test_move_into_apk_mirror_never_creates_mirror_filesystem_metadata(self):
        source=self.base/'system/etc/fixture.txt'
        mirror=self.base/'apktool_dec/app.apk';mirror.mkdir(parents=True)
        before={path.name for path in self.cfg.iterdir()}
        files=WorkspaceFiles(str(self.base));files.transfer(files,str(source),str(mirror),move=True)
        self.assertEqual((mirror/'fixture.txt').read_text(),'original');self.assertFalse(source.exists())
        self.assertEqual({path.name for path in self.cfg.iterdir()},before)
        self.assertNotIn('fixture',(self.cfg/'system_fs_config').read_text())

    def test_branch_move_removes_old_metadata_only_from_the_branch(self):
        from main_ext.class_NeoBranchManager import NeoBranchManager
        manager=NeoBranchManager(str(self.base));manager.create_branch('system','move','blue');manager.set_active_branch('system','move')
        original={path.name:path.read_bytes() for path in self.cfg.iterdir()}
        files=WorkspaceFiles(str(self.base));source=str(self.base/'system/etc/fixture.txt')
        destination=files.transfer(files,source,str(self.base/'system'),move=True)
        branch_config=Path(manager._branch_config_dir('system','move'))
        fs=(branch_config/'system_fs_config').read_text();ctx=(branch_config/'system_file_contexts').read_text()
        self.assertNotIn('system/etc/fixture.txt ',fs);self.assertIn('system/fixture.txt 1000 1001 0644 capabilities=0x0',fs)
        self.assertNotIn('/system/etc/fixture\\.txt ',ctx);self.assertIn('/system/fixture\\.txt ',ctx)
        self.assertIsNone(files.view.resolve(source)[0]);self.assertIsNotNone(files.view.resolve(destination)[0])
        self.assertEqual({path.name:path.read_bytes() for path in self.cfg.iterdir()},original)

    def test_branch_overlay_file_moves_directly_and_keeps_source_immutable(self):
        from main_ext.class_NeoBranchManager import NeoBranchManager
        manager=NeoBranchManager(str(self.base));manager.create_branch('system','overlay','blue');manager.set_active_branch('system','overlay')
        source=self.base/'system/etc/fixture.txt';overlay=Path(manager.record_edit('system','overlay',str(source)))
        overlay.write_text('branch version');identity=overlay.stat().st_ino
        files=WorkspaceFiles(str(self.base))
        with patch('main_ext.class_NeoBranchManager._copy2_with_retry',side_effect=AssertionError('overlay move copied payload')):
            target=files.transfer(files,str(source),str(self.base/'system'),move=True)
        resolved,origin=files.view.resolve(target)
        self.assertEqual(origin,'branch');self.assertEqual(Path(resolved).stat().st_ino,identity)
        self.assertEqual(Path(resolved).read_text(),'branch version');self.assertEqual(source.read_text(),'original')
        self.assertFalse(overlay.exists());self.assertIsNone(files.view.resolve(str(source))[0])

    def test_branch_merged_folder_move_materializes_both_layers_without_moving_source(self):
        from main_ext.class_NeoBranchManager import NeoBranchManager
        manager=NeoBranchManager(str(self.base));manager.create_branch('system','merged','blue');manager.set_active_branch('system','merged')
        source=self.base/'system/etc';overlay=Path(manager._branch_dir('system','merged'))/'system/etc'
        overlay.mkdir(parents=True);(overlay/'added.txt').write_text('branch only')
        original=(source/'fixture.txt').stat().st_ino
        files=WorkspaceFiles(str(self.base));target=files.transfer(files,str(source),str(self.base/'system'),name='moved',move=True)
        resolved,_=files.view.resolve(target)
        self.assertEqual(Path(resolved,'fixture.txt').read_text(),'original');self.assertEqual(Path(resolved,'added.txt').read_text(),'branch only')
        self.assertEqual((source/'fixture.txt').stat().st_ino,original);self.assertFalse(overlay.exists())
        self.assertIsNone(files.view.resolve(str(source))[0])

    def test_new_branch_folder_moves_directly_without_copying_its_children(self):
        from main_ext.class_NeoBranchManager import NeoBranchManager
        manager=NeoBranchManager(str(self.base));manager.create_branch('system','newfolder','blue');manager.set_active_branch('system','newfolder')
        files=WorkspaceFiles(str(self.base));source=files.create(str(self.base/'system'),'created',folder=True)
        leaf=files.create(source,'data.bin');physical,_=files.view.resolve(leaf);Path(physical).write_bytes(b'branch data')
        folder,_=files.view.resolve(source);identities=Path(folder).stat().st_ino,Path(physical).stat().st_ino
        with patch('core.workspace_files._copy_view',side_effect=AssertionError('overlay directory was copied')):
            target=files.transfer(files,source,str(self.base/'system/etc'),move=True)
        resolved,_=files.view.resolve(target)
        self.assertEqual((Path(resolved).stat().st_ino,Path(resolved,'data.bin').stat().st_ino),identities)
        self.assertEqual(Path(resolved,'data.bin').read_bytes(),b'branch data')
        self.assertIsNone(files.view.resolve(source)[0]);self.assertFalse((self.base/'system/created').exists())

    def test_branch_move_failure_restores_overlay_tracking_and_both_metadata_files(self):
        from main_ext.class_NeoBranchManager import NeoBranchManager
        manager=NeoBranchManager(str(self.base));manager.create_branch('system','rollback','blue');manager.set_active_branch('system','rollback')
        source=self.base/'system/etc/fixture.txt';overlay=Path(manager.record_edit('system','rollback',str(source)))
        overlay.write_text('branch data');identity=overlay.stat().st_ino
        cfg=Path(manager._branch_config_dir('system','rollback'));branch_json=Path(manager._branch_json_path('system','rollback'))
        before={path:path.read_bytes() for path in [*cfg.iterdir(),branch_json,*self.cfg.iterdir()]}
        original=WorkspaceFiles._write_metadata;writes=[]
        def fail_source_cleanup(path,contents):
            writes.append(path)
            if len(writes)==3:raise PermissionError('fixture source metadata locked')
            return original(path,contents)
        files=WorkspaceFiles(str(self.base))
        with patch.object(WorkspaceFiles,'_write_metadata',side_effect=fail_source_cleanup):
            with self.assertRaisesRegex(PermissionError,'source metadata locked'):
                files.transfer(files,str(source),str(self.base/'system'),move=True)
        self.assertEqual(overlay.stat().st_ino,identity);self.assertEqual(overlay.read_text(),'branch data')
        self.assertEqual({path:path.read_bytes() for path in before},before)
        self.assertEqual(files.view.resolve(str(source))[0],str(overlay))
        self.assertIsNone(files.view.resolve(str(self.base/'system/fixture.txt'))[0])

    def test_stale_branch_plan_is_rejected_before_copying_bytes(self):
        from main_ext.class_NeoBranchManager import NeoBranchManager
        source=str(self.base/'system/etc/fixture.txt');files=WorkspaceFiles(str(self.base));files.branch(source)
        manager=NeoBranchManager(str(self.base));manager.create_branch('system','changed','blue');manager.set_active_branch('system','changed')
        with self.assertRaisesRegex(RuntimeError,'branch changed'):
            files.transfer(files,source,str(self.base/'system'),move=True)
        self.assertTrue(Path(source).exists());self.assertFalse((self.base/'system/fixture.txt').exists())

    def test_native_preparation_restores_interrupted_overlay_before_loading(self):
        from core.workspace_preparation import prepare_sources
        from main_ext.class_NeoBranchManager import NeoBranchManager
        manager=NeoBranchManager(str(self.base))
        manager.create_branch('system','recovery','green')
        manager.set_active_branch('system','recovery')
        source=self.base/'system'/'etc'/'fixture.txt'
        overlay=manager.record_edit('system','recovery',str(source))
        Path(overlay).write_text('branch overlay',encoding='utf-8')
        manager.prepare_for_repack('system','recovery')
        self.assertEqual(source.read_text(),'branch overlay')
        ok,restored=prepare_sources([str(self.base),str(self.base)])
        self.assertTrue(ok);self.assertEqual(restored,{str(self.base):['system']})
        self.assertTrue(manager.is_physical_checkout('system'))
        self.assertEqual(source.read_text(),'branch overlay')
        manager.checkout_branch('system',None)
        self.assertEqual(source.read_text(),'original')
        self.assertEqual(Path(overlay).read_text(),'branch overlay')
        self.assertFalse(Path(manager._source_dir('system'),'repack_manifest.json').exists())


if __name__=='__main__':unittest.main()
