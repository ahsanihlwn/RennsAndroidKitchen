//! Logical partition metadata parser and reader.

use std::io::{Cursor, Read, Seek, SeekFrom};

use crate::sha256::sha256;

use super::format::{
    get_backup_geometry_offset, get_backup_metadata_offset, get_primary_geometry_offset,
    get_primary_metadata_offset, get_total_metadata_size, slot_suffix_for_slot_number, LpError,
    LpMetadata, LpMetadataBlockDevice, LpMetadataExtent, LpMetadataGeometry, LpMetadataHeader,
    LpMetadataPartition, LpMetadataPartitionGroup, LpMetadataTableDescriptor,
    LP_BLOCK_DEVICE_SLOT_SUFFIXED, LP_GROUP_SLOT_SUFFIXED, LP_METADATA_BLOCK_DEVICE_SIZE,
    LP_METADATA_EXTENT_SIZE, LP_METADATA_GEOMETRY_MAGIC, LP_METADATA_GEOMETRY_SIZE,
    LP_METADATA_GEOMETRY_STRUCT_SIZE, LP_METADATA_GROUP_SIZE, LP_METADATA_HEADER_MAGIC,
    LP_METADATA_HEADER_V1_0_SIZE, LP_METADATA_HEADER_V1_2_SIZE, LP_METADATA_MAJOR_VERSION,
    LP_METADATA_MINOR_VERSION_MAX, LP_METADATA_PARTITION_SIZE,
    LP_METADATA_VERSION_FOR_EXPANDED_HEADER, LP_METADATA_VERSION_FOR_UPDATED_ATTR,
    LP_PARTITION_ATTRIBUTE_MASK_V0, LP_PARTITION_ATTRIBUTE_MASK_V1,
    LP_PARTITION_ATTR_SLOT_SUFFIXED, LP_SECTOR_SIZE, LP_TARGET_TYPE_LINEAR,
};

pub fn parse_geometry(buffer: &[u8]) -> Result<LpMetadataGeometry, LpError> {
    let geometry = LpMetadataGeometry::from_bytes(buffer)?;
    if geometry.magic != LP_METADATA_GEOMETRY_MAGIC {
        return Err(LpError::InvalidMetadata(
            "Logical partition metadata has invalid geometry magic signature.".into(),
        ));
    }
    if geometry.struct_size as usize > LP_METADATA_GEOMETRY_STRUCT_SIZE {
        return Err(LpError::InvalidMetadata(
            "Logical partition metadata has unrecognized fields.".into(),
        ));
    }
    if geometry.struct_size as usize != LP_METADATA_GEOMETRY_STRUCT_SIZE {
        return Err(LpError::InvalidMetadata(
            "Logical partition metadata has invalid struct size.".into(),
        ));
    }

    let mut temp = geometry;
    temp.checksum = [0u8; 32];
    let computed = sha256(&temp.to_bytes()[..geometry.struct_size as usize]);
    if computed != geometry.checksum {
        return Err(LpError::InvalidMetadata(
            "Logical partition metadata has invalid geometry checksum.".into(),
        ));
    }

    if geometry.metadata_slot_count == 0 {
        return Err(LpError::InvalidMetadata(
            "Logical partition metadata has invalid slot count.".into(),
        ));
    }
    if geometry.metadata_max_size as u64 % LP_SECTOR_SIZE != 0 {
        return Err(LpError::InvalidMetadata(
            "Metadata max size is not sector-aligned.".into(),
        ));
    }
    Ok(geometry)
}

pub fn read_primary_geometry<R: Read + Seek>(reader: &mut R) -> Result<LpMetadataGeometry, LpError> {
    reader.seek(SeekFrom::Start(get_primary_geometry_offset()))?;
    let mut buffer = vec![0u8; LP_METADATA_GEOMETRY_SIZE];
    reader.read_exact(&mut buffer)?;
    parse_geometry(&buffer)
}

pub fn read_backup_geometry<R: Read + Seek>(reader: &mut R) -> Result<LpMetadataGeometry, LpError> {
    reader.seek(SeekFrom::Start(get_backup_geometry_offset()))?;
    let mut buffer = vec![0u8; LP_METADATA_GEOMETRY_SIZE];
    reader.read_exact(&mut buffer)?;
    parse_geometry(&buffer)
}

pub fn read_logical_partition_geometry<R: Read + Seek>(
    reader: &mut R,
) -> Result<LpMetadataGeometry, LpError> {
    match read_primary_geometry(reader) {
        Ok(geom) => Ok(geom),
        Err(primary_err) => read_backup_geometry(reader).map_err(|_| primary_err),
    }
}

fn validate_table_bounds(header: &LpMetadataHeader, table: &LpMetadataTableDescriptor) -> bool {
    if table.offset > header.tables_size {
        return false;
    }
    let Some(table_size) = (table.num_entries as u64).checked_mul(table.entry_size as u64) else {
        return false;
    };
    (header.tables_size - table.offset) as u64 >= table_size
}

fn read_metadata_header<R: Read>(reader: &mut R) -> Result<LpMetadataHeader, LpError> {
    let mut v1_buf = [0u8; LP_METADATA_HEADER_V1_0_SIZE];
    reader.read_exact(&mut v1_buf)?;
    let mut header = LpMetadataHeader::from_v1_0_bytes(&v1_buf)?;

    if header.magic != LP_METADATA_HEADER_MAGIC {
        return Err(LpError::InvalidMetadata(
            "Logical partition metadata has invalid magic value.".into(),
        ));
    }
    if header.major_version != LP_METADATA_MAJOR_VERSION
        || header.minor_version > LP_METADATA_MINOR_VERSION_MAX
    {
        return Err(LpError::InvalidMetadata(
            "Logical partition metadata has incompatible version.".into(),
        ));
    }

    let expected_struct_size = if header.minor_version < LP_METADATA_VERSION_FOR_EXPANDED_HEADER {
        LP_METADATA_HEADER_V1_0_SIZE as u32
    } else {
        LP_METADATA_HEADER_V1_2_SIZE as u32
    };
    if header.header_size != expected_struct_size {
        return Err(LpError::InvalidMetadata(
            "Invalid partition metadata header struct size.".into(),
        ));
    }

    let remaining = header.header_size as usize - LP_METADATA_HEADER_V1_0_SIZE;
    if remaining > 0 {
        let mut tail = vec![0u8; remaining];
        reader.read_exact(&mut tail)?;
        header.fill_v1_2_tail(&tail)?;
    }

    let mut temp = header;
    temp.header_checksum = [0u8; 32];
    let computed = sha256(&temp.to_bytes());
    if computed != header.header_checksum {
        return Err(LpError::InvalidMetadata(
            "Logical partition metadata has invalid checksum.".into(),
        ));
    }

    if !validate_table_bounds(&header, &header.partitions)
        || !validate_table_bounds(&header, &header.extents)
        || !validate_table_bounds(&header, &header.groups)
        || !validate_table_bounds(&header, &header.block_devices)
    {
        return Err(LpError::InvalidMetadata(
            "Logical partition metadata has invalid table bounds.".into(),
        ));
    }

    if header.partitions.entry_size as usize != LP_METADATA_PARTITION_SIZE {
        return Err(LpError::InvalidMetadata(
            "Logical partition metadata has invalid partition table entry size.".into(),
        ));
    }
    if header.extents.entry_size as usize != LP_METADATA_EXTENT_SIZE {
        return Err(LpError::InvalidMetadata(
            "Logical partition metadata has invalid extent table entry size.".into(),
        ));
    }
    if header.groups.entry_size as usize != LP_METADATA_GROUP_SIZE {
        return Err(LpError::InvalidMetadata(
            "Logical partition metadata has invalid group table entry size.".into(),
        ));
    }
    if header.block_devices.entry_size as usize != LP_METADATA_BLOCK_DEVICE_SIZE {
        return Err(LpError::InvalidMetadata(
            "Logical partition metadata has invalid block device table entry size.".into(),
        ));
    }

    Ok(header)
}

pub fn parse_metadata<R: Read>(
    geometry: LpMetadataGeometry,
    reader: &mut R,
) -> Result<LpMetadata, LpError> {
    let header = read_metadata_header(reader)?;

    if header.tables_size > geometry.metadata_max_size {
        return Err(LpError::InvalidMetadata(
            "Invalid partition metadata header table size.".into(),
        ));
    }

    let mut table_buf = vec![0u8; header.tables_size as usize];
    reader.read_exact(&mut table_buf)?;

    let computed = sha256(&table_buf);
    if computed != header.tables_checksum {
        return Err(LpError::InvalidMetadata(
            "Logical partition metadata has invalid table checksum.".into(),
        ));
    }

    let mut valid_attributes = LP_PARTITION_ATTRIBUTE_MASK_V0;
    if header.minor_version >= LP_METADATA_VERSION_FOR_UPDATED_ATTR {
        valid_attributes |= LP_PARTITION_ATTRIBUTE_MASK_V1;
    }

    let mut partitions = Vec::with_capacity(header.partitions.num_entries as usize);
    let mut cursor = header.partitions.offset as usize;
    for _ in 0..header.partitions.num_entries {
        let part = LpMetadataPartition::from_bytes(&table_buf[cursor..])?;
        cursor += header.partitions.entry_size as usize;

        if part.attributes & !valid_attributes != 0 {
            return Err(LpError::InvalidMetadata(
                "Logical partition has invalid attribute set.".into(),
            ));
        }
        let Some(end_extent) = part.first_extent_index.checked_add(part.num_extents) else {
            return Err(LpError::InvalidMetadata(
                "Logical partition first_extent_index + num_extents overflowed.".into(),
            ));
        };
        if end_extent > header.extents.num_entries {
            return Err(LpError::InvalidMetadata(
                "Logical partition has invalid extent list.".into(),
            ));
        }
        if part.group_index >= header.groups.num_entries {
            return Err(LpError::InvalidMetadata(
                "Logical partition has invalid group index.".into(),
            ));
        }
        partitions.push(part);
    }

    let mut extents = Vec::with_capacity(header.extents.num_entries as usize);
    cursor = header.extents.offset as usize;
    for _ in 0..header.extents.num_entries {
        let extent = LpMetadataExtent::from_bytes(&table_buf[cursor..])?;
        cursor += header.extents.entry_size as usize;

        if extent.target_type == LP_TARGET_TYPE_LINEAR
            && extent.target_source >= header.block_devices.num_entries
        {
            return Err(LpError::InvalidMetadata(
                "Logical partition extent has invalid block device.".into(),
            ));
        }
        extents.push(extent);
    }

    let mut groups = Vec::with_capacity(header.groups.num_entries as usize);
    cursor = header.groups.offset as usize;
    for _ in 0..header.groups.num_entries {
        let group = LpMetadataPartitionGroup::from_bytes(&table_buf[cursor..])?;
        cursor += header.groups.entry_size as usize;
        groups.push(group);
    }

    let mut block_devices = Vec::with_capacity(header.block_devices.num_entries as usize);
    cursor = header.block_devices.offset as usize;
    for _ in 0..header.block_devices.num_entries {
        let device = LpMetadataBlockDevice::from_bytes(&table_buf[cursor..])?;
        cursor += header.block_devices.entry_size as usize;
        block_devices.push(device);
    }

    let super_device = block_devices.first().ok_or_else(|| {
        LpError::InvalidMetadata("Metadata does not specify a super device.".into())
    })?;

    let metadata_region =
        get_total_metadata_size(geometry.metadata_max_size, geometry.metadata_slot_count);
    if metadata_region > super_device.first_logical_sector * LP_SECTOR_SIZE {
        return Err(LpError::InvalidMetadata(
            "Logical partition metadata overlaps with logical partition contents.".into(),
        ));
    }

    Ok(LpMetadata {
        geometry,
        header,
        partitions,
        extents,
        groups,
        block_devices,
    })
}

pub fn parse_metadata_slice(
    geometry: LpMetadataGeometry,
    buffer: &[u8],
) -> Result<LpMetadata, LpError> {
    let mut cursor = Cursor::new(buffer);
    parse_metadata(geometry, &mut cursor)
}

pub fn read_primary_metadata<R: Read + Seek>(
    reader: &mut R,
    geometry: &LpMetadataGeometry,
    slot_number: u32,
) -> Result<LpMetadata, LpError> {
    let offset = get_primary_metadata_offset(geometry, slot_number);
    reader.seek(SeekFrom::Start(offset))?;
    parse_metadata(*geometry, reader)
}

pub fn read_backup_metadata<R: Read + Seek>(
    reader: &mut R,
    geometry: &LpMetadataGeometry,
    slot_number: u32,
) -> Result<LpMetadata, LpError> {
    let offset = get_backup_metadata_offset(geometry, slot_number);
    reader.seek(SeekFrom::Start(offset))?;
    parse_metadata(*geometry, reader)
}

fn adjust_metadata_for_slot(metadata: &mut LpMetadata, slot_number: u32) -> Result<(), LpError> {
    let slot_suffix = slot_suffix_for_slot_number(slot_number)?;

    for partition in &mut metadata.partitions {
        if partition.attributes & LP_PARTITION_ATTR_SLOT_SUFFIXED == 0 {
            continue;
        }
        let new_name = format!("{}{}", partition.name(), slot_suffix);
        if !partition.set_name(&new_name) {
            return Err(LpError::InvalidMetadata(format!(
                "Partition name too long: {new_name}"
            )));
        }
        partition.attributes &= !LP_PARTITION_ATTR_SLOT_SUFFIXED;
    }

    for block_device in &mut metadata.block_devices {
        if block_device.flags & LP_BLOCK_DEVICE_SLOT_SUFFIXED == 0 {
            continue;
        }
        let new_name = format!("{}{}", block_device.partition_name(), slot_suffix);
        if !block_device.set_partition_name(&new_name) {
            return Err(LpError::InvalidMetadata(format!(
                "Block device partition name too long: {new_name}"
            )));
        }
        block_device.flags &= !LP_BLOCK_DEVICE_SLOT_SUFFIXED;
    }

    for group in &mut metadata.groups {
        if group.flags & LP_GROUP_SLOT_SUFFIXED == 0 {
            continue;
        }
        let new_name = format!("{}{}", group.name(), slot_suffix);
        if !group.set_name(&new_name) {
            return Err(LpError::InvalidMetadata(format!(
                "Group name too long: {new_name}"
            )));
        }
        group.flags &= !LP_GROUP_SLOT_SUFFIXED;
    }

    Ok(())
}

/// Read logical partition metadata from a super image stream, falling back to
/// backup geometry and backup metadata copies if primary copies are corrupted.
pub fn read_metadata<R: Read + Seek>(
    reader: &mut R,
    slot_number: u32,
) -> Result<LpMetadata, LpError> {
    let geometry = read_logical_partition_geometry(reader)?;
    if slot_number >= geometry.metadata_slot_count {
        return Err(LpError::InvalidMetadata(format!(
            "Invalid metadata slot number: {slot_number} (slot count is {})",
            geometry.metadata_slot_count
        )));
    }

    let primary_offset = get_primary_metadata_offset(&geometry, slot_number);
    let backup_offset = get_backup_metadata_offset(&geometry, slot_number);

    let mut metadata = match reader
        .seek(SeekFrom::Start(primary_offset))
        .map_err(LpError::from)
        .and_then(|_| parse_metadata(geometry, reader))
    {
        Ok(m) => m,
        Err(primary_err) => reader
            .seek(SeekFrom::Start(backup_offset))
            .map_err(LpError::from)
            .and_then(|_| parse_metadata(geometry, reader))
            .map_err(|_| primary_err)?,
    };

    adjust_metadata_for_slot(&mut metadata, slot_number)?;
    Ok(metadata)
}

/// Returns true if the stream begins with geometry magic at offset 0 (an empty super image).
pub fn is_empty_super_image<R: Read + Seek>(reader: &mut R) -> Result<bool, LpError> {
    let saved = reader.stream_position()?;
    let size = reader.seek(SeekFrom::End(0))?;
    if size < LP_METADATA_GEOMETRY_SIZE as u64 {
        reader.seek(SeekFrom::Start(saved))?;
        return Ok(false);
    }
    reader.seek(SeekFrom::Start(0))?;
    let mut magic = [0u8; 4];
    let res = reader.read_exact(&mut magic);
    reader.seek(SeekFrom::Start(saved))?;
    match res {
        Ok(()) => Ok(u32::from_le_bytes(magic) == LP_METADATA_GEOMETRY_MAGIC),
        Err(_) => Ok(false),
    }
}

pub fn read_from_image_reader<R: Read + Seek>(reader: &mut R) -> Result<LpMetadata, LpError> {
    reader.seek(SeekFrom::Start(0))?;
    let mut geom_buf = vec![0u8; LP_METADATA_GEOMETRY_SIZE];
    reader.read_exact(&mut geom_buf)?;
    let geometry = parse_geometry(&geom_buf)?;
    parse_metadata(geometry, reader)
}

pub fn read_from_image_blob(data: &[u8]) -> Result<LpMetadata, LpError> {
    if data.len() < LP_METADATA_GEOMETRY_SIZE {
        return Err(LpError::InvalidMetadata(format!(
            "{} is smaller than geometry header",
            data.len()
        )));
    }
    let geometry = parse_geometry(&data[..LP_METADATA_GEOMETRY_SIZE])?;
    parse_metadata_slice(geometry, &data[LP_METADATA_GEOMETRY_SIZE..])
}

/// Automatically detect whether the stream is an empty super image or a full super image
/// and parse its metadata.
pub fn read_metadata_auto<R: Read + Seek>(
    reader: &mut R,
    slot_number: u32,
) -> Result<(LpMetadata, bool), LpError> {
    if is_empty_super_image(reader)? {
        let mut metadata = read_from_image_reader(reader)?;
        if slot_number >= metadata.geometry.metadata_slot_count {
            return Err(LpError::InvalidMetadata(format!(
                "Invalid metadata slot number: {slot_number}"
            )));
        }
        adjust_metadata_for_slot(&mut metadata, slot_number)?;
        Ok((metadata, true))
    } else {
        let metadata = read_metadata(reader, slot_number)?;
        Ok((metadata, false))
    }
}
