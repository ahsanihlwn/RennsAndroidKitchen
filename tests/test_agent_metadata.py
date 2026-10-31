# SPDX-License-Identifier: MPL-2.0
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from PySide6.QtCore import QObject,QCoreApplication
from core.agent_models import load_agy_models,run_cli_query
from core.agent_metadata import agy_efforts,cli_usage,codex_limits
from core.command_context import CommandFailure
from services.async_jobs import AsyncJobs


class MetadataTests(unittest.TestCase):
    def test_real_model_probe_timeout_preserves_output_and_is_an_operation_failure(self):
        with tempfile.TemporaryDirectory() as root:
            log=Path(root)/'catalog.json'
            provider={'argv':[sys.executable,'-c','import sys,time;print("partial model output",flush=True);print("stderr details",file=sys.stderr,flush=True);time.sleep(60)']}
            started=time.monotonic();ok,result=load_agy_models(provider,root,log,timeout=.8)
            self.assertFalse(ok);self.assertFalse(result.application_error)
            self.assertLess(time.monotonic()-started,5)
            data=json.loads(log.read_text());self.assertIn('partial model output',data['stdout']);self.assertIn('stderr details',data['stderr'])
            self.assertIn('TimeoutExpired',str(result));self.assertIsNotNone(data['exit_code'])

    def test_quota_groups_are_shared_and_not_token_counters(self):
        output=json.dumps({'status':'SUCCESS','command':{'name':'usage','data':{'groups':[{'name':'Gemini Models','buckets':[{'name':'5h','remaining_fraction':.73,'reset_time':'2030-01-01'}]}]}}})
        quota=cli_usage('agy',output)
        self.assertEqual(quota['groups'][0]['buckets'][0]['remaining'],73)
        self.assertTrue(quota['quotaAvailable'])
        usage=cli_usage('claude',json.dumps({'usage':{'input_tokens':500},'result':'Session usage'}))
        self.assertFalse(usage['quotaAvailable']);self.assertEqual(usage['groups'],[])
        self.assertEqual(codex_limits({'rateLimits':{'primary':{'usedPercent':25}}})['groups'][0]['buckets'][0]['remaining'],75)

    def test_agy_efforts_use_readonly_native_capability_report(self):
        result=agy_efforts(json.dumps({'status':'SUCCESS','command':{'name':'effort','data':{'adjustable':True,'available':['low','high'],'current':'high'}}}))
        self.assertEqual([r['id'] for r in result['rows']],['low','high'])
        with self.assertRaises(ValueError):agy_efforts('{"status":"ERROR"}')

    def test_codex_keeps_window_and_reset_data_for_localized_presentation(self):
        quota=codex_limits({'rateLimits':{'limitId':'codex','primary':{'usedPercent':0,'windowDurationMins':300,'resetsAt':1910000000},'secondary':{'usedPercent':68,'windowDurationMins':10080}}})
        buckets=quota['groups'][0]['buckets']
        self.assertEqual([row['windowMinutes'] for row in buckets],[300,10080])
        self.assertEqual([row['remaining'] for row in buckets],[100,32])
        self.assertEqual(buckets[0]['resetAt'],1910000000)

    def test_zero_exit_with_native_error_result_is_still_a_failed_query(self):
        with tempfile.TemporaryDirectory() as root:
            command=[sys.executable,'-c','print(\'{"is_error":true,"result":"model unavailable"}\')']
            ok,result=run_cli_query(command,root,Path(root)/'query.json')
            self.assertFalse(ok);self.assertFalse(result.application_error);self.assertIn('model unavailable',str(result))


class OptionalJobReportingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QCoreApplication.instance() or QCoreApplication([])

    def run_job(self,payload,*,report=True):
        owner=QObject();jobs=AsyncJobs(owner);received=[]
        with patch('core.error_reporting.report_fatal') as presenter:
            jobs.submit('optional-catalog',lambda:(False,payload),None,lambda ok,result:received.append((ok,result)),report_operation_errors=report)
            until=time.monotonic()+3
            while not received or jobs.busy():
                self.app.processEvents();self.assertLess(time.monotonic(),until);time.sleep(.005)
            return received,presenter.call_args_list

    def test_optional_operation_failure_stays_failed_without_a_modal(self):
        received,presented=self.run_job(CommandFailure('catalog timed out'),report=False)
        self.assertFalse(received[0][0]);self.assertEqual(presented,[])

    def test_optional_setting_never_suppresses_internal_defects_or_unrelated_gui_errors(self):
        _,internal=self.run_job(CommandFailure('typed internal traceback',application_error=True),report=False)
        _,ordinary=self.run_job(CommandFailure('ordinary GUI failure'))
        self.assertEqual(len(internal),1);self.assertTrue(internal[0].kwargs['application_error'])
        self.assertEqual(len(ordinary),1);self.assertFalse(ordinary[0].kwargs['application_error'])


if __name__=='__main__':unittest.main()
