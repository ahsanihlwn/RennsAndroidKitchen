# SPDX-License-Identifier: MPL-2.0
"""Verify binary_handler external/native split and locked first-token terminal routing."""
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import QPointF, QSizeF, Qt, qInstallMessageHandler
from PySide6.QtGui import QGuiApplication, QKeyEvent
from PySide6.QtQuick import QQuickWindow

from core import binary_handler, fsdetect, native_backend, terminal_binary, terminal_native_router, terminal_titles
from core.qtterm import Terminal
from ui.terminal import TerminalSession


class BinaryHandlerAndFsDetectTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="rak-bin-test-")
        self.bin_root = Path(self.tmp.name)
        (self.bin_root / "erofs_env").mkdir()
        (self.bin_root / "erofs_env" / "mkfs.erofs.exe").write_bytes(b"MZ")
        (self.bin_root / "erofs_env" / "dump.erofs.exe").write_bytes(b"MZ")
        (self.bin_root / "apktool").mkdir()
        (self.bin_root / "apktool" / "apktool.jar").write_bytes(b"PK")
        (self.bin_root / "package.json").write_text(
            json.dumps({
                "erofs_env": {
                    "binaries": [
                        {"name": "mkfs.erofs.exe", "size": 2},
                        {"name": "dump.erofs.exe", "size": 2},
                    ]
                },
                "apktool": {
                    "binaries": [{"name": "apktool.jar", "size": 2}]
                },
            }),
            encoding="utf-8",
        )

    def tearDown(self):
        self.tmp.cleanup()

    def test_external_vs_native_resolution(self):
        self.assertIsNone(binary_handler.resolve_external_binary(str(self.bin_root), "mkfs.erofs.exe"))
        self.assertIsNone(binary_handler.resolve_external_binary(str(self.bin_root), "adb.exe"))
        apktool = binary_handler.resolve_external_binary(str(self.bin_root), "apktool.jar")
        self.assertIsNotNone(apktool)
        self.assertTrue(apktool.endswith("apktool.jar"))

        native_erofs = binary_handler.resolve_binary(str(self.bin_root), "mkfs.erofs.exe")
        self.assertIsNotNone(native_erofs)
        self.assertFalse(native_erofs.lower().endswith(".exe"))

    def test_terminal_binary_excludes_legacy_native_dirs_and_exes(self):
        dirs = terminal_binary.get_terminal_binary_directories(str(self.bin_root))
        norm_dirs = [str(Path(d).resolve()) for d in dirs]
        self.assertNotIn(str((self.bin_root / "erofs_env").resolve()), norm_dirs)
        self.assertIn(str((self.bin_root / "apktool").resolve()), norm_dirs)
        self.assertIsNone(terminal_binary.resolve_terminal_binary(str(self.bin_root), "mkfs.erofs"))
        self.assertEqual(
            terminal_titles.normalize_terminal_command("adb devices", str(self.bin_root)),
            "adb devices",
        )
        self.assertEqual(
            terminal_titles.normalize_terminal_command("erofs info system.img", str(self.bin_root)),
            "erofs info system.img",
        )

    def test_fsdetect_erofs_uses_native_without_popen(self):
        src_dir = self.bin_root / "src_tree"
        (src_dir / "etc").mkdir(parents=True)
        (src_dir / "etc" / "build.prop").write_text("ro.build.id=TEST\n", encoding="utf-8")
        out_img = self.bin_root / "test_erofs.img"
        native_backend.erofs_pack(str(src_dir), str(out_img))

        with patch("subprocess.Popen", side_effect=AssertionError("must not spawn Popen")):
            self.assertEqual(fsdetect.detect_fs_type(str(out_img)), "EROFS")


class TerminalNativeRouterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QGuiApplication.instance() or QGuiApplication(sys.argv)

    def test_prompt_extraction_and_first_token_lock(self):
        lines = [
            "Microsoft Windows",
            r"C:\roms\port\system>erofs info ..\system.img",
        ]
        parsed = terminal_native_router.extract_prompt_command(lines, 1, 80, fallback_cwd=str(Path.cwd()))
        self.assertIsNotNone(parsed)
        cwd, raw_cmd, tokens, shell_kind = parsed
        self.assertEqual(raw_cmd, r"erofs info ..\system.img")
        self.assertEqual(tokens, ["erofs", "info", r"..\system.img"])
        self.assertEqual(shell_kind, "cmd")

        self.assertIsNone(
            terminal_native_router.extract_prompt_command(
                [r"C:\roms>echo hello"], 0, 80, fallback_cwd=str(Path.cwd())
            )
        )
        self.assertIsNone(
            terminal_native_router.extract_prompt_command(
                [r"C:\roms>adb devices | findstr device"], 0, 80, fallback_cwd=str(Path.cwd())
            )
        )

    def test_open_terminal_does_not_spawn_ensure_rak_bat_and_pipes_locked_commands(self):
        with patch("core.rak_cli.ensure_rak_bat", side_effect=AssertionError("must not call ensure_rak_bat on open")):
            session = TerminalSession()
            self.addCleanup(session.stop)
            self.assertTrue(session.start())
            term = Terminal()
            self.addCleanup(term.stop)
            term.setSize(QSizeF(900, 480))
            session.bindTerminal(term)
            term.start()

        for _ in range(25):
            self.app.processEvents()
            if ">" in term.screenText():
                break
            time.sleep(0.03)

        # 1) Click/programmatic runCommand with locked tool runs in-process without cmd/exe spawn
        with patch("subprocess.Popen", side_effect=AssertionError("must not spawn exe")):
            session.runCommand("erofs --help")
            found = False
            for _ in range(40):
                self.app.processEvents()
                if "erofs (native erofs-rs in-process)" in term.screenText():
                    found = True
                    break
                time.sleep(0.03)
            self.assertTrue(found, f"Missing erofs in-process output:\n{term.screenText()}")

            # Wait for prompt return
            for _ in range(20):
                self.app.processEvents()
                if not term._native_cmd_running:
                    break
                time.sleep(0.02)

            # 2) Interactive keyboard typing + Enter locks first token and pipes output in-process
            for ch in "adb version":
                ev = QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_Space if ch == " " else ord(ch.upper()), Qt.KeyboardModifier.NoModifier, ch)
                term.keyPressEvent(ev)
            for _ in range(10):
                self.app.processEvents()
                if "adb version" in term.screenText():
                    break
                time.sleep(0.02)

            enter_ev = QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_Return, Qt.KeyboardModifier.NoModifier, "\r")
            term.keyPressEvent(enter_ev)

            found_adb = False
            for _ in range(50):
                self.app.processEvents()
                txt = term.screenText()
                if ("List of devices attached" in txt or "Android Debug Bridge" in txt or "Version" in txt) and not term._native_cmd_running:
                    found_adb = True
                    break
                time.sleep(0.03)
            self.assertTrue(found_adb, f"Missing native adb version output:\n{term.screenText()}")
            # Ensure interactive typing did not duplicate the command prompt line
            self.assertEqual(term.screenText().count("adb version"), 1, term.screenText())

        # 3) Follow-up shell command renders cleanly via row_bias without overwriting adb output
        session.runCommand("echo AFTER_NATIVE_SYNC")
        found_echo = False
        for _ in range(40):
            self.app.processEvents()
            txt = term.screenText()
            if "AFTER_NATIVE_SYNC" in txt:
                found_echo = True
                break
            time.sleep(0.03)
        self.assertTrue(found_echo, f"Missing follow-up echo output:\n{term.screenText()}")
        self.assertTrue(
            "Android Debug Bridge" in term.screenText() or "Version" in term.screenText(),
            f"Native adb output was overwritten:\n{term.screenText()}",
        )

        # 4) Up-arrow history must stay clean (no @(for /l ...)__R^... sync pollution)
        for _ in range(2):
            term.keyPressEvent(QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_Up, Qt.KeyboardModifier.NoModifier, ""))
            for _ in range(8):
                self.app.processEvents()
                time.sleep(0.02)
        screen_after_up = term.screenText()
        self.assertNotIn("@(for /l", screen_after_up)
        self.assertNotIn("__R^", screen_after_up)

    def test_ctrl_c_copies_selection_and_live_paint_emits_no_timer_warnings(self):
        warnings = []

        def _msg_handler(_mode, _ctx, msg):
            text = str(msg)
            if "killTimer" in text or "startTimer" in text:
                warnings.append(text)

        prev_handler = qInstallMessageHandler(_msg_handler)
        cb = QGuiApplication.clipboard()
        old_cb = cb.text()
        try:
            win = QQuickWindow()
            win.resize(900, 480)
            term = Terminal(win.contentItem())
            self.addCleanup(term.stop)
            self.addCleanup(win.close)
            term.setWidth(900)
            term.setHeight(480)
            win.show()
            term.start()

            for _ in range(30):
                self.app.processEvents()
                if ">" in term.screenText():
                    break
                time.sleep(0.03)

            term.selectAll()
            self.app.processEvents()
            term.keyPressEvent(
                QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier, "\x03")
            )
            for _ in range(10):
                self.app.processEvents()
                time.sleep(0.02)

            copied = cb.text()
            self.assertIn("Microsoft Windows", copied)
            self.assertEqual(warnings, [])
        finally:
            cb.setText(old_cb)
            qInstallMessageHandler(prev_handler)


if __name__ == "__main__":
    unittest.main()
