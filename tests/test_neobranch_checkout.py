# SPDX-License-Identifier: MPL-2.0
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from main_ext.class_NeoBranchManager import NeoBranchManager, SourceRestoreError
from core.workspace_preparation import prepare_sources


class PhysicalCheckoutTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='rak-physical-checkout-')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.live = self.base / 'vendor'
        self.live.mkdir()
        (self.live / 'build.prop').write_bytes(b'ro.test=source\r\n')
        (self.live / 'removed').write_text('keep Source')
        (self.base / 'config').mkdir()
        self.config = self.base / 'config' / 'vendor_file_contexts'
        self.config.write_text('/vendor(/.*)? u:object_r:vendor_file:s0\n')
        (self.base / 'config' / 'vendor_fs_config').write_text('vendor 0 0 0755\n')
        (self.base / 'config' / 'vendor_dlkm_file_contexts').write_text('other partition')
        self.manager = NeoBranchManager(str(self.base))

    def test_raw_host_edits_metadata_add_delete_rename_and_restart(self):
        self.manager.create_checkout_branch('vendor', 'port', 'blue')
        # A separate native process has no RAK imports or special file adapter.
        script = "from pathlib import Path; p=Path('.'); (p/'vendor/build.prop').write_bytes(b'ro.test=port\\r\\n'); (p/'vendor/added').mkdir(); (p/'vendor/added/raw').write_text('external'); (p/'vendor/removed').unlink(); (p/'vendor/build.prop').rename(p/'vendor/renamed.prop'); (p/'config/vendor_file_contexts').write_text('port contexts'); (p/'config/vendor_info').write_text('new metadata')"
        subprocess.run([sys.executable, '-c', script], cwd=self.base, check=True)
        prepare_sources([str(self.base)])
        self.manager.checkout_branch('vendor', None)
        self.assertEqual((self.live / 'build.prop').read_bytes(), b'ro.test=source\r\n')
        self.assertTrue((self.live / 'removed').is_file())
        self.assertFalse((self.live / 'added').exists())
        self.assertFalse((self.base / 'config/vendor_info').exists())
        self.manager.checkout_branch('vendor', 'port')
        self.assertEqual((self.live / 'renamed.prop').read_bytes(), b'ro.test=port\r\n')
        self.assertEqual((self.live / 'added/raw').read_text(), 'external')
        self.assertFalse((self.live / 'removed').exists())
        self.assertEqual(self.config.read_text(), 'port contexts')
        self.assertEqual((self.base / 'config/vendor_info').read_text(), 'new metadata')
        self.assertEqual((self.base / 'config/vendor_dlkm_file_contexts').read_text(), 'other partition')

    def test_new_branch_starts_from_source_and_source_is_editable(self):
        self.manager.create_checkout_branch('vendor', 'one', 'blue')
        (self.live / 'build.prop').write_text('one')
        self.config.write_text('one')
        self.manager.create_checkout_branch('vendor', 'two', 'green')
        self.assertEqual((self.live / 'build.prop').read_bytes(), b'ro.test=source\r\n')
        self.assertNotEqual(self.config.read_text(), 'one')
        self.manager.checkout_branch('vendor', None)
        (self.live / 'build.prop').write_text('source v2')
        self.manager.checkout_branch('vendor', 'one')
        self.assertEqual((self.live / 'build.prop').read_text(), 'one')
        self.manager.checkout_branch('vendor', None)
        self.assertEqual((self.live / 'build.prop').read_text(), 'source v2')

    def test_legacy_overlay_and_tombstones_migrate_without_changing_source(self):
        self.manager.create_branch('vendor', 'old', 'blue')
        self.manager.set_active_branch('vendor', 'old')
        Path(self.manager.record_edit('vendor', 'old', str(self.live / 'build.prop'))).write_text('legacy branch')
        self.manager.record_delete('vendor', 'old', 'vendor/removed')
        prepare_sources([str(self.base)])
        self.assertTrue(self.manager.is_physical_checkout('vendor'))
        self.assertEqual((self.live / 'build.prop').read_text(), 'legacy branch')
        self.assertFalse((self.live / 'removed').exists())
        self.manager.checkout_branch('vendor', None)
        self.assertEqual((self.live / 'build.prop').read_bytes(), b'ro.test=source\r\n')
        self.assertTrue((self.live / 'removed').exists())

    def test_inactive_legacy_branch_migrates_its_tombstones_lazily(self):
        self.manager.create_branch('vendor','first','blue')
        self.manager.create_branch('vendor','later','green')
        self.manager.record_delete('vendor','later','vendor/removed')
        self.manager.set_active_branch('vendor','first')
        prepare_sources([str(self.base)])
        self.manager.checkout_branch('vendor','later')
        self.assertFalse((self.live/'removed').exists())
        self.manager.checkout_branch('vendor',None)
        self.assertEqual((self.live/'removed').read_text(),'keep Source')

    def test_locked_install_recovers_without_copying_staging_again(self):
        self.manager.create_checkout_branch('vendor', 'port', 'blue')
        (self.live / 'build.prop').write_text('port')
        original = os.replace
        def replace(src, dst):
            if str(src).endswith('next-0'):
                raise PermissionError('injected locked checkout')
            return original(src, dst)
        import main_ext.class_NeoBranchManager as manager_module
        with patch('os.replace', side_effect=replace), patch.object(manager_module.time, 'sleep'):
            with self.assertRaises(SourceRestoreError):self.manager.checkout_branch('vendor', None)
        journal = self.base / 'neobranch/vendor/checkout/journal.json'
        self.assertTrue(journal.exists())
        import core.neobranch_checkout as checkout
        with patch.object(checkout.shutil, 'copytree', side_effect=AssertionError('must reuse staging')):
            prepare_sources([str(self.base)])
        self.assertFalse(journal.exists())
        self.assertEqual((self.live / 'build.prop').read_bytes(), b'ro.test=source\r\n')
        self.manager.checkout_branch('vendor', 'port')
        self.assertEqual((self.live / 'build.prop').read_text(), 'port')

    def test_recovery_handles_crash_between_rename_and_journal_write(self):
        self.manager.create_checkout_branch('vendor', 'port', 'blue')
        (self.live / 'build.prop').write_text('port')
        original = os.replace
        def replace(src, dst):
            original(src, dst)
            if str(src).endswith('next-0'):raise KeyboardInterrupt('simulated process exit')
        with patch('os.replace', side_effect=replace):
            with self.assertRaises(KeyboardInterrupt):self.manager.checkout_branch('vendor', None)
        prepare_sources([str(self.base)])
        self.assertEqual((self.live / 'build.prop').read_bytes(), b'ro.test=source\r\n')
        self.manager.checkout_branch('vendor', 'port')
        self.assertEqual((self.live / 'build.prop').read_text(), 'port')

    def test_external_writer_during_capture_preserves_live_tree(self):
        self.manager.create_checkout_branch('vendor', 'port', 'blue')
        (self.live / 'build.prop').write_text('port')
        import core.neobranch_checkout as checkout
        copy = checkout._copy
        def racing_copy(src, dst):
            copy(src, dst)
            if src == self.live/'build.prop':src.write_text('concurrent write')
        with patch.object(checkout, '_copy', side_effect=racing_copy):
            with self.assertRaisesRegex(RuntimeError, 'changed during'):self.manager.checkout_branch('vendor', None)
        self.assertEqual((self.live / 'build.prop').read_text(), 'concurrent write')
        self.assertEqual(self.manager.get_active_branch('vendor')['name'], 'port')
        self.manager.checkout_branch('vendor', None)
        self.manager.checkout_branch('vendor', 'port')
        self.assertEqual((self.live / 'build.prop').read_text(), 'concurrent write')

    def test_repack_uses_live_branch_and_keeps_it_active_after_failure(self):
        self.manager.create_checkout_branch('vendor', 'port', 'blue')
        (self.live / 'build.prop').write_text('port')
        from ui.workspace_tools import repack
        def build(path, kind):
            self.assertEqual((Path(path) / 'build.prop').read_text(), 'port')
            self.config.write_text('packer metadata')
            raise OSError('packer failed')
        with patch('ui.workspace_tools._repack', side_effect=build):
            with self.assertRaises(OSError):repack(str(self.live), 'repack_ext')
        self.assertEqual(self.manager.get_active_branch('vendor')['name'], 'port')
        self.assertEqual((self.live / 'build.prop').read_text(), 'port')
        self.manager.checkout_branch('vendor', None)
        self.manager.checkout_branch('vendor', 'port')
        self.assertEqual(self.config.read_text(), 'packer metadata')

    def test_managed_edits_operate_on_same_physical_files(self):
        self.manager.create_checkout_branch('vendor', 'port', 'blue')
        from core.workspace_files import WorkspaceFiles
        files = WorkspaceFiles(str(self.base))
        self.assertEqual(Path(files.write_path(str(self.live / 'build.prop'))), self.live / 'build.prop')
        self.assertEqual(Path(files.engine(str(self.live)).cfgdir), self.base / 'config')
        files.create(str(self.live), 'new')
        self.assertTrue((self.live / 'new').is_file())
        files.rename(str(self.live / 'new'), 'renamed')
        self.assertFalse((self.live / 'new').exists())
        self.assertTrue((self.live / 'renamed').exists())
        files.delete(str(self.live / 'renamed'))
        self.assertFalse((self.live / 'renamed').exists())

    def test_crash_matrix_recovers_partition_and_each_present_metadata_piece(self):
        self.manager.create_checkout_branch('vendor','port','blue')
        original=os.replace
        for piece in ('previous-0','next-0','next-1','next-2'):
            with self.subTest(piece=piece):
                (self.live/'build.prop').write_text('port '+piece)
                self.config.write_text('port contexts '+piece)
                def replace(src,dst):
                    original(src,dst)
                    if (str(dst).endswith(piece) if piece.startswith('previous') else str(src).endswith(piece)):
                        raise KeyboardInterrupt('crash after '+piece)
                with patch('os.replace',side_effect=replace):
                    with self.assertRaises(KeyboardInterrupt):self.manager.checkout_branch('vendor',None)
                prepare_sources([str(self.base)])
                self.assertIsNone(self.manager.get_active_branch('vendor'))
                self.assertEqual((self.live/'build.prop').read_bytes(),b'ro.test=source\r\n')
                self.assertIn('vendor_file',self.config.read_text())
                self.manager.checkout_branch('vendor','port')
                self.assertEqual((self.live/'build.prop').read_text(),'port '+piece)
                self.assertEqual(self.config.read_text(),'port contexts '+piece)

    def test_checkpoint_reuses_only_immutable_files_and_bounds_generations(self):
        self.manager.create_checkout_branch('vendor','port','blue')
        (self.live/'build.prop').write_text('port one')
        first=self.manager.checkpoint_checkout('vendor')
        (self.live/'build.prop').write_text('port two')
        second=self.manager.checkpoint_checkout('vendor')
        from core.neobranch_checkout import _owned,_snapshot
        _,store=_owned(self.manager,'vendor')
        before=_snapshot(store,first)/'vendor/removed'
        after=_snapshot(store,second)/'vendor/removed'
        self.assertEqual(before.read_bytes(),after.read_bytes())
        self.assertFalse(os.path.samefile(after,self.live/'removed'))
        (self.live/'removed').write_text('changed externally')
        self.assertEqual(after.read_text(),'keep Source')
        self.manager.checkout_branch('vendor',None)
        generations=list((store/'snapshots').iterdir())
        self.assertEqual(len(generations),2)
        self.manager.checkout_branch('vendor','port')
        self.assertEqual((self.live/'removed').read_text(),'changed externally')

    def test_active_branch_delete_restores_source_and_other_branch(self):
        self.manager.create_checkout_branch('vendor','one','blue')
        (self.live/'build.prop').write_text('one')
        self.manager.create_checkout_branch('vendor','two','green')
        (self.live/'build.prop').write_text('two')
        self.manager.delete_branch('vendor','two')
        self.assertIsNone(self.manager.get_active_branch('vendor'))
        self.assertEqual((self.live/'build.prop').read_bytes(),b'ro.test=source\r\n')
        self.manager.checkout_branch('vendor','one')
        self.assertEqual((self.live/'build.prop').read_text(),'one')

    def test_corrupt_state_is_not_treated_as_source(self):
        self.manager.create_checkout_branch('vendor','port','blue')
        (self.live/'build.prop').write_text('unsaved native branch work')
        state=Path(self.manager._state_path('vendor'))
        state.write_text('{ interrupted or manually damaged state')
        with self.assertRaisesRegex(ValueError,'Invalid NeoBranch state'):
            self.manager.checkout_branch('vendor',None)
        self.assertEqual((self.live/'build.prop').read_text(),'unsaved native branch work')
        self.assertEqual(state.read_text(),'{ interrupted or manually damaged state')

    def test_physical_tool_targets_use_worktree_but_protect_source_image(self):
        self.manager.create_checkout_branch('vendor','port','blue')
        from core.workspace_files import WorkspaceFiles
        from ui.workspace_cli import WorkspaceCli
        files=WorkspaceFiles(str(self.base))
        WorkspaceCli._tool_target(files,str(self.live/'archive.zip'))
        WorkspaceCli._tool_target(files,str(self.live/'extracted'))
        with self.assertRaisesRegex(ValueError,'active NeoBranch'):
            WorkspaceCli._tool_target(files,str(self.base/'vendor.img'))


if __name__ == '__main__':unittest.main()
