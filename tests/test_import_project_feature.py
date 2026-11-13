# SPDX-License-Identifier: MPL-2.0
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from PySide6.QtCore import QUrl
from PySide6.QtWidgets import QApplication

from core import ui_lang
from ui.presentation import LauncherModel
from ui.project_store import ProjectStore


class ImportProjectFeatureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        ui_lang.init()
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.store = ProjectStore(self.root / "state")

    def tearDown(self):
        self.temporary.cleanup()

    def test_strings_contain_import_and_remove_from_list(self):
        model = LauncherModel()
        strings = model.strings
        self.assertIn("btn_import_project", strings)
        self.assertIn("menu_remove_from_list", strings)
        self.assertIn("dlg_remove_from_list_title", strings)
        self.assertIn("dlg_remove_from_list_msg", strings)

        ui_lang.set_lang("id")
        model_id = LauncherModel(language="id")
        self.assertEqual(model_id.strings["btn_import_project"], "Import")
        self.assertEqual(model_id.strings["menu_remove_from_list"], "Hapus dari daftar")

    def test_store_import_and_remove_from_list(self):
        from core.project_config import resolve_tree_target
        folder = self.root / "ONEui"
        folder.mkdir()
        (folder / "system.img").write_text("dummy", encoding="utf-8")

        record = self.store.import_project(str(folder))
        self.assertEqual(record["project_name"], "ONEui")
        self.assertTrue(record["imported"])
        self.assertEqual(record["port1"], "ONEui")
        self.assertEqual(record["port2"], "")
        self.assertEqual(record["mode"], 1)

        # resolve_tree_target returns the folder itself directly
        self.assertEqual(Path(resolve_tree_target(record, "ONEui")).resolve(), folder.resolve())
        self.assertEqual(Path(resolve_tree_target(str(folder), "ONEui")).resolve(), folder.resolve())

        # projects list in store has the record
        projects = self.store.projects()
        self.assertEqual(len(projects), 1)
        self.assertEqual(projects[0]["project_name"], "ONEui")

        # remove_from_list removes it from projects.json but keeps folder
        removed = self.store.remove_from_list(str(folder))
        self.assertTrue(removed)
        self.assertEqual(self.store.projects(), [])
        self.assertTrue(folder.is_dir())
        self.assertTrue((folder / "system.img").is_file())

    def test_launcher_menu_includes_remove_from_list_for_imported_only(self):
        from ui.application import LauncherApplication
        launcher = LauncherApplication(self.app, store=self.store, start_services=False)
        try:
            normal_record = self.store.create({
                "name": "NormalProj", "port1": "port_a", "port2": "", "mode": 1
            }, location=str(self.root))

            imported_folder = self.root / "ImportedProj"
            imported_folder.mkdir()
            imported_record = self.store.import_project(str(imported_folder))

            # Normal project menu
            received_normal = []
            launcher.model.presentationRequested.connect(lambda cmd, data: received_normal.append((cmd, data)))
            launcher._project_menu({"path": normal_record["path"]})
            self.assertEqual(len(received_normal), 1)
            actions_normal = [a["action"] for a in received_normal[0][1]["actions"]]
            self.assertIn("open", actions_normal)
            self.assertIn("pin", actions_normal)
            self.assertIn("delete", actions_normal)
            self.assertNotIn("remove_from_list", actions_normal)

            # Imported project menu
            received_imported = []
            launcher.model.presentationRequested.connect(lambda cmd, data: received_imported.append((cmd, data)))
            launcher._project_menu({"path": imported_record["path"]})
            actions_imported = [a["action"] for a in received_imported[0][1]["actions"]]
            self.assertIn("open", actions_imported)
            self.assertIn("pin", actions_imported)
            self.assertIn("remove_from_list", actions_imported)
            self.assertIn("delete", actions_imported)

            # Test remove_from_list action triggers confirmation dialog
            messages = []
            launcher._message = lambda text, title=None, confirmation=None, delete_path=None: messages.append((text, title, confirmation))
            launcher._project_action({"action": "remove_from_list", "path": imported_record["path"]})
            self.assertEqual(len(messages), 1)
            text, title, confirmation = messages[0]
            self.assertIn("ImportedProj", text)
            self.assertIsNotNone(confirmation)

            # Trigger confirmation
            confirmation()
            self.assertNotIn(imported_record["path"], [p["path"] for p in self.store.projects()])
            self.assertTrue(imported_folder.is_dir())
        finally:
            launcher.quit()

    def test_import_project_dispatch_and_folder_selected(self):
        from ui.application import LauncherApplication
        launcher = LauncherApplication(self.app, store=self.store, start_services=False)
        try:
            dispatched = []
            launcher.model.presentationRequested.connect(lambda cmd, data: dispatched.append((cmd, data)))

            # User clicks Import button
            launcher.handle("importProject", {})
            self.assertEqual(len(dispatched), 1)
            self.assertEqual(dispatched[0][0], "chooseFolder")
            self.assertEqual(dispatched[0][1]["purpose"], "importProject")

            # Folder is selected in folder dialog
            test_folder = self.root / "TargetROM"
            test_folder.mkdir()
            url = QUrl.fromLocalFile(str(test_folder)).toString()

            with patch.object(launcher, "_import_project") as mock_import:
                launcher.handle("folderSelected", {"purpose": "importProject", "url": url})
                mock_import.assert_called_once_with(str(test_folder))
        finally:
            launcher.quit()

    def test_import_project_directly_opens_workspace(self):
        from ui.application import LauncherApplication
        launcher = LauncherApplication(self.app, store=self.store, start_services=False)
        try:
            test_folder = self.root / "DirectOpenROM"
            test_folder.mkdir()

            opened = []
            with patch.object(launcher, "_open_project", side_effect=lambda rec, origin: opened.append((rec, origin))):
                launcher._import_project(str(test_folder))
                # Wait for synchronous background job completion
                self.app.processEvents()
                # Run deferred jobs or timer if any
                import time
                deadline = time.time() + 2
                while not opened and time.time() < deadline:
                    self.app.processEvents()
                    time.sleep(0.05)

            self.assertEqual(len(opened), 1)
            record, origin = opened[0]
            self.assertEqual(record["project_name"], "DirectOpenROM")
            self.assertEqual(origin, "import")
        finally:
            launcher.quit()


if __name__ == "__main__":
    unittest.main()
