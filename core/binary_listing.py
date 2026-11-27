# SPDX-License-Identifier: MPL-2.0
"""Annotated AArch64 listings for recovered functions."""
import re

from core import binary_core


BRANCH = re.compile(r"(?:b(?:\.[a-z]+)?|bl|cb[n]?z|tb[n]?z)\Z")


def render(path, result, address, function, xrefs, strings, relocations):
    size = int(function.get("size") or 0)
    maximum = min(size, 65536) if size >= 4 else 256
    instructions = binary_core.instructions_va(path, result, address, maximum)
    symbol_map = result.get("addr_symbol_map", {})
    name = function.get("name") or symbol_map.get(address, "")
    qualifier = f"{size} bytes" if 4 <= size <= maximum else f"up to {maximum} bytes"
    lines = [f"// {address:#x} {name} ({qualifier})"]
    if not instructions:
        return lines[0] + "\n// No instructions decoded."
    targets = {}
    addresses = {row.address for row in instructions}
    for row in instructions:
        if not BRANCH.fullmatch(row.mnemonic):
            continue
        operand = row.op_str.rsplit(",", 1)[-1].strip().removeprefix("#")
        try:
            target = int(operand, 0)
        except ValueError:
            continue
        label = symbol_map.get(target) or (f".L_{target:x}" if target in addresses else hex(target))
        targets[row.address] = (target, label)

    local_labels = {target: label for target, label in targets.values() if label.startswith(".L_")}
    xrefs_by_address = {}
    for item in xrefs:
        if item["kind"] in {"ADR", "ADRP+ADD", "ADRP+LDR"} and item["to"] in strings:
            xrefs_by_address.setdefault(int(item["from"], 16), []).append(
                f'{item["kind"]} string {item["to"]} {strings[item["to"]][:200]!r}'
            )
    for row in instructions:
        notes = []
        if row.address in local_labels:
            lines.append(f"{local_labels[row.address]}:")
        if row.address in targets:
            notes.append("branch to " + targets[row.address][1])
        notes.extend(xrefs_by_address.get(row.address, ()))
        for relocation in relocations.get(row.address, ()):
            notes.append(f"relocation {relocation['type']} {relocation.get('symbol', '')}".rstrip())
        assembly = f"{row.mnemonic} {row.op_str}".rstrip()
        line = f"// {row.address:016x}  {row.bytes.hex()}  {assembly}"
        lines.append(line + ("  // " + " | ".join(notes) if notes else ""))
    return "\n".join(lines)
