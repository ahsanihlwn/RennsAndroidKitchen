# SPDX-License-Identifier: MPL-2.0
"""Connect the workspace's boot tree to kernel reconstruction and Kbuild."""
import json
import os
from pathlib import Path
import re
import sys

from core.command_context import OperationError


SOURCE_DIR = "kernel_src"
MANIFEST_NAME = "reconstruction_manifest.json"


def boot_kernel(folder):
    """Recognize a real unpacked Android boot folder and its visible kernel."""
    from core.boot_image import META_DIR, read_bootconf
    from core.magiskboot_tree import META_NAME

    folder = Path(folder)
    conf = folder.parent / "config" / f"{folder.name}_bootconf"
    kind, _ = read_bootconf(conf)
    kernel = folder / "kernel"
    if kind == "boot" and not folder.is_symlink() and not kernel.is_symlink() and kernel.is_file():
        try:
            meta = json.loads((folder / META_DIR / META_NAME).read_text(encoding="utf-8"))
            if meta.get("boot_kind") == "boot" and "kernel" in meta.get("visible_components", ()):
                return kernel
        except (OSError, ValueError, AttributeError, TypeError):
            pass
    return None


def gki_boot_folder(folder):
    from core.boot_image import read_bootconf

    candidate = Path(folder)
    sources = candidate / SOURCE_DIR
    if not reconstruction_manifest(sources):
        return False
    _, flag = read_bootconf(candidate.parent / "config" / f"{candidate.name}_bootconf")
    return bool(re.fullmatch(r"[0-9]{1,12}-[a-f0-9]{40}", flag) and boot_kernel(candidate))


def source_destination(path):
    path = Path(path)
    if path.name == "kernel" and boot_kernel(path.parent) == path:
        return path.parent / SOURCE_DIR
    return path.with_name(path.stem + "_src")


def reconstruction_manifest(folder):
    folder = Path(folder)
    manifest = folder / MANIFEST_NAME
    if not folder.name.endswith("_src") or folder.is_symlink() or manifest.is_symlink() or not manifest.is_file():
        return None
    return manifest


def has_kernel_reconstruction(folder):
    manifest = reconstruction_manifest(folder)
    if not manifest:
        return False
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
        return isinstance(data, dict) and bool(data.get("kernel_info"))
    except (OSError, ValueError):
        return False


def reconstructed_kernel(folder):
    folder = Path(os.path.abspath(folder))
    if folder.name == SOURCE_DIR and (kernel := boot_kernel(folder.parent)):
        return kernel
    manifest = reconstruction_manifest(folder)
    if not manifest:
        return None
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
        original = Path(data["binary_path"])
        if original.is_absolute() and original.parent == folder.parent and source_destination(original) == folder:
            if original.is_file() and not original.is_symlink() and data.get("kernel_info"):
                return original
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return None


_WSL_GIT_CACHE: dict[str, tuple[float, bool]] = {}


def _has_linux_git(distro) -> bool:
    import time
    from core import gki_checkout, kitchen_process, wsl_manager

    key = distro or ""
    now = time.monotonic()
    cached = _WSL_GIT_CACHE.get(key)
    if cached and now - cached[0] < 60.0:
        return cached[1]
    ok = False
    try:
        if sys.platform == "win32":
            distros = wsl_manager.list_distros()
            if distros and (not distro or distro in distros):
                prefix = gki_checkout._linux_command(distro)[:-3]
                with kitchen_process.background_probe():
                    probe = kitchen_process.run(
                        [*prefix, "/bin/sh", "-c", "command -v python3 >/dev/null && command -v git >/dev/null"],
                        capture_output=True, timeout=3, creationflags=wsl_manager._CREATE_NO_WINDOW,
                    )
                ok = probe.returncode == 0
        else:
            gki_checkout.require_linux_tools(distro, "python3", "git")
            ok = True
    except (OperationError, OSError, kitchen_process.TimeoutExpired):
        ok = False
    _WSL_GIT_CACHE[key] = (now, ok)
    return ok


def inspect_download_plan(path, *, use_wsl: bool = False) -> dict:
    """Inspect a kernel/boot image and report whether uncached remote sources need confirmation."""
    from dataclasses import replace
    from core import gki_checkout, kernel_source_provider, kernel_version, wsl_manager

    target = Path(os.path.abspath(path))
    info = kernel_version.inspect_kernel(target)
    if not info.is_kernel:
        raise OperationError(info.error or "The selected file does not contain a recognized kernel.")

    est_files = max(info.estimated_source_files, 1)
    release = None
    reason = ""
    network_unreachable = False
    try:
        release, reason = gki_checkout.resolve_release(info)
    except OperationError:
        network_unreachable = True

    if network_unreachable:
        return {
            "needs_download": False,
            "offline": True,
            "is_gki": False,
            "use_wsl": bool(use_wsl),
            "kernel_version": info.version_string or info.base_version or "Unknown",
            "repo_label": "",
            "repo_version": "",
            "commit": info.git_commit,
            "estimated_size": "0 MB (offline)",
            "estimated_files": est_files,
        }

    if release:
        distro = wsl_manager.distro_for_path(str(target))
        if use_wsl and _has_linux_git(distro):
            cache = gki_checkout.cache_checkout(release.build_id, release.commit, release=release, distro=distro)
            if cache.get("state") in ("ready", "source"):
                return {
                    "needs_download": False,
                    "offline": False,
                    "is_gki": True,
                    "use_wsl": True,
                    "kernel_version": info.version_string,
                    "repo_label": "AOSP Official GKI in WSL (android.googlesource.com/kernel/common)",
                    "repo_version": f"{release.display_name} · build ab{release.build_id}",
                    "commit": release.commit,
                    "estimated_size": "Cached in WSL",
                    "estimated_files": est_files,
                }
            low_mb = max(35, 35 + round(est_files * 0.015))
            high_mb = max(low_mb + 20, 50 + round(est_files * 0.030))
            ver_parts = [release.branch]
            if release.tag and release.tag != release.branch:
                ver_parts.append(f"tag {release.tag}")
            ver_parts.append(f"build ab{release.build_id}")
            return {
                "needs_download": True,
                "offline": False,
                "is_gki": True,
                "use_wsl": True,
                "mode": "gki_git",
                "kernel_version": info.version_string,
                "repo_label": "AOSP Official GKI via WSL Git (android.googlesource.com/kernel/common)",
                "repo_version": " · ".join(filter(None, ver_parts)),
                "commit": release.commit,
                "estimated_size": f"~{low_mb}–{high_mb} MB (WSL partial Git cache + ~{est_files:,} source files)",
                "estimated_files": est_files,
            }

        pinned_info = replace(info, git_commit=release.commit)
        provider = kernel_source_provider.KernelSourceProvider(pinned_info, offline_mode=True)
        threshold = max(16, min(est_files // 2, provider.max_remote_fetches or 80))
        if provider.cached_file_count(limit=threshold) >= threshold:
            return {
                "needs_download": False,
                "offline": False,
                "is_gki": True,
                "use_wsl": False,
                "kernel_version": info.version_string,
                "repo_label": "AOSP Official GKI via HTTPS (android.googlesource.com/kernel/common)",
                "repo_version": f"{release.display_name} · build ab{release.build_id}",
                "commit": release.commit,
                "estimated_size": "Cached locally",
                "estimated_files": est_files,
            }
        capped = min(est_files, provider.max_remote_fetches or 80)
        est_mb = max(1, round(capped * 0.035))
        ver_parts = [release.branch]
        if release.tag and release.tag != release.branch:
            ver_parts.append(f"tag {release.tag}")
        ver_parts.append(f"build ab{release.build_id}")
        return {
            "needs_download": True,
            "offline": False,
            "is_gki": True,
            "use_wsl": False,
            "mode": "gki_https",
            "kernel_version": info.version_string,
            "repo_label": "AOSP Official GKI via HTTPS (Windows cache, zero WSL writes)",
            "repo_version": " · ".join(filter(None, ver_parts)),
            "commit": release.commit,
            "estimated_size": f"~1–{max(2, est_mb + 2)} MB (up to {capped} pinned reference files)",
            "estimated_files": est_files,
        }

    provider = kernel_source_provider.KernelSourceProvider(info, offline_mode=True)
    repo_label, repo_version = provider.primary_remote_description()
    threshold = max(16, min(est_files // 2, provider.max_remote_fetches or 80))
    if not repo_label or provider.cached_file_count(limit=threshold) >= threshold:
        return {
            "needs_download": False,
            "offline": not bool(repo_label),
            "is_gki": False,
            "use_wsl": False,
            "kernel_version": info.version_string or info.base_version or "Unknown",
            "repo_label": repo_label,
            "repo_version": repo_version,
            "commit": info.git_commit,
            "estimated_size": "Cached locally" if repo_label else "0 MB",
            "estimated_files": est_files,
        }
    capped = min(est_files, provider.max_remote_fetches or 80)
    est_mb = max(1, round(capped * 0.035))
    return {
        "needs_download": True,
        "offline": False,
        "is_gki": False,
        "use_wsl": False,
        "mode": "upstream_https",
        "kernel_version": info.version_string or info.base_version,
        "repo_label": repo_label,
        "repo_version": f"{repo_version} (custom/vendor kernel — upstream reference)",
        "commit": info.git_commit,
        "estimated_size": f"~1–{max(2, est_mb + 2)} MB (up to {capped} reference files on demand)",
        "estimated_files": est_files,
    }


def format_download_prompt(plan: dict) -> str:
    lines = [
        "Matching upstream/GKI kernel sources are not cached locally yet.\n",
        f"• Detected Kernel: {plan.get('kernel_version') or 'Unknown'}",
        f"• Repository: {plan.get('repo_label')}",
        f"• Version / Branch: {plan.get('repo_version')}",
    ]
    commit = plan.get("commit") or ""
    if commit:
        lines.append(f"• Commit: {commit[:16]}{'...' if len(commit) > 16 else ''}")
    lines.extend([
        f"• Estimated Download: {plan.get('estimated_size')}",
        "",
        "What is the difference between the two modes?",
        "• With Source Reference (Download): Downloads matching Google GKI / upstream Linux C files and attaches the stock function underneath each decompiled file (KERNEL SOURCE REFERENCE) so you can compare vendor/OEM changes and prepare Kbuild rebuilds.",
        "• Offline Decompile (Binary Only): Zero download. Still recovers the source tree, kallsyms function names, BTF struct/field types, bug/warn line numbers, kernel.config, and annotated ASM directly from the binary — just without stock comparison code or kernel build headers.",
    ])
    return "\n".join(lines)


def decompile(
    path, *, progress_start=0, progress_end=100, dwarf_reference=None, max_funcs_per_file=5, offline=False, use_wsl=False
):
    from dataclasses import replace
    from core import binary_reconstruct, gki_checkout, kernel_version, wsl_manager
    from core.boot_image import _write_bootconf
    from core.threader_control import report_progress

    path = Path(os.path.abspath(path))
    dest = source_destination(path)
    if dest.is_symlink():
        raise OperationError(f"The reconstruction output is a symbolic link: {dest}")
    span = progress_end - progress_start

    def progress(percent, message):
        report_progress(progress_start + span * percent // 100, message)

    progress(5, "Identifying the kernel build...")
    info = kernel_version.inspect_kernel(path)
    if not info.is_kernel:
        raise OperationError(info.error or "The selected file does not contain a recognized kernel.")
    release = None
    reason = "Offline mode selected."
    if not offline:
        try:
            release, reason = gki_checkout.resolve_release(info)
        except OperationError as error:
            reason = str(error)
    else:
        release = gki_checkout._remembered_release(info)
        if not release:
            reason = "No cached GKI release manifest found while offline."

    source_loader = None
    if release:
        gki_checkout._remember_release(release)
        info = replace(info, git_commit=release.commit)
        if not offline and use_wsl:
            distro = wsl_manager.distro_for_path(str(path))
            progress(10, f"Checking WSL Git environment for GKI build {release.build_id}...")
            if _has_linux_git(distro):
                def source_loader(paths):
                    percent = 35
                    progress(percent, f"Fetching matching source for GKI {release.build_id}...")

                    def fetching(message):
                        nonlocal percent
                        if match := re.match(r"Loading kernel source files (\d+)/(\d+)", message):
                            current, total = map(int, match.groups())
                            percent = 35 + 14 * current // max(total, 1)
                        progress(percent, message)

                    return gki_checkout.fetch_source_files(release, paths, distro=distro, progress=fetching)

    if dest == path.parent / SOURCE_DIR and boot_kernel(path.parent) == path:
        flag = f"{release.build_id}-{release.commit}" if release else ""
        _write_bootconf(path.parent.parent, path.parent.name, "boot", gki=flag)

    def reconstruct_progress(current, total, stage):
        if stage == "Decompiling kernel functions...":
            progress(52 + 36 * current // max(total, 1), f"Decompiling functions · Batch {current}/{total}")
        elif stage == "Indexing DWARF source functions...":
            progress(21 + 13 * current // max(total, 1), stage)
        elif current == 0:
            progress(20, stage)
        else:
            progress(88 + 11 * current // max(total, 1), f"Reconstructing {current}/{total} · {Path(stage).name}")

    result = binary_reconstruct.reconstruct_source_tree(
        str(path), output_dir=str(dest), progress_callback=reconstruct_progress,
        dwarf_reference=dwarf_reference, max_funcs_per_file=max_funcs_per_file,
        source_loader=source_loader, offline=offline, strict_failures=False,
    )
    progress(100, "Kernel reconstruction finished")
    summary = f"{dest}\n{result['total_files']} files and {result['total_functions']} functions."
    if result.get("failed_functions"):
        summary += f"\n({result['failed_functions']} function exports skipped — see reconstruction_manifest.json.)"
    if not release:
        summary += "\n\n" + gki_checkout.vendor_requirements(info, reason)
    return summary


def compile_output(index, total, outcome):
    status = "C and Rust exported" if outcome.get("rust_path") else "C compiled"
    if error := outcome.get("error"):
        status, *diagnostics = error.splitlines()
        first_error = next((line.strip() for line in diagnostics if "error:" in line), "")
        if first_error:
            status += " — " + first_error
    return f"[{index}/{total}] {outcome['path']} — {status}"


def _run_compile_cli():
    from core.binary_cli import run

    def display(index, total, outcome):
        print(compile_output(index, total, outcome), flush=True)

    return run(sys.argv[1:], compile_progress=display, compile_jobs=min(8, os.cpu_count() or 1))


def compile_folder(folder, source, database, *, emit_rust=False, c2rust=None, vmlinux=None):
    from core import gki_checkout, kernel_rebuild, kitchen_process, wsl_manager
    from core.threader_control import report_progress

    c2rust = c2rust if emit_rust else None
    manifest = reconstruction_manifest(folder)
    if not manifest:
        raise OperationError("Select the reconstructed kernel sources folder.")
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise OperationError(f"Could not read the reconstruction manifest: {error}") from error
    if not isinstance(data, dict) or not data.get("kernel_info"):
        raise OperationError("This folder does not contain a kernel reconstruction manifest.")
    report_progress(5, "Checking Kbuild inputs and compiling recovered C...")
    if sys.platform == "win32":
        distros = {distro for endpoint in (manifest, source, database, c2rust, vmlinux) if endpoint
                   if (distro := wsl_manager.distro_for_path(str(endpoint)))}
        if len(distros) > 1:
            raise OperationError("Use a compilation database in the same WSL distribution as the kernel source.")
        distro = next(iter(distros), None)
        gki_checkout.require_linux_tools(distro, "python3")
        prefix = gki_checkout._linux_command(distro)[:-3]
        root = wsl_manager.windows_to_wsl_path(str(Path(__file__).resolve().parents[1]))
        paths = [wsl_manager.windows_to_wsl_path(str(endpoint)) for endpoint in (manifest, source, database)]
        command = [*prefix, "python3", "-c",
                   "import sys; sys.path.insert(0, sys.argv.pop(1)); from core.kernel_workspace import _run_compile_cli; sys.exit(_run_compile_cli())",
                   root, "compile-tree", paths[0], "--kernel-source", paths[1], "--compile-commands", paths[2]]
        if vmlinux:
            command.extend(("--vmlinux", wsl_manager.windows_to_wsl_path(vmlinux)))
        if emit_rust:
            command.append("--emit-rust")
            if c2rust:
                command.extend(("--c2rust", wsl_manager.windows_to_wsl_path(c2rust)))
        details = []
        with kitchen_process.Popen(command, stdout=kitchen_process.PIPE, stderr=kitchen_process.STDOUT,
                                   text=True, encoding="utf-8", errors="replace",
                                   creationflags=wsl_manager._CREATE_NO_WINDOW) as process:
            for line in process.stdout:
                if match := re.match(r"^\[(\d+)/(\d+)\] (.*)", line):
                    done, total, stage = match.groups()
                    if int(done) % 10 == 0 or done == total:
                        report_progress(5 + 94 * int(done) // int(total), stage)
                else:
                    details.append(line)
        if process.returncode:
            detail = "Kernel compilation failed.\n" + "".join(details)
            try:
                result = json.loads(next(line for line in reversed(details) if line.startswith("{")))
            except (StopIteration, ValueError):
                result = {}
            if isinstance(result, dict) and result.get("application_error") is True:
                raise RuntimeError(detail)
            raise OperationError(detail)
    else:
        from core.terminal_execution import library_operation

        with library_operation("Compile Kernel Sources") as log:
            kernel_rebuild.compile_tree(manifest, source, database, emit_rust=emit_rust, c2rust=c2rust,
                                        vmlinux=vmlinux,
                                        jobs=min(8, os.cpu_count() or 1),
                                        progress=lambda index, total, outcome: log(compile_output(index, total, outcome) + "\n"))
    report_progress(100, "Kernel compilation finished")
    return str(manifest.with_name("compile-report.json"))
