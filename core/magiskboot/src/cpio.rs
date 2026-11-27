use std::collections::BTreeMap;
use std::env;
use std::fs;
use std::io::Write;
use std::path::{Path, PathBuf};

use memchr::memmem;

use crate::pattern::{patch_encryption, patch_verity};

pub const S_IFMT: u32 = 0o170000;
pub const S_IFLNK: u32 = 0o120000;
pub const S_IFREG: u32 = 0o100000;
pub const S_IFDIR: u32 = 0o040000;

const CPIO_MAGIC: &[u8; 6] = b"070701";
const CPIO_HDR_LEN: usize = 110;

const MAGISK_PATCHED: i32 = 1 << 0;
const UNSUPPORTED_CPIO: i32 = 1 << 1;
const SONY_INIT: i32 = 1 << 2;

const UNSUPPORTED_LIST: &[&str] = &[
    "sbin/launch_daemonsu.sh",
    "sbin/su",
    "init.xposed.rc",
    "boot/sbin/launch_daemonsu.sh",
];

const MAGISK_LIST: &[&str] = &[
    ".backup/.magisk",
    "init.magisk.rc",
    "overlay/init.magisk.rc",
];

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CpioEntry {
    pub mode: u32,
    pub uid: u32,
    pub gid: u32,
    pub data: Vec<u8>,
}

impl CpioEntry {
    pub fn new(mode: u32, uid: u32, gid: u32, data: Vec<u8>) -> Self {
        Self {
            mode,
            uid,
            gid,
            data,
        }
    }

    pub fn is_dir(&self) -> bool {
        (self.mode & S_IFMT) == S_IFDIR
    }

    pub fn is_reg(&self) -> bool {
        (self.mode & S_IFMT) == S_IFREG
    }

    pub fn is_lnk(&self) -> bool {
        (self.mode & S_IFMT) == S_IFLNK
    }
}

#[derive(Debug, Default, Clone)]
pub struct CpioArchive {
    pub entries: BTreeMap<String, CpioEntry>,
}

fn align4(n: usize) -> usize {
    (n + 3) & !3
}

fn parse_hex8(bytes: &[u8]) -> Result<u32, String> {
    let s = std::str::from_utf8(bytes).map_err(|_| "bad cpio header")?;
    u32::from_str_radix(s, 16).map_err(|_| "bad cpio header".to_string())
}

impl CpioArchive {
    pub fn new() -> Self {
        Self {
            entries: BTreeMap::new(),
        }
    }

    pub fn from_file(path: &str) -> Result<Self, String> {
        eprintln!("Loading cpio: [{path}]");
        let data = fs::read(path).map_err(|e| format!("Failed reading [{path}]: {e}"))?;
        Self::from_bytes(&data)
    }

    pub fn from_bytes(buf: &[u8]) -> Result<Self, String> {
        let mut archive = Self::new();
        let mut pos = 0usize;

        while pos + CPIO_HDR_LEN <= buf.len() {
            if &buf[pos..pos + 6] != CPIO_MAGIC {
                return Err("bad cpio header".into());
            }
            let mode = parse_hex8(&buf[pos + 14..pos + 22])?;
            let uid = parse_hex8(&buf[pos + 22..pos + 30])?;
            let gid = parse_hex8(&buf[pos + 30..pos + 38])?;
            let filesize = parse_hex8(&buf[pos + 54..pos + 62])? as usize;
            let namesize = parse_hex8(&buf[pos + 94..pos + 102])? as usize;

            pos += CPIO_HDR_LEN;
            if namesize == 0 || pos + namesize > buf.len() {
                return Err("bad cpio name size".into());
            }
            let raw_name = &buf[pos..pos + namesize];
            let name_end = raw_name.iter().position(|&b| b == 0).unwrap_or(raw_name.len());
            let name = String::from_utf8_lossy(&raw_name[..name_end]).into_owned();
            pos = align4(pos + namesize);

            if name == "." || name == ".." {
                pos = align4(pos + filesize);
                continue;
            }
            if name == "TRAILER!!!" {
                if let Some(rel) = memmem::find(&buf[pos..], CPIO_MAGIC) {
                    pos += rel;
                    continue;
                }
                break;
            }
            if pos + filesize > buf.len() {
                return Err("bad cpio file size".into());
            }
            let data = buf[pos..pos + filesize].to_vec();
            pos = align4(pos + filesize);
            archive
                .entries
                .insert(name, CpioEntry::new(mode, uid, gid, data));
        }
        Ok(archive)
    }

    pub fn to_bytes(&self) -> Vec<u8> {
        let estimated: usize = self
            .entries
            .iter()
            .map(|(k, v)| CPIO_HDR_LEN + k.len() + 4 + v.data.len() + 4)
            .sum::<usize>()
            + 128;
        let mut out = Vec::with_capacity(estimated);
        let mut inode: u32 = 300_000;

        for (name, entry) in &self.entries {
            write_cpio_record(
                &mut out,
                inode,
                entry.mode,
                entry.uid,
                entry.gid,
                name.as_bytes(),
                &entry.data,
            );
            inode += 1;
        }
        write_cpio_record(&mut out, inode, 0o755, 0, 0, b"TRAILER!!!", &[]);
        out
    }

    pub fn dump_file(&self, path: &str) -> Result<(), String> {
        eprintln!("Dump cpio: [{path}]");
        let bytes = self.to_bytes();
        let mut f = fs::File::create(path).map_err(|e| format!("Failed creating [{path}]: {e}"))?;
        f.write_all(&bytes)
            .map_err(|e| format!("Failed writing [{path}]: {e}"))
    }

    pub fn merge_from(&mut self, other: &CpioArchive) {
        for (name, entry) in &other.entries {
            self.entries.insert(name.clone(), entry.clone());
        }
    }

    pub fn exists(&self, name: &str) -> bool {
        self.entries.contains_key(name)
    }

    pub fn rm(&mut self, name: &str, recursive: bool) {
        if !recursive {
            if self.entries.remove(name).is_some() {
                eprintln!("Remove [{name}]");
            }
            return;
        }
        let prefix = format!("{name}/");
        let keys: Vec<String> = self
            .entries
            .keys()
            .filter(|k| *k == name || k.starts_with(&prefix))
            .cloned()
            .collect();
        for k in keys {
            eprintln!("Remove [{k}]");
            self.entries.remove(&k);
        }
    }

    pub fn add_file(&mut self, mode: u32, name: &str, infile: &str) -> Result<(), String> {
        let data = fs::read(infile).map_err(|e| format!("Failed reading [{infile}]: {e}"))?;
        self.entries
            .insert(name.to_string(), CpioEntry::new(S_IFREG | mode, 0, 0, data));
        eprintln!("Add entry [{name}] ({mode:04o})");
        Ok(())
    }

    pub fn mkdir(&mut self, mode: u32, name: &str) {
        self.entries.insert(
            name.to_string(),
            CpioEntry::new(S_IFDIR | mode, 0, 0, Vec::new()),
        );
        eprintln!("Create directory [{name}] ({mode:04o})");
    }

    pub fn ln(&mut self, target: &str, name: &str) {
        self.entries.insert(
            name.to_string(),
            CpioEntry::new(S_IFLNK, 0, 0, target.as_bytes().to_vec()),
        );
        eprintln!("Create symlink [{name}] -> [{target}]");
    }

    pub fn mv(&mut self, from: &str, to: &str) -> bool {
        if let Some(entry) = self.entries.remove(from) {
            eprintln!("Move [{from}] -> [{to}]");
            self.entries.insert(to.to_string(), entry);
            true
        } else {
            eprintln!("Cannot find entry {from}");
            false
        }
    }

    pub fn extract_all(&self) -> Result<(), String> {
        let _ = fs::remove_file("cpio");
        let _ = fs::remove_dir_all("ramdisk");
        fs::create_dir("ramdisk").map_err(|e| format!("Failed creating ramdisk dir: {e}"))?;

        let mut cfg = if cfg!(windows) {
            Some(
                fs::File::create("cpio")
                    .map_err(|e| format!("Failed creating cpio config: {e}"))?,
            )
        } else {
            None
        };

        for (name, entry) in &self.entries {
            let dest = Path::new("ramdisk").join(name);
            extract_single_entry(name, entry, &dest, cfg.as_mut())?;
        }
        Ok(())
    }

    pub fn extract_one(&self, name: &str, outfile: &str) -> Result<bool, String> {
        let Some(entry) = self.entries.get(name) else {
            eprintln!("Cannot find the file entry [{name}]");
            return Ok(false);
        };
        let mut cfg = if cfg!(windows) {
            fs::OpenOptions::new()
                .create(true)
                .append(true)
                .open("cpio")
                .ok()
        } else {
            None
        };
        extract_single_entry(name, entry, Path::new(outfile), cfg.as_mut())?;
        Ok(true)
    }

    pub fn load_dir(&mut self, dir: &str, config: &str, sync: bool) -> Result<(), String> {
        let mut dentries = BTreeMap::new();
        collect_dir(Path::new(dir), Path::new(dir), &mut dentries)?;

        if let Ok(cfg_text) = fs::read_to_string(config) {
            for line in cfg_text.lines() {
                if line.is_empty() || line.starts_with('#') {
                    continue;
                }
                let parts: Vec<&str> = line.split(' ').collect();
                if parts.len() < 4 {
                    return Err(format!("Ill-formed line in [{config}]"));
                }
                if let Some(e) = dentries.get_mut(parts[0]) {
                    let mode_bits = u32::from_str_radix(parts[1], 8).unwrap_or(0) & 0o777;
                    let uid = parts[2].parse::<u32>().unwrap_or(0);
                    let gid = parts[3].parse::<u32>().unwrap_or(0);
                    e.mode = (e.mode & S_IFMT) | mode_bits;
                    e.uid = uid;
                    e.gid = gid;
                }
            }
        }

        if !sync {
            self.entries = dentries;
            return Ok(());
        }

        let old_keys: Vec<String> = self.entries.keys().cloned().collect();
        for k in old_keys {
            if !dentries.contains_key(&k) {
                eprintln!("Remove [{k}]");
                self.entries.remove(&k);
            }
        }

        for (name, new_e) in dentries {
            match self.entries.get(&name) {
                None => {
                    eprintln!("Add new entry [{name}] ({:04o})", new_e.mode & 0o777);
                    self.entries.insert(name, new_e);
                }
                Some(old_e) if old_e != &new_e => {
                    eprintln!("Updated entry [{name}] ({:04o})", new_e.mode & 0o777);
                    self.entries.insert(name, new_e);
                }
                _ => {}
            }
        }
        Ok(())
    }

    pub fn test(&self) -> i32 {
        for f in UNSUPPORTED_LIST {
            if self.exists(f) {
                return UNSUPPORTED_CPIO;
            }
        }
        let mut ret = 0;
        for f in MAGISK_LIST {
            if self.exists(f) {
                ret |= MAGISK_PATCHED;
                break;
            }
        }
        if self.exists("init.real") {
            ret |= SONY_INIT;
        }
        ret
    }

    pub fn patch(&mut self) {
        let keep_verity = env::var("KEEPVERITY").is_ok_and(|v| v == "true");
        let keep_force_encrypt = env::var("KEEPFORCEENCRYPT").is_ok_and(|v| v == "true");
        eprintln!(
            "Patch with flag KEEPVERITY=[{}] KEEPFORCEENCRYPT=[{}]",
            keep_verity, keep_force_encrypt
        );

        let keys: Vec<String> = self.entries.keys().cloned().collect();
        for key in keys {
            let Some(entry) = self.entries.get_mut(&key) else {
                continue;
            };
            let is_fstab = (!keep_verity || !keep_force_encrypt)
                && entry.is_reg()
                && !key.starts_with(".backup")
                && !key.contains("twrp")
                && !key.contains("recovery")
                && key.contains("fstab");

            if !keep_verity {
                if is_fstab {
                    eprintln!("Found fstab file [{key}]");
                    let new_len = patch_verity(&mut entry.data);
                    entry.data.truncate(new_len);
                } else if key == "verity_key" {
                    self.rm(&key, false);
                    continue;
                }
            }
            if !keep_force_encrypt && is_fstab {
                let new_len = patch_encryption(&mut entry.data);
                entry.data.truncate(new_len);
            }
        }
    }

    pub fn sha1(&self) -> Option<String> {
        for (name, entry) in &self.entries {
            if name == "init.magisk.rc" || name == "overlay/init.magisk.rc" {
                let text = String::from_utf8_lossy(&entry.data);
                for line in text.lines() {
                    if let Some(rest) = line.strip_prefix("#STOCKSHA1=") {
                        let hash = rest
                            .trim()
                            .trim_matches('\'')
                            .trim_matches('"')
                            .chars()
                            .take(40)
                            .collect::<String>();
                        if !hash.is_empty() {
                            return Some(hash);
                        }
                    }
                }
            } else if name == ".backup/.magisk" {
                let text = String::from_utf8_lossy(&entry.data);
                for line in text.lines() {
                    if let Some(rest) = line.strip_prefix("SHA1=") {
                        let hash = rest.trim().chars().take(40).collect::<String>();
                        if !hash.is_empty() {
                            return Some(hash);
                        }
                    }
                }
            } else if name == ".backup/.sha1" {
                let s = String::from_utf8_lossy(&entry.data)
                    .trim_end_matches('\0')
                    .to_string();
                return Some(s);
            }
        }
        None
    }

    pub fn restore(&mut self) {
        let has_bk = self.entries.contains_key(".backup");
        let has_mg = self.entries.contains_key(".backup/.magisk");
        let rmlist = self.entries.get(".backup/.rmlist").map(|e| e.data.clone());
        let backups: Vec<String> = self
            .entries
            .keys()
            .filter(|k| {
                k.starts_with(".backup/")
                    && *k != ".backup/.rmlist"
                    && *k != ".backup/.magisk"
            })
            .cloned()
            .collect();

        if has_bk && has_mg && rmlist.is_none() && backups.is_empty() {
            eprintln!("Remove all in ramdisk");
            self.entries.clear();
            return;
        }

        self.rm(".backup", false);
        self.rm(".backup/.magisk", false);
        if let Some(rm_bytes) = rmlist {
            for item in rm_bytes.split(|&b| b == 0) {
                if !item.is_empty() {
                    let s = String::from_utf8_lossy(item).into_owned();
                    self.rm(&s, false);
                }
            }
            self.rm(".backup/.rmlist", false);
        }

        for bk_name in backups {
            let orig_name = bk_name.strip_prefix(".backup/").unwrap().to_string();
            self.mv(&bk_name, &orig_name);
        }
    }
}

pub fn backup_archive(current: &mut CpioArchive, orig_file: &str) -> Result<(), String> {
    let mut backups: BTreeMap<String, CpioEntry> = BTreeMap::new();
    backups.insert(
        ".backup".to_string(),
        CpioEntry::new(S_IFDIR, 0, 0, Vec::new()),
    );

    let mut orig = if Path::new(orig_file).exists() {
        CpioArchive::from_file(orig_file)?
    } else {
        CpioArchive::new()
    };

    orig.rm(".backup", true);
    current.rm(".backup", true);

    let mut rm_list = Vec::new();
    for name in current.entries.keys() {
        if !orig.entries.contains_key(name) {
            eprintln!("Record new entry: [{name}] -> [.backup/.rmlist]");
            rm_list.extend_from_slice(name.as_bytes());
            rm_list.push(0);
        }
    }

    for (name, orig_entry) in orig.entries {
        match current.entries.get(&name) {
            None => {
                let bk = format!(".backup/{name}");
                eprintln!("Backup missing entry: [{name}] -> [{bk}]");
                backups.insert(bk, orig_entry);
            }
            Some(cur_entry) if cur_entry.data != orig_entry.data => {
                let bk = format!(".backup/{name}");
                eprintln!("Backup mismatch entry: [{name}] -> [{bk}]");
                backups.insert(bk, orig_entry);
            }
            _ => {}
        }
    }

    if !rm_list.is_empty() {
        backups.insert(
            ".backup/.rmlist".to_string(),
            CpioEntry::new(S_IFREG, 0, 0, rm_list),
        );
    }

    if backups.len() > 1 {
        current.entries.extend(backups);
    }
    Ok(())
}

fn write_cpio_record(
    out: &mut Vec<u8>,
    ino: u32,
    mode: u32,
    uid: u32,
    gid: u32,
    name: &[u8],
    data: &[u8],
) {
    let namesize = name.len() + 1;
    let filesize = data.len();
    let hdr = format!(
        "070701{ino:08x}{mode:08x}{uid:08x}{gid:08x}{nlink:08x}{mtime:08x}{filesize:08x}{devmaj:08x}{devmin:08x}{rdevmaj:08x}{rdevmin:08x}{namesize:08x}{check:08x}",
        nlink = 1u32,
        mtime = 0u32,
        devmaj = 0u32,
        devmin = 0u32,
        rdevmaj = 0u32,
        rdevmin = 0u32,
        check = 0u32,
    );
    out.extend_from_slice(hdr.as_bytes());
    out.extend_from_slice(name);
    out.push(0);
    pad_zeros_to_4(out);
    if !data.is_empty() {
        out.extend_from_slice(data);
        pad_zeros_to_4(out);
    }
}

fn pad_zeros_to_4(out: &mut Vec<u8>) {
    let rem = out.len() & 3;
    if rem != 0 {
        out.resize(out.len() + (4 - rem), 0);
    }
}

fn extract_single_entry(
    name: &str,
    entry: &CpioEntry,
    dest: &Path,
    cfg: Option<&mut fs::File>,
) -> Result<(), String> {
    let disp = dest.to_string_lossy().replace('\\', "/");
    eprintln!("Extract [{name}] to [{disp}]");

    if let Ok(meta) = fs::symlink_metadata(dest) {
        if meta.is_dir() {
            let _ = fs::remove_dir_all(dest);
        } else {
            let _ = fs::remove_file(dest);
        }
    }
    if let Some(parent) = dest.parent() {
        if !parent.as_os_str().is_empty() {
            fs::create_dir_all(parent)
                .map_err(|e| format!("Failed creating parent dir for [{disp}]: {e}"))?;
        }
    }

    if entry.is_dir() {
        fs::create_dir_all(dest).map_err(|e| format!("Failed mkdir [{disp}]: {e}"))?;
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            let _ = fs::set_permissions(dest, fs::Permissions::from_mode(entry.mode & 0o777));
        }
    } else if entry.is_reg() {
        fs::write(dest, &entry.data).map_err(|e| format!("Failed writing [{disp}]: {e}"))?;
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            let _ = fs::set_permissions(dest, fs::Permissions::from_mode(entry.mode & 0o777));
        }
    } else if entry.is_lnk() && entry.data.len() < 4096 {
        let target = String::from_utf8_lossy(&entry.data);
        create_symlink(target.as_ref(), dest)?;
    }

    if let Some(f) = cfg {
        writeln!(f, "{name} {:o} {} {}", entry.mode & 0o777, entry.uid, entry.gid)
            .map_err(|e| format!("Failed writing cpio config: {e}"))?;
    }
    Ok(())
}

fn create_symlink(target: &str, dest: &Path) -> Result<(), String> {
    #[cfg(unix)]
    {
        std::os::unix::fs::symlink(target, dest)
            .map_err(|e| format!("Failed symlink {}: {e}", dest.display()))
    }
    #[cfg(windows)]
    {
        // Write Cygwin/libnt-compatible symlink file so unprivileged Windows runs never fail
        let mut buf = Vec::with_capacity(12 + (target.len() + 1) * 2);
        buf.extend_from_slice(b"!<symlink>\xff\xfe");
        for unit in target.encode_utf16() {
            buf.extend_from_slice(&unit.to_le_bytes());
        }
        buf.extend_from_slice(&0u16.to_le_bytes());
        fs::write(dest, &buf)
            .map_err(|e| format!("Failed writing symlink {}: {e}", dest.display()))?;
        set_windows_system_attr(dest);
        Ok(())
    }
}

#[cfg(windows)]
fn set_windows_system_attr(path: &Path) {
    use std::os::windows::ffi::OsStrExt;
    const FILE_ATTRIBUTE_SYSTEM: u32 = 0x4;
    const FILE_ATTRIBUTE_NORMAL: u32 = 0x80;
    #[link(name = "kernel32")]
    unsafe extern "system" {
        fn GetFileAttributesW(lpFileName: *const u16) -> u32;
        fn SetFileAttributesW(lpFileName: *const u16, dwFileAttributes: u32) -> i32;
    }
    let wide: Vec<u16> = path.as_os_str().encode_wide().chain(Some(0)).collect();
    unsafe {
        let cur = GetFileAttributesW(wide.as_ptr());
        let new_attr = if cur == u32::MAX {
            FILE_ATTRIBUTE_SYSTEM | FILE_ATTRIBUTE_NORMAL
        } else {
            cur | FILE_ATTRIBUTE_SYSTEM
        };
        SetFileAttributesW(wide.as_ptr(), new_attr);
    }
}

fn collect_dir(
    root: &Path,
    current: &Path,
    out: &mut BTreeMap<String, CpioEntry>,
) -> Result<(), String> {
    let read_dir =
        fs::read_dir(current).map_err(|e| format!("Failed reading {}: {e}", current.display()))?;
    for item in read_dir {
        let item = item.map_err(|e| format!("Dir entry error: {e}"))?;
        let path: PathBuf = item.path();
        let meta = fs::symlink_metadata(&path)
            .map_err(|e| format!("Metadata error {}: {e}", path.display()))?;
        let rel = path
            .strip_prefix(root)
            .map_err(|e| format!("Path strip error: {e}"))?
            .to_string_lossy()
            .replace('\\', "/");

        #[cfg(unix)]
        let (uid, gid, mode_bits) = {
            use std::os::unix::fs::MetadataExt;
            (meta.uid(), meta.gid(), meta.mode() & 0o777)
        };
        #[cfg(not(unix))]
        let (uid, gid, mode_bits) = (0u32, 0u32, if meta.is_dir() { 0o755 } else { 0o644 });

        if meta.file_type().is_symlink() {
            let target = fs::read_link(&path)
                .map_err(|e| format!("read_link failed {}: {e}", path.display()))?;
            let data = target.to_string_lossy().replace('\\', "/").into_bytes();
            out.insert(rel, CpioEntry::new(S_IFLNK | mode_bits, uid, gid, data));
        } else if meta.is_dir() {
            collect_dir(root, &path, out)?;
            out.insert(rel, CpioEntry::new(S_IFDIR | mode_bits, uid, gid, Vec::new()));
        } else if meta.is_file() {
            #[cfg(windows)]
            if let Some(target) = try_read_cygwin_symlink(&path, &meta) {
                out.insert(rel, CpioEntry::new(S_IFLNK | 0o644, uid, gid, target));
                continue;
            }
            let data =
                fs::read(&path).map_err(|e| format!("Failed reading {}: {e}", path.display()))?;
            out.insert(rel, CpioEntry::new(S_IFREG | mode_bits, uid, gid, data));
        }
    }
    Ok(())
}

#[cfg(windows)]
fn try_read_cygwin_symlink(path: &Path, meta: &fs::Metadata) -> Option<Vec<u8>> {
    use std::os::windows::fs::MetadataExt;
    const FILE_ATTRIBUTE_SYSTEM: u32 = 0x4;
    if (meta.file_attributes() & FILE_ATTRIBUTE_SYSTEM) == 0 || meta.len() > 2060 {
        return None;
    }
    let bytes = fs::read(path).ok()?;
    if !bytes.starts_with(b"!<symlink>\xff\xfe") {
        return None;
    }
    let payload = &bytes[12..];
    if payload.len() % 2 != 0 {
        return None;
    }
    let units: Vec<u16> = payload
        .chunks_exact(2)
        .map(|c| u16::from_le_bytes([c[0], c[1]]))
        .take_while(|&u| u != 0)
        .collect();
    let target = String::from_utf16(&units).ok()?;
    Some(target.into_bytes())
}

pub fn cmd_cpio(args: &[String]) -> Result<i32, String> {
    if args.is_empty() {
        return Err("Missing cpio arguments".into());
    }

    if args[0] == "pack" {
        if args.len() < 3 {
            return Err("Usage: cpio pack [-c <config>] <infolder> <outcpio>".into());
        }
        let (config, infolder, outcpio) = if args.len() == 5 && args[1] == "-c" {
            (args[2].as_str(), args[3].as_str(), args[4].as_str())
        } else if args.len() == 3 {
            ("cpio", args[1].as_str(), args[2].as_str())
        } else {
            return Err("Invalid cpio pack arguments".into());
        };
        let mut archive = CpioArchive::new();
        archive.load_dir(infolder, config, false)?;
        archive.dump_file(outcpio)?;
        return Ok(0);
    }

    let incpio = &args[0];
    let mut archive = if Path::new(incpio).exists() {
        CpioArchive::from_file(incpio)?
    } else {
        CpioArchive::new()
    };

    for raw_cmd in &args[1..] {
        let parts: Vec<&str> = raw_cmd.split(' ').filter(|s| !s.is_empty()).collect();
        if parts.is_empty() || parts[0].starts_with('#') {
            continue;
        }
        match parts.as_slice() {
            ["test"] => return Ok(archive.test()),
            ["restore"] => archive.restore(),
            ["sha1"] => {
                if let Some(s) = archive.sha1() {
                    println!("{s}");
                }
                return Ok(0);
            }
            ["patch"] => archive.patch(),
            ["exists", entry] => return Ok(if archive.exists(entry) { 0 } else { 1 }),
            ["backup", orig] => backup_archive(&mut archive, orig)?,
            ["rm", entry] => archive.rm(entry, false),
            ["rm", "-r", entry] => archive.rm(entry, true),
            ["mv", from, to] => {
                archive.mv(from, to);
            }
            ["extract"] => {
                archive.extract_all()?;
                return Ok(0);
            }
            ["extract", entry, out] => {
                return Ok(if archive.extract_one(entry, out)? {
                    0
                } else {
                    1
                });
            }
            ["sync"] => archive.load_dir("ramdisk", "cpio", true)?,
            ["mkdir", mode_s, entry] => {
                let mode = u32::from_str_radix(mode_s, 8)
                    .map_err(|_| format!("Invalid octal mode: {mode_s}"))?;
                archive.mkdir(mode, entry);
            }
            ["ln", target, entry] => archive.ln(target, entry),
            ["add", mode_s, entry, infile] => {
                let mode = u32::from_str_radix(mode_s, 8)
                    .map_err(|_| format!("Invalid octal mode: {mode_s}"))?;
                archive.add_file(mode, entry, infile)?;
            }
            _ => return Err(format!("Unknown cpio command: {raw_cmd}")),
        }
    }

    archive.dump_file(incpio)?;
    Ok(0)
}
