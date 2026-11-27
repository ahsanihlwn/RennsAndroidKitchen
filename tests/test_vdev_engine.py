# SPDX-License-Identifier: MPL-2.0
import os
import struct
import tempfile
import unittest
from pathlib import Path

from core import native_backend
from core.vdev import analyze
from core.vdev.selinux import Policy
from core.vdev.source import MemoryTreeSource
from core.vdev.vfs import VirtualFS


def _encode_varint(n: int) -> bytes:
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            break
    return bytes(out)


def _pb_str(field: int, text: str) -> bytes:
    raw = text.encode("utf-8")
    return _encode_varint((field << 3) | 2) + _encode_varint(len(raw)) + raw


def _pb_jar(path: str, cp_enum: int, min_sdk: str = "", max_sdk: str = "") -> bytes:
    msg = bytearray(_pb_str(1, path))
    if min_sdk:
        msg.extend(_pb_str(2, min_sdk))
    if max_sdk:
        msg.extend(_pb_str(3, max_sdk))
    msg.extend(_encode_varint((4 << 3) | 0))
    msg.extend(_encode_varint(cp_enum))
    return _encode_varint((1 << 3) | 2) + _encode_varint(len(msg)) + bytes(msg)


class TestVdevEngine(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self._prev_qemu_cache = os.environ.get("RAK_QEMU_CACHE_DIR")
        os.environ["RAK_QEMU_CACHE_DIR"] = str(self.root / "_qemu_test_cache")

    def tearDown(self):
        if self._prev_qemu_cache is None:
            os.environ.pop("RAK_QEMU_CACHE_DIR", None)
        else:
            os.environ["RAK_QEMU_CACHE_DIR"] = self._prev_qemu_cache
        self._tmp.cleanup()

    def test_classpath_protobuf_decoder(self):
        pb = (
            _pb_jar("/system/framework/framework.jar", 1, "30", "")
            + _pb_jar("/system/framework/services.jar", 3)
            + _pb_jar("/apex/com.android.art/javalib/core-oj.jar", 1)
        )
        rows = native_backend.vdev_parse_classpath_pb(pb)
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0]["path"], "/system/framework/framework.jar")
        self.assertEqual(rows[0]["classpath"], "BOOTCLASSPATH")
        self.assertEqual(rows[1]["classpath"], "SYSTEMSERVERCLASSPATH")
        self.assertEqual(rows[2]["path"], "/apex/com.android.art/javalib/core-oj.jar")

    def test_rust_selinux_policy_engine(self):
        pol = native_backend.vdev_selinux_policy()
        cil = "\n".join([
            "(type kernel)",
            "(type init)",
            "(type init_exec)",
            "(type zygote)",
            "(type zygote_exec)",
            "(typeattribute exec_type)",
            "(typeattributeset exec_type (or init_exec zygote_exec))",
            "(sidcontext kernel (u r kernel ((s0) (s0))))",
            "(allow kernel init_exec (file (execute)))",
            "(typetransition kernel init_exec process init)",
            "(allow init zygote_exec (file (execute)))",
            "(dontaudit init init_exec (file (write)))",
            "(typetransition init zygote_exec process zygote)",
        ])
        pol.load_texts([cil])
        self.assertEqual(pol.sid_domain("kernel"), "kernel")
        self.assertEqual(pol.domain_for_exec("kernel", "init_exec"), "init")
        self.assertEqual(pol.domain_for_exec("init", "zygote_exec"), "zygote")
        self.assertTrue(pol.allows("init", "zygote_exec", "file", "execute"))
        self.assertTrue(pol.silenced("init", "init_exec", "file", "write"))
        self.assertFalse(pol.silenced("init", "init_exec", "file", "read"))
        self.assertIn("zygote_exec", set(pol.resolve_attr("exec_type")))

    def test_vbmeta_bytes_inspector(self):
        hdr = bytearray(256)
        hdr[0:4] = b"AVB0"
        struct.pack_into(">I", hdr, 28, 1)      # algorithm
        struct.pack_into(">Q", hdr, 96, 0)      # descriptors offset
        struct.pack_into(">Q", hdr, 104, 0)     # descriptors size
        struct.pack_into(">Q", hdr, 112, 5)     # rollback index
        struct.pack_into(">I", hdr, 120, 3)     # flags = hashtree + verification disabled
        hdr[128:136] = b"avbtool\x00"
        res = native_backend.vdev_inspect_vbmeta_bytes(bytes(hdr))
        self.assertTrue(res["valid"])
        self.assertTrue(res["hashtree_disabled"])
        self.assertTrue(res["verification_disabled"])
        self.assertEqual(res["rollback_index"], 5)

    def test_super_zero_unpack_erofs_and_system_server_sim(self):
        # Build tiny system & vendor trees, pack to EROFS, wrap into super.img, analyze super.img directly!
        sys_dir = self.root / "src_system"
        (sys_dir / "system" / "etc" / "selinux").mkdir(parents=True)
        (sys_dir / "system" / "etc" / "init" / "hw").mkdir(parents=True)
        (sys_dir / "system" / "etc" / "classpaths").mkdir(parents=True)
        (sys_dir / "system" / "bin").mkdir(parents=True)
        (sys_dir / "system" / "lib64").mkdir(parents=True)
        (sys_dir / "system" / "framework").mkdir(parents=True)
        (sys_dir / "system" / "apex").mkdir(parents=True)
        (sys_dir / "vendor").mkdir(parents=True)

        (sys_dir / "system" / "build.prop").write_text(
            "ro.build.version.sdk=35\nro.build.version.release=15\nro.product.device=testdev\nro.zygote=zygote64\n",
            encoding="utf-8",
        )
        (sys_dir / "system" / "etc" / "selinux" / "plat_sepolicy.cil").write_text(
            "(type kernel)\n(type init)\n(type init_exec)\n(sidcontext kernel (u r kernel ((s0) (s0))))\n"
            "(allow kernel init_exec (file (execute)))\n(typetransition kernel init_exec process init)\n",
            encoding="utf-8",
        )
        (sys_dir / "system" / "etc" / "init" / "hw" / "init.rc").write_text(
            "on early-init\n    start servicemanager\non zygote-start\n    start zygote\n"
            "service servicemanager /system/bin/servicemanager\n    class core\n"
            "service zygote /system/bin/app_process64\n    class main\n",
            encoding="utf-8",
        )
        for p in ("init", "servicemanager", "linkerconfig", "apexd", "app_process64"):
            (sys_dir / "system" / "bin" / p).write_bytes(b"ELF_DUMMY")
        (sys_dir / "system" / "lib64" / "libandroid_runtime.so").write_bytes(b"ELF_DUMMY")
        (sys_dir / "system" / "framework" / "framework-res.apk").write_bytes(b"PK\x05\x06" + b"\x00" * 18)
        (sys_dir / "system" / "framework" / "services.jar").write_bytes(b"PK\x05\x06" + b"\x00" * 18)
        (sys_dir / "system" / "framework" / "framework.jar").write_bytes(b"PK\x05\x06" + b"\x00" * 18)
        (sys_dir / "system" / "etc" / "classpaths" / "bootclasspath.pb").write_bytes(
            _pb_jar("/system/framework/framework.jar", 1)
            + _pb_jar("/system/framework/services.jar", 3)
        )

        sys_img = self.root / "system.img"
        native_backend.erofs_pack(str(sys_dir), str(sys_img))

        super_img = self.root / "super.img"
        sys_sz = sys_img.stat().st_size
        aligned = ((sys_sz + 4095) // 4096) * 4096
        native_backend.super_make(
            str(super_img),
            device_size=32 * 1024 * 1024,
            groups=[{"name": "main", "max_size": 16 * 1024 * 1024}],
            partitions=[{"name": "system_a", "attr": "readonly", "size": aligned, "group": "main", "image": str(sys_img)}],
            virtual_ab=True,
            sparse=False,
        )

        probe = native_backend.vdev_probe_super(str(super_img), slot=0)
        names = [p["name"] for p in probe["partitions"]]
        self.assertIn("system", names)

        report = analyze(str(super_img), options={"slot": 0, "deep_linker": False})
        self.assertEqual(report.error, "")
        self.assertEqual(report.profile.get("sdk"), "35")
        self.assertTrue(report.boot_simulation.get("phases"))
        cp_phase = next(p for p in report.boot_simulation["phases"] if p["phase"] == "derive_classpath")
        self.assertEqual(cp_phase["status"], "PASS")

    def test_vintf_version_mismatch_and_autofix_and_diff(self):
        from core.vdev import compare_firmwares
        from core.vdev.autofix import build_autofix_plan
        from core.vdev.stages import s3_vintf
        from core.vdev.report import Report

        stock = self.root / "stock"
        port = self.root / "port"
        for base, v_ver in ((stock, "7.0"), (port, "6.0")):
            (base / "system" / "system" / "etc" / "vintf").mkdir(parents=True)
            (base / "vendor" / "etc" / "vintf").mkdir(parents=True)
            (base / "system" / "system" / "build.prop").write_text(
                "ro.build.version.sdk=35\nro.build.version.release=15\nro.product.device=dev\n",
                encoding="utf-8",
            )
            (base / "system" / "system" / "etc" / "vintf" / "compatibility_matrix.7.xml").write_text(
                '<compatibility-matrix version="1.0" type="framework" level="7">'
                '<hal format="hidl"><name>android.hardware.audio</name><version>7.0-1</version></hal>'
                '</compatibility-matrix>',
                encoding="utf-8",
            )
            (base / "vendor" / "etc" / "vintf" / "manifest.xml").write_text(
                f'<manifest version="1.0" type="device" target-level="7">'
                f'<hal format="hidl"><name>android.hardware.audio</name><version>{v_ver}</version></hal>'
                f'</manifest>',
                encoding="utf-8",
            )

        diff = compare_firmwares(str(stock), str(port), options={"deep_linker": False})
        self.assertTrue(any(w["code"] == "hal-version-mismatch" for w in diff["new_warns"]))

        # Verify autofix generates SELinux allow rules and privapp XML
        class _DummyCtx:
            fstab_origin = ""
            shared = {
                "avc": {
                    "denial_lines": [
                        'avc: denied { execute } for path="/vendor/bin/foo" scontext=u:r:init:s0 tcontext=u:object_r:foo_exec:s0 tclass=file'
                    ],
                    "xattr_drift": [{"path": "/vendor/bin/foo", "actual": "u:object_r:vendor_file:s0", "expected": "u:object_r:foo_exec:s0"}],
                },
                "framework": {
                    "privapp_violations": [{"package": "com.example.priv", "vpath": "/system/priv-app/P/P.apk", "permissions": ["android.permission.REBOOT"]}]
                },
                "linker": {"unresolved": {"libmissing.so": {"/system/bin/x"}}, "abi_mismatch": {}},
            }

        plan = build_autofix_plan(_DummyCtx(), Report())
        ids = {a["id"] for a in plan}
        self.assertIn("selinux-audit2allow", ids)
        self.assertIn("selinux-xattr-align", ids)
        self.assertIn("privapp-allowlist-xml", ids)
        self.assertIn("linker-missing-libs", ids)
        priv_act = next(a for a in plan if a["id"] == "privapp-allowlist-xml")
        self.assertIn('package="com.example.priv"', priv_act["content"])

    def test_rak_cli_vdev_analyze_and_diff(self):
        import json
        from core import rak_cli

        rom_a = self.root / "cli_rom_a"
        rom_b = self.root / "cli_rom_b"
        for d, rel in ((rom_a, "15"), (rom_b, "15")):
            (d / "system" / "system").mkdir(parents=True)
            (d / "system" / "system" / "build.prop").write_text(
                f"ro.build.version.sdk=35\nro.build.version.release={rel}\n"
                "ro.product.device=cli_dev\n",
                encoding="utf-8",
            )

        ok, msg = rak_cli.execute_command(f'vdev analyze "{rom_a}" --json', cwd=str(self.root))
        self.assertTrue(ok, msg)
        parsed = json.loads(msg)
        self.assertEqual(parsed["profile"]["sdk"], "35")
        self.assertIn("boot_simulation", parsed)
        self.assertIn("autofix", parsed)

        ok2, msg2 = rak_cli.execute_command(f'vdev diff "{rom_a}" "{rom_b}" --json', cwd=str(self.root))
        self.assertTrue(ok2, msg2)
        diff = json.loads(msg2)
        self.assertIn("stock_summary", diff)
        self.assertIn("port_summary", diff)

    def test_workspace_mounts_bridge_and_spl_coherence(self):
        from core import native_backend
        from core.vdev import analyze_workspace_mounts
        from core.vdev.stages import s0_device
        from core.vdev.context import Context
        from core.vdev.mounts import Layout
        from core.vdev.vfs import VirtualFS
        from core.vdev.source import MemoryTreeSource

        # Build a real EROFS system.img in an external folder and expose it via mock WorkspaceMounts
        src_dir = self.root / "mnt_sys_src"
        (src_dir / "system" / "lib64").mkdir(parents=True)
        (src_dir / "system" / "build.prop").write_text(
            "ro.build.version.sdk=35\nro.build.version.release=15\n"
            "ro.product.device=mounted_dev\nro.build.version.security_patch=2025-02-05\n",
            encoding="utf-8",
        )
        ext_img_dir = self.root / "external_imgs"
        ext_img_dir.mkdir()
        sys_img = ext_img_dir / "system.img"
        native_backend.erofs_pack(str(src_dir), str(sys_img))

        proj_base = self.root / "empty_workspace"
        proj_base.mkdir()

        class _FakeReg:
            metadata = {str(sys_img): {"physical": str(sys_img), "fs": "EROFS"}}

        class _FakeMounts:
            _image_base = {str(sys_img): str(proj_base)}
            _registries = {str(proj_base): _FakeReg()}

        rep = analyze_workspace_mounts(_FakeMounts(), base=str(proj_base), options={"deep_linker": False})
        self.assertEqual(rep.error, "")
        self.assertEqual(rep.profile.get("sdk"), "35")
        self.assertEqual(rep.profile["props"].get("ro.product.device"), "mounted_dev")

        # Verify SPL & boot os_version mismatch detection in s0_device
        layout = Layout(str(proj_base))
        layout.boot_inspections["boot"] = {
            "header": {"os_version": "14.0.0", "os_patch_level": "2024-11"}
        }
        mem = MemoryTreeSource({
            "/system/build.prop": (
                b"ro.build.version.sdk=35\nro.build.version.release=15\n"
                b"ro.build.version.security_patch=2025-02-05\n"
            )
        }, label="mem")
        vfs = VirtualFS()
        vfs.add_mount("/", mem, "system")
        ctx = Context(str(proj_base), layout, vfs, None)
        findings = s0_device.run(ctx)
        codes = {f.code for f in findings}
        self.assertIn("spl-mismatch", codes)
        self.assertIn("boot-os-mismatch", codes)

    def test_rust_dex_vdex_verifier_catches_smali_bugs(self):
        import json
        import struct
        from core import native_backend

        def _uleb(val: int) -> bytes:
            out = bytearray()
            while True:
                b = val & 0x7F
                val >>= 7
                if val:
                    out.append(b | 0x80)
                else:
                    out.append(b)
                    break
            return bytes(out)

        def _build_minimal_dex(
            class_desc: str,
            super_desc: str,
            method_name: str,
            ret_desc: str,
            called_owner: str | None,
            called_name: str | None,
            insns: list[int],
            regs_size: int = 2,
            ins_size: int = 1,
        ) -> bytes:
            """Craft a real valid DEX 035 binary in memory for deterministic Rust verifier tests."""
            strings = sorted({
                class_desc, super_desc, method_name, ret_desc, "V",
                *([called_owner, called_name] if called_owner and called_name else []),
            })
            s_idx = {s: i for i, s in enumerate(strings)}
            types = sorted({class_desc, super_desc, ret_desc, "V", *([called_owner] if called_owner else [])})
            t_idx = {t: i for i, t in enumerate(types)}
            # protos: (shorty, ret_type, params_off=0)
            protos = sorted({("V", t_idx["V"]), (ret_desc[0], t_idx[ret_desc])})
            p_idx = {p: i for i, p in enumerate(protos)}
            methods = [(t_idx[class_desc], p_idx[(ret_desc[0], t_idx[ret_desc])], s_idx[method_name])]
            if called_owner and called_name:
                methods.append((t_idx[called_owner], p_idx[("V", t_idx["V"])], s_idx[called_name]))
            methods_sorted = sorted(set(methods))
            m_idx = {m: i for i, m in enumerate(methods_sorted)}

            # Layout sections cleanly after 0x70 header
            off = 0x70
            str_ids_off = off; off += len(strings) * 4
            type_ids_off = off; off += len(types) * 4
            proto_ids_off = off; off += len(protos) * 12
            method_ids_off = off; off += len(methods_sorted) * 8
            class_defs_off = off; off += 32

            data = bytearray(off)
            # String data items
            str_offs = []
            for s in strings:
                str_offs.append(len(data))
                raw = s.encode("utf-8")
                data += _uleb(len(s)) + raw + b"\x00"
            while len(data) % 4:
                data.append(0)

            # Code item (16-byte header + insns u16[])
            code_off = len(data)
            # Patch method_idx in any invoke-virtual (0x6e) instruction if called method present
            patched_insns = list(insns)
            if called_owner and called_name:
                target_midx = m_idx[(t_idx[called_owner], p_idx[("V", t_idx["V"])], s_idx[called_name])]
                for i in range(len(patched_insns)):
                    if (patched_insns[i] & 0xFF) in (0x6E, 0x71) and i + 1 < len(patched_insns):
                        patched_insns[i + 1] = target_midx
            data += struct.pack("<HHHHII", regs_size, ins_size, 0, 0, 0, len(patched_insns))
            for w in patched_insns:
                data += struct.pack("<H", w & 0xFFFF)
            while len(data) % 4:
                data.append(0)

            # class_data_item: 0 static fields, 0 inst fields, 0 direct methods, 1 virtual method
            class_data_off = len(data)
            my_midx = m_idx[(t_idx[class_desc], p_idx[(ret_desc[0], t_idx[ret_desc])], s_idx[method_name])]
            data += _uleb(0) + _uleb(0) + _uleb(0) + _uleb(1)
            data += _uleb(my_midx) + _uleb(0x0001) + _uleb(code_off)
            while len(data) % 4:
                data.append(0)

            file_size = len(data)
            # Fill header
            hdr = bytearray(0x70)
            hdr[0:8] = b"dex\n035\x00"
            struct.pack_into("<I", hdr, 32, file_size)
            struct.pack_into("<I", hdr, 36, 0x70)
            struct.pack_into("<I", hdr, 40, 0x12345678)
            struct.pack_into("<II", hdr, 56, len(strings), str_ids_off)
            struct.pack_into("<II", hdr, 64, len(types), type_ids_off)
            struct.pack_into("<II", hdr, 72, len(protos), proto_ids_off)
            struct.pack_into("<II", hdr, 88, len(methods_sorted), method_ids_off)
            struct.pack_into("<II", hdr, 96, 1, class_defs_off)
            data[0:0x70] = hdr

            for i, so in enumerate(str_offs):
                struct.pack_into("<I", data, str_ids_off + i * 4, so)
            for i, t in enumerate(types):
                struct.pack_into("<I", data, type_ids_off + i * 4, s_idx[t])
            for i, (sh, rt) in enumerate(protos):
                struct.pack_into("<III", data, proto_ids_off + i * 12, s_idx[sh], rt, 0)
            for i, (ci, pi, ni) in enumerate(methods_sorted):
                struct.pack_into("<HHI", data, method_ids_off + i * 8, ci, pi, ni)
            struct.pack_into(
                "<IIIIIIII",
                data,
                class_defs_off,
                t_idx[class_desc],
                0x0001,
                t_idx[super_desc],
                0,
                0xFFFFFFFF,
                0,
                class_data_off,
                0,
            )
            return bytes(data)

        # Provider: java.lang.Object + android.app.Activity (with existingMethod only)
        obj_dex = _build_minimal_dex(
            "Ljava/lang/Object;", "Ljava/lang/Object;", "toString", "V", None, None, [0x000E], regs_size=1, ins_size=1
        )
        act_dex = _build_minimal_dex(
            "Landroid/app/Activity;", "Ljava/lang/Object;", "onCreate", "V", None, None, [0x000E], regs_size=1, ins_size=1
        )

        # Target 1 (wrapped in synthetic VDEX v027!): SystemServer with guaranteed NPE + NoSuchMethodError
        # insns: const/4 v0, 0 (0x0012); invoke-virtual {v0}, Landroid/app/Activity;->missingMethod()V (0x106e, midx, 0x0000); return-void (0x000e)
        bad_npe_and_nsme = _build_minimal_dex(
            "Lcom/android/server/SystemServer;",
            "Ljava/lang/Object;",
            "run",
            "V",
            "Landroid/app/Activity;",
            "missingMethod",
            [0x0012, 0x106E, 0x0000, 0x0000, 0x000E],
            regs_size=2,
            ins_size=1,
        )
        vdex_blob = b"vdex027\x00" + b"\x00" * 16 + bad_npe_and_nsme

        # Target 2: VerifyError (out-of-bounds register v9 when regs_size=2 + return-void in int method) + missing superclass
        bad_verify_and_super = _build_minimal_dex(
            "Lcom/android/systemui/BrokenUi;",
            "Lcom/samsung/android/MissingSemBase;",
            "getCount",
            "I",
            None,
            None,
            [0x0912, 0x000E],  # const/4 v9, 0 (v9 >= 2!) + return-void in method returning I!
            regs_size=2,
            ins_size=1,
        )

        v = native_backend.vdev_dex_verifier()
        self.assertGreater(v.add_provider_bytes("core-oj.jar", obj_dex), 0)
        self.assertGreater(v.add_provider_bytes("framework.jar", act_dex), 0)
        self.assertGreater(v.add_target_bytes("services.jar", vdex_blob, True), 0)
        self.assertGreater(v.add_target_bytes("SystemUI.apk", bad_verify_and_super, True), 0)

        rep = json.loads(v.verify_json(["android", "com.android.systemui"]))
        self.assertEqual(rep["stats"]["vdex_extracted"], 1)
        self.assertTrue(any(e["kind"] == "NullPointerException" and e["boot_critical"] for e in rep["null_deref_risks"]), rep)
        self.assertTrue(any("missingMethod" in e["target"] for e in rep["missing_methods"]), rep)
        self.assertTrue(any("register v9 out of bounds" in e["detail"] for e in rep["verify_errors"]), rep)
        self.assertTrue(any("return-void" in e["detail"] for e in rep["verify_errors"]), rep)
        self.assertTrue(any("MissingSemBase" in e["target"] for e in rep["missing_superclasses"]), rep)

    def test_explicit_staged_layout_and_live_callbacks(self):
        from core import native_backend
        from core.vdev import analyze

        # Create messy folder containing unrelated .img AND explicit staged EROFS system + vendor dirs
        messy = self.root / "messy_folder"
        messy.mkdir()
        (messy / "unrelated_junk.img").write_bytes(b"\x00" * 4096)

        sys_src = self.root / "staged_sys_src"
        (sys_src / "system").mkdir(parents=True)
        (sys_src / "system" / "build.prop").write_text(
            "ro.build.version.sdk=35\nro.build.version.release=15\nro.product.device=staged_dev\n",
            encoding="utf-8",
        )
        sys_img = self.root / "custom_sys.img"
        native_backend.erofs_pack(str(sys_src), str(sys_img))

        vnd_dir = self.root / "staged_vendor_dir"
        (vnd_dir / "etc").mkdir(parents=True)
        (vnd_dir / "build.prop").write_text("ro.vendor.build.version.sdk=35\n", encoding="utf-8")

        vfs_hits = []
        stage_keys = []

        def on_vfs(vfs, _ramdisk, _layout, _fstab, _origin):
            vfs_hits.append(vfs.read_text("/system/build.prop") or "")

        def on_stage(stage_res, _ctx):
            stage_keys.append(stage_res.key)

        rep = analyze(
            str(messy),
            options={
                "staged": {
                    "partitions": {
                        "system": str(sys_img),
                        "vendor": str(vnd_dir),
                    }
                },
                "deep_linker": False,
            },
            on_vfs=on_vfs,
            on_stage=on_stage,
        )
        self.assertEqual(rep.error, "")
        self.assertEqual(rep.profile.get("props", {}).get("ro.product.device"), "staged_dev")
        self.assertEqual(len(vfs_hits), 1)
        self.assertIn("staged_dev", vfs_hits[0])
        self.assertGreaterEqual(len(stage_keys), 10)
        self.assertIn("system_server", stage_keys)

    def test_dtbo_deep_analysis_and_avb_hash(self):
        import hashlib
        from core import native_backend
        from core.vdev.stages import s10_kernel
        from core.vdev.context import Context
        from core.vdev.mounts import Layout
        from core.vdev.vfs import VirtualFS
        from core.vdev.source import MemoryTreeSource

        # 1) Corrupt DTBO file -> BLOCKER dtbo-corrupt
        bad_dtbo = self.root / "bad_dtbo.img"
        bad_dtbo.write_bytes(b"NOT_A_DTBO" + b"\x00" * 128)
        rep_bad = native_backend.vdev_inspect_dtbo(str(bad_dtbo))
        self.assertFalse(rep_bad["valid"])

        # 2) AVB hash verification (match + mismatch)
        salt = b"\xaa\xbb\xcc\xdd"
        payload = b"DTBO_SAMPLE_PAYLOAD_BYTES" * 16
        img_file = self.root / "hash_dtbo.img"
        img_file.write_bytes(payload + b"\x00" * 64)
        expected = hashlib.sha256(salt + payload).hexdigest()
        res_ok = native_backend.vdev_verify_avb_hash(
            str(img_file), len(payload), "sha256", salt.hex(), expected
        )
        self.assertTrue(res_ok["matched"])
        res_bad = native_backend.vdev_verify_avb_hash(
            str(img_file), len(payload), "sha256", salt.hex(), "00" * 32
        )
        self.assertFalse(res_bad["matched"])

        # 3) s10_kernel._check_dtbo catches SoC mismatch & missing __fixups__
        layout = Layout(str(self.root))
        layout.dtbo_source = "dtbo.img"
        layout.dtbo_report = {
            "valid": True,
            "version": 0,
            "entry_count": 1,
            "valid_fdt_count": 1,
            "invalid_fdt_indices": [],
            "entries": [{
                "index": 0,
                "fixups": ["disp0", "dsi0", "i2c1", "pmic_a", "soc"],
                "compatibles": ["qcom,sm6225-idp"],
            }],
            "all_compatibles": ["qcom,sm6225-idp"],
            "all_fixups": ["disp0", "dsi0", "i2c1", "pmic_a", "soc"],
            "all_fstab": [],
        }
        boot_insps = {
            "vendor_boot": {
                "dtb": {
                    "compatibles": ["mediatek,mt6855"],
                    "symbols": ["soc"],
                }
            }
        }
        mem = MemoryTreeSource({"/system/build.prop": b"ro.board.platform=mt6855\n"}, label="mem")
        vfs = VirtualFS()
        vfs.add_mount("/", mem, "system")
        ctx = Context(str(self.root), layout, vfs, None)
        ctx.profile = {"props": {"ro.board.platform": "mt6855"}}
        findings = []
        s10_kernel._check_dtbo(ctx, boot_insps, findings)
        codes = {f.code for f in findings}
        self.assertIn("dtbo-fixups-incompatible", codes)
        self.assertIn("dtbo-soc-mismatch", codes)

    def test_sabotage_scenarios_avb_toggle_and_critical_privapps(self):
        from core.vdev.stages import s1_mount, s7_framework
        from core.vdev.apkinfo import ApkInfo
        from core.vdev.context import Context
        from core.vdev.mounts import Layout, FstabEntry
        from core.vdev.vfs import VirtualFS
        from core.vdev.source import MemoryTreeSource

        # 1) Critical priv-app checks: missing SystemUI & SettingsProvider -> BLOCKER,
        #    missing PackageInstaller & Settings -> WARN only
        mem = MemoryTreeSource({
            "/system/framework/framework-res.apk": b"PK",
            "/system/framework/services.jar": b"PK",
        }, label="mem")
        vfs = VirtualFS()
        vfs.add_mount("/", mem, "system")
        layout = Layout(str(self.root))
        ctx = Context(str(self.root), layout, vfs, None)
        f_missing = []
        s7_framework._check_core(ctx, [], f_missing)
        by_code = {f.code: f.severity for f in f_missing}
        self.assertEqual(by_code.get("no-systemui"), "blocker")
        self.assertEqual(by_code.get("no-settings-provider"), "blocker")
        self.assertEqual(by_code.get("no-package-installer"), "warn")
        self.assertEqual(by_code.get("no-settings-app"), "warn")

        # When SystemUI + SettingsProvider + Launcher present -> no core blockers
        apks = []
        for pkg, is_home in (
            ("com.android.systemui", False),
            ("com.android.providers.settings", False),
            ("com.android.permissioncontroller", False),
            ("com.android.packageinstaller", False),
            ("com.android.settings", False),
            ("com.android.launcher3", True),
        ):
            a = ApkInfo(f"/system/priv-app/{pkg}/{pkg}.apk", None)
            a.package = pkg
            a.is_launcher = is_home
            apks.append(a)
        f_ok = []
        s7_framework._check_core(ctx, apks, f_ok)
        self.assertFalse(any(f.severity == "blocker" for f in f_ok), f_ok)

        # 2) Corrupt vbmeta & edited AVB partition: AVB ON -> BLOCKER, AVB OFF -> bypassed (0 blockers)
        layout2 = Layout(str(self.root))
        layout2.partitions["system"] = str(self.root)
        layout2.invalid_vbmeta = [("vbmeta", str(self.root / "vbmeta.img"))]
        fstab = [FstabEntry("system", "/system", "erofs", ["ro"], ["avb=vbmeta_system", "logical"])]

        ctx_on = Context(str(self.root), layout2, vfs, None, fstab=fstab, options={"vbmeta_enabled": True})
        f_on = []
        s1_mount._check_avb(ctx_on, fstab, f_on)
        on_codes = {f.code for f in f_on if f.severity == "blocker"}
        self.assertIn("vbmeta-corrupt", on_codes)
        self.assertIn("avb-verity-will-fail", on_codes)

        ctx_off = Context(str(self.root), layout2, vfs, None, fstab=fstab, options={"vbmeta_enabled": False})
        f_off = []
        s1_mount._check_avb(ctx_off, fstab, f_off)
        self.assertFalse(any(f.severity == "blocker" for f in f_off), f_off)
        self.assertTrue(any(f.code == "avb-bypassed" for f in f_off))

    def test_dex_logic_dataflow_parser(self):
        import json
        import struct
        from core import native_backend
        from core.vdev.stages import s13_dex_bytecode
        from core.vdev.context import Context
        from core.vdev.mounts import Layout
        from core.vdev.vfs import VirtualFS
        from core.vdev.source import MemoryTreeSource

        # Build a valid minimal DEX whose method runs:
        #   const-string v0, "missing_jni_foo"  (0x1a)
        #   invoke-static {v0}, Ljava/lang/System;->loadLibrary(Ljava/lang/String;)V (0x71)
        #   const-string v0, "ro.custom.test"   (0x1a)
        #   invoke-static {v0}, Landroid/os/SystemProperties;->get(Ljava/lang/String;)Ljava/lang/String; (0x71)
        #   return-void (0x0e)
        def _uleb(val: int) -> bytes:
            out = bytearray()
            while True:
                b = val & 0x7F
                val >>= 7
                if val:
                    out.append(b | 0x80)
                else:
                    out.append(b)
                    return bytes(out)

        def _mutf8(s: str) -> bytes:
            b = s.encode("utf-8")
            return _uleb(len(s)) + b + b"\x00"

        strings = sorted(set([
            "L", "VL",
            "Landroid/os/SystemProperties;",
            "Lcom/android/server/SystemServer;",
            "Ljava/lang/Object;",
            "Ljava/lang/String;",
            "Ljava/lang/System;",
            "V",
            "boot",
            "get",
            "loadLibrary",
            "missing_jni_foo",
            "ro.custom.test",
        ]))
        sidx = {s: i for i, s in enumerate(strings)}
        types = [
            "Landroid/os/SystemProperties;",
            "Lcom/android/server/SystemServer;",
            "Ljava/lang/Object;",
            "Ljava/lang/String;",
            "Ljava/lang/System;",
            "V",
        ]
        tidx = {t: i for i, t in enumerate(types)}
        # protos: 0=()V, 1=(String)V, 2=(String)String
        protos = [
            (sidx["V"], tidx["V"], []),
            (sidx["VL"], tidx["V"], [tidx["Ljava/lang/String;"]]),
            (sidx["L"], tidx["Ljava/lang/String;"], [tidx["Ljava/lang/String;"]]),
        ]
        methods = [
            (tidx["Landroid/os/SystemProperties;"], 2, sidx["get"]),
            (tidx["Lcom/android/server/SystemServer;"], 0, sidx["boot"]),
            (tidx["Ljava/lang/System;"], 1, sidx["loadLibrary"]),
        ]
        insns = [
            0x001A, sidx["missing_jni_foo"],        # const-string v0, "missing_jni_foo"
            0x1071, 2, 0x0000,                      # invoke-static {v0}, System.loadLibrary
            0x001A, sidx["ro.custom.test"],         # const-string v0, "ro.custom.test"
            0x1071, 0, 0x0000,                      # invoke-static {v0}, SystemProperties.get
            0x000E,                                 # return-void
        ]

        data = bytearray(0x70)
        str_ids_off = len(data)
        data.extend(b"\x00" * (4 * len(strings)))
        type_ids_off = len(data)
        for t in types:
            data.extend(struct.pack("<I", sidx[t]))
        proto_ids_off = len(data)
        data.extend(b"\x00" * (12 * len(protos)))
        method_ids_off = len(data)
        for ci, pi, ni in methods:
            data.extend(struct.pack("<HHI", ci, pi, ni))
        class_defs_off = len(data)
        data.extend(b"\x00" * 32)
        data_off = len(data)

        for i, s in enumerate(strings):
            off = len(data)
            data.extend(_mutf8(s))
            struct.pack_into("<I", data, str_ids_off + i * 4, off)
        for i, (si, rti, params) in enumerate(protos):
            poff = 0
            if params:
                while len(data) % 4:
                    data.append(0)
                poff = len(data)
                data.extend(struct.pack("<I", len(params)))
                for p in params:
                    data.extend(struct.pack("<H", p))
            struct.pack_into("<III", data, proto_ids_off + i * 12, si, rti, poff)
        while len(data) % 4:
            data.append(0)
        code_off = len(data)
        data.extend(struct.pack("<HHHHI", 2, 0, 1, 0, 0)[:-2])
        # Rewrite code_item header accurately: regs=2, ins=0, outs=1, tries=0, debug_off=0, insns_size
        data = data[:code_off]
        data.extend(struct.pack("<HHHHII", 2, 0, 1, 0, 0, len(insns)))
        for w in insns:
            data.extend(struct.pack("<H", w))
        class_data_off = len(data)
        data.extend(_uleb(0) + _uleb(0) + _uleb(1) + _uleb(0))
        data.extend(_uleb(1) + _uleb(0x0009) + _uleb(code_off))  # method 1 = boot, public static
        struct.pack_into(
            "<IIIIIIII", data, class_defs_off,
            tidx["Lcom/android/server/SystemServer;"], 0x0001, tidx["Ljava/lang/Object;"],
            0, 0xFFFFFFFF, 0, class_data_off, 0,
        )
        file_size = len(data)
        struct.pack_into(
            "<8sI20sIIIIIIIIIIIIIIIIIIII", data, 0,
            b"dex\n039\x00", 0, b"\x00" * 20, file_size, 0x70, 0x12345678,
            0, 0, 0, len(strings), str_ids_off, len(types), type_ids_off,
            len(protos), proto_ids_off, 0, 0, len(methods), method_ids_off,
            1, class_defs_off, file_size - data_off, data_off,
        )

        v = native_backend.vdev_dex_verifier()
        self.assertGreater(v.add_target_bytes("services.jar", bytes(data), True), 0)
        rep = json.loads(v.verify_json([]))
        lm = rep.get("logic_map") or {}
        jni_names = [x["value"] for x in (lm.get("jni_libraries") or [])]
        self.assertIn("missing_jni_foo", jni_names, lm)
        self.assertIn("ro.custom.test", lm.get("system_properties") or [], lm)

        # Verify Python _audit_logic_map flags missing JNI library cleanly
        mem = MemoryTreeSource({"/system/lib64/libexisting.so": b"\x7fELF"}, label="mem")
        vfs = VirtualFS()
        vfs.add_mount("/", mem, "system")
        lay = Layout(str(self.root))
        lay.partitions["vendor"] = str(self.root)
        ctx = Context(str(self.root), lay, vfs, None)
        ctx.profile = {"props": {"ro.custom.test": "1"}}
        findings = []
        summary = s13_dex_bytecode._audit_logic_map(ctx, lm, [], findings)
        self.assertIn("libmissing_jni_foo.so", summary["missing_jni"])
        self.assertEqual(summary["system_properties_set_count"], 1)
        self.assertTrue(any(f.code == "dex-missing-jni-lib" for f in findings))

    def test_dex_tier2_semantic_logic_all_five(self):
        """Verify all 5 Tier-2 Semantic DEX features:
        1. static final String (static_values_off + sget-object) + StringBuilder concat
        2. try/catch Guard Analyzer (guarded vs unguarded)
        3. Feature & SystemProperties Dead-Branch Pruning
        4. Reflection Method & Field Audit (Class.forName -> getDeclaredMethod)
        5. JNI Java_* Static ELF Symbol Binding Audit (static vs JNI_OnLoad)
        """
        import json
        import struct
        from core import native_backend
        from core.vdev.stages import s13_dex_bytecode
        from core.vdev.context import Context
        from core.vdev.mounts import Layout
        from core.vdev.vfs import VirtualFS
        from core.vdev.source import MemoryTreeSource

        def _uleb(val: int) -> bytes:
            out = bytearray()
            while True:
                b = val & 0x7F
                val >>= 7
                if val:
                    out.append(b | 0x80)
                else:
                    out.append(b)
                    return bytes(out)

        def _sleb(val: int) -> bytes:
            out = bytearray()
            more = True
            while more:
                b = val & 0x7F
                val >>= 7
                if (val == 0 and (b & 0x40) == 0) or (val == -1 and (b & 0x40) != 0):
                    more = False
                else:
                    b |= 0x80
                out.append(b)
            return bytes(out)

        def _mutf8(s: str) -> bytes:
            b = s.encode("utf-8")
            return _uleb(len(s)) + b + b"\x00"

        def _build_tier2_dex():
            strings = sorted(set([
                "<init>", "DESCRIPTOR", "L", "LL", "VL", "ZL",
                "Landroid/content/pm/PackageManager;",
                "Landroid/os/ServiceManager;",
                "Lcom/android/server/SystemServer;",
                "Lcom/android/server/SystemServiceManager;",
                "Lcom/android/server/Target;",
                "Ljava/lang/Class;",
                "Ljava/lang/Object;",
                "Ljava/lang/String;",
                "Ljava/lang/StringBuilder;",
                "Ljava/lang/System;",
                "Ljava/lang/UnsatisfiedLinkError;",
                "Ljava/lang/reflect/Method;",
                "V", "Z",
                "/default",
                "android.hardware.power.IPower",
                "android.hardware.type.automotive",
                "append",
                "com.android.car.CarService",
                "com.android.server.Target",
                "existingMethod",
                "getDeclaredMethod",
                "guarded_missing",
                "hasSystemFeature",
                "loadLibrary",
                "missingMethod",
                "nativeMissing",
                "nativeOk",
                "runChecks",
                "startService",
                "static_bridge",
                "toString",
                "unguarded_missing",
                "waitForDeclaredService",
                "forName",
            ]))
            sidx = {s: i for i, s in enumerate(strings)}
            types = sorted(set([
                "Landroid/content/pm/PackageManager;",
                "Landroid/os/ServiceManager;",
                "Lcom/android/server/SystemServer;",
                "Lcom/android/server/SystemServiceManager;",
                "Lcom/android/server/Target;",
                "Ljava/lang/Class;",
                "Ljava/lang/Object;",
                "Ljava/lang/String;",
                "Ljava/lang/StringBuilder;",
                "Ljava/lang/System;",
                "Ljava/lang/UnsatisfiedLinkError;",
                "Ljava/lang/reflect/Method;",
                "V", "Z",
            ]))
            tidx = {t: i for i, t in enumerate(types)}
            # protos:
            # 0: ()V
            # 1: (String)V
            # 2: (String)String
            # 3: (String)StringBuilder
            # 4: ()String
            # 5: (String)Z
            # 6: (String)Class
            # 7: (String)Method
            protos = [
                (sidx["V"], tidx["V"], []),
                (sidx["VL"], tidx["V"], [tidx["Ljava/lang/String;"]]),
                (sidx["LL"], tidx["Ljava/lang/String;"], [tidx["Ljava/lang/String;"]]),
                (sidx["LL"], tidx["Ljava/lang/StringBuilder;"], [tidx["Ljava/lang/String;"]]),
                (sidx["L"], tidx["Ljava/lang/String;"], []),
                (sidx["ZL"], tidx["Z"], [tidx["Ljava/lang/String;"]]),
                (sidx["LL"], tidx["Ljava/lang/Class;"], [tidx["Ljava/lang/String;"]]),
                (sidx["LL"], tidx["Ljava/lang/reflect/Method;"], [tidx["Ljava/lang/String;"]]),
            ]
            # fields: 0 = Target.DESCRIPTOR:Ljava/lang/String;
            fields = [
                (tidx["Lcom/android/server/Target;"], tidx["Ljava/lang/String;"], sidx["DESCRIPTOR"]),
            ]
            # methods:
            methods = [
                (tidx["Landroid/content/pm/PackageManager;"], 5, sidx["hasSystemFeature"]),     # 0
                (tidx["Landroid/os/ServiceManager;"], 2, sidx["waitForDeclaredService"]),       # 1
                (tidx["Lcom/android/server/SystemServer;"], 0, sidx["nativeMissing"]),          # 2
                (tidx["Lcom/android/server/SystemServer;"], 0, sidx["nativeOk"]),               # 3
                (tidx["Lcom/android/server/SystemServer;"], 0, sidx["runChecks"]),              # 4
                (tidx["Lcom/android/server/SystemServiceManager;"], 1, sidx["startService"]),   # 5
                (tidx["Lcom/android/server/Target;"], 0, sidx["existingMethod"]),               # 6
                (tidx["Ljava/lang/Class;"], 6, sidx["forName"]),                                # 7
                (tidx["Ljava/lang/Class;"], 7, sidx["getDeclaredMethod"]),                      # 8
                (tidx["Ljava/lang/StringBuilder;"], 0, sidx["<init>"]),                         # 9
                (tidx["Ljava/lang/StringBuilder;"], 3, sidx["append"]),                         # 10
                (tidx["Ljava/lang/StringBuilder;"], 4, sidx["toString"]),                       # 11
                (tidx["Ljava/lang/System;"], 1, sidx["loadLibrary"]),                           # 12
            ]
            # Build runChecks() bytecode:
            # v0..v4 scratch
            insns = []
            # A) static final String + StringBuilder -> waitForDeclaredService("android.hardware.power.IPower/default")
            insns += [0x0022, tidx["Ljava/lang/StringBuilder;"]]               # new-instance v0, StringBuilder
            insns += [0x1070, 9, 0x0000]                                       # invoke-direct {v0}, StringBuilder.<init>()V
            insns += [0x0162, 0]                                               # sget-object v1, Target.DESCRIPTOR
            insns += [0x206E, 10, 0x0010]                                      # invoke-virtual {v0, v1}, StringBuilder.append
            insns += [0x000C]                                                  # move-result-object v0
            insns += [0x011A, sidx["/default"]]                                # const-string v1, "/default"
            insns += [0x206E, 10, 0x0010]                                      # invoke-virtual {v0, v1}, StringBuilder.append
            insns += [0x000C]                                                  # move-result-object v0
            insns += [0x106E, 11, 0x0000]                                      # invoke-virtual {v0}, StringBuilder.toString
            insns += [0x000C]                                                  # move-result-object v0
            insns += [0x1071, 1, 0x0000]                                       # invoke-static {v0}, ServiceManager.waitForDeclaredService
            # B) Unguarded JNI loadLibrary("unguarded_missing") + loadLibrary("static_bridge")
            insns += [0x001A, sidx["unguarded_missing"]]
            insns += [0x1071, 12, 0x0000]
            insns += [0x001A, sidx["static_bridge"]]
            insns += [0x1071, 12, 0x0000]
            # C) Guarded try { loadLibrary("guarded_missing"); } catch (UnsatisfiedLinkError) {}
            try_start = len(insns)
            insns += [0x001A, sidx["guarded_missing"]]
            insns += [0x1071, 12, 0x0000]
            try_end = len(insns)
            # D) Feature-gated dead branch: if (pm.hasSystemFeature("android.hardware.type.automotive")) startService("com.android.car.CarService")
            insns += [0x011A, sidx["android.hardware.type.automotive"]]
            insns += [0x206E, 0, 0x0012]                                       # invoke-virtual {v2, v1}, pm.hasSystemFeature
            insns += [0x000A]                                                  # move-result v0
            branch_pc = len(insns)
            insns += [0x0038, 7]                                               # if-eqz v0, +7 (skips 2+3 = 5 words -> lands at +7)
            insns += [0x011A, sidx["com.android.car.CarService"]]              # 2 words
            insns += [0x206E, 5, 0x0013]                                       # 3 words: ssm.startService(v3, v1)
            # E) Reflection: Class.forName("com.android.server.Target") -> getDeclaredMethod("existingMethod") & getDeclaredMethod("missingMethod")
            insns += [0x001A, sidx["com.android.server.Target"]]
            insns += [0x1071, 7, 0x0000]                                       # Class.forName
            insns += [0x000C]                                                  # move-result-object v0
            insns += [0x011A, sidx["existingMethod"]]
            insns += [0x206E, 8, 0x0010]                                       # v0.getDeclaredMethod("existingMethod")
            insns += [0x011A, sidx["missingMethod"]]
            insns += [0x206E, 8, 0x0010]                                       # v0.getDeclaredMethod("missingMethod")
            catch_pc = len(insns)
            insns += [0x000E]                                                  # return-void

            # Assemble DEX binary
            data = bytearray(0x70)
            str_ids_off = len(data)
            data.extend(b"\x00" * (4 * len(strings)))
            type_ids_off = len(data)
            for t in types:
                data.extend(struct.pack("<I", sidx[t]))
            proto_ids_off = len(data)
            data.extend(b"\x00" * (12 * len(protos)))
            field_ids_off = len(data)
            for ci, ti, ni in fields:
                data.extend(struct.pack("<HHI", ci, ti, ni))
            method_ids_off = len(data)
            for ci, pi, ni in methods:
                data.extend(struct.pack("<HHI", ci, pi, ni))
            class_defs_off = len(data)
            data.extend(b"\x00" * (32 * 3))
            data_off = len(data)

            for i, s in enumerate(strings):
                off = len(data)
                data.extend(_mutf8(s))
                struct.pack_into("<I", data, str_ids_off + i * 4, off)
            for i, (si, rti, params) in enumerate(protos):
                poff = 0
                if params:
                    while len(data) % 4:
                        data.append(0)
                    poff = len(data)
                    data.extend(struct.pack("<I", len(params)))
                    for p in params:
                        data.extend(struct.pack("<H", p))
                struct.pack_into("<III", data, proto_ids_off + i * 12, si, rti, poff)

            # code_item for runChecks (with 1 try_item catching UnsatisfiedLinkError)
            while len(data) % 4:
                data.append(0)
            code_off = len(data)
            data.extend(struct.pack("<HHHHII", 5, 0, 2, 1, 0, len(insns)))
            for w in insns:
                data.extend(struct.pack("<H", w))
            if len(insns) % 2 == 1:
                data.extend(b"\x00\x00")
            # try_item: start_addr, insn_count, handler_off=1 (byte offset past list size uleb=1)
            data.extend(struct.pack("<IHH", try_start, try_end - try_start, 1))
            # encoded_catch_handler_list: size=1, handler[0]: size=+1, type=UnsatisfiedLinkError, addr=catch_pc
            data.extend(_uleb(1))
            data.extend(_sleb(1))
            data.extend(_uleb(tidx["Ljava/lang/UnsatisfiedLinkError;"]))
            data.extend(_uleb(catch_pc))

            # static_values_off for Target: encoded_array [ VALUE_STRING(sidx["android.hardware.power.IPower"]) ]
            static_vals_off = len(data)
            sv = sidx["android.hardware.power.IPower"]
            sv_bytes = sv.to_bytes(max(1, (sv.bit_length() + 7) // 8), "little")
            data.extend(_uleb(1))
            data.append(((len(sv_bytes) - 1) << 5) | 0x17)
            data.extend(sv_bytes)

            # class_data for SystemServer: 0 sf, 0 if, 3 dm (nativeMissing=2, nativeOk=3, runChecks=4), 0 vm
            cd_ss = len(data)
            data.extend(_uleb(0) + _uleb(0) + _uleb(3) + _uleb(0))
            data.extend(_uleb(2) + _uleb(0x0109) + _uleb(0))         # ACC_PUBLIC|STATIC|NATIVE
            data.extend(_uleb(1) + _uleb(0x0109) + _uleb(0))         # diff=1 -> method 3
            data.extend(_uleb(1) + _uleb(0x0009) + _uleb(code_off))  # diff=1 -> method 4

            # class_data for Target: 1 sf (DESCRIPTOR=0), 0 if, 0 dm, 1 vm (existingMethod=6)
            cd_tgt = len(data)
            data.extend(_uleb(1) + _uleb(0) + _uleb(0) + _uleb(1))
            data.extend(_uleb(0) + _uleb(0x0019))                    # ACC_PUBLIC|STATIC|FINAL
            data.extend(_uleb(6) + _uleb(0x0001) + _uleb(0))         # existingMethod

            struct.pack_into(
                "<IIIIIIII", data, class_defs_off,
                tidx["Lcom/android/server/SystemServer;"], 0x0001, tidx["Ljava/lang/Object;"],
                0, 0xFFFFFFFF, 0, cd_ss, 0,
            )
            struct.pack_into(
                "<IIIIIIII", data, class_defs_off + 32,
                tidx["Lcom/android/server/Target;"], 0x0001, tidx["Ljava/lang/Object;"],
                0, 0xFFFFFFFF, 0, cd_tgt, static_vals_off,
            )
            struct.pack_into(
                "<IIIIIIII", data, class_defs_off + 64,
                tidx["Ljava/lang/Object;"], 0x0001, 0xFFFFFFFF,
                0, 0xFFFFFFFF, 0, 0, 0,
            )
            file_size = len(data)
            struct.pack_into(
                "<8sI20sIIIIIIIIIIIIIIIIIIII", data, 0,
                b"dex\n039\x00", 0, b"\x00" * 20, file_size, 0x70, 0x12345678,
                0, 0, 0, len(strings), str_ids_off, len(types), type_ids_off,
                len(protos), proto_ids_off, len(fields), field_ids_off,
                len(methods), method_ids_off, 3, class_defs_off,
                file_size - data_off, data_off,
            )
            return bytes(data)

        dex_bytes = _build_tier2_dex()
        v = native_backend.vdev_dex_verifier()
        self.assertGreater(v.add_target_bytes("services.jar", dex_bytes, True), 0)

        # Case 1: Phone ROM (automotive feature ABSENT)
        rep_phone = json.loads(v.verify_json([], ["android.hardware.wifi"], {}))
        lm = rep_phone["logic_map"]
        self.assertGreaterEqual(lm["static_strings_resolved"], 1)
        self.assertGreaterEqual(lm["dead_branches_pruned"], 1)

        # 1) Static final String + StringBuilder concat resolved AIDL HAL name
        hal_vals = [b["value"] for b in lm["binder_services"]]
        self.assertIn("android.hardware.power.IPower/default", hal_vals, lm)

        # 2) Guarded vs unguarded JNI calls cleanly separated
        jni_by_name = {j["value"]: j for j in lm["jni_libraries"]}
        self.assertFalse(jni_by_name["unguarded_missing"]["guarded"])
        self.assertTrue(jni_by_name["guarded_missing"]["guarded"])

        # 3) Automotive CarService pruned as dead branch on phone ROM
        car_sites = [s for s in lm["system_services"] if s["value"] == "com.android.car.CarService"]
        self.assertEqual(len(car_sites), 1)
        self.assertTrue(car_sites[0]["dead_branch"])

        # 4) Reflection member audit: existingMethod resolved=True, missingMethod resolved=False
        refl_m = {m["value"]: m for m in lm["reflection_members"]}
        self.assertTrue(refl_m["com.android.server.Target->existingMethod"]["resolved"])
        self.assertFalse(refl_m["com.android.server.Target->missingMethod"]["resolved"])

        # 5) JniBinding recorded native methods for SystemServer -> static_bridge
        bindings = [b for b in lm["jni_bindings"] if b["lib"] == "static_bridge"]
        self.assertTrue(bindings)
        self.assertIn("nativeOk", bindings[0]["methods"])
        self.assertIn("nativeMissing", bindings[0]["methods"])

        # Verify Python audit emits exact severities (unguarded WARN, guarded INFO, dead branch skipped)
        mem_s = MemoryTreeSource({}, label="mem_s")
        mem_v = MemoryTreeSource({
            "/etc/vintf/manifest.xml": b"<manifest><hal><name>android.hardware.audio</name></hal></manifest>",
        }, label="mem_v")
        vfs = VirtualFS()
        vfs.add_mount("/", mem_s, "system")
        vfs.add_mount("/vendor", mem_v, "vendor")
        lay = Layout(str(self.root))
        lay.partitions["vendor"] = str(self.root)
        ctx = Context(str(self.root), lay, vfs, None)
        ctx.profile = {"props": {}}
        findings = []
        s13_dex_bytecode._audit_logic_map(ctx, lm, [], findings)
        by_code = {f.code: f for f in findings}
        self.assertIn("dex-missing-jni-lib", by_code)
        self.assertEqual(by_code["dex-missing-jni-lib"].severity, "warn")
        self.assertIn("dex-missing-jni-guarded", by_code)
        self.assertEqual(by_code["dex-missing-jni-guarded"].severity, "info")
        self.assertNotIn("dex-unresolved-boot-service", by_code)  # CarService was pruned!
        self.assertIn("dex-missing-reflection-member", by_code)
        self.assertEqual(by_code["dex-missing-reflection-member"].severity, "warn")
        self.assertIn("dex-blocking-hal-undeclared", by_code)

        # Case 2: Automotive ROM (automotive feature PRESENT) -> CarService is LIVE and flagged!
        rep_auto = json.loads(v.verify_json([], ["android.hardware.type.automotive"], {}))
        car_auto = [s for s in rep_auto["logic_map"]["system_services"] if s["value"] == "com.android.car.CarService"]
        self.assertFalse(car_auto[0]["dead_branch"])
        findings_auto = []
        s13_dex_bytecode._audit_logic_map(ctx, rep_auto["logic_map"], [], findings_auto)
        self.assertTrue(any(f.code == "dex-unresolved-boot-service" for f in findings_auto))

    def test_dex_jni_symbol_binding_audit(self):
        from core.vdev.stages import s13_dex_bytecode
        from core.vdev.context import Context
        from core.vdev.mounts import Layout
        from core.vdev.vfs import VirtualFS
        from core.vdev.source import MemoryTreeSource

        # Verify JNI mangling rules (_ -> _1, $ -> _00024, / -> _)
        self.assertEqual(
            s13_dex_bytecode._jni_short_symbol("Lcom/oem/my_pkg/Bridge$Inner;", "call_native"),
            "Java_com_oem_my_1pkg_Bridge_00024Inner_call_1native",
        )

        mem = MemoryTreeSource({
            "/system/lib64/libstatic_jni.so": b"\x7fELF",
            "/system/lib64/libdyn_jni.so": b"\x7fELF",
        }, label="mem")
        vfs = VirtualFS()
        vfs.add_mount("/", mem, "system")
        lay = Layout(str(self.root))
        lay.partitions["vendor"] = str(self.root)
        ctx = Context(str(self.root), lay, vfs, None)
        _, so_vpaths = s13_dex_bytecode._collect_available_so_map(ctx)

        orig_scan = s13_dex_bytecode._scan_so_defined_syms
        try:
            s13_dex_bytecode._scan_so_defined_syms = lambda _ctx, _vps: {
                "/system/lib64/libstatic_jni.so": {
                    "Java_com_vendor_Bridge_nativeOk",
                    "Java_com_vendor_Bridge_nativeOverload__I",
                },
                "/system/lib64/libdyn_jni.so": {
                    "JNI_OnLoad",
                },
            }
            bindings = [
                {
                    "lib": "static_jni",
                    "class_name": "Lcom/vendor/Bridge;",
                    "methods": ["nativeOk", "nativeOverload", "nativeMissing"],
                    "source": "/system/framework/oem.jar",
                    "guarded": False,
                    "boot_critical": False,
                },
                {
                    "lib": "dyn_jni",
                    "class_name": "Lcom/vendor/DynBridge;",
                    "methods": ["registeredAtRuntime"],
                    "source": "/system/framework/oem.jar",
                    "guarded": False,
                    "boot_critical": True,
                },
            ]
            findings = []
            missing, checked = s13_dex_bytecode._audit_jni_symbols(ctx, bindings, so_vpaths, findings)
            self.assertEqual(checked, 1)  # dyn_jni skipped due to JNI_OnLoad
            self.assertEqual(len(missing), 1)
            self.assertEqual(missing[0]["methods"], ["nativeMissing"])
            self.assertTrue(any(f.code == "dex-missing-jni-symbol" and f.severity == "warn" for f in findings))
        finally:
            s13_dex_bytecode._scan_so_defined_syms = orig_scan

    def test_dex_finally_rethrow_is_not_guarded(self):
        """A Dalvik catch_all handler ending in throw (0x27) is a compiler-generated
        `finally` or `synchronized` block and must NOT suppress fatal JNI/reflection errors."""
        import json
        import struct
        from core import native_backend
        from core.vdev.stages import s13_dex_bytecode
        from core.vdev.context import Context
        from core.vdev.mounts import Layout
        from core.vdev.vfs import VirtualFS
        from core.vdev.source import MemoryTreeSource

        def _uleb(v: int) -> bytes:
            out = bytearray()
            while True:
                b = v & 0x7F
                v >>= 7
                out.append(b | 0x80 if v else b)
                if not v:
                    return bytes(out)

        def _mutf8(s: str) -> bytes:
            return _uleb(len(s)) + s.encode("utf-8") + b"\x00"

        strings = sorted(set([
            "Lcom/android/server/SystemServer;", "Ljava/lang/Object;",
            "Ljava/lang/String;", "Ljava/lang/System;", "V", "VL",
            "android_servers", "loadLibrary", "run",
        ]))
        sidx = {s: i for i, s in enumerate(strings)}
        types = sorted(set([
            "Lcom/android/server/SystemServer;", "Ljava/lang/Object;",
            "Ljava/lang/String;", "Ljava/lang/System;", "V",
        ]))
        tidx = {t: i for i, t in enumerate(types)}
        protos = [
            (sidx["V"], tidx["V"], []),
            (sidx["VL"], tidx["V"], [tidx["Ljava/lang/String;"]]),
        ]
        methods = [
            (tidx["Lcom/android/server/SystemServer;"], 0, sidx["run"]),
            (tidx["Ljava/lang/System;"], 1, sidx["loadLibrary"]),
        ]
        # try { System.loadLibrary("android_servers"); } finally { move-exception v0; throw v0; }
        insns = [
            0x001A, sidx["android_servers"],  # pc=0..2
            0x1071, 1, 0x0000,                # pc=2..5
            0x000E,                           # pc=5: return-void
            0x000D,                           # pc=6: move-exception v0
            0x0027,                           # pc=7: throw v0 (re-throw!)
        ]
        data = bytearray(0x70)
        str_ids_off = len(data)
        data.extend(b"\x00" * (4 * len(strings)))
        type_ids_off = len(data)
        for t in types:
            data.extend(struct.pack("<I", sidx[t]))
        proto_ids_off = len(data)
        data.extend(b"\x00" * (12 * len(protos)))
        method_ids_off = len(data)
        for ci, pi, ni in methods:
            data.extend(struct.pack("<HHI", ci, pi, ni))
        class_defs_off = len(data)
        data.extend(b"\x00" * 32)
        data_off = len(data)
        for i, s in enumerate(strings):
            off = len(data)
            data.extend(_mutf8(s))
            struct.pack_into("<I", data, str_ids_off + i * 4, off)
        for i, (si, rti, params) in enumerate(protos):
            poff = 0
            if params:
                while len(data) % 4:
                    data.append(0)
                poff = len(data)
                data.extend(struct.pack("<I", len(params)))
                for p in params:
                    data.extend(struct.pack("<H", p))
            struct.pack_into("<III", data, proto_ids_off + i * 12, si, rti, poff)
        while len(data) % 4:
            data.append(0)
        code_off = len(data)
        data.extend(struct.pack("<HHHHII", 2, 0, 1, 1, 0, len(insns)))
        for w in insns:
            data.extend(struct.pack("<H", w))
        # try_item: [0..5), handler_off=1 -> catch_all (size=0) @ pc=6
        data.extend(struct.pack("<IHH", 0, 5, 1))
        data.extend(_uleb(1))
        data.append(0x00)  # sleb128(0) -> catch_all only
        data.extend(_uleb(6))  # catch_all_addr = 6 (move-exception + throw)
        cd_off = len(data)
        data.extend(_uleb(0) + _uleb(0) + _uleb(1) + _uleb(0))
        data.extend(_uleb(0) + _uleb(0x0009) + _uleb(code_off))
        struct.pack_into(
            "<IIIIIIII", data, class_defs_off,
            tidx["Lcom/android/server/SystemServer;"], 0x0001, tidx["Ljava/lang/Object;"],
            0, 0xFFFFFFFF, 0, cd_off, 0,
        )
        file_size = len(data)
        struct.pack_into(
            "<8sI20sIIIIIIIIIIIIIIIIIIII", data, 0,
            b"dex\n039\x00", 0, b"\x00" * 20, file_size, 0x70, 0x12345678,
            0, 0, 0, len(strings), str_ids_off, len(types), type_ids_off,
            len(protos), proto_ids_off, 0, 0, len(methods), method_ids_off,
            1, class_defs_off, file_size - data_off, data_off,
        )
        v = native_backend.vdev_dex_verifier()
        v.add_target_bytes("services.jar", bytes(data), True)
        rep = json.loads(v.verify_json([]))
        jnis = rep["logic_map"]["jni_libraries"]
        self.assertEqual(len(jnis), 1)
        self.assertEqual(jnis[0]["value"], "android_servers")
        self.assertFalse(jnis[0]["guarded"], "finally re-throw handler must not be treated as catch guard")

        vfs = VirtualFS()
        vfs.add_mount("/", MemoryTreeSource({}, label="empty"), "system")
        ctx = Context(str(self.root), Layout(str(self.root)), vfs, None)
        ctx.profile = {"props": {}}
        findings = []
        s13_dex_bytecode._audit_logic_map(ctx, rep["logic_map"], [], findings)
        self.assertTrue(any(f.code == "dex-missing-jni-critical" and f.severity == "blocker" for f in findings), findings)

    def test_native_daemon_cpp_symbol_audit(self):
        from core.vdev.stages import s5_linker
        from core.vdev.context import Context
        from core.vdev.mounts import Layout
        from core.vdev.vfs import VirtualFS
        from core.vdev.source import MemoryTreeSource

        mem = MemoryTreeSource({
            "/system/bin/surfaceflinger": b"\x7fELF",
            "/system/bin/audioserver": b"\x7fELF",
            "/system/lib64/libgui.so": b"\x7fELF",
            "/system/lib64/libc.so": b"\x7fELF",
        }, label="mem")
        vfs = VirtualFS()
        vfs.add_mount("/", mem, "system")
        ctx = Context(str(self.root), Layout(str(self.root)), vfs, None)
        lib_index = {"libgui.so": ["/system/lib64/libgui.so"], "libc.so": ["/system/lib64/libc.so"]}
        lib_path_set = {"/system/lib64/libgui.so", "/system/lib64/libc.so"}
        dir_cache = {("system", True): ["/system/lib64"], ("system", None): ["/system/lib64"]}
        cross_by_abi = {True: ["/system/lib64"]}

        class _DummyApex:
            available = True
            files = {}

        orig_batch = s5_linker._batch_scan_symbols
        try:
            # Case 1: surfaceflinger missing C++ ABI symbol from libgui.so -> BLOCKER
            s5_linker._batch_scan_symbols = lambda _vfs, vps, _nm: {
                vp: {
                    "/system/bin/surfaceflinger": {
                        "is_64": True, "needed": ["libgui.so", "libc.so"],
                        "defined": set(), "undef": ["_ZN7android14SurfaceControl12newAbiMethodEv", "malloc"],
                    },
                    "/system/bin/audioserver": {
                        "is_64": True, "needed": ["libc.so"],
                        "defined": set(), "undef": ["malloc"],
                    },
                    "/system/lib64/libgui.so": {
                        "is_64": True, "needed": ["libc.so"],
                        "defined": {"_ZN7android14SurfaceControl9oldMethodEv"}, "undef": [],
                    },
                    "/system/lib64/libc.so": {
                        "is_64": True, "needed": [],
                        "defined": {"malloc"}, "undef": [],
                    },
                }[vp]
                for vp in vps if vp in ("/system/bin/surfaceflinger", "/system/bin/audioserver", "/system/lib64/libgui.so", "/system/lib64/libc.so")
            }
            findings = []
            res = s5_linker._audit_critical_daemon_symbols(
                ctx, ["/system/bin/surfaceflinger", "/system/bin/audioserver"],
                lib_index, lib_path_set, set(), _DummyApex(),
                {}, {}, dir_cache, cross_by_abi, object(), findings,
            )
            self.assertTrue(any(f.code == "elf-symbol-missing-critical" and f.severity == "blocker" for f in findings), findings)
            sf = [r for r in res if r["binary"].endswith("surfaceflinger")][0]
            self.assertIn("_ZN7android14SurfaceControl12newAbiMethodEv", sf["missing_symbols"])
        finally:
            s5_linker._batch_scan_symbols = orig_batch

    def test_shared_uid_all_groups_signature_conflict(self):
        from core.vdev.stages import s7_framework
        from core.vdev.apkinfo import ApkInfo
        from core.vdev.context import Context
        from core.vdev.mounts import Layout
        from core.vdev.vfs import VirtualFS
        from core.vdev.source import MemoryTreeSource

        vfs = VirtualFS()
        vfs.add_mount("/", MemoryTreeSource({}, label="m"), "system")
        ctx = Context(str(self.root), Layout(str(self.root)), vfs, None)
        fw = ApkInfo("/system/framework/framework-res.apk", "")
        fw.package = "android"
        fw.cert_sha256 = "aaaa" * 16
        ctx.shared["framework_res_info"] = fw

        a1 = ApkInfo("/system_ext/priv-app/SystemUI/SystemUI.apk", "")
        a1.package = "com.android.systemui"
        a1.shared_user_id = "android.uid.systemui"
        a1.cert_sha256 = "1111" * 16

        a2 = ApkInfo("/product/priv-app/ForeignPlugin/ForeignPlugin.apk", "")
        a2.package = "com.oem.foreign.ui"
        a2.shared_user_id = "android.uid.systemui"
        a2.cert_sha256 = "2222" * 16

        a3 = ApkInfo("/system/priv-app/Settings/Settings.apk", "")
        a3.package = "com.android.settings"
        a3.shared_user_id = "android.uid.system"
        a3.cert_sha256 = "aaaa" * 16

        findings = []
        s7_framework._check_shared_uid(ctx, [a1, a2, a3], findings)
        codes = {(f.code, f.severity) for f in findings}
        self.assertIn(("shared-uid-ok", "pass"), codes)
        self.assertIn(("shared-uid-group-mismatch", "blocker"), codes)

    def test_fbe_keymint_gatekeeper_coherence(self):
        from core.vdev.stages import s1_mount
        from core.vdev.mounts import FstabEntry, Layout
        from core.vdev.context import Context
        from core.vdev.vfs import VirtualFS
        from core.vdev.source import MemoryTreeSource

        def _run_fbe(entries, vendor_files, sdk=35):
            vfs = VirtualFS()
            vfs.add_mount("/", MemoryTreeSource({}, label="sys"), "system")
            vfs.add_mount("/vendor", MemoryTreeSource(vendor_files, label="vnd"), "vendor")
            lay = Layout(str(self.root))
            lay.partitions["vendor"] = str(self.root)
            ctx = Context(str(self.root), lay, vfs, None, fstab=entries, fstab_origin="fstab.test")
            ctx.profile = {"props": {"ro.build.version.sdk": str(sdk)}, "sdk": sdk}
            f = []
            s1_mount._check_fbe_coherence(ctx, entries, f)
            return {x.code: x.severity for x in f}

        # 1) Missing /metadata + missing KeyMint/Keymaster -> both BLOCKERs
        e_bad = [FstabEntry("/dev/block/userdata", "/data", "f2fs", [], ["fileencryption=aes-256-xts:aes-256-cts:v2", "metadata_encryption=aes-256-xts"])]
        c1 = _run_fbe(e_bad, {"/etc/vintf/manifest.xml": b"<manifest></manifest>"})
        self.assertEqual(c1.get("fbe-metadata-mount-missing"), "blocker")
        self.assertEqual(c1.get("fbe-keystore-hal-missing"), "blocker")

        # 2) Legacy FDE on Android 15 -> BLOCKER
        e_fde = [FstabEntry("/dev/block/userdata", "/data", "ext4", [], ["forceencrypt=footer"])]
        c2 = _run_fbe(e_fde, {}, sdk=35)
        self.assertEqual(c2.get("fde-unsupported-sdk"), "blocker")

        # 3) Coherent FBE + /metadata + KeyMint + Gatekeeper -> PASS
        e_ok = [
            FstabEntry("/dev/block/metadata", "/metadata", "ext4", [], ["first_stage_mount"]),
            FstabEntry("/dev/block/userdata", "/data", "f2fs", [], ["fileencryption=aes-256-xts:aes-256-cts:v2+inlinecrypt_optimized", "metadata_encryption=aes-256-xts"]),
        ]
        manifest_ok = (
            b"<manifest>"
            b"<hal><name>android.hardware.security.keymint</name></hal>"
            b"<hal><name>android.hardware.gatekeeper</name></hal>"
            b"</manifest>"
        )
        c3 = _run_fbe(e_ok, {"/etc/vintf/manifest.xml": manifest_ok})
        self.assertEqual(c3.get("fbe-coherent"), "pass")

    def test_real_logcat_correlator_and_cil_generation(self):
        from core.vdev.logcat_correlator import correlate_log
        from core.vdev.report import Finding, Report, StageResult
        from core.vdev.vfs import VirtualFS
        from core.vdev.source import MemoryTreeSource

        sample_log = "\n".join([
            '01-01 00:00:05.123   412   412 F linker  : CANNOT LINK EXECUTABLE "/system/bin/surfaceflinger": cannot locate symbol "_ZN7android14SurfaceControl3fooEv" referenced by "/system/lib64/libsurfaceflinger.so"...',
            '01-01 00:00:06.100   450   450 E linker  : CANNOT LINK EXECUTABLE "/vendor/bin/hw/audio": library "libaudio_missing.so" not found',
            '01-01 00:00:07.200   175   175 W auditd  : type=1400 audit(0.0:12): avc: denied { read open } for comm="surfaceflinger" scontext=u:r:surfaceflinger:s0 tcontext=u:object_r:vendor_file:s0 tclass=file permissive=0',
            '01-01 00:00:08.300   724   724 E AndroidRuntime: java.lang.UnsatisfiedLinkError: dlopen failed: library "libandroid_servers.so" not found',
            '01-01 00:00:09.000   724   724 E PackageManager: Signature mismatch for shared user SharedUserSetting{1234 android.uid.systemui/1000}',
        ])
        vfs = VirtualFS()
        vfs.add_mount("/", MemoryTreeSource({
            "/system/bin/surfaceflinger": b"\x7fELF",
            "/system/lib64/libsurfaceflinger.so": b"\x7fELF",
            "/system/etc/selinux/plat_sepolicy.cil": b"; cil",
        }, label="mem"), "system")
        rep = Report("test")
        rep.stages.append(StageResult("linker", "Linker", [
            Finding("linker", "elf-symbol-missing-critical", "blocker", "missing sym"),
            Finding("linker", "lib-missing", "blocker", "missing lib"),
        ], ran=True))
        rep.stages.append(StageResult("dex_bytecode", "DEX", [
            Finding("dex_bytecode", "dex-missing-jni-critical", "blocker", "missing jni"),
        ], ran=True))
        res = correlate_log(sample_log, vfs=vfs, report=rep)
        self.assertTrue(res["ok"])
        self.assertEqual(res["totalEvents"], 5)
        self.assertGreaterEqual(res["matchedStatic"], 3)
        self.assertEqual(res["avcCount"], 1)
        self.assertIn("(allow surfaceflinger vendor_file (file", res["generatedCil"])

    def test_autofix_preview_apply_and_undo_roundtrip(self):
        from core.vdev.autofix import preview_autofix, apply_autofix, undo_autofix

        ws = self.root / "unpacked_ws"
        (ws / "system" / "system" / "etc" / "permissions").mkdir(parents=True)
        (ws / "system" / "system" / "etc" / "selinux").mkdir(parents=True)
        (ws / "product" / "overlay").mkdir(parents=True)
        bp = ws / "system" / "system" / "build.prop"
        bp.write_text("ro.zygote=zygote64_32\nro.product.name=test\n", encoding="utf-8")
        cil = ws / "system" / "system" / "etc" / "selinux" / "plat_sepolicy.cil"
        cil.write_text("(allow init system_file (file (read)))\n", encoding="utf-8")
        ov = ws / "product" / "overlay" / "BrokenForeignOverlay.apk"
        ov.write_bytes(b"PK\x03\x04broken")

        # 1) Preview + Apply + Undo privapp XML creation
        r_xml = {
            "id": "privapp-allowlist-xml", "kind": "xml_snippet", "title": "Add privapp XML",
            "target_path": "/system/etc/permissions/privapp-permissions-port-autofix.xml",
            "content": "<permissions/>\n",
        }
        prev = preview_autofix(r_xml, str(ws))
        self.assertTrue(prev["ok"] and prev["canApply"])
        app = apply_autofix(r_xml, str(ws))
        self.assertTrue(app["ok"])
        target_xml = ws / "system" / "system" / "etc" / "permissions" / "privapp-permissions-port-autofix.xml"
        self.assertTrue(target_xml.is_file())
        und = undo_autofix(app["backupManifest"])
        self.assertTrue(und["ok"])
        self.assertFalse(target_xml.is_file())

        # 2) Preview + Apply + Undo build.prop zygote fix
        r_prop = {
            "id": "zygote-prop-fix", "kind": "prop_patch", "title": "Fix zygote",
            "target_path": "/system/build.prop", "updates": [("ro.zygote", "zygote64")],
        }
        app2 = apply_autofix(r_prop, str(ws))
        self.assertTrue(app2["ok"])
        self.assertIn("ro.zygote=zygote64", bp.read_text(encoding="utf-8"))
        undo_autofix(app2["backupManifest"])
        self.assertIn("ro.zygote=zygote64_32", bp.read_text(encoding="utf-8"))

        # 3) Preview + Apply + Undo overlay quarantine
        r_ov = {
            "id": "overlay-prune-mismatch", "kind": "overlay_cleanup", "title": "Quarantine overlay",
            "overlays": [{"apk_path": "/product/overlay/BrokenForeignOverlay.apk"}],
        }
        app3 = apply_autofix(r_ov, str(ws))
        self.assertTrue(app3["ok"])
        self.assertFalse(ov.exists())
        undo_autofix(app3["backupManifest"])
        self.assertTrue(ov.exists())

        # 4) Read-only non-existent workspace gracefully refuses
        prev_ro = preview_autofix(r_xml, str(self.root / "nonexistent_readonly.zip"))
        self.assertFalse(prev_ro["ok"])

    def test_kernel_ikcfg_config_validation(self):
        from core.vdev.stages import s10_kernel
        from core.vdev.context import Context
        from core.vdev.mounts import Layout
        from core.vdev.vfs import VirtualFS
        from core.vdev.source import MemoryTreeSource

        vfs = VirtualFS()
        vfs.add_mount("/vendor", MemoryTreeSource({
            "/etc/fstab.test": (
                b"system /system erofs ro wait,avb\n"
                b"/dev/block/by-name/userdata /data f2fs rw,fileencryption=aes-256-xts:aes-256-cts:v2+inlinecrypt_optimized,inlinecrypt wait\n"
            ),
        }, label="vendor"), "vendor")
        lay = Layout(str(self.root))
        lay.partitions["vendor"] = str(self.root)
        ctx = Context(str(self.root), lay, vfs, None)
        ctx.profile = {"sdk": 35, "props": {"ro.build.version.sdk": "35"}}

        # Missing EROFS, BPF, USERFAULTFD, FBE
        bad_kinfo = {
            "has_config": True,
            "kernel_config_count": 3200,
            "kernel_config": {
                "CONFIG_EROFS_FS": "n",
                "CONFIG_F2FS_FS": "y",
                "CONFIG_EXT4_FS": "y",
                "CONFIG_SECURITY_SELINUX": "y",
                "CONFIG_ANDROID_BINDER_IPC": "y",
                "CONFIG_BPF_SYSCALL": "n",
                "CONFIG_CGROUP_BPF": "n",
                "CONFIG_USERFAULTFD": "n",
                "CONFIG_PSI": "y",
                "CONFIG_FS_ENCRYPTION": "n",
                "CONFIG_FS_ENCRYPTION_INLINE_CRYPT": "n",
                "CONFIG_BLK_INLINE_ENCRYPTION": "n",
                "CONFIG_DM_VERITY": "y",
            },
        }
        f_bad = []
        s10_kernel._check_kernel_config(ctx, bad_kinfo, {}, f_bad)
        bad_codes = {f.code: f.severity for f in f_bad}
        self.assertEqual(bad_codes.get("kernel-config-erofs-missing"), "blocker")
        self.assertEqual(bad_codes.get("kernel-config-bpf-missing"), "blocker")
        self.assertEqual(bad_codes.get("kernel-config-fbe-missing"), "blocker")
        self.assertEqual(bad_codes.get("kernel-config-userfaultfd-missing"), "warn")
        self.assertEqual(bad_codes.get("kernel-config-inlinecrypt-missing"), "warn")

        # Coherent Android 15 kernel .config passes cleanly
        good_kinfo = {
            "has_config": True,
            "kernel_config_count": 3200,
            "kernel_config": {
                "CONFIG_EROFS_FS": "y",
                "CONFIG_F2FS_FS": "y",
                "CONFIG_EXT4_FS": "y",
                "CONFIG_SECURITY_SELINUX": "y",
                "CONFIG_ANDROID_BINDER_IPC": "y",
                "CONFIG_BPF_SYSCALL": "y",
                "CONFIG_CGROUP_BPF": "y",
                "CONFIG_USERFAULTFD": "y",
                "CONFIG_PSI": "y",
                "CONFIG_FS_ENCRYPTION": "y",
                "CONFIG_FS_ENCRYPTION_INLINE_CRYPT": "y",
                "CONFIG_DM_VERITY": "y",
            },
        }
        f_good = []
        s10_kernel._check_kernel_config(ctx, good_kinfo, {}, f_good)
        self.assertEqual({f.code: f.severity for f in f_good}, {"kernel-config-ok": "pass"})

    def test_hardware_bringup_auditor(self):
        from core.vdev.stages import s14_hardware
        from core.vdev.context import Context
        from core.vdev.mounts import Layout
        from core.vdev.vfs import VirtualFS
        from core.vdev.source import MemoryTreeSource

        # Broken hardware: missing audio effect .so, missing media_codecs Include, missing rild.libpath
        vfs_bad = VirtualFS()
        vfs_bad.add_mount("/vendor", MemoryTreeSource({
            "/bin/hw/android.hardware.graphics.composer3-service": b"\x7fELF",
            "/bin/hw/android.hardware.audio.service": b"\x7fELF",
            "/lib64/egl/libEGL_mali.so": b"\x7fELF",
            "/etc/audio_policy_configuration.xml": b"<audioPolicyConfiguration version='1.0'/>",
            "/etc/audio_effects.xml": (
                b"<audio_effects_conf version='2.0'><libraries>"
                b"<library name='bundle' path='libbundlewrapper.so'/>"
                b"<library name='ghost' path='libmissing_dsp.so'/>"
                b"</libraries></audio_effects_conf>"
            ),
            "/lib64/soundfx/libbundlewrapper.so": b"\x7fELF",
            "/etc/media_codecs.xml": b"<MediaCodecs><Include href='media_codecs_missing.xml'/></MediaCodecs>",
            "/etc/permissions/handheld_core_hardware.xml": (
                b"<permissions>"
                b"<feature name='android.hardware.camera'/>"
                b"<feature name='android.hardware.telephony'/>"
                b"</permissions>"
            ),
        }, label="vendor"), "vendor")
        lay = Layout(str(self.root))
        lay.partitions["vendor"] = str(self.root)
        ctx = Context(str(self.root), lay, vfs_bad, None)
        ctx.profile = {"props": {"ro.hardware.egl": "mali", "vendor.rild.libpath": "/vendor/lib64/libghost_ril.so"}}
        res = s14_hardware.run(ctx)
        codes = {f.code for f in res}
        self.assertIn("hw-display-allocator-missing", codes)
        self.assertIn("hw-display-mapper-missing", codes)
        self.assertIn("hw-audio-effect-missing", codes)
        self.assertIn("hw-media-codecs-include-missing", codes)
        self.assertIn("hw-ril-lib-missing", codes)
        self.assertIn("hw-camera-hal-missing", codes)

        # Healthy hardware passes cleanly (full 5-layer graphics stack + audio/camera/RIL)
        vfs_ok = VirtualFS()
        vfs_ok.add_mount("/vendor", MemoryTreeSource({
            "/bin/hw/android.hardware.graphics.composer3-service": b"\x7fELF",
            "/bin/hw/android.hardware.graphics.allocator-service": b"\x7fELF",
            "/bin/hw/android.hardware.audio.service": b"\x7fELF",
            "/bin/hw/android.hardware.camera.provider-service": b"\x7fELF",
            "/bin/hw/rild": b"\x7fELF",
            "/lib64/hw/mapper.mali.so": b"\x7fELF",
            "/lib64/egl/libEGL_mali.so": b"\x7fELF",
            "/lib64/libmtk_ril.so": b"\x7fELF",
            "/lib64/soundfx/libbundlewrapper.so": b"\x7fELF",
            "/etc/audio_policy_configuration.xml": b"<audioPolicyConfiguration version='1.0'/>",
            "/etc/audio_effects.xml": (
                b"<audio_effects_conf version='2.0'><libraries>"
                b"<library name='bundle' path='libbundlewrapper.so'/>"
                b"</libraries></audio_effects_conf>"
            ),
            "/etc/media_codecs.xml": b"<MediaCodecs><Include href='media_codecs_vendor.xml'/></MediaCodecs>",
            "/etc/media_codecs_vendor.xml": b"<Included/>",
            "/etc/permissions/handheld_core_hardware.xml": (
                b"<permissions><feature name='android.hardware.camera'/></permissions>"
            ),
        }, label="vendor"), "vendor")
        ctx_ok = Context(str(self.root), lay, vfs_ok, None)
        ctx_ok.profile = {"props": {"ro.hardware.egl": "mali", "vendor.rild.libpath": "/vendor/lib64/libmtk_ril.so"}}
        res_ok = s14_hardware.run(ctx_ok)
        self.assertTrue(any(f.code == "hw-bringup-ok" and f.severity == "pass" for f in res_ok), res_ok)

    def test_config_syntax_and_integrity_validator(self):
        from core.vdev.stages import s15_configs
        from core.vdev.context import Context
        from core.vdev.mounts import Layout
        from core.vdev.vfs import VirtualFS
        from core.vdev.source import MemoryTreeSource

        vfs_bad = VirtualFS()
        vfs_bad.add_mount("/system", MemoryTreeSource({
            "/etc/permissions/broken.xml": b"<permissions><privapp-permissions package='x'></permissions>",
            "/etc/sysconfig/conflict.xml": b"<<<<<<< HEAD\n<config/>\n=======\n<config/>\n>>>>>>> port\n",
            "/etc/selinux/plat_sepolicy.cil": b"(allow init system_file (file (read))\n",
            "/etc/init/a.rc": b"service mydaemon /system/bin/foo\n    class main\n",
            "/etc/init/b.rc": b"service mydaemon /system/bin/bar\n    class main\n",
        }, label="system"), "system")
        lay = Layout(str(self.root))
        lay.partitions["system"] = str(self.root)
        ctx = Context(str(self.root), lay, vfs_bad, None)
        res = s15_configs.run(ctx)
        by_code = {f.code: f.severity for f in res}
        self.assertEqual(by_code.get("config-merge-conflict"), "blocker")
        self.assertEqual(by_code.get("config-syntax-corrupt"), "blocker")
        self.assertEqual(by_code.get("config-cil-syntax-corrupt"), "blocker")
        self.assertEqual(by_code.get("config-duplicate-service"), "warn")

        # Clean configs + valid 'override' service pass cleanly
        vfs_ok = VirtualFS()
        vfs_ok.add_mount("/system", MemoryTreeSource({
            "/etc/permissions/good.xml": b"<permissions><feature name='android.hardware.touchscreen'/></permissions>",
            "/etc/selinux/plat_sepolicy.cil": b"; comment with ) paren\n(allow init system_file (file (read)))\n",
            "/etc/init/a.rc": b"service mydaemon /system/bin/foo\n    class main\n",
            "/etc/init/b.rc": b"service mydaemon /system/bin/bar\n    override\n    class main\n",
        }, label="system"), "system")
        ctx_ok = Context(str(self.root), lay, vfs_ok, None)
        res_ok = s15_configs.run(ctx_ok)
        self.assertEqual({f.code: f.severity for f in res_ok}, {"configs-ok": "pass"})

    def test_linker_namespace_config_and_rpath_expansion(self):
        from core.vdev.stages import s5_linker
        from core.vdev.vfs import VirtualFS
        from core.vdev.source import MemoryTreeSource

        vfs = VirtualFS()
        vfs.add_mount("/", MemoryTreeSource({
            "/system/etc/linker.config.json": b'{"provideLibs": ["libcustom_llndk.so"]}',
            "/vendor/etc/linker.config.json": b'{"provideLibs": ["libvendor_pub.so"]}',
        }, label="mem"), "system")
        cfg = s5_linker._load_linker_configs(vfs)
        self.assertEqual(cfg["configs_loaded"], 2)
        self.assertIn("libcustom_llndk.so", cfg["system_provides"])
        self.assertIn("libvendor_pub.so", cfg["vendor_provides"])

        expanded = s5_linker._expand_rpath_dirs("/vendor/bin/hw/demo", ["$ORIGIN/../lib64:${ORIGIN}/priv"])
        self.assertEqual(expanded, ["/vendor/bin/lib64", "/vendor/bin/hw/priv"])

    def test_cli_vdev_correlate_and_report_text_builder(self):
        from core.rak_cli import execute_command
        from core.vdev.report import Finding, Report, StageResult
        from ui.boot_analyzer import BootAnalyzerController

        logf = self.root / "sample_crash.log"
        logf.write_text(
            '01-01 00:00:05.123 412 412 F linker : CANNOT LINK EXECUTABLE "/system/bin/surfaceflinger": library "libghost.so" not found\n',
            encoding="utf-8",
        )
        ok, out = execute_command(f'vdev correlate "{logf}" --json', cwd=str(self.root))
        self.assertTrue(ok, out)
        self.assertIn('"totalEvents": 1', out)

        # Verify built-in editor plain-text diagnostic report (no HTML/Markdown tags)
        opened = []

        class _FakeWorkspace:
            def openAs(self, path, kind):
                opened.append((path, kind))

        ctrl = BootAnalyzerController(_FakeWorkspace())
        rep = Report("demo")
        rep.stages.append(StageResult("configs", "Config syntax", [
            Finding("configs", "configs-ok", "pass", "All configs clean"),
        ], ran=True))
        ctrl._last_report = rep
        ctrl._hardware_raw = {"subsystems": {"display": {"status": "ok", "detail": "HWC OK"}}}
        ctrl._kernel_config_raw = {"total": 2500, "flags": {"CONFIG_EROFS_FS": "y"}}
        out_path = ctrl.openReportInEditor()
        self.assertTrue(out_path.endswith(".log"), out_path)
        self.assertEqual(len(opened), 1)
        self.assertEqual(opened[0][1], "editor")
        content = Path(out_path).read_text(encoding="utf-8")
        self.assertIn("BOOT ANALYZER DIAGNOSTIC REPORT", content)
        self.assertIn("CONFIG_EROFS_FS=y", content)
        self.assertNotIn("<html", content.lower())

    def test_manifest_component_to_dex_verifier(self):
        import struct
        from core import native_backend
        from core.vdev.apkinfo import ApkInfo
        from core.vdev.stages import s13_dex_bytecode
        from core.vdev.context import Context
        from core.vdev.mounts import Layout
        from core.vdev.vfs import VirtualFS

        def _uleb(val: int) -> bytes:
            out = bytearray()
            while True:
                b = val & 0x7F
                val >>= 7
                if val:
                    out.append(b | 0x80)
                else:
                    out.append(b)
                    return bytes(out)

        def _mutf8(s: str) -> bytes:
            b = s.encode("utf-8")
            return _uleb(len(s)) + b + b"\x00"

        strings = sorted([
            "Lcom/android/systemui/SystemUIApplication;",
            "Ljava/lang/Object;",
        ])
        sidx = {s: i for i, s in enumerate(strings)}
        types = list(strings)
        tidx = {t: i for i, t in enumerate(types)}
        data = bytearray(0x70)
        str_ids_off = len(data)
        data.extend(b"\x00" * (4 * len(strings)))
        type_ids_off = len(data)
        for t in types:
            data.extend(struct.pack("<I", sidx[t]))
        class_defs_off = len(data)
        data.extend(b"\x00" * 32)
        data_off = len(data)
        for i, s in enumerate(strings):
            off = len(data)
            data.extend(_mutf8(s))
            struct.pack_into("<I", data, str_ids_off + i * 4, off)
        struct.pack_into(
            "<IIIIIIII", data, class_defs_off,
            tidx["Lcom/android/systemui/SystemUIApplication;"], 0x0001, tidx["Ljava/lang/Object;"],
            0, 0xFFFFFFFF, 0, 0, 0,
        )
        file_size = len(data)
        struct.pack_into(
            "<8sI20sIIIIIIIIIIIIIIIIIIII", data, 0,
            b"dex\n039\x00", 0, b"\x00" * 20, file_size, 0x70, 0x12345678,
            0, 0, 0, len(strings), str_ids_off, len(types), type_ids_off,
            0, 0, 0, 0, 0, 0,
            1, class_defs_off, file_size - data_off, data_off,
        )

        v = native_backend.vdev_dex_verifier()
        v.add_target_bytes("/system_ext/priv-app/SystemUI/SystemUI.apk", bytes(data), True)
        res_has = v.has_classes(
            "/system_ext/priv-app/SystemUI/SystemUI.apk",
            ["com.android.systemui.SystemUIApplication", "com.android.systemui.GhostMissingService"],
        )
        self.assertEqual(res_has, [True, False])

        apk = ApkInfo("/system_ext/priv-app/SystemUI/SystemUI.apk", None)
        apk.package = "com.android.systemui"
        apk.components = [
            ("application", "com.android.systemui.SystemUIApplication"),
            ("service", "com.android.systemui.SystemUIService"),
            ("service", "com.oem.systemui.OptionalPluginService"),
        ]
        ctx = Context(str(self.root), Layout(str(self.root)), VirtualFS(), None)
        ctx.shared["apks"] = [apk]
        findings = []
        summary = s13_dex_bytecode._audit_manifest_components(
            ctx, v, ["/system_ext/priv-app/SystemUI/SystemUI.apk"], findings,
        )
        self.assertTrue(summary["has_blocker"])
        self.assertEqual(summary["checked"], 3)
        self.assertTrue(any(f.code == "dex-manifest-component-missing" and f.severity == "blocker" for f in findings))

        # Optional OEM secondary service alone emits WARN, not BLOCKER
        apk2 = ApkInfo("/system_ext/priv-app/SystemUI/SystemUI.apk", None)
        apk2.package = "com.android.systemui"
        apk2.components = [
            ("application", "com.android.systemui.SystemUIApplication"),
            ("service", "com.oem.systemui.OptionalPluginService"),
        ]
        ctx.shared["apks"] = [apk2]
        findings2 = []
        summary2 = s13_dex_bytecode._audit_manifest_components(
            ctx, v, ["/system_ext/priv-app/SystemUI/SystemUI.apk"], findings2,
        )
        self.assertFalse(summary2["has_blocker"])
        self.assertTrue(any(f.code == "dex-manifest-component-missing" and f.severity == "warn" for f in findings2))

    def test_kernel_ko_kmi_crc_modules_load_and_firmware_audit(self):
        from core.vdev.stages import s10_kernel
        from core.vdev.context import Context
        from core.vdev.mounts import Layout
        from core.vdev.vfs import VirtualFS
        from core.vdev.source import MemoryTreeSource

        # 1) Ramdisk modules.load missing .ko -> BLOCKER
        f_load = []
        s10_kernel._audit_ramdisk_modules_load({
            "vendor_boot": {
                "ramdisks": [{
                    "name": "",
                    "files": ["/lib/modules/ufs_qcom.ko"],
                    "modules_load": {"/lib/modules/modules.load": ["ufs_qcom.ko", "missing_boot_drv.ko"]},
                    "modules_dep": {},
                }],
            },
        }, f_load)
        self.assertTrue(any(f.code == "kernel-modules-load-missing" and f.severity == "blocker" for f in f_load), f_load)

        # 2) KMI module_layout CRC conflict & inter-module symbol CRC mismatch -> BLOCKER
        f_kmi_bad = []
        s10_kernel._audit_ko_kmi_and_symbols([
            {
                "path": "/vendor_boot/a.ko", "name": "a", "module_layout_crc": 0x11223344,
                "exported_crcs": [("foo_export", 0xAAAA0001)], "imported_crcs": [("module_layout", 0x11223344)],
            },
            {
                "path": "/vendor_dlkm/b.ko", "name": "b", "module_layout_crc": 0x99887766,
                "exported_crcs": [], "imported_crcs": [("module_layout", 0x99887766), ("foo_export", 0xBBBB0002)],
            },
        ], f_kmi_bad)
        bad_codes = {f.code: f.severity for f in f_kmi_bad}
        self.assertEqual(bad_codes.get("kernel-kmi-crc-mismatch"), "blocker")
        self.assertEqual(bad_codes.get("kernel-symbol-crc-mismatch"), "blocker")

        # Coherent KMI CRCs -> PASS
        f_kmi_ok = []
        s10_kernel._audit_ko_kmi_and_symbols([
            {
                "path": "/vendor_boot/a.ko", "name": "a", "module_layout_crc": 0x11223344,
                "exported_crcs": [("foo_export", 0xAAAA0001)], "imported_crcs": [("module_layout", 0x11223344)],
            },
            {
                "path": "/vendor_dlkm/b.ko", "name": "b", "module_layout_crc": 0x11223344,
                "exported_crcs": [], "imported_crcs": [("module_layout", 0x11223344), ("foo_export", 0xAAAA0001)],
            },
        ], f_kmi_ok)
        self.assertEqual({f.code: f.severity for f in f_kmi_ok}, {"kernel-kmi-ok": "pass"})

        # 3) Firmware blob audit honoring ueventd.rc firmware_directories
        vfs = VirtualFS()
        vfs.add_mount("/vendor", MemoryTreeSource({
            "/etc/ueventd.rc": b"firmware_directories /vendor/firmware/custom/\n",
            "/firmware/custom/wifi_ok.bin": b"\x00\x01\x02",
        }, label="vendor"), "vendor")
        lay = Layout(str(self.root))
        lay.partitions["vendor"] = str(self.root)
        ctx = Context(str(self.root), lay, vfs, None)
        f_fw_ok = []
        s10_kernel._audit_ko_firmware(ctx, [{"name": "wlan", "firmware": ["wifi_ok.bin"]}], {}, f_fw_ok)
        self.assertTrue(any(f.code == "kernel-firmware-ok" and f.severity == "pass" for f in f_fw_ok), f_fw_ok)

        f_fw_miss = []
        s10_kernel._audit_ko_firmware(ctx, [{"name": "wlan", "firmware": ["wifi_missing.bin"]}], {}, f_fw_miss)
        self.assertTrue(any(f.code == "kernel-firmware-missing" and f.severity == "warn" for f in f_fw_miss), f_fw_miss)

    def test_kernel_hardware_crosscheck_and_ueventd(self):
        from core.vdev.stages import s14_hardware
        from core.vdev.context import Context
        from core.vdev.mounts import Layout
        from core.vdev.vfs import VirtualFS
        from core.vdev.source import MemoryTreeSource

        vfs = VirtualFS()
        vfs.add_mount("/vendor", MemoryTreeSource({
            "/lib64/egl/libEGL_adreno.so": b"\x7fELF",
            "/etc/ueventd.rc": b"/dev/kgsl-3d0 0999 root root\n/dev/binder 0666 root root\n",
        }, label="vendor"), "vendor")
        lay = Layout(str(self.root))
        lay.partitions["vendor"] = str(self.root)
        ctx = Context(str(self.root), lay, vfs, None)
        ctx.profile = {"props": {"ro.hardware.egl": "adreno"}}
        ctx.shared["kernel_config"] = {
            "total": 3500,
            "flags": {"CONFIG_CFG80211": "n", "CONFIG_BT": "n", "CONFIG_SND": "n", "CONFIG_SOUND": "n"},
        }
        ctx.shared["kernel_hardware"] = {
            "ko_total": 10,
            "ko_names": ["mali_kbase"],
            "dtb_compatibles": ["arm,mali-valhall"],
            "of_alias_count": 5,
            "matched_of_compatibles": [],
        }
        findings = []
        res_khw = s14_hardware._audit_kernel_hw_coherence(
            ctx, vfs,
            {"android.hardware.wifi", "android.hardware.bluetooth"},
            {"android.hardware.audio"},
            findings,
        )
        res_ue = s14_hardware._audit_ueventd(ctx, vfs, findings)
        codes = {f.code: f.severity for f in findings}
        self.assertEqual(res_khw["status"], "fail")
        self.assertEqual(codes.get("hw-gpu-kernel-mismatch"), "blocker")
        self.assertEqual(codes.get("hw-wifi-kernel-missing"), "warn")
        self.assertEqual(codes.get("hw-bt-kernel-missing"), "warn")
        self.assertEqual(codes.get("hw-audio-kernel-missing"), "warn")
        self.assertEqual(res_ue["status"], "warn")
        self.assertEqual(codes.get("hw-ueventd-syntax"), "warn")

    def test_qemu_runner_abi_overlay_plan_and_cli(self):
        import gzip
        from core.vdev.qemu_runner import (
            detect_qemu_environment,
            resolve_qemu_abi,
            build_qemu_overlay_cpio_gz,
            prepare_qemu_plan,
            parse_qemu_output_line,
        )
        from core.rak_cli import execute_command

        # 1) ABI resolution for auto, arm64, arm64_32 (64_32 / 63_32), arm32
        self.assertEqual(resolve_qemu_abi({"props": {"ro.zygote": "zygote64_32"}}, "auto"), "arm64_32")
        self.assertEqual(resolve_qemu_abi({"props": {"ro.zygote": "zygote64"}}, "auto"), "arm64")
        self.assertEqual(resolve_qemu_abi({"props": {"ro.zygote": "zygote32"}}, "auto"), "arm32")
        self.assertEqual(resolve_qemu_abi({}, "64_32"), "arm64_32")
        self.assertEqual(resolve_qemu_abi({}, "arm32"), "arm32")

        # 2) Pure-Python CPIO gzip overlay shim contains fstab.qemu, first-stage dirs & init.qemu.rc (no fake vendor HAL override!)
        blob = build_qemu_overlay_cpio_gz([("system", "erofs"), ("vendor", "ext4"), ("userdata", "ext4")], abi_mode="arm64_32")
        raw_cpio = gzip.decompress(blob)
        self.assertIn(b"070701", raw_cpio)
        self.assertIn(b"debug_ramdisk", raw_cpio)
        self.assertIn(b"second_stage_resources", raw_cpio)
        self.assertIn(b"first_stage_ramdisk/fstab.qemu", raw_cpio)
        self.assertIn(b"fstab.qemu", raw_cpio)
        self.assertIn(b"init.qemu.rc", raw_cpio)
        self.assertNotIn(b"ro.hardware.hwcomposer ranchu", raw_cpio)
        self.assertNotIn(b"ro.hardware.gralloc ranchu", raw_cpio)
        self.assertIn(b"ro.zygote zygote64_32", raw_cpio)
        self.assertIn(b"/dev/block/vda  /system  erofs", raw_cpio)
        self.assertIn(b"TRAILER!!!", raw_cpio)

        # 3) Plan builder with synthetic images & kernel builds valid command without modifying source images
        sim_dir = self.root / "qemu_rom"
        sim_dir.mkdir()
        (sim_dir / "system.img").write_bytes(b"\x00" * 1024 + b"\xe2\xe1\xf5\xe0" + b"\x00" * 1024)
        (sim_dir / "vendor.img").write_bytes(b"\x00" * 1080 + b"\x53\xef" + b"\x00" * 1024)
        fake_kern = self.root / "Image-virtio-arm64"
        fake_kern.write_bytes(b"ARM\x64fakekernel")
        env = detect_qemu_environment()
        if env["available"]:
            plan = prepare_qemu_plan(str(sim_dir), abi="arm64_32", kernel_path=str(fake_kern))
            self.assertTrue(plan.ok, plan.error)
            self.assertEqual(plan.abi_mode, "arm64_32")
            self.assertTrue(Path(plan.ramdisk_path).is_file())
            self.assertIn("androidboot.zygote=zygote64_32", plan.cmdline)
            self.assertNotIn("androidboot.slot_suffix=", plan.cmdline)
            self.assertIn("enforcing=0", plan.cmdline)
            self.assertIn("androidboot.fstab_suffix=qemu", plan.cmdline)
            expected_disks = 6 if plan.virtio_gfx_bridge else 5
            self.assertEqual(len(plan.disks), expected_disks)  # system, misc, vendor, ephemeral metadata, userdata (+ oem VirtIO gfx bridge)
            if plan.backend == "upstream":
                self.assertIn("max", plan.command)

        # 4) Live output parser handles dmesg + threadtime logcat + kernel panic
        p_dmesg = parse_qemu_output_line("[    1.234567] Kernel panic - not syncing: Attempted to kill init!")
        self.assertEqual(p_dmesg["tag"], "qemu-kernel")
        self.assertEqual(p_dmesg["level"], "F")
        p_lc = parse_qemu_output_line("01-01 00:00:05.123   724   724 I SystemServer: Entered the Android system server!")
        self.assertEqual(p_lc["tag"], "SystemServer")
        self.assertEqual(p_lc["level"], "I")

        # 5) CLI `rak vdev qemu status --json`
        ok, out = execute_command("vdev qemu status --json", cwd=str(self.root))
        self.assertTrue(ok, out)
        self.assertIn('"GPL-2.0-only (external subprocess)"', out)

    def test_qemu_embedded_display_hub_and_rfb_loopback(self):
        import socket
        import struct
        import threading
        import time
        from PySide6.QtCore import QSize
        from core.vdev.qemu_display import QemuDisplayHub, QemuScreenProvider, RfbClient

        hub = QemuDisplayHub()
        provider = QemuScreenProvider(hub)
        hub.render_boot_console(
            phase="Zygote started",
            abi="arm64_32",
            backend="Upstream QEMU",
            elapsed_s=4.2,
            milestones=["kernel", "init_first", "zygote"],
            tail_lines=["[    0.000] Linux version 6.6.0", "Zygote64 starting"],
        )
        sz = QSize()
        img0 = provider.requestImage("frame?1", sz, QSize())
        self.assertEqual((img0.width(), img0.height()), (360, 720))
        self.assertFalse(hub.has_live_gpu)

        # Spin up a tiny mock RFB 3.8 server on localhost to verify live VNC frame decoding & touch/key packets
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.bind(("127.0.0.1", 0))
        port = srv.getsockname()[1]
        srv.listen(1)
        got_frame = threading.Event()
        rx_events = []

        def _rfb_server():
            try:
                conn, _ = srv.accept()
                conn.settimeout(3.0)
                conn.sendall(b"RFB 003.008\n")
                conn.recv(12)               # client version
                conn.sendall(b"\x01\x01")   # 1 security type: None (1)
                conn.recv(1)                # client chooses 1
                conn.sendall(struct.pack("!I", 0))  # security OK
                conn.recv(1)                # ClientInit
                # ServerInit: 4x4 desktop, 16-byte pixel format, name_len=0
                conn.sendall(struct.pack("!HH16sI", 4, 4, b"\x00" * 16, 0))
                # Read SetPixelFormat (20B) + SetEncodings (16B) + FramebufferUpdateRequest (10B)
                buf = b""
                while len(buf) < 46:
                    c = conn.recv(64)
                    if not c:
                        break
                    buf += c
                # Send 4x4 Raw BGRA bright-green frame (B=0, G=255, R=0, A=255)
                pixels = b"\x00\xff\x00\xff" * 16
                upd = struct.pack("!BBH", 0, 0, 1) + struct.pack("!HHHHi", 0, 0, 4, 4, 0) + pixels
                conn.sendall(upd)
                # Receive pointer/key events if sent
                try:
                    rx_events.append(conn.recv(64))
                except OSError:
                    pass
                conn.close()
            finally:
                srv.close()

        t = threading.Thread(target=_rfb_server, daemon=True)
        t.start()
        rfb = RfbClient("127.0.0.1", port, hub, on_frame=got_frame.set)
        hub.attach_rfb(rfb)
        rfb.start()
        self.assertTrue(got_frame.wait(timeout=4.0), "RFB client did not receive live frame")
        self.assertTrue(hub.has_live_gpu)
        hub.send_touch(0.5, 0.5, True)
        hub.send_android_key("home")
        time.sleep(0.05)
        rfb.stop()
        t.join(timeout=2.0)

        img1 = provider.requestImage("frame?2", QSize(), QSize())
        self.assertEqual((img1.width(), img1.height()), (4, 4))
        self.assertEqual(img1.pixelColor(2, 2).green(), 255)

    def test_kernel_contract_hardware_gate_pass_and_sabotage(self):
        import gzip
        from types import SimpleNamespace
        from PySide6.QtGui import QColor, QImage
        from core.vdev.qemu_display import QemuDisplayHub
        from core.vdev.qemu_runner import (
            build_hardware_gate,
            cleanup_vdev_ephemeral_caches,
            detect_qemu_environment,
            prepare_qemu_plan,
        )

        # 1) Blank black VNC scanout must NOT hide the live phone boot HUD
        hub = QemuDisplayHub()
        blank = QImage(640, 480, QImage.Format.Format_ARGB32)
        blank.fill(QColor("#000000"))
        hub.set_live_gpu_image(blank)
        self.assertFalse(hub.has_live_gpu)

        # 2) Passing kernel+vendor contract enables all generic VirtIO peripherals
        sim_dir = self.root / "hwgate_rom"
        sim_dir.mkdir()
        (sim_dir / "system.img").write_bytes(b"\x00" * 1024 + b"\xe2\xe1\xf5\xe0" + b"\x00" * 1024)
        (sim_dir / "vendor.img").write_bytes(b"\x00" * 1080 + b"\x53\xef" + b"\x00" * 1024)
        fake_kern = self.root / "Image-hwgate"
        fake_kern.write_bytes(b"ARM\x64fakekernel")

        clean_kcfg = {"flags": {"CONFIG_EROFS_FS": "y", "CONFIG_EXT4_FS": "y"}}
        clean_hw = {"subsystems": {"gpu": {"status": "ok", "detail": "Mali ok"}, "audio": {"status": "ok", "detail": "ASoC ok"}}}
        clean_rep = SimpleNamespace(stages=[], profile={})
        gate_ok = build_hardware_gate(clean_rep, disks=[{"fsType": "erofs", "readonly": True}], kernel_config=clean_kcfg, hardware_audit=clean_hw)
        self.assertEqual(gate_ok["faultCount"], 0)
        self.assertTrue(gate_ok["byKey"]["display"]["enabled"])
        self.assertTrue(gate_ok["byKey"]["touch"]["enabled"])
        self.assertTrue(gate_ok["byKey"]["audio"]["enabled"])
        self.assertEqual(gate_ok["byKey"]["filesystem"]["status"], "pass")

        env = detect_qemu_environment()
        if env["available"] and env["backend"] == "upstream":
            plan_ok = prepare_qemu_plan(
                str(sim_dir),
                abi="arm64",
                kernel_path=str(fake_kern),
                report=clean_rep,
                kernel_config=clean_kcfg,
                hardware_audit=clean_hw,
            )
            self.assertTrue(plan_ok.ok, plan_ok.error)
            self.assertTrue(any(str(t).startswith(("virtio-gpu-pci", "virtio-gpu-gl-pci")) for t in plan_ok.command))
            self.assertIn("virtio-tablet-pci", plan_ok.command)
            self.assertIn("virtio-sound-pci,audiodev=rak_snd0", plan_ok.command)
            self.assertGreater(plan_ok.vnc_port, 0)
            cpio_ok = gzip.decompress(Path(plan_ok.ramdisk_path).read_bytes())
            self.assertIn(b"setprop ro.rak.hwgate.display pass", cpio_ok)
            if plan_ok.virtio_gfx_bridge:
                self.assertIn(b"overlay  /vendor  overlay  ro,lowerdir=/oem:/vendor  first_stage_mount", cpio_ok)

            # 3) Sabotaged /vendor MUST NOT turn off kernel hardware (QEMU obeys Kernel+DTBO, NOT /vendor!),
            #    MUST disable the Generic VirtIO Graphics Bridge, and MUST fail the boot verdict accurately!
            bad_finding_disp = SimpleNamespace(severity="blocker", code="hw-gpu-broken", title="Vendor hwcomposer missing", detail="", path="/vendor/lib64/hw/hwcomposer.mtk.so")
            bad_finding_tc = SimpleNamespace(severity="blocker", code="ko-missing-fw", title="Touchscreen goodix firmware missing", detail="", path="/vendor/firmware/goodix_ts.bin")
            bad_finding_aud = SimpleNamespace(severity="blocker", code="audio-policy-invalid", title="audio_policy_configuration.xml broken", detail="", path="/vendor/etc/audio_policy_configuration.xml")
            bad_rep = SimpleNamespace(
                stages=[
                    SimpleNamespace(key="hardware", findings=[bad_finding_disp, bad_finding_aud]),
                    SimpleNamespace(key="kernel", findings=[bad_finding_tc]),
                ],
                profile={},
            )
            bad_kcfg = {"flags": {"CONFIG_EROFS_FS": "n", "CONFIG_EXT4_FS": "y"}}
            plan_bad = prepare_qemu_plan(
                str(sim_dir),
                abi="arm64",
                kernel_path=str(fake_kern),
                report=bad_rep,
                kernel_config=bad_kcfg,
            )
            self.assertTrue(plan_bad.ok, plan_bad.error)
            # Hardware stays active per Kernel+DTBO (never obeys broken /vendor!)
            self.assertTrue(any(str(t).startswith(("virtio-gpu-pci", "virtio-gpu-gl-pci")) for t in plan_bad.command))
            self.assertIn("virtio-tablet-pci", plan_bad.command)
            self.assertIn("virtio-sound-pci,audiodev=rak_snd0", plan_bad.command)
            self.assertGreater(plan_bad.vnc_port, 0)
            # Vendor faults & Kernel FS mismatch MUST block VirtIO Graphics Bridge & fail boot!
            self.assertFalse(plan_bad.virtio_gfx_bridge)
            self.assertTrue(plan_bad.hardware_gate["bootBlockedByVendor"])
            self.assertGreaterEqual(len(plan_bad.hardware_gate["vendorFaults"]), 3)
            self.assertEqual(plan_bad.hardware_gate["byKey"]["vendor"]["status"], "fault")
            self.assertEqual(plan_bad.hardware_gate["byKey"]["filesystem"]["status"], "fault")
            self.assertIn("androidboot.rak_fs_fault=1", plan_bad.cmdline)
            cpio_bad = gzip.decompress(Path(plan_bad.ramdisk_path).read_bytes())
            self.assertIn(b"/dev/block/vda  /system  erofs_unsupported_by_kernel", cpio_bad)
            self.assertIn(b"setprop ro.rak.vendor_fault 1", cpio_bad)
            self.assertNotIn(b"overlay  /vendor  overlay", cpio_bad)
            self.assertNotIn(b"ro.hardware.hwcomposer ranchu", cpio_bad)

            # 4) Verify _PANIC_RE catches real init/property_contexts bootloops without false-positiving on TEE keystore2
            from core.vdev.qemu_runner import _PANIC_RE, _BOOT_MILESTONES
            self.assertIsNotNone(_PANIC_RE.search("[    T1] init: InitFatalReboot: signal 6"))
            self.assertIsNotNone(_PANIC_RE.search("[    T1] reboot: Restarting system with command 'bootloader'"))
            self.assertIsNotNone(_PANIC_RE.search("Unable to serialize property contexts"))
            self.assertIsNone(_PANIC_RE.search("F/libc (221): Fatal signal 6 (SIGABRT) in tid 221 (binder:221_2)"))
            ms_keys = {k for k, _, _ in _BOOT_MILESTONES}
            self.assertIn("surfaceflinger", ms_keys)
            self.assertIn("bootanim", ms_keys)
            # 5) Passing a real Layout object (where boot_images is list[tuple[str, str]]) must never crash
            from core.vdev.mounts import Layout
            lay = Layout(str(sim_dir))
            lay.fs_images = {
                "system": str(sim_dir / "system.img"),
                "vendor": str(sim_dir / "vendor.img"),
            }
            lay.boot_images = [("VNDRBOOT", str(sim_dir / "missing_vendor_boot.img")), ("BOOT", str(fake_kern))]
            plan_lay = prepare_qemu_plan(
                lay,
                abi="arm64",
                kernel_path=str(fake_kern),
                report=clean_rep,
                kernel_config=clean_kcfg,
                hardware_audit=clean_hw,
            )
            self.assertTrue(plan_lay.ok, plan_lay.error)
            cleanup_vdev_ephemeral_caches(include_extracted=False, async_run=False)

    def test_qemu_android16_virgl_selinux_and_display_rules(self):
        import gzip
        from types import SimpleNamespace
        from PySide6.QtGui import QColor, QImage
        from core.vdev.qemu_display import QemuDisplayHub
        from core.vdev.qemu_runner import (
            build_qemu_overlay_cpio_gz,
            cleanup_vdev_ephemeral_caches,
            detect_qemu_environment,
            detect_rom_mapper_suffix,
            prepare_qemu_plan,
        )

        # 1) _is_non_blank_gpu_frame: ignore 640x480 QEMU banner (~0.9% lit), accept small 540x960 boot logo (~0.4% lit)
        banner_640 = QImage(640, 480, QImage.Format.Format_ARGB32)
        banner_640.fill(QColor("#000000"))
        for x in range(180, 460):
            for y in range(235, 245):
                banner_640.setPixelColor(x, y, QColor("#CCCCCC"))
        self.assertFalse(QemuDisplayHub._is_non_blank_gpu_frame(banner_640))

        logo_540 = QImage(540, 960, QImage.Format.Format_ARGB32)
        logo_540.fill(QColor("#000000"))
        for x in range(220, 320):
            for y in range(440, 500):
                logo_540.setPixelColor(x, y, QColor("#FFFFFF"))
        self.assertTrue(QemuDisplayHub._is_non_blank_gpu_frame(logo_540))

        # 2) Ephemeral first_stage_init overlay prefix rename ONLY when virtio_gfx_overlay=True and vendor not blocked
        fake_init = b"\x7fELF" + b"\x00" * 32 + b"overlay-\x00" + b"padding"
        cpio_on = gzip.decompress(build_qemu_overlay_cpio_gz(
            [("system", "erofs"), ("vendor", "erofs"), ("oem", "ext4")],
            first_stage_init_bytes=fake_init,
            virtio_gfx_overlay=True,
        ))
        self.assertIn(b"rak_gfx-\x00", cpio_on)
        self.assertNotIn(b"overlay-\x00", cpio_on)

        cpio_off = gzip.decompress(build_qemu_overlay_cpio_gz(
            [("system", "erofs"), ("vendor", "erofs")],
            first_stage_init_bytes=fake_init,
            virtio_gfx_overlay=False,
        ))
        self.assertIn(b"overlay-\x00", cpio_off)
        self.assertNotIn(b"rak_gfx-\x00", cpio_off)

        # 3) detect_rom_mapper_suffix gracefully returns "" on missing path
        self.assertEqual(detect_rom_mapper_suffix(str(self.root / "nonexistent_vendor.img")), "")

        # 4) Verify num-queues=1 on every virtio-blk-pci, enforcing=0 even under Enforcing, and no slot_suffix=_a
        env = detect_qemu_environment()
        if env["available"] and env["backend"] == "upstream":
            sim_dir = self.root / "a16_rules_rom"
            sim_dir.mkdir()
            (sim_dir / "system.img").write_bytes(b"\x00" * 1024 + b"\xe2\xe1\xf5\xe0" + b"\x00" * 1024)
            (sim_dir / "vendor.img").write_bytes(b"\x00" * 1080 + b"\x53\xef" + b"\x00" * 1024)
            fake_kern = self.root / "Image-a16"
            fake_kern.write_bytes(b"ARM\x64fakekernel")
            plan = prepare_qemu_plan(
                str(sim_dir),
                abi="arm64",
                permissive=False,
                kernel_path=str(fake_kern),
                report=SimpleNamespace(stages=[], profile={}),
                kernel_config={"flags": {"CONFIG_EROFS_FS": "y", "CONFIG_EXT4_FS": "y"}},
            )
            self.assertTrue(plan.ok, plan.error)
            self.assertIn("enforcing=0", plan.cmdline)
            self.assertIn("androidboot.selinux=enforcing", plan.cmdline)
            self.assertNotIn("androidboot.slot_suffix=", plan.cmdline)
            blk_devs = [t for t in plan.command if str(t).startswith("virtio-blk-pci,")]
            self.assertGreaterEqual(len(blk_devs), 4)
            for bdev in blk_devs:
                self.assertIn("num-queues=1", bdev)
            cleanup_vdev_ephemeral_caches(include_extracted=False, async_run=False)

    def test_qemu_interactive_timeout_and_partition_filter(self):
        from core.vdev.qemu_runner import (
            QemuBootPlan,
            QemuSession,
            _BOOT_MILESTONES,
            _is_mountable_partition_name,
        )

        # Interactive Play passes timeout_sec=0 -> unlimited (no forced 180s kill)
        plan = QemuBootPlan(ok=True)
        self.assertEqual(QemuSession(plan, timeout_sec=0).timeout_sec, 0)
        self.assertEqual(QemuSession(plan, timeout_sec=None).timeout_sec, 0)
        self.assertEqual(QemuSession(plan, timeout_sec=180).timeout_sec, 180)
        self.assertEqual(QemuSession(plan, timeout_sec=3).timeout_sec, 10)

        # Genuine OEM partitions accepted; user backup/repack copies rejected
        for ok_part in ("system", "vendor", "tr_product", "tr_region", "tr_misc", "tr_overlayfs", "tr_manifest"):
            self.assertTrue(_is_mountable_partition_name(ok_part), ok_part)
        for bad_part in ("tr_manifest_new", "tr_product_new_format", "tr_region_new", "product_new(baru)", "tr_region_bak"):
            self.assertFalse(_is_mountable_partition_name(bad_part), bad_part)

        # kmsg milestones detect SurfaceFlinger & SystemServer bring-up
        ms_map = {k: rx for k, rx, _lbl in _BOOT_MILESTONES}
        self.assertIsNotNone(ms_map["surfaceflinger"].search("init: starting service 'surfaceflinger'..."))
        self.assertIsNotNone(ms_map["system_server"].search("servicemanager: Caller(pid=1039,uid=1000,sid=u:r:system_server:s0) Found IPower"))

    def test_wave1_vintf_v9_kmi_vndk_props_init(self):
        from core.vdev.kernel import extract_kmi_release, kmi_matches_kernel
        from core.vdev.stages import s0_device, s3_vintf, s4_init, s8_apex
        from core.vdev.context import Context
        from core.vdev.mounts import Layout
        from core.vdev.vfs import VirtualFS
        from core.vdev.source import MemoryTreeSource

        # 1) Kernel GKI KMI extraction & matching
        self.assertEqual(extract_kmi_release("6.12.38-android16-5-g88d25a9-ab1234"), ("6.12", "6.12-android16-5"))
        ok_m, _ = kmi_matches_kernel(
            "6.12.38-android16-5-g11111111",
            "arm64",
            "6.12.38-android16-5-g99999999 SMP preempt mod_unload modversions aarch64",
            has_crcs=True,
        )
        self.assertTrue(ok_m)
        bad_m, reason = kmi_matches_kernel(
            "6.12.38-android16-5-g11111111",
            "arm64",
            "6.6.30-android15-8-g11111111 SMP preempt mod_unload modversions aarch64",
            has_crcs=True,
        )
        self.assertFalse(bad_m)
        self.assertIn("series", reason.lower())

        # 2) VINTF v9.0 catalog: zero false Automotive/TV warnings, reads APEX device manifests, catches missing core composer
        matrix_v9 = (
            b'<compatibility-matrix version="9.0" type="framework" level="202504">'
            b'<hal format="aidl"><name>android.hardware.automotive.vehicle</name><version>3</version></hal>'
            b'<hal format="aidl"><name>android.hardware.tv.cec</name><version>1</version></hal>'
            b'<hal format="aidl"><name>android.hardware.graphics.composer3</name><version>3</version></hal>'
            b'<hal format="aidl"><name>android.hardware.graphics.allocator</name><version>2</version></hal>'
            b'<hal format="native"><name>mapper</name><version>5.0</version></hal>'
            b'<hal format="aidl"><name>android.hardware.power</name><version>5</version></hal>'
            b'<hal format="aidl"><name>android.hardware.health</name><version>3</version></hal>'
            b'<hal format="aidl"><name>android.hardware.gatekeeper</name><version>1</version></hal>'
            b'<hal format="aidl"><name>android.hardware.security.keymint</name><version>3</version></hal>'
            b'</compatibility-matrix>'
        )
        vnd_manifest_ok = (
            b'<manifest version="9.0" type="device" target-level="202504">'
            b'<hal format="aidl"><name>android.hardware.graphics.allocator</name><version>2</version></hal>'
            b'<hal format="native"><name>mapper</name><version>5.0</version><fqname>@5.0/powervr</fqname></hal>'
            b'<hal format="aidl"><name>android.hardware.power</name><version>5</version></hal>'
            b'<hal format="aidl"><name>android.hardware.health</name><version>3</version></hal>'
            b'<hal format="aidl"><name>android.hardware.gatekeeper</name><version>1</version></hal>'
            b'<hal format="aidl"><name>android.hardware.security.keymint</name><version>3</version></hal>'
            b'</manifest>'
        )
        apex_composer = (
            b'<manifest version="1.0" type="device">'
            b'<hal format="aidl"><name>android.hardware.graphics.composer3</name><version>3</version></hal>'
            b'</manifest>'
        )
        vfs_v9 = VirtualFS()
        vfs_v9.add_mount("/", MemoryTreeSource({
            "/system/etc/vintf/compatibility_matrix.202504.xml": matrix_v9,
            "/apex/com.vendor.composer/etc/vintf/composer.xml": apex_composer,
        }, label="sys"), "system")
        vfs_v9.add_mount("/vendor", MemoryTreeSource({
            "/etc/vintf/manifest.xml": vnd_manifest_ok,
        }, label="vnd"), "vendor")
        lay_v9 = Layout(str(self.root))
        lay_v9.partitions["vendor"] = str(self.root)
        ctx_v9 = Context(str(self.root), lay_v9, vfs_v9, None)
        res_v9 = s3_vintf.run(ctx_v9)
        self.assertTrue(any(f.code == "hal-ok" and f.severity == "pass" for f in res_v9), res_v9)
        self.assertFalse(any(f.severity in ("warn", "blocker") for f in res_v9), res_v9)

        # Sabotage VINTF v9.0: remove composer3 APEX -> must report hal-missing for core boot HAL
        vfs_v9_bad = VirtualFS()
        vfs_v9_bad.add_mount("/", MemoryTreeSource({
            "/system/etc/vintf/compatibility_matrix.202504.xml": matrix_v9,
        }, label="sys"), "system")
        vfs_v9_bad.add_mount("/vendor", MemoryTreeSource({
            "/etc/vintf/manifest.xml": vnd_manifest_ok,
        }, label="vnd"), "vendor")
        ctx_v9_bad = Context(str(self.root), lay_v9, vfs_v9_bad, None)
        res_v9_bad = s3_vintf.run(ctx_v9_bad)
        self.assertTrue(any(f.code == "hal-missing" and "composer" in f.detail.lower() for f in res_v9_bad), res_v9_bad)

        # 3) Android 16 VNDK deprecation + s0_device intra-file build.prop override & cross-partition trace
        vfs_p = VirtualFS()
        vfs_p.add_mount("/", MemoryTreeSource({
            "/system/build.prop": (
                b"ro.build.version.sdk=36\nro.build.version.release=16\n"
                b"ro.product.device=note40\nro.shared.test=from_system\n"
            ),
        }, label="sys"), "system")
        vfs_p.add_mount("/vendor", MemoryTreeSource({
            "/build.prop": (
                b"ro.board.platform=common\nro.hardware.gralloc=common\n"
                b"ro.board.api_level=202504\nro.shared.test=from_vendor\n"
                b"ro.board.platform=mt6855\nro.hardware.gralloc=rogue\n"
            ),
        }, label="vnd"), "vendor")
        ctx_p = Context(str(self.root), lay_v9, vfs_p, None)
        f_dev = s0_device.run(ctx_p)
        self.assertEqual(ctx_p.prop("ro.board.platform"), "mt6855")
        self.assertEqual(ctx_p.prop("ro.hardware.gralloc"), "rogue")
        self.assertTrue(any(f.code == "prop-load" for f in f_dev))
        ptrace = ctx_p.profile.get("prop_trace") or {}
        self.assertTrue(any(c["key"] == "ro.shared.test" for c in ptrace.get("cross_conflicts") or ()))

        from types import SimpleNamespace
        ctx_p._apex = SimpleNamespace(
            apex_count=1,
            manifests={"/system/apex/com.android.vndk.v34.apex": {"name": "com.android.vndk.v34"}},
            libs={},
            public_libs=set(),
            failed=[],
            files={},
        )
        f_apex = s8_apex.run(ctx_p)
        self.assertTrue(any(f.code == "vndk-apex-deprecated" and f.severity == "pass" for f in f_apex), f_apex)

        # 4) s4_init: duplicate service (non-override WARN vs override OK), missing exec, unknown start, trigger_timeline
        vfs_init = VirtualFS()
        vfs_init.add_mount("/", MemoryTreeSource({
            "/system/bin/goodbin": b"\x7fELF",
            "/system/etc/init/hw/init.rc": (
                b"on early-init\n"
                b"    exec /system/bin/missing_exec_helper\n"
                b"    start ghost_service_xyz\n"
                b"    class_start core\n"
                b"service dup_svc /system/bin/goodbin\n    class core\n"
                b"service ovr_svc /system/bin/goodbin\n    class core\n"
            ),
            "/system/etc/init/extra.rc": (
                b"service dup_svc /system/bin/goodbin\n    class core\n"
                b"service ovr_svc /system/bin/goodbin\n    override\n    class core\n"
            ),
        }, label="sys"), "system")
        ctx_i = Context(str(self.root), Layout(str(self.root)), vfs_init, None)
        f_init = s4_init.run(ctx_i)
        icodes = {f.code for f in f_init}
        self.assertIn("duplicate-service", icodes)
        self.assertIn("exec-missing", icodes)
        self.assertIn("start-unknown-service", icodes)
        dup_f = next(f for f in f_init if f.code == "duplicate-service")
        self.assertIn("dup_svc", dup_f.detail)
        self.assertNotIn("ovr_svc", dup_f.detail)
        self.assertTrue(ctx_i.shared["init"].get("trigger_timeline"))

    def test_wave2_cil_binder_prop_avc_art_vdex_and_graphics5(self):
        import io
        import zipfile
        from core.vdev.selinux import audit_cil_symbols
        from core.vdev.stages import s2_selinux, s6_runtime, s11_avc, s14_hardware
        from core.vdev.initmodel import InitModel, Service
        from core.vdev.context import Context
        from core.vdev.mounts import Layout
        from core.vdev.vfs import VirtualFS
        from core.vdev.source import MemoryTreeSource

        # 1) CIL secilc -m symbol resolution: duplicate (type) legal, type vs typeattribute -> BLOCKER,
        #    unresolved symbol outside (optional) -> BLOCKER, inside (optional) -> ignored
        vfs_cil = VirtualFS()
        vfs_cil.add_mount("/", MemoryTreeSource({
            "/system/etc/selinux/plat_sepolicy.cil": (
                b"(type init)\n(type foo_type)\n(type clash_sym)\n"
                b"(typeattribute domain)\n"
                b"(optional opt_block (allow init opt_ghost_type (file (read))))\n"
            ),
        }, label="sys"), "system")
        vfs_cil.add_mount("/vendor", MemoryTreeSource({
            "/etc/selinux/plat_pub_versioned.cil": b"(type foo_type)\n",
            "/etc/selinux/vendor_sepolicy.cil": (
                b"(typeattribute clash_sym)\n"
                b"(allow init missing_real_type (file (read)))\n"
            ),
        }, label="vnd"), "vendor")
        cil_audit = audit_cil_symbols(vfs_cil)
        dup_names = {d[0] for d in cil_audit["duplicate_types"]}
        unres_names = {u[0] for u in cil_audit["unresolved_symbols"]}
        self.assertNotIn("foo_type", dup_names)
        self.assertIn("clash_sym", dup_names)
        self.assertIn("missing_real_type", unres_names)
        self.assertNotIn("opt_ghost_type", unres_names)

        lay = Layout(str(self.root))
        lay.partitions["vendor"] = str(self.root)
        ctx_se = Context(str(self.root), lay, vfs_cil, None)
        f_se = s2_selinux.run(ctx_se)
        se_codes = {f.code: f.severity for f in f_se}
        self.assertEqual(se_codes.get("cil-duplicate-type"), "blocker")
        self.assertEqual(se_codes.get("cil-symbol-unresolved"), "blocker")

        # 2) Binder service_contexts + property_contexts AVC audit (s11_avc)
        vfs_avc = VirtualFS()
        vfs_avc.add_mount("/", MemoryTreeSource({
            "/system/bin/init": b"\x7fELF",
            "/system/bin/servicemanager": b"\x7fELF",
            "/system/etc/selinux/plat_sepolicy.cil": (
                b"(type kernel)\n(type init)\n(type init_exec)\n(type vendor_init)\n(type surfaceflinger)\n"
                b"(type bootanim)\n(type servicemanager_exec)\n(type servicemanager)\n"
                b"(type hal_composer_service)\n(type default_android_service)\n"
                b"(type vendor_gfx_prop)\n(type default_prop)\n"
                b"(sidcontext kernel (u r kernel ((s0) (s0))))\n"
                b"(allow kernel init_exec (file (execute)))\n"
                b"(typetransition kernel init_exec process init)\n"
                b"(allow init servicemanager_exec (file (execute)))\n"
                b"(allow servicemanager servicemanager_exec (file (entrypoint)))\n"
                b"(allow init servicemanager (process (transition)))\n"
                b"(typetransition init servicemanager_exec process servicemanager)\n"
            ),
            "/system/etc/selinux/plat_file_contexts": (
                b"/system/bin/init u:object_r:init_exec:s0\n"
                b"/system/bin/servicemanager u:object_r:servicemanager_exec:s0\n"
            ),
            "/system/etc/selinux/plat_service_contexts": (
                b"android.hardware.graphics.composer3.IComposer/default u:object_r:hal_composer_service:s0\n"
                b"* u:object_r:default_android_service:s0\n"
            ),
            "/system/etc/selinux/plat_property_contexts": (
                b"ro.hardware.egl u:object_r:vendor_gfx_prop:s0\n"
                b"* u:object_r:default_prop:s0\n"
            ),
        }, label="sys"), "system")
        ctx_avc = Context(str(self.root), lay, vfs_avc, None)
        ctx_avc.profile = {
            "props": {"ro.hardware.egl": "mali"},
            "prop_trace": {"by_partition": {"vendor": {"ro.hardware.egl": "mali"}}},
        }
        im = InitModel({})
        svc_sm = Service("servicemanager", "/system/bin/servicemanager", [], "init.rc")
        svc_sm.started_by.add("early-init")
        im.services.append(svc_sm)
        ctx_avc.shared["init"] = {"model": im}
        ctx_avc.shared["vintf"] = {
            "provided_hals": {
                "android.hardware.graphics.composer3": {
                    "formats": {"aidl"}, "versions": {"3"},
                    "fqnames": {"IComposer/default"}, "sources": {"/vendor/etc/vintf/manifest.xml"},
                },
                "android.hardware.graphics.allocator": {
                    "formats": {"aidl"}, "versions": {"2"},
                    "fqnames": {"IAllocator/default"}, "sources": {"/vendor/etc/vintf/manifest.xml"},
                },
            }
        }
        f_avc = s11_avc.run(ctx_avc)
        avc_codes = {f.code: f.severity for f in f_avc}
        # IAllocator/default lacks explicit context -> BLOCKER; bootanim lacks find on hal_composer_service -> BLOCKER;
        # ro.hardware.egl denied to vendor_init -> WARN
        self.assertEqual(avc_codes.get("binder-service-context-missing"), "blocker")
        self.assertEqual(avc_codes.get("binder-avc-denied"), "blocker")
        self.assertEqual(avc_codes.get("prop-avc-denied-critical"), "warn")

        # 3) ART .vdex/.odex ZIP CRC-32 verification (match PASS, stale mismatch BLOCKER, stripped PASS)
        def _make_jar(dex_bytes: bytes | None) -> tuple[bytes, int]:
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_STORED) as zf:
                if dex_bytes is not None:
                    zf.writestr("classes.dex", dex_bytes)
                else:
                    zf.writestr("META-INF/MANIFEST.MF", b"Manifest-Version: 1.0\n")
            raw = buf.getvalue()
            crc = 0
            if dex_bytes is not None:
                with zipfile.ZipFile(io.BytesIO(raw), "r") as zf:
                    crc = zf.getinfo("classes.dex").CRC
            return raw, crc

        def _make_vdex027(crcs: list[int]) -> bytes:
            # vdex027 header (12B: magic 4B + ver 4B + n_sec 4B) + 1 section header (12B: kind=0, off=24, size=4*N) + crc array
            sec_off = 24
            sec_sz = 4 * len(crcs)
            hdr = b"vdex027\x00" + struct.pack("<I", 1)
            sec = struct.pack("<III", 0, sec_off, sec_sz)
            body = b"".join(struct.pack("<I", c) for c in crcs)
            return hdr + sec + body

        jar_ok, crc_ok = _make_jar(b"dex\n039\x00ORIGINAL_DEX_BYTES")
        jar_edited, _ = _make_jar(b"dex\n039\x00PATCHED_SMALI_BYTES!")
        jar_stripped, _ = _make_jar(None)
        vdex_orig = _make_vdex027([crc_ok])

        # Clean matching JAR + stripped JAR -> PASS
        vfs_art_ok = VirtualFS()
        vfs_art_ok.add_mount("/", MemoryTreeSource({
            "/system/framework/services.jar": jar_ok,
            "/system/framework/oat/arm64/services.vdex": vdex_orig,
            "/system/framework/oat/arm64/services.odex": b"oat",
            "/system/framework/framework.jar": jar_stripped,
            "/system/framework/arm64/boot-framework.vdex": vdex_orig,
            "/system/framework/arm64/boot-framework.art": b"art",
            "/system/framework/arm64/boot-framework.odex": b"oat",
        }, label="sys"), "system")
        f_art_ok = []
        art_info_ok = s6_runtime._audit_art_oat_vdex(vfs_art_ok, f_art_ok)
        self.assertEqual(art_info_ok["vdex_checked"], 2)
        self.assertEqual(art_info_ok["vdex_stripped_ok"], 1)
        self.assertTrue(any(f.code == "art-vdex-ok" and f.severity == "pass" for f in f_art_ok), f_art_ok)

        # Sabotage: patched services.jar with stale services.vdex -> BLOCKER
        vfs_art_bad = VirtualFS()
        vfs_art_bad.add_mount("/", MemoryTreeSource({
            "/system/framework/services.jar": jar_edited,
            "/system/framework/oat/arm64/services.vdex": vdex_orig,
        }, label="sys"), "system")
        f_art_bad = []
        s6_runtime._audit_art_oat_vdex(vfs_art_bad, f_art_bad)
        self.assertTrue(
            any(f.code == "art-vdex-checksum-mismatch" and f.severity == "blocker" for f in f_art_bad),
            f_art_bad,
        )

        # 4) 5-layer Graphics Stack: Mapper 5.0 VINTF mismatch & missing Vulkan ICD
        vfs_gfx = VirtualFS()
        vfs_gfx.add_mount("/vendor", MemoryTreeSource({
            "/bin/hw/android.hardware.graphics.composer3-service": b"\x7fELF",
            "/bin/hw/android.hardware.graphics.allocator-service": b"\x7fELF",
            "/lib64/hw/mapper.wrong.so": b"\x7fELF",
            "/lib64/egl/libGLES_powervr.so": b"\x7fELF",
        }, label="vnd"), "vendor")
        ctx_gfx = Context(str(self.root), lay, vfs_gfx, None)
        ctx_gfx.profile = {"props": {"ro.hardware.egl": "powervr", "ro.hardware.vulkan": "powervr"}}
        ctx_gfx.shared["vintf"] = {
            "provided_hals": {
                "mapper": {"formats": {"native"}, "versions": {"5.0"}, "fqnames": {"@5.0/powervr"}, "sources": set()},
            }
        }
        f_gfx = s14_hardware.run(ctx_gfx)
        gfx_codes = {f.code: f.severity for f in f_gfx}
        self.assertEqual(gfx_codes.get("hw-mapper5-lib-missing"), "blocker")
        self.assertEqual(gfx_codes.get("hw-vulkan-driver-mismatch"), "warn")

    def test_wave3_boot_trace_root_causes_and_verbose_ui(self):
        from core.vdev import analyze
        from core.vdev.report import Finding, Report, StageResult
        from ui.boot_analyzer import BootAnalyzerController

        # 1) Full analyze() emits populated boot_trace, ranked root_causes, and JSON dict fields
        rom = self.root / "w3_rom"
        (rom / "system" / "system" / "etc" / "init" / "hw").mkdir(parents=True)
        (rom / "system" / "system" / "etc" / "selinux").mkdir(parents=True)
        (rom / "system" / "system" / "bin").mkdir(parents=True)
        (rom / "system" / "system" / "build.prop").write_text(
            "ro.build.version.sdk=35\nro.build.version.release=15\nro.product.device=w3dev\n",
            encoding="utf-8",
        )
        (rom / "system" / "system" / "etc" / "selinux" / "plat_sepolicy.cil").write_text(
            "(type init)\n(type init_exec)\n(allow init init_exec (file (execute)))\n",
            encoding="utf-8",
        )
        (rom / "system" / "system" / "etc" / "init" / "hw" / "init.rc").write_text(
            "on early-init\n    start demo_svc\nservice demo_svc /system/bin/demo\n    class core\n",
            encoding="utf-8",
        )
        (rom / "system" / "system" / "bin" / "demo").write_bytes(b"\x7fELF")

        rep = analyze(str(rom), options={"deep_linker": False})
        self.assertEqual(rep.error, "")
        self.assertTrue(rep.boot_trace, "boot_trace should contain chronological entries")
        self.assertTrue(any(e["phase"] == "props" for e in rep.boot_trace))
        self.assertTrue(any(e["phase"] == "init_trigger" for e in rep.boot_trace))
        # Verify root_causes ordering: BLOCKERs always precede WARNs
        sevs = [c["severity"] for c in rep.root_causes]
        if "blocker" in sevs and "warn" in sevs:
            self.assertLess(max(i for i, s in enumerate(sevs) if s == "blocker"),
                            min(i for i, s in enumerate(sevs) if s == "warn"))
        d = rep.to_dict()
        self.assertIn("boot_trace", d)
        self.assertIn("root_causes", d)

        # 2) BootAnalyzerController Verbose toggle & Verdict rendering
        ctrl = BootAnalyzerController(None)
        self.assertTrue(ctrl.logcatVerbose)
        mock_rep = Report("mock")
        mock_rep.profile = {"device": "mockdev", "sdk": "36", "release": "16"}
        mock_rep.boot_simulation = {"reaches_system_server": False, "verdict": "BLOCKED before SystemServer", "phases": []}
        mock_rep.stages.append(StageResult("runtime", "Runtime", [
            Finding("runtime", "art-vdex-checksum-mismatch", "blocker", "Stale vdex mismatch", "detail", "delete oat/"),
        ], ran=True))
        mock_rep.boot_trace = [
            {"phase": "props", "pid": "1", "tid": "1", "level": "I", "tag": "init", "msg": "prop trace line", "detail": "", "verbose": True},
            {"phase": "graphics", "pid": "400", "tid": "400", "level": "I", "tag": "SurfaceFlinger", "msg": "gfx trace line", "detail": "", "verbose": True},
        ]
        mock_rep.root_causes = [
            {"rank": 1, "severity": "blocker", "stage": "runtime", "code": "art-vdex-checksum-mismatch",
             "title": "Stale vdex mismatch", "detail": "detail", "hint": "delete oat/", "path": "/system/framework/oat/arm64/services.vdex"},
        ]
        ctrl._apply_report(mock_rep, 420)
        lines_verbose = list(ctrl.logcatLines)
        self.assertTrue(any(r["tag"] == "BootTrace" for r in lines_verbose))
        self.assertTrue(any(r["msg"] == "prop trace line" for r in lines_verbose))
        self.assertTrue(any(r["tag"] == "BootVerdict" for r in lines_verbose))
        self.assertTrue(any(r["tag"] == "SystemServer" for r in lines_verbose))

        ctrl.setLogcatVerbose(False)
        self.assertFalse(ctrl.logcatVerbose)
        lines_compact = list(ctrl.logcatLines)
        self.assertFalse(any(r["tag"] == "BootTrace" for r in lines_compact))
        self.assertFalse(any(r["msg"] == "prop trace line" for r in lines_compact))
        self.assertTrue(any(r["tag"] == "BootVerdict" for r in lines_compact))
        self.assertTrue(any(r["tag"] == "SystemServer" for r in lines_compact))


if __name__ == "__main__":
    unittest.main()
