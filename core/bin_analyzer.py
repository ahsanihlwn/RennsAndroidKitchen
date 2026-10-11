#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0


import os
import re
import math
import hashlib
import struct
from typing import Optional, List, Dict, Tuple, Any

_MAX_READ_HEADER = 65536


def calc_entropy(data: bytes) -> float:

    if not data:
        return 0.0
    dlen = len(data)
    entropy = 0.0
    for x in range(256):
        count = data.count(bytes([x]))
        if count > 0:
            p_x = count / dlen
            entropy -= p_x * math.log2(p_x)
    return round(entropy, 2)


def get_entropy_label(entropy: float) -> Tuple[str, str]:

    if entropy < 1.0:
        return "Low / Zero-Padded (Sparse / Blank)", "#4CAF50"
    elif entropy < 5.0:
        return "Low / Repetitive Waveforms / Tables", "#8BC34A"
    elif entropy < 7.2:
        return "Normal / Uncompressed Machine Code / Text", "#2196F3"
    elif entropy < 7.7:
        return "High / Compressed Firmware / Packed Data", "#FF9800"
    else:
        return "Very High / Encrypted or Highly Compressed", "#F44336"


def calc_hashes(filepath: str, max_bytes: Optional[int] = None) -> Dict[str, str]:

    md5 = hashlib.md5()
    sha1 = hashlib.sha1()
    sha256 = hashlib.sha256()

    total_read = 0
    with open(filepath, "rb") as f:
        while True:
            chunk_size = 65536
            if max_bytes is not None:
                remaining = max_bytes - total_read
                if remaining <= 0:
                    break
                chunk_size = min(chunk_size, remaining)

            chunk = f.read(chunk_size)
            if not chunk:
                break
            total_read += len(chunk)
            md5.update(chunk)
            sha1.update(chunk)
            sha256.update(chunk)

    return {
        "md5": md5.hexdigest(),
        "sha1": sha1.hexdigest(),
        "sha256": sha256.hexdigest(),
        "read_bytes": str(total_read)
    }


def detect_binary_format(filepath: str, header_data: Optional[bytes] = None) -> Dict[str, Any]:

    fname = os.path.basename(filepath)
    fname_lower = fname.lower()
    file_size = os.path.getsize(filepath) if os.path.exists(filepath) else 0

    if header_data is None:
        try:
            with open(filepath, "rb") as f:
                header_data = f.read(_MAX_READ_HEADER)
        except Exception:
            header_data = b""

    dlen = len(header_data)
    info = {
        "name": fname,
        "size": file_size,
        "format": "Generic Binary",
        "vendor": "Generic / Unknown",
        "category": "Binary Blob",
        "description": "Raw binary file without recognized signature.",
        "extra": {}
    }

    if dlen < 4:
        info["description"] = "Empty or too small binary file."
        return info

    # ── 1. Android OTA Payload (CrAU) ──
    if header_data[:4] == b"CrAU":
        info["format"] = "Android OTA Payload"
        info["vendor"] = "Google / AOSP"
        info["category"] = "OTA Package"
        info["description"] = "Android OTA Delta Payload archive containing partition images."
        return info

    # ── 2. ELF (Executable and Linkable Format) ──
    if header_data[:4] == b"\x7FELF":
        is_64 = header_data[4] == 2 if dlen > 4 else False
        endian = "Little-Endian" if (dlen > 5 and header_data[5] == 1) else "Big-Endian"
        info["format"] = f"ELF {'64-bit' if is_64 else '32-bit'}"
        info["vendor"] = "Standard ELF"
        info["category"] = "Executable / Library / Firmware"
        info["description"] = f"ELF binary format ({endian})."
        info["extra"]["BitWidth"] = "64-bit" if is_64 else "32-bit"
        info["extra"]["Endian"] = endian
        return info

    # ── 3. Device Tree Blob (DTB / DTBO) ──
    if header_data[:4] == b"\xD0\x0D\xFE\xED":
        info["format"] = "Device Tree Blob (DTB)"
        info["vendor"] = "Linux Kernel / Device Tree"
        info["category"] = "Hardware Device Tree"
        info["description"] = "Compiled Flattened Device Tree binary."
        return info

    # ── 4. Android Boot & Vendor Boot Images ──
    if header_data.startswith(b"ANDROID!"):
        info["format"] = "Android Boot Image"
        info["vendor"] = "Google / AOSP"
        info["category"] = "Boot Image"
        info["description"] = "Android kernel boot image (boot.img, recovery.img, etc.)."
        return info
    if header_data.startswith(b"VNDRBOOT"):
        info["format"] = "Android Vendor Boot Image"
        info["vendor"] = "Google / AOSP"
        info["category"] = "Boot Image"
        info["description"] = "Android 11+ vendor_boot partition image."
        return info

    # ── 5. Android Sparse Image ──
    if dlen >= 4 and struct.unpack_from("<I", header_data, 0)[0] == 0xED26FF3A:
        info["format"] = "Android Sparse Image"
        info["vendor"] = "Google / AOSP"
        info["category"] = "Disk Image"
        info["description"] = "Android sparse filesystem image."
        return info

    # ── 6. MediaTek Connectivity / RAM Code Blobs ──
    # Check for timestamp pattern (e.g. 20240715113201..ALPS)
    if re.match(rb"^\d{14}\x00\x00ALPS", header_data):
        ts = header_data[:14].decode("ascii", errors="ignore")
        info["format"] = "MediaTek RAM Code / Patch Blob (ALPS)"
        info["vendor"] = "MediaTek (MTK)"
        info["category"] = "Connectivity Firmware"
        info["description"] = f"MediaTek hardware RAM microcode with ALPS build timestamp: {ts}."
        info["extra"]["BuildTimestamp"] = ts
        info["extra"]["Signature"] = "ALPS"
        return info
    elif re.match(rb"^\d{16}", header_data) and ("ram" in fname_lower or "wifi" in fname_lower or "bt" in fname_lower):
        ts = header_data[:16].decode("ascii", errors="ignore")
        info["format"] = "MediaTek RAM Code Blob"
        info["vendor"] = "MediaTek (MTK)"
        info["category"] = "Connectivity Firmware"
        info["description"] = f"MediaTek subsystem RAM microcode (Header timestamp: {ts})."
        info["extra"]["BuildTimestamp"] = ts
        return info
    elif fname_lower.startswith("wifi_ram_code") or "soc2_2_ram" in fname_lower:
        info["format"] = "MediaTek Wi-Fi/BT RAM Code"
        info["vendor"] = "MediaTek (MTK)"
        info["category"] = "Connectivity Firmware"
        info["description"] = "MediaTek wireless subsystem RAM microcode."
        return info

    # ── 7. MediaTek FM Radio DSP Coeffs & Patches ──
    if fname_lower.startswith("mt66") and ("_coeff" in fname_lower or "_patch" in fname_lower):
        info["format"] = "MediaTek FM Radio DSP Patch/Coeff"
        info["vendor"] = "MediaTek (MTK)"
        info["category"] = "DSP Firmware"
        info["description"] = "MediaTek FM Radio DSP filter coefficients & microcode patch."
        return info

    # ── 8. Goodix Touchscreen Controller Firmware & Config ──
    if "gt9886" in fname_lower or "gt9896" in fname_lower or "goodix" in fname_lower:
        is_cfg = "_cfg" in fname_lower
        chip = "GT9886 / GT9896"
        if "gt9886" in fname_lower:
            chip = "Goodix GT9886"
        elif "gt9896" in fname_lower:
            chip = "Goodix GT9896s"

        vendor_tag = ""
        if b"normandy" in header_data:
            vendor_tag = "normandy"
        elif b"YELSTO" in header_data:
            vendor_tag = "YELSTO"

        info["format"] = f"{chip} {'Configuration' if is_cfg else 'Firmware'}"
        info["vendor"] = "Goodix Technology"
        info["category"] = "Touchscreen Controller"
        info["description"] = f"{'Touchscreen register configuration' if is_cfg else 'Touchscreen controller microcode'} for {chip}."
        if vendor_tag:
            info["extra"]["ChipCodename"] = vendor_tag
        return info

    # ── 9. Novatek Touchscreen Firmware ──
    if fname_lower.startswith("novatek_ts") or "nvt_ts" in fname_lower:
        info["format"] = "Novatek Touchscreen Firmware"
        info["vendor"] = "Novatek Microelectronics"
        info["category"] = "Touchscreen Controller"
        info["description"] = "Microcode binary for Novatek touchscreen IC."
        return info

    # ── 10. Focaltech Touchscreen Firmware ──
    if fname_lower.startswith("focaltech_ts") or "ft_ts" in fname_lower:
        info["format"] = "FocalTech Touchscreen Firmware"
        info["vendor"] = "FocalTech Systems"
        info["category"] = "Touchscreen Controller"
        info["description"] = "Microcode binary for FocalTech touchscreen IC."
        return info

    # ── 11. Awinic Haptics / Vibration Waveforms ──
    if fname_lower.startswith("aw86") or "awinic" in fname_lower:
        is_rtp = "rtp" in fname_lower
        is_osc = "osc" in fname_lower
        subtype = "Oscillator RTP" if is_osc else ("RTP Waveform" if is_rtp else "Haptic Config")
        info["format"] = f"Awinic Haptic {subtype}"
        info["vendor"] = "Awinic Technology"
        info["category"] = "Haptic Driver Waveform"
        info["description"] = f"Vibration waveform pattern and calibration data for Awinic Linear Haptic Driver."
        return info

    # ── 12. FourSemi Smart PA Audio Firmware ──
    if fname_lower.endswith(".fsm") or "fs15" in fname_lower or "foursemi" in fname_lower:
        info["format"] = "FourSemi Smart PA Firmware"
        info["vendor"] = "FourSemi / Everest Semiconductor"
        info["category"] = "Audio Smart PA"
        info["description"] = "Audio Smart Power Amplifier state machine & protection firmware."
        return info

    # ── 13. Check for text/config file masquerading as binary ──
    try:
        printable = sum(1 for b in header_data if 32 <= b <= 126 or b in (9, 10, 13))
        if dlen > 0 and (printable / dlen) > 0.90:
            info["format"] = "Plaintext Configuration"
            info["vendor"] = "Plaintext / Config"
            info["category"] = "Text File"
            info["description"] = "File consists mostly of printable ASCII characters."
            return info
    except Exception:
        pass

    return info


def extract_strings(
    filepath: str,
    min_len: int = 4,
    max_strings: int = 15000,
    max_bytes: int = 10 * 1024 * 1024
) -> List[Dict[str, Any]]:

    if not os.path.exists(filepath):
        return []

    file_size = os.path.getsize(filepath)
    read_len = min(file_size, max_bytes)

    with open(filepath, "rb") as f:
        data = f.read(read_len)

    results = []

    # ASCII strings regex
    ascii_pat = re.compile(rb"[\x20-\x7E]{" + str(min_len).encode() + rb",}")
    for m in ascii_pat.finditer(data):
        text = m.group().decode("ascii", errors="ignore")
        results.append({
            "offset": m.start(),
            "encoding": "ASCII",
            "length": len(text),
            "text": text
        })
        if len(results) >= max_strings:
            break

    # UTF-16LE strings regex (if not reached max)
    if len(results) < max_strings:
        utf16_pat = re.compile(rb"(?:[\x20-\x7E]\x00){" + str(min_len).encode() + rb",}")
        for m in utf16_pat.finditer(data):
            try:
                text = m.group().decode("utf-16le", errors="ignore")
                results.append({
                    "offset": m.start(),
                    "encoding": "UTF-16LE",
                    "length": len(text),
                    "text": text
                })
                if len(results) >= max_strings:
                    break
            except Exception:
                continue

    results.sort(key=lambda item: item["offset"])
    return results


def read_hex_chunk(filepath: str, offset: int, length: int) -> bytes:

    if not os.path.exists(filepath) or offset < 0 or length <= 0:
        return b""
    try:
        with open(filepath, "rb") as f:
            f.seek(offset)
            return f.read(length)
    except Exception:
        return b""


def format_hex_lines(data: bytes, base_offset: int = 0) -> List[Dict[str, Any]]:

    lines = []
    total_len = len(data)
    for i in range(0, total_len, 16):
        chunk = data[i:i + 16]
        cur_offset = base_offset + i
        offset_str = f"{cur_offset:08X}"

        hex_parts = []
        for idx, b in enumerate(chunk):
            hex_parts.append(f"{b:02X}")
            if idx == 7 and len(chunk) > 8:
                hex_parts.append("")  # Visual gap between byte 7 and 8

        # Pad remaining hex slots if less than 16 bytes
        pad_count = 16 - len(chunk)
        hex_content = " ".join(hex_parts)
        if pad_count > 0:
            if len(chunk) <= 8:
                hex_content += " "  # extra space for gap
            hex_content += "   " * pad_count

        ascii_chars = []
        for b in chunk:
            if 32 <= b <= 126:
                ascii_chars.append(chr(b))
            else:
                ascii_chars.append("·")
        ascii_content = "".join(ascii_chars)

        lines.append({
            "offset_str": offset_str,
            "hex_str": hex_content,
            "ascii_str": ascii_content,
            "offset_int": cur_offset,
            "length": len(chunk)
        })

    return lines
