# SPDX-License-Identifier: MPL-2.0
import hashlib
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import sysconfig
import tempfile
import tomllib
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "native" / "rak_native" / "Cargo.toml"
RUST_DOWNLOAD = "https://rust-lang.org/tools/install/"
MSVC_DOWNLOAD = "https://rust-lang.github.io/rustup/installation/windows-msvc.html"


def cargo_bin():
    return Path(os.environ.get("CARGO_HOME", Path.home() / ".cargo")).expanduser() / "bin"


def add_cargo_path(*, prepend=False):
    directory = cargo_bin()
    if directory.is_dir():
        entries = os.environ.get("PATH", "").split(os.pathsep)
        present = any(os.path.normcase(entry) == os.path.normcase(str(directory)) for entry in entries)
        if present and not prepend:
            return
        entries = [entry for entry in entries if os.path.normcase(entry) != os.path.normcase(str(directory))]
        entries.insert(0 if prepend else len(entries), str(directory))
        os.environ["PATH"] = os.pathsep.join(entries)


def windows_target():
    targets = {
        "win-amd64": "x86_64-pc-windows-msvc",
        "win-arm64": "aarch64-pc-windows-msvc",
        "win32": "i686-pc-windows-msvc",
    }
    target = targets.get(sysconfig.get_platform())
    if target is None:
        raise RuntimeError(f"Unsupported Windows Python platform: {sysconfig.get_platform()}")
    return target


def command_output(command):
    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=30)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or f"Command failed: {command[0]}")
    return result.stdout.strip()


def windows_build_errors():
    import winreg

    arch = {"win-amd64": "x64", "win-arm64": "arm64", "win32": "x86"}[sysconfig.get_platform()]
    vswhere = Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Microsoft Visual Studio/Installer/vswhere.exe"
    linkers = []
    if vswhere.is_file():
        component = "Microsoft.VisualStudio.Component.VC.Tools.ARM64" if arch == "arm64" else "Microsoft.VisualStudio.Component.VC.Tools.x86.x64"
        locations = command_output([
            str(vswhere), "-products", "*", "-requires", component,
            "-property", "installationPath",
        ])
        for location in locations.splitlines():
            tools = Path(location) / "VC/Tools/MSVC"
            linkers.extend(path for path in tools.glob(f"*/bin/Host*/{arch}/link.exe")
                           if (path.parent / "cl.exe").is_file())
    errors = []
    libraries = [Path(path) for path in os.environ.get("LIB", "").split(os.pathsep) if path]
    if not linkers and not (shutil.which("cl.exe") and shutil.which("link.exe")):
        errors.append("Missing Visual Studio C++ compiler/linker")
    sdk_ready = any((path / "kernel32.lib").is_file() for path in libraries) and any(
        (path / "ucrt.lib").is_file() for path in libraries
    )
    if not sdk_ready:
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows Kits\Installed Roots", 0,
                                winreg.KEY_READ | winreg.KEY_WOW64_32KEY) as key:
                sdk = Path(winreg.QueryValueEx(key, "KitsRoot10")[0])
            sdk_ready = any(
                (version / "um" / arch / "kernel32.lib").is_file()
                and (version / "ucrt" / arch / "ucrt.lib").is_file()
                for version in (sdk / "Lib").glob("*")
            )
        except OSError:
            pass
    if not sdk_ready:
        errors.append("Missing Windows SDK libraries")
    return errors


def rust_status():
    add_cargo_path()
    details = []
    errors = []
    with MANIFEST.open("rb") as source:
        minimum = tomllib.load(source)["package"]["rust-version"]
    for name in ("cargo", "rustc"):
        executable = shutil.which(name)
        if executable is None:
            errors.append(f"Missing {name}")
            continue
        try:
            output = command_output([executable, "--version", "--verbose"])
            if not output:
                raise RuntimeError("Version command returned no output")
            details.append(output.splitlines()[0])
            if name == "rustc":
                fields = dict(line.split(": ", 1) for line in output.splitlines() if ": " in line)
                version = re.match(r"(\d+)\.(\d+)\.(\d+)", fields.get("release", ""))
                required = tuple(int(part) for part in minimum.split("."))
                if version is None or tuple(map(int, version.groups()))[:len(required)] < required:
                    errors.append(f"Rust {minimum} or newer is required")
                if sys.platform == "win32" and fields.get("host") != windows_target():
                    errors.append(f"Rust must match this Python: {windows_target()}")
        except (OSError, RuntimeError, subprocess.SubprocessError) as error:
            errors.append(f"{name}: {error}")
    compiler_ready = not errors
    if sys.platform == "win32":
        try:
            build_errors = windows_build_errors()
            errors.extend(build_errors)
            if not build_errors:
                details.append("Visual Studio C++ tools + Windows SDK: ready")
        except (OSError, RuntimeError, subprocess.SubprocessError) as error:
            errors.append(f"Windows build tools: {error}")
    details.extend(errors)
    return {"ready": not errors, "compiler_ready": compiler_ready, "details": details, "errors": errors}


def install_rust():
    if sys.platform != "win32":
        raise RuntimeError(f"Install Rust/Cargo with rustup: {RUST_DOWNLOAD}")
    target = windows_target()
    toolchain = f"stable-{target}"
    rustup = shutil.which("rustup")
    print(f"Rust: installing {toolchain}", flush=True)
    if rustup:
        subprocess.run([rustup, "toolchain", "install", toolchain, "--profile", "minimal"], cwd=ROOT, check=True)
    else:
        url = f"https://static.rust-lang.org/rustup/dist/{target}/rustup-init.exe"
        with tempfile.TemporaryDirectory(prefix="rak-rust-setup-") as scratch:
            installer = Path(scratch) / "rustup-init.exe"
            with urlopen(url, timeout=60) as response, installer.open("wb") as destination:
                shutil.copyfileobj(response, destination)
            with urlopen(f"{url}.sha256", timeout=60) as response:
                checksum = response.read().decode("ascii").strip().split()
            if not checksum or not re.fullmatch(r"[a-fA-F0-9]{64}", checksum[0]):
                raise RuntimeError("Rust installer checksum is missing or invalid.")
            if hashlib.sha256(installer.read_bytes()).hexdigest() != checksum[0].lower():
                raise RuntimeError("Rust installer checksum does not match.")
            subprocess.run([
                str(installer), "-y", "--profile", "minimal", "--default-host", target,
                "--default-toolchain", "stable", "--no-modify-path",
            ], cwd=ROOT, check=True)
    # Use the installed tools immediately, without changing the user's shell.
    add_cargo_path(prepend=True)
    os.environ["RUSTUP_TOOLCHAIN"] = toolchain


def ensure_rust():
    status = rust_status()
    if not status["compiler_ready"]:
        install_rust()
        status = rust_status()
    if not status["ready"]:
        raise RuntimeError(f"{'; '.join(status['errors'])}. Toolchain setup: {RUST_DOWNLOAD}; C++ setup: {MSVC_DOWNLOAD}")
    for detail in status["details"]:
        print(detail, flush=True)
