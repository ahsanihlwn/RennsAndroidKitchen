use std::path::PathBuf;
use std::process;

use clap::{Parser, Subcommand, ValueEnum};

use sdatimg::{
    Img2SdatOptions, InputFormat, Sdat2ImgOptions, SparseImage, img2sdat, raw_to_sparse, sdat2img,
};

#[derive(Parser)]
#[command(
    name = "sdatimg",
    version,
    about = "Unified tool for Android block-OTA images (sdat2img & img2sdat)"
)]
struct Cli {
    #[command(subcommand)]
    command: Commands,
}

#[derive(Copy, Clone, PartialEq, Eq, ValueEnum)]
enum FormatArg {
    Auto,
    Sparse,
    Raw,
}

impl From<FormatArg> for InputFormat {
    fn from(value: FormatArg) -> Self {
        match value {
            FormatArg::Auto => InputFormat::Auto,
            FormatArg::Sparse => InputFormat::Sparse,
            FormatArg::Raw => InputFormat::Raw,
        }
    }
}

#[derive(Subcommand)]
enum Commands {
    /// Convert <transfer_list> and <new_dat> into a partition image
    Sdat2img {
        /// Path to *.transfer.list
        transfer_list: PathBuf,
        /// Path to *.new.dat
        new_dat: PathBuf,
        /// Output image path (default: system.img)
        #[arg(default_value = "system.img")]
        output_img: PathBuf,
        /// Output an Android sparse image instead of a raw image
        #[arg(long)]
        sparse: bool,
        /// Do not overwrite if output image already exists
        #[arg(long)]
        no_clobber: bool,
        /// Suppress progress output
        #[arg(short, long)]
        quiet: bool,
    },

    /// Convert a sparse (or raw) partition image into transfer.list, new.dat, and patch.dat
    Img2sdat {
        /// Input system image (sparse or raw)
        image: PathBuf,
        /// Output directory
        #[arg(short = 'o', long = "outdir", default_value = ".")]
        outdir: PathBuf,
        /// transfer.list format version (1..4)
        #[arg(short = 'v', long = "version", default_value_t = 4, value_parser = clap::value_parser!(u8).range(1..=4))]
        version: u8,
        /// Output file basename (e.g. "system" -> system.new.dat)
        #[arg(short = 'p', long = "prefix", default_value = "system")]
        prefix: String,
        /// Input image format (auto, sparse, raw)
        #[arg(long, value_enum, default_value_t = FormatArg::Auto)]
        format: FormatArg,
        /// Optional block file map path
        #[arg(long)]
        file_map: Option<PathBuf>,
        /// Optional clobbered blocks range string (default: "0")
        #[arg(long)]
        clobbered: Option<String>,
        /// Optional source image for incremental block diff
        #[arg(long)]
        src: Option<PathBuf>,
        /// Optional source image block file map
        #[arg(long)]
        src_file_map: Option<PathBuf>,
        /// Optional /cache partition size in bytes for stash budgeting
        #[arg(long)]
        cache_size: Option<u64>,
        /// Stash threshold fraction of cache_size (default: 0.8)
        #[arg(long, default_value_t = 0.8)]
        stash_threshold: f64,
        /// Worker threads for incremental patch computation
        #[arg(long)]
        threads: Option<usize>,
        /// Disable imgdiff -z for apk/jar/zip files
        #[arg(long)]
        disable_imgdiff: bool,
        /// Suppress progress output
        #[arg(short, long)]
        quiet: bool,
    },

    /// Convert an Android sparse image to a raw image
    Simg2img {
        /// Input sparse image
        sparse_img: PathBuf,
        /// Output raw image
        raw_img: PathBuf,
    },

    /// Convert a raw image to an Android sparse image
    Img2simg {
        /// Input raw image
        raw_img: PathBuf,
        /// Output sparse image
        sparse_img: PathBuf,
    },
}

fn run() -> sdatimg::Result<()> {
    let cli = Cli::parse();
    match cli.command {
        Commands::Sdat2img {
            transfer_list,
            new_dat,
            output_img,
            sparse,
            no_clobber,
            quiet,
        } => {
            let opts = Sdat2ImgOptions {
                sparse_output: sparse,
                no_clobber,
                quiet,
            };
            sdat2img(&transfer_list, &new_dat, &output_img, &opts)
        }
        Commands::Img2sdat {
            image,
            outdir,
            version,
            prefix,
            format,
            file_map,
            clobbered,
            src,
            src_file_map,
            cache_size,
            stash_threshold,
            threads,
            disable_imgdiff,
            quiet,
        } => {
            let opts = Img2SdatOptions {
                outdir,
                version,
                prefix,
                input_format: format.into(),
                file_map,
                clobbered_blocks: clobbered,
                src_image: src,
                src_file_map,
                cache_size,
                stash_threshold,
                threads,
                disable_imgdiff,
                quiet,
            };
            img2sdat(&image, &opts)?;
            Ok(())
        }
        Commands::Simg2img {
            sparse_img,
            raw_img,
        } => {
            let mut img = SparseImage::open(&sparse_img, None, None, true, false)?;
            img.to_raw_image(&raw_img)
        }
        Commands::Img2simg {
            raw_img,
            sparse_img,
        } => raw_to_sparse(&raw_img, &sparse_img),
    }
}

fn main() {
    if let Err(err) = run() {
        eprintln!("{err}");
        process::exit(1);
    }
}
