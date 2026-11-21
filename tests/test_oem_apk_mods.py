# SPDX-License-Identifier: MPL-2.0
"""Tests for clean-room OEM containers, APK debloater, Quick Mods, ROM Porter, and UI integration."""
from __future__ import annotations

import hashlib
import os
import struct
import tempfile
import time
import unittest
import zipfile
import zlib
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QObject, Signal
from PySide6.QtGui import QGuiApplication

from core import apk_manager, header_magic, oem_containers, quick_mods
from ui.viewers import viewer_kind


def _ensure_qt():
    return QCoreApplication.instance() or QGuiApplication([])


def _build_synthetic_update_app(entries: list[tuple[str, bytes]]) -> bytes:
    """Build a minimal Huawei UPDATE.APP container."""
    out = bytearray(b"\x00" * 92)
    for name, payload in entries:
        hdr = bytearray(98)
        hdr[0:4] = b"\x55\xAA\x5A\xA5"
        hdr[4:8] = struct.pack("<I", 98)  # hdr_len
        hdr[8:12] = struct.pack("<I", 1)  # hw_id
        hdr[12:16] = struct.pack("<I", 0)  # seq
        hdr[16:20] = b"HISI"
        hdr[20:24] = struct.pack("<I", 0xFFFFFFFF)
        hdr[24:28] = struct.pack("<I", len(payload))
        name_bytes = name.encode("ascii")[:32]
        hdr[60 : 60 + len(name_bytes)] = name_bytes
        out.extend(hdr)
        out.extend(payload)
        pad = (4 - (len(out) % 4)) % 4
        out.extend(b"\x00" * pad)
    return bytes(out)


def _build_synthetic_dz(entries: list[tuple[str, str, bytes]]) -> bytes:
    """Build a minimal LG DZ container with zlib-compressed slices."""
    file_hdr = bytearray(512)
    file_hdr[0:4] = bytes((0x32, 0x96, 0x18, 0x74))
    file_hdr[4:8] = struct.pack("<I", 1)
    file_hdr[8:12] = struct.pack("<I", 0)
    file_hdr[16:22] = b"LG-Q9\x00"
    file_hdr[192:196] = struct.pack("<I", len(entries))

    out = bytearray(file_hdr)
    for part_name, chunk_name, raw in entries:
        comp = zlib.compress(raw)
        md5 = hashlib.md5(raw).digest()
        ch = bytearray(512)
        ch[0:4] = bytes((0x30, 0x12, 0x95, 0x78))
        p_b = part_name.encode("ascii")[:32]
        c_b = chunk_name.encode("ascii")[:64]
        ch[4 : 4 + len(p_b)] = p_b
        ch[36 : 36 + len(c_b)] = c_b
        ch[100:104] = struct.pack("<I", len(raw))
        ch[104:108] = struct.pack("<I", len(comp))
        ch[108:124] = md5
        out.extend(ch)
        out.extend(comp)
    return bytes(out)


def _build_synthetic_romfs(files: list[tuple[str, bytes]]) -> bytes:
    """Build a minimal RomFS (-rom1fs-) image with regular files in the root."""
    buf = bytearray(b"\x00" * 32)
    buf[0:8] = b"-rom1fs-"
    buf[16:20] = b"vol\x00"
    offset = 32
    for idx, (fname, data) in enumerate(files):
        name_raw = fname.encode("ascii") + b"\x00"
        name_pad = ((len(name_raw) + 15) // 16) * 16
        data_pad = ((len(data) + 15) // 16) * 16
        entry_len = 16 + name_pad + data_pad
        next_off = (offset + entry_len) if idx + 1 < len(files) else 0
        hdr = bytearray(16)
        hdr[0:4] = struct.pack(">I", next_off | 2)  # type=2 regular file
        hdr[8:12] = struct.pack(">I", len(data))
        buf.extend(hdr)
        buf.extend(name_raw.ljust(name_pad, b"\x00"))
        buf.extend(data.ljust(data_pad, b"\x00"))
        offset += entry_len
    buf[8:12] = struct.pack(">I", len(buf))
    return bytes(buf)


def _build_axml(package: str, version_name: str, permission: str) -> bytes:
    """Build a minimal AOSP binary XML AndroidManifest with string pool & manifest/uses-permission."""
    strings = [
        "manifest",
        "package",
        "versionName",
        "uses-permission",
        "name",
        "uses-sdk",
        "minSdkVersion",
        "targetSdkVersion",
        "application",
        "label",
        "icon",
        package,
        version_name,
        permission,
        "SampleApp",
        "res/drawable/ic_launcher.png",
    ]
    # StringPool chunk (UTF-8)
    str_blob = bytearray()
    offsets = []
    for s in strings:
        offsets.append(len(str_blob))
        b = s.encode("utf-8")
        str_blob.append(len(b))
        str_blob.append(len(b))
        str_blob.extend(b)
        str_blob.append(0)
    while len(str_blob) % 4 != 0:
        str_blob.append(0)

    hdr_size = 28
    offsets_bytes = b"".join(struct.pack("<I", o) for o in offsets)
    strings_start = hdr_size + len(offsets_bytes)
    sp_total = strings_start + len(str_blob)
    sp = struct.pack(
        "<HHIIIII",
        0x0001,  # RES_STRING_POOL_TYPE
        hdr_size,
        sp_total,
        len(strings),
        0,       # styleCount
        1 << 8,  # UTF8_FLAG
        strings_start,
    ) + struct.pack("<I", 0)
    sp_chunk = sp + offsets_bytes + bytes(str_blob)

    # ResourceId map matching attribute indices: 1(package),2(versionName),4(name),6(minSdk),7(targetSdk),9(label),10(icon)
    res_ids = [
        0,
        0x01010003,
        0x0101021C,  # versionName (attr id not needed by string name, just pad)
        0,
        0x01010003,  # android:name
        0,
        0x0101020C,  # minSdkVersion
        0x01010270,  # targetSdkVersion
        0,
        0x01010001,  # label
        0x01010002,  # icon
    ]
    rid_bytes = b"".join(struct.pack("<I", x) for x in res_ids)
    rid_chunk = struct.pack("<HHI", 0x0180, 8, 8 + len(rid_bytes)) + rid_bytes

    def start_elem(name_idx: int, attrs: list[tuple[int, int, int, int]]) -> bytes:
        # attrs: (name_idx, raw_idx, data_type, data)
        attr_bytes = b"".join(
            struct.pack("<IIIHBBI", 0xFFFFFFFF, n_i, r_i, 8, 0, dtype, dval)
            for n_i, r_i, dtype, dval in attrs
        )
        chunk_sz = 36 + len(attr_bytes)
        return (
            struct.pack(
                "<HHIIIIIHHHHHH",
                0x0102,      # RES_XML_START_ELEMENT_TYPE
                16,          # header size
                chunk_sz,    # total chunk size
                1,           # lineNumber
                0xFFFFFFFF,  # comment
                0xFFFFFFFF,  # ns
                name_idx,
                20,          # attributeStart
                20,          # attributeSize
                len(attrs),  # attributeCount
                0,           # idIndex
                0,           # classIndex
                0,           # styleIndex
            )
            + attr_bytes
        )

    def end_elem(name_idx: int) -> bytes:
        return struct.pack("<HHIIIII", 0x0103, 16, 24, 1, 0xFFFFFFFF, 0xFFFFFFFF, name_idx)

    TYPE_STRING = 0x03
    TYPE_INT_DEC = 0x10
    body = bytearray()
    body.extend(sp_chunk)
    body.extend(rid_chunk)
    body.extend(start_elem(0, [(1, 11, TYPE_STRING, 11), (2, 12, TYPE_STRING, 12)]))  # manifest
    body.extend(start_elem(3, [(4, 13, TYPE_STRING, 13)]))  # uses-permission
    body.extend(end_elem(3))
    body.extend(start_elem(5, [(6, 0xFFFFFFFF, TYPE_INT_DEC, 28), (7, 0xFFFFFFFF, TYPE_INT_DEC, 34)]))  # uses-sdk
    body.extend(end_elem(5))
    body.extend(start_elem(8, [(9, 14, TYPE_STRING, 14), (10, 15, TYPE_STRING, 15)]))  # application
    body.extend(end_elem(8))
    body.extend(end_elem(0))

    return struct.pack("<HHI", 0x0003, 8, 8 + len(body)) + bytes(body)


def _build_vbmeta(flags: int = 0) -> bytes:
    buf = bytearray(256)
    buf[0:4] = b"AVB0"
    buf[4:8] = struct.pack(">I", 1)
    buf[8:12] = struct.pack(">I", 0)
    buf[120:124] = struct.pack(">I", flags)
    return bytes(buf)


class OemApkModsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="rak-suite-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_oem_containers_detect_inspect_extract_and_traversal_guard(self):
        uapp = self.root / "UPDATE.APP"
        uapp.write_bytes(_build_synthetic_update_app([("SYSTEM", b"SYS_OK"), ("BOOT", b"BOOT_OK")]))
        self.assertEqual(oem_containers.detect_oem_kind(str(uapp)), "UPDATE_APP")
        self.assertEqual(header_magic.check_magic(str(uapp)), "UPDATE_APP")
        self.assertEqual(viewer_kind(str(uapp)), "oem")

        inspected = oem_containers.inspect_container(str(uapp))
        self.assertEqual(inspected["kind"], "UPDATE_APP")
        names = [e["name"] for e in inspected["entries"]]
        self.assertIn("system.img", names)
        self.assertIn("boot.img", names)

        out_sel = self.root / "uapp_out"
        ex = oem_containers.extract_container(str(uapp), str(out_sel), selected=["boot.img"])
        self.assertEqual(ex["extracted"], ["boot.img"])
        self.assertEqual((out_sel / "boot.img").read_bytes(), b"BOOT_OK")
        self.assertFalse((out_sel / "system.img").exists())

        dz = self.root / "firmware.dz"
        dz.write_bytes(_build_synthetic_dz([("boot_a", "chunk0", b"DZ_RAW_BYTES")]))
        self.assertEqual(oem_containers.detect_oem_kind(str(dz)), "DZ")
        dz_out = self.root / "dz_out"
        dz_ex = oem_containers.extract_container(str(dz), str(dz_out))
        self.assertEqual(dz_ex["extracted"], ["boot_a.img"])
        self.assertEqual((dz_out / "boot_a.img").read_bytes(), b"DZ_RAW_BYTES")

        rfs = self.root / "root.romfs"
        rfs.write_bytes(_build_synthetic_romfs([("hello.txt", b"world")]))
        self.assertEqual(oem_containers.detect_oem_kind(str(rfs)), "ROMFS")
        rfs_out = self.root / "rfs_out"
        rfs_ex = oem_containers.extract_container(str(rfs), str(rfs_out))
        self.assertEqual(rfs_ex["extracted"], ["hello.txt"])
        self.assertEqual((rfs_out / "hello.txt").read_bytes(), b"world")

        # Traversal protection inside UPDATE.APP entry names
        evil = self.root / "evil.app"
        evil.write_bytes(_build_synthetic_update_app([("../escape", b"BAD")]))
        evil_out = self.root / "evil_out"
        with self.assertRaises(Exception):
            oem_containers.extract_container(str(evil), str(evil_out))

    def test_f2fs_and_vbmeta_magic(self):
        f2fs = self.root / "userdata.img"
        f2fs_buf = bytearray(2048)
        f2fs_buf[1024:1028] = struct.pack("<I", 0xF2F52010)
        f2fs.write_bytes(bytes(f2fs_buf))
        self.assertEqual(header_magic.check_magic(str(f2fs)), "F2FS")

        vbm = self.root / "vbmeta.img"
        vbm.write_bytes(_build_vbmeta(0))
        self.assertEqual(header_magic.check_magic(str(vbm)), "VBMETA")

        _ensure_qt()
        from ui.directory import DirectoryEntry, DirectoryModel
        from ui.tree_entry_layout import entry_layout

        model = DirectoryModel(watch=False)
        for badge_name in ("VBMETA", "F2FS", "UPDATE_APP", "OZIP", "SQUASHFS"):
            node = DirectoryEntry(["file", "", ""])
            model._apply_badge_to_node(node, badge_name)
            self.assertEqual(node.data(0, 257), badge_name)
            layout = entry_layout("vbmeta.img", "12 KB", badge_name, False, "", 320, 24)
            self.assertTrue(layout["hasBadge"])
        model.close()

    def test_apk_scanner_icon_and_lossless_quarantine(self):
        proj = self.root / "proj"
        apk_path = proj / "product" / "app" / "BloatDemo" / "BloatDemo.apk"
        apk_path.parent.mkdir(parents=True, exist_ok=True)
        png_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
        with zipfile.ZipFile(apk_path, "w") as zf:
            zf.writestr(
                "AndroidManifest.xml",
                _build_axml("com.vendor.bloatdemo", "2.1.0", "android.permission.INTERNET"),
            )
            zf.writestr("res/drawable/ic_launcher.png", png_bytes)

        rows = apk_manager.scan_project_apks(str(proj))
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["package"], "com.vendor.bloatdemo")
        self.assertEqual(row["version_name"], "2.1.0")
        self.assertEqual(row["partition"], "product")
        self.assertIn("android.permission.INTERNET", row["permissions"])
        self.assertEqual(row["icon_entry"], "res/drawable/ic_launcher.png")

        data_url = apk_manager.load_apk_icon_data_url(row["path"], row["icon_entry"])
        self.assertTrue(data_url.startswith("data:image/png;base64,"))

        res = apk_manager.remove_apks(str(proj), [row])
        self.assertEqual(res["moved"], [row["rel_path"]])
        self.assertFalse(apk_path.exists())
        q = apk_manager.list_quarantined_apks(str(proj))
        self.assertEqual(len(q), 1)

        lst = self.root / "debloat.txt"
        apk_manager.export_debloat_list([row["package"]], str(lst))
        self.assertEqual(apk_manager.import_debloat_list(str(lst)), ["com.vendor.bloatdemo"])

        rest = apk_manager.restore_apks(str(proj), [row["rel_path"]])
        self.assertEqual(rest["restored"], [row["rel_path"]])
        self.assertTrue(apk_path.is_file())
        self.assertEqual(apk_manager.list_quarantined_apks(str(proj)), [])

    def test_quick_mods_fstab_vbmeta_prop_and_selinux(self):
        fstab = self.root / "vendor" / "etc" / "fstab.mt6877"
        fstab.parent.mkdir(parents=True, exist_ok=True)
        fstab.write_text(
            "/dev/block/by-name/system /system ext4 ro wait,avb=vbmeta_system,avb_keys=/avb/q.avbpubkey\n"
            "/dev/block/by-name/userdata /data f2fs rw wait,latemount,fileencryption=aes-256-xts:aes-256-cts:v2\n",
            encoding="utf-8",
        )
        prev = quick_mods.preview_fstab_patch(str(fstab), disable_avb=True, disable_encryption=True)
        self.assertTrue(prev["changed"])
        self.assertIn("latemount", prev["modified_text"])
        self.assertNotIn("avb=vbmeta_system", prev["modified_text"])
        self.assertNotIn("avb_keys=", prev["modified_text"])
        self.assertNotIn("fileencryption=", prev["modified_text"])

        quick_mods.apply_fstab_patch(str(fstab), disable_avb=True, disable_encryption=True)
        self.assertNotIn("avb=", fstab.read_text(encoding="utf-8"))

        vbm = self.root / "vbmeta_system.img"
        vbm.write_bytes(_build_vbmeta(0))
        info = quick_mods.inspect_vbmeta(str(vbm))
        self.assertTrue(info["valid"])
        self.assertEqual(info["flags"], 0)
        patched = quick_mods.apply_vbmeta_patch(str(vbm), disable_hashtree=True, disable_verification=True)
        self.assertEqual(patched["flags"], 3)
        self.assertTrue(patched["hashtree_disabled"])
        self.assertTrue(patched["verification_disabled"])
        self.assertEqual(quick_mods.inspect_vbmeta(str(vbm))["flags"], 3)

        log = (
            'avc: denied { read open } for pid=100 comm="cameraserver" '
            'scontext=u:r:hal_camera_default:s0 tcontext=u:object_r:vendor_file:s0 '
            "tclass=file permissive=1\n"
        )
        rules = quick_mods.generate_selinux_allow(log)
        self.assertIn("allow hal_camera_default vendor_file:file { open read };", rules["te_rules"])
        self.assertIn("(allow hal_camera_default vendor_file (file (open read)))", rules["cil_rules"])

        # SELinux directory detection & deduplicating .cil appender
        rom_a = self.root / "rom_direct"
        (rom_a / "etc" / "selinux").mkdir(parents=True)
        self.assertEqual(quick_mods.find_selinux_dir(str(rom_a)), str(rom_a / "etc" / "selinux"))

        rom_b = self.root / "rom_nested"
        (rom_b / "system" / "etc" / "selinux").mkdir(parents=True)
        self.assertEqual(quick_mods.find_selinux_dir(str(rom_b)), str(rom_b / "system" / "etc" / "selinux"))

        rom_none = self.root / "rom_plain"
        rom_none.mkdir(parents=True)
        self.assertEqual(quick_mods.find_selinux_dir(str(rom_none)), "")

        cil_target = rom_a / "etc" / "selinux" / "rak_custom_sepolicy.cil"
        first = quick_mods.append_cil_rules(str(cil_target), rules["cil_rules"])
        self.assertEqual(first["added"], 1)
        second = quick_mods.append_cil_rules(str(cil_target), rules["cil_rules"])
        self.assertEqual(second["added"], 0)
        self.assertEqual(second["skipped"], 1)

    def test_workspace_tools_and_separate_controllers(self):
        _ensure_qt()
        from unittest.mock import patch
        from ui.toolkit import WorkspaceToolkit
        from ui.workspace_tools import context_tools, dispatch_tool

        proj = self.root / "ws"
        (proj / "system" / "etc" / "selinux").mkdir(parents=True)
        (proj / "plain_dir").mkdir(parents=True)
        (proj / "config").mkdir(parents=True)
        uapp = proj / "UPDATE.APP"
        uapp.write_bytes(_build_synthetic_update_app([("BOOT", b"B")]))
        vbm = proj / "vbmeta.img"
        vbm.write_bytes(_build_vbmeta(0))
        init_boot = proj / "init_boot.img"
        init_boot.write_bytes(b"ANDROID!" + b"\x00" * 64)
        fst = proj / "system" / "etc" / "fstab.mt6765"
        fst.write_text("/dev/block/system /system ext4 ro wait,avb\n", encoding="utf-8")
        apkf = proj / "product" / "app" / "CameraTest" / "CameraTest.apk"
        apkf.parent.mkdir(parents=True)
        with zipfile.ZipFile(apkf, "w") as zf:
            zf.writestr("AndroidManifest.xml", _build_axml("com.example.test", "1.0", "android.permission.CAMERA"))

        class _Node:
            def __init__(self, badge: str, phys: str, folder: bool = False):
                self._badge = badge
                self._phys = phys
                self.folder = folder

            def data(self, _col: int, role: int):
                if role == 257:
                    return self._badge
                if role == 276:
                    return self._phys
                return None

        class _Tree:
            def __init__(self, base: str, nodes: dict):
                self.base = base
                self._nodes = nodes
                self.engine = None

            def reloadMetadata(self):
                pass

        class _Session(QObject):
            presentationRequested = Signal(str, object)

            def __init__(self, tree):
                super().__init__()
                self.active_tree = tree
                self.directories = (tree,)
                self.project = tree.base
                self.p1 = "."
                self.p2 = ""
                self._operation_serial = 0
                self.opened = []

            def openAs(self, p: str, kind: str):
                self.opened.append((p, kind))

        nodes = {
            str(uapp): _Node("UPDATE_APP", str(uapp)),
            str(vbm): _Node("VBMETA", str(vbm)),
            str(init_boot): _Node("BOOT", str(init_boot)),
            str(fst): _Node("CONF", str(fst)),
            str(apkf): _Node("APK", str(apkf)),
            str(proj / "system"): _Node("DIR", str(proj / "system"), folder=True),
            str(proj / "plain_dir"): _Node("DIR", str(proj / "plain_dir"), folder=True),
            str(proj / "config"): _Node("DIR", str(proj / "config"), folder=True),
        }
        session = _Session(_Tree(str(proj), nodes))
        events = []
        session.presentationRequested.connect(lambda k, d: events.append((k, d)))

        uapp_keys = {entry.get("key") for entry in context_tools(session, str(uapp)) if "key" in entry}
        self.assertIn("open_oem", uapp_keys)
        self.assertIn("unpack_oem", uapp_keys)

        vbm_keys = {entry.get("key") for entry in context_tools(session, str(vbm)) if "key" in entry}
        self.assertIn("patch_vbmeta", vbm_keys)

        with patch("core.binary_handler.is_available", return_value=True):
            boot_keys = {entry.get("key") for entry in context_tools(session, str(init_boot)) if "key" in entry}
        self.assertIn("magisk_patch", boot_keys)

        fst_keys = {entry.get("key") for entry in context_tools(session, str(fst)) if "key" in entry}
        self.assertIn("patch_fstab", fst_keys)

        apk_keys = {entry.get("key") for entry in context_tools(session, str(apkf)) if "key" in entry}
        self.assertNotIn("toolkit_apk", apk_keys)

        sys_keys = {entry.get("key") for entry in context_tools(session, str(proj / "system")) if "key" in entry}
        self.assertIn("sepolicy_rules_patch", sys_keys)
        self.assertIn("apk_debloater", sys_keys)

        plain_keys = {entry.get("key") for entry in context_tools(session, str(proj / "plain_dir")) if "key" in entry}
        self.assertNotIn("sepolicy_rules_patch", plain_keys)
        self.assertIn("apk_debloater", plain_keys)

        cfg_keys = {entry.get("key") for entry in context_tools(session, str(proj / "config")) if "key" in entry}
        self.assertNotIn("apk_debloater", cfg_keys)

        dispatch_tool(session, "open_oem", str(uapp), {})
        self.assertEqual(session.opened, [(str(uapp), "oem")])

        dispatch_tool(session, "apk_debloater", str(proj), {})
        self.assertEqual(events[-1][0], "apkDebloater")

        dispatch_tool(session, "sepolicy_rules_patch", str(proj / "system"), {})
        self.assertEqual(events[-1][0], "sepolicyPatch")

        dispatch_tool(session, "magisk_patch", str(init_boot), {})
        self.assertEqual(events[-1][0], "actionDialog")

        app = _ensure_qt()
        tk = WorkspaceToolkit(session)
        tk.openApkDebloater(str(proj))
        deadline = time.monotonic() + 3.0
        while tk.totalApkCount == 0 and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.02)
        self.assertEqual(tk.totalApkCount, 1)
        tk.setApkFilters("product/app/CameraTest", "All", "All")
        self.assertEqual(tk.apks.rowCount(), 1)
        self.assertEqual(tk.apks.snapshot()[0]["origin_folder"], "product/app/CameraTest")

        # Closing and reopening on a different folder must clear previous rows and scan cleanly.
        tk.dismiss()
        tk.openApkDebloater(str(proj / "plain_dir"))
        deadline = time.monotonic() + 3.0
        while tk.busy and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.02)
        self.assertFalse(tk.busy)
        self.assertEqual(tk.totalApkCount, 0)
        self.assertEqual(tk.apks.rowCount(), 0)

        tk.openSepolicyPatch(str(proj / "system"))
        self.assertEqual(tk.selinuxDir, str(proj / "system" / "etc" / "selinux"))
        avc_log = (
            'avc: denied { read open } for pid=100 comm="cameraserver" '
            'scontext=u:r:hal_camera_default:s0 tcontext=u:object_r:vendor_file:s0 '
            "tclass=file permissive=1\n"
        )
        tk.generateSelinuxRules(avc_log, False)
        self.assertIn("hal_camera_default", tk.selinuxCil)
        tk.applyCilRules("rak_custom_sepolicy.cil")
        deadline = time.monotonic() + 3.0
        cil_out = proj / "system" / "etc" / "selinux" / "rak_custom_sepolicy.cil"
        while not cil_out.is_file() and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.02)
        self.assertTrue(cil_out.is_file())
        tk.close()


if __name__ == "__main__":
    unittest.main()
