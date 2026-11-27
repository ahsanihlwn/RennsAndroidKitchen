# SPDX-License-Identifier: MPL-2.0
"""JSON interface to the same Binary Lab engine and sessions as the GUI."""
import argparse
import json
import logging
import os
from pathlib import Path

from core import binary_core, binary_session, binary_xref, native_decompiler, so_core
from core.command_context import CommandContext, command_scope, failure_from_exception


def _capstone_available():
    try:
        so_core._setup_capstone_path()
        import capstone  # noqa: F401
        return True
    except Exception:
        return False


def _build_paths(parser, *, required=False):
    parser.add_argument('--kernel-source', required=required, help='Source checkout for the original kernel build')
    database = parser.add_mutually_exclusive_group(required=required)
    database.add_argument('--compile-commands', help='Clang Kbuild compilation database')
    database.add_argument('--build-dir', help='Kbuild output containing compile_commands.json')


def _build_options(parser, *, required=False):
    _build_paths(parser, required=required)
    parser.add_argument('--emit-rust', action='store_true', help='Transpile compiled C units with C2Rust')
    parser.add_argument('--c2rust', help='C2Rust executable, if it is outside PATH')


def _compile_tree(manifest, args, progress=None, jobs=1):
    from core import kernel_rebuild
    return kernel_rebuild.compile_tree(
        manifest, args.kernel_source, args.compile_commands, emit_rust=args.emit_rust, c2rust=args.c2rust,
        vmlinux=args.vmlinux, progress=progress, jobs=jobs,
    )


def run(arguments, *, compile_progress=None, compile_jobs=1):
    parser = argparse.ArgumentParser(prog='rak binary', description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('engines', help='Show local engine availability')
    configure = commands.add_parser('configure-ghidra', help='Choose an existing Ghidra installation')
    configure.add_argument('folder')
    compile_tree = commands.add_parser('compile-tree', help='Compile a reconstructed tree with its Kbuild inputs')
    compile_tree.add_argument('manifest', help='Path to reconstruction_manifest.json')
    _build_options(compile_tree, required=True)
    compile_tree.add_argument('--vmlinux', help='Verified same-build vmlinux with DWARF source locations')
    kernel_check = commands.add_parser('kernel-check', help='Detect a kernel and check what its build still needs')
    kernel_check.add_argument('file', help='Android boot image or kernel binary')
    _build_options(kernel_check)
    trace = commands.add_parser('trace-compile', help='Trace Kbuild C through Clang, LLVM IR and object instructions')
    trace.add_argument('source_file', help='C path relative to the original kernel checkout')
    trace.add_argument('--function', required=True, help='C function name')
    trace.add_argument('--output', required=True, help='New folder for the compiler trace')
    trace.add_argument('--llvm-objdump', help='Path to the build toolchain llvm-objdump')
    _build_paths(trace, required=True)
    compare_boot = commands.add_parser('compare-boot', help='Compare Android boot images and their kernel payloads')
    compare_boot.add_argument('reference')
    compare_boot.add_argument('candidate')
    for name in ('inspect', 'functions', 'function', 'decompile', 'notes', 'annotate', 'strings', 'xref', 'disasm', 'search-bytes', 'rea-rpc', 'reconstruct-tree'):
        child = commands.add_parser(name)
        child.add_argument('file', help='Binary path; relative to the current directory')
        if name == 'reconstruct-tree':
            child.add_argument('--output', help='Destination folder for reconstructed source tree')
            selection = child.add_mutually_exclusive_group()
            selection.add_argument('--max-files', type=int, default=None, help='Limit number of files to reconstruct')
            selection.add_argument('--source-file', action='append', default=[], help='Reconstruct a source path found in the binary (repeat for multiple files)')
            child.add_argument('--system-map', help='Linux System.map for the same kernel build')
            child.add_argument('--btf', help='Raw BTF blob for the same kernel build')
            child.add_argument('--dwarf-reference', help='Same-build vmlinux for function source ownership')
            child.add_argument('--functions-per-file', type=int, default=5, help='Maximum functions per file (0 for all)')
            child.add_argument('--annotated-asm', action='store_true', help='Export annotated AArch64 listings')
            child.add_argument('--offline', action='store_true', help='Use local files and cached source references only')
            _build_options(child)
            child.add_argument('--vmlinux', help='Verified same-build vmlinux for the compile step')
        if name == 'functions':
            child.add_argument('--ghidra', action='store_true', help='Discover functions with Ghidra; reuse cached analysis')
            child.add_argument('--resident', action='store_true', help='Use the resident REA Ghidra bridge')
        if name in ('function', 'decompile', 'annotate'):
            child.add_argument('--address', required=True)
        if name in ('function', 'decompile'):
            child.add_argument('--resident', action='store_true', help='Use the resident REA Ghidra bridge')
            child.add_argument('--native', action='store_true', help='Use the built-in Ghidra C++ decompiler (no JVM); automatic when Ghidra is not installed')
        if name == 'annotate':
            child.add_argument('--name', required=True)
            child.add_argument('--comment', required=True)
            child.add_argument('--revision', type=int, required=True, help='Revision returned by notes; stale saves fail')
        if name == 'strings':
            child.add_argument('--query', default='')
            child.add_argument('--paths', action='store_true')
            child.add_argument('--limit', type=int, default=200)
        if name == 'xref':
            child.add_argument('--query', required=True)
        if name == 'disasm':
            target = child.add_mutually_exclusive_group(required=True)
            target.add_argument('--section')
            target.add_argument('--address')
            child.add_argument('--bytes', type=int, default=512)
        if name == 'search-bytes':
            child.add_argument('--pattern', required=True)
            child.add_argument('--hex', action='store_true')
            child.add_argument('--limit', type=int, default=64)
        if name == 'rea-rpc':
            child.add_argument('--method', required=True)
            child.add_argument('--params', default='{}')
    args = parser.parse_args(arguments)
    if args.command == 'reconstruct-tree' and args.functions_per_file < 0:
        parser.error('--functions-per-file must be 0 or greater')
    if hasattr(args, 'build_dir'):
        if args.build_dir:
            args.compile_commands = str(Path(args.build_dir) / 'compile_commands.json')
    if args.command in ('reconstruct-tree', 'compile-tree', 'kernel-check'):
        if args.command == 'reconstruct-tree' and (args.compile_commands or args.emit_rust or args.vmlinux) and not (
            args.kernel_source and args.compile_commands
        ):
            parser.error('Compilation and Rust export require --kernel-source and --build-dir or --compile-commands. '
                         'Run rak binary kernel-check FILE for the requirements.')
        if args.c2rust and not args.emit_rust:
            parser.error('--c2rust requires --emit-rust')
    with command_scope(CommandContext('binary ' + args.command)):
        try:
            if args.command == 'engines':
                from core import native_backend
                module = native_backend._get_native()
                result = {
                    'rust': bool(getattr(module, 'binary_analyze_json', None)),
                    'capstone': _capstone_available(),
                    'ghidra': binary_session.ghidra_status(),
                    'native_decompiler': native_decompiler.status(),
                    'rea': binary_session.rea_status(),
                }
            elif args.command == 'configure-ghidra':
                result = binary_session.configure_ghidra(args.folder)
            elif args.command == 'compile-tree':
                result = _compile_tree(args.manifest, args, compile_progress, compile_jobs)
            elif args.command == 'trace-compile':
                from core import kernel_rebuild
                result = kernel_rebuild.trace_compile(
                    args.kernel_source, args.compile_commands, args.source_file, args.function, args.output,
                    objdump=args.llvm_objdump,
                )
            elif args.command == 'kernel-check':
                from core import kernel_rebuild, kernel_version
                info = kernel_version.inspect_kernel(args.file)
                if not info.is_kernel:
                    message = info.error or 'Kernel detection found no kernel in this file. Use boot.img, Image or vmlinux.'
                    logging.getLogger(__name__).info('Binary Lab kernel-check: %s', message)
                    print(json.dumps({'ok': False, 'error': message, 'application_error': False}, ensure_ascii=False))
                    return 1
                result = {
                    'kernel': info.to_dict(),
                    **kernel_rebuild.check_build(
                        args.kernel_source, args.compile_commands, expected_config=info.config_sha256,
                        emit_rust=args.emit_rust, c2rust=args.c2rust,
                    ),
                }
            elif args.command == 'compare-boot':
                from core.kernel_boot_compare import compare_boot
                result = compare_boot(args.reference, args.candidate)
            elif args.command == 'inspect':
                result = so_core.result_to_jsonable(binary_core.analyze_file(args.file))
            elif args.command == 'functions' and not args.ghidra:
                result = binary_core.analyze_file(args.file)['functions']
            elif args.command == 'strings':
                analysis = binary_core.analyze_file(args.file)
                if args.paths:
                    items = [p for p in analysis['path_like_strings'] if not args.query or args.query.lower() in p.lower()]
                    result = []
                    for p in items[:args.limit]:
                        hits = binary_xref.find_string_locations(args.file, analysis, p, limit=1)
                        if hits:
                            result.append(hits[0])
                else:
                    result = binary_xref.find_string_locations(args.file, analysis, args.query or '', limit=args.limit) if args.query else [
                        {
                            'value': e['value'],
                            'offset': e['offset'],
                            'va': hex(va) if va is not None else '',
                            'section': sec,
                        }
                        for e in analysis.get('string_entries', [])[:args.limit]
                        for va, sec in [binary_xref.offset_to_va(analysis, int(e['offset'], 16))]
                    ]
            elif args.command == 'xref':
                analysis = binary_core.analyze_file(args.file)
                session = binary_session.BinarySession(args.file, analysis['sha256'])
                result = session.find_xrefs(analysis, args.query)
            elif args.command == 'disasm':
                analysis = binary_core.analyze_file(args.file)
                if args.section:
                    text = binary_core.disassemble(args.file, analysis, args.section, args.bytes)
                else:
                    text = binary_core.disassemble_va(args.file, analysis, args.address, args.bytes)
                result = {'disassembly': text}
            elif args.command == 'search-bytes':
                analysis = binary_core.analyze_file(args.file)
                result = binary_xref.search_bytes(args.file, analysis, args.pattern, hex_mode=args.hex, limit=args.limit)
            elif args.command == 'reconstruct-tree':
                from core import binary_reconstruct
                result = binary_reconstruct.reconstruct_source_tree(
                    args.file, output_dir=args.output, max_files=args.max_files,
                    system_map=args.system_map, btf_path=args.btf, annotated_asm=args.annotated_asm,
                    dwarf_reference=args.dwarf_reference, max_funcs_per_file=args.functions_per_file or None,
                    local_source_dir=args.kernel_source, offline=args.offline, source_paths=args.source_file,
                )
                if args.compile_commands:
                    result['build'] = _compile_tree(result['manifest_path'], args)
            else:
                session = binary_session.BinarySession(args.file)
                if args.command == 'notes':
                    result = session.annotations()
                elif args.command == 'annotate':
                    result = session.annotate(args.address, args.name, args.comment, args.revision)
                elif args.command == 'rea-rpc':
                    params = json.loads(args.params)
                    if not isinstance(params, dict):
                        raise ValueError('--params must be a JSON object')
                    result = session.rea_rpc(args.method, params)
                elif args.command in ('decompile', 'function') and (args.native or (
                        not binary_session.ghidra_status()['available'] and native_decompiler.status()['available'])):
                    analysis = binary_core.analyze_file(args.file)
                    result = session.native_function(analysis, args.address)
                    if args.command == 'decompile':
                        result = {**result, 'annotations': session.annotations()}
                elif args.command == 'decompile':
                    fn = session.analyze_resident(args.address) if args.resident else (session.analyze(), session.analyze(args.address))[1]
                    result = {**fn, 'annotations': session.annotations()}
                elif getattr(args, 'resident', False):
                    result = session.analyze_resident() if args.command == 'functions' else session.analyze_resident(args.address)
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


if __name__ == '__main__':
    import sys
    sys.exit(run(sys.argv[1:]))
