mod bootimg;
mod compress;
mod cpio;
mod dtb;
mod format;
mod hexpatch;
mod pattern;

use std::env;
use std::fs;
use std::process;

use memmap2::Mmap;
use sha1::{Digest, Sha1};

use crate::bootimg::{
    BOOTCONFIG_FILE, DTB_FILE, EXTRA_FILE, HEADER_FILE, KER_DTB_FILE, KERNEL_FILE, NEW_BOOT,
    RAMDISK_FILE, RECV_DTBO_FILE, SECOND_FILE, V4_META_FILE,
};
use crate::format::Format;

fn print_usage(prog: &str) -> ! {
    let fmts = Format::SUPPORTED_COMPRESSION_NAMES.join(", ");
    eprintln!(
        "Usage: {prog} <command> [options]

Commands:
  unpack [-n] [-h] <boot_image>
      Extract image contents into working directory.
      -n: skip decompression
      -h: export header key-values to 'header' file

  repack [-n] <orig_image> [out_image]
      Assemble image from current directory (default output: {NEW_BOOT}).
      -n: skip compression

  cpio <archive> [cmd...]
      Inspect or modify CPIO archive in-place.
      Commands: exists, rm, mkdir, ln, mv, add, extract, sync, test, patch, backup, restore, sha1

  cpio pack [-c <config>] <folder> <output_cpio>
      Package directory into CPIO archive.

  dtb <dtb_file> <print [-f]|patch|test>
      Inspect or update device tree blobs.

  split <kernel_dtb_file>
      Separate appended DTB from kernel.

  hexpatch <target_file> <find_hex> <replace_hex>
      Binary pattern substitution.

  sha1 <file>
      Compute SHA1 hash of target file.

  compress[=<fmt>] <input> [output]
      Supported algorithms: {fmts}

  decompress <input> [output]
      Auto-detect and extract compressed stream.

  cleanup
      Remove unpacked staging files in current directory."
    );
    process::exit(1);
}

fn cmd_cleanup() {
    eprintln!("Cleaning up...");
    for f in [
        HEADER_FILE,
        KERNEL_FILE,
        RAMDISK_FILE,
        SECOND_FILE,
        KER_DTB_FILE,
        EXTRA_FILE,
        RECV_DTBO_FILE,
        DTB_FILE,
        BOOTCONFIG_FILE,
        V4_META_FILE,
    ] {
        let _ = fs::remove_file(f);
    }
    if let Ok(entries) = fs::read_dir(".") {
        for entry in entries.flatten() {
            if let Some(name) = entry.file_name().to_str() {
                if let Some(rest) = name.strip_prefix("ramdisk_") {
                    if let Some(num) = rest.strip_suffix(".cpio") {
                        if !num.is_empty() && num.chars().all(|c| c.is_ascii_digit()) {
                            let _ = fs::remove_file(entry.path());
                        }
                    }
                }
            }
        }
    }
}

fn cmd_sha1(path: &str) -> Result<(), String> {
    let file = fs::File::open(path).map_err(|e| format!("Failed opening [{path}]: {e}"))?;
    let meta = file
        .metadata()
        .map_err(|e| format!("Failed stat [{path}]: {e}"))?;
    let digest = if meta.len() == 0 {
        Sha1::digest([])
    } else {
        let mmap = unsafe { Mmap::map(&file) }
            .map_err(|e| format!("Failed mapping [{path}]: {e}"))?;
        Sha1::digest(&mmap[..])
    };
    for b in digest {
        print!("{b:02x}");
    }
    println!();
    Ok(())
}

fn run() -> Result<i32, String> {
    let args: Vec<String> = env::args().collect();
    let prog = args.first().map(|s| s.as_str()).unwrap_or("magiskboot");
    if args.len() < 2 {
        print_usage(prog);
    }

    let action = args[1].strip_prefix("--").unwrap_or(&args[1]);

    if action == "cleanup" {
        cmd_cleanup();
        return Ok(0);
    }

    if args.len() > 2 && action == "sha1" {
        cmd_sha1(&args[2])?;
        return Ok(0);
    }

    if args.len() > 2 && action == "split" {
        return bootimg::cmd_split(&args[2]);
    }

    if args.len() > 2 && action == "unpack" {
        let mut idx = 2usize;
        let mut skip_decomp = false;
        let mut dump_hdr = false;
        while idx < args.len() {
            let arg = &args[idx];
            if !arg.starts_with('-') || arg == "-" {
                break;
            }
            for ch in arg[1..].chars() {
                match ch {
                    'n' => skip_decomp = true,
                    'h' => dump_hdr = true,
                    _ => print_usage(prog),
                }
            }
            idx += 1;
        }
        if idx >= args.len() {
            print_usage(prog);
        }
        return bootimg::cmd_unpack(&args[idx], skip_decomp, dump_hdr);
    }

    if args.len() > 2 && action == "repack" {
        if args[2] == "-n" {
            if args.len() <= 3 {
                print_usage(prog);
            }
            let out = args.get(4).map(|s| s.as_str()).unwrap_or(NEW_BOOT);
            bootimg::cmd_repack(&args[3], out, true)?;
        } else {
            let out = args.get(3).map(|s| s.as_str()).unwrap_or(NEW_BOOT);
            bootimg::cmd_repack(&args[2], out, false)?;
        }
        return Ok(0);
    }

    if args.len() > 2 && action == "decompress" {
        let out = args.get(3).map(|s| s.as_str());
        compress::cmd_decompress(&args[2], out)?;
        return Ok(0);
    }

    if args.len() > 2 && action.starts_with("compress") {
        let method = if let Some(rest) = action.strip_prefix("compress=") {
            rest
        } else if action == "compress" {
            "gzip"
        } else {
            print_usage(prog);
        };
        let out = args.get(3).map(|s| s.as_str());
        compress::cmd_compress(method, &args[2], out)?;
        return Ok(0);
    }

    if args.len() > 4 && action == "hexpatch" {
        let ok = hexpatch::cmd_hexpatch(&args[2], &args[3], &args[4])?;
        return Ok(if ok { 0 } else { 1 });
    }

    if args.len() > 2 && action == "cpio" {
        return cpio::cmd_cpio(&args[2..]);
    }

    if args.len() > 3 && action == "dtb" {
        return dtb::cmd_dtb(&args[2..]);
    }

    print_usage(prog);
}

fn main() {
    match run() {
        Ok(code) => process::exit(code),
        Err(err) => {
            eprintln!("Error: {err}");
            process::exit(1);
        }
    }
}
