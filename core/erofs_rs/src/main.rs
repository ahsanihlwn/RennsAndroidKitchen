// SPDX-License-Identifier: MIT

//! `erofs-rs` — Modern, MIT-licensed native Rust CLI for EROFS filesystem images.

use erofs_rs::avb::{read_avb_footer, strip_avb_footer};
use erofs_rs::compact::compact_image;
use erofs_rs::compress::CompressionAlg;
use erofs_rs::edit::{
    plan_path_write, read_sb_file, unlink_path, usage_report, write_path_data, write_path_mode,
    write_path_selinux,
};
use erofs_rs::error::{ErofsError, Result};
use erofs_rs::extract::{ExtractOptions, SymlinkMode, extract_image};
use erofs_rs::mount_index::MountRegistry;
use erofs_rs::pack::{InodeFormatMode, PackOptions, pack_directory};
use erofs_rs::reader::{ErofsImage, FileNode, is_text_bytes};
use erofs_rs::sparse::detect_fs;
use erofs_rs::verify::verify_image;
use serde::Serialize;
use std::collections::BTreeMap;
use std::fs::{self, File, OpenOptions};
use std::io::{self, Read, Write};
use std::path::{Path, PathBuf};
use std::process::ExitCode;

const VERSION: &str = env!("CARGO_PKG_VERSION");

fn main() -> ExitCode {
    match run() {
        Ok(()) => ExitCode::SUCCESS,
        Err(e) => {
            eprintln!("error: {e}");
            ExitCode::FAILURE
        }
    }
}

fn print_help() {
    println!(
        r#"erofs-rs {VERSION} (MIT License) — Native Rust EROFS Inspector, Unpacker, Packer & Direct Editor

USAGE:
  erofs-rs [--json] [--offset <BYTES>] <COMMAND> [ARGS...]

COMMANDS:
  detect <image>                       Detect filesystem type (EROFS/EXT) and Android Sparse status
  info <image>                         Show superblock, features, compression, UUID, CRC32C & AVB
  list <image> [path]                  List image entries (--tree, --long, --shallow, --filter <STR>)
  stat <image> <path>                  Show detailed inode metadata, xattrs & physical blocks
  read <image> <path>                  Read file contents to stdout or -o <file> (--check-text)
  unpack <image> -o <dir>              Multi-threaded extract + Android fs_config/file_contexts/fs_options
                                       (--target <P>, --targets-file <F>, --recursive, --config-only,
                                        --no-config, --overwrite, --verify-digest,
                                        --symlink-mode auto|cygwin|native, --threads <N>)
  pack <src_dir> <out_img>             Build an EROFS image from a directory
                                       (--algo none|lz4|lz4hc|deflate|zstd|lzma, --level <N>,
                                        --pcluster <BYTES>, --block-size <BYTES>, --fs-config <F>,
                                        --file-contexts <F>, --mount-point <P>, --label <STR>,
                                        --uuid <UUID|random|clear>, --timestamp <EPOCH>,
                                        --inode-format auto|compact|extended, --no-inline,
                                        --all-root, --force-uid <U>, --force-gid <G>,
                                        --uid-offset <N>, --gid-offset <N>, --exclude <REGEX>,
                                        --align-blocks <N>, --no-crc, --threads <N>)
  verify <image>                       Verify superblock CRC32C, dirent order & full decompression
                                       (--metadata-only, --verify-digest, --threads <N>)
  usage <image>                        Calculate data, metadata, wasted & reclaimable space
  compact <image>                      Reclaim wasted space & relocate metadata in-place
                                       (--dry-run, --no-relocate-meta)
  edit write <image> <path> ...        Direct edit file inside image without unpacking
                                       (--from <FILE> | --stdin, --dry-run, --no-grow, --no-strip-avb)
  edit chmod <image> <path> <octal>    Direct in-place permission update (e.g. 0644 or 0755)
  edit selinux <image> <path> <ctx>    Direct in-place inline security.selinux update
  edit rm <image> <path>               Direct unlink entry from parent directory (--recursive)
  avb inspect <image>                  Inspect AVB footer metadata
  avb strip <image>                    Strip AVB footer & vbmeta tail back to raw EROFS size
  resolve <image> <path>               Resolve Android symlink across mounted partition images
                                       (--mount <OTHER_IMG> ...)
  version                              Print version and available compression engines

GLOBAL FLAGS:
  --json                               Emit structured JSON output (for scripts & GUI integration)
  --offset <BYTES>                     Skip <BYTES> at start of image (for embedded images)
  -h, --help                           Show this help"#
    );
}

fn run() -> Result<()> {
    let raw_args: Vec<String> = std::env::args().skip(1).collect();
    if raw_args.is_empty() {
        print_help();
        return Ok(());
    }

    let mut json_mode = false;
    let mut offset: u64 = 0;
    let mut args: Vec<String> = Vec::new();

    let mut i = 0;
    while i < raw_args.len() {
        match raw_args[i].as_str() {
            "--json" => json_mode = true,
            "--offset" => {
                i += 1;
                let v = raw_args
                    .get(i)
                    .ok_or_else(|| ErofsError::InvalidArg("--offset requires a value".into()))?;
                offset = parse_u64(v)?;
            }
            s if s.starts_with("--offset=") => {
                offset = parse_u64(&s["--offset=".len()..])?;
            }
            _ => args.push(raw_args[i].clone()),
        }
        i += 1;
    }

    if args.is_empty() || matches!(args[0].as_str(), "-h" | "--help" | "help") {
        print_help();
        return Ok(());
    }

    match args[0].as_str() {
        "version" | "-V" | "--version" => cmd_version(json_mode),
        "detect" => cmd_detect(&args[1..], offset, json_mode),
        "info" => cmd_info(&args[1..], offset, json_mode),
        "list" | "ls" => cmd_list(&args[1..], offset, json_mode),
        "stat" => cmd_stat(&args[1..], offset, json_mode),
        "read" | "cat" => cmd_read(&args[1..], offset, json_mode),
        "unpack" | "extract" => cmd_unpack(&args[1..], offset, json_mode),
        "pack" | "build" => cmd_pack(&args[1..], json_mode),
        "verify" | "fsck" => cmd_verify(&args[1..], offset, json_mode),
        "usage" => cmd_usage(&args[1..], offset, json_mode),
        "compact" => cmd_compact(&args[1..], json_mode),
        "edit" => cmd_edit(&args[1..], json_mode),
        "avb" => cmd_avb(&args[1..], json_mode),
        "resolve" => cmd_resolve(&args[1..], offset, json_mode),
        other => Err(ErofsError::InvalidArg(format!(
            "unknown command '{other}' (run `erofs-rs --help`)"
        ))),
    }
}

fn cmd_version(json_mode: bool) -> Result<()> {
    #[derive(Serialize)]
    struct VersionInfo<'a> {
        name: &'a str,
        version: &'a str,
        license: &'a str,
        compressors: Vec<&'a str>,
    }
    let info = VersionInfo {
        name: "erofs-rs",
        version: VERSION,
        license: "MIT",
        compressors: vec!["lz4", "lz4hc", "deflate", "zstd", "lzma"],
    };
    if json_mode {
        println!("{}", serde_json::to_string_pretty(&info).unwrap());
    } else {
        println!("erofs-rs {} (MIT License)", info.version);
        println!("Compressors: {}", info.compressors.join(", "));
    }
    Ok(())
}

fn cmd_detect(args: &[String], offset: u64, json_mode: bool) -> Result<()> {
    let path = require_pos(args, 0, "image")?;
    let info = detect_fs(Path::new(path), offset)?;
    if json_mode {
        println!("{}", serde_json::to_string_pretty(&info).unwrap());
    } else {
        println!("Filesystem:    {}", info.fs.as_str());
        println!("Sparse:        {}", info.is_sparse);
        println!("File size:     {} ({})", fmt_size(info.file_size), info.file_size);
        println!("Virtual size:  {} ({})", fmt_size(info.virtual_size), info.virtual_size);
        if let Some(off) = info.erofs_magic_offset {
            println!("Magic offset:  0x{off:x} ({off})");
        }
    }
    Ok(())
}

fn cmd_info(args: &[String], offset: u64, json_mode: bool) -> Result<()> {
    let path = require_pos(args, 0, "image")?;
    let img = ErofsImage::open(Path::new(path), offset)?;
    let sum = img.summary();
    if json_mode {
        println!("{}", serde_json::to_string_pretty(&sum).unwrap());
    } else {
        println!("Image:         {path}");
        println!("UUID:          {}", sum.uuid);
        if !sum.volume_name.is_empty() {
            println!("Volume label:  {}", sum.volume_name);
        }
        println!("Block size:    {} bytes", sum.block_size);
        println!("Total blocks:  {}", sum.total_blocks);
        println!(
            "Image size:    {} ({} bytes){}",
            fmt_size(sum.image_size),
            sum.image_size,
            if sum.is_sparse { " [SPARSE]" } else { "" }
        );
        println!("Inodes:        {}", sum.inos);
        println!(
            "Layout addrs:  root_nid={} meta_blk={} xattr_blk={} packed_nid={} metabox_nid={}",
            sum.root_nid, sum.meta_blkaddr, sum.xattr_blkaddr, sum.packed_nid, sum.metabox_nid
        );
        println!("Build time:    {} (unix epoch)", sum.build_time);
        println!(
            "Compat flags:  {}",
            if sum.compat_features.is_empty() {
                "-".into()
            } else {
                sum.compat_features.join(", ")
            }
        );
        println!(
            "Incompat flags:{}",
            if sum.incompat_features.is_empty() {
                " -".into()
            } else {
                format!(" {}", sum.incompat_features.join(", "))
            }
        );
        println!(
            "Compressors:   {}",
            if sum.compression_algorithms.is_empty() {
                "none".into()
            } else {
                sum.compression_algorithms.join(", ")
            }
        );
        if sum.crc_present {
            let status = match sum.crc_valid {
                Some(true) => "OK",
                Some(false) => "MISMATCH",
                None => "N/A",
            };
            println!(
                "Superblock CRC:0x{:08x} (computed 0x{:08x}) [{}]",
                sum.crc_stored.unwrap_or(0),
                sum.crc_computed.unwrap_or(0),
                status
            );
        }
        if let Some(ref avb) = sum.avb_footer {
            println!(
                "AVB Footer:    v{}.{}, orig_size={}B, vbmeta@{} ({}B)",
                avb.version_major,
                avb.version_minor,
                avb.original_image_size,
                avb.vbmeta_offset,
                avb.vbmeta_size
            );
        }
        if let Some(ref ishare) = sum.ishare_xattr_name {
            println!("Digest xattr:  {ishare}");
        }
    }
    Ok(())
}

fn cmd_list(args: &[String], offset: u64, json_mode: bool) -> Result<()> {
    let mut tree_mode = false;
    let mut long_mode = false;
    let mut shallow = false;
    let mut include_root = false;
    let mut filter: Option<String> = None;
    let mut pos = Vec::new();

    let mut i = 0;
    while i < args.len() {
        match args[i].as_str() {
            "--tree" | "-t" => tree_mode = true,
            "--long" | "-l" => long_mode = true,
            "--shallow" => shallow = true,
            "--include-root" => include_root = true,
            "--filter" => {
                i += 1;
                filter = args.get(i).cloned();
            }
            s if s.starts_with("--filter=") => {
                filter = Some(s["--filter=".len()..].to_string());
            }
            other => pos.push(other.to_string()),
        }
        i += 1;
    }

    let img_path = require_pos(&pos, 0, "image")?;
    let sub_path = pos.get(1).map(|s| s.as_str());
    let img = ErofsImage::open(Path::new(img_path), offset)?;

    let mut nodes: Vec<FileNode> = if let Some(sp) = sub_path {
        if shallow {
            let dir = img.lookup(sp)?;
            if dir.is_dir() {
                let clean = sp.replace('\\', "/");
                let clean = clean.trim_matches('/');
                let entries = img.read_dir(&dir, false)?;
                let mut out = Vec::with_capacity(entries.len());
                for e in entries {
                    let child = img.read_inode(e.nid)?;
                    let full = if clean.is_empty() {
                        format!("/{}", e.name)
                    } else {
                        format!("/{clean}/{}", e.name)
                    };
                    out.push(img.build_node(full, &child)?);
                }
                out
            } else {
                img.walk_path(sp, false)?
            }
        } else {
            img.walk_path(sp, true)?
        }
    } else if shallow {
        let root = img.read_inode(img.sb.root_nid)?;
        let entries = img.read_dir(&root, false)?;
        let mut out = Vec::with_capacity(entries.len());
        for e in entries {
            let child = img.read_inode(e.nid)?;
            out.push(img.build_node(format!("/{}", e.name), &child)?);
        }
        out
    } else {
        img.walk(include_root)?
    };

    if let Some(ref f) = filter {
        let f_low = f.to_ascii_lowercase();
        nodes.retain(|n| n.path.to_ascii_lowercase().contains(&f_low));
    }

    if json_mode {
        println!("{}", serde_json::to_string_pretty(&nodes).unwrap());
        return Ok(());
    }

    if tree_mode {
        print_tree(&nodes);
        return Ok(());
    }

    for n in &nodes {
        if long_mode {
            let link_part = n
                .target
                .as_ref()
                .map(|t| format!(" -> {t}"))
                .unwrap_or_default();
            let cap_part = n
                .capabilities
                .filter(|&c| c != 0)
                .map(|c| format!(" cap=0x{c:X}"))
                .unwrap_or_default();
            let se = n.selinux.as_deref().unwrap_or("-");
            println!(
                "{} {:>5}:{:<5} {:>10} {:<18} nid={:<6} {}{}{} [{}]",
                n.perm, n.uid, n.gid, n.size, n.layout, n.nid, n.path, link_part, cap_part, se
            );
        } else {
            let link_part = n
                .target
                .as_ref()
                .map(|t| format!(" -> {t}"))
                .unwrap_or_default();
            println!(
                "{} {:>10} {:<4} {}{}",
                n.perm, n.size, n.file_type, n.path, link_part
            );
        }
    }
    Ok(())
}

fn print_tree(nodes: &[FileNode]) {
    let mut children: BTreeMap<String, Vec<&FileNode>> = BTreeMap::new();
    for n in nodes {
        let trimmed = n.path.trim_matches('/');
        if trimmed.is_empty() {
            continue;
        }
        let parent = trimmed
            .rsplit_once('/')
            .map(|(p, _)| p.to_string())
            .unwrap_or_default();
        children.entry(parent).or_default().push(n);
    }

    fn walk_print(
        parent: &str,
        prefix: &str,
        children: &BTreeMap<String, Vec<&FileNode>>,
    ) {
        let Some(list) = children.get(parent) else {
            return;
        };
        for (idx, n) in list.iter().enumerate() {
            let last = idx + 1 == list.len();
            let branch = if last { "`-- " } else { "|-- " };
            let name = n.path.rsplit('/').next().unwrap_or(&n.path);
            let suffix = if n.is_dir {
                "/".to_string()
            } else if let Some(ref t) = n.target {
                format!(" -> {t}")
            } else {
                format!(" ({})", fmt_size(n.size))
            };
            println!("{prefix}{branch}{name}{suffix}");
            if n.is_dir {
                let next_prefix = format!("{prefix}{}", if last { "    " } else { "|   " });
                walk_print(n.path.trim_matches('/'), &next_prefix, children);
            }
        }
    }

    println!("/");
    walk_print("", "", &children);
}

fn cmd_stat(args: &[String], offset: u64, json_mode: bool) -> Result<()> {
    let img_path = require_pos(args, 0, "image")?;
    let internal = require_pos(args, 1, "internal_path")?;
    let img = ErofsImage::open(Path::new(img_path), offset)?;
    let inode = img.lookup(internal)?;
    let clean = internal.replace('\\', "/");
    let clean = clean.trim_matches('/');
    let display = if clean.is_empty() {
        "/".to_string()
    } else {
        format!("/{clean}")
    };
    let node = img.build_node(display, &inode)?;
    let xattrs = img.read_xattrs(&inode)?;
    let pblocks = img.inode_data_blocks(&inode).unwrap_or_default();

    #[derive(Serialize)]
    struct StatOut {
        node: FileNode,
        is_extended: bool,
        inode_offset: u64,
        xattr_offset: u64,
        xattr_size: u64,
        data_offset: u64,
        physical_blocks_count: usize,
        physical_blocks_range: Option<(u64, u64)>,
        xattrs: Vec<XattrView>,
    }
    #[derive(Serialize)]
    struct XattrView {
        name: String,
        shared: bool,
        size: usize,
        value_text: String,
    }

    let range = if pblocks.is_empty() {
        None
    } else {
        Some((*pblocks.first().unwrap(), *pblocks.last().unwrap()))
    };
    let xviews: Vec<XattrView> = xattrs
        .iter()
        .map(|x| XattrView {
            name: x.name.clone(),
            shared: x.shared,
            size: x.value.len(),
            value_text: if is_text_bytes(&x.value) {
                x.value_utf8_trimmed()
            } else {
                x.value.iter().map(|b| format!("{b:02x}")).collect()
            },
        })
        .collect();

    let out = StatOut {
        node,
        is_extended: inode.is_extended,
        inode_offset: inode.offset,
        xattr_offset: inode.xattr_offset,
        xattr_size: inode.xattr_size,
        data_offset: inode.data_offset,
        physical_blocks_count: pblocks.len(),
        physical_blocks_range: range,
        xattrs: xviews,
    };

    if json_mode {
        println!("{}", serde_json::to_string_pretty(&out).unwrap());
    } else {
        println!("Path:       {}", out.node.path);
        println!("Type:       {} ({})", out.node.file_type, out.node.perm);
        println!(
            "Mode/Owner: {}  uid={} gid={} nlink={}",
            out.node.mode_octal, out.node.uid, out.node.gid, out.node.nlink
        );
        println!("Size:       {} bytes ({})", out.node.size, fmt_size(out.node.size));
        println!(
            "Inode:      nid={} ino={} format={} layout={}",
            out.node.nid,
            out.node.ino,
            if out.is_extended { "extended(64B)" } else { "compact(32B)" },
            out.node.layout
        );
        if let Some((lo, hi)) = out.physical_blocks_range {
            println!(
                "Data blks:  {} block(s), span {}..={}",
                out.physical_blocks_count, lo, hi
            );
        }
        if let Some(ref t) = out.node.target {
            println!("Symlink:    -> {t}");
        }
        if let Some(cap) = out.node.capabilities {
            println!("Capability: 0x{cap:X}");
        }
        if !out.xattrs.is_empty() {
            println!("Xattrs:");
            for x in &out.xattrs {
                println!(
                    "  {} [{}]: {}",
                    x.name,
                    if x.shared { "shared" } else { "inline" },
                    x.value_text
                );
            }
        }
    }
    Ok(())
}

fn cmd_read(args: &[String], offset: u64, json_mode: bool) -> Result<()> {
    let mut out_file: Option<PathBuf> = None;
    let mut check_text = false;
    let mut pos = Vec::new();
    let mut i = 0;
    while i < args.len() {
        match args[i].as_str() {
            "-o" | "--out" => {
                i += 1;
                out_file = Some(PathBuf::from(require_pos(args, i, "--out")?));
            }
            "--check-text" => check_text = true,
            other => pos.push(other.to_string()),
        }
        i += 1;
    }

    let img_path = require_pos(&pos, 0, "image")?;
    let internal = require_pos(&pos, 1, "internal_path")?;
    let img = ErofsImage::open(Path::new(img_path), offset)?;
    let (data, node) = img.read_path(internal)?;

    if check_text {
        let is_txt = is_text_bytes(&data);
        #[derive(Serialize)]
        struct ReadMeta {
            path: String,
            size: usize,
            is_text: bool,
            text: Option<String>,
        }
        let meta = ReadMeta {
            path: node.path,
            size: data.len(),
            is_text: is_txt,
            text: is_txt.then(|| String::from_utf8_lossy(&data).into_owned()),
        };
        if json_mode {
            println!("{}", serde_json::to_string_pretty(&meta).unwrap());
        } else {
            println!("path={} size={} is_text={}", meta.path, meta.size, meta.is_text);
            if let Some(t) = meta.text {
                print!("{t}");
            }
        }
        return Ok(());
    }

    if let Some(dst) = out_file {
        if let Some(par) = dst.parent() {
            if !par.as_os_str().is_empty() {
                fs::create_dir_all(par)?;
            }
        }
        File::create(&dst)?.write_all(&data)?;
        if json_mode {
            println!(
                "{}",
                serde_json::json!({
                    "path": node.path,
                    "written_to": dst.display().to_string(),
                    "bytes": data.len()
                })
            );
        }
    } else {
        io::stdout().write_all(&data)?;
        io::stdout().flush()?;
    }
    Ok(())
}

fn cmd_unpack(args: &[String], offset: u64, json_mode: bool) -> Result<()> {
    let mut out_dir: Option<PathBuf> = None;
    let mut targets: Vec<String> = Vec::new();
    let mut targets_file: Option<PathBuf> = None;
    let mut recursive = false;
    let mut config_only = false;
    let mut write_configs = true;
    let mut overwrite = false;
    let mut verify_digest = false;
    let mut symlink_mode = SymlinkMode::Auto;
    let mut threads = 0usize;
    let mut quiet = false;
    let mut pos = Vec::new();

    let mut i = 0;
    while i < args.len() {
        match args[i].as_str() {
            "-o" | "--out" | "--outdir" => {
                i += 1;
                out_dir = Some(PathBuf::from(require_pos(args, i, "--out")?));
            }
            "--target" => {
                i += 1;
                targets.push(require_pos(args, i, "--target")?.to_string());
            }
            "--targets-file" => {
                i += 1;
                targets_file = Some(PathBuf::from(require_pos(args, i, "--targets-file")?));
            }
            "--recursive" | "-r" => recursive = true,
            "--config-only" => config_only = true,
            "--no-config" => write_configs = false,
            "--overwrite" | "-f" => overwrite = true,
            "--verify-digest" => verify_digest = true,
            "--symlink-mode" => {
                i += 1;
                symlink_mode = SymlinkMode::parse(require_pos(args, i, "--symlink-mode")?)?;
            }
            "--quiet" | "-q" => quiet = true,
            "--threads" | "-j" => {
                i += 1;
                threads = require_pos(args, i, "--threads")?
                    .parse::<usize>()
                    .map_err(|_| ErofsError::InvalidArg("invalid --threads value".into()))?;
            }
            other => pos.push(other.to_string()),
        }
        i += 1;
    }

    let img_path_str = require_pos(&pos, 0, "image")?;
    let img_path = Path::new(img_path_str);
    let base_name = img_path
        .file_stem()
        .and_then(|s| s.to_str())
        .unwrap_or("erofs")
        .to_string();

    if let Some(tf) = targets_file {
        let content = fs::read_to_string(&tf)?;
        for line in content.lines() {
            let l = line.trim();
            if !l.is_empty() && !l.starts_with('#') {
                targets.push(l.to_string());
            }
        }
    }

    let opts = ExtractOptions {
        out_dir: out_dir.unwrap_or_else(|| PathBuf::from(".")),
        image_base_name: base_name,
        targets,
        recursive,
        config_only,
        write_configs,
        overwrite,
        verify_digest,
        symlink_mode,
        threads,
        quiet,
    };

    let start = std::time::Instant::now();
    let img = ErofsImage::open(img_path, offset)?;
    let report = extract_image(&img, &opts)?;
    let elapsed = start.elapsed().as_secs_f64();

    if json_mode {
        println!("{}", serde_json::to_string_pretty(&report).unwrap());
    } else if !quiet {
        println!("Extracted to:  {}", report.extract_dir);
        if let Some(ref c) = report.fs_config {
            println!("fs_config:     {c}");
        }
        if let Some(ref c) = report.file_contexts {
            println!("file_contexts: {c}");
        }
        if let Some(ref c) = report.fs_options {
            println!("fs_options:    {c}");
        }
        println!(
            "Summary:       {} files, {} dirs, {} symlinks ({} skipped), {} hardlinks, {} written in {:.3}s",
            report.files_extracted,
            report.dirs_created,
            report.symlinks_created,
            report.symlinks_skipped,
            report.hardlinks_created,
            fmt_size(report.bytes_written),
            elapsed
        );
        if !report.errors.is_empty() {
            eprintln!("Warnings/Errors ({}):", report.errors.len());
            for e in report.errors.iter().take(20) {
                eprintln!("  - {e}");
            }
        }
    }
    Ok(())
}

fn cmd_pack(args: &[String], json_mode: bool) -> Result<()> {
    let mut opts = PackOptions::default();
    let mut pos = Vec::new();

    let mut i = 0;
    while i < args.len() {
        match args[i].as_str() {
            "--algo" => {
                i += 1;
                opts.algorithm = CompressionAlg::parse(require_pos(args, i, "--algo")?)?;
            }
            "--level" => {
                i += 1;
                opts.level = Some(
                    require_pos(args, i, "--level")?
                        .parse::<i32>()
                        .map_err(|_| ErofsError::InvalidArg("invalid --level".into()))?,
                );
            }
            "--pcluster" => {
                i += 1;
                opts.pcluster_size = parse_u64(require_pos(args, i, "--pcluster")?)? as u32;
            }
            "--block-size" => {
                i += 1;
                opts.block_size = parse_u64(require_pos(args, i, "--block-size")?)? as u32;
            }
            "--fs-config" => {
                i += 1;
                opts.fs_config = Some(PathBuf::from(require_pos(args, i, "--fs-config")?));
            }
            "--file-contexts" => {
                i += 1;
                opts.file_contexts = Some(PathBuf::from(require_pos(args, i, "--file-contexts")?));
            }
            "--mount-point" => {
                i += 1;
                opts.mount_point = require_pos(args, i, "--mount-point")?.to_string();
            }
            "--label" => {
                i += 1;
                opts.volume_label = require_pos(args, i, "--label")?.to_string();
            }
            "--uuid" => {
                i += 1;
                opts.uuid = Some(require_pos(args, i, "--uuid")?.to_string());
            }
            "--timestamp" => {
                i += 1;
                opts.timestamp = Some(parse_u64(require_pos(args, i, "--timestamp")?)?);
            }
            "--inode-format" => {
                i += 1;
                opts.inode_format =
                    InodeFormatMode::parse(require_pos(args, i, "--inode-format")?)?;
            }
            "--no-inline" => opts.inline_data = false,
            "--all-root" => opts.all_root = true,
            "--no-crc" => opts.enable_crc = false,
            "--force-uid" => {
                i += 1;
                opts.force_uid = Some(parse_u64(require_pos(args, i, "--force-uid")?)? as u32);
            }
            "--force-gid" => {
                i += 1;
                opts.force_gid = Some(parse_u64(require_pos(args, i, "--force-gid")?)? as u32);
            }
            "--uid-offset" => {
                i += 1;
                opts.uid_offset = parse_u64(require_pos(args, i, "--uid-offset")?)? as u32;
            }
            "--gid-offset" => {
                i += 1;
                opts.gid_offset = parse_u64(require_pos(args, i, "--gid-offset")?)? as u32;
            }
            "--align-blocks" => {
                i += 1;
                opts.align_blocks = parse_u64(require_pos(args, i, "--align-blocks")?)?;
            }
            "--exclude" => {
                i += 1;
                opts.exclude_regex
                    .push(require_pos(args, i, "--exclude")?.to_string());
            }
            "--threads" | "-j" => {
                i += 1;
                opts.threads = parse_u64(require_pos(args, i, "--threads")?)? as usize;
            }
            other => pos.push(other.to_string()),
        }
        i += 1;
    }

    opts.source_dir = PathBuf::from(require_pos(&pos, 0, "source_dir")?);
    opts.output_image = PathBuf::from(require_pos(&pos, 1, "output_image")?);

    let start = std::time::Instant::now();
    let report = pack_directory(&opts)?;
    let elapsed = start.elapsed().as_secs_f64();

    if json_mode {
        println!("{}", serde_json::to_string_pretty(&report).unwrap());
    } else {
        println!("Built EROFS image: {}", report.output_image);
        println!(
            "Size:              {} ({} blocks x {}B)",
            fmt_size(report.image_bytes),
            report.total_blocks,
            report.block_size
        );
        println!(
            "Contents:          {} files ({} compressed), {} dirs, {} symlinks, {} shared xattrs",
            report.files, report.compressed_files, report.dirs, report.symlinks, report.shared_xattrs
        );
        println!(
            "Algorithm / UUID:  {} / {} (built in {:.3}s)",
            report.algorithm, report.uuid, elapsed
        );
        if let Some(crc) = report.crc32c {
            println!("Superblock CRC32C: 0x{crc:08x}");
        }
    }
    Ok(())
}

fn cmd_verify(args: &[String], offset: u64, json_mode: bool) -> Result<()> {
    let mut check_data = true;
    let mut verify_digest = false;
    let mut threads = 0usize;
    let mut pos = Vec::new();
    let mut i = 0;
    while i < args.len() {
        match args[i].as_str() {
            "--metadata-only" => check_data = false,
            "--verify-digest" => verify_digest = true,
            "--threads" | "-j" => {
                i += 1;
                threads = parse_u64(require_pos(args, i, "--threads")?)? as usize;
            }
            other => pos.push(other.to_string()),
        }
        i += 1;
    }

    let img_path = require_pos(&pos, 0, "image")?;
    let img = ErofsImage::open(Path::new(img_path), offset)?;
    let report = verify_image(&img, check_data, verify_digest, threads)?;

    if json_mode {
        println!("{}", serde_json::to_string_pretty(&report).unwrap());
    } else {
        println!("Status:   {}", if report.ok { "OK" } else { "FAILED" });
        println!(
            "Checked:  {} inodes ({} files, {} dirs), {} verified",
            report.checked_inodes,
            report.checked_files,
            report.checked_dirs,
            fmt_size(report.checked_bytes)
        );
        if verify_digest {
            println!("Digests:  {} verified", report.digests_verified);
        }
        for e in &report.errors {
            eprintln!("  ERR: {e}");
        }
    }
    if !report.ok {
        return Err(ErofsError::Corrupted(format!(
            "verification failed with {} error(s)",
            report.errors.len()
        )));
    }
    Ok(())
}

fn cmd_usage(args: &[String], offset: u64, json_mode: bool) -> Result<()> {
    let img_path = require_pos(args, 0, "image")?;
    let rep = usage_report(Path::new(img_path), offset)?;
    if json_mode {
        println!("{}", serde_json::to_string_pretty(&rep).unwrap());
    } else {
        println!("Image size:    {} ({} blocks x {}B)", fmt_size(rep.file_size), rep.total_blocks, rep.block_size);
        println!("Data used:     {} ({} blocks)", fmt_size(rep.data_bytes), rep.data_blocks);
        println!("Metadata:      {} ({} blocks)", fmt_size(rep.meta_bytes), rep.meta_blocks);
        println!("Wasted:        {} ({} blocks)", fmt_size(rep.wasted_bytes), rep.wasted_blocks);
        println!("Reclaimable:   {}", fmt_size(rep.reclaimable_bytes));
        println!("Logical data:  {}", fmt_size(rep.logical_bytes));
        println!(
            "Entries:       {} files, {} dirs, {} symlinks",
            rep.files, rep.dirs, rep.symlinks
        );
    }
    Ok(())
}

fn cmd_compact(args: &[String], json_mode: bool) -> Result<()> {
    let mut dry_run = false;
    let mut relocate_meta = true;
    let mut pos = Vec::new();
    for a in args {
        match a.as_str() {
            "--dry-run" => dry_run = true,
            "--no-relocate-meta" => relocate_meta = false,
            other => pos.push(other.to_string()),
        }
    }
    let img_path = require_pos(&pos, 0, "image")?;
    let rep = compact_image(Path::new(img_path), dry_run, relocate_meta)?;
    if json_mode {
        println!("{}", serde_json::to_string_pretty(&rep).unwrap());
    } else {
        println!(
            "Compact{}: {} -> {} (saved {})",
            if rep.dry_run { " [DRY-RUN]" } else { "" },
            fmt_size(rep.before_bytes),
            fmt_size(rep.after_bytes),
            fmt_size(rep.saved_bytes)
        );
        println!(
            "Moves: {} ({} blocks moved), {} holes before, {} inodes relocated",
            rep.moves, rep.blocks_moved, rep.holes_before, rep.inodes_relocated
        );
    }
    Ok(())
}

fn cmd_edit(args: &[String], json_mode: bool) -> Result<()> {
    let sub = require_pos(args, 0, "edit subcommand (write|chmod|selinux|rm)")?;
    let rest = &args[1..];
    match sub {
        "write" => {
            let mut from_file: Option<PathBuf> = None;
            let mut from_stdin = false;
            let mut dry_run = false;
            let mut allow_grow = true;
            let mut strip_avb = true;
            let mut pos = Vec::new();
            let mut i = 0;
            while i < rest.len() {
                match rest[i].as_str() {
                    "--from" => {
                        i += 1;
                        from_file = Some(PathBuf::from(require_pos(rest, i, "--from")?));
                    }
                    "--stdin" => from_stdin = true,
                    "--dry-run" => dry_run = true,
                    "--no-grow" => allow_grow = false,
                    "--no-strip-avb" => strip_avb = false,
                    other => pos.push(other.to_string()),
                }
                i += 1;
            }
            let img_path = require_pos(&pos, 0, "image")?;
            let internal = require_pos(&pos, 1, "internal_path")?;

            let data = if let Some(fp) = from_file {
                fs::read(fp)?
            } else if from_stdin {
                let mut buf = Vec::new();
                io::stdin().read_to_end(&mut buf)?;
                buf
            } else {
                return Err(ErofsError::InvalidArg(
                    "edit write requires --from <file> or --stdin".into(),
                ));
            };

            if dry_run {
                let plan = plan_path_write(Path::new(img_path), internal, data.len() as u64, strip_avb)?;
                if json_mode {
                    println!("{}", serde_json::to_string_pretty(&plan).unwrap());
                } else {
                    println!("Plan [{:?}]: {}", plan.mode, plan.detail);
                    println!("Size: {} -> {} (+{}B grow)", plan.old_size, plan.new_size, plan.grow_bytes);
                }
            } else {
                let res = write_path_data(Path::new(img_path), internal, &data, allow_grow, strip_avb)?;
                if json_mode {
                    println!("{}", serde_json::to_string_pretty(&res).unwrap());
                } else {
                    println!("Wrote {} [{:?}]: {}", res.path, res.mode, res.detail);
                    println!("Size: {} -> {} (+{}B)", res.old_size, res.new_size, res.grew_bytes);
                }
            }
            Ok(())
        }
        "chmod" => {
            let img_path = require_pos(rest, 0, "image")?;
            let internal = require_pos(rest, 1, "internal_path")?;
            let octal_str = require_pos(rest, 2, "octal_mode")?;
            let clean = octal_str
                .strip_prefix("0o")
                .or_else(|| octal_str.strip_prefix("0O"))
                .unwrap_or(octal_str);
            let perm = u16::from_str_radix(clean, 8)
                .map_err(|_| ErofsError::InvalidArg(format!("invalid octal mode '{octal_str}'")))?;
            let new_mode = write_path_mode(Path::new(img_path), internal, perm)?;
            if json_mode {
                println!(
                    "{}",
                    serde_json::json!({
                        "path": internal,
                        "mode": new_mode,
                        "mode_octal": format!("{:04o}", new_mode & 0o7777),
                    })
                );
            } else {
                println!("Updated mode of {internal} to {:04o}", new_mode & 0o7777);
            }
            Ok(())
        }
        "selinux" | "chcon" => {
            let img_path = require_pos(rest, 0, "image")?;
            let internal = require_pos(rest, 1, "internal_path")?;
            let ctx = require_pos(rest, 2, "context")?;
            let updated = write_path_selinux(Path::new(img_path), internal, ctx)?;
            if json_mode {
                println!(
                    "{}",
                    serde_json::json!({ "path": internal, "selinux": updated })
                );
            } else {
                println!("Updated SELinux context of {internal} to {updated}");
            }
            Ok(())
        }
        "rm" | "unlink" => {
            let mut recursive = false;
            let mut strip_avb = true;
            let mut pos = Vec::new();
            for a in rest {
                match a.as_str() {
                    "--recursive" | "-r" => recursive = true,
                    "--no-strip-avb" => strip_avb = false,
                    other => pos.push(other.to_string()),
                }
            }
            let img_path = require_pos(&pos, 0, "image")?;
            let internal = require_pos(&pos, 1, "internal_path")?;
            let res = unlink_path(Path::new(img_path), internal, recursive, strip_avb)?;
            if json_mode {
                println!("{}", serde_json::to_string_pretty(&res).unwrap());
            } else {
                println!(
                    "Unlinked {} (nid={}, entries {} -> {}, mode {:?})",
                    res.removed, res.removed_nid, res.entries_before, res.entries_after, res.mode
                );
            }
            Ok(())
        }
        other => Err(ErofsError::InvalidArg(format!(
            "unknown edit subcommand '{other}' (expected write|chmod|selinux|rm)"
        ))),
    }
}

fn cmd_avb(args: &[String], json_mode: bool) -> Result<()> {
    let sub = require_pos(args, 0, "avb subcommand (inspect|strip)")?;
    let img_path = require_pos(args, 1, "image")?;
    match sub {
        "inspect" => {
            let mut f = File::open(img_path)?;
            let footer = read_avb_footer(&mut f)?;
            if json_mode {
                println!("{}", serde_json::to_string_pretty(&footer).unwrap());
            } else if let Some(avb) = footer {
                println!(
                    "AVB Footer v{}.{}: original_size={}B, vbmeta@{} ({}B), total_file={}B",
                    avb.version_major,
                    avb.version_minor,
                    avb.original_image_size,
                    avb.vbmeta_offset,
                    avb.vbmeta_size,
                    avb.total_file_size
                );
            } else {
                println!("No AVB footer present.");
            }
            Ok(())
        }
        "strip" => {
            let mut f = OpenOptions::new().read(true).write(true).open(img_path)?;
            let sb = read_sb_file(&mut f)?;
            let info = strip_avb_footer(&mut f, &sb)?;
            if json_mode {
                println!("{}", serde_json::to_string_pretty(&info).unwrap());
            } else {
                println!(
                    "Stripped AVB footer: truncated {}B -> {}B",
                    info.total_file_size, info.original_image_size
                );
            }
            Ok(())
        }
        other => Err(ErofsError::InvalidArg(format!(
            "unknown avb subcommand '{other}' (expected inspect|strip)"
        ))),
    }
}

fn cmd_resolve(args: &[String], offset: u64, json_mode: bool) -> Result<()> {
    let mut mounts: Vec<String> = Vec::new();
    let mut pos = Vec::new();
    let mut i = 0;
    while i < args.len() {
        match args[i].as_str() {
            "--mount" | "-m" => {
                i += 1;
                mounts.push(require_pos(args, i, "--mount")?.to_string());
            }
            other => pos.push(other.to_string()),
        }
        i += 1;
    }
    let img_path = require_pos(&pos, 0, "image")?;
    let internal = require_pos(&pos, 1, "internal_path")?;

    let main_img = ErofsImage::open(Path::new(img_path), offset)?;
    let inode = main_img.lookup(internal)?;
    if !inode.is_symlink() {
        return Err(ErofsError::InvalidPath(format!(
            "'{internal}' is not a symlink"
        )));
    }
    let target = main_img.read_symlink(&inode)?;

    let mut reg = MountRegistry::default();
    reg.insert(img_path.to_string(), &main_img.walk(false)?);
    for m in &mounts {
        if let Ok(other) = ErofsImage::open(Path::new(m), 0) {
            if let Ok(nodes) = other.walk(false) {
                reg.insert(m.clone(), &nodes);
            }
        }
    }

    let res = reg.resolve_symlink(img_path, internal, &target);
    if json_mode {
        println!("{}", serde_json::to_string_pretty(&res).unwrap());
    } else {
        println!("Symlink: {internal} -> {target}");
        println!(
            "Resolved: image={} path={} is_dir={} exists={}",
            res.target_image.as_deref().unwrap_or("-"),
            res.target_internal_path.as_deref().unwrap_or("-"),
            res.is_dir,
            res.exists
        );
    }
    Ok(())
}

fn require_pos<'a>(args: &'a [String], idx: usize, name: &str) -> Result<&'a str> {
    args.get(idx)
        .map(|s| s.as_str())
        .ok_or_else(|| ErofsError::InvalidArg(format!("missing required argument <{name}>")))
}

fn parse_u64(s: &str) -> Result<u64> {
    if let Some(hex) = s.strip_prefix("0x").or_else(|| s.strip_prefix("0X")) {
        u64::from_str_radix(hex, 16)
    } else {
        s.parse::<u64>()
    }
    .map_err(|_| ErofsError::InvalidArg(format!("invalid integer '{s}'")))
}

fn fmt_size(n: u64) -> String {
    if n < 1024 {
        return format!("{n} B");
    }
    let mut x = n as f64;
    for unit in ["KB", "MB", "GB", "TB"] {
        x /= 1024.0;
        if x < 1024.0 {
            return format!("{x:.2} {unit}");
        }
    }
    format!("{x:.2} PB")
}
