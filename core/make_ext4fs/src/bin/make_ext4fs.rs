use std::env;
use std::fs::{self, File, OpenOptions};
use std::path::Path;
use std::process;

use make_ext4fs_rs::config::fs_config::FsConfigProvider;
use make_ext4fs_rs::config::selinux::FileContexts;
use make_ext4fs_rs::ext4::fs::{make_ext4fs, Ext4Options, OutputTarget};

fn usage(prog: &str) {
    let base = Path::new(prog)
        .file_name()
        .and_then(|s| s.to_str())
        .unwrap_or(prog);
    eprintln!("{} [ -l <len> ] [ -j <journal size> ] [ -b <block_size> ]", base);
    eprintln!("    [ -g <blocks per group> ] [ -i <inodes> ] [ -I <inode size> ]");
    eprintln!("    [ -L <label> ] [ -f ] [ -a <android mountpoint> ]");
    eprintln!("    [ -S file_contexts ] [ -C fs_config ] [ -T timestamp ]");
    eprintln!("    [ -z | -s ] [ -w ] [ -c ] [ -J ] [ -v ] [ -B <block_list_file> ]");
    eprintln!("    <filename> [<directory>]");
}

fn parse_num(arg: &str) -> i64 {
    let s = arg.trim_start();
    let (neg, rest) = if let Some(r) = s.strip_prefix('-') {
        (true, r)
    } else if let Some(r) = s.strip_prefix('+') {
        (false, r)
    } else {
        (false, s)
    };
    let digit_end = rest
        .find(|c: char| !c.is_ascii_digit())
        .unwrap_or(rest.len());
    let mut num = rest[..digit_end].parse::<u64>().unwrap_or(0);
    if neg {
        num = num.wrapping_neg();
    }
    if let Some(suffix) = rest[digit_end..].chars().next() {
        match suffix {
            'k' | 'K' => num = num.wrapping_mul(1024),
            'm' | 'M' => num = num.wrapping_mul(1024 * 1024),
            'g' | 'G' => num = num.wrapping_mul(1024 * 1024 * 1024),
            _ => {}
        }
    }
    num as i64
}

fn parse_i64_auto(arg: &str) -> i64 {
    let s = arg.trim();
    let (neg, rest) = if let Some(r) = s.strip_prefix('-') {
        (true, r)
    } else if let Some(r) = s.strip_prefix('+') {
        (false, r)
    } else {
        (false, s)
    };
    let val = if let Some(hex) = rest.strip_prefix("0x").or_else(|| rest.strip_prefix("0X")) {
        i64::from_str_radix(hex, 16).unwrap_or(0)
    } else if rest.starts_with('0') && rest.len() > 1 {
        i64::from_str_radix(&rest[1..], 8).unwrap_or(0)
    } else {
        rest.parse::<i64>().unwrap_or(0)
    };
    if neg {
        val.wrapping_neg()
    } else {
        val
    }
}

fn main() {
    let args: Vec<String> = env::args().collect();
    let prog = args.first().map(|s| s.as_str()).unwrap_or("make_ext4fs");

    let mut opts = Ext4Options::default();
    let mut mountpoint: Option<String> = None;
    let mut fs_config_file: Option<String> = None;
    let mut file_contexts_path: Option<String> = None;
    let mut block_list_path: Option<String> = None;
    let mut positional: Vec<String> = Vec::new();

    let mut i = 1;
    let mut end_of_opts = false;
    while i < args.len() {
        let arg = &args[i];
        if !end_of_opts && arg == "--" {
            end_of_opts = true;
            i += 1;
            continue;
        }
        if !end_of_opts && arg.starts_with('-') && arg.len() > 1 {
            let chars: Vec<char> = arg[1..].chars().collect();
            let mut ci = 0;
            while ci < chars.len() {
                let c = chars[ci];
                let needs_val = matches!(
                    c,
                    'l' | 'j' | 'b' | 'g' | 'i' | 'I' | 'L' | 'a' | 'S' | 'T' | 'C' | 'B'
                );
                let val = if needs_val {
                    if ci + 1 < chars.len() {
                        let rest: String = chars[ci + 1..].iter().collect();
                        ci = chars.len();
                        rest
                    } else {
                        i += 1;
                        ci = chars.len();
                        if i >= args.len() {
                            eprintln!("{}: option requires an argument -- '{}'", prog, c);
                            usage(prog);
                            process::exit(1);
                        }
                        args[i].clone()
                    }
                } else {
                    ci += 1;
                    String::new()
                };

                match c {
                    'l' => opts.len = parse_num(&val),
                    'j' => opts.journal_blocks = parse_num(&val) as u32,
                    'b' => opts.block_size = parse_num(&val) as u32,
                    'g' => opts.blocks_per_group = parse_num(&val) as u32,
                    'i' => opts.inodes = parse_num(&val) as u32,
                    'I' => opts.inode_size = parse_num(&val) as u32,
                    'L' => opts.label = val,
                    'f' => opts.force = true,
                    'a' => mountpoint = Some(val),
                    'w' => opts.wipe = true,
                    'z' => opts.gzip = true,
                    'J' => opts.no_journal = true,
                    'c' => opts.crc = true,
                    's' => opts.sparse = true,
                    't' => eprintln!("Warning: -t (initialize inode tables) is deprecated"),
                    'S' => file_contexts_path = Some(val),
                    'v' => opts.verbose = true,
                    'T' => opts.fixed_time = parse_i64_auto(&val),
                    'C' => fs_config_file = Some(val),
                    'B' => block_list_path = Some(val),
                    _ => {
                        eprintln!("{}: invalid option -- '{}'", prog, c);
                        usage(prog);
                        process::exit(1);
                    }
                }
            }
            i += 1;
        } else {
            positional.push(arg.clone());
            i += 1;
        }
    }

    let sehnd = if let Some(ref fc_path) = file_contexts_path {
        match FileContexts::open(Path::new(fc_path)) {
            Ok(fc) => Some(fc),
            Err(e) => {
                eprintln!("{}: {}", fc_path, e);
                process::exit(1);
            }
        }
    } else {
        None
    };

    let fs_config = if let Some(ref cfg_path) = fs_config_file {
        match make_ext4fs_rs::config::fs_config::CannedFsConfig::load(Path::new(cfg_path)) {
            Ok(canned) => FsConfigProvider::Canned(canned),
            Err(e) => {
                eprintln!("failed to open {}: {}", cfg_path, e);
                eprintln!("failed to load {}", cfg_path);
                process::exit(1);
            }
        }
    } else if mountpoint.is_some() {
        FsConfigProvider::Default
    } else {
        FsConfigProvider::None
    };

    if opts.wipe && opts.sparse {
        eprintln!("Cannot specifiy both wipe and sparse");
        usage(prog);
        process::exit(1);
    }

    if opts.wipe && opts.gzip {
        eprintln!("Cannot specifiy both wipe and gzip");
        usage(prog);
        process::exit(1);
    }

    if positional.is_empty() {
        eprintln!("Expected filename after options");
        usage(prog);
        process::exit(1);
    }

    let filename = &positional[0];
    let directory = positional.get(1).map(|s| s.as_str());
    if positional.len() > 2 {
        eprintln!("Unexpected argument: {}", positional[2]);
        usage(prog);
        process::exit(1);
    }

    let mut block_list_file = if let Some(ref blp) = block_list_path {
        match File::create(blp) {
            Ok(f) => Some(f),
            Err(e) => {
                eprintln!("failed to open block_list_file: {}", e);
                process::exit(1);
            }
        }
    } else {
        None
    };

    let output = if filename == "-" {
        OutputTarget::Stdout
    } else {
        match OpenOptions::new()
            .write(true)
            .create(true)
            .truncate(true)
            .open(filename)
        {
            Ok(f) => OutputTarget::File(f),
            Err(e) => {
                eprintln!("open: {}", e);
                process::exit(1);
            }
        }
    };

    let result = make_ext4fs(
        output,
        directory,
        mountpoint.as_deref(),
        &fs_config,
        sehnd.as_ref(),
        &opts,
        block_list_file.as_mut(),
    );
    drop(block_list_file);

    if let Err(e) = result {
        eprintln!("{}", e);
        if filename != "-" {
            eprintln!("Failed to create image {}, removing it", filename);
            let _ = fs::remove_file(filename);
        }
        process::exit(1);
    }
}
