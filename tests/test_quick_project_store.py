# SPDX-License-Identifier: MPL-2.0
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ui.project_store import ProjectStore


class ProjectStoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.store = ProjectStore(self.root / "state")

    def tearDown(self):
        self.temporary.cleanup()

    def create(self, **overrides):
        return self.store.create({"name": "Firmware", "port1": "system", "port2": "vendor", "mode": 2,
                                  **overrides}, location=str(self.root / "projects"))

    def test_created_record_is_compatible_with_workspace_and_persistent(self):
        record = self.create()
        self.assertEqual(self.store.projects(), [record])
        self.assertEqual(record["mode"], 2)
        for port in ("system", "vendor"):
            self.assertTrue((Path(record["path"]) / port / "config").is_dir())
        self.store.toggle_pin(record["path"])
        self.assertTrue(ProjectStore(self.root / "state").projects()[0]["pinned"])

    def test_traversal_duplicate_folders_and_existing_projects_are_rejected(self):
        for values in ({"name": "../escape"}, {"port1": "C:\\outside"},
                       {"port2": "SYSTEM"}, {"name": "CON"}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                self.create(**values)
        record = self.create()
        (Path(record["path"]) / "keep.txt").write_text("keep", encoding="utf-8")
        with self.assertRaises(FileExistsError):
            self.create()
        self.assertEqual((Path(record["path"]) / "keep.txt").read_text(), "keep")

    def test_corrupt_metadata_is_preserved_without_creating_folders(self):
        self.store.directory.mkdir()
        self.store.projects_path.write_text('{"invalid": true}', encoding="utf-8")
        before = self.store.projects_path.read_bytes()
        with self.assertRaises(ValueError):
            self.create()
        self.assertEqual(self.store.projects_path.read_bytes(), before)
        self.assertFalse((self.root / "projects").exists())

    def test_default_location_and_three_pin_limit_survive_reload(self):
        self.store.set_location(str(self.root / "destination"))
        self.assertEqual(self.store.default_location(), str(self.root / "destination"))
        for index in range(4):
            record = self.create(name=f"Firmware {index}")
            if index < 3:
                self.store.toggle_pin(record["path"])
        with self.assertRaisesRegex(ValueError, "pin_limit"):
            self.store.toggle_pin(record["path"])
        self.assertEqual(sum(row.get("pinned", False) for row in self.store.projects()), 3)
        self.store.set_location("")
        self.assertEqual(json.loads(self.store.location_path.read_text())["path"], "")

    def test_maintenance_skips_unavailable_drives_without_touching_the_filesystem(self):
        with patch.object(self.store.trash, 'entries', return_value=[{'project': {'path': 'E:\\offline'}}]), \
             patch.object(self.store.trash, '_drive_available', side_effect=AssertionError('Blocked volume was probed')):
            checked = []
            self.assertEqual(self.store.trash.maintain(drive_available=lambda path: checked.append(path) or False), [])
            self.assertEqual(checked, ['E:\\offline'])


if __name__ == "__main__":
    unittest.main()
