use std::collections::HashMap;
use std::fs::File;
use std::io::{self, BufRead, BufReader};
use std::path::Path;

const AID_ROOT: u32 = 0;
const AID_SYSTEM: u32 = 1000;
const AID_DHCP: u32 = 1014;
const AID_MEDIA_RW: u32 = 1023;
const AID_SDCARD_R: u32 = 1028;
const AID_LOGD: u32 = 1036;
const AID_SHARED_RELRO: u32 = 1037;
const AID_SHELL: u32 = 2000;
const AID_CACHE: u32 = 2001;
const AID_MISC: u32 = 9998;
const AID_APP: u32 = 10000;

const CAP_SETGID: u64 = 1 << 6;
const CAP_SETUID: u64 = 1 << 7;
const CAP_BLOCK_SUSPEND: u64 = 1 << 36;

struct FsPathConfig {
    mode: u32,
    uid: u32,
    gid: u32,
    capabilities: u64,
    prefix: Option<&'static str>,
}

const ANDROID_DIRS: &[FsPathConfig] = &[
    FsPathConfig { mode: 0o0770, uid: AID_SYSTEM, gid: AID_CACHE, capabilities: 0, prefix: Some("cache") },
    FsPathConfig { mode: 0o0555, uid: AID_ROOT, gid: AID_ROOT, capabilities: 0, prefix: Some("config") },
    FsPathConfig { mode: 0o0771, uid: AID_SYSTEM, gid: AID_SYSTEM, capabilities: 0, prefix: Some("data/app") },
    FsPathConfig { mode: 0o0771, uid: AID_SYSTEM, gid: AID_SYSTEM, capabilities: 0, prefix: Some("data/app-private") },
    FsPathConfig { mode: 0o0771, uid: AID_SYSTEM, gid: AID_SYSTEM, capabilities: 0, prefix: Some("data/app-ephemeral") },
    FsPathConfig { mode: 0o0771, uid: AID_ROOT, gid: AID_ROOT, capabilities: 0, prefix: Some("data/dalvik-cache") },
    FsPathConfig { mode: 0o0771, uid: AID_SYSTEM, gid: AID_SYSTEM, capabilities: 0, prefix: Some("data/data") },
    FsPathConfig { mode: 0o0771, uid: AID_SHELL, gid: AID_SHELL, capabilities: 0, prefix: Some("data/local/tmp") },
    FsPathConfig { mode: 0o0771, uid: AID_SHELL, gid: AID_SHELL, capabilities: 0, prefix: Some("data/local") },
    FsPathConfig { mode: 0o0770, uid: AID_DHCP, gid: AID_DHCP, capabilities: 0, prefix: Some("data/misc/dhcp") },
    FsPathConfig { mode: 0o0771, uid: AID_SHARED_RELRO, gid: AID_SHARED_RELRO, capabilities: 0, prefix: Some("data/misc/shared_relro") },
    FsPathConfig { mode: 0o1771, uid: AID_SYSTEM, gid: AID_MISC, capabilities: 0, prefix: Some("data/misc") },
    FsPathConfig { mode: 0o0775, uid: AID_MEDIA_RW, gid: AID_MEDIA_RW, capabilities: 0, prefix: Some("data/media/Music") },
    FsPathConfig { mode: 0o0775, uid: AID_MEDIA_RW, gid: AID_MEDIA_RW, capabilities: 0, prefix: Some("data/media") },
    FsPathConfig { mode: 0o0750, uid: AID_ROOT, gid: AID_SHELL, capabilities: 0, prefix: Some("data/nativetest") },
    FsPathConfig { mode: 0o0750, uid: AID_ROOT, gid: AID_SHELL, capabilities: 0, prefix: Some("data/nativetest64") },
    FsPathConfig { mode: 0o0750, uid: AID_ROOT, gid: AID_SHELL, capabilities: 0, prefix: Some("data/benchmarktest") },
    FsPathConfig { mode: 0o0750, uid: AID_ROOT, gid: AID_SHELL, capabilities: 0, prefix: Some("data/benchmarktest64") },
    FsPathConfig { mode: 0o0775, uid: AID_ROOT, gid: AID_ROOT, capabilities: 0, prefix: Some("data/preloads") },
    FsPathConfig { mode: 0o0771, uid: AID_SYSTEM, gid: AID_SYSTEM, capabilities: 0, prefix: Some("data") },
    FsPathConfig { mode: 0o0755, uid: AID_ROOT, gid: AID_SYSTEM, capabilities: 0, prefix: Some("mnt") },
    FsPathConfig { mode: 0o0751, uid: AID_ROOT, gid: AID_SHELL, capabilities: 0, prefix: Some("product/bin") },
    FsPathConfig { mode: 0o0751, uid: AID_ROOT, gid: AID_SHELL, capabilities: 0, prefix: Some("product/apex/*/bin") },
    FsPathConfig { mode: 0o0777, uid: AID_ROOT, gid: AID_ROOT, capabilities: 0, prefix: Some("sdcard") },
    FsPathConfig { mode: 0o0751, uid: AID_ROOT, gid: AID_SDCARD_R, capabilities: 0, prefix: Some("storage") },
    FsPathConfig { mode: 0o0751, uid: AID_ROOT, gid: AID_SHELL, capabilities: 0, prefix: Some("system/bin") },
    FsPathConfig { mode: 0o0755, uid: AID_ROOT, gid: AID_ROOT, capabilities: 0, prefix: Some("system/etc/ppp") },
    FsPathConfig { mode: 0o0755, uid: AID_ROOT, gid: AID_SHELL, capabilities: 0, prefix: Some("system/vendor") },
    FsPathConfig { mode: 0o0750, uid: AID_ROOT, gid: AID_SHELL, capabilities: 0, prefix: Some("system/xbin") },
    FsPathConfig { mode: 0o0751, uid: AID_ROOT, gid: AID_SHELL, capabilities: 0, prefix: Some("system/apex/*/bin") },
    FsPathConfig { mode: 0o0751, uid: AID_ROOT, gid: AID_SHELL, capabilities: 0, prefix: Some("system_ext/bin") },
    FsPathConfig { mode: 0o0751, uid: AID_ROOT, gid: AID_SHELL, capabilities: 0, prefix: Some("system_ext/apex/*/bin") },
    FsPathConfig { mode: 0o0751, uid: AID_ROOT, gid: AID_SHELL, capabilities: 0, prefix: Some("vendor/bin") },
    FsPathConfig { mode: 0o0751, uid: AID_ROOT, gid: AID_SHELL, capabilities: 0, prefix: Some("vendor/apex/*/bin") },
    FsPathConfig { mode: 0o0755, uid: AID_ROOT, gid: AID_SHELL, capabilities: 0, prefix: Some("vendor") },
    FsPathConfig { mode: 0o0755, uid: AID_ROOT, gid: AID_ROOT, capabilities: 0, prefix: None },
];

const ANDROID_FILES: &[FsPathConfig] = &[
    FsPathConfig { mode: 0o0644, uid: AID_SYSTEM, gid: AID_SYSTEM, capabilities: 0, prefix: Some("data/app/*") },
    FsPathConfig { mode: 0o0644, uid: AID_SYSTEM, gid: AID_SYSTEM, capabilities: 0, prefix: Some("data/app-ephemeral/*") },
    FsPathConfig { mode: 0o0644, uid: AID_SYSTEM, gid: AID_SYSTEM, capabilities: 0, prefix: Some("data/app-private/*") },
    FsPathConfig { mode: 0o0644, uid: AID_APP, gid: AID_APP, capabilities: 0, prefix: Some("data/data/*") },
    FsPathConfig { mode: 0o0644, uid: AID_MEDIA_RW, gid: AID_MEDIA_RW, capabilities: 0, prefix: Some("data/media/*") },
    FsPathConfig { mode: 0o0640, uid: AID_ROOT, gid: AID_SHELL, capabilities: 0, prefix: Some("data/nativetest/tests.txt") },
    FsPathConfig { mode: 0o0640, uid: AID_ROOT, gid: AID_SHELL, capabilities: 0, prefix: Some("data/nativetest64/tests.txt") },
    FsPathConfig { mode: 0o0750, uid: AID_ROOT, gid: AID_SHELL, capabilities: 0, prefix: Some("data/nativetest/*") },
    FsPathConfig { mode: 0o0750, uid: AID_ROOT, gid: AID_SHELL, capabilities: 0, prefix: Some("data/nativetest64/*") },
    FsPathConfig { mode: 0o0750, uid: AID_ROOT, gid: AID_SHELL, capabilities: 0, prefix: Some("data/benchmarktest/*") },
    FsPathConfig { mode: 0o0750, uid: AID_ROOT, gid: AID_SHELL, capabilities: 0, prefix: Some("data/benchmarktest64/*") },
    FsPathConfig { mode: 0o0600, uid: AID_ROOT, gid: AID_ROOT, capabilities: 0, prefix: Some("default.prop") },
    FsPathConfig { mode: 0o0600, uid: AID_ROOT, gid: AID_ROOT, capabilities: 0, prefix: Some("system/etc/prop.default") },
    FsPathConfig { mode: 0o0600, uid: AID_ROOT, gid: AID_ROOT, capabilities: 0, prefix: Some("odm/build.prop") },
    FsPathConfig { mode: 0o0600, uid: AID_ROOT, gid: AID_ROOT, capabilities: 0, prefix: Some("odm/default.prop") },
    FsPathConfig { mode: 0o0600, uid: AID_ROOT, gid: AID_ROOT, capabilities: 0, prefix: Some("odm/etc/build.prop") },
    FsPathConfig { mode: 0o0444, uid: AID_ROOT, gid: AID_ROOT, capabilities: 0, prefix: Some("odm/etc/fs_config_dirs") },
    FsPathConfig { mode: 0o0444, uid: AID_ROOT, gid: AID_ROOT, capabilities: 0, prefix: Some("odm/etc/fs_config_files") },
    FsPathConfig { mode: 0o0444, uid: AID_ROOT, gid: AID_ROOT, capabilities: 0, prefix: Some("oem/etc/fs_config_dirs") },
    FsPathConfig { mode: 0o0444, uid: AID_ROOT, gid: AID_ROOT, capabilities: 0, prefix: Some("oem/etc/fs_config_files") },
    FsPathConfig { mode: 0o0600, uid: AID_ROOT, gid: AID_ROOT, capabilities: 0, prefix: Some("product/build.prop") },
    FsPathConfig { mode: 0o0444, uid: AID_ROOT, gid: AID_ROOT, capabilities: 0, prefix: Some("product/etc/fs_config_dirs") },
    FsPathConfig { mode: 0o0444, uid: AID_ROOT, gid: AID_ROOT, capabilities: 0, prefix: Some("product/etc/fs_config_files") },
    FsPathConfig { mode: 0o0600, uid: AID_ROOT, gid: AID_ROOT, capabilities: 0, prefix: Some("system_ext/build.prop") },
    FsPathConfig { mode: 0o0444, uid: AID_ROOT, gid: AID_ROOT, capabilities: 0, prefix: Some("system_ext/etc/fs_config_dirs") },
    FsPathConfig { mode: 0o0444, uid: AID_ROOT, gid: AID_ROOT, capabilities: 0, prefix: Some("system_ext/etc/fs_config_files") },
    FsPathConfig { mode: 0o0755, uid: AID_ROOT, gid: AID_SHELL, capabilities: 0, prefix: Some("system/bin/crash_dump32") },
    FsPathConfig { mode: 0o0755, uid: AID_ROOT, gid: AID_SHELL, capabilities: 0, prefix: Some("system/bin/crash_dump64") },
    FsPathConfig { mode: 0o0755, uid: AID_ROOT, gid: AID_SHELL, capabilities: 0, prefix: Some("system/bin/debuggerd") },
    FsPathConfig { mode: 0o0550, uid: AID_LOGD, gid: AID_LOGD, capabilities: 0, prefix: Some("system/bin/logd") },
    FsPathConfig { mode: 0o0700, uid: AID_ROOT, gid: AID_ROOT, capabilities: 0, prefix: Some("system/bin/secilc") },
    FsPathConfig { mode: 0o0750, uid: AID_ROOT, gid: AID_ROOT, capabilities: 0, prefix: Some("system/bin/uncrypt") },
    FsPathConfig { mode: 0o0600, uid: AID_ROOT, gid: AID_ROOT, capabilities: 0, prefix: Some("system/build.prop") },
    FsPathConfig { mode: 0o0444, uid: AID_ROOT, gid: AID_ROOT, capabilities: 0, prefix: Some("system/etc/fs_config_dirs") },
    FsPathConfig { mode: 0o0444, uid: AID_ROOT, gid: AID_ROOT, capabilities: 0, prefix: Some("system/etc/fs_config_files") },
    FsPathConfig { mode: 0o0440, uid: AID_ROOT, gid: AID_SHELL, capabilities: 0, prefix: Some("system/etc/init.goldfish.rc") },
    FsPathConfig { mode: 0o0550, uid: AID_ROOT, gid: AID_SHELL, capabilities: 0, prefix: Some("system/etc/init.goldfish.sh") },
    FsPathConfig { mode: 0o0550, uid: AID_ROOT, gid: AID_SHELL, capabilities: 0, prefix: Some("system/etc/init.ril") },
    FsPathConfig { mode: 0o0555, uid: AID_ROOT, gid: AID_ROOT, capabilities: 0, prefix: Some("system/etc/ppp/*") },
    FsPathConfig { mode: 0o0555, uid: AID_ROOT, gid: AID_ROOT, capabilities: 0, prefix: Some("system/etc/rc.*") },
    FsPathConfig { mode: 0o0750, uid: AID_ROOT, gid: AID_ROOT, capabilities: 0, prefix: Some("vendor/bin/install-recovery.sh") },
    FsPathConfig { mode: 0o0600, uid: AID_ROOT, gid: AID_ROOT, capabilities: 0, prefix: Some("vendor/build.prop") },
    FsPathConfig { mode: 0o0600, uid: AID_ROOT, gid: AID_ROOT, capabilities: 0, prefix: Some("vendor/default.prop") },
    FsPathConfig { mode: 0o0440, uid: AID_ROOT, gid: AID_ROOT, capabilities: 0, prefix: Some("vendor/etc/recovery.img") },
    FsPathConfig { mode: 0o0444, uid: AID_ROOT, gid: AID_ROOT, capabilities: 0, prefix: Some("vendor/etc/fs_config_dirs") },
    FsPathConfig { mode: 0o0444, uid: AID_ROOT, gid: AID_ROOT, capabilities: 0, prefix: Some("vendor/etc/fs_config_files") },
    FsPathConfig { mode: 0o6755, uid: AID_ROOT, gid: AID_ROOT, capabilities: 0, prefix: Some("system/xbin/procmem") },
    FsPathConfig { mode: 0o4750, uid: AID_ROOT, gid: AID_SHELL, capabilities: 0, prefix: Some("system/xbin/su") },
    FsPathConfig { mode: 0o0700, uid: AID_SYSTEM, gid: AID_SHELL, capabilities: CAP_BLOCK_SUSPEND, prefix: Some("system/bin/inputflinger") },
    FsPathConfig { mode: 0o0750, uid: AID_ROOT, gid: AID_SHELL, capabilities: CAP_SETUID | CAP_SETGID, prefix: Some("system/bin/run-as") },
    FsPathConfig { mode: 0o0750, uid: AID_ROOT, gid: AID_SHELL, capabilities: CAP_SETUID | CAP_SETGID, prefix: Some("system/bin/simpleperf_app_runner") },
    FsPathConfig { mode: 0o0755, uid: AID_ROOT, gid: AID_ROOT, capabilities: 0, prefix: Some("first_stage_ramdisk/system/bin/e2fsck") },
    FsPathConfig { mode: 0o0755, uid: AID_ROOT, gid: AID_ROOT, capabilities: 0, prefix: Some("first_stage_ramdisk/system/bin/linker") },
    FsPathConfig { mode: 0o0755, uid: AID_ROOT, gid: AID_ROOT, capabilities: 0, prefix: Some("first_stage_ramdisk/system/bin/resize2fs") },
    FsPathConfig { mode: 0o0755, uid: AID_ROOT, gid: AID_ROOT, capabilities: 0, prefix: Some("first_stage_ramdisk/system/bin/snapuserd") },
    FsPathConfig { mode: 0o0755, uid: AID_ROOT, gid: AID_ROOT, capabilities: 0, prefix: Some("first_stage_ramdisk/system/bin/tune2fs") },
    FsPathConfig { mode: 0o0755, uid: AID_ROOT, gid: AID_ROOT, capabilities: 0, prefix: Some("bin/*") },
    FsPathConfig { mode: 0o0640, uid: AID_ROOT, gid: AID_SHELL, capabilities: 0, prefix: Some("fstab.*") },
    FsPathConfig { mode: 0o0750, uid: AID_ROOT, gid: AID_SHELL, capabilities: 0, prefix: Some("init*") },
    FsPathConfig { mode: 0o0755, uid: AID_ROOT, gid: AID_SHELL, capabilities: 0, prefix: Some("odm/bin/*") },
    FsPathConfig { mode: 0o0755, uid: AID_ROOT, gid: AID_SHELL, capabilities: 0, prefix: Some("product/bin/*") },
    FsPathConfig { mode: 0o0755, uid: AID_ROOT, gid: AID_SHELL, capabilities: 0, prefix: Some("product/apex/*bin/*") },
    FsPathConfig { mode: 0o0755, uid: AID_ROOT, gid: AID_SHELL, capabilities: 0, prefix: Some("system/bin/*") },
    FsPathConfig { mode: 0o0755, uid: AID_ROOT, gid: AID_SHELL, capabilities: 0, prefix: Some("system/xbin/*") },
    FsPathConfig { mode: 0o0755, uid: AID_ROOT, gid: AID_SHELL, capabilities: 0, prefix: Some("system/apex/*/bin/*") },
    FsPathConfig { mode: 0o0755, uid: AID_ROOT, gid: AID_SHELL, capabilities: 0, prefix: Some("system_ext/bin/*") },
    FsPathConfig { mode: 0o0755, uid: AID_ROOT, gid: AID_SHELL, capabilities: 0, prefix: Some("system_ext/apex/*/bin/*") },
    FsPathConfig { mode: 0o0755, uid: AID_ROOT, gid: AID_SHELL, capabilities: 0, prefix: Some("vendor/bin/*") },
    FsPathConfig { mode: 0o0755, uid: AID_ROOT, gid: AID_SHELL, capabilities: 0, prefix: Some("vendor/apex/*bin/*") },
    FsPathConfig { mode: 0o0755, uid: AID_ROOT, gid: AID_SHELL, capabilities: 0, prefix: Some("vendor/xbin/*") },
    FsPathConfig { mode: 0o0644, uid: AID_ROOT, gid: AID_ROOT, capabilities: 0, prefix: None },
];

/// POSIX `fnmatch(..., FNM_NOESCAPE)` style glob matching where `*` matches any sequence including `/`.
fn glob_match(pat: &[u8], text: &[u8]) -> bool {
    let (mut pi, mut ti) = (0usize, 0usize);
    let mut star_pi = None;
    let mut star_ti = 0usize;

    while ti < text.len() {
        if pi < pat.len() && pat[pi] == b'*' {
            star_pi = Some(pi);
            pi += 1;
            star_ti = ti;
        } else if pi < pat.len() && (pat[pi] == b'?' || pat[pi] == text[ti]) {
            pi += 1;
            ti += 1;
        } else if pi < pat.len() && pat[pi] == b'[' {
            if let Some((matched, next_pi)) = match_bracket(&pat[pi + 1..], text[ti]) {
                if matched {
                    pi = pi + 1 + next_pi;
                    ti += 1;
                    continue;
                }
            }
            if let Some(sp) = star_pi {
                pi = sp + 1;
                star_ti += 1;
                ti = star_ti;
            } else {
                return false;
            }
        } else if let Some(sp) = star_pi {
            pi = sp + 1;
            star_ti += 1;
            ti = star_ti;
        } else {
            return false;
        }
    }

    while pi < pat.len() && pat[pi] == b'*' {
        pi += 1;
    }
    pi == pat.len()
}

fn match_bracket(pat: &[u8], ch: u8) -> Option<(bool, usize)> {
    if pat.is_empty() {
        return None;
    }
    let mut idx = 0usize;
    let negate = pat[0] == b'!' || pat[0] == b'^';
    if negate {
        idx += 1;
    }
    let mut matched = false;
    let start = idx;
    while idx < pat.len() {
        if pat[idx] == b']' && idx > start {
            return Some((if negate { !matched } else { matched }, idx + 1));
        }
        if idx + 2 < pat.len() && pat[idx + 1] == b'-' && pat[idx + 2] != b']' {
            if ch >= pat[idx] && ch <= pat[idx + 2] {
                matched = true;
            }
            idx += 3;
        } else {
            if pat[idx] == ch {
                matched = true;
            }
            idx += 1;
        }
    }
    None
}

fn is_partition(path: &str) -> bool {
    const PARTITIONS: &[&str] = &["odm/", "oem/", "product/", "system_ext/", "vendor/"];
    PARTITIONS.iter().any(|p| path.starts_with(p))
}

fn fs_config_cmp(dir: bool, prefix: &str, path: &str) -> bool {
    let mut pattern = String::with_capacity(prefix.len() + 2);
    pattern.push_str(prefix);

    let mut input = String::with_capacity(path.len() + 1);
    input.push_str(path);

    if dir {
        if !input.ends_with('/') {
            input.push('/');
        }
        if !pattern.ends_with("/*") {
            if pattern.ends_with('/') {
                pattern.push('*');
            } else {
                pattern.push_str("/*");
            }
        }
    }

    if glob_match(pattern.as_bytes(), input.as_bytes()) {
        return true;
    }

    const LOGICAL_PARTITIONS: &[&str] = &[
        "system/product/",
        "system/system_ext/",
        "system/vendor/",
        "vendor/odm/",
    ];
    for lp in LOGICAL_PARTITIONS {
        if input.starts_with(lp) {
            if let Some(slash) = input.find('/') {
                let in_part = &input[slash + 1..];
                if is_partition(in_part) && glob_match(pattern.as_bytes(), in_part.as_bytes()) {
                    return true;
                }
            }
        }
    }
    false
}

pub fn default_fs_config(
    path: &str,
    dir: bool,
    uid: &mut u32,
    gid: &mut u32,
    mode: &mut u32,
    capabilities: &mut u64,
) {
    let mut p = path.strip_prefix('/').unwrap_or(path);
    if p.starts_with("system/system/") {
        p = &p["system/".len()..];
    }
    let table = if dir { ANDROID_DIRS } else { ANDROID_FILES };
    let mut matched = &table[table.len() - 1];

    for pc in table {
        let Some(prefix) = pc.prefix else {
            matched = pc;
            break;
        };
        if fs_config_cmp(dir, prefix, p) {
            matched = pc;
            break;
        }
    }

    *uid = matched.uid;
    *gid = matched.gid;
    *mode = (*mode & !0o7777) | matched.mode;
    *capabilities = matched.capabilities;
}

#[derive(Clone, Debug)]
struct CannedEntry {
    uid: u32,
    gid: u32,
    mode: u32,
    capabilities: u64,
}

pub struct CannedFsConfig {
    entries: HashMap<String, CannedEntry>,
}

fn parse_c_u32(s: &str, radix: u32) -> u32 {
    let s = s.trim();
    if let Ok(v) = i64::from_str_radix(s, radix) {
        return v as u32;
    }
    0
}

fn parse_c_u64_auto(s: &str) -> u64 {
    let s = s.trim();
    if let Some(neg) = s.strip_prefix('-') {
        return parse_c_u64_auto(neg).wrapping_neg();
    }
    let s = s.strip_prefix('+').unwrap_or(s);
    if let Some(hex) = s.strip_prefix("0x").or_else(|| s.strip_prefix("0X")) {
        u64::from_str_radix(hex, 16).unwrap_or(0)
    } else if s.starts_with('0') && s.len() > 1 {
        u64::from_str_radix(&s[1..], 8).unwrap_or(0)
    } else {
        s.parse::<u64>().unwrap_or(0)
    }
}

impl CannedFsConfig {
    pub fn load(path: &Path) -> io::Result<Self> {
        let f = File::open(path)?;
        let reader = BufReader::new(f);
        let mut entries = HashMap::new();
        let mut loaded_count = 0usize;

        for line_res in reader.lines() {
            let line_raw = line_res?;
            let mut line = line_raw.trim_end_matches(&['\r', '\n'][..]).to_string();
            if line.is_empty() {
                continue;
            }
            // AOSP historical format: root entry can start with a single space (" 0 0 0755").
            if line.starts_with(' ') {
                line.insert(0, '/');
            }
            let mut parts = line.split(' ').filter(|s| !s.is_empty());
            let Some(p_tok) = parts.next() else { continue };
            let key = p_tok.trim_start_matches('/').to_string();
            let uid = parts.next().map(|s| parse_c_u32(s, 10)).unwrap_or(0);
            let gid = parts.next().map(|s| parse_c_u32(s, 10)).unwrap_or(0);
            let mode = parts.next().map(|s| parse_c_u32(s, 8)).unwrap_or(0);
            let mut capabilities = 0u64;
            for tok in parts {
                if let Some(val) = tok.strip_prefix("capabilities=") {
                    capabilities = parse_c_u64_auto(val);
                    break;
                }
            }
            loaded_count += 1;
            // Insert overwrites prior entry for the same path ("last wins").
            entries.insert(
                key,
                CannedEntry {
                    uid,
                    gid,
                    mode,
                    capabilities,
                },
            );
        }

        println!("loaded {} fs_config entries", loaded_count);
        Ok(Self { entries })
    }

    pub fn lookup(
        &self,
        path: &str,
        dir: bool,
        uid: &mut u32,
        gid: &mut u32,
        mode: &mut u32,
        capabilities: &mut u64,
    ) {
        let key = path.trim_start_matches('/');
        if let Some(e) = self.entries.get(key) {
            if e.uid != u32::MAX {
                *uid = e.uid;
            }
            if e.gid != u32::MAX {
                *gid = e.gid;
            }
            if e.mode != u32::MAX {
                *mode = e.mode;
            }
            *capabilities = e.capabilities;
        } else {
            eprintln!("[{}] not found in canned fs_config, using defaults", key);
            default_fs_config(path, dir, uid, gid, mode, capabilities);
        }
    }
}

pub enum FsConfigProvider {
    None,
    Default,
    Canned(CannedFsConfig),
}

impl FsConfigProvider {
    pub fn apply(
        &self,
        path: &str,
        dir: bool,
        uid: &mut u32,
        gid: &mut u32,
        mode: &mut u32,
        capabilities: &mut u64,
    ) {
        match self {
            FsConfigProvider::None => {}
            FsConfigProvider::Default => {
                default_fs_config(path, dir, uid, gid, mode, capabilities);
            }
            FsConfigProvider::Canned(canned) => {
                canned.lookup(path, dir, uid, gid, mode, capabilities);
            }
        }
    }

    pub fn is_active(&self) -> bool {
        !matches!(self, FsConfigProvider::None)
    }
}
