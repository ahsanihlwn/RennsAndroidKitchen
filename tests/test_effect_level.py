# SPDX-License-Identifier: MPL-2.0
"""Effect-level preference migration and shared native dropdown behavior."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

if os.environ.get("RAK_TEST_SKIA_GPU") != "1":
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtWidgets import QApplication, QWidget
from main_ext import class_ThemeUi as theme_module
from app_ext import class_EffectLevelDropdown as control


class EffectLevelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.settings = Path(self.directory.name, "settings.json")
        self.patches = [
            patch.object(theme_module, "_USER_DIR", self.directory.name),
            patch.object(theme_module, "_SETTINGS_FILE", str(self.settings)),
            patch.object(theme_module, "_THEME_FILE", str(Path(self.directory.name, "theme.json"))),
            patch.object(theme_module, "_migrate_legacy_user_state"),
        ]
        for item in self.patches:
            item.start()
        self.theme = theme_module._Theme()
        self.root = QWidget()
        self.root.setAttribute(Qt.WA_DontShowOnScreen)
        self.root.resize(500, 400)
        self.root.show()

    def tearDown(self):
        self.root.hide()
        self.root.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        for item in reversed(self.patches):
            item.stop()
        self.directory.cleanup()

    def load(self, data):
        self.settings.write_text(json.dumps(data), encoding="utf-8")
        self.theme.load()

    def test_legacy_boolean_migrates_to_low_or_medium_without_enabling_live(self):
        for saved, expected in ((False, "low"), (True, "medium")):
            self.load({"blur_enabled": saved})
            self.assertEqual(self.theme.effect_level, expected)
            self.assertEqual(self.theme.blur_enabled, saved)
            self.assertFalse(self.theme.live_blur_enabled)
        self.load({"blur_enabled": False, "effect_level": "invalid"})
        self.assertEqual(self.theme.effect_level, "low")

    def test_explicit_level_takes_precedence_and_all_levels_persist(self):
        self.load({"effect_level": "high", "blur_enabled": False,
                   "animations_enabled": False, "tree_view_mode": "single_dir",
                   "project_drive_filters": {"D:": False}})
        self.assertTrue(self.theme.live_blur_enabled)
        for level in ("low", "medium", "high"):
            self.theme.set_effect_level(level)
            saved = json.loads(self.settings.read_text())
            self.assertEqual(saved["effect_level"], level)
            self.assertEqual(saved["blur_enabled"], level != "low")
            self.assertFalse(saved["animations_enabled"])
            self.assertEqual(saved["tree_view_mode"], "single_dir")
            self.assertEqual(saved["project_drive_filters"], {"D:": False})
            restored = theme_module._Theme()
            restored.load()
            self.assertEqual(restored.effect_level, level)

    def test_invalid_or_duplicate_selection_does_not_save_or_notify(self):
        changes = []
        self.theme.on_effect_level_change(changes.append)
        with patch.object(self.theme, "_save_settings") as save:
            self.theme.set_effect_level("bogus")
            self.theme.set_effect_level("medium")
            save.assert_not_called()
            self.theme.set_effect_level("HIGH")
            save.assert_called_once()
        self.assertEqual(changes, ["high"])
        self.assertTrue(self.theme.blur_enabled)
        self.assertTrue(self.theme.live_blur_enabled)

    def test_shared_dropdowns_sync_and_remove_listeners_when_destroyed(self):
        with patch.object(control, "theme", self.theme):
            first = control.EffectLevelDropdown(self.root)
            second = control.EffectLevelDropdown(self.root)
            second.move(0, 40)
            first.show()
            second.show()
            self.assertEqual(first.currentText(), "Medium")
            self.theme.set_effect_level("high")
            self.assertEqual((first.currentText(), second.currentText()), ("High", "High"))
            self.assertEqual(first.objectName(), "effect_level_dropdown")
            first.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
            self.assertEqual(len(self.theme._effect_level_listeners), 1)
            self.theme.set_effect_level("low")
            self.assertEqual(second.currentText(), "Low")
            second.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
            self.assertFalse(self.theme._effect_level_listeners)

    def test_effect_control_uses_global_popup_and_current_preference(self):
        with patch.object(control, "theme", self.theme), \
             patch.object(control, "show_choice_popup") as popup:
            button = control.EffectLevelDropdown(self.root)
            button._open_menu()
            popup.assert_called_once_with(button, control.EFFECT_LEVELS,
                                          "medium", self.theme.set_effect_level)


if __name__ == "__main__":
    unittest.main()
