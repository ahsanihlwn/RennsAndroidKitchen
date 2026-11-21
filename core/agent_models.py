# SPDX-License-Identifier: MPL-2.0
"""Optional, bounded model discovery with complete local diagnostics."""
import json
import logging
from pathlib import Path

from core import agent_backends,kitchen_process
from core.agent_process import spawn_agent_process
from core.command_context import CommandFailure,failure_from_exception

_log=logging.getLogger(__name__)


def run_cli_query(command,cwd,log_path,*,timeout=20,operation='Agent metadata query'):
    log_path=Path(log_path)
    stdout_path=log_path.with_suffix('.stdout');stderr_path=log_path.with_suffix('.stderr')
    failure=None;code=None;process=None;output='';errors=''
    try:
        log_path.parent.mkdir(parents=True,exist_ok=True)
        # Files, rather than pipes, keep timeout cleanup bounded even if a
        # provider's descendant inherits stdout/stderr and outlives the probe.
        with open(stdout_path,'wb') as stdout,open(stderr_path,'wb') as stderr:
            process=spawn_agent_process(command,cwd=cwd,stdin=kitchen_process.DEVNULL,stdout=stdout,stderr=stderr)
            try:code=process.wait(timeout=timeout)
            except kitchen_process.TimeoutExpired as error:
                failure=failure_from_exception(error)
                process.kill();code=process.wait(timeout=5)
        output=stdout_path.read_text(encoding='utf-8',errors='replace')
        errors=stderr_path.read_text(encoding='utf-8',errors='replace')
    except (OSError,kitchen_process.TimeoutExpired) as error:
        failure=failure_from_exception(error)
        if process is not None:code=process.poll()
    if failure is None and code!=0:failure=CommandFailure(operation+': process exited '+str(code))
    if failure is None:
        try:envelope=json.loads(output)
        except ValueError:envelope=None
        if isinstance(envelope,dict) and (envelope.get('is_error') is True or envelope.get('status') in ('ERROR','FAILED')):
            failure=CommandFailure(operation+': '+str(envelope.get('error') or envelope.get('result') or envelope.get('status')))
    diagnostics={'operation':operation,'command':command,'cwd':cwd,'exit_code':code,
        'stdout_file':str(stdout_path),'stderr_file':str(stderr_path),'stdout':output,'stderr':errors,'failure':str(failure or '')}
    try:log_path.write_text(json.dumps(diagnostics,ensure_ascii=False,indent=2),encoding='utf-8')
    except OSError:
        _log.exception('Writing agent metadata diagnostics: %s',diagnostics,extra={'fatal_suppress_ui':True})
    if failure is not None:
        failure=CommandFailure(operation+'\n'+str(failure)+'\nstdout:\n'+output+'\nstderr:\n'+errors+'\nLog: '+str(log_path),code=getattr(failure,'code','metadata_query_failed'))
        failure.log_path=str(log_path)
        return False,failure
    return True,diagnostics


def load_agy_models(provider,cwd,log_path,*,timeout=20):
    ok,result=run_cli_query(provider['argv']+['models'],cwd,log_path,timeout=timeout,operation='AGY model catalog')
    if not ok:return False,result
    rows=agent_backends.parse_agy_models(result['stdout'])
    if rows:return True,rows
    failure=CommandFailure('AGY model catalog returned no model rows.\n'+result['stdout']+'\n'+result['stderr'])
    failure.log_path=str(log_path)
    return False,failure
