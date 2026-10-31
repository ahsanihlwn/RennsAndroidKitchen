# SPDX-License-Identifier: MPL-2.0
"""Read-only CLI protocol handshakes. No model turn is submitted by default."""
import argparse
import json
import os
from pathlib import Path
import queue
import sys
import tempfile
import threading
import time

if __package__ in (None,''):sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from core import agent_backends,kitchen_process
from core.agent_protocols import PROTOCOLS
from core.agent_process import hidden_startup_options


def probe(provider,*,prompt=None):
    q=queue.Queue();events=[];diagnostics=[]
    with tempfile.TemporaryDirectory(prefix='rak-agent-probe-',ignore_cleanup_errors=True) as folder:
        args=provider['argv']+agent_backends.launch_arguments(provider)
        with kitchen_process.without_console():
            process=kitchen_process.Popen(args,cwd=folder,stdin=kitchen_process.PIPE,stdout=kitchen_process.PIPE,stderr=kitchen_process.PIPE,
                **hidden_startup_options(),text=True,encoding='utf-8',errors='replace',bufsize=1)
        def read(stream,channel):
            for line in stream:q.put((channel,line))
        readers=[threading.Thread(target=read,args=(process.stdout,'stdout'),daemon=True),threading.Thread(target=read,args=(process.stderr,'stderr'),daemon=True)]
        for reader in readers:reader.start()
        def send(data):process.stdin.write(json.dumps(data)+'\n');process.stdin.flush()
        protocol=PROTOCOLS[provider['protocol']](send,events.append,folder,'Use no tools. Reply only to the test prompt.')
        sent=False;deadline=time.monotonic()+(90 if prompt else 30)
        try:
            protocol.start()
            while time.monotonic()<deadline:
                if any(e['type']=='ready' for e in events):
                    if not prompt:break
                    if not sent:protocol.prompt(prompt);sent=True
                if any(e['type'] in ('done','error') for e in events):break
                if process.poll() is not None and q.empty():break
                try:channel,line=q.get(timeout=.2)
                except queue.Empty:continue
                if channel=='stderr':diagnostics.append(line);continue
                try:protocol.receive(json.loads(line))
                except (ValueError,KeyError,TypeError) as error:events.append({'type':'error','text':str(error),'wire':line[:1000]})
        finally:
            process.stdin.close()
            try:process.wait(timeout=5)
            except kitchen_process.TimeoutExpired:
                # This is an isolated test process, not a workspace/user tool.
                process.terminate()
                try:process.wait(timeout=3)
                except kitchen_process.TimeoutExpired:process.kill();process.wait()
            for reader in readers:reader.join(timeout=2)
            process.stdout.close();process.stderr.close()
        return {'provider':provider['id'],'ready':any(e['type']=='ready' for e in events),
            'models':sum(len(e['rows']) for e in events if e['type']=='models'),
            'commands':sum(len(e['rows']) for e in events if e['type']=='commands'),
            'result':[e for e in events if e['type'] in ('done','error')],
            'answer':'\n'.join(e.get('text','') for e in events if e['type']=='text' and not e.get('append')) or ''.join(e.get('text','') for e in events if e['type']=='text'),
            'timed_out':bool(prompt) and not any(e['type'] in ('done','error') for e in events),
            'events':[e['type'] for e in events], 'diagnostics':''.join(diagnostics)[-1800:]}


def main():
    parser=argparse.ArgumentParser();parser.add_argument('provider',choices=['codex','opencode','claude','agy']);parser.add_argument('--prompt')
    args=parser.parse_args()
    provider=next((p for p in agent_backends.discover() if p['id']==args.provider),None)
    if provider is None:raise SystemExit('CLI not found')
    print(json.dumps(probe(provider,prompt=args.prompt),ensure_ascii=False))


if __name__=='__main__':main()
