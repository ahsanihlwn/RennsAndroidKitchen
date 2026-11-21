use std::env;
use std::path::Path;
use std::process;

use ext_cli::check::check_ext;
use ext_cli::unpack::unpack_image;

fn print_help() -> ! {
    println!("EXT4 Tool CLI");
    println!("Usage:");
    println!("  --read   <path/to/image.img>");
    println!("  --unpack <path/to/image.img>");
    process::exit(1);
}

fn main() {
    let args: Vec<String> = env::args().collect();
    if args.len() < 3 {
        print_help();
    }

    let mut mode: Option<&str> = None;
    let mut img: Option<&str> = None;
    let mut i = 1;
    while i < args.len() {
        match args[i].as_str() {
            "--read" => mode = Some("--read"),
            "--unpack" => mode = Some("--unpack"),
            "--x" => {
                if i + 1 < args.len() {
                    i += 1;
                    img = Some(args[i].as_str());
                }
            }
            arg if !arg.starts_with('-') && img.is_none() => {
                img = Some(arg);
            }
            _ => {}
        }
        i += 1;
    }

    let (Some(cmd), Some(img_str)) = (mode, img) else {
        print_help();
    };

    let img_path = Path::new(img_str);
    if !img_path.exists() {
        println!("[ERR] File not found: {}", img_str);
        process::exit(2);
    }

    match cmd {
        "--read" => {
            check_ext(img_path);
        }
        "--unpack" => match unpack_image(img_path) {
            Ok(out_dir) => {
                println!("[OK] Unpack finished");
                println!("Output: {}", out_dir.display());
            }
            Err(e) => {
                println!("[ERR] Unpack failed: {}", e);
                process::exit(1);
            }
        },
        _ => print_help(),
    }
}
