# SPDX-License-Identifier: MPL-2.0
"""Per-command diagnostics, propagated explicitly across Qt and worker threads."""
from contextlib import contextmanager
from contextvars import ContextVar
import threading
import traceback
import re
import subprocess


_command = ContextVar('rak_cli_command', default=None)


class OperationError(RuntimeError):
    """A tool or filesystem operation failed without an application defect."""


class CommandFailure(str):
    def __new__(cls, text, *, application_error=False, code='operation_failed'):
        value = super().__new__(cls, str(text))
        value.application_error = bool(application_error)
        value.code = code
        return value


class DispatchedCommand(str):
    def __new__(cls, text, task_id=None):
        value = super().__new__(cls, text)
        value.task_id = task_id
        return value


def is_application_exception(error):
    # Validation and OS failures are normal command failures. Unknown exceptions
    # retain the crash UI, including failed Source rollback/recovery.
    # Keep archive/codec exception APIs intact while reporting malformed input
    # as an operation failure. Builtin TypeError/AttributeError remain defects.
    if type(error).__module__.startswith(('zipfile', 'tarfile', 'py7zr.', 'rarfile', 'lzma', '_lzma',
                                         'zstandard.', 'compression.zstd', 'backports.zstd', 'lz4.')):return False
    return not (isinstance(error, (OSError, EOFError, OperationError, UnicodeError, re.error, subprocess.SubprocessError))
                or type(error) is ValueError)


def failure_from_exception(error):
    application_error = is_application_exception(error)
    labels = {FileNotFoundError: 'Path not found', FileExistsError: 'Destination already exists',
              IsADirectoryError: 'Expected a file, found a directory',
              NotADirectoryError: 'Parent is not a directory', PermissionError: 'Access denied'}
    if type(error).__name__ == 'RecycleError':
        labels[type(error)] = 'Recycle Bin error'
    label = labels.get(type(error), type(error).__name__)
    reason = str(error)
    text = label + (': ' + reason if reason else '')
    if application_error or type(error) not in labels:
        text = ''.join(traceback.format_exception(type(error), error, error.__traceback__)).rstrip()
    return CommandFailure(text, application_error=application_error, code=type(error).__name__)


class CommandContext:
    def __init__(self, command):
        self.command = command
        self._lock = threading.Lock()
        self._records = []

    def record(self, text, *, application_error=False):
        with self._lock:
            item = (str(text), bool(application_error))
            if item not in self._records:
                self._records.append(item)

    def records(self):
        with self._lock:
            return list(self._records)

    def response(self, result):
        result = dict(result)
        message = result.get('msg', '')
        records = self.records()
        if records:
            result['log'] = '\n\n'.join(text for text, _ in records)
            result['application_error'] = any(internal for _, internal in records)
            if result.get('ok'):
                result['ok'] = False
                result['msg'] = 'Command reported an error; see the log below.'
        if not result.get('ok'):
            result.setdefault('application_error', getattr(message, 'application_error', False))
            result['error_code'] = getattr(message, 'code', 'command_failed')
        result['msg'] = str(result.get('msg', ''))
        return result


def current_command():
    return _command.get()


@contextmanager
def command_scope(command):
    token = _command.set(command)
    try:
        yield command
    finally:
        _command.reset(token)
