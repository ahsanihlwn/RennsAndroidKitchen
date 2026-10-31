# SPDX-License-Identifier: MPL-2.0
"""Shared RAK Terminal console for GUI processes and worker-run kitchen tools."""
import os
import re
import subprocess
import uuid
import weakref
from contextlib import contextmanager

from PySide6.QtCore import QObject,Signal,Slot,Qt
from core.terminal_pty import PtySignals


class ProcessTerminalManager:
    """A process-backed RAK terminal. Closing its view never interrupts a job."""
    def __init__(self,cwd):
        self.cwd=cwd;self.signals=PtySignals();self.bash_mode=False;self.windows_pty={}
        self._processes=set();self._buffer='';self._detached=False
    def start(self,**_):pass
    def resize(self,*_):pass
    def set_spam_enter(self,*_):pass
    def shutdown(self):self._detached=True
    def get_plain_text(self):return re.sub(r'\x1b\[[0-9;]*[A-Za-z]','',self._buffer)
    def feed(self,text):
        self._buffer=(self._buffer+text)[-100000:]
        if not self._detached:self.signals.data_ready.emit(text.replace('\r\n','\n').replace('\n','\r\n'))
    def observe(self,process,command):
        self._processes.add(process)
        self.feed('\n> '+command+'\n')
    def launch(self,process,program,args):
        self.observe(process,subprocess.list2cmdline([os.path.basename(program),*map(str,args)]))
        process.finished.connect(lambda code,*_:self.complete(process,code))
        process.errorOccurred.connect(lambda _:self.complete(process,-1) if process.error()==process.FailedToStart else None)
        process.start()
    def complete(self,process,code):
        if process not in self._processes:return
        self._processes.discard(process);self.feed(f'\n[exit {code}]\n')
        if not self._processes:self.signals.process_exited.emit(code)
    def write(self,text):
        if '\x03' in text:
            for process in tuple(self._processes):process.kill()
        elif len(self._processes)==1:
            process=next(iter(self._processes))
            if hasattr(process,'write'):process.write(text.encode('utf-8'))
            elif process.stdin:
                process.stdin.write(text if process.text_mode else text.encode('utf-8'));process.stdin.flush()


_publisher_ref=None


class TerminalPublisher(QObject):
    event=Signal(object)
    def __init__(self,owner):
        super().__init__(owner);self.owner=owner;self.jobs={}
        self.event.connect(self.deliver,Qt.QueuedConnection)
    @Slot(object)
    def deliver(self,event):
        if self.owner._closing:return
        kind,job,*payload=event
        if kind=='library':
            manager=self.owner.processTerminal()
            if manager is not None:manager.feed(payload[0])
        elif kind=='start':
            process,command=payload
            manager=self.owner.processTerminal()
            if manager is not None:manager.observe(process,command);self.jobs[job]=manager
        elif job in self.jobs:
            if kind=='data':self.jobs[job].feed(payload[0])
            elif kind=='done':self.jobs.pop(job).complete(*payload)


def register(owner):
    global _publisher_ref
    publisher=TerminalPublisher(owner);_publisher_ref=weakref.ref(publisher)
    return publisher


def publisher_for(command):
    publisher=_publisher_ref() if _publisher_ref else None
    if publisher is None or publisher.owner._closing:return None
    values=command if isinstance(command,(tuple,list)) else [command]
    program=os.path.basename(os.fspath(values[0])).lower().removesuffix('.exe') if values else ''
    # Housekeeping commands do not need an interactive task console.
    if program in ('attrib','reg','taskkill','where','which'):return None
    return publisher


def start_job(publisher,process,command):
    job=uuid.uuid4().hex
    values=command if isinstance(command,(list,tuple)) else None
    caption=subprocess.list2cmdline(list(map(os.fspath,values))) if values else str(command)
    publisher.event.emit(('start',job,process,caption))
    return job


@contextmanager
def library_operation(label):
    """Show Python-library work without changing process-wide stdout or stderr."""
    import traceback
    publisher=publisher_for(['rak',label])
    job=uuid.uuid4().hex
    def output(text):
        if publisher is not None:publisher.event.emit(('library',job,text))
    output('\n> '+label+' [Python library]\n')
    try:
        yield output
    except Exception:
        output(traceback.format_exc()+'[operation failed]\n')
        raise
    else:output('[operation failed]\n' if getattr(output,'failed',False) else '[operation completed]\n')
