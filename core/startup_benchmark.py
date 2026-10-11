# SPDX-License-Identifier: MPL-2.0
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import time


_ROOT = Path(__file__).resolve().parent.parent


def _probe(label: str, code: str) -> tuple[bool, str]:
    env = os.environ.copy()
    env.setdefault("QT_QPA_PLATFORM", "offscreen")
    env.setdefault("QTWEBENGINE_DISABLE_SANDBOX", "1")
    env.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu --no-sandbox")

    started = time.perf_counter()
    try:
        result = subprocess.run(
            [sys.executable, "-c", code],
            cwd=str(_ROOT),
            env=env,
            capture_output=True,
            text=True,
            timeout=45,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return False, f"{label}: timed out after 45s"

    elapsed_ms = (time.perf_counter() - started) * 1000
    if result.returncode:
        detail = (result.stderr or result.stdout).strip().splitlines()
        last_line = detail[-1] if detail else f"exit {result.returncode}"
        return False, f"{label}: failed ({last_line})"
    return True, f"{label}: {elapsed_ms:.0f}ms"


def run_startup_benchmark() -> tuple[bool, str]:

    probes = (
        (
            "cold editor module import",
            "import main",
        ),
        (
            "My Projects shell construction",
            "from PySide6.QtWidgets import QApplication; "
            "from app import AppUI; "
            "qt_app = QApplication.instance() or QApplication([]); "
            "ui = AppUI(offscreen_mode=True); "
            "ui.deleteLater()",
        ),
    )
    outcomes = [_probe(label, code) for label, code in probes]
    ok = all(passed for passed, _ in outcomes)
    lines = ["Startup benchmark (read-only, cold subprocesses):"]
    lines.extend(f"{'PASS' if passed else 'FAIL'}  {message}" for passed, message in outcomes)
    return ok, "\n".join(lines)
