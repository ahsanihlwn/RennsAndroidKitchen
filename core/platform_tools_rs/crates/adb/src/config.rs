//! CLI argument parser, `--version`, and `--help` output for `adb` (MIT).

use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, Ordering};

use pt_common::{ADB_PROTOCOL_VERSION_STR, PLATFORM_TOOLS_VERSION_FULL};

use crate::protocol::{AdbError, DEFAULT_ADB_PORT};

static EMBEDDED_MODE: AtomicBool = AtomicBool::new(false);

pub fn set_embedded_mode(enabled: bool) {
    EMBEDDED_MODE.store(enabled, Ordering::Relaxed);
}

pub fn is_embedded_mode() -> bool {
    EMBEDDED_MODE.load(Ordering::Relaxed)
}

/// Target device selection criteria specified via global options (`-d`, `-e`, `-s`, `-t`).
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct DeviceTarget {
    pub usb_only: bool,
    pub tcp_only: bool,
    pub serial: Option<String>,
    pub transport_id: Option<u64>,
}

impl DeviceTarget {
    /// Returns the classic `host:transport*` command for this target.
    pub fn transport_switch_cmd(&self) -> String {
        if let Some(tid) = self.transport_id {
            format!("host:transport-id:{tid}")
        } else if let Some(s) = &self.serial {
            format!("host:transport:{s}")
        } else if self.usb_only {
            "host:transport-usb".to_string()
        } else if self.tcp_only {
            "host:transport-local".to_string()
        } else {
            "host:transport-any".to_string()
        }
    }

    /// Formats a host-scoped command (`host-serial:<s >:<cmd>`, `host-usb:<cmd>`, `host:<cmd>`).
    pub fn scoped_host_cmd(&self, sub_cmd: &str) -> String {
        if let Some(tid) = self.transport_id {
            format!("host-transport-id:{tid}:{sub_cmd}")
        } else if let Some(s) = &self.serial {
            format!("host-serial:{s}:{sub_cmd}")
        } else if self.usb_only {
            format!("host-usb:{sub_cmd}")
        } else if self.tcp_only {
            format!("host-local:{sub_cmd}")
        } else {
            format!("host:{sub_cmd}")
        }
    }
}

/// Parsed ADB CLI command.
#[derive(Debug, Clone)]
pub enum AdbCommand {
    Version,
    Help { exit_code: i32 },
    Devices { long: bool },
    StartServer,
    KillServer,
    RunServerForeground,
    Connect(String),
    Disconnect(Option<String>),
    Pair {
        addr: String,
        code: Option<String>,
    },
    MdnsCheck,
    MdnsServices,
    ForwardList,
    Forward {
        no_rebind: bool,
        local: String,
        remote: String,
    },
    ForwardRemove(String),
    ForwardRemoveAll,
    ReverseList,
    Reverse {
        no_rebind: bool,
        remote: String,
        local: String,
    },
    ReverseRemove(String),
    ReverseRemoveAll,
    Push {
        sources: Vec<PathBuf>,
        remote: String,
        dry_run: bool,
        quiet: bool,
        compression: Option<String>,
    },
    Pull {
        remotes: Vec<String>,
        local: PathBuf,
        preserve_attrs: bool,
        quiet: bool,
        compression: Option<String>,
    },
    Sync {
        partition: Option<String>,
        list_only: bool,
        dry_run: bool,
        quiet: bool,
    },
    Shell {
        raw_mode: bool,
        command: Vec<String>,
    },
    Emu(Vec<String>),
    Install {
        flags: Vec<String>,
        apk: PathBuf,
    },
    InstallMultiple {
        flags: Vec<String>,
        apks: Vec<PathBuf>,
    },
    InstallMultiPackage {
        flags: Vec<String>,
        apks: Vec<PathBuf>,
    },
    Uninstall {
        keep_data: bool,
        package: String,
    },
    Bugreport(Option<PathBuf>),
    Jdwp,
    Logcat(Vec<String>),
    DisableVerity,
    EnableVerity,
    Keygen(PathBuf),
    WaitFor {
        transport: String,
        state: String,
    },
    GetState,
    GetSerialNo,
    GetDevPath,
    Remount {
        auto_reboot: bool,
    },
    Reboot(Option<String>),
    Sideload(PathBuf),
    Root,
    Unroot,
    Usb,
    Tcpip(u16),
    Reconnect(Option<String>),
    Attach,
    Detach,
    Features,
    HostFeatures,
}

/// Complete ADB invocation parsed from the command line.
#[derive(Debug, Clone)]
pub struct AdbCliConfig {
    pub listen_all: bool,
    pub server_host: String,
    pub server_port: u16,
    pub target: DeviceTarget,
    pub exit_on_write_error: bool,
    pub command: AdbCommand,
}

impl Default for AdbCliConfig {
    fn default() -> Self {
        let env_serial = std::env::var("ANDROID_SERIAL").ok();
        let env_port = std::env::var("ANDROID_ADB_SERVER_PORT")
            .ok()
            .and_then(|s| s.parse::<u16>().ok())
            .unwrap_or(DEFAULT_ADB_PORT);
        Self {
            listen_all: false,
            server_host: "127.0.0.1".to_string(),
            server_port: env_port,
            target: DeviceTarget {
                serial: env_serial,
                ..DeviceTarget::default()
            },
            exit_on_write_error: false,
            command: AdbCommand::Help { exit_code: 1 },
        }
    }
}

#[cfg(windows)]
fn host_os_version_string() -> String {
    #[repr(C)]
    struct OsVersionInfoW {
        dw_os_version_info_size: u32,
        dw_major_version: u32,
        dw_minor_version: u32,
        dw_build_number: u32,
        dw_platform_id: u32,
        sz_csd_version: [u16; 128],
    }

    #[link(name = "ntdll")]
    extern "system" {
        fn RtlGetVersion(lp_version_information: *mut OsVersionInfoW) -> i32;
    }

    unsafe {
        let mut info: OsVersionInfoW = std::mem::zeroed();
        info.dw_os_version_info_size = std::mem::size_of::<OsVersionInfoW>() as u32;
        if RtlGetVersion(&mut info) == 0 {
            return format!(
                "Windows {}.{}.{}",
                info.dw_major_version, info.dw_minor_version, info.dw_build_number
            );
        }
    }
    "Windows".to_string()
}

#[cfg(not(windows))]
fn host_os_version_string() -> String {
    format!("{} ({})", std::env::consts::OS, std::env::consts::ARCH)
}

/// Returns the 4-line `adb --version` / `adb version` output matching `platform-tools/adb` v37.0.3-16887417.
pub fn version_output() -> String {
    let exe = if is_embedded_mode() {
        "rak_native (embedded platform-tools-rs)".to_string()
    } else {
        std::env::current_exe()
            .map(|p| p.display().to_string())
            .unwrap_or_else(|_| "adb".to_string())
    };
    let os_line = host_os_version_string();
    format!(
        "Android Debug Bridge version {ADB_PROTOCOL_VERSION_STR}\n\
         Version {PLATFORM_TOOLS_VERSION_FULL}\n\
         Installed as {exe}\n\
         Running on {os_line}\n"
    )
}

#[cfg(windows)]
const LOCAL_AGENT_HELP_LINE: &str = "";
#[cfg(not(windows))]
const LOCAL_AGENT_HELP_LINE: &str =
    "     --local-agent: locate agent files from local source build (instead of SDK location)\n";

/// Returns the static usage body of `adb --help` matching `platform-tools/adb` v37.0.3-16887417.
pub fn help_body() -> String {
    let prefix = concat!(
        "global options:\n",
        " -a                       listen on all network interfaces, not just localhost\n",
        " -d                       use USB device (error if multiple devices connected)\n",
        " -e                       use TCP/IP device (error if multiple TCP/IP devices available)\n",
        " -s SERIAL                use device with given serial (overrides $ANDROID_SERIAL)\n",
        " -t ID                    use device with given transport id\n",
        " -H                       name of adb server host [default=localhost]\n",
        " -P                       port of adb server [default=5037]\n",
        " -L SOCKET                listen on given socket for adb server [default=tcp:localhost:5037]\n",
        " --one-device SERIAL|USB  only allowed with 'start-server' or 'server nodaemon', server will only connect to one USB device, specified by a serial number or USB device address.\n",
        " --exit-on-write-error    exit if stdout is closed\n",
        "\n",
        "general commands:\n",
        " devices [-l]             list connected devices (-l for long output)\n",
        " help                     show this help message\n",
        " version                  show version num\n",
        "\n",
        "networking:\n",
        " connect HOST[:PORT]      connect to a device via TCP/IP [default port=5555]\n",
        " disconnect [HOST[:PORT]]\n",
        "     disconnect from given TCP/IP device [default port=5555], or all\n",
        " pair [--qr | HOST[:PORT] [PAIRING_CODE]]\n",
        "     pair with a device for secure TCP/IP communication\n",
        " forward --list           list all forward socket connections\n",
        " forward [--no-rebind] LOCAL REMOTE\n",
        "     forward socket connection using:\n",
        "       tcp:<port> (<local> may be \"tcp:0\" to pick any open port)\n",
        "       localabstract:<unix domain socket name>\n",
        "       localreserved:<unix domain socket name>\n",
        "       localfilesystem:<unix domain socket name>\n",
        "       dev:<character device name>\n",
        "       dev-raw:<character device name> (open device in raw mode)\n",
        "       jdwp:<process pid> (remote only)\n",
        "       vsock:<CID>:<port> (remote only)\n",
        "       acceptfd:<fd> (listen only)\n",
        " forward --remove LOCAL   remove specific forward socket connection\n",
        " forward --remove-all     remove all forward socket connections\n",
        " reverse --list           list all reverse socket connections from device\n",
        " reverse [--no-rebind] REMOTE LOCAL\n",
        "     reverse socket connection using:\n",
        "       tcp:<port> (<remote> may be \"tcp:0\" to pick any open port)\n",
        "       localabstract:<unix domain socket name>\n",
        "       localreserved:<unix domain socket name>\n",
        "       localfilesystem:<unix domain socket name>\n",
        " reverse --remove REMOTE  remove specific reverse socket connection\n",
        " reverse --remove-all     remove all reverse socket connections from device\n",
        " mdns check               check if mdns discovery is available\n",
        " mdns services            list all discovered services\n",
        "\n",
        "file transfer:\n",
        " push [--sync] [-z ALGORITHM] [-Z] LOCAL... REMOTE\n",
        "     copy local files/directories to device\n",
        "     -n: dry run: push files to device without storing to the filesystem\n",
        "     -q: suppress progress messages\n",
        "     -Z: disable compression\n",
        "     -z: enable compression with a specified algorithm (any/none/brotli/lz4/zstd)\n",
        "     --sync: only push files that have different timestamps on the host than the device\n",
        " pull [-a] [-z ALGORITHM] [-Z] REMOTE... LOCAL\n",
        "     copy files/dirs from device\n",
        "     -a: preserve file timestamp and mode\n",
        "     -q: suppress progress messages\n",
        "     -Z: disable compression\n",
        "     -z: enable compression with a specified algorithm (any/none/brotli/lz4/zstd)\n",
        " sync [-l] [-z ALGORITHM] [-Z] [all|data|odm|odm_dlkm|oem|product|system|system_dlkm|system_ext|vendor|vendor_dlkm]\n",
        "     sync a local build from $ANDROID_PRODUCT_OUT to the device (default all)\n",
        "     -l: list files that would be copied, but don't copy them\n",
        "     -n: dry run: push files to device without storing to the filesystem\n",
        "     -q: suppress progress messages\n",
        "     -Z: disable compression\n",
        "     -z: enable compression with a specified algorithm (any/none/brotli/lz4/zstd)\n",
        "\n",
        "shell:\n",
        " shell [-e ESCAPE] [-n] [-Tt] [-x] [COMMAND...]\n",
        "     run remote shell command (interactive shell if no command given)\n",
        "     -e: choose escape character, or \"none\"; default '~'\n",
        "     -n: don't read from stdin\n",
        "     -T: disable pty allocation\n",
        "     -t: allocate a pty if on a tty (-tt: force pty allocation)\n",
        "     -x: disable remote exit codes and stdout/stderr separation\n",
        " emu COMMAND              run emulator console command\n",
        "\n",
        "app installation (see also `adb shell cmd package help`):\n",
        " install [-lrtsdg] [--instant] PACKAGE\n",
        "     push a single package to the device and install it\n",
        " install-multiple [-lrtsdpg] [--instant] PACKAGE...\n",
        "     push multiple APKs to the device for a single package and install them\n",
        " install-multi-package [-lrtsdpg] [--instant] PACKAGE...\n",
        "     push one or more packages to the device and install them atomically\n",
        "     -r: replace existing application\n",
        "     -t: allow test packages\n",
        "     -d: allow version code downgrade (debuggable packages only)\n",
        "     -p: partial application install (install-multiple only)\n",
        "     -g: grant all runtime permissions\n",
        "     --abi ABI: override platform's default ABI\n",
        "     --instant: cause the app to be installed as an ephemeral install app\n",
        "     --no-streaming: always push APK to device and invoke Package Manager as separate steps\n",
        "     --streaming: force streaming APK directly into Package Manager\n",
        "     --fastdeploy: [deprecated] use --deprecated-fastdeploy or `android install`\n",
        "     --deprecated-fastdeploy: use fast deploy (temporary; will be removed)\n",
        "     --no-fastdeploy: prevent use of fast deploy\n",
        "     --force-agent: force update of deployment agent when using fast deploy\n",
        "     --date-check-agent: update deployment agent when local version is newer and using fast deploy\n",
        "     --version-check-agent: update deployment agent when local version has different version code and using fast deploy\n",
    );
    let suffix = concat!(
        "     (See also `adb shell pm help` for more options.)\n",
        " uninstall [-k] PACKAGE\n",
        "     remove this app package from the device\n",
        "     '-k': keep the data and cache directories\n",
        "\n",
        "debugging:\n",
        " bugreport [PATH]\n",
        "     write bugreport to given PATH [default=bugreport.zip];\n",
        "     if PATH is a directory, the bug report is saved in that directory.\n",
        "     devices that don't support zipped bug reports output to stdout.\n",
        " jdwp                     list pids of processes hosting a JDWP transport\n",
        " logcat                   show device log (logcat --help for more)\n",
        "\n",
        "security:\n",
        " disable-verity           disable dm-verity checking on userdebug builds\n",
        " enable-verity            re-enable dm-verity checking on userdebug builds\n",
        " keygen FILE\n",
        "     generate adb public/private key; private key stored in FILE,\n",
        "\n",
        "scripting:\n",
        " wait-for[-TRANSPORT]-STATE...\n",
        "     wait for device to be in a given state\n",
        "     STATE: device, recovery, rescue, sideload, bootloader, or disconnect\n",
        "     TRANSPORT: usb, local, or any [default=any]\n",
        " get-state                print offline | bootloader | device\n",
        " get-serialno             print <serial-number>\n",
        " get-devpath              print <device-path>\n",
        " remount [-R]\n",
        "      remount partitions read-write. if a reboot is required, -R will\n",
        "      will automatically reboot the device.\n",
        " reboot [bootloader|recovery|sideload|sideload-auto-reboot]\n",
        "     reboot the device; defaults to booting system image but\n",
        "     supports bootloader and recovery too. sideload reboots\n",
        "     into recovery and automatically starts sideload mode,\n",
        "     sideload-auto-reboot is the same but reboots after sideloading.\n",
        " sideload OTAPACKAGE      sideload the given full OTA package\n",
        " root                     restart adbd with root permissions\n",
        " unroot                   restart adbd without root permissions\n",
        " usb                      restart adbd listening on USB\n",
        " tcpip PORT               restart adbd listening on TCP on PORT\n",
        "\n",
        "internal debugging:\n",
        " start-server             ensure that there is a server running\n",
        " kill-server              kill the server if it is running\n",
        " reconnect                kick connection from host side to force reconnect\n",
        " reconnect device         kick connection from device side to force reconnect\n",
        " reconnect offline        reset offline/unauthorized devices to force reconnect\n",
        "\n",
        "usb:\n",
        " attach                   attach a detached USB device\n",
        " detach                   detach from a USB device to allow use by other processes\n",
        "environment variables:\n",
        " $ADB_TRACE\n",
        "     comma/space separated list of debug info to log:\n",
        "     all,adb,sockets,packets,rwx,usb,sync,sysdeps,transport,jdwp,services,auth,fdevent,shell,incremental\n",
        " $ADB_VENDOR_KEYS         colon-separated list of keys (files or directories)\n",
        " $ANDROID_SERIAL          serial number to connect to (see -s)\n",
        " $ANDROID_LOG_TAGS        tags to be used by logcat (see logcat --help)\n",
        " $ADB_LOCAL_TRANSPORT_MAX_PORT max emulator scan port (default 5585, 16 emus)\n",
        " $ADB_MDNS_AUTO_CONNECT   comma-separated list of mdns services to allow auto-connect (default adb-tls-connect)\n",
        "\n",
        "Online documentation: https://android.googlesource.com/platform/packages/modules/adb/+/refs/heads/main/docs/user/adb.1.md\n",
        "\n",
    );
    format!("{prefix}{LOCAL_AGENT_HELP_LINE}{suffix}")
}

/// Returns the full `adb --help` output (version header + blank line + help body).
pub fn help_output() -> String {
    format!("{}\n{}", version_output(), help_body())
}

/// Parses command-line arguments (`args[1..]`) for `adb`.
pub fn parse_cli_args(args: &[String]) -> Result<AdbCliConfig, AdbError> {
    let mut cfg = AdbCliConfig::default();
    if args.is_empty() {
        cfg.command = AdbCommand::Help { exit_code: 1 };
        return Ok(cfg);
    }

    let mut i = 0usize;
    while i < args.len() {
        let arg = &args[i];
        if !arg.starts_with('-') {
            break;
        }
        match arg.as_str() {
            "--version" => {
                cfg.command = AdbCommand::Version;
                return Ok(cfg);
            }
            "--help" | "-h" => {
                cfg.command = AdbCommand::Help { exit_code: 0 };
                return Ok(cfg);
            }
            "-a" => {
                cfg.listen_all = true;
                i += 1;
            }
            "-d" => {
                cfg.target.usb_only = true;
                i += 1;
            }
            "-e" => {
                cfg.target.tcp_only = true;
                i += 1;
            }
            "-s" => {
                let s = args
                    .get(i + 1)
                    .ok_or_else(|| AdbError::InvalidArgument("-s requires an argument".into()))?;
                cfg.target.serial = Some(s.clone());
                i += 2;
            }
            "-t" => {
                let t = args
                    .get(i + 1)
                    .ok_or_else(|| AdbError::InvalidArgument("-t requires an argument".into()))?;
                cfg.target.transport_id = Some(
                    t.parse()
                        .map_err(|_| AdbError::InvalidArgument("invalid transport id".into()))?,
                );
                i += 2;
            }
            "-H" => {
                let h = args
                    .get(i + 1)
                    .ok_or_else(|| AdbError::InvalidArgument("-H requires an argument".into()))?;
                cfg.server_host = if h == "localhost" {
                    "127.0.0.1".to_string()
                } else {
                    h.clone()
                };
                i += 2;
            }
            "-P" => {
                let p = args
                    .get(i + 1)
                    .ok_or_else(|| AdbError::InvalidArgument("-P requires an argument".into()))?;
                cfg.server_port = p
                    .parse()
                    .map_err(|_| AdbError::InvalidArgument("invalid server port".into()))?;
                i += 2;
            }
            "-L" => {
                let l = args
                    .get(i + 1)
                    .ok_or_else(|| AdbError::InvalidArgument("-L requires an argument".into()))?;
                if let Some(rest) = l.strip_prefix("tcp:") {
                    if let Some((h, p)) = rest.rsplit_once(':') {
                        cfg.server_host = if h == "localhost" || h.is_empty() {
                            "127.0.0.1".to_string()
                        } else {
                            h.to_string()
                        };
                        if let Ok(port) = p.parse::<u16>() {
                            cfg.server_port = port;
                        }
                    } else if let Ok(port) = rest.parse::<u16>() {
                        cfg.server_port = port;
                    }
                }
                i += 2;
            }
            "--one-device" => {
                i += 2;
            }
            "--exit-on-write-error" => {
                cfg.exit_on_write_error = true;
                i += 1;
            }
            other => {
                return Err(AdbError::InvalidArgument(format!(
                    "unknown global option '{other}'"
                )));
            }
        }
    }

    let rem = &args[i..];
    if rem.is_empty() {
        cfg.command = AdbCommand::Help { exit_code: 1 };
        return Ok(cfg);
    }

    let cmd = rem[0].as_str();
    let sub = &rem[1..];

    cfg.command = match cmd {
        "version" => AdbCommand::Version,
        "help" => AdbCommand::Help { exit_code: 0 },
        "devices" => {
            let long = sub.iter().any(|s| s == "-l");
            AdbCommand::Devices { long }
        }
        "start-server" => AdbCommand::StartServer,
        "kill-server" => AdbCommand::KillServer,
        "nodaemon" | "fork-server" | "server" => AdbCommand::RunServerForeground,
        "connect" => {
            let host = sub
                .first()
                .ok_or_else(|| AdbError::InvalidArgument("empty address for connect".into()))?;
            AdbCommand::Connect(host.clone())
        }
        "disconnect" => AdbCommand::Disconnect(sub.first().cloned()),
        "pair" => {
            let addr = sub
                .first()
                .ok_or_else(|| AdbError::InvalidArgument("pair requires host[:port]".into()))?
                .clone();
            let code = sub.get(1).cloned();
            AdbCommand::Pair { addr, code }
        }
        "mdns" => match sub.first().map(String::as_str) {
            Some("check") => AdbCommand::MdnsCheck,
            Some("services") => AdbCommand::MdnsServices,
            _ => {
                return Err(AdbError::InvalidArgument(
                    "mdns requires 'check' or 'services'".into(),
                ));
            }
        },
        "forward" => parse_forward_sub(sub)?,
        "reverse" => parse_reverse_sub(sub)?,
        "push" => parse_push_sub(sub)?,
        "pull" => parse_pull_sub(sub)?,
        "sync" => parse_sync_sub(sub)?,
        "shell" => parse_shell_sub(sub),
        "emu" => AdbCommand::Emu(sub.to_vec()),
        "install" => parse_install_sub(sub)?,
        "install-multiple" => parse_install_multi_sub(sub, false)?,
        "install-multi-package" => parse_install_multi_sub(sub, true)?,
        "uninstall" => {
            let mut keep_data = false;
            let mut pkg = None;
            for tok in sub {
                if tok == "-k" {
                    keep_data = true;
                } else {
                    pkg = Some(tok.clone());
                }
            }
            let package = pkg
                .ok_or_else(|| AdbError::InvalidArgument("uninstall requires package".into()))?;
            AdbCommand::Uninstall { keep_data, package }
        }
        "bugreport" => AdbCommand::Bugreport(sub.first().map(PathBuf::from)),
        "jdwp" => AdbCommand::Jdwp,
        "logcat" => AdbCommand::Logcat(sub.to_vec()),
        "disable-verity" => AdbCommand::DisableVerity,
        "enable-verity" => AdbCommand::EnableVerity,
        "keygen" => {
            let file = sub
                .first()
                .ok_or_else(|| AdbError::InvalidArgument("keygen requires file path".into()))?;
            AdbCommand::Keygen(PathBuf::from(file))
        }
        s if s.starts_with("wait-for-") => {
            let rest = &s["wait-for-".len()..];
            let (transport, state) = if let Some((t, st)) = rest.split_once('-') {
                (t.to_string(), st.to_string())
            } else {
                ("any".to_string(), rest.to_string())
            };
            AdbCommand::WaitFor { transport, state }
        }
        "get-state" => AdbCommand::GetState,
        "get-serialno" => AdbCommand::GetSerialNo,
        "get-devpath" => AdbCommand::GetDevPath,
        "remount" => {
            let auto_reboot = sub.iter().any(|s| s == "-R");
            AdbCommand::Remount { auto_reboot }
        }
        "reboot" => AdbCommand::Reboot(sub.first().cloned()),
        "sideload" => {
            let pkg = sub
                .first()
                .ok_or_else(|| AdbError::InvalidArgument("sideload requires package".into()))?;
            AdbCommand::Sideload(PathBuf::from(pkg))
        }
        "root" => AdbCommand::Root,
        "unroot" => AdbCommand::Unroot,
        "usb" => AdbCommand::Usb,
        "tcpip" => {
            let port_str = sub
                .first()
                .ok_or_else(|| AdbError::InvalidArgument("tcpip requires port".into()))?;
            let port: u16 = port_str
                .parse()
                .map_err(|_| AdbError::InvalidArgument(format!("invalid port '{port_str}'")))?;
            AdbCommand::Tcpip(port)
        }
        "reconnect" => AdbCommand::Reconnect(sub.first().cloned()),
        "attach" => AdbCommand::Attach,
        "detach" => AdbCommand::Detach,
        "features" => AdbCommand::Features,
        "host-features" => AdbCommand::HostFeatures,
        other => {
            return Err(AdbError::InvalidArgument(format!(
                "unknown command '{other}'"
            )));
        }
    };

    Ok(cfg)
}

fn parse_forward_sub(sub: &[String]) -> Result<AdbCommand, AdbError> {
    if sub.is_empty() {
        return Err(AdbError::InvalidArgument(
            "forward requires arguments".into(),
        ));
    }
    match sub[0].as_str() {
        "--list" => Ok(AdbCommand::ForwardList),
        "--remove-all" => Ok(AdbCommand::ForwardRemoveAll),
        "--remove" => {
            let local = sub
                .get(1)
                .ok_or_else(|| AdbError::InvalidArgument("--remove requires local spec".into()))?;
            Ok(AdbCommand::ForwardRemove(local.clone()))
        }
        "--no-rebind" => {
            if sub.len() < 3 {
                return Err(AdbError::InvalidArgument(
                    "forward requires LOCAL and REMOTE".into(),
                ));
            }
            Ok(AdbCommand::Forward {
                no_rebind: true,
                local: sub[1].clone(),
                remote: sub[2].clone(),
            })
        }
        _ => {
            if sub.len() < 2 {
                return Err(AdbError::InvalidArgument(
                    "forward requires LOCAL and REMOTE".into(),
                ));
            }
            Ok(AdbCommand::Forward {
                no_rebind: false,
                local: sub[0].clone(),
                remote: sub[1].clone(),
            })
        }
    }
}

fn parse_reverse_sub(sub: &[String]) -> Result<AdbCommand, AdbError> {
    if sub.is_empty() {
        return Err(AdbError::InvalidArgument(
            "reverse requires arguments".into(),
        ));
    }
    match sub[0].as_str() {
        "--list" => Ok(AdbCommand::ReverseList),
        "--remove-all" => Ok(AdbCommand::ReverseRemoveAll),
        "--remove" => {
            let remote = sub
                .get(1)
                .ok_or_else(|| AdbError::InvalidArgument("--remove requires remote spec".into()))?;
            Ok(AdbCommand::ReverseRemove(remote.clone()))
        }
        "--no-rebind" => {
            if sub.len() < 3 {
                return Err(AdbError::InvalidArgument(
                    "reverse requires REMOTE and LOCAL".into(),
                ));
            }
            Ok(AdbCommand::Reverse {
                no_rebind: true,
                remote: sub[1].clone(),
                local: sub[2].clone(),
            })
        }
        _ => {
            if sub.len() < 2 {
                return Err(AdbError::InvalidArgument(
                    "reverse requires REMOTE and LOCAL".into(),
                ));
            }
            Ok(AdbCommand::Reverse {
                no_rebind: false,
                remote: sub[0].clone(),
                local: sub[1].clone(),
            })
        }
    }
}

fn parse_push_sub(sub: &[String]) -> Result<AdbCommand, AdbError> {
    let mut dry_run = false;
    let mut quiet = false;
    let mut compression = None;
    let mut paths = Vec::new();
    let mut i = 0;
    while i < sub.len() {
        match sub[i].as_str() {
            "--sync" => {}
            "-Z" => compression = Some("none".to_string()),
            "-n" => dry_run = true,
            "-q" => quiet = true,
            "-z" => {
                if let Some(algo) = sub.get(i + 1) {
                    compression = Some(algo.clone());
                }
                i += 1;
            }
            p => paths.push(p.to_string()),
        }
        i += 1;
    }
    if paths.len() < 2 {
        return Err(AdbError::InvalidArgument(
            "push requires LOCAL... and REMOTE".into(),
        ));
    }
    let remote = paths.pop().unwrap();
    let sources = paths.into_iter().map(PathBuf::from).collect();
    Ok(AdbCommand::Push {
        sources,
        remote,
        dry_run,
        quiet,
        compression,
    })
}

fn parse_pull_sub(sub: &[String]) -> Result<AdbCommand, AdbError> {
    let mut preserve_attrs = false;
    let mut quiet = false;
    let mut compression = None;
    let mut paths = Vec::new();
    let mut i = 0;
    while i < sub.len() {
        match sub[i].as_str() {
            "-a" => preserve_attrs = true,
            "-q" => quiet = true,
            "-Z" => compression = Some("none".to_string()),
            "-z" => {
                if let Some(algo) = sub.get(i + 1) {
                    compression = Some(algo.clone());
                }
                i += 1;
            }
            p => paths.push(p.to_string()),
        }
        i += 1;
    }
    if paths.is_empty() {
        return Err(AdbError::InvalidArgument(
            "pull requires REMOTE... [LOCAL]".into(),
        ));
    }
    let (remotes, local) = if paths.len() == 1 {
        let r = paths[0].clone();
        let loc = PathBuf::from(r.rsplit('/').next().unwrap_or("pulled_file"));
        (vec![r], loc)
    } else {
        let mut p = paths;
        let loc = PathBuf::from(p.pop().unwrap());
        (p, loc)
    };
    Ok(AdbCommand::Pull {
        remotes,
        local,
        preserve_attrs,
        quiet,
        compression,
    })
}

fn parse_sync_sub(sub: &[String]) -> Result<AdbCommand, AdbError> {
    let mut list_only = false;
    let mut dry_run = false;
    let mut quiet = false;
    let mut partition = None;
    let mut i = 0;
    while i < sub.len() {
        match sub[i].as_str() {
            "-l" => list_only = true,
            "-n" => dry_run = true,
            "-q" => quiet = true,
            "-Z" => {}
            "-z" => i += 1,
            p => partition = Some(p.to_string()),
        }
        i += 1;
    }
    Ok(AdbCommand::Sync {
        partition,
        list_only,
        dry_run,
        quiet,
    })
}

fn parse_shell_sub(sub: &[String]) -> AdbCommand {
    let mut raw_mode = false;
    let mut i = 0;
    while i < sub.len() {
        let s = sub[i].as_str();
        if !s.starts_with('-') {
            break;
        }
        match s {
            "-x" => raw_mode = true,
            "-n" | "-T" | "-t" | "-tt" => {}
            "-e" => i += 1,
            _ => break,
        }
        i += 1;
    }
    AdbCommand::Shell {
        raw_mode,
        command: sub[i..].to_vec(),
    }
}

fn parse_install_sub(sub: &[String]) -> Result<AdbCommand, AdbError> {
    let mut flags = Vec::new();
    let mut apk = None;
    let mut i = 0;
    while i < sub.len() {
        let s = &sub[i];
        if s == "--abi" {
            flags.push(s.clone());
            if let Some(v) = sub.get(i + 1) {
                flags.push(v.clone());
                i += 1;
            }
        } else if s.starts_with('-') {
            flags.push(s.clone());
        } else {
            apk = Some(PathBuf::from(s));
        }
        i += 1;
    }
    let apk = apk.ok_or_else(|| AdbError::InvalidArgument("install requires APK file".into()))?;
    Ok(AdbCommand::Install { flags, apk })
}

fn parse_install_multi_sub(sub: &[String], multi_pkg: bool) -> Result<AdbCommand, AdbError> {
    let mut flags = Vec::new();
    let mut apks = Vec::new();
    for s in sub {
        if s.starts_with('-') {
            flags.push(s.clone());
        } else {
            apks.push(PathBuf::from(s));
        }
    }
    if apks.is_empty() {
        return Err(AdbError::InvalidArgument(
            "install requires at least one APK".into(),
        ));
    }
    if multi_pkg {
        Ok(AdbCommand::InstallMultiPackage { flags, apks })
    } else {
        Ok(AdbCommand::InstallMultiple { flags, apks })
    }
}
