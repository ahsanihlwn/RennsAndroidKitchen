# SPDX-License-Identifier: MPL-2.0
"""Decompile functions with Ghidra's vendored Apache-2.0 C++ decompiler.

No JVM or Ghidra install is needed. The console decompiler (`decomp_cli`)
receives a <binaryimage> XML built from the ELF's mapped sections, SLEIGH tables
for the target architecture, and loader symbols. Build it with
`python tools/build_ghidra_native.py`.
"""
from __future__ import annotations

import os
from pathlib import Path
import re
import tempfile
from xml.sax.saxutils import quoteattr

from core import binary_core, kitchen_process, native_backend
from core.command_context import OperationError

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "build" / "ghidra_native" / "runtime"
EXE = "decomp_cli.exe" if os.name == "nt" else "decomp_cli"

LANGUAGES = {
    ("AARCH64", "little"): "AARCH64:LE:64:v8A",
    ("ARM64", "little"): "AARCH64:LE:64:v8A",
    ("X86_64", "little"): "x86:LE:64:default",
    ("X64", "little"): "x86:LE:64:default",
    ("386", "little"): "x86:LE:32:default",
    ("X86", "little"): "x86:LE:32:default",
    ("ARM", "little"): "ARM:LE:32:v8",
}
SHF_ALLOC = 0x2
WHOLE_SECTION_LIMIT = 16 * 1024 * 1024
WINDOW = 4 * 1024 * 1024
MAX_SYMBOLS = 60000
UNMAPPED_BYTES = re.compile(r"Low-level ERROR: Bytes at (0x[0-9a-f]+) are not mapped", re.IGNORECASE)


def runtime_dir() -> Path:
    override = os.environ.get("RAK_GHIDRA_NATIVE")
    return Path(override) if override else RUNTIME


def status() -> dict:
    exe = runtime_dir() / EXE
    ready = exe.is_file() and any((runtime_dir() / "specs").rglob("*.sla")) if exe.is_file() else False
    return {
        "available": bool(ready),
        "launcher": str(exe) if exe.is_file() else "",
        "message": "Native Ghidra decompiler ready (no JVM)" if ready else
                   "Build it once: python tools/build_ghidra_native.py",
    }


def language_for(result: dict) -> str:
    header = result["header"]
    raw_abi = str(header.get("abi") or header.get("machine", "")).upper()
    cleaned = raw_abi.replace("EM_", "").replace("IMAGE_FILE_MACHINE_", "").replace("PE_", "").replace("0X", "")
    if cleaned in ("AA64", "ARM64", "AARCH64"):
        abi = "AARCH64"
    elif cleaned in ("8664", "AMD64", "X86_64", "X64"):
        abi = "X86_64"
    elif cleaned in ("14C", "I386", "386", "X86"):
        abi = "X86"
    elif cleaned in ("1C0", "1C4", "ARM", "ARM32", "THUMB"):
        abi = "ARM"
    else:
        abi = cleaned
    endian = str(header.get("endianness", "little")).lower()
    key = (abi, endian)
    if key not in LANGUAGES:
        raise OperationError(f"Native decompiler does not support {raw_abi} ({endian}-endian) yet")
    return LANGUAGES[key]


def _sections(result: dict, target: int):
    chosen = []
    for sec in result.get("sections", []):
        size = int(sec.get("file_size", sec.get("size", 0)) or 0)
        if sec.get("type") == "SHT_NOBITS" or size <= 0 or not (int(sec.get("flags", 0)) & SHF_ALLOC):
            continue
        va = int(sec["addr"], 16)
        if not va:
            continue
        offset = int(sec["offset"], 16)
        start, length = 0, size
        if size > WHOLE_SECTION_LIMIT:
            if not va <= target < va + size:
                continue
            start = max(0, target - va - WINDOW // 2)
            length = min(size - start, WINDOW)
        chosen.append((va + start, offset + start, length, bool(int(sec.get("flags", 0)) & 0x4)))
    return chosen


def _symbol_name(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.$@]", "_", text)[:120] or "sym"


def build_image(path: str, result: dict, target: int, references=()) -> tuple[str, str]:
    xml, names = build_image_batch(path, result, [target], references=references)
    return xml, names[target]


def _merge_chunks(chunks):
    merged = []
    for address, offset, size, executable in sorted(chunks):
        if merged:
            first, position, length, flags = merged[-1]
            end = first + length
            same_mapping = offset - position == address - first and flags == executable
            if address <= end and same_mapping:
                merged[-1] = (first, position, max(end, address + size) - first, flags)
                continue
            if address < end:
                # Preserve ordering when distinct sections map conflicting bytes at the same VA.
                return chunks
        merged.append((address, offset, size, executable))
    return merged


def _discover_referenced_symbols(
    path: str, result: dict, targets: list[int], func_by_addr: dict[int, dict], addr_map: dict
) -> dict[int, str]:
    """Scan instruction windows around targets so distant BL callees & ADRP globals get real symbol names."""
    extra: dict[int, str] = {}
    header = result.get("header", {})
    abi = str(header.get("abi", "")).upper().replace("EM_", "")
    if abi not in ("AARCH64", "ARM64") or str(header.get("endianness", "little")).lower() != "little":
        return extra
    from core import binary_xref
    import struct

    try:
        with open(path, "rb") as stream:
            for target in targets:
                off, _ = binary_xref.va_to_offset(result, target)
                if off is None:
                    continue
                stream.seek(off)
                code = stream.read(8192)
                n = len(code) & ~3
                recent_page: dict[int, int] = {}
                for pos in range(0, n, 4):
                    insn = struct.unpack_from("<I", code, pos)[0]
                    pc = target + pos
                    # B / BL imm26
                    if (insn & 0x7C000000) == 0x14000000:
                        imm26 = insn & 0x03FFFFFF
                        if imm26 & (1 << 25):
                            imm26 -= 1 << 26
                        callee = pc + (imm26 << 2)
                        fn = func_by_addr.get(callee)
                        if fn is not None:
                            extra.setdefault(callee, _symbol_name(fn.get("demangled") or fn.get("name") or ""))
                        elif callee in addr_map:
                            extra.setdefault(callee, _symbol_name(str(addr_map[callee])))
                        continue
                    # ADRP Xd, page
                    if (insn & 0x9F000000) == 0x90000000:
                        rd = insn & 0x1F
                        immlo = (insn >> 29) & 0x3
                        immhi = (insn >> 5) & 0x7FFFF
                        val21 = (immhi << 2) | immlo
                        if val21 & (1 << 20):
                            val21 -= 1 << 21
                        recent_page[rd] = (pc & ~0xFFF) + (val21 << 12)
                        continue
                    # ADD Xd, Xn, #imm12
                    if (insn & 0xFF800000) == 0x91000000:
                        rn = (insn >> 5) & 0x1F
                        rd = insn & 0x1F
                        if rn in recent_page:
                            shift = 12 if ((insn >> 22) & 0x3) == 1 else 0
                            sym_va = recent_page[rn] + (((insn >> 10) & 0xFFF) << shift)
                            if sym_va in addr_map:
                                extra.setdefault(sym_va, _symbol_name(str(addr_map[sym_va])))
                        if rd == rn:
                            recent_page.pop(rd, None)
                        continue
                    # LDR Xt/Wt, [Xn, #pimm]
                    if (insn & 0x3B000000) == 0x39000000:
                        rn = (insn >> 5) & 0x1F
                        if rn in recent_page:
                            size_bits = (insn >> 30) & 0x3
                            sym_va = recent_page[rn] + (((insn >> 10) & 0xFFF) << size_bits)
                            if sym_va in addr_map:
                                extra.setdefault(sym_va, _symbol_name(str(addr_map[sym_va])))
    except OSError:
        pass
    return extra


def build_image_batch(path: str, result: dict, targets: list[int], *, references=()) -> tuple[str, dict[int, str]]:
    """Return (image XML, mapping of target addr -> symbol name)."""
    # Collect required sections across all targets first so large binaries can scope local symbols
    all_chunks = {}
    for addr in (*targets, *references):
        for va, offset, length, is_exec in _sections(result, addr):
            all_chunks[(va, offset, length, is_exec)] = None
    merged_ranges = _merge_chunks(list(all_chunks))

    functions = result.get("functions", [])
    addr_map = result.get("addr_symbol_map", {})
    names = {}
    target_set = set(targets)

    if len(functions) <= 4096:
        for fn in functions:
            addr = fn.get("addr")
            if addr in target_set:
                names.setdefault(addr, _symbol_name(fn.get("demangled") or fn.get("name") or ""))
        for fn in functions:
            if len(names) >= MAX_SYMBOLS:
                break
            addr = int(fn.get("addr", 0) or 0)
            if addr and len(names) < MAX_SYMBOLS:
                names.setdefault(addr, _symbol_name(fn.get("demangled") or fn.get("name") or ""))
    else:
        func_by_addr = {}
        for fn in functions:
            addr = int(fn.get("addr", 0) or 0)
            if addr:
                func_by_addr[addr] = fn
                if addr in target_set:
                    names.setdefault(addr, _symbol_name(fn.get("demangled") or fn.get("name") or ""))
        # 1. Inject all called functions & referenced global symbols even outside the 4 MB window
        for addr, sym in _discover_referenced_symbols(path, result, targets, func_by_addr, addr_map).items():
            if len(names) < MAX_SYMBOLS:
                names.setdefault(addr, sym)
        # 2. Inject functions residing inside the mapped bytechunk windows
        for fn in functions:
            if len(names) >= 24000:
                break
            addr = int(fn.get("addr", 0) or 0)
            if addr and any(va <= addr < va + length for va, _, length, _ in merged_ranges):
                names.setdefault(addr, _symbol_name(fn.get("demangled") or fn.get("name") or ""))

    for t in targets:
        names.setdefault(t, f"FUN_{t:x}")

    used, unique = set(), {}
    for addr, name in names.items():
        if name in used:
            name = f"{name}_{addr:x}"
        used.add(name)
        unique[addr] = name

    target_names = {t: unique[t] for t in targets}

    parts = [f'<binaryimage arch="{language_for(result)}">']
    ranges = [(va, offset, length) for va, offset, length, _ in merged_ranges]
    chunks = native_backend.binary_image_bytechunks(path, ranges)
    if chunks is None:
        with open(path, "rb") as stream:
            for va, offset, length in ranges:
                stream.seek(offset)
                data = stream.read(length)
                body = "\n".join(data[i:i + 64].hex() for i in range(0, len(data), 64))
                parts.append(f'<bytechunk space="ram" offset="{va:#x}" readonly="true">\n{body}\n</bytechunk>')
    elif chunks:
        parts.append(chunks)
    for addr, name in unique.items():
        parts.append(f'<symbol space="ram" offset="{addr:#x}" name={quoteattr(name)}/>')
    parts.append("</binaryimage>")
    return "\n".join(parts), target_names


def _run_image(launcher, xml, instructions, timeout):
    runtime = runtime_dir()
    work = runtime / "work"
    work.mkdir(parents=True, exist_ok=True)
    image = None
    try:
        with tempfile.NamedTemporaryFile(dir=work, prefix="image-", suffix=".xml", mode="w",
                                         encoding="ascii", delete=False) as stream:
            image = Path(stream.name)
            stream.write(xml)
        specs = sorted(path for path in (runtime / "specs").iterdir() if path.is_dir())
        command = [launcher]
        for directory in specs:
            command.extend(("-s", str(directory)))
        script = f"load file {image}\n{instructions}\nquit\n"
        return kitchen_process.run(command, input=script, capture_output=True, text=True,
                                   encoding="utf-8", errors="replace", timeout=timeout,
                                   creationflags=kitchen_process.CREATE_NO_WINDOW if os.name == "nt" else 0)
    finally:
        if image is not None:
            image.unlink(missing_ok=True)


def decompile_batch(path: str, result: dict, targets: list[int], timeout: int = 180) -> dict[int, dict]:
    """Decompile multiple functions in a single process run, saving huge startup overhead."""
    targets = [t for t in targets if t]
    if not targets:
        return {}
    state = status()
    if not state["available"]:
        raise OperationError(state["message"])
    if result.get("format") not in ("ELF", "PE", "Mach-O", "ARM64 Image"):
        raise OperationError("Native decompiler requires an ELF, PE, Mach-O, or raw ARM64 Image")

    xml, names_map = build_image_batch(path, result, targets)
    instructions = "\n".join(f"load function {names_map[target]}\ndecompile\nprint C" for target in targets)
    process = _run_image(state["launcher"], xml, instructions, timeout)

    out = process.stdout or ""
    if process.returncode:
        raise OperationError(f"Native decompiler failed (exit {process.returncode}):\n{out}{process.stderr or ''}")
    parts = out.split("print C")
    results = {}
    for i, target in enumerate(targets):
        if i + 1 < len(parts):
            chunk = parts[i + 1]
            end = chunk.find("[decomp]>")
            code = (chunk if end < 0 else chunk[:end]).strip("\r\n ")
            if "Execution error" in code or "ERROR" in code or not code:
                continue
            name = names_map[target]
            signature = next((line.strip() for line in code.splitlines() if line.strip()), name)
            results[target] = {
                "name": name,
                "address": hex(target),
                "signature": signature,
                "pseudocode": code,
            }
    if not results:
        raise OperationError(f"Native decompiler produced no batch output:\n{out}{process.stderr or ''}")
    return results


def _extract(stdout: str) -> str:
    marker = "print C"
    index = stdout.rfind(marker)
    if index < 0:
        return ""
    tail = stdout[index + len(marker):]
    end = tail.find("[decomp]>")
    return (tail if end < 0 else tail[:end]).strip("\r\n ")


def decompile(path: str, result: dict, target: int, timeout: int = 180) -> dict:
    state = status()
    if not state["available"]:
        raise OperationError(state["message"])
    if result.get("format") not in ("ELF", "PE", "Mach-O", "ARM64 Image"):
        raise OperationError("Native decompiler requires an ELF, PE, Mach-O, or raw ARM64 Image")
    references = []
    diagnostics = []
    while True:
        xml, name = build_image(path, result, target, references)
        process = _run_image(state["launcher"], xml, f"load function {name}\ndecompile\nprint C", timeout)
        out = process.stdout or ""
        if not process.returncode and "Execution error" not in out and "ERROR" not in out.split("print C")[0]:
            break
        detail = out + (process.stderr or "")
        diagnostics.append(detail)
        unmapped = UNMAPPED_BYTES.search(detail)
        if not unmapped or len(references) == 2:
            raise OperationError("Native decompiler failed:\n" + "\n".join(diagnostics))
        address = int(unmapped[1], 16)
        mapped = any(va <= address < va + size for current in (target, *references)
                     for va, _, size, _ in _sections(result, current))
        if mapped or not any(va <= address < va + size for va, _, size, _ in _sections(result, address)):
            raise OperationError("Native decompiler failed:\n" + "\n".join(diagnostics))
        references.append(address)
    code = _extract(out)
    if not code:
        raise OperationError("Native decompiler returned no code:\n" + out + (process.stderr or ""))
    signature = next((line.strip() for line in code.splitlines() if line.strip()), name)
    return {"name": name, "address": hex(target), "signature": signature, "pseudocode": code}
