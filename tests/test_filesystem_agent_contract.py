# SPDX-License-Identifier: MPL-2.0
"""The CLI follows the Kitchen's image/container/filesystem stages."""
import json
from pathlib import Path
import unittest
from unittest.mock import patch
from PySide6.QtCore import QCoreApplication
from tests import test_kitchen_cli as fixtures
from tools.mount_fixture import build_fixture
from tools.audit_partition_viewers import payload_fixture
from core import imgfs


class FilesystemAgentContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QCoreApplication.instance() or QCoreApplication([])
    setUp=fixtures.KitchenCliTests.setUp
    wait=fixtures.KitchenCliTests.wait
    command=fixtures.KitchenCliTests.command
    def tearDown(self):
        if getattr(self,'mounts',None):self.mounts.close()
        fixtures.KitchenCliTests.tearDown(self)

    def enable_mount_session(self):
        from ui.mounts import WorkspaceMounts
        self.mounts=WorkspaceMounts(self.owner);self.owner.mounts=self.mounts;self.owner.headless=False
        original=self.owner.dispatch
        self.owner.dispatch=lambda action,data:self.mounts.mount(data['path']) if action=='mount' else original(action,data)

    def test_br_dat_and_payload_are_not_mountable_and_describe_next_image_stage(self):
        for name in ('system.img.br','system.new.dat','vendor.new.dat.br'):(self.root/name).write_bytes(b'fixture')
        (self.root/'payload.bin').write_bytes(payload_fixture())
        for name in ('system.img.br','system.new.dat','vendor.new.dat.br','payload.bin'):
            with self.subTest(name=name):
                description=json.loads(self.command('inspect '+name+' --json')['msg'])
                self.assertFalse(description['mountable']);self.assertNotIn('mount',[row['action'] for row in description['operations']])
                self.assertIn(description['unpack_result']['result_kind'],('image','partition_images'))
                result=self.command('mount '+name)
                self.assertFalse(result['ok']);self.assertFalse(result['application_error']);self.assertIn('IMG',result['msg'])
        self.assertEqual(self.owner._tasks._serial,0)
        dat=json.loads(self.command('inspect system.new.dat --json')['msg'])
        self.assertEqual(dat['unpack_result']['requires'],[{'path':str(self.root/'system.transfer.list'),'exists':False}])

    def test_real_br_to_img_then_mount_finishes_actual_mount_task(self):
        image=build_fixture(self.root);data=image.read_bytes()
        from core.brotli_tool import compress_to_br
        ok,path=compress_to_br(str(image),keep_source=True);self.assertTrue(ok,path)
        result=self.command('unpack '+Path(path).name);self.assertTrue(result['ok'],result)
        self.assertEqual(image.read_bytes(),data)
        self.enable_mount_session()
        result=self.command('mount '+image.name);self.assertTrue(result['ok'],result)
        self.assertIsNotNone(self.mounts.reference(str(image)))
        self.assertTrue(self.mounts.writable(str(image)))
        history=self.owner._tasks.loading.get_history();self.assertTrue(history[-1]['ok'])
        self.assertIn('Mount image',history[-1]['label'])

    def test_mount_worker_failure_is_not_cli_success(self):
        image=build_fixture(self.root);self.enable_mount_session()
        with patch('core.imgfs.list_image',side_effect=ValueError('MOUNT_READ_FAILED')):
            result=self.command('mount '+image.name)
        self.assertFalse(result['ok']);self.assertFalse(result['application_error'])
        self.assertIn('MOUNT_READ_FAILED',result['msg']);self.assertIsNone(self.mounts.reference(str(image)))
        self.assertFalse(self.owner._tasks.loading.get_history()[-1]['ok'])

    def test_real_img_unpack_branch_edit_repack_to_requested_img(self):
        contexts=self.root/'fixture_contexts';contexts.write_text('/system(/.*)? u:object_r:system_file:s0\n')
        image=build_fixture(self.root,contexts=contexts,name='system.img');source_image=image.read_bytes()
        result=self.command('unpack '+image.name);self.assertTrue(result['ok'],result)
        folder=image.with_suffix('');source=folder/'etc/native.txt';original=source.read_bytes()
        self.assertTrue((self.root/'config'/f'{folder.name}_info').is_file())
        from main_ext.class_NeoBranchManager import NeoBranchManager
        manager=NeoBranchManager(str(self.root));manager.create_branch(folder.name,'edit','blue');manager.set_active_branch(folder.name,'edit')
        result=self.command("sed -i 's/Original/Edited/' "+folder.name+'/etc/native.txt');self.assertTrue(result['ok'],result)
        self.assertEqual(source.read_bytes(),original)
        with patch('core.repack_policy.get_selinux_mode',return_value='none'):
            result=self.command('repack '+folder.name+' -o edited.img --json')
        self.assertTrue(result['ok'],result);self.assertEqual(json.loads(result['msg'])['outputs'],[str(self.root/'edited.img')])
        self.assertEqual(imgfs.read_path(str(self.root/'edited.img'),'etc/native.txt')[0],b'Edited mounted document\n')
        self.assertEqual(source.read_bytes(),original);self.assertEqual(image.read_bytes(),source_image)

    def test_explicit_dat_outputs_keep_transfer_list_and_can_decode(self):
        folder=self.root/'system';folder.mkdir()
        config=self.root/'config';config.mkdir(exist_ok=True);(config/'system_info').write_text('Format: EXT4\n')
        payload=bytes(range(256))*16
        def build(path,kind):
            self.assertEqual(kind,'repack_ext')
            image=Path(path+'_new.img');image.write_bytes(payload);return True,str(image)
        for suffix in ('.new.dat','.new.dat.br'):
            with self.subTest(suffix=suffix),patch('ui.workspace_tools.repack',side_effect=build):
                name='export'+suffix
                result=self.command('repack system -o '+name+' --json');self.assertTrue(result['ok'],result)
                outputs=json.loads(result['msg'])['outputs'];self.assertIn(str(self.root/'export.transfer.list'),outputs)
                self.assertIn(str(self.root/'export.patch.dat'),outputs)
                from core.kitchen_conversions import dat_to_image
                rebuilt=dat_to_image(str(self.root/name));self.assertEqual(Path(rebuilt).read_bytes(),payload)
                for path in [*outputs,str(self.root/'export.img')]:Path(path).unlink(missing_ok=True)

    def test_explicit_img_and_img_br_outputs_use_filesystem_repack(self):
        folder=self.root/'system';folder.mkdir();config=self.root/'config';config.mkdir(exist_ok=True)
        (config/'system_info').write_text('Format: EXT4\n');payload=b'filesystem image data'*1024
        def build(path,kind):
            self.assertEqual(kind,'repack_ext');image=Path(path+'_new.img');image.write_bytes(payload);return True,str(image)
        with patch('ui.workspace_tools.repack',side_effect=build):
            result=self.command('repack system -o raw.img --json');self.assertTrue(result['ok'],result)
            self.assertEqual((self.root/'raw.img').read_bytes(),payload)
            result=self.command('repack system -o compressed.img.br --json');self.assertTrue(result['ok'],result)
        from core.brotli_tool import decompress_from_br
        ok,decoded=decompress_from_br(str(self.root/'compressed.img.br'),keep_source=True)
        self.assertTrue(ok,decoded);self.assertEqual(Path(decoded).read_bytes(),payload)

    def test_inspect_sparse_container_only_mounts_supported_inner_filesystem(self):
        (self.root/'sparse.img').write_bytes(bytes.fromhex('3aff26ed')+bytes(24))
        with patch('core.imgfs.detect_fs',return_value='SUPER'):
            description=json.loads(self.command('inspect sparse.img --json')['msg'])
        self.assertFalse(description['mountable']);self.assertEqual(description['unpack_result']['result_kind'],'raw_image')
        self.assertTrue(description['unpack_result']['replaces_input'])

    def test_nested_image_unpack_edit_repack_uses_its_adjacent_metadata(self):
        nested=self.root/'payload-output';nested.mkdir()
        contexts=self.root/'nested_contexts';contexts.write_text('/system(/.*)? u:object_r:system_file:s0\n')
        image=build_fixture(nested,contexts=contexts,name='system.img')
        self.assertTrue(self.command('unpack payload-output/system.img')['ok'])
        info=json.loads(self.command('inspect payload-output/system --json')['msg'])
        self.assertEqual(info['kind'],'EXT4');self.assertIn('repack_image',[row['action'] for row in info['operations']])
        self.assertTrue(self.command("sed -i 's/Original/Nested verified/' payload-output/system/etc/native.txt")['ok'])
        info=json.loads(self.command('stat payload-output/system/etc/native.txt --json')['msg'])
        self.assertEqual(info['android']['mode'],'0644')
        with patch('core.repack_policy.get_selinux_mode',return_value='none'):
            result=self.command('repack payload-output/system -o nested-verified.img --json')
        self.assertTrue(result['ok'],result)
        self.assertEqual(imgfs.read_path(str(self.root/'nested-verified.img'),'etc/native.txt')[0],b'Nested verified mounted document\n')

    def test_brotli_conversion_cannot_overwrite_source_image_of_active_branch(self):
        folder=self.root/'system';folder.mkdir();(folder/'original.txt').write_text('source')
        image=self.root/'system.img';image.write_bytes(b'original image');(self.root/'system.img.br').write_bytes(b'compressed fixture')
        from main_ext.class_NeoBranchManager import NeoBranchManager
        manager=NeoBranchManager(str(self.root));manager.create_branch('system','keep','blue');manager.set_active_branch('system','keep')
        description=json.loads(self.command('inspect system.img.br --json')['msg'])
        self.assertEqual(description['unpack_output_blocked_by_branch'],'keep')
        self.assertNotIn('unpack',[row['action'] for row in description['operations']])
        with patch('core.brotli_tool.decompress_from_br',side_effect=AssertionError('must validate output before conversion')):
            result=self.command('unpack system.img.br')
        self.assertFalse(result['ok']);self.assertFalse(result['application_error'])
        self.assertEqual(image.read_bytes(),b'original image')


if __name__=='__main__':unittest.main()
