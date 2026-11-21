# SPDX-License-Identifier: MPL-2.0
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image
from PySide6.QtCore import QObject, Signal

import core.cache as core_cache
import rennsproject.assets as renns_assets
import rennsproject.avatar.handler as avatar_handler
import rennsproject.core.login_auth as login_auth
from ui.accounts import ProfileModel


class AvatarActiveCacheTests(unittest.TestCase):
    def test_bundled_avatars_are_full_resolution(self):
        src_dir = renns_assets.avatar_source_dir()
        keys = avatar_handler.list_avatars()
        self.assertEqual(len(keys), 13)
        for key in keys:
            path = src_dir / f"{key}.png"
            self.assertTrue(path.is_file(), f"Missing avatar asset: {path}")
            with Image.open(path) as im:
                self.assertEqual(im.size, (1254, 1254))

    def test_source_cache_version_restore_and_active_resize(self):
        with tempfile.TemporaryDirectory(prefix="rak-avatar-cache-") as tmp:
            asset_cache = Path(tmp) / "assets"
            avatar_cache = asset_cache / "avatar"
            active_cache = avatar_cache / "active"

            with patch.object(renns_assets, "ASSET_CACHE_DIR", str(asset_cache)), \
                 patch.object(avatar_handler, "AVATAR_DIR", str(avatar_cache)), \
                 patch.object(avatar_handler, "AVATAR_ACTIVE_DIR", str(active_cache)):
                # 1. Initial sync at 720p -> full-res in avatar/, 128x128 in avatar/active/
                r1 = renns_assets.ensure_avatar_cache(screen_width=1280, screen_height=720, dpr=1.0)
                self.assertEqual(r1["target_dim"], 128)
                self.assertEqual(r1["processed"], 13)
                self.assertEqual(
                    (avatar_cache / ".avatar_cache_version").read_text(encoding="utf-8"),
                    renns_assets.AVATAR_CACHE_VERSION,
                )
                with Image.open(avatar_cache / "000.png") as full_im:
                    self.assertEqual(full_im.size, (1254, 1254))
                with Image.open(active_cache / "000.png") as active_im:
                    self.assertEqual(active_im.size, (128, 128))
                self.assertEqual(
                    os.path.normpath(avatar_handler.avatar_path("000")),
                    os.path.normpath(str(active_cache / "000.png")),
                )

                # 2. Same version & same screen -> source cache stays quiet, active reuses all
                sentinel_mtime = (avatar_cache / "000.png").stat().st_mtime_ns
                r2 = renns_assets.ensure_avatar_cache(screen_width=1280, screen_height=720, dpr=1.0)
                self.assertEqual(r2["processed"], 0)
                self.assertEqual(r2["reused"], 13)
                self.assertEqual((avatar_cache / "000.png").stat().st_mtime_ns, sentinel_mtime)

                # 3. Missing image in source cache -> restored from bundled source; others untouched
                (avatar_cache / "012.png").unlink()
                (active_cache / "012.png").unlink()
                r3 = renns_assets.ensure_avatar_cache(screen_width=1280, screen_height=720, dpr=1.0)
                self.assertTrue((avatar_cache / "012.png").is_file())
                self.assertEqual(r3["processed"], 1)
                self.assertEqual(r3["reused"], 12)
                self.assertEqual((avatar_cache / "000.png").stat().st_mtime_ns, sentinel_mtime)

                # 4. Restart on 4K display (1920x1080 @ 2.0 DPR) -> active/ updates to 384x384
                r4 = renns_assets.ensure_avatar_cache(screen_width=1920, screen_height=1080, dpr=2.0)
                self.assertEqual(r4["target_dim"], 384)
                self.assertEqual(r4["processed"], 13)
                with Image.open(active_cache / "000.png") as active_4k:
                    self.assertEqual(active_4k.size, (384, 384))
                # Source cache remains full 1254x1254
                with Image.open(avatar_cache / "000.png") as full_im_after:
                    self.assertEqual(full_im_after.size, (1254, 1254))

                # 5. Restart on 1080x2340 display -> active/ updates to 192x192
                r5 = renns_assets.ensure_avatar_cache(screen_width=1080, screen_height=2340, dpr=1.0)
                self.assertEqual(r5["target_dim"], 192)
                with Image.open(active_cache / "000.png") as active_tall:
                    self.assertEqual(active_tall.size, (192, 192))

                # 6. avatar/assets/google.png is cached and resized cleanly by Rust display logic
                badge_src = avatar_cache / "assets" / "google.png"
                badge_active = avatar_cache / "assets" / "active" / "google.png"
                self.assertTrue(badge_src.is_file())
                self.assertTrue(badge_active.is_file())
                with Image.open(badge_src) as raw_badge:
                    self.assertEqual(raw_badge.size, (120, 123))
                with Image.open(badge_active) as active_badge:
                    self.assertEqual(active_badge.size, (70, 72))

    def test_core_cache_rebuild_preserves_avatar_dir(self):
        with tempfile.TemporaryDirectory(prefix="rak-core-cache-") as tmp:
            root = Path(tmp)
            cache_dir = root / "cache"
            asset_cache = cache_dir / "assets"
            avatar_dir = asset_cache / "avatar"
            avatar_dir.mkdir(parents=True)
            google_png = avatar_dir / "google.png"
            google_png.write_bytes(b"google-avatar-bytes")

            with patch.object(core_cache, "CACHE_DIR", str(cache_dir)), \
                 patch.object(core_cache, "ASSET_CACHE_DIR", str(asset_cache)), \
                 patch.object(core_cache, "CACHE_MARK", str(asset_cache / f".ready_{core_cache.CACHE_VERSION}")), \
                 patch.object(core_cache, "REQUIRED_DIRS", (str(asset_cache / "icons"), str(asset_cache / "textures"))), \
                 patch.object(core_cache, "ensure_app_layout", lambda: asset_cache.mkdir(parents=True, exist_ok=True)), \
                 patch.object(core_cache, "hide_folder", lambda _p: None):
                core_cache.ensure_cache()
                self.assertTrue(google_png.is_file())
                self.assertEqual(google_png.read_bytes(), b"google-avatar-bytes")
                self.assertTrue((asset_cache / "icons").is_dir())
                self.assertTrue((asset_cache / "textures").is_dir())

    def test_google_and_default_avatars_are_strictly_separated(self):
        with tempfile.TemporaryDirectory(prefix="rak-google-avatar-") as tmp:
            asset_cache = Path(tmp) / "assets"
            avatar_cache = asset_cache / "avatar"
            active_cache = avatar_cache / "active"

            with patch.object(renns_assets, "ASSET_CACHE_DIR", str(asset_cache)), \
                 patch.object(avatar_handler, "AVATAR_DIR", str(avatar_cache)), \
                 patch.object(avatar_handler, "AVATAR_ACTIVE_DIR", str(active_cache)):
                renns_assets.ensure_avatar_cache(screen_width=1280, screen_height=720, dpr=1.0)
                Image.new("RGBA", (256, 256), (12, 120, 240, 255)).save(avatar_cache / "google.png")
                renns_assets.sync_active_avatars_for_screen(screen_width=1280, screen_height=720, dpr=1.0)

                google_acc = {"id": "u1", "auth_provider": "google", "avatar": "google"}
                email_acc = {"id": "u2", "auth_provider": "email", "avatar": "000"}

                # '000' must resolve to 000.png even on a Google account (never overwritten by google.png)
                self.assertEqual(
                    os.path.normpath(avatar_handler.resolve_avatar_path("000", google_acc)),
                    os.path.normpath(str(active_cache / "000.png")),
                )
                self.assertEqual(
                    os.path.normpath(avatar_handler.resolve_avatar_path("google", google_acc)),
                    os.path.normpath(str(active_cache / "google.png")),
                )

                # list_avatars exposes 'google' only for Google accounts
                google_list = avatar_handler.list_avatars(google_acc)
                self.assertEqual(google_list[0], "google")
                self.assertIn("000", google_list)
                email_list = avatar_handler.list_avatars(email_acc)
                self.assertNotIn("google", email_list)
                self.assertEqual(email_list[0], "000")

                class DummyAccounts(QObject):
                    changed = Signal()
                    account = dict(google_acc)
                    profile_provider = None

                accounts = DummyAccounts()
                profile = ProfileModel(accounts)
                self.assertEqual(profile.avatars[0]["key"], "google")
                profile.begin()
                profile.chooseAvatar("000")
                self.assertEqual(profile.draft["avatar"], "000")
                self.assertIn("/000.png", profile.preview)
                self.assertIn("/active/000.png", profile.preview)
                self.assertIn("/000.png", profile.previewHd)
                self.assertNotIn("/active/", profile.previewHd)
                self.assertEqual(
                    os.path.normpath(avatar_handler.resolve_avatar_hd_path("000", google_acc)),
                    os.path.normpath(str(avatar_cache / "000.png")),
                )
                profile.chooseAvatar("google")
                self.assertEqual(profile.draft["avatar"], "google")
                self.assertIn("/google.png", profile.preview)
                self.assertNotIn("/active/", profile.previewHd)
                self.assertIn("/assets/active/google.png", profile.googleBadgeUrl)

                # Logout clears cached google.png and removes 'google' from picker
                avatar_handler.clear_google_avatar()
                accounts.account = dict(email_acc)
                accounts.changed.emit()
                self.assertFalse((avatar_cache / "google.png").exists())
                self.assertFalse((active_cache / "google.png").exists())
                self.assertNotIn("google", [row["key"] for row in profile.avatars])

    def test_finalize_and_restore_session_google_profile_table(self):
        from types import SimpleNamespace

        inserted_rows = []

        class FakeQuery:
            def __init__(self, state):
                self.state = state
                self.op = "select"
                self.payload = None

            def select(self, *_args):
                self.op = "select"
                return self

            def insert(self, payload):
                self.op = "insert"
                self.payload = dict(payload)
                inserted_rows.append(self.payload)
                self.state["rows"] = [self.payload]
                return self

            def eq(self, *_args):
                return self

            def single(self):
                self.op = "single"
                return self

            def execute(self):
                rows = self.state["rows"]
                if self.op == "single":
                    return SimpleNamespace(data=rows[0])
                return SimpleNamespace(data=rows)

        class FakeSupabase:
            def __init__(self, state):
                self.state = state
                self.postgrest = SimpleNamespace(auth=lambda _tok: None)
                self.auth = SimpleNamespace(
                    refresh_session=lambda _rt: SimpleNamespace(
                        session=SimpleNamespace(
                            access_token="acc-tok",
                            refresh_token="ref-tok-2",
                            user=self.state["user"],
                        )
                    )
                )

            def table(self, name):
                assert name == "developer_profile"
                return FakeQuery(self.state)

        google_user = SimpleNamespace(
            id="uid-google-1",
            email="dev@example.com",
            app_metadata={"provider": "google", "providers": ["google"]},
            user_metadata={"full_name": "Dev Google", "avatar_url": "https://example.invalid/photo.jpg"},
            identities=[],
        )
        state = {"rows": [], "user": google_user}
        fake_sb = FakeSupabase(state)

        with patch("rennsproject.core.supabase_client.get_supabase", return_value=fake_sb), \
             patch("rennsproject.core.supabase_client._on_login_success", return_value=None), \
             patch("rennsproject.core.secure_store.save_session", return_value=None), \
             patch("rennsproject.core.secure_store.load_session", return_value={"refresh_token": "ref-tok-1"}), \
             patch("rennsproject.core.login_auth.load_pending_signup", return_value=None), \
             patch("rennsproject.avatar.handler.save_google_avatar", return_value=True) as save_g:
            # 1. New Google user inserts avatar='google' into developer_profile
            created = login_auth._finalize_session("acc-tok", google_user, is_google=True, refresh_token="ref-tok-1")
            self.assertEqual(inserted_rows[0]["avatar"], "google")
            self.assertEqual(created["avatar"], "google")
            self.assertEqual(created["auth_provider"], "google")
            save_g.assert_called_once()

            # 2. Existing Google user who chose '000' keeps '000' on restore_session, with auth_provider='google'
            state["rows"] = [{"id": "uid-google-1", "username": "Dev Google", "employee_status": "Developer", "avatar": "000", "dev_id": "DEV-123", "is_blocked": False}]
            restored = login_auth.restore_session()
            self.assertEqual(restored["avatar"], "000")
            self.assertEqual(restored["auth_provider"], "google")
            self.assertEqual(save_g.call_count, 2)


if __name__ == "__main__":
    unittest.main()
