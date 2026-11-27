# SPDX-License-Identifier: MPL-2.0
"""Cross-platform compatibility tests covering Windows, macOS, and Linux."""
from __future__ import annotations

import os
from pathlib import Path
import sys
import tarfile
import tempfile
import threading
import time
import unittest
from unittest.mock import MagicMock, patch
import zipfile


class AppPathsCrossPlatformTests(unittest.TestCase):
    def test_resolve_app_root_per_platform(self):
        from core.app_paths import resolve_app_root

        home = os.path.expanduser("~")
        win = resolve_app_root(platform="win32", env={"LOCALAPPDATA": r"C:\Users\Dev\AppData\Local"})
        self.assertTrue(win.endswith("RennsAndroidKitchen"))
        self.assertIn("AppData", win)

        mac = resolve_app_root(platform="darwin", env={})
        self.assertEqual(mac, os.path.join(home, "Library", "Application Support", "RennsAndroidKitchen"))

        linux_default = resolve_app_root(platform="linux", env={})
        self.assertEqual(linux_default, os.path.join(home, ".local", "share", "RennsAndroidKitchen"))

        linux_xdg = resolve_app_root(platform="linux", env={"XDG_DATA_HOME": "/tmp/xdg_data"})
        self.assertEqual(linux_xdg, os.path.join("/tmp/xdg_data", "RennsAndroidKitchen"))

        override = resolve_app_root(platform="linux", env={"RAK_APP_DATA_DIR": "/tmp/custom_rak"})
        self.assertEqual(override, os.path.abspath("/tmp/custom_rak"))


class PosixTrashCrossPlatformTests(unittest.TestCase):
    def test_linux_freedesktop_trash_lifecycle(self):
        from core import windows_recycle
        from core.project_trash import ProjectTrash

        with tempfile.TemporaryDirectory(prefix="rak-trash-linux-") as tmp:
            trash_dir = os.path.join(tmp, "Trash")
            meta_dir = os.path.join(tmp, "meta")
            proj_dir = os.path.join(tmp, "projects", "MyROM")
            os.makedirs(proj_dir, exist_ok=True)
            marker = os.path.join(proj_dir, "build.prop")
            Path(marker).write_text("ro.build.id=TEST\n", encoding="utf-8")

            with patch.dict(os.environ, {"RAK_TRASH_DIR": trash_dir}), \
                 patch.object(windows_recycle.sys, "platform", "linux"):
                handle = windows_recycle._posix_recycle(proj_dir, platform="linux")
                self.assertEqual(handle["platform"], "linux")
                self.assertFalse(os.path.exists(proj_dir))
                self.assertTrue(os.path.isdir(handle["filesystem_path"]))
                self.assertTrue(os.path.isfile(handle["info_path"]))
                info_text = Path(handle["info_path"]).read_text(encoding="utf-8")
                self.assertIn("[Trash Info]", info_text)
                self.assertIn("DeletionDate=", info_text)
                windows_recycle.validate_handle(handle)

                # Restore back to original path
                windows_recycle.restore(handle, proj_dir)
                self.assertTrue(os.path.isfile(marker))
                self.assertFalse(os.path.exists(handle["filesystem_path"]))
                self.assertFalse(os.path.exists(handle["info_path"]))

                # Recycle again and purge via ProjectTrash.maintain() after expiry
                now = [1000.0]
                trash = ProjectTrash(meta_dir, backend=windows_recycle, clock=lambda: now[0])
                entry = trash.recycle({"path": proj_dir, "project_name": "MyROM"})
                self.assertFalse(os.path.exists(proj_dir))
                self.assertEqual(len(trash.entries()), 1)

                now[0] = entry["expires_at"] + 1.0
                errors = trash.maintain()
                self.assertEqual(errors, [])
                self.assertEqual(trash.entries(), [])
                self.assertFalse(os.path.exists(entry["handle"]["filesystem_path"]))

    def test_macos_trash_lifecycle_and_validation(self):
        from core import windows_recycle

        with tempfile.TemporaryDirectory(prefix="rak-trash-mac-") as tmp:
            trash_dir = os.path.join(tmp, ".Trash")
            proj_dir = os.path.join(tmp, "Work", "MacROM")
            os.makedirs(proj_dir, exist_ok=True)
            Path(proj_dir, "sample.txt").write_text("hello\n", encoding="utf-8")

            with patch.dict(os.environ, {"RAK_TRASH_DIR": trash_dir}):
                handle = windows_recycle._posix_recycle(proj_dir, platform="darwin")
                self.assertEqual(handle["platform"], "darwin")
                self.assertEqual(handle["info_path"], "")
                self.assertEqual(os.path.dirname(handle["filesystem_path"]), os.path.abspath(trash_dir))
                windows_recycle.validate_handle(handle)

                # Traversal handle must be rejected
                with self.assertRaises(windows_recycle.RecycleError):
                    windows_recycle.validate_handle({
                        "platform": "darwin",
                        "filesystem_path": os.path.abspath(os.path.join(tmp, "Work")),
                        "parsing_name": os.path.abspath(os.path.join(tmp, "Work")),
                    })

                windows_recycle.purge(handle)
                self.assertFalse(os.path.exists(handle["filesystem_path"]))


class DiskAndStorageCrossPlatformTests(unittest.TestCase):
    def test_posix_mount_filtering_and_usage_paths(self):
        from core import disk_monitor

        self.assertEqual(disk_monitor._volume_usage_path("C:"), "C:\\")
        self.assertEqual(disk_monitor._volume_usage_path("/"), "/")
        self.assertEqual(disk_monitor._volume_usage_path("/Volumes/Data"), "/Volumes/Data")

        self.assertFalse(disk_monitor._should_skip_posix_mount("/", "ext4", "linux"))
        self.assertFalse(disk_monitor._should_skip_posix_mount("/home", "ext4", "linux"))
        self.assertTrue(disk_monitor._should_skip_posix_mount("/proc", "proc", "linux"))
        self.assertTrue(disk_monitor._should_skip_posix_mount("/snap/core/1", "squashfs", "linux"))
        self.assertTrue(disk_monitor._should_skip_posix_mount("/run/user/1000", "tmpfs", "linux"))

        self.assertFalse(disk_monitor._should_skip_posix_mount("/", "apfs", "darwin"))
        self.assertFalse(disk_monitor._should_skip_posix_mount("/Volumes/SSD", "apfs", "darwin"))
        self.assertTrue(disk_monitor._should_skip_posix_mount("/System/Volumes/VM", "apfs", "darwin"))

    def test_project_storage_key_posix_and_windows(self):
        from core import wsl_manager

        self.assertEqual(wsl_manager.project_storage_key(r"D:\ROMs\Pixel"), "D:")
        self.assertEqual(wsl_manager.project_storage_key(r"\\wsl$\Ubuntu\home\dev\rom"), "WSL:Ubuntu")

        with patch("core.disk_monitor._posix_drive_keys", return_value=["/", "/Volumes/Work", "/mnt/data"]):
            self.assertEqual(wsl_manager.project_storage_key("/Volumes/Work/ROM1"), "/Volumes/Work")
            self.assertEqual(wsl_manager.project_storage_key("/mnt/data/android"), "/mnt/data")
            self.assertEqual(wsl_manager.project_storage_key("/Users/dev/Documents/ROM"), "/")


class NativeLibraryNamesAndModifiersTests(unittest.TestCase):
    def test_rust_blur_and_termcore_filenames(self):
        from tools.build_termcore import library_filename as term_name

        self.assertEqual(term_name("win32"), "termcore.dll")
        self.assertEqual(term_name("darwin"), "libtermcore.dylib")
        self.assertEqual(term_name("linux"), "libtermcore.so")

        cfg = Path(__file__).resolve().parents[1] / "native" / "rak_blur" / "nuitka-package.config.yml"
        text = cfg.read_text(encoding="utf-8")
        self.assertIn("when: 'win32'", text)
        self.assertIn("when: 'linux'", text)
        self.assertIn("when: 'macos'", text)

        try:
            from core.rust_blur import library_filename as blur_name
        except ImportError:
            self.skipTest("PySide6 not installed")
        self.assertEqual(blur_name("win32"), "rak_blur.dll")
        self.assertEqual(blur_name("darwin"), "librak_blur.dylib")
        self.assertEqual(blur_name("linux"), "librak_blur.so")

    def test_qtterm_macos_ctrl_meta_modifier_swap(self):
        try:
            from PySide6.QtCore import Qt
            from core import qtterm
        except ImportError:
            self.skipTest("PySide6 not installed")

        # Test modifier mapping helper directly without constructing QQuickPaintedItem
        fn = qtterm.Terminal._mods_of
        self.assertEqual(fn(None, Qt.KeyboardModifier.ControlModifier, platform="win32"), qtterm.TM_CTRL)
        self.assertEqual(fn(None, Qt.KeyboardModifier.MetaModifier, platform="win32"), qtterm.TM_SUPER)
        self.assertEqual(fn(None, Qt.KeyboardModifier.ControlModifier, platform="linux"), qtterm.TM_CTRL)
        # On macOS, Qt maps physical Ctrl -> MetaModifier and Cmd -> ControlModifier
        self.assertEqual(fn(None, Qt.KeyboardModifier.MetaModifier, platform="darwin"), qtterm.TM_CTRL)
        self.assertEqual(fn(None, Qt.KeyboardModifier.ControlModifier, platform="darwin"), qtterm.TM_SUPER)


class CliAndToolsCrossPlatformTests(unittest.TestCase):
    def test_rak_cli_posix_scripts(self):
        try:
            from core import rak_cli
        except ImportError:
            self.skipTest("PySide6 not installed")

        sh = rak_cli._rak_sh_content("/opt/rak/run")
        self.assertTrue(sh.startswith("#!/usr/bin/env sh\n"))
        self.assertIn("/opt/rak/run", sh)
        self.assertIn("--rak-cli", sh)

        shim = rak_cli._native_shim_sh_content("/opt/rak/run", "adb")
        self.assertTrue(shim.startswith("#!/usr/bin/env sh\n"))
        self.assertIn("--rak-cli adb", shim)

    def test_apktool_java_resolution_posix(self):
        try:
            from core import apktool_tool
        except ImportError:
            self.skipTest("PySide6 not installed")

        with tempfile.TemporaryDirectory(prefix="rak-java-") as tmp:
            bin_dir = os.path.join(tmp, "jdk", "bin")
            os.makedirs(bin_dir, exist_ok=True)
            fake_java = os.path.join(bin_dir, "java")
            Path(fake_java).write_text("#!/bin/sh\n", encoding="utf-8")

            with patch.object(apktool_tool.sys, "platform", "linux"), \
                 patch.object(apktool_tool, "resolve_external_binary", return_value=None), \
                 patch.dict(os.environ, {"JAVA_HOME": os.path.join(tmp, "jdk")}):
                self.assertEqual(apktool_tool.resolve_java(), fake_java)

    def test_unix_tools_macos_gnu_preference_and_bsd_fallback(self):
        from core import unix_tools

        with patch.object(unix_tools.sys, "platform", "darwin"), \
             patch.object(unix_tools.shutil, "which", side_effect=lambda n: "/opt/homebrew/bin/gsed" if n == "gsed" else "/usr/bin/sed"):
            self.assertEqual(unix_tools.resolve_tool("sed"), "/opt/homebrew/bin/gsed")

        # When BSD sed is used on macOS, --sandbox and -s must not be passed.
        captured = {}

        def fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            res = MagicMock()
            res.returncode = 0
            res.stdout = "ok\n"
            res.stderr = ""
            return res

        with patch.object(unix_tools.sys, "platform", "darwin"), \
             patch.object(unix_tools, "resolve_tool", return_value="/usr/bin/sed"), \
             patch.object(unix_tools, "is_gnu_tool", return_value=False), \
             patch("core.kitchen_process.run", side_effect=fake_run):
            out = unix_tools.sed("hi\n", ["s/hi/ok/"], separate=True)
            self.assertEqual(out, "ok\n")
            self.assertNotIn("--sandbox", captured["cmd"])
            self.assertNotIn("-s", captured["cmd"])

    def test_qemu_android_emu_subdirs_per_os(self):
        from core.vdev import qemu_runner

        with patch.object(qemu_runner.os, "name", "posix"), \
             patch.object(qemu_runner.sys, "platform", "darwin"), \
             patch.object(qemu_runner.platform, "machine", return_value="arm64"):
            self.assertEqual(qemu_runner._android_emu_subdirs(), ["darwin-aarch64", "darwin-x86_64"])

        with patch.object(qemu_runner.os, "name", "posix"), \
             patch.object(qemu_runner.sys, "platform", "linux"), \
             patch.object(qemu_runner.platform, "machine", return_value="x86_64"):
            self.assertEqual(qemu_runner._android_emu_subdirs(), ["linux-x86_64"])

    def test_gki_checkout_allows_macos_and_linux(self):
        from core import gki_checkout

        with patch.object(gki_checkout.sys, "platform", "darwin"):
            self.assertEqual(gki_checkout._linux_command(None), ["python3", "-u", "-c"])
        with patch.object(gki_checkout.sys, "platform", "linux"):
            self.assertEqual(gki_checkout._linux_command(None), ["python3", "-u", "-c"])

    def test_file_run_commands_per_os(self):
        from main_ext import file_run_commands

        with patch.object(file_run_commands.sys, "platform", "linux"), \
             patch.object(file_run_commands.shutil, "which", return_value="/usr/bin/python3"):
            self.assertFalse(file_run_commands.is_runnable_file("test.bat"))
            self.assertFalse(file_run_commands.is_runnable_file("test.cmd"))
            self.assertTrue(file_run_commands.is_runnable_file("test.py"))
            self.assertTrue(file_run_commands.is_runnable_file("test.sh"))
            cmd = file_run_commands.build_run_command("/tmp/my script.py")
            self.assertTrue(cmd.startswith("python3 "))
            self.assertIn("'", cmd)


class BuildSystemCrossPlatformTests(unittest.TestCase):
    def test_build_release_names_and_platform_lists(self):
        import build

        self.assertTrue(build.release_name("OPSC", target_platform="win32").endswith(".zip"))
        mac_name = build.release_name("OPSC", target_platform="darwin", machine="arm64")
        self.assertTrue(mac_name.endswith("-macos-arm64.tar.gz"))
        lin_name = build.release_name("OPSC", target_platform="linux", machine="x86_64")
        self.assertTrue(lin_name.endswith("-linux-x86_64.tar.gz"))

        win_mods = build.include_modules_for_platform("win32")
        lin_mods = build.include_modules_for_platform("linux")
        self.assertIn("multiprocessing.popen_spawn_win32", win_mods)
        self.assertNotIn("multiprocessing.popen_spawn_win32", lin_mods)
        self.assertIn("multiprocessing.popen_spawn_posix", lin_mods)

        self.assertIn("winpty", build.third_party_packages_for_platform("win32"))
        self.assertNotIn("winpty", build.third_party_packages_for_platform("linux"))
        self.assertNotIn("winpty", build.third_party_packages_for_platform("darwin"))

        self.assertEqual(build.terminal_native_libs_for_platform("win32"), ["termcore.dll"])
        self.assertEqual(build.terminal_native_libs_for_platform("darwin"), ["libtermcore.dylib"])
        self.assertEqual(build.terminal_native_libs_for_platform("linux"), ["libtermcore.so"])

    def test_build_nuitka_command_flags_linux_and_macos(self):
        import build

        with tempfile.TemporaryDirectory(prefix="rak-build-cmd-") as tmp:
            out = Path(tmp)
            with patch.object(build, "embedded_asset_entries", return_value=[]), \
                 patch.object(build, "_find_capstone_libs", return_value=[]):
                lin_cmd = build.nuitka_command(out, private_build=False, debug=False, jobs=4, target_platform="linux")
                self.assertIn("--output-filename=run", lin_cmd)
                self.assertFalse(any("--windows-" in arg for arg in lin_cmd))
                self.assertFalse(any("winpty" in arg for arg in lin_cmd))

                mac_cmd = build.nuitka_command(
                    out, private_build=False, debug=False, jobs=4,
                    target_platform="darwin", macos_app_bundle=True,
                )
                self.assertIn("--output-filename=run", mac_cmd)
                self.assertIn("--macos-create-app-bundle", mac_cmd)
                self.assertFalse(any("--windows-" in arg for arg in mac_cmd))

    def test_package_release_posix_tar_gz_and_windows_zip(self):
        import build

        with tempfile.TemporaryDirectory(prefix="rak-pkg-") as tmp:
            root = Path(tmp)
            # POSIX tar.gz
            dist_posix = root / "dist_posix"
            dist_posix.mkdir()
            (dist_posix / "run").write_bytes(b"#!/bin/sh\necho ok\n")
            tar_archive = root / "out" / "rak-linux.tar.gz"
            build.package_release(dist_posix, tar_archive, target_platform="linux")
            self.assertTrue(tar_archive.is_file())
            with tarfile.open(tar_archive, "r:gz") as tf:
                names = set(tf.getnames())
                self.assertIn("run", names)
                self.assertIn("rak", names)
                self.assertIn("SOURCE_CODE.txt", names)

            # Windows zip
            dist_win = root / "dist_win"
            dist_win.mkdir()
            (dist_win / "run.exe").write_bytes(b"MZ")
            zip_archive = root / "out" / "rak-win.zip"
            build.package_release(dist_win, zip_archive, target_platform="win32")
            self.assertTrue(zip_archive.is_file())
            with zipfile.ZipFile(zip_archive, "r") as zf:
                names = set(zf.namelist())
                self.assertIn("run.exe", names)
                self.assertIn("rak.bat", names)
                self.assertIn("SOURCE_CODE.txt", names)


class FsWatcherAndProjectLinksTests(unittest.TestCase):
    def test_fs_watcher_detects_file_creation(self):
        from core.fs_watcher import ProjectFsWatcher

        with tempfile.TemporaryDirectory(prefix="rak-watch-") as tmp:
            seen = threading.Event()
            captured = []

            def on_batch(events, overflowed):
                captured.extend(events)
                seen.set()

            watcher = ProjectFsWatcher(tmp, on_batch, debounce_sec=0.05)
            try:
                self.assertTrue(watcher.start())
                target = os.path.join(tmp, "hello.txt")
                Path(target).write_text("world\n", encoding="utf-8")
                self.assertTrue(seen.wait(timeout=4.0), "Watcher did not emit event for created file")
                self.assertTrue(any(os.path.basename(p) == "hello.txt" for p, _ in captured))
            finally:
                watcher.stop()

    def test_project_config_link_creation_and_safe_removal(self):
        from core.project_config import _remove_junction_or_stub, try_create_junction

        with tempfile.TemporaryDirectory(prefix="rak-link-") as tmp:
            target = os.path.join(tmp, "target_dir")
            os.makedirs(target)
            marker = os.path.join(target, "keep.txt")
            Path(marker).write_text("precious\n", encoding="utf-8")
            link = os.path.join(tmp, "link_dir")

            self.assertTrue(try_create_junction(link, target))
            self.assertTrue(os.path.isdir(link))
            self.assertTrue(os.path.isfile(os.path.join(link, "keep.txt")))

            _remove_junction_or_stub(link)
            self.assertFalse(os.path.lexists(link))
            self.assertTrue(os.path.isfile(marker))


@unittest.skipIf(sys.platform == "win32", "POSIX PTY runtime test runs on macOS/Linux")
class PosixPtyLiveTests(unittest.TestCase):
    def test_posix_conpty_and_disk_monitor_import_cleanly(self):
        from core import agent_conpty, disk_monitor, storage_detector

        self.assertFalse(agent_conpty.is_supported())
        rows = disk_monitor.list_disk_usage(timeout=2.0)
        self.assertTrue(len(rows) >= 1, "Expected at least root '/' volume on POSIX")
        self.assertIn("/", [r["key"] for r in rows])
        # Should not raise on POSIX paths
        _ = storage_detector.is_rotational_drive("/")

    def test_posix_pty_executes_command_and_reads_output(self):
        from core.posix_pty import PosixPty

        pty_obj = PosixPty(80, 24)
        self.assertTrue(pty_obj.spawn("/bin/sh", cmdline="-c 'echo RAK_POSIX_PTY_OK'", cwd=os.getcwd()))
        collected = []
        deadline = time.monotonic() + 5.0
        try:
            while time.monotonic() < deadline:
                chunk = pty_obj.read(blocking=False)
                if chunk:
                    collected.append(chunk)
                    if "RAK_POSIX_PTY_OK" in "".join(collected):
                        break
                elif pty_obj.iseof() or not pty_obj.isalive():
                    break
                else:
                    time.sleep(0.02)
            pty_obj.set_size(100, 30)
        finally:
            pty_obj.cancel_io()
        self.assertIn("RAK_POSIX_PTY_OK", "".join(collected))


if __name__ == "__main__":
    unittest.main()
