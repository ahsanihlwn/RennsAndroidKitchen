// SPDX-License-Identifier: MPL-2.0
//! Read-only, format-aware analysis. No target code is executed.
use goblin::{elf, mach, Object};
use pyo3::{exceptions::PyValueError, prelude::*};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::{fs::File, io::Read, path::PathBuf};

const MAX_BYTES: u64 = 512 * 1024 * 1024;
const MAX_ROWS: usize = 200_000;

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
    let mut strings=Vec::new(); let mut start=0;
    for i in 0..=data.len() {
        if i<data.len() && (0x20..=0x7e).contains(&data[i]) {continue;}
        if i-start>=minimum {
            if strings.len()==MAX_ROWS {r["warnings"]=json!(["Strings truncated at 200000 entries"]);break;}
            strings.push(json!({"offset":hex(start as u64),"length":i-start,"encoding":"ascii","value":String::from_utf8_lossy(&data[start..i])}));
        }
        start=i+1;
    }
    r["string_entries"]=json!(strings);
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

#[cfg(test)]
mod tests {
    use super::*;
    #[test] fn bad_input_is_an_error() {assert!(analyze(b"not an executable",4).is_err());}
    #[test] fn entropy_known_distributions() {assert_eq!(entropy(&[0;32]),0.0);assert!((entropy(&(0..=255).collect::<Vec<_>>())-8.0).abs()<0.001);}
}
