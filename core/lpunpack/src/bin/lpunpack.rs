//! lpunpack - command-line tool for inspecting and extracting partition images from super.

use std::env;
use std::fs::{self, File};
use std::io::{self, Write};
use std::path::PathBuf;
use std::process;

use lp_tools::lp::{
    extract_partition_to_dir, filter_partitions, format_metadata_json, format_metadata_text,
    read_metadata_auto, select_partitions_for_extract, unsparse_super_image,
};
use lp_tools::sparse::ImageSource;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum InfoFormat {
    Text,
    Json,
}

struct CliArgs {
    partitions: Vec<String>,
    slot: u32,
    show_info: bool,
    normalize: bool,
    unsparse: bool,
    format: InfoFormat,
    super_image: PathBuf,
    output_dir: Option<PathBuf>,
}

fn print_usage(prog: &str) {
    eprintln!(
        "{prog} - command-line tool for extracting partition images from super\n\
         \n\
         Usage:\n\
         \x20 {prog} [options...] SUPER_IMAGE [OUTPUT_DIR]\n\
         \n\
         Options:\n\
         \x20 -h, --help               Show this help message and exit.\n\
         \x20 -p, --partition=NAME     Extract the named partition. Can be specified\n\
         \x20                          multiple times or delimited by ',' or ':'.\n\
         \x20 -S, --slot=NUM           Slot number (default is 0).\n\
         \x20     --info, --no-info    Display partition metadata info (default: false).\n\
         \x20     --norm, --no-norm    Normalize *_a/*_b partition slots to single <name>.img.\n\
         \x20     --unsparse           Convert an Android sparse image to a raw image.\n\
         \x20 -f, --format=FORMAT      Info output format: text or json (default: text)."
    );
}

fn parse_slot(val: &str) -> Result<u32, String> {
    if let Ok(n) = val.parse::<u32>() {
        return Ok(n);
    }
    match val {
        "a" | "_a" => Ok(0),
        "b" | "_b" => Ok(1),
        _ => Err(format!("Slot must be a valid unsigned number: '{val}'")),
    }
}

fn split_partition_names(val: &str, out: &mut Vec<String>) {
    for part in val.split(|c| c == ',' || c == ':') {
        let trimmed = part.trim();
        if !trimmed.is_empty() {
            out.push(trimmed.to_string());
        }
    }
}

fn parse_args() -> Result<Option<CliArgs>, (String, i32)> {
    let raw_args: Vec<String> = env::args().collect();
    let prog = raw_args
        .first()
        .map(|s| s.as_str())
        .unwrap_or("lpunpack");

    if raw_args.len() <= 1 {
        print_usage(prog);
        return Err((String::new(), 64));
    }

    let mut partitions = Vec::new();
    let mut slot: u32 = 0;
    let mut show_info = false;
    let mut normalize = false;
    let mut unsparse = false;
    let mut format = InfoFormat::Text;
    let mut positionals: Vec<String> = Vec::new();
    let mut stop_flags = false;

    let mut i = 1;
    while i < raw_args.len() {
        let arg = &raw_args[i];
        if !stop_flags && arg == "--" {
            stop_flags = true;
            i += 1;
            continue;
        }

        if !stop_flags && arg.starts_with("--") {
            let (flag, inline_val) = match arg.split_once('=') {
                Some((k, v)) => (k, Some(v)),
                None => (arg.as_str(), None),
            };
            match flag {
                "--help" => {
                    print_usage(prog);
                    return Ok(None);
                }
                "--info" => {
                    if inline_val.is_some() {
                        return Err(("--info does not take a value.".into(), 64));
                    }
                    show_info = true;
                }
                "--no-info" => {
                    if inline_val.is_some() {
                        return Err(("--no-info does not take a value.".into(), 64));
                    }
                    show_info = false;
                }
                "--norm" => {
                    if inline_val.is_some() {
                        return Err(("--norm does not take a value.".into(), 64));
                    }
                    normalize = true;
                }
                "--no-norm" => {
                    if inline_val.is_some() {
                        return Err(("--no-norm does not take a value.".into(), 64));
                    }
                    normalize = false;
                }
                "--unsparse" => {
                    if inline_val.is_some() {
                        return Err(("--unsparse does not take a value.".into(), 64));
                    }
                    unsparse = true;
                }
                "--partition" => {
                    let val = match inline_val {
                        Some(v) => v.to_string(),
                        None => {
                            i += 1;
                            raw_args
                                .get(i)
                                .cloned()
                                .ok_or_else(|| ("--partition requires a value.".into(), 64))?
                        }
                    };
                    split_partition_names(&val, &mut partitions);
                }
                "--slot" => {
                    let val = match inline_val {
                        Some(v) => v.to_string(),
                        None => {
                            i += 1;
                            raw_args
                                .get(i)
                                .cloned()
                                .ok_or_else(|| ("--slot requires a value.".into(), 64))?
                        }
                    };
                    slot = parse_slot(&val).map_err(|e| (e, 64))?;
                }
                "--format" => {
                    let val = match inline_val {
                        Some(v) => v.to_string(),
                        None => {
                            i += 1;
                            raw_args
                                .get(i)
                                .cloned()
                                .ok_or_else(|| ("--format requires a value.".into(), 64))?
                        }
                    };
                    format = match val.as_str() {
                        "text" => InfoFormat::Text,
                        "json" => InfoFormat::Json,
                        _ => {
                            return Err((
                                format!("Invalid format '{val}': expected 'text' or 'json'."),
                                64,
                            ))
                        }
                    };
                }
                _ => return Err((format!("Unrecognized argument: {arg}"), 64)),
            }
        } else if !stop_flags && arg.starts_with('-') && arg.len() > 1 {
            let mut chars = arg[1..].chars();
            while let Some(ch) = chars.next() {
                match ch {
                    'h' => {
                        print_usage(prog);
                        return Ok(None);
                    }
                    'p' => {
                        let rest: String = chars.collect();
                        let val = if !rest.is_empty() {
                            rest
                        } else {
                            i += 1;
                            raw_args
                                .get(i)
                                .cloned()
                                .ok_or_else(|| ("-p requires a value.".into(), 64))?
                        };
                        split_partition_names(&val, &mut partitions);
                        break;
                    }
                    'S' => {
                        let rest: String = chars.collect();
                        let val = if !rest.is_empty() {
                            rest
                        } else {
                            i += 1;
                            raw_args
                                .get(i)
                                .cloned()
                                .ok_or_else(|| ("-S requires a value.".into(), 64))?
                        };
                        slot = parse_slot(&val).map_err(|e| (e, 64))?;
                        break;
                    }
                    'f' => {
                        let rest: String = chars.collect();
                        let val = if !rest.is_empty() {
                            rest
                        } else {
                            i += 1;
                            raw_args
                                .get(i)
                                .cloned()
                                .ok_or_else(|| ("-f requires a value.".into(), 64))?
                        };
                        format = match val.as_str() {
                            "text" => InfoFormat::Text,
                            "json" => InfoFormat::Json,
                            _ => {
                                return Err((
                                    format!("Invalid format '{val}': expected 'text' or 'json'."),
                                    64,
                                ))
                            }
                        };
                        break;
                    }
                    _ => return Err((format!("Unrecognized argument: -{ch}"), 64)),
                }
            }
        } else {
            positionals.push(arg.clone());
        }
        i += 1;
    }

    if positionals.is_empty() {
        return Err(("Missing super image argument.".into(), 64));
    }
    if positionals.len() > 2 {
        return Err(("Unrecognized command-line arguments.".into(), 64));
    }

    let super_image = PathBuf::from(&positionals[0]);
    let output_dir = positionals.get(1).map(PathBuf::from);

    Ok(Some(CliArgs {
        partitions,
        slot,
        show_info,
        normalize,
        unsparse,
        format,
        super_image,
        output_dir,
    }))
}

fn run(args: CliArgs) -> Result<(), (String, i32)> {
    if args.unsparse {
        let target_path =
            unsparse_super_image(&args.super_image, args.output_dir.as_deref())
                .map_err(|e| (e.to_string(), 65))?;
        println!("Unsparsed image -> {}", target_path.display());
        if !args.show_info {
            return Ok(());
        }
    }

    let file = File::open(&args.super_image).map_err(|e| {
        (
            format!("open failed: {}: {e}", args.super_image.display()),
            71,
        )
    })?;
    let mut source = ImageSource::open(file).map_err(|e| (e.to_string(), 65))?;
    if source.is_sparse() && !args.show_info {
        println!("Sparse image detected.");
    }

    let (mut metadata, is_empty) =
        read_metadata_auto(&mut source, args.slot).map_err(|e| (e.to_string(), 65))?;

    filter_partitions(&mut metadata, &args.partitions, args.normalize)
        .map_err(|e| (e.to_string(), 70))?;

    if args.show_info {
        match args.format {
            InfoFormat::Text => print!("{}", format_metadata_text(&metadata, args.slot)),
            InfoFormat::Json => println!("{}", format_metadata_json(&metadata)),
        }
    }

    let extract_dir = match args.output_dir {
        Some(dir) => Some(dir),
        None if !args.show_info => Some(PathBuf::from(".")),
        None => None,
    };

    if let Some(out_dir) = extract_dir {
        if is_empty {
            return Err((
                "Empty super image contains metadata only and cannot be unpacked.".into(),
                64,
            ));
        }
        if !out_dir.exists() {
            fs::create_dir_all(&out_dir).map_err(|e| {
                (
                    format!("Failed to create output directory '{}': {e}", out_dir.display()),
                    73,
                )
            })?;
        }
        let plan = select_partitions_for_extract(&metadata, args.normalize, args.slot);
        for (part, out_name) in &plan {
            let part_name = part.name();
            if out_name == &part_name {
                print!("Extracting partition [{part_name}] ....");
            } else {
                print!("Extracting partition [{part_name} -> {out_name}] ....");
            }
            io::stdout().flush().map_err(|e| (e.to_string(), 74))?;
            extract_partition_to_dir(
                &args.super_image,
                &mut source,
                &metadata,
                part,
                out_name,
                &out_dir,
            )
            .map_err(|e| (e.to_string(), 70))?;
            println!(" [ok]");
        }
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
