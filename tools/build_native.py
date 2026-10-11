# SPDX-License-Identifier: MPL-2.0
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import sysconfig
import tempfile
import tomllib

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.rust_toolchain import ensure_rust

ROOT = Path(__file__).resolve().parents[1]
CRATE = ROOT / "native" / "rak_native"
MANIFEST = CRATE / "Cargo.toml"
WHEEL_CACHE = ROOT / "build" / "native" / "wheels"
API_VERSION = 3


def source_fingerprint():
    paths = {MANIFEST, CRATE / "Cargo.lock", CRATE / "pyproject.toml"}
    for folder in (CRATE / "src", CRATE / ".cargo", ROOT / ".cargo"):
        if folder.is_dir():
            paths.update(path for path in folder.rglob("*") if path.is_file())
    for name in ("build.rs", "rust-toolchain", "rust-toolchain.toml"):
        for folder in (CRATE, ROOT):
            path = folder / name
            if path.is_file():
                paths.add(path)
    environment = {
        "python": sys.implementation.name,
        "abi": sysconfig.get_config_var("SOABI"),
        "platform": sysconfig.get_platform(),
        "machine": platform.machine(),
        "api": API_VERSION,
        "flags": {name: os.environ.get(name, "") for name in (
            "RUSTFLAGS", "CARGO_ENCODED_RUSTFLAGS", "CARGO_BUILD_TARGET",
            "PYO3_USE_ABI3_FORWARD_COMPATIBILITY", "PYO3_CONFIG_FILE",
        )},
    }
    config = os.environ.get("PYO3_CONFIG_FILE")
    if config:
        environment["pyo3_config"] = Path(config).read_text(encoding="utf-8")
    digest = hashlib.sha256(json.dumps(environment, sort_keys=True).encode())
    for path in sorted(paths):
        digest.update(path.relative_to(ROOT).as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def native_environment(target=None):
    environment = os.environ.copy()
    if target is not None:
        entries = [str(target)]
        if environment.get("PYTHONPATH"):
            entries.append(environment["PYTHONPATH"])
        environment["PYTHONPATH"] = os.pathsep.join(entries)
    return environment


def installed_native(target=None):
    # Probe in another process so Windows keeps the extension DLL unlocked.
    code = (
        "import json, rak_native as m; "
        "print(json.dumps({'api': getattr(m, 'API_VERSION', None), "
        "'version': getattr(m, '__version__', ''), "
        "'fingerprint': getattr(m, 'BUILD_FINGERPRINT', '')}))"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=ROOT, capture_output=True,
        text=True, timeout=30, env=native_environment(target),
    )
    if result.returncode:
        return {}
    try:
        return json.loads(result.stdout.strip())
    except (ValueError, TypeError):
        return {}


def expected_native(fingerprint):
    with MANIFEST.open("rb") as source:
        version = tomllib.load(source)["package"]["version"]
    return {"api": API_VERSION, "version": version, "fingerprint": fingerprint}


def cached_wheel(fingerprint):
    directory = WHEEL_CACHE / fingerprint
    wheels = list(directory.glob("*.whl"))
    receipt = directory / "wheel.json"
    if len(wheels) != 1 or not receipt.is_file():
        return None
    try:
        metadata = json.loads(receipt.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None
    wheel = wheels[0]
    checksum = hashlib.sha256(wheel.read_bytes()).hexdigest()
    if metadata == {"name": wheel.name, "sha256": checksum}:
        return wheel
    return None


def build_wheel(fingerprint):
    ensure_rust()
    if importlib.util.find_spec("maturin") is None:
        subprocess.run([sys.executable, "-m", "pip", "install", "maturin==1.15.0"], check=True)
    environment = os.environ.copy()
    environment["RAK_NATIVE_BUILD_FINGERPRINT"] = fingerprint
    with tempfile.TemporaryDirectory(prefix="rak-native-build-") as scratch:
        subprocess.run([
            sys.executable, "-m", "maturin", "build", "--release", "--locked",
            "--manifest-path", str(MANIFEST), "--interpreter", sys.executable,
            "--out", scratch,
        ], cwd=ROOT, env=environment, check=True)
        if source_fingerprint() != fingerprint:
            raise RuntimeError("Rust source changed during compilation. Run the build again.")
        wheels = list(Path(scratch).glob("*.whl"))
        if len(wheels) != 1:
            raise RuntimeError(f"Expected one native wheel, found {len(wheels)}")
        directory = WHEEL_CACHE / fingerprint
        directory.mkdir(parents=True, exist_ok=True)
        wheel = directory / wheels[0].name
        shutil.copy2(wheels[0], wheel)
    checksum = hashlib.sha256(wheel.read_bytes()).hexdigest()
    (directory / "wheel.json").write_text(
        json.dumps({"name": wheel.name, "sha256": checksum}), encoding="utf-8",
    )
    return wheel


def ensure_native(*, force=False, install=True, target=None):
    fingerprint = source_fingerprint()
    expected = expected_native(fingerprint)
    if install and not force and installed_native(target) == expected:
        print(f"Rust: reuse installed rak_native {expected['version']}", flush=True)
        return None
    wheel = None if force else cached_wheel(fingerprint)
    if wheel is None:
        print("Rust: build source", flush=True)
        wheel = build_wheel(fingerprint)
    else:
        print(f"Rust: reuse cached wheel {wheel.name}", flush=True)
    if install:
        command = [
            sys.executable, "-m", "pip", "install", "--force-reinstall", "--no-deps",
            str(wheel),
        ]
        if target is not None:
            command.extend(["--target", str(target), "--upgrade"])
        subprocess.run(command, check=True)
        if installed_native(target) != expected:
            raise RuntimeError("Installed Rust extension does not match this source or ABI.")
    return wheel


def prepare_native(*, force=False):
    fingerprint = source_fingerprint()
    if not force and installed_native() == expected_native(fingerprint):
        print("Rust: reuse installed extension", flush=True)
        return None
    target = ROOT / "build" / "native" / "runtime" / fingerprint
    ensure_native(force=force, target=target)
    return target


def main():
    parser = argparse.ArgumentParser(description="Build or reuse the kitchen Rust extension.")
    parser.add_argument("--force", action="store_true", help="Rebuild even if source is unchanged")
    parser.add_argument("--no-install", action="store_true", help="Only prepare a release wheel")
    args = parser.parse_args()
    wheel = ensure_native(force=args.force, install=not args.no_install)
    if wheel is not None:
        print(f"Wheel: {wheel}")


if __name__ == "__main__":
    main()
