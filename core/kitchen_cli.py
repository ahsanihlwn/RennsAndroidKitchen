# SPDX-License-Identifier: MPL-2.0
"""Explicit, non-dialog Kitchen commands shared by live IPC and standalone CLI."""
import argparse
import os
import shlex
import math

NAMESPACES = ('archive', 'payload', 'super', 'apk', 'br', 'dat')
HELP = """Kitchen file commands (paths may be quoted):
  archive list <file> [--json]
  archive extract <file> --to <folder> [--path <member> ...]
  archive pack <folder> --to <file.zip|file.7z|file.tar[.gz|.bz2|.xz]>
  payload list <payload.bin|OTA.zip> [--json]
  payload extract <payload.bin|OTA.zip> --to <folder> (--all | --partition <name> ...)
  super list <super.img> [--json]
  super extract <super.img> --to <folder> (--all | --partition <name> ...)
  apk decompile|build|build-sign|build-keeporig|sign|unsign <app.apk>
  br compress|decompress <file> (source retained)
  dat to-image|to-folder <file.new.dat[.br]>
  unpack <file> (auto-detect images, archives, APK, BR/DAT and payload)
  repack <partition-folder|app.apk|apktool-folder>
Extraction and packing reject existing output files. Choose an empty destination.
Commands wait for their own task result; --json returns structured results.
Use --timeout <seconds> to change the wait, or --no-wait with a live Kitchen
session to receive a task ID and inspect it using rak activity show <id> --json.
"""


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError(message)


def parse(namespace, rest, cwd):
    from core.cli_arguments import tokens as argument_tokens
    tokens=argument_tokens(namespace,rest)
    if not tokens or tokens[0] in ('help', '--help', '-h'):
        return {'help': True}
    choices = {'archive': ('list', 'ls', 'extract', 'pack'),
               'payload': ('list', 'ls', 'extract'), 'super': ('list', 'ls', 'extract'),
               'apk': ('decompile', 'build', 'build-sign', 'build-keeporig', 'sign', 'unsign'),
               'br': ('compress', 'decompress'), 'dat': ('to-image', 'to-folder')}
    parser = Parser(add_help=False, allow_abbrev=False)
    parser.add_argument('action', choices=choices[namespace])
    parser.add_argument('path')
    parser.add_argument('--json', action='store_true')
    parser.add_argument('--timeout',type=float)
    parser.add_argument('--no-wait',action='store_true')
    if namespace in ('archive', 'payload', 'super'):
        parser.add_argument('--to')
    if namespace == 'archive':
        parser.add_argument('--path', dest='members', action='append', default=[])
    if namespace in ('payload', 'super'):
        group = parser.add_mutually_exclusive_group()
        group.add_argument('--all', action='store_true')
        group.add_argument('--partition', dest='partitions', action='append', default=[])
    options = vars(parser.parse_args(tokens))
    options['namespace'] = namespace
    if options['timeout'] is not None and (not math.isfinite(options['timeout']) or options['timeout']<=0):
        raise ValueError('--timeout must be a positive finite number')
    if options['action'] == 'ls':options['action'] = 'list'
    action = options['action']
    if action in ('extract', 'pack') and not options.get('to'):
        raise ValueError('--to <destination> is required')
    if action == 'extract' and namespace in ('payload', 'super') and not (options['all'] or options['partitions']):
        raise ValueError('Select --all or repeat --partition <name>')
    if action == 'list' and (options.get('to') or options.get('members') or options.get('all') or options.get('partitions')):
        raise ValueError('Extraction options cannot be used with list')
    if action == 'pack' and options.get('members'):
        raise ValueError('--path is an extraction option')
    for key in ('path', 'to'):
        if options.get(key):options[key] = os.path.abspath(os.path.join(cwd, options[key]))
    return options
