use std::env;
use std::fs::{File, OpenOptions};
use std::process;

use make_ext4fs_rs::sparse::SparseFile;

fn usage() {
    eprintln!("Usage: img2simg <raw_image_file> <sparse_image_file> [<block_size>]");
}

fn main() {
    let args: Vec<String> = env::args().collect();
    if args.len() < 3 || args.len() > 4 {
        usage();
        process::exit(255);
    }

    let block_size: u32 = if args.len() == 4 {
        args[3].parse::<i32>().unwrap_or(0) as u32
    } else {
        4096
    };

    if block_size < 1024 || block_size % 4 != 0 {
        usage();
        process::exit(255);
    }

    let in_file = File::open(&args[1]).unwrap_or_else(|_| {
        eprintln!("Cannot open input file {}", args[1]);
        process::exit(255);
    });

    let mut sf = SparseFile::read_normal_file(in_file, block_size).unwrap_or_else(|_| {
        eprintln!("Failed to read file");
        process::exit(255);
    });
    sf.verbose = true;

    if args[2] == "-" {
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
            .open(&args[2])
            .unwrap_or_else(|_| {
                eprintln!("Cannot open output file {}", args[2]);
                process::exit(255);
            });
        sf.write_to_file(out_file, false, true, false)
            .unwrap_or_else(|_| {
                eprintln!("Failed to write sparse file");
                process::exit(255);
            });
    }
}
