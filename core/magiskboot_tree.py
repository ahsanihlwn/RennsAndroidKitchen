#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0


from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

try:
    from core.binary_handler import resolve_binary
except Exception:  # standalone run without the package on sys.path
    resolve_binary = None

try:
    from core.proc_util import CREATE_NO_WINDOW
except Exception:  # standalone run without the package on sys.path
    CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


# Same managed bin root the rest of the kitchen uses (see core/boot_image.py).
BIN_PATH = os.path.join(
    os.environ.get("LOCALAPPDATA", os.path.expanduser("~")),
    "RennsAndroidKitchen",
    "bin",
)
MAGISKBOOT_NAME = "magiskboot.exe"

META_NAME = "magiskboot_tree.json"
SOURCE_IMAGE = "source.img"
RAMDISK_COMPONENT = "ramdisk.cpio"
RAMDISK_DECODED = "ramdisk.dec"
RAMDISK_DIR = "ramdisk"
CPIO_CONFIG = "cpio"


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


    if explicit:
        path = Path(explicit)
        if path.is_file():
            return path
        die(f"magiskboot not found at --magiskboot path: {explicit}")

    if resolve_binary is None:
        die(
            "core.binary_handler unavailable; pass --magiskboot PATH explicitly."
        )

    resolved = resolve_binary(BIN_PATH, MAGISKBOOT_NAME)
    if resolved and Path(resolved).is_file():
        return Path(resolved)

    die(
        f"{MAGISKBOOT_NAME} not installed. Install it via the kitchen's bin "
        f"manager (expected under {BIN_PATH}), or pass --magiskboot PATH."
    )
    raise AssertionError("unreachable")


def run(cmd: list[str | Path], cwd: Path, verbose: bool = False) -> str:
    printable = " ".join(str(part) for part in cmd)
    print(f"> {printable}")
    proc = subprocess.run(
        [str(part) for part in cmd],
        cwd=str(cwd),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        creationflags=CREATE_NO_WINDOW,
    )
    out = proc.stdout or ""
    if verbose and out:
        print(out, end="" if out.endswith("\n") else "\n")
    if proc.returncode != 0:
        if out:
            print(out, file=sys.stderr, end="" if out.endswith("\n") else "\n")
        raise ToolError(f"command failed with exit {proc.returncode}: {printable}")
    return out


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


def decode_ramdisk(tree: Path, magiskboot: Path, verbose: bool) -> tuple[str, bool]:
    component = tree / RAMDISK_COMPONENT
    decoded = tree / RAMDISK_DECODED
    if not component.is_file():
        return "none", False

    if looks_like_cpio(component):
        shutil.copy2(component, decoded)
        compression = "raw"
    else:
        out = run([magiskboot, "decompress", RAMDISK_COMPONENT, RAMDISK_DECODED], tree, verbose)
        match = re.search(r"Detected format:\s*\[(.*?)\]", out)
        compression = match.group(1) if match else "unknown"
        if compression == "unknown":
            print("warning: could not parse ramdisk compression; repack will reuse raw copy")

    old_ramdisk = tree / RAMDISK_DIR
    if old_ramdisk.exists():
        shutil.rmtree(old_ramdisk, onerror=_rm_readonly)
    cpio_config = tree / "cpio"
    if cpio_config.exists():
        cpio_config.unlink()
    run([magiskboot, "cpio", RAMDISK_DECODED, "extract"], tree, verbose)
    return compression, True


def encode_ramdisk(tree: Path, magiskboot: Path, compression: str, verbose: bool) -> None:
    decoded = tree / RAMDISK_DECODED
    ramdisk_dir = tree / RAMDISK_DIR
    component = tree / RAMDISK_COMPONENT
    if not decoded.exists() or not ramdisk_dir.exists():
        return

    run([magiskboot, "cpio", RAMDISK_DECODED, "sync"], tree, verbose)

    backup = tree / f"{RAMDISK_COMPONENT}.before_last_repack"
    if component.exists():
        shutil.copy2(component, backup)

    if compression in ("none", "raw", "unknown"):
        shutil.copy2(decoded, component)
    else:
        run(
            [magiskboot, f"compress={compression}", RAMDISK_DECODED, RAMDISK_COMPONENT],
            tree,
            verbose,
        )


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
    run([magiskboot, "unpack", "-h", SOURCE_IMAGE], tree, args.verbose)
    compression, has_ramdisk = decode_ramdisk(tree, magiskboot, args.verbose)

    meta = {
        "tool": "magiskboot_tree.py",
        "source_image": str(image),
        "source_copy": SOURCE_IMAGE,
        "magiskboot": str(magiskboot),
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
    magiskboot = find_magiskboot(args.magiskboot or meta.get("magiskboot"))

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
        run([magiskboot, "repack", source.name, temp_output.name], tree, args.verbose)
        backup = tree / (source.name + ".bak")
        if not backup.exists():
            shutil.copy2(source, backup)
        os.replace(temp_output, source)
        final_output = source
    else:
        run([magiskboot, "repack", source.name, str(output)], tree, args.verbose)
        final_output = output

    meta["ramdisk_compression"] = compression
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
        description="Unpack/repack boot or vendor_boot images as editable folder trees."
    )
    parser.add_argument(
        "--magiskboot",
        help="Path to magiskboot.exe. Defaults to the kitchen's managed bin (package.json).",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Print full magiskboot output.")

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
