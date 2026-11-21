// SPDX-License-Identifier: MIT

//! Multi-threaded EROFS extractor with Android config export (`fs_config`, `file_contexts`,
//! `fs_options`), hardlink preservation, Windows-safe path handling, and digest verification.

use crate::android_cfg::write_android_configs;
use crate::error::{ErofsError, Result};
use crate::format::{FT_DIR, FT_REG_FILE, FT_SYMLINK};
use crate::reader::{ErofsImage, FileNode};
use rayon::prelude::*;
use serde::Serialize;
use sha2::{Digest, Sha256};
use std::collections::{HashMap, HashSet};
use std::fs::{self, File};
use std::io::Write;
use std::path::{Path, PathBuf};
use std::sync::Mutex;
use std::sync::atomic::{AtomicU64, AtomicUsize, Ordering};

#[derive(Debug, Clone)]
pub struct ExtractOptions {
    pub out_dir: PathBuf,
    pub image_base_name: String,
    pub targets: Vec<String>,
    pub recursive: bool,
    pub config_only: bool,
    pub write_configs: bool,
    pub overwrite: bool,
    pub verify_digest: bool,
    pub threads: usize,
    pub quiet: bool,
}

#[derive(Debug, Clone, Serialize)]
pub struct ExtractReport {
    pub extract_dir: String,
    pub config_dir: Option<String>,
    pub fs_config: Option<String>,
    pub file_contexts: Option<String>,
    pub fs_options: Option<String>,
    pub files_extracted: usize,
    pub dirs_created: usize,
    pub symlinks_created: usize,
    pub symlinks_skipped: usize,
    pub hardlinks_created: usize,
    pub bytes_written: u64,
    pub skipped_existing: usize,
    pub errors: Vec<String>,
}

/// Sanitize a single path component for Windows filesystems while keeping POSIX names intact.
pub fn sanitize_component(name: &str) -> String {
    #[cfg(windows)]
    {
        let mut s: String = name
            .chars()
            .map(|c| {
                if matches!(c, '<' | '>' | ':' | '"' | '|' | '?' | '*') || (c as u32) < 32 {
                    '_'
                } else {
                    c
                }
            })
            .collect();
        while s.ends_with(' ') || s.ends_with('.') {
            s.pop();
        }
        if s.is_empty() { "_".to_string() } else { s }
    }
    #[cfg(not(windows))]
    {
        name.to_string()
    }
}

/// Safely join an internal EROFS path onto `dest_dir`, rejecting path traversal (`..`).
pub fn safe_dest_path(dest_dir: &Path, internal_path: &str) -> Result<PathBuf> {
    let mut out = dest_dir.to_path_buf();
    for seg in internal_path.replace('\\', "/").split('/') {
        if seg.is_empty() || seg == "." {
            continue;
        }
        if seg == ".." {
            return Err(ErofsError::InvalidPath(format!(
                "unsafe path traversal in '{internal_path}'"
            )));
        }
        out.push(sanitize_component(seg));
    }
    Ok(out)
}

/// Extract an EROFS image (or target subset) into `opts.out_dir`.
pub fn extract_image(img: &ErofsImage, opts: &ExtractOptions) -> Result<ExtractReport> {
    let is_targeted = !opts.targets.is_empty();

    let mut nodes: Vec<FileNode> = if is_targeted {
        let mut collected = Vec::new();
        let mut seen_paths = HashSet::new();
        for t in &opts.targets {
            let sub = img.walk_path(t, opts.recursive)?;
            for n in sub {
                if seen_paths.insert(n.path.clone()) {
                    collected.push(n);
                }
            }
        }
        collected
    } else {
        img.walk(true)?
    };

    if nodes.is_empty() {
        return Err(ErofsError::NotFound("no matching entries to extract".into()));
    }

    // Sort paths so parent directories always precede children.
    nodes.sort_by(|a, b| a.path.cmp(&b.path));

    let extract_dir = opts.out_dir.join(&opts.image_base_name);
    let config_dir = opts.out_dir.join("config");

    let digest_xattr_name = if opts.verify_digest {
        let name = img.ishare_xattr_name().ok_or_else(|| {
            ErofsError::InvalidFormat(
                "image has no ishare xattr inode digests (--verify-digest cannot run)".into(),
            )
        })?;
        Some(name)
    } else {
        None
    };

    let configs = if opts.write_configs || opts.config_only {
        Some(write_android_configs(
            img,
            &nodes,
            &config_dir,
            &extract_dir,
            &opts.image_base_name,
            !is_targeted,
        )?)
    } else {
        None
    };

    if opts.config_only {
        return Ok(ExtractReport {
            extract_dir: extract_dir.display().to_string(),
            config_dir: Some(config_dir.display().to_string()),
            fs_config: configs.as_ref().map(|c| c.fs_config_path.display().to_string()),
            file_contexts: configs
                .as_ref()
                .map(|c| c.file_contexts_path.display().to_string()),
            fs_options: configs
                .as_ref()
                .and_then(|c| c.fs_options_path.as_ref())
                .map(|p| p.display().to_string()),
            files_extracted: 0,
            dirs_created: 0,
            symlinks_created: 0,
            symlinks_skipped: 0,
            hardlinks_created: 0,
            bytes_written: 0,
            skipped_existing: 0,
            errors: Vec::new(),
        });
    }

    fs::create_dir_all(&extract_dir)?;

    // Detect Windows case-insensitive collisions & partition dirs vs files/symlinks/hardlinks.
    let mut dir_nodes: Vec<&FileNode> = Vec::new();
    let mut primary_non_dirs: Vec<&FileNode> = Vec::new();
    let mut hardlink_pairs: Vec<(PathBuf, PathBuf)> = Vec::new();
    let mut hardlink_first: HashMap<u64, PathBuf> = HashMap::new();
    let mut seen_out: HashMap<String, String> = HashMap::new();
    let mut collision_warnings: Vec<String> = Vec::new();

    for n in &nodes {
        if n.path == "/" {
            continue;
        }
        let dst = safe_dest_path(&extract_dir, &n.path)?;
        #[cfg(windows)]
        let key = dst.to_string_lossy().to_ascii_lowercase();
        #[cfg(not(windows))]
        let key = dst.to_string_lossy().into_owned();

        if let Some(prev) = seen_out.insert(key, n.path.clone()) {
            if prev != n.path {
                collision_warnings.push(format!(
                    "case/sanitization collision on host FS: '{}' vs '{}' (merging/overwriting)",
                    prev, n.path
                ));
            }
        }

        if n.is_dir {
            dir_nodes.push(n);
        } else if !n.is_link && n.nlink > 1 {
            if let Some(src_path) = hardlink_first.get(&n.nid) {
                hardlink_pairs.push((src_path.clone(), dst));
            } else {
                hardlink_first.insert(n.nid, dst);
                primary_non_dirs.push(n);
            }
        } else {
            primary_non_dirs.push(n);
        }
    }

    let dirs_created = AtomicUsize::new(0);
    let files_extracted = AtomicUsize::new(0);
    let symlinks_created = AtomicUsize::new(0);
    let symlinks_skipped = AtomicUsize::new(0);
    let hardlinks_created = AtomicUsize::new(0);
    let skipped_existing = AtomicUsize::new(0);
    let bytes_written = AtomicU64::new(0);
    let errors: Mutex<Vec<String>> = Mutex::new(collision_warnings);
    let skipped_links_log: Mutex<Vec<(String, String)>> = Mutex::new(Vec::new());

    // 1. Create all directories sequentially in sorted parent-first order.
    for d in &dir_nodes {
        let dst = safe_dest_path(&extract_dir, &d.path)?;
        if let Err(e) = fs::create_dir_all(&dst) {
            errors
                .lock()
                .unwrap()
                .push(format!("mkdir {}: {e}", d.path));
        } else {
            dirs_created.fetch_add(1, Ordering::Relaxed);
        }
    }

    // 2. Extract regular files and symlinks in parallel using Rayon.
    let worker_fn = |n: &&FileNode| {
        let dst = match safe_dest_path(&extract_dir, &n.path) {
            Ok(p) => p,
            Err(e) => {
                errors.lock().unwrap().push(e.to_string());
                return;
            }
        };
        if let Some(par) = dst.parent() {
            let _ = fs::create_dir_all(par);
        }

        if dst.symlink_metadata().is_ok() {
            if !opts.overwrite {
                skipped_existing.fetch_add(1, Ordering::Relaxed);
                return;
            }
            let _ = fs::remove_file(&dst);
        }

        let inode = match img.read_inode(n.nid) {
            Ok(i) => i,
            Err(e) => {
                errors
                    .lock()
                    .unwrap()
                    .push(format!("read_inode {}: {e}", n.path));
                return;
            }
        };

        match inode.file_type() {
            FT_REG_FILE => match img.read_inode_bytes(&inode) {
                Ok(data) => {
                    if let Some(ref dig_name) = digest_xattr_name {
                        if let Err(msg) = verify_inode_digest(img, &inode, dig_name, &data) {
                            errors
                                .lock()
                                .unwrap()
                                .push(format!("digest {}: {msg}", n.path));
                            return;
                        }
                    }
                    match File::create(&dst).and_then(|mut f| f.write_all(&data)) {
                        Ok(()) => {
                            apply_unix_permissions(&dst, inode.mode);
                            bytes_written.fetch_add(data.len() as u64, Ordering::Relaxed);
                            files_extracted.fetch_add(1, Ordering::Relaxed);
                        }
                        Err(e) => {
                            errors
                                .lock()
                                .unwrap()
                                .push(format!("write {}: {e}", n.path));
                        }
                    }
                }
                Err(e) => {
                    errors
                        .lock()
                        .unwrap()
                        .push(format!("extract {}: {e}", n.path));
                }
            },
            FT_SYMLINK => {
                let target = n.target.as_deref().unwrap_or("");
                if create_symlink(target, &dst).is_ok() {
                    symlinks_created.fetch_add(1, Ordering::Relaxed);
                } else {
                    symlinks_skipped.fetch_add(1, Ordering::Relaxed);
                    skipped_links_log
                        .lock()
                        .unwrap()
                        .push((n.path.clone(), target.to_string()));
                }
            }
            FT_DIR => {
                let _ = fs::create_dir_all(&dst);
                dirs_created.fetch_add(1, Ordering::Relaxed);
            }
            _ => {
                // Device nodes / FIFOs / sockets cannot be created unprivileged on Windows;
                // recorded cleanly in fs_config/file_contexts.
            }
        }
    };

    if opts.threads == 1 {
        primary_non_dirs.iter().for_each(worker_fn);
    } else if opts.threads > 1 {
        let pool = rayon::ThreadPoolBuilder::new()
            .num_threads(opts.threads)
            .build()
            .map_err(|e| ErofsError::Io(std::io::Error::other(e.to_string())))?;
        pool.install(|| primary_non_dirs.par_iter().for_each(worker_fn));
    } else {
        primary_non_dirs.par_iter().for_each(worker_fn);
    }

    // 3. Create hardlinks after primary targets exist.
    for (src, dst) in hardlink_pairs {
        if let Some(par) = dst.parent() {
            let _ = fs::create_dir_all(par);
        }
        if dst.exists() {
            if !opts.overwrite {
                skipped_existing.fetch_add(1, Ordering::Relaxed);
                continue;
            }
            let _ = fs::remove_file(&dst);
        }
        match fs::hard_link(&src, &dst).or_else(|_| fs::copy(&src, &dst).map(|_| ())) {
            Ok(()) => {
                hardlinks_created.fetch_add(1, Ordering::Relaxed);
            }
            Err(e) => {
                errors
                    .lock()
                    .unwrap()
                    .push(format!("hardlink {} -> {}: {e}", dst.display(), src.display()));
            }
        }
    }

    let skipped_links = skipped_links_log.into_inner().unwrap();
    if !skipped_links.is_empty() && opts.write_configs {
        let log_path = config_dir.join(format!("{}_skipped_symlinks.txt", opts.image_base_name));
        if let Ok(mut f) = File::create(&log_path) {
            let _ = writeln!(f, "# Symlinks skipped by OS permissions (path -> target):");
            for (p, t) in &skipped_links {
                let _ = writeln!(f, "{p} -> {t}");
            }
        }
    }

    let err_list = errors.into_inner().unwrap();
    if !err_list.is_empty() && opts.write_configs {
        let ex_path = config_dir.join("exception.log");
        if let Ok(mut f) = File::create(&ex_path) {
            for e in &err_list {
                let _ = writeln!(f, "{e}");
            }
        }
    }

    Ok(ExtractReport {
        extract_dir: extract_dir.display().to_string(),
        config_dir: configs.as_ref().map(|_| config_dir.display().to_string()),
        fs_config: configs
            .as_ref()
            .map(|c| c.fs_config_path.display().to_string()),
        file_contexts: configs
            .as_ref()
            .map(|c| c.file_contexts_path.display().to_string()),
        fs_options: configs
            .as_ref()
            .and_then(|c| c.fs_options_path.as_ref())
            .map(|p| p.display().to_string()),
        files_extracted: files_extracted.load(Ordering::Relaxed),
        dirs_created: dirs_created.load(Ordering::Relaxed),
        symlinks_created: symlinks_created.load(Ordering::Relaxed),
        symlinks_skipped: symlinks_skipped.load(Ordering::Relaxed),
        hardlinks_created: hardlinks_created.load(Ordering::Relaxed),
        bytes_written: bytes_written.load(Ordering::Relaxed),
        skipped_existing: skipped_existing.load(Ordering::Relaxed),
        errors: err_list,
    })
}

fn verify_inode_digest(
    img: &ErofsImage,
    inode: &crate::format::Inode,
    xattr_name: &str,
    data: &[u8],
) -> std::result::Result<(), String> {
    let xattrs = img.read_xattrs(inode).map_err(|e| e.to_string())?;
    let Some(entry) = xattrs.iter().find(|x| x.name == xattr_name) else {
        return Ok(());
    };
    let actual = Sha256::digest(data);
    if entry.value.len() == 32 && entry.value.as_slice() == actual.as_slice() {
        return Ok(());
    }
    if entry.value.len() == 39
        && entry.value.starts_with(b"sha256:")
        && &entry.value[7..] == actual.as_slice()
    {
        return Ok(());
    }
    let hex_actual = actual.iter().map(|b| format!("{b:02x}")).collect::<String>();
    let val_str = entry.value_utf8_trimmed();
    let stripped = val_str.strip_prefix("sha256:").unwrap_or(&val_str);
    if stripped.eq_ignore_ascii_case(&hex_actual) {
        return Ok(());
    }
    Err(format!("SHA-256 mismatch (computed {hex_actual})"))
}

#[cfg(unix)]
fn apply_unix_permissions(path: &Path, mode: u16) {
    use std::os::unix::fs::PermissionsExt;
    let _ = fs::set_permissions(path, fs::Permissions::from_mode((mode & 0o7777) as u32));
}

#[cfg(not(unix))]
fn apply_unix_permissions(_path: &Path, _mode: u16) {}

#[cfg(unix)]
fn create_symlink(target: &str, dst: &Path) -> std::io::Result<()> {
    std::os::unix::fs::symlink(target, dst)
}

#[cfg(windows)]
fn create_symlink(target: &str, dst: &Path) -> std::io::Result<()> {
    std::os::windows::fs::symlink_file(target, dst)
}
