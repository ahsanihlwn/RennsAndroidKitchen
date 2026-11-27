// SPDX-License-Identifier: MPL-2.0
//! Zero-unpack container readers for super.img (LP extents) and firmware .zip archives.

use crate::image_tools::{NativeErofsReader, NativeExtReader};
use lp_tools::lp::format::{LP_SECTOR_SIZE, LP_TARGET_TYPE_LINEAR, LP_TARGET_TYPE_ZERO};
use lp_tools::lp::reader::read_metadata_auto;
use lp_tools::lp::unpack::select_partitions_for_extract;
use lp_tools::sparse::SparseReader;
use pyo3::exceptions::{PyOSError, PyValueError};
use pyo3::prelude::*;
use pyo3::types::PyBytes;
use rayon::prelude::*;
use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::fs::{self, File};
use std::io::{self, BufReader, Read, Seek, SeekFrom, Write};
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};
use std::time::UNIX_EPOCH;
use zip::ZipArchive;

#[derive(Clone, Debug)]
pub(crate) struct ExtentSpan {
    pub logical_start: u64,
    pub logical_end: u64,
    pub physical_offset: Option<u64>,
}

enum SuperHandle {
    Raw(File),
    Sparse(SparseReader<File>),
}

impl SuperHandle {
    fn open(path: &Path) -> io::Result<Self> {
        let mut f = File::open(path)?;
        if SparseReader::is_sparse(&mut f)? {
            let sr = SparseReader::open(f, false)
                .map_err(|e| io::Error::new(io::ErrorKind::InvalidData, e.to_string()))?;
            Ok(Self::Sparse(sr))
        } else {
            Ok(Self::Raw(f))
        }
    }

    fn read_exact_at(&mut self, offset: u64, buf: &mut [u8]) -> io::Result<usize> {
        match self {
            Self::Raw(f) => {
                f.seek(SeekFrom::Start(offset))?;
                let mut done = 0usize;
                while done < buf.len() {
                    let n = f.read(&mut buf[done..])?;
                    if n == 0 {
                        break;
                    }
                    done += n;
                }
                Ok(done)
            }
            Self::Sparse(sr) => {
                sr.seek(SeekFrom::Start(offset))?;
                let mut done = 0usize;
                while done < buf.len() {
                    let n = sr.read(&mut buf[done..])?;
                    if n == 0 {
                        break;
                    }
                    done += n;
                }
                Ok(done)
            }
        }
    }
}

/// Thread-safe backing for a logical partition inside super.img (raw or sparse).
pub(crate) struct SuperPartitionBacking {
    path: PathBuf,
    pool: Mutex<Vec<SuperHandle>>,
    extents: Vec<ExtentSpan>,
    total_len: u64,
    is_sparse: bool,
}

impl SuperPartitionBacking {
    pub fn open(super_path: &Path, partition_name: &str, slot: u32) -> Result<Self, String> {
        let mut f = File::open(super_path).map_err(|e| e.to_string())?;
        let is_sparse = SparseReader::is_sparse(&mut f).map_err(|e| e.to_string())?;
        let mut handle = SuperHandle::open(super_path).map_err(|e| e.to_string())?;

        let meta = match &mut handle {
            SuperHandle::Raw(file) => read_metadata_auto(file, slot),
            SuperHandle::Sparse(sr) => read_metadata_auto(sr, slot),
        }
        .map(|(m, _)| m)
        .map_err(|e| e.to_string())?;

        let target = partition_name.trim().to_ascii_lowercase();
        let selected = select_partitions_for_extract(&meta, true, slot);
        let found = selected
            .into_iter()
            .find(|(_p, name)| name.to_ascii_lowercase() == target)
            .or_else(|| {
                meta.partitions
                    .iter()
                    .find(|p| p.name().to_ascii_lowercase() == target)
                    .map(|p| (*p, p.name()))
            })
            .ok_or_else(|| format!("Logical partition '{partition_name}' not found in super.img"))?;

        let part = found.0;
        let first = part.first_extent_index as usize;
        let count = part.num_extents as usize;
        let ext_slice = meta
            .extents
            .get(first..first.saturating_add(count))
            .ok_or_else(|| format!("Invalid extent range for partition '{partition_name}'"))?;

        let mut extents = Vec::with_capacity(ext_slice.len());
        let mut pos = 0u64;
        for ext in ext_slice {
            let len = ext.num_sectors * LP_SECTOR_SIZE;
            if len == 0 {
                continue;
            }
            let phys = match ext.target_type {
                LP_TARGET_TYPE_LINEAR => Some(ext.target_data * LP_SECTOR_SIZE),
                LP_TARGET_TYPE_ZERO => None,
                other => {
                    return Err(format!(
                        "Unsupported LP extent target_type {other} for '{partition_name}'"
                    ))
                }
            };
            extents.push(ExtentSpan {
                logical_start: pos,
                logical_end: pos + len,
                physical_offset: phys,
            });
            pos += len;
        }

        Ok(Self {
            path: super_path.to_path_buf(),
            pool: Mutex::new(vec![handle]),
            extents,
            total_len: pos,
            is_sparse,
        })
    }

    pub fn read_at_io(&self, pos: u64, buf: &mut [u8]) -> io::Result<usize> {
        if pos >= self.total_len || buf.is_empty() {
            return Ok(0);
        }
        let want = ((self.total_len - pos) as usize).min(buf.len());
        let mut cursor = pos;
        let mut dst = 0usize;

        let mut handle = {
            let mut guard = self.pool.lock().unwrap();
            guard.pop()
        };
        let mut owned = match handle.take() {
            Some(h) => h,
            None => SuperHandle::open(&self.path)?,
        };

        let res = (|| -> io::Result<usize> {
            while dst < want {
                let idx = self.extents.partition_point(|e| e.logical_end <= cursor);
                if idx >= self.extents.len() {
                    break;
                }
                let ext = &self.extents[idx];
                if cursor < ext.logical_start {
                    let gap = ((ext.logical_start - cursor) as usize).min(want - dst);
                    buf[dst..dst + gap].fill(0);
                    cursor += gap as u64;
                    dst += gap;
                    continue;
                }
                let take = ((ext.logical_end - cursor) as usize).min(want - dst);
                match ext.physical_offset {
                    None => buf[dst..dst + take].fill(0),
                    Some(phys_base) => {
                        let off = phys_base + (cursor - ext.logical_start);
                        let n = owned.read_exact_at(off, &mut buf[dst..dst + take])?;
                        if n < take {
                            buf[dst + n..dst + take].fill(0);
                        }
                    }
                }
                cursor += take as u64;
                dst += take;
            }
            Ok(dst)
        })();

        self.pool.lock().unwrap().push(owned);
        res
    }
}

impl erofs_rs::io::RandomAccessBacking for SuperPartitionBacking {
    fn len(&self) -> u64 {
        self.total_len
    }

    fn is_sparse(&self) -> bool {
        self.is_sparse
    }

    fn read_at(&self, pos: u64, buf: &mut [u8]) -> erofs_rs::error::Result<usize> {
        self.read_at_io(pos, buf)
            .map_err(erofs_rs::error::ErofsError::Io)
    }
}

pub(crate) struct SuperReadSeek {
    backing: Arc<SuperPartitionBacking>,
    pos: u64,
}

impl SuperReadSeek {
    pub fn new(backing: Arc<SuperPartitionBacking>) -> Self {
        Self { backing, pos: 0 }
    }
}

impl Read for SuperReadSeek {
    fn read(&mut self, buf: &mut [u8]) -> io::Result<usize> {
        let n = self.backing.read_at_io(self.pos, buf)?;
        self.pos += n as u64;
        Ok(n)
    }
}

impl Seek for SuperReadSeek {
    fn seek(&mut self, style: SeekFrom) -> io::Result<u64> {
        let next: i64 = match style {
            SeekFrom::Start(off) => off as i64,
            SeekFrom::Current(delta) => self.pos as i64 + delta,
            SeekFrom::End(delta) => self.backing.total_len as i64 + delta,
        };
        if next < 0 {
            return Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                "negative seek position",
            ));
        }
        self.pos = next as u64;
        Ok(self.pos)
    }
}

fn detect_fs_head(head: &[u8]) -> &'static str {
    for off in [0usize, 512, 1024, 4096] {
        if let Some(chunk) = head.get(off..off + 4) {
            if u32::from_le_bytes([chunk[0], chunk[1], chunk[2], chunk[3]]) == 0xE0F5_E1E2 {
                return "EROFS";
            }
        }
    }
    for sb in [1024usize, 0] {
        if let Some(chunk) = head.get(sb + 0x38..sb + 0x3A) {
            if u16::from_le_bytes([chunk[0], chunk[1]]) == 0xEF53 {
                return "EXT";
            }
        }
    }
    ""
}

#[derive(Serialize)]
pub struct SuperPartitionEntry {
    pub name: String,
    pub raw_name: String,
    pub size: u64,
    pub fs_type: String,
    pub extents: u32,
}

#[derive(Serialize)]
pub struct SuperProbeReport {
    pub slot: u32,
    pub is_sparse: bool,
    pub is_empty: bool,
    pub groups: Vec<String>,
    pub partitions: Vec<SuperPartitionEntry>,
}

#[pyfunction]
#[pyo3(signature = (super_path, slot=0))]
pub fn vdev_probe_super_json(py: Python<'_>, super_path: PathBuf, slot: u32) -> PyResult<String> {
    let report = py
        .detach(move || -> Result<SuperProbeReport, String> {
            let mut f = File::open(&super_path).map_err(|e| e.to_string())?;
            let is_sparse = SparseReader::is_sparse(&mut f).map_err(|e| e.to_string())?;
            let (meta, is_empty) = if is_sparse {
                let mut sr = SparseReader::open(f, false).map_err(|e| e.to_string())?;
                read_metadata_auto(&mut sr, slot).map_err(|e| e.to_string())?
            } else {
                read_metadata_auto(&mut f, slot).map_err(|e| e.to_string())?
            };

            let groups: Vec<String> = meta
                .groups
                .iter()
                .map(|g| g.name())
                .filter(|n| !n.is_empty())
                .collect();

            let selected = select_partitions_for_extract(&meta, true, slot);
            let mut partitions = Vec::with_capacity(selected.len());
            for (part, base_name) in selected {
                let size = meta.partition_size(&part);
                if size == 0 || part.num_extents == 0 {
                    continue;
                }
                let fs_type = match SuperPartitionBacking::open(&super_path, &base_name, slot) {
                    Ok(backing) => {
                        let mut head = vec![0u8; 8192];
                        let n = backing.read_at_io(0, &mut head).unwrap_or(0);
                        detect_fs_head(&head[..n]).to_string()
                    }
                    Err(_) => String::new(),
                };
                partitions.push(SuperPartitionEntry {
                    name: base_name,
                    raw_name: part.name(),
                    size,
                    fs_type,
                    extents: part.num_extents,
                });
            }

            Ok(SuperProbeReport {
                slot,
                is_sparse,
                is_empty,
                groups,
                partitions,
            })
        })
        .map_err(PyOSError::new_err)?;
    serde_json::to_string(&report).map_err(|e| PyOSError::new_err(e.to_string()))
}

#[pyfunction]
#[pyo3(signature = (super_path, partition, slot=0))]
pub fn vdev_super_open_erofs(
    py: Python<'_>,
    super_path: PathBuf,
    partition: String,
    slot: u32,
) -> PyResult<NativeErofsReader> {
    let img = py
        .detach(move || -> Result<erofs_rs::reader::ErofsImage, String> {
            let backing = Arc::new(SuperPartitionBacking::open(&super_path, &partition, slot)?);
            erofs_rs::reader::ErofsImage::open_custom(backing).map_err(|e| e.to_string())
        })
        .map_err(PyOSError::new_err)?;
    Ok(NativeErofsReader::from_erofs(img))
}

#[pyfunction]
#[pyo3(signature = (super_path, partition, slot=0))]
pub fn vdev_super_open_ext(
    py: Python<'_>,
    super_path: PathBuf,
    partition: String,
    slot: u32,
) -> PyResult<NativeExtReader> {
    let vol = py
        .detach(move || -> Result<ext_cli::extfs::Volume, String> {
            let backing = Arc::new(SuperPartitionBacking::open(&super_path, &partition, slot)?);
            ext_cli::extfs::Volume::open_custom(Box::new(SuperReadSeek::new(backing)))
                .map_err(|e| e.to_string())
        })
        .map_err(PyOSError::new_err)?;
    Ok(NativeExtReader::from_volume(vol))
}

#[derive(Serialize)]
pub struct ZipEntryInfo {
    pub name: String,
    pub basename: String,
    pub size: u64,
    pub compressed_size: u64,
    pub compression: String,
    pub crc32: u32,
    pub data_start: Option<u64>,
    pub role: String,
}

fn classify_zip_role(name: &str) -> String {
    let low = Path::new(name)
        .file_name()
        .and_then(|s| s.to_str())
        .unwrap_or("")
        .to_ascii_lowercase();
    if low.ends_with(".img") || low.ends_with(".img.zst") {
        let stem = low
            .strip_suffix(".img.zst")
            .or_else(|| low.strip_suffix(".img"))
            .unwrap_or(&low);
        let base = stem
            .strip_suffix("_a")
            .or_else(|| stem.strip_suffix("_b"))
            .unwrap_or(stem);
        if base == "super" {
            return "super".into();
        }
        if matches!(base, "boot" | "vendor_boot" | "init_boot" | "recovery") {
            return "boot".into();
        }
        if base.starts_with("vbmeta") {
            return "vbmeta".into();
        }
        if base == "dtbo" {
            return "dtbo".into();
        }
        if matches!(
            base,
            "system"
                | "system_ext"
                | "product"
                | "vendor"
                | "odm"
                | "oem"
                | "cust"
                | "vendor_dlkm"
                | "odm_dlkm"
                | "system_dlkm"
                | "mi_ext"
                | "preload"
                | "prism"
                | "optics"
        ) || base.starts_with("tr_")
            || base.starts_with("my_")
            || base.starts_with("oppo_")
        {
            return "partition".into();
        }
        return "firmware_img".into();
    }
    if low.ends_with(".new.dat.br") || low.ends_with(".new.dat") || low.ends_with(".transfer.list") {
        return "sdat".into();
    }
    if low == "payload.bin" {
        return "payload".into();
    }
    "other".into()
}

#[pyfunction]
pub fn vdev_probe_zip_json(py: Python<'_>, zip_path: PathBuf) -> PyResult<String> {
    let entries = py
        .detach(move || -> Result<Vec<ZipEntryInfo>, String> {
            let file = File::open(&zip_path).map_err(|e| e.to_string())?;
            let mut archive = ZipArchive::new(BufReader::new(file)).map_err(|e| e.to_string())?;
            let mut out = Vec::with_capacity(archive.len());
            for i in 0..archive.len() {
                let entry = archive.by_index_raw(i).map_err(|e| e.to_string())?;
                if entry.is_dir() {
                    continue;
                }
                let name = entry.name().to_string();
                let basename = Path::new(&name)
                    .file_name()
                    .and_then(|s| s.to_str())
                    .unwrap_or("")
                    .to_string();
                let comp = format!("{:?}", entry.compression());
                let data_start = if entry.compression() == zip::CompressionMethod::Stored {
                    Some(entry.data_start())
                } else {
                    None
                };
                out.push(ZipEntryInfo {
                    role: classify_zip_role(&name),
                    name,
                    basename,
                    size: entry.size(),
                    compressed_size: entry.compressed_size(),
                    compression: comp,
                    crc32: entry.crc32(),
                    data_start,
                });
            }
            Ok(out)
        })
        .map_err(PyOSError::new_err)?;
    serde_json::to_string(&entries).map_err(|e| PyOSError::new_err(e.to_string()))
}

pub(crate) fn read_zip_entry_vec(zip_path: &Path, entry_name: &str) -> Result<Vec<u8>, String> {
    let file = File::open(zip_path).map_err(|e| e.to_string())?;
    let mut archive = ZipArchive::new(BufReader::new(file)).map_err(|e| e.to_string())?;
    let mut entry = archive.by_name(entry_name).map_err(|e| e.to_string())?;
    let mut buf = Vec::with_capacity(entry.size().min(256 * 1024 * 1024) as usize);
    entry.read_to_end(&mut buf).map_err(|e| e.to_string())?;
    Ok(buf)
}

#[pyfunction]
pub fn vdev_read_zip_entry_bytes<'py>(
    py: Python<'py>,
    zip_path: PathBuf,
    entry_name: String,
) -> PyResult<Bound<'py, PyBytes>> {
    let bytes = py
        .detach(move || read_zip_entry_vec(&zip_path, &entry_name))
        .map_err(PyOSError::new_err)?;
    Ok(PyBytes::new(py, &bytes))
}

#[derive(Serialize, Deserialize)]
struct ZipStamp {
    zip_len: u64,
    zip_mtime_ns: u128,
    entries: HashMap<String, (u32, u64)>,
}

#[derive(Serialize)]
pub struct PreparedZipEntry {
    pub entry_name: String,
    pub host_path: String,
    pub size: u64,
    pub fs_type: String,
    pub cache_hit: bool,
}

#[pyfunction]
pub fn vdev_prepare_zip_images_json(
    py: Python<'_>,
    zip_path: PathBuf,
    entries: Vec<String>,
    cache_dir: PathBuf,
) -> PyResult<String> {
    if entries.is_empty() {
        return Ok("[]".to_string());
    }
    let result = py
        .detach(move || -> Result<Vec<PreparedZipEntry>, String> {
            fs::create_dir_all(&cache_dir).map_err(|e| e.to_string())?;
            let meta = fs::metadata(&zip_path).map_err(|e| e.to_string())?;
            let zip_len = meta.len();
            let zip_mtime_ns = meta
                .modified()
                .ok()
                .and_then(|t| t.duration_since(UNIX_EPOCH).ok())
                .map(|d| d.as_nanos())
                .unwrap_or(0);

            let stamp_path = cache_dir.join(".vdev_zip_stamp.json");
            let prev_stamp: Option<ZipStamp> = fs::read_to_string(&stamp_path)
                .ok()
                .and_then(|s| serde_json::from_str(&s).ok());
            let stamp_valid = prev_stamp
                .as_ref()
                .is_some_and(|s| s.zip_len == zip_len && s.zip_mtime_ns == zip_mtime_ns);

            let f = File::open(&zip_path).map_err(|e| e.to_string())?;
            let mut archive = ZipArchive::new(BufReader::new(f)).map_err(|e| e.to_string())?;
            let mut wanted = Vec::with_capacity(entries.len());
            for name in &entries {
                let entry = archive.by_name(name).map_err(|e| e.to_string())?;
                let base = Path::new(name)
                    .file_name()
                    .and_then(|s| s.to_str())
                    .ok_or_else(|| format!("Invalid zip entry name: {name}"))?;
                let is_zst = base.to_ascii_lowercase().ends_with(".zst");
                let out_name = if is_zst {
                    &base[..base.len() - 4]
                } else {
                    base
                };
                let out_path = cache_dir.join(out_name);
                let cached = stamp_valid
                    && prev_stamp
                        .as_ref()
                        .and_then(|s| s.entries.get(name))
                        .is_some_and(|&(crc, sz)| {
                            crc == entry.crc32()
                                && sz == entry.size()
                                && out_path.metadata().is_ok_and(|m| {
                                    if is_zst {
                                        m.len() > 0
                                    } else {
                                        m.len() == sz
                                    }
                                })
                        });
                wanted.push((name.clone(), out_path, entry.crc32(), entry.size(), cached, is_zst));
            }
            drop(archive);

            let extracted: Vec<Result<PreparedZipEntry, String>> = wanted
                .par_iter()
                .map(|(name, out_path, _crc, size, cached, is_zst)| {
                    if !*cached {
                        let f = File::open(&zip_path).map_err(|e| e.to_string())?;
                        let mut arc =
                            ZipArchive::new(BufReader::new(f)).map_err(|e| e.to_string())?;
                        let mut ent = arc.by_name(name).map_err(|e| e.to_string())?;
                        let tmp = out_path.with_extension("part");
                        {
                            let out_f = File::create(&tmp).map_err(|e| e.to_string())?;
                            let mut writer = io::BufWriter::with_capacity(1024 * 1024, out_f);
                            if *is_zst {
                                let mut dec = zstd::stream::read::Decoder::new(&mut ent)
                                    .map_err(|e| e.to_string())?;
                                io::copy(&mut dec, &mut writer).map_err(|e| e.to_string())?;
                            } else {
                                io::copy(&mut ent, &mut writer).map_err(|e| e.to_string())?;
                            }
                            writer.flush().map_err(|e| e.to_string())?;
                        }
                        if out_path.exists() {
                            let _ = fs::remove_file(out_path);
                        }
                        fs::rename(&tmp, out_path).map_err(|e| e.to_string())?;
                    }
                    let mut head = [0u8; 8192];
                    let n = File::open(out_path)
                        .and_then(|mut f| f.read(&mut head))
                        .unwrap_or(0);
                    let fs_type = detect_fs_head(&head[..n]).to_string();
                    let actual_size = out_path.metadata().map(|m| m.len()).unwrap_or(*size);
                    Ok(PreparedZipEntry {
                        entry_name: name.clone(),
                        host_path: out_path.to_string_lossy().into_owned(),
                        size: actual_size,
                        fs_type,
                        cache_hit: *cached,
                    })
                })
                .collect();

            let mut out = Vec::with_capacity(extracted.len());
            let mut new_entries = if stamp_valid {
                prev_stamp.map(|s| s.entries).unwrap_or_default()
            } else {
                HashMap::new()
            };
            for (item, (name, _p, crc, sz, _c, _z)) in extracted.into_iter().zip(wanted.into_iter()) {
                let entry_res = item?;
                new_entries.insert(name, (crc, sz));
                out.push(entry_res);
            }
            let stamp = ZipStamp {
                zip_len,
                zip_mtime_ns,
                entries: new_entries,
            };
            if let Ok(text) = serde_json::to_string(&stamp) {
                let _ = fs::write(&stamp_path, text);
            }
            Ok(out)
        })
        .map_err(|e| {
            if e.contains("not found") || e.contains("Invalid") {
                PyValueError::new_err(e)
            } else {
                PyOSError::new_err(e)
            }
        })?;
    serde_json::to_string(&result).map_err(|e| PyOSError::new_err(e.to_string()))
}
