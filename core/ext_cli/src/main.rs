use std::env;
use std::fs::{self, File};
use std::io::{self, Read, Write};
use std::path::Path;
use std::process;

use ext_cli::check::check_ext;
use ext_cli::edit::{unlink_path, write_path_data, write_path_mode, write_path_selinux};
use ext_cli::extfs::Volume;
use ext_cli::unpack::unpack_image;

fn print_help() -> ! {
    println!("EXT4 Tool CLI");
    println!("Usage:");
    println!("  --read    <image.img>");
    println!("  --unpack  <image.img>");
    println!("  --list    <image.img>");
    println!("  --usage   <image.img>");
    println!("  --cat     <image.img> <internal_path>");
    println!("  --write   <image.img> <internal_path> <local_file|->");
    println!("  --chmod   <image.img> <internal_path> <octal_mode>");
    println!("  --selinux <image.img> <internal_path> <context>");
    println!("  --rm      <image.img> <internal_path> [--no-recursive]");
    process::exit(1);
}

fn main() {
    let args: Vec<String> = env::args().collect();
    if args.len() < 3 {
        print_help();
    }

    let mut mode: Option<&str> = None;
    let mut positional: Vec<String> = Vec::new();
    let mut recursive = true;
    let mut i = 1;
    while i < args.len() {
        match args[i].as_str() {
            "--read" => mode = Some("--read"),
            "--unpack" => mode = Some("--unpack"),
            "--list" => mode = Some("--list"),
            "--usage" => mode = Some("--usage"),
            "--cat" => mode = Some("--cat"),
            "--write" => mode = Some("--write"),
            "--chmod" => mode = Some("--chmod"),
            "--selinux" => mode = Some("--selinux"),
            "--rm" => mode = Some("--rm"),
            "--no-recursive" => recursive = false,
            "--x" => {
                if i + 1 < args.len() {
                    i += 1;
                    positional.push(args[i].clone());
                }
            }
            "-" => positional.push("-".to_string()),
            arg if !arg.starts_with('-') => positional.push(arg.to_string()),
            _ => {}
        }
        i += 1;
    }

    let (Some(cmd), Some(img_str)) = (mode, positional.first().map(|s| s.as_str())) else {
        print_help();
    };

    let img_path = Path::new(img_str);
    if !img_path.exists() {
        eprintln!("[ERR] File not found: {}", img_str);
        process::exit(2);
    }

    match cmd {
        "--read" => check_ext(img_path),
        "--unpack" => match unpack_image(img_path) {
            Ok(out_dir) => {
                println!("[OK] Unpack finished");
                println!("Output: {}", out_dir.display());
            }
            Err(e) => {
                eprintln!("[ERR] Unpack failed: {}", e);
                process::exit(1);
            }
        },
        "--list" => {
            let f = File::open(img_path).unwrap_or_else(|e| {
                eprintln!("[ERR] Open failed: {e}");
                process::exit(1);
            });
            let mut vol = Volume::open(f).unwrap_or_else(|e| {
                eprintln!("[ERR] Invalid EXT image: {e}");
                process::exit(1);
            });
            match vol.walk_nodes() {
                Ok(nodes) => {
                    for n in nodes {
                        let kind = if n.is_dir { "d" } else if n.is_link { "l" } else { "f" };
                        println!(
                            "{kind}\t{}\t{}\t{}\t{}",
                            n.perm,
                            n.size,
                            n.selinux.as_deref().unwrap_or(""),
                            n.path
                        );
                    }
                }
                Err(e) => {
                    eprintln!("[ERR] List failed: {e}");
                    process::exit(1);
                }
            }
        }
        "--usage" => {
            let f = File::open(img_path).unwrap_or_else(|e| {
                eprintln!("[ERR] Open failed: {e}");
                process::exit(1);
            });
            let file_size = fs::metadata(img_path).map(|m| m.len()).unwrap_or(0);
            let mut vol = Volume::open(f).unwrap_or_else(|e| {
                eprintln!("[ERR] Invalid EXT image: {e}");
                process::exit(1);
            });
            match vol.usage_report(file_size) {
                Ok(rep) => {
                    println!(
                        "FS={} block_size={} file_size={} used_bytes={} free_bytes={} logical_bytes={} files={} dirs={} links={}",
                        rep.fs, rep.block_size, rep.file_size, rep.used_bytes, rep.free_bytes, rep.logical_bytes, rep.files, rep.dirs, rep.links
                    );
                }
                Err(e) => {
                    eprintln!("[ERR] Usage failed: {e}");
                    process::exit(1);
                }
            }
        }
        "--cat" => {
            let Some(internal) = positional.get(1) else {
                print_help();
            };
            let f = File::open(img_path).unwrap_or_else(|e| {
                eprintln!("[ERR] Open failed: {e}");
                process::exit(1);
            });
            let mut vol = Volume::open(f).unwrap_or_else(|e| {
                eprintln!("[ERR] Invalid EXT image: {e}");
                process::exit(1);
            });
            match vol.read_path(internal) {
                Ok((bytes, _node)) => {
                    if let Err(e) = io::stdout().write_all(&bytes) {
                        eprintln!("[ERR] Write stdout failed: {e}");
                        process::exit(1);
                    }
                }
                Err(e) => {
                    eprintln!("[ERR] Read failed: {e}");
                    process::exit(1);
                }
            }
        }
        "--write" => {
            let (Some(internal), Some(src)) = (positional.get(1), positional.get(2)) else {
                print_help();
            };
            let data = if src == "-" {
                let mut buf = Vec::new();
                io::stdin().read_to_end(&mut buf).unwrap_or_else(|e| {
                    eprintln!("[ERR] Stdin read failed: {e}");
                    process::exit(1);
                });
                buf
            } else {
                fs::read(src).unwrap_or_else(|e| {
                    eprintln!("[ERR] Read source failed: {e}");
                    process::exit(1);
                })
            };
            match write_path_data(img_path, internal, &data) {
                Ok(res) => println!(
                    "[OK] {} ({} -> {} B, freed {} blocks)",
                    res.path, res.old_size, res.new_size, res.blocks_freed
                ),
                Err(e) => {
                    eprintln!("[ERR] Write failed: {e}");
                    process::exit(1);
                }
            }
        }
        "--chmod" => {
            let (Some(internal), Some(mode_str)) = (positional.get(1), positional.get(2)) else {
                print_help();
            };
            let octal = u16::from_str_radix(mode_str.trim(), 8).unwrap_or_else(|e| {
                eprintln!("[ERR] Invalid octal mode '{mode_str}': {e}");
                process::exit(1);
            });
            match write_path_mode(img_path, internal, octal) {
                Ok(new_mode) => println!("[OK] {} mode={:04o}", internal, new_mode & 0o7777),
                Err(e) => {
                    eprintln!("[ERR] Chmod failed: {e}");
                    process::exit(1);
                }
            }
        }
        "--selinux" => {
            let (Some(internal), Some(ctx)) = (positional.get(1), positional.get(2)) else {
                print_help();
            };
            match write_path_selinux(img_path, internal, ctx) {
                Ok(val) => println!("[OK] {} selinux={}", internal, val),
                Err(e) => {
                    eprintln!("[ERR] SELinux write failed: {e}");
                    process::exit(1);
                }
            }
        }
        "--rm" => {
            let Some(internal) = positional.get(1) else {
                print_help();
            };
            match unlink_path(img_path, internal, recursive) {
                Ok(res) => println!(
                    "[OK] Removed {} (entries={}, blocks_freed={})",
                    res.removed, res.entries_removed, res.blocks_freed
                ),
                Err(e) => {
                    eprintln!("[ERR] Remove failed: {e}");
                    process::exit(1);
                }
            }
        }
        _ => print_help(),
    }
}
