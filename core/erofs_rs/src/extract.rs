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
use std::time::{Duration, UNIX_EPOCH};

#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum SymlinkMode {
    /// Cygwin UTF-16LE `!<symlink>\xff\xfe` + `FILE_ATTRIBUTE_SYSTEM` on Windows; OS symlink on Unix.
    #[default]
    Auto,
    /// Always emit Cygwin UTF-16LE symlinks (`!<symlink>\xff\xfe`), compatible with Windows ROM kitchens.
    Cygwin,
    /// Always create OS-native symlinks (`symlink_file` on Windows, `symlink` on Unix).
    Native,
}

impl SymlinkMode {
    pub fn parse(s: &str) -> Result<Self> {
        match s.to_ascii_lowercase().as_str() {
            "auto" => Ok(Self::Auto),
            "cygwin" => Ok(Self::Cygwin),
            "native" | "os" => Ok(Self::Native),
            other => Err(ErofsError::InvalidArg(format!(
                "invalid symlink mode '{other}' (expected auto|cygwin|native)"
            ))),
        }
    }

    fn effective_cygwin(self) -> bool {
        match self {
            Self::Auto => cfg!(windows),
            Self::Cygwin => true,
            Self::Native => false,
        }
    }
}

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
    pub symlink_mode: SymlinkMode,
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
    extract_image_with_progress(img, opts, |_, _| {})
}

fn emit_monotonic<F: Fn(u8, &str)>(last: &Mutex<u8>, cb: &F, pct: u8, stage: &str) {
    let clamped = pct.min(100);
    let mut guard = last.lock().unwrap();
    if clamped > *guard || clamped == 0 {
        *guard = clamped.max(*guard);
        cb(clamped, stage);
    }
}

pub fn extract_image_with_progress<F>(
    img: &ErofsImage,
    opts: &ExtractOptions,
    on_progress: F,
) -> Result<ExtractReport>
where
    F: Fn(u8, &str) + Sync,
{
    let last_pct = Mutex::new(0u8);
    emit_monotonic(&last_pct, &on_progress, 1, "Scanning EROFS image");
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
        emit_monotonic(&last_pct, &on_progress, 6, "Writing Android configs");
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
        emit_monotonic(&last_pct, &on_progress, 100, "Done");
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

    let mut case_sensitive_dirs: HashSet<PathBuf> = HashSet::new();
    if enable_ntfs_case_sensitive(&extract_dir) {
        case_sensitive_dirs.insert(extract_dir.clone());
    }

    let dirs_created = AtomicUsize::new(0);
    let files_extracted = AtomicUsize::new(0);
    let symlinks_created = AtomicUsize::new(0);
    let symlinks_skipped = AtomicUsize::new(0);
    let hardlinks_created = AtomicUsize::new(0);
    let skipped_existing = AtomicUsize::new(0);
    let bytes_written = AtomicU64::new(0);
    let mut initial_errors: Vec<String> = Vec::new();

    // 1. Create all directories sequentially parent-first, enabling NTFS case-sensitivity while empty.
    emit_monotonic(&last_pct, &on_progress, 10, "Creating directories");
    let total_dirs = nodes.iter().filter(|n| n.path != "/" && n.is_dir).count().max(1);
    let mut dir_idx = 0usize;
    for n in &nodes {
        if n.path == "/" || !n.is_dir {
            continue;
        }
        dir_idx += 1;
        let pct = 10 + ((dir_idx * 10) / total_dirs).min(10) as u8;
        emit_monotonic(&last_pct, &on_progress, pct, "Creating directories");
        let dst = safe_dest_path(&extract_dir, &n.path)?;
        if let Err(e) = create_dir_all_case_sensitive(&dst, &extract_dir, &mut case_sensitive_dirs)
        {
            initial_errors.push(format!("mkdir {}: {e}", n.path));
        } else {
            dirs_created.fetch_add(1, Ordering::Relaxed);
        }
    }

    // 2. Partition non-directory nodes vs hardlinks and detect any remaining host-FS collisions.
    let mut primary_non_dirs: Vec<&FileNode> = Vec::new();
    let mut sequential_non_dirs: Vec<&FileNode> = Vec::new();
    let mut hardlink_pairs: Vec<(PathBuf, PathBuf)> = Vec::new();
    let mut hardlink_first: HashMap<u64, PathBuf> = HashMap::new();
    let mut seen_out: HashMap<String, String> = HashMap::new();

    for n in &nodes {
        if n.path == "/" || n.is_dir {
            continue;
        }
        let dst = safe_dest_path(&extract_dir, &n.path)?;
        let parent_cs = dst
            .parent()
            .map(|p| case_sensitive_dirs.contains(p))
            .unwrap_or(false);
        #[cfg(windows)]
        let key = if parent_cs {
            dst.to_string_lossy().into_owned()
        } else {
            dst.to_string_lossy().to_ascii_lowercase()
        };
        #[cfg(not(windows))]
        let key = {
            let _ = parent_cs;
            dst.to_string_lossy().into_owned()
        };

        let is_collision = if let Some(prev) = seen_out.insert(key, n.path.clone()) {
            if prev != n.path {
                initial_errors.push(format!(
                    "case/sanitization collision on host FS: '{}' vs '{}' (merging/overwriting)",
                    prev, n.path
                ));
                true
            } else {
                false
            }
        } else {
            false
        };

        if !n.is_link && n.nlink > 1 {
            if let Some(src_path) = hardlink_first.get(&n.nid) {
                hardlink_pairs.push((src_path.clone(), dst));
            } else {
                hardlink_first.insert(n.nid, dst);
                if is_collision {
                    sequential_non_dirs.push(n);
                } else {
                    primary_non_dirs.push(n);
                }
            }
        } else if is_collision {
            sequential_non_dirs.push(n);
        } else {
            primary_non_dirs.push(n);
        }
    }

    let errors: Mutex<Vec<String>> = Mutex::new(initial_errors);
    let skipped_links_log: Mutex<Vec<(String, String)>> = Mutex::new(Vec::new());
    let use_cygwin_symlinks = opts.symlink_mode.effective_cygwin();
    let total_extract_items = (primary_non_dirs.len() + sequential_non_dirs.len()).max(1);
    let done_extract_items = AtomicUsize::new(0);
    emit_monotonic(&last_pct, &on_progress, 22, "Extracting files");

    // 3. Extract regular files and symlinks in parallel using Rayon.
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

        if let Ok(existing) = dst.symlink_metadata() {
            if !opts.overwrite {
                skipped_existing.fetch_add(1, Ordering::Relaxed);
                return;
            }
            clear_restrictive_attrs(&dst);
            if existing.is_dir() {
                let _ = fs::remove_dir_all(&dst);
            } else {
                let _ = fs::remove_file(&dst);
            }
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
                    let write_res = File::create(&dst).and_then(|mut f| {
                        f.write_all(&data)?;
                        let _ = f.set_modified(UNIX_EPOCH + Duration::from_secs(inode.mtime));
                        Ok(())
                    });
                    match write_res {
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
                let link_res = if use_cygwin_symlinks {
                    create_cygwin_symlink(target, &dst)
                } else {
                    create_native_symlink(target, &dst)
                };
                if link_res.is_ok() {
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
        let done = done_extract_items.fetch_add(1, Ordering::Relaxed) + 1;
        let pct = 22 + ((done * 72) / total_extract_items).min(72) as u8;
        emit_monotonic(&last_pct, &on_progress, pct, "Extracting files");
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
    sequential_non_dirs.iter().for_each(worker_fn);

    // 4. Create hardlinks after primary targets exist.
    emit_monotonic(&last_pct, &on_progress, 95, "Finalizing hardlinks");
    for (src, dst) in hardlink_pairs {
        if let Some(par) = dst.parent() {
            let _ = fs::create_dir_all(par);
        }
        if dst.symlink_metadata().is_ok() {
            if !opts.overwrite {
                skipped_existing.fetch_add(1, Ordering::Relaxed);
                continue;
            }
            clear_restrictive_attrs(&dst);
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

    emit_monotonic(&last_pct, &on_progress, 100, "Done");

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
fn create_native_symlink(target: &str, dst: &Path) -> std::io::Result<()> {
    std::os::unix::fs::symlink(target, dst)
}

#[cfg(windows)]
fn create_native_symlink(target: &str, dst: &Path) -> std::io::Result<()> {
    std::os::windows::fs::symlink_file(target, dst)
}

fn create_cygwin_symlink(target: &str, dst: &Path) -> std::io::Result<()> {
    clear_restrictive_attrs(dst);
    let mut payload = Vec::with_capacity(12 + target.len() * 2 + 2);
    payload.extend_from_slice(b"!<symlink>\xFF\xFE");
    for code_unit in target.encode_utf16() {
        payload.extend_from_slice(&code_unit.to_le_bytes());
    }
    payload.extend_from_slice(&[0u8, 0u8]);
    let mut f = File::create(dst)?;
    f.write_all(&payload)?;
    drop(f);
    set_cygwin_system_attr(dst);
    Ok(())
}

fn create_dir_all_case_sensitive(
    dst: &Path,
    stop_root: &Path,
    tracked: &mut HashSet<PathBuf>,
) -> std::io::Result<()> {
    let mut missing = Vec::new();
    let mut cur = dst.to_path_buf();
    while !cur.exists() {
        missing.push(cur.clone());
        if cur == stop_root {
            break;
        }
        let Some(p) = cur.parent() else {
            break;
        };
        cur = p.to_path_buf();
    }
    for dir in missing.into_iter().rev() {
        if let Err(e) = fs::create_dir(&dir) {
            if e.kind() != std::io::ErrorKind::AlreadyExists {
                return Err(e);
            }
        }
        if enable_ntfs_case_sensitive(&dir) {
            tracked.insert(dir);
        }
    }
    if !tracked.contains(dst) && enable_ntfs_case_sensitive(dst) {
        tracked.insert(dst.to_path_buf());
    }
    Ok(())
}

#[cfg(windows)]
fn to_wide_null(path: &Path) -> Vec<u16> {
    use std::os::windows::ffi::OsStrExt;
    let raw: Vec<u16> = path.as_os_str().encode_wide().collect();
    // Prepend \\?\ for long paths on drive-letter absolute paths if needed.
    if raw.len() > 240 && !raw.starts_with(&[b'\\' as u16, b'\\' as u16]) {
        if raw.get(1) == Some(&(b':' as u16)) {
            let mut out = Vec::with_capacity(4 + raw.len() + 1);
            out.extend_from_slice(&[b'\\' as u16, b'\\' as u16, b'?' as u16, b'\\' as u16]);
            for u in raw {
                out.push(if u == b'/' as u16 { b'\\' as u16 } else { u });
            }
            out.push(0);
            return out;
        }
    }
    let mut out = raw;
    out.push(0);
    out
}

#[cfg(windows)]
mod win_fs {
    use core::ffi::c_void;
    use std::path::Path;

    const FILE_READ_ATTRIBUTES: u32 = 0x0000_0080;
    const FILE_WRITE_ATTRIBUTES: u32 = 0x0000_0100;
    const FILE_SHARE_READ: u32 = 1;
    const FILE_SHARE_WRITE: u32 = 2;
    const FILE_SHARE_DELETE: u32 = 4;
    const OPEN_EXISTING: u32 = 3;
    const FILE_FLAG_BACKUP_SEMANTICS: u32 = 0x0200_0000;
    const INVALID_HANDLE: *mut c_void = -1isize as *mut c_void;
    const FILE_ATTRIBUTE_SYSTEM: u32 = 0x0000_0004;
    const FILE_ATTRIBUTE_NORMAL: u32 = 0x0000_0080;
    const FILE_CASE_SENSITIVE_INFORMATION: i32 = 71;
    const FILE_CS_FLAG_CASE_SENSITIVE_DIR: u32 = 1;
    const STATUS_DIRECTORY_NOT_EMPTY: i32 = 0xC000_0101u32 as i32;

    #[repr(C)]
    struct IoStatusBlock {
        status_or_ptr: usize,
        information: usize,
    }

    #[link(name = "kernel32")]
    unsafe extern "system" {
        fn CreateFileW(
            file_name: *const u16,
            desired_access: u32,
            share_mode: u32,
            security_attributes: *mut c_void,
            creation_disposition: u32,
            flags_and_attributes: u32,
            template_file: *mut c_void,
        ) -> *mut c_void;
        fn CloseHandle(handle: *mut c_void) -> i32;
        fn SetFileAttributesW(file_name: *const u16, file_attributes: u32) -> i32;
    }

    #[link(name = "ntdll")]
    unsafe extern "system" {
        fn NtSetInformationFile(
            file_handle: *mut c_void,
            io_status_block: *mut IoStatusBlock,
            file_information: *mut c_void,
            length: u32,
            file_information_class: i32,
        ) -> i32;
        fn NtQueryInformationFile(
            file_handle: *mut c_void,
            io_status_block: *mut IoStatusBlock,
            file_information: *mut c_void,
            length: u32,
            file_information_class: i32,
        ) -> i32;
    }

    pub fn enable_ntfs_case_sensitive(dir: &Path) -> bool {
        let wide = super::to_wide_null(dir);
        unsafe {
            let h = CreateFileW(
                wide.as_ptr(),
                FILE_READ_ATTRIBUTES | FILE_WRITE_ATTRIBUTES,
                FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
                core::ptr::null_mut(),
                OPEN_EXISTING,
                FILE_FLAG_BACKUP_SEMANTICS,
                core::ptr::null_mut(),
            );
            if h == INVALID_HANDLE {
                return false;
            }
            let mut iosb = IoStatusBlock {
                status_or_ptr: 0,
                information: 0,
            };
            let mut flags = FILE_CS_FLAG_CASE_SENSITIVE_DIR;
            let status = NtSetInformationFile(
                h,
                &mut iosb,
                (&mut flags as *mut u32).cast::<c_void>(),
                4,
                FILE_CASE_SENSITIVE_INFORMATION,
            );
            let ok = if status == 0 {
                true
            } else if status == STATUS_DIRECTORY_NOT_EMPTY {
                let mut cur_flags = 0u32;
                let mut qiosb = IoStatusBlock {
                    status_or_ptr: 0,
                    information: 0,
                };
                let qstatus = NtQueryInformationFile(
                    h,
                    &mut qiosb,
                    (&mut cur_flags as *mut u32).cast::<c_void>(),
                    4,
                    FILE_CASE_SENSITIVE_INFORMATION,
                );
                qstatus == 0 && (cur_flags & FILE_CS_FLAG_CASE_SENSITIVE_DIR) != 0
            } else {
                false
            };
            CloseHandle(h);
            ok
        }
    }

    pub fn clear_restrictive_attrs(path: &Path) {
        let wide = super::to_wide_null(path);
        unsafe {
            SetFileAttributesW(wide.as_ptr(), FILE_ATTRIBUTE_NORMAL);
        }
    }

    pub fn set_cygwin_system_attr(path: &Path) {
        let wide = super::to_wide_null(path);
        unsafe {
            SetFileAttributesW(wide.as_ptr(), FILE_ATTRIBUTE_SYSTEM);
        }
    }
}

#[cfg(windows)]
use win_fs::{clear_restrictive_attrs, enable_ntfs_case_sensitive, set_cygwin_system_attr};

#[cfg(not(windows))]
fn enable_ntfs_case_sensitive(_dir: &Path) -> bool {
    true
}

#[cfg(not(windows))]
fn clear_restrictive_attrs(_path: &Path) {}

#[cfg(not(windows))]
fn set_cygwin_system_attr(_path: &Path) {}
