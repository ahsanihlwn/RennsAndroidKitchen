# SPDX-License-Identifier: MPL-2.0
from __future__ import annotations

import os
import ntpath
import posixpath
import json
import re
import shutil
import subprocess
import sys
import tempfile

# Hide the console window the child process would otherwise flash on Windows.
_CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


def _wsl_path() -> str | None:

    if sys.platform != "win32":
        return None
    found = shutil.which("wsl.exe") or shutil.which("wsl")
    if found:
        return found
    # shutil.which can miss it if PATH is odd; check the canonical location.
    sysroot = os.environ.get("SystemRoot", r"C:\Windows")
    cand = os.path.join(sysroot, "System32", "wsl.exe")
    return cand if os.path.isfile(cand) else None


def _decode_output(data: bytes) -> str:
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16", errors="replace").strip()
    encoding = "utf-16-le" if b"\x00" in data else "utf-8-sig"
    return data.decode(encoding, errors="replace").strip()


def _run(args: list[str], timeout: int = 20) -> tuple[int | None, str]:

    exe = _wsl_path()
    if not exe:
        return None, "wsl.exe not found"
    try:
        p = subprocess.run(
            [exe, *args],
            capture_output=True,
            creationflags=_CREATE_NO_WINDOW,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return None, "timed out"
    except Exception as exc:  # pragma: no cover - defensive
        return None, str(exc)
    out = _decode_output(p.stdout)
    err = _decode_output(p.stderr)
    return p.returncode, out or err


def _parse_version(text: str) -> str:

    m = re.search(r"WSL version:\s*([0-9][0-9.]*)", text)
    return m.group(1) if m else ""


def list_distros() -> list[str]:

    rc, out = _run(["-l", "-q"])
    if rc is None or rc != 0 or not out:
        return []
    distros = []
    for line in out.splitlines():
        # -l -q can still emit NUL padding between glyphs on some builds; the
        # utf-16-le decode already handled that, so just clean stray controls.
        name = line.replace("\x00", "").strip()
        if name:
            distros.append(name)
    return distros


def distro_for_path(path: str) -> str | None:

    parts = str(path).replace("\\", "/").split("/")
    if len(parts) >= 4 and parts[:2] == ["", ""]:
        if parts[2].casefold() in ("wsl$", "wsl.localhost") and parts[3]:
            return parts[3]
    return None


def windows_to_wsl_path(path: str) -> str:

    path = str(path)
    if not path:
        raise ValueError("A terminal path is required.")
    if distro_for_path(path):
        parts = path.replace("\\", "/").split("/")
        return posixpath.normpath("/" + "/".join(parts[4:]))
    if path.startswith("/") and not path.startswith("//"):
        return posixpath.normpath(path)
    path = ntpath.normpath(path)
    drive, tail = ntpath.splitdrive(path)
    if re.fullmatch(r"[A-Za-z]:", drive) and tail.startswith("\\"):
        return "/mnt/" + drive[0].lower() + tail.replace("\\", "/")
    if not drive and not path.startswith("\\"):
        # Relative dropped filenames remain relative to the Linux shell cwd.
        return path.replace("\\", "/")
    raise ValueError(f"This Windows path cannot be mapped to WSL: {path}")


def terminal_is_ready() -> bool:

    status = detect()
    if not status["installed"] or not status["has_distro"]:
        return False
    rc, _ = _run(["--exec", "/bin/sh", "-c", "exit 0"], timeout=8)
    return rc == 0


def project_storage_key(path: str) -> str:

    normalized = ntpath.normpath(str(path or ""))
    if normalized.startswith("\\\\"):
        parts = normalized[2:].split("\\")
        if len(parts) >= 2 and parts[0].casefold() in ("wsl$", "wsl.localhost") and parts[1]:
            return f"WSL:{parts[1]}"
    return ntpath.splitdrive(normalized)[0]


def _parse_disk_usage(text: str) -> dict | None:
    # -P keeps each filesystem on one line; -k makes sizes explicit KiB.
    for line in reversed(text.splitlines()):
        fields = line.split()
        if len(fields) < 6 or fields[-1] != "/":
            continue
        try:
            total, used, available = (int(value) * 1024 for value in fields[-5:-2])
            percent = int(fields[-2].removesuffix("%"))
        except ValueError:
            continue
        if (total > 0 and 0 <= used <= total and available >= 0
                and fields[-2].endswith("%") and 0 <= percent <= 100):
            return {"total": total, "used": used, "available": available, "percent": percent}
    return None


def list_disk_usage() -> list[dict]:

    result = []
    for distro in dict.fromkeys(list_distros()):
        rc, output = _run(["--distribution", distro, "--exec", "df", "-P", "-k", "/"], timeout=15)
        usage = _parse_disk_usage(output) if rc == 0 else None
        if usage is not None:
            result.append({"distro": distro, "key": f"WSL:{distro}", **usage})
    return result


def _disk_cache_path() -> str:
    from core.app_paths import CACHE_DIR
    return os.path.join(CACHE_DIR, "wsl_disk_usage.json")


def load_disk_usage_cache(path: str | None = None) -> list[dict]:
    try:
        with open(path or _disk_cache_path(), encoding="utf-8") as stream:
            data = json.load(stream)
        result = []
        for item in data["disks"]:
            distro = item["distro"]
            total, used, available, percent = (item[key] for key in ("total", "used", "available", "percent"))
            if (not isinstance(distro, str) or not distro.strip()
                    or not all(type(value) is int for value in (total, used, available, percent))
                    or not (total > 0 and 0 <= used <= total and available >= 0 and 0 <= percent <= 100)):
                continue
            result.append({"distro": distro, "key": f"WSL:{distro}", "total": total,
                           "used": used, "available": available, "percent": percent})
        return result
    except (OSError, ValueError, KeyError, TypeError):
        return []


def save_disk_usage_cache(disks: list[dict], path: str | None = None) -> None:
    target = path or _disk_cache_path()
    temporary = None
    try:
        parent = os.path.dirname(os.path.abspath(target))
        os.makedirs(parent, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=parent, delete=False) as stream:
            temporary = stream.name
            json.dump({"disks": disks}, stream, ensure_ascii=False)
        os.replace(temporary, target)
        temporary = None
    except OSError:
        pass  # Cache availability must not prevent the fresh result reaching UI.
    finally:
        if temporary is not None:
            try:
                os.unlink(temporary)
            except OSError:
                pass


def detect() -> dict:

    exe = _wsl_path()
    if not exe:
        return {
            "installed": False, "present": False, "version": "",
            "has_distro": False, "distros": [],
            "detail": "WSL not available on this Windows build",
        }

    # `wsl --version` is the cleanest signal on modern (Store) WSL. On old
    # inbox-only WSL it fails; fall back to `--status`.
    rc_v, out_v = _run(["--version"])
    version = _parse_version(out_v) if rc_v == 0 else ""

    rc_s, out_s = _run(["--status"])
    status_ok = rc_s == 0 and bool(out_s)

    # Either a clean --version or a clean --status means WSL is usable.
    usable = (rc_v == 0) or status_ok

    distros = list_distros() if usable else []

    if not usable:
        detail = "WSL component present but not enabled"
    elif distros:
        ver = f" {version}" if version else ""
        detail = f"WSL{ver} · {', '.join(distros)}"
    else:
        ver = f" {version}" if version else ""
        detail = f"WSL{ver} installed · no Linux distro yet"

    return {
        "installed": usable, "present": True, "version": version,
        "has_distro": bool(distros), "distros": distros, "detail": detail,
    }


def install_command() -> list[str]:

    return ["--install", "--no-launch"]


def install(timeout: int = 600) -> tuple[bool, str]:

    exe = _wsl_path()
    if not exe:
        return False, "WSL is not available on this Windows build."
    rc, out = _run(install_command(), timeout=timeout)
    if rc == 0:
        return True, out or "WSL install started. A reboot may be required."
    if rc is None:
        return False, out or "Could not start the WSL installer."
    return False, out or f"WSL install failed (exit {rc})."
