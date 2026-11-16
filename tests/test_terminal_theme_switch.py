# SPDX-License-Identifier: MPL-2.0
import json
import unittest
from unittest.mock import MagicMock

from main_ext.class_ThemeUi import theme, _PALETTES
from ui.presentation import LauncherModel
from ui.terminal import TerminalSession
from tests.test_qtquick_sessions import FakePty


class TerminalThemeSwitchTests(unittest.TestCase):
    def setUp(self):
        self._initial_theme = theme.name

    def tearDown(self):
        theme.set(self._initial_theme)

    def test_theme_switch_updates_preferences_and_terminal_theme(self):
        theme.set("default")
        model = LauncherModel(palette="default")
        self.addCleanup(model.deleteLater)

        session = TerminalSession(manager=FakePty())
        self.addCleanup(session.stop)

        theme_changes = []
        session.bridge.themeChanged.connect(lambda val: theme_changes.append(json.loads(val)))

        # 1. Apply initial theme
        session.applyTheme(model.colors)
        self.assertFalse(session._theme["is_light"])
        self.assertEqual(session._theme["theme"]["background"], _PALETTES["default"]["BG"])
        self.assertIn("brightWhite", session._theme["theme"])
        self.assertEqual(session._theme["theme"]["brightWhite"], "#ffffff")

        # 2. Simulate web terminal ready
        session.bridge.onTerminalReady(80, 24)
        self.assertTrue(len(theme_changes) >= 1)
        initial_payload = theme_changes[-1]
        self.assertEqual(initial_payload["bg"], _PALETTES["default"]["BG"])
        self.assertFalse(initial_payload["is_light"])

        # 3. Connect model colorsChanged to session.applyTheme (matching WorkspaceSession._theme_changed)
        model.colorsChanged.connect(lambda: session.applyTheme(model.colors))

        # 4. Switch theme globally to light
        theme.set("light")

        # Verify LauncherModel dynamically synced with global theme change
        self.assertEqual(model.paletteName, "light")
        self.assertTrue(model.colors.get("is_light"))

        # Verify terminal session theme was updated with light-friendly colors
        self.assertTrue(session._theme["is_light"])
        self.assertEqual(session._theme["theme"]["background"], _PALETTES["light"]["BG"])
        self.assertEqual(session._theme["theme"]["brightWhite"], "#0f172a")

        # Verify themeChanged signal delivered the new theme payload
        latest_payload = theme_changes[-1]
        self.assertEqual(latest_payload["bg"], _PALETTES["light"]["BG"])
        self.assertTrue(latest_payload["is_light"])
        self.assertEqual(latest_payload["theme"]["brightWhite"], "#0f172a")

        # 5. Switch to dark_ocean
        theme.set("dark_ocean")
        self.assertEqual(model.paletteName, "dark_ocean")
        self.assertFalse(session._theme["is_light"])
        self.assertEqual(session._theme["theme"]["background"], _PALETTES["dark_ocean"]["BG"])
        self.assertEqual(session._theme["theme"]["brightWhite"], "#ffffff")


if __name__ == "__main__":
    unittest.main()
