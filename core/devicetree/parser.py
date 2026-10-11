# SPDX-License-Identifier: MPL-2.0
from __future__ import annotations

import os
import struct as _struct

from .data import Data, empty_data, REF_PHANDLE, REF_PATH, LABEL
from .tree import Node, Property, ReserveInfo, BootInfo, guess_boot_cpuid


class ParseError(RuntimeError):
    pass


# ── escape handling (util.c get_escape_char / get_oct_char / get_hex_char) ──
def _get_oct_char(s: str, i: int):
    val = 0
    n = 0
    j = i
    while n < 3 and j < len(s) and s[j] in "01234567":
        val = (val << 3) | int(s[j], 8)
        j += 1
        n += 1
    return val & 0xFF, j


def _get_hex_char(s: str, i: int):
    val = 0
    n = 0
    j = i
    while n < 2 and j < len(s) and s[j] in "0123456789abcdefABCDEF":
        val = (val << 4) | int(s[j], 16)
        j += 1
        n += 1
    return val & 0xFF, j


def _get_escape_char(s: str, i: int):

    c = s[i]
    j = i + 1
    simple = {"a": 7, "b": 8, "t": 9, "n": 10, "v": 11, "f": 12, "r": 13}
    if c in simple:
        return simple[c], j
    if c in "01234567":
        return _get_oct_char(s, i)  # re-read first digit
    if c == "x":
        return _get_hex_char(s, j)
    return ord(c), j


def _unescape_string(body: str) -> bytes:

    out = bytearray()
    i = 0
    n = len(body)
    while i < n:
        c = body[i]
        if c == "\\":
            val, i = _get_escape_char(body, i + 1)
            out.append(val)
        else:
            out += c.encode("utf-8")
            i += 1
    return bytes(out)


_PROPNODECHAR = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789,._+*#?@-")
_LABEL_START = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_")
_LABEL_CHAR = _LABEL_START | set("0123456789")
_HEX = set("0123456789abcdefABCDEF")
_OPS2 = {"<<", ">>", "<=", ">=", "==", "!=", "&&", "||"}


class _Token:
    __slots__ = ("kind", "value")

    def __init__(self, kind, value=None):
        self.kind = kind
        self.value = value

    def __repr__(self):
        return "Token(%s,%r)" % (self.kind, self.value)


class _Lexer:
    def __init__(self, text: str, base_dir: str = "."):
        self.s = text
        self.i = 0
        self.n = len(text)
        self.base_dir = base_dir
        # lexer states from the .l file
        self.bytestring = False
        self.propnodename = False

    def _strip_ws_comments(self):
        s, n = self.s, self.n
        while self.i < n:
            c = s[self.i]
            if c in " \t\r\n":
                self.i += 1
            elif c == "/" and self.i + 1 < n and s[self.i + 1] == "*":
                end = s.find("*/", self.i + 2)
                if end < 0:
                    raise ParseError("unterminated comment")
                self.i = end + 2
            elif c == "/" and self.i + 1 < n and s[self.i + 1] == "/":
                nl = s.find("\n", self.i)
                self.i = n if nl < 0 else nl + 1
            else:
                break

    def next(self) -> _Token:
        s, n = self.s, self.n

        # BYTESTRING state: only hex byte pairs and ']' (+ labels)
        if self.bytestring:
            self._strip_ws_comments()
            if self.i >= n:
                return _Token("EOF")
            c = s[self.i]
            if c == "]":
                self.i += 1
                self.bytestring = False
                self.propnodename = False
                return _Token("OP", "]")
            if c in _HEX and self.i + 1 < n and s[self.i + 1] in _HEX:
                val = int(s[self.i:self.i + 2], 16)
                self.i += 2
                return _Token("BYTE", val)
            if c in _LABEL_START:
                return self._lex_label_or_ref()
            raise ParseError("bad byte in bytestring: %r" % c)

        self._strip_ws_comments()
        if self.i >= n:
            return _Token("EOF")
        c = s[self.i]

        # /include/, /dts-v1/, /memreserve/, /bits/, /delete-*/, /incbin/
        if c == "/":
            for kw, tok in (("/dts-v1/", "V1"), ("/memreserve/", "MEMRESERVE"),
                            ("/bits/", "BITS"), ("/delete-property/", "DEL_PROP"),
                            ("/delete-node/", "DEL_NODE"), ("/incbin/", "INCBIN"),
                            ("/include/", "INCLUDE")):
                if s.startswith(kw, self.i):
                    self.i += len(kw)
                    if tok in ("DEL_PROP", "DEL_NODE"):
                        self.propnodename = True
                    return _Token(tok)
            # otherwise '/' is the path separator / divide operator

        # string
        if c == '"':
            j = self.i + 1
            while j < n:
                if s[j] == "\\":
                    j += 2
                    continue
                if s[j] == '"':
                    break
                j += 1
            body = s[self.i + 1:j]
            self.i = j + 1
            return _Token("STRING", _unescape_string(body))

        # char literal
        if c == "'":
            j = self.i + 1
            while j < n and s[j] != "'":
                if s[j] == "\\":
                    j += 2
                    continue
                j += 1
            body = s[self.i + 1:j]
            self.i = j + 1
            return _Token("CHAR", body)

        # &label or &{/path}
        if c == "&":
            return self._lex_label_or_ref(amp=True)

        # label (foo:) — the <*>{LABEL}: rule wins in every lexer state, so it
        # must be checked before PROPNODENAME consumes the bareword.
        if c in _LABEL_START:
            tok = self._maybe_label()
            if tok is not None:
                return tok

        # PROPNODENAME state (after { or ;): a run of propnode chars is a name
        if self.propnodename:
            start = self.i
            if c == "\\":
                self.i += 1
                start = self.i
            if self.i < n and s[self.i] in _PROPNODECHAR:
                while self.i < n and s[self.i] in _PROPNODECHAR:
                    self.i += 1
                name = s[start:self.i]
                self.propnodename = False
                return _Token("PROPNODENAME", name)
            # fall through to operator handling (e.g. '}' right after ';')

        # label  foo:
        if c in _LABEL_START:
            tok = self._maybe_label()
            if tok is not None:
                return tok
            # else it's a literal/propnodename-ish bareword handled below

        # numeric literal (V1 state is always on after /dts-v1/)
        if c.isdigit():
            j = self.i
            if s.startswith("0x", j) or s.startswith("0X", j):
                j += 2
                while j < n and s[j] in _HEX:
                    j += 1
            else:
                while j < n and s[j].isdigit():
                    j += 1
            # optional U/L/UL/LL/ULL suffix
            while j < n and s[j] in "UuLl":
                j += 1
            lit = s[self.i:j]
            self.i = j
            return _Token("LITERAL", lit)

        # two-char operators
        two = s[self.i:self.i + 2]
        if two in _OPS2:
            self.i += 2
            return _Token("OP", two)

        # single char operator / punctuation
        self.i += 1
        if c == "[":
            self.bytestring = True
        if c == "{" or c == ";":
            self.propnodename = True
        return _Token("OP", c)

    def _maybe_label(self):
        s, n = self.s, self.n
        j = self.i
        while j < n and s[j] in _LABEL_CHAR:
            j += 1
        if j < n and s[j] == ":":
            name = s[self.i:j]
            self.i = j + 1
            return _Token("LABEL", name)
        return None

    def _lex_label_or_ref(self, amp=False):
        s, n = self.s, self.n
        if amp:
            self.i += 1  # skip &
            if self.i < n and s[self.i] == "{":
                # &{/path}
                end = s.index("}", self.i)
                ref = s[self.i + 1:end]
                self.i = end + 1
                return _Token("REF", ref)
            j = self.i
            while j < n and s[j] in _LABEL_CHAR:
                j += 1
            ref = s[self.i:j]
            self.i = j
            return _Token("REF", ref)
        # label inside bytestring/array
        j = self.i
        while j < n and s[j] in _LABEL_CHAR:
            j += 1
        if j < n and s[j] == ":":
            name = s[self.i:j]
            self.i = j + 1
            return _Token("LABEL", name)
        raise ParseError("unexpected token at %r" % s[self.i:self.i + 10])


# parser
class _Parser:
    def __init__(self, text: str, base_dir: str = "."):
        self.lex = _Lexer(text, base_dir)
        self.base_dir = base_dir
        self.tok = self.lex.next()

    def _advance(self):
        self.tok = self.lex.next()

    def _expect_op(self, op):
        if self.tok.kind != "OP" or self.tok.value != op:
            raise ParseError("expected %r, got %r" % (op, self.tok))
        self._advance()

    def _is_op(self, op):
        return self.tok.kind == "OP" and self.tok.value == op

    # sourcefile: /dts-v1/ ';' memreserves devicetree
    def parse(self) -> BootInfo:
        if self.tok.kind != "V1":
            raise ParseError("expected /dts-v1/")
        self._advance()
        self._expect_op(";")

        reservelist = self._memreserves()
        root = self._devicetree()
        return BootInfo(reservelist, root, guess_boot_cpuid(root))

    def _memreserves(self):
        out = []
        while self.tok.kind == "MEMRESERVE" or (self.tok.kind == "LABEL"):
            # a label may precede memreserve; collect then attach
            labels = []
            while self.tok.kind == "LABEL":
                labels.append(self.tok.value)
                self._advance()
            if self.tok.kind != "MEMRESERVE":


                self._pending_root_labels = labels
                return out
            self._advance()
            addr = self._integer_prim()
            size = self._integer_prim()
            self._expect_op(";")
            re = ReserveInfo(addr, size)
            re.labels = labels
            out.append(re)
        return out

    # devicetree: '/' nodedef | devicetree '/' nodedef | devicetree DT_REF nodedef ...
    def _devicetree(self):
        root = None
        pending = getattr(self, "_pending_root_labels", [])
        while True:
            if self._is_op("/"):
                self._advance()
                node = self._nodedef()
                node.name = ""
                if root is None:
                    root = node
                    root.labels = pending + root.labels
                else:
                    self._merge_nodes(root, node)
            elif self.tok.kind == "REF":
                ref = self.tok.value
                self._advance()
                node = self._nodedef()
                target = root.get_node_by_ref(ref) if root else None
                if target is None:
                    raise ParseError("label or path '%s' not found" % ref)
                self._merge_nodes(target, node)
            elif self.tok.kind == "DEL_NODE":
                self._advance()
                if self.tok.kind != "REF":
                    raise ParseError("expected &ref after /delete-node/")
                ref = self.tok.value
                self._advance()
                self._expect_op(";")
                target = root.get_node_by_ref(ref) if root else None
                if target is not None:
                    target.deleted = True
            elif self.tok.kind == "EOF":
                break
            else:
                break
        if root is None:
            raise ParseError("no root node found")
        return root

    def _merge_nodes(self, old: Node, new: Node):
        old.deleted = False
        for l in new.labels:
            if l not in old.labels:
                old.labels.append(l)
        for prop in new.proplist:
            if prop.deleted:
                old.delete_property_by_name(prop.name)
                continue
            existing = None
            for p in old.proplist:
                if p.name == prop.name and not p.deleted:
                    existing = p
                    break
            if existing:
                existing.val = prop.val
                existing.labels = prop.labels
            else:
                old.add_property(prop)
        for child in new.children:
            if child.deleted:
                ex = old.get_subnode(child.name)
                if ex:
                    ex.deleted = True
                continue
            ex = old.get_subnode(child.name)
            if ex:
                self._merge_nodes(ex, child)
            else:
                old.add_child(child)

    # nodedef: '{' proplist subnodes '}' ';'
    def _nodedef(self) -> Node:
        self._expect_op("{")
        node = Node()
        self._proplist(node)
        self._subnodes(node)
        self._expect_op("}")
        self._expect_op(";")
        return node

    def _proplist(self, node: Node):
        while True:
            labels = []
            while self.tok.kind == "LABEL":
                labels.append(self.tok.value)
                self._advance()

            if self.tok.kind == "DEL_PROP":
                self._advance()
                if self.tok.kind != "PROPNODENAME":
                    raise ParseError("expected name after /delete-property/")
                name = self.tok.value
                self._advance()
                self._expect_op(";")
                p = Property(name, empty_data(), deleted=True)
                node.add_property(p)
                continue

            if self.tok.kind == "PROPNODENAME":
                # Could be a property (name = ... ; or name ;) or a subnode
                # (name { ). Lookahead one token.
                name = self.tok.value
                self._advance()
                if self._is_op("="):
                    self._advance()
                    val = self._propdata()
                    self._expect_op(";")
                    p = Property(name, val)
                    p.labels = labels
                    node.add_property(p)
                    continue
                elif self._is_op(";"):
                    self._advance()
                    p = Property(name, empty_data())
                    p.labels = labels
                    node.add_property(p)
                    continue
                elif self._is_op("{"):
                    # it's a subnode — hand control back with name stashed
                    self._pending_subnode = (name, labels)
                    return
                else:
                    raise ParseError("unexpected token after name %r: %r" % (name, self.tok))
            else:
                # no more properties
                self._pending_subnode = None
                self._pending_prop_labels = labels
                return

    def _subnodes(self, node: Node):
        # First, a subnode may have been detected in _proplist.
        pend = getattr(self, "_pending_subnode", None)
        self._pending_subnode = None
        if pend is not None:
            name, labels = pend
            child = self._nodedef()
            child.name = name
            child.labels = labels
            node.add_child(child)

        while True:
            labels = []
            while self.tok.kind == "LABEL":
                labels.append(self.tok.value)
                self._advance()
            if self.tok.kind == "DEL_NODE":
                self._advance()
                if self.tok.kind != "PROPNODENAME":
                    raise ParseError("expected name after /delete-node/")
                name = self.tok.value
                self._advance()
                self._expect_op(";")
                c = Node()
                c.name = name
                c.deleted = True
                node.add_child(c)
                continue
            if self.tok.kind == "PROPNODENAME":
                name = self.tok.value
                self._advance()
                child = self._nodedef()
                child.name = name
                child.labels = labels
                node.add_child(child)
                continue
            break

    # propdata: concatenation of strings / <arrays> / [bytestrings] / &refs
    def _propdata(self) -> Data:
        d = empty_data()
        first = True
        while True:
            if not first:
                if self._is_op(","):
                    self._advance()
                else:
                    break
            first = False

            while self.tok.kind == "LABEL":
                d.add_marker(LABEL, self.tok.value)
                self._advance()

            if self.tok.kind == "STRING":
                d.append_data(self.tok.value + b"\0")
                self._advance()
            elif self._is_op("<"):
                self._array(d, bits=32)
            elif self.tok.kind == "BITS":
                self._advance()
                bits = self._integer_prim()
                if bits not in (8, 16, 32, 64):
                    raise ParseError("Only 8/16/32/64-bit elements supported")
                self._expect_op("<")
                self._array(d, bits=bits)
            elif self._is_op("["):
                self._advance()
                self._bytestring(d)
            elif self.tok.kind == "REF":
                d.add_marker(REF_PATH, self.tok.value)
                self._advance()
            elif self.tok.kind == "INCBIN":
                self._incbin(d)
            else:
                if first:
                    raise ParseError("expected property value, got %r" % self.tok)
                break

            # trailing label
            while self.tok.kind == "LABEL":
                d.add_marker(LABEL, self.tok.value)
                self._advance()
        return d

    def _array(self, d: Data, bits: int):
        # '<' already consumed by caller for the /bits/ case; for plain '<' too.
        if self._is_op("<"):
            self._advance()
        while not self._is_op(">"):
            if self.tok.kind == "LABEL":
                d.add_marker(LABEL, self.tok.value)
                self._advance()
                continue
            if self.tok.kind == "REF":
                if bits != 32:
                    raise ParseError("references only allowed in 32-bit arrays")
                d.add_marker(REF_PHANDLE, self.tok.value)
                d.append_integer(0xFFFFFFFF, 32)
                self._advance()
                continue
            val = self._integer_prim()
            d.append_integer(val, bits)
        self._advance()  # consume '>'

    def _bytestring(self, d: Data):
        while True:
            if self.tok.kind == "BYTE":
                d.append_byte(self.tok.value)
                self._advance()
            elif self.tok.kind == "LABEL":
                d.add_marker(LABEL, self.tok.value)
                self._advance()
            elif self._is_op("]"):
                self._advance()
                break
            else:
                raise ParseError("bad token in bytestring: %r" % self.tok)

    def _incbin(self, d: Data):
        self._advance()  # INCBIN
        self._expect_op("(")
        if self.tok.kind != "STRING":
            raise ParseError("expected filename string in /incbin/")
        fname = self.tok.value.decode("utf-8")
        self._advance()
        offset = 0
        length = -1
        if self._is_op(","):
            self._advance()
            offset = self._integer_prim()
            self._expect_op(",")
            length = self._integer_prim()
        self._expect_op(")")
        path = os.path.join(self.base_dir, fname)
        with open(path, "rb") as f:
            f.seek(offset)
            data = f.read() if length < 0 else f.read(length)
        d.append_data(data)

    # integer expression grammar (full precedence)
    def _integer_prim(self) -> int:
        if self.tok.kind == "LITERAL":
            v = self._eval_literal(self.tok.value)
            self._advance()
            return v
        if self.tok.kind == "CHAR":
            v = self._eval_char(self.tok.value)
            self._advance()
            return v
        if self._is_op("("):
            self._advance()
            v = self._expr()
            self._expect_op(")")
            return v
        raise ParseError("expected integer primary, got %r" % self.tok)

    def _expr(self):
        return self._trinary()

    def _trinary(self):
        c = self._logor()
        if self._is_op("?"):
            self._advance()
            t = self._expr()
            self._expect_op(":")
            f = self._trinary()
            return t if c else f
        return c

    def _binop(self, sub, ops):
        left = sub()
        while self.tok.kind == "OP" and self.tok.value in ops:
            op = self.tok.value
            self._advance()
            right = sub()
            left = ops[op](left, right)
        return left

    def _logor(self):
        return self._binop(self._logand, {"||": lambda a, b: int(bool(a) or bool(b))})

    def _logand(self):
        return self._binop(self._bitor, {"&&": lambda a, b: int(bool(a) and bool(b))})

    def _bitor(self):
        return self._binop(self._bitxor, {"|": lambda a, b: a | b})

    def _bitxor(self):
        return self._binop(self._bitand, {"^": lambda a, b: a ^ b})

    def _bitand(self):
        return self._binop(self._eq, {"&": lambda a, b: a & b})

    def _eq(self):
        return self._binop(self._rela, {"==": lambda a, b: int(a == b),
                                        "!=": lambda a, b: int(a != b)})

    def _rela(self):
        return self._binop(self._shift, {"<": lambda a, b: int(a < b),
                                         ">": lambda a, b: int(a > b),
                                         "<=": lambda a, b: int(a <= b),
                                         ">=": lambda a, b: int(a >= b)})

    def _shift(self):
        return self._binop(self._add, {"<<": lambda a, b: a << b,
                                       ">>": lambda a, b: a >> b})

    def _add(self):
        return self._binop(self._mul, {"+": lambda a, b: a + b,
                                       "-": lambda a, b: a - b})

    def _mul(self):
        return self._binop(self._unary, {"*": lambda a, b: a * b,
                                         "/": lambda a, b: a // b if b else 0,
                                         "%": lambda a, b: a % b if b else 0})

    def _unary(self):
        if self._is_op("-"):
            self._advance()
            return -self._unary()
        if self._is_op("~"):
            self._advance()
            return ~self._unary()
        if self._is_op("!"):
            self._advance()
            return int(not self._unary())
        return self._integer_prim()

    @staticmethod
    def _eval_literal(s: str) -> int:
        s = s.rstrip("UuLl")
        return int(s, 0)

    @staticmethod
    def _eval_char(s: str) -> int:
        if not s:
            raise ParseError("empty character literal")
        if s[0] == "\\":
            val, j = _get_escape_char(s, 1)
            return val
        return ord(s[0])


def dt_from_source(text: str, base_dir: str = ".") -> BootInfo:
    return _Parser(text, base_dir).parse()
