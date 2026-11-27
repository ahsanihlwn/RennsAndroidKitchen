# SPDX-License-Identifier: MPL-2.0
import gzip
import json
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from core import kernel_boot_compare, kernel_rebuild, kernel_version
from core.command_context import OperationError


CONFIG = "CONFIG_RAK_TEST=y\n"
SOURCE = """/* SPDX-License-Identifier: GPL-2.0 */
#include "nearby.h"
#if CONFIG_RAK_TEST
int rak_probe(struct rak_device *value)
{
    return value->state + RAK_OFFSET;
}
#endif
_Static_assert(__LINE__ == 9, "Reconstruction must preserve downstream source lines");
"""
RECOVERED = "int rak_probe(struct rak_device *param_1) { return param_1->state + RAK_OFFSET; }"


def _pack_stored_kernel(path, kernel):
    ramdisk = b"fixture ramdisk"
    header = bytearray(4096)
    header[:8] = b"ANDROID!"
    struct.pack_into("<2I", header, 8, len(kernel), len(ramdisk))
    struct.pack_into("<I", header, 40, 4)
    path.write_bytes(header + kernel.ljust(4096, b"\0") + ramdisk.ljust(4096, b"\0"))


def _pack_boot(path, payload, mtime):
    _pack_stored_kernel(path, gzip.compress(payload, mtime=mtime))


class RebuildTests(unittest.TestCase):
    def setUp(self):
        clang = shutil.which("clang")
        if not clang:
            self.skipTest("Clang is unavailable in this environment")
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name).resolve()
        self.source_root = self.root / "kernel sources"
        self.build_root = self.root / "kernel build"
        self.driver = self.source_root / "drivers/rak"
        self.driver.mkdir(parents=True)
        self.generated = self.build_root / "include/generated"
        self.generated.mkdir(parents=True)
        self.source = self.driver / "a-probe.c"
        self.source.write_text(SOURCE, encoding="utf-8")
        (self.driver / "nearby.h").write_text(
            "#define RAK_OFFSET 9\nstruct rak_device { int state; };\n", encoding="utf-8",
        )
        (self.build_root / ".config").write_text(CONFIG, encoding="utf-8")
        (self.generated / "autoconf.h").write_text("#define CONFIG_RAK_TEST 1\n", encoding="utf-8")
        self.db = self.build_root / "compile_commands.json"
        self.command = [
            clang, "--target=aarch64-linux-gnu", "-O2", "-ffreestanding", "-include",
            "include/generated/autoconf.h", "-MMD", "-MF", "original.d",
            "-c", str(self.source), "-o", "original.o",
        ]
        self.db.write_text(json.dumps([{
            "directory": str(self.build_root), "file": str(self.source), "arguments": self.command,
        }]), encoding="utf-8")
        (self.build_root / "original.o").write_bytes(b"untouched object")
        out = self.root / "reconstructed"
        out.mkdir()
        self.manifest = out / "reconstruction_manifest.json"

    def write_manifest(self, code=RECOVERED, config=CONFIG):
        self.manifest.write_text(json.dumps({
            "kernel_info": {"config_sha256": kernel_version.kernel_config_digest(config)},
            "files": [{
                "path": "drivers/rak/a-probe.c",
                "recovered_functions": [{"name": "rak_probe", "address": "0x20", "code": code}],
            }],
        }), encoding="utf-8")

    def test_clang_compiles_vfs_body_with_same_build_headers(self):
        self.write_manifest()
        report = kernel_rebuild.compile_tree(self.manifest, self.source_root, self.db)
        self.assertEqual(report["compiled"], 1)
        row, = report["units"]
        self.assertTrue(row["object_identical"])
        self.assertTrue(row["config_matches_embedded"])
        verified = (self.manifest.parent / row["c_path"]).read_text(encoding="utf-8")
        self.assertIn("GPL-2.0", verified)
        self.assertIn("rak_recovered_arg_1->state + RAK_OFFSET", verified)
        self.assertEqual(self.source.read_text(encoding="utf-8"), SOURCE)
        self.assertEqual((self.build_root / "original.o").read_bytes(), b"untouched object")
        self.assertFalse((self.build_root / "original.d").exists())
        overlay = json.loads(Path(report["overlay"]).read_text(encoding="utf-8"))
        self.assertEqual(overlay["roots"][0]["name"], self.source.as_posix())

    def test_verified_bug_site_restores_the_original_compiler_trap(self):
        source = (
            '#define BUG() __builtin_trap()\n'
            '#define BUG_ON(test) do { if (test) BUG(); } while (0)\n'
            'int rak_probe(int value) {\n'
            '    BUG_ON(!value);\n'
            '    return value + 1;\n'
            '}\n'
        )
        self.source.write_text(source, encoding="utf-8")
        self.write_manifest(
            'int rak_probe(int4 param_1) {\n'
            '  code *pcVar1;\n'
            '  if (param_1 == 0) {\n'
            '    pcVar1 = (code *)SoftwareBreakpoint(0x800,0x2c);\n'
            '    (*pcVar1)();\n'
            '  }\n'
            '  return param_1 + 1;\n'
            '}'
        )
        data = json.loads(self.manifest.read_text(encoding="utf-8"))
        function = data["files"][0]["recovered_functions"][0]
        function["bug_sites"] = [{"address": "0x28", "line": 4}]
        self.manifest.write_text(json.dumps(data), encoding="utf-8")
        report = kernel_rebuild.compile_tree(self.manifest, self.source_root, self.db)
        row, = report["units"]
        self.assertEqual(row["verified_bug_rewrites"], 1)
        self.assertTrue(row["object_identical"])
        self.assertIn("#line 4\nBUG();", (self.manifest.parent / row["c_path"]).read_text(encoding="utf-8"))

    def test_shorter_recovered_body_keeps_offsets_for_later_inline_asm(self):
        tail = b'void later(void) { __asm__ volatile("nop"); }\n'
        source = (b"int rak_probe(int value) {\n"
                  b"    int another_name = value;\n"
                  b"    return another_name + 1;\n"
                  b"}\n" + tail)
        self.source.write_bytes(source)
        self.write_manifest("int rak_probe(int value) {\n    return value + 1;\n}")
        report = kernel_rebuild.compile_tree(self.manifest, self.source_root, self.db)
        row, = report["units"]
        verification = (self.manifest.parent / row["c_path"]).read_bytes()
        self.assertEqual(row["preserved_source_spans"], 1)
        self.assertEqual(len(verification), len(source))
        self.assertEqual(verification.count(b"\n"), source.count(b"\n"))
        self.assertEqual(verification[len(source) - len(tail):], tail)

    def test_build_check_reports_missing_tools_and_wrong_config(self):
        digest = kernel_version.kernel_config_digest(CONFIG)
        report = kernel_rebuild.check_build(self.source_root, self.db, expected_config=digest)
        self.assertTrue(report["ready"])
        self.assertIn("matches embedded IKCONFIG", str(report))

        (self.build_root / ".config").write_text("CONFIG_RAK_TEST=m\n", encoding="utf-8")
        report = kernel_rebuild.check_build(
            self.source_root, self.db, expected_config=digest, emit_rust=True,
            c2rust=str(self.root / "missing-c2rust"),
        )
        self.assertFalse(report["ready"])
        failed = {check["requirement"] for check in report["checks"] if not check["available"]}
        self.assertEqual(failed, {"kbuild_config", "c2rust"})

    def test_trace_includes_aarch64_words_and_build_inputs(self):
        objdump = shutil.which("llvm-objdump")
        if not objdump:
            self.skipTest("llvm-objdump is unavailable in this environment")
        output = self.root / "trace"
        report = kernel_rebuild.trace_compile(
            self.source_root, self.db, "drivers/rak/a-probe.c", "rak_probe", output, objdump=objdump,
        )
        self.assertEqual(report["object_format"], "ELF")
        self.assertIn("@rak_probe", (output / "rak_probe.ll").read_text(encoding="utf-8"))
        self.assertIn("FunctionDecl", (output / "rak_probe.ast.txt").read_text(encoding="utf-8"))
        self.assertRegex((output / "rak_probe.words.txt").read_text(encoding="ascii"), r"[0-9a-f]{8} [01]{32} ")
        self.assertFalse((self.build_root / "original.d").exists())

    def test_config_mismatch_and_bad_c_keep_failure_details(self):
        for code, config, marker in (
            (RECOVERED, "CONFIG_RAK_TEST=m\n", "differs from embedded IKCONFIG"),
            (RECOVERED.replace("RAK_OFFSET", "MISSING_FLAG"), CONFIG, "MISSING_FLAG"),
        ):
            with self.subTest(marker=marker):
                self.write_manifest(code, config)
                with self.assertRaisesRegex(OperationError, "compile-report.json"):
                    kernel_rebuild.compile_tree(self.manifest, self.source_root, self.db)
                report = json.loads((self.manifest.parent / "compile-report.json").read_text(encoding="utf-8"))
                self.assertEqual(report["compiled"], 0)
                self.assertIn(marker, report["units"][0]["error"])

    def test_missing_rust_tool_keeps_compiled_c(self):
        self.write_manifest()
        with self.assertRaisesRegex(OperationError, "compile-report.json"):
            kernel_rebuild.compile_tree(
                self.manifest, self.source_root, self.db, emit_rust=True, c2rust=str(self.root / "missing-c2rust"),
            )
        report = json.loads((self.manifest.parent / "compile-report.json").read_text(encoding="utf-8"))
        self.assertEqual(report["compiled"], 1)
        self.assertIn("C2Rust was not found", report["rust_error"])

    def test_linked_and_packaged_reconstruction_matches_boot_fixture(self):
        lld = shutil.which("ld.lld")
        objcopy = shutil.which("llvm-objcopy")
        if not lld or not objcopy:
            self.skipTest("LLD and llvm-objcopy are required")
        self.write_manifest()
        report = kernel_rebuild.compile_tree(self.manifest, self.source_root, self.db)
        linker_script = self.root / "fixture.lds"
        linker_script.write_text(
            """SECTIONS {
                . = 0;
                .head : { LONG(0x14000010) . = 56; LONG(0x644d5241) . = 64; }
                .text : { *(.text) }
                /DISCARD/ : { *(.comment .eh_frame .note.*) }
            }
            """, encoding="utf-8",
        )
        path = "drivers/rak/a-probe.c"
        unit = kernel_rebuild._prepare_unit(kernel_rebuild._units(self.db, self.source_root)[path], path)
        images = []
        for kind, flags in (("reference", []), ("reconstructed", ["-ivfsoverlay", report["overlay"]])):
            obj = self.root / f"{kind}.o"
            elf = self.root / f"{kind}.elf"
            image = self.root / f"{kind}.Image"
            boot = self.root / f"{kind}.img"
            kernel_rebuild._run(unit.command(*flags, "-c", "-o", str(obj)), unit.cwd, f"Compile {kind}")
            kernel_rebuild._run(
                [lld, "-m", "aarch64elf", "-T", str(linker_script),
                 "-o", str(elf), str(obj)], unit.cwd, f"Link {kind}",
            )
            kernel_rebuild._run([objcopy, "-O", "binary", str(elf), str(image)], unit.cwd, f"Create {kind} Image")
            payload = image.read_bytes()
            self.assertEqual(payload[56:60], b"ARMd")
            _pack_boot(boot, payload, mtime=0)
            images.append(boot)
        self.assertTrue(kernel_boot_compare.compare_boot(*images)["byte_identical"])

    def test_c2rust_reads_the_verified_overlay_and_publishes_a_module(self):
        self.write_manifest()
        real_run = kernel_rebuild._run

        def run(argv, cwd, label):
            if not label.startswith("C2Rust"):
                return real_run(argv, cwd, label)
            rust_args = json.loads(Path(argv[-1]).read_text(encoding="utf-8"))[0]
            args = rust_args["arguments"]
            processed = real_run(
                [*args[:-1], "-E"], rust_args["directory"], "Preprocess C2Rust invocation",
            )
            self.assertIn("return rak_recovered_arg_1->state + 9;", processed)
            rust_root = Path(argv[argv.index("--output-dir") + 1]) / "src"
            rust_root.mkdir()
            (rust_root / "a_probe.rs").write_text("pub fn rak_probe() -> i32 { 9 }\n", encoding="utf-8")
            return ""

        with patch.object(kernel_rebuild, "_run", side_effect=run):
            report = kernel_rebuild.compile_tree(
                self.manifest, self.source_root, self.db, emit_rust=True, c2rust=self.command[0],
            )
        self.assertEqual(report["transpiled"], 1)
        self.assertTrue((self.manifest.parent / "rust/drivers/rak/a-probe.rs").is_file())

    def test_real_c2rust_when_installed(self):
        if not shutil.which("c2rust"):
            self.skipTest("C2Rust is unavailable in this environment")
        self.write_manifest()
        report = kernel_rebuild.compile_tree(self.manifest, self.source_root, self.db, emit_rust=True)
        self.assertEqual(report["transpiled"], 1)

    def test_rust_retry_compiles_only_recovered_scalar_functions(self):
        self.source.write_text(
            "#include \"nearby.h\"\n"
            "typedef _Bool kernel_bool;\n"
            "kernel_bool rak_probe(int value) { return value != RAK_OFFSET; }\n", encoding="utf-8",
        )
        self.write_manifest("int rak_probe(int4 param_1) { return param_1 != 9; }")
        rust_calls = []

        def rust(unit, overlay, staging, executable, names):
            rust_calls.append(unit.source)
            if overlay:
                raise OperationError("C2Rust full input failed: Clang AST was invalid")
            self.assertNotEqual(unit.source, self.source)
            body = unit.source.read_text(encoding="utf-8")
            self.assertIn("_Bool rak_probe(int value)", body)
            self.assertIn("rak_recovered_arg_1 != 9", body)
            self.assertNotIn("nearby.h", body)
            return "pub unsafe extern \"C\" fn rak_probe(value: i32) -> bool { value != 9 }\n"

        with patch.object(kernel_rebuild, "_rust", side_effect=rust):
            report = kernel_rebuild.compile_tree(
                self.manifest, self.source_root, self.db, emit_rust=True, c2rust=self.command[0],
            )
        self.assertEqual(len(rust_calls), 2)
        self.assertEqual(report["units"][0]["rust_scope"], "recovered_functions")


class BootComparisonTests(unittest.TestCase):
    def test_distinguishes_gzip_container_from_decoded_kernel(self):
        with tempfile.TemporaryDirectory() as folder:
            paths = [Path(folder) / name for name in ("reference.img", "candidate.img")]
            for stamp, path in zip((0, 1), paths):
                _pack_boot(path, b"The same compiled Image", mtime=stamp)
            result = kernel_boot_compare.compare_boot(*paths)
            self.assertFalse(result["byte_identical"])
            self.assertFalse(result["matching_components"]["kernel"])
            self.assertTrue(result["matching_components"]["decoded_kernel"])
            self.assertTrue(result["matching_components"]["ramdisk"])
            self.assertEqual(result["decoded_kernel_similarity"]["matching_blocks"], 1)
            self.assertTrue(kernel_boot_compare.compare_boot(paths[0], paths[0])["byte_identical"])

    def test_legacy_lz4_comparison_cli_returns_json_from_a_fresh_process(self):
        try:
            import lz4.block
        except ImportError:
            self.skipTest("Legacy LZ4 codec is unavailable in this environment")
        with tempfile.TemporaryDirectory() as folder:
            image = Path(folder) / "boot.img"
            raw = b"Android legacy LZ4 kernel" * 100
            block = lz4.block.compress(raw, store_size=False)
            _pack_stored_kernel(image, b"\x02\x21\x4c\x18" + struct.pack("<I", len(block)) + block)
            result = subprocess.run(
                [sys.executable, "-m", "core.binary_cli", "compare-boot", str(image), str(image)],
                capture_output=True, text=True, check=True,
            )
            decoded = json.loads(result.stdout)["result"]
            self.assertTrue(decoded["matching_components"]["decoded_kernel"])


class PseudocodeTests(unittest.TestCase):
    def test_matching_member_equality_tokens_preserve_original_debug_columns(self):
        original = b"{\n\treturn left->parent == right->parent;\n}"
        recovered = b"{\n  return left -> parent == right -> parent;\n}"
        self.assertEqual(kernel_rebuild._fit_source_span(original, recovered), original)
        different = recovered.replace(b"==", b"!=")
        self.assertNotEqual(kernel_rebuild._fit_source_span(original, different), original)

    def test_unused_recovered_parameter_needs_no_compiler_alias(self):
        body = kernel_rebuild._function_body(
            "compare", "int compare(uint8 param_1, int param_2) { return param_2; }",
            [("device", "struct device *"), ("value", "int")],
        )
        self.assertNotIn(b"rak_recovered_arg_1", body)
        self.assertIn(b"return rak_recovered_arg_2;", body)

    def test_verified_dwarf_reassigns_an_inlined_driver_to_its_kbuild_source(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            common = root / "common"
            vmlinux = root / "vmlinux"
            vmlinux.write_bytes(b"\x7fELF fixture")
            digest = kernel_rebuild._fingerprint(vmlinux)
            consumer = "drivers/net/phy/mdio_devres.c"
            helper = "drivers/net/phy/mdio_bus.c"
            source = common / consumer
            commands = {consumer: [(source, root, {"arguments": ["clang", "-c", str(source)]})]}
            manifest = {"format": "ELF", "sha256": digest}
            candidate = {"name": "release", "address": "0x110", "code": "void release(void) {}",
                         "bug_sites": [{"line": 663, "address": "0x124"}]}
            recovered = [{"path": helper, "recovered_functions": [candidate]}]
            locations = "inline_helper\ncommon/drivers/net/phy/mdio_bus.c:663:2\nrelease\ncommon/" + consumer + ":13:0\n\n"
            with patch.object(kernel_rebuild, "_llvm_tool", return_value="llvm-symbolizer"), \
                    patch.object(kernel_rebuild, "_run", return_value=locations) as run:
                rows, evidence = kernel_rebuild._dwarf_compile_rows(vmlinux, manifest, recovered, commands, common)
                self.assertEqual(run.call_args.kwargs["input_text"], "0x110\n")
                run.reset_mock()
                vmlinux.write_bytes(b"\x7fELF different")
                with self.assertRaisesRegex(OperationError, "SHA-256 differs"):
                    kernel_rebuild._dwarf_compile_rows(vmlinux, manifest, recovered, commands, common)
                run.assert_not_called()
            self.assertEqual(rows[0]["path"], consumer)
            self.assertNotIn("bug_sites", rows[0]["recovered_functions"][0])
            self.assertIn("bug_sites", candidate)
            self.assertEqual(evidence["reassignments"][0]["from"], helper)

    def test_legacy_clang_filtered_ast_dump_with_heading(self):
        unit = kernel_rebuild.Unit(Path("driver.c").resolve(), Path.cwd(), ("clang",), None)
        declaration = {
            "kind": "FunctionDecl", "name": "probe", "loc": {"file": str(unit.source)},
            "inner": [{"kind": "CompoundStmt", "range": {"begin": {"offset": 4}, "end": {"offset": 12, "tokLen": 1}}}],
        }
        output = "Dumping probe:\n" + json.dumps(declaration)
        with patch.object(kernel_rebuild, "_run", return_value=output):
            definition = kernel_rebuild._ast_definition(unit, "probe")
        self.assertEqual((definition.start, definition.end), (4, 13))

    def test_recovers_fixed_width_clz_with_a_defined_zero_result(self):
        body = kernel_rebuild._function_body(
            "leading", "uint4 leading(uint8 param_1) { return LZCOUNT(param_1); }",
            [("input", "unsigned long")],
        )
        self.assertIn(b"rak_recovered_arg_1 ? __builtin_clzll((unsigned long long)rak_recovered_arg_1) : 64", body)


if __name__ == "__main__":
    unittest.main()
