# SPDX-License-Identifier: MPL-2.0
from __future__ import annotations

# magic & tags
FDT_MAGIC = 0xD00DFEED

FDT_BEGIN_NODE = 0x1
FDT_END_NODE = 0x2
FDT_PROP = 0x3
FDT_NOP = 0x4
FDT_END = 0x9

FDT_TAGSIZE = 4  # sizeof(fdt32_t)

# header sizes per version (fdt.h)
FDT_V1_SIZE = 7 * 4
FDT_V2_SIZE = FDT_V1_SIZE + 4
FDT_V3_SIZE = FDT_V2_SIZE + 4
FDT_V16_SIZE = FDT_V3_SIZE
FDT_V17_SIZE = FDT_V16_SIZE + 4

# default produced by dtc
DEFAULT_FDT_VERSION = 17

# size of one fdt_reserve_entry (two u64)
FDT_RESERVE_ENTRY_SIZE = 16


DT_TABLE_MAGIC = 0xD7B7AB1E
DT_TABLE_HEADER_SIZE = 32
DT_TABLE_ENTRY_SIZE = 32


def align(x: int, a: int) -> int:

    return (x + a - 1) & ~(a - 1)
