use std::fs::File;
use std::io::{self, BufRead, BufReader};
use std::path::Path;

const AID_ROOT: u32 = 0;
const AID_SYSTEM: u32 = 1000;
const AID_BLUETOOTH: u32 = 1002;
const AID_GRAPHICS: u32 = 1003;
const AID_WIFI: u32 = 1010;
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
const CAP_SETPCAP: u64 = 1 << 8;
const CAP_NET_ADMIN: u64 = 1 << 12;
const CAP_NET_RAW: u64 = 1 << 13;
const CAP_SYS_NICE: u64 = 1 << 23;
const CAP_AUDIT_CONTROL: u64 = 1 << 30;
const CAP_SYSLOG: u64 = 1 << 34;
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
    FsPathConfig { mode: 0o0500, uid: AID_ROOT, gid: AID_ROOT, capabilities: 0, prefix: Some("config") },
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
    FsPathConfig { mode: 0o0775, uid: AID_ROOT, gid: AID_ROOT, capabilities: 0, prefix: Some("data/preloads") },
    FsPathConfig { mode: 0o0771, uid: AID_SYSTEM, gid: AID_SYSTEM, capabilities: 0, prefix: Some("data") },
    FsPathConfig { mode: 0o0755, uid: AID_ROOT, gid: AID_SYSTEM, capabilities: 0, prefix: Some("mnt") },
    FsPathConfig { mode: 0o0755, uid: AID_ROOT, gid: AID_ROOT, capabilities: 0, prefix: Some("root") },
    FsPathConfig { mode: 0o0750, uid: AID_ROOT, gid: AID_SHELL, capabilities: 0, prefix: Some("sbin") },
    FsPathConfig { mode: 0o0777, uid: AID_ROOT, gid: AID_ROOT, capabilities: 0, prefix: Some("sdcard") },
    FsPathConfig { mode: 0o0751, uid: AID_ROOT, gid: AID_SDCARD_R, capabilities: 0, prefix: Some("storage") },
    FsPathConfig { mode: 0o0755, uid: AID_ROOT, gid: AID_SHELL, capabilities: 0, prefix: Some("system/bin") },
    FsPathConfig { mode: 0o0755, uid: AID_ROOT, gid: AID_ROOT, capabilities: 0, prefix: Some("system/etc/ppp") },
    FsPathConfig { mode: 0o0755, uid: AID_ROOT, gid: AID_SHELL, capabilities: 0, prefix: Some("system/vendor") },
    FsPathConfig { mode: 0o0755, uid: AID_ROOT, gid: AID_SHELL, capabilities: 0, prefix: Some("system/xbin") },
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
    FsPathConfig { mode: 0o0600, uid: AID_ROOT, gid: AID_ROOT, capabilities: 0, prefix: Some("default.prop") },
    FsPathConfig { mode: 0o0600, uid: AID_ROOT, gid: AID_ROOT, capabilities: 0, prefix: Some("system/etc/prop.default") },
    FsPathConfig { mode: 0o0600, uid: AID_ROOT, gid: AID_ROOT, capabilities: 0, prefix: Some("odm/build.prop") },
    FsPathConfig { mode: 0o0600, uid: AID_ROOT, gid: AID_ROOT, capabilities: 0, prefix: Some("odm/default.prop") },
    FsPathConfig { mode: 0o0444, uid: AID_ROOT, gid: AID_ROOT, capabilities: 0, prefix: Some("odm/etc/fs_config_dirs") },
    FsPathConfig { mode: 0o0444, uid: AID_ROOT, gid: AID_ROOT, capabilities: 0, prefix: Some("odm/etc/fs_config_files") },
    FsPathConfig { mode: 0o0600, uid: AID_ROOT, gid: AID_ROOT, capabilities: 0, prefix: Some("system/odm/build.prop") },
    FsPathConfig { mode: 0o0600, uid: AID_ROOT, gid: AID_ROOT, capabilities: 0, prefix: Some("system/odm/default.prop") },
    FsPathConfig { mode: 0o0444, uid: AID_ROOT, gid: AID_ROOT, capabilities: 0, prefix: Some("system/odm/etc/fs_config_dirs") },
    FsPathConfig { mode: 0o0444, uid: AID_ROOT, gid: AID_ROOT, capabilities: 0, prefix: Some("system/odm/etc/fs_config_files") },
    FsPathConfig { mode: 0o0444, uid: AID_ROOT, gid: AID_ROOT, capabilities: 0, prefix: Some("oem/etc/fs_config_dirs") },
    FsPathConfig { mode: 0o0444, uid: AID_ROOT, gid: AID_ROOT, capabilities: 0, prefix: Some("oem/etc/fs_config_files") },
    FsPathConfig { mode: 0o0444, uid: AID_ROOT, gid: AID_ROOT, capabilities: 0, prefix: Some("system/oem/etc/fs_config_dirs") },
    FsPathConfig { mode: 0o0444, uid: AID_ROOT, gid: AID_ROOT, capabilities: 0, prefix: Some("system/oem/etc/fs_config_files") },
    FsPathConfig { mode: 0o0750, uid: AID_ROOT, gid: AID_SHELL, capabilities: 0, prefix: Some("sbin/fs_mgr") },
    FsPathConfig { mode: 0o0755, uid: AID_ROOT, gid: AID_SHELL, capabilities: 0, prefix: Some("system/bin/crash_dump32") },
    FsPathConfig { mode: 0o0755, uid: AID_ROOT, gid: AID_SHELL, capabilities: 0, prefix: Some("system/bin/crash_dump64") },
    FsPathConfig { mode: 0o0755, uid: AID_ROOT, gid: AID_SHELL, capabilities: 0, prefix: Some("system/bin/debuggerd") },
    FsPathConfig { mode: 0o0750, uid: AID_ROOT, gid: AID_ROOT, capabilities: 0, prefix: Some("system/bin/install-recovery.sh") },
    FsPathConfig { mode: 0o0700, uid: AID_ROOT, gid: AID_ROOT, capabilities: 0, prefix: Some("system/bin/secilc") },
    FsPathConfig { mode: 0o0750, uid: AID_ROOT, gid: AID_ROOT, capabilities: 0, prefix: Some("system/bin/uncrypt") },
    FsPathConfig { mode: 0o0600, uid: AID_ROOT, gid: AID_ROOT, capabilities: 0, prefix: Some("system/build.prop") },
    FsPathConfig { mode: 0o0000, uid: AID_ROOT, gid: AID_ROOT, capabilities: 0, prefix: Some("system/etc/fs_config_dirs") },
    FsPathConfig { mode: 0o0444, uid: AID_ROOT, gid: AID_ROOT, capabilities: 0, prefix: Some("system/etc/fs_config_files") },
    FsPathConfig { mode: 0o0440, uid: AID_ROOT, gid: AID_SHELL, capabilities: 0, prefix: Some("system/etc/init.goldfish.rc") },
    FsPathConfig { mode: 0o0550, uid: AID_ROOT, gid: AID_SHELL, capabilities: 0, prefix: Some("system/etc/init.goldfish.sh") },
    FsPathConfig { mode: 0o0550, uid: AID_ROOT, gid: AID_SHELL, capabilities: 0, prefix: Some("system/etc/init.ril") },
    FsPathConfig { mode: 0o0555, uid: AID_ROOT, gid: AID_ROOT, capabilities: 0, prefix: Some("system/etc/ppp/*") },
    FsPathConfig { mode: 0o0555, uid: AID_ROOT, gid: AID_ROOT, capabilities: 0, prefix: Some("system/etc/rc.*") },
    FsPathConfig { mode: 0o0440, uid: AID_ROOT, gid: AID_ROOT, capabilities: 0, prefix: Some("system/etc/recovery.img") },
    FsPathConfig { mode: 0o0600, uid: AID_ROOT, gid: AID_ROOT, capabilities: 0, prefix: Some("system/vendor/build.prop") },
    FsPathConfig { mode: 0o0600, uid: AID_ROOT, gid: AID_ROOT, capabilities: 0, prefix: Some("system/vendor/default.prop") },
    FsPathConfig { mode: 0o0444, uid: AID_ROOT, gid: AID_ROOT, capabilities: 0, prefix: Some("system/vendor/etc/fs_config_dirs") },
    FsPathConfig { mode: 0o0444, uid: AID_ROOT, gid: AID_ROOT, capabilities: 0, prefix: Some("system/vendor/etc/fs_config_files") },
    FsPathConfig { mode: 0o0600, uid: AID_ROOT, gid: AID_ROOT, capabilities: 0, prefix: Some("vendor/build.prop") },
    FsPathConfig { mode: 0o0600, uid: AID_ROOT, gid: AID_ROOT, capabilities: 0, prefix: Some("vendor/default.prop") },
    FsPathConfig { mode: 0o0444, uid: AID_ROOT, gid: AID_ROOT, capabilities: 0, prefix: Some("vendor/etc/fs_config_dirs") },
    FsPathConfig { mode: 0o0444, uid: AID_ROOT, gid: AID_ROOT, capabilities: 0, prefix: Some("vendor/etc/fs_config_files") },
    FsPathConfig { mode: 0o6755, uid: AID_ROOT, gid: AID_ROOT, capabilities: 0, prefix: Some("system/xbin/procmem") },
    FsPathConfig { mode: 0o4750, uid: AID_ROOT, gid: AID_SHELL, capabilities: 0, prefix: Some("system/xbin/su") },
    FsPathConfig { mode: 0o0700, uid: AID_SYSTEM, gid: AID_SHELL, capabilities: CAP_BLOCK_SUSPEND, prefix: Some("system/bin/inputflinger") },
    FsPathConfig { mode: 0o0550, uid: AID_LOGD, gid: AID_LOGD, capabilities: CAP_SYSLOG | CAP_AUDIT_CONTROL | CAP_SETGID, prefix: Some("system/bin/logd") },
    FsPathConfig { mode: 0o0750, uid: AID_ROOT, gid: AID_SHELL, capabilities: CAP_SETUID | CAP_SETGID, prefix: Some("system/bin/run-as") },
    FsPathConfig { mode: 0o0755, uid: AID_SYSTEM, gid: AID_GRAPHICS, capabilities: CAP_SYS_NICE, prefix: Some("system/bin/surfaceflinger") },
    FsPathConfig { mode: 0o0755, uid: AID_WIFI, gid: AID_WIFI, capabilities: CAP_NET_ADMIN | CAP_NET_RAW, prefix: Some("system/vendor/bin/hostapd") },
    FsPathConfig { mode: 0o0755, uid: AID_WIFI, gid: AID_WIFI, capabilities: CAP_NET_ADMIN | CAP_NET_RAW, prefix: Some("vendor/bin/hostapd") },
    FsPathConfig { mode: 0o0700, uid: AID_BLUETOOTH, gid: AID_BLUETOOTH, capabilities: CAP_NET_ADMIN | CAP_SYS_NICE, prefix: Some("system/vendor/bin/hw/android.hardware.bluetooth@1.0-service") },
    FsPathConfig { mode: 0o0700, uid: AID_BLUETOOTH, gid: AID_BLUETOOTH, capabilities: CAP_NET_ADMIN | CAP_SYS_NICE, prefix: Some("vendor/bin/hw/android.hardware.bluetooth@1.0-service") },
    FsPathConfig { mode: 0o0755, uid: AID_WIFI, gid: AID_WIFI, capabilities: CAP_NET_ADMIN | CAP_NET_RAW, prefix: Some("system/vendor/bin/hw/android.hardware.wifi@1.0-service") },
    FsPathConfig { mode: 0o0755, uid: AID_WIFI, gid: AID_WIFI, capabilities: CAP_NET_ADMIN | CAP_NET_RAW, prefix: Some("vendor/bin/hw/android.hardware.wifi@1.0-service") },
    FsPathConfig { mode: 0o0750, uid: AID_ROOT, gid: AID_ROOT, capabilities: CAP_SETUID | CAP_SETGID | CAP_SETPCAP, prefix: Some("system/bin/webview_zygote32") },
    FsPathConfig { mode: 0o0750, uid: AID_ROOT, gid: AID_ROOT, capabilities: CAP_SETUID | CAP_SETGID | CAP_SETPCAP, prefix: Some("system/bin/webview_zygote64") },
    FsPathConfig { mode: 0o0755, uid: AID_ROOT, gid: AID_ROOT, capabilities: 0, prefix: Some("bin/*") },
    FsPathConfig { mode: 0o0640, uid: AID_ROOT, gid: AID_SHELL, capabilities: 0, prefix: Some("fstab.*") },
    FsPathConfig { mode: 0o0750, uid: AID_ROOT, gid: AID_SHELL, capabilities: 0, prefix: Some("init*") },
    FsPathConfig { mode: 0o0750, uid: AID_ROOT, gid: AID_SHELL, capabilities: 0, prefix: Some("sbin/*") },
    FsPathConfig { mode: 0o0755, uid: AID_ROOT, gid: AID_SHELL, capabilities: 0, prefix: Some("system/bin/*") },
    FsPathConfig { mode: 0o0755, uid: AID_ROOT, gid: AID_ROOT, capabilities: 0, prefix: Some("system/lib/valgrind/*") },
    FsPathConfig { mode: 0o0755, uid: AID_ROOT, gid: AID_ROOT, capabilities: 0, prefix: Some("system/lib64/valgrind/*") },
    FsPathConfig { mode: 0o0755, uid: AID_ROOT, gid: AID_SHELL, capabilities: 0, prefix: Some("system/vendor/bin/*") },
    FsPathConfig { mode: 0o0755, uid: AID_ROOT, gid: AID_SHELL, capabilities: 0, prefix: Some("system/vendor/xbin/*") },
    FsPathConfig { mode: 0o0755, uid: AID_ROOT, gid: AID_SHELL, capabilities: 0, prefix: Some("system/xbin/*") },
    FsPathConfig { mode: 0o0755, uid: AID_ROOT, gid: AID_SHELL, capabilities: 0, prefix: Some("vendor/bin/*") },
    FsPathConfig { mode: 0o0755, uid: AID_ROOT, gid: AID_SHELL, capabilities: 0, prefix: Some("vendor/xbin/*") },
    FsPathConfig { mode: 0o0644, uid: AID_ROOT, gid: AID_ROOT, capabilities: 0, prefix: None },
];

pub fn default_fs_config(
    path: &str,
    dir: bool,
    uid: &mut u32,
    gid: &mut u32,
    mode: &mut u32,
    capabilities: &mut u64,
) {
    let p = path.strip_prefix('/').unwrap_or(path);
    let table = if dir { ANDROID_DIRS } else { ANDROID_FILES };
    let mut matched = &table[table.len() - 1];

    for pc in table {
        let Some(prefix) = pc.prefix else {
            matched = pc;
            break;
        };
        if dir {
            if p.starts_with(prefix) {
                matched = pc;
                break;
            }
        } else if let Some(stem) = prefix.strip_suffix('*') {
            if p.starts_with(stem) {
                matched = pc;
                break;
            }
        } else if p == prefix {
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
    path: String,
    uid: u32,
    gid: u32,
    mode: u32,
    capabilities: u64,
}

pub struct CannedFsConfig {
    entries: Vec<CannedEntry>,
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
    if let Some(hex) = s.strip_prefix("0x").or_else(|| s.strip_prefix("0X")) {
        i64::from_str_radix(hex, 16).unwrap_or(0) as u64
    } else if s.starts_with('0') && s.len() > 1 {
        i64::from_str_radix(&s[1..], 8).unwrap_or(0) as u64
    } else {
        s.parse::<i64>().unwrap_or(0) as u64
    }
}

impl CannedFsConfig {
    pub fn load(path: &Path) -> io::Result<Self> {
        let f = File::open(path)?;
        let reader = BufReader::new(f);
        let mut entries = Vec::new();

        for line in reader.lines() {
            let line = line?;
            let line = line.trim_end_matches(&['\r', '\n'][..]);
            if line.is_empty() {
                continue;
            }
            let mut parts = line.split(' ').filter(|s| !s.is_empty());
            let Some(p) = parts.next() else { continue };
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
            entries.push(CannedEntry {
                path: p.to_string(),
                uid,
                gid,
                mode,
                capabilities,
            });
        }

        entries.sort_by(|a, b| a.path.as_bytes().cmp(b.path.as_bytes()));
        println!("loaded {} fs_config entries", entries.len());
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
        match self
            .entries
            .binary_search_by(|e| e.path.as_bytes().cmp(key.as_bytes()))
        {
            Ok(idx) => {
                let e = &self.entries[idx];
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
            }
            Err(_) => {
                eprintln!("[{}] not found in canned fs_config, using defaults", key);
                default_fs_config(path, dir, uid, gid, mode, capabilities);
            }
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
