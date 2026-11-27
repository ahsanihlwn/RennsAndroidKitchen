# SPDX-License-Identifier: MPL-2.0
"""Comprehensive maturity tests for kernel decompression, blind metadata recovery,
BTF local-alias semantics, Rust FFI export, and GUI download confirmation."""
from __future__ import annotations

import gzip
import json
import struct
from types import SimpleNamespace

import pytest

from core import (
    binary_core,
    binary_reconstruct,
    binary_semantics,
    kernel_btf,
    kernel_symbols,
    kernel_version,
    kernel_workspace,
)


BASE = 0xFFFFFFC008000000


def _build_token_tables(tokens_list: list[bytes]) -> tuple[bytes, bytes]:
    padded = list(tokens_list) + [f"t{i}".encode("ascii") for i in range(256 - len(tokens_list))]
    # Ensure standard '0'..'9' sequence exists so blind locator's fast signature hits
    for digit in range(10):
        padded[48 + digit] = str(digit).encode("ascii")
    table = bytearray()
    index = bytearray()
    for tok in padded:
        index.extend(struct.pack("<H", len(table)))
        table.extend(tok + b"\0")
    return bytes(table), bytes(index)


def _encode_symbol(kind: str, name: str, token_map: dict[bytes, int]) -> bytes:
    seq = [token_map[kind.encode("ascii")]]
    for ch in name.encode("ascii"):
        seq.append(token_map[bytes([ch])])
    assert len(seq) < 128
    return bytes([len(seq), *seq])


def _ascii_token_set() -> tuple[bytes, bytes, dict[bytes, int]]:
    # Single-char printable tokens plus multi-char tokens at high slots so vals[255] > 520
    tokens = []
    token_map = {}
    for i in range(240):
        ch = chr(33 + (i % 90)).encode("ascii")
        if i < 90:
            tokens.append(ch)
            token_map[ch] = i
        else:
            tokens.append(f"sym_{i:02d}".encode("ascii"))
    for i in range(240, 256):
        tokens.append(f"kernel_tok_{i}".encode("ascii"))
    # Ensure exact '0'..'9' consecutive run
    base_slot = 100
    for d in range(10):
        b = str(d).encode("ascii")
        tokens[base_slot + d] = b
        token_map[b] = base_slot + d
    table = bytearray()
    index = bytearray()
    for tok in tokens:
        index.extend(struct.pack("<H", len(table)))
        table.extend(tok + b"\0")
    return bytes(table), bytes(index), token_map


def test_gzip_kernel_with_appended_dtb_decompresses_and_inspects(tmp_path):
    raw_img = bytearray(4096)
    raw_img[56:60] = b"ARM\x64"
    banner = b"Linux version 4.14.141-perf-g54df45d (builder@miui) (clang version 9.0.3) #1 SMP\n\0"
    raw_img[256 : 256 + len(banner)] = banner
    src_path = b"arch/arm64/kernel/cpu_errata.c\0"
    raw_img[512 : 512 + len(src_path)] = src_path

    # Append a real Flattened Device Tree magic trailer (0xd00dfeed) after gzip stream
    dtb_trailer = b"\xd0\x0d\xfe\xed" + bytes(256)
    gz_dtb = gzip.compress(bytes(raw_img)) + dtb_trailer

    header = bytearray(4096)
    header[:8] = b"ANDROID!"
    struct.pack_into("<I", header, 8, len(gz_dtb))
    struct.pack_into("<I", header, 40, 2)
    struct.pack_into("<I", header, 36, 4096)
    boot = tmp_path / "boot_gz_dtb.img"
    boot.write_bytes(header + gz_dtb)

    info = kernel_version.inspect_kernel(boot)
    assert info.is_kernel is True
    assert info.error == ""
    assert info.base_version == "4.14.141"
    assert info.git_commit == "54df45d"
    assert info.estimated_source_files >= 1


@pytest.mark.parametrize("modern_6_4_order", [False, True])
def test_blind_kallsyms_recovers_pre_and_post_6_4_layouts(tmp_path, modern_6_4_order):
    tok_table, tok_index, tok_map = _ascii_token_set()
    syms = [
        (0x0, "T", "_text"),
        (0x40, "T", "rak_driver_init"),
    ]
    names = b"".join(_encode_symbol(k, n, tok_map) for _, k, n in syms)
    names_padded = names + b"\0" * ((4 - len(names) % 4) % 4)
    markers = struct.pack("<I", 0)
    tok_padded = tok_table + b"\0" * ((4 - len(tok_table) % 4) % 4)
    offsets = struct.pack("<2I", 0x0, 0x40)
    rel_base = struct.pack("<Q", BASE)
    num_syms = struct.pack("<II", len(syms), 0)

    image = bytearray(0x400)
    image[56:60] = b"ARM\x64"
    struct.pack_into("<I", image, 0x40, 0xD65F03C0)  # RET
    if not modern_6_4_order:
        image.extend(offsets + rel_base + num_syms + names_padded + markers + tok_padded + tok_index)
    else:
        image.extend(num_syms + names_padded + markers + tok_padded + tok_index + offsets + rel_base)

    path = tmp_path / "Image"
    path.write_bytes(image)
    analysis = binary_core.analyze_file(path)
    recovered = kernel_symbols.recover_blind(str(path), analysis)
    assert recovered == [
        kernel_symbols.Symbol(BASE, "T", "_text"),
        kernel_symbols.Symbol(BASE + 0x40, "T", "rak_driver_init"),
    ]


@pytest.mark.parametrize("use_rela", [False, True])
def test_blind_kallsyms_recovers_legacy_64bit_markers_and_rela(tmp_path, use_rela):
    tok_table, tok_index, tok_map = _ascii_token_set()
    kbase = 0xFFFFFF8008080000
    syms = [
        (kbase, "T", "_text"),
        (kbase + 0x80, "T", "legacy_mtk_probe"),
    ]
    names = b"".join(_encode_symbol(k, n, tok_map) for _, k, n in syms)

    def align256(buf: bytearray):
        rem = len(buf) % 0x100
        if rem:
            buf.extend(b"\0" * (0x100 - rem))

    image = bytearray(0x400)
    image[56:60] = b"ARM\x64"
    struct.pack_into("<I", image, 0x80, 0xD65F03C0)
    align256(image)
    addr_off = len(image)
    if use_rela:
        image.extend(b"\0" * (len(syms) * 8))
    else:
        for addr, _, _ in syms:
            image.extend(struct.pack("<Q", addr))
    align256(image)
    image.extend(struct.pack("<Q", len(syms)))
    align256(image)
    image.extend(names)
    align256(image)
    image.extend(struct.pack("<Q", 0))  # 64-bit marker (Linux <= 4.19)
    align256(image)
    image.extend(tok_table)
    align256(image)
    image.extend(tok_index)
    if use_rela:
        align256(image)
        # Emit >= len(syms) + 2 R_AARCH64_RELATIVE (0x403) Elf64_Rela records
        for idx, (addr, _, _) in enumerate(syms):
            image.extend(struct.pack("<QQQ", kbase + addr_off + idx * 8, 0x403, addr))
        image.extend(struct.pack("<QQQ", kbase + addr_off + 0x1000, 0x403, kbase))
        image.extend(struct.pack("<QQQ", kbase + addr_off + 0x1008, 0x403, kbase))

    path = tmp_path / "Image"
    path.write_bytes(image)
    analysis = binary_core.analyze_file(path)
    recovered = kernel_symbols.recover_blind(str(path), analysis)
    assert recovered == [
        kernel_symbols.Symbol(kbase, "T", "_text"),
        kernel_symbols.Symbol(kbase + 0x80, "T", "legacy_mtk_probe"),
    ]


def test_blind_bug_table_and_embedded_btf_without_system_map(tmp_path):
    from tests.test_binary_reconstruction_metadata import _btf

    image = bytearray(0x2000)
    image[56:60] = b"ARM\x64"
    src = b"drivers/usb/core/hub.c\0"
    image[0x200 : 0x200 + len(src)] = src
    # Place 16 BRK #0x800 instructions and 16 contiguous 12-byte bug_entry records
    table_off = 0x800
    for i in range(16):
        code_off = 0x40 + i * 4
        struct.pack_into("<I", image, code_off, 0xD4210000)
        entry_off = table_off + i * 12
        struct.pack_into("<iiHH", image, entry_off, code_off - entry_off, 0x200 - entry_off, 100 + i, 0)
    btf_blob = _btf()
    image.extend(btf_blob)

    path = tmp_path / "Image"
    path.write_bytes(image)
    analysis = binary_core.analyze_file(path)
    metadata = binary_reconstruct._load_metadata(str(path), analysis, None, None)
    assert metadata.sources.get("btf") == "embedded Image scan"
    assert metadata.btf is not None
    assert metadata.bug_table is not None
    refs, _ = binary_reconstruct._kernel_bug_references(str(path), analysis, metadata.bug_table)
    assert "drivers/usb/core/hub.c" in refs
    assert refs["drivers/usb/core/hub.c"][0]["kind"] == "BUG_TABLE:L100"


def test_warn_slowpath_call_site_attributes_file_and_line(tmp_path):
    image = bytearray(0x1000)
    image[56:60] = b"ARM\x64"
    src = b"arch/arm64/kernel/cpu_errata.c\0"
    image[0x300 : 0x300 + len(src)] = src
    # Function at 0x40: ADR X0, #0x2c0 (0x40 + 0x2c0 = 0x300); MOVZ W1, #321; BL 0x80 (warn_slowpath_null); RET
    adr_x0 = 0x10000000 | (((0x2C0 & 0x3) << 29) | (((0x2C0 >> 2) & 0x7FFFF) << 5))
    movz_w1_321 = 0x52800001 | (321 << 5)
    bl_to_80 = 0x94000000 | ((0x80 - 0x48) >> 2)
    struct.pack_into("<4I", image, 0x40, adr_x0, movz_w1_321, bl_to_80, 0xD65F03C0)
    struct.pack_into("<I", image, 0x80, 0xD65F03C0)

    path = tmp_path / "Image"
    path.write_bytes(image)
    analysis = binary_core.analyze_file(path)
    base = binary_core.RAW_ARM64_BASE
    analysis["addr_symbol_map"][base + 0x80] = "warn_slowpath_null"
    xrefs = [{"from": hex(base + 0x40), "to": hex(base + 0x300), "kind": "ADR"}]
    binary_reconstruct._annotate_arm64_warn_xrefs(str(path), analysis, xrefs)
    assert xrefs[0]["kind"] == "WARN_SITE:L321"
    assert xrefs[0]["line"] == 321


def test_semantics_rewrites_void_ptr_and_immutable_local_aliases():
    from tests.test_binary_reconstruction_metadata import _btf

    btf = kernel_btf.BTF(_btf(), 8, "little")
    proto = btf.prototype("rak_example")

    code = (
        "int rak_example(void *param_1) {\n"
        "  longlong lVar1;\n"
        "  lVar1 = *(longlong *)(param_1 + 0x18);\n"
        "  *(int *)(lVar1 + 0x20) = 1;\n"
        "  return 0;\n"
        "}"
    )
    rewritten, count = binary_semantics.rewrite(code, btf, proto)
    assert count == 2
    assert "struct rak_mixer * mixer" in rewritten or "struct rak_mixer *mixer" in rewritten
    assert "struct rak_device *lVar1;" in rewritten
    assert "lVar1 = mixer->device;" in rewritten
    assert "lVar1->state = 1;" in rewritten

    # Mutated local variable must NOT be rewritten
    mutated = code.replace("*(int *)(lVar1 + 0x20) = 1;", "lVar1 += 8; *(int *)(lVar1 + 0x20) = 1;")
    rewritten_mut, count_mut = binary_semantics.rewrite(mutated, btf, proto)
    assert "->state" not in rewritten_mut
    assert count_mut == 1  # Only param_1 -> mixer->device rewrites, not mutated lVar1!


def test_rust_ffi_skeleton_generates_no_std_extern_c_bindings():
    lines = binary_reconstruct._rust_ffi_skeleton(
        ["int rak_example(struct rak_mixer * mixer);", "static void helper(const int * count);"],
        None,
        [],
    )
    joined = "\n".join(lines)
    assert "#![no_std]" in joined
    assert "pub struct rak_mixer" in joined
    assert 'unsafe extern "C"' in joined
    assert "pub fn rak_example(mixer: *mut rak_mixer) -> core::ffi::c_int;" in joined
    assert "pub fn helper(count: *const core::ffi::c_int);" in joined


def test_download_confirmation_popup_ok_and_cancel_flow(tmp_path, monkeypatch):
    from ui import workspace_tools

    raw_img = bytearray(4096)
    raw_img[56:60] = b"ARM\x64"
    banner = b"Linux version 6.12.38-android16-5-gb575a0b6e647-ab14355190-4k (kleaf@host) (clang 19) #1\n\0"
    raw_img[256 : 256 + len(banner)] = banner
    for i in range(25):
        s = f"drivers/test/mod_{i}.c\0".encode("ascii")
        raw_img[512 + i * 32 : 512 + i * 32 + len(s)] = s
    kfile = tmp_path / "Image"
    kfile.write_bytes(raw_img)

    fake_release = SimpleNamespace(
        build_id="14355190",
        tag="android16-6.12-2025-09_r8",
        commit="b575a0b6e6473964000000000000000000000000",
        branch="android16-6.12",
        kernel_version="6.12.38",
        display_name="android16-6.12-2025-09_r8",
    )
    monkeypatch.setattr("core.gki_checkout.resolve_release", lambda info: (fake_release, ""))
    monkeypatch.setattr(kernel_workspace, "_has_linux_git", lambda distro: True)
    monkeypatch.setattr("core.gki_checkout.cache_checkout", lambda *a, **kw: {})

    plan = kernel_workspace.inspect_download_plan(kfile)
    assert plan["needs_download"] is True
    assert plan["is_gki"] is True
    prompt = kernel_workspace.format_download_prompt(plan)
    assert "ab14355190" in prompt
    assert "android16-6.12" in prompt
    assert "MB" in prompt
    assert "With Source Reference" in prompt
    assert "Offline Decompile" in prompt

    # Verify UI mode selector dialog captures Download vs Offline vs Cancel properly
    captured = {}
    dispatched = []

    class _Signal:
        @staticmethod
        def emit(*args):
            if len(args) >= 2 and args[0] == "actionDialog":
                captured.update(args[1])

    class FakeSession:
        _closing = False
        _operation_serial = 0
        _history = []
        directories = []
        active_tree = SimpleNamespace(base=str(tmp_path))
        statusChanged = SimpleNamespace(emit=lambda: None)
        presentationRequested = _Signal()

        class _tasks:
            @staticmethod
            def enqueue(label, worker, finished, **kwargs):
                res = worker()
                finished(True, res)

    monkeypatch.setattr(
        workspace_tools,
        "WorkspaceFiles",
        lambda base: SimpleNamespace(
            base=base,
            view=SimpleNamespace(resolve=lambda p: (p, None), manager=SimpleNamespace(is_physical_checkout=lambda r: True)),
            branch=lambda p: (p, "Image", base, ""),
        ),
    )
    orig_dispatch = workspace_tools.dispatch_tool

    def spy_dispatch(sess, act, pth, dat):
        if dat.get("download_confirmed"):
            dispatched.append((act, pth, dat))
            return
        orig_dispatch(sess, act, pth, dat)

    monkeypatch.setattr(workspace_tools, "dispatch_tool", spy_dispatch)
    sess = FakeSession()
    spy_dispatch(sess, "decompile_kernel", str(kfile), {})

    # Choice dialog must be shown first; nothing decompiled until user submits a mode
    assert dispatched == []
    assert captured["title"] == "Kernel Source Comparison & Decompile Mode"
    assert captured["accept"] == "Decompile"
    assert "ab14355190" in captured["description"]
    choices = captured["fields"][0]["choices"]
    assert [c["key"] for c in choices] == ["download", "offline"]

    # Selecting "download" enables source fetching (offline=False)
    token = captured["token"]
    sess._forms[token]({"mode": "download"})
    assert len(dispatched) == 1
    assert dispatched[0][2]["download_confirmed"] is True
    assert dispatched[0][2]["offline"] is False

    # Selecting "offline" disables network source fetching (offline=True)
    captured.clear()
    spy_dispatch(sess, "decompile_kernel", str(kfile), {})
    sess._forms[captured["token"]]({"mode": "offline"})
    assert len(dispatched) == 2
    assert dispatched[1][2]["download_confirmed"] is True
    assert dispatched[1][2]["offline"] is True

    # Inspecting download plan with use_wsl=False avoids WSL Git; use_wsl=True selects WSL Git
    assert kernel_workspace.inspect_download_plan(kfile, use_wsl=False)["use_wsl"] is False
    assert kernel_workspace.inspect_download_plan(kfile, use_wsl=True)["use_wsl"] is True
    assert kernel_workspace.inspect_download_plan(kfile, use_wsl=True)["mode"] == "gki_git"

    # Dispatching decompile_kernel_wsl sets use_wsl=True cleanly
    captured.clear()
    spy_dispatch(sess, "decompile_kernel_wsl", str(kfile), {})
    assert "WSL" in captured["description"]
    sess._forms[captured["token"]]({"mode": "download"})
    assert dispatched[2][0] == "decompile_kernel"
    assert dispatched[2][2]["use_wsl"] is True

    # Non-kernel binary via binary_reconstruct_tree skips dialog completely
    non_kernel = tmp_path / "libsample.so"
    non_kernel.write_bytes(b"\x7fELF" + bytes(256))
    captured.clear()
    spy_dispatch(sess, "binary_reconstruct_tree", str(non_kernel), {})
    assert captured == {}
    assert len(dispatched) == 4
    assert dispatched[3][0] == "binary_reconstruct_tree"
    assert dispatched[3][2]["download_confirmed"] is True


def test_wsl_decompile_menu_visibility_follows_wsl_detection(tmp_path, monkeypatch):
    from core import binary_handler, wsl_manager
    from ui import workspace_tools

    boot = tmp_path / "boot.img"
    boot.write_bytes(b"ANDROID!" + bytes(256))
    kern = tmp_path / "Image"
    kern.write_bytes(bytearray(256))

    class FakeNode:
        def __init__(self, badge):
            self._badge = badge
            self.folder = False

        def data(self, col, role):
            if role == 257:
                return self._badge
            return None

    class FakeSession:
        def __init__(self, badge_map):
            self.active_tree = SimpleNamespace(
                base=str(tmp_path),
                _nodes=badge_map,
                engine=None,
            )

    monkeypatch.setattr(binary_handler, "is_available", lambda *a: True)

    # Without WSL: no WSL decompile entries appear
    monkeypatch.setattr(wsl_manager, "has_wsl_cached", lambda: False)
    boot_keys_no_wsl = [r.get("key") for r in workspace_tools.context_tools(FakeSession({str(boot): FakeNode("BOOT")}), str(boot))]
    kern_keys_no_wsl = [r.get("key") for r in workspace_tools.context_tools(FakeSession({str(kern): FakeNode("")}), str(kern))]
    assert "unpack_boot_decompile" in boot_keys_no_wsl
    assert "unpack_boot_decompile_wsl" not in boot_keys_no_wsl
    assert "decompile_kernel" in kern_keys_no_wsl
    assert "decompile_kernel_wsl" not in kern_keys_no_wsl

    # With WSL detected: WSL decompile options automatically appear on Windows
    monkeypatch.setattr(wsl_manager, "has_wsl_cached", lambda: True)
    boot_keys_wsl = [r.get("key") for r in workspace_tools.context_tools(FakeSession({str(boot): FakeNode("BOOT")}), str(boot))]
    kern_keys_wsl = [r.get("key") for r in workspace_tools.context_tools(FakeSession({str(kern): FakeNode("")}), str(kern))]
    assert "unpack_boot_decompile_wsl" in boot_keys_wsl
    assert "decompile_kernel_wsl" in kern_keys_wsl


def test_arm64_image_origin_inference_without_text_symbol(tmp_path):
    kbase = 0xFFFFFF8008080000
    # Case 1: header word 0 branches directly to stext at offset 0x1350000 (Linux 4.14 relocatable)
    img1 = bytearray(0x200)
    struct.pack_into("<IIQ", img1, 0, 0x14000000 | (0x1350000 >> 2), 0, 0x80000)
    img1[56:60] = b"ARM\x64"
    p1 = tmp_path / "Image_4_14"
    p1.write_bytes(img1)
    syms1 = [
        kernel_symbols.Symbol(kbase + 0x800, "T", "_stext"),
        kernel_symbols.Symbol(kbase + 0x1350000, "T", "stext"),
    ]
    assert binary_reconstruct._infer_arm64_image_origin(str(p1), syms1) == kbase

    # Case 2: header branches to +0x40 (no stext in kallsyms), infer via _stext + text_offset (Linux 4.4)
    img2 = bytearray(0x200)
    struct.pack_into("<IIQ", img2, 0, 0x14000010, 0, 0x80000)
    img2[56:60] = b"ARM\x64"
    p2 = tmp_path / "Image_4_4"
    p2.write_bytes(img2)
    syms2 = [
        kernel_symbols.Symbol(kbase + 0x1000, "T", "do_undefinstr"),
        kernel_symbols.Symbol(kbase + 0x1000, "T", "_stext"),
    ]
    assert binary_reconstruct._infer_arm64_image_origin(str(p2), syms2) == kbase


def test_scan_xrefs_prefers_kallsyms_function_over_heuristic_prologue(tmp_path, monkeypatch):
    from core import binary_xref, native_backend

    img = tmp_path / "Image"
    raw = bytearray(0x400)
    raw[56:60] = b"ARM\x64"
    img.write_bytes(raw)
    analysis = binary_core.analyze_file(img)
    base = binary_core.RAW_ARM64_BASE
    # True kallsyms function starts at base + 0x100; xref instruction is at base + 0x180
    analysis["functions"] = [{"addr": base + 0x100, "size": 0, "name": "mtk_real_driver_probe", "demangled": ""}]
    analysis["_function_starts"] = [base + 0x100]
    analysis["addr_symbol_map"] = {base + 0x100: "mtk_real_driver_probe"}

    class FakeNative:
        @staticmethod
        def binary_fast_scan_xrefs_json(path, vas, abi, endian, max_hits, raw_base=None):
            return json.dumps([{
                "from": hex(base + 0x180),
                "to": hex(base + 0x300),
                "kind": "ADR",
                "section": ".image",
                # Wrong mid-function heuristic guess from prologue scan
                "function_address": hex(base + 0x140),
            }])

    monkeypatch.setattr(native_backend, "_get_native", lambda: FakeNative())
    xrefs = binary_xref.scan_xrefs(str(img), analysis, [base + 0x300])
    assert len(xrefs) == 1
    assert xrefs[0]["function"] == "mtk_real_driver_probe"
    assert xrefs[0]["function_address"] == hex(base + 0x100)


