# SPDX-License-Identifier: MPL-2.0
"""Build RAK's cross-platform Rust terminal engine (termcore) shared library."""
from __future__ import annotations

import argparse
from pathlib import Path
import shutil
import subprocess
import sys

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.rust_toolchain import ensure_rust


def library_filename(platform: str | None = None) -> str:
    plat = sys.platform if platform is None else platform
    if plat == "win32":
        return "termcore.dll"
    if plat == "darwin":
        return "libtermcore.dylib"
    return "libtermcore.so"


def build_termcore(*, force: bool = False) -> Path:
    repo = Path(__file__).resolve().parents[1]
    crate = repo / "core" / "terminal" / "core"
    target_dir = repo / "build" / "termcore_runtime"
    filename = library_filename()
    output = repo / "core" / filename
    if not force and output.is_file():
        src_files = [crate / "Cargo.toml", crate / "Cargo.lock", *(crate / "src").rglob("*.rs")]
        newest_src = max((p.stat().st_mtime for p in src_files if p.is_file()), default=0.0)
        if output.stat().st_mtime >= newest_src:
            return output

    ensure_rust()
    cmd = [
        "cargo", "build", "--release",
        "--manifest-path", str(crate / "Cargo.toml"),
        "--target-dir", str(target_dir),
    ]
    if (crate / "Cargo.lock").is_file():
        cmd.append("--locked")
    subprocess.run(cmd, check=True, cwd=repo)

    built = target_dir / "release" / filename
    if not built.is_file():
        raise FileNotFoundError(f"Expected built library not found: {built}")
    output.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(built, output)
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description="Build RAK termcore shared library.")
    parser.add_argument("--force", action="store_true", help="Rebuild even if output is up to date.")
    args = parser.parse_args()
    out = build_termcore(force=args.force)
    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
