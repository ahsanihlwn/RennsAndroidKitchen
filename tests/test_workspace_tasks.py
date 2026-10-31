# SPDX-License-Identifier: MPL-2.0
"""Queue order, source-restore halt and original loading lifetime."""
import unittest
import time

from PySide6.QtCore import QCoreApplication
from ui.workspace_tasks import WorkspaceTasks


class WorkspaceTasksTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QCoreApplication.instance() or QCoreApplication([])

    def setUp(self):
        self.running=[];self.results=[]
        self.tasks=WorkspaceTasks(runner=lambda worker,arg,callback:self.running.append((worker,callback)))

    def tearDown(self):self.tasks.close()

    def enqueue(self,label,worker=lambda:True):
        return self.tasks.enqueue(label,worker,lambda ok,result:self.results.append((label,ok,result)))

    def finish(self):
        worker,callback=self.running.pop(0)
        callback(*worker())

    def test_same_label_tasks_keep_identity_and_source_ring_order(self):
        keys=[self.enqueue('Extracting fixture',lambda n=n:(True,n)) for n in range(3)]
        self.assertEqual(self.tasks.queue.snapshot(),{'ring1':keys[0],'ring2':keys[1],'ring3':keys[2:]})
        self.assertEqual(len(self.running),1);self.assertEqual(self.tasks.count,3)
        self.assertTrue(self.tasks.pending)
        for _ in range(3):self.finish()
        self.assertEqual([row[2] for row in self.results],[0,1,2])
        self.assertEqual(self.tasks.count,0);self.assertEqual(self.tasks.state,'done')
        self.assertEqual(len(self.tasks.history_lines),3)
        self.tasks.loading.set_clean()
        self.assertEqual(self.tasks.state,'idle');self.assertEqual(len(self.tasks.history_lines),3)
        self.tasks.clear_history();self.assertFalse(self.tasks.history_lines)

    def test_normal_failure_continues_but_restore_failure_halts_waiting_tasks(self):
        self.enqueue('First',lambda:(False,'normal failure'))
        self.enqueue('Repack',lambda:(False,'NEOBRANCH_RESTORE_FAILED: fixture'))
        never=[];self.enqueue('Queued',lambda:never.append(True))
        self.finish();self.assertEqual(len(self.running),1)
        self.finish()
        self.assertFalse(never);self.assertFalse(self.running)
        self.assertEqual(self.tasks.count,0);self.assertTrue(self.tasks.halted)
        self.assertTrue(self.results[-1][2].startswith('Queue halted:'))
        history=list(self.tasks.history_lines)
        with self.assertRaisesRegex(RuntimeError,'source restore'):self.enqueue('Later')
        self.assertEqual(self.tasks.history_lines,history)

    def test_close_ignores_late_completion_and_does_not_start_pending_worker(self):
        self.enqueue('Running');self.enqueue('Waiting')
        self.tasks.close();self.finish()
        self.assertFalse(self.results);self.assertFalse(self.running)
        self.assertFalse(self.tasks.queue.is_busy())

    def test_partition_batch_has_one_progress_group_and_reports_every_member(self):
        results=[]
        keys=self.tasks.enqueue_batch('Extracting payload',[
            ('system',lambda:(False,'fixture failure'),lambda ok,value:results.append((ok,value))),
            ('vendor',lambda:(True,'vendor'),lambda ok,value:results.append((ok,value)))])
        self.assertEqual(len(set(keys)),2)
        self.assertEqual(len({entry['batch'] for entry in self.tasks._entries.values()}),1)
        self.finish();self.assertEqual(self.tasks.count,1)
        self.finish();self.assertEqual(results,[(False,'fixture failure'),(True,'vendor')])
        self.assertEqual(self.tasks.state,'error');self.assertEqual(len(self.tasks.history_lines),2)
        self.assertEqual({entry['label'] for entry in self.tasks.loading.get_history()},{'Extracting payload'})

    def test_actual_background_worker_delivers_payload_on_gui_thread(self):
        from PySide6.QtCore import QThread
        native=WorkspaceTasks();received=[]
        try:
            native.enqueue('Fixture',lambda:(True,{'value':7}),
                           lambda ok,value:received.append((ok,value,QThread.currentThread())))
            deadline=time.monotonic()+5
            while not received and time.monotonic()<deadline:
                self.app.processEvents();time.sleep(.005)
            self.assertEqual(received,[(True,{'value':7},self.app.thread())])
        finally:native.close()


if __name__=='__main__':unittest.main()
