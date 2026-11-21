//! Logical partition metadata serializer and super image builder.

use std::cell::RefCell;
use std::collections::BTreeMap;
use std::fs::{self, File, OpenOptions};
use std::io::{Read, Seek, SeekFrom, Write};
use std::path::{Path, PathBuf};
use std::rc::Rc;

use crate::sha256::sha256;
use crate::sparse::{ImageSource, SparseFile};

use super::format::{
    LpError, LpMetadata, LpMetadataGeometry, LP_METADATA_GEOMETRY_SIZE,
    LP_PARTITION_RESERVED_BYTES, LP_SECTOR_SIZE, LP_TARGET_TYPE_LINEAR,
};

pub fn serialize_geometry(input: &LpMetadataGeometry) -> Vec<u8> {
    let mut geometry = *input;
    geometry.checksum = [0u8; 32];
    let raw = geometry.to_bytes();
    geometry.checksum = sha256(&raw);

    let mut blob = vec![0u8; LP_METADATA_GEOMETRY_SIZE];
    let final_raw = geometry.to_bytes();
    blob[..final_raw.len()].copy_from_slice(&final_raw);
    blob
}

pub fn serialize_metadata(input: &LpMetadata) -> Vec<u8> {
    let mut header = input.header;

    let mut partitions_bytes = Vec::with_capacity(input.partitions.len() * 52);
    for p in &input.partitions {
        partitions_bytes.extend_from_slice(&p.to_bytes());
    }

    let mut extents_bytes = Vec::with_capacity(input.extents.len() * 24);
    for e in &input.extents {
        extents_bytes.extend_from_slice(&e.to_bytes());
    }

    let mut groups_bytes = Vec::with_capacity(input.groups.len() * 48);
    for g in &input.groups {
        groups_bytes.extend_from_slice(&g.to_bytes());
    }

    let mut block_devices_bytes = Vec::with_capacity(input.block_devices.len() * 64);
    for d in &input.block_devices {
        block_devices_bytes.extend_from_slice(&d.to_bytes());
    }

    header.partitions.offset = 0;
    header.extents.offset = header.partitions.offset + partitions_bytes.len() as u32;
    header.groups.offset = header.extents.offset + extents_bytes.len() as u32;
    header.block_devices.offset = header.groups.offset + groups_bytes.len() as u32;
    header.tables_size = header.block_devices.offset + block_devices_bytes.len() as u32;

    let mut tables = Vec::with_capacity(header.tables_size as usize);
    tables.extend_from_slice(&partitions_bytes);
    tables.extend_from_slice(&extents_bytes);
    tables.extend_from_slice(&groups_bytes);
    tables.extend_from_slice(&block_devices_bytes);

    header.tables_checksum = sha256(&tables);
    header.header_checksum = [0u8; 32];
    let header_zeroed = header.to_bytes();
    header.header_checksum = sha256(&header_zeroed);

    let mut out = header.to_bytes();
    out.extend_from_slice(&tables);
    out
}

/// Write a metadata-only super image (`super_empty.img`).
pub fn write_empty_image_file(path: &Path, metadata: &LpMetadata) -> Result<(), LpError> {
    let geometry = serialize_geometry(&metadata.geometry);
    let meta_blob = serialize_metadata(metadata);
    let mut file = OpenOptions::new()
        .create(true)
        .write(true)
        .truncate(true)
        .open(path)?;
    file.write_all(&geometry)?;
    file.write_all(&meta_blob)?;
    Ok(())
}

pub fn write_to_image_file(
    path: &Path,
    metadata: &LpMetadata,
    block_size: u32,
    images: &BTreeMap<String, PathBuf>,
    sparsify: bool,
) -> Result<(), LpError> {
    let mut builder = ImageBuilder::new(metadata, block_size, images, sparsify)?;
    builder.build()?;
    builder.export(path)
}

pub fn write_split_image_files(
    output_dir: &Path,
    metadata: &LpMetadata,
    block_size: u32,
    images: &BTreeMap<String, PathBuf>,
    sparsify: bool,
) -> Result<(), LpError> {
    let mut builder = ImageBuilder::new(metadata, block_size, images, sparsify)?;
    builder.build()?;
    builder.export_files(output_dir)
}

pub struct ImageBuilder<'a> {
    metadata: &'a LpMetadata,
    block_size: u32,
    sparsify: bool,
    images: BTreeMap<String, PathBuf>,
    device_images: Vec<SparseFile>,
}

impl<'a> ImageBuilder<'a> {
    pub fn new(
        metadata: &'a LpMetadata,
        block_size: u32,
        images: &BTreeMap<String, PathBuf>,
        sparsify: bool,
    ) -> Result<Self, LpError> {
        let total_size = metadata.total_super_partition_size();
        if block_size == 0 || block_size as u64 % LP_SECTOR_SIZE != 0 {
            return Err(LpError::InvalidMetadata(format!(
                "Block size must be a multiple of the sector size, {LP_SECTOR_SIZE}"
            )));
        }
        if total_size % block_size as u64 != 0 {
            return Err(LpError::InvalidMetadata(format!(
                "Device size must be a multiple of the block size, {block_size}"
            )));
        }
        if metadata.geometry.metadata_max_size % block_size != 0 {
            return Err(LpError::InvalidMetadata(format!(
                "Metadata max size must be a multiple of the block size, {block_size}"
            )));
        }
        if LP_METADATA_GEOMETRY_SIZE % block_size as usize != 0 {
            return Err(LpError::InvalidMetadata(format!(
                "Geometry size is not a multiple of the block size, {block_size}"
            )));
        }
        if LP_PARTITION_RESERVED_BYTES % block_size as u64 != 0 {
            return Err(LpError::InvalidMetadata(format!(
                "Reserved size is not a multiple of the block size, {block_size}"
            )));
        }

        let num_blocks = total_size / block_size as u64;
        if num_blocks >= u32::MAX as u64 {
            return Err(LpError::InvalidMetadata(
                "Block device is too large to encode with libsparse.".into(),
            ));
        }

        let mut device_images = Vec::with_capacity(metadata.block_devices.len());
        for dev in &metadata.block_devices {
            device_images.push(SparseFile::new(block_size, dev.size)?);
        }

        Ok(Self {
            metadata,
            block_size,
            sparsify,
            images: images.clone(),
            device_images,
        })
    }

    pub fn build(&mut self) -> Result<(), LpError> {
        if self.device_images.is_empty() {
            return Err(LpError::InvalidMetadata(
                "Metadata specifies no block devices.".into(),
            ));
        }

        self.device_images[0].add_fill(0, LP_PARTITION_RESERVED_BYTES, 0);

        let geometry_blob = serialize_geometry(&self.metadata.geometry);
        let mut metadata_blob = serialize_metadata(self.metadata);
        if metadata_blob.len() > self.metadata.geometry.metadata_max_size as usize {
            return Err(LpError::InvalidMetadata(format!(
                "Logical partition metadata is too large: {} > {}",
                metadata_blob.len(),
                self.metadata.geometry.metadata_max_size
            )));
        }
        metadata_blob.resize(self.metadata.geometry.metadata_max_size as usize, 0);

        let slot_copies = self.metadata.geometry.metadata_slot_count as usize * 2;
        let mut all_metadata =
            Vec::with_capacity(geometry_blob.len() * 2 + metadata_blob.len() * slot_copies);
        all_metadata.extend_from_slice(&geometry_blob);
        all_metadata.extend_from_slice(&geometry_blob);
        for _ in 0..slot_copies {
            all_metadata.extend_from_slice(&metadata_blob);
        }

        let first_sector = LP_PARTITION_RESERVED_BYTES / LP_SECTOR_SIZE;
        let first_block = self.sector_to_block(first_sector)?;
        self.device_images[0].add_data(all_metadata, first_block);

        self.check_extent_ordering()?;

        for partition in &self.metadata.partitions {
            let name = partition.name();
            if let Some(image_path) = self.images.remove(&name) {
                self.add_partition_image(partition, &image_path)?;
            }
        }

        if let Some((unused, _)) = self.images.iter().next() {
            return Err(LpError::InvalidMetadata(format!(
                "Partition image was specified for unknown partition: {unused}"
            )));
        }
        Ok(())
    }

    pub fn export(&mut self, file_path: &Path) -> Result<(), LpError> {
        if self.device_images.len() > 1 {
            return Err(LpError::InvalidMetadata(
                "Cannot export to a single image on multi-device builds.".into(),
            ));
        }
        let mut file = OpenOptions::new()
            .create(true)
            .read(true)
            .write(true)
            .truncate(true)
            .open(file_path)?;
        self.device_images[0].write_to_file(&mut file, self.sparsify, false)?;
        Ok(())
    }

    pub fn export_files(&mut self, output_dir: &Path) -> Result<(), LpError> {
        if !output_dir.exists() {
            fs::create_dir_all(output_dir)?;
        }
        for (i, sparse_file) in self.device_images.iter_mut().enumerate() {
            let dev_name = self.metadata.block_devices[i].partition_name();
            let file_path = output_dir.join(format!("super_{dev_name}.img"));
            let mut file = OpenOptions::new()
                .create(true)
                .read(true)
                .write(true)
                .truncate(true)
                .open(&file_path)?;
            sparse_file.write_to_file(&mut file, self.sparsify, false)?;
        }
        Ok(())
    }

    fn sector_to_block(&self, sector: u64) -> Result<u32, LpError> {
        let byte_offset = sector * LP_SECTOR_SIZE;
        if byte_offset % self.block_size as u64 != 0 {
            return Err(LpError::InvalidMetadata(format!(
                "Sector {sector} is not aligned to block size {}",
                self.block_size
            )));
        }
        Ok((byte_offset / self.block_size as u64) as u32)
    }

    fn check_extent_ordering(&self) -> Result<(), LpError> {
        let mut last_sectors = vec![0u64; self.metadata.block_devices.len()];
        for extent in &self.metadata.extents {
            if extent.target_type != LP_TARGET_TYPE_LINEAR {
                return Err(LpError::InvalidMetadata(
                    "Extents must all be type linear.".into(),
                ));
            }
            let src = extent.target_source as usize;
            if extent.target_data <= last_sectors[src] {
                return Err(LpError::InvalidMetadata(
                    "Extents must appear in increasing order.".into(),
                ));
            }
            if (extent.num_sectors * LP_SECTOR_SIZE) % self.block_size as u64 != 0 {
                return Err(LpError::InvalidMetadata(
                    "Extents must be aligned to the block size.".into(),
                ));
            }
            last_sectors[src] = extent.target_data;
        }
        Ok(())
    }

    fn add_partition_image(
        &mut self,
        partition: &super::format::LpMetadataPartition,
        file_path: &Path,
    ) -> Result<(), LpError> {
        let file = File::open(file_path).map_err(|e| {
            LpError::InvalidMetadata(format!(
                "Could not open image '{}' for partition {}: {e}",
                file_path.display(),
                partition.name()
            ))
        })?;
        let source = Rc::new(RefCell::new(ImageSource::open(file)?));
        let file_length = source.borrow().logical_size();

        let partition_size = self.metadata.partition_size(partition);
        if file_length > partition_size {
            return Err(LpError::InvalidMetadata(format!(
                "Image for partition '{}' is greater than its size ({file_length}, expected {partition_size})",
                partition.name()
            )));
        }
        if file_length == 0 {
            return Ok(());
        }
        if partition.num_extents == 0 {
            return Err(LpError::InvalidMetadata(format!(
                "Partition '{}' has no extents.",
                partition.name()
            )));
        }

        let mut extent_index = partition.first_extent_index as usize;
        let first_extent = &self.metadata.extents[extent_index];
        if first_extent.target_type != LP_TARGET_TYPE_LINEAR {
            return Err(LpError::InvalidMetadata(format!(
                "Partition should only have linear extents: {}",
                partition.name()
            )));
        }

        let mut output_sector: u64 = 0;
        let mut extent_last_sector = first_extent.num_sectors;
        let mut output_block = self.sector_to_block(first_extent.target_data)?;
        let mut output_device_idx = first_extent.target_source as usize;

        source.borrow_mut().seek(SeekFrom::Start(0))?;

        let blk_sz = self.block_size as usize;
        let batch_bytes = blk_sz * ((1024 * 1024) / blk_sz).max(1);
        let mut buffer = vec![0u8; batch_bytes];
        let mut pos: u64 = 0;
        let mut remaining = file_length;

        while remaining > 0 {
            let batch_read = (remaining as usize).min(batch_bytes);
            {
                let mut src = source.borrow_mut();
                src.seek(SeekFrom::Start(pos))?;
                src.read_exact(&mut buffer[..batch_read])?;
            }

            let mut offset = 0;
            while offset < batch_read {
                if output_sector == extent_last_sector {
                    extent_index += 1;
                    if extent_index
                        >= (partition.first_extent_index + partition.num_extents) as usize
                    {
                        return Err(LpError::InvalidMetadata(
                            "Image is larger than extent table.".into(),
                        ));
                    }
                    let ext = &self.metadata.extents[extent_index];
                    if ext.target_type != LP_TARGET_TYPE_LINEAR {
                        return Err(LpError::InvalidMetadata(format!(
                            "Partition should only have linear extents: {}",
                            partition.name()
                        )));
                    }
                    extent_last_sector += ext.num_sectors;
                    output_device_idx = ext.target_source as usize;
                    output_block = self.sector_to_block(ext.target_data)?;
                }

                let read_size = (batch_read - offset).min(blk_sz);
                let block_slice = &buffer[offset..offset + read_size];

                if read_size == blk_sz {
                    if let Some(fill_val) = uniform_fill_u32(block_slice) {
                        self.device_images[output_device_idx].add_fill(
                            fill_val,
                            blk_sz as u64,
                            output_block,
                        );
                    } else {
                        self.device_images[output_device_idx].add_source(
                            Rc::clone(&source),
                            pos + offset as u64,
                            blk_sz as u64,
                            output_block,
                        );
                    }
                } else {
                    // Partial trailing block (< block_size): zero-pad to block size.
                    let mut padded = vec![0u8; blk_sz];
                    padded[..read_size].copy_from_slice(block_slice);
                    if let Some(fill_val) = uniform_fill_u32(&padded) {
                        self.device_images[output_device_idx].add_fill(
                            fill_val,
                            blk_sz as u64,
                            output_block,
                        );
                    } else {
                        self.device_images[output_device_idx]
                            .add_data(block_slice.to_vec(), output_block);
                    }
                }

                offset += read_size;
                output_sector += self.block_size as u64 / LP_SECTOR_SIZE;
                output_block += 1;
            }

            pos += batch_read as u64;
            remaining -= batch_read as u64;
        }

        Ok(())
    }
}

fn uniform_fill_u32(buf: &[u8]) -> Option<u32> {
    if buf.len() < 4 || buf.len() % 4 != 0 {
        return None;
    }
    let first = &buf[0..4];
    if buf.chunks_exact(4).all(|c| c == first) {
        Some(u32::from_le_bytes(first.try_into().unwrap()))
    } else {
        None
    }
}
