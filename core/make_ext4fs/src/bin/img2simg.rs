use std::env;
use std::fs::{File, OpenOptions};
use std::process;

use make_ext4fs_rs::sparse::SparseFile;

fn usage() {
    eprintln!("Usage: img2simg [-s] <raw_image_file> <sparse_image_file> [<block_size>]");
}

fn main() {
    let raw_args: Vec<String> = env::args().collect();
    let mut pos_args = Vec::with_capacity(raw_args.len());
    if let Some(prog) = raw_args.first() {
        pos_args.push(prog.clone());
    }
    let mut i = 1;
    let mut end_of_opts = false;
    while i < raw_args.len() {
        let a = &raw_args[i];
        if !end_of_opts && a == "--" {
            end_of_opts = true;
            i += 1;
            continue;
        }
        if !end_of_opts && a.starts_with('-') && a.len() > 1 {
            if a == "-s" {
                i += 1;
                continue;
            }
            usage();
            process::exit(255);
        }
        pos_args.push(a.clone());
        i += 1;
    }

    if pos_args.len() < 3 || pos_args.len() > 4 {
        usage();
        process::exit(255);
    }

    let block_size: u32 = if pos_args.len() == 4 {
        pos_args[3].parse::<i32>().unwrap_or(0) as u32
    } else {
        4096
    };

    if block_size < 1024 || block_size % 4 != 0 {
        usage();
        process::exit(255);
    }

    let in_file = File::open(&pos_args[1]).unwrap_or_else(|_| {
        eprintln!("Cannot open input file {}", pos_args[1]);
        process::exit(255);
    });

    let mut sf = SparseFile::read_normal_file(in_file, block_size).unwrap_or_else(|_| {
        eprintln!("Failed to read file");
        process::exit(255);
    });
    sf.verbose = true;

    if pos_args[2] == "-" {
        let stdout = std::io::stdout();
        sf.write_to_stream(stdout.lock(), false, true, false)
            .unwrap_or_else(|_| {
                eprintln!("Failed to write sparse file");
                process::exit(255);
            });
    } else {
        let out_file = OpenOptions::new()
            .write(true)
            .create(true)
            .truncate(true)
            .open(&pos_args[2])
            .unwrap_or_else(|_| {
                eprintln!("Cannot open output file {}", pos_args[2]);
                process::exit(255);
            });
        sf.write_to_file(out_file, false, true, false)
            .unwrap_or_else(|_| {
                eprintln!("Failed to write sparse file");
                process::exit(255);
            });
    }
}
