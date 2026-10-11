# SPDX-License-Identifier: MPL-2.0
from __future__ import annotations

import ast
import asyncio
import hashlib
import json
import os
import shutil
import tempfile
import zipfile
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import urlopen

from core.archive_safety import UnsafeArchiveError, safe_extract_zip
from core.dependency_manager import DependencyManager
import core.dependency_manager as dependency_module
from core.rak_fs_tools import run_command
from core.rennsproject_bridge import append_private_security_checks
from services.process_manager import ServiceProcessManager
from services.protocol import handshake_mac


def _no_shell_true(module_path: Path) -> bool:
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        for keyword in node.keywords:
            if (keyword.arg == "shell" and isinstance(keyword.value, ast.Constant) and
                    keyword.value.value is True):
                return False
    return True


def run_security_check() -> tuple[bool, str]:

    outcomes: list[tuple[str, bool, str]] = []

    # A crafted member must never materialize beside the extraction target.
    temp_root = tempfile.mkdtemp(prefix="rak_security_")
    try:
        archive_path = os.path.join(temp_root, "traversal.zip")
        extract_dir = os.path.join(temp_root, "extract")
        escaped = os.path.join(temp_root, "escaped.txt")
        with zipfile.ZipFile(archive_path, "w") as archive:
            archive.writestr("../../escaped.txt", "must not be written")
        try:
            safe_extract_zip(archive_path, extract_dir)
            archive_ok = False
        except UnsafeArchiveError:
            archive_ok = not os.path.exists(escaped)
        outcomes.append(("archive traversal rejection", archive_ok, "ZIP extraction guard"))
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)

    # Official vendors do not always publish SHA-256 values, so HTTPS remains
    # a supported baseline while non-TLS package URLs are never accepted.
    try:
        DependencyManager._validate_https_url("http://vendor.example/package.zip", "test")
        http_rejected = False
    except RuntimeError:
        http_rejected = True
    try:
        DependencyManager._validate_https_url("https://vendor.example/package.zip", "test")
        https_accepted = True
    except RuntimeError:
        https_accepted = False
    outcomes.append(("package HTTPS policy", http_rejected and https_accepted,
                     "official HTTPS allowed; HTTP denied"))

    # A malformed update must fail while leaving a previously working package
    # in place. Exercise the real install staging path in a private temp root.
    package_root = tempfile.mkdtemp(prefix="rak_package_security_")
    prior_bin_root = dependency_module.BIN_ROOT
    prior_cache_path = dependency_module.LOCAL_CACHE_JSON
    try:
        dependency_module.BIN_ROOT = os.path.join(package_root, "bin")
        dependency_module.LOCAL_CACHE_JSON = os.path.join(package_root, "package.json")
        target = os.path.join(dependency_module.BIN_ROOT, "demo")
        os.makedirs(target, exist_ok=True)
        old_binary = os.path.join(target, "working.exe")
        Path(old_binary).write_bytes(b"known-good")
        invalid_update = os.path.join(package_root, "invalid.zip")
        with zipfile.ZipFile(invalid_update, "w") as archive:
            archive.writestr("other.exe", b"not the declared binary")
        manager = DependencyManager(allow_remote=False)
        manager.registry = {
            "demo": {
                "url": "https://vendor.example/demo.zip",
                "binaries": [{"name": "required.exe"}],
            }
        }
        manager._download_zip = lambda _url, _hash: invalid_update
        try:
            manager.install_package("demo")
            staged_rejected = False
        except RuntimeError:
            staged_rejected = os.path.isfile(old_binary)
        outcomes.append(("package transactional update", staged_rejected,
                         "invalid update preserves prior package"))
    finally:
        dependency_module.BIN_ROOT = prior_bin_root
        dependency_module.LOCAL_CACHE_JSON = prior_cache_path
        shutil.rmtree(package_root, ignore_errors=True)

    append_private_security_checks(outcomes)

    proof = handshake_mac("unit-token", 43210, 1234)
    handshake_ok = proof == handshake_mac("unit-token", 43210, 1234) and proof != handshake_mac("other", 43210, 1234)
    outcomes.append(("IPC handshake MAC", handshake_ok, "tamper-resistant discovery"))

    # The service must require the token over a real loopback WebSocket, not
    # merely expose a validation helper that never gets used.
    manager = ServiceProcessManager()
    try:
        port = manager.start(timeout=10)

        async def _probe_service():
            import websockets
            endpoint = f"ws://127.0.0.1:{port}"
            async with websockets.connect(endpoint) as ws:
                await ws.send(json.dumps({"id": "bad", "op": "system.ping", "token": "wrong"}))
                rejected = json.loads(await ws.recv()).get("error") == "unauthorized"
            async with websockets.connect(endpoint) as ws:
                await ws.send(json.dumps({"id": "good", "op": "system.ping", "token": manager.token}))
                accepted = json.loads(await ws.recv()).get("ok") is True
            return rejected and accepted

        service_ok = asyncio.run(_probe_service())
        outcomes.append(("loopback service auth", service_ok, "rejects bad token, accepts valid token"))
    except Exception as exc:
        outcomes.append(("loopback service auth", False, f"{type(exc).__name__}: {exc}"))
    finally:
        manager.stop()

    agent_shell = run_command("echo should-not-run")
    agent_shell_ok = (not agent_shell.get("ok") and
                      "disabled by default" in agent_shell.get("error", ""))
    outcomes.append(("agent shell default-deny", agent_shell_ok, "structured RAK commands remain available"))

    project_root = Path(__file__).resolve().parent.parent
    shell_modules = (
        "core/erofs.py", "core/fsdetect.py", "core/super_logic.py",
        "core/rak_fs_tools.py",
    )
    shell_ok = all(_no_shell_true(project_root / item) for item in shell_modules)
    outcomes.append(("external tool argv", shell_ok, "no shell interpreter"))

    ok = all(passed for _, passed, _ in outcomes)
    lines = [f"{'PASS' if passed else 'FAIL'}  {name}: {detail}" for name, passed, detail in outcomes]
    lines.append(f"Security check: {sum(1 for _, passed, _ in outcomes if passed)}/{len(outcomes)} passed")
    return ok, "\n".join(lines)
