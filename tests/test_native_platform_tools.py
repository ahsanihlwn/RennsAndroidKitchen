# SPDX-License-Identifier: MPL-2.0
"""Verify native in-process platform-tools-rs (MIT) integration across rak_native, CLI, and terminal."""
from __future__ import annotations

import json
import os
from pathlib import Path
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import zlib

from core import dependency_manager, native_backend, rak_cli
from core.app_paths import BIN_DIR
from core.binary_handler import resolve_binary
from core.cli_environment import describe as describe_cli_env
from core.android_tools import parse_fastboot_output
from core.native_tools import NATIVE_BUILTIN_PACKAGES, NATIVE_PLATFORM_TOOLS, is_native_tool
from core.terminal_binary import get_native_cli_directory, get_terminal_binary_directories, resolve_terminal_binary

REPO_ROOT = Path(__file__).resolve().parents[1]


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _recv_exact(sock: socket.socket, n: int) -> bytes:
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise EOFError("unexpected EOF on TCP fastboot socket")
        buf.extend(chunk)
    return bytes(buf)


def _send_fb_pkt(sock: socket.socket, payload: bytes) -> None:
    sock.sendall(struct.pack(">Q", len(payload)) + payload)


def _recv_fb_pkt(sock: socket.socket) -> bytes:
    hdr = _recv_exact(sock, 8)
    (length,) = struct.unpack(">Q", hdr)
    return _recv_exact(sock, length)


class _TcpFastbootFixture:
    """Minimal in-process AOSP FB01 TCP fastboot loopback server."""

    def __init__(self, *, max_download_size: int = 0x10000, stall_download: bool = False):
        self.max_download_size = max_download_size
        self.stall_download = stall_download
        self.commands: list[str] = []
        self.downloaded_chunks: list[bytes] = []
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(8)
        self._sock.settimeout(0.2)
        self.port = int(self._sock.getsockname()[1])
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self) -> None:
        while not self._stop.is_set():
            try:
                conn, _ = self._sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            threading.Thread(target=self._handle_conn, args=(conn,), daemon=True).start()

    def _handle_conn(self, conn: socket.socket) -> None:
        with conn:
            conn.settimeout(2.0)
            try:
                hs = _recv_exact(conn, 4)
                if hs != b"FB01":
                    return
                conn.sendall(b"FB01")
                while not self._stop.is_set():
                    try:
                        pkt = _recv_fb_pkt(conn)
                    except (EOFError, OSError, socket.timeout):
                        break
                    cmd = pkt.decode("utf-8", errors="replace")
                    self.commands.append(cmd)
                    if cmd == "getvar:all":
                        for line in (
                            "product:fixture_phone",
                            "serialno:FBTEST01",
                            "current-slot:b",
                            "unlocked:yes",
                            "has-slot:system:yes",
                            "partition-size:system_a:0x400000",
                            "partition-size:system_b:0x400000",
                            "is-logical:system_b:yes",
                            f"max-download-size:0x{self.max_download_size:x}",
                        ):
                            _send_fb_pkt(conn, b"INFO" + line.encode("utf-8"))
                        _send_fb_pkt(conn, b"OKAY")
                    elif cmd.startswith("getvar:"):
                        var = cmd[len("getvar:") :]
                        if var == "max-download-size":
                            _send_fb_pkt(conn, f"OKAY0x{self.max_download_size:x}".encode("ascii"))
                        elif var == "current-slot":
                            _send_fb_pkt(conn, b"OKAYb")
                        elif var.startswith("has-slot:"):
                            part = var[len("has-slot:") :]
                            _send_fb_pkt(conn, b"OKAYyes" if part == "system" else b"OKAYno")
                        elif var.startswith("is-logical:"):
                            _send_fb_pkt(conn, b"OKAYyes")
                        else:
                            _send_fb_pkt(conn, b"FAILunknown variable")
                    elif cmd.startswith("download:"):
                        size = int(cmd.split(":", 1)[1], 16)
                        _send_fb_pkt(conn, f"DATA{size:08x}".encode("ascii"))
                        received = bytearray()
                        aborted = False
                        while len(received) < size:
                            if self.stall_download:
                                while not self._stop.is_set():
                                    time.sleep(0.05)
                                aborted = True
                                break
                            chunk = _recv_fb_pkt(conn)
                            received.extend(chunk)
                        if aborted:
                            break
                        self.downloaded_chunks.append(bytes(received))
                        _send_fb_pkt(conn, b"OKAY")
                    elif cmd.startswith("flash:"):
                        _send_fb_pkt(conn, b"OKAY")
                    else:
                        _send_fb_pkt(conn, b"OKAY")
            except (EOFError, OSError):
                pass

    def close(self) -> None:
        self._stop.set()
        try:
            self._sock.close()
        except OSError:
            pass
        self._thread.join(timeout=1.0)


class NativePlatformToolsTests(unittest.TestCase):
    def tearDown(self) -> None:
        native_backend.adb_shutdown_servers()

    def test_license_notice_and_clean_source_tree(self) -> None:
        pt_dir = REPO_ROOT / "core" / "platform_tools_rs"
        license_text = (pt_dir / "LICENSE").read_text(encoding="utf-8")
        self.assertIn("MIT License", license_text)
        self.assertIn("ahsanihlwn", license_text)

        notice_text = (REPO_ROOT / "NOTICE").read_text(encoding="utf-8")
        self.assertIn("core/platform_tools_rs/", notice_text)
        self.assertFalse((pt_dir / "dist").exists())
        self.assertFalse((pt_dir / "target").exists())

        forbidden_exts = {".exe", ".dll", ".pdb", ".lib"}
        bad_files = []
        for root, dirs, files in os.walk(pt_dir):
            for f in files:
                if Path(f).suffix.lower() in forbidden_exts:
                    bad_files.append(str(Path(root) / f))
        self.assertEqual(bad_files, [], f"Forbidden compiled binaries found: {bad_files}")

    def test_native_registry_dependency_manager_and_cli_environment(self) -> None:
        self.assertEqual(
            NATIVE_PLATFORM_TOOLS,
            ("adb", "fastboot", "mke2fs", "make_f2fs", "make_f2fs_casefold", "etc1tool", "hprof-conv", "sqlite3"),
        )
        self.assertIn("platform-tools", NATIVE_BUILTIN_PACKAGES)
        for tool in NATIVE_PLATFORM_TOOLS:
            self.assertTrue(is_native_tool(tool), tool)
            self.assertTrue(is_native_tool(f"{tool}.exe"), tool)

        adb_path = resolve_binary(BIN_DIR, "adb.exe")
        fb_path = resolve_binary(BIN_DIR, "fastboot.exe")
        self.assertIsNotNone(adb_path)
        self.assertIsNotNone(fb_path)
        self.assertFalse(str(adb_path).lower().endswith(".exe"))
        self.assertFalse(str(fb_path).lower().endswith(".exe"))

        with tempfile.TemporaryDirectory() as tmp:
            empty_bin = Path(tmp)
            with patch.object(dependency_manager, "BIN_ROOT", str(empty_bin)), \
                 patch.object(dependency_manager, "LOCAL_CACHE_JSON", str(empty_bin / "package.json")):
                dm = dependency_manager.DependencyManager(allow_remote=False)
                dm.registry = {
                    "platform-tools": {"display": "Platform Tools", "binaries": [{"name": "adb.exe"}]},
                    "platform_tools": {"display": "Platform Tools", "binaries": [{"name": "fastboot.exe"}]},
                    "apktool_env": {"display": "Apktool", "binaries": [{"name": "apktool.jar"}]},
                }
                names = [p["name"] for p in dm.list_packages()]
                self.assertEqual(names, ["apktool_env"])

        env_info = describe_cli_env(str(REPO_ROOT))
        self.assertEqual(env_info["tools"]["adb.exe"].get("backend"), "rak_native")
        self.assertEqual(env_info["tools"]["fastboot.exe"].get("backend"), "rak_native")

    def test_adb_in_process_zero_subprocess(self) -> None:
        with patch("subprocess.Popen", side_effect=AssertionError("subprocess.Popen forbidden")), \
             patch("subprocess.run", side_effect=AssertionError("subprocess.run forbidden")):
            ver = native_backend.platform_tools_version()
            self.assertEqual(ver["version"], "37.0.3")
            self.assertEqual(ver["adb_protocol"], "1.0.41")

            res_ver = native_backend.adb_run(["--version"])
            self.assertEqual(res_ver["exit_code"], 0)
            self.assertIn("37.0.3", res_ver["output"])

            with tempfile.TemporaryDirectory() as tmp:
                key_path = Path(tmp) / "test_adbkey"
                res_key = native_backend.adb_run(["keygen", str(key_path)])
                self.assertEqual(res_key["exit_code"], 0, res_key)
                self.assertTrue(key_path.is_file())
                self.assertTrue(Path(f"{key_path}.pub").is_file())

            port = _free_port()
            self.assertTrue(native_backend.adb_ensure_server("127.0.0.1", port))
            res_dev = native_backend.adb_run(["-P", str(port), "devices"])
            self.assertEqual(res_dev["exit_code"], 0, res_dev)
            self.assertIn("List of devices attached", res_dev["output"])

            res_kill = native_backend.adb_run(["-P", str(port), "kill-server"])
            self.assertEqual(res_kill["exit_code"], 0, res_kill)

    def test_fastboot_tcp_loopback_getvar_sparse_flash_progress_and_cancel(self) -> None:
        fixture = _TcpFastbootFixture(max_download_size=0x10000)
        self.addCleanup(fixture.close)
        serial = f"tcp:127.0.0.1:{fixture.port}"

        with patch("subprocess.Popen", side_effect=AssertionError("subprocess.Popen forbidden")), \
             patch("subprocess.run", side_effect=AssertionError("subprocess.run forbidden")):
            res_vars = native_backend.fastboot_run(["-s", serial, "getvar", "all"], timeout_ms=5000)
            self.assertEqual(res_vars["exit_code"], 0, res_vars)
            parsed = parse_fastboot_output(res_vars["output"])
            self.assertEqual(parsed["device"], "fixture_phone")
            self.assertEqual(parsed["slot"], "b")
            self.assertTrue(parsed["unlocked"])
            self.assertIn("system", parsed["partitions"])

            with tempfile.TemporaryDirectory() as tmp:
                img_path = Path(tmp) / "system.img"
                # 160 KiB raw payload > 64 KiB max-download-size -> exercises multi-chunk sparse splitting
                img_path.write_bytes((b"RAK_NATIVE_TEST_" * 10240))
                progress_events = []
                res_flash = native_backend.fastboot_run(
                    ["-s", serial, "flash", "system", str(img_path)],
                    on_progress=lambda ev: progress_events.append(ev),
                    timeout_ms=10000,
                )
                self.assertEqual(res_flash["exit_code"], 0, res_flash)
                self.assertGreaterEqual(len(fixture.downloaded_chunks), 2)
                self.assertTrue(progress_events, "Expected real-time progress events during fastboot flash")
                pcts = [float(ev["percent"]) for ev in progress_events if ev.get("percent") is not None]
                self.assertEqual(pcts, sorted(pcts), f"Non-monotonic percentages: {pcts}")
                self.assertGreater(pcts[-1], 0.0)

        # Verify cancellation aborts a stalled TCP download cleanly
        stalled = _TcpFastbootFixture(max_download_size=0x100000, stall_download=True)
        self.addCleanup(stalled.close)
        token = native_backend.platform_cancel_token()
        with tempfile.TemporaryDirectory() as tmp:
            big_img = Path(tmp) / "stall.img"
            big_img.write_bytes(b"\x5a" * (512 * 1024))
            timer = threading.Timer(0.25, token.cancel)
            timer.start()
            try:
                t0 = time.monotonic()
                res_abort = native_backend.fastboot_run(
                    ["-s", f"tcp:127.0.0.1:{stalled.port}", "flash", "vendor", str(big_img)],
                    cancel=token,
                    timeout_ms=10000,
                )
                elapsed = time.monotonic() - t0
            finally:
                timer.cancel()
            self.assertNotEqual(res_abort["exit_code"], 0)
            self.assertLess(elapsed, 4.0)

    def test_companion_platform_tools_and_terminal_shims(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "test.db"
            res = subprocess.run(
                [sys.executable, str(REPO_ROOT / "core" / "rak_cli.py"), "sqlite3", str(db_path), "CREATE TABLE t(x INT); INSERT INTO t VALUES (42); SELECT x FROM t;"],
                cwd=str(REPO_ROOT),
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(res.returncode, 0, res.stderr)
            self.assertIn("42", res.stdout)

            # Check adb --version & fastboot --version through rak CLI
            for tool in ("adb", "fastboot"):
                cli_res = subprocess.run(
                    [sys.executable, str(REPO_ROOT / "core" / "rak_cli.py"), tool, "--version"],
                    cwd=str(REPO_ROOT),
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(cli_res.returncode, 0, cli_res.stderr)
                self.assertIn("37.0.3", cli_res.stdout)

        # Check mke2fs, make_f2fs, etc1tool, hprof-conv invocation paths directly
        with tempfile.TemporaryDirectory() as tmp:
            ext_img = Path(tmp) / "ext.img"
            rc_ext = native_backend.platform_tool_run_stdio("mke2fs", ["-t", "ext4", "-b", "4096", str(ext_img), "256"])
            self.assertEqual(rc_ext, 0)
            self.assertTrue(ext_img.is_file())

            f2fs_img = Path(tmp) / "f2fs.img"
            with f2fs_img.open("wb") as fh:
                fh.truncate(64 * 1024 * 1024)
            rc_f2fs = native_backend.platform_tool_run_stdio("make_f2fs", ["-f", str(f2fs_img)])
            self.assertEqual(rc_f2fs, 0)

            # Check etc1tool real 4x4 PNG -> PKM -> PNG roundtrip
            def _png_chunk(tag: bytes, payload: bytes) -> bytes:
                crc = zlib.crc32(tag + payload) & 0xFFFFFFFF
                return struct.pack(">I", len(payload)) + tag + payload + struct.pack(">I", crc)

            w, h = 4, 4
            raw_rows = b"".join(b"\x00" + (b"\x40\x80\xc0" * w) for _ in range(h))
            ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
            png_bytes = (
                b"\x89PNG\r\n\x1a\n"
                + _png_chunk(b"IHDR", ihdr)
                + _png_chunk(b"IDAT", zlib.compress(raw_rows))
                + _png_chunk(b"IEND", b"")
            )
            in_png = Path(tmp) / "in.png"
            out_pkm = Path(tmp) / "out.pkm"
            dec_png = Path(tmp) / "dec.png"
            in_png.write_bytes(png_bytes)
            rc_etc_enc = native_backend.platform_tool_run_stdio("etc1tool", [str(in_png), "--encode", "-o", str(out_pkm)])
            self.assertEqual(rc_etc_enc, 0)
            self.assertTrue(out_pkm.is_file() and out_pkm.stat().st_size > 16)
            rc_etc_dec = native_backend.platform_tool_run_stdio("etc1tool", [str(out_pkm), "--decode", "-o", str(dec_png)])
            self.assertEqual(rc_etc_dec, 0)
            self.assertTrue(dec_png.is_file() and dec_png.read_bytes().startswith(b"\x89PNG\r\n\x1a\n"))

            # Check hprof-conv real HPROF 1.0.3 -> 1.0.2 conversion
            in_hprof = Path(tmp) / "in.hprof"
            out_hprof = Path(tmp) / "out.hprof"
            in_hprof.write_bytes(b"JAVA PROFILE 1.0.3\0" + struct.pack(">IQ", 4, 0x0102030405060708))
            rc_hprof = native_backend.platform_tool_run_stdio("hprof-conv", [str(in_hprof), str(out_hprof)])
            self.assertEqual(rc_hprof, 0)
            self.assertTrue(out_hprof.is_file() and out_hprof.read_bytes().startswith(b"JAVA PROFILE 1.0.2\0"))

        # Verify ensure_rak_bat creates native_cli/*.cmd shims and terminal resolution prefers them
        rak_cli.ensure_rak_bat()
        native_dir = Path(get_native_cli_directory(BIN_DIR))
        self.assertTrue(native_dir.is_dir())
        for tool in NATIVE_PLATFORM_TOOLS:
            self.assertTrue((native_dir / f"{tool}.cmd").is_file(), f"Missing shim {tool}.cmd")
        dirs = get_terminal_binary_directories(BIN_DIR)
        self.assertEqual(os.path.normpath(dirs[0]), os.path.normpath(str(native_dir)))
        resolved_adb = resolve_terminal_binary(BIN_DIR, "adb")
        self.assertIsNotNone(resolved_adb)
        self.assertEqual(os.path.normpath(resolved_adb), os.path.normpath(str(native_dir / "adb.cmd")))


if __name__ == "__main__":
    unittest.main()
