# SPDX-License-Identifier: MPL-2.0
"""JSON interface to the same Binary Lab engine and sessions as the GUI."""
import argparse
import json
import logging
import os

from core import binary_core, binary_session, so_core
from core.command_context import CommandContext, command_scope, failure_from_exception


def run(arguments):
    parser = argparse.ArgumentParser(prog='rak binary', description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('engines', help='Show local engine availability')
    configure = commands.add_parser('configure-ghidra', help='Choose an existing Ghidra installation')
    configure.add_argument('folder')
    for name in ('inspect', 'functions', 'function', 'notes', 'annotate'):
        child = commands.add_parser(name)
        child.add_argument('file', help='Binary path; relative to the current directory')
        if name == 'functions': child.add_argument('--ghidra', action='store_true', help='Discover functions with Ghidra; reuse cached analysis')
        if name in ('function', 'annotate'): child.add_argument('--address', required=True)
        if name == 'annotate':
            child.add_argument('--name', required=True)
            child.add_argument('--comment', required=True)
            child.add_argument('--revision', type=int, required=True, help='Revision returned by notes; stale saves fail')
    args = parser.parse_args(arguments)
    with command_scope(CommandContext('binary ' + args.command)):
        try:
            if args.command == 'engines':
                from core import native_backend
                module = native_backend._get_native()
                result = {'rust': bool(getattr(module, 'binary_analyze_json', None)), 'ghidra': binary_session.ghidra_status()}
            elif args.command == 'configure-ghidra': result = binary_session.configure_ghidra(args.folder)
            elif args.command == 'inspect': result = so_core.result_to_jsonable(binary_core.analyze_file(args.file))
            elif args.command == 'functions' and not args.ghidra: result = binary_core.analyze_file(args.file)['functions']
            else:
                session = binary_session.BinarySession(args.file)
                if args.command == 'notes': result = session.annotations()
                elif args.command == 'annotate': result = session.annotate(args.address, args.name, args.comment, args.revision)
                else:
                    index = session.analyze()
                    result = index if args.command == 'functions' else session.analyze(args.address)
            print(json.dumps({'ok': True, 'result': result}, ensure_ascii=False))
            return 0
        except Exception as error:
            failure = failure_from_exception(error)
            logging.getLogger(__name__).error('Binary Lab %s: %s', args.command, failure)
            if failure.application_error:
                from core.error_reporting import report_fatal
                report_fatal('Binary Lab ' + args.command + '\n\n' + str(failure), application_error=True)
            print(json.dumps({'ok': False, 'error': str(failure), 'application_error': failure.application_error}, ensure_ascii=False))
            return 1
