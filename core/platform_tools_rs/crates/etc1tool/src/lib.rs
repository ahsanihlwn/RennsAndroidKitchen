pub mod etc1;

use std::fs::File;
use std::io::{self, BufReader, BufWriter, Read, Write};
use std::path::Path;

use png::{BitDepth, ColorType, Decoder, Encoder, Transformations};
use thiserror::Error;

use crate::etc1::{
    decode_image, encode_image, encoded_data_size, format_pkm_header, is_valid_pkm_header,
    pkm_get_height, pkm_get_width, ETC1_PKM_HEADER_SIZE,
};

#[derive(Debug, Error)]
pub enum Etc1ToolError {
    #[error("I/O error: {0}")]
    Io(#[from] io::Error),
    #[error("PNG decoding error: {0}")]
    PngDecode(#[from] png::DecodingError),
    #[error("PNG encoding error: {0}")]
    PngEncode(#[from] png::EncodingError),
    #[error("ETC1 error: {0}")]
    Etc1(#[from] etc1::Etc1Error),
    #[error("{0} is not a PNG file.")]
    NotAPngFile(String),
    #[error("Bad header PKM header for input file {0}")]
    BadPkmHeader(String),
    #[error("Output file has incorrect bounds: {w1}, {h1} != {w2}, {h2}")]
    OutputBoundsMismatch { w1: u32, h1: u32, w2: u32, h2: u32 },
    #[error("Could not change extension of input file name: {0}")]
    InvalidExtensionChange(String),
}

/// Reads a PNG image from `path` and converts it to tightly packed 8-bit RGB888 `(rgb, width, height)`.
pub fn read_png_rgb(path: &Path) -> Result<(Vec<u8>, u32, u32), Etc1ToolError> {
    let mut f = File::open(path)?;
    let mut sig = [0u8; 8];
    if f.read_exact(&mut sig).is_err() || sig != [0x89, b'P', b'N', b'G', 0x0d, 0x0a, 0x1a, 0x0a] {
        return Err(Etc1ToolError::NotAPngFile(path.display().to_string()));
    }
    drop(f);

    let file = File::open(path)?;
    let mut decoder = Decoder::new(BufReader::new(file));
    decoder.set_transformations(Transformations::EXPAND | Transformations::STRIP_16);
    let mut reader = decoder.read_info()?;
    let mut buf = vec![0u8; reader.output_buffer_size()];
    let info = reader.next_frame(&mut buf)?;
    let samples = &buf[..info.buffer_size()];

    let width = info.width;
    let height = info.height;
    let rgb = match (info.color_type, info.bit_depth) {
        (ColorType::Rgb, BitDepth::Eight) => samples.to_vec(),
        (ColorType::Rgba, BitDepth::Eight) => {
            let mut out = Vec::with_capacity((width as usize) * (height as usize) * 3);
            for px in samples.chunks_exact(4) {
                out.extend_from_slice(&px[..3]);
            }
            out
        }
        (ColorType::Grayscale, BitDepth::Eight) => {
            let mut out = Vec::with_capacity((width as usize) * (height as usize) * 3);
            for &g in samples {
                out.extend_from_slice(&[g, g, g]);
            }
            out
        }
        (ColorType::GrayscaleAlpha, BitDepth::Eight) => {
            let mut out = Vec::with_capacity((width as usize) * (height as usize) * 3);
            for px in samples.chunks_exact(2) {
                let g = px[0];
                out.extend_from_slice(&[g, g, g]);
            }
            out
        }
        (ct, bd) => {
            return Err(Etc1ToolError::NotAPngFile(format!(
                "{} (unsupported decoded format {:?} {:?})",
                path.display(),
                ct,
                bd
            )));
        }
    };

    Ok((rgb, width, height))
}

/// Writes tightly packed 8-bit RGB888 image data to a PNG file at `path`.
pub fn write_png_rgb(
    path: &Path,
    width: u32,
    height: u32,
    rgb: &[u8],
) -> Result<(), Etc1ToolError> {
    let file = File::create(path)?;
    let w = BufWriter::new(file);
    let mut encoder = Encoder::new(w, width, height);
    encoder.set_color(ColorType::Rgb);
    encoder.set_depth(BitDepth::Eight);
    let mut writer = encoder.write_header()?;
    writer.write_image_data(rgb)?;
    Ok(())
}

/// Reads and decodes a PKM file into tightly packed 8-bit RGB888 `(rgb, width, height)`.
pub fn read_pkm_rgb(path: &Path) -> Result<(Vec<u8>, u32, u32), Etc1ToolError> {
    let mut file = BufReader::new(File::open(path)?);
    let mut header = [0u8; ETC1_PKM_HEADER_SIZE];
    if file.read_exact(&mut header).is_err() || !is_valid_pkm_header(&header) {
        return Err(Etc1ToolError::BadPkmHeader(path.display().to_string()));
    }
    let width = pkm_get_width(&header);
    let height = pkm_get_height(&header);
    let enc_size = encoded_data_size(width, height);
    let mut encoded = vec![0u8; enc_size];
    file.read_exact(&mut encoded)?;
    let rgb = decode_image(&encoded, width, height, 3, width as usize * 3)?;
    Ok((rgb, width, height))
}

/// Computes the per-channel difference image matching AOSP `etc1tool --showDifference`:
/// `min((orig - decoded)^2 * 8, 255)`.
pub fn compute_diff_image(orig_rgb: &[u8], decoded_rgb: &[u8]) -> Vec<u8> {
    orig_rgb
        .iter()
        .zip(decoded_rgb.iter())
        .map(|(&o, &d)| {
            let diff = i32::from(o) - i32::from(d);
            (diff * diff * 8).min(255) as u8
        })
        .collect()
}

/// Replaces or appends `new_ext` (e.g. `".pkm"` or `".png"`) matching AOSP `changeExtension`.
pub fn default_output_path(infile: &str, new_ext: &str) -> Result<String, Etc1ToolError> {
    if infile == "."
        || infile == ".."
        || infile.ends_with("/.")
        || infile.ends_with("/..")
        || infile.ends_with("\\.")
        || infile.ends_with("\\..")
    {
        return Err(Etc1ToolError::InvalidExtensionChange(infile.to_string()));
    }

    let bytes = infile.as_bytes();
    let mut i = bytes.len();
    let mut split_at = bytes.len();
    while i > 0 {
        i -= 1;
        let b = bytes[i];
        if b == b'.' {
            split_at = i;
            break;
        }
        if b == b'/' || b == b'\\' {
            break;
        }
    }

    let mut out = String::with_capacity(split_at + new_ext.len());
    out.push_str(&infile[..split_at]);
    out.push_str(new_ext);
    Ok(out)
}

/// Encodes `infile` (PNG) to `outfile` (PKM or raw ETC1), optionally writing a difference PNG.
pub fn encode_file(
    infile: &Path,
    outfile: &Path,
    emit_header: bool,
    diff_file: Option<&Path>,
) -> Result<(), Etc1ToolError> {
    let (orig_rgb, width, height) = read_png_rgb(infile)?;
    let encoded = encode_image(&orig_rgb, width, height, 3, width as usize * 3)?;

    {
        let mut out = BufWriter::new(File::create(outfile)?);
        if emit_header {
            out.write_all(&format_pkm_header(width, height))?;
        }
        out.write_all(&encoded)?;
        out.flush()?;
    }

    if let Some(diff_path) = diff_file {
        let (dec_rgb, dec_w, dec_h) = read_pkm_rgb(outfile)?;
        if dec_w != width || dec_h != height {
            return Err(Etc1ToolError::OutputBoundsMismatch {
                w1: dec_w,
                h1: dec_h,
                w2: width,
                h2: height,
            });
        }
        let diff_rgb = compute_diff_image(&orig_rgb, &dec_rgb);
        write_png_rgb(diff_path, width, height, &diff_rgb)?;
    }

    Ok(())
}

/// Decodes `infile` (PKM) to `outfile` (PNG).
pub fn decode_file(infile: &Path, outfile: &Path) -> Result<(), Etc1ToolError> {
    let (rgb, width, height) = read_pkm_rgb(infile)?;
    write_png_rgb(outfile, width, height, &rgb)?;
    Ok(())
}

#[derive(Clone, Copy, PartialEq, Eq)]
enum CliMode {
    Encode,
    EncodeNoHeader,
    Decode,
}

fn print_usage(prog: &str) {
    eprintln!(
        "\
{prog} infile [--help | --encode | --encodeNoHeader | --decode] [--showDifference difffile] [-o outfile]
\tDefault is --encode
\t\t--help           print this usage information.
\t\t--encode         create an ETC1 file from a PNG file.
\t\t--encodeNoHeader create a raw ETC1 data file (without a header) from a PNG file.
\t\t--decode         create a PNG file from an ETC1 file.
\t\t--showDifference difffile    Write difference between original and encoded
\t\t                             image to difffile. (Only valid when encoding).
\tIf outfile is not specified, an outfile path is constructed from infile,
\twith the apropriate suffix (.pkm or .png)."
    );
}

/// Top-level CLI runner for `etc1tool` (expects arguments WITHOUT `argv[0]`).
pub fn run_cli(args: &[String]) -> i32 {
    let prog = "etc1tool";
    if args.is_empty() {
        print_usage(prog);
        return 255;
    }

    let mut infile: Option<String> = None;
    let mut outfile: Option<String> = None;
    let mut difffile: Option<String> = None;
    let mut mode: Option<CliMode> = None;

    let mut i = 0usize;
    while i < args.len() {
        let arg = &args[i];
        if arg.starts_with('-') {
            if arg.starts_with("--") {
                match arg.as_str() {
                    "--help" => {
                        print_usage(prog);
                        return 1;
                    }
                    "--encode" => {
                        if mode.is_some() {
                            eprintln!(
                                "At most one occurrence of --encode --encodeNoHeader or --decode is allowed."
                            );
                            print_usage(prog);
                            return 255;
                        }
                        mode = Some(CliMode::Encode);
                    }
                    "--encodeNoHeader" => {
                        if mode.is_some() {
                            eprintln!(
                                "At most one occurrence of --encode --encodeNoHeader or --decode is allowed."
                            );
                            print_usage(prog);
                            return 255;
                        }
                        mode = Some(CliMode::EncodeNoHeader);
                    }
                    "--decode" => {
                        if mode.is_some() {
                            eprintln!(
                                "At most one occurrence of --encode --encodeNoHeader or --decode is allowed."
                            );
                            print_usage(prog);
                            return 255;
                        }
                        mode = Some(CliMode::Decode);
                    }
                    "--showDifference" => {
                        if difffile.is_some() {
                            eprintln!("Only one --showDifference option allowed.");
                            print_usage(prog);
                            return 255;
                        }
                        i += 1;
                        if i >= args.len() {
                            eprintln!("Expected difffile after --showDifference");
                            print_usage(prog);
                            return 255;
                        }
                        difffile = Some(args[i].clone());
                    }
                    _ => {
                        eprintln!("Unknown flag {arg}");
                        print_usage(prog);
                        return 255;
                    }
                }
            } else if arg == "-o" {
                if outfile.is_some() {
                    eprintln!("Only one -o option allowed.");
                    print_usage(prog);
                    return 255;
                }
                i += 1;
                if i >= args.len() {
                    eprintln!("Expected outfile after -o");
                    print_usage(prog);
                    return 255;
                }
                outfile = Some(args[i].clone());
            } else {
                eprintln!("Unknown flag {arg}");
                print_usage(prog);
                return 255;
            }
        } else {
            if let Some(prev) = &infile {
                eprintln!("Already have input file {prev}, don't know what to do with {arg}");
                print_usage(prog);
                return 255;
            }
            infile = Some(arg.clone());
        }
        i += 1;
    }

    let selected_mode = mode.unwrap_or(CliMode::Encode);
    let is_encode = matches!(selected_mode, CliMode::Encode | CliMode::EncodeNoHeader);
    if !is_encode && difffile.is_some() {
        eprintln!("--showDifference is only valid when encoding.");
        print_usage(prog);
        return 255;
    }

    let Some(in_path_str) = infile else {
        print_usage(prog);
        return 255;
    };

    let out_path_str = match outfile {
        Some(p) => p,
        None => {
            let ext = if is_encode { ".pkm" } else { ".png" };
            match default_output_path(&in_path_str, ext) {
                Ok(s) => s,
                Err(e) => {
                    eprintln!("{e}");
                    return 255;
                }
            }
        }
    };

    let res = match selected_mode {
        CliMode::Encode => encode_file(
            Path::new(&in_path_str),
            Path::new(&out_path_str),
            true,
            difffile.as_deref().map(Path::new),
        ),
        CliMode::EncodeNoHeader => encode_file(
            Path::new(&in_path_str),
            Path::new(&out_path_str),
            false,
            difffile.as_deref().map(Path::new),
        ),
        CliMode::Decode => decode_file(Path::new(&in_path_str), Path::new(&out_path_str)),
    };

    if let Err(e) = res {
        eprintln!("{e}");
        return 1;
    }
    0
}

#[cfg(test)]
mod tests {
    use tempfile::tempdir;

    use super::*;

    #[test]
    fn test_default_output_path() {
        assert_eq!(default_output_path("foo.png", ".pkm").unwrap(), "foo.pkm");
        assert_eq!(default_output_path("foo", ".pkm").unwrap(), "foo.pkm");
        assert_eq!(
            default_output_path("dir.with.dot/img", ".pkm").unwrap(),
            "dir.with.dot/img.pkm"
        );
        assert_eq!(
            default_output_path("dir\\img.pkm", ".png").unwrap(),
            "dir\\img.png"
        );
        assert!(default_output_path(".", ".pkm").is_err());
        assert!(default_output_path("dir/..", ".pkm").is_err());
    }

    #[test]
    fn test_compute_diff_image() {
        let orig = [10u8, 100, 200];
        let dec = [12u8, 95, 210];
        // (2^2)*8=32, (5^2)*8=200, (10^2)*8=800->255
        assert_eq!(compute_diff_image(&orig, &dec), [32, 200, 255]);
    }

    #[test]
    fn test_encode_decode_roundtrip_files() {
        let dir = tempdir().unwrap();
        let in_png = dir.path().join("in.png");
        let out_pkm = dir.path().join("out.pkm");
        let out_png = dir.path().join("dec.png");
        let diff_png = dir.path().join("diff.png");

        let w = 8u32;
        let h = 8u32;
        let mut rgb = vec![0u8; (w * h * 3) as usize];
        for (i, b) in rgb.iter_mut().enumerate() {
            *b = ((i * 23 + 11) & 0xff) as u8;
        }
        write_png_rgb(&in_png, w, h, &rgb).unwrap();

        encode_file(&in_png, &out_pkm, true, Some(&diff_png)).unwrap();
        decode_file(&out_pkm, &out_png).unwrap();

        let (dec_rgb, dw, dh) = read_png_rgb(&out_png).unwrap();
        assert_eq!((dw, dh), (w, h));
        assert_eq!(dec_rgb.len(), rgb.len());

        let (diff_rgb, fw, fh) = read_png_rgb(&diff_png).unwrap();
        assert_eq!((fw, fh), (w, h));
        assert_eq!(diff_rgb, compute_diff_image(&rgb, &dec_rgb));
    }
}
