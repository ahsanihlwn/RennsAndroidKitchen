# SPDX-License-Identifier: MPL-2.0
"""Verify recovered C in its Kbuild translation unit and export it through C2Rust."""
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import posixpath
import re
import shlex
import shutil
import tempfile

from core import binary_core, kernel_version, kitchen_process
from core.binary_semantics import TOKEN, restore_kernel_bugs
from core.command_context import OperationError
from core.kernel_btf import IDENTIFIER


ALIASES = {
    "byte": "unsigned char", "ushort": "unsigned short", "uint": "unsigned int",
    "ulong": "unsigned long", "longlong": "long long", "ulonglong": "unsigned long long",
    **{f"int{n}": signed for n, signed in zip((1, 2, 4, 8), ("signed char", "short", "int", "long long"))},
    **{
        f"{prefix}{n}": unsigned
        for prefix in ("uint", "undefined", "xunknown")
        for n, unsigned in zip((1, 2, 4, 8), ("unsigned char", "unsigned short", "unsigned int", "unsigned long long"))
    },
}
DROP_FLAGS = {"-c", "-MD", "-MMD", "-MP", "-MG"}
DROP_WITH_VALUE = {"-o", "-MF", "-MT", "-MQ", "-MJ", "--serialize-diagnostics"}
STRICT_C = ("-Werror=implicit-function-declaration", "-Werror=incompatible-pointer-types", "-Werror=int-conversion")
PRIMITIVE_TYPE = re.compile(
    r"(?:(?:const|volatile|restrict|signed|unsigned|short|long) +)*"
    r"(?:void|_Bool|char|short|int|long|float|double)"
    r"(?:\s*\*(?:\s*(?:const|volatile|restrict)\b)*)*\Z"
)
MEMBER_EQUALITY_BODY = re.compile(
    rb"\{\s*return\s+([A-Za-z_]\w*(?:\s*->\s*[A-Za-z_]\w*)+)\s*(==|!=)\s*"
    rb"([A-Za-z_]\w*(?:\s*->\s*[A-Za-z_]\w*)+)\s*;\s*\}\Z"
)


@dataclass(frozen=True)
class Unit:
    source: Path
    cwd: Path
    argv: tuple[str, ...]
    build_object: Path | None

    def command(self, *options):
        return [*self.argv, *options]


@dataclass(frozen=True)
class Definition:
    start: int
    end: int
    parameters: list
    node: dict


def _read_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise OperationError(f"Cannot read {path}: {error}") from error


def _write_json(path, value):
    _publish(path, (json.dumps(value, indent=2, ensure_ascii=False) + "\n").encode("utf-8"))


def _publish(path, content):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("wb", dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(content)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _fingerprint(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _units(database, kernel_source):
    records = _read_json(database)
    if not isinstance(records, list):
        raise OperationError("Clang compilation database must contain a JSON array")
    by_path = {}
    for row in records:
        if not isinstance(row, dict) or not all(isinstance(row.get(key), str) for key in ("directory", "file")):
            raise OperationError("Compilation database entry is missing directory or file")
        cwd = (database.parent / row["directory"]).resolve()
        original = cwd / row["file"]
        source = original.resolve()
        if not source.is_relative_to(kernel_source) or source.suffix != ".c":
            continue
        path = source.relative_to(kernel_source).as_posix()
        by_path.setdefault(path, []).append((source, cwd, row))
    return by_path


def _prepare_unit(entries, path):
    if not entries:
        raise OperationError(f"No Kbuild command for {path}")
    units = []
    for source, cwd, row in entries:
        args = row.get("arguments")
        if args is None and isinstance(row.get("command"), str):
            args = shlex.split(row["command"], posix=True)
        if not isinstance(args, list) or not args or not all(isinstance(arg, str) and arg for arg in args):
            raise OperationError(f"Invalid compiler arguments for {path}")
        compiler_index = 1 if Path(args[0]).stem in {"ccache", "sccache"} else 0
        if compiler_index >= len(args) or not re.fullmatch(
            r"(?:[\w.-]+-)?clang(?:-[0-9]+)?(?:\.exe)?", Path(args[compiler_index]).name
        ):
            raise OperationError(f"{path} requires a Clang Kbuild command")
        compiler = args[compiler_index]
        argv = [str((cwd / compiler).resolve()) if "/" in compiler or "\\" in compiler else compiler]
        pending = None
        build_object = (cwd / row["output"]).resolve() if isinstance(row.get("output"), str) else None
        found_source = False
        for arg in args[compiler_index + 1:]:
            if pending:
                if pending == "-o":
                    build_object = (cwd / arg).resolve()
                pending = None
                continue
            if arg in DROP_WITH_VALUE:
                pending = arg
            elif arg in DROP_FLAGS or arg.startswith(("-Wp,-MMD,", "-Wp,-MD,", "-MF", "-MT", "-MQ", "-MJ")):
                continue
            elif arg in {"-S", "-E", "-M", "-MM", "-fsyntax-only", "--"} or arg.startswith((
                "@", "-save-temps", "-ftime-trace", "-fprofile-", "-fstack-usage", "--output=", "-ivfsoverlay",
            )):
                raise OperationError(f"Unsupported build action or output option in {path}: {arg}")
            elif not arg.startswith("-") and arg.endswith(".c") and (cwd / arg).resolve() == source:
                if found_source:
                    raise OperationError(f"Kbuild command repeats its input for {path}")
                argv.append(arg)
                found_source = True
            else:
                argv.append(arg)
        if pending or not found_source or "-c" not in args:
            raise OperationError(f"Expected one compile-only Kbuild command for {path}")
        units.append(Unit(source, cwd, tuple(argv), build_object))
    if len(set(units)) != 1:
        raise OperationError(f"Multiple distinct build commands for {path}")
    return units[0]


def _build_config(cwd, expected_config):
    config = cwd / ".config"
    autoconf = cwd / "include/generated/autoconf.h"
    missing = [str(path) for path in (config, autoconf) if not path.is_file()]
    if missing:
        raise OperationError(
            f"Missing Kbuild inputs: {', '.join(missing)}. "
            "Prepare the output directory with the original kernel configuration and build recipe."
        )
    digest = kernel_version.kernel_config_digest(config.read_text(encoding="utf-8"))
    return {
        "config_sha256": digest,
        "autoconf_sha256": _fingerprint(autoconf),
        "config_matches_embedded": digest == expected_config if expected_config else None,
    }


def _require_build_config(cwd, config):
    if not config["config_sha256"] or config["config_matches_embedded"] is False:
        raise OperationError(
            f"Kbuild config is empty or differs from embedded IKCONFIG in {cwd}. "
            "Use the configuration from the original build."
        )


def _c2rust_executable(path):
    executable = shutil.which(path or "c2rust")
    return str(Path(executable).resolve()) if executable else None


def check_build(kernel_source, compile_commands, *, expected_config=None, emit_rust=False, c2rust=None):
    """Check local build inputs without running commands from the database."""
    checks = []

    def add(name, available, detail):
        checks.append({"requirement": name, "available": available, "detail": detail})

    source = Path(kernel_source).resolve() if kernel_source else None
    database = Path(compile_commands).resolve() if compile_commands else None
    source_present = source is not None and source.is_dir()
    database_present = database is not None and database.is_file()
    add("kernel_source", source_present, str(source) if source_present else
        "Pass --kernel-source with the full source checkout used to build the kernel.")
    add("compile_commands", database_present, str(database) if database_present else
        "Generate compile_commands.json from the configured build, then pass --build-dir or --compile-commands.")
    if source_present and database_present:
        try:
            records = _units(database, source)
        except OperationError as error:
            add("compilation_database_entries", False, str(error))
        else:
            samples = {}
            for path, entries in records.items():
                for entry in entries:
                    samples.setdefault(entry[1], (path, entry))
            add("compilation_database_entries", bool(samples), f"{len(records)} mapped C source paths." if samples else
                "The checkout has no matching C paths in the database. Regenerate it in this Linux/WSL workspace.")
            for cwd, (path, entry) in sorted(samples.items()):
                try:
                    config = _build_config(cwd, expected_config)
                    _require_build_config(cwd, config)
                except (OperationError, OSError, UnicodeError) as error:
                    add("kbuild_config", False, str(error))
                else:
                    verification = (
                        "matches embedded IKCONFIG" if config["config_matches_embedded"]
                        else "IKCONFIG unavailable for comparison"
                    )
                    add("kbuild_config", True, f"{cwd} ({verification})")
                try:
                    unit = _prepare_unit([entry], path)
                except (OperationError, ValueError) as error:
                    add("clang", False, str(error))
                else:
                    compiler = shutil.which(unit.argv[0])
                    if not unit.source.is_file():
                        add("clang", False, f"Source is missing at {unit.source}. Restore the full checkout.")
                    else:
                        add("clang", compiler is not None, compiler or
                            f"Clang is unavailable at {unit.argv[0]}. Restore the toolchain and run in the build environment.")
    if emit_rust:
        executable = _c2rust_executable(c2rust)
        add("c2rust", executable is not None, executable or
            "Install C2Rust in the build environment, add it to PATH or pass --c2rust.")
    return {"ready": all(check["available"] for check in checks), "checks": checks}


def _run(argv, cwd, label, *, input_text=None):
    try:
        process = kitchen_process.run(
            argv, cwd=cwd, capture_output=True, input=input_text, text=True,
            encoding="utf-8", errors="replace", timeout=300,
        )
    except kitchen_process.TimeoutExpired as error:
        raise OperationError(f"{label} timed out\n{error.stdout or ''}\n{error.stderr or ''}") from error
    except OSError as error:
        raise OperationError(f"Cannot start {label}: {error}") from error
    if process.returncode:
        raise OperationError(f"{label} failed (exit {process.returncode})\n{process.stdout}{process.stderr}")
    return process.stdout


def _llvm_tool(unit, name):
    compiler = Path(shutil.which(unit.argv[0]) or unit.argv[0])
    sibling = compiler.with_name(name + compiler.suffix if compiler.suffix == ".exe" else name)
    tool = shutil.which(str(sibling)) or shutil.which(name)
    if not tool:
        raise OperationError(f"Install {name} from the kernel build's LLVM toolchain.")
    return tool


def _dwarf_addresses(vmlinux, data, unit):
    if not vmlinux.is_file() or binary_core.identify(vmlinux) != "ELF":
        raise OperationError("Choose the unstripped ELF vmlinux from the original kernel build.")
    expected = data.get("sha256", "")
    if not isinstance(expected, str) or not re.fullmatch(r"[a-f0-9]{64}", expected):
        raise OperationError("The reconstruction manifest has no valid kernel SHA-256.")
    kind = data.get("format")
    if kind == "ELF":
        if _fingerprint(vmlinux) != expected:
            raise OperationError("vmlinux SHA-256 differs from the reconstructed kernel.")
        return 0, None, 0
    if kind not in {"PE", "ARM64 Image"} or data.get("architecture") not in {"ARM64", "AARCH64"}:
        raise OperationError("DWARF source matching requires an ELF kernel or an ARM64 Image.")

    with tempfile.TemporaryDirectory(prefix="rak-kernel-image-") as folder:
        image = Path(folder) / "Image"
        objcopy = _llvm_tool(unit, "llvm-objcopy")
        _run([objcopy, "-O", "binary", "-R", ".note", "-R", ".note.gnu.build-id",
              "-R", ".comment", "-S", str(vmlinux), str(image)], unit.cwd, "Extract Image from vmlinux")
        if _fingerprint(image) != expected:
            raise OperationError("Image SHA-256 extracted from vmlinux differs from the reconstructed kernel.")
        size = image.stat().st_size
        if kind == "PE":
            readobj = _llvm_tool(unit, "llvm-readobj")
            headers = _run([readobj, "--file-headers", "--sections", str(image)], unit.cwd,
                           "Read the verified Image's EFI code mapping")
            image_base = re.search(r"^  ImageBase: (0x[0-9a-fA-F]+)$", headers, re.M)
            sections = re.findall(r"^  Section \{\n(.*?)^  \}", headers, re.M | re.S)
            code = next((block for block in sections if re.search(r"^    Name: \.text( |$)", block, re.M)), "")
            va = re.search(r"^    VirtualAddress: (0x[0-9a-fA-F]+)$", code, re.M)
            raw = re.search(r"^    PointerToRawData: (0x[0-9a-fA-F]+)$", code, re.M)
            if not (image_base and va and raw):
                raise OperationError("The verified EFI Image has no .text file mapping.")
            legacy_base = int(image_base[1], 16) + int(va[1], 16) - int(raw[1], 16)
        else:
            legacy_base = binary_core.RAW_ARM64_BASE
    nm = _llvm_tool(unit, "llvm-nm")
    symbols = _run([nm, "--defined-only", "--format=posix", str(vmlinux)], unit.cwd, "Read _text from vmlinux")
    addresses = {int(value, 16) for value in re.findall(r"^_text [Tt] ([0-9a-fA-F]+)(?: |$)", symbols, re.M)}
    if len(addresses) != 1:
        raise OperationError("vmlinux needs a unique _text address for Image source matching.")
    return addresses.pop(), size, legacy_base


def _dwarf_source_path(location, source_root, units):
    matched = re.fullmatch(r"(.+):[1-9][0-9]*:[0-9]+", location)
    if not matched:
        return None
    path = posixpath.normpath(matched[1])
    source = Path(path)
    if source.is_relative_to(source_root):
        path = source.relative_to(source_root).as_posix()
    elif source_root.name == "common":
        if path.startswith("common/"):
            path = path.removeprefix("common/")
        elif "/common/" in path:
            path = path.split("/common/", 1)[1]
    return path if path in units else None


def _dwarf_compile_rows(vmlinux, data, recovered, units, source_root):
    """Resolve real C owners using the outer DWARF frame of the verified kernel."""
    if not units:
        raise OperationError("The compilation database has no C units for this kernel checkout.")
    path, entries = next(iter(units.items()))
    unit = _prepare_unit(entries[:1], path)
    text, image_size, legacy_base = _dwarf_addresses(vmlinux, data, unit)
    queries = {}
    for row in recovered:
        for fn in row["recovered_functions"]:
            address = int(fn["address"], 16)
            if image_size is None:
                absolute = address
            else:
                base = text if data["format"] == "ARM64 Image" and text <= address < text + image_size else legacy_base
                offset = int(fn["file_offset"], 16) if "file_offset" in fn else address - base
                if not 0 <= offset < image_size:
                    continue
                absolute = text + offset
            queries[fn["name"], fn["address"]] = absolute
    if not queries:
        raise OperationError("The manifest has no function addresses within the verified kernel.")
    symbolizer = _llvm_tool(unit, "llvm-symbolizer")
    frames = _run([symbolizer, f"--obj={vmlinux}", "--inlines", "--functions=linkage", "--no-demangle"],
                  unit.cwd, "Read original kernel function locations",
                  input_text="".join(f"{address:#x}\n" for address in queries.values()))
    blocks = frames.strip().split("\n\n")
    if len(blocks) != len(queries):
        raise OperationError("llvm-symbolizer returned an incomplete set of function locations.")
    owners = {}
    for key, block in zip(queries, blocks):
        name, _ = key
        lines = block.splitlines()
        if len(lines) >= 2 and len(lines) % 2 == 0 and lines[-2] in {name, name + ".cfi"}:
            source = _dwarf_source_path(lines[-1], source_root, units)
            if source:
                owners[key] = source
    if not owners:
        raise OperationError("vmlinux DWARF did not identify matching C functions in this compilation database.")

    grouped = {}
    moved = []
    for row in recovered:
        origin = row["path"]
        for fn in row["recovered_functions"]:
            key = (fn["name"], fn["address"])
            owner = owners.get(key, origin)
            variants = grouped.setdefault(owner, {}).setdefault(key, [])
            # __bug_table names the trap site, which may be in a different
            # file from the function's own definition after inlining.
            if owner != origin:
                fn = {field: value for field, value in fn.items() if field != "bug_sites"}
                moved.append({"function": fn["name"], "address": fn["address"], "from": origin, "to": owner})
            for index, existing in enumerate(variants):
                if existing["code"] == fn["code"]:
                    if owner == origin:
                        variants[index] = fn
                    break
            else:
                variants.append(fn)
    rows = [
        {"path": path, "recovered_functions": [fn for variants in functions.values() for fn in variants]}
        for path, functions in grouped.items()
    ]
    return rows, {"vmlinux": str(vmlinux), "kernel_sha256": data["sha256"],
                  "resolved_functions": len(owners), "reassignments": moved}


def _ast_definition(unit, name, overlay=None):
    options = ["-fsyntax-only", "-Xclang", "-ast-dump=json", "-Xclang", "-ast-dump-filter", "-Xclang", name]
    if overlay:
        options.extend(("-ivfsoverlay", str(overlay)))
    output = _run(unit.command(*options), unit.cwd, f"Clang AST for {name}")
    decoder = json.JSONDecoder()
    definitions = []
    cursor = 0
    while cursor < len(output):
        while cursor < len(output) and output[cursor].isspace():
            cursor += 1
        if cursor == len(output):
            break
        # Clang 12 prefixes each filtered JSON declaration with a plain-text heading.
        if output.startswith("Dumping ", cursor):
            ending = output.find("\n", cursor)
            if ending >= 0 and output[ending - 1] == ":":
                cursor = ending + 1
                continue
        try:
            node, cursor = decoder.raw_decode(output, cursor)
        except ValueError as error:
            raise OperationError(f"Invalid Clang AST for {name}\n{output}") from error
        loc = node.get("loc", {})
        if node.get("kind") != "FunctionDecl" or node.get("name") != name or not loc.get("file"):
            continue
        if (unit.cwd / loc["file"]).resolve() != unit.source:
            continue
        children = node.get("inner", ())
        body = next((child for child in children if child["kind"] == "CompoundStmt"), None)
        if body is None or node.get("variadic"):
            continue
        begin, end = (body["range"][edge] for edge in ("begin", "end"))
        if "offset" in begin and "offset" in end and "tokLen" in end:
            parameters = [
                (arg.get("name", ""), arg["type"].get("desugaredQualType", arg["type"]["qualType"]))
                for arg in children if arg["kind"] == "ParmVarDecl"
            ]
            definitions.append(Definition(begin["offset"], end["offset"] + end["tokLen"], parameters, node))
    if len(definitions) != 1:
        raise OperationError(f"No unique active function definition for {name} in {unit.source}")
    return definitions[0]


def _normalized_type(text):
    for word in ("const", "volatile", "restrict"):
        text = re.sub(rf"\b{word}\b", "", text)
    return re.sub(r"\s+", "", text)


def _leading_zero_count(variable, bits):
    builtin = "__builtin_clzll" if bits == 64 else "__builtin_clz"
    unsigned = {8: "unsigned char", 16: "unsigned short", 32: "unsigned int", 64: "unsigned long long"}[bits]
    count = f"{builtin}(({unsigned}){variable})"
    if bits < 32:
        count += f" - {32 - bits}"
    return f"({variable} ? {count} : {bits})"


def _function_body(name, code, parameters):
    tokens = [match for match in TOKEN.finditer(code) if not match[0].startswith(("//", "/*"))]
    words = [token[0] for token in tokens]
    try:
        opened = words.index("(")
        closed = words.index(")", opened)
    except ValueError as error:
        raise OperationError(f"No C-like function signature recovered for {name}") from error
    if not opened or words[opened - 1] != name or words[closed + 1:closed + 2] != ["{"] or words[-1:] != ["}"]:
        raise OperationError(f"Recovered definition does not have a simple signature and body for {name}")
    depth = 0
    for index in range(closed + 1, len(words)):
        depth += int(words[index] == "{") - int(words[index] == "}")
        if depth == 0 and index != len(words) - 1:
            raise OperationError(f"Unexpected tokens after recovered body of {name}")
    if depth:
        raise OperationError(f"Unbalanced recovered body for {name}")
    captured = " ".join(words[opened + 1:closed])
    recovered = [] if captured in ("", "void") else [item.split() for item in captured.split(",")]
    if len(recovered) != len(parameters):
        raise OperationError(f"Parameter count differs for {name}")
    aliases = []
    replacements = {}
    widths = {}
    body_names = set(words[closed + 2:-1])
    seen = set(words) | {param for param, _ in parameters}
    for index, (declaration, (param, source_type)) in enumerate(zip(recovered, parameters), 1):
        if len(declaration) < 2 or not IDENTIFIER.fullmatch(declaration[-1]) or not IDENTIFIER.fullmatch(param):
            raise OperationError(f"Unsupported parameter declaration in {name}")
        kind = " ".join(ALIASES.get(part, part) for part in declaration[:-1])
        if not all(part == "*" or IDENTIFIER.fullmatch(part) for part in declaration[:-1]):
            raise OperationError(f"Unsupported parameter type in {name}: {kind}")
        recovered_name = declaration[-1]
        if recovered_name not in body_names:
            continue
        if len(declaration) == 2:
            fixed_width = re.fullmatch(r"(?:u?int|undefined|xunknown)([1248])", declaration[0])
            if fixed_width:
                widths[recovered_name] = int(fixed_width[1]) * 8
        if recovered_name == param and _normalized_type(kind) == _normalized_type(source_type):
            continue
        if any(mark in source_type for mark in ("(", "[", "_Atomic")):
            raise OperationError(f"Unsupported Kbuild parameter type in {name}: {source_type}")
        alias = f"rak_recovered_arg_{index}"
        while alias in seen:
            alias += "_"
        seen.add(alias)
        cast = f"({kind})"
        if ("*" in kind) != ("*" in source_type):
            cast += "(__UINTPTR_TYPE__)"
        aliases.append(f"    {kind} {alias} = {cast}{param};")
        replacements[recovered_name] = alias
    start = tokens[closed + 1].end()
    end = tokens[-1].start()
    body = code[start:end]
    edits = []
    consumed = closed + 2
    for index in range(closed + 2, len(tokens) - 1):
        if index < consumed:
            continue
        token = tokens[index]
        if words[index:index + 2] == ["LZCOUNT", "("] and words[index + 3:index + 4] == [")"]:
            variable = words[index + 2]
            if variable in widths:
                intrinsic = _leading_zero_count(replacements.get(variable, variable), widths[variable])
                edits.append((token.start(), tokens[index + 3].end(), intrinsic))
                consumed = index + 4
                continue
        previous = words[index - 1]
        if previous in {".", "->", "goto"} or (
            previous in {"{", "}", ";", ":"} and words[index + 1] == ":"
        ):
            continue
        replacement = replacements.get(token[0], ALIASES.get(token[0]))
        if replacement:
            edits.append((token.start(), token.end(), replacement))
    for a, b, replacement in reversed(edits):
        body = body[:a - start] + replacement + body[b - start:]
    if aliases:
        body = "\n" + "\n".join(aliases) + "\n" + body
    return ("{" + body + "}").encode("utf-8")


def _fit_source_span(original, body):
    """Preserve offsets for later Clang source locations when the body fits."""
    if original == body:
        return body
    if MEMBER_EQUALITY_BODY.fullmatch(body) and MEMBER_EQUALITY_BODY.fullmatch(original):
        if re.sub(rb"\s+", b"", body) == re.sub(rb"\s+", b"", original):
            return original
    if b"\r" in original or b"\r" in body or body.count(b"\n") > original.count(b"\n"):
        return None
    prefix = body[:-1].rstrip()
    if prefix.endswith((b"\\", b"??/")):
        return None
    newlines = original.count(b"\n") - prefix.count(b"\n")
    spaces = len(original) - len(prefix) - newlines - 1
    closing_column = len(original.rsplit(b"\n", 1)[-1]) - 1
    if newlines:
        if spaces < closing_column:
            return None
        return prefix + b" " * (spaces - closing_column) + b"\n" * newlines + b" " * closing_column + b"}"
    if spaces >= 0 and len(prefix.rsplit(b"\n", 1)[-1]) + spaces == closing_column:
        return prefix + b" " * spaces + b"}"
    return None


def _overlay(path, files):
    _write_json(path, {
        "version": 0, "use-external-names": False,
        "roots": [
            {"type": "file", "name": original.as_posix(), "external-contents": replacement.as_posix()}
            for original, replacement in files
        ],
    })


def _safe_output(root, relative):
    path = root / relative
    if path.resolve() != path or not path.is_relative_to(root):
        raise OperationError(f"Invalid generated file path: {relative}")
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def trace_compile(kernel_source, compile_commands, source_file, function, output, *, objdump=None):
    """Capture the original Kbuild command at each compiler stage for a C unit."""
    if not IDENTIFIER.fullmatch(function):
        raise OperationError(f"Invalid C function name: {function}")
    source_root = Path(kernel_source).resolve()
    database = Path(compile_commands).resolve()
    relative = Path(source_file).as_posix()
    unit = _prepare_unit(_units(database, source_root).get(relative), relative)
    config = _build_config(unit.cwd, None)
    _require_build_config(unit.cwd, config)
    output = Path(output).resolve()
    if output.exists():
        raise OperationError(f"Choose a new output directory for this compiler trace: {output}")

    artifacts = {}
    with tempfile.TemporaryDirectory(prefix="rak-compiler-trace-") as folder:
        staging = Path(folder)
        dump = _run(unit.command(
            "-Xclang", "-ast-dump", "-Xclang", "-ast-dump-filter", "-Xclang", function, "-fsyntax-only",
        ), unit.cwd, "Clang AST")
        if not re.search(rf"\bFunctionDecl\b[^\n]*\b{re.escape(function)}\b", dump):
            raise OperationError(f"Clang AST does not contain the function {function} in {relative}")
        artifacts["ast"] = f"{function}.ast.txt"
        (staging / artifacts["ast"]).write_text(dump, encoding="utf-8")
        for kind, suffix, options in (
            ("preprocessed", ".i", ("-E",)),
            ("llvm_ir", ".ll", ("-S", "-emit-llvm")),
            ("object", ".o", ("-c",)),
        ):
            artifacts[kind] = function + suffix
            _run(unit.command(*options, "-o", str(staging / artifacts[kind])), unit.cwd, f"Clang {kind}")
        obj = staging / artifacts["object"]
        with obj.open("rb") as stream:
            header = stream.read(20)
        if header.startswith(b"\x7fELF"):
            object_format = "ELF"
            artifacts["assembly"] = f"{function}.s"
            _run(unit.command("-S", "-o", str(staging / artifacts["assembly"])), unit.cwd, "Clang target assembly")
            compiler = Path(unit.argv[0])
            sibling = compiler.with_name("llvm-objdump.exe" if compiler.suffix == ".exe" else "llvm-objdump")
            executable = shutil.which(objdump) if objdump else shutil.which(str(sibling)) or shutil.which("llvm-objdump")
            if not executable:
                raise OperationError("Install llvm-objdump from the build toolchain or pass --llvm-objdump.")
            disassembly = _run([executable, "-dr", f"--disassemble-symbols={function}", str(obj)], unit.cwd, "LLVM object disassembly")
            artifacts["disassembly"] = f"{function}.objdump.txt"
            (staging / artifacts["disassembly"]).write_text(disassembly, encoding="utf-8")
            if f"<{function}>:" not in disassembly:
                raise OperationError(f"{function} has no object symbol. Check if it was inlined or eliminated.")
            if header[:6] == b"\x7fELF\x02\x01" and header[18:20] == b"\xb7\x00":
                words = re.findall(
                    r"^\s*[0-9a-f]+:[ \t]+([0-9a-f]{8}|[0-9a-f]{2}(?:[ \t]+[0-9a-f]{2}){3})[ \t]",
                    disassembly, re.M,
                )
                if words:
                    artifacts["aarch64_words"] = f"{function}.words.txt"
                    lines = ["word_hex bits bytes_little_endian"]
                    for word in words:
                        value = int(word, 16) if len(word) == 8 else int.from_bytes(bytes.fromhex(word), "little")
                        lines.append(f"{value:08x} {value:032b} {value.to_bytes(4, 'little').hex(' ')}")
                    (staging / artifacts["aarch64_words"]).write_text("\n".join(lines) + "\n", encoding="ascii")
        elif header.startswith((b"BC\xc0\xde", b"\xde\xc0\x17\x0b")):
            object_format = "LLVM bitcode. Inspect linked vmlinux for target instructions."
        else:
            raise OperationError("Clang produced an unsupported kernel object format.")
        report = {
            "source": relative, "source_sha256": _fingerprint(unit.source),
            "function": function, "directory": str(unit.cwd), "compile_args": list(unit.argv),
            "compiler": _run([unit.argv[0], "--version"], unit.cwd, "Clang version").splitlines()[0],
            "object_format": object_format, "artifacts": artifacts, **config,
        }
        output.mkdir(parents=True, exist_ok=False)
        for name in artifacts.values():
            shutil.copyfile(staging / name, _safe_output(output, name))
        _write_json(_safe_output(output, "trace-report.json"), report)
    return {**report, "output": str(output)}


def _rust(unit, overlay, staging, executable, names):
    database = staging / "compile_commands.json"
    out = staging / "rust"
    out.mkdir()
    options = ["-ivfsoverlay", str(overlay)] if overlay else []
    # Clang 12 required an opt-in for zero initialization. C2Rust's newer
    # Clang accepts -ftrivial-auto-var-init=zero without that obsolete opt-in.
    arguments = [arg for arg in unit.command(*options, "-c")
                 if arg != "-enable-trivial-auto-var-init-zero-knowing-it-will-be-removed-from-clang"]
    _write_json(database, [{
        "directory": str(unit.cwd), "file": str(unit.source), "arguments": arguments,
    }])
    _run([executable, "transpile", "--fail-on-error", "--preserve-unused-functions", "--emit-no-std",
          "--output-dir", str(out), str(database)],
         staging, f"C2Rust for {unit.source.name}")
    matches = list(out.rglob(unit.source.stem.replace("-", "_") + ".rs"))
    if len(matches) != 1:
        raise OperationError(f"C2Rust did not generate a unique Rust source for {unit.source.name}")
    rust = matches[0].read_text(encoding="utf-8")
    missing = [name for name in names if not re.search(rf"\bfn\s+(?:r#)?{re.escape(name)}\s*\(", rust)]
    if missing:
        raise OperationError(f"C2Rust omitted recovered functions: {', '.join(missing)}")
    return rust


def _scalar_aliases(node):
    aliases = {}
    pending = [node]
    while pending:
        item = pending.pop()
        kind = item.get("type", {})
        spelling, canonical = kind.get("qualType", ""), kind.get("desugaredQualType", "")
        if IDENTIFIER.fullmatch(spelling) and canonical != spelling and PRIMITIVE_TYPE.fullmatch(canonical):
            if spelling in aliases and aliases[spelling] != canonical:
                raise OperationError(f"Multiple definitions of C type {spelling}")
            aliases[spelling] = canonical
        pending.extend(item.get("inner", ()))
    return aliases


def _member_comparison_layout(unit, overlay, staging, definition, body):
    match = MEMBER_EQUALITY_BODY.fullmatch(body)
    if not match:
        return None
    left, right = [re.sub(rb"\s+", b"", match[index]).decode("ascii").split("->") for index in (1, 3)]
    if len(left) != 2 or len(right) != 2 or left[1] != right[1]:
        return None
    parameters = dict(definition.parameters)
    kind = parameters.get(left[0], "")
    if kind != parameters.get(right[0]) or any(
        name not in {left[0], right[0]} and not PRIMITIVE_TYPE.fullmatch(value)
        for name, value in parameters.items()
    ):
        return None
    tag = re.fullmatch(r"(?:const )?struct ([A-Za-z_]\w*)\s*\*", kind)
    if not tag:
        return None
    record, field = tag[1], left[1]
    text = _run(unit.command(
        "-ivfsoverlay", str(overlay), "-Xclang", "-fdump-record-layouts", "-c", "-o", str(staging / "layout.o"),
    ), unit.cwd, f"Read the Kbuild layout of {record}")
    records = re.findall(
        rf"^[ \t]*0 \| struct {re.escape(record)}\n([\s\S]*?)^[ \t]*\| \[sizeof=(\d+), align=(\d+)\]",
        text, re.M,
    )
    if len(records) != 1:
        return None
    fields, size, alignment = records[0]
    member = re.search(rf"^[ \t]*(\d+) \|   (\S.*?)\s+{re.escape(field)}$", fields, re.M)
    if not member or not member[2].endswith("*") or int(member[1]) >= int(size):
        return None
    offset = int(member[1])
    prefix = f"unsigned char rak_padding_prefix[{offset}]; " if offset else ""
    declaration = (
        f"struct {record} {{ {prefix}void *{field}; "
        f"unsigned char rak_padding_suffix[{size} - {offset} - sizeof(void *)]; }};\n"
        f'_Static_assert(sizeof(struct {record}) == {size}, "Kbuild record size");\n'
        f'_Static_assert(__alignof__(struct {record}) == {alignment}, "Kbuild record alignment");\n'
        f'_Static_assert(__builtin_offsetof(struct {record}, {field}) == {offset}, "Kbuild field offset");'
    )
    return record, declaration


def _selected_rust_unit(unit, content, overlay, staging, names):
    source = staging / unit.source.name
    typedefs = {}
    layouts = {}
    prototypes = []
    functions = []
    for name in names:
        fn = _ast_definition(unit, name, overlay=overlay)
        aliases = _scalar_aliases(fn.node)
        for alias, kind in aliases.items():
            if alias in typedefs and typedefs[alias] != kind:
                raise OperationError(f"Conflicting declaration types for {alias}")
            typedefs[alias] = kind
        function_type = fn.node["type"]["qualType"]
        return_type = function_type.split("(", 1)[0].strip()
        return_type = aliases.get(return_type, return_type)
        if "__attribute__" in function_type or not PRIMITIVE_TYPE.fullmatch(return_type) or any(
            not IDENTIFIER.fullmatch(param) for param, _ in fn.parameters
        ):
            raise OperationError(f"C2Rust needs the full kernel declarations for {name}")
        body = content[fn.start:fn.end]
        if any(not PRIMITIVE_TYPE.fullmatch(kind) for _, kind in fn.parameters):
            layout = _member_comparison_layout(unit, overlay, staging, fn, body)
            if layout is None:
                raise OperationError(f"C2Rust needs the full kernel declarations for {name}")
            record, declaration = layout
            if layouts.setdefault(record, declaration) != declaration:
                raise OperationError(f"C2Rust needs consistent layout declarations for {record}")
        parameters = ", ".join(f"{kind} {param}" for param, kind in fn.parameters) or "void"
        storage = "static " if fn.node.get("storageClass") == "static" else ""
        signature = f"{storage}{return_type} {name}({parameters})"
        prototypes.append(signature + ";")
        functions.append(f"{signature}\n{body.decode('utf-8')}")

    declarations = "\n".join(f"typedef {kind} {alias};" for alias, kind in sorted(typedefs.items()))
    source.write_text("\n\n".join((declarations, *layouts.values(), "\n".join(prototypes), "\n\n".join(functions))) + "\n",
                      encoding="utf-8")
    argv = [unit.argv[0]]
    skip = False
    for arg in unit.argv[1:]:
        if skip:
            skip = False
        elif arg in ("-include", "-imacros"):
            skip = True
        elif arg.endswith(".c") and not arg.startswith("-") and (unit.cwd / arg).resolve() == unit.source:
            argv.append(str(source))
        else:
            argv.append(arg)
    selected = Unit(source, unit.cwd, (*argv, "-Wno-unused-function"), None)
    _run(selected.command(*STRICT_C, "-c", "-o", str(staging / "selected.o")), unit.cwd,
         f"Compile isolated functions for C2Rust in {unit.source.name}")
    return selected


def _compile_unit(row, entries, root, expected_config, executable):
    functions = row["recovered_functions"]
    relative = row["path"]
    outcome = {"path": relative}
    published = None
    try:
        unit = _prepare_unit(entries, relative)
        outcome.update(_build_config(unit.cwd, expected_config))
        _require_build_config(unit.cwd, outcome)
        original = unit.source.read_bytes()
        edits = []
        names = []
        for function in functions:
            name = function["name"]
            if not IDENTIFIER.fullmatch(name) or name in names:
                raise OperationError(f"Invalid or duplicate recovered function name: {name}")
            definition = _ast_definition(unit, name)
            start, end = definition.start, definition.end
            if original[start:start + 1] != b"{" or original[end - 1:end] != b"}":
                raise OperationError(f"Clang range for {name} is outside a source body")
            body = _function_body(name, function["code"], definition.parameters)
            body, traps = restore_kernel_bugs(body, function.get("bug_sites"), original, start, end)
            if traps:
                outcome["verified_bug_rewrites"] = outcome.get("verified_bug_rewrites", 0) + traps
            elif (same_span := _fit_source_span(original[start:end], body)) is not None:
                body = same_span
                outcome["preserved_source_spans"] = outcome.get("preserved_source_spans", 0) + 1
            if traps or body.count(b"\n") != original.count(b"\n", start, end):
                closing_line = 1 + original.count(b"\n", 0, end)
                body = body[:-1] + f"\n#line {closing_line}\n}}".encode("ascii")
            edits.append((start, end, body))
            names.append(name)
        content = original
        previous = len(content)
        for start, end, body in sorted(edits, reverse=True):
            if end > previous:
                raise OperationError("Recovered function body ranges overlap")
            content = content[:start] + body + content[end:]
            previous = start
        with tempfile.TemporaryDirectory(prefix="rak-kbuild-") as temp:
            staging = Path(temp).resolve()
            replacement = staging / unit.source.name
            replacement.write_bytes(content)
            overlay = staging / "overlay.json"
            _overlay(overlay, [(unit.source, replacement)])
            obj = staging / "unit.o"
            command = unit.command(*STRICT_C, "-c", "-o", str(obj))
            _run(command, unit.cwd, f"Kbuild baseline for {relative}")
            baseline = _fingerprint(obj)
            obj.unlink()
            _run(command + ["-ivfsoverlay", str(overlay)], unit.cwd, f"Kbuild reconstruction for {relative}")
            recompiled = _fingerprint(obj)
            c_path = _safe_output(root, "compiled-c/" + relative)
            _publish(c_path, content)
            published = (unit.source, c_path)
            outcome.update(
                c_path=c_path.relative_to(root).as_posix(), functions=names, baseline_sha256=baseline,
                object_sha256=recompiled, object_identical=baseline == recompiled,
            )
            if unit.build_object and unit.build_object.is_file():
                build_sha = _fingerprint(unit.build_object)
                outcome.update(original_object_sha256=build_sha, matches_original_object=build_sha == recompiled)
            if executable:
                try:
                    rust = _rust(unit, overlay, staging, executable, names)
                    outcome["rust_scope"] = "translation_unit"
                except OperationError as error:
                    if "Clang AST was invalid" not in str(error):
                        raise
                    outcome["rust_full_unit_error"] = str(error)
                    selected_dir = staging / "selected"
                    selected_dir.mkdir()
                    selected = _selected_rust_unit(unit, content, overlay, selected_dir, names)
                    rust = _rust(selected, None, selected_dir, executable, names)
                    outcome["rust_scope"] = "recovered_functions"
                license_tag = re.search(rb"SPDX-License-Identifier: ([^\r\n*]+)", original[:1024])
                if license_tag and "SPDX-License-Identifier:" not in rust[:1024]:
                    license_id = license_tag[1].decode("utf-8").strip()
                    rust = f"// SPDX-License-Identifier: {license_id}\n" + rust
                rust_path = _safe_output(root, "rust/" + str(Path(relative).with_suffix(".rs").as_posix()))
                _publish(rust_path, rust.encode("utf-8"))
                outcome["rust_path"] = rust_path.relative_to(root).as_posix()
    except (OperationError, OSError, ValueError) as error:
        outcome["error"] = str(error)
    return outcome, published


def compile_tree(manifest, kernel_source, compile_commands, *, emit_rust=False, c2rust=None,
                 vmlinux=None, progress=None, jobs=1):
    """Build candidate objects under the recorded config, optionally transpiling verified C."""
    manifest = Path(manifest).resolve()
    root = manifest.parent
    source_root = Path(kernel_source).resolve()
    database = Path(compile_commands).resolve()
    units = _units(database, source_root)
    data = _read_json(manifest)
    if not isinstance(data, dict) or not isinstance(data.get("files"), list):
        raise OperationError("Invalid reconstruction manifest")
    executable = _c2rust_executable(c2rust) if emit_rust else None
    kernel_info = data.get("kernel_info") or {}
    expected_config = kernel_info.get("config_sha256")
    report = {
        "manifest": str(manifest), "compile_commands": str(database),
        "compile_commands_sha256": _fingerprint(database), "kernel_source": str(source_root),
        "units": [],
    }
    report_path = _safe_output(root, "compile-report.json")
    published = []
    have_errors = emit_rust and not executable
    if have_errors:
        report["rust_error"] = "C2Rust was not found in this build environment. Add it to PATH or pass --c2rust."
    if executable:
        report["c2rust"] = executable
    recovered = [row for row in data["files"] if row.get("recovered_functions")]
    if vmlinux and recovered:
        try:
            recovered, reference = _dwarf_compile_rows(Path(vmlinux).resolve(), data, recovered, units, source_root)
        except (OSError, ValueError) as error:
            raise OperationError(f"Cannot read inputs for DWARF source matching: {error}") from error
        report["dwarf_reference"] = reference

    def compile_row(row):
        return _compile_unit(row, units.get(row["path"]), root, expected_config, executable)

    def record(results):
        for index, (outcome, generated) in enumerate(results, 1):
            report["units"].append(outcome)
            if generated:
                published.append(generated)
            if progress:
                progress(index, len(recovered), outcome)

    if jobs > 1 and len(recovered) > 1 and len({row["path"] for row in recovered}) == len(recovered):
        with ThreadPoolExecutor(max_workers=min(jobs, len(recovered))) as pool:
            pending = [pool.submit(copy_context().run, compile_row, row) for row in recovered]
            record(future.result() for future in pending)
    else:
        record(map(compile_row, recovered))
    have_errors |= any("error" in outcome for outcome in report["units"])
    if published or (root / "compiled-c/vfs-overlay.json").exists():
        overlay_path = _safe_output(root, "compiled-c/vfs-overlay.json")
        _overlay(overlay_path, published)
        if published:
            report["overlay"] = str(overlay_path)
    report["compiled"] = sum("c_path" in row for row in report["units"])
    report["transpiled"] = sum("rust_path" in row for row in report["units"])
    if not report["units"]:
        report["error"] = "The manifest contains no named recovered C functions. Re-run reconstruct-tree."
    _write_json(report_path, report)
    if have_errors or not report["compiled"]:
        raise OperationError(
            f"Kernel build verification incomplete. See {report_path}. "
            "Run rak binary kernel-check with the kernel image and the same build options."
        )
    summary = {**report, "report": str(report_path)}
    summary["units"] = [
        {key: value for key, value in row.items() if key != "rust_full_unit_error"}
        for row in report["units"]
    ]
    return summary
