# SPDX-License-Identifier: MPL-2.0
import argparse
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile

from core.flag import APP_VERSION, APP_VERSION_LABEL, edition_label
from core.rennsproject_bridge import private_bundle_enabled
from requirments import ensure_python_dependencies
from tools.build_native import native_environment, prepare_native

ROOT = Path(__file__).resolve().parent
BUILD_ROOT = ROOT / "build" / "nuitka"
RELEASE_ROOT = ROOT / "release"

INCLUDE_PACKAGES = ["core", "main_ext", "app_ext", "windows", "icons", "services", "textures"]
INCLUDE_MODULES = [
    "main", "core.neocat_load", "core.error_reporting", "services.service_main", "core.rak_cli",
    "multiprocessing", "multiprocessing.spawn", "multiprocessing.popen_spawn_win32",
    "multiprocessing.context", "multiprocessing.resource_tracker", "multiprocessing.connection",
    "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineCore", "PySide6.QtWebChannel",
    "PySide6.QtNetwork", "PySide6.QtWebSockets", "PySide6.QtMultimedia",
    "PySide6.QtMultimediaWidgets", "PySide6.QtSvg", "PySide6.QtSvgWidgets",
    "PySide6.QtOpenGL", "PySide6.QtOpenGLWidgets", "PySide6.QtQuick",
    "skia",
]
THIRD_PARTY_PACKAGES = [
    "requests", "urllib3", "charset_normalizer", "idna", "certifi", "websockets",
    "cryptography", "elftools", "capstone", "lief", "psutil", "py7zr", "rarfile",
    "lz4", "brotli", "numpy", "zstandard", "google.protobuf", "rak_native", "winpty",
]
NUITKA_EXCLUDES = [
    "torch", "torchvision", "torchaudio", "tensorflow", "pyiceberg", "hive_metastore",
    "thrift", "mmh3", "pyroaring", "google.genai", "google.generativeai",
    "rennsproject.build_config", "rennsproject.requirements",
    "rennsproject.updater",
]
ASSET_DIRS = [
    "icons", "textures", "learning_contexts", "fonts", "assets/monaco",
    "core/flashable_assets", "app_ext/dependency_registry", "licenses",
]
TERMINAL_ASSETS = [
    "terminal.html", "xterm.css", "xterm.js", "xterm-addon-fit.js",
    "xterm-addon-web-links.js", "qwebchannel.js", "marked.min.js",
]
LICENSE_FILES = ["LICENSE", "NOTICE", "core/img2sdat/LICENSE", "core/sdat2img/LICENSE"]


def release_name(edition):
    version = f"{APP_VERSION}{APP_VERSION_LABEL}"
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", version):
        raise ValueError("Version contains characters that cannot be used in release filenames.")
    return f"RennsAndroidKitchen_{edition}-{version}.zip"


def file_version():
    parts = APP_VERSION.split(".")
    if not 1 <= len(parts) <= 4 or any(not part.isdigit() for part in parts):
        raise ValueError("APP_VERSION must have one to four numeric components.")
    if any(int(part) > 65535 for part in parts):
        raise ValueError("Windows version components cannot exceed 65535.")
    return ".".join(parts + ["0"] * (4 - len(parts)))


def embedded_asset_entries(private_build):
    for relative in ASSET_DIRS:
        source = ROOT / relative
        if not source.is_dir():
            raise FileNotFoundError(f"Missing build asset: {source}")
        yield source, relative
    for filename in TERMINAL_ASSETS:
        source = ROOT / "core" / filename
        if not source.is_file():
            raise FileNotFoundError(f"Missing terminal asset: {source}")
        yield source, f"core/{filename}"
    for relative in LICENSE_FILES:
        source = ROOT / relative
        if not source.is_file():
            raise FileNotFoundError(f"Missing dependency license: {source}")
        yield source, relative
    if private_build:
        avatar = ROOT / "rennsproject" / "avatar"
        if not avatar.is_dir():
            raise FileNotFoundError(f"Missing Official avatar assets: {avatar}")
        # Private Python modules are compiled; only artwork and JSON are data.
        for source in sorted(avatar.iterdir()):
            if source.is_file() and source.suffix.lower() in (
                ".png", ".jpg", ".jpeg", ".webp", ".svg", ".ico", ".json",
            ):
                yield source, f"rennsproject/avatar/{source.name}"


def nuitka_command(output, *, private_build, debug, jobs):
    version = file_version()
    command = [
        sys.executable, "-m", "nuitka", str(ROOT / "app.py"),
        "--standalone", "--assume-yes-for-downloads",
        f"--output-dir={output}", "--output-filename=run.exe",
        f"--windows-icon-from-ico={ROOT / 'icon.ico'}",
        "--company-name=Renns Studio", "--product-name=Renns Android Kitchen",
        f"--file-version={version}", f"--product-version={version}",
        "--file-description=Renns Android Kitchen", "--copyright=(c) Renns Studio",
        "--windows-console-mode=force" if debug else "--windows-console-mode=disable",
        "--enable-plugin=pyside6", "--include-qt-plugins=sensible,multimedia",
        f"--report={output / 'report.xml'}", f"--jobs={jobs}",
        "--include-package-data=capstone", "--include-package-data=elftools",
    ]
    packages = list(INCLUDE_PACKAGES)
    third_party = list(THIRD_PARTY_PACKAGES)
    follow = []
    if private_build:
        from rennsproject.build_config import FOLLOW_PACKAGES, THIRD_PARTY_PACKAGES as official
        packages.append("rennsproject")
        third_party.extend(official)
        follow.extend(FOLLOW_PACKAGES)
    else:
        command.append("--nofollow-import-to=rennsproject")
    for package in dict.fromkeys(packages + follow):
        command.extend([f"--include-package={package}", f"--follow-import-to={package}"])
    for package in dict.fromkeys(third_party):
        if package not in packages + follow:
            command.append(f"--include-package={package}")
    command.extend(f"--include-module={module}" for module in INCLUDE_MODULES)
    command.extend(f"--nofollow-import-to={module}" for module in NUITKA_EXCLUDES)
    for source, destination in embedded_asset_entries(private_build):
        kind = "dir" if source.is_dir() else "files"
        command.append(f"--include-data-{kind}={source}={destination}")
    # Qt's multimedia plugin collects FFmpeg DLLs.
    import capstone
    capstone_dll = Path(capstone.__file__).parent / "lib" / "capstone.dll"
    if not capstone_dll.is_file():
        raise FileNotFoundError(f"Missing Capstone DLL: {capstone_dll}")
    command.extend([
        f"--include-data-files={capstone_dll}=capstone/lib/capstone.dll",
        f"--include-data-files={capstone_dll}=capstone.dll",
    ])
    command.extend(["--show-progress", "--show-modules"] if debug else ["--lto=yes", "--remove-output"])
    return command


def clean_build(output):
    target = output.resolve()
    if (target.parent != BUILD_ROOT.resolve() or target.name not in ("OPSC", "OFCL")
            or not target.is_relative_to(ROOT)):
        raise ValueError(f"Unexpected build directory: {target}")
    if target.exists():
        shutil.rmtree(target)


def package_release(distribution, archive):
    if not (distribution / "run.exe").is_file():
        raise FileNotFoundError(f"Missing Nuitka executable: {distribution / 'run.exe'}")
    (distribution / "rak.bat").write_bytes(
        b'@echo off\r\n"%~dp0run.exe" --rak-cli %*\r\nexit /b %ERRORLEVEL%\r\n'
    )
    (distribution / "SOURCE_CODE.txt").write_text(
        f"Renns Android Kitchen {APP_VERSION}{APP_VERSION_LABEL}\n"
        "Public source code is available under MPL-2.0:\n"
        "https://github.com/ahsanihlwn/RennsAndroidKitchen\n"
        "See LICENSE and NOTICE for licensing scope and third-party notices.\n",
        encoding="utf-8",
    )
    archive.parent.mkdir(parents=True, exist_ok=True)
    # Replace the old ZIP only after the new archive is complete.
    with tempfile.NamedTemporaryFile(dir=archive.parent, suffix=".tmp", delete=False) as handle:
        temporary = Path(handle.name)
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as bundle:
            for source in sorted(distribution.rglob("*")):
                if source.is_file():
                    bundle.write(source, source.relative_to(distribution).as_posix())
        temporary.replace(archive)
    finally:
        temporary.unlink(missing_ok=True)
    return archive


def main():
    parser = argparse.ArgumentParser(description="Build the kitchen with Nuitka and create its release ZIP.")
    parser.add_argument("--debug", action="store_true", help="Enable console and compiler progress")
    parser.add_argument("--jobs", type=int, default=os.cpu_count() or 4, help="Parallel compiler jobs")
    parser.add_argument("--clean", action="store_true", help="Remove this edition's previous Nuitka build")
    parser.add_argument("--rebuild-rust", action="store_true", help="Force a Rust rebuild")
    args = parser.parse_args()
    if sys.platform != "win32":
        parser.error("This builder produces Windows releases.")
    if args.jobs < 1:
        parser.error("--jobs must be at least 1")
    private_build = private_bundle_enabled()
    edition = edition_label()
    output = BUILD_ROOT / edition
    archive = RELEASE_ROOT / edition / release_name(edition)
    file_version()
    print(f"Edition: {edition} | Version: {APP_VERSION}{APP_VERSION_LABEL}", flush=True)
    if args.clean:
        clean_build(output)
    output.mkdir(parents=True, exist_ok=True)
    ensure_python_dependencies(private_build=private_build)
    native_target = prepare_native(force=args.rebuild_rust)
    command = nuitka_command(output, private_build=private_build, debug=args.debug, jobs=args.jobs)
    print("Nuitka: compiling kitchen", flush=True)
    subprocess.run(command, cwd=ROOT, env=native_environment(native_target), check=True)
    distribution = output / "app.dist"
    if private_build:
        from rennsproject.build_config import prepare_distribution
        prepare_distribution(distribution, debug=args.debug, jobs=args.jobs, version=file_version())
    package_release(distribution, archive)
    print(f"Release: {archive}")


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, ValueError, subprocess.CalledProcessError) as error:
        print(f"Build failed: {error}", file=sys.stderr)
        sys.exit(1)
