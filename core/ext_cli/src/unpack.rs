use std::collections::HashSet;
use std::fs::{self, File};
use std::io::{self, BufWriter, Write};
use std::path::{Path, PathBuf};

use crate::check::detect_type;
use crate::extfs::{InodeData, Volume, EXT4_ROOT_INO, FT_DIR};

#[cfg(windows)]
fn create_symlink_entry(target: &str, dest: &Path) -> io::Result<()> {
    use std::os::windows::ffi::OsStrExt;

    extern "system" {
        fn SetFileAttributesW(lpFileName: *const u16, dwFileAttributes: u32) -> i32;
    }

    const FILE_ATTRIBUTE_SYSTEM: u32 = 0x4;
    const SYMLINK_MAGIC: &[u8] = b"!<symlink>\xff\xfe";

    let mut data = Vec::with_capacity(SYMLINK_MAGIC.len() + (target.len() + 1) * 2);
    data.extend_from_slice(SYMLINK_MAGIC);
    for unit in target.encode_utf16() {
        data.extend_from_slice(&unit.to_le_bytes());
    }
    data.extend_from_slice(&0u16.to_le_bytes());

    if data.len() > 1024 {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            format!("symlink target exceeds 1024-byte limit: {}", target),
        ));
    }

    if let Some(parent) = dest.parent() {
        fs::create_dir_all(parent)?;
    }
    if dest.exists() {
        let _ = fs::remove_file(dest);
    }
    fs::write(dest, &data)?;

    let wide_path: Vec<u16> = dest
        .as_os_str()
        .encode_wide()
        .chain(std::iter::once(0))
        .collect();
    let ok = unsafe { SetFileAttributesW(wide_path.as_ptr(), FILE_ATTRIBUTE_SYSTEM) };
    if ok == 0 {
        return Err(io::Error::last_os_error());
    }
    Ok(())
}

#[cfg(not(windows))]
fn create_symlink_entry(target: &str, dest: &Path) -> io::Result<()> {
    if let Some(parent) = dest.parent() {
        fs::create_dir_all(parent)?;
    }
    if dest.symlink_metadata().is_ok() {
        let _ = fs::remove_file(dest);
    }
    std::os::unix::fs::symlink(target, dest)
}

fn escape_regex(s: &str) -> String {
    let mut out = String::with_capacity(s.len() + 8);
    for ch in s.chars() {
        if matches!(
            ch,
            '\\' | '^' | '$' | '.' | '|' | '?' | '*' | '+' | '(' | ')' | '{' | '}' | '[' | ']'
        ) {
            out.push('\\');
        }
        out.push(ch);
    }
    out
}

fn sanitize_local_component(s: &str, trim_trailing_dot: bool) -> String {
    let mut out = s.replace(' ', "_").replace('"', "");
    if trim_trailing_dot && out.ends_with('.') && cfg!(windows) {
        out.pop();
    }
    out
}

struct UnpackContext<'a> {
    partition_name: String,
    extract_dir: PathBuf,
    fs_config: Vec<String>,
    file_contexts: Vec<String>,
    seen_fc_regex: HashSet<String>,
    space_paths: Vec<String>,
    error_times: usize,
    total_entries_est: usize,
    processed_entries: usize,
    last_pct: u8,
    on_progress: &'a mut dyn FnMut(u8, &str),
}

impl<'a> UnpackContext<'a> {
    fn emit_progress(&mut self, pct: u8, stage: &str) {
        let clamped = pct.min(100);
        if clamped > self.last_pct || clamped == 0 || clamped == 100 {
            self.last_pct = clamped;
            (self.on_progress)(clamped, stage);
        }
    }

    fn push_file_context(&mut self, regex_str: String, ctx: &str) {
        if self.seen_fc_regex.insert(regex_str.clone()) {
            self.file_contexts.push(format!("{} {}", regex_str, ctx));
        }
    }

    fn scan_dir(
        &mut self,
        vol: &mut Volume,
        dir_inode: &InodeData,
        root_path: &str,
    ) -> io::Result<()> {
        let entries = vol.read_dir(dir_inode)?;
        for entry in entries {
            if entry.name == "." || entry.name == ".." || entry.name.ends_with(" (2)") {
                continue;
            }
            if self.error_times >= 200 {
                println!("Some thing wrong, stop scan!");
                break;
            }

            let entry_inode = vol.read_inode(entry.inode, entry.file_type)?;
            let entry_inode_path = format!("{}/{}", root_path, entry.name);

            if entry_inode_path.ends_with('/') && !entry_inode.is_dir() {
                self.error_times += 1;
                continue;
            }

            let mode = format!("{:04o}", entry_inode.i_mode & 0o7777);
            let uid = entry_inode.i_uid;
            let gid = entry_inode.i_gid;

            let tmp_raw = format!("{}{}", self.partition_name, entry_inode_path);
            let tmp_path = tmp_raw.trim_start_matches('/').to_string();

            let has_space = tmp_path.get(1..).map(|s| s.contains(' ')).unwrap_or(false);
            let out_path = if has_space {
                self.space_paths.push(tmp_path.clone());
                tmp_path.replace(' ', "_")
            } else {
                tmp_path.clone()
            };

            let mut cap = String::new();
            for (xname, xval) in vol.read_xattrs(&entry_inode)? {
                if xname == "security.selinux" {
                    let trimmed_len = xval
                        .iter()
                        .position(|&b| b == 0)
                        .unwrap_or(xval.len());
                    let ctx = String::from_utf8_lossy(&xval[..trimmed_len])
                        .trim_end()
                        .to_string();
                    if !ctx.is_empty() {
                        let regex_str = format!("/{}", escape_regex(&out_path));
                        self.push_file_context(regex_str, &ctx);
                    }
                } else if xname == "security.capability" && xval.len() >= 12 {
                    let magic_etc = u32::from_le_bytes([xval[0], xval[1], xval[2], xval[3]]);
                    let ver = magic_etc & 0xFF00_0000;
                    let cap_lo = u32::from_le_bytes([xval[4], xval[5], xval[6], xval[7]]) as u64;
                    let cap_hi = if ver >= 0x0200_0000 && xval.len() >= 16 {
                        u32::from_le_bytes([xval[12], xval[13], xval[14], xval[15]]) as u64
                    } else {
                        0
                    };
                    let cap_val = (cap_hi << 32) | cap_lo;
                    cap = format!(" capabilities={:#x}", cap_val);
                }
            }

            let link_target = if entry_inode.is_symlink() {
                let raw = vol.read_inode_bytes(&entry_inode)?;
                String::from_utf8_lossy(&raw).into_owned()
            } else {
                String::new()
            };

            let skip_fs_entry = root_path.is_empty() && entry.name == "lost+found";
            if !skip_fs_entry {
                if link_target.is_empty() {
                    self.fs_config
                        .push(format!("{} {} {} {}{}", out_path, uid, gid, mode, cap));
                } else {
                    self.fs_config.push(format!(
                        "{} {} {} {}{} {}",
                        out_path, uid, gid, mode, cap, link_target
                    ));
                }
            }

            let rel_disk = entry_inode_path.trim_start_matches('/');
            let mut local_rel = PathBuf::new();
            for comp in rel_disk.split('/') {
                if !comp.is_empty() {
                    local_rel.push(sanitize_local_component(comp, entry_inode.is_dir()));
                }
            }
            let target_path = self.extract_dir.join(&local_rel);

            if entry_inode.is_dir() {
                fs::create_dir_all(&target_path)?;
                self.scan_dir(vol, &entry_inode, &entry_inode_path)?;
            } else if entry_inode.is_file() {
                if let Some(parent) = target_path.parent() {
                    fs::create_dir_all(parent)?;
                }
                match File::create(&target_path) {
                    Ok(out_f) => {
                        let mut writer = BufWriter::with_capacity(1024 * 1024, out_f);
                        vol.stream_inode_to(&entry_inode, &mut writer)?;
                        writer.flush()?;
                    }
                    Err(e) => {
                        println!("[E] Cannot write to {}: {}", target_path.display(), e);
                    }
                }
            } else if entry_inode.is_symlink() {
                if let Err(e) = create_symlink_entry(&link_target, &target_path) {
                    println!(
                        "[W] Cannot create symlink {}: {}",
                        target_path.display(),
                        e
                    );
                }
            }
            self.processed_entries += 1;
            let pct = 5 + ((self.processed_entries * 88) / self.total_entries_est.max(1)).min(88) as u8;
            self.emit_progress(pct, "Extracting EXT files");
        }
        Ok(())
    }
}

pub fn unpack_image(img_path: &Path) -> io::Result<PathBuf> {
    unpack_image_with_progress(img_path, |_, _| {})
}

pub fn unpack_image_with_progress<F>(img_path: &Path, mut on_progress: F) -> io::Result<PathBuf>
where
    F: FnMut(u8, &str),
{
    on_progress(1, "Opening EXT image");
    let root = img_path
        .parent()
        .filter(|p| !p.as_os_str().is_empty())
        .unwrap_or_else(|| Path::new("."));
    let partition_name = img_path
        .file_stem()
        .and_then(|s| s.to_str())
        .unwrap_or("image")
        .to_string();

    let part_dir = root.join(&partition_name);
    let config_dir = root.join("config");

    if part_dir.is_dir() {
        fs::remove_dir_all(&part_dir)?;
    }
    fs::create_dir_all(&part_dir)?;
    fs::create_dir_all(&config_dir)?;

    let file = File::open(img_path)?;
    let mut vol = Volume::open(file)?;
    let det = detect_type(&vol.superblock);
    let engine_name = match det.fs_type {
        "EXT2" => "ext2",
        "EXT3" => "ext3",
        _ => "ext4",
    };
    println!("[ENGINE] Volume = {}", engine_name);

    let info_path = config_dir.join(format!("{}_info", partition_name));
    let filesystem_size = vol.superblock.s_blocks_count * vol.block_size;
    let info_text = format!(
        "PartitionName: {}\nFormat: {}\nSize: {}\nBlockSize: {}\nBlockCount: {}\nFreeBlocks: {}\nMountPoint: {}\nVolumeName: {}\nUUID: {}\n",
        partition_name,
        det.fs_type,
        filesystem_size,
        vol.block_size,
        vol.superblock.s_blocks_count,
        vol.superblock.s_free_blocks_count,
        vol.superblock.last_mounted(),
        vol.superblock.volume_name(),
        vol.superblock.uuid_string(),
    );
    fs::write(&info_path, info_text)?;

    let root_inode = vol.read_inode(EXT4_ROOT_INO, FT_DIR)?;
    let mut root_sel_label: Option<String> = None;
    for (xname, xval) in vol.read_xattrs(&root_inode)? {
        if xname == "security.selinux" {
            let trimmed_len = xval.iter().position(|&b| b == 0).unwrap_or(xval.len());
            let ctx = String::from_utf8_lossy(&xval[..trimmed_len])
                .trim_end()
                .to_string();
            if !ctx.is_empty() {
                root_sel_label = Some(ctx);
                break;
            }
        }
    }

    let used_inodes = vol
        .superblock
        .s_inodes_count
        .saturating_sub(vol.superblock.s_free_inodes_count)
        .saturating_sub(10)
        .max(1) as usize;

    let mut ctx = UnpackContext {
        partition_name: partition_name.clone(),
        extract_dir: part_dir.clone(),
        fs_config: Vec::new(),
        file_contexts: Vec::new(),
        seen_fc_regex: HashSet::new(),
        space_paths: Vec::new(),
        error_times: 0,
        total_entries_est: used_inodes,
        processed_entries: 0,
        last_pct: 4,
        on_progress: &mut on_progress,
    };

    ctx.emit_progress(5, "Extracting EXT files");
    ctx.scan_dir(&mut vol, &root_inode, "")?;
    ctx.emit_progress(94, "Writing Android configs");

    // Header entries for fs_config (preserve root inode ownership and permissions)
    let root_uid = root_inode.i_uid;
    let root_gid = root_inode.i_gid;
    let root_mode = root_inode.i_mode & 0o7777;
    ctx.fs_config.insert(
        0,
        format!("/ {} {} {:04o}", root_uid, root_gid, root_mode),
    );
    ctx.fs_config.insert(
        1,
        format!(
            "{} {} {} {:04o}",
            partition_name, root_uid, root_gid, root_mode
        ),
    );
    ctx.fs_config
        .insert(2, format!("{}/lost+found 0 0 0700", partition_name));

    // Header entries for file_contexts (sorted, deduplicated by regex)
    if !ctx.file_contexts.is_empty() {
        ctx.file_contexts.sort();

        let sel_label = root_sel_label
            .or_else(|| {
                ctx.file_contexts[0]
                    .split_ascii_whitespace()
                    .nth(1)
                    .map(|s| s.to_string())
            })
            .unwrap_or_else(|| "u:object_r:system_file:s0".to_string());

        let mut headers = vec![
            ("/".to_string(), format!("/ {}", sel_label)),
            (
                format!("/{}(/.*)?", partition_name),
                format!("/{}(/.*)? {}", partition_name, sel_label),
            ),
            (
                format!("/{}", partition_name),
                format!("/{} {}", partition_name, sel_label),
            ),
            (
                format!("/{}/lost\\+found", partition_name),
                format!("/{}/lost\\+found {}", partition_name, sel_label),
            ),
        ];

        // Check for SAR /system/system/build..prop pattern
        let has_sar_build_prop = ctx.file_contexts.iter().any(|line| {
            line.starts_with("/system/system/build")
                && line.split_ascii_whitespace().next().is_some_and(|p| p.contains("prop"))
        });

        // Prepend headers not already present (checked by regex token)
        let mut final_fc = Vec::with_capacity(ctx.file_contexts.len() + 6);
        let mut final_seen = HashSet::new();
        for (regex_key, full_line) in headers.drain(..) {
            if !ctx.seen_fc_regex.contains(&regex_key) && final_seen.insert(regex_key) {
                final_fc.push(full_line);
            }
        }
        if has_sar_build_prop {
            let lf_key = "/lost\\+found".to_string();
            if !ctx.seen_fc_regex.contains(&lf_key) && final_seen.insert(lf_key) {
                final_fc.push("/lost\\+found u:object_r:rootfs:s0".to_string());
            }
            let dbl_key = format!("/{}/{}/(/.*)?", partition_name, partition_name);
            if !ctx.seen_fc_regex.contains(&dbl_key) && final_seen.insert(dbl_key.clone()) {
                final_fc.push(format!("{} {}", dbl_key, sel_label));
            }
        }
        final_fc.extend(ctx.file_contexts);
        ctx.file_contexts = final_fc;
    }

    let fs_config_path = config_dir.join(format!("{}_fs_config", partition_name));
    let file_contexts_path = config_dir.join(format!("{}_file_contexts", partition_name));
    let space_path = config_dir.join(format!("{}_space.txt", partition_name));

    fs::write(&fs_config_path, ctx.fs_config.join("\n"))?;

    if !ctx.file_contexts.is_empty() {
        fs::write(&file_contexts_path, ctx.file_contexts.join("\n"))?;
    }

    if !ctx.space_paths.is_empty() {
        fs::write(&space_path, ctx.space_paths.join("\n"))?;
    }

    on_progress(100, "Done");

    Ok(part_dir)
}
