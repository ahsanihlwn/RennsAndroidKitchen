# SPDX-License-Identifier: MPL-2.0
import argparse
import os
from pathlib import Path
import platform as _platform
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile

from core.flag import APP_VERSION, APP_VERSION_LABEL, edition_label
from core.rennsproject_bridge import private_bundle_enabled
from requirments import ensure_python_dependencies
from tools.build_native import native_environment, prepare_native

ROOT = Path(__file__).resolve().parent
BUILD_ROOT = ROOT / "build" / "nuitka"
RELEASE_ROOT = ROOT / "release"

INCLUDE_PACKAGES = ["core", "main_ext", "app_ext", "windows", "icons", "services", "textures", "ui"]
_COMMON_INCLUDE_MODULES = [
    "main", "core.neocat_load", "core.error_reporting", "services.service_main", "core.rak_cli",
    "multiprocessing", "multiprocessing.spawn",
    "multiprocessing.context", "multiprocessing.resource_tracker", "multiprocessing.connection",
    "PySide6.QtNetwork", "PySide6.QtWebSockets", "PySide6.QtMultimedia",
    "PySide6.QtMultimediaWidgets", "PySide6.QtSvg", "PySide6.QtSvgWidgets",
    "PySide6.QtOpenGL", "PySide6.QtOpenGLWidgets", "PySide6.QtQuick",
    "PySide6.QtQml", "PySide6.QtQuickControls2",
    "skia",
]
_COMMON_THIRD_PARTY_PACKAGES = [
    "requests", "urllib3", "charset_normalizer", "idna", "certifi", "websockets",
    "cryptography", "elftools", "capstone", "lief", "psutil", "py7zr", "rarfile",
    "lz4", "brotli", "numpy", "zstandard", "google.protobuf", "rak_native", "quill_native",
]


def include_modules_for_platform(target_platform: str | None = None) -> list[str]:
    plat = sys.platform if target_platform is None else target_platform
    modules = list(_COMMON_INCLUDE_MODULES)
    if plat == "win32":
        modules.append("multiprocessing.popen_spawn_win32")
    else:
        modules.extend(["multiprocessing.popen_spawn_posix", "multiprocessing.popen_fork"])
    return modules


def third_party_packages_for_platform(target_platform: str | None = None) -> list[str]:
    plat = sys.platform if target_platform is None else target_platform
    pkgs = list(_COMMON_THIRD_PARTY_PACKAGES)
    if plat == "win32":
        pkgs.append("winpty")
    return pkgs


def terminal_native_libs_for_platform(target_platform: str | None = None) -> list[str]:
    plat = sys.platform if target_platform is None else target_platform
    if plat == "win32":
        return ["termcore.dll"]
    if plat == "darwin":
        return ["libtermcore.dylib"]
    return ["libtermcore.so"]


def blur_native_lib_for_platform(target_platform: str | None = None) -> str:
    plat = sys.platform if target_platform is None else target_platform
    if plat == "win32":
        return "rak_blur.dll"
    if plat == "darwin":
        return "librak_blur.dylib"
    return "librak_blur.so"


INCLUDE_MODULES = include_modules_for_platform()
THIRD_PARTY_PACKAGES = third_party_packages_for_platform()
TERMINAL_NATIVE_LIBS = terminal_native_libs_for_platform()

NUITKA_EXCLUDES = [
    "torch", "torchvision", "torchaudio", "tensorflow", "pyiceberg", "hive_metastore",
    "thrift", "mmh3", "pyroaring", "google.genai", "google.generativeai",
    "rennsproject.build_config", "rennsproject.requirements",
    "rennsproject.updater",
    "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineCore", "PySide6.QtWebEngineQuick", "PySide6.QtWebChannel",
]
ASSET_DIRS = [
    "icons", "textures", "learning_contexts", "fonts",
    "core/flashable_assets", "app_ext/dependency_registry", "licenses", "package",
]
LICENSE_FILES = [
    "LICENSE",
    "NOTICE",
    "core/lpunpack/LICENSE",
    "core/ext_cli/LICENSE",
    "core/make_ext4fs/LICENSE",
    "core/make_ext4fs/NOTICE",
    "core/pac_extractor/LICENSE",
    "core/magiskboot/LICENSE",
    "core/erofs_rs/LICENSE",
    "core/erofs_rs/NOTICE",
    "core/sdatimg/LICENSE",
    "core/brotli_rs/LICENSE",
]


def _normalized_arch(machine: str | None = None) -> str:
    raw = (_platform.machine() if machine is None else machine).lower()
    if raw in ("amd64", "x86_64"):
        return "x86_64"
    if raw in ("arm64", "aarch64"):
        return "arm64"
    return re.sub(r"[^a-z0-9_]+", "", raw) or "unknown"


def release_name(edition, *, target_platform: str | None = None, machine: str | None = None):
    version = f"{APP_VERSION}{APP_VERSION_LABEL}"
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", version):
        raise ValueError("Version contains characters that cannot be used in release filenames.")
    plat = sys.platform if target_platform is None else target_platform
    if plat == "win32":
        return f"RennsAndroidKitchen_{edition}-{version}.zip"
    os_tag = "macos" if plat == "darwin" else "linux"
    arch = _normalized_arch(machine)
    return f"RennsAndroidKitchen_{edition}-{version}-{os_tag}-{arch}.tar.gz"


def file_version():
    parts = APP_VERSION.split(".")
    if not 1 <= len(parts) <= 4 or any(not part.isdigit() for part in parts):
        raise ValueError("APP_VERSION must have one to four numeric components.")
    if any(int(part) > 65535 for part in parts):
        raise ValueError("Windows version components cannot exceed 65535.")
    return ".".join(parts + ["0"] * (4 - len(parts)))


def embedded_asset_entries(private_build, *, target_platform: str | None = None):
    # QML stays beside the compiled ui package, matching its source layout.
    for source in sorted(path for path in (ROOT / "ui").rglob("*") if path.suffix in (".qml", ".js", ".frag", ".vert", ".qsb")):
        yield source, source.relative_to(ROOT).as_posix()
    for relative in ASSET_DIRS:
        source = ROOT / relative
        if not source.is_dir():
            raise FileNotFoundError(f"Missing build asset: {source}")
        yield source, relative
    for filename in terminal_native_libs_for_platform(target_platform):
        source = ROOT / "core" / filename
        if not source.is_file():
            raise FileNotFoundError(f"Missing terminal native library: {source}")
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
        avatar_assets = avatar / "assets"
        if avatar_assets.is_dir():
            for source in sorted(avatar_assets.iterdir()):
                if source.is_file() and source.suffix.lower() in (
                    ".png", ".jpg", ".jpeg", ".webp", ".svg", ".ico",
                ):
                    yield source, f"rennsproject/avatar/assets/{source.name}"


def _find_capstone_libs(target_platform: str | None = None) -> list[Path]:
    plat = sys.platform if target_platform is None else target_platform
    import capstone
    lib_dir = Path(capstone.__file__).parent / "lib"
    if not lib_dir.is_dir():
        return []
    if plat == "win32":
        dll = lib_dir / "capstone.dll"
        if not dll.is_file():
            raise FileNotFoundError(f"Missing Capstone DLL: {dll}")
        return [dll]
    patterns = ("libcapstone*.dylib",) if plat == "darwin" else ("libcapstone.so*",)
    libs: list[Path] = []
    for pat in patterns:
        libs.extend(sorted(p for p in lib_dir.glob(pat) if p.is_file()))
    return libs


def nuitka_command(output, *, private_build, debug, jobs, target_platform: str | None = None, macos_app_bundle: bool = False):
    plat = sys.platform if target_platform is None else target_platform
    version = file_version()
    exe_name = "run.exe" if plat == "win32" else "run"
    command = [
        sys.executable, "-m", "nuitka", str(ROOT / "app.py"),
        "--standalone", "--assume-yes-for-downloads",
        f"--output-dir={output}", f"--output-filename={exe_name}",
    ]
    if plat == "win32":
        command.extend([
            f"--windows-icon-from-ico={ROOT / 'icon.ico'}",
            "--company-name=Renns Studio", "--product-name=Renns Android Kitchen",
            f"--file-version={version}", f"--product-version={version}",
            "--file-description=Renns Android Kitchen", "--copyright=(c) Renns Studio",
            "--windows-console-mode=force" if debug else "--windows-console-mode=disable",
        ])
    elif plat == "darwin":
        if macos_app_bundle:
            command.append("--macos-create-app-bundle")
        command.extend([
            "--macos-app-name=Renns Android Kitchen",
            f"--macos-app-version={APP_VERSION}",
        ])
        icns = ROOT / "icon.icns"
        if icns.is_file():
            command.append(f"--macos-app-icon={icns}")
    command.extend([
        "--enable-plugin=pyside6", "--include-qt-plugins=sensible,multimedia,qml",
        f"--report={output / 'report.xml'}", f"--jobs={jobs}",
        "--include-package-data=capstone", "--include-package-data=elftools",
        f"--user-package-configuration-file={ROOT / 'native/rak_blur/nuitka-package.config.yml'}",
    ])
    packages = list(INCLUDE_PACKAGES)
    third_party = third_party_packages_for_platform(plat)
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
    command.extend(f"--include-module={module}" for module in include_modules_for_platform(plat))
    command.extend(f"--nofollow-import-to={module}" for module in NUITKA_EXCLUDES)
    for source, destination in embedded_asset_entries(private_build, target_platform=plat):
        kind = "dir" if source.is_dir() else "files"
        command.append(f"--include-data-{kind}={source}={destination}")
    # Include Capstone shared library beside both capstone/lib/ and root for reliable ctypes lookup.
    for cap_lib in _find_capstone_libs(plat):
        command.extend([
            f"--include-data-files={cap_lib}=capstone/lib/{cap_lib.name}",
            f"--include-data-files={cap_lib}={cap_lib.name}",
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


def package_release(distribution, archive, *, target_platform: str | None = None):
    plat = sys.platform if target_platform is None else target_platform
    exe_name = "run.exe" if plat == "win32" else "run"
    if not (distribution / exe_name).is_file():
        raise FileNotFoundError(f"Missing Nuitka executable: {distribution / exe_name}")
    if plat == "win32":
        (distribution / "rak.bat").write_bytes(
            b'@echo off\r\n"%~dp0run.exe" --rak-cli %*\r\nexit /b %ERRORLEVEL%\r\n'
        )
    else:
        launcher = distribution / "rak"
        launcher.write_text(
            '#!/usr/bin/env sh\n'
            'DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)\n'
            'exec "$DIR/run" --rak-cli "$@"\n',
            encoding="utf-8",
            newline="\n",
        )
        try:
            launcher.chmod(0o755)
        except OSError:
            pass
    (distribution / "SOURCE_CODE.txt").write_text(
        f"Renns Android Kitchen {APP_VERSION}{APP_VERSION_LABEL}\n"
        "Public source code is available under MPL-2.0:\n"
        "https://github.com/ahsanihlwn/RennsAndroidKitchen\n"
        "See LICENSE and NOTICE for licensing scope and third-party notices.\n",
        encoding="utf-8",
    )
    archive.parent.mkdir(parents=True, exist_ok=True)
    # Replace the old archive only after the new archive is complete.
    with tempfile.NamedTemporaryFile(dir=archive.parent, suffix=".tmp", delete=False) as handle:
        temporary = Path(handle.name)
    try:
        if archive.name.endswith(".tar.gz") or archive.name.endswith(".tgz"):
            with tarfile.open(temporary, "w:gz", compresslevel=6) as bundle:
                for source in sorted(distribution.rglob("*")):
                    arcname = source.relative_to(distribution).as_posix()
                    if source.is_symlink() or source.is_file():
                        bundle.add(source, arcname=arcname, recursive=False)
        else:
            with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as bundle:
                for source in sorted(distribution.rglob("*")):
                    if source.is_file():
                        bundle.write(source, source.relative_to(distribution).as_posix())
        temporary.replace(archive)
    finally:
        temporary.unlink(missing_ok=True)
    return archive


def main():
    parser = argparse.ArgumentParser(description="Build the kitchen with Nuitka for Windows, macOS, or Linux.")
    parser.add_argument("--debug", action="store_true", help="Enable console and compiler progress")
    parser.add_argument("--jobs", type=int, default=os.cpu_count() or 4, help="Parallel compiler jobs")
    parser.add_argument("--clean", action="store_true", help="Remove this edition's previous Nuitka build")
    parser.add_argument("--rebuild-rust", action="store_true", help="Force a rak_native Rust rebuild")
    parser.add_argument("--rebuild-blur", action="store_true", help="Rebuild the Rust Gaussian GPU shader")
    parser.add_argument("--rebuild-termcore", action="store_true", help="Rebuild the Rust terminal core")
    parser.add_argument("--rebuild-quill", action="store_true", help="Rebuild the quill_native editor extension")
    parser.add_argument("--macos-app-bundle", action="store_true", help="Produce a macOS .app bundle")
    args = parser.parse_args()
    if args.jobs < 1:
        parser.error("--jobs must be at least 1")
    private_build = private_bundle_enabled()
    edition = edition_label()
    output = BUILD_ROOT / edition
    archive = RELEASE_ROOT / edition / release_name(edition)
    file_version()
    print(f"Edition: {edition} | Version: {APP_VERSION}{APP_VERSION_LABEL} | Platform: {sys.platform}", flush=True)
    if args.clean:
        clean_build(output)
    output.mkdir(parents=True, exist_ok=True)
    ensure_python_dependencies(private_build=private_build)
    from tools.build_quick_shaders import build_shaders
    build_shaders()
    native_target = prepare_native(force=args.rebuild_rust)
    from tools.build_quill import build_and_install as build_quill
    build_quill(force=args.rebuild_quill)
    from tools.build_termcore import build_termcore
    build_termcore(force=args.rebuild_termcore)
    blur_lib = ROOT / "core" / "native" / blur_native_lib_for_platform()
    if args.rebuild_blur or not blur_lib.is_file():
        from tools.build_gpu_blur import main as build_blur
        build_blur()
    command = nuitka_command(
        output,
        private_build=private_build,
        debug=args.debug,
        jobs=args.jobs,
        macos_app_bundle=args.macos_app_bundle,
    )
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
