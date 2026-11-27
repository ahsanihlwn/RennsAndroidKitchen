# SPDX-License-Identifier: MPL-2.0
import gzip
import json
import struct

import pytest

from core import binary_cli, binary_core, binary_semantics, kernel_btf, kernel_symbols, so_document
from core.command_context import OperationError


SOURCE = "drivers/rak/example.c"
BASE = 0xffffffc008000000


def test_native_decompiler_merges_overlapping_windows_with_the_same_mapping(tmp_path, monkeypatch):
    from core import native_backend, native_decompiler

    data = bytes(range(128))
    binary = tmp_path / "Image"
    binary.write_bytes(data)
    monkeypatch.setattr(native_decompiler, "WHOLE_SECTION_LIMIT", 32)
    monkeypatch.setattr(native_decompiler, "WINDOW", 64)
    result = {
        "header": {"abi": "AARCH64", "endianness": "little"},
        "sections": [{"addr": hex(BASE), "offset": "0x0", "size": len(data), "flags": 6}],
    }
    monkeypatch.setenv("RAK_NATIVE_BACKEND", "python")
    xml, names = native_decompiler.build_image_batch(str(binary), result, [BASE + 32, BASE + 64])
    assert len(names) == 2
    chunks = f'<bytechunk space="ram" offset="{BASE:#x}" readonly="true">\n'
    chunks += data[:64].hex() + "\n" + data[64:96].hex() + "\n</bytechunk>"
    assert chunks in xml and xml.count("<bytechunk ") == 1
    monkeypatch.setenv("RAK_NATIVE_BACKEND", "auto")
    if native_backend.binary_image_bytechunks(str(binary), [(BASE, 0, 96)]) is not None:
        assert native_decompiler.build_image_batch(str(binary), result, [BASE + 32, BASE + 64]) == (xml, names)


def test_integer_address_batch_populates_the_same_cache_as_hex_lookup(tmp_path, monkeypatch):
    from core import binary_session, native_decompiler

    image = tmp_path / "Image"
    image.write_bytes(b"function bytes")
    monkeypatch.setattr(binary_session, "storage_root", lambda: tmp_path / "sessions")
    session = binary_session.BinarySession(image)
    address = 0x75A58
    result = {"name": "fixture", "address": hex(address), "pseudocode": "void fixture(void) {}"}

    def batch(path, analysis, addresses):
        assert addresses == [address]
        return {address: result}

    monkeypatch.setattr(native_decompiler, "decompile_batch", batch)
    monkeypatch.setattr(native_decompiler, "decompile", lambda *args: pytest.fail("expected cached batch result"))
    assert session.decompile_native_batch({}, [address]) == {address: result}
    assert session.decompile_native({}, hex(address)) == result


def test_native_decompiler_retries_with_the_referenced_image_bytes(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from core import native_decompiler

    image = tmp_path / "Image"
    image.write_bytes(bytes(256))
    monkeypatch.setattr(native_decompiler, "WHOLE_SECTION_LIMIT", 32)
    monkeypatch.setattr(native_decompiler, "WINDOW", 64)
    monkeypatch.setattr(native_decompiler, "status", lambda: {"available": True, "launcher": "decomp_cli"})
    result = {
        "format": "ARM64 Image",
        "header": {"abi": "AARCH64"},
        "sections": [{"addr": hex(BASE), "offset": "0x0", "size": 256, "flags": 6}],
    }

    def run_image(launcher, xml, instructions, timeout):
        if f'<bytechunk space="ram" offset="{BASE + 160:#x}"' not in xml:
            output = f"Low-level ERROR: Bytes at {BASE + 192:#x} are not mapped\nExecution error: No function selected"
        else:
            assert f'<symbol space="ram" offset="{BASE + 192:#x}"' not in xml
            output = "[decomp]> print C\nvoid fixture(void) {}\n[decomp]> quit\n"
        return SimpleNamespace(stdout=output, stderr="", returncode=0)

    monkeypatch.setattr(native_decompiler, "_run_image", run_image)
    assert native_decompiler.decompile(str(image), result, BASE + 32)["pseudocode"] == "void fixture(void) {}"


def test_native_cache_waits_for_a_concurrent_publisher(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from core import binary_session, native_decompiler

    image = tmp_path / "Image"
    image.write_bytes(b"function bytes")
    monkeypatch.setattr(binary_session, "storage_root", lambda: tmp_path / "sessions")
    session = binary_session.BinarySession(image)
    ready = Event()
    fresh = {"pseudocode": "void fresh(void) {}"}
    published = {"pseudocode": "void published(void) {}"}

    def decompile(*args):
        ready.set()
        return fresh

    monkeypatch.setattr(native_decompiler, "decompile", decompile)
    folder = session.folder / "native"
    with ThreadPoolExecutor(max_workers=1) as pool:
        with binary_session._lock(folder):
            attempt = pool.submit(session.decompile_native, {}, "0x40")
            assert ready.wait(3)
            binary_session._write(folder / "function-40.json", published)
        assert attempt.result(timeout=5) == published


@pytest.mark.parametrize("file_field_offset", (0, 4))
def test_arm64_bug_table_resolves_file_displacement_from_the_matching_origin(tmp_path, file_field_offset):
    from core import binary_reconstruct

    image, _ = _image()
    table = 0x600
    struct.pack_into("<I", image, 0x40, 0xd4210000)
    struct.pack_into("<iiHH", image, table, 0x40 - table, 0x100 - table - file_field_offset, 211, 0)
    # An entry pointing to ordinary code cannot validate a source path.
    struct.pack_into("<iiHH", image, table + 12, 0x44 - table - 12, 0x100 - table - 12 - file_field_offset, 404, 0)
    path = tmp_path / "Image"
    path.write_bytes(image)
    result = binary_core.analyze_file(path)
    system_map = tmp_path / "System.map"
    system_map.write_text(
        f"{BASE:x} T _text\n{BASE + 0x40:x} T rak_example\n"
        f"{BASE + table:x} D __start___bug_table\n{BASE + table + 24:x} D __stop___bug_table\n",
        encoding="ascii",
    )
    metadata = binary_reconstruct._load_metadata(path, result, None, system_map)
    references, locations = binary_reconstruct._kernel_bug_references(path, result, metadata.bug_table)
    assert list(references) == [SOURCE]
    assert [ref["kind"] for ref in references[SOURCE]] == ["BUG_TABLE:L211"]
    assert references[SOURCE][0]["function_address"] == hex(BASE + 0x40)
    assert locations[SOURCE][0]["offset"] == "0x100"


def _btf():
    strings = bytearray(b"\0")

    def name(text):
        offset = len(strings)
        strings.extend(text.encode("ascii") + b"\0")
        return offset

    def record(kind, value, text="", count=0, extra=()):
        return struct.pack("<III", name(text) if text else 0, kind << 24 | count, value) + struct.pack(
            "<" + "I" * len(extra), *extra
        )

    types = b"".join([
        record(1, 4, "int", extra=(0x01000020,)),  # 1
        record(4, 0x24, "rak_device", 1, (name("state"), 1, 0x20 * 8)),  # 2
        record(2, 2),  # 3
        record(4, 0x20, "rak_mixer", 1, (name("device"), 3, 0x18 * 8)),  # 4
        record(2, 4),  # 5
        record(13, 1, count=1, extra=(name("mixer"), 5)),  # 6
        record(12, 6, "rak_example", count=1),  # 7
    ])
    return struct.pack("<HBB5I", 0xEB9F, 1, 0, 24, 0, len(types), len(types), len(strings)) + types + strings


def test_rewrites_nested_field_only_with_matching_parameter_and_layout():
    btf = kernel_btf.BTF(_btf(), 8, "little")
    proto = btf.prototype("rak_example")
    expr = "*(int *)(*(longlong *)(param_1 + 0x18) + 0x20)"
    source = "int rak_example(longlong param_1) { " + expr + " = 1; return 0; }"
    result, count = binary_semantics.rewrite(source, btf, proto)
    assert count == 1
    assert result == "int rak_example(struct rak_mixer *mixer) { mixer->device->state = 1; return 0; }"
    assert btf.declaration("rak_example", proto) == "int rak_example(struct rak_mixer * mixer);"

    mixed = source.replace("return 0;", "return consumes(param_1);")
    result, count = binary_semantics.rewrite(mixed, btf, proto)
    assert count == 1
    assert "((struct rak_mixer *)param_1)->device->state" in result
    assert "return consumes(param_1);" in result

    for variant in (
        source.replace("0x20", "0x21"),
        source.replace("*(int *)", "*(uint *)"),
        source.replace("longlong param_1", "int * param_1"),
        source.replace("{ ", "{ (param_1) += 8; "),
        source.replace(expr + " = 1;", f'log("{expr}"); /* {expr} */'),
    ):
        assert binary_semantics.rewrite(variant, btf, proto) == (variant, 0)
    unknown = source.replace("*(int *)", "*(xunknown4 *)")
    assert binary_semantics.rewrite(unknown, btf, proto)[1] == 1
    unknown_read = unknown.replace(expr.replace("*(int *)", "*(xunknown4 *)") + " = 1;", "")
    unknown_read = unknown_read.replace("return 0;", "return " + expr.replace("*(int *)", "*(xunknown4 *)") + ";")
    assert binary_semantics.rewrite(unknown_read, btf, proto) == (unknown_read, 0)
    unsigned_read = source.replace(expr + " = 1; return 0;", "return " + expr.replace("*(int *)", "*(uint4 *)") + ";")
    assert binary_semantics.rewrite(unsigned_read, btf, proto) == (
        "int rak_example(struct rak_mixer *mixer) { return ((unsigned int)mixer->device->state); }", 1,
    )
    with pytest.raises(ValueError, match="header"):
        kernel_btf.BTF(_btf()[:-1], 8, "little")


def test_rewrites_pointer_field_equality_only_when_both_operands_match_btf():
    btf = kernel_btf.BTF(_btf(), 8, "little")
    proto = kernel_btf.Type(13, "", 1, (("left", 5), ("right", 5)))
    source = (
        "int same(longlong param_1,longlong param_2) "
        "{ return *(int8 *)(param_1 + 0x18) == *(int8 *)(param_2 + 0x18); }"
    )
    assert binary_semantics.rewrite(source, btf, proto) == (
        "int same(struct rak_mixer *left,struct rak_mixer *right) { return left->device == right->device; }", 2,
    )
    for changed in (
        source.replace("param_2 + 0x18", "param_2 + 0x19"),
        source.replace("== *(int8 *)", "== *(int4 *)"),
        source.replace("param_2 + 0x18);", "param_2 + 0x18) + 1;"),
    ):
        assert binary_semantics.rewrite(changed, btf, proto) == (changed, 0)

def _image():
    image = bytearray(0x800)
    image[56:60] = b"ARM\x64"
    # ADRP X0, .; ADD X0, X0, #0x100; B +4; RET
    struct.pack_into("<4I", image, 0x40, 0x90000000, 0x91040000, 0x14000001, 0xd65f03c0)
    image[0x100:0x100 + len(SOURCE)] = SOURCE.encode("ascii")

    table_labels = {
        "offsets": 0x200, "relative_base": 0x208, "num_syms": 0x210, "names": 0x218,
        "markers": 0x220, "token_table": 0x228, "token_index": 0x400,
    }
    struct.pack_into("<I", image, 0x200, 0x40)
    struct.pack_into("<Q", image, 0x208, BASE)
    struct.pack_into("<I", image, 0x210, 1)
    image[0x218:0x21b] = b"\x02\x00\x01"
    tokens = [b"T", b"rak_example"] + [b""] * 254
    offset = 0
    for index, token in enumerate(tokens):
        struct.pack_into("<H", image, 0x400 + index * 2, offset)
        pos = 0x228 + offset
        image[pos:pos + len(token)] = token
        offset += len(token) + 1
    labels = {"kallsyms_" + label: BASE + offset for label, offset in table_labels.items()}
    return image, labels


def test_rejects_corrupt_kallsyms_markers(tmp_path):
    image, labels = _image()
    path = tmp_path / "Image"
    path.write_bytes(image)
    result = binary_core.analyze_file(path)
    assert "AARCH64" in so_document.overview(result)
    bias = binary_core.RAW_ARM64_BASE - BASE
    assert kernel_symbols.recover(path, result, labels, bias) == [
        kernel_symbols.Symbol(BASE + 0x40, "T", "rak_example")
    ]
    image[0x220] = 4
    path.write_bytes(image)
    with pytest.raises(ValueError, match="validation failed"):
        kernel_symbols.recover(path, result, labels, bias)


def test_reads_raw_btf_using_build_labels(tmp_path):
    from core import binary_reconstruct

    data, _ = _image()
    offset = len(data)
    blob = _btf()
    data.extend(blob)
    image = tmp_path / "Image"
    image.write_bytes(data)
    system_map = tmp_path / "System.map"
    system_map.write_text(
        f"{BASE:x} T _text\n{BASE + offset:x} R __start_BTF\n{BASE + len(data):x} R __stop_BTF\n",
        encoding="ascii",
    )
    analysis = binary_core.analyze_file(image)
    metadata = binary_reconstruct._load_metadata(str(image), analysis, None, system_map)
    assert metadata.sources["btf"] == "System.map Image range"
    assert metadata.btf.prototype("rak_example")


@pytest.mark.parametrize("failed", [False, True])
def test_boot_reconstruction_cli_connects_kallsyms_btf_and_asm(tmp_path, monkeypatch, capsys, failed):
    from core import binary_session, native_decompiler

    image, labels = _image()
    incidental = b"vendor/unknown/not_a_unit.c"
    image[0x730:0x730 + len(incidental)] = incidental
    kernel = gzip.compress(image)
    header = bytearray(4096)
    header[:8] = b"ANDROID!"
    struct.pack_into("<I", header, 8, len(kernel))
    struct.pack_into("<I", header, 40, 4)
    boot = tmp_path / "boot.img"
    boot.write_bytes(header + kernel)
    system_map = tmp_path / "System.map"
    system_map.write_text(
        f"{BASE:x} T _text\n" + "".join(f"{address:x} r {label}\n" for label, address in labels.items()),
        encoding="ascii",
    )
    btf = tmp_path / "vmlinux.btf"
    btf.write_bytes(_btf())

    def decompile(_self, result, address):
        assert int(address, 16) == BASE + 0x40
        assert result["addr_symbol_map"][BASE + 0x40] == "rak_example"
        if failed:
            raise OperationError("Decompilation fixture failure")
        return {"pseudocode": (
            "int rak_example(longlong param_1) { "
            "*(int *)(*(longlong *)(param_1 + 0x18) + 0x20) = 1; return 0; }"
        )}

    monkeypatch.setattr(binary_session.BinarySession, "decompile_native", decompile)
    monkeypatch.setattr(native_decompiler, "status", lambda: {"available": False})
    out = tmp_path / "src"
    assert binary_cli.run([
        "reconstruct-tree", str(boot), "--output", str(out), "--btf", str(btf), "--system-map", str(system_map),
        "--annotated-asm",
    ]) == int(failed)
    response = json.loads(capsys.readouterr().out)
    assert response["ok"] is not failed
    manifest = json.loads((out / "reconstruction_manifest.json").read_text(encoding="utf-8"))
    assert manifest["binary_path"] == str(boot)
    assert manifest["origin_sha256"] != manifest["sha256"]
    assert manifest["metadata"]["kallsyms"]["new_functions"] == 1
    assert manifest["failed_functions"] == int(failed)
    assert manifest["typed_field_rewrites"] == int(not failed)
    recovered_map = out / manifest["recovered_system_map"]
    assert recovered_map.read_text(encoding="ascii").strip() == f"{BASE + 0x40:x} T rak_example"
    row, = manifest["files"]
    assert row["path"] == SOURCE
    assert row["path_attribution"] == "direct_reference"
    assert row["output_path"].startswith("unattributed/")
    assert not (out / SOURCE).exists()
    assert incidental.decode() in manifest["unverified_path_strings"]
    assert not (out / incidental.decode()).exists()
    reconstructed = (out / row["output_path"]).read_text(encoding="utf-8")
    if failed:
        assert response["application_error"] is False
        assert "Decompile failed" in reconstructed
        assert manifest["files"][0]["function_errors"][0]["detail"] == "Decompilation fixture failure"
    else:
        assert "mixer->device->state = 1;" in reconstructed
    assembly = (out / row["annotated_asm"]).read_text(encoding="utf-8")
    assert "ADRP+ADD string" in assembly
    assert "branch to .L_" in assembly
    assert "c0035fd6" in assembly


def test_vendor_boot_cannot_produce_a_kernel_tree_even_with_analysis(tmp_path):
    from core import binary_reconstruct

    image = tmp_path / "vendor_boot.img"
    image.write_bytes(b"VNDRBOOT" + b"Linux version 6.12.38" + bytes(4096))
    output = tmp_path / "source"
    with pytest.raises(OperationError, match="Vendor boot images"):
        binary_reconstruct.reconstruct_source_tree(str(image), output_dir=str(output), analysis={})
    assert not output.exists()


def test_rejected_dwarf_reference_preserves_previous_export(tmp_path):
    from core import binary_reconstruct

    image, _ = _image()
    path = tmp_path / "Image"
    path.write_bytes(image)
    reference = tmp_path / "vmlinux"
    reference.write_bytes(b"not an ELF")
    output = tmp_path / "kernel_src"
    output.mkdir()
    readme = output / "README.md"
    readme.write_text("previous verified export", encoding="utf-8")
    with pytest.raises(OperationError, match="Cannot read kernel DWARF"):
        binary_reconstruct.reconstruct_source_tree(
            str(path), output_dir=str(output), dwarf_reference=str(reference), offline=True,
        )
    assert readme.read_text(encoding="utf-8") == "previous verified export"


def test_raw_image_reaches_native_ghidra(tmp_path):
    from core import native_decompiler

    if not native_decompiler.status()["available"]:
        pytest.skip("Native decompiler runtime unavailable")
    image, _ = _image()
    path = tmp_path / "Image"
    path.write_bytes(image)
    analysis = binary_core.analyze_file(path)
    address = binary_core.RAW_ARM64_BASE + 0x40
    code = native_decompiler.decompile(str(path), analysis, address)["pseudocode"]
    assert "return 0x100100;" in code

    # LDR X0,[X0,#24]; MOV W8,#1; STR W8,[X0,#32]; MOV W0,#0; RET
    struct.pack_into("<5I", image, 0x40, 0xf9400c00, 0x52800028, 0xb9002008, 0x52800000, 0xd65f03c0)
    path.write_bytes(image)
    analysis = binary_core.analyze_file(path)
    code = native_decompiler.decompile(str(path), analysis, address)["pseudocode"]
    btf = kernel_btf.BTF(_btf(), 8, "little")
    recovered, count = binary_semantics.rewrite(code, btf, btf.prototype("rak_example"))
    assert count == 1
    assert recovered.startswith("int FUN_100040(struct rak_mixer *mixer)")
    assert "mixer->device->state = 1;" in recovered
