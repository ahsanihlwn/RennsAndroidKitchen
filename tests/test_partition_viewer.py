# SPDX-License-Identifier: MPL-2.0
from pathlib import Path
from types import SimpleNamespace
import tempfile
import time
import unittest
from unittest.mock import patch

from PySide6.QtCore import QCoreApplication,QEvent,Qt
from core.partition_viewer import payload_partitions,extract_payload,super_info,extract_super
from ui.partition_viewer import PartitionDocument,extract
from ui.workspace_tasks import WorkspaceTasks
from tools.audit_partition_viewers import PARTS,SUPER_PARTS,payload_fixture


class PartitionViewerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QCoreApplication.instance() or QCoreApplication([])
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'payload.bin';self.path.write_bytes(payload_fixture());self.document=PartitionDocument()
    def tearDown(self):
        self.document.close();self.document.deleteLater();QCoreApplication.sendPostedEvents(None,QEvent.DeferredDelete);self.tmp.cleanup()
    def wait(self,predicate):
        deadline=time.monotonic()+8
        while not predicate():
            self.assertLess(time.monotonic(),deadline);self.app.processEvents();time.sleep(.002)

    def test_real_payload_metadata_and_exact_partition_extraction(self):
        self.assertEqual(payload_partitions(str(self.path)),PARTS)
        with patch('core.partition_viewer._payload_service',return_value=None):
            extract_payload(str(self.path),'vendor',self.tmp.name)
        self.assertEqual((Path(self.tmp.name)/'vendor.img').read_bytes(),(b'NATIVE_PARTITION_vendor\n'*4096)[:4096])
        self.assertFalse((Path(self.tmp.name)/'system.img').exists())

    def test_payload_range_control_and_select_all(self):
        self.document.load(str(self.path),'payload');self.wait(lambda:not self.document.busy)
        self.document.select('a',0,0);self.document.select('a',2,Qt.ShiftModifier.value)
        self.assertEqual(self.document.selected,PARTS[:3])
        self.document.select('a',1,Qt.ControlModifier.value);self.assertEqual(self.document.selected,['system','product'])
        self.document.selectAll(True);self.assertEqual(self.document.selected,PARTS)
        self.document.selectAll(False);self.assertFalse(self.document.selected)

    def test_super_slots_filter_group_names_and_control_preserves_other_slot(self):
        with patch('ui.partition_viewer.super_info',return_value={'partitions':SUPER_PARTS,'active_slot':'A'}):
            self.document.load(str(self.path),'super');self.wait(lambda:not self.document.busy)
        self.assertEqual(self.document.partsA,['system_a','vendor_a','product_a']);self.assertEqual(self.document.partsB,['system_b','vendor_b'])
        self.document.select('a',0,0);self.document.select('b',0,Qt.ControlModifier.value)
        self.assertEqual(self.document.selected,['system_a','system_b'])
        self.document.select('b',1,0);self.assertEqual(self.document.selected,['vendor_b'])

    def test_super_info_and_selected_all_commands_use_original_argument_order(self):
        with patch('core.partition_viewer._super_command',return_value=[' Name: system_a',' Group: main_a',' Name: vendor_b',' Group: main_b']) as run:
            self.assertEqual(super_info('super.img')['active_slot'],'B')
            self.assertEqual(super_info('super.img')['partitions'],['system_a','vendor_b'])
            extract_super('super.img','system_a','out');self.assertEqual(run.call_args.args,('-p','system_a','super.img','out'))
            extract_super('super.img',None,'out');self.assertEqual(run.call_args.args,('super.img','out'))

    def test_workspace_owns_partition_batch_and_refreshes_after_success(self):
        running=[];tasks=WorkspaceTasks(runner=lambda worker,arg,callback:running.append((worker,callback)))
        refreshed=[];messages=[]
        session=SimpleNamespace(_tab_data=[{'path':str(self.path),'documentPath':str(self.path),'kind':'payload'}],_closing=False,_history=[],_tasks=tasks,statusChanged=SimpleNamespace(emit=lambda:None),message=lambda *args:messages.append(args),directories=[SimpleNamespace(base=self.tmp.name,reloadMetadata=lambda:refreshed.append(True))])
        try:
            with patch('core.partition_viewer.extract_payload',side_effect=lambda path,part,out:part):
                extract(session,str(self.path),'payload',['vendor','system']);self.assertEqual(tasks.count,2)
                batches={row['batch'] for row in tasks._entries.values()};self.assertEqual(len(batches),1)
                while running:
                    worker,callback=running.pop(0);callback(*worker())
            self.assertEqual([row['label'] for row in session._history],['vendor','system']);self.assertEqual(len(refreshed),2);self.assertFalse(messages)
            self.assertEqual(tasks.count,0);self.assertEqual(tasks.state,'done')
            extract(session,str(self.path),'payload',['../outside']);self.assertTrue(messages);self.assertEqual(tasks.count,0)
        finally:tasks.close();tasks.deleteLater();QCoreApplication.sendPostedEvents(None,QEvent.DeferredDelete)


if __name__=='__main__':unittest.main()
