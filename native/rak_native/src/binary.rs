// SPDX-License-Identifier: MPL-2.0
//! Read-only, format-aware analysis. No target code is executed.
use goblin::{elf, mach, Object};
use pyo3::{exceptions::PyValueError, prelude::*};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::{
    fs::File,
    io::{self, BufReader, Read, Seek, SeekFrom},
    path::PathBuf,
};

const MAX_BYTES: u64 = 512 * 1024 * 1024;
const MAX_ROWS: usize = 200_000;

fn image_bytechunks(source: &mut (impl Read + Seek), ranges: &[(u64, u64, usize)]) -> io::Result<String> {
    const HEX: &[u8; 16] = b"0123456789abcdef";
    let mut output = String::new();
    let mut buffer = [0u8; 16 * 1024];
    for (index, &(address, offset, size)) in ranges.iter().enumerate() {
        if index != 0 {
            output.push('\n');
        }
        output.push_str(&format!("<bytechunk space=\"ram\" offset=\"{address:#x}\" readonly=\"true\">\n"));
        source.seek(SeekFrom::Start(offset))?;
        let mut processed = 0;
        while processed < size {
            let capacity = buffer.len().min(size - processed);
            let length = source.read(&mut buffer[..capacity])?;
            if length == 0 {
                break;
            }
            for &byte in &buffer[..length] {
                if processed != 0 && processed % 64 == 0 {
                    output.push('\n');
                }
                output.push(HEX[(byte >> 4) as usize] as char);
                output.push(HEX[(byte & 0xf) as usize] as char);
                processed += 1;
            }
        }
        output.push_str("\n</bytechunk>");
    }
    Ok(output)
}

#[pyfunction]
pub fn binary_image_bytechunks(
    py: Python<'_>,
    path: PathBuf,
    ranges: Vec<(u64, u64, usize)>,
) -> PyResult<String> {
    py.detach(move || {
        let total = ranges.iter().try_fold(0u64, |sum, range| sum.checked_add(range.2 as u64));
        if total.is_none_or(|size| size > MAX_BYTES) {
            return Err(PyValueError::new_err("Binary byte ranges exceed the analysis limit"));
        }
        let mut source = BufReader::new(File::open(path)?);
        Ok(image_bytechunks(&mut source, &ranges)?)
    })
}

#[pyfunction]
pub fn binary_flat_sha256(
    py: Python<'_>,
    path: PathBuf,
    ranges: Vec<(u64, u64, u64)>,
) -> PyResult<String> {
    py.detach(move || {
        let mut source = File::open(path)?;
        let mut digest = Sha256::new();
        let mut position = 0;
        let zeros = [0u8; 64 * 1024];
        let mut buffer = [0u8; 64 * 1024];
        for (start, offset, length) in ranges {
            let end = start.checked_add(length).filter(|end| *end <= MAX_BYTES);
            let Some(end) = end else {
                return Err(PyValueError::new_err("Flat binary exceeds the analysis limit"));
            };
            if start < position {
                return Err(PyValueError::new_err("Overlapping flat binary ranges"));
            }
            let mut gap = start - position;
            while gap > 0 {
                let take = gap.min(zeros.len() as u64) as usize;
                digest.update(&zeros[..take]);
                gap -= take as u64;
            }
            source.seek(SeekFrom::Start(offset))?;
            let mut remaining = length;
            while remaining > 0 {
                let take = remaining.min(buffer.len() as u64) as usize;
                source.read_exact(&mut buffer[..take])?;
                digest.update(&buffer[..take]);
                remaining -= take as u64;
            }
            position = end;
        }
        Ok(format!("{:x}", digest.finalize()))
    })
}

#[pyfunction]
pub fn binary_dwarf_functions(
    py: Python<'_>,
    path: PathBuf,
) -> PyResult<Option<Vec<crate::dwarf::Location>>> {
    py.detach(move || {
        let source = File::open(path)?;
        let metadata = source.metadata()?;
        if !metadata.is_file() || metadata.len() > MAX_BYTES {
            return Err(PyValueError::new_err("vmlinux exceeds the binary analysis limit"));
        }
        let mut bytes = Vec::with_capacity(metadata.len() as usize);
        source.take(MAX_BYTES + 1).read_to_end(&mut bytes)?;
        if bytes.len() as u64 > MAX_BYTES {
            return Err(PyValueError::new_err("vmlinux exceeds the binary analysis limit"));
        }
        crate::dwarf::function_locations(&bytes).map_err(|error| PyValueError::new_err(error.to_string()))
    })
}

fn hex(n: u64) -> String { format!("{n:#x}") }
fn demangle(name: &str) -> String {
    cpp_demangle::Symbol::new(name).ok()
        .and_then(|s| s.demangle(&cpp_demangle::DemangleOptions::default()).ok())
        .unwrap_or_else(|| name.to_owned())
}
fn entropy(data: &[u8]) -> f64 {
    if data.is_empty() { return 0.0; }
    let mut counts = [0usize; 256];
    for b in data { counts[*b as usize] += 1; }
    counts.iter().filter(|n| **n > 0).map(|n| {
        let p = *n as f64 / data.len() as f64; -p * p.log2()
    }).sum()
}
fn section(name: &str, kind: &str, addr: u64, offset: u64, size: u64, flags: u64, data: &[u8], file_size: u64) -> Value {
    let bytes = usize::try_from(offset).ok().zip(usize::try_from(file_size).ok())
        .and_then(|(o,s)| o.checked_add(s).and_then(|end| data.get(o..end)));
    json!({"name":name,"type":kind,"addr":hex(addr),"offset":hex(offset),"size":size,
        "file_size":file_size,"flags":flags,"entropy":bytes.map(entropy)})
}
fn symbol(name: &str, addr: u64, size: u64, kind: &str) -> Value {
    json!({"name":name,"demangled":demangle(name),"value":hex(addr),"size":size,
        "type":kind,"bind":"STB_GLOBAL","section_table":"","shndx":0})
}
fn analyze(data: &[u8], minimum: usize) -> Result<Value, String> {
    let mut r = json!({"schema_version":1,"engine":"rust/goblin","format":"",
        "sha256":format!("{:x}",Sha256::digest(data)),"entropy":entropy(data),
        "sections":[],"segments":[],"symbols":{"exported":[],"imported":[],"local":[],"total":0},
        "dynamic":{"needed_libraries":[],"soname":null,"rpath":[]},"relocations":[],
        "functions":[],"addr_symbol_map":{},"exec_sections":[],"warnings":[],
        "security":{},"dwarf":{"has_debug_info":false,"source_files":[]},
        "appended_data":{"elf_content_end_offset":"0x0","file_size":data.len(),"trailing_bytes":0}});
    let mut sections=Vec::new(); let mut segments=Vec::new();
    let mut exports=Vec::new(); let mut imports=Vec::new(); let mut locals=Vec::new();
    let mut relocations=Vec::new();
    match Object::parse(data).map_err(|e| e.to_string())? {
        Object::Elf(mut e) => {
            // Goblin discovers dynamic symbols from PT_DYNAMIC. Relocatable
            // objects and section-only ELF files can still have SHT_DYNSYM.
            if e.dynsyms.is_empty() {
                if let Some(sh)=e.section_headers.iter().find(|s|s.sh_type==elf::section_header::SHT_DYNSYM) {
                    let width=if e.is_64 {24} else {16};
                    if sh.sh_entsize!=width || sh.sh_size%width!=0 || sh.sh_size/width>MAX_ROWS as u64 {return Err("Invalid or excessive dynamic symbol table".into());}
                    let ctx=goblin::container::Ctx::new(if e.is_64 {goblin::container::Container::Big} else {goblin::container::Container::Little},if e.little_endian {goblin::container::Endian::Little} else {goblin::container::Endian::Big});
                    e.dynsyms=elf::sym::Symtab::parse(data,sh.sh_offset as usize,(sh.sh_size/width) as usize,ctx).map_err(|e|e.to_string())?;
                    let strings=e.section_headers.get(sh.sh_link as usize).ok_or("Invalid symbol string table link")?;
                    e.dynstrtab=goblin::strtab::Strtab::parse(data,strings.sh_offset as usize,strings.sh_size as usize,0).map_err(|e|e.to_string())?;
                }
            }
            r["format"]=json!("ELF");
            r["header"]=json!({"size":data.len(),"class":if e.is_64 {64} else {32},
                "endianness":if e.little_endian {"little"} else {"big"},
                "machine":format!("EM_{}",elf::header::machine_to_str(e.header.e_machine)),
                "abi":elf::header::machine_to_str(e.header.e_machine),
                "file_type":format!("ET_{}",elf::header::et_to_str(e.header.e_type)),
                "entry_point":hex(e.entry),"num_sections":e.section_headers.len(),"num_segments":e.program_headers.len()});
            let mut end=(e.header.e_ehsize as u64).max(e.header.e_shoff.saturating_add(e.section_headers.len() as u64 * e.header.e_shentsize as u64))
                .max(e.header.e_phoff.saturating_add(e.program_headers.len() as u64 * e.header.e_phentsize as u64));
            for s in &e.section_headers {
                let name=e.shdr_strtab.get_at(s.sh_name).unwrap_or("");
                let file_size=if s.sh_type==elf::section_header::SHT_NOBITS {0} else {s.sh_size};
                sections.push(section(name,elf::section_header::sht_to_str(s.sh_type),s.sh_addr,s.sh_offset,s.sh_size,s.sh_flags,data,file_size));
                end=end.max(s.sh_offset.saturating_add(file_size));
            }
            for p in &e.program_headers {
                segments.push(json!({"type":elf::program_header::pt_to_str(p.p_type),"vaddr":hex(p.p_vaddr),"offset":hex(p.p_offset),"filesz":p.p_filesz,"memsz":p.p_memsz,"flags":p.p_flags}));
                end=end.max(p.p_offset.saturating_add(p.p_filesz));
            }
            for (table,strings,label) in [(&e.dynsyms,&e.dynstrtab,".dynsym"),(&e.syms,&e.strtab,".symtab")] {
                for s in table.iter() {
                    let name=strings.get_at(s.st_name).unwrap_or(""); if name.is_empty() {continue;}
                    let mut row=symbol(name,s.st_value,s.st_size,&format!("STT_{}",elf::sym::type_to_str(s.st_type())));
                    row["bind"]=json!(format!("STB_{}",elf::sym::bind_to_str(s.st_bind())));
                    row["section_table"]=json!(label);
                    row["shndx"]=if s.st_shndx==0 {json!("SHN_UNDEF")} else {json!(s.st_shndx)};
                    if s.st_shndx==0 {imports.push(row)} else if s.st_bind()==elf::sym::STB_LOCAL {locals.push(row)} else {exports.push(row)}
                }
            }
            r["dynamic"]=json!({"needed_libraries":e.libraries,"soname":e.soname,"rpath":e.rpaths.iter().chain(e.runpaths.iter()).collect::<Vec<_>>()});
            // Section relocations also include relocatable objects; use dynamic
            // tables when stripped ELF binaries have no section headers.
            if e.shdr_relocs.is_empty() {
                for (label,rels) in [(".rela.dyn",&e.dynrelas),(".rel.dyn",&e.dynrels),(".rela.plt",&e.pltrelocs)] {
                    for rel in rels.iter() {
                        let name=e.dynsyms.get(rel.r_sym).and_then(|s|e.dynstrtab.get_at(s.st_name)).unwrap_or("");
                        relocations.push(json!({"reloc_section":label,"offset":hex(rel.r_offset),"symbol":name,"demangled":demangle(name),"type":rel.r_type,"addend":rel.r_addend}));
                    }
                }
            } else {
                for (idx,rels) in &e.shdr_relocs {
                    let sh=&e.section_headers[*idx];
                    let dynamic=e.section_headers.get(sh.sh_link as usize).is_some_and(|s|s.sh_type==elf::section_header::SHT_DYNSYM);
                    for rel in rels.iter() {
                        let name=if dynamic {e.dynsyms.get(rel.r_sym).and_then(|s|e.dynstrtab.get_at(s.st_name))} else {e.syms.get(rel.r_sym).and_then(|s|e.strtab.get_at(s.st_name))}.unwrap_or("");
                        relocations.push(json!({"reloc_section":e.shdr_strtab.get_at(sh.sh_name).unwrap_or(""),"offset":hex(rel.r_offset),"symbol":name,"demangled":demangle(name),"type":rel.r_type,"addend":rel.r_addend}));
                    }
                }
            }
            let now=e.dynamic.as_ref().is_some_and(|d|d.dyns.iter().any(|v|
                v.d_tag==elf::dynamic::DT_BIND_NOW || (v.d_tag==elf::dynamic::DT_FLAGS && v.d_val&8!=0) || (v.d_tag==elf::dynamic::DT_FLAGS_1 && v.d_val&1!=0)));
            let relro=e.program_headers.iter().any(|p|p.p_type==elf::program_header::PT_GNU_RELRO);
            let names:Vec<_>=imports.iter().filter_map(|s|s["name"].as_str()).collect();
            r["security"]=json!({"stripped":e.syms.is_empty(),
                "nx_stack":e.program_headers.iter().find(|p|p.p_type==elf::program_header::PT_GNU_STACK).map(|p|p.p_flags&1==0),
                "relro":if relro {if now {"full"} else {"partial"}} else {"none"},
                "stack_canary":names.contains(&"__stack_chk_fail"),"fortify_source":names.iter().any(|n|n.ends_with("_chk")),
                "rpath_or_runpath":!e.rpaths.is_empty()||!e.runpaths.is_empty(),
                "note_pie":if e.header.e_type==elf::header::ET_DYN {"ET_DYN: shared object or PIE; type alone does not prove PIC"} else {"Not ET_DYN"}});
            r["appended_data"]=json!({"elf_content_end_offset":hex(end),"file_size":data.len(),"trailing_bytes":(data.len() as u64).saturating_sub(end)});
        },
        Object::PE(p) => {
            r["format"]=json!("PE");
            r["header"]=json!({"size":data.len(),"class":if p.is_64 {64} else {32},"endianness":"little",
                "machine":format!("PE_{:#x}",p.header.coff_header.machine),"abi":match p.header.coff_header.machine {0x8664=>"X86_64",0x14c=>"X86",0xaa64=>"AARCH64",0x1c4=>"ARM",_=>"Unknown"},
                "file_type":if p.is_lib {"DLL"} else {"Executable"},"entry_point":hex(p.image_base+p.entry as u64),"num_sections":p.sections.len(),"num_segments":0});
            for s in &p.sections {
                let flags=(if s.characteristics&0x80000000!=0 {1} else {0}) | (if s.characteristics&0x40000000!=0 {2} else {0}) | (if s.characteristics&0x20000000!=0 {4} else {0});
                sections.push(section(s.name().unwrap_or(""),"PE_SECTION",p.image_base+s.virtual_address as u64,s.pointer_to_raw_data as u64,s.virtual_size as u64,flags,data,s.size_of_raw_data as u64));
            }
            for s in p.exports {if let Some(name)=s.name {exports.push(symbol(name,p.image_base+s.rva as u64,0,"STT_NOTYPE"));}}
            for s in p.imports {let mut row=symbol(&s.name,p.image_base+s.rva as u64,s.size as u64,"STT_NOTYPE");row["library"]=json!(s.dll);imports.push(row);}
            r["dynamic"]["needed_libraries"]=json!(p.libraries);
            if let Some(o)=p.header.optional_header {let flags=o.windows_fields.dll_characteristics;r["security"]=json!({"aslr":flags&0x40!=0,"nx_compat":flags&0x100!=0,"high_entropy_va":flags&0x20!=0,"control_flow_guard":flags&0x4000!=0});}
        },
        Object::Mach(mach::Mach::Binary(m)) => {
            r["format"]=json!("Mach-O");
            r["header"]=json!({"size":data.len(),"class":if m.is_64 {64} else {32},"endianness":if m.little_endian {"little"} else {"big"},
                "machine":format!("MACH_{:#x}",m.header.cputype),"abi":match m.header.cputype {0x1000007=>"X86_64",7=>"X86",0x100000c=>"AARCH64",12=>"ARM",_=>"Unknown"},
                "file_type":format!("Mach-O type {}",m.header.filetype),"entry_point":hex(m.entry),"num_sections":0,"num_segments":m.segments.len()});
            for seg in &m.segments {
                let flags=(if seg.initprot&1!=0 {4} else {0}) | (if seg.initprot&2!=0 {2} else {0}) | (if seg.initprot&4!=0 {1} else {0});
                segments.push(json!({"type":seg.name().unwrap_or(""),"vaddr":hex(seg.vmaddr),"offset":hex(seg.fileoff),"filesz":seg.filesize,"memsz":seg.vmsize,"flags":flags}));
                for (s,_) in seg.sections().map_err(|e|e.to_string())? {
                    let flags=if s.flags&0x80000400!=0 {6} else {2};
                    let zerofill=matches!(s.flags&0xff,1|0xc|0x12);
                    sections.push(section(s.name().unwrap_or(""),"MACH_SECTION",s.addr,s.offset as u64,s.size,flags,data,if zerofill {0} else {s.size}));
                }
            }
            r["header"]["num_sections"]=json!(sections.len());
            for s in m.symbols() {let (name,s)=s.map_err(|e|e.to_string())?;if s.n_type&0xe0!=0 {continue;}
                let row=symbol(name,s.n_value,0,"STT_NOTYPE");
                if s.n_type&0xe==0 {imports.push(row)} else if s.n_type&1!=0 {exports.push(row)} else {locals.push(row)}
            }
            r["dynamic"]=json!({"needed_libraries":m.libs,"soname":m.name,"rpath":m.rpaths});
            r["security"]=json!({"pie":m.header.flags&0x200000!=0});
        },
        Object::Mach(mach::Mach::Fat(_)) => return Err("Universal Mach-O: extract an architecture slice before inspection".into()),
        _ => return Err("Unsupported binary format; use Hex for raw data".into()),
    }
    if [sections.len(),segments.len(),exports.len(),imports.len(),locals.len(),relocations.len()].iter().any(|n|*n>MAX_ROWS) {return Err("Binary metadata exceeds the 200000-row analysis limit".into());}
    let funcs:Vec<_>=exports.iter().chain(locals.iter()).filter(|s|s["type"]=="STT_FUNC").map(|s|json!({"name":s["name"],"demangled":s["demangled"],"addr":u64::from_str_radix(s["value"].as_str().unwrap_or("0x0").trim_start_matches("0x"),16).unwrap_or(0),"size":s["size"],"source":"symbol"})).collect();
    r["functions"]=json!(funcs);
    r["exec_sections"]=json!(sections.iter().filter(|s|s["flags"].as_u64().unwrap_or(0)&4!=0).map(|s|&s["name"]).collect::<Vec<_>>());
    r["symbols"]=json!({"total":exports.len()+imports.len()+locals.len(),"exported":exports,"imported":imports,"local":locals});
    r["sections"]=json!(sections);r["segments"]=json!(segments);r["relocations"]=json!(relocations);
    let mut spans: Vec<(u32, u32, u8)> = Vec::new();
    let mut start = 0usize;
    for i in 0..=data.len() {
        if i < data.len() && (0x20..=0x7e).contains(&data[i]) {
            continue;
        }
        let len = i - start;
        if len >= minimum {
            let nul = i < data.len() && data[i] == 0;
            let tier = if nul && (len >= 6 || data[start..i].contains(&b'/')) {
                0
            } else if nul {
                1
            } else if len >= 8 {
                2
            } else {
                3
            };
            spans.push((start as u32, len as u32, tier));
        }
        start = i + 1;
    }
    if spans.len() > MAX_ROWS {
        r["warnings"] = json!(["Strings truncated at 200000 entries"]);
        let mut selected: Vec<(u32, u32)> = Vec::with_capacity(MAX_ROWS);
        for tier in 0..=3u8 {
            for &(s, l, t) in &spans {
                if t == tier {
                    selected.push((s, l));
                    if selected.len() == MAX_ROWS {
                        break;
                    }
                }
            }
            if selected.len() == MAX_ROWS {
                break;
            }
        }
        selected.sort_unstable_by_key(|&(s, _)| s);
        let strings: Vec<Value> = selected
            .into_iter()
            .map(|(s, l)| {
                let st = s as usize;
                let en = st + l as usize;
                json!({"offset":hex(s as u64),"length":l as usize,"encoding":"ascii","value":String::from_utf8_lossy(&data[st..en])})
            })
            .collect();
        r["string_entries"] = json!(strings);
    } else {
        let strings: Vec<Value> = spans
            .into_iter()
            .map(|(s, l, _)| {
                let st = s as usize;
                let en = st + l as usize;
                json!({"offset":hex(s as u64),"length":l as usize,"encoding":"ascii","value":String::from_utf8_lossy(&data[st..en])})
            })
            .collect();
        r["string_entries"] = json!(strings);
    }
    Ok(r)
}

#[pyfunction]
#[pyo3(signature=(path, minimum=4))]
pub fn binary_analyze_json(py: Python<'_>, path: PathBuf, minimum: usize) -> PyResult<String> {
    if !(4..=1024).contains(&minimum) {return Err(PyValueError::new_err("minimum must be 4..1024"));}
    py.detach(move || -> PyResult<String> {
        let file=File::open(&path)?;
        if !file.metadata()?.is_file() {return Err(PyValueError::new_err("Expected a regular binary file"));}
        let mut data=Vec::new();file.take(MAX_BYTES+1).read_to_end(&mut data)?;
        if data.len() as u64>MAX_BYTES {return Err(PyValueError::new_err("Binary exceeds the 512 MiB analysis limit"));}
        let mut result=analyze(&data,minimum).map_err(PyValueError::new_err)?;
        result["filepath"]=json!(path.to_string_lossy());result["header"]["file"]=json!(path.to_string_lossy());
        serde_json::to_string(&result).map_err(|e|PyValueError::new_err(e.to_string()))
    })
}

fn sign_extend_21(imm: u32) -> i64 {
    let sign = 1i64 << 20;
    let v = imm as i64;
    (v & (sign - 1)) - (v & sign)
}

fn find_arm64_prologue(data: &[u8], va: u64, sec_va: u64, sec_off: usize) -> Option<u64> {
    if va < sec_va { return None; }
    let rel = (va - sec_va) as usize;
    let limit = 0x8000;
    let low = if rel > limit { rel - limit } else { 0 };
    if sec_off + rel > data.len() { return None; }
    let mut pos = rel & !3;
    while pos >= low {
        let off = sec_off + pos;
        if off + 4 <= data.len() {
            let w = u32::from_le_bytes([data[off], data[off + 1], data[off + 2], data[off + 3]]);
            if matches!(w, 0xD503233F | 0xD503237F | 0xD503245F | 0xD50324DF | 0xD503249F) {
                return Some(sec_va + pos as u64);
            }
            if (w & 0xFFC07FFF) == 0xA9807BFD {
                return Some(sec_va + pos as u64);
            }
        }
        if pos < 4 { break; }
        pos -= 4;
    }
    None
}

#[pyfunction]
#[pyo3(signature=(path, target_vas, abi, endian="little", max_hits=40, base_va=None))]
pub fn binary_fast_scan_xrefs_json(
    py: Python<'_>,
    path: PathBuf,
    target_vas: Vec<u64>,
    abi: &str,
    endian: &str,
    max_hits: usize,
    base_va: Option<u64>,
) -> PyResult<String> {
    let abi_owned = abi.to_string();
    let endian_owned = endian.to_string();
    py.detach(move || -> PyResult<String> {
        let file = File::open(&path)?;
        let mut data = Vec::new();
        file.take(MAX_BYTES + 1).read_to_end(&mut data)?;
        let targets: std::collections::HashSet<u64> = target_vas.into_iter().collect();
        if targets.is_empty() {
            return Ok("[]".to_string());
        }

        let is_little = endian_owned.to_lowercase() != "big";
        let is_aarch64 = matches!(abi_owned.to_uppercase().as_str(), "AARCH64" | "ARM64" | "PE_0XAA64");
        let is_x86_64 = matches!(abi_owned.to_uppercase().as_str(), "X86_64" | "X64" | "AMD64" | "PE_0X8664");

        let mut exec_secs = Vec::new();
        let mut data_secs = Vec::new();
        let mut text_sec: Option<(u64, usize, usize)> = None;

        if base_va.is_none() {
            if let Ok(obj) = Object::parse(&data) {
                match obj {
                    Object::Elf(e) => {
                        for s in &e.section_headers {
                            let is_exec = s.sh_flags & 4 != 0;
                            let va = s.sh_addr;
                            let off = s.sh_offset as usize;
                            let sz = s.sh_size as usize;
                            let name = e.shdr_strtab.get_at(s.sh_name).unwrap_or("").to_string();
                            if is_exec {
                                if text_sec.is_none() || name == ".text" {
                                    text_sec = Some((va, off, sz));
                                }
                                exec_secs.push((va, off, sz, name));
                            } else if s.sh_flags & 3 != 0 || matches!(name.as_str(), ".rodata" | ".data" | ".data.rel.ro" | "__bug_table") {
                                data_secs.push((va, off, sz, name));
                            }
                        }
                    }
                    Object::PE(p) => {
                        for s in &p.sections {
                            let flags = (if s.characteristics & 0x80000000 != 0 { 1 } else { 0 })
                                | (if s.characteristics & 0x40000000 != 0 { 2 } else { 0 })
                                | (if s.characteristics & 0x20000000 != 0 { 4 } else { 0 });
                            let va = p.image_base + s.virtual_address as u64;
                            let off = s.pointer_to_raw_data as usize;
                            let sz = s.size_of_raw_data as usize;
                            let name = s.name().unwrap_or("").to_string();
                            if flags & 4 != 0 {
                                if text_sec.is_none() || name == ".text" {
                                    text_sec = Some((va, off, sz));
                                }
                                exec_secs.push((va, off, sz, name));
                            } else if flags & 3 != 0 || name == ".data" || name == ".rdata" {
                                data_secs.push((va, off, sz, name));
                            }
                        }
                    }
                    _ => {}
                }
            }
        }

        if exec_secs.is_empty() {
            let raw_va = base_va.unwrap_or(0);
            exec_secs.push((raw_va, 0, data.len(), ".image".to_string()));
            data_secs.push((raw_va, 0, data.len(), ".image".to_string()));
            text_sec = Some((raw_va, 0, data.len()));
        }

        let mut hits = Vec::new();
        let pages: std::collections::HashSet<u64> = targets.iter().map(|va| va & !0xFFF).collect();

        // 1. Scan Executable Sections
        for (sec_va, sec_off, sec_sz, sec_name) in &exec_secs {
            if hits.len() >= max_hits {
                break;
            }
            let end = (sec_off + sec_sz).min(data.len());
            if *sec_off >= end {
                continue;
            }
            let slice = &data[*sec_off..end];

            if is_aarch64 && is_little {
                let mut recent_adrp: std::collections::HashMap<u32, (u64, u64)> = std::collections::HashMap::new();
                let n = slice.len() & !3;
                let mut off = 0;
                while off < n {
                    let insn = u32::from_le_bytes([slice[off], slice[off+1], slice[off+2], slice[off+3]]);
                    let pc = sec_va + off as u64;

                    if (insn & 0x9F000000) == 0x10000000 {
                        let rd = insn & 0x1F;
                        let immlo = (insn >> 29) & 0x3;
                        let immhi = (insn >> 5) & 0x7FFFF;
                        let imm = sign_extend_21((immhi << 2) | immlo) as u64;
                        let target = pc.wrapping_add(imm);
                        recent_adrp.remove(&rd);
                        if targets.contains(&target) {
                            let fn_addr = find_arm64_prologue(&data, pc, *sec_va, *sec_off);
                            hits.push(json!({
                                "from": format!("{:#x}", pc),
                                "to": format!("{:#x}", target),
                                "kind": "ADR",
                                "section": sec_name,
                                "function_address": fn_addr.map(|a| format!("{:#x}", a)).unwrap_or_default(),
                                "line": 0
                            }));
                            if hits.len() >= max_hits {
                                break;
                            }
                        }
                        off += 4;
                        continue;
                    }

                    if (insn & 0x9F000000) == 0x90000000 {
                        let rd = insn & 0x1F;
                        let immlo = (insn >> 29) & 0x3;
                        let immhi = (insn >> 5) & 0x7FFFF;
                        let imm = (sign_extend_21((immhi << 2) | immlo) << 12) as u64;
                        let page = (pc & !0xFFF).wrapping_add(imm);
                        if pages.contains(&page) {
                            recent_adrp.insert(rd, (pc, page));
                        } else {
                            recent_adrp.remove(&rd);
                        }
                        off += 4;
                        continue;
                    }

                    if (insn & 0xFF800000) == 0x91000000 {
                        let rd = insn & 0x1F;
                        let rn = (insn >> 5) & 0x1F;
                        if let Some((_, page)) = recent_adrp.get(&rn) {
                            let shift = (insn >> 22) & 0x3;
                            let imm12 = (((insn >> 10) & 0xFFF) << (if shift == 1 { 12 } else { 0 })) as u64;
                            let target = page.wrapping_add(imm12);
                            if targets.contains(&target) {
                                let fn_addr = find_arm64_prologue(&data, pc, *sec_va, *sec_off);
                                hits.push(json!({
                                    "from": format!("{:#x}", pc),
                                    "to": format!("{:#x}", target),
                                    "kind": "ADRP+ADD",
                                    "section": sec_name,
                                    "function_address": fn_addr.map(|a| format!("{:#x}", a)).unwrap_or_default(),
                                    "line": 0
                                }));
                                if hits.len() >= max_hits {
                                    break;
                                }
                            }
                            if rd != rn {
                                off += 4;
                                continue;
                            }
                        }
                        recent_adrp.remove(&rd);
                        off += 4;
                        continue;
                    }

                    if (insn & 0x3B000000) == 0x39000000 {
                        let rn = (insn >> 5) & 0x1F;
                        if let Some((_, page)) = recent_adrp.get(&rn) {
                            let size_bits = (insn >> 30) & 0x3;
                            let imm12 = (((insn >> 10) & 0xFFF) << size_bits) as u64;
                            let target = page.wrapping_add(imm12);
                            if targets.contains(&target) {
                                let fn_addr = find_arm64_prologue(&data, pc, *sec_va, *sec_off);
                                hits.push(json!({
                                    "from": format!("{:#x}", pc),
                                    "to": format!("{:#x}", target),
                                    "kind": "ADRP+LDR",
                                    "section": sec_name,
                                    "function_address": fn_addr.map(|a| format!("{:#x}", a)).unwrap_or_default(),
                                    "line": 0
                                }));
                                if hits.len() >= max_hits {
                                    break;
                                }
                            }
                        }
                        recent_adrp.remove(&rn);
                        off += 4;
                        continue;
                    }

                    off += 4;
                }
            } else if is_x86_64 && is_little {
                let n = slice.len();
                let mut i = 0;
                while i + 7 <= n {
                    let b0 = slice[i];
                    if (0x48..=0x4F).contains(&b0) && slice[i + 1] == 0x8D && (slice[i + 2] & 0xC7) == 0x05 {
                        let disp = i32::from_le_bytes([slice[i+3], slice[i+4], slice[i+5], slice[i+6]]);
                        let pc = sec_va + i as u64;
                        let target = pc.wrapping_add(7).wrapping_add(disp as i64 as u64);
                        if targets.contains(&target) {
                            hits.push(json!({
                                "from": format!("{:#x}", pc),
                                "to": format!("{:#x}", target),
                                "kind": "LEA [RIP]",
                                "section": sec_name,
                                "function_address": format!("{:#x}", pc),
                                "line": 0
                            }));
                            if hits.len() >= max_hits {
                                break;
                            }
                        }
                        i += 7;
                        continue;
                    }
                    i += 1;
                }
            }
        }

        // 2. Scan Data Sections for Pointer Tables and Linux __bug_table 32-bit Displacements
        for (sec_va, sec_off, sec_sz, sec_name) in &data_secs {
            if hits.len() >= max_hits {
                break;
            }
            let end = (sec_off + sec_sz).min(data.len());
            if *sec_off >= end {
                continue;
            }
            let slice = &data[*sec_off..end];
            let n = slice.len() & !3;
            let mut off = 0;
            while off < n {
                let disp = i32::from_le_bytes([slice[off], slice[off+1], slice[off+2], slice[off+3]]);
                let tbl_va = sec_va + off as u64;
                let target = tbl_va.wrapping_add(disp as i64 as u64);
                if targets.contains(&target) {
                    if off >= 4 {
                        let bug_disp = i32::from_le_bytes([slice[off-4], slice[off-3], slice[off-2], slice[off-1]]);
                        let code_va = (tbl_va - 4).wrapping_add(bug_disp as i64 as u64);
                        let line = if off + 6 <= slice.len() {
                            u16::from_le_bytes([slice[off+4], slice[off+5]])
                        } else {
                            0
                        };
                        let fn_addr = if let Some((t_va, t_off, _)) = text_sec {
                            find_arm64_prologue(&data, code_va, t_va, t_off)
                        } else {
                            None
                        };
                        hits.push(json!({
                            "from": format!("{:#x}", code_va),
                            "to": format!("{:#x}", target),
                            "kind": if line > 0 { format!("BUG_TABLE:L{}", line) } else { "BUG_TABLE".to_string() },
                            "section": sec_name,
                            "function_address": fn_addr.map(|a| format!("{:#x}", a)).unwrap_or_default(),
                            "line": line
                        }));
                        if hits.len() >= max_hits {
                            break;
                        }
                    }
                }

                if off + 8 <= slice.len() {
                    let ptr = u64::from_le_bytes([slice[off], slice[off+1], slice[off+2], slice[off+3], slice[off+4], slice[off+5], slice[off+6], slice[off+7]]);
                    if targets.contains(&ptr) {
                        hits.push(json!({
                            "from": format!("{:#x}", tbl_va),
                            "to": format!("{:#x}", ptr),
                            "kind": "PTR64",
                            "section": sec_name,
                            "function_address": "",
                            "line": 0
                        }));
                        if hits.len() >= max_hits {
                            break;
                        }
                    }
                }

                off += 4;
            }
        }

        serde_json::to_string(&hits).map_err(|e| PyValueError::new_err(e.to_string()))
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test] fn bad_input_is_an_error() {assert!(analyze(b"not an executable",4).is_err());}
    #[test] fn entropy_known_distributions() {assert_eq!(entropy(&[0;32]),0.0);assert!((entropy(&(0..=255).collect::<Vec<_>>())-8.0).abs()<0.001);}
}
