# SPDX-License-Identifier: MPL-2.0
"""Build RAK's custom Rust/OpenGL Gaussian library."""
from pathlib import Path
import shutil
import subprocess
import sys


def main():
    repo = Path(__file__).resolve().parents[1]
    crate = repo / "native/rak_blur"
    target = repo / "build/rak_blur"
    subprocess.run(["cargo", "build", "--release", "--locked", "--manifest-path", str(crate / "Cargo.toml"),
                    "--target-dir", str(target)], check=True, cwd=repo)
    filename = "rak_blur.dll" if sys.platform == "win32" else "librak_blur.so"
    output = repo / "core/native" / filename
    output.parent.mkdir(exist_ok=True)
    shutil.copy2(target / "release" / filename, output)
    print(output)


if __name__ == "__main__":
    main()
