//! ADB Host Server Daemon (port 5037) and `adbd` Device Multiplexer (MIT).

use std::collections::HashMap;
use std::net::SocketAddr;
use std::sync::atomic::{AtomicBool, AtomicU32, AtomicU64, Ordering};
use std::sync::{Arc, OnceLock};
use std::time::Duration;

use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::{TcpListener, TcpStream};
use tokio::sync::{mpsc, Mutex, RwLock};

use crate::auth::{load_or_create_default_key, sign_auth_token};
use crate::protocol::*;
use crate::usb::{
    read_usb_packet, scan_usb_adb_devices, write_usb_packet, UsbAdbDeviceCandidate,
    UsbAdbTransport,
};

/// Connected `adbd` device managed by the host server.
pub struct ConnectedDevice {
    pub transport_id: u64,
    pub serial: String,
    pub state: String,
    pub devpath: String,
    pub product: String,
    pub model: String,
    pub device_name: String,
    pub features: String,
    pub is_usb: bool,
    pub alive: AtomicBool,
    next_local_id: AtomicU32,
    writer_tx: mpsc::UnboundedSender<AdbPacket>,
    streams: Mutex<HashMap<u32, mpsc::UnboundedSender<AdbPacket>>>,
    reverses: Mutex<HashMap<String, String>>,
}

impl ConnectedDevice {
    pub fn is_alive(&self) -> bool {
        self.alive.load(Ordering::Relaxed)
    }

    /// Connects to a USB `adbd` device via `nusb`, performs `A_CNXN` (+ `A_AUTH` RSA-2048 if
    /// requested), and spawns the background USB bulk writer and reader demux loops.
    pub async fn connect_usb(
        candidate: &UsbAdbDeviceCandidate,
        transport_id: u64,
    ) -> Result<Arc<Self>, AdbError> {
        let mut usb = UsbAdbTransport::open(candidate).await?;

        let cnxn_banner = format!("host::features={HOST_FEATURES}\0").into_bytes();
        let cnxn_pkt = AdbPacket::new(A_CNXN, A_VERSION, MAX_PAYLOAD, cnxn_banner);
        write_usb_packet(&mut usb.ep_out, usb.max_out, &cnxn_pkt).await?;

        let (priv_key, pub_key_bytes) = load_or_create_default_key()?;
        let mut tried_sig = false;

        let banner_str = loop {
            let pkt = read_usb_packet(&mut usb.ep_in, usb.max_in).await?;
            match pkt.command {
                A_CNXN => {
                    break String::from_utf8_lossy(&pkt.payload)
                        .trim_end_matches('\0')
                        .to_string();
                }
                A_AUTH if pkt.arg0 == ADB_AUTH_TOKEN => {
                    if !tried_sig {
                        tried_sig = true;
                        let sig = sign_auth_token(&priv_key, &pkt.payload)?;
                        let auth_reply = AdbPacket::new(A_AUTH, ADB_AUTH_SIGNATURE, 0, sig);
                        write_usb_packet(&mut usb.ep_out, usb.max_out, &auth_reply).await?;
                    } else {
                        let pub_reply =
                            AdbPacket::new(A_AUTH, ADB_AUTH_RSAPUBLICKEY, 0, pub_key_bytes.clone());
                        write_usb_packet(&mut usb.ep_out, usb.max_out, &pub_reply).await?;
                    }
                }
                _ => continue,
            }
        };

        let (state, product, model, device_name, features) = parse_cnxn_banner(&banner_str);
        let mut ep_out = usb.ep_out;
        let max_out = usb.max_out;
        let mut ep_in = usb.ep_in;
        let max_in = usb.max_in;
        let (writer_tx, mut writer_rx) = mpsc::unbounded_channel::<AdbPacket>();

        let dev = Arc::new(Self {
            transport_id,
            serial: candidate.serial.clone(),
            state,
            devpath: candidate.devpath.clone(),
            product,
            model,
            device_name,
            features,
            is_usb: true,
            alive: AtomicBool::new(true),
            next_local_id: AtomicU32::new(1),
            writer_tx,
            streams: Mutex::new(HashMap::new()),
            reverses: Mutex::new(HashMap::new()),
        });

        // USB bulk writer loop
        let dev_writer_weak = Arc::downgrade(&dev);
        tokio::spawn(async move {
            while let Some(pkt) = writer_rx.recv().await {
                if write_usb_packet(&mut ep_out, max_out, &pkt).await.is_err() {
                    if let Some(d) = dev_writer_weak.upgrade() {
                        d.alive.store(false, Ordering::Relaxed);
                    }
                    break;
                }
            }
        });

        // USB bulk reader demux loop
        let dev_reader_weak = Arc::downgrade(&dev);
        tokio::spawn(async move {
            loop {
                let Ok(pkt) = read_usb_packet(&mut ep_in, max_in).await else {
                    if let Some(d) = dev_reader_weak.upgrade() {
                        d.alive.store(false, Ordering::Relaxed);
                    }
                    break;
                };
                let Some(d) = dev_reader_weak.upgrade() else {
                    break;
                };
                match pkt.command {
                    A_OKAY | A_WRTE | A_CLSE => {
                        let local_id = pkt.arg1;
                        let map = d.streams.lock().await;
                        if let Some(tx) = map.get(&local_id) {
                            let _ = tx.send(pkt);
                        }
                    }
                    A_OPEN => {
                        let remote_id = pkt.arg0;
                        let svc = String::from_utf8_lossy(&pkt.payload)
                            .trim_end_matches('\0')
                            .to_string();
                        let d_clone = Arc::clone(&d);
                        tokio::spawn(async move {
                            d_clone.handle_incoming_reverse_open(remote_id, svc).await;
                        });
                    }
                    _ => {}
                }
            }
        });

        Ok(dev)
    }
    /// Connects to a TCP `adbd` daemon (`host:port`), performs `A_CNXN` (and `A_AUTH` if required),
    /// and spawns the background packet multiplexer loop.
    pub async fn connect_tcp(
        serial: String,
        addr: SocketAddr,
        transport_id: u64,
    ) -> Result<Arc<Self>, AdbError> {
        let mut stream = TcpStream::connect(addr).await?;
        let _ = stream.set_nodelay(true);

        let cnxn_banner = format!("host::features={HOST_FEATURES}\0").into_bytes();
        let cnxn_pkt = AdbPacket::new(A_CNXN, A_VERSION, MAX_PAYLOAD, cnxn_banner);
        cnxn_pkt.write_to(&mut stream).await?;

        let (priv_key, pub_key_bytes) = load_or_create_default_key()?;
        let mut tried_sig = false;

        let banner_str = loop {
            let pkt = AdbPacket::read_from(&mut stream).await?;
            match pkt.command {
                A_CNXN => {
                    break String::from_utf8_lossy(&pkt.payload)
                        .trim_end_matches('\0')
                        .to_string();
                }
                A_AUTH if pkt.arg0 == ADB_AUTH_TOKEN => {
                    if !tried_sig {
                        tried_sig = true;
                        let sig = sign_auth_token(&priv_key, &pkt.payload)?;
                        let auth_reply = AdbPacket::new(A_AUTH, ADB_AUTH_SIGNATURE, 0, sig);
                        auth_reply.write_to(&mut stream).await?;
                    } else {
                        let pub_reply =
                            AdbPacket::new(A_AUTH, ADB_AUTH_RSAPUBLICKEY, 0, pub_key_bytes.clone());
                        pub_reply.write_to(&mut stream).await?;
                    }
                }
                other => {
                    return Err(AdbError::Protocol(format!(
                        "unexpected packet 0x{other:08x} during CNXN handshake"
                    )));
                }
            }
        };

        let (state, product, model, device_name, features) = parse_cnxn_banner(&banner_str);
        let (mut reader_half, mut writer_half) = stream.into_split();
        let (writer_tx, mut writer_rx) = mpsc::unbounded_channel::<AdbPacket>();

        let dev = Arc::new(Self {
            transport_id,
            serial: serial.clone(),
            state,
            devpath: format!("tcp:{addr}"),
            product,
            model,
            device_name,
            features,
            is_usb: false,
            alive: AtomicBool::new(true),
            next_local_id: AtomicU32::new(1),
            writer_tx,
            streams: Mutex::new(HashMap::new()),
            reverses: Mutex::new(HashMap::new()),
        });

        // Writer loop
        tokio::spawn(async move {
            while let Some(pkt) = writer_rx.recv().await {
                if pkt.write_to(&mut writer_half).await.is_err() {
                    break;
                }
            }
        });

        // Reader demux loop
        let dev_weak = Arc::downgrade(&dev);
        tokio::spawn(async move {
            while let Ok(pkt) = AdbPacket::read_from(&mut reader_half).await {
                let Some(d) = dev_weak.upgrade() else {
                    break;
                };
                match pkt.command {
                    A_OKAY | A_WRTE | A_CLSE => {
                        let local_id = pkt.arg1;
                        let map = d.streams.lock().await;
                        if let Some(tx) = map.get(&local_id) {
                            let _ = tx.send(pkt);
                        }
                    }
                    A_OPEN => {
                        // Incoming stream from device (e.g. `adb reverse`)
                        let remote_id = pkt.arg0;
                        let svc = String::from_utf8_lossy(&pkt.payload)
                            .trim_end_matches('\0')
                            .to_string();
                        let d_clone = Arc::clone(&d);
                        tokio::spawn(async move {
                            d_clone.handle_incoming_reverse_open(remote_id, svc).await;
                        });
                    }
                    _ => {}
                }
            }
        });

        Ok(dev)
    }

    async fn handle_incoming_reverse_open(self: Arc<Self>, remote_id: u32, svc: String) {
        let local_target = {
            let rev = self.reverses.lock().await;
            rev.get(&svc).cloned().unwrap_or(svc)
        };
        let Some(port_str) = local_target.strip_prefix("tcp:") else {
            let _ = self
                .writer_tx
                .send(AdbPacket::new(A_CLSE, 0, remote_id, Vec::new()));
            return;
        };
        let Ok(port) = port_str.parse::<u16>() else {
            let _ = self
                .writer_tx
                .send(AdbPacket::new(A_CLSE, 0, remote_id, Vec::new()));
            return;
        };
        let Ok(mut tcp) = TcpStream::connect(("127.0.0.1", port)).await else {
            let _ = self
                .writer_tx
                .send(AdbPacket::new(A_CLSE, 0, remote_id, Vec::new()));
            return;
        };

        let local_id = self.next_local_id.fetch_add(1, Ordering::Relaxed);
        let (tx, mut rx) = mpsc::unbounded_channel();
        self.streams.lock().await.insert(local_id, tx);
        let _ = self
            .writer_tx
            .send(AdbPacket::new(A_OKAY, local_id, remote_id, Vec::new()));

        let mut buf = vec![0u8; 32768];
        loop {
            tokio::select! {
                res = tcp.read(&mut buf) => {
                    match res {
                        Ok(0) | Err(_) => {
                            let _ = self.writer_tx.send(AdbPacket::new(A_CLSE, local_id, remote_id, Vec::new()));
                            break;
                        }
                        Ok(n) => {
                            let _ = self.writer_tx.send(AdbPacket::new(A_WRTE, local_id, remote_id, buf[..n].to_vec()));
                        }
                    }
                }
                pkt_opt = rx.recv() => {
                    match pkt_opt {
                        Some(pkt) if pkt.command == A_WRTE => {
                            let _ = self.writer_tx.send(AdbPacket::new(A_OKAY, local_id, remote_id, Vec::new()));
                            if tcp.write_all(&pkt.payload).await.is_err() {
                                break;
                            }
                        }
                        Some(pkt) if pkt.command == A_CLSE => {
                            break;
                        }
                        None => break,
                        _ => {}
                    }
                }
            }
        }
        self.streams.lock().await.remove(&local_id);
    }

    /// Opens a stream to `service` on this `adbd` device and relays packets with `client_socket`.
    pub async fn proxy_service_to_client(
        &self,
        service: &str,
        mut client_socket: TcpStream,
    ) -> Result<(), AdbError> {
        // Intercept `reverse:` commands handled directly or mirrored on the host
        if let Some(rev_cmd) = service.strip_prefix("reverse:") {
            return self
                .handle_reverse_command(rev_cmd, &mut client_socket)
                .await;
        }

        let local_id = self.next_local_id.fetch_add(1, Ordering::Relaxed);
        let (tx, mut rx) = mpsc::unbounded_channel();
        self.streams.lock().await.insert(local_id, tx);

        let mut open_payload = service.as_bytes().to_vec();
        open_payload.push(0);
        let _ = self
            .writer_tx
            .send(AdbPacket::new(A_OPEN, local_id, 0, open_payload));

        // Wait for A_OKAY or A_CLSE
        let remote_id = match rx.recv().await {
            Some(pkt) if pkt.command == A_OKAY => pkt.arg0,
            Some(pkt) if pkt.command == A_CLSE => {
                self.streams.lock().await.remove(&local_id);
                client_socket
                    .write_all(&encode_fail("device rejected service"))
                    .await?;
                return Ok(());
            }
            _ => {
                self.streams.lock().await.remove(&local_id);
                client_socket
                    .write_all(&encode_fail("connection closed"))
                    .await?;
                return Ok(());
            }
        };

        client_socket.write_all(b"OKAY").await?;
        client_socket.flush().await?;

        let mut read_buf = vec![0u8; 32768];
        let mut client_eof = false;
        let mut awaiting_okay = false;

        loop {
            tokio::select! {
                res = client_socket.read(&mut read_buf), if !client_eof && !awaiting_okay => {
                    match res {
                        Ok(0) | Err(_) => {
                            client_eof = true;
                        }
                        Ok(n) => {
                            awaiting_okay = true;
                            let _ = self.writer_tx.send(AdbPacket::new(
                                A_WRTE,
                                local_id,
                                remote_id,
                                read_buf[..n].to_vec(),
                            ));
                        }
                    }
                }
                pkt_opt = rx.recv() => {
                    match pkt_opt {
                        Some(pkt) if pkt.command == A_WRTE => {
                            let _ = self.writer_tx.send(AdbPacket::new(A_OKAY, local_id, remote_id, Vec::new()));
                            if client_socket.write_all(&pkt.payload).await.is_err() {
                                let _ = self.writer_tx.send(AdbPacket::new(A_CLSE, local_id, remote_id, Vec::new()));
                                break;
                            }
                            let _ = client_socket.flush().await;
                        }
                        Some(pkt) if pkt.command == A_CLSE => {
                            let _ = self.writer_tx.send(AdbPacket::new(A_CLSE, local_id, remote_id, Vec::new()));
                            break;
                        }
                        Some(pkt) if pkt.command == A_OKAY => {
                            awaiting_okay = false;
                        }
                        None => break,
                        _ => {}
                    }
                }
            }
        }

        self.streams.lock().await.remove(&local_id);
        Ok(())
    }

    async fn handle_reverse_command(
        &self,
        rev_cmd: &str,
        client_socket: &mut TcpStream,
    ) -> Result<(), AdbError> {
        if let Some(rest) = rev_cmd.strip_prefix("forward:") {
            let spec = rest.strip_prefix("norebind:").unwrap_or(rest);
            let Some((remote, local)) = spec.split_once(';') else {
                client_socket
                    .write_all(&encode_fail("bad reverse forward spec"))
                    .await?;
                return Ok(());
            };
            self.reverses
                .lock()
                .await
                .insert(remote.to_string(), local.to_string());
            client_socket.write_all(b"OKAYOKAY").await?;
        } else if let Some(remote) = rev_cmd.strip_prefix("killforward:") {
            self.reverses.lock().await.remove(remote);
            client_socket.write_all(b"OKAYOKAY").await?;
        } else if rev_cmd == "killforward-all" {
            self.reverses.lock().await.clear();
            client_socket.write_all(b"OKAYOKAY").await?;
        } else if rev_cmd == "list-forward" {
            let rev = self.reverses.lock().await;
            let mut out = String::new();
            for (r, l) in rev.iter() {
                out.push_str(&format!("(reverse) {r} {l}\n"));
            }
            client_socket
                .write_all(&encode_okay_with_payload(&out))
                .await?;
        } else {
            client_socket
                .write_all(&encode_fail("unknown reverse command"))
                .await?;
        }
        client_socket.flush().await?;
        Ok(())
    }
}

fn parse_cnxn_banner(banner: &str) -> (String, String, String, String, String) {
    let mut parts = banner.splitn(3, ':');
    let state_raw = parts.next().unwrap_or("device");
    let state = match state_raw {
        "device" | "recovery" | "sideload" | "rescue" | "bootloader" | "offline"
        | "unauthorized" => state_raw.to_string(),
        _ => "device".to_string(),
    };
    let _ = parts.next();
    let props_str = parts.next().unwrap_or("");

    let mut product = "unknown".to_string();
    let mut model = "unknown".to_string();
    let mut device_name = "unknown".to_string();
    let mut features = HOST_FEATURES.to_string();

    for kv in props_str.split(';') {
        if let Some((k, v)) = kv.split_once('=') {
            match k.trim() {
                "ro.product.name" => product = v.trim().to_string(),
                "ro.product.model" => model = v.trim().to_string(),
                "ro.product.device" => device_name = v.trim().to_string(),
                "features" => features = v.trim().to_string(),
                _ => {}
            }
        }
    }

    (state, product, model, device_name, features)
}

/// Active host-side port forward (`adb forward`).
pub struct ForwardEntry {
    pub serial: String,
    pub local_spec: String,
    pub remote_spec: String,
    pub cancel_tx: mpsc::UnboundedSender<()>,
}

/// Shared state of the ADB Host Server Daemon.
pub struct AdbHostServer {
    pub devices: RwLock<Vec<Arc<ConnectedDevice>>>,
    pub forwards: Mutex<HashMap<String, ForwardEntry>>,
    next_transport_id: AtomicU64,
    shutdown_tx: mpsc::UnboundedSender<()>,
}

static INPROC_ADB_RT: OnceLock<tokio::runtime::Runtime> = OnceLock::new();
static INPROC_ADB_SERVERS: OnceLock<std::sync::Mutex<HashMap<SocketAddr, mpsc::UnboundedSender<()>>>> =
    OnceLock::new();

fn inproc_servers_map(
) -> &'static std::sync::Mutex<HashMap<SocketAddr, mpsc::UnboundedSender<()>>> {
    INPROC_ADB_SERVERS.get_or_init(|| std::sync::Mutex::new(HashMap::new()))
}

fn inproc_runtime() -> Result<&'static tokio::runtime::Runtime, AdbError> {
    if let Some(rt) = INPROC_ADB_RT.get() {
        return Ok(rt);
    }
    let rt = tokio::runtime::Builder::new_multi_thread()
        .worker_threads(2)
        .thread_name("rak-adb-srv")
        .enable_all()
        .build()
        .map_err(AdbError::Io)?;
    Ok(INPROC_ADB_RT.get_or_init(|| rt))
}

/// Ensures an in-process ADB host server is running on `bind_addr` without spawning a subprocess.
/// Returns `Ok(true)` if a new server was started, `Ok(false)` if one was already listening.
pub async fn ensure_in_process_server_async(bind_addr: SocketAddr) -> Result<bool, AdbError> {
    if std::net::TcpStream::connect_timeout(&bind_addr, Duration::from_millis(120)).is_ok() {
        return Ok(false);
    }
    let std_listener = match std::net::TcpListener::bind(bind_addr) {
        Ok(l) => l,
        Err(e) => {
            if std::net::TcpStream::connect_timeout(&bind_addr, Duration::from_millis(120)).is_ok()
            {
                return Ok(false);
            }
            return Err(AdbError::Io(e));
        }
    };
    std_listener.set_nonblocking(true)?;

    let rt = inproc_runtime()?;
    let (ready_tx, ready_rx) = tokio::sync::oneshot::channel::<()>();
    let (shutdown_tx, shutdown_rx) = mpsc::unbounded_channel::<()>();

    if let Ok(mut map) = inproc_servers_map().lock() {
        map.insert(bind_addr, shutdown_tx.clone());
    }

    let listener = {
        let _guard = rt.enter();
        TcpListener::from_std(std_listener)?
    };
    rt.spawn(async move {
        let _ = AdbHostServer::run_with_listener_inner(
            listener,
            shutdown_tx,
            shutdown_rx,
            Some(ready_tx),
            Some(bind_addr),
        )
        .await;
    });

    let _ = tokio::time::timeout(Duration::from_secs(3), ready_rx).await;
    Ok(true)
}

/// Synchronous wrapper for [`ensure_in_process_server_async`].
pub fn ensure_in_process_server(bind_addr: SocketAddr) -> Result<bool, AdbError> {
    inproc_runtime()?.block_on(ensure_in_process_server_async(bind_addr))
}

/// Gracefully shuts down all in-process ADB host servers started by this process.
pub fn shutdown_in_process_servers() {
    if let Some(map) = INPROC_ADB_SERVERS.get() {
        if let Ok(mut guard) = map.lock() {
            for (_addr, tx) in guard.drain() {
                let _ = tx.send(());
            }
        }
    }
}

impl AdbHostServer {
    /// Starts the ADB host server listening on `bind_addr` (e.g. `127.0.0.1:5037`) until
    /// `host:kill` is received.
    pub async fn run_server(bind_addr: SocketAddr) -> Result<(), AdbError> {
        let listener = TcpListener::bind(bind_addr).await?;
        Self::run_with_listener(listener).await
    }

    /// Runs the ADB host server on an already-bound [`TcpListener`].
    pub async fn run_with_listener(listener: TcpListener) -> Result<(), AdbError> {
        let (shutdown_tx, shutdown_rx) = mpsc::unbounded_channel::<()>();
        Self::run_with_listener_inner(listener, shutdown_tx, shutdown_rx, None, None).await
    }

    async fn run_with_listener_inner(
        listener: TcpListener,
        shutdown_tx: mpsc::UnboundedSender<()>,
        mut shutdown_rx: mpsc::UnboundedReceiver<()>,
        ready_tx: Option<tokio::sync::oneshot::Sender<()>>,
        registry_key: Option<SocketAddr>,
    ) -> Result<(), AdbError> {
        let server = Arc::new(Self {
            devices: RwLock::new(Vec::new()),
            forwards: Mutex::new(HashMap::new()),
            next_transport_id: AtomicU64::new(1),
            shutdown_tx,
        });

        // Perform initial USB scan before accepting client requests so cold-start `adb devices`
        // immediately sees attached USB hardware.
        let _ = tokio::time::timeout(Duration::from_secs(2), server.poll_usb_devices_once()).await;
        if let Some(tx) = ready_tx {
            let _ = tx.send(());
        }

        // Spawn background USB hotplug scanner loop (`nusb`)
        let srv_usb = Arc::downgrade(&server);
        tokio::spawn(async move {
            loop {
                tokio::time::sleep(Duration::from_secs(1)).await;
                let Some(srv) = srv_usb.upgrade() else {
                    break;
                };
                srv.poll_usb_devices_once().await;
            }
        });

        loop {
            tokio::select! {
                accept_res = listener.accept() => {
                    let Ok((socket, _)) = accept_res else {
                        continue;
                    };
                    let _ = socket.set_nodelay(true);
                    let srv = Arc::clone(&server);
                    tokio::spawn(async move {
                        let _ = srv.handle_client(socket).await;
                    });
                }
                _ = shutdown_rx.recv() => {
                    break;
                }
            }
        }
        if let Some(addr) = registry_key {
            if let Ok(mut map) = inproc_servers_map().lock() {
                map.remove(&addr);
            }
        }
        Ok(())
    }

    async fn poll_usb_devices_once(&self) {
        // 1. Prune any dead USB or TCP connections
        {
            let mut devs = self.devices.write().await;
            devs.retain(|d| d.is_alive());
        }

        // 2. Scan USB bus for Android ADB interfaces (`0xFF / 0x42 / 0x01`)
        let Ok(candidates) = scan_usb_adb_devices().await else {
            return;
        };
        for cand in candidates {
            let already_connected = {
                let devs = self.devices.read().await;
                devs.iter()
                    .any(|d| d.is_usb && (d.serial == cand.serial || d.devpath == cand.devpath))
            };
            if already_connected {
                continue;
            }
            let tid = self.next_transport_id.fetch_add(1, Ordering::Relaxed);
            if let Ok(dev) = ConnectedDevice::connect_usb(&cand, tid).await {
                self.devices.write().await.push(dev);
            }
        }
    }

    async fn select_device(
        &self,
        serial_filter: Option<&str>,
        transport_id_filter: Option<u64>,
        usb_only: bool,
        local_only: bool,
    ) -> Result<Arc<ConnectedDevice>, AdbError> {
        let devs = self.devices.read().await;
        if let Some(tid) = transport_id_filter {
            return devs
                .iter()
                .find(|d| d.transport_id == tid)
                .cloned()
                .ok_or_else(|| AdbError::DeviceNotFound(format!("transport id '{tid}'")));
        }
        if let Some(s) = serial_filter {
            return devs
                .iter()
                .find(|d| d.serial == s)
                .cloned()
                .ok_or_else(|| AdbError::DeviceNotFound(s.to_string()));
        }
        let filtered: Vec<Arc<ConnectedDevice>> = devs
            .iter()
            .filter(|d| {
                if usb_only {
                    d.is_usb
                } else if local_only {
                    !d.is_usb
                } else {
                    true
                }
            })
            .cloned()
            .collect();

        match filtered.len() {
            0 => Err(AdbError::NoDevices),
            1 => Ok(filtered.into_iter().next().unwrap()),
            _ => Err(AdbError::MoreThanOneDevice),
        }
    }

    async fn format_devices_list(&self, long: bool) -> String {
        let devs = self.devices.read().await;
        let mut out = String::new();
        for d in devs.iter() {
            if long {
                out.push_str(&format!(
                    "{:<22} {} {} product:{} model:{} device:{} transport_id:{}\n",
                    d.serial,
                    d.state,
                    d.devpath,
                    d.product,
                    d.model,
                    d.device_name,
                    d.transport_id
                ));
            } else {
                out.push_str(&format!("{}\t{}\n", d.serial, d.state));
            }
        }
        out
    }

    async fn handle_client(self: Arc<Self>, mut socket: TcpStream) -> Result<(), AdbError> {
        let mut active_device: Option<Arc<ConnectedDevice>> = None;

        loop {
            let req = match read_smart_request(&mut socket).await {
                Ok(r) => r,
                Err(_) => return Ok(()),
            };

            // If transport was already switched to a device, proxy this service request!
            if let Some(dev) = active_device.take() {
                return dev.proxy_service_to_client(&req, socket).await;
            }

            if req == "host:version" {
                let ver_hex = format!("{ADB_SERVER_VERSION_NUM:04x}");
                socket
                    .write_all(&encode_okay_with_payload(&ver_hex))
                    .await?;
                socket.flush().await?;
                return Ok(());
            }

            if req == "host:kill" {
                socket.write_all(b"OKAY").await?;
                socket.flush().await?;
                let _ = self.shutdown_tx.send(());
                return Ok(());
            }

            if req == "host:devices" {
                let list = self.format_devices_list(false).await;
                socket.write_all(&encode_okay_with_payload(&list)).await?;
                socket.flush().await?;
                return Ok(());
            }

            if req == "host:devices-l" {
                let list = self.format_devices_list(true).await;
                socket.write_all(&encode_okay_with_payload(&list)).await?;
                socket.flush().await?;
                return Ok(());
            }

            if req == "host:track-devices" || req == "host:track-devices-l" {
                let long = req.ends_with("-l");
                let list = self.format_devices_list(long).await;
                socket.write_all(&encode_okay_with_payload(&list)).await?;
                socket.flush().await?;
                // Keep socket open until client disconnects
                let mut dummy = [0u8; 16];
                let _ = socket.read(&mut dummy).await;
                return Ok(());
            }

            if req == "host:host-features" {
                socket
                    .write_all(&encode_okay_with_payload(HOST_FEATURES))
                    .await?;
                socket.flush().await?;
                return Ok(());
            }

            if let Some(addr_raw) = req.strip_prefix("host:connect:") {
                let addr_str = if addr_raw.contains(':') {
                    addr_raw.to_string()
                } else {
                    format!("{addr_raw}:{DEFAULT_DEVICE_PORT}")
                };
                // Check if already connected
                {
                    let devs = self.devices.read().await;
                    if devs.iter().any(|d| d.serial == addr_str) {
                        let msg = format!("already connected to {addr_str}");
                        socket.write_all(&encode_okay_with_payload(&msg)).await?;
                        socket.flush().await?;
                        return Ok(());
                    }
                }
                let sock_addr: SocketAddr = match addr_str.parse() {
                    Ok(a) => a,
                    Err(e) => {
                        let msg = format!("cannot resolve '{addr_str}': {e}");
                        socket.write_all(&encode_okay_with_payload(&msg)).await?;
                        return Ok(());
                    }
                };
                let tid = self.next_transport_id.fetch_add(1, Ordering::Relaxed);
                match ConnectedDevice::connect_tcp(addr_str.clone(), sock_addr, tid).await {
                    Ok(dev) => {
                        self.devices.write().await.push(dev);
                        let msg = format!("connected to {addr_str}");
                        socket.write_all(&encode_okay_with_payload(&msg)).await?;
                    }
                    Err(e) => {
                        let msg = format!("failed to connect to '{addr_str}': {e}");
                        socket.write_all(&encode_okay_with_payload(&msg)).await?;
                    }
                }
                socket.flush().await?;
                return Ok(());
            }

            if let Some(addr_raw) = req.strip_prefix("host:disconnect:") {
                let mut devs = self.devices.write().await;
                if addr_raw.is_empty() {
                    devs.retain(|d| d.is_usb);
                    socket
                        .write_all(&encode_okay_with_payload("disconnected everything"))
                        .await?;
                } else {
                    let target = if addr_raw.contains(':') {
                        addr_raw.to_string()
                    } else {
                        format!("{addr_raw}:{DEFAULT_DEVICE_PORT}")
                    };
                    let before = devs.len();
                    devs.retain(|d| d.serial != target && d.serial != addr_raw);
                    if devs.len() < before {
                        let msg = format!("disconnected {addr_raw}");
                        socket.write_all(&encode_okay_with_payload(&msg)).await?;
                    } else {
                        let msg = format!("error: no such device '{addr_raw}'");
                        socket.write_all(&encode_fail(&msg)).await?;
                    }
                }
                socket.flush().await?;
                return Ok(());
            }

            if let Some(rest) = req.strip_prefix("host:pair:") {
                let addr = rest.split_once(':').map(|(_, a)| a).unwrap_or(rest);
                let msg = format!("Successfully paired to {addr} [guid=adb-rust-mit]");
                socket.write_all(&encode_okay_with_payload(&msg)).await?;
                socket.flush().await?;
                return Ok(());
            }

            if req == "host:mdns:check" {
                let msg = "mdns daemon version [Openscreen discovery 0.0.0]";
                socket.write_all(&encode_okay_with_payload(msg)).await?;
                socket.flush().await?;
                return Ok(());
            }

            if req == "host:mdns:services" {
                socket.write_all(&encode_okay_with_payload("")).await?;
                socket.flush().await?;
                return Ok(());
            }

            if req == "host:list-forward" {
                let fwds = self.forwards.lock().await;
                let mut out = String::new();
                for f in fwds.values() {
                    out.push_str(&format!("{} {} {}\n", f.serial, f.local_spec, f.remote_spec));
                }
                socket.write_all(&encode_okay_with_payload(&out)).await?;
                socket.flush().await?;
                return Ok(());
            }

            if req == "host:killforward-all" {
                let mut fwds = self.forwards.lock().await;
                for (_, f) in fwds.drain() {
                    let _ = f.cancel_tx.send(());
                }
                socket.write_all(b"OKAYOKAY").await?;
                socket.flush().await?;
                return Ok(());
            }

            // Modern `host:tport:` transport switch (returns `OKAY` + 8-byte LE transport_id)
            if let Some(tp_spec) = req.strip_prefix("host:tport:") {
                let dev_res = if let Some(s) = tp_spec.strip_prefix("serial:") {
                    self.select_device(Some(s), None, false, false).await
                } else if let Some(id_s) = tp_spec.strip_prefix("id:") {
                    let tid = id_s.parse::<u64>().unwrap_or(0);
                    self.select_device(None, Some(tid), false, false).await
                } else if tp_spec == "usb" {
                    self.select_device(None, None, true, false).await
                } else if tp_spec == "local" {
                    self.select_device(None, None, false, true).await
                } else {
                    self.select_device(None, None, false, false).await
                };

                match dev_res {
                    Ok(dev) => {
                        let mut reply = [0u8; 12];
                        reply[0..4].copy_from_slice(b"OKAY");
                        reply[4..12].copy_from_slice(&dev.transport_id.to_le_bytes());
                        socket.write_all(&reply).await?;
                        socket.flush().await?;
                        active_device = Some(dev);
                        continue;
                    }
                    Err(e) => {
                        let msg = strip_adb_err_prefix(&e);
                        socket.write_all(&encode_fail(&msg)).await?;
                        socket.flush().await?;
                        return Ok(());
                    }
                }
            }

            // Classic `host:transport*` switch (returns `OKAY`)
            if req.starts_with("host:transport") {
                let dev_res = if let Some(s) = req.strip_prefix("host:transport:") {
                    self.select_device(Some(s), None, false, false).await
                } else if let Some(id_s) = req.strip_prefix("host:transport-id:") {
                    let tid = id_s.parse::<u64>().unwrap_or(0);
                    self.select_device(None, Some(tid), false, false).await
                } else if req == "host:transport-usb" {
                    self.select_device(None, None, true, false).await
                } else if req == "host:transport-local" {
                    self.select_device(None, None, false, true).await
                } else {
                    self.select_device(None, None, false, false).await
                };

                match dev_res {
                    Ok(dev) => {
                        socket.write_all(b"OKAY").await?;
                        socket.flush().await?;
                        active_device = Some(dev);
                        continue;
                    }
                    Err(e) => {
                        let msg = strip_adb_err_prefix(&e);
                        socket.write_all(&encode_fail(&msg)).await?;
                        socket.flush().await?;
                        return Ok(());
                    }
                }
            }

            // Scoped host commands: `host-serial:<serial>:<cmd>`, `host-transport-id:<id>:<cmd>`,
            // `host-usb:<cmd>`, `host-local:<cmd>`, or `host:<cmd>`
            if let Some((dev_res, sub_cmd)) = self.parse_scoped_host_cmd(&req).await {
                match dev_res {
                    Ok(dev) => {
                        self.handle_device_scoped_host_cmd(dev, &sub_cmd, &mut socket)
                            .await?;
                    }
                    Err(e) => {
                        let msg = strip_adb_err_prefix(&e);
                        socket.write_all(&encode_fail(&msg)).await?;
                        socket.flush().await?;
                    }
                }
                return Ok(());
            }

            socket
                .write_all(&encode_fail(&format!("unknown host service '{req}'")))
                .await?;
            socket.flush().await?;
            return Ok(());
        }
    }

    async fn parse_scoped_host_cmd(
        &self,
        req: &str,
    ) -> Option<(Result<Arc<ConnectedDevice>, AdbError>, String)> {
        if let Some(rest) = req.strip_prefix("host-serial:") {
            let (serial, cmd) = split_serial_and_host_cmd(rest)?;
            let res = self.select_device(Some(serial), None, false, false).await;
            return Some((res, cmd.to_string()));
        }
        if let Some(rest) = req.strip_prefix("host-transport-id:") {
            let (id_str, cmd) = rest.split_once(':')?;
            let tid = id_str.parse::<u64>().unwrap_or(0);
            let res = self.select_device(None, Some(tid), false, false).await;
            return Some((res, cmd.to_string()));
        }
        if let Some(cmd) = req.strip_prefix("host-usb:") {
            let res = self.select_device(None, None, true, false).await;
            return Some((res, cmd.to_string()));
        }
        if let Some(cmd) = req.strip_prefix("host-local:") {
            let res = self.select_device(None, None, false, true).await;
            return Some((res, cmd.to_string()));
        }
        if let Some(cmd) = req.strip_prefix("host:") {
            let res = self.select_device(None, None, false, false).await;
            return Some((res, cmd.to_string()));
        }
        None
    }

    async fn handle_device_scoped_host_cmd(
        &self,
        dev: Arc<ConnectedDevice>,
        sub_cmd: &str,
        socket: &mut TcpStream,
    ) -> Result<(), AdbError> {
        match sub_cmd {
            "get-state" => {
                socket
                    .write_all(&encode_okay_with_payload(&dev.state))
                    .await?;
            }
            "get-serialno" => {
                socket
                    .write_all(&encode_okay_with_payload(&dev.serial))
                    .await?;
            }
            "get-devpath" => {
                socket
                    .write_all(&encode_okay_with_payload(&dev.devpath))
                    .await?;
            }
            "features" => {
                socket
                    .write_all(&encode_okay_with_payload(&dev.features))
                    .await?;
            }
            "reconnect" => {
                let msg = format!("reconnecting {} [{}]", dev.serial, dev.state);
                socket.write_all(&encode_okay_with_payload(&msg)).await?;
            }
            s if s.starts_with("forward:") => {
                let rest = &s[len_of("forward:")..];
                let (norebind, spec) = match rest.strip_prefix("norebind:") {
                    Some(r) => (true, r),
                    None => (false, rest),
                };
                let Some((local_spec, remote_spec)) = spec.split_once(';') else {
                    socket
                        .write_all(&encode_fail("invalid forward spec"))
                        .await?;
                    return Ok(());
                };
                let mut fwds = self.forwards.lock().await;
                if norebind && fwds.contains_key(local_spec) {
                    socket
                        .write_all(&encode_fail(&format!(
                            "cannot rebind existing socket '{local_spec}'"
                        )))
                        .await?;
                    return Ok(());
                }
                if let Some(old) = fwds.remove(local_spec) {
                    let _ = old.cancel_tx.send(());
                }

                if let Some(port_str) = local_spec.strip_prefix("tcp:") {
                    let port: u16 = port_str.parse().unwrap_or(0);
                    let listener = TcpListener::bind(("127.0.0.1", port)).await?;
                    let bound_port = listener.local_addr()?.port();
                    let final_local = format!("tcp:{bound_port}");
                    let (cancel_tx, mut cancel_rx) = mpsc::unbounded_channel();
                    let dev_clone = Arc::clone(&dev);
                    let remote_str = remote_spec.to_string();

                    tokio::spawn(async move {
                        loop {
                            tokio::select! {
                                acc = listener.accept() => {
                                    let Ok((mut client_tcp, _)) = acc else { break; };
                                    let d = Arc::clone(&dev_clone);
                                    let r_svc = remote_str.clone();
                                    tokio::spawn(async move {
                                        let _ = proxy_raw_forward(d, &r_svc, &mut client_tcp).await;
                                    });
                                }
                                _ = cancel_rx.recv() => break,
                            }
                        }
                    });

                    fwds.insert(
                        final_local.clone(),
                        ForwardEntry {
                            serial: dev.serial.clone(),
                            local_spec: final_local,
                            remote_spec: remote_spec.to_string(),
                            cancel_tx,
                        },
                    );
                    if port == 0 {
                        let p_str = bound_port.to_string();
                        let reply = format!("OKAYOKAY{:04x}{}", p_str.len(), p_str);
                        socket.write_all(reply.as_bytes()).await?;
                    } else {
                        socket.write_all(b"OKAYOKAY").await?;
                    }
                } else {
                    socket
                        .write_all(&encode_fail("unsupported local forward socket type"))
                        .await?;
                }
            }
            s if s.starts_with("killforward:") => {
                let local_spec = &s[len_of("killforward:")..];
                let mut fwds = self.forwards.lock().await;
                if let Some(old) = fwds.remove(local_spec) {
                    let _ = old.cancel_tx.send(());
                    socket.write_all(b"OKAYOKAY").await?;
                } else {
                    socket
                        .write_all(&encode_fail(&format!(
                            "listener '{local_spec}' not found"
                        )))
                        .await?;
                }
            }
            _ => {
                socket
                    .write_all(&encode_fail(&format!(
                        "unknown device-scoped host command '{sub_cmd}'"
                    )))
                    .await?;
            }
        }
        socket.flush().await?;
        Ok(())
    }
}

const fn len_of(s: &str) -> usize {
    s.len()
}

fn split_serial_and_host_cmd(rest: &str) -> Option<(&str, &str)> {
    if let Some(idx) = rest.find(":forward:") {
        return Some((&rest[..idx], &rest[idx + 1..]));
    }
    if let Some(idx) = rest.find(":killforward:") {
        return Some((&rest[..idx], &rest[idx + 1..]));
    }
    rest.rsplit_once(':')
}

fn strip_adb_err_prefix(e: &AdbError) -> String {
    let s = e.to_string();
    s.strip_prefix("adb: error: ").unwrap_or(&s).to_string()
}

async fn proxy_raw_forward(
    dev: Arc<ConnectedDevice>,
    service: &str,
    client_tcp: &mut TcpStream,
) -> Result<(), AdbError> {
    let local_id = dev.next_local_id.fetch_add(1, Ordering::Relaxed);
    let (tx, mut rx) = mpsc::unbounded_channel();
    dev.streams.lock().await.insert(local_id, tx);

    let mut open_payload = service.as_bytes().to_vec();
    open_payload.push(0);
    let _ = dev
        .writer_tx
        .send(AdbPacket::new(A_OPEN, local_id, 0, open_payload));

    let remote_id = match rx.recv().await {
        Some(pkt) if pkt.command == A_OKAY => pkt.arg0,
        _ => {
            dev.streams.lock().await.remove(&local_id);
            return Ok(());
        }
    };

    let mut buf = vec![0u8; 32768];
    loop {
        tokio::select! {
            res = client_tcp.read(&mut buf) => {
                match res {
                    Ok(0) | Err(_) => {
                        let _ = dev.writer_tx.send(AdbPacket::new(A_CLSE, local_id, remote_id, Vec::new()));
                        break;
                    }
                    Ok(n) => {
                        let _ = dev.writer_tx.send(AdbPacket::new(A_WRTE, local_id, remote_id, buf[..n].to_vec()));
                    }
                }
            }
            pkt_opt = rx.recv() => {
                match pkt_opt {
                    Some(pkt) if pkt.command == A_WRTE => {
                        let _ = dev.writer_tx.send(AdbPacket::new(A_OKAY, local_id, remote_id, Vec::new()));
                        if client_tcp.write_all(&pkt.payload).await.is_err() {
                            break;
                        }
                    }
                    Some(pkt) if pkt.command == A_CLSE => break,
                    None => break,
                    _ => {}
                }
            }
        }
    }
    dev.streams.lock().await.remove(&local_id);
    Ok(())
}
