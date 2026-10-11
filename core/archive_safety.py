# SPDX-License-Identifier: MPL-2.0
from __future__ import annotations

import os
import stat
import zipfile
from pathlib import PureWindowsPath
from typing import Iterable


class UnsafeArchiveError(ValueError):
    pass


def _member_parts(member_name: str) -> tuple[str, ...]:

    if not isinstance(member_name, str) or not member_name or "\x00" in member_name:
        raise UnsafeArchiveError("archive contains an invalid member name")

    # ZIP names conventionally use '/', but attackers often use Windows '\\'
    # specifically to bypass POSIX-only checks.
    normalized = member_name.replace("\\", "/")
    win_path = PureWindowsPath(member_name)
    if (normalized.startswith("/") or member_name.startswith("\\") or
            win_path.is_absolute() or win_path.drive):
        raise UnsafeArchiveError(f"absolute archive path rejected: {member_name!r}")

    parts = tuple(part for part in normalized.split("/") if part)
    if not parts or any(part in (".", "..") for part in parts):
        raise UnsafeArchiveError(f"unsafe archive path rejected: {member_name!r}")
    return parts


def validate_archive_member_names(member_names: Iterable[str]) -> list[tuple[str, ...]]:

    result: list[tuple[str, ...]] = []
    seen: set[str] = set()
    for member_name in member_names:
        parts = _member_parts(member_name)
        key = os.path.normcase("/".join(parts))
        if key in seen:
            raise UnsafeArchiveError(f"duplicate archive member rejected: {member_name!r}")
        seen.add(key)
        result.append(parts)
    return result


def _zip_member_is_symlink(info: zipfile.ZipInfo) -> bool:
    # The upper 16 bits carry Unix st_mode when an archive was created on Unix.
    return stat.S_IFMT(info.external_attr >> 16) == stat.S_IFLNK


def safe_extract_zip(
    source: str | zipfile.ZipFile,
    destination: str,
    *,
    max_members: int | None = None,
    max_uncompressed_bytes: int | None = None,
) -> list[str]:

    close_when_done = not isinstance(source, zipfile.ZipFile)
    archive = zipfile.ZipFile(source, "r") if close_when_done else source
    try:
        infos = archive.infolist()
        if max_members is not None and len(infos) > max_members:
            raise UnsafeArchiveError(
                f"archive has too many members ({len(infos)} > {max_members})"
            )

        declared_total = sum(info.file_size for info in infos)
        if max_uncompressed_bytes is not None and declared_total > max_uncompressed_bytes:
            raise UnsafeArchiveError(
                "archive expands beyond the permitted uncompressed size"
            )

        paths = validate_archive_member_names(info.filename for info in infos)
        for info in infos:
            if info.flag_bits & 0x1:
                raise UnsafeArchiveError("encrypted ZIP archives are not supported")
            if _zip_member_is_symlink(info):
                raise UnsafeArchiveError(f"symbolic-link member rejected: {info.filename!r}")

        os.makedirs(destination, exist_ok=True)
        root = os.path.normcase(os.path.realpath(destination))
        written = 0
        extracted: list[str] = []

        for info, parts in zip(infos, paths):
            out_path = os.path.realpath(os.path.join(destination, *parts))
            try:
                contained = os.path.commonpath((root, os.path.normcase(out_path))) == root
            except ValueError:
                contained = False
            if not contained:
                raise UnsafeArchiveError(f"archive path escapes destination: {info.filename!r}")

            if info.is_dir():
                os.makedirs(out_path, exist_ok=True)
                continue

            os.makedirs(os.path.dirname(out_path), exist_ok=True)
            with archive.open(info, "r") as src, open(out_path, "wb") as dst:
                while chunk := src.read(1024 * 1024):
                    written += len(chunk)
                    if (max_uncompressed_bytes is not None and
                            written > max_uncompressed_bytes):
                        raise UnsafeArchiveError(
                            "archive expands beyond the permitted uncompressed size"
                        )
                    dst.write(chunk)
            extracted.append(out_path)
        return extracted
    finally:
        if close_when_done:
            archive.close()
