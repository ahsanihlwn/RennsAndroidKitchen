# SPDX-License-Identifier: MPL-2.0
"""subprocess-compatible kitchen execution with live RAK Terminal I/O."""
import codecs
import io
import os
import subprocess as _native
import threading
import sys
from contextlib import contextmanager
from contextvars import ContextVar


_background_probe = ContextVar('rak_background_probe', default=False)


@contextmanager
def without_console():
    """Use pipes for a machine protocol or background probe, without a TUI.

    Scoped to this execution context, so concurrent user tools retain their
    terminal output and CommandContext. The probe's caller owns diagnostics.
    """
    token = _background_probe.set(True)
    try:
        yield
    finally:
        _background_probe.reset(token)


background_probe = without_console


def __getattr__(name):return getattr(_native,name)


class Popen(_native.Popen):
    def __init__(self,*args,**kwargs):
        command=args[0] if args else kwargs.get('args',[])
        execution=sys.modules.get('core.terminal_execution')
        publisher=execution.publisher_for(command) if not _background_probe.get() and execution and hasattr(execution,'publisher_for') else None
        inherited=set()
        if publisher is not None and len(args)<=3:
            if os.name == 'nt':
                flags = kwargs.get('creationflags', 0)
                if not flags & (_native.CREATE_NEW_CONSOLE | _native.DETACHED_PROCESS):
                    kwargs['creationflags'] = flags | _native.CREATE_NO_WINDOW
            for channel in ('stdout','stderr'):
                if kwargs.get(channel) is None:kwargs[channel]=_native.PIPE;inherited.add(channel)
        super().__init__(*args,**kwargs)
        if publisher is None:return
        job=execution.start_job(publisher,self,command)
        readers=[]
        for channel in ('stdout','stderr'):
            original=getattr(self,channel)
            if original is None:continue
            text=isinstance(original,io.TextIOBase)
            encoding=getattr(original,'encoding',None) or 'utf-8'
            errors=getattr(original,'errors',None) or 'replace'
            mirror_only=channel in inherited
            write_fd=None
            target=None
            if not mirror_only:
                read_fd,write_fd=os.pipe()
                target=os.fdopen(read_fd,'rb')
                if text:target=io.TextIOWrapper(target,encoding=encoding,errors=errors)
            setattr(self,channel,target)
            def copy_output(original=original,text=text,encoding=encoding,write_fd=write_fd):
                decoder=codecs.getincrementaldecoder(encoding)(errors='replace')
                source=original.buffer if text else original
                try:
                    with os.fdopen(write_fd,'wb',buffering=0) if write_fd is not None else _null_sink() as sink:
                        while True:
                            chunk=source.read1(16384) if hasattr(source,'read1') else source.read(16384)
                            if not chunk:break
                            value=decoder.decode(chunk)
                            if value:publisher.event.emit(('data',job,value))
                            if sink is not None:
                                remaining=memoryview(chunk)
                                while remaining:remaining=remaining[sink.write(remaining):]
                        value=decoder.decode(b'',final=True)
                        if value:publisher.event.emit(('data',job,value))
                except (BrokenPipeError,OSError,ValueError):pass
                finally:original.close()
            thread=threading.Thread(target=copy_output,daemon=True,name='RAK-Terminal-Output');thread.start();readers.append(thread)
        def completed():
            code=_native.Popen.wait(self)
            for thread in readers:thread.join()
            publisher.event.emit(('done',job,self,code))
        threading.Thread(target=completed,daemon=True,name='RAK-Terminal-Process').start()


from contextlib import nullcontext as _null_sink


def run(*args,input=None,capture_output=False,timeout=None,check=False,**kwargs):
    if input is not None:
        if kwargs.get('stdin') is not None:raise ValueError('stdin and input arguments may not both be used')
        kwargs['stdin']=_native.PIPE
    if capture_output:
        if kwargs.get('stdout') is not None or kwargs.get('stderr') is not None:raise ValueError('stdout and stderr may not be used with capture_output')
        kwargs['stdout']=kwargs['stderr']=_native.PIPE
    with Popen(*args,**kwargs) as process:
        try:stdout,stderr=process.communicate(input,timeout=timeout)
        except _native.TimeoutExpired as error:
            process.kill();error.stdout,error.stderr=process.communicate();raise
        except BaseException:process.kill();raise
        code=process.poll()
        if check and code:raise _native.CalledProcessError(code,process.args,output=stdout,stderr=stderr)
        return _native.CompletedProcess(process.args,code,stdout,stderr)


def call(*args,timeout=None,**kwargs):
    with Popen(*args,**kwargs) as process:
        try:return process.wait(timeout=timeout)
        except BaseException:process.kill();raise


def check_call(*args,**kwargs):
    code=call(*args,**kwargs)
    if code:raise _native.CalledProcessError(code,kwargs.get('args',args[0] if args else None))
    return 0


def check_output(*args,timeout=None,**kwargs):
    if 'stdout' in kwargs:raise ValueError('stdout argument not allowed')
    return run(*args,stdout=_native.PIPE,timeout=timeout,check=True,**kwargs).stdout
