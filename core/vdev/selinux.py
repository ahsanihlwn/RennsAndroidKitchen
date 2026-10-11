# SPDX-License-Identifier: MPL-2.0
from __future__ import annotations

import re

print("[CORE STAGE] VDEV SELINUX")

# Direktori tempat sepolicy tinggal, urut prioritas baca.
_POLICY_DIRS = (
    "/system/etc/selinux",
    "/system_ext/etc/selinux",
    "/product/etc/selinux",
    "/vendor/etc/selinux",
    "/odm/etc/selinux",
)

_CIL_SUFFIX = "_sepolicy.cil"
_PUB_CIL = "plat_pub_versioned.cil"
_FC_SUFFIX = "_file_contexts"

_BOOL_HEADS = ("and", "or", "not", "all", "xor", "eq", "neq")


def _parse_line(line):

    if not line or line[0] != "(":
        return None
    toks = line.replace("(", " ( ").replace(")", " ) ").split()
    stack = [[]]
    for t in toks:
        if t == "(":
            node = []
            stack[-1].append(node)
            stack.append(node)
        elif t == ")":
            if len(stack) > 1:
                stack.pop()
        else:
            stack[-1].append(t)
    top = stack[0]
    return top[0] if top and isinstance(top[0], list) else None


class Policy:


    def __init__(self):
        self.types = set()          # (type X)
        self.attributes = set()     # (typeattribute X)
        self.attr_expr = {}         # attr -> DAFTAR ekspresi keanggotaan (mentah)
        self.allow = {}             # (src, tgt, cls) -> set(perm)
        self.dontaudit = {}         # idem
        self.typetrans = {}         # (src, tgt, cls) -> hasil
        self.sid_context = {}       # nama SID awal -> context (mis. kernel)
        self.class_perms = {}       # cls -> set(perm) (sudah digabung common)
        self._common = {}           # nama common -> set(perm)
        self._class_common = {}     # cls -> nama common
        self.sources = []           # berkas cil yang benar-benar terbaca
        self.vendor_vers = ""       # versi plat policy yang diminta vendor
        self._members_cache = {}    # attr -> set(type)
        self._covers_cache = {}     # type -> set(nama yang mencakupnya)
        self._type_attrs = None     # type -> set(attr), dibangun malas

    # ── baca ──
    @classmethod
    def load(cls, vfs):

        pol = cls()

        vers = ""
        for cand in ("/vendor/etc/selinux/plat_sepolicy_vers.txt",
                    "/odm/etc/selinux/plat_sepolicy_vers.txt"):
            text = vfs.read_text(cand, 64)
            if text:
                vers = text.strip().split()[0] if text.strip() else ""
                if vers:
                    break

        def _take(vpath):
            text = vfs.read_text(vpath)
            if not text:
                return
            pol._absorb(text)
            pol.sources.append(vpath)

        for d in _POLICY_DIRS:
            if not vfs.is_dir(d):
                continue
            for name in sorted(vfs.listdir(d) or []):
                if name.endswith(_CIL_SUFFIX) or name == _PUB_CIL:
                    _take(d + "/" + name)
            # mapping untuk versi yang diminta vendor
            if vers:
                mdir = d + "/mapping"
                if vfs.is_dir(mdir):
                    for fname in (vers + ".cil", vers + ".compat.cil"):
                        if fname in (vfs.listdir(mdir) or []):
                            _take(mdir + "/" + fname)

        if not pol.sources:
            return None
        pol.vendor_vers = vers
        pol._finish_classes()
        return pol

    def _absorb(self, text):
        for raw in text.splitlines():
            line = raw.strip()
            if not line or line[0] != "(":
                continue
            # Saring cepat sebelum parsing penuh — mayoritas baris tidak
            # kita perlukan, dan parsing 5 MB S-expression itu mahal.
            head = line[1:line.find(" ")] if " " in line else ""
            if head not in ("allow", "dontaudit", "typetransition", "type",
                            "typeattribute", "typeattributeset", "class",
                            "common", "classcommon", "sidcontext"):
                continue
            node = _parse_line(line)
            if not node:
                continue
            self._add(node)

    def _add(self, node):
        head = node[0]

        if head == "type" and len(node) == 2 and isinstance(node[1], str):
            self.types.add(node[1])
        elif head == "typeattribute" and len(node) == 2 and isinstance(node[1], str):
            self.attributes.add(node[1])
        elif head == "typeattributeset" and len(node) == 3:


            self.attributes.add(node[1])
            self.attr_expr.setdefault(node[1], []).append(node[2])
        elif head in ("allow", "dontaudit") and len(node) == 4:
            src, tgt, spec = node[1], node[2], node[3]
            if not (isinstance(src, str) and isinstance(tgt, str)):
                return
            if not (isinstance(spec, list) and len(spec) == 2
                    and isinstance(spec[0], str) and isinstance(spec[1], list)):
                return
            cls_name = spec[0]
            perms = {p for p in spec[1] if isinstance(p, str)}
            table = self.allow if head == "allow" else self.dontaudit
            table.setdefault((src, tgt, cls_name), set()).update(perms)
        elif head == "typetransition":
            # (typetransition SRC TGT CLASS RESULT)
            # (typetransition SRC TGT CLASS "nama" RESULT)  <- bentuk bernama
            parts = [p for p in node[1:] if isinstance(p, str)]
            if len(parts) == 4:
                src, tgt, cls_name, result = parts
                self.typetrans[(src, tgt, cls_name)] = result
        elif head == "class" and len(node) == 3 and isinstance(node[2], list):
            self.class_perms.setdefault(node[1], set()).update(
                p for p in node[2] if isinstance(p, str))
        elif head == "common" and len(node) == 3 and isinstance(node[2], list):
            self._common.setdefault(node[1], set()).update(
                p for p in node[2] if isinstance(p, str))
        elif head == "classcommon" and len(node) == 3:
            self._class_common[node[1]] = node[2]
        elif head == "sidcontext" and len(node) == 3 and isinstance(node[2], list):


            parts = [x for x in node[2] if isinstance(x, str)]
            if len(parts) >= 3:
                self.sid_context[node[1]] = ":".join(parts[:3])

    def _finish_classes(self):
        for cls_name, common_name in self._class_common.items():
            extra = self._common.get(common_name)
            if extra:
                self.class_perms.setdefault(cls_name, set()).update(extra)

    # ── set-aljabar atribut ──
    def _eval(self, node, seen):
        if isinstance(node, str):
            return self._members(node, seen)
        if not node:
            return set()
        head = node[0]
        if isinstance(head, str) and head in _BOOL_HEADS:
            if head == "all":
                return set(self.types)
            if head == "not":
                inner = set()
                for x in node[1:]:
                    inner |= self._eval(x, seen)
                return self.types - inner
            if head == "and":
                out = None
                for x in node[1:]:
                    got = self._eval(x, seen)
                    out = got if out is None else (out & got)
                return out or set()
            if head == "or":
                out = set()
                for x in node[1:]:
                    out |= self._eval(x, seen)
                return out
            # xor/eq/neq: jarang di policy Android; tidak ditebak-tebak.
            return set()
        out = set()
        for x in node:
            out |= self._eval(x, seen)
        return out

    def _members(self, name, seen):

        if name in self._members_cache:
            return self._members_cache[name]
        if name not in self.attr_expr:
            # Tipe konkret (atau nama tak dikenal — diperlakukan apa adanya,
            # bukan diasumsikan kosong).
            return {name}
        if name in seen:
            return set()                      # penjaga siklus
        deeper = seen | {name}
        got = set()
        for expr in self.attr_expr[name]:
            got |= self._eval(expr, deeper)
        self._members_cache[name] = got
        return got

    def members(self, name):
        return self._members(name, frozenset())

    def _build_type_attrs(self):
        if self._type_attrs is not None:
            return
        m = {}
        for attr in self.attr_expr:
            for t in self.members(attr):
                m.setdefault(t, set()).add(attr)
        self._type_attrs = m

    def covers(self, type_name):

        hit = self._covers_cache.get(type_name)
        if hit is not None:
            return hit
        self._build_type_attrs()
        out = {type_name} | self._type_attrs.get(type_name, set())
        self._covers_cache[type_name] = out
        return out

    # ── tanya ──
    def _lookup(self, table, src, tgt, cls_name, perm):
        for s in self.covers(src):
            for t in self.covers(tgt):
                perms = table.get((s, t, cls_name))
                if perms and perm in perms:
                    return True
        return False

    def allowed(self, src, tgt, cls_name, perm):
        return self._lookup(self.allow, src, tgt, cls_name, perm)

    def silenced(self, src, tgt, cls_name, perm):

        return self._lookup(self.dontaudit, src, tgt, cls_name, perm)

    def domain_for_exec(self, src_domain, exec_type):

        for s in self.covers(src_domain):
            for t in self.covers(exec_type):
                got = self.typetrans.get((s, t, "process"))
                if got:
                    return got
        return ""

    def sid_domain(self, sid_name):

        ctx = self.sid_context.get(sid_name, "")
        if not ctx:
            return ""
        parts = ctx.split(":")
        return parts[2] if len(parts) >= 3 else ""

    def knows_type(self, name):
        return name in self.types or name in self.attributes


class FileContexts:


    def __init__(self):
        self.exact = {}     # path literal -> context
        self.rules = []     # (compiled_regex, panjang_prefix_literal, context)
        self.sources = []

    @classmethod
    def load(cls, vfs):
        fc = cls()
        for d in _POLICY_DIRS:
            if not vfs.is_dir(d):
                continue
            for name in sorted(vfs.listdir(d) or []):
                if not name.endswith(_FC_SUFFIX):
                    continue
                text = vfs.read_text(d + "/" + name)
                if not text:
                    continue
                fc._absorb(text)
                fc.sources.append(d + "/" + name)
        return fc if fc.sources else None

    def _absorb(self, text):
        for raw in text.splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split()
            if len(parts) < 2:
                continue
            path_re = parts[0]
            ctx = parts[-1]
            if ":" not in ctx:
                continue
            if not re.search(r"[\\\[\]().*+?{}|^$]", path_re):
                self.exact[path_re] = ctx
                continue
            try:
                rx = re.compile(path_re + r"\Z")
            except re.error:
                continue
            self.rules.append((rx, len(path_re), ctx))


        self.rules.sort(key=lambda r: -r[1])

    def lookup(self, path):
        hit = self.exact.get(path)
        if hit:
            return hit
        for rx, _n, ctx in self.rules:
            if rx.match(path):
                return ctx
        return ""


class UnpackContexts:


    def __init__(self):
        self.exact = {}
        self.rules = []     # (regex, panjang pola, context) — sama seperti FileContexts
        self.sources = []

    @classmethod
    def load(cls, layout):
        import os as _os
        from core.vdev.mounts import partition_mount_point

        uc = cls()
        cfg = _os.path.join(layout.port_dir, "config")
        if not _os.path.isdir(cfg):
            return None

        for name, host in (layout.partitions or {}).items():
            folder = _os.path.basename(_os.path.normpath(host))
            path = _os.path.join(cfg, folder + "_file_contexts")
            if not _os.path.isfile(path):
                continue
            mp = partition_mount_point(layout, name)
            prefix = "/" + folder
            try:
                with open(path, "r", encoding="utf-8", errors="ignore") as f:
                    text = f.read()
            except OSError:
                continue
            for raw in text.splitlines():
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split()
                if len(parts) < 2:
                    continue
                p, ctx = parts[0], parts[-1]
                if ":" not in ctx:
                    continue
                if p == prefix:
                    rel = ""
                elif p.startswith(prefix + "/"):
                    rel = p[len(prefix):]
                else:
                    continue
                vpath = (rel if mp == "/" else mp + rel) or "/"
                if not re.search(r"[\\\[\]().*+?{}|^$]", p):
                    uc.exact.setdefault(vpath, ctx)
                    continue
                try:
                    uc.rules.append((re.compile(vpath + r"\Z"), len(vpath), ctx))
                except re.error:
                    continue
            uc.sources.append(path)
        if not uc.sources:
            return None
        # Kekhususan sama seperti FileContexts: pola terpanjang menang.
        uc.rules.sort(key=lambda r: -r[1])
        return uc

    def lookup(self, path):
        hit = self.exact.get(path)
        if hit:
            return hit
        for rx, _n, ctx in self.rules:
            if rx.match(path):
                return ctx
        return ""


def type_of(context):

    if not context:
        return ""
    parts = context.split(":")
    return parts[2] if len(parts) >= 3 else ""
