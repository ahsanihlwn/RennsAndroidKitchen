# SPDX-License-Identifier: MPL-2.0
import os
from pathlib import Path
import struct
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlComponent, QQmlEngine

from core import native_backend, super_logic, ui_lang
from core.tree_entries import IS_FOLDER_ROLE
from ui.directory import DirectoryEntry
from ui.pack_super import PackSuperController
from ui.presentation import LauncherModel
from ui.workspace_actions import context_actions, dispatch
from ui.workspace_tasks import WorkspaceTasks


def _make_sparse_img(path: Path, raw_bytes: bytes, block_size: int = 4096):
    raw_tmp = path.with_suffix(".raw.tmp")
    raw_tmp.write_bytes(raw_bytes)
    try:
        native_backend.sparse_img2simg(str(raw_tmp), str(path), block_size=block_size)
    finally:
        raw_tmp.unlink(missing_ok=True)


def _make_ext4_blob(size: int = 128 * 1024) -> bytes:
    buf = bytearray(size)
    # EXT4 magic 0xEF53 at sb_start(1024) + 0x38, INCOMPAT_EXTENTS (0x40) at +0x60
    struct.pack_into("<H", buf, 1024 + 0x38, 0xEF53)
    struct.pack_into("<I", buf, 1024 + 0x60, 0x0040)
    return bytes(buf)


def _make_erofs_blob(size: int = 128 * 1024) -> bytes:
    buf = bytearray(size)
    # EROFS magic 0xE0F5E1E2 at offset 1024
    struct.pack_into("<I", buf, 1024, 0xE0F5E1E2)
    return bytes(buf)


class PackSuperContextTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from ui.runtime import initialize_quick_runtime
        initialize_quick_runtime()
        cls.app = QGuiApplication.instance() or QGuiApplication([])
        from core.fonts import apply_app_font
        apply_app_font(cls.app, polish_widgets=False)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        tasks = WorkspaceTasks(runner=lambda *args, **kwargs: None)
        self.addCleanup(tasks.close)
        directory = SimpleNamespace(
            base=str(self.base),
            location=str(self.base),
            _nodes={},
            _history=[],
            engine=SimpleNamespace(root_map_ctx={}, root_map_fs={}),
        )
        self.presentations = []
        self.session = SimpleNamespace(
            active_tree=directory,
            _selection=[],
            _clipboard=None,
            mounts=SimpleNamespace(reference=lambda *args, **kwargs: None),
            _tasks=tasks,
            statusChanged=SimpleNamespace(emit=lambda: None),
            presentationRequested=SimpleNamespace(emit=lambda kind, data: self.presentations.append((kind, data))),
            message=lambda *args, **kwargs: None,
        )
        p = patch.object(ui_lang, "_current_lang", "id")
        p.start()
        self.addCleanup(p.stop)

    def entry(self, name: str, badge: str = "", folder: bool = False, data: bytes = b"fixture"):
        path = self.base / name
        if folder:
            path.mkdir()
        else:
            path.write_bytes(data)
        node = DirectoryEntry([name, "", ""])
        node.setData(0, Qt.UserRole, str(path))
        node.setData(0, Qt.UserRole + 1, badge)
        node.setData(0, IS_FOLDER_ROLE, folder)
        self.session.active_tree._nodes[str(path)] = node
        return path, node

    def actions(self, paths):
        with patch("core.binary_handler.is_available", lambda *args: True):
            return context_actions(self.session, str(paths[0]), [str(p) for p in paths])

    def test_context_shows_repack_super_for_erofs_ext_and_mixed_with_min_partitions(self):
        s_img, _ = self.entry("system.img", "EROFS")
        se_img, _ = self.entry("system_ext.img", "EXT4")
        p_img, _ = self.entry("product.img", "EROFS")
        v_img, _ = self.entry("vendor.img", "EXT2")
        rows = self.actions([s_img, se_img, p_img, v_img])
        keys = [r.get("key") for r in rows]
        self.assertIn("repack_super", keys)
        super_row = next(r for r in rows if r.get("key") == "repack_super")
        self.assertEqual(super_row["paths"], [str(s_img), str(se_img), str(p_img), str(v_img)])

        dispatch(self.session, "repack_super", {"path": str(s_img), "paths": super_row["paths"]})
        self.assertEqual(self.presentations[-1][0], "packSuper")
        self.assertEqual(self.presentations[-1][1]["paths"], super_row["paths"])

    def test_context_shows_repack_super_for_slot_suffixed_and_sparse_images(self):
        s_img, _ = self.entry("system_a.img", "SPARSE", data=b"")
        _make_sparse_img(s_img, _make_erofs_blob())
        se_img, _ = self.entry("system_ext_a.img", "EXT4", data=_make_ext4_blob())
        p_img, _ = self.entry("product_a.img", "EROFS", data=_make_erofs_blob())
        rows = self.actions([s_img, se_img, p_img])
        self.assertIn("repack_super", [r.get("key") for r in rows])

    def test_context_hides_repack_super_when_missing_required_partition_or_invalid_member(self):
        s_img, _ = self.entry("system.img", "EROFS")
        se_img, _ = self.entry("system_ext.img", "EXT4")
        v_img, _ = self.entry("vendor.img", "EROFS")
        # Missing product
        self.assertNotIn("repack_super", [r.get("key") for r in self.actions([s_img, se_img, v_img])])

        p_img, _ = self.entry("product.img", "EROFS")
        boot_img, _ = self.entry("boot.img", "BOOT")
        # Non-EROFS/EXT file selected alongside
        self.assertNotIn("repack_super", [r.get("key") for r in self.actions([s_img, se_img, p_img, boot_img])])

        # Folder instead of file
        sys_dir, _ = self.entry("system", "EROFS", folder=True)
        self.assertNotIn("repack_super", [r.get("key") for r in self.actions([sys_dir, se_img, p_img])])


class PackSuperEngineAndUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from ui.runtime import initialize_quick_runtime
        initialize_quick_runtime()
        cls.app = QGuiApplication.instance() or QGuiApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_pack_super_roundtrip_mixed_erofs_ext_raw_and_sparse(self):
        sys_img = self.root / "system.img"
        se_img = self.root / "system_ext.img"
        prod_img = self.root / "product.img"
        sys_img.write_bytes(_make_erofs_blob(128 * 1024))
        _make_sparse_img(se_img, _make_ext4_blob(128 * 1024))
        prod_img.write_bytes(_make_erofs_blob(128 * 1024))

        # Test Virtual-AB + Sparse output
        out_sparse = self.root / "super_sparse.img"
        events = []
        ok, res = super_logic.pack_super(
            [str(sys_img), str(se_img), str(prod_img)],
            str(out_sparse),
            super_type="virtual_ab",
            group_name="qti_dynamic_partitions",
            attrib="readonly",
            sparse=True,
            progress=lambda pct, stage: events.append((pct, stage)),
        )
        self.assertTrue(ok, res)
        self.assertTrue(out_sparse.is_file())
        self.assertEqual(struct.unpack_from("<I", out_sparse.read_bytes(), 0)[0], 0xED26FF3A)
        self.assertTrue(any(pct == 100 for pct, _ in events))

        info = native_backend.super_info(str(out_sparse))
        self.assertIn("system_a", info["partitions"])
        self.assertIn("system_ext_a", info["partitions"])
        self.assertIn("product_a", info["partitions"])
        self.assertIn("system_b", info["partitions"])
        self.assertIn("qti_dynamic_partitions_a", info["groups"])
        self.assertIn("qti_dynamic_partitions_b", info["groups"])

        extract_dir = self.root / "unpacked"
        ext_res = native_backend.super_extract(str(out_sparse), str(extract_dir), normalize=True)
        self.assertEqual(len(ext_res["outputs"]), 3)
        self.assertTrue((extract_dir / "system.img").is_file())
        self.assertTrue((extract_dir / "system_ext.img").is_file())
        self.assertTrue((extract_dir / "product.img").is_file())

        # Test A-only + Raw output
        out_raw = self.root / "super_raw.img"
        ok_raw, res_raw = super_logic.pack_super(
            [str(sys_img), str(se_img), str(prod_img)],
            str(out_raw),
            super_type="a_only",
            group_name="main",
            attrib="none",
            sparse=False,
        )
        self.assertTrue(ok_raw, res_raw)
        self.assertNotEqual(struct.unpack_from("<I", out_raw.read_bytes(), 0)[0], 0xED26FF3A)
        info_raw = native_backend.super_info(str(out_raw))
        self.assertEqual(info_raw["partitions"], ["system", "system_ext", "product"])
        self.assertIn("main", info_raw["groups"])

    def test_controller_and_qml_component_load(self):
        sys_img = self.root / "system.img"
        se_img = self.root / "system_ext.img"
        prod_img = self.root / "product.img"
        sys_img.write_bytes(_make_erofs_blob(128 * 1024))
        se_img.write_bytes(_make_ext4_blob(128 * 1024))
        prod_img.write_bytes(_make_erofs_blob(128 * 1024))

        ws = SimpleNamespace(active_tree=None, directories=(), message=lambda *a, **k: None)
        ctrl = PackSuperController(ws)
        self.addCleanup(ctrl.close)
        import time
        ctrl.prepare([str(sys_img), str(se_img), str(prod_img)], str(self.root))
        deadline = time.monotonic() + 5.0
        while ctrl.loading and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.01)
        self.assertFalse(ctrl.loading)
        self.assertEqual(ctrl.selectedCount, 3)
        self.assertTrue(ctrl.canPack)

        # Unchecking product triggers validation hint
        prod_key = os.path.normcase(os.path.normpath(str(prod_img)))
        ctrl.toggleKey(prod_key, False)
        self.assertFalse(ctrl.canPack)
        self.assertIn("product", ctrl.validationHint)
        ctrl.toggleKey(prod_key, True)
        self.assertTrue(ctrl.canPack)

        # Verify WorkspacePackSuper.qml compiles cleanly with QQmlEngine
        qml_path = Path(__file__).resolve().parents[1] / "ui" / "controls" / "WorkspacePackSuper.qml"
        engine = QQmlEngine()
        comp = QQmlComponent(engine, QUrl.fromLocalFile(str(qml_path)))
        self.assertEqual(comp.status(), QQmlComponent.Ready, comp.errorString())


if __name__ == "__main__":
    unittest.main()
