# SPDX-License-Identifier: MPL-2.0
"""Load and structure verbatim project (MPL-2.0) and embedded (MIT) license texts."""
from __future__ import annotations

from functools import lru_cache
import html
from pathlib import Path
import re
import sys

_FALLBACK_MIT = """MIT License

Copyright (c) 2026 ahsanihlwn

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""


def _resource_roots() -> list[Path]:
    roots = [Path(__file__).resolve().parents[1]]
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        roots.insert(0, Path(meipass))
    if getattr(sys, "frozen", False):
        roots.append(Path(sys.executable).resolve().parent)
    return roots


def _read_repo_file(*rel_parts: str, fallback: str = "") -> str:
    for root in _resource_roots():
        candidate = root.joinpath(*rel_parts)
        if candidate.is_file():
            try:
                return candidate.read_text(encoding="utf-8", errors="replace").strip()
            except OSError:
                continue
    return fallback.strip()


def _collapse(lines: list[str]) -> str:
    return re.sub(r"\s+", " ", " ".join(part.strip() for part in lines if part.strip())).strip()


def _clean_license_lines(raw: str) -> list[str]:
    """Strip ASCII star-box borders (used in MPL-2.0 Sections 6 & 7) before reflow."""
    cleaned: list[str] = []
    for line in raw.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        s = line.strip()
        if re.fullmatch(r"\*{3,}", s):
            cleaned.append("")
            continue
        m = re.fullmatch(r"\*(.*)\*", s)
        if m:
            cleaned.append(m.group(1).strip())
            continue
        cleaned.append(line)
    return cleaned


def _parse_license_blocks(raw: str) -> list[dict[str, str]]:
    """Reflow hard-wrapped license text into clean semantic blocks without altering wording."""
    blocks: list[dict[str, str]] = []
    lines = _clean_license_lines(raw)
    para: list[str] = []

    def flush_para() -> None:
        if para:
            text = _collapse(para)
            if text:
                blocks.append({"kind": "p", "text": text})
            para.clear()

    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        if not stripped:
            flush_para()
            i += 1
            continue

        if i + 1 < len(lines) and re.fullmatch(r"[=-]{3,}", lines[i + 1].strip()):
            flush_para()
            marker = lines[i + 1].strip()[0]
            blocks.append({"kind": "h1" if marker == "=" else "h2", "text": stripped})
            i += 2
            continue

        if re.fullmatch(r"[=-]{3,}", stripped):
            flush_para()
            i += 1
            continue

        if re.match(r"^\d+\.\d+\.\s+\S", stripped) and not line.startswith(" "):
            flush_para()
            blocks.append({"kind": "h3", "text": stripped})
            i += 1
            continue

        if re.match(r"^Exhibit\s+[A-Z]\b", stripped):
            flush_para()
            blocks.append({"kind": "h2", "text": stripped})
            i += 1
            continue

        if re.match(r"^\([a-z0-9]+\)\s+", stripped):
            flush_para()
            para.append(stripped)
            i += 1
            continue

        para.append(stripped)
        i += 1

    flush_para()
    return blocks


def _mit_blocks(raw: str) -> list[dict[str, str]]:
    blocks = _parse_license_blocks(raw)
    if blocks and blocks[0]["text"].strip().lower() == "mit license":
        blocks[0]["kind"] = "h1"
    if len(blocks) > 1 and blocks[1]["text"].lower().startswith("copyright"):
        blocks[1]["kind"] = "h3"
    return blocks


def blocks_to_html(
    blocks: list[dict[str, str]],
    *,
    accent: str = "#63a4ff",
    fg_bright: str = "#ffffff",
    fg: str = "#eef2f7",
    fg_muted: str = "#b7c0ce",
) -> str:
    """Render parsed license blocks as clean RichText HTML for QTextEdit."""
    parts: list[str] = []
    for block in blocks:
        kind = block["kind"]
        safe = html.escape(block["text"])
        if kind == "h1":
            parts.append(f'<p style="margin:0 0 8px 0; font-size:15px; font-weight:700; color:{accent};">{safe}</p>')
        elif kind == "h2":
            parts.append(f'<p style="margin:14px 0 4px 0; font-size:13px; font-weight:600; color:{fg_bright};">{safe}</p>')
        elif kind == "h3":
            parts.append(f'<p style="margin:9px 0 2px 0; font-size:12px; font-weight:600; color:{fg};">{safe}</p>')
        else:
            parts.append(f'<p style="margin:0 0 8px 0; font-size:12px; line-height:148%; color:{fg_muted};">{safe}</p>')
    return "".join(parts)


@lru_cache(maxsize=1)
def license_entries() -> list[dict[str, object]]:
    """Return master-detail license items (verbatim license blocks + separate attribution)."""
    mpl_raw = _read_repo_file("LICENSE", fallback="Mozilla Public License Version 2.0")
    mit_ahsan_raw = _read_repo_file("core", "platform_tools_rs", "LICENSE", fallback=_FALLBACK_MIT)
    mit_quill_raw = _read_repo_file("assets", "quill-native", "LICENSE", fallback=_FALLBACK_MIT)

    return [
        {
            "key": "mpl",
            "name": "Renns Android Kitchen",
            "meta": "MPL-2.0 · rennsproject",
            "summary": "",
            "blocks": _parse_license_blocks(mpl_raw),
        },
        {
            "key": "mit_native",
            "name": "Native Tools (rak_native)",
            "meta": "MIT · ahsanihlwn",
            "summary": "Modules: lpunpack, ext_cli, make_ext4fs, pac-extractor, magiskboot, erofs-rs, sdatimg, brotli-rs, platform-tools-rs.",
            "blocks": _mit_blocks(mit_ahsan_raw),
        },
        {
            "key": "mit_quill",
            "name": "Quill Native",
            "meta": "MIT · Quill Native contributors",
            "summary": "Module: assets/quill-native",
            "blocks": _mit_blocks(mit_quill_raw),
        },
    ]
