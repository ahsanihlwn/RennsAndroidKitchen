//! Text and JSON metadata formatters for super partition inspection.

use std::fmt::Write as FmtWrite;

use super::format::{
    LpMetadata, LP_BLOCK_DEVICE_SLOT_SUFFIXED, LP_GROUP_SLOT_SUFFIXED,
    LP_HEADER_FLAG_VIRTUAL_AB_DEVICE, LP_PARTITION_ATTR_DISABLED, LP_PARTITION_ATTR_READONLY,
    LP_PARTITION_ATTR_SLOT_SUFFIXED, LP_PARTITION_ATTR_UPDATED, LP_TARGET_TYPE_LINEAR,
    LP_TARGET_TYPE_ZERO,
};

pub fn build_header_flag_string(mut flags: u32) -> String {
    let mut parts = Vec::new();
    if flags & LP_HEADER_FLAG_VIRTUAL_AB_DEVICE != 0 {
        parts.push("virtual_ab_device".to_string());
        flags &= !LP_HEADER_FLAG_VIRTUAL_AB_DEVICE;
    }
    for bit in 0..32 {
        if flags & (1u32 << bit) != 0 {
            parts.push(format!("unknown_flag_bit_{bit}"));
        }
    }
    if parts.is_empty() {
        "none".to_string()
    } else {
        parts.join(",")
    }
}

pub fn build_attribute_string(attrs: u32) -> String {
    let mut parts = Vec::new();
    if attrs & LP_PARTITION_ATTR_READONLY != 0 {
        parts.push("readonly");
    }
    if attrs & LP_PARTITION_ATTR_SLOT_SUFFIXED != 0 {
        parts.push("slot-suffixed");
    }
    if attrs & LP_PARTITION_ATTR_UPDATED != 0 {
        parts.push("updated");
    }
    if attrs & LP_PARTITION_ATTR_DISABLED != 0 {
        parts.push("disabled");
    }
    if parts.is_empty() {
        "none".to_string()
    } else {
        parts.join(",")
    }
}

pub fn build_block_device_flag_string(flags: u32) -> &'static str {
    if flags & LP_BLOCK_DEVICE_SLOT_SUFFIXED != 0 {
        "slot-suffixed"
    } else {
        "none"
    }
}

pub fn build_group_flag_string(flags: u32) -> &'static str {
    if flags & LP_GROUP_SLOT_SUFFIXED != 0 {
        "slot-suffixed"
    } else {
        "none"
    }
}

/// Format metadata as human-readable text.
pub fn format_metadata_text(metadata: &LpMetadata, slot: u32) -> String {
    let mut out = String::new();
    let _ = writeln!(out, "Slot {slot}:");
    let _ = writeln!(
        out,
        "Metadata version: {}.{}",
        metadata.header.major_version, metadata.header.minor_version
    );
    let _ = writeln!(
        out,
        "Metadata size: {} bytes",
        metadata.header.header_size + metadata.header.tables_size
    );
    let _ = writeln!(
        out,
        "Metadata max size: {} bytes",
        metadata.geometry.metadata_max_size
    );
    let _ = writeln!(
        out,
        "Metadata slot count: {}",
        metadata.geometry.metadata_slot_count
    );
    let _ = writeln!(
        out,
        "Header flags: {}",
        build_header_flag_string(metadata.header.flags)
    );

    let _ = writeln!(out, "Partition table:");
    let _ = writeln!(out, "------------------------");

    let mut layout_entries: Vec<(String, u64, String, u64)> = Vec::new();

    for partition in &metadata.partitions {
        let name = partition.name();
        let group_name = metadata
            .groups
            .get(partition.group_index as usize)
            .map(|g| g.name())
            .unwrap_or_default();
        let _ = writeln!(out, "  Name: {name}");
        let _ = writeln!(out, "  Group: {group_name}");
        let _ = writeln!(
            out,
            "  Attributes: {}",
            build_attribute_string(partition.attributes)
        );
        let _ = writeln!(out, "  Extents:");

        let mut first_sector: u64 = 0;
        for i in 0..partition.num_extents {
            let idx = (partition.first_extent_index + i) as usize;
            let extent = &metadata.extents[idx];
            let end_sector = first_sector + extent.num_sectors.saturating_sub(1);
            if extent.target_type == LP_TARGET_TYPE_LINEAR {
                let dev_name = metadata
                    .block_devices
                    .get(extent.target_source as usize)
                    .map(|d| d.partition_name())
                    .unwrap_or_default();
                let _ = writeln!(
                    out,
                    "    {first_sector} .. {end_sector} linear {dev_name} {}",
                    extent.target_data
                );
                layout_entries.push((
                    dev_name,
                    extent.target_data,
                    name.clone(),
                    extent.num_sectors,
                ));
            } else if extent.target_type == LP_TARGET_TYPE_ZERO {
                let _ = writeln!(out, "    {first_sector} .. {end_sector} zero");
            }
            first_sector += extent.num_sectors;
        }
        let _ = writeln!(out, "------------------------");
    }

    layout_entries.sort_by(|a, b| (&a.0, a.1).cmp(&(&b.0, b.1)));

    let _ = writeln!(out, "Super partition layout:");
    let _ = writeln!(out, "------------------------");
    for (dev_name, offset, part_name, num_sectors) in layout_entries {
        let _ = writeln!(
            out,
            "{dev_name}: {offset} .. {}: {part_name} ({num_sectors} sectors)",
            offset + num_sectors
        );
    }
    let _ = writeln!(out, "------------------------");

    let _ = writeln!(out, "Block device table:");
    let _ = writeln!(out, "------------------------");
    for dev in &metadata.block_devices {
        let _ = writeln!(out, "  Partition name: {}", dev.partition_name());
        let _ = writeln!(out, "  First sector: {}", dev.first_logical_sector);
        let _ = writeln!(out, "  Size: {} bytes", dev.size);
        let _ = writeln!(
            out,
            "  Flags: {}",
            build_block_device_flag_string(dev.flags)
        );
        let _ = writeln!(out, "------------------------");
    }

    let _ = writeln!(out, "Group table:");
    let _ = writeln!(out, "------------------------");
    for group in &metadata.groups {
        let _ = writeln!(out, "  Name: {}", group.name());
        let _ = writeln!(out, "  Maximum size: {} bytes", group.maximum_size);
        let _ = writeln!(out, "  Flags: {}", build_group_flag_string(group.flags));
        let _ = writeln!(out, "------------------------");
    }

    out
}

fn json_escape(s: &str) -> String {
    let mut out = String::with_capacity(s.len() + 2);
    out.push('"');
    for ch in s.chars() {
        match ch {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            c if (c as u32) < 0x20 => {
                let _ = write!(out, "\\u{:04x}", c as u32);
            }
            c => out.push(c),
        }
    }
    out.push('"');
    out
}

/// Format metadata as JSON.
pub fn format_metadata_json(metadata: &LpMetadata) -> String {
    let mut out = String::new();
    out.push_str("{\n");

    // partitions
    out.push_str(" \"partitions\": [");
    if !metadata.partitions.is_empty() {
        out.push('\n');
    }
    for (idx, part) in metadata.partitions.iter().enumerate() {
        let group_name = metadata
            .groups
            .get(part.group_index as usize)
            .map(|g| g.name())
            .unwrap_or_default();
        let size = metadata.partition_size(part);
        out.push_str("  {\n");
        let _ = writeln!(out, "   \"name\": {},", json_escape(&part.name()));
        let _ = writeln!(out, "   \"group_name\": {},", json_escape(&group_name));
        if size != 0 {
            out.push_str("   \"is_dynamic\": true,\n");
            let _ = writeln!(out, "   \"size\": \"{size}\"");
        } else {
            out.push_str("   \"is_dynamic\": true\n");
        }
        out.push_str("  }");
        if idx + 1 < metadata.partitions.len() {
            out.push(',');
        }
        out.push('\n');
    }
    out.push_str(" ],\n");

    // groups
    out.push_str(" \"groups\": [");
    if !metadata.groups.is_empty() {
        out.push('\n');
    }
    for (idx, group) in metadata.groups.iter().enumerate() {
        out.push_str("  {\n");
        if group.maximum_size != 0 {
            let _ = writeln!(out, "   \"name\": {},", json_escape(&group.name()));
            let _ = writeln!(out, "   \"maximum_size\": \"{}\"", group.maximum_size);
        } else {
            let _ = writeln!(out, "   \"name\": {}", json_escape(&group.name()));
        }
        out.push_str("  }");
        if idx + 1 < metadata.groups.len() {
            out.push(',');
        }
        out.push('\n');
    }
    out.push_str(" ],\n");

    // block_devices
    out.push_str(" \"block_devices\": [");
    if !metadata.block_devices.is_empty() {
        out.push('\n');
    }
    for (idx, dev) in metadata.block_devices.iter().enumerate() {
        let mut fields: Vec<String> = Vec::new();
        fields.push(format!("   \"name\": {}", json_escape(&dev.partition_name())));
        if dev.size != 0 {
            fields.push(format!("   \"size\": \"{}\"", dev.size));
        }
        if metadata.geometry.logical_block_size != 0 {
            fields.push(format!(
                "   \"block_size\": \"{}\"",
                metadata.geometry.logical_block_size
            ));
        }
        if dev.alignment != 0 {
            fields.push(format!("   \"alignment\": \"{}\"", dev.alignment));
        }
        if dev.alignment_offset != 0 {
            fields.push(format!(
                "   \"alignment_offset\": \"{}\"",
                dev.alignment_offset
            ));
        }
        out.push_str("  {\n");
        out.push_str(&fields.join(",\n"));
        out.push_str("\n  }");
        if idx + 1 < metadata.block_devices.len() {
            out.push(',');
        }
        out.push('\n');
    }
    out.push_str(" ]\n");

    out.push('}');
    out
}
