# SPDX-License-Identifier: MPL-2.0
import zipfile
import shutil
import tempfile
import requests

import os
import json
import hashlib
import hmac
from enum import Enum
from urllib.parse import urlparse
from core.archive_safety import safe_extract_zip
from core.app_paths import BIN_DIR


PROJECT_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..")
)

BIN_ROOT = BIN_DIR


REMOTE_REGISTRY_URL = (
    "https://raw.githubusercontent.com/"
    "ahsanihlwn/auth/refs/heads/main/packages.json"
)

# Large Android toolchains are legitimate, but an unbounded download lets a
# hostile/mistaken registry URL exhaust user bandwidth and disk.
MAX_ARCHIVE_DOWNLOAD_BYTES = 2 * 1024 * 1024 * 1024

LOCAL_CACHE_JSON = os.path.join(BIN_ROOT, "package.json")

print("[DependencyManager] registry source: REMOTE")
print("[DependencyManager] registry url:", REMOTE_REGISTRY_URL)


class PackageState(Enum):
    INSTALLED = "installed"
    NOT_INSTALLED = "not_installed"
    UPDATE_AVAILABLE = "update_available"
    ANOMALY = "anomaly"


class DependencyManager:
    def __init__(self, allow_remote=True):
        self.local_cache = self._load_json(LOCAL_CACHE_JSON)
        self.registry = {}

        if allow_remote:
            try:
                self.registry = self._load_remote_registry()
            except Exception:
                self.registry = {}


    def _load_json(self, path):
        if not os.path.exists(path):
            return {}
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def _load_remote_registry(self):
        self._validate_https_url(REMOTE_REGISTRY_URL, "registry")
        with requests.get(REMOTE_REGISTRY_URL, timeout=(10, 30)) as response:
            response.raise_for_status()
            self._validate_https_url(response.url, "redirected registry")
            return response.json()


    def list_packages(self):
        result = {}

        # 1️⃣ local dulu = sumber kebenaran
        for pkg_name, local_meta in self.local_cache.items():
            result[pkg_name] = self._resolve_package(
                pkg_name,
                self.registry.get(pkg_name, {})  # bisa kosong
            )

        # 2️⃣ remote cuma nambah / update info
        for pkg_name, meta in self.registry.items():
            if pkg_name not in result:
                result[pkg_name] = self._resolve_package(pkg_name, meta)

        return list(result.values())

    def _merge_meta(self, local_meta, registry_meta):
        # registry menang kalo ada (online)
        if registry_meta:
            return registry_meta
        # fallback ke local (offline)
        return local_meta or {}


    def _resolve_package(self, pkg_name, registry_meta):
        local_meta = self.local_cache.get(pkg_name)
        meta = self._merge_meta(local_meta, registry_meta)

        # belum pernah diinstall / schema lama
        if not local_meta or "binaries" not in local_meta:
            return {
                "name": pkg_name,
                "display": meta.get("display", pkg_name),
                "author": meta.get("author"),
                "license": meta.get("license"),
                "version": meta.get("version"),
                "state": PackageState.NOT_INSTALLED.value,
                "binaries": []
            }

        pkg_dir = os.path.join(BIN_ROOT, pkg_name)
        binaries_info = []
        anomaly = False

        for b in meta.get("binaries", []):
            bin_name = b["name"]
            bin_path = os.path.join(pkg_dir, bin_name)
            if not os.path.isfile(bin_path):
                bin_path = self._find_file_recursive(pkg_dir, bin_name) or bin_path
            exists = os.path.exists(bin_path)
            actual_size = os.path.getsize(bin_path) if exists else None

            local_bin = next(
                (x for x in local_meta["binaries"] if x["name"] == bin_name),
                None
            )

            expected_size = local_bin["size"] if local_bin else None

            if not exists or local_bin is None or actual_size != expected_size:
                anomaly = True

            binaries_info.append({
                "name": bin_name,
                "exists": exists,
                "expected_size": expected_size,
                "actual_size": actual_size
            })


        remote_version = registry_meta.get("version")
        local_version = local_meta.get("version")

        if not local_meta or "binaries" not in local_meta:
            state = PackageState.NOT_INSTALLED

        elif remote_version and local_version != remote_version:
            state = PackageState.UPDATE_AVAILABLE

        elif anomaly:
            state = PackageState.ANOMALY

        else:
            state = PackageState.INSTALLED


        return {
            "name": pkg_name,
            "display": meta.get("display", pkg_name),
            "author": meta.get("author"),
            "license": meta.get("license"),
            "version": meta.get("version"),
            "state": state.value,
            "binaries": binaries_info
        }

    @staticmethod
    def _validate_https_url(url: str, label: str) -> None:
        parsed = urlparse(url)
        if (parsed.scheme.lower() != "https" or not parsed.hostname or
                parsed.username is not None or parsed.password is not None):
            raise RuntimeError(f"{label} must use a plain HTTPS URL")

    def _download_zip(self, url: str, expected_sha256: str | None = None) -> str:

        self._validate_https_url(url, "package download")
        if expected_sha256 is not None:
            if not isinstance(expected_sha256, str) or len(expected_sha256) != 64:
                raise RuntimeError("Package sha256 must be a 64-character hexadecimal value")
            try:
                int(expected_sha256, 16)
            except ValueError as exc:
                raise RuntimeError("Package sha256 must be a 64-character hexadecimal value") from exc

        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".zip")
        tmp_path = tmp.name
        digest = hashlib.sha256()
        downloaded = 0
        try:
            with requests.get(url, stream=True, timeout=(10, 60)) as response:
                response.raise_for_status()
                self._validate_https_url(response.url, "redirected package download")
                declared_size = response.headers.get("Content-Length")
                if declared_size is not None:
                    try:
                        if int(declared_size) > MAX_ARCHIVE_DOWNLOAD_BYTES:
                            raise RuntimeError("Package archive exceeds the download size limit")
                    except ValueError as exc:
                        raise RuntimeError("Package server returned an invalid Content-Length") from exc
                for chunk in response.iter_content(1024 * 1024):
                    if not chunk:
                        continue
                    downloaded += len(chunk)
                    if downloaded > MAX_ARCHIVE_DOWNLOAD_BYTES:
                        raise RuntimeError("Package archive exceeds the download size limit")
                    digest.update(chunk)
                    tmp.write(chunk)
            tmp.close()
            if expected_sha256 and not hmac.compare_digest(digest.hexdigest(), expected_sha256.lower()):
                raise RuntimeError("Package SHA-256 mismatch; installation aborted")
            return tmp_path
        except Exception:
            tmp.close()
            try:
                os.remove(tmp_path)
            except OSError:
                pass
            raise

    def _find_file_recursive(self, root: str, filename: str) -> str | None:
        target = filename.lower()
        for base, _, files in os.walk(root):
            for f in files:
                if f.lower() == target:
                    return os.path.join(base, f)
        return None

    def install_package(self, pkg_name: str):
        meta = self.registry.get(pkg_name) or self.local_cache.get(pkg_name)

        if "url" not in meta:
            raise RuntimeError("Offline: download URL not available")

        url = meta["url"]
        binaries = meta.get("binaries", [])
        target_dir = os.path.join(BIN_ROOT, pkg_name)

        zip_path = None
        extract_root = None
        backup_dir = None
        try:


            expected_sha256 = meta.get("sha256") or meta.get("archive_sha256")
            zip_path = self._download_zip(url, expected_sha256)
            extract_root = self._extract_zip(zip_path)

            # Validate the staged archive before changing a working install.
            missing = [
                b["name"] for b in binaries
                if not self._find_file_recursive(extract_root, b["name"])
            ]
            if missing:
                raise RuntimeError(
                    f"Missing binaries for {pkg_name}: {', '.join(missing)}"
                )

            # Keep a previous package until the complete staged package has
            # passed validation. A broken update must not delete a usable tool.
            os.makedirs(BIN_ROOT, exist_ok=True)
            if os.path.isdir(target_dir):
                backup_dir = tempfile.mkdtemp(prefix=f".{pkg_name}-backup-", dir=BIN_ROOT)
                os.rmdir(backup_dir)
                shutil.move(target_dir, backup_dir)
            try:
                shutil.move(extract_root, target_dir)
                extract_root = None
            except Exception:
                if backup_dir and os.path.exists(backup_dir):
                    shutil.move(backup_dir, target_dir)
                    backup_dir = None
                raise
        finally:
            if zip_path:
                try:
                    os.remove(zip_path)
                except OSError:
                    pass
            if extract_root:
                shutil.rmtree(extract_root, ignore_errors=True)

        # update local cache
        self.local_cache[pkg_name] = {
            "version": meta.get("version"),
            "binaries": [
                {"name": b["name"], "size": os.path.getsize(self._find_file_recursive(target_dir, b["name"]))}
                for b in binaries
            ]
        }

        with open(LOCAL_CACHE_JSON, "w", encoding="utf-8") as f:
            json.dump(self.local_cache, f, indent=2)

        if backup_dir:
            shutil.rmtree(backup_dir, ignore_errors=True)

    def _extract_zip(self, zip_path: str) -> str:
        extract_dir = tempfile.mkdtemp()
        try:


            safe_extract_zip(
                zip_path, extract_dir,
                max_members=50_000,
                max_uncompressed_bytes=1024 * 1024 * 1024,
            )
            return extract_dir
        except Exception:
            shutil.rmtree(extract_dir, ignore_errors=True)
            raise
