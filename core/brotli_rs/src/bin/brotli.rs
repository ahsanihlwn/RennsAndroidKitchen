//! Brotli command-line utility.

use std::env;
use std::fs::{self, File, FileTimes, OpenOptions};
use std::io::{self, IsTerminal, Read, Write};
use std::path::Path;
use std::process;
use std::sync::{Arc, Mutex};
use std::time::Instant;

use brotli_rs::common::{
    BROTLI_LARGE_MAX_WBITS, BROTLI_MAX_DISTANCE, BROTLI_MAX_QUALITY, BROTLI_MAX_WINDOW_BITS,
    BROTLI_MIN_QUALITY, BROTLI_MIN_WINDOW_BITS, BROTLI_VERSION_MAJOR, BROTLI_VERSION_MINOR,
    BROTLI_VERSION_PATCH, BrotliSharedDictionaryType, brotli_max_backward_limit,
};
use brotli_rs::dec::{BrotliDecoderParameter, BrotliDecoderResult, BrotliDecoderState};
use brotli_rs::enc::{
    BrotliEncoderOperation, BrotliEncoderParameter, BrotliEncoderState, PreparedDictionary,
    brotli_encoder_prepare_raw_dictionary,
};

const DEFAULT_LGWIN: i32 = 24;
const DEFAULT_SUFFIX: &str = ".br";
const MAX_OPTIONS: usize = 24;
const MAX_COMMENT_LEN: usize = 80;
const FILE_BUFFER_SIZE: usize = 1 << 19;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum Command {
    Compress,
    Decompress,
    Help,
    Invalid,
    TestIntegrity,
    Noop,
    Version,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum CommentState {
    Init,
    Read,
    Ok,
    Bad,
}

struct CommentCheck {
    expected: Vec<u8>,
    pos: usize,
    state: CommentState,
}

struct CliContext {
    quality: i32,
    lgwin: i32,
    verbosity: i32,
    force_overwrite: bool,
    junk_source: bool,
    reject_uncompressible: bool,
    copy_stat: bool,
    write_to_stdout: bool,
    test_integrity: bool,
    decompress: bool,
    allow_concatenated: bool,
    output_path: Option<String>,
    dictionary_path: Option<String>,
    suffix: String,
    comment: Vec<u8>,
    input_paths: Vec<Option<String>>,
    dictionary: Option<Vec<u8>>,
    prepared_dictionary: Option<Arc<PreparedDictionary>>,
    iterator_error: bool,
}

impl Default for CliContext {
    fn default() -> Self {
        Self {
            quality: 11,
            lgwin: -1,
            verbosity: 0,
            force_overwrite: false,
            junk_source: false,
            reject_uncompressible: false,
            copy_stat: true,
            write_to_stdout: false,
            test_integrity: false,
            decompress: false,
            allow_concatenated: false,
            output_path: None,
            dictionary_path: None,
            suffix: DEFAULT_SUFFIX.to_string(),
            comment: Vec::new(),
            input_paths: Vec::new(),
            dictionary: None,
            prepared_dictionary: None,
            iterator_error: false,
        }
    }
}

fn parse_base64(s: &str) -> Option<Vec<u8>> {
    let mut out = Vec::new();
    let mut bit_count = 0usize;
    let mut bits = 0u32;
    let mut padding_count = 0usize;
    for b in s.bytes() {
        if matches!(b, 9 | 10 | 13 | b' ') {
            continue;
        }
        if b == b'=' {
            padding_count += 1;
            continue;
        }
        if padding_count != 0 {
            return None;
        }
        let sextet: u32 = match b {
            b'+' | b'-' => 62,
            b'/' | b'_' => 63,
            b'A'..=b'Z' => (b - b'A') as u32,
            b'a'..=b'z' => (b - b'a' + 26) as u32,
            b'0'..=b'9' => (b - b'0' + 52) as u32,
            _ => return None,
        };
        bits = (bits << 6) | sextet;
        bit_count += 6;
        if bit_count >= 8 {
            if out.len() >= MAX_COMMENT_LEN {
                return None;
            }
            bit_count -= 8;
            out.push(((bits >> bit_count) & 0xFF) as u8);
        }
    }
    if padding_count > 2 {
        return None;
    }
    Some(out)
}

fn parse_int(s: &str, low: i32, high: i32) -> Option<i32> {
    let bytes = s.as_bytes();
    if bytes.is_empty() || bytes.len() > 5 {
        return None;
    }
    if bytes.len() > 1 && bytes[0] == b'0' {
        return None;
    }
    let mut value = 0i32;
    for &c in bytes {
        if !c.is_ascii_digit() {
            return None;
        }
        value = 10 * value + ((c - b'0') as i32);
    }
    if value < low || value > high {
        return None;
    }
    Some(value)
}

fn file_name_part(path: &str) -> &str {
    let mut p = path;
    if let Some(idx) = p.rfind('/') {
        p = &p[idx + 1..];
    }
    if let Some(idx) = p.rfind('\\') {
        p = &p[idx + 1..];
    }
    p
}

fn check_alias(argv0: &str, alias: &str) -> bool {
    let name = file_name_part(argv0);
    if let Some(rest) = name.strip_prefix(alias) {
        rest.is_empty() || rest.starts_with('.')
    } else {
        false
    }
}

fn parse_params(args: &[String], ctx: &mut CliContext) -> Command {
    let mut command_set = false;
    let mut quality_set = false;
    let mut output_set = false;
    let mut keep_set = false;
    let mut squash_set = false;
    let mut lgwin_set = false;
    let mut suffix_set = false;
    let mut after_dash_dash = false;
    let mut comment_set = false;
    let mut concatenated_set = false;
    let mut option_count = 0usize;
    let mut command = Command::Compress;

    if !args.is_empty() {
        if check_alias(&args[0], "brcat") {
            command_set = true;
            command = Command::Decompress;
            concatenated_set = true;
            ctx.allow_concatenated = true;
            output_set = true;
            ctx.write_to_stdout = true;
        } else if check_alias(&args[0], "unbrotli") {
            command_set = true;
            command = Command::Decompress;
        }
    }

    let mut i = 1usize;
    while i < args.len() {
        let arg = &args[i];
        if option_count > MAX_OPTIONS - 2 {
            eprintln!("too many options passed");
            return Command::Invalid;
        }

        if arg.is_empty() {
            option_count += 1;
            i += 1;
            continue;
        }

        if after_dash_dash || !arg.starts_with('-') || arg.len() == 1 {
            if arg == "-" {
                ctx.input_paths.push(None);
            } else {
                ctx.input_paths.push(Some(arg.clone()));
            }
            i += 1;
            continue;
        }

        option_count += 1;

        if arg == "--" {
            after_dash_dash = true;
            i += 1;
            continue;
        }

        if !arg.starts_with("--") {
            let chars: Vec<char> = arg[1..].chars().collect();
            for (j, &c) in chars.iter().enumerate() {
                match c {
                    '0'..='9' => {
                        if quality_set {
                            eprintln!("quality already set");
                            return Command::Invalid;
                        }
                        quality_set = true;
                        ctx.quality = (c as i32) - ('0' as i32);
                    }
                    'c' => {
                        if output_set {
                            eprintln!("write to standard output already set");
                            return Command::Invalid;
                        }
                        output_set = true;
                        ctx.write_to_stdout = true;
                    }
                    'd' => {
                        if command_set {
                            eprintln!("command already set when parsing -d");
                            return Command::Invalid;
                        }
                        command_set = true;
                        command = Command::Decompress;
                    }
                    'f' => {
                        if ctx.force_overwrite {
                            eprintln!("force output overwrite already set");
                            return Command::Invalid;
                        }
                        ctx.force_overwrite = true;
                    }
                    'h' => return Command::Help,
                    'j' | 'k' => {
                        if keep_set {
                            eprintln!("argument --rm / -j or --keep / -k already set");
                            return Command::Invalid;
                        }
                        keep_set = true;
                        ctx.junk_source = c == 'j';
                    }
                    'n' => {
                        if !ctx.copy_stat {
                            eprintln!("argument --no-copy-stat / -n already set");
                            return Command::Invalid;
                        }
                        ctx.copy_stat = false;
                    }
                    's' => {
                        if squash_set {
                            eprintln!("argument --squash / -s already set");
                            return Command::Invalid;
                        }
                        squash_set = true;
                        ctx.reject_uncompressible = true;
                    }
                    't' => {
                        if command_set {
                            eprintln!("command already set when parsing -t");
                            return Command::Invalid;
                        }
                        command_set = true;
                        command = Command::TestIntegrity;
                    }
                    'v' => {
                        if ctx.verbosity > 0 {
                            eprintln!("argument --verbose / -v already set");
                            return Command::Invalid;
                        }
                        ctx.verbosity = 1;
                    }
                    'K' => {
                        if concatenated_set {
                            eprintln!("argument -K / --concatenated already set");
                            return Command::Invalid;
                        }
                        concatenated_set = true;
                        ctx.allow_concatenated = true;
                    }
                    'V' => return Command::Version,
                    'Z' => {
                        if quality_set {
                            eprintln!("quality already set");
                            return Command::Invalid;
                        }
                        quality_set = true;
                        ctx.quality = 11;
                    }
                    'o' | 'q' | 'w' | 'C' | 'D' | 'S' => {
                        if j + 1 != chars.len() {
                            eprintln!("expected parameter for argument -{}", c);
                            return Command::Invalid;
                        }
                        i += 1;
                        if i >= args.len() || args[i].is_empty() {
                            eprintln!("expected parameter for argument -{}", c);
                            return Command::Invalid;
                        }
                        option_count += 1;
                        let val = &args[i];
                        match c {
                            'o' => {
                                if output_set {
                                    eprintln!("write to standard output already set (-o)");
                                    return Command::Invalid;
                                }
                                output_set = true;
                                ctx.output_path = Some(val.clone());
                            }
                            'q' => {
                                if quality_set {
                                    eprintln!("quality already set");
                                    return Command::Invalid;
                                }
                                if let Some(q) = parse_int(val, BROTLI_MIN_QUALITY, BROTLI_MAX_QUALITY) {
                                    quality_set = true;
                                    ctx.quality = q;
                                } else {
                                    eprintln!("error parsing quality value [{}]", val);
                                    return Command::Invalid;
                                }
                            }
                            'w' => {
                                if lgwin_set {
                                    eprintln!("lgwin parameter already set");
                                    return Command::Invalid;
                                }
                                if let Some(w) = parse_int(val, 0, BROTLI_MAX_WINDOW_BITS) {
                                    lgwin_set = true;
                                    ctx.lgwin = w;
                                } else {
                                    eprintln!("error parsing lgwin value [{}]", val);
                                    return Command::Invalid;
                                }
                                if ctx.lgwin != 0 && ctx.lgwin < BROTLI_MIN_WINDOW_BITS {
                                    eprintln!(
                                        "lgwin parameter ({}) smaller than the minimum ({})",
                                        ctx.lgwin, BROTLI_MIN_WINDOW_BITS
                                    );
                                    return Command::Invalid;
                                }
                            }
                            'C' => {
                                if comment_set {
                                    eprintln!("comment already set");
                                    return Command::Invalid;
                                }
                                if let Some(c_bytes) = parse_base64(val) {
                                    ctx.comment = c_bytes;
                                    comment_set = true;
                                } else {
                                    eprintln!("invalid base64-encoded comment");
                                    return Command::Invalid;
                                }
                            }
                            'D' => {
                                if ctx.dictionary_path.is_some() {
                                    eprintln!("dictionary path already set");
                                    return Command::Invalid;
                                }
                                ctx.dictionary_path = Some(val.clone());
                            }
                            'S' => {
                                if suffix_set {
                                    eprintln!("suffix already set");
                                    return Command::Invalid;
                                }
                                suffix_set = true;
                                ctx.suffix = val.clone();
                            }
                            _ => unreachable!(),
                        }
                        break;
                    }
                    _ => {
                        eprintln!("invalid argument -{}", c);
                        return Command::Invalid;
                    }
                }
            }
        } else {
            let long_opt = &arg[2..];
            match long_opt {
                "best" => {
                    if quality_set {
                        eprintln!("quality already set");
                        return Command::Invalid;
                    }
                    quality_set = true;
                    ctx.quality = 11;
                }
                "concatenated" => {
                    if concatenated_set {
                        eprintln!("argument -K / --concatenated already set");
                        return Command::Invalid;
                    }
                    concatenated_set = true;
                    ctx.allow_concatenated = true;
                }
                "decompress" => {
                    if command_set {
                        eprintln!("command already set when parsing --decompress");
                        return Command::Invalid;
                    }
                    command_set = true;
                    command = Command::Decompress;
                }
                "force" => {
                    if ctx.force_overwrite {
                        eprintln!("force output overwrite already set");
                        return Command::Invalid;
                    }
                    ctx.force_overwrite = true;
                }
                "help" => return Command::Help,
                "keep" => {
                    if keep_set {
                        eprintln!("argument --rm / -j or --keep / -k already set");
                        return Command::Invalid;
                    }
                    keep_set = true;
                    ctx.junk_source = false;
                }
                "no-copy-stat" => {
                    if !ctx.copy_stat {
                        eprintln!("argument --no-copy-stat / -n already set");
                        return Command::Invalid;
                    }
                    ctx.copy_stat = false;
                }
                "rm" => {
                    if keep_set {
                        eprintln!("argument --rm / -j or --keep / -k already set");
                        return Command::Invalid;
                    }
                    keep_set = true;
                    ctx.junk_source = true;
                }
                "squash" => {
                    if squash_set {
                        eprintln!("argument --squash / -s already set");
                        return Command::Invalid;
                    }
                    squash_set = true;
                    ctx.reject_uncompressible = true;
                }
                "stdout" => {
                    if output_set {
                        eprintln!("write to standard output already set");
                        return Command::Invalid;
                    }
                    output_set = true;
                    ctx.write_to_stdout = true;
                }
                "test" => {
                    if command_set {
                        eprintln!("command already set when parsing --test");
                        return Command::Invalid;
                    }
                    command_set = true;
                    command = Command::TestIntegrity;
                }
                "verbose" => {
                    if ctx.verbosity > 0 {
                        eprintln!("argument --verbose / -v already set");
                        return Command::Invalid;
                    }
                    ctx.verbosity = 1;
                }
                "version" => return Command::Version,
                _ => {
                    let Some((key, value)) = long_opt.split_once('=') else {
                        eprintln!("must pass the parameter as --{}=value", long_opt);
                        return Command::Invalid;
                    };
                    if value.is_empty() {
                        eprintln!("must pass the parameter as --{}=value", long_opt);
                        return Command::Invalid;
                    }
                    match key {
                        "comment" => {
                            if comment_set {
                                eprintln!("comment already set");
                                return Command::Invalid;
                            }
                            if let Some(c_bytes) = parse_base64(value) {
                                ctx.comment = c_bytes;
                                comment_set = true;
                            } else {
                                eprintln!("invalid base64-encoded comment");
                                return Command::Invalid;
                            }
                        }
                        "dictionary" => {
                            if ctx.dictionary_path.is_some() {
                                eprintln!("dictionary path already set");
                                return Command::Invalid;
                            }
                            ctx.dictionary_path = Some(value.to_string());
                        }
                        "lgwin" => {
                            if lgwin_set {
                                eprintln!("lgwin parameter already set");
                                return Command::Invalid;
                            }
                            if let Some(w) = parse_int(value, 0, BROTLI_MAX_WINDOW_BITS) {
                                lgwin_set = true;
                                ctx.lgwin = w;
                            } else {
                                eprintln!("error parsing lgwin value [{}]", value);
                                return Command::Invalid;
                            }
                            if ctx.lgwin != 0 && ctx.lgwin < BROTLI_MIN_WINDOW_BITS {
                                eprintln!(
                                    "lgwin parameter ({}) smaller than the minimum ({})",
                                    ctx.lgwin, BROTLI_MIN_WINDOW_BITS
                                );
                                return Command::Invalid;
                            }
                        }
                        "large_window" => {
                            if lgwin_set {
                                eprintln!("lgwin parameter already set");
                                return Command::Invalid;
                            }
                            if let Some(w) = parse_int(value, 0, BROTLI_LARGE_MAX_WBITS as i32) {
                                lgwin_set = true;
                                ctx.lgwin = w;
                            } else {
                                eprintln!("error parsing lgwin value [{}]", value);
                                return Command::Invalid;
                            }
                            if ctx.lgwin != 0 && ctx.lgwin < BROTLI_MIN_WINDOW_BITS {
                                eprintln!(
                                    "lgwin parameter ({}) smaller than the minimum ({})",
                                    ctx.lgwin, BROTLI_MIN_WINDOW_BITS
                                );
                                return Command::Invalid;
                            }
                        }
                        "output" => {
                            if output_set {
                                eprintln!("write to standard output already set (--output)");
                                return Command::Invalid;
                            }
                            output_set = true;
                            ctx.output_path = Some(value.to_string());
                        }
                        "quality" => {
                            if quality_set {
                                eprintln!("quality already set");
                                return Command::Invalid;
                            }
                            if let Some(q) = parse_int(value, BROTLI_MIN_QUALITY, BROTLI_MAX_QUALITY) {
                                quality_set = true;
                                ctx.quality = q;
                            } else {
                                eprintln!("error parsing quality value [{}]", value);
                                return Command::Invalid;
                            }
                        }
                        "suffix" => {
                            if suffix_set {
                                eprintln!("suffix already set");
                                return Command::Invalid;
                            }
                            suffix_set = true;
                            ctx.suffix = value.to_string();
                        }
                        _ => {
                            eprintln!("invalid parameter: [{}]", long_opt);
                            return Command::Invalid;
                        }
                    }
                }
            }
        }
        i += 1;
    }

    ctx.decompress = command == Command::Decompress;
    ctx.test_integrity = command == Command::TestIntegrity;

    if ctx.input_paths.len() > 1 && output_set {
        return Command::Invalid;
    }
    if ctx.test_integrity && (ctx.output_path.is_some() || ctx.write_to_stdout) {
        return Command::Invalid;
    }
    if ctx.reject_uncompressible && ctx.write_to_stdout {
        return Command::Invalid;
    }
    if ctx.suffix.contains('/') || ctx.suffix.contains('\\') {
        return Command::Invalid;
    }
    if !ctx.decompress && ctx.allow_concatenated {
        return Command::Invalid;
    }
    if ctx.allow_concatenated && !ctx.comment.is_empty() {
        return Command::Invalid;
    }

    command
}

fn print_version() {
    println!(
        "brotli {}.{}.{} (brotli-rs)",
        BROTLI_VERSION_MAJOR, BROTLI_VERSION_MINOR, BROTLI_VERSION_PATCH
    );
}

fn print_help(name: &str, to_stdout: bool) {
    let text = format!(
        "Usage: {name} [OPTION]... [FILE]...\n\
Options:\n  -#                          compression level (0-9)\n\
  -c, --stdout                write on standard output\n\
  -d, --decompress            decompress\n\
  -f, --force                 force output file overwrite\n\
  -h, --help                  display this help and exit\n\
  -j, --rm                    remove source file(s)\n\
  -s, --squash                remove destination file if larger than source\n\
  -k, --keep                  keep source file(s) (default)\n\
  -n, --no-copy-stat          do not copy source file(s) attributes\n\
  -o FILE, --output=FILE      output file (only if 1 input file)\n\
  -q NUM, --quality=NUM       compression level ({min_q}-{max_q})\n\
  -t, --test                  test compressed file integrity\n\
  -v, --verbose               verbose mode\n\
  -w NUM, --lgwin=NUM         set LZ77 window size (0, {min_w}-{max_w})\n\
                              window size = 2**NUM - 16\n\
                              0 lets compressor choose the optimal value\n\
  --large_window=NUM          use incompatible large-window brotli\n\
                              bitstream with window size (0, {min_w}-{max_lw})\n\
                              WARNING: this format is not compatible\n\
                              with brotli RFC 7932 and may not be\n\
                              decodable with regular brotli decoders\n\
  -C B64, --comment=B64       set comment; argument is base64-decoded first;\n\
                              (maximal decoded length: {max_c})\n\
                              when decoding: check stream comment;\n\
                              when encoding: embed comment (fingerprint)\n\
  -D FILE, --dictionary=FILE  use FILE as raw (LZ77) dictionary\n\
  -K, --concatenated          allows concatenated brotli streams as input\n\
  -S SUF, --suffix=SUF        output file suffix (default:'{suf}')\n\
  -V, --version               display version and exit\n\
  -Z, --best                  use best compression level (11) (default)\n\
Simple options could be coalesced, i.e. '-9kf' is equivalent to '-9 -k -f'.\n\
With no FILE, or when FILE is -, read standard input.\n\
All arguments after '--' are treated as files.",
        name = name,
        min_q = BROTLI_MIN_QUALITY,
        max_q = BROTLI_MAX_QUALITY,
        min_w = BROTLI_MIN_WINDOW_BITS,
        max_w = BROTLI_MAX_WINDOW_BITS,
        max_lw = BROTLI_LARGE_MAX_WBITS,
        max_c = MAX_COMMENT_LEN,
        suf = DEFAULT_SUFFIX
    );
    if to_stdout {
        println!("{text}");
    } else {
        eprintln!("{text}");
    }
}

fn printable_path(path: Option<&str>) -> &str {
    path.unwrap_or("con")
}

fn read_dictionary(ctx: &mut CliContext, cmd: Command) -> bool {
    let Some(ref path) = ctx.dictionary_path else {
        return true;
    };
    let max_dict_size = BROTLI_MAX_DISTANCE - brotli_max_backward_limit(24);
    let data = match fs::read(path) {
        Ok(d) => d,
        Err(e) => {
            eprintln!("failed to open dictionary file [{}]: {}", path, e);
            return false;
        }
    };
    if data.len() > max_dict_size {
        eprintln!(
            "dictionary [{}] is larger than maximum allowed: {}",
            path, max_dict_size
        );
        return false;
    }
    if cmd == Command::Compress {
        let Some(prep) = brotli_encoder_prepare_raw_dictionary(&data) else {
            eprintln!("failed to prepare dictionary [{}]", path);
            return false;
        };
        ctx.prepared_dictionary = Some(prep);
    }
    ctx.dictionary = Some(data);
    true
}

fn format_bytes(value: u64) -> String {
    if value < 1024 {
        format!("{} B", value)
    } else if value < 1_048_576 {
        format!("{:.3} KiB", (value as f64) / 1024.0)
    } else if value < 1_073_741_824 {
        format!("{:.3} MiB", (value as f64) / 1_048_576.0)
    } else {
        format!("{:.3} GiB", (value as f64) / 1_073_741_824.0)
    }
}

fn resolve_output_path(ctx: &mut CliContext, input_path: Option<&str>) -> Option<Option<String>> {
    if ctx.output_path.is_some() {
        return Some(ctx.output_path.clone());
    }
    if ctx.write_to_stdout {
        return Some(None);
    }
    let Some(in_p) = input_path else {
        return Some(None);
    };
    if ctx.decompress {
        let name = file_name_part(in_p);
        if name.len() < ctx.suffix.len() + 1 || !name.ends_with(&ctx.suffix) {
            if name.len() < ctx.suffix.len() + 1 {
                eprintln!("empty output file name for [{}] input file", in_p);
            } else {
                eprintln!("input file [{}] suffix mismatch", in_p);
            }
            ctx.iterator_error = true;
            return None;
        }
        let stripped = &in_p[..in_p.len() - ctx.suffix.len()];
        Some(Some(stripped.to_string()))
    } else {
        Some(Some(format!("{}{}", in_p, ctx.suffix)))
    }
}

fn open_input(path: Option<&str>) -> Option<(Box<dyn Read>, Option<u64>)> {
    if let Some(p) = path {
        match File::open(p) {
            Ok(f) => {
                let len = f.metadata().ok().map(|m| m.len());
                Some((Box::new(f), len))
            }
            Err(e) => {
                eprintln!("failed to open input file [{}]: {}", p, e);
                None
            }
        }
    } else {
        Some((Box::new(io::stdin()), None))
    }
}

fn open_output(path: Option<&str>, force: bool) -> Option<Box<dyn Write>> {
    if let Some(p) = path {
        let mut opts = OpenOptions::new();
        opts.write(true);
        if force {
            opts.create(true).truncate(true);
        } else {
            opts.create_new(true);
        }
        match opts.open(p) {
            Ok(f) => Some(Box::new(f)),
            Err(e) => {
                eprintln!("failed to open output file [{}]: {}", p, e);
                None
            }
        }
    } else {
        Some(Box::new(io::stdout()))
    }
}

fn copy_stat(input_path: Option<&str>, output_path: Option<&str>) {
    let (Some(in_p), Some(out_p)) = (input_path, output_path) else {
        return;
    };
    let Ok(meta) = fs::metadata(in_p) else {
        return;
    };
    let Ok(out_file) = OpenOptions::new().write(true).open(out_p) else {
        return;
    };
    let mut times = FileTimes::new();
    if let Ok(accessed) = meta.accessed() {
        times = times.set_accessed(accessed);
    }
    if let Ok(modified) = meta.modified() {
        times = times.set_modified(modified);
    }
    let _ = out_file.set_times(times);
    let _ = fs::set_permissions(out_p, meta.permissions());
}

fn init_decoder(ctx: &CliContext) -> (BrotliDecoderState, Option<Arc<Mutex<CommentCheck>>>) {
    let mut s = BrotliDecoderState::new();
    s.set_parameter(BrotliDecoderParameter::LargeWindow, 1);
    if let Some(ref dict) = ctx.dictionary {
        s.attach_dictionary(BrotliSharedDictionaryType::Raw, dict);
    }
    let checker = if !ctx.comment.is_empty() {
        let chk = Arc::new(Mutex::new(CommentCheck {
            expected: ctx.comment.clone(),
            pos: 0,
            state: CommentState::Init,
        }));
        let chk_start = Arc::clone(&chk);
        let chk_chunk = Arc::clone(&chk);
        s.set_metadata_callbacks(
            Some(Box::new(move |size: usize| {
                let mut g = chk_start.lock().unwrap();
                if g.state == CommentState::Init {
                    if g.expected.len() != size {
                        g.state = CommentState::Bad;
                    } else {
                        g.pos = 0;
                        g.state = CommentState::Read;
                    }
                }
            })),
            Some(Box::new(move |chunk: &[u8]| {
                let mut g = chk_chunk.lock().unwrap();
                if g.state == CommentState::Read {
                    for &b in chunk {
                        if g.pos >= g.expected.len() || g.expected[g.pos] != b {
                            g.state = CommentState::Bad;
                            return;
                        }
                        g.pos += 1;
                    }
                    if g.pos == g.expected.len() {
                        g.state = CommentState::Ok;
                    }
                }
            })),
        );
        Some(chk)
    } else {
        None
    };
    (s, checker)
}

fn decompress_single(
    ctx: &CliContext,
    input_path: Option<&str>,
    output_path: Option<&str>,
    mut fin: Box<dyn Read>,
    mut fout: Option<Box<dyn Write>>,
) -> bool {
    let (mut s, checker) = init_decoder(ctx);
    let mut in_buf = vec![0u8; FILE_BUFFER_SIZE];
    let mut out_buf = vec![0u8; FILE_BUFFER_SIZE];
    let mut in_pos = 0usize;
    let mut in_len = 0usize;
    let mut total_in = 0u64;
    let mut total_out = 0u64;
    let start = Instant::now();
    let mut result = BrotliDecoderResult::NeedsMoreInput;
    let mut eof = false;

    loop {
        if let Some(ref chk) = checker {
            if chk.lock().unwrap().state == CommentState::Bad {
                eprintln!("corrupt input [{}]", printable_path(input_path));
                if ctx.verbosity > 0 {
                    eprintln!("reason: comment mismatch");
                }
                return false;
            }
        }

        match result {
            BrotliDecoderResult::NeedsMoreInput => {
                if eof {
                    eprintln!("corrupt input [{}]", printable_path(input_path));
                    if ctx.verbosity > 0 {
                        eprintln!("reason: truncated input");
                    }
                    return false;
                }
                match fin.read(&mut in_buf) {
                    Ok(0) => {
                        eprintln!("corrupt input [{}]", printable_path(input_path));
                        if ctx.verbosity > 0 {
                            eprintln!("reason: truncated input");
                        }
                        return false;
                    }
                    Ok(n) => {
                        in_pos = 0;
                        in_len = n;
                        total_in += n as u64;
                    }
                    Err(e) => {
                        eprintln!("failed to read input [{}]: {}", printable_path(input_path), e);
                        return false;
                    }
                }
            }
            BrotliDecoderResult::NeedsMoreOutput => {}
            BrotliDecoderResult::Success => {
                let mut has_more_input = in_pos < in_len;
                if !has_more_input && !eof {
                    match fin.read(&mut in_buf[..1]) {
                        Ok(0) => eof = true,
                        Ok(n) => {
                            in_pos = 0;
                            in_len = n;
                            total_in += n as u64;
                            has_more_input = true;
                        }
                        Err(e) => {
                            eprintln!(
                                "failed to read input [{}]: {}",
                                printable_path(input_path),
                                e
                            );
                            return false;
                        }
                    }
                }
                if has_more_input {
                    if ctx.allow_concatenated {
                        if ctx.verbosity > 0 {
                            eprintln!("extra input");
                        }
                        let (new_s, _) = init_decoder(ctx);
                        s = new_s;
                    } else {
                        eprintln!("corrupt input [{}]", printable_path(input_path));
                        if ctx.verbosity > 0 {
                            eprintln!("reason: extra input");
                        }
                        return false;
                    }
                } else {
                    if let Some(ref mut w) = fout {
                        if let Err(e) = w.flush() {
                            eprintln!(
                                "failed to write output [{}]: {}",
                                printable_path(output_path),
                                e
                            );
                            return false;
                        }
                    }
                    if let Some(ref chk) = checker {
                        if chk.lock().unwrap().state != CommentState::Ok {
                            eprintln!("corrupt input [{}]", printable_path(input_path));
                            if ctx.verbosity > 0 {
                                eprintln!("reason: comment mismatch");
                            }
                            return false;
                        }
                    }
                    if ctx.verbosity > 0 {
                        let secs = start.elapsed().as_secs_f64();
                        eprintln!(
                            "Decompressed [{}]: {} -> {} in {:.2} sec",
                            printable_path(input_path),
                            format_bytes(total_in),
                            format_bytes(total_out),
                            secs
                        );
                    }
                    return true;
                }
            }
            BrotliDecoderResult::Error => {
                eprintln!("corrupt input [{}]", printable_path(input_path));
                if ctx.verbosity > 0 {
                    let err_str = s.error_code.as_str();
                    let pretty = err_str.strip_prefix("_ERROR_").unwrap_or(err_str);
                    eprintln!("reason: {} ({})", pretty, s.error_code as i32);
                }
                return false;
            }
        }

        let mut out_pos = 0usize;
        result = s.decompress_stream(
            &in_buf[..in_len],
            &mut in_pos,
            &mut out_buf,
            &mut out_pos,
            None,
        );
        if out_pos > 0 {
            total_out += out_pos as u64;
            if let Some(ref mut w) = fout {
                if let Err(e) = w.write_all(&out_buf[..out_pos]) {
                    eprintln!(
                        "failed to write output [{}]: {}",
                        printable_path(output_path),
                        e
                    );
                    return false;
                }
            }
        }
    }
}

fn decompress_files(ctx: &mut CliContext) -> bool {
    let inputs = if ctx.input_paths.is_empty() {
        vec![None]
    } else {
        ctx.input_paths.clone()
    };

    for in_opt in inputs {
        let in_ref = in_opt.as_deref();
        let Some(out_opt) = resolve_output_path(ctx, in_ref) else {
            return false;
        };
        let out_ref = out_opt.as_deref();

        if in_ref.is_none() && !ctx.force_overwrite && io::stdin().is_terminal() {
            eprintln!("Use -h help. Use -f to force input from a terminal.");
            return false;
        }

        let Some((fin, _)) = open_input(in_ref) else {
            return false;
        };
        let fout = if !ctx.test_integrity {
            let Some(w) = open_output(out_ref, ctx.force_overwrite) else {
                return false;
            };
            Some(w)
        } else {
            None
        };

        let is_ok = decompress_single(ctx, in_ref, out_ref, fin, fout);
        if !is_ok {
            if let Some(op) = out_ref {
                if !ctx.test_integrity {
                    let _ = fs::remove_file(op);
                }
            }
            return false;
        }
        if !ctx.test_integrity && ctx.copy_stat {
            copy_stat(in_ref, out_ref);
        }
        if ctx.junk_source {
            if let Some(ip) = in_ref {
                if let Err(e) = fs::remove_file(ip) {
                    eprintln!("failed to remove input [{}]: {}", ip, e);
                    return false;
                }
            }
        }
    }
    true
}

fn compress_single(
    ctx: &CliContext,
    input_path: Option<&str>,
    output_path: Option<&str>,
    mut fin: Box<dyn Read>,
    mut fout: Box<dyn Write>,
    file_len: Option<u64>,
) -> Option<(u64, u64)> {
    let mut s = BrotliEncoderState::new();
    s.set_parameter(BrotliEncoderParameter::Quality, ctx.quality as u32);
    if ctx.lgwin > 0 {
        if ctx.lgwin > BROTLI_MAX_WINDOW_BITS {
            s.set_parameter(BrotliEncoderParameter::LargeWindow, 1);
        }
        s.set_parameter(BrotliEncoderParameter::Lgwin, ctx.lgwin as u32);
    } else {
        let mut lgwin = DEFAULT_LGWIN as u32;
        if let Some(len) = file_len {
            lgwin = BROTLI_MIN_WINDOW_BITS as u32;
            while (brotli_max_backward_limit(lgwin) as u64) < len {
                lgwin += 1;
                if lgwin == BROTLI_MAX_WINDOW_BITS as u32 {
                    break;
                }
            }
        }
        s.set_parameter(BrotliEncoderParameter::Lgwin, lgwin);
    }
    if let Some(len) = file_len {
        if len > 0 {
            let hint = len.min(1u64 << 30) as u32;
            s.set_parameter(BrotliEncoderParameter::SizeHint, hint);
        }
    }
    if let Some(ref prep) = ctx.prepared_dictionary {
        s.attach_prepared_dictionary(Arc::clone(prep));
    }

    let mut in_buf = vec![0u8; FILE_BUFFER_SIZE];
    let mut out_buf = vec![0u8; FILE_BUFFER_SIZE];
    let mut in_pos = 0usize;
    let mut in_len = 0usize;
    let mut total_in = 0u64;
    let mut total_out = 0u64;
    let mut is_eof = false;
    let mut prologue = !ctx.comment.is_empty();
    let start = Instant::now();

    loop {
        if in_pos >= in_len && !is_eof {
            match fin.read(&mut in_buf) {
                Ok(0) => {
                    is_eof = true;
                    in_pos = 0;
                    in_len = 0;
                }
                Ok(n) => {
                    in_pos = 0;
                    in_len = n;
                    total_in += n as u64;
                }
                Err(e) => {
                    eprintln!("failed to read input [{}]: {}", printable_path(input_path), e);
                    return None;
                }
            }
        }

        let mut out_pos = 0usize;
        if prologue {
            prologue = false;
            let mut meta_pos = 0usize;
            if !s.compress_stream(
                BrotliEncoderOperation::EmitMetadata,
                &ctx.comment,
                &mut meta_pos,
                &mut out_buf,
                &mut out_pos,
                None,
            ) || meta_pos != ctx.comment.len()
            {
                eprintln!(
                    "failed to emit metadata [{}]",
                    printable_path(input_path)
                );
                return None;
            }
        } else {
            let op = if is_eof {
                BrotliEncoderOperation::Finish
            } else {
                BrotliEncoderOperation::Process
            };
            if !s.compress_stream(
                op,
                &in_buf[..in_len],
                &mut in_pos,
                &mut out_buf,
                &mut out_pos,
                None,
            ) {
                eprintln!(
                    "failed to compress data [{}]",
                    printable_path(input_path)
                );
                return None;
            }
        }

        if out_pos > 0 {
            total_out += out_pos as u64;
            if let Err(e) = fout.write_all(&out_buf[..out_pos]) {
                eprintln!(
                    "failed to write output [{}]: {}",
                    printable_path(output_path),
                    e
                );
                return None;
            }
        }

        if s.is_finished() {
            if let Err(e) = fout.flush() {
                eprintln!(
                    "failed to write output [{}]: {}",
                    printable_path(output_path),
                    e
                );
                return None;
            }
            if ctx.verbosity > 0 {
                let secs = start.elapsed().as_secs_f64();
                eprintln!(
                    "Compressed [{}]: {} -> {} in {:.2} sec",
                    printable_path(input_path),
                    format_bytes(total_in),
                    format_bytes(total_out),
                    secs
                );
            }
            return Some((total_in, total_out));
        }
    }
}

fn compress_files(ctx: &mut CliContext) -> bool {
    let inputs = if ctx.input_paths.is_empty() {
        vec![None]
    } else {
        ctx.input_paths.clone()
    };

    for in_opt in inputs {
        let in_ref = in_opt.as_deref();
        let Some(out_opt) = resolve_output_path(ctx, in_ref) else {
            return false;
        };
        let out_ref = out_opt.as_deref();

        if out_ref.is_none() && !ctx.force_overwrite && io::stdout().is_terminal() {
            eprintln!("Use -h help. Use -f to force output to a terminal.");
            return false;
        }

        let Some((fin, file_len)) = open_input(in_ref) else {
            return false;
        };
        let Some(fout) = open_output(out_ref, ctx.force_overwrite) else {
            return false;
        };

        let result = compress_single(ctx, in_ref, out_ref, fin, fout, file_len);
        let Some((total_in, total_out)) = result else {
            if let Some(op) = out_ref {
                let _ = fs::remove_file(op);
            }
            return false;
        };

        let mut rm_output = false;
        if ctx.reject_uncompressible && total_out >= total_in {
            rm_output = true;
            if ctx.verbosity > 0 {
                eprintln!("Output is larger than input");
            }
            if let Some(op) = out_ref {
                let _ = fs::remove_file(op);
            }
        } else if ctx.copy_stat {
            copy_stat(in_ref, out_ref);
        }

        if !rm_output && ctx.junk_source {
            if let Some(ip) = in_ref {
                if let Err(e) = fs::remove_file(ip) {
                    eprintln!("failed to remove input [{}]: {}", ip, e);
                    return false;
                }
            }
        }
    }
    true
}

fn main() {
    let args: Vec<String> = env::args().collect();
    let mut ctx = CliContext::default();
    let mut cmd = parse_params(&args, &mut ctx);
    let mut is_ok = true;

    if matches!(
        cmd,
        Command::Compress | Command::Decompress | Command::TestIntegrity
    ) && !read_dictionary(&mut ctx, cmd)
    {
        is_ok = false;
        cmd = Command::Noop;
    }

    match cmd {
        Command::Noop => {}
        Command::Version => print_version(),
        Command::Compress => {
            is_ok = compress_files(&mut ctx);
        }
        Command::Decompress | Command::TestIntegrity => {
            is_ok = decompress_files(&mut ctx);
        }
        Command::Help | Command::Invalid => {
            is_ok = cmd == Command::Help;
            let prog = args
                .first()
                .map(|s| Path::new(s).file_name().and_then(|n| n.to_str()).unwrap_or("brotli"))
                .unwrap_or("brotli");
            print_help(prog, is_ok);
        }
    }

    if ctx.iterator_error {
        is_ok = false;
    }
    if !is_ok {
        process::exit(1);
    }
}
