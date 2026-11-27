// SPDX-License-Identifier: MIT

//! Thread-safe random-access image I/O abstraction (raw files, offset slices, and Android Sparse).
//! Uses an independent per-worker `File` handle pool on raw images to avoid Windows synchronous
//! `FILE_OBJECT` kernel lock contention during parallel reads.

use crate::error::{ErofsError, Result};
use crate::sparse::{SparseImage, is_sparse_file};
use std::fs::File;
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};

/// Random-access backing trait for virtual/composite image sources (e.g., LP extents in super.img).
pub trait RandomAccessBacking: Send + Sync {
    fn len(&self) -> u64;
    fn is_sparse(&self) -> bool {
        false
    }
    fn read_at(&self, pos: u64, buf: &mut [u8]) -> Result<usize>;
}

/// Thread-safe random-access image backing store.
pub enum ImageBacking {
    Raw {
        path: PathBuf,
        pool: Mutex<Vec<File>>,
        offset: u64,
        len: u64,
    },
    Sparse {
        sparse: SparseImage,
        offset: u64,
    },
    Memory {
        data: Arc<Vec<u8>>,
    },
    Custom(Arc<dyn RandomAccessBacking>),
}

#[derive(Clone)]
pub struct ImageSource {
    inner: Arc<ImageBacking>,
}

impl ImageSource {
    pub fn from_bytes(data: Vec<u8>) -> Self {
        Self {
            inner: Arc::new(ImageBacking::Memory {
                data: Arc::new(data),
            }),
        }
    }

    pub fn from_custom(backing: Arc<dyn RandomAccessBacking>) -> Self {
        Self {
            inner: Arc::new(ImageBacking::Custom(backing)),
        }
    }

    pub fn open(path: &Path, offset: u64) -> Result<Self> {
        if offset == 0 && is_sparse_file(path) {
            let sparse = SparseImage::open(path)?;
            return Ok(Self {
                inner: Arc::new(ImageBacking::Sparse { sparse, offset: 0 }),
            });
        }
        let file = File::open(path)?;
        let file_len = file.metadata()?.len();
        if offset > file_len {
            return Err(ErofsError::InvalidArg(format!(
                "offset {offset} exceeds file size {file_len}"
            )));
        }
        Ok(Self {
            inner: Arc::new(ImageBacking::Raw {
                path: path.to_path_buf(),
                pool: Mutex::new(vec![file]),
                offset,
                len: file_len - offset,
            }),
        })
    }

    pub fn is_sparse(&self) -> bool {
        match &*self.inner {
            ImageBacking::Sparse { .. } => true,
            ImageBacking::Custom(b) => b.is_sparse(),
            _ => false,
        }
    }

    pub fn len(&self) -> u64 {
        match &*self.inner {
            ImageBacking::Raw { len, .. } => *len,
            ImageBacking::Sparse { sparse, offset } => sparse.total_size.saturating_sub(*offset),
            ImageBacking::Memory { data } => data.len() as u64,
            ImageBacking::Custom(b) => b.len(),
        }
    }

    pub fn is_empty(&self) -> bool {
        self.len() == 0
    }

    /// Read bytes at logical `pos` into `buf`, returning actual bytes read.
    pub fn read_at(&self, pos: u64, buf: &mut [u8]) -> Result<usize> {
        match &*self.inner {
            ImageBacking::Raw {
                path,
                pool,
                offset,
                len,
            } => {
                if pos >= *len || buf.is_empty() {
                    return Ok(0);
                }
                let avail = ((*len - pos) as usize).min(buf.len());
                let file = {
                    let mut guard = pool.lock().unwrap();
                    guard.pop()
                };
                let file = match file {
                    Some(f) => f,
                    None => File::open(path)?,
                };
                let res = read_file_at(&file, offset + pos, &mut buf[..avail]);
                pool.lock().unwrap().push(file);
                res
            }
            ImageBacking::Sparse { sparse, offset } => sparse.read_at(offset + pos, buf),
            ImageBacking::Memory { data } => {
                let len = data.len() as u64;
                if pos >= len || buf.is_empty() {
                    return Ok(0);
                }
                let start = pos as usize;
                let avail = (data.len() - start).min(buf.len());
                buf[..avail].copy_from_slice(&data[start..start + avail]);
                Ok(avail)
            }
            ImageBacking::Custom(b) => b.read_at(pos, buf),
        }
    }

    /// Read up to `len` bytes starting at `pos`.
    pub fn read_vec(&self, pos: u64, len: usize) -> Result<Vec<u8>> {
        let mut out = vec![0u8; len];
        let mut done = 0usize;
        while done < len {
            let n = self.read_at(pos + done as u64, &mut out[done..])?;
            if n == 0 {
                break;
            }
            done += n;
        }
        out.truncate(done);
        Ok(out)
    }

    pub fn read_exact_vec(&self, pos: u64, len: usize) -> Result<Vec<u8>> {
        let v = self.read_vec(pos, len)?;
        if v.len() < len {
            return Err(ErofsError::Corrupted(format!(
                "unexpected EOF at offset {pos}: wanted {len} bytes, got {}",
                v.len()
            )));
        }
        Ok(v)
    }
}

#[cfg(windows)]
fn read_file_at(file: &File, offset: u64, buf: &mut [u8]) -> Result<usize> {
    use std::os::windows::fs::FileExt;
    let mut done = 0usize;
    while done < buf.len() {
        let n = file.seek_read(&mut buf[done..], offset + done as u64)?;
        if n == 0 {
            break;
        }
        done += n;
    }
    Ok(done)
}

#[cfg(unix)]
fn read_file_at(file: &File, offset: u64, buf: &mut [u8]) -> Result<usize> {
    use std::os::unix::fs::FileExt;
    let mut done = 0usize;
    while done < buf.len() {
        let n = file.read_at(&mut buf[done..], offset + done as u64)?;
        if n == 0 {
            break;
        }
        done += n;
    }
    Ok(done)
}
