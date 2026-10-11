# SPDX-License-Identifier: MPL-2.0
import os
import sys
import platform


def make_symlink_bytes(target: str) -> bytes:

    header = b"!<symlink>"
    bom = b"\xff\xfe"
    encoded = target.encode("utf-16-le")
    terminator = b"\x00\x00"
    return header + bom + encoded + terminator


def make_symlink(output_path: str, target: str, overwrite: bool = False) -> bool:

    output_path = os.path.normpath(output_path)

    if os.path.exists(output_path) and not overwrite:
        print(f"[SKIP] File sudah ada: {output_path} (gunakan overwrite=True)")
        return False

    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)

    data = make_symlink_bytes(target)

    with open(output_path, "wb") as f:
        f.write(data)

    # set FILE_ATTRIBUTE_SYSTEM supaya konsisten sama file asli Android
    if platform.system() == "Windows":
        import subprocess
        _no_window = 0x08000000
        subprocess.run(
            ["attrib", "+S", output_path],
            check=False, capture_output=True, creationflags=_no_window
        )

    print(f"[OK] {output_path} -> {target}  ({len(data)} bytes)")
    return True


def make_symlinks_bulk(symlinks: list[tuple[str, str]], base_dir: str = ".", overwrite: bool = False):

    ok = 0
    skip = 0

    for name, target in symlinks:
        out = os.path.join(base_dir, name)
        if make_symlink(out, target, overwrite=overwrite):
            ok += 1
        else:
            skip += 1

    print(f"\nDone: {ok} dibuat, {skip} diskip.")


ANDROID_ROOT_SYMLINKS = [
    ("bin",        "/system/bin"),
    ("etc",        "/system/etc"),
    ("init",       "/system/bin/init"),
    ("d",          "/sys/kernel/debug"),
    ("product",    "/product"),
    ("system_ext", "/system_ext"),
    ("vendor",     "/vendor"),
]

TOYBOX_SYMLINKS = [
    # tambah command toybox lain sesuai kebutuhan
    ("acpi",       "toybox_vendor"),
    ("base64",     "toybox_vendor"),
    ("basename",   "toybox_vendor"),
    ("cat",        "toybox_vendor"),
    ("chmod",      "toybox_vendor"),
    ("chown",      "toybox_vendor"),
    ("cp",         "toybox_vendor"),
    ("date",       "toybox_vendor"),
    ("df",         "toybox_vendor"),
    ("du",         "toybox_vendor"),
    ("echo",       "toybox_vendor"),
    ("find",       "toybox_vendor"),
    ("grep",       "toybox_vendor"),
    ("id",         "toybox_vendor"),
    ("kill",       "toybox_vendor"),
    ("ln",         "toybox_vendor"),
    ("ls",         "toybox_vendor"),
    ("mkdir",      "toybox_vendor"),
    ("mount",      "toybox_vendor"),
    ("mv",         "toybox_vendor"),
    ("ps",         "toybox_vendor"),
    ("pwd",        "toybox_vendor"),
    ("rm",         "toybox_vendor"),
    ("rmdir",      "toybox_vendor"),
    ("sleep",      "toybox_vendor"),
    ("sort",       "toybox_vendor"),
    ("stat",       "toybox_vendor"),
    ("tail",       "toybox_vendor"),
    ("touch",      "toybox_vendor"),
    ("uname",      "toybox_vendor"),
    ("wc",         "toybox_vendor"),
]


USAGE = """
Usage:
    python make_symlink.py <target>
        -> nama file = basename target
        -> contoh: python make_symlink.py /product          => file: product -> /product

    python make_symlink.py <output_file> <target>
        -> eksplisit nama file + target
        -> contoh: python make_symlink.py product /product  => file: product -> /product

    python make_symlink.py <base_dir> <output_file> <target>
        -> output ke subfolder
        -> contoh: python make_symlink.py system/bin sh /system/bin/sh

    python make_symlink.py --preset-root
        -> bikin semua Android root symlinks di folder saat ini

    python make_symlink.py --preset-toybox
        -> bikin semua toybox symlinks di folder saat ini
"""


def _cli():
    args = sys.argv[1:]

    if not args:
        print(USAGE)
        sys.exit(1)

    # mode preset
    if len(args) == 1 and args[0] == "--preset-root":
        print("Membuat Android root symlinks di folder saat ini...\n")
        make_symlinks_bulk(ANDROID_ROOT_SYMLINKS, base_dir=".", overwrite=True)
        return

    if len(args) == 1 and args[0] == "--preset-toybox":
        print("Membuat toybox symlinks di folder saat ini...\n")
        make_symlinks_bulk(TOYBOX_SYMLINKS, base_dir=".", overwrite=True)
        return

    # mode 1 argumen: target saja, nama file = basename target
    if len(args) == 1:
        target = args[0]
        output_file = os.path.basename(target.strip("/").strip("\\"))
        if not output_file:
            print(f"[ERROR] Tidak bisa ambil nama file dari target: {target}")
            sys.exit(1)
        make_symlink(output_file, target, overwrite=True)
        return

    # mode 2 argumen: output_file + target
    if len(args) == 2:
        output_file, target = args
        make_symlink(output_file, target, overwrite=True)
        return

    # mode 3 argumen: base_dir + output_file + target
    if len(args) == 3:
        base_dir, output_file, target = args
        make_symlink(os.path.join(base_dir, output_file), target, overwrite=True)
        return

    print(USAGE)
    sys.exit(1)


if __name__ == "__main__":
    _cli()
