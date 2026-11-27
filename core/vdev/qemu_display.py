# SPDX-License-Identifier: MPL-2.0
"""Embedded QEMU Display Engine (Pure-Python RFB/VNC Client + Early-Boot Phone Renderer + QQuickImageProvider).

Connects over localhost TCP (127.0.0.1) to QEMU's `-vnc` display server without linking any GPL code.
Provides:
- Live RGB framebuffer QImage streaming into QtQuick (`image://qemu_screen/frame?<seq>`)
- Direct mouse/touch PointerEvent forwarding from the embedded phone screen
- Android hardware/nav KeyEvent forwarding (Back, Home, Recents, Power, Vol+, Vol-)
- Crisp Early-Boot Console screen rendering before VirtIO-GPU draws its first frame
"""
from __future__ import annotations

import socket
import struct
import threading
import time

from PySide6.QtCore import Qt, QRect, QSize
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPen
from PySide6.QtQuick import QQuickImageProvider


# X11 keysyms understood by QEMU's RFB/VNC input backend
_ANDROID_KEYSYMS = {
    "back": 0xFF1B,      # Escape -> Android BACK
    "home": 0xFF50,      # Home -> Android HOME
    "recents": 0xFF67,   # Menu / App Switch
    "power": 0x1008FF2A, # XF86PowerOff (fallback: F7 / End)
    "vol_up": 0x1008FF13,
    "vol_down": 0x1008FF11,
    "enter": 0xFF0D,
}


class QemuDisplayHub:
    """Thread-safe singleton/shared framebuffer backing `image://qemu_screen/...`."""

    def __init__(self):
        self._lock = threading.Lock()
        self._image: QImage | None = None
        self._has_live_gpu = False
        self._fb_w = 360
        self._fb_h = 720
        self._rfb: RfbClient | None = None

    def _render_idle_screen(self) -> QImage:
        img = QImage(360, 720, QImage.Format.Format_ARGB32)
        img.fill(QColor("#0B0F17"))
        from PySide6.QtGui import QGuiApplication
        if QGuiApplication.instance() is None:
            return img
        p = QPainter(img)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setPen(QColor("#38BDF8"))
        p.setFont(QFont("Segoe UI", 13, QFont.Weight.Bold))
        p.drawText(QRect(20, 282, 320, 32), Qt.AlignmentFlag.AlignCenter, "RAK Virtual Device")
        p.setPen(QColor("#FBBF24"))
        p.setFont(QFont("Consolas", 8, QFont.Weight.Bold))
        p.drawText(QRect(20, 314, 320, 18), Qt.AlignmentFlag.AlignCenter, "EXPERIMENTAL · QEMU LIVE BOOT")
        p.setPen(QColor("#94A3B8"))
        p.setFont(QFont("Segoe UI", 9))
        p.drawText(
            QRect(28, 338, 304, 72),
            Qt.AlignmentFlag.AlignHCenter | Qt.TextFlag.TextWordWrap,
            "Click Play QEMU to execute real kernel/init/SurfaceFlinger under software TCG (OEM HALs & GUI display vary).",
        )
        p.end()
        return img

    def render_boot_console(
        self,
        *,
        phase: str,
        abi: str,
        backend: str,
        elapsed_s: float,
        milestones: list[str],
        tail_lines: list[str],
        panic: bool = False,
        running: bool = True,
        hw_gates: list[dict] | None = None,
    ):
        """Render a crisp live phone boot console onto the embedded display while waiting for GPU frames."""
        with self._lock:
            if self._has_live_gpu:
                return
        w, h = 360, 720
        img = QImage(w, h, QImage.Format.Format_ARGB32)
        img.fill(QColor("#090D14"))
        from PySide6.QtGui import QGuiApplication
        if QGuiApplication.instance() is None:
            with self._lock:
                if not self._has_live_gpu:
                    self._image = img
            return
        p = QPainter(img)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        # Header accent stripe
        has_fault = any(str(g.get("status", "")).lower() == "fault" for g in (hw_gates or ()))
        accent = (
            QColor("#EF4444") if (panic or has_fault)
            else (QColor("#22C55E") if "boot_completed" in milestones else QColor("#38BDF8"))
        )
        p.fillRect(0, 0, w, 4, accent)

        # Top phone status row
        p.setPen(QColor("#94A3B8"))
        p.setFont(QFont("Consolas", 8, QFont.Weight.Bold))
        p.drawText(14, 24, f"QEMU · {abi.upper()}")
        p.drawText(QRect(14, 12, w - 28, 16), Qt.AlignmentFlag.AlignRight, f"{elapsed_s:05.1f}s")

        # Center boot badge & phase
        p.setPen(accent)
        p.setFont(QFont("Segoe UI", 12, QFont.Weight.Bold))
        title = (
            "BOOT FAILED · PANIC" if panic
            else ("BOOT FAILED · VENDOR/KERNEL FAULT" if has_fault
                  else ("ANDROID BOOTING" if running else "SESSION STOPPED"))
        )
        p.drawText(QRect(16, 40, w - 32, 24), Qt.AlignmentFlag.AlignCenter, title)

        p.setPen(QColor("#E2E8F0"))
        p.setFont(QFont("Segoe UI", 9, QFont.Weight.DemiBold))
        p.drawText(
            QRect(18, 66, w - 36, 32),
            Qt.AlignmentFlag.AlignHCenter | Qt.TextFlag.TextWordWrap,
            phase or "Initializing virtual hardware…",
        )

        # Kernel-Contract Hardware Gate pills (FS · DISP · TC · AUD · KMI)
        y_gate = 104
        if hw_gates:
            gx = 14
            pill_w = (w - 28 - max(0, len(hw_gates) - 1) * 4) // max(1, len(hw_gates))
            for g in hw_gates[:6]:
                st = str(g.get("status", "generic")).lower()
                if st == "fault":
                    bg, fg = QColor("#3B1219"), QColor("#F87171")
                elif st == "warn":
                    bg, fg = QColor("#3A2A0A"), QColor("#FBBF24")
                elif st == "pass":
                    bg, fg = QColor("#0C2D1B"), QColor("#4ADE80")
                else:
                    bg, fg = QColor("#112136"), QColor("#7DD3FC")
                p.setPen(QPen(fg, 1))
                p.setBrush(bg)
                p.drawRoundedRect(gx, y_gate, pill_w, 20, 4, 4)
                p.setPen(fg)
                p.setFont(QFont("Consolas", 7, QFont.Weight.Bold))
                lbl = str(g.get("shortLabel") or g.get("label") or "")[:6]
                p.drawText(QRect(gx, y_gate, pill_w, 20), Qt.AlignmentFlag.AlignCenter, lbl)
                gx += pill_w + 4

        # Milestone progress dots
        stages = ("kernel", "init_first", "fs_mounted", "selinux", "init_second", "apexd", "zygote", "boot_completed")
        labels = ("KRN", "1ST", "MNT", "SEL", "2ND", "APX", "ZYG", "OK")
        y_dot = 144 if hw_gates else 128
        step = (w - 44) / max(1, len(stages) - 1)
        for i, (key, lbl) in enumerate(zip(stages, labels)):
            cx = int(22 + i * step)
            done = key in milestones
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor("#22C55E") if done else QColor("#1E293B"))
            p.drawEllipse(cx - 5, y_dot - 5, 10, 10)
            p.setPen(QColor("#CBD5E1") if done else QColor("#64748B"))
            p.setFont(QFont("Consolas", 7))
            p.drawText(QRect(cx - 16, y_dot + 8, 32, 14), Qt.AlignmentFlag.AlignCenter, lbl)

        # Divider
        y_div = y_dot + 28
        p.setPen(QPen(QColor("#1E293B"), 1))
        p.drawLine(14, y_div, w - 14, y_div)

        # Scrolling serial / dmesg / logcat console inside the screen
        p.setFont(QFont("Consolas", 8))
        y = y_div + 14
        line_h = 15
        max_lines = max(4, (h - y - 24) // line_h)
        for ln in tail_lines[-max_lines:]:
            low = ln.lower()
            if "panic" in low or "fatal" in low or "error" in low or "denied" in low or "fault" in low:
                p.setPen(QColor("#F87171"))
            elif "warn" in low:
                p.setPen(QColor("#FBBF24"))
            elif "linux version" in low or "systemserver" in low or "boot_completed" in low or "second stage" in low or "activated" in low:
                p.setPen(QColor("#4ADE80"))
            else:
                p.setPen(QColor("#94A3B8"))
            p.drawText(QRect(12, y, w - 24, line_h), Qt.AlignmentFlag.AlignLeft, ln[:52])
            y += line_h

        # Footer backend note
        p.setPen(QColor("#475569"))
        p.setFont(QFont("Consolas", 7))
        p.drawText(QRect(12, h - 20, w - 24, 14), Qt.AlignmentFlag.AlignCenter, backend or "VirtIO Framebuffer Standby")
        p.end()

        with self._lock:
            if not self._has_live_gpu:
                self._image = img

    @staticmethod
    def _is_non_blank_gpu_frame(img: QImage) -> bool:
        """Return True when the VNC framebuffer actually renders graphics (not a blank screen or 640x480 QEMU banner)."""
        w, h = img.width(), img.height()
        if w <= 0 or h <= 0:
            return False
        # Before DRM mode-set, QEMU displays a 640x480 banner ("Guest has not initialized the display (yet).")
        # occupying ~0.9% of sampled pixels; once DRM mode-sets (e.g. 540x960), even subtle small boot logos
        # (>= 0.2% non-black pixels on a 48x48 grid) represent genuine live Android GPU output.
        is_modeset = (w, h) != (640, 480)
        grid = 48 if is_modeset else 32
        step_x = max(1, w // grid)
        step_y = max(1, h // grid)
        total = 0
        non_black = 0
        for y in range(0, h, step_y):
            for x in range(0, w, step_x):
                total += 1
                rgb = img.pixel(x, y) & 0xFFFFFF
                if rgb != 0:
                    non_black += 1
        if total <= 0:
            return False
        if is_modeset:
            return non_black >= 4 and (non_black / total) >= 0.002
        return (non_black / total) >= 0.015

    def set_live_gpu_image(self, img: QImage):
        # Keep the live phone boot HUD visible while virtio-gpu is still a blank black scanout
        if not self._is_non_blank_gpu_frame(img):
            with self._lock:
                self._fb_w = img.width()
                self._fb_h = img.height()
            return
        with self._lock:
            self._has_live_gpu = True
            self._fb_w = img.width()
            self._fb_h = img.height()
            self._image = img.copy()

    def reset_for_boot(self):
        with self._lock:
            self._has_live_gpu = False
            self._fb_w = 360
            self._fb_h = 720
            self._image = None

    def attach_rfb(self, rfb: RfbClient | None):
        with self._lock:
            old = self._rfb
            self._rfb = rfb
        if old is not None and old is not rfb:
            old.stop()

    def send_touch(self, norm_x: float, norm_y: float, pressed: bool):
        with self._lock:
            rfb = self._rfb
            w, h = self._fb_w, self._fb_h
        if rfb is None:
            return
        x = max(0, min(w - 1, int(norm_x * w)))
        y = max(0, min(h - 1, int(norm_y * h)))
        rfb.send_pointer(x, y, 1 if pressed else 0)

    def send_android_key(self, name: str):
        with self._lock:
            rfb = self._rfb
        if rfb is None:
            return
        sym = _ANDROID_KEYSYMS.get(str(name or "").lower())
        if sym:
            rfb.send_key(sym, True)
            rfb.send_key(sym, False)

    def snapshot(self) -> QImage:
        with self._lock:
            img = self._image
        if img is None:
            img = self._render_idle_screen()
            with self._lock:
                if self._image is None:
                    self._image = img
                else:
                    img = self._image
        return img.copy()

    @property
    def has_live_gpu(self) -> bool:
        with self._lock:
            return self._has_live_gpu


_GLOBAL_HUB: QemuDisplayHub | None = None


def get_qemu_display_hub() -> QemuDisplayHub:
    global _GLOBAL_HUB
    if _GLOBAL_HUB is None:
        _GLOBAL_HUB = QemuDisplayHub()
    return _GLOBAL_HUB


class QemuScreenProvider(QQuickImageProvider):
    """QtQuick image provider registered as `image://qemu_screen/...`."""

    def __init__(self, hub: QemuDisplayHub | None = None):
        super().__init__(QQuickImageProvider.ImageType.Image)
        self._hub = hub or get_qemu_display_hub()

    def requestImage(self, _id: str, size: QSize, requestedSize: QSize) -> QImage:
        img = self._hub.snapshot()
        if requestedSize.width() > 0 and requestedSize.height() > 0:
            img = img.scaled(
                requestedSize,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        if size is not None:
            size.setWidth(img.width())
            size.setHeight(img.height())
        return img


# ── Minimal Pure-Python RFB 3.8 (VNC) Client for Localhost QEMU ──

def _recv_exact(sock: socket.socket, n: int) -> bytes:
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise EOFError("RFB socket closed")
        buf.extend(chunk)
    return bytes(buf)


class RfbClient:
    """Lightweight pure-Python RFB 3.8 client talking to QEMU's localhost `-vnc` server."""

    def __init__(self, host: str, port: int, hub: QemuDisplayHub, on_frame=None):
        self.host = host
        self.port = int(port)
        self.hub = hub
        self.on_frame = on_frame
        self._stop = threading.Event()
        self._sock: socket.socket | None = None
        self._send_lock = threading.Lock()
        self._width = 0
        self._height = 0
        self._fb = bytearray()
        self._thread: threading.Thread | None = None

    def start(self):
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="RAK-QEMU-RFB")
        self._thread.start()

    def stop(self):
        self._stop.set()
        sock = self._sock
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except Exception:
                pass
            try:
                sock.close()
            except Exception:
                pass

    def send_pointer(self, x: int, y: int, mask: int):
        sock = self._sock
        if sock is None or self._stop.is_set():
            return
        pkt = struct.pack("!BBHH", 5, mask & 0xFF, max(0, x), max(0, y))
        with self._send_lock:
            try:
                sock.sendall(pkt)
            except OSError:
                pass

    def send_key(self, keysym: int, down: bool):
        sock = self._sock
        if sock is None or self._stop.is_set():
            return
        pkt = struct.pack("!BBHI", 4, 1 if down else 0, 0, keysym & 0xFFFFFFFF)
        with self._send_lock:
            try:
                sock.sendall(pkt)
            except OSError:
                pass

    def _connect_with_retry(self) -> socket.socket | None:
        deadline = time.monotonic() + 25.0
        while not self._stop.is_set() and time.monotonic() < deadline:
            try:
                s = socket.create_connection((self.host, self.port), timeout=2.0)
                s.settimeout(4.0)
                return s
            except OSError:
                time.sleep(0.35)
        return None

    def _run(self):
        sock = self._connect_with_retry()
        if sock is None:
            return
        self._sock = sock
        try:
            # 1) ProtocolVersion handshake
            srv_ver = _recv_exact(sock, 12)
            if not srv_ver.startswith(b"RFB "):
                return
            sock.sendall(b"RFB 003.008\n")

            # 2) Security handshake (expect None = 1)
            nsec = _recv_exact(sock, 1)[0]
            if nsec == 0:
                return
            stypes = _recv_exact(sock, nsec)
            if 1 not in stypes:
                return
            sock.sendall(b"\x01")
            sec_res = struct.unpack("!I", _recv_exact(sock, 4))[0]
            if sec_res != 0:
                return

            # 3) ClientInit (shared=1) + ServerInit
            sock.sendall(b"\x01")
            hdr = _recv_exact(sock, 24)
            w, h = struct.unpack("!HH", hdr[:4])
            name_len = struct.unpack("!I", hdr[20:24])[0]
            if name_len:
                _recv_exact(sock, name_len)
            if w <= 0 or h <= 0 or w > 4096 or h > 4096:
                return
            self._width = w
            self._height = h
            self._fb = bytearray(w * h * 4)

            # 4) SetPixelFormat -> 32-bit BGRA little-endian (matches Qt Format_RGB32 directly!)
            # bpp=32, depth=24, big_endian=0, true_color=1, r_max=255, g_max=255, b_max=255, r_shift=16, g_shift=8, b_shift=0
            set_pf = struct.pack(
                "!BBBBBBBBHHHBBB3x",
                0, 0, 0, 0,
                32, 24, 0, 1,
                255, 255, 255,
                16, 8, 0,
            )
            sock.sendall(set_pf)
            # SetEncodings: Raw (0) + CopyRect (1) + DesktopSize (-223)
            set_enc = struct.pack("!BBHiii", 2, 0, 3, 0, 1, -223)
            sock.sendall(set_enc)

            # 5) Frame update loop
            incremental = 0
            sock.settimeout(2.5)
            while not self._stop.is_set():
                req = struct.pack("!BBHHHH", 3, incremental, 0, 0, self._width, self._height)
                with self._send_lock:
                    sock.sendall(req)
                incremental = 1
                try:
                    msg_type = _recv_exact(sock, 1)[0]
                except socket.timeout:
                    continue
                if msg_type == 0:
                    # FramebufferUpdate
                    _pad, nrects = struct.unpack("!BH", _recv_exact(sock, 3))
                    dirty = False
                    for _ in range(nrects):
                        rx, ry, rw, rh, enc = struct.unpack("!HHHHi", _recv_exact(sock, 12))
                        if enc == -223:
                            # DesktopSize pseudo-encoding
                            if 0 < rw <= 4096 and 0 < rh <= 4096:
                                self._width, self._height = rw, rh
                                self._fb = bytearray(rw * rh * 4)
                                incremental = 0
                        elif enc == 0:
                            # Raw 32-bit BGRA
                            nbytes = rw * rh * 4
                            pixels = _recv_exact(sock, nbytes)
                            stride = self._width * 4
                            row_b = rw * 4
                            for row in range(rh):
                                dst = (ry + row) * stride + rx * 4
                                src = row * row_b
                                if dst + row_b <= len(self._fb):
                                    self._fb[dst:dst + row_b] = pixels[src:src + row_b]
                            dirty = True
                        elif enc == 1:
                            # CopyRect
                            sx, sy = struct.unpack("!HH", _recv_exact(sock, 4))
                            stride = self._width * 4
                            row_b = rw * 4
                            tmp = bytearray(rh * row_b)
                            for row in range(rh):
                                s_off = (sy + row) * stride + sx * 4
                                tmp[row * row_b:(row + 1) * row_b] = self._fb[s_off:s_off + row_b]
                            for row in range(rh):
                                d_off = (ry + row) * stride + rx * 4
                                self._fb[d_off:d_off + row_b] = tmp[row * row_b:(row + 1) * row_b]
                            dirty = True
                    if dirty:
                        qimg = QImage(
                            bytes(self._fb),
                            self._width,
                            self._height,
                            self._width * 4,
                            QImage.Format.Format_RGB32,
                        )
                        self.hub.set_live_gpu_image(qimg)
                        if self.on_frame:
                            self.on_frame()
                elif msg_type == 1:
                    # SetColourMapEntries
                    _pad, _first, ncols = struct.unpack("!BHH", _recv_exact(sock, 5))
                    _recv_exact(sock, ncols * 6)
                elif msg_type == 2:
                    # Bell
                    pass
                elif msg_type == 3:
                    # ServerCutText
                    _pad, length = struct.unpack("!3sI", _recv_exact(sock, 7))
                    if length:
                        _recv_exact(sock, length)
                else:
                    break
                time.sleep(0.05)
        except Exception:
            pass
        finally:
            try:
                sock.close()
            except Exception:
                pass
            self._sock = None
