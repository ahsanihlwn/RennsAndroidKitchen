//! Logical partition table builder and greedy extent allocator.

use std::collections::HashMap;

use super::format::{
    align_to, align_to_offset, get_total_metadata_size, set_fixed_array, LpError, LpMetadata,
    LpMetadataBlockDevice, LpMetadataExtent, LpMetadataGeometry, LpMetadataHeader,
    LpMetadataPartition, LpMetadataPartitionGroup, LpMetadataTableDescriptor, BlockDeviceInfo,
    DEFAULT_GROUP, LP_BLOCK_DEVICE_SLOT_SUFFIXED, LP_GROUP_SLOT_SUFFIXED,
    LP_HEADER_FLAG_VIRTUAL_AB_DEVICE, LP_METADATA_BLOCK_DEVICE_SIZE, LP_METADATA_EXTENT_SIZE,
    LP_METADATA_GEOMETRY_MAGIC, LP_METADATA_GEOMETRY_STRUCT_SIZE, LP_METADATA_GROUP_SIZE,
    LP_METADATA_HEADER_MAGIC, LP_METADATA_HEADER_V1_0_SIZE, LP_METADATA_HEADER_V1_2_SIZE,
    LP_METADATA_MAJOR_VERSION, LP_METADATA_MINOR_VERSION_MIN, LP_METADATA_PARTITION_SIZE,
    LP_METADATA_VERSION_FOR_EXPANDED_HEADER, LP_METADATA_VERSION_FOR_UPDATED_ATTR,
    LP_PARTITION_ATTRIBUTE_MASK, LP_PARTITION_ATTRIBUTE_MASK_V1, LP_PARTITION_ATTR_SLOT_SUFFIXED,
    LP_SECTOR_SIZE, LP_TARGET_TYPE_LINEAR, LP_TARGET_TYPE_ZERO,
};

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Extent {
    Linear {
        num_sectors: u64,
        device_index: u32,
        physical_sector: u64,
    },
    Zero {
        num_sectors: u64,
    },
}

impl Extent {
    pub fn num_sectors(&self) -> u64 {
        match *self {
            Extent::Linear { num_sectors, .. } | Extent::Zero { num_sectors } => num_sectors,
        }
    }

    pub fn set_num_sectors(&mut self, sectors: u64) {
        match self {
            Extent::Linear { num_sectors, .. } | Extent::Zero { num_sectors } => {
                *num_sectors = sectors;
            }
        }
    }

    pub fn as_linear(&self) -> Option<(u64, u32, u64)> {
        match *self {
            Extent::Linear {
                num_sectors,
                device_index,
                physical_sector,
            } => Some((num_sectors, device_index, physical_sector)),
            Extent::Zero { .. } => None,
        }
    }

    pub fn overlaps_interval(&self, interval: &Interval) -> bool {
        match *self {
            Extent::Linear {
                num_sectors,
                device_index,
                physical_sector,
            } => {
                device_index == interval.device_index
                    && physical_sector < interval.end
                    && interval.start < physical_sector + num_sectors
            }
            Extent::Zero { .. } => false,
        }
    }

    pub fn overlaps_linear(&self, other: &Extent) -> bool {
        match (self.as_linear(), other.as_linear()) {
            (Some((s1, d1, p1)), Some((s2, d2, p2))) => {
                d1 == d2 && p1 < p2 + s2 && p2 < p1 + s1
            }
            _ => false,
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Interval {
    pub device_index: u32,
    pub start: u64,
    pub end: u64,
}

impl Interval {
    pub fn new(device_index: u32, start: u64, end: u64) -> Self {
        Self {
            device_index,
            start,
            end,
        }
    }

    pub fn length(&self) -> u64 {
        self.end.saturating_sub(self.start)
    }

    pub fn intersect(a: &Interval, b: &Interval) -> Interval {
        if a.device_index != b.device_index {
            return Interval::new(a.device_index, a.start, a.start);
        }
        let start = a.start.max(b.start);
        let end = start.max(a.end.min(b.end));
        Interval::new(a.device_index, start, end)
    }

    pub fn intersect_lists(a: &[Interval], b: &[Interval]) -> Vec<Interval> {
        let mut ret = Vec::new();
        for ai in a {
            for bi in b {
                let inter = Interval::intersect(ai, bi);
                if inter.length() > 0 {
                    ret.push(inter);
                }
            }
        }
        ret
    }
}

#[derive(Debug, Clone)]
pub struct PartitionGroup {
    pub name: String,
    pub maximum_size: u64,
}

#[derive(Debug, Clone)]
pub struct Partition {
    pub name: String,
    pub group_name: String,
    pub attributes: u32,
    extents: Vec<Extent>,
    size: u64,
}

impl Partition {
    pub fn new(name: impl Into<String>, group_name: impl Into<String>, attributes: u32) -> Self {
        Self {
            name: name.into(),
            group_name: group_name.into(),
            attributes,
            extents: Vec::new(),
            size: 0,
        }
    }

    pub fn extents(&self) -> &[Extent] {
        &self.extents
    }

    pub fn size(&self) -> u64 {
        self.size
    }

    pub fn bytes_on_disk(&self) -> u64 {
        self.extents
            .iter()
            .filter_map(|e| e.as_linear().map(|(s, _, _)| s))
            .sum::<u64>()
            * LP_SECTOR_SIZE
    }

    pub fn add_extent(&mut self, extent: Extent) {
        self.size += extent.num_sectors() * LP_SECTOR_SIZE;
        if let Extent::Linear {
            num_sectors,
            device_index,
            physical_sector,
        } = extent
        {
            if let Some(Extent::Linear {
                num_sectors: prev_sectors,
                device_index: prev_dev,
                physical_sector: prev_phys,
            }) = self.extents.last_mut()
            {
                if *prev_dev == device_index && *prev_phys + *prev_sectors == physical_sector {
                    *prev_sectors += num_sectors;
                    return;
                }
            }
        }
        self.extents.push(extent);
    }

    pub fn remove_extents(&mut self) {
        self.size = 0;
        self.extents.clear();
    }

    pub fn shrink_to(&mut self, aligned_size: u64) {
        if aligned_size == 0 {
            self.remove_extents();
            return;
        }
        let mut sectors_to_remove = (self.size - aligned_size) / LP_SECTOR_SIZE;
        while sectors_to_remove > 0 {
            let last_sectors = match self.extents.last() {
                Some(e) => e.num_sectors(),
                None => break,
            };
            if last_sectors > sectors_to_remove {
                self.size -= sectors_to_remove * LP_SECTOR_SIZE;
                if let Some(last) = self.extents.last_mut() {
                    last.set_num_sectors(last_sectors - sectors_to_remove);
                }
                break;
            }
            self.size -= last_sectors * LP_SECTOR_SIZE;
            sectors_to_remove -= last_sectors;
            self.extents.pop();
        }
    }
}

pub struct MetadataBuilder {
    geometry: LpMetadataGeometry,
    header: LpMetadataHeader,
    partitions: Vec<Partition>,
    groups: Vec<PartitionGroup>,
    block_devices: Vec<LpMetadataBlockDevice>,
    auto_slot_suffixing: bool,
}

impl MetadataBuilder {
    fn base() -> Self {
        let geometry = LpMetadataGeometry {
            magic: LP_METADATA_GEOMETRY_MAGIC,
            struct_size: LP_METADATA_GEOMETRY_STRUCT_SIZE as u32,
            checksum: [0u8; 32],
            metadata_max_size: 0,
            metadata_slot_count: 0,
            logical_block_size: 0,
        };
        let header = LpMetadataHeader {
            magic: LP_METADATA_HEADER_MAGIC,
            major_version: LP_METADATA_MAJOR_VERSION,
            minor_version: LP_METADATA_MINOR_VERSION_MIN,
            header_size: LP_METADATA_HEADER_V1_0_SIZE as u32,
            header_checksum: [0u8; 32],
            tables_size: 0,
            tables_checksum: [0u8; 32],
            partitions: LpMetadataTableDescriptor {
                offset: 0,
                num_entries: 0,
                entry_size: LP_METADATA_PARTITION_SIZE as u32,
            },
            extents: LpMetadataTableDescriptor {
                offset: 0,
                num_entries: 0,
                entry_size: LP_METADATA_EXTENT_SIZE as u32,
            },
            groups: LpMetadataTableDescriptor {
                offset: 0,
                num_entries: 0,
                entry_size: LP_METADATA_GROUP_SIZE as u32,
            },
            block_devices: LpMetadataTableDescriptor {
                offset: 0,
                num_entries: 0,
                entry_size: LP_METADATA_BLOCK_DEVICE_SIZE as u32,
            },
            flags: 0,
            reserved: [0u8; 124],
        };
        Self {
            geometry,
            header,
            partitions: Vec::new(),
            groups: Vec::new(),
            block_devices: Vec::new(),
            auto_slot_suffixing: false,
        }
    }

    pub fn new(
        block_devices: &[BlockDeviceInfo],
        super_partition: &str,
        metadata_max_size: u32,
        metadata_slot_count: u32,
    ) -> Result<Self, LpError> {
        if (metadata_max_size as usize) < LP_METADATA_HEADER_V1_2_SIZE {
            return Err(LpError::InvalidMetadata(
                "Invalid metadata maximum size.".into(),
            ));
        }
        if metadata_slot_count == 0 {
            return Err(LpError::InvalidMetadata(
                "Invalid metadata slot count.".into(),
            ));
        }
        if block_devices.is_empty() {
            return Err(LpError::InvalidMetadata(
                "No block devices were specified.".into(),
            ));
        }

        let metadata_max_size = align_to(metadata_max_size as u64, LP_SECTOR_SIZE as u32) as u32;
        let mut builder = Self::base();
        let mut logical_block_size: u32 = 0;

        for device_info in block_devices {
            verify_device_properties(device_info)?;
            if logical_block_size == 0 {
                logical_block_size = device_info.logical_block_size;
            }
            if logical_block_size != device_info.logical_block_size {
                return Err(LpError::InvalidMetadata(
                    "All partitions must have the same logical block size.".into(),
                ));
            }

            let mut free_area_start = LP_SECTOR_SIZE;
            if device_info.alignment != 0 || device_info.alignment_offset != 0 {
                free_area_start = align_to_offset(
                    free_area_start,
                    device_info.alignment,
                    device_info.alignment_offset,
                );
            } else {
                free_area_start = align_to(free_area_start, logical_block_size);
            }
            let first_logical_sector = free_area_start / LP_SECTOR_SIZE;
            let minimum_size =
                first_logical_sector * LP_SECTOR_SIZE + logical_block_size as u64;
            if device_info.size < minimum_size {
                return Err(LpError::InvalidMetadata(format!(
                    "Block device {} is too small to hold any logical partitions.",
                    device_info.name()
                )));
            }

            let out = LpMetadataBlockDevice {
                first_logical_sector,
                alignment: device_info.alignment,
                alignment_offset: device_info.alignment_offset,
                size: device_info.size,
                partition_name: device_info.partition_name,
                flags: 0,
            };

            if device_info.name() == super_partition {
                builder.block_devices.insert(0, out);
            } else {
                builder.block_devices.push(out);
            }
        }

        if builder
            .block_devices
            .first()
            .map(|d| d.partition_name())
            .as_deref()
            != Some(super_partition)
        {
            return Err(LpError::InvalidMetadata(
                "No super partition was specified.".into(),
            ));
        }

        let total_reserved = get_total_metadata_size(metadata_max_size, metadata_slot_count);
        {
            let super_dev = &mut builder.block_devices[0];
            if super_dev.size < total_reserved {
                return Err(LpError::InvalidMetadata(
                    "Attempting to create metadata on a block device that is too small.".into(),
                ));
            }
            let mut free_area_start = total_reserved;
            if super_dev.alignment != 0 || super_dev.alignment_offset != 0 {
                free_area_start = align_to_offset(
                    free_area_start,
                    super_dev.alignment,
                    super_dev.alignment_offset,
                );
            } else {
                free_area_start = align_to(free_area_start, logical_block_size);
            }
            super_dev.first_logical_sector = free_area_start / LP_SECTOR_SIZE;

            let minimum_disk_size =
                super_dev.first_logical_sector * LP_SECTOR_SIZE + logical_block_size as u64;
            if super_dev.size < minimum_disk_size {
                return Err(LpError::InvalidMetadata(format!(
                    "Device must be at least {minimum_disk_size} bytes, only has {}",
                    super_dev.size
                )));
            }
        }

        builder.geometry.metadata_max_size = metadata_max_size;
        builder.geometry.metadata_slot_count = metadata_slot_count;
        builder.geometry.logical_block_size = logical_block_size;

        builder.add_group(DEFAULT_GROUP, 0)?;
        Ok(builder)
    }

    pub fn from_metadata(metadata: &LpMetadata) -> Result<Self, LpError> {
        let mut builder = Self::base();
        builder.geometry = metadata.geometry;
        builder.block_devices = metadata.block_devices.clone();

        if metadata.header.minor_version >= LP_METADATA_VERSION_FOR_EXPANDED_HEADER {
            builder.require_expanded_metadata_header();
            builder.header.flags = metadata.header.flags;
        }

        for group in &metadata.groups {
            builder.add_group(&group.name(), group.maximum_size)?;
        }

        for part in &metadata.partitions {
            let group_name = metadata.groups[part.group_index as usize].name();
            let idx = builder.add_partition(&part.name(), &group_name, part.attributes)?;
            let start = part.first_extent_index as usize;
            let end = start + part.num_extents as usize;
            for ext in &metadata.extents[start..end] {
                if ext.target_type == LP_TARGET_TYPE_LINEAR {
                    builder.partitions[idx].add_extent(Extent::Linear {
                        num_sectors: ext.num_sectors,
                        device_index: ext.target_source,
                        physical_sector: ext.target_data,
                    });
                } else if ext.target_type == LP_TARGET_TYPE_ZERO {
                    builder.partitions[idx].add_extent(Extent::Zero {
                        num_sectors: ext.num_sectors,
                    });
                }
            }
        }

        Ok(builder)
    }

    pub fn set_auto_slot_suffixing(&mut self) {
        self.auto_slot_suffixing = true;
    }

    pub fn require_expanded_metadata_header(&mut self) {
        if self.header.minor_version >= LP_METADATA_VERSION_FOR_EXPANDED_HEADER {
            return;
        }
        self.header.minor_version = LP_METADATA_VERSION_FOR_EXPANDED_HEADER;
        self.header.header_size = LP_METADATA_HEADER_V1_2_SIZE as u32;
    }

    pub fn set_virtual_ab_device_flag(&mut self) {
        self.require_expanded_metadata_header();
        self.header.flags |= LP_HEADER_FLAG_VIRTUAL_AB_DEVICE;
    }

    pub fn add_group(&mut self, group_name: &str, maximum_size: u64) -> Result<(), LpError> {
        if self.find_group(group_name).is_some() {
            return Err(LpError::InvalidMetadata(format!(
                "Group name {group_name} already exists."
            )));
        }
        self.groups.push(PartitionGroup {
            name: group_name.to_string(),
            maximum_size,
        });
        Ok(())
    }

    pub fn find_group(&self, group_name: &str) -> Option<usize> {
        self.groups.iter().position(|g| g.name == group_name)
    }

    pub fn find_partition(&self, name: &str) -> Option<usize> {
        self.partitions.iter().position(|p| p.name == name)
    }

    pub fn add_partition(
        &mut self,
        name: &str,
        group_name: &str,
        attributes: u32,
    ) -> Result<usize, LpError> {
        if name.is_empty() {
            return Err(LpError::InvalidMetadata(
                "Partition must have a non-empty name.".into(),
            ));
        }
        if self.find_partition(name).is_some() {
            return Err(LpError::InvalidMetadata(format!(
                "Partition {name} already exists."
            )));
        }
        if self.find_group(group_name).is_none() {
            return Err(LpError::InvalidMetadata(format!(
                "Could not find partition group: {group_name}"
            )));
        }
        self.partitions
            .push(Partition::new(name, group_name, attributes));
        Ok(self.partitions.len() - 1)
    }

    pub fn remove_partition(&mut self, name: &str) {
        if let Some(idx) = self.find_partition(name) {
            self.partitions.remove(idx);
        }
    }

    pub fn add_linear_extent(
        &mut self,
        partition_idx: usize,
        block_device: &str,
        num_sectors: u64,
        physical_sector: u64,
    ) -> Result<(), LpError> {
        let device_index = self
            .block_devices
            .iter()
            .position(|d| d.partition_name() == block_device)
            .ok_or_else(|| {
                LpError::InvalidMetadata(format!(
                    "Could not find backing block device for extent: {block_device}"
                ))
            })? as u32;
        self.partitions[partition_idx].add_extent(Extent::Linear {
            num_sectors,
            device_index,
            physical_sector,
        });
        Ok(())
    }

    pub fn change_partition_group(
        &mut self,
        partition_idx: usize,
        group_name: &str,
    ) -> Result<(), LpError> {
        if self.find_group(group_name).is_none() {
            return Err(LpError::InvalidMetadata(format!(
                "Partition cannot change to unknown group: {group_name}"
            )));
        }
        self.partitions[partition_idx].group_name = group_name.to_string();
        Ok(())
    }

    pub fn change_group_size(
        &mut self,
        group_name: &str,
        maximum_size: u64,
    ) -> Result<(), LpError> {
        if group_name == DEFAULT_GROUP {
            return Err(LpError::InvalidMetadata(
                "Cannot change the size of the default group.".into(),
            ));
        }
        let idx = self.find_group(group_name).ok_or_else(|| {
            LpError::InvalidMetadata(format!(
                "Cannot change size of unknown partition group: {group_name}"
            ))
        })?;
        self.groups[idx].maximum_size = maximum_size;
        Ok(())
    }

    pub fn logical_block_size(&self) -> u32 {
        self.geometry.logical_block_size
    }

    pub fn allocatable_space(&self) -> u64 {
        self.block_devices
            .iter()
            .map(|d| d.size - d.first_logical_sector * LP_SECTOR_SIZE)
            .sum()
    }

    pub fn used_space(&self) -> u64 {
        self.partitions.iter().map(|p| p.size()).sum()
    }

    fn total_size_of_group(&self, group_name: &str) -> u64 {
        self.partitions
            .iter()
            .filter(|p| p.group_name == group_name)
            .map(|p| p.bytes_on_disk())
            .sum()
    }

    fn align_sector(&self, block_device: &LpMetadataBlockDevice, sector: u64) -> u64 {
        let lba = sector * LP_SECTOR_SIZE;
        let aligned = align_to_offset(lba, block_device.alignment, block_device.alignment_offset);
        align_to(aligned, LP_SECTOR_SIZE as u32) / LP_SECTOR_SIZE
    }

    pub fn get_free_regions(&self) -> Vec<Interval> {
        let mut device_extents: Vec<Vec<Interval>> = vec![Vec::new(); self.block_devices.len()];
        for partition in &self.partitions {
            for extent in partition.extents() {
                if let Some((num_sectors, device_index, physical_sector)) = extent.as_linear() {
                    device_extents[device_index as usize].push(Interval::new(
                        device_index,
                        physical_sector,
                        physical_sector + num_sectors,
                    ));
                }
            }
        }

        let mut free_regions = Vec::new();
        for (i, extents) in device_extents.iter_mut().enumerate() {
            let block_device = &self.block_devices[i];
            let first_sector = block_device.first_logical_sector;
            let last_sector = block_device.size / LP_SECTOR_SIZE;
            extents.push(Interval::new(i as u32, first_sector, first_sector));
            extents.push(Interval::new(i as u32, last_sector, last_sector));
            extents.sort_by(|a, b| (a.start, a.end).cmp(&(b.start, b.end)));

            for pair in extents.windows(2) {
                let previous = &pair[0];
                let current = &pair[1];
                let aligned = self.align_sector(&self.block_devices[current.device_index as usize], previous.end);
                if aligned < current.start {
                    free_regions.push(Interval::new(current.device_index, aligned, current.start));
                }
            }
        }
        free_regions
    }

    fn validate_partition_size_change(
        &self,
        partition_idx: usize,
        old_size: u64,
        new_size: u64,
        force_check: bool,
    ) -> Result<(), LpError> {
        let partition = &self.partitions[partition_idx];
        let group_idx = self
            .find_group(&partition.group_name)
            .ok_or_else(|| LpError::InvalidMetadata("Partition belongs to unknown group.".into()))?;
        let group = &self.groups[group_idx];

        if !force_check && new_size <= old_size {
            return Ok(());
        }

        let space_needed = new_size.saturating_sub(old_size);
        if group.maximum_size > 0 {
            let group_size = self.total_size_of_group(&group.name);
            if group_size >= group.maximum_size || group.maximum_size - group_size < space_needed {
                return Err(LpError::InvalidMetadata(format!(
                    "Partition {} is part of group {} which does not have enough space free ({space_needed} requested, {group_size} used out of {})",
                    partition.name, group.name, group.maximum_size
                )));
            }
        }
        Ok(())
    }

    fn is_any_region_allocated(&self, candidate: &Extent) -> bool {
        for partition in &self.partitions {
            for extent in partition.extents() {
                if extent.overlaps_linear(candidate) {
                    return true;
                }
            }
        }
        false
    }

    fn extend_final_extent(
        &self,
        partition_idx: usize,
        free_list: &[Interval],
        sectors_needed: u64,
    ) -> Option<Extent> {
        let partition = &self.partitions[partition_idx];
        let (num_sectors, device_index, physical_sector) = partition.extents().last()?.as_linear()?;
        let end_sector = physical_sector + num_sectors;
        let block_device = &self.block_devices[device_index as usize];
        let next_aligned_sector = self.align_sector(block_device, end_sector);
        if end_sector == next_aligned_sector {
            return None;
        }
        let extend_sectors = (next_aligned_sector - end_sector).min(sectors_needed);
        let candidate = Extent::Linear {
            num_sectors: extend_sectors,
            device_index,
            physical_sector: end_sector,
        };
        if self.is_any_region_allocated(&candidate)
            || free_list.iter().any(|r| candidate.overlaps_interval(r))
        {
            return None;
        }
        Some(candidate)
    }

    fn grow_partition(
        &mut self,
        partition_idx: usize,
        aligned_size: u64,
        free_region_hint: &[Interval],
    ) -> Result<(), LpError> {
        let space_needed = aligned_size - self.partitions[partition_idx].size();
        let mut sectors_needed = space_needed / LP_SECTOR_SIZE;

        let mut free_regions = self.get_free_regions();
        if !free_region_hint.is_empty() {
            free_regions = Interval::intersect_lists(&free_regions, free_region_hint);
        }

        let sectors_per_block = self.geometry.logical_block_size as u64 / LP_SECTOR_SIZE;
        let mut new_extents = Vec::new();

        if let Some(ext) = self.extend_final_extent(partition_idx, &free_regions, sectors_needed) {
            sectors_needed -= ext.num_sectors();
            new_extents.push(ext);
        }

        for region in &mut free_regions {
            if sectors_needed == 0 {
                break;
            }
            if region.length() % sectors_per_block != 0 {
                region.end = region.start + (region.length() / sectors_per_block) * sectors_per_block;
                if region.length() == 0 {
                    continue;
                }
            }
            let sectors = sectors_needed.min(region.length());
            new_extents.push(Extent::Linear {
                num_sectors: sectors,
                device_index: region.device_index,
                physical_sector: region.start,
            });
            sectors_needed -= sectors;
        }

        if sectors_needed > 0 {
            return Err(LpError::InvalidMetadata(format!(
                "Not enough space on device for partition {} with size {}",
                self.partitions[partition_idx].name, aligned_size
            )));
        }

        for ext in new_extents {
            self.partitions[partition_idx].add_extent(ext);
        }
        Ok(())
    }

    pub fn resize_partition(
        &mut self,
        partition_idx: usize,
        requested_size: u64,
        free_region_hint: &[Interval],
    ) -> Result<(), LpError> {
        let aligned_size = align_to(requested_size, self.geometry.logical_block_size);
        let old_size = self.partitions[partition_idx].size();

        self.validate_partition_size_change(partition_idx, old_size, aligned_size, false)?;

        if aligned_size > old_size {
            self.grow_partition(partition_idx, aligned_size, free_region_hint)?;
        } else if aligned_size < old_size {
            self.partitions[partition_idx].shrink_to(aligned_size);
        }
        Ok(())
    }

    fn validate_partition_groups(&self) -> Result<(), LpError> {
        for group in &self.groups {
            if group.maximum_size == 0 {
                continue;
            }
            let used = self.total_size_of_group(&group.name);
            if used > group.maximum_size {
                return Err(LpError::InvalidMetadata(format!(
                    "Partition group {} exceeds maximum size ({used} bytes used, maximum {})",
                    group.name, group.maximum_size
                )));
            }
        }
        Ok(())
    }

    pub fn export(&self) -> Result<LpMetadata, LpError> {
        self.validate_partition_groups()?;

        let mut header = self.header;
        let geometry = self.geometry;

        let mut block_devices = Vec::with_capacity(self.block_devices.len());
        for dev in &self.block_devices {
            let mut d = *dev;
            if self.auto_slot_suffixing {
                d.flags |= LP_BLOCK_DEVICE_SLOT_SUFFIXED;
            }
            block_devices.push(d);
        }

        let mut group_indices = HashMap::new();
        let mut groups = Vec::with_capacity(self.groups.len());
        for group in &self.groups {
            let mut out = LpMetadataPartitionGroup {
                name: [0u8; 36],
                flags: if self.auto_slot_suffixing && group.name != DEFAULT_GROUP {
                    LP_GROUP_SLOT_SUFFIXED
                } else {
                    0
                },
                maximum_size: group.maximum_size,
            };
            if !set_fixed_array(&mut out.name, &group.name) {
                return Err(LpError::InvalidMetadata(format!(
                    "Partition group name is too long: {}",
                    group.name
                )));
            }
            group_indices.insert(group.name.as_str(), groups.len() as u32);
            groups.push(out);
        }

        let mut partitions = Vec::with_capacity(self.partitions.len());
        let mut extents = Vec::new();

        for partition in &self.partitions {
            if partition.attributes & !LP_PARTITION_ATTRIBUTE_MASK != 0 {
                return Err(LpError::InvalidMetadata(format!(
                    "Partition {} has unsupported attribute.",
                    partition.name
                )));
            }
            if partition.attributes & LP_PARTITION_ATTRIBUTE_MASK_V1 != 0 {
                header.minor_version = header.minor_version.max(LP_METADATA_VERSION_FOR_UPDATED_ATTR);
            }

            let group_index = *group_indices.get(partition.group_name.as_str()).ok_or_else(|| {
                LpError::InvalidMetadata(format!(
                    "Partition {} is a member of unknown group {}",
                    partition.name, partition.group_name
                ))
            })?;

            let mut part = LpMetadataPartition {
                name: [0u8; 36],
                attributes: partition.attributes
                    | if self.auto_slot_suffixing {
                        LP_PARTITION_ATTR_SLOT_SUFFIXED
                    } else {
                        0
                    },
                first_extent_index: extents.len() as u32,
                num_extents: partition.extents.len() as u32,
                group_index,
            };
            if !set_fixed_array(&mut part.name, &partition.name) {
                return Err(LpError::InvalidMetadata(format!(
                    "Partition name is too long: {}",
                    partition.name
                )));
            }

            for ext in &partition.extents {
                match *ext {
                    Extent::Linear {
                        num_sectors,
                        device_index,
                        physical_sector,
                    } => {
                        if device_index as usize >= block_devices.len() {
                            return Err(LpError::InvalidMetadata(
                                "Extent references unknown block device.".into(),
                            ));
                        }
                        extents.push(LpMetadataExtent {
                            num_sectors,
                            target_type: LP_TARGET_TYPE_LINEAR,
                            target_data: physical_sector,
                            target_source: device_index,
                        });
                    }
                    Extent::Zero { num_sectors } => {
                        extents.push(LpMetadataExtent {
                            num_sectors,
                            target_type: LP_TARGET_TYPE_ZERO,
                            target_data: 0,
                            target_source: 0,
                        });
                    }
                }
            }

            partitions.push(part);
        }

        header.partitions.num_entries = partitions.len() as u32;
        header.extents.num_entries = extents.len() as u32;
        header.groups.num_entries = groups.len() as u32;
        header.block_devices.num_entries = block_devices.len() as u32;

        Ok(LpMetadata {
            geometry,
            header,
            partitions,
            extents,
            groups,
            block_devices,
        })
    }
}

fn verify_device_properties(device_info: &BlockDeviceInfo) -> Result<(), LpError> {
    let name = device_info.name();
    if device_info.logical_block_size == 0 {
        return Err(LpError::InvalidMetadata(format!(
            "Block device {name} logical block size must not be zero."
        )));
    }
    if device_info.logical_block_size as u64 % LP_SECTOR_SIZE != 0 {
        return Err(LpError::InvalidMetadata(format!(
            "Block device {name} logical block size must be a multiple of 512."
        )));
    }
    if device_info.size % device_info.logical_block_size as u64 != 0 {
        return Err(LpError::InvalidMetadata(format!(
            "Block device {name} size must be a multiple of its block size."
        )));
    }
    if device_info.alignment_offset as u64 % LP_SECTOR_SIZE != 0 {
        return Err(LpError::InvalidMetadata(format!(
            "Block device {name} alignment offset is not sector-aligned."
        )));
    }
    if device_info.alignment as u64 % LP_SECTOR_SIZE != 0 {
        return Err(LpError::InvalidMetadata(format!(
            "Block device {name} partition alignment is not sector-aligned."
        )));
    }
    if device_info.alignment_offset > device_info.alignment {
        return Err(LpError::InvalidMetadata(format!(
            "Block device {name} partition alignment offset is greater than its alignment."
        )));
    }
    Ok(())
}
