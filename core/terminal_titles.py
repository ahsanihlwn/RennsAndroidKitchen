# SPDX-License-Identifier: MPL-2.0
"""Original terminal caption parsing, independent of Main's presentation."""
import os
import shlex


def normalize_terminal_command(command,bin_root):
    if not bin_root:return command
    try:parts=shlex.split(command,posix=False)
    except ValueError:return command
    if not parts:return command
    from core.terminal_native_router import is_locked_terminal_command
    if is_locked_terminal_command(parts[0]):
        return command
    from core.terminal_binary import resolve_terminal_binary
    binary=resolve_terminal_binary(bin_root,parts[0])
    return ' '.join(['"'+binary+'"',*parts[1:]]) if binary else command


def command_name(raw):
    clean=str(raw or '').strip()
    if not clean:return 'terminal'
    try:parts=shlex.split(clean,posix=False)
    except ValueError:parts=clean.split()
    if not parts:return 'terminal'
    first=parts[0].strip().strip('"').strip("'")
    if not first:return 'terminal'
    base=os.path.basename(first.replace('\\','/'))
    name,extension=os.path.splitext(base)
    if extension.lower() in ('.exe','.bat','.cmd','.sh','.py','.ps1'):base=name
    return base or first or 'terminal'
