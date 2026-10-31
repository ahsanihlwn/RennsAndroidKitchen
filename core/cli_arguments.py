# SPDX-License-Identifier: MPL-2.0
"""Preserve actual CLI argv across IPC; strings remain a compatibility input."""
from contextlib import contextmanager
from contextvars import ContextVar
import shlex

_arguments=ContextVar('rak_cli_arguments',default=None)


@contextmanager
def argument_scope(arguments):
    if arguments is not None and (not isinstance(arguments,(list,tuple)) or any(not isinstance(value,str) for value in arguments)):
        raise ValueError('CLI argv must be an array of strings')
    token=_arguments.set(tuple(arguments) if arguments is not None else None)
    try:yield
    finally:_arguments.reset(token)


def tokens(command,rest):
    arguments=_arguments.get()
    if arguments and arguments[0].lower()==command:return list(arguments[1:])
    return [value[1:-1] if len(value)>1 and value[0]==value[-1] and value[0] in "\"'" else value
            for value in shlex.split(rest,posix=False)]
