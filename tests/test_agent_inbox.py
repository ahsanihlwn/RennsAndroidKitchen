# SPDX-License-Identifier: MPL-2.0
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from core.agent_inbox import AgentInbox
from core.agent_cli import parse


class InboxTests(unittest.TestCase):
    def test_waiting_consumer_receives_the_frozen_context(self):
        store=AgentInbox();result=[];context={'paths':['vendor/a.rc'],'documents':[{'content':'draft'}]}
        waiter=threading.Thread(target=lambda:result.append(store.next('agent-one',2)))
        waiter.start();message=store.send('Please check this',context);context['documents'][0]['content']='changed'
        waiter.join(1);self.assertFalse(waiter.is_alive())
        self.assertEqual(result[0]['message']['id'],message['id'])
        self.assertEqual(result[0]['message']['context']['documents'][0]['content'],'draft')
        self.assertNotIn('claim',store.get(message['id']))
    def test_two_agents_cannot_claim_the_same_request(self):
        store=AgentInbox();message=store.send('work',{});results=[];gate=threading.Barrier(3)
        def claim(agent):gate.wait();results.append(store.next(agent))
        threads=[threading.Thread(target=claim,args=(name,)) for name in ('a','b')]
        for thread in threads:thread.start()
        gate.wait()
        for thread in threads:thread.join(1)
        self.assertEqual(sum(row['message'] is not None for row in results),1)
        self.assertEqual(store.get(message['id'])['state'],'claimed')
    def test_wrong_owner_and_expired_claim_cannot_finish_work(self):
        store=AgentInbox();message=store.send('work',{});claim=store.next('a')
        with self.assertRaises(ValueError):store.update(message['id'],'b',claim['claim'],'reply',{'answer':'wrong'})
        with self.assertRaises(ValueError):store.update(message['id'],'a','wrong','reply',{'answer':'wrong'})
        store.update(message['id'],'a',claim['claim'],'start')
        store.update(message['id'],'a',claim['claim'],'progress',{'message':'Repacking'})
        done=store.update(message['id'],'a',claim['claim'],'reply',{'answer':'Done','paths':['vendor.img']})
        self.assertEqual(done['state'],'completed');self.assertEqual(done['paths'],['vendor.img'])
        with self.assertRaises(ValueError):store.update(message['id'],'a',claim['claim'],'reply',{'answer':'again'})
    def test_stop_is_cooperative_and_does_not_pretend_a_tool_was_killed(self):
        store=AgentInbox();message=store.send('work',{});claim=store.next('a')
        row=store.cancel(message['id']);self.assertTrue(row['cancel_requested']);self.assertEqual(row['state'],'claimed')
        with self.assertRaises(ValueError):store.update(message['id'],'a',claim['claim'],'start')
        row=store.update(message['id'],'a',claim['claim'],'ack-cancel')
        self.assertEqual(row['state'],'cancelled')
        queued=store.send('cancel before receipt',{});store.cancel(queued['id'])
        self.assertIsNone(store.next('a')['message'])
    def test_persistence_preserves_queue_results_and_marks_abandoned_work(self):
        with tempfile.TemporaryDirectory() as temporary:
            path=Path(temporary)/'inbox.json';store=AgentInbox(path)
            first=store.send('first',{});claim=store.next('a')
            store.update(first['id'],'a',claim['claim'],'reply',{'answer':'result'})
            second=store.send('second',{});store.next('a')
            queued=store.send('third',{})
            reopened=AgentInbox(path)
            self.assertEqual(reopened.get(first['id'])['answer'],'result')
            self.assertEqual(reopened.get(second['id'])['state'],'interrupted')
            self.assertEqual(reopened.get(queued['id'])['state'],'queued')
            self.assertNotIn('claim',reopened.get(second['id']))
    def test_failed_atomic_write_rolls_back_memory_and_keeps_existing_history(self):
        with tempfile.TemporaryDirectory() as temporary:
            path=Path(temporary)/'inbox.json';store=AgentInbox(path);message=store.send('work',{});saved=path.read_bytes()
            with patch('core.agent_inbox.os.replace',side_effect=OSError('disk unavailable')):
                with self.assertRaises(OSError):store.next('a')
            self.assertEqual(store.get(message['id'])['state'],'queued');self.assertEqual(path.read_bytes(),saved)
    def test_close_releases_inbox_waiters(self):
        store=AgentInbox();result=[];thread=threading.Thread(target=lambda:result.append(store.next('a',2)))
        thread.start();store.close();thread.join(1)
        self.assertTrue(result[0]['closed'])
    def test_corrupt_history_is_rejected_without_overwriting_it(self):
        with tempfile.TemporaryDirectory() as temporary:
            path=Path(temporary)/'inbox.json';data=b'{"version":1,"messages":[{"state":"completed"}]}'
            path.write_bytes(data)
            with self.assertRaises(ValueError):AgentInbox(path)
            self.assertEqual(path.read_bytes(),data)
    def test_targeted_messages_and_failed_results(self):
        store=AgentInbox();message=store.send('work',{},target='chosen')
        self.assertIsNone(store.next('other')['message']);claim=store.next('chosen')
        result=store.update(message['id'],'chosen',claim['claim'],'fail',{'answer':'Tool returned exit code 1'})
        self.assertEqual(result['state'],'failed');self.assertEqual(result['answer'],'Tool returned exit code 1')
    def test_cli_requires_explicit_claim_and_structured_result(self):
        self.assertEqual(parse('inbox','next --agent "My agent" --wait 30','.')['agent'],'My agent')
        with self.assertRaises(ValueError):parse('inbox','next --wait 30','.')
        with self.assertRaises(ValueError):parse('inbox','reply id --agent a --claim token','.')
        with tempfile.TemporaryDirectory() as temporary:
            path=Path(temporary)/'result.json';path.write_text(json.dumps({'answer':'Done'}))
            options=parse('inbox','reply id --agent a --claim token --from "'+str(path)+'"','.')
            self.assertEqual(options['payload']['answer'],'Done')


if __name__=='__main__':unittest.main()
