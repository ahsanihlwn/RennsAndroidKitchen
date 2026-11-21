// SPDX-License-Identifier: MPL-2.0
//! Clean-room embedded filesystem readers and extractors for RomFS and SquashFS.

use super::{safe_join_relative, OemEntry, OemInspectResult};
use backhand::{FilesystemReader, InnerNode};
use std::collections::{HashMap, HashSet};
use std::fs::{self, File};
use std::io::{BufReader, Read, Seek, SeekFrom, Write};
use std::path::Path;

// ============================================================================
// 1. Linux RomFS (-rom1fs-)
// ============================================================================

const ROMFS_MAGIC: &[u8; 8] = b"-rom1fs-";

#[inline]
fn be_u32(b: &[u8]) -> u32 {
    u32::from_be_bytes([b[0], b[1], b[2], b[3]])
}

fn align16(v: u64) -> u64 {
    (v + 15) & !15
}

fn read_padded_cstring(f: &mut File, start: u64) -> Result<(String, u64), String> {
    f.seek(SeekFrom::Start(start)).map_err(|e| e.to_string())?;
    let mut raw = Vec::new();
    let mut chunk = [0u8; 16];
    let mut cur = start;
    loop {
        if f.read_exact(&mut chunk).is_err() {
            return Err("Unexpected EOF reading RomFS name".to_string());
        }
        cur += 16;
        if let Some(pos) = chunk.iter().position(|&b| b == 0) {
            raw.extend_from_slice(&chunk[..pos]);
            break;
        }
        raw.extend_from_slice(&chunk);
        if raw.len() > 4096 {
            return Err("RomFS filename exceeds maximum length".to_string());
        }
    }
    Ok((String::from_utf8_lossy(&raw).into_owned(), cur))
}

#[derive(Clone)]
struct RomfsEntry {
    rel_path: String,
    kind: u8, // 0=hardlink, 1=dir, 2=file, 3=symlink
    spec_info: u64,
    size: u64,
    data_offset: u64,
}

struct RomfsParsed {
    volume_name: String,
    image_size: u64,
    entries: Vec<RomfsEntry>,
}

fn parse_romfs(path: &Path) -> Result<RomfsParsed, String> {
    let mut f = File::open(path).map_err(|e| e.to_string())?;
    let file_len = f.metadata().map_err(|e| e.to_string())?.len();
    let mut hdr = [0u8; 16];
    f.read_exact(&mut hdr).map_err(|e| e.to_string())?;
    if &hdr[0..8] != ROMFS_MAGIC {
        return Err("Invalid RomFS magic (expected -rom1fs-)".to_string());
    }
    let image_size = (be_u32(&hdr[8..12]) as u64).min(file_len);
    let (volume_name, root_start) = read_padded_cstring(&mut f, 16)?;

    let mut entries = Vec::new();
    let mut visited = HashSet::new();
    let mut stack: Vec<(u64, String)> = vec![(root_start, String::new())];

    while let Some((mut offset, prefix)) = stack.pop() {
        while offset != 0 && offset + 16 <= file_len {
            if !visited.insert(offset) {
                break;
            }
            f.seek(SeekFrom::Start(offset)).map_err(|e| e.to_string())?;
            let mut ehdr = [0u8; 16];
            f.read_exact(&mut ehdr).map_err(|e| e.to_string())?;
            let next_word = be_u32(&ehdr[0..4]);
            let spec_info = be_u32(&ehdr[4..8]) as u64;
            let size = be_u32(&ehdr[8..12]) as u64;
            let ftype = (next_word & 0x7) as u8;
            let next_off = (next_word & !0xF) as u64;

            let (name, data_offset) = read_padded_cstring(&mut f, offset + 16)?;
            if name != "." && name != ".." && !name.is_empty() {
                let rel_path = if prefix.is_empty() {
                    name.clone()
                } else {
                    format!("{prefix}/{name}")
                };
                if data_offset + size <= file_len {
                    entries.push(RomfsEntry {
                        rel_path: rel_path.clone(),
                        kind: ftype,
                        spec_info,
                        size,
                        data_offset,
                    });
                }
                if ftype == 1 && spec_info != 0 && spec_info < file_len {
                    stack.push((spec_info, rel_path));
                }
            }
            offset = next_off;
        }
    }

    entries.sort_by(|a, b| a.rel_path.cmp(&b.rel_path));
    Ok(RomfsParsed {
        volume_name,
        image_size,
        entries,
    })
}

pub fn inspect_romfs(path: &Path) -> Result<OemInspectResult, String> {
    let parsed = parse_romfs(path)?;
    let mut metadata = HashMap::new();
    metadata.insert("volume_name".to_string(), parsed.volume_name);
    metadata.insert("image_size".to_string(), parsed.image_size.to_string());

    let entries = parsed
        .entries
        .into_iter()
        .map(|e| OemEntry {
            name: e.rel_path,
            size: e.size,
            offset: e.data_offset,
            kind: match e.kind {
                1 => "dir",
                2 => "file",
                3 => "symlink",
                0 => "hardlink",
                _ => "other",
            }
            .to_string(),
            extra: HashMap::new(),
        })
        .collect();
    Ok(OemInspectResult {
        kind: "ROMFS".to_string(),
        metadata,
        entries,
    })
}

pub fn extract_romfs(
    path: &Path,
    out_dir: &Path,
    selected: Option<&HashSet<String>>,
) -> Result<Vec<String>, String> {
    let parsed = parse_romfs(path)?;
    fs::create_dir_all(out_dir).map_err(|e| e.to_string())?;
    let mut src = File::open(path).map_err(|e| e.to_string())?;
    let mut extracted = Vec::new();
    let mut buf = vec![0u8; 64 * 1024];

    for entry in &parsed.entries {
        if let Some(sel) = selected {
            if !sel.contains(&entry.rel_path) {
                continue;
            }
        }
        let dest = safe_join_relative(out_dir, &entry.rel_path)?;
        match entry.kind {
            1 => {
                fs::create_dir_all(&dest).map_err(|e| e.to_string())?;
            }
            2 => {
                if let Some(p) = dest.parent() {
                    fs::create_dir_all(p).map_err(|e| e.to_string())?;
                }
                src.seek(SeekFrom::Start(entry.data_offset))
                    .map_err(|e| e.to_string())?;
                let mut dst = File::create(&dest).map_err(|e| e.to_string())?;
                let mut rem = entry.size;
                while rem > 0 {
                    let want = (rem as usize).min(buf.len());
                    src.read_exact(&mut buf[..want]).map_err(|e| e.to_string())?;
                    dst.write_all(&buf[..want]).map_err(|e| e.to_string())?;
                    rem -= want as u64;
                }
                extracted.push(entry.rel_path.clone());
            }
            3 => {
                // Record symlink target inside a safe marker or create symlink when valid.
                if let Some(p) = dest.parent() {
                    fs::create_dir_all(p).map_err(|e| e.to_string())?;
                }
                src.seek(SeekFrom::Start(entry.data_offset))
                    .map_err(|e| e.to_string())?;
                let mut target_bytes = vec![0u8; (entry.size as usize).min(4096)];
                src.read_exact(&mut target_bytes).map_err(|e| e.to_string())?;
                let target = String::from_utf8_lossy(&target_bytes).into_owned();
                #[cfg(unix)]
                {
                    let _ = std::os::unix::fs::symlink(&target, &dest);
                }
                #[cfg(not(unix))]
                {
                    let _ = fs::write(&dest, format!("!<symlink>{target}\0"));
                }
                extracted.push(entry.rel_path.clone());
            }
            _ => {
                let _ = align16(entry.spec_info);
            }
        }
    }
    Ok(extracted)
}

// ============================================================================
// 2. SquashFS (via backhand, MIT/Apache-2.0)
// ============================================================================

pub fn inspect_squashfs(path: &Path) -> Result<OemInspectResult, String> {
    let file = BufReader::new(File::open(path).map_err(|e| e.to_string())?);
    let reader = FilesystemReader::from_reader(file)
        .map_err(|e| format!("Failed to read SquashFS image: {e}"))?;

    let mut metadata = HashMap::new();
    metadata.insert("block_size".to_string(), reader.block_size.to_string());
    metadata.insert("compressor".to_string(), format!("{:?}", reader.compressor));
    metadata.insert("block_log".to_string(), reader.block_log.to_string());

    let mut entries = Vec::new();
    for node in reader.files() {
        let rel = node.fullpath.to_string_lossy().trim_start_matches('/').to_string();
        if rel.is_empty() {
            continue;
        }
        let (kind, size) = match &node.inner {
            InnerNode::File(f) => ("file".to_string(), f.file_len() as u64),
            InnerNode::Dir(_) => ("dir".to_string(), 0),
            InnerNode::Symlink(_) => ("symlink".to_string(), 0),
            _ => ("other".to_string(), 0),
        };
        entries.push(OemEntry {
            name: rel,
            size,
            offset: 0,
            kind,
            extra: HashMap::new(),
        });
    }
    entries.sort_by(|a, b| a.name.cmp(&b.name));
    Ok(OemInspectResult {
        kind: "SQUASHFS".to_string(),
        metadata,
        entries,
    })
}

pub fn extract_squashfs(
    path: &Path,
    out_dir: &Path,
    selected: Option<&HashSet<String>>,
) -> Result<Vec<String>, String> {
    let file = BufReader::new(File::open(path).map_err(|e| e.to_string())?);
    let reader = FilesystemReader::from_reader(file)
        .map_err(|e| format!("Failed to read SquashFS image: {e}"))?;
    fs::create_dir_all(out_dir).map_err(|e| e.to_string())?;

    let mut extracted = Vec::new();
    for node in reader.files() {
        let rel = node.fullpath.to_string_lossy().trim_start_matches('/').to_string();
        if rel.is_empty() {
            continue;
        }
        if let Some(sel) = selected {
            if !sel.contains(&rel) {
                continue;
            }
        }
        let dest = safe_join_relative(out_dir, &rel)?;
        match &node.inner {
            InnerNode::Dir(_) => {
                fs::create_dir_all(&dest).map_err(|e| e.to_string())?;
            }
            InnerNode::File(file_node) => {
                if let Some(p) = dest.parent() {
                    fs::create_dir_all(p).map_err(|e| e.to_string())?;
                }
                let mut rdr = reader.file(file_node).reader();
                let mut dst = File::create(&dest).map_err(|e| e.to_string())?;
                std::io::copy(&mut rdr, &mut dst).map_err(|e| e.to_string())?;
                extracted.push(rel);
            }
            InnerNode::Symlink(sym) => {
                if let Some(p) = dest.parent() {
                    fs::create_dir_all(p).map_err(|e| e.to_string())?;
                }
                let target = sym.link.to_string_lossy();
                #[cfg(unix)]
                {
                    let _ = std::os::unix::fs::symlink(target.as_ref(), &dest);
                }
                #[cfg(not(unix))]
                {
                    let _ = fs::write(&dest, format!("!<symlink>{target}\0"));
                }
                extracted.push(rel);
            }
            _ => {}
        }
    }
    Ok(extracted)
}
