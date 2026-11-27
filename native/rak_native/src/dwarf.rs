// SPDX-License-Identifier: MPL-2.0
//! Read emitted function locations without retaining a tree of DWARF entries.
use gimli::{
    AttributeValue, DW_AT_abstract_origin, DW_AT_decl_file, DW_AT_low_pc, DW_AT_name,
    DW_AT_specification, DW_TAG_subprogram, DebuggingInformationEntry, Dwarf, EndianSlice,
    LittleEndian, Unit,
};
use goblin::elf::{section_header::SHF_COMPRESSED, Elf};

type Reader<'data> = EndianSlice<'data, LittleEndian>;
type Error = Box<dyn std::error::Error>;
pub type Location = (String, String, String, u64);

struct Declaration<'data> {
    name: Option<AttributeValue<Reader<'data>>>,
    file: Option<u64>,
    references: [Option<AttributeValue<Reader<'data>>>; 2],
}

impl<'data> Declaration<'data> {
    fn read(entry: &DebuggingInformationEntry<'_, '_, Reader<'data>>) -> gimli::Result<Self> {
        let file = match entry.attr_value(DW_AT_decl_file)? {
            Some(AttributeValue::FileIndex(index)) => Some(index),
            _ => None,
        };
        Ok(Self {
            name: entry.attr_value(DW_AT_name)?,
            file,
            references: [
                entry.attr_value(DW_AT_abstract_origin)?,
                entry.attr_value(DW_AT_specification)?,
            ],
        })
    }

    fn resolve(&mut self, unit: &Unit<Reader<'data>>) -> gimli::Result<bool> {
        for index in 0..self.references.len() {
            let offset = match self.references[index] {
                None => continue,
                Some(AttributeValue::UnitRef(offset)) => offset,
                Some(AttributeValue::DebugInfoRef(offset)) => {
                    let Some(offset) = offset.to_unit_offset(&unit.header) else {
                        return Ok(false);
                    };
                    offset
                }
                _ => return Ok(false),
            };
            *self = Self::read(&unit.entry(offset)?)?;
        }
        Ok(true)
    }
}

fn file_path<'data>(
    dwarf: &Dwarf<Reader<'data>>,
    unit: &Unit<Reader<'data>>,
    index: u64,
) -> gimli::Result<Option<String>> {
    let Some(program) = &unit.line_program else {
        return Ok(None);
    };
    let header = program.header();
    if header.version() < 5 && index == 0 {
        return Ok(None);
    }
    let Some(file) = header.file(index) else {
        return Ok(None);
    };
    let name = dwarf.attr_string(unit, file.path_name())?;
    let name = name.to_string_lossy();
    if name.starts_with('/') || (header.version() < 5 && file.directory_index() == 0) {
        return Ok(Some(name.into_owned()));
    }
    let directory = file
        .directory(header)
        .map(|value| dwarf.attr_string(unit, value))
        .transpose()?;
    Ok(Some(match directory {
        Some(path) if !path.is_empty() => format!("{}/{}", path.to_string_lossy(), name),
        _ => name.into_owned(),
    }))
}

pub fn function_locations(bytes: &[u8]) -> Result<Option<Vec<Location>>, Error> {
    let elf = Elf::parse(bytes)?;
    if elf.section_headers.iter().any(|section| {
        let name = elf.shdr_strtab.get_at(section.sh_name).unwrap_or("");
        name.starts_with(".zdebug_")
            || (name.starts_with(".debug_") && section.sh_flags & u64::from(SHF_COMPRESSED) != 0)
    }) {
        return Ok(None);
    }
    let dwarf = Dwarf::load(|id| {
        let section = elf
            .section_headers
            .iter()
            .find(|section| elf.shdr_strtab.get_at(section.sh_name) == Some(id.name()));
        let data = match section {
            Some(section) => section
                .file_range()
                .and_then(|range| bytes.get(range))
                .ok_or("DWARF section outside vmlinux")?,
            None => &[],
        };
        Ok::<_, Error>(Reader::new(data, LittleEndian))
    })?;
    let mut functions = Vec::new();
    let mut headers = dwarf.units();
    while let Some(header) = headers.next()? {
        let unit = dwarf.unit(header)?;
        let Some(source) = unit.name else { continue };
        let source = source.to_string_lossy();
        if !source.ends_with(".c") {
            continue;
        }
        let mut tree = unit.entries_tree(None)?;
        let mut children = tree.root()?.children();
        while let Some(child) = children.next()? {
            let entry = child.entry();
            if entry.tag() != DW_TAG_subprogram {
                continue;
            }
            let Some(pc) = entry.attr_value(DW_AT_low_pc)? else {
                continue;
            };
            let Some(address) = dwarf.attr_address(&unit, pc)? else {
                continue;
            };
            let mut declaration = Declaration::read(entry)?;
            if !declaration.resolve(&unit)? {
                continue;
            }
            let (Some(name), Some(file)) = (declaration.name, declaration.file) else {
                continue;
            };
            let Some(path) = file_path(&dwarf, &unit, file)? else {
                continue;
            };
            let symbol = dwarf
                .attr_string(&unit, name)?
                .to_string_lossy()
                .into_owned();
            functions.push((source.to_string(), path, symbol, address));
        }
    }
    Ok(Some(functions))
}
