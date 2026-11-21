//! Native Pure-Rust USB transport (`nusb`) for Android `adbd` devices (MIT).
//!
//! Matches USB interfaces with `class == 0xFF`, `subclass == 0x42`, `protocol == 0x01`
//! and implements the ADB USB bulk transfer protocol (separate 24-byte `apacket` header
//! and payload bulk transfers, plus Zero-Length Packet termination on packet-boundary multiples).

use nusb::descriptors::TransferType;
use nusb::transfer::{Buffer, Bulk, Direction, In, Out};
use nusb::{DeviceInfo, Endpoint, Interface};

use crate::protocol::{AdbError, AdbPacket, APACKET_HEADER_SIZE, MAX_PAYLOAD};

/// USB interface class for Android ADB (`0xFF` vendor-specific).
pub const ADB_USB_CLASS: u8 = 0xFF;
/// USB interface subclass for Android ADB (`0x42`).
pub const ADB_USB_SUBCLASS: u8 = 0x42;
/// USB interface protocol for Android ADB (`0x01`, whereas `0x03` is Fastboot).
pub const ADB_USB_PROTOCOL: u8 = 0x01;

/// Metadata for a discovered USB `adbd` device.
#[derive(Debug, Clone)]
pub struct UsbAdbDeviceCandidate {
    pub info: DeviceInfo,
    pub interface_number: u8,
    pub serial: String,
    pub devpath: String,
}

/// Finds the ADB interface number on a USB device if it exposes `0xFF / 0x42 / 0x01`.
pub fn find_adb_interface(info: &DeviceInfo) -> Option<u8> {
    info.interfaces().find_map(|i| {
        if i.class() == ADB_USB_CLASS
            && i.subclass() == ADB_USB_SUBCLASS
            && i.protocol() == ADB_USB_PROTOCOL
        {
            Some(i.interface_number())
        } else {
            None
        }
    })
}

/// Scans the host USB bus via `nusb` for all currently attached Android ADB devices.
pub async fn scan_usb_adb_devices() -> Result<Vec<UsbAdbDeviceCandidate>, AdbError> {
    let devs = nusb::list_devices()
        .await
        .map_err(|e| AdbError::Usb(e.to_string()))?;
    let mut out = Vec::new();
    for info in devs {
        if let Some(interface_number) = find_adb_interface(&info) {
            let serial = info
                .serial_number()
                .map(str::to_string)
                .filter(|s| !s.is_empty())
                .unwrap_or_else(|| {
                    format!(
                        "usb-{:04x}-{:04x}-{}",
                        info.vendor_id(),
                        info.product_id(),
                        info.device_address()
                    )
                });
            let devpath = format!(
                "usb:{:04x}:{:04x}:{}",
                info.vendor_id(),
                info.product_id(),
                info.device_address()
            );
            out.push(UsbAdbDeviceCandidate {
                info,
                interface_number,
                serial,
                devpath,
            });
        }
    }
    Ok(out)
}

/// Active USB bulk endpoint pair (`Endpoint<Bulk, Out>` and `Endpoint<Bulk, In>`) for an `adbd` device.
pub struct UsbAdbTransport {
    pub ep_out: Endpoint<Bulk, Out>,
    pub max_out: usize,
    pub ep_in: Endpoint<Bulk, In>,
    pub max_in: usize,
}

impl UsbAdbTransport {
    /// Opens the USB device, claims the ADB interface, and extracts its bulk `IN` and `OUT` endpoints.
    pub async fn open(candidate: &UsbAdbDeviceCandidate) -> Result<Self, AdbError> {
        let dev = candidate
            .info
            .open()
            .await
            .map_err(|e| AdbError::Usb(format!("open USB device failed: {e}")))?;
        let interface = dev
            .claim_interface(candidate.interface_number)
            .await
            .map_err(|e| AdbError::Usb(format!("claim ADB USB interface failed: {e}")))?;
        Self::from_interface(interface)
    }

    fn from_interface(interface: Interface) -> Result<Self, AdbError> {
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
            .ok_or_else(|| AdbError::Usb("missing bulk IN/OUT endpoints on ADB interface".into()))?;

        let ep_out = interface
            .endpoint::<Bulk, Out>(ep_out_addr)
            .map_err(|e| AdbError::Usb(e.to_string()))?;
        let ep_in = interface
            .endpoint::<Bulk, In>(ep_in_addr)
            .map_err(|e| AdbError::Usb(e.to_string()))?;

        Ok(Self {
            ep_out,
            max_out: max_out.max(64),
            ep_in,
            max_in: max_in.max(64),
        })
    }
}

/// Writes an [`AdbPacket`] to a USB bulk `OUT` endpoint using the AOSP two-transfer rule
/// (24-byte header transfer followed by payload transfer + ZLP if aligned to `max_out`).
pub async fn write_usb_packet(
    ep_out: &mut Endpoint<Bulk, Out>,
    max_out: usize,
    pkt: &AdbPacket,
) -> Result<(), AdbError> {
    let hdr = pkt.encode_header();
    ep_out.submit(hdr.to_vec().into());
    ep_out
        .next_complete()
        .await
        .into_result()
        .map_err(|e| AdbError::Usb(format!("USB header write error: {e}")))?;

    if !pkt.payload.is_empty() {
        ep_out.submit(pkt.payload.clone().into());
        ep_out
            .next_complete()
            .await
            .into_result()
            .map_err(|e| AdbError::Usb(format!("USB payload write error: {e}")))?;

        if max_out > 0 && pkt.payload.len() % max_out == 0 {
            ep_out.submit(Vec::<u8>::new().into());
            let _ = ep_out.next_complete().await.into_result();
        }
    }
    Ok(())
}

/// Reads an [`AdbPacket`] from a USB bulk `IN` endpoint (24-byte header transfer followed by
/// `data_length` payload bytes).
pub async fn read_usb_packet(
    ep_in: &mut Endpoint<Bulk, In>,
    max_in: usize,
) -> Result<AdbPacket, AdbError> {
    let hdr_bytes = loop {
        ep_in.submit(Buffer::new(max_in.max(APACKET_HEADER_SIZE)));
        let buf = ep_in
            .next_complete()
            .await
            .into_result()
            .map_err(|e| AdbError::Usb(format!("USB header read error: {e}")))?;
        if buf.is_empty() {
            continue;
        }
        if buf.len() < APACKET_HEADER_SIZE {
            return Err(AdbError::Protocol(format!(
                "short USB apacket header ({} bytes)",
                buf.len()
            )));
        }
        break buf;
    };

    let command = u32::from_le_bytes(hdr_bytes[0..4].try_into().unwrap());
    let arg0 = u32::from_le_bytes(hdr_bytes[4..8].try_into().unwrap());
    let arg1 = u32::from_le_bytes(hdr_bytes[8..12].try_into().unwrap());
    let data_len = u32::from_le_bytes(hdr_bytes[12..16].try_into().unwrap()) as usize;
    let magic = u32::from_le_bytes(hdr_bytes[20..24].try_into().unwrap());

    if magic != (command ^ 0xFFFF_FFFF) {
        return Err(AdbError::Protocol(format!(
            "bad USB apacket magic 0x{magic:08x} for command 0x{command:08x}"
        )));
    }
    if data_len > (MAX_PAYLOAD as usize) * 4 {
        return Err(AdbError::Protocol(format!(
            "USB apacket payload too large ({data_len})"
        )));
    }

    let mut payload = Vec::with_capacity(data_len);
    if hdr_bytes.len() > APACKET_HEADER_SIZE {
        let extra = &hdr_bytes[APACKET_HEADER_SIZE..];
        let take = extra.len().min(data_len);
        payload.extend_from_slice(&extra[..take]);
    }

    while payload.len() < data_len {
        let rem = data_len - payload.len();
        let alloc_sz = rem.next_multiple_of(max_in.max(64));
        ep_in.submit(Buffer::new(alloc_sz));
        let chunk = ep_in
            .next_complete()
            .await
            .into_result()
            .map_err(|e| AdbError::Usb(format!("USB payload read error: {e}")))?;
        if chunk.is_empty() {
            break;
        }
        let take = chunk.len().min(rem);
        payload.extend_from_slice(&chunk[..take]);
    }

    Ok(AdbPacket {
        command,
        arg0,
        arg1,
        payload,
    })
}
