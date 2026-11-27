# SPDX-License-Identifier: MPL-2.0
"""Read build-specific types from a Linux BTF blob."""
from dataclasses import dataclass
import re
import struct


MAX_BTF_BYTES = 64 * 1024 * 1024
IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z_0-9]*\Z")
VARIABLE_RECORD_BYTES = {4: 12, 5: 12, 6: 8, 13: 8, 15: 12, 19: 12}
FIXED_RECORD_BYTES = {1: 4, 3: 12, 14: 4, 17: 4}


@dataclass(frozen=True)
class Member:
    name: str
    type_id: int
    bit_offset: int
    bitfield: bool


@dataclass(frozen=True)
class Type:
    kind: int
    name: str
    value: int
    extra: tuple = ()


class BTF:
    def __init__(self, data: bytes, pointer_size: int, endian: str):
        prefix = "<" if endian == "little" else ">"
        self.pointer_size = pointer_size
        if len(data) < 24 or len(data) > MAX_BTF_BYTES:
            raise ValueError("Invalid BTF length")
        magic, version, flags, header, type_offset, type_size, str_offset, str_size = (
            struct.unpack_from(prefix + "HBB5I", data)
        )
        types_end = header + type_offset + type_size
        strings = header + str_offset
        if (magic, version, flags) != (0xEB9F, 1, 0) or not (
            24 <= header <= types_end <= strings < strings + str_size <= len(data)
        ) or data[strings] != 0:
            raise ValueError("Invalid BTF header")

        def name(offset):
            begin = strings + offset
            if begin >= strings + str_size:
                raise ValueError("BTF string offset outside table")
            end = data.find(b"\0", begin, strings + str_size)
            if end < 0:
                raise ValueError("Unterminated BTF string")
            return data[begin:end].decode("utf-8", errors="replace")

        self.types = [Type(0, "void", 0)]
        self.functions = {}
        self.linkage = {}
        pos = header + type_offset
        while pos < types_end:
            if pos + 12 > types_end:
                raise ValueError("Truncated BTF type")
            name_offset, info, value = struct.unpack_from(prefix + "III", data, pos)
            pos += 12
            kind, count = (info >> 24) & 31, info & 0xffff
            if not 1 <= kind <= 19:
                raise ValueError(f"Unsupported BTF kind {kind}")
            length = VARIABLE_RECORD_BYTES.get(kind, 0) * count + FIXED_RECORD_BYTES.get(kind, 0)
            if pos + length > types_end:
                raise ValueError("Truncated BTF record")
            type_name = name(name_offset)
            extra = ()
            if kind == 1:
                extra = struct.unpack_from(prefix + "I", data, pos)
            elif kind == 4:
                has_bitfields = bool(info >> 31)
                extra = tuple(
                    Member(name(member), type_id, offset & 0xffffff if has_bitfields else offset,
                           has_bitfields and bool(offset >> 24))
                    for member, type_id, offset in struct.iter_unpack(prefix + "III", data[pos:pos + length])
                )
            elif kind == 13:
                extra = tuple(
                    (name(param), type_id)
                    for param, type_id in struct.iter_unpack(prefix + "II", data[pos:pos + length])
                )
            elif kind == 12:
                self.functions.setdefault(type_name, set()).add(value)
                self.linkage.setdefault(type_name, set()).add(count)
            self.types.append(Type(kind, type_name, value, extra))
            pos += length

    def resolved(self, type_id: int, aliases=(8,)):
        visited = set()
        while 0 <= type_id < len(self.types) and type_id not in visited:
            item = self.types[type_id]
            if item.kind not in aliases:
                return item
            visited.add(type_id)
            type_id = item.value
        return None

    def prototype(self, symbol):
        candidates = self.functions.get(symbol, ())
        prototypes = [self.resolved(type_id) for type_id in candidates]
        if prototypes and all(item == prototypes[0] for item in prototypes):
            item = prototypes[0]
            if item and item.kind == 13:
                return item
        return None

    def c_type(self, type_id, depth=0):
        if depth >= 16:
            return None
        item = self.resolved(type_id)
        if item is None:
            return None
        if item.kind == 0:
            return "void"
        if item.kind in (1, 16) and re.fullmatch(r"[A-Za-z_]+(?: [A-Za-z_]+)*", item.name):
            return item.name
        if item.kind in (4, 5, 7) and IDENTIFIER.fullmatch(item.name):
            aggregate = "union" if item.kind == 5 else "struct"
            if item.kind == 7:
                # FWD records do not carry enough layout information for this pass.
                return None
            return f"{aggregate} {item.name}"
        if item.kind in (2, 9, 10, 11):
            target = self.c_type(item.value, depth + 1)
            if target:
                suffix = {2: "*", 9: "volatile", 10: "const", 11: "restrict"}[item.kind]
                return f"{target} {suffix}"
        return None

    def declaration(self, symbol, prototype):
        if not IDENTIFIER.fullmatch(symbol) or not prototype:
            return None
        return_type = self.c_type(prototype.value)
        if not return_type:
            return None
        parameters = []
        for index, (name, type_id) in enumerate(prototype.extra):
            if not name and type_id == 0 and index == len(prototype.extra) - 1 and parameters:
                parameters.append("...")
                continue
            spelling = self.c_type(type_id)
            if not type_id or not spelling:
                return None
            parameters.append(f"{spelling} {name if IDENTIFIER.fullmatch(name) else f'arg{index}'}")
        storage = self.linkage.get(symbol)
        if storage not in ({0}, {1}, {2}):
            return None
        specifier = "static " if storage == {0} else ""
        return f"{specifier}{return_type} {symbol}({', '.join(parameters) or 'void'});"
