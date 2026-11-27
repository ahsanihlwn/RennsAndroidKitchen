# SPDX-License-Identifier: MPL-2.0
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.kernel_version import (
    KernelVersionInfo,
    parse_kernel_banner,
    inspect_kernel,
    decompress_kernel_stream,
    ARM64_MAGIC,
    ARM64_MAGIC_OFF,
)
from core.kernel_source_provider import (
    KernelSourceProvider,
    get_default_cache_dir,
)


class TestKernelVersionAndProvider(unittest.TestCase):
    def test_parse_aosp_kernel_banner(self):
        banner = (
            "Linux version 5.10.168-android12-9-00001-g38fa741d44fa "
            "(build-user@build-host) (Android (8490178, based on r450784d) clang version 14.0.6) "
            "#1 SMP PREEMPT Wed Feb 15 12:30:00 UTC 2023"
        )
        info = KernelVersionInfo(file_path="mock/kernel")
        parse_kernel_banner(banner, info)

        self.assertEqual(info.version_string, "5.10.168-android12-9-00001-g38fa741d44fa")
        self.assertEqual(info.base_version, "5.10.168")
        self.assertEqual(info.major, 5)
        self.assertEqual(info.minor, 10)
        self.assertEqual(info.patch, 168)
        self.assertEqual(info.short_version, "5.10")
        self.assertEqual(info.android_release, "android12")
        self.assertEqual(info.aosp_branch, "android12-5.10")
        self.assertEqual(info.git_commit, "38fa741d44fa")
        self.assertIn("clang version 14.0.6", info.compiler)

    def test_parse_mainline_kernel_banner(self):
        banner = (
            "Linux version 6.1.75 (builder@kernel.org) (gcc version 12.2.0) "
            "#1 SMP PREEMPT_DYNAMIC Thu Jan 25 10:00:00 UTC 2024"
        )
        info = KernelVersionInfo(file_path="mock/kernel")
        parse_kernel_banner(banner, info)

        self.assertEqual(info.base_version, "6.1.75")
        self.assertEqual(info.major, 6)
        self.assertEqual(info.minor, 1)
        self.assertEqual(info.patch, 75)
        self.assertEqual(info.short_version, "6.1")
        self.assertEqual(info.aosp_branch, "linux-6.1.y")
        self.assertEqual(info.git_commit, "")

    def test_inspect_kernel_binary_with_arm64_header(self):
        with tempfile.NamedTemporaryFile(delete=False) as f:
            buf = bytearray(4096)
            # Insert ARM64 magic at offset 56
            buf[ARM64_MAGIC_OFF : ARM64_MAGIC_OFF + 4] = ARM64_MAGIC
            # Insert linux banner in rodata section
            banner = b"Linux version 5.15.78-android13-2-g12345678 (builder@host) (clang version 15.0.0) #1\n\x00"
            buf[1024 : 1024 + len(banner)] = banner
            f.write(buf)
            temp_path = f.name

        try:
            info = inspect_kernel(temp_path)
            self.assertTrue(info.is_kernel)
            self.assertEqual(info.arch, "arm64")
            self.assertEqual(info.base_version, "5.15.78")
            self.assertEqual(info.aosp_branch, "android13-5.15")
            self.assertEqual(info.git_commit, "12345678")
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)

    def test_inspection_reports_unavailable_codec_without_using_partial_banner(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "Image.lz4"
            path.write_bytes(b"\x02\x21\x4c\x18Linux version 6.12.38 (mock) (clang 19)\0")
            with patch("core.kernel_version.decompress_kernel_stream", side_effect=lambda data, **kwargs: data):
                info = inspect_kernel(path)
            self.assertFalse(info.is_kernel)
            self.assertIn("Install the Python lz4 package", info.error)

    def test_kernel_source_provider_local_resolution(self):
        with tempfile.TemporaryDirectory() as local_src:
            # Create mock source tree: sound/usb/mixer.c
            sub_dir = Path(local_src) / "sound" / "usb"
            sub_dir.mkdir(parents=True)
            mock_c = (
                "// Mock mixer.c\n"
                "#include <linux/kernel.h>\n\n"
                "static int snd_usb_mixer_controls(struct usb_mixer_interface *mixer)\n"
                "{\n"
                "    pr_err(\"cannot malloc mixer\\n\");\n"
                "    return 0;\n"
                "}\n"
            )
            (sub_dir / "mixer.c").write_text(mock_c, encoding="utf-8")

            info = KernelVersionInfo(
                file_path="mock",
                base_version="5.10.168",
                short_version="5.10",
                aosp_branch="android12-5.10",
            )
            provider = KernelSourceProvider(info, local_source_dir=local_src, offline_mode=True)

            # Test resolving file
            content = provider.get_source_file("sound/usb/mixer.c")
            self.assertIsNotNone(content)
            self.assertIn("snd_usb_mixer_controls", content)

            # Test finding function by anchor string
            res = provider.find_function_by_anchor_string("sound/usb/mixer.c", "cannot malloc mixer")
            self.assertIsNotNone(res)
            self.assertEqual(res["name"], "snd_usb_mixer_controls")
            self.assertEqual(res["start_line"], 4)
            self.assertIn("return 0;", res["source_code"])

            # Test finding function by line number (e.g. line 6 inside function)
            res_line = provider.find_function_by_line("sound/usb/mixer.c", 6)
            self.assertIsNotNone(res_line)
            self.assertEqual(res_line["name"], "snd_usb_mixer_controls")


    def test_branch_cache_does_not_shadow_other_commits(self):
        with tempfile.TemporaryDirectory() as cache:
            first = KernelVersionInfo("first", aosp_branch="android16-6.12", git_commit="01234567")
            second = KernelVersionInfo("second", aosp_branch="android16-6.12", git_commit="76543210")
            path = "lib/math/int_sqrt.c"
            first_provider = KernelSourceProvider(first, cache_dir=cache)
            with patch.object(first_provider, "_fetch_remote", side_effect=[None, "branch source"]):
                self.assertEqual(first_provider.get_source_file(path), "branch source")
            self.assertIn("/refs/heads/android16-6.12/", first_provider.source_origin(path))

            second_provider = KernelSourceProvider(second, cache_dir=cache)
            with patch.object(second_provider, "_fetch_remote", return_value="commit source") as fetch:
                self.assertEqual(second_provider.get_source_file(path), "commit source")
                self.assertIn("/76543210/", fetch.call_args.args[0])
            offline = KernelSourceProvider(second, cache_dir=cache, offline_mode=True)
            with patch.object(offline, "_fetch_remote") as fetch:
                self.assertEqual(offline.get_source_file(path), "commit source")
                self.assertIn("/76543210/", offline.source_origin(path))
                self.assertIsNone(offline.get_source_file("lib/other.c"))
                fetch.assert_not_called()

    def test_source_reference_formats_the_loaded_origin_without_version_tag(self):
        from core.binary_reconstruct import reconstruct_file_content

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "probe.c"
            path.write_text("static int probe(void)\n{\n    return 1;\n}\n", encoding="utf-8")
            provider = KernelSourceProvider(KernelVersionInfo("fixture"), local_source_dir=folder, offline_mode=True)
            report = {}
            output, _, _ = reconstruct_file_content(
                "Image", {}, "probe.c", locations=[], func_addrs=[], line_numbers=[3],
                source_provider=provider, report=report,
            )
            self.assertIn("return 1;", output)
            self.assertEqual(report["source_reference"]["location"], str(path.resolve()))


if __name__ == "__main__":
    unittest.main()
