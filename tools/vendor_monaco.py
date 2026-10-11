#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0


import base64
import hashlib
import io
import json
import pathlib
import sys
import tarfile
import urllib.request

PKG_NAME = "monaco-editor"
PKG_VER = "0.44.0"
REGISTRY_URL = f"https://registry.npmjs.org/{PKG_NAME}/{PKG_VER}"
REQUIRED_FILES = (
    ("loader.js",),
    ("editor", "editor.main.js"),
    ("editor", "editor.main.css"),
    ("base", "worker", "workerMain.js"),
)
CORE_FILES = REQUIRED_FILES


def vendor_monaco(workspace_root: pathlib.Path = None) -> None:
    if workspace_root is None:
        workspace_root = pathlib.Path(__file__).resolve().parent.parent
    monaco_dir = (workspace_root / "assets" / "monaco").resolve()
    vs_dir = (monaco_dir / "vs").resolve()

    prov_file = monaco_dir / "provenance.json"
    if prov_file.is_file() and vs_dir.is_dir() and all((vs_dir.joinpath(*cf)).is_file() for cf in REQUIRED_FILES):
        try:
            prov_data = json.loads(prov_file.read_text(encoding="utf-8"))
            if prov_data.get("version") == PKG_VER:
                files = [p for p in vs_dir.rglob("*") if p.is_file()]
                total_size = sum(p.stat().st_size for p in files)
                print(f"Skipping download: Monaco {PKG_VER} already present ({len(files)} files, {total_size} bytes)")
                return
        except Exception:
            pass

    print(f"Fetching metadata: {REGISTRY_URL}")
    req = urllib.request.Request(REGISTRY_URL, headers={"User-Agent": "rak-vendor/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        meta = json.loads(resp.read().decode("utf-8"))

    dist = meta["dist"]
    tarball_url = dist["tarball"]
    integrity = dist["integrity"]

    algo, expected_b64 = integrity.split("-", 1)
    if algo.lower() != "sha512":
        raise ValueError(f"Integrity check requires sha512, got: {algo}")

    print(f"Downloading tarball: {tarball_url}")
    tb_req = urllib.request.Request(tarball_url, headers={"User-Agent": "rak-vendor/1.0"})
    with urllib.request.urlopen(tb_req, timeout=30) as resp:
        tb_bytes = resp.read()

    hasher = hashlib.new(algo)
    hasher.update(tb_bytes)
    if hasher.digest() != base64.b64decode(expected_b64):
        raise ValueError(f"Integrity check failed for {tarball_url}")

    vs_dir.mkdir(parents=True, exist_ok=True)
    count = 0
    total_size = 0

    with tarfile.open(fileobj=io.BytesIO(tb_bytes), mode="r:*") as tar:
        for member in tar.getmembers():
            if not member.isfile() or member.issym() or member.islnk():
                continue
            name = member.name.replace("\\", "/")
            target = None
            if name.startswith("package/min/vs/"):
                target = vs_dir / name[len("package/min/vs/"):]
            elif name.startswith("package/") and any(
                pathlib.PurePosixPath(name).name.lower().startswith(k)
                for k in ("license", "thirdpartynotices")
            ):
                target = vs_dir / pathlib.PurePosixPath(name).name

            if target is not None:
                resolved = target.resolve()
                try:
                    resolved.relative_to(monaco_dir)
                except ValueError:
                    raise ValueError(f"Path traversal detected: {name} -> {target}")
                resolved.parent.mkdir(parents=True, exist_ok=True)
                f_in = tar.extractfile(member)
                if f_in is not None:
                    content = f_in.read()
                    resolved.write_bytes(content)
                    count += 1
                    total_size += len(content)

    missing = [parts for parts in REQUIRED_FILES if not vs_dir.joinpath(*parts).is_file()]
    if missing:
        missing_str = ", ".join("/".join(p) for p in missing)
        raise RuntimeError(f"Vendoring incomplete, missing required files: {missing_str}")

    prov = {
        "name": PKG_NAME,
        "version": PKG_VER,
        "tarball": tarball_url,
        "integrity": integrity,
        "license": meta.get("license", "MIT"),
    }
    (monaco_dir / "provenance.json").write_text(json.dumps(prov, indent=2), encoding="utf-8")
    print(f"Vendored Monaco {PKG_VER}: {count} files, {total_size} bytes")


if __name__ == "__main__":
    vendor_monaco()
