//! USB (`nusb`), TCP (`FB01`), and UDP Fastboot transport implementations (MIT).

use std::collections::BTreeMap;
use std::io::{self, Read, Write};
use std::net::{SocketAddr, TcpStream, ToSocketAddrs, UdpSocket};
use std::time::{Duration, Instant};

use nusb::descriptors::TransferType;
use nusb::transfer::{Buffer, Bulk, Direction, In, Out};
use nusb::{DeviceInfo, Endpoint, Interface};

use crate::protocol::{
    FastbootError, FastbootResponse, DEFAULT_NET_PORT, DOWNLOAD_CHUNK_SIZE, TCP_HANDSHAKE,
};

/// Information about an enumerated fastboot device for `fastboot devices [-l]`.
#[derive(Debug, Clone)]
pub struct FastbootDeviceEntry {
    pub serial: String,
    pub mode: String,
    pub device_path: String,
}

/// Lists all currently connected USB fastboot devices (`class 0xff, subclass 0x42, protocol 0x03`).
pub async fn list_usb_devices() -> Result<Vec<FastbootDeviceEntry>, FastbootError> {
    let devs = nusb::list_devices()
        .await
        .map_err(|e| FastbootError::Usb(e.to_string()))?;
    let mut entries = Vec::new();
    for info in devs {
        if find_fastboot_interface(&info).is_some() {
            let serial = info
                .serial_number()
                .map(str::to_string)
                .unwrap_or_else(|| "????????????".to_string());
            let device_path = format!("usb:{:04x}:{:04x}", info.vendor_id(), info.product_id());
            entries.push(FastbootDeviceEntry {
                serial,
                mode: "fastboot".to_string(),
                device_path,
            });
        }
    }
    Ok(entries)
}

fn find_fastboot_interface(info: &DeviceInfo) -> Option<u8> {
    info.interfaces().find_map(|i| {
        if i.class() == 0xff && i.subclass() == 0x42 && i.protocol() == 0x03 {
            Some(i.interface_number())
        } else {
            None
        }
    })
}

/// Unified Fastboot transport covering USB, TCP, and UDP connections.
pub enum AnyFastbootTransport {
    Tcp(TcpFastbootTransport),
    Udp(UdpFastbootTransport),
    Usb(UsbFastbootTransport),
}

fn stdio_output_sink(text: &str, _is_stderr: bool) {
    eprint!("{text}");
}

fn noop_abort() -> Result<(), FastbootError> {
    Ok(())
}

impl AnyFastbootTransport {
    /// Connects to the device specified by `serial` (`Some("tcp:...")`, `Some("udp:...")`,
    /// `Some("<usb_serial>")`, or `None` for the first available USB device or `ANDROID_SERIAL`).
    pub async fn connect(serial_opt: Option<&str>) -> Result<Self, FastbootError> {
        let env_serial = std::env::var("ANDROID_SERIAL").ok();
        let target = serial_opt.or(env_serial.as_deref());

        if let Some(s) = target {
            if let Some(rest) = s.strip_prefix("tcp:") {
                let tcp = TcpFastbootTransport::connect(rest)?;
                return Ok(Self::Tcp(tcp));
            }
            if let Some(rest) = s.strip_prefix("udp:") {
                let udp = UdpFastbootTransport::connect(rest)?;
                return Ok(Self::Udp(udp));
            }
        }

        let usb = UsbFastbootTransport::open(target).await?;
        Ok(Self::Usb(usb))
    }

    /// Sends a raw command string and processes `INFO`/`TEXT` messages until `OKAY` or `FAIL`.
    pub async fn execute_command(&mut self, cmd: &str) -> Result<(String, Duration), FastbootError> {
        let mut sink = stdio_output_sink;
        self.execute_command_io(cmd, &noop_abort, &mut sink).await
    }

    pub async fn execute_command_io(
        &mut self,
        cmd: &str,
        check_abort: &dyn Fn() -> Result<(), FastbootError>,
        on_output: &mut dyn FnMut(&str, bool),
    ) -> Result<(String, Duration), FastbootError> {
        check_abort()?;
        let start = Instant::now();
        self.send_packet(cmd.as_bytes()).await?;
        let res = self.read_until_okay_or_fail_io(check_abort, on_output).await?;
        Ok((res, start.elapsed()))
    }

    /// Queries a single bootloader variable via `getvar:<name>`, returning `Ok(Some(val))` on
    /// `OKAY` and `Ok(None)` if the bootloader responds with `FAIL`.
    pub async fn get_var_optional(&mut self, name: &str) -> Result<Option<String>, FastbootError> {
        self.get_var_optional_io(name, &noop_abort).await
    }

    pub async fn get_var_optional_io(
        &mut self,
        name: &str,
        check_abort: &dyn Fn() -> Result<(), FastbootError>,
    ) -> Result<Option<String>, FastbootError> {
        check_abort()?;
        let cmd = format!("getvar:{name}");
        self.send_packet(cmd.as_bytes()).await?;
        loop {
            match self.read_response_abortable(check_abort).await? {
                FastbootResponse::Info(_) | FastbootResponse::Text(_) => {}
                FastbootResponse::Okay(v) => return Ok(Some(v)),
                FastbootResponse::Fail(_) => return Ok(None),
                FastbootResponse::Data(_) => {
                    return Err(FastbootError::Protocol("unexpected DATA in getvar".into()));
                }
            }
        }
    }

    /// Queries `getvar:<name>`, printing `(bootloader)` lines and returning the `OKAY` value.
    pub async fn get_var(&mut self, name: &str) -> Result<(String, Duration), FastbootError> {
        let mut sink = stdio_output_sink;
        self.get_var_io(name, &noop_abort, &mut sink).await
    }

    pub async fn get_var_io(
        &mut self,
        name: &str,
        check_abort: &dyn Fn() -> Result<(), FastbootError>,
        on_output: &mut dyn FnMut(&str, bool),
    ) -> Result<(String, Duration), FastbootError> {
        check_abort()?;
        let start = Instant::now();
        let cmd = format!("getvar:{name}");
        self.send_packet(cmd.as_bytes()).await?;
        loop {
            match self.read_response_abortable(check_abort).await? {
                FastbootResponse::Info(line) => {
                    on_output(&format!("(bootloader) {line}\n"), true);
                }
                FastbootResponse::Text(txt) => {
                    on_output(&txt, true);
                }
                FastbootResponse::Okay(v) => return Ok((v, start.elapsed())),
                FastbootResponse::Fail(reason) => return Err(FastbootError::RemoteFail(reason)),
                FastbootResponse::Data(_) => {
                    return Err(FastbootError::Protocol("unexpected DATA in getvar".into()));
                }
            }
        }
    }

    /// Executes `getvar:all` and returns all `(line)` strings reported via `INFO`.
    pub async fn get_all_vars(&mut self) -> Result<(Vec<String>, Duration), FastbootError> {
        let mut sink = stdio_output_sink;
        self.get_all_vars_io(&noop_abort, &mut sink).await
    }

    pub async fn get_all_vars_io(
        &mut self,
        check_abort: &dyn Fn() -> Result<(), FastbootError>,
        on_output: &mut dyn FnMut(&str, bool),
    ) -> Result<(Vec<String>, Duration), FastbootError> {
        check_abort()?;
        let start = Instant::now();
        self.send_packet(b"getvar:all").await?;
        let mut lines = Vec::new();
        loop {
            match self.read_response_abortable(check_abort).await? {
                FastbootResponse::Info(line) => {
                    on_output(&format!("(bootloader) {line}\n"), true);
                    lines.push(line);
                }
                FastbootResponse::Text(txt) => {
                    on_output(&txt, true);
                }
                FastbootResponse::Okay(_) => return Ok((lines, start.elapsed())),
                FastbootResponse::Fail(reason) => return Err(FastbootError::RemoteFail(reason)),
                FastbootResponse::Data(_) => {
                    return Err(FastbootError::Protocol(
                        "unexpected DATA in getvar:all".into(),
                    ));
                }
            }
        }
    }

    /// Downloads `data` to the device using `download:<size:08x>`.
    pub async fn download_data(&mut self, data: &[u8]) -> Result<Duration, FastbootError> {
        let mut sink = stdio_output_sink;
        self.download_data_with_progress(data, &noop_abort, &mut sink, |_, _| {})
            .await
    }

    /// Downloads `data` to the device while emitting chunk-level `(bytes_sent, bytes_total)` progress.
    pub async fn download_data_with_progress(
        &mut self,
        data: &[u8],
        check_abort: &dyn Fn() -> Result<(), FastbootError>,
        on_output: &mut dyn FnMut(&str, bool),
        mut on_bytes: impl FnMut(u64, u64),
    ) -> Result<Duration, FastbootError> {
        check_abort()?;
        let start = Instant::now();
        let size = u32::try_from(data.len()).map_err(|_| {
            FastbootError::InvalidArgument(format!("download buffer too large ({})", data.len()))
        })?;
        let cmd = format!("download:{size:08x}");
        self.send_packet(cmd.as_bytes()).await?;

        loop {
            match self.read_response_abortable(check_abort).await? {
                FastbootResponse::Info(i) => on_output(&format!("(bootloader) {i}\n"), true),
                FastbootResponse::Text(t) => on_output(&t, true),
                FastbootResponse::Data(expected) => {
                    if expected != size {
                        return Err(FastbootError::Protocol(format!(
                            "device requested {expected} bytes, expected {size}"
                        )));
                    }
                    break;
                }
                FastbootResponse::Fail(reason) => return Err(FastbootError::RemoteFail(reason)),
                FastbootResponse::Okay(_) => {
                    return Err(FastbootError::Protocol(
                        "unexpected OKAY before DATA phase".into(),
                    ));
                }
            }
        }

        self.send_raw_payload_with_progress(data, check_abort, &mut on_bytes)
            .await?;
        let _ = self.read_until_okay_or_fail_io(check_abort, on_output).await?;
        Ok(start.elapsed())
    }

    /// Executes an upload command (`upload` for `get_staged` or `fetch:...`) and returns the
    /// uploaded payload bytes.
    pub async fn upload_data(&mut self, cmd: &str) -> Result<(Vec<u8>, Duration), FastbootError> {
        let mut sink = stdio_output_sink;
        self.upload_data_io(cmd, &noop_abort, &mut sink).await
    }

    pub async fn upload_data_io(
        &mut self,
        cmd: &str,
        check_abort: &dyn Fn() -> Result<(), FastbootError>,
        on_output: &mut dyn FnMut(&str, bool),
    ) -> Result<(Vec<u8>, Duration), FastbootError> {
        check_abort()?;
        let start = Instant::now();
        self.send_packet(cmd.as_bytes()).await?;
        let data_len = loop {
            match self.read_response_abortable(check_abort).await? {
                FastbootResponse::Info(i) => on_output(&format!("(bootloader) {i}\n"), true),
                FastbootResponse::Text(t) => on_output(&t, true),
                FastbootResponse::Data(sz) => break sz as usize,
                FastbootResponse::Fail(reason) => return Err(FastbootError::RemoteFail(reason)),
                FastbootResponse::Okay(_) => {
                    return Err(FastbootError::Protocol(
                        "unexpected OKAY before upload DATA phase".into(),
                    ));
                }
            }
        };

        let payload = self.recv_raw_payload_abortable(data_len, check_abort).await?;
        let _ = self.read_until_okay_or_fail_io(check_abort, on_output).await?;
        Ok((payload, start.elapsed()))
    }

    async fn read_until_okay_or_fail_io(
        &mut self,
        check_abort: &dyn Fn() -> Result<(), FastbootError>,
        on_output: &mut dyn FnMut(&str, bool),
    ) -> Result<String, FastbootError> {
        loop {
            match self.read_response_abortable(check_abort).await? {
                FastbootResponse::Info(i) => on_output(&format!("(bootloader) {i}\n"), true),
                FastbootResponse::Text(t) => on_output(&t, true),
                FastbootResponse::Okay(msg) => return Ok(msg),
                FastbootResponse::Fail(reason) => return Err(FastbootError::RemoteFail(reason)),
                FastbootResponse::Data(_) => {
                    return Err(FastbootError::Protocol("unexpected DATA reply".into()));
                }
            }
        }
    }

    async fn send_packet(&mut self, data: &[u8]) -> Result<(), FastbootError> {
        match self {
            Self::Tcp(t) => t.send_packet(data),
            Self::Udp(u) => u.send_packet(data),
            Self::Usb(u) => u.send_packet(data).await,
        }
    }

    async fn read_response_abortable(
        &mut self,
        check_abort: &dyn Fn() -> Result<(), FastbootError>,
    ) -> Result<FastbootResponse, FastbootError> {
        check_abort()?;
        let raw = match self {
            Self::Tcp(t) => t.recv_packet_abortable(check_abort)?,
            Self::Udp(u) => u.recv_packet()?,
            Self::Usb(u) => u.recv_packet().await?,
        };
        FastbootResponse::from_bytes(&raw)
    }

    async fn send_raw_payload_with_progress(
        &mut self,
        data: &[u8],
        check_abort: &dyn Fn() -> Result<(), FastbootError>,
        on_bytes: &mut dyn FnMut(u64, u64),
    ) -> Result<(), FastbootError> {
        let total = data.len() as u64;
        let mut sent = 0u64;
        if total == 0 {
            on_bytes(0, 0);
            return Ok(());
        }
        match self {
            Self::Tcp(t) => {
                let step = (256 * 1024usize).min(DOWNLOAD_CHUNK_SIZE);
                for chunk in data.chunks(step) {
                    check_abort()?;
                    t.send_packet_abortable(chunk, check_abort)?;
                    sent += chunk.len() as u64;
                    on_bytes(sent, total);
                }
                Ok(())
            }
            Self::Udp(u) => {
                for chunk in data.chunks(u.max_payload) {
                    check_abort()?;
                    u.send_packet(chunk)?;
                    sent += chunk.len() as u64;
                    on_bytes(sent, total);
                }
                Ok(())
            }
            Self::Usb(u) => {
                let step = (256 * 1024usize)
                    .min(DOWNLOAD_CHUNK_SIZE)
                    .next_multiple_of(u.max_out.max(512));
                for chunk in data.chunks(step) {
                    check_abort()?;
                    u.send_packet(chunk).await?;
                    sent += chunk.len() as u64;
                    on_bytes(sent, total);
                }
                Ok(())
            }
        }
    }

    async fn recv_raw_payload_abortable(
        &mut self,
        total_len: usize,
        check_abort: &dyn Fn() -> Result<(), FastbootError>,
    ) -> Result<Vec<u8>, FastbootError> {
        match self {
            Self::Tcp(t) => t.recv_payload_abortable(total_len, check_abort),
            Self::Udp(u) => u.recv_payload(total_len),
            Self::Usb(u) => u.recv_payload(total_len).await,
        }
    }
}

fn resolve_host_port(spec: &str) -> Result<SocketAddr, FastbootError> {
    let addr_str = if spec.starts_with('[') {
        if spec.contains("]:") {
            spec.to_string()
        } else {
            format!("{spec}:{DEFAULT_NET_PORT}")
        }
    } else if spec.contains(':') {
        spec.to_string()
    } else {
        format!("{spec}:{DEFAULT_NET_PORT}")
    };
    addr_str
        .to_socket_addrs()?
        .next()
        .ok_or_else(|| FastbootError::InvalidArgument(format!("could not resolve '{spec}'")))
}

/// TCP Fastboot transport implementing the AOSP `FB01` + 8-byte big-endian length framing.
pub struct TcpFastbootTransport {
    stream: TcpStream,
}

impl TcpFastbootTransport {
    pub fn connect(host_spec: &str) -> Result<Self, FastbootError> {
        let addr = resolve_host_port(host_spec)?;
        let mut stream = TcpStream::connect_timeout(&addr, Duration::from_secs(5))?;
        let _ = stream.set_nodelay(true);
        let _ = stream.set_read_timeout(Some(Duration::from_millis(250)));
        let _ = stream.set_write_timeout(Some(Duration::from_millis(250)));
        stream.write_all(TCP_HANDSHAKE)?;
        let mut resp = [0u8; 4];
        Self::read_exact_loop(&mut stream, &mut resp, &noop_abort)?;
        if &resp != TCP_HANDSHAKE {
            return Err(FastbootError::Protocol(
                "invalid TCP fastboot handshake (expected 'FB01')".into(),
            ));
        }
        Ok(Self { stream })
    }

    fn read_exact_loop(
        stream: &mut TcpStream,
        mut buf: &mut [u8],
        check_abort: &dyn Fn() -> Result<(), FastbootError>,
    ) -> Result<(), FastbootError> {
        let start = Instant::now();
        while !buf.is_empty() {
            check_abort()?;
            match stream.read(buf) {
                Ok(0) => {
                    return Err(FastbootError::Io(io::Error::new(
                        io::ErrorKind::UnexpectedEof,
                        "early EOF on TCP fastboot socket",
                    )));
                }
                Ok(n) => buf = &mut buf[n..],
                Err(e)
                    if matches!(
                        e.kind(),
                        io::ErrorKind::WouldBlock
                            | io::ErrorKind::TimedOut
                            | io::ErrorKind::Interrupted
                    ) =>
                {
                    check_abort()?;
                    if start.elapsed() > Duration::from_secs(120) {
                        return Err(FastbootError::TimedOut);
                    }
                    continue;
                }
                Err(e) => return Err(FastbootError::Io(e)),
            }
        }
        Ok(())
    }

    fn write_all_loop(
        stream: &mut TcpStream,
        mut buf: &[u8],
        check_abort: &dyn Fn() -> Result<(), FastbootError>,
    ) -> Result<(), FastbootError> {
        let start = Instant::now();
        while !buf.is_empty() {
            check_abort()?;
            match stream.write(buf) {
                Ok(0) => {
                    return Err(FastbootError::Io(io::Error::new(
                        io::ErrorKind::WriteZero,
                        "failed to write TCP fastboot packet",
                    )));
                }
                Ok(n) => buf = &buf[n..],
                Err(e)
                    if matches!(
                        e.kind(),
                        io::ErrorKind::WouldBlock
                            | io::ErrorKind::TimedOut
                            | io::ErrorKind::Interrupted
                    ) =>
                {
                    check_abort()?;
                    if start.elapsed() > Duration::from_secs(30) {
                        return Err(FastbootError::TimedOut);
                    }
                    continue;
                }
                Err(e) => return Err(FastbootError::Io(e)),
            }
        }
        Ok(())
    }

    pub fn send_packet(&mut self, data: &[u8]) -> Result<(), FastbootError> {
        self.send_packet_abortable(data, &noop_abort)
    }

    pub fn send_packet_abortable(
        &mut self,
        data: &[u8],
        check_abort: &dyn Fn() -> Result<(), FastbootError>,
    ) -> Result<(), FastbootError> {
        let len_bytes = (data.len() as u64).to_be_bytes();
        Self::write_all_loop(&mut self.stream, &len_bytes, check_abort)?;
        Self::write_all_loop(&mut self.stream, data, check_abort)?;
        self.stream.flush()?;
        Ok(())
    }

    pub fn recv_packet(&mut self) -> Result<Vec<u8>, FastbootError> {
        self.recv_packet_abortable(&noop_abort)
    }

    pub fn recv_packet_abortable(
        &mut self,
        check_abort: &dyn Fn() -> Result<(), FastbootError>,
    ) -> Result<Vec<u8>, FastbootError> {
        let mut len_bytes = [0u8; 8];
        Self::read_exact_loop(&mut self.stream, &mut len_bytes, check_abort)?;
        let len = u64::from_be_bytes(len_bytes) as usize;
        let mut buf = vec![0u8; len];
        Self::read_exact_loop(&mut self.stream, &mut buf, check_abort)?;
        Ok(buf)
    }

    pub fn recv_payload(&mut self, total_len: usize) -> Result<Vec<u8>, FastbootError> {
        self.recv_payload_abortable(total_len, &noop_abort)
    }

    pub fn recv_payload_abortable(
        &mut self,
        total_len: usize,
        check_abort: &dyn Fn() -> Result<(), FastbootError>,
    ) -> Result<Vec<u8>, FastbootError> {
        let mut out = Vec::with_capacity(total_len);
        while out.len() < total_len {
            let chunk = self.recv_packet_abortable(check_abort)?;
            if chunk.is_empty() {
                return Err(FastbootError::Protocol("unexpected empty TCP data chunk".into()));
            }
            out.extend_from_slice(&chunk);
        }
        Ok(out)
    }
}

/// UDP Fastboot transport implementing the AOSP 4-byte `[id, flags, seq_hi, seq_lo]` framing.
pub struct UdpFastbootTransport {
    socket: UdpSocket,
    seq: u16,
    pub max_payload: usize,
}

impl UdpFastbootTransport {
    pub fn connect(host_spec: &str) -> Result<Self, FastbootError> {
        let addr = resolve_host_port(host_spec)?;
        let bind_addr = if addr.is_ipv6() {
            "[::]:0"
        } else {
            "0.0.0.0:0"
        };
        let socket = UdpSocket::bind(bind_addr)?;
        socket.connect(addr)?;
        socket.set_read_timeout(Some(Duration::from_millis(500)))?;

        // 1. Send Query packet (id = 0x01) to read device's next sequence number
        let query_pkt = [0x01u8, 0x00, 0x00, 0x00];
        socket.send(&query_pkt)?;
        let mut buf = [0u8; 2048];
        let n = socket.recv(&mut buf)?;
        if n < 6 || buf[0] != 0x01 {
            return Err(FastbootError::Protocol("invalid UDP query response".into()));
        }
        let mut seq = u16::from_be_bytes([buf[4], buf[5]]);

        // 2. Send Init packet (id = 0x02): version=1 (u16 BE), max_pkt_size=1024 (u16 BE)
        let init_pkt = [
            0x02u8,
            0x00,
            (seq >> 8) as u8,
            seq as u8,
            0x00,
            0x01,
            0x04,
            0x00,
        ];
        socket.send(&init_pkt)?;
        let n = socket.recv(&mut buf)?;
        if n < 8 || buf[0] != 0x02 {
            return Err(FastbootError::Protocol("invalid UDP init response".into()));
        }
        let negotiated_max = u16::from_be_bytes([buf[6], buf[7]]).max(512) as usize;
        seq = seq.wrapping_add(1);

        Ok(Self {
            socket,
            seq,
            max_payload: negotiated_max - 4,
        })
    }

    fn exchange_raw(&mut self, id: u8, flags: u8, payload: &[u8]) -> Result<Vec<u8>, FastbootError> {
        let mut pkt = Vec::with_capacity(4 + payload.len());
        pkt.push(id);
        pkt.push(flags);
        pkt.extend_from_slice(&self.seq.to_be_bytes());
        pkt.extend_from_slice(payload);

        let mut resp = vec![0u8; 65536];
        for _ in 0..10 {
            self.socket.send(&pkt)?;
            match self.socket.recv(&mut resp) {
                Ok(n) if n >= 4 => {
                    let r_id = resp[0];
                    let r_seq = u16::from_be_bytes([resp[2], resp[3]]);
                    if r_seq == self.seq {
                        self.seq = self.seq.wrapping_add(1);
                        if r_id == 0x00 {
                            let msg = String::from_utf8_lossy(&resp[4..n]).into_owned();
                            return Err(FastbootError::Protocol(format!("UDP error: {msg}")));
                        }
                        return Ok(resp[4..n].to_vec());
                    }
                }
                _ => continue,
            }
        }
        Err(FastbootError::Protocol("UDP fastboot timed out".into()))
    }

    pub fn send_packet(&mut self, data: &[u8]) -> Result<(), FastbootError> {
        for (idx, chunk) in data.chunks(self.max_payload).enumerate() {
            let is_last = (idx + 1) * self.max_payload >= data.len();
            let flags = if is_last { 0x00 } else { 0x01 };
            let _ = self.exchange_raw(0x03, flags, chunk)?;
        }
        Ok(())
    }

    pub fn recv_packet(&mut self) -> Result<Vec<u8>, FastbootError> {
        let mut out = Vec::new();
        loop {
            let mut pkt = [0u8; 4];
            pkt[0] = 0x03;
            pkt[2..4].copy_from_slice(&self.seq.to_be_bytes());
            self.socket.send(&pkt)?;
            let mut resp = vec![0u8; 65536];
            let n = self.socket.recv(&mut resp)?;
            if n < 4 {
                return Err(FastbootError::Protocol("short UDP packet".into()));
            }
            let flags = resp[1];
            self.seq = self.seq.wrapping_add(1);
            out.extend_from_slice(&resp[4..n]);
            if (flags & 0x01) == 0 {
                break;
            }
        }
        Ok(out)
    }

    pub fn recv_payload(&mut self, total_len: usize) -> Result<Vec<u8>, FastbootError> {
        let mut out = Vec::with_capacity(total_len);
        while out.len() < total_len {
            let chunk = self.recv_packet()?;
            out.extend_from_slice(&chunk);
        }
        out.truncate(total_len);
        Ok(out)
    }
}

/// USB Fastboot transport backed by `nusb`.
pub struct UsbFastbootTransport {
    ep_out: Endpoint<Bulk, Out>,
    max_out: usize,
    ep_in: Endpoint<Bulk, In>,
    max_in: usize,
}

impl UsbFastbootTransport {
    pub async fn open(wanted_serial: Option<&str>) -> Result<Self, FastbootError> {
        let devs = nusb::list_devices()
            .await
            .map_err(|e| FastbootError::Usb(e.to_string()))?;
        let mut matched: Option<(DeviceInfo, u8)> = None;
        for info in devs {
            if let Some(if_num) = find_fastboot_interface(&info) {
                if let Some(w) = wanted_serial {
                    if info.serial_number() == Some(w) {
                        matched = Some((info, if_num));
                        break;
                    }
                } else {
                    matched = Some((info, if_num));
                    break;
                }
            }
        }

        let (info, if_num) = matched.ok_or(FastbootError::NoDevice)?;
        let dev = info
            .open()
            .await
            .map_err(|e| FastbootError::Usb(e.to_string()))?;
        let interface = dev
            .claim_interface(if_num)
            .await
            .map_err(|e| FastbootError::Usb(e.to_string()))?;
        Self::from_interface(interface)
    }

    fn from_interface(interface: Interface) -> Result<Self, FastbootError> {
        let (ep_out_addr, max_out, ep_in_addr, max_in) = interface
            .descriptors()
            .find_map(|alt| {
                let (out_a, out_m) = alt.endpoints().find_map(|ep| {
                    if ep.transfer_type() == TransferType::Bulk && ep.direction() == Direction::Out
                    {
                        Some((ep.address(), ep.max_packet_size()))
                    } else {
                        None
                    }
                })?;
                let (in_a, in_m) = alt.endpoints().find_map(|ep| {
                    if ep.transfer_type() == TransferType::Bulk && ep.direction() == Direction::In {
                        Some((ep.address(), ep.max_packet_size()))
                    } else {
                        None
                    }
                })?;
                Some((out_a, out_m, in_a, in_m))
            })
            .ok_or_else(|| FastbootError::Usb("missing bulk IN/OUT endpoints".into()))?;

        let ep_out = interface
            .endpoint::<Bulk, Out>(ep_out_addr)
            .map_err(|e| FastbootError::Usb(e.to_string()))?;
        let ep_in = interface
            .endpoint::<Bulk, In>(ep_in_addr)
            .map_err(|e| FastbootError::Usb(e.to_string()))?;

        Ok(Self {
            ep_out,
            max_out,
            ep_in,
            max_in,
        })
    }

    pub async fn send_packet(&mut self, data: &[u8]) -> Result<(), FastbootError> {
        self.ep_out.submit(data.to_vec().into());
        self.ep_out
            .next_complete()
            .await
            .into_result()
            .map_err(|e| FastbootError::Usb(e.to_string()))?;
        Ok(())
    }

    pub async fn recv_packet(&mut self) -> Result<Vec<u8>, FastbootError> {
        self.ep_in.submit(Buffer::new(self.max_in.max(64)));
        let buf = self
            .ep_in
            .next_complete()
            .await
            .into_result()
            .map_err(|e| FastbootError::Usb(e.to_string()))?;
        Ok(buf.to_vec())
    }

    pub async fn send_payload(&mut self, data: &[u8]) -> Result<(), FastbootError> {
        let chunk_sz = DOWNLOAD_CHUNK_SIZE.next_multiple_of(self.max_out.max(512));
        for chunk in data.chunks(chunk_sz) {
            self.send_packet(chunk).await?;
        }
        Ok(())
    }

    pub async fn recv_payload(&mut self, total_len: usize) -> Result<Vec<u8>, FastbootError> {
        let mut out = Vec::with_capacity(total_len);
        while out.len() < total_len {
            let want = (total_len - out.len()).min(DOWNLOAD_CHUNK_SIZE);
            self.ep_in.submit(Buffer::new(want.next_multiple_of(self.max_in.max(64))));
            let buf = self
                .ep_in
                .next_complete()
                .await
                .into_result()
                .map_err(|e| FastbootError::Usb(e.to_string()))?;
            out.extend_from_slice(&buf);
        }
        out.truncate(total_len);
        Ok(out)
    }
}

/// Helper to parse `getvar:all` INFO lines into a key-value map.
pub fn parse_all_vars_map(lines: &[String]) -> BTreeMap<String, String> {
    let mut map = BTreeMap::new();
    for line in lines {
        if let Some((k, v)) = line.rsplit_once(':') {
            map.insert(k.trim().to_string(), v.trim().to_string());
        }
    }
    map
}
