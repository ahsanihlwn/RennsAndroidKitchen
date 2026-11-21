use std::env;
use std::io::{self, Write};
use std::path::PathBuf;
use std::process::ExitCode;

use pac_extractor::pac::{
    PacError, PacHeader, PartitionEntry, open_pac_file, prepare_output_dir,
};

struct CliOptions {
    pac_file: PathBuf,
    out_dir: Option<PathBuf>,
    debug: bool,
    verify_crc: bool,
}

enum ParseOutcome {
    Run(CliOptions),
    Help,
}

fn print_usage(program: &str) {
    println!(
        "PAC Firmware Extractor & CRC-16 Verifier\n\n\
         Usage:\n  \
           {program} [OPTIONS] <pacfile> [outdir]\n\n\
         Arguments:\n  \
           <pacfile>     Path to Spreadtrum/Unisoc .pac firmware file\n  \
           [outdir]      Destination directory (defaults to ./outdir)\n\n\
         Options:\n  \
           -d, --debug   Print decoded PAC and partition header fields\n  \
           -c, --crc     Verify header (CRC1) and payload (CRC2) checksums\n  \
           -h, --help    Show this help message"
    );
}

fn parse_args() -> Result<ParseOutcome, String> {
    let mut args = env::args();
    let _program = args.next();

    let mut debug = false;
    let mut verify_crc = false;
    let mut positionals: Vec<String> = Vec::new();
    let mut flags_ended = false;

    for arg in args {
        if !flags_ended && arg == "--" {
            flags_ended = true;
            continue;
        }

        if !flags_ended && arg.starts_with('-') && arg.len() > 1 {
            match arg.as_str() {
                "-h" | "--help" => return Ok(ParseOutcome::Help),
                "-d" | "--debug" => debug = true,
                "-c" | "--crc" => verify_crc = true,
                _ => {
                    // Support combined short flags such as `-dc`
                    if !arg.starts_with("--") {
                        for ch in arg[1..].chars() {
                            match ch {
                                'd' => debug = true,
                                'c' => verify_crc = true,
                                'h' => return Ok(ParseOutcome::Help),
                                other => return Err(format!("unknown option '-{other}'")),
                            }
                        }
                    } else {
                        return Err(format!("unknown option '{arg}'"));
                    }
                }
            }
        } else {
            positionals.push(arg);
        }
    }

    match positionals.len() {
        0 => Err("missing required <pacfile> argument".to_string()),
        1 => Ok(ParseOutcome::Run(CliOptions {
            pac_file: PathBuf::from(&positionals[0]),
            out_dir: None,
            debug,
            verify_crc,
        })),
        2 => Ok(ParseOutcome::Run(CliOptions {
            pac_file: PathBuf::from(&positionals[0]),
            out_dir: Some(PathBuf::from(&positionals[1])),
            debug,
            verify_crc,
        })),
        _ => Err(format!(
            "unexpected extra argument '{}'",
            positionals[2]
        )),
    }
}

fn print_field(label: &str, value: impl std::fmt::Display) {
    println!("{label:<14} = {value}");
}

fn print_pac_header(header: &PacHeader) {
    print_field("Version", &header.version);
    if header.hi_size == 0 {
        print_field("Size", header.lo_size);
    } else {
        print_field("HiSize", header.hi_size);
        print_field("LoSize", header.lo_size);
        print_field("Size", header.total_size());
    }
    print_field("PrdName", &header.product_name);
    print_field("FirmwareName", &header.firmware_name);
    print_field("FileCount", header.partition_count);
    print_field("FileOffset", header.partitions_list_start);
    print_field("Mode", header.mode);
    print_field("FlashType", header.flash_type);
    print_field("NandStrategy", header.nand_strategy);
    print_field("IsNvBackup", header.is_nv_backup);
    print_field("NandPageType", header.nand_page_type);
    print_field("PrdAlias", &header.product_alias);
    print_field("OmaDmPrdFlag", header.oma_dm_product_flag);
    print_field("IsOmaDM", header.is_oma_dm);
    print_field("IsPreload", header.is_preload);
    print_field("Magic", format_args!("0x{:08x}", header.magic));
    print_field("CRC1", header.crc1);
    print_field("CRC2", header.crc2);
    println!();
}

fn print_partition_entry(entry: &PartitionEntry) {
    print_field("Size", entry.struct_size);
    print_field("FileID", &entry.partition_id);
    print_field("FileName", &entry.file_name);
    if entry.hi_partition_size == 0 {
        print_field("FileSize", entry.lo_partition_size);
    } else {
        print_field("HiFileSize", entry.hi_partition_size);
        print_field("LoFileSize", entry.lo_partition_size);
        print_field("FileSize", entry.partition_size());
    }
    print_field("FileFlag", entry.file_flag);
    print_field("CheckFlag", entry.check_flag);
    if entry.hi_data_offset == 0 {
        print_field("DataOffset", entry.lo_data_offset);
    } else {
        print_field("HiDataOffset", entry.hi_data_offset);
        print_field("LoDataOffset", entry.lo_data_offset);
        print_field("DataOffset", entry.data_offset());
    }
    print_field("CanOmitFlag", entry.can_omit_flag);
    println!();
}

fn render_progress(label: &str, done: u64, total: u64, last_pct: &mut Option<u64>) {
    let pct = (u128::from(done) * 100)
        .checked_div(u128::from(total))
        .map_or(100, |v| v as u64);
    if *last_pct == Some(pct) {
        return;
    }
    *last_pct = Some(pct);
    print!("\r{pct:>3}%  {label}");
    let _ = io::stdout().flush();
}

fn run(opts: CliOptions) -> Result<(), PacError> {
    let out_dir = match opts.out_dir {
        Some(dir) => dir,
        None => env::current_dir()?.join("outdir"),
    };

    if out_dir.is_file() {
        return Err(PacError::OutputPathIsFile(out_dir));
    }

    let mut reader = open_pac_file(&opts.pac_file)?;

    if opts.debug {
        print_pac_header(reader.header());
    }

    if opts.verify_crc {
        if reader.header().has_magic() {
            println!("Checking CRC Part 1 (header)...");
        }
        println!("Checking CRC Part 2 (payload)...");
        let mut last_pct = None;
        let summary = reader.verify_crc(|done, total| {
            render_progress("verifying", done, total, &mut last_pct);
        })?;
        println!("\r100%  CRC verified");
        if opts.debug {
            if let Some((computed, expected)) = summary.header_crc {
                println!("CRC1 OK: 0x{computed:04x} (header: 0x{expected:04x})");
            }
            println!(
                "CRC2 OK: 0x{:04x} (header: 0x{:04x})\n",
                summary.payload_crc.0, summary.payload_crc.1
            );
        }
    }

    let entries = reader.read_partitions()?;
    if opts.debug {
        for entry in &entries {
            print_partition_entry(entry);
        }
    }

    println!("\nExtracting to {}\n", out_dir.display());
    prepare_output_dir(&out_dir)?;

    for entry in &entries {
        if entry.partition_size() == 0 {
            continue;
        }
        let mut last_pct = None;
        let name = entry.file_name.as_str();
        if reader
            .extract_entry(entry, &out_dir, |done, total| {
                render_progress(name, done, total, &mut last_pct);
            })?
            .is_some()
        {
            println!("\r100%  {name}");
        }
    }

    println!("\nDone.");
    Ok(())
}

fn main() -> ExitCode {
    let program = env::args()
        .next()
        .unwrap_or_else(|| "pac-extractor".to_string());

    let opts = match parse_args() {
        Ok(ParseOutcome::Help) => {
            print_usage(&program);
            return ExitCode::SUCCESS;
        }
        Ok(ParseOutcome::Run(opts)) => opts,
        Err(msg) => {
            eprintln!("error: {msg}\nRun '{program} --help' for usage.");
            return ExitCode::from(2);
        }
    };

    if let Err(err) = run(opts) {
        eprintln!("\nerror: {err}");
        return ExitCode::FAILURE;
    }

    ExitCode::SUCCESS
}
