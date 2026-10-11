# SPDX-License-Identifier: MPL-2.0
from __future__ import annotations

import struct as _struct

# markertype enum (dtc.h)
REF_PHANDLE = "REF_PHANDLE"
REF_PATH = "REF_PATH"
LABEL = "LABEL"


class Marker:
    __slots__ = ("type", "offset", "ref")

    def __init__(self, mtype: str, offset: int, ref: str):
        self.type = mtype
        self.offset = offset
        self.ref = ref


class Data:
    __slots__ = ("val", "markers")

    def __init__(self, val: bytes = b"", markers=None):
        self.val = bytearray(val)
        self.markers = list(markers) if markers else []

    # introspection
    @property
    def len(self) -> int:
        return len(self.val)

    def __len__(self) -> int:
        return len(self.val)

    # append helpers (data_append_*)
    def append_data(self, p: bytes) -> "Data":
        self.val += p
        return self

    def append_byte(self, byte: int) -> "Data":
        self.val.append(byte & 0xFF)
        return self

    def append_zeroes(self, n: int) -> "Data":
        self.val += b"\0" * n
        return self

    def append_align(self, a: int) -> "Data":
        # ALIGN(len, a) - len zero bytes
        newlen = (len(self.val) + a - 1) & ~(a - 1)
        return self.append_zeroes(newlen - len(self.val))

    def append_integer(self, value: int, bits: int) -> "Data":
        if bits == 8:
            self.val += _struct.pack(">B", value & 0xFF)
        elif bits == 16:
            self.val += _struct.pack(">H", value & 0xFFFF)
        elif bits == 32:
            self.val += _struct.pack(">I", value & 0xFFFFFFFF)
        elif bits == 64:
            self.val += _struct.pack(">Q", value & 0xFFFFFFFFFFFFFFFF)
        else:
            raise ValueError("Invalid literal size (%d)" % bits)
        return self

    def append_cell(self, word: int) -> "Data":
        return self.append_integer(word, 32)

    def append_re(self, address: int, size: int) -> "Data":
        self.val += _struct.pack(">QQ", address & 0xFFFFFFFFFFFFFFFF,
                                 size & 0xFFFFFFFFFFFFFFFF)
        return self

    # markers (data_add_marker / data_merge)
    def add_marker(self, mtype: str, ref: str) -> "Data":
        self.markers.append(Marker(mtype, len(self.val), ref))
        return self

    def merge(self, other: "Data") -> "Data":

        base = len(self.val)
        self.val += other.val
        for m in other.markers:
            self.markers.append(Marker(m.type, m.offset + base, m.ref))
        return self

    def is_one_string(self) -> bool:

        n = len(self.val)
        if n == 0:
            return False
        for i in range(n - 1):
            if self.val[i] == 0:
                return False
        return self.val[n - 1] == 0

    def copy(self) -> "Data":
        return Data(bytes(self.val), [Marker(m.type, m.offset, m.ref) for m in self.markers])


def empty_data() -> Data:
    return Data()
