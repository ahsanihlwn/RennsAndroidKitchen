# SPDX-License-Identifier: MPL-2.0
import io
import lzma
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Optional

from elftools.elf.elffile import ELFFile
from elftools.elf.sections import SymbolTableSection
from elftools.elf.dynamic import DynamicSection


PRINTABLE_RE = re.compile(rb"[\x20-\x7e]{4,}")

PATH_LIKE_RE = re.compile(
    r"""^(?:
        (?:[A-Za-z]:)?[/\\][\w\-.]+(?:[/\\][\w\-.]+)+
        |
        [\w\-]+(?:/[\w\-]+)+\.\w{1,6}
    )$""",
    re.VERBOSE,
)

KNOWN_EXTS = {
    ".so", ".dll", ".dylib", ".txt", ".json", ".xml", ".ttf", ".otf", ".png",
    ".jpg", ".jpeg", ".webp", ".bin", ".dat", ".cfg", ".ini", ".yaml", ".yml",
    ".proto", ".wasm", ".jar", ".apk", ".c", ".cpp", ".h", ".hpp", ".py",
}


MAGIC_SIGNATURES = [
    ("PNG image", b"\x89PNG\r\n\x1a\n"),
    ("GIF image", b"GIF89a"),
    ("GIF image", b"GIF87a"),
    ("JPEG image", b"\xff\xd8\xff"),
    ("ZIP/JAR/APK archive", b"PK\x03\x04"),
    ("GZIP archive", b"\x1f\x8b\x08"),
    ("BZIP2 archive", b"BZh"),
    ("XZ archive", b"\xfd7zXZ\x00"),
    ("7-Zip archive", b"7z\xbc\xaf\x27\x1c"),
    ("PDF document", b"%PDF-"),
    ("RIFF/WAV container", b"RIFF"),
    ("Nested ELF binary", b"\x7fELF"),
    ("SQLite database", b"SQLite format 3\x00"),
]


def human_size(n: float) -> str:
    for unit in ["B", "KB", "MB", "GB"]:
        if n < 1024:
            return f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}TB"


def extract_raw_strings(data: bytes, min_len: int = 4):
    out = []
    for m in PRINTABLE_RE.finditer(data):
        s = m.group().decode("ascii", errors="ignore")
        if len(s) >= min_len:
            out.append(s)
    return out


def is_path_like(s: str) -> bool:
    if PATH_LIKE_RE.match(s):
        return True
    if "/" in s:
        _, ext = os.path.splitext(s)
        if ext.lower() in KNOWN_EXTS:
            return True
    return False


_CXXFILT_BIN = shutil.which("c++filt")


def demangle(name: str) -> str:

    if not name.startswith("_Z") or _CXXFILT_BIN is None:
        return name
    try:
        r = subprocess.run([_CXXFILT_BIN, name], capture_output=True, text=True, timeout=5)
        out = r.stdout.strip()
        return out if out else name
    except Exception:
        return name


def demangle_batch(names: list[str]) -> dict[str, str]:

    if _CXXFILT_BIN is None or not names:
        return {n: n for n in names}
    try:
        r = subprocess.run(
            [_CXXFILT_BIN], input="\n".join(names), capture_output=True, text=True, timeout=15
        )
        results = r.stdout.splitlines()
        if len(results) != len(names):
            return {n: n for n in names}
        return dict(zip(names, results))
    except Exception:
        return {n: n for n in names}


@dataclass
class EmbeddedFile:

    label: str            # nama tampilan, misal ".gnu_debugdata (minidebuginfo)"
    kind: str              # "minidebuginfo" | "carved"
    file_type: str         # deskripsi tipe, misal "PNG image", "Nested ELF"
    offset: int             # offset di file ASLI (bukan di data terdekompresi)
    data: bytes             # isi bytes yang sudah di-extract, siap dibuka/disimpan
    exact_length: bool = True  # apakah panjangnya presisi atau cuma perkiraan


@dataclass
class PathNode:
    files: list = field(default_factory=list)      # nama file (leaf), string biasa
    embedded: list = field(default_factory=list)    # leaf yang py EmbeddedFile (openable)
    children: dict = field(default_factory=dict)    # nama folder -> PathNode


def build_path_tree(path_like_strings: list[str], embedded_files: list[EmbeddedFile]) -> PathNode:
    root = PathNode()

    for p in sorted(set(path_like_strings)):
        parts = [seg for seg in re.split(r"[/\\]", p) if seg]
        node = root
        for i, seg in enumerate(parts):
            if i == len(parts) - 1:
                node.files.append(seg)
            else:
                node = node.children.setdefault(seg, PathNode())

    if embedded_files:
        emb_root = root.children.setdefault("[embedded_in_binary]", PathNode())
        for ef in embedded_files:
            emb_root.embedded.append(ef)

    return root


def analyze_header(elf: ELFFile, filepath: str) -> dict:
    h = elf.header
    return {
        "file": filepath,
        "size": os.path.getsize(filepath),
        "class": elf.elfclass,
        "endianness": "little" if elf.little_endian else "big",
        "machine": h["e_machine"],
        "file_type": h["e_type"],
        "entry_point": hex(h["e_entry"]),
        "abi": elf.get_machine_arch(),
        "num_sections": h["e_shnum"],
        "num_segments": h["e_phnum"],
    }


def analyze_sections(elf: ELFFile) -> list[dict]:
    return [{
        "name": sec.name, "type": sec["sh_type"], "addr": hex(sec["sh_addr"]),
        "offset": hex(sec["sh_offset"]), "size": sec["sh_size"], "flags": sec["sh_flags"],
    } for sec in elf.iter_sections()]


def analyze_segments(elf: ELFFile) -> list[dict]:
    return [{
        "type": seg["p_type"], "vaddr": hex(seg["p_vaddr"]), "offset": hex(seg["p_offset"]),
        "filesz": seg["p_filesz"], "memsz": seg["p_memsz"], "flags": seg["p_flags"],
    } for seg in elf.iter_segments()]


def analyze_symbols(elf: ELFFile) -> dict:
    exported, imported, local, all_syms = [], [], [], []
    names_to_demangle = []
    for sec in elf.iter_sections():
        if not isinstance(sec, SymbolTableSection):
            continue
        for sym in sec.iter_symbols():
            if not sym.name:
                continue
            info = {
                "name": sym.name, "demangled": sym.name, "section_table": sec.name,
                "bind": sym["st_info"]["bind"], "type": sym["st_info"]["type"],
                "shndx": sym["st_shndx"], "value": hex(sym["st_value"]),
                "size": sym["st_size"],
            }
            all_syms.append(info)
            names_to_demangle.append(sym.name)
            if sym["st_shndx"] == "SHN_UNDEF":
                imported.append(info)
            elif sym["st_info"]["bind"] in ("STB_GLOBAL", "STB_WEAK"):
                exported.append(info)
            elif sym["st_info"]["bind"] == "STB_LOCAL":
                local.append(info)

    dmap = demangle_batch(list(set(names_to_demangle)))
    for info in all_syms:
        info["demangled"] = dmap.get(info["name"], info["name"])

    return {"exported": exported, "imported": imported, "local": local, "total": len(all_syms)}


def analyze_dynamic(elf: ELFFile) -> dict:
    needed, soname, rpath = [], None, []
    for sec in elf.iter_sections():
        if not isinstance(sec, DynamicSection):
            continue
        for tag in sec.iter_tags():
            if tag.entry.d_tag == "DT_NEEDED":
                needed.append(tag.needed)
            elif tag.entry.d_tag == "DT_SONAME":
                soname = tag.soname
            elif tag.entry.d_tag in ("DT_RPATH", "DT_RUNPATH"):
                rpath.append(tag.rpath if hasattr(tag, "rpath") else tag.runpath)
    return {"needed_libraries": needed, "soname": soname, "rpath": rpath}


def analyze_dwarf_paths(elf: ELFFile) -> dict:
    if not elf.has_dwarf_info(strict=True):
        return {"has_debug_info": False, "source_files": []}
    dwarfinfo = elf.get_dwarf_info()
    files = set()
    try:
        for CU in dwarfinfo.iter_CUs():
            lineprog = dwarfinfo.line_program_for_CU(CU)
            if lineprog is None:
                continue
            for f in lineprog["file_entry"]:
                files.add(f.name.decode() if isinstance(f.name, bytes) else f.name)
    except Exception:
        pass
    return {"has_debug_info": True, "source_files": sorted(files)}


def analyze_gnu_debugdata(elf: ELFFile) -> Optional[tuple[EmbeddedFile, int, int]]:

    sec = elf.get_section_by_name(".gnu_debugdata")
    if sec is None:
        return None
    raw = sec.data()
    try:
        decompressed = lzma.decompress(raw)
    except Exception:
        return None
    ef = EmbeddedFile(
        label=".gnu_debugdata (minidebuginfo)",
        kind="minidebuginfo",
        file_type="Nested ELF (XZ compressed)",
        offset=sec["sh_offset"],
        data=decompressed,
        exact_length=True,
    )
    return ef, sec["sh_offset"], sec["sh_size"]


def scan_embedded_files(filepath: str, min_gap: int = 16, exclude_ranges: list[tuple[int, int]] = None) -> list[EmbeddedFile]:

    exclude_ranges = exclude_ranges or []
    with open(filepath, "rb") as f:
        data = f.read()

    def in_excluded(offset):
        return any(start <= offset < start + size for start, size in exclude_ranges)

    hits = []
    for label, magic in MAGIC_SIGNATURES:
        start = 0
        while True:
            idx = data.find(magic, start)
            if idx == -1:
                break
            if idx != 0 and not in_excluded(idx):
                hits.append((idx, label, magic))
            start = idx + 1

    hits.sort()
    results = []
    for i, (offset, label, magic) in enumerate(hits):
        next_offset = hits[i + 1][0] if i + 1 < len(hits) else len(data)
        exact = False
        end = next_offset

        if magic.startswith(b"\x89PNG"):
            end_guess = _find_png_end(data, offset)
            if end_guess:
                end, exact = end_guess, True
        elif magic == b"\x1f\x8b\x08":
            end_guess = _find_gzip_end(data, offset)
            if end_guess:
                end, exact = end_guess, True
        elif magic == b"\x7fELF" and offset != 0:
            end_guess = _find_nested_elf_end(data, offset)
            if end_guess:
                end, exact = end_guess, True

        chunk = data[offset:end]
        if len(chunk) < min_gap:
            continue
        results.append(EmbeddedFile(
            label=f"{label} @ offset {hex(offset)}",
            kind="carved",
            file_type=label,
            offset=offset,
            data=chunk,
            exact_length=exact,
        ))
    return results


def _find_png_end(data: bytes, start: int):

    pos = start + 8
    try:
        while pos < len(data):
            length = int.from_bytes(data[pos:pos + 4], "big")
            ctype = data[pos + 4:pos + 8]
            pos += 8 + length + 4  # length + type + data + CRC
            if ctype == b"IEND":
                return pos
    except Exception:
        return None
    return None


def _find_gzip_end(data: bytes, start: int):
    import gzip
    try:
        d = gzip.GzipFile(fileobj=io.BytesIO(data[start:]))
        d.read()
        # posisi setelah decompress selesai = ukuran gzip stream yang valid
        return start + d.fileobj.tell()
    except Exception:
        return None


def _find_nested_elf_end(data: bytes, start: int):
    try:
        inner = ELFFile(io.BytesIO(data[start:]))
        max_end = 0
        for sec in inner.iter_sections():
            if sec["sh_type"] != "SHT_NOBITS":
                max_end = max(max_end, sec["sh_offset"] + sec["sh_size"])
        for seg in inner.iter_segments():
            max_end = max(max_end, seg["p_offset"] + seg["p_filesz"])
        max_end = max(max_end, inner.header["e_shoff"] + inner.header["e_shnum"] * inner.header["e_shentsize"])
        return start + max_end if max_end > 0 else None
    except Exception:
        return None


def analyze_relocations(elf: ELFFile) -> list[dict]:

    from elftools.elf.relocation import RelocationSection
    out = []
    for sec in elf.iter_sections():
        if not isinstance(sec, RelocationSection):
            continue
        symtab = elf.get_section(sec["sh_link"]) if sec["sh_link"] else None
        for rel in sec.iter_relocations():
            sym_name = ""
            if symtab is not None:
                try:
                    sym = symtab.get_symbol(rel["r_info_sym"])
                    sym_name = sym.name or ""
                except Exception:
                    pass
            out.append({
                "reloc_section": sec.name,
                "offset": hex(rel["r_offset"]),
                "symbol": sym_name,
                "demangled": demangle(sym_name) if sym_name else "",
                "type": rel["r_info_type"],
            })
    return out


def analyze_security(elf: ELFFile, dynamic: dict, symbols: dict) -> dict:

    imported_names = {s["name"] for s in symbols["imported"]}

    nx_stack = None
    relro = "tidak ada"
    for seg in elf.iter_segments():
        if seg["p_type"] == "PT_GNU_STACK":
            nx_stack = not bool(seg["p_flags"] & 0x1)  # PF_X = 0x1
        if seg["p_type"] == "PT_GNU_RELRO":
            relro = "partial"

    bind_now = False
    for sec in elf.iter_sections():
        if sec.name == ".dynamic":
            for tag in sec.iter_tags():
                if tag.entry.d_tag == "DT_BIND_NOW":
                    bind_now = True
                if tag.entry.d_tag == "DT_FLAGS" and getattr(tag, "flags", 0) & 0x8:  # DF_BIND_NOW
                    bind_now = True
                if tag.entry.d_tag == "DT_FLAGS_1" and getattr(tag.entry, "d_val", 0) & 0x1:  # DF_1_NOW
                    bind_now = True
    if relro == "partial" and bind_now:
        relro = "full"

    has_symtab = any(sec["sh_type"] == "SHT_SYMTAB" for sec in elf.iter_sections())

    return {
        "stripped": not has_symtab,
        "nx_stack": nx_stack,
        "relro": relro,
        "stack_canary": "__stack_chk_fail" in imported_names,
        "fortify_source": any(n.endswith("_chk") for n in imported_names),
        "rpath_or_runpath": bool(dynamic["rpath"]),
        "note_pie": "N/A — ini shared library (.so selalu position-independent/PIC secara desain)",
    }


def get_section_bytes(filepath: str, section_name: str) -> Optional[bytes]:
    with open(filepath, "rb") as f:
        elf = ELFFile(f)
        sec = elf.get_section_by_name(section_name)
        if sec is None or sec["sh_type"] == "SHT_NOBITS":
            return None
        return sec.data()


def compare_symbols(result_a: dict, result_b: dict) -> dict:

    def name_set(r):
        return {s["name"]: s["demangled"] for s in r["symbols"]["exported"]}

    a, b = name_set(result_a), name_set(result_b)
    only_a = sorted(a[n] for n in (set(a) - set(b)))
    only_b = sorted(b[n] for n in (set(b) - set(a)))
    common = sorted(set(a) & set(b))
    return {
        "file_a": result_a["filepath"], "file_b": result_b["filepath"],
        "only_in_a": only_a, "only_in_b": only_b,
        "common_count": len(common),
    }


def analyze_appended_data(elf: ELFFile, filepath: str) -> dict:
    size = os.path.getsize(filepath)
    max_end = 0
    for sec in elf.iter_sections():
        if sec["sh_type"] != "SHT_NOBITS":
            max_end = max(max_end, sec["sh_offset"] + sec["sh_size"])
    for seg in elf.iter_segments():
        max_end = max(max_end, seg["p_offset"] + seg["p_filesz"])
    max_end = max(max_end, elf.header["e_shoff"] + elf.header["e_shnum"] * elf.header["e_shentsize"])
    trailing = size - max_end
    return {
        "elf_content_end_offset": hex(max_end),
        "file_size": size,
        "trailing_bytes": max(trailing, 0),
    }


def extract_functions(elf: ELFFile, symbols: dict) -> list[dict]:

    funcs = []
    for s in symbols["exported"] + symbols["local"]:
        if s["type"] != "STT_FUNC" or s["size"] == 0:
            continue
        funcs.append({
            "name": s["name"], "demangled": s["demangled"],
            "addr": int(s["value"], 16), "size": s["size"],
        })
    funcs.sort(key=lambda f: f["addr"])
    return funcs


def build_plt_map(elf: ELFFile, relocations: list[dict]) -> dict:

    plt = elf.get_section_by_name(".plt")
    plt_relocs = [r for r in relocations if r["reloc_section"] == ".rela.plt" and r["symbol"]]
    if plt is None or not plt_relocs:
        return {}

    n = len(plt_relocs)
    size = plt["sh_size"]
    for header, stub in [(32, 16), (16, 16), (16, 8), (0, 16), (0, 8)]:
        if size > header and (size - header) == n * stub:
            base = plt["sh_addr"] + header
            return {
                base + i * stub: (r["demangled"] or r["symbol"]) + "@plt"
                for i, r in enumerate(plt_relocs)
            }
    return {}


def build_address_symbol_map(symbols: dict) -> dict:

    addr_map = {}
    for category in ("exported", "local"):
        for s in symbols[category]:
            try:
                addr = int(s["value"], 16)
            except (ValueError, TypeError):
                continue
            if addr:
                addr_map[addr] = s["demangled"]
    return addr_map


def _annotate_targets(instructions: list[dict], addr_map: dict) -> list[dict]:

    hex_re = re.compile(r"#?(0x[0-9a-fA-F]+)")
    for instr in instructions:
        instr["annotation"] = None
        for m in hex_re.finditer(instr["op_str"]):
            try:
                target = int(m.group(1), 16)
            except ValueError:
                continue
            if target in addr_map:
                instr["annotation"] = addr_map[target]
                break
    return instructions


def vaddr_to_file_offset(elf: ELFFile, vaddr: int) -> Optional[int]:
    for sec in elf.iter_sections():
        if sec["sh_addr"] == 0:
            continue
        if sec["sh_addr"] <= vaddr < sec["sh_addr"] + sec["sh_size"]:
            return sec["sh_offset"] + (vaddr - sec["sh_addr"])
    return None


def _setup_capstone_path():

    if os.getenv("LIBCAPSTONE_PATH") and os.path.isfile(os.path.join(os.getenv("LIBCAPSTONE_PATH"), "capstone.dll")):
        return
    candidates = []
    exe_dir = os.path.dirname(sys.executable)
    app_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

    for base in (exe_dir, app_dir, os.getcwd()):
        candidates.append(base)
        candidates.append(os.path.join(base, "capstone", "lib"))
        candidates.append(os.path.join(base, "capstone"))
        candidates.append(os.path.join(base, "lib"))

    for c in candidates:
        if os.path.isfile(os.path.join(c, "capstone.dll")):
            os.environ["LIBCAPSTONE_PATH"] = c
            return


def disassemble_function(elf: ELFFile, func: dict, addr_map: dict, raw_bytes: bytes):
    _setup_capstone_path()
    try:
        import capstone
    except Exception as e:
        return {"error": f"capstone load error: {e}"}

    offset = vaddr_to_file_offset(elf, func["addr"])
    if offset is None:
        return {"error": "gak nemu offset file buat address ini"}

    code = raw_bytes[offset: offset + func["size"]]
    arch_map = {
        "EM_X86_64": (capstone.CS_ARCH_X86, capstone.CS_MODE_64),
        "EM_386": (capstone.CS_ARCH_X86, capstone.CS_MODE_32),
        "EM_ARM": (capstone.CS_ARCH_ARM, capstone.CS_MODE_ARM),
        "EM_AARCH64": (capstone.CS_ARCH_ARM64, capstone.CS_MODE_ARM),
    }
    machine = elf.header["e_machine"]
    if machine not in arch_map:
        return {"error": f"arsitektur {machine} belum di-support disassembler ini"}
    cs_arch, cs_mode = arch_map[machine]
    md = capstone.Cs(cs_arch, cs_mode)
    instructions = [
        {"addr": i.address, "mnemonic": i.mnemonic, "op_str": i.op_str}
        for i in md.disasm(code, func["addr"])
    ]
    return {"instructions": _annotate_targets(instructions, addr_map)}


def disassemble_section(elf: ELFFile, section_name: str, max_bytes: int = 4096, addr_map: dict = None):
    _setup_capstone_path()
    try:
        import capstone
    except Exception as e:
        return {"error": f"capstone load error: {e}"}

    sec = elf.get_section_by_name(section_name)
    if sec is None:
        return {"error": f"section {section_name} tidak ditemukan"}

    code = sec.data()[:max_bytes]
    addr = sec["sh_addr"]
    arch_map = {
        "EM_X86_64": (capstone.CS_ARCH_X86, capstone.CS_MODE_64),
        "EM_386": (capstone.CS_ARCH_X86, capstone.CS_MODE_32),
        "EM_ARM": (capstone.CS_ARCH_ARM, capstone.CS_MODE_ARM),
        "EM_AARCH64": (capstone.CS_ARCH_ARM64, capstone.CS_MODE_ARM),
    }
    machine = elf.header["e_machine"]
    if machine not in arch_map:
        return {"error": f"arsitektur {machine} belum di-support disassembler ini"}
    cs_arch, cs_mode = arch_map[machine]
    md = capstone.Cs(cs_arch, cs_mode)
    instructions = [
        {"addr": i.address, "mnemonic": i.mnemonic, "op_str": i.op_str}
        for i in md.disasm(code, addr)
    ]
    if addr_map:
        instructions = _annotate_targets(instructions, addr_map)
    return {"instructions": instructions}


def path_node_to_dict(node: PathNode) -> dict:
    return {
        "files": list(node.files),
        "embedded": [
            {"label": e.label, "type": e.file_type, "size": len(e.data),
             "exact_length": e.exact_length, "offset": hex(e.offset)}
            for e in node.embedded
        ],
        "children": {k: path_node_to_dict(v) for k, v in node.children.items()},
    }


def result_to_jsonable(result: dict) -> dict:

    out = dict(result)
    out["path_tree"] = path_node_to_dict(result["path_tree"])
    out["embedded_files"] = [
        {"label": e.label, "kind": e.kind, "type": e.file_type,
         "offset": hex(e.offset), "size": len(e.data), "exact_length": e.exact_length}
        for e in result["embedded_files"]
    ]
    return out


def executable_section_names(elf: ELFFile) -> list[str]:

    names = []
    for sec in elf.iter_sections():
        if sec["sh_flags"] & 0x4 and sec["sh_size"] > 0:
            names.append(sec.name)
    return names


def analyze_file(filepath: str, min_str_len: int = 4) -> dict:
    with open(filepath, "rb") as f:
        elf = ELFFile(f)
        result = {"filepath": filepath}
        result["header"] = analyze_header(elf, filepath)
        result["sections"] = analyze_sections(elf)
        result["segments"] = analyze_segments(elf)
        result["symbols"] = analyze_symbols(elf)
        result["dynamic"] = analyze_dynamic(elf)
        result["dwarf"] = analyze_dwarf_paths(elf)
        result["appended_data"] = analyze_appended_data(elf, filepath)
        result["exec_sections"] = executable_section_names(elf)
        result["relocations"] = analyze_relocations(elf)
        result["security"] = analyze_security(elf, result["dynamic"], result["symbols"])
        result["functions"] = extract_functions(elf, result["symbols"])
        result["addr_symbol_map"] = build_address_symbol_map(result["symbols"])
        result["addr_symbol_map"].update(build_plt_map(elf, result["relocations"]))
        gnu_dbg = analyze_gnu_debugdata(elf)

    with open(filepath, "rb") as f:
        raw = f.read()
    strings = extract_raw_strings(raw, min_str_len)
    path_like = [s for s in strings if is_path_like(s)]

    if gnu_dbg:
        ef, ex_offset, ex_size = gnu_dbg
        embedded = scan_embedded_files(filepath, exclude_ranges=[(ex_offset, ex_size)])
        embedded.insert(0, ef)
    else:
        embedded = scan_embedded_files(filepath)

    result["total_strings"] = len(strings)
    result["all_strings"] = strings
    result["path_like_strings"] = path_like
    result["embedded_files"] = embedded
    result["path_tree"] = build_path_tree(path_like, embedded)
    return result
