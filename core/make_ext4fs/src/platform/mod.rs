use std::ffi::OsString;
use std::fs;
use std::io;
use std::path::Path;
use std::time::UNIX_EPOCH;

use crate::ext4::types::{S_IFBLK, S_IFCHR, S_IFDIR, S_IFIFO, S_IFLNK, S_IFMT, S_IFREG, S_IFSOCK};

#[cfg(windows)]
pub const MODE_PERM_MASK: u32 = 0o0777;
#[cfg(not(windows))]
pub const MODE_PERM_MASK: u32 = 0o7777;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum EntryKind {
    Regular,
    Directory,
    Symlink,
    Chr,
    Blk,
    Fifo,
    Sock,
}

#[derive(Debug, Clone)]
pub struct StatInfo {
    pub kind: EntryKind,
    pub st_mode: u32,
    pub size: u64,
    pub mtime: u32,
    pub link_target: Option<String>,
}

pub fn scandir_sorted(dir: &Path) -> io::Result<Vec<OsString>> {
    let mut names = Vec::new();
    for entry in fs::read_dir(dir)? {
        let entry = entry?;
        let name = entry.file_name();
        if name == "." || name == ".." {
            continue;
        }
        names.push(name);
    }
    names.sort_by(|a, b| a.as_encoded_bytes().cmp(b.as_encoded_bytes()));
    Ok(names)
}

fn system_time_to_unix_u32(t: io::Result<std::time::SystemTime>) -> u32 {
    match t {
        Ok(st) => match st.duration_since(UNIX_EPOCH) {
            Ok(d) => d.as_secs() as u32,
            Err(e) => (-(e.duration().as_secs() as i64)) as u32,
        },
        Err(_) => 0,
    }
}

#[cfg(windows)]
pub fn lstat_entry(path: &Path, block_size: u32) -> io::Result<StatInfo> {
    use std::os::windows::fs::MetadataExt;

    const FILE_ATTRIBUTE_SYSTEM: u32 = 0x4;
    const SYMLINK_MAGIC: &[u8] = b"!<symlink>\xff\xfe";
    const SYMLINK_MAXSIZE: u64 = 1024;

    let meta = fs::metadata(path)?;
    let mtime = system_time_to_unix_u32(meta.modified());

    if meta.is_dir() {
        return Ok(StatInfo {
            kind: EntryKind::Directory,
            st_mode: S_IFDIR | 0o777,
            size: meta.len(),
            mtime,
            link_target: None,
        });
    }

    let attrs = meta.file_attributes();
    let fsize = meta.len();
    if (attrs & FILE_ATTRIBUTE_SYSTEM) != 0 && fsize <= SYMLINK_MAXSIZE {
        if let Ok(bytes) = fs::read(path) {
            if bytes.starts_with(SYMLINK_MAGIC) {
                let raw_u16_bytes = &bytes[SYMLINK_MAGIC.len()..];
                let mut units = Vec::with_capacity(raw_u16_bytes.len() / 2);
                for pair in raw_u16_bytes.chunks_exact(2) {
                    let u = u16::from_le_bytes([pair[0], pair[1]]);
                    if u == 0 {
                        break;
                    }
                    units.push(u);
                }
                let mut target = String::from_utf16_lossy(&units);
                let max_len = (block_size as usize).saturating_sub(1);
                if target.len() > max_len {
                    target.truncate(max_len);
                }
                let char_len = ((fsize as usize - SYMLINK_MAGIC.len()) / 2) as u64;
                return Ok(StatInfo {
                    kind: EntryKind::Symlink,
                    st_mode: S_IFLNK | 0o777,
                    size: char_len,
                    mtime,
                    link_target: Some(target),
                });
            }
        }
    }

    let perm = if meta.permissions().readonly() {
        0o444
    } else {
        0o666
    };
    Ok(StatInfo {
        kind: EntryKind::Regular,
        st_mode: S_IFREG | perm,
        size: fsize,
        mtime,
        link_target: None,
    })
}

#[cfg(not(windows))]
pub fn lstat_entry(path: &Path, block_size: u32) -> io::Result<StatInfo> {
    use std::os::unix::fs::MetadataExt;

    let meta = fs::symlink_metadata(path)?;
    let st_mode = meta.mode();
    let fmt = st_mode & S_IFMT;
    let mtime = meta.mtime() as u32;
    let size = meta.size();

    let (kind, link_target) = match fmt {
        S_IFREG => (EntryKind::Regular, None),
        S_IFDIR => (EntryKind::Directory, None),
        S_IFCHR => (EntryKind::Chr, None),
        S_IFBLK => (EntryKind::Blk, None),
        S_IFIFO => (EntryKind::Fifo, None),
        S_IFSOCK => (EntryKind::Sock, None),
        S_IFLNK => {
            let target_os = fs::read_link(path)?;
            let mut target = target_os.to_string_lossy().into_owned();
            let max_len = (block_size as usize).saturating_sub(1);
            if target.len() > max_len {
                target.truncate(max_len);
            }
            (EntryKind::Symlink, Some(target))
        }
        _ => {
            return Err(io::Error::new(
                io::ErrorKind::InvalidData,
                format!("unknown file type on {}", path.display()),
            ));
        }
    };

    Ok(StatInfo {
        kind,
        st_mode,
        size,
        mtime,
        link_target,
    })
}

#[allow(dead_code)]
fn _keep_imports() {
    let _ = (S_IFBLK, S_IFCHR, S_IFIFO, S_IFMT, S_IFSOCK);
}
