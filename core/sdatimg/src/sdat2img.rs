use std::fs::{File, OpenOptions};
use std::io::{BufRead, BufReader, Read, Seek, SeekFrom, Write};
use std::path::Path;

use crate::blockimgdiff::BLOCK_SIZE;
use crate::error::{Error, Result};
use crate::rangelib::RangeSet;
use crate::sparse_img::raw_to_sparse;

pub const VERSION: &str = "1.2";

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum CommandKind {
    Erase,
    New,
    Zero,
}

impl CommandKind {
    pub fn as_str(self) -> &'static str {
        match self {
            CommandKind::Erase => "erase",
            CommandKind::New => "new",
            CommandKind::Zero => "zero",
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TransferCommand {
    pub kind: CommandKind,
    pub ranges: Vec<(u64, u64)>,
}

#[derive(Debug, Clone)]
pub struct TransferList {
    pub version: u32,
    pub new_blocks: u64,
    pub stash_slots: Option<u64>,
    pub max_stashed_blocks: Option<u64>,
    pub commands: Vec<TransferCommand>,
}

pub fn parse_transfer_list_file(path: &Path) -> Result<TransferList> {
    let f = File::open(path)?;
    let mut reader = BufReader::new(f);

    let read_next_line = |reader: &mut BufReader<File>, label: &str| -> Result<String> {
        let mut line = String::new();
        let n = reader.read_line(&mut line)?;
        if n == 0 {
            return Err(Error::InvalidTransferList(format!(
                "unexpected EOF reading {label}"
            )));
        }
        Ok(line)
    };

    let version_line = read_next_line(&mut reader, "version")?;
    let version: u32 = version_line.trim().parse().map_err(|_| {
        Error::InvalidTransferList(format!("invalid version line: '{}'", version_line.trim()))
    })?;

    let new_blocks_line = read_next_line(&mut reader, "new_blocks")?;
    let new_blocks: u64 = new_blocks_line.trim().parse().map_err(|_| {
        Error::InvalidTransferList(format!(
            "invalid new_blocks line: '{}'",
            new_blocks_line.trim()
        ))
    })?;

    let (stash_slots, max_stashed_blocks) = if version >= 2 {
        let s_line = read_next_line(&mut reader, "stash_slots")?;
        let m_line = read_next_line(&mut reader, "max_stashed_blocks")?;
        let s = s_line.trim().parse::<u64>().ok();
        let m = m_line.trim().parse::<u64>().ok();
        (s, m)
    } else {
        (None, None)
    };

    let mut commands = Vec::new();
    let mut line = String::new();
    loop {
        line.clear();
        let n = reader.read_line(&mut line)?;
        if n == 0 {
            break;
        }
        let trimmed = line.trim();
        if trimmed.is_empty() {
            continue;
        }
        let mut parts = trimmed.split(' ');
        let cmd = parts.next().unwrap_or("");
        match cmd {
            "erase" | "new" | "zero" => {
                let range_arg = parts.next().ok_or_else(|| {
                    Error::InvalidTransferList(format!("missing rangeset for '{cmd}' command"))
                })?;
                let ranges = RangeSet::parse_raw_pairs(range_arg)?;
                let kind = match cmd {
                    "erase" => CommandKind::Erase,
                    "new" => CommandKind::New,
                    "zero" => CommandKind::Zero,
                    _ => unreachable!(),
                };
                commands.push(TransferCommand { kind, ranges });
            }
            other => {
                let first_is_digit = other
                    .chars()
                    .next()
                    .map(|c| c.is_ascii_digit())
                    .unwrap_or(false);
                if !first_is_digit {
                    return Err(Error::InvalidCommand(other.to_string()));
                }
            }
        }
    }

    Ok(TransferList {
        version,
        new_blocks,
        stash_slots,
        max_stashed_blocks,
        commands,
    })
}

#[derive(Debug, Clone, Default)]
pub struct Sdat2ImgOptions {
    pub sparse_output: bool,
    pub no_clobber: bool,
    pub quiet: bool,
}

/// Convert `transfer.list` + `new.dat` into a raw (or optional sparse) image file.
pub fn sdat2img(
    transfer_list_path: &Path,
    new_data_path: &Path,
    output_image_path: &Path,
    opts: &Sdat2ImgOptions,
) -> Result<()> {
    if !opts.quiet {
        println!("sdatimg sdat2img v{VERSION}");
    }

    let transfer_list = parse_transfer_list_file(transfer_list_path)?;

    if !opts.quiet {
        let desc = match transfer_list.version {
            1 => "Transfer list format v1 (Android 5.0)",
            2 => "Transfer list format v2 (Android 5.1)",
            3 => "Transfer list format v3 (Android 6.x)",
            4 => "Transfer list format v4 (Android 7.0+)",
            _ => "Unknown transfer list format version",
        };
        println!("{desc}");
    }

    if opts.no_clobber && output_image_path.exists() {
        return Err(Error::InvalidData(format!(
            "output file \"{}\" already exists",
            output_image_path.display()
        )));
    }

    let raw_target = if opts.sparse_output {
        let mut tmp = output_image_path.as_os_str().to_os_string();
        tmp.push(".raw.tmp");
        std::path::PathBuf::from(tmp)
    } else {
        output_image_path.to_path_buf()
    };

    struct RemoveOnDrop(Option<std::path::PathBuf>);
    impl Drop for RemoveOnDrop {
        fn drop(&mut self) {
            if let Some(p) = &self.0 {
                let _ = std::fs::remove_file(p);
            }
        }
    }
    let _tmp_guard = RemoveOnDrop(if opts.sparse_output {
        Some(raw_target.clone())
    } else {
        None
    });

    let mut output_img = OpenOptions::new()
        .write(true)
        .create(true)
        .truncate(true)
        .open(&raw_target)?;

    let mut new_data_file = File::open(new_data_path)?;

    let max_end_block = transfer_list
        .commands
        .iter()
        .flat_map(|c| c.ranges.iter().map(|&(_, end)| end))
        .max()
        .unwrap_or(0);
    let max_file_size = max_end_block * BLOCK_SIZE;

    const BUF_SIZE: usize = 4 * 1024 * 1024;
    let mut buf = vec![0u8; BUF_SIZE];

    for cmd in &transfer_list.commands {
        match cmd.kind {
            CommandKind::New => {
                for &(begin, end) in &cmd.ranges {
                    let block_count = end - begin;
                    if !opts.quiet {
                        println!("Copying {block_count} blocks into position {begin}...");
                    }
                    output_img.seek(SeekFrom::Start(begin * BLOCK_SIZE))?;

                    let mut remaining = block_count * BLOCK_SIZE;
                    while remaining > 0 {
                        let to_read = (remaining as usize).min(BUF_SIZE);
                        let n = new_data_file.read(&mut buf[..to_read])?;
                        if n == 0 {
                            return Err(Error::UnexpectedEof(format!(
                                "new data file {} ({} bytes remaining)",
                                new_data_path.display(),
                                remaining
                            )));
                        }
                        output_img.write_all(&buf[..n])?;
                        remaining -= n as u64;
                    }
                }
            }
            CommandKind::Erase | CommandKind::Zero => {
                if !opts.quiet {
                    println!("Skipping command {}...", cmd.kind.as_str());
                }
            }
        }
    }

    output_img.flush()?;
    let current_len = output_img.metadata()?.len();
    if current_len < max_file_size {
        output_img.set_len(max_file_size)?;
    }
    drop(output_img);

    if opts.sparse_output {
        raw_to_sparse(&raw_target, output_image_path)?;
    }

    if !opts.quiet {
        let abs = std::fs::canonicalize(output_image_path)
            .unwrap_or_else(|_| output_image_path.to_path_buf());
        println!("Done! Output image: {}", abs.display());
    }

    Ok(())
}
