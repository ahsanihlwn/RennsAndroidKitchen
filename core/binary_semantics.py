# SPDX-License-Identifier: MPL-2.0
"""Conservative rewrites for the subset of Ghidra C with proven BTF layouts."""
from collections import Counter, defaultdict
from dataclasses import dataclass
import re

from core.kernel_btf import IDENTIFIER


TOKEN = re.compile(
    r"""//[^\n]*|/\*[\s\S]*?\*/|"(?:\\[\s\S]|[^"\\])*"|'(?:\\[\s\S]|[^'\\])*'|"""
    r"[A-Za-z_][A-Za-z_0-9]*|0[xX][A-Fa-f0-9]+[uUlL]*|[0-9]+[uUlL]*|"
    r"<<=|>>=|[+\-*/%&|^]=|\+\+|--|->|==|!=|<=|>=|&&|\|\||[^\s]"
)
ASSIGNMENTS = {"=", "+=", "-=", "*=", "/=", "%=", "&=", "|=", "^=", "<<=", ">>=", "++", "--"}
GHIDRA_BUG = re.compile(
    r"(?m)^[ \t]*(?P<pointer>[A-Za-z_]\w*)[ \t]*=[ \t]*\(code[ \t]*\*[ \t]*\)"
    r"SoftwareBreakpoint\(0x800,[ \t]*(?P<pc>0x[0-9A-Fa-f]+)\);[ \t]*\r?\n"
    r"[ \t]*\(\*(?P=pointer)\)\(\);[ \t]*$"
)
FIXED_INTS = {
    "signed char": (1, True), "unsigned char": (1, False), "byte": (1, False),
    "short": (2, True), "short int": (2, True), "ushort": (2, False),
    "unsigned short": (2, False), "unsigned short int": (2, False),
    "int": (4, True), "uint": (4, False), "unsigned": (4, False), "unsigned int": (4, False),
    "longlong": (8, True), "ulonglong": (8, False),
    "long long": (8, True), "long long int": (8, True), "unsigned long long": (8, False),
    "unsigned long long int": (8, False),
}


def restore_kernel_bugs(body, sites, source, start, end):
    """Restore a non-warning BRK only at a verified BUG source line in this definition."""
    if not sites:
        return body, 0
    first = source.count(b"\n", 0, start) + 1
    original = source[start:end].decode("utf-8", errors="replace")
    source_tokens = [token for token in TOKEN.finditer(original) if not token[0].startswith(("/*", "//"))]
    lines = {
        first + original.count("\n", 0, token.start())
        for token, following in zip(source_tokens, source_tokens[1:])
        if token[0] in ("BUG", "BUG_ON") and following[0] == "("
    }
    verified = defaultdict(set)
    for site in sites:
        line = site["line"]
        if isinstance(line, int) and line in lines:
            verified[int(site["address"], 16)].add(line)
    if not verified:
        return body, 0

    text = body.decode("utf-8")
    tokens = [token for token in TOKEN.finditer(text) if not token[0].startswith(("/*", "//"))]
    indices = {token.start(): index for index, token in enumerate(tokens)}
    counts = Counter(token[0] for token in tokens)
    pairs = defaultdict(list)
    for match in GHIDRA_BUG.finditer(text):
        index = indices.get(match.start("pointer"), 0)
        if not index or tokens[index - 1][0] not in ("{", "}", ";"):
            continue
        pc = int(match["pc"], 16)
        # Ghidra may report the next instruction as the resume address.
        possibilities = [(address, line) for address in (pc, pc - 4) for line in verified[address]]
        if len(possibilities) == 1:
            pairs[match["pointer"]].append((match.start(), match.end(), possibilities[0][1]))

    edits = []
    total = 0
    words = [token[0] for token in tokens]
    for pointer, calls in pairs.items():
        if counts[pointer] != 1 + 2 * len(calls):
            continue
        declaration = next((index for index in range(1, len(words) - 3)
                            if words[index - 1] in ("{", ";") and words[index:index + 4] == ["code", "*", pointer, ";"]), None)
        if declaration is None:
            continue
        edits.append((tokens[declaration].start(), tokens[declaration + 3].end(), ""))
        edits.extend((start, end, f"\n#line {line}\nBUG();") for start, end, line in calls)
        total += len(calls)
    for start, end, replacement in sorted(edits, reverse=True):
        text = text[:start] + replacement + text[end:]
    return text.encode("utf-8"), total


def _integer(words, pointer_size):
    spelling = " ".join(words)
    if spelling in ("long", "long int", "ulong", "unsigned long", "unsigned long int"):
        return pointer_size, spelling.startswith("long")
    storage = re.fullmatch(r"(int|uint|xunknown|undefined)([1248])", spelling)
    if storage:
        prefix, size = storage.groups()
        return int(size), prefix == "int" if prefix in ("int", "uint") else None
    return FIXED_INTS.get(spelling)


def _literal(token):
    token = token.rstrip("uUlL")
    if len(token) <= 18 and re.fullmatch(r"0[xX][0-9a-fA-F]+|0|[1-9][0-9]*", token):
        return int(token, 0)
    return None


def _fits(value, width, signed):
    bits = width * 8 - int(signed)
    return value is not None and (-(1 << bits) if signed else 0) <= value < 1 << bits


@dataclass(frozen=True)
class Access:
    base: str
    offset: int
    integer: tuple[int, bool | None]
    spelling: tuple[str, ...]
    end: int


def _read_access(tokens, start, pointer_size, depth=0):
    if depth >= 8 or tokens[start:start + 2] != ["*", "("]:
        return None
    cast_end = start + 2
    while cast_end < min(len(tokens), start + 7) and IDENTIFIER.fullmatch(tokens[cast_end]):
        cast_end += 1
    spelling = tuple(tokens[start + 2:cast_end])
    integer = _integer(spelling, pointer_size)
    if not integer or tokens[cast_end:cast_end + 3] != ["*", ")", "("]:
        return None
    base_start = cast_end + 3
    if base_start >= len(tokens):
        return None
    if IDENTIFIER.fullmatch(tokens[base_start]):
        base, pos = tokens[base_start], base_start + 1
    else:
        base = _read_access(tokens, base_start, pointer_size, depth + 1)
        if base is None:
            return None
        pos = base.end
    if tokens[pos:pos + 1] == [")"]:
        return Access(base, 0, integer, spelling, pos + 1)
    if pos + 2 >= len(tokens) or tokens[pos] != "+" or tokens[pos + 2] != ")":
        return None
    number = _literal(tokens[pos + 1])
    if number is None:
        return None
    return Access(base, number, integer, spelling, pos + 3)


def _integer_rank(words):
    spelling = " ".join(words)
    numbered = re.fullmatch(r"u?int([1248])", spelling)
    if numbered:
        return {"1": "char", "2": "short", "4": "int", "8": "long long"}[numbered[1]]
    if spelling in {"longlong", "ulonglong"} or words.count("long") == 2:
        return "long long"
    if spelling == "ulong" or "long" in words:
        return "long"
    if spelling == "ushort" or "short" in words:
        return "short"
    if spelling == "byte" or "char" in words:
        return "char"
    return "int"


def _field(access, roots, btf, pointer_result=False, literal_store=None, scalar_read=False):
    if isinstance(access.base, str):
        type_id = roots.get(access.base)
        root = path = access.base
    else:
        parent = _field(access.base, roots, btf, pointer_result=True)
        if parent is None:
            return None
        path, type_id, root = parent
    if type_id is None:
        return None
    pointer = btf.resolved(type_id, aliases=(8, 9, 10, 11))
    if pointer is None or pointer.kind != 2:
        return None
    aggregate = btf.resolved(pointer.value, aliases=(8, 9, 10))
    if aggregate is None or aggregate.kind != 4 or not IDENTIFIER.fullmatch(aggregate.name):
        return None
    fields = [member for member in aggregate.extra if member.bit_offset == access.offset * 8]
    width, signed = access.integer
    if len(fields) != 1 or access.offset + width > aggregate.value:
        return None
    member = fields[0]
    if member.bitfield or not IDENTIFIER.fullmatch(member.name):
        return None
    field = btf.resolved(member.type_id, aliases=(8, 9, 10))
    if field is None:
        return None
    unsigned_cast = ""
    if pointer_result:
        if field.kind != 2 or width != btf.pointer_size:
            return None
    else:
        if field.kind == 6 and scalar_read and field.value == width and signed is not None:
            pass
        elif field.kind != 1 or field.value != width:
            return None
        else:
            encoding = field.extra[0]
            field_signed = bool(encoding & (1 << 24))
            if encoding != ((int(field_signed) << 24) | (width * 8)):
                return None
            if signed is None:
                if not _fits(literal_store, width, field_signed):
                    return None
            else:
                rank = _integer_rank(access.spelling)
                if rank != _integer_rank(field.name.split()):
                    return None
                if signed != field_signed:
                    if not (scalar_read and signed is False and field_signed):
                        return None
                    unsigned_cast = f"unsigned {rank}"
    if isinstance(access.base, str):
        spelling = btf.c_type(type_id)
        if not spelling:
            return None
        path = f"(({spelling}){path})"
    path = f"{path}->{member.name}"
    if unsigned_cast:
        path = f"(({unsigned_cast}){path})"
    return path, member.type_id, root


def rewrite(code, btf, prototype):
    """Use BTF layouts for scalar accesses with a known parameter origin."""
    for type_id in (prototype.value, *(type_id for _, type_id in prototype.extra)):
        item = btf.resolved(type_id, aliases=(8, 9, 10, 11, 18))
        if item is None or (item.kind != 2 and (
            item.kind not in (0, 1, 6, 19) or item.value > btf.pointer_size
        )):
            return code, 0
    if any(type_id == 0 for _, type_id in prototype.extra):
        return code, 0
    matches = [match for match in TOKEN.finditer(code) if not match[0].startswith(("/*", "//"))]
    tokens = [match[0] for match in matches]
    if len(tokens) < 3 or "{" not in tokens:
        return code, 0
    body = tokens.index("{")
    if ")" not in tokens[:body] or "(" not in tokens[:body]:
        return code, 0
    begin = tokens.index("(")
    if body < begin + 2 or tokens[body - 1] != ")":
        return code, 0
    ends = [index for index in range(begin + 1, body) if tokens[index] == "," or index == body - 1]
    if len(ends) != len(prototype.extra) or any(
        token not in {",", "*"} and not IDENTIFIER.fullmatch(token) for token in tokens[begin + 1:body - 1]
    ):
        return code, 0
    roots = {}
    parameters = {}
    recovered_names = Counter(name for name, _ in prototype.extra)
    argument_start = begin + 1
    for slot, (argument_end, (name, type_id)) in enumerate(zip(ends, prototype.extra), 1):
        first = argument_start
        declaration = tokens[argument_start:argument_end]
        argument_start = argument_end + 1
        if not declaration or declaration[-1] != f"param_{slot}":
            continue
        spelling = declaration[:-1]
        wide_integer = _integer(spelling, btf.pointer_size)
        void_pointer = spelling == ["void", "*"]
        byte_pointer = spelling[-1:] == ["*"] and (
            spelling[:-1] == ["char"] or _integer(spelling[:-1], btf.pointer_size) in {(1, False), (1, True)}
        )
        if void_pointer or byte_pointer or (wide_integer and wide_integer[0] == btf.pointer_size):
            original = declaration[-1]
            roots[original] = type_id
            parameters[original] = (matches[first].start(), matches[argument_end - 1].end(), name)

    uses = Counter(tokens[body + 1:])
    for index in range(body + 1, len(tokens)):
        token = tokens[index]
        if token not in roots:
            continue
        previous, following = index - 1, index + 1
        while tokens[previous] == "(":
            previous -= 1
        while following < len(tokens) and tokens[following] == ")":
            following += 1
        next_is_assignment = following < len(tokens) and tokens[following] in ASSIGNMENTS
        if tokens[previous] in {"&", "++", "--"} or next_is_assignment:
            roots.pop(token)
    if not roots:
        return code, 0

    # Track single-assignment immutable local pointer aliases (e.g. lVar1 = param_1; or lVar1 = *(int8 *)(param_1 + off);)
    local_decls: dict[str, tuple[int, int]] = {}
    for index in range(body + 1, len(tokens) - 3):
        if tokens[index - 1] in {"{", ";"} and tokens[index + 2] == ";":
            w_int = _integer([tokens[index]], btf.pointer_size)
            if w_int and w_int[0] == btf.pointer_size and IDENTIFIER.fullmatch(tokens[index + 1]):
                local_decls[tokens[index + 1]] = (matches[index].start(), matches[index + 1].end())
        elif tokens[index - 1] in {"{", ";"} and tokens[index : index + 2] == ["void", "*"] and tokens[index + 3] == ";":
            if IDENTIFIER.fullmatch(tokens[index + 2]):
                local_decls[tokens[index + 2]] = (matches[index].start(), matches[index + 2].end())
    local_aliases: set[str] = set()
    if local_decls:
        assign_counts: Counter = Counter()
        bad_locals: set[str] = set()
        alias_candidates: dict[str, int] = {}
        for index in range(body + 1, len(tokens)):
            tok = tokens[index]
            if tok not in local_decls:
                continue
            prev_i, next_i = index - 1, index + 1
            while tokens[prev_i] == "(":
                prev_i -= 1
            while next_i < len(tokens) and tokens[next_i] == ")":
                next_i += 1
            if tokens[prev_i] in {"&", "++", "--"}:
                bad_locals.add(tok)
            elif next_i < len(tokens) and tokens[next_i] in ASSIGNMENTS:
                assign_counts[tok] += 1
                if tokens[next_i] != "=" or tokens[index - 1] not in {"{", "}", ";"}:
                    bad_locals.add(tok)
                else:
                    rhs = index + 2
                    if rhs < len(tokens) and tokens[rhs] in roots and tokens[rhs + 1 : rhs + 2] == [";"]:
                        alias_candidates[tok] = roots[tokens[rhs]]
                    elif rhs < len(tokens):
                        acc = _read_access(tokens, rhs, btf.pointer_size)
                        if acc and tokens[acc.end : acc.end + 1] == [";"]:
                            resolved_f = _field(acc, roots, btf, pointer_result=True)
                            if resolved_f is not None:
                                alias_candidates[tok] = resolved_f[1]
        for loc_name, loc_type_id in alias_candidates.items():
            if assign_counts[loc_name] == 1 and loc_name not in bad_locals and loc_name not in parameters:
                roots[loc_name] = loc_type_id
                local_aliases.add(loc_name)
                parameters[loc_name] = (*local_decls[loc_name], loc_name)

    changes = []
    consumed = body
    for index in range(body + 1, len(tokens)):
        if index < consumed or tokens[index] != "*" or tokens[index - 1] not in {
            "{", "}", ";", "(", ",", "return", "=", "!", "&&", "||", "==", "!=",
        }:
            continue
        access = _read_access(tokens, index, btf.pointer_size)
        if access is None or tokens[access.end:access.end + 1] in (["["], ["."], ["->"], ["("]):
            continue
        start, end = matches[index].start(), matches[access.end - 1].end()
        if "/*" in code[start:end] or "//" in code[start:end]:
            continue
        following = tokens[access.end:access.end + 3]
        if (
            index >= 2
            and tokens[index - 1] == "="
            and tokens[index - 2] in local_decls
            and following[:1] == [";"]
        ):
            loc_target = tokens[index - 2]
            ptr_field = _field(access, roots, btf, pointer_result=True)
            if ptr_field:
                changes.append((start, end, f"__RAK_ALIAS_RHS_{loc_target}__:{ptr_field[0]}", ptr_field[2]))
                consumed = access.end
                continue
        if following[:1] in (["=="], ["!="]) and tokens[index - 1] in {"return", "(", "&&", "||"}:
            right_start = access.end + 1
            right = _read_access(tokens, right_start, btf.pointer_size)
            if right and tokens[right.end:right.end + 1] in ([")"], [";"], ["&&"], ["||"]):
                left_field = _field(access, roots, btf, pointer_result=True)
                right_field = _field(right, roots, btf, pointer_result=True)
                right_begin, right_end = matches[right_start].start(), matches[right.end - 1].end()
                if left_field and right_field and btf.resolved(left_field[1]) == btf.resolved(right_field[1]):
                    if "/*" not in code[right_begin:right_end] and "//" not in code[right_begin:right_end]:
                        changes.append((start, end, left_field[0], left_field[2]))
                        changes.append((right_begin, right_end, right_field[0], right_field[2]))
                        consumed = right.end
                        continue
        literal_store = None
        if len(following) == 3 and following[0] == "=" and following[2] == ";":
            literal_store = _literal(following[1])
        field = _field(access, roots, btf, literal_store=literal_store,
                       scalar_read=not following or following[0] not in ASSIGNMENTS)
        if field:
            changes.append((start, end, field[0], field[2]))
            consumed = access.end
    changed = Counter(root for _, _, _, root in changes)
    replacements = {}
    edits = []
    for root, count in changed.items():
        expected_uses = uses[root] - 2 if root in local_aliases else uses[root]
        if count != expected_uses:
            continue
        start, end, name = parameters[root]
        if root not in local_aliases and (not IDENTIFIER.fullmatch(name) or recovered_names[name] != 1 or name in tokens):
            name = root
        declaration = btf.c_type(roots[root])
        if declaration and "/*" not in code[start:end] and "//" not in code[start:end]:
            replacements[root] = name
            edits.append((start, end, f"{declaration}{name}"))
    for start, end, text, root in changes:
        alias_match = re.match(r"^__RAK_ALIAS_RHS_([A-Za-z_]\w*)__:(.*)\Z", text, re.S)
        if alias_match:
            loc_target, inner_expr = alias_match.group(1), alias_match.group(2)
            text = inner_expr if loc_target in replacements else f"((long long)({inner_expr}))"
        if root in replacements:
            text = re.sub(rf"\(\(struct \w+(?: const)* \*\){root}\)", replacements[root], text, count=1)
        edits.append((start, end, text))
    return_type = btf.resolved(prototype.value, aliases=(8, 9, 10, 11))
    if changes and begin == 2 and return_type and return_type.kind == 1:
        original_type = _integer(tokens[:1], btf.pointer_size)
        spelling = btf.c_type(prototype.value)
        if original_type and original_type[1] is None and spelling:
            width = return_type.value
            encoding = return_type.extra[0]
            signed = bool(encoding & (1 << 24))
            literals_only = encoding == ((int(signed) << 24) | (width * 8))
            returns = [index for index in range(body + 1, len(tokens)) if tokens[index] == "return"]
            if returns and literals_only and all(
                tokens[index + 2:index + 3] == [";"] and _fits(_literal(tokens[index + 1]), width, signed)
                for index in returns
            ):
                edits.append((matches[0].start(), matches[0].end(), spelling))
    for start, end, text in sorted(edits, reverse=True):
        code = code[:start] + text + code[end:]
    return code, len(changes)
