# SPDX-License-Identifier: MPL-2.0
"""Local cross-reference (Xref) discovery and path-to-code reconstruction."""
from __future__ import annotations

from bisect import bisect_right
import os
import struct

from core import binary_core


def _parse_hex(value) -> int:
    if isinstance(value, int):
        return value
    text = str(value or "").strip()
    if not text:
        return 0
    return int(text, 16) if text.lower().startswith("0x") else int(text)


def _section_ranges(result: dict) -> list[dict]:
    ranges = []
    for sec in result.get("sections", []):
        file_size = int(sec.get("file_size", sec.get("size", 0)) or 0)
        if sec.get("type") == "SHT_NOBITS" or file_size <= 0:
            continue
        file_offset = _parse_hex(sec.get("offset", 0))
        va = _parse_hex(sec.get("addr", 0))
        ranges.append({
            "name": sec.get("name", ""),
            "va": va,
            "offset": file_offset,
            "size": file_size,
            "flags": int(sec.get("flags", 0) or 0),
        })
    return ranges


def offset_to_va(result: dict, file_offset: int) -> tuple[int | None, str]:
    for sec in _section_ranges(result):
        if sec["offset"] <= file_offset < sec["offset"] + sec["size"]:
            if sec["va"] == 0:
                return None, sec["name"]
            return sec["va"] + (file_offset - sec["offset"]), sec["name"]
    return None, ""


def va_to_offset(result: dict, va: int) -> tuple[int | None, str]:
    for sec in _section_ranges(result):
        if sec["va"] and sec["va"] <= va < sec["va"] + sec["size"]:
            return sec["offset"] + (va - sec["va"]), sec["name"]
    return None, ""


def _get_string_map(result: dict, path: str) -> dict[str, list[dict]]:
    if "_string_map" in result:
        return result["_string_map"]
    entries = result.get("string_entries")
    if entries is None:
        entries = _scan_ascii_entries(path)
    smap: dict[str, list[dict]] = {}
    for entry in entries:
        val = (entry.get("value") or "").strip().lower()
        if val:
            smap.setdefault(val, []).append(entry)
            trimmed = val.lstrip("/")
            if trimmed != val:
                smap.setdefault(trimmed, []).append(entry)
            base = os.path.basename(val)
            if base and base != val and base != trimmed:
                smap.setdefault(base, []).append(entry)
    result["_string_map"] = smap
    return smap


def find_string_locations(
    path: str, result: dict, query: str, limit: int = 25, *, exact: bool = False
) -> list[dict]:
    needle = (query or "").strip()
    if not needle:
        return []
    needle_l = needle.lower()
    trimmed_l = needle_l.lstrip("/")
    base_l = os.path.basename(needle_l)

    # Fast O(1) indexed lookup first (millisecond speed for thousands of calls)
    smap = _get_string_map(result, path)
    ordered = smap.get(needle_l) or smap.get(trimmed_l) or (None if exact else (smap.get(base_l) if base_l else None))
    if ordered is None:
        ordered = []
        if not exact:
            entries = result.get("string_entries")
            if entries is None:
                entries = _scan_ascii_entries(path)
            for entry in entries:
                val = (entry.get("value") or "").lower()
                if needle_l in val:
                    ordered.append(entry)
                    if len(ordered) >= limit:
                        break

    # Zero-copy mmap fallback if string entries were truncated at MAX_ROWS
    if not ordered and path and os.path.isfile(path) and os.path.getsize(path) > 0:
        import mmap
        target_bytes = needle.encode("utf-8", errors="ignore")
        if target_bytes:
            try:
                with open(path, "rb") as stream, mmap.mmap(stream.fileno(), 0, access=mmap.ACCESS_READ) as mm:
                    pos = 0
                    size = len(mm)
                    while len(ordered) < limit:
                        idx = mm.find(target_bytes, pos)
                        if idx == -1:
                            break
                        end = idx
                        while end < size and 0x20 <= mm[end] <= 0x7E and end - idx < 512:
                            end += 1
                        start = idx
                        while start > 0 and 0x20 <= mm[start - 1] <= 0x7E and idx - start < 256:
                            start -= 1
                        ordered.append({
                            "value": mm[start:end].decode("utf-8", errors="ignore"),
                            "offset": hex(start),
                        })
                        pos = max(end, idx + len(target_bytes))
            except OSError:
                pass

    out = []
    for entry in ordered[:limit]:
        off = _parse_hex(entry.get("offset", 0))
        va, sec_name = offset_to_va(result, off)
        out.append({
            "value": entry.get("value", ""),
            "offset": hex(off),
            "va": hex(va) if va is not None else "",
            "va_int": va,
            "section": sec_name,
        })
    return out


def _scan_ascii_entries(path: str, minimum: int = 4, cap: int = 200_000) -> list[dict]:
    entries = []
    with open(path, "rb") as stream:
        data = stream.read()
    start = 0
    length = len(data)
    for i in range(length + 1):
        if i < length and 0x20 <= data[i] <= 0x7E:
            continue
        if i - start >= minimum:
            entries.append({
                "offset": hex(start),
                "length": i - start,
                "value": data[start:i].decode("ascii", errors="ignore"),
            })
            if len(entries) >= cap:
                break
        start = i + 1
    return entries


def _enclosing_function(va: int, functions: list[dict], addr_map: dict) -> dict | None:
    best = None
    for fn in functions:
        start = _parse_hex(fn.get("addr", fn.get("address", 0)))
        size = int(fn.get("size", 0) or 0)
        if start and size and start <= va < start + size:
            return {"name": fn.get("demangled") or fn.get("name") or hex(start), "address": hex(start), "source": fn.get("source", "symbol")}
        if start and start <= va and (best is None or start > best[0]):
            if va - start <= 0x4000:
                best = (start, fn.get("demangled") or fn.get("name") or hex(start), fn.get("source", "symbol"))
    if best is not None:
        return {"name": best[1], "address": hex(best[0]), "source": best[2]}
    if addr_map:
        nearest = None
        for addr, sym in addr_map.items():
            if addr <= va and va - addr <= 0x2000 and (nearest is None or addr > nearest[0]):
                nearest = (addr, sym)
        if nearest is not None:
            return {"name": nearest[1], "address": hex(nearest[0]), "source": "symbol"}
    return None


def guess_function_start(path: str, result: dict, va: int, limit: int = 0x10000) -> int | None:
    """Best-effort entry for stripped code: nearest preceding prologue.

    Known symbols win. AArch64 recognises PACIASP/BTI landing pads and the
    `stp x29,x30,[sp,#-N]!` frame push; x86-64 recognises `endbr64`/`push rbp`
    after a padding or return boundary.
    """
    for fn in result.get("functions", []):
        start = _parse_hex(fn.get("addr", fn.get("address", 0)))
        size = int(fn.get("size", 0) or 0)
        if start and size and start <= va < start + size:
            return start
    off, sec_name = va_to_offset(result, va)
    sec = next((s for s in _section_ranges(result) if s["name"] == sec_name), None)
    if off is None or sec is None or not (sec["flags"] & 0x4):
        return None
    header = result.get("header", {})
    abi = str(header.get("abi", "")).upper().replace("EM_", "")
    low = max(sec["va"], va - limit)
    with open(path, "rb") as stream:
        stream.seek(sec["offset"] + (low - sec["va"]))
        data = stream.read(va - low + 4)
    if abi in ("AARCH64", "ARM64"):
        fmt = "<I" if header.get("endianness", "little") == "little" else ">I"
        for pos in range(((va - low) & ~3), -1, -4):
            word = struct.unpack_from(fmt, data, pos)[0]
            if word in (0xD503233F, 0xD503237F, 0xD503245F, 0xD50324DF, 0xD503249F):  # paciasp/pacibsp/bti c|j|jc
                return low + pos
            if (word & 0xFFC07FFF) == 0xA9807BFD:  # stp x29,x30,[sp,#imm]!
                return low + pos
    elif abi in ("ARM", "ARM32"):
        fmt = "<I" if header.get("endianness", "little") == "little" else ">I"
        for pos in range(((va - low) & ~3), -1, -4):
            word = struct.unpack_from(fmt, data, pos)[0]
            if (word & 0xFFFF4000) == 0xE92D4000:  # push {..., lr}
                return low + pos
    elif abi in ("X86_64", "X64", "386", "X86"):
        pad = (0xCC, 0x90, 0xC3)
        for pos in range(va - low, 3, -1):
            if data[pos:pos + 4] == b"\xf3\x0f\x1e\xfa" and (pos == 0 or data[pos - 1] in pad):
                return low + pos
            if data[pos] == 0x55 and data[pos - 1] in pad and (data[pos + 1:pos + 4] == b"\x48\x89\xe5" or data[pos + 1:pos + 3] == b"\x89\xe5"):
                return low + pos
    return None


def _sign_extend(val: int, bits: int) -> int:
    sign = 1 << (bits - 1)
    return (val & (sign - 1)) - (val & sign)


def _scan_aarch64_xrefs(code: bytes, base_va: int, target_vas: set[int], endian: str, max_hits: int) -> list[tuple[int, int, str]]:
    hits = []
    fmt = "<I" if endian == "little" else ">I"
    pages = {va & ~0xFFF for va in target_vas}
    recent_adrp: dict[int, tuple[int, int]] = {}
    n = len(code) - (len(code) % 4)
    for off in range(0, n, 4):
        insn = struct.unpack_from(fmt, code, off)[0]
        pc = base_va + off
        # ADRP Xd, label: bit31=1, bits[28:24]=10000
        if (insn & 0x9F000000) == 0x90000000:
            rd = insn & 0x1F
            immlo = (insn >> 29) & 0x3
            immhi = (insn >> 5) & 0x7FFFF
            imm = _sign_extend((immhi << 2) | immlo, 21) << 12
            page = (pc & ~0xFFF) + imm
            if page in pages:
                recent_adrp[rd] = (pc, page)
            else:
                recent_adrp.pop(rd, None)
            continue
        # ADD Xd, Xn, #imm12 (64-bit, shift 0 or 12)
        if (insn & 0xFF800000) == 0x91000000:
            rd = insn & 0x1F
            rn = (insn >> 5) & 0x1F
            if rn in recent_adrp:
                _, page = recent_adrp[rn]
                shift = (insn >> 22) & 0x3
                imm12 = ((insn >> 10) & 0xFFF) << (12 if shift == 1 else 0)
                target = page + imm12
                if target in target_vas:
                    hits.append((pc, target, "ADRP+ADD"))
                    if len(hits) >= max_hits:
                        return hits
                if rd != rn:
                    continue
            recent_adrp.pop(rd, None)
            continue
        # LDR Xt/Wt/Dt, [Xn, #pimm]
        if (insn & 0x3B000000) == 0x39000000:
            rn = (insn >> 5) & 0x1F
            if rn in recent_adrp:
                _, page = recent_adrp[rn]
                size_bits = (insn >> 30) & 0x3
                imm12 = ((insn >> 10) & 0xFFF) << size_bits
                target = page + imm12
                if target in target_vas:
                    hits.append((pc, target, "ADRP+LDR"))
                    if len(hits) >= max_hits:
                        return hits
    return hits


def _scan_x86_64_xrefs(code: bytes, base_va: int, target_vas: set[int], max_hits: int) -> list[tuple[int, int, str]]:
    hits = []
    n = len(code)
    i = 0
    while i + 7 <= n:
        b0 = code[i]
        # REX.W (0x48..0x4f) + LEA (0x8d) + ModRM rip-rel (mod=00, rm=101 -> &0xc7==0x05)
        if 0x48 <= b0 <= 0x4F and code[i + 1] == 0x8D and (code[i + 2] & 0xC7) == 0x05:
            disp = struct.unpack_from("<i", code, i + 3)[0]
            pc = base_va + i
            target = pc + 7 + disp
            if target in target_vas:
                hits.append((pc, target, "LEA [RIP]"))
                if len(hits) >= max_hits:
                    return hits
            i += 7
            continue
        i += 1
    return hits


def _scan_pointer_xrefs(data: bytes, base_va: int, target_vas: set[int], bits: int, endian: str, max_hits: int) -> list[tuple[int, int, str]]:
    hits = []
    step = 8 if bits == 64 else 4
    fmt = ("<Q" if endian == "little" else ">Q") if bits == 64 else ("<I" if endian == "little" else ">I")
    n = len(data) - (len(data) % step)
    for off in range(0, n, step):
        ptr = struct.unpack_from(fmt, data, off)[0]
        if ptr in target_vas:
            hits.append((base_va + off, ptr, f"PTR{bits}"))
            if len(hits) >= max_hits:
                return hits
    return hits


def _scan_relative32_xrefs(data: bytes, base_va: int, target_vas: set[int], endian: str, max_hits: int) -> list[tuple[int, int, str]]:
    hits = []
    fmt_i = "<i" if endian == "little" else ">i"
    fmt_h = "<H" if endian == "little" else ">H"
    n = len(data) - (len(data) % 4)
    for off in range(0, n, 4):
        disp = struct.unpack_from(fmt_i, data, off)[0]
        tbl_va = base_va + off
        tgt = tbl_va + disp
        if tgt in target_vas:
            if off >= 4:
                bug_disp = struct.unpack_from(fmt_i, data, off - 4)[0]
                code_va = (tbl_va - 4) + bug_disp
                line = struct.unpack_from(fmt_h, data, off + 4)[0] if off + 6 <= len(data) else 0
                kind = f"BUG_TABLE:L{line}" if line else "BUG_TABLE"
                hits.append((code_va, tgt, kind))
            else:
                hits.append((tbl_va, tgt, "REL32_DISP"))
            if len(hits) >= max_hits:
                return hits
    return hits


def scan_xrefs(path: str, result: dict, target_vas: list[int] | set[int], *, max_hits: int = 40) -> list[dict]:
    vas = [int(va) for va in target_vas if va]
    if not vas or not os.path.isfile(path):
        return []
    header = result.get("header", {})
    abi = str(header.get("abi", "")).upper().replace("EM_", "")
    bits = int(header.get("bits", header.get("class", 64)) or 64)
    endian = str(header.get("endianness", "little")).lower()
    functions = result.get("functions", [])
    addr_map = result.get("addr_symbol_map", {})
    starts = result.get("_function_starts")

    def enclosing(pc):
        if starts is None:
            return _enclosing_function(pc, functions, addr_map)
        index = bisect_right(starts, pc) - 1
        if index >= 0:
            row = functions[index]
            length = row["size"] or 0x4000
            if pc < row["addr"] + length:
                return {"address": hex(row["addr"]), "name": row["demangled"] or row["name"]}
        return None

    # Native Rust acceleration if available (sub-millisecond scanning, releases Python GIL)
    try:
        from core import native_backend
        native = native_backend._get_native()
        if native is not None and hasattr(native, "binary_fast_scan_xrefs_json"):
            import json
            raw_base = None
            if result.get("format") == "ARM64 Image" and result.get("sections"):
                raw_base = _parse_hex(result["sections"][0].get("addr", 0))
            try:
                raw_json = native.binary_fast_scan_xrefs_json(path, vas, abi, endian, max_hits, raw_base)
            except TypeError:
                if raw_base is not None:
                    raise
                raw_json = native.binary_fast_scan_xrefs_json(path, vas, abi, endian, max_hits)
            raw_hits = json.loads(raw_json)
            out = []
            for item in raw_hits:
                pc = int(item["from"], 16) if isinstance(item["from"], str) and item["from"].startswith("0x") else int(item["from"])
                tgt = int(item["to"], 16) if isinstance(item["to"], str) and item["to"].startswith("0x") else int(item["to"])
                fn = enclosing(pc)
                fa = (fn["address"] if fn else "") or item.get("function_address") or ""
                fn_name = fn["name"] if fn else ""
                out.append({
                    "from": hex(pc),
                    "to": hex(tgt),
                    "kind": item.get("kind", ""),
                    "section": item.get("section", ""),
                    "function": fn_name,
                    "function_address": fa,
                })
            return out
    except Exception:
        pass

    raw_hits: list[tuple[int, int, str, str]] = []
    with open(path, "rb") as stream:
        for sec in _section_ranges(result):
            if len(raw_hits) >= max_hits:
                break
            if sec["va"] == 0 or sec["size"] > 64 * 1024 * 1024:
                continue
            is_exec = bool(sec["flags"] & 0x4)
            is_data = not is_exec and (bool(sec["flags"] & 0x3) or sec["name"] in (".rodata", ".data", ".data.rel.ro", "__bug_table", ".init.rodata", ".rela.dyn", ".rdata"))
            if not (is_exec or is_data):
                continue
            stream.seek(sec["offset"])
            chunk = stream.read(sec["size"])
            rem = max_hits - len(raw_hits)
            if is_exec:
                if abi in ("AARCH64", "ARM64"):
                    for pc, tgt, kind in _scan_aarch64_xrefs(chunk, sec["va"], vas, endian, rem):
                        raw_hits.append((pc, tgt, kind, sec["name"]))
                elif abi in ("X86_64", "X64") and endian == "little":
                    for pc, tgt, kind in _scan_x86_64_xrefs(chunk, sec["va"], vas, rem):
                        raw_hits.append((pc, tgt, kind, sec["name"]))
            elif is_data:
                for pc, tgt, kind in _scan_pointer_xrefs(chunk, sec["va"], vas, bits, endian, rem):
                    raw_hits.append((pc, tgt, kind, sec["name"]))
                if len(raw_hits) < max_hits:
                    rem = max_hits - len(raw_hits)
                    for pc, tgt, kind in _scan_relative32_xrefs(chunk, sec["va"], vas, endian, rem):
                        raw_hits.append((pc, tgt, kind, sec["name"]))

    out = []
    for pc, tgt, kind, sec_name in raw_hits:
        fn = enclosing(pc)
        out.append({
            "from": hex(pc),
            "to": hex(tgt),
            "kind": kind,
            "section": sec_name,
            "function": fn["name"] if fn else "",
            "function_address": fn["address"] if fn else "",
        })
    return out


def disassemble_around(path: str, result: dict, va: int, before: int = 24, after: int = 56) -> str:
    off, sec_name = va_to_offset(result, va)
    if off is None:
        return ""
    sec = next((s for s in _section_ranges(result) if s["name"] == sec_name), None)
    if sec is None or not (sec["flags"] & 0x4):
        return ""
    start_va = max(sec["va"], va - before)
    header = result.get("header", {})
    abi = str(header.get("abi", "")).upper().replace("EM_", "")
    if abi in ("AARCH64", "ARM64", "ARM"):
        start_va &= ~0x3
    length = min(before + after, (sec["va"] + sec["size"]) - start_va)
    if length <= 0:
        return ""
    try:
        return binary_core.disassemble_va(path, result, start_va, length, highlight_va=va)
    except Exception:
        return ""


def _ghidra_string_xrefs(ghidra_index: dict | None, query: str) -> list[dict]:
    if not isinstance(ghidra_index, dict):
        return []
    needle = (query or "").strip().lower().lstrip("/")
    if not needle:
        return []
    out = []
    for item in ghidra_index.get("string_xrefs", []):
        val = str(item.get("value", ""))
        val_l = val.lower().lstrip("/")
        if val_l == needle or val_l.endswith("/" + needle) or needle in val_l:
            for ref in item.get("refs", []):
                out.append({
                    "from": ref.get("from", ""),
                    "to": item.get("address", ""),
                    "kind": "GHIDRA_XREF",
                    "section": "",
                    "function": ref.get("function", ""),
                    "function_address": ref.get("function_address", ""),
                    "value": val,
                })
    return out


def reconstruct_path_context(
    path: str,
    result: dict,
    query: str,
    *,
    ghidra_index: dict | None = None,
    cached_functions: dict | None = None,
    decompile=None,
) -> dict:
    locations = find_string_locations(path, result, query)
    target_vas = [loc["va_int"] for loc in locations if loc.get("va_int")]
    local_xrefs = scan_xrefs(path, result, target_vas)
    ghidra_xrefs = _ghidra_string_xrefs(ghidra_index, query)

    seen = set()
    xrefs = []
    for row in ghidra_xrefs + local_xrefs:
        key = (row.get("from", "").lower(), row.get("to", "").lower())
        if key in seen:
            continue
        seen.add(key)
        xrefs.append(row)

    # Stripped images (kernels) have no sizes/symbols: recover entries from prologues.
    for row in xrefs[:40]:
        if row.get("function_address"):
            continue
        try:
            start = guess_function_start(path, result, _parse_hex(row["from"]))
        except (ValueError, OSError):
            start = None
        if start:
            row["function_address"] = hex(start)
            row["function"] = row.get("function") or f"FUN_{start:x}"
            row["function_guessed"] = True

    disasm_snippets = []
    for row in xrefs[:4]:
        try:
            va = _parse_hex(row["from"])
        except ValueError:
            continue
        snippet = disassemble_around(path, result, va)
        if snippet:
            fn_label = row.get("function") or row.get("from")
            disasm_snippets.append(f"; Xref from {fn_label} ({row['kind']} -> {row['to']})\n{snippet}")

    pseudocode_blocks = []
    decompile_notes = []
    func_addrs = []
    for row in xrefs:
        faddr = row.get("function_address")
        if faddr and faddr not in func_addrs:
            func_addrs.append(faddr)
    for faddr in func_addrs[:3]:
        key = faddr.lower().removeprefix("0x").lstrip("0") or "0"
        detail = (cached_functions or {}).get(key)
        if detail and detail.get("pseudocode"):
            fname = detail.get("function", {}).get("name", faddr)
            pseudocode_blocks.append(f"// {fname} ({faddr})\n{detail['pseudocode']}")
        elif decompile is not None:
            try:
                native = decompile(faddr)
                pseudocode_blocks.append(f"// {native.get('name', faddr)} ({faddr}) [native decompiler]\n{native['pseudocode']}")
            except Exception as error:  # reported in-line; Xref data stays usable
                decompile_notes.append(f"  {faddr}: {str(error).splitlines()[0] if str(error) else 'failed'}")

    lines = [f"Target Path Reference: {query}"]
    if locations:
        lines.append("\n[String Locations]")
        for loc in locations[:10]:
            va_txt = f"VA {loc['va']}" if loc["va"] else "no VA"
            sec_txt = loc["section"] or "raw"
            lines.append(f"  - {loc['value']}  @ offset {loc['offset']} ({va_txt}, section {sec_txt})")
    else:
        lines.append("\n[String Locations]\n  No direct ASCII string match found.")

    lines.append("\n[Cross-References (Xrefs)]")
    if xrefs:
        for row in xrefs[:20]:
            guessed = " (entry guessed from prologue)" if row.get("function_guessed") else ""
            fn_part = f"{row['function']} ({row['function_address']}){guessed}" if row.get("function") else "(unnamed function)"
            lines.append(f"  - {row['from']}  [{row['kind']}] -> {row['to']}  in {fn_part}")
    else:
        lines.append("  No direct code Xrefs found (the string may be reached through relative tables, __bug_table offsets or runtime-built paths).")

    if pseudocode_blocks:
        lines.append("\n[Decompiled Pseudo-C]")
        lines.append("\n\n".join(pseudocode_blocks))
    if decompile_notes:
        lines.append("\n[Decompiler notes]")
        lines.extend(decompile_notes)
    if disasm_snippets:
        lines.append("\n[Disassembly Around Xrefs]")
        lines.append("\n\n".join(disasm_snippets))

    summary_text = "\n".join(lines)
    agent_prompt = (
        f"Reconstruct and explain the behavior associated with `{query}` in `{os.path.basename(path)}` "
        f"using Binary Lab (`rak binary` / Ghidra / disassembly evidence below):\n\n{summary_text}"
    )
    return {
        "query": query,
        "locations": [{k: v for k, v in loc.items() if k != "va_int"} for loc in locations],
        "xrefs": xrefs,
        "disassembly": "\n\n".join(disasm_snippets),
        "pseudocode": "\n\n".join(pseudocode_blocks),
        "function_addresses": func_addrs,
        "summary_text": summary_text,
        "agent_prompt": agent_prompt,
    }


def search_bytes(path: str, result: dict, pattern: str, *, hex_mode: bool = False, limit: int = 64) -> list[dict]:
    if not pattern:
        return []
    if hex_mode:
        cleaned = "".join(pattern.split()).removeprefix("0x")
        if len(cleaned) % 2 != 0:
            raise ValueError("Hex byte pattern must have an even number of digits")
        needle = bytes.fromhex(cleaned)
    else:
        needle = pattern.encode("utf-8")
    if not needle:
        return []
    with open(path, "rb") as stream:
        data = stream.read()
    functions = result.get("functions", [])
    addr_map = result.get("addr_symbol_map", {})
    exec_sections = {s["name"] for s in _section_ranges(result) if s["flags"] & 0x4}
    hits = []
    start = 0
    while len(hits) < limit:
        idx = data.find(needle, start)
        if idx == -1:
            break
        va, sec_name = offset_to_va(result, idx)
        fn = _enclosing_function(va, functions, addr_map) if (va is not None and sec_name in exec_sections) else None
        hits.append({
            "offset": hex(idx),
            "va": hex(va) if va is not None else "",
            "section": sec_name,
            "function": fn["name"] if fn else "",
            "function_address": fn["address"] if fn else "",
        })
        start = idx + 1
    return hits
