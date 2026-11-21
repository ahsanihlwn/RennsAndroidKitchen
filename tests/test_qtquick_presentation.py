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
        self.assertEqual(model.settings['blurRadius'], 17)
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

    def test_favorite_filter_toggles_and_filters_pinned_projects(self):
        from PySide6.QtCore import QPersistentModelIndex
        model = LauncherModel(projects=[
            {"project_name": "Normal", "path": "C:/Normal", "pinned": False},
            {"project_name": "Pinned 1", "path": "C:/Pinned1", "pinned": True},
            {"project_name": "Pinned 2", "path": "D:/Pinned2", "pinned": True},
        ])
        self.assertFalse(model.favoriteFilter)
        self.assertEqual(len(model.projects), 3)
        self.assertEqual(model.favoriteCount, 2)
        self.assertEqual(model.visibleProjectsCount, 3)
        normal_index = QPersistentModelIndex(model.projectRows.index(2))
        model.setFavoriteFilter(True)
        self.assertTrue(model.favoriteFilter)
        self.assertEqual([row["path"] for row in model.projects], ["C:/Pinned1", "D:/Pinned2"])
        self.assertEqual(model.visibleProjectsCount, 2)
        self.assertEqual(model.projectRows.rowCount(), 3)
        self.assertTrue(normal_index.isValid())
        model.setFavoriteFilter(False)
        self.assertFalse(model.favoriteFilter)
        self.assertEqual(len(model.projects), 3)
        self.assertEqual(model.visibleProjectsCount, 3)
        self.assertTrue(normal_index.isValid())

    def test_projects_page_filter_pills_hide_cards_without_destroying_or_delaying(self):
        import os
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtCore import Qt, QObject
        from PySide6.QtTest import QTest
        from PySide6.QtWidgets import QApplication
        from ui.runtime import create_launcher

        app = QApplication.instance() or QApplication([])
        model = LauncherModel(
            projects=[
                {"project_name": "Pinned", "path": "C:/Pinned", "port1": "system", "pinned": True},
                {"project_name": "Normal", "path": "C:/Normal", "port1": "vendor", "pinned": False},
            ],
            settings={"animations": True, "effects": "low"},
        )
        model.replaceTrash([
            {"project_name": "Deleted", "path": "C:/Deleted", "port1": "system", "_trash_id": "trash-1"},
        ])
        engine, window = create_launcher(model, visible=True)

        def visual_item(name):
            pending = [window.contentItem()]
            while pending:
                candidate = pending.pop()
                if candidate.objectName() == name:
                    return candidate
                pending.extend(candidate.childItems())
            return None

        def click_item(name):
            target = visual_item(name)
            self.assertIsNotNone(target, name)
            QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier,
                             target.mapToScene(target.boundingRect().center()).toPoint())
            app.processEvents()

        try:
            QTest.qWait(50)
            self.assertEqual(engine._diagnostics, [])
            project_list = visual_item("projectList")
            trash_list = visual_item("trashList")
            motion = visual_item("projectListMotion")
            page = project_list.parentItem().parentItem()
            normal_card = visual_item("projectCard_C:/Normal")
            pinned_card = visual_item("projectCard_C:/Pinned")
            self.assertIsNotNone(normal_card)
            self.assertIsNotNone(pinned_card)
            normal_delegate = normal_card.parentItem().parentItem()

            # Clicking Favorit smoothly animates and hides non-pinned delegates without deleting them.
            click_item("favoriteDrive")
            self.assertTrue(model.favoriteFilter)
            self.assertFalse(motion.property("active"))
            QTest.qWait(50)
            self.assertGreater(normal_delegate.property("reveal"), 0.0)
            self.assertLess(normal_delegate.property("reveal"), 1.0)
            QTest.qWait(220)
            self.assertFalse(normal_delegate.property("visible"))
            self.assertEqual(normal_delegate.property("height"), 0)
            self.assertIs(visual_item("projectCard_C:/Normal"), normal_card)
            self.assertIs(visual_item("projectCard_C:/Pinned"), pinned_card)

            # Clicking Sampah smoothly cross-fades to trashList without destroying project cards.
            click_item("trashDrive")
            self.assertTrue(page.property("trash"))
            self.assertFalse(motion.property("active"))
            QTest.qWait(50)
            self.assertGreater(page.property("trashProgress"), 0.0)
            self.assertLess(page.property("trashProgress"), 1.0)
            QTest.qWait(220)
            self.assertFalse(project_list.property("visible"))
            self.assertTrue(trash_list.property("visible"))
            trash_card = visual_item("projectCard_C:/Deleted")
            self.assertIsNotNone(trash_card)
            self.assertIs(visual_item("projectCard_C:/Normal"), normal_card)

            # Clicking Semua smoothly restores visibility of the exact same ProjectCard instance.
            click_item("allProjectsDrive")
            self.assertFalse(page.property("trash"))
            self.assertFalse(model.favoriteFilter)
            self.assertFalse(motion.property("active"))
            QTest.qWait(250)
            self.assertTrue(project_list.property("visible"))
            self.assertFalse(trash_list.property("visible"))
            self.assertTrue(normal_delegate.property("visible"))
            self.assertEqual(normal_delegate.property("height"), 74)
            self.assertIs(visual_item("projectCard_C:/Normal"), normal_card)
            self.assertIs(visual_item("projectCard_C:/Deleted"), trash_card)

            # Spamming filter pills mid-animation continues from current position with proportional duration.
            click_item("favoriteDrive")
            QTest.qWait(45)
            mid_reveal = normal_delegate.property("reveal")
            self.assertGreater(mid_reveal, 0.0)
            self.assertLess(mid_reveal, 1.0)
            click_item("allProjectsDrive")
            # Continuing from mid_reveal back to 1.0 uses remaining duration < full 190ms.
            QTest.qWait(180)
            self.assertEqual(normal_delegate.property("reveal"), 1.0)

            click_item("trashDrive")
            QTest.qWait(45)
            mid_trash = page.property("trashProgress")
            self.assertGreater(mid_trash, 0.0)
            self.assertLess(mid_trash, 1.0)
            click_item("allProjectsDrive")
            QTest.qWait(190)
            self.assertEqual(page.property("trashProgress"), 0.0)

            # Header and footer GlassBand share identical edge fade in High mode only.
            header_glass = visual_item("projectsHeaderGlass")
            footer_glass = visual_item("projectsFooterGlass")
            self.assertIsNotNone(header_glass)
            self.assertIsNotNone(footer_glass)
            self.assertEqual(header_glass.property("fadeEdge"), "bottom")
            self.assertEqual(footer_glass.property("fadeEdge"), "top")
            self.assertEqual(header_glass.property("fadeSize"), footer_glass.property("fadeSize"))
            self.assertGreater(header_glass.property("fadeSize"), 0)

            model.chooseSetting("animations", False)
            model.chooseSetting("effects", "high")
            app.processEvents()
            self.assertTrue(header_glass.property("edgeFading"))
            self.assertTrue(footer_glass.property("edgeFading"))
            self.assertTrue(header_glass.property("masked"))
            self.assertTrue(footer_glass.property("masked"))

            for mode in ("medium", "low"):
                model.chooseSetting("effects", mode)
                app.processEvents()
                self.assertFalse(header_glass.property("edgeFading"))
                self.assertFalse(footer_glass.property("edgeFading"))
                self.assertFalse(header_glass.property("masked"))
                self.assertFalse(footer_glass.property("masked"))
        finally:
            window.close()
            window.deleteLater()
            engine.deleteLater()
            app.processEvents()

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
