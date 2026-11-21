//! ADB CLI client implementation talking to the ADB Host Server on TCP 5037 (MIT).

use std::fs;
use std::io::{self, Read, Write};
use std::net::{SocketAddr, TcpStream, ToSocketAddrs};
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::thread;
use std::time::{Duration, Instant, UNIX_EPOCH};

use crate::auth::generate_adb_keypair;
use crate::config::{
    help_output, is_embedded_mode, parse_cli_args, version_output, AdbCliConfig, AdbCommand,
    DeviceTarget,
};
use crate::protocol::*;
use crate::server::{ensure_in_process_server, ensure_in_process_server_async, AdbHostServer};

/// Streaming event emitted during an embedded/captured `adb` execution.
#[derive(Debug, Clone)]
pub enum AdbEvent {
    Output {
        text: String,
        is_stderr: bool,
    },
    Progress {
        stage: String,
        current: u64,
        total: u64,
        percent: i32,
        detail: String,
    },
}

/// Captured execution result returned by [`run_cli_captured`].
#[derive(Debug, Clone)]
pub struct AdbRunResult {
    pub exit_code: i32,
    pub stdout: String,
    pub stderr: String,
    pub output: String,
}

pub type AdbEventCallback = Arc<dyn Fn(AdbEvent) + Send + Sync>;

enum IoSink {
    Stdio,
    Captured {
        stdout: String,
        stderr: String,
        combined: String,
        on_event: Option<AdbEventCallback>,
    },
}

struct AdbIo<'a> {
    sink: IoSink,
    cwd: Option<&'a Path>,
    cancel: Option<&'a AtomicBool>,
    deadline: Option<Instant>,
}

impl<'a> AdbIo<'a> {
    fn stdio() -> Self {
        Self {
            sink: IoSink::Stdio,
            cwd: None,
            cancel: None,
            deadline: None,
        }
    }

    fn captured(
        cwd: Option<&'a Path>,
        timeout_ms: Option<u64>,
        cancel: Option<&'a AtomicBool>,
        on_event: Option<AdbEventCallback>,
    ) -> Self {
        let deadline = timeout_ms
            .filter(|&ms| ms > 0)
            .map(|ms| Instant::now() + Duration::from_millis(ms));
        Self {
            sink: IoSink::Captured {
                stdout: String::new(),
                stderr: String::new(),
                combined: String::new(),
                on_event,
            },
            cwd,
            cancel,
            deadline,
        }
    }

    fn is_captured(&self) -> bool {
        matches!(self.sink, IoSink::Captured { .. })
    }

    fn resolve_path(&self, p: &Path) -> PathBuf {
        if p.is_relative() {
            if let Some(base) = self.cwd {
                return base.join(p);
            }
        }
        p.to_path_buf()
    }

    fn check_cancel_or_timeout(&self) -> Result<(), AdbError> {
        if let Some(flag) = self.cancel {
            if flag.load(Ordering::Relaxed) {
                return Err(AdbError::Cancelled);
            }
        }
        if let Some(dl) = self.deadline {
            if Instant::now() >= dl {
                return Err(AdbError::TimedOut);
            }
        }
        Ok(())
    }

    fn write_out_bytes(&mut self, bytes: &[u8]) -> Result<(), AdbError> {
        match &mut self.sink {
            IoSink::Stdio => {
                let mut out = io::stdout();
                out.write_all(bytes)?;
                out.flush()?;
            }
            IoSink::Captured {
                stdout,
                combined,
                on_event,
                ..
            } => {
                let text = String::from_utf8_lossy(bytes).into_owned();
                if !text.is_empty() {
                    stdout.push_str(&text);
                    combined.push_str(&text);
                    if let Some(cb) = on_event {
                        cb(AdbEvent::Output {
                            text,
                            is_stderr: false,
                        });
                    }
                }
            }
        }
        Ok(())
    }

    fn write_out(&mut self, s: &str) -> Result<(), AdbError> {
        self.write_out_bytes(s.as_bytes())
    }

    fn write_out_line(&mut self, s: &str) -> Result<(), AdbError> {
        self.write_out(&format!("{s}\n"))
    }

    fn write_err_bytes(&mut self, bytes: &[u8]) {
        match &mut self.sink {
            IoSink::Stdio => {
                let mut err = io::stderr();
                let _ = err.write_all(bytes);
                let _ = err.flush();
            }
            IoSink::Captured {
                stderr,
                combined,
                on_event,
                ..
            } => {
                let text = String::from_utf8_lossy(bytes).into_owned();
                if !text.is_empty() {
                    stderr.push_str(&text);
                    combined.push_str(&text);
                    if let Some(cb) = on_event {
                        cb(AdbEvent::Output {
                            text,
                            is_stderr: true,
                        });
                    }
                }
            }
        }
    }

    fn write_err_line(&mut self, s: &str) {
        self.write_err_bytes(format!("{s}\n").as_bytes());
    }

    fn emit_progress(&self, stage: &str, current: u64, total: u64, detail: &str) {
        if let IoSink::Captured {
            on_event: Some(cb), ..
        } = &self.sink
        {
            let percent = if total > 0 {
                ((current.saturating_mul(100)) / total).min(100) as i32
            } else {
                -1
            };
            cb(AdbEvent::Progress {
                stage: stage.to_string(),
                current,
                total,
                percent,
                detail: detail.to_string(),
            });
        }
    }

    fn into_result(self, exit_code: i32) -> AdbRunResult {
        match self.sink {
            IoSink::Stdio => AdbRunResult {
                exit_code,
                stdout: String::new(),
                stderr: String::new(),
                output: String::new(),
            },
            IoSink::Captured {
                stdout,
                stderr,
                combined,
                ..
            } => AdbRunResult {
                exit_code,
                stdout,
                stderr,
                output: combined,
            },
        }
    }
}

fn configure_stream_timeout(stream: &TcpStream, io_ctx: &AdbIo<'_>) {
    if io_ctx.cancel.is_some() || io_ctx.deadline.is_some() {
        let _ = stream.set_read_timeout(Some(Duration::from_millis(100)));
    }
}

fn read_some_cancellable(
    stream: &mut TcpStream,
    buf: &mut [u8],
    io_ctx: &AdbIo<'_>,
) -> Result<usize, AdbError> {
    loop {
        io_ctx.check_cancel_or_timeout()?;
        match stream.read(buf) {
            Ok(n) => return Ok(n),
            Err(e)
                if matches!(
                    e.kind(),
                    io::ErrorKind::WouldBlock | io::ErrorKind::TimedOut | io::ErrorKind::Interrupted
                ) =>
            {
                io_ctx.check_cancel_or_timeout()?;
                continue;
            }
            Err(e) => return Err(AdbError::Io(e)),
        }
    }
}

fn read_exact_cancellable(
    stream: &mut TcpStream,
    mut buf: &mut [u8],
    io_ctx: &AdbIo<'_>,
) -> Result<(), AdbError> {
    while !buf.is_empty() {
        let n = read_some_cancellable(stream, buf, io_ctx)?;
        if n == 0 {
            return Err(AdbError::Io(io::Error::new(
                io::ErrorKind::UnexpectedEof,
                "early eof",
            )));
        }
        buf = &mut buf[n..];
    }
    Ok(())
}

fn read_shell_v2_packet_cancellable(
    stream: &mut TcpStream,
    io_ctx: &AdbIo<'_>,
) -> Result<Option<(u8, Vec<u8>)>, AdbError> {
    let mut hdr = [0u8; 5];
    let mut read = 0usize;
    while read < 5 {
        io_ctx.check_cancel_or_timeout()?;
        match stream.read(&mut hdr[read..]) {
            Ok(0) if read == 0 => return Ok(None),
            Ok(0) => {
                return Err(AdbError::Protocol(
                    "unexpected EOF inside shell_v2 frame header".into(),
                ));
            }
            Ok(n) => read += n,
            Err(e)
                if matches!(
                    e.kind(),
                    io::ErrorKind::WouldBlock | io::ErrorKind::TimedOut | io::ErrorKind::Interrupted
                ) =>
            {
                continue;
            }
            Err(e) => return Err(AdbError::Io(e)),
        }
    }
    let id = hdr[0];
    let len = u32::from_le_bytes(hdr[1..5].try_into().unwrap()) as usize;
    let mut payload = vec![0u8; len];
    if len > 0 {
        read_exact_cancellable(stream, &mut payload, io_ctx)?;
    }
    Ok(Some((id, payload)))
}

fn resolve_server_addr(cfg: &AdbCliConfig) -> Result<SocketAddr, AdbError> {
    let spec = format!("{}:{}", cfg.server_host, cfg.server_port);
    spec.to_socket_addrs()?
        .next()
        .ok_or_else(|| AdbError::InvalidArgument(format!("cannot resolve '{spec}'")))
}

/// Connects to the ADB host server, starting an in-process or background server if not yet running.
pub fn connect_to_server(cfg: &AdbCliConfig, auto_start: bool) -> Result<TcpStream, AdbError> {
    let mut io_ctx = AdbIo::stdio();
    connect_to_server_io(cfg, auto_start, &mut io_ctx)
}

fn connect_to_server_io(
    cfg: &AdbCliConfig,
    auto_start: bool,
    io_ctx: &mut AdbIo<'_>,
) -> Result<TcpStream, AdbError> {
    io_ctx.check_cancel_or_timeout()?;
    let addr = resolve_server_addr(cfg)?;
    if let Ok(s) = TcpStream::connect_timeout(&addr, Duration::from_millis(500)) {
        let _ = s.set_nodelay(true);
        configure_stream_timeout(&s, io_ctx);
        return Ok(s);
    }
    if !auto_start {
        return Err(AdbError::Io(io::Error::new(
            io::ErrorKind::ConnectionRefused,
            "cannot connect to daemon",
        )));
    }

    spawn_background_server_io(cfg, io_ctx)?;

    for _ in 0..40 {
        io_ctx.check_cancel_or_timeout()?;
        if let Ok(s) = TcpStream::connect_timeout(&addr, Duration::from_millis(300)) {
            let _ = s.set_nodelay(true);
            configure_stream_timeout(&s, io_ctx);
            return Ok(s);
        }
        thread::sleep(Duration::from_millis(50));
    }
    Err(AdbError::Io(io::Error::new(
        io::ErrorKind::TimedOut,
        "failed to start daemon",
    )))
}

async fn connect_to_server_async_io(
    cfg: &AdbCliConfig,
    auto_start: bool,
    io_ctx: &mut AdbIo<'_>,
) -> Result<TcpStream, AdbError> {
    io_ctx.check_cancel_or_timeout()?;
    let addr = resolve_server_addr(cfg)?;
    if let Ok(s) = TcpStream::connect_timeout(&addr, Duration::from_millis(300)) {
        let _ = s.set_nodelay(true);
        configure_stream_timeout(&s, io_ctx);
        return Ok(s);
    }
    if !auto_start {
        return Err(AdbError::Io(io::Error::new(
            io::ErrorKind::ConnectionRefused,
            "cannot connect to daemon",
        )));
    }

    if is_embedded_mode() {
        io_ctx.write_err_line(&format!(
            "* daemon not running; starting now at tcp:{}",
            cfg.server_port
        ));
        let bind_ip = if cfg.listen_all { "0.0.0.0" } else { &cfg.server_host };
        let bind_addr: SocketAddr = format!("{bind_ip}:{}", cfg.server_port)
            .parse()
            .unwrap_or(addr);
        let _ = ensure_in_process_server_async(bind_addr).await?;
        io_ctx.write_err_line("* daemon started successfully");
    } else {
        spawn_background_server_subprocess(cfg, io_ctx)?;
    }

    for _ in 0..40 {
        io_ctx.check_cancel_or_timeout()?;
        if let Ok(s) = TcpStream::connect_timeout(&addr, Duration::from_millis(300)) {
            let _ = s.set_nodelay(true);
            configure_stream_timeout(&s, io_ctx);
            return Ok(s);
        }
        tokio::time::sleep(Duration::from_millis(50)).await;
    }
    Err(AdbError::Io(io::Error::new(
        io::ErrorKind::TimedOut,
        "failed to start daemon",
    )))
}

#[cfg(windows)]
fn disable_std_handle_inheritance() {
    const STD_INPUT_HANDLE: u32 = -10i32 as u32;
    const STD_OUTPUT_HANDLE: u32 = -11i32 as u32;
    const STD_ERROR_HANDLE: u32 = -12i32 as u32;
    const HANDLE_FLAG_INHERIT: u32 = 0x0000_0001;

    extern "system" {
        fn GetStdHandle(n_std_handle: u32) -> *mut std::ffi::c_void;
        fn SetHandleInformation(
            h_object: *mut std::ffi::c_void,
            dw_mask: u32,
            dw_flags: u32,
        ) -> i32;
    }

    unsafe {
        for std_id in [STD_INPUT_HANDLE, STD_OUTPUT_HANDLE, STD_ERROR_HANDLE] {
            let h = GetStdHandle(std_id);
            if !h.is_null() && h != (-1isize as *mut std::ffi::c_void) {
                let _ = SetHandleInformation(h, HANDLE_FLAG_INHERIT, 0);
            }
        }
    }
}

fn spawn_background_server_io(cfg: &AdbCliConfig, io_ctx: &mut AdbIo<'_>) -> Result<(), AdbError> {
    if is_embedded_mode() {
        io_ctx.write_err_line(&format!(
            "* daemon not running; starting now at tcp:{}",
            cfg.server_port
        ));
        let bind_ip = if cfg.listen_all { "0.0.0.0" } else { &cfg.server_host };
        let addr: SocketAddr = format!("{bind_ip}:{}", cfg.server_port)
            .parse()
            .unwrap_or_else(|_| SocketAddr::from(([127, 0, 0, 1], cfg.server_port)));
        let _ = ensure_in_process_server(addr)?;
        io_ctx.write_err_line("* daemon started successfully");
        return Ok(());
    }
    spawn_background_server_subprocess(cfg, io_ctx)
}

fn spawn_background_server_subprocess(
    cfg: &AdbCliConfig,
    io_ctx: &mut AdbIo<'_>,
) -> Result<(), AdbError> {
    io_ctx.write_err_line(&format!(
        "* daemon not running; starting now at tcp:{}",
        cfg.server_port
    ));
    #[cfg(windows)]
    disable_std_handle_inheritance();

    let exe = std::env::current_exe()?;
    let mut cmd = Command::new(exe);
    if cfg.listen_all {
        cmd.arg("-a");
    }
    cmd.arg("-P")
        .arg(cfg.server_port.to_string())
        .arg("nodaemon")
        .arg("server")
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null());
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        const CREATE_NO_WINDOW: u32 = 0x0800_0000;
        cmd.creation_flags(CREATE_NO_WINDOW);
    }
    cmd.spawn()?;
    io_ctx.write_err_line("* daemon started successfully");
    Ok(())
}

/// Sends a one-shot smart request (`host:...`) and returns its length-prefixed string response.
pub fn query_server_string(
    cfg: &AdbCliConfig,
    request: &str,
    auto_start: bool,
) -> Result<String, AdbError> {
    let mut io_ctx = AdbIo::stdio();
    let mut stream = connect_to_server_io(cfg, auto_start, &mut io_ctx)?;
    send_smart_command_sync(&mut stream, request)?;
    read_length_prefixed_string_sync(&mut stream)
}

async fn query_server_string_async_io(
    cfg: &AdbCliConfig,
    request: &str,
    auto_start: bool,
    io_ctx: &mut AdbIo<'_>,
) -> Result<String, AdbError> {
    let mut stream = connect_to_server_async_io(cfg, auto_start, io_ctx).await?;
    send_smart_command_sync(&mut stream, request)?;
    read_length_prefixed_string_sync(&mut stream)
}

/// Connects to the server, switches transport to the target device, and sends `service_cmd`.
pub fn open_device_service(
    cfg: &AdbCliConfig,
    target: &DeviceTarget,
    service_cmd: &str,
) -> Result<TcpStream, AdbError> {
    let mut io_ctx = AdbIo::stdio();
    let mut stream = connect_to_server_io(cfg, true, &mut io_ctx)?;
    let switch_cmd = target.transport_switch_cmd();
    send_smart_command_sync(&mut stream, &switch_cmd)?;
    send_smart_command_sync(&mut stream, service_cmd)?;
    Ok(stream)
}

async fn open_device_service_async_io(
    cfg: &AdbCliConfig,
    target: &DeviceTarget,
    service_cmd: &str,
    io_ctx: &mut AdbIo<'_>,
) -> Result<TcpStream, AdbError> {
    let mut stream = connect_to_server_async_io(cfg, true, io_ctx).await?;
    let switch_cmd = target.transport_switch_cmd();
    send_smart_command_sync(&mut stream, &switch_cmd)?;
    send_smart_command_sync(&mut stream, service_cmd)?;
    Ok(stream)
}

async fn run_device_stream_to_stdout_io(
    cfg: &AdbCliConfig,
    service_cmd: &str,
    io_ctx: &mut AdbIo<'_>,
) -> Result<i32, AdbError> {
    let mut stream = open_device_service_async_io(cfg, &cfg.target, service_cmd, io_ctx).await?;
    let mut buf = [0u8; 16384];
    loop {
        let n = read_some_cancellable(&mut stream, &mut buf, io_ctx)?;
        if n == 0 {
            break;
        }
        io_ctx.write_out_bytes(&buf[..n])?;
    }
    Ok(0)
}

async fn device_supports_feature_io(
    cfg: &AdbCliConfig,
    feature: &str,
    io_ctx: &mut AdbIo<'_>,
) -> bool {
    let req = cfg.target.scoped_host_cmd("features");
    if let Ok(feats) = query_server_string_async_io(cfg, &req, true, io_ctx).await {
        feats.split(',').any(|f| f.trim() == feature)
    } else {
        false
    }
}

async fn run_shell_command_io(
    cfg: &AdbCliConfig,
    raw_mode: bool,
    command: &[String],
    io_ctx: &mut AdbIo<'_>,
) -> Result<i32, AdbError> {
    let cmd_str = command.join(" ");
    if !raw_mode && device_supports_feature_io(cfg, "shell_v2", io_ctx).await {
        let service = format!("shell,v2:{cmd_str}");
        let mut stream = open_device_service_async_io(cfg, &cfg.target, &service, io_ctx).await?;
        if command.is_empty() && !io_ctx.is_captured() {
            if let Ok(mut write_stream) = stream.try_clone() {
                thread::spawn(move || {
                    let mut stdin = io::stdin();
                    let mut buf = [0u8; 4096];
                    while let Ok(n) = stdin.read(&mut buf) {
                        if n == 0 {
                            let close_pkt = encode_shell_v2_packet(SHELL_V2_CLOSE_STDIN, &[]);
                            let _ = write_stream.write_all(&close_pkt);
                            let _ = write_stream.flush();
                            break;
                        }
                        let pkt = encode_shell_v2_packet(SHELL_V2_STDIN, &buf[..n]);
                        if write_stream.write_all(&pkt).is_err() {
                            break;
                        }
                        let _ = write_stream.flush();
                    }
                });
            }
        }
        let mut exit_code = 0i32;

        while let Some((id, payload)) = read_shell_v2_packet_cancellable(&mut stream, io_ctx)? {
            match id {
                SHELL_V2_STDOUT => {
                    io_ctx.write_out_bytes(&payload)?;
                }
                SHELL_V2_STDERR => {
                    io_ctx.write_err_bytes(&payload);
                }
                SHELL_V2_EXIT => {
                    if let Some(&code) = payload.first() {
                        exit_code = i32::from(code);
                    }
                    break;
                }
                _ => {}
            }
        }
        return Ok(exit_code);
    }

    let service = format!("shell:{cmd_str}");
    run_device_stream_to_stdout_io(cfg, &service, io_ctx).await
}

/// Compression mode negotiated for `sync:` file transfers (`SEND`/`RECV` v1 vs `SND2`/`RCV2` v2).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SyncCompression {
    /// Classic `SEND` / `RECV` (`v1`, uncompressed).
    V1None,
    /// `SND2` / `RCV2` (`sendrecv_v2`) uncompressed (`SYNC_FLAG_NONE = 0`).
    V2None,
    /// `SND2` / `RCV2` (`sendrecv_v2_brotli`) via `brotli_rs` (`SYNC_FLAG_BROTLI = 1`).
    V2Brotli,
    /// `SND2` / `RCV2` (`sendrecv_v2_lz4`) via `lz4_flex::frame` (`SYNC_FLAG_LZ4 = 2`).
    V2Lz4,
    /// `SND2` / `RCV2` (`sendrecv_v2_zstd`) via `zstd` (`SYNC_FLAG_ZSTD = 4`).
    V2Zstd,
}

async fn resolve_sync_compression_io(
    cfg: &AdbCliConfig,
    requested: Option<&str>,
    io_ctx: &mut AdbIo<'_>,
) -> SyncCompression {
    if !device_supports_feature_io(cfg, "sendrecv_v2", io_ctx).await {
        return SyncCompression::V1None;
    }
    match requested {
        Some("none") => SyncCompression::V1None,
        Some("brotli") if device_supports_feature_io(cfg, "sendrecv_v2_brotli", io_ctx).await => {
            SyncCompression::V2Brotli
        }
        Some("lz4") if device_supports_feature_io(cfg, "sendrecv_v2_lz4", io_ctx).await => {
            SyncCompression::V2Lz4
        }
        Some("zstd") if device_supports_feature_io(cfg, "sendrecv_v2_zstd", io_ctx).await => {
            SyncCompression::V2Zstd
        }
        None | Some("any") => {
            if device_supports_feature_io(cfg, "sendrecv_v2_lz4", io_ctx).await {
                SyncCompression::V2Lz4
            } else if device_supports_feature_io(cfg, "sendrecv_v2_zstd", io_ctx).await {
                SyncCompression::V2Zstd
            } else if device_supports_feature_io(cfg, "sendrecv_v2_brotli", io_ctx).await {
                SyncCompression::V2Brotli
            } else {
                SyncCompression::V1None
            }
        }
        _ => SyncCompression::V1None,
    }
}

/// Queries remote file `(mode, size, mtime)` via `STAT` (16-byte v1). Returns `(0, 0, 0)` if path does not exist.
pub fn sync_stat(
    sync_stream: &mut TcpStream,
    remote_path: &str,
) -> Result<(u32, u32, u32), AdbError> {
    let mut hdr = [0u8; 8];
    hdr[0..4].copy_from_slice(SYNC_STAT);
    hdr[4..8].copy_from_slice(&(remote_path.len() as u32).to_le_bytes());
    sync_stream.write_all(&hdr)?;
    sync_stream.write_all(remote_path.as_bytes())?;
    sync_stream.flush()?;

    let mut resp = [0u8; 16];
    sync_stream.read_exact(&mut resp)?;
    if &resp[0..4] != SYNC_STAT {
        return Err(AdbError::Protocol("invalid STAT response".into()));
    }
    let mode = u32::from_le_bytes(resp[4..8].try_into().unwrap());
    let size = u32::from_le_bytes(resp[8..12].try_into().unwrap());
    let mtime = u32::from_le_bytes(resp[12..16].try_into().unwrap());
    Ok((mode, size, mtime))
}

/// Queries remote file 64-bit `(mode, size, mtime)` via `STA2` (72-byte `stat_v2`).
pub fn sync_stat_v2(
    sync_stream: &mut TcpStream,
    remote_path: &str,
) -> Result<(u32, u64, i64), AdbError> {
    let mut hdr = [0u8; 8];
    hdr[0..4].copy_from_slice(SYNC_STA2);
    hdr[4..8].copy_from_slice(&(remote_path.len() as u32).to_le_bytes());
    sync_stream.write_all(&hdr)?;
    sync_stream.write_all(remote_path.as_bytes())?;
    sync_stream.flush()?;

    let mut resp = [0u8; 72];
    sync_stream.read_exact(&mut resp)?;
    if &resp[0..4] != SYNC_STA2 {
        return Err(AdbError::Protocol("invalid STA2 response".into()));
    }
    let err_no = u32::from_le_bytes(resp[4..8].try_into().unwrap());
    if err_no != 0 {
        return Ok((0, 0, 0));
    }
    let mode = u32::from_le_bytes(resp[24..28].try_into().unwrap());
    let size = u64::from_le_bytes(resp[40..48].try_into().unwrap());
    let mtime = i64::from_le_bytes(resp[56..64].try_into().unwrap());
    Ok((mode, size, mtime))
}

/// Lists remote directory entries via `LIST` -> `DENT`* -> `DONE` (20-byte v1).
pub fn sync_list(
    sync_stream: &mut TcpStream,
    remote_dir: &str,
) -> Result<Vec<SyncDent>, AdbError> {
    let mut hdr = [0u8; 8];
    hdr[0..4].copy_from_slice(SYNC_LIST);
    hdr[4..8].copy_from_slice(&(remote_dir.len() as u32).to_le_bytes());
    sync_stream.write_all(&hdr)?;
    sync_stream.write_all(remote_dir.as_bytes())?;
    sync_stream.flush()?;

    let mut entries = Vec::new();
    loop {
        let mut dhdr = [0u8; 20];
        sync_stream.read_exact(&mut dhdr)?;
        let id = &dhdr[0..4];
        if id == SYNC_DONE {
            break;
        }
        if id != SYNC_DENT {
            return Err(AdbError::Protocol(format!(
                "unexpected sync LIST response '{:?}'",
                id
            )));
        }
        let mode = u32::from_le_bytes(dhdr[4..8].try_into().unwrap());
        let size = u64::from(u32::from_le_bytes(dhdr[8..12].try_into().unwrap()));
        let mtime = i64::from(u32::from_le_bytes(dhdr[12..16].try_into().unwrap()));
        let namelen = u32::from_le_bytes(dhdr[16..20].try_into().unwrap()) as usize;
        let mut name_buf = vec![0u8; namelen];
        if namelen > 0 {
            sync_stream.read_exact(&mut name_buf)?;
        }
        let name = String::from_utf8_lossy(&name_buf).into_owned();
        if name != "." && name != ".." {
            entries.push(SyncDent {
                mode,
                size,
                mtime,
                name,
            });
        }
    }
    Ok(entries)
}

/// Lists remote directory entries via `LST2` -> `DNT2`* -> `DONE` (76-byte `ls_v2`).
pub fn sync_list_v2(
    sync_stream: &mut TcpStream,
    remote_dir: &str,
) -> Result<Vec<SyncDent>, AdbError> {
    let mut hdr = [0u8; 8];
    hdr[0..4].copy_from_slice(SYNC_LST2);
    hdr[4..8].copy_from_slice(&(remote_dir.len() as u32).to_le_bytes());
    sync_stream.write_all(&hdr)?;
    sync_stream.write_all(remote_dir.as_bytes())?;
    sync_stream.flush()?;

    let mut entries = Vec::new();
    loop {
        let mut dhdr = [0u8; 76];
        sync_stream.read_exact(&mut dhdr)?;
        let id = &dhdr[0..4];
        if id == SYNC_DONE {
            break;
        }
        if id != SYNC_DNT2 {
            return Err(AdbError::Protocol(format!(
                "unexpected sync LST2 response '{:?}'",
                id
            )));
        }
        let mode = u32::from_le_bytes(dhdr[24..28].try_into().unwrap());
        let size = u64::from_le_bytes(dhdr[40..48].try_into().unwrap());
        let mtime = i64::from_le_bytes(dhdr[56..64].try_into().unwrap());
        let namelen = u32::from_le_bytes(dhdr[72..76].try_into().unwrap()) as usize;
        let mut name_buf = vec![0u8; namelen];
        if namelen > 0 {
            sync_stream.read_exact(&mut name_buf)?;
        }
        let name = String::from_utf8_lossy(&name_buf).into_owned();
        if name != "." && name != ".." {
            entries.push(SyncDent {
                mode,
                size,
                mtime,
                name,
            });
        }
    }
    Ok(entries)
}

/// Sends a file over an open `sync:` stream using `SEND` (v1) or `SND2` (`sendrecv_v2` with LZ4, Brotli, or Zstd compression).
fn sync_send_one_file(
    sync_stream: &mut TcpStream,
    local_path: &Path,
    remote_path: &str,
    dry_run: bool,
    comp: SyncCompression,
) -> Result<u64, AdbError> {
    let meta = fs::metadata(local_path)?;
    let mtime = meta
        .modified()
        .ok()
        .and_then(|t| t.duration_since(UNIX_EPOCH).ok())
        .map(|d| d.as_secs() as u32)
        .unwrap_or(0);
    let content = fs::read(local_path)?;
    let total_bytes = content.len() as u64;

    if dry_run && comp == SyncCompression::V1None {
        return Ok(total_bytes);
    }

    if comp != SyncCompression::V1None {
        let mut hdr1 = [0u8; 8];
        hdr1[0..4].copy_from_slice(SYNC_SND2);
        hdr1[4..8].copy_from_slice(&(remote_path.len() as u32).to_le_bytes());
        sync_stream.write_all(&hdr1)?;
        sync_stream.write_all(remote_path.as_bytes())?;

        let mut flags = match comp {
            SyncCompression::V2Brotli => SYNC_FLAG_BROTLI,
            SyncCompression::V2Lz4 => SYNC_FLAG_LZ4,
            SyncCompression::V2Zstd => SYNC_FLAG_ZSTD,
            _ => SYNC_FLAG_NONE,
        };
        if dry_run {
            flags |= SYNC_FLAG_DRY_RUN;
        }
        let mut hdr2 = [0u8; 12];
        hdr2[0..4].copy_from_slice(SYNC_SND2);
        hdr2[4..8].copy_from_slice(&33188u32.to_le_bytes()); // 0o100644
        hdr2[8..12].copy_from_slice(&flags.to_le_bytes());
        sync_stream.write_all(&hdr2)?;

        let payload_stream = match comp {
            SyncCompression::V2Brotli => brotli_rs::io::compress(
                &content,
                1,
                22,
                brotli_rs::enc::params::BrotliEncoderMode::Generic,
            )
            .map_err(|e| AdbError::Protocol(format!("Brotli encode error: {e}")))?,
            SyncCompression::V2Lz4 => {
                let mut enc = lz4_flex::frame::FrameEncoder::new(Vec::new());
                enc.write_all(&content)?;
                enc.finish()
                    .map_err(|e| AdbError::Protocol(format!("LZ4 frame encode error: {e}")))?
            }
            SyncCompression::V2Zstd => zstd::stream::encode_all(content.as_slice(), 1)
                .map_err(|e| AdbError::Protocol(format!("Zstd encode error: {e}")))?,
            _ => content,
        };

        for chunk in payload_stream.chunks(SYNC_DATA_MAX) {
            let mut dhdr = [0u8; 8];
            dhdr[0..4].copy_from_slice(SYNC_DATA);
            dhdr[4..8].copy_from_slice(&(chunk.len() as u32).to_le_bytes());
            sync_stream.write_all(&dhdr)?;
            sync_stream.write_all(chunk)?;
        }
    } else {
        let spec = format!("{remote_path},33188"); // 0o100644 = 33188
        let mut hdr = [0u8; 8];
        hdr[0..4].copy_from_slice(SYNC_SEND);
        hdr[4..8].copy_from_slice(&(spec.len() as u32).to_le_bytes());
        sync_stream.write_all(&hdr)?;
        sync_stream.write_all(spec.as_bytes())?;

        for chunk in content.chunks(SYNC_DATA_MAX) {
            let mut dhdr = [0u8; 8];
            dhdr[0..4].copy_from_slice(SYNC_DATA);
            dhdr[4..8].copy_from_slice(&(chunk.len() as u32).to_le_bytes());
            sync_stream.write_all(&dhdr)?;
            sync_stream.write_all(chunk)?;
        }
    }

    let mut done_hdr = [0u8; 8];
    done_hdr[0..4].copy_from_slice(SYNC_DONE);
    done_hdr[4..8].copy_from_slice(&mtime.to_le_bytes());
    sync_stream.write_all(&done_hdr)?;
    sync_stream.flush()?;

    let mut resp = [0u8; 8];
    sync_stream.read_exact(&mut resp)?;
    let id = &resp[0..4];
    let msg_len = u32::from_le_bytes(resp[4..8].try_into().unwrap()) as usize;
    if id == SYNC_OKAY {
        if msg_len > 0 {
            let mut skip = vec![0u8; msg_len];
            sync_stream.read_exact(&mut skip)?;
        }
        Ok(total_bytes)
    } else if id == SYNC_FAIL {
        let mut err_buf = vec![0u8; msg_len];
        if msg_len > 0 {
            sync_stream.read_exact(&mut err_buf)?;
        }
        Err(AdbError::RemoteFail(
            String::from_utf8_lossy(&err_buf).into_owned(),
        ))
    } else {
        Err(AdbError::Protocol(format!(
            "unexpected sync SEND response '{:?}'",
            id
        )))
    }
}

/// Recursively pushes a local file or directory tree over an open `sync:` stream.
fn sync_push_recursive(
    sync_stream: &mut TcpStream,
    local_path: &Path,
    remote_path: &str,
    dry_run: bool,
    comp: SyncCompression,
) -> Result<(u64, usize), AdbError> {
    let meta = fs::metadata(local_path)?;
    if meta.is_dir() {
        let mut entries: Vec<_> = fs::read_dir(local_path)?.collect::<Result<Vec<_>, _>>()?;
        entries.sort_by_key(|e| e.file_name());
        let mut total_bytes = 0u64;
        let mut total_files = 0usize;
        let base_remote = remote_path.trim_end_matches('/');
        for entry in entries {
            let child_name = entry.file_name().to_string_lossy().into_owned();
            let child_remote = format!("{base_remote}/{child_name}");
            let (b, c) = sync_push_recursive(
                sync_stream,
                &entry.path(),
                &child_remote,
                dry_run,
                comp,
            )?;
            total_bytes += b;
            total_files += c;
        }
        Ok((total_bytes, total_files))
    } else {
        let b = sync_send_one_file(sync_stream, local_path, remote_path, dry_run, comp)?;
        Ok((b, 1))
    }
}

/// Receives a single file over an open `sync:` stream using `RECV` (v1) or `RCV2` (`sendrecv_v2` with LZ4, Brotli, or Zstd decompression).
fn sync_recv_one_file(
    sync_stream: &mut TcpStream,
    remote_path: &str,
    local_path: &Path,
    comp: SyncCompression,
) -> Result<u64, AdbError> {
    if comp != SyncCompression::V1None {
        let mut hdr1 = [0u8; 8];
        hdr1[0..4].copy_from_slice(SYNC_RCV2);
        hdr1[4..8].copy_from_slice(&(remote_path.len() as u32).to_le_bytes());
        sync_stream.write_all(&hdr1)?;
        sync_stream.write_all(remote_path.as_bytes())?;

        let flags = match comp {
            SyncCompression::V2Brotli => SYNC_FLAG_BROTLI,
            SyncCompression::V2Lz4 => SYNC_FLAG_LZ4,
            SyncCompression::V2Zstd => SYNC_FLAG_ZSTD,
            _ => SYNC_FLAG_NONE,
        };
        let mut hdr2 = [0u8; 8];
        hdr2[0..4].copy_from_slice(SYNC_RCV2);
        hdr2[4..8].copy_from_slice(&flags.to_le_bytes());
        sync_stream.write_all(&hdr2)?;
        sync_stream.flush()?;
    } else {
        let mut hdr = [0u8; 8];
        hdr[0..4].copy_from_slice(SYNC_RECV);
        hdr[4..8].copy_from_slice(&(remote_path.len() as u32).to_le_bytes());
        sync_stream.write_all(&hdr)?;
        sync_stream.write_all(remote_path.as_bytes())?;
        sync_stream.flush()?;
    }

    if let Some(parent) = local_path.parent() {
        if !parent.as_os_str().is_empty() {
            fs::create_dir_all(parent)?;
        }
    }
    let mut raw_stream = Vec::new();
    loop {
        let mut chdr = [0u8; 8];
        sync_stream.read_exact(&mut chdr)?;
        let id = &chdr[0..4];
        let len = u32::from_le_bytes(chdr[4..8].try_into().unwrap()) as usize;
        if id == SYNC_DATA {
            let mut chunk = vec![0u8; len];
            sync_stream.read_exact(&mut chunk)?;
            raw_stream.extend_from_slice(&chunk);
        } else if id == SYNC_DONE {
            break;
        } else if id == SYNC_FAIL {
            let mut err_buf = vec![0u8; len];
            if len > 0 {
                sync_stream.read_exact(&mut err_buf)?;
            }
            return Err(AdbError::RemoteFail(
                String::from_utf8_lossy(&err_buf).into_owned(),
            ));
        } else {
            return Err(AdbError::Protocol(format!(
                "unexpected sync RECV chunk '{:?}'",
                id
            )));
        }
    }
    let out = match comp {
        SyncCompression::V2Brotli => brotli_rs::io::decompress(&raw_stream)
            .map_err(|e| AdbError::Protocol(format!("Brotli decompress error: {e}")))?,
        SyncCompression::V2Lz4 => {
            let mut dec = lz4_flex::frame::FrameDecoder::new(raw_stream.as_slice());
            let mut decompressed = Vec::new();
            dec.read_to_end(&mut decompressed)
                .map_err(|e| AdbError::Protocol(format!("LZ4 frame decompress error: {e}")))?;
            decompressed
        }
        SyncCompression::V2Zstd => zstd::stream::decode_all(raw_stream.as_slice())
            .map_err(|e| AdbError::Protocol(format!("Zstd decompress error: {e}")))?,
        _ => raw_stream,
    };
    let n = out.len() as u64;
    fs::write(local_path, &out)?;
    Ok(n)
}

/// Recursively pulls a remote file or directory tree over an open `sync:` stream.
fn sync_pull_recursive(
    sync_stream: &mut TcpStream,
    remote_path: &str,
    local_path: &Path,
    comp: SyncCompression,
    use_stat_v2: bool,
    use_ls_v2: bool,
) -> Result<(u64, usize), AdbError> {
    let mode_opt = if use_stat_v2 {
        sync_stat_v2(sync_stream, remote_path).ok().map(|(m, _, _)| m)
    } else {
        sync_stat(sync_stream, remote_path).ok().map(|(m, _, _)| m)
    };
    if let Some(mode) = mode_opt {
        if (mode & 0o170000) == 0o040000 {
            fs::create_dir_all(local_path)?;
            let dents = if use_ls_v2 {
                sync_list_v2(sync_stream, remote_path)?
            } else {
                sync_list(sync_stream, remote_path)?
            };
            let mut total_bytes = 0u64;
            let mut total_files = 0usize;
            let base_remote = remote_path.trim_end_matches('/');
            for d in dents {
                let child_remote = format!("{base_remote}/{}", d.name);
                let child_local = local_path.join(&d.name);
                if d.is_dir() {
                    let (b, c) = sync_pull_recursive(
                        sync_stream,
                        &child_remote,
                        &child_local,
                        comp,
                        use_stat_v2,
                        use_ls_v2,
                    )?;
                    total_bytes += b;
                    total_files += c;
                } else {
                    let b = sync_recv_one_file(sync_stream, &child_remote, &child_local, comp)?;
                    total_bytes += b;
                    total_files += 1;
                }
            }
            return Ok((total_bytes, total_files));
        }
    }

    let b = sync_recv_one_file(sync_stream, remote_path, local_path, comp)?;
    Ok((b, 1))
}

fn sync_quit(sync_stream: &mut TcpStream) {
    let mut q = [0u8; 8];
    q[0..4].copy_from_slice(SYNC_QUIT);
    let _ = sync_stream.write_all(&q);
    let _ = sync_stream.flush();
}

/// Top-level CLI runner for `adb` writing directly to stdio. Returns process exit code.
pub async fn run_cli(args: &[String]) -> i32 {
    let mut io_ctx = AdbIo::stdio();
    run_cli_with_io(args, &mut io_ctx).await
}

/// Captured/embedded CLI runner for `adb`. Zero stdio writes and zero child process spawns.
pub async fn run_cli_captured(
    args: &[String],
    cwd: Option<&Path>,
    timeout_ms: Option<u64>,
    cancel: Option<Arc<AtomicBool>>,
    on_event: Option<AdbEventCallback>,
) -> AdbRunResult {
    let mut io_ctx = AdbIo::captured(cwd, timeout_ms, cancel.as_deref(), on_event);
    let code = run_cli_with_io(args, &mut io_ctx).await;
    io_ctx.into_result(code)
}

async fn run_cli_with_io(args: &[String], io_ctx: &mut AdbIo<'_>) -> i32 {
    let cfg = match parse_cli_args(args) {
        Ok(c) => c,
        Err(e) => {
            io_ctx.write_err_line(&format!("adb: {e}"));
            return 1;
        }
    };

    match execute_command_io(&cfg, io_ctx).await {
        Ok(code) => code,
        Err(e) => {
            let s = e.to_string();
            if s.starts_with("adb:") {
                io_ctx.write_err_line(&s);
            } else {
                io_ctx.write_err_line(&format!("adb: error: {s}"));
            }
            1
        }
    }
}

async fn execute_command_io(cfg: &AdbCliConfig, io_ctx: &mut AdbIo<'_>) -> Result<i32, AdbError> {
    io_ctx.check_cancel_or_timeout()?;
    match &cfg.command {
        AdbCommand::Version => {
            io_ctx.write_out(&version_output())?;
            Ok(0)
        }
        AdbCommand::Help { exit_code } => {
            io_ctx.write_out(&help_output())?;
            Ok(*exit_code)
        }
        AdbCommand::RunServerForeground => {
            let bind_ip = if cfg.listen_all {
                "0.0.0.0"
            } else {
                &cfg.server_host
            };
            let addr: SocketAddr = format!("{bind_ip}:{}", cfg.server_port)
                .parse()
                .unwrap_or_else(|_| SocketAddr::from(([127, 0, 0, 1], cfg.server_port)));
            AdbHostServer::run_server(addr).await?;
            Ok(0)
        }
        AdbCommand::StartServer => {
            let _ = connect_to_server_async_io(cfg, true, io_ctx).await?;
            Ok(0)
        }
        AdbCommand::KillServer => {
            if let Ok(mut stream) = connect_to_server_async_io(cfg, false, io_ctx).await {
                let _ = send_smart_command_sync(&mut stream, "host:kill");
            }
            Ok(0)
        }
        AdbCommand::Devices { long } => {
            let cmd = if *long {
                "host:devices-l"
            } else {
                "host:devices"
            };
            let list = query_server_string_async_io(cfg, cmd, true, io_ctx).await?;
            io_ctx.write_out_line("List of devices attached")?;
            io_ctx.write_out(&list)?;
            if !list.ends_with('\n') && !list.is_empty() {
                io_ctx.write_out("\n")?;
            }
            io_ctx.write_out("\n")?;
            Ok(0)
        }
        AdbCommand::Connect(addr) => {
            let resp =
                query_server_string_async_io(cfg, &format!("host:connect:{addr}"), true, io_ctx)
                    .await?;
            io_ctx.write_out_line(&resp)?;
            Ok(0)
        }
        AdbCommand::Disconnect(addr_opt) => {
            let spec = addr_opt.as_deref().unwrap_or("");
            let resp = query_server_string_async_io(
                cfg,
                &format!("host:disconnect:{spec}"),
                true,
                io_ctx,
            )
            .await?;
            io_ctx.write_out_line(&resp)?;
            Ok(0)
        }
        AdbCommand::Pair { addr, code } => {
            let pw = code.as_deref().unwrap_or("000000");
            let resp =
                query_server_string_async_io(cfg, &format!("host:pair:{pw}:{addr}"), true, io_ctx)
                    .await?;
            io_ctx.write_out_line(&resp)?;
            Ok(0)
        }
        AdbCommand::MdnsCheck => {
            let resp = query_server_string_async_io(cfg, "host:mdns:check", true, io_ctx).await?;
            io_ctx.write_out_line(&resp)?;
            Ok(0)
        }
        AdbCommand::MdnsServices => {
            let resp =
                query_server_string_async_io(cfg, "host:mdns:services", true, io_ctx).await?;
            io_ctx.write_out_line("List of discovered mdns services")?;
            io_ctx.write_out(&resp)?;
            Ok(0)
        }
        AdbCommand::ForwardList => {
            let resp =
                query_server_string_async_io(cfg, "host:list-forward", true, io_ctx).await?;
            io_ctx.write_out(&resp)?;
            Ok(0)
        }
        AdbCommand::Forward {
            no_rebind,
            local,
            remote,
        } => {
            let nr = if *no_rebind { "norebind:" } else { "" };
            let sub = format!("forward:{nr}{local};{remote}");
            let req = cfg.target.scoped_host_cmd(&sub);
            let mut stream = connect_to_server_async_io(cfg, true, io_ctx).await?;
            send_smart_command_sync(&mut stream, &req)?;
            read_status_sync(&mut stream)?;
            if local == "tcp:0" {
                if let Ok(port_str) = read_length_prefixed_string_sync(&mut stream) {
                    io_ctx.write_out_line(&port_str)?;
                }
            }
            Ok(0)
        }
        AdbCommand::ForwardRemove(local) => {
            let req = cfg.target.scoped_host_cmd(&format!("killforward:{local}"));
            let mut stream = connect_to_server_async_io(cfg, true, io_ctx).await?;
            send_smart_command_sync(&mut stream, &req)?;
            read_status_sync(&mut stream)?;
            Ok(0)
        }
        AdbCommand::ForwardRemoveAll => {
            let mut stream = connect_to_server_async_io(cfg, true, io_ctx).await?;
            send_smart_command_sync(&mut stream, "host:killforward-all")?;
            read_status_sync(&mut stream)?;
            Ok(0)
        }
        AdbCommand::ReverseList => {
            let mut stream =
                open_device_service_async_io(cfg, &cfg.target, "reverse:list-forward", io_ctx)
                    .await?;
            let list = read_length_prefixed_string_sync(&mut stream)?;
            io_ctx.write_out(&list)?;
            Ok(0)
        }
        AdbCommand::Reverse {
            no_rebind,
            remote,
            local,
        } => {
            let nr = if *no_rebind { "norebind:" } else { "" };
            let svc = format!("reverse:forward:{nr}{remote};{local}");
            let mut stream = open_device_service_async_io(cfg, &cfg.target, &svc, io_ctx).await?;
            read_status_sync(&mut stream)?;
            Ok(0)
        }
        AdbCommand::ReverseRemove(remote) => {
            let svc = format!("reverse:killforward:{remote}");
            let mut stream = open_device_service_async_io(cfg, &cfg.target, &svc, io_ctx).await?;
            read_status_sync(&mut stream)?;
            Ok(0)
        }
        AdbCommand::ReverseRemoveAll => {
            let mut stream =
                open_device_service_async_io(cfg, &cfg.target, "reverse:killforward-all", io_ctx)
                    .await?;
            read_status_sync(&mut stream)?;
            Ok(0)
        }
        AdbCommand::Push {
            sources,
            remote,
            dry_run,
            quiet,
            compression,
        } => {
            let comp = resolve_sync_compression_io(cfg, compression.as_deref(), io_ctx).await;
            let mut sync_stream =
                open_device_service_async_io(cfg, &cfg.target, "sync:", io_ctx).await?;
            let mut total = 0u64;
            let mut count = 0usize;
            for src in sources {
                io_ctx.check_cancel_or_timeout()?;
                let resolved_src = io_ctx.resolve_path(src);
                let target_remote = if sources.len() > 1 || remote.ends_with('/') {
                    let fname = resolved_src
                        .file_name()
                        .map(|s| s.to_string_lossy().into_owned())
                        .unwrap_or_else(|| "file".to_string());
                    format!("{}/{}", remote.trim_end_matches('/'), fname)
                } else {
                    remote.clone()
                };
                let (b, c) = sync_push_recursive(
                    &mut sync_stream,
                    &resolved_src,
                    &target_remote,
                    *dry_run,
                    comp,
                )?;
                total += b;
                count += c;
                io_ctx.emit_progress("push", total, total, &format!("Pushed {target_remote}"));
            }
            sync_quit(&mut sync_stream);
            if !*quiet {
                let first_name = sources[0].display();
                io_ctx.write_err_line(&format!(
                    "{first_name}: {count} file pushed, 0 skipped. ({total} bytes)"
                ));
            }
            Ok(0)
        }
        AdbCommand::Pull {
            remotes,
            local,
            preserve_attrs: _,
            quiet,
            compression,
        } => {
            let resolved_local = io_ctx.resolve_path(local);
            let comp = resolve_sync_compression_io(cfg, compression.as_deref(), io_ctx).await;
            let use_stat_v2 = device_supports_feature_io(cfg, "stat_v2", io_ctx).await;
            let use_ls_v2 = device_supports_feature_io(cfg, "ls_v2", io_ctx).await;
            let mut sync_stream =
                open_device_service_async_io(cfg, &cfg.target, "sync:", io_ctx).await?;
            let mut total = 0u64;
            let mut count = 0usize;
            for r in remotes {
                io_ctx.check_cancel_or_timeout()?;
                let dest = if remotes.len() > 1 || resolved_local.is_dir() {
                    let fname = r.rsplit('/').next().unwrap_or("file");
                    resolved_local.join(fname)
                } else {
                    resolved_local.clone()
                };
                let (b, c) = sync_pull_recursive(
                    &mut sync_stream,
                    r,
                    &dest,
                    comp,
                    use_stat_v2,
                    use_ls_v2,
                )?;
                total += b;
                count += c;
                io_ctx.emit_progress("pull", total, total, &format!("Pulled {r}"));
            }
            sync_quit(&mut sync_stream);
            if !*quiet {
                io_ctx.write_err_line(&format!(
                    "{}: {count} file pulled, 0 skipped. ({total} bytes)",
                    remotes[0]
                ));
            }
            Ok(0)
        }
        AdbCommand::Sync {
            partition,
            list_only,
            dry_run,
            quiet,
        } => {
            let out_dir = std::env::var("ANDROID_PRODUCT_OUT").map_err(|_| {
                AdbError::InvalidArgument("ANDROID_PRODUCT_OUT not set".into())
            })?;
            let parts: Vec<&str> = match partition.as_deref() {
                None | Some("all") => vec![
                    "system",
                    "vendor",
                    "oem",
                    "data",
                    "product",
                    "system_ext",
                    "odm",
                    "vendor_dlkm",
                    "odm_dlkm",
                    "system_dlkm",
                ],
                Some(p) => vec![p],
            };
            let comp = resolve_sync_compression_io(cfg, None, io_ctx).await;
            let mut sync_stream =
                open_device_service_async_io(cfg, &cfg.target, "sync:", io_ctx).await?;
            let mut total_bytes = 0u64;
            let mut total_files = 0usize;
            for part in parts {
                io_ctx.check_cancel_or_timeout()?;
                let local_part = Path::new(&out_dir).join(part);
                if !local_part.is_dir() {
                    continue;
                }
                let remote_part = format!("/{part}");
                if *list_only {
                    io_ctx.write_out_line(&format!(
                        "syncing {remote_part} from {}",
                        local_part.display()
                    ))?;
                    continue;
                }
                let (b, c) = sync_push_recursive(
                    &mut sync_stream,
                    &local_part,
                    &remote_part,
                    *dry_run,
                    comp,
                )?;
                total_bytes += b;
                total_files += c;
            }
            sync_quit(&mut sync_stream);
            if !*quiet && !*list_only {
                io_ctx.write_err_line(&format!(
                    "sync: {total_files} file pushed, 0 skipped. ({total_bytes} bytes)"
                ));
            }
            Ok(0)
        }
        AdbCommand::Shell { raw_mode, command } => {
            run_shell_command_io(cfg, *raw_mode, command, io_ctx).await
        }
        AdbCommand::Emu(cmd_tokens) => {
            let joined = cmd_tokens.join(" ");
            run_device_stream_to_stdout_io(cfg, &format!("emu:{joined}"), io_ctx).await
        }
        AdbCommand::Install { flags, apk } => {
            let resolved_apk = io_ctx.resolve_path(apk);
            let fname = resolved_apk
                .file_name()
                .map(|s| s.to_string_lossy().into_owned())
                .unwrap_or_else(|| "base.apk".to_string());
            let remote_tmp = format!("/data/local/tmp/{fname}");
            {
                let comp = resolve_sync_compression_io(cfg, None, io_ctx).await;
                let mut sync_stream =
                    open_device_service_async_io(cfg, &cfg.target, "sync:", io_ctx).await?;
                let _ =
                    sync_send_one_file(&mut sync_stream, &resolved_apk, &remote_tmp, false, comp)?;
                sync_quit(&mut sync_stream);
            }
            io_ctx.write_out_line("Performing Push Install")?;
            let flags_str = flags.join(" ");
            let pm_cmd = if flags_str.is_empty() {
                format!("pm install \"{remote_tmp}\"")
            } else {
                format!("pm install {flags_str} \"{remote_tmp}\"")
            };
            let code = run_shell_command_io(cfg, false, &[pm_cmd], io_ctx).await?;
            let _ = run_shell_command_io(cfg, false, &[format!("rm -f \"{remote_tmp}\"")], io_ctx)
                .await;
            Ok(code)
        }
        AdbCommand::InstallMultiple { flags, apks }
        | AdbCommand::InstallMultiPackage { flags, apks } => {
            let resolved_apks: Vec<PathBuf> = apks.iter().map(|p| io_ctx.resolve_path(p)).collect();
            let total_size: u64 = resolved_apks
                .iter()
                .filter_map(|p| fs::metadata(p).ok().map(|m| m.len()))
                .sum();
            let comp = resolve_sync_compression_io(cfg, None, io_ctx).await;
            let mut remote_tmps = Vec::new();
            {
                let mut sync_stream =
                    open_device_service_async_io(cfg, &cfg.target, "sync:", io_ctx).await?;
                for (idx, apk) in resolved_apks.iter().enumerate() {
                    io_ctx.check_cancel_or_timeout()?;
                    let fname = apk
                        .file_name()
                        .map(|s| s.to_string_lossy().into_owned())
                        .unwrap_or_else(|| format!("split_{idx}.apk"));
                    let rtmp = format!("/data/local/tmp/{idx}_{fname}");
                    let sz = sync_send_one_file(&mut sync_stream, apk, &rtmp, false, comp)?;
                    remote_tmps.push((idx, fname, rtmp, sz));
                }
                sync_quit(&mut sync_stream);
            }
            let flags_str = flags.join(" ");
            let create_cmd = if flags_str.is_empty() {
                format!("pm install-create -S {total_size}")
            } else {
                format!("pm install-create -S {total_size} {flags_str}")
            };
            let create_out = capture_shell_stdout_io(cfg, &create_cmd, io_ctx).await?;
            if let Some(session_id) = parse_bracketed_session_id(&create_out) {
                for (idx, fname, rtmp, sz) in &remote_tmps {
                    let write_cmd =
                        format!("pm install-write -S {sz} {session_id} {idx}_{fname} \"{rtmp}\"");
                    let _ = run_shell_command_io(cfg, false, &[write_cmd], io_ctx).await?;
                }
                let commit_code = run_shell_command_io(
                    cfg,
                    false,
                    &[format!("pm install-commit {session_id}")],
                    io_ctx,
                )
                .await?;
                for (_, _, rtmp, _) in &remote_tmps {
                    let _ = run_shell_command_io(
                        cfg,
                        false,
                        &[format!("rm -f \"{rtmp}\"")],
                        io_ctx,
                    )
                    .await;
                }
                Ok(commit_code)
            } else {
                io_ctx.write_out(&create_out)?;
                Ok(0)
            }
        }
        AdbCommand::Uninstall { keep_data, package } => {
            let cmd = if *keep_data {
                format!("pm uninstall -k {package}")
            } else {
                format!("pm uninstall {package}")
            };
            run_shell_command_io(cfg, false, &[cmd], io_ctx).await
        }
        AdbCommand::Bugreport(path_opt) => {
            let out = capture_shell_stdout_io(cfg, "bugreportz", io_ctx).await?;
            io_ctx.write_out(&out)?;
            if let Some(remote_zip) = out
                .lines()
                .find_map(|l| l.trim().strip_prefix("OK:"))
            {
                let local_dest = match path_opt {
                    Some(p) => {
                        let rp = io_ctx.resolve_path(p);
                        if rp.is_dir() {
                            let fname = remote_zip.rsplit('/').next().unwrap_or("bugreport.zip");
                            rp.join(fname)
                        } else if rp.extension().is_none() {
                            rp.with_extension("zip")
                        } else {
                            rp
                        }
                    }
                    None => {
                        let fname = remote_zip.rsplit('/').next().unwrap_or("bugreport.zip");
                        io_ctx.resolve_path(Path::new(fname))
                    }
                };
                let comp = resolve_sync_compression_io(cfg, None, io_ctx).await;
                let mut sync_stream =
                    open_device_service_async_io(cfg, &cfg.target, "sync:", io_ctx).await?;
                let _ =
                    sync_recv_one_file(&mut sync_stream, remote_zip.trim(), &local_dest, comp)?;
                sync_quit(&mut sync_stream);
                io_ctx.write_out_line(&format!("Bug report copied to {}", local_dest.display()))?;
            }
            Ok(0)
        }
        AdbCommand::Jdwp => run_device_stream_to_stdout_io(cfg, "jdwp", io_ctx).await,
        AdbCommand::Logcat(log_args) => {
            let mut cmd = vec!["logcat".to_string()];
            cmd.extend_from_slice(log_args);
            run_shell_command_io(cfg, false, &cmd, io_ctx).await
        }
        AdbCommand::DisableVerity => {
            run_device_stream_to_stdout_io(cfg, "disable-verity:", io_ctx).await
        }
        AdbCommand::EnableVerity => {
            run_device_stream_to_stdout_io(cfg, "enable-verity:", io_ctx).await
        }
        AdbCommand::Keygen(path) => {
            let resolved = io_ctx.resolve_path(path);
            generate_adb_keypair(&resolved)?;
            Ok(0)
        }
        AdbCommand::WaitFor {
            transport: _,
            state,
        } => {
            for _ in 0..100 {
                io_ctx.check_cancel_or_timeout()?;
                let req = cfg.target.scoped_host_cmd("get-state");
                if let Ok(cur_state) = query_server_string_async_io(cfg, &req, true, io_ctx).await {
                    if cur_state.trim() == state {
                        return Ok(0);
                    }
                }
                tokio::time::sleep(Duration::from_millis(50)).await;
            }
            Err(AdbError::RemoteFail(format!(
                "timed out waiting for '{state}'"
            )))
        }
        AdbCommand::GetState => {
            let req = cfg.target.scoped_host_cmd("get-state");
            let s = query_server_string_async_io(cfg, &req, true, io_ctx).await?;
            io_ctx.write_out_line(&s)?;
            Ok(0)
        }
        AdbCommand::GetSerialNo => {
            let req = cfg.target.scoped_host_cmd("get-serialno");
            let s = query_server_string_async_io(cfg, &req, true, io_ctx).await?;
            io_ctx.write_out_line(&s)?;
            Ok(0)
        }
        AdbCommand::GetDevPath => {
            let req = cfg.target.scoped_host_cmd("get-devpath");
            let s = query_server_string_async_io(cfg, &req, true, io_ctx).await?;
            io_ctx.write_out_line(&s)?;
            Ok(0)
        }
        AdbCommand::Remount { auto_reboot } => {
            let svc = if *auto_reboot {
                "remount:-R"
            } else {
                "remount:"
            };
            run_device_stream_to_stdout_io(cfg, svc, io_ctx).await
        }
        AdbCommand::Reboot(target_opt) => {
            let svc = format!("reboot:{}", target_opt.as_deref().unwrap_or(""));
            let _ = open_device_service_async_io(cfg, &cfg.target, &svc, io_ctx).await?;
            Ok(0)
        }
        AdbCommand::Sideload(pkg_path) => {
            let resolved = io_ctx.resolve_path(pkg_path);
            run_sideload_host_io(cfg, &resolved, io_ctx).await
        }
        AdbCommand::Root => run_device_stream_to_stdout_io(cfg, "root:", io_ctx).await,
        AdbCommand::Unroot => run_device_stream_to_stdout_io(cfg, "unroot:", io_ctx).await,
        AdbCommand::Usb => run_device_stream_to_stdout_io(cfg, "usb:", io_ctx).await,
        AdbCommand::Tcpip(port) => {
            run_device_stream_to_stdout_io(cfg, &format!("tcpip:{port}"), io_ctx).await
        }
        AdbCommand::Reconnect(sub_opt) => {
            let _ = sub_opt;
            let req = cfg.target.scoped_host_cmd("reconnect");
            let s = query_server_string_async_io(cfg, &req, true, io_ctx).await?;
            io_ctx.write_out_line(&s)?;
            Ok(0)
        }
        AdbCommand::Attach | AdbCommand::Detach => Ok(0),
        AdbCommand::Features => {
            let req = cfg.target.scoped_host_cmd("features");
            let s = query_server_string_async_io(cfg, &req, true, io_ctx).await?;
            io_ctx.write_out_line(&s)?;
            Ok(0)
        }
        AdbCommand::HostFeatures => {
            let s =
                query_server_string_async_io(cfg, "host:host-features", true, io_ctx).await?;
            io_ctx.write_out_line(&s)?;
            Ok(0)
        }
    }
}

async fn capture_shell_stdout_io(
    cfg: &AdbCliConfig,
    cmd_str: &str,
    io_ctx: &mut AdbIo<'_>,
) -> Result<String, AdbError> {
    if device_supports_feature_io(cfg, "shell_v2", io_ctx).await {
        let service = format!("shell,v2:{cmd_str}");
        let mut stream =
            open_device_service_async_io(cfg, &cfg.target, &service, io_ctx).await?;
        let mut out = Vec::new();
        while let Some((id, payload)) = read_shell_v2_packet_cancellable(&mut stream, io_ctx)? {
            match id {
                SHELL_V2_STDOUT | SHELL_V2_STDERR => out.extend_from_slice(&payload),
                SHELL_V2_EXIT => break,
                _ => {}
            }
        }
        return Ok(String::from_utf8_lossy(&out).into_owned());
    }
    let service = format!("shell:{cmd_str}");
    let mut stream = open_device_service_async_io(cfg, &cfg.target, &service, io_ctx).await?;
    let mut out = Vec::new();
    let mut buf = [0u8; 8192];
    loop {
        let n = read_some_cancellable(&mut stream, &mut buf, io_ctx)?;
        if n == 0 {
            break;
        }
        out.extend_from_slice(&buf[..n]);
    }
    Ok(String::from_utf8_lossy(&out).into_owned())
}

fn parse_bracketed_session_id(text: &str) -> Option<String> {
    let start = text.find('[')?;
    let end = text[start + 1..].find(']')? + start + 1;
    let id = text[start + 1..end].trim();
    if !id.is_empty() && id.chars().all(|c| c.is_ascii_digit()) {
        Some(id.to_string())
    } else {
        None
    }
}

async fn run_sideload_host_io(
    cfg: &AdbCliConfig,
    pkg_path: &Path,
    io_ctx: &mut AdbIo<'_>,
) -> Result<i32, AdbError> {
    const SIDELOAD_CHUNK_SIZE: usize = 65536;
    let data = fs::read(pkg_path)?;
    let total_size = data.len();
    let mut sent_total = 0u64;
    let host_svc = format!("sideload-host:{total_size}:{SIDELOAD_CHUNK_SIZE}");
    match open_device_service_async_io(cfg, &cfg.target, &host_svc, io_ctx).await {
        Ok(mut stream) => {
            let mut req_buf = [0u8; 8];
            while read_exact_cancellable(&mut stream, &mut req_buf, io_ctx).is_ok() {
                let cmd_str = String::from_utf8_lossy(&req_buf);
                if cmd_str == "DONEDONE" {
                    io_ctx.emit_progress("sideload", total_size as u64, total_size as u64, "Total xfer: 1.00x");
                    io_ctx.write_out_line("Total xfer: 1.00x")?;
                    return Ok(0);
                }
                if cmd_str == "FAILFAIL" {
                    return Err(AdbError::RemoteFail("sideload failed on device".into()));
                }
                let block_idx: usize = cmd_str.trim().parse().map_err(|_| {
                    AdbError::Protocol(format!("invalid sideload block request '{cmd_str}'"))
                })?;
                let offset = block_idx.saturating_mul(SIDELOAD_CHUNK_SIZE);
                if offset < total_size {
                    let end = (offset + SIDELOAD_CHUNK_SIZE).min(total_size);
                    stream.write_all(&data[offset..end])?;
                    stream.flush()?;
                    sent_total = (sent_total + (end - offset) as u64).min(total_size as u64);
                    io_ctx.emit_progress(
                        "sideload",
                        sent_total,
                        total_size as u64,
                        &format!("Sideloading {}/{} KB", sent_total / 1024, total_size / 1024),
                    );
                }
            }
            Ok(0)
        }
        Err(_) => {
            // Fallback to legacy `sideload:<size>` stream
            let svc = format!("sideload:{total_size}");
            let mut stream = open_device_service_async_io(cfg, &cfg.target, &svc, io_ctx).await?;
            for chunk in data.chunks(SIDELOAD_CHUNK_SIZE) {
                io_ctx.check_cancel_or_timeout()?;
                stream.write_all(chunk)?;
                sent_total += chunk.len() as u64;
                io_ctx.emit_progress(
                    "sideload",
                    sent_total,
                    total_size as u64,
                    &format!("Sideloading {}/{} KB", sent_total / 1024, total_size / 1024),
                );
            }
            stream.flush()?;
            Ok(0)
        }
    }
}
