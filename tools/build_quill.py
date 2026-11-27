# SPDX-License-Identifier: MPL-2.0
"""Build and install the bundled quill_native Rust/PySide6 editor extension."""
from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.rust_toolchain import ensure_rust

ROOT = Path(__file__).resolve().parents[1]
QUILL_ROOT = ROOT / "assets" / "quill-native"


def is_quill_installed(target: Path | None = None) -> bool:
    env = None
    if target is not None:
        import os
        env = os.environ.copy()
        env["PYTHONPATH"] = str(target) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    proc = subprocess.run(
        [sys.executable, "-c", "import quill_native, quill_native._engine"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
    )
    return proc.returncode == 0


def build_and_install(*, force: bool = False, target: Path | None = None) -> bool:
    if not QUILL_ROOT.is_dir():
        return False
    if not force and is_quill_installed(target=target):
        return False

    ensure_rust()
    if importlib.util.find_spec("maturin") is None:
        subprocess.run([sys.executable, "-m", "pip", "install", "maturin==1.15.0"], check=True)

    with tempfile.TemporaryDirectory(prefix="rak-quill-build-") as scratch:
        out_dir = Path(scratch) / "wheels"
        out_dir.mkdir(parents=True, exist_ok=True)
        cmd = [
            sys.executable, "-m", "maturin", "build",
            "--release",
            "--manifest-path", str(QUILL_ROOT / "native" / "Cargo.toml"),
            "--interpreter", sys.executable,
            "--out", str(out_dir),
        ]
        if (QUILL_ROOT / "native" / "Cargo.lock").is_file():
            cmd.append("--locked")
        subprocess.run(cmd, check=True, cwd=QUILL_ROOT)
        wheels = list(out_dir.glob("*.whl"))
        if len(wheels) != 1:
            raise RuntimeError(f"Expected 1 quill_native wheel, found {len(wheels)}")
        install_cmd = [
            sys.executable, "-m", "pip", "install",
            "--no-deps", "--force-reinstall", str(wheels[0]),
        ]
        if target is not None:
            install_cmd.extend(["--target", str(target)])
        subprocess.run(install_cmd, check=True, cwd=ROOT)
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="Build and install quill_native.")
    parser.add_argument("--force", action="store_true", help="Rebuild even if already installed.")
    parser.add_argument("--target", type=Path, help="Install into a staging directory.")
    args = parser.parse_args()
    built = build_and_install(force=args.force, target=args.target)
    print("Built and installed quill_native." if built else "quill_native is already installed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
