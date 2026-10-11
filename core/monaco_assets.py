# SPDX-License-Identifier: MPL-2.0
import os
import pathlib
import sys

REQUIRED_FILES = (
    ("loader.js",),
    ("editor", "editor.main.js"),
    ("editor", "editor.main.css"),
    ("base", "worker", "workerMain.js"),
)


def _validate_vs_dir(path: pathlib.Path) -> bool:
    if not path.is_dir():
        return False
    return all((path.joinpath(*parts)).is_file() for parts in REQUIRED_FILES)


def resolve_monaco_vs() -> pathlib.Path:

    candidates = []

    file_root = pathlib.Path(__file__).resolve().parent.parent
    candidates.append(file_root / "assets" / "monaco" / "vs")

    if "__compiled__" in globals() or getattr(sys, "frozen", False):
        exe_dir = pathlib.Path(sys.executable).resolve().parent
        candidates.append(exe_dir / "assets" / "monaco" / "vs")

    for cand in candidates:
        if _validate_vs_dir(cand):
            return cand.resolve()

    checked_paths = "\n  - ".join(str(c) for c in candidates)
    req_files = "\n  - ".join("/".join(p) for p in REQUIRED_FILES)
    raise FileNotFoundError(
        "Monaco Editor assets are missing. Required files:\n"
        f"  - {req_files}\n"
        f"Checked locations:\n  - {checked_paths}\n"
        "Please run 'python tools/vendor_monaco.py' to download and vendor local Monaco assets."
    )


def resolve_monaco_base() -> pathlib.Path:

    return resolve_monaco_vs().parent


def monaco_vs_url() -> str:

    return resolve_monaco_vs().as_uri()
