//! lpmake - command-line tool for creating Android Logical Partition images.

use std::collections::BTreeMap;
use std::env;
use std::path::PathBuf;
use std::process;

use lp_tools::lp::{
    write_empty_image_file, write_split_image_files, write_to_image_file, BlockDeviceInfo,
    MetadataBuilder, DEFAULT_BLOCK_SIZE, DEFAULT_GROUP, DEFAULT_PARTITION_ALIGNMENT,
    LP_METADATA_DEFAULT_PARTITION_NAME, LP_PARTITION_ATTR_NONE, LP_PARTITION_ATTR_READONLY,
};

fn print_usage(prog: &str) {
    eprintln!(
        "{prog} - command-line tool for creating Android Logical Partition images.\n\
         \n\
         Usage:\n\
         \x20 {prog} [options]\n\
         \n\
         Required options:\n\
         \x20 -d,--device-size=SIZE         Size of the block device for logical partitions.\n\
         \x20 -m,--metadata-size=SIZE       Maximum size to reserve for partition metadata.\n\
         \x20 -s,--metadata-slots=COUNT     Number of slots to store metadata copies.\n\
         \x20 -p,--partition=DATA           Add a partition given the data, see below.\n\
         \x20 -o,--output=FILE              Output file.\n\
         \n\
         Optional:\n\
         \x20 -b,--block-size=SIZE          Physical block size, defaults to 4096.\n\
         \x20 -a,--alignment=N              Optimal partition alignment in bytes.\n\
         \x20 -O,--alignment-offset=N       Alignment offset in bytes to device parent.\n\
         \x20 -S,--sparse                   Output a sparse image for fastboot.\n\
         \x20 -i,--image=PARTITION=FILE     If building a sparse/full image, include the given\n\
         \x20                               file (or sparse file) as initial data for the\n\
         \x20                               named partition.\n\
         \x20 -g,--group=GROUP:SIZE         Define a named partition group with the given\n\
         \x20                               maximum size.\n\
         \x20 -D,--device=DATA              Add a block device that the super partition spans\n\
         \x20                               over. If specified, then -d/--device-size and\n\
         \x20                               alignments must not be specified.\n\
         \x20 -n,--super-name=NAME          Specify the name of the block device that will\n\
         \x20                               house the super partition.\n\
         \x20 -x,--auto-slot-suffixing      Mark the block device and partition names needing\n\
         \x20                               slot suffixes before being used.\n\
         \x20 -F,--force-full-image         Force a full image to be written even if no\n\
         \x20                               partition images were specified.\n\
         \x20 --virtual-ab                  Add the VIRTUAL_AB_DEVICE flag to the metadata\n\
         \x20                               header (requires v1.2+ metadata parser).\n\
         \x20 -h,--help                     Print this help message and exit.\n\
         \n\
         Partition data format:\n\
         \x20 <name>:<attributes>:<size>[:group]\n\
         \x20 Attrs must be 'none' or 'readonly'.\n\
         \n\
         Device data format:\n\
         \x20 <partition_name>:<size>[:<alignment>:<alignment_offset>]"
    );
}

struct CliArgs {
    blockdevice_size: u64,
    metadata_size: u32,
    metadata_slots: u32,
    alignment_offset: u32,
    alignment: u32,
    block_size: u32,
    super_name: String,
    output_path: PathBuf,
    partitions: Vec<String>,
    groups: Vec<String>,
    block_devices: Vec<BlockDeviceInfo>,
    images: BTreeMap<String, PathBuf>,
    output_sparse: bool,
    has_implied_super: bool,
    auto_slot_suffixing: bool,
    force_full_image: bool,
    virtual_ab: bool,
}

fn next_arg(raw_args: &[String], i: &mut usize, inline: Option<&str>, flag: &str) -> Result<String, (String, i32)> {
    if let Some(v) = inline {
        return Ok(v.to_string());
    }
    *i += 1;
    raw_args
        .get(*i)
        .cloned()
        .ok_or_else(|| (format!("{flag} requires an argument."), 64))
}

fn parse_device_spec(spec: &str) -> Result<BlockDeviceInfo, (String, i32)> {
    let parts: Vec<&str> = spec.split(':').collect();
    if parts.len() < 2 || parts.len() > 4 {
        return Err(("Block device info has invalid formatting.".into(), 64));
    }
    let name = parts[0];
    if name.is_empty() {
        return Err(("Block device must have a valid partition name.".into(), 64));
    }
    let size: u64 = parts[1]
        .parse()
        .ok()
        .filter(|&v| v > 0)
        .ok_or_else(|| ("Block device must have a valid size.".into(), 64))?;
    let alignment: u32 = if parts.len() >= 3 {
        parts[2]
            .parse()
            .map_err(|_| ("Block device must have a valid alignment.".into(), 64))?
    } else {
        DEFAULT_PARTITION_ALIGNMENT
    };
    let alignment_offset: u32 = if parts.len() >= 4 {
        parts[3]
            .parse()
            .map_err(|_| ("Block device must have a valid alignment offset.".into(), 64))?
    } else {
        0
    };
    BlockDeviceInfo::new(name, size, alignment, alignment_offset, DEFAULT_BLOCK_SIZE)
        .map_err(|e| (e.to_string(), 64))
}

fn parse_args() -> Result<Option<CliArgs>, (String, i32)> {
    let raw_args: Vec<String> = env::args().collect();
    let prog = raw_args.first().map(|s| s.as_str()).unwrap_or("lpmake");

    if raw_args.len() <= 1 {
        print_usage(prog);
        return Err((String::new(), 64));
    }

    let mut blockdevice_size: u64 = 0;
    let mut metadata_size: u32 = 0;
    let mut metadata_slots: u32 = 0;
    let mut alignment_offset: u32 = 0;
    let mut alignment: u32 = DEFAULT_PARTITION_ALIGNMENT;
    let mut block_size: u32 = DEFAULT_BLOCK_SIZE;
    let mut super_name = LP_METADATA_DEFAULT_PARTITION_NAME.to_string();
    let mut output_path: Option<PathBuf> = None;
    let mut partitions: Vec<String> = Vec::new();
    let mut groups: Vec<String> = Vec::new();
    let mut block_devices: Vec<BlockDeviceInfo> = Vec::new();
    let mut images: BTreeMap<String, PathBuf> = BTreeMap::new();
    let mut output_sparse = false;
    let mut has_implied_super = false;
    let mut auto_slot_suffixing = false;
    let mut force_full_image = false;
    let mut virtual_ab = false;

    let mut i = 1;
    while i < raw_args.len() {
        let arg = &raw_args[i];
        if arg.starts_with("--") {
            let (flag, inline) = match arg.split_once('=') {
                Some((k, v)) => (k, Some(v)),
                None => (arg.as_str(), None),
            };
            match flag {
                "--help" => {
                    print_usage(prog);
                    return Ok(None);
                }
                "--device-size" => {
                    let v = next_arg(&raw_args, &mut i, inline, flag)?;
                    blockdevice_size = v
                        .parse::<u64>()
                        .ok()
                        .filter(|&x| x > 0)
                        .ok_or_else(|| ("Invalid argument to --device-size.".into(), 64))?;
                    has_implied_super = true;
                }
                "--metadata-size" => {
                    let v = next_arg(&raw_args, &mut i, inline, flag)?;
                    metadata_size = v
                        .parse()
                        .map_err(|_| ("Invalid argument to --metadata-size.".into(), 64))?;
                }
                "--metadata-slots" => {
                    let v = next_arg(&raw_args, &mut i, inline, flag)?;
                    metadata_slots = v
                        .parse()
                        .map_err(|_| ("Invalid argument to --metadata-slots.".into(), 64))?;
                }
                "--partition" => {
                    let v = next_arg(&raw_args, &mut i, inline, flag)?;
                    partitions.push(v);
                }
                "--output" => {
                    let v = next_arg(&raw_args, &mut i, inline, flag)?;
                    output_path = Some(PathBuf::from(v));
                }
                "--alignment-offset" => {
                    let v = next_arg(&raw_args, &mut i, inline, flag)?;
                    alignment_offset = v
                        .parse()
                        .map_err(|_| ("Invalid argument to --alignment-offset.".into(), 64))?;
                    has_implied_super = true;
                }
                "--alignment" => {
                    let v = next_arg(&raw_args, &mut i, inline, flag)?;
                    alignment = v
                        .parse()
                        .map_err(|_| ("Invalid argument to --alignment.".into(), 64))?;
                    has_implied_super = true;
                }
                "--sparse" => {
                    if inline.is_some() {
                        return Err(("--sparse does not take a value.".into(), 64));
                    }
                    output_sparse = true;
                }
                "--block-size" => {
                    let v = next_arg(&raw_args, &mut i, inline, flag)?;
                    block_size = v
                        .parse::<u32>()
                        .ok()
                        .filter(|&x| x > 0)
                        .ok_or_else(|| ("Invalid argument to --block-size.".into(), 64))?;
                }
                "--image" => {
                    let v = next_arg(&raw_args, &mut i, inline, flag)?;
                    let (part_name, file) = v
                        .split_once('=')
                        .filter(|(p, f)| !p.is_empty() && !f.is_empty())
                        .ok_or_else(|| ("Expected PARTITION=FILE.".into(), 64))?;
                    images.insert(part_name.to_string(), PathBuf::from(file));
                }
                "--group" => {
                    let v = next_arg(&raw_args, &mut i, inline, flag)?;
                    groups.push(v);
                }
                "--device" => {
                    let v = next_arg(&raw_args, &mut i, inline, flag)?;
                    block_devices.push(parse_device_spec(&v)?);
                }
                "--super-name" => {
                    super_name = next_arg(&raw_args, &mut i, inline, flag)?;
                }
                "--auto-slot-suffixing" => {
                    if inline.is_some() {
                        return Err(("--auto-slot-suffixing does not take a value.".into(), 64));
                    }
                    auto_slot_suffixing = true;
                }
                "--force-full-image" => {
                    if inline.is_some() {
                        return Err(("--force-full-image does not take a value.".into(), 64));
                    }
                    force_full_image = true;
                }
                "--virtual-ab" => {
                    if inline.is_some() {
                        return Err(("--virtual-ab does not take a value.".into(), 64));
                    }
                    virtual_ab = true;
                }
                _ => return Err((format!("Unrecognized option: {arg}"), 64)),
            }
        } else if arg.starts_with('-') && arg.len() > 1 {
            let mut chars = arg[1..].chars();
            while let Some(ch) = chars.next() {
                let take_val = |raw_args: &[String], i: &mut usize, rest: String, flag: char| -> Result<String, (String, i32)> {
                    if !rest.is_empty() {
                        Ok(rest)
                    } else {
                        *i += 1;
                        raw_args
                            .get(*i)
                            .cloned()
                            .ok_or_else(|| (format!("-{flag} requires an argument."), 64))
                    }
                };
                match ch {
                    'h' => {
                        print_usage(prog);
                        return Ok(None);
                    }
                    'S' => output_sparse = true,
                    'x' => auto_slot_suffixing = true,
                    'F' => force_full_image = true,
                    'd' => {
                        let rest: String = chars.collect();
                        let v = take_val(&raw_args, &mut i, rest, ch)?;
                        blockdevice_size = v
                            .parse::<u64>()
                            .ok()
                            .filter(|&x| x > 0)
                            .ok_or_else(|| ("Invalid argument to --device-size.".into(), 64))?;
                        has_implied_super = true;
                        break;
                    }
                    'm' => {
                        let rest: String = chars.collect();
                        let v = take_val(&raw_args, &mut i, rest, ch)?;
                        metadata_size = v
                            .parse()
                            .map_err(|_| ("Invalid argument to --metadata-size.".into(), 64))?;
                        break;
                    }
                    's' => {
                        let rest: String = chars.collect();
                        let v = take_val(&raw_args, &mut i, rest, ch)?;
                        metadata_slots = v
                            .parse()
                            .map_err(|_| ("Invalid argument to --metadata-slots.".into(), 64))?;
                        break;
                    }
                    'p' => {
                        let rest: String = chars.collect();
                        let v = take_val(&raw_args, &mut i, rest, ch)?;
                        partitions.push(v);
                        break;
                    }
                    'o' => {
                        let rest: String = chars.collect();
                        let v = take_val(&raw_args, &mut i, rest, ch)?;
                        output_path = Some(PathBuf::from(v));
                        break;
                    }
                    'O' => {
                        let rest: String = chars.collect();
                        let v = take_val(&raw_args, &mut i, rest, ch)?;
                        alignment_offset = v
                            .parse()
                            .map_err(|_| ("Invalid argument to --alignment-offset.".into(), 64))?;
                        has_implied_super = true;
                        break;
                    }
                    'a' => {
                        let rest: String = chars.collect();
                        let v = take_val(&raw_args, &mut i, rest, ch)?;
                        alignment = v
                            .parse()
                            .map_err(|_| ("Invalid argument to --alignment.".into(), 64))?;
                        has_implied_super = true;
                        break;
                    }
                    'b' => {
                        let rest: String = chars.collect();
                        let v = take_val(&raw_args, &mut i, rest, ch)?;
                        block_size = v
                            .parse::<u32>()
                            .ok()
                            .filter(|&x| x > 0)
                            .ok_or_else(|| ("Invalid argument to --block-size.".into(), 64))?;
                        break;
                    }
                    'i' => {
                        let rest: String = chars.collect();
                        let v = take_val(&raw_args, &mut i, rest, ch)?;
                        let (part_name, file) = v
                            .split_once('=')
                            .filter(|(p, f)| !p.is_empty() && !f.is_empty())
                            .ok_or_else(|| ("Expected PARTITION=FILE.".into(), 64))?;
                        images.insert(part_name.to_string(), PathBuf::from(file));
                        break;
                    }
                    'g' => {
                        let rest: String = chars.collect();
                        let v = take_val(&raw_args, &mut i, rest, ch)?;
                        groups.push(v);
                        break;
                    }
                    'D' => {
                        let rest: String = chars.collect();
                        let v = take_val(&raw_args, &mut i, rest, ch)?;
                        block_devices.push(parse_device_spec(&v)?);
                        break;
                    }
                    'n' => {
                        let rest: String = chars.collect();
                        super_name = take_val(&raw_args, &mut i, rest, ch)?;
                        break;
                    }
                    _ => return Err((format!("Unrecognized option: -{ch}"), 64)),
                }
            }
        } else {
            return Err((format!("Unexpected positional argument: {arg}"), 64));
        }
        i += 1;
    }

    if (has_implied_super && (!block_devices.is_empty() || blockdevice_size == 0))
        || (!has_implied_super && block_devices.is_empty())
        || (block_devices.is_empty() && blockdevice_size == 0)
    {
        return Err(("Must specify --device OR --device-size.".into(), 64));
    }
    if metadata_size == 0 {
        return Err(("--metadata-size must be more than 0 bytes.".into(), 64));
    }
    if metadata_slots == 0 {
        return Err(("--metadata-slots must be more than 0.".into(), 64));
    }
    let output_path = output_path
        .filter(|p| !p.as_os_str().is_empty())
        .ok_or_else(|| ("--output must specify a valid path.".into(), 64))?;
    if partitions.is_empty() {
        return Err(("Partition table must have at least one entry.".into(), 64));
    }

    Ok(Some(CliArgs {
        blockdevice_size,
        metadata_size,
        metadata_slots,
        alignment_offset,
        alignment,
        block_size,
        super_name,
        output_path,
        partitions,
        groups,
        block_devices,
        images,
        output_sparse,
        has_implied_super,
        auto_slot_suffixing,
        force_full_image,
        virtual_ab,
    }))
}

fn run(mut args: CliArgs) -> Result<(), (String, i32)> {
    if args.has_implied_super {
        let dev = BlockDeviceInfo::new(
            &args.super_name,
            args.blockdevice_size,
            args.alignment,
            args.alignment_offset,
            args.block_size,
        )
        .map_err(|e| (e.to_string(), 64))?;
        args.block_devices.push(dev);
    } else {
        for dev in &mut args.block_devices {
            dev.logical_block_size = args.block_size;
        }
    }

    let mut builder = MetadataBuilder::new(
        &args.block_devices,
        &args.super_name,
        args.metadata_size,
        args.metadata_slots,
    )
    .map_err(|e| (format!("Invalid metadata parameters: {e}"), 64))?;

    if args.auto_slot_suffixing {
        builder.set_auto_slot_suffixing();
    }
    if args.virtual_ab {
        builder.set_virtual_ab_device_flag();
    }

    for group_info in &args.groups {
        let parts: Vec<&str> = group_info.split(':').collect();
        if parts.len() != 2 {
            return Err(("Partition group info has invalid formatting.".into(), 64));
        }
        let name = parts[0];
        if name.is_empty() {
            return Err(("Partition group must have a valid name.".into(), 64));
        }
        let max_size: u64 = parts[1]
            .parse()
            .map_err(|_| ("Partition group must have a valid maximum size.".into(), 64))?;
        builder
            .add_group(name, max_size)
            .map_err(|e| (e.to_string(), 70))?;
    }

    for part_info in &args.partitions {
        let parts: Vec<&str> = part_info.split(':').collect();
        if parts.len() < 3 || parts.len() > 4 {
            return Err(("Partition info has invalid formatting.".into(), 64));
        }
        let name = parts[0];
        if name.is_empty() {
            return Err(("Partition must have a valid name.".into(), 64));
        }
        let attr_flags = match parts[1] {
            "readonly" => LP_PARTITION_ATTR_READONLY,
            "none" => LP_PARTITION_ATTR_NONE,
            other => {
                return Err((format!("Attribute not recognized: {other}"), 64));
            }
        };
        let size: u64 = parts[2]
            .parse()
            .map_err(|_| ("Partition must have a valid size.".into(), 64))?;
        let group_name = if parts.len() >= 4 {
            parts[3]
        } else {
            DEFAULT_GROUP
        };

        let part_idx = builder
            .add_partition(name, group_name, attr_flags)
            .map_err(|e| (format!("Could not add partition {name}: {e}"), 70))?;
        builder
            .resize_partition(part_idx, size, &[])
            .map_err(|e| (e.to_string(), 70))?;
    }

    let metadata = builder.export().map_err(|e| (e.to_string(), 70))?;

    if !args.images.is_empty() || args.force_full_image {
        if args.block_devices.len() == 1 {
            write_to_image_file(
                &args.output_path,
                &metadata,
                args.block_size,
                &args.images,
                args.output_sparse,
            )
            .map_err(|e| (e.to_string(), 73))?;
        } else {
            write_split_image_files(
                &args.output_path,
                &metadata,
                args.block_size,
                &args.images,
                args.output_sparse,
            )
            .map_err(|e| (e.to_string(), 73))?;
        }
    } else {
        write_empty_image_file(&args.output_path, &metadata).map_err(|e| (e.to_string(), 73))?;
    }

    Ok(())
}

fn main() {
    match parse_args() {
        Ok(None) => process::exit(0),
        Ok(Some(args)) => {
            if let Err((msg, code)) = run(args) {
                if !msg.is_empty() {
                    eprintln!("{msg}");
                }
                process::exit(code);
            }
        }
        Err((msg, code)) => {
            if !msg.is_empty() {
                eprintln!("{msg}");
            }
            process::exit(code);
        }
    }
}
