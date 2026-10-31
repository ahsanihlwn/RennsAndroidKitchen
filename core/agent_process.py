# SPDX-License-Identifier: MPL-2.0
"""Hidden, pipe-based agent transports. Never create an interactive terminal."""
import json
import logging
import os
from pathlib import Path
import queue
import threading

from PySide6.QtCore import QObject, Signal
from core import kitchen_process


_log = logging.getLogger(__name__)


def hidden_startup_options():
    """Give CLI descendants an inherited hidden console on Windows.

    CREATE_NO_WINDOW leaves the CLI without a console. A console application
    it launches without that flag can then allocate a visible window. Starting
    with a hidden console avoids that gap without intercepting provider tools.
    Pipes remain the transport; this console is never an interactive shell.
    """
    if os.name!='nt':return {}
    startup=kitchen_process.STARTUPINFO()
    startup.dwFlags|=kitchen_process.STARTF_USESHOWWINDOW
    startup.wShowWindow=kitchen_process.SW_HIDE
    return {'creationflags':kitchen_process.CREATE_NEW_CONSOLE,'startupinfo':startup}


class AgentProcess(QObject):
    event = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._outgoing = queue.Queue()
        self._process = None
        self._closed = False
        self._termination_requested=threading.Event()
        self._process_lock=threading.Lock();self._termination_started=False

    def start(self, argv, cwd, environment, log_path):
        def notify(event):
            try:self.event.emit(event)
            except RuntimeError:pass  # The workspace's Qt object was destroyed; keep draining/logging.
        def run():
            try:
                Path(log_path).parent.mkdir(parents=True, exist_ok=True)
                with open(log_path, 'a', encoding='utf-8') as log:
                    lock = threading.Lock()
                    def record(channel, text):
                        with lock:
                            log.write(json.dumps({'channel': channel, 'text': text}, ensure_ascii=False) + '\n'); log.flush()
                    record('process', json.dumps({'command': argv, 'cwd': cwd}))
                    with kitchen_process.without_console():
                        process = kitchen_process.Popen(argv, cwd=cwd, env=environment, stdin=kitchen_process.PIPE,
                            stdout=kitchen_process.PIPE, stderr=kitchen_process.PIPE, **hidden_startup_options(),
                            text=True, encoding='utf-8', errors='replace', bufsize=1)
                    self._process = process
                    notify(('started', process.pid))
                    self._terminate_current()
                    def read(stream, channel):
                        try:
                            for line in stream:
                                record(channel, line)
                                if channel == 'stderr': notify(('diagnostic', line)); continue
                                try:
                                    data = json.loads(line)
                                    if not isinstance(data, dict): raise ValueError('Expected a JSON object')
                                except (ValueError, json.JSONDecodeError):
                                    notify(('protocol_error', 'Unexpected non-JSON output. See the agent log.')); continue
                                notify(('message', data))
                        finally: stream.close()
                    readers = [threading.Thread(target=read, args=(process.stdout, 'stdout'), daemon=True),
                               threading.Thread(target=read, args=(process.stderr, 'stderr'), daemon=True)]
                    for reader in readers: reader.start()
                    def write():
                        try:
                            while True:
                                message = self._outgoing.get()
                                if message is None: break
                                process.stdin.write(json.dumps(message, ensure_ascii=False) + '\n'); process.stdin.flush()
                        except (BrokenPipeError, OSError, ValueError): pass
                        finally:
                            try: process.stdin.close()
                            except OSError: pass
                    writer = threading.Thread(target=write, daemon=True); writer.start()
                    code = process.wait()
                    self._closed=True
                    record('process_exit',str(code));notify(('process_exited',code))
                    self._outgoing.put(None)
                    for reader in readers: reader.join()
                    writer.join()
                    record('exit', str(code)); notify(('exit', code))
            except OSError as error:
                _log.exception('Agent process could not start'); notify(('launch_error', str(error)))
        threading.Thread(target=run, daemon=True, name='RAK-Agent-Process').start()

    def send(self, message):
        if not self._closed: self._outgoing.put(message)

    def close_input(self):
        if not self._closed:
            self._closed = True; self._outgoing.put(None)

    def terminate(self):
        """Stop only this owned CLI process; never kill Kitchen/device jobs."""
        self._termination_requested.set();self.close_input()
        threading.Thread(target=self._terminate_current,daemon=True,name='RAK-Agent-Stop').start()

    def _terminate_current(self):
        with self._process_lock:
            process=self._process
            if not self._termination_requested.is_set() or process is None:return
            code=process.poll()
            if code is not None:
                try:self.event.emit(('process_exited',code))
                except RuntimeError:pass
                return
            if self._termination_started:return
            self._termination_started=True
        try:process.terminate()
        except OSError as error:
            with self._process_lock:self._termination_started=False
            _log.exception('Stopping owned agent process')
            try:self.event.emit(('control_error',str(error)))
            except RuntimeError:pass
