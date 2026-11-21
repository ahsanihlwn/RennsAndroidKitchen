// SPDX-License-Identifier: MPL-2.0
//! Clean-room readers and extractors for standard/OEM firmware container formats:
//! Huawei UPDATE.APP, Unisoc PAC, LG KDZ/DZ, Coolpad CPB, Amlogic burn image, Allwinner IMAGEWTY.

use super::{safe_join_relative, OemEntry, OemInspectResult};
use flate2::read::ZlibDecoder;
use md5::{Digest as Md5Digest, Md5};
use std::collections::{HashMap, HashSet};
use std::fs::{self, File};
use std::io::{BufWriter, Read, Seek, SeekFrom, Write};
use std::path::Path;

#[inline]
fn le_u32(b: &[u8], o: usize) -> u32 {
    u32::from_le_bytes([b[o], b[o + 1], b[o + 2], b[o + 3]])
}

#[inline]
fn le_u64(b: &[u8], o: usize) -> u64 {
    u64::from_le_bytes([
        b[o], b[o + 1], b[o + 2], b[o + 3], b[o + 4], b[o + 5], b[o + 6], b[o + 7],
    ])
}

fn cstr_ascii(bytes: &[u8]) -> String {
    let end = bytes.iter().position(|&b| b == 0).unwrap_or(bytes.len());
    String::from_utf8_lossy(&bytes[..end])
        .chars()
        .filter(|c| c.is_ascii_graphic() || *c == ' ')
        .collect::<String>()
        .trim()
        .to_string()
}

fn copy_exact_stream(src: &mut File, dst: &mut File, mut remaining: u64) -> Result<(), String> {
    let mut buf = vec![0u8; 1024 * 1024];
    while remaining > 0 {
        let want = (remaining as usize).min(buf.len());
        src.read_exact(&mut buf[..want])
            .map_err(|e| format!("Unexpected EOF while copying stream: {e}"))?;
        dst.write_all(&buf[..want]).map_err(|e| e.to_string())?;
        remaining -= want as u64;
    }
    Ok(())
}

// ============================================================================
// 1. Huawei UPDATE.APP
// ============================================================================

const UAPP_MAGIC: [u8; 4] = [0x55, 0xAA, 0x5A, 0xA5];

pub fn parse_update_app(path: &Path) -> Result<Vec<(String, u64, u64)>, String> {
    let mut f = File::open(path).map_err(|e| e.to_string())?;
    let file_len = f.metadata().map_err(|e| e.to_string())?.len();
    let mut out = Vec::new();
    let mut seen: HashMap<String, usize> = HashMap::new();
    let mut pos = 0u64;

    // Skip optional leading padding (commonly 92 bytes) until first 55 AA 5A A5 marker.
    let mut probe = [0u8; 4];
    while pos + 98 <= file_len {
        f.seek(SeekFrom::Start(pos)).map_err(|e| e.to_string())?;
        if f.read_exact(&mut probe).is_err() {
            break;
        }
        if probe != UAPP_MAGIC {
            pos += 4;
            continue;
        }
        let mut hdr = [0u8; 94];
        if f.read_exact(&mut hdr).is_err() {
            break;
        }
        let header_size = le_u32(&hdr, 0) as u64;
        let data_size = le_u32(&hdr, 20) as u64;
        let raw_name = cstr_ascii(&hdr[56..72]).to_ascii_lowercase();
        let data_start = pos + header_size;
        if header_size < 98 || data_start + data_size > file_len {
            break;
        }
        let base_name = if raw_name.is_empty() {
            format!("part_{}", out.len())
        } else {
            raw_name
        };
        let cnt = seen.entry(base_name.clone()).or_insert(0);
        *cnt += 1;
        let final_name = if *cnt == 1 {
            format!("{base_name}.img")
        } else {
            format!("{base_name}_{cnt}.img")
        };
        out.push((final_name, data_start, data_size));
        let next = data_start + data_size;
        let rem = next % 4;
        pos = if rem == 0 { next } else { next + (4 - rem) };
    }
    Ok(out)
}

pub fn inspect_update_app(path: &Path) -> Result<OemInspectResult, String> {
    let parts = parse_update_app(path)?;
    let entries = parts
        .into_iter()
        .map(|(name, offset, size)| OemEntry {
            name,
            size,
            offset,
            kind: "partition".to_string(),
            extra: HashMap::new(),
        })
        .collect();
    Ok(OemInspectResult {
        kind: "UPDATE_APP".to_string(),
        metadata: HashMap::new(),
        entries,
    })
}

pub fn extract_update_app(
    path: &Path,
    out_dir: &Path,
    selected: Option<&HashSet<String>>,
) -> Result<Vec<String>, String> {
    let parts = parse_update_app(path)?;
    fs::create_dir_all(out_dir).map_err(|e| e.to_string())?;
    let mut src = File::open(path).map_err(|e| e.to_string())?;
    let mut extracted = Vec::new();
    for (name, offset, size) in parts {
        let stem = name.strip_suffix(".img").unwrap_or(&name);
        if let Some(sel) = selected {
            if !sel.contains(&name) && !sel.contains(stem) {
                continue;
            }
        }
        let dest = safe_join_relative(out_dir, &name)?;
        if let Some(p) = dest.parent() {
            fs::create_dir_all(p).map_err(|e| e.to_string())?;
        }
        src.seek(SeekFrom::Start(offset)).map_err(|e| e.to_string())?;
        let mut dst = File::create(&dest).map_err(|e| e.to_string())?;
        copy_exact_stream(&mut src, &mut dst, size)?;
        extracted.push(name);
    }
    Ok(extracted)
}

// ============================================================================
// 2. Unisoc / Spreadtrum PAC (backed by core/pac_extractor)
// ============================================================================

fn pac_header_crc_valid(path: &Path, header: &pac_extractor::pac::PacHeader) -> bool {
    if !header.has_magic() {
        return false;
    }
    let Ok(mut f) = File::open(path) else {
        return false;
    };
    let mut buf = [0u8; pac_extractor::pac::PAC_HEADER_CRC_SPAN];
    if f.read_exact(&mut buf).is_err() {
        return false;
    }
    pac_extractor::crc16::checksum(&buf) == header.crc1
}

pub fn inspect_pac(path: &Path) -> Result<OemInspectResult, String> {
    let mut reader = pac_extractor::pac::open_pac_file(path).map_err(|e| e.to_string())?;
    let header = reader.header().clone();
    let raw_entries = reader.read_partitions().map_err(|e| e.to_string())?;
    let crc_valid = pac_header_crc_valid(path, &header);

    let total_size = header.total_size();
    let has_magic = header.has_magic();
    let mut metadata = HashMap::new();
    metadata.insert("pac_version".to_string(), header.version);
    metadata.insert("product_name".to_string(), header.product_name.clone());
    metadata.insert("firmware_name".to_string(), header.product_name);
    metadata.insert("firmware_version".to_string(), header.firmware_name);
    metadata.insert("alias".to_string(), header.product_alias);
    metadata.insert("total_size".to_string(), total_size.to_string());
    metadata.insert("has_magic".to_string(), has_magic.to_string());
    metadata.insert("header_crc_valid".to_string(), crc_valid.to_string());

    let entries = raw_entries
        .into_iter()
        .filter(|e| !e.file_name.is_empty() && e.partition_size() > 0)
        .map(|entry| {
            let size = entry.partition_size();
            let offset = entry.data_offset();
            let mut extra = HashMap::new();
            extra.insert("partition_id".to_string(), entry.partition_id);
            extra.insert("type".to_string(), entry.file_flag.to_string());
            extra.insert("check_flag".to_string(), entry.check_flag.to_string());
            extra.insert("can_omit_flag".to_string(), entry.can_omit_flag.to_string());
            OemEntry {
                name: entry.file_name,
                size,
                offset,
                kind: "file".to_string(),
                extra,
            }
        })
        .collect();
    Ok(OemInspectResult {
        kind: "PAC".to_string(),
        metadata,
        entries,
    })
}

pub fn extract_pac(
    path: &Path,
    out_dir: &Path,
    selected: Option<&HashSet<String>>,
) -> Result<Vec<String>, String> {
    let mut reader = pac_extractor::pac::open_pac_file(path).map_err(|e| e.to_string())?;
    let entries = reader.read_partitions().map_err(|e| e.to_string())?;
    pac_extractor::pac::prepare_output_dir(out_dir).map_err(|e| e.to_string())?;

    let mut extracted = Vec::new();
    for entry in entries {
        if entry.partition_size() == 0 || entry.file_name.is_empty() {
            continue;
        }
        let leaf = entry
            .file_name
            .rsplit(['/', '\\'])
            .find(|segment| !segment.is_empty())
            .unwrap_or(&entry.file_name);
        if let Some(sel) = selected {
            if !sel.contains(&entry.file_name) && !sel.contains(leaf) {
                continue;
            }
        }
        if let Some(dest) = reader
            .extract_entry(&entry, out_dir, |_, _| {})
            .map_err(|e| e.to_string())?
        {
            let name = dest
                .file_name()
                .map(|s| s.to_string_lossy().into_owned())
                .unwrap_or_else(|| leaf.to_string());
            extracted.push(name);
        }
    }
    Ok(extracted)
}

// ============================================================================
// 3. LG KDZ & DZ
// ============================================================================

const KDZ_MAGICS: [[u8; 8]; 3] = [
    [0x28, 0x05, 0x00, 0x00, b'$', b'8', b'"', b'%'],
    [0x28, 0x05, 0x00, 0x00, b'4', b'1', b'%', 0x80],
    [0x18, 0x05, 0x00, 0x00, b'2', b'y', b'D', b'P'],
];
const DZ_FILE_MAGIC: [u8; 4] = [0x32, 0x96, 0x18, 0x74];
const DZ_CHUNK_MAGIC: [u8; 4] = [0x30, 0x12, 0x95, 0x78];

fn parse_kdz(path: &Path) -> Result<Vec<(String, u64, u64)>, String> {
    let mut f = File::open(path).map_err(|e| e.to_string())?;
    let file_len = f.metadata().map_err(|e| e.to_string())?.len();
    let mut magic = [0u8; 8];
    f.read_exact(&mut magic).map_err(|e| e.to_string())?;
    if !KDZ_MAGICS.contains(&magic) {
        return Err("Not a recognized KDZ header".to_string());
    }

    let mut segments = Vec::new();
    let mut entry = [0u8; 272];
    let mut min_offset = file_len;

    loop {
        let pos = f.stream_position().map_err(|e| e.to_string())?;
        if pos + 272 > min_offset || pos + 272 > file_len {
            break;
        }
        if f.read_exact(&mut entry).is_err() {
            break;
        }
        if entry.iter().all(|&b| b == 0) {
            break;
        }
        let name = cstr_ascii(&entry[0..256]);
        let length = le_u64(&entry, 256);
        let offset = le_u64(&entry, 264);
        if name.is_empty() || length == 0 || offset + length > file_len {
            break;
        }
        min_offset = min_offset.min(offset);
        segments.push((name, offset, length));
        // Peek next 4 bytes; end of header table is typically marked by 0x00000000 or 0x03.
        let mut next4 = [0u8; 4];
        if f.read_exact(&mut next4).is_ok() {
            if le_u32(&next4, 0) == 0 {
                break;
            }
            f.seek(SeekFrom::Current(-4)).map_err(|e| e.to_string())?;
        }
    }
    Ok(segments)
}

pub fn inspect_kdz(path: &Path) -> Result<OemInspectResult, String> {
    let segs = parse_kdz(path)?;
    let entries = segs
        .into_iter()
        .map(|(name, offset, size)| OemEntry {
            name,
            size,
            offset,
            kind: "segment".to_string(),
            extra: HashMap::new(),
        })
        .collect();
    Ok(OemInspectResult {
        kind: "KDZ".to_string(),
        metadata: HashMap::new(),
        entries,
    })
}

pub fn extract_kdz(
    path: &Path,
    out_dir: &Path,
    selected: Option<&HashSet<String>>,
) -> Result<Vec<String>, String> {
    let segs = parse_kdz(path)?;
    fs::create_dir_all(out_dir).map_err(|e| e.to_string())?;
    let mut src = File::open(path).map_err(|e| e.to_string())?;
    let mut extracted = Vec::new();
    for (name, offset, size) in segs {
        if let Some(sel) = selected {
            if !sel.contains(&name) {
                continue;
            }
        }
        let dest = safe_join_relative(out_dir, &name)?;
        src.seek(SeekFrom::Start(offset)).map_err(|e| e.to_string())?;
        let mut dst = File::create(&dest).map_err(|e| e.to_string())?;
        copy_exact_stream(&mut src, &mut dst, size)?;
        extracted.push(name);
    }
    Ok(extracted)
}

struct DzChunkMeta {
    slice_name: String,
    chunk_name: String,
    target_size: u64,
    data_size: u64,
    data_offset: u64,
    target_addr: u64,
    md5: [u8; 16],
}

struct DzParsed {
    device: String,
    version: String,
    chunks: Vec<DzChunkMeta>,
}

fn parse_dz(path: &Path) -> Result<DzParsed, String> {
    let mut f = File::open(path).map_err(|e| e.to_string())?;
    let file_len = f.metadata().map_err(|e| e.to_string())?.len();
    let mut hdr = [0u8; 512];
    f.read_exact(&mut hdr).map_err(|e| e.to_string())?;
    if hdr[0..4] != DZ_FILE_MAGIC {
        return Err("Invalid DZ magic".to_string());
    }
    let device = cstr_ascii(&hdr[16..48]);
    let version = cstr_ascii(&hdr[48..192]);
    let chunk_count = le_u32(&hdr, 192) as usize;

    let mut chunks = Vec::with_capacity(chunk_count.min(4096));
    let mut ch = [0u8; 512];
    loop {
        let pos = f.stream_position().map_err(|e| e.to_string())?;
        if pos + 512 > file_len {
            break;
        }
        if f.read_exact(&mut ch).is_err() {
            break;
        }
        if ch[0..4] != DZ_CHUNK_MAGIC {
            break;
        }
        let slice_name = cstr_ascii(&ch[4..36]);
        let chunk_name = cstr_ascii(&ch[36..100]);
        let target_size = le_u32(&ch, 100) as u64;
        let data_size = le_u32(&ch, 104) as u64;
        let mut md5 = [0u8; 16];
        md5.copy_from_slice(&ch[108..124]);
        let target_addr = le_u32(&ch, 124) as u64;
        let data_offset = pos + 512;
        if data_offset + data_size > file_len {
            break;
        }
        chunks.push(DzChunkMeta {
            slice_name,
            chunk_name,
            target_size,
            data_size,
            data_offset,
            target_addr,
            md5,
        });
        f.seek(SeekFrom::Start(data_offset + data_size))
            .map_err(|e| e.to_string())?;
        if chunks.len() >= chunk_count && chunk_count > 0 {
            break;
        }
    }

    Ok(DzParsed {
        device,
        version,
        chunks,
    })
}

pub fn inspect_dz(path: &Path) -> Result<OemInspectResult, String> {
    let parsed = parse_dz(path)?;
    let mut metadata = HashMap::new();
    metadata.insert("device".to_string(), parsed.device);
    metadata.insert("version".to_string(), parsed.version);

    let mut slices: HashMap<String, (u64, u64, usize)> = HashMap::new();
    for ch in parsed.chunks {
        let entry = slices
            .entry(ch.slice_name)
            .or_insert((ch.data_offset, 0u64, 0usize));
        entry.1 += ch.target_size;
        entry.2 += 1;
    }
    let mut entries: Vec<OemEntry> = slices
        .into_iter()
        .map(|(name, (offset, size, count))| {
            let mut extra = HashMap::new();
            extra.insert("chunks".to_string(), count.to_string());
            OemEntry {
                name: format!("{name}.img"),
                size,
                offset,
                kind: "slice".to_string(),
                extra,
            }
        })
        .collect();
    entries.sort_by(|a, b| a.name.cmp(&b.name));
    Ok(OemInspectResult {
        kind: "DZ".to_string(),
        metadata,
        entries,
    })
}

pub fn extract_dz(
    path: &Path,
    out_dir: &Path,
    selected: Option<&HashSet<String>>,
) -> Result<Vec<String>, String> {
    let parsed = parse_dz(path)?;
    fs::create_dir_all(out_dir).map_err(|e| e.to_string())?;
    let mut src = File::open(path).map_err(|e| e.to_string())?;

    let mut by_slice: HashMap<String, Vec<DzChunkMeta>> = HashMap::new();
    for ch in parsed.chunks {
        by_slice.entry(ch.slice_name.clone()).or_default().push(ch);
    }

    let mut extracted = Vec::new();
    for (slice_name, mut slice_chunks) in by_slice {
        let out_name = format!("{slice_name}.img");
        if let Some(sel) = selected {
            if !sel.contains(&out_name) && !sel.contains(&slice_name) {
                continue;
            }
        }
        slice_chunks.sort_by_key(|c| c.target_addr);
        let dest = safe_join_relative(out_dir, &out_name)?;
        let mut dst = BufWriter::new(File::create(&dest).map_err(|e| e.to_string())?);
        let base_addr = slice_chunks.first().map(|c| c.target_addr).unwrap_or(0);

        for ch in slice_chunks {
            let rel_offset = ch.target_addr.saturating_sub(base_addr) * 4096;
            dst.seek(SeekFrom::Start(rel_offset))
                .map_err(|e| e.to_string())?;
            src.seek(SeekFrom::Start(ch.data_offset))
                .map_err(|e| e.to_string())?;
            let mut comp = vec![0u8; ch.data_size as usize];
            src.read_exact(&mut comp).map_err(|e| e.to_string())?;

            let mut decompressed = Vec::with_capacity(ch.target_size as usize);
            if comp.starts_with(&[0x28, 0xB5, 0x2F, 0xFD]) {
                let mut dec = zstd::stream::read::Decoder::new(&comp[..]).map_err(|e| e.to_string())?;
                dec.read_to_end(&mut decompressed)
                    .map_err(|e| format!("DZ zstd error in {}: {e}", ch.chunk_name))?;
            } else {
                let mut dec = ZlibDecoder::new(&comp[..]);
                dec.read_to_end(&mut decompressed)
                    .map_err(|e| format!("DZ zlib error in {}: {e}", ch.chunk_name))?;
            }

            if ch.md5 != [0u8; 16] {
                let actual = Md5::digest(&decompressed);
                if actual.as_slice() != &ch.md5 {
                    return Err(format!("DZ MD5 mismatch on chunk {}", ch.chunk_name));
                }
            }
            dst.write_all(&decompressed).map_err(|e| e.to_string())?;
        }
        dst.flush().map_err(|e| e.to_string())?;
        extracted.push(out_name);
    }
    extracted.sort();
    Ok(extracted)
}

// ============================================================================
// 4. Coolpad CPB
// ============================================================================

const CPB_HEADER_SIZE: usize = 552;
const CPB_ENTRY_SIZE: usize = 76;

struct CpbParsed {
    model: String,
    hardware_version: String,
    version: String,
    entries: Vec<(String, u64, u64)>,
}

fn parse_cpb(path: &Path) -> Result<CpbParsed, String> {
    let mut f = File::open(path).map_err(|e| e.to_string())?;
    let file_len = f.metadata().map_err(|e| e.to_string())?.len();
    let mut hdr = [0u8; CPB_HEADER_SIZE];
    f.read_exact(&mut hdr).map_err(|e| e.to_string())?;
    if &hdr[0..2] != b"CP" {
        return Err("Invalid CPB magic".to_string());
    }
    let model = cstr_ascii(&hdr[48..80]);
    let hardware_version = cstr_ascii(&hdr[80..96]);
    let version = cstr_ascii(&hdr[96..160]);
    let img_hdr_end = le_u32(&hdr, 416) as usize;
    if img_hdr_end < CPB_HEADER_SIZE || (img_hdr_end as u64) > file_len {
        return Err("Invalid CPB image header end offset".to_string());
    }
    let table_len = img_hdr_end - CPB_HEADER_SIZE;
    let count = table_len / CPB_ENTRY_SIZE;
    let mut entries = Vec::with_capacity(count);
    let mut rec = [0u8; CPB_ENTRY_SIZE];
    for _ in 0..count {
        f.read_exact(&mut rec).map_err(|e| e.to_string())?;
        let name = cstr_ascii(&rec[0..64]);
        let offset = le_u32(&rec, 64) as u64;
        let size = le_u32(&rec, 68) as u64;
        if !name.is_empty() && offset + size <= file_len {
            entries.push((name, offset, size));
        }
    }
    Ok(CpbParsed {
        model,
        hardware_version,
        version,
        entries,
    })
}

pub fn inspect_cpb(path: &Path) -> Result<OemInspectResult, String> {
    let parsed = parse_cpb(path)?;
    let mut metadata = HashMap::new();
    metadata.insert("model".to_string(), parsed.model);
    metadata.insert("hardware_version".to_string(), parsed.hardware_version);
    metadata.insert("version".to_string(), parsed.version);
    let entries = parsed
        .entries
        .into_iter()
        .map(|(name, offset, size)| OemEntry {
            name,
            size,
            offset,
            kind: "file".to_string(),
            extra: HashMap::new(),
        })
        .collect();
    Ok(OemInspectResult {
        kind: "CPB".to_string(),
        metadata,
        entries,
    })
}

pub fn extract_cpb(
    path: &Path,
    out_dir: &Path,
    selected: Option<&HashSet<String>>,
) -> Result<Vec<String>, String> {
    let parsed = parse_cpb(path)?;
    fs::create_dir_all(out_dir).map_err(|e| e.to_string())?;
    let mut src = File::open(path).map_err(|e| e.to_string())?;
    let mut extracted = Vec::new();
    for (name, offset, size) in parsed.entries {
        if let Some(sel) = selected {
            if !sel.contains(&name) {
                continue;
            }
        }
        let dest = safe_join_relative(out_dir, &name)?;
        if let Some(p) = dest.parent() {
            fs::create_dir_all(p).map_err(|e| e.to_string())?;
        }
        src.seek(SeekFrom::Start(offset)).map_err(|e| e.to_string())?;
        let mut dst = File::create(&dest).map_err(|e| e.to_string())?;
        copy_exact_stream(&mut src, &mut dst, size)?;
        extracted.push(name);
    }
    Ok(extracted)
}

// ============================================================================
// 5. Amlogic Burn Image (v2)
// ============================================================================

const AML_MAGIC: u32 = 0x27B5_1956;
const AML_HEADER_SIZE: usize = 72;
const AML_ITEM_SIZE: usize = 576;

struct AmlParsed {
    version: u32,
    entries: Vec<(String, String, String, u64, u64, bool)>,
}

fn parse_amlogic(path: &Path) -> Result<AmlParsed, String> {
    let mut f = File::open(path).map_err(|e| e.to_string())?;
    let file_len = f.metadata().map_err(|e| e.to_string())?.len();
    let mut hdr = [0u8; AML_HEADER_SIZE];
    f.read_exact(&mut hdr).map_err(|e| e.to_string())?;
    let version = le_u32(&hdr, 4);
    let magic = le_u32(&hdr, 8);
    if magic != AML_MAGIC {
        return Err("Invalid Amlogic burn image magic".to_string());
    }
    if version != 2 {
        return Err(format!("Unsupported Amlogic image version {version} (only v2 supported)"));
    }
    let item_num = le_u32(&hdr, 24) as usize;
    if item_num > 4096 {
        return Err("Unreasonable Amlogic item count".to_string());
    }
    let mut entries = Vec::with_capacity(item_num);
    let mut item = [0u8; AML_ITEM_SIZE];
    for _ in 0..item_num {
        f.read_exact(&mut item).map_err(|e| e.to_string())?;
        let offset = le_u64(&item, 8);
        let size = le_u64(&item, 16);
        let main_type = cstr_ascii(&item[24..280]);
        let sub_type = cstr_ascii(&item[280..536]);
        let is_verify = main_type == "VERIFY";
        if !is_verify && offset + size <= file_len {
            let out_name = if main_type.is_empty() {
                sub_type.clone()
            } else {
                format!("{sub_type}.{main_type}")
            };
            entries.push((out_name, main_type, sub_type, offset, size, is_verify));
        }
    }
    Ok(AmlParsed { version, entries })
}

pub fn inspect_amlogic(path: &Path) -> Result<OemInspectResult, String> {
    let parsed = parse_amlogic(path)?;
    let mut metadata = HashMap::new();
    metadata.insert("version".to_string(), parsed.version.to_string());
    let entries = parsed
        .entries
        .into_iter()
        .map(|(name, main_t, sub_t, offset, size, _)| {
            let mut extra = HashMap::new();
            extra.insert("main_type".to_string(), main_t);
            extra.insert("sub_type".to_string(), sub_t);
            OemEntry {
                name,
                size,
                offset,
                kind: "item".to_string(),
                extra,
            }
        })
        .collect();
    Ok(OemInspectResult {
        kind: "AMLOGIC".to_string(),
        metadata,
        entries,
    })
}

pub fn extract_amlogic(
    path: &Path,
    out_dir: &Path,
    selected: Option<&HashSet<String>>,
) -> Result<Vec<String>, String> {
    let parsed = parse_amlogic(path)?;
    fs::create_dir_all(out_dir).map_err(|e| e.to_string())?;
    let mut src = File::open(path).map_err(|e| e.to_string())?;
    let mut extracted = Vec::new();
    for (name, _main_t, _sub_t, offset, size, _verify) in parsed.entries {
        if let Some(sel) = selected {
            if !sel.contains(&name) {
                continue;
            }
        }
        let dest = safe_join_relative(out_dir, &name)?;
        src.seek(SeekFrom::Start(offset)).map_err(|e| e.to_string())?;
        let mut dst = File::create(&dest).map_err(|e| e.to_string())?;
        copy_exact_stream(&mut src, &mut dst, size)?;
        extracted.push(name);
    }
    Ok(extracted)
}

// ============================================================================
// 6. Allwinner IMAGEWTY
// ============================================================================

const IMAGEWTY_MAGIC: &[u8; 8] = b"IMAGEWTY";
const IMAGEWTY_FILE_HDR_START: u64 = 1024;
const IMAGEWTY_FILE_HDR_SIZE: usize = 1024;

struct AllwinnerParsed {
    version: u32,
    entries: Vec<(String, String, u64, u64)>,
}

fn parse_allwinner(path: &Path) -> Result<AllwinnerParsed, String> {
    let mut f = File::open(path).map_err(|e| e.to_string())?;
    let file_len = f.metadata().map_err(|e| e.to_string())?.len();
    let mut hdr = [0u8; 96];
    f.read_exact(&mut hdr).map_err(|e| e.to_string())?;
    if &hdr[0..8] != IMAGEWTY_MAGIC {
        return Err("Invalid IMAGEWTY magic (or encrypted Allwinner image)".to_string());
    }
    let version = le_u32(&hdr, 12);
    // In IMAGEWTY format, V1 subheader starts at 32 (num_files at +24 => 56),
    // V3 subheader has an extra 4-byte field so num_files sits at +28 => 60.
    let num_files = if version >= 0x0100_2340 {
        le_u32(&hdr, 60)
    } else {
        le_u32(&hdr, 56)
    } as usize;

    if num_files == 0 || num_files > 1024 {
        return Err(format!("Unexpected Allwinner num_files: {num_files}"));
    }

    let mut entries = Vec::with_capacity(num_files);
    let mut rec = [0u8; IMAGEWTY_FILE_HDR_SIZE];
    f.seek(SeekFrom::Start(IMAGEWTY_FILE_HDR_START))
        .map_err(|e| e.to_string())?;

    for _ in 0..num_files {
        f.read_exact(&mut rec).map_err(|e| e.to_string())?;
        // V1/V3 file header: filename_len(u32@0), total_hdr_size(u32@4), maintype(8@8), subtype(16@16),
        // or in V1: filename[256] at 0x24, stored_length at 0x124, original_length at 0x128, offset at 0x12C (version >= 0x100234 uses 0x00/0x28 layout).
        let (name, stored_len, offset) = if version >= 0x0100_2340 {
            let fname = cstr_ascii(&rec[0x28..0x128]);
            let orig_len = le_u32(&rec, 0x128) as u64;
            let stored = le_u32(&rec, 0x12C) as u64;
            let off = le_u32(&rec, 0x130) as u64;
            (fname, if stored > 0 { stored } else { orig_len }, off)
        } else {
            let fname = cstr_ascii(&rec[0x00..0x100]);
            let stored = le_u32(&rec, 0x100) as u64;
            let off = le_u32(&rec, 0x108) as u64;
            (fname, stored, off)
        };
        let clean = name.trim_start_matches(['\\', '/']).to_string();
        if !clean.is_empty() && stored_len > 0 && offset + stored_len <= file_len {
            entries.push((clean.clone(), clean, offset, stored_len));
        }
    }

    Ok(AllwinnerParsed { version, entries })
}

pub fn inspect_allwinner(path: &Path) -> Result<OemInspectResult, String> {
    let parsed = parse_allwinner(path)?;
    let mut metadata = HashMap::new();
    metadata.insert("header_version".to_string(), format!("0x{:x}", parsed.version));
    let entries = parsed
        .entries
        .into_iter()
        .map(|(name, _raw, offset, size)| OemEntry {
            name,
            size,
            offset,
            kind: "file".to_string(),
            extra: HashMap::new(),
        })
        .collect();
    Ok(OemInspectResult {
        kind: "ALLWINNER".to_string(),
        metadata,
        entries,
    })
}

pub fn extract_allwinner(
    path: &Path,
    out_dir: &Path,
    selected: Option<&HashSet<String>>,
) -> Result<Vec<String>, String> {
    let parsed = parse_allwinner(path)?;
    fs::create_dir_all(out_dir).map_err(|e| e.to_string())?;
    let mut src = File::open(path).map_err(|e| e.to_string())?;
    let mut extracted = Vec::new();
    for (name, _raw, offset, size) in parsed.entries {
        if let Some(sel) = selected {
            if !sel.contains(&name) {
                continue;
            }
        }
        let dest = safe_join_relative(out_dir, &name)?;
        if let Some(p) = dest.parent() {
            fs::create_dir_all(p).map_err(|e| e.to_string())?;
        }
        src.seek(SeekFrom::Start(offset)).map_err(|e| e.to_string())?;
        let mut dst = File::create(&dest).map_err(|e| e.to_string())?;
        copy_exact_stream(&mut src, &mut dst, size)?;
        extracted.push(name);
    }
    Ok(extracted)
}
