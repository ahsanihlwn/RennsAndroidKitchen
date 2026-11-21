//! Library functions for inspecting, unpacking, and unsparsing super images.

use std::collections::{HashMap, HashSet};
use std::fs::{self, File, OpenOptions};
use std::io::{self, Read, Seek, SeekFrom, Write};
use std::path::{Path, PathBuf};
use std::process;

use crate::sparse::{ImageSource, SparseReader};

use super::dump::format_metadata_text;
use super::format::{
    LpError, LpMetadata, LpMetadataExtent, LpMetadataPartition, LP_SECTOR_SIZE,
    LP_TARGET_TYPE_LINEAR, LP_TARGET_TYPE_ZERO,
};
use super::reader::read_metadata_auto;

#[derive(Debug, Clone)]
pub struct SuperImageInfo {
    pub raw_text: String,
    pub partitions: Vec<String>,
    pub groups: Vec<String>,
    pub active_slot: Option<String>,
    pub is_empty: bool,
}

pub fn strip_slot_suffix(name: &str) -> (&str, Option<u32>) {
    if let Some(base) = name.strip_suffix("_a") {
        if !base.is_empty() {
            return (base, Some(0));
        }
    } else if let Some(base) = name.strip_suffix("_b") {
        if !base.is_empty() {
            return (base, Some(1));
        }
    }
    (name, None)
}

pub fn select_partitions_for_extract(
    metadata: &LpMetadata,
    normalize: bool,
    preferred_slot: u32,
) -> Vec<(LpMetadataPartition, String)> {
    if !normalize {
        return metadata
            .partitions
            .iter()
            .map(|p| (*p, p.name()))
            .collect();
    }

    let mut chosen: Vec<(LpMetadataPartition, String, Option<u32>)> = Vec::new();
    let mut index_by_base: HashMap<String, usize> = HashMap::new();

    for part in &metadata.partitions {
        if part.num_extents == 0 || metadata.partition_size(part) == 0 {
            continue;
        }
        let raw_name = part.name();
        let (base, slot_id) = strip_slot_suffix(&raw_name);
        let base_str = base.to_string();
        if let Some(&idx) = index_by_base.get(&base_str) {
            let existing_slot = chosen[idx].2;
            let replace = slot_id == Some(preferred_slot) && existing_slot != Some(preferred_slot);
            if replace {
                chosen[idx] = (*part, base_str, slot_id);
            }
        } else {
            index_by_base.insert(base_str.clone(), chosen.len());
            chosen.push((*part, base_str, slot_id));
        }
    }

    chosen
        .into_iter()
        .map(|(part, out_name, _)| (part, out_name))
        .collect()
}

pub struct SparseHoleWriter {
    output: File,
    block_size: usize,
    hole_size: u64,
    written_pos: u64,
}

impl SparseHoleWriter {
    pub fn new(output: File, block_size: u32) -> Self {
        Self {
            output,
            block_size: block_size as usize,
            hole_size: 0,
            written_pos: 0,
        }
    }

    pub fn write_extent<R: Read + Seek>(
        &mut self,
        source: &mut R,
        extent: &LpMetadataExtent,
    ) -> Result<(), LpError> {
        let total_bytes = extent.num_sectors * LP_SECTOR_SIZE;
        if extent.target_type == LP_TARGET_TYPE_ZERO {
            self.hole_size += total_bytes;
            return Ok(());
        }
        if extent.target_type != LP_TARGET_TYPE_LINEAR {
            return Err(LpError::InvalidMetadata(format!(
                "Unsupported target type in extent: {}",
                extent.target_type
            )));
        }

        let super_offset = extent.target_data * LP_SECTOR_SIZE;
        source.seek(SeekFrom::Start(super_offset))?;

        let batch_size = self.block_size * ((1024 * 1024) / self.block_size).max(1);
        let mut buf = vec![0u8; batch_size];
        let mut remaining = total_bytes;
        while remaining > 0 {
            let to_read = (remaining as usize).min(batch_size);
            source.read_exact(&mut buf[..to_read])?;
            self.write_batched(&buf[..to_read])?;
            remaining -= to_read as u64;
        }
        Ok(())
    }

    fn write_batched(&mut self, data: &[u8]) -> io::Result<()> {
        let mut offset = 0;
        let mut run_start: Option<usize> = None;

        while offset < data.len() {
            let end = (offset + self.block_size).min(data.len());
            let block = &data[offset..end];
            if block.iter().all(|&b| b == 0) {
                if let Some(start) = run_start.take() {
                    self.flush_nonzero(&data[start..offset])?;
                }
                self.hole_size += block.len() as u64;
            } else if run_start.is_none() {
                run_start = Some(offset);
            }
            offset = end;
        }

        if let Some(start) = run_start {
            self.flush_nonzero(&data[start..])?;
        }
        Ok(())
    }

    fn flush_nonzero(&mut self, data: &[u8]) -> io::Result<()> {
        if self.hole_size > 0 {
            self.output.seek(SeekFrom::Current(self.hole_size as i64))?;
            self.written_pos += self.hole_size;
            self.hole_size = 0;
        }
        self.output.write_all(data)?;
        self.written_pos += data.len() as u64;
        Ok(())
    }

    pub fn finish(mut self) -> io::Result<()> {
        if self.hole_size > 0 {
            self.output.set_len(self.written_pos + self.hole_size)?;
        }
        self.output.flush()?;
        Ok(())
    }
}

fn open_device_source(
    super_path: &Path,
    metadata: &LpMetadata,
    target_source: u32,
) -> Result<Option<ImageSource<File>>, LpError> {
    if target_source == 0 {
        return Ok(None);
    }
    let dev_name = metadata
        .block_devices
        .get(target_source as usize)
        .map(|d| d.partition_name())
        .ok_or_else(|| {
            LpError::InvalidMetadata(format!(
                "Extent references unknown block device index {target_source}"
            ))
        })?;
    let parent = super_path.parent().unwrap_or_else(|| Path::new("."));
    let sibling = parent.join(format!("super_{dev_name}.img"));
    if sibling.exists() {
        let file = File::open(&sibling)?;
        return Ok(Some(ImageSource::open(file)?));
    }
    Err(LpError::InvalidMetadata(format!(
        "Split super devices are not supported (missing '{}').",
        sibling.display()
    )))
}

pub fn extract_partition_to_dir(
    super_path: &Path,
    primary_source: &mut ImageSource<File>,
    metadata: &LpMetadata,
    partition: &LpMetadataPartition,
    out_name: &str,
    output_dir: &Path,
) -> Result<PathBuf, LpError> {
    let out_path = output_dir.join(format!("{out_name}.img"));
    let out_file = OpenOptions::new()
        .create(true)
        .write(true)
        .truncate(true)
        .open(&out_path)?;

    let mut writer = SparseHoleWriter::new(out_file, metadata.geometry.logical_block_size);

    for i in 0..partition.num_extents {
        let idx = (partition.first_extent_index + i) as usize;
        let extent = &metadata.extents[idx];
        if extent.target_type == LP_TARGET_TYPE_LINEAR && extent.target_source != 0 {
            if let Some(mut alt_src) =
                open_device_source(super_path, metadata, extent.target_source)?
            {
                writer.write_extent(&mut alt_src, extent)?;
            }
        } else {
            writer.write_extent(primary_source, extent)?;
        }
    }

    writer.finish()?;
    Ok(out_path)
}

pub fn inspect_super_image(super_path: &Path, slot: u32) -> Result<SuperImageInfo, LpError> {
    let file = File::open(super_path)?;
    let mut source = ImageSource::open(file)?;
    let (metadata, is_empty) = read_metadata_auto(&mut source, slot)?;
    let raw_text = format_metadata_text(&metadata, slot);
    let partitions = metadata.partitions.iter().map(|p| p.name()).collect();
    let groups: Vec<String> = metadata.groups.iter().map(|g| g.name()).collect();
    let mut active_slot = None;
    for grp in &groups {
        if grp.ends_with("_a") {
            active_slot = Some("A".to_string());
        } else if grp.ends_with("_b") {
            active_slot = Some("B".to_string());
        }
    }
    Ok(SuperImageInfo {
        raw_text,
        partitions,
        groups,
        active_slot,
        is_empty,
    })
}

pub fn filter_partitions(
    metadata: &mut LpMetadata,
    requested: &[String],
    normalize: bool,
) -> Result<(), LpError> {
    if requested.is_empty() {
        return Ok(());
    }
    let mut remaining: HashSet<String> = requested.iter().cloned().collect();
    let mut filtered = Vec::new();
    for part in &metadata.partitions {
        let name = part.name();
        let base = strip_slot_suffix(&name).0.to_string();
        let matched_exact = remaining.remove(&name);
        let matched_norm = normalize && remaining.remove(&base);
        if matched_exact || matched_norm {
            filtered.push(*part);
        }
    }
    if let Some(missing) = remaining.iter().next() {
        return Err(LpError::InvalidMetadata(format!(
            "Could not find partition: {missing}"
        )));
    }
    metadata.partitions = filtered;
    Ok(())
}

pub fn unpack_super_image(
    super_path: &Path,
    out_dir: &Path,
    partitions: &[String],
    slot: u32,
    normalize: bool,
) -> Result<Vec<PathBuf>, LpError> {
    let file = File::open(super_path)?;
    let mut source = ImageSource::open(file)?;
    let (mut metadata, is_empty) = read_metadata_auto(&mut source, slot)?;
    if is_empty {
        return Err(LpError::InvalidMetadata(
            "Empty super image contains metadata only and cannot be unpacked.".into(),
        ));
    }
    filter_partitions(&mut metadata, partitions, normalize)?;
    if !out_dir.exists() {
        fs::create_dir_all(out_dir)?;
    }
    let plan = select_partitions_for_extract(&metadata, normalize, slot);
    let mut outputs = Vec::with_capacity(plan.len());
    for (part, out_name) in &plan {
        let out_path = extract_partition_to_dir(
            super_path,
            &mut source,
            &metadata,
            part,
            out_name,
            out_dir,
        )?;
        outputs.push(out_path);
    }
    Ok(outputs)
}

pub fn unsparse_super_image(src_path: &Path, output_dir: Option<&Path>) -> Result<PathBuf, LpError> {
    let file_name = src_path
        .file_name()
        .ok_or_else(|| LpError::InvalidMetadata("Invalid image filename.".into()))?;
    let target_dir = match output_dir {
        Some(dir) => dir,
        None => src_path
            .parent()
            .filter(|p| !p.as_os_str().is_empty())
            .unwrap_or_else(|| Path::new(".")),
    };
    if !target_dir.exists() {
        fs::create_dir_all(target_dir)?;
    }
    let target_path = target_dir.join(file_name);

    let mut src_file = File::open(src_path)?;
    let is_sparse = SparseReader::is_sparse(&mut src_file)?;
    if !is_sparse {
        return Err(LpError::InvalidMetadata(
            "Input image is not a sparse image.".into(),
        ));
    }

    let mut reader = SparseReader::open(src_file, false)?;
    let tmp_name = format!(
        ".{}.unsparse.{}.tmp",
        file_name.to_string_lossy(),
        process::id()
    );
    let tmp_path = target_dir.join(tmp_name);

    let write_result = (|| -> io::Result<()> {
        let mut out_file = OpenOptions::new()
            .create(true)
            .write(true)
            .truncate(true)
            .open(&tmp_path)?;
        reader.write_raw_to(&mut out_file)?;
        out_file.flush()?;
        Ok(())
    })();

    if let Err(e) = write_result {
        let _ = fs::remove_file(&tmp_path);
        return Err(LpError::Io(e));
    }

    drop(reader);

    if let Err(e) = fs::rename(&tmp_path, &target_path) {
        let _ = fs::remove_file(&tmp_path);
        return Err(LpError::Io(e));
    }

    Ok(target_path)
}
