# SPDX-License-Identifier: MPL-2.0
"""Use authenticated ELF debug metadata to locate emitted kernel functions."""
from bisect import bisect_right
from collections import Counter, defaultdict
import hashlib
from pathlib import Path
import posixpath

from elftools.common.exceptions import ELFError, DWARFError
from elftools.elf.elffile import ELFFile

from core import binary_core, binary_xref, native_backend
from core.command_context import OperationError
from core.kernel_btf import IDENTIFIER


def _image_layout(elf):
    loads = [segment for segment in elf.iter_segments() if segment["p_type"] == "PT_LOAD"]
    placed = []
    base = None
    for section in elf.iter_sections():
        flags, size, offset = (section[key] for key in ("sh_flags", "sh_size", "sh_offset"))
        if not flags & 2 or not size or section.name in {".note", ".note.gnu.build-id", ".comment"}:
            continue
        virtual = section["sh_addr"]
        if section["sh_type"] == "SHT_NOBITS":
            base = virtual if base is None else min(base, virtual)
            continue
        physical = {
            segment["p_paddr"] + offset - segment["p_offset"]
            for segment in loads
            if segment["p_offset"] <= offset and offset + size <= segment["p_offset"] + segment["p_filesz"]
        }
        if len(physical) > 1:
            raise OperationError("vmlinux has ambiguous load addresses for an Image section.")
        address = next(iter(physical), virtual)
        base = address if base is None else min(base, address)
        placed.append((address, offset, size, virtual, flags))
    if not placed:
        raise OperationError("vmlinux has no loadable Image sections.")
    layout = [(address - base, offset, size, virtual, flags)
              for address, offset, size, virtual, flags in sorted(placed)]
    previous = 0
    for start, _, size, _, _ in layout:
        if start < previous or start + size > binary_core.MAX_BYTES:
            raise OperationError("vmlinux has overlapping or oversized Image sections.")
        previous = start + size
    return layout


def _flat_digest(path, stream, layout):
    ranges = [row[:3] for row in layout]
    digest = native_backend.binary_flat_sha256(path, ranges)
    if digest is not None:
        return digest
    checksum = hashlib.sha256()
    position = 0
    zeros = bytes(64 * 1024)
    for start, offset, length in ranges:
        while position < start:
            count = min(start - position, len(zeros))
            checksum.update(zeros[:count])
            position += count
        stream.seek(offset)
        while length:
            block = stream.read(min(length, len(zeros)))
            if not block:
                raise OperationError("Truncated loadable vmlinux section.")
            checksum.update(block)
            length -= len(block)
            position += len(block)
    return checksum.hexdigest()


def _declaration_path(line_program, declaration):
    attr = declaration.attributes.get("DW_AT_decl_file")
    if not attr or line_program is None:
        return ""
    header = line_program.header
    offset = 0 if header.version >= 5 else 1
    file_index = attr.value - offset
    if not 0 <= file_index < len(header.file_entry):
        return ""
    entry = header.file_entry[file_index]
    directory = entry.dir_index - offset
    parent = header.include_directory[directory] if 0 <= directory < len(header.include_directory) else b""
    return posixpath.join(parent.decode("utf-8", errors="replace"), entry.name.decode("utf-8", errors="replace"))


def source_functions(reference, result, symbols, clean_path, selected, *, limit=None, progress=None):
    """Return source file owners in the addresses used by the selected kernel."""
    reference = Path(reference).resolve()
    if result["format"] not in {"PE", "ARM64 Image", "ELF"}:
        raise OperationError("Kernel DWARF source matching needs an ARM64 Image or vmlinux.")
    try:
        with reference.open("rb") as stream:
            elf = ELFFile(stream)
            if elf["e_machine"] != "EM_AARCH64" or not elf.little_endian:
                raise OperationError("Choose a little-endian ARM64 vmlinux.")
            if result["format"] == "ELF":
                stream.seek(0)
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
                locations = None
            else:
                layout = _image_layout(elf)
                digest = _flat_digest(reference, stream, layout)
                locations = sorted((virtual, virtual + size, start)
                                   for start, _, size, virtual, flags in layout if flags & 4)
            if digest != result["sha256"]:
                raise OperationError("The kernel SHA-256 from vmlinux differs from the selected kernel.")
            if not elf.has_dwarf_info(strict=True):
                raise OperationError("The same-build vmlinux must contain DWARF debug information.")

            if progress:
                progress(0, 1, "Indexing DWARF source functions...")
            rows = native_backend.binary_dwarf_functions(reference) if reference.stat().st_size <= binary_core.MAX_BYTES else None
            if rows is None:
                dwarf = elf.get_dwarf_info(relocate_dwarf_sections=False, follow_links=False)
                files = _function_locations(dwarf, result, symbols, locations, clean_path, selected, limit, progress)
            else:
                files = _native_locations(rows, result, symbols, locations, clean_path, selected, limit)
    except (OSError, ELFError, DWARFError, ValueError) as error:
        raise OperationError(f"Cannot read kernel DWARF from {reference}: {error}") from error
    if not files:
        raise OperationError("vmlinux has no matching emitted C functions for the requested source paths.")
    return files


def _verified_address(result, symbols, locations, starts, symbol, address):
    if locations is not None:
        index = bisect_right(starts, address) - 1
        if index < 0 or address >= locations[index][1]:
            return None
        first, _, file_start = locations[index]
        address, _ = binary_xref.offset_to_va(result, file_start + address - first)
    row = symbols.get(address)
    return address if IDENTIFIER.fullmatch(symbol) and row and row["name"] == symbol else None


def _native_locations(rows, result, symbols, locations, clean_path, selected, limit):
    files = defaultdict(set)
    starts = [row[0] for row in locations] if locations is not None else ()
    for raw_source, declaration, symbol, pc in rows:
        source = clean_path(posixpath.normpath(raw_source))
        if not source.endswith(".c") or (selected and source.casefold() not in selected):
            continue
        if limit is not None and len(files.get(source, ())) >= limit:
            continue
        if clean_path(posixpath.normpath(declaration)) != source:
            continue
        address = _verified_address(result, symbols, locations, starts, symbol, pc)
        if address is not None:
            files[source].add(address)
    return _unique_owners(files)


def _function_locations(dwarf, result, symbols, locations, clean_path, selected, limit, progress):
    files = defaultdict(set)
    starts = [row[0] for row in locations] if locations is not None else ()
    for index, cu in enumerate(dwarf.iter_CUs()):
        if progress and index % 32 == 0:
            progress(cu.cu_offset, dwarf.debug_info_sec.size, "Indexing DWARF source functions...")
        top = cu.get_top_DIE()
        name = top.attributes.get("DW_AT_name")
        if name is None:
            continue
        source = clean_path(posixpath.normpath(name.value.decode("utf-8", errors="replace")))
        if not source.endswith(".c") or (selected and source.casefold() not in selected):
            continue
        if limit is not None and len(files.get(source, ())) >= limit:
            continue
        line_program = dwarf.line_program_for_CU(cu)
        for die in top.iter_children():
            pc = die.attributes.get("DW_AT_low_pc")
            if die.tag != "DW_TAG_subprogram" or not pc:
                continue
            declaration = die
            for origin in ("DW_AT_abstract_origin", "DW_AT_specification"):
                if origin in declaration.attributes:
                    declaration = declaration.get_DIE_from_attribute(origin)
            name = declaration.attributes.get("DW_AT_name")
            if not name or clean_path(posixpath.normpath(_declaration_path(line_program, declaration))) != source:
                continue
            symbol = name.value.decode("utf-8", errors="replace")
            address = _verified_address(result, symbols, locations, starts, symbol, pc.value)
            if address is not None:
                files[source].add(address)
                if limit is not None and len(files[source]) >= limit:
                    break
    return _unique_owners(files)


def _unique_owners(files):
    claims = Counter(address for functions in files.values() for address in functions)
    owners = {}
    for path, functions in files.items():
        addresses = [hex(address) for address in sorted(functions) if claims[address] == 1]
        if addresses:
            owners[path] = addresses
    return owners
