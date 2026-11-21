use std::env;
use std::fs::{File, OpenOptions};
use std::io::{Seek, SeekFrom};
use std::process;

use make_ext4fs_rs::sparse::SparseFile;

fn usage() {
    eprintln!("Usage: simg2img <sparse_image_files> <raw_image_file>");
}

fn main() {
    let args: Vec<String> = env::args().collect();
    if args.len() < 3 {
        usage();
        process::exit(255);
    }

    let out_path = &args[args.len() - 1];
    let mut out_file = OpenOptions::new()
        .write(true)
        .create(true)
        .truncate(true)
        .open(out_path)
        .unwrap_or_else(|_| {
            eprintln!("Cannot open output file {}", out_path);
            process::exit(255);
        });

    for in_path in &args[1..args.len() - 1] {
        let in_file = File::open(in_path).unwrap_or_else(|_| {
            eprintln!("Cannot open input file {}", in_path);
            process::exit(255);
        });
        let mut sf = SparseFile::import_file(in_file, true, false).unwrap_or_else(|_| {
            eprintln!("Failed to read sparse file");
            process::exit(255);
        });
        out_file.seek(SeekFrom::Start(0)).unwrap_or_else(|e| {
            eprintln!("lseek failed: {}", e);
            process::exit(1);
        });
        let cloned = out_file.try_clone().unwrap_or_else(|_| {
            eprintln!("Cannot write output file");
            process::exit(255);
        });
        sf.write_to_file(cloned, false, false, false)
            .unwrap_or_else(|_| {
                eprintln!("Cannot write output file");
                process::exit(255);
            });
    }
}
