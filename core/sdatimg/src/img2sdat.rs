use std::path::{Path, PathBuf};

use crate::blockimgdiff::{BlockImageDiff, BlockOptions, EmptyImage, Image};
use crate::error::{Error, Result};
use crate::rangelib::RangeSet;
use crate::sparse_img::{RawImage, SparseImage, is_sparse_image};

pub const VERSION: &str = "1.7";
pub const DEFAULT_VERSION: u8 = 4;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum InputFormat {
    #[default]
    Auto,
    Sparse,
    Raw,
}

#[derive(Debug, Clone)]
pub struct Img2SdatOptions {
    pub outdir: PathBuf,
    pub version: u8,
    pub prefix: String,
    pub input_format: InputFormat,
    pub file_map: Option<PathBuf>,
    pub clobbered_blocks: Option<String>,
    pub src_image: Option<PathBuf>,
    pub src_file_map: Option<PathBuf>,
    pub cache_size: Option<u64>,
    pub stash_threshold: f64,
    pub threads: Option<usize>,
    pub disable_imgdiff: bool,
    pub quiet: bool,
}

impl Default for Img2SdatOptions {
    fn default() -> Self {
        Self {
            outdir: PathBuf::from("."),
            version: DEFAULT_VERSION,
            prefix: "system".to_string(),
            input_format: InputFormat::Auto,
            file_map: None,
            clobbered_blocks: None,
            src_image: None,
            src_file_map: None,
            cache_size: None,
            stash_threshold: 0.8,
            threads: None,
            disable_imgdiff: false,
            quiet: false,
        }
    }
}

fn open_image(
    path: &Path,
    format: InputFormat,
    file_map: Option<&Path>,
    clobbered: Option<&str>,
    quiet: bool,
) -> Result<Box<dyn Image>> {
    let use_raw = match format {
        InputFormat::Sparse => false,
        InputFormat::Raw => true,
        InputFormat::Auto => {
            if is_sparse_image(path)? {
                false
            } else {
                let len = std::fs::metadata(path)?.len();
                if len > 0 && len % 4096 == 0 {
                    if !quiet {
                        println!(
                            "Input '{}' is not a sparse image; treating as raw 4096-byte block image.",
                            path.display()
                        );
                    }
                    true
                } else if len > 0 {
                    return Err(Error::InvalidData(format!(
                        "Input '{}' is neither an Android sparse image (magic 0xED26FF3A) nor a 4096-byte block-aligned raw image (size: {len} bytes)",
                        path.display()
                    )));
                } else {
                    false
                }
            }
        }
    };

    if use_raw {
        let eff_clob = clobbered.unwrap_or("0");
        let img = RawImage::open(path, file_map, Some(eff_clob), quiet)?;
        Ok(Box::new(img))
    } else {
        let mut img = SparseImage::open(path, None, clobbered, true, quiet)?;
        let default_clob = if clobbered.is_none() {
            let block0 = RangeSet::from_range(0, 1);
            if img.care_map.overlaps(&block0) {
                block0
            } else {
                RangeSet::new()
            }
        } else {
            img.clobbered_blocks.clone()
        };
        img.load_file_block_map(file_map, &default_clob)?;
        Ok(Box::new(img))
    }
}

/// Convert a sparse (or raw) image into `<outdir>/<prefix>.{new.dat,transfer.list,patch.dat}`.
pub fn img2sdat(input_image: &Path, opts: &Img2SdatOptions) -> Result<PathBuf> {
    if !opts.quiet {
        println!("sdatimg img2sdat v{VERSION}");
    }

    if !opts.outdir.is_dir() {
        std::fs::create_dir_all(&opts.outdir)?;
    }

    let outdir_prefix = opts.outdir.join(&opts.prefix);

    let mut tgt_img = open_image(
        input_image,
        opts.input_format,
        opts.file_map.as_deref(),
        opts.clobbered_blocks.as_deref(),
        opts.quiet,
    )?;

    let mut src_img: Box<dyn Image> = if let Some(src_path) = &opts.src_image {
        open_image(
            src_path,
            opts.input_format,
            opts.src_file_map.as_deref(),
            opts.clobbered_blocks.as_deref(),
            opts.quiet,
        )?
    } else {
        Box::new(EmptyImage::new())
    };

    let block_opts = BlockOptions {
        cache_size: opts.cache_size,
        stash_threshold: opts.stash_threshold,
    };

    let mut diff = BlockImageDiff::new(
        tgt_img.as_mut(),
        src_img.as_mut(),
        opts.version,
        opts.threads,
        opts.disable_imgdiff,
        block_opts,
    )?;
    diff.quiet = opts.quiet;
    diff.compute(&outdir_prefix)?;

    if !opts.quiet {
        println!("Done! Output files: {}", opts.outdir.display());
    }

    Ok(outdir_prefix)
}
