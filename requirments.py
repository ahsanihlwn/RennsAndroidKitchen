# SPDX-License-Identifier: MPL-2.0
import argparse
from importlib import metadata
import importlib.util
import subprocess
import sys

from core.flag import edition_label
from core.rennsproject_bridge import private_bundle_enabled
from tools.rust_toolchain import ensure_rust, rust_status

REQUIRED_PYTHON = (3, 14)
PYTHON_DOWNLOAD = "https://www.python.org/downloads/"

REQUIREMENTS = [
    # UI
    "PySide6==6.10.1",
    "shiboken6==6.10.1",
    "skia-python==144.0.post2",
    # Runtime
    "requests==2.32.5",
    "websockets==15.0.1",
    "protobuf==6.33.5",
    "py7zr==1.1.0",
    "rarfile==4.2",
    "lz4==4.4.5",
    "zstandard==0.25.0",
    "Brotli==1.2.0",
    "numpy==2.4.2",
    "pyelftools==0.33",
    "capstone==5.0.9",
    "psutil==7.2.2",
    "lief==0.17.1",
    "cryptography==46.0.4",
    # Build
    "Nuitka==4.1.2",
    "maturin==1.15.0",
]
if sys.platform == "win32":
    REQUIREMENTS.append("pywinpty==3.0.5")


def get_requirements(private_build=None):
    requirements = list(REQUIREMENTS)
    if private_build is None:
        private_build = private_bundle_enabled()
    if private_build:
        from rennsproject.requirements import REQUIREMENTS as official
        requirements.extend(official)
    return list(dict.fromkeys(requirements))


def check_python():
    current = sys.version_info
    print(f"Python: {current.major}.{current.minor}.{current.micro}", flush=True)
    if (current.major, current.minor) == REQUIRED_PYTHON:
        return True
    print(f"Python {REQUIRED_PYTHON[0]}.{REQUIRED_PYTHON[1]}.x is required: {PYTHON_DOWNLOAD}")
    return False


def package_status(private_build=None):
    status = []
    for requirement in get_requirements(private_build):
        name, expected = requirement.split("==", 1)
        try:
            installed = metadata.version(name)
        except metadata.PackageNotFoundError:
            installed = None
        status.append((requirement, installed, installed == expected))
    return status


def ensure_python_dependencies(*, private_build=None, upgrade=False, check_version=True):
    if check_version and not check_python():
        raise RuntimeError("Use Python 3.14.x to build this project.")
    status = package_status(private_build)
    if all(ready for _, _, ready in status) and not upgrade:
        print("Python dependencies: ready", flush=True)
        return
    if importlib.util.find_spec("pip") is None:
        subprocess.run([sys.executable, "-m", "ensurepip", "--upgrade"], check=True)
    command = [sys.executable, "-m", "pip", "install"]
    if upgrade:
        command.append("--upgrade")
    # Supply all pins so pip preserves the project's dependency versions.
    command.extend(get_requirements(private_build))
    print("Python dependencies: installing project requirements", flush=True)
    subprocess.run(command, check=True)
    missing = [requirement for requirement, _, ready in package_status(private_build) if not ready]
    if missing:
        raise RuntimeError(f"Dependencies still need installation: {', '.join(missing)}")
    print("Python dependencies: ready", flush=True)


def main():
    parser = argparse.ArgumentParser(description="Set up kitchen dependencies and the Rust toolchain.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="Check Python packages and Rust without installing")
    mode.add_argument("--list", action="store_true", help="List dependencies for the current edition")
    parser.add_argument("--upgrade", action="store_true", help="Refresh dependencies within the project pins")
    parser.add_argument("--no-python-check", action="store_true", help="Allow another Python version")
    args = parser.parse_args()
    print(f"Dependencies: {edition_label()}", flush=True)
    if args.list:
        for requirement in get_requirements():
            print(requirement)
        print("Rust/Cargo + Visual Studio C++ tools and Windows SDK (Windows)")
        return 0
    if args.check:
        python_ready = args.no_python_check or check_python()
        packages = package_status()
        for requirement, installed, ready in packages:
            detail = "ready" if ready else f"installed: {installed or 'missing'}"
            print(f"{'OK' if ready else 'MISSING'} {requirement} ({detail})")
        rust = rust_status()
        for detail in rust["details"]:
            print(detail)
        ready = python_ready and all(item[2] for item in packages) and rust["ready"]
        print("Environment: ready" if ready else "Environment: incomplete")
        return 0 if ready else 1
    ensure_python_dependencies(upgrade=args.upgrade, check_version=not args.no_python_check)
    ensure_rust()
    print("Dependencies: ready. Run python build.py to compile the kitchen.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as error:
        print(f"Setup failed: {error}", file=sys.stderr)
        sys.exit(1)
