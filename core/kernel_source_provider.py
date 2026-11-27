# SPDX-License-Identifier: MPL-2.0
"""Kernel source code provider and anchor matching engine.

Retrieves and caches Linux/AOSP kernel source files based on the detected
KernelVersionInfo. Supports:
1. Local source directory (offline, instant)
2. Local disk cache keyed by repository and revision (~/.rak_kernel_cache/by-origin/...)
3. Remote on-demand fetching via AOSP Gitiles API (Base64) & GitHub / Kernel.org raw
4. Function discovery via string anchors and __bug_table line numbers.
"""
from __future__ import annotations

import base64
import hashlib
from pathlib import Path, PurePosixPath
import re
import tempfile
import urllib.request
from urllib.parse import quote
from typing import Optional, List, Dict, Any

from core.kernel_version import KernelVersionInfo


def get_default_cache_dir() -> Path:
    """Returns the persistent directory for caching kernel source files."""
    home = Path.home()
    cache_base = home / ".rak_kernel_cache"
    cache_base.mkdir(parents=True, exist_ok=True)
    return cache_base


class KernelSourceProvider:
    def __init__(
        self,
        version_info: KernelVersionInfo,
        local_source_dir: Optional[str | Path] = None,
        cache_dir: Optional[str | Path] = None,
        offline_mode: bool = False,
        timeout_seconds: int = 5,
        exact_local_only: bool = False,
        max_remote_fetches: int = 80,
    ):
        self.info = version_info
        self.local_dir: Optional[Path] = Path(local_source_dir) if local_source_dir else None
        self.cache_dir: Path = Path(cache_dir) if cache_dir else get_default_cache_dir()
        self.offline_mode = offline_mode
        self.timeout = timeout_seconds
        self.exact_local_only = exact_local_only
        self.max_remote_fetches = max_remote_fetches
        self._remote_fetches = 0
        self._consecutive_failures = 0

        # In-memory session cache for fast repeat lookups
        self._mem_cache: Dict[str, str] = {}
        self._source_origins: Dict[str, str] = {}

    def cached_file_count(self, limit: int = 32) -> int:
        """Count already-cached files across matching local or remote-origin cache directories."""
        if self.local_dir and self.local_dir.is_dir():
            return limit
        count = 0
        for origin, _ in self._remote_origins():
            scope = hashlib.sha256(origin.encode("ascii")).hexdigest()
            root = self.cache_dir / "by-origin" / scope
            if not root.is_dir():
                continue
            for entry in root.rglob("*"):
                if entry.is_file():
                    count += 1
                    if count >= limit:
                        return count
        return count

    def primary_remote_description(self) -> tuple[str, str]:
        """Return (repo_label, repo_version) for user-facing download confirmation."""
        if re.fullmatch(r"android\d+-\d+\.\d+", self.info.aosp_branch):
            ver = self.info.aosp_branch
            if self.info.git_commit:
                ver += f" · commit {self.info.git_commit[:12]}"
            return "AOSP Common Kernel (android.googlesource.com/kernel/common)", ver
        if self.info.git_commit and self.info.android_release:
            return (
                "AOSP Common Kernel (android.googlesource.com/kernel/common)",
                f"commit {self.info.git_commit[:12]} (base {self.info.base_version})",
            )
        if self.info.base_version:
            return (
                "Upstream Linux Stable (github.com/gregkh/linux)",
                f"v{self.info.base_version} / {self.info.aosp_branch or ('linux-' + self.info.short_version + '.y')}",
            )
        return "", ""

    def source_origin(self, rel_path: str) -> Optional[str]:
        """Describe the file actually loaded, including a fallback's real ref."""
        return self._source_origins.get(PurePosixPath(rel_path.replace("\\", "/")).as_posix())

    def get_source_file(self, rel_path: str) -> Optional[str]:
        """Resolves and returns the full text of a source file by relative path."""
        relative = PurePosixPath(rel_path.replace("\\", "/").strip())
        if relative.is_absolute() or not relative.parts or any(
            part == ".." or ":" in part for part in relative.parts
        ):
            return None
        norm_path = relative.as_posix()

        # 1. Check in-memory cache
        if norm_path in self._mem_cache:
            return self._mem_cache[norm_path]

        # 2. Check local source directory
        if self.local_dir and self.local_dir.exists():
            local_file = self.local_dir.joinpath(*relative.parts)
            if local_file.is_file():
                try:
                    content = local_file.read_text(encoding="utf-8", errors="replace")
                    self._mem_cache[norm_path] = content
                    self._source_origins[norm_path] = str(local_file.resolve())
                    return content
                except Exception:
                    pass

        if self.exact_local_only:
            return None

        # Preserve commit priority even when a moving branch already has cached content.
        for origin, gitiles in self._remote_origins():
            scope = hashlib.sha256(origin.encode("ascii")).hexdigest()
            cache_file = self.cache_dir / "by-origin" / scope / Path(*relative.parts)
            url = origin + quote(norm_path) + ("?format=TEXT" if gitiles else "")
            try:
                content = cache_file.read_text(encoding="utf-8")
            except (OSError, UnicodeError):
                if (
                    self.offline_mode
                    or self._consecutive_failures >= 4
                    or (self.max_remote_fetches and self._remote_fetches >= self.max_remote_fetches)
                ):
                    continue
                content = self._fetch_remote(url, gitiles)
                if content is None:
                    continue
                self._remote_fetches += 1
                self._consecutive_failures = 0
                self._cache_source(cache_file, content)
            self._mem_cache[norm_path] = content
            self._source_origins[norm_path] = url
            return content

        if not self.offline_mode:
            self._consecutive_failures += 1
        return None

    def _remote_origins(self):
        common = "https://android.googlesource.com/kernel/common/+/"
        if re.fullmatch(r"[0-9a-fA-F]{7,40}", self.info.git_commit):
            yield common + self.info.git_commit + "/", True
        if re.fullmatch(r"android\d+-\d+\.\d+", self.info.aosp_branch):
            yield common + "refs/heads/" + self.info.aosp_branch + "/", True
        if re.fullmatch(r"\d+\.\d+\.\d+", self.info.base_version):
            yield f"https://raw.githubusercontent.com/gregkh/linux/v{self.info.base_version}/", False
        if re.fullmatch(r"\d+\.\d+", self.info.short_version):
            yield f"https://raw.githubusercontent.com/torvalds/linux/v{self.info.short_version}/", False
            yield f"https://raw.githubusercontent.com/gregkh/linux/linux-{self.info.short_version}.y/", False

    def _fetch_remote(self, url: str, gitiles: bool) -> Optional[str]:
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": "RAK/KernelSourceProvider", "Accept": "*/*"},
            )
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read()
                return (base64.b64decode(raw) if gitiles else raw).decode("utf-8", errors="replace")
        except (OSError, ValueError):
            return None

    @staticmethod
    def _cache_source(path: Path, content: str) -> None:
        temporary = None
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(content)
            temporary.replace(path)
        except OSError:
            pass
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def find_function_by_line(self, rel_path: str, line_no: int) -> Optional[Dict[str, Any]]:
        """Uses line number (e.g. from __bug_table) to pinpoint the exact function in upstream source."""
        content = self.get_source_file(rel_path)
        if not content:
            return None

        lines = content.splitlines()
        if not (1 <= line_no <= len(lines)):
            return None

        target_idx = line_no - 1  # 0-indexed

        # Scan backwards to locate the opening function definition
        func_start_idx = None
        func_name = None
        sig_lines = []

        # Find the function boundary: look for a top-level function signature (starting at col 0)
        func_sig_re = re.compile(
            r"^(?:(?:static|inline|__init|__exit|asmlinkage|void|int|long|bool|u\d+|s\d+|struct\s+[a-zA-Z0-9_]+)\s+[*&]*)+"
            r"([a-zA-Z0-9_]+)\s*\("
        )

        for idx in range(target_idx, max(-1, target_idx - 250), -1):
            line = lines[idx]
            match = func_sig_re.match(line)
            if match:
                func_start_idx = idx
                func_name = match.group(1)
                break
            elif line.startswith("{") and idx > 0:
                prev = lines[idx - 1]
                match2 = func_sig_re.match(prev)
                if match2:
                    func_start_idx = idx - 1
                    func_name = match2.group(1)
                    break

        if func_start_idx is None:
            return None

        # Scan forwards from func_start_idx to find the closing brace
        brace_count = 0
        func_end_idx = func_start_idx
        started = False

        for idx in range(func_start_idx, min(len(lines), func_start_idx + 800)):
            l = lines[idx]
            brace_count += l.count("{") - l.count("}")
            if "{" in l:
                started = True
            if started and brace_count <= 0:
                func_end_idx = idx
                break

        func_lines = lines[func_start_idx : func_end_idx + 1]
        signature = lines[func_start_idx].strip()

        return {
            "name": func_name,
            "signature": signature,
            "start_line": func_start_idx + 1,
            "end_line": func_end_idx + 1,
            "target_line": line_no,
            "source_code": "\n".join(func_lines),
        }

    def find_function_by_anchor_string(self, rel_path: str, anchor: str) -> Optional[Dict[str, Any]]:
        """Scans the source file for anchor string and finds the enclosing function."""
        content = self.get_source_file(rel_path)
        if not content:
            return None

        lines = content.splitlines()
        clean_anchor = anchor.strip().strip('"').strip("'")
        if not clean_anchor or len(clean_anchor) < 4:
            return None

        matched_line_no = None
        for i, line in enumerate(lines, 1):
            if clean_anchor in line:
                matched_line_no = i
                break

        if matched_line_no:
            return self.find_function_by_line(rel_path, matched_line_no)

        return None
