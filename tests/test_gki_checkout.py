# SPDX-License-Identifier: MPL-2.0
import html
import hashlib
from io import BytesIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from core import gki_checkout, kernel_version
from core.command_context import OperationError


COMMIT = "b575a0b6e6473964ce34c8c826208c18ca54c498"
CLANG_COMMIT = "9a98ca4072ec000f301bd3ece481ea2baca76ca5"
MANIFEST = f"""<manifest>
  <remote name="aosp" fetch="https://android.googlesource.com/" />
  <default remote="aosp" revision="main" />
  <project name="kernel/common" path="common" revision="{COMMIT}" />
  <project name="platform/prebuilts/clang/host/linux-x86"
      path="prebuilts/clang/host/linux-x86" revision="{CLANG_COMMIT}" />
</manifest>""".encode()


class GkiReleaseTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        cache = patch.object(gki_checkout, "GKI_CACHE_INDEX", Path(folder.name) / "gki")
        cache.start()
        self.addCleanup(cache.stop)
        self.info = kernel_version.KernelVersionInfo(
            "boot.img", is_kernel=True, base_version="6.12.38", aosp_branch="android16-6.12",
            git_commit=COMMIT[:12], arch="arm64", android_build_id="14355190",
        )
        entry = {"tag": "android16-6.12-2025-09_r8", "sha1": COMMIT, "kernel_bid": "14355190"}
        catalog = {"name": "android16-6.12", "branches": [{"kernel_version": "6.12.38", "releases": [entry]}]}
        self.page = f"<pre><code>{html.escape(json.dumps(catalog))}</code></pre>".encode()

    def test_missing_wsl_distro_reports_manual_activation(self):
        with patch.object(gki_checkout.sys, "platform", "win32"), \
             patch.object(gki_checkout.wsl_manager, "list_distros", return_value=[]), \
             patch.object(gki_checkout.kitchen_process, "run") as run:
            with self.assertRaisesRegex(OperationError, "Activate WSL.*wsl --install"):
                gki_checkout.require_linux_tools(None, "python3", "git")
        run.assert_not_called()

    def test_matching_build_resolves_the_pinned_manifest(self):
        with patch.object(gki_checkout, "_download", side_effect=[self.page, MANIFEST]) as download:
            release, reason = gki_checkout.resolve_release(self.info)
        self.assertEqual(reason, "")
        self.assertEqual(release.commit, COMMIT)
        self.assertEqual(release.project_count, 2)
        self.assertTrue(download.call_args.args[0].endswith("/manifest_14355190.xml"))

    def test_same_build_number_with_different_commit_gets_vendor_guidance(self):
        self.info.git_commit = "000000000000"
        with patch.object(gki_checkout, "_download", side_effect=[self.page, MANIFEST]) as download:
            release, reason = gki_checkout.resolve_release(self.info)
        self.assertIsNone(release)
        self.assertIn("did not match", reason)
        self.assertEqual(download.call_count, 2)
        for filename in (".config", "autoconf.h", "compile_commands.json"):
            self.assertIn(filename, gki_checkout.vendor_requirements(self.info, reason))

    def test_verified_manifest_is_reusable_without_network(self):
        with patch.object(gki_checkout, "_download", side_effect=[self.page, MANIFEST]):
            release, _ = gki_checkout.resolve_release(self.info)
        gki_checkout._remember_release(release)
        with patch.object(gki_checkout, "_download") as download:
            reused, reason = gki_checkout.resolve_release(self.info)
        self.assertEqual(reason, "")
        self.assertEqual(reused, release)
        download.assert_not_called()

    def test_older_ci_manifest_supports_legacy_clang_layout_and_offline_reuse(self):
        manifest = MANIFEST.replace(b'path="prebuilts/clang/', b'path="prebuilts-master/clang/')
        with patch.object(gki_checkout, "_download", side_effect=[None, manifest]):
            release, reason = gki_checkout.resolve_release(self.info)
        self.assertEqual(reason, "")
        self.assertEqual(release.commit, COMMIT)
        self.assertEqual(release.display_name, self.info.aosp_branch)
        self.assertEqual(release.clang_path, "prebuilts-master/clang/host/linux-x86")
        gki_checkout._remember_release(release)
        with patch.object(gki_checkout, "_download") as download:
            self.assertEqual(gki_checkout.resolve_release(self.info), (release, ""))
        download.assert_not_called()

    def test_manifest_must_pin_the_release_and_toolchain(self):
        variants = (
            MANIFEST.replace(COMMIT.encode(), CLANG_COMMIT.encode()),
            MANIFEST.replace(CLANG_COMMIT.encode(), b"main"),
            MANIFEST.replace(b"path=\"common\"", b"path=\"../common\""),
        )
        for manifest in variants:
            with self.subTest(manifest=manifest), self.assertRaises(OperationError):
                gki_checkout._validate_manifest(manifest, COMMIT)

    def test_system_map_requires_an_identical_official_image_and_can_be_reused_offline(self):
        image = b"example-image"
        digest = hashlib.sha256(image).hexdigest()
        symbols = b"ffffffc008000000 T _text\n"
        with patch.object(gki_checkout, "_download", side_effect=[self.page, MANIFEST, symbols]) as download:
            with patch.object(gki_checkout, "urlopen", side_effect=[BytesIO(b"different-IMG"), BytesIO(image)]):
                self.assertIsNone(gki_checkout.analysis_system_map(self.info, digest, len(image)))
                self.assertEqual(download.call_count, 2)
                download.side_effect = [self.page, MANIFEST, symbols]
                path = gki_checkout.analysis_system_map(self.info, digest, len(image))
        self.assertEqual(path.read_bytes(), symbols)
        with patch.object(gki_checkout, "urlopen") as open_url:
            self.assertEqual(gki_checkout.analysis_system_map(self.info, digest, len(image), offline=True), path)
        open_url.assert_not_called()
