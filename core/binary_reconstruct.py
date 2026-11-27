# SPDX-License-Identifier: MPL-2.0
"""Export decompiled functions and record the evidence for kernel source paths."""
from __future__ import annotations

from bisect import bisect_right
from collections import Counter, defaultdict
from contextvars import copy_context
from dataclasses import dataclass, field
import hashlib
import json
import logging
import mmap
import os
from pathlib import Path
import re
import struct
import tempfile
import time
from typing import Callable

from core import (
    binary_core, binary_listing, binary_semantics, binary_session, binary_xref, gki_checkout,
    kernel_btf, kernel_source_provider, kernel_symbols, kernel_version, kitchen_process, native_decompiler,
)
from core.command_context import OperationError

SOURCE_EXTENSIONS = (
    ".c", ".h", ".s", ".rs", ".cpp", ".cc", ".cxx", ".hpp", ".hh", ".inc"
)
RESOURCE_EXTENSIONS = (
    ".xml", ".json", ".conf", ".cfg", ".prop", ".txt", ".ini", ".rc", ".sh", ".so"
)


KERNEL_TOP_DIRS = {
    "arch", "block", "certs", "crypto", "drivers", "fs", "include", "init",
    "ipc", "kernel", "lib", "mm", "net", "samples", "scripts", "security",
    "sound", "tools", "usr", "virt"
}
LOG = logging.getLogger(__name__)
OUTPUT_FILES = {"readme.md", "reconstruction_manifest.json", "system.map.recovered", "kernel.config"}
WINDOWS_DEVICE = re.compile(r"(?:con|prn|aux|nul|com[1-9¹²³]|lpt[1-9¹²³])(?:\..*)?\Z", re.I)


@dataclass
class ReconstructionMetadata:
    btf: kernel_btf.BTF | None = None
    functions: dict = field(default_factory=dict)
    names: Counter = field(default_factory=Counter)
    relocations: dict = field(default_factory=dict)
    sources: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    recovered_symbols: list[kernel_symbols.Symbol] = field(default_factory=list)
    bug_table: tuple[int, int] | None = None

    def prototype(self, address):
        name = self.functions.get(address, {}).get("name", "")
        if self.btf is not None and self.names[name] == 1:
            return name, self.btf.prototype(name)
        return name, None


def _infer_arm64_image_origin(path: str, recovered: list) -> int | None:
    """Infer VA corresponding to file offset 0x0 (_text) of a raw ARM64 Linux Image."""
    origin = next((row.address for row in recovered if row.name == "_text" and row.address), None)
    if origin is not None:
        return origin

    hdr_words = (0, 0)
    text_offset = 0
    try:
        with open(path, "rb") as stream:
            hdr = stream.read(64)
        if len(hdr) >= 16:
            w0, w1, text_offset = struct.unpack_from("<IIQ", hdr, 0)
            hdr_words = (w0, w1)
    except OSError:
        pass

    entry_syms = {
        row.name: row.address
        for row in recovered
        if row.name in ("stext", "primary_entry") and row.address
    }
    if entry_syms:
        for idx, word in enumerate(hdr_words):
            if (word & 0xFC000000) == 0x14000000:
                b_off = idx * 4 + ((word & 0x03FFFFFF) << 2)
                for sym_va in entry_syms.values():
                    if b_off > 0 and sym_va > b_off and ((sym_va - b_off) & 0xFFF) == 0:
                        return sym_va - b_off

    anchor = next((row.address for row in recovered if row.name == "_stext" and row.kind in "Tt" and row.address), None)
    if anchor is None:
        anchor = next((row.address for row in recovered if row.kind in "Tt" and row.address), None)
    if anchor is None:
        return None
    if 0 <= text_offset < 0x200000 and (text_offset & 0xFFF) == 0:
        low_mask = 0x7FFFF
        t_low = text_offset & low_mask
        cand = ((anchor - t_low) & ~low_mask) + t_low
        if 0 <= anchor - cand < 0x40000:
            return cand
    return anchor & ~0xFFFF


def _load_metadata(path, result, btf_path, system_map):
    metadata = ReconstructionMetadata()
    header = result["header"]
    external = []
    bias = 0
    if system_map:
        try:
            external = kernel_symbols.read_system_map(system_map)
            if result["format"] == "ARM64 Image":
                bases = {item.address for item in external if item.name == "_text" and item.kind in "Tt"}
                if len(bases) != 1 or not 0 < min(bases) <= (1 << 64) - int(header["size"]):
                    raise ValueError("A raw Image requires a unique _text address from the same build")
                base = bases.pop()
                result["sections"] = [{**row, "addr": hex(base)} for row in result["sections"]]
                result["header"] = {**header, "entry_point": hex(base)}
                result["warnings"] = ["Image base from System.map. Section boundaries are unavailable."]
            elif result["format"] == "PE":
                origin = next((row.address for row in external if row.name == "_text"), None)
                section = next((row for row in result["sections"] if row["name"] == ".text"), None)
                if origin is None or not section:
                    raise ValueError("An EFI Image requires _text and a mapped .text section")
                bias = int(section["addr"], 16) - int(section["offset"], 16) - origin
            result["_native_cache_tag"] = "map-" + binary_core.fingerprint(system_map)
        except (OSError, ValueError) as error:
            raise OperationError(f"Cannot load System.map: {error}") from error

    labels = {row.name: row.address for row in external if row.name.startswith("kallsyms_")}
    for category in ("exported", "local"):
        for row in result["symbols"].get(category, ()):
            if row["name"].startswith("kallsyms_"):
                labels[row["name"]] = int(row["value"], 16) - bias
    result["addr_symbol_map"] = result["addr_symbol_map"].copy()
    try:
        recovered = kernel_symbols.recover(path, result, labels, bias)
        discovery = "labelled"
        if not recovered and not external and result["format"] in {"ARM64 Image", "PE"}:
            recovered = kernel_symbols.recover_blind(path, result)
            discovery = "blind"
            if recovered:
                if result["format"] == "ARM64 Image":
                    origin = _infer_arm64_image_origin(path, recovered)
                    section = result["sections"][0] if result.get("sections") else None
                    if origin is not None and section:
                        bias = int(section["addr"], 16) - int(section["offset"], 16) - origin
                elif result["format"] == "PE":
                    origin = next((row.address for row in recovered if row.name == "_text"), None)
                    section = next((row for row in result["sections"] if row["name"] == ".text"), None)
                    if origin is not None and section:
                        bias = int(section["addr"], 16) - int(section["offset"], 16) - origin
                    elif section:
                        stext = next((row.address for row in recovered if row.name == "_stext"), None)
                        if stext is not None:
                            bias = int(section["addr"], 16) - stext
        if recovered:
            added = kernel_symbols.add_symbols(result, recovered, "kallsyms", bias)
            metadata.sources["kallsyms"] = {
                "symbols": len(recovered), "new_functions": added, "discovery": discovery,
            }
            metadata.recovered_symbols = recovered
            result.setdefault("_native_cache_tag", "kallsyms")
    except ValueError as error:
        metadata.warnings.append(str(error))
    if external:
        added = kernel_symbols.add_symbols(result, external, "System.map", bias)
        metadata.sources["system_map"] = {"symbols": len(external), "new_functions": added}
    for symbols in (external, metadata.recovered_symbols):
        bounds = {row.name: row.address + bias for row in symbols
                  if row.name in ("__start___bug_table", "__stop___bug_table")}
        if len(bounds) == 2:
            metadata.bug_table = bounds["__start___bug_table"], bounds["__stop___bug_table"]
            break
    if metadata.bug_table is None and result["header"].get("abi") in {"AARCH64", "ARM64"}:
        metadata.bug_table = _discover_arm64_bug_table(path, result)

    section = next((row for row in result["sections"] if row["name"] == ".BTF"), None)
    btf_origin = ".BTF"
    if not btf_path and not section:
        for origin_label, symbols in (
            ("System.map Image range", external),
            ("kallsyms Image range", metadata.recovered_symbols),
        ):
            if not symbols:
                continue
            boundaries = {
                row.name: row.address + bias for row in symbols
                if row.name in ("__start_BTF", "__stop_BTF")
            }
            start = boundaries.get("__start_BTF", 0)
            stop = boundaries.get("__stop_BTF", 0)
            if 0 < stop - start <= kernel_btf.MAX_BTF_BYTES:
                begin, begin_section = binary_xref.va_to_offset(result, start)
                last, last_section = binary_xref.va_to_offset(result, stop - 1)
                if (
                    begin is not None and last is not None
                    and begin_section == last_section and last - begin == stop - start - 1
                ):
                    section = {"offset": hex(begin), "size": stop - start}
                    btf_origin = origin_label
                    break
    if not btf_path and not section and result["format"] in {"ARM64 Image", "PE"}:
        # Fallback: scan for embedded raw BTF header magic (0xEB9F, version=1, flags=0, hdr_len=24)
        btf_magic = struct.pack("<HBBI" if header["endianness"] == "little" else ">HBBI", 0xEB9F, 1, 0, 24)
        fmt_hdr = "<5I" if header["endianness"] == "little" else ">5I"
        try:
            with open(path, "rb") as stream, mmap.mmap(stream.fileno(), 0, access=mmap.ACCESS_READ) as mm:
                hit = mm.find(btf_magic)
                while hit != -1 and hit + 24 <= len(mm):
                    hdr_len, type_off, type_len, str_off, str_len = struct.unpack_from(fmt_hdr, mm, hit + 4)
                    total_btf = hdr_len + str_off + str_len
                    if (
                        hdr_len >= 24
                        and type_off + type_len <= str_off
                        and 24 < total_btf <= kernel_btf.MAX_BTF_BYTES
                        and hit + total_btf <= len(mm)
                        and mm[hit + hdr_len + str_off] == 0
                    ):
                        section = {"offset": hex(hit), "size": total_btf}
                        btf_origin = "embedded Image scan"
                        break
                    hit = mm.find(btf_magic, hit + 4)
        except OSError:
            pass
    if btf_path or section:
        try:
            if btf_path:
                with open(btf_path, "rb") as stream:
                    data = stream.read(kernel_btf.MAX_BTF_BYTES + 1)
            else:
                size = section.get("file_size", section["size"])
                if not 0 < size <= kernel_btf.MAX_BTF_BYTES or section.get("type") == "SHT_NOBITS":
                    raise ValueError("Invalid .BTF section length")
                with open(path, "rb") as stream:
                    stream.seek(int(section["offset"], 16))
                    data = stream.read(size)
                if len(data) != size:
                    raise ValueError("Truncated .BTF section")
            metadata.btf = kernel_btf.BTF(data, int(header["class"]) // 8, header["endianness"])
            metadata.sources["btf"] = "sidecar" if btf_path else btf_origin
        except (OSError, ValueError) as error:
            if btf_path:
                raise OperationError(f"Cannot load BTF: {error}") from error
            metadata.warnings.append(f"Cannot read .BTF: {error}")
    metadata.functions = {row["addr"]: row for row in result["functions"]}
    metadata.names.update(row["name"] for row in metadata.functions.values())
    for row in result.get("relocations", ()):
        metadata.relocations.setdefault(int(row["offset"], 16), []).append(row)
    return metadata


_RUST_KEYWORDS = {
    "as", "async", "await", "box", "break", "const", "continue", "crate", "dyn", "else", "enum",
    "extern", "false", "fn", "for", "if", "impl", "in", "let", "loop", "macro", "match", "mod",
    "move", "mut", "priv", "pub", "ref", "return", "self", "Self", "static", "struct", "super",
    "trait", "true", "type", "typeof", "unsafe", "unsized", "use", "virtual", "where", "while", "yield",
}
_RUST_PRIMITIVES = {
    "void": "()",
    "_Bool": "bool",
    "bool": "bool",
    "char": "core::ffi::c_char",
    "signed char": "i8",
    "unsigned char": "u8",
    "short": "i16",
    "short int": "i16",
    "unsigned short": "u16",
    "unsigned short int": "u16",
    "int": "core::ffi::c_int",
    "signed int": "core::ffi::c_int",
    "unsigned": "core::ffi::c_uint",
    "unsigned int": "core::ffi::c_uint",
    "long": "isize",
    "long int": "isize",
    "unsigned long": "usize",
    "unsigned long int": "usize",
    "long long": "i64",
    "long long int": "i64",
    "unsigned long long": "u64",
    "unsigned long long int": "u64",
}


def _c_type_to_rust(c_type: str) -> tuple[str, set[str]]:
    structs: set[str] = set()
    t = " ".join(c_type.strip().split())
    if t.endswith("*"):
        inner = t[:-1].strip()
        is_const = False
        if inner.endswith(" const") or inner.startswith("const "):
            is_const = True
            inner = re.sub(r"\bconst\b", "", inner).strip()
        inner = re.sub(r"\b(?:volatile|restrict)\b", "", inner).strip()
        if inner == "void":
            return ("*const core::ffi::c_void" if is_const else "*mut core::ffi::c_void"), structs
        mapped, sub_structs = _c_type_to_rust(inner)
        structs.update(sub_structs)
        return (f"*const {mapped}" if is_const else f"*mut {mapped}"), structs
    t_clean = re.sub(r"\b(?:const|volatile|restrict)\b", "", t).strip()
    t_clean = " ".join(t_clean.split())
    if t_clean in _RUST_PRIMITIVES:
        return _RUST_PRIMITIVES[t_clean], structs
    m = re.fullmatch(r"(?:struct|union)\s+([A-Za-z_][A-Za-z_0-9]*)", t_clean)
    if m:
        structs.add(m.group(1))
        return m.group(1), structs
    return "*mut core::ffi::c_void", structs


def _rust_ident(name: str) -> str:
    clean = re.sub(r"[^A-Za-z0-9_]", "_", name)
    if not clean or clean[0].isdigit():
        clean = "arg_" + clean
    return f"r#{clean}" if clean in _RUST_KEYWORDS else clean


def _rust_ffi_skeleton(declarations: list[str], metadata: ReconstructionMetadata | None, func_addrs: list[str]) -> list[str]:
    out = [
        "#![no_std]",
        "#![allow(non_camel_case_types, non_snake_case, dead_code)]\n",
    ]
    all_structs: set[str] = set()
    extern_fns: list[str] = []
    seen_syms: set[str] = set()

    for decl in declarations:
        d = decl.strip().rstrip(";")
        d = re.sub(r"^static\s+", "", d)
        m = re.match(r"^(.*?)\b([A-Za-z_][A-Za-z_0-9]*)\s*\((.*)\)\Z", d)
        if not m:
            continue
        ret_c, sym, params_c = m.group(1).strip(), m.group(2), m.group(3).strip()
        ret_rs, s1 = _c_type_to_rust(ret_c)
        all_structs.update(s1)
        rs_params = []
        variadic = False
        if params_c and params_c != "void":
            for idx, p in enumerate(params_c.split(",")):
                p = p.strip()
                if p == "...":
                    variadic = True
                    rs_params.append("...")
                    continue
                pm = re.match(r"^(.*?)([A-Za-z_][A-Za-z_0-9]*)\Z", p)
                if pm:
                    pt, pn = pm.group(1).strip(), _rust_ident(pm.group(2))
                else:
                    pt, pn = p, f"arg{idx}"
                pt_rs, sp = _c_type_to_rust(pt)
                all_structs.update(sp)
                rs_params.append(f"{pn}: {pt_rs}")
        ret_clause = "" if ret_rs == "()" else f" -> {ret_rs}"
        fn_ident = _rust_ident(sym)
        if not variadic or len(rs_params) > 1:
            extern_fns.append(f"    pub fn {fn_ident}({', '.join(rs_params)}){ret_clause};")
            seen_syms.add(sym)

    if metadata:
        for faddr in func_addrs:
            addr_int = int(faddr, 16)
            sym = metadata.functions.get(addr_int, {}).get("name", "")
            if sym and kernel_btf.IDENTIFIER.fullmatch(sym) and sym not in seen_syms:
                extern_fns.append(f"    // Unverified prototype for {sym} @ {faddr}")
                extern_fns.append(f"    pub fn {_rust_ident(sym)}();")
                seen_syms.add(sym)

    for st in sorted(all_structs):
        out.append(f"#[repr(C)]\npub struct {_rust_ident(st)} {{\n    _private: [u8; 0],\n}}\n")
    if extern_fns:
        out.append('unsafe extern "C" {')
        out.extend(extern_fns)
        out.append("}\n")
    return out


def _comment(text):
    return "\n".join("// " + line + (" " if line.endswith("\\") else "") for line in text.splitlines())


def _clean_rel_path(path_str: str) -> str:
    cleaned = re.sub(r"^[a-zA-Z]:", "", path_str.replace("\\", "/").strip()).lstrip("/")
    parts = [
        p for p in cleaned.split("/")
        if p and p != "." and p != ".." and not (p.startswith("[") and p.endswith("]"))
    ]
    for i, part in enumerate(parts):
        p_low = part.lower()
        if p_low in KERNEL_TOP_DIRS:
            parts = parts[i:]
            break
        elif p_low == "rust" and i + 1 < len(parts):
            parts = parts[i:]
            break
    while len(parts) >= 2 and parts[0].lower() in ("proc", "self", "cwd", "filled", "smss", "home", "build"):
        parts = parts[1:]
    if any(
        re.search(r'[<>:"|?*\x00-\x1f]', part) or part.endswith((".", " ")) or WINDOWS_DEVICE.fullmatch(part)
        for part in parts
    ):
        return ""
    return "/".join(parts)


def is_source_filename(path_str: str) -> bool:
    norm = path_str.lower().strip()
    return any(norm.endswith(ext) for ext in SOURCE_EXTENSIONS)


def _discover_arm64_bug_table(path, result) -> tuple[int, int] | None:
    """Blindly locate a contiguous Linux AArch64 relative __bug_table when kallsyms omits data symbols."""
    if result["header"].get("endianness") != "little":
        return None
    sections = binary_xref._section_ranges(result)
    if not sections:
        return None

    def offset_to_va(off):
        for part in sections:
            if part["va"] and part["offset"] <= off < part["offset"] + part["size"]:
                return part["va"] + (off - part["offset"])
        return None

    def va_to_offset(va, executable=False):
        for part in sections:
            if (part["va"] and part["va"] <= va < part["va"] + part["size"]
                    and (not executable or part["flags"] & 4)):
                return part["offset"] + (va - part["va"])
        return None

    try:
        with open(path, "rb") as stream, mmap.mmap(stream.fileno(), 0, access=mmap.ACCESS_READ) as data:
            n = len(data)
            if n < 256:
                return None

            def valid_at(pos):
                if pos < 0 or pos + 12 > n:
                    return False
                va = offset_to_va(pos)
                if va is None:
                    return False
                pc_disp, file_disp, line, _ = struct.unpack_from("<iiHH", data, pos)
                pc = va + pc_disp
                code = va_to_offset(pc, executable=True)
                if not line or pc % 4 or code is None or code + 4 > n or data[code : code + 4] != b"\x00\x00\x21\xd4":
                    return False
                for delta in (0, 4):
                    f_off = va_to_offset(va + file_disp + delta)
                    if f_off is not None and 0 <= f_off < n - 4 and 0x21 <= data[f_off] <= 0x7E:
                        end = data.find(b"\0", f_off, min(n, f_off + 256))
                        if end > f_off + 2 and b"/" in data[f_off:end]:
                            return True
                return False

            for probe in range(0, n - 192, 192):
                for align in (0, 4, 8):
                    pos = probe + align
                    if valid_at(pos) and valid_at(pos + 12) and valid_at(pos + 24) and valid_at(pos + 36):
                        start = pos
                        while start >= 12 and valid_at(start - 12):
                            start -= 12
                        stop = pos + 48
                        while stop + 12 <= n and valid_at(stop):
                            stop += 12
                        if (stop - start) // 12 >= 16:
                            va_s = offset_to_va(start)
                            va_e = offset_to_va(stop - 12)
                            if va_s is not None and va_e is not None:
                                return va_s, va_e + 12
    except OSError:
        pass
    return None


def _annotate_arm64_warn_xrefs(path, result, xrefs):
    """Detect WARN()/warn_slowpath_*(file, line, ...) call sites when CONFIG_DEBUG_BUGVERBOSE=n."""
    if not xrefs or result["header"].get("abi") not in {"AARCH64", "ARM64"} or result["header"].get("endianness") != "little":
        return
    addr_map = result.get("addr_symbol_map", {})
    if not addr_map:
        return
    warn_helpers = ("warn_slowpath_", "__warn_printk", "assfail")
    try:
        with open(path, "rb") as stream, mmap.mmap(stream.fileno(), 0, access=mmap.ACCESS_READ) as data:
            n = len(data)
            for item in xrefs:
                if item.get("kind") not in ("ADR", "ADRP+ADD"):
                    continue
                try:
                    pc = int(item["from"], 16)
                except (KeyError, ValueError, TypeError):
                    continue
                off, _ = binary_xref.va_to_offset(result, pc)
                if off is None:
                    continue
                win_lo = max(0, (off - 20) & ~3)
                win_hi = min(n & ~3, off + 48)
                called_warn = False
                line_val = 0
                for cur in range(win_lo, win_hi, 4):
                    insn = struct.unpack_from("<I", data, cur)[0]
                    cur_va = pc + (cur - off)
                    if cur >= off and (insn & 0x7C000000) == 0x14000000:
                        imm26 = insn & 0x03FFFFFF
                        if imm26 & (1 << 25):
                            imm26 -= 1 << 26
                        callee = cur_va + (imm26 << 2)
                        sym = addr_map.get(callee, "")
                        if sym == "__warn" or sym.startswith(warn_helpers):
                            called_warn = True
                    # MOVZ W1, #imm16 (register 1 carries __LINE__ in warn_slowpath_fmt/null)
                    if (insn & 0xFFE0001F) == 0x52800001:
                        cand_line = (insn >> 5) & 0xFFFF
                        if 0 < cand_line < 65000:
                            line_val = cand_line
                if called_warn and line_val:
                    item["kind"] = f"WARN_SITE:L{line_val}"
                    item["line"] = line_val
    except OSError:
        pass


def _kernel_bug_references(path, result, bounds):
    if not bounds or result["header"]["abi"] not in {"AARCH64", "ARM64"}:
        return {}, {}
    start, stop = bounds
    entries = struct.Struct("<iiHH" if result["header"]["endianness"] == "little" else ">iiHH")
    size = stop - start
    if not 0 < size <= 16 * 1024 * 1024 or size % entries.size:
        return {}, {}
    sections = binary_xref._section_ranges(result)

    def offset(va, executable=False):
        for part in sections:
            if (part["va"] and part["va"] <= va < part["va"] + part["size"]
                    and (not executable or part["flags"] & 4)):
                return part["offset"] + va - part["va"]
        return None

    begin, last = offset(start), offset(stop - 1)
    if begin is None or last != begin + size - 1:
        return {}, {}
    variants = ([], [])
    with open(path, "rb") as stream, mmap.mmap(stream.fileno(), 0, access=mmap.ACCESS_READ) as data:
        if last >= len(data):
            return {}, {}
        for position in range(begin, last + 1, entries.size):
            pc_disp, file_disp, line, flags = entries.unpack_from(data, position)
            entry = start + position - begin
            pc = entry + pc_disp
            code = offset(pc, executable=True)
            # Linux AArch64 BUG records point to BRK #0x800.
            if not line or pc % 4 or code is None or data[code:code + 4] != b"\x00\x00\x21\xd4":
                continue
            for index, matches in enumerate(variants):
                address = entry + file_disp + index * 4
                at = offset(address)
                if at is None or (end := data.find(b"\0", at, min(at + 512, len(data)))) == -1:
                    continue
                try:
                    name = data[at:end].decode("ascii")
                except UnicodeDecodeError:
                    continue
                relative = _clean_rel_path(name)
                top = relative.partition("/")[0]
                if (top in KERNEL_TOP_DIRS or top == "rust") and is_source_filename(relative):
                    matches.append((relative, address, at, name, pc, line, flags))
    if len(variants[0]) == len(variants[1]):
        return {}, {}
    references = defaultdict(list)
    locations = defaultdict(dict)
    starts = result.get("_function_starts", [])
    for relative, address, at, name, pc, line, flags in max(variants, key=len):
        function_index = bisect_right(starts, pc) - 1
        function = ""
        if function_index >= 0:
            row = result["functions"][function_index]
            if pc < row["addr"] + (row["size"] or 0x4000):
                function = hex(row["addr"])
        references[relative].append({"from": hex(pc), "to": hex(address), "kind": f"BUG_TABLE:L{line}",
                                     "section": "__bug_table", "function_address": function,
                                     "line": line, "flags": flags})
        locations[relative][address] = {"va": hex(address), "va_int": address, "offset": hex(at), "value": name}
    return references, {relative: list(rows.values()) for relative, rows in locations.items()}


def is_reconstructible_filename(path_str: str) -> bool:
    norm = path_str.lower().strip()
    base = os.path.basename(norm)
    if not base or base.startswith("."):
        return False
    return is_source_filename(norm) or any(norm.endswith(ext) for ext in RESOURCE_EXTENSIONS) or ("." in base and len(base) > 2)


def collect_source_paths(result: dict, binary_path: str | None = None) -> list[str]:
    raw_paths = list(result.get("path_like_strings", []))

    # If few paths found (< 50) and binary_path is provided, scan the binary directly
    # to catch rodata strings that may have been cut off by string extraction row caps.
    if len(raw_paths) < 50 and binary_path and os.path.isfile(binary_path):
        try:
            with open(binary_path, "rb") as stream:
                data = stream.read()
            # Match paths with a directory separator or standard source extensions
            matches = re.findall(
                rb"([a-zA-Z0-9_\-\./]+/(?:[a-zA-Z0-9_\-\.]+\.[a-zA-Z0-9_]{1,5}))[\x00\r\n\t]",
                data,
            )
            for m in matches:
                try:
                    s = m.decode("utf-8", errors="ignore")
                    raw_paths.append(s)
                except Exception:
                    pass
        except Exception:
            pass

    seen = set(OUTPUT_FILES)
    collected = []
    for p in raw_paths:
        cleaned = _clean_rel_path(p)
        key = cleaned.casefold()
        if not cleaned or key in seen:
            continue
        if is_reconstructible_filename(cleaned):
            seen.add(key)
            collected.append(cleaned)
    return collected


def reconstruct_file_content(
    binary_path: str,
    result: dict,
    rel_path: str,
    session: binary_session.BinarySession | None = None,
    max_funcs: int = 5,
    locations: list[dict] | None = None,
    func_addrs: list[str] | None = None,
    line_numbers: list[int] | None = None,
    source_provider: kernel_source_provider.KernelSourceProvider | None = None,
    metadata: ReconstructionMetadata | None = None,
    report: dict | None = None,
    diagnostic_references=(),
) -> tuple[str, int, list[str]]:
    """Generate C/header/assembly source content for a single reconstructed path."""
    if locations is None:
        locations = binary_xref.find_string_locations(binary_path, result, rel_path, limit=3)
    target_va = locations[0]["va_int"] if locations else None

    if func_addrs is None or line_numbers is None:
        direct_xrefs = binary_xref.scan_xrefs(binary_path, result, [target_va]) if target_va else []
        func_addrs = []
        line_numbers = []
        for x in direct_xrefs:
            kind = x.get("kind", "")
            if "BUG_TABLE:L" in kind:
                try:
                    line_numbers.append(int(kind.split(":L")[1]))
                except (ValueError, IndexError):
                    pass
            fa = x.get("function_address")
            if not fa and x.get("from"):
                try:
                    guessed = binary_xref.guess_function_start(binary_path, result, int(x["from"], 16))
                    if guessed:
                        fa = hex(guessed)
                except (ValueError, OSError):
                    fa = None
            if fa and fa not in func_addrs:
                func_addrs.append(fa)

        # If no direct Xrefs were found, scan the contiguous rodata cluster
        if not func_addrs and target_va:
            cluster_vas = [target_va + i for i in range(-128, 256, 16)]
            cluster_xrefs = binary_xref.scan_xrefs(binary_path, result, cluster_vas, max_hits=10)
            for cx in cluster_xrefs:
                cfa = cx.get("function_address")
                if not cfa and cx.get("from"):
                    try:
                        g = binary_xref.guess_function_start(binary_path, result, int(cx["from"], 16))
                        if g:
                            cfa = hex(g)
                    except (ValueError, OSError):
                        cfa = None
                if cfa and cfa not in func_addrs:
                    func_addrs.append(cfa)

    # Decompile functions
    decompiled_blocks = []
    failures = []
    rewrites = 0
    declarations = []
    bug_sites = defaultdict(list)
    for reference in diagnostic_references:
        if reference["flags"] == 0 and reference["function_address"]:
            bug_sites[reference["function_address"]].append({"address": reference["from"], "line": reference["line"]})
    ext = os.path.splitext(rel_path)[1].lower()
    names = metadata.names if metadata else Counter()
    if metadata and report and report.get("path_attribution") == "dwarf":
        names = Counter(metadata.functions.get(int(address, 16), {}).get("name", "") for address in func_addrs)
    for faddr in func_addrs[:max_funcs]:
        address = int(faddr, 16)
        declaration = None
        prototype = None
        if metadata and metadata.btf:
            symbol, prototype = metadata.prototype(address)
            declaration = metadata.btf.declaration(symbol, prototype)
            if declaration and declaration not in declarations:
                declarations.append(declaration)
        if ext == ".s":
            continue
        try:
            if session is not None:
                d = session.decompile_native(result, faddr)
            else:
                d = native_decompiler.decompile(binary_path, result, address)
            code = d.get("pseudocode", "").strip()
            if not code:
                raise OperationError("Decompiler returned no pseudocode")
            if prototype:
                code, changed = binary_semantics.rewrite(code, metadata.btf, prototype)
                rewrites += changed
            if report is not None and ext == ".c" and metadata:
                name = metadata.functions.get(address, {}).get("name", "")
                if kernel_btf.IDENTIFIER.fullmatch(name) and names[name] == 1:
                    recovered = {"name": name, "address": faddr, "code": code}
                    offset, _ = binary_xref.va_to_offset(result, address)
                    if offset is not None:
                        recovered["file_offset"] = hex(offset)
                    if faddr in bug_sites:
                        recovered["bug_sites"] = bug_sites[faddr]
                    report.setdefault("recovered_functions", []).append(recovered)
            heading = f"// Function @ {faddr}"
            if declaration:
                heading += f"\n// BTF prototype: {declaration}"
            decompiled_blocks.append(f"{heading}\n{_comment(code) if ext in ('.h', '.rs') else code}")
        except (OperationError, OSError, kitchen_process.TimeoutExpired) as error:
            detail = str(error)
            if isinstance(error, kitchen_process.TimeoutExpired):
                detail += f"\n{error.stdout or ''}\n{error.stderr or ''}"
            failures.append({"address": faddr, "detail": detail})
            LOG.warning("Reconstruct %s at %s\n%s", rel_path, faddr, detail)
            decompiled_blocks.append(f"// Function @ {faddr} (Decompile failed. See log.)")
    if report is not None:
        report.update(failed_functions=len(failures), typed_field_rewrites=rewrites)
        if failures:
            report["function_errors"] = failures

    # Format file body
    header_info = result.get("header", {})
    arch = header_info.get("abi", "Unknown")
    endian = header_info.get("endianness", "little")

    # Upstream matching via KernelSourceProvider
    matched_upstream = []
    if source_provider and line_numbers:
        for ln in sorted(set(line_numbers))[:3]:
            m = source_provider.find_function_by_line(rel_path, ln)
            if m and not any(x["name"] == m["name"] for x in matched_upstream):
                matched_upstream.append(m)

    if source_provider and not matched_upstream and locations:
        for loc in locations[:2]:
            val = loc.get("value", "")
            if len(val) >= 6:
                m = source_provider.find_function_by_anchor_string(rel_path, val)
                if m and not any(x["name"] == m["name"] for x in matched_upstream):
                    matched_upstream.append(m)
                    break

    lines = [
        "/*",
        f" * Reconstructed Source File: {rel_path}",
        f" * Binary Origin: {result.get('_origin_name', os.path.basename(binary_path))}",
        f" * Architecture: {arch} ({endian}-endian)",
    ]
    if line_numbers:
        lines.append(f" * Verified Line References (__bug_table): {sorted(set(line_numbers))}")
    if locations:
        lines.append(f" * Rodata String Offset: {locations[0]['offset']} (VA {locations[0]['va']})")
    count = len(decompiled_blocks) - len(failures)
    lines.append(f" * Reconstructed Functions: {count}")
    if matched_upstream:
        origin = source_provider.source_origin(rel_path)
        if report is not None:
            report["source_reference"] = {"location": origin, "match": "line or string anchor"}
        lines.append(" * Candidate source matches:")
        for mu in matched_upstream:
            lines.append(f" *   - {mu['name']}() at line {mu['start_line']}")
    lines.append(" */\n")

    if ext == ".h":
        guard = re.sub(r"[^A-Za-z0-9_]", "_", rel_path).upper()
        lines.append(f"#ifndef RAK_RECONSTRUCTED_{guard}")
        lines.append(f"#define RAK_RECONSTRUCTED_{guard}\n")
        tags = {tag for declaration in declarations for tag in re.findall(r"(?:struct|union) \w+", declaration)}
        lines.extend(tag + ";" for tag in sorted(tags))
        lines.extend(declarations)
        if decompiled_blocks:
            lines.append("\n" + "\n\n".join(decompiled_blocks))
        lines.append(f"\n#endif /* RAK_RECONSTRUCTED_{guard} */")
    elif ext == ".rs":
        lines.extend(_rust_ffi_skeleton(declarations, metadata, func_addrs[:max_funcs]))
        lines.append("// Recovered C pseudocode for reference during Rust reconstruction.\n")
        if decompiled_blocks:
            lines.append("\n" + "\n\n".join(decompiled_blocks))
    elif ext == ".s":
        lines.append("// Recovered instructions are included in the annotated listing below.")
    else:
        lines.append("#include <stdint.h>\n")
        if decompiled_blocks:
            lines.append("\n" + "\n\n".join(decompiled_blocks))
        else:
            lines.append("// No direct function entry point was isolated for this translation unit.")

    if matched_upstream:
        lines.append("\n\n/* ==========================================================================")
        lines.append(" * KERNEL SOURCE REFERENCE")
        lines.append(" * (Candidate source matched by line or string anchor)")
        lines.append(" * ========================================================================= */")
        if origin:
            lines.append(_comment(origin))
        for mu in matched_upstream:
            lines.append(f"\n// --- {mu['name']} ({rel_path}:{mu['start_line']}) ---")
            lines.append(_comment(mu["source_code"]))

    content = "\n".join(lines) + "\n"
    return content, count, func_addrs


def reconstruct_source_tree(
    binary_path: str,
    output_dir: str | None = None,
    progress_callback: Callable[[int, int, str], None] | None = None,
    max_files: int | None = None,
    max_funcs_per_file: int | None = 5,
    *,
    analysis: dict | None = None,
    system_map: str | None = None,
    btf_path: str | None = None,
    dwarf_reference: str | None = None,
    annotated_asm: bool = False,
    local_source_dir: str | None = None,
    offline: bool = False,
    source_paths: tuple[str, ...] | list[str] = (),
    source_loader: Callable[[list[str]], str | None] | None = None,
    strict_failures: bool = True,
) -> dict:
    """Reconstruct a native binary or its extracted Android kernel payload."""
    binary_path = os.path.abspath(binary_path)
    if not os.path.isfile(binary_path):
        raise OperationError(f"Binary file not found: {binary_path}")
    if max_funcs_per_file is not None and max_funcs_per_file < 1:
        raise OperationError("Functions per file must be positive or unlimited.")

    options = {
        "output_dir": output_dir, "progress_callback": progress_callback, "max_files": max_files,
        "max_funcs_per_file": max_funcs_per_file, "system_map": system_map, "btf_path": btf_path,
        "annotated_asm": annotated_asm, "local_source_dir": local_source_dir, "offline": offline,
        "dwarf_reference": dwarf_reference,
        "source_paths": source_paths,
        "source_loader": source_loader,
        "strict_failures": strict_failures,
    }
    with open(binary_path, "rb") as stream:
        magic = stream.read(8)
        if magic == b"VNDRBOOT":
            raise OperationError("Vendor boot images do not contain the kernel. Use boot.img or Image.")
        if magic == b"ANDROID!":
            payload = kernel_version.extract_kernel_payload_from_boot_img(binary_path, binary_core.MAX_BYTES)
        elif magic.startswith((
            b"\x1f\x8b", b"\xfd7zXZ\x00", b"BZh", b"\x28\xb5\x2f\xfd",
            b"\x04\x22\x4d\x18", b"\x02\x21\x4c\x18",
        )):
            stream.seek(0)
            payload = stream.read(binary_core.MAX_BYTES + 1)
        else:
            payload = None
    if magic != b"ANDROID!" and payload is None:
        if analysis is not None or binary_core.identify(binary_path):
            return _reconstruct(binary_path, binary_path, analysis=analysis, **options)
        raise OperationError("Unsupported binary format for source reconstruction")
    if not payload or len(payload) > binary_core.MAX_BYTES:
        raise OperationError("Missing, truncated, or oversized kernel payload")
    payload = kernel_version.decompress_kernel_stream(payload, max_output=binary_core.MAX_BYTES)
    if len(payload) > binary_core.MAX_BYTES:
        raise OperationError("Decompressed kernel exceeds the 512 MiB analysis limit")
    with tempfile.TemporaryDirectory(prefix="rak-kernel-") as folder:
        image = Path(folder) / "Image"
        image.write_bytes(payload)
        del payload
        kernel_info = kernel_version.inspect_kernel(image)
        if not kernel_info.is_kernel or not binary_core.identify(image):
            raise OperationError(kernel_info.error or "The payload has no recognized kernel image or banner.")
        return _reconstruct(str(image), binary_path, kernel_info=kernel_info, **options)


def _reconstruct(
    binary_path, origin, *, output_dir, progress_callback, max_files, max_funcs_per_file,
    system_map, btf_path, dwarf_reference, annotated_asm, local_source_dir, offline, source_paths, source_loader,
    strict_failures=True, analysis=None, kernel_info=None,
):
    result = dict(analysis) if analysis is not None else binary_core.analyze_file(binary_path)
    result.pop("_string_map", None)
    session = binary_session.BinarySession(binary_path, result.get("sha256"))

    # Inspect kernel version and initialize upstream source provider if applicable
    k_info = kernel_info or kernel_version.inspect_kernel(binary_path)
    k_info.file_path = origin
    reference_error = ""
    can_check_gki_map = k_info.is_kernel and system_map is None and result["format"] in {"ARM64 Image", "PE"}
    if can_check_gki_map:
        system_map = gki_checkout.analysis_system_map(
            k_info, session.sha256, os.path.getsize(binary_path), offline=True,
        )
    source_provider = None
    if source_loader is None and (k_info.base_version or local_source_dir):
        source_provider = kernel_source_provider.KernelSourceProvider(
            k_info, local_source_dir=local_source_dir, offline_mode=offline,
        )

    base_name = os.path.basename(origin)
    result["_origin_name"] = base_name
    if not output_dir:
        output_dir = os.path.join(os.path.dirname(origin), f"{base_name}_src")
    output_dir = os.path.abspath(output_dir)
    os.makedirs(output_dir, exist_ok=True)
    output_root = Path(output_dir).resolve()
    inputs = {Path(path).resolve() for path in (origin, system_map, btf_path, dwarf_reference) if path}

    def export_path(name):
        path = output_root / name
        resolved = path.resolve()
        if resolved != path or not resolved.is_relative_to(output_root) or resolved in inputs:
            raise OperationError(f"Reconstruction output would overwrite an input or follow a link: {path}")
        return path

    # Keep the previous reconstruction intact until the optional reference is verified.
    readme_path = export_path("README.md")
    initial_readme = (
        f"# Reconstructed Source Tree\n\nOrigin binary: `{base_name}`\nStatus: Decompiling and generating sources...\n"
    )
    if not dwarf_reference:
        readme_path.write_text(initial_readme, encoding="utf-8")

    raw_paths = collect_source_paths(result, binary_path)
    start_time = time.time()
    if progress_callback:
        progress_callback(0, len(raw_paths), "Analyzing symbols and path references...")
    metadata = _load_metadata(binary_path, result, btf_path, system_map)
    if can_check_gki_map and system_map is None and not metadata.recovered_symbols and not offline:
        if progress_callback:
            progress_callback(0, 1, "Checking for matching GKI build symbols...")
        try:
            system_map = gki_checkout.analysis_system_map(
                k_info, session.sha256, os.path.getsize(binary_path), offline=False,
            )
            if system_map is not None:
                inputs.add(Path(system_map).resolve())
                metadata = _load_metadata(binary_path, result, btf_path, system_map)
        except (OperationError, OSError) as error:
            reference_error = str(error)
            LOG.warning("GKI build symbols for %s\n%s", origin, error)
    if reference_error:
        metadata.warnings.append(reference_error)
    diagnostics, diagnostic_locations = _kernel_bug_references(binary_path, result, metadata.bug_table)
    if diagnostics:
        metadata.sources["bug_table"] = {"files": len(diagnostics), "references": sum(map(len, diagnostics.values()))}
        raw_paths = {path.casefold(): path for path in (*raw_paths, *diagnostics)}.values()
    selected_paths = {_clean_rel_path(path).casefold(): path for path in source_paths}
    dwarf_functions = {}
    if dwarf_reference:
        from core import kernel_dwarf

        if progress_callback:
            progress_callback(0, 1, "Verifying vmlinux and finding source functions...")
        dwarf_functions = kernel_dwarf.source_functions(
            dwarf_reference, result, metadata.functions, _clean_rel_path, selected_paths,
            limit=max_funcs_per_file, progress=progress_callback,
        )
        metadata.sources["dwarf"] = {"files": len(dwarf_functions), "functions": sum(map(len, dwarf_functions.values()))}
        raw_paths = {path.casefold(): path for path in (*raw_paths, *dwarf_functions)}.values()
        readme_path.write_text(initial_readme, encoding="utf-8")
    dwarf_owners = {address: path for path, addresses in dwarf_functions.items() for address in addresses}

    # Sort paths: prioritize genuine C/Rust drivers and subsystems first
    def _path_sort_key(p: str):
        p_low = p.lower()
        is_src = any(p_low.endswith(ext) for ext in (".c", ".rs", ".cpp", ".s"))
        is_h = any(p_low.endswith(ext) for ext in (".h", ".hpp", ".inc"))
        top_match = any(p_low.startswith(d + "/") for d in ("sound", "drivers", "fs", "kernel", "net", "arch", "crypto"))
        priority = 0 if (is_src and top_match) else (1 if is_src else (2 if is_h else 3))
        return (p not in dwarf_functions, p not in diagnostics, priority, p_low)

    paths = sorted((path for path in raw_paths if not selected_paths or path.casefold() in selected_paths),
                   key=_path_sort_key)
    if max_files and not k_info.is_kernel:
        paths = paths[:max_files]

    symbol_map_name = None
    if metadata.recovered_symbols:
        symbol_map_name = "System.map.recovered"
        width = int(result["header"]["class"]) // 4
        with export_path(symbol_map_name).open("w", encoding="ascii") as stream:
            stream.writelines(
                f"{row.address:0{width}x} {row.kind} {row.name}\n" for row in metadata.recovered_symbols
            )
    arch = str(result["header"]["abi"]).upper().removeprefix("EM_")
    has_asm = arch in {"AARCH64", "ARM64"}
    if annotated_asm and not has_asm:
        metadata.warnings.append("Annotated ASM currently requires AArch64.")

    # Pass 1: Resolve string locations and gather all target VAs using O(1) indexed lookup
    file_locs = {}
    primary_vas = set()
    cleaned_string_entries: dict[str, list[dict]] = {}
    if k_info.is_kernel:
        for entry in result.get("string_entries") or ():
            val = entry.get("value") or ""
            if "/" not in val and "\\" not in val:
                continue
            cleaned = _clean_rel_path(val)
            if cleaned:
                bucket = cleaned_string_entries.setdefault(cleaned.casefold(), [])
                if len(bucket) < 2:
                    bucket.append(entry)

    for rel_path in paths:
        if rel_path in diagnostics:
            file_locs[rel_path] = (diagnostic_locations[rel_path], [])
            continue
        if rel_path in dwarf_functions:
            file_locs[rel_path] = ([], [])
            continue
        indexed = cleaned_string_entries.get(rel_path.casefold()) if k_info.is_kernel else None
        if indexed:
            locations = []
            for entry in indexed[:2]:
                off = binary_xref._parse_hex(entry.get("offset", 0))
                va, sec_name = binary_xref.offset_to_va(result, off)
                locations.append({
                    "value": entry.get("value", ""),
                    "offset": hex(off),
                    "va": hex(va) if va is not None else "",
                    "va_int": va,
                    "section": sec_name,
                })
        else:
            locations = binary_xref.find_string_locations(
                binary_path, result, rel_path, limit=2, exact=k_info.is_kernel
            )
            if k_info.is_kernel:
                locations = [row for row in locations if _clean_rel_path(row["value"]) == rel_path]
        targets = [row["va_int"] for row in locations if row["va_int"] is not None]
        file_locs[rel_path] = (locations, targets)
        primary_vas.update(targets)

    # Scan all primary targets in ONE fast batched pass (Rust accelerated, no GIL contention)
    direct_xrefs = binary_xref.scan_xrefs(binary_path, result, list(primary_vas), max_hits=max(len(primary_vas) * 15, 100)) if primary_vas else []
    if k_info.is_kernel and direct_xrefs and not diagnostics:
        _annotate_arm64_warn_xrefs(binary_path, result, direct_xrefs)

    xrefs_by_target: dict[int, list[dict]] = {}
    for x in direct_xrefs:
        if diagnostics and x.get("kind", "").startswith("BUG_TABLE"):
            continue
        try:
            tgt_int = int(x.get("to", "0"), 16) if isinstance(x.get("to"), str) and str(x.get("to")).startswith("0x") else int(x.get("to", 0))
            xrefs_by_target.setdefault(tgt_int, []).append(x)
        except (ValueError, TypeError):
            pass

    # For files with no direct xrefs, gather their rodata clusters for a second fast batched pass
    cluster_vas = set()
    cluster_to_files: dict[int, list[str]] = {}
    for rel_path in (() if k_info.is_kernel else paths):
        _, targets = file_locs[rel_path]
        t_va = targets[0] if targets else None
        if t_va and not any(xrefs_by_target.get(target) for target in targets):
            for offset in range(-128, 256, 16):
                c_va = t_va + offset
                cluster_vas.add(c_va)
                cluster_to_files.setdefault(c_va, []).append(rel_path)

    if cluster_vas:
        cluster_xrefs = binary_xref.scan_xrefs(binary_path, result, list(cluster_vas), max_hits=max(len(cluster_vas) * 2, 100))
        for cx in cluster_xrefs:
            try:
                c_tgt = int(cx.get("to", "0"), 16) if isinstance(cx.get("to"), str) and str(cx.get("to")).startswith("0x") else int(cx.get("to", 0))
                for rpath in cluster_to_files.get(c_tgt, []):
                    _, targets = file_locs[rpath]
                    xrefs_by_target.setdefault(targets[0], []).append(cx)
            except (ValueError, TypeError):
                pass

    # Assemble file plans & collect all function addresses
    file_plans = {}
    all_needed_addrs = set()
    unverified_path_strings = []
    for rel_path in paths:
        locations, targets = file_locs[rel_path]
        xrefs = diagnostics.get(rel_path) or [xref for target in targets for xref in xrefs_by_target.get(target, ())]
        func_addrs = []
        diagnostic_functions = []
        line_numbers = []
        for x in xrefs:
            kind = x.get("kind", "")
            line = None
            if "BUG_TABLE:L" in kind or "WARN_SITE:L" in kind:
                try:
                    line = int(kind.split(":L")[1])
                except (ValueError, IndexError):
                    pass
            fa = x.get("function_address")
            if not fa and x.get("from"):
                try:
                    g = binary_xref.guess_function_start(binary_path, result, int(x["from"], 16))
                    if g:
                        fa = hex(g)
                except (ValueError, OSError):
                    fa = None
            if fa and dwarf_owners.get(fa, rel_path) != rel_path:
                continue
            if fa and fa not in func_addrs:
                func_addrs.append(fa)
            if line is not None and (fa or not k_info.is_kernel):
                line_numbers.append(line)
                if fa and fa not in diagnostic_functions:
                    diagnostic_functions.append(fa)

        if k_info.is_kernel and diagnostic_functions:
            func_addrs = diagnostic_functions
        if rel_path in dwarf_functions:
            func_addrs = dwarf_functions[rel_path]
        selected_funcs = func_addrs[:max_funcs_per_file]
        if k_info.is_kernel and (not is_source_filename(rel_path) or not selected_funcs):
            unverified_path_strings.append(rel_path)
            continue
        if k_info.is_kernel and max_files and len(file_plans) >= max_files:
            continue
        if not rel_path.lower().endswith(".s"):
            all_needed_addrs.update(int(address, 16) for address in selected_funcs)
        file_plans[rel_path] = (locations, selected_funcs, line_numbers, xrefs)
    missing_paths = selected_paths.keys() - {path.casefold() for path in file_plans}
    if missing_paths:
        missing = ", ".join(selected_paths[path] for path in sorted(missing_paths))
        raise OperationError(f"No source reconstruction plan for: {missing}")
    if source_loader is not None and file_plans:
        checkout = source_loader(list(file_plans))
        if checkout:
            source_provider = kernel_source_provider.KernelSourceProvider(
                k_info, local_source_dir=checkout, offline_mode=True, exact_local_only=True,
            )

    # -------------------------------------------------------------
    # PARALLEL DECOMPILATION: Multi-core workers for function batches
    # -------------------------------------------------------------
    needed_list = sorted(all_needed_addrs)
    batch_size = 32 if len(needed_list) > 64 else 16
    chunks = [needed_list[i:i + batch_size] for i in range(0, len(needed_list), batch_size)]
    if chunks and native_decompiler.status()["available"]:
        phase = "Decompiling kernel functions..."
        if progress_callback:
            progress_callback(0, len(chunks), phase)
        def warm_cache(addresses):
            try:
                session.decompile_native_batch(result, addresses)
            except (OperationError, OSError, kitchen_process.TimeoutExpired) as error:
                LOG.warning("Batch decompilation failed. Trying individual function exports.\n%s", error)

        worker_count = min(4, os.cpu_count() or 4, len(chunks))
        if worker_count > 1:
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=worker_count) as pool:
                futures = [pool.submit(copy_context().run, warm_cache, ch) for ch in chunks]
                for index, fut in enumerate(concurrent.futures.as_completed(futures), 1):
                    fut.result()
                    if progress_callback:
                        progress_callback(index, len(chunks), phase)
        else:
            for index, ch in enumerate(chunks, 1):
                warm_cache(ch)
                if progress_callback:
                    progress_callback(index, len(chunks), phase)

    # -------------------------------------------------------------
    # Export only the plans that survived attribution.
    # -------------------------------------------------------------
    written_files = []
    total_decompiled = 0
    total_failed = 0
    total_asm_errors = 0
    total_rewrites = 0
    last_emit = 0.0
    input_paths = {path.casefold() for path in file_plans}
    total = len(file_plans)

    for idx, (rel_path, plan) in enumerate(file_plans.items(), 1):
        locations, selected_funcs, line_numbers, xrefs = plan
        output_path = rel_path
        file_report = {}
        if k_info.is_kernel:
            has_bug = rel_path in diagnostics or any("BUG_TABLE:L" in x.get("kind", "") for x in xrefs)
            file_report["path_attribution"] = (
                "dwarf" if rel_path in dwarf_functions
                else "bug_table" if has_bug
                else "warn_site" if line_numbers
                else "direct_reference"
            )
            if file_report["path_attribution"] == "direct_reference":
                identity = hashlib.sha256(rel_path.encode("utf-8")).hexdigest()[:12]
                output_path = f"unattributed/{selected_funcs[0]}-{identity}{Path(rel_path).suffix.lower()}"
            file_report["output_path"] = output_path
        dest_file = export_path(output_path)
        dest_file.parent.mkdir(parents=True, exist_ok=True)
        content, n_funcs, _ = reconstruct_file_content(
            binary_path,
            result,
            rel_path,
            session=session,
            max_funcs=len(selected_funcs),
            locations=locations,
            func_addrs=selected_funcs,
            line_numbers=line_numbers,
            source_provider=source_provider,
            metadata=metadata,
            report=file_report,
            diagnostic_references=diagnostics.get(rel_path, ()),
        )
        is_asm = rel_path.lower().endswith(".s")
        if has_asm and selected_funcs and (is_asm or annotated_asm):
            blocks = []
            strings = {location["va"]: location["value"] for location in locations}
            for function_address in selected_funcs:
                address = int(function_address, 16)
                try:
                    blocks.append(binary_listing.render(
                        binary_path, result, address, metadata.functions.get(address, {}),
                        xrefs, strings, metadata.relocations,
                    ))
                except (OperationError, OSError) as error:
                    LOG.warning("Cannot disassemble %s at %#x\n%s", rel_path, address, error)
                    file_report.setdefault("asm_errors", []).append(f"{address:#x}: {error}")
                    total_asm_errors += 1
            if blocks:
                listing = "\n\n".join(blocks) + "\n"
                if is_asm:
                    content += "\n" + listing
                elif (output_path + ".asm").casefold() not in input_paths:
                    sidecar = export_path(output_path + ".asm")
                    sidecar.write_text(listing, encoding="utf-8")
                    file_report["annotated_asm"] = output_path + ".asm"
        dest_file.write_text(content, encoding="utf-8")

        written_files.append({
            "path": rel_path,
            "size": len(content),
            "functions": n_funcs,
            **file_report,
        })
        total_decompiled += n_funcs
        total_failed += file_report["failed_functions"]
        total_rewrites += file_report["typed_field_rewrites"]

        now = time.time()
        if progress_callback and (now - last_emit >= 0.08 or idx == total):
            progress_callback(idx, total, rel_path)
            last_emit = now

        time.sleep(0.001)  # Yield GIL to QtQuick rendering thread

    elapsed = round(time.time() - start_time, 2)

    # Keep the detected build configuration with the reconstruction.
    config_name = None
    if k_info.config_sha256:
        config_name = "kernel.config"
        export_path(config_name).write_bytes(k_info.config_text.encode("utf-8"))

    # Write manifest and README
    manifest = {
        "binary": base_name,
        "binary_path": origin,
        "format": result.get("format"),
        "sha256": result.get("sha256"),
        "origin_sha256": binary_core.fingerprint(origin) if origin != binary_path else result.get("sha256"),
        "architecture": result.get("header", {}).get("abi"),
        "kernel_info": k_info.to_dict() if (k_info and k_info.is_kernel) else None,
        "kernel_config": config_name,
        "unverified_path_strings": unverified_path_strings,
        "reconstructed_files_count": len(written_files),
        "total_decompiled_functions": total_decompiled,
        "failed_functions": total_failed,
        "failed_asm_listings": total_asm_errors,
        "typed_field_rewrites": total_rewrites,
        "metadata": metadata.sources,
        "recovered_system_map": symbol_map_name,
        "warnings": [*result.get("warnings", ()), *metadata.warnings],
        "elapsed_seconds": elapsed,
        "files": written_files,
    }
    manifest_path = export_path("reconstruction_manifest.json")
    with manifest_path.open("w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)

    readme_lines = [
        f"# Reconstructed Source Tree: {base_name}\n",
        "Generated by **Renn's Android Kitchen (RAK) Binary Reconstructor**.\n",
        f"- **Source Binary**: `{base_name}`",
        f"- **SHA-256**: `{result.get('sha256')}`",
        f"- **Format**: `{result.get('format')}`",
        f"- **Architecture**: `{result.get('header', {}).get('abi')}`",
    ]
    if k_info and k_info.is_kernel:
        readme_lines.append(f"- **Kernel Version**: `{k_info.version_string}`")
        readme_lines.append(f"- **Base Upstream / AOSP Branch**: `{k_info.aosp_branch}`")
        if k_info.compiler:
            readme_lines.append(f"- **Compiler**: `{k_info.compiler}`")
    readme_lines.extend([
        f"- **Total Files Reconstructed**: {len(written_files)}",
        f"- **Decompiled Functions**: {total_decompiled}",
        f"- **Function Export Failures**: {total_failed}",
        f"- **ASM Listing Failures**: {total_asm_errors}",
        f"- **Elapsed Time**: {elapsed}s\n",
        "### Directory Structure",
        "C pseudocode and upstream candidates are for inspection. Header declarations and field",
        "rewrites use build-specific BTF when available. Rust skeletons hold C references as comments.\n",
    ])
    if k_info.is_kernel:
        readme_lines.extend([
            "### Kernel file attribution",
            "Path strings without function evidence are recorded in the manifest.",
            "Direct code references can point to a different file. Those exports are in `unattributed/`.",
            "A `bug_table` attribution records an embedded source location for a diagnostic site.",
            "A `dwarf` attribution uses function owners from a matching vmlinux.",
            "The manifest retains the path and the output location of each export.",
            "",
            "### Compile the recovered C",
            "Run `rak binary kernel-check` with the original boot image or kernel file.",
            "Use the original source checkout, prepared Kbuild output, and matching Clang toolchain.",
            "Pass the checkout as `--kernel-source` and the output containing `compile_commands.json`",
            "as `--build-dir`. The build also needs `.config` and generated headers.",
            "Use Linux or WSL when the build paths are Linux paths. For Rust export, install C2Rust",
            "in that environment and add `--emit-rust` to the check and compile commands.",
            "Source references retrieved during reconstruction can come from a branch fallback.",
            "Obtain the full checkout and build inputs from the kernel's release or vendor.",
        ])
        if config_name:
            readme_lines.append("The extracted IKCONFIG settings are saved in `kernel.config`.")
    readme_content = "\n".join(readme_lines)
    readme_path.write_text(readme_content, encoding="utf-8")
    if strict_failures and (total_failed or total_asm_errors):
        raise OperationError(
            f"Reconstruction saved with {total_failed} function export failures and "
            f"{total_asm_errors} ASM listing failures. See {manifest_path}."
        )

    return {
        "output_dir": output_dir,
        "total_files": len(written_files),
        "total_functions": total_decompiled,
        "reconstructed_files_count": len(written_files),
        "total_decompiled_functions": total_decompiled,
        "failed_functions": total_failed,
        "typed_field_rewrites": total_rewrites,
        "metadata": metadata.sources,
        "kernel_detected": k_info.is_kernel,
        "recovered_system_map": str(output_root / symbol_map_name) if symbol_map_name else None,
        "elapsed": elapsed,
        "elapsed_seconds": elapsed,
        "manifest_path": str(manifest_path),
    }
