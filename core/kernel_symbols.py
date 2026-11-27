# SPDX-License-Identifier: MPL-2.0
"""Decode in-image kallsyms tables located by ELF symbols or a same-build System.map."""
from bisect import bisect_right
from dataclasses import dataclass
import mmap
import os
import re
import struct
import time

from core import binary_xref, kernel_version


MAX_SYMBOLS = 1_000_000
LABELS = {
    "num_syms", "names", "markers", "token_table", "token_index", "addresses", "offsets", "relative_base",
}
NAMES = re.compile(rb"[A-Za-z?][!-~]{1,1023}\Z")


@dataclass(frozen=True)
class Symbol:
    address: int
    kind: str
    name: str


def read_system_map(path):
    symbols = []
    with open(path, encoding="ascii", errors="replace") as stream:
        for line in stream:
            parts = line.split()
            if len(parts) != 3:
                continue
            address, kind, name = parts
            if len(kind) == 1 and re.fullmatch(r"[0-9A-Fa-f]{1,16}", address):
                symbols.append(Symbol(int(address, 16), kind, name))
                if len(symbols) > MAX_SYMBOLS:
                    raise ValueError("System.map exceeds symbol limit")
    if not symbols:
        raise ValueError("System.map contains no Linux symbols")
    return symbols


def _decode(
    data, tables, endian, pointer_size, absolute_percpu, long_names,
    *, marker_size=4, rela_addresses=None,
):
    prefix = "<" if endian == "little" else ">"
    word = "Q" if pointer_size == 8 else "I"
    marker_code = "Q" if marker_size == 8 else "I"

    def table(label, length):
        start, end = tables[label]
        if not 0 <= start <= start + length <= end <= len(data):
            raise ValueError(f"kallsyms_{label} extends beyond its file mapping")
        return data[start:start + length]

    count, = struct.unpack(prefix + "I", table("num_syms", 4))
    if not 0 < count <= MAX_SYMBOLS:
        raise ValueError("Invalid kallsyms symbol count")
    indexes = struct.unpack(prefix + "256H", table("token_index", 512))
    marker_count = (count + 255) // 256
    markers = struct.unpack(
        prefix + f"{marker_count}{marker_code}", table("markers", marker_count * marker_size)
    )
    token_start, token_end = tables["token_table"]
    tokens = []
    pos = token_start
    for offset in indexes:
        if pos != token_start + offset:
            raise ValueError("Invalid kallsyms token sequence")
        end = data.find(b"\0", pos, min(token_end, pos + 1024))
        if end < 0:
            raise ValueError("Unterminated kallsyms token")
        tokens.append(data[pos:end])
        pos = end + 1

    if rela_addresses is not None:
        if len(rela_addresses) != count:
            raise ValueError("Mismatched RELA kallsyms address count")
        addresses = ((addr,) for addr in rela_addresses)
    elif "addresses" in tables:
        addresses = struct.iter_unpack(prefix + word, table("addresses", count * pointer_size))
    else:
        base, = struct.unpack(prefix + word, table("relative_base", pointer_size))
        offsets = struct.iter_unpack(prefix + ("i" if absolute_percpu else "I"), table("offsets", count * 4))
        addresses = (
            (base + offset if not absolute_percpu else offset if offset >= 0 else base - 1 - offset,)
            for offset, in offsets
        )
    names_start, names_end = tables["names"]
    pos = names_start
    output = []
    previous = -1
    for index, (address,) in enumerate(addresses):
        if (index & 0xFFF) == 0xFFF:
            time.sleep(0)
        if index % 256 == 0 and markers[index // 256] != pos - names_start:
            raise ValueError("Invalid kallsyms name marker")
        if pos >= names_end or not previous <= address < 1 << (pointer_size * 8):
            raise ValueError("Invalid kallsyms address or name bounds")
        previous = address
        length = data[pos]
        pos += 1
        if long_names and length & 0x80:
            if pos >= names_end:
                raise ValueError("Truncated kallsyms name length")
            length = (length & 0x7f) | (data[pos] << 7)
            pos += 1
        if not 0 < length <= 1024 or pos + length > names_end:
            raise ValueError("Invalid kallsyms name length")
        expanded = b"".join(tokens[token] for token in data[pos:pos + length])
        if not NAMES.fullmatch(expanded):
            raise ValueError("Invalid expanded kallsyms symbol")
        output.append(Symbol(address, chr(expanded[0]), expanded[1:].decode("ascii")))
        pos += length
    if names_end - pos > 512 or any(data[pos:names_end]):
        raise ValueError("Invalid kallsyms padding after the names")
    return output


def recover(path, result, labels, address_bias=0):
    """Read a labelled table if all required mappings are present."""
    ranges = binary_xref._section_ranges(result)
    offsets = {}
    for label in LABELS:
        address = labels.get("kallsyms_" + label)
        if address is None:
            continue
        va = address + address_bias
        for section in ranges:
            if section["va"] and section["va"] <= va < section["va"] + section["size"]:
                offsets[label] = (
                    section["offset"] + va - section["va"], section["offset"] + section["size"],
                )
                break
    required = {"num_syms", "names", "markers", "token_table", "token_index"}
    if not required <= offsets.keys() or not (
        "addresses" in offsets or {"relative_base", "offsets"} <= offsets.keys()
    ):
        return []
    ordered = sorted(offsets.items(), key=lambda item: item[1][0])
    for (key, (start, end)), (_, (next_start, _)) in zip(ordered, ordered[1:]):
        offsets[key] = (start, min(end, next_start))

    header = result["header"]
    modes = (False,)
    with open(path, "rb") as stream, mmap.mmap(stream.fileno(), 0, access=mmap.ACCESS_READ) as data:
        if offsets["names"][1] > len(data) or offsets["token_table"][1] > len(data):
            raise ValueError("Truncated kallsyms file mapping")
        if "offsets" in offsets and "addresses" not in offsets:
            config = kernel_version.extract_ikconfig(data)
            if config and "CONFIG_KALLSYMS_ABSOLUTE_PERCPU=y" in config.splitlines():
                modes = (True,)
            elif not config:
                modes = (False, True)
        for marker_size in ((4, 8) if int(header["class"]) == 64 else (4,)):
            for absolute_percpu in modes:
                for long_names in (True, False):
                    try:
                        rows = _decode(
                            data, offsets, header["endianness"], int(header["class"]) // 8,
                            absolute_percpu, long_names, marker_size=marker_size,
                        )
                    except ValueError:
                        continue
                    if any(row.kind in "TtWw" and row.address for row in rows):
                        return rows
    raise ValueError("Kallsyms tables were located, but name, marker or address validation failed")


def _find_rela_table(data: mmap.mmap, min_entries: int = 3) -> tuple[int, int] | None:
    """Locate the largest contiguous AArch64 R_AARCH64_RELATIVE (0x403) Elf64_Rela table."""
    needle = b"\x03\x04\x00\x00\x00\x00\x00\x00"
    data_len = len(data)
    best: tuple[int, int] | None = None
    best_len = 0
    pos = 0
    while True:
        hit = data.find(needle, pos)
        if hit < 8 or hit + 64 > data_len:
            return best
        if (
            struct.unpack_from("<Q", data, hit + 24)[0] == 0x403
            and struct.unpack_from("<Q", data, hit + 48)[0] == 0x403
        ):
            start = hit - 8
            while start >= 24 and struct.unpack_from("<Q", data, start - 16)[0] == 0x403:
                start -= 24
            end = hit + 64
            while end + 24 <= data_len and struct.unpack_from("<Q", data, end + 8)[0] == 0x403:
                end += 24
            span = end - start
            if span // 24 >= min_entries and span > best_len:
                best = (start, end)
                best_len = span
            pos = end
        else:
            pos = hit + 8


def _rela_kallsyms_addresses(
    data: mmap.mmap, rela_bounds: tuple[int, int] | None, addr_file_off: int, count: int
) -> list[int] | None:
    """Recover zeroed kallsyms_addresses[] from R_AARCH64_RELATIVE r_addend records."""
    if rela_bounds is None or count <= 0:
        return None
    start, end = rela_bounds
    total = (end - start) // 24
    if total < count:
        return None
    page_off = addr_file_off & 0xFFF
    # Scan in steps or match page offset since r_offset is sorted ascending in .rela.dyn
    for idx in range(0, total - count + 1):
        ro_first = struct.unpack_from("<Q", data, start + idx * 24)[0]
        if (ro_first & 0xFFF) != page_off:
            continue
        ro_last = struct.unpack_from("<Q", data, start + (idx + count - 1) * 24)[0]
        if ro_last == ro_first + (count - 1) * 8:
            blob = data[start + idx * 24 : start + (idx + count) * 24]
            return [addend for _, _, addend in struct.iter_unpack("<QQQ", blob)]
    return None


def recover_blind(path, result):
    """Locate and decode embedded kallsyms directly from a raw/PE kernel image without System.map."""
    header = result.get("header", {})
    endian = header.get("endianness", "little")
    pointer_size = int(header.get("class", 64) or 64) // 8
    if endian != "little" or pointer_size not in (4, 8):
        return []
    needle = b"0\x001\x002\x003\x004\x005\x006\x007\x008\x009\x00"
    with open(path, "rb") as stream, mmap.mmap(stream.fileno(), 0, access=mmap.ACCESS_READ) as data:
        data_len = len(data)
        if data_len < 1024:
            return []
        config = kernel_version.extract_ikconfig(data)
        if config and "CONFIG_KALLSYMS_ABSOLUTE_PERCPU=y" in config.splitlines():
            modes = (True, False)
        else:
            modes = (False, True)
        rela_cache = False  # Lazy-discovered only if zeroed kallsyms_addresses[] is encountered

        pos = 0
        while True:
            hit = data.find(needle, pos)
            if hit < 0:
                break
            pos = hit + len(needle)
            for idx_off in range(hit & ~3, min(data_len - 512, hit + 2048) + 1, 4):
                if data[idx_off : idx_off + 2] != b"\x00\x00":
                    continue
                vals = struct.unpack_from("<256H", data, idx_off)
                if vals[255] <= 520 or not all(2 <= vals[i + 1] - vals[i] <= 64 for i in range(255)):
                    continue
                tok_start = None
                for pad in range(0, 512):
                    end255 = idx_off - pad - 1
                    if end255 <= 0 or data[end255] != 0:
                        continue
                    prev_nul = data.rfind(b"\x00", max(0, end255 - 64), end255)
                    if prev_nul < 0:
                        continue
                    cand = prev_nul + 1 - vals[255]
                    if cand >= 0 and cand % 4 == 0 and all(
                        data[cand + vals[k] - 1] == 0 for k in range(1, 256)
                    ):
                        tok_start = cand
                        break
                if tok_start is None:
                    continue

                for marker_size in ((4, 8) if pointer_size == 8 else (4,)):
                    m_fmt = "<Q" if marker_size == 8 else "<I"
                    marker_ranges = []
                    m_end = tok_start
                    while (
                        m_end >= marker_size * 2
                        and m_end > tok_start - 512
                        and struct.unpack_from(m_fmt, data, m_end - marker_size)[0] == 0
                    ):
                        m_end -= marker_size
                    cur = m_end - marker_size
                    if cur >= 0:
                        last_m = struct.unpack_from(m_fmt, data, cur)[0]
                        if last_m > 0:
                            while cur >= marker_size:
                                prev_m = struct.unpack_from(m_fmt, data, cur - marker_size)[0]
                                if prev_m == 0 and 1 <= last_m <= 32768:
                                    marker_ranges.append((cur - marker_size, m_end))
                                    break
                                if not (32 <= last_m - prev_m <= 32768):
                                    break
                                last_m = prev_m
                                cur -= marker_size
                    # Also support tiny tables with <= 256 symbols (m_count == 1, markers == [0])
                    cand_m_starts = (
                        tok_start - marker_size,
                        tok_start - marker_size * 2,
                        (tok_start - 1) & ~0xFF if tok_start > 0 else -1,
                    )
                    for cand_m in cand_m_starts:
                        if 0 <= cand_m <= tok_start - marker_size and not any(data[cand_m : tok_start]):
                            pair = (cand_m, cand_m + marker_size)
                            if pair not in marker_ranges:
                                marker_ranges.append(pair)

                    for m_start, m_end in marker_ranges:
                        m_count = (m_end - m_start) // marker_size
                        max_marker = struct.unpack_from(m_fmt, data, m_end - marker_size)[0]
                        lo = max(0, m_start - max_marker - (65536 if m_count > 1 else 2048))
                        hi = min(data_len - 4, m_start - max_marker + 8)
                        for ns_off in range(hi & ~3, lo - 1, -4):
                            cnt = struct.unpack_from("<I", data, ns_off)[0]
                            if not (max(0, (m_count - 1) * 256) < cnt <= min(MAX_SYMBOLS, m_count * 256)):
                                continue
                            cand_names = [ns_off + 4, ns_off + 8]
                            aligned_256 = (ns_off + 0x100) & ~0xFF
                            if aligned_256 not in cand_names:
                                cand_names.append(aligned_256)
                            for names_start in cand_names:
                                if names_start > m_start or any(data[ns_off + 4 : names_start]):
                                    continue
                                layouts = []
                                # Layout A (Linux <= 6.3, BASE_RELATIVE): offsets -> relative_base -> num_syms
                                for rb_pad in (pointer_size, 0x100):
                                    if ns_off >= rb_pad + cnt * 4:
                                        rb_off = ns_off - rb_pad
                                        if any(data[rb_off + pointer_size : ns_off]):
                                            continue
                                        rb = struct.unpack_from("<Q" if pointer_size == 8 else "<I", data, rb_off)[0]
                                        if (rb & 0xFFF) == 0 and (pointer_size == 4 or (rb >> 48) in (0xFFFF, 0x0000)):
                                            for off_s in dict.fromkeys((
                                                rb_off - cnt * 4,
                                                (rb_off - cnt * 4) & ~7,
                                                (rb_off - cnt * 4) & ~0xFF,
                                            )):
                                                if off_s >= 0 and not any(data[off_s + cnt * 4 : rb_off]):
                                                    layouts.append((
                                                        0 if (pointer_size == 8 and (rb >> 48) == 0xFFFF) else 1,
                                                        {"offsets": (off_s, rb_off), "relative_base": (rb_off, ns_off)},
                                                        None,
                                                    ))
                                # Layout B (Linux >= 6.4, BASE_RELATIVE): token_index -> offsets -> relative_base
                                after_idx = idx_off + 512
                                for pad_a in (0, 4, 8):
                                    off_s = after_idx + pad_a
                                    off_e = off_s + cnt * 4
                                    for rb_off in (off_e, (off_e + 7) & ~7):
                                        if rb_off + pointer_size <= data_len:
                                            rb = struct.unpack_from("<Q" if pointer_size == 8 else "<I", data, rb_off)[0]
                                            if (rb & 0xFFF) == 0 and (pointer_size == 4 or (rb >> 48) in (0xFFFF, 0x0000)):
                                                layouts.append((
                                                    0 if (pointer_size == 8 and (rb >> 48) == 0xFFFF) else 1,
                                                    {"offsets": (off_s, off_e), "relative_base": (rb_off, rb_off + pointer_size)},
                                                    None,
                                                ))
                                # Layout C (!BASE_RELATIVE): addresses array before num_syms (tight or 0x100-aligned)
                                for pad_c in range(0, 0x200, pointer_size):
                                    a_s = ns_off - pad_c - cnt * pointer_size
                                    if a_s < 0 or any(data[ns_off - pad_c : ns_off]):
                                        continue
                                    if pad_c > 8 and (a_s & 0xFF) != 0:
                                        continue
                                    probe_len = min(64, cnt * pointer_size)
                                    if pointer_size == 8 and data[a_s : a_s + probe_len] == bytes(probe_len):
                                        if rela_cache is False:
                                            rela_cache = _find_rela_table(data)
                                        rela_addrs = _rela_kallsyms_addresses(data, rela_cache, a_s, cnt)
                                        if rela_addrs:
                                            layouts.append((0, {}, rela_addrs))
                                        continue
                                    first_nz = next(
                                        (
                                            struct.unpack_from("<Q" if pointer_size == 8 else "<I", data, a_s + k * pointer_size)[0]
                                            for k in range(min(cnt, 16))
                                            if struct.unpack_from("<Q" if pointer_size == 8 else "<I", data, a_s + k * pointer_size)[0]
                                        ),
                                        0,
                                    )
                                    prio = 0 if (pointer_size == 8 and (first_nz >> 48) == 0xFFFF) else 2
                                    layouts.append((prio, {"addresses": (a_s, a_s + cnt * pointer_size)}, None))

                                layouts.sort(key=lambda item: item[0])
                                for _, extra, rela_addrs in layouts:
                                    tables = {
                                        **extra,
                                        "num_syms": (ns_off, names_start),
                                        "names": (names_start, m_start),
                                        "markers": (m_start, m_end),
                                        "token_table": (tok_start, idx_off),
                                        "token_index": (idx_off, idx_off + 512),
                                    }
                                    for abs_p in modes:
                                        for long_n in (True, False):
                                            try:
                                                rows = _decode(
                                                    data, tables, endian, pointer_size, abs_p, long_n,
                                                    marker_size=marker_size, rela_addresses=rela_addrs,
                                                )
                                            except ValueError:
                                                continue
                                            if any(row.kind in "TtWw" and row.address for row in rows):
                                                return rows
    return []


def add_symbols(result, symbols, source, address_bias=0):
    """Add executable symbols with valid mapped addresses and preserve ELF names."""
    address_map = result.setdefault("addr_symbol_map", {})
    functions = {row["addr"]: row for row in result["functions"]}
    ranges = sorted(
        (section["va"], section["va"] + section["size"])
        for section in binary_xref._section_ranges(result) if section["flags"] & 4 and section["va"]
    )
    starts = [start for start, _ in ranges]
    added = 0
    for idx, symbol in enumerate(symbols):
        if (idx & 0xFFF) == 0xFFF:
            time.sleep(0)
        address = symbol.address + address_bias
        if address <= 0:
            continue
        address_map.setdefault(address, symbol.name)
        index = bisect_right(starts, address) - 1
        if symbol.kind not in "TtWw" or index < 0 or address >= ranges[index][1]:
            continue
        if address not in functions:
            functions[address] = {
                "addr": address, "name": symbol.name, "demangled": symbol.name, "size": 0, "source": source,
            }
            added += 1
        address_map.setdefault(address, functions[address]["name"])
    if added:
        result["functions"] = sorted(functions.values(), key=lambda row: row["addr"])
        result["_function_starts"] = [row["addr"] for row in result["functions"]]
    return added
