// SPDX-License-Identifier: MPL-2.0
//! Native Rust Android DTBO container parser/builder and FDT-to-DTS decompiler.

use flate2::read::{GzDecoder, ZlibDecoder};
use flate2::write::{GzEncoder, ZlibEncoder};
use flate2::Compression;
use pyo3::exceptions::{PyOSError, PyValueError};
use pyo3::prelude::*;
use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::fs::{self, File};
use std::io::{Read, Write};
use std::path::PathBuf;

pub const DT_TABLE_MAGIC: u32 = 0xd7b7_ab1e;
pub const ACPIO_MAGIC: u32 = 0x4143_5049;
pub const DT_TABLE_HEADER_SIZE: usize = 32;
pub const DT_TABLE_ENTRY_SIZE: usize = 32;

pub const FDT_MAGIC: u32 = 0xd00d_feed;
const FDT_BEGIN_NODE: u32 = 1;
const FDT_END_NODE: u32 = 2;
const FDT_PROP: u32 = 3;
const FDT_NOP: u32 = 4;
const FDT_END: u32 = 9;

const COMP_NONE: u32 = 0;
const COMP_ZLIB: u32 = 1;
const COMP_GZIP: u32 = 2;
const COMP_FORMAT_MASK: u32 = 0x0f;

fn emit_py_progress(cb: Option<&Py<PyAny>>, pct: u8, stage: &str) {
    let Some(func) = cb else {
        return;
    };
    Python::attach(|py| {
        let _ = func.call1(py, (pct, stage));
    });
}

#[inline]
fn read_be_u32(buf: &[u8], off: usize) -> Option<u32> {
    let slice = buf.get(off..off + 4)?;
    Some(u32::from_be_bytes(slice.try_into().ok()?))
}

#[inline]
fn read_be_u64(buf: &[u8], off: usize) -> Option<u64> {
    let slice = buf.get(off..off + 8)?;
    Some(u64::from_be_bytes(slice.try_into().ok()?))
}

#[inline]
fn align4(x: usize) -> usize {
    (x + 3) & !3
}

#[inline]
fn align8(x: usize) -> usize {
    (x + 7) & !7
}

#[inline]
fn align_up(x: usize, a: usize) -> usize {
    if a <= 1 {
        x
    } else {
        (x + a - 1) & !(a - 1)
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DtboHeaderInfo {
    pub magic: u32,
    pub dt_type: String,
    pub total_size: u32,
    pub header_size: u32,
    pub dt_entry_size: u32,
    pub dt_entry_count: u32,
    pub dt_entries_offset: u32,
    pub page_size: u32,
    pub version: u32,
    pub file_size: u64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DtboEntryInfo {
    pub index: usize,
    pub dt_size: u32,
    pub dt_offset: u32,
    pub id: u32,
    pub rev: u32,
    pub flags: u32,
    pub custom: [u32; 4],
    pub compression: String,
    pub uncompressed_size: usize,
    pub is_fdt: bool,
}

#[derive(Debug, Clone, Serialize)]
pub struct DtboInspectJson {
    pub header: DtboHeaderInfo,
    pub entries: Vec<DtboEntryInfo>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DtboManifestEntry {
    pub index: usize,
    pub dtb_file: String,
    pub dts_file: Option<String>,
    pub id: u32,
    pub rev: u32,
    pub flags: u32,
    pub custom: [u32; 4],
    pub compression: String,
    pub orig_offset: u32,
    pub orig_size: u32,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DtboManifest {
    pub dt_type: String,
    pub version: u32,
    pub page_size: u32,
    pub original_total_size: u32,
    pub original_file_size: u64,
    pub entries: Vec<DtboManifestEntry>,
}

#[derive(Debug, Clone, Serialize)]
pub struct DtboUnpackResult {
    pub out_dir: String,
    pub manifest_path: String,
    pub page_size: u32,
    pub version: u32,
    pub entry_count: usize,
    pub dtb_files: Vec<String>,
    pub dts_files: Vec<String>,
}

#[derive(Debug, Clone, Deserialize)]
pub struct DtboPackEntrySpec {
    pub dtb_path: String,
    #[serde(default)]
    pub id: u32,
    #[serde(default)]
    pub rev: u32,
    #[serde(default)]
    pub flags: u32,
    #[serde(default)]
    pub custom: [u32; 4],
    #[serde(default)]
    pub compression: String,
}

#[derive(Debug, Clone, Deserialize)]
pub struct DtboPackSpec {
    #[serde(default = "default_dt_type")]
    pub dt_type: String,
    #[serde(default = "default_page_size")]
    pub page_size: u32,
    #[serde(default)]
    pub version: u32,
    #[serde(default)]
    pub pad_to_size: Option<u64>,
    pub entries: Vec<DtboPackEntrySpec>,
}

fn default_dt_type() -> String {
    "dtb".to_string()
}

fn default_page_size() -> u32 {
    2048
}

#[derive(Debug, Clone, Serialize)]
pub struct DtboPackResult {
    pub output_path: String,
    pub total_size: u32,
    pub written_size: u64,
    pub page_size: u32,
    pub version: u32,
    pub entry_count: usize,
}

fn compression_name(comp: u32) -> &'static str {
    match comp {
        COMP_ZLIB => "zlib",
        COMP_GZIP => "gzip",
        _ => "none",
    }
}

fn compression_code(name: &str) -> Result<u32, String> {
    match name.trim().to_ascii_lowercase().as_str() {
        "" | "none" | "raw" => Ok(COMP_NONE),
        "zlib" => Ok(COMP_ZLIB),
        "gzip" | "gz" => Ok(COMP_GZIP),
        other => Err(format!("Unsupported DTBO compression format: {other}")),
    }
}

pub(crate) fn parse_dtbo_container(blob: &[u8]) -> Result<(DtboHeaderInfo, Vec<(DtboEntryInfo, Vec<u8>)>), String> {
    if blob.len() < DT_TABLE_HEADER_SIZE {
        return Err("Invalid DTBO image: file smaller than 32-byte dt_table_header".into());
    }
    let magic = read_be_u32(blob, 0).unwrap();
    if magic != DT_TABLE_MAGIC && magic != ACPIO_MAGIC {
        return Err(format!("Not a DTBO/ACPIO container (magic 0x{magic:08x})"));
    }
    let total_size = read_be_u32(blob, 4).unwrap();
    let header_size = read_be_u32(blob, 8).unwrap();
    let dt_entry_size = read_be_u32(blob, 12).unwrap();
    let dt_entry_count = read_be_u32(blob, 16).unwrap();
    let dt_entries_offset = read_be_u32(blob, 20).unwrap();
    let page_size = read_be_u32(blob, 24).unwrap();
    let version = read_be_u32(blob, 28).unwrap();

    if (header_size as usize) < DT_TABLE_HEADER_SIZE {
        return Err(format!("Invalid dt_table header_size: {header_size}"));
    }
    if (dt_entry_size as usize) < DT_TABLE_ENTRY_SIZE {
        return Err(format!("Invalid dt_entry_size: {dt_entry_size}"));
    }
    if version > 1 {
        return Err(format!("Unsupported DTBO table version: {version}"));
    }

    let entries_end = (dt_entries_offset as usize)
        .checked_add((dt_entry_count as usize).saturating_mul(dt_entry_size as usize))
        .ok_or_else(|| "DTBO entry table overflow".to_string())?;
    if entries_end > blob.len() {
        return Err(format!(
            "DTBO entry table truncated (need {entries_end} bytes, file has {})",
            blob.len()
        ));
    }

    let header = DtboHeaderInfo {
        magic,
        dt_type: if magic == ACPIO_MAGIC {
            "acpi".to_string()
        } else {
            "dtb".to_string()
        },
        total_size,
        header_size,
        dt_entry_size,
        dt_entry_count,
        dt_entries_offset,
        page_size,
        version,
        file_size: blob.len() as u64,
    };

    let mut parsed = Vec::with_capacity(dt_entry_count as usize);
    for idx in 0..(dt_entry_count as usize) {
        let eoff = dt_entries_offset as usize + idx * (dt_entry_size as usize);
        let dt_size = read_be_u32(blob, eoff).unwrap();
        let dt_offset = read_be_u32(blob, eoff + 4).unwrap();
        let id = read_be_u32(blob, eoff + 8).unwrap();
        let rev = read_be_u32(blob, eoff + 12).unwrap();
        let (flags, custom) = if version == 1 {
            (
                read_be_u32(blob, eoff + 16).unwrap(),
                [
                    read_be_u32(blob, eoff + 20).unwrap(),
                    read_be_u32(blob, eoff + 24).unwrap(),
                    read_be_u32(blob, eoff + 28).unwrap(),
                    0,
                ],
            )
        } else {
            (
                0,
                [
                    read_be_u32(blob, eoff + 16).unwrap(),
                    read_be_u32(blob, eoff + 20).unwrap(),
                    read_be_u32(blob, eoff + 24).unwrap(),
                    read_be_u32(blob, eoff + 28).unwrap(),
                ],
            )
        };

        let start = dt_offset as usize;
        let end = start
            .checked_add(dt_size as usize)
            .ok_or_else(|| format!("DTBO entry {idx} offset overflow"))?;
        if end > blob.len() {
            return Err(format!(
                "DTBO entry {idx} extends past EOF ({end} > {})",
                blob.len()
            ));
        }

        let raw_slice = &blob[start..end];
        let comp_code = if version == 1 {
            flags & COMP_FORMAT_MASK
        } else {
            COMP_NONE
        };
        let payload = match comp_code {
            COMP_NONE => raw_slice.to_vec(),
            COMP_ZLIB => {
                let mut dec = ZlibDecoder::new(raw_slice);
                let mut out = Vec::new();
                dec.read_to_end(&mut out)
                    .map_err(|e| format!("Failed zlib decompressing DTBO entry {idx}: {e}"))?;
                out
            }
            COMP_GZIP => {
                let mut dec = GzDecoder::new(raw_slice);
                let mut out = Vec::new();
                dec.read_to_end(&mut out)
                    .map_err(|e| format!("Failed gzip decompressing DTBO entry {idx}: {e}"))?;
                out
            }
            other => {
                return Err(format!(
                    "Unsupported compression format 0x{other:x} in DTBO entry {idx}"
                ));
            }
        };

        let is_fdt = read_be_u32(&payload, 0) == Some(FDT_MAGIC);
        if magic == DT_TABLE_MAGIC && !is_fdt {
            return Err(format!("DTBO entry {idx} is not a valid FDT blob"));
        }

        let info = DtboEntryInfo {
            index: idx,
            dt_size,
            dt_offset,
            id,
            rev,
            flags,
            custom,
            compression: compression_name(comp_code).to_string(),
            uncompressed_size: payload.len(),
            is_fdt,
        };
        parsed.push((info, payload));
    }

    Ok((header, parsed))
}

// ============================================================================
// FDT -> DTS decompiler (matching dtc / core.devicetree.source output)
// ============================================================================

struct FdtHeader {
    totalsize: usize,
    off_dt_struct: usize,
    off_dt_strings: usize,
    off_mem_rsvmap: usize,
    version: u32,
}

struct FdtProp<'a> {
    name: String,
    val: &'a [u8],
}

struct FdtNode<'a> {
    name: String,
    props: Vec<FdtProp<'a>>,
    children: Vec<FdtNode<'a>>,
}

fn parse_fdt_header(buf: &[u8]) -> Result<FdtHeader, String> {
    if buf.len() < 40 {
        return Err("FDT blob smaller than 40-byte header".into());
    }
    let magic = read_be_u32(buf, 0).unwrap();
    if magic != FDT_MAGIC {
        return Err(format!("Bad FDT magic 0x{magic:08x}"));
    }
    let totalsize = read_be_u32(buf, 4).unwrap() as usize;
    let off_dt_struct = read_be_u32(buf, 8).unwrap() as usize;
    let off_dt_strings = read_be_u32(buf, 12).unwrap() as usize;
    let off_mem_rsvmap = read_be_u32(buf, 16).unwrap() as usize;
    let version = read_be_u32(buf, 20).unwrap();
    if totalsize < 40
        || totalsize > buf.len()
        || off_dt_struct >= totalsize
        || off_dt_strings > totalsize
        || off_mem_rsvmap > totalsize
    {
        return Err("Malformed FDT header offsets".into());
    }
    Ok(FdtHeader {
        totalsize,
        off_dt_struct,
        off_dt_strings,
        off_mem_rsvmap,
        version,
    })
}

fn read_cstr(buf: &[u8], start: usize) -> Result<(&str, usize), String> {
    let tail = buf
        .get(start..)
        .ok_or_else(|| "FDT string offset out of bounds".to_string())?;
    let rel_end = tail
        .iter()
        .position(|&b| b == 0)
        .ok_or_else(|| "Unterminated FDT string".to_string())?;
    let s = std::str::from_utf8(&tail[..rel_end])
        .map_err(|_| "Invalid UTF-8 in FDT string".to_string())?;
    Ok((s, start + rel_end + 1))
}

fn parse_fdt_node<'a>(
    fdt: &'a [u8],
    hdr: &FdtHeader,
    pos: &mut usize,
) -> Result<FdtNode<'a>, String> {
    let (raw_name, after_name) = read_cstr(&fdt[..hdr.totalsize], *pos + 4)?;
    let name = if hdr.version < 16 {
        raw_name.rsplit('/').next().unwrap_or(raw_name).to_string()
    } else {
        raw_name.to_string()
    };
    *pos = hdr.off_dt_struct + align4(after_name - hdr.off_dt_struct);

    let mut props = Vec::new();
    let mut children = Vec::new();

    loop {
        let tag = read_be_u32(&fdt[..hdr.totalsize], *pos)
            .ok_or_else(|| "Unexpected EOF in FDT struct".to_string())?;
        match tag {
            FDT_NOP => {
                *pos += 4;
            }
            FDT_PROP => {
                let len = read_be_u32(fdt, *pos + 4)
                    .ok_or_else(|| "Malformed FDT_PROP len".to_string())?
                    as usize;
                let nameoff = read_be_u32(fdt, *pos + 8)
                    .ok_or_else(|| "Malformed FDT_PROP nameoff".to_string())?
                    as usize;
                let mut val_off = *pos + 12;
                if hdr.version < 16 && len >= 8 {
                    let rel = val_off - hdr.off_dt_struct;
                    val_off = hdr.off_dt_struct + align8(rel);
                }
                let val_end = val_off
                    .checked_add(len)
                    .ok_or_else(|| "FDT_PROP length overflow".to_string())?;
                if val_end > hdr.totalsize {
                    return Err("FDT_PROP value out of bounds".into());
                }
                let (pname, _) =
                    read_cstr(&fdt[..hdr.totalsize], hdr.off_dt_strings + nameoff)?;
                props.push(FdtProp {
                    name: pname.to_string(),
                    val: &fdt[val_off..val_end],
                });
                *pos = hdr.off_dt_struct + align4(val_end - hdr.off_dt_struct);
            }
            FDT_BEGIN_NODE => {
                children.push(parse_fdt_node(fdt, hdr, pos)?);
            }
            FDT_END_NODE => {
                *pos += 4;
                break;
            }
            FDT_END => {
                return Err("Premature FDT_END inside node".into());
            }
            other => {
                return Err(format!("Unknown FDT token 0x{other:08x}"));
            }
        }
    }

    Ok(FdtNode {
        name,
        props,
        children,
    })
}

fn is_dts_string_char(c: u8) -> bool {
    c == 0 || (0x20..=0x7e).contains(&c) || matches!(c, 0x07..=0x0d)
}

fn is_string_list(raw: &[u8]) -> bool {
    if raw.is_empty() || *raw.last().unwrap() != 0 {
        return false;
    }
    let mut nnul = 0usize;
    for &b in raw {
        if !is_dts_string_char(b) {
            return false;
        }
        if b == 0 {
            nnul += 1;
        }
    }
    nnul <= raw.len() - nnul
}

fn format_dts_string_piece(raw: &[u8]) -> String {
    let mut out = String::with_capacity(raw.len() + 2);
    out.push('"');
    for &b in raw {
        match b {
            0x07 => out.push_str("\\a"),
            0x08 => out.push_str("\\b"),
            0x09 => out.push_str("\\t"),
            0x0a => out.push_str("\\n"),
            0x0b => out.push_str("\\v"),
            0x0c => out.push_str("\\f"),
            0x0d => out.push_str("\\r"),
            0x5c => out.push_str("\\\\"),
            0x22 => out.push_str("\\\""),
            0x20..=0x7e => out.push(b as char),
            other => out.push_str(&format!("\\x{other:02x}")),
        }
    }
    out.push('"');
    out
}

fn format_prop_value(raw: &[u8]) -> String {
    if raw.is_empty() {
        return ";".to_string();
    }
    let mut out = String::from(" = ");
    if is_string_list(raw) {
        let trimmed = &raw[..raw.len() - 1];
        for (idx, part) in trimmed.split(|&b| b == 0).enumerate() {
            if idx > 0 {
                out.push_str(", ");
            }
            out.push_str(&format_dts_string_piece(part));
        }
    } else if raw.len() % 4 == 0 {
        out.push('<');
        for (idx, chunk) in raw.chunks_exact(4).enumerate() {
            if idx > 0 {
                out.push(' ');
            }
            let val = u32::from_be_bytes(chunk.try_into().unwrap());
            out.push_str(&format!("0x{val:02x}"));
        }
        out.push('>');
    } else {
        out.push('[');
        for (idx, &b) in raw.iter().enumerate() {
            if idx > 0 {
                out.push(' ');
            }
            out.push_str(&format!("{b:02x}"));
        }
        out.push(']');
    }
    out.push(';');
    out
}

fn write_dts_node(node: &FdtNode<'_>, level: usize, out: &mut Vec<String>) {
    let prefix = "\t".repeat(level);
    if node.name.is_empty() {
        out.push(format!("{prefix}/ {{"));
    } else {
        out.push(format!("{prefix}{} {{", node.name));
    }

    let prop_prefix = "\t".repeat(level + 1);
    for prop in &node.props {
        out.push(format!("{prop_prefix}{}{}", prop.name, format_prop_value(prop.val)));
    }

    for child in &node.children {
        out.push(String::new());
        write_dts_node(child, level + 1, out);
    }

    out.push(format!("{prefix}}};"));
}

pub fn fdt_to_dts(fdt: &[u8]) -> Result<String, String> {
    let hdr = parse_fdt_header(fdt)?;
    let mut out = vec!["/dts-v1/;".to_string(), String::new()];

    let mut rsv_pos = hdr.off_mem_rsvmap;
    while rsv_pos + 16 <= hdr.totalsize {
        let addr = read_be_u64(fdt, rsv_pos).unwrap();
        let size = read_be_u64(fdt, rsv_pos + 8).unwrap();
        if addr == 0 && size == 0 {
            break;
        }
        out.push(format!("/memreserve/\t0x{addr:016x} 0x{size:016x};"));
        rsv_pos += 16;
    }

    let mut pos = hdr.off_dt_struct;
    while pos + 4 <= hdr.totalsize {
        let tag = read_be_u32(&fdt[..hdr.totalsize], pos).unwrap();
        if tag == FDT_NOP {
            pos += 4;
            continue;
        }
        if tag != FDT_BEGIN_NODE {
            return Err(format!("Expected FDT_BEGIN_NODE, found 0x{tag:08x}"));
        }
        let root = parse_fdt_node(fdt, &hdr, &mut pos)?;
        write_dts_node(&root, 0, &mut out);
        return Ok(out.join("\n") + "\n");
    }

    Err("No root node found in FDT blob".into())
}

// ============================================================================
// DTBO container builder
// ============================================================================

fn build_dtbo_image(spec: &DtboPackSpec, mut on_progress: impl FnMut(u8, &str)) -> Result<(Vec<u8>, u32), String> {
    if spec.entries.is_empty() {
        return Err("DTBO entry list cannot be empty".into());
    }
    if spec.version > 1 {
        return Err(format!("Unsupported DTBO version: {}", spec.version));
    }
    let magic = if spec.dt_type.eq_ignore_ascii_case("acpi") {
        ACPIO_MAGIC
    } else {
        DT_TABLE_MAGIC
    };
    let page_size = if spec.page_size == 0 { 2048 } else { spec.page_size };
    let count = spec.entries.len();
    let table_end = DT_TABLE_HEADER_SIZE + count * DT_TABLE_ENTRY_SIZE;

    let mut payload = Vec::new();
    let mut dedup: HashMap<(Vec<u8>, u32), (u32, u32)> = HashMap::new();
    let mut records: Vec<(u32, u32, &DtboPackEntrySpec)> = Vec::with_capacity(count);

    on_progress(5, "Reading DTBO entries");
    for (idx, entry) in spec.entries.iter().enumerate() {
        let raw_dtb = fs::read(&entry.dtb_path)
            .map_err(|e| format!("Failed reading '{}': {e}", entry.dtb_path))?;
        if magic == DT_TABLE_MAGIC && read_be_u32(&raw_dtb, 0) != Some(FDT_MAGIC) {
            return Err(format!("'{}' is not a valid DTB/FDT blob", entry.dtb_path));
        }

        let comp = if spec.version == 1 {
            let explicit = compression_code(&entry.compression)?;
            if explicit != COMP_NONE {
                explicit
            } else {
                entry.flags & COMP_FORMAT_MASK
            }
        } else {
            COMP_NONE
        };

        let encoded = match comp {
            COMP_NONE => raw_dtb,
            COMP_ZLIB => {
                let mut enc = ZlibEncoder::new(Vec::new(), Compression::default());
                enc.write_all(&raw_dtb).map_err(|e| e.to_string())?;
                enc.finish().map_err(|e| e.to_string())?
            }
            COMP_GZIP => {
                let mut enc = GzEncoder::new(Vec::new(), Compression::default());
                enc.write_all(&raw_dtb).map_err(|e| e.to_string())?;
                enc.finish().map_err(|e| e.to_string())?
            }
            _ => unreachable!(),
        };

        let (dt_offset, dt_size) = if let Some(&(off, sz)) = dedup.get(&(encoded.clone(), comp)) {
            (off, sz)
        } else {
            let start = table_end + payload.len();
            let aligned_start = if idx == 0 {
                // Standard mkdtboimg places first entry immediately after entry table
                start
            } else {
                align_up(start, 4)
            };
            if aligned_start > start {
                payload.resize(payload.len() + (aligned_start - start), 0);
            }
            let off = (table_end + payload.len()) as u32;
            let sz = encoded.len() as u32;
            dedup.insert((encoded.clone(), comp), (off, sz));
            payload.extend_from_slice(&encoded);
            (off, sz)
        };

        records.push((dt_size, dt_offset, entry));
        let pct = 10 + (((idx + 1) * 70) / count.max(1)) as u8;
        on_progress(pct, &format!("Packing entry {}/{}", idx + 1, count));
    }

    let total_size = (table_end + payload.len()) as u32;
    let final_len = match spec.pad_to_size {
        Some(target) if target > total_size as u64 => target as usize,
        _ => total_size as usize,
    };

    let mut out = vec![0u8; final_len];
    let header_vals = [
        magic,
        total_size,
        DT_TABLE_HEADER_SIZE as u32,
        DT_TABLE_ENTRY_SIZE as u32,
        count as u32,
        DT_TABLE_HEADER_SIZE as u32,
        page_size,
        spec.version,
    ];
    for (i, v) in header_vals.iter().enumerate() {
        out[i * 4..(i + 1) * 4].copy_from_slice(&v.to_be_bytes());
    }

    for (idx, (dt_size, dt_offset, entry)) in records.iter().enumerate() {
        let base = DT_TABLE_HEADER_SIZE + idx * DT_TABLE_ENTRY_SIZE;
        let words = if spec.version == 1 {
            [
                *dt_size,
                *dt_offset,
                entry.id,
                entry.rev,
                entry.flags,
                entry.custom[0],
                entry.custom[1],
                entry.custom[2],
            ]
        } else {
            [
                *dt_size,
                *dt_offset,
                entry.id,
                entry.rev,
                entry.custom[0],
                entry.custom[1],
                entry.custom[2],
                entry.custom[3],
            ]
        };
        for (i, w) in words.iter().enumerate() {
            out[base + i * 4..base + (i + 1) * 4].copy_from_slice(&w.to_be_bytes());
        }
    }

    out[table_end..table_end + payload.len()].copy_from_slice(&payload);
    on_progress(95, "Writing dtbo.img");
    Ok((out, total_size))
}

// ============================================================================
// PyO3 entry points
// ============================================================================

#[pyfunction]
pub fn dtbo_inspect_json(py: Python<'_>, img_path: PathBuf) -> PyResult<String> {
    let payload = py
        .detach(move || -> Result<DtboInspectJson, String> {
            let blob = fs::read(&img_path)
                .map_err(|e| format!("Failed reading '{}': {e}", img_path.display()))?;
            let (header, entries) = parse_dtbo_container(&blob)?;
            Ok(DtboInspectJson {
                header,
                entries: entries.into_iter().map(|(info, _)| info).collect(),
            })
        })
        .map_err(PyOSError::new_err)?;
    serde_json::to_string(&payload).map_err(|e| PyOSError::new_err(e.to_string()))
}

#[pyfunction]
#[pyo3(signature = (img_path, out_dir, decompile_dts=true, progress=None))]
pub fn dtbo_unpack_json(
    py: Python<'_>,
    img_path: PathBuf,
    out_dir: PathBuf,
    decompile_dts: bool,
    progress: Option<Py<PyAny>>,
) -> PyResult<String> {
    let cb = progress.as_ref();
    let payload = py
        .detach(move || -> Result<DtboUnpackResult, String> {
            emit_py_progress(cb, 5, "Reading dtbo.img");
            let blob = fs::read(&img_path)
                .map_err(|e| format!("Failed reading '{}': {e}", img_path.display()))?;
            let (header, entries) = parse_dtbo_container(&blob)?;
            if entries.is_empty() {
                return Err("DTBO image contains no device tree entries".into());
            }

            let dtb_dir = out_dir.join("dtb");
            let dts_dir = out_dir.join("dts");
            fs::create_dir_all(&dtb_dir).map_err(|e| e.to_string())?;
            if decompile_dts {
                fs::create_dir_all(&dts_dir).map_err(|e| e.to_string())?;
            }

            let total = entries.len();
            let mut dtb_files = Vec::with_capacity(total);
            let mut dts_files = Vec::with_capacity(total);
            let mut manifest_entries = Vec::with_capacity(total);

            for (idx, (info, fdt_bytes)) in entries.iter().enumerate() {
                let dtb_rel = format!("dtb/dtbo.{idx}.dtb");
                let dtb_path = out_dir.join(&dtb_rel);
                fs::write(&dtb_path, fdt_bytes).map_err(|e| e.to_string())?;
                dtb_files.push(dtb_path.to_string_lossy().into_owned());

                let dts_rel = if decompile_dts && info.is_fdt {
                    let dts_text = fdt_to_dts(fdt_bytes)
                        .map_err(|e| format!("Failed decompiling dtbo.{idx}: {e}"))?;
                    let rel = format!("dts/dtbo.{idx}.dts");
                    let dts_path = out_dir.join(&rel);
                    fs::write(&dts_path, dts_text).map_err(|e| e.to_string())?;
                    dts_files.push(dts_path.to_string_lossy().into_owned());
                    Some(rel)
                } else {
                    None
                };

                manifest_entries.push(DtboManifestEntry {
                    index: idx,
                    dtb_file: dtb_rel,
                    dts_file: dts_rel,
                    id: info.id,
                    rev: info.rev,
                    flags: info.flags,
                    custom: info.custom,
                    compression: info.compression.clone(),
                    orig_offset: info.dt_offset,
                    orig_size: info.dt_size,
                });

                let pct = 15 + (((idx + 1) * 80) / total.max(1)) as u8;
                emit_py_progress(cb, pct, &format!("Extracted entry {}/{}", idx + 1, total));
            }

            let manifest = DtboManifest {
                dt_type: header.dt_type.clone(),
                version: header.version,
                page_size: header.page_size,
                original_total_size: header.total_size,
                original_file_size: header.file_size,
                entries: manifest_entries,
            };
            let manifest_path = out_dir.join("dtbo_manifest.json");
            let manifest_json =
                serde_json::to_string_pretty(&manifest).map_err(|e| e.to_string())?;
            fs::write(&manifest_path, manifest_json).map_err(|e| e.to_string())?;

            emit_py_progress(cb, 100, "Done");
            Ok(DtboUnpackResult {
                out_dir: out_dir.to_string_lossy().into_owned(),
                manifest_path: manifest_path.to_string_lossy().into_owned(),
                page_size: header.page_size,
                version: header.version,
                entry_count: total,
                dtb_files,
                dts_files,
            })
        })
        .map_err(PyOSError::new_err)?;

    serde_json::to_string(&payload).map_err(|e| PyOSError::new_err(e.to_string()))
}

#[pyfunction]
#[pyo3(signature = (out_path, spec_json, progress=None))]
pub fn dtbo_pack_json(
    py: Python<'_>,
    out_path: PathBuf,
    spec_json: String,
    progress: Option<Py<PyAny>>,
) -> PyResult<String> {
    let spec: DtboPackSpec = serde_json::from_str(&spec_json)
        .map_err(|e| PyValueError::new_err(format!("Invalid dtbo spec_json: {e}")))?;
    let cb = progress.as_ref();

    let payload = py
        .detach(move || -> Result<DtboPackResult, String> {
            let (bytes, total_size) = build_dtbo_image(&spec, |pct, stage| {
                emit_py_progress(cb, pct, stage);
            })?;
            if let Some(parent) = out_path.parent() {
                if !parent.as_os_str().is_empty() && !parent.exists() {
                    fs::create_dir_all(parent).map_err(|e| e.to_string())?;
                }
            }
            let mut file = File::create(&out_path)
                .map_err(|e| format!("Failed creating '{}': {e}", out_path.display()))?;
            file.write_all(&bytes).map_err(|e| e.to_string())?;
            emit_py_progress(cb, 100, "Done");
            Ok(DtboPackResult {
                output_path: out_path.to_string_lossy().into_owned(),
                total_size,
                written_size: bytes.len() as u64,
                page_size: if spec.page_size == 0 { 2048 } else { spec.page_size },
                version: spec.version,
                entry_count: spec.entries.len(),
            })
        })
        .map_err(PyOSError::new_err)?;

    serde_json::to_string(&payload).map_err(|e| PyOSError::new_err(e.to_string()))
}

#[pyfunction]
pub fn fdt_to_dts_text(py: Python<'_>, dtb_path: PathBuf) -> PyResult<String> {
    py.detach(move || -> Result<String, String> {
        let bytes = fs::read(&dtb_path)
            .map_err(|e| format!("Failed reading '{}': {e}", dtb_path.display()))?;
        fdt_to_dts(&bytes)
    })
    .map_err(PyOSError::new_err)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn minimal_fdt() -> Vec<u8> {
        // Minimal valid v17 FDT: root node with prop `compatible = "test,dtbo\0"` and child `fragment@0`
        let strings = b"compatible\0target\0\0";
        let mut dt_struct = Vec::new();
        // FDT_BEGIN_NODE ""
        dt_struct.extend_from_slice(&FDT_BEGIN_NODE.to_be_bytes());
        dt_struct.extend_from_slice(&[0u8; 4]);
        // prop compatible = "test,dtbo\0"
        dt_struct.extend_from_slice(&FDT_PROP.to_be_bytes());
        dt_struct.extend_from_slice(&10u32.to_be_bytes());
        dt_struct.extend_from_slice(&0u32.to_be_bytes());
        dt_struct.extend_from_slice(b"test,dtbo\0\0\0");
        // child fragment@0
        dt_struct.extend_from_slice(&FDT_BEGIN_NODE.to_be_bytes());
        dt_struct.extend_from_slice(b"fragment@0\0\0");
        dt_struct.extend_from_slice(&FDT_PROP.to_be_bytes());
        dt_struct.extend_from_slice(&4u32.to_be_bytes());
        dt_struct.extend_from_slice(&11u32.to_be_bytes());
        dt_struct.extend_from_slice(&0xffff_ffffu32.to_be_bytes());
        dt_struct.extend_from_slice(&FDT_END_NODE.to_be_bytes());
        dt_struct.extend_from_slice(&FDT_END_NODE.to_be_bytes());
        dt_struct.extend_from_slice(&FDT_END.to_be_bytes());

        let hdr_size = 40u32;
        let rsv_off = 40u32;
        let rsv_size = 16u32;
        let struct_off = rsv_off + rsv_size;
        let str_off = struct_off + dt_struct.len() as u32;
        let total = str_off + strings.len() as u32;

        let mut out = vec![0u8; total as usize];
        let hdr = [
            FDT_MAGIC,
            total,
            struct_off,
            str_off,
            rsv_off,
            17,
            16,
            0,
            strings.len() as u32,
            dt_struct.len() as u32,
        ];
        for (i, v) in hdr.iter().enumerate() {
            out[i * 4..(i + 1) * 4].copy_from_slice(&v.to_be_bytes());
        }
        out[struct_off as usize..str_off as usize].copy_from_slice(&dt_struct);
        out[str_off as usize..total as usize].copy_from_slice(strings);
        out
    }

    #[test]
    fn test_fdt_to_dts_and_dtbo_roundtrip() {
        let fdt = minimal_fdt();
        let dts = fdt_to_dts(&fdt).unwrap();
        assert!(dts.starts_with("/dts-v1/;"));
        assert!(dts.contains("compatible = \"test,dtbo\";"));
        assert!(dts.contains("target = <0xffffffff>;"));

        let tmp = std::env::temp_dir().join("rak_dtbo_unit_test");
        let _ = fs::remove_dir_all(&tmp);
        fs::create_dir_all(&tmp).unwrap();
        let dtb_path = tmp.join("entry.dtb");
        fs::write(&dtb_path, &fdt).unwrap();

        let spec = DtboPackSpec {
            dt_type: "dtb".to_string(),
            page_size: 2048,
            version: 1,
            pad_to_size: None,
            entries: vec![DtboPackEntrySpec {
                dtb_path: dtb_path.to_string_lossy().into_owned(),
                id: 0x10,
                rev: 0x20,
                flags: COMP_GZIP,
                custom: [1, 2, 3, 0],
                compression: "gzip".to_string(),
            }],
        };
        let (img, _) = build_dtbo_image(&spec, |_, _| {}).unwrap();
        let (hdr, entries) = parse_dtbo_container(&img).unwrap();
        assert_eq!(hdr.version, 1);
        assert_eq!(hdr.page_size, 2048);
        assert_eq!(entries.len(), 1);
        assert_eq!(entries[0].0.id, 0x10);
        assert_eq!(entries[0].0.rev, 0x20);
        assert_eq!(entries[0].0.compression, "gzip");
        assert_eq!(entries[0].1, fdt);
        let _ = fs::remove_dir_all(&tmp);
    }
}
