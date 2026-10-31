# SPDX-License-Identifier: MPL-2.0
import unittest
from ui.presentation import LauncherModel


class PresentationTests(unittest.TestCase):
    def test_panel_border_tracks_palette_and_effect_without_global_theme(self):
        from PySide6.QtGui import QColor
        from main_ext.class_ThemeUi import theme
        before = theme.name, theme.effect_level
        model = LauncherModel(palette='light')
        notifications = []
        model.panelBorderChanged.connect(lambda: notifications.append(model.panelBorder))
        self.assertEqual(QColor(model.panelBorder).getRgb(), (0, 0, 0, 26))
        model.chooseSetting('effects', 'high')
        self.assertEqual(QColor(model.panelBorder).getRgb(), (0, 0, 0, 13))
        model.choosePalette('dark_ocean')
        self.assertEqual(QColor(model.panelBorder).getRgb(), (255, 255, 255, 13))
        model.chooseSetting('effects', 'low')
        self.assertEqual(QColor(model.panelBorder).getRgb(), (255, 255, 255, 26))
        self.assertEqual(len(notifications), 3)
        self.assertEqual((theme.name, theme.effect_level), before)

    def test_search_keeps_pinned_order_and_full_records(self):
        model = LauncherModel(projects=[
            {"project_name": "Pixel", "path": "C:/Pixel", "pinned": False},
            {"project_name": "Pixel pinned", "path": "D:/Pixel", "pinned": True},
            {"project_name": "Other", "path": "C:/Other"},
        ])
        model.search("PIXEL")
        self.assertEqual([row["path"] for row in model.projects], ["D:/Pixel", "C:/Pixel"])

    def test_settings_return_to_origin_page(self):
        model = LauncherModel()
        model.navigate("create")
        model.navigate("settings")
        model.back()
        self.assertEqual(model.page, "create")
        model.back()
        self.assertEqual(model.page, "projects")

    def test_presentation_changes_emit_commands_without_changing_global_theme(self):
        from main_ext.class_ThemeUi import theme
        before = theme.name, theme.effect_level, theme.animations_enabled
        model = LauncherModel()
        commands = []
        model.command.connect(lambda name, data: commands.append((name, data)))
        model.choosePalette("dark_green")
        model.chooseSetting("effects", "high")
        model.chooseSetting("animations", False)
        self.assertEqual((theme.name, theme.effect_level, theme.animations_enabled), before)
        self.assertEqual([name for name, _ in commands], ["theme", "setting", "setting"])

    def test_invalid_settings_do_not_escape_as_commands(self):
        model = LauncherModel()
        commands = []
        model.command.connect(lambda *args: commands.append(args))
        model.chooseSetting("effects", "live")
        model.chooseSetting("unknown", True)
        model.choosePalette("unknown")
        self.assertEqual(commands, [])

    def test_blur_radius_validates_updates_and_emits_without_mutating_global_theme(self):
        from main_ext.class_ThemeUi import theme
        before = theme.blur_radius
        model = LauncherModel()
        commands = []
        model.command.connect(lambda name, data: commands.append((name, data)))
        self.assertEqual(model.settings['blurRadius'], 6)
        self.assertEqual(model.effectiveBlurRadius, 18)
        for value in (-1, 27, True, '12', None, float('nan'), float('inf')):
            model.chooseSetting('blurRadius', value)
        self.assertEqual(commands, [])
        model.chooseSetting('blurRadius', 12.2)
        self.assertEqual(model.settings['blurRadius'], 12)
        model.chooseSetting('blurRadius', 12)
        self.assertEqual(commands, [('setting', {'key': 'blurRadius', 'value': 12})])
        self.assertEqual(theme.blur_radius, before)

    def test_medium_is_fixed_and_high_uses_named_preset_radii(self):
        model = LauncherModel(settings={'effects': 'medium', 'blurRadius': 6})
        model.chooseSetting('blurRadius', 17)
        self.assertEqual(model.effectiveBlurRadius, 18)
        model.chooseSetting('effects', 'high')
        self.assertEqual(model.effectiveBlurRadius, 17)
        model.chooseSetting('blurRadius', 6)
        self.assertEqual(model.effectiveBlurRadius, 6)
        model.chooseSetting('effects', 'medium')
        self.assertEqual(model.effectiveBlurRadius, 18)

    def test_loading_updates_leave_appearance_and_settings_bindings_alone(self):
        model = LauncherModel()
        invalidated = []
        for signal in (model.colorsChanged, model.stringsChanged, model.settingsChanged):
            signal.connect(lambda: invalidated.append(True))
        colors, strings = model.colors, model.strings
        model.setStartupActive(True)
        model.setBusy(True)
        model.setDeletingPath('C:/Pixel')
        model.replaceLocation('C:/Projects', 'C:/Default')
        model.replaceProfile({'name': 'Fixture'})
        model.setBusy(False)
        self.assertEqual(invalidated, [])
        self.assertIs(model.colors, colors)
        self.assertIs(model.strings, strings)
        model.chooseLanguage('id')
        self.assertIsNot(model.strings, strings)
        model.choosePalette('light')
        self.assertEqual(model.colors['BG'], '#f0f2f5')
        self.assertEqual(model.colors['FG_SYNC'], '#3c68b8')

    def test_refresh_preferences_notifies_only_changed_values(self):
        model = LauncherModel()
        notifications = []
        model.settingsChanged.connect(lambda: notifications.append('settings'))
        model.colorsChanged.connect(lambda: notifications.append('colors'))
        model.stringsChanged.connect(lambda: notifications.append('strings'))
        model.refreshPreferences('default', 'en', {'animations': False})
        self.assertEqual(notifications, ['settings'])
        model.refreshPreferences('default', 'en', {'animations': False})
        self.assertEqual(notifications, ['settings'])
        model.refreshPreferences('light', 'id', {'animations': False})
        self.assertEqual(notifications, ['settings', 'colors', 'strings'])

    def test_drive_usage_refresh_preserves_project_view_until_filters_change(self):
        model = LauncherModel(projects=[{"project_name": "Pixel", "path": "C:/Pixel", "drive": "C:"},
                                         {"project_name": "Other", "path": "D:/Other", "drive": "D:"}])
        changes = []
        model.projectsChanged.connect(lambda: changes.append(True))
        model.replaceDrives([{"key": "C:", "percent": 50}])
        model.replaceDrives([{"key": "C:", "percent": 51}])
        self.assertEqual(changes, [])
        model.replaceDrives([], ["c:"])
        self.assertEqual([row["project_name"] for row in model.projects], ["Other"])
        self.assertEqual(len(changes), 1)

    def test_trash_search_refreshes_count_and_retains_restore_identifier(self):
        model = LauncherModel()
        changes = []
        model.trashChanged.connect(lambda: changes.append(True))
        model.replaceTrash([{"project_name": "Pixel", "path": "C:/Pixel", "_trash_id": "restore-id"}])
        model.search("no match")
        self.assertEqual(model.trashProjects, [])
        self.assertEqual(model.trashCount, 1)
        model.search("Pixel")
        self.assertEqual(model.trashProjects[0]["_trash_id"], "restore-id")
        self.assertEqual(len(changes), 3)


if __name__ == "__main__":
    unittest.main()
