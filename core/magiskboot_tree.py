#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
#
# magiskboot_tree — unpack/repack boot & vendor_boot images as editable folder
# trees, using the built-in Rust magiskboot native library (via rak_native /
# PyO3) instead of an external magiskboot.exe binary.
#
# External callers that previously called find_magiskboot() / run() can now
# call the same high-level helpers (decode_ramdisk, encode_ramdisk, …) without
# supplying a magiskboot path; the native backend is resolved automatically.

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path

try:
    from core import native_backend as _nb
    _NATIVE = True
except Exception:
    _NATIVE = False


# ── Constants (unchanged public API) ─────────────────────────────────────────

MAGISKBOOT_NAME = "magiskboot.exe"

META_NAME = "magiskboot_tree.json"
SOURCE_IMAGE = "source.img"
RAMDISK_COMPONENT = "ramdisk.cpio"
RAMDISK_DECODED = "ramdisk.dec"
RAMDISK_DIR = "ramdisk"
CPIO_CONFIG = "cpio"


# ── Utilities ─────────────────────────────────────────────────────────────────

def set_hidden(path: Path) -> None:
    if os.name != "nt":
        return
    try:
        import ctypes
        FILE_ATTRIBUTE_HIDDEN = 0x02
        ctypes.windll.kernel32.SetFileAttributesW(str(path), FILE_ATTRIBUTE_HIDDEN)
    except Exception:
        pass


class ToolError(RuntimeError):
    pass


def _rm_readonly(func, path, _exc):
    import stat
    os.chmod(path, stat.S_IWRITE)
    func(path)


def die(message: str, code: int = 1) -> None:
    print(f"error: {message}", file=sys.stderr)
    raise SystemExit(code)


def find_magiskboot(explicit: str | None) -> Path:
    """Compatibility shim — returns a sentinel Path; callers should prefer
    the native functions below.  Raises if an explicit non-existent path is
    given; otherwise returns a placeholder that native functions ignore.
    """
    if explicit:
        path = Path(explicit)
        if path.is_file():
            return path
        die(f"magiskboot not found at --magiskboot path: {explicit}")
    # Native backend is always available; return a sentinel so old callers
    # that thread this value through still compile.
    return Path(MAGISKBOOT_NAME)


def _native_unpack(image: Path, out_dir: Path, dump_hdr: bool = True) -> dict:
    if not _NATIVE:
        raise ToolError("rak_native not available; cannot unpack boot image natively")
    result = _nb.boot_unpack(str(image), str(out_dir), skip_decomp=False, dump_hdr=dump_hdr)
    return result


def _native_repack(meta_dir: Path, orig_img: Path, out_img: Path) -> dict:
    if not _NATIVE:
        raise ToolError("rak_native not available; cannot repack boot image natively")
    return _nb.boot_repack(str(meta_dir), str(orig_img), str(out_img))


def _native_decompress(src: Path, dst: Path) -> str:
    """Decompress src → dst using the native backend. Returns format name."""
    if not _NATIVE:
        raise ToolError("rak_native not available; cannot decompress natively")
    result = _nb.boot_decompress(str(src), str(dst))
    return result.get("format", "unknown")


def _native_compress(src: Path, dst: Path, fmt: str) -> None:
    """Compress src → dst using native backend."""
    if not _NATIVE:
        raise ToolError("rak_native not available; cannot compress natively")
    _nb.boot_compress(str(src), str(dst), fmt)


def _native_cpio(cpio: Path, commands: list[str]) -> int:
    """Run cpio sub-commands via native backend. Returns exit code."""
    if not _NATIVE:
        raise ToolError("rak_native not available; cannot run cpio natively")
    result = _nb.boot_cpio(str(cpio), commands)
    return result.get("exit_code", 1)


def run(cmd: list, cwd: Path, verbose: bool = False) -> str:
    """Compatibility shim for callers that still invoke run() directly.

    Intercepts known magiskboot sub-commands and routes them to the native
    backend.  Falls through to the original subprocess implementation only
    for unrecognised commands (should never happen in practice).
    """
    # Strip the leading binary path; extract sub-command.
    args = [str(a) for a in cmd]
    # args[0] is the magiskboot binary path (now ignored); args[1] is the action.
    if len(args) < 2:
        raise ToolError(f"run(): too few arguments: {args!r}")
    action = args[1]

    if action == "unpack":
        flags = args[2:-1]
        image = args[-1]
        skip = "-n" in flags
        hdr = "-h" in flags
        out_dir = cwd
        _native_unpack(Path(image) if os.path.isabs(image) else cwd / image,
                       out_dir, dump_hdr=hdr)
        return ""

    if action == "repack":
        orig = args[2]
        out = args[3] if len(args) > 3 else str(cwd / "new-boot.img")
        _native_repack(cwd, cwd / orig, Path(out) if os.path.isabs(out) else cwd / out)
        return ""

    if action == "decompress" and len(args) >= 4:
        src = cwd / args[2]
        dst = cwd / args[3]
        fmt = _native_decompress(src, dst)
        return f"Detected format: [{fmt}]"

    if action.startswith("compress") and len(args) >= 4:
        fmt = action.partition("=")[2] or "gzip"
        src = cwd / args[2]
        dst = cwd / args[3]
        _native_compress(src, dst, fmt)
        return ""

    if action == "cpio" and len(args) >= 3:
        cpio_name = args[2]
        cmds = args[3:]
        cpio_path = cwd / cpio_name
        _native_cpio(cpio_path, cmds)
        return ""

    raise ToolError(f"run(): unrecognised magiskboot command: {action!r}")


# ── Core helpers ──────────────────────────────────────────────────────────────

def looks_like_cpio(path: Path) -> bool:
    try:
        magic = path.read_bytes()[:6]
    except FileNotFoundError:
        return False
    return magic in (b"070701", b"070702")


def clean_tree_dir(path: Path) -> None:
    if not path.exists():
        return
    if not path.is_dir():
        die(f"{path} exists and is not a directory")
    for child in path.iterdir():
        if child.is_dir():
            shutil.rmtree(child, onerror=_rm_readonly)
        else:
            child.unlink()


def write_meta(tree: Path, meta: dict) -> None:
    meta["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    (tree / META_NAME).write_text(
        json.dumps(meta, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def read_meta(tree: Path) -> dict:
    path = tree / META_NAME
    if not path.is_file():
        die(f"{META_NAME} not found in {tree}; run unpack first")
    return json.loads(path.read_text(encoding="utf-8"))


def decompress_ramdisk_component(component: Path, decoded: Path,
                                 magiskboot: Path, verbose: bool = False) -> str:
    """Decompress the ramdisk component, returning the compression format name."""
    if looks_like_cpio(component):
        shutil.copy2(component, decoded)
        return "raw"
    return _native_decompress(component, decoded)


def decode_ramdisk(tree: Path, magiskboot: Path, verbose: bool) -> tuple[str, bool]:
    component = tree / RAMDISK_COMPONENT
    decoded = tree / RAMDISK_DECODED
    if not component.is_file():
        return "none", False

    compression = decompress_ramdisk_component(component, decoded, magiskboot, verbose)
    if compression == "unknown":
        print("warning: could not parse ramdisk compression; repack will reuse raw copy")

    old_ramdisk = tree / RAMDISK_DIR
    if old_ramdisk.exists():
        shutil.rmtree(old_ramdisk, onerror=_rm_readonly)
    cpio_config = tree / "cpio"
    if cpio_config.exists():
        cpio_config.unlink()
    # Extract the cpio natively.
    _native_cpio(decoded, ["extract"])
    return compression, True


def encode_ramdisk(tree: Path, magiskboot: Path, compression: str, verbose: bool) -> None:
    decoded = tree / RAMDISK_DECODED
    ramdisk_dir = tree / RAMDISK_DIR
    component = tree / RAMDISK_COMPONENT
    if not decoded.exists() or not ramdisk_dir.exists():
        return

    _native_cpio(decoded, ["sync"])

    backup = tree / f"{RAMDISK_COMPONENT}.before_last_repack"
    if component.exists():
        shutil.copy2(component, backup)

    if compression in ("none", "raw", "unknown"):
        shutil.copy2(decoded, component)
    else:
        _native_compress(decoded, component, compression)


# ── CLI commands ──────────────────────────────────────────────────────────────

def command_unpack(args: argparse.Namespace) -> None:
    image = Path(args.image).resolve()
    if not image.is_file():
        die(f"image not found: {image}")
    magiskboot = find_magiskboot(args.magiskboot)

    tree = Path(args.output).resolve() if args.output else image.with_suffix("").with_name(image.stem + "_tree")
    if tree.exists() and not tree.is_dir():
        die(f"{tree} exists and is not a directory")
    if tree.exists() and any(tree.iterdir()) and not args.force:
        die(f"{tree} already has files; pass --force to replace it")
    tree.mkdir(parents=True, exist_ok=True)
    if args.force:
        clean_tree_dir(tree)

    shutil.copy2(image, tree / SOURCE_IMAGE)
    # Use native unpack (runs in-process, no subprocess).
    result = _native_unpack(tree / SOURCE_IMAGE, tree, dump_hdr=True)
    compression, has_ramdisk = decode_ramdisk(tree, magiskboot, args.verbose)

    meta = {
        "tool": "magiskboot_tree.py",
        "source_image": str(image),
        "source_copy": SOURCE_IMAGE,
        "magiskboot": "native",
        "has_ramdisk": has_ramdisk,
        "ramdisk_component": RAMDISK_COMPONENT,
        "ramdisk_decoded": RAMDISK_DECODED,
        "ramdisk_dir": RAMDISK_DIR,
        "ramdisk_compression": compression,
    }
    write_meta(tree, meta)

    print("")
    print(f"tree: {tree}")
    print(f"ramdisk: {compression}")
    print(f"edit: {tree / RAMDISK_DIR}")


def resolve_output_path(tree: Path, args: argparse.Namespace) -> tuple[Path, bool]:
    source = tree / args.source
    if not source.is_file():
        die(f"source image copy not found: {source}")

    if args.in_place:
        if args.output:
            die("--in-place cannot be combined with --output")
        return source, True

    if args.output:
        return Path(args.output).resolve(), False

    return tree / (source.stem + ".repacked" + source.suffix), False


def command_repack(args: argparse.Namespace) -> None:
    tree = Path(args.tree).resolve()
    if not tree.is_dir():
        die(f"tree not found: {tree}")
    meta = read_meta(tree)
    magiskboot = find_magiskboot(args.magiskboot or None)

    compression = args.ramdisk_format or meta.get("ramdisk_compression", "raw")
    encode_ramdisk(tree, magiskboot, compression, args.verbose)

    output, in_place = resolve_output_path(tree, args)
    output.parent.mkdir(parents=True, exist_ok=True)
    source = tree / args.source

    if output.exists() and not args.force and not in_place:
        die(f"{output} exists; pass --force to overwrite it")

    if in_place or output.resolve() == source.resolve():
        temp_output = tree / (source.name + ".new")
        if temp_output.exists():
            temp_output.unlink()
        _native_repack(tree, source, temp_output)
        backup = tree / (source.name + ".bak")
        if not backup.exists():
            shutil.copy2(source, backup)
        os.replace(temp_output, source)
        final_output = source
    else:
        _native_repack(tree, source, output)
        final_output = output

    meta["ramdisk_compression"] = compression
    meta["magiskboot"] = "native"
    write_meta(tree, meta)

    print("")
    print(f"image: {final_output}")
    if final_output.exists():
        print(f"size: {final_output.stat().st_size}")


def command_status(args: argparse.Namespace) -> None:
    tree = Path(args.tree).resolve()
    meta = read_meta(tree)
    print(json.dumps(meta, indent=2, sort_keys=True))
    header = tree / "header"
    if header.is_file():
        cmdline = [
            line.strip()
            for line in header.read_text(encoding="utf-8", errors="replace").splitlines()
            if line.startswith("cmdline=")
        ]
        if cmdline:
            print(cmdline[0])


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Unpack/repack boot or vendor_boot images as editable folder trees (native)."
    )
    parser.add_argument(
        "--magiskboot",
        help="Ignored — the native Rust backend is always used. Kept for CLI compatibility.",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Print verbose output.")

    sub = parser.add_subparsers(dest="cmd", required=True)

    unpack = sub.add_parser("unpack", help="Unpack image into editable folder tree.")
    unpack.add_argument("image", help="boot.img, vendor_boot.img, init_boot.img, etc.")
    unpack.add_argument("-o", "--output", help="Output tree directory.")
    unpack.add_argument("--force", action="store_true", help="Replace output tree if it exists.")
    unpack.set_defaults(func=command_unpack)

    repack = sub.add_parser("repack", help="Repack an editable folder tree into an image.")
    repack.add_argument("tree", help="Tree directory created by unpack.")
    repack.add_argument("-o", "--output", help="Output image path. Defaults to source.repacked.img in tree.")
    repack.add_argument("--force", action="store_true", help="Overwrite output image.")
    repack.add_argument(
        "--in-place",
        action="store_true",
        help="Replace tree/source.img and keep source.img.bak once.",
    )
    repack.add_argument(
        "--source",
        default=SOURCE_IMAGE,
        help=f"Source image copy inside the tree. Default: {SOURCE_IMAGE}",
    )
    repack.add_argument(
        "--ramdisk-format",
        help="Override ramdisk compression, e.g. raw, gzip, lz4_legacy, lz4, xz.",
    )
    repack.set_defaults(func=command_repack)

    status = sub.add_parser("status", help="Show tree metadata and cmdline.")
    status.add_argument("tree", help="Tree directory created by unpack.")
    status.set_defaults(func=command_status)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        args.func(args)
    except ToolError as exc:
        die(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

