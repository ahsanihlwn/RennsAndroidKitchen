# SPDX-License-Identifier: MPL-2.0
from pathlib import Path
import tempfile
import unittest
import zipfile
from unittest.mock import patch
from core.workspace_transfer import plan_transfer
from core.archive_document import ArchiveReader


class TransferTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.source=self.root/'incoming';self.target=self.root/'project';self.source.mkdir();self.target.mkdir()
    def tearDown(self):self.tmp.cleanup()
    def plan(self,name,move=False):return plan_transfer(str(self.target),[(str(self.source),str(self.source/name))],str(self.target),move=move)
    def test_preflight_writes_nothing_and_skip_retains_both_files(self):
        (self.source/'same.txt').write_text('incoming');(self.target/'same.txt').write_text('existing')
        plan=self.plan('same.txt');self.assertEqual(len(plan.conflicts),1)
        self.assertFalse((self.source/'config').exists())
        plan.execute({str(self.target/'same.txt'):'skip'})
        self.assertEqual((self.target/'same.txt').read_text(),'existing')
        self.assertTrue((self.source/'same.txt').exists())
    def test_replace_import_does_not_create_metadata_in_source(self):
        (self.source/'same.txt').write_text('incoming');(self.target/'same.txt').write_text('existing')
        self.plan('same.txt').execute({str(self.target/'same.txt'):'replace'})
        self.assertEqual((self.target/'same.txt').read_text(),'incoming')
        self.assertFalse((self.source/'config').exists())
    def test_cut_folder_merge_keeps_skipped_children_and_existing_siblings(self):
        src=self.source/'folder';dst=self.target/'folder';src.mkdir();dst.mkdir()
        (src/'skip.txt').write_text('incoming');(src/'move.txt').write_text('moved')
        (dst/'skip.txt').write_text('keep');(dst/'old.txt').write_text('unrelated')
        plan=self.plan('folder',True)
        plan.execute({str(dst/'skip.txt'):'skip'})
        self.assertEqual((dst/'skip.txt').read_text(),'keep')
        self.assertEqual((dst/'move.txt').read_text(),'moved')
        self.assertEqual((dst/'old.txt').read_text(),'unrelated')
        self.assertTrue((src/'skip.txt').exists());self.assertFalse((src/'move.txt').exists())
    def test_archive_folder_with_one_member_keeps_folder_and_exact_bytes(self):
        archive=self.root/'fixture.zip'
        with zipfile.ZipFile(archive,'w') as target:target.writestr('folder/only.txt',b'exact\0bytes')
        with ArchiveReader(str(archive)) as reader:
            paths=reader.extract_selection_to(['folder'],str(self.source))
        self.assertEqual(paths,[str(self.source/'folder')])
        self.assertEqual((self.source/'folder'/'only.txt').read_bytes(),b'exact\0bytes')
    def test_missing_decision_does_not_replace(self):
        (self.source/'same.txt').write_text('incoming');(self.target/'same.txt').write_text('existing')
        with self.assertRaises(ValueError):self.plan('same.txt').execute({})
        self.assertEqual((self.target/'same.txt').read_text(),'existing')

    def test_move_onto_itself_or_inside_its_own_folder_is_a_no_op(self):
        source=self.source/'folder';source.mkdir();child=source/'child';child.mkdir()
        (source/'keep.txt').write_text('keep');identity=source.stat().st_ino
        for directory in (self.source,source,child):
            plan=plan_transfer(str(self.source),[(str(self.source),str(source))],str(directory),move=True)
            self.assertFalse(plan.items);self.assertFalse(plan.conflicts)
            self.assertEqual(plan.execute({}),{'completed':[],'skipped':[]})
            self.assertEqual(source.stat().st_ino,identity);self.assertEqual((source/'keep.txt').read_text(),'keep')
        self.assertFalse((self.source/'config').exists())

    def test_batch_self_drop_skips_recipient_folder_and_moves_other_selected_items(self):
        folder=self.source/'folder';folder.mkdir();leaf=self.source/'leaf.txt';leaf.write_text('move me')
        identity=leaf.stat().st_ino
        plan=plan_transfer(str(self.source),[(str(self.source),str(folder)),(str(self.source),str(leaf))],str(folder),move=True)
        result=plan.execute({})
        self.assertEqual(result['completed'],[str(folder/'leaf.txt')])
        self.assertEqual((folder/'leaf.txt').stat().st_ino,identity);self.assertFalse(leaf.exists())

    def test_copy_in_place_uses_source_numbered_names_without_conflict(self):
        (self.target/'file.txt').write_text('original');(self.target/'file(2).txt').write_text('keep')
        plan=plan_transfer(str(self.target),[(str(self.target),str(self.target/'file.txt'))],str(self.target),duplicate_in_place=True)
        self.assertFalse(plan.conflicts);plan.execute({})
        self.assertEqual((self.target/'file(3).txt').read_text(),'original')
        self.assertEqual((self.target/'file(2).txt').read_text(),'keep')
    def test_copy_numbered_folder_in_place_keeps_source_and_uses_next_suffix(self):
        source=self.target/'folder(2)';source.mkdir();(source/'leaf.txt').write_text('folder')
        plan=plan_transfer(str(self.target),[(str(self.target),str(source))],str(self.target),duplicate_in_place=True)
        plan.execute({});self.assertEqual((self.target/'folder(3)'/'leaf.txt').read_text(),'folder')
        self.assertTrue((source/'leaf.txt').exists())

    def test_drag_cannot_move_project_config(self):
        config=self.source/'config';config.mkdir(exist_ok=True)
        with self.assertRaisesRegex(ValueError,'config folder'):
            plan_transfer(str(self.target),[(str(self.source),str(config))],str(self.target),move=True)
        self.assertTrue(config.is_dir())

    def test_cross_rom_apk_move_keeps_decompiled_mirror_paired(self):
        from core import apktool_tool
        apk=self.source/'fixture.apk';apk.write_bytes(b'APK fixture')
        mirror=Path(apktool_tool.mirror_dir_for(str(self.source),str(apk)));mirror.mkdir(parents=True)
        (mirror/apktool_tool.APKTOOL_MARKER).write_text('{}');(mirror/'contents.txt').write_text('decompiled')
        identities=apk.stat().st_ino,mirror.stat().st_ino,(mirror/'contents.txt').stat().st_ino
        plan_transfer(str(self.target),[(str(self.source),str(apk))],str(self.target),move=True).execute({})
        target=self.target/'fixture.apk';new_mirror=Path(apktool_tool.mirror_dir_for(str(self.target),str(target)))
        self.assertEqual(target.read_bytes(),b'APK fixture');self.assertEqual((new_mirror/'contents.txt').read_text(),'decompiled')
        self.assertFalse(apk.exists());self.assertFalse(mirror.exists())
        self.assertEqual((target.stat().st_ino,new_mirror.stat().st_ino,(new_mirror/'contents.txt').stat().st_ino),identities)

    def test_whole_filesystem_move_retains_metadata_and_identity(self):
        partition=self.source/'system';(partition/'etc').mkdir(parents=True);(partition/'etc/a.prop').write_text('data')
        cfg=self.source/'config';cfg.mkdir()
        entries={'system_fs_config':'system 0 0 0755\nsystem/etc/a.prop 1000 1001 0640 capabilities=0x40\n',
                 'system_file_contexts':'# authoritative rules\n/system(/.*)? u:object_r:system_file:s0\n/system/etc/a\\.prop u:object_r:custom_file:s0\n',
                 'system_fs_options':'--mount-point=/system -L system\n'}
        for name,contents in entries.items():(cfg/name).write_text(contents)
        identities=partition.stat().st_ino,(partition/'etc/a.prop').stat().st_ino
        plan_transfer(str(self.target),[(str(self.source),str(partition))],str(self.target),move=True).execute({})
        self.assertEqual((self.target/'system/etc/a.prop').read_text(),'data')
        self.assertEqual(((self.target/'system').stat().st_ino,(self.target/'system/etc/a.prop').stat().st_ino),identities)
        for name,contents in entries.items():self.assertEqual((self.target/'config'/name).read_text(),contents)

    def test_whole_filesystem_merge_retains_skipped_source_metadata(self):
        for base in (self.source,self.target):
            (base/'system').mkdir();(base/'config').mkdir()
        (self.source/'system/move.txt').write_text('move');(self.source/'system/skip.txt').write_text('incoming')
        (self.target/'system/skip.txt').write_text('existing')
        (self.source/'config/system_fs_config').write_text('system 0 0 0755\nsystem/move.txt 1000 1001 0600 capabilities=0x40\nsystem/skip.txt 1002 1003 0640\n')
        (self.source/'config/system_file_contexts').write_text('/system u:object_r:system_file:s0\n/system/move\\.txt u:object_r:custom_file:s0\n/system/skip\\.txt u:object_r:skip_file:s0\n')
        plan_transfer(str(self.target),[(str(self.source),str(self.source/'system'))],str(self.target),move=True).execute({str(self.target/'system/skip.txt'):'skip'})
        self.assertEqual((self.target/'system/skip.txt').read_text(),'existing')
        self.assertEqual((self.source/'system/skip.txt').read_text(),'incoming')
        self.assertIn('system/move.txt 1000 1001 0600 capabilities=0x40',(self.target/'config/system_fs_config').read_text())
        self.assertIn('system/skip.txt 1002 1003 0640',(self.source/'config/system_fs_config').read_text())
        self.assertNotIn('move.txt',(self.source/'config/system_fs_config').read_text())

    def filesystem(self):
        folder=self.source/'system';(folder/'etc').mkdir(parents=True)
        (folder/'etc/a.prop').write_text('filesystem payload')
        cfg=self.source/'config';cfg.mkdir()
        (cfg/'system_file_contexts').write_text('/system u:object_r:system_file:s0\n/system/etc u:object_r:custom_file:s0\n/system/etc/a\\.prop u:object_r:custom_file:s0\n')
        (cfg/'system_fs_config').write_text('system 0 0 0755\nsystem/etc 1000 1001 0750\nsystem/etc/a.prop 1000 1001 0640 capabilities=0x40\n')
        (cfg/'system_fs_options').write_text('mkfs.erofs options: --mount-point=/system\n')
        return folder,cfg

    def test_drag_subfolder_out_builds_its_filesystem_with_source_permissions(self):
        folder,cfg=self.filesystem();original={p.name:p.read_bytes() for p in cfg.iterdir()}
        source=folder/'etc';identity=source.stat().st_ino
        plan_transfer(str(self.source),[(str(self.source),str(source))],str(self.source),move=True,drag=True).execute({})
        self.assertEqual((self.source/'etc').stat().st_ino,identity)
        self.assertIn('etc/a.prop 1000 1001 0640 capabilities=0x40',(cfg/'etc_fs_config').read_text())
        self.assertIn('/etc/a\\.prop u:object_r:custom_file:s0',(cfg/'etc_file_contexts').read_text())
        self.assertIn('--mount-point=/etc',(cfg/'etc_fs_options').read_text())
        self.assertEqual({name:(cfg/name).read_bytes() for name in original},original)

    def test_drag_filesystem_into_plain_destination_keeps_source_metadata(self):
        folder,cfg=self.filesystem();original={p.name:p.read_bytes() for p in cfg.iterdir()}
        plain=self.target/'plain';plain.mkdir();identity=folder.stat().st_ino
        plan_transfer(str(self.target),[(str(self.source),str(folder))],str(plain),move=True,drag=True).execute({})
        self.assertEqual((plain/'system').stat().st_ino,identity)
        self.assertEqual({p.name:p.read_bytes() for p in cfg.iterdir()},original)
        self.assertFalse(list((self.target/'config').glob('*')))

    def test_drag_folder_merge_into_plain_destination_keeps_source_metadata(self):
        folder,cfg=self.filesystem();original={p.name:p.read_bytes() for p in cfg.iterdir()}
        plain=self.source/'plain';(plain/'system/etc').mkdir(parents=True)
        (plain/'system/etc/keep.txt').write_text('existing sibling')
        plan_transfer(str(self.source),[(str(self.source),str(folder))],str(plain),move=True,drag=True).execute({})
        self.assertFalse(folder.exists())
        self.assertEqual((plain/'system/etc/a.prop').read_text(),'filesystem payload')
        self.assertEqual((plain/'system/etc/keep.txt').read_text(),'existing sibling')
        self.assertEqual({p.name:p.read_bytes() for p in cfg.iterdir()},original)

    def test_drag_builder_failure_restores_payload_and_all_metadata(self):
        folder,cfg=self.filesystem();original={p.name:p.read_bytes() for p in cfg.iterdir()}
        source=folder/'etc';identity=source.stat().st_ino
        from main_ext.class_FilesystemBuilder import FilesystemBuilder
        def fail(builder):
            (cfg/'etc_file_contexts').write_text('partial generated metadata')
            (cfg/'etc_fs_options').write_text('partial identity')
            raise OSError('fixture builder failed')
        with patch.object(FilesystemBuilder,'create_filesystem',fail):
            with self.assertRaisesRegex(OSError,'builder failed'):
                plan_transfer(str(self.source),[(str(self.source),str(source))],str(self.source),move=True,drag=True).execute({})
        self.assertEqual(source.stat().st_ino,identity)
        self.assertEqual((source/'a.prop').read_text(),'filesystem payload')
        self.assertFalse((self.source/'etc').exists())
        self.assertEqual({p.name:p.read_bytes() for p in cfg.iterdir()},original)

    def test_drag_cannot_reparent_a_filesystem_root_with_an_active_branch(self):
        folder,cfg=self.filesystem();plain=self.source/'plain';plain.mkdir()
        from main_ext.class_NeoBranchManager import NeoBranchManager
        manager=NeoBranchManager(str(self.source));manager.create_branch('system','active','blue')
        manager.set_active_branch('system','active')
        with self.assertRaisesRegex(ValueError,'active NeoBranch'):
            plan_transfer(str(self.source),[(str(self.source),str(folder))],str(plain),move=True,drag=True)
        self.assertTrue((folder/'etc/a.prop').is_file());self.assertFalse((plain/'system').exists())


if __name__=='__main__':unittest.main()
