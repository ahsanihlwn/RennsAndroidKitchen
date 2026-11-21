//! CLI argument parser, `--help`, `--version`, and `fastboot-info.txt` parser for `fastboot` (MIT).

use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, Ordering};

use android_bootimg::{parse_os_patch_level, parse_os_version, BootImageArgs};
use pt_common::PLATFORM_TOOLS_VERSION_FULL;

use crate::protocol::{parse_size_with_suffix, parse_u64_auto, FastbootError};

static EMBEDDED_MODE: AtomicBool = AtomicBool::new(false);

pub fn set_embedded_mode(enabled: bool) {
    EMBEDDED_MODE.store(enabled, Ordering::Relaxed);
}

pub fn is_embedded_mode() -> bool {
    EMBEDDED_MODE.load(Ordering::Relaxed)
}

/// Action returned after parsing CLI arguments.
#[derive(Debug, Clone)]
#[allow(clippy::large_enum_variant)]
pub enum FastbootCliAction {
    ShowVersion,
    ShowHelp { exit_code: i32 },
    ListDevices { long: bool },
    Execute(FastbootSessionConfig),
}

/// A single fastboot command parsed from the CLI.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum FastbootTask {
    GetVar(String),
    Erase(String),
    Format {
        partition: String,
        fs_type: Option<String>,
        size_override: Option<u64>,
    },
    Flash {
        partition: String,
        filename: Option<PathBuf>,
    },
    FlashRaw {
        partition: String,
        kernel: PathBuf,
        ramdisk: Option<PathBuf>,
        second: Option<PathBuf>,
    },
    Boot {
        kernel: PathBuf,
        ramdisk: Option<PathBuf>,
        second: Option<PathBuf>,
    },
    FlashAll,
    Update(PathBuf),
    Reboot(Option<String>),
    Continue,
    SetActive(String),
    Flashing(String),
    Oem(String),
    Gsi(String),
    WipeSuper(Option<PathBuf>),
    CreateLogicalPartition {
        name: String,
        size: u64,
    },
    DeleteLogicalPartition(String),
    ResizeLogicalPartition {
        name: String,
        size: u64,
    },
    SnapshotUpdate(String),
    Fetch {
        partition: String,
        out_file: PathBuf,
        offset: Option<u64>,
        size: Option<u64>,
    },
    Stage(PathBuf),
    GetStaged(PathBuf),
}

/// Complete session configuration and queued tasks for a `fastboot` run.
#[derive(Debug, Clone, Default)]
pub struct FastbootSessionConfig {
    pub serial: Option<String>,
    pub sparse_limit: Option<u64>,
    pub slot_override: Option<String>,
    pub set_active_slot: Option<Option<String>>,
    pub wipe_userdata: bool,
    pub force: bool,
    pub skip_secondary: bool,
    pub skip_reboot: bool,
    pub disable_verity: bool,
    pub disable_verification: bool,
    pub disable_super_optimization: bool,
    pub exclude_dynamic_partitions: bool,
    pub disable_fastboot_info: bool,
    pub fs_options: Vec<String>,
    pub dtb_path: Option<PathBuf>,
    pub boot_args: BootImageArgs,
    pub verbose: bool,
    pub unbuffered: bool,
    pub tasks: Vec<FastbootTask>,
}

/// Returns the `--version` output matching `platform-tools/fastboot` v37.0.3-16887417.
pub fn version_output() -> String {
    let exe = if is_embedded_mode() {
        "rak_native (embedded platform-tools-rs)".to_string()
    } else {
        std::env::current_exe()
            .map(|p| p.display().to_string())
            .unwrap_or_else(|_| "fastboot".to_string())
    };
    format!("fastboot version {PLATFORM_TOOLS_VERSION_FULL}\nInstalled as {exe}\n")
}

/// Returns the `--help` output matching `platform-tools/fastboot` v37.0.3-16887417.
pub fn help_output() -> &'static str {
    concat!(
        "usage: fastboot [OPTION...] COMMAND...\n",
        "\n",
        "flashing:\n",
        " update ZIP                 Flash all partitions from an update.zip package.\n",
        " flashall                   Flash all partitions from $ANDROID_PRODUCT_OUT.\n",
        "                            On A/B devices, flashed slot is set as active.\n",
        "                            Secondary images may be flashed to inactive slot.\n",
        " flash PARTITION [FILENAME] Flash given partition, using the image from\n",
        "                            $ANDROID_PRODUCT_OUT if no filename is given.\n",
        " flash vendor_boot:RAMDISK [FILENAME]\n",
        "                            Flash vendor_boot ramdisk, fetching the existing\n",
        "                            vendor_boot image and repackaging it with the new\n",
        "                            ramdisk.\n",
        " --dtb DTB                  If set with flash vendor_boot:RAMDISK, then\n",
        "                            update the vendor_boot image with provided DTB.\n",
        "\n",
        "basics:\n",
        " devices [-l]               List devices in bootloader (-l: with device paths).\n",
        " getvar NAME                Display given bootloader variable.\n",
        " reboot [bootloader]        Reboot device.\n",
        "\n",
        "locking/unlocking:\n",
        " flashing lock|unlock       Lock/unlock partitions for flashing\n",
        " flashing lock_critical|unlock_critical\n",
        "                            Lock/unlock 'critical' bootloader partitions.\n",
        " flashing get_unlock_ability\n",
        "                            Check whether unlocking is allowed (1) or not(0).\n",
        "\n",
        "advanced:\n",
        " erase PARTITION            Erase a flash partition.\n",
        " format[:FS_TYPE[:SIZE]] PARTITION\n",
        "                            Format a flash partition.\n",
        " set_active SLOT            Set the active slot.\n",
        " oem [COMMAND...]           Execute OEM-specific command.\n",
        " gsi wipe|disable|status    Wipe, disable or show status of a GSI installation\n",
        "                            (fastbootd only).\n",
        " wipe-super [SUPER_EMPTY]   Wipe the super partition. This will reset it to\n",
        "                            contain an empty set of default dynamic partitions.\n",
        " create-logical-partition NAME SIZE\n",
        "                            Create a logical partition with the given name and\n",
        "                            size, in the super partition.\n",
        " delete-logical-partition NAME\n",
        "                            Delete a logical partition with the given name.\n",
        " resize-logical-partition NAME SIZE\n",
        "                            Change the size of the named logical partition.\n",
        " snapshot-update cancel     On devices that support snapshot-based updates, cancel\n",
        "                            an in-progress update. This may make the device\n",
        "                            unbootable until it is reflashed.\n",
        " snapshot-update merge      On devices that support snapshot-based updates, finish\n",
        "                            an in-progress update if it is in the \"merging\"\n",
        "                            phase.\n",
        " fetch PARTITION OUT_FILE [OFFSET [SIZE]]\n",
        "                            Fetch a partition image from the device, optionally\n",
        "                            specifying a range (offset and size in bytes).\n",
        "boot image:\n",
        " boot KERNEL [RAMDISK [SECOND]]\n",
        "                            Download and boot kernel from RAM.\n",
        " flash:raw PARTITION KERNEL [RAMDISK [SECOND]]\n",
        "                            Create boot image and flash it.\n",
        " --dtb DTB                  Specify path to DTB for boot image header version 2.\n",
        " --cmdline CMDLINE          Override kernel command line.\n",
        " --base ADDRESS             Set kernel base address (default: 0x10000000).\n",
        " --kernel-offset            Set kernel offset (default: 0x00008000).\n",
        " --ramdisk-offset           Set ramdisk offset (default: 0x01000000).\n",
        " --tags-offset              Set tags offset (default: 0x00000100).\n",
        " --dtb-offset               Set dtb offset (default: 0x01100000).\n",
        " --page-size BYTES          Set flash page size (default: 2048).\n",
        " --header-version VERSION   Set boot image header version.\n",
        " --os-version MAJOR[.MINOR[.PATCH]]\n",
        "                            Set boot image OS version (default: 0.0.0).\n",
        " --os-patch-level YYYY-MM-DD\n",
        "                            Set boot image OS security patch level.\n",
        "\n",
        "Android Things:\n",
        " stage IN_FILE              Sends given file to stage for the next command.\n",
        " get_staged OUT_FILE        Writes data staged by the last command to a file.\n",
        "\n",
        "options:\n",
        " -w                         Wipe userdata.\n",
        " -s SERIAL                  Specify a USB device.\n",
        " -s tcp|udp:HOST[:PORT]     Specify a network device.\n",
        " -S SIZE[K|M|G]             Break into sparse files no larger than SIZE.\n",
        " --force                    Force a flash operation that may be unsafe.\n",
        " --slot SLOT                Use SLOT; 'all' for both slots, 'other' for\n",
        "                            non-current slot (default: current active slot).\n",
        " --set-active[=SLOT]        Sets the active slot before rebooting.\n",
        " --skip-secondary           Don't flash secondary slots in flashall/update.\n",
        " --skip-reboot              Don't reboot device after flashing.\n",
        " --disable-verity           Sets disable-verity when flashing vbmeta.\n",
        " --disable-verification     Sets disable-verification when flashing vbmeta.\n",
        " --disable-super-optimization\n",
        "                            Disables optimizations on flashing super partition.\n",
        " --exclude-dynamic-partitions\n",
        "                            Excludes flashing of dynamic partitions.\n",
        " --disable-fastboot-info    Will collects tasks from image list rather than \n",
        "                            $OUT/fastboot-info.txt.\n",
        " --fs-options=OPTION[,OPTION]\n",
        "                            Enable filesystem features. OPTION supports casefold, projid, compress\n",
        " --unbuffered               Don't buffer input or output.\n",
        " --verbose, -v              Verbose output.\n",
        " --version                  Display version.\n",
        " --help, -h                 Show this message.\n",
    )
}

fn is_flag(s: &str) -> bool {
    s.starts_with('-')
}

fn is_known_command(s: &str) -> bool {
    matches!(
        s,
        "devices"
            | "getvar"
            | "erase"
            | "flash"
            | "flash:raw"
            | "boot"
            | "flashall"
            | "update"
            | "reboot"
            | "reboot-bootloader"
            | "reboot-fastboot"
            | "reboot-recovery"
            | "continue"
            | "set_active"
            | "flashing"
            | "oem"
            | "gsi"
            | "wipe-super"
            | "create-logical-partition"
            | "delete-logical-partition"
            | "resize-logical-partition"
            | "snapshot-update"
            | "fetch"
            | "stage"
            | "get_staged"
            | "help"
    ) || s.starts_with("format")
}

/// Parses command-line arguments (`args[1..]`) for `fastboot`.
pub fn parse_cli_args(args: &[String]) -> Result<FastbootCliAction, FastbootError> {
    if args.is_empty() {
        return Err(FastbootError::InvalidArgument(
            "no command specified".into(),
        ));
    }

    let mut cfg = FastbootSessionConfig::default();
    let mut os_ver_bits = 0u32;
    let mut os_patch_bits = 0u32;
    let mut pos: Vec<String> = Vec::new();

    let mut i = 0usize;
    while i < args.len() {
        let arg = &args[i];
        if arg == "--" {
            pos.extend_from_slice(&args[i + 1..]);
            break;
        }
        if arg == "--help" || arg == "-h" || arg == "help" {
            return Ok(FastbootCliAction::ShowHelp { exit_code: 0 });
        }
        if arg == "--version" {
            return Ok(FastbootCliAction::ShowVersion);
        }

        if let Some(long_opt) = arg.strip_prefix("--") {
            let (key, inline_val) = match long_opt.split_once('=') {
                Some((k, v)) => (k, Some(v.to_string())),
                None => (long_opt, None),
            };
            let next_val = |i: &mut usize, inline: Option<String>| -> Result<String, FastbootError> {
                if let Some(v) = inline {
                    Ok(v)
                } else if *i + 1 < args.len() {
                    *i += 1;
                    Ok(args[*i].clone())
                } else {
                    Err(FastbootError::InvalidArgument(format!(
                        "option '--{key}' requires an argument"
                    )))
                }
            };

            match key {
                "force" => cfg.force = true,
                "skip-secondary" => cfg.skip_secondary = true,
                "skip-reboot" => cfg.skip_reboot = true,
                "disable-verity" => cfg.disable_verity = true,
                "disable-verification" => cfg.disable_verification = true,
                "disable-super-optimization" => cfg.disable_super_optimization = true,
                "exclude-dynamic-partitions" => cfg.exclude_dynamic_partitions = true,
                "disable-fastboot-info" => cfg.disable_fastboot_info = true,
                "unbuffered" => cfg.unbuffered = true,
                "verbose" => cfg.verbose = true,
                "set-active" => {
                    cfg.set_active_slot = Some(inline_val);
                }
                "slot" => {
                    cfg.slot_override = Some(next_val(&mut i, inline_val)?);
                }
                "fs-options" => {
                    let v = next_val(&mut i, inline_val)?;
                    for opt in v.split(',').map(str::trim).filter(|s| !s.is_empty()) {
                        cfg.fs_options.push(opt.to_string());
                    }
                }
                "dtb" => {
                    cfg.dtb_path = Some(PathBuf::from(next_val(&mut i, inline_val)?));
                }
                "cmdline" => {
                    cfg.boot_args.cmdline = Some(next_val(&mut i, inline_val)?);
                }
                "base" => {
                    cfg.boot_args.base =
                        parse_u64_auto(&next_val(&mut i, inline_val)?)? as u32;
                }
                "kernel-offset" => {
                    cfg.boot_args.kernel_offset =
                        parse_u64_auto(&next_val(&mut i, inline_val)?)? as u32;
                }
                "ramdisk-offset" => {
                    cfg.boot_args.ramdisk_offset =
                        parse_u64_auto(&next_val(&mut i, inline_val)?)? as u32;
                }
                "second-offset" => {
                    cfg.boot_args.second_offset =
                        parse_u64_auto(&next_val(&mut i, inline_val)?)? as u32;
                }
                "tags-offset" => {
                    cfg.boot_args.tags_offset =
                        parse_u64_auto(&next_val(&mut i, inline_val)?)? as u32;
                }
                "dtb-offset" => {
                    cfg.boot_args.dtb_offset =
                        parse_u64_auto(&next_val(&mut i, inline_val)?)?;
                }
                "page-size" => {
                    cfg.boot_args.page_size =
                        parse_u64_auto(&next_val(&mut i, inline_val)?)? as u32;
                }
                "header-version" => {
                    cfg.boot_args.header_version =
                        parse_u64_auto(&next_val(&mut i, inline_val)?)? as u32;
                }
                "os-version" => {
                    os_ver_bits = parse_os_version(&next_val(&mut i, inline_val)?)?;
                }
                "os-patch-level" => {
                    os_patch_bits = parse_os_patch_level(&next_val(&mut i, inline_val)?)?;
                }
                other => {
                    return Err(FastbootError::InvalidArgument(format!(
                        "unrecognized option '--{other}'"
                    )));
                }
            }
            i += 1;
            continue;
        }

        if arg.starts_with('-') && arg.len() > 1 {
            // Check `-l` when following `devices`
            if arg == "-l" && pos.first().map(String::as_str) == Some("devices") {
                pos.push(arg.clone());
                i += 1;
                continue;
            }
            let chars: Vec<char> = arg[1..].chars().collect();
            let mut ci = 0usize;
            while ci < chars.len() {
                let c = chars[ci];
                let take_short_val = |ci: &mut usize, i: &mut usize| -> Result<String, FastbootError> {
                    if *ci + 1 < chars.len() {
                        let s: String = chars[*ci + 1..].iter().collect();
                        *ci = chars.len();
                        Ok(s)
                    } else if *i + 1 < args.len() {
                        *i += 1;
                        *ci = chars.len();
                        Ok(args[*i].clone())
                    } else {
                        Err(FastbootError::InvalidArgument(format!(
                            "option '-{c}' requires an argument"
                        )))
                    }
                };
                match c {
                    'w' => cfg.wipe_userdata = true,
                    'v' => cfg.verbose = true,
                    's' => {
                        cfg.serial = Some(take_short_val(&mut ci, &mut i)?);
                    }
                    'S' => {
                        let v = take_short_val(&mut ci, &mut i)?;
                        let sz = parse_size_with_suffix(&v)?;
                        cfg.sparse_limit = if sz > 0 { Some(sz) } else { None };
                    }
                    other => {
                        return Err(FastbootError::InvalidArgument(format!(
                            "unknown option -- {other}"
                        )));
                    }
                }
                ci += 1;
            }
            i += 1;
            continue;
        }

        pos.push(arg.clone());
        i += 1;
    }

    cfg.boot_args.os_version = (os_ver_bits << 11) | os_patch_bits;

    // Parse positional command tokens into FastbootTasks
    let mut pi = 0usize;
    while pi < pos.len() {
        let cmd = pos[pi].as_str();
        match cmd {
            "devices" => {
                let long = pos.get(pi + 1).map(String::as_str) == Some("-l");
                return Ok(FastbootCliAction::ListDevices { long });
            }
            "getvar" => {
                let name = pos.get(pi + 1).ok_or_else(|| {
                    FastbootError::InvalidArgument("getvar requires variable name".into())
                })?;
                cfg.tasks.push(FastbootTask::GetVar(name.clone()));
                pi += 2;
            }
            "erase" => {
                let part = pos.get(pi + 1).ok_or_else(|| {
                    FastbootError::InvalidArgument("erase requires partition".into())
                })?;
                cfg.tasks.push(FastbootTask::Erase(part.clone()));
                pi += 2;
            }
            s if s == "format" || s.starts_with("format:") => {
                let part = pos.get(pi + 1).ok_or_else(|| {
                    FastbootError::InvalidArgument("format requires partition".into())
                })?;
                let mut sp = s.split(':');
                let _ = sp.next(); // "format"
                let fs_type = sp.next().filter(|x| !x.is_empty()).map(str::to_string);
                let size_override = match sp.next().filter(|x| !x.is_empty()) {
                    Some(sz_str) => Some(parse_u64_auto(sz_str)?),
                    None => None,
                };
                cfg.tasks.push(FastbootTask::Format {
                    partition: part.clone(),
                    fs_type,
                    size_override,
                });
                pi += 2;
            }
            "flash" => {
                let part = pos.get(pi + 1).ok_or_else(|| {
                    FastbootError::InvalidArgument("flash requires partition name".into())
                })?;
                let filename = if let Some(next_tok) = pos.get(pi + 2) {
                    if !is_known_command(next_tok) && !is_flag(next_tok) {
                        pi += 3;
                        Some(PathBuf::from(next_tok))
                    } else {
                        pi += 2;
                        None
                    }
                } else {
                    pi += 2;
                    None
                };
                cfg.tasks.push(FastbootTask::Flash {
                    partition: part.clone(),
                    filename,
                });
            }
            "flash:raw" => {
                let part = pos.get(pi + 1).ok_or_else(|| {
                    FastbootError::InvalidArgument("flash:raw requires partition".into())
                })?;
                let kernel = pos.get(pi + 2).ok_or_else(|| {
                    FastbootError::InvalidArgument("flash:raw requires kernel path".into())
                })?;
                pi += 3;
                let ramdisk = if let Some(tok) = pos.get(pi) {
                    if !is_known_command(tok) {
                        pi += 1;
                        Some(PathBuf::from(tok))
                    } else {
                        None
                    }
                } else {
                    None
                };
                let second = if ramdisk.is_some() {
                    if let Some(tok) = pos.get(pi) {
                        if !is_known_command(tok) {
                            pi += 1;
                            Some(PathBuf::from(tok))
                        } else {
                            None
                        }
                    } else {
                        None
                    }
                } else {
                    None
                };
                cfg.tasks.push(FastbootTask::FlashRaw {
                    partition: part.clone(),
                    kernel: PathBuf::from(kernel),
                    ramdisk,
                    second,
                });
            }
            "boot" => {
                let kernel = pos.get(pi + 1).ok_or_else(|| {
                    FastbootError::InvalidArgument("boot requires kernel image".into())
                })?;
                pi += 2;
                let ramdisk = if let Some(tok) = pos.get(pi) {
                    if !is_known_command(tok) {
                        pi += 1;
                        Some(PathBuf::from(tok))
                    } else {
                        None
                    }
                } else {
                    None
                };
                let second = if ramdisk.is_some() {
                    if let Some(tok) = pos.get(pi) {
                        if !is_known_command(tok) {
                            pi += 1;
                            Some(PathBuf::from(tok))
                        } else {
                            None
                        }
                    } else {
                        None
                    }
                } else {
                    None
                };
                cfg.tasks.push(FastbootTask::Boot {
                    kernel: PathBuf::from(kernel),
                    ramdisk,
                    second,
                });
            }
            "flashall" => {
                cfg.tasks.push(FastbootTask::FlashAll);
                pi += 1;
            }
            "update" => {
                let zip_path = pos
                    .get(pi + 1)
                    .map(PathBuf::from)
                    .unwrap_or_else(|| PathBuf::from("update.zip"));
                cfg.tasks.push(FastbootTask::Update(zip_path));
                pi += if pos.get(pi + 1).is_some() { 2 } else { 1 };
            }
            "reboot" => {
                let target = if let Some(tok) = pos.get(pi + 1) {
                    if matches!(
                        tok.as_str(),
                        "bootloader" | "recovery" | "fastboot" | "emergency"
                    ) {
                        pi += 2;
                        Some(tok.clone())
                    } else {
                        pi += 1;
                        None
                    }
                } else {
                    pi += 1;
                    None
                };
                cfg.tasks.push(FastbootTask::Reboot(target));
            }
            "reboot-bootloader" => {
                cfg.tasks
                    .push(FastbootTask::Reboot(Some("bootloader".into())));
                pi += 1;
            }
            "reboot-fastboot" => {
                cfg.tasks
                    .push(FastbootTask::Reboot(Some("fastboot".into())));
                pi += 1;
            }
            "reboot-recovery" => {
                cfg.tasks
                    .push(FastbootTask::Reboot(Some("recovery".into())));
                pi += 1;
            }
            "continue" => {
                cfg.tasks.push(FastbootTask::Continue);
                pi += 1;
            }
            "set_active" => {
                let slot = pos.get(pi + 1).ok_or_else(|| {
                    FastbootError::InvalidArgument("set_active requires slot".into())
                })?;
                cfg.tasks.push(FastbootTask::SetActive(slot.clone()));
                pi += 2;
            }
            "flashing" => {
                let sub = pos.get(pi + 1).ok_or_else(|| {
                    FastbootError::InvalidArgument("flashing requires subcommand".into())
                })?;
                cfg.tasks.push(FastbootTask::Flashing(sub.clone()));
                pi += 2;
            }
            "oem" => {
                if pi + 1 >= pos.len() {
                    return Err(FastbootError::InvalidArgument(
                        "empty oem command".into(),
                    ));
                }
                let joined = pos[pi + 1..].join(" ");
                cfg.tasks.push(FastbootTask::Oem(joined));
                break;
            }
            "gsi" => {
                let sub = pos.get(pi + 1).ok_or_else(|| {
                    FastbootError::InvalidArgument("gsi requires wipe|disable|status".into())
                })?;
                cfg.tasks.push(FastbootTask::Gsi(sub.clone()));
                pi += 2;
            }
            "wipe-super" => {
                let img = if let Some(tok) = pos.get(pi + 1) {
                    if !is_known_command(tok) {
                        pi += 2;
                        Some(PathBuf::from(tok))
                    } else {
                        pi += 1;
                        None
                    }
                } else {
                    pi += 1;
                    None
                };
                cfg.tasks.push(FastbootTask::WipeSuper(img));
            }
            "create-logical-partition" => {
                let name = pos.get(pi + 1).ok_or_else(|| {
                    FastbootError::InvalidArgument("missing partition name".into())
                })?;
                let sz_str = pos.get(pi + 2).ok_or_else(|| {
                    FastbootError::InvalidArgument("missing partition size".into())
                })?;
                let size = parse_u64_auto(sz_str)?;
                cfg.tasks.push(FastbootTask::CreateLogicalPartition {
                    name: name.clone(),
                    size,
                });
                pi += 3;
            }
            "delete-logical-partition" => {
                let name = pos.get(pi + 1).ok_or_else(|| {
                    FastbootError::InvalidArgument("missing partition name".into())
                })?;
                cfg.tasks
                    .push(FastbootTask::DeleteLogicalPartition(name.clone()));
                pi += 2;
            }
            "resize-logical-partition" => {
                let name = pos.get(pi + 1).ok_or_else(|| {
                    FastbootError::InvalidArgument("missing partition name".into())
                })?;
                let sz_str = pos.get(pi + 2).ok_or_else(|| {
                    FastbootError::InvalidArgument("missing partition size".into())
                })?;
                let size = parse_u64_auto(sz_str)?;
                cfg.tasks.push(FastbootTask::ResizeLogicalPartition {
                    name: name.clone(),
                    size,
                });
                pi += 3;
            }
            "snapshot-update" => {
                let sub = pos.get(pi + 1).ok_or_else(|| {
                    FastbootError::InvalidArgument("snapshot-update requires cancel|merge".into())
                })?;
                cfg.tasks.push(FastbootTask::SnapshotUpdate(sub.clone()));
                pi += 2;
            }
            "fetch" => {
                let part = pos.get(pi + 1).ok_or_else(|| {
                    FastbootError::InvalidArgument("fetch requires partition".into())
                })?;
                let out_file = pos.get(pi + 2).ok_or_else(|| {
                    FastbootError::InvalidArgument("fetch requires output file".into())
                })?;
                pi += 3;
                let offset = if let Some(tok) = pos.get(pi) {
                    if !is_known_command(tok) {
                        pi += 1;
                        Some(parse_u64_auto(tok)?)
                    } else {
                        None
                    }
                } else {
                    None
                };
                let size = if offset.is_some() {
                    if let Some(tok) = pos.get(pi) {
                        if !is_known_command(tok) {
                            pi += 1;
                            Some(parse_u64_auto(tok)?)
                        } else {
                            None
                        }
                    } else {
                        None
                    }
                } else {
                    None
                };
                cfg.tasks.push(FastbootTask::Fetch {
                    partition: part.clone(),
                    out_file: PathBuf::from(out_file),
                    offset,
                    size,
                });
            }
            "stage" => {
                let in_file = pos.get(pi + 1).ok_or_else(|| {
                    FastbootError::InvalidArgument("stage requires input file".into())
                })?;
                cfg.tasks.push(FastbootTask::Stage(PathBuf::from(in_file)));
                pi += 2;
            }
            "get_staged" => {
                let out_file = pos.get(pi + 1).ok_or_else(|| {
                    FastbootError::InvalidArgument("get_staged requires output file".into())
                })?;
                cfg.tasks
                    .push(FastbootTask::GetStaged(PathBuf::from(out_file)));
                pi += 2;
            }
            other => {
                return Err(FastbootError::InvalidArgument(format!(
                    "unknown command {other}"
                )));
            }
        }
    }

    if cfg.tasks.is_empty() && !cfg.wipe_userdata && cfg.set_active_slot.is_none() {
        return Err(FastbootError::InvalidArgument(
            "no command specified".into(),
        ));
    }

    Ok(FastbootCliAction::Execute(cfg))
}

/// Entry parsed from `fastboot-info.txt` (used by `flashall` and `update <zip>`).
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum FastbootInfoStep {
    Version(u32),
    Flash {
        partition: String,
        image_name: Option<String>,
        slot_other: bool,
        if_wipe: bool,
    },
    Reboot(Option<String>),
    UpdateSuper,
    Erase(String),
}

/// Parses an AOSP `fastboot-info.txt` manifest into a sequence of [`FastbootInfoStep`]s.
pub fn parse_fastboot_info(text: &str) -> Vec<FastbootInfoStep> {
    let mut steps = Vec::new();
    for raw_line in text.lines() {
        let line = raw_line.split('#').next().unwrap_or("").trim();
        if line.is_empty() {
            continue;
        }
        let mut if_wipe = false;
        let tokens: Vec<&str> = line.split_whitespace().collect();
        let slice = if tokens.len() >= 2 && tokens[0] == "if-wipe" {
            if_wipe = true;
            &tokens[1..]
        } else {
            &tokens[..]
        };
        if slice.is_empty() {
            continue;
        }
        match slice[0] {
            "version" => {
                if let Some(v) = slice.get(1).and_then(|s| s.parse::<u32>().ok()) {
                    steps.push(FastbootInfoStep::Version(v));
                }
            }
            "flash" => {
                let mut slot_other = false;
                let mut pos_args = Vec::new();
                for &tok in &slice[1..] {
                    if tok == "--slot-other" {
                        slot_other = true;
                    } else if !tok.starts_with('-') {
                        pos_args.push(tok);
                    }
                }
                if let Some(&part) = pos_args.first() {
                    let img = pos_args.get(1).map(|s| (*s).to_string());
                    steps.push(FastbootInfoStep::Flash {
                        partition: part.to_string(),
                        image_name: img,
                        slot_other,
                        if_wipe,
                    });
                }
            }
            "reboot" => {
                let target = slice.get(1).map(|s| (*s).to_string());
                steps.push(FastbootInfoStep::Reboot(target));
            }
            "update-super" => {
                steps.push(FastbootInfoStep::UpdateSuper);
            }
            "erase" => {
                if let Some(&part) = slice.get(1) {
                    steps.push(FastbootInfoStep::Erase(part.to_string()));
                }
            }
            _ => {}
        }
    }
    steps
}
