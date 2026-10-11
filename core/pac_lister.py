#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0


from __future__ import annotations

import argparse
import json
import os
import struct
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable


PAC_HEADER_FMT = "44s I I 512s 512s I I I I I I I 200s I I I 800s I H H"
FILE_HEADER_FMT = "I 512s 512s 504s I I I I I I I I 5I 996s"

PAC_HEADER_SIZE = struct.calcsize(PAC_HEADER_FMT)
FILE_HEADER_SIZE = struct.calcsize(FILE_HEADER_FMT)

SUPPORTED_VERSIONS = {"BP_R1.0.0", "BP_R2.0.1"}
PAC_MAGIC = 0xFFFAFFFA


class PacFormatError(ValueError):
    pass


@dataclass(frozen=True)
class PacHeader:
    path: str
    version: str
    size: int
    actual_size: int
    product_name: str
    firmware_name: str
    partition_count: int
    partitions_list_start: int
    mode: int
    flash_type: int
    nand_strategy: int
    is_nv_backup: int
    nand_page_type: int
    product_alias: str
    oma_dm_product_flag: int
    is_oma_dm: int
    is_preload: int
    reserved: int
    magic: int
    crc1: int
    crc2: int

    @property
    def size_matches(self) -> bool:
        return self.size == self.actual_size

    @property
    def has_known_magic(self) -> bool:
        return self.magic == PAC_MAGIC


@dataclass(frozen=True)
class PacEntry:
    index: int
    partition_name: str
    file_name: str
    reserved_file_name: str
    size: int
    data_offset: int
    file_flag: int
    check_flag: int
    can_omit_flag: int
    address_count: int
    address: int
    reserved: int
    header_length: int

    @property
    def is_file(self) -> bool:
        return self.size > 0 and bool(self.file_name)

    @property
    def is_operation(self) -> bool:
        return not self.is_file

    @property
    def archive_path(self) -> str:
        if self.is_file:
            return self.file_name
        name = self.partition_name or f"operation_{self.index}"
        return f"__operations__/{name}"


@dataclass(frozen=True)
class PacListing:
    header: PacHeader
    entries: list[PacEntry]

    @property
    def files(self) -> list[PacEntry]:
        return [entry for entry in self.entries if entry.is_file]

    @property
    def operations(self) -> list[PacEntry]:
        return [entry for entry in self.entries if entry.is_operation]


def _decode_utf16le(raw: bytes) -> str:
    return raw.decode("utf-16le", errors="replace").rstrip("\x00")


def _u64(hi: int, lo: int) -> int:
    return hi * 0x100000000 + lo


def _human_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024.0 or unit == "TB":
            if unit == "B":
                return f"{size} B"
            return f"{value:.2f} {unit}"
        value /= 1024.0
    return f"{size} B"


def read_pac_header(pac_path: str | os.PathLike[str]) -> PacHeader:
    path = Path(pac_path)
    actual_size = path.stat().st_size
    if actual_size < PAC_HEADER_SIZE:
        raise PacFormatError(f"{path} is too small to be a PAC file")

    with path.open("rb") as handle:
        raw = handle.read(PAC_HEADER_SIZE)

    if len(raw) != PAC_HEADER_SIZE:
        raise PacFormatError("Could not read complete PAC header")

    values = struct.unpack(PAC_HEADER_FMT, raw)
    version = _decode_utf16le(values[0])
    size = _u64(values[1], values[2])

    header = PacHeader(
        path=str(path),
        version=version,
        size=size,
        actual_size=actual_size,
        product_name=_decode_utf16le(values[3]),
        firmware_name=_decode_utf16le(values[4]),
        partition_count=values[5],
        partitions_list_start=values[6],
        mode=values[7],
        flash_type=values[8],
        nand_strategy=values[9],
        is_nv_backup=values[10],
        nand_page_type=values[11],
        product_alias=_decode_utf16le(values[12]),
        oma_dm_product_flag=values[13],
        is_oma_dm=values[14],
        is_preload=values[15],
        reserved=values[16],
        magic=values[17],
        crc1=values[18],
        crc2=values[19],
    )

    if header.version not in SUPPORTED_VERSIONS:
        raise PacFormatError(f"Unsupported PAC version: {header.version!r}")
    if not header.size_matches:
        raise PacFormatError(
            f"PAC size mismatch: header={header.size}, actual={header.actual_size}"
        )

    return header


def iter_pac_entries(
    pac_path: str | os.PathLike[str],
    header: PacHeader | None = None,
) -> Iterable[PacEntry]:
    path = Path(pac_path)
    header = header or read_pac_header(path)

    with path.open("rb") as handle:
        handle.seek(header.partitions_list_start)

        for index in range(header.partition_count):
            raw = handle.read(FILE_HEADER_SIZE)
            if len(raw) != FILE_HEADER_SIZE:
                raise PacFormatError(f"Short read while parsing entry #{index}")

            values = struct.unpack(FILE_HEADER_FMT, raw)
            header_length = values[0]
            if header_length != FILE_HEADER_SIZE:
                raise PacFormatError(
                    f"Unknown PAC file header length at entry #{index}: {header_length}"
                )

            yield PacEntry(
                index=index,
                partition_name=_decode_utf16le(values[1]),
                file_name=_decode_utf16le(values[2]),
                reserved_file_name=_decode_utf16le(values[3]),
                size=_u64(values[4], values[6]),
                data_offset=_u64(values[5], values[9]),
                file_flag=values[7],
                check_flag=values[8],
                can_omit_flag=values[10],
                address_count=values[11],
                address=values[12],
                reserved=values[13],
                header_length=header_length,
            )


def list_pac(pac_path: str | os.PathLike[str]) -> PacListing:
    header = read_pac_header(pac_path)
    entries = list(iter_pac_entries(pac_path, header=header))
    return PacListing(header=header, entries=entries)


def is_pac(pac_path: str | os.PathLike[str]) -> bool:
    try:
        read_pac_header(pac_path)
        return True
    except (OSError, PacFormatError, struct.error):
        return False


def listing_to_dict(listing: PacListing) -> dict:
    return {
        "header": asdict(listing.header),
        "entries": [asdict(entry) | {
            "is_file": entry.is_file,
            "is_operation": entry.is_operation,
            "archive_path": entry.archive_path,
        } for entry in listing.entries],
    }


def _print_listing(listing: PacListing, files_only: bool = False, limit: int | None = None):
    header = listing.header
    entries = listing.files if files_only else listing.entries
    if limit is not None:
        entries = entries[:limit]

    print(f"PAC: {header.path}")
    print(f"Version: {header.version}")
    print(f"Product: {header.product_name}")
    print(f"Firmware: {header.firmware_name}")
    print(f"Alias: {header.product_alias}")
    print(f"Size: {_human_size(header.size)}")
    print(f"Entries: {len(listing.entries)} ({len(listing.files)} files, {len(listing.operations)} operations)")
    print()
    print(f"{'#':>2}  {'Type':<4} {'FileID':<18} {'Name':<42} {'Size':>12} {'Offset':>12}")
    print("-" * 100)
    for entry in entries:
        kind = "file" if entry.is_file else "op"
        name = entry.file_name if entry.is_file else entry.archive_path
        print(
            f"{entry.index:2d}  {kind:<4} "
            f"{entry.partition_name:<18.18} "
            f"{name:<42.42} "
            f"{_human_size(entry.size):>12} "
            f"{entry.data_offset:12d}"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="List files inside a Spreadtrum/Unisoc PAC firmware.")
    parser.add_argument("pacfile", help="Path to .pac firmware")
    parser.add_argument("--json", action="store_true", help="print machine-readable JSON")
    parser.add_argument("--files-only", action="store_true", help="hide non-file operation entries")
    parser.add_argument("--limit", type=int, help="print only the first N entries")
    args = parser.parse_args(argv)

    listing = list_pac(args.pacfile)
    if args.json:
        print(json.dumps(listing_to_dict(listing), indent=2))
    else:
        _print_listing(listing, files_only=args.files_only, limit=args.limit)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
