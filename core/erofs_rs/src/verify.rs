// SPDX-License-Identifier: MIT

//! EROFS integrity verifier (`verify` / `fsck`): validates superblock CRC32C,
//! directory entry ordering, inode readability, full decompression of all files,
//! and optional SHA-256 xattr inode digest checks.

use crate::error::Result;
use crate::reader::ErofsImage;
use rayon::prelude::*;
use serde::Serialize;
use sha2::{Digest, Sha256};
use std::sync::Mutex;
use std::sync::atomic::{AtomicU64, AtomicUsize, Ordering};

#[derive(Debug, Clone, Serialize)]
pub struct VerifyReport {
    pub ok: bool,
    pub crc_valid: Option<bool>,
    pub checked_inodes: usize,
    pub checked_files: usize,
    pub checked_dirs: usize,
    pub checked_bytes: u64,
    pub digests_verified: usize,
    pub errors: Vec<String>,
}

pub fn verify_image(
    img: &ErofsImage,
    check_data: bool,
    verify_digest: bool,
    threads: usize,
) -> Result<VerifyReport> {
    let summary = img.summary();
    let mut initial_errors = Vec::new();
    if summary.crc_valid == Some(false) {
        initial_errors.push(format!(
            "superblock CRC32C mismatch: stored=0x{:08x}, computed=0x{:08x}",
            summary.crc_stored.unwrap_or(0),
            summary.crc_computed.unwrap_or(0)
        ));
    }

    let digest_name = if verify_digest {
        match img.ishare_xattr_name() {
            Some(n) => Some(n),
            None => {
                initial_errors
                    .push("image has no ishare xattr inode digests for --verify-digest".into());
                None
            }
        }
    } else {
        None
    };

    let nodes = img.walk(true)?;
    let checked_files = AtomicUsize::new(0);
    let checked_dirs = AtomicUsize::new(0);
    let checked_bytes = AtomicU64::new(0);
    let digests_verified = AtomicUsize::new(0);
    let errors = Mutex::new(initial_errors);

    let check_one = |n: &crate::reader::FileNode| {
        let inode = match img.read_inode(n.nid) {
            Ok(i) => i,
            Err(e) => {
                errors
                    .lock()
                    .unwrap()
                    .push(format!("inode {}: {e}", n.path));
                return;
            }
        };

        if inode.is_dir() {
            checked_dirs.fetch_add(1, Ordering::Relaxed);
            match img.read_dir(&inode, true) {
                Ok(entries) => {
                    // Verify EROFS strict byte-wise ascending dirent ordering within directory
                    for w in entries.windows(2) {
                        if w[0].raw_name > w[1].raw_name {
                            errors.lock().unwrap().push(format!(
                                "unsorted dirents in {}: '{}' > '{}'",
                                n.path, w[0].name, w[1].name
                            ));
                            break;
                        }
                    }
                }
                Err(e) => {
                    errors.lock().unwrap().push(format!("dir {}: {e}", n.path));
                }
            }
        } else if inode.is_reg() {
            checked_files.fetch_add(1, Ordering::Relaxed);
            if check_data {
                match img.read_inode_bytes(&inode) {
                    Ok(data) => {
                        if data.len() as u64 != inode.size {
                            errors.lock().unwrap().push(format!(
                                "size mismatch {}: decoded {} != inode {}",
                                n.path,
                                data.len(),
                                inode.size
                            ));
                        }
                        checked_bytes.fetch_add(data.len() as u64, Ordering::Relaxed);
                        if let Some(ref dname) = digest_name {
                            if let Ok(xattrs) = img.read_xattrs(&inode) {
                                if let Some(ent) = xattrs.iter().find(|x| x.name == *dname) {
                                    let actual = Sha256::digest(&data);
                                    let hex_actual = actual
                                        .iter()
                                        .map(|b| format!("{b:02x}"))
                                        .collect::<String>();
                                    let val_str = ent.value_utf8_trimmed();
                                    let stripped = val_str.strip_prefix("sha256:").unwrap_or(&val_str);
                                    let ok = (ent.value.len() == 32
                                        && ent.value.as_slice() == actual.as_slice())
                                        || (ent.value.len() == 39
                                            && ent.value.starts_with(b"sha256:")
                                            && &ent.value[7..] == actual.as_slice())
                                        || stripped.eq_ignore_ascii_case(&hex_actual);
                                    if ok {
                                        digests_verified.fetch_add(1, Ordering::Relaxed);
                                    } else {
                                        errors
                                            .lock()
                                            .unwrap()
                                            .push(format!("SHA-256 digest mismatch: {}", n.path));
                                    }
                                }
                            }
                        }
                    }
                    Err(e) => {
                        errors
                            .lock()
                            .unwrap()
                            .push(format!("decompress/read {}: {e}", n.path));
                    }
                }
            }
        }
    };

    if threads == 1 {
        nodes.iter().for_each(check_one);
    } else if threads > 1 {
        let pool = rayon::ThreadPoolBuilder::new()
            .num_threads(threads)
            .build()
            .unwrap();
        pool.install(|| nodes.par_iter().for_each(check_one));
    } else {
        nodes.par_iter().for_each(check_one);
    }

    let err_list = errors.into_inner().unwrap();
    Ok(VerifyReport {
        ok: err_list.is_empty(),
        crc_valid: summary.crc_valid,
        checked_inodes: nodes.len(),
        checked_files: checked_files.load(Ordering::Relaxed),
        checked_dirs: checked_dirs.load(Ordering::Relaxed),
        checked_bytes: checked_bytes.load(Ordering::Relaxed),
        digests_verified: digests_verified.load(Ordering::Relaxed),
        errors: err_list,
    })
}
