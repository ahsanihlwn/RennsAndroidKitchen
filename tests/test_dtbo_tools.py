# SPDX-License-Identifier: MPL-2.0
from pathlib import Path
import struct
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication

from core import bin_analyzer, dtbo_image, header_magic, native_backend, ui_lang
from core.devicetree import compile as dt_compile
from core.tree_entries import IS_FOLDER_ROLE
from ui.directory import DirectoryEntry
from ui.workspace_actions import context_actions
from ui.workspace_tasks import WorkspaceTasks
from ui.workspace_tools import dispatch_tool


SAMPLE_DTBOS = (
    r"d:\PORT\NOTE 40 PRO 5G\TESTING\dtbo.img",
    r"d:\PORT\MT6789\X6850\dtbo.img",
)


def _sample_dts() -> str:
    return (
        "/dts-v1/;\n\n"
        "/ {\n"
        "\tcompatible = \"rak,dtbo-test\";\n\n"
        "\tfragment@0 {\n"
        "\t\ttarget = <0xffffffff>;\n\n"
        "\t\t__overlay__ {\n"
        "\t\t\tstatus = \"okay\";\n"
        "\t\t};\n"
        "\t};\n"
        "};\n"
    )


class DtboNativeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_v1_compressed_dtbo_pack_unpack_roundtrip_and_magic(self):
        fdt = dt_compile(_sample_dts())
        dtb_path = self.root / "entry0.dtb"
        dtb_path.write_bytes(fdt)

        out_img = self.root / "dtbo.img"
        spec = {
            "dt_type": "dtb",
            "page_size": 4096,
            "version": 1,
            "entries": [
                {
                    "dtb_path": str(dtb_path),
                    "id": 0x100,
                    "rev": 0x2,
                    "flags": 0x02,  # gzip
                    "custom": [0x11, 0x22, 0x33, 0],
                    "compression": "gzip",
                }
            ],
        }
        pack_res = native_backend.dtbo_pack(str(out_img), spec)
        self.assertEqual(pack_res["entry_count"], 1)
        self.assertEqual(header_magic.check_magic(str(out_img)), "DTBO")
        self.assertEqual(bin_analyzer.detect_binary_format(str(out_img))["format"], "Android DTBO Container")

        entries_meta = native_backend.directory_entries(str(self.root), str(self.root), include_magic=True)
        row = next(e for e in entries_meta if e["name"] == "dtbo.img")
        self.assertEqual(row["magic"], "DTBO")

        info = dtbo_image.inspect_dtbo_image(out_img)
        self.assertEqual(info["header"]["version"], 1)
        self.assertEqual(info["header"]["page_size"], 4096)
        self.assertEqual(info["entries"][0]["compression"], "gzip")
        self.assertEqual(info["entries"][0]["id"], 0x100)

        ok, unpacked_dir = dtbo_image.unpack_dtbo_image(out_img)
        self.assertTrue(ok, unpacked_dir)
        unpacked = Path(unpacked_dir)
        self.assertTrue((unpacked / "dtb" / "dtbo.0.dtb").is_file())
        self.assertTrue((unpacked / "dts" / "dtbo.0.dts").is_file())
        self.assertTrue((unpacked / "dtbo_manifest.json").is_file())
        self.assertTrue((self.root / "config" / "dtbo_dtboconf").is_file())
        self.assertTrue(dtbo_image.is_dtbo_folder(unpacked))

        # Modify DTS and repack
        dts_file = unpacked / "dts" / "dtbo.0.dts"
        dts_text = dts_file.read_text(encoding="utf-8").replace("okay", "disabled")
        dts_file.write_text(dts_text, encoding="utf-8")

        ok_repack, rebuilt = dtbo_image.repack_dtbo_image(unpacked)
        self.assertTrue(ok_repack, rebuilt)
        rebuilt_path = Path(rebuilt)
        self.assertTrue(rebuilt_path.is_file())
        self.assertEqual(header_magic.check_magic(str(rebuilt_path)), "DTBO")

        reunpacked = self.root / "reunpacked"
        ok2, _ = dtbo_image.unpack_dtbo_image(rebuilt_path, reunpacked)
        self.assertTrue(ok2)
        self.assertIn("disabled", (reunpacked / "dts" / "dtbo.0.dts").read_text(encoding="utf-8"))
        info2 = dtbo_image.inspect_dtbo_image(rebuilt_path)
        self.assertEqual(info2["header"]["version"], 1)
        self.assertEqual(info2["entries"][0]["compression"], "gzip")
        self.assertEqual(info2["entries"][0]["id"], 0x100)

    def test_real_sample_dtbo_images_unpack_and_repack_roundtrip(self):
        for idx, sample in enumerate(SAMPLE_DTBOS):
            src = Path(sample)
            if not src.is_file():
                continue
            with self.subTest(sample=sample):
                self.assertEqual(header_magic.check_magic(str(src)), "DTBO")
                info = dtbo_image.inspect_dtbo_image(src)
                self.assertGreaterEqual(info["header"]["dt_entry_count"], 1)

                out_dir = self.root / f"sample_{idx}"
                ok, res = dtbo_image.unpack_dtbo_image(src, out_dir)
                self.assertTrue(ok, res)

                rebuilt = self.root / f"sample_{idx}_new.img"
                ok_r, res_r = dtbo_image.repack_dtbo_image(out_dir, rebuilt)
                self.assertTrue(ok_r, res_r)
                self.assertEqual(header_magic.check_magic(str(rebuilt)), "DTBO")

                re_dir = self.root / f"sample_{idx}_reunpacked"
                ok2, res2 = dtbo_image.unpack_dtbo_image(rebuilt, re_dir)
                self.assertTrue(ok2, res2)

                orig_dts = (out_dir / "dts" / "dtbo.0.dts").read_text(encoding="utf-8")
                re_dts = (re_dir / "dts" / "dtbo.0.dts").read_text(encoding="utf-8")
                self.assertEqual(orig_dts, re_dts)


class DtboContextMenuTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QGuiApplication.instance() or QGuiApplication([])

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        tasks = WorkspaceTasks(runner=lambda *args, **kwargs: None)
        self.addCleanup(tasks.close)
        directory = SimpleNamespace(
            base=str(self.base),
            location=str(self.base),
            _nodes={},
            _history=[],
            engine=SimpleNamespace(root_map_ctx={}, root_map_fs={}),
        )
        self.session = SimpleNamespace(
            active_tree=directory,
            _selection=[],
            _clipboard=None,
            mounts=SimpleNamespace(reference=lambda *args, **kwargs: None),
            _tasks=tasks,
            statusChanged=SimpleNamespace(emit=lambda: None),
            message=lambda *args, **kwargs: None,
        )
        p = patch.object(ui_lang, "_current_lang", "id")
        p.start()
        self.addCleanup(p.stop)

    def test_context_menu_offers_unpack_and_repack_dtbo(self):
        img = self.base / "dtbo.img"
        img.write_bytes(struct.pack(">I", 0xD7B7AB1E) + b"\x00" * 60)
        node = DirectoryEntry(["dtbo.img", "", ""])
        node.setData(0, Qt.UserRole, str(img))
        node.setData(0, Qt.UserRole + 1, "DTBO")
        node.setData(0, IS_FOLDER_ROLE, False)
        self.session.active_tree._nodes[str(img)] = node

        with patch("core.binary_handler.is_available", lambda *args: True):
            keys = [r.get("key") for r in context_actions(self.session, str(img), [str(img)])]
        self.assertIn("unpack_dtbo", keys)

        folder = self.base / "dtbo"
        (folder / "dts").mkdir(parents=True)
        (folder / "dts" / "dtbo.0.dts").write_text(_sample_dts(), encoding="utf-8")
        fnode = DirectoryEntry(["dtbo", "", ""])
        fnode.setData(0, Qt.UserRole, str(folder))
        fnode.setData(0, Qt.UserRole + 1, "DTBO")
        fnode.setData(0, IS_FOLDER_ROLE, True)
        self.session.active_tree._nodes[str(folder)] = fnode

        with patch("core.binary_handler.is_available", lambda *args: True):
            fkeys = [r.get("key") for r in context_actions(self.session, str(folder), [str(folder)])]
        self.assertIn("repack_dtbo", fkeys)
        self.assertNotIn("make_filesystem", fkeys)


if __name__ == "__main__":
    unittest.main()
