# SPDX-License-Identifier: MPL-2.0
"""Build Ghidra's Apache-2.0 C++ decompiler and compile SLEIGH tables.

Output lands in build/ghidra_native/runtime, which core.native_decompiler
discovers. Nothing here executes inspected binaries.
"""
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "core" / "ghidra_native"
BUILD = ROOT / "build" / "ghidra_native"
RUNTIME = BUILD / "runtime"
EXE = ".exe" if os.name == "nt" else ""

# (folder in specs/, slaspec name inside the vendored Ghidra tree, output .sla)
# The .slaspec sources are fetched on demand from the Ghidra checkout given by
# --ghidra-src (only the small data/languages folders are needed).
TABLES = {
    "AARCH64": [("AARCH64.slaspec", "AARCH64.sla")],
    "x86": [("x86-64.slaspec", "x86-64.sla")],
    "ARM": [("ARM8_le.slaspec", "ARM8_le.sla")],
}


def vcvars():
    if os.name != "nt":
        return None
    roots = [os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"), os.environ.get("ProgramFiles", r"C:\Program Files")]
    for root in roots:
        for hit in Path(root, "Microsoft Visual Studio").glob("*/*/VC/Auxiliary/Build/vcvars64.bat"):
            return hit
    return None


def run(command, shell_prefix=None):
    if shell_prefix:
        command = f'"{shell_prefix}" >nul && ' + command
        return subprocess.run(command, shell=True, check=True)
    return subprocess.run(command, shell=True, check=True)


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ghidra-src", help="Ghidra checkout containing Ghidra/Processors/*/data/languages (needed to compile .sla tables)")
    parser.add_argument("--skip-tables", action="store_true")
    args = parser.parse_args(argv)

    BUILD.mkdir(parents=True, exist_ok=True)
    RUNTIME.mkdir(parents=True, exist_ok=True)
    prefix = vcvars()
    generator = "-G Ninja" if shutil.which("ninja") or (prefix and True) else ""
    configure = f'cmake -S "{SOURCE}" -B "{BUILD / "out"}" {generator} -DCMAKE_BUILD_TYPE=Release'
    build = f'cmake --build "{BUILD / "out"}" --target sleigh_comp decomp_cli'
    run(configure, prefix)
    run(build, prefix)
    for name in ("sleigh_comp", "decomp_cli"):
        built = BUILD / "out" / (name + EXE)
        shutil.copy2(built, RUNTIME / (name + EXE))

    for folder in ("AARCH64", "ARM", "x86"):
        target = RUNTIME / "specs" / folder
        target.mkdir(parents=True, exist_ok=True)
        for item in (SOURCE / "specs" / folder).iterdir():
            shutil.copy2(item, target / item.name)

    if not args.skip_tables:
        if not args.ghidra_src:
            print("Skipping .sla tables: pass --ghidra-src <Ghidra checkout>", file=sys.stderr)
            return 0
        for folder, tables in TABLES.items():
            languages = Path(args.ghidra_src) / "Ghidra" / "Processors" / folder / "data" / "languages"
            for spec, sla in tables:
                subprocess.run([str(RUNTIME / ("sleigh_comp" + EXE)), str(languages / spec), str(RUNTIME / "specs" / folder / sla)], check=True)
    print("Built", RUNTIME)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
